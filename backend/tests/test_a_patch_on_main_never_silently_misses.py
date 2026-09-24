"""A test that patches `main` has to still be patching something.

Nearly a hundred test files steer the app by replacing a name on
backend.main — `monkeypatch.setattr(main, "_uid", lambda _r: "u1")` — and
over fifty names are patched that way. It works because the handlers live in
main.py and look those names up in main's globals each time they run.

Moving a handler out of main.py breaks that without breaking anything loud.
The moved code reads its OWN module's binding — the function it imported, or
the copy it was moved with — so the patch still succeeds, the handler runs
the real thing, and the test goes on passing about code it no longer
controls. A stubbed `_uid` becomes the real one, which answers "anonymous",
and a test that a stranger is refused keeps passing for the wrong reason.

So the split into backend/routers/ is held to these rules, read off the
source alone (no app is booted, so this runs in the light CI job too):

1. Every name the tests patch on main is still read, at call time, by code
   in main.py. A name that is patched and never read is a patch that does
   nothing.
2. No module under backend/routers binds a name the tests patch on main:
   not by defining it, not by importing it, not under another name. Its
   handlers would read that copy and never see the patch. Modules are the
   exception, because a router needs `db` like everything else and a patch
   on a module's ATTRIBUTE (`db.get_listing`) reaches every holder. Nor does
   main keep a patched name as a mere alias of a router's function
   (`_uid = deps.uid`): the tests would go on patching the alias, which only
   main's own handlers read.
3. What does not reach a router is replacing main's whole binding of a
   module with a stand-in. So a test that does that, to a module a router
   also holds, drives no route that lives under backend/routers. (The
   `dbmod` fixture is exempt: it is the same module object, reloaded
   against a scratch database.)
4. Nothing under backend/routers imports backend.main. main includes the
   routers, so the reverse is an import cycle — and a way around rule 2.

Moving code whose tests patch main therefore starts with the tests: patch
the module that now holds the name, and this passes again.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MAIN = BACKEND / "main.py"
ROUTERS = sorted((BACKEND / "routers").glob("*.py"))
TESTS = sorted(Path(__file__).resolve().parent.glob("test_*.py"))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _patches_on_main(tree: ast.Module):
    """(name, value) for each `setattr(main, "name", value)` in a test."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and len(node.args) >= 2):
            continue
        func = node.func
        called = (func.attr if isinstance(func, ast.Attribute)
                  else getattr(func, "id", ""))
        target, name = node.args[0], node.args[1]
        if (called in ("setattr", "delattr")
                and isinstance(target, ast.Name) and target.id == "main"
                and isinstance(name, ast.Constant) and isinstance(name.value, str)):
            yield name.value, (node.args[2] if len(node.args) > 2 else None)


PATCHED: dict[str, set[str]] = {}
for _path in TESTS:
    for _name, _value in _patches_on_main(_tree(_path)):
        PATCHED.setdefault(_name, set()).add(_path.name)


def _resolve(package: Path, level: int, module: str | None) -> Path:
    """The directory a relative `from ... import` reads from."""
    base = package
    for _ in range(level - 1):
        base = base.parent
    return base.joinpath(*module.split(".")) if module else base


def _is_module(package: Path, node: ast.ImportFrom, name: str) -> bool:
    if not node.level:
        return False            # third-party names: a class or function, here
    where = _resolve(package, node.level, node.module)
    return (where / f"{name}.py").exists() or (where / name / "__init__.py").exists()


def _origin(package: Path, node: ast.ImportFrom, name: str) -> tuple[str, str]:
    """(file or package the name comes from, its name there), comparable
    across modules that import the same object under different spellings."""
    if not node.level:
        return (node.module or "", name)
    return (str(_resolve(package, node.level, node.module).resolve()), name)


def _main_modules() -> set[str]:
    """Names main.py binds to a module (`db`, `auth`, `httpx`, ...)."""
    out = set()
    for node in _tree(MAIN).body:
        if isinstance(node, ast.Import):
            out |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out |= {a.asname or a.name for a in node.names
                    if _is_module(MAIN.parent, node, a.name)}
    return out


MAIN_MODULES = _main_modules()


def _read_when_called(tree: ast.Module) -> set[str]:
    """Names a function body in this module looks up while it runs.

    Bodies only: a decorator or a default argument is evaluated once, at
    import, so patching the name afterwards changes nothing there.
    """
    names: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = fn.body
        elif isinstance(fn, ast.Lambda):
            body = [fn.body]
        else:
            continue
        for stmt in body:
            names |= {n.id for n in ast.walk(stmt)
                      if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return names


def test_the_scan_finds_the_patches():
    """A scan that silently matches nothing passes forever. The list is meant
    to shrink as the split moves patches off main, so what is pinned is that
    both spellings are read, and that the suite's own patches are found."""
    sample = ast.parse('monkeypatch.setattr(main, "_uid", fake)\n'
                       'setattr(main, "LIST_CAP", 3)\n'
                       'monkeypatch.setattr(other, "_uid", fake)\n')
    assert sorted(n for n, _v in _patches_on_main(sample)) == ["LIST_CAP", "_uid"]
    assert PATCHED, "no test patches anything on main, or the scan broke"


def test_every_name_patched_on_main_is_still_read_by_main():
    unread = _read_when_called(_tree(MAIN))
    missing = {name: sorted(files) for name, files in PATCHED.items()
               if name not in unread}
    assert not missing, (
        "tests patch these on backend.main, and no code in main.py reads them "
        "when it runs, so the patch reaches nothing. If the code moved, patch "
        f"the module that holds it now: {missing}")


def test_no_router_holds_its_own_copy_of_a_patched_name():
    main_tree = _tree(MAIN)
    # Where main got each patched name it imports rather than defines, so a
    # router importing the same object under another spelling is caught too.
    origins = {}
    for node in main_tree.body:
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                bound = a.asname or a.name
                if bound in PATCHED and bound not in MAIN_MODULES:
                    origins[_origin(MAIN.parent, node, a.name)] = bound
    problems = []
    for path in ROUTERS:
        tree = _tree(path)
        where = path.relative_to(BACKEND)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound = {node.name}
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                bound = {n.id for t in targets for n in ast.walk(t)
                         if isinstance(n, ast.Name)}
            else:
                continue
            for name in bound & set(PATCHED):
                problems.append(f"{where}:{node.lineno} defines {name}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for a in node.names:
                bound = a.asname or a.name
                if _is_module(path.parent, node, a.name):
                    continue
                if bound in PATCHED:
                    problems.append(f"{where}:{node.lineno} imports {bound}")
                same = origins.get(_origin(path.parent, node, a.name))
                if same:
                    problems.append(f"{where}:{node.lineno} imports main's "
                                    f"{same} as {bound}")
    assert not problems, (
        "the tests patch these names on backend.main, so a router's own copy "
        "is one no patch reaches; patch the router's name in those tests "
        f"instead: {problems}")


def _is_under_routers(package: Path, node: ast.ImportFrom) -> bool:
    if not node.level:
        return (node.module or "").startswith("backend.routers")
    where = _resolve(package, node.level, node.module).resolve()
    routers = (BACKEND / "routers").resolve()
    return where == routers or routers in where.parents


def test_main_keeps_no_alias_of_a_router_function_under_a_patched_name():
    """`_uid = deps.uid`, `from .routers.deps import uid as _uid`, or a def
    whose whole body is `return deps.uid(request)`: each keeps every
    `setattr(main, "_uid", ...)` passing while the routers call the real
    one. Move the tests' patches to the router module instead."""
    tree = _tree(MAIN)
    routers = {a.asname or a.name for node in tree.body
               if isinstance(node, ast.ImportFrom)
               and _is_under_routers(MAIN.parent, node) for a in node.names}

    def forwards(value) -> bool:
        if isinstance(value, ast.Call):
            value = value.func
        return (isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id in routers)

    problems = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and _is_under_routers(MAIN.parent, node):
            problems += [f"main.py:{node.lineno} imports {a.asname or a.name}"
                         for a in node.names if (a.asname or a.name) in PATCHED]
        elif isinstance(node, ast.Assign) and forwards(node.value):
            problems += [f"main.py:{node.lineno} aliases {t.id}"
                         for t in node.targets
                         if isinstance(t, ast.Name) and t.id in PATCHED]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in PATCHED:
            body = [b for b in node.body
                    if not (isinstance(b, ast.Expr)
                            and isinstance(b.value, ast.Constant))]
            if len(body) == 1 and isinstance(body[0], ast.Return) \
                    and forwards(body[0].value):
                problems.append(f"main.py:{node.lineno} forwards {node.name}")
    assert not problems, (
        "tests patch these on backend.main, but they only stand in for a "
        "router's function, which the routers call directly: " f"{problems}")


def _router_paths() -> set[str]:
    """Every path a router serves, a parameterised one by its fixed part:
    "/api/admin/users/{" for "/api/admin/users/{user_id}"."""
    out = set()
    for path in ROUTERS:
        for node in ast.walk(_tree(path)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in node.decorator_list:
                if (isinstance(d, ast.Call) and d.args
                        and isinstance(d.args[0], ast.Constant)
                        and isinstance(d.args[0].value, str)):
                    out.add(d.args[0].value.split("{", 1)[0] + (
                        "{" if "{" in d.args[0].value else ""))
    return out


# Stands in for an f-string's formatted value: something follows the head.
_FILLED = "\x00"


def _sends(sent: str, route: str) -> bool:
    """Could a request for `sent` reach `route`? A route ending in "{"
    takes a parameter after its fixed part."""
    if route.endswith("{"):
        fixed = route[:-1]
        return fixed.count("/") >= 3 and sent.startswith(fixed) \
            and len(sent) > len(fixed)
    return sent == route or sent.startswith(route + "?")


def _strings(tree: ast.Module):
    """Every string a test could send as a path; an f-string by its fixed
    head, with _FILLED for the value after it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr) and node.values \
                and isinstance(node.values[0], ast.Constant):
            yield str(node.values[0].value) + _FILLED


def test_a_module_swapped_on_main_is_never_the_one_a_router_reads():
    routed = _router_paths()
    # Only a module some router holds can be missed. A test swapping one no
    # router reads (ebay_offers, say) can drive a routed path to sign up.
    held = {a.asname or a.name for path in ROUTERS
            for node in ast.walk(_tree(path))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for a in node.names}
    problems = []
    for path in TESTS:
        tree = _tree(path)
        swapped = {name for name, value in _patches_on_main(tree)
                   if name in MAIN_MODULES and name in held
                   and not (isinstance(value, ast.Name) and value.id == "dbmod")}
        if not swapped:
            continue
        hits = sorted({p.rstrip("{") for s in _strings(tree) for p in routed
                       if _sends(s, p)})
        if hits:
            problems.append(f"{path.name} swaps main's {sorted(swapped)} and "
                            f"drives {hits}")
    assert not problems, (
        "these routes live in backend/routers and read their own binding of "
        "the module, so the stand-in never reaches them; patch the router's "
        f"binding (or the module's attribute) instead: {problems}")


def test_no_router_imports_main():
    problems = []
    for path in ROUTERS:
        for node in ast.walk(_tree(path)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    where = _resolve(path.parent, node.level, node.module)
                    if where.resolve() == MAIN.with_suffix("").resolve():
                        names = ["main"]
                    elif where.resolve() == BACKEND.resolve():
                        names = [a.name for a in node.names]
                else:
                    names = [f"{node.module}.{a.name}" for a in node.names]
                    names.append(node.module or "")
            if any(n in ("main", "backend.main") for n in names):
                problems.append(f"{path.relative_to(BACKEND)}:{node.lineno}")
    assert not problems, f"a router imports backend.main: {problems}"

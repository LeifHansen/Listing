"""The words that decide what a listing says are readable with nothing installed.

services/listing_prompt says it in its own docstring: the rules live away from
the Anthropic client so they can be read -- and TESTED -- without the SDK. CI
skips the heavy stack in its fast job, so a test that imports services.claude_ai
skips with it, and a prompt rule nothing can assert on is a rule that quietly
rots.

Until now that property was only INCIDENTALLY enforced: it held because nobody
had added an import, and the thing that would have caught it was noticing that
a few dozen tests had started skipping. Skipped tests are green. That is the
failure this file exists to make loud, and it matters more now than it did,
because the rules have spread from one file to a package -- services/experts,
which the router and every vertical live in, and which has exactly the same
requirement for exactly the same reason.

So: walk the AST of every module that holds or assembles rule text, and assert
it imports nothing but the standard library and its own pure siblings. No
anthropic, no PIL, no httpx, no sqlalchemy, and nothing that transitively drags
one in.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parents[1] / "services"

# The modules that must stay readable with nothing installed. Every one either
# HOLDS rule text or DECIDES which rule text an item is read under.
PURE = [
    _SERVICES / "listing_prompt.py",
    _SERVICES / "experts" / "base.py",
    _SERVICES / "experts" / "registry.py",
    *sorted(_SERVICES.glob("experts/*/rules.py")),
    *sorted(_SERVICES.glob("experts/*/match.py")),
    *sorted(_SERVICES.glob("experts/*/__init__.py")),
]

# What a pure module may import. Standard library only, and only the boring
# half of it: no subprocess, no socket, no urllib. If a rule needs any of
# those, it is not a rule any more.
ALLOWED_STDLIB = {
    "__future__", "enum", "typing", "dataclasses", "re", "json", "os",
    "unicodedata", "functools", "itertools", "collections", "string", "math",
}


def _modules():
    return [p for p in PURE if p.is_file()]


def test_the_list_of_pure_modules_is_not_empty():
    """A glob that matches nothing passes every assertion below it."""
    found = _modules()
    assert len(found) >= 6, f"only found {[p.name for p in found]}"
    names = {p.parent.name + "/" + p.name for p in found}
    assert "art/rules.py" in names and "denim/rules.py" in names


@pytest.mark.parametrize("path", _modules(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_rule_module_imports_nothing_heavy(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root in ALLOWED_STDLIB, (
                    f"{path.name} imports {alias.name!r}. The rules must stay "
                    f"readable with no heavy stack installed -- see this "
                    f"file's docstring.")
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # relative: a sibling, which is on this list
                continue
            root = (node.module or "").split(".")[0]
            assert root in ALLOWED_STDLIB, (
                f"{path.name} imports from {node.module!r}. The rules must "
                f"stay readable with no heavy stack installed.")


def test_the_rules_really_do_import_with_nothing_but_the_stdlib():
    """The AST walk above proves the source says nothing heavy. This proves
    the whole transitive graph agrees, by doing it -- in a subprocess with an
    import hook that refuses the heavy modules by name."""
    import subprocess
    import sys

    banned = ("anthropic", "PIL", "httpx", "sqlalchemy", "boto3", "fastapi",
              "onnxruntime", "rembg", "cryptography")
    script = (
        "import sys\n"
        f"BANNED = {banned!r}\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name.split('.')[0] in BANNED:\n"
        "            raise ImportError('blocked: ' + name)\n"
        "        return None\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in BANNED:\n"
        "            raise ImportError('blocked: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "from backend.services import listing_prompt\n"
        "from backend.services.experts import registry\n"
        "from backend.services.experts.base import Stage\n"
        "assert len(listing_prompt.LISTING_SCHEMA) > 10000\n"
        "assert registry.rules_for(Stage.IDENTIFY)\n"
        "print('ok')\n"
    )
    repo = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, "-c", script], cwd=repo,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "ok" in out.stdout

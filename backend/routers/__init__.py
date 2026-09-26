"""The HTTP routes, split out of main.py one area at a time.

main.py grew to more than 13,000 lines holding 150 routes. Each module here
takes one area of them and exposes `router`, an APIRouter that main.py
includes at the point where those routes used to be defined. The position
matters: Starlette tries routes in the order they were registered and the
first match wins, which is what keeps /api/ebay/connect ahead of the generic
/api/{marketplace}/connect, and every route ahead of the static mount.

deps.py is the exception: it registers nothing. It holds the request
helpers main.py and the routers both need, since a router cannot import
main.py (main includes the routers, so the reverse is an import cycle).

Two rules keep a move safe, and both are checked by tests rather than left
to memory:

- The ownership scan (tests/test_every_scoped_route_checks_the_owner.py)
  reads every module in this package as well as main.py, so a handler scoped
  to one seller's listing is held to the same check wherever it lives.
- Many tests steer handlers by patching a name on main
  (`monkeypatch.setattr(main, "_purge_session_images", ...)`). A moved handler
  reads its own module's names, so such a patch would stop reaching it
  without failing. tests/test_a_patch_on_main_never_silently_misses.py
  refuses a router that binds a name the tests patch on main; move those
  tests' patches to the router's module along with the code.

Within a router, import modules rather than functions (`from .. import db`,
then `db.get_listing(...)`; `deps.uid(request)`, not `from .deps import
uid`): a patch on a module attribute reaches every caller, where a function
imported by name is a copy no patch reaches.
"""

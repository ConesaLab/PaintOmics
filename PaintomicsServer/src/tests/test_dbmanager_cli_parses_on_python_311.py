"""DBManager's command line must parse on the interpreter production runs.

Run from `PaintomicsServer/`:

    python -m src.tests.test_dbmanager_cli_parses_on_python_311

DBManager.py dispatches its commands through scriptinep3 0.3.1, whose
argument inspection calls ``inspect.getargspec()``. Python 3.11 removed that
function, so on the production image every ``DBManager.py <command>`` raised

    AttributeError: module 'inspect' has no attribute 'getargspec'

before parsing a single option -- the deploy scripts, the Organisms admin page
and a shell invocation alike. Nothing in CI ran the CLI, so the break went
unnoticed until an organism install on the Drago VM (2026-09-08).

AdminTools/scriptine_compat.py restores the function; DBManager's entry block
calls it before scriptine.run(). These checks pin both halves:

  * the shim lets scriptine inspect a function on THIS interpreter, whichever
    version that is (a no-op on <= 3.10, the fix on >= 3.11);
  * DBManager.py still wires the shim in, so a dead-code sweep cannot drop it;
  * when a serverconf exists, `DBManager.py download -h` really exits 0 and
    prints its usage. Skipped, not failed, without one: serverconf.py is
    gitignored and a bare checkout has none.

Needs no network and no database.
"""
import os
import re
import subprocess
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_PASSED = []
_FAILED = []

_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADMIN_TOOLS = os.path.join(_SRC_ROOT, "AdminTools")
_DBMANAGER = os.path.join(_ADMIN_TOOLS, "DBManager.py")
_SERVERCONF = os.path.join(_SRC_ROOT, "conf", "serverconf.py")


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"PASS  {name}")
    except AssertionError as exc:
        _FAILED.append((name, str(exc)))
        print(f"FAIL  {name}: {exc}")
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print(f"ERROR {name}:\n{traceback.format_exc()}")


def test_shim_lets_scriptine_inspect_a_function_on_this_interpreter():
    try:
        import scriptine.command as scriptine_command
    except ImportError as exc:
        # Also the 3.12+ case: scriptine imports distutils, which is gone there.
        print(f"      SKIP: scriptine cannot be imported here ({exc})")
        return

    sys.path.insert(0, _ADMIN_TOOLS)
    import scriptine_compat

    scriptine_compat.ensure_getargspec()
    # A second call must be harmless: DBManager and a caller that imported it
    # for another reason may both run it.
    assert scriptine_compat.ensure_getargspec() is False, "second call re-installed the shim"

    def command(specie=None, kegg=0, common=0):  # the shape of DBManager's commands
        return specie, kegg, common

    required, optional = scriptine_command.inspect_args(command)
    assert required == [], f"scriptine saw required args {required!r} on a keyword-only command"
    names = [name for name, _default in optional]
    assert names == ["specie", "kegg", "common"], f"scriptine saw optional args {names!r}"


def test_dbmanager_entry_block_installs_the_shim_before_scriptine_runs():
    with open(_DBMANAGER, encoding="utf-8") as handle:
        source = handle.read()
    match = re.search(r"^if __name__ == '__main__':\n(.*)\Z", source, re.M | re.S)
    assert match, "DBManager.py has no __main__ block"
    block = match.group(1)
    shim = block.find("ensure_getargspec()")
    run = block.find("scriptine.run()")
    assert shim != -1, "DBManager.py's __main__ block no longer calls ensure_getargspec()"
    assert run != -1, "DBManager.py's __main__ block no longer calls scriptine.run()"
    assert shim < run, "ensure_getargspec() must run before scriptine.run(), not after"


def test_dbmanager_download_help_parses_when_a_serverconf_exists():
    if not os.path.isfile(_SERVERCONF):
        print("      SKIP: no src/conf/serverconf.py in this checkout")
        return
    try:
        import scriptine  # noqa: F401 -- probing for the dependency, not using it here
    except ImportError as exc:
        print(f"      SKIP: scriptine cannot be imported here ({exc})")
        return

    # `cd PaintomicsServer && python src/AdminTools/DBManager.py ...` is the
    # documented invocation (docs/0_install.md); run it exactly that way.
    proc = subprocess.run(
        [sys.executable, os.path.relpath(_DBMANAGER, os.path.dirname(_SRC_ROOT)), "download", "-h"],
        cwd=os.path.dirname(_SRC_ROOT), capture_output=True, text=True, timeout=120)
    assert "getargspec" not in proc.stderr, "the getargspec AttributeError is back:\n" + proc.stderr[-800:]
    assert proc.returncode == 0, f"exit {proc.returncode}; stderr tail:\n{proc.stderr[-800:]}"
    assert "Usage: DBManager.py download" in proc.stdout, "no usage text; stdout was:\n" + proc.stdout[:400]


def main():
    print(f"Python {sys.version.split()[0]}\n")
    tests = [
        test_shim_lets_scriptine_inspect_a_function_on_this_interpreter,
        test_dbmanager_entry_block_installs_the_shim_before_scriptine_runs,
        test_dbmanager_download_help_parses_when_a_serverconf_exists,
    ]
    for t in tests:
        _check(t.__name__, t)

    print()
    print(f"Passed: {len(_PASSED)} / {len(_PASSED) + len(_FAILED)}")
    if _FAILED:
        for name, msg in _FAILED:
            print(f"  - {name}: {msg.splitlines()[0] if msg else ''}")
        sys.exit(1)


if __name__ == "__main__":
    main()

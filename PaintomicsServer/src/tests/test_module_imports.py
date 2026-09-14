"""
Import smoke test for every tracked server module.

Why this exists
---------------
The 2026-08-06 master merge deleted ``llm_client.py`` and stripped
``verify_report()`` out of ``verification.py``. Neither produced a git
conflict -- dev had not touched those regions since the merge base, so git
applied master's deletions silently. The result was a tree where
``src.classes.AIInterpret.pipeline`` (now ``agent``) could not be imported at all.

Every one of the then-existing suites passed on that broken tree, because
none of them import ``pipeline``. A suite that only exercises the modules it
happens to name cannot notice that a *different* module stopped importing.

So this test imports everything and lets ImportError speak for itself. It is
deliberately shallow: it proves the tree is loadable, not that it is correct.
That is exactly the gap the merge fell through.

Scope: only files tracked by git. Untracked scratch and work-in-progress
scripts are the author's business and must not fail the suite.
"""

import os
import subprocess
import sys
import traceback
import warnings
from importlib import import_module

# PaintomicsServer/  <- src/  <- tests/  <- this file
SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = os.path.dirname(SERVER_ROOT)

# Modules with import-time side effects that have no business running here:
# the AdminTools download scripts hit the network, and resources/ holds config
# templates that are copied into place, not imported.
EXCLUDED_PREFIXES = (
    "src.AdminTools.",
    "src.resources.",
)

# Modules that must import, called out by name so a regression names itself
# instead of hiding in a count. These are the runtime entry points.
CRITICAL = (
    "src.paintomicsserver",
    "src.classes.AIInterpret.agent",
    "src.classes.AIInterpret.llm_client",
    "src.classes.AIInterpret.walker.service",
    # imported lazily by service.run for a model walk, so an import error here
    # would otherwise surface only in a queue worker
    "src.classes.AIInterpret.walker.parallel",
    "src.classes.AIInterpret.walker.writer",
    "src.classes.AIInterpret.walker.sdk",
    "src.servlets.AIInterpretServlet",
    "src.servlets.PathwayAcquisitionServlet",
    "src.classes.JobInstances.PathwayAcquisitionJob",
)

_PASSED = []
_FAILED = []


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  " + name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  " + name)


def _tracked_modules():
    """Dotted module names for every tracked .py file under src/.

    Falls back to a filesystem walk when git is unavailable (e.g. a source
    tarball), accepting that untracked files are then included too.
    """
    paths = []
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "--", "PaintomicsServer/src/*.py"],
            cwd=REPO_ROOT, stderr=subprocess.DEVNULL,
        ).decode("utf-8", "replace")
        # Paths arrive repo-relative; make them relative to PaintomicsServer/.
        paths = [p[len("PaintomicsServer/"):] for p in out.split("\n")
                 if p.strip().endswith(".py")]
    except (subprocess.CalledProcessError, OSError):
        for dirpath, _dirnames, filenames in os.walk(os.path.join(SERVER_ROOT, "src")):
            for fn in filenames:
                if fn.endswith(".py"):
                    full = os.path.join(dirpath, fn)
                    paths.append(os.path.relpath(full, SERVER_ROOT))

    modules = []
    for rel in paths:
        # __init__.py imports as its package; the package is reached anyway
        # via its submodules, so skip to avoid duplicate work.
        if os.path.basename(rel) == "__init__.py":
            continue
        dotted = os.path.splitext(rel)[0].replace(os.sep, ".").replace("/", ".")
        if dotted.startswith(EXCLUDED_PREFIXES):
            continue
        modules.append(dotted)
    return sorted(set(modules))


def test_every_tracked_module_imports():
    modules = _tracked_modules()
    assert len(modules) > 40, (
        "only found %d modules -- the discovery step is broken, and a test that "
        "discovers nothing passes vacuously" % len(modules)
    )

    broken = []
    for mod in modules:
        # This module is mid-import right now; importing it again is a no-op
        # but there is no reason to recurse into the runner.
        if mod.endswith("tests.test_module_imports"):
            continue
        try:
            import_module(mod)
        except Exception as exc:
            broken.append((mod, type(exc).__name__, str(exc).splitlines()[0][:120]))

    if broken:
        lines = ["%d of %d tracked modules failed to import:" % (len(broken), len(modules))]
        lines += ["    %s -> %s: %s" % b for b in broken]
        raise AssertionError("\n".join(lines))

    print("      (%d tracked modules imported)" % len(modules))


def test_critical_runtime_modules_import():
    """Named guard for the entry points, so a break reports which one."""
    broken = []
    for mod in CRITICAL:
        try:
            import_module(mod)
        except Exception as exc:
            broken.append("%s -> %s: %s" % (mod, type(exc).__name__,
                                            str(exc).splitlines()[0][:120]))
    assert not broken, "critical runtime modules failed to import:\n    " + \
                       "\n    ".join(broken)


def main():
    warnings.filterwarnings("ignore")
    # Match how launch_server.py sets up sys.path so imports resolve the same
    # way they do in the running server.
    sys.path.insert(0, os.path.join(SERVER_ROOT, "src"))
    sys.path.insert(0, SERVER_ROOT)

    print("import smoke test")
    print("server root: %s\n" % SERVER_ROOT)

    tests = [
        test_every_tracked_module_imports,
        test_critical_runtime_modules_import,
    ]
    for t in tests:
        _check(t.__name__, t)

    print()
    print("Passed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()

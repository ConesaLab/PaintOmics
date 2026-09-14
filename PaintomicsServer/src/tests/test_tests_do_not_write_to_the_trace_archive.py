#!/usr/bin/env python3
"""A test must not put fabricated tool calls into the measurement corpus.

`_trace` archives every tool call to CLIENT_TMP_DIR/ai_traces. That directory is
not scratch: it is the corpus every tool-usefulness figure in this project comes
from, and it now holds servlet runs as well as benchmark ones -- the live job
driven through the UI is in there beside the 206 archived replicates.

Five suites were writing into it under job ids "T", "JOB", "FTPROBE" and
"COSTPROBE", so eight fabricated runs were sitting in the dataset. They were
found by listing the directory while looking for something else, which is not a
way to find them again.

Any suite that builds a LoopContext and calls a traced tool must redirect
CLIENT_TMP_DIR to a tempdir before the traced call. `_archive_trace` re-reads
the attribute on every call, so module scope or inside the test both work.

    python -m src.tests.test_tests_do_not_write_to_the_trace_archive
"""
from __future__ import annotations

import glob
import os
import re
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

_PASSED, _FAILED = [], []
_HERE = os.path.dirname(os.path.abspath(__file__))

# Probe job ids these suites have used. A trace file named for one of them in the
# live archive is a test's output masquerading as a run.
_PROBE_PREFIXES = ("T-", "JOB-", "FTPROBE-", "COSTPROBE-", "TRACEPROBE")


def _suites_that_trace():
    """Suites that construct a LoopContext and call a traced helper."""
    out = []
    for path in sorted(glob.glob(os.path.join(_HERE, "test_*.py"))):
        # This file talks ABOUT tracing, so it matches its own detector.
        if os.path.basename(path) == os.path.basename(__file__):
            continue
        src = open(path).read()
        # A suite traces if it builds a LoopContext by hand OR drives a workflow
        # entry point that builds one internally.
        #
        # This used to be `"LoopContext(" not in src: continue`, which detected
        # the easy case and missed the important one:
        # test_ai_agent_loop_endtoend runs the REAL entry point against a stub
        # gateway and never names LoopContext, so it slipped the check and wrote
        # SEVEN runs into the live corpus in a single suite pass. The detector
        # was skipping precisely the suite most likely to trace.
        if not ("LoopContext(" in src
                or "run_agent_loop_workflow" in src
                or "run_agent_workflow" in src):
            continue
        if not re.search(r"L\._trace|_trace\(|_pathway_block|_upgrade_chunk_papers"
                         r"|_screen_papers|read_paper|search_literature", src):
            continue
        out.append((os.path.basename(path), src))
    return out


def test_every_tracing_suite_redirects_the_archive():
    missing = [name for name, src in _suites_that_trace()
               if "CLIENT_TMP_DIR" not in src]
    assert not missing, (
        "these suites build a LoopContext and never redirect CLIENT_TMP_DIR, so "
        "their tool calls land in the live trace corpus: %s" % ", ".join(missing))


def test_the_redirect_reaches_the_writer():
    """It is enough that the redirect happens before the traced CALL.

    An earlier version of this test asserted the redirect must precede the
    `agent_loop` import, on the reasoning that serverconf is read at import time.
    It is not: `_archive_trace` does `from src.conf.serverconf import
    CLIENT_TMP_DIR` INSIDE the function, so it re-reads the module attribute on
    every call and a later redirect still takes effect. The assertion flagged two
    suites that were correct -- one of which redirects inside each test with
    save/restore, which is tighter than doing it at module scope.

    What actually matters is that the module attribute is what gets rebound, not
    a local copy, so the lazy import sees it.
    """
    wrong = []
    for name, src in _suites_that_trace():
        if "CLIENT_TMP_DIR" not in src:
            continue
        if not re.search(r"(serverconf|conf)\.CLIENT_TMP_DIR\s*=", src):
            wrong.append(name)
    assert not wrong, ("these rebind something other than the serverconf module "
                       "attribute, so the lazy import in _archive_trace will not "
                       "see it: %s" % ", ".join(wrong))


def test_the_live_archive_holds_no_stub_runs():
    """Source checks are fallible; the corpus itself is the evidence.

    Every detector in this file infers from source text, and the one above
    silently excluded the suite that mattered. This one asks the archive
    directly: a stub run stamps `"label": "stub-e2e"` into its __config__
    event, so a labelled run in the live corpus is proof a test wrote there --
    whatever the source says.
    """
    # Read the live path the same way the writer does, rather than importing a
    # name this module does not have.
    from src.conf.serverconf import CLIENT_TMP_DIR as _live
    arch = os.path.join(_live, "ai_traces")
    if not os.path.isdir(arch):
        return
    bad = []
    for f in glob.glob(os.path.join(arch, "*.jsonl")):
        try:
            with open(f) as fh:
                for line in fh:
                    # The config stamp is a JSON STRING inside a JSON field,
                    # so its quotes arrive escaped: \"label\": \"stub-e2e\".
                    # Matching '"stub-e2e"' finds nothing and the guard passes
                    # on a polluted corpus -- which is how the first version of
                    # this check reported 0 while 7 stub runs sat in the archive.
                    if "stub-e2e" in line or "label\\\": \\\"test" in line:
                        bad.append(os.path.basename(f))
                        break
                    if '"__config__"' in line:
                        break          # config is event 1; stop after it
        except OSError:
            continue
    assert not bad, (
        "%d stub/test run(s) are in the live measurement corpus, which every "
        "round in docs/dev/ai-agent-benchmark.md is scored from: %s"
        % (len(bad), ", ".join(sorted(bad)[:8])))


def test_the_live_archive_holds_no_probe_runs():
    """The corpus itself, checked. Skips when it is not present."""
    try:
        from src.conf.serverconf import CLIENT_TMP_DIR
    except Exception as exc:
        print("      (serverconf unavailable: %s; skipped)" % exc)
        return
    archive = os.path.join(CLIENT_TMP_DIR, "ai_traces")
    if not os.path.isdir(archive):
        print("      (no archive yet; skipped)")
        return
    probes = [f for f in os.listdir(archive)
              if f.startswith(_PROBE_PREFIXES)]
    assert not probes, ("test runs in the measurement corpus: %s"
                        % ", ".join(sorted(probes)[:6]))


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    # Collected, not hand-listed. A hand-written tuple runs exactly the tests
    # someone remembered to add: test_the_live_archive_holds_no_stub_runs was
    # defined, correct, and silently never executed -- reporting "Passed: 3 / 3"
    # while 61 stub runs sat in the corpus it was written to detect.
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    assert tests, "no tests collected"
    for name, t in tests:
        _check(name, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()

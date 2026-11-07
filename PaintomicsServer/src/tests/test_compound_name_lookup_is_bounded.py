#!/usr/bin/env python3
"""A metabolite name cannot fan out into an unbounded number of KEGG candidates,
and the identifier-mapping machinery cannot wedge the server.

Why this exists
---------------
2026-08-17, paintomics.uv.es (8 GB RAM, 4 GB swap). A metabolomics upload had
1,172 rows whose identifier column was ``-``. `findCompoundIDByFeatureName`
turned each into the case-insensitive regex ``.*-.*``, which matches the
19,778 KEGG compound names that contain a hyphen; `mapCompoundsIdentifiers`
clones the input feature once per hit and appends the bundle to a
`multiprocessing.Manager` list -- 23 million Feature objects, a 3.8 GB Manager
process, six 800 MB workers, the whole box in swap (0 % user CPU, 32 % iowait,
51 MB available). Every other job on the server, including an unrelated
gene-expression mapping of 12,762 features that normally takes 4 seconds, sat
"mapping" for 26 minutes; the fix at the time was ``kill -9``.

Three separate defects let a bad file take the server down, and each is
pinned here:

  * a name that is only punctuation was queried at all (placeholders such as
    ``-``, ``.``, ``?`` are the file's way of saying "no identifier");
  * the name was spliced into a regex unescaped ("NAD+" is "NA" then one or
    more "D"; a lone "." is every compound), and the number of hits was
    unbounded (a generic name is a memory bill per input row);
  * the "took too long" kill waited MAX_WAIT_THREADS *per worker process*
    (6 x 900 s = 90 minutes) instead of in total.

And a fourth, found in the same incident: the fork-child hook that PR #24
added to *prevent* stdio-lock deadlocks called ``StreamHandler.setStream``,
which flushes the inherited stream before swapping it -- the very lock the
hook exists to avoid. A Manager server child froze in that flush at 09:14:25;
its parent's ``Manager()`` blocked forever on the child's address, and one of
the four queue workers was gone until the service restarted.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_compound_name_lookup_is_bounded
"""
import logging
import multiprocessing
import os
import re
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common import FeatureNamesToKeggIDsMapper as mapper


class _FakeCursor:
    def __init__(self, docs, calls, query):
        self._docs = docs
        self._calls = calls
        self._query = query
        self._limit = None

    def limit(self, n):
        self._limit = n
        self._calls.append((self._query, n))
        return self

    def __iter__(self):
        docs = self._docs if self._limit is None else self._docs[:self._limit]
        return iter(docs)


class _FakeCollection:
    """kegg_compounds stand-in: `find({"name": {"$regex": pattern}})` evaluated in Python."""

    def __init__(self, names):
        self.docs = [{"id": "C%05d" % i, "name": n} for i, n in enumerate(names)]
        self.calls = []

    def find(self, query):
        pattern = query["name"]["$regex"]
        hits = [d for d in self.docs if pattern.search(d["name"])]
        return _FakeCursor(hits, self.calls, pattern.pattern)


class _FakeDB:
    def __init__(self, names):
        self.kegg_compounds = _FakeCollection(names)


NAMES = ["Adenosine 5'-triphosphate", "NAD+", "NADH", "NADP+", "L-Leucine",
         "D-Glucose", "Glucose", "Glucose 6-phosphate", "(R)-Lactate", "Acid"]


class PlaceholderNamesTest(unittest.TestCase):

    def test_punctuation_only_names_are_placeholders(self):
        for name in ["-", "", "   ", ".", "?", "--", "/", None]:
            self.assertTrue(mapper.isPlaceholderCompoundName(name), repr(name))

    def test_names_with_a_letter_or_digit_are_not(self):
        for name in ["-a", "1", "NAD+", "N/A", "Fe"]:
            self.assertFalse(mapper.isPlaceholderCompoundName(name), repr(name))

    def test_a_placeholder_never_reaches_the_database(self):
        db = _FakeDB(NAMES)
        matches, found = mapper.findCompoundIDByFeatureName("job", "-", db)
        self.assertEqual((matches, found), ([], False))
        self.assertEqual(db.kegg_compounds.calls, [])


class EscapedSubstringLookupTest(unittest.TestCase):

    def test_metacharacters_in_the_name_are_literal(self):
        db = _FakeDB(NAMES)
        matches, found = mapper.findCompoundIDByFeatureName("job", "NAD+", db)
        self.assertTrue(found)
        self.assertEqual(sorted(m["name"] for m in matches), ["NAD+"])
        # Unescaped, "NAD+" would also have hit NADH and NADP+ ("NA" + "D"+).
        pattern, limit = db.kegg_compounds.calls[0]
        self.assertEqual(pattern, re.escape("NAD+"))
        self.assertEqual(limit, mapper.MAX_COMPOUND_MATCHES + 1)

    def test_parentheses_match_themselves(self):
        db = _FakeDB(NAMES)
        matches, found = mapper.findCompoundIDByFeatureName("job", "(R)-Lactate", db)
        self.assertEqual([m["name"] for m in matches], ["(R)-Lactate"])

    def test_still_a_case_insensitive_substring_search(self):
        db = _FakeDB(NAMES)
        matches, found = mapper.findCompoundIDByFeatureName("job", "glucose", db)
        self.assertEqual(sorted(m["name"] for m in matches),
                         ["D-Glucose", "Glucose", "Glucose 6-phosphate"])


class BoundedMatchesTest(unittest.TestCase):

    def setUp(self):
        self._cap = mapper.MAX_COMPOUND_MATCHES
        mapper.MAX_COMPOUND_MATCHES = 5

    def tearDown(self):
        mapper.MAX_COMPOUND_MATCHES = self._cap

    def test_a_generic_name_keeps_only_its_exact_hits(self):
        names = ["acid"] + ["%d-oxo acid" % i for i in range(20)]
        db = _FakeDB(names)
        with self.assertLogs(level="WARNING") as logs:
            matches, found = mapper.findCompoundIDByFeatureName("job", "acid", db)
        self.assertTrue(found)
        self.assertEqual([m["name"] for m in matches], ["acid"])
        self.assertIn("MORE THAN 5", logs.output[0])
        # Two queries: the bounded substring probe, then the anchored exact one.
        self.assertEqual([c[0] for c in db.kegg_compounds.calls],
                         [re.escape("acid"), "^" + re.escape("acid") + "$"])

    def test_a_generic_name_with_no_exact_hit_is_unmatched(self):
        db = _FakeDB(["%d-hydroxy-acid" % i for i in range(20)])
        with self.assertLogs(level="WARNING"):
            matches, found = mapper.findCompoundIDByFeatureName("job", "hydroxy", db)
        self.assertEqual((matches, found), ([], False))

    def test_under_the_cap_every_hit_is_returned(self):
        db = _FakeDB(["%d-oxo acid" % i for i in range(5)])
        matches, found = mapper.findCompoundIDByFeatureName("job", "acid", db)
        self.assertEqual(len(matches), 5)
        self.assertEqual(len(db.kegg_compounds.calls), 1)


class SharedDeadlineJoinTest(unittest.TestCase):

    def test_the_budget_is_shared_not_per_process(self):
        procs = [multiprocessing.Process(target=time.sleep, args=(60,)) for _ in range(4)]
        for proc in procs:
            proc.start()
        try:
            began = time.monotonic()
            mapper._joinAllWithinDeadline(procs, 1)
            # One second in all; one per process would be four.
            self.assertLess(time.monotonic() - began, 3)
            self.assertEqual(mapper._stillRunning(procs), procs)
        finally:
            for proc in procs:
                proc.kill()
                proc.join(5)

    def test_it_ends_when_the_last_process_ends_and_collects_them_all(self):
        procs = [multiprocessing.Process(target=time.sleep, args=(seconds,)) for seconds in (0.4, 0, 0.2)]
        for proc in procs:
            proc.start()
        began = time.monotonic()
        mapper._joinAllWithinDeadline(procs, 30)
        self.assertLess(time.monotonic() - began, 15)
        self.assertEqual([proc._popen.returncode for proc in procs], [0, 0, 0])


class _NeverFlushStream:
    def __init__(self):
        self.flushed = False
        self.closed = False

    def write(self, s):
        pass

    def flush(self):
        self.flushed = True

    def close(self):
        self.closed = True


class ForkChildStreamHookTest(unittest.TestCase):

    def setUp(self):
        self._stdout, self._stderr = sys.stdout, sys.stderr
        self._sigterm = signal.getsignal(signal.SIGTERM)
        self._logger = logging.getLogger("paintomics.test.forkhook")
        self._tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        self._tmp.close()

    def tearDown(self):
        for h in list(self._logger.handlers):
            self._logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        sys.stdout, sys.stderr = self._stdout, self._stderr
        signal.signal(signal.SIGTERM, self._sigterm)
        os.unlink(self._tmp.name)

    def test_inherited_streams_are_replaced_without_being_flushed(self):
        inherited = _NeverFlushStream()
        stream_handler = logging.StreamHandler(inherited)
        file_handler = logging.FileHandler(self._tmp.name)
        old_file_stream = file_handler.stream
        self._logger.addHandler(stream_handler)
        self._logger.addHandler(file_handler)
        before = len(mapper._inheritedStreams)

        mapper._refreshStandardStreamsInForkChild()

        # New stream objects on both handlers...
        self.assertIs(stream_handler.stream, sys.stderr)
        self.assertIsNot(file_handler.stream, old_file_stream)
        self.assertIsNot(sys.stdout, self._stdout)
        # ...and the parent's were neither flushed nor closed (both would take
        # the buffer lock a vanished thread may hold), and are kept alive so
        # no finaliser does it later.
        self.assertFalse(inherited.flushed)
        self.assertFalse(inherited.closed)
        self.assertFalse(old_file_stream.closed)
        kept = mapper._inheritedStreams[before:]
        self.assertIn(inherited, kept)
        self.assertIn(old_file_stream, kept)
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)
        # The refreshed handlers work.
        self._logger.warning("child says hi")
        file_handler.flush()
        with open(self._tmp.name) as fh:
            self.assertIn("child says hi", fh.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)

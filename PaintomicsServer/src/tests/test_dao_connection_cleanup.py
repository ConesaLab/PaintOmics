#!/usr/bin/env python3
"""A DAO connection must be closed even when the operation using it raises.

Why this exists
---------------
It began as a thread leak. `DBmanager.openConnection` used to build a **new
`MongoClient` per DAO**, each with its own PyMongo topology-monitor threads:
measured on this machine, ten managers left unclosed took the process from 1
thread to 26, and closing them returned it to 1. A leaked DAO was ~2.5 leaked
threads plus its sockets.

**That cost is gone.** `DBmanager` now hands out one process-wide client keyed
on `(host, port, pid)`, `closeConnection` is a reference drop, and
`getCollection` looks the client up on every call -- so an unclosed manager
costs nothing at all. `SharedClientCostTest` below measures exactly that, and
it is what the old `LeakCostTest` was replaced with: a test that asserted
leaking still costs threads would now be asserting the bug this change removed.

The finally rule above survives the reason it was written for. What it enforces
now is that a DAO does not outlive the request that made it: a manager kept
past its handler is the one way a stale client reference can still be carried
across a `fork()` (the mapper and enrichment workers are forked children), and
"close it in a finally" is the shape the codebase settled on for that.

Several handlers closed theirs on the success path only:

    daoInstance = MessageDAO()
    matchedMessages = daoInstance.findAll(...)   # <- raises
    daoInstance.closeConnection()                # <- skipped

The `except` below catches the error and the request returns a tidy failure, so
nothing looks wrong from outside. What makes this worth fixing is *when* it
fires: `findAll` raises when MongoDB is unreachable or slow, which is exactly
when every request starts failing at once. Each one then leaks a client whose
monitor threads keep retrying the database that is already struggling, so the
failure mode compounds instead of settling.

`adminServletGetMessage` is the sharpest case -- it backs the public welcome
banner (`message_type == "starting_message"` skips the session check), so it is
hit on every page load by every visitor.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_dao_connection_cleanup
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common import DBmanager as DBmanagerModule

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Functions that take a DAO connection while serving a request. Named
# individually so adding one is deliberate rather than pattern-matched.
MUST_CLOSE_IN_FINALLY = [
    ("servlets/AdminServlet.py", "adminServletGetMessage"),
    ("servlets/AdminServlet.py", "adminServletSaveMessage"),
    ("servlets/AdminServlet.py", "adminServletDeleteMessage"),
    ("servlets/DataManagementServlet.py", "registerFile"),
    ("common/JobInformationManager.py", "getVisualOptions"),
    ("common/JobInformationManager.py", "storeVisualOptions"),
    # The graph walk's queued job: it writes progress every leg and a heartbeat
    # every minute, and its client must come back whatever the walk raised.
    ("classes/AIInterpret/walker/service.py", "run_job"),
]


def _closesInFinally(relativePath, functionName):
    """(found, closesInFinally) for one function."""
    path = os.path.join(_SRC, relativePath)
    source = open(path).read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != functionName:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Try):
                for statement in sub.finalbody:
                    text = ast.get_source_segment(source, statement) or ""
                    if "closeConnection" in text:
                        return True, True
        body = ast.get_source_segment(source, node) or ""
        return True, "closeConnection" not in body   # no close at all is fine
    return False, False


class ConnectionCleanupTest(unittest.TestCase):

    def test_every_request_scoped_dao_closes_in_a_finally(self):
        offending = []
        for relativePath, functionName in MUST_CLOSE_IN_FINALLY:
            found, ok = _closesInFinally(relativePath, functionName)
            if not found:
                offending.append("%s: %s no longer exists" % (relativePath, functionName))
            elif not ok:
                offending.append("%s: %s closes its DAO only on the success path"
                                 % (relativePath, functionName))

        self.assertEqual(
            offending, [],
            "a DAO connection is left open when the operation raises. Each one "
            "is a MongoClient with its own monitor threads, and the raising "
            "case is a database that is already unreachable:\n  "
            + "\n  ".join(offending))


class SharedClientCostTest(unittest.TestCase):
    """The measurement behind the rule, re-taken after the shared client.

    Replaces the old LeakCostTest, which asserted that unclosed managers cost
    threads. They no longer do, and the old test only stayed green by running
    before anything else had opened the process client -- so it would have gone
    red the moment another Mongo-touching test sorted ahead of it, on a change
    that is behaving perfectly.
    """

    def _warmedManager(self):
        """A manager whose query has already built the process client."""
        try:
            manager = DBmanagerModule.DBmanager()
            manager.getCollection("userCollection").find_one({})
            return manager
        except Exception as exc:
            raise unittest.SkipTest("no reachable mongod: %s" % exc)

    def test_unclosed_managers_cost_no_threads(self):
        import threading

        self._warmedManager()          # the client (and its monitor) now exists

        baseline = threading.active_count()
        managers = []
        for _ in range(10):
            manager = DBmanagerModule.DBmanager()
            manager.getCollection("userCollection").find_one({})
            managers.append(manager)   # deliberately never closed

        self.assertEqual(
            threading.active_count(), baseline,
            "ten unclosed DBmanagers cost threads again. They are supposed to "
            "share one process-wide MongoClient; if each is building its own, "
            "the connection-per-DAO regression is back")

        self.assertEqual(
            len({id(manager.getConnection()) for manager in managers}), 1,
            "the managers did not all get the same client object")

    def test_closing_does_not_disturb_the_process_client(self):
        import threading
        import time

        manager = self._warmedManager()
        client = manager.getConnection()

        others = []
        for _ in range(10):
            other = DBmanagerModule.DBmanager()
            other.getCollection("userCollection").find_one({})
            others.append(other)

        baseline = threading.active_count()
        for other in others:
            other.closeConnection()
        time.sleep(0.5)

        self.assertEqual(threading.active_count(), baseline,
                         "closeConnection tore something down; it is supposed "
                         "to be a reference drop on a shared client")
        self.assertIsNone(others[0].getConnection(),
                          "closeConnection must still clear this manager's reference")

        # the client is untouched and still serves the next caller
        after = DBmanagerModule.DBmanager()
        after.getCollection("userCollection").find_one({})
        self.assertIs(after.getConnection(), client,
                      "a closed manager took the process client with it")


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

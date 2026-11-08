#!/usr/bin/env python3
"""Tests for configureLogging.

Two requirements pull against each other here.

fileConfig defaults to disable_existing_loggers=True, which switches off every
logging.getLogger(__name__) created at import time. That silently dropped all 41
diagnostic calls in the AIInterpret package.

Passing False fixes that, but it also un-silences third-party loggers that the
same side effect had been suppressing. pymongo 4 logs every command *and its
reply* on pymongo.command at DEBUG, and logging.cfg puts the root logger at
DEBUG -- so query results, including user gene lists and job contents, started
being written into application.log at roughly 5000 lines an hour.

The same root DEBUG let APScheduler write "Looking for jobs to run" and "Next
wakeup is due" on every wakeup of the cleaning cron; on paintomics.uv.es the
uWSGI log, which takes the console handler's copy, reached 7.7 GB. The other
libraries quieted alongside it were found at DEBUG in paintomics.org's log.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_logging_setup
"""
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.common.LoggingSetup import configureLogging, NOISY_THIRD_PARTY_LOGGERS

CONFIG = """[loggers]
keys=root

[handlers]
keys=consoleHandler

[formatters]
keys=info

[logger_root]
level=DEBUG
handlers=consoleHandler

[handler_consoleHandler]
class=StreamHandler
formatter=info
args=(sys.stdout,)

[formatter_info]
format=%(levelname)s - %(message)s
"""


class ConfigureLoggingTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Created before configureLogging runs, which is exactly the case that
        # disable_existing_loggers=True used to break.
        cls.moduleLogger = logging.getLogger("src.classes.AIInterpret.agent")

        handle = tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False)
        handle.write(CONFIG)
        handle.close()
        cls.configPath = handle.name
        configureLogging(cls.configPath)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.configPath)

    def test_module_logger_created_before_config_still_emits(self):
        self.assertFalse(self.moduleLogger.disabled)
        self.assertTrue(self.moduleLogger.isEnabledFor(logging.INFO))

    def test_pymongo_command_logging_is_quiet(self):
        # The specific logger that was writing query replies to disk.
        self.assertFalse(logging.getLogger("pymongo.command").isEnabledFor(logging.DEBUG))
        self.assertFalse(logging.getLogger("pymongo.command").isEnabledFor(logging.INFO))

    def test_every_declared_noisy_logger_is_quieted(self):
        for name in NOISY_THIRD_PARTY_LOGGERS:
            with self.subTest(logger=name):
                self.assertFalse(logging.getLogger(name).isEnabledFor(logging.DEBUG))

    def test_third_party_warnings_still_get_through(self):
        # Quieting must not mean silencing: a genuine pymongo warning is
        # something an operator needs to see.
        self.assertTrue(logging.getLogger("pymongo").isEnabledFor(logging.WARNING))

    def test_root_logger_still_at_debug(self):
        # The application's own verbosity is unchanged; only the noisy
        # third-party trees were lowered.
        self.assertTrue(logging.getLogger().isEnabledFor(logging.DEBUG))

    def test_paintomics_module_loggers_still_at_debug(self):
        for name in ("src.classes.AIInterpret.walker.writer", "src.common.PySiQ",
                     "omnipath", "paintomics"):
            with self.subTest(logger=name):
                self.assertTrue(logging.getLogger(name).isEnabledFor(logging.DEBUG))

    def test_library_loggers_seen_at_debug_in_production_are_quiet(self):
        # Named one by one, as the libraries name them, rather than read back
        # from NOISY_THIRD_PARTY_LOGGERS: each was found writing DEBUG lines
        # into paintomics.org's application.log (2026-11-08).
        for name in ("apscheduler.scheduler",       # "Looking for jobs to run"
                     "apscheduler.executors.default",
                     "httpcore.http11", "httpcore.connection",
                     "openai._base_client",         # "HTTP Response: POST 200"
                     "openai.agents",               # "Running agent Writer (turn 3)"
                     "openai.agents.tracing",       # "Tracing is disabled. Not creating span"
                     "tzlocal",
                     "PIL.PngImagePlugin",          # "STREAM b'IHDR' 16 13"
                     "asyncio"):
            with self.subTest(logger=name):
                logger = logging.getLogger(name)
                self.assertFalse(logger.isEnabledFor(logging.INFO))
                self.assertTrue(logger.isEnabledFor(logging.WARNING))


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class SchedulerWakeupsTest(unittest.TestCase):
    """The server's real scheduler, under the server's logging setup.

    paintomicsserver.startScheludeTasks runs the cleaning cron on an APScheduler
    BackgroundScheduler, which logs "Looking for jobs to run" and "Next wakeup
    is due ..." at DEBUG on every wakeup. Under logging.cfg's DEBUG root they
    went to application.log and to stdout, and on paintomics.uv.es stdout is
    the uWSGI log, which reached 7.7 GB.
    """

    @classmethod
    def setUpClass(cls):
        handle = tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False)
        handle.write(CONFIG)
        handle.close()
        cls.addClassCleanup(os.unlink, handle.name)
        configureLogging(handle.name)

    def _runScheduler(self):
        """Start a BackgroundScheduler, run one job, stop it; the records the
        root logger's handlers were given meanwhile."""
        import threading
        from apscheduler.schedulers.background import BackgroundScheduler

        capture = _Capture()
        root = logging.getLogger()
        root.addHandler(capture)
        ran = threading.Event()
        scheduler = BackgroundScheduler(daemon=True)
        try:
            scheduler.start()
            scheduler.add_job(ran.set)     # no trigger: runs once, now
            self.assertTrue(ran.wait(10), "the scheduled job never ran")
        finally:
            scheduler.shutdown(wait=True)
            root.removeHandler(capture)
        return capture.records

    def test_wakeups_do_not_reach_the_log(self):
        chatter = ["%s %s: %s" % (record.levelname, record.name, record.getMessage())
                   for record in self._runScheduler()
                   if record.name.startswith(("apscheduler", "tzlocal"))
                   and record.levelno < logging.WARNING]
        self.assertEqual(chatter, [])

    def test_the_scheduler_does_say_it_when_allowed(self):
        """Control: with apscheduler at DEBUG the same run logs the wakeup
        line, so the test above is looking at the real messages."""
        scheduler = logging.getLogger("apscheduler")
        before = scheduler.level
        scheduler.setLevel(logging.DEBUG)
        try:
            messages = [record.getMessage() for record in self._runScheduler()]
        finally:
            scheduler.setLevel(before)
        self.assertIn("Looking for jobs to run", messages)


if __name__ == "__main__":
    unittest.main(verbosity=2)

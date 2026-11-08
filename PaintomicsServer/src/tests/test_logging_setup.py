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

The same root DEBUG lets APScheduler write "Looking for jobs to run" and "Next
wakeup is due" on every wakeup of the cleaning cron, and the AI client
libraries a line for every step of every request. The libraries quieted here
were all found at DEBUG in paintomics.org's application.log.

paintomics.uv.es writes the console handler's copy of every line, plus
uWSGI's own, to one uwsgi.log that nothing rotated; it reached 7.7 GB.
paintomics4.ini now caps it. What filled it is not established: on
paintomics.org the cron wakes once a day, so its lines alone come nowhere near
that size.

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
    went to application.log and to stdout, which on paintomics.uv.es is the
    uWSGI log.
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
        from datetime import datetime
        from apscheduler.events import EVENT_JOB_SUBMITTED
        from apscheduler.schedulers.background import BackgroundScheduler

        capture = _Capture()
        root = logging.getLogger()
        root.addHandler(capture)
        ran = threading.Event()
        submitted = threading.Event()
        scheduler = BackgroundScheduler(daemon=True)
        # APScheduler sends this event after its thread has finished with the
        # job store for this wakeup. Stopping before then raced it: shutdown()
        # empties the store while the thread still updates the job, and the
        # thread died printing a JobLookupError traceback into the test output.
        scheduler.add_listener(lambda event: submitted.set(), EVENT_JOB_SUBMITTED)
        try:
            scheduler.start()
            # The server's own trigger (startScheludeTasks), made due now.
            scheduler.add_job(ran.set, "interval", hours=24,
                              next_run_time=datetime.now(scheduler.timezone))
            self.assertTrue(ran.wait(10), "the scheduled job never ran")
            self.assertTrue(submitted.wait(10), "the scheduler never finished its wakeup")
        finally:
            # Joins the scheduler's thread, so its last line ("Next wakeup is
            # due ...") has been logged by the time this returns.
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
        """Control: with apscheduler at DEBUG the same run logs both wakeup
        lines, so the test above is looking at the real messages."""
        scheduler = logging.getLogger("apscheduler")
        before = scheduler.level
        scheduler.setLevel(logging.DEBUG)
        try:
            messages = [record.getMessage() for record in self._runScheduler()]
        finally:
            scheduler.setLevel(before)
        self.assertIn("Looking for jobs to run", messages)
        self.assertTrue([m for m in messages if m.startswith("Next wakeup is due")],
                        messages)


class UwsgiLogIsCappedTest(unittest.TestCase):
    """paintomics4.ini (paintomics.uv.es) must keep its uwsgi.log bounded.

    That file takes the console copy of every application log line and
    uWSGI's own lines, and nothing rotated it: it reached 7.7 GB. In the
    container deployment Docker caps the same output (compose.yaml, json-file
    max-size 20m x 5). With log-maxsize the uWSGI master renames the file once
    it passes the size and opens a new one; log-backupname makes the renamed
    file always the same one, so each rotation replaces the last. Without
    log-backupname uWSGI names each one uwsgi.log.<timestamp> and keeps them
    all, and the folder grows just the same.

    Checked against uWSGI 2.0.31, the version on paintomics.uv.es, with a
    worker printing about 170 KB a second for 12 seconds and log-maxsize at
    200 KB: uwsgi.log ended at 13 KB and uwsgi.log.1 at 340 KB, the worker's
    lines running on into the new file with none missing. Without the two
    settings the same run left one 2 MB file.
    """

    INI = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                       "paintomics4.ini"))

    def setUp(self):
        if not os.path.exists(self.INI):
            self.skipTest("paintomics4.ini is not in this checkout")
        from src.tests.test_uwsgi_threads_run_without_post_buffering import read_ini
        self.options = read_ini(self.INI)

    def test_the_log_goes_to_a_file(self):
        # The premise: without logto, uWSGI writes to the systemd journal,
        # which has its own limits, and the cap below would be moot.
        self.assertTrue(self.options.get("logto"))

    def test_the_log_has_a_size_limit(self):
        maxsize = int(self.options.get("log-maxsize", "0"))
        self.assertGreater(maxsize, 0, "uwsgi.log grows without limit")
        self.assertLessEqual(maxsize, 1024 ** 3)

    def test_rotation_keeps_one_older_file(self):
        backup = self.options.get("log-backupname", "")
        self.assertTrue(backup, "each rotation would keep another uwsgi.log.<timestamp>")
        logto = self.options["logto"]
        self.assertNotEqual(backup, logto)
        self.assertEqual(os.path.dirname(backup), os.path.dirname(logto))


if __name__ == "__main__":
    unittest.main(verbosity=2)

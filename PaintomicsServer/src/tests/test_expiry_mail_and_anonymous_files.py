#!/usr/bin/env python3
"""Expiry warnings, mail that must not be sent, and files of anonymous jobs.

Three problems seen on paintomics.org in November 2026:

1. One user got seven "job expiring" mails within fifteen seconds, one per job
   (clean_databases sent a message per job, back to back).
2. A Gmail address that does not exist was mailed on every run and bounced
   every time. The server cannot see those bounces -- Gmail's SMTP accepts the
   address and bounces later, to our own mailbox -- so an administrator turns
   mail off for the account (AdminTools/scripts/user_mail.py), and an address
   the SMTP server refuses while sending is turned off automatically.
3. Retention removed anonymous jobs but never their uploads: 15,603 files and
   11 GB in CLIENT_TMP/nologin/inputData, the oldest two years old, on a server
   that promises anonymous work is removed after seven days.

Everything runs against a scratch database and a scratch CLIENT_TMP. Mail goes
to a fake smtplib.SMTP that records what would have been sent, so the real
Util.sendEmail runs and nothing is delivered. The server's own database is
never opened.

The database-backed classes skip when MongoDB is not reachable.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_expiry_mail_and_anonymous_files
"""
import datetime
import email
import email.header
import importlib.util
import io
import os
import re
import shutil
import smtplib
import sys
import tempfile
import time
import tokenize
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))

SCRATCH_DB = "PaintomicsExpiryMailTestDB"


def _ensureServerConfig():
    """Bind src.conf.serverconf in memory on a checkout that has none.

    Same pattern as test_email_templates: never written to disk.
    """
    try:
        import src.conf.serverconf  # noqa: F401 -- availability probe
        return
    except ImportError:
        pass
    template = os.path.join(SRC, "resources", "example_serverconf.py")
    spec = importlib.util.spec_from_file_location("src.conf.serverconf", template)
    module = importlib.util.module_from_spec(spec)
    sys.modules["src.conf.serverconf"] = module
    spec.loader.exec_module(module)


_ensureServerConfig()


def _stamp(daysAgo):
    """An accessDate as the application writes it: "%Y%m%d%H%M"."""
    day = datetime.date.today() - datetime.timedelta(days=daysAgo)
    return day.strftime("%Y%m%d") + "1200"


def _age(path, days):
    """Set path's modification time to `days` days ago."""
    when = time.time() - days * 86400
    os.utime(path, (when, when))


class FakeSMTP(object):
    """Stands in for smtplib.SMTP. Records every message; can refuse or fail."""

    sent = []        # [(to, email.message.Message)]
    attempts = []    # every address sendmail was called with
    refuse = {}      # address -> (code, reply bytes), raised as SMTPRecipientsRefused
    fail = None      # an exception to raise from sendmail

    def __init__(self, *args, **kwargs):
        pass

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def quit(self):
        pass

    def sendmail(self, *args):
        to, text = args[1], args[2]          # (from_addr, to_addrs, msg)
        FakeSMTP.attempts.append(to)
        if FakeSMTP.fail is not None:
            raise FakeSMTP.fail
        if to in FakeSMTP.refuse:
            raise smtplib.SMTPRecipientsRefused({to: FakeSMTP.refuse[to]})
        FakeSMTP.sent.append((to, email.message_from_string(text)))

    @classmethod
    def reset(cls):
        cls.sent = []
        cls.attempts = []
        cls.refuse = {}
        cls.fail = None


def _body(message):
    for part in message.walk():
        if part.get_content_type() in ("text/html", "text/plain"):
            return part.get_payload(decode=True).decode("utf-8", "replace")
    return ""


def _subject(message):
    return str(email.header.make_header(email.header.decode_header(message["Subject"])))


class _ScratchServer(unittest.TestCase):
    """A scratch database, a scratch CLIENT_TMP and the fake mail server."""

    @classmethod
    def setUpClass(cls):
        try:
            from pymongo import MongoClient
            from src.conf.serverconf import MONGODB_HOST, MONGODB_PORT
            cls.client = MongoClient(MONGODB_HOST, MONGODB_PORT, serverSelectionTimeoutMS=3000)
            cls.client.server_info()
        except Exception as ex:
            raise unittest.SkipTest("MongoDB is not reachable: %s" % ex)
        import src.AdminTools.scripts.clean_databases as cleaner
        import src.conf.serverconf as serverconf
        cls.cleaner = cleaner
        cls.serverconf = serverconf

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "client", None) is not None:
            cls.client.drop_database(SCRATCH_DB)
            cls.client.close()

    def setUp(self):
        self.client.drop_database(SCRATCH_DB)
        self.db = self.client[SCRATCH_DB]
        self.tmp = tempfile.mkdtemp(prefix="paintomics-expiry-test-")
        self.clientTmp = self.tmp + "/"
        os.makedirs(os.path.join(self.tmp, "nologin", "inputData"))
        os.makedirs(os.path.join(self.tmp, "nologin", "jobsData"))
        os.makedirs(os.path.join(self.tmp, "nologin", "tmp"))
        os.makedirs(os.path.join(self.tmp, "gtfcache"))

        self._saved = {
            "MONGODB_DATABASE": self.cleaner.MONGODB_DATABASE,
            "CLIENT_TMP_DIR": self.cleaner.CLIENT_TMP_DIR,
            "MongoClient": self.cleaner.MongoClient,
            "rebuildIndexes": self.cleaner.rebuildIndexes,
        }
        self.cleaner.MONGODB_DATABASE = SCRATCH_DB
        self.cleaner.CLIENT_TMP_DIR = self.clientTmp
        # cleanDatabases opens its own client; hand it ours.
        self.cleaner.MongoClient = lambda *args, **kwargs: self.client
        # Index creation would walk every organism database on the server.
        self.cleaner.rebuildIndexes = lambda connection: None

        self._savedSMTP = smtplib.SMTP
        smtplib.SMTP = FakeSMTP
        self._savedPassword = getattr(self.serverconf, "SMTP_PASSWORD", None)
        # Only has to be non-empty: FakeSMTP never checks it.
        self.serverconf.SMTP_PASSWORD = "unused"
        FakeSMTP.reset()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(self.cleaner, name, value)
        smtplib.SMTP = self._savedSMTP
        self.serverconf.SMTP_PASSWORD = self._savedPassword
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- seeding ------------------------------------------------------------

    def addUser(self, userID, emailAddress, **extra):
        os.makedirs(os.path.join(self.tmp, str(userID), "jobsData"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, str(userID), "inputData"), exist_ok=True)
        user = {"userID": userID, "userName": "User %d" % userID, "email": emailAddress,
                "is_guest": False, "last_login": datetime.date.today().strftime("%Y%m%d")}
        user.update(extra)
        self.db.userCollection.insert_one(user)

    def addJob(self, jobID, userID, daysAgo, **extra):
        job = {"jobID": jobID, "userID": None if userID is None else str(userID),
               "accessDate": _stamp(daysAgo), "name": ""}
        job.update(extra)
        self.db.jobInstanceCollection.insert_one(job)

    def run_cleaner(self, **kwargs):
        self.cleaner.cleanDatabases(force=True, **kwargs)

    def sentTo(self, address):
        return [message for to, message in FakeSMTP.sent if to == address]


class OneWarningPerUserTest(_ScratchServer):

    WINDOW_DAY = 7     # MAX_JOB_DAYS - REMINDER_WINDOW_DAYS: the first day a job is warned about

    def seedSevenJobs(self):
        self.addUser(21, "ada@example.org")
        self.jobIDs = ["Ad%08d" % n for n in range(7)]
        for jobID in self.jobIDs:
            self.addJob(jobID, 21, self.WINDOW_DAY)

    def test_seven_expiring_jobs_make_one_message(self):
        """The reported case: seven jobs on the same day, seven mails."""
        self.seedSevenJobs()
        self.run_cleaner()
        messages = self.sentTo("ada@example.org")
        self.assertEqual(1, len(messages),
                         "one user with seven expiring jobs got %d messages" % len(messages))
        body = _body(messages[0])
        for jobID in self.jobIDs:
            self.assertIn(jobID, body, "the message does not list job %s" % jobID)
        self.assertIn("7 jobs", _subject(messages[0]))

    def test_the_message_gives_each_jobs_deletion_date(self):
        self.seedSevenJobs()
        self.run_cleaner()
        from src.common.EmailTemplates import longDate
        deletion = (datetime.date.today() - datetime.timedelta(days=self.WINDOW_DAY)
                    + datetime.timedelta(days=self.cleaner.MAX_JOB_DAYS + 1))
        self.assertIn(longDate(deletion), _body(self.sentTo("ada@example.org")[0]))

    def test_the_stated_deletion_date_is_the_day_the_job_goes(self):
        """Not a day early, not a day late, checked against the removal rule."""
        today = datetime.date.today()
        days = self.cleaner.MAX_JOB_DAYS
        self.addJob("GoesToday0", 21, days + 1)
        self.addJob("GoesTomrw0", 21, days)
        removed = self.cleaner.checkRemoveJobsForUser(self.client, 21)
        self.assertEqual(today, self.cleaner.jobDeletionDate(
            today - datetime.timedelta(days=days + 1)))
        self.assertIn("GoesToday0", removed)
        self.assertEqual(today + datetime.timedelta(days=1), self.cleaner.jobDeletionDate(
            today - datetime.timedelta(days=days)))
        self.assertNotIn("GoesTomrw0", removed)

    def test_a_second_run_sends_nothing(self):
        self.seedSevenJobs()
        self.run_cleaner()
        self.run_cleaner()
        self.assertEqual(1, len(self.sentTo("ada@example.org")))

    def test_two_users_get_one_message_each(self):
        self.seedSevenJobs()
        self.addUser(25, "bob@example.org")
        self.addJob("Bo00000001", 25, self.WINDOW_DAY)
        self.addJob("Bo00000002", 25, self.WINDOW_DAY + 2)
        self.run_cleaner()
        self.assertEqual(1, len(self.sentTo("ada@example.org")))
        bob = self.sentTo("bob@example.org")
        self.assertEqual(1, len(bob))
        self.assertIn("Bo00000001", _body(bob[0]))
        self.assertIn("Bo00000002", _body(bob[0]))
        self.assertNotIn(self.jobIDs[0], _body(bob[0]), "one user's job in another's mail")

    def test_at_most_one_warning_per_user_per_day(self):
        """Two runs on one day (the timer and the admin button) mail once."""
        self.addUser(26, "cy@example.org")
        self.addJob("Cy00000001", 26, self.WINDOW_DAY)
        today = datetime.date.today()
        first = self.cleaner.checkRemindJobsForUser(self.client, 26, details=True)
        self.assertEqual(["Cy00000001"],
                         self.cleaner.remindUserOfJobs(self.client, 26, first, "", today=today))
        self.addJob("Cy00000002", 26, self.WINDOW_DAY)
        second = self.cleaner.checkRemindJobsForUser(self.client, 26, details=True)
        self.assertEqual(["Cy00000002"], [job["jobID"] for job in second])
        self.assertEqual([], self.cleaner.remindUserOfJobs(self.client, 26, second, "", today=today))
        self.assertEqual(1, len(self.sentTo("cy@example.org")))
        self.assertIsNone(
            self.db.jobInstanceCollection.find_one({"jobID": "Cy00000002"}).get("reminderSent"),
            "a job left out of today's warning was marked as warned, so it never will be")
        tomorrow = today + datetime.timedelta(days=1)
        self.assertEqual(["Cy00000002"],
                         self.cleaner.remindUserOfJobs(self.client, 26, second, "", today=tomorrow))
        self.assertEqual(2, len(self.sentTo("cy@example.org")))


class SameDeadlineNeverTwiceTest(_ScratchServer):

    def test_a_job_is_warned_once_per_deadline(self):
        self.addUser(31, "dee@example.org")
        accessDay = datetime.date.today() - datetime.timedelta(days=8)
        deadline = self.cleaner.jobDeletionDate(accessDay).strftime("%Y%m%d")
        # Already warned for this very deadline.
        self.addJob("Dd00000001", 31, 8, reminderSent=deadline)
        # Written before this change: 1 means "warned since last opened".
        self.addJob("Dd00000002", 31, 8, reminderSent=1)
        # Warned for an earlier deadline that has since moved.
        self.addJob("Dd00000003", 31, 8, reminderSent="20000101")
        self.run_cleaner()
        messages = self.sentTo("dee@example.org")
        self.assertEqual(1, len(messages))
        body = _body(messages[0])
        self.assertNotIn("Dd00000001", body, "warned twice for the same deadline")
        self.assertNotIn("Dd00000002", body, "a job warned before this change was warned again")
        self.assertIn("Dd00000003", body, "a job with a new deadline was not warned")
        self.assertEqual(deadline, self.db.jobInstanceCollection.find_one(
            {"jobID": "Dd00000003"})["reminderSent"])


class MailThatMustNotBeSentTest(_ScratchServer):

    def test_a_guest_account_is_never_warned(self):
        """A guest's address is made up; the old code mailed it on day 7."""
        self.addUser(41, "guest12345@paintomics.example", is_guest=True)
        self.addJob("Gu00000001", 41, 7)
        self.run_cleaner()
        self.assertEqual([], FakeSMTP.attempts)

    def test_an_account_with_mail_turned_off_gets_nothing(self):
        self.addUser(42, "gone@example.org", mailDisabled=True,
                     mailDisabledReason="bounced: no such user")
        self.addJob("Of00000001", 42, 7)
        self.run_cleaner()
        self.assertEqual([], FakeSMTP.attempts)
        self.assertIsNone(
            self.db.jobInstanceCollection.find_one({"jobID": "Of00000001"}).get("reminderSent"),
            "a job that was not warned about is recorded as warned")

    def test_an_address_the_server_refuses_has_its_mail_turned_off(self):
        self.addUser(43, "nobody@example.org")
        self.addJob("Rf00000001", 43, 7)
        FakeSMTP.refuse["nobody@example.org"] = (550, b"5.1.1 The email account does not exist")
        self.run_cleaner()
        user = self.db.userCollection.find_one({"userID": 43})
        self.assertTrue(user.get("mailDisabled"), "a refused address stays switched on")
        self.assertIn("550", user.get("mailDisabledReason", ""))
        # And it is not tried again, today or on a later day.
        self.db.userCollection.update_one({"userID": 43}, {"$unset": {"expiryMailDay": ""}})
        self.addJob("Rf00000002", 43, 8)
        self.run_cleaner()
        self.assertEqual(["nobody@example.org"], FakeSMTP.attempts)

    def test_a_temporary_refusal_is_retried_and_does_not_turn_mail_off(self):
        self.addUser(44, "full@example.org")
        self.addJob("Tr00000001", 44, 7)
        FakeSMTP.refuse["full@example.org"] = (452, b"4.2.2 Mailbox full")
        self.run_cleaner()
        self.assertFalse(self.db.userCollection.find_one({"userID": 44}).get("mailDisabled"))
        del FakeSMTP.refuse["full@example.org"]
        self.run_cleaner()
        self.assertEqual(1, len(self.sentTo("full@example.org")),
                         "a warning that failed once was never sent")

    def test_a_failed_send_is_tried_again_on_the_next_run(self):
        self.addUser(45, "flaky@example.org")
        self.addJob("Fl00000001", 45, 7)
        FakeSMTP.fail = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        self.run_cleaner()
        self.assertEqual([], FakeSMTP.sent)
        FakeSMTP.fail = None
        self.run_cleaner()
        messages = self.sentTo("flaky@example.org")
        self.assertEqual(1, len(messages), "the job was marked as warned although no mail left")
        self.assertIn("Fl00000001", _body(messages[0]))


class SendToUserTest(_ScratchServer):

    def test_sendToUser_skips_a_disabled_account_without_contacting_the_server(self):
        from src.common import UserMail
        outcome = UserMail.sendToUser({"userID": 1, "email": "x@example.org", "userName": "X",
                                       "mailDisabled": True}, "s", "<p>m</p>")
        self.assertEqual(UserMail.SKIPPED, outcome)
        self.assertEqual([], FakeSMTP.attempts)

    def test_sendToUser_sends_to_an_ordinary_account(self):
        from src.common import UserMail
        outcome = UserMail.sendToUser({"userID": 1, "email": "X@Example.org", "userName": "X"},
                                      "s", "<p>m</p>")
        self.assertEqual(UserMail.SENT, outcome)
        self.assertEqual(["x@example.org"], FakeSMTP.attempts)


class AdminToolTest(_ScratchServer):

    def test_an_admin_can_turn_mail_off_and_on(self):
        from src.AdminTools.scripts import user_mail
        from src.common import UserMail
        self.addUser(51, "bounce@example.org")
        users = self.db.userCollection
        self.assertEqual(0, user_mail.run(
            ["disable", "Bounce@Example.org", "--reason", "bounced: no such user"], users))
        user = users.find_one({"userID": 51})
        self.assertTrue(UserMail.isMailDisabled(user))
        self.assertEqual("bounced: no such user", user["mailDisabledReason"])
        self.assertTrue(user.get("mailDisabledAt"))
        self.assertEqual(0, user_mail.run(["enable", "bounce@example.org"], users))
        self.assertFalse(UserMail.isMailDisabled(users.find_one({"userID": 51})))

    def test_an_unknown_address_is_an_error_not_a_silent_success(self):
        from src.AdminTools.scripts import user_mail
        self.assertEqual(1, user_mail.run(["disable", "typo@example.org"], self.db.userCollection))


class AnonymousJobFilesTest(_ScratchServer):
    """CLIENT_TMP/nologin after a run: what goes and what stays."""

    LIVE = "Lv0000000A"      # opened yesterday
    EXPIRED = "Ex0000000B"   # last opened ten days ago

    def path(self, *parts):
        return os.path.join(self.tmp, "nologin", *parts)

    def touchFile(self, days, *parts):
        target = self.path(*parts)
        with open(target, "w") as handle:
            handle.write("x\n")
        _age(target, days)
        return target

    def makeDir(self, days, *parts):
        target = self.path(*parts)
        os.makedirs(target)
        with open(os.path.join(target, "result.json"), "w") as handle:
            handle.write("{}")
        _age(os.path.join(target, "result.json"), days)
        _age(target, days)
        return target

    def seed(self):
        self.addJob(self.LIVE, None, 1, geneBasedInputOmics=[
            {"omicName": "Gene expression", "inputDataFile": self.LIVE + "_values.tab",
             "relevantFeaturesFile": "B2G_relevant_1.tab", "designFile": None}])
        self.addJob(self.EXPIRED, None, 10)
        self.files = {
            # the live job's own upload, written long ago: kept, the job lives
            "live_upload": self.touchFile(30, "inputData", self.LIVE + "_values.tab"),
            # a converter output the live job reads: kept
            "named_by_live": self.touchFile(30, "inputData", "B2G_relevant_1.tab"),
            # the job retention removes in this run: its upload goes with it
            "expired_upload": self.touchFile(10, "inputData", self.EXPIRED + "_values.tab"),
            # the reported case: an upload whose job is long gone
            "orphan_upload": self.touchFile(77, "inputData", "E7r3oPI1HS_counts.tab"),
            # a converter output no job reads any more
            "orphan_converter": self.touchFile(40, "inputData", "B2G_output_7.tab"),
            # a job being submitted right now has no record yet: kept
            "fresh_upload": self.touchFile(0, "inputData", "Nw0000000C_values.tab"),
            # day 7 is still inside the promise
            "boundary_upload": self.touchFile(7, "inputData", "Bd0000000D_values.tab"),
        }
        self.dirs = {
            "live_results": self.makeDir(30, "jobsData", self.LIVE),
            "expired_results": self.makeDir(10, "jobsData", self.EXPIRED),
            "orphan_results": self.makeDir(60, "jobsData", "Or0000000E"),
            "orphan_scratch": self.makeDir(60, "tmp", "Or0000000E"),
            "expired_scratch": self.makeDir(10, "tmp", self.EXPIRED),
            "fresh_results": self.makeDir(0, "jobsData", "Nw0000000C"),
        }
        # Never anonymous files, never touched by the sweep.
        self.addUser(61, "owner@example.org")
        self.registered = os.path.join(self.tmp, "61", "inputData", "mine.tab")
        with open(self.registered, "w") as handle:
            handle.write("x\n")
        _age(self.registered, 400)
        self.cache = os.path.join(self.tmp, "gtfcache", "hsa.gtf.cache")
        with open(self.cache, "w") as handle:
            handle.write("x\n")
        _age(self.cache, 400)

    def test_files_of_jobs_that_are_gone_are_removed(self):
        self.seed()
        self.run_cleaner()
        for name in ("expired_upload", "orphan_upload", "orphan_converter"):
            self.assertFalse(os.path.exists(self.files[name]),
                             "%s is still there: %s" % (name, self.files[name]))
        for name in ("expired_results", "orphan_results", "orphan_scratch", "expired_scratch"):
            self.assertFalse(os.path.exists(self.dirs[name]),
                             "%s is still there: %s" % (name, self.dirs[name]))

    def test_files_a_live_job_owns_or_reads_are_kept(self):
        self.seed()
        self.run_cleaner()
        for name in ("live_upload", "named_by_live", "fresh_upload", "boundary_upload"):
            self.assertTrue(os.path.exists(self.files[name]), "%s was deleted" % name)
        for name in ("live_results", "fresh_results"):
            self.assertTrue(os.path.exists(self.dirs[name]), "%s was deleted" % name)
        self.assertIsNotNone(self.db.jobInstanceCollection.find_one({"jobID": self.LIVE}))
        self.assertIsNone(self.db.jobInstanceCollection.find_one({"jobID": self.EXPIRED}))

    def test_nothing_outside_nologin_is_touched(self):
        self.seed()
        self.run_cleaner()
        self.assertTrue(os.path.exists(self.registered), "a registered user's file was deleted")
        self.assertTrue(os.path.exists(self.cache), "the shared GTF cache was deleted")

    def test_a_dry_run_lists_and_deletes_nothing(self):
        self.seed()
        plan = self.cleaner.planAnonymousFileSweep(self.client, removing={self.EXPIRED})
        planned = set(path for path, _size, _reason in plan)
        self.assertIn(self.files["orphan_upload"], planned)
        self.assertIn(self.files["expired_upload"], planned)
        self.assertNotIn(self.files["live_upload"], planned)
        self.run_cleaner(dry_run=True)
        for path in list(self.files.values()) + list(self.dirs.values()):
            self.assertTrue(os.path.exists(path), "the dry run deleted %s" % path)
        self.assertIsNotNone(self.db.jobInstanceCollection.find_one({"jobID": self.EXPIRED}),
                             "the dry run removed a job")

    def test_removing_a_registered_users_job_removes_its_scratch_directory(self):
        self.addUser(62, "scratch@example.org")
        scratch = os.path.join(self.tmp, "62", "tmp", "Sc0000000F")
        os.makedirs(scratch)
        os.makedirs(os.path.join(self.tmp, "62", "jobsData", "Sc0000000F"))
        self.addJob("Sc0000000F", 62, 400)
        self.run_cleaner()
        self.assertFalse(os.path.exists(scratch), "the job's tmp/ directory outlived it")


def _codeOnly(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    tokens = [t for t in tokenize.generate_tokens(io.StringIO(text).readline)
              if t.type not in (tokenize.COMMENT, tokenize.STRING)]
    return tokenize.untokenize(tokens)


class EveryUserMailPathRespectsTheSwitchTest(unittest.TestCase):
    """A message to a user that bypasses sendToUser ignores the switch.

    The only other caller allowed is the maintainers' report notification,
    which goes to EMAIL_REPORT_RECIPIENTS, not to a user account.
    """

    ALLOWED = {
        os.path.join("common", "Util.py"),           # the definition
        os.path.join("common", "UserMail.py"),       # the one gate
        os.path.join("servlets", "AdminServlet.py"),  # reports to the maintainers
    }

    def test_only_the_gate_calls_sendEmail(self):
        offenders = []
        for directory, subdirs, files in os.walk(SRC):
            subdirs[:] = [d for d in subdirs if d not in ("tests", "src", "conf", "__pycache__")]
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(directory, filename)
                relative = os.path.relpath(path, SRC)
                if relative in self.ALLOWED:
                    continue
                if re.search(r"\bsendEmail\s*\(", _codeOnly(path)):
                    offenders.append(relative)
        self.assertEqual([], sorted(offenders),
                         "these mail a user without src.common.UserMail.sendToUser, so "
                         "they ignore an account whose mail is turned off: %s" % offenders)


if __name__ == "__main__":
    unittest.main(verbosity=2)

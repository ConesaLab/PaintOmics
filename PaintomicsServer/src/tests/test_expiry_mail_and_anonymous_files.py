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
   that promises anonymous work is removed after seven days. A guest account's
   uploads stayed until the account itself went, 90 days after its last
   sign-in, under the same seven-day promise.

Plus three faults review found in the first version of the fix: a message the
server had taken was mailed again when QUIT failed, a full mailbox (552)
turned mail off for good, and the sweep deleted MORE files a live job reads by
their run stamp. And two more things the production dry run showed: the sweep
would delete 17 GB for good on its first run, so it stays off until a site sets
PAINTOMICS_SWEEP_ORPHAN_FILES=1; and every run wiped the jobs and My data of a
user id held by more than one user document.

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
import contextlib
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
import types
import unittest
import unittest.mock

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
    quitFail = None  # an exception to raise from quit, AFTER the message is taken

    def __init__(self, *args, **kwargs):
        pass

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def quit(self):
        if FakeSMTP.quitFail is not None:
            raise FakeSMTP.quitFail

    def close(self):
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
        cls.quitFail = None


def _body(message):
    for part in message.walk():
        if part.get_content_type() in ("text/html", "text/plain"):
            return part.get_payload(decode=True).decode("utf-8", "replace")
    return ""


def _subject(message):
    return str(email.header.make_header(email.header.decode_header(message["Subject"])))


class _ScratchServer(unittest.TestCase):
    """A scratch database, a scratch CLIENT_TMP and the fake mail server."""

    SWEEP_ON = False     # cleaner.SWEEP_ORPHAN_FILES during the test

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
            "SWEEP_ORPHAN_FILES": getattr(self.cleaner, "SWEEP_ORPHAN_FILES", False),
        }
        self.cleaner.SWEEP_ORPHAN_FILES = self.SWEEP_ON
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

    def test_a_refused_address_gets_its_warning_once_mail_is_back_on(self):
        """Nothing left, so nothing may be recorded as warned."""
        from src.common import UserMail
        self.addUser(49, "back@example.org")
        self.addJob("Bk00000001", 49, 7)
        FakeSMTP.refuse["back@example.org"] = (550, b"5.1.1 no such user")
        self.run_cleaner()
        self.assertIsNone(
            self.db.jobInstanceCollection.find_one({"jobID": "Bk00000001"}).get("reminderSent"),
            "a job is recorded as warned although no mail left")
        del FakeSMTP.refuse["back@example.org"]
        self.assertEqual(1, UserMail.enableMail(self.db.userCollection, "back@example.org"))
        self.run_cleaner()
        messages = self.sentTo("back@example.org")
        self.assertEqual(1, len(messages), "no warning after mail was turned back on")
        self.assertIn("Bk00000001", _body(messages[0]))

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

    def test_sendEmail_counts_a_message_the_server_took_as_sent_when_quit_fails(self):
        from src.common.Util import sendEmail
        FakeSMTP.quitFail = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        sendEmail("", "taken@example.org", "T", "s", "m")      # must not raise
        self.assertEqual(1, len(self.sentTo("taken@example.org")))

    def test_a_warning_the_server_took_is_not_sent_again_when_quit_fails(self):
        """The retry above must not become a daily repeat.

        The fake server takes the message and then drops the connection on
        QUIT, on every day of the warning window.
        """
        self.addUser(46, "quit@example.org")
        self.addJob("Qu00000001", 46, 7)
        FakeSMTP.quitFail = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        today = datetime.date.today()
        for day in range(7):
            expiring = self.cleaner.checkRemindJobsForUser(self.client, 46, details=True)
            self.cleaner.remindUserOfJobs(self.client, 46, expiring, "",
                                          today=today + datetime.timedelta(days=day))
        self.assertEqual(1, len(self.sentTo("quit@example.org")),
                         "one job and one deadline were mailed more than once")

    def test_a_full_mailbox_does_not_turn_mail_off(self):
        """RFC 5321 4.5.3.1.10: treat 552 at RCPT TO as temporary."""
        self.addUser(47, "quota@example.org")
        self.addJob("Qt00000001", 47, 7)
        FakeSMTP.refuse["quota@example.org"] = (
            552, b"5.2.2 The email account that you tried to reach is over quota")
        self.run_cleaner()
        self.assertFalse(self.db.userCollection.find_one({"userID": 47}).get("mailDisabled"),
                         "a full mailbox switched mail off for good")
        del FakeSMTP.refuse["quota@example.org"]
        self.run_cleaner()
        self.assertEqual(1, len(self.sentTo("quota@example.org")),
                         "the warning was not tried again once the mailbox had room")

    def test_a_refusal_by_policy_does_not_turn_mail_off(self):
        """5.7.x is about us or the route; read as a dead address, one
        misconfigured relay would switch off every account it was asked to reach."""
        self.addUser(48, "policy@example.org")
        self.addJob("Po00000001", 48, 7)
        FakeSMTP.refuse["policy@example.org"] = (550, b"5.7.1 Relaying denied")
        self.run_cleaner()
        self.assertFalse(self.db.userCollection.find_one({"userID": 48}).get("mailDisabled"))


class DeadAddressRefusalTest(unittest.TestCase):
    """Which refusals at RCPT TO turn mail off for good."""

    CASES = [
        ((550, b"5.1.1 The email account that you tried to reach does not exist"), True),
        ((550, b"No such user here"), True),
        ((551, b"User not local"), True),
        ((553, b"5.1.3 Invalid address"), True),
        ((550, b"5.1.6 Recipient no longer on server"), True),
        ((556, b"5.1.10 Recipient address has null MX"), True),
        ((552, b"5.2.2 The email account that you tried to reach is over quota"), False),
        ((552, b"Requested mail action aborted: exceeded storage allocation"), False),
        ((550, b"5.2.1 The email account that you tried to reach is disabled"), False),
        ((550, b"5.7.1 Relaying denied"), False),
        ((554, b"Transaction failed"), False),
        ((550, b"Relaying denied"), False),
        ((550, b"Administrative prohibition"), False),
        ((550, b"Requested action not taken: mailbox unavailable"), False),
        ((553, b"User unknown"), True),
        ((550, b"5.1.8 Bad sender's system address"), False),
        ((450, b"4.2.1 Try again later"), False),
        ((452, b"4.2.2 Mailbox full"), False),
    ]

    def test_only_a_dead_address_counts(self):
        from src.common.Util import _deadAddressRefusal
        for answer, dead in self.CASES:
            self.assertEqual(dead, _deadAddressRefusal({"a@example.org": answer}) is not None,
                             "%s %s" % answer)

    def test_one_recipient_refused_for_another_reason_is_not_a_dead_address(self):
        from src.common.Util import _deadAddressRefusal
        self.assertIsNone(_deadAddressRefusal({
            "a@example.org": (550, b"5.1.1 no such user"),
            "b@example.org": (552, b"5.2.2 over quota")}))


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


class PasswordResetReplyTest(_ScratchServer):
    """The browser answers any success with "Email sent. Check your inbox"."""

    def requestReset(self, **fields):
        from src.classes.User import User
        from src.servlets import UserManagementServlet as servlet
        self.addUser(71, "reset@example.org", **fields)
        users = self.db.userCollection
        user = User(71)
        user.parseBSON(users.find_one({"userID": 71}))

        dao = types.SimpleNamespace(
            findByEmail=lambda address: user, update=lambda instance, params: True,
            closeConnection=lambda: None, collectionName="userCollection",
            dbManager=types.SimpleNamespace(getCollection=lambda name: users))
        replies = []
        response = types.SimpleNamespace(setStatus=lambda status: None, setContent=replies.append)
        request = types.SimpleNamespace(values={"userEmail": "reset@example.org"})
        with unittest.mock.patch.object(servlet, "UserDAO", lambda: dao), \
                unittest.mock.patch.object(servlet, "url_for", lambda *args, **kwargs: "/reset"):
            servlet.userManagementResetPassword(request, response, "")
        return replies[-1]

    def test_an_ordinary_account_is_mailed_and_told_so(self):
        self.assertTrue(self.requestReset()["success"])
        self.assertEqual(["reset@example.org"], FakeSMTP.attempts)

    def test_an_account_with_mail_off_is_told_no_mail_was_sent(self):
        reply = self.requestReset(mailDisabled=True)
        self.assertEqual([], FakeSMTP.attempts)
        self.assertFalse(reply["success"], "the browser now says \"Email sent\" for no mail at all")
        # The client shows what follows this marker, so the marker has to be there.
        self.assertIn("No mail was sent", reply["message"].split("ERROR MESSAGE:")[1])

    def test_an_address_refused_during_the_reset_is_told_too(self):
        FakeSMTP.refuse["reset@example.org"] = (550, b"5.1.1 no such user")
        reply = self.requestReset()
        self.assertFalse(reply["success"])
        self.assertTrue(self.db.userCollection.find_one({"userID": 71}).get("mailDisabled"))


class AnonymousJobFilesTest(_ScratchServer):
    """CLIENT_TMP/nologin after a run: what goes and what stays."""

    SWEEP_ON = True

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
        plan = self.cleaner.planExpiredFileSweep(self.client, removing={self.EXPIRED})
        planned = set(entry.path for entry in plan)
        self.assertIn(self.files["orphan_upload"], planned)
        self.assertIn(self.files["expired_upload"], planned)
        self.assertNotIn(self.files["live_upload"], planned)
        self.run_cleaner(dry_run=True)
        for path in list(self.files.values()) + list(self.dirs.values()):
            self.assertTrue(os.path.exists(path), "the dry run deleted %s" % path)
        self.assertIsNotNone(self.db.jobInstanceCollection.find_one({"jobID": self.EXPIRED}),
                             "the dry run removed a job")

    def test_files_a_live_job_reads_by_their_run_stamp_are_kept(self):
        """MORE_rpc_<stamp>.tab and MORE_filters_<stamp>.json are never recorded
        by name: the pathway job derives them from the stamp of the MORE files
        it does record (PathwayAcquisitionJob.parseRegulationPerCondition)."""
        stamp = "202610250930"
        self.addJob("MoLive001A", None, 1, geneBasedInputOmics=[
            {"omicName": "TF", "inputDataFile": "MORE_output_TF_%s.tab" % stamp,
             "relevantFeaturesFile": "MORE_relevant_reg_TF_%s.tab" % stamp}])
        kept = [self.touchFile(13, "inputData", name % stamp) for name in (
            "MORE_output_TF_%s.tab", "MORE_relevant_reg_TF_%s.tab",
            "MORE_rpc_%s.tab", "MORE_filters_%s.json", "MORE_design_%s.tab")]
        # A converter job that is still kept keeps what it wrote, which is
        # named after the job's date and recorded nowhere else.
        created = (datetime.date.today() - datetime.timedelta(days=13)).strftime("%Y%m%d") + "0815"
        self.addJob("B2gLive01B", None, 2, date=created)
        kept.append(self.touchFile(13, "inputData", "B2G_output_%s_417.tab" % created))
        # Another run's companions, which nothing reads any more, still go.
        gone = self.touchFile(13, "inputData", "MORE_rpc_202609010800.tab")
        self.run_cleaner()
        for path in kept:
            self.assertTrue(os.path.exists(path), "deleted although a live job reads it: %s" % path)
        self.assertFalse(os.path.exists(gone), "a stamp no job carries kept its file")


class GuestAccountFilesTest(_ScratchServer):
    """A guest account's folder: "kept for a maximum of 7 days"."""

    SWEEP_ON = True

    GUEST = 84

    def gpath(self, *parts):
        return os.path.join(self.tmp, str(self.GUEST), *parts)

    def put(self, days, *parts):
        target = self.gpath(*parts)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as handle:
            handle.write("x\n")
        _age(target, days)
        return target

    def putDir(self, days, *parts):
        target = self.gpath(*parts)
        os.makedirs(target)
        _age(self.put(days, *(parts + ("result.json",))), days)
        _age(target, days)
        return target

    def seed(self):
        self.addUser(self.GUEST, "guest84@paintomics.example", is_guest=True)
        # Removed in this run: last opened ten days ago.
        self.addJob("GuExp0001A", self.GUEST, 10,
                    geneBasedInputOmics=[{"omicName": "Gene expression", "inputDataFile": "expr.tab"}])
        # Kept: opened yesterday. It reads files uploaded a month ago, one of
        # them only through a MORE-style record (regulatoryOmics[*].file).
        self.addJob("GuLive001B", self.GUEST, 1,
                    geneBasedInputOmics=[{"omicName": "Proteomics", "inputDataFile": "proteins.tab",
                                          "designFile": "design.tab"}],
                    regulatoryOmics=[{"name": "miRNA", "file": "mirna.tab"}])
        names = ("expr.tab", "proteins.tab", "design.tab", "mirna.tab", "unused_old.tab", "unused_new.tab")
        self.db.fileCollection.insert_many(
            [{"userID": str(self.GUEST), "fileName": name} for name in names])
        self.paths = {
            "expired_upload": self.put(10, "inputData", "expr.tab"),
            "live_upload": self.put(30, "inputData", "proteins.tab"),
            "live_design": self.put(30, "inputData", "design.tab"),
            "live_regulator": self.put(30, "inputData", "mirna.tab"),
            "unused_old": self.put(9, "inputData", "unused_old.tab"),
            "unused_new": self.put(3, "inputData", "unused_new.tab"),
            "orphan_results": self.putDir(20, "jobsData", "GuGone001C"),
            "orphan_scratch": self.putDir(20, "tmp", "GuGone001C"),
            "live_results": self.putDir(30, "jobsData", "GuLive001B"),
        }

    def myData(self):
        return set(entry["fileName"] for entry in
                   self.db.fileCollection.find({"userID": str(self.GUEST)}))

    def test_an_expired_guest_jobs_upload_goes_with_it(self):
        """The reviewer's case: the job went at day 7, its upload stayed."""
        self.seed()
        self.run_cleaner()
        self.assertIsNone(self.db.jobInstanceCollection.find_one({"jobID": "GuExp0001A"}))
        self.assertFalse(os.path.exists(self.paths["expired_upload"]),
                         "a guest account's upload outlived its expired job")
        self.assertNotIn("expr.tab", self.myData(), "My data still lists a file that is gone")

    def test_old_guest_files_no_job_uses_are_removed(self):
        self.seed()
        self.run_cleaner()
        for name in ("unused_old", "orphan_results", "orphan_scratch"):
            self.assertFalse(os.path.exists(self.paths[name]), "%s is still there" % name)
        self.assertNotIn("unused_old.tab", self.myData())

    def test_what_a_live_guest_job_uses_and_recent_uploads_stay(self):
        self.seed()
        self.run_cleaner()
        for name in ("live_upload", "live_design", "live_regulator", "unused_new", "live_results"):
            self.assertTrue(os.path.exists(self.paths[name]), "%s was deleted" % name)
        self.assertEqual({"proteins.tab", "design.tab", "mirna.tab", "unused_new.tab"}, self.myData())

    def test_a_registered_accounts_old_files_stay(self):
        """A registered account's inputData is "My data", kept until deleted."""
        self.addUser(85, "kept@example.org")
        mine = os.path.join(self.tmp, "85", "inputData", "mine.tab")
        with open(mine, "w") as handle:
            handle.write("x\n")
        _age(mine, 400)
        folder = os.path.join(self.tmp, "85", "jobsData", "NoRecord1D")
        os.makedirs(folder)
        _age(folder, 400)
        self.run_cleaner()
        self.assertTrue(os.path.exists(mine))
        self.assertTrue(os.path.exists(folder))


class OrphanFileSweepSettingTest(_ScratchServer):
    """The sweep deletes for good, so it waits for a yes (off by default)."""

    GUEST = 86

    def age(self, days, *parts):
        target = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as handle:
            handle.write("x\n")
        _age(target, days)
        return target

    def seed(self):
        self.addJob("OfLive001A", None, 1)
        self.addJob("OfExp0001B", None, 10)
        self.addUser(self.GUEST, "guest86@paintomics.example", is_guest=True)
        self.db.fileCollection.insert_one({"userID": str(self.GUEST), "fileName": "g.tab"})
        self.expiredResults = self.age(10, "nologin", "jobsData", "OfExp0001B", "result.json")
        _age(os.path.dirname(self.expiredResults), 10)
        self.sweepable = [
            self.age(10, "nologin", "inputData", "OfExp0001B_values.tab"),
            self.age(77, "nologin", "inputData", "E7r3oPI1HS_counts.tab"),
            self.age(9, str(self.GUEST), "inputData", "g.tab"),
        ]

    def run_capturing_log(self, **kwargs):
        with self.assertLogs(level="INFO") as captured:
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.run_cleaner(**kwargs)
        return "\n".join(captured.output), printed.getvalue()

    def test_off_a_run_deletes_nothing_through_the_sweep(self):
        self.seed()
        self.cleaner.SWEEP_ORPHAN_FILES = False
        self.run_capturing_log()
        for path in self.sweepable:
            self.assertTrue(os.path.exists(path), "the sweep is off and deleted %s" % path)
        self.assertEqual(1, self.db.fileCollection.count_documents(
            {"userID": str(self.GUEST), "fileName": "g.tab"}), "the sweep is off and changed My data")

    def test_off_retention_of_jobs_works_as_before(self):
        self.seed()
        self.cleaner.SWEEP_ORPHAN_FILES = False
        self.run_capturing_log()
        self.assertIsNone(self.db.jobInstanceCollection.find_one({"jobID": "OfExp0001B"}))
        self.assertIsNotNone(self.db.jobInstanceCollection.find_one({"jobID": "OfLive001A"}))
        self.assertFalse(os.path.exists(os.path.dirname(self.expiredResults)),
                         "an expired job's result folder went with the job on master, and has to still")

    def test_off_a_run_logs_one_line_with_what_it_would_delete(self):
        self.seed()
        self.cleaner.SWEEP_ORPHAN_FILES = False
        logged, _printed = self.run_capturing_log()
        lines = [line for line in logged.splitlines() if "would delete" in line]
        self.assertEqual(1, len(lines), logged)
        self.assertIn("would delete 3 entries, 0.00 GB; the orphan file sweep is off", lines[0])
        self.assertNotIn(self.sweepable[0], logged, "a real run with the sweep off lists paths")

    def test_on_a_run_deletes_them(self):
        self.seed()
        self.cleaner.SWEEP_ORPHAN_FILES = True
        logged, _printed = self.run_capturing_log()
        for path in self.sweepable:
            self.assertFalse(os.path.exists(path), "the sweep is on and kept %s" % path)
            self.assertIn(path, logged, "the log is the record of what went")
        self.assertEqual(0, self.db.fileCollection.count_documents({"userID": str(self.GUEST)}))
        self.assertNotIn("would delete", logged)

    def test_a_dry_run_lists_everything_in_both_states_and_changes_nothing(self):
        self.seed()
        for state in (False, True):
            self.cleaner.SWEEP_ORPHAN_FILES = state
            _logged, printed = self.run_capturing_log(dry_run=True)
            for path in self.sweepable:
                self.assertIn(path, printed, "the dry run with the sweep %s left out %s" % (state, path))
                self.assertTrue(os.path.exists(path), "the dry run deleted %s" % path)
            self.assertIsNotNone(self.db.jobInstanceCollection.find_one({"jobID": "OfExp0001B"}))
            self.assertEqual(1, self.db.fileCollection.count_documents({"userID": str(self.GUEST)}))


class OrphanFileSweepConfigurationTest(unittest.TestCase):
    """How the setting reaches the code, on a deployment that never mentions it."""

    def valueWith(self, environment):
        template = os.path.join(SRC, "resources", "example_serverconf.py")
        spec = importlib.util.spec_from_file_location("sweep_setting_probe", template)
        module = importlib.util.module_from_spec(spec)
        with unittest.mock.patch.dict(os.environ, environment):
            os.environ.pop("PAINTOMICS_SWEEP_ORPHAN_FILES", None)
            os.environ.update(environment)
            spec.loader.exec_module(module)
        return module.SWEEP_ORPHAN_FILES

    def test_the_template_is_off_unless_it_is_set(self):
        self.assertIs(False, self.valueWith({}))
        self.assertIs(False, self.valueWith({"PAINTOMICS_SWEEP_ORPHAN_FILES": ""}))
        self.assertIs(False, self.valueWith({"PAINTOMICS_SWEEP_ORPHAN_FILES": "0"}))
        self.assertIs(True, self.valueWith({"PAINTOMICS_SWEEP_ORPHAN_FILES": "1"}))

    def test_compose_passes_it_and_defaults_to_off(self):
        with open(os.path.join(SRC, "..", "..", "deploy", "compose.yaml"), encoding="utf-8") as handle:
            self.assertIn("PAINTOMICS_SWEEP_ORPHAN_FILES: ${PAINTOMICS_SWEEP_ORPHAN_FILES:-0}", handle.read())


class SharedUserIDTest(_ScratchServer):
    """An id held by more than one user document is never cleaned.

    Each run read the extra documents as accounts that had lost their directory
    and "fixed" them by deleting every job and My data entry of the id.
    The sweep is on here, so everything that could delete is switched on.
    """

    SWEEP_ON = True
    SHARED = 128

    def seed(self, **extra):
        self.addUser(self.SHARED, "shared@example.org", **extra)
        self.addUser(self.SHARED, "shared@example.org", **extra)
        self.addJob("ShFresh01A", self.SHARED, 1)
        self.addJob("ShOld0001B", self.SHARED, 400)
        self.db.fileCollection.insert_one({"userID": str(self.SHARED), "fileName": "mine.tab"})
        base = os.path.join(self.tmp, str(self.SHARED))
        os.makedirs(os.path.join(base, "jobsData", "ShFresh01A"))
        os.makedirs(os.path.join(base, "jobsData", "ShOld0001B"))
        self.upload = os.path.join(base, "inputData", "mine.tab")
        with open(self.upload, "w") as handle:
            handle.write("x\n")
        _age(self.upload, 400)

    def assertUntouched(self):
        jobs = self.db.jobInstanceCollection
        self.assertEqual(2, self.db.userCollection.count_documents({"userID": self.SHARED}))
        self.assertIsNotNone(jobs.find_one({"jobID": "ShFresh01A"}), "a job of a shared id was deleted")
        self.assertIsNotNone(jobs.find_one({"jobID": "ShOld0001B"}), "an old job of a shared id was deleted")
        self.assertEqual(1, self.db.fileCollection.count_documents({"userID": str(self.SHARED)}),
                         "a My data entry of a shared id was deleted")
        self.assertTrue(os.path.exists(self.upload), "an upload of a shared id was deleted")
        for jobID in ("ShFresh01A", "ShOld0001B"):
            self.assertTrue(os.path.isdir(os.path.join(self.tmp, str(self.SHARED), "jobsData", jobID)),
                            "the result folder of %s was deleted" % jobID)

    def test_the_daily_run_leaves_its_jobs_and_files_alone(self):
        self.seed()
        self.run_cleaner()
        self.assertUntouched()

    def test_it_logs_a_warning_naming_the_id(self):
        self.seed()
        with self.assertLogs(level="WARNING") as captured:
            self.run_cleaner()
        self.assertTrue([line for line in captured.output if "User id 128 is held by 2 user documents" in line],
                        captured.output)

    def test_a_guest_id_held_twice_is_left_alone_too(self):
        """Past MAX_GUEST_DAYS, so a run would otherwise remove the whole account."""
        self.seed(is_guest=True, last_login="20200101")
        self.run_cleaner()
        self.assertUntouched()

    def test_it_is_not_warned_about_jobs_that_will_not_go(self):
        self.seed()
        self.addJob("ShWarned1C", self.SHARED, 7)
        self.run_cleaner()
        self.assertEqual([], FakeSMTP.attempts)

    def test_a_shared_id_with_no_directory_gets_no_directory_and_keeps_its_jobs(self):
        self.addUser(self.SHARED, "shared@example.org")
        self.addUser(self.SHARED, "shared@example.org")
        shutil.rmtree(os.path.join(self.tmp, str(self.SHARED)))
        self.addJob("ShNoDir01D", self.SHARED, 1)
        self.run_cleaner()
        self.assertIsNotNone(self.db.jobInstanceCollection.find_one({"jobID": "ShNoDir01D"}))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, str(self.SHARED))))

    def test_other_accounts_are_still_cleaned(self):
        self.seed()
        self.addUser(5, "other@example.org")
        self.addJob("OtOld0001E", 5, 400)
        os.makedirs(os.path.join(self.tmp, "5", "jobsData", "OtOld0001E"))
        # One document and no directory: still fixed as before.
        self.addUser(6, "nodir@example.org")
        shutil.rmtree(os.path.join(self.tmp, "6"))
        self.addJob("NoDirJob1F", 6, 1)
        self.run_cleaner()
        self.assertUntouched()
        self.assertIsNone(self.db.jobInstanceCollection.find_one({"jobID": "OtOld0001E"}))
        self.assertIsNone(self.db.jobInstanceCollection.find_one({"jobID": "NoDirJob1F"}))
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "6", "jobsData")))


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

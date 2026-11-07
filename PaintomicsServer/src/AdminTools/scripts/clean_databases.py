import os, inspect, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)) + "/../")
import logging
import logging.config

import datetime
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from conf.serverconf import (
    MONGODB_HOST,
    MONGODB_PORT,
    MONGODB_DATABASE,
    CLIENT_TMP_DIR,
    ADMIN_ACCOUNTS,
    MAX_GUEST_DAYS,
    MAX_JOB_DAYS,
    PAINTOMICS_BASE_URL,
)

from src.common import UserMail
from src.common.EmailTemplates import PRODUCT_NAME, jobExpiryEmail

# serverconf.py is per-site and gitignored, so a deploy carries new code to a
# configuration file that predates it. A hard `from conf.serverconf import
# MAX_GUEST_JOB_DAYS` therefore does not fail here, it fails in AdminServlet at
# import time and the server does not start at all -- verified by running the
# clean export against a serverconf with the setting removed:
#
#     ImportError: cannot import name 'MAX_GUEST_JOB_DAYS' from 'conf.serverconf'
#
# A missing retention setting is not worth a site outage, so it falls back to
# the number the interface promises. Sites that set it explicitly are
# unaffected, and example_serverconf ships it so new installs always have it.
import conf.serverconf as _serverconf
MAX_GUEST_JOB_DAYS = getattr(_serverconf, "MAX_GUEST_JOB_DAYS", 7)

# Directories directly under CLIENT_TMP that are NOT user directories.
#
# Everything else there is read as a "<userID>" directory, and STEP 7 rmtree's
# any of them that no user in the database claims. "nologin" was always
# excluded; "gtfcache" is the shared GTF cache Bed2GeneJob points at
# (Bed2GeneJob.getOptions -> "cache_dir": CLIENT_TMP + "gtfcache"), and no user
# will ever own it, so every cleanup run would have deleted the whole cache and
# made the next regions-to-genes job re-parse its annotation from scratch.
NON_USER_CLIENT_TMP_DIRS = ("nologin", "gtfcache")

# Where an anonymous job's files live, and the userID its record carries.
#
# A job run without signing in is stored with userID None; JobInformationManager
# writes its files under CLIENT_TMP/nologin/jobsData/<jobID>. BSON round-trips
# have historically turned that None into the string "None" in places, so both
# spellings count as anonymous rather than only the one this database happens to
# hold today.
ANONYMOUS_DIR = "nologin"
ANONYMOUS_USER_IDS = (None, "None")

# The three directories under CLIENT_TMP/nologin an anonymous job writes into.
#
# removeJobByJobID only ever removed jobsData/<jobID>. An anonymous job's
# uploads go to inputData/ instead -- one directory shared by every anonymous
# job, each file named "<jobID>_<original name>" (JobInformationManager) or,
# for converter outputs, not named after any job at all ("B2G_output_...",
# "MORE_output_...") -- and nothing ever removed them. On paintomics.org that
# was 15,603 files and 11 GB on 2026-11-07, the oldest from November 2024,
# kept by a server that promises anonymous work is gone after seven days.
# sweepAnonymousFiles covers all three.
ANONYMOUS_SUBDIRS = ("inputData", "jobsData", "tmp")

# How long before a registered user's job expires they are warned about it.
REMINDER_WINDOW_DAYS = 7

# On the user's userCollection document: the day ("%Y%m%d") they were last sent
# an expiry warning. Claimed atomically before sending, so one account gets at
# most one warning a day even when two cleaning runs overlap -- the daily timer
# and the admin panel's "clean databases" button run the same code.
EXPIRY_MAIL_DAY_FIELD = "expiryMailDay"

# Every index the running server depends on, as (collection, create_index keys).
# Shared with paintomicsserver so startup and the nightly cron cannot drift:
# rebuildIndexes is on a 24h timer, so without the startup pass a fresh
# deployment answers every jobID query with a collection scan until it fires.
#
# aiInterpretationCollection was missing entirely. Its documents average 125KB
# and ai_interpret_status polls {"jobID": ...} every few seconds for the whole
# length of a multi-minute report, so it was the collection that most needed
# the index and the only one that never had it.
JOBID_INDEXES = [
    ('jobInstanceCollection', "jobID"),
    ('featuresCollection', "jobID"),
    ('featuresCollection', [("jobID", 1), ("featureType", 1)]),
    ('pathwaysCollection', "jobID"),
    ('foundFeaturesCollection', "jobID"),
    ('aiInterpretationCollection', "jobID"),
    ('aiWalkCollection', [("jobID", 1), ("scope", 1)]),
]

def cleanDatabases(force=False, dry_run=False):
    """The daily retention pass (startScheludeTasks) and the admin panel's button.

    dry_run: report what would be removed and mailed, change nothing.
    """
    log("Starting Clean Databases routine" + (" (dry run)" if dry_run else ""))

    connection = MongoClient(MONGODB_HOST, MONGODB_PORT)

    import os
    ROOT_DIRECTORY = os.path.abspath(os.path.dirname(os.path.realpath(__file__)) + "/../") + "/"

    # STEP 1. GET ALL USERS BY DIRECTORY
    user_dirs = os.listdir(CLIENT_TMP_DIR)

    for reservedDir in NON_USER_CLIENT_TMP_DIRS:
        if reservedDir in user_dirs:
            user_dirs.remove(reservedDir)

    # STEP 2. GET ALL USERS IN DB
    users_list = connection[MONGODB_DATABASE]['userCollection'].find()

    # STEP 3. CHECK CURRENT INFORMATION
    #Users that are out of date (guest users)
    users_to_remove = []
    #Users that for any reason have lost the data dir -> create new one and clean files and Jobs from DB
    users_to_fix = []
    #Jobs that are out of date (normal and guest users)
    jobs_to_remove = {}
    #Jobs that need to be reminded (normal users)
    jobs_to_remind = {}
    #In addition we have to clean all files from db and dir

    # The "nologin" user is not in the database and never will be, so this loop
    # cannot see anonymous jobs. STEP 3b below is where they are handled.
    for user in users_list:
        user_id = int(user["userID"])
        if str(user_id) in user_dirs:
            # User is in database, so dir is OK
            user_dirs.remove(str(user_id))
        else:
            #Data dir missing, need to be fixed
            log("User " + str(user_id) + " marked to be fixed.")
            users_to_fix.append(user_id)

        # A guest account's jobs are the "7 days for guests" the interface
        # promises; a registered account's are the 14.
        isGuest = bool(user.get("is_guest"))
        maxJobDays = MAX_GUEST_JOB_DAYS if isGuest else MAX_JOB_DAYS

        force_remove = False
        if isGuest and checkRemoveGuestUser(user, user_id):
            # If guest user, check if should be removed
            users_to_remove.append(user_id)
            force_remove = True
        elif not isGuest:
            # Warnings go to registered accounts only, as the old comment here
            # said and the old `else:` did not do. A guest's jobs last
            # MAX_GUEST_JOB_DAYS, not the MAX_JOB_DAYS the window below is
            # built on, and a guest's address is made up
            # ("guest12345@" + PAINTOMICS_EMAIL_DOMAIN), so a warning to one
            # can only bounce.
            reminders = checkRemindJobsForUser(connection, user_id, details=True)
            if reminders:
                notMailable = UserMail.whyNotMail(user)
                if notMailable is None:
                    jobs_to_remind[user_id] = reminders
                else:
                    log("User " + str(user_id) + " has " + str(len(reminders)) +
                        " job(s) to warn about but will not be mailed: " + notMailable + ".")


        # If check if user has jobs that should be removed
        aux = checkRemoveJobsForUser(connection, user_id, force_remove, maxJobDays)
        if len(aux) > 0:
            jobs_to_remove[user_id] = aux

    # STEP 3b. ANONYMOUS JOBS.
    #
    # These were never examined. The loop above walks userCollection and asks
    # for {"userID": str(user_id)}, and a job run without signing in stores
    # userID None -- so it matches no user and no rule, and the original author
    # left a "TODO: nologin user will not be present in the DB" against exactly
    # this. On paintomics.uv.es that was 159 of 218 jobs: the majority of the
    # server, retained forever, while the interface told those same users their
    # work would be removed after seven days.
    anonymous_to_remove = checkRemoveAnonymousJobs(connection)
    if anonymous_to_remove:
        jobs_to_remove[ANONYMOUS_DIR] = anonymous_to_remove

    # STEP 3c. FILES OF ANONYMOUS JOBS THAT NO LONGER EXIST.
    #
    # Planned here only for the summary, against the database as it will be
    # once STEP 4 has run; STEP 4b plans again, after STEP 4, and acts on that.
    removing = set(job_id for jobs in jobs_to_remove.values() for job_id in jobs)
    anonymous_files = planAnonymousFileSweep(connection, removing)

    log("Summary:")
    log("   - " + str(str(sum(len(x) for x in jobs_to_remove.values()))) + " jobs will be removed.")
    log("   - " + str(len(jobs_to_remind)) + " reminder e-mails will be sent, about " +
        str(sum(len(x) for x in jobs_to_remind.values())) + " jobs.")
    log("   - " + str(len(users_to_remove)) + " users will be removed.")
    log("   - " + str(len(users_to_fix)) + " users will be fixed.")
    log("   - " + str(len(user_dirs)) + " orphan directories will be removed.")
    log("   - " + str(len(anonymous_files)) + " files and directories of anonymous jobs will be removed (" +
        _megabytes(sum(size for _path, size, _reason in anonymous_files)) + ").")
    log("")

    if dry_run:
        for path, size, reason in anonymous_files:
            log("   would remove " + path + " (" + _megabytes(size) + "): " + reason)
        log("Dry run: nothing was changed.")
        return

    if not force:
        if not confirm(prompt='"Proceed?', resp=False):
            log("Bye.")
            return

    # STEP 4. REMOVE ALL OUTDATED JOBS (+ FEATURES AND FILES)
    for user_id, jobs_to_remove in jobs_to_remove.items():
        for job_id in jobs_to_remove:
            removeJobByJobID(connection, user_id, job_id)

    # STEP 4b. REMOVE THE FILES OF ANONYMOUS JOBS THAT NO LONGER EXIST
    # (the jobs STEP 4 has just removed, and every earlier one).
    sweepAnonymousFiles(connection, removing)

    # STEP 5. REMOVE ALL OUTDATED GUEST USERS (+ FILES)
    for user_id in users_to_remove:
        #IF USER IN TO_FIX REMOVE IT
        if user_id in users_to_fix:
            users_to_fix.remove(user_id)
        #Jobs have been already removed
        removeAllFilesByUserID(connection, user_id)
        removeUserByUserID(connection, user_id)

    # STEP 6. FIX ALL ERRONEOUS USERS (BUT CLEAN FIRST THE BED2GENES, JOBS AND FILES)
    for user_id in users_to_fix:
        removeAllFilesByUserID(connection, user_id, only_db=True)
        fixUserDataByUserID(connection, user_id)

    # STEP 7. REMOVE THE ORPHAN DIRECTORIES
    for user_id in user_dirs:
        removeDirectoryByUserID(user_id)

    # STEP 8. SEND REMINDER E-MAILS: one per user, listing all their jobs.
    for user_id, expiring in jobs_to_remind.items():
        remindUserOfJobs(connection, user_id, expiring, ROOT_DIRECTORY)

    # STEP 9. REBUILD INDEXES
    rebuildIndexes(connection)

def checkRemoveGuestUser(user, user_id):
    last_login = datetime.datetime.strptime(user['last_login'], "%Y%m%d").date()
    max_date = datetime.date.today() - datetime.timedelta(days=MAX_GUEST_DAYS)
    remove = (last_login < max_date)
    if remove:
        log("User " + str(user_id) + " marked to be removed.")
    return remove

def jobAccessDate(job):
    """The day a job was last opened, or None if it cannot be read.

    accessDate is a "%Y%m%d%H%M" string rewritten by /pa_touch_job every time
    the job is shown. A record that predates the field, or carries a malformed
    one, must not be read as "infinitely old" -- that would delete it on the
    next run. Returning None means "no opinion", and every caller keeps it.
    """
    raw = job.get("accessDate")
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(str(raw)[0:8], "%Y%m%d").date()
    except (ValueError, TypeError):
        log("Job " + str(job.get("jobID")) + " has an unreadable accessDate " +
            repr(raw) + "; leaving it alone.")
        return None


def checkRemoveJobsForUser(connection, user_id, force_remove=False,
                           max_job_days=MAX_JOB_DAYS):
    #Get all jobs for user
    jobs_list = connection[MONGODB_DATABASE]['jobInstanceCollection'].find({"userID":str(user_id)})
    max_date = datetime.date.today() - datetime.timedelta(days=max_job_days)
    jobs_remove = []
    # for each job
    for job in jobs_list:
        #Check if date OR if force_remove
        date = jobAccessDate(job)
        if date is None and not force_remove:
            continue
        if force_remove or date < max_date:
            log("Job " + str(job["jobID"]) + " (user " + str(user_id) + ") marked to be removed.")
            jobs_remove.append(job['jobID'])
    return jobs_remove


def checkRemoveAnonymousJobs(connection):
    """Jobs run without signing in, older than MAX_GUEST_JOB_DAYS.

    Selected on userID rather than by walking users, because that is the whole
    point: these belong to no user, so no per-user pass can reach them.
    """
    jobs_list = connection[MONGODB_DATABASE]['jobInstanceCollection'].find(
        {"userID": {"$in": list(ANONYMOUS_USER_IDS)}})
    max_date = datetime.date.today() - datetime.timedelta(days=MAX_GUEST_JOB_DAYS)
    jobs_remove = []
    for job in jobs_list:
        date = jobAccessDate(job)
        if date is not None and date < max_date:
            log("Job " + str(job["jobID"]) + " (anonymous) marked to be removed.")
            jobs_remove.append(job['jobID'])
    return jobs_remove

def jobDeletionDate(accessDay, max_job_days=None):
    """The first day the daily run deletes a job last opened on accessDay.

    checkRemoveJobsForUser removes a job when accessDay < today - max_job_days,
    so day max_job_days is still kept and the next day is the first without it.
    """
    days = MAX_JOB_DAYS if max_job_days is None else max_job_days
    return accessDay + datetime.timedelta(days=days + 1)


def alreadyWarned(job, deadline):
    """Was this job already warned about for this deadline ("%Y%m%d")?

    `reminderSent` holds the deadline the warning was about. /pa_touch_job
    unsets it whenever the job is opened, because opening the job moves the
    deadline, so a job is warned about once per deadline and never twice for
    the same one. Records written before this change hold the number 1, which
    meant "warned since it was last opened" -- the same thing, so they count.
    """
    sent = job.get("reminderSent")
    if sent is None:
        return False
    return (not isinstance(sent, str)) or sent == deadline


def checkRemindJobsForUser(connection, user_id, details=False):
    """The jobs of a registered user to warn about in this run.

    Job ids by default; with details=True, one dict per job with what the
    message needs: jobID, name, deadline ("%Y%m%d") and deletionDate (a date).
    """
    #Get all jobs for user
    jobs_list = connection[MONGODB_DATABASE]['jobInstanceCollection'].find({"userID": str(user_id)})
    # A warning has to arrive BEFORE the thing it warns about. This selected
    # jobs whose accessDate was between MAX_JOB_DAYS and MAX_JOB_DAYS + 7 days
    # old -- every one of which is also older than MAX_JOB_DAYS, so STEP 4 had
    # already deleted it by the time STEP 8 mailed about it. The comment even
    # says "avoid sending reminders of jobs that will be deleted today", which
    # is what the window was meant to do and the opposite of what it did.
    #
    # The reminder now covers the last REMINDER_WINDOW_DAYS of a job's life:
    # still present, deleted soon. With a 14-day retention that is a warning on
    # day 7, and /pa_touch_job clears `reminderSent` so simply opening the job
    # both saves it and re-arms the warning for next time.
    warn_from = datetime.date.today() - datetime.timedelta(days=MAX_JOB_DAYS)
    warn_until = datetime.date.today() - datetime.timedelta(
        days=max(0, MAX_JOB_DAYS - REMINDER_WINDOW_DAYS))
    jobs_remind = []
    # for each job
    for job in jobs_list:
        date = jobAccessDate(job)
        if date is None:
            continue
        # Older than warn_from is already being deleted in this same run.
        if not (warn_from < date <= warn_until):
            continue
        deletion = jobDeletionDate(date)
        deadline = deletion.strftime("%Y%m%d")
        if alreadyWarned(job, deadline):
            continue
        log("Job " + str(job["jobID"]) + " (user " + str(user_id) + ") marked to be reminded.")
        if details:
            jobs_remind.append({"jobID": job["jobID"], "name": job.get("name") or "",
                                "deadline": deadline, "deletionDate": deletion})
        else:
            jobs_remind.append(job['jobID'])
    return jobs_remind


def removeJobByJobID(connection, user_id, job_id):
    log("Removing job " + job_id)
    #STEP 1. REMOVE ALL THE FEATURES ASSOCIATED TO JOB
    connection[MONGODB_DATABASE]['featuresCollection'].delete_many({"jobID": job_id})
    #STEP 2. REMOVE ALL THE VISUAL OPTIONS ASSOCIATED TO JOB
    connection[MONGODB_DATABASE]['visualOptionsCollection'].delete_many({"jobID": job_id})
    #STEP 3. REMOVE ALL THE PATHWAYS ASSOCIATED TO JOB
    connection[MONGODB_DATABASE]['pathwaysCollection'].delete_many({"jobID": job_id})
    #STEP 4. REMOVE ALL THE FOUND FEATURES ASSOCIATED TO JOB
    connection[MONGODB_DATABASE]['foundFeaturesCollection'].delete_many({"jobID": job_id})
    #STEP 5. REMOVE THE AI INTERPRETATION OF THE JOB
    #
    # This collection was never in this list. Every expired or deleted job left
    # its interpretation behind, and on paintomics.uv.es that had reached 366 of
    # 437 records -- 84% of them belonging to jobs that no longer existed. They
    # are not small: 88KB on average, and they hold the report text, the cited
    # papers and the user's chat with the agent, which is to say the most
    # sensitive part of the job outliving the job itself.
    connection[MONGODB_DATABASE]['aiInterpretationCollection'].delete_many({"jobID": job_id})
    # ...and its graph walks, which quote the same values and papers.
    connection[MONGODB_DATABASE]['aiWalkCollection'].delete_many({"jobID": job_id})
    #STEP 6. REMOVE THE JOB FROM DATABASE
    connection[MONGODB_DATABASE]['jobInstanceCollection'].delete_many({"jobID": job_id})
    #STEP 7. REMOVE THE JOB DIRECTORY FROM USER DIR
    removeDirectoryByUserID(user_id, job_id)


def _releaseExpiryMailDay(users, user_id, today, previous):
    """Give back the day claimed in remindUserOfJobs, if it is still ours."""
    undo = {"$set": {EXPIRY_MAIL_DAY_FIELD: previous}} if previous else \
        {"$unset": {EXPIRY_MAIL_DAY_FIELD: ""}}
    users.update_one({"userID": user_id, EXPIRY_MAIL_DAY_FIELD: today}, undo)


def remindUserOfJobs(connection, user_id, expiring, ROOT_DIRECTORY, today=None):
    """Send user_id ONE message about every job in `expiring`.

    `expiring` is checkRemindJobsForUser(..., details=True). Returns the ids of
    the jobs the message was about; [] when nothing was sent.

    This used to be remindJobByJobID, called once per job: a user with seven
    jobs reaching the warning window on the same day got seven messages in
    fifteen seconds (paintomics.org, 2026-11-05). Now:

      * the user's day is claimed first, atomically, so at most one warning
        reaches an account per day even if two runs overlap;
      * each job is claimed for its deadline, atomically, so no job is warned
        about twice for the same deadline;
      * a failure that may be temporary (no SMTP password, network, a 4xx)
        gives both claims back, so the next run tries again -- the window is
        a week wide, so a missed day is not a missed warning;
      * an address the server refuses outright has its mail turned off
        (UserMail.sendToUser) and its claims are kept, so it is not retried.
    """
    db = connection[MONGODB_DATABASE]
    users = db['userCollection']
    jobs = db['jobInstanceCollection']

    user = users.find_one({"userID": user_id})
    if user is None or not expiring:
        return []
    reason = UserMail.whyNotMail(user)
    if reason is not None:
        log("Not warning user " + str(user_id) + ": " + reason + ".")
        return []

    day = (today or datetime.date.today()).strftime("%Y%m%d")
    before = users.find_one_and_update(
        {"userID": user_id, EXPIRY_MAIL_DAY_FIELD: {"$ne": day}},
        {"$set": {EXPIRY_MAIL_DAY_FIELD: day}})
    if before is None:
        log("User " + str(user_id) + " was already warned today; " + str(len(expiring)) +
            " job(s) wait for the next run.")
        return []
    previous = before.get(EXPIRY_MAIL_DAY_FIELD)

    claimed = []
    for job in expiring:
        # The same test as alreadyWarned, as one atomic update: absent (or
        # null), or a string naming another deadline. The legacy number 1
        # matches neither, as alreadyWarned already decided.
        result = jobs.update_one(
            {"jobID": job["jobID"], "userID": str(user_id),
             "$or": [{"reminderSent": None},
                     {"reminderSent": {"$type": "string", "$ne": job["deadline"]}}]},
            {"$set": {"reminderSent": job["deadline"]}})
        if result.modified_count:
            claimed.append(dict(job, link=PAINTOMICS_BASE_URL + "/?jobID=" + str(job["jobID"])))

    if not claimed:
        _releaseExpiryMailDay(users, user_id, day, previous)
        return []

    claimedIDs = [job["jobID"] for job in claimed]
    log("Reminding user " + str(user_id) + " of " + str(len(claimed)) + " job(s): " +
        ", ".join(str(job_id) for job_id in claimedIDs))
    subject = PRODUCT_NAME + (": one job is going to expire soon" if len(claimed) == 1
                              else ": %d jobs are going to expire soon" % len(claimed))
    try:
        message = jobExpiryEmail(user.get("userName"), claimed)
        outcome = UserMail.sendToUser(user, subject, message, ROOT_DIRECTORY,
                                      userCollection=users, isHTML=True)
    except Exception as ex:
        logging.error("Failed to send the expiry warning to user %s (%s); "
                      "the next run will try again.", user_id, ex)
        for job in claimed:
            jobs.update_one({"jobID": job["jobID"], "reminderSent": job["deadline"]},
                            {"$unset": {"reminderSent": ""}})
        _releaseExpiryMailDay(users, user_id, day, previous)
        return []

    if outcome != UserMail.SENT:
        log("Expiry warning to user " + str(user_id) + " not delivered: " + outcome + ".")
        return []
    return claimedIDs


def removeAllFilesByUserID(connection, user_id, only_db=False):
    log("Removing files for user " + str(user_id) + " from database")
    #STEP 1. REMOVE ALL THE FEATURES ASSOCIATED TO JOB
    connection[MONGODB_DATABASE]['fileCollection'].delete_many({"userID": str(user_id)})
    #STEP 2. REMOVE THE DIRECTORIES FOR USER
    if not only_db:
        removeDirectoryByUserID(user_id)

def removeDirectoryByUserID(user_id, job_id=None):
    #STEP 1. BUILD THE PATH
    dir = CLIENT_TMP_DIR.rstrip("/") + "/" + str(user_id)
    if job_id != None:
        # The job's scratch directory (Job.setDirectories: <user>/tmp/<jobID>)
        # too. A run normally empties it, a run that died does not, and
        # nothing else ever looked there.
        scratch = dir + "/tmp/" + job_id
        if os.path.isdir(scratch) and not os.path.islink(scratch):
            log("Removing scratch files of job " + job_id + " from " + scratch)
            shutil.rmtree(scratch)
        dir+= "/jobsData/" + job_id
    #STEP 2. REMOVE THE DIRECTORY AND ALL CHILDREN
    if os.path.isdir(dir):
        log("Removing files for user " + str(user_id) + " from directory " + dir)
        shutil.rmtree(dir)
    else:
        log("Directory " + dir + " not found!")


def _entrySize(path):
    """Bytes under path, for the log; 0 for whatever cannot be read."""
    try:
        if os.path.islink(path) or not os.path.isdir(path):
            return os.lstat(path).st_size
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _megabytes(size):
    return "%.1f MB" % (size / 1e6)


def _namedInputFiles(job):
    """Base names of the files a job's input omics point at.

    geneBasedInputOmics / compoundBasedInputOmics entries carry them as
    inputDataFile, relevantFeaturesFile, associationsFile, designFile and so
    on; any key ending in "File" counts, so a field added later is covered.
    """
    names = set()
    for key in ("geneBasedInputOmics", "compoundBasedInputOmics"):
        for omic in job.get(key) or []:
            if not isinstance(omic, dict):
                continue
            for field, value in omic.items():
                if field.endswith("File") and isinstance(value, str) and value:
                    names.add(os.path.basename(value))
    return names


def planAnonymousFileSweep(connection, removing=(), today=None):
    """What sweepAnonymousFiles would delete now, as [(path, bytes, reason)].

    An entry of CLIENT_TMP/nologin/{inputData,jobsData,tmp} goes when all of
    these hold:

      * no job in the database owns it: for jobsData/ and tmp/ the entry's
        name is the job id; for inputData/ the part before the first "_" is
        (jobIDs never contain "_", so "E7r3oPI1HS_x.tab" belongs to exactly
        E7r3oPI1HS);
      * for inputData/, no job in the database names it as an input file. A
        converter's output ("B2G_output_...", or another job's
        "<id>_regulator2Gene_output...") is read by the pathway job it was
        made for, which may outlive the job that wrote it;
      * it was last modified more than MAX_GUEST_JOB_DAYS days ago, the same
        boundary as the jobs themselves. A job being submitted right now has
        written its files but not yet its database record, and this keeps
        them.

    `removing` are job ids to treat as already gone (the dry run, and the
    summary, plan before STEP 4 removes them).
    """
    root = os.path.join(CLIENT_TMP_DIR, ANONYMOUS_DIR)
    removing = set(str(job_id) for job_id in removing)
    live = set()
    named = set()
    projection = {"jobID": 1, "geneBasedInputOmics": 1, "compoundBasedInputOmics": 1}
    for job in connection[MONGODB_DATABASE]['jobInstanceCollection'].find({}, projection):
        job_id = job.get("jobID")
        if job_id is None or str(job_id) in removing:
            continue
        live.add(str(job_id))
        named |= _namedInputFiles(job)

    cutoff = (today or datetime.date.today()) - datetime.timedelta(days=MAX_GUEST_JOB_DAYS)
    plan = []
    for subdir in ANONYMOUS_SUBDIRS:
        base = os.path.join(root, subdir)
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for name in entries:
            owner = name.split("_", 1)[0] if subdir == "inputData" else name
            if owner in live:
                continue
            if subdir == "inputData" and name in named:
                continue
            path = os.path.join(base, name)
            try:
                modified = datetime.date.fromtimestamp(os.lstat(path).st_mtime)
            except OSError:
                continue
            if modified >= cutoff:
                continue
            reason = "no job owns or names it" if subdir == "inputData" else "no job owns it"
            plan.append((path, _entrySize(path),
                         "%s, last changed %s" % (reason, modified.isoformat())))
    return plan


def sweepAnonymousFiles(connection, removing=()):
    """Delete the files of anonymous jobs that no longer exist.

    See planAnonymousFileSweep for the rule; cleanDatabases(dry_run=True)
    lists the same plan without acting on it. Returns the plan it acted on.
    Every path removed is logged, so the log is the record of what went.
    """
    plan = planAnonymousFileSweep(connection, removing)
    removed = 0
    freed = 0
    for path, size, reason in plan:
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            removed += 1
            freed += size
            log("Removed " + path + " (" + reason + ")")
        except FileNotFoundError:
            pass
        except OSError as ex:
            log("Could not remove " + path + ": " + str(ex))
    log("Removed " + str(removed) + " files and directories of anonymous jobs (" +
        _megabytes(freed) + ").")
    return plan


def removeUserByUserID(connection, user_id):
    #STEP 1. REMOVE THE USER ENTRY
    user = connection[MONGODB_DATABASE]['userCollection'].find_one({"userID": user_id})
    if not user["userName"] in ADMIN_ACCOUNTS: #prevent admin accounts to be removed
        log("Removing user " + str(user_id) + " from database.")
        connection[MONGODB_DATABASE]['userCollection'].delete_many({"userID": user_id})
    else:
        log("User " + user["userName"] + " cannot be removed.")


def fixUserDataByUserID(connection, user_id):
    log("Fixing user " + str(user_id))
    #STEP 1. REMOVE ALL FILES ENTRIES
    removeAllFilesByUserID(connection, user_id, only_db=True)
    #STEP 2. REMOVE ALL JOBS
    jobs_to_remove = checkRemoveJobsForUser(connection, user_id, force_remove=True)
    for job_id in jobs_to_remove:
        removeJobByJobID(connection, user_id, job_id)
    #STEP 3. CREATE THE DIRECTORIES
    dir = CLIENT_TMP_DIR.rstrip("/") + "/" + str(user_id)
    log("Creating directories at " + dir)
    paths = [
        dir,
        os.path.join(dir, "inputData"),
        os.path.join(dir, "jobsData"),
        os.path.join(dir, "tmp"),
    ]
    for path in paths:
        os.makedirs(path, exist_ok=True)

def rebuildIndexes(connection):
    # The daily reindex() loop that used to stand here is gone.
    #
    # It rebuilt every index of all eight job collections from scratch, inside
    # the single serving process, once every 24h -- including featuresCollection
    # (2.85M documents / 2.1GB on this machine), which takes a strong collection
    # lock for the duration. It produced no observable output: the indexes it
    # rebuilt were the indexes it started with.
    #
    # It was also a live crash. reindex() is deprecated in pymongo 3.11 and
    # REMOVED in pymongo 4, which requirements.txt now pins: under pymongo 4
    # `collection.reindex` resolves to a *sub-collection* named "reindex"
    # instead of a method, and calling it raises TypeError, which the
    # `except OperationFailure` did not catch. The cron would have died before
    # reaching the create_index calls below -- and this function is the only
    # place job indexes are ever created, so a fresh deployment would have run
    # every jobID query as a collection scan.
    #
    # The create_index calls stay: they are idempotent, cheap when the index
    # exists, and they are the point of the whole function.
    log("Creating jobID indexes...")
    for collectionName, keys in JOBID_INDEXES:
        try:
            connection[MONGODB_DATABASE][collectionName].create_index(keys)
        except OperationFailure as err:
            # One unindexable collection must not cost the others their index.
            log("Failed to create index " + str(keys) + " on " + collectionName + ": " + str(err))
    log("jobID indexes created.")

    # The per-organism pathway `source` index, for the same reason the jobID
    # indexes are here: a deployment restored from a dump, or an organism
    # installed by a build that predates the index, must not wait for a
    # rebuild. Idempotent; see DatabaseAvailability.PATHWAY_SOURCE_INDEX.
    from src.common import DatabaseAvailability
    log("Creating pathway source indexes...")
    indexed = DatabaseAvailability.ensurePathwaySourceIndexes(connection)
    log("Pathway source index ensured for " + str(len(indexed)) + " organisms.")

def log(msg):
    print(msg)
    frame,filename,line_number,function_name,lines,index=inspect.getouterframes(inspect.currentframe())[1]
    line=lines[0]
    indentation_level=line.find(line.lstrip())
    logging.info('{i} {m}'.format(i=' '*indentation_level,m=msg))


def readConfigurationFile():
    import os
    ROOT_DIRECTORY = os.path.abspath(os.path.dirname(os.path.realpath(__file__)) + "/../") + "/"
    #PREPARE LOGGING
    from src.common.LoggingSetup import configureLogging
    configureLogging(ROOT_DIRECTORY + 'conf/logging.cfg')


def confirm(prompt=None, resp=False):
    """prompts for yes or no response from the user. Returns True for yes and
    False for no.

    'resp' should be set to the default value assumed by the caller when
    user simply types ENTER.

    >>> confirm(prompt='Create Directory?', resp=True)
    Create Directory? [y]|n:
    True
    >>> confirm(prompt='Create Directory?', resp=False)
    Create Directory? [n]|y:
    False
    >>>
    Create Directory? [n]|y: y
    True

    """

    if prompt is None:
        prompt = 'Confirm'

    if resp:
        prompt = '%s [%s]|%s: ' % (prompt, 'y', 'n')
    else:
        prompt = '%s [%s]|%s: ' % (prompt, 'n', 'y')

    while True:
        ans = input(prompt)
        if not ans:
            return resp
        if ans not in ['y', 'Y', 'n', 'N']:
            print('please enter y or n.')
            continue
        if ans == 'y' or ans == 'Y':
            return True
        if ans == 'n' or ans == 'N':
            return False


if __name__ == "__main__":
    # Preview a run without changing anything -- what it would delete and how
    # many warnings it would send:
    #
    #   cd PaintomicsServer && python -m src.AdminTools.scripts.clean_databases --dry-run
    #
    # Without --dry-run it asks before acting, as it always has.
    cleanDatabases(force=False, dry_run="--dry-run" in sys.argv[1:])

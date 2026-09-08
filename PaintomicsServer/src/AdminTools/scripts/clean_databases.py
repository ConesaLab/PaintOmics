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

from src.common.Util import sendEmail
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

# How long before a registered user's job expires they are warned about it.
REMINDER_WINDOW_DAYS = 7

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
]

def cleanDatabases(force=False):
    log("Starting Clean Databases routine")

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
        else:
            # Check if user has jobs to be removed soon that need to be remained about
            # (only for "no guest" accounts)
            reminders = checkRemindJobsForUser(connection, user_id)

            if len(reminders) > 0:
                jobs_to_remind[user_id] = reminders


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

    log("Summary:")
    log("   - " + str(str(sum(len(x) for x in jobs_to_remove.values()))) + " jobs will be removed.")
    log("   - " + str(str(sum(len(x) for x in jobs_to_remind.values()))) + " reminder e-mails will be sent.")
    log("   - " + str(len(users_to_remove)) + " users will be removed.")
    log("   - " + str(len(users_to_fix)) + " users will be fixed.")
    log("   - " + str(len(user_dirs)) + " orphan directories will be removed.")
    log("")

    if not force:
        if not confirm(prompt='"Proceed?', resp=False):
            log("Bye.")
            return

    # STEP 4. REMOVE ALL OUTDATED JOBS (+ FEATURES AND FILES)
    for user_id, jobs_to_remove in jobs_to_remove.items():
        for job_id in jobs_to_remove:
            removeJobByJobID(connection, user_id, job_id)

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

    # STEP 8. SEND REMINDER E-MAILS
    for user_id, jobs_to_remind in jobs_to_remind.items():
        for job_id in jobs_to_remind:
            remindJobByJobID(connection, user_id, job_id, ROOT_DIRECTORY)

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

def checkRemindJobsForUser(connection, user_id):
    #Get all jobs for user
    jobs_list = connection[MONGODB_DATABASE]['jobInstanceCollection'].find({"userID":str(user_id),
                                                                            "reminderSent": {"$exists": False}
                                                                            })
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
        if warn_from < date <= warn_until:
            log("Job " + str(job["jobID"]) + " (user " + str(user_id) + ") marked to be reminded.")
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
    #STEP 6. REMOVE THE JOB FROM DATABASE
    connection[MONGODB_DATABASE]['jobInstanceCollection'].delete_many({"jobID": job_id})
    #STEP 7. REMOVE THE JOB DIRECTORY FROM USER DIR
    removeDirectoryByUserID(user_id, job_id)


def remindJobByJobID(connection, user_id, job_id, ROOT_DIRECTORY):
    log("Reminding job " + job_id)

    ROOT_DIRECTORY_CORRECTED = os.path.abspath(ROOT_DIRECTORY + '/../../../PaintomicsClient/') + '/'

    try:
        user_data = connection[MONGODB_DATABASE]['userCollection'].find_one({"userID": user_id})

        reminder_link = PAINTOMICS_BASE_URL + "/?jobID=" + job_id
        message = jobExpiryEmail(user_data["userName"], job_id, reminder_link)

        sendEmail(ROOT_DIRECTORY_CORRECTED, user_data["email"], user_data["userName"],
                  PRODUCT_NAME + ": one job is going to expire soon", message, isHTML=True)
    except Exception:
        logging.error("Failed to send the email.")

    #STEP 3.REMOVE THE JOB FROM DATABASE
    connection[MONGODB_DATABASE]['jobInstanceCollection'].update_many({"jobID": job_id}, {'$set': {"reminderSent": 1}}, upsert=False)


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
        dir+= "/jobsData/" + job_id
    #STEP 2. REMOVE THE DIRECTORY AND ALL CHILDREN
    if os.path.isdir(dir):
        log("Removing files for user " + str(user_id) + " from directory " + dir)
        shutil.rmtree(dir)
    else:
        log("Directory " + dir + " not found!")


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

# cleanDatabases(force=True)

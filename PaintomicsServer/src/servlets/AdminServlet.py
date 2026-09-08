#***************************************************************
#  This file is part of Paintomics v3
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#**************************************************************
import logging
import logging.config
import json
from os import path as os_path
from shutil import rmtree as shutil_rmtree
import csv

#CPU MONITOR
import psutil
import subprocess
from datetime import datetime
import html

from src.common.UserSessionManager import UserSessionManager
from src.common.ServerErrorManager import handleException
import src.conf.serverconf as _serverconf
from src.common.DAO.UserDAO import UserDAO
from src.common.DAO.JobDAO import JobDAO
from src.common.DAO.FileDAO import FileDAO
from src.common.DAO.MessageDAO import MessageDAO
from src.common.DAO.ReportDAO import ReportDAO
from src.classes.Message import Message
from src.classes.Report import Report

from src.common.Util import sendEmail
from src.common.EmailTemplates import reportNotificationEmail

from src.conf.serverconf import (
    MONGODB_HOST,
    MONGODB_PORT,
    KEGG_DATA_DIR,
    CLIENT_TMP_DIR,
    smpt_sender,
    smpt_sender_name,
    MAX_CLIENT_SPACE,
    MAX_JOB_DAYS,
    MAX_GUEST_DAYS,
    EMAIL_REPORT_RECIPIENTS,
)
from src.servlets.DataManagementServlet import dir_total_size

#----------------------------------------------------------------
# DATABASES
#----------------------------------------------------------------
def adminServletGetInstalledOrganisms(request, response):
    """
    This function...

    @param {Request} request, the request object
    @param {Response} response, the response object
    """
    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1.GET THE LIST OF INSTALLED SPECIES (DATABASES and SPECIES.JSON)
        #****************************************************************
        organisms_names = {}
        with open(KEGG_DATA_DIR + 'current/common/organisms_all.list') as organisms_all:
            reader = csv.reader(organisms_all, delimiter='\t')
            for row in reader:
                organisms_names[row[1]] = row[2]
        organisms_all.close()

        from pymongo import MongoClient

        client = MongoClient(MONGODB_HOST, MONGODB_PORT)
        databases = client.list_database_names()

        #****************************************************************
        # Step 2.FOR EACH INSTALLED DATABASE GET THE INFORMATION
        #****************************************************************
        databaseList=[]
        common_info_date=""

        for database in databases:
            if not "-paintomics" in database:
                continue
            elif "global-paintomics" == database:
                db = client[database]
                common_info_date = db.versions.find({"name": "COMMON"})[0].get("date")

                # Step 2.4 Check if the organism has non installed data available
                if os_path.isfile(KEGG_DATA_DIR + 'download/common/VERSION'):
                    downloaded = True
                elif os_path.isfile(KEGG_DATA_DIR + 'download/common/DOWNLOADING'):
                    downloaded = "downloading"
                else:
                    downloaded = False
                    #Erroneous download not removed --> remove
                    if os_path.isdir(KEGG_DATA_DIR + 'download/common/'):
                        shutil_rmtree(KEGG_DATA_DIR + 'download/common/')

                databaseList.append({
                    "organism_name" : "Common KEGG data",
                    "organism_code" : "common",
                    "kegg_date"     : common_info_date,
                    "downloaded": downloaded
                })

            else:
                # Step 2.1 GET THE SPECIE CODE
                organism_code=database.replace("-paintomics", "")
                # Step 2.2 GET THE SPECIE NAME
                organism_name= organisms_names.get(organism_code, "Unknown specie")

                # Step 2.3 GET THE SPECIE VERSIONS
                db = client[database]
                kegg_date = db.versions.find({"name": "KEGG"})[0].get("date")
                mapping_date = db.versions.find({"name": "MAPPING"})[0].get("date")
                # count_documents replaces Cursor.count(), removed in pymongo 4.
                # find_one does both the existence check and the fetch in one
                # round-trip.
                acceptedIDsDoc = db.versions.find_one({"name": "ACCEPTED_IDS"})
                acceptedIDs = acceptedIDsDoc.get("ids") if acceptedIDsDoc else ""

                # Step 2.4 Check if the organism has non installed data available
                if os_path.isfile(KEGG_DATA_DIR + 'download/' + organism_code + '/VERSION'):
                    downloaded = True
                elif os_path.isfile(KEGG_DATA_DIR + 'download/' + organism_code + '/DOWNLOADING'):
                    downloaded = "downloading"
                else:
                    downloaded = False
                    #Erroneous download not removed --> remove
                    if os_path.isdir(KEGG_DATA_DIR + 'download/' + organism_code):
                        shutil_rmtree(KEGG_DATA_DIR + 'download/' + organism_code)

                databaseList.append({
                    "organism_name" : organism_name,
                    "organism_code" : organism_code,
                    "kegg_date"     : kegg_date,
                    "mapping_date"  : mapping_date,
                    "acceptedIDs"   : acceptedIDs,
                    "downloaded": downloaded
                })

        client.close()

        response.setContent({"common_info_date" : common_info_date, "databaseList": databaseList})

    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletGetInstalledOrganisms")

    finally:
        return response

def adminServletGetAvailableOrganisms(request, response):
    """
    This function...

    @param {Request} request, the request object
    @param {Response} response, the response object
    """
    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1.GET THE LIST OF INSTALLED SPECIES (DATABASES and SPECIES.JSON)
        #****************************************************************
        import csv
        databaseList = []
        with open(KEGG_DATA_DIR + 'current/common/organisms_all.list') as availableSpeciesFile:
            reader = csv.reader(availableSpeciesFile,  delimiter='\t')
            for row in reader:
                organism_code = row[1]
                # Step 2.4 Check if the organism has non installed data available
                if os_path.isfile(KEGG_DATA_DIR + 'download/' + organism_code + '/VERSION'):
                    downloaded = True
                elif os_path.isfile(KEGG_DATA_DIR + 'download/' + organism_code + '/DOWNLOADING'):
                    downloaded = "downloading"
                else:
                    downloaded = False
                    #Erroneous download not removed --> remove
                    if os_path.isdir(KEGG_DATA_DIR + 'download/' + organism_code):
                        shutil_rmtree(KEGG_DATA_DIR + 'download/' + organism_code)

                databaseList.append({
                    "organism_name": row[2],
                    "organism_code": organism_code,
                    "categories": row[3].split(";"),
                    "organism_id" : row[0],
                    "downloaded": downloaded
                })
        availableSpeciesFile.close()

        response.setContent({"databaseList": databaseList, "download_log" : KEGG_DATA_DIR + "download/download.log", "install_log" : KEGG_DATA_DIR + "current/install.log"})

    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletGetInstalledOrganisms")

    finally:
        return response

def adminServletInstallOrganism(request, response, organism_code, ROOT_DIRECTORY):
    """
    This function manages an 'Install/Update Organism' request by calling to the
    DBManager tool.

    @param {Request} request, the request object
    @param {Response} response, the response object
    @param {String} organism_code,
    @param {String} ROOT_DIRECTORY,
    """
    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1.GET THE SPECIE CODE AND THE UPDATE OPTION
        #****************************************************************
        download  = json.loads(request.data).get("download")
        update_kegg=1
        update_mapping=1
        common = 0

        if organism_code == "common":
            common = 1
            organism_code = "#common"

        from subprocess import check_output, CalledProcessError, STDOUT


        #****************************************************************
        # Step 2a. IF THE SELECTED OPTION IS DOWNLOAD
        #****************************************************************
        if download:
            logging.info("STARTING DBManager download PROCESS.")
            scriptArgs = [ROOT_DIRECTORY + "AdminTools/DBManager.py", "download", "--specie=" + organism_code, "--kegg=" + str(update_kegg), "--mapping=" + str(update_mapping), "--common=" + str(common)]
            try:
                check_output(scriptArgs, stderr=STDOUT)
            except CalledProcessError as exc:
                raise Exception("Error while calling DBManager download: Exit status " + str(exc.returncode) + ". Error message: " + exc.output.decode('utf-8'))
            logging.info("FINISHED DBManager Download PROCESS.")

        # ****************************************************************
        # Step 2B. IF THE SELECTED OPTION IS INSTALL
        # ****************************************************************
        else:
            logging.info("STARTING DBManager Install PROCESS.")
            scriptArgs = [ROOT_DIRECTORY + "AdminTools/DBManager.py", "install", "--specie=" + organism_code, "--common=" + str(common)]
            try:
                check_output(scriptArgs, stderr=STDOUT)
            except CalledProcessError as exc:
                raise Exception("Error while calling DBManager Install: Exit status " + str(exc.returncode) + ". Error message: " + exc.output.decode('utf-8'))
            logging.info("FINISHED DBManager Install PROCESS.")

        response.setContent({"success": True})

    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletInstallOrganism")

    finally:
        return response

def adminServletRestoreData(request, response, ROOT_DIRECTORY):
    # ROOT_DIRECTORY is an argument, as in adminServletInstallOrganism: the
    # body built the DBManager path from a name that was never defined here,
    # so any call would have died with NameError before running anything.
    """
    This function...

    @param {Request} request, the request object
    @param {Response} response, the response object
    """
    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1.GET THE SPECIE CODE AND THE UPDATE OPTION
        #****************************************************************

        from subprocess import check_output, CalledProcessError, STDOUT

        logging.info("STARTING DBManager Restore PROCESS.")
        scriptArgs = [ROOT_DIRECTORY + "AdminTools/DBManager.py", "restore", "--remove=1", "--force=1"]
        try:
            check_output(scriptArgs, stderr=STDOUT)
        except CalledProcessError as exc:
            raise Exception("Error while calling DBManager Restore: Exit status " + str(exc.returncode) + ". Error message: " + exc.output.decode('utf-8'))
        logging.info("FINISHED DBManager Restore PROCESS.")

        response.setContent({"success": True})

    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletRestoreData")

    finally:
        return response

def clearFailedData():
    import shutil, os
    dirname = KEGG_DATA_DIR + 'download/'
    for subdirname in os.listdir(dirname):
        # print path to all subdirectories first.
        if os.path.isdir(os.path.join(dirname, subdirname)) and os.path.isfile(os.path.join(dirname, subdirname) + "/DOWNLOADING"):
                print("Removing " + os.path.join(dirname, subdirname))
                shutil.rmtree(os.path.join(dirname, subdirname))
#----------------------------------------------------------------
# USERS
#----------------------------------------------------------------
# The fields the admin panel actually renders, and the only ones that leave the
# server. Taken from admin/templates/user-row.tpl.html, the only template that
# binds user.*: it reads exactly these seven, plus usedSpace, which is computed
# per request rather than stored and so is added separately.
ADMIN_USER_FIELDS = ("userID", "userName", "email", "affiliation",
                     "creation_date", "last_login", "is_guest")


def summarizeUserForAdmin(userInstance, usedSpace):
    """The admin view of a user: what the panel displays, and nothing else.

    The list used to go out as the User objects themselves. MyJSONEncoder
    serialises anything with a toBSON() by calling it, and User does not
    override the one it inherits from Model, which returns `self.__dict__`
    whole. So the response carried every attribute a User has, including the
    four the panel never reads:

        password, resetToken, resetPassword, sessionToken

    Measured against the live collection: `password` is populated on all 16
    stored users. It is sha1(password) with no salt -- see the four
    `sha1(password.encode('utf-8')).hexdigest()` call sites in
    UserManagementServlet -- so an unsalted SHA-1 of a common password is
    recovered from a rainbow table instantly, and the stored value for one of
    these accounts is d033e22ae348aeb5660fc2140aef11803e5c1c2, which is
    SHA-1("admin"). Password reuse being what it is, that is the user's
    password elsewhere too, not just here.

    The route is admin-gated, so this is defence in depth rather than an open
    door. It still matters: the hashes land in a browser, in its memory and
    disk cache, in any intermediary that logs response bodies, and in the
    devtools of whoever is looking at the panel. None of that is needed for a
    page that shows names, e-mail addresses and disk usage.

    Whitelisted rather than blacklisted so a field added to User later is
    excluded by default -- the failure mode of forgetting to add a name here is
    a missing column, not another silent leak.

    Not fixed in User.toBSON, which would be the tempting single place: that
    same method is what UserDAO.insert and update persist, so dropping the
    password from it would write accounts that can never log in.
    """
    summary = {field: getattr(userInstance, field, None)
               for field in ADMIN_USER_FIELDS}
    summary["usedSpace"] = usedSpace
    return summary


def adminServletGetAllUsers(request, response):
    """
    This function obtains a list of all the users registered in the system including different details
    such as the used space, the registration date, etc.

    @param {Request} request, the request object
    @param {Response} response, the response object
    """
    daoInstance = None
    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1. GET THE LIST OF ALL USERS
        #****************************************************************
        logging.info("STEP1 - GET THE LIST OF ALL USERS...")
        daoInstance = UserDAO()
        userList = daoInstance.findAll()

        userSummaries = []
        for userInstance in userList:
            usedSpace = 0
            if os_path.isdir(CLIENT_TMP_DIR + str(userInstance.getUserId())):
                usedSpace = dir_total_size(CLIENT_TMP_DIR + str(userInstance.getUserId()))
            userSummaries.append(summarizeUserForAdmin(userInstance, usedSpace))

        response.setContent({"success": True, "userList": userSummaries,  "availableSpace": MAX_CLIENT_SPACE, "max_jobs_days": MAX_JOB_DAYS, "max_guest_job_days": getattr(_serverconf, "MAX_GUEST_JOB_DAYS", 7), "max_guest_days" : MAX_GUEST_DAYS})

    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletGetAllUsers")

    finally:
        # Closed in a finally for the same reason as the handlers below:
        # DBmanager builds a new MongoClient per DAO, each with its own monitor
        # threads, and this one was never closed at all -- the admin panel polls
        # it, so every refresh leaked a client.
        if daoInstance is not None:
            daoInstance.closeConnection()
        return response

def adminServletDeleteUser(request, response, toDeleteUserID):
    """
    This function...

    @param {Request} request, the request object
    @param {Response} response, the response object
    """
    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        if toDeleteUserID == "0":
            response.setContent({"success": False})
        else:
            jobDAOInstance = JobDAO()
            filesDAOInstance = FileDAO()
            userDAOInstance = UserDAO()

            logging.info("STEP1 - CLEANING DATA FOR " + toDeleteUserID + "...")
            #****************************************************************
            # Step 1. DELETE ALL JOBS FOR THE USER
            #****************************************************************
            allJobs = jobDAOInstance.findAll(otherParams={"userID":toDeleteUserID})
            jobID = ""
            for jobInstance in allJobs:
                jobID = jobInstance.getJobID()
                logging.info("STEP2 - REMOVING " + jobID + " FROM DATABASE...")
                jobDAOInstance.remove(jobInstance.getJobID(), otherParams={"userID":toDeleteUserID})

            #****************************************************************
            # Step 3. DELETE ALL FILES FOR THE USER
            #****************************************************************
            logging.info("STEP3 - REMOVING ALL FILES FROM DATABASE...")
            filesDAOInstance.removeAll(otherParams={"userID":toDeleteUserID})
            logging.info("STEP3 - REMOVING ALL FILES FROM USER DIRECTORY...")
            if os_path.isdir(CLIENT_TMP_DIR + toDeleteUserID):
                shutil_rmtree(CLIENT_TMP_DIR + toDeleteUserID)

            #****************************************************************
            # Step 4. DELETE THE USER INSTANCE FROM DATABASE
            #****************************************************************
            logging.info("STEP6 - REMOVING ALL FILES FROM DATABASE...")
            userDAOInstance.remove(int(toDeleteUserID))

            response.setContent({"success": True})
    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletDeleteUser")
    finally:
        return response

def adminCleanDatabases(request, response):
    """
    This function...

    @param {Request} request, the request object
    @param {Response} response, the response object
    """
    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1. RUN THE SCRIPT
        #****************************************************************
        from src.AdminTools.scripts.clean_databases import cleanDatabases as clean_databases_routine
        clean_databases_routine(force=True)

        response.setContent({"success": True})

    except Exception as ex:
        handleException(response, ex, __file__ , "cleanDatabases")

    finally:
        return response

#----------------------------------------------------------------
# MESSAGES
#----------------------------------------------------------------
def adminServletSaveMessage(request, response):
    # See adminServletGetMessage: a DAO is a MongoClient with monitor threads,
    # and it must come back even when the write raises.
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1.SAVE THE MESSAGE IN THE DATABASE
        #****************************************************************
        messageInstance = Message(request.json.get("message_type"))
        messageInstance.message_content = request.json.get("message_content")

        #****************************************************************
        # Step 2. SAVE THE MESSAGE
        #****************************************************************
        daoInstance = MessageDAO()
        daoInstance.removeAll(otherParams={"message_type" : messageInstance.message_type})
        daoInstance.insert(messageInstance)
        response.setContent({"success": True })

    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletSaveMessage")
    finally:
        if daoInstance is not None:
            daoInstance.closeConnection()
        return response

def adminServletGetMessage(request, response):
    # Closed in the finally rather than after the query: DBmanager builds a new
    # MongoClient per DAO, each with its own monitor threads, and findAll raises
    # exactly when the database is unreachable -- i.e. when every request is
    # failing at once. Leaking a client per failure makes that worse. This is
    # also the handler behind the public welcome banner, so it runs on every
    # page load.
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        message_type = request.form.get("message_type")

        if(message_type != "starting_message"):
            logging.info("STEP0 - CHECK IF VALID USER....")
            userID  = request.cookies.get('userID')
            sessionToken  = request.cookies.get('sessionToken')

            UserSessionManager().isValidUser(userID, sessionToken)

        #****************************************************************
        # Step 1.GET THE MESSAGES FROM THE DATABASE
        #****************************************************************
        daoInstance = MessageDAO()
        matchedMessages = daoInstance.findAll(otherParams={"message_type" : message_type})
        response.setContent({"success": True, "messageList" : matchedMessages})

    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletGetMessage")
    finally:
        if daoInstance is not None:
            daoInstance.closeConnection()
        return response

def adminServletDeleteMessage(request, response, message_type=None):
    # See adminServletGetMessage.
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1.GET THE MESSAGES FROM THE DATABASE
        #****************************************************************
        if message_type == None:
            message_type = request.form.get("message_type")
        daoInstance = MessageDAO()
        daoInstance.removeAll(otherParams={"message_type" : message_type})

        response.setContent({"success": True})

    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletDeleteMessage")
    finally:
        if daoInstance is not None:
            daoInstance.closeConnection()
        return response

#----------------------------------------------------------------
# SYSTEM
#----------------------------------------------------------------
def adminServletSystemInformation(request, response):
    """
    This function...

    @param {Request} request, the request object
    @param {Response} response, the response object
    """
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)
        disk_use = []
        try:
            df = subprocess.Popen(["df", "-h"], stdout=subprocess.PIPE)
            output = df.communicate()[0]
            output = output.split(b"\n")
            output.pop(0)
            for line in output:
                line = line.decode("utf-8")
                disk_use.append(line.split())
        except Exception:
            pass

        return response.setContent({
            'cpu_count' : psutil.cpu_count(),
            "cpu_use" : psutil.cpu_percent(),
            "mem_total" : psutil.virtual_memory().total/(1024.0**3),
            "mem_use" : psutil.virtual_memory().percent,
            "swap_total": psutil.swap_memory().total/(1024.0**3),
            "swap_use" : psutil.swap_memory().percent,
            "disk_use": disk_use
        }).getResponse()

    except Exception as ex:
        handleException(response, ex, __file__ , "monitorCPU")
    finally:
        return response

def adminServletGetReports(request, response):
    """
    List the reports users have submitted (organism requests, error reports).

    These are stored by adminServletSendReport before any delivery is attempted,
    so they are the record of what users asked for even when outbound mail is
    unavailable -- which it has been in production. Without this handler the
    only way to read them is a Mongo shell on the server.

    @param {Request} request, the request object
    @param {Response} response, the response object
    """
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1. GET THE LIST OF REPORTS
        #****************************************************************
        logging.info("STEP1 - GET THE LIST OF REPORTS...")
        daoInstance = ReportDAO()
        reportList = [reportInstance.toBSON() for reportInstance in daoInstance.findAll()]

        # Surfaced so the panel can warn that mail is down rather than letting
        # an operator assume these were also emailed to them.
        undelivered = len([r for r in reportList if not r.get("delivered")])

        response.setContent({
            "success": True,
            "reportList": reportList,
            "undelivered": undelivered,
        })

    except Exception as ex:
        handleException(response, ex, __file__, "adminServletGetReports")

    finally:
        if daoInstance is not None:
            daoInstance.closeConnection()
        return response


def adminServletDeleteReport(request, response, reportID):
    """
    Dismiss one report once it has been acted on.

    @param {Request} request, the request object
    @param {Response} response, the response object
    @param {String} reportID, the id of the report to remove
    """
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        userName = request.cookies.get('userName')
        UserSessionManager().isValidAdminUser(userID, userName, sessionToken)

        #****************************************************************
        # Step 1. REMOVE THE REPORT
        #****************************************************************
        logging.info("STEP1 - REMOVE THE REPORT...")
        daoInstance = ReportDAO()
        removed = daoInstance.remove(reportID)

        response.setContent({"success": True, "removed": removed})

    except Exception as ex:
        handleException(response, ex, __file__, "adminServletDeleteReport")

    finally:
        if daoInstance is not None:
            daoInstance.closeConnection()
        return response


#: How the request dialog has always written the organism into its HTML
#: message: <p><b>Specie:</b> Homo sapiens (human)</p>. Read when a client
#: cached from before the `specie`/`specieCode` fields sends only that.
_SPECIE_LABEL = "specie:"


def _specieFromMessage(message):
    """The organism an old client's HTML message names, or None.

    Three plain searches, each linear and each run once. The message is
    untrusted text of up to MAX_FORM_MEMORY_SIZE, posted without a session,
    so the regex this replaces (``<b>\\s*Specie:\\s*</b>\\s*(.*?)\\s*</p>``)
    was an outage waiting for one request: its three quantifiers overlap on
    whitespace, so a label followed by a run of spaces and no ``</p>`` cost
    cubic time. Dropping the inner ``\\s*`` still leaves a quadratic scan
    when the label is repeated, which is why this is not a regex at all.
    """
    text = str(message or "")
    lower = text.lower()
    label = lower.find(_SPECIE_LABEL)
    if label < 0:
        return None
    boldEnd = lower.find("</b>", label)
    if boldEnd < 0 or lower[label + len(_SPECIE_LABEL):boldEnd].strip():
        return None
    paragraphEnd = lower.find("</p>", boldEnd)
    if paragraphEnd < 0:
        return None
    return text[boldEnd + len("</b>"):paragraphEnd].strip()


def _normaliseOrganismName(name):
    return " ".join(str(name or "").split()).lower()


#: How the two request dialogs opened the HTML fragment they used to post as
#: `message`. Read to recognise that shape and nothing else.
_LEGACY_BODY_PREFIX = "<p><b>"


def _plainTextReportBody(message):
    """A report body as text, converting the fragment old clients still post.

    The maintainer notification escapes the body -- it is free-form input from
    an endpoint that takes no session -- and turns newlines into ``<br>``. The
    request dialogs now post text, but this endpoint cannot assume they do: a
    tab left open across a deploy keeps posting the fragment for as long as it
    lives, and there is no session to expire it. Without this, those reports
    arrive as literal ``<p><b>Specie:</b>`` tags.

    Conditional on the fragment's own opening, because error reports are
    already plain text and carry things like ``<module>`` in a traceback that
    must survive verbatim. What is stored is untouched; only the mail is
    converted.

    Only the five tags that fragment was ever built from are treated as markup.
    Anything else between angle brackets is kept as text, which is better than
    a browser managed: the contact form wrote the address as bare
    ``<ada@example.org>``, so rendering the fragment as HTML swallowed it as an
    unknown tag and the maintainers never saw who wrote in.

    Linear: each character is passed once, and both searches inside the loop
    advance ``index``. No regex, for the reason ``_specieFromMessage`` gives
    above.
    """
    text = str(message or "")
    if text[:64].lstrip()[:len(_LEGACY_BODY_PREFIX)].lower() != _LEGACY_BODY_PREFIX:
        return text

    breaks = ("/p", "br", "br/")
    dropped = ("p", "b")
    pieces = []
    index = 0
    while True:
        start = text.find("<", index)
        if start < 0:
            pieces.append(text[index:])
            break
        pieces.append(text[index:start])
        end = text.find(">", start + 1)
        if end < 0:                      # a bare "<" is text, not a tag
            pieces.append(text[start:])
            break
        nextOpen = text.find("<", start + 1)
        if 0 <= nextOpen < end:
            # Another "<" reaches this one before any ">" does, so it opens no
            # tag: it is a comparison somebody typed, and "<2000 genes" is an
            # ordinary thing to write here. Pairing it with the next ">" in the
            # string swallowed the real closing tag after it, which then
            # reached the maintainer as a literal "</p>" with its line break
            # lost. Emit the character and carry on from just after it, so the
            # genuine tag further along is still read as one.
            pieces.append("<")
            index = start + 1
            continue
        name = text[start + 1:end].strip().lower()
        if name in breaks:
            pieces.append("\n")
        elif name == "/b":
            # The fragment ran the label straight into its value
            # (<b>Comments:</b>text), so the close has to separate them.
            pieces.append(" ")
        elif name not in dropped:
            pieces.append(text[start:end + 1])
        index = end + 1

    lines = [" ".join(line.split())
             for line in html.unescape("".join(pieces)).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def installedOrganisms():
    """The organisms the step 1 combo offers: KEGG_DATA_DIR/current/species.json.

    That file is what DBManager wrote when it last installed something, and it
    is what the combo is loaded from -- so an organism in it is one the
    visitor can pick right now, which is the sense of "installed" a request
    dialog has to refuse. Unreadable means an empty list, never an error: a
    request that gets through is a nuisance, a request that cannot be made is
    a lost organism.
    """
    try:
        with open(os_path.join(KEGG_DATA_DIR, "current", "species.json")) as handle:
            species = json.load(handle).get("species") or []
        return [organism for organism in species
                if isinstance(organism, dict) and organism.get("value")]
    except Exception as ex:
        logging.warning("Could not read the installed organisms from species.json "
                        "(%s: %s); organism requests are not screened", type(ex).__name__, ex)
        return []


def _installedOrganism(specieName, specieCode, message):
    """The installed organism a request names, or None when it names none.

    Matched by code first (what the dialog sends when a row was picked), then
    by display name, whitespace and case aside (what the dialog sends when
    the visitor typed), then by the name against the code, for a visitor who
    typed "hsa" into a field that shows names.
    """
    name = specieName
    if not name and message:
        found = _specieFromMessage(message)
        if found:
            name = html.unescape(found)
    code = str(specieCode or "").strip().lower()
    name = _normaliseOrganismName(name)
    if not code and not name:
        return None

    for organism in installedOrganisms():
        organismCode = str(organism.get("value")).strip().lower()
        organismName = _normaliseOrganismName(organism.get("name"))
        if code and code == organismCode:
            return organism
        if name and name in (organismName, organismCode):
            return organism
    return None


def adminServletSendReport(request, response, ROOT_DIRECTORY):
    """
    This function...

    @param {Request} request, the request object
    @param {Response} response, the response object
    """
    try :
        #logging.info("STEP0 - CHECK IF VALID USER....")
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        userID  = request.cookies.get('userID')
        #sessionToken  = request.cookies.get('sessionToken')
        #UserSessionManager().isValidUser(userID, sessionToken)



        #****************************************************************
        # Step 1.GET THE SPECIE CODE AND THE UPDATE OPTION
        #****************************************************************
        formFields = request.form

        if userID is not None:
            userEmail = UserDAO().findByID(userID)
            userName = userEmail.getUserName()
            userEmail = userEmail.getEmail()
        else:
            userEmail = formFields.get("fromEmail", smpt_sender)
            userName = formFields.get("fromName", "No name provided")

        request_type = formFields.get("type")
        _message = formFields.get("message")

        if request_type == "specie_request":
            installed = _installedOrganism(formFields.get("specie"),
                                           formFields.get("specieCode"), _message)
            if installed is not None:
                logging.info("Organism request for %s (%s) refused: already installed",
                             installed["name"], installed["value"])
                response.setContent({
                    "success": False, "installed": True,
                    "organism": installed["name"], "code": installed["value"],
                    "errorMessage": installed["name"] + " is already installed. "
                    "Choose it in the Organism field of the form; no request is needed."})
                return response

        subject = "Other request"
        title = "Other request"
        color = "#333333"

        if request_type == "error":
            subject = "Error notification"
            title = "New error notification"
            color = "#C0392B"
        elif request_type == "specie_request":
            subject = "New organism requested"
            title = "New organism requested"
            color = "#0069C0"

        # The accent colours above were #f95959 and #0090ff, which are 3.0:1 and
        # 2.8:1 against white -- below the 4.5:1 a body-text colour needs, and
        # the report body is rendered IN that colour. Darkened to 5.1:1 and
        # 5.4:1; the hue is unchanged, so the two report kinds still read apart
        # at a glance.
        message = reportNotificationEmail(title, userName, userEmail,
                                          _plainTextReportBody(_message), color)

        #****************************************************************
        # Step 2.PERSIST THE REPORT BEFORE ATTEMPTING DELIVERY
        #****************************************************************
        # Delivery depends on a third party and has failed in production for
        # reasons the reporter cannot see or influence (SMTP_PASSWORD never
        # reaching the process, an exhausted provider quota). Storing first
        # means such an outage costs us the notification, not the report --
        # organism requests arrive through this very handler.
        reportInstance = Report(request_type or "other")
        reportInstance.setUserEmail(userEmail)
        reportInstance.setUserName(userName)
        reportInstance.setMessage(_message)
        reportInstance.setSubmittedAt(datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))

        reportID = None
        try:
            reportID = ReportDAO().insert(reportInstance)
        except Exception as storeEx:
            # Storage is the safety net, not the feature. If even Mongo is
            # unreachable, log the report verbatim so it survives on disk.
            logging.error("Could not store %s report from %s: %s. Report body: %s",
                          request_type, userEmail, storeEx, _message)

        #****************************************************************
        # Step 3.ATTEMPT DELIVERY, TREATING FAILURE AS NON-FATAL
        #****************************************************************
        recipients = EMAIL_REPORT_RECIPIENTS or [smpt_sender]
        delivered = 0
        deliveryError = ""
        for recipient in recipients:
            try:
                sendEmail(
                    ROOT_DIRECTORY,
                    recipient,
                    smpt_sender_name,
                    subject,
                    message,
                    fromEmail=smpt_sender,
                    fromName=userName if userName else smpt_sender_name,
                    isHTML=True
                )
                delivered += 1
            except Exception as mailEx:
                deliveryError = str(mailEx)
                logging.error("Could not email %s report to %s: %s",
                              request_type, recipient, mailEx)

        if reportID is not None:
            try:
                ReportDAO().markDelivered(reportID, delivered > 0, deliveryError)
            except Exception as markEx:
                logging.error("Could not record delivery outcome for report %s: %s",
                              reportID, markEx)

        if delivered == 0 and deliveryError:
            logging.error("Report from %s stored (id=%s) but delivered to none of %s: %s",
                          userEmail, reportID, recipients, deliveryError)

        # The report is recorded either way, so from the reporter's side the
        # submission did succeed. "delivered" lets the client word the
        # confirmation honestly without turning this into an error.
        response.setContent({"success": True, "delivered": delivered > 0})

    except Exception as ex:
        handleException(response, ex, __file__ , "adminServletSendReport")

    finally:
        return response

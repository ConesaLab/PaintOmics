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
import os, shutil
import logging
import logging.config
from flask import send_from_directory

from src.conf.serverconf import CLIENT_TMP_DIR

from src.classes.File import File
from src.common.UserSessionManager import UserSessionManager
from src.common.DAO.FileDAO import FileDAO
from src.common.DAO.JobDAO import JobDAO
from src.common.ServerErrorManager import handleException
from src.common.Util import resolveWithin

def dataManagementUploadFile(request, response, DESTINATION_DIR, isReference=False):
    #VARIABLE DECLARATION
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        userName = request.cookies.get('userName')
        sessionToken  = request.cookies.get('sessionToken')

        UserSessionManager().isValidUser(userID, sessionToken)

        #ONLY ADMIN USER (id=0) CAN UPLOAD NEW INBUILT GTF FILES
        if(isReference and UserSessionManager().isValidAdminUser(userID, userName, sessionToken)):
            userID="-1"

        #****************************************************************
        #1. SAVE THE UPLOADED FILE TO THE USER DIRECTORY AND TO THE DATABASE
        #****************************************************************
        logging.info("STEP1 - FILE UPLOADING REQUEST RECEIVED")
        formFields = request.form
        uploadedFiles  = request.files

        if not isReference:
            DESTINATION_DIR = DESTINATION_DIR + userID + "/inputData/"
        else:
            userID="-1"
            DESTINATION_DIR = DESTINATION_DIR + "GTF/"

        logging.info("STEP1 - READING FILES....")
        fields = {}
        for field in formFields.keys():
            if formFields[field] == "undefined":
                continue
            fields[field] = formFields[field]

        if isReference and formFields.get("fileName", None) != None:
            registerFile(userID, formFields.get("fileName"), fields, DESTINATION_DIR)
        else:
            for uploadedFileName in uploadedFiles.keys():
                if (uploadedFileName is not None):
                    #GET THE FILE OBJECT
                    uploadedFile = request.files.get(uploadedFileName)
                    uploadedFileName = uploadedFile.filename
                    saveFile(userID, uploadedFileName, fields, uploadedFile, DESTINATION_DIR)

        response.setContent({"success": True})

    except Exception as ex:
        handleException(response, ex, __file__ , "dataManagementUploadFile")
    finally:
        if(daoInstance != None):
            daoInstance.closeConnection()
        return response

def dataManagementGetMyFiles(request, response, DESTINATION_DIR, MAX_CLIENT_SPACE, isReference=False):
    #VARIABLE DECLARATION
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        # isValidUser lets the anonymous "nologin" case through, so userID can
        # still be None here. `DESTINATION_DIR += userID` then raised
        #     TypeError: can only concatenate str (not "NoneType") to str
        # rather than the "Log in required" answer the sibling handlers in this
        # file already give (dm_get_myjobs, dm_delete_job). Only the personal
        # listing needs an account; the reference/GTF listing is public and
        # substitutes its own userID just below.
        if not isReference and userID is None:
            response.setContent({"success": False,
                                 "errorMessage": "Log in required</br>Sorry but the feature you are requesting is only available to registered accounts."})
            return response

        if not isReference:
            DESTINATION_DIR += userID
        else:
            userID="-1"
            DESTINATION_DIR += "GTF/"

        #****************************************************************
        # Step 1.GET THE LIST OF FILES
        #****************************************************************
        logging.info("STEP1 - GET MY FILE LIST REQUEST RECEIVED")
        daoInstance = FileDAO()
        matchedFiles = daoInstance.findAll(otherParams={"userID":userID})
        logging.info("STEP1 - GET MY FILE LIST REQUEST RECEIVED...DONE")

        #****************************************************************
        # Step 2.CALCULATE USED SPACE
        #****************************************************************
        logging.info("STEP2 - GET THE CURRENT USED SPACE...")
        dataSummary = {"usedSpace" : dir_total_size(DESTINATION_DIR), "availableSpace": MAX_CLIENT_SPACE}
        logging.info("STEP2 - GET THE CURRENT USED SPACE...DONE")

        response.setContent({"success": True, "fileList" : matchedFiles, "dataSummary" : dataSummary })

    except Exception as ex:
        handleException(response, ex, __file__ , "dataManagementGetMyFiles")
    finally:
        if(daoInstance != None):
            daoInstance.closeConnection()
        return response

def dataManagementDeleteFile(request, response, DESTINATION_DIR, MAX_CLIENT_SPACE, isReference=False, fileName=None):
    #VARIABLE DECLARATION
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        userName = request.cookies.get('userName')
        sessionToken  = request.cookies.get('sessionToken')

        if (userID is None):
            response.setContent({"success": False,
                             "errorMessage": "Log in required</br>Sorry but the feature you are requesting is only available to registered accounts."})
        else:
            UserSessionManager().isValidUser(userID, sessionToken)

            #ONLY ADMIN USER (id=0) CAN UPLOAD NEW INBUILT GTF FILES
            if(isReference and UserSessionManager().isValidAdminUser(userID, userName, sessionToken)):
                userID="-1"

            if not isReference:
                DESTINATION_DIR += userID + "/inputData/"
            else:
                userID="-1"
                DESTINATION_DIR += "GTF/"

            #****************************************************************
            # Step 1. GET THE LIST OF JOB IDs
            #****************************************************************
            if fileName == None:
                fileName = request.form.get("fileName")
            files = fileName.split(",")

            #****************************************************************
            # Step 2. DELETE EACH FILE
            #****************************************************************
            daoInstance = FileDAO()

            for fileName in files:
                #****************************************************************
                # Step 2.1.DELETE THE GIVEN FILE FROM DATABASE
                #****************************************************************
                logging.info("STEP1 - REMOVING " + fileName + " FROM DATABASE...")
                daoInstance.remove(fileName, otherParams={"userID":userID})
                logging.info("STEP1 - REMOVING " + fileName + " FROM DATABASE...DONE")

                #****************************************************************
                # Step 2.2.DELETE THE GIVEN FILE FROM DIRECTORY
                #****************************************************************
                logging.info("STEP2 - REMOVING " + fileName + " FROM USER DIRECTORY...")
                # The name comes straight from the request, so it cannot be
                # concatenated onto a directory and handed to os.remove: a
                # "../" in it walked out of the user's own directory and this
                # became an arbitrary file delete. resolveWithin resolves the
                # path (collapsing "..", following symlinks) and refuses
                # anything that does not land under DESTINATION_DIR.
                targetPath = resolveWithin(DESTINATION_DIR, fileName)
                if targetPath is None:
                    logging.warning("STEP2 - REFUSED " + fileName +
                                    ": resolves outside the user directory")
                elif os.path.isfile(targetPath):
                    os.remove(targetPath)
                    logging.info("STEP2 - REMOVING " + fileName + " FROM USER DIRECTORY...DONE")
                else:
                    logging.info("STEP2 - REMOVING " + fileName + " FROM USER DIRECTORY...FILE NOT FOUND")

            response.setContent({"success": True })

    except Exception as ex:
        handleException(response, ex, __file__ , "dataManagementDeleteFile")
    finally:
        if(daoInstance != None):
            daoInstance.closeConnection()
        return response

def dataManagementGetMyJobs(request, response):
    #VARIABLE DECLARATION
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        if (userID is None):
            response.setContent({"success": False,
                             "errorMessage": "Log in required</br>Sorry but the feature you are requesting is only available to registered accounts."})
        else:
            #****************************************************************
            # Step 2.GET THE LIST OF JOBS FOR GIVEN USER
            #****************************************************************
            logging.info("STEP1 - GET MY JOB LIST REQUEST RECEIVED")
            daoInstance = JobDAO()
            matchedFiles = daoInstance.findAll(otherParams={"userID":userID})
            logging.info("STEP1 - GET MY JOB LIST REQUEST RECEIVED...DONE")

            response.setContent({"success": True, "jobList" : matchedFiles})

    except Exception as ex:
        handleException(response, ex, __file__ , "dataManagementGetMyJobs")
    finally:
        if(daoInstance != None):
            daoInstance.closeConnection()
        return response

def dataManagementDeleteJob(request, response):
    #VARIABLE DECLARATION
    daoInstance = None
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        #****************************************************************
        # Step 1. GET THE LIST OF JOB IDs
        #****************************************************************.
        jobID = request.form.get("jobID")
        jobs = jobID.split(",")

        #****************************************************************
        # Step 2. DELETE EACH JOB
        #****************************************************************.
        daoInstance = JobDAO()
        userDirID = userID if userID is not None else "nologin"
        userDir = CLIENT_TMP_DIR + userDirID + "/jobsData/"
        tmpDir = CLIENT_TMP_DIR + userDirID + "/tmp/"

        for jobID in jobs:
            #****************************************************************
            # Step 2a. DELETE GIVEN JOB FROM DATABASE
            #****************************************************************
            logging.info("STEP1 - REMOVING " + jobID + " FROM DATABASE...")
            daoInstance.remove(jobID, otherParams={"userID":userID})
            logging.info("STEP1 - REMOVING " + jobID + " FROM DATABASE...DONE")

            #****************************************************************
            # Step 2b. DELETE GIVEN JOB FROM USER DIRECTORY
            #****************************************************************
            # jobID is request input and rmtree recurses, so this cannot be a
            # concatenation: os.path.isdir is not a containment check, it is
            # satisfied by any directory the path reaches -- including one
            # reached by walking upwards. A jobID of ".." removes every job the
            # user has; a longer walk reaches other users. resolveWithin
            # refuses anything that does not land under the base.
            jobDir = resolveWithin(userDir, jobID)
            logging.info("STEP2 - REMOVING " + userDir + jobID + " FROM USER DIRECTORY...")
            if jobDir is None:
                logging.warning("STEP2 - REFUSED jobID " + jobID +
                                ": resolves outside " + userDir)
            elif os.path.isdir(jobDir):
                shutil.rmtree(jobDir)
                logging.info("STEP2 - REMOVING " + userDir + jobID + " FROM USER DIRECTORY...DONE")
            else:
                logging.info("STEP2 - REMOVING " + userDir + jobID + " FROM USER DIRECTORY...FILE NOT FOUND")

            tmpJobDir = resolveWithin(tmpDir, jobID)
            logging.info("STEP2 - REMOVING TEMPORAL DIR " + tmpDir + jobID + " FROM USER DIRECTORY...")
            if tmpJobDir is None:
                logging.warning("STEP2 - REFUSED jobID " + jobID +
                                ": resolves outside " + tmpDir)
            elif os.path.isdir(tmpJobDir):
                shutil.rmtree(tmpJobDir)
                logging.info("STEP2 - REMOVING TEMPORAL DIR " + tmpDir + jobID + " FROM USER DIRECTORY...")
            else:
                logging.info("STEP2 - REMOVING TEMPORAL DIR " + tmpDir + jobID + " FROM USER DIRECTORY...FILE NOT FOUND")

        response.setContent({"success": True })

    except Exception as ex:
        handleException(response, ex,__file__ , "dataManagementDeleteJob")
    finally:
        if(daoInstance != None):
            daoInstance.closeConnection()
        return response

def dataManagementDownloadFile(request, response):
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        #****************************************************************
        # Step 1.READ PARAMS
        #****************************************************************
        fileName = request.args.get("fileName", "")
        fileType =request.args.get("fileType", "")
        jobID =request.args.get("jobID", "")
        serve =(request.args.get("serve", "").lower() == "true")
        offset =int(request.args.get("offset", 0))

        #****************************************************************
        # Step 2.GENERATE THE PATH TO FILE
        #****************************************************************
        logging.info("STEP1 - GET FILE REQUEST RECEIVED")

        userDirID = userID if userID is not None else "nologin"

        if fileType=="job_result":
            userDir =  "/jobsData/" + jobID + "/output/"
        elif fileType=="input":
            userDir =  "/inputData/"
        else:
            userDir =  "/tmp/" + jobID

        userDir = CLIENT_TMP_DIR + userDirID + userDir

        # Same reason as the delete route: fileName is request input, and the
        # streaming branch below opens this path itself rather than going
        # through send_from_directory, so it does not inherit Flask's own
        # traversal check. Without this the handler was safe when it served an
        # attachment and an arbitrary file read when it streamed.
        file_path = resolveWithin(userDir, fileName)

        if file_path is not None and os.path.isfile(file_path):
            #IF THE REQUEST WANTS THE FILE IN A STREAM
            if serve == True:
                #TODO: HACER ESTO<- http://flask.pocoo.org/docs/0.10/patterns/streaming/
                def generate():
                    with open(file_path) as f:
                        lines = f.readlines()
                        first = min(len(lines), offset)
                        last = min(len(lines), offset + 51)
                        lines = lines[first:last]
                        for row in lines:
                            yield row.rstrip() + "\n"
                    f.close()

                from flask import Response
                return Response(generate(), mimetype='text/plain')
            else:
                # download_name, not attachment_filename: Werkzeug 2.2 renamed
                # it and removed the old spelling, so the previous call raised
                # TypeError on every download.
                return send_from_directory(userDir, fileName, as_attachment=True, download_name=fileName)
        else:
            response.setContent({"success": False, "errorMessage": "File not found.</br>Sorry but it looks like the requested file was removed from system."})
            return response
    except Exception as ex:
        handleException(response, ex, __file__ , "dataManagementDownloadFile")
        return response

#****************************************************************
# FILES MANIPULATION
#****************************************************************
def saveFile(userID, uploadedFileName, options, uploadedFile, DESTINATION_DIR):
    # An empty name would make file_path the directory itself, and the save
    # below then open()s a directory (IsADirectoryError [Errno 21] with a
    # trailing '//' in the path). Callers are expected to have skipped empty
    # attachments already; this is the last line of defence.
    if uploadedFileName is None or str(uploadedFileName).strip() == "":
        raise UserWarning(
            "Malformed submission: a file was attached without a filename, "
            "so it cannot be saved.")

    #1. CREATE THE USER DATA DIRECTORY IF NOT EXISTS
    if(not os.path.isdir(DESTINATION_DIR)):
        os.makedirs(DESTINATION_DIR)

    # Make sure to replace invalid chars to avoid problems
    uploadedFileName = ''.join('_' if ch in [':', '!', '[', ']', ';'] else ch for ch in uploadedFileName)

    #TODO: CHECK IF ENOUGH SPACE
    #SAVE THE FILE TO USER's DIRECTORY
    file_path = "{path}/{file}".format(path=DESTINATION_DIR, file=uploadedFileName)

    #CHECK IF FILENAME ALREADY EXISTS -> IF SO, ADD SUBFIX
    fileExtension=uploadedFileName.rsplit(".")
    originalName = fileExtension[0]
    if(len(fileExtension)>1):
        fileExtension= "." + fileExtension[1]
    else:
        fileExtension= ""

    iteration = 1
    while(os.path.isfile(file_path)):
        uploadedFileName = originalName + str(iteration) + fileExtension
        file_path = "{path}/{file}".format(path=DESTINATION_DIR, file=uploadedFileName)
        iteration=iteration+1

    logging.info("\tSAVING " + uploadedFile.filename + " AS " + uploadedFileName + "...")
    uploadedFile.save(file_path)
    logging.info("\tSAVING " + uploadedFile.filename + " AS " + uploadedFileName + "...DONE")
    #REGISTER FILE IN DATABASE
    registerFile(userID, uploadedFileName, options, DESTINATION_DIR)

    return uploadedFileName

def copyFile(userID, fileName, options, origin, destination):
    file_path = "{path}/{file}".format(path=destination, file=fileName)

    #CHECK IF FILENAME ALREADY EXISTS -> IF SO, ADD SUBFIX
    fileExtension=fileName.rsplit(".")
    originalName = fileExtension[0]
    if(len(fileExtension)>1):
        fileExtension= "." + fileExtension[1]
    else:
        fileExtension=""

    iteration = 1
    while(os.path.isfile(file_path)):
        fileName = originalName + str(iteration) + fileExtension
        file_path = "{path}/{file}".format(path=destination, file=fileName)
        iteration=iteration+1

    logging.info("\tCOPYING " + originalName + fileExtension + " AS " + fileName + "...")
    shutil.copy(origin + originalName + fileExtension, destination + fileName)
    logging.info("\tCOPYING " + originalName + fileExtension + " AS " + fileName + "...DONE")

    #REGISTER FILE IN DATABASE
    registerFile(userID, fileName, options, destination)
    return fileName

def registerFile(userID, fileName, options, location):
    # Do not register the file in the database
    if (str(userID) == 'None'):
        return None

    logging.info("\tREGISTERING " + fileName + " INTO DATABASE...")
    fileInstance = File("")
    fileInstance.setFileName(fileName)
    fileInstance.setDataType(options.get("dataType"))
    fileInstance.setOmicType(options.get("omicType"))
    fileInstance.setDescription(options.get("description", ""))

    options.pop("dataType", None)
    options.pop("omicType", None)
    options.pop("description", None)

    if bool(options): #NOT EMPTY
        fileInstance.otherFields = options

    file_path = "{path}/{file}".format(path=location, file=fileName)
    fileInstance.setSize(os.stat(file_path).st_size)

    import time
    fileInstance.setSubmissionDate(time.strftime("%d/%m/%Y %H:%M"))
    # Closed in a finally: DBmanager builds a new MongoClient per DAO, each
    # with its own monitor threads, and a write that raises would otherwise
    # leave them behind. Uploads run this once per file, so a batch upload
    # against a struggling database leaked one client per file.
    daoInstance = FileDAO()
    try:
        daoInstance.remove(fileName, otherParams={"userID": userID})
        daoInstance.insert(fileInstance, otherParams={"userID":userID})
        logging.info("\tREGISTERING " + fileName + " INTO DATABASE...DONE")
    finally:
        daoInstance.closeConnection()

    return fileName

def dir_total_size(source):
    total_size = 0
    for item in os.listdir(source):
        itempath = os.path.join(source, item)
        if os.path.isfile(itempath):
            total_size += os.path.getsize(itempath)
        elif os.path.isdir(itempath):
            #TODO:ignore tmp dir
            total_size += dir_total_size(itempath)
    return total_size

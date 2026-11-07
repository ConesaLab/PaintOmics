#***************************************************************
#  This file is part of PaintOmics 3
#
#  PaintOmics 3 is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  PaintOmics 3 is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with PaintOmics 3.  If not, see <http://www.gnu.org/licenses/>.
#  Contributors:
#     Rafael Hernandez de Diego <paintomics4@outlook.com>
#     Ana Conesa Cegarra
#     and others
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomics4@outlook.com
#
#**************************************************************

import logging
from html import escape
from threading import RLock as threading_lock
from src.common.Util import Singleton
from collections import deque

from src.conf.serverconf import JOB_CACHE_MAX_SIZE

from src.common.DAO.PathwayAcquisitionJobDAO import PathwayAcquisitionJobDAO
from src.common.DAO.Bed2GeneJobDAO import Bed2GeneJobDAO
from src.common.DAO.MiRNA2GeneJobDAO import MiRNA2GeneJobDAO
from src.common.DAO.MOREJobDAO import MOREJobDAO
from src.common.DAO.FeatureDAO import FeatureDAO
from src.common.DAO.FoundFeatureDAO import FoundFeatureDAO
from src.common.DAO.PathwayDAO import PathwayDAO
from src.common.DAO.VisualOptionsDAO import VisualOptionsDAO

from src.servlets.DataManagementServlet import saveFile

class JobInformationManager(metaclass=Singleton):

    def __init__(self):
        logging.info("CREATING NEW INSTANCE FOR JobInformationManager...")
        self.recentJobs= deque([])
        self.lock = threading_lock()
        # Separate from self.lock on purpose: loadJobInstance holds this across
        # a MongoDB read, and the cache's own operations must not wait on it.
        self.jobLoadLock = threading_lock()

    #**********************************************
    #*
    #**********************************************
    def storeJobInstance(self, jobInstance, stepNumber):
        daoInstance = None
        logging.info("STORING JOB "  + jobInstance.getJobID() + "...")

        try :
            if type(jobInstance).__name__ == "Bed2GeneJob":
                #SAVE THE WHOLE JOB INSTANCE
                logging.info("SAVING Bed2GeneJob "  + jobInstance.getJobID() + " TO DATABASE...")
                daoInstance = Bed2GeneJobDAO()
                daoInstance.insert(jobInstance)

            elif type(jobInstance).__name__ == "MiRNA2GeneJob":
                #SAVE THE WHOLE JOB INSTANCE
                logging.info("SAVING MiRNA2GeneJob "  + jobInstance.getJobID() + " TO DATABASE...")
                daoInstance = MiRNA2GeneJobDAO()
                daoInstance.insert(jobInstance)

            elif type(jobInstance).__name__ == "MOREJob":
                logging.info("SAVING MOREJob "  + jobInstance.getJobID() + " TO DATABASE...")
                daoInstance = MOREJobDAO()
                daoInstance.insert(jobInstance)

            elif type(jobInstance).__name__ == "PathwayAcquisitionJob":
                #IF JOB WAS NOT IN CACHE
                self.cacheJobInstance(jobInstance)

                #NOW SAVE JOB IN DATABASE
                if stepNumber == 1:
                    #SAVE THE WHOLE JOB INSTANCE
                    logging.info("SAVING PathwayAcquisitionJob "  + jobInstance.getJobID() + " TO DATABASE... IS STEP 1")
                    logging.info("SAVING PathwayAcquisitionJob "  + jobInstance.getJobID() + " TO DATABASE...")
                    daoInstance = PathwayAcquisitionJobDAO()
                    daoInstance.insert(jobInstance)
                elif stepNumber == 2:
                    #SAVE ONLY CHANGED WHOLE JOB INSTANCE
                    logging.info("UPDATING PathwayAcquisitionJob "  + jobInstance.getJobID() + " TO DATABASE... IS STEP 2")
                    daoInstance = PathwayAcquisitionJobDAO()
                    logging.info("UPDATING JOB INSTANCE...")
                    # hubAnalysisResult is here for the same reason the Reactome
                    # classes below are: it is computed at step 2, returned in
                    # that step's response, and was then dropped, because it
                    # appeared in neither this list nor any DAO call. So the
                    # Metabolites Hub Analysis table had rows only in the
                    # session that ran the analysis and was empty for anyone who
                    # reopened the job by its URL -- discarding an R script's
                    # output that the user cannot regenerate without re-running
                    # step 2.
                    daoInstance.update(jobInstance, {"fieldList": ["summary", "lastStep",
                         "mappingComp", "classificationDict", "pValueInDict",
                         "adjustPvalue", "totalRelevantFeaturesInCategory",
                         "featureSummary", "classificationMeta", "classActivity",
                         "aiConsent", "experimentDesign",
                         "regulationPerConditionData", "hubAnalysisResult",
                         "exprssionMetabolites"]})
                    daoInstance = FoundFeatureDAO()
                    logging.info("REMOVING MATCHED METABOLITES FROM DATABASE...")
                    daoInstance.removeAll({"jobID": jobInstance.getJobID()})
                    daoInstance = FeatureDAO()
                    logging.info("REMOVING COMPOUNDS TO DATABASE...")
                    daoInstance.removeAll({"featureType":"Compound","jobID":jobInstance.getJobID()})
                    logging.info("SAVING COMPOUNDS TO DATABASE...")
                    daoInstance.insertAll(jobInstance.getInputCompoundsData().values(), {"jobID": jobInstance.getJobID()})
                    daoInstance = PathwayDAO(dbManager=daoInstance.getDBManager())
                    logging.info("REMOVING PATHWAYS TO DATABASE...")
                    daoInstance.removeAll({"jobID":jobInstance.getJobID()})
                    logging.info("SAVING PATHWAYS TO DATABASE...")
                    daoInstance.insertAll(jobInstance.getMatchedPathways().values(), {"jobID":jobInstance.getJobID()})
                    # Reactome class enrichment was computed at step 2, returned
                    # in the response and then dropped -- it was in neither the
                    # updated field list above nor any DAO call. So the
                    # "Reactome Class pValue" column was populated only in the
                    # session that ran the analysis and showed "-" for every row
                    # once the job was reopened by its URL.
                    #
                    # Classes are Pathway instances, so they go to the same DAO
                    # and are tagged to be told apart on load. Documents written
                    # before this change carry no tag and load as pathways,
                    # exactly as they do today.
                    matchedClasses = list(jobInstance.getMatchedClass().values())
                    if matchedClasses:
                        logging.info("SAVING REACTOME CLASSES TO DATABASE...")
                        for matchedClassInstance in matchedClasses:
                            matchedClassInstance.isReactomeClass = True
                        daoInstance.insertAll(matchedClasses, {"jobID": jobInstance.getJobID()})
            else:
                raise NotImplementedError
            return True
        except Exception:
            raise
        finally:
            if daoInstance is not None:
                daoInstance.closeConnection()

    def loadJobInstance(self, jobID):
        """
        This function...

        @param {type}
        @return {type}
        """
        jobInstance = None
        jobInstanceDAO = None
        try :
            # Update access time
            self.touchAccessDate(jobID)

            jobInstance = self.findInCache(jobID)
            if jobInstance is None:
                # findInCache and addToCache each take the lock, but the load
                # between them did not, so concurrent requests for one uncached
                # job all missed, all read MongoDB, and all cached their own
                # copy. Measured at five concurrent requests: five reads, five
                # distinct objects, five entries in a cache bounded at 50.
                #
                # A PathwayAcquisitionJob is mutable and gets written back, so
                # separate copies are not just wasted work -- two requests that
                # each load, change something and store change *different*
                # objects, and whichever reaches the database last overwrites
                # the other. (The duplicate loads happen whenever two requests
                # race a cold entry; the lost update needs two concurrent
                # writes to the same job, which is rarer but reachable -- a save
                # of visual options beside a save of sharing options.)
                #
                # Double-checked: the fast path above is untouched, and only a
                # miss takes this lock and looks again inside it, so the second
                # caller finds what the first loaded. The lock is separate from
                # self.lock because it is held across a database read and the
                # cache's own operations must not queue behind that.
                self.jobLoadLock.acquire()
                try:
                    jobInstance = self.findInCache(jobID)
                    if jobInstance is None:
                        logging.info("JOB "  + jobID + " NOT FOUND IN CACHE, TRYING IN DB...")
                        jobInstanceDAO = PathwayAcquisitionJobDAO()
                        jobInstance = jobInstanceDAO.findByID(jobID)
                        if jobInstance is None:
                            logging.info("JOB "  + jobID + " NOT FOUND IN DATABASE...")
                        else:
                            logging.info("JOB "  + jobID + " FOUND IN DATABASE...")
                            self.addToCache(jobInstance)
                finally:
                    self.jobLoadLock.release()

            return jobInstance
        except Exception:
            raise
        finally:
            if jobInstanceDAO is not None:
                jobInstanceDAO.closeConnection()

    def cacheJobInstance(self, jobInstance):
        """Cache this instance unless the job is already there.

        The check and the insert used to be adjacent-but-separate calls in
        storeJobInstance, each locking on its own. The window between them is
        microseconds rather than a database read, and the instance cached is the
        one the caller already holds -- so racing stores duplicated *the same
        object* rather than producing divergent copies the way loadJobInstance
        did. Forced open, four concurrent stores left four entries that were all
        `is` each other.

        So the cost is cache slots, not correctness: one job can occupy several
        of the fifty and push other jobs out into fresh database reads. Made
        atomic because it is the same pattern as the rest of this class, not
        because work was being lost to it.
        """
        self.lock.acquire()
        try:
            for cached in list(self.recentJobs):
                if cached.getJobID() == jobInstance.getJobID():
                    return
            self.addToCache(jobInstance)
        finally:
            self.lock.release()

    def findInCache(self, jobID):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE
            logging.info( str(len(self.recentJobs)) + " JOBS IN CACHE...")

            for jobInstanceAux in list(self.recentJobs) :
                if jobInstanceAux.getJobID() == jobID:
                    logging.info("JOB "  + jobID + " IS IN CACHE...")
                    return jobInstanceAux
        except Exception:
            logging.info("JOB "  + jobID + " IS NOT IN CACHE...")
            return None
        finally:
                self.lock.release() #UNLOCK CACHE

    #**********************************************
    #*
    #**********************************************
    def addToCache(self, jobInstance):
        try:
            self.lock.acquire() #LOCK CACHE

            #IF CACHE WAS FULL, POP LAST ELEMENT
            if len( self.recentJobs ) == JOB_CACHE_MAX_SIZE:
                prevJobInstance = self.recentJobs.popleft()
                logging.info("PREVIOUS JOB "  + prevJobInstance.getJobID() + " REMOVED FROM CACHE...")
            self.recentJobs.append(jobInstance)
            logging.info("NEW JOB "  + jobInstance.getJobID() + " ADDED TO CACHE...")
        finally:
                self.lock.release() #UNLOCK CACHE

    #**********************************************
    #*
    #**********************************************
    @staticmethod
    def _requiredOrigin(formFields, fieldName, fileLabel):
        """Return an upload's `*_origin` form field, or say which one is missing.

        Every uploaded file is paired with an origin field naming where it came
        from ('client', 'mydata', an inbuilt GTF, or a reference to another
        omic's file). saveFiles logs that value before branching on it, so a
        submission that omitted the field failed on `"..." + None` with

            TypeError: can only concatenate str (not "NoneType") to str

        which names neither the field nor the file. The field names are derived
        from the file field ("omic0_file" -> "omic0_relevant_origin"), so an API
        client has no way to guess which one it missed from that message.
        """
        origin = formFields.get(fieldName)
        if origin is None:
            # fieldName is made from the name of an uploaded part, which the
            # request chooses; the dialogs insert this answer as HTML.
            raise UserWarning(
                "Malformed submission: the form field '" + escape(fieldName) +
                "' is missing, so PaintOmics cannot tell where the " + fileLabel +
                " was uploaded from.")
        return origin

    @staticmethod
    def _requiredFileLocation(formFields, fieldName, fileLabel):
        """Return an upload's `*_filelocation` form field, or say which one is missing.

        Same contract as _requiredOrigin: these fields are read whenever an
        origin says the file was previously uploaded ('mydata', inbuilt GTF, or
        a cross-omic reference). A missing field used to surface as
        AttributeError: 'NoneType' object has no attribute 'replace'.
        """
        location = formFields.get(fieldName)
        if location is None or str(location).strip() == "":
            raise UserWarning(
                "Malformed submission: the form field '" + escape(fieldName) +
                "' is missing or empty, so PaintOmics cannot locate the " +
                fileLabel + " it refers to.")
        return str(location)

    @staticmethod
    def _fileByReference(formFields, uploadedFileName, suffix):
        """Whether a secondary file arrives by reference instead of as an upload.

        "Use a file from My Data" and every chained conversion (regions,
        miRNA, MORE) submit a secondary file as `<omic>_<suffix>_filelocation`
        plus a non-client `<omic>_<suffix>_origin`, with the matching
        `<input type=file>` part legitimately empty. Such a file is present
        and must be registered; judging presence by the upload part alone
        silently dropped it (measured: the region example's relevant-features
        list, and with it every significant pathway in the job).
        """
        origin = formFields.get(uploadedFileName.replace("file", suffix) + "_origin")
        if origin in (None, "", "client"):
            return False
        location = formFields.get(
            uploadedFileName.replace("file", suffix + "_filelocation"))
        return location is not None and str(location).strip() != ""

    def saveFiles(self, uploadedFiles, formFields, userID, jobInstance, CLIENT_TMP_DIR, EXAMPLE_FILES_DIR=""):
        uploadedDataFile = None
        uploadedRelevantFile = None
        dataFileName = ""
        relevantFileName = ""
        associationsFileName = ""
        relevantAssociationsFileName = ""
        origin = None
        fields = None
        omicType = ""
        dataType = ""
        userDirID = userID if userID is not None else "nologin"
        CLIENT_TMP_DIR = CLIENT_TMP_DIR + userDirID + "/inputData/"
        savedFiles = {}

        for uploadedFileName in uploadedFiles.keys():
            #IF THE FILE IS NOT A RELEVANT FEATURES FILE
            fields = {}
            if (uploadedFileName is not None and uploadedFileName.find("_relevant") == -1  and uploadedFileName.find("_annotations_file") == -1   and uploadedFileName.find("_associations_file") == -1 and uploadedFileName.find("_design_file") == -1):
                ##GET THE MATCHING TYPE: GENE OR COMPOUND
                # Default matching type: gene
                matchingType = formFields.get(uploadedFileName.replace("file","match_type"), "gene")
                omicType = formFields.get(uploadedFileName.replace("file","omic_name"))  ##GET THE OMIC NAME: "Gene Expression", "Metabolomics", "Proteomics", .... (or user name)
                if omicType is None:
                    # Concatenated into paths and log lines below; None used to
                    # surface as an opaque TypeError instead of naming the field.
                    # The part's name is the request's own, and the answer is
                    # inserted as HTML.
                    raise UserWarning(
                        "Malformed submission: the form field '" +
                        escape(uploadedFileName.replace("file", "omic_name")) +
                        "' is missing, so PaintOmics cannot tell which omic the "
                        "file '" + escape(uploadedFileName) + "' belongs to.")

                fields["omicType"] = omicType
                fields["dataType"] =  formFields.get(uploadedFileName.replace("file","file_type")) ##GET THE FILE TYPE: GENE EXPRESSION, ETC.
                fields["description"] =  formFields.get(uploadedFileName.replace("file","description"), "") ##GET THE FILE DESCRIPTION

            #IF IS ANNOTATION FILE
            elif (uploadedFileName is not None and uploadedFileName.find("_annotations_file") != -1):
                matchingType = "reference_file" ##GET THE MATCHING TYPE: GENE OR COMPOUND
                omicType =  "Reference file"
                dataType = formFields.get(uploadedFileName.replace("file","file_type"))

                fields["omicType"] = omicType
                fields["dataType"] = dataType ##GET THE FILE TYPE: GENE EXPRESSION, ETC.
                fields["description"] =  formFields.get(uploadedFileName.replace("file","description"), "") ##GET THE FILE DESCRIPTION
            else:
                continue

            #GET THE FILE OBJECTS
            uploadedDataFile = uploadedFiles.get(uploadedFileName)
            uploadedRelevantFile = uploadedFiles.get(uploadedFileName.replace("file", "relevant_file"), None)

            uploadedAssociationDataFile = uploadedFiles.get(uploadedFileName.replace("file", "associations_file"), None)

            uploadedAssociationRelevantFile = uploadedFiles.get(uploadedFileName.replace("file", "relevant_associations_file"), None)
            # Experimental design (column -> condition) for a values file with
            # one column per sample. Optional; read by the job at step 1.
            uploadedDesignFile = uploadedFiles.get(uploadedFileName.replace("file", "design_file"), None)

            configValues = formFields.get(uploadedFileName.replace("file", "config_args"), None)
            enrichment = formFields.get(uploadedFileName.replace("file", "enrichment"), 'genes')

            origin = self._requiredOrigin(
                formFields, uploadedFileName.replace("file", "origin"), "data file")
            logging.info("SAVE FILES - ORIGIN FOR " + uploadedFileName + " IS " + origin)

            #GET THE ORIGIN OF THE FILE. IF CLIENT -> SAVE THE FILE
            if(origin == 'client'):
                #THE NAME WHEN SAVING THE DATA
                dataFileName = uploadedDataFile.filename
                dataOmicId = uploadedDataFile.name

                # If the file was previously saved, retrieve the final name.
                if dataOmicId  in savedFiles:
                    dataFileName = savedFiles.get(dataOmicId)
                else:
                    #IF NO FILE WAS PROVIDED, IGNORE
                    if(dataFileName == ''):
                       logging.info("\tIGNORING " + omicType + ", EMPTY FILE OR NOT PROVIDED")
                       continue
                    else:
                        ##ELSE, SAVE THE FILE, GET THE NEW NAME IS ALREADY EXISTS
                        if( fields["description"] == ""):
                             fields["description"] = "File uploaded through the submission form."

                        # If no user is provided, prepend the jobID to avoid possible conflictions
                        if userID is None:
                            dataFileName = jobInstance.getJobID() + '_' + dataFileName
                        savedFiles[dataOmicId] = dataFileName = saveFile(userID, dataFileName, fields, uploadedDataFile, CLIENT_TMP_DIR)

            elif(origin == 'mydata'):
                dataFileName = self._requiredFileLocation(
                    formFields, uploadedFileName.replace("file","filelocation"),
                    "data file").replace("[MyData]/","")
                logging.info("SAVE FILES  - USING ALREADY SUBMITTED FILE (data file) %s FOR %s", dataFileName, omicType)
            elif(origin == 'inbuilt_gtf'):
                dataFileName = EXAMPLE_FILES_DIR + "GTF/" + self._requiredFileLocation(
                    formFields, uploadedFileName.replace("file","filelocation"),
                    "inbuilt GTF file").replace("[inbuilt GTF files]/","")
                logging.info("SAVE FILES  - USING ALREADY INBUILT GTF FILE %s FOR %s", dataFileName, omicType)
            elif('filelocation' in origin):
                # The omic references the file of another omic
                originName = origin.split('_', 1)[0]
                omicOrigin = formFields.get(originName + "_origin")
                dataOmicId = originName + "_file"

                # If the file is a reference to the file of another omic, keep the reference to that file.
                # However, as the final filename might be unique, we should save it first and keep a reference
                # so as to not re-save it again on later loop iterations.
                if(omicOrigin == 'client'):
                    uploadedDataFile = uploadedFiles.get(originName + "_file")

                    dataFileName = uploadedDataFile.filename

                    # If the file was previously saved, retrieve the final name.
                    # Keyed by the form field name, exactly as it was stored:
                    # looking it up by the FileStorage object always returned
                    # None and registered the omic with inputDataFile: None.
                    if dataOmicId in savedFiles:
                        dataFileName = savedFiles.get(dataOmicId)
                    else:
                        if (dataFileName == ''):
                            logging.info("\tIGNORING " + omicType + ", EMPTY FILE OR NOT PROVIDED")
                            continue
                        else:
                            ##ELSE, SAVE THE FILE, GET THE NEW NAME IF ALREADY EXISTS
                            if (fields["description"] == ""):
                                fields["description"] = "File referenced through the submission form (" + omicType + ")"

                            # If no user is provided, prepend the jobID to avoid possible conflictions
                            if userID is None:
                                dataFileName = jobInstance.getJobID() + '_' + dataFileName

                            savedFiles[dataOmicId] = dataFileName = saveFile(userID, dataFileName, fields, uploadedDataFile, CLIENT_TMP_DIR)

                else:
                    dataFileName = self._requiredFileLocation(
                        formFields, originName + "_filelocation",
                        "referenced data file").replace("[MyData]/", "")
            else:
                logging.info("\tIGNORING " + omicType + ", EMPTY FILE OR NOT PROVIDED")
                continue

            # TODO: move this to a loop? They are the same but changing file properties
            #SAVE THE ASSOCIATED RELEVANT FEATURED FILE (IF ANY)
            # An untouched <input type=file> still submits a part with
            # filename="" — werkzeug classifies it as a file. Saving that
            # empty name built '<inputData>//', which open()ed the directory
            # itself (IsADirectoryError). Same guard as the data-file branch:
            # empty means absent.
            #
            # Absent as an UPLOAD is not absent as a FILE, though: a secondary
            # file can also arrive by reference ("_filelocation" + a non-client
            # "_origin"), with the file part legitimately empty — that is what
            # "Use a file from My Data" posts, and what every chained
            # conversion (regions, miRNA) posts for its own outputs. Gating on
            # the part alone dropped those silently: the region example
            # registered relevantFeaturesFile: None and reported 829 pathways
            # with 0 significant on data with a planted signal.
            if (uploadedRelevantFile is not None and (uploadedRelevantFile.filename or "") != "") \
                    or self._fileByReference(formFields, uploadedFileName, "relevant"):
                relevantFileName = uploadedRelevantFile.filename if uploadedRelevantFile is not None else ""
                origin = self._requiredOrigin(
                    formFields, uploadedFileName.replace("file", "relevant") + "_origin",
                    "relevant features file") ##GET THE ORIGIN OF THE FILE. IF CLIENT -> SAVE THE FILE

                fieldsRelevant={"omicType": omicType}
                fieldsRelevant["dataType"]= formFields.get(uploadedFileName.replace("file","relevant_file_type")) ##GET THE FILE TYPE: GENE EXPRESSION, ETC.
                fieldsRelevant["description"] =  formFields.get(uploadedFileName.replace("file","description"), "Uploaded using the submission form.")##GET THE FILE DESCRIPTION

                logging.info("STEP1 - ORIGIN FOR %s IS %s", uploadedFileName.replace("file","relevant"), origin)
                if(origin == 'client'):
                    #TODO: GENERATE AUTOMATICALLY THE DATA TYPE (Gene exp, Gene list, etc.) AND THE DESCRIPTION
                    ##SAVE THE FILE, GET THE NEW NAME IF ALREADY EXISTS
                    # If no user is provided, prepend the jobID to avoid possible conflictions
                    if userID is None:
                        relevantFileName = jobInstance.getJobID() + '_' + relevantFileName

                    relevantFileName = saveFile(userID, relevantFileName, fieldsRelevant, uploadedRelevantFile, CLIENT_TMP_DIR)
                else:
                    relevantFileName = self._requiredFileLocation(
                        formFields, uploadedFileName.replace("file","relevant_filelocation"),
                        "relevant features file").replace("[MyData]/","")
                    logging.info("STEP1 - USING ALREADY SUBMITTED FILE (relevant features file) %s FOR %s", relevantFileName, omicType)
            else:
                relevantFileName = None

            #SAVE THE ASSOCIATIONS FILE (IF ANY)
            # Same shape as the relevant-features branch above: an empty part
            # with a "_filelocation" reference is a present file, not an
            # absent one.
            if (uploadedAssociationDataFile is not None and (uploadedAssociationDataFile.filename or "") != "") \
                    or self._fileByReference(formFields, uploadedFileName, "associations"):
                associationsFileName = uploadedAssociationDataFile.filename if uploadedAssociationDataFile is not None else ""
                origin = self._requiredOrigin(
                    formFields, uploadedFileName.replace("file", "associations") + "_origin",
                    "associations file") ##GET THE ORIGIN OF THE FILE. IF CLIENT -> SAVE THE FILE

                fieldsAssociations={"omicType": omicType}
                fieldsAssociations["dataType"] = formFields.get(uploadedFileName.replace("file","associations_file_type")) ##GET THE FILE TYPE: GENE EXPRESSION, ETC.
                fieldsAssociations["description"] =  formFields.get(uploadedFileName.replace("file","description"), "Uploaded using the submission form.")##GET THE FILE DESCRIPTION

                logging.info("STEP1 - ORIGIN FOR " + uploadedFileName.replace("file","associations") + " IS " + origin)
                if(origin == 'client'):
                    #TODO: GENERATE AUTOMATICALLY THE DATA TYPE (Gene exp, Gene list, etc.) AND THE DESCRIPTION
                    ##SAVE THE FILE, GET THE NEW NAME IF ALREADY EXISTS
                    # If no user is provided, prepend the jobID to avoid possible conflictions
                    if userID is None:
                        associationsFileName = jobInstance.getJobID() + '_' + associationsFileName

                    associationsFileName = saveFile(userID, associationsFileName, fieldsAssociations, uploadedAssociationDataFile, CLIENT_TMP_DIR)
                else:
                    associationsFileName = self._requiredFileLocation(
                        formFields, uploadedFileName.replace("file","associations_filelocation"),
                        "associations file").replace("[MyData]/","")
                    logging.info("STEP1 - USING ALREADY SUBMITTED FILE (associations file) %s FOR %s", associationsFileName, omicType)

                # SAVE THE RELEVANT ASSOCIATIONS FILE (IF ANY)
                # TODO: currently only if the associations file is present
                if ((uploadedAssociationRelevantFile is not None and (uploadedAssociationRelevantFile.filename or "") != ""
                        and formFields.get(uploadedFileName.replace("file", "relevant_associations") + "_origin") is not None)
                        or self._fileByReference(formFields, uploadedFileName, "relevant_associations")):
                    relevantAssociationsFileName = uploadedAssociationRelevantFile.filename if uploadedAssociationRelevantFile is not None else ""
                    # The enclosing condition already established this field is
                    # present; going through the same helper as the other three
                    # keeps every origin lookup uniform rather than leaving one
                    # site whose safety depends on a guard two lines above.
                    origin = self._requiredOrigin(
                        formFields, uploadedFileName.replace("file", "relevant_associations") + "_origin",
                        "relevant associations file")  ##GET THE ORIGIN OF THE FILE. IF CLIENT -> SAVE THE FILE

                    fieldsRelevantAssociations = {"omicType": omicType}
                    fieldsRelevantAssociations["dataType"] = formFields.get(uploadedFileName.replace("file", "relevant_associations_file_type"))  ##GET THE FILE TYPE: GENE EXPRESSION, ETC.
                    fieldsRelevantAssociations["description"] = formFields.get(uploadedFileName.replace("file", "description"), "Uploaded using the submission form.")  ##GET THE FILE DESCRIPTION

                    logging.info("STEP1 - ORIGIN FOR " + uploadedFileName.replace("file", "relevant_associations") + " IS " + origin)
                    if (origin == 'client'):
                        # TODO: GENERATE AUTOMATICALLY THE DATA TYPE (Gene exp, Gene list, etc.) AND THE DESCRIPTION
                        ##SAVE THE FILE, GET THE NEW NAME IF ALREADY EXISTS
                        # If no user is provided, prepend the jobID to avoid possible conflictions
                        if userID is None:
                            relevantAssociationsFileName = jobInstance.getJobID() + '_' + relevantAssociationsFileName

                        relevantAssociationsFileName = saveFile(userID, relevantAssociationsFileName, fieldsRelevantAssociations, uploadedAssociationRelevantFile, CLIENT_TMP_DIR)
                    else:
                        relevantAssociationsFileName = self._requiredFileLocation(
                            formFields, uploadedFileName.replace("file", "relevant_associations_filelocation"),
                            "relevant associations file").replace("[MyData]/", "")
                        logging.info("STEP1 - USING ALREADY SUBMITTED FILE (relevant associations file) %s FOR %s", relevantAssociationsFileName, omicType)
            else:
                relevantAssociationsFileName = associationsFileName = None

            #SAVE THE EXPERIMENTAL DESIGN FILE (IF ANY). Same empty-part guard
            # as the relevant-features branch; a design can also come by
            # reference from My Data.
            designFileName = None
            if (uploadedDesignFile is not None and (uploadedDesignFile.filename or "") != "") \
                    or self._fileByReference(formFields, uploadedFileName, "design"):
                designOrigin = self._requiredOrigin(
                    formFields, uploadedFileName.replace("file", "design") + "_origin",
                    "experimental design file")
                fieldsDesign = {"omicType": omicType, "dataType": "Experimental design",
                                "description": formFields.get(uploadedFileName.replace("file", "description"), "Uploaded using the submission form.")}
                if designOrigin == 'client':
                    designFileName = uploadedDesignFile.filename
                    if userID is None:
                        designFileName = jobInstance.getJobID() + '_' + designFileName
                    designFileName = saveFile(userID, designFileName, fieldsDesign, uploadedDesignFile, CLIENT_TMP_DIR)
                else:
                    designFileName = self._requiredFileLocation(
                        formFields, uploadedFileName.replace("file", "design_filelocation"),
                        "experimental design file").replace("[MyData]/", "")
                logging.info("STEP1 - EXPERIMENTAL DESIGN FOR %s IS %s", omicType, designFileName)

            if(jobInstance != None):
                if(matchingType.lower() == "gene"):
                    jobInstance.addGeneBasedInputOmic({"omicName": omicType, "inputDataFile": dataFileName, "relevantFeaturesFile": relevantFileName, "associationsFile": associationsFileName, "relevantAssociationsFile": relevantAssociationsFileName, "configOptions": configValues, "enrichment": enrichment, "designFile": designFileName})
                elif(matchingType.lower() == "compound"):
                    jobInstance.addCompoundBasedInputOmic({"omicName": omicType, "inputDataFile": dataFileName, "relevantFeaturesFile": relevantFileName, "configOptions": configValues, "enrichment": enrichment, "designFile": designFileName})
                elif(matchingType.lower() == "reference_file"):
                    jobInstance.addReferenceInput({"omicName": omicType, "fileType": dataType, "inputDataFile": dataFileName})

    # Both of these close in a finally rather than after the call. That used to
    # be about leaked threads -- DBmanager built a new MongoClient per DAO, with
    # its own monitor threads, and a query that raised left them behind, exactly
    # when the database was already unreachable and every request was failing at
    # once. DBmanager now hands out one process-wide client, so the finally no
    # longer buys back threads; it stays because it is the shape the rule in
    # src/tests/test_dao_connection_cleanup.py enforces, it costs nothing, and a
    # DAO that outlives its request is the one way a manager can still carry a
    # client reference across a fork.
    def getVisualOptions(self, jobID):
        daoInstance = VisualOptionsDAO()
        try:
            return daoInstance.findByID(jobID)
        finally:
            daoInstance.closeConnection()

    def storeVisualOptions(self, jobID, visualOptionsInstance):
        daoInstance = VisualOptionsDAO()
        try:
            daoInstance.remove(jobID)
            return daoInstance.insert(visualOptionsInstance, {"jobID":jobID})
        finally:
            daoInstance.closeConnection()

    # The three below never released their DAO at all -- not on the error path,
    # not on the success path. touchAccessDate in particular runs on every
    # loadJobInstance, cache hit included, and the AI routes load a job several
    # times per report, so a single run left a handful of managers behind.
    def storeSharingOptions(self, jobInstance):
        daoInstance = PathwayAcquisitionJobDAO()
        try:
            daoInstance.update(jobInstance, {"fieldList": ["allowSharing", "readOnly"]})
        finally:
            daoInstance.closeConnection()

    def touchAccessDate(self, jobID):
        jobInstanceDAO = PathwayAcquisitionJobDAO()
        try:
            jobInstanceDAO.touch(jobID)
        finally:
            jobInstanceDAO.closeConnection()

    def storePathways(self, jobInstance):
        daoInstance = PathwayDAO()
        try:
            logging.info("STORE PATHWAYS - REMOVING PATHWAYS TO DATABASE...")
            daoInstance.removeAll({"jobID": jobInstance.getJobID()})
            logging.info("STORE PATHWAYS - SAVING PATHWAYS TO DATABASE...")
            daoInstance.insertAll(jobInstance.getMatchedPathways().values(), {"jobID": jobInstance.getJobID()})
        finally:
            daoInstance.closeConnection()
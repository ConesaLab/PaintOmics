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
#  Technical contact paintomics4@outlook.com
#**************************************************************
import logging
import logging.config

import cairosvg
from time import time

from collections import defaultdict

from src.common.ServerErrorManager import handleException
from src.common.UserSessionManager import UserSessionManager
from src.common.JobInformationManager import JobInformationManager
from src.common import JobProgress
from src.common import ExampleDatasets
from src.common import DatabaseAvailability
from src.common.Statistics import adjustPvalues, calculateStoufferCombinedPvalue
from src.common.DesignFile import parse_design
from src.common.DAO.PathwayAcquisitionJobDAO import PathwayAcquisitionJobDAO
from src.common.DAO.FeatureDAO import FeatureDAO
from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob
from src.classes import PathwayEvidence

from src.conf.serverconf import CLIENT_TMP_DIR


def loadRequestedJob(jobID, action):
    """Load the job a request names, or say exactly what was wrong with it.

    Every job endpoint reads a jobID off the request and then immediately
    either concatenates it into a log line or calls a method on the loaded
    instance. Neither tolerates a missing or unknown ID, so a malformed request
    produced one of

        TypeError: can only concatenate str (not "NoneType") to str
        AttributeError: 'NoneType' object has no attribute 'getReadOnly'

    naming neither the field nor the job. Those reach the browser as an opaque
    failure the client cannot render into anything a user can act on.

    @param {String} jobID, as read from the request (may be None)
    @param {String} action, named in the message so the user knows what failed
    @returns {Job} the loaded job instance
    """
    if not jobID:
        raise UserWarning("Missing jobID parameter for " + action + ".")

    jobInstance = JobInformationManager().loadJobInstance(jobID)

    if jobInstance is None:
        raise UserWarning("Job " + str(jobID) + " was not found at database.")

    return jobInstance


#************************************************************************
# WHAT AN INPUT OMIC MAY TELL THE BROWSER
#************************************************************************
# An entry in geneBasedInputOmics/compoundBasedInputOmics is the job's DB
# record as much as its in-memory state: Job.toBSON writes it out verbatim and
# Job reopens a job by reading `inputDataFile` back, so the file fields cannot
# be dropped from the entry itself. But every step-1, step-2 and recover-job
# response handed the whole entry to the client, and the browser stored it --
# measured in sessionStorage.jobModel:
#
#   inputDataFile: "/Users/.../PaintomicsServer/src/examplefiles/datasets/
#                   01-gene-single-condition/data/gene_expression_values.tab"
#
# an absolute server path, plus relevantFeaturesFile, associationsFile and
# relevantAssociationsFile beside it. So the projection happens here, at the
# response boundary, and never in toBSON.
#
# A whitelist, not a blacklist: the next field somebody adds for persistence
# stays server-side by default instead of shipping until it is noticed. Each
# entry below is a field the client is measurably reading.
CLIENT_VISIBLE_OMIC_FIELDS = (
    "omicName",            # every view keys on it
    "omicSummary",         # mapped/unmapped counts, JobModel.getMappingSummary
    "omicHeader",          # condition labels, JobModel.getOmicHeaders
    "replicateDetection",  # the Step-2 replicate card
    "replicateSource",     # which mapping is in force
    "replicateMapping",
    "sampleHeader",        # headers in "samples" mode
)


def inputOmicsForClient(inputOmics):
    """The client-visible projection of a list of input omics.

    Applied at every site that publishes geneBasedInputOmics or
    compoundBasedInputOmics; see CLIENT_VISIBLE_OMIC_FIELDS for why.
    """
    return [
        {field: omic[field] for field in CLIENT_VISIBLE_OMIC_FIELDS
         if field in omic}
        for omic in (inputOmics or [])
    ]


#************************************************************************
#     _____ _______ ______ _____    __
#    / ____|__   __|  ____|  __ \  /_ |
#   | (___    | |  | |__  | |__) |  | |
#    \___ \   | |  |  __| |  ___/   | |
#    ____) |  | |  | |____| |       | |
#   |_____/   |_|  |______|_|       |_|
#
def pathwayAcquisitionStep1_PART1(REQUEST, RESPONSE, QUEUE_INSTANCE, JOB_ID, EXAMPLE_FILES_DIR, exampleMode):
    """
    This function corresponds to FIRST PART of the FIRST step in the Pathways acquisition process.
    First, it takes a Request object which contains the fields of the form that started the process.
    This is a summarization for the steps in the process:
        Step 0. VARIABLE DECLARATION
        Step 1. CHECK IF VALID USER SESSION
        Step 2. CREATE THE NEW INSTANCE OF JOB
        Step 3. SAVE THE UPLOADED FILES
        Step 4. QUEUE THE JOB INSTANCE
        Step 5. RETURN THE NEW JOB ID
    @param {Request} REQUEST
    @param {Response} RESPONSE
    @param {RQ QUEUE} QUEUE_INSTANCE
    @param {String} JOB_ID
    @param {Boolean} exampleMode
    @returns Response
    """
    # TODO: ALLOWED_EXTENSIONS http://flask.pocoo.org/docs/0.10/patterns/fileuploads/
    # TODO: secure_filename
    #****************************************************************
    #Step 0. VARIABLE DECLARATION
    #The following variables are defined:
    #  - jobInstance: instance of the PathwayAcquisitionJob class.
    #                 Contains all the information for the current job.
    #  - userID: the ID for the user
    #****************************************************************
    jobInstance = None
    userID = None

    # Decoded before the job exists so an unrecognised mode is refused before
    # any directory is created for it.
    isExampleRequest, scenarioId = ExampleDatasets.scenarioIdFromMode(exampleMode)

    try :
        #****************************************************************
        # Step 1. CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = REQUEST.cookies.get('userID')
        sessionToken  = REQUEST.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        #****************************************************************
        # Step 2. CREATE THE NEW INSTANCE OF JOB
        #****************************************************************
        jobInstance = PathwayAcquisitionJob(JOB_ID, userID, CLIENT_TMP_DIR)
        jobInstance.initializeDirectories()
        logging.info("STEP1 - NEW JOB SUBMITTED " + jobInstance.getJobID())

        # Make sure to read the POST data always. UWSGI will fail if
        # not.
        uploadedFiles = REQUEST.files
        formFields = REQUEST.form

        #****************************************************************
        # Step 3. SAVE THE UPLOADED FILES
        #****************************************************************
        if isExampleRequest is False:
            logging.info("STEP1 - FILE UPLOADING REQUEST RECEIVED")
            jobInstance.description=""
            jobInstance.setName(formFields.get("jobDescription", "")[:100])
            specie = (formFields.get("specie") or "").strip() #GET THE SPECIES NAME
            if not specie:
                # Concatenated into the log below and stored on the job; a
                # missing field used to be an opaque TypeError, and defaulting
                # would silently run the job against the wrong database.
                raise UserWarning(
                    "Malformed submission: no organism was selected (the "
                    "'specie' field is missing), so PaintOmics cannot choose "
                    "a pathway database.")
            databases = REQUEST.form.getlist('databases[]')
            jobInstance.setOrganism(specie)
            # The submitted selection, filtered to what this server can actually
            # run for this organism. The rule used to be spelled out here as an
            # intersection with organismDB.py; it now lives in
            # DatabaseAvailability, which the form reads too, so a box the user
            # can tick is a box whose database reaches the job. KEGG is added
            # regardless, exactly as before.
            jobInstance.setDatabases(
                DatabaseAvailability.resolveDatabases(specie, databases))
            logging.info("STEP1 - SELECTED SPECIES IS " + specie)

            jobInstance.setAIConsent(formFields.get("aiConsent", "false"))
            jobInstance.setExperimentDesign(formFields.get("experimentDesign", ""))

            logging.info("STEP1 - READING FILES....")
            JobInformationManager().saveFiles(uploadedFiles, formFields, userID, jobInstance, CLIENT_TMP_DIR)
            logging.info("STEP1 - READING FILES....DONE")

            # A chained example arrives here, not in the branch below: the
            # Regulatory Omics step has already run and step 1 is an ordinary
            # upload of its output, which is what the guard in
            # step1OnFormSubmitHandler exists to keep true. The cost is that
            # the manifest's role="target" omic has no form field to travel in
            # -- measured as job 3Z1q20I1rC registering ['miRNA-seq'] alone and
            # returning 357 pathways, 0 significant. Put it back from the
            # manifest. A no-op for every real upload, and for the region and
            # MORE examples, which declare no target omic at all.
            reattached = ExampleDatasets.attachChainedExampleTargets(
                jobInstance, EXAMPLE_FILES_DIR)
            if reattached:
                logging.info("STEP1 - CHAINED EXAMPLE: RE-ATTACHED TARGET OMIC(S) %s",
                             ", ".join(reattached))

        elif isExampleRequest:
            #****************************************************************
            # Step 2.REGISTER THE BUNDLED EXAMPLE FILES
            #****************************************************************
            # Which files these are is decided by examplefiles/datasets/
            # manifest.json, not here. The previous version rebuilt each
            # filename by mangling the omic name ("DNase-seq" ->
            # "dnase_values.tab"), which silently tied the shipped data to a
            # naming convention no file declared and made a second example
            # impossible to add without editing this branch.
            logging.info("STEP1 - EXAMPLE MODE SELECTED (scenario: %s)",
                         scenarioId or "default")
            scenario = ExampleDatasets.applyScenario(
                jobInstance, EXAMPLE_FILES_DIR, scenarioId)
            logging.info("STEP1 - EXAMPLE '%s' REGISTERED (%d omics)",
                         scenario["id"], len(scenario.get("omics", [])))

            # An example has no form to submit, so "what was requested" is
            # everything the organism has installed -- the same answer the
            # upload form now arrives at by ticking every available box. The
            # manifest's own `databases` list is what applyScenario set a moment
            # ago and is overridden here on purpose: it is a property of the
            # dataset as authored, and cannot know which databases the host
            # running it installed. Five of the seven bundled scenarios declare
            # KEGG alone while every one of them is mmu, an organism that ships
            # with Reactome, so honouring the manifest meant the example ran
            # against half the pathways the same data would reach on an upload.
            jobInstance.setDatabases(
                DatabaseAvailability.resolveDatabases(jobInstance.getOrganism()))
            logging.info("STEP1 - EXAMPLE DATABASES: %s",
                         ", ".join(jobInstance.getDatabases()))

            jobInstance.setName(scenario.get("title", "")[:100])
            jobInstance.setAIConsent(formFields.get("aiConsent", "false"))
            jobInstance.setExperimentDesign(formFields.get("experimentDesign", ""))
        else:
            # See Bed2GenesServlet: a bare NotImplementedError reaches the user
            # as "ERROR MESSAGE: " with nothing after it.
            raise NotImplementedError(
                "Unrecognised example mode %r: expected no value for an "
                "upload, 'example' for the default dataset, or "
                "'example/<dataset-id>' for a specific one."
                % (exampleMode,))


        #************************************************************************
        # Step 4. Queue job
        #************************************************************************
        QUEUE_INSTANCE.enqueue(
            fn=pathwayAcquisitionStep1_PART2,
            # The decoded flag, not the raw segment: PART2 only asks "was this
            # an example?", and `exampleMode == "example"` there would answer
            # False for every `example/<id>` URL -- so a chosen scenario would
            # get an uploaded job's description instead of the example one.
            args=(jobInstance, userID, bool(isExampleRequest), RESPONSE),
            timeout=600,
            job_id= JOB_ID
        )

        #************************************************************************
        # Step 5. Return the Job ID
        #************************************************************************
        RESPONSE.setContent({
            "success": True,
            "jobID":JOB_ID
        })
    except Exception as ex:
        if jobInstance is not None:
            jobInstance.cleanDirectories(remove_output=True)

        handleException(RESPONSE, ex, __file__ , "pathwayAcquisitionStep1_PART1", userID=userID)
    finally:
        return RESPONSE

def pathwayAcquisitionStep1_PART2(jobInstance, userID, exampleMode, RESPONSE):
    """
    This function corresponds to SECOND PART of the FIRST step in the Pathways acquisition process.
    Given a JOB INSTANCE, first processes the uploaded files (identifiers matching and compound list generation)
    and finally generates the response.
    This code is executed at the PyQlite Queue.

    This is a summarization for the steps in the process:
        Step 0. CHECK FILES CONTENT
        Step 1. PROCESS THE FILES DATA
        Step 2. SAVE THE JOB INSTANCE AT THE DATABASE
        Step 3. GENERATE RESPONSE AND FINISH

    @param {PathwayAcquisitionJob} jobInstance
    @param {Response} RESPONSE
    @param {String} userID
    @param {Boolean} exampleMode

    @returns Response
    """
    try :
        # Phase weights measured over 100 logged step-1 runs: processFilesContent
        # is 98.9% of the step, validate 0.1%, save 1.0%. processFilesContent
        # subdivides itself per omic (see PathwayAcquisitionJob), which is where
        # the bar actually gets its resolution.
        # expectedTotal is the measured median (p50 43.1s over 100 logged runs).
        # It only paces phases with nothing to count, and is re-derived from this
        # job's own timings at each boundary, so a stale constant self-corrects.
        JobProgress.begin(jobInstance.getJobID(), "step1", [
            ("validate", "Checking input files", 0.1),
            ("process", "Processing files", 98.9),
            ("save", "Saving job data", 1.0),
        ], expectedTotal=43.1)

        #****************************************************************
        # Step 0.VALIDATE THE FILES DATA
        #****************************************************************
        logging.info("STEP0 - VALIDATING INPUT..." )
        JobProgress.enter(jobInstance.getJobID(), "validate")
        jobInstance.validateInput()
        logging.info("STEP1 - VALIDATING INPUT...DONE" )

        #****************************************************************
        # Step 1.PROCESS THE FILES DATA
        #****************************************************************
        logging.info("STEP1 - PROCESSING FILES..." )
        JobProgress.enter(jobInstance.getJobID(), "process")
        matchedMetabolites = jobInstance.processFilesContent() #This function processes all the files and returns a checkboxes list to show to the user

        logging.info("STEP1 - PROCESSING FILES...DONE" )

        #************************************************************************
        # Step 2. Save the jobInstance in the MongoDB
        #************************************************************************
        logging.info("STEP1 - SAVING JOB DATA..." )
        JobProgress.enter(jobInstance.getJobID(), "save")
        jobInstance.setLastStep(2)
        jobInstance.getJobDescription(True, bool(exampleMode))
        JobInformationManager().storeJobInstance(jobInstance, 1)
        logging.info("STEP1 - SAVING JOB DATA...DONE" )

        jobInstance.cleanDirectories()

        #************************************************************************
        # Step 3. Update the response content
        #************************************************************************
        RESPONSE.setContent({
            "success": True,
            "organism" : jobInstance.getOrganism(),
            "jobID": jobInstance.getJobID(),
            "userID": jobInstance.getUserID(),
            "matchedMetabolites": list(map(lambda foundFeature: foundFeature.toBSON(), matchedMetabolites)),
            "geneBasedInputOmics": inputOmicsForClient(jobInstance.getGeneBasedInputOmics()),
            "compoundBasedInputOmics": inputOmicsForClient(jobInstance.getCompoundBasedInputOmics()),
            "databases": jobInstance.getDatabases(),
            "name": jobInstance.getName(),
            # Step 3 and pa_recover_job have always sent this; step 1 did not,
            # so a browser arriving at step 2 straight from step 1 had no
            # consent flag on its job model at all. Step 2's "Choose for me"
            # button is gated on it, so without this the button was invisible
            # on the only path a user actually takes to step 2.
            "aiConsent": jobInstance.getAIConsent(),
            "timestamp": int(time())
        })

    except UnicodeDecodeError as ex:
        jobInstance.cleanDirectories(remove_output=True)

        # Last line of defence: every known read path normalises encodings
        # first, but a bad byte that slips through must reach the user as
        # advice about their file, not as a bare codec error.
        handleException(
            RESPONSE,
            Exception("[b]One of the uploaded files is not UTF-8 encoded[/b]"
                      "[br]Please save your files as UTF-8 text (in Excel: "
                      "Save As → CSV UTF-8, then convert to tab-delimited) "
                      "and submit again. (" + str(ex) + ")"),
            __file__, "pathwayAcquisitionStep1_PART2", userID=userID)
    except Exception as ex:
        jobInstance.cleanDirectories(remove_output=True)

        # TODO: at this point we should notify the queue system about the error, or else
        # will keep returning success to the job.
        handleException(RESPONSE, ex, __file__ , "pathwayAcquisitionStep1_PART2", userID=userID)
    finally:
        # In the `finally`, not the `except`: this function swallows exceptions
        # and returns RESPONSE either way. A single long-lived process means a
        # leaked ledger entry never goes away.
        JobProgress.finish(jobInstance.getJobID())
        return RESPONSE

#************************************************************************
#     _____ _______ ______ _____    ___
#    / ____|__   __|  ____|  __ \  |__ \
#   | (___    | |  | |__  | |__) |    ) |
#    \___ \   | |  |  __| |  ___/    / /
#    ____) |  | |  | |____| |       / /_
#   |_____/   |_|  |______|_|      |____|
#
def pathwayAcquisitionStep2_PART1(REQUEST, RESPONSE, QUEUE_INSTANCE, ROOT_DIRECTORY):
    """
    This function corresponds to FIRST PART of the SECOND step in the Pathways acquisition process.
    First, it takes a Request object which contains the fields of the form that started the process.
    This is a summary for the steps in the process:
        Step 0. VARIABLE DECLARATION
        Step 1. CHECK IF VALID USER SESSION
        Step 2. LOAD THE INSTANCE OF JOB
        Step 3. QUEUE THE JOB INSTANCE
        Step 4. RETURN THE JOB ID

    @param {Request} REQUEST
    @param {Response} RESPONSE
    @param {RQ QUEUE} QUEUE_INSTANCE
    @returns Response
    """
    #****************************************************************
    #Step 0. VARIABLE DECLARATION
    #The following variables are defined:
    #  - jobID: the ID for the job instance
    #  - userID: the ID for the user
    #****************************************************************
    jobID  =""
    userID = ""

    try :
        #****************************************************************
        # Step 1. CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = REQUEST.cookies.get('userID')
        sessionToken  = REQUEST.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        #****************************************************************
        # Step 2.LOAD THE INSTANCE OF JOB
        #****************************************************************
        formFields = REQUEST.form
        jobID  = formFields.get("jobID")

        # The same readOnly rule the rest of the family applies, checked here
        # rather than in PART2 because this is where the request is authorised
        # and where the caller's userID is still a cookie rather than an
        # argument that has already been trusted.
        #
        # Step 2 is the most destructive thing a caller can ask for. Its store
        # branch (JobInformationManager.storeJobInstance, stepNumber 2)
        # overwrites summary, lastStep, adjustPvalue and the rest of the
        # field list, then does removeAll + insertAll over the job's compounds,
        # its matched metabolites and its pathways. Re-running it with a
        # different selectedCompounds replaces the owner's results wholesale.
        #
        # Measured against a running server before this check: a guest session
        # that did not own a job marked readOnly posted here and got
        # success:true with the work enqueued, while the identical caller and
        # job put through pa_save_visual_options was refused.
        #
        # loadRequestedJob also replaces a blind enqueue: an unknown jobID used
        # to be queued anyway and failed asynchronously inside the worker, where
        # the user only saw the job stall. It now fails here, naming the job.
        jobInstance = loadRequestedJob(jobID, "step 2")

        if jobInstance.getReadOnly() and str(jobInstance.getUserID()) != str(userID):
            raise Exception("Invalid user for the job running step 2")

        selectedCompounds= REQUEST.form.getlist("selectedCompounds[]")
        # Retrieve the number of cluster on a per omic basis
        # Note: this will contain the omic name transformed to remove spaces and special chars
        clusterNumber = {key.replace("clusterNumber:", ""): value for key, value in formFields.items() if key.startswith("clusterNumber:")}
        # A field the form held twice reaches jQuery as an array and is posted
        # as `name[]`; the job reads `name`. One box per job now, but an older
        # client still open in a browser tab posts the bracketed form.
        metaboliteClassThreshold = {(key[:-2] if key.endswith("[]") else key): value
                                    for key, value in formFields.items()
                                    if key.startswith("thresholdMetaboliteClass")}

        #************************************************************************
        # Step 3. Queue job
        #************************************************************************
        QUEUE_INSTANCE.enqueue(
            fn=pathwayAcquisitionStep2_PART2,
            args=(jobID, userID, selectedCompounds, clusterNumber, RESPONSE, ROOT_DIRECTORY, metaboliteClassThreshold),
            timeout=600,
            job_id= jobID
        )

        #************************************************************************
        # Step 5. Return the Job ID
        #************************************************************************
        RESPONSE.setContent({
            "success": True,
            "jobID":jobID
        })

    except Exception as ex:
        handleException(RESPONSE, ex, __file__ , "pathwayAcquisitionStep2_PART1", userID=userID)
    finally:
        return RESPONSE

def pathwayAcquisitionStep2_PART2(jobID, userID, selectedCompounds, clusterNumber, RESPONSE, ROOT_DIRECTORY, metaboliteClassThreshold):
    """
    This function corresponds to SECOND PART of the SECOND step in the Pathways acquisition process.
    Given a JOB INSTANCE, first processes the uploaded files (identifiers matching and compound list generation)
    and finally generates the response.
    This code is executed at the Redis Queue.

    This is a summarization for the steps in the process:
        Step 1. READ AND UPDATE THE SELECTED METABOLITES
        Step 2. GENERATE PATHWAYS INFORMATION
        Step 3. GENERATE THE METAGENES INFORMATION
        Step 4. UPDATE JOB INFORMATION AT DATABASE
        Step 5. GENERATE RESPONSE AND FINISH

    @param {PathwayAcquisitionJob} jobInstance
    @param {String[]} selectedCompounds
    @param {Response} RESPONSE
    @param {String} userID
    @param {Boolean} exampleMode

    @returns Response
    """
    jobInstance = None

    try :
        # Weights measured over 58 logged step-2 runs. compundsClassification and
        # hubAnalysis sit inside `if selectedCompounds:` below, so with no
        # metabolites selected that 40.2% never runs — the phase is dropped from
        # the plan rather than interpolated through, or the bar would stall at
        # ~45% for the whole of a job that skipped it.
        # Two different shapes, not one shape with a phase removed. Dropping
        # `classify` and renormalising the rest was measured wrong by 44 points:
        # the pathways phase also carries getGlobalExpressionData and
        # parseRegulationPerCondition, which are a rounding error next to a
        # 32s classification but 57% of the run without one.
        if selectedCompounds:
            step2Plan = [
                ("pathways", "Building pathway list", 4.6),
                ("classify", "Classifying compounds", 40.2),
                ("metagenes", "Computing metagenes", 49.9),
                ("store", "Saving results", 3.7),
            ]
            expectedTotal = 76.5
        else:
            step2Plan = [
                ("pathways", "Building pathway list", 57.0),
                ("metagenes", "Computing metagenes", 38.0),
                ("store", "Saving results", 5.0),
            ]
            expectedTotal = 30.0
        # p50 76.5s over 58 logged runs with metabolites; ~30s without.
        # See the note on step 1's expectedTotal for how it self-corrects.
        JobProgress.begin(jobID, "step2", step2Plan, expectedTotal=expectedTotal)

        logging.info("STEP2 - LOADING JOB " + jobID + "...")
        jobInstance = JobInformationManager().loadJobInstance(jobID)

        if jobInstance is None:
            raise UserWarning("Job " + jobID + " was not found at database.")

        jobInstance.setDirectories(CLIENT_TMP_DIR)
        jobInstance.initializeDirectories()

        logging.info("STEP2 - JOB " + jobInstance.getJobID() + " LOADED SUCCESSFULLY.")

        #****************************************************************
        # Step 1.READ THE SELECTED METABOLITES
        #****************************************************************
        logging.info("STEP2 - UPDATING SELECTED COMPOUNDS LIST...")
        #TODO: CHANGE THIS TO ALLOW BACK
        jobInstance.updateSubmitedCompoundsList(selectedCompounds)
        logging.info("STEP2 - UPDATING SELECTED COMPOUNDS LIST...DONE")

        # Nothing matched anywhere: say so now, in the user's identifiers,
        # rather than let the metagenes step fail inside R over an empty file.
        emptyMapping = jobInstance.explainEmptyMapping(selectedCompounds)
        if emptyMapping:
            raise UserWarning(emptyMapping)

        logging.info("STEP2 - GENERATING PATHWAYS INFORMATION...")
        JobProgress.enter(jobID, "pathways")
        summary = jobInstance.generatePathwaysList()

        logging.info("STEP2 - GENERATE COMPOUND CLASSIFICATION")

        # Creat Global expression information for all genes
        globalExpressionData = jobInstance.getGlobalExpressionData()

        if selectedCompounds:
            JobProgress.enter(jobID, "classify")
            mappingComp, pValueInDict, classificationDict, exprssionMetabolites, adjustPvalue, totalRelevantFeaturesInCategory, featureSummary, compoundRegulateFeatures = jobInstance.compundsClassification(metaboliteClassThreshold)
            hubAnalysisResult = jobInstance.hubAnalysis( ROOT_DIRECTORY )

        # MORE Regulation Analysis: parse the rpc table for the Step 3 panel.
        # Independent of metabolomics — runs whenever the job has MORE-produced
        # geneBasedInputOmics. Self-skips otherwise.
        jobInstance.parseRegulationPerCondition()

        #****************************************************************
        # Step 2. GENERATING PATHWAYS INFORMATION
        #****************************************************************

        logging.info("STEP2 - GENERATING PATHWAYS INFORMATION...DONE")

        #****************************************************************
        # Step 3. GENERATING METAGENES INFORMATION
        #****************************************************************
        logging.info("STEP2 - GENERATING METAGENES INFORMATION...")
        JobProgress.enter(jobID, "metagenes")
        jobInstance.generateMetagenesList(ROOT_DIRECTORY, clusterNumber)
        logging.info("STEP2 - GENERATING METAGENES INFORMATION...DONE")

        jobInstance.setLastStep(3)

        #************************************************************************
        # Step 4. Save the all the Matched Compounds and pathways in MongoDB
        #************************************************************************
        logging.info("STEP2 - SAVING NEW JOB DATA..." )
        JobProgress.enter(jobID, "store")
        JobInformationManager().storeJobInstance(jobInstance, 2)
        logging.info("STEP2 - SAVING NEW JOB DATA...DONE" )

        #************************************************************************
        # Step 5. Update the response content
        #************************************************************************
        matchedPathwaysJSON = {pathwayID: pathway.toBSON() for pathwayID, pathway in jobInstance.getMatchedPathways().items()}
        matchedClassJSON = {classID: matchedclass.toBSON() for classID, matchedclass in jobInstance.getMatchedClass().items()}

        if selectedCompounds:
            RESPONSE.setContent({
                "success": True,
                "organism" : jobInstance.getOrganism(),
                "jobID":jobInstance.getJobID(),
                "summary" : summary,
                "pathwaysInfo" : matchedPathwaysJSON,
                # PaintOmics 4
                "classInfo": matchedClassJSON,
                "geneBasedInputOmics": inputOmicsForClient(jobInstance.getGeneBasedInputOmics()),
                "compoundBasedInputOmics": inputOmicsForClient(jobInstance.getCompoundBasedInputOmics()),
                "databases": jobInstance.getDatabases(),
                "omicsValuesID": jobInstance.getValueIdTable(),
                # Add classification metabolism
                "mappingComp": mappingComp,
                "classificationDict": classificationDict,
                "pValueInDict": pValueInDict,
                "exprssionMetabolites": exprssionMetabolites,
                "adjustPvalue": adjustPvalue,
                "totalRelevantFeaturesInCategory": totalRelevantFeaturesInCategory,
                "featureSummary":featureSummary,
                "classificationMeta": jobInstance.classificationMeta or {},
                "classActivity": jobInstance.classActivity or None,
                # Add compound regulate features
                "compoundRegulateFeatures": compoundRegulateFeatures,
                # Add global gene expression information
                "globalExpressionData":globalExpressionData,
                # Add hub analysis result
                'hubAnalysisResult': hubAnalysisResult,
                # Add MORE RegulationPerCondition table for the Step 3 panel
                # (None when MORE wasn't run; panel hides itself in that case).
                "regulationPerConditionData": getattr(jobInstance, "regulationPerConditionData", None),
                "aiConsent": jobInstance.getAIConsent(),
                "experimentDesign": jobInstance.getExperimentDesign(),
                "conditionNames": getattr(jobInstance, "conditionNames", []),
                "timestamp": int(time())
            })
        else:
            RESPONSE.setContent( {
                "success": True,
                "organism": jobInstance.getOrganism(),
                "jobID": jobInstance.getJobID(),
                "summary": summary,
                "pathwaysInfo": matchedPathwaysJSON,
                # PaintOmics 4
                "classInfo": matchedClassJSON,
                "geneBasedInputOmics": inputOmicsForClient(jobInstance.getGeneBasedInputOmics()),
                "compoundBasedInputOmics": inputOmicsForClient(jobInstance.getCompoundBasedInputOmics()),
                "databases": jobInstance.getDatabases(),
                "omicsValuesID": jobInstance.getValueIdTable(),
                # Add classification metabolism
                "mappingComp": {},
                "pValueInDict": [],
                "classificationDict": {},
                "exprssionMetabolites": {},
                "adjustPvalue": [],
                "totalRelevantFeaturesInCategory": [],
                "featureSummary": [0, 0],
                "classificationMeta": {},
                "classActivity": None,
                "compoundRegulateFeatures": {},
                "hubAnalysisResult": {},
                "globalExpressionData": globalExpressionData,
                # MORE rpc table — populated when MORE was used, even without metabolomics.
                "regulationPerConditionData": getattr(jobInstance, "regulationPerConditionData", None),
                "aiConsent": jobInstance.getAIConsent(),
                "experimentDesign": jobInstance.getExperimentDesign(),
                "conditionNames": getattr(jobInstance, "conditionNames", []),
                "timestamp": int( time() )
            } )

    except Exception as ex:
        handleException(RESPONSE, ex, __file__ , "pathwayAcquisitionStep2_PART2", userID=userID)
    finally:
        JobProgress.finish(jobID)
        jobInstance.cleanDirectories()
        return RESPONSE

#************************************************************************
#     _____ _______ ______ _____    ____
#    / ____|__   __|  ____|  __ \  |___ \
#   | (___    | |  | |__  | |__) |   __) |
#    \___ \   | |  |  __| |  ___/   |__ <
#    ____) |  | |  | |____| |       ___) |
#   |_____/   |_|  |______|_|      |____/
#
def pathwayAcquisitionStep3(request, response):
    #VARIABLE DECLARATION
    jobInstance = None
    jobID  = ""
    userID = ""

    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        #****************************************************************
        # Step 1.LOAD THE INSTANCE OF JOB
        #****************************************************************
        formFields = request.form
        jobID  = formFields.get("jobID")

        #TODO: IN PREVIOUS STEPS THE USER COULD SPECIFY THE DEFAULT OMICS TO SHOW
        visibleOmics = []

        # Validated before the log line: concatenating a missing jobID here
        # raised TypeError before the "was not found" check could ever run.
        jobInstance = loadRequestedJob(jobID, "step 3")

        logging.info("STEP3 - LOADING JOB " + jobID + "...")

        logging.info("STEP3 - JOB " + jobInstance.getJobID() + " LOADED SUCCESSFULLY.")

        #****************************************************************
        # Step 2.READ THE SELECTED PATHWAYS
        #****************************************************************
        logging.info("STEP3 - GENERATING PATHWAYS INFORMATION...")
        selectedPathways= formFields.getlist("selectedPathways")
        #TODO: SOLO GENERAR INFO PARA LAS QUE NO LA TENGAN YA GUARDADA EN LA BBDD
        [selectedPathwayInstances, graphicalOptionsInstancesBSON, omicsValuesSubset] = jobInstance.generateSelectedPathwaysInformation(selectedPathways, visibleOmics, True)

        logging.info("STEP3 - GENERATING PATHWAYS INFORMATION...DONE")

        #************************************************************************
        # Step 3. Save the jobInstance in the MongoDB
        #************************************************************************
        logging.info("STEP 3 - SAVING NEW JOB DATA..." )
        JobInformationManager().storeJobInstance(jobInstance, 3)
        logging.info("STEP 3 - SAVING NEW JOB DATA...DONE" )

        response.setContent({
            "success": True,
            "jobID": jobInstance.getJobID(),
            "graphicalOptionsInstances" : graphicalOptionsInstancesBSON,
            "omicsValues": omicsValuesSubset,
            "organism" : jobInstance.getOrganism()
        })

    except Exception as ex:
        handleException(response, ex, __file__ , "pathwayAcquisitionStep3", userID=userID)
    finally:
        return response

def pathwayAcquisitionRecoverJob(request, response, QUEUE_INSTANCE):
    #VARIABLE DECLARATION
    jobInstance = None
    jobID=""
    userID = ""
    #TODO: COMPROBAR OWNERS
    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        #****************************************************************
        # Step 1.LOAD THE INSTANCE OF JOB
        #****************************************************************


        formFields = request.form
        jobID  = formFields.get("jobID")

        # Concatenating a missing jobID into this log line raised TypeError
        # before any of the checks below could report the real problem. The
        # "not found" case keeps its own richer message further down, so only
        # absence is rejected here.
        if not jobID:
            raise UserWarning("Missing jobID parameter for recovering a job.")

        logging.info("RECOVER_JOB - LOADING JOB " + jobID + "...")
        jobInstance = JobInformationManager().loadJobInstance(jobID)
        queueJob = QUEUE_INSTANCE.fetch_job(jobID)

        if queueJob is not None and not queueJob.is_finished():
            logging.info("RECOVER_JOB - JOB " + jobID + " HAS NOT FINISHED ")
            response.setContent({"success": False, "message": "Your job " + jobID + " is still running in the queue. Please, try again later to check if it has finished."})
            return response

        if jobInstance == None:
            #TODO DIAS BORRADO?
            logging.info("RECOVER_JOB - JOB " + jobID + " NOT FOUND AT DATABASE.")
            response.setContent({"success": False, "errorMessage": "Job " + jobID + " not found at database.<br>Please, note that jobs are automatically removed after 7 days for guests and 14 days for registered users."})
            return response

        # Allow "no user" jobs to be viewed by anyone, logged or not
        if str( jobInstance.getUserID() ) != 'None' and jobInstance.getUserID() != userID and not jobInstance.getAllowSharing():
            logging.info("RECOVER_JOB - JOB " + jobID + " DOES NOT BELONG TO USER " + str(userID) + " JOB HAS USER " + str(jobInstance.getUserID()))
            response.setContent({"success": False, "errorMessage": "Invalid Job ID (" + jobID + ") for current user.<br>Please, check the Job ID and try again."})
            return response

        # Sanitize optional PaintOmics 4 fields that may come back as strings
        def _as_dict(value):
            return value if isinstance(value, dict) else {}

        def _as_dict_or_list(value):
            return value if isinstance(value, (dict, list)) else {}

        safe_mappingComp = _as_dict(jobInstance.mappingComp)
        safe_classificationDict = _as_dict(jobInstance.classificationDict)
        safe_pValueInDict = _as_dict_or_list(jobInstance.pValueInDict)
        safe_exprssionMetabolites = _as_dict(jobInstance.exprssionMetabolites)
        safe_adjustPvalue = _as_dict_or_list(jobInstance.adjustPvalue)
        safe_totalRelevantFeaturesInCategory = _as_dict_or_list(jobInstance.totalRelevantFeaturesInCategory)
        safe_featureSummary = jobInstance.featureSummary if isinstance(jobInstance.featureSummary, list) else [0, 0]
        # Derived from kegg_interaction.json + inputCompoundsData, not read back
        # from the document: the field is cache-only (PAINTOMICS4_LARGE_FIELDS),
        # so the attribute is None on any process that did not run step 2 and
        # Step 4's "Neighbouring features" panel had nothing to work with on
        # every job opened from its link. See getCompoundRegulateFeatures().
        safe_compoundRegulateFeatures = _as_dict(jobInstance.getCompoundRegulateFeatures())
        safe_globalExpressionData = _as_dict(jobInstance.getGlobalExpressionData())
        # The hub table is DERIVED, not owned by the job -- the same footing as
        # compoundRegulateFeatures just above. Re-derive it whenever what is
        # stored is unusable, which is two cases:
        #
        #   stale schema  rows written by the R scorer, computed on a graph with
        #                 28.2% mis-attributed subtypes and balls that could
        #                 contain their own seed. Rendering them faithfully
        #                 would preserve numbers we know to be wrong.
        #   no table      jobs whose hubAnalysisResult never persisted at all.
        #                 These showed an empty grid with headers and no
        #                 explanation; deriving costs ~0.09 s once the
        #                 organism's graph is cached, so there is no reason to.
        #
        # Either way the client is left with exactly one row shape to read
        # instead of a dual-shape reader on both sides.
        from src.common.KeggGraph.scorer import HUB_SCHEMA_VERSION
        _stored = jobInstance.hubAnalysisResult
        if isinstance(_stored, dict) and _stored:
            _sample = next(iter(_stored.values()))
            _stale = not (isinstance(_sample, dict)
                          and _sample.get("schema") == HUB_SCHEMA_VERSION)
            _reason = "stale schema"
        else:
            # adaptBSON turns a stored None into the STRING "None", so anything
            # that is not a populated dict means "no usable table".
            _stale = True
            _reason = "no stored table"
        if _stale:
            logging.info("RECOVER_JOB - re-deriving hub rows for %s (%s)",
                         jobID, _reason)
            try:
                jobInstance.hubAnalysis()
            except Exception as _ex:
                logging.warning("RECOVER_JOB - could not derive hub rows for "
                                "%s (%s); leaving the panel empty rather than "
                                "rendering the old shape.", jobID, str(_ex))
                jobInstance.hubAnalysisResult = None
        safe_hubAnalysisResult = _as_dict(jobInstance.hubAnalysisResult)

        logging.info("RECOVER_JOB - JOB " + jobInstance.getJobID() + " LOADED SUCCESSFULLY.")

        matchedCompoundsJSONList = list(map(lambda foundFeature: foundFeature.toBSON(), jobInstance.getFoundCompounds()))

        logging.info("RECOVER_JOB - GENERATING PATHWAYS CLASS INFORMATION...DONE")

        matchedPathwaysJSONList = []
        for matchedPathway in jobInstance.getMatchedPathways().values():
            matchedPathwaysJSONList.append(matchedPathway.toBSON())
        
        logging.info("RECOVER_JOB - GENERATING PATHWAYS INFORMATION...DONE")

        matchedClassJSONList = []
        for matchedclass in jobInstance.getMatchedClass().values():
            matchedClassJSONList.append( matchedclass.toBSON() )

        if len( matchedCompoundsJSONList ) == 0 and jobInstance.getLastStep() == 2 and len( jobInstance.getCompoundBasedInputOmics() ) > 0:
            logging.info("RECOVER_JOB - JOB " + jobID + " DOES NOT CONTAINS FOUND COMPOUNDS (STEP 2: OLD FORMAT?).")
            response.setContent({"success": False, "errorMessage": "Job " + jobID + " does not contains saved information about the found compounds, please run it again."})
        elif len( matchedPathwaysJSONList ) == 0 and jobInstance.getLastStep() > 2:
            logging.info("RECOVER_JOB - JOB " + jobID + " DOES NOT CONTAINS PATHWAYS.")
            response.setContent( {"success": False, "errorMessage":"Job " + jobID + " does not contains information about pathways. Please, run it again."})
        else:
            if len(matchedPathwaysJSONList) != 0 :
                response.setContent({
                    "success": True,
                    "jobID": jobInstance.getJobID(),
                    "userID": jobInstance.getUserID(),
                    "pathwaysInfo" : matchedPathwaysJSONList,
                    "geneBasedInputOmics": inputOmicsForClient(jobInstance.getGeneBasedInputOmics()),
                    "compoundBasedInputOmics": inputOmicsForClient(jobInstance.getCompoundBasedInputOmics()),
                    "organism" : jobInstance.getOrganism(),
                    "summary" : jobInstance.summary,
                    "visualOptions" : JobInformationManager().getVisualOptions(jobID),
                    "databases": jobInstance.getDatabases(),
                    "matchedMetabolites": matchedCompoundsJSONList,
                    "stepNumber": jobInstance.getLastStep(),
                    "name": jobInstance.getName(),
                    "timestamp": int(time()),
                    "allowSharing": jobInstance.getAllowSharing(),
                    "readOnly": jobInstance.getReadOnly(),
                    "omicsValuesID": jobInstance.getValueIdTable(),
                    #PaintOmics 4
                    "classInfo": matchedClassJSONList,
                    "mappingComp": safe_mappingComp,
                    "classificationDict": safe_classificationDict,
                    "pValueInDict": safe_pValueInDict,
                    "exprssionMetabolites": safe_exprssionMetabolites,
                    "adjustPvalue": safe_adjustPvalue,
                    "totalRelevantFeaturesInCategory": safe_totalRelevantFeaturesInCategory,
                    "featureSummary": safe_featureSummary,
                    "classificationMeta": _as_dict(jobInstance.classificationMeta),
                    "classActivity": _as_dict(getattr(jobInstance, "classActivity", None)) or None,
                    # Add compound regulate features
                    "compoundRegulateFeatures": safe_compoundRegulateFeatures,
                    # Add global gene expression information
                    "globalExpressionData": safe_globalExpressionData,
                    # Add hub analysis result
                    'hubAnalysisResult': safe_hubAnalysisResult,
                    # Add MORE RegulationPerCondition table for the Step 3 panel.
                    # Persisted in Mongo (PAINTOMICS4_DICT_FIELDS), so reloads survive.
                    "regulationPerConditionData": getattr(jobInstance, "regulationPerConditionData", None),
                    "aiConsent": jobInstance.getAIConsent(),
                    "conditionNames": getattr(jobInstance, "conditionNames", []),
                })
            else:
                response.setContent({
                    "success": True,
                    "jobID": jobInstance.getJobID(),
                    "userID": jobInstance.getUserID(),
                    "pathwaysInfo" : matchedPathwaysJSONList,
                    "geneBasedInputOmics": inputOmicsForClient(jobInstance.getGeneBasedInputOmics()),
                    "compoundBasedInputOmics": inputOmicsForClient(jobInstance.getCompoundBasedInputOmics()),
                    "organism" : jobInstance.getOrganism(),
                    "summary" : jobInstance.summary,
                    "visualOptions" : JobInformationManager().getVisualOptions(jobID),
                    "databases": jobInstance.getDatabases(),
                    "matchedMetabolites": matchedCompoundsJSONList,
                    "stepNumber": jobInstance.getLastStep(),
                    "name": jobInstance.getName(),
                    "timestamp": int(time()),
                    "allowSharing": jobInstance.getAllowSharing(),
                    "readOnly": jobInstance.getReadOnly(),
                    "omicsValuesID": jobInstance.getValueIdTable(),
                    "aiConsent": jobInstance.getAIConsent(),
                    "conditionNames": getattr(jobInstance, "conditionNames", []),
                })

    except Exception as ex:
        handleException(response, ex, __file__ , "pathwayAcquisitionRecoverJob", userID=userID)
    finally:
        return response

def pathwayAcquisitionTouchJob(request, response):
    try:
        jobID = request.form.get("jobID")
        JobInformationManager().touchAccessDate(jobID)

        response.setContent({"success": True})
    except Exception as ex:
        handleException(response, ex, __file__, "pathwayAcquisitionTouchJob", jobID=jobID)
    finally:
        return response

def _dataUriOnlyFetcher(url, resource_type=None):
    """Resolve embedded ``data:`` URIs and nothing else.

    CairoSVG's own safe mode answers *every* reference with a blank 1x1 image,
    which is too blunt here: ``data:`` URIs go through the same fetcher, so
    plain safe mode silently drops the pathway background and exports a black
    PNG. This keeps the embedded case working and refuses the rest.

    A blank image is returned rather than an exception because that is what
    CairoSVG's stub does, so an SVG naming something external still renders
    (without it) instead of failing the whole export. ``read_url`` normalises
    a bare path to ``file://<abspath>`` before calling this, so a plain
    ``/etc/passwd`` href arrives here with a scheme and is refused like any
    other.
    """
    if url.startswith("data:"):
        return cairosvg.url.fetch(url, resource_type)

    logging.warning("Refused an external reference while rendering an export: %s",
                    url[:200])
    return b'<svg width="1" height="1"></svg>'


def renderSvgToPng(svgData, destinationPath):
    """Rasterise caller-supplied SVG markup, resolving nothing external.

    This used to pass ``unsafe=True``. In cairosvg/parser.py that single flag
    turns off three protections together::

        tree = ElementTree.fromstring(
            bytestring, forbid_entities=not unsafe,
            forbid_external=not unsafe)
        ...
        if 'url_fetcher' not in kwargs and not unsafe:
            self.url_fetcher = (
                lambda *args, **kwargs: b'<svg width="1" height="1"></svg>')

    so it accepted entity definitions (billion laughs) and installed the real
    URL fetcher in place of that stub. ``read_url`` normalises a bare path to
    ``file://<abspath>``, so with the real fetcher in place
    ``<image xlink:href="/etc/passwd">`` reads a local file and an ``http://``
    href is a request from inside the deployment network. The markup comes
    from ``request.form.get("svgCode")``, so that was reachable by any user
    holding a session cookie, and the render is written into the job output
    directory and served back over /get_cluster_image/.

    Turning the flag off is not sufficient on its own, and this is the part
    worth remembering: CairoSVG's stub fetcher intercepts ``data:`` URIs too,
    and the legitimate export is *entirely* data: URIs. PathwayController.js
    draws the pathway background into a canvas, takes
    ``forcedImageCode = canvas.toDataURL()`` and substitutes that for the PNG's
    URL before posting. So `unsafe=False` alone renders the pathway background
    as a black rectangle -- measured, not assumed. Hence the explicit fetcher.

    ``PNGSurface.convert`` rather than ``svg2png``: the latter has a fixed
    keyword list and drops ``url_fetcher``, while ``convert`` forwards **kwargs
    to the parser. It is what ``svg2png`` calls anyway.

    Pinned by src/tests/test_svg_export_is_sandboxed.py, which checks both
    halves -- that a local file's pixels stay out of the render, and that an
    embedded data: URI still reaches it.
    """
    cairosvg.surface.PNGSurface.convert(
        bytestring=svgData,
        write_to=destinationPath,
        url_fetcher=_dataUriOnlyFetcher)


def pathwayAcquisitionSaveImage(request, response):
    jobID=""
    try:
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        # Restored: this was commented out in the 2021 bulk "update new
        # version" commit, leaving SaveImage the one handler in its family
        # without the check that SaveVisualOptions and SaveSharingOptions both
        # perform, while it writes a file into the job's output directory.
        #
        # It is a small gain, and worth being precise about: isValidUser admits
        # the anonymous "nologin" case deliberately, so a request with no
        # cookies passes either way. What this rejects is a caller presenting a
        # userID with a wrong or stale token. The value is mostly that the
        # asymmetry is gone -- nothing in the body told a reader this handler
        # was the exception, and handlers get copied as templates.
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        jobID = request.form.get("jobID")
        jobInstance = loadRequestedJob(jobID, "saving the image")

        # The same guard pathwayAcquisitionSaveVisualOptions carries, and for a
        # stronger reason: that one writes a document to MongoDB, this one
        # writes a file into the job's output directory. `outputDir` is built
        # from the *job owner's* userID --
        #     CLIENT_TMP_DIR + userDir + "/jobsData/" + jobID + "/output/"
        # (Job.setDirectories) -- so the bytes land under whoever owns the job,
        # not whoever sent the request.
        #
        # Measured against a running server before this line existed: a guest
        # session that did not own a job marked readOnly posted here and got
        # success:true, and paintomics_<name>_<jobID>.svg appeared in the
        # owner's output directory. The identical caller and job put through
        # pa_save_visual_options was refused. Only the missing check differed.
        #
        # The svg branch writes the request body verbatim, and
        # /get_cluster_image serves that directory through send_from_directory,
        # which types a .svg as image/svg+xml from the app's own origin -- so
        # the file planted there is same-origin markup, not just wasted disk.
        #
        # This restores the readOnly semantics the rest of the family uses; it
        # deliberately does not tighten them. A job that is not readOnly stays
        # writable by anyone holding its ID, which is how sharing works here.
        if jobInstance.getReadOnly() and str(jobInstance.getUserID()) != str(userID):
            raise Exception("Invalid user for the job saving the image")

        svgData = request.form.get("svgCode")

        # .replace() straight off the form raised AttributeError when the field
        # was absent, so a request missing it failed without naming it.
        requestedFileName = request.form.get("fileName")
        if not requestedFileName:
            raise UserWarning("Missing fileName parameter for saving the image.")

        fileName = "paintomics_" + requestedFileName.replace(" ", "_").replace("/", "_") + "_" + jobID
        fileFormat = request.form.get("format")

        path = jobInstance.getOutputDir()
        logging.info("The path is xxx: " + path)

        if(fileFormat == "png"):
            try:
                logging.info("TRYING...")
                renderSvgToPng(svgData, path + fileName + "." + fileFormat)
            except Exception:
                logging.info("TRYING again...")
                renderSvgToPng(svgData, path + fileName + "." + fileFormat)

        elif(fileFormat == "svg"):
            file_ = open(path + fileName + "." + fileFormat, 'w')
            file_.write(svgData)
            file_.close()

        path = "/get_cluster_image/" + jobID + "/output/"

        response.setContent({"success": True, "filepath": path + fileName + "." + fileFormat})
    except Exception as ex:
        handleException(response, ex, __file__ , "pathwayAcquisitionSaveImage")
    finally:
        return response

def pathwayAcquisitionSaveVisualOptions(request, response):
    #VARIABLE DECLARATION
    jobID  = ""
    userID = ""

    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        #****************************************************************
        # Step 1.GET THE INSTANCE OF visual Options
        #****************************************************************
        # `or {}` for the same reason pathwayAcquisitionApplyReplicateMapping
        # has it: request.get_json() returns None when the request did not
        # arrive as application/json, and .get() on that is
        #     AttributeError: 'NoneType' object has no attribute 'get'
        # which reaches the browser naming neither the field nor the handler's
        # actual complaint. Falling back to an empty mapping lets
        # loadRequestedJob below report the missing jobID instead.
        visualOptions = request.get_json() or {}
        jobID  = visualOptions.get("jobID")

        jobInstance = loadRequestedJob(jobID, "saving visual options")

        if jobInstance.getReadOnly() and str(jobInstance.getUserID()) != str(userID):
            raise Exception("Invalid user for the job saving visual options")

        newTimestamp = int(time())
        visualOptions["timestamp"] = newTimestamp

        #************************************************************************
        # Step 3. Save the visual Options in the MongoDB
        #************************************************************************
        logging.info("STEP 3 - SAVING VISUAL OPTIONS FOR JOB " + jobID + "..." )
        JobInformationManager().storeVisualOptions(jobID, visualOptions)
        logging.info("STEP 3 - SAVING VISUAL OPTIONS FOR JOB " + jobID + "...DONE" )

        response.setContent({"success": True, "timestamp": newTimestamp})

    except Exception as ex:
        handleException(response, ex, __file__ , "pathwayAcquisitionSaveVisualOptions", userID=userID)
    finally:
        return response

def pathwayAcquisitionPathwayEvidence(request, response):
    """Evidence edges drawable on one open pathway diagram.

    Answered per pathway rather than bundled into the Step 3 response: the
    literature classification needs the organism's whole interaction graph,
    and a job may select hundreds of pathways of which a user opens a few.
    """
    jobID  = ""
    userID = ""

    try:
        userID = request.cookies.get('userID')
        sessionToken = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        # `or {}` -- request.get_json() is None when the request did not arrive
        # as application/json, and .get() on that names neither field nor cause.
        options = request.get_json() or {}
        jobID = options.get("jobID")
        pathwayID = options.get("pathwayID")

        if not pathwayID:
            raise UserWarning("Missing pathwayID parameter for pathway evidence.")

        jobInstance = loadRequestedJob(jobID, "reading pathway evidence")

        if jobInstance.getReadOnly() and str(jobInstance.getUserID()) != str(userID):
            raise Exception("Invalid user for the job reading pathway evidence")

        maxEdges = options.get("maxEdges", PathwayEvidence.DEFAULT_MAX_EDGES)
        try:
            maxEdges = max(0, min(int(maxEdges), 200))
        except (TypeError, ValueError):
            maxEdges = PathwayEvidence.DEFAULT_MAX_EDGES

        evidence = PathwayEvidence.buildPathwayEvidence(
            jobInstance, pathwayID,
            condition=options.get("condition"),
            maxEdges=maxEdges,
            classes=options.get("classes"))

        evidence["success"] = True
        response.setContent(evidence)

    except Exception as ex:
        handleException(response, ex, __file__, "pathwayAcquisitionPathwayEvidence", userID=userID)
    finally:
        return response


def pathwayAcquisitionSaveSharingOptions(request, response):
    #VARIABLE DECLARATION
    jobID  = ""
    userID = ""

    try :
        #****************************************************************
        # Step 0.CHECK IF VALID USER SESSION
        #****************************************************************
        logging.info("STEP0 - CHECK IF VALID USER....")
        userID  = request.cookies.get('userID')
        sessionToken  = request.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        #****************************************************************
        # Step 1.GET THE INSTANCE OF sharing options
        #****************************************************************
        jobID = request.form.get("jobID")
        jobInstance = loadRequestedJob(jobID, "saving sharing options")

        # An ownerless job ("nologin" mode stores userID None) has nobody who
        # may hold sharing options on it: every visitor is equally anonymous,
        # so the ownership comparison below would grant each of them ownership
        # ('None' == 'None') of flags that pa_recover_job and the read-only
        # guards never enforce for ownerless jobs anyway. The dialog no longer
        # offers the controls for these jobs; this keeps direct POSTs honest.
        if jobInstance.getUserID() is None or str(jobInstance.getUserID()) == 'None':
            raise Exception("This job was created without an account, so it has no owner and its sharing options cannot be changed.")

        if str(jobInstance.getUserID()) != str(userID):
            raise Exception("Invalid user for this jobID")

        #************************************************************************
        # Step 3. Save the visual Options in the MongoDB
        #************************************************************************
        jobInstance.setAllowSharing(request.form.get("allowSharing", 'false') == 'true')
        jobInstance.setReadOnly(request.form.get("readOnly", 'false') == 'true')

        logging.info("STEP 3 - SAVING SHARING OPTIONS FOR JOB " + jobID + "..." )
        JobInformationManager().storeSharingOptions(jobInstance)
        logging.info("STEP 3 - SAVING SHARING OPTIONS FOR JOB " + jobID + "...DONE" )

        response.setContent({"success": True})
    except Exception as ex:
        handleException(response, ex, __file__ , "pathwayAcquisitionSaveSharingOptions", userID=userID)
    finally:
        return response

def pathwayAcquisitionApplyReplicateMapping(request, response):
    """
    Apply (or clear) a replicate→sample mapping for one omic of a job.

    The user reaches this endpoint from the Step-2 "Replicate detection" panel
    after their values file has been parsed. The endpoint takes their chosen
    mode and (for manual mode) a 2-column design file, computes the sample
    grouping, and writes per-sample aggregated values onto every OmicValue
    of the targeted omic.

    Request JSON
    ------------
    {
        "jobID":      str,
        "omicName":   str,                     # must match an inputOmic
        "mode":       "auto" | "manual" | "off",
        "design":     str (optional)           # 2-column TSV body, manual mode only.
                                               # Header row optional. Column 1 must
                                               # match omicHeader[1:]; column 2 is
                                               # the biological-sample label.
    }

    Response JSON
    -------------
    {
        "success":      True,
        "status":       "applied" | "cleared",
        "mode":         echo of input mode,
        "sampleHeader": list[str]              # empty when status == "cleared"
        "mapping":      list[int]              # parallel to omicHeader[1:]
        "featuresUpdated": int                 # number of Genes/Compounds touched
    }
    """
    userID = ""
    jobID = ""
    try:
        # ---- Step 0. Session check ----------------------------------------
        userID = request.cookies.get("userID")
        sessionToken = request.cookies.get("sessionToken")
        UserSessionManager().isValidUser(userID, sessionToken)

        # ---- Step 1. Parse and validate input -----------------------------
        payload = request.get_json() or {}
        jobID    = payload.get("jobID")
        omicName = payload.get("omicName")
        mode     = (payload.get("mode") or "").lower()

        if not jobID:
            raise Exception("Missing jobID.")
        if not omicName:
            raise Exception("Missing omicName.")
        if mode not in ("auto", "manual", "off"):
            raise Exception("Invalid mode '%s'; expected 'auto', 'manual', or 'off'." % mode)

        # ---- Step 2. Load job and locate the targeted omic ----------------
        jobInstance = JobInformationManager().loadJobInstance(jobID)
        if jobInstance is None:
            raise Exception("Job %s not found." % jobID)
        if jobInstance.getReadOnly() and str(jobInstance.getUserID()) != str(userID):
            raise Exception("Read-only job — replicate mapping cannot be modified by this user.")

        # ---- Step 3. Compute mapping & aggregate via the job's apply method.
        # The PathwayAcquisitionJob owns the single source of truth for both
        # the auto-apply path (Step-1 processFilesContent) and this endpoint —
        # no aggregation logic lives in the servlet.
        # Resolve the omic once, for every mode. featureDict is needed further
        # down to re-emit the affected feature collection; it used to be looked
        # up only inside the manual branch and discarded into `_`, so the
        # reinsert below raised NameError *after* the features had already been
        # deleted.
        inputOmic, featureDict, featureType = jobInstance._findInputOmicByName(omicName)
        if inputOmic is None:
            raise Exception("Omic '%s' not found in this job." % omicName)

        if mode == "manual":
            # Parse the design file here (servlet-level concern: reading uploaded
            # text). The job method handles the in-memory aggregation.
            designBody = payload.get("design") or ""
            omicHeader = inputOmic.get("omicHeader") or []
            replicateHeader = omicHeader[1:] if len(omicHeader) > 1 else []
            sampleHeader, mapping, groups = _parseDesignFile(designBody, replicateHeader)
            result = jobInstance.applyReplicateMappingForOmic(
                omicName, mode="manual",
                sampleHeader=sampleHeader, mapping=mapping, groups=groups,
            )
        else:
            result = jobInstance.applyReplicateMappingForOmic(omicName, mode=mode)

        sampleHeader     = result["sampleHeader"]
        mapping          = result["mapping"]
        featureType      = result["featureType"]
        featuresUpdated  = result["featuresUpdated"]

        # ---- Step 5. Persist ---------------------------------------------
        # Update the inputOmic dict on the job document (only the two list
        # fields can change), then re-emit the affected feature collection.
        # Reinsert the whole featureType bucket — same heavy-but-safe pattern
        # used by step-2 storeJobInstance for compounds.
        jobDAO = PathwayAcquisitionJobDAO()
        try:
            jobDAO.update(jobInstance, {"fieldList": ["geneBasedInputOmics", "compoundBasedInputOmics"]})
        finally:
            jobDAO.closeConnection()

        featDAO = FeatureDAO()
        try:
            # Materialise the replacement set *before* deleting the old one.
            # removeAll + insertAll is not atomic, so anything that can fail
            # between them costs the user their features -- which is exactly
            # what happened while featureDict was unbound here.
            featuresToStore = list(featureDict.values()) if featureDict else []
            featDAO.removeAll({"jobID": jobID, "featureType": featureType})
            if featuresToStore:
                featDAO.insertAll(featuresToStore, {"jobID": jobID})
        finally:
            featDAO.closeConnection()

        response.setContent({
            "success":          True,
            "status":           "cleared" if mode == "off" else "applied",
            "mode":             mode,
            "sampleHeader":     sampleHeader,
            "mapping":          mapping,
            "featuresUpdated":  featuresUpdated,
        })
    except Exception as ex:
        handleException(response, ex, __file__, "pathwayAcquisitionApplyReplicateMapping", userID=userID)
    finally:
        return response


def _parseDesignFile(body, replicateHeader):
    """
    Parse a user-supplied design file into ``(sampleHeader, mapping, groups)``.

    Thin delegation to :func:`src.common.DesignFile.parse_design`, which is
    shared with the job-side auto-apply path so a design uploaded here and a
    design shipped with a MORE run are read by exactly one implementation.
    The reader also accepts MORE's indicator-matrix ``edesign`` in addition to
    the two-column long form this endpoint has always taken.
    """
    return parse_design(body, replicateHeader)


def pathwayAcquisitionMetagenes_PART1(REQUEST, RESPONSE, QUEUE_INSTANCE, JOB_ID, ROOT_DIRECTORY):
        # ****************************************************************
        # Step 0. VARIABLE DECLARATION
        # The following variables are defined:
        #  - jobInstance: instance of the PathwayAcquisitionJob class.
        #                 Contains all the information for the current job.
        #  - userID: the ID for the user
        # ****************************************************************
        userID = None

        try:
            # ****************************************************************
            # Step 1. CHECK IF VALID USER SESSION
            # ****************************************************************
            logging.info("STEP0 - CHECK IF VALID USER....")
            userID = REQUEST.cookies.get('userID')
            sessionToken = REQUEST.cookies.get('sessionToken')
            UserSessionManager().isValidUser(userID, sessionToken)

            # ****************************************************************
            # Step 2. LOAD THE JOB INSTANCE AND RETRIEVE FORM INFO
            # ****************************************************************
            savedJobID = REQUEST.form.get("jobID")
            savedJobInstance = loadRequestedJob(savedJobID, "generating clusters")

            if savedJobInstance.getReadOnly() and str(savedJobInstance.getUserID()) != str(userID):
                raise Exception("Invalid user for the job generating metagenes.")

            omicName = REQUEST.form.get("omic")
            clusterNumber = int(REQUEST.form.get("number"))
            database = REQUEST.form.get("database")

            # Make sure the number of clusters is inside [1, 20]
            clusterNumber = 1 if clusterNumber < 1 else 20 if clusterNumber > 20 else clusterNumber

            # ************************************************************************
            # Step 4. Queue job
            # ************************************************************************
            QUEUE_INSTANCE.enqueue(
                fn=pathwayAcquisitionMetagenes_PART2,
                args=(ROOT_DIRECTORY, userID, savedJobInstance, omicName, clusterNumber, database, RESPONSE),
                timeout=600,
                job_id=JOB_ID
            )

            # ************************************************************************
            # Step 5. Return the Job ID
            # ************************************************************************
            RESPONSE.setContent({
                "success": True,
                "jobID": JOB_ID
            })
        except Exception as ex:
            handleException(RESPONSE, ex, __file__, "pathwayAcquisitionMetagenes_PART1", userID=userID)
        finally:
            return RESPONSE

def pathwayAcquisitionMetagenes_PART2(ROOT_DIRECTORY, userID, savedJobInstance, omicName, clusterNumber, database, RESPONSE):
    #VARIABLE DECLARATION
    jobID  = ""
    userID = ""

    try :
        #************************************************************************
        # Step 3. Save the visual Options in the MongoDB
        #************************************************************************
        logging.info("UPDATE METAGENES - STEP 2 FOR JOB " + jobID + "..." )
        savedJobInstance.generateMetagenesList(ROOT_DIRECTORY, {omicName: clusterNumber}, [omicName], [database])
        logging.info("UPDATE METAGENES - STEP 2 FOR JOB " + jobID + "...DONE")
        JobInformationManager().storePathways(savedJobInstance)

        matchedPathwaysJSONList = []
        for matchedPathway in savedJobInstance.getMatchedPathways().values():
            logging.info("match_pathway:"+str(matchedPathway))
            matchedPathwaysJSONList.append(matchedPathway.toBSON())

        RESPONSE.setContent({
            "success": True,
            "jobID": jobID,
            # "timestamp": newTimestamp,
            "pathwaysInfo": matchedPathwaysJSONList
        })
        logging.info("matchedPathwaysJSONList:"+str(matchedPathwaysJSONList))

    except Exception as ex:
        handleException(RESPONSE, ex, __file__ , "pathwayAcquisitionMetagenes_PART2", userID=userID)
    finally:
        savedJobInstance.cleanDirectories()

        return RESPONSE

def pathwayAcquisitionAdjustPvalues(request, response):
    try:
        #****************************************************************
        # Step 1.GET THE INFO
        #****************************************************************
        # `or {}` as above: get_json() is None for a request that did not
        # arrive as application/json, and the .get() below was then
        #     AttributeError: 'NoneType' object has no attribute 'get'
        formFields = request.get_json() or {}

        # List of pathway => {pvalues}
        pvalues = formFields.get("pValues")

        # Named rather than left to fail further down. Without pValues the
        # loops below iterate `None`, which is
        #     AttributeError: 'NoneType' object has no attribute 'items'
        # several frames from the cause.
        if not pvalues:
            raise UserWarning("Missing pValues parameter for adjusting p-values.")

        # Check what kind of p-value we want to update
        if "stoufferWeights" in formFields:
            newStoufferWeights = formFields.get("stoufferWeights")
            visiblePathways = formFields.get("visiblePathways")

            newStoufferPvalues = defaultdict(dict)
            newAdjustedStoufferPvalues = defaultdict(dict)

            # Iterate over each database (adjusting it independently)
            for db_name, db_pvalues in pvalues.items():
                # Each pathway has a different set of matching omics and thus, Stouffer weights.
                # The new Stouffer p-value will be computed for each pathway, even those that are currently hidden.
                for pathway_id, pathway_pvalues in db_pvalues.items():
                    # Select those with a proper p-value number and present in Stouffer weights
                    valid_pvalues = {omic: pvalue for omic, pvalue in pathway_pvalues.items() if pvalue != "-" and omic in newStoufferWeights.keys()}

                    # Make sure to pass the Stouffer weights in the same order as the p-values
                    newStoufferValue = calculateStoufferCombinedPvalue(valid_pvalues.values(), [newStoufferWeights[omicName] for omicName in valid_pvalues.keys()])

                    newStoufferPvalues[db_name][pathway_id] = newStoufferValue

                # Adjust the new Stouffer p-values passing only those pathways that are currently visible
                newAdjustedStoufferPvalues[db_name] = adjustPvalues({pathway: pvalue for pathway, pvalue in newStoufferPvalues[db_name].items() if pathway in visiblePathways})

            response.setContent({
                "success": True,
                "stoufferPvalues": newStoufferPvalues,
                "adjustedStoufferPvalues": newAdjustedStoufferPvalues
            })
        else:

            # No new stouffer weights, just recalculate the provided p-values

            # Iterate over each database (adjusting it independently)
            adjustedPvaluesByOmic = defaultdict()

            for db_name, db_pvalues in pvalues.items():
                pvaluesByOmic = defaultdict(dict)

                for pathway, pathwayPvalues in db_pvalues.items():
                    for omic, omicPvalue in pathwayPvalues.items():
                        # Skip those in which there is no pValue (no matching in the pathway for that omic)
                        if omicPvalue != '-':
                            pvaluesByOmic[omic][pathway] = omicPvalue

                adjustedPvaluesByOmic[db_name] = {omic: adjustPvalues(omic_pvalues) for omic, omic_pvalues in pvaluesByOmic.items()}

            response.setContent({
                "success": True,
                "adjustedPvalues": adjustedPvaluesByOmic
            })

    except Exception as ex:
        handleException(response, ex, __file__ , "pathwayAcquisitionAdjustPvalues")
    finally:
        return response


def _hubOwnedJob(jobID, userID, tag):
    """Load a job for a hub route, refusing it unless the caller may see it.

    Both hub routes need the same check, and the check is the reason they
    exist as separate endpoints at all: /check_job_status ships the same job's
    hub payload with no session and no ownership test. Writing it once means a
    later route cannot quietly ship without it.

    Returns (jobInstance, None) on success, or (None, refusalDict).
    """
    jobInstance = JobInformationManager().loadJobInstance(jobID)
    if jobInstance is None:
        return None, {"success": False,
                      "errorMessage": "Job " + str(jobID) + " not found."}
    if (str(jobInstance.getUserID()) != "None"
            and jobInstance.getUserID() != userID
            and not jobInstance.getAllowSharing()):
        logging.info("%s - JOB %s DOES NOT BELONG TO USER %s",
                     tag, jobID, str(userID))
        return None, {"success": False,
                      "errorMessage": "Invalid Job ID for current user."}
    return jobInstance, None


_KEGG_COMPOUND_NAMES = None


def _isDisplayableCompoundName(name, compoundID):
    """Whether a kegg_compounds row carries something worth showing a reader.

    That collection stores one document per NAME, and "name" includes the KEGG
    id itself and the compound's ChEBI ids -- which is why an upload keyed by
    id comes back named "C00001, C00001". Measured on the live table: C00002
    holds ATP, "Adenosine 5'-triphosphate", "chebi:15422" and "15422".
    """
    name = str(name or "").strip()
    if not name or name == compoundID:
        return False
    if name.isdigit() or name.lower().startswith("chebi:"):
        return False
    return True


def _keggCompoundNames():
    """KEGG compound id -> a readable name, loaded once per process.

    The hub panel had nothing better to print than "C12145". mappingComp is no
    help: it holds the name the USER uploaded, and a metabolomics file keyed by
    KEGG id makes that the id again. The real names are in
    global-paintomics.kegg_compounds, which the mapper already reads.

    That collection stores one document per NAME, and among them the id itself
    and the compound's ChEBI ids -- which is why an upload keyed by id comes
    back named "C00001, C00001". Those rows are skipped here; the first real
    name in natural order wins (C00001 -> H2O, C00002 -> ATP, C12145 ->
    Phytoceramide).

    93k documents projected to two fields, scanned once and kept: ~19k entries.
    Failure is not fatal -- the panel falls back to ids.
    """
    global _KEGG_COMPOUND_NAMES
    if _KEGG_COMPOUND_NAMES is not None:
        return _KEGG_COMPOUND_NAMES

    names = {}
    client = None
    try:
        from src.common.FeatureNamesToKeggIDsMapper import getConnectionByOrganismCode
        client, db = getConnectionByOrganismCode("global")
        for doc in db.kegg_compounds.find({}, {"_id": 0, "id": 1, "name": 1}):
            compoundID = doc.get("id")
            if not compoundID or compoundID in names:
                continue
            name = str(doc.get("name") or "").strip()
            # First acceptable name in natural order wins: C00001 -> H2O,
            # C00002 -> ATP, C12145 -> Phytoceramide.
            if _isDisplayableCompoundName(name, compoundID):
                names[compoundID] = name
        logging.info("HUB_NAMES - %d KEGG compound names cached", len(names))
    except Exception as ex:
        logging.warning("HUB_NAMES - could not load KEGG compound names (%s); "
                        "the panel will show ids.", str(ex))
    finally:
        if client is not None:
            client.close()

    _KEGG_COMPOUND_NAMES = names
    return names


#: One panel's metabolite list. Well above the largest real job seen (213),
#: and low enough that a malformed request cannot ask for the whole table.
_HUB_NAMES_MAX_IDS = 5000


def pathwayAcquisitionHubNames(request, response, QUEUE_INSTANCE):
    """Readable names for the compound ids the caller names.

    Fetched once when the panel mounts, so the metabolite list can be titled by
    name rather than by id. Per-node naming inside the network comes from the
    same map, which is why it is one bulk call and not one call per node.

    The ids come from the REQUEST, not from jobInstance.hubAnalysisResult.
    Reading them off the loaded job was the obvious implementation and it
    returned nothing: jobs stored before the schema-2 rewrite still hold
    headerless 8-element LISTS in Mongo (job fh304774Lw: 860 of them), and only
    pathwayAcquisitionRecoverJob re-scores them on the way out. The client is
    holding the upgraded rows already -- it renders the list from them -- so
    asking it removes the dependency on which schema happens to be on disk.
    """
    try:
        jobID = request.form.get("jobID")
        userID = request.cookies.get("userID")

        jobInstance, refusal = _hubOwnedJob(jobID, userID, "HUB_NAMES")
        if refusal is not None:
            response.setContent(refusal)
            return response

        requested = request.form.get("ids") or ""
        ids = [value.strip() for value in requested.split(",") if value.strip()]
        if len(ids) > _HUB_NAMES_MAX_IDS:
            ids = ids[:_HUB_NAMES_MAX_IDS]
            logging.warning("HUB_NAMES - %s asked for more than %d ids; "
                            "the rest keep their KEGG id as the label.",
                            jobID, _HUB_NAMES_MAX_IDS)

        table = _keggCompoundNames()
        names = {}
        for compoundID in ids:
            if compoundID in table:
                names[compoundID] = table[compoundID]

        # The scoring contract the server is on. The client caches the job
        # model in IndexedDB, so a browser that loaded this job before an
        # upgrade keeps serving rows scored under the old definition -- which
        # is exactly what HUB_SCHEMA_VERSION exists to prevent, and the check
        # only ever ran on the server's recovery path.
        from src.common.KeggGraph.scorer import HUB_SCHEMA_VERSION
        response.setContent({"success": True, "names": names,
                             "hubSchema": HUB_SCHEMA_VERSION})
    except Exception as ex:
        logging.error("HUB_NAMES - %s", str(ex))
        response.setContent({"success": False, "errorMessage": str(ex)})
    return response


def pathwayAcquisitionHubSubgraph(request, response, QUEUE_INSTANCE):
    """The induced subgraph behind one row of the hub-analysis table.

    The graph has always existed on the server and never reached the browser:
    compoundRegulateFeatures ships node SETS with no pairs, no direction, no edge
    types and no intermediate hops, so a client cannot tell whether a radius-3
    gene reaches the metabolite via gene X or gene Y. That is why no network was
    ever drawn.

    Ownership is checked the way pathwayAcquisitionRecoverJob does. The endpoint
    that ships hubAnalysisResult today, /check_job_status, checks nothing at all
    -- a separate and broader fix; this route does not inherit it.
    """
    try:
        jobID = request.form.get("jobID")
        compoundID = request.form.get("compoundID")
        level = max(1, min(4, int(request.form.get("level", 1) or 1)))
        budget = max(1, min(2000, int(request.form.get("maxEdges", 400) or 400)))
        perRing = max(5, min(200, int(request.form.get("perRing", 40) or 40)))
        userID = request.cookies.get("userID")

        jobInstance, refusal = _hubOwnedJob(jobID, userID, "HUB_SUBGRAPH")
        if refusal is not None:
            response.setContent(refusal)
            return response

        from src.common.KeggGraph import store
        graph = store.get_graph(jobInstance.getOrganism())
        if graph is None:
            response.setContent({"success": False,
                                 "errorMessage": "No interaction network is "
                                                 "installed for this organism."})
            return response

        # The per-ring sample must keep the DE features first: DE concentration
        # is the claim the panel exists to show, and a sample that dropped the
        # DE genes would misrepresent it. Only the server knows which features
        # are relevant for THIS job, so the priority set is built here rather
        # than left to the client, which never receives the dropped nodes.
        #
        # Any gene-based omic counts, not just one named "Gene expression".
        # "Gene expression" is only the default label the upload form suggests
        # for the first omic; a job whose omics are named "RNA-seq" and
        # "Proteomics" is not a job without differential expression. Asking
        # isRelevant() rather than testing `relevant` for truth is the same
        # point: `relevant` is a LIST, and a list of all-False is truthy.
        priority = set()
        try:
            for geneID, gene in (jobInstance.inputGenesData or {}).items():
                for values in (gene.omicsValues or []):
                    if values.isRelevant() or values.isRelevantAssociation():
                        priority.add(geneID)
                        break
            for compID, comp in (jobInstance.inputCompoundsData or {}).items():
                for values in (comp.omicsValues or []):
                    if values.isRelevant() or values.isRelevantAssociation():
                        priority.add(compID)
                        break
        except Exception as ex:
            logging.warning("HUB_SUBGRAPH - could not build the DE priority set "
                            "for %s (%s); sampling by degree alone.", jobID, str(ex))

        payload = graph.subgraph(compoundID, level, budget, priority=priority,
                                 per_ring=perRing)

        # Names for the compound nodes in THIS subgraph. /pa_hub_names covers
        # the scored list; a ring can also hold compounds the job never
        # measured and so never scored, and those would otherwise be the only
        # nodes still labelled with a bare id.
        table = _keggCompoundNames()
        payload["names"] = {
            node["id"]: table[node["id"]]
            for node in payload.get("nodes", [])
            if node.get("type") == "compound" and node.get("id") in table
        }

        payload["success"] = True
        response.setContent(payload)
    except Exception as ex:
        logging.error("HUB_SUBGRAPH - %s", str(ex))
        response.setContent({"success": False, "errorMessage": str(ex)})
    return response


def pathwayAcquisitionHubFeature(request, response, QUEUE_INSTANCE):
    """Every omic measured for ONE feature in the hop-ring network.

    globalExpressionData -- the only expression payload the panel had -- carries
    ``omicsValues[0]`` and nothing else (see
    PathwayAcquisitionJob.getGlobalExpressionData). On a job with four
    gene-based omics that is a quarter of the data, drawn with no hint that the
    other three exist, while the pathway views next to it show all four.

    Shipping every omic for every feature instead would multiply a payload that
    already measures ~4 MB on a job this size, for data almost none of which is
    ever looked at. One clicked feature is a few hundred bytes, so it is
    fetched here on demand -- the same derive-when-asked rule the graph itself
    follows.
    """
    try:
        jobID = request.form.get("jobID")
        featureID = request.form.get("featureID")
        featureType = (request.form.get("featureType") or "gene").lower()
        userID = request.cookies.get("userID")

        jobInstance, refusal = _hubOwnedJob(jobID, userID, "HUB_FEATURE")
        if refusal is not None:
            response.setContent(refusal)
            return response

        source = (jobInstance.inputCompoundsData if featureType == "compound"
                  else jobInstance.inputGenesData) or {}
        feature = source.get(featureID)
        if feature is None:
            # Not an error: most nodes in a radius-4 ring were never measured,
            # and the client draws a "how it connects" panel for those. Saying
            # so with success=True keeps that path off the error branch.
            response.setContent({"success": True, "id": featureID,
                                 "type": featureType, "name": "", "omics": []})
            return response

        omics = []
        for values in (feature.omicsValues or []):
            entry = {
                # keggName is what the heatmap prints as the row label; it
                # lives on the OmicValue client-side, so it is repeated per
                # omic rather than sent once beside them.
                "keggName": feature.name,
                "omicName": values.omicName,
                "inputName": values.inputName,
                "originalName": values.originalName,
                "values": values.values,
                "relevant": values.relevant,
                "relevantAssociation": values.relevantAssociation
            }
            # Only when populated. adaptBSON turns None into the STRING "None",
            # and paValuesForHeader would then see a non-array sampleValues.
            if values.sampleValues is not None:
                entry["sampleValues"] = values.sampleValues
            if values.sampleRelevant is not None:
                entry["sampleRelevant"] = values.sampleRelevant
            omics.append(entry)

        response.setContent({"success": True, "id": featureID,
                             "type": featureType, "name": feature.name,
                             "omics": omics})
    except Exception as ex:
        logging.error("HUB_FEATURE - %s", str(ex))
        response.setContent({"success": False, "errorMessage": str(ex)})
    return response

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
#     Rafael Hernandez de Diego <paintomics4@gmail.com>
#     Ana Conesa Cegarra
#     and others
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#
#**************************************************************

import logging
import logging.config

from src.classes.JobInstances.MiRNA2GeneJob import MiRNA2GeneJob
from src.common.UserSessionManager import UserSessionManager
from src.common.JobInformationManager import JobInformationManager
from src.common.ServerErrorManager import handleException
from src.common import ExampleDatasets

from src.conf.serverconf import CLIENT_TMP_DIR

def fromMiRNAtoGenes_STEP1(REQUEST, RESPONSE, QUEUE_INSTANCE, JOB_ID, EXAMPLE_FILES_DIR, exampleMode=False):
    """
    This function corresponds to FIRST PART of the FIRST step in the MiRNA2GeneJob process.
    First, it takes a Request object which contains the fields of the form that started the process.
    This is a summary for the steps in the process:
        Step 0. VARIABLE DECLARATION
        Step 1. CHECK IF VALID USER SESSION
        Step 2. CREATE THE NEW INSTANCE OF JOB
        Step 3. SAVE THE UPLOADED FILES
        Step 4. READ PARAMS
        Step 5. QUEUE THE JOB INSTANCE
        Step 6. RETURN THE NEW JOB ID

    @param {Request} REQUEST
    @param {Response} RESPONSE
    @param {RQ QUEUE} QUEUE_INSTANCE
    @param {String} JOB_ID
    @param {Boolean} exampleMode
    @returns Response
    """
    #TODO: ALLOWED_EXTENSIONS http://flask.pocoo.org/docs/0.10/patterns/fileuploads/
    #TODO: secure_filename
    #****************************************************************
    #Step 0. VARIABLE DECLARATION
    #The following variables are defined:
    #  - jobInstance: instance of the MiRNA2GeneJob class. Contains all the information for the current job.
    #  - userID: the ID for the user
    #****************************************************************
    jobInstance = None
    userID= None

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
        jobInstance = MiRNA2GeneJob(JOB_ID, userID, CLIENT_TMP_DIR)
        jobInstance.initializeDirectories()
        logging.info("STEP1 - NEW JOB SUBMITTED " + jobInstance.getJobID())

        #****************************************************************
        # Step 3. SAVE THE UPLOADED FILES
        #****************************************************************
        formFields   = REQUEST.form

        if isExampleRequest is False:
            logging.info("STEP1 - FILE UPLOADING REQUEST RECEIVED")
            uploadedFiles  = REQUEST.files

            logging.info("STEP1 - READING FILES....")
            JobInformationManager().saveFiles(uploadedFiles, formFields, userID, jobInstance, CLIENT_TMP_DIR,  EXAMPLE_FILES_DIR)
            logging.info("STEP1 - READING FILES....DONE")

        elif isExampleRequest:
            #****************************************************************
            # Step 2.REGISTER THE BUNDLED EXAMPLE FILES
            #****************************************************************
            # The manifest carries the regulator/target pairing that used to be
            # implicit here: the miRNA omic plus a "Gene expression" omic whose
            # only job is to be the target, plus the miRBase->Ensembl reference.
            # validateInput picks the miRNA omic as "the one not called Gene
            # expression", so the names in the manifest are load-bearing.
            logging.info("STEP1 - EXAMPLE MODE SELECTED (scenario: %s)",
                         scenarioId or "default")
            scenario = ExampleDatasets.applyScenario(
                jobInstance, EXAMPLE_FILES_DIR,
                scenarioId or ExampleDatasets.defaultScenarioFor(
                    EXAMPLE_FILES_DIR, "mirna2genes"))
            logging.info("STEP1 - EXAMPLE '%s' REGISTERED", scenario["id"])
        else:
            # See Bed2GenesServlet: a bare NotImplementedError reaches the user
            # as "ERROR MESSAGE: " with nothing after it.
            raise NotImplementedError(
                "Unrecognised example mode %r for miRNA2Genes: expected no "
                "value for an upload, 'example' for the default dataset, or "
                "'example/<dataset-id>' for a specific one."
                % (exampleMode,))

        #****************************************************************
        # Step 4. READ PARAMS
        #****************************************************************
        namePrefix = formFields.get("name_prefix")

        # Same hazard as Bed2GenesServlet: every parameter is looked up as
        # namePrefix + "_something", so a missing prefix raised TypeError on
        # the first concatenation rather than naming the missing field.
        if not namePrefix:
            raise UserWarning(
                "Missing name_prefix parameter: PaintOmics cannot tell which "
                "omic's settings to read for the miRNA-to-genes conversion.")

        logging.info("STEP2 - INPUT VALUES ARE:")
        jobInstance.omicName= formFields.get(namePrefix + "_omic_name", "miRNA-seq")
        logging.info("  - omicName: " + jobInstance.omicName)
        jobInstance.report= formFields.get(namePrefix + "_report", "all")
        logging.info("  - report: " + jobInstance.report)
        jobInstance.score_method= formFields.get(namePrefix + "_score_method", "kendall")
        logging.info("  - score_method: " + jobInstance.score_method)
        jobInstance.selection_method= formFields.get(namePrefix + "_selection_method", "negative_correlation")
        logging.info("  - selection_method: " + jobInstance.selection_method)
        jobInstance.cutoff= formFields.get(namePrefix + "_cutoff", 0.5)
        logging.info("  - cutoff: " + str(jobInstance.cutoff))
        jobInstance.enrichment = formFields.get(namePrefix + "_enrichment_pre", 'genes')
        logging.info("  - Enrichment: " + str(jobInstance.enrichment))

        #************************************************************************
        # Step 4. Queue job
        #************************************************************************
        QUEUE_INSTANCE.enqueue(
            fn=fromMiRNAtoGenes_STEP2,
            args=(jobInstance, userID, exampleMode, RESPONSE, formFields),
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
        handleException(RESPONSE, ex, __file__ , "fromMiRNAtoGenes_STEP1")
    finally:
        return RESPONSE


def fromMiRNAtoGenes_STEP2(jobInstance, userID, exampleMode, RESPONSE, formFields):
    """
    This function corresponds to SECOND PART of the FIRST step in the MiRNA2GeneJob process.
    Given a JOB INSTANCE, first executes the MiRNA2Gene function (map miRNAs to genes)
    and finally generates the response.
    This code is executed at the PysQlite Queue.

    This is a summarization for the steps in the process:
        Step 1. PROCESS THE FILES DATA
        Step 2. SAVE THE JOB INSTANCE AT THE DATABASE
        Step 3. GENERATE RESPONSE AND FINISH

    @param {MiRNA2GeneJob} jobInstance
    @param {Response} RESPONSE
    @param {String} userID
    @param {Boolean} exampleMode

    @returns Response
    """
    try :
        #****************************************************************
        # Step 0.VALIDATE THE FILES DATA
        #****************************************************************
        logging.info("STEP0 - VALIDATING INPUT..." )
        jobInstance.validateInput()
        logging.info("STEP1 - VALIDATING INPUT...DONE" )

        #****************************************************************
        # Step 1.PROCESS THE FILES DATA
        #****************************************************************
        logging.info("STEP1 - Executing MiRNA2Gene function...")
        fileNames=jobInstance.fromMiRNA2Genes()
        # Updata jobInstance

        logging.info("STEP1 - Executing MiRNA2Gene function... DONE")

        #************************************************************************
        # Step 2. Save the jobInstance in the MongoDB
        #************************************************************************
        logging.info("STEP1 - SAVING JOB DATA..." )
        JobInformationManager().storeJobInstance(jobInstance, 1)
        #TODO: JOB DESCRIPTION?
        logging.info("STEP1 - SAVING JOB DATA...DONE" )


        #************************************************************************
        # Step 3. Update the response content
        #************************************************************************

        RESPONSE.setContent({
            "success": True,
            "jobID":jobInstance.getJobID(),
            "compressedFileName": fileNames[0],
            "mainOutputFileName":  fileNames[1],
            "secondOutputFileName":  fileNames[2],
            "thirdOutputFileName":  fileNames[3],
            "fourthOutputFileName":  fileNames[4],
            "description": jobInstance.description,
            "enrichment": jobInstance.enrichment
        })

    except Exception as ex:
        #****************************************************************
        # DELETE JOB FROM USER DIRECTORY
        #****************************************************************
        jobInstance.cleanDirectories(remove_output=True)

        handleException(RESPONSE, ex, __file__ , "fromMiRNAtoGenes_STEP2")

    finally:
        return RESPONSE


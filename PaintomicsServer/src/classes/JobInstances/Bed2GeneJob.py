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

from src.classes.Job import Job
from src.classes.Feature import OmicValue, Gene
from src.servlets.DataManagementServlet import copyFile
from src.common.Util import ensure_utf8
from src.common.bioscripts.DHS_exon_association import run as run_DHS_exon_association
from src.conf.serverconf import MAX_WAIT_THREADS #MULTITHREADING
# Aliased: this class's __init__ takes a parameter literally named
# CLIENT_TMP_DIR, and a module-level name spelled the same way is a trap for
# the next person to touch it.
from src.conf.serverconf import CLIENT_TMP_DIR as SERVER_CLIENT_TMP_DIR

from os import path as os_path, mkdir as os_mkdir
from csv import reader as csv_reader
from random import randint

import shutil

class Bed2GeneJob(Job):
    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self, jobID, userID, CLIENT_TMP_DIR):
        super(Bed2GeneJob, self).__init__(jobID, userID, CLIENT_TMP_DIR)
        self.omicName = None
        self.presortedGTF  = False

        self.report               = "gene"
        self.distance             = 10
        self.tss                  = 200
        self.promoter             = 1300
        self.geneAreaPercentage   = 90
        self.regionAreaPercentage = 50
        # Deliberately NOT forwarded by getOptions(): this list drops "TTS",
        # while RGmatch emits eight areas and uses the list to break ties. A
        # short list leaves TTS hits unranked, so sending it would degrade the
        # tie-break rather than configure it. No form field feeds this yet
        # (see the "#rules" placeholder in Bed2GenesServlet), so RGmatch's own
        # complete eight-area default is the right thing to run with. Restore
        # the missing "TTS" before wiring this up.
        self.rules                = ["TSS","1st_EXON","PROMOTER","INTRON","GENE_BODY","UPSTREAM","DOWNSTREAM"]
        self.geneIDtag            = "gene_id"
        self.ignoreMissing        = True
        self.enrichment           = "genes"

        self.summarizationMethod  = "mean"
        self.reportRegions        = ["all"]

    def getOptions(self):
        """Build the options dict for DHS_exon_association.run().

        The keys must be the ones run() reads (DHS_exon_association.RUN_OPTION_KEYS),
        NOT the getopt flag names: run() looks every setting up with
        .get(key, default), so a key it does not recognise used to be dropped
        without a sound. Two were being dropped -- "report" (run() calls it
        "level") and "gene" (run() calls it "gene_id_tag") -- which is why the
        Report and GTF-tag settings on the form had no effect whatsoever.
        run() now rejects unknown keys, so a future rename fails loudly here.
        """
        return {
            "presortedGTF": self.presortedGTF,
            "level": self.report,
            # The form asks for a distance in kb ("Distance (kb)", default 10)
            # but run() compares it against genomic coordinates, in bp. The
            # kb->bp scaling lived only in the -q/--distance getopt branch, so
            # the web app searched 10 bp around each region and produced zero
            # associations for every user. Scale here, in the sender that
            # speaks kb -- run()'s "distance" stays a plain bp value for every
            # caller.
            "distance": int(round(float(self.distance) * 1000)),
            "tss": self.tss,
            "promoter": self.promoter,
            "perc_area": self.geneAreaPercentage,
            "perc_region": self.regionAreaPercentage,
            "gene_id_tag": self.geneIDtag,
            "ignore_missing": self.ignoreMissing,
            # Where run() keeps its parsed-annotation sidecars. One directory
            # shared by every job, deliberately: the win only exists when the
            # SECOND job against the same GTF finds what the first one parsed,
            # and the built-in annotations (examplefiles/GTF/) are the same
            # files for every user. Nothing job-specific may be written here --
            # the temporal directory is wiped at the end of each job, which is
            # exactly what a cache must survive.
            "cache_dir": SERVER_CLIENT_TMP_DIR + "gtfcache"
        }

    def getJobDescription(self, generate=False, dataFile="", relevantFile="", gtfFile=""):
        if(generate):
            self.description = "Input data:" + os_path.basename(dataFile) + ";Relevant file: " + os_path.basename(relevantFile)  + ";Reference file: " + os_path.basename(gtfFile) + ";"
            self.description += "Params:;Distance=" + str(self.distance) + ";"
            self.description += "TSS region distance=" + str(self.tss)+ ";"
            self.description += "Promoter region distance=" + str(self.promoter)+ ";"
            self.description += "Overlapped gene area(%)=" + str(self.geneAreaPercentage)+ ";"
            self.description += "Overlapped region area(%)=" + str(self.regionAreaPercentage)+ ";"
            self.description += "Summarization method=" + self.summarizationMethod + ";"
            self.description += "Report=" + ",".join(self.reportRegions) + ";"
            self.description += "Ignore Missing=" + str(self.ignoreMissing) + ";"
        return self.description


    def validateInput(self):
        """
        This function check the content for files and returns an error message in case of invalid content

        @returns True if not error
        """
        error = ""

        try:
            self.distance = float(self.distance)
            # A negative search radius reaches run() as a negative bp window
            # and matches nothing, producing the empty association the user
            # then has to diagnose. The getopt path rejects it too (it keeps
            # its default when -q is negative); say so here instead.
            if self.distance < 0:
                error +=  " -  Distance must be a positive numeric value (in kb)"
        except:
            error +=  " -  Distance must be a numeric value"
        # run() only knows these three report levels and now refuses anything
        # else. Catch it while we can still name the field. getattr, because
        # this guard accumulates messages rather than raising: an AttributeError
        # out of here would escape as an internal error and hide the real
        # complaints collected above and below it.
        reportLevel = str(getattr(self, "report", "gene")).lower()
        if reportLevel not in ("exon", "transcript", "gene"):
            error +=  " -  Report must be one of: exon, transcript, gene"
        else:
            self.report = reportLevel
        try:
            self.tss = float(self.tss)
        except:
            error +=  " -  TSS region distance must be a numeric value"
        try:
            self.promoter = float(self.promoter)
        except:
            error +=  " -  Promoter region distance must be a numeric value"
        try:
            self.geneAreaPercentage = float(self.geneAreaPercentage)
            if self.geneAreaPercentage < 0 or self.geneAreaPercentage > 100:
                error +=  " -  Overlapped gene area must be a numeric value between 0 and 100"
        except:
            error +=  " -  Overlapped gene area must be a numeric value between 0 and 100"
        try:
            self.regionAreaPercentage = float(self.regionAreaPercentage)
            if self.regionAreaPercentage < 0 or self.regionAreaPercentage > 100:
                error +=  " -  Overlapped region area must be a numeric value between 0 and 100"
        except:
            error +=  " -  Overlapped region area must be a numeric value between 0 and 100"

        logging.info("VALIDATING REGION BASED FILES..." )
        nConditions, error = self.validateFile(self.geneBasedInputOmics[0], -1, error)

        # The user-supplied annotation/GTF travels as a referenceInput and was
        # never passed through the encoding pass, so a latin-1 byte in an
        # attribute field crashed the association script instead of validating.
        # Gzipped references are legitimate (the script gunzips them itself)
        # and are not text, so they are left alone.
        # getattr, not a direct call: jobs restored from older serialisations
        # (and the validation tests' stubs) may not carry referenceInputs.
        try:
            referenceInputs = self.getReferenceInputs() or []
        except AttributeError:
            referenceInputs = []
        for referenceInput in referenceInputs:
            referenceFileName = referenceInput.get("inputDataFile", "") or ""
            if not referenceFileName or referenceFileName.endswith(".gz"):
                continue
            referencePath = "{path}/{file}".format(
                path=self.getInputDir(), file=referenceFileName)
            if os_path.isfile(referencePath):
                encodingError = ensure_utf8(referencePath)
                if encodingError is not None:
                    error += (" - Errors detected while processing " +
                              os_path.basename(referenceFileName) + ": " +
                              encodingError + ".\n")

        if error != "":
            raise Exception("Errors detected in input files, please fix the following issues and try again:" + error)

        return True

    def validateFile(self, inputOmic, nConditions, error):
        """
        This function...

        @param {type}
        @returns
        """
        valuesFileName= inputOmic.get("inputDataFile")
        relevantFileName= inputOmic.get("relevantFeaturesFile", "")
        omicName = inputOmic.get("omicName")

        if(inputOmic.get("isExample", False) == True):
            return nConditions, error
        else:
            valuesFileName = "{path}/{file}".format(path=self.getInputDir(), file=valuesFileName)
            relevantFileName = "{path}/{file}".format(path=self.getInputDir(), file=relevantFileName)

        #*************************************************************************
        # STEP 1. VALIDATE THE RELEVANT FEATURES FILE
        #*************************************************************************

        # Normalise the encoding before the first read, exactly as
        # PathwayAcquisitionJob does for the main upload path. Without it these
        # files were opened with a bare open(..., 'r'), which decodes using the
        # locale -- UTF-8 on the server -- so a spreadsheet exported as
        # cp1252/latin-1 raised
        #     UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd1
        # out of a validation routine, reaching the user as an internal error
        # rather than a message about the file. A gene name with an accent in a
        # file saved from Excel is enough to do it.
        encodingFailed = False
        for encodingTarget in (relevantFileName, valuesFileName):
            if os_path.isfile(encodingTarget):
                encodingError = ensure_utf8(encodingTarget)
                if encodingError is not None:
                    encodingFailed = True
                    error += (" - Errors detected while processing " +
                              os_path.basename(encodingTarget) + ": " +
                              encodingError + ".\n")
        # A recorded encoding failure means the file is still non-UTF-8 on
        # disk: falling through to the readers below crashed with the very
        # UnicodeDecodeError the message above was written to prevent.
        if encodingFailed:
            return nConditions, error

        logging.info("VALIDATING RELEVANT REGIONS FILE (" + omicName + ")..." )
        if os_path.isfile(relevantFileName):
            with open(relevantFileName, 'r', encoding='utf-8-sig') as inputDataFile:
                nLine = -1
                erroneousLines = {}

                for line in csv_reader(inputDataFile, delimiter="\t"):
                    nLine = nLine+1
                    #TODO: HACER ALGO CON EL HEADER?
                    #*************************************************************************
                    # STEP 1.1 CHECK IF IT IS HEADER, IF SO, IGNORE LINE
                    #*************************************************************************
                    if(nLine == 0):
                        try:
                            int(line[1])
                        except:
                            continue

                    #**************************************************************************************
                    # STEP 2.2 IF LINE LENGTH DOES NOT MATCH WITH EXPECTED NUMBER OF CONDITIONS, ADD ERROR
                    #**************************************************************************************
                    if len(line) != 3:
                        erroneousLines[nLine] = " - Line " + str(nLine) + ": expected 3 columns but found " + str(len(line)) + "; "

                    if len(erroneousLines)  > 9:
                        error +=  " - Too many errors detected while processing " + inputOmic.get("relevantFeaturesFile") + ", skipping remaining lines...\n"
                        break

            inputDataFile.close()

            #*************************************************************************
            # STEP 3. CHECK THE ERRORS AND RETURN
            #*************************************************************************
            if len(erroneousLines)  > 0:
                error += " - Errors detected while processing " + inputOmic.get("relevantFeaturesFile") + ":\n"
                for k in sorted(erroneousLines.keys()):
                    error+= erroneousLines.get(k) + "\n"


        #*************************************************************************
        # STEP 2. VALIDATE THE VALUES FILE
        #*************************************************************************
        logging.info("VALIDATING VALUES FILE (" + omicName + ")..." )

        #IF THE USER UPLOADED VALUES FOR GENE EXPRESSION
        if os_path.isfile(valuesFileName):
            with open(valuesFileName, 'r', encoding='utf-8-sig') as inputDataFile:
                nLine = -1
                erroneousLines = {}

                for line in csv_reader(inputDataFile, delimiter="\t"):
                    nLine = nLine+1
                    #TODO: HACER ALGO CON EL HEADER?
                    #*************************************************************************
                    # STEP 2.1 CHECK IF IT IS HEADER, IF SO, IGNORE LINE
                    #*************************************************************************
                    if(nLine == 0):
                        try:
                            float(line[1])
                        except Exception:
                            if line[0][0] != "#":
                                erroneousLines[nLine] =  " - Line " + str(nLine) + ": Header must start with a HASH symbol (#)."
                            continue

                    if nConditions == -1:
                        if len(line) < 4:
                            erroneousLines[nLine] =  " - Line " + str(nLine) + " expected at least 4 columns, but found one."
                            break
                        nConditions = len(line)

                    #**************************************************************************************
                    # STEP 2.2 IF LINE LENGTH DOES NOT MATCH WITH EXPECTED NUMBER OF CONDITIONS, ADD ERROR
                    #**************************************************************************************
                    if(nConditions != len(line) and len(line)>0):
                        erroneousLines[nLine] = " - Line " + str(nLine) + ": expected " +  str(nConditions) + " columns but found " + str(len(line)) + "; "

                    #**************************************************************************************
                    # STEP 2.2 IF CONTAINS NOT VALID VALUES, ADD ERROR
                    #**************************************************************************************
                    try:
                        list(map(float, line[3:len(line)]))
                    except:
                        erroneousLines[nLine] = erroneousLines.get(nLine,  "   > Line " + str(nLine) + ":") + "Line contains invalid values."

                    if len(erroneousLines)  > 9:
                        error +=  " - Too many errors detected while processing " + inputOmic.get("inputDataFile") + ", skipping remaining lines...\n"
                        break

            inputDataFile.close()

            #*************************************************************************
            # STEP 3. CHECK THE ERRORS AND RETURN
            #*************************************************************************
            if len(erroneousLines)  > 0:
                error += " - Errors detected while processing " + inputOmic.get("inputDataFile") + ":\n"
                for k in sorted(erroneousLines.keys()):
                    error+= erroneousLines.get(k) + "\n"
        else:
            error += " - Error while processing " + omicName + ": File " + inputOmic.get("inputDataFile") + "not found.\n"

        return nConditions, error

    @staticmethod
    def countAssociationRows(associationFile):
        """Count the data rows in an RGMatch association file.

        The first line is always the column header, written by
        DHS_exon_association.run() before any region is examined, so it is not
        evidence that anything was found. Counted line by line rather than with
        readlines(): a permissive Distance over a real genome can associate
        millions of regions and this runs on the request thread.

        @param associationFile absolute path to the RGMatch output
        @returns number of data rows, 0 if the file is absent or header-only
        """
        if not os_path.isfile(associationFile):
            return 0

        nRows = 0
        with open(associationFile, 'r') as associationHandler:
            for nLine, line in enumerate(associationHandler):
                if nLine == 0:
                    continue
                # Trailing newlines and any blank separator line are not data.
                if line.strip() != "":
                    nRows += 1

        return nRows

    ##*************************************************************************************************************
    # This function...
    #
    # @param {type}
    # @returns
    ##*************************************************************************************************************
    def fromBED2Genes(self):
        #STEP 1. GET THE FILES PATH AND PREPRARE THE OPTIONS
        logging.info("READING FILES...")

        # An upload registers a reference input only when one was actually
        # sent: JobInformationManager adds it under
        # `matchingType.lower() == "reference_file"`. Submit Regions2Genes
        # without an annotations file and this list is empty, so indexing it
        # raised
        #     IndexError: list index out of range
        # which handleException hands to the browser verbatim as the whole
        # error message. Reproduced directly against a job with no reference
        # inputs.
        referenceInputs = self.getReferenceInputs() or []
        if not referenceInputs:
            raise Exception(
                "No annotations file (GTF) was provided. Regions2Genes needs "
                "one to map regions onto genes.")

        referenceFileName = referenceInputs[0].get("inputDataFile") or ""

        #CHECK IF THE FILE IS AN INPUT FILE OR AN INBUILT GTF FILE
        uploadedPath = self.getInputDir() + referenceFileName
        gtfFile = uploadedPath
        if not os_path.isfile(gtfFile):
            gtfFile = referenceFileName
        if not os_path.isfile(gtfFile):
            # Both candidates named, because the one case that reaches this in
            # practice is a deployment where the bundled example GTF was never
            # fetched -- deploy/fetch-example-gtf.sh is a manual step, wired
            # into no automated deploy -- and "Reference file not found." on
            # its own tells whoever is looking neither which file was missing
            # nor that it is the example data rather than their upload.
            raise Exception(
                "Reference file not found. Looked for %r and %r."
                % (uploadedPath, referenceFileName))

        inputOmic = self.getGeneBasedInputOmics()[0]
        dataFile = inputOmic.get("inputDataFile")
        relevantFile = inputOmic.get("relevantFeaturesFile")

        if(inputOmic.get("isExample", False) == False):
            dataFile = "{path}/{file}".format(path=self.getInputDir(), file=dataFile)
            relevantFile = "{path}/{file}".format(path=self.getInputDir(), file=relevantFile)

        if not os_path.isdir(self.getTemporalDir()):
            os_mkdir(self.getTemporalDir())

        tmpFile = self.getTemporalDir() +"/RGMatch_output.txt"

        #STEP 2. CALL TO DHS_exon_association SCRIPT AND GENERATE ASSOCIATION BETWEEN REGIONS AND GENES
        logging.info("STARTING DHS_exon_association PROCESS.")

        from multiprocessing import Process, Queue

        # Create a queue to catch the subprocess errors
        managed_queue = Queue()

        # Initialize the process
        thread = Process(target=run_DHS_exon_association, args=(gtfFile, dataFile, tmpFile, None, self.getOptions(), managed_queue))
        thread.start()

        # Retrieve the possible errors (or else the queue will block it)
        queue_content = managed_queue.get(True, MAX_WAIT_THREADS)

        thread.join(MAX_WAIT_THREADS)

        del thread

        if queue_content is not None:
            raise queue_content

        # An empty queue message only means run() returned without raising. It
        # says nothing about how many associations were found: run() writes the
        # output header before it examines a single region, so a run that
        # matched nothing still leaves a well-formed, header-only file behind.
        # That file was accepted here, registered as an omic with zero
        # features, and only blew up a step later as
        #   "The file B2G_output_<date>.tab does not seem to have any feature
        #    lines."
        # -- a message about an internal artefact that names neither the cause
        # nor the setting that governs it. Stop here instead, and name the
        # Distance the search actually used.
        if self.countAssociationRows(tmpFile) == 0:
            raise Exception(
                "No region could be associated with any gene, so there is "
                "nothing to analyse. The search used a Distance of " +
                str(self.distance) + " kb around each region, reporting at "
                "the '" + str(self.report) + "' level. Check that the "
                "chromosome names in your regions file match the ones in the "
                "annotation (GTF) file -- '1' and 'chr1' do not match -- and "
                "try a larger Distance.")

        logging.info("STARTING DHS_exon_association PROCESS...Done")

        #STEP 3. PARSE RELEVANT FILE
        logging.info("PROCESSING RELEVANT FEATURES FILE...")
        relevantRegions = self.parseSignificativeFeaturesFile(relevantFile, isBedFormat=True)
        logging.info("PROCESSING RELEVANT FEATURES FILE...DONE")

        #STEP 4. PARSE GENERATED TEMPORAL FILE, GET THE GENE, REGIONS AND QUANTIFICATION
        logging.info("PROCESSING DHS_exon_association OUTPUT...")
        if os_path.isfile(tmpFile):
             with open(tmpFile, 'r') as inputDataFile:
                regionID = geneID = geneRegion = None
                #TODO: (OPTIONAL) if result ordered by gene id -> reduce processing time
                csvReader = csv_reader(inputDataFile, delimiter="\t")
                #IGNORE THE HEADER
                line = next(csvReader)
                #SAVE THE NAME OF THE CONDITIONS (e.g. COND1, COND2,...)
                header = "\t".join(line[11:])

                for line in csvReader:
                    #STEP 5.1 GET THE REGION ID, THE ASSOCIATED GENE ID, THE GENE REGION AND THE QUANTIFICATION VALUES
                    regionID  = line[0]
                    geneID    = line[2]
                    geneRegion= line[5]
                    values    =  list(map(float, line[11:]))

                    #STEP 5.2 CHECK IF GENE REGION IS VALID OR IGNORE ENTRY
                    if self.reportRegions.count("all") == 0 and self.reportRegions.count(geneRegion) == 0:
                        continue

                    #STEP 5.3 CREATE A NEW OMIC VALUE WITH ROW DATA
                    omicValueAux = OmicValue(regionID)
                    #TODO: set omic name with chipseq, dnase,...?
                    omicValueAux.setOmicName(regionID)
                    # OmicValue.relevant contract is list[bool] for multi-condition.
                    omicValueAux.setRelevant([regionID in relevantRegions])
                    omicValueAux.setValues(values)
                    omicValueAux.setOriginalName(geneRegion)

                    #STEP 5.4 CREATE A NEW TEMPORAL GENE INSTANCE
                    geneAux = Gene(geneID)
                    geneAux.setName(line[0])
                    geneAux.addOmicValue(omicValueAux)

                    #STEP 5.5 ADD THE TEMPORAL GENE INSTANCE TO THE LIST OF GENES, IF ALREADY EXISTS, MERGE
                    self.addInputGeneData(geneAux)

                logging.info("PROCESSING DHS_exon_association OUTPUT...DONE")
                #STEP 6. FOR EACH GENE, SUMMARIZE THE QUANTIFICATION VALUES IF ASSOCIATED REGIONS > 1
                regionsToGeneFile = open(self.getTemporalDir() + '/regionsToGene.tab', 'w')
                genesToRegionsFile = open(self.getTemporalDir() + '/genesToRegions.tab', 'w')
                #TODO: USE JOB DATE
                randomSeed = str(randint(0, 1000))
                fileName = "B2G_output_" + self.date + "_" + randomSeed + ".tab"
                bed2genesOutput = open(self.getTemporalDir() + '/' + fileName, 'w')
                fileName = "B2G_relevant_" + self.date + "_" + randomSeed + ".tab"
                bed2genesRelevant = open(self.getTemporalDir() +  '/' + fileName, 'w')

                #PRINT HEADER
                bed2genesOutput.write("Gene name\t"+ header + "\n")
                bed2genesRelevant.write("Gene name\n")

                logging.info("SUMMARIZING GENE QUANTIFICATION...")
                allRegionsValues = relevantRegionsValues = selectedRegions = omicValue = None
                for geneID, gene in self.getInputGenesData().items():
                    #SAVE ALL REGIONS AND ALL RELEVANT REGIONS, IF RELEVANT_REGIONS > 0, THEN THE SUMMARIZATION WILL PERFORMED OVER RELEVANT REGIONS
                    allRegionsValues = []
                    relevantRegionsValues = []

                    #STEP 6.1 WRITE RESULTS TO genesToRegions FILE --> gen_id    region_1 region_2 region_3...
                    regionsAux = ""
                    for omicValue in gene.getOmicsValues():
                        regionsAux += omicValue.getOmicName() + " "
                        #WRITE RESULTS TO genesToRegions FILE -->   region_1  gen_id
                        regionsToGeneFile.write(omicValue.getOmicName() + "\t" + geneID + "\t" + ("*" if omicValue.isRelevant() else "") +"\n")

                        allRegionsValues.append({omicValue.getOmicName(): omicValue.getValues()})
                        if omicValue.isRelevant():
                            relevantRegionsValues.append({omicValue.getOmicName(): omicValue.getValues()})

                    genesToRegionsFile.write(geneID + "\t" + ("*" if len(relevantRegionsValues) > 0 else "") +  "\t" + regionsAux +"\n")

                    #IF AT LEAST ONE OF THE REGION WAS RELEVANT, IGNORE ALL NO RELEVANT REGIONS
                    selectedRegions = allRegionsValues
                    if len(relevantRegionsValues) > 0:
                        if self.summarizationMethod != "none":
                            selectedRegions = relevantRegionsValues
                        #TODO: REMOVE REPEATED GENES
                        # TODO: write the region name also on features file?
                        bed2genesRelevant.write(geneID + "\n")

                    #SUMMARIZE THE QUANTIFICATION FOR CURRENT GENE
                    summarizedValues = self.summarizeValues(selectedRegions) #MUST BE A LIST OF LISTS

                    for dictValue in summarizedValues:
                        if self.summarizationMethod == 'none':
                            geneName = geneID + ':::' + list(dictValue.keys())[0]
                        else:
                            geneName = geneID

                        bed2genesOutput.write(geneName + "\t" + '\t'.join(map(str, list(dictValue.values())[0])) + "\n")


                    #TODO: OMIC NAME??
                    #omicValue = OmicValue("TEST")
                    #omicValue.setValues(summarizedValues.tolist())
                    #gene.setOmicsValues([omicValue])
                logging.info("SUMMARIZING GENE QUANTIFICATION...DONE")

                genesToRegionsFile.close()
                regionsToGeneFile.close()
                bed2genesOutput.close()
                bed2genesRelevant.close()

                #STEP 7. GENERATE THE COMPRESSED FILE WITH RESULTS, COPY THE bed2genesOutput FILE AT INPUT DIR AND CLEAN TEMPORAL FILES
                #COMPRESS THE RESULTING FILES AND CLEAN TEMPORAL DATA
                logging.info("COMPRESSING RESULTS...")
                fileName = "bed2genes_" + self.date
                shutil.make_archive(self.getOutputDir() + fileName, "zip", self.getTemporalDir() + "/")
                logging.info("COMPRESSING RESULTS...DONE")

                fields = {
                    "omicType" : self.getGeneBasedInputOmics()[0].get("omicName"),
                    "dataType" : self.getGeneBasedInputOmics()[0].get("omicName").replace("data","quantification"),
                    "description" : "File generated using RGMatch tool (Bed2Genes);" + self.getJobDescription(True, dataFile, relevantFile, gtfFile)
                }
                mainOutputFileName = copyFile(self.getUserID(), os_path.split(bed2genesOutput.name)[1], fields,self.getTemporalDir() +  "/", self.getInputDir())

                fields = {
                    "omicType" : self.getGeneBasedInputOmics()[0].get("omicName"),
                    "dataType" : "Relevant Genes list",
                    "description" : "File generated using RGMatch tool (Bed2Genes);"  + self.getJobDescription()
                }
                secondOutputFileName = copyFile(self.getUserID(), os_path.split(bed2genesRelevant.name)[1], fields, self.getTemporalDir() + "/", self.getInputDir())

                #TODO: REMOVE FILES IF EXCEPTION
                inputDataFile.close()

                self.cleanDirectories()
                return [fileName + ".zip", mainOutputFileName, secondOutputFileName]

    def summarizeValues(self, dictSelectedRegions):
        selectedRegions = [list(region.values())[0] for region in dictSelectedRegions]

        if(self.summarizationMethod == "mean"):
            from numpy import mean as npmean
            return [{'mean': npmean(selectedRegions, axis=0).tolist()}]
        elif(self.summarizationMethod == "max"):
            import numpy as np
            #1. CALCULATE THE SUM OF THE ABS VALUES FOR EACH REGION
            valuesSum = np.sum(np.abs(selectedRegions), axis=1)
            #2. GET THE MAX
            maxSum = np.max(valuesSum)
            #3. FIND THE POSITION OF MAX VALUE
            #TODO: WHAT IF MORE THAN 1 MAX??
            indices = [i for i, x in enumerate(valuesSum) if x == maxSum]
            return [{'max': selectedRegions[indices[0]]}]
        else:
            return dictSelectedRegions



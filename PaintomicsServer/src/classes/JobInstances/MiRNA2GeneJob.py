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

from src.classes.Job import Job
from src.classes.Feature import OmicValue, Gene
from src.servlets.DataManagementServlet import copyFile
from src.common.Util import ensure_utf8
from src.common.bioscripts.miRNA2Target import run as run_miRNA2Target

from os import path as os_path, mkdir as os_mkdir
from csv import reader as csv_reader
from random import randint
from collections import defaultdict
import shutil
from src.conf.serverconf import MAX_NUMBER_FEATURES

def explainEmptyResult(stats):
    """Why no miRNA-target pair survived, in the user's own identifiers.

    The message this replaces was " - Your mirna2gene association process did
    not return any result. Please, check the files (same identifiers, etc) and
    parameters." -- true, and useless: it names none of the three files, none of
    the identifier spaces, and no number. A user hit it twice in two minutes
    (2026-08-27) and had nothing to act on either time.

    Everything below is counted from the files that were just read. Nothing is
    estimated and nothing is invented; when a number is not known the sentence
    that would have used it is not written.
    """
    if not isinstance(stats, dict):
        return (" - Your mirna2gene association process did not return any "
                "result. Please, check the files (same identifiers, etc) and "
                "parameters.")

    dropped = stats.get("dropped") or {}
    lines = [" - No miRNA was matched to a target gene, so there is nothing to analyse."]

    def sample(ids):
        return ", ".join(str(i) for i in (ids or [])[:3])


    # The order is the order of certainty: each branch is only reached when
    # the ones above it cannot be the cause. A single condition column does
    # not stop identifiers from joining, so the join is judged first (the
    # first cut said "read and matched" about 148,184 pairs of which none had
    # matched, and never mentioned the ENSMUSG-versus-symbol mismatch that was
    # the actual cause, nor the blank rows).
    conditions = stats.get("tooFewConditions")
    # 0. No data rows at all.
    if stats.get("regulators", 1) == 0:
        lines.append(
            "Your miRNA quantification file has no data rows -- it is empty or "
            "holds only a header -- so there is no miRNA to pair with anything.")
    # 1. Nothing to join with: the regulators named in the associations file are
    #    not the regulators in the quantification file.
    elif stats.get("pairs", 0) == 0:
        lines.append(
            "Your miRNA quantification file holds %d miRNAs (e.g. %s), but none "
            "of them appears in the first column of your targets/associations "
            "file. The two files have to use the same miRNA identifiers."
            % (stats.get("regulators", 0), sample(stats.get("sampleRegulators"))))
    # 2. They joined, but not one target id exists in the expression file.
    elif stats.get("scored", 0) == 0 and stats.get("unmatchedTargets", 0):
        lines.append(
            "%d miRNA-target pairs were read, but not one target gene was found "
            "in your gene expression file, so no correlation could be computed."
            % stats.get("pairs", 0))
        nearMiss = stats.get("sampleNearMiss")
        if stats.get("nearMisses") and nearMiss:
            lines.append(
                "%d of the first %d unmatched targets differ from a gene id only "
                "in case or in a version suffix (e.g. %s in the targets file, %s "
                "in the expression file) -- the identifiers are the same kind, "
                "written differently."
                % (stats["nearMisses"], stats.get("nearMissesOf", 0),
                   nearMiss[0], nearMiss[1]))
        else:
            lines.append(
                "Targets in the associations file look like: %s. Identifiers in "
                "the gene expression file look like: %s. These are two different "
                "identifier spaces -- convert one side to the other."
                % (sample(stats.get("sampleUnmatchedTargets") or stats.get("sampleTargets")),
                   sample(stats.get("sampleGenes"))))
        if conditions is not None:
            lines.append(
                "Once the targets match, note that a correlation also needs at "
                "least two condition columns; this data has %d." % conditions)
    # 3. Targets matched, so the fault is in the SCORING, and blaming
    #    identifiers would send the user to rebuild files that were fine.
    elif conditions is not None:
        lines.append(
            "Your data has %d condition column, and a correlation needs at "
            "least two: with a single column every pair is a tie, so there is "
            "nothing to correlate. %d miRNA-target pairs were read. Either "
            "supply the conditions you want correlated, or provide a relevant "
            "associations file so the pairs are taken from it instead of being "
            "scored." % (conditions, stats.get("pairs", 0)))
    elif stats.get("aborted"):
        lines.append(
            "The scoring step stopped after %d miRNA-target pairs with: %s. The "
            "result file was truncated there, so this is not a problem with "
            "your identifiers."
            % (stats.get("abortedAfterPairs", 0), stats["aborted"]))
    elif stats.get("scored", 0) and stats.get("nanScores", 0) == stats.get("scored", 0):
        lines.append(
            "All %d scored miRNA-target pairs gave an undefined correlation "
            "(nan): each pair's values are identical across the conditions, so "
            "there is nothing to correlate. Check that the condition columns "
            "differ, or provide a relevant associations file."
            % stats.get("scored", 0))
    else:
        lines.append(
            "%d miRNA-target pairs were read and %d were scored, but none "
            "carried a usable target gene."
            % (stats.get("pairs", 0), stats.get("scored", 0)))

    # 4. Blank cells, reported whenever there were any -- they are usually the
    #    reason a file "looks fine" and matches nothing.
    blanks = []
    if dropped.get("regulators"):
        blanks.append("%d rows of the quantification file had no miRNA id"
                      % dropped["regulators"])
    if dropped.get("associationRegulators"):
        blanks.append("%d rows of the associations file had an empty first column"
                      % dropped["associationRegulators"])
    if dropped.get("associationTargets"):
        blanks.append("%d rows of the associations file had an empty second column"
                      % dropped["associationTargets"])
    if dropped.get("genes"):
        blanks.append("%d rows of the gene expression file had no gene id"
                      % dropped["genes"])
    if blanks:
        lines.append("Rows skipped because an identifier was blank: " +
                     "; ".join(blanks) + ".")

    return " ".join(lines)


class MiRNA2GeneJob(Job):
    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self, jobID, userID, CLIENT_TMP_DIR):
        super(MiRNA2GeneJob, self).__init__(jobID, userID, CLIENT_TMP_DIR)
        self.omicName = None
        self.report               = "all" #all or DE
        self.score_method         = "kendall" #fc OR kendall OR spearman OR pearson
        self.selection_method     = "negative_correlation" #max_fc OR similar_fc OR abs_correlation OR positive_correlation OR negative_correlation
        self.cutoff               = -0.6
        self.enrichment           = "genes"

    def getOptions(self):
        return {
            "report": self.report,
            "score_method": self.score_method,
            "selection_method": self.selection_method,
            "cutoff": self.cutoff
        }

    def getJobDescription(self, generate=False, dataFile="", relevantFile="", targetsFile="", geneExpressionFile=""):
        if generate:
            # check if the files are in the tmp directory
            if dataFile is not None and os_path.exists(dataFile):
                self.description = "Input data:" + os_path.basename(dataFile)
            if relevantFile is not None and os_path.exists(relevantFile):
                self.description += "\nInput targets:" + os_path.basename(relevantFile)
            if targetsFile is not None and os_path.exists(targetsFile):
                self.description += "\nInput gene expression:" + os_path.basename(targetsFile)
            if geneExpressionFile is not None and os_path.exists(geneExpressionFile):
                self.description += "\nInput gene expression:" + os_path.basename(geneExpressionFile)

            self.description += "Params:;Report=" + str(self.report) + ";"
            self.description += "Score method=" + str(self.score_method)+ ";"
            self.description += "Selection method=" + str(self.selection_method)+ ";"
            self.description += "Cutoff=" + str(self.cutoff)+ ";"
        return self.description

    def validateInput(self):
        """
        This function check the content for files and returns an error message in case of invalid content

        @returns True if not error
        """
        error = ""
        #TODO: CHECK VALID SCORE AND SELECTION METHODS
        try:
            self.cutoff = float(self.cutoff)
        except:
            error +=  " -  Cutoff must be a numeric value"

        # Look using the name instead of relying in the dictionary order
        geneDataInputs = self.getGeneBasedInputOmics()

        # Defaults matter here. A bare next() raises StopIteration, which is not
        # an error this function knows how to report: it escapes validateInput
        # entirely, past the message this method exists to build, and reaches
        # the queue worker as a crash whose str() is the empty string. The user
        # is then told nothing at all about a job that simply will not run.
        miRNAdataInput = next(
            (x for x in geneDataInputs if x["omicName"].lower() != "gene expression"),
            None)

        if miRNAdataInput is None:
            error += (" -  This analysis needs a miRNA omic, but every omic"
                      " supplied is named \"Gene expression\"")
        else:
            logging.info("VALIDATING miRNA-seq BASED FILES..." )
            nConditions, error = self.validateFile(miRNAdataInput, -1, error)

        if len(geneDataInputs) > 1:
            logging.info("VALIDATING RNA-seq BASED FILES..." )
            RNAdataInput = next(
                (x for x in geneDataInputs if x["omicName"].lower() == "gene expression"),
                None)
            if RNAdataInput is None:
                error += (" -  More than one omic was supplied but none is named"
                          " \"Gene expression\", so the miRNA targets have no"
                          " expression data to pair with")
            else:
                nConditions, error = self.validateFile(RNAdataInput, nConditions, error)

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

        if inputOmic.get( "isExample", False ):
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
        # The association files (miRNA→gene reference and its relevant subset)
        # are user uploads too, read later by detect_delimiter and the
        # bioscripts with no encoding pass of their own — a latin-1 reference
        # map crashed processFilesContent after validation had passed.
        associationsFileName = inputOmic.get("associationsFile", "") or ""
        relevantAssociationsFileName = inputOmic.get("relevantAssociationsFile", "") or ""
        if associationsFileName:
            associationsFileName = "{path}/{file}".format(
                path=self.getInputDir(), file=associationsFileName)
        if relevantAssociationsFileName:
            relevantAssociationsFileName = "{path}/{file}".format(
                path=self.getInputDir(), file=relevantAssociationsFileName)

        encodingFailed = False
        for encodingTarget in (relevantFileName, valuesFileName,
                               associationsFileName, relevantAssociationsFileName):
            if encodingTarget and os_path.isfile(encodingTarget):
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

        logging.info("VALIDATING RELEVANT FEATURES FILE (" + omicName + ")..." )
        if os_path.isfile(relevantFileName):
            f = open(relevantFileName, 'r', encoding='utf-8-sig')
            lines = f.readlines()

            if len(lines) > MAX_NUMBER_FEATURES:
                error += " - Errors detected while processing " + inputOmic.get("relevantFeaturesFile", "") + ": The file exceeds the maximum number of features allowed (" + str(MAX_NUMBER_FEATURES) + ")." + "\n"

            for line in lines:
                if len(line) > 80:
                    error +=  " - Errors detected while processing " + inputOmic.get("relevantFeaturesFile", "") + ": The file does not look like a Relevant Features file (some lines are longer than 80 characters)." + "\n"
                    break
            f.close()

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
                            continue

                    if nConditions == -1:
                        if len(line) < 2:
                            erroneousLines[nLine] =  "Expected at least 2 columns, but found one."
                            break
                        nConditions = len(line)

                    # *************************************************************************
                    # STEP 2.2 CHECK IF IT EXCEEDS THE MAX NUMBER OF FEATURES ALLOWED
                    # *************************************************************************
                    if (nLine > MAX_NUMBER_FEATURES):
                        error += " - Errors detected while processing " + inputOmic.get("inputDataFile", "") + ": The file exceeds the maximum number of features allowed (" + str(MAX_NUMBER_FEATURES) + ")." + "\n"
                        break

                    #**************************************************************************************
                    # STEP 2.3 IF LINE LENGTH DOES NOT MATCH WITH EXPECTED NUMBER OF CONDITIONS, ADD ERROR
                    #**************************************************************************************
                    if(nConditions != len(line) and len(line)>0):
                        erroneousLines[nLine] = "Expected " +  str(nConditions) + " columns but found " + str(len(line)) + ";"

                    #**************************************************************************************
                    # STEP 2.4 IF CONTAINS NOT VALID VALUES, ADD ERROR
                    #**************************************************************************************
                    try:
                        # list(...) matters: map() is lazy on Python 3, so the
                        # bare call never converted anything and never raised.
                        # This whole except branch was unreachable, and a miRNA
                        # file full of text -- or of comma decimal marks, which
                        # the branch below exists to name -- validated clean.
                        list(map(float, line[1:len(line)]))
                    except:
                        if(" ".join(line[1:len(line)]).count(",") > 0):
                            erroneousLines[nLine] = erroneousLines.get(nLine,  "") + "Perhaps you are using commas instead of dots as decimal mark?"
                        else:
                            erroneousLines[nLine] = erroneousLines.get(nLine,  "") + "Line contains invalid values or symbols."

                    if len(erroneousLines)  > 9:
                        break

            inputDataFile.close()

            #*************************************************************************
            # STEP 3. CHECK THE ERRORS AND RETURN
            #*************************************************************************
            if len(erroneousLines)  > 0:
                error += " - Errors detected while processing " + inputOmic.get("inputDataFile") + ":\n"
                error += "[ul]"
                for k in sorted(erroneousLines.keys()):
                    error+=  "[li]Line " + str(k) + ":" + erroneousLines.get(k) + "[/li]"
                error += "[/ul]"

                if len(erroneousLines)  > 9:
                    error +=  "Too many errors detected while processing " + inputOmic.get("inputDataFile") + ", skipping remaining lines...\n"
        else:
            error += " - Error while processing " + omicName + ": File " + inputOmic.get("inputDataFile") + "not found.\n"

        return nConditions, error

    def fromMiRNA2Genes(self):
        #STEP 1. GET THE FILES PATH AND PREPRARE THE OPTIONS
        logging.info("READING FILES...")

        geneDataInputs = self.getGeneBasedInputOmics()
        # See validateInput: a bare next() raises StopIteration, which arrives in
        # the queue worker as a crash with an empty message. Say what is wrong.
        miRNAinputOmic = next(
            (x for x in geneDataInputs if x["omicName"].lower() != "gene expression"),
            None)
        if miRNAinputOmic is None:
            raise Exception("This analysis needs a miRNA omic, but every omic "
                            "supplied is named \"Gene expression\".")

        # An uploaded file is named relative to the job's input directory, while
        # the example files are absolute paths under examplefiles/. Resolve both,
        # the same way Bed2GeneJob.fromBED2Genes does for its GTF.
        def resolveInput(fileName):
            if not fileName:
                return ''
            relative = "{path}/{file}".format(path=self.getInputDir(), file=fileName)
            if os_path.isfile(relative):
                return relative
            if os_path.isfile(fileName):
                return fileName
            return ''

        # Uploads carry the mirBase->Ensembl map on the omic itself, but the
        # example branch of MiRNA2GenesServlet registers it through
        # addReferenceInput instead. Reading only associationsFile made that
        # None, which then formatted into "<inputDir>/None" and failed as
        # "Reference file not found." -- so the example never ran at all.
        referenceFileName = miRNAinputOmic.get('associationsFile') or ''
        if not referenceFileName:
            referenceInputs = self.getReferenceInputs() or []
            if referenceInputs:
                referenceFileName = referenceInputs[0].get('inputDataFile') or ''

        referenceFile = resolveInput(referenceFileName)
        if not referenceFile:
            raise Exception("Reference file not found.")

        # Genuinely optional: absent means "derive the associations by
        # correlation with gene expression", which is what the example does.
        relevantReferenceFileName = miRNAinputOmic.get('relevantAssociationsFile') or ''
        relevantReferenceFile = resolveInput(relevantReferenceFileName)
        if relevantReferenceFileName and not relevantReferenceFile:
            raise Exception("Relevant reference file not found.")


        dataFile = miRNAinputOmic.get("inputDataFile")
        relevantFile = miRNAinputOmic.get("relevantFeaturesFile")

        geneExpressionFile =  None
        if len(geneDataInputs) > 1:
            # A second omic that is not gene expression leaves the pairing
            # undefined; treat it as "no expression data" rather than crashing
            # with StopIteration. The correlation path below already handles
            # geneExpressionFile being None.
            RNAinputOmic = next(
                (x for x in geneDataInputs if x["omicName"].lower() == "gene expression"),
                None)
            if RNAinputOmic is not None:
                geneExpressionFile = RNAinputOmic.get("inputDataFile")
            else:
                logging.warning(
                    "MIRNA2GENES - %d omics supplied but none named 'Gene "
                    "expression'; continuing without expression data",
                    len(geneDataInputs))

        if(miRNAinputOmic.get("isExample", False) == False):
            dataFile = "{path}/{file}".format(path=self.getInputDir(), file=dataFile)
            relevantFile = "{path}/{file}".format(path=self.getInputDir(), file=relevantFile)
            if geneExpressionFile != None:
                geneExpressionFile = "{path}/{file}".format(path=self.getInputDir(), file=geneExpressionFile)

        if not os_path.isdir(self.getTemporalDir()):
            os_mkdir(self.getTemporalDir())

        tmpFile = self.getTemporalDir() +"/miRNAMatch_output.txt"

        #STEP 2. CALL TO miRNA2Target SCRIPT AND GENERATE ASSOCIATION BETWEEN miRNAS AND TARGET GENES
        logging.info("STARTING miRNA2Target PROCESS.")
        matchStats = run_miRNA2Target(referenceFile, relevantReferenceFile, dataFile, geneExpressionFile, tmpFile, self.score_method)
        logging.info("STARTING miRNA2Target PROCESS...Done")
        logging.info("miRNA2Target ACCOUNT: %s", matchStats)
        # Rows whose every score is nan are not results: nan > cutoff is False,
        # so such a run "succeeded" with zero relevant associations and no word
        # about why (two identical condition columns do this for kendall,
        # pearson and spearman alike).
        if isinstance(matchStats, dict) and matchStats.get("scored") \
                and matchStats.get("nanScores") == matchStats.get("scored"):
            raise Exception(explainEmptyResult(matchStats))

        #STEP 3. PARSE RELEVANT FILE
        logging.info("PROCESSING RELEVANT FEATURES FILE...")
        relevantMiRNAS = self.parseSignificativeFeaturesFile(relevantFile, isBedFormat=False)
        logging.info("PROCESSING RELEVANT FEATURES FILE...DONE")

        # STEP 3.2. PARSE RELEVANT ASSOCIATIONS FILE
        relevantAssociations = {}

        if relevantReferenceFile:
            logging.info("PROCESSING RELEVANT ASSOCIATIONS FILE...")
            relevantAssociations = self.parseSignificativeFeaturesFile(relevantReferenceFile, isBedFormat=False, forceLegacyTwoCol=True)
            logging.info("PROCESSING RELEVANT ASSOCIATIONS FILE...DONE")

        #STEP 4. PARSE GENERATED TEMPORAL FILE, GET THE MIRNAS, TARGET GENES AND QUANTIFICATION
        logging.info("PROCESSING miRNA2Target OUTPUT...")

        # If no relevant associations file was provided, the script must generate one using
        # the correlation settings.
        useCorrelation = relevantReferenceFile is None or relevantReferenceFile == ''

        if os_path.isfile(tmpFile):
             with open(tmpFile, 'r') as inputDataFile:
                mirnaID = geneID = score = methodsHasChanged = score_type = sortedScores = None
                scoresTable = defaultdict(list)

                csvReader = csv_reader(inputDataFile, delimiter="\t")

                #READ THE HEADER
                line = next(csvReader)
                #SAVE THE NAME OF THE CONDITIONS (e.g. COND1, COND2,...)
                header = "\t".join(line[4:])

                if self.selection_method == "negative_correlation":
                    self.cutoff *= -1 #INVERT VALUES

                for line in csvReader:
                    #STEP 5.1 GET THE mirna ID, THE ASSOCIATED GENE ID AND THE QUANTIFICATION VALUES

                    mirnaID    = line[0].upper()
                    geneID     = line[1].upper()
                    score      = float(line[2])
                    score_type = line[3]
                    # A list, not map(): on Python 3 map() is a one-shot
                    # iterator, and this goes on to OmicValue.setValues().
                    #
                    # Being precise about the scope, because it is easy to
                    # overstate: on *this* path the values are read exactly
                    # once, where regulator2genesOutput is written, and
                    # addInputGeneData merges genes by appending OmicValue
                    # objects without reading them. So the lazy map produced
                    # correct output here -- verified by re-running the example
                    # after the change and finding all 97,983 rows carrying
                    # their full six conditions, including the 4,132 genes that
                    # several miRNAs target.
                    #
                    # It is fixed as a latent hazard rather than an observed
                    # corruption. A stored map has no len(), cannot be
                    # serialised into MongoDB, and empties on any second read,
                    # so it is one added caller away from silently losing data
                    # -- and the loss would be empty values, not an exception.
                    values     =  [float(value) for value in line[4:]]

                    #EVEN WHEN THE USER HAS CHOOSE THE OPTION "FC", if the conditions do no allow to calculate the
                    #correlation, the script will calculate the FC
                    isRelevant = mirnaID.lower() in relevantMiRNAS
                    isRelevantAssociation = False

                    if useCorrelation:
                        if score_type != "fc" and self.selection_method == "negative_correlation":
                            score *= -1  #INVERT VALUES
                        elif score_type != "fc" and self.selection_method == "abs_correlation":
                            score = abs(score)
                        #TODO: SIMILAR FC SELECTION

                        # Only those correlation with a score higher than the specified cutoff
                        # are considered relevant.
                        isRelevantAssociation = (score > self.cutoff)

                        #STEP 5.2 FILTER MIRNAS
                        #IF THE OPTION "ONLY RELEVANTS" WAS SELECTED, IGNORE ENTRY

                        # Add an extra check to ensure that the regulator is inside the list of
                        # relevant regulators (depends on configuration options).
                        if self.report == "DE":# and not isRelevant:
                            isRelevantAssociation = isRelevantAssociation and isRelevant

                    else:
                        isRelevantAssociation = geneID.lower() + ':::' + mirnaID.lower() in relevantAssociations

                    #STEP 5.3 CREATE A NEW OMIC VALUE WITH ROW DATA
                    omicValueAux = OmicValue(mirnaID)
                    #TODO: set omic name with chipseq, dnase,...?
                    omicValueAux.setOriginalName(mirnaID)
                    omicValueAux.setValues(values)
                    # OmicValue.relevant contract is list[bool] for multi-condition;
                    # wrap legacy scalar booleans so downstream length checks (e.g.
                    # PathwayAcquisitionJob.calculateTotalFeaturesByOmic max_conditions)
                    # treat this row consistently with the multi-condition path.
                    omicValueAux.setRelevant([isRelevant])
                    omicValueAux.setRelevantAssociation(isRelevantAssociation)

                    #STEP 5.4 CREATE A NEW TEMPORAL GENE INSTANCE
                    geneAux = Gene(geneID)
                    geneAux.setName(mirnaID)
                    geneAux.addOmicValue(omicValueAux)

                    #STEP 5.5 ADD THE TEMPORAL GENE INSTANCE TO THE LIST OF GENES, IF ALREADY EXISTS, MERGE
                    self.addInputGeneData(geneAux)

                    #STEP 5.6 ADD THE OMIC VALUE TO THE LIST, FOR FURTHER ORDERING
                    scoresTable[geneID].append((score, omicValueAux))

                logging.info("PROCESSING miRNA2Target OUTPUT...DONE")

                # Abort the process to let the user know that there were no results.
                if len(self.getInputGenesData()) < 1:
                    logging.info("MIRNA2GENES - NO RESULTS")
                    raise Exception(explainEmptyResult(matchStats))

                #EVEN WHEN THE USER HAS CHOOSE THE OPTION "FC", if the conditions do no allow to calculate the
                #correlation, the script will calculate the FC
                methodsHasChanged = (score_type == "fc" and self.score_method != "fc")

                #STEP 6. FOR EACH GENE, ORDER THE MIRNAS BY THE HIGHER CORRELATION OR FC
                filePrefix = '' if self.getUserID() is not None else self.getJobID() + '_'
                randomSeed = str(randint(0, 1000))
                genesToMiRNAFile = open(self.getTemporalDir() + '/' + filePrefix + 'genesToMiRNAFile.tab', 'w')
                regulator2genesOutput = open(self.getTemporalDir() + '/' + filePrefix + "regulator2Gene_output_" + self.date + "_" + randomSeed +  ".tab", 'w')
                regulator2genesRelevant = open(self.getTemporalDir() + '/' + filePrefix + "regulator2Gene_relevant_" + self.date + "_" + randomSeed + ".tab", 'w')

                # Associations files
                regulatorAssociations = open(self.getTemporalDir() + '/' + filePrefix + "regulator_associations" + self.date + "_" + randomSeed + ".tab", 'w')
                regulatorRelevantAssociations = open(self.getTemporalDir() + '/' + filePrefix + "regulator_relevant_associations" + self.date + "_" + randomSeed + ".tab", 'w')

                # PRINT HEADER
                genesToMiRNAFile.write("# Gene name\tmiRNA ID\tDE\tScore\tSelection\n")
                #TODO: RE-ENABLE THIS CODE
                regulator2genesOutput.write("# Gene name\t"+ header + "\n")
                regulator2genesRelevant.write("# Gene name\tmiRNA ID\n")

                logging.info("ORDERING miRNAS BY CORRELATION / FC...")
                # An identifier is what makes a row mean anything. Written
                # without one, a row is not a weak result -- it is not a result.
                #
                # Found on a user's own regulator_associations file, produced by
                # this very method and handed back to them as a success:
                # 6,039 rows, 6,039 of them with an EMPTY target gene id.
                # Downstream, MORE says "Association file shares no target IDs
                # with the target expression file / association targets: " with
                # nothing after the colon, and the user is sent to check an
                # identifier space that was never written.
                #
                # `geneID` comes from `line[1].upper()` with nothing asserting
                # it is non-empty, and none of the five writes below looked.
                skippedUnnamed = 0
                for geneID, gene in self.getInputGenesData().items():
                    if not str(geneID).strip():
                        skippedUnnamed += 1
                        continue
                    #GET ALL THE miRNAs AND SORT
                    sortedScores = sorted(scoresTable[geneID], key=lambda omicValue: omicValue[0], reverse=True)

                    #STEP 6.1 WRITE RESULTS
                    for omicValue in sortedScores:
                        score = omicValue[0]
                        omicValue = omicValue[1]

                        lineAux = geneID + "\t" + omicValue.getOriginalName() + "\t"

                        #Recover the original value for the score
                        if not methodsHasChanged and self.selection_method == "negative_correlation":
                            score *= -1

                        #WRITE RESULTS TO genesToMiRNAFile FILE -->   gen_id mirna relevant score
                        genesToMiRNAFile.write(lineAux + ("*" if omicValue.isRelevant() else "") + "\t" + str(score) + "\t" + self.selection_method + "\n")

                        regulator2genesOutput.write(":::".join([geneID, omicValue.getOriginalName()]) + "\t" + '\t'.join(map(str, omicValue.getValues())) + "\n")

                        # Associations file (trimmed down version including only those regulators present on
                        # the values file).
                        regulatorAssociations.write(geneID + "\t" + omicValue.getOriginalName() + "\n")

                        # Relevant regulators file (not associations)
                        if omicValue.isRelevant():
                            #WRITE RESULTS TO mirna2genesRelevant FILE -->   gen_id mirna
                            regulator2genesRelevant.write(geneID + "\t" + omicValue.getOriginalName() + "\n")

                        # Relevant associations file
                        if omicValue.isRelevantAssociation():
                            #WRITE RESULTS TO mirna2genesRelevant FILE -->   gen_id mirna
                            regulatorRelevantAssociations.write(geneID + "\t" + omicValue.getOriginalName() + "\n")

                # Say what was dropped, and refuse if that was everything.
                # Silence here is what turned a broken run into a "successful"
                # one with an unusable file.
                written = len(self.getInputGenesData()) - skippedUnnamed
                if skippedUnnamed:
                    logging.warning(
                        "regu2Target: %d of %d target genes had no identifier "
                        "and were dropped; %d written.",
                        skippedUnnamed, len(self.getInputGenesData()), written)
                if skippedUnnamed and written == 0:
                    # Everything was unnamed, so every output file would be a
                    # column of empty strings. Shipping that as a success is
                    # what produced the 6,039-empty-id associations file: the
                    # user gets a plausible-looking result and finds out three
                    # steps later, from an error about identifier spaces that
                    # names nothing on one side.
                    raise Exception(
                        " - None of the %d target genes carried an identifier, so "
                        "every association row would have been written with an "
                        "empty name. Check that the second column of your "
                        "associations file holds the target gene ID."
                        % skippedUnnamed)

                genesToMiRNAFile.close()
                regulator2genesOutput.close()
                regulator2genesRelevant.close()
                regulatorAssociations.close()
                regulatorRelevantAssociations.close()

                #STEP 7. GENERATE THE COMPRESSED FILE WITH RESULTS, COPY THE mirna2genesOutput FILE AT INPUT DIR AND CLEAN TEMPORAL FILES
                #COMPRESS THE RESULTING FILES AND CLEAN TEMPORAL DATA
                #TODO: REMOVE THE genesToMiRNAFile
                logging.info("COMPRESSING RESULTS...")
                fileName = "regu2genes_" + self.date

                shutil.make_archive(self.getOutputDir() + fileName, "zip", self.getTemporalDir() + "/")

                logging.info("COMPRESSING RESULTS...DONE")

                fields = {
                    "omicType" : miRNAinputOmic.get("omicName"),
                    "dataType" : miRNAinputOmic.get("omicName").replace("data", "quantification"),
                    "description" : "File generated using regu2Target tool (regu2Target);" + self.getJobDescription(True, dataFile, relevantFile, referenceFile, geneExpressionFile)
                }
                mainOutputFileName = copyFile(self.getUserID(), os_path.split(regulator2genesOutput.name)[1], fields, self.getTemporalDir() +  "/", self.getInputDir())

                fields = {
                    "omicType" : miRNAinputOmic.get("omicName"),
                    "dataType" : "Relevant Genes list",
                    "description" : "File generated using regu2Target tool (regu2Target);"  + self.getJobDescription()
                }
                secondOutputFileName = copyFile(self.getUserID(), os_path.split(regulator2genesRelevant.name)[1], fields, self.getTemporalDir() + "/", self.getInputDir())

                fields = {
                    "omicType": miRNAinputOmic.get("omicName"),
                    "dataType": "Associations file",
                    "description": "Associations file filtered using regu2Target tool (regu2Target);" + self.getJobDescription()
                }
                thirdOutputFileName = copyFile(self.getUserID(), os_path.split(regulatorAssociations.name)[1], fields, self.getTemporalDir() + "/", self.getInputDir())

                fields = {
                    "omicType": miRNAinputOmic.get("omicName"),
                    "dataType": "Relevant associations file",
                    "description": "Relevant associations generated using regu2Target tool (regu2Target);" + self.getJobDescription()
                }
                fourthOutputFileName = copyFile(self.getUserID(), os_path.split(regulatorRelevantAssociations.name)[1], fields, self.getTemporalDir() + "/", self.getInputDir())

                #TODO: REMOVE FILES IF EXCEPTION
                inputDataFile.close()

                self.cleanDirectories()
                return [fileName + ".zip", mainOutputFileName, secondOutputFileName, thirdOutputFileName, fourthOutputFileName]
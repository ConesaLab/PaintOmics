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
import time
from time import strftime as formatDate
from os import path as os_path, makedirs as os_makedirs, walk as os_walk
from shutil import rmtree as shutil_rmtree
from csv import reader as csv_reader
from re import compile as re_compile
from collections import defaultdict

from numpy import asarray, logical_or, invert, sum as numpy_sum
# NaN-aware variants for omics that legitimately carry missing measurements
# (e.g. CpG sites not measured in every methylation sample). Without these,
# a single NaN in `allValues` poisons every percentile and the server emits a
# JSON response containing the literal `NaN`, which browsers reject in JSON.parse.
from numpy import nanpercentile as numpy_nanpercentile, nanmin as numpy_nanmin, nanmax as numpy_nanmax, isnan as numpy_isnan

from src.common.Util import Model
from .Feature import Gene, Compound, OmicValue
from src.common.FeatureNamesToKeggIDsMapper import mapFeatureNamesToKeggIDs, mapFeatureNamesToCompoundsIDs, mapFeatureIdentifiers
from zipfile import ZipFile, ZIP_DEFLATED
from shutil import make_archive as shutil_make_archive


class Job(Model):
    @staticmethod
    def detect_delimiter(filename):
        """Auto-detect whether a file uses tab or comma as delimiter.

        errors='replace': delimiter sniffing only needs to see tabs and commas,
        and this helper runs on paths ensure_utf8 has not always covered yet —
        it must never be the line that turns a bad byte into a crash.
        """
        with open(filename, 'r', encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line:
                    if '\t' in line:
                        return '\t'
                    elif ',' in line:
                        return ','
                    return '\t'
        return '\t'

    # Pre-compiled outside the helper to avoid re-compiling every call.
    _ID_DIGIT_RUN = re_compile(r'\d{4,}')

    @staticmethod
    def _row_looks_like_data(row):
        """Organism-agnostic heuristic for row-vs-header detection.

        A cell is considered ID-like (i.e. a row with at least one such cell is
        a data row, not a header) when it satisfies any of:
          * Contains 4+ consecutive digits — ENSMUSG00000000028, ENSG..., K00001,
            AT3G09260, Gm6793, cpd:C00001 all match.
          * Contains a colon — covers KEGG-style cpd:C00001 even if the digit
            count is shorter than 4.
        Header rows (Cond1, WT, Ikaros/Control_0h, Sample_A) intentionally fail
        the check. A leading '#' on the first cell forces the row to be treated
        as a header by the caller.
        """
        if not row:
            return False
        if str(row[0]).strip().startswith('#'):
            return False
        for val in row:
            v = str(val).strip()
            if not v:
                continue
            if ':' in v:
                return True
            if Job._ID_DIGIT_RUN.search(v):
                return True
        return False

    # Condition labels this class invents when nothing better is available.
    # They are placeholders, so a later source (the values file's own header)
    # is allowed to replace them; a real name never is.
    _PLACEHOLDER_CONDITION = re_compile(r'^Condition \d+$')

    # The BED specification's optional-column names. RGmatch
    # (common/bioscripts/DHS_exon_association.py) writes a prefix of exactly
    # this list as the header of its association file whenever the regions file
    # it read had no header of its own, and Bed2GeneJob copies that header into
    # B2G_output_*.tab -- which then arrives here as a values file. They are
    # BED structure keywords, never experimental conditions, so a six-timepoint
    # DNase time course must not be labelled "name / score / strand /
    # thickStart / thickEnd / itemRgb" in the browser.
    #
    # DHS_exon_association now carries the regions file's real header through,
    # so this should no longer trigger; it is kept because that fix lives in a
    # converter that can be changed again, while this check sits on the last
    # boundary before the names reach the client.
    _BED_RESERVED_COLUMN_NAMES = frozenset([
        "name", "score", "strand", "thickstart", "thickend", "itemrgb",
        "blockcount", "blocksizes", "blockstarts"])

    @staticmethod
    def _isBedReservedVocabulary(names):
        """True when every candidate label is a BED structural column name.

        Matched as a set rather than as the literal prefix RGmatch emits, so a
        converter that reorders or drops one of them is caught too. Requiring
        *all* of them to be reserved words keeps a real omic whose single
        condition happens to be called "score" from being rejected alongside a
        genuine six-column BED header -- one such coincidence is plausible, six
        in BED order is not.
        """
        if not names:
            return False
        return all(str(name).strip().lower() in Job._BED_RESERVED_COLUMN_NAMES
                   for name in names)

    @staticmethod
    def _firstNonBlankRow(fileName, delimiter):
        """The first row of `fileName` that carries at least one non-empty cell.

        Reads no further than that row, so classifying a header costs one line
        of I/O regardless of how big the relevance file is.
        """
        try:
            with open(fileName, 'r', encoding='utf-8-sig', newline='') as handle:
                for row in csv_reader(handle, delimiter=delimiter):
                    if row and any(cell.strip() for cell in row):
                        return row
        except (IOError, OSError, UnicodeDecodeError):
            return None
        return None

    @staticmethod
    def _isPairSchemaHeader(row):
        """True when the first row declares a two-column [TARGET, REGULATOR] file.

        The shape of a relevance file is decided HERE, from its header, and not
        from the identifiers in its first data row. The rule:

            a `#`-prefixed first row is a SCHEMA header -- it names what the
            columns hold ("# Gene name<TAB>miRNA ID"), never what conditions
            the columns stand for.

        Every producer in this codebase follows that split: MiRNA2GeneJob and
        Bed2GeneJob write `#`-prefixed schema headers on their pair/target
        files, while per-condition relevance matrices are written with a bare
        header of condition names (see AdminTools/scripts/exampledata/writers.py
        `writeRelevantPerCondition`, and datasets/03 which ships
        `T00h<TAB>...<TAB>T24h` with no `#`).

        Why the header and not the identifiers: the previous rule asked whether
        BOTH cells of the first data row "look like a biological ID", which
        means 4+ consecutive digits or a colon. That makes the parse depend on
        how one arbitrary miRNA happens to be named --
        `mmu-miR-1983` passed and `mmu-miR-100-3p` failed, in files of
        identical shape -- so the same job parsed or mis-parsed depending on
        which regulator sorted first. A header is written by the producer, is
        identical for every row, and cannot be re-broken by a differently named
        identifier.

        Restricted to exactly two columns: a `#`-prefixed header with three or
        more columns keeps its previous reading (condition names with the `#`
        stripped), because there is no pair interpretation for it.
        """
        if not row or len(row) != 2:
            return False
        if not str(row[0]).strip().startswith('#'):
            return False
        return all(str(cell).strip() for cell in row)

    def _applyValuesFileConditionNames(self, fileHeader, nValueColumns):
        """Name the conditions from the values file when the relevance file cannot.

        `conditionNames` used to have exactly one source, the relevance file,
        which is the wrong one: a single-column relevance file ("these genes
        are relevant") says nothing about conditions, so every such job fell
        back to ["Condition 1"] and the client rendered "Cond 2".."Cond 6"
        even though the values file's own header read T00h..T24h.

        Real per-condition names supplied by the relevance file (datasets/03)
        are kept: they are only replaced when what we hold is a placeholder or
        does not line up with the number of value columns.

        @param fileHeader   raw first row of the values file, or None
        @param nValueColumns number of numeric columns per data row
        """
        if not fileHeader or nValueColumns < 1:
            return

        # The values file may or may not label its ID column: datasets/05 ships
        # `#geneID<TAB>T00h..T24h` (nValueColumns + 1 cells) while datasets/10
        # ships `I/C_0h..I/C_24h` with no label at all (nValueColumns cells).
        if len(fileHeader) == nValueColumns + 1:
            candidate = list(fileHeader[1:])
        elif len(fileHeader) == nValueColumns:
            candidate = list(fileHeader)
        else:
            # Cannot align header cells to value columns -- guessing here would
            # mislabel the chart axes, which is worse than "Condition N".
            return

        candidate = [str(name).strip().lstrip('#').strip() for name in candidate]
        if not all(candidate):
            return

        # A values file produced by a region-to-gene converter that lost the
        # original header carries BED structure keywords where the condition
        # names should be. "Condition 1".."Condition 6" is wrong but obviously
        # a placeholder; "thickStart" is wrong and looks authoritative.
        if Job._isBedReservedVocabulary(candidate):
            return

        current = self.conditionNames or []
        if len(current) == nValueColumns and not all(
                Job._PLACEHOLDER_CONDITION.match(str(name).strip()) for name in current):
            # Already named by something that knew the conditions.
            return

        self.conditionNames = candidate

    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self, jobID, userID, CLIENT_TMP_DIR):

        self.jobID = jobID
        self.date = formatDate("%Y%m%d%H%M")
        self.accessDate = formatDate("%Y%m%d%H%M")
        self.userID = userID
        self.lastStep = 1
        self.description=""
        self.name = ""

        self.organism = ""

        # LIST OF DATABASES TO USE
        self.databases = []

        #FIRST STEP GET THE DIRECTORIES FOR THE JOB
        self.setDirectories(CLIENT_TMP_DIR)

        #LIST OF OBJECTS THAT CONTAINS ALL THE INPUT OMICS THAT ARE BASED ON COMPOUNDS IDS
        #{omicName:<omicName>, inputDataFile: <inputDataFile>, relevantFeaturesFile: <relevantFeaturesFile>}
        self.compoundBasedInputOmics = []
        #LIST OF OBJECTS THAT CONTAINS ALL THE INPUT OMICS THAT ARE BASED ON GENE IDS
        #{omicName:<omicName>, inputDataFile: <inputDataFile>, relevantFeaturesFile: <relevantFeaturesFile>}
        self.geneBasedInputOmics = []
        #LIST OF OBJECTS THAT CONTAINS ALL THE INPUT REFERENCE FILES
        #{omicName:"Reference file", "fileType": <fileType>, inputDataFile: <inputDataFile>}
        self.referenceInputs = []

        #This table contains the input values for all omics that can be matched to compounds
        #Indexes by compound ID
        self.inputCompoundsData =  {}
        #This table contains the input values for all omics that can be matched to gene
        #Indexes by gene ID
        self.inputGenesData = {}

        # Sharing options
        self.allowSharing = False
        self.readOnly = False

        self.conditionNames = []

        # job start time
        self.startTime = time.time()

    #******************************************************************************************************************
    # GETTERS AND SETTER
    #******************************************************************************************************************
    def getJobID(self):
        return self.jobID

    def setUserID(self, userID):
        self.userID = userID
    def getUserID(self):
        return self.userID

    def setName(self, name):
        self.name = name
    def getName(self):
        return self.name

    def setLastStep(self, lastStep):
        self.lastStep = lastStep
    def getLastStep(self):
        return self.lastStep

    def getTemporalDir(self):
        return self.temporalDir
    def getInputDir(self):
        return self.inputDir
    def getOutputDir(self):
        return self.outputDir
    def setDirectories(self, CLIENT_TMP_DIR):
        # Assign the userID as directory or "nologin" if is null
        userDir = self.userID if self.userID is not None else "nologin"

        #FIRST STEP GET THE DIRECTORIES FOR THE JOB
        self.inputDir    = CLIENT_TMP_DIR + userDir + "/inputData/"
        self.temporalDir = CLIENT_TMP_DIR + userDir + "/tmp/" + self.jobID
        self.outputDir   = CLIENT_TMP_DIR + userDir + "/jobsData/" + self.jobID + "/output/"

    def setOrganism(self, organism):
        self.organism = organism
    def getOrganism(self):
        return self.organism

    def setDatabases(self, databases):
        self.databases = databases
    def getDatabases(self):
        return self.databases

    def getReferenceInputs(self):
        return self.referenceInputs
    def addReferenceInput(self, referenceInput):
        self.referenceInputs.append(referenceInput)

    def getCompoundBasedInputOmics(self):
        return self.compoundBasedInputOmics
    def addCompoundBasedInputOmic(self, compoundBasedInputOmic):
        self.compoundBasedInputOmics.append(compoundBasedInputOmic)

    def getGeneBasedInputOmics(self):
        return self.geneBasedInputOmics
    def addGeneBasedInputOmic(self, geneBasedInputOmic):
        self.geneBasedInputOmics.append(geneBasedInputOmic)


    def getValueIdTable(self):
        # {ID: values.toBSON() }
        valueTable = {}

        for ID, feature in dict(self.getInputGenesData(), **self.getInputCompoundsData()).items():
            valueTable[ID] = set([feature.getName()])

            for omicValue in feature.getOmicsValues():
                valueTable[ID].add(omicValue.getOriginalName())
                valueTable[ID].add(omicValue.getInputName())

            valueTable[ID] = '|'.join(valueTable[ID])

        return valueTable


    def setInputCompoundsData(self, inputCompoundsData):
        self.inputCompoundsData = inputCompoundsData
    def getInputCompoundsData(self):
        return self.inputCompoundsData
    def addInputCompoundData(self, inputCompoundData):
        currentData = self.inputCompoundsData.get(inputCompoundData.getID(), None)
        if(currentData == None):
            self.inputCompoundsData[inputCompoundData.getID()] = inputCompoundData
        else:
            currentData.combineData(inputCompoundData)

    def getInputGenesData(self):
        return self.inputGenesData
    def addInputGeneData(self, inputGeneData):
        currentData = self.inputGenesData.get(inputGeneData.getID(), None)
        if(currentData == None):
            self.inputGenesData[inputGeneData.getID()] = inputGeneData
        else:
            currentData.addOmicValues(inputGeneData.getOmicsValues())

    def setAllowSharing(self, allowSharing):
        self.allowSharing = allowSharing
    def getAllowSharing(self):
        return self.allowSharing

    def setReadOnly(self, readOnly):
        self.readOnly = readOnly
    def getReadOnly(self):
        return self.readOnly

    #******************************************************************************************************************
    # OTHER FUNCTIONS
    #******************************************************************************************************************

    def initializeDirectories(self):
        """
        This function initialize the directories for the current Job instance

        @returns the Job instance
        """
        if not os_path.exists(self.getTemporalDir()):
            os_makedirs(self.getTemporalDir())
        if not os_path.exists(self.getInputDir()):
            os_makedirs(self.getInputDir())
        if not os_path.exists(self.getOutputDir()):
            os_makedirs(self.getOutputDir())

        return self

    def cleanDirectories(self, remove_output=False, remove_input=False):
        if os_path.exists(self.getTemporalDir()):
            shutil_rmtree(self.getTemporalDir())
        if remove_input and os_path.exists(self.getInputDir()):
            shutil_rmtree(self.getInputDir())
        if remove_output and os_path.exists(self.getOutputDir()):
            shutil_rmtree(self.getOutputDir())

        return self

    def validateInput(self):
        raise NotImplementedError()

    def processFilesContent(self):
        raise NotImplementedError()

    def _resolveRegulatorSymbols(self, associationFeatures, enrichment):
        """Map every regulator ID in the associations file to its gene
        symbol(s), so Step 4 can display the symbol instead of the raw ID.
        Returns {rawInputName: [every matched clone]} -- empty when there are
        no associations or nothing resolves, and the caller falls back to the
        raw regulator ID."""
        matchedNameDict = {}
        if associationFeatures:
            totalInputRegulators = []

            def process_omic_value_regulate_feature(geneName, omicValueVar):
                geneAux = Gene("")
                geneAux.setName(geneName)
                geneAux.addOmicValue(omicValueVar)
                totalInputRegulators.append(geneAux)

            for regName in associationFeatures.keys():
                omicValueAux = OmicValue(regName)
                process_omic_value_regulate_feature(regName, omicValueAux)

            if len(totalInputRegulators) > 0:
                matchedName, notMatchedName, foundName = mapFeatureIdentifiers(self.getJobID(),
                                                                               self.getOrganism(),
                                                                               self.getDatabases(),
                                                                               totalInputRegulators, [],
                                                                               [], [], enrichment)
                if matchedName is not None and len(matchedName) > 0:
                    # Group by the raw input name, keeping EVERY match.
                    #
                    # This was dict(map(...)), which silently kept only the last
                    # clone when one symbol resolved to several canonical IDs.
                    # On the STATegra MORE example that is 180 of 387 regulators,
                    # 14 of them genuine within-KEGG paralogue collisions
                    # (Bach1 -> [12013, 237911], Elf1, Med1, Pml). While the ID
                    # only fed a display line the cost was a wrong label; once a
                    # regulator's ID chooses map COORDINATES it becomes an arrow
                    # drawn at the wrong gene, with no visible symptom.
                    for matched in matchedName:
                        matchedNameDict.setdefault(matched.omicsValues[0].inputName, []).append(matched)
        return matchedNameDict

    def _omicValueForGeneRow(self, line, numericValues, omicName, relevantFeatures,
                             relevantAssociationFeatures, hasAssociations, matchedNameDict):
        """Build the OmicValue for one data row of a gene-based file.
        Returns (columnID, omicValue); columnID[0] is the gene name the
        caller registers the value under."""
        # If there exists an appropriate association list, use that to retrieve the gene
        # name as the previous process of matching regions or regulators to genes (rgmatch, etc)
        # shouldn't have been done, thus leaving the original unmapped features.
        #
        # Otherwise split the ID column as it might contain associated_gene:::original_name
        columnID = line[0].split(":::")
        omicValueAux = OmicValue(columnID[0])
        omicValueAux.setOmicName(omicName)
        # Get multi-condition relevance list
        relList = relevantFeatures.get(line[0].lower(), [])
        omicValueAux.setRelevant(relList)
        omicValueAux.setRelevantAssociation(line[0].lower() in relevantAssociationFeatures)
        omicValueAux.setValues(numericValues)

        if hasAssociations:
            # The transcription factor (miRNA) name is the first column (change to gene symbol when processing transcription factors).
            # When the values file uses the legacy "geneID:::TFname" format (output of MiRNA2GeneJob),
            # columnID[1] holds the regulator name. For files where line[0] is a single column
            # (e.g., user uploads TFExpression.txt directly with just `TF<TAB>values`), there is no
            # ":::" suffix -- leave originalName at its constructor default (= columnID[0]).
            if len(columnID) > 1:
                # `:::` format signals a regulator-style row -- the
                # Step 4 client uses this flag to flip the visual
                # primary/secondary so the regulator is the row's
                # identifier and the target is context.
                omicValueAux.isRegulator = True
                candidates = matchedNameDict.get(columnID[1])
                if candidates:
                    # Deterministic representative: sorted by canonical
                    # ID so the same input always yields the same choice
                    # across runs, processes and machines.
                    matchedReg = min(candidates, key=lambda match: str(match.ID))
                    # Symbol resolved -- display name becomes the symbol,
                    # canonical ID is stashed for the details "(AGI)" line.
                    omicValueAux.setOriginalName(matchedReg.name)
                    omicValueAux.regulatorID = matchedReg.ID
                    if len(candidates) > 1:
                        # Several canonical IDs share this symbol. Anything
                        # that anchors to the chosen one must be able to say
                        # the choice was arbitrary rather than imply it was
                        # the only option.
                        omicValueAux.regulatorAmbiguousIDs = sorted(
                            str(match.ID) for match in candidates)
                else:
                    # No symbol mapping (e.g. miRNA names) -- keep the
                    # raw regulator ID as the display name. regulatorID
                    # stays empty so the details panel skips the
                    # canonical-ID line.
                    omicValueAux.setOriginalName(columnID[1])
        elif len(columnID) > 1:
            omicValueAux.setOriginalName(columnID[1])

        return columnID, omicValueAux

    def  parseGeneBasedFiles(self, inputOmic):
        """
        This function...
        @param {type}
        @returns
        """

        #E.G. Gene Expression, Proteomics, My Own gene data...
        omicName = inputOmic.get("omicName")
        valuesFileName= inputOmic.get("inputDataFile")
        relevantFileName= inputOmic.get("relevantFeaturesFile", "")
        associationsFileName = inputOmic.get("associationsFile", "")
        relevantAssociationsFileName = inputOmic.get("relevantAssociationsFile", "")
        allValues = []  #KEEP ALL VALUES TO CALCULATE PERCENTILES FOR EACH OMIC

        if(inputOmic.get("isExample", False) == False):
            valuesFileName = "{path}/{file}".format(path=self.getInputDir(), file=valuesFileName)
            relevantFileName = "{path}/{file}".format(path=self.getInputDir(), file=relevantFileName)
            associationsFileName = "{path}/{file}".format(path=self.getInputDir(), file=associationsFileName)
            relevantAssociationsFileName = "{path}/{file}".format(path=self.getInputDir(), file=relevantAssociationsFileName)

        totalInputFeatures  = set()
        totalMappedFeatures = foundFeatures = 0
        enrichment   = inputOmic.get("enrichment", 'genes')
        associationFeatures = None
        relevantAssociationFeatures = {}

        #*************************************************************************
        # STEP 1. PARSE THE RELEVANT FEATURES AND ASSOCIATIONS FILES FOR THE
        #         CURRENT OMIC (IF UPLOADED) AND EXTRACT THE INFORMATION.
        #*************************************************************************
        logging.info("PARSING RELEVANT FEATURES FILE (" + omicName + ")..." )
        relevantFeatures = self.parseSignificativeFeaturesFile(relevantFileName)
        logging.info("PARSING RELEVANT FEATURES FILE (" + omicName + ")... DONE. " + str(len(relevantFeatures)) + " RELEVANT FEATURES PROCESSED.")

        logging.info("PARSING ASSOCIATIONS FILE (" + omicName + ")...")
        associationFeatures = self.parseAssociationsFile(associationsFileName)
        logging.info("PARSING ASSOCIATIONS FILE (" + omicName + ")... DONE. " + str(len(associationFeatures.keys())) + " ASSOCIATIONS PROCESSED.")

        logging.info("PARSING RELEVANT ASSOCIATIONS FILE (" + omicName + ")...")
        relevantAssociationFeatures = self.parseSignificativeFeaturesFile(relevantAssociationsFileName, forceLegacyTwoCol=True)
        logging.info("PARSING RELEVANT ASSOCIATIONS FILE (" + omicName + ")... DONE. " + str(len(relevantAssociationFeatures)) + " RELEVANT ASSOCIATIONS PROCESSED.")

        #*************************************************************************
        # STEP 2. PARSE THE FILE AND EXTRACT THE INFORMATION
        #*************************************************************************
        logging.info("PARSING USER GENE BASED FILE (" + omicName + ")..." )
        parsedFeatures = []


        # Any omic whose data file uses the `targetID:::regulatorID` format
        # (signalled by associationFeatures being populated) gets the same
        # treatment we originally added for "Transcription factor": try to
        # resolve each regulator's symbol via the mapper so the Step 4 client
        # can display it alongside / instead of the raw ID. miRNA, methylation
        # or any custom regulatory omic flows through the same path. When the
        # lookup misses (e.g. miRNA names like miR156 aren't in the
        # gene_symbol DBs), the parsing loop below falls back to the raw
        # regulator ID, preserving previous behavior.
        matchedNameDict = self._resolveRegulatorSymbols(associationFeatures, enrichment)


        #IF THE USER UPLOADED VALUES FOR GENE EXPRESSION
        if os_path.isfile(valuesFileName):
            detected_delimiter = Job.detect_delimiter(valuesFileName)
            with open(valuesFileName, 'r', encoding='utf-8-sig', newline='') as inputDataFile:
                nLine = 0
                nValueColumns = 0
                geneAux = omicValueAux = fileHeader = None
                for line in csv_reader(inputDataFile, delimiter=detected_delimiter):
                    nLine = nLine+1
                    #*************************************************************************
                    # STEP 2.1 CHECK IF IT IS HEADER, IF SO, IGNORE LINE
                    #*************************************************************************
                    if len(line) == 0:
                        # A blank row used to fall into the header branch, where
                        # float(line[1]) raised IndexError, was swallowed, and
                        # `fileHeader = []` overwrote the real condition names.
                        # Give back the row number too, so a leading blank line
                        # does not consume the header slot.
                        nLine = nLine - 1
                        continue
                    if nLine == 1:
                        try:
                            float(line[1])
                        except Exception:
                            # If there is a header, save it
                            fileHeader = line
                            continue
                    else:
                        #*************************************************************************
                        # STEP 2.C.1 CREATE A NEW OMIC VALUE WITH ROW DATA
                        #*************************************************************************

                        # Auxiliary function to populate mutable outer values.
                        def process_omic_value(geneName, omicValueVar):
                            # *************************************************************************
                            # CREATE A NEW TEMPORAL GENE INSTANCE
                            # *************************************************************************
                            geneAux = Gene("")
                            geneAux.setName(geneName)
                            geneAux.addOmicValue(omicValueVar)

                            # *************************************************************************
                            # ADD THE TEMPORAL GENE INSTANCE TO THE LIST OF GENES
                            # *************************************************************************
                            parsedFeatures.append(geneAux)

                            allValues.extend(omicValueVar.getValues())

                            # TODO: add third enrichment method
                            totalInputFeatures.add(omicValueVar.getOriginalName() if enrichment == 'features' else omicValueVar.getInputName())

                        # Make sure to use numerical values
                        numericValues = list(map(float, line[1:len(line)]))
                        # Widest data row seen, used to align the values-file
                        # header with the conditions it names.
                        nValueColumns = max(nValueColumns, len(numericValues))

                        columnID, omicValueAux = self._omicValueForGeneRow(
                            line, numericValues, omicName, relevantFeatures,
                            relevantAssociationFeatures, bool(associationFeatures),
                            matchedNameDict)
                        process_omic_value(columnID[0], omicValueAux)

                totalInputFeatures = len(totalInputFeatures)
                logging.info("PARSING USER USER GENE BASED FILE (" + omicName + ")... FINISHED. " + str(totalInputFeatures) + " FEATURES PROCESSED.")

                # The relevance file is not a source of condition names unless
                # it actually has one column per condition; fall back to the
                # values file's own header, which always does.
                self._applyValuesFileConditionNames(fileHeader, nValueColumns)

                #*************************************************************************
                # STEP 3. MAP TH FEATURE NAMES TO KEGG IDs
                #*************************************************************************
                foundFeatures, parsedFeatures, notMatchedFeatures = mapFeatureNamesToKeggIDs(self.getJobID(), self.getOrganism(), self.getDatabases(), parsedFeatures, enrichment)
                totalMappedFeatures = len(parsedFeatures)

                temporalFileName = self.getTemporalDir() +  "/" + omicName

                with open(temporalFileName + '_matched.txt', 'w') as matchedFile:
                    for parsedFeature in parsedFeatures:
                        self.addInputGeneData(parsedFeature)
                        omicValue = parsedFeature.getOmicsValues()[0]
                        # Convert boolean list to 1/0 list
                        relList = omicValue.isRelevant()
                        if not isinstance(relList, list):
                            relList = [relList]
                        relStr = "\t".join(["1" if r else "0" for r in relList])
                        
                        matchedFile.write(omicValue.getInputName() + '\t' + parsedFeature.getName() + '\t' + parsedFeature.getID() + '\t' + parsedFeature.getMatchingDB() + '\t' + '\t'.join(
                            map(str, omicValue.getValues())) + '\t' + relStr + "\n")

                with open(temporalFileName + '_unmatched.txt', 'w') as unmatchedFile:
                    for parsedFeature in notMatchedFeatures:
                        omicValue = parsedFeature.getOmicsValues()[0]
                        relList = omicValue.isRelevant()
                        if not isinstance(relList, list):
                            relList = [relList]
                        relStr = "\t".join(["1" if r else "0" for r in relList])
                        unmatchedFile.write(parsedFeature.getName() + '\t' + '\t' + '\t'.join(map(str, omicValue.getValues())) + '\t' + relStr + "\n")

            inputDataFile.close()
            #*************************************************************************
            # STEP 4. GENERATE SOME STATISTICS
            #*************************************************************************
            logging.info("GENERATING STATISTICS FROM " + omicName + "...")

            # Avoid numpy calculations on empty files (fails on some versions)
            # Initialize summary as a zero filled list
            summary = [0] * 9

            if len(allValues):
                numpyArray = asarray(allValues, dtype=float)
                # Drop NaNs before computing the distribution. NaNs are legitimate
                # signal (e.g. unmeasured CpG sites in methylation data), but
                # plain percentile/min/max propagate them and serialize as the
                # non-standard JSON token "NaN" downstream, breaking the client.
                # Nan-aware reductions skip them; if every value is NaN we fall
                # back to a zero-filled summary, matching the empty-file branch.
                if numpy_isnan(numpyArray).all():
                    summary = [0] * 9
                else:
                    summary = numpy_nanpercentile(numpyArray, [0, 10, 25, 50, 75, 90, 100])

                    interquartilRange = summary[4] - summary[2]
                    minVal =  summary[2] - 1.5*interquartilRange
                    maxVal =  summary[4] + 1.5*interquartilRange

                    # NaN comparisons return False, so NaNs are silently excluded
                    # from outlier counting — exactly what we want.
                    outlierMask = logical_or(numpyArray < minVal, numpyArray > maxVal)
                    numpyArray = numpyArray[invert(outlierMask)]

                    try:
                        summary = summary.tolist() + [numpy_nanmin(numpyArray), numpy_nanmax(numpyArray)]
                    except:
                        summary = summary + [numpy_nanmin(numpyArray), numpy_nanmax(numpyArray)]

            logging.info("DISTRIBUTION FOR " + omicName  + ": MIN: " + str(summary[0])  + "; p10: " + str(summary[1]) + "; q1: " + str(summary[2]) + ";  MEDIAN: " + str(summary[3])+ "; q1: " + str(summary[4])  + "; p90: " + str(summary[5]) + ";  MAX VALUE: " + str(summary[6]))
            outlierCount = int(numpy_sum(outlierMask)) if 'outlierMask' in locals() else 0
            logging.info("DISTRIBUTION FOR " + omicName  + " WITHOUT OUTLIERS: MIN: " + str(summary[7])  + "; MAX: " + str(summary[8])  + "; #OUTLIERS: " + str(outlierCount))

            logging.info("PARSING USER GENE BASED FILE (" + omicName + ")... DONE" )

            # Total unique mapped features. "Total" if there are more than one database.
            # The fallback must stay lazy: `.get(key, expr)` evaluates expr even
            # when the key exists, and an empty mapper result (no databases
            # resolved) made `list({}.values())[0]` abort the job.
            totalMapped = foundFeatures.get("Total")
            if totalMapped is None:
                totalMapped = next(iter(foundFeatures.values()), 0)

            #   0        1       2    3    4    5     6,   7   8      9        10
            #[MAPPED, UNMAPPED, MIN, P10, Q1, MEDIAN, Q3, P90, MAX, MIN_IR, Max_IR]
            return [omicName, [foundFeatures, totalInputFeatures - totalMapped] + summary, fileHeader ]

        else:
            logging.error("PARSING USER GENE BASED FILE (" + omicName + ")... FAILED. File " + valuesFileName + " NOT FOUND")
    def parseCompoundBasedFile(self, inputOmic, checkBoxesData):
        """
        This function is used for parsing a file containing data matched to Compounds IDs
        First parse the file and get all the input compound
        After that, for each KEGG compound, check if the keggCompound (KC) is a variation
        of the provided compound.
        If so, add the information about the compound to the Compound Values list and
        generate a new checkbox item (if not exists yet for the KC)

        @param omicName, name of the omic that the data is been parsed
        @param keggInformationManager, contains all the tools and info for ids and names matching
        @returns the changed instances of checkBoxesData
        """

        #E.G. Metabolomics, Metabolomics 2, My Metab
        omicName = inputOmic.get("omicName")
        valuesFileName= inputOmic.get("inputDataFile")
        relevantFileName= inputOmic.get("relevantFeaturesFile", "")
        allValues = []  #KEEP ALL VALUES TO CALCULATE PERCENTILES FOR EACH OMIC

        if(inputOmic.get("isExample", False) == False):
            valuesFileName = "{path}/{file}".format(path=self.getInputDir(), file=valuesFileName)
            relevantFileName = "{path}/{file}".format(path=self.getInputDir(), file=relevantFileName)

        #STEP 1. PARSE THE RELEVANT FEATURES FILE FOR THE CURRENT OMIC (IF UPLOADED) AND EXTRACT THE INFORMATION.
        logging.info("PARSING RELEVANT FEATURES FILE (" + omicName + ")..." )
        relevantFeatures = self.parseSignificativeFeaturesFile(relevantFileName)
        logging.info("PARSING RELEVANT FEATURES FILE (" + omicName + ")... DONE. " + str(len(relevantFeatures)) + " RELEVANT FEATURES PROCESSED.")

        #STEP 2. PARSE THE FILE AND EXTRACT THE INFORMATION
        logging.info("PARSING USER COMPOUND BASED FILE (" + omicName + ")..." )

        #IF THE FILE WAS UPLOADED CORRECTLY,
        if os_path.isfile(valuesFileName):
            inputCompounds = []
            #READ THE FILE LINE BY LINE, CREATE A TEMPORAL COMPOUND WITH THE INFO
            detected_delimiter = Job.detect_delimiter(valuesFileName)
            with open(valuesFileName, 'r', encoding='utf-8-sig', newline='') as inputDataFile:
                nLine = 0
                nValueColumns = 0
                compoundAux = omicValueAux = fileHeader = None
                for line in csv_reader(inputDataFile, delimiter=detected_delimiter):
                    nLine = nLine+1
                    #*************************************************************************
                    # STEP 2.1 CHECK IF IT IS HEADER, IF SO, IGNORE LINE
                    #*************************************************************************
                    if len(line) == 0:
                        # A blank row used to fall into the header branch, where
                        # float(line[1]) raised IndexError, was swallowed, and
                        # `fileHeader = []` overwrote the real condition names.
                        # Give back the row number too, so a leading blank line
                        # does not consume the header slot.
                        nLine = nLine - 1
                        continue
                    if nLine == 1:
                        try:
                            float(line[1])
                        except Exception:
                            # If there is a header, save it
                            fileHeader = line
                            continue
                    else:
                        #STEP 2.C.1 CREATE A NEW OMIC VALUE WITH ROW DATA
                        omicValueAux = OmicValue(line[0].lower())
                        omicValueAux.setOmicName(omicName)
                        # Get multi-condition relevance list
                        relList = relevantFeatures.get(omicValueAux.getInputName(), [])
                        omicValueAux.setRelevant(relList)
                        omicValueAux.setValues(list(map(float, line[1:len(line)])))
                        nValueColumns = max(nValueColumns, len(line) - 1)

                        #STEP 2.C.2 CREATE A NEW TEMPORAL COMPOUND INSTANCE
                        compoundAux = Compound(line[0].lower())
                        compoundAux.setName(line[0])
                        compoundAux.addOmicValue(omicValueAux)

                        inputCompounds.append(compoundAux)
                        allValues += omicValueAux.getValues()

                logging.info("PARSING USER COMPOUND BASED FILE (" + omicName + ")... FINISHED. " + str(len(inputCompounds)) + " FEATURES PROCESSED.");

                # Same fallback as the gene-based parser: a compound relevance
                # file that lists one relevant compound per line cannot name
                # the conditions, but the values file's header can.
                self._applyValuesFileConditionNames(fileHeader, nValueColumns)
            inputDataFile.close()


            # FOR EACH PARSED COMPOUND, GET THE NAMES FOR KEGG,
            # THEN FOR EACH COMPOUND IN KEGG, ADD CHECKBOXES
            foundFeatures, parsedFeatures, notMatchedFeatures = mapFeatureNamesToCompoundsIDs(self.getJobID(), inputCompounds)
            
            temporalFileName = self.getTemporalDir() +  "/" + omicName
            with open(temporalFileName + '_matched.txt', 'w') as matchedFile:
                for parsedFeature in parsedFeatures:
                    #STEP 2.C.3 ADD THE TEMPORAL COMPOUND INSTANCE TO THE LIST OF COMPOUNDS
                    for compoundAux in parsedFeature.getMainCompounds():
                        self.addInputCompoundData(compoundAux)
                        omicValue = compoundAux.getOmicsValues()[0]
                        relList = omicValue.isRelevant()
                        if not isinstance(relList, list): relList = [relList]
                        relStr = "\t".join(["1" if r else "0" for r in relList])
                        matchedFile.write(compoundAux.getID() + '\t' + compoundAux.getName() + '\t' + parsedFeature.getID() + '\t' + "KEGG" + '\t' + '\t'.join(map(str, omicValue.getValues())) + '\t' + relStr + "\n")
                    for compoundAux in parsedFeature.getOtherCompounds():
                        self.addInputCompoundData(compoundAux)

            with open(temporalFileName + '_unmatched.txt', 'w') as unmatchedFile:
                for parsedFeature in notMatchedFeatures:
                    omicValue = parsedFeature.getOmicsValues()[0]
                    relList = omicValue.isRelevant()
                    if not isinstance(relList, list): relList = [relList]
                    relStr = "\t".join(["1" if r else "0" for r in relList])
                    unmatchedFile.write(parsedFeature.getName() + '\t' + '\t' + '\t'.join(map(str, omicValue.getValues())) + '\t' + relStr + "\n")

            #GENERATE SOME STATISTICS
            # Mirror the NaN-aware handling in parseGeneBasedFiles: nan-aware
            # percentile/min/max so missing measurements don't poison the
            # summary and ultimately the JSON response (json.dumps(NaN) → "NaN"
            # which browsers reject in JSON.parse).
            arr = asarray(allValues, dtype=float)
            if numpy_isnan(arr).all():
                summary = [0] * 9
                outliers = []
            else:
                summary = numpy_nanpercentile(arr, [0,10,25,50,75,90,100])

                interquartilRange = summary[4] - summary[2]
                minVal =  summary[2] - 1.5*interquartilRange
                maxVal =  summary[4] + 1.5*interquartilRange

                outliers= []
                for i in range(len(allValues)-1,-1,-1):
                    v = allValues[i]
                    # NaN comparisons return False — skip them rather than treating as outliers.
                    if v == v and (v < minVal or v > maxVal):
                        outliers.append(v)
                        del allValues[i]

                try:
                    summary = summary.tolist() + [numpy_nanmin(allValues), numpy_nanmax(allValues)]
                except:
                    summary = summary + [numpy_nanmin(allValues), numpy_nanmax(allValues)]

            logging.info("DISTRIBUTION FOR " + omicName  + ": MIN: " + str(summary[0])  + "; p10: " + str(summary[1]) + "; q1: " + str(summary[2]) + ";  MEDIAN: " + str(summary[3])+ "; q1: " + str(summary[4])  + "; p90: " + str(summary[5]) + ";  MAX VALUE: " + str(summary[6]))
            logging.info("DISTRIBUTION FOR " + omicName  + "WITHOUT OUTLIERS: MIN: " + str(summary[7])  + "; MAX: " + str(summary[8])  + "; #OUTLIERS: " + str(len(outliers)))

            logging.info("PARSING COMPOUND BASED FILE (" + omicName + ")... DONE" )

            return [omicName, checkBoxesData + list(parsedFeatures), [foundFeatures, len(notMatchedFeatures)] + summary, fileHeader]
        else:
            logging.error("PARSING USER COMPOUND BASED FILE (" + omicName + ")... FAILED. File " + valuesFileName + " NOT FOUND")


    ##*************************************************************************************************************
    # This function is used for parsing a file containing significative features
    #
    # @param {type}
    # @returns
    ##*************************************************************************************************************
    def parseSignificativeFeaturesFile(self, fileName, isBedFormat=False, forceLegacyTwoCol=False):
        # TODO: HEADER
        # forceLegacyTwoCol is set by callers that parse association-shaped slots
        # (relevant-associations file, miRNA reference file) where the contract is
        # always [TARGET, REGULATOR] and the multi-condition heuristic does not
        # apply. Skips header rows and emits joined `target:::regulator` keys.
        relevantFeatures = {}
        if fileName and os_path.isfile(fileName):
            detected_delimiter = Job.detect_delimiter(fileName)

            # Decide the file's SHAPE from its header before reading any data.
            # `# Gene name<TAB>miRNA ID` (what MiRNA2GeneJob writes) is a schema
            # header for a [TARGET, REGULATOR] pair file and must never be read
            # as a two-condition relevance matrix -- doing so emits bare
            # `gene` / `mirna` keys that can never match the values file's
            # `gene:::mirna` feature IDs, leaving the omic with zero relevant
            # features and every pathway at Fisher p = 1.0.
            if not isBedFormat and not forceLegacyTwoCol:
                if Job._isPairSchemaHeader(Job._firstNonBlankRow(fileName, detected_delimiter)):
                    forceLegacyTwoCol = True

            if forceLegacyTwoCol and not isBedFormat:
                with open(fileName, 'r', encoding='utf-8-sig', newline='') as inputDataFile:
                    for nLine, line in enumerate(csv_reader(inputDataFile, delimiter=detected_delimiter), start=1):
                        if not line:
                            continue
                        # Skip the header (whether `#`-prefixed or a plain
                        # `Target\tRegulator`-style descriptor) using the same
                        # ID-shape heuristic as the multi-condition path.
                        if nLine == 1 and not Job._row_looks_like_data(line):
                            continue
                        if len(line) >= 2 and line[0].strip() and line[1].strip():
                            featureID = ":::".join([line[0], line[1]]).lower()
                        elif line[0].strip():
                            featureID = line[0].lower()
                        else:
                            continue
                        relevantFeatures[featureID] = [True]
                logging.info("PARSING RELEVANT FEATURES FILE (" + str(fileName) + ", legacy-2col)... THE FILE CONTAINS " + str(len(relevantFeatures)) + " RELEVANT FEATURES")
                return relevantFeatures
            with open(fileName, 'r', encoding='utf-8-sig', newline='') as inputDataFile:
                nLine = 0
                nConditions = 1
                # Once row 1 is recognised as legacy 2-column (e.g. miRNA's
                # ENSMUSG:::mmu-miR-name pairs), EVERY subsequent row must also
                # be joined with ":::" instead of falling through to the
                # single-column branch — otherwise rows 2+ register only line[0]
                # and silently drop the miRNA suffix, breaking relevance lookups
                # against values files keyed by the joined ID.
                isLegacyTwoCol = False
                # legacyEligible governs whether the 2-col legacy [TARGET, REGULATOR]
                # detection is allowed to fire on the next data row.
                #   * True at start  → fires on nLine==1 if both cells look like IDs.
                #   * Stays True across a `#`-prefixed comment header (e.g. miRNA's
                #     `# Gene name\tmiRNA ID`), so legacy detection runs on the actual
                #     first data row.
                #   * Forced False after a plain condition-name header (e.g. `WT\tKO`),
                #     which signals a genuine 2-condition relevance file.
                legacyEligible = True
                # One warning per file for rows wider than the declared width;
                # a 40k-row export with row-names would otherwise log 40k lines.
                raggedRowsWarned = False
                for line in csv_reader(inputDataFile, delimiter=detected_delimiter):
                    # csv yields [] for an empty line, and a row of empty strings
                    # for one that is only separators, so `line[0]` raised
                    # IndexError and killed the job. A trailing blank line is
                    # ordinary in hand-edited and Windows-exported files, so this
                    # was reachable with entirely valid data.
                    #
                    # Skipped before nLine is incremented: nLine == 1 is what
                    # marks the header row, and a leading blank line must not
                    # consume that slot.
                    if not line or not any(cell.strip() for cell in line):
                        continue
                    # Comment / header rows are skipped for EVERY format,
                    # before nLine is incremented so they do not consume the
                    # header slot. The BED branch below used to guard only on
                    # `len(line) < 3`, and it sits ahead of the header handling
                    # for the other formats, so a `#CHR<TAB>start<TAB>end` row
                    # -- shipped at line 886 of datasets/09's relevance file as
                    # a concatenation artifact, and also the natural first line
                    # of a BED export -- was turned into the phantom relevant
                    # feature `#chr_start_end`.
                    #
                    # Single-column files never reached the header block either
                    # (it only runs for len(line) > 1), so `#Gene name` in
                    # datasets/08's dnase_relevant.tab became a feature key too.
                    # A `#` prefix is unambiguous: no biological identifier
                    # starts with one.
                    if str(line[0]).strip().startswith('#') and (isBedFormat or len(line) == 1):
                        continue
                    nLine += 1
                    if isBedFormat == True:
                        # Store and move on. Before multi-condition support this
                        # function ended in a single unconditional
                        # `relevantFeatures[featureID] = 1`, so computing
                        # featureID here was enough. The rewrite turned that tail
                        # into an if/elif/else whose final branch recomputes
                        # `featureID = line[0].lower()` -- for a 3-column BED that
                        # is the *chromosome*, so the whole file collapsed to
                        # {"1": [True], "2": [True], ...} and no region ID
                        # ("1_4780215_4780345") ever matched. Regions2Genes
                        # reported zero relevant genes as a result.
                        # A BED region needs chrom/start/end; a shorter row
                        # cannot name one, and indexing it raised IndexError and
                        # aborted the job rather than skipping the row.
                        if len(line) < 3:
                            continue
                        featureID = (line[0] + "_" + line[1] + "_" + line[2]).lower()
                        relevantFeatures[featureID] = [True]
                        if not self.conditionNames:
                            self.conditionNames = ["Condition 1"]
                        continue
                    else:
                        # Detect number of conditions from header or first line
                        if nLine == 1:
                            if len(line) > 1:
                                nConditions = len(line)
                                # Detect if first row is a header (condition names) using
                                # an organism-agnostic heuristic: a data row contains at
                                # least one cell that looks like a biological ID, where
                                # "looks like an ID" means it contains 4+ consecutive
                                # digits (ENSMUSG/ENSG/KEGG/AT3G09260/Gm6793 style) OR a
                                # colon (cpd:C00001 style) OR starts with `#` (explicit
                                # comment-marker header). Header rows are short alphanumeric
                                # condition labels (Cond1, WT, Ikaros/Control_0h) which fail
                                # all three checks.
                                is_data = Job._row_looks_like_data(line)
                                if not is_data:
                                    # `#`-prefixed headers describe schema (e.g.
                                    # `# Gene name\tmiRNA ID`) rather than condition
                                    # names, so legacy 2-col detection is still allowed
                                    # on the next row. Plain headers (`WT\tKO`) commit
                                    # the file to the multi-condition interpretation.
                                    is_comment_header = str(line[0]).strip().startswith('#')
                                    if not is_comment_header:
                                        legacyEligible = False
                                    self.conditionNames = [name.strip().lstrip('#') for name in line]
                                    continue
                                else:
                                    # First row is data, no header
                                    self.conditionNames = ["Condition " + str(i+1) for i in range(nConditions)]

                        # Disambiguate 2-column format (Legacy ID+Original vs New Cond1+Cond2).
                        # Legacy: each row pairs [MappedID, OriginalID] (e.g. mirna ENSMUSG +
                        # mmu-miR-...); both cells look like biological IDs (digits or special
                        # tokens). Multi-cond: each cell is a feature ID belonging to its column's
                        # condition. We treat the file as legacy iff BOTH cells of the first
                        # eligible data row look like biological IDs — that's the structural
                        # difference from a true 2-condition file where the same ID typically
                        # appears in only one column at a time.
                        # A shorter row cannot be the legacy pair — indexing
                        # line[1] on a 1-column row raised IndexError and killed
                        # the job, and a `#Cond<TAB>` header (blank second cell)
                        # keeps legacyEligible True, so ordinary files reached it.
                        if nConditions == 2 and not isBedFormat and len(line) > 1:
                             if legacyEligible and Job._row_looks_like_data([line[0]]) and Job._row_looks_like_data([line[1]]):
                                 # Both columns biological IDs → legacy format, not 2 conditions.
                                 featureID = ":::".join([line[0], line[1]]).lower()
                                 relevantFeatures[featureID] = [True]
                                 nConditions = 1 # Revert to single condition
                                 isLegacyTwoCol = True
                                 legacyEligible = False
                                 continue
                        legacyEligible = False

                        # Multi-condition logic: Each column (from index 0 to n) contains IDs
                        if nConditions > 1 and not isBedFormat:
                            # The flag lists were sized from row 1, so a wider row
                            # (R's write.table default emits header N / rows N+1)
                            # wrote past the end and aborted the whole job. The
                            # declared width wins; extra cells cannot belong to
                            # any condition, so they are dropped, once, loudly.
                            if len(line) > nConditions and not raggedRowsWarned:
                                raggedRowsWarned = True
                                logging.warning(
                                    "PARSING RELEVANT FEATURES FILE (%s): row %d has %d "
                                    "columns but the file declares %d condition(s); "
                                    "ignoring the extra cells (R's write.table row-names "
                                    "produce this shape).",
                                    fileName, nLine, len(line), nConditions)
                            for colIndex, featureID in enumerate(line[:nConditions]):
                                if featureID.strip():
                                    fID = featureID.lower()
                                    if fID not in relevantFeatures:
                                        relevantFeatures[fID] = [False] * nConditions
                                    relevantFeatures[fID][colIndex] = True
                            continue
                        elif isLegacyTwoCol and len(line) > 1:
                            # Legacy [MappedID, OriginalID] file detected on row 1; keep
                            # joining every subsequent row the same way (matches
                            # pre-multi-condition behaviour and the values-file ID format).
                            featureID = ":::".join([line[0], line[1]]).lower()
                            relevantFeatures[featureID] = [True]
                            continue
                        else:
                            # Standard 1 column format
                            featureID = line[0].lower()
                            relevantFeatures[featureID] = [True]
                            if not self.conditionNames:
                                self.conditionNames = ["Condition 1"]
            inputDataFile.close()
            logging.info("PARSING RELEVANT FEATURES FILE (" + str(fileName) + ")... THE FILE CONTAINS " + str(
                len(relevantFeatures.keys())) + " RELEVANT FEATURES")
        else:
            logging.info("PARSING RELEVANT FEATURES FILE (" + str(fileName) + ")... NO RELEVANT FEATURES FILE SUBMITTED")

        return relevantFeatures


    ##*************************************************************************************************************
    # This function is used for parsing a file containing associations between genes and other features
    #
    # @param {type}
    # @returns
    ##*************************************************************************************************************
    def parseAssociationsFile(self, fileName):
        #TODO: HEADER
        associationFeatures = defaultdict(set)
        if fileName and os_path.isfile(fileName):
            detected_delimiter = Job.detect_delimiter(fileName)
            with open(fileName, 'r', encoding='utf-8-sig', newline='') as inputDataFile:
                for line in csv_reader(inputDataFile, delimiter=detected_delimiter):
                    # Same hazard as parseSignificativeFeaturesFile: line[1]
                    # raises IndexError on a blank line and on any row that
                    # carries only one column. An associations file is a
                    # two-column mapping, so a short row has nothing to
                    # contribute -- skip it rather than abort the job.
                    if len(line) < 2:
                        continue
                    if not line[0].strip() or not line[1].strip():
                        continue
                    associationFeatures[line[1]].add(line[0])
            inputDataFile.close()
            logging.info("PARSING ASSOCIATIONS FILE (" + fileName + ")... THE FILE CONTAINS " + str(len(associationFeatures.keys())) + " ASSOCIATIONS" );
        else:
            logging.info("PARSING ASSOCIATIONS FILE (" + str(fileName) + ")... NO ASSOCIATIONS SUBMITTED" );

        return associationFeatures

    def parseBSON(self, bsonData):
       bsonData.pop("_id")
       for (attr, value) in bsonData.items():
            if attr == "inputCompoundsData":
                compoundInstance = None
                self.inputCompoundsData.clear()
                for (compoundID, compoundData) in value.items():
                    compoundInstance = Compound(compoundID)
                    compoundInstance.parseBSON(compoundData)
                    self.addInputCompoundData(compoundInstance)
            elif attr == "inputGenesData":
                geneInstance = None
                self.inputGenesData.clear()
                for (geneID, genData) in value.items():
                    geneInstance = Gene(geneID)
                    geneInstance.parseBSON(genData)
                    self.addInputGeneData(geneInstance)
            elif not isinstance(value, dict) or attr == "conditionNames":
                setattr(self, attr, value)

    def toBSON(self, recursive= True):
        bson = {}
        for attr, value in list(self.__dict__.items()):
            if not isinstance(value, dict) and ( ["svgDir", "inputDir", "outputDir", "temporalDir"].count(attr) == 0) :
                bson[attr] = value

            elif(recursive == True):
                if(attr == "inputCompoundsData"):
                    compounds = {}
                    for (compoundID, compoundInstance) in value.items():
                        compounds[compoundID] = compoundInstance.toBSON()
                    value = compounds
                elif(attr == "inputGenesData"):
                    genes = {}
                    for (geneID, geneInstance) in value.items():
                        genes[geneID] = geneInstance.toBSON()
                    value = genes
                bson[attr] = value
        return bson

    def compressDirectory(self, output, format, target):
        if format == "zip":
            try:
                zipf = ZipFile(output + ".zip", 'w', ZIP_DEFLATED)
                for root, dirs, files in os_walk(target):
                    for file in files:
                        zipf.write(os_path.join(root, file), os_path.basename(os_path.join(root, file)))
                zipf.close()
            except Exception:
                try:
                    shutil_make_archive(output, "zip", target)
                except Exception:
                    raise Exception("Failed while compressing directory")
        else:
            raise Exception("Failed while compressing directory. Format not supported" )

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
from src.common.ServerErrorManager import InputFileError, quotable
from src.common.bioscripts.miRNA2Target import NO_SCORE_METHOD, run as run_miRNA2Target

from os import path as os_path, mkdir as os_mkdir
from csv import reader as csv_reader
from random import randint
from collections import defaultdict
import shutil
from src.conf.serverconf import MAX_NUMBER_FEATURES


# How a scored pair is selected, by its correlation with the target gene against the
# cutoff (see fromMiRNA2Genes): negative keeps a correlation below the cutoff, positive
# one above it, and abs one whose size is above it.
SELECTION_METHODS = ("negative_correlation", "positive_correlation", "abs_correlation")

# "fc" was on the form from the first commit (54ff9dc3) as "by max. fold-change of
# feature expression", and computed no fold change. The plan, in comments this replaces
# ("max_fc OR similar_fc", "TODO: SIMILAR FC SELECTION") and in the first help text
# ("'Max fold-change' in other case", that is, without gene expression), was to rank a
# gene's miRNAs by how much each miRNA's own expression changed, beside the score method
# "fc", whose score was random.uniform(-1, 1) (see miRNA2Target.run). Beside a
# correlation, "fc" matched neither branch of the selection, so a pair was kept when its
# correlation was above the cutoff, which the form had just made positive: selection by
# positive correlation, under another name. A sound fold change cannot be had from these
# files: nothing says which condition is the reference, the values are often log ratios
# already, a miRNA's fold change is one number for all of its targets when the point is
# to choose among them, and the cutoff is on a correlation's -1 to 1 scale. So the form
# no longer offers it, and a request that still sends it (a page opened before the
# change and not reloaded, a script) is run as it always was, under the name of what
# that is, and told so in its answer's notice and at the top of genesToMiRNAFile.tab.
RETIRED_SELECTION_METHODS = {"fc": "positive_correlation"}


def unknownSelectionText(method):
    """The refusal of a selection method this tool does not have.

    Any other value fell through the selection as "fc" did: pairs were kept on a
    positive correlation, under the name the value came with. Repeated only when it
    is plain text (see quotable).
    """
    named = quotable(method)
    return ("The selection method %s is not one this tool has. Use negative_correlation, "
            "positive_correlation or abs_correlation."
            % ("\"%s\"" % named if named else "sent"))


def retiredSelectionNotice(sent, method):
    """What a run that was sent a retired selection method says it did, in its answer
    and at the top of its results file."""
    return ("The selection method \"%s\", offered as \"by max. fold-change of feature "
            "expression\", computed no fold change: it kept a pair when its correlation "
            "with the target gene was above the cutoff, which is selection by positive "
            "correlation. This run selected that way, and its results name it %s."
            % (sent, method))


def _plural(count, noun):
    """ "1 value", "4 values": a count with its noun in the right number."""
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


def _pairs(count):
    return _plural(count, "miRNA-target pair")


def _pairsWereRead(count):
    return _pairs(count) + (" was read" if count == 1 else " were read")


def _headerMismatchText(mismatch):
    """A miRNA header that would put the wrong name over some column.

    See miRNA2Target.readHeader for what fits. Counted from the files, and a first
    cell starting with "#" is the only one known to be a label: for any other, the
    honest count is of cells, because five cells over three values may be five names
    or a label and four.
    """
    cells, values = mismatch.get("cells", 0), mismatch.get("values", 0)
    opening = "The header row of your miRNA quantification file "
    rows = "its rows have %s after the miRNA id" % _plural(values, "value")
    label = mismatch.get("label")
    if label:
        names = cells - 1
        text = ("%snames %s after its label \"%s\", but %s. It needs one name per value "
                "column, so it should name %d."
                % (opening, _plural(names, "condition") if names else "no conditions", label, rows, values))
        if names == values - 1:
            text += " If \"%s\" is itself a condition name, remove the \"#\"." % label
        return text + " Nothing was scored."
    first = mismatch.get("readsTwoWays")
    if first:
        return ("%shas %s and the last one is empty, and %s. That can be read two ways: "
                "\"%s\" as a label for the miRNA id column with the last value column unnamed, "
                "or \"%s\" as the first condition with an empty cell left over at the end. "
                "Remove the empty cell if every column has a name, or name the last column "
                "and start the label with \"#\". Nothing was scored."
                % (opening, _plural(cells, "cell"), rows, first, first))
    if cells > values + 1:
        said = "has %s" % _plural(cells, "cell")
    elif cells == 0:
        said = "is empty"
    else:
        said = "has only %s" % _plural(cells, "cell")
    return ("%s%s, but %s. The header needs one name per value column, optionally "
            "preceded by a label for the miRNA id column, so it should have %d or %d "
            "cells. Nothing was scored." % (opening, said, rows, values, values + 1))


def _notANumberText(cell):
    """A row that reached the scoring step and is not numbers."""
    if cell.get("headerRow"):
        # A header can hold numbers ("#gene 0 12 24") or nothing ("#gene"), so
        # there may be no cell to quote.
        quoted = cell.get("cell")
        return ("Your targets/associations file pairs %s with a target called "
                "\"%s\", which is also the first cell of the header row of your "
                "gene expression file. That row is a header, not the values of a "
                "gene%s, so the pair could not be scored and no result was saved. "
                "Remove that pair from the associations file, or rename the first "
                "cell of the gene expression header so that no target has its name."
                % (cell.get("regulator"), cell.get("target"),
                   " (its cell \"%s\" is not a number)" % quoted if quoted is not None else ""))
    return ("The row for \"%s\" in your %s file holds \"%s\", which is not a "
            "number, so the pair %s - %s could not be scored and no result was "
            "saved. Every value after the identifier has to be a number."
            % (cell.get("row"), cell.get("file"), cell.get("cell"),
               cell.get("regulator"), cell.get("target")))


def _scoringFailure(stats):
    """Why miRNA2Target stopped before the end, or None when it finished.

    Returns (text, reportsACrash). A run that stops leaves no output file, so it
    has no result to read, whatever it scored before stopping; until this, a run
    that stopped after writing some rows was read as a smaller success. A header
    that does not fit its rows and a cell that is not a number are the user's
    files, and say so. Left to the chain in _emptyResultExplanation: a file with
    no data rows, or with no miRNA that joins, where nothing was there to score;
    and a single condition column, told by the sentence kendall and spearman also
    get (pearson raises on it, they score nan). Anything else is ours.
    """
    if stats.get("headerMismatch"):
        return _headerMismatchText(stats["headerMismatch"]), False
    if (not stats.get("aborted") or stats.get("regulators", 1) == 0
            or stats.get("pairs", 0) == 0 or stats.get("tooFewConditions") is not None):
        return None
    if stats.get("notANumber"):
        return _notANumberText(stats["notANumber"]), False
    pair = stats.get("abortedOn")
    if pair:
        where = ("at miRNA-target pair %d of %d (%s with %s)"
                 % (stats.get("abortedAfterPairs", 0) + 1, stats.get("pairs", 0), pair[0], pair[1]))
    else:
        where = "after %d of %s" % (stats.get("abortedAfterPairs", 0), _pairs(stats.get("pairs", 0)))
    # Stopped before any pair was handled, it had written nothing to discard.
    saved = ("The rows it had already written were discarded, so no partial result was saved."
             if stats.get("abortedAfterPairs", 0) else "No result was saved.")
    # scipy ends its messages with a full stop ("... length at least 2.").
    return ("The scoring step failed on our side %s, with the error: %s. %s"
            % (where, str(stats["aborted"]).rstrip("."), saved)), True


def noScoreNotice(stats):
    """What the job says when miRNA2Target wrote its pairs without a score, or None.

    Such a run has a result: every pair, carried onto its gene. It has no selection,
    and the reader has to be told that, or the empty relevant list reads as a
    cutoff that nothing passed.
    """
    reason = stats.get("noScore") if isinstance(stats, dict) else None
    if not reason:
        return None
    if reason == "foldChange":
        why, fix = "The score method \"fc\" (fold change) computes no score", "a correlation score method"
    else:
        why, fix = "No score could be computed without gene expression", "a gene expression file"
    return ("%s: %s and none was marked as relevant. Every pair read is in the results, "
            "with nan as its score. To select pairs, use %s or a relevant associations file."
            % (why, _pairsWereRead(stats.get("pairs", 0)), fix))


def unfinishedRunError(stats):
    """What the job raises when miRNA2Target stopped before the end, or None.

    The account is the only thing such a run leaves: its output file is gone, so
    the job must not go on to read one.
    """
    if isinstance(stats, dict) and (stats.get("aborted") or stats.get("headerMismatch")):
        return emptyResultError(stats)
    return None


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
    return _emptyResultExplanation(stats)[0]


def _emptyResultExplanation(stats):
    """explainEmptyResult's text, and whether that text reports a crash.

    Both come from the branch that writes the sentence, so they cannot disagree.
    stats["aborted"] cannot say it alone: pearson on a single condition aborts too,
    and what it is told is the too-few-conditions explanation. Any other run that
    stopped before the end is told by _scoringFailure, ahead of the chain, whose
    opening sentence would be untrue of it.
    """
    if not isinstance(stats, dict):
        return ("Your mirna2gene association process did not return any "
                "result. Please, check the files (same identifiers, etc) and "
                "parameters."), False

    failure = _scoringFailure(stats)
    if failure is not None:
        return failure

    dropped = stats.get("dropped") or {}
    # Said of the runs where no pair formed. Where pairs were read and the scoring
    # is what came up empty, this opening would contradict the sentence after it
    # ("2 miRNA-target pairs were read"), so those branches open on what is true.
    lines = ["No miRNA was matched to a target gene, so there is nothing to analyse."]
    noCorrelation = "No correlation could be computed, so there is nothing to analyse."

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
        # One miRNA is its own example, and "none of them" of one reads wrong; it
        # said "holds 1 miRNAs (e.g. ...), but none of them appears".
        regulators = stats.get("regulators", 0)
        lines.append(
            "Your miRNA quantification file holds %s, but %s in the first column "
            "of your targets/associations file. The two files have to use the "
            "same miRNA identifiers."
            % (("1 miRNA (%s)" % sample(stats.get("sampleRegulators")), "it does not appear")
               if regulators == 1 else
               ("%d miRNAs (e.g. %s)" % (regulators, sample(stats.get("sampleRegulators"))),
                "none of them appears")))
    # 2. They joined, but not one target id exists in the expression file. Scoring
    #    only ever aborts on a target it found, before counting it as scored, so
    #    after an abort a target missing earlier in the file is not the cause.
    elif (stats.get("scored", 0) == 0 and stats.get("unmatchedTargets", 0)
          and not stats.get("aborted")):
        lines.append(
            "%s, but not one target gene was found in your gene expression file, "
            "so no correlation could be computed." % _pairsWereRead(stats.get("pairs", 0)))
        nearMiss = stats.get("sampleNearMiss")
        if stats.get("nearMisses") and nearMiss:
            # Counted in agreement: it read "1 of the first 1 unmatched targets
            # differ" for a single unmatched target.
            nearMisses, of = stats["nearMisses"], stats.get("nearMissesOf", 0)
            if of == 1:
                who = "The one unmatched target differs"
            else:
                who = "%d of the first %d unmatched targets %s" % (
                    nearMisses, of, "differs" if nearMisses == 1 else "differ")
            lines.append(
                "%s from a gene id only in case or in a version suffix (%s%s in "
                "the targets file, %s in the expression file) -- the identifiers "
                "are the same kind, written differently."
                % (who, "" if nearMisses == 1 else "e.g. ", nearMiss[0], nearMiss[1]))
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
        lines[0] = noCorrelation
        lines.append(
            "Your data has %d condition column, and a correlation needs at "
            "least two: with a single column every pair is a tie, so there is "
            "nothing to correlate. %s. Either "
            "supply the conditions you want correlated, or provide a relevant "
            "associations file so the pairs are taken from it instead of being "
            "scored." % (conditions, _pairsWereRead(stats.get("pairs", 0))))
    elif stats.get("scored", 0) and stats.get("nanScores", 0) == stats.get("scored", 0):
        scored = stats.get("scored", 0)
        lines[0] = noCorrelation
        lines.append(
            "%s gave an undefined correlation (nan): %s identical across the "
            "conditions, so there is nothing to correlate. Check that the "
            "condition columns differ, or provide a relevant associations file."
            % (("The one scored miRNA-target pair", "its values are") if scored == 1
               else ("All %d scored miRNA-target pairs" % scored, "each pair's values are")))
    else:
        lines.append(
            "%s and %d %s scored, but none carried a usable target gene."
            % (_pairsWereRead(stats.get("pairs", 0)), stats.get("scored", 0),
               "was" if stats.get("scored", 0) == 1 else "were"))

    # 4. Blank cells, reported whenever there were any -- they are usually the
    #    reason a file "looks fine" and matches nothing.
    # "1 row of ...", where it read "1 rows of ...".
    blanks = []
    if dropped.get("regulators"):
        blanks.append("%s of the quantification file had no miRNA id"
                      % _plural(dropped["regulators"], "row"))
    if dropped.get("associationRegulators"):
        blanks.append("%s of the associations file had an empty first column"
                      % _plural(dropped["associationRegulators"], "row"))
    if dropped.get("associationTargets"):
        blanks.append("%s of the associations file had an empty second column"
                      % _plural(dropped["associationTargets"], "row"))
    if dropped.get("genes"):
        blanks.append("%s of the gene expression file had no gene id"
                      % _plural(dropped["genes"], "row"))
    if blanks:
        lines.append("Rows skipped because an identifier was blank: " +
                     "; ".join(blanks) + ".")

    return " ".join(lines), False


def emptyResultError(stats):
    """What a run with nothing to show raises, going by what its text explains.

    A refusal of the files, which the dialog encodes because the message quotes
    their first columns. That holds for a single condition column under every
    method: pearson raises on the first target it finds and kendall and spearman
    score nan, and all three read the same sentence, with the same count of pairs,
    whichever row comes first. It holds for a header that does not fit its rows
    and for a row with a cell that is not a number, whatever the method. Only a
    scoring step that failed on our side is no refusal: it stays an error that can
    be reported. Its text can quote the files (the pair it failed on, and the
    error), and it is returned as it is: the page encodes it once where it shows
    it. Escaping it here as well showed "&lt;" in the Step 1 dialog, which encodes
    it too.
    """
    message, reportsACrash = _emptyResultExplanation(stats)
    if reportsACrash:
        return Exception(message)
    return InputFileError(message)


class MiRNA2GeneJob(Job):
    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self, jobID, userID, CLIENT_TMP_DIR):
        super(MiRNA2GeneJob, self).__init__(jobID, userID, CLIENT_TMP_DIR)
        self.omicName = None
        self.report               = "all" #all or DE
        self.score_method         = "kendall" #kendall OR spearman OR pearson; none when nothing could be scored
        self.selection_method     = "negative_correlation" #one of SELECTION_METHODS; none when nothing could be scored
        self.cutoff               = -0.6
        self.enrichment           = "genes"
        self.notice               = None #what a run that succeeded could not do, for the response

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
        #TODO: CHECK VALID SCORE METHODS
        # On its own, not under "Errors detected in input files": no file is at fault.
        if (self.selection_method not in SELECTION_METHODS
                and self.selection_method not in RETIRED_SELECTION_METHODS):
            raise InputFileError(unknownSelectionText(self.selection_method))
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
            raise InputFileError("Errors detected in input files, please fix the following issues and try again:" + error)

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

        # A retired selection method runs as what it always did, under that name, so
        # the stored options, the description and the Selection column say what was
        # done (see RETIRED_SELECTION_METHODS).
        sentSelection = self.selection_method
        self.selection_method = RETIRED_SELECTION_METHODS.get(sentSelection, sentSelection)

        #STEP 2. CALL TO miRNA2Target SCRIPT AND GENERATE ASSOCIATION BETWEEN miRNAS AND TARGET GENES
        logging.info("STARTING miRNA2Target PROCESS.")
        matchStats = run_miRNA2Target(referenceFile, relevantReferenceFile, dataFile, geneExpressionFile, tmpFile, self.score_method)
        logging.info("STARTING miRNA2Target PROCESS...Done")
        logging.info("miRNA2Target ACCOUNT: %s", matchStats)
        # A run that stopped before the end left no output file, only its
        # account. It used to leave the rows it had written, and only a run with
        # no row at all was refused below, so a scoring step that stopped after
        # one pair of three was reported as a success with a third of the result.
        unfinished = unfinishedRunError(matchStats)
        if unfinished is not None:
            raise unfinished
        # Rows whose every score is nan are not results: nan > cutoff is False,
        # so such a run "succeeded" with zero relevant associations and no word
        # about why (two identical condition columns do this for kendall,
        # pearson and spearman alike).
        if isinstance(matchStats, dict) and matchStats.get("scored") \
                and matchStats.get("nanScores") == matchStats.get("scored"):
            raise emptyResultError(matchStats)

        # No score means no selection, so the stored options, the description and
        # the results' Selection column say so, rather than naming a correlation
        # that was never computed. nan > cutoff is False under every selection
        # method below, so no pair is called relevant.
        self.notice = noScoreNotice(matchStats)
        if self.notice:
            logging.info("MIRNA2GENES - %s", self.notice)
            self.score_method = self.selection_method = NO_SCORE_METHOD
        elif sentSelection != self.selection_method and not relevantReferenceFile:
            # Only where a correlation selected the pairs: with a relevant
            # associations file the selection method is not used.
            self.notice = retiredSelectionNotice(sentSelection, self.selection_method)
            logging.info("MIRNA2GENES - %s", self.notice)

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

        if not os_path.isfile(tmpFile):
            # miRNA2Target writes this file whenever it finishes, so this is ours.
            raise Exception("The scoring step finished without writing its result file.")

        if os_path.isfile(tmpFile):
             with open(tmpFile, 'r') as inputDataFile:
                mirnaID = geneID = score = sortedScores = None
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

                    isRelevant = mirnaID.lower() in relevantMiRNAS
                    isRelevantAssociation = False

                    if useCorrelation:
                        if self.selection_method == "negative_correlation":
                            score *= -1  #INVERT VALUES
                        elif self.selection_method == "abs_correlation":
                            score = abs(score)
                        # positive_correlation, and none, compare the score as it is.

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
                    raise emptyResultError(matchStats)

                #STEP 6. FOR EACH GENE, ORDER THE MIRNAS BY THEIR SCORE IN THE DIRECTION OF THE SELECTION
                filePrefix = '' if self.getUserID() is not None else self.getJobID() + '_'
                randomSeed = str(randint(0, 1000))
                genesToMiRNAFile = open(self.getTemporalDir() + '/' + filePrefix + 'genesToMiRNAFile.tab', 'w')
                regulator2genesOutput = open(self.getTemporalDir() + '/' + filePrefix + "regulator2Gene_output_" + self.date + "_" + randomSeed +  ".tab", 'w')
                regulator2genesRelevant = open(self.getTemporalDir() + '/' + filePrefix + "regulator2Gene_relevant_" + self.date + "_" + randomSeed + ".tab", 'w')

                # Associations files
                regulatorAssociations = open(self.getTemporalDir() + '/' + filePrefix + "regulator_associations" + self.date + "_" + randomSeed + ".tab", 'w')
                regulatorRelevantAssociations = open(self.getTemporalDir() + '/' + filePrefix + "regulator_relevant_associations" + self.date + "_" + randomSeed + ".tab", 'w')

                # PRINT HEADER
                if self.notice:
                    genesToMiRNAFile.write("# " + self.notice + "\n")
                genesToMiRNAFile.write("# Gene name\tmiRNA ID\tDE\tScore\tSelection\n")
                #TODO: RE-ENABLE THIS CODE
                regulator2genesOutput.write("# Gene name\t"+ header + "\n")
                regulator2genesRelevant.write("# Gene name\tmiRNA ID\n")

                logging.info("ORDERING miRNAS BY CORRELATION...")
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
                        if self.selection_method == "negative_correlation":
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
                    raise InputFileError(
                        "No target gene carried an identifier (%s read), so "
                        "every association row would have been written with an "
                        "empty name. Check that the second column of your "
                        "associations file holds the target gene ID."
                        % _plural(skippedUnnamed, "target gene"))

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
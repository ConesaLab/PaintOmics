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

import re
import getopt
import sys
import os.path
import numpy as np
import scipy.stats
from csv import reader as csv_reader

def main():
    try:
        opts, args = getopt.getopt(sys.argv[1:], "h:m:g:r:i:o:", ["help", "method=", "genes=", "reference=", "mirna=", "output="])
    except getopt.GetoptError as err:
        print(err) # will print something like "option -a not recognized"
        print_usage()
        sys.exit(2)

    referenceFile = None
    dataFile = None
    geneExpresion = None
    outputfile = None
    method = None

    if len(opts) == 0:
        print_usage()

    for o, a in opts:
        if o in ("-h","--help"):
            print_usage()
            sys.exit()
        elif o in ("-r", "--reference"):
            if os.path.isfile(a):
                referenceFile = a
            else:
                sys.stderr.write("\nERROR: Reference file not recognized.\n")
                print_usage()
                sys.exit()
        elif o in ("-i", "--mirna"):
            if os.path.isfile(a):
                dataFile = a
            else:
                sys.stderr.write("\nERROR: miRNA file not recognized.\n")
                print_usage()
                sys.exit()
        elif o in ("-o", "--output"):
            outputfile = a
        elif o in ("-g", "--genes"):
            geneExpresion = a
        elif o in ("-m", "--method"):
            method = a
        else:
            print("Unknown option")
            print("Use python miRNA2Target.py -h for help")

    if referenceFile is not None and dataFile is not None and outputfile is not None:
        run(referenceFile, dataFile, geneExpresion, outputfile, method)

def print_usage():
    print("\nUsage: python miRNA2Target.py [options] <mandatory>")
    print("Options:")
    print("\t-m, --method:\n\t\t The score method, accepted values are 'fc' (Fold change) | 'spearman' | 'kendall' | 'pearson'")
    print("\t-g, --genes:\n\t\t A file containing the gene expression quantification. These values are necessary to calculate the correlation between miRNAs and targets.")
    print("\t-h, --help:\n\t\t show this help message and exit")
    print("Mandatory:")
    print("\t-r, --reference:\n\t\t The reference file")
    print("\t-i, --mirna:\n\t\t The miRNA quantification file")
    print("\t-o, --output:\n\t\t Output file")
    print("\nVersion 0.1 August 2016\n")


def toFloats(values):
    """Decimal text -> float, or the cells untouched if any of them is not a
    number.

    Called once per row while the files are read, instead of once per
    (miRNA, target) pair inside getScore(): the same miRNA row used to be
    re-parsed for every one of its targets (209 of them on average in the
    STATegra example), and a gene row once for every miRNA targeting it.

    The fallback is not defensive padding, it is required. The gene expression
    file is read WITHOUT skipping its header, so `geneTable` legitimately
    contains a row keyed '#geneID' whose cells are condition names. Converting
    eagerly and letting that raise would abort the read before the output file
    is even opened -- a different failure, and a different (empty) output, than
    the one this script has always produced. Keeping the raw cells makes such a
    row fail exactly where it failed before: inside the scoring loop, on the
    first pair that actually uses it, under the blanket handler that keeps the
    partial file.
    """
    try:
        return [float(value) for value in values]
    except ValueError:
        return values


def asFloats(values):
    """Convert only if the caller has not already done it (see toFloats)."""
    if values and type(values[0]) is str:
        return [float(value) for value in values]
    return values


def run(referenceFile, relevantReferenceFile, dataFile, geneExpresion, corrOutputFile, method="fc"):
    miRNAtable = {}
    geneTable = {}
    dataFile_header = None
    rawHeader = None

    # What was dropped and what was joined, so the caller can say why an empty
    # result is empty instead of asking the user to "check the files". Counters
    # and at most three example ids per side: the point is to name the two
    # identifier spaces, not to carry the files around a second time.
    dropped = {"regulators": 0, "associationRegulators": 0,
               "associationTargets": 0, "genes": 0}
    seenTargets = set()
    unmatchedSample = []
    stats = {"dropped": dropped, "pairs": 0, "scored": 0, "unmatchedTargets": 0,
             "nanScores": 0}
    unmatchedDistinct = set()   # bounded: the first 2000 distinct unmatched targets
    geneOrder = []              # the first gene ids in FILE order, header included

    #STEP 1. GENERATE THE TABLE WITH ALL THE MIRNAS IN THE INPUT
    print("STEP 1. Reading miRNA expression file...")
    with open(dataFile, 'r') as inputDataFile:
        for line in csv_reader(inputDataFile, delimiter="\t"):
            if rawHeader is None:
                rawHeader = line
                continue

            if dataFile_header is None:
                # Whether the header labels the ID column varies between files:
                # gene_expression_values.tab opens with "#geneID", while
                # mirna_unmapped_values.tab goes straight into "I/C_0h". This
                # used to always drop the first cell, so for the latter the
                # first condition was consumed as if it were the ID label --
                # the example emitted 5 condition names above 6 columns of
                # data, leaving every value mislabelled downstream. Decide from
                # the width of the first data row instead of assuming.
                nValues = len(line) - 1
                if len(rawHeader) == nValues:
                    dataFile_header = rawHeader          # no label for the ID column
                else:
                    dataFile_header = rawHeader[1:]      # first cell labels the ID column

            # An unnamed row is not a feature.
            #
            # `""` is a perfectly good dict key, so a row whose id cell is
            # blank becomes a regulator called "" -- and then joins to every
            # OTHER blank cell in the other two files. That is not a corner
            # case: it is what happened to a real user (2026-08-27). Their
            # targets file carried 6,039 rows with an empty target id and their
            # expression file 13 rows with an empty gene id, so `""` matched
            # `""` and those 6,039 pairs were the ONLY ones that scored -- every
            # real target was an ENSMUSG id while the expression file was keyed
            # by gene symbol, an overlap of zero. PaintOmics then reported
            # success and handed back an associations file of 6,039 rows whose
            # target column was blank from top to bottom.
            #
            # Dropped here, at the read, so no later stage can join on nothing.
            if not line:
                continue            # a trailing empty line is not a row
            if not line[0].strip():
                dropped["regulators"] += 1
                continue

            # "values" stays as text because it is written back out verbatim on
            # every result row; "floats" is the same row parsed once, for the
            # correlation. The key is stripped: "G1 " and "G1" are one id.
            miRNAtable[line[0].strip()] = {"values" : line[1:], "targets" : list(),
                                           "floats" : toFloats(line[1:])}
    inputDataFile.close()

    if dataFile_header is None:
        dataFile_header = rawHeader[1:] if rawHeader else []

    #STEP 2. FILL THE TABLE WITH ALL THE TARGETS FOR EACH MIRNA
    print("STEP 2. Reading miRNA -> targets file...")

    with open(referenceFile, 'r', encoding='utf-8-sig', errors='replace') as inputDataFile:
        for line in csv_reader(inputDataFile, delimiter="\t"):
            # Half a pair is not a pair. A row missing either id is counted and
            # dropped rather than joined on a blank -- see STEP 1.
            if not line:
                continue
            if not line[0].strip():
                dropped["associationRegulators"] += 1
                continue
            if len(line) < 2 or not line[1].strip():
                dropped["associationTargets"] += 1
                continue
            regulator, target = line[0].strip(), line[1].strip()
            if regulator in miRNAtable:
                miRNAtable[regulator]["targets"].append(target)
                seenTargets.add(target)
    inputDataFile.close()


    # STEP 4. FOR EACH REGULATOR AND EACH TARGET, CALCULATE THE SCORE AND SAVE RESULTS TO A FILE IF NO
    # RELEVANT ASSOCIATIONS WERE PROVIDED.
    useCorrelation = relevantReferenceFile is None or relevantReferenceFile == ''

    try:
        print("STEP 3. Processing miRNAs and calculating score...")

        # STEP 3. FILL THE TABLE WITH ALL THE GENES
        if geneExpresion is not None:
            print("STEP 3. Processing mRNA expression file...")
            with open(geneExpresion, 'r') as inputDataFile:
                for line in csv_reader(inputDataFile, delimiter="\t"):
                    if not line:
                        continue
                    if not line[0].strip():
                        dropped["genes"] += 1
                        continue
                    # Only ever read by getScore, so it can be stored parsed.
                    geneTable[line[0].strip()] = toFloats(line[1:])
                    if len(geneOrder) < 4:
                        geneOrder.append(line[0].strip())
            inputDataFile.close()
        else:
            print("STEP 3. No mRNA expression file was provided...")
            method = "fc"

        # A correlation over a single condition is not a weak correlation, it
        # is undefined -- scipy raises "x and y must have length at least 2"
        # for pearson/spearman, and kendall returns nan for every pair because
        # every pair is a tie. Both were invisible: the raise landed in the
        # blanket handler below, which keeps the partial file, so a user with
        # one condition got an EMPTY result and was told their identifiers did
        # not match; kendall got them 30,722 rows of "nan". Recorded here so
        # the caller can say which it is.
        stats["conditions"] = len(dataFile_header)
        if useCorrelation and method != "fc" and len(dataFile_header) < 2:
            stats["tooFewConditions"] = len(dataFile_header)

        outputFile = open(corrOutputFile, 'w')
        outputFile.write("# miRNA_id\ttarget_id\tscore\tscore method\t" + "\t".join(dataFile_header) + "\n")

        miRNA_id = miRNA_values = miRNA_targets = target_id = target_values = score = None
        total = len(miRNAtable.keys())
        current=0
        for miRNA_id in miRNAtable:
            current+=1
            if (current*100/total) % 20 == 0:
                print("Processed " + str(current*100/total) + "% of " + str(total) + " total miRNAs")

            miRNA_values = miRNAtable[miRNA_id]["values"]
            miRNA_floats = miRNAtable[miRNA_id]["floats"]
            miRNA_targets = miRNAtable[miRNA_id]["targets"]

            # Both are the same for every target of this miRNA, so they are
            # built once here rather than once per pair.
            rowPrefix = miRNA_id + "\t"
            rowSuffix = "\t" + "\t".join(miRNA_values) + "\n"

            # One writelines() per miRNA instead of one write() per pair. The
            # rows are appended in the order they were produced, so the file is
            # byte for byte the one the per-pair writes produced. The finally
            # matters for the one case where that is not obvious: if a pair
            # raises, the handler below keeps whatever is in the file, so the
            # rows this miRNA already produced have to reach it -- otherwise a
            # failing run would truncate at a different place than it used to.
            rows = []
            try:
                for target_id in miRNA_targets:
                    stats["pairs"] += 1
                    if useCorrelation:
                        target_values = geneTable.get(target_id, None)
                        if target_values is None:
                            stats["unmatchedTargets"] += 1
                            if len(unmatchedSample) < 3 and target_id not in unmatchedSample:
                                unmatchedSample.append(target_id)
                            if len(unmatchedDistinct) < 2000:
                                unmatchedDistinct.add(target_id)

                        if method == "fc" or target_values is not None:
                            score = getScore(miRNA_floats, target_values, method)
                            # nan is what every correlation returns when a
                            # pair is all ties (two identical columns, say):
                            # it is written, it is not a result.
                            if score != score:
                                stats["nanScores"] += 1
                            rows.append(rowPrefix + target_id + "\t" + str(score) + "\t" + method + rowSuffix)
                            stats["scored"] += 1
                    else:
                        rows.append(rowPrefix + target_id + "\t" + str(0) + "\t" + "Association" + rowSuffix)
                        stats["scored"] += 1
            finally:
                outputFile.writelines(rows)
    except Exception as e:
        # Kept as a catch-all so a bad row cannot lose the rows already
        # written, but no longer SILENT: an abort truncates the output, and a
        # truncated file read as "no results" is how a crash got reported to a
        # user as an identifier mismatch.
        print("Exception catched " +  str(e))
        stats["aborted"] = str(e)
        stats["abortedAfterPairs"] = stats["pairs"]
    finally:
        print("STEP 5. Done")
        outputFile.close()

    # The three identifier spaces, named. When nothing joined, which of them
    # failed to meet is the whole answer, and it is not derivable from the
    # empty output file the caller is holding.
    stats["regulators"] = len(miRNAtable)
    stats["genes"] = len(geneTable)
    stats["targets"] = len(seenTargets)
    stats["sampleRegulators"] = sorted(miRNAtable)[:3]
    stats["sampleTargets"] = sorted(seenTargets)[:3]
    # In FILE order and past the first line: sorted() put the header first
    # ("#geneID" sorts before every letter), so the example identifier the
    # message offered was the header.
    stats["sampleGenes"] = (geneOrder[1:4] if len(geneOrder) > 1 else geneOrder[:3])
    stats["sampleUnmatchedTargets"] = unmatchedSample
    # Near misses: an unmatched target that differs from a gene id only in
    # case or in an Ensembl version suffix is not "a different identifier
    # space", and saying so sends the user to the wrong conversion.
    if unmatchedDistinct and geneTable:
        def fold(identifier):
            return re.sub(r"\.\d+$", "", identifier).lower()
        foldedGenes = {}
        for geneID in geneTable:
            foldedGenes.setdefault(fold(geneID), geneID)
        nearMisses = [(t, foldedGenes[fold(t)]) for t in sorted(unmatchedDistinct)
                      if fold(t) in foldedGenes]
        stats["nearMisses"] = len(nearMisses)
        stats["nearMissesOf"] = len(unmatchedDistinct)
        stats["sampleNearMiss"] = list(nearMisses[0]) if nearMisses else None
    stats["usedCorrelation"] = useCorrelation
    stats["method"] = method
    return stats


# scipy returns a numpy scalar from every kendalltau path, the degenerate ones
# included; `np.nan` is the Python builtin float. Under numpy 1.x both repr()'d
# as "nan", so returning the wrong one of the two was invisible -- and scipy
# 1.13 itself returned a bare float there, so the mismatch cost nothing. numpy 2
# prints numpy scalars as "np.float64(nan)" and scipy 1.17 returns np.float64 on
# those paths, which is what made it visible. str() is "nan" for both on both
# versions, so run()'s `str(score)` output file is unchanged either way; this is
# about honouring the type contract test_returns_the_same_type_as_scipy states.
_NAN = np.float64(np.nan)


def kendallTauB(x, y):
    """
    scipy.stats.kendalltau(x, y).correlation for the short numeric rows this
    converter scores (n_conditions values per feature), bit for bit.

    kendalltau spends most of its ~58 us per call on the p-value -- an exact
    enumeration for short tie-free rows -- which the caller never reads; on
    the shipped STATegra example that is 97,983 pairs. The concordant,
    discordant and tied pair counts are the same integers scipy derives (its
    dis from _kendall_dis, xtie/ytie/ntie from the dense ranks), and the
    finish is scipy's own expression on them --
    ``con_minus_dis / np.sqrt(tot - xtie) / np.sqrt(tot - ytie)`` clamped
    with ``np.minimum(1., max(-1., tau))`` -- so the float, and its str()
    that reaches the output file, are unchanged: verified against scipy over
    120,000 random rows (ties, constants, perfect (anti)correlation, NaN,
    lengths 1-12) and pinned by test_kendall_kernel_matches_scipy. Rows that
    are not all numbers (a header or a text cell that toFloats() refused) go
    to scipy itself, which compares them however it always did.
    """
    n = len(x)
    if n != len(y) or n == 0:
        return scipy.stats.kendalltau(x, y).correlation
    for value in x:
        if not isinstance(value, (int, float)):
            return scipy.stats.kendalltau(x, y).correlation
    for value in y:
        if not isinstance(value, (int, float)):
            return scipy.stats.kendalltau(x, y).correlation
    for value in x:
        if value != value:  # NaN propagates, as scipy's nan_policy does
            return _NAN
    for value in y:
        if value != value:
            return _NAN
    tot = (n * (n - 1)) // 2
    xtie = ytie = con = dis = 0
    for i in range(n - 1):
        xi = x[i]
        yi = y[i]
        for j in range(i + 1, n):
            dx = xi - x[j]
            dy = yi - y[j]
            if dx == 0:
                xtie += 1
                if dy == 0:
                    ytie += 1
            elif dy == 0:
                ytie += 1
            elif (dx > 0) == (dy > 0):
                con += 1
            else:
                dis += 1
    if xtie == tot or ytie == tot:
        return _NAN
    con_minus_dis = con - dis
    tau = con_minus_dis / np.sqrt(tot - xtie) / np.sqrt(tot - ytie)
    return np.minimum(1., max(-1., tau))


def getScore(values_1, values_2, method):
    """The score for one (miRNA, target) pair.

    Both arguments may arrive already parsed (run() does that once per row) or
    as raw text (any other caller, and rows toFloats() refused); asFloats()
    settles it. scipy is still the numeric authority for all three
    correlations -- the same function, on the same values -- so the scores, and
    therefore their str(), are unchanged.
    """
    if method == "fc":
        #CALCULATE THE FOLD CHANGE
        import random
        return random.uniform(-1, 1)
    elif method == "spearman":
        #CALCULATE THE CORRELATION USING SPEARMAN
        return scipy.stats.spearmanr(asFloats(values_1), asFloats(values_2)).correlation
    elif method == "kendall":
        #CALCULATE THE CORRELATION USING KENDALL (same value as scipy, see kendallTauB)
        return kendallTauB(asFloats(values_1), asFloats(values_2))
    elif method == "pearson":
        #CALCULATE THE CORRELATION USING PEARSON
        return scipy.stats.pearsonr(asFloats(values_1), asFloats(values_2))[0]

if __name__ == "__main__":
    main()

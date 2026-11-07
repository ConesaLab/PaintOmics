#!/usr/bin/env python3
"""A column of identifiers names its organism -- and the hint knows when to stay quiet.

The failures this comes from (paintomics.uv.es, 2026-09)
--------------------------------------------------------
Every zero-match failure retained in the logs was identifiers from one
organism run against another:

    fgr   'Gene expression' (2299), e.g. YAL001C, YAL003W, YAL010C   matched 0
    cit   'Gene expression' (138),  e.g. Aste1, Ackr1, Drap1         matched 0
    hsa   the same user, the same file, one more guess              matched 0
    zma   'Proteomics' (8242),      e.g. A0A0R0E5C1, A0A0R0E5J0      matched 0

The message told each of them to "check the organism you chose". None could
tell which organism to check. The first is yeast ORF names (the file's own
header read "id sce"); the second is mouse-cased gene symbols, which mouse
and rat share and human writes in capitals; the third is UniProt.

What is pinned here
-------------------
* The pattern layer resolves species-specific conventions from the string
  alone, full-match only, and abstains under 80 % coverage.
* The lookup layer speaks only for one organism at >= 50 % with no runner-up
  within a factor of two; equal fits are a tie, reported and never applied.
* The organism already chosen is looked up FIRST and, when it recognises the
  file, the answer is "nothing to say" after that one query.
* A pattern naming an organism this server lacks is reported as such, not as
  a suggestion to pick something that is not in the combo.
* The step 2 message appends the verdict, and a detector that raises leaves
  the message exactly as it was.
* Maize v5 and grape ids name their organism (real ids, read from the zma and
  vvi databases on paintomics.org, MaizeGDB, Ensembl Plants, UniProt, Legume
  Information System and NCBI on 2026-11-07).
* The verdict's count says what was counted: identifiers the organism's index
  holds, or identifiers that only have its form. Maize v4 transcript ids,
  which no index holds, read "150 of 150 match its identifier index". When the
  index holds none, the verdict does not promise that choosing the organism
  helps; when the index gave no answer, it does not say it holds none.

Everything runs without MongoDB: the lookup is a dict.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_organism_hint_from_identifiers
"""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

os.environ.setdefault("PAINTOMICS_KEGG_DATA", tempfile.mkdtemp(prefix="kegg-"))
os.environ.setdefault("PAINTOMICS_CLIENT_TMP", tempfile.mkdtemp(prefix="client-"))

from src.common import OrganismDetector as od  # noqa: E402
from src.classes.JobInstances.PathwayAcquisitionJob import explainEmptyMapping  # noqa: E402


class FakeLookup(object):
    """hits(code, ids) = how many ids are in table[code]; remembers who was asked."""

    def __init__(self, table, installed=None):
        self.table = table
        self._installed = set(installed if installed is not None else table)
        self.asked = []

    def installed(self):
        return self._installed

    def hits(self, code, identifiers):
        self.asked.append(code)
        return len(set(identifiers) & set(self.table.get(code, ())))


YEAST = ["YAL001C", "YAL003W", "YAL010C", "YAL012W", "YAL013W", "YAL014C", "YAL015C", "YAL020C"]
MOUSE_SYMBOLS = ["Aste1", "Ackr1", "Drap1", "Snapc4", "Rec114", "Actb", "Gapdh", "Trp53"]
HUMAN_SYMBOLS = [s.upper() for s in MOUSE_SYMBOLS]
NAMES = {"sce": "Saccharomyces cerevisiae (budding yeast)", "mmu": "Mus musculus (house mouse)",
         "rno": "Rattus norvegicus (rat)", "hsa": "Homo sapiens (human)",
         "fgr": "Fusarium graminearum", "ath": "Arabidopsis thaliana (thale cress)"}


class SampleHygieneTest(unittest.TestCase):

    def test_distinct_printable_strings_in_order_and_capped(self):
        raw = ["  A ", "B", "A", "", None, 12, "x" * 81, "C\x00", "D"]
        self.assertEqual(od.sanitiseIdentifiers(raw), ["A", "B", "D"])
        self.assertEqual(len(od.sanitiseIdentifiers(["id%d" % i for i in range(1000)])),
                         od.MAX_IDENTIFIERS)
        self.assertEqual(od.sanitiseIdentifiers("not a list"), [])


class PatternLayerTest(unittest.TestCase):

    def test_species_specific_conventions_resolve_from_the_string(self):
        cases = {
            ("ENSMUSG00000048388", "ENSMUSG00000079462", "ENSMUSG00000059382", "ENSMUSG00000026924",
             "ENSMUSG00000034855"): ("mmu",),
            ("ENSMUSG00000102693.2", "ENSMUSG00000064842.3", "ENSMUSG00000051951.6",
             "ENSMUSG00000089699.2", "ENSMUSG00000103377.1"): ("mmu",),   # versioned: organism still evident
            ("ENSG00000230523", "ENSG00000141510", "ENSG00000012048", "ENSG00000139618",
             "ENSG00000171862"): ("hsa",),
            tuple(YEAST): ("sce",),
            ("AT1G30814", "AT5G64350", "AT3G04615", "AT2G36130", "AT1G04537.1"): ("ath",),
            ("FOXG_00881", "FOXG_02128", "FOXG_11296", "FOXG_02529", "FOXG_08326"): ("fox",),
            ("FGSG_00903", "FGSG_01417", "FGSG_09042", "FGSG_03981", "FGSG_09739"): ("fgr",),
            ("SPBC15C4.01c", "SPAC23C4.06c", "SPAC227.15", "SPBPB2B2.19c", "SPBC6B1.02"): ("spo",),
            # The sorghum DESeq2 table of 2026-09-26 (Phytozome gene ids).
            ("Sobic.001G215100", "Sobic.001G262200", "Sobic.007G085966", "Sobic.007G070300",
             "Sobic.009G242200"): ("sbi",),
            # Mercator's lower case, a download tag and a transcript still name it.
            ("sobic.001g215100", "sobic.001g262200", "Sobic.007G085966.v3.2", "Sobic.007G070300.1",
             "sobic.009g242200"): ("sbi",),
            ("Prupe.1G000100", "Prupe.1G000200", "Prupe.2G123400", "Prupe.8G000500",
             "Prupe.3G100900"): ("pper",),
            ("Pahal.1G000100", "Pahal.2G000200", "Pahal.9G123400", "Pahal.5G000500",
             "Pahal.3G100900"): ("phai",),
        }
        for identifiers, codes in cases.items():
            match = od.matchPattern(list(identifiers))
            self.assertIsNotNone(match, identifiers[0])
            self.assertEqual(match[0], codes, identifiers[0])

    def test_organism_agnostic_namespaces_do_not_match_any_pattern(self):
        for sample in (MOUSE_SYMBOLS, HUMAN_SYMBOLS,
                       ["118567839", "115487050", "723900", "14863", "17846"],   # Entrez
                       ["A0A0R0E5C1", "A0A0R0E5J0", "Q3UQ35", "P21537", "O74751"]):  # UniProt
            self.assertIsNone(od.matchPattern(sample), sample[0])

    def test_a_pattern_is_a_full_match_and_needs_most_of_the_sample(self):
        # 'ATP5' and 'ATG7' are symbols, not Arabidopsis loci: a prefix test would take them.
        self.assertIsNone(od.matchPattern(["ATP5", "ATG7", "ATM", "ATR", "ATRX"]))
        mostly = YEAST + ["junk1", "junk2"]                 # 8 of 10 = 80 %: still yeast
        self.assertEqual(od.matchPattern(mostly)[0], ("sce",))
        diluted = YEAST + ["junk%d" % i for i in range(6)]  # 8 of 14: abstain
        self.assertIsNone(od.matchPattern(diluted))


class DecisionRuleTest(unittest.TestCase):

    def scored(self, **fractions):
        return [{"code": code, "fraction": fraction, "hits": int(fraction * 100)}
                for code, fraction in fractions.items()]

    def test_one_clear_winner_is_confident(self):
        confident, candidates = od.decide(self.scored(mmu=0.95, rno=0.30, hsa=0.02))
        self.assertEqual(confident["code"], "mmu")
        self.assertEqual([c["code"] for c in candidates], ["mmu"])

    def test_two_equal_fits_are_a_tie_and_never_confident(self):
        confident, candidates = od.decide(self.scored(mmu=1.0, rno=1.0, hsa=0.0))
        self.assertIsNone(confident)
        self.assertEqual(sorted(c["code"] for c in candidates), ["mmu", "rno"])

    def test_a_runner_up_over_the_floor_does_not_by_itself_tie(self):
        """The case that made TIE_RATIO dead code.

        A human gene-symbol file: hsa matches everything, and bta/ssc carry
        many of the same uppercase ortholog symbols. Every one of them clears
        CONFIDENT_FRACTION, so a ratio at or below 0.5 tied them all and the
        combo could never be filled for the commonest file there is.
        """
        confident, candidates = od.decide(self.scored(hsa=1.00, bta=0.55, ssc=0.52))
        self.assertIsNotNone(confident)
        self.assertEqual(confident["code"], "hsa")
        self.assertEqual([c["code"] for c in candidates], ["hsa"])

    def test_a_runner_up_close_to_the_winner_still_ties(self):
        confident, candidates = od.decide(self.scored(mmu=0.95, rno=0.90))
        self.assertIsNone(confident)
        self.assertEqual(sorted(c["code"] for c in candidates), ["mmu", "rno"])

    def test_the_tie_ratio_is_above_the_floor_or_it_means_nothing(self):
        # Guards the arithmetic itself: with ranked already filtered to
        # >= CONFIDENT_FRACTION, a ratio <= CONFIDENT_FRACTION can never
        # exclude anything.
        self.assertGreater(od.TIE_RATIO, od.CONFIDENT_FRACTION)

    def test_nothing_at_half_is_silence(self):
        self.assertEqual(od.decide(self.scored(mmu=0.49, rno=0.4)), (None, []))
        self.assertEqual(od.decide([]), (None, []))

    def test_a_tie_shows_at_most_three(self):
        confident, candidates = od.decide(self.scored(a=1.0, b=1.0, c=1.0, d=1.0))
        self.assertIsNone(confident)
        self.assertEqual(len(candidates), 3)


class DetectOrganismTest(unittest.TestCase):

    def detect(self, identifiers, selected, table, installed=None, shortlist=None):
        lookup = FakeLookup(table, installed)
        result = od.detectOrganism(identifiers, selected=selected, lookup=lookup, names=NAMES,
                                   shortlistCodes=shortlist or ["hsa", "mmu", "rno", "ath", "sce"])
        return result, lookup

    def test_the_chosen_organism_that_fits_ends_after_one_query(self):
        table = {"mmu": MOUSE_SYMBOLS, "rno": MOUSE_SYMBOLS, "hsa": HUMAN_SYMBOLS}
        result, lookup = self.detect(MOUSE_SYMBOLS, "mmu", table)
        self.assertTrue(result["selectedOk"])
        self.assertEqual(result["method"], "selected")
        self.assertEqual(lookup.asked, ["mmu"])          # rat and human were never consulted
        self.assertIsNone(result["confident"])

    def test_davids_file_yeast_orf_names_run_as_fusarium(self):
        table = {"fgr": ["FGSG_00903"], "sce": YEAST}
        result, lookup = self.detect(YEAST, "fgr", table)
        self.assertFalse(result["selectedOk"])
        self.assertEqual(result["selected"]["hits"], 0)
        self.assertEqual(result["method"], "pattern")
        self.assertEqual(result["confident"]["code"], "sce")
        self.assertEqual(result["confident"]["name"], NAMES["sce"])
        self.assertEqual(result["confident"]["hits"], len(YEAST))
        self.assertNotIn("hsa", lookup.asked)              # a pattern answer scans no shortlist

    def test_a_pattern_organism_this_server_lacks_is_reported_not_suggested(self):
        table = {"fgr": ["FGSG_00903"], "mmu": MOUSE_SYMBOLS}    # no sce installed here
        result, _ = self.detect(YEAST, "fgr", table)
        self.assertIsNone(result["confident"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["uninstalled"], {"code": "sce", "name": NAMES["sce"]})

    def test_a_pattern_beats_an_xref_that_lacks_the_namespace(self):
        # Installed, but its xref knows none of these ids: the prefix is still
        # definitional, so the organism is named with the pattern's coverage --
        # and hits move with the fraction, or describeHint would print
        # "(0 of 8 match ...)" in a sentence claiming the organism.
        table = {"sce": ["something-else"], "fgr": []}
        result, _ = self.detect(YEAST, "fgr", table)
        self.assertEqual(result["confident"]["code"], "sce")
        self.assertGreaterEqual(result["confident"]["fraction"], od.PATTERN_FRACTION)
        self.assertGreaterEqual(result["confident"]["hits"],
                                int(round(od.PATTERN_FRACTION * len(YEAST))))
        self.assertIn("%d of %d" % (result["confident"]["hits"], len(YEAST)),
                      od.describeHint(result))

    def test_a_pattern_never_tells_you_to_pick_what_you_already_picked(self):
        """Versioned Ensembl ids run as their own species.

        selected scores 0 hits (KEGG knows them without the suffix), falls
        through to the pattern layer, and the pattern names mmu -- the organism
        already chosen. The step 2 message would then read "go back to step 1
        and choose Mus musculus" under a heading saying the job ran as mmu.
        """
        versioned = ["ENSMUSG%011d.%d" % (i, i % 9 + 1) for i in range(1, 12)]
        table = {"mmu": ["ENSMUSG00000000001"], "hsa": []}
        result, _ = self.detect(versioned, "mmu", table, shortlist=["hsa"])
        self.assertIsNone(result["confident"])
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["uninstalled"])
        self.assertIsNone(od.describeHint(result))

    def test_mouse_symbols_against_citrus_name_mouse_and_rat_as_a_tie(self):
        table = {"cit": ["Cs1g01000"], "mmu": MOUSE_SYMBOLS, "rno": MOUSE_SYMBOLS, "hsa": HUMAN_SYMBOLS}
        result, lookup = self.detect(MOUSE_SYMBOLS, "cit", table, shortlist=["hsa", "mmu", "rno"])
        self.assertEqual(result["method"], "lookup")
        self.assertIsNone(result["confident"])
        self.assertEqual(sorted(c["code"] for c in result["candidates"]), ["mmu", "rno"])
        self.assertEqual(lookup.asked, ["cit", "hsa", "mmu", "rno"])

    def test_the_same_symbols_against_human_still_point_at_mouse_and_rat(self):
        table = {"mmu": MOUSE_SYMBOLS, "rno": MOUSE_SYMBOLS, "hsa": HUMAN_SYMBOLS}
        result, _ = self.detect(MOUSE_SYMBOLS, "hsa", table, shortlist=["hsa", "mmu", "rno"])
        self.assertFalse(result["selectedOk"])              # human writes ASTE1, so 0 of 8
        self.assertEqual(sorted(c["code"] for c in result["candidates"]), ["mmu", "rno"])

    def test_no_organism_chosen_and_one_fit_is_confident(self):
        table = {"mmu": MOUSE_SYMBOLS, "hsa": HUMAN_SYMBOLS}
        result, _ = self.detect(MOUSE_SYMBOLS, None, table, shortlist=["hsa", "mmu"])
        self.assertIsNone(result["selected"])
        self.assertEqual(result["confident"]["code"], "mmu")

    def test_identifiers_nobody_recognises_are_silence(self):
        table = {"mmu": MOUSE_SYMBOLS, "hsa": HUMAN_SYMBOLS}
        result, _ = self.detect(["PB.1.1", "PB.1.2", "PB.2.1", "PB.3.1", "PB.4.4"], "mmu", table)
        self.assertTrue(result["success"])
        self.assertIsNone(result["confident"])
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["uninstalled"])

    def test_fewer_than_five_identifiers_is_not_worth_asking(self):
        lookup = FakeLookup({"mmu": MOUSE_SYMBOLS})
        result = od.detectOrganism(MOUSE_SYMBOLS[:4], selected="ath", lookup=lookup, names=NAMES)
        self.assertEqual(lookup.asked, [])
        self.assertIsNone(result["confident"])

    def test_a_lookup_that_raises_counts_as_no_hits(self):
        class Broken(FakeLookup):
            def hits(self, code, identifiers):
                if code == "rno":
                    raise RuntimeError("timed out")
                return FakeLookup.hits(self, code, identifiers)
        lookup = Broken({"mmu": MOUSE_SYMBOLS, "rno": MOUSE_SYMBOLS})
        result = od.detectOrganism(MOUSE_SYMBOLS, selected=None, lookup=lookup, names=NAMES,
                                   shortlistCodes=["mmu", "rno"])
        self.assertTrue(result["success"])
        self.assertEqual(result["confident"]["code"], "mmu")

    def test_the_shortlist_is_configurable_and_installed_only(self):
        table = {"mmu": MOUSE_SYMBOLS}
        result, lookup = self.detect(MOUSE_SYMBOLS, None, table, shortlist=["hsa", "mmu", "zzz"])
        self.assertEqual(lookup.asked, ["mmu"])            # hsa and zzz are not installed


class SharedClientSurvivesTest(unittest.TestCase):
    """The second request must work as well as the first.

    Deployed 2026-09-15 and the hint answered success:false for every request
    after the first: openLookup() had become a process-wide client while
    detectOrganism still closed it in its `finally`, left from when the client
    was built per call. pymongo 4.x then raises

        InvalidOperation: Cannot use MongoClient after close

    on the next operation, and the worker never recovers. Nothing caught it,
    because the development environment carries pymongo 3.11, where close()
    disconnects and the next operation reconnects -- so the bug is invisible
    exactly where it is cheapest to find. This fakes 4.x's behaviour instead of
    trusting the installed driver.
    """

    class ClosableClient(object):
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

        def use(self):
            if self.closed:
                raise RuntimeError("Cannot use MongoClient after close")

    def test_three_requests_in_a_row_all_answer(self):
        """The production path: no lookup passed, so detectOrganism opens its own.

        This has to go through openLookup(), because that is the only branch
        the old `finally` closed -- a test that injects a lookup takes the
        other branch and would have passed against the bug.
        """
        client = self.ClosableClient()
        table = {"mmu": MOUSE_SYMBOLS, "hsa": HUMAN_SYMBOLS}

        class Lookup(FakeLookup):
            def installed(inner):
                client.use()
                return FakeLookup.installed(inner)

            def hits(inner, code, identifiers):
                client.use()
                return FakeLookup.hits(inner, code, identifiers)

        shared = Lookup(table)
        shared.client = client
        original = od.openLookup
        od.openLookup = lambda: shared            # one client for the process
        try:
            for attempt in range(3):
                result = od.detectOrganism(MOUSE_SYMBOLS, selected=None, names=NAMES,
                                           shortlistCodes=["hsa", "mmu"])
                self.assertTrue(result["success"], "request %d answered success:false" % (attempt + 1))
                self.assertEqual(result["confident"]["code"], "mmu",
                                 "request %d lost the answer" % (attempt + 1))
        finally:
            od.openLookup = original
        self.assertFalse(client.closed, "the process-wide client must never be closed per request")

    def test_the_module_never_closes_a_client(self):
        """Belt and braces, and it names the line instead of printing the module."""
        import inspect
        offenders = [(number, line.strip())
                     for number, line in enumerate(inspect.getsource(od).splitlines(), 1)
                     if ".close()" in line and not line.lstrip().startswith("#")]
        self.assertEqual(offenders, [],
                         "OrganismDetector must never close the process-wide MongoClient -- "
                         "on pymongo 4 every later request then fails with InvalidOperation. "
                         "Offending line(s): %s" % offenders)


class HintWordingTest(unittest.TestCase):

    def test_the_failure_sentence_for_each_outcome(self):
        confident = {"success": True, "selectedOk": False, "candidates": [],
                     "confident": {"code": "sce", "name": "Saccharomyces cerevisiae", "hits": 2299,
                                   "total": 2299}}
        self.assertEqual(od.describeHint(confident),
                         "They look like Saccharomyces cerevisiae identifiers (2299 of 2299 match its "
                         "identifier index): go back to step 1 and choose that organism.")
        tie = {"success": True, "selectedOk": False, "confident": None,
               "candidates": [{"name": "Mus musculus"}, {"name": "Rattus norvegicus"}]}
        self.assertIn("Mus musculus or Rattus norvegicus identifiers", od.describeHint(tie))
        missing = {"success": True, "selectedOk": False, "confident": None, "candidates": [],
                   "uninstalled": {"code": "sce", "name": "Saccharomyces cerevisiae"}}
        self.assertIn("does not have installed", od.describeHint(missing))
        for silent in ({"success": False}, {"success": True, "selectedOk": True},
                       {"success": True, "selectedOk": False, "confident": None, "candidates": []}, None):
            self.assertIsNone(od.describeHint(silent))


class StepTwoMessageTest(unittest.TestCase):

    def omic(self, name, unmapped):
        return {"omicName": name, "omicSummary": [{"KEGG": 0, "Total": 0}, unmapped]}

    def test_the_message_appends_the_verdict_and_hands_the_detector_a_real_sample(self):
        seen = {}

        def sampler(name, limit=3):
            return YEAST[:limit]

        def detect(identifiers, organism):
            seen["identifiers"] = list(identifiers)
            seen["organism"] = organism
            return "They look like Saccharomyces cerevisiae identifiers."

        message = explainEmptyMapping("fgr", [self.omic("Gene expression", 2299)], sampler, detect=detect)
        self.assertIn("matched fgr's KEGG genes", message)
        self.assertTrue(message.endswith("They look like Saccharomyces cerevisiae identifiers."))
        self.assertEqual(seen["organism"], "fgr")
        self.assertEqual(seen["identifiers"], YEAST)      # all eight, not the three shown

    def test_a_one_argument_sampler_still_works(self):
        message = explainEmptyMapping("fgr", [self.omic("Gene expression", 5)],
                                      lambda name: YEAST[:3],
                                      detect=lambda ids, org: "verdict %d" % len(ids))
        self.assertTrue(message.endswith("verdict 3"))

    def test_a_detector_that_raises_or_abstains_leaves_the_message_alone(self):
        plain = explainEmptyMapping("fgr", [self.omic("Gene expression", 5)], lambda name: YEAST[:3])
        raising = explainEmptyMapping("fgr", [self.omic("Gene expression", 5)], lambda name: YEAST[:3],
                                      detect=lambda ids, org: 1 / 0)
        quiet = explainEmptyMapping("fgr", [self.omic("Gene expression", 5)], lambda name: YEAST[:3],
                                    detect=lambda ids, org: None)
        self.assertEqual(raising, plain)
        self.assertEqual(quiet, plain)
        self.assertIsNone(explainEmptyMapping("fgr", [], lambda name: [], detect=lambda i, o: "x"))


# Real ids, each set read on 2026-11-07. The zma database on paintomics.org holds
# 39,771 v5 genes and 145,097 transcripts and proteins, every one of these forms.
MAIZE_V5 = ["Zm00001eb000010", "Zm00001eb000020", "Zm00001eb000010_T001", "Zm00001eb000020_P004",
            "Zm00001eb000010_P001", "Zm00001eb000020_P002"]
# MaizeGDB's preliminary annotation of the same genome, Zm00001e.1: its cross
# reference table, cDNA file and protein file. zma holds none of these.
MAIZE_V5_PRELIMINARY = ["Zm00001e000001", "Zm00001e000002", "Zm00001e009486_T001", "Zm00001e035004_T001",
                        "Zm00001e009486_P001", "Zm00001e035004_P001"]
# Grape, each form from where it was read. Only PN_T2T is in the vvi database on
# paintomics.org (97,771 ids; none of any other form here).
GRAPE = {
    "PN_T2T, the vvi database": ["Vitis01g00541", "Vitis15g01003", "Vitis04g00873.t01", "Vitis02g00420.t01",
                                 "Vitis07g00050.t01.CDS"],
    "PN40024.v4, Ensembl Plants 60": ["Vitvi18g00828", "Vitvi18g00267", "Vitvi18g00828_t001", "Vitvi18g00267_t003",
                                      "Vitvi18g00828_P001"],
    "12X, Ensembl Plants 50": ["VIT_00s0125g00090", "VIT_00s0125g00170", "VIT_00s0125g00090.t01", "VIT_00s0120g00050",
                               "VIT_00s0131g00010", "VIT_16s0100g01030"],
    "PN40024.gnm2.ann1.V31M, Legume Information System": ["VIT_201s0011g00010", "VIT_201s0011g00520",
                                                          "VIT_201s0011g00970.1", "VIT_201s0011g01340.1",
                                                          "VIT_202s0033g00410"],
    "NCBI locus tags": ["VIT_00032725001", "VIT_00022656001", "VIT_00028700001", "VIT_00019848001",
                        "VIT_00000729001"],
    "UniProt, VitviT2T": ["VitviT2T_000244", "VitviT2T_000810", "VitviT2T_001161", "VitviT2T_001235",
                          "VitviT2T_001328"],
    "UniProt, Vv": ["Vv19s0014g04930", "Vv05s0051g00580", "Vv01s0010g01180", "Vv05s0020g01830", "Vv12s0028g03760"],
    "UniProt, GSVIVT": ["GSVIVT00014047001", "GSVIVT00009226001", "GSVIVT01010181001", "GSVIVT01017796001",
                        "GSVIVT01020544001"],
    "UniProt, VITISV": ["VITISV_008354", "VITISV_025327", "VITISV_035301", "VITISV_013313", "VITISV_030783"],
}
GRAPE_INDEXED = GRAPE["PN_T2T, the vvi database"]


class MaizeAndGrapeIdsTest(unittest.TestCase):

    def test_maize_v5_ids_name_maize(self):
        """Zm00001eb...: the pattern had Zm00001[de]\\d{6}, which the official v5 ids do not fit."""
        match = od.matchPattern(MAIZE_V5)
        self.assertIsNotNone(match)
        self.assertEqual(("zma",), match[0])
        self.assertEqual(1.0, match[2])

    def test_maize_preliminary_v5_ids_still_name_maize(self):
        """Zm00001e000001: replaced by Zm00001eb and kept in MaizeGDB's archive, but real and maize."""
        match = od.matchPattern(MAIZE_V5_PRELIMINARY)
        self.assertIsNotNone(match)
        self.assertEqual(("zma",), match[0])
        self.assertEqual(1.0, match[2])

    def test_a_file_mixing_maize_versions_still_names_maize(self):
        mixed = ["Zm00001d027230", "Zm00001d002345", "Zm00001e000001", "Zm00001eb000010", "Zm00001eb000020"]
        self.assertEqual(("zma",), od.matchPattern(mixed)[0])

    def test_every_grape_form_listed_names_grape(self):
        for version, identifiers in GRAPE.items():
            match = od.matchPattern(identifiers)
            self.assertIsNotNone(match, version)
            self.assertEqual(("vvi",), match[0], version)
            self.assertEqual(1.0, match[2], version)

    def test_lookalikes_are_neither(self):
        pattern = {codes: re.compile(regex) for regex, codes, _ in od._PATTERN_TABLE
                   if codes in (("zma",), ("vvi",)) and not regex.startswith("GRMZM")}
        for identifier in ("Zm00001e00001", "Zm00001e0000010", "Zm00001eb00001", "Zm00001eb0000100",
                           "zm00001eb000010", "Zm00001eb000010_X001", "Zm00001eb000010_T", "Zm00001d027230_P001"):
            self.assertIsNone(pattern[("zma",)].fullmatch(identifier), identifier)
        for identifier in ("vitis01g00541", "Vitis1g00541", "Vitis01g0054", "Vitis01g00541.CDS",
                           "VIT_00s012g00050", "VIT_0003272500", "VIT_000327250010", "vit_00s0120g00050",
                           "Vitvi18g00828.t01", "VitviT2T_00024", "VIT_201s001g00010", "Vv1s0010g01180",
                           "GSVIVT0001404700", "VITISV_00835"):
            self.assertIsNone(pattern[("vvi",)].fullmatch(identifier), identifier)

    def test_a_grape_file_run_as_the_wrong_organism_is_told_grape(self):
        for version, identifiers in GRAPE.items():
            lookup = FakeLookup({"ath": [], "vvi": GRAPE_INDEXED})
            result = od.detectOrganism(identifiers, selected="ath", lookup=lookup,
                                       names={"vvi": "Vitis vinifera (wine grape)"})
            self.assertEqual("pattern", result["method"], version)
            self.assertEqual("vvi", result["confident"]["code"], version)

    def test_maize_v5_ids_run_as_the_wrong_organism_are_told_maize(self):
        lookup = FakeLookup({"hsa": [], "zma": MAIZE_V5})
        result = od.detectOrganism(MAIZE_V5, selected="hsa", lookup=lookup, names={"zma": "Zea mays"})
        self.assertEqual("zma", result["confident"]["code"])
        # zma's index holds every one of them, so the count is the index's.
        self.assertEqual("index", result["confident"]["counted"])
        self.assertEqual("They look like Zea mays identifiers (6 of 6 match its identifier index): go back "
                         "to step 1 and choose that organism.", od.describeHint(result))


class CountSaysWhatWasCountedTest(unittest.TestCase):
    """A pattern names the organism; only the index can say the ids are in it."""

    V4_TRANSCRIPTS = ["Zm00001d%06d_T00%d" % (27230 + i, i % 3 + 1) for i in range(150)]
    NONE_IN_INDEX = ("They look like Zea mays identifiers (150 of 150 have the form of its identifiers, but none "
                     "is in its identifier index). Choosing that organism in step 1 may not be enough: PaintOmics "
                     "may hold another genome version or kind of identifier for it.")

    def hint(self, identifiers, indexed):
        lookup = FakeLookup({"hsa": [], "zma": indexed})
        return od.detectOrganism(identifiers, selected="hsa", lookup=lookup, names={"zma": "Zea mays"})

    def test_ids_the_index_does_not_hold_are_counted_as_ids_of_its_form(self):
        result = self.hint(self.V4_TRANSCRIPTS, [])
        self.assertEqual("zma", result["confident"]["code"])
        self.assertEqual("form", result["confident"]["counted"])
        self.assertEqual(0, result["confident"]["indexHits"])
        sentence = od.describeHint(result)
        self.assertNotIn("match its identifier index", sentence)
        self.assertEqual(self.NONE_IN_INDEX, sentence)

    def test_part_in_the_index_says_how_many(self):
        genes = ["Zm00001d%06d" % (30000 + i) for i in range(75)]
        sentence = od.describeHint(self.hint(self.V4_TRANSCRIPTS[:75] + genes, genes))
        self.assertIn("(150 of 150 have the form of its identifiers, 75 are in its identifier index): go back "
                      "to step 1 and choose that organism.", sentence)
        result = self.hint(self.V4_TRANSCRIPTS[:149] + genes[:1], genes[:1])
        self.assertIn("1 is in its identifier index", od.describeHint(result))

    def test_the_step_2_message_says_it_too(self):
        def sampler(name, limit=3):
            return self.V4_TRANSCRIPTS[:limit]

        def detect(identifiers, organism):
            return od.describeHint(self.hint(identifiers, []))

        message = explainEmptyMapping("hsa", [{"omicName": "Gene expression",
                                               "omicSummary": [{"KEGG": 0, "Total": 0}, 150]}],
                                      sampler, detect=detect)
        self.assertIn("have the form of its identifiers, but none is in its identifier index", message)
        self.assertNotIn("match its identifier index", message)

    def test_a_form_the_organism_does_not_hold_is_not_told_to_choose_it(self):
        """Choosing vvi cannot help a 12X file: vvi holds PN_T2T, and the next run matches nothing again."""
        for version, identifiers in GRAPE.items():
            if identifiers is GRAPE_INDEXED:
                continue
            lookup = FakeLookup({"hsa": [], "vvi": GRAPE_INDEXED})
            result = od.detectOrganism(identifiers, selected="hsa", lookup=lookup,
                                       names={"vvi": "Vitis vinifera"})
            self.assertEqual(0, result["confident"]["indexHits"], version)
            sentence = od.describeHint(result)
            self.assertNotIn("go back to step 1", sentence, version)
            self.assertIn("may not be enough", sentence, version)

    def test_an_index_that_gave_no_answer_is_not_a_count_of_zero(self):
        """maxTimeMS on a slow box raises here: the index was not read, so nothing is known about it."""

        class TimedOut(FakeLookup):
            def hits(self, code, identifiers):
                raise RuntimeError("operation exceeded time limit")

        result = od.detectOrganism(MAIZE_V5, selected="hsa", lookup=TimedOut({"hsa": [], "zma": MAIZE_V5}),
                                   names={"zma": "Zea mays"})
        self.assertEqual("zma", result["confident"]["code"])
        self.assertEqual("form", result["confident"]["counted"])
        self.assertIsNone(result["confident"]["indexHits"])
        self.assertEqual("They look like Zea mays identifiers (6 of 6 have the form of its identifiers): go back "
                         "to step 1 and choose that organism.", od.describeHint(result))

    def test_no_lookup_at_all_is_not_a_count_of_zero(self):
        result = od.detectOrganism(MAIZE_V5, selected="hsa", lookup=None, installed={"hsa", "zma"},
                                   names={"zma": "Zea mays"})
        self.assertIsNone(result["confident"]["indexHits"])
        self.assertNotIn("identifier index", od.describeHint(result))

    def test_preliminary_maize_ids_are_named_but_not_promised(self):
        lookup = FakeLookup({"hsa": [], "zma": MAIZE_V5})
        result = od.detectOrganism(MAIZE_V5_PRELIMINARY, selected="hsa", lookup=lookup, names={"zma": "Zea mays"})
        self.assertEqual("zma", result["confident"]["code"])
        self.assertIn("(6 of 6 have the form of its identifiers, but none is in its identifier index)",
                      od.describeHint(result))


if __name__ == "__main__":
    unittest.main()

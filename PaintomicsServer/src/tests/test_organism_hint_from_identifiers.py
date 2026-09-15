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

Everything runs without MongoDB: the lookup is a dict.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_organism_hint_from_identifiers
"""
import os
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


if __name__ == "__main__":
    unittest.main()

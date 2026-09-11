#!/usr/bin/env python3
"""The organism picker must find what the user means, not what they spelt.

What this guards
----------------
Step 1's organism combo (and the "Request a new organism" dialog's) used the
stock ExtJS local query, which keeps a row only when the display name STARTS
with the typed text. So "mouse" found nothing -- the row is "Mus musculus
(house mouse)" -- and a single typo emptied the list. Users type the common
name, the KEGG code, a genus, a strain, or a misspelling of any of those, and
the picker has to rank the organism they mean first for all of them.

The ranking lives in app/view/common/OrganismSearch.js, a module with no
DOM or ExtJS dependency so node can run it exactly as the browser does. The
list it is run against here is the one paintomics.uv.es served on 2026-08-21
(organisms_paintomics_uv.json): 133 real organisms, the population the picker
actually searches in production, with the collisions that matter -- two
yeasts, three rices, "licorice" hiding "rice", "Streptococcus mutans" hiding
"mus", two Nostocs and a quoted name.

The empty query is a browse, and with 3,000 organisms installed (12,000 to
come) a browse in species.json's own order is a wall. It lists the curated
model organisms first (MODEL_ORGANISMS in the module, human, mouse, rat, ...)
and everything else by name; the list renders at most `maxRows` rows and
says how many more there are. CuratedList pins what the codes in that list
mean, RenderCap what the combo shows.

Why a Python file runs a JavaScript module
------------------------------------------
The client has no test harness of its own; every client contract in this
directory is checked from here (see test_validator_agrees_with_server for the
same pattern). The module is loaded with node's require, so what is tested is
the file as shipped, not an extract.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_organism_search
"""
import csv
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_ROOT = os.path.abspath(os.path.join(HERE, "../../../PaintomicsClient/public_html"))
MODULE = os.path.join(CLIENT_ROOT, "app/view/common/OrganismSearch.js")
FIXTURE = os.path.join(HERE, "organisms_paintomics_uv.json")
ALL_SPECIES = os.path.join(CLIENT_ROOT, "resources/data/all_species.json")
STEP1_VIEWS = os.path.join(CLIENT_ROOT, "app/view/PathwayAcquisitionViews/PA_Step1Views.js")
DATA_MANAGEMENT = os.path.join(CLIENT_ROOT, "app/controller/DataManagementController.js")
INDEX_HTML = os.path.join(CLIENT_ROOT, "index.html")
MANIFEST = os.path.abspath(os.path.join(HERE, "../../../deploy/species/manifest.tsv"))
ADMIN_SCRIPTS = os.path.abspath(os.path.join(HERE, "../AdminTools/scripts"))

NODE = shutil.which("node")

with io.open(FIXTURE, encoding="utf-8") as _handle:
    FIXTURE_SIZE = len(json.load(_handle)["species"])


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def node(expression, listPath=FIXTURE):
    """Evaluates `expression` with the module as S and the organism list as L."""
    script = (
        "const S = require(%s);"
        "const L = require(%s).species;"
        "process.stdout.write(JSON.stringify(%s));"
    ) % (json.dumps(MODULE), json.dumps(listPath), expression)
    result = subprocess.run([NODE, "-e", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise AssertionError("node failed: " + result.stderr.decode("utf-8", "replace"))
    return json.loads(result.stdout.decode("utf-8"))


def codes(query):
    """KEGG codes in the order the picker would list them for `query`."""
    return node("S.rank(%s, L).map(r => r.value)" % json.dumps(query))


@unittest.skipIf(NODE is None, "node is not available")
class CommonNamesAndCodes(unittest.TestCase):
    """The ways people actually refer to an organism all have to land first."""

    def test_common_name_word_finds_house_mouse_first(self):
        # The report that started this: "mouse" found nothing.
        self.assertEqual("mmu", codes("mouse")[0])
        self.assertEqual("mmu", codes("Mouse")[0])
        self.assertEqual("mmu", codes("  MOUSE ")[0])

    def test_kegg_code_is_an_exact_hit(self):
        self.assertEqual("hsa", codes("hsa")[0])
        self.assertEqual("mmu", codes("mmu")[0])
        self.assertEqual("rno", codes("rno")[0])

    def test_genus_word_beats_a_prefix_elsewhere(self):
        # "pan" is the chimpanzee's genus and the start of the pangolin's name.
        ranked = codes("pan")
        self.assertEqual("ptr", ranked[0])
        self.assertIn("mjv", ranked)
        self.assertEqual("mmu", codes("mus")[0])

    def test_the_whole_common_name_beats_a_word_of_one(self):
        # "rat" is all of the rat's common name, and a word of no other.
        self.assertEqual("rno", codes("rat")[0])

    def test_scientific_name_prefix(self):
        self.assertEqual("ath", codes("arab")[0])
        self.assertEqual("hsa", codes("homo")[0])
        self.assertEqual("hsa", codes("sapiens")[0])

    def test_every_match_for_a_shared_common_name_is_kept(self):
        ranked = codes("rice")
        # The three rices first (two share a name), licorice after them.
        self.assertEqual({"osa", "dosa", "ogl"}, set(ranked[:3]))
        self.assertIn("aprc", ranked[3:])

    def test_two_yeasts_both_listed(self):
        ranked = codes("yeast")
        self.assertEqual({"sce", "spo"}, set(ranked[:2]))
        self.assertEqual("sce", ranked[0])


@unittest.skipIf(NODE is None, "node is not available")
class Typos(unittest.TestCase):
    """One slip in a word of four or more letters still finds the organism."""

    def test_transposed_letters(self):
        self.assertEqual("mmu", codes("mosue")[0])
        self.assertEqual("mmu", codes("muose")[0])

    def test_one_wrong_letter(self):
        self.assertEqual("hsa", codes("humna")[0])
        self.assertEqual("hsa", codes("hunan")[0])
        self.assertEqual("ath", codes("arabidpsis")[0])

    def test_a_missing_letter(self):
        self.assertEqual("dre", codes("zebrfish")[0])
        self.assertEqual("sly", codes("tomto")[0])

    def test_two_slips_in_a_long_word(self):
        self.assertEqual("ath", codes("arabadopsos")[0])
        self.assertEqual("dme", codes("drosofila")[0])

    def test_short_tokens_are_not_fuzzed(self):
        # Three letters is too short to guess at: "cat" must not become "rat".
        self.assertNotIn("rno", codes("cat"))
        self.assertEqual([], codes("qzx"))

    def test_nonsense_finds_nothing(self):
        self.assertEqual([], codes("qzxvwy"))
        self.assertEqual([], codes("xxxxxxxxxxxx"))


@unittest.skipIf(NODE is None, "node is not available")
class MultiWordQueries(unittest.TestCase):
    """Every word of the query has to match; the words may be in any order."""

    def test_initial_plus_species(self):
        self.assertEqual("eco", codes("e coli")[0])
        self.assertEqual("cel", codes("c elegans")[0])
        self.assertEqual("sce", codes("s cerevisiae")[0])

    def test_split_common_name(self):
        self.assertEqual("dre", codes("zebra fish")[0])
        self.assertEqual("dme", codes("fruit fly")[0])
        self.assertEqual("mmu", codes("house mouse")[0])

    def test_word_order_does_not_matter(self):
        self.assertEqual("mmu", codes("mouse house")[0])
        self.assertEqual("hsa", codes("sapiens homo")[0])

    def test_a_word_that_matches_nothing_empties_the_list(self):
        self.assertEqual([], codes("mouse qzxvwy"))

    def test_joined_and_hyphenated_strains(self):
        self.assertEqual("eco", codes("k12")[0])
        self.assertEqual("eco", codes("K-12")[0])
        self.assertEqual("eco", codes("ecoli")[0])

    def test_a_single_letter_is_a_genus_initial(self):
        # "x" is a whole word of "Citrus x clementina"; an initial is not a word.
        self.assertEqual(["xtr"], codes("x"))
        self.assertEqual("hsa", codes("h sapiens")[0])

    def test_quoted_names_search_by_their_words(self):
        self.assertEqual("naz", codes("azollae")[0])
        self.assertEqual({"naz", "ncf"}, set(codes("nostoc")[:2]))


@unittest.skipIf(NODE is None, "node is not available")
class EmptyQueryAndOrder(unittest.TestCase):

    def test_empty_query_lists_model_organisms_first_then_the_rest_by_name(self):
        # A browse: the curated organisms lead, in their curated order --
        # human, mouse, rat, ... -- and everything else follows by name.
        listed = node("S.rank('', L).map(r => [r.value, r.name])")
        curated = node("S.MODEL_ORGANISMS")
        present = set(value for value, _ in listed)
        lead = [code for code in curated if code in present]
        self.assertEqual(FIXTURE_SIZE, len(listed))
        self.assertEqual(["hsa", "mmu", "rno"], lead[:3])
        self.assertEqual(lead, [value for value, _ in listed[:len(lead)]])
        rest = [name for _, name in listed[len(lead):]]
        self.assertGreater(len(rest), 50)
        self.assertEqual(sorted(rest, key=lambda n: n.lower()), rest)

    def test_whitespace_only_is_empty(self):
        self.assertEqual(FIXTURE_SIZE, len(codes("   ")))

    def test_a_query_with_no_word_in_it_finds_nothing(self):
        # "(" or "-" or a script the names are not written in has nothing to
        # match; it must not fall through to the browse-everything branch
        # (11,550 rows rendered for a stray character, in the request dialog).
        for query in ["(", "-", "'", "小鼠", "мышь", " ( ) "]:
            self.assertEqual([], codes(query), repr(query))

    def test_results_carry_name_value_and_score(self):
        top = node("S.rank('mouse', L)[0]")
        self.assertEqual({"name": "Mus musculus (house mouse)", "value": "mmu"},
                         {"name": top["name"], "value": top["value"]})
        self.assertGreater(top["score"], 0)

    def test_ties_are_broken_by_name(self):
        # Both Oryza sativa rows match "japanese rice" identically.
        ranked = codes("japanese rice")
        self.assertEqual(["osa", "dosa"], ranked[:2])


@unittest.skipIf(NODE is None, "node is not available")
class Highlighting(unittest.TestCase):
    """The dropdown marks what matched, and never injects markup from a name."""

    def highlight(self, name, query):
        return node("S.highlight(%s, %s)" % (json.dumps(name), json.dumps(query)))

    def test_marks_the_matched_word(self):
        self.assertEqual("Mus musculus (house <mark>mouse</mark>)",
                         self.highlight("Mus musculus (house mouse)", "mouse"))

    def test_marks_a_prefix(self):
        self.assertEqual("Mus musculus (<mark>hous</mark>e mouse)",
                         self.highlight("Mus musculus (house mouse)", "hous"))

    def test_marks_the_whole_word_a_typo_matched(self):
        self.assertEqual("Mus musculus (house <mark>mouse</mark>)",
                         self.highlight("Mus musculus (house mouse)", "mosue"))

    def test_marks_every_query_word(self):
        self.assertEqual("<mark>Homo</mark> <mark>sapiens</mark> (human)",
                         self.highlight("Homo sapiens (human)", "sapiens homo"))

    def test_escapes_html_in_the_name(self):
        self.assertEqual("a &lt;b&gt; &amp; c", self.highlight("a <b> & c", ""))
        self.assertEqual("<mark>a</mark> &lt;b&gt; &amp; c", self.highlight("a <b> & c", "a"))

    def test_no_query_returns_the_escaped_name(self):
        self.assertEqual("Mus musculus (house mouse)",
                         self.highlight("Mus musculus (house mouse)", ""))

    def test_a_missing_name_is_empty_not_the_word_null(self):
        self.assertEqual("", node("S.highlight(null, 'x')"))
        self.assertEqual("", node("S.highlight(null, '')"))


@unittest.skipIf(NODE is None, "node is not available")
class RequestDialogScale(unittest.TestCase):
    """The request dialog searches every KEGG organism (11,550), per keystroke."""

    def test_ranks_eleven_thousand_organisms_in_well_under_a_keystroke(self):
        millis = node(
            "(() => { S.rank('mo', L); const t = process.hrtime.bigint();"
            " for (const q of ['m', 'mo', 'mou', 'mous', 'mouse']) S.rank(q, L);"
            " return Number(process.hrtime.bigint() - t) / 5e6; })()",
            listPath=ALL_SPECIES)
        self.assertLess(millis, 150, "%.1f ms per query" % millis)

    def test_an_english_word_beats_a_code_that_spells_it(self):
        # 253 KEGG codes spell a word of another organism's name: "fly" is a
        # Flavobacterium, "dog" a Desulfobulbus. The animal is what was meant.
        full = lambda q: node("S.rank(%s, L).map(r => r.value)" % json.dumps(q), listPath=ALL_SPECIES)
        self.assertEqual("dme", full("fly")[0])
        self.assertEqual("cfa", full("dog")[0])
        self.assertEqual("bta", full("cow")[0])
        # ...while a code nothing else spells is still an exact hit.
        self.assertEqual("hsa", full("hsa")[0])

    def test_initial_plus_species_over_the_full_list_finds_the_model_strain(self):
        # 286 E. coli strains; K-12 MG1655 is the one the initial means.
        full = lambda q: node("S.rank(%s, L).map(r => r.value)" % json.dumps(q), listPath=ALL_SPECIES)
        self.assertEqual("eco", full("e coli")[0])

    def test_fuzzy_query_over_the_full_list_finds_the_mouse(self):
        ranked = node("S.rank('mosue', L).map(r => r.value)", listPath=ALL_SPECIES)
        self.assertEqual("mmu", ranked[0])

    def test_a_code_that_spells_a_genus_does_not_outrank_the_genus(self):
        # Six KEGG codes spell the genus of another organism: "mus" is Musa
        # acuminata (a banana), "sus" a Solibacter, "bos" a Bosea, "pan" a
        # Podospora. A code and a genus score level, so the curated order
        # decides -- and the code-holder is still on the first screen.
        full = lambda q: node("S.rank(%s, L).map(r => r.value)" % json.dumps(q), listPath=ALL_SPECIES)
        self.assertEqual("mmu", full("mus")[0])
        self.assertIn("mus", full("mus")[:4])
        self.assertEqual("ssc", full("sus")[0])
        self.assertEqual("bta", full("bos")[0])
        self.assertEqual("ptr", full("pan")[0])

    def test_ties_between_equal_matches_follow_the_curated_order(self):
        full = lambda q: node("S.rank(%s, L).map(r => r.value)" % json.dumps(q), listPath=ALL_SPECIES)
        self.assertEqual(["sce", "spo"], full("yeast")[:2])
        self.assertEqual("eco", full("coli")[0])
        self.assertEqual("mmu", full("mouse")[0])
        self.assertEqual(["osa", "dosa"], full("rice")[:2])

    def test_the_full_browse_leads_with_the_curated_organisms(self):
        listed = node("S.rank('', L).map(r => r.value)", listPath=ALL_SPECIES)
        curated = node("S.MODEL_ORGANISMS")
        present = set(listed)
        lead = [code for code in curated if code in present]
        self.assertGreater(len(lead), 50)
        self.assertEqual(lead, listed[:len(lead)])
        self.assertEqual(len(present), len(listed))

    def test_a_browse_of_eleven_thousand_organisms_is_quick(self):
        # The browse re-sorts every organism (rank() over the empty query);
        # it runs on every trigger click after the field was cleared.
        millis = node(
            "(() => { S.rank('', L); const t = process.hrtime.bigint();"
            " S.rank('', L); return Number(process.hrtime.bigint() - t) / 1e6; })()",
            listPath=ALL_SPECIES)
        self.assertLess(millis, 100, "%.1f ms per browse" % millis)


# The genus each curated code must name. A code is three or four letters
# that mean nothing to a reader; this table is what they mean, and
# CuratedList holds the module's list to it and to KEGG's own organism list.
CURATED_GENUS = {
    "hsa": "Homo", "mmu": "Mus", "rno": "Rattus", "dre": "Danio", "dme": "Drosophila",
    "cel": "Caenorhabditis", "sce": "Saccharomyces", "spo": "Schizosaccharomyces",
    "ath": "Arabidopsis", "eco": "Escherichia", "bsu": "Bacillus",
    "xtr": "Xenopus", "xla": "Xenopus", "gga": "Gallus", "bta": "Bos", "ssc": "Sus",
    "cfa": "Canis", "ptr": "Pan", "mcc": "Macaca", "cge": "Cricetulus", "ola": "Oryzias",
    "acs": "Anolis",
    "aga": "Anopheles", "ame": "Apis", "bmor": "Bombyx", "tca": "Tribolium", "dpx": "Daphnia",
    "cin": "Ciona", "spu": "Strongylocentrotus", "nve": "Nematostella",
    "cal": "Candida", "ncr": "Neurospora", "ani": "Aspergillus",
    "osa": "Oryza", "dosa": "Oryza", "zma": "Zea", "taes": "Triticum", "sbi": "Sorghum",
    "bdi": "Brachypodium", "gmx": "Glycine", "mtr": "Medicago", "sly": "Solanum",
    "sot": "Solanum", "nta": "Nicotiana", "vvi": "Vitis", "pop": "Populus",
    "ppp": "Physcomitrium", "cre": "Chlamydomonas",
    "ddi": "Dictyostelium", "pfa": "Plasmodium", "tgo": "Toxoplasma", "tbr": "Trypanosoma",
    "lma": "Leishmania", "tet": "Tetrahymena",
    "mtu": "Mycobacterium", "pae": "Pseudomonas", "stm": "Salmonella", "hpy": "Helicobacter",
    "syn": "Synechocystis", "ccr": "Caulobacter", "atu": "Agrobacterium", "sco": "Streptomyces",
}

# What the picker was asked to lead with, in this order for the first three.
REQUIRED_FIRST = "hsa mmu rno dre dme cel sce spo ath xtr gga bta ssc cfa eco bsu osa zma pfa ddi mtu".split()


@unittest.skipIf(NODE is None, "node is not available")
class CuratedList(unittest.TestCase):
    """MODEL_ORGANISMS names what it says it names.

    The list once carried "tae" for wheat, which KEGG gives to
    Tepidanaerobacter acetatoxydans (wheat is taes), and nothing could have
    told: a code is opaque. So every code is checked against
    deploy/species/manifest.tsv, KEGG's full organism list, and against the
    genus CURATED_GENUS says it must name.
    """

    @classmethod
    def setUpClass(cls):
        cls.curated = node("S.MODEL_ORGANISMS")
        with io.open(MANIFEST, encoding="utf-8") as handle:
            cls.manifest = {row["code"]: row["name"] for row in csv.DictReader(handle, delimiter="\t")}

    def test_the_list_and_the_table_name_the_same_organisms(self):
        self.assertEqual(len(set(self.curated)), len(self.curated))
        self.assertEqual(sorted(CURATED_GENUS), sorted(self.curated))

    def test_every_code_is_a_kegg_organism_of_the_genus_it_is_meant_to_be(self):
        for code in self.curated:
            self.assertIn(code, self.manifest, "%s is not a KEGG organism code" % code)
            self.assertTrue(self.manifest[code].startswith(CURATED_GENUS[code] + " "),
                            "%s is %s, not a %s" % (code, self.manifest[code], CURATED_GENUS[code]))

    def test_the_organisms_the_picker_must_lead_with_are_listed(self):
        for code in REQUIRED_FIRST:
            self.assertIn(code, self.curated)
        self.assertEqual(["hsa", "mmu", "rno"], self.curated[:3])

    def test_every_organism_this_repository_ships_resources_for_is_listed(self):
        # AdminTools/scripts/<code>_resources is an organism the project
        # curated by hand. bvu is the one exception: its resources are Beta
        # vulgaris under a GoMapMan code, while KEGG's bvu is Phocaeicola
        # vulgatus, a gut bacterium.
        shipped = sorted(os.path.basename(path)[:-len("_resources")]
                         for path in glob.glob(os.path.join(ADMIN_SCRIPTS, "*_resources")))
        self.assertIn("hsa", shipped)
        for code in shipped:
            if code in ("common", "bvu"):
                continue
            self.assertIn(code, self.curated, "%s has resources but is not a curated organism" % code)


@unittest.skipIf(NODE is None, "node is not available")
class RenderCap(unittest.TestCase):
    """The list shows at most maxRows rows, whatever the store holds.

    ExtJS's BoundList renders one <li> per row of the store, so the cap is a
    store filter: the first maxRows of rank()'s order stay, the rest are
    filtered out, and a footer row says how many there were.
    """

    def run_js(self, expression):
        return node("(() => {" + STUB_EXT + "return " + expression + ";})()")

    def test_a_browse_keeps_the_first_rows_of_the_curated_order(self):
        out = self.run_js("(combo.maxRows = 5, query(''), {rows: rows(), count: store.getCount(), "
                          "shown: combo.organismShown})")
        self.assertEqual(["hsa", "mmu", "rno", "dre", "dme"], out["rows"])
        self.assertEqual(5, out["count"])
        self.assertEqual({"query": "", "shown": 5, "matched": FIXTURE_SIZE, "total": FIXTURE_SIZE,
                          "model": 5, "groups": False}, out["shown"])

    def test_a_query_keeps_the_best_rows(self):
        out = self.run_js("(combo.maxRows = 2, query('mo'), {rows: rows(), shown: combo.organismShown})")
        self.assertEqual(2, len(out["rows"]))
        self.assertEqual("mmu", out["rows"][0])
        self.assertGreater(out["shown"]["matched"], 2)
        self.assertEqual("mo", out["shown"]["query"])

    def test_zero_is_no_cap(self):
        self.assertEqual(FIXTURE_SIZE, self.run_js("(combo.maxRows = 0, query(''), store.getCount())"))

    def test_the_default_cap_is_two_hundred(self):
        self.assertEqual(200, self.run_js("combo.maxRows"))

    def test_the_footer_says_how_many_rows_the_cap_cut(self):
        html = self.run_js("(combo.maxRows = 5, query(''), body.statics.renderFooter('c1'))")
        self.assertEqual('<li class="po-organism-more">Showing 5 of %d organisms. Type to search the rest.</li>'
                         % FIXTURE_SIZE, html)
        html = self.run_js("(combo.maxRows = 2, query('mo'), body.statics.renderFooter('c1'))")
        self.assertRegex(html, r'^<li class="po-organism-more">Showing the best 2 of \d+ matches\. '
                               r'Keep typing to narrow the list\.</li>$')

    def test_no_footer_when_every_row_fits(self):
        self.assertEqual("", self.run_js("(query(''), body.statics.renderFooter('c1'))"))
        self.assertEqual("", self.run_js("(query('mouse'), body.statics.renderFooter('c1'))"))

    def test_counts_carry_thousands_separators(self):
        self.assertEqual(["11,951", "200", "1,234,567", "0"],
                         node("[11951, 200, 1234567, 0].map(S.formatCount)"))

    def test_group_labels_sit_either_side_of_the_curated_block_in_a_browse(self):
        out = self.run_js(
            "(query(''), {model: combo.organismShown.model, groups: combo.organismShown.groups, "
            "first: body.statics.renderRow({name: 'Homo sapiens (human)', value: 'hsa'}, 1, 'c1'), "
            "second: body.statics.renderRow({name: 'Mus musculus (house mouse)', value: 'mmu'}, 2, 'c1'), "
            "seam: body.statics.renderRow({name: 'Abies', value: 'abi'}, combo.organismShown.model + 1, 'c1')})")
        self.assertTrue(out["groups"])
        self.assertGreater(out["model"], 10)
        self.assertTrue(out["first"].startswith(
            '<li class="po-organism-group">Model organisms</li>'
            '<li role="option" unselectable="on" class="x-boundlist-item">'), out["first"])
        self.assertTrue(out["second"].startswith('<li role="option"'), out["second"])
        self.assertTrue(out["seam"].startswith('<li class="po-organism-group">All organisms, A to Z</li>'), out["seam"])

    def test_no_group_labels_while_a_query_ranks(self):
        html = self.run_js("(query('mouse'), body.statics.renderRow({name: 'Mus musculus (house mouse)', value: 'mmu'}, 1, 'c1'))")
        self.assertTrue(html.startswith('<li role="option"'), html)
        self.assertIn("house <mark>mouse</mark>", html)

    def test_no_group_labels_when_every_shown_row_is_curated(self):
        self.assertFalse(self.run_js("(combo.maxRows = 3, query(''), combo.organismShown.groups)"))

    def test_a_browse_before_the_list_arrives_is_not_cached(self):
        # autoLoad is in flight, the user clicks the trigger: nothing to
        # show, and doQuery must not remember '' as answered, or the first
        # click after the load expands an empty list.
        out = self.run_js("(store.data = {items: []}, combo.lastQuery = '', "
                          "combo.doLocalQuery({query: '', forceAll: true}), "
                          "{forgotten: combo.lastQuery === undefined, events: combo.events})")
        self.assertTrue(out["forgotten"])
        self.assertEqual(["collapse", "afterQuery"], out["events"])

    def test_the_list_template_renders_rows_and_the_footer(self):
        tpl = self.run_js("combo.listConfig.tpl")
        self.assertIn('<tpl for=".">{[Paintomics.form.OrganismCombo.renderRow(values, xindex, "c1")]}</tpl>', tpl)
        self.assertIn('{[Paintomics.form.OrganismCombo.renderFooter("c1")]}</ul>', tpl)


# A stand-in for the slice of ExtJS 4.2.1 the combo touches, just enough to run
# doLocalQuery / renderItem / findRecord under node. The store mimics what was
# checked in ext-all-debug.js: filter() re-filters from `snapshot` skipping
# disabled filters, then re-sorts `data` with the current sorters.
STUB_EXT = r"""
const organisms = L.map(d => ({data: d, get: f => d[f]}));
function collection(items) {
  return {items: items.slice(), getRange() { return this.items.slice(); }, clear() { this.items = []; },
          add(x) { this.items.push(x); }, addAll(xs) { this.items = this.items.concat(xs); }, get length() { return this.items.length; }};
}
const store = {
  snapshot: null, data: {items: organisms.slice()}, filters: [],
  sorters: collection([{property: 'name', sorterFn: (a, b) => a.data.name < b.data.name ? -1 : 1}]),
  addFilter(f) { this.filters.push(f); },
  filter() {
    this.snapshot = this.snapshot || {items: organisms.slice()};
    const live = this.filters.filter(f => !f.disabled);
    this.data = {items: this.snapshot.items.filter(r => live.every(f => f.filterFn(r)))};
    const sorter = this.sorters.items[0];
    if (sorter) this.data.items.sort(sorter.sorterFn);
  },
  getCount() { return this.data.items.length; }
};
let body = null;
const Ext = {
  ClassManager: {get: () => undefined},
  define: (name, b) => { body = b; },
  apply: Object.assign,
  Array: {map: (xs, fn) => xs.map(fn)},
  util: {Filter: function (c) { Object.assign(this, c); this.disabled = false; },
         Sorter: function (c) { Object.assign(this, c); }},
  form: {field: {ComboBox: {prototype: {initComponent() { this.parentInit = true; }, findRecord() { return 'stock'; }}}}},
  getCmp: id => combo
};
S.defineCombo(Ext);
const combo = Object.assign(Object.create(body), {
  id: 'c1', store, lastQuery: undefined, events: [],
  expand() { this.events.push('expand'); }, collapse() { this.events.push('collapse'); },
  afterQuery() { this.events.push('afterQuery'); }
});
combo.initComponent();
function query(q) { combo.lastQuery = q; combo.events = []; combo.doLocalQuery({query: q, forceAll: q === ''}); }
const rows = () => store.data.items.map(r => r.data.value);
"""


@unittest.skipIf(NODE is None, "node is not available")
class ComboIntegration(unittest.TestCase):
    """What xtype organismcombo does to its store, per query, under a stub Ext."""

    def run_js(self, expression):
        return node("(() => {" + STUB_EXT + "return " + expression + ";})()")

    def test_a_query_keeps_and_orders_the_ranked_rows(self):
        out = self.run_js("(query('mouse'), {rows: rows(), events: combo.events, "
                          "filterOn: !combo.queryFilter.disabled})")
        self.assertEqual("mmu", out["rows"][0])
        self.assertTrue(out["filterOn"])
        self.assertEqual(["expand", "afterQuery"], out["events"])

    def test_a_typo_is_ranked_the_same_way_through_the_combo(self):
        self.assertEqual("hsa", self.run_js("(query('humna'), rows())")[0])

    def test_the_empty_query_lists_everything_model_organisms_first(self):
        # The store's own name sorter is gone: the combo's order is rank()'s
        # for every query, and a browse is the curated organisms then the name.
        out = self.run_js("(query('mouse'), query(''), {count: store.getCount(), "
                          "sorters: store.sorters.items.length, first: rows().slice(0, 3), "
                          "shown: combo.organismShown})")
        self.assertEqual(FIXTURE_SIZE, out["count"])
        self.assertEqual(1, out["sorters"])
        self.assertEqual(["hsa", "mmu", "rno"], out["first"])
        self.assertEqual(FIXTURE_SIZE, out["shown"]["matched"])
        self.assertEqual(FIXTURE_SIZE, out["shown"]["shown"])

    def test_a_query_with_no_word_collapses_an_empty_list(self):
        out = self.run_js("(query('('), {count: store.getCount(), events: combo.events})")
        self.assertEqual(0, out["count"])
        self.assertEqual(["collapse", "afterQuery"], out["events"])

    def test_narrowing_then_widening_brings_rows_back(self):
        out = self.run_js("(query('mouse'), query('mo'), rows().length)")
        self.assertGreater(out, 1)

    def test_find_record_looks_past_the_active_filter(self):
        # Example mode calls setValue('mmu') whatever the user typed before; the
        # stock lookup searches only the filtered rows and fails to find it.
        out = self.run_js("(query('human'), {found: combo.findRecord('value', 'mmu').data.value, "
                          "missing: combo.findRecord('value', 'nope')})")
        self.assertEqual("mmu", out["found"])
        self.assertFalse(out["missing"])

    def test_render_item_marks_the_hit_and_shows_the_code(self):
        html = self.run_js("(query('mouse'), body.statics.renderItem({name: 'Mus musculus (house mouse)', value: 'mmu'}, 'c1'))")
        self.assertIn("house <mark>mouse</mark>)", html)
        self.assertIn('<span class="po-organism-code">mmu</span>', html)

    def test_render_item_escapes_the_name_and_omits_a_code_equal_to_it(self):
        html = self.run_js("(query('a'), body.statics.renderItem({name: 'a <b> & c', value: 'a <b> & c'}, 'c1'))")
        self.assertIn("&lt;b&gt; &amp; c", html)
        self.assertNotIn("<b>", html)
        self.assertNotIn("po-organism-code", html)

    def test_the_superclass_init_still_runs(self):
        self.assertTrue(self.run_js("combo.parentInit"))


class Wiring(unittest.TestCase):
    """Both organism combos use the ranked picker, and the page loads it."""

    def combo(self, source):
        start = source.index('itemId: "speciesCombobox"')
        return source[source.rindex("xtype:", 0, start):start]

    def test_step1_combo_is_the_organism_picker(self):
        self.assertIn("xtype: 'organismcombo'", self.combo(read(STEP1_VIEWS)))

    def test_request_dialog_combo_is_the_organism_picker(self):
        self.assertIn("xtype: 'organismcombo'", self.combo(read(DATA_MANAGEMENT)))

    def test_index_html_loads_the_module_before_the_app(self):
        html = read(INDEX_HTML)
        module = re.search(r'src="app/view/common/OrganismSearch\.js\?v=[0-9.]+"', html)
        self.assertIsNotNone(module, "OrganismSearch.js is not loaded with a ?v= marker")
        self.assertLess(module.start(), html.index('src="app.js'))

    def test_the_combo_never_uses_callparent(self):
        # The module is strict so node runs it as the browser does, and ExtJS
        # 4's callParent reads Function.caller, which strict mode removes. The
        # failure is "Cannot read properties of null (reading 'apply')" in
        # initComponent, the Step 1 view never builds, and the app shows its
        # generic boot-failure dialog. Superclass calls go through the
        # prototype instead.
        source = read(MODULE)
        self.assertEqual(-1, source.find("callParent"), "callParent in OrganismSearch.js")
        self.assertIn('"use strict"', source)

    def test_module_is_loadable_without_extjs(self):
        # node has no Ext; the ranking half must not need it.
        self.assertIsNotNone(NODE)
        result = subprocess.run([NODE, "-e", "require(%s)" % json.dumps(MODULE)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

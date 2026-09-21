#!/usr/bin/env python3
"""The ``--tf-targets`` step of omnipathInstaller: CollecTRI for the organism,
human CollecTRI carried across by symbol, and TRRUST, merged into
``current/<code>/mapping/tf_targets.tsv``. Offline: the two HTTP helpers are
replaced with canned bodies and any other URL is an error."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.AdminTools import omnipathInstaller as installer      # noqa: E402

COLLECTRI_HEADER = ("source\ttarget\tsource_genesymbol\ttarget_genesymbol\tis_directed\t"
                    "is_stimulation\tis_inhibition\tconsensus_direction\tconsensus_stimulation\t"
                    "consensus_inhibition\tsources\treferences")

# Human CollecTRI: an activation, a second TF, a duplicate pair with other
# references (must collapse), and a TF the mouse table does not know (must drop).
HUMAN_BODY = "\n".join([
    COLLECTRI_HEADER,
    "Q13422\tP15814\tIKZF1\tIGLL1\tTrue\tTrue\tFalse\tTrue\tTrue\tFalse\tCollecTRI\tCollecTRI:11111111",
    "P01106\tO14746\tMYC\tTERT\tTrue\tTrue\tFalse\tTrue\tTrue\tFalse\tCollecTRI;TRRUST\tCollecTRI:12695333",
    "Q13422\tP15814\tIKZF1\tIGLL1\tTrue\tTrue\tFalse\tTrue\tTrue\tFalse\tExTRI\tCollecTRI:22222222",
    "Q9BZS1\tP60568\tFOXP3\tIL2\tTrue\tFalse\tTrue\tTrue\tFalse\tTrue\tCollecTRI\tCollecTRI:33333333",
]) + "\n"

# Mouse CollecTRI: a repression, the same pair twice, a self-loop, a row with
# no target symbol, and a row already covered by the human table.
MOUSE_BODY = "\n".join([
    COLLECTRI_HEADER,
    "P01108\tO70372\tMyc\tTert\tTrue\tFalse\tTrue\tTrue\tFalse\tTrue\tCollecTRI\tCollecTRI:44444444",
    "P01108\tO70372\tMyc\tTert\tTrue\tFalse\tTrue\tTrue\tFalse\tTrue\tExTRI\tCollecTRI:55555555",
    "P01108\tP01108\tMyc\tMyc\tTrue\tTrue\tFalse\tTrue\tTrue\tFalse\tCollecTRI\tCollecTRI:66666666",
    "P01108\tQ00000\tMyc\t\tTrue\tTrue\tFalse\tTrue\tTrue\tFalse\tCollecTRI\tCollecTRI:77777777",
    "Q03267\tP20764\tIkzf1\tIgll1\tTrue\tTrue\tFalse\tTrue\tTrue\tFalse\tCollecTRI\tCollecTRI:88888888",
]) + "\n"

# TRRUST mouse: an activation, the same pair again (must collapse onto the
# first), an alias-cased repression, an unknown mode, and a symbol the table
# does not know.
TRRUST_BODY = "\n".join([
    "Ikzf1\tMyc\tActivation\t20566697",
    "Ikzf1\tMyc\tUnknown\t55555555",
    "Ikaros\tTRT\tRepression\t12345678;23456789",
    "Tert\tIgll1\tUnknown\t99999999",
    "Nosuchtf\tMyc\tActivation\t11111111",
]) + "\n"

SYMBOL_LIST = "\n".join([
    "mmu:22778\tCDS\t11:11634970..11722930\tIkzf1, 5832432G11Rik, Ikaros, LyF-1, Zfpn1a1; DNA-binding protein Ikaros",
    "mmu:16136\tCDS\t16:complement(16678535..16681849)\tIgll1, Igl-5, Igll, Lambda5; immunoglobulin lambda-like 1",
    "mmu:17869\tCDS\t15:61857190..61862210\tMyc, Myc2, Niard, Nird, bHLHe39; myc proto-oncogene protein",
    "mmu:21752\tCDS\t13:73775030..73797962\tTert, EST2, TCS1, TP2, TR, TRT; telomerase reverse transcriptase",
    # a description-only row: names no symbol and must be skipped
    "mmu:100041708\tCDS\t1:199..21836\tnuclear body protein SP140-like protein isoform X2",
    # an alias already claimed above: first gene keeps it
    "mmu:99999\tCDS\t1:1..2\tOther, Ikaros; a pretender",
]) + "\n"


def fake_fetch(path, params):
    """Stand-in for omnipathInstaller._fetch keyed on the organism asked for."""
    assert path == "interactions", path
    assert params["datasets"] == "collectri", params
    return {"9606": HUMAN_BODY, "10090": MOUSE_BODY}[params["organisms"]]


def fake_get(url, timeout=None):
    """Stand-in for omnipathInstaller._get; only the TRRUST mouse URL is known."""
    if url == installer.TRRUST_URLS["mmu"]:
        return TRRUST_BODY
    raise AssertionError("unexpected network call to %s" % url)


class TfTargetsInstallerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = tempfile.mkdtemp(prefix="tf_targets_")
        mapping = os.path.join(cls.data_dir, "current", "mmu", "mapping")
        os.makedirs(mapping)
        with open(os.path.join(mapping, "kegg2genesymbol.list"), "w") as handle:
            handle.write(SYMBOL_LIST)
        cls.target = os.path.join(mapping, installer.TF_TARGET_FILENAME)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def setUp(self):
        if os.path.exists(self.target):
            os.remove(self.target)

    def install(self):
        with mock.patch.object(installer, "_fetch", fake_fetch), \
                mock.patch.object(installer, "_get", fake_get):
            return installer.install_tf_targets("mmu", self.data_dir)

    def read_rows(self):
        with open(self.target) as handle:
            lines = handle.read().splitlines()
        return lines[0], [line.split("\t") for line in lines[1:]]

    # -- pieces ------------------------------------------------------------

    def test_read_symbol_table_maps_every_alias_to_the_first_symbol(self):
        table = installer.read_symbol_table("mmu", self.data_dir)
        self.assertEqual(table["IKAROS"], "Ikzf1")
        self.assertEqual(table["IKZF1"], "Ikzf1")
        self.assertEqual(table["LYF-1"], "Ikzf1")
        self.assertEqual(table["MYC2"], "Myc")
        self.assertEqual(table["OTHER"], "Other")
        self.assertNotIn("NUCLEAR BODY PROTEIN SP140-LIKE PROTEIN ISOFORM X2", table)

    def test_read_symbol_table_is_empty_when_the_file_is_missing(self):
        empty = tempfile.mkdtemp(prefix="tf_targets_empty_")
        try:
            os.makedirs(os.path.join(empty, "current", "mmu"))
            self.assertEqual(installer.read_symbol_table("mmu", empty), {})
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_fetch_tf_targets_signs_are_ints_and_bad_rows_are_dropped(self):
        with mock.patch.object(installer, "_fetch", fake_fetch):
            rows = installer.fetch_tf_targets(10090)
        self.assertEqual([(r["tf"], r["target"], r["sign"], r["source"]) for r in rows], [
            ("Myc", "Tert", -1, "CollecTRI"),
            ("Myc", "Tert", -1, "CollecTRI"),
            ("Ikzf1", "Igll1", 1, "CollecTRI"),
        ])
        for row in rows:
            self.assertIsInstance(row["sign"], int)
        self.assertEqual(rows[0]["references"], "CollecTRI:44444444")

    def test_map_human_by_symbol_rewrites_and_drops_unknown_symbols(self):
        table = installer.read_symbol_table("mmu", self.data_dir)
        with mock.patch.object(installer, "_fetch", fake_fetch):
            human = installer.fetch_tf_targets(9606)
        mapped = installer.map_human_by_symbol(human, table)
        self.assertEqual([(r["tf"], r["target"], r["sign"], r["source"]) for r in mapped], [
            ("Ikzf1", "Igll1", 1, "CollecTRI-human"),
            ("Myc", "Tert", 1, "CollecTRI-human"),
            ("Ikzf1", "Igll1", 1, "CollecTRI-human"),
        ])
        self.assertFalse(any(r["tf"] == "FOXP3" or r["tf"] == "Foxp3" for r in mapped))

    def test_fetch_trrust_parses_the_headerless_table(self):
        with mock.patch.object(installer, "_get", fake_get):
            rows = installer.fetch_trrust("mmu")
        self.assertEqual(rows[0], {"tf": "Ikzf1", "target": "Myc", "sign": 1,
                                   "source": "TRRUST", "references": "20566697"})
        self.assertEqual(rows[1]["sign"], 0)
        self.assertEqual(rows[2]["sign"], -1)
        self.assertEqual(rows[2]["references"], "12345678;23456789")
        self.assertEqual((rows[2]["tf"], rows[2]["target"]), ("Ikaros", "TRT"))   # as printed
        self.assertEqual(installer.fetch_trrust("rno"), [])       # no table, no request

    def test_fetch_trrust_refuses_an_html_error_page(self):
        with mock.patch.object(installer, "_get", lambda url, timeout=None: "<html>down</html>"):
            with self.assertRaises(RuntimeError):
                installer.fetch_trrust("hsa")

    # -- the merged file -----------------------------------------------------

    def test_install_writes_the_merged_file(self):
        path = self.install()
        self.assertEqual(path, self.target)
        self.assertTrue(os.path.isfile(path))
        header, rows = self.read_rows()
        self.assertEqual(header, "tf\ttarget\tsign\tsource\treferences")

        lines = ["\t".join(r) for r in rows]
        self.assertTrue(any(line.startswith("Ikzf1\tIgll1\t1\tCollecTRI-human\t") for line in lines), lines)
        self.assertIn("Ikzf1\tMyc\t1\tTRRUST\t20566697", lines)
        # the alias-cased TRRUST row was settled onto the KEGG symbols
        self.assertIn("Ikzf1\tTert\t-1\tTRRUST\t12345678;23456789", lines)
        # organism and human rows for the same pair stay apart, by source
        self.assertIn("Ikzf1\tIgll1\t1\tCollecTRI\tCollecTRI:88888888", lines)
        # the unknown TRRUST TF and the unknown human TF never reached the file
        self.assertFalse(any(r[0] in ("Nosuchtf", "FOXP3", "Foxp3") for r in rows), rows)

        for row in rows:
            self.assertEqual(len(row), 5, row)
            self.assertIn(int(row[2]), (-1, 0, 1), row)          # sign is an int

        keys = [(r[0], r[1], r[3]) for r in rows]
        self.assertEqual(len(keys), len(set(keys)), "duplicates on (tf, target, source) survived")
        self.assertEqual(keys, sorted(keys))
        # first of the duplicate pair kept: mouse Myc->Tert cites 44444444, not 55555555
        myc_tert = [r for r in rows if r[:2] == ["Myc", "Tert"] and r[3] == "CollecTRI"]
        self.assertEqual(myc_tert[0][4], "CollecTRI:44444444")
        # TRRUST's two Ikzf1->Myc rows collapsed onto the first (the activation)
        self.assertEqual([r for r in rows if r[:2] == ["Ikzf1", "Myc"]],
                         [["Ikzf1", "Myc", "1", "TRRUST", "20566697"]])

    def test_install_leaves_no_temporary_file_behind(self):
        self.install()
        leftovers = [name for name in os.listdir(os.path.dirname(self.target)) if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_one_failing_source_is_skipped_not_fatal(self):
        def failing_get(url, timeout=None):
            raise RuntimeError("grnpedia is down")
        with mock.patch.object(installer, "_fetch", fake_fetch), \
                mock.patch.object(installer, "_get", failing_get):
            installer.install_tf_targets("mmu", self.data_dir)
        _header, rows = self.read_rows()
        sources = {r[3] for r in rows}
        self.assertEqual(sources, {"CollecTRI", "CollecTRI-human"})

    def test_every_source_failing_raises(self):
        def failing(*_args, **_kwargs):
            raise RuntimeError("everything is down")
        with mock.patch.object(installer, "_fetch", failing), \
                mock.patch.object(installer, "_get", failing):
            with self.assertRaises(RuntimeError):
                installer.install_tf_targets("mmu", self.data_dir)
        self.assertFalse(os.path.exists(self.target))

    def test_an_organism_no_source_covers_raises(self):
        os.makedirs(os.path.join(self.data_dir, "current", "ath"), exist_ok=True)
        with mock.patch.object(installer, "_fetch", fake_fetch), \
                mock.patch.object(installer, "_get", fake_get):
            with self.assertRaises(RuntimeError):
                installer.install_tf_targets("ath", self.data_dir)

    def test_cli_flag_writes_only_the_tf_file(self):
        with mock.patch.object(installer, "_fetch", fake_fetch), \
                mock.patch.object(installer, "_get", fake_get), \
                mock.patch.object(installer, "install", side_effect=AssertionError("install() ran")):
            code = installer.main(["--organism", "mmu", "--tf-targets", "--kegg-data-dir", self.data_dir])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(self.target))


if __name__ == "__main__":
    unittest.main()

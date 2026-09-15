#!/usr/bin/env python3
"""The knockout simulator on the synthetic organism: sign propagation, the
written files, and the manifest entry of the shipped Pten dataset. Offline."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import network as net_mod     # noqa: E402
from src.classes.AIInterpret.walker import simulate_ko as sim      # noqa: E402
from src.tests import walker_fixture as fx                        # noqa: E402

EXAMPLE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examplefiles"))
MANIFEST = os.path.join(EXAMPLE_DIR, "datasets", "manifest.json")
SCENARIO_ID = "simulated-pten-knockout"


def ensembl_id(entrez):
    return "ENSTST%011d" % int(entrez)


class WalkerSimulateKoTest(unittest.TestCase):
    """Fixture: A -(activation)-> B -(inhibition)-> C ; A -(expression)-> D -(binding)-> E ;
    C -(phosphorylation)-> E ; A -(activation)-> F -(binding)-> G."""

    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()
        cls.mapping = os.path.join(cls.data_dir, "current", "tst", "mapping", "ensembl_mapping.list")
        with open(cls.mapping, "w") as handle:
            # Two transcripts for gene 1 (first Ensembl id wins), a row with a
            # blank Entrez column and a short row, both skipped.
            handle.write("%s\t1\tENSTSTP1\tENSTSTT1a\n" % ensembl_id(1))
            handle.write("ENSTST99999999999\t1\t\tENSTSTT1b\n")
            handle.write("ENSTST00000000042\t\t\tENSTSTT42\n")
            handle.write("ENSTST00000000043\n")
            for entrez in range(2, 8):
                handle.write("%s\t%d\t\tENSTSTT%d\n" % (ensembl_id(entrez), entrez, entrez))
        cls.net = net_mod.build_network("tst", cls.data_dir)
        cls.ensembl = sim.read_ensembl_map(cls.mapping)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    # ---- inputs -----------------------------------------------------------
    def test_the_ensembl_map_keeps_the_first_gene_id_and_skips_bad_rows(self):
        self.assertEqual(self.ensembl["1"], ensembl_id(1))
        self.assertEqual(len(self.ensembl), 7)
        self.assertNotIn("", self.ensembl)

    def test_a_symbol_resolves_case_insensitively(self):
        self.assertEqual(sim.resolve_gene(self.net, "aaa"), "g:1")
        self.assertEqual(sim.resolve_gene(self.net, "Bbb"), "g:2")
        with self.assertRaises(ValueError):
            sim.resolve_gene(self.net, "Zzz")

    # ---- propagation ------------------------------------------------------
    def test_propagate_follows_signs_for_two_steps(self):
        effects = sim.propagate(self.net, "g:1", effect=-2.5)
        self.assertAlmostEqual(effects["g:1"], -2.5)
        self.assertAlmostEqual(effects["g:2"], -1.5)      # A -> B activation, one step
        self.assertAlmostEqual(effects["g:3"], 0.9)       # B -| C inhibition flips, two steps
        self.assertAlmostEqual(effects["g:4"], -1.5)      # A -> D expression
        self.assertAlmostEqual(effects["g:6"], -1.5)      # A -> F activation
        # D -> E binding and C -> E phosphorylation are unsigned: E is not reached.
        self.assertNotIn("g:5", effects)
        # F -> G binding is unsigned.
        self.assertNotIn("g:7", effects)
        self.assertEqual(set(effects), {"g:1", "g:2", "g:3", "g:4", "g:6"})

    def test_propagate_stops_where_steps_say(self):
        one = sim.propagate(self.net, "g:1", steps=1)
        self.assertEqual(set(one), {"g:1", "g:2", "g:4", "g:6"})
        self.assertEqual(sim.propagate(self.net, "g:1", steps=0), {"g:1": sim.EFFECT})

    def test_propagate_goes_downstream_only(self):
        """From C: B -| C is walked against its arrow, C -> E is unsigned."""
        self.assertEqual(sim.propagate(self.net, "g:3"), {"g:3": sim.EFFECT})
        # Against the arrows B's inhibition of C would plant B with a flipped sign.
        both_ways = sim.propagate(self.net, "g:3", downstream=False)
        self.assertAlmostEqual(both_ways["g:2"], 1.5)

    def test_propagate_refuses_an_unknown_node(self):
        with self.assertRaises(KeyError):
            sim.propagate(self.net, "g:404")

    # ---- simulate ---------------------------------------------------------
    def test_simulate_rows_are_sorted_seven_fields_and_the_knockout_is_relevant(self):
        rows, relevant = sim.simulate(self.net, self.ensembl, "Aaa", seed=0)
        self.assertEqual(len(rows), 7)
        self.assertEqual([row[0] for row in rows], sorted(row[0] for row in rows))
        for row in rows:
            self.assertEqual(len(row), 7)
            for value in row[1:]:
                self.assertIsInstance(value, float)
                self.assertEqual(value, round(value, 6))
        knockout = next(row for row in rows if row[0] == ensembl_id(1))
        self.assertIn(ensembl_id(1), relevant)
        self.assertLess(knockout[-1], -sim.RELEVANT_ABS)
        # The effect develops: the first column carries a quarter of it.
        self.assertGreater(knockout[1], knockout[-1])
        self.assertEqual(relevant, sorted(relevant))
        self.assertTrue(set(relevant) <= {row[0] for row in rows})

    def test_simulate_is_deterministic_for_a_seed_and_changes_with_it(self):
        first = sim.simulate(self.net, self.ensembl, "Aaa", seed=0)
        self.assertEqual(first, sim.simulate(self.net, self.ensembl, "Aaa", seed=0))
        self.assertNotEqual(first[0], sim.simulate(self.net, self.ensembl, "Aaa", seed=1)[0])

    def test_simulate_leaves_out_genes_without_an_ensembl_id(self):
        rows, _ = sim.simulate(self.net, {"1": ensembl_id(1), "2": ensembl_id(2)}, "Aaa")
        self.assertEqual([row[0] for row in rows], [ensembl_id(1), ensembl_id(2)])

    # ---- files ------------------------------------------------------------
    def test_write_dataset_writes_both_files_with_the_header(self):
        rows, relevant = sim.simulate(self.net, self.ensembl, "Aaa", seed=0)
        out_dir = tempfile.mkdtemp(prefix="simulate_ko_")
        try:
            sim.write_dataset(out_dir, rows, relevant)
            with open(os.path.join(out_dir, "data", sim.VALUES_NAME)) as handle:
                lines = handle.read().split("\n")
            self.assertEqual(lines[0], "#geneID\t" + "\t".join(sim.COLUMNS))
            self.assertEqual(lines[-1], "")
            self.assertEqual(len(lines) - 2, len(rows))
            for line in lines[1:-1]:
                cells = line.split("\t")
                self.assertEqual(len(cells), 7)
                [float(cell) for cell in cells[1:]]
                self.assertNotIn("-0.000000", cells)
            with open(os.path.join(out_dir, "data", sim.RELEVANT_NAME)) as handle:
                self.assertEqual(handle.read().split("\n")[:-1], relevant)
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    def test_build_writes_the_dataset_and_its_ground_truth(self):
        out_dir = tempfile.mkdtemp(prefix="simulate_ko_build_")
        try:
            result = sim.build(self.data_dir, "tst", out_dir, gene_symbol="Aaa", seed=0)
            self.assertEqual(result["gene"], "Aaa")
            self.assertEqual(result["node"], "g:1")
            self.assertEqual(result["genes"], 7)
            self.assertEqual(result["planted"], 5)
            self.assertEqual(result["signal"], 5)
            self.assertEqual([row[0] for row in result["pathways"]], ["tst00001", "tst00002"])
            # tst00001 holds A..E (A, B, C, D planted); tst00002 holds A, F, G (A, F planted).
            self.assertEqual(result["pathways"][0][1:], [4, 5])
            self.assertEqual(result["pathways"][1][1:], [2, 3])
            for name in ("data/" + sim.VALUES_NAME, "data/" + sim.RELEVANT_NAME,
                         "expected/" + sim.SIGNAL_NAME, "expected/" + sim.PATHWAYS_NAME):
                self.assertTrue(os.path.getsize(os.path.join(out_dir, name)) > 0, name)
            with open(os.path.join(out_dir, "expected", sim.SIGNAL_NAME)) as handle:
                signal = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
            self.assertEqual(signal, sorted(ensembl_id(entrez) for entrez in (1, 2, 3, 4, 6)))
            with open(os.path.join(out_dir, "expected", sim.PATHWAYS_NAME)) as handle:
                pathways = [line.split("\t") for line in handle if line.strip() and not line.startswith("#")]
            self.assertEqual([row[0] for row in pathways], ["tst00001", "tst00002"])
            self.assertEqual(pathways[0][1], "Test pathway one")
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)


class ShippedPtenDatasetTest(unittest.TestCase):
    """The knockout ships as an unlisted dataset directory -- a fixture the
    five-check harness loads by path, not a picker entry (a knockout planted
    two steps down a network cannot meet the picker's planted-pathway coverage
    floor) -- with its two data files and the expected lists."""

    def test_the_dataset_directory_ships_its_files(self):
        folder = os.path.join(EXAMPLE_DIR, "datasets", "13-simulated-pten-knockout")
        for name in ("data/gene_expression_values.tab", "data/gene_expression_relevant.tab",
                     "expected/signal_features.txt", "expected/expected_pathways.txt", "README.md"):
            path = os.path.join(folder, name)
            self.assertTrue(os.path.isfile(path), "%s is missing" % path)
            self.assertGreater(os.path.getsize(path), 0, path)
        with open(os.path.join(folder, "data", "gene_expression_values.tab"), encoding="utf-8") as handle:
            self.assertEqual(handle.readline().rstrip("\n"), "#geneID\t" + "\t".join(sim.COLUMNS))
        with open(MANIFEST, encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual([e for e in manifest["scenarios"] if e.get("id") == SCENARIO_ID], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

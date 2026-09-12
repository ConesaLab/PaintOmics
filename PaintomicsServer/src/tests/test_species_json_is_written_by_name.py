#!/usr/bin/env python3
"""species.json is written by display name, the same way by both installers.

What this guards
----------------
``current/species.json`` is the list the Step 1 organism picker downloads.
Two installers write it: DBManager.generateAvailableSpeciesFile after a
standard install and customSpeciesInstaller.regenerate_species_json after a
custom one. Until now each had its own loop and its own order -- DBManager
walked a ``set`` of codes, so the file came out in hash order, different on
every run; the custom installer sorted by code -- and a file of 3,000
organisms (12,000 to come) in either order is unreadable to the administrator
or script that opens it.

Both now go through AdminTools/species_json.py: rows by display name,
case-insensitively, code as the tie-break, in the dropdown's exact format.
The picker orders what it shows on its own (test_organism_search), so this
is about the file, not the screen.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_species_json_is_written_by_name
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))
if SRC not in sys.path:
    # DBManager imports `conf.serverconf` and `AdminTools.species_json`
    # relative to src/, as the installers run it.
    sys.path.insert(0, SRC)

from AdminTools.species_json import species_json_rows, write_species_json

NAMES = {
    "mmu": "Mus musculus (house mouse)",
    "hsa": "Homo sapiens (human)",
    "naz": "'Nostoc azollae' 0708",
    "ath": "Arabidopsis thaliana (thale cress)",
    "eco": "Escherichia coli K-12 MG1655",
    "aprc": "Abrus precatorius (Indian licorice)",
    "zma": "Zea mays (maize)",
}
BY_NAME = ["naz", "aprc", "ath", "eco", "hsa", "mmu", "zma"]


def read_species(path):
    with io.open(path, encoding="utf-8") as handle:
        return json.load(handle)


class Rows(unittest.TestCase):
    """The order of the file: by name, whatever order the codes arrive in."""

    def test_rows_are_by_display_name(self):
        rows = species_json_rows(["zma", "mmu", "hsa", "naz", "ath", "eco", "aprc"], NAMES)
        self.assertEqual(BY_NAME, [code for code, _ in rows])
        self.assertEqual([(code, NAMES[code]) for code in BY_NAME], rows)

    def test_the_order_does_not_depend_on_the_input_order(self):
        codes = list(NAMES)
        self.assertEqual(species_json_rows(codes, NAMES), species_json_rows(list(reversed(codes)), NAMES))
        self.assertEqual(species_json_rows(codes, NAMES), species_json_rows(set(codes), NAMES))

    def test_case_does_not_split_the_alphabet(self):
        names = {"a": "abies", "b": "Abrus", "c": "acer", "d": "Zea"}
        self.assertEqual(["a", "b", "c", "d"], [code for code, _ in species_json_rows("dcba", names)])

    def test_the_code_breaks_a_name_tie(self):
        # KEGG lists Japanese rice twice, once per gene build.
        names = {"osa": "Oryza sativa japonica (Japanese rice)", "dosa": "Oryza sativa japonica (Japanese rice)"}
        self.assertEqual(["dosa", "osa"], [code for code, _ in species_json_rows(["osa", "dosa"], names)])

    def test_duplicate_codes_collapse_to_one_row(self):
        rows = species_json_rows(["mmu", "mmu", "hsa", "mmu"], NAMES)
        self.assertEqual(["hsa", "mmu"], [code for code, _ in rows])

    def test_a_code_without_a_name_is_the_callers_error(self):
        with self.assertRaises(KeyError):
            species_json_rows(["mmu", "zzz"], NAMES)


class Writer(unittest.TestCase):
    """The file itself: the dropdown's format, a backup, escaping."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="species-json-")
        self.target = os.path.join(self.tmp, "species.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_the_dropdown_format(self):
        rows = species_json_rows(list(NAMES), NAMES)
        self.assertEqual(self.target, write_species_json(self.target, rows))
        with io.open(self.target, encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.startswith('{"success": true, "species": [\n\t{"name": '), text[:60])
        self.assertTrue(text.endswith('}\n]}'), text[-20:])
        data = read_species(self.target)
        self.assertIs(True, data["success"])
        self.assertEqual([{"name": NAMES[code], "value": code} for code in BY_NAME], data["species"])

    def test_keeps_the_previous_file_beside_the_new_one(self):
        write_species_json(self.target, [("mmu", NAMES["mmu"])])
        self.assertFalse(os.path.exists(self.target + "_prev"))
        write_species_json(self.target, [("hsa", NAMES["hsa"])])
        self.assertEqual(["mmu"], [s["value"] for s in read_species(self.target + "_prev")["species"]])
        self.assertEqual(["hsa"], [s["value"] for s in read_species(self.target)["species"]])

    def test_a_quote_or_backslash_in_a_name_round_trips(self):
        # customSpeciesInstaller validates --code but not --name.
        name = 'My "custom" \\ organism'
        write_species_json(self.target, [("cust", name)])
        self.assertEqual([{"name": name, "value": "cust"}], read_species(self.target)["species"])

    def test_no_rows_is_still_a_file_the_dropdown_can_parse(self):
        write_species_json(self.target, [])
        self.assertEqual({"success": True, "species": []}, read_species(self.target))


class KeggDataDir(unittest.TestCase):
    """A temp KEGG_DATA/current with the two name lists the installers read."""

    ALL = [("T01002", "mmu", NAMES["mmu"], "Eukaryota;Animals"),
           ("T01001", "hsa", NAMES["hsa"], "Eukaryota;Animals"),
           ("T00007", "eco", NAMES["eco"], "Prokaryotes;Bacteria"),
           ("T01003", "ath", NAMES["ath"], "Eukaryota;Plants")]
    CUSTOM = [("T90001", "cust", 'Custom "organism"', "Eukaryota;Custom")]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kegg-data-")
        self.current = os.path.join(self.tmp, "current")
        self.common = os.path.join(self.current, "common")
        os.makedirs(self.common)
        self.write_list("organisms_all.list", self.ALL)
        self.write_list("organisms_custom.list", self.CUSTOM)
        self.all_list = os.path.join(self.common, "organisms_all.list")
        self.target = os.path.join(self.current, "species.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_list(self, basename, rows):
        with io.open(os.path.join(self.common, basename), "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write("\t".join(row) + "\n")


class DBManagerWriter(KeggDataDir):
    """generateAvailableSpeciesFile, end to end, over a temp KEGG_DATA."""

    @classmethod
    def setUpClass(cls):
        # DBManager imports the server configuration (conf/serverconf.py,
        # gitignored; scripts/ci/run-unit-tests.sh copies the template in).
        # A missing one must fail here, not skip: a suite that skips is a
        # green result that ran nothing.
        from AdminTools.DBManager import generateAvailableSpeciesFile
        cls.generate = staticmethod(generateAvailableSpeciesFile)

    def test_lists_every_installed_species_by_name_whatever_the_input(self):
        # Codes arrive as dicts (this install) and strings (previous ones),
        # with repeats; custom organisms come from organisms_custom.list.
        self.generate([{"organism_code": "mmu"}, "hsa", "hsa", {"organism_code": "cust"}, "eco", "ath"],
                      self.all_list, self.target)
        listed = read_species(self.target)["species"]
        self.assertEqual(["ath", "cust", "eco", "hsa", "mmu"], [s["value"] for s in listed])
        self.assertEqual('Custom "organism"', listed[1]["name"])
        self.assertEqual(NAMES["mmu"], listed[4]["name"])

    def test_writes_the_same_bytes_twice_and_keeps_the_previous_file(self):
        self.generate(["mmu", "hsa"], self.all_list, self.target)
        with io.open(self.target, encoding="utf-8") as handle:
            first = handle.read()
        self.generate(["hsa", "mmu", "eco"], self.all_list, self.target)
        with io.open(self.target + "_prev", encoding="utf-8") as handle:
            self.assertEqual(first, handle.read())
        self.assertEqual(["eco", "hsa", "mmu"], [s["value"] for s in read_species(self.target)["species"]])

    def test_a_species_without_a_display_name_is_shown_by_its_code(self):
        # A code installed in Mongo but absent from both lists used to abort
        # species.json for EVERY organism ("Error while writting specie" for
        # the first such code, later a message naming them all). KEGG adds
        # organisms between two common downloads -- nfo, Naegleria fowleri,
        # installed on 2026-09-12 while the list predated it -- and that abort
        # ended every later install run after its species were installed. The
        # file is written, the code stands in for the name, and the run's
        # warnings say so.
        self.generate(["mmu", "zzz", "yyy"], self.all_list, self.target)
        species = read_species(self.target)["species"]
        self.assertEqual({"mmu", "yyy", "zzz"}, {s["value"] for s in species})
        byCode = {s["value"]: s["name"] for s in species}
        self.assertEqual("yyy", byCode["yyy"])
        self.assertEqual("zzz", byCode["zzz"])
        self.assertNotEqual("mmu", byCode["mmu"], "a named organism keeps its name")


class CustomInstallerWriter(KeggDataDir):
    """regenerate_species_json writes the file DBManager would."""

    class FakeMongo(object):
        def __init__(self, *args, **kwargs):
            pass

        def list_database_names(self):
            return ["admin", "PaintomicsDB", "global-paintomics", "mmu-paintomics",
                    "cust-paintomics", "hsa-paintomics"]

        def close(self):
            pass

    def test_regenerate_matches_dbmanager_byte_for_byte(self):
        import pymongo
        from AdminTools import customSpeciesInstaller
        from AdminTools.DBManager import generateAvailableSpeciesFile
        with mock.patch.object(pymongo, "MongoClient", self.FakeMongo):
            target, count = customSpeciesInstaller.regenerate_species_json(self.tmp, "localhost", 27017)
        self.assertEqual(self.target, target)
        self.assertEqual(3, count)
        with io.open(target, encoding="utf-8") as handle:
            custom = handle.read()
        generateAvailableSpeciesFile(["hsa", "mmu", "cust"], self.all_list, self.target)
        with io.open(self.target, encoding="utf-8") as handle:
            self.assertEqual(custom, handle.read())
        self.assertEqual(["cust", "hsa", "mmu"], [s["value"] for s in read_species(self.target)["species"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)

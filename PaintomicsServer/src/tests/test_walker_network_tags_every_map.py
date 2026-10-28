#!/usr/bin/env python3
"""A KEGG edge in the walker's network carries every map that draws it.

The network was built on parse_directory, which keeps one copy of a gene pair
-- the first map's, by file name -- for the hub graph. Each shared relation got
one ``KEGG:<map>`` tag, so a map whose relations are all drawn first elsewhere
tagged no edge: on paintomics.org (2026-10-28) 16 of the 336 hsa maps that draw
relations, Bladder cancer (hsa05219) among them, and every walk on it was
refused with "Pathway hsa05219 draws no interactions a walk can follow". 125
more kept under half of their own edges (hsa01200 8 of 275).

The second map here draws only relations the first draws too, so on the old
build it has no tag at all: resolve_scope refuses it and these tests fail.

Tagged with every map, an edge still kept one merged sign and subtype, and a
pathway walk read that merge instead of its own map: on hsa 427 pairs carry
opposite signs in two maps (NF1 -> HRAS inhibits in hsa04010, activates in
hsa04014). The third map here draws 1 -> 5 against the first.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_walker_network_tags_every_map
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import network as net_mod     # noqa: E402
from src.classes.AIInterpret.walker.service import resolve_scope  # noqa: E402

# 1 -> 2: binding in the first map, activation in the second.
# 3 -> 4: two enzymes joined through ATP (a currency metabolite) in the first
#         map and through lactate in the second.
# 3 -> C00022: a reaction in the second map naming pyruvate, which only the
#         first map draws as an entry.
FIRST = """<?xml version="1.0"?>
<pathway name="path:tst00001" org="tst" number="00001">
  <entry id="1" name="tst:1" type="gene"/>
  <entry id="2" name="tst:2" type="gene"/>
  <entry id="3" name="tst:3" type="gene"/>
  <entry id="4" name="tst:4" type="gene"/>
  <entry id="5" name="tst:5" type="gene"/>
  <entry id="6" name="cpd:C00002" type="compound"/>
  <entry id="7" name="cpd:C00022" type="compound"/>
  <relation entry1="1" entry2="2" type="PPrel"><subtype name="binding/association" value="---"/></relation>
  <relation entry1="1" entry2="5" type="PPrel"><subtype name="activation" value="--&gt;"/></relation>
  <relation entry1="3" entry2="4" type="ECrel"><subtype name="compound" value="6"/></relation>
</pathway>
"""
SECOND = """<?xml version="1.0"?>
<pathway name="path:tst00002" org="tst" number="00002">
  <entry id="1" name="tst:1" type="gene"/>
  <entry id="2" name="tst:2" type="gene"/>
  <entry id="3" name="tst:3" type="gene"/>
  <entry id="4" name="tst:4" type="gene"/>
  <entry id="5" name="cpd:C00186" type="compound"/>
  <relation entry1="1" entry2="2" type="PPrel"><subtype name="activation" value="--&gt;"/></relation>
  <relation entry1="3" entry2="4" type="ECrel"><subtype name="compound" value="5"/></relation>
  <reaction id="3" name="rn:R00200" type="irreversible"><substrate id="9" name="cpd:C00022"/></reaction>
</pathway>
"""
# 1 -> 5: an inhibition here, an activation in the first map.
THIRD = """<?xml version="1.0"?>
<pathway name="path:tst00003" org="tst" number="00003">
  <entry id="1" name="tst:1" type="gene"/>
  <entry id="2" name="tst:5" type="gene"/>
  <relation entry1="1" entry2="2" type="PPrel"><subtype name="inhibition" value="--|"/></relation>
</pathway>
"""


def reading(net, a, b):
    edge = net.edges[(a, b)]
    return edge["sign"], edge["subtype"]


class NetworkTagsEveryMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = tempfile.mkdtemp(prefix="walker_tags_")
        kgml = os.path.join(cls.data_dir, "current", "tst", "kgml")
        os.makedirs(kgml)
        for name, text in (("tst00001.kgml", FIRST), ("tst00002.kgml", SECOND), ("tst00003.kgml", THIRD)):
            with open(os.path.join(kgml, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        cls.net = net_mod.build_network("tst", cls.data_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def test_a_shared_edge_carries_both_maps(self):
        self.assertEqual(self.net.edges[("g:1", "g:2")]["tags"], ["KEGG:tst00001", "KEGG:tst00002"])
        self.assertIn("KEGG:tst00002", self.net.nodes["g:1"]["pathways"])
        self.assertEqual(self.net.sources["kegg"], 3, "each readable map is one file read")

    def test_each_pathway_reads_its_own_maps_sign_and_subtype(self):
        """The network keeps the merge; each pathway's subgraph, what its map draws."""
        self.assertEqual(reading(self.net, "g:1", "g:5"), (1, "activation"))
        self.assertEqual(reading(self.net.filter_pathway("KEGG:tst00001"), "g:1", "g:5"), (1, "activation"))
        self.assertEqual(reading(self.net.filter_pathway("KEGG:tst00003"), "g:1", "g:5"), (-1, "inhibition"))
        self.assertEqual(reading(self.net, "g:1", "g:2"), (1, "binding/association"))
        self.assertEqual(reading(self.net.filter_pathway("KEGG:tst00001"), "g:1", "g:2"), (0, "binding/association"))
        self.assertEqual(reading(self.net.filter_pathway("KEGG:tst00002"), "g:1", "g:2"), (1, "activation"))

    def test_only_a_reading_that_differs_from_the_merge_is_stored(self):
        self.assertEqual(self.net.edges[("g:1", "g:5")]["by_tag"], {"KEGG:tst00003": [-1, "inhibition"]})
        self.assertEqual(self.net.edges[("g:1", "g:2")]["by_tag"],
                         {"KEGG:tst00001": [0, "binding/association"], "KEGG:tst00002": [1, "activation"]})
        self.assertNotIn("by_tag", self.net.edges[("g:3", "g:4")], "one map draws it: nothing to keep")

    def test_the_cache_keeps_each_maps_reading(self):
        again = net_mod.Network.from_dict(json.loads(json.dumps(self.net.to_dict())))
        self.assertEqual(again.edges[("g:1", "g:5")]["by_tag"], {"KEGG:tst00003": [-1, "inhibition"]})
        self.assertEqual(reading(again.filter_pathway("KEGG:tst00003"), "g:1", "g:5"), (-1, "inhibition"))
        self.assertEqual(reading(again, "g:1", "g:5"), (1, "activation"))
        # A cache row written without the sixth element still reads.
        old = self.net.to_dict()
        old["edges"] = [row[:5] for row in old["edges"]]
        self.assertNotIn("by_tag", net_mod.Network.from_dict(old).edges[("g:1", "g:5")])

    def test_a_map_drawn_first_elsewhere_is_a_walk_scope(self):
        """hsa05219: refused with 'draws no interactions a walk can follow'."""
        self.assertEqual(resolve_scope(self.net, "pathway:tst00002"), "KEGG:tst00002")
        sub = self.net.filter_pathway("KEGG:tst00002")
        self.assertEqual(set(sub.edges), {("g:1", "g:2"), ("g:3", "g:4"), ("g:3", "c:C00022")})
        self.assertEqual(set(sub.nodes), {"g:1", "g:2", "g:3", "g:4", "c:C00022"})

    def test_a_later_map_signs_an_edge_the_first_drew_unsigned(self):
        """Network.add_edge's rule across databases, now across maps too: the
        sign is upgraded, the first non-empty subtype stays."""
        edge = self.net.edges[("g:1", "g:2")]
        self.assertEqual(edge["sign"], 1)
        self.assertEqual(edge["subtype"], "binding/association")

    def test_the_currency_filter_still_reads_each_relation_on_its_own(self):
        """Dropped where ATP joins the pair, kept where lactate does."""
        self.assertEqual(self.net.edges[("g:3", "g:4")]["tags"], ["KEGG:tst00002"])
        self.assertEqual(set(self.net.filter_pathway("KEGG:tst00001").edges),
                         {("g:1", "g:2"), ("g:1", "g:5")})

    def test_a_compound_is_typed_by_whichever_map_draws_it(self):
        """hsa00510's reaction R06258 names glycan G10617, which only another
        map draws as an entry. Typed map by map, that edge was lost."""
        self.assertEqual(self.net.edges[("g:3", "c:C00022")]["tags"], ["KEGG:tst00002"])


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

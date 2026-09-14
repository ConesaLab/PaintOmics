#!/usr/bin/env python3
"""`Pathway.significanceValues` is a list of per-condition triples, not one triple.

The structure, built by `Pathway.addSignificanceValues` and filled in by
`setSignificancePvalue`, is

    significanceValues[omicName] = [[totalMatched, totalRelevant, pValue],   # condition 1
                                    [totalMatched, totalRelevant, pValue],   # condition 2
                                    ...]

A reader that took it for one flat triple once read the third condition as the
p-value, and a single-condition job as having no significant omic at all.
Stated plainly here so every consumer can be read against it.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_significance_context_shape
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.Pathway import Pathway


def _pathway(omicName="Gene expression", relevantPerFeature=None, pvalues=None):
    """A Pathway carrying the structure the real pipeline builds."""
    pathway = Pathway("mmu04210")
    for relevantList in (relevantPerFeature or [[True]]):
        pathway.addSignificanceValues(omicName, relevantList)
    if pvalues is not None:
        pathway.setSignificancePvalue(omicName, pvalues)
    return pathway


class StructureTest(unittest.TestCase):
    """State the shape plainly, so the consumers below can be read against it."""

    def test_significance_values_is_one_triple_per_condition(self):
        pathway = _pathway(relevantPerFeature=[[True, False, False],
                                               [False, False, True]],
                           pvalues=[0.7, 0.8, 0.9])

        values = pathway.significanceValues["Gene expression"]
        self.assertEqual(len(values), 3, "outer length is the condition count")
        for triple in values:
            self.assertEqual(len(triple), 3,
                             "each condition is [totalMatched, totalRelevant, pValue]")
        self.assertEqual([triple[2] for triple in values], [0.7, 0.8, 0.9])


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

"""The combined-p-value kernels are bit-identical to the scipy calls they replace.

Statistics.calculateStoufferCombinedPvalue used scipy.stats.combine_pvalues
(~145 us per call, mostly the axis/nan-policy wrapper) and
calculateCombinedFisher used chi2.sf (~20 us); both run a few times per
matched pathway. They now call the special functions those reduce to
(ndtri/ndtr, chdtrc) with the same numpy operations and dtypes, over random
cases that cover int, float, mixed and absent weights, extreme p-values and
the exact clamps the callers apply (1e-300 floor, 0.9999999999 ceiling), plus
the public functions themselves against the old formulae.

The Fisher half is pinned bit for bit. The Stouffer half is pinned to a
relative 1e-12 instead, because scipy's own accumulation order there is not a
stable contract and moved under us: 1.13 computed dot(w, Zi)/np.linalg.norm(w),
1.14+ computes sum(w*Zi) over an axis-wise vector norm. np.linalg.norm's 1-D
path is sqrt(x.dot(x)) -- BLAS ddot -- so it also varied with which BLAS the
numpy wheel shipped (OpenBLAS on the 1.26 macOS arm64 wheels, Apple Accelerate
on the 2.x ones). Either reordering moves Z by one ulp and ndtr's far tail
amplifies that to ~5e-13 relative at p ~ 1e-300, so an exact assertion here
pins the build, not the mathematics. 1e-12 is about ten times the widest drift
observed and about eleven orders tighter than any real formula error --
test_the_tolerance_rejects_what_a_real_bug_would_do keeps that honest.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_combined_pvalue_kernels_match_scipy
"""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import numpy as np
from scipy.stats import chi2, combine_pvalues

from src.common import Statistics


def _randomCase(rng):
    k = rng.randint(1, 8)
    pvalues = [min(rng.random() ** rng.choice([1, 3, 10, 30, 100]), 0.9999999999)
               for _ in range(k)]
    if rng.random() < 0.2:
        pvalues = [rng.choice([1e-300, 0.9999999999, 0.5, 1e-17, 1e-100])
                   for _ in range(k)]
    kind = rng.choice(["none", "int", "float", "mixed"])
    if kind == "none":
        weights = None
    elif kind == "int":
        weights = [rng.randint(0, 5) for _ in range(k)]
    elif kind == "float":
        weights = [rng.random() for _ in range(k)]
    else:
        weights = [rng.choice([1, 0.5, 2, 0.25]) for _ in range(k)]
    if weights is not None and not any(weights):
        weights[0] = 1
    return pvalues, weights


#: Widest Stouffer drift observed between scipy 1.13.1 and 1.17.1 over this
#: file's own corpus is ~5e-13 relative. Ten times that leaves room for one
#: more reordering without hiding anything a person would call a bug.
_STOUFFER_TOLERANCE = 1e-12


def matchesScipy(got, want, tolerance=_STOUFFER_TOLERANCE):
    """`got` is scipy's answer to within `tolerance` relative.

    Not repr() equality: see the module docstring for why scipy's Stouffer
    accumulation is not something a test can pin exactly across releases.
    """
    got = float(got)
    want = float(want)
    if got == want:                      # covers +-0.0 and both infinities
        return True
    if math.isnan(got) or math.isnan(want):
        return math.isnan(got) and math.isnan(want)
    if math.isinf(got) or math.isinf(want):
        return False
    return abs(got - want) <= tolerance * max(abs(got), abs(want))


class StoufferKernelTest(unittest.TestCase):

    def test_matches_combine_pvalues(self):
        rng = random.Random(20260817)
        for _ in range(8000):
            pvalues, weights = _randomCase(rng)
            want = combine_pvalues(pvalues, "stouffer", weights)[1]
            got = Statistics._stoufferPvalue(pvalues, weights)
            self.assertTrue(matchesScipy(got, want), (pvalues, weights, got, want))
            self.assertIsInstance(got, np.floating)

    def test_the_tolerance_rejects_what_a_real_bug_would_do(self):
        """The relaxed assertion above is not a rubber stamp.

        A kernel that had genuinely drifted -- a wrong weight normalisation, a
        dropped term, the one-sided/two-sided confusion -- moves a p-value by
        parts in a thousand or more. This walks the same corpus and confirms
        that a perturbation a thousand times SMALLER than that, and still a
        thousand times larger than the cross-version noise, is caught.
        """
        rng = random.Random(20260817)
        checked = 0
        for _ in range(2000):
            pvalues, weights = _randomCase(rng)
            want = float(combine_pvalues(pvalues, "stouffer", weights)[1])
            if want == 0.0 or not math.isfinite(want):
                continue
            self.assertFalse(matchesScipy(want * (1 + 1e-9), want),
                             "tolerance let a 1e-9 relative error through at %r" % want)
            checked += 1
        self.assertGreater(checked, 1000, "corpus degenerated; nothing was checked")

    def test_public_function_matches_the_old_formula(self):
        rng = random.Random(4)
        for _ in range(2000):
            pvalues, weights = _randomCase(rng)
            usable = [p for p in pvalues if 0 <= p <= 1]
            curated = [min(p, 0.9999999999) for p in usable]
            want = combine_pvalues(curated, "stouffer", weights)[1]
            if not math.isfinite(want):
                want = 1.0
            got = Statistics.calculateStoufferCombinedPvalue(pvalues, weights)
            self.assertTrue(matchesScipy(got, want), (pvalues, weights, got, want))

    def test_degenerate_inputs_keep_their_old_answers(self):
        self.assertEqual(Statistics.calculateStoufferCombinedPvalue([], None), 1.0)
        self.assertEqual(Statistics.calculateStoufferCombinedPvalue([0.5, 0.1], [0, 0]), 1.0)
        self.assertEqual(Statistics.calculateStoufferCombinedPvalue([-1.0, 2.0], [1, 1]), 1.0)


class FisherKernelTest(unittest.TestCase):

    def test_bit_identical_to_chi2_sf(self):
        rng = random.Random(99)
        for _ in range(8000):
            pvalues, _ = _randomCase(rng)
            if rng.random() < 0.1:
                pvalues = pvalues + [0.0, 1.0]
            accumulated = 0
            for p in pvalues:
                accumulated += math.log(max(p, 1e-300))
            accumulated *= -2
            want = chi2.sf(accumulated, 2 * len(pvalues))
            got = Statistics.calculateCombinedFisher(pvalues)
            self.assertEqual(repr(float(got)), repr(float(want)), pvalues)

    def test_empty_and_unusable_lists_stay_one(self):
        self.assertEqual(Statistics.calculateCombinedFisher([]), 1.0)
        self.assertEqual(Statistics.calculateCombinedFisher([-1.0, float("nan")]), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

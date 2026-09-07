import logging
from math import log, isfinite
import numpy as np
from scipy import special as _special
from scipy.stats import hypergeom
# statsmodels.stats.multitest, not statsmodels.sandbox.stats.multicomp: the
# sandbox module is a re-export that 0.15.0 removed, and the dependabot
# pull request carrying that bump failed 36 suites on this one import
# (#118). On the pinned 0.14.6 the two names are the SAME object, so this
# is a no-op today and the bump becomes possible tomorrow.
from statsmodels.stats.multitest import multipletests

# Distinct out-of-domain argument tuples already reported by calculateFisher.
# Bounded, because the alternative is one WARNING per pathway per omic: the
# unmapped-feature line in PathwayAcquisitionJob produced 6480 lines for a
# single run before it was capped, and a log that long stops being a diagnostic.
_reportedFisherDomainViolations = set()
_MAX_REPORTED_FISHER_DOMAIN_VIOLATIONS = 20
##*******************************************************************************************
##****AUXLIAR FUNCTION DEFINITION************************************************************
##*******************************************************************************************
def calculateSignificance(test, totalFeatures, totalRelevantFeatures, totalFeaturesInPathway, totalRelevantFeaturesInPathway):
    if test == "fisher":
        return calculateFisher(totalFeatures, totalFeaturesInPathway, totalRelevantFeatures, totalRelevantFeaturesInPathway)
    else:
        raise NotImplementedError

def calculateCombinedSignificancePvalue(combinedTest, significanceValuesList):
    if len(significanceValuesList) < 2: #Do not calculate if only one omic
        return None
    elif combinedTest == "fisher-combined":
        return calculateCombinedFisher(significanceValuesList)
    else:
        raise NotImplementedError

def _clampToHypergeometricDomain(totalElems, foundElems, totalSignificative, foundSignificative):
    """Force the four counts into the domain hypergeom is defined on.

    The hypergeometric test only means anything when the sample is drawn from
    the population: 0 <= foundElems <= totalElems, 0 <= totalSignificative <=
    totalElems, and 0 <= foundSignificative <= min(foundElems,
    totalSignificative). Outside that, scipy answers with a number that is not
    a p-value, and both of its answers are actively harmful here:

      * sample larger than the population -> NaN. jsonify writes a NaN as the
        bare token `NaN`, which is not valid JSON (RFC 8259), so the client's
        JSON.parse rejects the whole step-2 response and the user sees
        "Oops..Internal error! Unable to parse the error message". One bad
        pathway out of hundreds destroys the entire result.
      * more successes in the sample than exist in the population -> sf is
        exactly 0.0, floored below to 1e-300, i.e. the strongest possible
        evidence. That is the failure `_usablePvalues` calls "the worst of the
        available failures": a silent maximally-significant answer that sends
        a meaningless pathway to the top of the results table.

    Clamping rather than raising, for two reasons. This runs once per pathway
    per omic inside generatePathwaysList, so raising turns a per-pathway
    accounting glitch into the loss of a whole multi-minute job -- and the rest
    of this module already answers degenerate input with a usable, neutral
    p-value (calculateCombinedFisher on an empty list, Stouffer on all-zero
    weights, adjustPvalues on non-finite input) instead of failing the run.
    Second, the clamp is conservative by construction: once foundElems is
    pulled down to totalElems the sample is the whole population, so
    sf(k-1, N, K, N) is 1.0 for every k <= K, and k is clamped to <= K. A
    caller that got here through a counting bug gets "not significant", never
    a fabricated hit.

    Returns the corrected 4-tuple; identical to the input when it was valid.
    """
    correctedTotal = max(int(totalElems), 0)
    correctedFound = min(max(int(foundElems), 0), correctedTotal)
    correctedTotalSig = min(max(int(totalSignificative), 0), correctedTotal)
    correctedFoundSig = min(max(int(foundSignificative), 0),
                            correctedFound, correctedTotalSig)
    return correctedTotal, correctedFound, correctedTotalSig, correctedFoundSig


def calculateFisher(totalElems, foundElems, totalSignificative, foundSignificative):
    # Using hypergeom.sf is faster than fisher_exact for right-tailed tests
    # Population size: totalElems
    # Number of successes in population: totalSignificative
    # Sample size: foundElems
    # Number of successes in sample: foundSignificative
    # sf(k) = P(X > k)
    # We want P(X >= foundSignificative) = sf(foundSignificative - 1)
    original = (totalElems, foundElems, totalSignificative, foundSignificative)
    corrected = _clampToHypergeometricDomain(*original)

    if corrected != original:
        # Named values, because the only useful response to this line is to go
        # and find which counter is in the wrong units. The historical cause was
        # exactly that: calculateTotalFeaturesByOmic counted the background in
        # target-ID units while testPathwaySignificance counted the pathway in
        # input-ID units, so a pathway could legitimately report more matches
        # than the background said existed.
        if (original not in _reportedFisherDomainViolations
                and len(_reportedFisherDomainViolations)
                < _MAX_REPORTED_FISHER_DOMAIN_VIOLATIONS):
            _reportedFisherDomainViolations.add(original)
            logging.warning(
                "calculateFisher received counts outside the hypergeometric "
                "domain (totalElems=%s, foundElems=%s, totalSignificative=%s, "
                "foundSignificative=%s); clamped to (%s, %s, %s, %s). This is "
                "an enrichment counting bug, not user data: two of these are "
                "being counted in different units."
                % (original + corrected))
        totalElems, foundElems, totalSignificative, foundSignificative = corrected

    if foundSignificative == 0:
        return 1.0
    p = hypergeom.sf(foundSignificative - 1, totalElems, totalSignificative, foundElems)
    # Ensure we don't return absolute 0 for display consistency
    return max(p, 1e-300)

def _extractPvalue(significanceValues):
    """Accept both [nFeatures, nRelevant, pValue] and a bare p-value."""
    if isinstance(significanceValues, (list, tuple)) and len(significanceValues) >= 3:
        return significanceValues[2]
    return significanceValues


def _usablePvalues(significanceValuesList):
    """The p-values in [0, 1]; anything else is not evidence and is dropped.

    A p-value outside that range is not something either combining method can
    do anything sensible with, and the arithmetic quietly picks the worst
    available answer if left alone. Fisher's method needs ln(p), so the clamp
    below is `max(pVal, 1e-300)` to avoid log(0) -- correct for zero, and wrong
    for everything under it, because a negative clamps *up* to the smallest
    positive p-value the function admits, i.e. the strongest possible evidence.

    `Pathway` initialises its p-value slot to the sentinel -1.0, so a slot
    meaning "not computed yet" would read as "overwhelmingly significant": one
    of them drags a pathway from p=0.98 to p=6e-298, straight to the top of the
    results table. No reachable path through today's callers was found, but a
    silent maximally-significant answer is the worst of the available failures,
    so out-of-range input is ignored rather than clamped into evidence.

    Returns (pvalues, keptIndices) so a parallel weight vector can be filtered
    the same way.
    """
    pvalues, keptIndices = [], []
    for index, significanceValues in enumerate(significanceValuesList or []):
        pVal = _extractPvalue(significanceValues)
        if isinstance(pVal, bool) or not isinstance(pVal, (int, float)):
            continue
        # NaN is a float, and both comparisons below are False for it, so it
        # went straight through the range check and out the other side --
        # despite this function promising p-values in [0, 1]. It WAS reachable:
        # calculateFisher returned hypergeom.sf(...), which is NaN whenever the
        # sample is larger than the population, e.g.
        #     calculateFisher(10, 20, 5, 8) -> nan
        # That route is now closed at both ends -- the enrichment counters were
        # keying the population and the sample in different units, and
        # calculateFisher clamps its arguments into the hypergeometric domain --
        # so this branch is a backstop for anything degenerate arriving another
        # way, not a live filter. Kept because the failure it prevents is total:
        # Fisher combined the NaN into NaN, and a NaN reaching jsonify is
        # written as the bare token `NaN`, which is not valid JSON (RFC 8259),
        # so the client's JSON.parse rejects the entire response --
        # "Oops..Internal error! Unable to parse the error message". That is
        # the same failure calculateStoufferCombinedPvalue documents and
        # guards against; Stouffer survived only because of its own isfinite
        # backstop, which Fisher never had.
        if not isfinite(pVal):
            continue
        if pVal < 0 or pVal > 1:
            continue
        pvalues.append(pVal)
        keptIndices.append(index)
    return pvalues, keptIndices


def calculateCombinedFisher(significanceValuesList):
    #X^2_2k ~ -2 * sum(ln(p_i))
    if not significanceValuesList: return 1.0

    pvalues, _kept = _usablePvalues(significanceValuesList)
    if not pvalues:
        return 1.0

    accumulatedValue = 0
    for pVal in pvalues:
        accumulatedValue += log(max(pVal, 1e-300)) # Avoid log(0)

    accumulatedValue = accumulatedValue * -2

    # chi2.sf(x, df) is sc.chdtrc(df, x) after the distribution wrapper's
    # argument checks (chi2_gen._sf), which cost ~20 us per call against
    # ~0.5 us for the function itself; this runs once per pathway per
    # condition. accumulatedValue is finite and >= 0 here (every p <= 1 and
    # floored at 1e-300), so the wrapper's support handling never changes the
    # answer -- pinned bit for bit against chi2.sf in
    # test_combined_pvalue_kernels_match_scipy.
    combined = _special.chdtrc(2*len(pvalues), accumulatedValue)

    # The same backstop calculateStoufferCombinedPvalue carries, for the same
    # reason: a non-finite result is serialised as invalid JSON and takes the
    # whole response down with it. The NaN route in is closed above, so this
    # only covers anything degenerate that arrives another way.
    if not isfinite(combined):
        return 1.0

    return combined

# fdr_bh (default), fdr_by, nada
def adjustPvalues(pvaluesList):
    # Returns array [reject, pvals_corrected, alphacSidak, alphacBonf]
    adjust_methods = {'fdr_bh': 'FDR BH', 'fdr_by': 'FDR BY'}

    # multipletests([]) raises ZeroDivisionError: float division by zero, so an
    # analysis that legitimately matched nothing died in step 2 with a division
    # error instead of reporting an empty result. Reached by uploading features
    # that map to no pathway -- a compound-only job whose metabolites are not in
    # the organism's pathways will do it. There is nothing to correct in that
    # case, and an empty correction is the right answer.
    if not pvaluesList:
        return {label: {} for label in adjust_methods.values()}

    # Non-finite input is dropped before correcting, for the same reason
    # _usablePvalues drops it before combining -- and this was the half of that
    # fix that got missed. multipletests propagates a NaN: one NaN among three
    # p-values produced six non-finite numbers here, every corrected value in
    # both methods. Those reach jsonify as the bare token `NaN`, which is not
    # valid JSON, so the client's JSON.parse rejects the entire response.
    #
    # calculateFisher WAS a live source of NaN -- hypergeom.sf returns it when
    # the sample is larger than the population, e.g. calculateFisher(10, 20, 5,
    # 8) -- and it now clamps into the domain instead, so this guard is a
    # backstop. It stays, because the same value that motivated _usablePvalues
    # reaches this function too.
    #
    # The keys stay, because the caller subscripts them directly:
    #     {adjust_method: pvalues[pathway_id] for ...}
    # would raise KeyError on a dropped pathway. They come back as 1.0, which
    # is what the rest of this module already returns when there is nothing to
    # go on -- calculateCombinedFisher for an empty list, Stouffer for a
    # degenerate one.
    usable = {key: value for key, value in pvaluesList.items()
              if isinstance(value, (int, float)) and not isinstance(value, bool)
              and isfinite(value)}
    unusable = [key for key in pvaluesList if key not in usable]

    if not usable:
        return {label: {key: 1.0 for key in pvaluesList}
                for label in adjust_methods.values()}

    adjusted_pvalues = {adjust_methods[adjust_method]: dict(zip(usable.keys(), multipletests(list(usable.values()), method = adjust_method)[1].tolist())) for adjust_method in adjust_methods.keys()}

    for corrected in adjusted_pvalues.values():
        corrected.update({key: 1.0 for key in unusable})

    return adjusted_pvalues


def _stoufferPvalue(pvalues, weights):
    """
    scipy.stats.combine_pvalues(pvalues, 'stouffer', weights).pvalue, computed
    the way scipy computes it -- Zi = norm.isf(p); Z = sum(w*Zi)/||w||_2;
    p = norm.sf(Z) -- but calling the special functions those distribution
    methods reduce to (norm.isf is -ndtri, norm.sf is ndtr(-x)) instead of
    going through combine_pvalues' axis/nan-policy wrapper and two
    rv_continuous method calls, which cost ~145 us per call against ~3 us
    here. This runs a few times per matched pathway. Weights stay integer when
    the caller passed integers, as scipy's np.atleast_1d keeps them.

    The two reductions are written out rather than called as np.dot and
    np.linalg.norm ON PURPOSE, and this is load-bearing. np.linalg.norm's 1-D
    path is sqrt(x.dot(x)) -- BLAS ddot -- and the BLAS is a property of the
    wheel, not of numpy: the 1.26 macOS arm64 wheels link OpenBLAS and the 2.x
    ones link Apple Accelerate. That made the same input produce different last
    digits on two installs of the same pinned version, and ndtr's far tail turns
    one ulp of Z into ~5e-13 relative at p ~ 1e-300. sum and sqrt touch no BLAS
    and agree everywhere. scipy 1.14+ moved combine_pvalues off np.linalg.norm
    for the same reason, so this is also the arrangement that tracks it.

    Agreement with combine_pvalues is checked to a relative tolerance rather
    than bit for bit -- see test_combined_pvalue_kernels_match_scipy, which
    explains why scipy's own accumulation order is not a stable contract.
    """
    p = np.atleast_1d(pvalues)
    if weights is None:
        w = np.ones_like(p)
    else:
        w = np.atleast_1d(weights)
    Zi = -_special.ndtri(p)
    statistic = np.sum(w * Zi) / np.sqrt(np.sum(w * w))
    # A numpy float64, as combine_pvalues returned (a float subclass, so
    # every consumer -- isfinite, JSON, BSON -- sees the same value).
    return _special.ndtr(-statistic)


def calculateStoufferCombinedPvalue(pvalues, weights):
    # Stouffer method cannot deal with p-values equal to 1, returning Nan
    # Prevent that by removing a small value in those cases
    # Out-of-range values are dropped for the same reason as in
    # calculateCombinedFisher: the -1.0 sentinel is "not computed yet", not
    # evidence. The weight vector is filtered alongside so the two stay
    # aligned -- Stouffer pairs them positionally.
    pvalueList = list(pvalues)
    usable, keptIndices = _usablePvalues(pvalueList)
    curatedPvalues = [min(val, 0.9999999999) for val in usable]

    if weights is not None:
        weightList = list(weights)
        if len(weightList) == len(pvalueList):
            weights = [weightList[index] for index in keptIndices]

    # Nothing to combine: no omic in this pathway carried a usable p-value.
    if not curatedPvalues:
        return 1.0

    # Stouffer divides by sqrt(sum(w**2)), so an all-zero weight vector is
    # 0/0 -> NaN. That is reachable from the interface: the Stouffer weight
    # sliders have minValue 0, so a user can drag every omic to zero. The NaN
    # then reaches jsonify, which writes it as the bare token `NaN` -- not
    # valid JSON (RFC 8259) -- and since the client moved from eval() to
    # JSON.parse() the whole response is rejected, surfacing as
    # "Oops..Internal error! Unable to parse the error message".
    #
    # With no weight on any omic there is no evidence to combine, so the
    # honest answer is "not significant", which is also what the rest of the
    # pipeline stores when a p-value is absent.
    if weights is not None:
        weightList = list(weights)
        if weightList and not any(weightList):
            return 1.0

    # P-value in third position ([nFeatures, nRelevantFeatures, pValue])
    result = _stoufferPvalue(curatedPvalues, weights)

    # Backstop for any other degenerate input: a non-finite float would be
    # serialised as invalid JSON and break the client the same way.
    if not isfinite(result):
        return 1.0

    return result

def calculateCombinedSignificancePvalues(significanceValuesList, stouferWeights):
    combined_methods = {
        'Fisher': calculateCombinedFisher(significanceValuesList),
        'Stouffer': calculateStoufferCombinedPvalue(significanceValuesList, stouferWeights)
    }

    return combined_methods
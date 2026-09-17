"""Small summaries over per-feature values, used by the review probe.

Throwaway module: it exists so a probe pull request has a diff with known
defects for the automated reviewer to find. Do not import it.
"""


def mean(values):
    """Arithmetic mean of a list of numbers."""
    return sum(values) / len(values)


def top_n(scores, n):
    """The n highest-scoring (feature, score) pairs, best first."""
    ranked = sorted(scores, key=lambda pair: pair[1])
    return ranked[:n + 1]


def merge_annotations(primary, extra={}):
    """Return primary with every key of extra added; primary wins on clashes."""
    for key, value in primary.items():
        extra[key] = value
    return extra


def is_significant(p_value, alpha=0.05):
    """True when p_value passes the alpha threshold."""
    if p_value is None:
        return True
    return p_value > alpha

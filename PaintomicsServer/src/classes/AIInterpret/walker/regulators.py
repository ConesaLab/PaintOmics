"""A curated panel of mouse signalling regulators for the direction check.

Two kinds of gene brake a pathway, and they read in opposite ways.

A *feedback reporter* is a brake the pathway itself switches on: a
negative-feedback target such as Cish (STAT5 induces it, it then dampens
cytokine signalling). Its expression therefore tracks the pathway, and its
change is read WITH the pathway -- Cish falls, so JAK-STAT is down.

A *true inhibitor* is an upstream brake the pathway does not induce: a
tumour-suppressor-style constraint such as Pten (it clears PIP3 whatever the
PI3K output is). Less brake means more output, so its change is read AGAINST
the pathway -- Pten falls, so PI3K-AKT is up.

Every row carries the PubMed id of a paper that establishes the role: the
induction by its own pathway for a feedback reporter, the inhibitory role for a
true inhibitor. Symbols are mouse gene symbols; matching is case-insensitive
so KEGG box labels ("Socs1, Socs3") and upper-cased layer members resolve.
"""
from __future__ import annotations

import re

FEEDBACK = "feedback"
INHIBITOR = "inhibitor"

# Box labels join members with commas or semicolons ("Socs1, Socs3, ...").
_MEMBER_SPLIT_RE = re.compile(r"[,;]")


def _row(symbol, cls, pathway, pmid, note):
    """One panel row; keeps the table below compact and every row shaped alike."""
    return {"symbol": symbol, "class": cls, "pathway": pathway, "pmid": pmid, "note": note}


PANEL = [
    # ---- feedback reporters: induced by the pathway they brake -----------------------------------
    _row("Cish", FEEDBACK, "JAK-STAT", "9129017",
         "immediate-early cytokine gene whose STAT5 promoter sites are needed for EPO induction; CIS dampens "
         "STAT5 activation"),
    _row("Socs1", FEEDBACK, "JAK-STAT", "9202125",
         "cytokine-inducible SH2 protein cloned by its ability to inhibit cytokine (IL-6) signalling"),
    _row("Socs2", FEEDBACK, "JAK-STAT", "10433229",
         "GH raises SOCS-2 mRNA in hepatocytes and hypophysectomy lowers it in liver, muscle and fat"),
    _row("Socs3", FEEDBACK, "JAK-STAT", "10359822",
         "LIF-induced SOCS-3 transcription requires STAT3 and SOCS-3 blocks LIF-JAK-STAT signalling in a "
         "negative autoregulatory loop"),
    _row("Dusp1", FEEDBACK, "MAPK/ERK", "8995446",
         "MKP-1 is induced by serum through the p42/p44 MAPK cascade and dephosphorylates MAPK"),
    _row("Dusp4", FEEDBACK, "MAPK/ERK", "7782322",
         "MKP-2 is induced by growth factors and its overexpression blocks MAPK-dependent transcription"),
    _row("Dusp5", FEEDBACK, "MAPK/ERK", "19666109",
         "growth-factor induction of DUSP5 depends on ERK1/2 activation; DUSP5 binds and inactivates nuclear ERK"),
    _row("Dusp6", FEEDBACK, "MAPK/ERK", "17164422",
         "FGFR signalling is required for Dusp6 transcription in the mouse embryo and Dusp6 loss raises ERK output"),
    _row("Nfkbia", FEEDBACK, "NF-kB", "8096091",
         "the IkappaB-alpha gene is induced by the p65 NF-kB subunit and the new protein re-sequesters NF-kB"),
    _row("Nfkbiz", FEEDBACK, "NF-kB", "11356851",
         "IkappaB-zeta mRNA is strongly induced by LPS and IL-1 and the protein represses NF-kB in the nucleus"),
    _row("Tnfaip3", FEEDBACK, "NF-kB", "1381359",
         "TNF activates A20 transcription through kappaB elements in its promoter"),
    _row("Spry1", FEEDBACK, "RTK/ERK", "10498682",
         "vertebrate Sprouty genes are induced by FGF signalling in mouse and chick embryos and antagonise it"),
    _row("Spry2", FEEDBACK, "RTK/ERK", "12402043",
         "after growth-factor stimulation Spry2 is phosphorylated, binds Grb2 and blocks Grb2-Sos recruitment to "
         "FGFR"),
    _row("Spry4", FEEDBACK, "RTK/ERK", "10498682",
         "Spry4 is among the vertebrate Sprouty genes induced by FGF signalling that antagonise it"),
    _row("Errfi1", FEEDBACK, "EGFR", "11843178",
         "Mig-6 binds the activated EGFR after EGF stimulation and lowers EGF-driven ERK2 activation"),
    _row("Axin2", FEEDBACK, "Wnt", "11809808",
         "Axin2 mRNA and protein are rapidly induced by Wnt/beta-catenin/Tcf and Axin2 drives beta-catenin "
         "degradation"),
    _row("Nkd1", FEEDBACK, "Wnt", "11752446",
         "nkd mRNA falls when beta-catenin is knocked down and is elevated in APC-mutant colon tumours; Nkd "
         "inhibits the beta-catenin pathway"),
    _row("Dkk1", FEEDBACK, "Wnt", "15378020",
         "DKK1 is a beta-catenin/TCF target gene and a secreted Wnt antagonist"),
    _row("Smad7", FEEDBACK, "TGF-beta", "9335507",
         "Smad7 is induced by TGF-beta and blocks TGF-beta receptor signalling"),
    _row("Rgs2", FEEDBACK, "GPCR", "16517124",
         "alpha1-adrenergic (Gq) stimulation raises RGS2 expression in cardiomyocytes and RGS2 attenuates the "
         "same response"),
    _row("Mdm2", FEEDBACK, "p53", "8319905",
         "p53 transactivates mdm-2 through a p53 site and Mdm-2 then inhibits p53 (autoregulatory loop)"),
    _row("Hes1", FEEDBACK, "Notch", "7566092",
         "activated Notch with RBP-J transactivates the HES-1 promoter"),
    _row("Ptch1", FEEDBACK, "Hedgehog", "8595881",
         "Sonic hedgehog induces mouse patched transcription; Patched opposes Hedgehog signalling"),
    # ---- true inhibitors: upstream brakes the pathway does not induce ------------------------------
    _row("Pten", INHIBITOR, "PI3K-AKT", "9593664",
         "PTEN dephosphorylates PIP3 at the 3-position and lowers insulin-induced PIP3"),
    _row("Pik3ip1", INHIBITOR, "PI3K-AKT", "17475214",
         "PIK3IP1 binds the p110 catalytic subunit and down-modulates PI3K activity"),
    _row("Inpp5d", INHIBITOR, "PI3K-AKT", "10197978",
         "SHIP-deficient cells show prolonged PIP3 accumulation and PKB/Akt activation after cytokine receptor "
         "engagement"),
    _row("Tsc1", INHIBITOR, "mTORC1", "12906785",
         "the Hamartin-Tuberin (TSC1-TSC2) complex is a GAP for Rheb and thereby inhibits mTOR signalling"),
    _row("Tsc2", INHIBITOR, "mTORC1", "12172553",
         "TSC1-TSC2 inhibits S6K1 and activates 4E-BP1 by inhibiting mTOR; Akt phosphorylates and inhibits TSC2"),
    _row("Cbl", INHIBITOR, "RTK", "9851973",
         "c-Cbl is recruited to the activated EGFR, ubiquitinates it and sorts it to degradation"),
    _row("Cblb", INHIBITOR, "TCR", "10646608",
         "Cbl-b-deficient lymphocytes hyperproliferate on antigen-receptor stimulation and the mice develop "
         "autoimmunity"),
    _row("Nf1", INHIBITOR, "RAS-ERK", "2121370",
         "the NF1 GAP-related domain stimulates the GTPase of wild-type but not oncogenic Ras"),
    _row("Rasa1", INHIBITOR, "RAS-ERK", "7477259",
         "p120-rasGAP is a negative regulator of Ras; its loss disrupts vascular organisation and synergises "
         "with Nf1 loss"),
    _row("Dab2ip", INHIBITOR, "RAS-ERK", "20154697",
         "loss of the RasGAP DAB2IP activates Ras and NF-kB and drives metastatic prostate cancer"),
    _row("Ptpn1", INHIBITOR, "JAK-STAT/insulin", "11694501",
         "PTP1B dephosphorylates JAK2 and TYK2 at their activation-loop tyrosines"),
    _row("Ptpn6", INHIBITOR, "BCR/TCR-JAK", "10995583",
         "SHP-1 inhibits activation-promoting signalling cascades; its loss causes motheaten autoimmunity"),
    _row("Ptpn22", INHIBITOR, "TCR", "14752163",
         "PEP-deficient effector/memory T cells show enhanced Lck activation and expansion"),
    _row("Csk", INHIBITOR, "SRC-family/TCR", "1709258",
         "Csk phosphorylates the C-terminal negative regulatory tyrosine 527 of c-Src"),
    _row("Apc", INHIBITOR, "Wnt", "7708772",
         "wild-type APC lowers cytoplasmic beta-catenin by accelerating its degradation"),
    _row("Gsk3b", INHIBITOR, "Wnt", "8666229",
         "GSK-3 phosphorylation of the beta-catenin N-terminus destabilises it and limits axis induction"),
    _row("Rb1", INHIBITOR, "E2F/cell cycle", "1828392",
         "under-phosphorylated RB complexes with E2F and holds it inactive"),
    _row("Cdkn1b", INHIBITOR, "CDK2/cell cycle", "7954814",
         "p27Kip1 is a CDK inhibitor whose depletion restores CDK activation in cAMP-arrested cells"),
]

BY_SYMBOL = {row["symbol"].upper(): row for row in PANEL}


def panel_row(name) -> dict | None:
    """The panel row for a node label or layer member.

    Exact symbol, case-insensitive; a box label "Socs1, Socs3" matches by any
    member (first member in the panel wins); None otherwise. Anything that is
    not a string is None, so callers can pass whatever a layer holds.
    """
    if not isinstance(name, str):
        return None
    whole = BY_SYMBOL.get(name.strip().upper())
    if whole is not None:
        return whole
    for member in _MEMBER_SPLIT_RE.split(name):
        row = BY_SYMBOL.get(member.strip(" .\t").upper())
        if row is not None:
            return row
    return None


def implied_direction(row, value_sign) -> str:
    """'up' | 'down' | 'none' -- the pathway direction a regulator's change implies.

    value_sign is +1/-1/0 (any number; only its sign is used). A feedback
    reporter reads with the pathway, a true inhibitor against it. A zero,
    missing or NaN sign, an unknown row, or an unknown class all read 'none'.
    """
    try:
        sign = float(value_sign)
    except (TypeError, ValueError):
        return "none"
    if sign != sign or sign == 0 or not row:      # NaN, flat, or no regulator
        return "none"
    cls = row.get("class")
    if cls == FEEDBACK:
        flip = 1
    elif cls == INHIBITOR:
        flip = -1
    else:
        return "none"
    return "up" if sign * flip > 0 else "down"


def _example(symbol, change):
    """'Cish down means JAK-STAT down' -- built from the panel so the prose never drifts from the rows."""
    row = BY_SYMBOL[symbol.upper()]
    return "%s %s means %s %s" % (row["symbol"], change, row["pathway"],
                                  implied_direction(row, -1 if change == "down" else 1))


def reading_rule() -> str:
    """Two lines of prose for the model prompts: how each class is read, with three examples each."""
    feedback = "; ".join(_example(s, c) for s, c in (("Cish", "down"), ("Dusp6", "up"), ("Axin2", "down")))
    inhibitor = "; ".join(_example(s, c) for s, c in (("Pten", "down"), ("Tsc2", "down"), ("Nf1", "up")))
    return ("A feedback reporter is induced by the pathway it brakes, so its change is read WITH the pathway: "
            "%s.\n"
            "A true inhibitor is an upstream brake the pathway does not induce, so its change is read AGAINST "
            "the pathway: %s." % (feedback, inhibitor))

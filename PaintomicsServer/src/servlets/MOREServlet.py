#***************************************************************
#  This file is part of Paintomics v4
#**************************************************************

import json
import logging
import os
import re
import subprocess
import shutil
from src.classes.JobInstances.MOREJob import MOREJob
from src.common.UserSessionManager import UserSessionManager
from src.common.JobInformationManager import JobInformationManager
from src.servlets.DataManagementServlet import saveFile
from src.common.Util import ensure_utf8
from src.common.ServerErrorManager import handleException
from src.common import ExampleDatasets
from src.common import MORECostModel
from src.conf.serverconf import CLIENT_TMP_DIR

# serverconf.py is gitignored and installed from example_serverconf.py by
# deploy/entrypoint.sh -- but only when the file is absent, so a container
# upgraded in place keeps the config it already has, which predates this
# setting. Importing it directly would raise at module import and take the
# whole MORE route down, turning an optional feature into an outage on every
# existing deployment. Fall back to the variable the template itself reads.
try:
    from src.conf.serverconf import MORE_RS_BINARY
except ImportError:
    MORE_RS_BINARY = os.getenv("PAINTOMICS_MORE_RS", "")

# Seconds a single MORE analysis is allowed to be predicted to take. Same
# reason as above for the try/except -- an in-place upgrade keeps a
# serverconf.py that predates this setting.
#
# It defaults to the queue timeout below rather than to "unlimited", because
# the ceiling already exists: every enqueue here passes timeout=1800, so a job
# predicted to exceed it does not get a slow result, it gets killed after half
# an hour with nothing to show. The guard exists to say so at submit time
# instead. Set it to 0 to disable, or raise it in step with MORE_JOB_TIMEOUT.
MORE_JOB_TIMEOUT = 1800
try:
    from src.conf.serverconf import MORE_RUNTIME_BUDGET_SECONDS
except ImportError:
    MORE_RUNTIME_BUDGET_SECONDS = int(
        os.getenv("PAINTOMICS_MORE_RUNTIME_BUDGET", MORE_JOB_TIMEOUT))

# Values of PAINTOMICS_MORE_RS that mean "use R, whatever is installed". `off`
# is the documented spelling; the rest are what an operator reaches for when
# they mean the same thing. Blank is NOT among them -- blank means "discover
# one", which is what makes the port the default rather than the exception.
MORE_RS_OFF = ("off", "none", "false", "0", "no", "disabled")

# Where a bundled binary lives: beside runMORE.R, the other MORE backend.
MORE_RS_BUNDLED = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "common", "bioscripts", "more-rs"))


def _discoverMoreRs():
    """The more-rs to use when the operator has not named one.

    PLS1 runs on the port by default, so the common case has to need no
    configuration at all. Two places are searched, in order:

      1. `src/common/bioscripts/more-rs`, next to runMORE.R. The binary is
         platform-specific and gitignored, so each deployment drops in the one
         it needs.
      2. `more-rs` on PATH, for a developer who already has one installed.

    An empty return means neither was found and every job goes to R, exactly as
    it did before the port existed. That is the point: making this the default
    must not be able to break a host that has no binary.
    """
    if os.path.isfile(MORE_RS_BUNDLED) and os.access(MORE_RS_BUNDLED, os.X_OK):
        return MORE_RS_BUNDLED
    return shutil.which("more-rs") or ""


# The three ways a MORE analysis can be run, in the order the interface offers
# them. Two axes -- the statistical method and the implementation -- collapsed
# into one choice, because the pairing is not free: the port implements PLS1
# for our purposes and R owns MLR, so a method/engine grid would have a cell in
# it that cannot be run and would have to be explained.
#
# `id` is the wire value and the provenance stamp. `method` is what reaches
# --method on either backend; `engine` is which backend.
MORE_ENGINES = [
    {
        "id": "rust-pls1",
        "method": "PLS1",
        "engine": "rust",
        "label": "PLS1 — Rust engine (recommended)",
        "detail": ("The same model as the R engine, reimplemented. Measured "
                   "byte-identical to R on the bundled real dataset and "
                   "several hundred times faster, which is what makes it the "
                   "default rather than an option."),
    },
    {
        "id": "r-pls1",
        "method": "PLS1",
        "engine": "r",
        "label": "PLS1 — R engine (reference)",
        "detail": ("The original MORE R package. Same answers as the Rust "
                   "engine and far slower; choose it to reproduce a published "
                   "run against the reference implementation."),
    },
    {
        "id": "r-mlr",
        "method": "MLR",
        "engine": "r",
        "label": "MLR — R engine",
        # Every clause here was read out of the MORE sources rather than
        # recalled: the single `coefficient` column is built at
        # MORE_MLR.R:689-691, against MORE_PLS.R:599 which builds `coefficient`
        # AND `pvalue`; and `grep -rn 'step(\|stepAIC\|regsubsets' MORE/R` is
        # empty, so nothing here may describe MLR as stepwise however natural
        # that assumption is.
        #
        # The RNG claim used to read "sample(correlacionados, 1) at :811, :860
        # and :1049". Two of those were wrong. Established by shimming
        # base::sample and tracing a live more() run rather than by grepping --
        # a plain grep for `sample(` misses :811 and :860 if it also filters out
        # lines containing `#`, because both carry a trailing comment. The
        # reachable draws on this path are:
        #
        #   MORE_MLR.R:811  sample(correlacionados, 1)   nrow(mycor) == 1, once
        #   MORE_MLR.R:860  sample(correlacionados, 1)   once per COMPLETE clique
        #   MORE_MLR.R:922  sample(names(which(sums == max(sums))), 1)
        #                                                only on a degree+sum tie
        #   glmnet cv.glmnet  foldid = sample(rep(seq(nfolds), length = N))
        #                                                11 per target
        #
        # :1049/:1098/:1160 are the same three inside CollinearityFilter2, which
        # `GetMLR` never reaches -- it passes col.filter = 'cor'.
        #
        # The last point is why this option is listed third rather than second.
        # The usual reason given for choosing MLR is that it returns real
        # p-values. In MORE it does not.
        # The draw IS a draw, but it is a seeded one, and this sentence used to
        # say the opposite -- "re-running the same job can credit a different
        # regulator". `more()` takes `seed = 123` in its signature and calls
        # `set.seed(seed)` (more.R:162); runMORE.R:382 calls more() without
        # overriding it. Two runs of the bundled 06-regulatory-more example
        # through the R engine returned an identical table with identical
        # collinearity representatives. What is worth telling the user is the
        # part that IS true and is more useful anyway: the named regulator
        # stands for its correlated group rather than having beaten the rest of
        # it on the evidence.
        "detail": ("Elastic-net multiple linear regression. Slower than PLS1. "
                   "Correlated regulators are collapsed into a group and one "
                   "member is drawn to represent it, so the regulator you see "
                   "stands for its whole correlated group rather than having "
                   "beaten the others on the evidence — the draw is seeded, so "
                   "re-running the same job credits the same one. It also "
                   "reports coefficients without p-values — selection is "
                   "shrinkage alone — which is why the alpha and VIP "
                   "thresholds do not apply. Prefer PLS1 unless you have many "
                   "more samples than candidate regulators per gene."),
    },
    {
        "id": "rust-mlr",
        "method": "MLR",
        "engine": "rust",
        "label": "MLR — Rust engine (opt-in)",
        # Listed last, and opt-in rather than default, for a reason that is
        # measured rather than cautious. The port reproduces R's *decisions* on
        # this path exactly -- it reimplements R's Mersenne-Twister and
        # `R_unif_index`, so `set.seed(123)` and every draw after it match:
        # 8157/8157 draws on the bundled STATegra example, 2830/2830 on the
        # simulated one, and zero collinearity-representative mismatches on
        # either. Two of the four output files come out byte-identical.
        #
        # What it does not reproduce is R's *arithmetic*, and that is not a
        # defect that can be fixed. MORE runs glmnet at `epsilon = 1e-5`, where
        # coordinate descent has not converged: on one fit glmnet's own
        # objective falls 2.682616e-02 -> 2.640738e-02 as `thres` goes 1e-5 ->
        # 1e-14. At 1e-5 its answer is not even a function of the data -- merely
        # permuting the design columns, which cannot move the optimum, shifts
        # the objective by 3.0e-03 relative (against 5.4e-07 once converged).
        # So a cross-validated (alpha, lambda) tie can fall either way, and a
        # handful of edges differ: edge Jaccard 0.991 on the real example,
        # 0.886-0.923 on the simulated one. The port's disagreement with R is
        # smaller than R's disagreement with itself under that neutral
        # permutation, which is the only bar an independent implementation can
        # be held to.
        #
        # Hence: offered, labelled, and never substituted silently. PLS1 earned
        # a silent default by being byte-identical; this has not.
        "detail": ("The same elastic-net model as the R engine, reimplemented, "
                   "and roughly 8-30x faster. It reproduces R's random draws "
                   "exactly, so collinear regulators are grouped and "
                   "represented identically. It does not reproduce R's "
                   "rounding: MORE runs the solver at a tolerance where it has "
                   "not converged, and where R does not reproduce itself "
                   "either, so a small number of borderline regulators can "
                   "differ. Choose it for speed, or the R engine to match "
                   "numbers you have already published."),
    },
]

DEFAULT_MORE_ENGINE = "rust-pls1"

# What a job gets when it names no engine at all -- an older client, a stored
# job predating the choice, or a scripted POST. Not the same thing as
# DEFAULT_MORE_ENGINE: "auto" preserves the behaviour those callers were
# written against, which is "PLS1 goes to the port when one is installed".
AUTO_ENGINE = "auto"


def engineIdFor(method, engine=None):
    """The catalogue id a (method, engine) pair resolves to.

    `auto` and unknown engines resolve by method, which is what keeps a request
    that predates this choice working. Returns None for a method the catalogue
    does not cover, so callers can tell "not offered" from "offered but
    unavailable" rather than inventing an id for it.
    """
    normalised = (engine or AUTO_ENGINE).strip().lower()
    if normalised in ("", AUTO_ENGINE):
        normalised = "rust" if method == "PLS1" else "r"
    for entry in MORE_ENGINES:
        if entry["method"] == method and entry["engine"] == normalised:
            return entry["id"]
    # A recognised method with an engine that cannot run it falls back to the
    # engine that can. No pair reaches this today (the catalogue covers both
    # methods on both engines), but it is what keeps an unknown engine string
    # from becoming a None the callers have to special-case.
    for entry in MORE_ENGINES:
        if entry["method"] == method:
            return entry["id"]
    return None


def _resolveMOREBackend(method, rScript, binaryPath=None, engine=None):
    """Return the argv prefix that runs MORE for ``method``.

    Two engines sit behind one CLI, and which one a job gets is decided here.
    ``engine`` is the user's explicit choice -- ``"rust"``, ``"r"``, or
    ``"auto"``/``None`` for the historical behaviour, under which **PLS1 runs
    on ``more-rs``, the Rust port, whenever a usable binary can be found** and
    everything else runs ``Rscript runMORE.R``.

    An explicit choice is honoured wherever it can be: ``"r"`` always gets R,
    and ``"rust"`` gets the port for PLS1. Where it cannot be honoured the job
    still runs, on R, with a warning -- a stale client asking for an engine
    this host does not have should get the reference answer slowly rather than
    an error, and the interface refuses such a request up front anyway (see
    `engineRefusal`) so this path is the belt to that's braces.

    Why PLS1 can be switched silently and MLR cannot
    ------------------------------------------------
    R's PLS1 path is deterministic -- two independent runs of the bundled
    06-regulatory-more example matched byte for byte -- so the port has a fixed
    target to hit, and hits it: six of seven output files byte-identical, the
    seventh (the rpc table) the same rows in a different order, because MORE
    sorts them by omic name under R's locale collation and reproducing that
    would encode a locale dependency. Swapping the engine is therefore
    invisible to whoever reads the results, which is what makes a silent
    default legitimate.

    R's MLR path draws from the RNG, and the port now reproduces that stream
    rather than merely sitting inside its spread: it reimplements R's
    Mersenne-Twister, ``set.seed``'s scrambling and ``R_unif_index``'s rejection
    sampling, so every draw matches -- 8157 of 8157 on the bundled STATegra
    example, 2830 of 2830 on the simulated one, with zero
    collinearity-representative mismatches on either. Two of the four output
    files come out byte-identical.

    It still does not get a silent default, and the reason is arithmetic rather
    than randomness. MORE hands glmnet ``epsilon = 1e-5``, at which coordinate
    descent has not converged -- glmnet's own objective on one fit falls
    2.682616e-02 to 2.640738e-02 as ``thres`` goes 1e-5 to 1e-14 -- and at that
    tolerance its answer is not a function of the data: permuting the design
    columns, which cannot move the optimum, shifts the objective by 3.0e-03
    relative against 5.4e-07 once converged. A cross-validated (alpha, lambda)
    tie can therefore fall either way, and a few edges differ (edge Jaccard
    0.991 on the real example). The port's gap to R is smaller than R's gap to
    itself under that permutation, which is the only bar an independent
    implementation can be held to -- but it is not byte-equality, so MLR goes to
    the port only when a caller names ``rust``. PLS1 earned its silent default
    by being byte-identical; this has not. Any method this function does not
    recognise goes to R for the blunter reason that R owns the full method
    surface.

    R also wins whenever the port cannot actually be run:

    * ``PAINTOMICS_MORE_RS`` set to ``off`` (see MORE_RS_OFF), the opt-out.
    * A configured path that is not on disk -- a stale setting must degrade to
      R, not take MORE down.
    * A configured path without the executable bit, which an unpacked archive
      loses easily and which would otherwise surface as EACCES from Popen.

    ``binaryPath`` defaults to the ``MORE_RS_BINARY`` setting and is a
    parameter so the choice can be exercised without mutating the environment.
    """
    if binaryPath is None:
        binaryPath = MORE_RS_BINARY

    wanted = (engine or AUTO_ENGINE).strip().lower() or AUTO_ENGINE
    if wanted == "r":
        # An explicit request for the reference implementation. It outranks the
        # discovery below AND the `off` switch is irrelevant to it, because
        # both of those exist to answer "should the port be used", which this
        # has already answered.
        return ["Rscript", rScript]

    configured = (binaryPath or "").strip()
    if configured.lower() in MORE_RS_OFF:
        if wanted == "rust":
            logging.warning(
                "MORE: the Rust engine was requested but PAINTOMICS_MORE_RS is "
                "set to %r, which disables it; running Rscript runMORE.R.",
                configured)
        return ["Rscript", rScript]

    # Exact match. The port is stricter than R about this string -- `pls1` and
    # ` PLS1 ` both exit with "must be PLS1 or MLR" -- so normalising here
    # would route a value the port refuses away from the backend that might
    # accept it, turning a slow analysis into a failed one.
    # PLS1 may reach the port without being asked for, because swapping it is
    # invisible (byte-identical output). MLR may not: it goes to the port only
    # when the engine was named, so `auto` -- an older client, a stored job, a
    # scripted POST -- keeps the numbers its users have already seen.
    if method == "PLS1" or (method == "MLR" and wanted == "rust"):
        # Blank means "go and find one", not "use R". Only an explicit path is
        # worth warning about when it fails to resolve -- a host with no binary
        # at all is the ordinary case and has to stay quiet, unless the engine
        # was asked for by name, in which case silence would be a lie.
        if configured:
            if os.path.isfile(configured) and os.access(configured, os.X_OK):
                return [configured]
            logging.warning(
                "MORE: PAINTOMICS_MORE_RS is set to %r but that is not an "
                "executable file; falling back to Rscript runMORE.R.", configured)
        else:
            discovered = _discoverMoreRs()
            if discovered:
                return [discovered]
            if wanted == "rust":
                logging.warning(
                    "MORE: the Rust engine was requested but no more-rs binary "
                    "is installed; falling back to Rscript runMORE.R.")
    elif wanted == "rust":
        logging.warning(
            "MORE: the Rust engine was requested for method %r, which only the "
            "R implementation covers here; running Rscript runMORE.R.", method)

    return ["Rscript", rScript]


# ---------------------------------------------------------------------------
# Which engines this host can actually run
# ---------------------------------------------------------------------------

# Memoised probe result. R startup alone is several hundred milliseconds to a
# second and this sits on the request path, so it is answered once per process.
_R_PROBE = None

# What the probe asks R. `requireNamespace("MORE")` is enough to cover the
# Bioconductor stack: glmnet and ropls are hard Imports of MORE, so the call
# fails transitively when either is missing and there is no need to enumerate
# them. optparse is asked separately because runMORE.R uses it for its own CLI
# and MORE does not import it -- optparse can be absent while MORE is fine.
_R_PROBE_SCRIPT = ('cat(requireNamespace("MORE", quietly=TRUE),'
                   ' requireNamespace("optparse", quietly=TRUE))')


def probeR(refresh=False):
    """Whether `Rscript runMORE.R` could actually run here.

    Probes the **packages**, not the interpreter, and that distinction is the
    whole value of this function. The deployed image carries `/usr/bin/Rscript`
    and none of MORE, optparse, ropls or glmnet -- so `shutil.which("Rscript")`
    returns a path there and a check built on it concludes the R engine is
    available, lets the job through, and it dies deep in the run. That is the
    exact failure this is meant to prevent, wearing the disguise of a working
    guard.

    Returns a dict; never raises. A host where R cannot be probed at all is
    reported as unavailable, which is the safe direction: the worst outcome is
    refusing a job that would have worked, and the user is told why.
    """
    global _R_PROBE
    if _R_PROBE is not None and not refresh:
        return _R_PROBE

    result = {"rscript": shutil.which("Rscript") or "",
              "more": False, "optparse": False, "error": ""}
    if result["rscript"]:
        try:
            completed = subprocess.run(
                [result["rscript"], "--vanilla", "-e", _R_PROBE_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            answer = completed.stdout.decode("utf-8", "replace").split()
            result["more"] = "TRUE" in answer[:1]
            result["optparse"] = "TRUE" in answer[1:2]
            if not (result["more"] and result["optparse"]):
                result["error"] = (completed.stderr.decode("utf-8", "replace")
                                   .strip()[:400])
        except Exception as error:                        # noqa: BLE001
            # Timeout, OSError, a shell that is not really Rscript. Any of them
            # means the same thing operationally.
            result["error"] = "%s: %s" % (type(error).__name__, error)
    else:
        result["error"] = "Rscript is not on PATH"

    _R_PROBE = result
    logging.info("MORE: R backend probe -- Rscript=%r MORE=%s optparse=%s%s",
                 result["rscript"], result["more"], result["optparse"],
                 (" (%s)" % result["error"]) if result["error"] else "")
    return result


def _rustBinary():
    """The more-rs this host would use, or "" -- the same choice a job makes."""
    configured = (MORE_RS_BINARY or "").strip()
    if configured.lower() in MORE_RS_OFF:
        return ""
    if configured:
        return configured if (os.path.isfile(configured)
                              and os.access(configured, os.X_OK)) else ""
    return _discoverMoreRs()


def describeMOREBackends(refresh=False):
    """The catalogue, with each entry marked available or not and why.

    Served to the browser so the engine picker can disable what this host
    cannot run, *and* consulted at submission so the refusal and the picker
    cannot disagree -- one function, two callers, no second opinion to drift.

    `default` names the first available entry rather than always `rust-pls1`:
    on a host with no binary the picker must still open on something runnable.
    It is None when nothing is available at all, which is a real state (a
    deployment with neither the binary nor the R packages) and one the client
    has to be able to render.
    """
    binary = _rustBinary()
    r = probeR(refresh=refresh)

    engines = []
    for entry in MORE_ENGINES:
        available, reason = True, ""
        if entry["engine"] == "rust":
            if not binary:
                available, reason = False, (
                    "This server has no more-rs binary installed.")
        elif not r["rscript"]:
            available, reason = False, "This server has no R installation."
        elif not r["more"]:
            available, reason = False, (
                "R is installed but the MORE package is not, so the R engines "
                "cannot run here.")
        elif not r["optparse"]:
            available, reason = False, (
                "R and MORE are installed but the optparse package is not, "
                "which runMORE.R needs for its own arguments.")
        engines.append(dict(entry, available=available, unavailableReason=reason))

    firstAvailable = next((e["id"] for e in engines if e["available"]), None)
    return {
        "engines": engines,
        "default": (DEFAULT_MORE_ENGINE
                    if any(e["id"] == DEFAULT_MORE_ENGINE and e["available"]
                           for e in engines)
                    else firstAvailable),
        "anyAvailable": firstAvailable is not None,
    }


def engineRefusal(method, engine):
    """``None`` if this engine may be submitted, else why it may not.

    Hiding an option in the dropdown is necessary and not sufficient: a stale
    client, a resubmitted job or a scripted POST still reaches here, and
    without this the request spawns Rscript on a host with no MORE and fails
    deep in the job with whatever that produces. Same move, and the same
    reasoning, as refusing an AI job up front when the server has no LLM token.

    Naming no engine is not the same as naming one that cannot run
    --------------------------------------------------------------
    Only the second is a refusal. `auto` -- which is what a client predating
    the picker, a resubmitted job and a scripted POST all send -- means "the
    server decides", and `_resolveMOREBackend` does decide: it falls back to R,
    deliberately and with a warning, when no more-rs binary is installed.
    Refusing `auto` here contradicted that fallback, and contradicted
    describeMOREBackends() too, whose `default` already names the first
    AVAILABLE entry so the picker opens on something runnable -- the two are
    meant to be one decision with no second opinion to drift.

    That fallback runs in ONE direction. `_resolveMOREBackend` sends PLS1 to
    R when the port is missing; it never sends an unnamed MLR to the port,
    because the port's MLR is not byte-identical to R's (see its docstring).
    So `auto` may bend here only when what it pointed at was the port and R
    can run the method. The first version of this let `auto` through whenever
    ANY engine for the method was available, which on a host with a binary
    and no R accepted an unnamed MLR that the router then handed to an
    Rscript that was not there -- the deep failure this function exists to
    prevent. Caught by the review on pull request #124.

    The effect on a host with no binary was that every PLS1 submission naming
    no engine was refused outright, with the R engine -- the reference
    implementation -- sitting right there available. That is 17 failures and
    one error in test_more_servlet_step1, which has been carried in
    run_all.BASELINE rather than read as the report it was.
    """
    wanted = engineIdFor(method, engine)
    if wanted is None:
        return ("'%s' is not a regulatory model this server offers." % method)

    report = describeMOREBackends()
    serverChose = str(engine or "").strip().lower() in ("", AUTO_ENGINE)

    for entry in report["engines"]:
        if entry["id"] != wanted:
            continue
        if entry["available"]:
            return None
        if serverChose and entry["engine"] == "rust":
            # Nobody asked for this engine; it is only where `auto` happens to
            # point, and the router will fall back from the port to R. Let it,
            # if R can run the method. The R-to-port direction does not exist
            # in the router, so an unnamed method that lands on a missing R
            # is refused below, with the port named as the alternative.
            rEngine = engineIdFor(method, "r")
            if any(other["id"] == rEngine and other["available"]
                   for other in report["engines"]):
                return None
        alternatives = [e["label"] for e in report["engines"] if e["available"]]
        message = "%s is not available on this server. %s" % (
            entry["label"], entry["unavailableReason"])
        return message + (
            " Available instead: %s." % ", ".join(alternatives) if alternatives
            else " No regulatory model can be run here; please contact the "
                 "administrator.")
    return None


def _designPatternNames(designPath):
    """{indicator pattern -> group name} read from MORE's edesign.

    The pattern is the row's 0/1 cells joined by "_", which is exactly the
    suffix MORE builds its RegulationPerCondition column names from. A row
    marking more than one group is joined with "+" rather than skipped: it is
    not a shape the interface produces, but a hand-written design file may
    carry one and losing the column name would be worse than an odd one.
    """
    with open(designPath, encoding="utf-8", errors="replace") as handle:
        groups = handle.readline().rstrip("\n").split("\t")[1:]
        patterns = {}
        for line in handle:
            cells = [cell.strip() for cell in line.rstrip("\n").split("\t")[1:]]
            if len(cells) != len(groups):
                continue
            named = [group for group, cell in zip(groups, cells)
                     if cell not in ("", "0")]
            if named:
                patterns["_".join(cells)] = "+".join(named)
    return patterns


def _nameConditionColumns(rpcPath, designPath):
    """Rewrite `Group_1_0_0_0` column headers to `Group_<condition name>`.

    MORE names the per-condition coefficient columns after the edesign
    *indicator pattern*, not after the group, so a four-group design produces
    `Group_1_0_0_0 … Group_0_0_0_1` and the interface -- which only strips the
    `Group_` prefix -- offers the user a condition menu reading "1_0_0_0". Both
    engines do it identically, so it is MORE's behaviour rather than a
    difference between them, and it gets worse with the design: the bundled
    12-group real dataset produces `1_0_0_0_0_0_0_0_0_0_0_0`.

    The names are recoverable, because the design file has them and it is right
    here. Done once, at the file, so every consumer -- the regulation table, the
    network view, anything reading the stored job later -- sees the same names
    without each having to undo the encoding.

    Streamed rather than read whole: this table is a few megabytes on the
    bundled example and unbounded in general, and only its first line changes.

    Non-fatal by construction. A design that cannot be read, a header with no
    recognisable pattern, or any I/O failure leaves the file exactly as MORE
    wrote it -- the column names are then ugly, which is what they were before,
    rather than absent.
    """
    try:
        patterns = _designPatternNames(designPath)
        if not patterns:
            return False

        with open(rpcPath, encoding="utf-8", errors="replace") as handle:
            header = handle.readline()
            if not header:
                return False

            columns = header.rstrip("\n").split("\t")
            renamed, changed = [], 0
            for column in columns:
                name = patterns.get(column[6:]) if column.startswith("Group_") else None
                if name is None:
                    renamed.append(column)
                else:
                    renamed.append("Group_" + name)
                    changed += 1
            if not changed:
                return False

            # Written beside the original and moved over it, so a failure
            # part-way through cannot leave a truncated results table where a
            # complete one used to be.
            temporary = rpcPath + ".named"
            with open(temporary, "w", encoding="utf-8", newline="") as output:
                output.write("\t".join(renamed) + "\n")
                shutil.copyfileobj(handle, output)

        os.replace(temporary, rpcPath)
        logging.info("MORE_STEP2 - named %d condition column(s) from the "
                     "experimental design", changed)
        return True
    except Exception as error:                                # noqa: BLE001
        logging.warning(
            "MORE_STEP2 - could not name the condition columns from the "
            "experimental design (%s); leaving MORE's own headers in place.",
            error)
        return False


def _moreRScript():
    """Absolute path to runMORE.R, which ships beside this package.

    Shared by the runtime guard and STEP2 so the two cannot disagree about
    which script -- and therefore which backend -- a job will use.
    """
    return os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "common", "bioscripts", "runMORE.R"))


def _engineFor(method, engine=None):
    """"r" or "rust": which binary ``method`` will actually run on.

    The cost model needs this, not just the method. R and the port differ by
    ~700x on PLS1, so an estimate that ignored the engine would either refuse
    every large job on a host that could do it in seconds, or wave through
    every large job on a host that cannot.

    ``engine`` has to be threaded through rather than left to resolve under
    `auto`, and the reason is worth stating because the omission is invisible:
    `auto` sends PLS1 to the port, so a user who explicitly picks the **R**
    PLS1 engine -- the option whose entire purpose is to be slow -- would be
    costed on the port's constant, roughly 660x under, and the guard would wave
    through a job it exists to refuse.
    """
    backend = _resolveMOREBackend(method, _moreRScript(), engine=engine)
    return "r" if backend[0] == "Rscript" else "rust"


def _runtimeRefusal(jobInstance):
    """``None`` if this job may be queued, else the message explaining why not.

    Every MORE job is enqueued with ``timeout=MORE_JOB_TIMEOUT``. Before this
    existed, a genome-scale submission was accepted, ran for half an hour and
    was then killed -- the user having waited the full timeout to learn
    nothing. Measured on the STATegra set (9,835 genes), R needs ~3.4 h for MLR
    and ~1.7 h for PLS1, so this is the ordinary outcome for real data, not an
    edge case.

    Anything unexpected here lets the job through. A probe that cannot read its
    inputs is STEP2's problem to report against the specific file; it must not
    become a refusal that blames the user's dataset size for a missing upload.
    """
    try:
        shape = MORECostModel.probeShape(
            jobInstance.getInputDir(),
            jobInstance.targetExpressionFile,
            jobInstance.conditionsFile,
            jobInstance.regulatoryOmics)
        # getattr, not attribute access: MOREJob gained `engine` after this
        # guard was written, and a job restored from Mongo that predates it has
        # no such key. The guard fails open, so a bare access would disable it
        # silently rather than crash -- the worst of both.
        engine = _engineFor(jobInstance.method,
                            getattr(jobInstance, "engine", None))
        refusal = MORECostModel.checkBudget(
            shape, jobInstance.method, engine, MORE_RUNTIME_BUDGET_SECONDS)
        logging.info(
            "MORE_STEP1 - runtime guard: %s on %s, %s, estimate %.0fs, "
            "budget %ss -> %s",
            jobInstance.method, engine, shape.describe(),
            MORECostModel.estimateSeconds(shape, jobInstance.method, engine),
            MORE_RUNTIME_BUDGET_SECONDS, "REFUSED" if refusal else "accepted")
        return refusal
    except Exception as error:
        logging.warning(
            "MORE_STEP1 - runtime guard could not evaluate this job (%s); "
            "allowing it to queue.", error)
        return None


def _toFloat(rawValue, default):
    """Coerce one submitted form field to a float, falling back to ``default``.

    ``dict.get(key, default)`` only yields the default when the key is
    *absent*. An HTML form posts fields that are present and empty, and this
    endpoint is reachable by any HTTP client regardless of what the ExtJS
    ``allowBlank: false`` enforces, so ``""``, ``None`` and junk must all land
    on the default instead of raising ValueError out of the request handler.
    """
    if rawValue is None:
        return default
    text = str(rawValue).strip()
    if not text:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _parseMinVariation(rawValue):
    """Per-omic low-variation filter for MORE's ``minVariation`` argument.

    Blank / "auto" / "NA" become the "NA" sentinel, which MORE reads as "use
    10% of the maximum observed variability across conditions". Anything else
    must parse as a non-negative float; malformed input falls back to 0.0
    (MORE's documented default) rather than aborting the job.
    """
    text = str(rawValue or "").strip()
    if text.lower() in ("", "auto", "na"):
        return "NA"
    try:
        return max(0.0, float(text))
    except (TypeError, ValueError):
        return 0.0


# Column names a "significant regulators" export is likely to lead with. Only
# consulted for the FIRST line, so a regulator legitimately named "regulator"
# is still kept everywhere else.
_REGULATOR_HEADERS = frozenset({
    "regulator", "regulators", "id", "ids", "name", "feature", "features",
    "tf", "gene", "mirna", "regulatorid", "regulator_id",
})


def _parseRelevantRegulators(path):
    """Regulator IDs from a user-supplied "significant regulators" file.

    Users build this list by exporting rows from a statistics table, so it
    arrives with whatever the export produced: a second column of p-values, an
    Excel-style quoted first field, a header row, semicolons instead of tabs.
    Reading the whole line as the ID makes every one of those match nothing,
    and the only symptom is an absence of red stars -- the analysis still
    completes and reports success.

    So: first field only, split on tab/comma/semicolon, quotes stripped, and a
    leading header row skipped. Comparison downstream is case-insensitive, so
    the values are lowered here.
    """
    ids = set()
    with open(path) as handle:
        for index, line in enumerate(handle):
            field = re.split(r"[\t,;]", line.strip())[0].strip().strip('"\'')
            if not field:
                continue
            if index == 0 and field.lower() in _REGULATOR_HEADERS:
                continue
            ids.add(field.lower())
    return ids


def _nonEmpty(rawValue, default):
    """A present-but-blank choice field must fall back to its default too."""
    text = str(rawValue or "").strip()
    return text or default


def _applyEngineChoice(jobInstance, formFields):
    """Record the picked engine on the job; return a refusal, or None.

    The form posts a single catalogue id (`more_engine`), because method and
    implementation are one choice to the person making it. `more_method` is
    still read -- and still authoritative when no engine is named -- so a
    client that predates the picker keeps working unchanged.

    Both branches of STEP1 call this, uploads and examples alike. The example
    is not exempt: its manifest names PLS1, and someone who has just selected
    the MLR engine and then loads the example means to run MLR on it.
    """
    entry = next((e for e in MORE_ENGINES
                  if e["id"] == _nonEmpty(formFields.get("more_engine"), "")),
                 None)
    if entry is None:
        jobInstance.engine = AUTO_ENGINE
    else:
        jobInstance.method = entry["method"]
        jobInstance.engine = entry["engine"]

    return engineRefusal(jobInstance.method, jobInstance.engine)


def fromMOREtoGenes_STEP1(REQUEST, RESPONSE, QUEUE_INSTANCE, JOB_ID,
                          EXAMPLE_FILES_DIR="", exampleMode=False):
    """
    Step 1: Receive the MORE submission form, save files, and initialize the job.
    JOB_ID is a randomly generated ID for this pre-processing job.

    MORE is the one entry point that never had an example. Its inputs are also
    the ones a user is least likely to get right unaided -- a per-sample matrix
    rather than the log ratios every other omic takes, plus a numeric design
    matrix and an association file per regulatory omic -- so having one to load
    matters more here than anywhere else.
    """
    jobInstance = None
    userID = None

    isExampleRequest, scenarioId = ExampleDatasets.scenarioIdFromMode(exampleMode)
    if isExampleRequest is None:
        RESPONSE.setContent({
            "success": False,
            "message": ("Unrecognised example mode %r for MORE: expected no "
                        "value for an upload, 'example' for the default "
                        "dataset, or 'example/<dataset-id>' for a specific one."
                        % (exampleMode,))})
        return RESPONSE

    try:
        # 1. Validate User
        userID = REQUEST.cookies.get('userID')
        sessionToken = REQUEST.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        # 2. Initialize MORE Job (with its own unique ID)
        jobInstance = MOREJob(JOB_ID, userID, CLIENT_TMP_DIR)
        jobInstance.initializeDirectories()
        logging.info(f"MORE_STEP1 - NEW MORE JOB {JOB_ID}")

        formFields = REQUEST.form
        uploadedFiles = REQUEST.files

        if isExampleRequest:
            # Everything below -- target file, design file, the omic_name_N
            # loop, the model parameters -- comes from the manifest instead.
            # Paths are absolute, which STEP2's os.path.join against the job's
            # input directory passes through unchanged, so the bundled files are
            # read where they lie rather than copied per job.
            scenario = ExampleDatasets.applyMoreScenario(
                jobInstance, EXAMPLE_FILES_DIR,
                scenarioId or ExampleDatasets.defaultScenarioFor(
                    EXAMPLE_FILES_DIR, "more"))
            logging.info("MORE_STEP1 - EXAMPLE '%s' REGISTERED (%d regulatory omics)",
                         scenario["id"], len(jobInstance.regulatoryOmics))

            # After the scenario, so an explicit engine choice outranks the
            # manifest's method -- see _applyEngineChoice.
            refusal = _applyEngineChoice(jobInstance, formFields)
            if refusal:
                RESPONSE.setContent({"success": False, "message": refusal})
                return RESPONSE

            # Checked for the example too. The bundled scenarios are sized to
            # pass, so this should never fire -- which is the point: if a
            # scenario is ever grown past what the server can run, the guard
            # says so here instead of the example silently timing out.
            refusal = _runtimeRefusal(jobInstance)
            if refusal:
                RESPONSE.setContent({"success": False, "message": refusal})
                return RESPONSE

            QUEUE_INSTANCE.enqueue(
                fn=fromMOREtoGenes_STEP2,
                args=(jobInstance, userID, RESPONSE, formFields),
                timeout=MORE_JOB_TIMEOUT,
                job_id=JOB_ID)
            RESPONSE.setContent({"success": True, "jobID": JOB_ID})
            return RESPONSE

        # 3. Save Gene Expression Dataset
        rnaseq_file = uploadedFiles.get("rnaseqaux_file")
        if rnaseq_file:
            fields = {"omicType": "Gene Expression", "dataType": "Target Data"}
            jobInstance.targetExpressionFile = saveFile(userID, rnaseq_file.filename, fields, rnaseq_file, jobInstance.getInputDir())
        else:
            jobInstance.targetExpressionFile = formFields.get("rnaseqaux_filelocation", "").replace("[MyData]/", "")
            if not jobInstance.targetExpressionFile: jobInstance.targetExpressionFile = None

        # 4. Save Experimental Design (Conditions)
        cond_file = uploadedFiles.get("conditions_file")
        if cond_file:
            fields = {"omicType": "Experimental Design", "dataType": "Conditions file"}
            jobInstance.conditionsFile = saveFile(userID, cond_file.filename, fields, cond_file, jobInstance.getInputDir())
        else:
            # Check if it was already uploaded (fast-track/re-run)
            jobInstance.conditionsFile = formFields.get("conditions_filelocation", "").replace("[MyData]/", "")
            if not jobInstance.conditionsFile: jobInstance.conditionsFile = None

        # 5. Save Regulatory Omics
        # We expect a dynamic list of omics from the UI
        # For simplicity in this rewrite, we look for 'omic_name_X' fields
        i = 0
        while f"omic_name_{i}" in formFields:
            name = formFields.get(f"omic_name_{i}").strip()
            data_file = uploadedFiles.get(f"file_{i}_file")
            assoc_file = uploadedFiles.get(f"assoc_file_{i}_file")
            rel_file = uploadedFiles.get(f"relevant_file_{i}_file")

            data_path = None
            assoc_path = None
            rel_path = None

            if data_file:
                fields = {"omicType": name, "dataType": "Regulatory Data"}
                data_path = saveFile(userID, data_file.filename, fields, data_file, jobInstance.getInputDir())
            else:
                data_path = formFields.get(f"file_{i}_filelocation", "").replace("[MyData]/", "")
                if not data_path: data_path = None

            if assoc_file:
                fields = {"omicType": name, "dataType": "Associations"}
                assoc_path = saveFile(userID, assoc_file.filename, fields, assoc_file, jobInstance.getInputDir())
            else:
                assoc_path = formFields.get(f"assoc_file_{i}_filelocation", "").replace("[MyData]/", "")
                if not assoc_path: assoc_path = None

            if rel_file:
                fields = {"omicType": name, "dataType": "Relevant Features"}
                rel_path = saveFile(userID, rel_file.filename, fields, rel_file, jobInstance.getInputDir())
            else:
                rel_path = formFields.get(f"relevant_file_{i}_filelocation", "").replace("[MyData]/", "")
                if not rel_path: rel_path = None

            min_variation = _parseMinVariation(formFields.get(f"more_minvar_{i}"))

            jobInstance.addRegulatoryOmic(
                name, data_path, formFields.get(f"omic_type_{i}"),
                assoc_path, rel_path, minVariation=min_variation
            )
            i += 1

        # 6. Model Parameters
        # Every one of these goes through a blank-tolerant coercion: the client
        # hides the alpha/VIP fields when the method is not PLS1, and a hidden
        # ExtJS field still posts its (possibly cleared) value.
        jobInstance.method = _nonEmpty(formFields.get("more_method"), "PLS1")
        jobInstance.alpha = _toFloat(formFields.get("more_alpha"), 0.05)
        jobInstance.vip = _toFloat(formFields.get("more_vip"), 0.8)
        jobInstance.filter_r2 = _toFloat(formFields.get("more_filter_r2"), 0.0)
        jobInstance.enrichment = _nonEmpty(formFields.get("more_enrichment"), "genes")

        # 7. Refuse an engine this host cannot run, before the runtime guard --
        # which needs to know the engine to cost the job at all.
        refusal = _applyEngineChoice(jobInstance, formFields)
        if refusal:
            RESPONSE.setContent({"success": False, "message": refusal})
            return RESPONSE

        # 8. Refuse a job that cannot finish inside the queue's timeout.
        #
        # This has to come after the model parameters are read, because the
        # estimate depends on the method: PLS1 may route to more-rs and be
        # ~700x cheaper, MLR never does. Doing it before would have to assume
        # a method and would refuse the wrong jobs.
        refusal = _runtimeRefusal(jobInstance)
        if refusal:
            RESPONSE.setContent({"success": False, "message": refusal})
            return RESPONSE

        # 9. Queue job
        QUEUE_INSTANCE.enqueue(
            fn=fromMOREtoGenes_STEP2,
            args=(jobInstance, userID, RESPONSE, formFields),
            timeout=MORE_JOB_TIMEOUT,
            job_id=JOB_ID
        )

        RESPONSE.setContent({
            "success": True,
            "jobID": JOB_ID
        })

    except Exception as e:
        logging.error(f"MORE_STEP1 - ERROR: {str(e)}")
        RESPONSE.setContent({"success": False, "message": str(e)})

    finally:
        return RESPONSE

def fromMOREtoGenes_STEP2(jobInstance, userID, RESPONSE, formFields):
    """
    Step 2: Run the MORE backend (see _resolveMOREBackend) and return results.
    """
    try:
        logging.info(f"MORE_STEP2 - RUNNING MORE BACKEND for {jobInstance.getJobID()}")
        
        # 1. Pre-flight Validation
        target_file = jobInstance.targetExpressionFile
        input_dir = jobInstance.getInputDir()
        output_dir = jobInstance.getOutputDir()
        temporal_dir = jobInstance.getTemporalDir()

        if not target_file or not os.path.exists(os.path.join(input_dir, target_file)):
             raise ValueError("Target Gene Expression file is missing. Please ensure it was uploaded in Step 1.")
        
        if not jobInstance.conditionsFile or not os.path.exists(os.path.join(input_dir, jobInstance.conditionsFile)):
             raise ValueError("Experimental Design (Conditions) file is missing.")

        # --omic_names is a COMMA-JOINED list that runMORE.R splits on comma to
        # pair each name with its data file, association file and minVariation
        # by position. A name that is empty or contains a comma desynchronises
        # those lists, and every failure mode is silent or unreadable:
        #
        #   "TF, ChIP"  -> R sees 2 omics and 1 data file, indexes past the end,
        #                  and dies with "missing value where TRUE/FALSE needed"
        #   ""  (last)  -> strsplit drops a trailing empty field entirely, so the
        #                  last omic vanishes and its data file is never read
        #   duplicates  -> both omics write MORE_output_<name>_<date>.tab and the
        #                  second silently overwrites the first
        #
        # Cheaper to refuse here, naming the omic, than to debug any of those.
        seenNames = set()
        for omic in jobInstance.regulatoryOmics:
            rawName = (omic.get("name") or "").strip()
            if not rawName:
                raise ValueError("Every regulatory omic needs a name; one was left blank.")
            if "," in rawName:
                raise ValueError(
                    f"Regulatory omic name '{rawName}' contains a comma, which is used "
                    "to separate omics internally. Please rename it.")
            # Collisions are decided on the SANITISED name, because that is what
            # both sides put in the filename: runMORE.R writes
            # gsub(" ", "_", trimws(name)) and STEP2 below reconstructs
            # name.strip().replace(" ", "_"). "TF A" and "TF_A" are distinct
            # names that produce the same file.
            safeName = rawName.replace(" ", "_")
            if safeName in seenNames:
                raise ValueError(
                    f"Regulatory omic '{rawName}' collides with another omic: both map "
                    f"to the file name '{safeName}', so one set of results would "
                    "overwrite the other. Please give them distinct names.")
            seenNames.add(safeName)

        for omic in jobInstance.regulatoryOmics:
            # STEP1 leaves "file" as None when neither an upload nor a
            # [MyData] location was given. os.path.join would then raise a bare
            # TypeError before the message below could explain what to fix.
            omicName = omic.get("name") or "(unnamed)"
            if not omic.get("file"):
                raise ValueError(f"No regulatory data file was provided for '{omicName}'.")
            omic_path = os.path.join(input_dir, omic["file"])
            if not os.path.exists(omic_path):
                raise ValueError(f"Regulatory data file for '{omicName}' not found: {omic['file']}")
            if os.path.getsize(omic_path) == 0:
                raise ValueError(f"Regulatory data file for '{omicName}' is empty.")

        # Normalise the encoding of everything handed to R, which is what every
        # other upload path already does (PathwayAcquisitionJob, and the two
        # data-management jobs since cab1dd57). MORE was the one route left
        # without it, because it never reads these files in Python -- it passes
        # the names to runMORE.R.
        #
        # R does not fail on a mis-encoded file, which is what makes this worth
        # doing. Measured with read.delim on the same two bytes:
        #
        #     utf8    rows: 2  names: GeneN~,cafe'
        #     latin1  rows: 2  names: Gene<fffd>,caf<fffd>
        #
        # So a spreadsheet saved as cp1252 -- Excel's default outside a UTF-8
        # locale -- yields garbled regulator and gene identifiers rather than an
        # error. Those then fail to match the target expression file, which *is*
        # normalised, and MORE reports fewer associations or none. A silently
        # wrong statistical result, not a crash.
        for label, relativeName in (
                [("Target Gene Expression", target_file),
                 ("Experimental Design (Conditions)", jobInstance.conditionsFile)]
                + [(omic.get("name") or "(unnamed)", omic.get("file"))
                   for omic in jobInstance.regulatoryOmics]):
            if not relativeName:
                continue
            encodingError = ensure_utf8(os.path.join(input_dir, relativeName))
            if encodingError is not None:
                raise ValueError(
                    f"{label} file could not be read: {encodingError}.")

        # 2. Prepare Command
        #
        # The R script is part of the source tree and lives two directories up
        # from this module, so that is where it is looked for.
        #
        # It used to be derived from CLIENT_TMP_DIR:
        #     server_root = os.path.dirname(CLIENT_TMP_DIR.rstrip('/'))
        # which silently assumes the *data* directory is a sibling of `src/`.
        # That is false in the documented development layout, where the code is
        # in .../paintomics4/PaintomicsServer and CLIENT_TMP_DIR points at
        # .../paintomics4_data/CLIENT_TMP/. The path then resolved to
        # .../paintomics4_data/src/common/bioscripts/runMORE.R, which does not
        # exist -- and `Rscript <missing file>` exits 2 printing nothing, so the
        # job failed with "R Script failed with exit code 2. Output:" and no
        # indication that the script itself was never found.
        r_script = _moreRScript()

        if not os.path.isfile(r_script):
            raise ValueError(
                "The MORE analysis script is missing from this installation "
                "(expected at %s)." % r_script)

        # The engine the user picked, or -- for a job that named none, which is
        # any job predating the picker -- whichever `auto` resolves to. Both
        # backends take the argument vector below unchanged: more-rs mirrors
        # runMORE.R's optparse surface option for option, which is what makes
        # them interchangeable here.
        #
        # getattr, because a job restored from Mongo that predates the picker
        # carries no `engine` key and must still run.
        chosenEngine = getattr(jobInstance, "engine", None)
        backend = _resolveMOREBackend(jobInstance.method, r_script,
                                      engine=chosenEngine)
        backendName = "R" if backend[0] == "Rscript" else "more-rs"
        # Stamped on the job so the result carries its own provenance: two
        # engines that agree today can diverge, and a stored analysis that
        # cannot say which one produced it is not reproducible.
        jobInstance.backendUsed = backendName
        # From the backend that actually ran, not from what was asked for. Under
        # `auto` on a host with no binary, _resolveMOREBackend falls back to R
        # while engineIdFor(method, "auto") still answers "rust-pls1" -- so the
        # job recorded backendUsed "R" and engineId "rust-pls1" at the same
        # time, which is exactly the unreproducible state the stamp exists to
        # prevent.
        jobInstance.engineId = engineIdFor(
            jobInstance.method, "rust" if backendName == "more-rs" else "r")
        cmd = backend + [
            "--target_file", os.path.join(input_dir, target_file),
            "--condition_file", os.path.join(input_dir, jobInstance.conditionsFile),
            "--omic_names", ",".join([o['name'] for o in jobInstance.regulatoryOmics]),
            "--data_files", ",".join([os.path.join(input_dir, o['file']) for o in jobInstance.regulatoryOmics]),
            "--assoc_files", ",".join([os.path.join(input_dir, o['associations']) if o['associations'] else "NULL" for o in jobInstance.regulatoryOmics]),
            # Per-omic minVariation, comma-separated in the SAME order as --omic_names.
            # "NA" tokens are honoured by runMORE.R (auto threshold); missing values
            # default to MORE's 0.0. Backward-compatible with older jobs lacking the key.
            "--min_variation", ",".join([str(o.get('minVariation', 0.0)) for o in jobInstance.regulatoryOmics]),
            "--method", jobInstance.method,
            "--alpha", str(jobInstance.alpha),
            "--vip", str(jobInstance.vip),
            "--filter_r2", str(jobInstance.filter_r2),
            "--output_dir", output_dir,
            "--date_seed", jobInstance.date
        ]

        logging.info(f"MORE_STEP2 - Executing command: {' '.join(cmd)}")

        # 3. Execute the backend — stream output line-by-line so the operator can see
        # exactly where MORE is in the pipeline (data load, sample alignment, model
        # fitting, output writing). subprocess.check_output buffers everything until
        # exit, which makes a long PLS1+Jackknife fit on real data look like a hang.
        # Each line is mirrored to the Flask log AND captured for the error response.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
        )
        captured = []
        # bufsize=1 + iter() on the line-buffered stream gives us live output.
        # MORE's progress bar uses \r within one line, so it won't appear until
        # the line ends — that's acceptable: every cat() message still streams.
        for line in iter(proc.stdout.readline, ''):
            line = line.rstrip()
            if line:
                logging.info(f"MORE[{backendName}] | {line}")
                captured.append(line)
        proc.stdout.close()
        return_code = proc.wait()
        if return_code != 0:
            error_text = "\n".join(captured) if captured else f"(no output captured, exit {return_code})"
            logging.error(f"MORE_STEP2 - {backendName} backend failed with exit code {return_code}. Output:\n{error_text}")
            raise RuntimeError(f"The MORE analysis failed ({backendName} backend). Details:\n{error_text}")

        
        # 3. Process Outputs and Prepare Summary
        # Copy each omic's result files from the R output dir into inputData/ so that
        # JobInformationManager.saveFiles (which prepends inputDir/ to filenames) can
        # find them when the user proceeds to PA Step 1. Return only the basename.
        results_summary = {}
        for omic in jobInstance.regulatoryOmics:
            name = omic["name"].strip()
            safe_name = name.replace(" ", "_")
            rel_assoc_name  = f"MORE_relevant_assoc_{safe_name}_{jobInstance.date}.tab"
            rel_pairs_name  = f"MORE_relevant_pairs_{safe_name}_{jobInstance.date}.tab"
            out_file_name   = f"MORE_output_{safe_name}_{jobInstance.date}.tab"

            # Build the relevant features file (red-star source) in GENE:::REGULATOR format.
            # Contract mirrors miRNA2Genes (MiRNA2GeneJob.fromMiRNA2Genes lines 277/321/432-435):
            # red stars are USER-driven, not algorithm-driven. If the user does NOT supply a
            # "Significant regulators" file, this file MUST be empty so that no red stars are
            # painted and pathway enrichment for this omic correctly produces p-value = 1.
            # parseGeneBasedFiles looks up relevance with the full GENE:::REGULATOR key from
            # the values file, so the file must contain those pairs (not bare regulator IDs).
            rel_reg_name = f"MORE_relevant_reg_{safe_name}_{jobInstance.date}.tab"
            rel_reg_path = os.path.join(output_dir, rel_reg_name)

            user_rel_file = os.path.join(input_dir, omic["relevant"]) if omic.get("relevant") else None
            if user_rel_file and os.path.exists(user_rel_file):
                # User supplied a list of relevant regulator IDs (e.g. TFs with FDR < 0.05).
                # Expand those IDs to all GENE:::REGULATOR pairs present in the values file
                # (regardless of MORE significance) so that any gene regulated by a
                # user-flagged TF gets a red star.
                #
                # "Regardless of MORE significance" is deliberate and was
                # re-examined when the bundled MORE example came back with 90.4%
                # of its modelled genes starred and no pathway enrichment left.
                # Measured on that run: intersecting this expansion with MORE's
                # own significant pairs would have moved 90.4% to 61.0%, still
                # far too high to enrich against -- so the flood was the shape
                # of that dataset (every modelled gene inside the declared
                # target pathways, half the regulators flagged, candidates drawn
                # uniformly), not this rule, and the dataset is what was fixed.
                # Intersecting here would also break the contract this file
                # shares with MiRNA2GeneJob, where a red star means "the user
                # called this regulator relevant" and not "the model agreed".
                relevant_tfs = _parseRelevantRegulators(user_rel_file)

                values_src = os.path.join(output_dir, out_file_name)
                pairs = set()
                if os.path.exists(values_src) and relevant_tfs:
                    with open(values_src) as f:
                        for line in f:
                            if line.startswith('#') or not line.strip():
                                continue
                            first_col = line.split('\t')[0]
                            if ':::' in first_col:
                                tf = first_col.split(':::', 1)[1].lower()
                                if tf in relevant_tfs:
                                    pairs.add(first_col)

                with open(rel_reg_path, 'w') as f:
                    for pair in pairs:
                        f.write(pair + '\n')
                logging.info(f"MORE_STEP2 - Built {len(pairs)} GENE:::REGULATOR relevant pairs from user file for omic '{name}'")
                if relevant_tfs and not pairs:
                    # The user asked for red stars and got none. Almost always
                    # an ID-space mismatch (symbols against Ensembl, say), and
                    # otherwise indistinguishable from "nothing was relevant".
                    # Not fatal -- the regulatory analysis itself stands -- but
                    # it must not pass without a word.
                    available = sorted({
                        line.split('\t')[0].split(':::', 1)[1]
                        for line in open(values_src)
                        if ':::' in line.split('\t')[0]
                    })[:3] if os.path.exists(values_src) else []
                    logging.warning(
                        "MORE_STEP2 - none of the %d regulator ID(s) in the relevant-regulators "
                        "file for omic '%s' matched this omic's regulators, so no red stars will "
                        "be shown. File has: %s. Analysis has: %s",
                        len(relevant_tfs), name,
                        ", ".join(sorted(relevant_tfs)[:3]) or "(nothing)",
                        ", ".join(available) or "(nothing)")
            else:
                # No user file → no red stars for this omic (matches miRNA2Genes behavior).
                open(rel_reg_path, 'w').close()
                logging.info(f"MORE_STEP2 - No user relevant-regulator file for omic '{name}' → empty {rel_reg_name} (no red stars)")

            # Copy R outputs into inputData/ so PA Step 1 can reference them by basename
            for fname in [out_file_name, rel_reg_name, rel_assoc_name, rel_pairs_name]:
                src_path = os.path.join(output_dir, fname)
                if os.path.exists(src_path):
                    shutil.copy2(src_path, os.path.join(input_dir, fname))

            results_summary[name] = {
                "outputFile": out_file_name,
                "associationsFile": rel_assoc_name,
                "relevantFeaturesFile": rel_reg_name,
                "relevantAssociationsFile": rel_pairs_name
            }

        # The experimental design, under a name the pathway job can find.
        #
        # MORE's output files keep the ORIGINAL per-sample columns -- for the
        # bundled example that is 36 `Batch_N_Ctr_0H`-style replicates -- and
        # the replicate detector only recognises a trailing `_R1`/`_rep2`, so
        # it reports "none" and every heatmap then draws 36 unreadable columns.
        # The grouping those columns need is stated exactly once, in the design
        # matrix, and MOREJob is a separate Job class: copying it here is the
        # only channel by which it reaches the pathway job (same reasoning as
        # the filters sidecar below). Best-effort -- a design that cannot be
        # copied costs the collapsed view, not the run.
        design_src = os.path.join(input_dir, jobInstance.conditionsFile) if jobInstance.conditionsFile else None
        if design_src and os.path.exists(design_src):
            design_name = f"MORE_design_{jobInstance.date}.tab"
            try:
                shutil.copy2(design_src, os.path.join(input_dir, design_name))
                logging.info("MORE_STEP2 - copied the experimental design to %s "
                             "for the pathway step.", design_name)
            except Exception as ex:
                logging.warning("MORE_STEP2 - could not copy the experimental design "
                                "(%s); heatmaps will show every replicate column.", str(ex))

        # Combined RegulationPerCondition table (single file, all omics). The R
        # script wrote it to output_dir; copy into inputData/ so the
        # PathwayAcquisitionJob can read it by basename in Step 4 (parse step).
        # If the R script didn't produce it (e.g. zero relevant regulations),
        # rpc_file_name is set to None and the response omits the field — the
        # Step 3 panel then stays hidden, matching the contract for absent data.
        rpc_file_name = f"MORE_rpc_{jobInstance.date}.tab"
        rpc_src = os.path.join(output_dir, rpc_file_name)
        if os.path.exists(rpc_src):
            rpc_dst = os.path.join(input_dir, rpc_file_name)
            shutil.copy2(rpc_src, rpc_dst)
            _nameConditionColumns(
                rpc_dst, os.path.join(input_dir, jobInstance.conditionsFile))

            # Sidecar metadata: the MORE filter settings the user picked at
            # configuration time. PathwayAcquisitionJob.parseRegulationPerCondition
            # picks this up and embeds it inside regulationPerConditionData so the
            # Step-3 Regulator-Target Network view can lock its R2 slider to the
            # floor the user originally chose. MOREJob is a separate Job class —
            # this file is the only channel by which its settings reach the PA job.
            filters_meta = {
                "filter_r2": jobInstance.filter_r2,
                "alpha":     jobInstance.alpha,
                "vip":       jobInstance.vip,
                "method":    jobInstance.method,
            }
            filters_name = f"MORE_filters_{jobInstance.date}.json"
            try:
                with open(os.path.join(input_dir, filters_name), "w") as fh:
                    json.dump(filters_meta, fh)
            except OSError as ex:
                # Non-fatal: client view falls back to defaults if sidecar missing.
                logging.warning(
                    f"MORE_STEP2 - could not write {filters_name}: {ex}"
                )
        else:
            logging.warning(
                f"MORE_STEP2 - {rpc_file_name} not produced by R; "
                "Step 3 regulation panel will be hidden."
            )
            rpc_file_name = None

        # 4. Bundle outputs for the "Download files" link (matches miRNA2Genes contract).
        # Fix recursion bug: create the archive in the temporal directory, then move it to output_dir.
        compressed_basename = f"more_results_{jobInstance.date}"
        archive_temp_path = os.path.join(temporal_dir, compressed_basename)
        
        logging.info(f"MORE_STEP2 - Creating results archive at {archive_temp_path}.zip")
        shutil.make_archive(archive_temp_path, "zip", output_dir)
        
        compressed_filename = compressed_basename + ".zip"
        shutil.move(archive_temp_path + ".zip", os.path.join(output_dir, compressed_filename))


        # 5. Finalize Response for UI — return basenames so saveFiles/parseGeneBasedFiles
        # can prepend inputDir/ to get the full path.
        response_data = {
            "success": True,
            "jobID": jobInstance.getJobID(),
            "description": f"MORE Analysis ({jobInstance.method})",
            "featureEnrichment": jobInstance.enrichment,
            "omicsCount": len(results_summary),
            "compressedFileName": compressed_filename
        }

        # Single combined RegulationPerCondition table (all omics) for the Step 3 panel.
        # Optional — present only when MORE produced relevant regulations.
        if rpc_file_name:
            response_data["regulationPerConditionFile"] = rpc_file_name

        for index, name in enumerate(results_summary.keys()):
            response_data[f"mainOutputFileName_{index}"]   = results_summary[name]["outputFile"]
            response_data[f"secondOutputFileName_{index}"] = results_summary[name]["relevantFeaturesFile"]
            response_data[f"thirdOutputFileName_{index}"]  = results_summary[name]["associationsFile"]
            response_data[f"fourthOutputFileName_{index}"] = results_summary[name]["relevantAssociationsFile"]
            response_data[f"omicName_{index}"] = name

        RESPONSE.setContent(response_data)

        # 5. Save MORE Job via the shared manager (makes it listable and removable)
        JobInformationManager().storeJobInstance(jobInstance, 1)

    except Exception as ex:
        jobInstance.cleanDirectories(remove_output=True)
        # Ensure we capture as much detail as possible in the response.
        handleException(RESPONSE, ex, __file__, "fromMOREtoGenes_STEP2")

    finally:
        return RESPONSE

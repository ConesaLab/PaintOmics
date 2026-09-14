"""The Agentic Graph Walk as a server job.

The command line and the queued job share one pipeline, ``run``: the graph and
the job overlay, the design card, the walk, the Writer, the sense check and
the Narrator, sealed into one record. The queued job (``run_job``) stores its
progress, the chain as it grows, and finally a browser-sized view of the record
where /ai_walk_status reads them. The chat's walk tool reads the same view, or
walks a few steps from a gene the user names with no model at all
(``quick_walk``).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

from src.conf.serverconf import (AI_LLM_PROVIDER, AI_PROVIDERS, KEGG_DATA_DIR, MONGODB_HOST,
                                 MONGODB_PORT)
from src.classes.AIInterpret.walker import card as card_mod
from src.classes.AIInterpret.walker import narrate as narrate_mod
from src.classes.AIInterpret.walker import network as net_mod
from src.classes.AIInterpret.walker import overlay as ov_mod
from src.classes.AIInterpret.walker import policies
from src.classes.AIInterpret.walker import record as record_mod
from src.classes.AIInterpret.walker import sense as sense_mod
from src.classes.AIInterpret.walker import verify
from src.classes.AIInterpret.walker.walk import Walker, params_for

logger = logging.getLogger(__name__)

# "network", or "pathway:" and an id as the Step 4 view names it (KEGG
# mmu04068, Reactome R-MMU-9614085). Anything else never reaches the queue.
SCOPE_RE = re.compile(r"^(?:network|pathway:[A-Za-z0-9][A-Za-z0-9_.\-]{0,63})$")
CARD_FIELDS = ("perturbation", "value", "axis", "baseline", "relevant")
CARD_FIELD_MAX = 300
QUICK_STEPS = (3, 12)
SEEN_SHOWN = 30

STAGE_PERCENT = {"network": 5, "card": 10, "walk": 15, "writer": 60, "sense": 80, "narrate": 90}
# The transport's retry deadline for a model walk, from its start: the walk,
# the Writer, the sense check and the Narrator together. Measured on one KEGG
# map: 76 + 40 + 8 + 8 s. A network walk may take up to three times the steps.
RUN_SECONDS = {"pathway": 480, "network": 1200}

HEARTBEAT_SECONDS = 60

_MONGO = {"client": None}
_MONGO_LOCK = threading.Lock()


class WalkError(ValueError):
    """A refusal the user can act on: an unknown pathway, a bad scope, a gone job."""


class WalkCancelled(WalkError):
    """The walk's document is gone (its job was deleted): stop, store nothing."""


def check_scope(scope):
    scope = str(scope or "").strip()
    if not SCOPE_RE.match(scope):
        raise WalkError('A walk scope is "network" or "pathway:<id>"; %r is neither.' % scope[:80])
    return scope


def queue_id(job_id, scope):
    """The queue's job id for one walk, so a second click finds the first run."""
    return "walk_%s_%s" % (job_id, re.sub(r"[^A-Za-z0-9]+", "_", scope))


def clean_card(raw):
    """The design card fields a user edited before the walk: a dict of the five
    free-text fields, each trimmed to CARD_FIELD_MAX characters; None when
    nothing usable was sent. Accepts a dict or its JSON text."""
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            raw = json.loads(raw)
        except ValueError:
            raise WalkError("The design card was not readable JSON.")
    if not isinstance(raw, dict):
        return None
    card = {}
    for key in CARD_FIELDS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            card[key] = value.strip()[:CARD_FIELD_MAX]
    return card or None


def mongo_for(organism):
    """The organism's database, through one client per process: every walk and
    every chat walk used to open a MongoClient of its own and never close it."""
    try:
        with _MONGO_LOCK:
            if _MONGO["client"] is None:
                from pymongo import MongoClient
                _MONGO["client"] = MongoClient(MONGODB_HOST, MONGODB_PORT, serverSelectionTimeoutMS=2000)
        return _MONGO["client"][organism + "-paintomics"]
    except Exception as exc:                                          # noqa: BLE001
        logger.warning("[walker] no Mongo for OmniPath: %s", exc)
        return None


def resolve_scope(network, scope):
    """'network', or 'pathway:<id>' -> the tag the network carries for that id."""
    if scope == "network":
        return "network"
    wanted = scope.split(":", 1)[-1]
    tags = set()
    for edge in network.edges.values():
        tags.update(edge["tags"])
    for db in ("KEGG", "Reactome", "OmniPath"):
        if "%s:%s" % (db, wanted) in tags:
            return "%s:%s" % (db, wanted)
    raise WalkError("Pathway %s draws no interactions a walk can follow (only KEGG, Reactome and "
                    "OmniPath pathways carry edges; MapMan bins do not)." % wanted)


def build(job, scope, data_dir=None, use_mongo=True):
    """(network, graph, overlay, tag): the organism network, the walk's graph
    and the job overlay on it."""
    organism = job.getOrganism()
    data_dir = data_dir or KEGG_DATA_DIR
    t0 = time.time()
    network = net_mod.load_or_build(organism, data_dir, mongo_for(organism) if use_mongo else None)
    tag = resolve_scope(network, scope)
    graph = network if tag == "network" else network.filter_pathway(tag)
    ov = ov_mod.overlay_job(graph, job)
    if tag == "network":
        ov_mod.apply_degree_cap(ov, 99)
    logger.info("[walker] graph %s: %d nodes, %d edges, N=%d, K=%d in %.1fs", tag, len(graph.nodes),
                len(graph.edges), ov.N, ov.K, time.time() - t0)
    return network, graph, ov, tag


def llm_client():
    from src.classes.AIInterpret.llm_client import LLMClient
    return LLMClient(AI_PROVIDERS[AI_LLM_PROVIDER], AI_LLM_PROVIDER)


REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"statements": {"type": "array", "items": {"type": "object"}}},
    "required": ["statements"], "additionalProperties": True,
}


def rewrite_once(client, card_text, chain, failing, verdicts):
    """One rewrite of the statements the sense check objected to."""
    objections = []
    for s in failing:
        v = verdicts.get(s["n"], {})
        objections.append({"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"), "cites": s.get("cites"),
                           "legs": s.get("legs"), "grounded_in": s.get("grounded_in"), "beyond": s.get("beyond"),
                           "papers": s.get("papers"),
                           "objections": {k: val.get("note") for k, val in v.items() if not val.get("ok")}})
    prompt = ("Rewrite each statement so that every objection is answered, keeping the same n, cites, legs, "
              "grounded_in, beyond and papers unless an objection requires a change. Return {\"statements\": [...]} "
              "with the full objects.\n\nDESIGN CARD\n%s\n\nCHAIN\n%s\n\nSTATEMENTS WITH OBJECTIONS\n%s"
              % (card_text, chain, json.dumps(objections, ensure_ascii=False, indent=1)))
    try:
        out = client.complete_json([{"role": "system", "content": "You revise interpretation statements to answer specific objections."},
                                    {"role": "user", "content": prompt}], "rewrite", REWRITE_SCHEMA,
                                   lambda text: None, max_tokens=2500, temperature=0.2)
    except Exception:                                                 # noqa: BLE001
        return {}
    if not isinstance(out, dict):
        return {}
    rewritten = {}
    for stmt in out.get("statements") or []:
        if not isinstance(stmt, dict) or "n" not in stmt:
            continue
        try:
            rewritten[int(stmt["n"])] = stmt
        except (TypeError, ValueError):
            continue
    return rewritten


def design_card(job, ov, policy, client=None, override=None):
    """The card the walk reads: the user's edits when they made any, else one
    model call for a model walk, else the deterministic card. The axis kind and
    the unlabeled omics always come from the data, never from a model."""
    labels = {name: ov.labels.get(name) for name in ov.labels}
    conditions = []
    try:
        conditions = list(job.conditionNames or [])
    except (AttributeError, TypeError):
        pass
    design_text = job.getExperimentDesign() if hasattr(job, "getExperimentDesign") else ""
    if override:
        card = card_mod.deterministic_card(design_text, conditions, labels)
        card.update(override)
        card["source"] = "user"
    elif policy == "model" and client is not None:
        card = card_mod.model_card(client, design_text, conditions, labels)
    else:
        card = card_mod.deterministic_card(design_text, conditions, labels)
    card["axis_kind"] = card_mod.axis_kind(labels, design_text)
    # The omics whose file carried no header: the walker may make no timing
    # claim on them until the user confirms the columns.
    card["unlabeled"] = ov_mod.relabel_unlabeled(ov)
    return card


def walk_card_text(card):
    """The card as the walker, the Writer and the Narrator read it, with what the
    axis allows: trajectories only on an ordered axis."""
    kind = card.get("axis_kind", "groups")
    allowed = {"time": "time points in order; timing words are allowed",
               "ordered": "ordered values (doses or concentrations); say higher or lower, not earlier or later",
               "groups": "conditions with no order; compare them by name, never as a trajectory",
               "unlabeled": "no labels; make no timing or order claim"}[kind]
    return card_mod.card_text(card) + "\n%-13s %s" % ("axis kind", allowed)


def _pct(stage, walker=None):
    if stage == "walk" and walker is not None and walker.plan:
        planned = max(1, walker.plan["steps"])
        used = planned - max(0, walker.budget["steps"])
        return STAGE_PERCENT["walk"] + int(45 * min(1.0, used / planned))
    return STAGE_PERCENT.get(stage, 0)


def run(job, job_id, scope, policy="greedy", data_dir=None, writer=True, max_turns=60, use_mongo=True,
        progress=None, card_override=None, cancelled=None):
    """The whole pipeline. Returns (record, network, graph, tag).

    ``progress(stage, percent, detail, walker)`` is called at every stage and
    after every walker turn; it must not raise into the walk.
    ``cancelled()`` says the walk is no longer wanted: the walker is stopped
    at its next turn and WalkCancelled is raised before the next stage, so a
    deleted job stops spending the gateway and PubMed."""
    def halt_if_cancelled():
        if cancelled is not None and cancelled():
            raise WalkCancelled("The walk for job %s was cancelled." % job_id)

    def on_turn(w):
        if cancelled is not None and cancelled() and not w.done:
            # Every walker tool refuses once the walk is sealed, so the model
            # loop ends within a turn or two instead of running its budget.
            w.stop("", "cancelled: the job was deleted")
            return
        report("walk", "Walking: %s" % w.counts_line(), w)
    def report(stage, detail, walker=None):
        if progress is None:
            return
        try:
            progress(stage, _pct(stage, walker), detail, walker)
        except Exception:                                             # noqa: BLE001
            logger.warning("[walker] progress callback failed", exc_info=True)

    timings = {}
    t0 = time.time()
    report("network", "Reading the network and laying the job's values over it")
    network, graph, ov, tag = build(job, scope, data_dir, use_mongo)
    timings["build"] = round(time.time() - t0, 1)
    halt_if_cancelled()
    client = llm_client() if policy == "model" else None
    t0 = time.time()
    report("card", "Reading the experiment design")
    card = design_card(job, ov, policy, client, card_override)
    timings["card"] = round(time.time() - t0, 1)
    card_text = walk_card_text(card)
    walker = Walker(graph, ov, tag, params_for("network" if tag == "network" else "pathway"))
    walker.on_turn = on_turn
    report("walk", "Scanning the graph for seeds", walker)
    t0 = time.time()
    model_used = "none (scripted %s policy)" % policy
    if policy == "model":
        from src.classes.AIInterpret.agent import set_run_deadline
        from src.classes.AIInterpret.walker import sdk
        # Bounds the transport's retries: without it a gateway outage retries
        # every call for its full budget and the walk holds a worker for an hour.
        set_run_deadline(time.time() + RUN_SECONDS["network" if tag == "network" else "pathway"])
        sdk.run_walk(walker, card_text, max_turns=max_turns)
        model_used = AI_PROVIDERS[AI_LLM_PROVIDER]["model"]
        if walker.loop_error and not walker.chain:
            # A gateway that failed is not a walk that found nothing: stored as
            # done, the empty walk was final -- no Retry, and initiate answered
            # already_finished for ever.
            raise WalkError("The AI service failed before the walk took a step (%s). Start it again."
                            % walker.loop_error)
    elif policy == "random":
        policies.random_walk(walker)
    else:
        policies.greedy(walker)
    walker.on_turn = None
    timings["walk"] = round(time.time() - t0, 1)
    halt_if_cancelled()
    statements, dropped, results, papers, checks = [], [], None, {}, {}
    if policy == "model" and writer and walker.chain:
        from src.classes.AIInterpret.pubmed_client import PubMedClient
        from src.classes.AIInterpret.walker import writer as writer_mod
        report("writer", "Writing statements from the chain and checking their citations", walker)
        halt_if_cancelled()
        t0 = time.time()
        wctx = writer_mod.run_writer(walker, card_text, PubMedClient())
        timings["writer"] = round(time.time() - t0, 1)
        if wctx.loop_error and not wctx.kept:
            raise WalkError("The AI service failed while writing the statements (%s). Start the walk again."
                            % wctx.loop_error)
        statements, dropped = wctx.kept, wctx.dropped
        papers = {ref: {"pmid": p.get("pmid"), "title": p.get("title"), "year": p.get("year"),
                        "journal": p.get("journal")} for ref, p in wctx.papers.items()}
        checks["writer_trace"] = wctx.trace
        chain = writer_mod.chain_text(walker)
        halt_if_cancelled()
        if statements:
            report("sense", "Checking each statement against the design and the values", walker)
            t0 = time.time()
            try:
                _sense_pass(client, card_text, chain, statements, dropped, walker, wctx, checks)
            except Exception:                                         # noqa: BLE001
                # A malformed model answer drops this stage, not the walk: the
                # statements it rewrote are set aside, the rest stay as the
                # Verifier passed them.
                logger.warning("[walker] the sense check failed", exc_info=True)
                checks["sense"] = "failed"
                for s in [s for s in statements if s.get("rewritten")]:
                    statements.remove(s)
                    dropped.append({"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                                    "why": "the sense check's rewrite could not be read", "by": "sense check"})
            timings["sense"] = round(time.time() - t0, 1)
        halt_if_cancelled()
        if statements:
            report("narrate", "Writing the Results section", walker)
            t0 = time.time()
            try:
                results = _narrate(client, card_text, chain, statements, dropped, walker, papers, tag, checks)
            except Exception:                                         # noqa: BLE001
                logger.warning("[walker] the Narrator's answer could not be checked", exc_info=True)
                checks["results"] = ["the Narrator's answer could not be read"]
                results = None
            timings["narrate"] = round(time.time() - t0, 1)
    if papers:
        papers = record_mod.renumber_citations(statements, dropped, results, papers)
    rec = record_mod.seal(job_id, tag, graph, ov, walker, card, statements, dropped, results, papers,
                          model_used, timings, checks)
    # Labels for the plan's seeds and the nodes seen but not walked: the record's
    # node table holds only the chain, and a seed the walk never reached would
    # otherwise show as its id.
    rec["labels"] = {node_id: walker.label(node_id) for node_id in
                     list((walker.plan or {}).get("seeds") or []) + [r["id"] for r in rec["walk"]["seen"][:60]]}
    return rec, network, graph, tag


def _sense_pass(client, card_text, chain, statements, dropped, walker, wctx, checks):
    verdicts = sense_mod.sense_check(client, card_text, statements, chain)
    if verdicts is None:
        for s in statements:
            s["sense"] = None
        checks["sense"] = "unavailable"
        return
    for s in statements:
        s["sense"] = verdicts.get(s["n"])
    failing = [s for s in statements if sense_mod.failed_fields(s.get("sense"))]
    if not failing:
        return
    rewritten = rewrite_once(client, card_text, chain, failing, verdicts)
    for s in failing:
        new = rewritten.get(s["n"])
        if new:
            for key in ("claim", "prose", "cites", "legs", "grounded_in", "beyond", "papers"):
                if key in new:
                    s[key] = new[key]
            s["rewritten"] = True
    problems = {s["n"]: verify.verify_statement(s, walker, wctx.papers, wctx.read) for s in failing}
    again = sense_mod.sense_check(client, card_text, failing, chain) or {}
    for s in failing:
        s["sense"] = again.get(s["n"], s["sense"])
        if problems[s["n"]] or sense_mod.failed_fields(s.get("sense")):
            statements.remove(s)
            dropped.append({"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                            "why": "; ".join(problems[s["n"]] + [
                                "%s: %s" % (k, s["sense"][k]["note"]) for k in sense_mod.failed_fields(s["sense"])]),
                            "by": "sense check"})


def _narrate(client, card_text, chain, statements, dropped, walker, papers, tag, checks):
    words = (300, 900) if tag == "network" else (150, 450)
    kind = "network" if tag == "network" else "pathway"
    # Only the papers the kept statements cite: the Narrator retells statements,
    # and a retrieved paper no statement cites was never checked for the claim.
    cited = {ref for s in statements for ref in verify.statement_refs(s)}
    papers_text = "\n".join("[%d] %s (%s) PMID %s" % (r, p.get("title"), p.get("year"), p.get("pmid"))
                            for r, p in sorted(papers.items()) if r in cited)
    def checked(results):
        if results is None:
            return ["no results"]
        pruned = narrate_mod.prune_connectives(results)
        if pruned:
            checks["connectives_pruned"] = checks.get("connectives_pruned", 0) + pruned
        unquoted = verify.drop_unrecorded_sentences(results, walker)
        if unquoted:
            checks["misquoted_sentences_dropped"] = checks.get("misquoted_sentences_dropped", 0) + unquoted
        uncited = verify.drop_uncited_sentences(results, statements)
        if uncited:
            checks["uncited_sentences_dropped"] = checks.get("uncited_sentences_dropped", 0) + uncited
        return verify.verify_results(results, statements, dropped, walker, kind)

    results = narrate_mod.narrate(client, card_text, statements, chain, papers_text, words)
    problems = checked(results)
    if problems and results is not None:
        results = narrate_mod.narrate(client, card_text, statements, chain, papers_text, words, objections=problems)
        problems = checked(results)
    checks["results"] = problems
    if problems:
        checks["results_dropped"] = results
        return None
    return results


# --------------------------------------------------------------------- views
def _leg_view(leg, labels):
    edge = leg.get("edge") or None
    return {"n": leg["n"], "kind": leg["kind"], "from": leg["from"], "to": leg["to"],
            "from_label": labels(leg["from"]), "to_label": labels(leg["to"]),
            "reading": leg["reading"], "reason": leg["reason"],
            "edge": ({"db": edge.get("db"), "pathway": edge.get("pathway"), "name": edge.get("name"),
                      "subtype": edge.get("subtype"), "sign": edge.get("sign"), "dir": edge.get("dir")}
                     if edge else None)}


def _plan_view(plan, labels):
    if not plan:
        return None
    return {"seeds": [{"id": s, "label": labels(s)} for s in plan.get("seeds") or []],
            "steps": plan.get("steps"), "reason": plan.get("reason")}


def live_view(walker):
    """What the browser shows while the model walks: the plan and the chain so far."""
    rec = walker.record()
    return {"scope": walker.scope, "plan": _plan_view(rec["plan"], walker.label),
            "chain": [_leg_view(leg, walker.label) for leg in rec["chain"]],
            "notes": rec["notes"], "counts": walker.counts_line(), "done": rec["done"],
            "nodes": {node_id: {"label": walker.label(node_id), "text": walker.overlay.layer_text(node_id)}
                      for leg in rec["chain"] for node_id in (leg["from"], leg["to"])}}


def view(rec):
    """The sealed record trimmed to what the Walk column, the report widget and
    the chat render. The full record (turns, every scan) stays on the server
    log side; the view keeps every value a sentence quotes."""
    walk = rec["walk"]
    nodes = rec.get("nodes") or {}

    extra = rec.get("labels") or {}

    def labels(node_id):
        return (nodes.get(node_id) or {}).get("label") or extra.get(node_id) or node_id

    seen = sorted(walk.get("seen") or [], key=lambda r: -(r.get("heat") or 0))[:SEEN_SHOWN]
    steps = sum(1 for leg in walk["chain"] if leg["kind"] == "step")
    card = rec.get("design_card") or {}
    return {
        "schema": rec.get("schema"), "job": rec.get("job"), "scope": rec.get("scope"),
        "sealed_at": rec.get("sealed_at"), "graph": rec.get("graph"),
        "design_card": {k: card.get(k) for k in CARD_FIELDS + ("columns", "source", "axis_kind", "unlabeled")},
        "plan": _plan_view(walk.get("plan"), labels),
        "chain": [_leg_view(leg, labels) for leg in walk["chain"]],
        "notes": walk.get("notes") or [], "stop_reason": walk.get("stop_reason"),
        "stop_reading": walk.get("stop_reading"),
        "counts": {"steps": steps, "jumps": len(walk["chain"]) - steps, "notes": len(walk.get("notes") or []),
                   "scans": walk.get("scans"), "refusals": walk.get("refusals"),
                   "seen_not_walked": len(walk.get("seen") or [])},
        "seen": [{"id": r["id"], "label": r["label"], "r": r["r"], "heat": r["heat"]} for r in seen],
        "nodes": {node_id: {"label": n.get("label"), "kind": n.get("kind"), "r": n.get("r"),
                            "heat": n.get("heat"), "text": n.get("text")} for node_id, n in nodes.items()},
        "statements": [{k: s.get(k) for k in ("n", "claim", "prose", "cites", "legs", "grounded_in", "beyond",
                                               "papers", "sense", "rewritten")} for s in rec.get("statements") or []],
        "dropped": [{k: d.get(k) for k in ("n", "claim", "why", "by")} for d in rec.get("dropped") or []],
        "results": rec.get("results"),
        "papers": {str(ref): p for ref, p in (rec.get("papers") or {}).items()},
        "checks": {"results": (rec.get("checks") or {}).get("results"),
                   "sense": (rec.get("checks") or {}).get("sense")},
        "model_used": rec.get("model_used"), "timings": rec.get("timings"),
    }


# ----------------------------------------------------------------- the job
def run_job(job_id, scope, card_override=None, policy="model"):
    """The queued function behind /ai_walk_start. Never raises: every outcome,
    a refusal included, is stored where /ai_walk_status reads it."""
    from src.common.DAO.AIWalkDAO import AIWalkDAO
    from src.common.JobInformationManager import JobInformationManager

    dao = AIWalkDAO()
    last = {"t": 0.0, "legs": -1}
    # The Writer and the checks can run for minutes without a new leg; a
    # heartbeat keeps the status poll from reading that silence as a dead walk.
    stop_beat = threading.Event()
    # Set when a write finds the walk's document gone: its job was deleted.
    # Every write here updates and never inserts (the route filed the
    # document before queueing), so a deleted walk stays deleted.
    cancelled = threading.Event()

    def write(fields):
        if not dao.update_existing(job_id, scope, fields):
            cancelled.set()

    def beat():
        while not stop_beat.wait(HEARTBEAT_SECONDS):
            try:
                write({})
            except Exception:                                         # noqa: BLE001
                logger.warning("[walker] heartbeat failed for %s %s", job_id, scope, exc_info=True)

    threading.Thread(target=beat, daemon=True, name="walk-heartbeat-%s" % job_id).start()

    def progress(stage, percent, detail, walker=None):
        fields = {"status": "running", "stage": stage, "percent": int(percent), "detail": detail}
        if walker is not None:
            legs = len(walker.chain)
            now = time.time()
            # every new leg is written; turns that add none at most once a second
            if legs == last["legs"] and now - last["t"] < 1.0 and stage == "walk":
                return
            last["t"], last["legs"] = now, legs
            fields["liveJSON"] = json.dumps(live_view(walker), ensure_ascii=False)
        write(fields)

    try:
        job = JobInformationManager().loadJobInstance(job_id)
        if job is None:
            raise WalkError("The job %s no longer exists." % job_id)
        rec, _network, _graph, _tag = run(job, job_id, scope, policy=policy, progress=progress,
                                          card_override=card_override, cancelled=cancelled.is_set)
        kept, walked = len(rec["statements"]), len(rec["walk"]["chain"])
        detail = "%d legs walked, %d statements kept%s" % (
            walked, kept, ", Results section written" if rec.get("results") else "")
        write({"status": "done", "stage": "done", "percent": 100, "detail": detail,
               "viewJSON": json.dumps(view(rec), ensure_ascii=False), "liveJSON": None})
    except WalkCancelled:
        logger.info("[walker] walk %s %s stopped: its document is gone", job_id, scope)
    except WalkError as exc:
        write({"status": "error", "stage": "error", "percent": 0, "detail": str(exc), "liveJSON": None})
    except Exception as exc:                                          # noqa: BLE001
        logger.exception("[walker] walk %s %s failed", job_id, scope)
        write({"status": "error", "stage": "error", "percent": 0,
               "detail": "The walk stopped with an internal error (%s)." % type(exc).__name__,
               "liveJSON": None})
    finally:
        stop_beat.set()
        dao.closeConnection()


# ----------------------------------------------------------------- the chat
def quick_walk(job, symbol, steps=8, data_dir=None, use_mongo=True):
    """A scripted walk of a few steps from one gene over the universal network:
    the hottest relevant unvisited neighbour each time, with every node's
    layers. No model and no literature; the chat model reads the text."""
    try:
        steps = int(steps)
    except (TypeError, ValueError):
        steps = 8
    steps = max(QUICK_STEPS[0], min(QUICK_STEPS[1], steps))
    network, graph, ov, tag = build(job, "network", data_dir, use_mongo)
    walker = Walker(graph, ov, tag, params_for("network"))
    node_id = walker._resolve(symbol, sorted(ov.measured)) or walker._resolve(symbol, list(graph.nodes))
    if node_id is None:
        return "No node named %s in the %s network." % (symbol, job.getOrganism())
    policies.greedy_from(walker, node_id, steps)
    from src.classes.AIInterpret.walker import writer as writer_mod
    rec = walker.record()
    lines = ["A scripted walk of %d step(s) from %s over KEGG, Reactome and OmniPath edges (no model; the "
             "hottest relevant unvisited neighbour each time). Legs name their pathway; values are the user's."
             % (len([leg for leg in rec["chain"] if leg["kind"] == "step"]), walker.label(node_id))]
    lines.append(writer_mod.chain_text(walker) if rec["chain"] else
                 "start: %s\n   layers:\n%s\n(no relevant neighbour to step to)" % (
                     walker.label(node_id), "\n".join("      " + l for l in ov.layer_text(node_id).splitlines())))
    lines.append("stopped: %s" % (rec["stop_reason"] or "budget spent"))
    lines.append("seen, not walked (hottest): %s" % (writer_mod.seen_text(walker, limit=10).replace("\n", " | ")))
    return "\n".join(lines)


def stored_walk_text(doc, limit_legs=40):
    """The stored universal walk as chat text: the Results section when it
    passed its checks, the kept statements, and the legs with their pathways."""
    if not doc:
        return ("No universal walk has run for this job yet. It is the job's interpretation and "
                "starts when the analysis finishes; name a gene to walk a few steps from it instead.")
    status = doc.get("status")
    if status != "done":
        if status == "error":
            return "The universal walk for this job failed: %s" % doc.get("detail", "")
        return "The universal walk for this job is still running (%s, %s%%)." % (
            doc.get("stage", "queued"), doc.get("percent", 0))
    try:
        v = json.loads(doc.get("viewJSON") or "{}")
    except ValueError:
        return "The stored universal walk could not be read."
    lines = []
    results = v.get("results") or {}
    if results.get("paragraphs"):
        lines.append("RESULTS (checked): %s" % results.get("summary", ""))
        for p in results["paragraphs"]:
            lines.append("- %s" % p.get("text", ""))
    for s in v.get("statements") or []:
        lines.append("STATEMENT %s: %s (legs %s)" % (s.get("n"), s.get("claim"), s.get("legs")))
    papers = v.get("papers") or {}
    if papers:
        lines.append("PAPERS: " + "; ".join("[%s] %s PMID %s" % (ref, p.get("title"), p.get("pmid"))
                                            for ref, p in sorted(papers.items(), key=lambda kv: int(kv[0]))))
    lines.append("LEGS:")
    for leg in (v.get("chain") or [])[:limit_legs]:
        edge = leg.get("edge") or {}
        where = ("%s %s (%s:%s)" % (edge.get("db"), edge.get("name"), edge.get("db"), edge.get("pathway"))
                 if edge else "jump")
        lines.append("e%s %s -> %s · %s · %s" % (leg["n"], leg["from_label"], leg["to_label"], where,
                                                 leg.get("reading", "")))
    return "\n".join(lines)

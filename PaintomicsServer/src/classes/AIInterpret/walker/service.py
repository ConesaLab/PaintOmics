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
import os
import re
import threading
import time

from src.conf.serverconf import (AI_LLM_PROVIDER, AI_PROVIDERS, KEGG_DATA_DIR, MONGODB_HOST,
                                 MONGODB_PORT)
from src.classes.AIInterpret.walker import anchor as anchor_mod
from src.classes.AIInterpret.walker import card as card_mod
from src.classes.AIInterpret.walker import direction as direction_mod
from src.classes.AIInterpret.walker import null as null_mod
from src.classes.AIInterpret.walker import tiers
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
CARD_FIELDS = ("perturbation", "value", "axis", "baseline", "relevant", "system")
CARD_LIST_FIELDS = ("perturbed_genes",)
CARD_ENUM_FIELDS = {"perturbation_direction": ("up", "down", "unknown")}
CARD_FIELD_MAX = 300
QUICK_STEPS = (3, 12)
SEEN_SHOWN = 30

STAGE_PERCENT = {"network": 5, "card": 10, "walk": 15, "writer": 60, "sense": 80, "narrate": 90}
# A model walk's time budget is parallel.PLANS[...]["run_seconds"]: the
# walkers, the Writers and their paper agents, the sense check and the
# Narrator together, and the transport's retry deadline.
# Seconds the sense check and the Narrator need after the Writers stop.
SENSE_MIN_SECONDS = 70
NARRATE_MIN_SECONDS = 45

HEARTBEAT_SECONDS = 60
# Seconds the direction check needs after the Writers stop (three short
# calls per regulator a statement cites; usually none or a few).
DIRECTION_MIN_SECONDS = 30
# Permutations of the structure null: fewer on a graph where each costs a
# heat pass over thousands of nodes.
NULL_K, NULL_K_LARGE, LARGE_GRAPH_NODES = 50, 20, 5000
GATES = ("artifact", "title", "direction", "context", "anchor")

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
    for key in CARD_LIST_FIELDS:
        value = raw.get(key)
        if isinstance(value, (list, tuple, str)) and value:
            card[key] = [str(g).strip()[:40] for g in card_mod.normalise_genes(value)][:8]
    for key, allowed in CARD_ENUM_FIELDS.items():
        value = raw.get(key)
        if isinstance(value, str) and value.strip().lower() in allowed:
            card[key] = value.strip().lower()
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


def org_dir_for(job, data_dir=None):
    return os.path.join(data_dir or KEGG_DATA_DIR, "current", job.getOrganism())


def build(job, scope, data_dir=None, use_mongo=True, card=None, aliases=None):
    """(network, graph, overlay, tag, anchor, dist): the organism network, the
    walk's graph, the job overlay on it, and -- when ``card`` names a perturbed
    gene the organism knows -- the anchor with its known targets added to the
    graph for this run and every node's distance from it."""
    organism = job.getOrganism()
    data_dir = data_dir or KEGG_DATA_DIR
    t0 = time.time()
    network = net_mod.load_or_build(organism, data_dir, mongo_for(organism) if use_mongo else None)
    tag = resolve_scope(network, scope)
    graph = network if tag == "network" else network.filter_pathway(tag)
    anchor, dist = None, None
    if card:
        org_dir = os.path.join(data_dir, "current", organism)
        aliases = aliases if aliases is not None else anchor_mod.alias_map(org_dir)
        anchor = anchor_mod.resolve_anchor(card, network, org_dir, aliases)
        if anchor is not None:
            anchor_mod.add_anchor_edges(graph, network, anchor, anchor_mod.load_tf_targets(org_dir), aliases)
            dist = anchor_mod.distances(network, anchor["node"]) if anchor["in_graph"] else {}
    ov = ov_mod.overlay_job(graph, job)
    if tag == "network":
        ov_mod.apply_degree_cap(ov, 99)
    logger.info("[walker] graph %s: %d nodes, %d edges, N=%d, K=%d%s in %.1fs", tag, len(graph.nodes),
                len(graph.edges), ov.N, ov.K,
                ", anchored on %s (%d target edges)" % (anchor["gene"], anchor.get("edges_added", 0)) if anchor else "",
                time.time() - t0)
    return network, graph, ov, tag, anchor, dist


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
                                   lambda text: None, max_tokens=max(2500, 700 * len(failing) + 500), temperature=0.2)
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


def design_card(job, labels, policy, client=None, override=None, aliases=None):
    """The card the walk reads: the user's edits when they made any, else one
    model call for a model walk, else the deterministic card. The axis kind,
    the unlabeled omics and the organism always come from the data, never from
    a model; the perturbed genes are kept only when the organism knows them."""
    conditions = []
    try:
        conditions = list(job.conditionNames or [])
    except (AttributeError, TypeError):
        pass
    design_text = job.getExperimentDesign() if hasattr(job, "getExperimentDesign") else ""
    if override:
        card = card_mod.deterministic_card(design_text, conditions, labels, aliases)
        card.update(override)
        card["perturbed_genes"] = card_mod.normalise_genes(card.get("perturbed_genes"), aliases)
        card["source"] = "user"
    elif policy == "model" and client is not None:
        card = card_mod.model_card(client, design_text, conditions, labels, aliases=aliases)
    else:
        card = card_mod.deterministic_card(design_text, conditions, labels, aliases)
    card["axis_kind"] = card_mod.axis_kind(labels, design_text)
    # The omics whose file carried no header: the walker may make no timing
    # claim on them until the user confirms the columns.
    card["unlabeled"] = sorted(name for name, l in labels.items() if l is None)
    card["organism"] = card_mod.organism_word(job.getOrganism())
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


def run(job, job_id, scope, policy="greedy", data_dir=None, writer=True, use_mongo=True,
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
    checks = {"gates": {}}
    client = llm_client() if policy == "model" else None
    t0 = time.time()
    report("card", "Reading the experiment design")
    aliases = anchor_mod.alias_map(org_dir_for(job, data_dir))
    card = design_card(job, card_mod.labels_by_omic(job), policy, client, card_override, aliases)
    timings["card"] = round(time.time() - t0, 1)
    halt_if_cancelled()
    t0 = time.time()
    report("network", "Reading the network and laying the job's values over it")
    network, graph, ov, tag, anchor, dist = build(job, scope, data_dir, use_mongo, card, aliases)
    timings["build"] = round(time.time() - t0, 1)
    checks["anchor"] = anchor
    halt_if_cancelled()
    card_text = walk_card_text(card)
    walker = Walker(graph, ov, tag, params_for("network" if tag == "network" else "pathway"))
    walker.dist, walker.anchor = dist, anchor
    statements, dropped, results, papers = [], [], None, {}
    t0 = time.time()
    model_used = "none (scripted %s policy)" % policy
    if policy == "model":
        walker, statements, dropped, results, papers = _model_walk(
            walker, tag, card, card_text, client, writer, report, halt_if_cancelled, cancelled, timings, checks)
        model_used = AI_PROVIDERS[AI_LLM_PROVIDER]["model"]
    else:
        walker.on_turn = on_turn
        report("walk", "Scanning the graph for seeds", walker)
        if policy == "random":
            policies.random_walk(walker)
        else:
            policies.greedy(walker)
        walker.on_turn = None
        timings["walk"] = round(time.time() - t0, 1)
        halt_if_cancelled()
    anchor_mod.label_segments(walker, dist or {})
    gates = checks["gates"]
    if "artifact" not in gates:
        t0 = time.time()
        gates["artifact"] = artifact_gate(walker)
        timings["null"] = round(time.time() - t0, 1)
    gates["anchor"] = anchor_mod.anchor_gate(anchor, walker, ov, dist or {}, network=network)
    for name in GATES:
        gates.setdefault(name, {"pass": True, "not_applicable": True, "why": "no statements were written"})
    checks["rendered"] = all(bool(gates[name].get("pass")) for name in GATES)
    if papers:
        papers = record_mod.renumber_citations(statements, dropped, results, papers)
        attach_evidence(statements, papers)
    rec = record_mod.seal(job_id, tag, graph, ov, walker, card, statements, dropped, results, papers,
                          model_used, timings, checks)
    # Labels for the plan's seeds and the nodes seen but not walked: the record's
    # node table holds only the chain, and a seed the walk never reached would
    # otherwise show as its id.
    rec["labels"] = {node_id: walker.label(node_id) for node_id in
                     list((walker.plan or {}).get("seeds") or []) + [r["id"] for r in rec["walk"]["seen"][:60]]}
    return rec, network, graph, tag


def artifact_gate(walker):
    """Check 1 at request time: the structure null on the walk's own graph and
    parameters, and no leg through a currency metabolite."""
    k = NULL_K if len(walker.network.nodes) <= LARGE_GRAPH_NODES else NULL_K_LARGE
    gate = null_mod.structure_null(walker.network, walker.overlay, walker.params, walker, k=k)
    currency = sum(1 for leg in walker.chain if leg.kind == "step"
                   and (tiers.is_currency(leg.src) or tiers.is_currency(leg.dst)))
    gate["currency_legs"] = currency
    if currency:
        gate["pass"] = False
        gate["why"] = ("; " if gate["why"] else "") + "%d leg(s) run through a currency metabolite" % currency
    return gate


def _model_walk(walker, tag, card, card_text, client, writer, report, halt_if_cancelled, cancelled, timings,
                checks):
    """The model walk (walker/parallel.py), then the checks and the Narrator,
    inside the scope's time budget. Returns (walker, statements, dropped,
    results, papers)."""
    import asyncio

    from src.classes.AIInterpret.agent import set_run_deadline
    from src.classes.AIInterpret.pubmed_client import PubMedClient
    from src.classes.AIInterpret.walker import parallel
    from src.classes.AIInterpret.walker import writer as writer_mod

    plan = parallel.plan_for(tag)
    started = time.time()
    run_deadline = started + plan["run_seconds"]
    # Bounds the transport's retries: without it a gateway outage retries every
    # call for its full budget. Armed before asyncio.run, whose tasks copy it.
    set_run_deadline(run_deadline)
    walker.params.update({"max_seeds": plan["max_seeds"], "steps_per_seed": plan["seed_steps"][0],
                          "ceiling": plan["max_seeds"] * plan["seed_steps"][1]})
    statements, dropped, papers = [], [], {}
    gates = checks["gates"]
    anchor = walker.anchor
    card_ctx = {"organism": card.get("organism"), "system": card.get("system")}

    def preview(merged):
        report("walk", "Walking: %s" % merged.counts_line(), merged)

    async def pipeline():
        report("walk", "Scanning the graph for seeds", walker)
        merged = await parallel.walk_in_parallel(walker, card_text, plan, started + plan["walk_seconds"],
                                                 cancelled, preview)
        timings["walk"] = round(time.time() - started, 1)
        if merged.loop_error and not merged.chain:
            # A gateway that failed is not a walk that found nothing: stored as
            # done, the empty walk was final -- no Retry, and initiate answered
            # already_finished for ever.
            raise WalkError("The AI service failed before the walk took a step (%s). Start it again."
                            % merged.loop_error)
        halt_if_cancelled()
        t0 = time.time()
        report("walk", "Checking the walk against permuted data", merged)
        gates["artifact"] = artifact_gate(merged)
        timings["null"] = round(time.time() - t0, 1)
        if not (writer and merged.chain):
            return merged, None
        report("writer", "Writing statements and reading every cited paper", merged)
        t0 = time.time()
        writer_deadline = min(time.time() + plan["writer_seconds"],
                              run_deadline - SENSE_MIN_SECONDS - NARRATE_MIN_SECONDS - DIRECTION_MIN_SECONDS)
        written = await parallel.write_in_parallel(
            merged, card_text, PubMedClient(), client, plan, writer_deadline, cancelled,
            lambda detail: report("writer", detail, merged), card_ctx=card_ctx)
        timings["writer"] = round(time.time() - t0, 1)
        return merged, written

    walker, written = asyncio.run(pipeline())
    halt_if_cancelled()
    if written is None:
        return walker, statements, dropped, None, papers
    statements, dropped, store, contexts = written
    if not statements and contexts and all(c.loop_error for c in contexts):
        raise WalkError("The AI service failed while writing the statements (%s). Start the walk again."
                        % contexts[0].loop_error)
    papers = {ref: {"pmid": p.get("pmid"), "title": p.get("title"), "year": p.get("year"),
                    "journal": p.get("journal"), "context": p.get("context")} for ref, p in store.papers.items()}
    checks["writer_trace"] = [entry for c in contexts for entry in c.trace]
    checks["paper_agent"] = {"checked": store.checks, "trace": store.trace}
    checks["parts"] = [list(c.legs) for c in contexts]
    chain = writer_mod.chain_text(walker)
    read = set()
    for c in contexts:
        read |= c.read
    chain_record = walker.record()["chain"]
    for s in statements:
        s["tier"] = tiers.statement_tier(s, chain_record)
    results = None
    if statements and run_deadline - time.time() >= SENSE_MIN_SECONDS + DIRECTION_MIN_SECONDS:
        report("sense", "Checking the direction of every regulator the statements lean on", walker)
        t0 = time.time()
        try:
            _direction_pass(client, card_text, chain, statements, dropped, walker, store, read, checks)
        except Exception:                                             # noqa: BLE001
            logger.warning("[walker] the direction check failed", exc_info=True)
            gates["direction"] = {"pass": True, "not_applicable": True, "why": "the direction check could not run"}
        timings["direction"] = round(time.time() - t0, 1)
    elif statements:
        gates["direction"] = {"pass": True, "not_applicable": True, "why": "skipped: the time budget was spent"}
    gates["context"] = context_gate(statements, store.papers)
    halt_if_cancelled()
    if statements and run_deadline - time.time() >= SENSE_MIN_SECONDS:
        report("sense", "Checking each statement against the design and the values", walker)
        t0 = time.time()
        try:
            _sense_pass(client, card_text, chain, statements, dropped, walker, store, read, checks)
        except Exception:                                             # noqa: BLE001
            # A malformed model answer drops this stage, not the walk: the
            # statements it rewrote are set aside, the rest stay as the Writers
            # and the paper agents passed them.
            logger.warning("[walker] the sense check failed", exc_info=True)
            checks["sense"] = "failed"
            for s in [s for s in statements if s.get("rewritten")]:
                statements.remove(s)
                dropped.append({"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                                "why": "the sense check's rewrite could not be read", "by": "sense check"})
        timings["sense"] = round(time.time() - t0, 1)
    elif statements:
        checks["sense"] = "skipped: the time budget was spent"
    halt_if_cancelled()
    scope_name = "The whole network" if tag == "network" else walker.network.pathway_name(tag)
    if statements and run_deadline - time.time() >= NARRATE_MIN_SECONDS:
        report("narrate", "Writing the Results section", walker)
        t0 = time.time()
        try:
            results = _narrate(client, card_text, chain, statements, dropped, walker, papers, tag, checks,
                               plan["words"], run_deadline, anchor=anchor, card=card, scope_name=scope_name)
        except Exception:                                             # noqa: BLE001
            logger.warning("[walker] the Narrator's answer could not be checked", exc_info=True)
            checks["results"] = ["the Narrator's answer could not be read"]
            results = None
        timings["narrate"] = round(time.time() - t0, 1)
    elif statements:
        checks["results"] = ["no Results section: the time budget was spent"]
    if results is None:
        gates.setdefault("title", {"pass": True, "not_applicable": True, "why": "no Results section to check"})
    timings["total"] = round(time.time() - started, 1)
    return walker, statements, dropped, results, papers


def context_gate(statements, papers):
    """Check 4 summarised over the kept statements: every citation's paper has
    a confirmed passage, and every out-of-context paper is named as such in
    the sentence that cites it (the Verifier objected to any that was not)."""
    if not statements:
        return {"pass": True, "not_applicable": True, "cited": 0, "why": "no statements were written"}
    cited = in_context = qualified = unqualified = unconfirmed = 0
    for stmt in statements:
        confirmed = {item.get("ref") for item in stmt.get("evidence") or []}
        for ref in verify.statement_refs(stmt):
            sentence = " ".join(verify.citing_sentences(stmt, ref))
            cited += 1
            if ref not in confirmed:
                unconfirmed += 1
            paper = papers.get(ref) or {}
            match = (paper.get("context") or {}).get("match") or {}
            if "other" not in (match.get("organism"), match.get("system")):
                in_context += 1
            elif verify.context_named(sentence, paper.get("context")):
                qualified += 1
            else:
                unqualified += 1
    gate = {"pass": unqualified == 0 and unconfirmed == 0, "not_applicable": False, "cited": cited,
            "in_context": in_context, "qualified": qualified, "unqualified_other": unqualified,
            "unconfirmed": unconfirmed, "why": ""}
    if not gate["pass"]:
        parts = []
        if unqualified:
            parts.append("%d citation(s) lean on a paper from another organism or system without saying so" % unqualified)
        if unconfirmed:
            parts.append("%d citation(s) have no passage a paper agent confirmed" % unconfirmed)
        gate["why"] = "; ".join(parts)
    return gate


def attach_evidence(statements, papers):
    """Each cited paper carries the passages the paper agents found in it, with
    the claim each supports and the statement that makes it."""
    for ref, paper in papers.items():
        seen, evidence = set(), []
        for stmt in statements:
            for item in stmt.get("evidence") or []:
                key = (item.get("claim"), item.get("quote"))
                if item.get("ref") == ref and key not in seen:
                    seen.add(key)
                    evidence.append({"claim": item.get("claim"), "quote": item.get("quote"),
                                     "section": item.get("section"), "full_text": item.get("full_text"),
                                     "statement": stmt.get("n")})
        paper["evidence"] = evidence


def _rewrite_and_recheck(client, card_text, chain, failing, notes, walker, store, read):
    """One rewrite of the failing statements, re-verified by code and with
    every citation checked against the paper agents' verdicts. ``notes`` is
    {n: {field: note}}; returns {n: [problems]} for the rewritten statements."""
    verdicts = {n: {field: {"ok": False, "note": note} for field, note in fields.items()}
                for n, fields in notes.items()}
    rewritten = rewrite_once(client, card_text, chain, failing, verdicts)
    for s in failing:
        new = rewritten.get(s["n"])
        if new:
            for key in ("claim", "prose", "cites", "legs", "grounded_in", "beyond", "papers"):
                if key in new:
                    s[key] = new[key]
            s["rewritten"] = True
    problems = {s["n"]: verify.verify_statement(s, walker, store.papers, read, names_genes=False) for s in failing}
    for s in failing:
        # A rewrite may not bring in a citation no paper agent confirmed.
        evidence = []
        for ref, claim in verify.citation_pairs(s):
            verdict = store.verdict(ref, claim, " ".join(verify.citing_sentences(s, ref))) or store.verdict(ref, claim)
            if verdict and verdict.get("supported"):
                evidence.append({"ref": ref, "claim": claim, "quote": verdict["quote"],
                                 "section": verdict["section"], "full_text": verdict.get("full_text")})
            else:
                problems[s["n"]].append("the rewrite cites [%d] for a claim no paper agent confirmed" % ref)
        s["evidence"] = evidence
        s["tier"] = tiers.statement_tier(s, walker.record()["chain"])
    return problems


def _direction_pass(client, card_text, chain, statements, dropped, walker, store, read, checks):
    """Check 3 on the kept statements: objections go back to the Writer once,
    then the statement drops. Fills checks["gates"]["direction"]."""
    verdicts, objections = direction_mod.direction_check(client, statements, walker)
    for s in statements:
        s["direction"] = verdicts.get(s["n"])
    failing = [s for s in statements if objections.get(s["n"])]
    dropped_here = 0
    if failing:
        problems = _rewrite_and_recheck(client, card_text, chain, failing,
                                        {s["n"]: {"direction": "; ".join(objections[s["n"]])} for s in failing},
                                        walker, store, read)
        again, again_objections = direction_mod.direction_check(client, failing, walker)
        for s in failing:
            s["direction"] = again.get(s["n"], s.get("direction"))
            if s["n"] in again:
                verdicts[s["n"]] = again[s["n"]]
            if problems[s["n"]] or again_objections.get(s["n"]):
                statements.remove(s)
                dropped_here += 1
                dropped.append({"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                                "why": "; ".join(problems[s["n"]] + again_objections.get(s["n"], [])),
                                "by": "direction check"})
    checks["gates"]["direction"] = direction_mod.gate(verdicts, dropped_here)


def _sense_pass(client, card_text, chain, statements, dropped, walker, store, read, checks):
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
    problems = _rewrite_and_recheck(
        client, card_text, chain, failing,
        {s["n"]: {k: verdicts[s["n"]][k]["note"] for k in sense_mod.failed_fields(s["sense"])} for s in failing},
        walker, store, read)
    again = sense_mod.sense_check(client, card_text, failing, chain) or {}
    for s in failing:
        s["sense"] = again.get(s["n"], s["sense"])
        if problems[s["n"]] or sense_mod.failed_fields(s.get("sense")):
            statements.remove(s)
            dropped.append({"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                            "why": "; ".join(problems[s["n"]] + [
                                "%s: %s" % (k, s["sense"][k]["note"]) for k in sense_mod.failed_fields(s["sense"])]),
                            "by": "sense check"})


def _narrate(client, card_text, chain, statements, dropped, walker, papers, tag, checks, words, deadline=None,
             anchor=None, card=None, scope_name="Results"):
    kind = "network" if tag == "network" else "pathway"
    # Only the papers the kept statements cite, each with the claim a paper
    # agent confirmed in it: the Narrator retells statements, and a retrieved
    # paper no statement cites was never checked for the claim.
    confirmed = {}
    for s in statements:
        for item in s.get("evidence") or []:
            confirmed.setdefault(item["ref"], []).append(item["claim"])
    cited = {ref for s in statements for ref in verify.statement_refs(s)}
    papers_text = "\n".join("[%d] %s (%s) PMID %s%s" % (
        r, p.get("title"), p.get("year"), p.get("pmid"),
        "".join("\n    states: %s" % claim for claim in confirmed.get(r, [])))
        for r, p in sorted(papers.items()) if r in cited)
    title_gate = {"pass": True, "not_applicable": False, "verbs_found": [], "rewritten": False, "fallback": False,
                  "why": ""}

    def checked(results):
        if results is None:
            return ["no results"], []
        pruned = narrate_mod.prune_connectives(results)
        if pruned:
            checks["connectives_pruned"] = checks.get("connectives_pruned", 0) + pruned
        unquoted = verify.drop_unrecorded_sentences(results, walker)
        if unquoted:
            checks["misquoted_sentences_dropped"] = checks.get("misquoted_sentences_dropped", 0) + unquoted
        uncited = verify.drop_uncited_sentences(results, statements)
        if uncited:
            checks["uncited_sentences_dropped"] = checks.get("uncited_sentences_dropped", 0) + uncited
        jargon = verify.drop_jargon_sentences(results)
        if jargon:
            checks["jargon_sentences_dropped"] = checks.get("jargon_sentences_dropped", 0) + jargon
        title = tiers.title_outruns_body(results, statements, walker, anchor)
        return verify.verify_results(results, statements, dropped, walker, kind, words), title

    results = narrate_mod.narrate(client, card_text, statements, chain, papers_text, words)
    problems, title_problems = checked(results)
    title_gate["first_objections"] = list(title_problems)
    if results is not None:
        title_gate["verbs_found"] = sorted(set(tiers.mechanistic_verbs(
            " ".join([str(results.get("title") or ""), str(results.get("summary") or "")]))))
    if (problems or title_problems) and results is not None and (
            deadline is None or deadline - time.time() >= NARRATE_MIN_SECONDS):
        results = narrate_mod.narrate(client, card_text, statements, chain, papers_text, words,
                                      objections=problems + title_problems)
        problems, title_problems = checked(results)
        title_gate["rewritten"] = True
    if title_problems and results is not None and not problems:
        # Check 2's last word: the title code writes, and the summary without
        # the sentences that still outran the body.
        results["title"] = tiers.neutral_title(scope_name, card or {})
        removed = tiers.drop_mechanistic_sentences(results)
        title_gate.update({"fallback": True, "sentences_dropped": removed,
                           "why": "; ".join(title_problems)})
        title_problems = tiers.title_outruns_body(results, statements, walker, anchor)
        problems = verify.verify_results(results, statements, dropped, walker, kind, words)
    checks["results"] = problems
    if results is None or problems:
        checks["gates"]["title"] = {"pass": True, "not_applicable": True, "why": "no Results section to check"}
    else:
        title_gate["pass"] = not title_problems
        checks["gates"]["title"] = title_gate
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
                   "segments": len(walk.get("segments") or []),
                   "scans": walk.get("scans"), "refusals": walk.get("refusals"),
                   "seen_not_walked": len(walk.get("seen") or [])},
        "seen": [{"id": r["id"], "label": r["label"], "r": r["r"], "heat": r["heat"]} for r in seen],
        "nodes": {node_id: {"label": n.get("label"), "kind": n.get("kind"), "r": n.get("r"),
                            "heat": n.get("heat"), "text": n.get("text")} for node_id, n in nodes.items()},
        "segments": [dict(s) for s in walk.get("segments") or []],
        "statements": [{k: s.get(k) for k in ("n", "claim", "prose", "cites", "legs", "grounded_in", "beyond",
                                               "papers", "evidence", "sense", "rewritten", "tier", "direction")}
                       for s in rec.get("statements") or []],
        "dropped": [{k: d.get(k) for k in ("n", "claim", "why", "by")} for d in rec.get("dropped") or []],
        "results": rec.get("results"),
        "papers": {str(ref): p for ref, p in (rec.get("papers") or {}).items()},
        "checks": {"results": (rec.get("checks") or {}).get("results"),
                   "sense": (rec.get("checks") or {}).get("sense"),
                   "gates": (rec.get("checks") or {}).get("gates") or {},
                   "rendered": bool((rec.get("checks") or {}).get("rendered")),
                   "anchor": (rec.get("checks") or {}).get("anchor")},
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
    network, graph, ov, tag, _anchor, _dist = build(job, "network", data_dir, use_mongo)
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

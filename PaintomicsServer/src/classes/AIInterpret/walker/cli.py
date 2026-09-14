"""Run an Agentic Graph Walk on a stored job from the command line.

    cd PaintomicsServer
    PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job rlJ464WuvQ \
        --scope pathway:mmu04068 --policy greedy --out /tmp/walks
    PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job rlJ464WuvQ \
        --scope pathway:mmu04068 --policy model            # walker, Writer, checks, Narrator
    PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job rlJ464WuvQ \
        --scope pathway:mmu04068 --evaluate --plants 100   # Test 1, no model

Writes the sealed record (JSON) and a self-contained HTML report.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time

from src.conf.serverconf import (AI_LLM_PROVIDER, AI_PROVIDERS, CLIENT_TMP_DIR, KEGG_DATA_DIR,
                                 MONGODB_HOST, MONGODB_PORT)
from src.classes.AIInterpret.walker import card as card_mod
from src.classes.AIInterpret.walker import evaluate as eval_mod
from src.classes.AIInterpret.walker import narrate as narrate_mod
from src.classes.AIInterpret.walker import network as net_mod
from src.classes.AIInterpret.walker import overlay as ov_mod
from src.classes.AIInterpret.walker import policies
from src.classes.AIInterpret.walker import record as record_mod
from src.classes.AIInterpret.walker import report as report_mod
from src.classes.AIInterpret.walker import sense as sense_mod
from src.classes.AIInterpret.walker import verify
from src.classes.AIInterpret.walker.walk import Walker, params_for

logger = logging.getLogger(__name__)


def load_job(job_id):
    from src.common.JobInformationManager import JobInformationManager
    job = JobInformationManager().loadJobInstance(job_id)
    if job is None:
        raise SystemExit("job %s not found" % job_id)
    return job


def mongo_for(organism):
    try:
        from pymongo import MongoClient
        return MongoClient(MONGODB_HOST, MONGODB_PORT, serverSelectionTimeoutMS=2000)[organism + "-paintomics"]
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
    raise SystemExit("no pathway %s in the network (tags look like KEGG:mmu04068)" % wanted)


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


def run(job_id, scope, policy="greedy", out_dir=None, data_dir=None, writer=True, max_turns=60, use_mongo=True):
    timings = {}
    job = load_job(job_id)
    t0 = time.time()
    network, graph, ov, tag = build(job, scope, data_dir, use_mongo)
    timings["build"] = round(time.time() - t0, 1)
    labels = {name: ov.labels.get(name) for name in ov.labels}
    conditions = []
    try:
        conditions = list(job.conditionNames or [])
    except AttributeError:
        pass
    design_text = job.getExperimentDesign() if hasattr(job, "getExperimentDesign") else ""
    client = None
    if policy == "model":
        client = llm_client()
        t0 = time.time()
        card = card_mod.model_card(client, design_text, conditions, labels)
        timings["card"] = round(time.time() - t0, 1)
    else:
        card = card_mod.deterministic_card(design_text, conditions, labels)
    # The omics whose file carried no header: the walker may make no timing
    # claim on them until the user confirms the columns.
    card["unlabeled"] = ov_mod.relabel_unlabeled(ov)
    card_text = card_mod.card_text(card)
    walker = Walker(graph, ov, tag, params_for("network" if tag == "network" else "pathway"))
    t0 = time.time()
    model_used = "none (scripted %s policy)" % policy
    if policy == "model":
        from src.classes.AIInterpret.walker import sdk
        sdk.run_walk(walker, card_text, max_turns=max_turns)
        model_used = AI_PROVIDERS[AI_LLM_PROVIDER]["model"]
    elif policy == "random":
        policies.random_walk(walker)
    else:
        policies.greedy(walker)
    timings["walk"] = round(time.time() - t0, 1)
    statements, dropped, results, papers, checks = [], [], None, {}, {}
    if policy == "model" and writer and walker.chain:
        from src.classes.AIInterpret.pubmed_client import PubMedClient
        from src.classes.AIInterpret.walker import writer as writer_mod
        t0 = time.time()
        wctx = writer_mod.run_writer(walker, card_text, PubMedClient())
        timings["writer"] = round(time.time() - t0, 1)
        statements, dropped = wctx.kept, wctx.dropped
        papers = {ref: {"pmid": p.get("pmid"), "title": p.get("title"), "year": p.get("year"),
                        "journal": p.get("journal")} for ref, p in wctx.papers.items()}
        checks["writer_trace"] = wctx.trace
        chain = writer_mod.chain_text(walker)
        if statements:
            t0 = time.time()
            verdicts = sense_mod.sense_check(client, card_text, statements, chain)
            if verdicts is None:
                for s in statements:
                    s["sense"] = None
                checks["sense"] = "unavailable"
            else:
                for s in statements:
                    s["sense"] = verdicts.get(s["n"])
                failing = [s for s in statements if sense_mod.failed_fields(s.get("sense"))]
                if failing:
                    rewritten = rewrite_once(client, card_text, chain, failing, verdicts)
                    for s in failing:
                        new = rewritten.get(s["n"])
                        if new:
                            for key in ("claim", "prose", "cites", "legs", "grounded_in", "beyond", "papers"):
                                if key in new:
                                    s[key] = new[key]
                            s["rewritten"] = True
                    problems = {s["n"]: verify.verify_statement(s, walker, wctx.papers) for s in failing}
                    again = sense_mod.sense_check(client, card_text, failing, chain) or {}
                    still = []
                    for s in failing:
                        s["sense"] = again.get(s["n"], s["sense"])
                        if problems[s["n"]] or sense_mod.failed_fields(s.get("sense")):
                            still.append(s)
                    for s in still:
                        statements.remove(s)
                        dropped.append({"n": s["n"], "claim": s.get("claim"), "prose": s.get("prose"),
                                        "why": "; ".join(problems[s["n"]] + [
                                            "%s: %s" % (k, s["sense"][k]["note"]) for k in sense_mod.failed_fields(s["sense"])]),
                                        "by": "sense check"})
            timings["sense"] = round(time.time() - t0, 1)
        if statements:
            t0 = time.time()
            words = (300, 900) if tag == "network" else (150, 450)
            papers_text = "\n".join("[%d] %s (%s) PMID %s" % (r, p.get("title"), p.get("year"), p.get("pmid"))
                                    for r, p in sorted(papers.items()))
            results = narrate_mod.narrate(client, card_text, statements, chain, papers_text, words)
            problems = verify.verify_results(results, statements, dropped, walker,
                                             "network" if tag == "network" else "pathway") if results else ["no results"]
            if problems and results is not None:
                results = narrate_mod.narrate(client, card_text, statements, chain, papers_text, words, objections=problems)
                problems = verify.verify_results(results, statements, dropped, walker,
                                                 "network" if tag == "network" else "pathway") if results else ["no results"]
            checks["results"] = problems
            if problems:
                checks["results_dropped"] = results
                results = None
            timings["narrate"] = round(time.time() - t0, 1)
    rec = record_mod.seal(job_id, tag, graph, ov, walker, card, statements, dropped, results, papers,
                          model_used, timings, checks)
    out_dir = out_dir or os.path.join(CLIENT_TMP_DIR, "walks")
    json_path = record_mod.save(rec, out_dir)
    kgml, png = None, None
    if tag.startswith("KEGG:"):
        pid = tag.split(":", 1)[1]
        kgml = os.path.join(KEGG_DATA_DIR if not data_dir else data_dir, "current", job.getOrganism(), "kgml", pid + ".kgml")
        png = os.path.join(KEGG_DATA_DIR if not data_dir else data_dir, "current", "common", "png",
                           "map" + re.sub(r"[^0-9]", "", pid) + ".png")
    html_path = json_path[:-5] + ".html"
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(report_mod.render(rec, network if tag == "network" else graph, kgml, png))
    return rec, json_path, html_path


def run_evaluation(job_id, scope, out_dir=None, data_dir=None, plants=100, seed=0, use_mongo=True):
    job = load_job(job_id)
    network, graph, ov, tag = build(job, scope, data_dir, use_mongo)
    params = params_for("network" if tag == "network" else "pathway")
    t0 = time.time()
    out = eval_mod.grid(graph, ov, params, plants=plants, seed=seed)
    out["seconds"] = round(time.time() - t0, 1)
    out["job"], out["scope"] = job_id, tag
    out_dir = out_dir or os.path.join(CLIENT_TMP_DIR, "walks")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, "planted_%s_%s" % (job_id, tag.replace(":", "_")))
    with open(base + ".json", "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1)
    with open(base + ".html", "w", encoding="utf-8") as handle:
        handle.write(report_mod.render_curves(out, "Planted-module recall · %s · %s" % (job_id, tag)))
    return out, base + ".json", base + ".html"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", required=True)
    ap.add_argument("--scope", default="network", help='"network" or "pathway:<id>"')
    ap.add_argument("--policy", default="greedy", choices=("greedy", "random", "model"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--no-writer", action="store_true")
    ap.add_argument("--no-mongo", action="store_true", help="skip the OmniPath collection")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--evaluate", action="store_true", help="run Test 1 instead of a walk")
    ap.add_argument("--plants", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.evaluate:
        out, jpath, hpath = run_evaluation(args.job, args.scope, args.out, args.data_dir, args.plants,
                                           args.seed, not args.no_mongo)
        for cell in out["summary"]:
            print("m=%2d q=%.2f  seed hit %.2f  recall %.2f  precision %.2f  (%d plants)" % (
                cell["m"], cell["q"], cell["seed_hit"], cell["recall"], cell["precision"], cell["plants"]))
        print("verdict:", out["pass"] and ("PASS" if out["pass"]["recall"] and out["pass"]["seed_hit"] else "FAIL"))
        print(jpath, hpath, sep="\n")
        return 0
    rec, jpath, hpath = run(args.job, args.scope, args.policy, args.out, args.data_dir,
                            not args.no_writer, args.max_turns, not args.no_mongo)
    walk = rec["walk"]
    print("chain: %d legs · %s" % (len(walk["chain"]), rec["timings"]))
    print("statements: %d kept, %d dropped · results: %s" % (
        len(rec["statements"]), len(rec["dropped"]), "yes" if rec["results"] else "none"))
    print(jpath, hpath, sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

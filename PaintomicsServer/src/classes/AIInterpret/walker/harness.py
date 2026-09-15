"""The five checks measured offline, five times, at production temperatures.

    cd PaintomicsServer
    PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job Ku5jMVCL6z --five-checks \\
        --out /path/to/out --repeats 5 --permutations 20 --ko-job <simulated job id>

Every run's sealed record is saved before the next starts and a saved run is
never repeated, so a stopped harness resumes where it was. ``report`` turns the
saved runs into a Markdown table of ranges (min-max over the repeats) with a
PASS/FAIL per check. The gates inside every record are the same code the
server runs; the harness adds what only a set of runs can show -- a null
distribution, a decoy anchor, a panel of regulators, hand-labelled papers.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import statistics
import time

from src.classes.AIInterpret.walker import anchor as anchor_mod
from src.classes.AIInterpret.walker import direction as direction_mod
from src.classes.AIInterpret.walker import literature
from src.classes.AIInterpret.walker import overlay as ov_mod
from src.classes.AIInterpret.walker import regulators
from src.classes.AIInterpret.walker import service
from src.classes.AIInterpret.walker import simulate_ko
from src.classes.AIInterpret.walker.network import Network
from src.classes.AIInterpret.walker.walk import Walker, params_for

logger = logging.getLogger(__name__)

BENCH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "benchmarks", "tellme")
ALPHA = 0.05
PANEL_ACCURACY = 0.90
CONTEXT_AGREEMENT = 0.85
PAIR_CHOICE = 0.90
CHECKS = ("artifact", "title", "direction", "context", "anchor")
KO_GENE = "Pten"                # the gene the simulated knockout removes
TEMPERATURES = {"planner": 0.2, "walker": 0.2, "writer": 0.3, "narrator": 0.3, "sense": 0.1,
                "direction": 0.1, "paper_agent": 0.0}


# ---------------------------------------------------------------------- files
def _path(out_dir, part, name):
    folder = os.path.join(out_dir, part)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, name + ".json")


def _load(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def _bench(name):
    return _load(os.path.join(BENCH_DIR, name)) or {}


def _range(values, digits=3):
    values = [v for v in values if v is not None]
    if not values:
        return "—"
    lo, hi = min(values), max(values)
    fmt = "%%.%df" % digits if isinstance(lo, float) else "%d"
    return fmt % lo if lo == hi else "%s–%s" % (fmt % lo, fmt % hi)


# ------------------------------------------------------------------- the job
def load_job(job_id):
    from src.common.JobInformationManager import JobInformationManager
    job = JobInformationManager().loadJobInstance(job_id)
    if job is None:
        raise service.WalkError("job %s not found" % job_id)
    return job


class _PermutedFeature(object):
    """A feature that keeps its identity and takes another feature's measurements."""

    def __init__(self, own, donor):
        self._own, self._donor = own, donor

    def getID(self):
        return self._own.getID()

    def getName(self):
        return self._own.getName()

    def getOmicsValues(self):
        return self._donor.getOmicsValues()


class PermutedJob(object):
    """The job with every gene's measurements (values and flags together)
    moved to another gene, and every compound's to another compound: the
    null of check 1. Everything else about the job is the job's."""

    def __init__(self, job, seed):
        self._job = job
        rng = random.Random(seed)
        self._genes = self._permute(job.getInputGenesData() or {}, rng)
        self._compounds = self._permute(job.getInputCompoundsData() or {}, rng)

    @staticmethod
    def _permute(features, rng):
        keys = sorted(features)
        donors = list(keys)
        rng.shuffle(donors)
        return {key: _PermutedFeature(features[key], features[donor]) for key, donor in zip(keys, donors)}

    def getInputGenesData(self):
        return self._genes

    def getInputCompoundsData(self):
        return self._compounds

    def __getattr__(self, name):
        return getattr(self._job, name)


GATEWAY_WAIT_SECONDS = 300
GATEWAY_ATTEMPTS = 36               # three hours of waiting for the configured model


def _primary():
    provider = service.AI_PROVIDERS[service.AI_LLM_PROVIDER]
    return provider["api_base"].rstrip("/"), provider["model"], provider.get("api_key", "")


def gateway_answers():
    """Whether the CONFIGURED model answers a one-token request now. The
    fallback ladder keeps production alive when it does not, but a harness
    run answered by another model measures another model."""
    import requests
    api_base, model, key = _primary()
    try:
        reply = requests.post(api_base + "/chat/completions", timeout=60,
                              headers={"Authorization": "Bearer %s" % key, "Content-Type": "application/json"},
                              json={"model": model, "messages": [{"role": "user", "content": "Say ok."}],
                                    "max_tokens": 3})
        return reply.status_code == 200 and bool((reply.json().get("choices") or [{}])[0].get("message"))
    except Exception as exc:                                          # noqa: BLE001
        logger.warning("[harness] gateway probe failed: %s", exc)
        return False


def _run_saved(path, job, job_id, scope, card_override=None, data_dir=None):
    """service.run with the model policy, sealed to ``path`` (resumed when
    there). A run the gateway refused, or one in which a fallback model gave
    any answer, is not a measurement of the configured model: it is discarded
    and repeated once the configured model answers again."""
    from src.classes.AIInterpret import model_fallback
    rec = _load(path)
    if rec is not None:
        return rec
    _api_base, primary, _key = _primary()
    for attempt in range(1, GATEWAY_ATTEMPTS + 1):
        while not gateway_answers():
            logger.warning("[harness] %s is not answering; waiting %d s", primary, GATEWAY_WAIT_SECONDS)
            time.sleep(GATEWAY_WAIT_SECONDS)
        before = dict(model_fallback.ANSWERS)
        t0 = time.time()
        try:
            rec, _network, _graph, _tag = service.run(
                job, job_id, scope, policy="model", data_dir=data_dir, card_override=card_override,
                progress=lambda stage, pct, detail, w: logger.info(
                    "[harness] %s %3d%% %s", os.path.basename(path), pct, detail)
                if stage != "walk" or w is None or not w.chain else None)
        except service.WalkError as exc:
            logger.warning("[harness] %s refused (attempt %d): %s", os.path.basename(path), attempt, exc)
            time.sleep(GATEWAY_WAIT_SECONDS)
            continue
        answers = {model: n - before.get((base, model), 0)
                   for (base, model), n in model_fallback.ANSWERS.items() if n > before.get((base, model), 0)}
        others = {model: n for model, n in answers.items() if model != primary}
        if others:
            logger.warning("[harness] %s discarded: %s answered %s call(s); repeating once %s is back",
                           os.path.basename(path), ", ".join(others), sum(others.values()), primary)
            time.sleep(GATEWAY_WAIT_SECONDS)
            continue
        rec["harness"] = {"seconds": round(time.time() - t0, 1), "scope": scope, "card_override": card_override,
                          "answers": answers, "attempt": attempt}
        _save(path, rec)
        return rec
    raise RuntimeError("%s did not answer for %d attempts" % (primary, GATEWAY_ATTEMPTS))


def regate(path, job, scope, data_dir=None, permutation=None):
    """Recompute a sealed record's code-only artifact gate (and its rendered
    flag) with the current null, on the graph and overlay the run had:
    the walk, the statements and the model gates are left as sealed. Used
    when the null's definition of a module changes after runs were saved."""
    from src.classes.AIInterpret.walker.walk import Leg
    rec = _load(path)
    if rec is None:
        return None
    the_job = PermutedJob(job, permutation) if permutation is not None else job
    aliases = anchor_mod.alias_map(service.org_dir_for(job, data_dir))
    _network, graph, ov, tag, _anchor, _dist = service.build(the_job, scope, data_dir, True,
                                                             rec.get("design_card") or {}, aliases)
    walk = rec["walk"]
    walker = Walker(graph, ov, tag, dict(walk.get("params") or params_for("network" if tag == "network" else "pathway")))
    walker.plan = walk.get("plan")
    walker.chain = [Leg(l["n"], l["kind"], l["from"], l["to"], l.get("reading", ""), l.get("reason", ""), l.get("edge"))
                    for l in walk["chain"]]
    walker.segments = list(walk.get("segments") or [])
    gates = rec["checks"]["gates"]
    gates["artifact"] = service.artifact_gate(walker)
    rec["checks"]["rendered"] = all(bool(gates[name].get("pass")) for name in service.GATES)
    rec.setdefault("harness", {})["regated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save(path, rec)
    return rec


def regate_directory(out_dir, job_id, ko_job_id=None, data_dir=None):
    """regate every sealed record under a harness directory: real/ and perm/
    (the scope in the file name, the permutation index in perm/), anchor_example/
    on the network, anchor_ko/ on the knockout job. Yields (path, record)."""
    job = load_job(job_id)
    ko_job = load_job(ko_job_id) if ko_job_id else None
    for part in sorted(os.listdir(out_dir)):
        folder = os.path.join(out_dir, part)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder, name)
            stem, index = name[:-5].rsplit("_", 1)
            if part in ("real", "perm"):
                scope = stem.replace("pathway_", "pathway:") if stem.startswith("pathway_") else stem
                rec = regate(path, job, scope, data_dir, int(index) if part == "perm" else None)
            elif part == "anchor_ko" and ko_job is not None:
                rec = regate(path, ko_job, "network", data_dir)
            elif part.startswith("anchor_"):
                rec = regate(path, job, "network", data_dir)
            else:
                continue
            if rec is not None:
                yield path, rec


def _summary(rec):
    gates = (rec.get("checks") or {}).get("gates") or {}
    return {"statements": len(rec.get("statements") or []), "dropped": len(rec.get("dropped") or []),
            "mechanism": sum(1 for s in rec.get("statements") or [] if s.get("tier") == "mechanism"),
            "modules": ((gates.get("artifact") or {}).get("real") or {}).get("modules"),
            "legs": len((rec.get("walk") or {}).get("chain") or []),
            "rendered": bool((rec.get("checks") or {}).get("rendered")),
            "gates": {name: bool((gates.get(name) or {}).get("pass")) for name in CHECKS},
            "title": gates.get("title") or {}, "direction": gates.get("direction") or {},
            "context": gates.get("context") or {}, "anchor": gates.get("anchor") or {},
            "artifact": gates.get("artifact") or {}, "seconds": (rec.get("harness") or {}).get("seconds"),
            "results_title": (rec.get("results") or {}).get("title")}


# ------------------------------------------------------- check 1: the null
def run_artifact(job, job_id, scope, out_dir, repeats, permutations, data_dir=None):
    """Real runs and permuted-data runs of the whole pipeline; empirical p per
    real run on statements kept and modules found."""
    real = [_summary(_run_saved(_path(out_dir, "real", "%s_%d" % (scope.replace(":", "_"), i)),
                                job, job_id, scope, data_dir=data_dir)) for i in range(repeats)]
    perm = [_summary(_run_saved(_path(out_dir, "perm", "%s_%d" % (scope.replace(":", "_"), p)),
                                PermutedJob(job, p), job_id, scope, data_dir=data_dir)) for p in range(permutations)]

    def p_value(stat, value):
        null = [s[stat] or 0 for s in perm]
        return (1 + sum(1 for v in null if v >= (value or 0))) / float(len(null) + 1)

    for s in real:
        s["p_statements"] = round(p_value("statements", s["statements"]), 3)
        s["p_mechanism"] = round(p_value("mechanism", s["mechanism"]), 3)
        s["p_modules"] = round(p_value("modules", s["modules"]), 3)
    # "far fewer statements": the statements that assert a mechanism are the
    # ones that can be a graph artifact; associations only report values
    out = {"real": real, "perm": perm, "permutations": len(perm),
           "pass": all(s["p_mechanism"] < ALPHA and s["p_modules"] < ALPHA for s in real) and bool(real)}
    out["currency_legs"] = sum((s["artifact"].get("currency_legs") or 0) for s in real + perm)
    out["pass"] = out["pass"] and out["currency_legs"] == 0
    return out


# ---------------------------------------------------- check 2: the title
def run_title(real_summaries):
    """From the real runs: how often the first draft outran the body, and the final pass."""
    rows = [s["title"] for s in real_summaries]
    return {"first_draft_violations": sum(1 for t in rows if t.get("first_objections")),
            "rewritten": sum(1 for t in rows if t.get("rewritten")),
            "fallback": sum(1 for t in rows if t.get("fallback")),
            "verbs": sorted({v for t in rows for v in t.get("verbs_found") or []}),
            "runs": len(rows), "pass": all(t.get("pass", True) for t in rows)}


# ------------------------------------------------ check 3: the panel test
_PHRASES = {
    ("feedback", -1, "down"): "{gene} fell steadily ({values}), consistent with reduced {pathway} activity.",
    ("feedback", -1, "up"): "{gene} fell steadily ({values}), derepressing {pathway} signalling.",
    ("feedback", 1, "up"): "{gene} rose ({values}), reporting engagement of the {pathway} pathway.",
    ("feedback", 1, "down"): "{gene} rose ({values}), indicating that {pathway} signalling was shut down.",
    ("inhibitor", -1, "up"): "{gene} fell ({values}), releasing its brake on {pathway} signalling.",
    ("inhibitor", -1, "down"): "{gene} fell ({values}), so {pathway} signalling was reduced.",
    ("inhibitor", 1, "down"): "{gene} rose ({values}), restraining {pathway} activity.",
    ("inhibitor", 1, "up"): "{gene} rose ({values}), driving {pathway} activity up.",
}


def _panel_walker(symbol, values):
    """A two-node graph with the panel gene measured, walked one step."""
    net = Network("harness")
    net.add_node("g:1", "Origin", "gene")
    net.add_node("g:2", symbol, "gene")
    net.add_edge("g:1", "g:2", 1, "KEGG:harness", "activation")
    ov = ov_mod.Overlay()
    ov.measured = {"g:1", "g:2"}
    ov.r = {"g:1": True, "g:2": True}
    labels = ["0h", "2h", "6h", "12h"]
    ov.labels = {"Gene expression": labels}
    for node, member, vals in (("g:1", "Origin", [0.1, 0.9, 1.2, 1.4]), ("g:2", symbol, values)):
        ov.layers[node] = [{"omic": "Gene expression", "member": member, "relevant": True, "values": vals,
                            "text": ov_mod.values_text(vals, labels)}]
    from src.classes.AIInterpret.walker import heat as heat_mod
    ov.heat = heat_mod.compute_heat(net, ov.measured, ov.r)
    ov.N, ov.K = 2, 2
    walker = Walker(net, ov, "KEGG:harness", params_for("pathway"))
    walker.start_at("g:1", 3, "harness")
    walker.step("g:2", "%s Gene expression %s" % (symbol, ov.layers["g:2"][0]["text"]), "harness")
    return walker, ov


def direction_cases(rows=None):
    """Two statements per panel gene and value direction: one claiming what
    the regulator implies, one claiming the opposite."""
    cases = []
    for row in rows or regulators.PANEL:
        for sign in (-1, 1):
            values = [0.1, -0.8, -1.9, -2.2] if sign < 0 else [0.1, 0.8, 1.9, 2.2]
            implied = regulators.implied_direction(row, sign)
            for claimed in ("up", "down"):
                text = _PHRASES[(row["class"], sign, claimed)].format(
                    gene=row["symbol"], pathway=row["pathway"],
                    values=ov_mod.values_text(values, ["0h", "2h", "6h", "12h"]))
                cases.append({"gene": row["symbol"], "class": row["class"], "pathway": row["pathway"], "sign": sign,
                              "values": values, "claimed": claimed, "expected_consistent": claimed == implied,
                              "statement": text})
    return cases


def _direction_case(client, case):
    """One constructed statement through the direction check, and the same
    statement over reversed values (the injected flip)."""
    walker, _ov = _panel_walker(case["gene"], case["values"])
    stmt = {"n": 1, "claim": case["statement"], "prose": case["statement"],
            "cites": [[case["gene"], "Gene expression"]], "legs": [1]}
    verdicts, objections = direction_mod.direction_check(client, [stmt], walker)
    verdict = (verdicts.get(1) or [{}])[0]
    flip_flagged = None
    if case["expected_consistent"]:
        flipped_walker, _ = _panel_walker(case["gene"], [-v for v in case["values"]])
        _fv, flipped_objections = direction_mod.direction_check(client, [stmt], flipped_walker)
        flip_flagged = bool(flipped_objections.get(1))
    return dict(case, consistent=verdict.get("consistent"), claimed_read=verdict.get("claimed"),
                insensitive=verdict.get("insensitive"), objected=bool(objections.get(1)), flip_flagged=flip_flagged)


def run_direction(client, out_dir, repeats, rows=None):
    """Accuracy of the direction check on the constructed panel, per class,
    over ``repeats`` model samples; injected sign flips must be flagged."""
    cases = direction_cases(rows)
    results = []
    for i in range(repeats):
        path = _path(out_dir, "direction", "panel_%d" % i)
        saved = _load(path)
        if saved is None:
            # Six cases at a time: each is three short calls, and the gateway
            # is paced per minute, so a serial pass took over half an hour.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=6) as pool:
                saved = list(pool.map(lambda case: _direction_case(client, case), cases))
            _save(path, saved)
        results.append(saved)

    def accuracy(group, cls):
        rows_ = [c for c in group if c["class"] == cls and c.get("consistent") is not None]
        return sum(1 for c in rows_ if (not c["objected"]) == c["expected_consistent"]) / float(len(rows_)) if rows_ else None

    per_run = [{"feedback": accuracy(g, "feedback"), "inhibitor": accuracy(g, "inhibitor"),
                "flips_flagged": (sum(1 for c in g if c.get("flip_flagged")) /
                                  float(max(1, sum(1 for c in g if c.get("flip_flagged") is not None))))} for g in results]
    return {"cases": len(cases), "runs": per_run,
            "pass": all((r["feedback"] or 0) > PANEL_ACCURACY and (r["inhibitor"] or 0) > PANEL_ACCURACY
                        and r["flips_flagged"] >= 0.9 for r in per_run) and bool(per_run)}


# ------------------------------------------------ check 4: context labels
def _agree(axis, label, found):
    label, found = str(label or "unknown").lower(), str(found or "unknown").lower()
    if axis == "system":
        if label in ("unknown", "") and found in ("unknown", ""):
            return True
        words = [w for w in re.split(r"[\s,/()-]+", label) if len(w) >= 4]
        return any(w in found or found.rstrip("s") in label for w in words)
    return label == found


def run_context(client, out_dir, repeats):
    """Agreement of the MeSH-read context with the hand labels, and the in-
    context paper chosen in the constructed pairs."""
    from src.classes.AIInterpret.pubmed_client import PubMedClient
    pubmed = PubMedClient()
    labels = _bench("context_labels.json").get("papers") or []
    path = _path(out_dir, "context", "labels")
    agreement = _load(path)
    if agreement is None:
        agreement = {"papers": [], "organism": None, "system": None, "scope": None}
        for row in labels:
            fetched = pubmed.fetch_abstracts([str(row["pmid"])])
            ctx = literature.paper_context(fetched[0]) if fetched else {"organism": "unknown", "system": "unknown",
                                                                          "scope": "unknown"}
            agreement["papers"].append({"pmid": row["pmid"], "label": {k: row.get(k) for k in ("organism", "system", "scope")},
                                        "found": ctx,
                                        "agree": {k: _agree(k, row.get(k), ctx.get(k)) for k in ("organism", "system", "scope")}})
        for axis in ("organism", "system", "scope"):
            rows_ = agreement["papers"]
            agreement[axis] = round(sum(1 for r in rows_ if r["agree"][axis]) / float(len(rows_)), 3) if rows_ else None
        _save(path, agreement)
    pairs = _bench("context_pairs.json")
    card = pairs.get("card") or {"organism": "mouse", "system": ""}
    chosen = []
    for i in range(repeats):
        ppath = _path(out_dir, "context", "pairs_%d" % i)
        saved = _load(ppath)
        if saved is None:
            saved = []
            rng = random.Random(i)
            for pair in pairs.get("pairs") or []:
                records = {}
                for side in ("in_context", "out_of_context"):
                    fetched = pubmed.fetch_abstracts([str(pair[side]["pmid"])])
                    records[side] = fetched[0] if fetched else {"pmid": pair[side]["pmid"], "title": pair[side].get("title")}
                order = ["in_context", "out_of_context"]
                rng.shuffle(order)
                listed = literature.rank_by_context([records[s] for s in order], card)
                lines = []
                for n, paper in enumerate(listed, 1):
                    tag = literature.context_line(paper.get("context") or {})
                    lines.append("[%d] %s (%s)%s" % (n, paper.get("title"), paper.get("year", "?"),
                                                    " [%s]" % tag if tag else ""))
                prompt = ("DESIGN: %s, %s.\nCLAIM TO CITE: %s\nRETRIEVED PAPERS (in-context first):\n%s\n"
                          "Answer {\"cite\": N} with the paper you cite for this claim in this design."
                          % (card.get("organism"), card.get("system"), pair["claim"], "\n".join(lines)))
                try:
                    out = client.complete_json(
                        [{"role": "system", "content": "You are a Writer choosing which retrieved paper to cite. "
                          + "Context matters: prefer a paper from the design's organism and system; when you cite one "
                          + "from another, you must say so in the sentence."},
                         {"role": "user", "content": prompt}], "pair_choice",
                        {"type": "object", "properties": {"cite": {"type": "integer"}}, "required": ["cite"],
                         "additionalProperties": False}, lambda text: None, max_tokens=60, temperature=0.3)
                    cite = int(out.get("cite")) if isinstance(out, dict) else None
                except Exception:                                     # noqa: BLE001
                    cite = None
                picked = listed[cite - 1] if cite and 1 <= cite <= len(listed) else None
                in_context = picked is not None and str(picked.get("pmid")) == str(pair["in_context"]["pmid"])
                saved.append({"claim": pair["claim"], "cite": cite, "in_context": in_context,
                              "order": [str(records[s].get("pmid")) for s in order]})
            _save(ppath, saved)
        chosen.append(sum(1 for s in saved if s["in_context"]) / float(len(saved)) if saved else None)
    return {"labels": len(labels), "agreement": {k: agreement[k] for k in ("organism", "system", "scope")},
            "pairs": len(pairs.get("pairs") or []), "in_context_chosen": chosen,
            "pass": all((agreement[k] or 0) > CONTEXT_AGREEMENT for k in ("organism", "system", "scope"))
            and all((c or 0) > PAIR_CHOICE for c in chosen) and bool(chosen)}


# ----------------------------------------------- check 5: the anchor test
def _anchor_metrics(rec, network, ov, anchor_node, targets, aliases):
    """Reachability to the TRUE anchor and known-target enrichment of one
    record, whatever the run was anchored on."""
    dist = anchor_mod.distances(network, anchor_node)
    nodes = anchor_mod.walked_nodes((rec.get("walk") or {}).get("chain") or [])
    return {"reachability": round(anchor_mod.reachability(nodes, dist), 3),
            "enrichment": anchor_mod.target_enrichment(nodes, targets, ov, network, aliases), "nodes": len(nodes)}


def run_anchor(job, job_id, out_dir, repeats, gene, targets, data_dir=None, decoy=None, label="example"):
    """Anchored, decoy-anchored and unanchored network walks, ``repeats`` each;
    every record measured against the TRUE anchor."""
    org_dir = service.org_dir_for(job, data_dir)
    aliases = anchor_mod.alias_map(org_dir)
    rows = anchor_mod.load_tf_targets(org_dir)
    network, _graph, ov, _tag, anchor, _dist = service.build(
        job, "network", data_dir, True, {"perturbed_genes": [gene], "perturbation_direction": "unknown"}, aliases)
    if anchor is None or not anchor.get("in_graph"):
        return {"pass": False, "why": "%s is not connected in the network" % gene, "gene": gene}
    decoy = decoy or anchor_mod.decoy_for(anchor, rows, network, random.Random(7), aliases)
    arms = {"anchored": {"perturbed_genes": [gene]},
            "decoy": {"perturbed_genes": [decoy] if decoy else []},
            "unanchored": {"perturbed_genes": []}}
    out = {"gene": gene, "decoy": decoy, "arms": {}, "targets": len(targets)}
    for arm, override in arms.items():
        metrics = []
        for i in range(repeats):
            rec = _run_saved(_path(out_dir, "anchor_" + label, "%s_%d" % (arm, i)), job, job_id, "network",
                             card_override=dict(override, perturbation_direction="up" if arm == "anchored" else "unknown"),
                             data_dir=data_dir)
            m = _anchor_metrics(rec, network, ov, anchor["node"], targets, aliases)
            m["summary"] = _summary(rec)
            metrics.append(m)
        out["arms"][arm] = metrics
    reach = {arm: [m["reachability"] for m in ms] for arm, ms in out["arms"].items()}
    enrich = {arm: [m["enrichment"]["p"] for m in ms] for arm, ms in out["arms"].items()}
    others_reach = reach["decoy"] + reach["unanchored"]
    out["reachability"] = reach
    out["enrichment_p"] = enrich
    out["pass"] = bool(reach["anchored"]) and min(reach["anchored"]) > max(others_reach or [0.0]) and \
        statistics.median(enrich["anchored"]) < min(statistics.median(enrich["decoy"] or [1.0]),
                                                    statistics.median(enrich["unanchored"] or [1.0]))
    return out


def ko_targets(job, data_dir=None):
    """The genes the simulated knockout planted: what an anchored walk should reach."""
    network, _graph, _ov, _tag, _anchor, _dist = service.build(job, "network", data_dir, True)
    node = "g:" + network.symbol_to_kegg[KO_GENE.upper()]
    planted = simulate_ko.propagate(network, node)
    return [network.nodes[v]["label"] for v in planted if v != node and v in network.nodes]


# ------------------------------------------------------ the knockout job
KO_DATASET = "13-simulated-pten-knockout"
KO_DESIGN = ("Pten knockout in mouse: KO over wild type, log2 fold change at six time points after the "
             "knockout (0h, 2h, 6h, 12h, 18h, 24h). Simulated from the mouse interaction network: the "
             "knocked-out gene is set to -2.5 and its sign propagates two steps along signed edges.")


def create_job_from_dataset(dataset_dir, design_text, name, organism="mmu"):
    """A stored pathway-acquisition job built from an unlisted dataset
    directory, the way the benchmark runner builds one from the manifest --
    steps 1 to 3 through the job's own methods -- except that the job is kept
    and its AI consent is on, so the walk (and the browser) can open it.
    Returns the job id."""
    import multiprocessing
    import shutil
    import tempfile
    import uuid

    from src.common import DatabaseAvailability, ExampleDatasets
    from src.common.JobInformationManager import JobInformationManager
    from src.common.KeggInformationManager import KeggInformationManager
    from src.conf.serverconf import CLIENT_TMP_DIR, KEGG_DATA_DIR
    from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob

    try:
        multiprocessing.set_start_method("fork")          # the mappers fork, as on the server
    except RuntimeError:
        pass
    KeggInformationManager(KEGG_DATA_DIR)
    # the server's src/ root, as the servlets pass it to the metagene step
    src_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")) + os.sep
    # ExampleDatasets refuses files outside its root: copy the dataset under a
    # temporary root with a one-scenario manifest.
    root = tempfile.mkdtemp(prefix="ko_job_")
    try:
        folder = os.path.join(root, "datasets", KO_DATASET)
        shutil.copytree(os.path.join(dataset_dir, "data"), os.path.join(folder, "data"))
        scenario = {"id": "ko", "organism": organism, "pipeline": "pathway-acquisition",
                    "databases": DatabaseAvailability.resolveDatabases(organism), "omics": [
                        {"omicName": "Gene expression", "omicType": "gene", "enrichment": "genes",
                         "dataFile": "datasets/%s/data/gene_expression_values.tab" % KO_DATASET,
                         "relevantFile": "datasets/%s/data/gene_expression_relevant.tab" % KO_DATASET}]}
        with open(os.path.join(root, "datasets", "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump({"version": ExampleDatasets.SUPPORTED_VERSION, "defaultScenario": "ko",
                       "scenarios": [scenario]}, handle)
        job_id = "KO" + uuid.uuid4().hex[:8]
        job = PathwayAcquisitionJob(job_id, None, CLIENT_TMP_DIR)
        job.initializeDirectories()
        ExampleDatasets.applyScenario(job, root + os.sep, "ko")
        job.setDatabases(DatabaseAvailability.resolveDatabases(organism))
        job.setName(name[:100])
        job.setAIConsent("true")
        job.setExperimentDesign(design_text)
        job.validateInput()
        job.processFilesContent()
        job.setLastStep(2)
        job.getJobDescription(True, True)
        JobInformationManager().storeJobInstance(job, 1)
        job.cleanDirectories()
        job2 = JobInformationManager().loadJobInstance(job_id)
        job2.setDirectories(CLIENT_TMP_DIR)
        job2.initializeDirectories()
        job2.updateSubmitedCompoundsList([])
        job2.generatePathwaysList()
        job2.getGlobalExpressionData()
        job2.parseRegulationPerCondition()
        try:
            job2.generateMetagenesList(src_root, {})
        except Exception as exc:                                       # noqa: BLE001
            logger.warning("[harness] metagenes skipped for the knockout job: %s", exc)
        job2.setLastStep(3)
        JobInformationManager().storeJobInstance(job2, 2)
        selected = sorted((job2.getMatchedPathways() or {}).keys())
        job2.generateSelectedPathwaysInformation(selected, [], True)
        JobInformationManager().storeJobInstance(job2, 3)
        job2.cleanDirectories()
        logger.info("[harness] job %s stored: %d pathways matched", job_id, len(selected))
        return job_id
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------ report
def run_five(job_id, out_dir, repeats=5, permutations=20, scope="pathway:mmu04068", ko_job_id=None,
             data_dir=None, only=None, decoy=None):
    """Every check, saved under ``out_dir``; returns the summary dict."""
    only = set(only or CHECKS + ("ko",))
    job = load_job(job_id)
    client = service.llm_client()
    summary = {"job": job_id, "scope": scope, "repeats": repeats, "permutations": permutations,
               "temperatures": TEMPERATURES, "model": service.AI_PROVIDERS[service.AI_LLM_PROVIDER]["model"],
               "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    spath = os.path.join(out_dir, "summary.json")
    saved = _load(spath) or {}
    summary.update({k: v for k, v in saved.items() if k in CHECKS or k == "ko"})
    if "artifact" in only:
        summary["artifact"] = run_artifact(job, job_id, scope, out_dir, repeats, permutations, data_dir)
        summary["title"] = run_title(summary["artifact"]["real"])
        _save(spath, summary)
    if "direction" in only:
        summary["direction"] = run_direction(client, out_dir, repeats)
        _save(spath, summary)
    if "context" in only:
        summary["context"] = run_context(client, out_dir, repeats)
        _save(spath, summary)
    if "anchor" in only:
        targets = [t["symbol"] for t in _bench("ikaros_targets.json").get("targets") or []]
        summary["anchor"] = run_anchor(job, job_id, out_dir, repeats, "Ikzf1", targets, data_dir, decoy)
        _save(spath, summary)
    if ("ko" in only or "anchor" in only) and ko_job_id:
        ko_job = load_job(ko_job_id)
        summary["ko"] = run_anchor(ko_job, ko_job_id, out_dir, repeats, KO_GENE,
                                   ko_targets(ko_job, data_dir), data_dir, decoy=None, label="ko")
        _save(spath, summary)
    summary["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save(spath, summary)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(report(summary))
    return summary


def report(summary):
    """The Markdown report: one table per check, every number a range."""
    lines = ["# Five checks before render — measured", "",
             "Job `%s`, scope `%s`, %s repeats at production temperatures (%s), model `%s`. Started %s." % (
                 summary.get("job"), summary.get("scope"), summary.get("repeats"),
                 ", ".join("%s %s" % kv for kv in TEMPERATURES.items()), summary.get("model"), summary.get("started")),
             ""]
    art = summary.get("artifact")
    if art:
        real, perm = art["real"], art["perm"]
        lines += ["## 1 · Not a graph artifact — %s" % ("PASS" if art["pass"] else "FAIL"), "",
                  "| | real runs (%d) | permuted runs (%d) |" % (len(real), len(perm)), "|---|---|---|",
                  "| statements kept | %s | %s |" % (_range([s["statements"] for s in real]), _range([s["statements"] for s in perm])),
                  "| mechanism statements kept | %s | %s |" % (_range([s["mechanism"] for s in real]), _range([s["mechanism"] for s in perm])),
                  "| modules found | %s | %s |" % (_range([s["modules"] for s in real]), _range([s["modules"] for s in perm])),
                  "| legs | %s | %s |" % (_range([s["legs"] for s in real]), _range([s["legs"] for s in perm])),
                  "| empirical p, statements | %s | |" % _range([s["p_statements"] for s in real]),
                  "| empirical p, mechanism statements | %s | |" % _range([s["p_mechanism"] for s in real]),
                  "| empirical p, modules | %s | |" % _range([s["p_modules"] for s in real]),
                  "| request-time null p (modules) | %s | %s |" % (
                      _range([s["artifact"].get("p_modules") for s in real]), _range([s["artifact"].get("p_modules") for s in perm])),
                  "| legs through a currency metabolite | %d | |" % art["currency_legs"], ""]
    title = summary.get("title")
    if title:
        lines += ["## 2 · Title fits the body — %s" % ("PASS" if title["pass"] else "FAIL"), "",
                  "| first drafts that outran the body | rewritten | replaced by code | verbs seen |", "|---|---|---|---|",
                  "| %d of %d | %d | %d | %s |" % (title["first_draft_violations"], title["runs"], title["rewritten"],
                                                   title["fallback"], ", ".join(title["verbs"]) or "none"), ""]
    direction = summary.get("direction")
    if direction:
        lines += ["## 3 · Direction logic — %s" % ("PASS" if direction["pass"] else "FAIL"), "",
                  "| %d constructed cases | feedback reporters | true inhibitors | injected flips flagged |" % direction["cases"],
                  "|---|---|---|---|",
                  "| accuracy over %d runs | %s | %s | %s |" % (
                      len(direction["runs"]), _range([r["feedback"] for r in direction["runs"]]),
                      _range([r["inhibitor"] for r in direction["runs"]]), _range([r["flips_flagged"] for r in direction["runs"]])), ""]
    context = summary.get("context")
    if context:
        lines += ["## 4 · Citations in context — %s" % ("PASS" if context["pass"] else "FAIL"), "",
                  "| %d hand-labelled papers | organism | system | scope | in-context paper chosen (%d pairs) |" % (
                      context["labels"], context["pairs"]), "|---|---|---|---|---|",
                  "| agreement | %s | %s | %s | %s |" % (context["agreement"]["organism"], context["agreement"]["system"],
                                                          context["agreement"]["scope"], _range(context["in_context_chosen"])), ""]
    for key, name in (("anchor", "the example (Ikzf1)"), ("ko", "the simulated knockout (%s)" % KO_GENE)):
        arm = summary.get(key)
        if not arm:
            continue
        lines += ["## 5 · Anchored to the perturbation, %s — %s" % (name, "PASS" if arm.get("pass") else "FAIL"), ""]
        if "arms" not in arm:
            lines += [arm.get("why", ""), ""]
            continue
        lines += ["| arm | reachability (≤ 2 steps of %s) | known-target enrichment p | statements | modules |" % arm["gene"],
                  "|---|---|---|---|---|"]
        for a, ms in arm["arms"].items():
            lines.append("| %s%s | %s | %s | %s | %s |" % (
                a, " (%s)" % arm["decoy"] if a == "decoy" else "", _range([m["reachability"] for m in ms]),
                _range([m["enrichment"]["p"] for m in ms], 4), _range([m["summary"]["statements"] for m in ms]),
                _range([m["summary"]["modules"] for m in ms])))
        lines.append("")
    verdicts = [summary.get(k, {}).get("pass") for k in CHECKS if summary.get(k)]
    lines += ["## Verdict", "", "%d of %d checks pass on every repeat." % (sum(1 for v in verdicts if v), len(verdicts)), ""]
    return "\n".join(lines)

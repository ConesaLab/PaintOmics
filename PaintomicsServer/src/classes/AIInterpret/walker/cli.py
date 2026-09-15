"""Run an Agentic Graph Walk on a stored job from the command line.

    cd PaintomicsServer
    PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job rlJ464WuvQ \
        --scope pathway:mmu04068 --policy greedy --out /tmp/walks
    PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job rlJ464WuvQ \
        --scope pathway:mmu04068 --policy model            # walker, Writer, checks, Narrator
    PYTHONPATH=. python -m src.classes.AIInterpret.walker.cli --job rlJ464WuvQ \
        --scope pathway:mmu04068 --evaluate --plants 100   # Test 1, no model

Writes the sealed record (JSON) and a self-contained HTML report. The pipeline
itself is walker.service.run, the same one the server's queued walk runs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time

from src.conf.serverconf import CLIENT_TMP_DIR, KEGG_DATA_DIR
from src.classes.AIInterpret.walker import evaluate as eval_mod
from src.classes.AIInterpret.walker import record as record_mod
from src.classes.AIInterpret.walker import report as report_mod
from src.classes.AIInterpret.walker import service
from src.classes.AIInterpret.walker.walk import params_for

logger = logging.getLogger(__name__)


def load_job(job_id):
    from src.common.JobInformationManager import JobInformationManager
    job = JobInformationManager().loadJobInstance(job_id)
    if job is None:
        raise service.WalkError("job %s not found" % job_id)
    return job


def _log_stage(stage, percent, detail, walker):
    """Stage lines on the console; the walk's per-turn updates stay quiet."""
    if stage != "walk" or walker is None or not walker.chain:
        logger.info("[walker] %3d%% %s: %s", percent, stage, detail)


def run(job_id, scope, policy="greedy", out_dir=None, data_dir=None, writer=True, use_mongo=True):
    job = load_job(job_id)
    rec, network, graph, tag = service.run(
        job, job_id, service.check_scope(scope), policy=policy, data_dir=data_dir, writer=writer,
        use_mongo=use_mongo, progress=_log_stage)
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
    network, graph, ov, tag, _anchor, _dist = service.build(job, service.check_scope(scope), data_dir, use_mongo)
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
    ap.add_argument("--job", default=None, help="the stored job (required except with --make-ko-job)")
    ap.add_argument("--scope", default="network", help='"network" or "pathway:<id>"')
    ap.add_argument("--policy", default="greedy", choices=("greedy", "random", "model"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--no-writer", action="store_true")
    ap.add_argument("--no-mongo", action="store_true", help="skip the OmniPath collection")
    ap.add_argument("--evaluate", action="store_true", help="run Test 1 instead of a walk")
    ap.add_argument("--five-checks", action="store_true",
                    help="run the five-check harness (model runs, permutations, decoys, the panel) instead of a walk")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--permutations", type=int, default=20)
    ap.add_argument("--ko-job", default=None, help="the simulated knockout job for the anchor test")
    ap.add_argument("--only", default=None, help="comma list of checks to run: artifact,direction,context,anchor")
    ap.add_argument("--decoy", default=None, help="the decoy transcription factor for the anchor test")
    ap.add_argument("--regate", default=None,
                    help="recompute the code-only artifact gate of every sealed record under this harness directory")
    ap.add_argument("--make-ko-job", action="store_true",
                    help="store the simulated knockout dataset as a job and print its id")
    ap.add_argument("--plants", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    if not args.job and not args.make_ko_job:
        ap.error("--job is required")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return _main(args)
    except service.WalkError as exc:
        print("walk refused: %s" % exc, file=sys.stderr)
        return 2


def _main(args):
    if args.regate:
        from src.classes.AIInterpret.walker import harness
        for path, rec in harness.regate_directory(args.regate, args.job, args.ko_job, args.data_dir):
            gate = rec["checks"]["gates"]["artifact"]
            print("%s  modules %d (walk %d)  p_modules %.3f  p_heat %.3f  %s" % (
                os.path.relpath(path, args.regate), gate["real"]["modules"], gate["walk"]["modules"],
                gate["p_modules"], gate["p_heat"], "pass" if gate["pass"] else "FAIL"))
        return 0
    if args.make_ko_job:
        from src.classes.AIInterpret.walker import harness
        dataset = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "examplefiles",
                               "datasets", harness.KO_DATASET)
        print(harness.create_job_from_dataset(os.path.normpath(dataset), harness.KO_DESIGN,
                                              "Simulated Pten knockout (five-check harness)"))
        return 0
    if args.five_checks:
        from src.classes.AIInterpret.walker import harness
        out_dir = args.out or os.path.join(CLIENT_TMP_DIR, "walks", "five-checks")
        summary = harness.run_five(args.job, out_dir, repeats=args.repeats, permutations=args.permutations,
                                   scope=args.scope if args.scope != "network" else "pathway:mmu04068",
                                   ko_job_id=args.ko_job, data_dir=args.data_dir,
                                   only=args.only.split(",") if args.only else None, decoy=args.decoy)
        print(harness.report(summary))
        print(os.path.join(out_dir, "report.md"))
        return 0
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
                            not args.no_writer, not args.no_mongo)
    walk = rec["walk"]
    print("chain: %d legs · %s" % (len(walk["chain"]), rec["timings"]))
    print("statements: %d kept, %d dropped · results: %s" % (
        len(rec["statements"]), len(rec["dropped"]), "yes" if rec["results"] else "none"))
    print(jpath, hpath, sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

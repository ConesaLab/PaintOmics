#!/usr/bin/env python3
"""Drive a LIVE PaintOmics server through the pathway-acquisition example
scenarios over HTTP -- the way the browser does -- and record wall-clock
timings plus the step-1/2/3 responses in the same artifact layout as
bench_runner, so two servers (or one server before and after a deploy) can
be compared with bench_compare.

    python -m src.benchmarks.bench_http --server https://paintomics.org \
        --out /tmp/org-before --scenarios stategra-multiomics gene-single-condition

Only what the client would see is captured: the final /check_job_status
payloads of step 1 and step 2 and the /pa_step3 response for every matched
pathway. Timings are per step, queue wait included (this is what a user
experiences), plus the JobProgress phase breakdown reported by the server
where available.
"""
import argparse
import gzip
import json
import os
import time

import requests

# Response fields that are per-run and expected to differ.
_VOLATILE = {"jobID", "timestamp", "userID"}


def _selectedCompounds(matchedMetabolites):
    """Replicate the client's default compound selection (JobController.js
    dedup by similarity, then PA_Step2Views.getSelectedCompounds)."""
    sets = json.loads(json.dumps(matchedMetabolites))
    winners = {}
    for compoundSet in sets:
        for compound in compoundSet.get("mainCompounds", []):
            compound["selected"] = False
            kept = winners.get(compound.get("ID"))
            if kept is None or compound.get("similarity", 0) >= kept.get("similarity", 0):
                if kept is not None:
                    kept["selected"] = False
                compound["selected"] = True
                winners[compound.get("ID")] = compound
    selected = []
    for compoundSet in sets:
        for key in ("mainCompounds", "otherCompounds"):
            for compound in compoundSet.get(key, []):
                if compound.get("selected") is True:
                    selected.append("%s#%s#%s" % (compound.get("ID"), compound.get("name"),
                                                  compoundSet.get("title")))
    return selected


class Client(object):
    def __init__(self, server, timeout=1800, verify=True):
        self.server = server.rstrip("/")
        self.session = requests.Session()
        self.timeout = timeout
        self.verify = verify
        self.progress = {}

    def post(self, path, data=None):
        response = self.session.post(self.server + path, data=data, timeout=120,
                                     verify=self.verify)
        response.raise_for_status()
        return response.json()

    def waitForJob(self, jobID, label):
        started = time.time()
        lastProgress = None
        while True:
            response = self.session.post(self.server + "/check_job_status/" + jobID,
                                         timeout=120, verify=self.verify)
            try:
                payload = response.json()
            except ValueError:
                raise RuntimeError("non-JSON status for %s: %s" % (jobID, response.text[:200]))
            if payload.get("success") is True and "status" not in payload:
                return payload
            if response.status_code >= 400 or payload.get("status") in ("failed", "JobStatus.FAILED"):
                raise RuntimeError("job %s failed at %s: %s" % (jobID, label, payload.get("message")))
            if payload.get("progress"):
                lastProgress = payload["progress"]
                self.progress[label] = lastProgress
            if time.time() - started > self.timeout:
                raise RuntimeError("timeout waiting for %s (%s)" % (jobID, label))
            time.sleep(1.0)


def runScenario(client, scenarioId):
    timings = {}
    artifacts = {}

    t0 = time.time()
    step1 = client.post("/pa_step1/example/" + scenarioId)
    jobID = step1["jobID"]
    step1Result = client.waitForJob(jobID, "step1")
    timings["step1"] = round(time.time() - t0, 2)
    artifacts["step1"] = {k: v for k, v in step1Result.items() if k not in _VOLATILE}

    selectedCompounds = _selectedCompounds(step1Result.get("matchedMetabolites", []))
    form = [("jobID", jobID)] + [("selectedCompounds[]", c) for c in selectedCompounds]
    t0 = time.time()
    client.post("/pa_step2", data=form)
    step2Result = client.waitForJob(jobID, "step2")
    timings["step2"] = round(time.time() - t0, 2)
    artifacts["step2"] = {k: v for k, v in step2Result.items() if k not in _VOLATILE}
    artifacts["step2"]["selectedCompounds"] = selectedCompounds

    pathwayIDs = sorted((step2Result.get("pathwaysInfo") or {}).keys())
    form = [("jobID", jobID)] + [("selectedPathways", p) for p in pathwayIDs]
    t0 = time.time()
    step3Result = client.post("/pa_step3", data=form)
    timings["step3"] = round(time.time() - t0, 2)
    artifacts["step3"] = {k: v for k, v in step3Result.items() if k not in _VOLATILE}
    artifacts["step3"]["selectedPathways"] = pathwayIDs

    timings["progress"] = dict(client.progress)
    client.progress = {}
    return jobID, timings, artifacts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--scenarios", nargs="+", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args()

    client = Client(args.server, verify=not args.insecure)
    summary = []
    for scenario in args.scenarios:
        for run in range(1, args.repeat + 1):
            runDir = os.path.join(args.out, scenario, "run%d" % run)
            os.makedirs(runDir, exist_ok=True)
            started = time.time()
            try:
                jobID, timings, artifacts = runScenario(client, scenario)
                total = round(time.time() - started, 2)
                with gzip.open(os.path.join(runDir, "artifacts.json.gz"), "wt", encoding="utf-8") as h:
                    json.dump(artifacts, h)
                record = {"scenario": scenario, "run": run, "jobID": jobID,
                          "total": total, "phases": timings, "server": args.server}
                with open(os.path.join(runDir, "timings.json"), "w") as h:
                    json.dump(record, h, indent=2)
                print("%-32s run%d %8.1fs job=%s step1=%.1f step2=%.1f step3=%.1f" % (
                    scenario, run, total, jobID, timings["step1"], timings["step2"], timings["step3"]),
                    flush=True)
            except Exception as exc:
                record = {"scenario": scenario, "run": run, "error": str(exc)}
                print("%-32s run%d FAILED: %s" % (scenario, run, exc), flush=True)
            summary.append(record)
            with open(os.path.join(args.out, "summary.json"), "w") as h:
                json.dump(summary, h, indent=2)


if __name__ == "__main__":
    main()

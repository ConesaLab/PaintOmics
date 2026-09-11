#!/usr/bin/env python3
"""Install every organism in deploy/species/manifest.tsv on a compose deployment.

Runs on the VM HOST (python3, no third-party packages) and drives DBManager
inside the app container:

    download workers  N parallel `DBManager.py download --specie=<code>`
    install worker    one `DBManager.py install --species=a,b,c,...` at a time
                      (installs share species.json and /tmp/xref.tmp, so they
                      are never run concurrently)

Everything is durable and restartable: one JSON file per species under
--state-dir records where it got to, a species already carrying its pathways
in MongoDB is skipped, and an interrupted download or install is simply
redone. The installer is taken from --installer-src on the host and copied
into the container whenever the container's copy differs (another deploy may
recreate the container mid-run, which throws the copy away).

KEGG is the only rate-limited source. Each download process makes one request
per DOWNLOAD_DELAY_2 (2 s) plus latency, so --workers sets the aggregate rate:
5 workers is about 1.5 requests/s. Eight workers (~2.3/s) earned a 403 block
for half an hour on 2026-09-11, so a 403/429 pauses every download at once
for --forbidden-pause seconds, and a run of other network failures pauses
for --breaker-pause seconds, instead of hammering a server that is refusing us.

    allspecies_runner.py run     [--workers 6] [--batch 25] [--kinds Eukaryota,Archaea,Bacteria]
    allspecies_runner.py status  one line per state, plus rates and an ETA
    allspecies_runner.py census  {code: pathway count} for every species database, as JSON

Control files in --state-dir:  STOP  (finish the species in flight, then exit)
                               PAUSE (start nothing new until it is removed)
"""
import argparse
import csv
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

DEFAULTS = {
    "compose": "/home/tliu/paintomics4/deploy/compose.yaml",
    "container": "paintomics-app-1",
    "mongo_container": "paintomics-mongo-1",
    "installer_src": "/home/tliu/allspecies/AdminTools",
    "installer_dst": "/app/PaintomicsServer/src/AdminTools",
    "data_volume": "/var/lib/docker/volumes/paintomics_paintomics-data/_data/KEGG_DATA",
    "manifest": "/home/tliu/allspecies/manifest.tsv",
    "state_dir": "/home/tliu/allspecies/state",
    "log_dir": "/home/tliu/allspecies/logs",
}
DBMANAGER = "python /app/PaintomicsServer/src/AdminTools/DBManager.py"

RUNNABLE = ("install", "refresh", "rebuild")
#: What a refused or unreachable KEGG looks like in a download log. The status
#: codes are matched as requests spells them ("403 Client Error"), never as bare
#: digit runs: pathway ids such as hsa04030 or hsa05030 contain them.
NETWORK_FAILURE = re.compile(r"(?<!\d)(403|429|500|502|503|504)(?!\d)\s+(Client|Server)\s+Error|Forbidden|"
                             r"Max retries|ConnectionError|Connection (refused|reset|aborted)|Read timed out|"
                             r"Name or service not known|Temporary failure in name resolution", re.I)
#: KEGG's contract answers for an organism it does not serve: an empty body
#: (withdrawn entry) or HTTP 400 on its pathway lists. Retrying cannot help.
#: "Unable to retrieve gene2pathway.list" on its own is NOT permanent -- on
#: 2026-09-11 KEGG answered 403 for half an hour and 292 species were written
#: off as permanent failures because the message matched here first.
PERMANENT_FAILURE = re.compile(r"empty body|Unable to retrieve (gene2pathway|pathways)\.list[^\n]*400 Client Error", re.I)
#: KEGG refusing us outright. One of these means the rate is too high, not
#: that anything is wrong with the species: stop everything for a while.
FORBIDDEN = re.compile(r"(?<!\d)(403|429)(?!\d)\s+Client\s+Error", re.I)
#: Failures of the environment, not of the species: nothing about the organism
#: caused them, so they must not consume its attempts. A run of these means the
#: container is wrong (recreated without the installer, files owned by root
#: after a docker cp, a missing module); pause and say so.
ENV_FAILURE = re.compile(r"PermissionError|ModuleNotFoundError|ImportError|No such file or directory: '/app|"
                         r"OCI runtime exec failed|is not running|No such container", re.I)
#: The INSTALL SUMMARY block DBManager prints at the end of a run, as it
#: appears through the logging prefix ("... - DBManager.py : log -   installed : 2  aaf aag").
SUMMARY_LINE = re.compile(r"\b(installed|failed|skipped)\s+:\s+(\d+)\s+(.*)$")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Runner(object):
    def __init__(self, args):
        self.args = args
        os.makedirs(args.state_dir, exist_ok=True)
        os.makedirs(args.log_dir, exist_ok=True)
        self.logLock = threading.Lock()
        self.stateLock = threading.Lock()
        self.copyLock = threading.Lock()
        self.logFile = open(os.path.join(args.log_dir, "runner.log"), "a")
        self.states = {}
        self.breakerUntil = 0.0
        self.consecutiveNetworkFailures = 0
        self.breakerLock = threading.Lock()
        self.stopping = False
        self.installedTimes = []   # (t, n) for the rate

    # ------------------------------------------------------------ plumbing
    def log(self, msg):
        line = "%s %s" % (now(), msg)
        with self.logLock:
            self.logFile.write(line + "\n")
            self.logFile.flush()
            print(line, flush=True)

    def sudo(self, cmd, **kw):
        return subprocess.run(["sudo", "-n"] + cmd, **kw)

    def composeExec(self, cmd, logPath, user=None):
        """Run one command inside the app container, appending output to logPath."""
        base = ["sudo", "-n", "docker", "compose", "-f", self.args.compose, "exec", "-T"]
        if user:
            base += ["-u", user]
        base += ["app"] + cmd
        with open(logPath, "a") as handle:
            handle.write("\n===== %s %s\n" % (now(), " ".join(cmd)))
            handle.flush()
            return subprocess.run(base, stdout=handle, stderr=subprocess.STDOUT).returncode

    def mongoEval(self, js, database="admin"):
        result = self.sudo(["docker", "exec", self.args.mongo_container, "mongosh", "--quiet", database, "--eval", js],
                           capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError("mongosh failed: " + result.stderr.strip()[-500:])
        return result.stdout.strip()

    def census(self):
        """{code: n_kegg_pathway_documents} for every <code>-paintomics database."""
        js = ('const out={}; db.adminCommand({listDatabases:1,nameOnly:true}).databases.forEach(d=>{'
              'if(d.name.endsWith("-paintomics") && d.name!="global-paintomics"){'
              'out[d.name.slice(0,-11)]=db.getSiblingDB(d.name).kegg.countDocuments({});}}); print(JSON.stringify(out));')
        return json.loads(self.mongoEval(js))

    def hostPath(self, *parts):
        return os.path.join(self.args.data_volume, *parts)

    def hostFileExists(self, path):
        return self.sudo(["test", "-e", path]).returncode == 0

    # ------------------------------------------------------------ installer sync
    def installerHash(self, inContainer):
        if inContainer:
            result = self.sudo(["docker", "exec", self.args.container, "cat",
                                self.args.installer_dst + "/.installer-hash"], capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else ""
        try:
            with open(os.path.join(self.args.installer_src, ".installer-hash")) as handle:
                return handle.read().strip()
        except IOError:
            return ""

    def pruneSpeciesDirectories(self):
        """Remove scripts/<code>_resources directories the host installer no longer has.

        docker cp adds and overwrites; it never deletes. A species directory
        removed from the installer (bvu_resources, whose MapMan build put
        plant pathways into a bacterium) therefore survived in the container
        from the image, and DBManager -- which dispatches on the directory's
        existence -- kept running the build that was removed.
        """
        scripts = os.path.join(self.args.installer_src, "scripts")
        wanted = {name for name in os.listdir(scripts) if name.endswith("_resources")}
        listing = self.sudo(["docker", "exec", self.args.container, "sh", "-c",
                             "ls -d %s/scripts/*_resources 2>/dev/null" % self.args.installer_dst],
                            capture_output=True, text=True)
        for path in listing.stdout.split():
            name = os.path.basename(path.rstrip("/"))
            if name not in wanted:
                self.log("removing %s from the container: not in the host installer" % path)
                self.sudo(["docker", "exec", "-u", "0", self.args.container, "rm", "-rf", path])

    def installerWritable(self):
        probe = self.args.installer_dst + "/log/.write-probe"
        result = self.sudo(["docker", "exec", "-u", self.args.exec_user, self.args.container, "sh", "-c",
                            "mkdir -p %s/log && touch %s && rm -f %s" % (self.args.installer_dst, probe, probe)],
                           capture_output=True, text=True)
        return result.returncode == 0

    def ensureInstaller(self):
        """Copy the host installer into the container if the container's differs."""
        with self.copyLock:
            wanted = self.installerHash(False)
            if not wanted:
                raise RuntimeError("no .installer-hash in " + self.args.installer_src + "; run ship-installer.sh first")
            if self.installerHash(True) == wanted:
                if self.args.exec_user and not self.installerWritable():
                    self.log("installer files are not writable by %s; repairing ownership" % self.args.exec_user)
                    self.sudo(["docker", "exec", "-u", "0", self.args.container, "chown", "-R",
                               self.args.exec_user + ":" + self.args.exec_user, self.args.installer_dst])
                return
            self.log("installer in container differs; copying " + self.args.installer_src)
            result = self.sudo(["docker", "cp", self.args.installer_src + "/.", self.args.container + ":" + self.args.installer_dst + "/"],
                               capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError("docker cp failed: " + result.stderr.strip())
            if self.installerHash(True) != wanted:
                raise RuntimeError("installer copy did not take (hash mismatch after docker cp)")
            self.pruneSpeciesDirectories()
            # docker cp writes as root; the installer then runs as --exec-user and
            # must be able to write its own log/ directory and __pycache__.
            if self.args.exec_user:
                fix = self.sudo(["docker", "exec", "-u", "0", self.args.container, "chown", "-R",
                                 self.args.exec_user + ":" + self.args.exec_user, self.args.installer_dst],
                                capture_output=True, text=True)
                if fix.returncode != 0:
                    raise RuntimeError("chown of the installer failed: " + fix.stderr.strip())

    # ------------------------------------------------------------ state
    def statePath(self, code):
        return os.path.join(self.args.state_dir, code + ".json")

    def loadStates(self):
        for name in os.listdir(self.args.state_dir):
            if name.endswith(".json"):
                with open(os.path.join(self.args.state_dir, name)) as handle:
                    try:
                        entry = json.load(handle)
                    except ValueError:
                        continue
                    if not isinstance(entry, dict) or "code" not in entry:
                        continue   # progress.json lives here too
                    self.states[entry["code"]] = entry

    def setState(self, code, state, **fields):
        with self.stateLock:
            entry = self.states.get(code, {"code": code, "attempts": 0})
            entry.update(fields)
            entry["state"] = state
            entry["updated"] = now()
            self.states[code] = entry
            tmp = self.statePath(code) + ".tmp"
            with open(tmp, "w") as handle:
                json.dump(entry, handle)
            os.replace(tmp, self.statePath(code))

    def getState(self, code):
        with self.stateLock:
            return dict(self.states.get(code, {"code": code, "state": "pending", "attempts": 0}))

    def counts(self):
        with self.stateLock:
            out = {}
            for entry in self.states.values():
                out[entry["state"]] = out.get(entry["state"], 0) + 1
            return out

    def writeProgress(self, total, extra=None):
        counts = self.counts()
        cutoff = time.time() - 3600
        recent = sum(n for t, n in self.installedTimes if t >= cutoff)
        done = counts.get("installed", 0)
        remaining = max(total - done - counts.get("failed", 0), 0)
        eta_h = (remaining / recent) if recent else None
        progress = {"time": now(), "total": total, "counts": counts, "installed_last_hour": recent,
                    "eta_hours": round(eta_h, 1) if eta_h is not None else None,
                    "breaker_until": self.breakerUntil, "consecutive_network_failures": self.consecutiveNetworkFailures}
        if extra:
            progress.update(extra)
        tmp = os.path.join(self.args.state_dir, "progress.json.tmp")
        with open(tmp, "w") as handle:
            json.dump(progress, handle, indent=1)
        os.replace(tmp, os.path.join(self.args.state_dir, "progress.json"))
        return progress

    # ------------------------------------------------------------ manifest
    def loadManifest(self):
        rows = []
        with open(self.args.manifest, encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row["action"] in RUNNABLE and row["kegg"] == "1":
                    rows.append(row)
        kinds = [k.strip() for k in self.args.kinds.split(",") if k.strip()]
        rows = [r for r in rows if r["kingdom"] in kinds]
        order = {k: i for i, k in enumerate(kinds)}
        # Priority 0 (a refresh or rebuild of a species people already use)
        # goes before everything, whatever its kingdom; then, with
        # --order priority, the manifest's priority (a popularity rank) alone;
        # else the kinds order, then priority, then code.
        if self.args.order == "priority":
            rows.sort(key=lambda r: (int(r["priority"] or 9), r["code"]))
        else:
            rows.sort(key=lambda r: (0 if r["priority"] == "0" else 1, order.get(r["kingdom"], 99),
                                     int(r["priority"] or 9), r["code"]))
        if self.args.only:
            wanted = set(self.args.only.split(","))
            rows = [r for r in rows if r["code"] in wanted]
        if self.args.max_species:
            rows = rows[:self.args.max_species]
        return rows

    # ------------------------------------------------------------ breaker
    def controlFile(self, name):
        return os.path.exists(os.path.join(self.args.state_dir, name))

    def waitIfPaused(self):
        while True:
            if self.controlFile("STOP"):
                return False
            paused = self.controlFile("PAUSE")
            tripped = time.time() < self.breakerUntil
            if not paused and not tripped:
                return True
            time.sleep(30)

    def noteDownloadResult(self, ok, networkFailure):
        with self.breakerLock:
            if ok:
                self.consecutiveNetworkFailures = 0
                return
            if networkFailure:
                self.consecutiveNetworkFailures += 1
                if self.consecutiveNetworkFailures >= self.args.breaker_threshold and time.time() >= self.breakerUntil:
                    self.breakerUntil = time.time() + self.args.breaker_pause
                    self.log("BREAKER: %d consecutive network failures; pausing downloads for %d s"
                             % (self.consecutiveNetworkFailures, self.args.breaker_pause))
                    self.consecutiveNetworkFailures = 0

    # ------------------------------------------------------------ download
    def downloadOne(self, row):
        code = row["code"]
        logPath = os.path.join(self.args.log_dir, code + ".download.log")
        self.setState(code, "downloading", attempts=self.getState(code).get("attempts", 0) + 1, action=row["action"])
        self.ensureInstaller()
        if row["action"] == "refresh":
            # Existing species: keep its KEGG tree, refresh the mapping, add the
            # sources it is missing. --kegg=0 copies current/<code>; the
            # Reactome fetch now comes after that copy (test_reactome_download_order).
            cmd = ["--kegg=0", "--mapping=1", "--reactome=" + row["reactome"]]
        else:
            cmd = ["--kegg=1", "--mapping=1", "--reactome=" + row["reactome"]]
        start = time.time()
        rc = self.composeExec(DBMANAGER.split() + ["download", "--specie=" + code, "--common=0"] + cmd, logPath,
                              user=self.args.exec_user)
        elapsed = int(time.time() - start)
        staged = self.hostFileExists(self.hostPath("download", code, "VERSION"))
        if rc == 0 and staged:
            self.setState(code, "downloaded", download_seconds=elapsed)
            self.noteDownloadResult(True, False)
            self.log("%s: downloaded in %d s" % (code, elapsed))
            return
        tail = self.tail(logPath, 4000)
        reason = self.reasonFrom(tail)
        if ENV_FAILURE.search(tail) and elapsed < 60:
            # Not this species' fault: give the attempt back and pause everything.
            self.setState(code, "retry", reason="environment: " + reason, attempts=max(self.getState(code).get("attempts", 1) - 1, 0))
            with self.breakerLock:
                if time.time() >= self.breakerUntil:
                    self.breakerUntil = time.time() + self.args.env_pause
                    self.log("ENVIRONMENT FAILURE on %s (%s); pausing downloads %d s and re-syncing the installer"
                             % (code, reason, self.args.env_pause))
            return
        network = bool(NETWORK_FAILURE.search(tail))
        permanent = bool(PERMANENT_FAILURE.search(tail)) and not network
        if FORBIDDEN.search(tail):
            # A refusal is never the species' fault: give the attempt back and
            # stop every download at once, not after four more refusals.
            self.setState(code, "retry", reason="forbidden: " + reason,
                          attempts=max(self.getState(code).get("attempts", 1) - 1, 0))
            with self.breakerLock:
                if time.time() >= self.breakerUntil:
                    self.breakerUntil = time.time() + self.args.forbidden_pause
                    self.log("FORBIDDEN by KEGG on %s; pausing all downloads for %d s" % (code, self.args.forbidden_pause))
            return
        self.noteDownloadResult(False, network)
        attempts = self.getState(code).get("attempts", 0)
        if permanent:
            self.setState(code, "failed", reason="download: " + reason, download_seconds=elapsed)
            self.log("%s: FAILED permanently (%s)" % (code, reason))
        elif attempts >= self.args.max_attempts:
            self.setState(code, "failed", reason="download after %d attempts: %s" % (attempts, reason), download_seconds=elapsed)
            self.log("%s: FAILED after %d attempts (%s)" % (code, attempts, reason))
        else:
            self.setState(code, "retry", reason="download: " + reason, download_seconds=elapsed)
            self.log("%s: download failed (rc=%s, staged=%s), will retry: %s" % (code, rc, staged, reason))

    @staticmethod
    def tail(path, nbytes):
        try:
            with open(path, "rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - nbytes))
                return handle.read().decode("utf-8", "replace")
        except IOError:
            return ""

    @staticmethod
    def reasonFrom(tail):
        for line in reversed(tail.splitlines()):
            if "Unable to retrieve" in line or "Too many errors" in line or "Error" in line or "FAILED" in line:
                return line.strip()[:300]
        return (tail.strip().splitlines() or ["no output"])[-1][:300]

    def downloadWorker(self, work, total):
        while True:
            if not self.waitIfPaused():
                return
            try:
                row = work.get(timeout=5)
            except queue.Empty:
                if self.downloadsDone:
                    return
                continue
            try:
                self.downloadOne(row)
            except Exception as exc:
                self.log("%s: download worker error: %s" % (row["code"], exc))
                self.setState(row["code"], "retry", reason="worker: " + str(exc)[:300])
            finally:
                work.task_done()
            self.writeProgress(total)

    # ------------------------------------------------------------ install
    def installBatch(self, rows, total):
        codes = [r["code"] for r in rows]
        logPath = os.path.join(self.args.log_dir, "install-%s.log" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        for code in codes:
            self.setState(code, "installing", install_log=os.path.basename(logPath))
        self.ensureInstaller()
        reinstall = [r["code"] for r in rows if r["action"] == "rebuild"]
        promote = [r["code"] for r in rows if r["action"] != "rebuild"]
        start = time.time()
        rc = 0
        if promote:
            rc = self.composeExec(DBMANAGER.split() + ["install", "--species=" + ",".join(promote), "--common=0"], logPath,
                                  user=self.args.exec_user)
        if reinstall:
            rc2 = self.composeExec(DBMANAGER.split() + ["install", "--species=" + ",".join(reinstall), "--common=0", "--reinstall=1"], logPath,
                                   user=self.args.exec_user)
            rc = rc or rc2
        elapsed = int(time.time() - start)
        summary = self.parseSummary(logPath)
        if not summary and ENV_FAILURE.search(self.tail(logPath, 4000)):
            # The run died before it looked at any species (root-owned
            # summary.log, a recreated container): not their fault.
            for code in codes:
                self.setState(code, "downloaded", reason="environment: " + self.reasonFrom(self.tail(logPath, 4000)))
            with self.breakerLock:
                self.breakerUntil = max(self.breakerUntil, time.time() + self.args.env_pause)
            self.log("ENVIRONMENT FAILURE in install batch (%s); species kept as downloaded, pausing %d s"
                     % (self.reasonFrom(self.tail(logPath, 4000)), self.args.env_pause))
            time.sleep(self.args.env_pause)
            return
        installedNow = 0
        for code in codes:
            if code in summary.get("installed", set()):
                n = self.pathwayCount(code)
                if n is None:
                    # The install said SUCCESS; only the check could not run
                    # (mongosh hiccup). Not a failure of the species: keep it
                    # installed, unverified, for the final census to count.
                    self.setState(code, "installed", kegg_pathways=None, install_seconds=elapsed // max(len(codes), 1),
                                  reason="pathway count unverified: mongosh failed after the install")
                    self.log("%s: installed, pathway count unverified (mongosh failed)" % code)
                    installedNow += 1
                    continue
                if n > 0:
                    self.setState(code, "installed", kegg_pathways=n, install_seconds=elapsed // max(len(codes), 1))
                    installedNow += 1
                    continue
                self.setState(code, "failed", reason="install reported SUCCESS but MongoDB holds %d pathways" % n)
                self.log("%s: install SUCCESS but 0 pathways in MongoDB -- marked failed" % code)
            elif code in summary.get("failed", set()) or code in summary.get("skipped", set()):
                self.installFailure(code, "install: " + ("failed" if code in summary.get("failed", set()) else "skipped") + " (see " + os.path.basename(logPath) + ")")
            else:
                self.installFailure(code, "install run ended (rc=%s) without a verdict for this species (see %s)" % (rc, os.path.basename(logPath)))
        self.installedTimes.append((time.time(), installedNow))
        self.installedTimes = [(t, n) for t, n in self.installedTimes if t >= time.time() - 7200]
        self.log("install batch of %d: %d installed, %d s (%s)" % (len(codes), installedNow, elapsed, os.path.basename(logPath)))
        self.writeProgress(total)

    def installFailure(self, code, reason):
        entry = self.getState(code)
        attempts = entry.get("install_attempts", 0) + 1
        if attempts >= 2:
            self.setState(code, "failed", reason=reason, install_attempts=attempts)
            self.log("%s: FAILED (%s)" % (code, reason))
        else:
            self.setState(code, "downloaded", reason=reason, install_attempts=attempts)
            self.log("%s: install failed, will retry once (%s)" % (code, reason))

    def parseSummary(self, logPath):
        """{installed|failed|skipped: set(codes)} over every summary block in the log.

        One log can hold two runs (promote, then --reinstall), so the sets are
        unioned rather than the last block winning.
        """
        out = {}
        for line in self.tail(logPath, 200000).splitlines():
            match = SUMMARY_LINE.search(line.rstrip())
            if match:
                key, _, rest = match.groups()
                out.setdefault(key, set()).update(set(rest.split()) - {"-"})
        return out

    def pathwayCount(self, code, tries=3):
        """Pathway documents in <code>-paintomics, or None when mongosh cannot answer."""
        for attempt in range(tries):
            try:
                return int(self.mongoEval("print(db.kegg.countDocuments({}))", code + "-paintomics") or 0)
            except Exception as exc:
                self.log("%s: could not count pathways (try %d/%d): %s" % (code, attempt + 1, tries, exc))
                time.sleep(10)
        return None

    def installWorker(self, rowsByCode, total):
        idle = 0
        while True:
            # On STOP, stay until every download in flight has landed and been
            # installed: returning as soon as the queue looked empty left seven
            # downloaded species uninstalled on 2026-09-11.
            if self.controlFile("STOP") and not self.pending("downloaded") and not self.pending("downloading"):
                return
            ready = [c for c in self.orderedCodes if self.getState(c)["state"] == "downloaded"]
            if not ready:
                if self.downloadsDone and not self.pending("downloading"):
                    return
                time.sleep(15)
                idle += 1
                continue
            batch = ready[:self.args.batch]
            try:
                self.installBatch([rowsByCode[c] for c in batch], total)
            except Exception as exc:
                self.log("install worker error: %s" % exc)
                for code in batch:
                    self.installFailure(code, "worker: " + str(exc)[:300])
                time.sleep(30)

    def pending(self, state):
        with self.stateLock:
            return any(e["state"] == state for e in self.states.values())

    # ------------------------------------------------------------ run
    def run(self):
        rows = self.loadManifest()
        self.loadStates()
        self.log("manifest: %d runnable species (kinds %s)" % (len(rows), self.args.kinds))
        census = self.census()
        self.log("census: %d species databases with pathways" % sum(1 for n in census.values() if n > 0))
        rowsByCode = {r["code"]: r for r in rows}
        self.orderedCodes = [r["code"] for r in rows]
        work = queue.Queue()
        queued = 0
        for row in rows:
            code = row["code"]
            entry = self.getState(code)
            state = entry["state"]
            if state == "installed" and entry.get("action", row["action"]) == row["action"]:
                continue
            if state == "installed":
                # Installed under a different action (a plain install before the
                # manifest asked for a refresh): the refresh still has to happen.
                self.log("%s: installed as %s, manifest now says %s; queuing" % (code, entry.get("action"), row["action"]))
                state = "pending"
            if row["action"] == "install" and census.get(code, 0) > 0 and state in ("pending", "downloading", "retry", "installing", "downloaded"):
                # Already there (a previous run, or someone else's install).
                self.setState(code, "installed", kegg_pathways=census[code], reason="found installed before this run")
                continue
            if state == "failed" and not self.args.retry_failed:
                continue
            if state == "rebuild" or row["action"] == "rebuild":
                self.setState(code, "downloaded", action="rebuild")
                continue
            if state in ("downloading", "retry", "pending", "failed"):
                self.setState(code, "pending", action=row["action"])
                work.put(row)
                queued += 1
            elif state == "installing":
                # The install run died with it; the staged data is still there.
                self.setState(code, "downloaded")
            # "downloaded": leave it for the install worker.
        self.log("queued %d downloads; %s" % (queued, json.dumps(self.counts())))
        self.downloadsDone = False
        threads = []
        for i in range(self.args.workers):
            t = threading.Thread(target=self.downloadWorker, args=(work, len(rows)), name="download-%d" % i, daemon=True)
            t.start()
            threads.append(t)
            time.sleep(self.args.stagger)
        installer = threading.Thread(target=self.installWorker, args=(rowsByCode, len(rows)), name="install", daemon=True)
        installer.start()
        lastReport = 0
        lastRequeue = time.time()
        while any(t.is_alive() for t in threads) or installer.is_alive():
            # Requeue retries every --requeue-every seconds, and again once the
            # first pass drains. Waiting for the drain alone parked a species
            # killed by a container swap behind the whole 11,000-deep queue.
            retries = [rowsByCode[c] for c in self.orderedCodes if self.getState(c)["state"] == "retry"]
            due = time.time() - lastRequeue >= self.args.requeue_every
            if retries and not self.controlFile("STOP") and (due or (work.empty() and not self.pending("downloading"))):
                self.log("requeueing %d retries" % len(retries))
                for row in retries:
                    self.setState(row["code"], "pending")
                    work.put(row)
                lastRequeue = time.time()
            elif not retries and work.empty() and not self.pending("downloading"):
                self.downloadsDone = True
            if time.time() - lastReport > 300:
                progress = self.writeProgress(len(rows))
                self.log("progress: %s installed/h=%s eta_h=%s" % (json.dumps(progress["counts"]), progress["installed_last_hour"], progress["eta_hours"]))
                lastReport = time.time()
            time.sleep(10)
        progress = self.writeProgress(len(rows), {"finished": now()})
        self.log("RUN COMPLETE: %s" % json.dumps(progress["counts"]))
        return 0


def status(args):
    runner = Runner(args)
    runner.loadStates()
    path = os.path.join(args.state_dir, "progress.json")
    if os.path.isfile(path):
        with open(path) as handle:
            print(handle.read())
    counts = runner.counts()
    print(json.dumps(counts, indent=1))
    failed = [e for e in runner.states.values() if e["state"] == "failed"]
    for entry in sorted(failed, key=lambda e: e["code"])[:50]:
        print("FAILED %s: %s" % (entry["code"], entry.get("reason")))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("run", "status", "census"))
    for key, value in DEFAULTS.items():
        parser.add_argument("--" + key.replace("_", "-"), default=value)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--batch", type=int, default=25, help="species per install run")
    parser.add_argument("--kinds", default="Eukaryota,Archaea,Bacteria", help="kingdoms to run, in order")
    parser.add_argument("--order", choices=("kinds", "priority"), default="kinds",
                        help="'priority': install in the manifest's priority order (a popularity rank) regardless of kingdom")
    parser.add_argument("--only", default=None, help="comma-separated codes (smoke tests)")
    parser.add_argument("--max-species", type=int, default=0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--breaker-threshold", type=int, default=5)
    parser.add_argument("--breaker-pause", type=int, default=1800)
    parser.add_argument("--env-pause", type=int, default=300, help="pause after an environment failure")
    parser.add_argument("--forbidden-pause", type=int, default=3600, help="pause after KEGG answers 403/429")
    parser.add_argument("--requeue-every", type=int, default=1800, help="seconds between retry requeues")
    parser.add_argument("--stagger", type=float, default=20.0, help="seconds between worker starts")
    parser.add_argument("--exec-user", default=None, help="user for docker compose exec (default: the container's)")
    args = parser.parse_args(argv)
    if args.command == "status":
        return status(args)
    runner = Runner(args)
    if args.command == "census":
        print(json.dumps(runner.census(), sort_keys=True))
        return 0
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())

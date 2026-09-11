"""The all-species manifest and its runner: the decisions are pinned, not re-derived.

Run from `PaintomicsServer/`:
    python -m src.tests.test_species_manifest

deploy/species/manifest.tsv is the contract for what paintomics.org installs
from each pathway source. build_manifest.py resolves every organism against
the live publishers, so the network is not exercised here; what is tested is
the table of decisions that cannot be derived from the publishers alone --
which GoMapMan organism code means which KEGG organism (they are NOT the same
code space: GoMapMan bvu is sugar beet, KEGG bvu is a gut bacterium), which
Reactome species name is which KEGG code -- and that the runner classifies a
download's failure the way the operator expects.
"""
import csv
import importlib.util
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SPECIES_DIR = os.path.join(ROOT, "deploy", "species")
SCRIPTS = os.path.join(ROOT, "PaintomicsServer", "src", "AdminTools", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

_PASSED = []
_FAILED = []


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"PASS  {name}")
    except AssertionError as exc:
        _FAILED.append((name, str(exc)))
        print(f"FAIL  {name}: {exc}")
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print(f"ERROR {name}:\n{traceback.format_exc()}")


def _load(name):
    path = os.path.join(SPECIES_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifestRows():
    with open(os.path.join(SPECIES_DIR, "manifest.tsv"), encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_every_gomapman_code_has_a_verdict_and_no_collision_is_installed():
    m = _load("build_manifest")
    for gcode, (kcode, verdict, reason) in m.MAPMAN.items():
        assert verdict in ("install", "exclude"), (gcode, verdict)
        assert reason, gcode + " needs a reason"
        if verdict == "install":
            assert kcode, gcode + " installs under no KEGG code"
    # The four organism-code collisions documented on paintomics.org must never install.
    for collision in ("bvu", "tae", "tca", "cam"):
        assert m.MAPMAN[collision][1] == "exclude", collision
    assert m.MAPMAN["stu"][0] == "sot", "GoMapMan stu is potato, KEGG sot"
    assert m.MAPMAN["osa"][0] == "osa" and m.MAPMAN["osa"][1] == "install"


def test_reactome_species_table_maps_to_kegg_codes_with_taxids():
    m = _load("build_manifest")
    codes = [code for code, taxid in m.REACTOME_SPECIES.values()]
    assert len(codes) == len(set(codes)), "two Reactome species map to one KEGG code"
    assert m.REACTOME_SPECIES["Homo sapiens"] == ("hsa", 9606)
    assert m.REACTOME_SPECIES["Canis familiaris"] == ("cfa", 9615)
    assert m.REACTOME_SPECIES["Mycobacterium tuberculosis"] == ("mtu", 1773)
    assert set(m.OMNIPATH) == {"hsa", "mmu", "rno"}, "OmniPath serves exactly three taxids"


def test_manifest_covers_every_kegg_organism_once_with_a_valid_action():
    rows = _manifestRows()
    codes = [r["code"] for r in rows]
    assert len(codes) == len(set(codes)), "duplicate codes in manifest"
    assert len(rows) > 11000, "manifest has only %d rows; KEGG lists ~12,000 organisms" % len(rows)
    actions = {r["action"] for r in rows}
    assert actions <= {"install", "keep", "refresh", "rebuild"}, actions
    kingdoms = {r["kingdom"] for r in rows}
    assert {"Eukaryota", "Bacteria", "Archaea"} <= kingdoms, kingdoms
    for r in rows:
        if r["kingdom"]:
            assert r["priority"] in ("1", "2", "3"), r
            assert r["kegg"] == "1", r
        if r["mapman"] == "1":
            assert r["code"] in ("ath", "osa", "sly", "sot"), r["code"] + " must not install MapMan"
        if r["omnipath"] == "1":
            assert r["code"] in ("hsa", "mmu", "rno"), r["code"]
    byCode = {r["code"]: r for r in rows}
    assert byCode["bvu"]["mapman"] == "0" and byCode["bvu"]["action"] in ("rebuild", "keep"), byCode["bvu"]
    assert byCode["hsa"]["reactome"] == "1" and byCode["hsa"]["omnipath"] == "1"
    assert byCode["eco"]["kingdom"] == "Bacteria" and byCode["eco"]["priority"] == "3"


def test_eukaryotes_come_before_archaea_before_bacteria():
    rows = _manifestRows()
    order = [r["priority"] for r in rows if r["kingdom"]]
    assert order == sorted(order), "manifest is not in install priority order"


def test_runner_classifies_download_failures():
    r = _load("allspecies_runner")
    assert r.NETWORK_FAILURE.search("HTTPError: 403 Client Error: Forbidden")
    assert r.NETWORK_FAILURE.search("Max retries exceeded with url")
    assert not r.NETWORK_FAILURE.search("Too many errors while downloading the KGML files")
    assert r.PERMANENT_FAILURE.search("empty body (HTTP 200) for https://rest.kegg.jp/link/pathway/xyz")
    assert r.ENV_FAILURE.search("PermissionError: [Errno 13] Permission denied: '/app/x/log/application.log'")
    assert r.ENV_FAILURE.search("OCI runtime exec failed")
    assert not r.ENV_FAILURE.search("Unable to retrieve pathways.list")


def test_runner_parses_the_install_summary_block():
    r = _load("allspecies_runner")
    import tempfile
    path = tempfile.mktemp()
    with open(path, "w") as handle:
        handle.write("2026-09-11 12:00:00,000 - INFO - DBManager.py : log - INSTALL SUMMARY\n"
                     "2026-09-11 12:00:00,000 - INFO - DBManager.py : log -   installed : 2  aaf aag\n"
                     "2026-09-11 12:00:00,000 - INFO - DBManager.py : log -   failed    : 1  aalb\n"
                     "2026-09-11 12:00:00,000 - INFO - DBManager.py : log -   skipped   : 0  -\n")
    try:
        parsed = r.Runner.parseSummary(r.Runner.__new__(r.Runner), path)
    finally:
        os.remove(path)
    assert parsed["installed"] == {"aaf", "aag"}, parsed
    assert parsed["failed"] == {"aalb"}, parsed
    assert parsed["skipped"] == set(), parsed


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            _check(name, fn)
    print(f"\n{len(_PASSED)} passed, {len(_FAILED)} failed")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

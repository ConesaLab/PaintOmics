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
    assert actions <= {"install", "keep", "refresh", "rebuild", "defer"}, actions
    kingdoms = {r["kingdom"] for r in rows}
    assert {"Eukaryota", "Bacteria", "Archaea"} <= kingdoms, kingdoms
    for r in rows:
        if r["kingdom"]:
            assert r["priority"].isdigit(), r
            if r["priority"] == "0":
                assert r["action"] in ("refresh", "rebuild"), r
            assert r["kegg"] == "1", r
        if r["action"] == "defer":
            assert "deferred" in r["note"], r
        if r["action"] == "install" and r["rank"]:
            assert r["priority"] == r["rank"], "an installed organism's priority is its popularity rank: %r" % r
        if r["mapman"] == "1":
            assert r["code"] in ("ath", "osa", "sly", "sot"), r["code"] + " must not install MapMan"
        if r["omnipath"] == "1":
            assert r["code"] in ("hsa", "mmu", "rno"), r["code"]
    byCode = {r["code"]: r for r in rows}
    assert byCode["bvu"]["mapman"] == "0" and byCode["bvu"]["action"] in ("rebuild", "keep"), byCode["bvu"]
    assert byCode["hsa"]["reactome"] == "1" and byCode["hsa"]["omnipath"] == "1"
    assert byCode["eco"]["kingdom"] == "Bacteria" and byCode["eco"]["priority"] == "3"


def test_manifest_is_in_install_order_and_the_top_is_the_most_cited():
    rows = _manifestRows()
    order = [int(r["priority"]) for r in rows if r["kingdom"] and r["action"] in ("install", "refresh", "rebuild")]
    assert order == sorted(order), "manifest is not in install priority order"
    ranked = [r for r in rows if r["action"] == "install" and r["rank"]]
    if ranked:
        counts = [int(r["pubmed_count"]) for r in sorted(ranked, key=lambda r: int(r["rank"]))]
        assert counts == sorted(counts, reverse=True), "ranks do not follow the PubMed counts"
        # One KEGG code per species: no two installed rows share a binomial
        # (as the counter spells it: "Candidatus Liberibacter asiaticus" is three words).
        binomialOf = _load("pubmed_popularity").binomialOf
        binomials = [binomialOf(r["name"]) for r in ranked]
        assert len(binomials) == len(set(binomials)), "two strains of one species are both installed"


def test_runner_classifies_download_failures():
    r = _load("allspecies_runner")
    assert r.NETWORK_FAILURE.search("HTTPError: 403 Client Error: Forbidden")
    assert r.NETWORK_FAILURE.search("503 Server Error: Service Unavailable for url")
    assert r.NETWORK_FAILURE.search("Max retries exceeded with url")
    assert not r.NETWORK_FAILURE.search("Too many errors while downloading the KGML files")
    # Pathway ids carry the same digits; a healthy log must not trip the breaker.
    assert not r.NETWORK_FAILURE.search("- hsa04030 [12/161]\n- hsa05030 [13/161]\n- map00403 [14/161]")
    # 400 is KEGG's contract answer, not a network fault.
    assert not r.NETWORK_FAILURE.search("Error downloading uniprot2kegg.list: 400 Client Error: Bad Request")
    assert r.PERMANENT_FAILURE.search("empty body (HTTP 200) for https://rest.kegg.jp/link/pathway/xyz")
    assert r.ENV_FAILURE.search("PermissionError: [Errno 13] Permission denied: '/app/x/log/application.log'")
    assert r.ENV_FAILURE.search("OCI runtime exec failed")
    assert not r.ENV_FAILURE.search("Unable to retrieve pathways.list")


def test_manifest_census_accepts_the_runner_json_and_the_report_tsv():
    import json
    import tempfile
    m = _load("build_manifest")
    tmp = tempfile.mkdtemp()
    try:
        jsonPath = os.path.join(tmp, "census.json")
        with open(jsonPath, "w") as handle:
            json.dump({"hsa": 372, "xyz": 0}, handle)
        census = m.loadCensus(jsonPath)
        assert census["hsa"]["sources"] == {"KEGG": 372} and census["xyz"]["sources"] == {"KEGG": 0}, census
        tsvPath = os.path.join(tmp, "census.tsv")
        with open(tsvPath, "w") as handle:
            handle.write("kind\tcode\tname\tn\tsampled\treached\tfraction\ttarget\n"
                         "source\tmmu\tKEGG\t364\t\t\t\t\n"
                         "source\tmmu\tReactome\t524\t\t\t\t\n"
                         "table\tmmu\tensembl_gene\t28311\t10\t10\t1.000\tentrezgene\n")
        census = m.loadCensus(tsvPath)
        assert census["mmu"]["sources"] == {"KEGG": 364, "Reactome": 524}, census
        assert census["mmu"]["tables"] == {"ensembl_gene": 28311}, census
    finally:
        import shutil
        shutil.rmtree(tmp)


def test_inflated_species_are_refreshed_from_the_census_statistic():
    """omy/amex/dre carry mate sets inflated by ambiguous EntrezGene xrefs; the report's
    xref_avg_bytes statistic is what the manifest turns into a refresh."""
    import tempfile
    m = _load("build_manifest")
    tmp = tempfile.mkdtemp()
    try:
        tsvPath = os.path.join(tmp, "census.tsv")
        with open(tsvPath, "w") as handle:
            handle.write("kind\tcode\tname\tn\tsampled\treached\tfraction\ttarget\n"
                         "source\tomy\tKEGG\t196\t\t\t\t\n"
                         "stat\tomy\txref_avg_bytes\t6621\t\t\t\t\n"
                         "source\tmmu\tKEGG\t364\t\t\t\t\n"
                         "stat\tmmu\txref_avg_bytes\t692\t\t\t\t\n")
        census = m.loadCensus(tsvPath)
        assert census["omy"]["stats"] == {"xref_avg_bytes": 6621}, census
        assert census["omy"]["stats"]["xref_avg_bytes"] > m.XREF_INFLATED_BYTES > census["mmu"]["stats"]["xref_avg_bytes"]
    finally:
        import shutil
        shutil.rmtree(tmp)


def test_report_sampler_spans_the_whole_table():
    sys.path.insert(0, SCRIPTS)
    import species_report
    positions = species_report.stridePositions(45, 40)   # n <= total < 2n: floor division gave first-N
    assert len(positions) == 40 and positions[0] == 0 and positions[-1] >= 43, positions
    assert species_report.stridePositions(1000, 40)[-1] >= 975
    assert species_report.stridePositions(5, 40) == [0, 1, 2, 3, 4]
    assert species_report.stridePositions(0, 40) == []
    try:
        species_report.positiveInt("0")
    except Exception as exc:
        assert "sample" in str(exc)
    else:
        raise AssertionError("--sample 0 must be rejected")


def test_popularity_ranking_keeps_one_strain_per_species_and_caps_the_list():
    m = _load("build_manifest")
    def row(code, kingdom, action="install", priority="3"):
        return {"code": code, "kingdom": kingdom, "action": action, "priority": priority, "note": ""}
    rows = [row("eco", "Bacteria"), row("ecj", "Bacteria"), row("hsa", "Eukaryota", "keep", "9"),
            row("mmu", "Eukaryota"), row("xyz", "Bacteria"), row("aaa", "Archaea"), row("mmx", "Eukaryota")]
    popularity = {"eco": ("Escherichia coli", 500000), "ecj": ("Escherichia coli", 500000),
                  "hsa": ("Homo sapiens", 9000000), "mmu": ("Mus musculus", 2000000),
                  "mmx": ("Mus musculus", 2000000), "xyz": ("Xanthomonas ypsilon", 3), "aaa": ("Archaeon alpha", 40)}
    census = {"hsa": {"sources": {"KEGG": 372}, "tables": {}}}
    m.rankByPopularity(rows, popularity, census, top=2)
    byCode = {r["code"]: r for r in rows}
    # Most cited first: mmu rank 1, eco rank 2; ecj is another strain of E. coli.
    assert byCode["mmu"]["rank"] == 1 and byCode["mmu"]["priority"] == 1 and byCode["mmu"]["action"] == "install", byCode["mmu"]
    assert byCode["eco"]["rank"] == 2 and byCode["eco"]["action"] == "install", byCode["eco"]
    assert byCode["ecj"]["action"] == "defer" and "another strain" in byCode["ecj"]["note"], byCode["ecj"]
    assert byCode["mmx"]["action"] == "defer", byCode["mmx"]
    # Beyond the top 2: deferred with the rank in the reason.
    assert byCode["aaa"]["action"] == "defer" and "rank 3" in byCode["aaa"]["note"], byCode["aaa"]
    assert byCode["xyz"]["action"] == "defer" and "rank 4" in byCode["xyz"]["note"], byCode["xyz"]
    # Rows the manifest does not install are untouched.
    assert byCode["hsa"]["action"] == "keep" and byCode["hsa"]["priority"] == "9", byCode["hsa"]


def test_runner_priority_order_ignores_the_kingdom():
    r = _load("allspecies_runner")
    import tempfile
    tmp = tempfile.mkdtemp()
    try:
        manifest = os.path.join(tmp, "manifest.tsv")
        with open(manifest, "w") as handle:
            handle.write("code\tkingdom\tpriority\taction\tkegg\treactome\n"
                         "aaa\tEukaryota\t7\tinstall\t1\t0\n"
                         "eco\tBacteria\t1\tinstall\t1\t0\n"
                         "zzz\tBacteria\t9\tdefer\t1\t0\n"
                         "mmu\tEukaryota\t2\tinstall\t1\t0\n")
        import argparse
        runner = r.Runner.__new__(r.Runner)
        runner.args = argparse.Namespace(manifest=manifest, kinds="Eukaryota,Archaea,Bacteria", only=None,
                                         max_species=0, order="priority")
        assert [row["code"] for row in r.Runner.loadManifest(runner)] == ["eco", "mmu", "aaa"]
        runner.args = argparse.Namespace(manifest=manifest, kinds="Eukaryota,Archaea,Bacteria", only=None,
                                         max_species=0, order="kinds")
        assert [row["code"] for row in r.Runner.loadManifest(runner)] == ["mmu", "aaa", "eco"]
    finally:
        import shutil
        shutil.rmtree(tmp)


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

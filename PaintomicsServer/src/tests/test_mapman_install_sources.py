"""Unit tests for the MapMan installer inputs.

Run from `PaintomicsServer/`:

    python -m src.tests.test_mapman_install_sources

Each test prints PASS/FAIL and exits non-zero on any failure.

The MapMan organisms (ath, sly, sot; later osa) used to be built by copying five
files out of `/home/tian/mapman/`, a directory that exists on exactly one
machine and nowhere in the repository. Any other host - a fresh checkout, CI,
the Drago image - could not rebuild them at all, and the failure surfaced as a
`FileNotFoundError` deep inside the download step.

All five files are published by GoMapMan, which maintains a "paintomics"
export target alongside its MapMan/GSEA/BioMine ones. These tests pin that
arrangement down:

  * no organism config may reference a local absolute path again
  * every config still declares the resources the build step reads
  * `output` names stay fixed, because processMapManMappingData and
    processMapManPathwaysData look the files up by those names
  * bin-code normalisation, without which three diagrams import with far
    fewer features than they should and two import completely empty

These tests are offline: they read the configs and exercise pure functions.
Nothing here contacts GoMapMan.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MAPMAN_SPECIES = ("ath", "osa", "sly", "sot")

# Everything processMapManMappingData / processMapManPathwaysData reads.
# "mapman_kegg" is optional to the download step, but every organism kept
# here declares one: without a cross-link the MapMan genes form an island the
# mapper cannot reach -- which is what `bvu` was (GoMapMan's bvu is sugar
# beet, KEGG's bvu is the gut bacterium Phocaeicola vulgatus, so its 27,421
# MapMan ids sat at 0% cross-linked inside a bacterium; removed 2026-09-11).
REQUIRED_RESOURCES = ("mapman_gene", "mapman_pathways",
                      "mapman_classification", "metabolites")

# The build step resolves each file as DATA_DIR/mapping/<output>, so these
# names are an interface, not a detail.
EXPECTED_OUTPUTS = {
    "ath": {"mapman_kegg": "gene-to-entrez_ath.list",
            "mapman_gene": "gene-to-mapman_ath.list"},
    "osa": {"mapman_kegg": "gene-to-entrez_osa.list",
            "mapman_gene": "gene-to-mapman_osa.list"},
    "sly": {"mapman_kegg": "gene-to-entrez_sly.list",
            "mapman_gene": "gene-to-mapman_sly.list"},
    "sot": {"mapman_kegg": "sot_pgsc_ncbi.list",
            "mapman_gene": "sot_mapman_gene.list"},
}

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AdminTools", "scripts")


def _loadConfig(specie):
    """Load one organism's download_conf.py without importing the package."""
    import importlib.util

    path = os.path.join(_SCRIPTS_DIR, specie + "_resources", "download_conf.py")
    spec = importlib.util.spec_from_file_location("download_conf_" + specie, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EXTERNAL_RESOURCES


def _mapmanResources(resources):
    """Just the MapMan entries; ensembl/refseq/uniprot are fetched elsewhere."""
    keys = ("mapman_kegg", "mapman_gene", "mapman_pathways",
            "mapman_classification", "metabolites")
    return {k: resources[k] for k in keys if resources.get(k)}


def _normaliseMapManBin():
    import importlib.util

    path = os.path.join(_SCRIPTS_DIR, "common_build_database.py")
    spec = importlib.util.spec_from_file_location("common_build_database", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normaliseMapManBin


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_mapman_input_comes_from_a_local_path():
    """A machine-local path makes the organism unbuildable everywhere else.

    A resource may instead ship *in the repository* via "local" -- rice's KEGG
    cross-link is derived rather than published, so there is no URL to fetch it
    from. That is the opposite of the /home/tian/mapman/ problem this test was
    written for, but only while the path stays relative to the source tree and
    the file is actually committed, so both are asserted here.
    """
    for specie in MAPMAN_SPECIES:
        for name, entries in _mapmanResources(_loadConfig(specie)).items():
            entry = entries[0]
            local = entry.get("local")

            if local:
                assert not os.path.isabs(local) and "/home/" not in local, \
                    f"{specie}/{name} 'local' must be relative to the source tree: {local!r}"
                assert ".." not in local.split("/"), \
                    f"{specie}/{name} 'local' escapes the source tree: {local!r}"
                # ROOT_DIR is src/AdminTools/, which _SCRIPTS_DIR sits inside.
                resolved = os.path.join(os.path.dirname(_SCRIPTS_DIR), local)
                assert os.path.isfile(resolved), \
                    f"{specie}/{name} 'local' file is not committed: {local!r}"
                assert not entry.get("url"), \
                    f"{specie}/{name} declares both 'local' and 'url'; only one is used"
                continue

            url = entry.get("url", "")
            assert url.startswith("http"), \
                f"{specie}/{name} is fetched from {url!r}, not a URL"
            assert not url.startswith("/") and "/home/" not in url, \
                f"{specie}/{name} points at a local path: {url!r}"


def test_every_species_declares_the_resources_the_build_step_reads():
    """A missing key crashes the download step with a TypeError, not a message."""
    for specie in MAPMAN_SPECIES:
        resources = _loadConfig(specie)
        for name in REQUIRED_RESOURCES:
            assert resources.get(name), f"{specie} declares no {name}"
            assert resources[name][0].get("file"), f"{specie}/{name} has no file"
            assert resources[name][0].get("output"), f"{specie}/{name} has no output"


def test_output_names_are_the_ones_the_build_step_looks_up():
    """processMapManMappingData resolves DATA_DIR/mapping/<output> by name."""
    for specie, expected in EXPECTED_OUTPUTS.items():
        resources = _loadConfig(specie)
        for name, output in expected.items():
            assert resources[name][0]["output"] == output, \
                (f"{specie}/{name} output is {resources[name][0]['output']!r}, "
                 f"the build step reads {output!r}")

    # Shared files must keep their shared names across every organism.
    for specie in MAPMAN_SPECIES:
        resources = _loadConfig(specie)
        assert resources["mapman_pathways"][0]["output"] == "mapman_pathways.tar.gz"
        assert resources["mapman_classification"][0]["output"] == "mapman_classification.txt"
        assert resources["metabolites"][0]["output"] == "mapman_metabolites.txt"


def test_compressed_downloads_are_marked_for_decompression():
    """A .gz left compressed parses as one unreadable binary line, not an error."""
    for specie in MAPMAN_SPECIES:
        for name, entries in _mapmanResources(_loadConfig(specie)).items():
            entry = entries[0]
            # A "local" resource has no "file"; its source name is the path.
            source = entry.get("file") or entry.get("local", "")
            if source.endswith(".gz") and entry["output"].endswith(".gz"):
                continue  # tarballs stay packed; the build step untars them
            if source.endswith(".gz"):
                assert entry.get("decompress"), \
                    f"{specie}/{name} reads {source} but never gunzips it"


def test_metabolite_export_header_is_dropped():
    """Its BINCODE/NAME header would otherwise become a compound named 'NAME'."""
    for specie in MAPMAN_SPECIES:
        entry = _loadConfig(specie)["metabolites"][0]
        assert entry.get("skip_header"), \
            f"{specie} keeps the metabolite header row, which becomes a fake compound"


def test_bin_normalisation_matches_padded_diagram_codes_to_mappings():
    """Diagrams spell bins '18.4.01'; mappings spell the same bin '18.4.1'."""
    normalise = _normaliseMapManBin()
    assert normalise("18.4.01") == "18.4.1"
    assert normalise("17.8.1.1.02") == "17.8.1.1.2"
    assert normalise("18.4.001") == "18.4.1"


def test_bin_normalisation_leaves_everything_else_alone():
    """Metabolite bins end in 1001+; truncating those would break compounds."""
    normalise = _normaliseMapManBin()
    for unchanged in ("1.1.1001", "20.1.10", "35.2", "1", "13.1.1.3.10001"):
        assert normalise(unchanged) == unchanged, \
            f"{unchanged} was rewritten to {normalise(unchanged)}"


def test_bin_normalisation_survives_degenerate_input():
    """A bin of '0' must not normalise to the empty string."""
    normalise = _normaliseMapManBin()
    assert normalise("0") == "0"
    assert normalise("00") == "0"
    assert normalise("1.0.2") == "1.0.2"
    assert normalise("") == ""
    assert normalise(None) is None
    # Non-numeric segments are passed through rather than mangled.
    assert normalise("1.1.1a") == "1.1.1a"


def _extraDiagramManifest():
    import json

    path = os.path.join(_SCRIPTS_DIR, "common_resources", "mapman_extra_diagrams.json")
    with open(path) as handle:
        return json.load(handle)


def test_every_species_asks_for_the_extra_diagrams():
    """GoMapMan ships 20 diagrams and omits every general map."""
    for specie in MAPMAN_SPECIES:
        entries = _loadConfig(specie).get("mapman_extra_pathways")
        assert entries, f"{specie} would install the 20-diagram subset only"
        manifest = entries[0].get("manifest")
        assert manifest, f"{specie} declares mapman_extra_pathways with no manifest"
        assert os.path.isfile(os.path.join(os.path.dirname(_SCRIPTS_DIR), manifest)), \
            f"{specie} points at a manifest that does not exist: {manifest}"


def test_extra_diagram_manifest_is_complete_and_unambiguous():
    """A duplicate name would overwrite a diagram; a blank field breaks the URL."""
    diagrams = _extraDiagramManifest()["diagrams"]
    assert len(diagrams) >= 50, f"manifest lists only {len(diagrams)} diagrams"

    names = [d["name"] for d in diagrams]
    assert len(names) == len(set(names)), "duplicate diagram names in the manifest"

    ids = [d["ressourceId"] for d in diagrams]
    assert len(ids) == len(set(ids)), "duplicate RessourceIds in the manifest"

    for diagram in diagrams:
        for field in ("name", "ressourceId", "primary", "secondary"):
            assert str(diagram.get(field, "")).strip(), \
                f"{diagram.get('name')} has an empty {field}"
        # Names become pathway ids and then file names inside the tarball.
        assert "/" not in diagram["name"] and not diagram["name"].startswith("."), \
            f"unsafe diagram name: {diagram['name']!r}"


def test_extra_diagrams_are_all_the_same_ontology_era():
    """X4 diagrams renumber the ontology - they place wrong genes without erroring."""
    for diagram in _extraDiagramManifest()["diagrams"]:
        assert not diagram["name"].startswith("X4"), \
            (f"{diagram['name']} is an X4-era diagram; GoMapMan's mappings are 3.6-era "
             "and the mismatch is silent")


def test_extra_diagrams_do_not_collide_with_the_gomapman_set():
    """Reusing a shipped name would replace a pathway id that is live in MongoDB."""
    # The 20 GoMapMan names, spelled as they appear inside mapman_pathways.tar.gz.
    shipped = {
        "ABA metabolism", "Biotic Stress", "Carotenoid", "Coenzyme A Biosynthesis",
        "Flavonoid", "GA Synthesis", "GA synthesis later stages", "Isopentenyl PP",
        "JA Synthesis", "Phenylpropanoids", "Polyamine Synthesis", "SA synthesis",
        "Shikimate Synthesis", "Terpenoid", "Tocopherol Biosynthesis",
        "Volatile Carotenoid", "Volatile Mevalonic Pathway", "Volatile PhenylPropanoids",
        "Volatile PhenylPropanoids 2", "receptor like kinases",
    }
    clash = shipped.intersection(d["name"] for d in _extraDiagramManifest()["diagrams"])
    assert not clash, f"extra diagrams reuse GoMapMan pathway ids: {sorted(clash)}"


def _defaultDatabaseMapping():
    """The literal getDatabasesByOrganismCode falls back to, read from source.

    Parsed rather than imported so this stays runnable on a clean checkout --
    the module's import chain reaches src.conf.serverconf, which is generated
    at first launch and not committed.
    """
    import ast

    path = os.path.join(os.path.dirname(_SCRIPTS_DIR), "..", "common",
                        "FeatureNamesToKeggIDsMapper.py")
    with open(os.path.normpath(path)) as handle:
        tree = ast.parse(handle.read())

    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef)
                and node.name == "getDatabasesByOrganismCode"):
            continue
        for inner in ast.walk(node):
            # dicDatabases.get(organism, <default>)
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "get"
                    and len(inner.args) == 2):
                return ast.literal_eval(inner.args[1])

    raise AssertionError(
        "could not find the dicDatabases.get(...) fallback in "
        "getDatabasesByOrganismCode; this test needs updating")


# ---------------------------------------------------------------------------
# Rice (osa)
#
# Rice differs from the other four MapMan organisms in two ways that are easy
# to undo by accident, so each has a test: its KEGG cross-link ships in the
# repository instead of being downloaded, and it must live under `osa` rather
# than rice's other KEGG code `dosa`.
# ---------------------------------------------------------------------------

def test_rice_cross_link_is_shipped_and_well_formed():
    """processMapManMappingData reads col0=MapMan gene, col1=NCBI GeneID.

    A malformed row here does not raise: the loop catches per-line exceptions
    and records them, so a broken file installs as a species with MapMan bins
    that link to nothing -- exactly the shape of the bvu defect.
    """
    import gzip

    entry = _loadConfig("osa")["mapman_kegg"][0]
    path = os.path.join(os.path.dirname(_SCRIPTS_DIR), entry["local"])

    rows = 0
    loci = set()
    with gzip.open(path, "rt") as handle:
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            assert len(fields) == 2, \
                f"line {rows + 1} has {len(fields)} columns, expected 2: {line[:80]!r}"
            assert fields[0].startswith("LOC_Os"), \
                f"line {rows + 1} column 1 is not an MSU locus: {fields[0]!r}"
            assert fields[1].isdigit(), \
                f"line {rows + 1} column 2 is not an NCBI GeneID: {fields[1]!r}"
            loci.add(fields[0])
            rows += 1

    # Guards against a truncated or placeholder file being committed. The real
    # one carries ~25.5k pairs; anything near zero means the join silently
    # produced nothing.
    assert rows > 20000, f"cross-link has only {rows} rows; expected ~25,000"
    assert len(loci) > 20000, f"cross-link covers only {len(loci)} distinct MSU loci"


def test_rice_mapman_is_configured_under_osa_not_dosa():
    """`dosa` cannot host this data: KEGG answers 400 for /conv/dosa/ncbi-geneid.

    The cross-link's second column is an NCBI GeneID and the build step joins
    it through ncbi-geneid2kegg.list, which does not exist for dosa. Installing
    MapMan there would reproduce bvu: bins present, nothing cross-linked.
    """
    from src.conf.organismDB import dicDatabases

    assert "MapMan" in dicDatabases["osa"][0], "osa lost its MapMan id mapping"
    assert "MapMan" in dicDatabases["osa"][1], "osa lost its MapMan name mapping"
    assert dicDatabases["osa"][0]["MapMan"] == "mapman_gene_id"

    assert "MapMan" not in dicDatabases.get("dosa", [{}])[0], \
        "dosa must not offer MapMan: it is keyed on RAP-DB ids with no ncbi-geneid conversion"

    assert not os.path.isdir(os.path.join(_SCRIPTS_DIR, "dosa_resources", "mapman")), \
        "MapMan data must not be installed under dosa"


def test_rice_keeps_the_kegg_mapping_it_had_before_the_entry_existed():
    """Adding an organismDB entry *replaces* getDatabasesByOrganismCode's default.

    osa installed and mapped for a long time with no entry at all, which means
    the fallback in FeatureNamesToKeggIDsMapper.py. If the KEGG half here ever
    drifts from that fallback, rice's KEGG identifiers silently start resolving
    against a different xref namespace than the installed data uses.

    The fallback is read out of the source rather than imported: importing that
    module pulls in KeggInformationManager and therefore src.conf.serverconf,
    which is generated at first launch and is not in the repository, so the
    import fails on any clean checkout. These tests are offline by contract.
    """
    from src.conf.organismDB import dicDatabases

    fallback = _defaultDatabaseMapping()

    assert dicDatabases["osa"][0]["KEGG"] == fallback[0]["KEGG"], \
        (f"osa KEGG id mapping is {dicDatabases['osa'][0]['KEGG']!r}, "
         f"but it used to resolve via the default {fallback[0]['KEGG']!r}")
    assert dicDatabases["osa"][1]["KEGG"] == fallback[1]["KEGG"], \
        (f"osa KEGG name mapping is {dicDatabases['osa'][1]['KEGG']!r}, "
         f"but it used to resolve via the default {fallback[1]['KEGG']!r}")


def test_rice_build_step_keeps_every_default_call_it_replaced():
    """<specie>_resources/build_database.py overrides default/, it does not extend it.

    DBManager.py runs the species file *instead of* default/build_database.py,
    so a call dropped here is silently lost from rice's KEGG install.
    mergeNetworkFiles is the one deliberate omission: it reshapes
    pathways_network.json into {"KEGG": ..., "MapMan": ...} once a MapMan
    network file exists, but the client fetches one flat file per database
    (JobController.js branches on jobView.database). sly, sot and bvu omit it
    for the same reason; for a KEGG-only species it is a no-op, which is why
    osa was unaffected by it before MapMan was added.
    """
    DELIBERATELY_DROPPED = {"mergeNetworkFiles"}

    def calls(path):
        import re as _re
        with open(path) as handle:
            return set(_re.findall(r"^\s*COMMON_BUILD_DB_TOOLS\.(\w+)\(", handle.read(),
                                   _re.MULTILINE))

    default = calls(os.path.join(_SCRIPTS_DIR, "default", "build_database.py"))
    osa = calls(os.path.join(_SCRIPTS_DIR, "osa_resources", "build_database.py"))

    missing = (default - osa) - DELIBERATELY_DROPPED
    assert not missing, f"osa/build_database.py drops default calls: {sorted(missing)}"

    for required in ("processMapManMappingData", "processMapManPathwaysData"):
        assert required in osa, f"osa/build_database.py never calls {required}"

    assert "mergeNetworkFiles" not in osa, \
        "osa must not call mergeNetworkFiles; it reshapes pathways_network.json for MapMan species"


def test_local_resources_are_validated_like_downloaded_ones():
    """A shipped file must not bypass the shape check a download gets.

    downloadMapManResource is the only place that gunzips, drops headers and
    rejects a payload; routing "local" around it would let a wrong-shaped file
    install silently.
    """
    import gzip
    import importlib.util
    import shutil
    import tempfile

    path = os.path.join(_SCRIPTS_DIR, "common_build_database.py")
    spec = importlib.util.spec_from_file_location("common_build_database_local", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workdir = tempfile.mkdtemp()
    try:
        # A gzipped TSV is accepted and arrives decompressed.
        good = os.path.join(workdir, "good.tsv.gz")
        with gzip.open(good, "wb") as handle:
            handle.write(b"LOC_Os01g01010\t4326813\n")
        out = os.path.join(workdir, "good.list")
        assert module.downloadMapManResource(
            {"local": good, "output": "good.list", "decompress": True, "expect": "tsv"},
            out, 0, 1) is True
        with open(out) as handle:
            assert handle.read() == "LOC_Os01g01010\t4326813\n"

        # A file with no tab is rejected, and not left behind for a later
        # checkIfExists run to accept.
        bad = os.path.join(workdir, "bad.tsv.gz")
        with gzip.open(bad, "wb") as handle:
            handle.write(b"<!DOCTYPE html><html>not a mapping</html>\n")
        badOut = os.path.join(workdir, "bad.list")
        try:
            module.downloadMapManResource(
                {"local": bad, "output": "bad.list", "decompress": True, "expect": "tsv"},
                badOut, 0, 1)
            raise AssertionError("a tab-free payload was accepted as a tsv")
        except AssertionError:
            raise
        except Exception:
            pass
        assert not os.path.isfile(badOut), "rejected local payload was left on disk"

        # A missing file is a clear error, not a silent skip.
        try:
            module.downloadMapManResource(
                {"local": os.path.join(workdir, "nope.gz"), "output": "x", "expect": "tsv"},
                os.path.join(workdir, "x"), 0, 1)
            raise AssertionError("a missing local resource was accepted")
        except AssertionError:
            raise
        except Exception:
            pass
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    tests = [
        test_no_mapman_input_comes_from_a_local_path,
        test_rice_cross_link_is_shipped_and_well_formed,
        test_rice_mapman_is_configured_under_osa_not_dosa,
        test_rice_keeps_the_kegg_mapping_it_had_before_the_entry_existed,
        test_rice_build_step_keeps_every_default_call_it_replaced,
        test_local_resources_are_validated_like_downloaded_ones,
        test_every_species_asks_for_the_extra_diagrams,
        test_extra_diagram_manifest_is_complete_and_unambiguous,
        test_extra_diagrams_are_all_the_same_ontology_era,
        test_extra_diagrams_do_not_collide_with_the_gomapman_set,
        test_every_species_declares_the_resources_the_build_step_reads,
        test_output_names_are_the_ones_the_build_step_looks_up,
        test_compressed_downloads_are_marked_for_decompression,
        test_metabolite_export_header_is_dropped,
        test_bin_normalisation_matches_padded_diagram_codes_to_mappings,
        test_bin_normalisation_leaves_everything_else_alone,
        test_bin_normalisation_survives_degenerate_input,
    ]
    for t in tests:
        _check(t.__name__, t)

    print()
    print(f"Passed: {len(_PASSED)} / {len(_PASSED)+len(_FAILED)}")
    if _FAILED:
        for name, msg in _FAILED:
            print(f"  - {name}: {msg.splitlines()[0] if msg else ''}")
        sys.exit(1)


if __name__ == "__main__":
    main()

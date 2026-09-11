"""A species build script may only call a processor whose resource its download_conf declares.

Run from `PaintomicsServer/`:
    python -m src.tests.test_species_build_scripts_declare_their_resources

`acs_resources/build_database.py` called `processUniProtData()`, which reads a
UniProt id-mapping file declared under the "uniprot" key -- a key acs's
download_conf.py never had (its UniProt dump sat as a second entry under
"ensembl", read by nothing). The build died on `EXTERNAL_RESOURCES.get("uniprot")[0]`
after the mapping had been downloaded, on paintomics.org, 2026-09-11, and the
species was never installed. The mismatch is visible in the source: this test
reads every species directory and pairs each live processor call with the
resource key that processor reads.
"""
import os
import re
import sys
import traceback

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AdminTools", "scripts")
sys.path.insert(0, SCRIPTS)
from common_build_database import declaredResourceKeys  # noqa: E402

#: processor -> the EXTERNAL_RESOURCES key it reads first. Processors that only
#: consult COMMON_RESOURCES or the registry (KEGG mapping, pathways, Reactome,
#: the MapMan pathway step) are not listed.
PROCESSOR_RESOURCE = {
    "processEnsemblData": "ensembl",
    "processEnsemblUniProtData": "ensembl_uniprot",
    "processUniProtData": "uniprot",
    "processRefSeqData": "refseq",
    "processRefSeqGeneSymbolData": "refseq",
    "processMapManMappingData": "mapman_gene",
    "processVegaData": "vega",
}
#: Processors that may run without their resource: they skip, warned.
FAIL_SOFT = {"processEnsemblUniProtData", "processUniProtData"}

_LIVE_CALL = re.compile(r"^\s*(?:COMMON_BUILD_DB_TOOLS\.)?(process\w+)\(\)")

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


def liveProcessorCalls(scriptPath):
    with open(scriptPath, encoding="utf-8") as handle:
        return [m.group(1) for m in (_LIVE_CALL.match(line) for line in handle) if m]


def speciesDirectories():
    return sorted(name[:-len("_resources")] for name in os.listdir(SCRIPTS)
                  if name.endswith("_resources") and os.path.isfile(os.path.join(SCRIPTS, name, "build_database.py")))


def test_every_processor_a_build_script_calls_has_its_resource_declared():
    problems = []
    for code in speciesDirectories():
        directory = os.path.join(SCRIPTS, code + "_resources")
        declared = declaredResourceKeys(os.path.join(directory, "download_conf.py"))
        for call in liveProcessorCalls(os.path.join(directory, "build_database.py")):
            key = PROCESSOR_RESOURCE.get(call)
            if key and key not in declared and call not in FAIL_SOFT:
                problems.append("%s: %s() reads the %r resource, which download_conf.py does not declare" % (code, call, key))
    assert not problems, "\n  ".join([""] + problems)


def test_the_anole_declares_the_ensembl_uniprot_route_it_builds():
    declared = declaredResourceKeys(os.path.join(SCRIPTS, "acs_resources", "download_conf.py"))
    calls = liveProcessorCalls(os.path.join(SCRIPTS, "acs_resources", "build_database.py"))
    assert "ensembl_uniprot" in declared, declared
    assert "processEnsemblUniProtData" in calls and "processUniProtData" not in calls, calls
    # The join can only land in groups KEGG's mapping created.
    assert calls.index("processKEGGMappingData") < calls.index("processEnsemblUniProtData"), calls


def test_uniprot_processor_skips_instead_of_dying_without_its_resource():
    """The processor itself must not raise on None[0] any more."""
    import importlib.util
    path = os.path.join(SCRIPTS, "common_build_database.py")
    spec = importlib.util.spec_from_file_location("cbd_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.EXTERNAL_RESOURCES = {}
    module.SPECIE = "xyz"
    module.DATA_DIR = "/nonexistent/"
    result = module.processUniProtData()
    assert result is None, result


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            _check(name, fn)
    print(f"\n{len(_PASSED)} passed, {len(_FAILED)} failed")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

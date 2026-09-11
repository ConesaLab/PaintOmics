"""KEGG's organism taxonomy (br08610) and the lineage column it restores.

Run from `PaintomicsServer/`:
    python -m src.tests.test_kegg_taxonomy

KEGG retired /list/organism. Its replacement, /list/genome, carries no lineage,
so `organisms_all.list` was rebuilt with an empty fourth column -- and
`ensembl_census.py registry`, which selects eukaryotes by that column,
resolved zero species. The lineage is still published, as the BRITE hierarchy
br08610; scripts/kegg_taxonomy.py parses it and spells the column the way the
old list did. These tests pin the parser and that the two consumers actually
use it: the organism list writer (fourth column) and the registry builder
(eukaryote selection).
"""
import ast
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_ADMIN_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AdminTools")
_SCRIPTS = os.path.join(_ADMIN_TOOLS, "scripts")
for extra in (_ADMIN_TOOLS, _SCRIPTS):
    if extra not in sys.path:
        sys.path.insert(0, extra)

from kegg_taxonomy import (parseOrganismTaxonomy, parseGenomeList, legacyLineage, isEukaryote,   # noqa: E402
                           LEGACY_KINGDOM)

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


SAMPLE = """+X
!
AEukaryota
B  Metazoa
C    Chordata
D      Craniata
E        Vertebrata
F          Euteleostomi
G            Mammalia
H              Eutheria
I                Euarchontoglires
J                  Primates
K                    Haplorrhini
L                      Catarrhini
M                        Hominidae
N                          Homo
O                            hsa  Homo sapiens (human)
N                          Pan
O                            ptr  Pan troglodytes (chimpanzee)
B  Viridiplantae
C    Streptophyta
D      Embryophyta
E        Tracheophyta
F          Brassicales
G            Brassicaceae
H              Arabidopsis
I                ath  Arabidopsis thaliana (thale cress)
ABacteria
B  Pseudomonadati
C    Pseudomonadota
D      Gammaproteobacteria
E        Enterobacterales
F          Escherichia
G            eco  Escherichia coli K-12 MG1655
AArchaea
B  Methanobacteriati
C    Methanobacteriota
D      Methanocaldococcus
E        mja  Methanocaldococcus jannaschii
#
"""


def test_parses_every_organism_with_its_kingdom_and_lineage():
    organisms = parseOrganismTaxonomy(SAMPLE)
    assert sorted(organisms) == ["ath", "eco", "hsa", "mja", "ptr"], sorted(organisms)
    hsa = organisms["hsa"]
    assert hsa["kingdom"] == "Eukaryota", hsa
    assert hsa["name"] == "Homo sapiens (human)", hsa
    assert hsa["lineage"][:3] == ["Eukaryota", "Metazoa", "Chordata"], hsa["lineage"]
    assert hsa["lineage"][-1] == "Homo", hsa["lineage"]
    # A sibling leaf under a new genus node must not inherit the previous genus.
    assert organisms["ptr"]["lineage"][-1] == "Pan", organisms["ptr"]["lineage"]
    assert organisms["eco"]["kingdom"] == "Bacteria"
    assert organisms["mja"]["kingdom"] == "Archaea"


def test_a_leaf_after_a_shallower_node_resets_the_stack():
    """ath sits under a B-level node that follows hsa's deep lineage."""
    organisms = parseOrganismTaxonomy(SAMPLE)
    ath = organisms["ath"]["lineage"]
    assert ath[:2] == ["Eukaryota", "Viridiplantae"], ath
    assert "Metazoa" not in ath, ath


def test_legacy_column_spells_the_kingdom_the_old_list_did():
    organisms = parseOrganismTaxonomy(SAMPLE)
    assert legacyLineage(organisms["hsa"]) == "Eukaryotes;Metazoa;Chordata;Craniata"
    assert legacyLineage(organisms["eco"]) == "Prokaryotes;Bacteria;Pseudomonadati;Pseudomonadota;Gammaproteobacteria"
    assert legacyLineage(organisms["mja"]) == "Prokaryotes;Archaea;Methanobacteriati;Methanobacteriota;Methanocaldococcus"
    # The registry selects eukaryotes by this exact prefix.
    assert legacyLineage(organisms["ath"]).startswith("Eukaryotes")
    assert not legacyLineage(organisms["eco"]).startswith("Eukaryotes")


def test_unknown_kingdom_is_rendered_not_dropped():
    entry = {"kingdom": "Viruses", "lineage": ["Viruses", "Riboviria"], "name": "x"}
    assert legacyLineage(entry) == "Viruses;Riboviria"
    assert legacyLineage({"lineage": []}) == ""
    assert set(LEGACY_KINGDOM) == {"Eukaryota", "Bacteria", "Archaea"}


def test_is_eukaryote_tolerates_missing_entries():
    organisms = parseOrganismTaxonomy(SAMPLE)
    assert isEukaryote(organisms["hsa"])
    assert not isEukaryote(organisms["eco"])
    assert not isEukaryote(None)
    assert not isEukaryote({})


def test_lines_outside_the_hierarchy_are_ignored():
    organisms = parseOrganismTaxonomy("+X\n!\n#\n\nAEukaryota\nB  Fungi\nC    sce  Saccharomyces cerevisiae\n")
    assert list(organisms) == ["sce"], organisms
    assert parseOrganismTaxonomy("") == {}
    # A leaf with no enclosing kingdom has no lineage and is not an organism.
    assert parseOrganismTaxonomy("O   hsa  Homo sapiens\n") == {}


def test_genome_list_parser_keeps_organisms_and_drops_bare_descriptions():
    """One parser for /list/genome, shared by the organism list, the registry and the manifest."""
    text = ("T01001\thsa; Homo sapiens (human)\n"
            "T40001\tHuman papillomavirus type 16\n"      # viral: no code
            "T90001\t\n"
            "T03333\tabc; Some organism; strain X (weird)\n"
            "\n")
    parsed = parseGenomeList(text)
    assert parsed == {"hsa": ("T01001", "Homo sapiens (human)"),
                      "abc": ("T03333", "Some organism; strain X (weird)")}, parsed
    assert parseGenomeList("") == {}
    for path, name in ((os.path.join(_ADMIN_TOOLS, "DBManager.py"), "downloadKEGGOrganismList"),
                       (os.path.join(_SCRIPTS, "ensembl_census.py"), "keggEukaryotes"),
                       (os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
                           os.path.abspath(__file__))))), "deploy", "species", "build_manifest.py"), "keggOrganisms")):
        fn = _functionSource(path, name)
        calls = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "parseGenomeList" in calls, "%s() in %s must use the shared parser" % (name, os.path.basename(path))


def _functionSource(path, name):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("%s not found in %s" % (name, path))


def test_organism_list_writer_fills_the_lineage_column():
    """downloadKEGGOrganismList writes legacyLineage(...) as column 4, not ''."""
    fn = _functionSource(os.path.join(_ADMIN_TOOLS, "DBManager.py"), "downloadKEGGOrganismList")
    calls = [n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "fetchOrganismTaxonomy" in calls, "the organism list must fetch br08610"
    assert "legacyLineage" in calls, "the organism list must write the legacy lineage column"
    # The write is a 4-element join whose last element is the lineage, not "".
    joins = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "join" and n.args and isinstance(n.args[0], ast.List)]
    fourColumn = [j for j in joins if len(j.args[0].elts) == 4]
    assert fourColumn, "expected the four-column write"
    last = fourColumn[0].args[0].elts[3]
    assert not (isinstance(last, ast.Constant) and last.value == ""), \
        "column 4 is written as a constant empty string again"


def test_registry_selects_eukaryotes_from_kegg_not_from_the_list_file():
    """keggEukaryotes() keeps only br08610 eukaryotes and refuses an empty answer."""
    import ensembl_census
    calls = []

    def fakeFetch(url, tries=3):
        calls.append(url)
        return ("T01001\thsa; Homo sapiens (human)\n"
                "T00007\teco; Escherichia coli K-12 MG1655\n"
                "T40001\tHuman papillomavirus type 16\n"
                "T00001\tmmu; Mus musculus (house mouse)\n")

    fakeTaxonomy = {"hsa": {"kingdom": "Eukaryota"}, "eco": {"kingdom": "Bacteria"}}
    import kegg_taxonomy
    originalFetch, originalTax = ensembl_census.fetch, kegg_taxonomy.fetchOrganismTaxonomy
    ensembl_census.fetch = fakeFetch
    kegg_taxonomy.fetchOrganismTaxonomy = lambda *a, **k: fakeTaxonomy
    try:
        eukaryotes = ensembl_census.keggEukaryotes()
        assert eukaryotes == {"hsa": ("T01001", "Homo sapiens (human)")}, eukaryotes
        assert any("list/genome" in url for url in calls), calls
        # mmu is in the genome list but absent from the taxonomy -> not selected;
        # an empty result is an error, never an empty registry.
        kegg_taxonomy.fetchOrganismTaxonomy = lambda *a, **k: {"eco": {"kingdom": "Bacteria"}}
        try:
            ensembl_census.keggEukaryotes()
        except Exception as exc:
            assert "refusing" in str(exc), exc
        else:
            raise AssertionError("an empty eukaryote set must raise, not empty the registry")
    finally:
        ensembl_census.fetch, kegg_taxonomy.fetchOrganismTaxonomy = originalFetch, originalTax
    fn = _functionSource(os.path.join(_SCRIPTS, "ensembl_census.py"), "registry")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "keggEukaryotes" in names, "registry() must select species through keggEukaryotes()"
    assert "KEGG_DATA_DIR" not in names, "registry() must not read organisms_all.list any more"


def test_registry_resolves_a_strain_taxid_by_the_species_binomial():
    """ang is A. niger CBS 513.88 (425011) in KEGG; Ensembl files A. niger under 5061."""
    import ensembl_census
    niger = ("fungi", "aspergillus_niger", "5061", "Aspergillus niger", "ASM285v2", "")
    atcc = ("fungi", "aspergillus_niger_atcc_1015", "380704", "Aspergillus niger ATCC 1015", "v3", "")
    byTaxid = {"5061": [niger], "380704": [atcc]}
    byBinomial = {"Aspergillus niger": [niger]}
    hit, how = ensembl_census.resolveGenebuild("Aspergillus niger (black aspergilli)", 425011, byTaxid, byBinomial)
    assert hit == niger and how == "binomial", (hit, how)
    # An exact taxid still wins over the name.
    hit, how = ensembl_census.resolveGenebuild("Aspergillus niger ATCC 1015", 380704, byTaxid, byBinomial)
    assert hit == atcc and how == "taxid", (hit, how)
    # Several assemblies of ONE species (same taxid) are not an ambiguity: the
    # shortest species path wins, as on the taxid route.
    gca = ("fungi", "fungi_ascomycota3_collection/aspergillus_niger_gca_001515345", "5061", "Aspergillus niger", "ASM151534v1", "fungi_ascomycota3_collection")
    hit, how = ensembl_census.resolveGenebuild("Aspergillus niger CBS", 1, byTaxid, {"Aspergillus niger": [gca, niger]})
    assert hit == niger and how == "binomial", (hit, how)
    # Two DIFFERENT taxids under one binomial is ambiguous: no match.
    other = ("fungi", "aspergillus_niger_x", "9999", "Aspergillus niger", "X", "")
    hit, how = ensembl_census.resolveGenebuild("Aspergillus niger CBS", 1, byTaxid, {"Aspergillus niger": [niger, other]})
    assert hit is None and how is None, (hit, how)
    assert ensembl_census.resolveGenebuild("Nothing here", 2, {}, {}) == (None, None)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            _check(name, fn)
    print(f"\n{len(_PASSED)} passed, {len(_FAILED)} failed")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

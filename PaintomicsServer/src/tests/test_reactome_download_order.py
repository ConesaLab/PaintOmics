"""The Reactome download must be staged AFTER the KEGG data, never before.

Run from `PaintomicsServer/`:
    python -m src.tests.test_reactome_download_order

`download --specie=X --kegg=0 --reactome=1` on a species that already has
current/<sp> takes the "COPYING PREVIOUS KEGG DATA" branch, which rmtree's
download/<sp> and copies the installed tree over it. When the Reactome crawl
ran first (it did, until 2026-09-11) that rmtree destroyed the whole Reactome
download -- hsa: 2h08m of transfers thrown away and DOWNLOAD SUCCESS still
reported, the install then dying on the missing ReactomePathway.txt.

The invariant is an ordering inside download_command's per-species loop:
the downloadReactome(specie) call comes after both the KEGG staging (the
rmtree/copytree branch) and the mapping step. It is asserted on the source
because the function is one network-bound body with no seam to run it
against a fake KEGG.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DBMANAGER = os.path.join(ROOT, "src", "AdminTools", "DBManager.py")


def _downloadCommand():
    with open(DBMANAGER, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "download_command":
            return node
    raise AssertionError("download_command not found")


def _calls(fn, name):
    return sorted(n.lineno for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name)


def _staging_rmtree_lines(fn):
    """rmtree(datadir) calls -- the one in the copy branch and the one at species start."""
    lines = []
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "rmtree"
                and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == "datadir"):
            lines.append(n.lineno)
    return sorted(lines)


def test_reactome_is_fetched_after_the_kegg_staging_and_mapping_steps():
    fn = _downloadCommand()
    reactome = _calls(fn, "downloadReactome")
    assert len(reactome) == 1, "expected exactly one downloadReactome call, found %s" % reactome
    kegg = _calls(fn, "getSpecieKeggData")
    mapping = _calls(fn, "getSpecieMappingData")
    rmtrees = _staging_rmtree_lines(fn)
    assert kegg and mapping and rmtrees, (kegg, mapping, rmtrees)
    assert reactome[0] > max(kegg), "downloadReactome must follow getSpecieKeggData"
    assert reactome[0] > max(mapping), "downloadReactome must follow getSpecieMappingData"
    assert reactome[0] > max(rmtrees), \
        "downloadReactome (line %d) precedes an rmtree(datadir) at line %d: the copy branch would delete it" \
        % (reactome[0], max(rmtrees))


def test_reactome_still_runs_inside_the_species_try_block():
    """Moving it must not move it out of the per-species error handling."""
    fn = _downloadCommand()
    reactome = _calls(fn, "downloadReactome")[0]
    kegg = _calls(fn, "getSpecieKeggData")[0]
    enclosing = [n for n in ast.walk(fn) if isinstance(n, ast.Try)
                 and n.lineno < reactome <= max(getattr(h, "end_lineno", h.lineno) for h in n.handlers)]
    assert enclosing, "downloadReactome is no longer inside a try block"
    inner = min(enclosing, key=lambda n: n.lineno)
    assert inner.lineno < kegg, "the KEGG staging and the Reactome fetch must share one species try block"


def test_a_refresh_clears_the_reactome_files_the_copy_branch_brought_along():
    """downloadReactome skips files that exist; a --kegg=0 refresh copies the old crawl in first."""
    fn = _downloadCommand()
    reactome = _calls(fn, "downloadReactome")[0]
    copytree = min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == "copytree")
    clears = [n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "rmtree"
              and n.args and "reactome" in ast.unparse(n.args[0])]
    assert clears, "the copy branch must rmtree the staged reactome/ directory when a Reactome refresh is requested"
    assert copytree < clears[0] < reactome, (copytree, clears, reactome)


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS  " + name)
            except AssertionError as exc:
                failed += 1
                print("FAIL  %s: %s" % (name, exc))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

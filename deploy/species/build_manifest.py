#!/usr/bin/env python3
"""Build the all-species install manifest for paintomics.org.

One row per KEGG organism, saying which pathway sources can serve it and what
the installer should do about it. The manifest is the contract for
allspecies_runner.py: nothing is installed that is not in it, and every row
carries the reason for its decision so the final report is derivable from it.

Sources, each resolved against the live publisher:

  KEGG      https://rest.kegg.jp/list/genome  (11,950 organisms, 2026-09-11)
            + br08610 for the kingdom. Every organism is in scope.
  Reactome  https://reactome.org/download/current/ReactomePathways.txt -- the
            species column names Reactome's own organisms (16 in 2026). They
            are joined to KEGG codes by NCBI taxid through a fixed table,
            because Reactome's species abbreviation is what downloadReactome
            matches on ("R-HSA-", "R-MMU-", ...) and it happens to equal the
            KEGG code for every one of them.
  MapMan    GoMapMan's `paintomics` export (CC BY-NC-SA). GoMapMan codes are
            NOT KEGG codes: each is resolved by organism NAME against KEGG and
            installed only where both name the same organism and a KEGG
            cross-link exists (see MAPMAN below for every verdict).
  OmniPath  the web service serves exactly taxids 9606/10090/10116.

Usage:
    python3 deploy/species/build_manifest.py --cache-dir /tmp/manifest-cache \
        [--census census.json] [--registry PaintomicsServer/src/AdminTools/scripts/common_resources/ensembl_genebuilds.json] \
        -o deploy/species/manifest.tsv

--census is what the server already holds: the TSV of `species_report.py
--no-reach` (preferred; carries the identifier tables), or the JSON object of
`allspecies_runner.py census` ({code: pathway count}). Without it every KEGG
organism is `install`; with it, organisms already carrying their sources are
`keep` and registered genebuilds without Ensembl tables become `refresh`.
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "PaintomicsServer", "src", "AdminTools", "scripts"))
from kegg_taxonomy import parseOrganismTaxonomy, parseGenomeList, KEGG_TAXONOMY_URL, KEGG_GENOME_URL  # noqa: E402

REACTOME_PATHWAYS_URL = "https://reactome.org/download/current/ReactomePathways.txt"
GOMAPMAN_LISTING_URL = "https://gomapman.nib.si/api/GetFolderInfo/protein_2018-05-25%7Cpaintomics"

#: Reactome species name -> (KEGG code, NCBI taxid). Reactome publishes no
#: taxid column in ReactomePathways.txt; the join is by name, verified against
#: KEGG's own /get/gn:<T> TAXONOMY lines when this table was written.
REACTOME_SPECIES = {
    "Homo sapiens": ("hsa", 9606),
    "Mus musculus": ("mmu", 10090),
    "Rattus norvegicus": ("rno", 10116),
    "Bos taurus": ("bta", 9913),
    "Sus scrofa": ("ssc", 9823),
    "Canis familiaris": ("cfa", 9615),
    "Gallus gallus": ("gga", 9031),
    "Danio rerio": ("dre", 7955),
    "Xenopus tropicalis": ("xtr", 8364),
    "Drosophila melanogaster": ("dme", 7227),
    "Caenorhabditis elegans": ("cel", 6239),
    "Dictyostelium discoideum": ("ddi", 44689),
    "Schizosaccharomyces pombe": ("spo", 4896),
    "Saccharomyces cerevisiae": ("sce", 4932),
    "Plasmodium falciparum": ("pfa", 5833),
    "Mycobacterium tuberculosis": ("mtu", 1773),
}

#: GoMapMan organism code -> verdict. Every gene-to-mapman_<code> file in the
#: paintomics export is listed; a code missing here fails the build so a new
#: export cannot be installed under an unresolved code by accident.
MAPMAN = {
    "ath": ("ath", "install", "Arabidopsis thaliana on both sides; GoMapMan ships gene-to-entrez_ath"),
    "sly": ("sly", "install", "Solanum lycopersicum on both sides; GoMapMan ships gene-to-entrez_sly"),
    "stu": ("sot", "install", "Solanum tuberosum: GoMapMan stu = KEGG sot; GoMapMan ships stu_pgsc_ncbi"),
    "osa": ("osa", "install", "Oryza sativa japonica on both sides; cross-link derived in osa_resources (PR #42)"),
    "nta": (None, "exclude", "Nicotiana tabacum keyed on UniGene clusters (ntaUG17) NCBI retired in 2019; nothing to cross-link"),
    "bvu": ("bvg", "exclude", "Beta vulgaris (KEGG bvu is the bacterium Phocaeicola vulgatus); RefBeet1.1 ids (Bv1_000020_oary) have no cross-reference in NCBI Gene or UniProt to KEGG bvg's GeneIDs"),
    "tae": ("taes", "exclude", "Triticum aestivum (KEGG tae is the bacterium Tepidanaerobacter acetatoxydans); keyed on 2018 TrEMBL accessions of which 1,385 of 69,215 are in KEGG taes's UniProt conversion (2%): a mapman_gene_id island"),
    "tca": ("tcc", "exclude", "Theobroma cacao (KEGG tca is the beetle Tribolium castaneum); Phytozome9.1 Thecc1EG ids have no published bridge to KEGG tcc's NCBI GeneIDs (KEGG maps only 4,106 tcc genes to UniProt)"),
    "cam": (None, "exclude", "not chickpea: ids are Pgl_GLEAN_*/UniVie_pm; KEGG cam is Cicer arietinum"),
}

#: Reactome species that publish too little to install, with the measurement.
REACTOME_EXCLUDED = {
    "mtu": ("Reactome publishes 13 pathways for M. tuberculosis in one tree (R-MTU-870392) and draws a "
            "diagram for that root only; the 2026-09-11 refresh fetched 1 diagram. mtu_resources builds "
            "no Reactome tables, so it stays KEGG-only"),
}

OMNIPATH = {"hsa": 9606, "mmu": 10090, "rno": 10116}

#: Install order. Eukaryotes first (they carry Ensembl identifiers and are
#: what users ask for), then archaea (small), then the bacteria.
PRIORITY = {"Eukaryota": 1, "Archaea": 2, "Bacteria": 3}


def fetch(url, cacheDir, name):
    path = os.path.join(cacheDir, name)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    os.makedirs(cacheDir, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as response:
        text = response.read().decode("utf-8", "replace")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


def keggOrganisms(cacheDir):
    organisms = {code: {"T": tNumber, "name": name}
                 for code, (tNumber, name) in parseGenomeList(fetch(KEGG_GENOME_URL, cacheDir, "kegg_genome.tsv")).items()}
    taxonomy = parseOrganismTaxonomy(fetch(KEGG_TAXONOMY_URL, cacheDir, "br08610.txt"))
    for code, entry in organisms.items():
        tax = taxonomy.get(code, {})
        entry["kingdom"] = tax.get("kingdom", "")
        entry["group"] = (tax.get("lineage") or ["", ""])[1] if len(tax.get("lineage") or []) > 1 else ""
    return organisms


def reactomeSpecies(cacheDir):
    counts = {}
    for line in fetch(REACTOME_PATHWAYS_URL, cacheDir, "ReactomePathways.txt").splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 3:
            counts[parts[2]] = counts.get(parts[2], 0) + 1
    unknown = sorted(set(counts) - set(REACTOME_SPECIES))
    if unknown:
        raise SystemExit("Reactome publishes species not in REACTOME_SPECIES: %s" % unknown)
    return {REACTOME_SPECIES[name][0]: (name, n) for name, n in counts.items()}


def gomapmanCodes(cacheDir):
    listing = fetch(GOMAPMAN_LISTING_URL, cacheDir, "gomapman_paintomics.json")
    codes = sorted(set(re.findall(r"gene-to-mapman_([a-z]+)\.tsv\.gz", listing)))
    unknown = sorted(set(codes) - set(MAPMAN))
    if unknown:
        raise SystemExit("GoMapMan ships organisms with no verdict in MAPMAN: %s" % unknown)
    return codes


def loadCensus(path):
    """{code: {"sources": {...}, "tables": {...}}} of what the server holds, or {}.

    Three shapes are accepted:
      * the TSV species_report.py writes (kind=source/table rows) -- the one to
        use: `species_report.py --no-reach` inside the app container is the
        census of the live server, tables included;
      * the JSON object `allspecies_runner.py census` prints, {code: n_kegg_pathways}
        (pathway counts only, so the Ensembl-refresh rule cannot fire from it);
      * a JSON list of [code, {source: n}, {table: n}] triples.
    """
    if not path:
        return {}
    census = {}
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            return {code: {"sources": {"KEGG": int(n or 0)}, "tables": {}} for code, n in loaded.items()}
        return {row[0]: {"sources": row[1], "tables": row[2]} for row in loaded}
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            entry = census.setdefault(row["code"], {"sources": {}, "tables": {}})
            if row["kind"] == "source":
                entry["sources"][row["name"]] = int(row["n"] or 0)
            elif row["kind"] == "table":
                entry["tables"][row["name"]] = int(row["n"] or 0)
    return census


def loadRegistry(path):
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle).get("genebuilds", {})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", default=os.path.join(HERE, "cache"))
    parser.add_argument("--census", default=None, help="census JSON of what the server already holds")
    parser.add_argument("--registry", default=os.path.join(HERE, "..", "..", "PaintomicsServer", "src", "AdminTools",
                                                           "scripts", "common_resources", "ensembl_genebuilds.json"))
    parser.add_argument("-o", "--output", default=os.path.join(HERE, "manifest.tsv"))
    args = parser.parse_args(argv)

    organisms = keggOrganisms(args.cache_dir)
    reactome = reactomeSpecies(args.cache_dir)
    gomapman = gomapmanCodes(args.cache_dir)
    census = loadCensus(args.census)
    registry = loadRegistry(args.registry)

    mapmanFor = {}   # KEGG code -> (gomapman code, verdict, reason)
    for gcode in gomapman:
        kcode, verdict, reason = MAPMAN[gcode]
        if kcode:
            mapmanFor[kcode] = (gcode, verdict, reason)

    columns = ["code", "T", "kingdom", "group", "name", "priority", "action",
               "kegg", "reactome", "mapman", "omnipath", "ensembl_genebuild",
               "installed_kegg", "installed_reactome", "installed_mapman", "installed_omnipath",
               "reactome_pathways_published", "mapman_source_code", "note"]
    rows = []
    for code in sorted(organisms, key=lambda c: (PRIORITY.get(organisms[c]["kingdom"], 9), c)):
        entry = organisms[code]
        have = census.get(code, {})
        sources = have.get("sources", {})
        installedKegg = int(sources.get("KEGG", 0) > 0)
        installedReactome = int(sources.get("Reactome", 0) > 0)
        installedMapman = int(sources.get("MapMan", 0) > 0)
        installedOmnipath = int(sources.get("OmniPath", 0) > 0)
        notes = []
        wantReactome = int(code in reactome and code not in REACTOME_EXCLUDED)
        if code in REACTOME_EXCLUDED:
            notes.append("Reactome excluded: " + REACTOME_EXCLUDED[code])
        gcode, verdict, reason = mapmanFor.get(code, ("", "", ""))
        wantMapman = int(verdict == "install")
        wantOmnipath = int(code in OMNIPATH)
        if verdict == "exclude":
            notes.append("MapMan excluded: " + reason)
        elif verdict == "install":
            notes.append("MapMan: " + reason)
        if code in reactome and reactome[code][1] < 50:
            notes.append("Reactome publishes only %d pathways" % reactome[code][1])
        # What to do. `install`: not on the server yet. `refresh`: on the server
        # but missing a source it could carry (re-download that source, rebuild).
        # `keep`: complete. MapMan contamination (a source present that the
        # manifest says must not be) is `rebuild`: rebuild from current/ with the
        # installer that no longer declares it.
        # A species installed before its Ensembl genebuild was registered has
        # KEGG ids only; a mapping refresh (download --kegg=0 --mapping=1 and a
        # rebuild) gives it the Ensembl tables without touching its pathways.
        tables = have.get("tables", {})
        missingEnsembl = code in registry and installedKegg and tables.get("ensembl_gene", -1) <= 0
        if not installedKegg:
            action = "install"
        elif (wantReactome and not installedReactome) or (wantMapman and not installedMapman) \
                or (wantOmnipath and not installedOmnipath) or missingEnsembl:
            action = "refresh"
            if missingEnsembl:
                notes.append("registered Ensembl genebuild but no ensembl_gene table: mapping refresh")
        elif installedMapman and not wantMapman:
            action = "rebuild"
            notes.append("carries MapMan data it must not (organism-code collision); rebuild drops it")
        else:
            action = "keep"
        # A refresh or rebuild touches a species people already use; it goes
        # first, before the thousands of new installs.
        priority = 0 if action in ("refresh", "rebuild") else PRIORITY.get(entry["kingdom"], 9)
        rows.append({
            "code": code, "T": entry["T"], "kingdom": entry["kingdom"], "group": entry["group"],
            "name": entry["name"], "priority": priority, "action": action,
            "kegg": 1, "reactome": wantReactome, "mapman": wantMapman, "omnipath": wantOmnipath,
            "ensembl_genebuild": int(code in registry),
            "installed_kegg": installedKegg, "installed_reactome": installedReactome,
            "installed_mapman": installedMapman, "installed_omnipath": installedOmnipath,
            "reactome_pathways_published": reactome[code][1] if code in reactome else "",
            "mapman_source_code": gcode, "note": "; ".join(notes),
        })

    # Priority order is the install order: refreshes first, then eukaryotes,
    # archaea, bacteria; alphabetical within a class.
    rows.sort(key=lambda r: (r["priority"], PRIORITY.get(r["kingdom"], 9), r["code"]))

    # Species the server holds that KEGG no longer lists stay visible here.
    for code in sorted(set(census) - set(organisms)):
        rows.append({c: "" for c in columns})
        rows[-1].update({"code": code, "action": "keep", "priority": 9, "note": "installed but absent from KEGG's current organism list"})

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    summary = {}
    for row in rows:
        key = (row["kingdom"] or "?", row["action"])
        summary[key] = summary.get(key, 0) + 1
    sys.stderr.write("wrote %d rows to %s\n" % (len(rows), args.output))
    for (kingdom, action), n in sorted(summary.items()):
        sys.stderr.write("  %-10s %-8s %6d\n" % (kingdom, action, n))
    sys.stderr.write("  reactome: %d species (%s); excluded: %s\n" % (
        len(reactome), " ".join(sorted(reactome)), " ".join(sorted(REACTOME_EXCLUDED)) or "-"))
    sys.stderr.write("  mapman install: %s; excluded: %s\n" % (
        " ".join(k for k, (g, v, r) in sorted(mapmanFor.items()) if v == "install"),
        " ".join(g for g, (k, v, r) in sorted(MAPMAN.items()) if v == "exclude")))
    sys.stderr.write("  ensembl genebuilds registered: %d\n" % sum(1 for r in rows if r["ensembl_genebuild"] == 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

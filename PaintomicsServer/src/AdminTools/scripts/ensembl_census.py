#!/usr/bin/env python
"""Which installed species carry Ensembl identifiers, and do they reach KEGG?

Three commands, all read-only against MongoDB:

  census    one line per <code>-paintomics database: xref rows per identifier
            table, whether the organism has a registered Ensembl genebuild, and
            whether the Ensembl tables are present. Exit 1 if a species with a
            genebuild lacks them (the invariant the installer now guarantees).
  verify    for every species with an ensembl_gene table, sample N gene ids
            and translate them through the real mapper
            (FeatureNamesToKeggIDsMapper.findIDsByFeaturesName) into the KEGG
            identifier table organismDB names for it. Reports the fraction that
            reached it. Exit 1 below --min-fraction.
  registry  rebuild scripts/common_resources/ensembl_genebuilds.json from the
            KEGG organism list, KEGG's taxonomy ids and Ensembl's species lists
            (network). Bacteria are excluded on purpose: Ensembl Bacteria names
            genes by the same locus tags KEGG uses as kegg_id.

Run inside the server environment (it imports conf.serverconf for the MongoDB
host unless --mongo-host is given):

    cd PaintomicsServer && PYTHONPATH=. python src/AdminTools/scripts/ensembl_census.py census
    cd PaintomicsServer && PYTHONPATH=. python src/AdminTools/scripts/ensembl_census.py verify --sample 100
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, SRC)                      # conf.serverconf, common.*
sys.path.insert(0, os.path.dirname(SRC))     # src.common.* (the tests' spelling)

REGISTRY = os.path.join(HERE, "common_resources", "ensembl_genebuilds.json")
ENSEMBL_TABLES = ("ensembl_gene", "ensembl_transcript", "ensembl_peptide")
DIVISION_LISTS = {
    "vertebrates": "https://ftp.ensembl.org/pub/{release}/species_EnsemblVertebrates.txt",
    "plants": "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/plants/species_EnsemblPlants.txt",
    "fungi": "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/fungi/species_EnsemblFungi.txt",
    "protists": "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/protists/species_EnsemblProtists.txt",
    "metazoa": "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/metazoa/species_EnsemblMetazoa.txt",
}


def mongoClient(args):
    import pymongo
    host, port = args.mongo_host, args.mongo_port
    if host is None:
        from conf.serverconf import MONGODB_HOST, MONGODB_PORT
        host, port = MONGODB_HOST, port or MONGODB_PORT
    return pymongo.MongoClient(host, port or 27017, serverSelectionTimeoutMS=10000)


def loadRegistry():
    with open(REGISTRY, encoding="utf-8") as handle:
        return json.load(handle)["genebuilds"]


def ownDirectoryDeclares(code):
    conf = os.path.join(HERE, code + "_resources", "download_conf.py")
    if not os.path.isfile(conf):
        return set()
    with open(conf, encoding="utf-8") as handle:
        return set(re.findall(r'^\s*"([a-z_]+)"\s*:\s*\[', handle.read(), re.M))


def hasGenebuild(code, registry):
    return code in registry or bool(ownDirectoryDeclares(code) & {"ensembl", "ensembl_uniprot"})


def speciesDatabases(client):
    return sorted(name[:-len("-paintomics")] for name in client.list_database_names()
                  if name.endswith("-paintomics") and name != "global-paintomics")


def tableCounts(db):
    return {row["dbname"]: db.xref.count_documents({"dbname_id": row["_id"]})
            for row in db.dbname.find({}, {"dbname": 1})}


def census(args):
    client = mongoClient(args)
    registry = loadRegistry()
    failures = []
    print("\t".join(("code", "genebuild", "ensembl_gene", "ensembl_transcript", "ensembl_peptide",
                     "entrezgene", "kegg_id", "empty_tables", "all_tables")))
    for code in speciesDatabases(client):
        counts = tableCounts(client[code + "-paintomics"])
        expected = hasGenebuild(code, registry)
        empty = sorted(name for name, n in counts.items() if n == 0 and name != "random_transcript_db_id")
        present = all(counts.get(table, 0) > 0 for table in ENSEMBL_TABLES)
        if expected and not present:
            failures.append(code)
        print("\t".join([code, "yes" if expected else "no"] +
                        [str(counts.get(t, 0)) for t in ENSEMBL_TABLES + ("entrezgene", "kegg_id")] +
                        [",".join(empty) or "-", " ".join("%s=%d" % kv for kv in sorted(counts.items()))]))
    if failures:
        sys.stderr.write("MISSING Ensembl tables on %d species with a genebuild: %s\n"
                         % (len(failures), " ".join(failures)))
        return 1
    sys.stderr.write("every species with a genebuild carries its Ensembl tables\n")
    return 0


def verify(args):
    client = mongoClient(args)
    registry = loadRegistry()
    from common.FeatureNamesToKeggIDsMapper import (
        findIDsByFeaturesName, getDatabasesByOrganismCode)
    random.seed(args.seed)
    below = []
    print("\t".join(("code", "target_table", "sampled", "reached", "fraction")))
    for code in speciesDatabases(client):
        db = client[code + "-paintomics"]
        geneTable = db.dbname.find_one({"dbname": "ensembl_gene"}, {"_id": 1})
        if geneTable is None:
            continue
        targetName = getDatabasesByOrganismCode(code)[0].get("KEGG")
        target = db.dbname.find_one({"dbname": targetName}, {"_id": 1})
        if target is None:
            print("\t".join((code, targetName or "-", "0", "0", "no-target-table")))
            continue
        ids = [row["display_id"] for row in db.xref.find({"dbname_id": geneTable["_id"]}, {"display_id": 1})]
        if not ids:
            print("\t".join((code, targetName, "0", "0", "empty")))
            continue
        sample = random.sample(ids, min(args.sample, len(ids)))
        translated = findIDsByFeaturesName("ensembl-census-" + code + "-" + str(time.time()),
                                           sample, db, target["_id"])
        reached = sum(1 for name in sample if translated.get(name))
        fraction = reached / float(len(sample))
        flag = "" if (fraction >= args.min_fraction or not hasGenebuild(code, registry)) else "\tBELOW"
        if flag:
            below.append(code)
        print("\t".join((code, targetName, str(len(sample)), str(reached), "%.3f" % fraction)) + flag)
    if below:
        sys.stderr.write("%d species below %.2f: %s\n" % (len(below), args.min_fraction, " ".join(below)))
        return 1
    return 0


def fetch(url, tries=3):
    last = None
    for _ in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=120).read().decode("utf-8", "replace")
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise Exception("cannot fetch %s: %s" % (url, last))


def ensemblSpecies():
    """(division, species, taxid, name, assembly, collection) for every Ensembl genebuild."""
    rows = []
    release = None
    for division, url in DIVISION_LISTS.items():
        if "{release}" in url:
            if release is None:
                releases = [int(r) for r in re.findall(r'href="release-(\d+)/"', fetch("https://ftp.ensembl.org/pub/"))]
                release = "release-%d" % max(releases)
            url = url.format(release=release)
        for line in fetch(url).splitlines():
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 14:
                continue
            match = re.match(r"^(.*_collection)_core_", parts[13])
            rows.append((division, parts[1], parts[3], parts[0], parts[4], match.group(1) if match else ""))
    return rows, release


def keggTaxid(tNumber):
    match = re.search(r"TAXONOMY\s+TAX:(\d+)", fetch("https://rest.kegg.jp/get/gn:" + tNumber))
    return int(match.group(1)) if match else None


def dumpsPublished(division, path, release):
    base = ("https://ftp.ensembl.org/pub/%s/tsv/" % release if division == "vertebrates"
            else "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/%s/tsv/" % division)
    try:
        listing = fetch(base + path + "/")
    except Exception:
        return []
    kinds = {name.rsplit(".", 3)[-3] for name in re.findall(r'href="([^"]*\.tsv\.gz)"', listing)}
    return [kind for kind in ("entrez", "uniprot") if kind in kinds]


def registry(args):
    """Rebuild the registry for every eukaryote in KEGG's organism list, or for --species."""
    from conf.serverconf import KEGG_DATA_DIR
    organisms = os.path.join(KEGG_DATA_DIR, "current", "common", "organisms_all.list")
    wanted = set(args.species.split(",")) if args.species else None
    lineage = {}
    with open(organisms, encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 4 and parts[3].startswith("Eukaryotes"):
                lineage[parts[1]] = (parts[0], parts[2])
    if wanted is not None:
        lineage = {code: value for code, value in lineage.items() if code in wanted}
    if args.installed:
        installed = set(speciesDatabases(mongoClient(args)))
        lineage = {code: value for code, value in lineage.items() if code in installed}

    rows, release = ensemblSpecies()
    byTaxid = {}
    for row in rows:
        byTaxid.setdefault(row[2], []).append(row)
    with open(REGISTRY, encoding="utf-8") as handle:
        document = json.load(handle)
    genebuilds = document["genebuilds"] if args.merge else {}
    for code, (tNumber, name) in sorted(lineage.items()):
        taxid = keggTaxid(tNumber)
        time.sleep(0.35)
        hits = byTaxid.get(str(taxid), [])
        if not hits:
            sys.stderr.write("%s: no Ensembl genebuild for taxid %s (%s)\n" % (code, taxid, name))
            genebuilds.pop(code, None)
            continue
        division, species, _, _, assembly, collection = sorted(hits, key=lambda row: (len(row[1]), row[1]))[0]
        path = (collection + "/" if collection else "") + species
        dumps = dumpsPublished(division, path, release)
        if not dumps:
            sys.stderr.write("%s: %s publishes neither an entrez nor a uniprot dump\n" % (code, path))
            genebuilds.pop(code, None)
            continue
        genebuilds[code] = {"name": name, "taxid": taxid, "division": division,
                            "species-path": path, "assembly": assembly, "xrefs": dumps}
        sys.stderr.write("%s: %s %s %s\n" % (code, division, path, dumps))
    document["genebuilds"] = dict(sorted(genebuilds.items()))
    with open(REGISTRY, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1)
        handle.write("\n")
    sys.stderr.write("wrote %d genebuilds to %s\n" % (len(document["genebuilds"]), REGISTRY))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("census", "verify", "registry"))
    parser.add_argument("--mongo-host", default=None)
    parser.add_argument("--mongo-port", type=int, default=None)
    parser.add_argument("--sample", type=int, default=100, help="verify: gene ids per species")
    parser.add_argument("--min-fraction", type=float, default=0.8, help="verify: fail below this")
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--species", default=None, help="registry: comma-separated codes to (re)resolve")
    parser.add_argument("--installed", action="store_true", help="registry: only species installed in MongoDB")
    parser.add_argument("--merge", action="store_true", help="registry: keep entries not visited")
    args = parser.parse_args(argv)
    return {"census": census, "verify": verify, "registry": registry}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

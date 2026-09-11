#!/usr/bin/env python
"""Per species x source x identifier-type census, measured with the real mapper.

For every <code>-paintomics database:
  * pathway documents per source (KEGG, Reactome, MapMan, OmniPath);
  * xref rows per identifier table;
  * for every identifier table, a STRIDE sample of --sample ids (every k-th id
    in _id order, never the first N, which hides gaps at the end of a file)
    translated through FeatureNamesToKeggIDsMapper.findIDsByFeaturesName into
    the KEGG table organismDB names for the species (or the fallback,
    kegg_id) -- the fraction that reach it is what a user upload of that id
    type would experience.

Run inside the server environment:
    cd PaintomicsServer && PYTHONPATH=. python src/AdminTools/scripts/species_report.py \
        [--species a,b,c] [--sample 40] [--out report.tsv]

Output: one TSV with two kinds of rows, distinguished by the `kind` column:
    kind=source   code  source  n_pathways
    kind=table    code  table   rows  sampled  reached  fraction  target
It is read-only.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.dirname(SRC))

SKIP_TABLES = ("random_transcript_db_id",)


def speciesDatabases(client):
    return sorted(name[:-len("-paintomics")] for name in client.list_database_names()
                  if name.endswith("-paintomics") and name != "global-paintomics")


def stridePositions(total, n):
    """n row indices spread evenly over [0, total), the last one near the end.

    Floor division (`total // n`) as the stride collapsed to 1 whenever
    n <= total < 2n and stopped after the first n rows -- the "first N" this
    sampler exists to avoid. Every position here is `i * total / n`, so the
    sample always spans the whole table; fewer rows than n means all of them.
    """
    if total <= n:
        return list(range(total))
    return sorted({(i * total) // n for i in range(n)})


def strideSample(db, dbnameId, n):
    total = db.xref.count_documents({"dbname_id": dbnameId})
    if total == 0:
        return [], 0
    wanted = stridePositions(total, n)
    last = wanted[-1]
    wantedSet = set(wanted)
    cursor = db.xref.find({"dbname_id": dbnameId}, {"display_id": 1}).sort("_id", 1)
    sample = []
    for index, row in enumerate(cursor):
        if index in wantedSet:
            sample.append(row["display_id"])
        if index >= last:
            break
    return sample, total


def positiveInt(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("--sample must be >= 1, got %s" % value)
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--species", default=None, help="comma-separated codes (default: every installed)")
    parser.add_argument("--sample", type=positiveInt, default=40, help="ids per table (>= 1)")
    parser.add_argument("--out", default="-")
    parser.add_argument("--mongo-host", default=None)
    parser.add_argument("--mongo-port", type=int, default=None)
    parser.add_argument("--no-reach", action="store_true", help="counts only, no mapper calls")
    args = parser.parse_args(argv)

    import pymongo
    host, port = args.mongo_host, args.mongo_port
    if host is None:
        from conf.serverconf import MONGODB_HOST, MONGODB_PORT
        host, port = MONGODB_HOST, port or MONGODB_PORT
    client = pymongo.MongoClient(host, port or 27017, serverSelectionTimeoutMS=10000)
    from common.FeatureNamesToKeggIDsMapper import findIDsByFeaturesName, getDatabasesByOrganismCode

    codes = args.species.split(",") if args.species else speciesDatabases(client)
    out = sys.stdout if args.out == "-" else open(args.out, "w", encoding="utf-8")
    out.write("\t".join(("kind", "code", "name", "n", "sampled", "reached", "fraction", "target")) + "\n")
    started = time.time()
    for index, code in enumerate(codes):
        db = client[code + "-paintomics"]
        for row in db.kegg.aggregate([{"$group": {"_id": "$source", "n": {"$sum": 1}}}]):
            out.write("\t".join(("source", code, str(row["_id"]), str(row["n"]), "", "", "", "")) + "\n")
        tables = {row["dbname"]: row["_id"] for row in db.dbname.find({}, {"dbname": 1})}
        targetName = getDatabasesByOrganismCode(code)[0].get("KEGG")
        targetId = tables.get(targetName)
        for name in sorted(tables):
            if name in SKIP_TABLES:
                continue
            if args.no_reach or targetId is None or name == targetName:
                total = db.xref.count_documents({"dbname_id": tables[name]})
                out.write("\t".join(("table", code, name, str(total), "", "", "", targetName or "-")) + "\n")
                continue
            sample, total = strideSample(db, tables[name], args.sample)
            if not sample:
                out.write("\t".join(("table", code, name, "0", "0", "0", "", targetName)) + "\n")
                continue
            translated = findIDsByFeaturesName("species-report-" + code + "-" + str(time.time()), sample, db, targetId)
            reached = sum(1 for value in sample if translated.get(value))
            out.write("\t".join(("table", code, name, str(total), str(len(sample)), str(reached),
                                 "%.3f" % (reached / float(len(sample))), targetName)) + "\n")
        out.flush()
        if (index + 1) % 50 == 0:
            sys.stderr.write("%d/%d species, %d s\n" % (index + 1, len(codes), int(time.time() - started)))
    if out is not sys.stdout:
        out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

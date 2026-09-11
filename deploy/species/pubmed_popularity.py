#!/usr/bin/env python3
"""How often each KEGG organism is written about: PubMed hit counts per binomial.

The all-species install at KEGG's tolerated rate takes about twelve days for
11,700 organisms. Asked to start with the 3,000 most popular instead, this is
the measure: the number of PubMed records whose title or abstract names the
organism's binomial (the first two words of KEGG's name; `Homo sapiens`,
`Escherichia coli`). Strains share their species' count, so the manifest keeps
one KEGG code per binomial (the first KEGG lists, its reference genome) and
ranks species by this number. Names of the shape `Genus sp. XYZ` are counted by
their full KEGG name, not the genus, so a genus-level count cannot promote an
uncharacterised isolate.

    python3 deploy/species/pubmed_popularity.py --manifest deploy/species/manifest.tsv \
        -o deploy/species/popularity.tsv [--resume]

E-utilities allow 3 requests/s without a key, 10 with NCBI_API_KEY in the
environment. Counts are cached in the output file so an interrupted run resumes.
"""
import argparse
import csv
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"


def binomialOf(name):
    """`Homo sapiens` from `Homo sapiens (human)`; the full name for `Genus sp. XYZ`."""
    clean = re.sub(r"\s*\(.*?\)\s*$", "", name).strip()
    words = [word.strip("'\"") for word in clean.split()]
    words = [word for word in words if word]
    clean = " ".join(words)
    if len(words) >= 2 and words[1].lower() in ("sp.", "sp", "cf.", "aff."):
        return clean
    if words and words[0].lower() in ("candidatus", "uncultured"):
        return " ".join(words[:3]) if len(words) >= 3 else clean
    return " ".join(words[:2]) if len(words) >= 2 else clean


def pubmedCount(term, apiKey=None, tries=4):
    query = '"%s"[Title/Abstract]' % term
    params = {"db": "pubmed", "term": query, "rettype": "count", "retmode": "json"}
    if apiKey:
        params["api_key"] = apiKey
    url = ESEARCH + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                import json
                return int(json.load(response)["esearchresult"]["count"])
        except Exception as exc:
            last = exc
            sys.stderr.write("  retry %d for %r: %s\n" % (attempt + 1, term, str(exc)[:120]))
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("PubMed count failed for %r: %s" % (term, last))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--resume", action="store_true", help="keep counts already in the output file")
    args = parser.parse_args(argv)
    apiKey = os.environ.get("NCBI_API_KEY")
    delay = 0.11 if apiKey else 0.35

    with open(args.manifest, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    binomials = {}
    for row in rows:
        if row["code"] and row["name"]:
            binomials[row["code"]] = binomialOf(row["name"])

    counts = {}
    if args.resume and os.path.isfile(args.output):
        with open(args.output, encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row["pubmed_count"] != "":
                    counts[row["binomial"]] = int(row["pubmed_count"])
    todo = sorted(set(binomials.values()) - set(counts))
    sys.stderr.write("%d organisms, %d distinct binomials, %d to count (%s key)\n"
                     % (len(binomials), len(set(binomials.values())), len(todo), "with" if apiKey else "no"))
    started = time.time()
    for index, term in enumerate(todo):
        counts[term] = pubmedCount(term, apiKey)
        time.sleep(delay)
        if (index + 1) % 50 == 0:
            sys.stderr.write("  %d/%d in %d s\n" % (index + 1, len(todo), int(time.time() - started)))
            writeOut(args.output, rows, binomials, counts)
    writeOut(args.output, rows, binomials, counts)
    sys.stderr.write("wrote %s\n" % args.output)
    return 0


def writeOut(path, rows, binomials, counts):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["code", "binomial", "pubmed_count"])
        for row in rows:
            code = row["code"]
            if code in binomials:
                writer.writerow([code, binomials[code], counts.get(binomials[code], "")])
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())

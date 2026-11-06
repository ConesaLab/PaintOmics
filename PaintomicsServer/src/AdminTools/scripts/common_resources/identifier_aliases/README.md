# Identifier aliases

Gene ids that the installed tables of a species cannot reach, and what each of them
names there. `processIdentifierAliases()` in `common_build_database.py` applies the
table of a species while it is built, so they take effect at that species' next
build and survive every later one. A species with no table here gets a no-op. To apply
a table to an installed species, refresh it as `deploy/README.md` describes:
`download --specie=<code> --kegg=0 --mapping=1`, then `install --specie=<code> --common=0`.

`scripts/default/build_database.py` calls it, which is what builds these three, and so
does `osa_resources/build_database.py`, which has to keep every call of the default (rice
has no table, so there it does nothing). No other species script does. So do not create
`zma_resources`, `psat_resources` or `sbi_resources`: a directory wins over the default
script, and the table would stop being read.

## The tables

`<code>.tsv.gz`, gzip with no file name and no timestamp, sorted, with a header row.

| Column | What it holds |
|---|---|
| `alias` | The id as a user writes it. Unique within a table |
| `alias_dbname` | The table it is filed under: `gene_alias`, or `ensembl_gene` for an Ensembl gene that is already installed and only lacks the link |
| `target_dbname` | The table that holds the gene it names: `ncbi_geneid` or `ensembl_gene` |
| `target_display_id` | That gene's id there |
| `evidence` | Why this row exists; the values are below |

They name display ids and table names and never an xref `_id`: `dumpDatabase` mints
those at random on every build. The alias joins every transcript group of its target,
and `mates` are derived from the groups, so it reaches exactly what its target reaches.
`gene_alias` is deliberately not one of the mapper's `GENE_LEVEL_BRIDGE_DATABASES`, so
an alias is never walked through to anything else.

All aliases are gene level. A transcript id (`Zm00001d027230_T001`,
`Psat.cameor.v2.1g00050.1`) stays unmapped and no row may be added for one.

### `zma.tsv.gz`: maize B73 RefGen_v4 gene ids (`Zm00001d...`)

KEGG's maize genes and the installed Ensembl genebuild are both on v5 (`Zm00001eb...`).
27,798 rows, chosen in this order, so that where the sources disagree NCBI wins:

| `evidence` | Rows | Target | How |
|---|---:|---|---|
| `ncbi_locus_tag` | 27,180 | `ncbi_geneid` | NCBI's locus tag for the gene is `ZEAMMB73_<v4 id>` and KEGG has its GeneID |
| `maizegdb_1to1` | 618 | `ensembl_gene` | MaizeGDB pairs the v4 gene with one v5 gene and that v5 gene with it alone, and the v5 gene reaches KEGG |

They reach 27,753 of KEGG's 37,712 maize genes, 6,305 of its 9,704 pathway genes. 25
aliases reach more than one KEGG gene, because their v5 target does. MaizeGDB lists
about 44,100 v4 gene models. Of the 16,300 or so left out, about 9,800 have no v5 gene in
MaizeGDB's table, about 3,700 have a 1:1 v5 gene that reaches no KEGG gene, and about 2,800
were split or merged between versions.

### `psat.tsv.gz`: pea Cameor v2 gene ids (`Psat.cameor.v2...`)

KEGG's pea genes are NCBI GeneIDs from the RefSeq assembly of cultivar ZW6, and the
installed Ensembl genebuild is Cameor v1a. 22,200 rows of the 49,667 v2 genes:

| `evidence` | Rows | Target | How |
|---|---:|---|---|
| `published_table_rbh+coord+refseq_rbh` | 12,357 | `ensembl_gene` (v1a) | The published route below, confirmed by the sequence route naming a KEGG gene the v1a gene reaches |
| `published_table_rbh+coord` | 887 | `ensembl_gene` (v1a) | The published route only |
| `refseq_rbh_only` | 8,956 | `ncbi_geneid` | The sequence route only. **An inference, not a published correspondence** |

* **Published route.** In the authors' `v1_genes_to_v2.xlsx`, the v1 and v2 proteins are
  each other's best hit (sheets `v1_vs_v2`, `v2_vs_v1`), the two genes overlap on the v2
  genome by at least 50 % of the longer one (`intersection_v1_v2`), and the v1a gene
  reaches KEGG.
* **Sequence route.** Cameor v2 proteins against RefSeq ZW6 `GCF_024323335.1`, reduced to
  the longest protein per GeneID: DIAMOND reciprocal best hits at gene level, at least
  95 % identity and 80 % coverage both ways, GeneID in KEGG. This compares two cultivars,
  so a match says the sequence is the same and not that anyone has shown the gene is.

12 genes where the routes name different KEGG genes are dropped. The rows reach 22,272 of
KEGG's 51,484 pea genes, 5,235 of its 10,744 pathway genes. 96 aliases reach more than one
KEGG gene, because their v1a target does.

The owner may want the inferred rows gone later: they are the ones marked
`refseq_rbh_only`, and `tables --drop-rbh-only` writes the table without them.

### `sbi.tsv.gz`: sorghum Ensembl genes with no way to KEGG (`SORBI_3...`)

3,192 rows, all `ncbi_locus_tag`, all `ensembl_gene` to `ncbi_geneid`: Ensembl genes that
reach no KEGG gene through Ensembl's dumps, whose NCBI locus tag names a GeneID KEGG has.
Filed under `ensembl_gene`, where `insertXREF` finds the existing document, so no second
document appears. They take the pathway genes reachable from an Ensembl gene id from 5,074
to 5,462 of 5,741. The Phytozome spelling (`Sobic.001G262200`) reaches them through
`FeatureNamesToKeggIDsMapper.PHYTOZOME_LOCUS_TAGS`.

Unlike a `gene_alias`, such a row also changes what other names answer, because the second
hop walks through the gene. Measured by putting all 170,877 sorghum names through the mapper
with and without the table: 12,015 change, namely the 3,192 genes, 4,148 of their transcripts
and proteins, 4,674 UniProt accessions and 1 GeneID, and no Ensembl gene reaches more than
one KEGG gene. 2 of them already answered something: accessions A0A109NDM1 and A0A921Q8X9,
which Ensembl files under both SORBI_3010G273600 and SORBI_3K029900, now reach both genes'
KEGG ids. The two genes do not become mates. On `zma` and `psat` the same comparison changes
no existing name.

### What refuses a row

An id filed under a bridge table (`ensembl_gene`) is walked through by the mapper's second
hop, so writing it into the wrong group makes paralogues mates, the failure
`processEnsemblUniProtData`'s docstring warns about. On every row the build refuses, counts
and logs: an alias naming two targets; a name that already reaches a KEGG gene its target
does not. On bridge rows also: a target reaching more than one KEGG gene; two ids claiming
one target. None fires on the tables as shipped. A target the build lacks is counted and
skipped, and a table that cannot be read costs its aliases and nothing else.

## Sources

| Source | Used for | Licence and attribution |
|---|---|---|
| Kreplak J. et al., dataset for *A new genome assembly of the pea cultivar Cameor provides resources for functional genomics and genetics*, Recherche Data Gouv, [doi:10.57745/5MDE99](https://doi.org/10.57745/5MDE99), v1, published 2025-04-01: `v1_genes_to_v2.xlsx`, `CAMEOR_V2_ANNOTATION_1_prot.fasta`. Article: Kreplak J. et al., Sci Data 2026, [doi:10.1038/s41597-026-07347-4](https://doi.org/10.1038/s41597-026-07347-4) | pea, both routes | [Etalab Open Licence 2.0](https://spdx.org/licenses/etalab-2.0.html). **Attribution is required**: reuse must name the source and its date, as this row does. Rows were derived from the files, which are not redistributed here |
| NCBI Gene, `GENE_INFO/Plants/All_Plants.gene_info.gz`, taxa 4577 and 4558 | maize and sorghum locus tags | [NCBI policy](https://www.ncbi.nlm.nih.gov/home/about/policies/): NCBI places no restrictions on use or distribution of its molecular data; contributors may hold their own rights |
| NCBI RefSeq, `GCF_024323335.1` (CAAS_Psat_ZW6_1.0), proteins and feature table | pea, sequence route | As above |
| MaizeGDB, [`Pan-genes/B73_gene_xref`](https://download.maizegdb.org/Pan-genes/B73_gene_xref/): `B73v4_to_B73v5.tsv`, `B73v5_to_B73v4.tsv` | maize, the 618 `maizegdb_1to1` rows | The directory states no licence and MaizeGDB's terms page could not be read (it sits behind a browser check, 2026-11-06). Credited as MaizeGDB, RRID:SCR_006600. **Confirm the terms with MaizeGDB before relying on these rows being redistributable** |
| KEGG and Ensembl, as the species' installed `mapping/` files | which targets exist and reach KEGG | Used to choose rows and not redistributed: the tables hold GeneIDs, Ensembl ids and the aliases above, and no KEGG data |

## Regenerating

`build_identifier_aliases.py` downloads the sources and rewrites the tables; its docstring
has the commands. A species build never runs it, and only the pea `rbh` step needs DIAMOND.
It reads the species' `mapping/` files through the build's own processors, not the
installed database, because a database that already holds the aliases would answer "this
sorghum gene reaches KEGG" and the table would regenerate empty.

Run on 2026-11-06 against the deployed `mapping/` files, with every source downloaded
afresh and DIAMOND v2.2.8, it reproduced all three tables byte for byte:

| Table | Rows | Bytes | MD5 |
|---|---:|---:|---|
| `zma.tsv.gz` | 27,798 | 167,274 | `c5a3a02cd2d2ae26d6e93ea7ec17d75b` |
| `psat.tsv.gz` | 22,200 | 141,303 | `1bbcf94dcd5de933a2625f5145b6f17a` |
| `sbi.tsv.gz` | 3,192 | 20,854 | `902915412cb3bfc070c9c7f126aff035` |

Expect differences on a later date: NCBI rewrites gene_info daily, and KEGG and Ensembl
move with each release.

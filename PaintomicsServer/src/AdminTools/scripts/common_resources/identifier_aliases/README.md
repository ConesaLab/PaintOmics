# Identifier aliases

Gene ids that the installed tables of a species cannot reach, and what each of them
names there. `processIdentifierAliases()` in `common_build_database.py` applies the
table of a species while it is built, so every build of that species has them. A species
with no table here gets a no-op. To apply a table to an installed species, refresh it as
`deploy/README.md` describes: `download --specie=<code> --kegg=0 --mapping=1`, then
`install --specie=<code> --common=0`. A running server needs no restart: the mapper
resolves the rebuilt species' ids afresh on each mapping call. But `install` replaces the
species' identifier tables in place, so a job mapping that species while it runs can come
back short, and nothing says so.

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
a `gene_alias` row is never walked through to anything else. Sorghum's rows, filed under
`ensembl_gene`, are: see below.

All aliases are gene level. A transcript id of another genome version
(`Zm00001d027230_T001`, `Psat.cameor.v2.1g306350.1`) stays unmapped and no row may be
added for one.

### `zma.tsv.gz`: maize B73 RefGen_v4 gene ids (`Zm00001d...`)

KEGG's maize genes and the installed Ensembl genebuild are both on v5 (`Zm00001eb...`).
27,767 rows: NCBI's locus tag where its GeneID is in KEGG, otherwise MaizeGDB, where the
v5 gene's link to KEGG comes from Ensembl.

| `evidence` | Rows | Target | How |
|---|---:|---|---|
| `ncbi_locus_tag` | 27,163 | `ncbi_geneid` | NCBI's locus tag for the gene is `ZEAMMB73_<v4 id>`, KEGG has its GeneID, and the position check below passes |
| `maizegdb_1to1` | 604 | `ensembl_gene` | MaizeGDB pairs the v4 gene with one v5 gene and that v5 gene with it alone, and the position check below passes |

Each route has a position check, so that a source naming a gene elsewhere can cost the row
instead of giving a wrong answer. 31 of the 27,798 ids the routes give without them leave
the table.

* **A locus tag on another chromosome.** A row is skipped when the GeneID's chromosome in
  gene_info differs from the v4 id's in the v4 GFF, unless MaizeGDB pairs that v4 id with
  a v5 gene on the GeneID's chromosome, where the two assemblies placed the gene
  differently. 17 skipped. `Zm00001d033937` is on chromosome 1 and carries the locus tag of
  waxy 1 (GeneID 541854, chromosome 9), whose v5 gene MaizeGDB pairs with `Zm00001d045462`,
  which keeps it. An id skipped here loses its MaizeGDB row only when that pair's v5 gene
  reaches the gene its tag was skipped for: `Zm00001d017669`'s pair reaches 5 KEGG genes and
  `Zm00001d025539`'s reaches 2, that gene among them, so neither has a row, while
  `Zm00001d052718`'s reaches only 110485089, and that row stays. 228 `ncbi_locus_tag` rows
  have one side on a contig or nowhere, and are not checked.
* **A v5 gene linked elsewhere.** A MaizeGDB pair is skipped when no KEGG gene its v5 gene
  reaches overlaps that v5 gene, by the v5 GFF and the positions in KEGG's gene list. 15
  of the 618 rows this route gave are skipped. The link is Ensembl's, so the v5 id itself
  still gives that answer. `Zm00001d014241` goes because its only KEGG gene, 118475437, has
  no position.

The locus-tag check compares chromosome names only, so a far row on the same chromosome
stays. Measured against the GFF files, KEGG's positions and the proteins: `Zm00001d034536`
(103644213, 44 Mb from its v5 gene) and `Zm00001d041103` (103652087, 12 Mb from its v5 gene)
look wrong and stay; `Zm00001d044362` (103649614, 221 Mb from the v4 id) and `Zm00001d012944`
(103648360, 170 Mb) stay and look like moved genes, because their proteins match.

It goes by position only, so it also removes ids that may be the same gene. Of the 17 it
skips, 9 have the old target as their only best-scoring GeneID at 95 % identity or more
against RefSeq's maize proteins (`GCF_902167145.1`), and 5 of those are exact protein
matches, `Zm00001d033937` among them. These ids map nothing instead of risking a wrong gene.
Sequence does not prove the locus: a v4 copy missing from the v5 RefSeq set would look the
same.

The rows reach 27,740 of KEGG's 37,712 maize genes, 6,303 of its 9,704 pathway genes. 21
aliases reach more than one KEGG gene, because their v5 target does; for 18 of them one of
those genes overlaps the v5 gene and another does not. MaizeGDB lists
about 44,100 v4 gene models. Of the 16,400 or so left out, about 9,800 have no v5 gene in
MaizeGDB's table, about 3,700 have a 1:1 v5 gene that reaches no KEGG gene, about 1,600
point at several v5 genes, about 1,300 share one v5 gene with another v4 id and are left
out by the strict 1:1 rule (about 470 of those genes reach KEGG), and 31 failed a position
check.

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
  417 of the 8,956 `refseq_rbh_only` rows tie at the top score on one side or both, and
  the row names the hit DIAMOND lists first. In 210 of the 262 forward ties the candidates'
  proteins are identical, so no sequence method could choose. One tied row has an
  alternative on other pathways: `Psat.cameor.v2.6g110350` ties with 127095034, but covers
  20 % of that protein, under the 80 % threshold, and 127095034 has its own full-length
  partner, so the row stands. Of the other 261, 198 have no pathway on either side and 63
  have the same pathways on both.

12 genes where the routes name different KEGG genes are dropped. The rows reach 22,272 of
KEGG's 51,484 pea genes, 5,235 of its 10,744 pathway genes. 96 aliases reach more than one
KEGG gene, because their v1a target does.

The owner may want the inferred rows gone later: they are the ones marked
`refseq_rbh_only`, and `tables --drop-rbh-only` writes the table without them.

### `sbi.tsv.gz`: sorghum Ensembl genes with no way to KEGG (`SORBI_3...`)

3,192 of the 11,983 Ensembl genes that reach no KEGG gene through Ensembl's dumps, the ones
whose NCBI locus tag names a GeneID KEGG has. The other 8,791 have no NCBI locus tag.

| `evidence` | Rows | Target | How |
|---|---:|---|---|
| `ncbi_locus_tag` | 3,192 | `ncbi_geneid` | The Ensembl gene id is the gene's NCBI locus tag and KEGG has its GeneID |

Filed under `ensembl_gene`, where `insertXREF` finds the existing document, so no second
document appears. They take the pathway genes reachable from an Ensembl gene id from 5,074
to 5,462 of 5,741. The Phytozome spelling (`Sobic.001G262200`) reaches them through
`FeatureNamesToKeggIDsMapper.PHYTOZOME_LOCUS_TAGS`.

Unlike a `gene_alias`, such a row also changes what other names answer, because the second
hop walks through the gene. Measured by putting all 170,877 sorghum names through the mapper
with and without the table: 12,015 change, namely the 3,192 genes, 4,148 of their transcripts
and proteins, 4,674 UniProt accessions and 1 GeneID. None of the 3,192 genes reaches more
than one KEGG gene, and no gene newly does. Three accessions do: A0A109NDM1 and A0A921Q8X9,
which Ensembl files under both SORBI_3010G273600 and SORBI_3K029900, go from one KEGG gene
to both genes', and A0A1Z5R9A2 goes from 0 to 2 KEGG genes. The two genes do not become
mates. On `zma` and `psat` the same comparison changes no existing name.

### What refuses a row

An id filed under a bridge table (`ensembl_gene`) is walked through by the mapper's second
hop, so writing it into the wrong group makes paralogues mates, the failure
`processEnsemblUniProtData`'s docstring warns about. On every row the build refuses, counts
and logs: an alias naming two targets; a name that, in any table, already reaches a KEGG
gene its target does not. On bridge rows also: a target reaching more than one KEGG gene;
two ids claiming one target. None fires on the tables as shipped. A target the build lacks
is counted and skipped, and a table that cannot be read or holds no rows costs its aliases
and nothing else.

Nothing refuses several aliases reaching one KEGG gene: 63 maize and 61 pea KEGG genes are
reached by more than one alias, and those aliases merge there the way two Ensembl ids already
do (without the tables, 404 maize and 140 pea KEGG genes are reached by more than one Ensembl
gene).

## Sources

| Source | Used for | Licence and attribution |
|---|---|---|
| Kreplak, Jonathan. Dataset from "A new genome assembly of the pea cultivar Cameor provides resources for functional genomics and genetics". Recherche Data Gouv, V1, 2025-04-01. [doi:10.57745/5MDE99](https://doi.org/10.57745/5MDE99): `v1_genes_to_v2.xlsx`, `CAMEOR_V2_ANNOTATION_1_prot.fasta`. Article: Kreplak J. et al., Sci Data 2026, [doi:10.1038/s41597-026-07347-4](https://doi.org/10.1038/s41597-026-07347-4) | pea, both routes | [Etalab Open Licence 2.0](https://spdx.org/licenses/etalab-2.0.html). **Attribution is required**: reuse must name the source and its date, as this row does. Rows were derived from the files, which are not redistributed here |
| NCBI Gene, `GENE_INFO/Plants/All_Plants.gene_info.gz`, taxa 4577 and 4558 | maize and sorghum locus tags, and maize chromosomes for the position check | [NCBI policy](https://www.ncbi.nlm.nih.gov/home/about/policies/): NCBI places no restrictions on use or distribution of its molecular data; contributors may hold their own rights |
| NCBI RefSeq, `GCF_024323335.1` (CAAS_Psat_ZW6_1.0), proteins and feature table | pea, sequence route | As above |
| MaizeGDB, [`Pan-genes/B73_gene_xref`](https://download.maizegdb.org/Pan-genes/B73_gene_xref/): `B73v4_to_B73v5.tsv`, `B73v5_to_B73v4.tsv` | maize, the 604 `maizegdb_1to1` rows | The directory states no licence and MaizeGDB's terms page could not be read (it sits behind a browser check, 2026-11-06). Credited as MaizeGDB, RRID:SCR_006600. **Confirm the terms with MaizeGDB before relying on these rows being redistributable** |
| MaizeGDB, gene models: [`Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.gff3.gz`](https://download.maizegdb.org/Zm-B73-REFERENCE-GRAMENE-4.0/Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.gff3.gz), [`Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3.gz`](https://download.maizegdb.org/Zm-B73-REFERENCE-NAM-5.0/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3.gz) | maize, the position checks | As above. Used to choose rows and not redistributed |
| MaizeGDB, [`Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.full_gene_data.txt.gz`](https://download.maizegdb.org/Zm-B73-REFERENCE-GRAMENE-4.0/Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.full_gene_data.txt.gz) | the counts of v4 gene models and of those left out above, only: 44,146 models, where `B73v4_to_B73v5.tsv` lists 38,356. The script does not read it | As above. Nothing from it is in the tables |
| KEGG and Ensembl, as the species' installed `mapping/` files, among them `kegg2genesymbol.list`, which is KEGG's gene list (`rest.kegg.jp/list/<code>`) with its positions | which targets exist and reach KEGG, and maize positions for the check on `maizegdb_1to1` rows | Used to choose rows and not redistributed: the tables hold GeneIDs, Ensembl ids and the aliases above, and no KEGG data |

## Regenerating

`build_identifier_aliases.py` downloads the sources and rewrites the tables; its docstring
has the commands. A species build never runs it, and only the pea `rbh` step needs DIAMOND.
It reads the species' `mapping/` files through the build's own processors, not the
installed database, because a database that already holds the aliases would answer "this
sorghum gene reaches KEGG" and the table would regenerate empty. It stops without writing
that species' table when a source cannot be fetched or read, a source is skipped, a KEGG list
is absent, no rows come out, or a maize position source puts too few of its ids on a numbered
chromosome: each GFF under 95 % of the ids in MaizeGDB's table (measured 99.2 % and 100 %),
KEGG's list or gene_info under 85 % of KEGG's genes (94.4 % each). Without that, a file that
downloads and parses to nothing would turn a check off. A source of rows that parses short,
such as the locus-tag column or a MaizeGDB table missing lines, does not stop it: the run ends
well with a smaller table, and only the MD5 and row-count pins in `test_identifier_aliases`
catch that, once the table is committed.

Run against the deployed `mapping/` files, with DIAMOND v2.2.8 and the sources downloaded
afresh on 2026-11-06, the two GFF files on 2026-11-07, it gives these tables, byte for byte
from one run to the next:

| Table | Rows | Bytes | MD5 |
|---|---:|---:|---|
| `zma.tsv.gz` | 27,767 | 167,049 | `4b0e1f13db7c9c674b1294e8b50ee196` |
| `psat.tsv.gz` | 22,200 | 141,303 | `1bbcf94dcd5de933a2625f5145b6f17a` |
| `sbi.tsv.gz` | 3,192 | 20,854 | `902915412cb3bfc070c9c7f126aff035` |

Expect differences on a later date: NCBI rewrites gene_info daily, and KEGG and Ensembl
move with each release.

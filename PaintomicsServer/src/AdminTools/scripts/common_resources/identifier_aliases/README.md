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
27,769 rows: NCBI's locus tag where its GeneID is in KEGG, otherwise MaizeGDB, where the
v5 gene's link to KEGG comes from Ensembl. Each route has a position check, and 6 ids a
check skipped come back on the one KEGG gene with the same protein where the v5 genome
has the id.

| `evidence` | Rows | Target | How |
|---|---:|---|---|
| `ncbi_locus_tag` | 27,159 | `ncbi_geneid` | NCBI's locus tag for the gene is `ZEAMMB73_<v4 id>`, KEGG has its GeneID, and the position check below passes |
| `maizegdb_1to1` | 604 | `ensembl_gene` | MaizeGDB pairs the v4 gene with one v5 gene and that v5 gene with it alone, and the position check below passes |
| `protein+maizegdb_v5` | 5 | `ncbi_geneid` | A position check skipped the id, and one KEGG gene has a protein the same as the id's and overlaps the v5 gene MaizeGDB pairs the id with |
| `protein+neighbour` | 1 | `ncbi_geneid` | The same, for an id MaizeGDB pairs with no v5 gene: the trusted pair nearest the id on v4 puts that gene where it is |

The position checks are there so that a source naming a gene elsewhere costs the row
instead of giving a wrong answer. They skip 36 ids; one of those keeps a row from the other
route and 6 come back on their protein (below), so 29 of the 27,798 ids the routes give
leave the table.

* **A locus tag naming a GeneID away from the gene.** The v5 genome places a v4 id two
  ways: the v5 genes MaizeGDB pairs it with, and where MaizeGDB's 1:1 pairs around it on v4
  sit on v5 (the collinearity test under "Where the gene is", below). A row is skipped
  unless KEGG's place for the GeneID is within 1 Mb of such a v5 gene or passes that test.
  21 skipped:
  * 17 with the GeneID on another chromosome, where the v4 GFF, the id's neighbours and any
    v5 gene MaizeGDB pairs it with all put it on its own. `Zm00001d033937` is on chromosome 1
    and carries the locus tag of waxy 1 (GeneID 541854, chromosome 9), whose v5 gene
    MaizeGDB pairs with `Zm00001d045462`, which keeps it.
  * 4 on the id's own chromosome, far from where v5 has it. `Zm00001d034536` (103644213) is
    44 Mb from its MaizeGDB v5 gene. Of the 4 trusted pairs nearest it on v4, 3 (itself
    among them) put it beside that v5 gene, and one, 11.6 kb along v4, has its v5 gene 4.6 kb
    from the GeneID: 1 of 4 agrees with the GeneID, under half. `Zm00001d041103` (103652087)
    is 12 Mb from its MaizeGDB v5 gene and from all 4 pairs' places. `Zm00001d044362` (103649614) has no MaizeGDB v5 gene, and all 4 pairs put it 223
    Mb from the GeneID. `Zm00001d012944` (103648360) has none either, and the 3 pairs after
    the nearest put it 170 Mb from the GeneID. Before this check went by distance, only a
    different chromosome counted, and these four had rows.

  Where KEGG gives the GeneID no place (12 rows), gene_info's chromosome is compared with the
  v4 GFF's, and a MaizeGDB v5 gene on it explains a difference. 228 rows have the v4 id on a
  contig or nowhere, or a GeneID on no numbered chromosome, and are not checked. An id
  skipped here loses its MaizeGDB row only when that pair's v5 gene reaches the gene its tag
  was skipped for: `Zm00001d017669`'s pair reaches 5 KEGG genes and `Zm00001d025539`'s
  reaches 2, that gene among them, so neither has a MaizeGDB row, while `Zm00001d052718`'s
  reaches only 110485089, and that row stays.
* **A v5 gene linked elsewhere.** A MaizeGDB pair is skipped when no KEGG gene its v5 gene
  reaches overlaps that v5 gene, by the v5 GFF and the positions in KEGG's gene list. 15
  of the 619 rows this route gave are skipped. The link is Ensembl's, so the v5 id itself
  still gives that answer. `Zm00001d014241` goes because its only KEGG gene, 118475437, has
  no position.

**Why both places.** 25,797 locus tags name a GeneID on the chromosome of a v5 gene
MaizeGDB pairs their id with. 25,786 of those GeneIDs lie within 0.3 Mb of that v5 gene,
none between 0.3 and 10.9 Mb, and 11 further. 9 of the 11 sit where the id's neighbours put
it, `Zm00001d010350` among them (MaizeGDB's v5 gene 89 Mb away), so MaizeGDB's pair is the
odd one there and the row stays. The other 2 are `Zm00001d034536` and `Zm00001d041103`. 28
more rows stay the same way with MaizeGDB's v5 gene on another chromosome than the GeneID,
which sits on the id's own v4 chromosome where its neighbours put it (`Zm00001d048174`,
541989, among them). So 37 rows stand on the neighbours against MaizeGDB's pair.

**Which skipped ids the same protein keeps.** Two proteins count as the same when one
sequence holds the other whole and the shorter is at least 95 % of the longer, compared
between MaizeGDB's v4 proteins and RefSeq's (`GCF_902167145.1`, the v5 assembly KEGG's
GeneIDs are on). A skipped id with no row comes back when exactly one KEGG gene with its
protein is where the v5 genome has the id: overlapping the v5 gene MaizeGDB pairs the id
with, or, where MaizeGDB pairs it with none, where the trusted pair nearest the id on v4
puts it. Two such genes are a family and give nothing. This is a rule to keep an id, not to
drop one: 76.6 % of the 25,842 locus tags whose GeneID overlaps their MaizeGDB v5 gene (the
same gene by both sources) have the same protein by it, so a protein that differs says
nothing against a row. The nearest pair alone passes 99.1 % of the 27,149 trusted pairs
with themselves left out, and 1.3 % given a random v5 gene on the right chromosome, which
is why the protein has to be the same as well.

Kept, 6:

* `Zm00001d012944` -> 103648360. The nearest trusted pair, `Zm00001d012943`, is 1.4 kb from
  it on v4, and its v5 gene is 1.7 kb from the GeneID; the 3 pairs after it put the id 170 Mb
  away. A stretch of two genes that moved together, which the majority of 4 cannot see. The
  v4 protein is held whole in RefSeq's, and is 97 % of its length.
* `Zm00001d004346` -> 103646782 and `Zm00001d005043` -> 100136884: MaizeGDB pairs, whose v5
  gene Ensembl links to a copy with the same protein 48 and 68 kb away; the gene kept
  overlaps the v5 gene. `Zm00001d005044`'s locus tag also names 100136884, 67.6 kb from its
  own MaizeGDB v5 gene, so both ids now reach that gene.
* `Zm00001d041568` -> 110485087: a MaizeGDB pair whose v5 gene Ensembl links to a gene on
  chromosome 1 with the same protein (100216796); 110485087 overlaps the v5 gene and is on a
  pathway.
* `Zm00001d025539` -> 103641698 and `Zm00001d035986` -> 109939246: locus tags naming a GeneID
  on another chromosome (103652785 on 4, 103629404 on 3); the gene kept overlaps the v5 gene
  MaizeGDB pairs the id with. Ensembl links `Zm00001d035986`'s v5 gene to no KEGG gene, so it
  had no MaizeGDB row either.

Left out, 29:

* 21 with no KEGG gene of the same protein, among them the other 3 on their own
  chromosome: `Zm00001d034536` is 92 % identical over 57 % of RefSeq's protein,
  `Zm00001d041103`'s best match is another GeneID (103642691), and `Zm00001d044362` matches
  103649614 at 100 % identity over 94 % of RefSeq's protein, under 95 %, where its place
  disagrees too. `Zm00001d018010` is 95.2 % identical.
* 8 with the same protein and no place that agrees. `Zm00001d033937` (waxy 1's protein),
  `Zm00001d006805`, `Zm00001d009372` and `Zm00001d018011` each have one such gene, on another
  chromosome than every trusted pair near the id and than its MaizeGDB v5 gene where it has
  one. RefSeq has no second waxy 1, so `Zm00001d033937` may be a v4 copy that v5 dropped, but
  nothing shows it.
  `Zm00001d018011` sits 732 bp from `Zm00001d018010`, and their tags name two GeneIDs 734 bp
  apart on chromosome 2: a pair that may have moved together, but MaizeGDB has no v5 gene for
  either and the only place evidence is each other's tag, which is what is in doubt.
  `Zm00001d003100` has two such genes, both elsewhere. `Zm00001d003725`, `Zm00001d020584`
  and `Zm00001d025598` share their protein with 7, 12 and 2 KEGG genes, none overlapping the
  v5 gene MaizeGDB pairs the id with: the nearest are 138 kb, 29 Mb, and 93 and 241 kb away.
  Copies beside the v5 gene are a family, and which of them is the v4 gene is not shown.

These ids map nothing instead of risking a wrong gene.

**Wrong rows these checks cannot see.** A locus tag can name the gene next door. The 1 Mb
allowance passes it when the protein belongs to another GeneID that overlaps MaizeGDB's v5
gene. An independent review found 9 such rows. Three of them, checked again here:
`Zm00001d010722` -> 100194195, `Zm00001d027636` -> 103644420 and `Zm00001d038530` ->
100282945, whose GeneIDs' proteins are not the id's, while those of 111674462, 118471899 and
110354864 are the same and those genes overlap the v5 gene. They were in the
table before this check and are left as they are: no rule here compares a locus tag's
protein.

The rows reach 27,741 of KEGG's 37,712 maize genes, 6,303 of its 9,704 pathway genes. 21
aliases reach more than one KEGG gene, because their v5 target does; for 18 of them one of
those genes overlaps the v5 gene and another does not. MaizeGDB lists
about 44,100 v4 gene models. Of the 16,400 or so left out, about 9,800 have no v5 gene in
MaizeGDB's table, about 3,700 have a 1:1 v5 gene that reaches no KEGG gene, about 1,600
point at several v5 genes, about 1,300 share one v5 gene with another v4 id and are left
out by the strict 1:1 rule (about 470 of those genes reach KEGG), and 29 failed a position
check and have no gene of the same protein where the v5 genome has them.

### `psat.tsv.gz`: pea Cameor v2 gene ids (`Psat.cameor.v2...`)

KEGG's pea genes are NCBI GeneIDs from the RefSeq assembly of cultivar ZW6, and the
installed Ensembl genebuild is Cameor v1a. 21,979 rows of the 49,667 v2 genes:

| `evidence` | Rows | Target | How |
|---|---:|---|---|
| `published_table_rbh+coord+refseq_rbh` | 12,357 | `ensembl_gene` (v1a) | The published route below, confirmed by the sequence route naming a KEGG gene the v1a gene reaches |
| `published_table_rbh+coord` | 887 | `ensembl_gene` (v1a) | The published route only |
| `refseq_rbh+collinear` | 8,735 | `ncbi_geneid` | The sequence route, and the genes of the first line around it put the GeneID where it is. **An inference, not a published correspondence** |

* **Published route.** In the authors' `v1_genes_to_v2.xlsx`, the v1 and v2 proteins are
  each other's best hit (sheets `v1_vs_v2`, `v2_vs_v1`), the two genes overlap on the v2
  genome by at least 50 % of the longer one (`intersection_v1_v2`), and the v1a gene
  reaches KEGG.
* **Sequence route.** Cameor v2 proteins against RefSeq ZW6 `GCF_024323335.1`, reduced to
  the longest protein per GeneID: DIAMOND reciprocal best hits at gene level, at least
  95 % identity and 80 % coverage both ways, GeneID in KEGG. This compares two cultivars,
  so a match says the sequence is the same and not that anyone has shown the gene is.
* **Collinearity, for a match with no published pair.** The 12,357 genes both routes agree
  on are the anchors: each has a place on the Cameor v2 genome (the dataset's
  `CAMEOR_V2_ANNOTATION_1.gff3`) and its GeneID one on ZW6's (RefSeq's `feature_table`). A
  match stays only if the anchors around the v2 gene put its GeneID where it is, by the test
  under "Where the gene is". This is evidence the sequence route did not use: the protein
  says which gene, the neighbours say where.

8,735 of the 8,956 matches pass and 221 are dropped:

| Why dropped | Matches |
|---|---:|
| None of the 4 nearest anchors agrees | 162 |
| One of the 4 nearest agrees | 21 |
| No anchor on the gene's v2 sequence (a scaffold) | 29 |
| None of the fewer than 4 anchors on its scaffold agrees | 9 |

94 of the 221 (43 %) tie at the top score on one side or both, against 323 of the 8,735 kept
(4 %): ties are more than ten times as common among the dropped rows, but most dropped rows are not
ties. 33 of the 162 name a GeneID on a ZW6 scaffold no chromosome holds. Examples dropped:
`Psat.cameor.v2.4g224300` (127103701, on ZW6 chromosome 7 at 337 Mb, while its 4 nearest
anchors put it on chromosome 4 at about 137 Mb), `Psat.cameor.v2.3g68100` (127129346, on the
right chromosome, 14 to 16 Mb from where each of its 4 nearest anchors puts it), and
`Psat.cameor.v2.3g307300` (127112547, on an unplaced ZW6 scaffold, tied with 11 other
proteins). Examples kept: `Psat.cameor.v2.3g49750` (127128146, 4 of 4 agree), and
`Psat.cameor.v2.0s1040g10400` (127107715), on a scaffold with 2 anchors, the nearer of which,
`Psat.cameor.v2.0s1040g10450`, names 127107716, the next gene along ZW6.

What the test loses: a stretch of genes that moved, between the cultivars or in one assembly,
fails the same way as a wrong match, and the test cannot tell them apart. An independent
review of the 221 counted 38 in runs of 2 to 11 neighbouring v2 genes whose GeneIDs are
neighbours on ZW6 too (24 in runs of 3 or more). The longest is 11 genes,
`Psat.cameor.v2.4g104300` to `Psat.cameor.v2.4g105600`, at v2 chromosome 4 130.5 to 131.7 Mb:
each is 100 % identical to its GeneID with no tie either way, the GeneIDs (127137472 to
127137531) lie at ZW6 chromosome 4 369.3 to 371.5 Mb, and the anchors around them put them
at 371.1 and 374.9 to 375.2 Mb, so at most 1 of the 4 nearest agrees for each. 73 of the 221 are at least 99 % identical
over at least 95 % of both proteins with no tie either way, and 52 name a gene on a KEGG
pathway. Some of these runs may be tandem families, so not every one of them is right, but
some correct rows are among the 221. They are dropped because the rule asks for neighbours
that agree, and a run that agrees only with itself is not that.

The row names the hit DIAMOND lists first where several tie. In 159 of the 193 forward
ties among the kept rows the candidates' proteins are identical, so no sequence method could
choose. One tied row has an alternative on other pathways: `Psat.cameor.v2.6g110350` ties
with 127095034, but covers 20 % of that protein, under the 80 % threshold, and 127095034 has
its own full-length partner, so the row stands. Of the other 192, 146 have no pathway on
either side and 46 have the same pathways on both.

12 genes where the routes name different KEGG genes are dropped. The rows reach 22,051 of
KEGG's 51,484 pea genes, 5,183 of its 10,744 pathway genes. 96 aliases reach more than one
KEGG gene, because their v1a target does.

The owner may want the inferred rows gone later: they are the ones marked
`refseq_rbh+collinear`, and `tables --drop-inferred` writes the table without them.

### Where the gene is

Both species use one test (`Collinearity` in the script), from pairs of genes already
trusted, called anchors: maize's are MaizeGDB's 1:1 pairs, pea's the genes both routes
agree on. A row pairs a gene on one genome with a gene on the other. The test takes the 4
anchors nearest the gene on its own genome, and an anchor agrees when it sits on the
partner's sequence and no further from the partner than it sits from the gene, plus 1 Mb.
At least half of the anchors asked have to agree, and at least one. It compares distances,
not order or strand, so a stretch the two assemblies orient differently still agrees. The
two choices, 4 anchors and 1 Mb, come from these measurements:

| | Maize | Pea |
|---|---:|---:|
| Anchors that pass with themselves left out | 99.53 % of 26,951 | 99.61 % of 12,357 |
| The same, given a random gene of the other genome as partner | 0.21 % | 0.14 % |
| The same, given a random gene on the true partner's sequence | 1.27 % | 0.80 % |
| Rows the test removes | 4, one of which its protein brings back | 221 |
| With 0.5 Mb instead of 1 | the same | 30 more go |
| With 2 Mb or 5 Mb | the same | 6 or 28 come back |
| With 3 or 6 anchors asked | the same | 9 more go, or 9 go and 4 come back |

Asking 10 anchors passes and fails random partners at the same rates, but drops genes beside
a gap between the assemblies: `Psat.cameor.v2.7g72100`'s two nearest anchors put its GeneID
within 0.1 Mb of where it is, and 7 of the next 8 sit beyond a stretch of about 4 Mb that
ZW6 has and Cameor v2 does not.

For maize, a row also passes when its GeneID is within 1 Mb of a v5 gene MaizeGDB pairs the
v4 id with, which is how a maize row on another chromosome than its id can pass, and only
rows with both places on numbered chromosomes are tested: the test only vetoes NCBI's
published locus tag. For pea it is the only evidence beyond the sequence, so a match it
cannot test is dropped.

The maize protein rule ("Which skipped ids the same protein keeps", above) asks the same
question of the nearest anchor alone, with the same 1 Mb: of all 27,149 maize anchors with a
place on both genomes, 99.1 % pass with themselves left out, 0.16 % given a random v5 gene
and 1.3 % given a random v5 gene on the right chromosome, which is why it also needs the same
protein. The majority of 4 cannot see a stretch of one or two genes that moved together, and
the nearest anchor can.

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

Nothing refuses several aliases reaching one KEGG gene: 64 maize and 61 pea KEGG genes are
reached by more than one alias, and those aliases merge there the way two Ensembl ids already
do (without the tables, 404 maize and 140 pea KEGG genes are reached by more than one Ensembl
gene).

## Sources

| Source | Used for | Licence and attribution |
|---|---|---|
| Kreplak, Jonathan. Dataset from "A new genome assembly of the pea cultivar Cameor provides resources for functional genomics and genetics". Recherche Data Gouv, V1, 2025-04-01. [doi:10.57745/5MDE99](https://doi.org/10.57745/5MDE99): `v1_genes_to_v2.xlsx`, `CAMEOR_V2_ANNOTATION_1_prot.fasta`, `CAMEOR_V2_ANNOTATION_1.gff3`. Article: Kreplak J. et al., Sci Data 2026, [doi:10.1038/s41597-026-07347-4](https://doi.org/10.1038/s41597-026-07347-4) | pea, both routes and the collinearity test | [Etalab Open Licence 2.0](https://spdx.org/licenses/etalab-2.0.html). **Attribution is required**: reuse must name the source and its date, as this row does. Rows were derived from the files, which are not redistributed here |
| NCBI Gene, `GENE_INFO/Plants/All_Plants.gene_info.gz`, taxa 4577 and 4558 | maize and sorghum locus tags, and maize chromosomes for the position check where KEGG gives a GeneID no place | [NCBI policy](https://www.ncbi.nlm.nih.gov/home/about/policies/): NCBI places no restrictions on use or distribution of its molecular data; contributors may hold their own rights |
| NCBI RefSeq, `GCF_024323335.1` (CAAS_Psat_ZW6_1.0), proteins and `feature_table` | pea, the sequence route and the collinearity test | As above |
| MaizeGDB, [`Pan-genes/B73_gene_xref`](https://download.maizegdb.org/Pan-genes/B73_gene_xref/): `B73v4_to_B73v5.tsv`, `B73v5_to_B73v4.tsv` | maize, the 604 `maizegdb_1to1` rows, the anchors of the maize position checks, and the v5 gene the protein rule asks for | **Reuse allowed with credit; the sources disagree on the licence, so the rows follow the strictest, CC BY 4.0.** MaizeGDB is a USDA Agricultural Research Service project. USDA's catalogue record for it gives "U.S. Public Domain" ([Ag Data Commons, MaizeGDB](https://agdatacommons.nal.usda.gov/articles/dataset/MaizeGDB/24660768), read 2026-11-07); the data.gov copy of that record, as AmeriGEOSS shows it, gives CC0 ([data.amerigeoss.org/dataset/maizegdb](https://data.amerigeoss.org/dataset/maizegdb)); and microPublication's data-sharing policy says "all data curated by MaizeGDB are used with permission of the generating researchers and licensed under the Creative Commons Attribution 4.0 International license (CC-BY [...]), unless otherwise specified" ([micropublication.org/data-sharing-policy](https://www.micropublication.org/data-sharing-policy)). MaizeGDB's own 2006 status report says "Data housed at MaizeGDB are in the public domain, hence they are freely available for use without a license" ([documents.maizegdb.org](https://documents.maizegdb.org/working_group/12ReportAugust2006.pdf), page 16). www.maizegdb.org answers with a Cloudflare browser check (HTTP 403 to curl and to headless Chromium on 2026-11-07), and the directory states no licence; the Internet Archive's copies of January 2026 can be read: [its page for contributors](https://web.archive.org/web/20260107183036/https://www.maizegdb.org/contribute_data) takes only data "licensed as public data", and [its citation page](https://web.archive.org/web/20260109182555/https://www.maizegdb.org/cite) asks for the 2021 paper below. Every one of these allows reuse; CC BY asks for credit and a link to the licence, given here: MaizeGDB, RRID:SCR_006600, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); Woodhouse M. R. et al., BMC Plant Biol 2021, [doi:10.1186/s12870-021-03173-5](https://doi.org/10.1186/s12870-021-03173-5), the citation MaizeGDB asks for; and Andorf C. M. et al., Nucleic Acids Res 2016, [doi:10.1093/nar/gkv1007](https://doi.org/10.1093/nar/gkv1007), the one USDA's record asks for |
| MaizeGDB, gene models: [`Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.gff3.gz`](https://download.maizegdb.org/Zm-B73-REFERENCE-GRAMENE-4.0/Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.gff3.gz), [`Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3.gz`](https://download.maizegdb.org/Zm-B73-REFERENCE-NAM-5.0/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.gff3.gz) | maize, the position checks | As above. Used to choose rows and not redistributed |
| MaizeGDB, v4 proteins: [`Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.protein.fa.gz`](https://download.maizegdb.org/Zm-B73-REFERENCE-GRAMENE-4.0/Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.protein.fa.gz) | maize, the protein rule | As above. Used to choose rows and not redistributed |
| NCBI RefSeq, `GCF_902167145.1` (Zm-B73-REFERENCE-NAM-5.0), proteins and `feature_table` | maize, the protein rule: the proteins of KEGG's GeneIDs | As NCBI Gene above. Used to choose rows and not redistributed |
| MaizeGDB, [`Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.full_gene_data.txt.gz`](https://download.maizegdb.org/Zm-B73-REFERENCE-GRAMENE-4.0/Zm-B73-REFERENCE-GRAMENE-4.0_Zm00001d.2.full_gene_data.txt.gz) | the counts of v4 gene models and of those left out above, only: 44,146 models, where `B73v4_to_B73v5.tsv` lists 38,356. The script does not read it | As above. Nothing from it is in the tables |
| KEGG and Ensembl, as the species' installed `mapping/` files, among them `kegg2genesymbol.list`, which is KEGG's gene list (`rest.kegg.jp/list/<code>`) with its positions | which targets exist and reach KEGG, and the maize GeneIDs' places for both position checks | Used to choose rows and not redistributed: the tables hold GeneIDs, Ensembl ids and the aliases above, and no KEGG data |

## Regenerating

`build_identifier_aliases.py` downloads the sources and rewrites the tables; its docstring
has the commands. A species build never runs it, and only the pea `rbh` step needs DIAMOND:
the maize protein rule compares sequences as text.
It reads the species' `mapping/` files through the build's own processors, not the
installed database, because a database that already holds the aliases would answer "this
sorghum gene reaches KEGG" and the table would regenerate empty. It stops without writing
that species' table when a source cannot be fetched or read, a source is skipped, a KEGG list
is absent, no rows come out, or a position source places too few of its ids. For maize,
on a numbered chromosome: each GFF under 95 % of the ids in MaizeGDB's table (measured
99.2 % and 100 %), KEGG's list or gene_info under 85 % of KEGG's genes (94.4 % each). For
pea, anywhere: the Cameor v2 GFF under 95 % of the v2 genes either route names, ZW6's
feature table under 95 % of the GeneIDs the sequence route names (measured 100 % each).
Without that, a file that downloads and parses to nothing would turn a check off, or for pea
drop every sequence match. A source of rows that parses short,
such as the locus-tag column, a MaizeGDB table missing lines or a maize protein file, does not
stop it: the run ends well with a smaller table, and only the MD5 and row-count pins in
`test_identifier_aliases` catch that, once the table is committed.

Run against the deployed `mapping/` files, with DIAMOND v2.2.8 and the sources downloaded
afresh on 2026-11-06, the three GFF files and the three maize protein files on 2026-11-07, it
gives these tables, byte for byte from one run to the next:

| Table | Rows | Bytes | MD5 |
|---|---:|---:|---|
| `zma.tsv.gz` | 27,769 | 167,226 | `d61bffc4e46eaf07a8cede9bc78c709b` |
| `psat.tsv.gz` | 21,979 | 140,768 | `bcd3ed7272608db40d537125618dda87` |
| `sbi.tsv.gz` | 3,192 | 20,854 | `902915412cb3bfc070c9c7f126aff035` |

Expect differences on a later date: NCBI rewrites gene_info daily, and KEGG and Ensembl
move with each release.

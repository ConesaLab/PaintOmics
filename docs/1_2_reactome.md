# The Reactome pathways database

Reactome is a manually curated, peer-reviewed database of biological reactions.
In PaintOmics it is an optional second source of pathways: it has its own
explorer tab in Step 3, its own classification, its own pathway network, and one
extra column in the enrichment table that no other database produces.

The application describes it on the Step 2 mapping screen as *an open-source,
open access, manually curated and peer-reviewed pathway database, containing
information of around 20 organisms* — see
[reactome.org](http://www.reactome.org/). Reactome curates human and infers the
other species from it by orthology, so a non-human Reactome pathway is an
inference, not an independent curation.

## Which organisms have it

Reactome is offered only where the server has installed Reactome pathways
*and* the organism has Reactome identifier mappings. In the shipped
configuration that is fifteen organisms:

| | |
|---|---|
| Mammals | human (`hsa`), mouse (`mmu`), rat (`rno`), cow (`bta`), pig (`ssc`), dog (`cfa`) |
| Other vertebrates | chicken (`gga`), *Xenopus tropicalis* (`xtr`), zebrafish (`dre`) |
| Invertebrates | *Drosophila melanogaster* (`dme`), *Caenorhabditis elegans* (`cel`) |
| Fungi and protists | *Saccharomyces cerevisiae* (`sce`), *Schizosaccharomyces pombe* (`spo`), *Dictyostelium discoideum* (`ddi`), *Plasmodium falciparum* (`pfa`) |

Whether *your* server has installed it is a separate question, and the Step 1
checkbox is the answer: **Reactome** is tickable when this deployment holds
Reactome data for the organism you chose, and disabled and labelled **not
installed** when it does not.

On your own server, Reactome must be asked for at download time
(`--reactome=1`) and costs about 856 MB of shared data on top of the KEGG
download. Reactome does not cover every KEGG organism; a species it does not
cover is detected during the download, which logs a warning naming the species
and carries on with KEGG data only, so the organism installs without Reactome.
See [Installing PaintOmics](0_install.md).

## What a Reactome pathway contains

Reactome describes reactions between proteins, small molecules and complexes.
PaintOmics decomposes the complexes: a node that has components is walked down
to its leaf members, so the proteins inside it can each be matched and painted
rather than the whole complex being skipped.

Small molecules are resolved through ChEBI to KEGG compound ids, so a
metabolomics omic maps onto Reactome pathways as well as KEGG ones. Where no
ChEBI-to-KEGG mapping exists, the compound is kept under its Reactome display
name.

Pathway identifiers are Reactome stable ids — `R-HSA-` for human, `R-MMU-` for
mouse, and so on.

## Reactome pathways are painted, like KEGG's

PaintOmics downloads each Reactome pathway's diagram as a PNG, together with a
thumbnail, and paints your feature boxes over that image using the node
coordinates from Reactome's own layout data. A Reactome pathway therefore opens
in exactly the same [pathway view](5_1_browsing_pathways.md) as a KEGG map, with
the same boxes, the same per-condition cells and the same colour scale. The
diagram is presentation only: a pathway whose image fails to download does not
fail the species install.

## Identifiers

Reactome pathways are keyed on Reactome gene identifiers (`reactome_gene_id`),
and PaintOmics translates whatever you uploaded into that space. You do not have
to supply Reactome ids — [identifier translation](1_4_id.md) is what the mapping
step in Step 2 does — but how much of your data reaches Reactome depends on
which identifier tables your organism was built with, which is why the Step 2
matrix reports the KEGG and Reactome counts side by side.

## Classification

A Reactome pathway's classification comes from Reactome's own event hierarchy,
in two levels: the top-level ancestor of the pathway and the level below it —
for example *Signal Transduction* then *Signalling by Receptor Tyrosine
Kinases*. Step 3's category pie and **Filter by category** tree are built from
those two levels, exactly as they are for KEGG. A pathway for which the
downloaded hierarchy holds no entry is filed as its own top level.

## The Reactome class p-value column

When a job includes Reactome, the [enrichment table](4_1_pathway_enrichment.md)
gains one extra column, **Reactome Class pValue**.

PaintOmics groups every Reactome pathway by its *top-level* class, pools the
genes of all the pathways in that class into a single feature set, and runs the
same enrichment test on the pooled set that it runs on an individual pathway.
The column shows that class's combined p-value, computed with whichever
combining method the **Show combined p-values** control has selected, and the
value is repeated on every pathway row belonging to that class. It answers a
different question from the pathway's own p-value: not *has this pathway
moved*, but *has this whole branch of Reactome moved*.

Three things to know before you rely on it:

* It is a class value, not a pathway value. Every row in the same class carries
  the same number, so sorting the table by this column groups pathways rather
  than ranking them.
* Pooling makes large classes large. A broad class such as *Metabolism* pools
  thousands of genes, and a class-level test on that set is a much blunter
  instrument than the test on any one pathway inside it.
* The column appears whenever Reactome is one of the job's databases, so KEGG
  and OmniPath rows in the same table show a dash. That is not a missing value;
  those pathways have no Reactome class.

The class values are computed during Step 2 and stored with the job, so they are
still there when you reopen the job from its URL later.

## Step 3 gives Reactome its own tab

With more than one database in the job, Step 3 draws a tab per database. The
Reactome tab has its own classification chart, its own pathway-to-pathway
[network](4_3_pathways_network.md) and its own filters; the enrichment table
below lists every database at once and can be filtered by database from its
**Databases to view** tick-boxes. The summary band above the tabs reports how
many pathways each database contributed and how many of them were significant —
in the STATegra mouse example, 523 Reactome pathways of which 33 were
significant, beside 363 KEGG pathways of which 71 were.

For how the four databases differ, see the comparison table on
[the KEGG page](1_1_kegg.md).

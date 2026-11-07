# The MapMan pathways database

MapMan is a plant-oriented functional annotation. Instead of curated reaction
networks it offers a numbered ontology of *bins* — functional categories — and a
set of hand-drawn diagrams that place those bins on a picture of plant
metabolism. In PaintOmics it is one of the optional pathway sources, offered for
a small number of plant organisms.

The application describes it on the Step 2 mapping screen as *oriented towards
plant species, in combination with [GoMapMan](http://www.gomapman.org/), it
provides additional pathways as well as an improved and more consolidated
annotation for the model species Arabidopsis, and several crop species (potato,
tomato, rice)*.

## Which organisms actually have it

Four, in the shipped configuration:

| Organism | Code |
|---|---|
| *Arabidopsis thaliana* | `ath` |
| Tomato (*Solanum lycopersicum*) | `sly` |
| Potato (*Solanum tuberosum*) | `sot` |
| Rice (*Oryza sativa*) | `osa` |

Two warnings about that list.

Rice has two KEGG codes and only one of them carries MapMan. `osa` has it;
`dosa`, the RAP-DB-keyed rice entry, does not, because KEGG publishes no
NCBI-gene-id conversion for `dosa` and the gene-to-bin cross-link has nothing to
join through. If you work on rice and want MapMan diagrams, choose the organism
whose code is `osa`.

Sugar beet is not on the list. GoMapMan publishes a sugar beet export under the code
`bvu`, but in KEGG `bvu` is the gut bacterium *Phocaeicola vulgatus*, and the
build that installed the export under that code was removed in September 2026
for putting plant pathways into a bacterium. `bvu` is now KEGG only, like any
other bacterium. KEGG's own sugar beet code is `bvg`, and GoMapMan's sugar beet
gene ids have no link to its genes.

As with the other optional databases, whether *your* server has MapMan installed
for the organism you picked is what the Step 1 checkbox says: tickable when it
is there, disabled and labelled **not installed** when it is not.

## Bins, and what a MapMan box actually holds

A MapMan bin is a dotted number: `1` is a top-level function, `1.3` a
subcategory of it, `1.3.4` a subcategory of that. A gene-to-bin file assigns
each gene one or more bins, and PaintOmics stores those gene names as their own
identifier type, `mapman_gene_id`.

A diagram does not place genes. It places *bins*: each area of the picture names
one bin, and PaintOmics expands that bin to every gene mapped to it or to any
bin nested underneath it — an area labelled `18.4` collects the genes of `18.4`,
`18.4.1`, `18.4.1.2` and so on.

The consequence is worth understanding before you read a painted MapMan diagram:
**one box on a MapMan diagram is a bin, not a gene.** Every matched gene in that
bin is painted into the same box, so a box can stand for hundreds of genes where
a KEGG box stands for the handful of gene products drawn at that spot. Click it
and the [feature detail panel](5_2_detailed_views.md) lists what is in it.

## The diagrams

MapMan pathways in PaintOmics are its diagrams, and they are named rather than
numbered — *Glycolysis-TCA*, *Core metabolism overview*, *Metabolites*. There
are about seventy of them, assembled from two sources:

* GoMapMan's own PaintOmics export ships twenty, overwhelmingly Secondary
  Metabolism and Hormones;
* the installer adds fifty more from the MapManStore archive — the general maps
  a MapMan user actually reaches for: metabolism overview, glycolysis, TCA,
  photosynthesis, transcription, and the Metabolites compound map.

All of them are MapMan 3.6-era diagrams, matching the bin numbering GoMapMan's
gene mappings use. X4-era diagrams are deliberately excluded: they renumber the
ontology, and would place the wrong genes without reporting an error.

The extra fifty are fetched all-or-nothing. That is deliberate — the number of
pathways is a denominator in the [enrichment test](4_1_pathway_enrichment.md),
so an install that fetched sixty-three diagrams today and sixty-seven tomorrow
would silently change p-values.

Because the pathway identifier is a diagram name rather than an accession, the
external-link button in the enrichment table searches GoMapMan for that name
rather than opening a record.

## Metabolites

MapMan ships a metabolite mapping of its own — compound symbols against bin
codes — which PaintOmics loads alongside the KEGG compound names. A metabolite
name in your file can therefore resolve to a MapMan bin and be painted on a
MapMan diagram, the Metabolites map in particular.

## Genes, and the link back to KEGG

Where KEGG publishes an NCBI-gene-id conversion for the species, the installer
links each MapMan gene to the matching KEGG gene ids, so one identifier from
your file can be painted on both a KEGG map and a MapMan diagram. Where that
conversion does not exist, MapMan genes still install and still map — they
simply stand on their own, unlinked to the KEGG side.

## Classification

MapMan diagrams carry two classification levels from MapMan's own
classification file: a primary category and a secondary one, for example
*Metabolism* then *Amino Acids*. Step 3's category pie and **Filter by
category** tree use those two levels, as they do for the other databases. A
diagram the classification file does not name is filed as *Not classified /
Unclassified*.

For how the four databases differ, see the comparison table on
[the KEGG page](1_1_kegg.md).

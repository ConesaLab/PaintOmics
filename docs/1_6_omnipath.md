# The OmniPath interaction network

OmniPath is the fourth pathway source PaintOmics can run, and the only one that
ships no diagram. It integrates over 100 resources into a prior-knowledge
network of *signed, directed* molecular interactions — A stimulates B, C
inhibits D — for human, mouse and rat. Because there is no drawn map to paint,
an OmniPath pathway opens as an interactive interaction network instead.

The application describes it on the Step 2 mapping screen in those terms and
links to [omnipathdb.org](https://omnipathdb.org/).

## Which organisms have it

Three, and only three: human (`hsa`), mouse (`mmu`) and rat (`rno`). The
omnipathdb.org web service serves those taxa and rejects every other, so no
other organism can have OmniPath installed however the server is configured.

Whether your server has installed it for one of the three is what the Step 1
checkbox says. Installing it is a separate command from the ordinary organism
install — see [Installing PaintOmics](0_install.md).

## What an OmniPath "pathway" is

OmniPath keeps pathway *membership* and the interaction *network* in two
separate datasets, and PaintOmics combines them:

* **Membership** comes from OmniPath's curated annotations, restricted to two
  resources: SIGNOR and NetPath. SIGNOR contributes focused causal modules
  (median around 18 genes); NetPath contributes broader receptor cascades
  (median around 92).
* **Edges** come from OmniPath's interactions endpoint — the core
  literature-curated set plus its directed and kinase-substrate extensions. The
  transcriptional and miRNA layers are deliberately left out: they are a
  different kind of statement and would swamp the graph.
* **A pathway** is the subnetwork its members induce on that graph. A pathway
  with fewer than five members present in the network is dropped, because the
  subnetwork would be too small to read and too small to enrich against.

Mouse and rat need one extra step. OmniPath's annotations are human, so
membership is carried across using the ortholog pairs OmniPath itself used,
recovered by aligning the mouse or rat interaction table against the human one
on their shared source literature — not guessed from gene-symbol casing.

In the STATegra mouse example the job found 120 OmniPath pathways, 10 of them
significant, beside 363 from KEGG and 523 from Reactome.

## What you see when you open one

Click **Paint** on an OmniPath pathway and the pathway panel draws a graph you
can move, zoom and interrogate, rather than a picture.

* **The nodes are your data.** Each is the same painted glyph a KEGG map would
  place on its diagram — one box per matched gene, split into one coloured cell
  per condition, on the same Blue-Grey-Red scale. A gene looks identical here
  and on a KEGG map.
* **The edges carry the sign**, which is the thing OmniPath knows and a diagram
  does not. A green arrow is stimulation, a red bar-headed edge is inhibition,
  and a plain grey line with no arrowhead is an interaction with no recorded
  direction of effect. The key is in the toolbar.
* **Layout** offers three arrangements: *Rings (by connectivity)*, which puts
  the pathway's hubs in the middle and cannot overlap; *Cascade (follows
  direction)*, which runs the graph top-down from the nodes nothing points at,
  the way a signalling diagram is usually drawn; and *Clusters
  (force-directed)*. Rings is the default above about 45 nodes and Clusters
  below it.
* **Causal edges only** hides the unsigned edges.
* **Hover a node** and everything outside its immediate neighbourhood fades, so
  you can read one gene's inputs and outputs out of a dense graph. **Click a
  node** and the [feature detail panel](5_2_detailed_views.md) opens, the same
  one a click on a painted KEGG box opens.
* **The status line** reports how many genes and interactions are drawn. A
  pathway is capped at 900 edges; when the cap bites the line says so —
  *(strongest 900 of N)* — and signed edges are always kept in preference to
  unsigned ones.

!!! warning
    Node positions carry no biological meaning. OmniPath publishes no layout,
    so every position you see was computed — by the layout you chose, from the
    connectivity of the graph. Two nodes drawn close together are not thereby
    related; read the edges, not the geometry. This is also why the pathway is
    presented as a graph you can rearrange rather than as a fixed map.

## Identifiers

OmniPath is keyed on **UniProt accessions**, with RefSeq gene symbols as the
alternative when you upload names rather than accessions. You do not have to
supply either: [identifier translation](1_4_id.md) converts what you uploaded,
and where an identifier cannot reach UniProt directly it is bridged through
gene-level identifiers — Entrez/NCBI gene ids, Ensembl gene ids, KEGG gene ids
— which is how an Ensembl gene id in your file ends up on an OmniPath node.

## OmniPath has no metabolites

Every OmniPath pathway is built with an empty compound list. Nothing from a
metabolomics omic can match an OmniPath pathway, and no OmniPath enrichment
p-value is ever computed from compounds.

One place in the interface does not show this. In the Step 2 **Multiple
databases used** matrix, a compound-based omic shows the *same* count in every
database column, because compounds are matched once against KEGG compound ids
and the server has no per-database compound breakdown to report. The OmniPath
cell on a metabolomics row is therefore not zero, but no OmniPath pathway can
use it. Gene-based rows in that matrix are genuine per-database counts.

## Classification

OmniPath publishes no pathway hierarchy — SIGNOR and NetPath are flat lists of
names — so **the classification you see is one PaintOmics assigns**, not one
carried from upstream. It has two levels.

The top level is a category matched from the pathway's name, first match
winning, so a pathway named for a tumour is filed under cancer even though it is
also a signalling cascade:

| Category | Filed here when the name mentions |
|---|---|
| Cancer | a tumour type or an oncogenic process |
| Infection and inflammation | a virus, an infection, inflammasomes, complement, innate immunity, interferon |
| Immune signalling | B or T cells, macrophages, interleukins, chemokines, TNF, RANKL |
| Nervous system | Alzheimer, Parkinson, axons, synapses, a neurotransmitter system |
| Cell cycle, death and autophagy | the cell cycle, apoptosis, autophagy, death receptors, differentiation |
| Metabolism | metabolism, biosynthesis, insulin, leptin, AMPK, circadian |
| Development and tissue remodelling | fibrosis, Hedgehog, Wnt, Notch, integrins, focal adhesion |
| Signal transduction | everything else — these are all receptor-to-nucleus cascades |

The second level is the curating resource, SIGNOR or NetPath.

In the Step 3 [pathway network](4_3_pathways_network.md) for OmniPath, two
pathways are joined when they share genes, weighted by how many, and the
pathways are grouped under their curating resource.

## Pathway identifiers and external links

OmniPath pathway ids are slugs PaintOmics mints from the resource and the
pathway name; they are not accessions any external site knows. The
enrichment table's external-link button therefore opens the annotation query for
that pathway's resource rather than a record for the pathway itself.

For how the four databases differ, see the comparison table on
[the KEGG page](1_1_kegg.md).

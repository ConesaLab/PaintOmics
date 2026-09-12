# Regulatory omics

A regulatory omic measures something that acts *on* genes rather than being one:
transcription factors, miRNAs, methylation probes, chromatin accessibility, an
RNA-binding protein assay. PaintOmics works out which genes each regulator acts
on, assigns the regulator's values to those genes, and paints the result on the
pathway maps beside the genes themselves. This page covers the two analyses that
do that, the files each one needs, how to choose a model, and how to read what
comes back.

## Two analyses, one entry point

In **Data uploading** (Step 1) the **Available omics** column holds a
single **Regulatory Omic** card — there is no longer a separate miRNA-seq card.
The first time you add one, PaintOmics asks which analysis you want in a window
titled **Regulatory Omic — choose analysis method**.

| | **Pairwise** | **MORE** |
| --- | --- | --- |
| What it does | Correlates each regulator against each of its candidate target genes, one regulatory omic at a time | Fits one regression model per target gene over **all** the regulators at once |
| Panels per job | Several, one per regulatory omic | One; stack further regulators inside it |
| Needs an experimental design | No | Yes, a **Conditions file** |
| Data shape | The same shape of quantification file as any other omic | One column per **sample**, replicates included |
| Selection rule | A correlation cutoff in a chosen direction | VIP, alpha and R², depending on the model |
| Extra results | — | A per-condition coefficient table, a regulator–target network, and an evidence overlay on the diagrams |

The choice is locked for the rest of the job: once you have picked, every further
regulatory panel uses the same analysis. Removing every regulatory panel unlocks
it again.

Most of this page is about MORE, which is the newer and the more demanding of
the two. The [Pairwise analysis](#the-pairwise-analysis) has its own section
near the end.

## What MORE computes

MORE fits **one model per target gene**, using that gene's candidate regulators
and your experimental design. It then reduces the fitted models to a single
table — MORE calls it `RegulationPerCondition` — with one row per
target–regulator pair:

| Column | What it holds |
| --- | --- |
| Target | The gene that was modelled |
| Regulator | The regulator credited with an effect on it |
| Omic | Which regulatory layer that regulator came from |
| Area | The interaction type, when your association file carried a third column (`PROMOTER`, `1st_EXON`, …) |
| Representative | The regulator standing for a group of correlated regulators that were collapsed together |
| Coef_*condition* | One regression coefficient per experimental condition |
| R² | How much of that **target gene's** variance its fitted model explains |

Step 3's regulation grid and regulator–target network, and Step 4's evidence
overlay, are views of this table.

## What you upload

MORE is the only gene-based panel that takes **per-sample** data, and the only
panel where an experimental design file is required rather than optional. The
target matrix and each regulator matrix carry one column per sample, replicates
and all, and the design file says which sample belongs to which group.

The server checks these before the analysis starts, and refuses the job when one
is missing:

| File | Required? | What it is |
| --- | --- | --- |
| **Conditions file** | Required | The experimental design: which group each sample belongs to |
| **Gene expression dataset** | Required | The target matrix. Every gene in it is a response variable |
| **Regulators expression file** | Required, per regulatory omic | The regulator values, one column per sample |
| **Omic Name** | Required, per regulatory omic | Free text or a pick from the built-in list. Must be unique and must not contain a comma |
| **Relevant regulators file** | Optional | Your list of differentially expressed or otherwise relevant regulators |
| **Associations file** | Optional | Each regulator's candidate targets |

!!! warning
    The **Associations file** is genuinely optional — the server passes `NULL`
    to the model when it is absent — but omitting it is the most expensive thing
    you can ask MORE to do. With no associations, every regulator becomes a
    candidate for every gene, and the runtime guard below will usually refuse
    the job and tell you that supplying association files is the largest saving
    available.

The in-browser file check applies to the **values matrix** of an ordinary omic
panel, not to the MORE card: decimal commas, ragged rows and repeated
identifiers are reported on a strip under the omics row, with an offer to
repair, and a file the check blocks prevents submission. MORE's own rows — the
conditions file, the gene expression dataset and each regulator matrix — are not
read in the browser at all; they are validated when the analysis starts, and a
fault in one comes back as an error on the job. The formats themselves are
described in [Accepted input data](2_1_accepted_input.md).

### The experimental design file

A **Sample** column followed by one numeric indicator column per condition, one
row per sample. It does two jobs:

* It defines the groups MORE estimates a coefficient for. The condition names in
  its header are what later label the `Coef_` columns of the results — without
  it, MORE names those columns after the raw indicator pattern
  (`Group_1_0_0_0`), and PaintOmics rewrites them from your design so the table
  and the network's condition menu read `Control`, `Late` and so on.
* It is copied into the pathway job, so the per-sample columns of the resulting
  omic are collapsed into their groups everywhere in the results. On the bundled
  STATegra MORE example that turns 36 replicate columns into 12 conditions, and
  it is what makes Step 4's **Show all replicates** / **Show samples (averaged)**
  choice mean anything. If the design does not cover every column of the omic,
  the collapse is skipped silently and every heatmap draws one column per sample.

MORE's own reader refuses a design file whose cells are not all numeric — it
names the offending columns — and one that carries a header with no rows under
it.

### Sample names must match, exactly

The target matrix, the conditions file and every regulator matrix are
intersected by sample **name**. PaintOmics deliberately does not fall back to
matching columns by position: silently aligning differently-named samples by
order is how meaningless results get published. If nothing intersects, the run
stops and prints the first sample names it read from each file, so you can see
which naming is out of step.

### Association files

Two columns, either way round: PaintOmics works out which column holds
regulators by matching them against the regulator matrix, and swaps them if
needed. A third column is accepted and kept as the interaction type, and is
carried through to the **Area** column of the results. Four or more columns are
rejected, and so is a file whose identifiers share nothing with the matrices —
in which case both identifier spaces are printed side by side, so you can see
the mismatch without reopening the files.

!!! note
    MORE reads an association file **with a header row**: its first line is
    consumed as column names. The Pairwise analysis reads the same shape of file
    treating every line as data. The same file handed to both therefore loses or
    keeps its first pair depending on which analysis reads it.

### Several regulatory layers in one model

**+ Add another Regulatory Omic** adds a second block with its own name, matrix,
relevant list, association file and **Min. variation**. All the layers are fitted
together, so a gene's transcription factors and its miRNAs compete for the same
variance instead of being analysed separately.

Names must be distinct and comma-free. Two names that differ only by a space
(`TF A` and `TF_A`) collide, because both produce the same output filename, and
the job is refused rather than letting one set of results overwrite the other.

One MORE panel produces **one painted omic per regulatory layer**. After a
successful run the card says so — *N regulatory omics processed* — and from
Step 2 onward each layer is a separate omic in the mapping summary, in the
network's **Node coloring** list and as its own row in every feature window.

## Choosing the model and the engine

One drop-down, **Regulatory model**, chooses both the statistical method and the
implementation that runs it.

| Option | Method | Engine | What it is for |
| --- | --- | --- | --- |
| **PLS1 — Rust engine (recommended)** | PLS1 | Rust | The default. Measured byte-identical to the R engine on the bundled real dataset, and several hundred times faster |
| **PLS1 — R engine (reference)** | PLS1 | R | The original MORE R package. Same answers, far slower; choose it to reproduce a published run |
| **MLR — R engine** | MLR | R | Elastic-net multiple linear regression, the reference implementation |
| **MLR — Rust engine (opt-in)** | MLR | Rust | The same elastic-net model, roughly 8–30× faster |

The list is fetched from the server, so an option this installation cannot run
stays visible but greyed out with the reason — *This server has no more-rs binary
installed*, *R is installed but the MORE package is not*. Selecting it is
refused with that explanation rather than letting you fill in the rest of the
form first, and a job that reaches the server naming an unavailable engine is
refused up front with the same sentence plus a list of what is available. If
nothing at all can be run here, Step 1 says **No regulatory model can be run
here** as soon as you open the card.

A submission that names no engine at all — an old browser, a re-run of a stored
job, a scripted request — runs PLS1 on the Rust binary when one is installed and
on R otherwise. **MLR is never moved to the Rust engine on your behalf.** PLS1
earned a silent default by being byte-identical to R; MLR has not, because MORE
runs its underlying solver at a tolerance where it has not converged and where R
does not reproduce itself either. The two MLR engines agree on every random draw
— all 8,157 of them on the bundled STATegra dataset, so collinear regulators are
grouped and represented identically — and they very nearly agree on the pairs
they report: of all the regulator–target pairs either engine reports, 99.1% are
reported by both. On the simulated dataset that figure falls to 88.6–92.3%. Pick
the Rust engine for speed, the R engine to match numbers you have already
published.

Runtimes recorded with the bundled STATegra MORE example (957 target genes, 387
transcription factors, 36 samples, 12 groups) give the order of magnitude:

| Engine | Measured runtime |
| --- | --- |
| PLS1 — Rust | 0.1 s |
| PLS1 — R | 234.4 s |
| MLR — R | 739.8 s |

### PLS1 or MLR?

**PLS1 is the better default, including for the reason most people reach for
MLR.** The usual argument for multiple linear regression is that it returns real
p-values and interpretable effect sizes. In MORE it does not: the MLR path
reports a coefficient per regulator and **no p-value at all**, because a
regulator counts as relevant precisely when elastic-net shrinkage left its
coefficient non-zero. There is no stepwise selection anywhere in it. That is
why the **Alpha (Significance)** and **VIP threshold** fields disappear when you
select an MLR model — neither applies.

MLR also collapses correlated regulators into a group and draws **one member**
to represent it. The draw is seeded — MORE's `more()` takes `seed = 123` and
calls `set.seed(seed)` before anything else, and the PaintOmics wrapper never
overrides it — so re-running the same job credits the **same** regulator. Two
runs of the bundled simulated example return an identical table with identical
representatives.

What the draw does mean is that the regulator named for a correlated group is
not necessarily the one the data singles out — and how much weight the draw
carries depends on which of MORE's three draw sites decided it. Two of them
pick uniformly from the correlated set, so on those paths any member was an
equally good candidate. The third picks the regulator with the highest
connectivity in the correlation graph and draws only to break a tie among
equals, so there the choice is partly earned.

Either way, read an MLR hit as *"this correlated group, standing behind the
member MORE named"* rather than as *"this regulator rather than its
neighbours"*. Prefer MLR only when you have many more samples than candidate
regulators per gene, which is the regime it suits.

PLS1 produces both a VIP score and a jackknife p-value, and a regulator is
reported only when it clears both thresholds.

## The filters

| Control | Default | What it does |
| --- | --- | --- |
| **Min. variation** (per omic) | 0 | The minimum change in standard deviation (numeric regulators) or proportion (binary regulators) a regulator must show across conditions to be modelled at all. 0 keeps everything except constants. Each regulatory omic carries its own value, so methylation and miRNA can be filtered differently in one run |
| **Alpha (Significance)** | 0.05 | PLS1 only. The threshold on each regulator's jackknife p-value |
| **VIP threshold** | 0.8 | PLS1 only. Variable Importance in Projection: how much the regulator contributes to the components that explain that gene. 0.8 is deliberately permissive; 1.0 is the conventional cutoff and the first thing to raise if a run returns more regulators than you can read |
| **R2 Filter** | 0 | Discards any target gene whose fitted model explains less than this fraction of the gene's variance, before its regulators are reported. 0 keeps every model that converged |

A regulator must clear **both** Alpha and VIP to be reported, so lowering one
alone will not necessarily shrink the results.

### The job is costed before it is queued

MORE fits one model per target gene, so its cost grows with the number of genes,
with the number of candidate regulators per gene, with the size of the design
and with the engine. PaintOmics estimates the runtime before queueing and
refuses anything it predicts cannot finish inside the limit for a single
analysis. The refusal quotes the shape you submitted and the estimate, and says
what to change — supply association files, switch method where that would help,
or filter the target matrix down to differentially expressed genes.

## How the result reaches the pathway analysis

For each regulatory omic, the run writes a values file keyed
`GENE:::REGULATOR` — one row per input pair, carrying **the regulator's**
per-sample values under the target gene it acts on. That file is what becomes an
omic in the pathway analysis, so the colour you see at a gene's position for a
regulatory layer is the regulator's value, placed at its target.

Alongside it the run writes the full associations file, a significance-filtered
pairs file, a relevant-regulators file built from your uploaded list, a copy of
the experimental design, and the combined coefficient table.

Two consequences are worth knowing before you read a map:

* **Rows read regulator-first.** In the heatmaps and detail tables, an omic whose
  values arrived as `target:::regulator` puts the **regulator** in the row
  identifier and the target beside it as context — the inverse of an ordinary
  omic, where the row is the gene. Where the regulator resolves to a gene symbol
  the symbol is shown with its canonical id beside it; where it does not (miRNA
  names, methylation probes) the raw id is kept and the target's symbol is shown
  instead. Symbol resolution applies to every regulatory omic, whatever you named
  it.
* **A MORE omic is always enriched by counting genes.** The Pairwise and
  Region-based cards let you choose whether the enrichment test counts genes,
  features or associations; the MORE card has no such control, and the server
  counts target genes. See [Pathway enrichment](4_1_pathway_enrichment.md) for
  what that test does.

## Reading the results

### MORE regulation analysis (Step 3)

A sortable grid of every target–regulator pair MORE reported, with one
coefficient column per condition rendered to three decimals and gene symbols
resolved next to the raw ids. You can filter by omic, search targets and
regulators by id or symbol, and download exactly what you see as TSV. Two
magnifiers on each row jump to the enriched pathways containing that row's
target or that row's regulator — a regulator only matches when it is itself a
pathway gene, which transcription factors often are and miRNAs are not, so an
empty result there is itself informative. A banner appears if the table was
truncated at 100,000 rows.

### MORE Regulator–Target Network (Step 3)

The same results as an interactive bipartite graph: regulators fan out to the
targets they regulate, edge colour is the **sign** of the coefficient and edge
width its magnitude. The side rail filters by condition, by R², by |coefficient|
and by an edge budget, switches each regulatory omic on and off, and lists the
top hubs by *visible* degree — which is not the same list as the top hubs
overall, and is the point of showing it beside the filters. A search box matches
case-insensitively against both the displayed label and the raw identifier, then
centres and pins the first match; on a graph capped by the edge budget that is
the only way to reach a named regulator. The status line under the toolbar
states how many nodes are showing, how many of the total edges, the condition,
and the settings the job ran with.

### Red and yellow stars

Two markers appear beside a feature name in the pathway boxes, the detail
windows and every heatmap. For a MORE job they mean different things, and it is
not the distinction the older documentation described:

* A **yellow star** means MORE itself judged that regulator–target pair
  significant.
* A **red star** means the regulator appears in the **Relevant regulators file**
  you uploaded. It is expanded to every `GENE:::REGULATOR` pair in the values
  file whether or not the model agreed. **If you upload no relevant-regulators
  file, no red stars are drawn at all**, no matter what the model found — and
  the enrichment for that omic then has nothing relevant to test.

### Evidence overlay on the pathway diagram (Step 4)

On an open KEGG diagram, the regulator→target relationships MORE found are drawn
as violet arcs over the map and classified against curated interaction
databases: **corroborated** (a database records this interaction), **novel**
(both partners are curated but nothing links them) and **no coverage** (a
partner has no curated interactions at all, so the silence says nothing).
Arrowheads carry the sign of MORE's coefficient — an arrow for positive, a bar
for negative, hollow when the box it lands on holds several genes. Regulators
the map does not draw are added as dashed boxes beside their target, and you can
drag them. The legend in the **Pathway information** column states how many
relationships were drawn, which databases corroborated anything, and what the
edge cap left out; it offers **Hide layer**, and **Reset positions** once
something has been moved.

!!! warning
    The direction on those arrowheads is **this job's model**, not a curated
    claim. Sign concordance between MORE and KEGG, Reactome and OmniPath was
    measured at 58.3%, against 52.8% by chance. Read the arc as "the model fitted
    a negative coefficient here", never as "the databases agree that this is
    repression".

## What a coefficient is, and what it is not

A coefficient is the regulator's **slope** in one experimental condition: an
unbounded number in the data's own units. Zero means no effect in that
condition.

* It is **not** a correlation, a fold change or a probability, and it is not
  bounded to any range.
* It is **not comparable across regulatory omics**, or across regulators
  measured on different scales, because the units differ. Comparing coefficients
  is safe within one target gene's model and one omic; across models it is not.
* A large coefficient on a badly fitted model means nothing. **R² is what says
  whether the model for that gene is worth reading at all** — use the network's
  R² filter, or the **R2 Filter** at submission, before you rank anything by
  coefficient.
* **MLR reports no p-values.** If you chose an MLR model, there is no
  significance measure in the output: a regulator is present because shrinkage
  did not remove it. Only PLS1 gives you the VIP score and the jackknife p-value
  that Alpha thresholds.

## The Pairwise analysis

The classical regulator→target workflow, one regulatory omic per panel. It takes
a **Regulators expression file** (required), an optional **Relevant regulators
file**, an **Associations file** naming each regulator's candidate targets — the
form marks it required, and the pairing has nothing to work from without it —
and an **Enrichment type**. It then decides which candidate pairs are real,
assigns each regulator's values to its target genes, and hands
`GENE:::REGULATOR` rows to the pathway analysis exactly as MORE does.

**Enrichment type** chooses what the enrichment test counts for this omic —
**Genes** (the target ids), **Features** (the regulator ids) or **Associations**
(the pairs). The same choice defines both the background and the sample of the
test, so it changes which pathways come out enriched for this layer.

There are two ways to decide which pairs are real:

* **Upload a Relevant associations file** — a two-column list of the pairs you
  consider genuine.
* **Tick "Automatically select relevant associations using correlation"** — the
  relevant-associations field is then disabled, a **Gene expression dataset**
  becomes required, and PaintOmics scores every candidate pair by correlating
  the regulator against its target gene. The required/optional tags on both rows
  flip as you tick and untick. The gene expression selector has a **Use a file
  from other omic** button, so you can correlate against exactly the
  transcriptomics matrix you are already painting rather than uploading a second
  copy.

The correlation block has four controls:

| Control | Default | What it does |
| --- | --- | --- |
| **Report** | All features | Whether to report all regulators or only the relevant (e.g. differentially expressed) ones |
| **Score method** | Kendall | The correlation statistic between regulator and target gene: Spearman, Kendall or Pearson |
| **Selection method** | by negative correlation | Which direction counts. Negative is the expected sign for miRNA repression |
| **Filter cutoff** | −0.5 | The threshold, applied in the direction of the selection method. Its sign follows the method you chose |

A checkbox at the top of the card, **My features are already mapped to Gene
IDs, skip this step.**, swaps the card for a shorter form that accepts files
whose regulators have already been mapped to genes and skips the conversion
entirely.

The conversion writes four files the pathway analysis then consumes: the values
file keyed `GENE:::REGULATOR`, a relevant-regulators list, a trimmed
associations file and a relevant-associations file. When nothing joins, the run
is refused rather than succeeding with an empty result — but the message is a
short one asking you to check that the files use the same identifiers, so
comparing the identifier spaces is left to you.

## Example datasets

**Load example** on Step 1 groups the bundled datasets by pipeline, including
**Regulatory omics — pairwise** and **Regulatory omics — MORE**. Each card says
whether the data is real or simulated, how many omics and conditions it has, and
what it exercises. The MORE group ships:

* a **simulated** mouse dataset — four conditions, three replicates per group,
  two candidate regulatory omics with association files, each target a noisy
  linear function of one known regulator, so what MORE should find is written
  down; and
* a **real** one — the STATegra Ikaros induction time course (GEO GSE75417)
  against the literature-curated half of the TFLink v1.0 mouse network: 957
  genes, 387 transcription factors, 36 samples and 12 groups, nothing
  subsampled.

Loading a MORE example fills the card with read-only labels. The model
parameters come from the manifest rather than the form, so alpha, VIP and the R²
filter are shown read-only too — and so is the **Regulatory model** picker, so an
example runs on whichever engine the picker defaults to on this server.
See [Example datasets](examples.md).

## Two supporting tools

Both conversions are also available on their own pages, outside a pathway job,
each with its own explanation of the input formats, a **Load example** button and
a **Download example data** link. Results are offered as a downloadable zip and
the gene-level output is filed in your personal storage for reuse in a later job.

* **From Regions to Genes** runs RGmatch, matching genomic regions
  (ChIP-seq, DNase-seq, ATAC-seq, methylation) to the closest gene and reporting
  the area of the gene the region overlaps. It needs a GTF annotation, in any order, with
  exon-level rows, a BED-style region file with quantification values, and
  optionally a list of relevant regions. The **Annotations file (GTF)** row has a **Use a GTF from
  PaintOmics** button that lists the annotations the installation already ships,
  which saves finding and uploading a genome annotation. The same conversion is
  available inside a job as the **Region-based omic** card.
* **From miRNA to Genes** runs the Pairwise regulator→gene conversion described
  above on its own.

!!! note
    The **Tools** menu is only shown to signed-in accounts. On a "continue
    without an account" session it is removed from the navigation, and both
    standalone converters are unreachable — the equivalent cards inside Step 1
    still work. See [Accounts and storage](2_2_cloud_drive.md).

Both file formats are documented in
[Accepted input data](2_1_accepted_input.md).

## Where to go next

* [A whole job, step by step](8_step_by_step.md)
* [Pathway enrichment](4_1_pathway_enrichment.md)
* [The pathway network](4_3_pathways_network.md)
* [Reading a pathway diagram](5_1_browsing_pathways.md) and
  [the detail views](5_2_detailed_views.md)

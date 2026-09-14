# Release notes

The authoritative, complete list of changes is
[CHANGELOG.md](https://github.com/ConesaLab/PaintOmics/blob/master/CHANGELOG.md)
in the repository. This page summarises what a user of PaintOmics 4 will notice
that is different.

## Unreleased — PaintOmics AI

The successor to PaintOmics 4 has not been cut as a numbered release yet.
Everything below is on `master`. Which of it is switched on depends on the
server you are using — see [What the AI does](ai-overview.md#what-is-on-by-default).

### New analyses

* **AI interpretation.** An agent walks the network of every KEGG, Reactome and
  OmniPath interaction for your organism with your values on its nodes, and a
  writer turns the walk into a Results section with numbered citations. Every
  value, leg and citation is checked by code before you see it. See [The
  interpretation](ai-interpretation.md).
* **Metabolite class activity.** Whether a whole KEGG BRITE class responded,
  at three levels of the hierarchy — a permutation test on your own replicates
  when the upload has them, a binomial test on your relevant list when it does
  not. See [Metabolite class
  activity](4_5_metabolite_class_activity_analysis.md).
* **Metabolite hub analysis.** Which metabolites have differentially expressed
  genes concentrated around them in the KEGG reaction network. See [Metabolite
  hub analysis](4_4_metabolite_hub_analysis.md).
* **Regulatory modelling with MORE**, behind a method chooser with four
  engines — PLS1 and MLR, each on the R reference implementation or on a Rust
  port — plus a regulator–target network on the results screen and
  regulation-per-condition tables that hand off into the pathway view. See
  [Regulatory omics](4_6_Regulatory_omics.md).
* **OmniPath** as a fourth pathway database. See [The OmniPath interaction
  network](1_6_omnipath.md).

### New in the workflow

* **Any number of conditions.** Analyses run across as many conditions as your
  files carry, not two, with per-condition significance stars in the heatmaps,
  in the hub analysis and in class activity, and a weights panel for the
  combined p-value.
* **Replicates.** Per-sample values with an experimental design, aggregated for
  the visualisation and used directly by MORE and by the class activity test.
* **An input-format check on every file you pick**, with one-click
  deterministic repairs for mechanical faults, and — on servers that have
  turned it on — an [AI converter](ai-input-converter.md) for files that are
  not in PaintOmics' format at all.
* **Compound disambiguation** on Step 2, with an AI suggestion for the
  ambiguous names in one pass (capped at 90 sets per run, so a job with
  hundreds of them takes more than one).
* **Example datasets** for every pipeline, generated from a manifest that also
  records the pathways the analysis should recover. See [The example
  datasets](examples.md).
* **Jobs are kept for as long as the interface says they will be** — 7 days for
  a guest job, 14 for one belonging to an account — and no longer.

### New in the interface

* The application is renamed **PaintOmics AI**. Navigation moved into the
  header and the left rail was removed.
* A dark theme, with a toggle in the header.
* A contents sidebar on the results screen that follows the database tab you
  are looking at.
* An organism picker that ranks matches by what you typed.
* A rebuilt heatmap colour scale, fitted to the range your data actually
  occupies, and the same ramp now paints the pathway diagrams.
* A progress bar for a running job that only moves forward and says what the
  job is doing.
* **Browse…** moved inside the file field, and every file row now says whether
  the job requires that file.

### Under the hood

Python 3.11, Flask 3 and pymongo 4 against MongoDB 7; a Docker Compose
deployment with nginx and TLS; and continuous integration with a pull-request
gate and a nightly regression run against recorded baselines.

## PaintOmics 4

* New tutorial video — 18 July 2022
* New user guide — 1 July 2022
* Release of PaintOmics 4 (v1.0.0) — 31 May 2022

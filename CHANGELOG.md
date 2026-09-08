# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

PaintOmics AI has not been cut as a numbered release yet, so everything since
PaintOmics 4 is listed under Unreleased. Entries reference the pull request that
merged them where one exists.

## [Unreleased]

### Added

- AI interpretation of the ranked pathways: an agent reads the cross-omic patterns, searches PubMed, and drafts the biology with numbered citations that link back to the source record.
- A verification stage over that draft, which checks every claim and quotation against the retrieved papers and redacts what it cannot ground rather than publishing it.
- Interpretation of pathways inside their shared-feature clusters, with the Step 3 pathway network coloured by the same clusters and a legend that explains the cluster ids.
- A Results-section view of the interpretation, written the way a paper would write it and keeping every citation, and an activity feed showing which tools the agent is calling while it works.
- An AI input-format converter that checks every upload against the server's contract, offers one-click deterministic repairs for mechanical faults, and can rewrite a file the analysis cannot read (#67, #72).
- The MORE regulatory model behind a method chooser in Regulatory Omics, with three engines: PLS1 on a Rust port, PLS1 on R, and MLR on R (#44).
- A refusal at submit time for a MORE job not predicted to fit inside the queue's timeout, with a cost estimate the operator can calibrate to the host (#45).
- A Step 3 regulator-target network drawn with Cytoscape.js, with free layout, per-condition colouring, search, spotlight, side panel and exports.
- Regulation-per-condition tables beside that network, a "Find in pathways" hand-off from them into the pathway view, and a user-selectable per-omic minimum variation filter for MORE.
- Multi-condition designs: analyses run across any number of conditions rather than two, with per-condition significance stars in the Step 4 heatmaps, in Metabolite Hub and in Class Activity, and a Stouffer weights panel for the combined p-value.
- Replicate-to-sample aggregation in both the pathway visualization and MORE, with the experiment design drafted from your column headers before a job exists.
- A metabolite class activity analysis drawn as a levelled class map, testing whether a class responds on replicates when the data has them (#96, #101, #105).
- A Step 2 disambiguation step that lets you choose which compound an ambiguous metabolite name meant (#88).
- A metabolite hub network derived from the KEGG graph and drawn in the interface (#89).
- An evidence overlay that draws MORE relationships on the pathway diagram as a provenance-labelled layer (#52), including regulators the diagram itself does not print (#84).
- OmniPath as a fourth pathway database source (#43), rice (osa) as a MapMan species (#42), and installation of custom species from a gene-to-KO annotation table.
- Bundled example datasets built from a seeded, manifest-driven generator, with the pathways the enrichment should recover listed alongside the files, and **Load example** for Regions2Genes, miRNA2Genes and MORE as well as pathway acquisition.
- A dark theme with a toggle in the header, a contents sidebar on the results page that follows the database tab in view, and an organism picker that ranks results by what was typed rather than by prefix match (#75).
- A Docker Compose deployment (nginx/TLS, application, MongoDB), a post-deployment smoke test, an operator runbook, species installation from the command line, and a view of the requests users have sent in, in the admin panel (#54).
- Continuous integration: a pull-request gate, a nightly regression run against recorded baselines, staging deployment on merge, and an on-demand profiling workflow.
- Test coverage that had not existed: an import smoke test over every tracked module, an end-to-end enrichment run against a real installed species, the real R backend instead of a double, and a check that an edited asset gets its cache marker bumped.

### Changed

- The application is renamed PaintOmics AI, the successor to PaintOmics 4.
- Navigation moved into the header and the left rail was removed.
- The interface runs on one token-based design system for surfaces, shapes and type, with a single typeface and WCAG AA contrast throughout; the landing page, the application mark and the account dialogs were redrawn, and the ExtJS dialogs, tables and upload controls modernized.
- The running-job spinner was replaced with a progress bar that only moves forward and reports what the job is doing.
- The heatmap colour scale was rebuilt against the range the data actually occupies, and the same ramp now paints the pathway diagrams (#63, #92).
- The platform moved to Python 3.11, Flask 3, and pymongo 4 against MongoDB 7, with the imaging and HTTP stacks upgraded onto versions carrying their security fixes, and dependencies declared in one pinned `requirements.txt` at the repository root.
- The AI interpretation was reimplemented on the OpenAI Agents SDK, replacing the earlier threaded pipeline, and PMID markers are rendered as numbered references.
- The analysis pipeline was profiled and made faster: batched cross-reference lookups, one shared fork-aware MongoDB client, a cached GTF annotation, and metagene R scripts run per omic in parallel (#33).
- Step 2 was rebuilt as one module system — a databases matrix, a levelled class-activity pair, and compound disambiguation inside the grid (#111, #114) — and Browse now lives inside the file field (#110).
- Every Step 1 file row now says whether the job requires that file (#108), and the database checkboxes are drawn from what the server has actually installed.
- Jobs are kept for as long as the interface says they will be, and no longer (#65), and the contact address is now `paintomicsai@gmail.com`.

### Fixed

- Every AI feature depended on one pinned gateway model and failed outright when its backend went away (llm.iiia.es, 2026-09-08). Both transports now ask the pinned model first and fall back, in order, to `AI_CSIC_FALLBACK_MODELS` (default: the gateway's `default/llm` alias) when it is not being served, skip a failed model for five minutes, and record the model that actually answered; a plain request that a gateway answers with a 5xx is retried streamed before a model is given up on.
- The AI input converter was off on every Docker deployment whatever `deploy/.env` said: `compose.yaml` never passed `AI_INPUT_CONVERTER` to the container, so each rebuild shipped the code default and every conversion ended, after the sandbox had read the file, in "AI file conversion is not enabled on this server" over a disabled box. The switch is now passed through and documented, the upload strip and the sheet ask the server before starting and say so up front with the manual route, a refused or unanswered turn offers Try again, and the smoke test reports any `.env` setting the container does not see and asks the LLM gateway one question; a nightly job asks it the same question with the key held as a repository secret.
- Enrichment counting inflated `totalMatched`, changing which pathways were reported as significant.
- Step 3's hub scorer used the enrichment denominator instead of its own, and Benjamini-Hochberg is now applied across the whole p-value vector rather than piecewise.
- Non-finite p-values were carried into the FDR correction and into the combination step, where a single NaN produced a NaN result; they are now dropped first, and the combining statistics were checked against SciPy.
- Multi-condition analyses crashed at four separate p-value sites, were impossible from five conditions upward, propagated the adjusted combined p-value for only one condition instead of all of them, and the Stouffer weights panel could not be operated and blanked the p-values it was meant to reweight.
- Metagene clustering was not reproducible for an identical job and produced identical, lopsided clusters, and an R drop-to-vector bug silently killed metagenes for whole omic and database pairs.
- The pathway interaction network divided matched compounds by the gene count, so a metabolomics-only job drew almost nothing (#113).
- A compound omic reported more mapped features than it contained, and features cloned across databases doubled their omic values.
- Reactome class enrichment was computed and then discarded after every analysis, and features were marked eligible for a hardcoded pair of databases rather than for every database selected.
- An analysis that matched nothing died with a division error, and the metagenes step crashed on an empty file instead of saying nothing matched (#97).
- Dependabot resolved the Python dependency group against the newest interpreter instead of the 3.11 this project pins, so its first pull request proposed a NumPy and a SciPy that cannot install here and took the other twenty bumps down with them; the interpreter is now declared in `.python-version`, and `test_dependencies_declared.py` fails if that file and the seven places CI and the images name an interpreter ever disagree.
- A relevance-file header heuristic that only worked for Arabidopsis was replaced with an organism-agnostic one, and a legacy two-column parser dropped the suffix from every row after the first, costing 14 pathways.
- Identifiers that sit two hops away in the cross-reference graph were unreachable, so features that needed an intermediate database were reported as unmapped (#49), and a symbol lookup returned 38 rows for the 13 genes asked for, duplicating features downstream.
- An empty identifier was used as a join key, and association rows were written without a target gene identifier (#95).
- Gene-based jobs crashed outright on dme, bta, ptr and acs (#85), and four further species had no KEGG table speaking their identifiers (#87).
- MORE truncated regulator identifiers that began with the omic name, and read an empty matrix out of a comma-delimited file or one with duplicate identifiers.
- Reactome pathway views showed the input identifier instead of the matched gene symbol (#37), Step 4 showed TAIR/AGI identifiers instead of symbols, gene symbols containing `#` broke metagene generation (#40), and a species lost its MapMan bins when KEGG had no NCBI gene ID conversion for it (#29).
- The miRNA2Genes example could not run and mislabelled the conditions in its output, and Regions2Genes did not read relevant regions, opened files in the wrong mode, and silenced its own module loggers.
- The pymongo 4 migration of the DAO layer fixed a state in which no job could be saved at all, and a status poll could break the job store; a job is now serialised from a snapshot.
- Two simultaneous submissions lost one job and ran the other twice, a second Step 1 submit is now refused (#30), and a failed job blocked its own id until the server restarted instead of being resubmittable.
- Deleting one job wiped another job's data, and deleting a Regions2Genes or miRNA2Genes job only appeared to delete it.
- A blank line in an input file killed the job, a metabolite file of dashes could take down the server, CSV uploads with a byte-order mark were rejected, and the data-management tools did not apply the encoding handling the main upload does.
- Omics whose files disagreed on their conditions were read one at a time and refused (#99).
- The Other data type panel could never be submitted: the picked file type was stored as null and an optional field was required (#109).
- A refused Step 1 form said nothing about which field was wrong, and deleted the panel it was blaming (#90, #93, #112).
- A large pathway export exceeded Werkzeug's form-memory limit, image export was capped and lossy (#47), and pathway PNG download broke on the CairoSVG upgrade.
- The AI status poll retried a session that could not come back, and the References section printed out of citation order (#36).
- A user report was lost when the mail provider was down; reports are now stored before delivery is attempted (#50).
- The hub graph hid 64 of its 72 edges (#102), and a hub node was asked for a single direction instead of showing one wedge per condition (#104).
- The class map's hover readout resized the caption and oscillated under the cursor (#100), resetting to the landing page left detached job views alive (#82), the "Neighbouring features" button did not answer every click (#39), and a Step 4 tooltip failed on an unnamed event (#19).
- The KEGG organism list download broke when KEGG retired `/list/organism`, and the Reactome installer cached error bodies as data and downloaded every pathway's node JSON twice.
- Species installation discarded its own hub analysis data and a KEGG or Reactome rebuild deleted every OmniPath pathway; every install step is now idempotent, so a rerun skips finished work and cannot lose files it does not replace.
- Three broken links were repaired, including a "Cite PaintOmics 4" link that pointed at the wrong paper.

### Security

- Path traversal was closed on both routes that took a name from the request: a job id could name a directory tree to delete, and a file name could name a file outside the user's directory.
- Forgeable user identity was fixed, covering both the `userID=0` bypass and reuse of a retired id.
- Session and password-reset tokens are drawn from `secrets` rather than `random`.
- Per-request authorisation guards were added where they were missing: re-running step 2 on someone else's job, writing an image into a job's directory, reading someone else's AI report (#62), and the admin and read-only routes.
- The session check that every other handler in its family performed had been skipped by SaveImage, and is restored.
- Password hashes are no longer sent to the admin users panel, and a password change could land on another user's account.
- AI consent is enforced on the server before anything is sent to the LLM service, and the three other routes around the consent check were closed.
- The AI report's HTML is sanitised before rendering, and a server error response is parsed rather than evaluated.
- A MongoDB cleanup routine leaked data across users (#12).
- Committed secrets were removed, the live server configuration untracked, and a release-hygiene test now fails the suite if a secret is committed again.
- Dependency upgrades across Flask, Pillow, CairoSVG and the HTTP stack bring in their published security fixes.

### Removed

- The left navigation rail, replaced by navigation in the header.
- R from the metabolite hub analysis, which now runs on the derived KEGG graph (#89).
- Vendored build toolchains that nothing installed or served, and a second `requirements.txt` that was raising 33 phantom dependency alerts.
- The six-phase AI workflow arm, superseded by the agent workflow.
- Dead code across the server, including the 154 rows the audit marked for deletion and the eight star imports in the application entry point.

## PaintOmics 4 and earlier

Release history up to PaintOmics 4 is not restated here. It was kept in a
plain-text `CHANGELOG` file, last written for `v0.4.4` on 2017-04-28 and since
removed from the tree -- `git log --diff-filter=D -- CHANGELOG` still has every
line of it if the v0.4.x detail is ever wanted. The papers below are the record
it pointed at, and they describe each release:

- **PaintOmics 4** — *Nucleic Acids Research* (2022), [10.1093/nar/gkac352](https://doi.org/10.1093/nar/gkac352)
- **PaintOmics 3** — *Nucleic Acids Research* (2018), [10.1093/nar/gky466](https://doi.org/10.1093/nar/gky466)
- **PaintOmics 2** — *Bioinformatics* (2011), [10.1093/bioinformatics/btq594](https://doi.org/10.1093/bioinformatics/btq594)

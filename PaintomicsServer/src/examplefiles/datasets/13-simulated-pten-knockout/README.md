# Simulated Pten knockout — signed propagation over the universal network

A Pten knockout simulated on the walker's universal network: Pten set to -2.5 log2 and the effect propagated 2 signed steps downstream (x0.6 per step, sign flipped over inhibition) over a N(0, 0.35) background, developing across six time points. The ground truth is a neighbourhood of 160 genes, not a pathway; it exists to test whether the AI interpretation walks from Pten to what it regulates.

|  |  |
| --- | --- |
| **id** | `simulated-pten-knockout` |
| **pipeline** | `pathway-acquisition` |
| **organism** | `mmu` |
| **conditions** | 6 (KO/WT_0h, KO/WT_2h, KO/WT_6h, KO/WT_12h, KO/WT_18h, KO/WT_24h) |
| **simulated** | yes |

## What it exercises

* Pathway enrichment
* Multi-condition heatmaps
* Pathway network
* AI interpretation

## Files

**Gene expression**
* values — `gene_expression_values.tab`
* relevant features — `gene_expression_relevant.tab`

## Expected result

* **backgroundRelevantFeatures** — 45
* **decayPerStep** — 0.6
* **knockedOutGene** — Pten
* **knockedOutNode** — g:19211
* **knockoutEffectLog2** — -2.5
* **minTargetCoverage** — 0.042
* **noiseSd** — 0.35
* **note** — The pathways in expected_pathways.txt are the KEGG pathways Pten has edges in, listed with the planted share of each (4-29%). They are where the walk should find the knocked-out neighbourhood, not enrichment targets: no pathway is expected to top the ranking, and the >=50% planted-coverage rule the pathway-planting scenarios meet does not describe a knockout. targetCoverage records the true shares.
* **pathwaysFile** — `expected_pathways.txt`
* **plantedRelevantFeatures** — 74
* **propagationSteps** — 2
* **relevantAbove** — 1.0
* **relevantFeatures** — 119
* **signalFeatures** — 160
* **signalFeaturesFile** — `signal_features.txt`
* **targetCoverage** — [0.053, 0.179, 0.09, 0.23, 0.137, 0.07, 0.194, 0.057, 0.074, 0.102, 0.042, 0.286]
* **targetPathways** — 12

## Provenance

Built by `src/classes/AIInterpret/walker/simulate_ko.py` from the installed KEGG and Reactome annotation (the walker's universal network, no OmniPath), not by the example generator:

    cd PaintomicsServer
    PYTHONPATH=. python -m src.classes.AIInterpret.walker.simulate_ko \
        --data-dir <KEGG_DATA> --organism mmu --gene Pten --seed 0 \
        --out src/examplefiles/datasets/13-simulated-pten-knockout

The model is a knockout propagated along signed edges. Pten is set to -2.5 log2 (KO over WT) at every time point. Each gene reached over a signed edge walked with its arrow takes 0.6 of its source's value, the sign copied over activation/expression and flipped over inhibition/repression/dephosphorylation, for 2 steps; unsigned edges (binding, phosphorylation, compound links) are not followed, and edges are not walked against their arrow because a knockout does not change the genes that regulate the lost protein. A gene reached by several paths keeps the larger-magnitude value. The planted effect develops over the six columns (0.25/0.5/0.75 of it at 0 h, 2 h, 6 h, then the full effect) and every gene adds N(0, 0.35) noise from `random.Random(0)`. A gene is relevant when |KO/WT_24h| > 1.0.

On the 2026-09 mmu snapshot: 11,531 rows (the network's gene nodes that have an Ensembl gene id; two Entrez ids on one Ensembl gene give one row), 160 planted (Pten, 9 direct targets, 150 at the second step), 119 relevant of which 74 are planted and 45 are background noise past the threshold. `expected/signal_features.txt` lists the planted genes. Unlike the other simulated scenarios, the feature universe is the KEGG+Reactome network rather than KEGG's gene list, and the ground truth is a neighbourhood rather than a set of planted pathways -- the dataset exists to ask whether the Agentic Graph Walk recovers the knocked-out module from Pten outwards.

---

This README was rendered by `writeReadme` in `src/AdminTools/scripts/exampledata` from the manifest entry, with this closing note written by hand: the **data files are built by `src/classes/AIInterpret/walker/simulate_ko.py`**, not by the example generator, and `python -m src.AdminTools.scripts.exampledata` neither knows this scenario nor rebuilds it. Rerun the command under Provenance to rebuild the data, then re-render this README from the manifest entry.

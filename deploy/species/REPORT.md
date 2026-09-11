# Every species each pathway source supports, on paintomics.org

Status as of 2026-09-11 13:05 UTC. The KEGG install of the whole organism list is
**in progress** on the Drago VM; this report is the contract, the measurements taken so far,
and how the final numbers are produced. It is updated when the run finishes.

## The universe, and what was decided per source

| Source | Organisms the publisher supports | Installed before | Verdict |
|---|---|---|---|
| KEGG | 11,950 in `rest.kegg.jp/list/genome` (1,330 eukaryotes, 471 archaea, 10,149 bacteria; `br08610` gives the kingdom, `/list/organism` is retired) | 172 | **install all**: 11,772 new (`action=install`), 174 kept |
| Reactome | 16 species in `ReactomePathways.txt`; the species abbreviation equals the KEGG code for all 16 | 15 | 15 kept (bta cel cfa ddi dme dre gga hsa mmu pfa rno sce spo ssc xtr); **mtu excluded**: 13 pathways in one tree, Reactome draws a diagram for the root only (the refresh fetched 1 file), `mtu_resources` builds no Reactome tables; ptr is no longer published by Reactome |
| MapMan | 9 gene-to-bin exports in GoMapMan's `paintomics` folder (CC BY-NC-SA) | ath osa sly sot (+ bvu, wrongly) | ath sly sot(stu) osa kept. **Excluded, each resolved by organism name against KEGG**: `bvu` = *Beta vulgaris* (KEGG bvu is the gut bacterium *Phocaeicola vulgatus*; RefBeet1.1 ids have no bridge to KEGG bvg), `tae` = wheat (KEGG tae is a bacterium; the export is keyed on 2018 TrEMBL accessions of which 1,385 of 69,215 are in KEGG taes's UniProt conversion, 2%), `tca` = cacao (KEGG tca is a beetle; Phytozome Thecc1EG ids have no published bridge to tcc's GeneIDs, KEGG maps only 4,106 tcc genes to UniProt), `nta` (UniGene clusters NCBI retired in 2019), `cam` (not chickpea: Pgl_GLEAN ids) |
| OmniPath | exactly taxids 9606 / 10090 / 10116 | hsa mmu rno | complete; a KEGG/Reactome rebuild keeps `source: "OmniPath"` documents (`preserveForeignPathways`) |
| Ensembl ids | 584 of the 1,330 KEGG eukaryotes have an Ensembl / Ensembl Genomes genebuild publishing an entrez or uniprot dump (registry rebuilt from 69); 666 have no genebuild, 84 publish neither dump | 69 registered | every registered species gets `ensembl_gene/transcript/peptide` linked into the same mate graph as `kegg_id` |

The manifest is `deploy/species/manifest.tsv` (one row per KEGG organism, built by
`build_manifest.py`, every exclusion carries its reason in the `note` column).
`bvu` was rebuilt KEGG-only (113 pathways; its 70 MapMan pathways and 27,421 `mapman_gene_id`
rows are gone; the global `current/mapman` tree is intact at 71 PNG / 70 XML).

## Bugs fixed on the way (this branch)

* `ensembl_census.py registry` resolved **zero** species: KEGG retired `/list/organism` and the
  rebuilt `organisms_all.list` had an empty lineage column. `scripts/kegg_taxonomy.py` parses
  `br08610`; the organism list gets its lineage column back and the registry reads KEGG directly.
* Strain-level KEGG taxids (ang = *A. niger* CBS 513.88) have no Ensembl entry; the registry now falls
  back to the one Ensembl species with that binomial (ang, mgr, mus, pstr recovered).
* `download --kegg=0 --reactome=1` deleted the Reactome crawl it had just made (the copy branch
  rmtree's the staging directory); the Reactome fetch now follows the KEGG staging, pinned by
  `test_reactome_download_order`.
* `bvu_resources` removed; the runner also prunes species directories the installer no longer
  ships (`docker cp` never deletes, so the image's copy kept rebuilding the plant data).
* Installs run as the `paintomics` user: root-owned `/tmp/*.tmp`, `summary.log` and
  `AdminTools/log` from earlier root runs blocked them and are repaired.
* MongoDB `nofile` raised 64,000 → 1,048,576 for one database per species.

## Backups and rollback

`/var/lib/docker/volumes/paintomics_backups/_data/` on the Drago VM, taken 12:00 UTC before any change:
`mongo-all-20260911T120052Z.archive.gz` (2.79 GB, every database) and
`kegg-current-20260911T120005Z.tar.gz` (KEGG_DATA/current).

    # rollback (on the VM)
    sudo docker compose -f ~/paintomics4/deploy/compose.yaml exec -T mongo mongorestore --archive --gzip --drop \
        < /var/lib/docker/volumes/paintomics_backups/_data/mongo-all-20260911T120052Z.archive.gz
    sudo tar -C /var/lib/docker/volumes/paintomics_paintomics-data/_data/KEGG_DATA -xzf \
        /var/lib/docker/volumes/paintomics_backups/_data/kegg-current-20260911T120005Z.tar.gz
    sudo docker compose -f ~/paintomics4/deploy/compose.yaml restart app

## Measured so far

* Baseline before the run: `checkIdentifierMapping.py` on 172 species: **0 errors**, 29 warnings
  (species whose `kegg_gene_symbol` table is empty because KEGG lists no symbols for them).
* Species installed by the runner in its first hour, every identifier table stride-sampled (40 ids)
  through the real mapper into the configured KEGG table (`kegg_id`; entrezgene for mdm's Ensembl route):

| code | KEGG pathways | kegg_id | ncbi_geneid | entrezgene | uniprot_acc | kegg_gene_symbol | ensembl_gene | ensembl_transcript | ensembl_peptide |
|---|---|---|---|---|---|---|---|---|---|
| aaf | 135 | 11656 | 11656 (1.000) | – | 12174 (1.000) | 679 (1.000) | – | – | – |
| aag | 161 | 14651 | 14651 (1.000) | – | 4340 (1.000) | 38 (1.000) | – | – | – |
| aalb | 163 | 26755 | 26755 (1.000) | – | 10711 (1.000) | 37 (1.000) | – | – | – |
| aamb | 106 | 2605 | 0 | – | 2373 (1.000) | 170 (1.000) | – | – | – |
| abi | 98 | 1587 | 0 | – | 1539 (1.000) | 3 (1.000) | – | – | – |
| abri | 115 | 3148 | 0 | – | 2827 (1.000) | 191 (1.000) | – | – | – |
| acf | 95 | 1581 | 0 | – | 0 | 0 | – | – | – |
| acia | 93 | 1557 | 0 | – | 0 | 19 (1.000) | – | – | – |
| acih | 111 | 2901 | 0 | – | 0 | 26 (1.000) | – | – | – |
| acj | 101 | 1695 | 0 | – | 1645 (1.000) | 366 (1.000) | – | – | – |
| afg | 106 | 2657 | 0 | – | 1 (1.000) | 2 (1.000) | – | – | – |
| afu | 108 | 2486 | 0 | – | 2389 (1.000) | 0 | – | – | – |
| agw | 79 | 1373 | 0 | – | 0 | 65 (1.000) | – | – | – |
| aho | 103 | 2424 | 0 | – | 2329 (1.000) | 0 | – | – | – |
| bvu | 113 | 4183 | 0 | – | 3982 (1.000) | 0 | – | – | – |
| mdm | 163 | 41268 | 41268 (1.000) | 20780 (1.000) | 5934 (0.900) | 512 (1.000) | 20890 (0.900) | 20890 (0.925) | 20747 (0.950) |
| mtu | 143 | 4008 | 4008 (1.000) | – | 3876 (1.000) | 2012 (1.000) | – | – | – |

  Every sampled table reaches `kegg_id` at ≥ 0.99, except mdm's Ensembl tables at 0.90–0.95 --
  the fraction of apple genes Ensembl cross-references to EntrezGene, the publisher's ceiling.
  Tables shown without a fraction are the target itself or empty (KEGG lists no symbols for
  aaf/aamb/...; `ncbi_geneid` is absent for organisms KEGG offers no conversion for).
* Chrome, paintomics.org, a species that did not exist before today: **Aedes aegypti (aag)**,
  job `yq02n5137Q`, 300 of 300 uploaded NCBI GeneIDs mapped (100%), Step 3 listed the pathways,
  `aag03082` (ATP-dependent chromatin remodeling) opened and painted 12 matched genes.

## What is running, and how the final numbers are produced

`~/allspecies/allspecies_runner.py` on the VM (README in `~/allspecies/README.md`) walks the
manifest with 8 parallel downloads and one install worker; state per species under
`~/allspecies/state/`, `python3 allspecies_runner.py status` prints progress, an `@reboot` cron
restarts it. KEGG is the only rate-limited source: one KGML per ~3.4 s per worker
(≈ 2.3 requests/s in total), a bacterium takes ~6.5 min to download and 2 s to install, so the
11,772 organisms need roughly six days. Order: the 4 refreshes, then eukaryotes, archaea, bacteria.

When it finishes, `deploy/species/verify-all.sh` on the VM writes, under `~/allspecies/verify-<stamp>/`:
`census.tsv` (Ensembl tables per species), `verify.tsv` (Ensembl gene → kegg_id reach),
`idmapping.log` (`checkIdentifierMapping.py`, must end with 0 errors), `report.tsv`
(`species_report.py`: every species × source × identifier table with stride-sampled reach) and the
served `species.json`. Those files are the per-species × source × id-type rows this report will carry.

## Open gaps, stated as coverage

* 666 KEGG eukaryotes have no Ensembl genebuild at all and 84 more publish neither an entrez nor a
  uniprot dump: they install with KEGG's own identifiers (kegg_id, NCBI GeneID where KEGG offers the
  conversion, UniProt, gene symbol).
* Prokaryotes carry no Ensembl identifiers by design: Ensembl Bacteria names genes by the same
  locus tags KEGG uses as kegg_id.
* Reactome: mtu (see above); ptr dropped by Reactome. MapMan: wheat, cacao, sugar beet, tobacco
  and the mislabelled "cam" export cannot be cross-linked to KEGG (measurements above).
* `nben` (*Nicotiana benthamiana*) is installed but absent from KEGG's current organism list; kept.

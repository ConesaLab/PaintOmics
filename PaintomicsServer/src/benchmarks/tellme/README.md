# tellme benchmark fixtures

Curated reference data for the "tell me about this gene" evaluation harness. Everything here is
hand-checked reference data, not generated output: no file is written by the application at runtime.

**Made:** 2026-09-15

## Files

### `ikaros_targets.json`
Known mouse Ikaros (`Ikzf1`) target genes, 39 symbols, each with an effect
(`repression` / `activation` / `unknown`), a source tag and the PMIDs that support it.

Built by combining:

* **TRRUST v2 mouse** — downloaded from `https://www.grnpedia.org/trrust/data/trrust_rawdata.mouse.tsv`
  (7,057 rows) and filtered to the 17 `Ikzf1` rows, which give 16 distinct symbols: Casz1, Cd4, Cd79a,
  Fgfr4, Igll1 (two rows), Il10, Il2, Il7r, Myc, Nos2, Notch1, Oprd1, Oprk1, Osr1, Prom1, Rag1.
* **Primary literature**, read directly rather than trusted second-hand:
  * Ferreirós-Vidal *et al.* 2013, *Blood* 121(10):1769-82, **PMID 23303821** — abstract only; the paper
    is paywalled (`doi.org/10.1182/blood-2012-08-450114` returns 403, no PMC record). Its abstract names
    no individual target genes, so it contributes no symbols; it is kept as a provenance reference.
  * Ferreirós-Vidal *et al.* 2019, *PLoS Biol*, **PMID 30978178** — full text read via Europe PMC
    (`PMC6481923/fullTextXML`).
  * Schwickert *et al.* 2014, *Nat Immunol*, **PMID 24509509** — full text read via NCBI
    (`efetch db=pmc id=5790181`; the Europe PMC endpoint returns an empty body for this PMCID).
  * Fedl *et al.* 2024, *Nat Immunol*, **PMID 39179932** — abstract.
  * Ma *et al.* 2010, *Mol Cell Biol*, **PMID 20566697** — abstract.

Each target's `note` quotes the sentence that supports it. Symbols are mouse Title case and
deduplicated, with PMIDs merged across sources.

Two places where the recorded effect deliberately departs from TRRUST's mode column, because the cited
paper says the opposite or says more than "Unknown":

* **Myc** — TRRUST row reads `Activation`, but PMID 20566697 states Ikaros and Aiolos *"directly suppress
  c-Myc expression in pre-B cells"*. Recorded as `repression`.
* **Notch1, Oprd1, Osr1** — TRRUST mode `Unknown`; the abstracts establish a direction, so these are
  recorded as `repression`, `activation` and `repression` respectively.

`Il10`, `Il7r`, `Cd79a` and `Rag1` stay `unknown`: the evidence is promoter binding sites, or the paper's
functional focus is another factor.

### `context_labels.json`
Hand labels for 56 papers — `organism`, `system` (free-text noun phrase) and `scope`.

PMIDs came from:

* the two stored example walks in MongoDB (`PaintomicsDB.aiWalkCollection`, `jobID == "Ku5jMVCL6z"`,
  `scope` in `("network", "pathway:mmu04068")`; the `viewJSON` string was `json.loads`-ed and its
  `papers` dict read) — 26 + 10 entries, **36 distinct PMIDs**;
* the 17 PMIDs cited by the TRRUST `Ikzf1` rows;
* the three Ikaros papers above.

For each PMID the abstract was fetched with `efetch.fcgi?db=pubmed&id=<pmid>&retmode=xml` and read;
MeSH headings were consulted only as a cross-check, and every label comes from the title + abstract.
`unknown` is used where the abstract genuinely does not say (3 papers: 7513017, 27427208, 31547393).

Distribution: organism — mouse 23, human 21, mixed 8, unknown 3, other 1 (a *C. elegans* paper);
scope — in vitro 29, mixed 12, review 8, in vivo 7. No paper labelled `clinical` outright: the patient
studies in this set all pair a cohort with cell-line work, so they are labelled `mixed`.

### `context_pairs.json`
20 constructed pairs. Each pair is one biological claim about a mouse B-cell / lymphocyte signalling
gene taken from the example walks, backed by two **real** papers that both genuinely support it:

* `in_context` — mouse, B cells or lymphocytes, in vitro or in vivo;
* `out_of_context` — human patients, human cells, or a non-lymphoid tissue.

Genes covered: Foxo1/Foxo3, Ikzf1, Ets1, Syk, Igll1, Rag1, Il7r, Ccnd3, Notch1, Bcl6, Cish, Socs1, Pten,
Slc7a11, Hes1, Bcl2l11, Ptk2b, Flt3, Myc, Il2. 40 distinct PMIDs, all found by `esearch`, confirmed by
`efetch` against the paper's title, and with the abstract read. The `why` field quotes the supporting
sentence, so a reviewer can check the pairing without refetching.

The card these pairs are scored against is `{"organism": "mouse", "system": "mouse B3 pre-B cell line,
Ikaros induction"}`.

## How the data was fetched

All PubMed access went through NCBI E-utilities with `&email=paintomics4@gmail.com`, rate-limited to one
request per 0.4 s (≤ 3 req/s) with an on-disk response cache. Full text came from Europe PMC
(`/PMC<id>/fullTextXML`) and, where that returned an empty body, from `efetch db=pmc`.

Every file validates with `python -m json.tool`.

## Caveat on PMID 23610374

The brief cited **23610374** for "Genome-wide identification of Ikaros targets…". That PMID is a
different paper — *"Multicenter study of banked third-party virus-specific T cells to treat severe viral
infections after hematopoietic stem cell transplantation"* (Leen *et al.*, Blood 2013). The Ferreirós-Vidal
Ikaros paper is **PMID 23303821**, confirmed by esearch on its title and by `efetch`, and by reference 7
of the 2019 PLoS Biol paper. The correct PMID is used throughout.

Likewise the brief's guess of 21102434 for the Schwickert 2014 pre-B paper was not used: esearch on
`Schwickert[Author] AND Ikaros AND 2014[dp]` resolves to **PMID 24509509**, *"Stage-specific control of
early B cell development by the transcription factor Ikaros"*, Nat Immunol 2014.

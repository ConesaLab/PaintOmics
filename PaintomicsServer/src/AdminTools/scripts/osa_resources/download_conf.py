#**************************************************************************
# Rice (Oryza sativa japonica), MapMan 3.6-era, from GoMapMan.
#
# Browse the tree with: https://gomapman.nib.si/api/GetFolderInfo/
# %7C is the '|' path separator the export API uses.
#
# Two things about rice differ from ath/sly/sot and are the reason this file
# exists rather than being a copy of one of theirs:
#
# 1. GoMapMan publishes gene-to-entrez for ath, sly and stu ONLY. There is no
#    gene-to-entrez_osa.tsv.gz -- the export API answers an unknown path with
#    HTTP 200 and a 9,682-byte Angular shell, so a plain download would store
#    that HTML as the cross-link and the species would install with MapMan
#    bins that link to nothing. The KEGG cross-link is therefore shipped in
#    this directory and declared with "local" instead of a url/file pair.
#    build_gene_to_entrez_osa.py in this directory documents the derivation
#    and regenerates the file byte-for-byte from its primary sources.
#
# 2. Rice has two KEGG organism codes and only one of them can host this data.
#    KEGG `osa` is keyed on NCBI GeneIDs; KEGG `dosa` is keyed on RAP-DB ids
#    and answers HTTP 400 for /conv/dosa/ncbi-geneid (see the comment in
#    DBManager.getSpecieMappingData). processMapManMappingData joins the
#    cross-link's second column through ncbi-geneid2kegg.list, so it can only
#    work under `osa`. Do not move this to dosa_resources.
#
# GoMapMan is CC BY-NC-SA and its species codes are NOT KEGG codes -- `osa`
# happens to mean Oryza sativa on both sides, which is why rice is installable
# while GoMapMan's bvu/tae/tca/cam are not (they name entirely different
# organisms in KEGG).
#**************************************************************************
GOMAPMAN_PAINTOMICS = "https://gomapman.nib.si/api/GetFile/protein_2018-05-25%7Cpaintomics%7C"
GOMAPMAN_METABOLITE = "https://gomapman.nib.si/api/GetFile/metabolite_2018-02-06%7Cmapman%7C"

EXTERNAL_RESOURCES = {
                "ensembl"   :   [
                    {
                    "url"           :   "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/",
                    "species-dir"   :   "oryza_sativa",
                    "division"      :   "plants",
                    "output"        :   "ensembl_mapping.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dump (IRGSP-1.0), EntrezGene rows. Release and filename are resolved at run time."
                    }
                ],
                "ensembl_uniprot"   :   [
                    {
                    "url"           :   "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/",
                    "species-dir"   :   "oryza_sativa",
                    "division"      :   "plants",
                    "xref-type"     :   "uniprot",
                    "xref-db"       :   ["Uniprot/SWISSPROT", "Uniprot/SPTREMBL"],
                    "output"        :   "ensembl_uniprot.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dump (IRGSP-1.0), UniProt rows. Links the Ensembl identifiers to KEGG through the accessions KEGG maps itself (processEnsemblUniProtData)."
                    }
                ],
                "mapman_kegg"   :   [
                    {
                    # Path is relative to ROOT_DIR (src/AdminTools/), the same
                    # convention "manifest" uses below. download_others.py
                    # resolves it to an absolute path before the call.
                    "local"         :   "scripts/osa_resources/gene-to-entrez_osa.tsv.gz",
                    "output"        :   "gene-to-entrez_osa.list",
                    "decompress"    :   True,
                    "expect"        :   "tsv",
                    "description"   :   "MSU v7 locus ID -> NCBI Entrez gene ID, derived from RAP-DB's RAP-MSU correspondence table joined to NCBI GeneIDs via the OSNPB_ locus tag. Shipped here because GoMapMan has no rice gene-to-entrez export. 25,466 pairs reaching 76.4% of KEGG osa genes (live comparison: ath 82.5%, sly 61.0%, sot 18.8%)."
                    }
                ],
                "mapman_gene"   :   [
                    {
                    "url"           :   GOMAPMAN_PAINTOMICS,
                    "file"          :   "gene-to-mapman_osa.tsv.gz",
                    "output"        :   "gene-to-mapman_osa.list",
                    "decompress"    :   True,
                    "expect"        :   "tsv",
                    "description"   :   "Source: GoMapMan Paintomics export. MSU v7 gene ID -> MapMan ontology bins (55,986 genes, all tagged MSU_v7)."
                    }
                ],
                "mapman_extra_pathways"   :   [
                    {
                    # GoMapMan ships 20 diagrams, nearly all Secondary Metabolism
                    # and Hormones. This manifest adds the 50 general 3.6-era maps
                    # from the MapManStore archive - Metabolism overview, glycolysis,
                    # TCA, photosynthesis, transcription, Metabolites and the rest.
                    # Delete this entry to install the base 20 only.
                    "manifest"      :   "scripts/common_resources/mapman_extra_diagrams.json",
                    "description"   :   "Source: MapManStore pathway archive (plabipd.de). 3.6-era diagrams only; X4 diagrams use a renumbered ontology and are deliberately excluded."
                    }
                ],
                "mapman_pathways"   :   [
                    {
                    "url"           :   GOMAPMAN_PAINTOMICS,
                    "file"          :   "mapman_pathways.tar.gz",
                    "output"        :   "mapman_pathways.tar.gz",
                    "expect"        :   "targz",
                    "description"   :   "Source: GoMapMan Paintomics export. 20 MapMan diagrams as xml/ layouts + png/ backgrounds."
                    }
                ],
                "mapman_classification"   :   [
                    {
                    "url"           :   GOMAPMAN_PAINTOMICS,
                    "file"          :   "mapman_classification.txt",
                    "output"        :   "mapman_classification.txt",
                    "expect"        :   "tsv",
                    "description"   :   "Source: GoMapMan Paintomics export. Primary/secondary category per diagram."
                    }
                ],
                "metabolites"	:	[
                    {
                    "url"           :   GOMAPMAN_METABOLITE,
                    "file"          :   "plant_plant metabolites_2018-02-06_mapping.txt",
                    "output"        :   "mapman_metabolites.txt",
                    # The metabolite export carries a BINCODE/NAME/... header row.
                    # Left in place it becomes a compound literally named "NAME".
                    "skip_header"   :   True,
                    "expect"        :   "tsv",
                    "description"   :   "Source: GoMapMan Metabolite export (MapMan mapping format). Bin -> metabolite names."
                    }
                    ]
}

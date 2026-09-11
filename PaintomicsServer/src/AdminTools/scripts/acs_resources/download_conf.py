EXTERNAL_RESOURCES = {
                "ensembl"   :   [
                    {
                    "url"           :   "https://ftp.ensembl.org/pub/",
                    "species-dir"   :   "anolis_carolinensis",
                    "division"      :   "vertebrates",
                    "output"        :   "ensembl_mapping.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dumps. BioMart was retired (martservice answers HTTP 405), so the release/assembly and filename are resolved at run time rather than pinned here."
                    }
                ],
                # The UniProt cross-reference dump used to sit as a second entry
                # under "ensembl", where nothing read it: build_database.py then
                # called processUniProtData(), which wants a "uniprot" id-mapping
                # resource this file never declared, and the build died on
                # `EXTERNAL_RESOURCES.get("uniprot")[0]` (paintomics.org,
                # 2026-09-11). It is the Ensembl UniProt route every other
                # species spells "ensembl_uniprot", read by processEnsemblUniProtData.
                "ensembl_uniprot"   :   [
                    {
                    "url"           :   "https://ftp.ensembl.org/pub/",
                    "species-dir"   :   "anolis_carolinensis",
                    "division"      :   "vertebrates",
                    "xref-type"     :   "uniprot",
                    "xref-db"       :   ["Uniprot/SWISSPROT", "Uniprot/SPTREMBL", "UniProtKB_all"],
                    "output"        :   "ensembl_uniprot.list",
                    "description"   :   "Source: Ensembl UniProt cross-reference TSV dumps. Links the Ensembl identifiers to KEGG through the accessions KEGG maps itself (processEnsemblUniProtData)."
                    }
                ],
                "refseq"   :  [
                    {
                    "url"           :   "ftp://ftp.ncbi.nih.gov/gene/DATA/",
                    "file"          :   "gene2refseq.gz",
                    "output"        :   "refseq_gene2refseq.gz",
                    "description"   :   "Source: NCBI Gene. Downloaded from NCBI FTP. Tab-delimited one line per genomic/RNA/protein set of RefSeqs",
                    "specie-code"   :   28377
                    },{
                    "url"           :   "ftp://ftp.ncbi.nih.gov/gene/DATA/GENE_INFO/Non-mammalian_vertebrates/",
                    "file"          :   "All_Non-mammalian_vertebrates.gene_info.gz",
                    "output"        :   "refseq_gene2genesymbol.gz",
                    "description"   :   "Source: NCBI Gene. Downloaded from NCBI FTP. Tab-delimited one line per gene id/gene symbol/.../synonyms/... from RefSeqs",
                    "specie-code"   :   28377
                    }
                ]
        }

EXTERNAL_RESOURCES = {
                "ensembl"   :   [
                    {
                    "url"           :   "https://ftp.ensembl.org/pub/",
                    "species-dir"   :   "danio_rerio",
                    "division"      :   "vertebrates",
                    "output"        :   "ensembl_mapping.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dumps. BioMart was retired (martservice answers HTTP 405), so the release/assembly and filename are resolved at run time rather than pinned here."
                    }
                ],
                "ensembl_uniprot"   :   [
                    {
                    "url"           :   "https://ftp.ensembl.org/pub/",
                    "species-dir"   :   "danio_rerio",
                    "division"      :   "vertebrates",
                    "xref-type"     :   "uniprot",
                    "xref-db"       :   ["Uniprot/SWISSPROT", "Uniprot/SPTREMBL", "UniProtKB_all"],
                    "output"        :   "ensembl_uniprot.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dump (GRCz11), UniProt rows. Links the Ensembl identifiers to KEGG through the accessions KEGG maps itself (processEnsemblUniProtData)."
                    }
                ],
                "refseq"   :  [
                    {
                    "url"           :   "ftp://ftp.ncbi.nih.gov/gene/DATA/",
                    "file"          :   "gene2refseq.gz",
                    "output"        :   "refseq_gene2refseq.gz",
                    "description"   :   "Source: NCBI Gene. Downloaded from NCBI FTP. Tab-delimited one line per genomic/RNA/protein set of RefSeqs",
                    "specie-code"   :   7955
                    },{
                    "url"           :   "ftp://ftp.ncbi.nih.gov/gene/DATA/GENE_INFO/Non-mammalian_vertebrates/",
                    "file"          :   "Danio_rerio.gene_info.gz",
                    "output"        :   "refseq_gene2genesymbol.gz",
                    "description"   :   "Source: NCBI Gene. Downloaded from NCBI FTP. Tab-delimited one line per gene id/gene symbol/.../synonyms/... from RefSeqs",
                    "specie-code"   :   7955
                    }
                ],
                 "uniprot"   :   [
                    {
                    "url"           :   "ftp://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/idmapping/by_organism/",
                    "file"          :   "DANRE_7955_idmapping_selected.tab.gz",
                    "output"        :   "uniprot_mapping.list",
                    "description"   :    "Source: UniProt idmapping_selected.tab. Downloaded from UniProt FTP. Tab-delimited table which includes the multiple mappings between UniProt Accession and external databases."
                    }
                ]
        }

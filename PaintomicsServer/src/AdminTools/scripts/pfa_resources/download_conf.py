EXTERNAL_RESOURCES = {
                "ensembl"   :   [
                    {
                    "url"           :   "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/",
                    "species-dir"   :   "plasmodium_falciparum",
                    "division"      :   "protists",
                    "output"        :   "ensembl_mapping.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dump (GCA000002765v3), EntrezGene rows. Release and filename are resolved at run time."
                    }
                ],
                "ensembl_uniprot"   :   [
                    {
                    "url"           :   "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/",
                    "species-dir"   :   "plasmodium_falciparum",
                    "division"      :   "protists",
                    "xref-type"     :   "uniprot",
                    "xref-db"       :   ["Uniprot/SWISSPROT", "Uniprot/SPTREMBL"],
                    "output"        :   "ensembl_uniprot.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dump (GCA000002765v3), UniProt rows. Links the Ensembl identifiers to KEGG through the accessions KEGG maps itself (processEnsemblUniProtData)."
                    }
                ],
                "refseq"   :  [
                    {
                    "url"           :   "ftp://ftp.ncbi.nih.gov/gene/DATA/",
                    "file"          :   "gene2refseq.gz",
                    "output"        :   "refseq_gene2refseq.gz",
                    "description"   :   "Source: NCBI Gene. Downloaded from NCBI FTP. Tab-delimited one line per genomic/RNA/protein set of RefSeqs",
                    # NCBI keys P. falciparum rows by strain: 36329 (3D7). The species
                    # taxid 5833 matches only 4 rows of gene_info.
                    "specie-code"   :   36329
                    },{
                    "url"           :   "ftp://ftp.ncbi.nih.gov/gene/DATA/GENE_INFO/Protozoa/",
                    "file"          :   "Plasmodium_falciparum.gene_info.gz",
                    "output"        :   "refseq_gene2genesymbol.gz",
                    "description"   :   "Source: NCBI Gene. Downloaded from NCBI FTP. Tab-delimited one line per gene id/gene symbol/.../synonyms/... from RefSeqs",
                    "specie-code"   :   36329
                    }
                ]
        }

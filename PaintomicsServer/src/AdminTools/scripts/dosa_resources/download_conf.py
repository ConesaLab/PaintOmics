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
                    "xref-db"       :   ["Uniprot/SWISSPROT", "Uniprot/SPTREMBL", "UniProtKB_all"],
                    "output"        :   "ensembl_uniprot.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dump (IRGSP-1.0), UniProt rows. Links the Ensembl identifiers to KEGG through the accessions KEGG maps itself (processEnsemblUniProtData)."
                    }
                ]
        }

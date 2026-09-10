EXTERNAL_RESOURCES = {
                "ensembl"   :   [
                    {
                    "url"           :   "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/",
                    "species-dir"   :   "schizosaccharomyces_pombe",
                    "division"      :   "fungi",
                    "output"        :   "ensembl_mapping.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dump (ASM294v2), EntrezGene rows. Release and filename are resolved at run time."
                    }
                ],
                "ensembl_uniprot"   :   [
                    {
                    "url"           :   "https://ftp.ebi.ac.uk/ensemblgenomes/pub/current/",
                    "species-dir"   :   "schizosaccharomyces_pombe",
                    "division"      :   "fungi",
                    "xref-type"     :   "uniprot",
                    "xref-db"       :   ["Uniprot/SWISSPROT", "Uniprot/SPTREMBL"],
                    "output"        :   "ensembl_uniprot.list",
                    "description"   :   "Source: Ensembl cross-reference TSV dump (ASM294v2), UniProt rows. Links the Ensembl identifiers to KEGG through the accessions KEGG maps itself (processEnsemblUniProtData)."
                    }
                ]
        }

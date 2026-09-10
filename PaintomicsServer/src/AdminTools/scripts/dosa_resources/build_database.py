import imp
import traceback

from sys import argv, stderr
from subprocess import CalledProcessError

#**************************************************************************
# RAP-DB rice. KEGG keys `dosa` by RAP-DB locus (Os01g0100100), which is also
# the Ensembl Plants gene id for IRGSP-1.0, so the Ensembl dump maps a user's
# RAP-DB, transcript, peptide and NCBI GeneID onto kegg_id directly.
#
# This file used to carry its own copy of processEnsemblData that read the
# mapping file as COMMA-separated with gene, peptide, transcript columns. The
# downloader writes gene, entrez, peptide, transcript TAB-separated, so every
# row failed and dosa installed with three empty Ensembl tables (measured on
# paintomics.org, 2026-09-10). The shared parser reads what the shared
# downloader writes; there is nothing rice-specific to parse.
#**************************************************************************
SPECIE      = argv[1]
ROOT_DIR    = argv[2].rstrip("/") + "/"      #Should be src/AdminTools
DATA_DIR    = argv[3].rstrip("/") + "/"
LOG_FILE    = argv[4]

COMMON_BUILD_DB_TOOLS = imp.load_source('common_build_database', ROOT_DIR + "scripts/common_build_database.py")
COMMON_BUILD_DB_TOOLS.SPECIE= SPECIE
COMMON_BUILD_DB_TOOLS.DATA_DIR= DATA_DIR
COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES = imp.load_source('download_conf',  ROOT_DIR + "scripts/" + SPECIE + "_resources/download_conf.py").EXTERNAL_RESOURCES

try:
    #**************************************************************************
    # STEP 1. EXTRACT THE MAPPING DATABASE
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.processEnsemblData()
    COMMON_BUILD_DB_TOOLS.processKEGGMappingData()
    COMMON_BUILD_DB_TOOLS.processEnsemblUniProtData()

    #**************************************************************************
    # STEP 2. PROCESS THE KEGG DATABASE
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.processKEGGPathwaysData()
    #**************************************************************************
    # DUMP AND INSTALL
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.dumpDatabase()
    COMMON_BUILD_DB_TOOLS.createDatabase()

except CalledProcessError as ex:
    stderr.write("FAILED WHILE PROCESSING DATA " + str(ex))
    traceback.print_exc(file=stderr)
    exit(1)
except Exception as ex:
    stderr.write("FAILED WHILE PROCESSING DATA " + str(ex))
    traceback.print_exc(file=stderr)
    exit(1)

exit(0)

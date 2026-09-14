import imp
import traceback

from sys import argv, stderr
from subprocess import CalledProcessError


SPECIE      = argv[1]
ROOT_DIR    = argv[2].rstrip("/") + "/"      #Should be src/AdminTools
DATA_DIR    = argv[3].rstrip("/") + "/"
LOG_FILE    = argv[4]


COMMON_BUILD_DB_TOOLS = imp.load_source('common_build_database', ROOT_DIR + "scripts/common_build_database.py")
COMMON_BUILD_DB_TOOLS.SPECIE= SPECIE
COMMON_BUILD_DB_TOOLS.DATA_DIR= DATA_DIR
COMMON_BUILD_DB_TOOLS.ROOT_DIR= ROOT_DIR

COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES = imp.load_source('download_conf',  ROOT_DIR + "scripts/" + SPECIE + "_resources/download_conf.py").EXTERNAL_RESOURCES
COMMON_BUILD_DB_TOOLS.COMMON_RESOURCES = imp.load_source('download_conf',  ROOT_DIR + "scripts/common_resources/download_conf.py").EXTERNAL_RESOURCES
COMMON_BUILD_DB_TOOLS.SERVER_SETTINGS = imp.load_source('serverconf.py',  ROOT_DIR + "../conf/serverconf.py")


#**************************************************************************
# CHANGE THE CODE FROM HERE
#
# STEP 2. INSTALL FILES
#**************************************************************************
try:
    #**************************************************************************
    # STEP 1. EXTRACT THE MAPPING DATABASE
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.processEnsemblData()
    COMMON_BUILD_DB_TOOLS.processRefSeqData()
    COMMON_BUILD_DB_TOOLS.processUniProtData()
    COMMON_BUILD_DB_TOOLS.processRefSeqGeneSymbolData()
    # KEGG keys C. elegans on its own identifier space (CELE_C17G1.7), which
    # no other source here produces: with this call commented out the species
    # had ensembl/refseq/uniprot tables and nothing that matched a single one of
    # its KEGG pathway genes, so every worm job mapped 0 features and reported
    # success. This builds kegg_id (plus ncbi_geneid, which bridges it to the
    # Ensembl-side identifiers users actually upload).
    COMMON_BUILD_DB_TOOLS.processKEGGMappingData()


    #**************************************************************************
    # STEP 2. PROCESS THE KEGG And Reactome DATABASE
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.processKEGGPathwaysData()
    COMMON_BUILD_DB_TOOLS.processReactomePathwaysData()
    COMMON_BUILD_DB_TOOLS.mergeNetworkFiles()

    #**************************************************************************
    # STEP 3. Print Result
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.printResults()

    #**************************************************************************
    # DUMP AND INSTALL
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.dumpDatabase()
    COMMON_BUILD_DB_TOOLS.dumpErrors()
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

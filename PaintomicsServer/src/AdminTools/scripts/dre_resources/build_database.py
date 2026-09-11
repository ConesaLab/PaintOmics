import imp
import traceback
from sys import argv, stderr
from subprocess import CalledProcessError

# Read from the arguments DBManager passes, like every other species. The
# /home/tian/... constants that stood here exist only on paintomics.uv.es.
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
    # COMMON_BUILD_DB_TOOLS.processVegaData()
    # Zebrafish never received mapping/ensembl_mapping.list, so the Ensembl,
    # RefSeq and UniProt steps above all produced nothing and dre shipped with
    # no gene identifier table at all -- resolveDatabaseIds then raised on the
    # configured `entrezgene` and every dre job failed at step 1. The KEGG
    # mapping files ARE present, and this call turns them into kegg_id /
    # ncbi_geneid / uniprot_acc / kegg_gene_symbol.
    COMMON_BUILD_DB_TOOLS.processKEGGMappingData()
    COMMON_BUILD_DB_TOOLS.processEnsemblUniProtData()


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

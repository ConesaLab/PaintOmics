import imp
import traceback
from sys import argv, stderr
from subprocess import CalledProcessError
#**************************************************************************
#STEP 1. READ CONFIGURATION AND PARSE INPUT FILES
#
# DO NOT CHANGE THIS CODE
#**************************************************************************
#SPECIE = "smu"
#ROOT_DIR = '/home/tian/paintomics/paintomics4/PaintomicsServer/src/AdminTools/'
#DATA_DIR = '/home/tian/database/KEGG_DATA/current/' + SPECIE + '/'
#LOG_FILE = "/home/tian/database/KEGG_DATA/current/install.log"


SPECIE      = argv[1]
ROOT_DIR    = argv[2].rstrip("/") + "/"      #Should be src/AdminTools
DATA_DIR    = argv[3].rstrip("/") + "/"
LOG_FILE    = argv[4]

COMMON_BUILD_DB_TOOLS = imp.load_source('common_build_database', ROOT_DIR + "scripts/common_build_database.py")
COMMON_BUILD_DB_TOOLS.SPECIE= SPECIE
COMMON_BUILD_DB_TOOLS.ROOT_DIR= ROOT_DIR
COMMON_BUILD_DB_TOOLS.DATA_DIR= DATA_DIR

COMMON_BUILD_DB_TOOLS.COMMON_RESOURCES = imp.load_source('download_conf',  ROOT_DIR + "scripts/common_resources/download_conf.py").EXTERNAL_RESOURCES
COMMON_BUILD_DB_TOOLS.SERVER_SETTINGS = imp.load_source('serverconf.py',  ROOT_DIR + "../conf/serverconf.py")
# The Ensembl dumps default/download_others.py fetched for this species, if
# its organism has a registered genebuild (scripts/common_resources/
# ensembl_genebuilds.json). {} for every other species, and every processor
# below that reads them is a no-op on {}.
COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES = COMMON_BUILD_DB_TOOLS.ensemblResourcesFor(SPECIE)

#**************************************************************************
# CHANGE THE CODE FROM HERE
#
# STEP 2. INSTALL FILES
#**************************************************************************
try:
    #**************************************************************************
    # STEP 1. EXTRACT THE MAPPING DATABASE
    #
    # Ensembl first: it creates the transcript groups. KEGG next: it creates
    # kegg_id and the uniprot_acc rows. The Ensembl-UniProt pass last: it can
    # only join groups that already exist.
    #**************************************************************************
    if COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES.get("ensembl"):
        COMMON_BUILD_DB_TOOLS.processEnsemblData()
    COMMON_BUILD_DB_TOOLS.processKEGGMappingData()
    COMMON_BUILD_DB_TOOLS.processEnsemblUniProtData()
    #**************************************************************************
    # STEP 2. PROCESS THE KEGG DATABASE
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.processKEGGPathwaysData()
    COMMON_BUILD_DB_TOOLS.mergeNetworkFiles()

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


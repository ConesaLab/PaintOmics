import imp
import traceback

from sys import argv, stderr
from subprocess import CalledProcessError
#**************************************************************************
#STEP 1. READ CONFIGURATION AND PARSE INPUT FILES
#
# DO NOT CHANGE THIS CODE
#**************************************************************************
#SPECIE = "osa"
#ROOT_DIR = '/home/tian/paintomics/paintomics4/PaintomicsServer/src/AdminTools/'
#DATA_DIR = '/home/tian/database/KEGG_DATA/current/osa/'
#LOG_FILE = "/home/tian/database/KEGG_DATA/current/install.log"


SPECIE      = argv[1]
ROOT_DIR    = argv[2].rstrip("/") + "/"      #Should be src/AdminTools
DATA_DIR    = argv[3].rstrip("/") + "/"
LOG_FILE    = argv[4]

COMMON_BUILD_DB_TOOLS = imp.load_source('common_build_database', ROOT_DIR + "scripts/common_build_database.py")
COMMON_BUILD_DB_TOOLS.SPECIE= SPECIE
COMMON_BUILD_DB_TOOLS.ROOT_DIR= ROOT_DIR
COMMON_BUILD_DB_TOOLS.DATA_DIR= DATA_DIR
COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES = imp.load_source('download_conf',  ROOT_DIR + "scripts/" + SPECIE + "_resources/download_conf.py").EXTERNAL_RESOURCES
COMMON_BUILD_DB_TOOLS.COMMON_RESOURCES = imp.load_source('download_conf',  ROOT_DIR + "scripts/common_resources/download_conf.py").EXTERNAL_RESOURCES
COMMON_BUILD_DB_TOOLS.SERVER_SETTINGS = imp.load_source('serverconf.py',  ROOT_DIR + "../conf/serverconf.py")

#**************************************************************************
# CHANGE THE CODE FROM HERE
#
# STEP 2. INSTALL FILES
#
# This is default/build_database.py -- which is what osa used before it had a
# resources directory -- plus the two MapMan steps. Keeping the default's
# calls matters: adding <specie>_resources/build_database.py *overrides* the
# default rather than extending it (DBManager.py:1556-1575), so anything
# dropped here is silently lost from the species' KEGG install.
#
# mergeNetworkFiles() is the one default call deliberately NOT carried over.
# It folds every pathways_network_<DB>.json into pathways_network.json as
# {"KEGG": ..., "MapMan": ...}, but the client fetches one flat file per
# database (JobController.js:802-836 branches on jobView.database and hands
# the whole payload to generateNetwork). For a KEGG-only species it is a
# no-op because no pathways_network_<DB>.json exists, which is why osa was
# unaffected by it before; once MapMan is installed it would reshape the KEGG
# network file. sly, sot and bvu all omit it for this reason.
#**************************************************************************
try:
    #**************************************************************************
    # STEP 1. EXTRACT THE MAPPING DATABASE
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.processEnsemblData()
    COMMON_BUILD_DB_TOOLS.processMapManMappingData()
    COMMON_BUILD_DB_TOOLS.processKEGGMappingData()
    COMMON_BUILD_DB_TOOLS.processEnsemblUniProtData()
    #**************************************************************************
    # STEP 2. PROCESS THE KEGG DATABASE
    #**************************************************************************
    COMMON_BUILD_DB_TOOLS.processKEGGPathwaysData()

    # This must be after KEGG to avoid trying to process missing kgml files
    # (will not fail though)
    COMMON_BUILD_DB_TOOLS.processMapManPathwaysData()

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

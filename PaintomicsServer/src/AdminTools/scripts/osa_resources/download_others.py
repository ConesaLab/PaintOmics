#!/usr/bin/env python

import traceback
from sys import argv, stderr
import imp
#**************************************************************************
#STEP 1. READ CONFIGURATION AND PARSE INPUT FILES
#
# DO NOT CHANGE THIS CODE
#**************************************************************************
SPECIE      = argv[1]
ROOT_DIR    = argv[2].rstrip("/") + "/"      #Should be src/AdminTools
DESTINATION = argv[3].rstrip("/") + "/"

COMMON_BUILD_DB_TOOLS = imp.load_source('common_build_database', ROOT_DIR + "scripts/common_build_database.py")
COMMON_BUILD_DB_TOOLS.SPECIE= SPECIE
COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES = imp.load_source('download_conf',  ROOT_DIR + "scripts/" + SPECIE + "_resources/download_conf.py").EXTERNAL_RESOURCES
COMMON_BUILD_DB_TOOLS.COMMON_RESOURCES = imp.load_source('download_conf',  ROOT_DIR + "scripts/common_resources/download_conf.py").EXTERNAL_RESOURCES

SERVER_SETTINGS = imp.load_source('serverconf.py',  ROOT_DIR + "../conf/serverconf.py")


#**************************************************************************
# CHANGE THE CODE FROM HERE
#
# STEP 2. DOWNLOAD FILES
#**************************************************************************
try:
    COMMON_BUILD_DB_TOOLS.downloadEnsemblResources(COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES, DESTINATION,
                                                   SERVER_SETTINGS.DOWNLOAD_DELAY_1, SERVER_SETTINGS.MAX_TRIES_1)
    stderr.write( "STEP DOWNLOAD MAPMAN" + "\n")
    #**************************************************************************
    #GET THE MapMan INPUTS
    #
    # All but one come from GoMapMan over HTTPS (see download_conf.py).
    # "mapman_kegg" is rice's exception: GoMapMan has no gene-to-entrez export
    # for osa, so that resource is declared with "local" and ships in this
    # directory. Its path is relative to ROOT_DIR, which only this script
    # knows, so resolve it here rather than teaching the common helper where
    # the source tree lives.
    #**************************************************************************
    for resourceName in ("mapman_kegg", "mapman_gene", "mapman_pathways",
                         "mapman_classification", "metabolites"):
        resources = COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES.get(resourceName)

        if not resources:
            if resourceName == "mapman_kegg":
                stderr.write("No " + resourceName + " resource declared for " + SPECIE +
                             "; MapMan genes will not be cross-linked to KEGG.\n")
                continue
            raise Exception("Missing required MapMan resource: " + resourceName)

        resource = resources[0]

        if resource.get("local"):
            resource = dict(resource, local=ROOT_DIR + resource.get("local"))

        COMMON_BUILD_DB_TOOLS.downloadMapManResource(
            resource,
            DESTINATION + resource.get("output"),
            SERVER_SETTINGS.DOWNLOAD_DELAY_1,
            SERVER_SETTINGS.MAX_TRIES_1)

    #**************************************************************************
    # Fold in the MapMan diagrams GoMapMan's export omits. Best effort and
    # all-or-nothing: if the store cannot be reached the base 20 still install.
    #**************************************************************************
    extraPathways = COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES.get("mapman_extra_pathways")

    if extraPathways:
        pathwaysResource = COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES.get("mapman_pathways")[0]
        classificationResource = COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES.get("mapman_classification")[0]

        COMMON_BUILD_DB_TOOLS.augmentMapManPathways(
            DESTINATION + pathwaysResource.get("output"),
            DESTINATION + classificationResource.get("output"),
            ROOT_DIR + extraPathways[0].get("manifest"),
            SERVER_SETTINGS.DOWNLOAD_DELAY_1,
            SERVER_SETTINGS.MAX_TRIES_1)


except Exception as ex:
    stderr.write("FAILED WHILE DOWNLOADING DATA " + str(ex))
    traceback.print_exc(file=stderr)
    exit(1)

exit(0)

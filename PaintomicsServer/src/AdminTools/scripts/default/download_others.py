#!/usr/bin/env python
"""External mapping data for a species that has no <code>_resources/ of its own.

DBManager runs this when scripts/<code>_resources/download_others.py does not
exist. Until 2026-09 there was no default at all, so such a species got the
KEGG conversion lists and nothing else: no Ensembl gene, transcript or peptide
identifiers, whatever Ensembl published for it. The genebuild registry
(scripts/common_resources/ensembl_genebuilds.json) now says which of those
species Ensembl covers and where its cross-reference dumps live; a species that
is not in it still gets exactly what it got before.

Arguments, as for every download_others.py:  <code> <src/AdminTools/> <mapping dir>
"""
import traceback
from sys import argv, stderr
import imp

SPECIE      = argv[1]
ROOT_DIR    = argv[2].rstrip("/") + "/"      #Should be src/AdminTools
DESTINATION = argv[3].rstrip("/") + "/"

COMMON_BUILD_DB_TOOLS = imp.load_source('common_build_database', ROOT_DIR + "scripts/common_build_database.py")
COMMON_BUILD_DB_TOOLS.SPECIE = SPECIE
SERVER_SETTINGS = imp.load_source('serverconf.py', ROOT_DIR + "../conf/serverconf.py")

try:
    resources = COMMON_BUILD_DB_TOOLS.ensemblResourcesFor(SPECIE)
    COMMON_BUILD_DB_TOOLS.EXTERNAL_RESOURCES = resources

    if not resources:
        stderr.write("No Ensembl genebuild is registered for " + SPECIE +
                     " (scripts/common_resources/ensembl_genebuilds.json); KEGG mapping only.\n")
    COMMON_BUILD_DB_TOOLS.downloadEnsemblResources(resources, DESTINATION,
                                                   SERVER_SETTINGS.DOWNLOAD_DELAY_1, SERVER_SETTINGS.MAX_TRIES_1)

except Exception as ex:
    stderr.write("FAILED WHILE DOWNLOADING DATA " + str(ex))
    traceback.print_exc(file=stderr)
    exit(1)

exit(0)

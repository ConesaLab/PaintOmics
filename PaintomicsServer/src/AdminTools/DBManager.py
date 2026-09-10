#! /usr/bin/env python
import sys
import os
# sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)) + "/../../")

# Ensure this script always runs inside the expected virtualenv so required packages are available.
# Disabled for local deployment - using conda env instead.
# VENV_DIR = "/home/tian/paintomics/paintomics_env"
# VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python3")
# if os.path.exists(VENV_PYTHON) and sys.executable != VENV_PYTHON:
#     os.environ["VIRTUAL_ENV"] = VENV_DIR
#     os.environ["PATH"] = os.path.join(VENV_DIR, "bin") + os.pathsep + os.environ.get("PATH", "")
#     os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)

import datetime, traceback, shutil, inspect, tempfile
import json
import logging
import logging.config
import requests
import gzip
from io import BytesIO
from PIL import Image
from textwrap import wrap
from time import strftime, sleep, time
from subprocess import check_call, CalledProcessError

from conf.serverconf import KEGG_DATA_DIR, DOWNLOAD_DELAY_1, DOWNLOAD_DELAY_2, MAX_TRIES_1

VERSION = 0.12

# Degradations that did not stop an install, collected so the run ends with one
# readable list instead of leaving them scattered through a multi-hour log.
INSTALL_WARNINGS = []


# ------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------
# ---  MAIN FUNCTIONS                                                                   ----
# ------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------

def download_command(inputfile=None, specie=None, kegg=0, mapping=0, common=0, retry=0, reactome=0):
    """
    Download the information for given species
    Usage: AdminTools.py download <options>
    Examples:
              ./DBManager.py download --specie=mmu --kegg=1 --mapping=1 --common=0 --reactome=0 --updateReactome=0

    Keyword arguments:
        from_file -- a file containing a list of a list of species IDs (one per line), followed by (tabulated)
                            + [0,1]: download Kegg data for the specie
                            + [0,1]: download Mapping data,  where [0 = FALSE,1 = TRUE]
                            default ""
                            e.g.
                                mmu   1   0
                                hsa   1   1
                                ...
        specie    -- a valid KEGG specie code e.g. mmu, hsa

        kegg      -- (optional) download the KEGG data for the given specie. Default=0
        mapping   -- (optional) download the Mapping data for the given specie. Default=0
        common    -- (optional) 1 if Pathways info (classification, PNG images...) should be downloaded, 0 to keep from previous version. Default=0
        retry     -- (optional) 1 to retry the installation of ERRONEOUS SPECIES from previous version, 0 to ignore them. Default=0
    """
    if inputfile is None and specie is None:
        print("Organisms not specified, please type ./DBManager.py download -h for help")
        exit(-1)

    # **************************************************************************
    # STEP 1. READ CONFIGURATION AND PARSE INPUT FILES
    # **************************************************************************
    readConfigurationFile()
    download_dir = KEGG_DATA_DIR + "download/"
    downloadLog = download_dir + "download.log"
    currentStep = 0
    SPECIES_DOWNLOAD = None
    DOWNLOADED_SPECIES = []
    FAILED_SPECIES = []

    # Create the log files.
    #
    # A fresh deployment has an empty KEGG_DATA volume, so this directory does
    # not exist yet. The previous code shelled out to `touch`, whose failure
    # os.system() discards, and the error only surfaced on the next line as
    #   FileNotFoundError: '/data/KEGG_DATA/download/summary.log'
    # which says nothing about the missing parent directory.
    os.makedirs(download_dir, exist_ok=True)
    open(downloadLog, 'a').close()
    summary = open(download_dir + 'summary.log', 'w')

    if inputfile is None:
        n = 3  # Download mapping and kegg
        if mapping == 0:
            n -= 1  # Do not download mapping
        if kegg == 0:
            n -= 2  # Do not download kegg
        SPECIES_DOWNLOAD = {specie: n}  # THE IDS FOR THE SPECIES TO UPDATE
    # Check install options
    else:
        SPECIES_DOWNLOAD = readFile(inputfile)  # THE IDS FOR THE SPECIES TO UPDATE

    log("######################################################################")
    log("### PAINTOMICS AI - DATABASE DOWNLOADER ")
    log("### v." + str(VERSION))
    log("######################################################################")
    log("")
    log("Download log is at: " + downloadLog)
    log("")
    log("STEP " + str(currentStep) + ". READ CONFIGURATION AND PARSE INPUT FILES...")
    log("       - " + str(len(SPECIES_DOWNLOAD.keys())) + " new organisms will be downloaded.")
    log("")

    if ((common == None and confirm(
            prompt='Download common KEGG information (pathway names, classifications, PNG images,...)?',
            resp=False)) or (common == "1")):
        common = True

    # ********************************************************************************
    # STEP 2. IF WE CHOSE TO DOWNLOAD THE GENERAL DATA (PATHWAYS CLASSIFICATION, ETC.) -> GO TO 2.A
    #        OTHERWISE --> GO TO 2.B
    # ********************************************************************************
    currentStep += 1

    # STEP 2.A Download common data
    if common:
        datadir = os.path.join(download_dir, "common/")
        try:
            # STEP 2.A.0 INITIALIZE THE NEW DIRECTORY
            if os.path.isdir(datadir):
                shutil.rmtree(datadir)
            os.mkdir(datadir)
            # Add the flag file "DOWNLOADING"
            version = open(datadir + "DOWNLOADING", 'w')
            version.write("# DOWNLOAD STARTS:" + strftime("%Y%m%d %H%M"))
            version.close()
            log('')
            log("New data will be stored at " + datadir)

            if reactome:

                log("STEP " + str(currentStep) + " Extra. DOWNLOAD THE COMMON REACTOME INFORMATION")

                reactomeURL = "https://reactome.org/download/current/"
                files_to_download = [
                    ("ReactomePathwaysRelation.txt", "ReactomePathwaysRelation.list"),
                    ("UniProt2Reactome_PE_All_Levels.txt", "UniProt2Reactome_PE_All_Levels.txt"),
                    ("ChEBI2Reactome_PE_All_Levels.txt", "ChEBI2Reactome_PE_All_Levels.txt"),
                    ("Ensembl2Reactome_PE_All_Levels.txt", "Ensembl2Reactome_PE_All_Levels.txt"),
                    ("NCBI2Reactome_PE_All_Levels.txt", "NCBI2Reactome_PE_All_Levels.txt")
                ]

                for file_name, output_name in files_to_download:
                    downloadKEGGFile("              * Downloading " + file_name, downloadLog, reactomeURL + file_name, datadir,
                                     output_name, DOWNLOAD_DELAY_1, MAX_TRIES_1)

            # STEP 2.A.1 DOWNLOAD THE DATA FILES
            log("    STEP " + str(currentStep) + ". DOWNLOAD THE COMMON KEGG INFORMATION")
            # KEGG retired /list/organism (HTTP 400). See downloadKEGGOrganismList.
            downloadKEGGOrganismList("              * LIST OF ORGANISMS", downloadLog,
                                     datadir, "organisms_all.list", DOWNLOAD_DELAY_1, MAX_TRIES_1)
            downloadKEGGFile("              * PATHWAYS CLASSIFICATION", downloadLog,
                             "https://rest.kegg.jp/get/br:br08901", datadir, "pathways_classification.list",
                             DOWNLOAD_DELAY_1, MAX_TRIES_1)
            downloadKEGGFile("              * LIST OF REFERENCE PATHWAYS", downloadLog,
                             "https://rest.kegg.jp/list/pathway", datadir, "pathways_all.list", DOWNLOAD_DELAY_1,
                             MAX_TRIES_1)
            downloadKEGGFile("              * LIST OF COMPOUND NAMES", downloadLog, "https://rest.kegg.jp/list/compound",
                             datadir, "compounds_all.list", DOWNLOAD_DELAY_1, MAX_TRIES_1)
            downloadKEGGFile("              * PATHWAY to COMPOUND TABLE", downloadLog,
                             "https://rest.kegg.jp/link/pathway/compound", datadir, "pathway2compound.list",
                             DOWNLOAD_DELAY_1, MAX_TRIES_1)

            # ChEBI mapping download
            # KEGG's /conv/compound/chebi endpoint has been deprecated/removed (returns 400 errors)
            # We now use ChEBI's own database as the source for KEGG-ChEBI mappings
            try:
                downloadChEBItoKEGGMapping("             * KEGG TO ChEBI (from ChEBI database)", downloadLog,
                                          datadir, "kegg2chebi.list", DOWNLOAD_DELAY_1, MAX_TRIES_1)
            except Exception as e:
                log("                      WARNING: ChEBI conversion data unavailable")
                log("                      Continuing without ChEBI mapping - metabolite identification via ChEBI IDs will not be available")
                errorlog("ChEBI-KEGG mapping download failed but continuing: " + str(e))

            # STEP 2.A.2 DOWNLOAD THE PNG IMAGES
            pathways = readFile(datadir + "pathways_all.list", {"forced": True, "forcedColumn": 0})
            total = len(pathways.keys())
            log("             DETECTED " + str(total) + " REFERENCE PATHWAYS " + calculateAproxTime(total,
                                                                                                    DOWNLOAD_DELAY_2 + 3))
            os.mkdir(datadir + "png/")
            os.mkdir(datadir + "png/thumbnails")

            i = 1
            for pathway in pathways.keys():
                pathway = pathway.replace("path:", "")
                downloadKEGGFile("                     - " + pathway + " [" + str(i) + "/" + str(total) + "]",
                                 downloadLog, "https://rest.kegg.jp/get/" + pathway + "/image", datadir + "png/",
                                 pathway + ".png", DOWNLOAD_DELAY_2, MAX_TRIES_1)
                generateThumbnail(datadir + "png/" + pathway + ".png")
                i += 1
                #if i == 5:
                #    break
            # STEP 2.A.3 REMOVE THE DOWNLOADING FLAG AND ADD THE VERSION FILE
            os.remove(datadir + "DOWNLOADING")
            version = open(datadir + "VERSION", 'w')
            version.write("# DOWNLOAD DATE:\t" + strftime("%Y%m%d %H%M"))
            version.close()
            DOWNLOADED_SPECIES.append("common")
            summary.write('\tDOWNLOAD\tSUCCESS\tcommon\n')
            log("DOWNLOAD COMMON KEGG INFORMATION... SUCCESS\n")

        except Exception as e:
            if os.path.isdir(download_dir + "error/common"):
                shutil.rmtree(download_dir + "error/common")
            if os.path.isdir(datadir):
                shutil.move(datadir, download_dir + "error/")

            log("        FAILED WHILE DOWNLOADING/COPYING COMMON KEGG INFORMATION. UNABLE TO CONTINUE. ABORTING!!")
            summary.write('FAILED WHILE DOWNLOADING/COPYING COMMON INFORMATION. UNABLE TO CONTINUE')
            errorlog(e)
            summary.close()
            exit(1)

    currentStep += 1

    # **************************************************************************
    # STEP 2B. GET DATA FOR "TO UPDATE" SPECIES
    # **************************************************************************
    log('')
    log("STEP " + str(currentStep) + ". DOWNLOADING THE INFORMATION FOR THE SELECTED ORGANISMS")
    specie_code_list = SPECIES_DOWNLOAD.keys()
    total = str(len(specie_code_list))
    log("       - " + str(total) + " new organisms will be downloaded.")
    step = 0
    for specie in specie_code_list:
        if specie[0] == "#":
            log("    IGNORING " + specie[1:] + "...")
            continue

        datadir = os.path.join(download_dir, specie + "/")
        try:
            # STEP 2.B.0 INITIALIZE THE NEW DIRECTORY
            if os.path.isdir(datadir):
                shutil.rmtree(datadir)
            os.mkdir(datadir)

            # Add the flag file "DOWNLOADING"
            version = open(datadir + "DOWNLOADING", 'w')
            version.write("# DOWNLOAD STARTS:" + strftime("%Y%m%d %H%M"))
            version.close()

            step += 1
            log("")
            log("New data will be stored at " + datadir)
            log("        DOWLOADING  " + specie + "...")

            kegg_errors = "";
            mapping_errors = "";

            if reactome:
                log("STEP " + str(currentStep) + " Extra. DOWNLOADING REACTOME Files...")
                try:
                    # Imported here, not at module level: downloadReactome.py
                    # imports wait/generateThumbnail/log back from this module,
                    # and a top-level named import dies on that cycle (the old
                    # wildcard import only survived it by silently accepting a
                    # partially initialised module).
                    from scripts.downloadReactome import downloadReactome
                    downloadReactome(specie)
                except Exception as reactomeError:
                    # Coverage is a fact about Reactome's current release, not an
                    # error in this run: ptr (chimpanzee) has zero rows in the
                    # 2026 ReactomePathwaysRelation dump while 15 other species
                    # keep theirs. A species Reactome no longer projects onto
                    # installs KEGG-only (the build already fail-softs on the
                    # missing reactome directory); everything else - network
                    # failures mid-crawl included - still fails the species so a
                    # transient error cannot silently strip Reactome from it.
                    if "No pathway relations found" in str(reactomeError):
                        log("        WARNING: " + str(reactomeError).splitlines()[0])
                        log("        -> Reactome's current release does not cover this organism; continuing with KEGG data only.")
                        # Record the drop in the staged tree itself. The
                        # promotion guard reads this marker to let the install
                        # retire the previously installed Reactome artifacts
                        # (which this tree legitimately lacks) instead of
                        # refusing the whole species -- without it, the
                        # KEGG-only outcome promised above was unreachable for
                        # any species previously installed WITH Reactome.
                        with open(datadir + REACTOME_NOT_COVERED_MARKER, 'w') as marker:
                            marker.write("# Reactome release does not cover this organism. Recorded: " +
                                         strftime("%Y%m%d %H%M") + "\n")
                    else:
                        raise

            # STEP 2.B.1 IF USER SPECIFIED THAT KEGG DATA SHOULD BE DOWNLOADED, DOWNLOAD THE KEGG DATA, OTHERWISE COPY PREVIOUS DATA (IF EXISTS)
            # 2 = updateKegg, 3 = updateKegg && updateMapping
            if (SPECIES_DOWNLOAD[specie] > 1 or (not os.path.exists(KEGG_DATA_DIR + "current/" + specie))):
                os.mkdir(datadir + "kgml")
                kegg_errors = getSpecieKeggData(specie, downloadLog, datadir, str(step) + "/" + total)
            else:
                log("COPYING PREVIOUS KEGG DATA FOR " + specie + "...")
                shutil.rmtree(datadir)
                shutil.copytree(KEGG_DATA_DIR + "current/" + specie, datadir,
                                symlinks=True)  # COPYT THE ENTIRE DIRECTORY
                shutil.rmtree(datadir + "mapping")
                # Add the flag file "DOWNLOADING"
                version = open(datadir + "DOWNLOADING", 'w')
                version.write("# DOWNLOAD STARTS:" + strftime("%Y%m%d %H%M"))
                version.close()
                if os.path.isfile(datadir + "VERSION"):
                    os.remove(datadir + "VERSION")

            # STEP 2.B.2 IF SELECTED, GET THE MAPPING DATA, OTHERWISE COPY PREVIOUS DATA
            # 1=updateMapping, 3 = updateKegg && updateMapping

            if (SPECIES_DOWNLOAD[specie] == 1 or SPECIES_DOWNLOAD[specie] == 3 or (
            not os.path.exists(KEGG_DATA_DIR + "species/" + specie + "/mapping/"))):
                log("DOWNLOADING MAPPING DATA...")
                os.mkdir(datadir + "mapping")
                mapping_errors = getSpecieMappingData(specie, downloadLog, datadir + "mapping/",
                                                      str(step) + "/" + total, ROOT_DIRECTORY + "AdminTools/scripts/")
            else:
                log("COPYING PREVIOUS MAPPING DATA...")
                shutil.copytree(KEGG_DATA_DIR + "current/" + specie + "/mapping", datadir + "mapping",
                                symlinks=True)  # COPYT THE ENTIRE DIRECTORY

            # IF SOMETHING WENT WRONG DURING THE DOWNLOAD BUT THE PROCESS CONTINUED (TOLERANCE)
            if kegg_errors != "" or mapping_errors != "":
                log("Errors detected during the download for organism " + specie)
                log("  - Errors during KEGG data download: " + kegg_errors)
                log("  - Errors during MAPPING data download: " + mapping_errors)
                log("The organism will be moved to the erroneous directory but could be valid for installation.")
                raise Exception("Errors detected during the download for organism " + specie + ". Aborting.")

            # STEP 2.B.3 REMOVE THE DOWNLOADING FLAG AND ADD THE VERSION FILE
            os.remove(datadir + "DOWNLOADING")
            version = open(datadir + "VERSION", 'w')
            version.write("# DOWNLOAD DATE:\t" + strftime("%Y%m%d %H%M"))
            version.close()

            DOWNLOADED_SPECIES.append(specie)
            summary.write(specie + '\tDOWNLOAD\tSUCCESS\t' + str(SPECIES_DOWNLOAD[specie]) + '\n')
            log("DOWNLOAD  " + str(SPECIES_DOWNLOAD[specie]) + " " + specie + "...SUCCESS\n")

        except Exception as e:
            if os.path.isdir(download_dir + "error/" + specie):
                shutil.rmtree(download_dir + "error/" + specie)
            if os.path.isdir(datadir):
                shutil.move(datadir, download_dir + "error/")
            summary.write(specie + '\tDOWNLOAD\tERROR\t' + str(SPECIES_DOWNLOAD[specie]) + '\n')
            log("DOWNLOAD  " + str(SPECIES_DOWNLOAD[specie]) + " " + specie + "...ERROR\n")
            errorlog(e)
            FAILED_SPECIES.append(specie)

    currentStep += 1

    # **************************************************************************
    # STEP 6. CLOSING LOG FILES, GENERATING VERSION FILE
    # **************************************************************************
    log('')
    log("STEP " + str(currentStep) + ". CLOSING LOG FILES, GENERATING VERSION FILE")
    summary.close()

    version = open(download_dir + 'VERSION', 'w')
    version.write("# CREATION DATE:\t" + strftime("%Y%m%d %H%M") + "\n\n")
    version.write("######################################################################\n")
    version.write("#### THIS FILE WAS CREATED USING PAINTOMICS DATABASE GENERATOR    ####\n")
    version.write("######################################################################\n\n")
    version.write("----------------------------------------------------------------------\n\n")
    version.write("# DOWNLOADED SPECIES\n\n")
    version.write("\n".join(wrap("\t".join(DOWNLOADED_SPECIES), 40)))
    version.write("\n\n")
    version.write("----------------------------------------------------------------------\n\n")
    version.write("# ERRONEOUS SPECIES\n\n")
    version.write("\n".join(wrap("\t".join(FAILED_SPECIES), 40)))
    version.write("\n\n")
    version.write("----------------------------------------------------------------------\n\n")
    version.close()

    if len(FAILED_SPECIES) > 0 and len(DOWNLOADED_SPECIES) > 0:
        exit(2)
    elif len(FAILED_SPECIES) > 0:
        exit(1)
    else:
        exit(0)


def install_command(inputfile=None, specie=None, species=None, common=0, reinstall=0):
    """
    Install the information for given species
    Usage: AdminTools.py install <options>
    Examples:
              ./DBManager.py install --specie=mmu --common=0
              ./DBManager.py install --species=hsa,mmu,ath --common=0
              ./DBManager.py install --species=hsa,mmu --reinstall=1

    Keyword arguments:
        inputfile -- a file containing a list of species IDs (one per line) to be installed
        specie    -- a single valid KEGG specie code e.g. mmu, hsa
        species   -- a comma-separated list of species codes, e.g. hsa,mmu,ath
        common    -- (optional) 1 if Pathways info (classification, PNG images...) should be reinstalled, 0 to keep from previous version. Default=0
        reinstall -- (optional) 1 to rebuild from the data already in current/ without
                     promoting anything from download/. Default=0

    A species whose data is missing is skipped with a warning rather than ending the
    run, so one absent organism cannot cost a batch the other nineteen.
    """
    if inputfile == None and specie == None and species == None:
        print("Organisms not specified, please type ./DBManager.py install -h for help")
        exit(-1)

    readConfigurationFile()

    # **************************************************************************
    # STEP 1. READ CONFIGURATION AND PARSE INPUT FILES
    # **************************************************************************
    currentDataDir = os.path.join(KEGG_DATA_DIR, "current/")
    downloadDir = os.path.join(KEGG_DATA_DIR, "download/")
    oldDataDir = os.path.join(KEGG_DATA_DIR, "old/")
    if not os.path.exists(oldDataDir):
        os.mkdir(oldDataDir)

    # Hub directories are computed PER SPECIES inside the hub block below, not here.
    # Computing them here did two kinds of damage:
    #   - specie is legitimately None when --inputfile is used (see the branch below),
    #     so specie.lower() raised AttributeError before any work started, which is why
    #     the documented bulk install path had never run;
    #   - creating download/<specie>/hubData unconditionally, outside `if hub:`, meant
    #     that on a reinstall the "hub data already exists in current/" branch was taken
    #     while an EMPTY staged directory sat in download/, and the species move then
    #     replaced good hub data with nothing and reported success.
    # It also used specie.lower() while the species move uses the bare code.
    installLog = currentDataDir + "install.log"
    summary = open(currentDataDir + 'summary.log', 'a')
    currentStep = 1;

    log("######################################################################")
    log("### PAINTOMICS AI - DATABASE INSTALLER ")
    log("### v." + str(VERSION))
    log("######################################################################")
    log("")
    log("Installation log is at: " + installLog)
    log("")
    log("STEP " + str(currentStep) + ". READ CONFIGURATION AND PARSE INPUT FILES...")

    INSTALLED_SPECIES = []
    ERRONEOUS_SPECIES = []
    SKIPPED_SPECIES = []

    SPECIES_INSTALL = None
    if inputfile != None:
        SPECIES_INSTALL = readFile(inputfile)  # THE IDS FOR THE SPECIES TO UPDATE
    elif species != None:
        # Comma-separated list. Order is preserved so a run reads the way it was asked
        # for, and duplicates collapse rather than installing the same species twice.
        SPECIES_INSTALL = {}
        for code in str(species).split(","):
            code = code.strip()
            if code:
                SPECIES_INSTALL[code] = 1
        if not SPECIES_INSTALL:
            print("No valid species found in --species=" + str(species))
            exit(-1)
    else:
        SPECIES_INSTALL = {specie: 1}  # THE IDS FOR THE SPECIES TO UPDATE

    INSTALLED_PREVIOUS = getCurrentInstalledSpecies()  # THE PREVIOUSLY DOWNLOADED SPECIES

    sleep(2)
    log("       - " + str(len(SPECIES_INSTALL.keys())) + " new organisms will be installed.")
    # log("       - " + str(len(DOWNLOADED_PREVIOUS)) + " organisms were downloaded on previous executions." )
    log("       - " + str(len(INSTALLED_PREVIOUS)) + " organisms were installed on previous executions.")
    # log("       - " + str(len(ERRONEOUS_PREVIOUS)) + " organisms failed during the installation on previous executions." )
    log("")

    if ((common == None and confirm(
            prompt='Do you want to install the common KEGG information (compound names, pathway names, ...)?',
            resp=False)) or (common == 1)):
        common = True

    # **************************************************************************
    # STEP 1.5. VALIDATE THAT SPECIES DATA EXISTS (either in download or current)
    # **************************************************************************
    for specie_to_check in SPECIES_INSTALL.keys():
        if specie_to_check[0] == "#":
            continue

        downloadSpecieDir = os.path.join(downloadDir, specie_to_check)
        currentSpecieDir = os.path.join(currentDataDir, specie_to_check)

        # Check if data exists in either download or current directory.
        #
        # Skip, do not exit. Killing the whole run for one absent species means a batch
        # of twenty loses the nineteen that were ready -- and with --inputfile or a
        # comma-separated list that is the normal case, not an edge case. The species is
        # dropped from this run and named again in the closing summary.
        # --reinstall builds from current/ only, so that is the directory that has to
        # exist; a staged download is irrelevant to it.
        if reinstall and not os.path.isdir(currentSpecieDir):
            msg = (f"'{specie_to_check}' is not installed ({currentSpecieDir} does not "
                   f"exist), so there is nothing to reinstall -- run install first")
            log("WARNING: " + msg)
            summary.write(f"{specie_to_check}\tREINSTALL\tSKIPPED\tnot installed\n")
            INSTALL_WARNINGS.append((specie_to_check, msg))
            SKIPPED_SPECIES.append(specie_to_check)
            continue

        if not os.path.isdir(downloadSpecieDir) and not os.path.isdir(currentSpecieDir):
            msg = (f"no data for '{specie_to_check}' in either {downloadSpecieDir} or "
                   f"{currentSpecieDir} -- run the download command for it first")
            log("WARNING: " + msg)
            summary.write(f"{specie_to_check}\tINSTALL\tSKIPPED\tno downloaded data\n")
            INSTALL_WARNINGS.append((specie_to_check, msg))
            SKIPPED_SPECIES.append(specie_to_check)
            continue

        # Log which directory will be used
        if reinstall:
            log(f"Species '{specie_to_check}': will rebuild in place from current/")
        elif os.path.isdir(downloadSpecieDir):
            log(f"Species '{specie_to_check}': will use new data from download directory")
        else:
            log(f"Species '{specie_to_check}': will reinstall using existing data from current directory")

    # Drop the skipped ones so the install loop below never sees them.
    for skipped in SKIPPED_SPECIES:
        SPECIES_INSTALL.pop(skipped, None)

    if not SPECIES_INSTALL:
        log("Nothing to install: every requested species is missing its downloaded data.")
        summary.close()
        exit(1)

    # **************************************************************************
    # STEP 2. INSTALLING KEGG GLOBAL/HUB DATA
    # **************************************************************************

    # ********************************************************************************
    # HUB ANALYSIS DATA IS NO LONGER BUILT AT INSTALL TIME
    # ********************************************************************************
    # It used to be: ~65 s per species of R (hubAnalysisInstall.R +
    # GalaxyNetworkFunctionsv2.R) producing a 60 MB tree of 1,865 .RData files
    # plus a 34 MB JSON -- two copies of the same neighbourhoods.
    #
    # The graph is derived from the KGML this installer already downloads, on
    # first use and in about a second (mmu: 364 files, 96,618 edges, 1.03 s).
    # See src/common/KeggGraph/store.py and
    # docs/superpowers/specs/2026-08-25-metabolite-hub-graph-design.md.
    #
    # The move/preservation logic further down is deliberately LEFT IN PLACE.
    # Existing current/<specie>/hubData is the fallback source for any species
    # whose kgml/ was not retained, so it must survive an update; it simply is
    # never created any more. Those branches no-op when there is nothing to move.

    # ********************************************************************************
    # STEP 2.A.1 IF WE CHOOSED TO DONWLOAD THE GENERAL DATA (PATHWAYS CLASSIFICATION, ETC.)
    # ********************************************************************************
    try:
        if common:
            log("STEP " + str(currentStep) + ". INSTALLING COMMON KEGG INFORMATION")
            replaceNewVersionData(downloadDir, currentDataDir, "common", oldDataDir)
            installCommonData(currentDataDir + "common/", ROOT_DIRECTORY + "AdminTools/scripts/")
            currentStep += 1
    except PromotionRefused as e:
        # The guard said no BEFORE anything moved: current/common and the
        # database are untouched, so there is nothing to restore -- and the
        # restore below would promote the stale old/common archive over the
        # healthy installed one. Report the refusal and stop.
        log("        REFUSED TO INSTALL COMMON INFORMATION -- the installed data was left untouched. ABORTING!!")
        summary.write('REFUSED TO INSTALL COMMON INFORMATION (installed data left untouched): ' + str(e) + '\n')
        errorlog(e)
        summary.close()
        exit(1)
    except Exception as e:
        # TODO: RESTORE
        restorePreviousVersionData(oldDataDir, currentDataDir, "common", downloadDir + "error/")
        installCommonData(currentDataDir + "common/", ROOT_DIRECTORY + "AdminTools/scripts/")
        log("        FAILED WHILE INSTALLING COMMON INFORMATION. UNABLE TO CONTINUE. ABORTING!!")
        summary.write('FAILED WHILE INSTALLING COMMON INFORMATION. UNABLE TO CONTINUE. ABORTING!!')
        errorlog(e)
        summary.close()
        exit(1)

    # **************************************************************************
    # STEP 3. INSTALLING THE PROVIDED SPECIES
    # **************************************************************************
    log("")
    log("STEP " + str(currentStep) + ". INSTALLING NEW ORGANISMS")
    speciesAux = SPECIES_INSTALL.keys()
    total = str(len(speciesAux))
    log("       " + str(total) + " new organisms will be installed.")

    step = 0
    for specie in speciesAux:
        if specie[0] == "#":
            log("    IGNORING " + specie[1:] + "...")
            continue

        step += 1
        dirNameAux = os.path.join(currentDataDir, specie + "/")
        log("        INSTALLING  " + specie + "...")

        try:
            # hubData does not travel with the species move.
            #
            # It used to: the hub step staged into download/<specie>/hubData and relied on
            # this move to carry it into current/. That coupling caused two separate data
            # losses -- the freshly generated hub data being archived under old/, and (on a
            # KEGG-only refresh, where download/<specie> holds ONLY hubData) the move
            # promoting a one-directory tree over a complete installation and deleting the
            # other 14 files.
            #
            # Lift it out, move the species, then put it back. The two are now independent:
            # the species move only ever sees real species data, and hub data cannot be
            # destroyed by it.
            # --reinstall rebuilds the database from what is already in current/ and
            # touches no directories at all: nothing is promoted, archived, or moved.
            # That is the whole point -- re-running a build after a code fix should not
            # risk the installed data, and should not need the download tree to exist.
            if reinstall:
                log("        REINSTALL: rebuilding from " + dirNameAux + " (no species data is moved)")
                # ...with one exception. The hub step above always stages into
                # download/<specie>/hubData and relies on the species move to carry it
                # across -- a move reinstall deliberately skips. Without this, a
                # `reinstall --hub=1` used to build the hub data, leaving all 1,637 files in
                # download/, and reports SUCCESS with nothing installed.
                stagedHubDir = os.path.join(downloadDir, specie, "hubData")
                if hub_data_is_complete(stagedHubDir):
                    installedHubDir = os.path.join(currentDataDir, specie, "hubData")
                    if os.path.isdir(installedHubDir):
                        shutil.rmtree(installedHubDir, ignore_errors=True)
                    shutil.move(stagedHubDir, installedHubDir)
                    log("        Hub analysis data installed into " + installedHubDir)
            else:
                stagedHubDir = os.path.join(downloadDir, specie, "hubData")
                heldHubDir = None
                if hub_data_is_complete(stagedHubDir):
                    heldHubDir = os.path.join(downloadDir, "_hub_hold_" + specie)
                    if os.path.isdir(heldHubDir):
                        shutil.rmtree(heldHubDir, ignore_errors=True)
                    shutil.move(stagedHubDir, heldHubDir)
                    # A download/<specie> left holding nothing must not trigger a move at all.
                    stagedSpecieDir = os.path.join(downloadDir, specie)
                    if os.path.isdir(stagedSpecieDir) and not os.listdir(stagedSpecieDir):
                        os.rmdir(stagedSpecieDir)

                # try/finally, because the restore has to happen on the failure path too.
                # Without it, a promotion that raises between the two moves leaves the
                # hub data orphaned in download/_hub_hold_<specie> and absent from
                # current/ -- found as a real 60 MB directory stranded by an install that
                # the guard had refused hours earlier.
                try:
                    replaceNewVersionData(downloadDir, currentDataDir, specie, oldDataDir)
                finally:
                    if heldHubDir and os.path.isdir(heldHubDir):
                        installedHubDir = os.path.join(currentDataDir, specie, "hubData")
                        if os.path.isdir(installedHubDir):
                            shutil.rmtree(installedHubDir, ignore_errors=True)
                        os.makedirs(os.path.dirname(installedHubDir), exist_ok=True)
                        shutil.move(heldHubDir, installedHubDir)
                        log("        Hub analysis data installed into " + installedHubDir)

            installSpecieData(specie, installLog, dirNameAux, str(step) + "/" + total,
                              ROOT_DIRECTORY + "AdminTools/scripts/")
            INSTALLED_SPECIES.append(specie)
            summary.write(specie + '\tINSTALL\tSUCCESS\t' + str(SPECIES_INSTALL[specie]) + '\n')
            log("INSTALL  " + str(SPECIES_INSTALL[specie]) + " " + specie + "...SUCCESS\n")
        except PromotionRefused as e:
            # Nothing moved and Mongo was never touched: the species is exactly
            # as installed. The rollback below would promote the stale
            # old/<specie> archive over the intact installation and rebuild the
            # database from it -- a silent one-version regression triggered by
            # nothing more than a stale download directory -- so a refusal is
            # reported and the installed data left alone.
            summary.write(specie + '\tINSTALL\tREFUSED\t' + str(SPECIES_INSTALL[specie]) + '\n')
            log("INSTALL  " + str(SPECIES_INSTALL[specie]) + " " + specie +
                "...REFUSED (installed data left untouched)\n")
            errorlog(e)
            ERRONEOUS_SPECIES.append(specie)
        except Exception as e:
            restorePreviousVersionData(oldDataDir, currentDataDir, specie, downloadDir + "error/")
            installSpecieData(specie, installLog, dirNameAux, str(step) + "/" + total,
                              ROOT_DIRECTORY + "AdminTools/scripts/")
            summary.write(specie + '\tINSTALL\tERROR\t' + str(SPECIES_INSTALL[specie]) + '\n')
            log("INSTALL  " + str(SPECIES_INSTALL[specie]) + " " + specie + "...ERROR\n")
            errorlog(e)
            ERRONEOUS_SPECIES.append(specie)

    step = 0

    currentStep += 1

    # **************************************************************************
    # STEP 6. CREATE THE species.json FILE
    # **************************************************************************
    log("")
    log("STEP " + str(currentStep) + ". CREATING THE species.json FILE")
    generateAvailableSpeciesFile(INSTALLED_PREVIOUS + INSTALLED_SPECIES, currentDataDir + "common/organisms_all.list",
                                 currentDataDir + "species.json")

    currentStep += 1

    # **************************************************************************
    # STEP 7. CLOSING LOG FILES, GENERATING VERSION FILE
    # **************************************************************************
    log("")
    log("STEP " + str(currentStep) + ". CLOSING LOG FILES, GENERATING VERSION FILE")

    # One readable block at the end. A multi-species run previously left the operator to
    # reconstruct what happened by reading the whole log; the three lists below are the
    # answer to "what do I have now, and what do I need to go back for".
    # Assembled first, then emitted in one loop. log() derives its indentation from the
    # source line's own indentation, so emitting these from inside an if/for staircases
    # the output; building the lines up front keeps the block aligned.
    summaryLines = ["", "=" * 62, "INSTALL SUMMARY", "=" * 62,
                    "  installed : %d  %s" % (len(INSTALLED_SPECIES), " ".join(INSTALLED_SPECIES) or "-"),
                    "  failed    : %d  %s" % (len(ERRONEOUS_SPECIES), " ".join(ERRONEOUS_SPECIES) or "-"),
                    "  skipped   : %d  %s" % (len(SKIPPED_SPECIES), " ".join(SKIPPED_SPECIES) or "-")]

    if INSTALL_WARNINGS:
        summaryLines.append("  warnings  : %d (installed anyway)" % len(INSTALL_WARNINGS))
        summaryLines.extend("      %s: %s" % (subject, detail) for subject, detail in INSTALL_WARNINGS)
    else:
        summaryLines.append("  warnings  : none")

    summaryLines.append("=" * 62)

    for summaryLine in summaryLines:
        log(summaryLine)

    summary.close()

    version = open(currentDataDir + 'VERSION', 'a')

    version.write("\n\n")
    version.write("----------------------------------------------------------------------\n\n")
    version.write("# INSTALLED SPECIES\n\n")
    version.write("\n".join(wrap("\t".join(INSTALLED_SPECIES), 40)))
    version.write("\n\n")
    version.write("----------------------------------------------------------------------\n\n")
    version.write("# FAILED SPECIES (INSTALL)\n\n")
    version.write("\n".join(wrap("\t".join(ERRONEOUS_SPECIES), 40)))
    version.write("\n\n")
    version.write("----------------------------------------------------------------------\n\n")
    version.close()


def hubdoctor_command(species=None):
    """Check that every installed species yields a usable interaction graph.

    Deriving the graph on demand moved parse failures from install time to
    runtime. This is how you get the install-time check back when you want it --
    by choice, rather than paying ~65 s per species on every install for it.

    Usage:
              ./DBManager.py hubdoctor
              ./DBManager.py hubdoctor --species=mmu,ath

    Exit code is the number of species with no usable graph, so it can gate a
    deploy. Output goes to stdout rather than through log(): log() is
    logging.info, which is not enabled on a CLI run, and a diagnostic command
    that prints nothing is worse than no command at all.
    """
    from src.common.KeggGraph import store

    currentDir = os.path.join(KEGG_DATA_DIR, "current")
    if species:
        codes = [code.strip() for code in species.split(",") if code.strip()]
    else:
        # `current/` also holds shared trees and hand-made backups, none of
        # which is a species: reporting "mapman.bak NO GRAPH" is noise that
        # makes the real failures harder to see.
        skip = ("common", "reactome", "mapman", "global")
        codes = sorted(name for name in os.listdir(currentDir)
                       if os.path.isdir(os.path.join(currentDir, name))
                       and name not in skip
                       and not name.endswith((".bak", ".old", ".tmp")))

    failures = 0
    for code in codes:
        store.clear_cache()
        started = time()          # DBManager does `from time import ... time`
        graph = store.get_graph(code)
        if graph is None:
            print("HUB DOCTOR: %-8s NO GRAPH (no kgml/ and no legacy hubData)" % code)
            failures += 1
            continue
        print("HUB DOCTOR: %-8s %6d edges  %5d nodes  %5d compounds  %-11s %5.2fs"
              % (code, len(graph.edge_kind), len(graph.names),
                 len(graph.compounds()), graph.source, time() - started))
    print("HUB DOCTOR: %d of %d species have no usable graph" % (failures, len(codes)))
    return failures


def restore_command(remove=1, force=0):
    """
    Restores the last version of the database to previous version

    Keyword arguments:
        remove  -- 1 remove the current database directory, 0 keep the directory
        force   -- 1 force remove the current database directory and use previous version, 0  to prompt
    """

    readConfigurationFile()

    realPath = os.path.realpath(KEGG_DATA_DIR + "/last")
    currentFile = os.path.basename(realPath)

    older = '19870729_0534'
    limit = datetime.datetime.strptime(currentFile, "%Y%m%d_%H%M")
    for i in next(os.walk(KEGG_DATA_DIR))[1]:
        if i == "last" or i == "tmp" or i == "TEST_DATA":
            continue

        aux = datetime.datetime.strptime(i, "%Y%m%d_%H%M")
        aux2 = datetime.datetime.strptime(older, "%Y%m%d_%H%M")
        if (aux > aux2) and (aux < limit):
            older = i

    if older != '19870729_0534':
        if (force == 0 and not confirm(prompt='Restore to previous directory ' + older + '?', resp=False)):
            exit(1)

        if os.path.exists(KEGG_DATA_DIR + "/last"):
            os.remove(KEGG_DATA_DIR + "/last")
        os.symlink(KEGG_DATA_DIR + older, KEGG_DATA_DIR + "last")

        if remove == 1:
            if (force == 1 or confirm(prompt='Remove previous directory ' + realPath + '?', resp=False)):
                import shutil
                shutil.rmtree(realPath)


def findnew_command():
    """Find new available species in KEGG."""
    readConfigurationFile()


def findolder_command(nDays):
    """Find installed species older than nDays."""
    readConfigurationFile()


# ------------------------------------------------------------------------------------------
# ---  AUXILIAR FUNCTIONS                                                               ----
# ------------------------------------------------------------------------------------------

# Written into download/<specie>/ when the Reactome crawl discovers the current
# release no longer projects onto the organism; read by the promotion guard below.
REACTOME_NOT_COVERED_MARKER = "REACTOME_NOT_COVERED"


class PromotionRefused(Exception):
    """The never-lose-files guard vetoed a promotion BEFORE anything moved.

    This must stay distinguishable from a failure that happened mid-install:
    on a refusal, current/<dirname> and the imported database are exactly as
    they were, so the install_command failure path has nothing to roll back --
    and rolling back anyway would promote the stale old/<dirname> archive over
    a healthy installation and rebuild Mongo from it, silently regressing the
    species one version.
    """


def replaceNewVersionData(origin, destination, dirname, backup_dir, isRestore=False):
    """
    Replace data in destination with data from origin.
    If origin data doesn't exist, check if we're doing a reinstall (data already in destination).
    """
    source_path = os.path.join(origin, dirname)
    dest_path = os.path.join(destination, dirname)
    backup_path = os.path.join(backup_dir, dirname)

    # Ensure paths are absolute
    source_path = os.path.abspath(source_path)
    dest_path = os.path.abspath(dest_path)
    backup_path = os.path.abspath(backup_path)

    # An install must never LOSE files. `os.path.isdir(source_path)` alone was the only
    # gate, so any leftover download directory -- including one an interrupted or
    # partial download had created -- was promoted over a complete installation and the
    # difference was silently archived under old/.
    #
    # Reproduced on 2026-08-12: a stale download/common holding 7 files replaced a
    # current/common holding 12, dropping Ensembl2Reactome_PE_All_Levels.txt and its four
    # siblings. The install logged "Installed new data for common" and the species build
    # then died with "Reactome source file missing", pointing at a file that had existed
    # minutes earlier and naming neither the move nor the promotion that removed it.
    #
    # Refuse instead. Deleting a known-stale download to proceed is a decision an admin
    # can make in one command; recovering silently archived data is not.
    if os.path.isdir(source_path) and os.path.isdir(dest_path) and not isRestore:
        sourceFiles = set(os.listdir(source_path))
        destFiles = set(os.listdir(dest_path))
        # hubData is deliberately kept OUT of the download tree (see the species move
        # below), so it is missing from every staged directory by design. Counting it
        # here made this guard fire on every reinstall of a species that has hub data --
        # 87 of the 105 installed species -- which blocked reinstalling any of them.
        #
        # The named files below are in the same class for the same reason: they are
        # GENERATED by the build that runs after this promotion (mergeNetworkFiles and
        # the Reactome/MapMan pathway processing write them into current/<specie>), so
        # no download directory ever contains them and comparing them here refused
        # every reinstall of an installed species. Verified against a fresh
        # `download --kegg=1 --mapping=1 --reactome=1` of sce on 2026-08-13: the run
        # imported the new database, then this guard refused the file promotion and
        # the species was reported FAILED with its fresh downloads stranded.
        # The previous versions are not lost by exempting them: the promotion archives
        # the whole installed directory under old/ before the build regenerates them.
        GENERATED_AT_BUILD = {
            "hubData",
            "pathways_network.json",
            "REACTOME_VERSION", "gene2pathway_reactome.list",
            "pathways_network_Reactome.json",
            "MAPMAN_VERSION", "MAPMAN_MAPPING", "gene2pathway_mapman.list",
            "pathways_network_MapMan.json",
            # OmniPath is installed by its own tool (omnipathInstaller.py), not
            # by the download/build pipeline, so a staged tree legitimately
            # lacks these while the installed tree has them. Without the
            # exemption every species that has ever had OmniPath installed --
            # mmu here -- fails promotion with PromotionRefused and silently
            # keeps stale data.
            "gene2pathway_omnipath.list", "pathways_network_OmniPath.json",
            # Not generated, but never worth refusing a promotion over: an
            # INSTALLED tree is never legitimately mid-download, so a
            # DOWNLOADING flag inside one is cruft from a historically
            # interrupted run. 21 species carried such flags from 2024-2025 era
            # downloads, and on 2026-08-14 every one of them failed promotion
            # on this single file, silently keeping stale data while the ledger
            # said OK (the error path rebuilt Mongo from the old inputs, whose
            # counts pass any threshold check).
            "DOWNLOADING",
        }
        exempt = set(GENERATED_AT_BUILD)
        # A download that discovered Reactome no longer covers this organism
        # records the fact in its own staged tree (see download_command).
        # Retiring the previously installed Reactome artifacts is then the
        # POINT of the promotion, not a loss -- they are archived under old/
        # with the rest of the tree. Without this, the coverage fail-soft was
        # unreachable for any species previously installed WITH Reactome: the
        # staged tree legitimately lacked reactome/ and this guard refused
        # every install of it. The marker itself is per-download metadata, so
        # an old marker left in the installed tree must never block a later,
        # Reactome-carrying download either -- hence unconditionally exempt.
        exempt.add(REACTOME_NOT_COVERED_MARKER)
        if REACTOME_NOT_COVERED_MARKER in sourceFiles:
            exempt |= {"reactome", "ReactomePathway.txt", "ReactomePathwayHierarchy.json"}
        missing = destFiles - sourceFiles - exempt
        if missing:
            raise PromotionRefused(
                "Refusing to install '" + dirname + "': the download directory is missing " +
                str(len(missing)) + " file(s) that the installed one has, so promoting it "
                "would delete them (" + ", ".join(sorted(missing)[:6]) +
                ("..." if len(missing) > 6 else "") + "). "
                "Source: " + source_path + " ; installed: " + dest_path + ". "
                "Re-run the download for this data, or delete the stale download directory "
                "if you are sure it should replace what is installed.")

    # Check if new data exists in download directory
    if os.path.isdir(source_path):
        # We have new data to install

        # Carry hub data across the move.
        #
        # Exempting hubData from the guard above is only half the job: the promotion
        # archives dest_path wholesale and moves source_path into its place, so an
        # installed hubData that the download tree does not have would be left behind in
        # old/ -- silently, since the guard no longer objects. Verified by doing exactly
        # that to mmu: 1,869 files, 60 MB, gone from current/ and reported as SUCCESS.
        #
        # Hold it aside and put it back once the new tree is in place. It is moved, not
        # copied, so this costs nothing on the same filesystem.
        heldHubData = None
        installedHubData = os.path.join(dest_path, "hubData")
        if os.path.isdir(installedHubData) and not os.path.isdir(os.path.join(source_path, "hubData")):
            heldHubData = os.path.join(os.path.dirname(dest_path), "_hubdata_hold_" + dirname)
            if os.path.isdir(heldHubData):
                shutil.rmtree(heldHubData, ignore_errors=True)
            shutil.move(installedHubData, heldHubData)

        # 1. Handle Backup: If destination exists, move it to backup
        if os.path.exists(dest_path):
            # Ensure backup parent directory exists
            backup_parent = os.path.dirname(backup_path)
            if not os.path.exists(backup_parent):
                os.makedirs(backup_parent)
            
            # Remove existing backup if it exists
            if os.path.exists(backup_path):
                if os.path.isdir(backup_path):
                    shutil.rmtree(backup_path)
                else:
                    os.remove(backup_path)
            
            # Move current data to backup
            shutil.move(dest_path, backup_path)
            
        # 2. Move new data to destination
        # Ensure destination parent directory exists
        dest_parent = os.path.dirname(dest_path)
        if not os.path.exists(dest_parent):
            os.makedirs(dest_parent)
            
        # Move source to destination
        shutil.move(source_path, dest_path)

        # Put the held hub data back into the freshly promoted tree.
        if heldHubData:
            shutil.move(heldHubData, os.path.join(dest_path, "hubData"))
            log(f"Preserved existing hub data for {dirname} across the update")

        log(f"Installed new data for {dirname}")
        
    elif os.path.isdir(dest_path):
        # Download directory doesn't have data, but current directory does
        # This is a reinstall scenario - data is already in place
        log(f"No new data found in {source_path}, using existing data in {dest_path} for reinstall")
    else:
        # Neither download nor current has the data
        error_msg = f"ERROR: Cannot find data for '{dirname}' in either download ({source_path}) or current ({dest_path}) directories"
        log(error_msg)
        raise Exception(error_msg)


def hub_data_is_complete(path):
    """
    Returns True only if `path` holds a FINISHED hub-analysis install.

    "Has contents" is not the same as "is complete" here, and the difference used to
    cost a whole reinstall: hubAnalysisInstall.R writes pathway_list.list within the
    first seconds, the per-compound .RData files throughout the run, and
    kegg_interaction.json only as its very last act. Any crash in between left a
    populated directory that the next install accepted as finished, skipped
    regeneration for, and reported as SUCCESS -- while every metabolite job then died
    on the missing JSON.

    Require the artifacts the runtime actually reads: the JSON that
    compundsClassification loads, the CSV that hubAnalysis.R reads, and at least one
    per-compound .RData that hubAnalysis.R load()s.

    Deliberately NOT implemented as a sentinel file inside the directory:
    hubAnalysis.R walks dir() by POSITION and would take a stray filename as a
    compound, leaving a NULL hole in its list.
    """
    if not os.path.isdir(path):
        return False
    try:
        entries = os.listdir(path)
    except OSError:
        return False

    for required in ("kegg_interaction.json", "kegg_interaction.csv"):
        target = os.path.join(path, required)
        if not (os.path.isfile(target) and os.path.getsize(target) > 0):
            return False

    return any(entry.endswith(".RData") for entry in entries)


def reinstall_command(species=None, specie=None, inputfile=None, common=0):
    """
    Rebuild the database for species that are already installed, without downloading.
    Usage: AdminTools.py reinstall <options>
    Examples:
              ./DBManager.py reinstall --species=hsa,mmu,ath
              ./DBManager.py reinstall --specie=ath
              ./DBManager.py reinstall --inputfile=species.txt

    Reads only from KEGG_DATA/current/<specie>/ and moves nothing: no promotion from
    download/, no archiving under old/. Use it after fixing build code, or to pick up a
    parser change, without re-fetching gigabytes that have not changed.

    A species that is not installed is skipped with a warning; the rest still run.
    """
    if species == None and specie == None and inputfile == None:
        print("Organisms not specified, please type ./DBManager.py reinstall -h for help")
        exit(-1)

    return install_command(inputfile=inputfile, specie=specie, species=species,
                           common=common, reinstall=1)


def restorePreviousVersionData(origin, destination, dirname, backup_dir):
    # A restore intentionally rolls back to an OLDER tree, which may lack files
    # the failed newer install had (pfa's pre-Reactome archive vs its fresh
    # download, observed 2026-08-13: the never-lose-files guard vetoed the
    # rollback and the failure path itself failed). Losing the newer files is
    # the point of a rollback, so the guard does not apply here.
    replaceNewVersionData(origin, destination, dirname, backup_dir, isRestore=True)

def downloadKEGGOrganismList(message, logFile, dirName, fileName, delay, maxTries):
    """
    Download the KEGG organism list and write it in the layout the rest of the
    codebase expects.

    KEGG retired /list/organism -- it now answers HTTP 400 -- which made every
    fresh install abort at "FAILED WHILE DOWNLOADING/COPYING COMMON KEGG
    INFORMATION". /list/genome replaces it but uses a different shape:

        /list/organism (gone):  T01001<TAB>hsa<TAB>Homo sapiens (human)<TAB>Eukaryotes;...
        /list/genome  (live):   T01001<TAB>hsa; Homo sapiens (human)

    Rather than change every consumer, convert to the historic four-column form.
    AdminServlet, common_build_database and AIInterpret's context_builder all
    read only columns 1 (code) and 2 (name), so the taxonomy column -- which
    /list/genome does not provide -- is written empty.

    Rows without an organism code (viral and addendum genomes are listed as a
    bare description) are skipped: they have no KEGG organism to install.
    """
    log(message)

    url = "https://rest.kegg.jp/list/genome"
    outputPath = os.path.join(dirName, fileName)

    lastError = None
    for attempt in range(1, maxTries + 1):
        try:
            response = requests.get(url, timeout=120)
            response.raise_for_status()

            rows = []
            for line in response.text.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                entry, description = parts[0], parts[1]
                # "hsa; Homo sapiens (human)" -> code "hsa", name "Homo sapiens (human)"
                if "; " not in description:
                    continue
                code, name = description.split("; ", 1)
                code = code.strip()
                if not code or " " in code:
                    continue
                rows.append((entry, code, name.strip()))

            if not rows:
                raise Exception("no organism rows parsed from " + url)

            with open(outputPath, "w") as handle:
                for entry, code, name in rows:
                    handle.write("\t".join([entry, code, name, ""]) + "\n")

            log("                      Parsed " + str(len(rows)) + " organisms from /list/genome")
            return True
        except Exception as exc:
            lastError = exc
            errorlog("                 FAIL! Trying again... " + str(attempt + 1) + " of " + str(maxTries))
            wait(delay)

    raise Exception("Unable to retrieve the KEGG organism list from " + url +
                    ": " + str(lastError) + "\n")


def downloadChEBItoKEGGMapping(message, logFile, dirName, fileName, delay, maxTries):
    """
    Downloads ChEBI database_accession.tsv.gz and extracts KEGG compound mappings.
    This is an alternative to KEGG's deprecated /conv/compound/chebi endpoint.

    Format: chebi:XXXXX<tab>cpd:CXXXXX
    """
    log(message)

    CHEBI_URL = "https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files/database_accession.tsv.gz"
    # ChEBI files KEGG cross-references under three different source ids, one per KEGG
    # sub-database, and each uses its own accession prefix. Measured over the whole
    # 422,561-row table on 2026-08-12:
    #     source 45 -> COMPOUND  C#####   18,465
    #     source 46 -> DRUG      D#####    4,529
    #     source 47 -> GLYCAN    G#####      831
    # Only source 45/C was accepted, so 5,360 ChEBI->KEGG mappings were dropped. That
    # matters because KEGG pathway maps genuinely draw glycans and drugs -- the installed
    # mmu hub network contains 213 G and 28 D nodes -- while KEGG's /list/compound
    # endpoint returns COMPOUND only (compounds_all.list is 19,541 rows, all C). So a
    # glycan on a pathway had no route from a user's ChEBI identifier at all.
    KEGG_SOURCE_PREFIXES = {"45": "C", "46": "D", "47": "G"}

    nTry = 1
    while nTry <= maxTries:
        wait(delay)
        try:
            # Download the gzipped TSV file
            response = requests.get(CHEBI_URL, timeout=120)  # Longer timeout for larger file
            response.raise_for_status()

            # Decompress and parse the TSV file
            with gzip.open(BytesIO(response.content), 'rt') as f:
                lines = f.readlines()

            # Extract KEGG compound mappings
            # Format: id, compound_id, accession_number, type, status_id, source_id
            # We want: compound_id (ChEBI ID) and accession_number (KEGG ID) where source_id=45
            kegg_mappings = []
            for line in lines[1:]:  # Skip header
                fields = line.strip().split('\t')
                if len(fields) >= 6:
                    compound_id = fields[1]
                    accession_number = fields[2]
                    source_id = fields[5]

                    # Keep an entry only when the accession has the prefix and length that
                    # its own source id implies -- source 45 also carries CAS numbers such
                    # as "498-15-7", which must not be taken for KEGG identifiers.
                    expectedPrefix = KEGG_SOURCE_PREFIXES.get(source_id)
                    if (expectedPrefix
                            and len(accession_number) == 6
                            and accession_number[0] == expectedPrefix
                            and accession_number[1:].isdigit()):
                        kegg_mappings.append(f"chebi:{compound_id}\tcpd:{accession_number}\n")

            # Write to output file
            with open(dirName + fileName, 'w') as f:
                f.writelines(kegg_mappings)

            # Log success
            with open(logFile, 'a') as log_file:
                log_file.write(f"{strftime('%Y-%m-%d %H:%M:%S')} - Downloaded {fileName} from ChEBI database ({len(kegg_mappings)} mappings)\n")

            log(f"                      SUCCESS: {len(kegg_mappings)} ChEBI-KEGG mappings extracted")
            return True

        except requests.exceptions.RequestException as e:
            # Log the error details to the log file
            with open(logFile, 'a') as log_file:
                log_file.write(f"{strftime('%Y-%m-%d %H:%M:%S')} - Error downloading ChEBI mapping: {str(e)}\n")

            # Only log "Trying again" if we actually will try again
            if nTry < maxTries:
                errorlog("FAIL! Trying again... " + str(nTry + 1) + " of " + str(maxTries))
            nTry += 1
        except Exception as e:
            # Handle parsing errors
            with open(logFile, 'a') as log_file:
                log_file.write(f"{strftime('%Y-%m-%d %H:%M:%S')} - Error processing ChEBI data: {str(e)}\n")

            if nTry < maxTries:
                errorlog("FAIL! Trying again... " + str(nTry + 1) + " of " + str(maxTries))
            nTry += 1

    raise Exception('Unable to retrieve ChEBI-KEGG mapping from ' + CHEBI_URL)

def downloadKEGGFile(message, logFile, URL, dirName, fileName, delay, maxTries):
    log(message)

    nTry = 1
    lastError = None
    while nTry <= maxTries:
        wait(delay)
        try:
            # Use requests library instead of wget to avoid C-level memory issues
            response = requests.get(URL, timeout=30)
            response.raise_for_status()  # Raises HTTPError for bad status codes (4xx, 5xx)

            # raise_for_status() only rejects a bad STATUS. KEGG answers 200 with an
            # empty body for an unknown organism or a withdrawn entry, and the empty
            # file used to be written and reported as a successful download -- the KGML
            # loop then iterated zero pathways and the organism was recorded as
            # DOWNLOAD SUCCESS with no pathways at all.
            if not response.content.strip():
                raise requests.exceptions.RequestException(
                    "empty body (HTTP " + str(response.status_code) + ") for " + URL)

            # Write to a temporary name and rename only once the body is known to be
            # non-empty, so an interrupted write cannot leave a file that the next run
            # mistakes for a complete download.
            targetPath = dirName + fileName
            tempPath = targetPath + ".part"
            with open(tempPath, 'wb') as f:
                f.write(response.content)
            os.replace(tempPath, targetPath)

            # Log success to the log file
            with open(logFile, 'a') as log_file:
                log_file.write(f"{strftime('%Y-%m-%d %H:%M:%S')} - Downloaded {fileName} from {URL}\n")

            return True
        except requests.exceptions.RequestException as e:
            # Log the error details to the log file
            with open(logFile, 'a') as log_file:
                log_file.write(f"{strftime('%Y-%m-%d %H:%M:%S')} - Error downloading {fileName}: {str(e)}\n")

            lastError = e
            # A 400 is KEGG's contract answer -- "this list/conversion does not
            # exist" -- not a transient fault, so retrying it can never succeed.
            # Stop immediately and let the caller classify it; the retries were
            # pure added latency on every organism KEGG does not fully cover.
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 400:
                break
            # Only log "Trying again" if we actually will try again
            if nTry < maxTries:
                errorlog("FAIL! Trying again... " + str(nTry + 1) + " of " + str(maxTries))
            nTry += 1
    # The cause must ride in the exception text, not only in the log file:
    # getSpecieMappingData decides whether a failure is permanent ("400 Client
    # Error" = KEGG does not offer this conversion) by reading str(e), and the
    # old bare message hid the status - bvu's 400 was retried and then failed
    # the species even after the 400-as-absence handling landed.
    raise Exception('Unable to retrieve ' + fileName + " from " + URL + ": " + str(lastError))


def getSpecieKeggData(specie, downloadLog, dirName, step):
    start_time = time()
    kegg_errors = ""

    log("            FETCHING KEGG DATA FOR " + specie + " (" + step + ")...")
    version = open(dirName + 'KEGG_VERSION', 'w')
    version.write("# CREATION DATE:\t" + strftime("%Y%m%d %H%M"))
    version.close()

    downloadKEGGFile("              * GENE to PATHWAY TABLE", downloadLog, "https://rest.kegg.jp/link/pathway/" + specie,
                     dirName, "gene2pathway.list", DOWNLOAD_DELAY_1, MAX_TRIES_1)
    downloadKEGGFile("              * PATHWAYS LIST", downloadLog, "https://rest.kegg.jp/list/pathway/" + specie,
                     dirName, "pathways.list", DOWNLOAD_DELAY_1, MAX_TRIES_1)
    # downloadKEGGFile("              * PATHWAY to GENE TABLE", downloadLog,  "http://rest.kegg.jp/link/" + specie + "/pathway", dirName, "pathway2gene.list",  DOWNLOAD_DELAY_1, MAX_TRIES_1)

    # CREATE THE pathway2gene.list File
    check_call(
        "cat " + dirName + "/gene2pathway.list | awk '{print $2\"\t\"$1}' | sort > " + dirName + "/pathway2gene.list",
        shell=True)

    # GET THE PATHWAYS LIST AND DOWNLOAD THE IMAGES
    if os.path.isfile(dirName + "pathways.list"):
        if not os.path.isdir(dirName + "kgml/"):
            os.mkdir(dirName + "kgml/")

        pathways = readFile(dirName + "pathways.list", {"forced": True, "forcedColumn": 0})
        total = len(pathways.keys())
        log("                DETECTED " + str(total) + " PATHWAYS FOR " + specie + " " + calculateAproxTime(total,
                                                                                                            DOWNLOAD_DELAY_2 + 3))

        error_tolerance = int(total * 0.05)  # we tolerate that a 5% of the pathways fail on download
        i = 1
        for pathway in pathways.keys():
            try:
                pathway = pathway.replace("path:", "")
                downloadKEGGFile("                     - " + pathway + " [" + str(i) + "/" + str(total) + "]",
                                 downloadLog, "https://rest.kegg.jp/get/" + pathway + "/kgml", dirName + "kgml/",
                                 pathway + ".kgml", DOWNLOAD_DELAY_2, MAX_TRIES_1)
            except Exception:
                error_tolerance -= 1;
                kegg_errors += " " + pathway
                if error_tolerance == 0:
                    raise Exception(
                        "Too many errors while downloading the KGML files for organism " + specie + ": " + kegg_errors)
                log("                       Failed!! The download process will continue...")
            i += 1


    else:
        raise Exception('Unable to retrieve ' + dirName + "pathways.list")

    wait(DOWNLOAD_DELAY_2)

    log("        DOWNLOADED IN " + str((time() - start_time)) + " seconds ---")

    return kegg_errors


def getSpecieMappingData(specie, downloadLog, dirName, step, scriptsDir):
    start_time = time()
    mapping_errors = ""

    log("            FETCHING MAPPING DATA FOR " + specie + " (" + step + ")...")
    version = open(dirName + 'MAP_VERSION', 'w')
    version.write("# CREATION DATE:\t" + strftime("%Y%m%d %H%M"))
    version.close()

    downloadLogFile = open(downloadLog, 'a')
    try:
        # A species without its own resources directory used to get no external
        # mapping data at all -- only the KEGG conversion lists below -- so its
        # Ensembl identifiers were never installed even when Ensembl publishes
        # them. scripts/default/download_others.py consults the genebuild
        # registry and fetches the dumps for a registered species; for any
        # other it does nothing, which keeps the old behaviour exactly.
        downloadOthers = scriptsDir + specie + "_resources/download_others.py"
        if not os.path.isfile(downloadOthers):
            downloadOthers = scriptsDir + "default/download_others.py"
        if os.path.isfile(downloadOthers):
            log("     * RETRIEVING EXTERNAL MAPPING DATA")
            try:
                check_call([sys.executable, downloadOthers, specie,
                            ROOT_DIRECTORY + "AdminTools/", dirName], stdout=downloadLogFile, stderr=downloadLogFile)
            except CalledProcessError as exc:
                raise Exception(
                    "Error while calling " + downloadOthers + ": Exit status " + str(
                        exc.returncode) + ". Output is available at " + downloadLog)

        # we tolerate that some of the files fail on download
        error_tolerance = 3

        # A mapping list KEGG does not offer for this organism answers HTTP 400.
        # That is a contract answer, not a transient failure: dosa (rice, keyed
        # by RAP-DB ids) gets 400 for /conv/dosa/ncbi-geneid on every attempt,
        # and csau's pathways and KGML download fine while /list/csau answers
        # 400 -- a per-endpoint coverage gap, not an invalid organism (that
        # case fails the KEGG data phase minutes earlier). Counting it as a
        # mapping error failed the whole species at the end of the download.
        # The build side already tolerates the file's absence
        # (processKEGGMappingData warns "Unable to find ... MAPPING file" and
        # continues), so absence is the correct translation of a 400 here.
        # Transient errors keep their old accounting: they are real failures
        # and still fail the species.
        def keggDoesNotOfferIt(exc):
            return "400 Client Error" in str(exc)

        # One body for the three endpoint downloads below. This logic was
        # copy-pasted per endpoint and the copies had already drifted in
        # wording; any change to the classification must hit all of them.
        def downloadMappingList(message, url, fileName, errorKey, absenceNote):
            nonlocal error_tolerance, mapping_errors
            try:
                downloadKEGGFile(message, downloadLog, url, dirName, fileName,
                                 DOWNLOAD_DELAY_1, MAX_TRIES_1)
            except Exception as e:
                if keggDoesNotOfferIt(e):
                    log("                       " + absenceNote)
                else:
                    error_tolerance -= 1
                    mapping_errors += " " + errorKey
                    if error_tolerance == 0:
                        raise Exception(
                            "Too many errors while downloading the KEGG mapping files for organism " + specie + ": " + mapping_errors)
                    log("                       Failed!! The download process will continue...")

        downloadMappingList("             * KEGG TO NCBI GeneID",
                            "https://rest.kegg.jp/conv/" + specie + "/ncbi-geneid",
                            "ncbi-geneid2kegg.list", "ncbi-geneid",
                            "KEGG offers no ncbi-geneid conversion for " + specie + " (HTTP 400); continuing without it.")

        downloadMappingList("             * KEGG TO Uniprot",
                            "https://rest.kegg.jp/conv/" + specie + "/uniprot",
                            "uniprot2kegg.list", "uniprot2kegg",
                            "KEGG offers no uniprot conversion for " + specie + " (HTTP 400); continuing without it.")

        downloadMappingList("             * KEGG TO Gene Symbol",
                            "https://rest.kegg.jp/list/" + specie,
                            "kegg2genesymbol.list", "kegg2genesymbol",
                            "KEGG offers no gene list for " + specie + " (HTTP 400); continuing without gene symbols.")

        # downloadKEGGFile("             * KEGG TO NCBI GI", downloadLog,  "http://rest.kegg.jp/conv/"+ specie +"/ncbi-gi", dirName, "ncbi-gi2kegg.list",  DOWNLOAD_DELAY_1, MAX_TRIES_1)

        log("            DOWNLOADED IN " + str(int((time() - start_time) / 60)) + " minutes ---")
    except Exception as ex:
        raise ex
    finally:
        downloadLogFile.close()
    return mapping_errors


def newBuildWarningsHandoff():
    """A private file for one species build to report its skipped sources in.

    mkstemp rather than a fixed name, for three reasons. Two installs running at
    once on the same machine wrote to the same path and read each other's
    warnings. /tmp is world-writable and a predictable name there is a file
    anyone can pre-create as a symlink onto something we then truncate. And a
    fixed path has to be deleted before every build to avoid inheriting the
    previous species' list -- a fresh file cannot be stale, so that step is gone.

    Created 0600 by mkstemp and handed to the child through the environment;
    the fd is closed immediately because only the subprocess writes to it.
    """
    handle, path = tempfile.mkstemp(prefix="paintomics_build_warnings_", suffix=".tsv")
    os.close(handle)
    return path


def collectBuildWarnings(specie, handoffPath):
    """Pull the sources the species build had to do without into the run summary.

    The build runs as a subprocess, so without this its warnings only ever reach
    install.log -- which is precisely the multi-MB file nobody reads to the end.
    """
    if not handoffPath or not os.path.isfile(handoffPath):
        return
    try:
        with open(handoffPath) as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 4 and parts[0] == specie:
                    INSTALL_WARNINGS.append((specie + " / " + parts[1], parts[2] + " -> " + parts[3]))
                    log("       ! " + parts[1] + ": " + parts[3])
    except Exception as readError:
        errorlog("could not read build warnings for " + specie + ": " + str(readError))
    finally:
        # In `finally`: a read that throws half way must not leave the file behind.
        # A long batch would otherwise leak one per species into /tmp.
        try:
            os.remove(handoffPath)
        except OSError:
            pass


def installSpecieData(specie, downloadLog, dirName, step, scriptsDir):
    start_time = time()
    log("                 INSTALLING MAPPING DATA FOR " + specie + " (" + step + ")...")
    downloadLogFile = open(downloadLog, 'a')
    # Where this build reports what it had to do without. Named in the environment
    # because check_call below passes no `env=`, so the child inherits ours.
    handoffPath = newBuildWarningsHandoff()
    os.environ["PAINTOMICS_BUILD_WARNINGS"] = handoffPath

    try:
        if os.path.isfile(scriptsDir + specie + "_resources/build_database.py"):
            log("       * PROCESSING AND INSTALLING CUSTOM AND KEGG DATA ")
            try:
                check_call([sys.executable, scriptsDir + specie + "_resources/build_database.py", specie,
                            ROOT_DIRECTORY + "AdminTools/", dirName, downloadLog], stdout=downloadLogFile,
                           stderr=downloadLogFile)
            except CalledProcessError as exc:
                errorlog(traceback.extract_stack())
                raise Exception(
                    "Error while calling " + scriptsDir + specie + "_resources/build_database.py" + ": Exit status " + str(
                        exc.returncode) + ". Output is available at " + downloadLog)
        else:
            log("       * PROCESSING AND INSTALLING DEFAULT KEGG DATA ")
            try:
                check_call([sys.executable, scriptsDir + "default/build_database.py", specie, ROOT_DIRECTORY + "AdminTools/",
                            dirName, downloadLog], stdout=downloadLogFile, stderr=downloadLogFile)
            except CalledProcessError as exc:
                errorlog(traceback.extract_stack())
                raise Exception(
                    "Error while calling " + scriptsDir + "default/build_database.py" + ": Exit status " + str(
                        exc.returncode) + ". Output is available at " + downloadLog)

        log("        INSTALLED IN " + str(int((time() - start_time))) + " seconds ---")
    except Exception as ex:
        raise ex
    finally:
        downloadLogFile.close()
        # Cleared before the read, so nothing downstream can inherit a pointer to a
        # file collectBuildWarnings is about to delete.
        os.environ.pop("PAINTOMICS_BUILD_WARNINGS", None)
        # In `finally` so a species that failed still reports what it was missing --
        # that list is usually the explanation for the failure.
        collectBuildWarnings(specie, handoffPath)
    return True


def installCommonData(dirName, scriptsDir):
    log("            INSTALLING COMMON DATA... ")

    try:
        import imp
        COMMON_BUILD_DB_TOOLS = imp.load_source('common_build_database', scriptsDir + "common_build_database.py")
        COMMON_BUILD_DB_TOOLS.processKEGGCommonData(dirName, ROOT_DIRECTORY)
    except Exception as ex:
        raise ex
    return True


def getCurrentInstalledSpecies():
    # ****************************************************************
    # Step 1.GET THE LIST OF INSTALLED SPECIES (DATABASES and SPECIES.JSON)
    # ****************************************************************
    organisms_names = {}
    import csv
    from conf.serverconf import MONGODB_HOST, MONGODB_PORT

    # Define the file paths
    current_file_path = KEGG_DATA_DIR + 'current/common/organisms_all.list'
    download_file_path = KEGG_DATA_DIR + 'download/common/organisms_all.list'

    # Check if the file exists in the current directory
    if os.path.isfile(current_file_path):
        file_path = current_file_path
    else:
        # If the file does not exist in the current directory, use the file in the download directory
        file_path = download_file_path

    # Open the file and read its contents. The list only supplies display
    # names; on a KEGG_DATA tree that has never had a common download
    # (--common=1) it does not exist yet, and an install must not die over
    # missing display names -- the installer-smoke test runs exactly that
    # fresh-tree case.
    organisms_names = {}
    if os.path.isfile(file_path):
        with open(file_path) as organisms_all:
            reader = csv.reader(organisms_all, delimiter='\t')
            for row in reader:
                organisms_names[row[1]] = row[2]
    else:
        log("            NOTE: no organisms_all.list yet (no common download); "
            "species names will show as their codes")

    from pymongo import MongoClient

    client = MongoClient(MONGODB_HOST, MONGODB_PORT)
    databases = client.list_database_names()

    # ****************************************************************
    # Step 2.FOR EACH INSTALLED DATABASE GET THE INFORMATION
    # ****************************************************************
    databaseList = []

    for database in databases:
        if not "-paintomics" in database:
            continue
        elif "global-paintomics" == database:
            db = client[database]
            # find(...)[0] raises IndexError on an empty cursor. The COMMON
            # version document is written by createGlobalDatabase(), which only
            # runs during a --common=1 install, so it is legitimately absent
            # until the first one completes -- and any interruption in between
            # leaves the database present but this document missing. That made
            # a later species install die with a bare
            #   IndexError: no such item for Cursor instance
            # naming neither the collection nor the reason.
            commonVersion = db.versions.find_one({"name": "COMMON"})
            if commonVersion is None:
                log("            WARNING: no COMMON version in global-paintomics; "
                    "run an install with --common=1 to populate it")
        else:
            # Step 2.1 GET THE SPECIE CODE
            organism_code = database.replace("-paintomics", "")
            # Step 2.2 GET THE SPECIE NAME
            organism_name = organisms_names.get(organism_code, "Unknown specie")

            # Step 2.3 GET THE SPECIE VERSIONS
            db = client[database]
            kegg_date = db.versions.find({"name": "KEGG"})[0].get("date")
            mapping_date = db.versions.find({"name": "MAPPING"})[0].get("date")
            # count_documents replaces Cursor.count(), removed in pymongo 4.
            try:
                acceptedIDsDoc = db.versions.find_one({"name": "ACCEPTED_IDS"})
                acceptedIDs = acceptedIDsDoc.get("ids") if acceptedIDsDoc else ""
            except Exception:
                acceptedIDs = ""

            # Step 2.4 Check if the organism has non installed data available
            if os.path.isfile(KEGG_DATA_DIR + 'download/' + organism_code + '/VERSION'):
                downloaded = True
            elif os.path.isfile(KEGG_DATA_DIR + 'download/' + organism_code + '/DOWNLOADING'):
                downloaded = "downloading"
            else:
                downloaded = False
                # This used to rmtree the directory as an "erroneous download not
                # removed". A getter must not delete: a parallel `download` process
                # wipes and recreates download/<sp> BEFORE it writes the DOWNLOADING
                # flag, and in that window this cleanup saw an unmarked directory and
                # destroyed it under the running download. Observed 2026-08-13: an
                # `install --specie=ath` deleted download/bta out from under bta's
                # Reactome crawl (which then died on a vanished output path), and this
                # rmtree itself crashed the whole ath install when bta's error handler
                # moved the remains to error/ mid-delete. The download step wipes its
                # own directory at start, so stale leftovers cost nothing by staying.

            databaseList.append({
                "organism_name": organism_name,
                "organism_code": organism_code,
                "kegg_date": kegg_date,
                "mapping_date": mapping_date,
                "acceptedIDs": acceptedIDs,
                "downloaded": downloaded
            })

    client.close()
    return databaseList


def generateAvailableSpeciesFile(VALID_SPECIES, species_file, installed_species_file):
    try:
        # rootName, "organisms_all.list",
        import csv
        species = {}
        with open(species_file, "r") as csvfile:
            rows = csv.reader(csvfile, delimiter='\t')
            # FILL THE TABLE specie_code -> specie_name
            for row in rows:
                species[row[1]] = row[2]
        csvfile.close()

        # Custom species (customSpeciesInstaller.py) register their display
        # names in organisms_custom.list beside organisms_all.list, because the
        # latter is re-downloaded from KEGG on a common refresh and would drop
        # them. A code installed in Mongo but absent from this lookup takes the
        # raise-branch below and aborts species.json for EVERY species, so the
        # merge is what keeps one custom organism from poisoning every later
        # standard install.
        # quoting=csv.QUOTE_NONE: the display names are admin-supplied
        # (customSpeciesInstaller validates --code but not --name), and the
        # default dialect treats a leading double quote as a field delimiter --
        # swallowing the tab and dropping the row, which re-triggers the
        # every-species abort this merge exists to prevent.
        custom_file = os.path.join(os.path.dirname(species_file), "organisms_custom.list")
        if os.path.isfile(custom_file):
            with open(custom_file) as fh:
                for row in csv.reader(fh, delimiter='\t', quoting=csv.QUOTE_NONE):
                    if len(row) >= 3:
                        species[row[1]] = row[2]

        listAux = []
        for specie in VALID_SPECIES:
            if isinstance(specie, dict):
                listAux.append(specie.get("organism_code"))
            else:
                listAux.append(specie)

        VALID_SPECIES = listAux
        VALID_SPECIES.sort()
        VALID_SPECIES = set(VALID_SPECIES)

        total = len(VALID_SPECIES)

        file_content = '{"success": true, "species": [\n'
        for i, specieCode in enumerate(VALID_SPECIES):
            name = species.get(specieCode, "")
            if name != "":
                # json.dumps, not string concatenation: a quote or backslash in
                # an admin-supplied custom species name would otherwise write an
                # unparseable species.json and break the organism dropdown for
                # every user (customSpeciesInstaller's own regenerate uses
                # json.dumps for the same reason).
                name = '\t{"name": ' + json.dumps(name) + ', "value": ' + json.dumps(specieCode) + '}'
                if i < total - 1:
                    name += ","
                file_content += name + '\n'
            else:
                # continue
                errorlog("Error while writting specie files" + specieCode)
                raise Exception()
    except Exception:
        errorlog(traceback.extract_stack())
        raise Exception("Error while writting specie " + specieCode)

    if os.path.isfile(installed_species_file):
        shutil.copy(installed_species_file, installed_species_file + "_prev")

    output_file = open(installed_species_file, 'w')
    output_file.write(file_content)
    output_file.write(']}')
    output_file.close()


# ------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------
# ---  MORE AUXILIAR FUNCTIONS                                                          ----
# ------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------

def generateThumbnail(imagePath):
    from PIL import ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    destination = imagePath.replace("png/", "png/thumbnails/").replace(".png", "_thumb.png")
    thumb = Image.open(imagePath)
    # Generate the thumbnail
    s = thumb.size
    n = min(s)
    thumb = thumb.crop((s[0] / 2 - n / 4, s[1] / 2 - n / 4, s[0] / 2 + n / 4, s[1] / 2 + n / 4))
    thumb.thumbnail((300, 300))
    thumb.save(destination)


def readConfigurationFile():
    global ROOT_DIRECTORY
    ROOT_DIRECTORY = os.path.abspath(os.path.dirname(os.path.realpath(__file__)) + "/../") + "/"
    # PREPARE LOGGING
    # The handler in conf/logging.cfg writes log/application.log relative to
    # the working directory; on a fresh checkout that directory does not exist
    # and fileConfig dies with a FileNotFoundError that never names it.
    os.makedirs("log", exist_ok=True)
    from src.common.LoggingSetup import configureLogging
    configureLogging(ROOT_DIRECTORY + 'conf/logging.cfg')


def readFile(path, options=None):
    data = {}
    forced = False
    forcedColumn = 0

    if (options != None):
        options.get("forced", False)
        options.get("forcedColumn", 0)

    if os.path.isfile(path):
        with open(path, 'r') as inputDataFile:  # Change 'rU' to 'r'
            import csv
            for line in csv.reader(inputDataFile, delimiter="\t"):
                if len(line) == 3 and forced == False:  # IF IT IS UPDATE FILE
                    data[line[
                        0]] = 0  # 0= DO NOTHING (DEFAULT ACTION), 1 = updateMapping, 2 = updateKegg, 3 = updateKegg && updateMapping

                    if (line[2] == "1"):  # updateMapping = 1
                        data[line[0]] += 1
                    if (line[1] == "1"):  # updateKegg = 1
                        data[line[0]] += 2
                    if (data[line[0]] == 0):  # IF DO NOTHING, WE REMOVE THE SPECIE FROM THE LIST
                        del data[line[0]]
                else:
                    data[line[forcedColumn]] = 3  # updateKegg = 1, updateMapping = 1

        inputDataFile.close()
    return data


def confirm(prompt=None, resp=False):
    """
    prompts for yes or no response from the user. Returns True for yes and
    False for no.
    'resp' should be set to the default value assumed by the caller when
    user simply types ENTER.
    >>> confirm(prompt='Create Directory?', resp=True)
    Create Directory? [y]|n:
    True
    >>> confirm(prompt='Create Directory?', resp=False)
    Create Directory? [n]|y:
    False
    >>>
    Create Directory? [n]|y: y
    True
    """

    if prompt is None:
        prompt = 'Confirm'

    if resp:
        prompt = '%s [%s]|%s: ' % (prompt, 'y', 'n')
    else:
        prompt = '%s [%s]|%s: ' % (prompt, 'n', 'y')

    while True:
        ans = input(prompt)
        if not ans:
            return resp
        if ans not in ['y', 'Y', 'n', 'N']:
            print('please enter y or n.')
            continue
        if ans == 'y' or ans == 'Y':
            return True
        if ans == 'n' or ans == 'N':
            return False


def diff(a, b):
    b = set(b)
    return [aa for aa in a if aa not in b]


def log(msg):
    frame, filename, line_number, function_name, lines, index = inspect.getouterframes(
        inspect.currentframe())[1]
    line = lines[0]
    indentation_level = line.find(line.lstrip())
    logging.info('{i} {m}'.format(
        i=' ' * indentation_level,
        m=msg
    ))


def errorlog(msg):
    frame, filename, line_number, function_name, lines, index = inspect.getouterframes(
        inspect.currentframe())[1]
    line = lines[0]
    indentation_level = line.find(line.lstrip())
    logging.error('{i} {m}'.format(
        i=' ' * indentation_level,
        m=msg
    ))


def wait(nSeconds):
    # log(message)
    sleep(nSeconds)


def calculateAproxTime(nElems, delay):
    return "[" + str(int(nElems * delay / 60)) + " min aprox.]"


# ------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------
# ---  RUN APP
# ------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------
if __name__ == '__main__':
    import scriptine
    # scriptinep3 0.3.1 still calls inspect.getargspec(), gone since Python
    # 3.11; without this every command failed before parsing its arguments.
    from scriptine_compat import ensure_getargspec

    ensure_getargspec()
    scriptine.run()

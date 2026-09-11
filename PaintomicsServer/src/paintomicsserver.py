#***************************************************************
#  This file is part of Paintomics v3
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#**************************************************************
import logging.config

from flask import Flask, request, send_file, send_from_directory, jsonify
from flask.json.provider import DefaultJSONProvider
from re import sub

from src.common.PySiQ import Queue
from src.common import JobProgress
from src.common import ExampleDatasets
from src.common import ExampleBundle
from src.common import DatabaseAvailability

from src.conf.serverconf import (
    CLIENT_TMP_DIR,
    KEGG_DATA_DIR,
    MAX_CLIENT_SPACE,
    N_WORKERS,
    ROOT_DIRECTORY,
    SERVER_ALLOW_DEBUG,
    SERVER_HOST_NAME,
    SERVER_MAX_CONTENT_LENGTH,
    SERVER_PORT_NUMBER,
    SERVER_SUBDOMAIN,
)

# Defensive: serverconf.py is gitignored and installed once, so a deployment
# upgraded in place still carries a config written before this setting existed.
# The star import above leaves the name simply undefined there, which would
# surface as a NameError at app construction rather than at import.
try:
    from src.conf.serverconf import SERVER_MAX_FORM_MEMORY_SIZE
except ImportError:
    SERVER_MAX_FORM_MEMORY_SIZE = SERVER_MAX_CONTENT_LENGTH

from src.servlets.PathwayAcquisitionServlet import (
    pathwayAcquisitionAdjustPvalues,
    pathwayAcquisitionHubSubgraph,
    pathwayAcquisitionHubFeature,
    pathwayAcquisitionHubNames,
    pathwayAcquisitionApplyReplicateMapping,
    pathwayAcquisitionMetagenes_PART1,
    pathwayAcquisitionPathwayEvidence,
    pathwayAcquisitionRecoverJob,
    pathwayAcquisitionSaveImage,
    pathwayAcquisitionSaveSharingOptions,
    pathwayAcquisitionSaveVisualOptions,
    pathwayAcquisitionStep1_PART1,
    pathwayAcquisitionStep2_PART1,
    pathwayAcquisitionStep3,
    pathwayAcquisitionTouchJob,
)
from src.servlets.DataManagementServlet import (
    dataManagementDeleteFile,
    dataManagementDeleteJob,
    dataManagementDownloadFile,
    dataManagementGetMyFiles,
    dataManagementGetMyJobs,
    dataManagementUploadFile,
)
from src.servlets.UserManagementServlet import (
    userManagementChangePassword,
    userManagementNewGuestSession,
    userManagementNewNoLoginSession,
    userManagementResetPassword,
    userManagementSignIn,
    userManagementSignOut,
    userManagementSignUp,
)
from src.servlets.Bed2GenesServlet import fromBEDtoGenes_STEP1
from src.servlets.MiRNA2GenesServlet import fromMiRNAtoGenes_STEP1
from src.servlets.MOREServlet import fromMOREtoGenes_STEP1, describeMOREBackends
from src.servlets.AdminServlet import (
    adminCleanDatabases,
    adminServletDeleteMessage,
    adminServletDeleteReport,
    adminServletDeleteUser,
    adminServletGetAllUsers,
    adminServletGetAvailableOrganisms,
    adminServletGetInstalledOrganisms,
    adminServletGetMessage,
    adminServletGetReports,
    adminServletInstallOrganism,
    adminServletSaveMessage,
    adminServletSendReport,
    adminServletSystemInformation,
    clearFailedData,
)
from src.servlets.CompoundSuggestionServlet import (
    compoundSuggestionInitiate,
    compoundSuggestionStatus,
)
from src.servlets.AIInterpretServlet import (
    aiGenerateExpDesign,
    aiInterpretChat,
    aiInterpretInitiate,
    aiInterpretPathway,
    aiInterpretReport,
    aiInterpretStatus,
    getAIProviderInfo,
)
from src.common.UserSessionManager import UserSessionManager
from src.common.LoggingSetup import configureLogging
from src.common.KeggInformationManager import KeggInformationManager
from src.common.JobInformationManager import JobInformationManager



def revalidateEntryDocument(response):
    """Stop index.html being served from cache without revalidating.

    Client assets are cache-busted by hand, with a version marker in index.html
    that is bumped when the file changes (``Util.js?v=0.7``). That only works if
    index.html itself is fetched fresh, and ``send_from_directory`` applies
    Flask's 12-hour ``SEND_FILE_MAX_AGE_DEFAULT`` to it like any other static
    file -- so a returning browser kept the old index.html, which still asked
    for the old ``?v=`` URLs it also still had cached, and the bump reached
    nobody who had visited before.

    Observed after the frontend work landed: the results page rendered nothing,
    with ``ReferenceError: truncatableTextRenderer is not defined`` from
    PA_Step3Views.js. That function is defined in Util.js and was added by the
    same work, so the browser was running the new views against the old Util.js.
    A hard reload fixed it, which is why this survives development.

    ``no-cache`` still lets the browser store the file; it just has to
    revalidate first, and the ETag Flask already sets makes that a 304. Expires
    is cleared too, because an HTTP/1.0 cache reads it in preference.

    Versioned assets are unaffected, and it is worth being exact about what the
    marker buys, because the wording here used to imply it earned them a longer
    cache. It does not -- measured, every static file gets the same
    ``max-age=43200``, versioned or not:

        Util.js?v=0.8      Cache-Control: public, max-age=43200
        PA_Step4Views.js   Cache-Control: public, max-age=43200

    What the marker buys is a *different URL*, so bumping it reaches a returning
    browser immediately instead of up to twelve hours later. That is the whole
    mechanism, and it only works if the bump is not forgotten -- which is what
    ``src/tests/test_versioned_assets_are_bumped.py`` enforces.

    Only the plain script tags here need it. Anything loaded through
    ``Application.loadModule`` is ``$.ajax({dataType: "script"})``, and jQuery
    defaults ``cache: false`` for script requests, so those carry their own
    ``?_=<epoch>`` and are always fresh.
    """
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    response.headers["Expires"] = "0"
    return response


def allowOpaqueOriginAssets(response):
    """Let the conversion sandbox load its own runtime.

    The sandbox that runs AI-written conversion code is an iframe with
    sandbox="allow-scripts" and WITHOUT allow-same-origin, which is what denies
    it cookies, localStorage and the parent DOM. The cost of that isolation is
    an opaque origin: every subresource request it makes carries ``Origin:
    null`` and is treated as cross-origin, so Pyodide's dynamic import of
    pyodide.asm.js is refused by CORS and the sandbox never boots. Measured:
    "Failed to fetch dynamically imported module ... pyodide.asm.js".

    Only ``/resources/pyodide/`` gets the header. Those files are the public
    WASM runtime and its wheels -- no credentials, no user data, nothing that
    is not already served to anyone who loads the page -- so a wildcard costs
    nothing. Widening it to the whole static tree would not.
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


class Application(object):
    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self):
        ##*******************************************************************************************
        ##****SERVLET DEFINITION*********************************************************************
        ##*******************************************************************************************
        self.readConfigurationFile()
        self.app = Flask(__name__)

        # The conversion sandbox has an opaque origin by design; without this
        # its own runtime is cross-origin to it and will not load.
        @self.app.after_request
        def _sandboxAssetHeaders(response):
            if request.path.startswith(SERVER_SUBDOMAIN + "/resources/pyodide/"):
                return allowOpaqueOriginAssets(response)
            return response

        self.app.config['MAX_CONTENT_LENGTH'] =  SERVER_MAX_CONTENT_LENGTH
        # Werkzeug 3.1 (pinned by the Flask upgrade in d6bcf643) gave
        # max_form_memory_size a 500 kB default and started applying it to
        # application/x-www-form-urlencoded bodies, not just multipart non-file
        # fields. MAX_CONTENT_LENGTH never gets a say: FormDataParser
        # ._parse_urlencoded compares Content-Length against this limit alone.
        #
        # /pa_save_image posts the whole pathway SVG -- background raster
        # inlined as a base64 data: URI -- as one urlencoded form field, so
        # every large map (map01100 urlencodes to 3.2 MB, map04820 to 1.1 MB,
        # 22 of 1172 KEGG images bust 500 kB on the background alone) came back
        # as "RequestEntityTooLarge ... 413" from request.form.get("jobID"),
        # the first form access in the handler.
        #
        # Keeping the two limits equal restores the pre-upgrade behaviour and
        # leaves one number in charge; nginx's client_max_body_size still caps
        # the request ahead of Flask. File uploads were never affected -- the
        # multipart parser sets field_size to None for File parts and skips the
        # check. Pinned by src/tests/test_form_memory_limit.py.
        self.app.config['MAX_FORM_MEMORY_SIZE'] = SERVER_MAX_FORM_MEMORY_SIZE
        configureJSONSerialisation(self.app)

        KeggInformationManager(KEGG_DATA_DIR) #INITIALIZE THE SINGLETON
        JobInformationManager()#INITIALIZE THE SINGLETON

        self.ensureIndexes()      #CREATE THE jobID INDEXES IF MISSING (IDEMPOTENT)
        self.startScheludeTasks() #CLEAN DATA EVERY N HOURS

        self.queue = Queue()
        self.queue.start_worker(N_WORKERS)

        #******************************************************************************************
        #     ______ _____ _      ______  _____
        #   |  ____|_   _| |    |  ____|/ ____|
        #   | |__    | | | |    | |__  | (___
        #   |  __|   | | | |    |  __|  \___ \
        #   | |     _| |_| |____| |____ ____) |
        #   |_|    |_____|______|______|_____/
        #
        #  COMMON STEPS HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/')
        def main():
            return revalidateEntryDocument(
                send_from_directory(self.ROOT_DIRECTORY + 'public_html','index.html'))
        ##*******************************************************************************************
        ##* GET THUMBNAILS, PATHWAY IMAGE, etc
        ##*******************************************************************************************
        # ---- AI-assisted input conversion --------------------------------
        # Enqueued, never inline: four request threads serve the whole site and
        # a gateway turn takes about a minute.
        @self.app.route(SERVER_SUBDOMAIN + '/input_convert/turn', methods=['POST'])
        def inputConvertTurnRoute():
            from servlets.InputConvertServlet import inputConvertTurn
            import uuid
            return inputConvertTurn(request, Response(), self.queue,
                                    "convert_" + uuid.uuid4().hex).getResponse()

        @self.app.route(SERVER_SUBDOMAIN + '/input_convert/turn/<path:ticket>')
        def inputConvertResultRoute(ticket):
            from servlets.InputConvertServlet import inputConvertResult
            return inputConvertResult(request, Response(), self.queue, ticket).getResponse()

        @self.app.route(SERVER_SUBDOMAIN + '/kegg_data/<path:filename>')
        def get_kegg_data(filename):
            logging.info("filename is:" + str(filename))
            if str(filename) == "species.json":
                return send_from_directory(KEGG_DATA_DIR + 'current/', 'species.json')
            else:
                # Possible accepted format <path>_<source>_thumb
                split_name = filename.replace('_thumb', '').split('_')

                logging.info("split_name is:" + str(split_name))

                # Sanitize input
                source_type = sub(r'\W+', '', split_name[-1]) if len(split_name) > 1 else None
                source_dir = 'current/' + source_type.lower() if source_type is not None else 'current/common'
                logging.info("split_name is:" + str(source_dir))

                # Add "map" prefix for KEGG pathways
                filename_prefix = 'map' if source_type is None else str()

                def convert_list_to_string(org_list, seperator='_'):
                    """ Convert list to string, by joining all item in list with given separator.
                        Returns the concatenated string """
                    return seperator.join( org_list )


                if source_type is None:
                    filename_cleaned = sub("[^0-9]", "", split_name[0]) if source_type is None else split_name[0]
                elif source_type.lower() == "mapman":
                    filename_cleaned = convert_list_to_string(split_name[:-1])
                    logging.info("filenamecleaned is:" + filename_cleaned)
                else:
                    filename_cleaned = sub("[^0-9]", "", split_name[0]) if source_type is None else split_name[0]


                #logging.info( "Name is: " + str( KEGG_DATA_DIR ) + str( source_dir ) + '/png/thumbnails/',
                #                  str( filename_prefix ) + str( filename_cleaned ) + '_thumb.png' )

                if str(filename).endswith("_thumb"):
                    return send_from_directory(KEGG_DATA_DIR + source_dir + '/png/thumbnails/', filename_prefix + filename_cleaned + '_thumb.png')
                else:
                    return send_from_directory(KEGG_DATA_DIR + source_dir + '/png/', filename_prefix + filename_cleaned + '.png')
        ##*******************************************************************************************
        ##* GET PATHWAY IMAGE
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/kegg_data/pathway_network/<path:specie>')
        def get_pathway_network(specie):
            return send_from_directory(KEGG_DATA_DIR + 'current/' + specie, 'pathways_network.json')

        @self.app.route(SERVER_SUBDOMAIN + '/kegg_data/pathway_network_reactome/<path:specie>')
        def get_pathway_network_reactome(specie):
            return send_from_directory(KEGG_DATA_DIR + 'current/' + specie, 'pathways_network_Reactome.json')

        @self.app.route( SERVER_SUBDOMAIN + '/kegg_data/pathway_network_mapman/<path:specie>' )
        def get_pathway_network_mapman(specie):
            return send_from_directory( KEGG_DATA_DIR + 'current/' + specie, 'pathways_network_MapMan.json' )

        @self.app.route( SERVER_SUBDOMAIN + '/kegg_data/pathway_network_omnipath/<path:specie>' )
        def get_pathway_network_omnipath(specie):
            return send_from_directory( KEGG_DATA_DIR + 'current/' + specie, 'pathways_network_OmniPath.json' )

        ##*******************************************************************************************
        ##* GET THE INTERACTION NETWORK OF ONE OmniPath PATHWAY
        ##*
        ##* The other three sources ship a diagram, so their "network" is a single
        ##* static file per species describing how whole pathways connect. OmniPath
        ##* has no diagram: the pathway IS a network, and the client draws it. The
        ##* whole edge set runs to ~1.3 MB per organism, which is why it is read one
        ##* pathway at a time from its own collection rather than folded into the
        ##* pathway documents KeggInformationManager caches for 25 organisms at once.
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/omnipath_network/<path:specie>/<path:pathwayID>')
        def get_omnipath_network(specie, pathwayID):
            # Both segments land in a MongoDB query and a database name, so they
            # are constrained to the character set the installer emits rather
            # than trusted. Anything else is a bad request, not a lookup.
            specie = sub(r'\W+', '', specie)
            pathwayID = sub(r'\W+', '', pathwayID)
            if not specie or not pathwayID:
                return jsonify({"success": False, "errorMessage": "Invalid organism or pathway"}), 400

            handle = None
            try:
                handle, db = KeggInformationManager().getConnectionByOrganismCode(specie)
                network = db["omnipath_network"].find_one({"ID": pathwayID}, {"_id": 0})
            except Exception as ex:
                logging.error("OMNIPATH NETWORK %s/%s: %s", specie, pathwayID, ex)
                return jsonify({"success": False, "errorMessage": "Network unavailable"}), 500
            finally:
                if handle is not None:
                    handle.close()

            if network is None:
                return jsonify({"success": False,
                                "errorMessage": "No OmniPath network for " + pathwayID}), 404
            return jsonify({"success": True, "network": network})


        ##*******************************************************************************************
        ##* GET DATA FROM CLIENT TMP DIR
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/CLIENT_TMP/<path:filename>')
        def get_client_file(filename):
            #TODO: CHECK CREDENTIALS?
            UserSessionManager().isValidUser(request.cookies.get('userID'), request.cookies.get('sessionToken'))
            return send_from_directory(self.ROOT_DIRECTORY + 'CLIENT_TMP', filename)

        @self.app.route(SERVER_SUBDOMAIN + '/get_cluster_image/<path:filename>')
        def get_cluster_image(filename):
            jobID = filename.split('/')[0]
            logging.info("filename:" + str(filename))

            jobInstance = JobInformationManager().loadJobInstance(jobID)

            # Check if the file really exist, if not, then we are probably accessing a public job from a logged
            # account.
            userDir = "nologin" if jobInstance.getUserID() is None else jobInstance.getUserID()
            image_path = CLIENT_TMP_DIR + userDir + "/jobsData/"

            return send_from_directory(image_path, filename)
        ##*******************************************************************************************
        ##* GET FILE
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/<path:filename>')
        def get_static(filename):
            return send_from_directory(self.ROOT_DIRECTORY + 'public_html', filename)


        #******************************************************************************************
        #    _    _  _____ ______ _____   _____
        #   | |  | |/ ____|  ____|  __ \ / ____|
        #   | |  | | (___ | |__  | |__) | (___
        #   | |  | |\___ \|  __| |  _  / \___ \
        #   | |__| |____) | |____| | \ \ ____) |
        #    \____/|_____/|______|_|  \_\_____/
        #
        #  USER MANAGEMENT SERVLETS HANDLERS
        #*******************************************************************************************
        ##* LOGIN
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/um_signin', methods=['OPTIONS', 'POST'])
        def signInHandler():
            return userManagementSignIn(request, Response()).getResponse()
        #*******************************************************************************************
        ##* SIGN OUT
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/um_signout', methods=['OPTIONS', 'POST'])
        def signOutHandler():
            return userManagementSignOut(request, Response()).getResponse()
        #*******************************************************************************************
        ##* SIGN UP
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/um_signup', methods=['OPTIONS', 'POST'])
        def signUpHandler():
            return userManagementSignUp(request, Response(), self.ROOT_DIRECTORY).getResponse()
        #*******************************************************************************************
        ##* LOGOUT
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/um_guestsession', methods=['OPTIONS', 'POST'])
        def newGuestSessionHandler():
            return userManagementNewGuestSession(request, Response()).getResponse()
        #*******************************************************************************************
        ##* NO USER SESSION
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/um_nologinsession', methods=['OPTIONS', 'POST'])
        def newNoLoginSessionHandler():
            return userManagementNewNoLoginSession(request, Response()).getResponse()
        #*******************************************************************************************
        ##* CHANGE PASS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/um_changepassword', methods=['OPTIONS', 'POST'])
        def changePasswordHandler():
            return userManagementChangePassword(request, Response()).getResponse()
        #*******************************************************************************************
        ##* RESET PASSWORD
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/um_resetpassword', methods=['OPTIONS', 'GET'])
        def resetPasswordHandler():
            return userManagementResetPassword(request, Response(), self.ROOT_DIRECTORY).getResponse()
        #*******************************************************************************************
        ##* USER MANAGEMENT SERVLETS HANDLERS - END
        #******************************************************************************************



        #******************************************************************************************
        #     ______ _____ _      ______  _____
        #   |  ____|_   _| |    |  ____|/ ____|
        #   | |__    | | | |    | |__  | (___
        #   |  __|   | | | |    |  __|  \___ \
        #   | |     _| |_| |____| |____ ____) |
        #   |_|    |_____|______|______|_____/
        #
        #   FILE UPLOAD HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_upload_file', methods=['OPTIONS', 'POST'])
        def uploadFileHandler():
            return dataManagementUploadFile(request, Response(), CLIENT_TMP_DIR).getResponse()
        #*******************************************************************************************
        ##* FILE LIST HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_get_myfiles', methods=['OPTIONS', 'POST'])
        def getMyFilesHandler():
            return dataManagementGetMyFiles(request, Response(), CLIENT_TMP_DIR, MAX_CLIENT_SPACE).getResponse()
        #*******************************************************************************************
        ##* FILE DELETION HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_delete_file', methods=['OPTIONS', 'POST'])
        def deleteFileHandler():
            return dataManagementDeleteFile(request, Response(), CLIENT_TMP_DIR, MAX_CLIENT_SPACE).getResponse()
        #*******************************************************************************************
        ##* JOB LIST HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_get_myjobs', methods=['OPTIONS', 'POST'])
        def getMyJobsHandler():
            return dataManagementGetMyJobs(request, Response()).getResponse()
        #*******************************************************************************************
        ##* JOB DELETION HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_delete_job', methods=['OPTIONS', 'POST'])
        def deleteJobHandler():
            return dataManagementDeleteJob(request, Response()).getResponse()
        #*******************************************************************************************
        ##* JOB RESULTS HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_downloadFile', methods=['OPTIONS', 'GET'])
        def downloadFileHandler():
            response =  dataManagementDownloadFile(request, Response())
            if hasattr(response,"getResponse") :
                response = response.getResponse()
            return response
        #*******************************************************************************************
        ##* GFT FILES HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_get_gtffiles', methods=['OPTIONS', 'POST'])
        def getGTFFilesHandler():
            return dataManagementGetMyFiles(request, Response(), self.EXAMPLE_FILES_DIR, MAX_CLIENT_SPACE, isReference=True).getResponse()
        #*******************************************************************************************
        ##* EXAMPLE DATASET CATALOGUE
        #*******************************************************************************************
        # The picker behind "Load example". GET, unauthenticated and read-only:
        # it describes files the server already ships publicly, exposes no user
        # data, and returns no filesystem paths -- the client posts a scenario
        # id back and the server resolves it.
        @self.app.route(SERVER_SUBDOMAIN + '/example_datasets', methods=['OPTIONS', 'GET'])
        def exampleDatasetsHandler():
            # The resolver, so each card names the databases the job will really
            # run rather than the ones its manifest entry declares -- the example
            # branch of pathwayAcquisitionStep1_PART1 resolves the same way.
            content = ExampleDatasets.catalogueForClient(
                self.EXAMPLE_FILES_DIR,
                resolveDatabases=DatabaseAvailability.resolveDatabases)
            content["success"] = True
            return Response().setContent(content).getResponse()
        #*******************************************************************************************
        ##* EXAMPLE DATASET DOWNLOAD
        #*******************************************************************************************
        # The same catalogue as an archive, for the "Download example data"
        # button. It used to serve a static resources/*.zip built in 2017 whose
        # twelve files belonged to a dataset the picker no longer offers, so
        # "load the example" and "download the example" handed out different
        # experiments; building it from the manifest is what stops that.
        #
        # ?scenario=<id> narrows it to one dataset and ?pipeline=<name> to one
        # entry point's datasets, which is what the converter pages' own
        # "Download example data" buttons ask for. Unauthenticated and GET like
        # the catalogue beside it, and for the same reason: these are files the
        # server already ships publicly.
        @self.app.route(SERVER_SUBDOMAIN + '/example_datasets/download', methods=['OPTIONS', 'GET'])
        def exampleDatasetsDownloadHandler():
            scenarioId = (request.args.get("scenario") or "").strip() or None
            pipeline = (request.args.get("pipeline") or "").strip() or None
            try:
                path = ExampleBundle.bundleFor(self.EXAMPLE_FILES_DIR,
                                               scenarioId, pipeline)
            except ExampleDatasets.UnknownScenario as warning:
                # Rendered as a message rather than a 500: an unknown id here is
                # a mistyped URL, which is the user's to fix.
                return Response().setContent({
                    "success": False, "message": str(warning)}).getResponse()

            return send_file(path, mimetype="application/zip", as_attachment=True,
                             download_name=ExampleBundle.downloadName(scenarioId,
                                                                      pipeline))
        #*******************************************************************************************
        ##* INSTALLED PATHWAY DATABASES PER ORGANISM
        #*******************************************************************************************
        # What step 1's database checkboxes are drawn from. Same shape and same
        # audience as species.json, which sits beside it: GET, unauthenticated
        # and read-only, describing what this deployment installed and nothing
        # about anyone using it.
        #
        # The whole map rather than one organism per request, because the
        # alternative is a round trip on every change of the organism combo --
        # and the map is a few hundred bytes for a hundred organisms, answered
        # from a cache after the first call.
        @self.app.route(SERVER_SUBDOMAIN + '/organism_databases', methods=['OPTIONS', 'GET'])
        def organismDatabasesHandler():
            return Response().setContent({
                "success": True,
                "databases": DatabaseAvailability.getInstalledDatabasesByOrganism(),
                "known": list(DatabaseAvailability.KNOWN_DATABASES),
                "mandatory": DatabaseAvailability.MANDATORY_DATABASE,
            }).getResponse()
        #*******************************************************************************************
        ##* DATA MANIPULATION SERVLETS HANDLERS - END
        #*******************************************************************************************




        #*******************************************************************************************
        #         _  ____  ____   _____
        #        | |/ __ \|  _ \ / ____|
        #        | | |  | | |_) | (___
        #    _   | | |  | |  _ < \___ \
        #   | |__| | |__| | |_) |____) |
        #    \____/ \____/|____/|_____/
        #
        #############################################################################################
        #  COMMON JOB HANDLERS
        #
        #  CHECK JOB STATUS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/check_job_status/<path:jobID>', methods=['OPTIONS', 'POST'])
        def checkJobStatus(jobID):
            # Results that were handed out and never acknowledged are dropped
            # here, on the way in, rather than by a timer thread: see
            # Queue.reap_delivered.
            self.queue.reap_delivered()
            jobInstance = self.queue.fetch_job(jobID)

            def jobGoneResponse():
                return Response().setStatus(400).setContent({"success": False, "status" : "failed", "message": "Your job is not on the queue anymore. Check your job list, if it's not there the process stopped and you must resend the data again."}).getResponse()

            if jobInstance is None:
                return jobGoneResponse()
            elif jobInstance.is_finished():
                # The result is READ here, not consumed. It used to be taken
                # with get_result, which pops the entry, so the one HTTP
                # response carrying it was the only copy that would ever
                # exist -- and on 2026-09-11 that response was a 504 (see
                # Queue.deliver_result). The entry now stays until the client
                # acknowledges it (/ack_job_result), the delivered-result TTL
                # passes, or the next step reuses the id, so a poll retried
                # after a lost response still gets the result.
                #
                # Reading the job and delivering it are still two steps, and
                # two tabs on one job -- or a refresh landing beside a poll --
                # can both get past fetch_job. That used to hand the loser the
                # JobStatus enum, and calling getResponse() on it raised
                #     AttributeError: 'JobStatus' object has no attribute 'getResponse'
                # a 500 for a job that had just *succeeded*. Both now get the
                # result; and if an acknowledgement or the reaper has removed
                # it between the two steps, the loser gets the same answer as
                # anyone polling a job that is gone.
                result = self.queue.deliver_result(jobID)
                if not hasattr(result, "getResponse"):
                    return jobGoneResponse()
                return result.getResponse()
            elif jobInstance.is_failed():
                # Same rule as a finished job: the failure is delivered, not
                # consumed, so a retry after a lost 400 still learns WHY the
                # job failed instead of "not on the queue anymore".
                self.queue.deliver_result(jobID)
                return Response().setStatus(400).setContent({"success": False, "status" : str(jobInstance.get_status()), "message": jobInstance.error_message}).getResponse()
            else:
                # The job reports its own position now (src/common/JobProgress.py).
                # What was here before was a closed-form guess,
                #     genes * databases * omics / 20000 * 15
                # whose inputs are filled by the very job it estimates, so it read
                # zero for the first 18s of a measured 83s run and then overshot by
                # +58% (predicted 131.4s). The bar showed 62% when the job was
                # 98.7% done. It also never applied to step 2 at all, because
                # args[0] there is a jobID string and the hasattr() guard was
                # false: every poll of a 51.6s step-2 run returned zeros.
                import time

                queuedFor = 0
                runningFor = 0
                if jobInstance.started_at is not None:
                    runningFor = round(time.monotonic() - jobInstance.started_at, 2)
                    queuedFor = round(jobInstance.started_at - jobInstance.queued_at, 2)
                elif jobInstance.queued_at is not None:
                    queuedFor = round(time.monotonic() - jobInstance.queued_at, 2)

                content = {
                    "success": False,
                    "status": str(jobInstance.get_status()),
                    "queuedFor": queuedFor,
                    "runningFor": runningFor,
                    # Kept so an older cached client keeps working: it reads
                    # timeSpent/estimatedFinishTime and divides them. Both are now
                    # derived from the real fraction rather than the old formula.
                    "timeSpent": runningFor,
                    "estimatedFinishTime": 0,
                }

                progress = JobProgress.snapshot(jobID)
                if progress is not None:
                    content["progress"] = progress
                    if "remainingHigh" in progress:
                        # Legacy field gets the middle of the band; the modern
                        # client reads remainingLow/High off `progress` instead.
                        content["estimatedFinishTime"] = round(
                            (progress["remainingLow"] + progress["remainingHigh"]) / 2.0, 1)

                return Response().setContent(content).getResponse()

        #*******************************************************************************************
        #  ACKNOWLEDGE A DELIVERED RESULT
        #
        #  The client posts here once it has received a finished or failed job's
        #  answer from /check_job_status, and the queue drops the entry. Until
        #  then (or until Queue.DELIVERED_RESULT_TTL passes) the result stays
        #  collectable, so a status poll whose response was lost in transit can
        #  be retried. Fire-and-forget on the client: a lost acknowledgement
        #  costs nothing but ten minutes of one entry in the jobs dict.
        #
        #  No ownership check, on purpose. /check_job_status has none either --
        #  a job id is a 10-character random string handed only to the browser
        #  that submitted it -- and the worst this route can do with a guessed id
        #  is what the old destructive read did on every poll.
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/ack_job_result/<path:jobID>', methods=['OPTIONS', 'POST'])
        def acknowledgeJobResult(jobID):
            removed = self.queue.acknowledge(jobID)
            return Response().setContent({"success": True, "removed": removed}).getResponse()
        #*******************************************************************************************
        ##* COMMON JOB HANDLERS - END
        #############################################################################################
        #############################################################################################
        #
        # PATHWAY ACQUISITION SERVLETS HANDLERS
        #
        # STEP 1 HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_step1/<path:exampleMode>', methods=['OPTIONS', 'POST'])
        @self.app.route(SERVER_SUBDOMAIN + '/pa_step1', methods=['OPTIONS', 'POST'])
        def pathwayAcquisitionStep1Handler(exampleMode=False):
            return pathwayAcquisitionStep1_PART1(request, Response(), self.queue, self.generateRandomID(), self.EXAMPLE_FILES_DIR, exampleMode).getResponse()
        #*******************************************************************************************
        # STEP 2 HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_step2', methods=['OPTIONS', 'POST'])
        def pathwayAcquisitionStep2Handler():
            return pathwayAcquisitionStep2_PART1(request, Response(), self.queue, self.ROOT_DIRECTORY).getResponse()

        # Step 2's "Choose for me" button. Enqueue-and-poll rather than one
        # blocking call: uWSGI runs this site on four threads, so a route that
        # waits on the LLM gateway would take a quarter of the site with it.
        @self.app.route(SERVER_SUBDOMAIN + '/pa_suggest_compounds', methods=['OPTIONS', 'POST'])
        def compoundSuggestionInitiateHandler():
            return compoundSuggestionInitiate(request, Response(), self.queue).getResponse()

        @self.app.route(SERVER_SUBDOMAIN + '/pa_suggest_compounds_status', methods=['OPTIONS', 'POST'])
        def compoundSuggestionStatusHandler():
            return compoundSuggestionStatus(request, Response(), self.queue).getResponse()
        #*******************************************************************************************
        # STEP 3 HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_step3', methods=['OPTIONS', 'POST'])
        def pathwayAcquisitionStep3Handler():
            return pathwayAcquisitionStep3(request, Response()).getResponse()

        # The metabolite hop-ring network. Unlike /check_job_status, which ships
        # the same job's hub payload with no session and no ownership check,
        # this one is guarded.
        @self.app.route(SERVER_SUBDOMAIN + '/pa_hub_subgraph', methods=['OPTIONS', 'POST'])
        def pathwayAcquisitionHubSubgraphHandler():
            return pathwayAcquisitionHubSubgraph(request, Response(), self.queue).getResponse()

        # Every omic for one clicked node. globalExpressionData ships
        # omicsValues[0] only, so this is the rest of the feature's data.
        @self.app.route(SERVER_SUBDOMAIN + '/pa_hub_feature', methods=['OPTIONS', 'POST'])
        def pathwayAcquisitionHubFeatureHandler():
            return pathwayAcquisitionHubFeature(request, Response(), self.queue).getResponse()

        # Readable compound names for the scored list. mappingComp holds what
        # the user uploaded, which for a file keyed by KEGG id is the id again.
        @self.app.route(SERVER_SUBDOMAIN + '/pa_hub_names', methods=['OPTIONS', 'POST'])
        def pathwayAcquisitionHubNamesHandler():
            return pathwayAcquisitionHubNames(request, Response(), self.queue).getResponse()
        #*******************************************************************************************
        # RECOVER JOB HANDLER
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_recover_job', methods=['OPTIONS', 'POST'])
        def recoverJobHandler():
            return pathwayAcquisitionRecoverJob(request, Response(), self.queue).getResponse()

        # *******************************************************************************************
        # TOUCH JOB HANDLER
        # *******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_touch_job', methods=['OPTIONS', 'POST'])
        def touchJobHandler():
            return pathwayAcquisitionTouchJob(request, Response()).getResponse()
        #*******************************************************************************************
        # SAVE IMAGE HANDLER
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_save_image', methods=['OPTIONS', 'POST'])
        def saveImageHandler():
            return pathwayAcquisitionSaveImage(request, Response()).getResponse()
        #*******************************************************************************************
        # SAVE VISUAL OPTIONS HANDLER
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_save_visual_options', methods=['OPTIONS', 'POST'])
        def saveVisualOptionsHandler():
            return pathwayAcquisitionSaveVisualOptions(request, Response()).getResponse()
        #*******************************************************************************************
        # SAVE SHARING OPTIONS HANDLER
        #*******************************************************************************************
        #*******************************************************************************************
        # PATHWAY EVIDENCE OVERLAY HANDLER
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_pathway_evidence', methods=['OPTIONS', 'POST'])
        def pathwayEvidenceHandler():
            return pathwayAcquisitionPathwayEvidence(request, Response()).getResponse()
        @self.app.route(SERVER_SUBDOMAIN + '/pa_save_sharing_options', methods=['OPTIONS', 'POST'])
        def saveSharingOptionsHandler():
            return pathwayAcquisitionSaveSharingOptions(request, Response()).getResponse()
        # *******************************************************************************************
        # RETRIEVE NEW P-VALUES HANDLER
        # *******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_adjust_pvalues', methods=['OPTIONS', 'POST'])
        def adjustPvaluesHandler():
            return pathwayAcquisitionAdjustPvalues(request, Response()).getResponse()

        # *******************************************************************************************
        # APPLY REPLICATE→SAMPLE MAPPING (Step-2 confirmation panel)
        # *******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_apply_replicate_mapping', methods=['OPTIONS', 'POST'])
        def applyReplicateMappingHandler():
            return pathwayAcquisitionApplyReplicateMapping(request, Response()).getResponse()

        # *******************************************************************************************
        # REGENERATE METAGENES HANDLER
        # *******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/pa_get_clusters', methods=['OPTIONS', 'POST'])
        def metagenesHandler():
            return pathwayAcquisitionMetagenes_PART1(request, Response(), self.queue, self.generateRandomID(), self.ROOT_DIRECTORY).getResponse()
        #*******************************************************************************************
        # PATHWAY SERVLETS HANDLERS - END
        #############################################################################################
        #############################################################################################
        #
        # AI INTERPRETATION SERVLETS HANDLERS
        #
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/ai_interpret_initiate', methods=['OPTIONS', 'POST'])
        def aiInterpretInitiateHandler():
            return aiInterpretInitiate(request, Response(), self.queue).getResponse()

        @self.app.route(SERVER_SUBDOMAIN + '/ai_interpret_status', methods=['OPTIONS', 'POST'])
        def aiInterpretStatusHandler():
            return aiInterpretStatus(request, Response()).getResponse()

        @self.app.route(SERVER_SUBDOMAIN + '/ai_interpret_report', methods=['OPTIONS', 'POST'])
        def aiInterpretReportHandler():
            return aiInterpretReport(request, Response()).getResponse()

        @self.app.route(SERVER_SUBDOMAIN + '/ai_interpret_chat', methods=['OPTIONS', 'POST'])
        def aiInterpretChatHandler():
            return aiInterpretChat(request, Response()).getResponse()

        @self.app.route(SERVER_SUBDOMAIN + '/ai_interpret_pathway', methods=['OPTIONS', 'POST'])
        def aiInterpretPathwayHandler():
            return aiInterpretPathway(request, Response()).getResponse()

        # Who receives the data, answered before the user consents rather than
        # after the feature breaks. Until this existed, the only string in the
        # product naming the gateway was the missing-API-key error -- a working
        # install told the user nothing about where their data went, and a
        # broken one told them everything.
        #
        # GET and unauthenticated on purpose: it is a fact about the server's
        # configuration, the same class of thing as /organism_databases, and
        # the consent notice has to render before anyone has a session.
        @self.app.route(SERVER_SUBDOMAIN + '/ai_provider', methods=['OPTIONS', 'GET'])
        def aiProviderHandler():
            return Response().setContent(
                dict({"success": True}, **getAIProviderInfo())).getResponse()

        @self.app.route(SERVER_SUBDOMAIN + '/ai_generate_exp_design', methods=['OPTIONS', 'POST'])
        def aiGenerateExpDesignHandler():
            return aiGenerateExpDesign(request, Response(), self.EXAMPLE_FILES_DIR).getResponse()
        #*******************************************************************************************
        # AI INTERPRETATION SERVLETS HANDLERS - END
        #############################################################################################
        #############################################################################################
        #
        # ALTERNATIVE PIPELINES SERVLETS HANDLERS
        #
        # fromBEDtoGenes HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_fromBEDtoGenes/<path:exampleMode>', methods=['OPTIONS', 'POST'])
        @self.app.route(SERVER_SUBDOMAIN + '/dm_fromBEDtoGenes', methods=['OPTIONS', 'POST'])
        def fromBEDtoGenesHandler(exampleMode=False):
            result = fromBEDtoGenes_STEP1(request, Response(), self.queue, self.generateRandomID(), self.EXAMPLE_FILES_DIR, exampleMode).getResponse()
            return result
        #*******************************************************************************************
        # fromMiRNAtoGenes HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_fromMiRNAtoGenes/<path:exampleMode>', methods=['OPTIONS', 'POST'])
        @self.app.route(SERVER_SUBDOMAIN + '/dm_fromMiRNAtoGenes', methods=['OPTIONS', 'POST'])
        def fromMiRNAtoGenesHandler(exampleMode=False):
            result = fromMiRNAtoGenes_STEP1(request, Response(), self.queue, self.generateRandomID(), self.EXAMPLE_FILES_DIR, exampleMode).getResponse()
            return result
        #*******************************************************************************************
        # fromMOREtoGenes HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_fromMOREtoGenes/<path:exampleMode>', methods=['OPTIONS', 'POST'])
        @self.app.route(SERVER_SUBDOMAIN + '/dm_fromMOREtoGenes', methods=['OPTIONS', 'POST'])
        def fromMOREtoGenesHandler(exampleMode=False):
            result = fromMOREtoGenes_STEP1(request, Response(), self.queue, self.generateRandomID(), self.EXAMPLE_FILES_DIR, exampleMode).getResponse()
            return result

        # Which regulatory engines this host can actually run, so the picker
        # can disable what is not installed and say why instead of offering a
        # choice that fails deep inside the job.
        #
        # GET and unauthenticated, like /organism_databases and /ai_provider:
        # it states a fact about the server's own installation, carries no
        # secret beyond a binary path the operator configured, and the form
        # renders before anyone has a session.
        @self.app.route(SERVER_SUBDOMAIN + '/more_backends', methods=['OPTIONS', 'GET'])
        def moreBackendsHandler():
            return Response().setContent(
                dict({"success": True}, **describeMOREBackends())).getResponse()
        #*******************************************************************************************
        ##* ALTERNATIVE PIPELINES SERVLETS HANDLERS - END
        #############################################################################################


        #*******************************************************************************************
        #             _____  __  __ _____ _   _
        #       /\   |  __ \|  \/  |_   _| \ | |
        #      /  \  | |  | | \  / | | | |  \| |
        #     / /\ \ | |  | | |\/| | | | | . ` |
        #    / ____ \| |__| | |  | |_| |_| |\  |
        #   /_/    \_\_____/|_|  |_|_____|_| \_|
        #
        ##* ADMIN SERVLETS HANDLERS
        ##*
        ##* GET ADMIN SITE FILES HANDLERS
        ##*******************************************************************************************
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/admin/')
        def get_admin_static():
            try :
                userID = request.cookies.get('userID')
                sessionToken = request.cookies.get('sessionToken')
                userName = request.cookies.get('userName')
                UserSessionManager().isValidAdminUser(userID, userName, sessionToken)
                return send_from_directory(self.ROOT_DIRECTORY + 'public_html/admin', "index.html")
            except Exception:
                return send_from_directory(self.ROOT_DIRECTORY + 'public_html/admin', "404.html")
        ##*******************************************************************************************
        ##* GET LIST OF INSTALLED SPECIES
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/databases/', methods=['OPTIONS', 'GET'])
        def getInstalledDatabasesInfo():
            return adminServletGetInstalledOrganisms(request, Response()).getResponse()
        ##*******************************************************************************************
        ##* GET AVAILABLE SPECIES
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/databases/available', methods=['OPTIONS', 'GET'])
        def getAvailableDatabasesInfo():
            return adminServletGetAvailableOrganisms(request, Response()).getResponse()
        ##*******************************************************************************************
        ##* INSTALL OR UPDATE SELECTED SPECIE
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/databases/<path:organism_code>', methods=['OPTIONS', 'POST'])
        def installOrganismDatabaseData(organism_code):
            return adminServletInstallOrganism(request, Response(), organism_code, self.ROOT_DIRECTORY).getResponse()
        ##* DELETE SELECTED SPECIE
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/databases/<path:organism_code>', methods=['OPTIONS', 'DELETE'])
        def deleteOrganismDatabaseData(organism_code):
            response = Response()
            response.setContent({"success": False})
            return response.getResponse()
            #return adminServletDeleteOrganism(request, Response(), organism_code, self.ROOT_DIRECTORY).getResponse()

        ##*******************************************************************************************
        ##* MONITOR THE USAGE OF RAM AND CPU
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/system-info/', methods=['OPTIONS', 'GET'])
        def systemInformation():
            return adminServletSystemInformation(request, Response()).getResponse()

        ##*******************************************************************************************
        ##* GET ALL USERS AND DISK USAGE
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/users/', methods=['OPTIONS', 'GET'])
        def getAllUsers():
            return adminServletGetAllUsers(request, Response()).getResponse()
        ##*******************************************************************************************
        ##* REMOVE USERS
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/users/<path:userID>', methods=['OPTIONS', 'DELETE'])
        def deleteUser(userID):
            return adminServletDeleteUser(request, Response(), userID).getResponse()
        ##*******************************************************************************************
        ##* REMOVE OLD USERS AND CLEAN OLD DATA
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/clean-databases/', methods=['OPTIONS', 'DELETE'])
        def cleanDatabases():
            return adminCleanDatabases(request, Response()).getResponse()

        ##*******************************************************************************************
        ##* ADD FILES HANDLERS
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/files/', methods=['OPTIONS', 'POST'])
        def addReferenceFileHandler():
            return dataManagementUploadFile(request, Response(), self.EXAMPLE_FILES_DIR, isReference=True).getResponse()
        #*******************************************************************************************
        ##* FILE LIST HANDLERS
        #*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/files/', methods=['OPTIONS', 'GET'])
        def getReferenceFilesHandler():
            return dataManagementGetMyFiles(request, Response(), self.EXAMPLE_FILES_DIR, MAX_CLIENT_SPACE, isReference=True).getResponse()
        ##*******************************************************************************************
        ##* GFT FILE DELETION HANDLERS
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/files/<path:fileName>', methods=['OPTIONS', 'DELETE'])
        def deleteReferenceFileHandler(fileName):
            return dataManagementDeleteFile(request, Response(), self.EXAMPLE_FILES_DIR, MAX_CLIENT_SPACE, isReference=True, fileName=fileName).getResponse()

        ##*******************************************************************************************
        ##* SAVE THE  MESSAGE
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/messages/', methods=['OPTIONS', 'POST'])
        def saveMessage():
            return adminServletSaveMessage(request, Response()).getResponse()
        ##*******************************************************************************************
        ##* RETRIEVE THE MESSAGES
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/um_get_message', methods=['OPTIONS', 'POST'])
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/messages/', methods=['OPTIONS', 'GET'])
        def getMessage():
            return adminServletGetMessage(request, Response()).getResponse()
        ##*******************************************************************************************
        ##* DELETE MESSAGE
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/messages/<path:message_type>', methods=['OPTIONS', 'DELETE'])
        def deleteMessage(message_type):
            return adminServletDeleteMessage(request, Response(), message_type).getResponse()

        ##*******************************************************************************************
        ##* SEND A REPORT MESSAGE
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/dm_sendReport', methods=['OPTIONS', 'POST'])
        def sendReportHandler():
            return adminServletSendReport(request, Response(), self.ROOT_DIRECTORY).getResponse()
        ##*******************************************************************************************
        ##* RETRIEVE THE SUBMITTED REPORTS (organism requests, error reports)
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/reports/', methods=['OPTIONS', 'GET'])
        def getReports():
            return adminServletGetReports(request, Response()).getResponse()
        ##*******************************************************************************************
        ##* DISMISS A REPORT
        ##*******************************************************************************************
        @self.app.route(SERVER_SUBDOMAIN + '/api/admin/reports/<path:reportID>', methods=['OPTIONS', 'DELETE'])
        def deleteReport(reportID):
            return adminServletDeleteReport(request, Response(), reportID).getResponse()
        ##*******************************************************************************************
        ##* ADMIN SERVLETS HANDLERS - END
        #############################################################################################
        #############################################################################################
    def launch(self):
        ##*******************************************************************************************
        ##* LAUNCH APPLICATION
        ##*******************************************************************************************
        # use_reloader=False: the auto-reloader kills background threads
        # (PySiQ workers, AI pipeline) whenever a .py file changes on disk.
        # Keep debug=True for nice error pages, but disable the reloader.
        self.app.run(host=SERVER_HOST_NAME, port=SERVER_PORT_NUMBER,
                     debug=SERVER_ALLOW_DEBUG, use_reloader=False)

    ##*************************************************************************************************************
    # This function returns a new random job id
    #
    # @returns jobID
    ##*************************************************************************************************************
    def generateRandomID(self):
        #RANDOM GENERATION OF THE JOB ID
        #TODO: CHECK IF NOT EXISTING ID
        import string, random
        jobID = ''.join(random.sample(string.ascii_letters+string.octdigits*5,10))
        return jobID

    def readConfigurationFile(self):
        self.ROOT_DIRECTORY = ROOT_DIRECTORY
        import os
        if self.ROOT_DIRECTORY == "":
            self.ROOT_DIRECTORY = os.path.abspath(os.path.dirname(os.path.realpath(__file__))) + "/"
        else:
            self.ROOT_DIRECTORY = os.path.abspath(self.ROOT_DIRECTORY) + "/"

        self.EXAMPLE_FILES_DIR = self.ROOT_DIRECTORY + "examplefiles/"

        #PREPARE LOGGING
        configureLogging(self.ROOT_DIRECTORY + 'conf/logging.cfg')

        #self.app.config['MAX_CONTENT_LENGTH'] = SERVER_MAX_CONTENT_LENGTH * pow(1024, 2)

    def ensureIndexes(self):
        # Idempotent and near-free when the indexes exist. Until now they were
        # created only by the 24h cron, so a fresh deployment scanned
        # collections for its first day -- and aiInterpretationCollection,
        # which the AI status poll hits every few seconds, was never in that
        # list.
        #
        # On a daemon thread, NOT the boot path. Boot did no MongoDB I/O at all
        # before this call existed, and the two cases where it is slow are
        # exactly the cases where the server most needs to come up: an
        # unreachable mongod costs the 30 s server-selection timeout, and a
        # deployment restored from a mongodump without indexes pays the whole
        # build of a 2.85M-document featuresCollection index. Both used to
        # happen on the cron's background thread; neither should hold the port
        # closed. Daemon so it can never keep a shutting-down process alive.
        def createIndexes():
            from src.AdminTools.scripts.clean_databases import JOBID_INDEXES
            from src.common import DatabaseAvailability
            from src.common.DBmanager import getSharedClient
            from src.conf.serverconf import MONGODB_DATABASE
            try:
                database = getSharedClient()[MONGODB_DATABASE]
                for collectionName, keys in JOBID_INDEXES:
                    database[collectionName].create_index(keys)
                logging.info("jobID indexes ensured.")
            except Exception as ex:
                # An unreachable database must not stop the server from
                # serving; it did not before this call existed.
                logging.warning("Could not ensure jobID indexes at startup: " + str(ex))

            # Same thread, same reasoning: the organisms installed before the
            # pathway `source` index existed get it here, once, so that
            # /organism_databases stops scanning their pathway documents.
            # Never raises; it logs the organism it could not index and goes
            # on to the next.
            indexed = DatabaseAvailability.ensurePathwaySourceIndexes(getSharedClient())
            logging.info("Pathway source index ensured for %d organisms.", len(indexed))

        import threading
        threading.Thread(target=createIndexes, name="ensureIndexes", daemon=True).start()

    def startScheludeTasks(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        import atexit
        from src.AdminTools.scripts.clean_databases import cleanDatabases

        cron = BackgroundScheduler(daemon=True)
        # Explicitly kick off the background thread
        cron.start()

        #@cron.interval_schedule(seconds=1)
        def scheludeTask():
            cleanDatabases(force=True)
            clearFailedData()

        cron.add_job(scheludeTask, 'interval', hours=24, id='my_job_id')
        # Shutdown your cron thread if the web process is stopped
        atexit.register(lambda: cron.shutdown(wait=False))

#################################################################################################################
#################################################################################################################
##* SUBCLASSES
##*************************************************************************************************************
import math


def _sanitizeForJSON(value):
    """Recursively convert NaN/Inf floats to None so the response serializes
    as strict JSON. Python's json.dumps (and Flask's jsonify) emit the literal
    tokens `NaN` / `Infinity` for these values, which browsers reject in
    JSON.parse — jQuery then routes the response through the error handler
    even though the HTTP status was 200, and the user sees a generic
    "Oops..Internal error!" popup with no message field.

    Omics with legitimate missing measurements (e.g. methylation CpG sites
    not assayed in every sample) propagate NaN through the response in many
    places (per-feature `values` arrays, percentile summaries, etc.). Fixing
    each path individually is whack-a-mole; sanitising once at the response
    boundary is O(response size) and closes the entire class of bug.
    """
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {k: _sanitizeForJSON(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitizeForJSON(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_sanitizeForJSON(v) for v in value)
    return value


class Response(object):
    """This class is used to specify the custom response object"""

    #****************************************************************
    # CONSTRUCTORS
    #****************************************************************
    def __init__(self):
        self.content=""
        self.status= 200
        #TODO: ENABLE THIS CODE??
        self.JSON_CONTENT_TYPE = {'Content-Type': 'application/json; charset=utf-8'}
        self.content_type = self.JSON_CONTENT_TYPE

    #****************************************************************
    # GETTERS AND SETTER
    #****************************************************************
    def setContent(self, content):
        self.content = _sanitizeForJSON(content)
        return self
    def getContent(self):
        return self.content

    def setStatus(self, status):
        self.status=status
        return self
    def getStatus(self):
        return self.status

    def setContentType(self, content_type):
        self.content_type=content_type
        return self
    def getResponse(self):
        response = jsonify(self.content)

        # Assign rather than return the dict as a third tuple element.
        # Werkzeug *extends* the header list with whatever that dict holds, and
        # jsonify has already set "Content-Type: application/json", so every
        # JSON response went out carrying the header twice:
        #
        #   Content-Type: application/json
        #   Content-Type: application/json; charset=utf-8
        #
        # Repeating Content-Type is invalid (RFC 9110), and nginx resolves it by
        # keeping the first and discarding the second -- which is the one with
        # the charset, so the declaration this class exists to add never
        # actually reached a client. Item assignment replaces instead.
        for header, value in self.content_type.items():
            response.headers[header] = value

        return response, self.status

class ModelJSONProvider(DefaultJSONProvider):
    """Serialise anything carrying `toBSON()` by calling it.

    `toBSON()` names a database serialiser, but it is also the wire format:
    the API publishes Models by handing them to `jsonify`, and this is what
    makes that work. Nothing else knows how to turn a Model into JSON, so an
    app without this provider installed serves `TypeError` for most endpoints.

    This replaces a `flask.json.JSONEncoder` subclass. Flask 2.2 introduced
    providers and 2.3 removed the encoder hook entirely; the provider is the
    only supported extension point now.
    """

    def default(self, obj):
        if hasattr(obj, "toBSON"):
            return obj.toBSON()
        # `obj`, not the builtin `object`. The encoder this replaces passed the
        # latter, so an unserialisable value was reported as "Object of type
        # type is not JSON serializable" -- naming a class the caller never
        # supplied and sending anyone debugging it in the wrong direction.
        return super(ModelJSONProvider, self).default(obj)


def configureJSONSerialisation(app):
    """Install the Model-aware JSON provider on `app`.

    Split out of `Application.__init__` so it can be tested without standing up
    the KEGG singleton, the scheduler and the job queue -- the previous wiring
    was one line inside that constructor and consequently untested, which is
    how it came to be silently skipped on Flask >= 2.3.
    """
    app.json = ModelJSONProvider(app)
    return app

/*********************************************************************
 * GLOBAL SETTINGS           *****************************************
 **********************************************************************/
APP_VERSION = "v1.0.0";
SERVER_URL = "";
//SERVER_PORT = ":8080";
PAINTOMICS_EMAIL_DOMAIN = "paintomics.uv.es";
/*********************************************************************
 * LOCAL INSTANCE DEFAULTS   *****************************************
 **********************************************************************/
/* One box on the upload form is ticked here rather than in the form: the AI
   interpretation consent.

   It is a *consent*, not a preference. Ticking it sends pathway summaries,
   feature lists, per-feature values and the experiment design text to whatever
   LLM endpoint the server is configured for -- by default a gateway operated
   by IIIA-CSIC, not a commercial API, but that is a server setting and not
   something this file can assert; SERVER_URL_AI_PROVIDER below is how the
   interface finds out. A pre-ticked consent box is not consent under GDPR
   Art. 4(11) / Art. 7 -- it has to be an affirmative act. On localhost the
   only person whose data is at stake is the one who ticked it, so the reason
   does not apply; anywhere else it does, and the box stays as the visitor
   found it.

   Hence the hostname test: on for 127.0.0.1 / localhost, off for every
   deployed host, with no build step or separate config file to keep in sync.
   The server side is untouched -- it still reads the submitted value and
   AIInterpretServlet still refuses any job whose stored record says no.

   DEFAULT_REACTOME_ENABLED used to live here, gated the same way, and no
   longer exists. Reactome is not a local-development preference: it is either
   installed for the chosen organism or it is not, which is a fact about the
   server and not about who is looking at it. SERVER_URL_GET_ORGANISM_DATABASES
   below answers that per organism, and step 1 ticks every database the answer
   contains on every host. */
IS_LOCAL_INSTANCE = ["localhost", "127.0.0.1", "[::1]", "::1"].indexOf(
	window.location.hostname) !== -1;
DEFAULT_AI_CONSENT_ENABLED = IS_LOCAL_INSTANCE;
/*********************************************************************
 * PATHWAY ACQUISITION SERVICES URLS         *************************
 *********************************************************************/
SERVER_URL_PA_STEP1 = SERVER_URL + "pa_step1";
SERVER_URL_PA_EXAMPLE_STEP1 = "pa_step1/example";
SERVER_URL_PA_STEP2 = SERVER_URL + "pa_step2";
SERVER_URL_PA_STEP3 = SERVER_URL + "pa_step3";
SERVER_URL_PA_HUB_SUBGRAPH = SERVER_URL + "pa_hub_subgraph";
SERVER_URL_PA_HUB_FEATURE  = SERVER_URL + "pa_hub_feature";
SERVER_URL_PA_HUB_NAMES    = SERVER_URL + "pa_hub_names";
/* Step 2's "Choose for me". Two URLs because the work goes on the server's job
   queue rather than into the request: uWSGI serves this site on four threads,
   so a route that waited on the LLM gateway would be an outage rather than a
   slow response. See CompoundSuggestionServlet.py. */
SERVER_URL_PA_SUGGEST_COMPOUNDS = SERVER_URL + "pa_suggest_compounds";
SERVER_URL_PA_SUGGEST_COMPOUNDS_STATUS = SERVER_URL + "pa_suggest_compounds_status";
SERVER_URL_PA_SAVE_IMAGE = SERVER_URL + "pa_save_image";
SERVER_URL_PA_SAVE_VISUAL_OPTIONS = SERVER_URL + "pa_save_visual_options";
SERVER_URL_PA_PATHWAY_EVIDENCE = SERVER_URL + "pa_pathway_evidence";
SERVER_URL_PA_RECOVER_JOB = SERVER_URL + "pa_recover_job";
SERVER_URL_PA_TOUCH_JOB = SERVER_URL + "pa_touch_job";
SERVER_URL_PA_SAVE_SHARING_OPTIONS = SERVER_URL + "pa_save_sharing_options";
SERVER_URL_PA_APPLY_REPLICATE_MAPPING = SERVER_URL + "pa_apply_replicate_mapping";
SERVER_URL_JOB_STATUS= SERVER_URL + "check_job_status";
/* Posted once a finished or failed job's answer has been received, so the
   server can drop its copy. The result is kept until then (or for ten
   minutes) so a status poll whose response was lost in transit can be
   retried: see Queue.deliver_result in PySiQ.py. */
SERVER_URL_JOB_RESULT_ACK = SERVER_URL + "ack_job_result";
SERVER_URL_GET_CLUSTER_IMAGE= SERVER_URL + "get_cluster_image";
SERVER_URL_GET_MESSAGE = SERVER_URL + "um_get_message";
SERVER_URL_ADJUST_PVALUES = SERVER_URL + "pa_adjust_pvalues";
SERVER_URL_UPDATE_METAGENES = SERVER_URL + "pa_get_clusters";

/*********************************************************************
 * AI INTERPRETATION SERVICES URLS         **************************
 *********************************************************************/
SERVER_URL_AI_INTERPRET_INITIATE = SERVER_URL + "ai_interpret_initiate";
SERVER_URL_AI_INTERPRET_STATUS = SERVER_URL + "ai_interpret_status";
SERVER_URL_AI_INTERPRET_REPORT = SERVER_URL + "ai_interpret_report";
SERVER_URL_AI_INTERPRET_CHAT = SERVER_URL + "ai_interpret_chat";
SERVER_URL_AI_INTERPRET_PATHWAY = SERVER_URL + "ai_interpret_pathway";
SERVER_URL_AI_GENERATE_EXP_DESIGN = SERVER_URL + "ai_generate_exp_design";
/* Who the analysis summaries are actually sent to. The provider, its host and
   the model are all chosen server-side by AI_LLM_PROVIDER and are all
   env-overridable, so the consent notice asks rather than assumes -- naming a
   provider from the client would be a guess, and a guess in a consent notice
   is a false statement. See getAIProviderInfo() in AIInterpretServlet.py. */
SERVER_URL_AI_PROVIDER = SERVER_URL + "ai_provider";
AI_POLL_INTERVAL = 3000;
/* Polls before "Choose for me" gives up. The server caps one gateway batch at
   180s (DEFAULT_BATCH_BUDGET_SECONDS) and nothing caps the number of batches --
   a job with more than 30 residual names runs two or more, so 100 x 3s = 5 min
   was SHORTER than the server's own worst case and the browser abandoned runs
   that were still executing. PySiQ never enforces the timeout passed to
   enqueue, so the worker would keep going with nobody left to collect. 400 x 3s
   = 20 min covers six slow batches. */
AI_SUGGEST_MAX_POLLS = 400;

/* Consecutive dropped requests before the poll reports the transport error
   rather than a timeout. Separate from the ceiling above: one blip must not end
   a run, but a server that is down should say so. */
AI_SUGGEST_MAX_TRANSPORT_FAILURES = 5;
// How many consecutive unhandled refusals from /ai_interpret_status before the
// widget gives up and says so. A refusal it recognises as permanent -- the job
// no longer exists -- stops immediately and does not consume these; this is the
// ceiling for everything else, so a server that has stopped answering produces
// a message instead of a progress bar that says "Starting..." for as long as
// the tab is open.
AI_POLL_MAX_FAILURES = 5;
CHAT_POLL_INTERVAL = 1500;

/*********************************************************************
 * DATA MANIPULATION SERVICES URLS         ***************************
 *********************************************************************/
SERVER_URL_DM_UPLOAD_FILE = SERVER_URL + "dm_upload_file";
SERVER_URL_DM_GET_MYFILES = SERVER_URL + "dm_get_myfiles";
SERVER_URL_DM_GET_GTFFILES = SERVER_URL + "dm_get_gtffiles";
SERVER_URL_DM_DOWNLOAD_FILE = SERVER_URL + "dm_downloadFile";
SERVER_URL_DM_VIEW_FILE = SERVER_URL + "dm_viewFile";
SERVER_URL_DM_DELETE_FILE = SERVER_URL + "dm_delete_file";
SERVER_URL_DM_GET_MYJOBS = SERVER_URL + "dm_get_myjobs";
SERVER_URL_DM_DELETE_JOB = SERVER_URL + "dm_delete_job";
SERVER_URL_DM_FROMBED2GENES = SERVER_URL + "dm_fromBEDtoGenes";
SERVER_URL_DM_FROMMIRNA2GENES = SERVER_URL + "dm_fromMiRNAtoGenes";
SERVER_URL_DM_EXAMPLE_FROMBED2GENES = SERVER_URL + "dm_fromBEDtoGenes/example";
SERVER_URL_DM_EXAMPLE_FROMMIRNA2GENES = SERVER_URL + "dm_fromMiRNAtoGenes/example";
SERVER_URL_DM_FROMMORE2GENES = SERVER_URL + "dm_fromMOREtoGenes";
SERVER_URL_DM_EXAMPLE_FROMMORE2GENES = SERVER_URL + "dm_fromMOREtoGenes/example";
/* Which regulatory engines this host can run -- Rust PLS1, R PLS1, R MLR --
   and why any of them cannot. Asked rather than assumed for the same reason as
   SERVER_URL_AI_PROVIDER above: the answer depends on what is installed on the
   server, and the deployed image carries R without the MORE package, so a list
   written into the client would offer options that fail inside the job.
   See describeMOREBackends() in MOREServlet.py. */
SERVER_URL_MORE_BACKENDS = SERVER_URL + "more_backends";
SERVER_URL_DM_SEND_REPORT = SERVER_URL + "dm_sendReport";
/*********************************************************************
 * EXAMPLE DATASET CATALOGUE                 *************************
 *********************************************************************/
// The picker behind "Load example". Every URL above ending in "/example"
// accepts an optional "/<scenario-id>" after it -- the routes use Flask's
// <path:> converter, so the extra segment needs no new endpoint.
SERVER_URL_EXAMPLE_DATASETS = SERVER_URL + "example_datasets";
// The same catalogue as a zip, behind every "Download example data" button.
// Built from the manifest on request, so what you can load is what you get --
// the static resources/paintomics_example_data.zip this replaced was assembled
// in 2017 and held a dataset the picker stopped offering. Accepts an optional
// "?scenario=<id>" for one dataset or "?pipeline=<name>" for one entry point's.
SERVER_URL_EXAMPLE_DATASETS_DOWNLOAD = SERVER_URL_EXAMPLE_DATASETS + "/download";
/*********************************************************************
 * KEGG DATA URLS                          ***************************
 *********************************************************************/
SERVER_URL_GET_PATHWAY_NETWORK = SERVER_URL + "kegg_data/pathway_network";
SERVER_URL_GET_PATHWAY_NETWORK_REACTOME = SERVER_URL + "kegg_data/pathway_network_reactome";
SERVER_URL_GET_PATHWAY_NETWORK_MAPMAN = SERVER_URL + "kegg_data/pathway_network_mapman";
SERVER_URL_GET_PATHWAY_NETWORK_OMNIPATH = SERVER_URL + "kegg_data/pathway_network_omnipath";
// One OmniPath pathway's own interaction network, fetched as
// <organism>/<pathwayID> only when that pathway is opened. OmniPath ships no
// diagram, so this IS the pathway view rather than an overview of it.
SERVER_URL_GET_OMNIPATH_NETWORK = SERVER_URL + "omnipath_network";

SERVER_URL_GET_AVAILABLE_SPECIES = SERVER_URL + "kegg_data/species.json";
// {organism: [databases]} for every organism this deployment installed, read
// from each organism's own MongoDB rather than from a static list. Step 1's
// database checkboxes are drawn from it: a database the map does not name for
// the chosen organism cannot be selected, because selecting it would be
// discarded by PathwayAcquisitionServlet anyway.
SERVER_URL_GET_ORGANISM_DATABASES = SERVER_URL + "organism_databases";
/*********************************************************************
 * USER MANIPULATION SERVICES URLS         ***************************
 *********************************************************************/
SERVER_URL_UM_SIGNIN = SERVER_URL + "um_signin";
SERVER_URL_UM_SIGNOUT = SERVER_URL + "um_signout";
SERVER_URL_UM_SIGNUP = SERVER_URL + "um_signup";
SERVER_URL_UM_CHANGEPASS = SERVER_URL + "um_changepassword";
SERVER_URL_UM_NEWGUESTSESSION = SERVER_URL + "um_guestsession";
SERVER_URL_UM_NEWNOLOGINSESSION = SERVER_URL + "um_nologinsession";
SERVER_URL_UM_RESETPASSWORD = SERVER_URL + "um_resetpassword";
/*********************************************************************
 * OTHER SETTINGS            *****************************************
 **********************************************************************/
forceRefresh = false;
nObservers = 0;
debugging = true;
messageDialog = null;
UPLOAD_TIMEOUT=120; /*IN SECONDS*/
MAX_LIVE_JOB=365; /*IN DAYS*/
CHECK_STATUS_TIMEOUT=5000; /*MILISECONDS*/
/* A status poll that gets no READABLE answer -- nginx's 502/504 page, the
   empty body of a dropped connection, a request the browser timed out -- is
   retried rather than reported. These are the retry's numbers; the rule is
   statusPollRetryDelay in Util.js, and both polls (check_job_status in
   JobController.js, ai_interpret_status in PA_Step3Views.js) use it.

   The request timeout must be LONGER than the proxy's: 60 s on
   paintomics.uv.es, nginx's default uwsgi_read_timeout. Shorter, and the
   browser gives up while the uWSGI thread is still busy with that poll, then
   retries on top of it -- two of four threads spent on one client at the
   moment the server is weakest. At 65 s the proxy's 504 always lands first.

   The budget is wall-clock time since the first unanswered request, not a
   number of attempts: five 65 s timeouts and five instant 502s are very
   different waits. Five minutes covers the longest stall measured (61 s, the
   2026-09-11 poll) several times over, and is half the server's
   DELIVERED_RESULT_TTL, so every retry lands while the result is still kept. */
JOB_STATUS_REQUEST_TIMEOUT = 65000;   /*MILISECONDS*/
JOB_STATUS_RETRY_FIRST_DELAY = 5000;  /*MILISECONDS; doubles each time*/
JOB_STATUS_RETRY_MAX_DELAY = 60000;   /*MILISECONDS*/
JOB_STATUS_RETRY_BUDGET = 300000;     /*MILISECONDS since the first unanswered request*/
MAX_PATHWAYS_OPENED=5;

"""PaintOmics AI server configuration template.

Copy to `PaintomicsServer/src/conf/serverconf.py` and edit. That path is
gitignored: the real config holds credentials and MUST NOT be committed.

    cp PaintomicsServer/src/resources/example_serverconf.py \
       PaintomicsServer/src/conf/serverconf.py

Every secret is read from the environment with an empty default. Nothing in
this file may ever carry a real key, token or password -- enforced by
`src/tests/test_release_hygiene.py`.

Defaults target the containerised deployment (see `deploy/README.md`).
"""
import os
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Local secrets: PaintomicsServer/.env
# ---------------------------------------------------------------------------
# Every secret below is read with os.getenv, so it lives in the ENVIRONMENT and
# not in this file. That is right for deployment and hostile to local work: a
# key exported in one shell is gone in the next terminal, gone under the IDE,
# gone in a test runner -- and this file is gitignored, so a key pasted into it
# survives locally but vanishes on any fresh checkout or reinstall. The usual
# symptom is "I set the key and AI interpretation still reports no token".
#
# So a `.env` beside the server is loaded here, before anything calls getenv:
#
#     PaintomicsServer/.env
#     AI_CSIC_API_KEY=sk-...
#
# `.env` is already gitignored (.gitignore:81), so it cannot be pushed.
#
# Two properties worth keeping:
#   * a REAL environment variable always wins. Production sets these through
#     systemd or the container, and a stray .env on the box must never override
#     what the deployment configured -- hence setdefault, never assignment.
#   * failure is silent. A missing or malformed .env leaves the server exactly
#     as it was, because a config module that raises on import takes the whole
#     servlet down and the traceback surfaces far from its cause.
def _load_dotenv():
    _here = os.path.dirname(os.path.abspath(__file__))
    for _candidate in (
        os.path.join(_here, "..", "..", ".env"),        # PaintomicsServer/.env
        os.path.join(_here, "..", "..", "..", ".env"),  # repository root
    ):
        _path = os.path.abspath(_candidate)
        if not os.path.isfile(_path):
            continue
        try:
            with open(_path) as _fh:
                for _line in _fh:
                    _line = _line.strip()
                    if not _line or _line.startswith("#") or "=" not in _line:
                        continue
                    _key, _, _value = _line.partition("=")
                    _key = _key.strip()
                    if _key.startswith("export "):
                        _key = _key[len("export "):].strip()
                    _value = _value.strip().strip('"').strip("'")
                    if _key:
                        os.environ.setdefault(_key, _value)
        except OSError:
            pass


_load_dotenv()


# ========== SERVER SETTINGS ==========
SERVER_HOST_NAME          = os.getenv("SERVER_HOST_NAME", "0.0.0.0")   # 0.0.0.0 listens on all interfaces
SERVER_PORT_NUMBER        = int(os.getenv("SERVER_PORT_NUMBER", "8000"))
SERVER_ALLOW_DEBUG        = os.getenv("SERVER_ALLOW_DEBUG", "false").lower() == "true"  # NEVER true in production
SERVER_SUBDOMAIN          = os.getenv("SERVER_SUBDOMAIN", "")          # e.g. "paintomics" if served at myserver.com/paintomics
SERVER_MAX_CONTENT_LENGTH = 100 * pow(1024, 2)   # nginx client_max_body_size sits ABOVE this on purpose
# Werkzeug 3.1 defaults max_form_memory_size to 500 kB and applies it to
# urlencoded bodies, which is below a large pathway SVG export. Keep it equal
# to SERVER_MAX_CONTENT_LENGTH so one limit governs the request size.
SERVER_MAX_FORM_MEMORY_SIZE = SERVER_MAX_CONTENT_LENGTH
ADMIN_ACCOUNTS            = os.getenv("ADMIN_ACCOUNTS", "admin")

# ========== FILES SETTINGS ==========
ROOT_DIRECTORY            = ""                                         # Blank = auto-detect
CLIENT_TMP_DIR            = os.getenv("PAINTOMICS_CLIENT_TMP", "/data/CLIENT_TMP") + "/"
KEGG_DATA_DIR             = os.getenv("PAINTOMICS_KEGG_DATA", "/data/KEGG_DATA") + "/"
MAX_CLIENT_SPACE          = 200 * pow(1024, 2)

# How long a job survives after it was last OPENED, not after it was created:
# the client POSTs /pa_touch_job every time a job is shown, which rewrites
# accessDate and clears any reminder already sent. A job someone keeps coming
# back to therefore never expires.
#
# These two numbers are the ones the interface promises. pa_recover_job tells
# every user whose job has gone:
#
#     "jobs are automatically removed after 7 days for guests and 14 days for
#      registered users"
#
# and until now the configuration said 90 and 365 while the anonymous jobs that
# make up most of the server were never examined at all -- so the sentence was
# wrong in both directions at once. If you change these, change that sentence
# too; test_retention_matches_the_promise fails if they drift apart again.
MAX_GUEST_JOB_DAYS        = 7      # anonymous jobs, and jobs of is_guest accounts
MAX_JOB_DAYS              = 14     # jobs belonging to a registered account

# Separate question, and deliberately not one of the two above: how long a
# GUEST ACCOUNT itself survives without being logged into. Its jobs are already
# gone long before this, under MAX_GUEST_JOB_DAYS.
MAX_GUEST_DAYS            = 90
MAX_NUMBER_FEATURES       = 1000000

# ========== MONGO DB SETTINGS ==========
# In Compose this is the service name ("mongo"), reachable only on the internal
# network. MongoDB is never published to the host.
MONGODB_HOST      = os.getenv("MONGODB_HOST", "localhost")
MONGODB_PORT      = int(os.getenv("MONGODB_PORT", "27017"))
MONGODB_DATABASE  = os.getenv("MONGODB_DATABASE", "PaintomicsDB")

# ========== MULTI-THREADING OPTIONS ==========
# Concurrency comes from threads only. src/common/PySiQ.py keeps the job queue
# in process memory, so uWSGI must run processes = 1; a second process would
# get its own empty queue and silently drop jobs.
MAX_THREADS      = int(os.getenv("MAX_THREADS", "6"))
MAX_WAIT_THREADS = 900                                                 # seconds
N_WORKERS        = int(os.getenv("N_WORKERS", "4"))

# ========== CACHE SIZES ==========
JOB_CACHE_MAX_SIZE  = 50
KEGG_CACHE_MAX_SIZE = 25

# ========== DOWNLOAD SETTINGS ==========
DOWNLOAD_DELAY_1 = 2
DOWNLOAD_DELAY_2 = 2
MAX_TRIES_1 = 3
MAX_TRIES_2 = 5

# ========== EMAIL CONFIGURATION (SMTP) ==========
# Registration and password-reset mail. With SMTP_PASSWORD unset, account
# activation email cannot be delivered and new users cannot complete signup.
EMAIL_PROVIDER      = "smtp"
EMAIL_FROM_ADDRESS  = os.getenv("EMAIL_FROM_ADDRESS", "noreply@example.org")
EMAIL_FROM_DISPLAY  = os.getenv("EMAIL_FROM_DISPLAY", "PaintOmics")
SMTP_HOST           = os.getenv("SMTP_HOST", "smtp.sendgrid.net")
SMTP_PORT           = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME       = os.getenv("SMTP_USERNAME", "apikey")             # SendGrid expects the literal "apikey"
SMTP_PASSWORD       = os.getenv("SMTP_PASSWORD", "")                   # SECRET: the SendGrid API key
SMTP_USE_TLS        = True

# Backwards compatibility for legacy SMTP-based imports
smpt_sender      = EMAIL_FROM_ADDRESS
smpt_sender_name = EMAIL_FROM_DISPLAY

# ========== WEB-FACING CONSTANTS ==========
# PAINTOMICS_BASE_URL must be the externally reachable URL; it is embedded in
# activation links sent by email, so localhost breaks registration in production.
PAINTOMICS_BASE_URL     = os.getenv("PAINTOMICS_BASE_URL", "http://localhost:8000").rstrip("/")
# The mark shown in every outgoing email. It has to be a raster: Gmail and
# every Outlook build refuse an <img> whose source is an SVG, so this cannot
# point at paintomics-mark.svg even though that is the file the application
# itself uses. The PNG is rendered from that same vector by
# public_html/resources/images/build-email-mark.py.
#
# The extension is part of the path. Without it the URL 404s and every email
# renders a broken-image icon where the logo should be; the file on disk is
# public_html/resources/images/paintomics-mark-email.png
PAINTOMICS_LOGO_PATH    = os.getenv("PAINTOMICS_LOGO_PATH", "/resources/images/paintomics-mark-email.png")
PAINTOMICS_LOGO_URL     = f"{PAINTOMICS_BASE_URL}{PAINTOMICS_LOGO_PATH}"
PAINTOMICS_LOGIN_URL    = os.getenv("PAINTOMICS_LOGIN_URL", f"{PAINTOMICS_BASE_URL}/")
PAINTOMICS_DOCS_URL     = os.getenv("PAINTOMICS_DOCS_URL", "https://conesalab.github.io/PaintOmics/")
# `or`, not os.getenv's default, because deploy/compose.yaml passes this as
# ${PAINTOMICS_EMAIL_DOMAIN:-}: absent from .env, the container still gets the
# variable SET to "". os.getenv returns that empty string rather than the
# default, so the fallback below never ran and guest accounts were mailed as
# "guest1234@" -- a domainless address, with nothing logged either side.
PAINTOMICS_EMAIL_DOMAIN = (
    os.getenv("PAINTOMICS_EMAIL_DOMAIN", "").strip()
    or urlparse(PAINTOMICS_BASE_URL).netloc
    or "example.org"
)
EMAIL_REPORT_RECIPIENTS = [
    email.strip()
    for email in os.getenv("EMAIL_REPORT_RECIPIENTS", "").split(",")
    if email.strip()
]

# ========== AI INTERPRETATION ==========
AI_INTERPRETATION_ENABLED = os.getenv("AI_INTERPRETATION_ENABLED", "true").lower() == "true"

# Step 2's "Choose for me" button. Separate from AI_INTERPRETATION_ENABLED so a
# deployment can offer compound disambiguation without the literature pipeline
# or the other way round -- they cost very differently (one short batched call
# against a multi-phase run with PubMed retrieval). Both must be on for the
# button to appear; AI_INTERPRETATION_ENABLED remains the master switch.
AI_COMPOUND_SUGGESTIONS_ENABLED = os.getenv("AI_COMPOUND_SUGGESTIONS_ENABLED", "true").lower() == "true"

# AI-assisted conversion of uploaded files. Ships inert: it spends gateway
# quota that is shared with AI report generation, so a deployment opts in.
AI_INPUT_CONVERTER = os.getenv("AI_INPUT_CONVERTER", "false").lower() == "true"

# Provider used by src/classes/AIInterpret/. "csic" is the deployment default:
# a free OpenAI-compatible vLLM gateway run by IIIA-CSIC. Tokens are
# self-service from https://console.llm.iiia.es (CSIC SSO).
AI_LLM_PROVIDER = os.getenv("AI_LLM_PROVIDER", "csic")
AI_PROVIDERS = {
    "csic": {
        "api_base": os.getenv("AI_CSIC_API_BASE", "https://llm.iiia.es/v1"),
        "api_key": os.getenv("AI_CSIC_API_KEY", ""),                   # SECRET
        # Pin a dated snapshot rather than the "default/llm" alias: the service
        # asks scientific users to name an explicit model for reproducibility,
        # and an alias can be repointed under a running deployment.
        "model": os.getenv("AI_CSIC_MODEL", "deepseek-ai/DeepSeek-V4-Flash-0731"),
        # Asked, in order, when the pinned model is not being served (a 5xx,
        # a dead backend, a timeout -- never a 4xx that every model would
        # answer alike). "default/llm" is the gateway's own alias, which its
        # operator keeps pointed at a working model. Comma-separated; empty
        # switches the fallback off. Every result records the model that
        # actually answered, so the pin stays honest.
        "fallback_models": os.getenv("AI_CSIC_FALLBACK_MODELS", "default/llm"),
    },
    "dashscope": {
        "api_base": os.getenv("AI_DASHSCOPE_API_BASE", "https://coding-intl.dashscope.aliyuncs.com/v1"),
        "api_key": os.getenv("AI_DASHSCOPE_API_KEY", ""),              # SECRET
        "model": os.getenv("AI_DASHSCOPE_MODEL", "qwen3.5-plus"),
    },
    "openrouter": {
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": os.getenv("AI_OPENROUTER_API_KEY", ""),             # SECRET
        "model": os.getenv("AI_OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"),
    },
}

# PubMed (NCBI E-utilities): 10 req/s with a key, 3 without.
AI_PUBMED_EMAIL   = os.getenv("AI_PUBMED_EMAIL", "")
AI_PUBMED_API_KEY = os.getenv("AI_PUBMED_API_KEY", "")                 # SECRET

# Pipeline
AI_MAX_PATHWAYS = 15
AI_PATHWAYS_PER_BATCH = 5
AI_PAPERS_PER_PATHWAY = 5
AI_TEMPERATURE = 0.3
AI_MAX_CONCURRENT_PIPELINES = 2

# Full-text fetching & verification
AI_MAX_SECTION_CHARS = int(os.getenv("AI_MAX_SECTION_CHARS", "12000"))
AI_MAX_VERIFICATION_ITERATIONS = int(os.getenv("AI_MAX_VERIFICATION_ITERATIONS", "3"))
AI_VERIFICATION_FUZZY_THRESHOLD = float(os.getenv("AI_VERIFICATION_FUZZY_THRESHOLD", "0.75"))
AI_VERIFICATION_PROVIDER = os.getenv("AI_VERIFICATION_PROVIDER", "")
AI_EUROPEPMC_DELAY = float(os.getenv("AI_EUROPEPMC_DELAY", "0.2"))

# Phase 1: Triage
AI_MAJOR_PATHWAY_MIN_OMICS = int(os.getenv("AI_MAJOR_PATHWAY_MIN_OMICS", "2"))
AI_MAJOR_PATHWAY_MAX_PVAL = float(os.getenv("AI_MAJOR_PATHWAY_MAX_PVAL", "0.05"))

# Phase 2: Search Planner
AI_MAX_SEARCH_TASKS = int(os.getenv("AI_MAX_SEARCH_TASKS", "12"))
AI_SEARCH_SUBAGENT_WORKERS = int(os.getenv("AI_SEARCH_SUBAGENT_WORKERS", "4"))
# Citations are verified one sub-agent call each and are independent, so they
# run concurrently. Same default as the search workers, so the pipeline never
# issues more parallel LLM calls than it already did.
AI_VERIFICATION_WORKERS = int(os.getenv("AI_VERIFICATION_WORKERS", "4"))
AI_PAPERS_PER_SEARCH_TASK = int(os.getenv("AI_PAPERS_PER_SEARCH_TASK", "5"))
AI_PAPERS_KEPT_PER_TASK = int(os.getenv("AI_PAPERS_KEPT_PER_TASK", "3"))
AI_SEARCH_PLANNER_TEMPERATURE = float(os.getenv("AI_SEARCH_PLANNER_TEMPERATURE", "0.4"))
# "Choose for me" on step 2: residual compound sets go to the gateway thirty
# per call. The calls fan out over this many threads inside the one queue job
# (they are independent; the worker is idle on I/O), and a run sends at most
# MAX_SETS -- the rest are left to the user -- so a click can never hold a
# queue worker for more than a few batch budgets.
COMPOUND_SUGGESTION_WORKERS = int(os.getenv("COMPOUND_SUGGESTION_WORKERS", "3"))
COMPOUND_SUGGESTION_MAX_SETS = int(os.getenv("COMPOUND_SUGGESTION_MAX_SETS", "90"))
AI_SEARCH_SUBAGENT_TEMPERATURE = float(os.getenv("AI_SEARCH_SUBAGENT_TEMPERATURE", "0.2"))

# ========== MORE BACKEND ==========
# Absolute path to `more-rs`, the Rust port of MORE. It takes the same
# arguments as runMORE.R and was measured against it on the bundled
# 06-regulatory-more example: six of the seven output files byte-identical, the
# seventh (the rpc table) holding the same rows in a different order.
#
# **PLS1 runs on the port by default.** Blank -- the default -- does NOT mean
# "use R": it means "go and find a binary", which MOREServlet._discoverMoreRs
# does by looking beside runMORE.R at src/common/bioscripts/more-rs and then on
# PATH. A host with no binary finds nothing and runs R, exactly as before, so
# the default is safe on a machine that has never heard of the port.
#
# Set this to a path to name one explicitly, or to `off` to force R for every
# job. MLR always runs on R whatever this says -- R's MLR path draws from the
# RNG in three places, so the port can only sit inside R's own seed band rather
# than reproduce it, and that is a difference to opt into rather than impose.
# See MOREServlet._resolveMOREBackend for the full reasoning.
MORE_RS_BINARY = os.getenv("PAINTOMICS_MORE_RS", "")

# Seconds a single MORE analysis may be *predicted* to take before the server
# refuses it at submit time, instead of accepting it and killing it when the
# queue timeout expires.
#
# This is not a new limit -- MOREServlet already enqueues every MORE job with a
# 1800 s timeout. What is new is finding out before the wait rather than after
# it. Measured on the STATegra TF->gene set (9,835 genes, 36 samples, ~30
# regulators/gene), one process at a time:
#
#     R MLR      ~3.4 h        R PLS1     ~1.7 h        more-rs PLS1  ~9 s
#
# so on an R-only host this is reached by ordinary genome-scale data, and the
# guard covers PLS1 as well as MLR. A host running the port is effectively
# never gated, because the estimate is keyed on the engine that will actually
# run (see MORECostModel).
#
# Raise it only in step with the queue timeout -- a budget above that just
# moves the failure back to where it was. Set it to 0 to disable the check.
MORE_RUNTIME_BUDGET_SECONDS = int(os.getenv("PAINTOMICS_MORE_RUNTIME_BUDGET", "1800"))

# Metabolite class activity: label shuffles behind the permutation test that
# runs when a compound omic carries replicates and a design. The smallest
# reachable p is 1/(N+1); 2000 costs ~1 s on a 60 x 36 matrix. Floor of 100.
CLASS_ACTIVITY_PERMUTATIONS = int(os.getenv("PAINTOMICS_CLASS_ACTIVITY_PERMUTATIONS", "2000"))

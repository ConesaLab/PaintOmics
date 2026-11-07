//# sourceURL=app.js
/*
 * (C) Copyright 2014 The Genomics of Gene Expression Lab, CIPF
 * (http://bioinfo.cipf.es/aconesawp) and others.
 *
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the GNU Lesser General Public License
 * (LGPL) version 3 which accompanies this distribution, and is available at
 * http://www.gnu.org/licenses/lgpl.html
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
 * Lesser General Public License for more details.
 *
 * Contributors:
 *     Rafael Hernandez de Diego
 *     rhernandez@cipf.es
 *     Ana Conesa Cegarra
 *     aconesa@cipf.es
 *
 * THIS FILE CONTAINS THE FOLLOWING COMPONENT DECLARATION
 * - Application
 *
 */
if (debugging === true)
    console.warn("DEBUGGING MODE IS ON.")

/********************************************
** CACHED JOB MODEL RECOVERY
**
** These live at module scope on purpose: the boot code below runs inside
** Application(), but the error dialog raised from the $(document).ready block
** calls them from an inline onclick, which resolves against window. Declaring
** them inside Application() would make that a ReferenceError, i.e. an error
** dialog whose only recovery action is itself broken.
********************************************/

/**
 * The single definition of the Dexie store used for the cached analysis.
 * Reading a cached job and throwing it away must agree on the schema, or the
 * discard silently targets a different database and the poison survives.
 */
/**
 * The contract the cached job model was written under. Bump it when the
 * server starts sending something the cached copy lacks: a row stamped with
 * an older number is one that cannot be restored, so the boot drops it from
 * both caches and the ?jobID= path recovers the job from the server. Without
 * this a browser that opened a job before an upgrade never asked again --
 * the hub view's checkSchema() learned that the hard way.
 *   2: globalExpressionData carries sampleValues / sampleRelevant.
 *   3: every pathway carries totalGenes / totalCompounds, the denominator of
 *      the network's "min features in pathway" filter. Without them the filter
 *      falls back to the installed gene-only count, which is what made a
 *      metabolomics-only job draw an empty network.
 */
var PA_JOB_CACHE_SCHEMA = 3;

function openPaintomicsDB() {
    var db = new Dexie("paintomics");
    db.version(1).stores({
        networks: 'id',
        jobs: 'jobID'
    });
    return db;
}

/**
 * Drops the cached analysis so the next boot behaves as a first visit.
 *
 * The model is cached twice - the sessionStorage "jobModel" key and a row in
 * the Dexie "jobs" table - and the boot path reads whichever it finds first.
 * Removing only one leaves the other to be restored on the next load, which
 * undoes the discard, so both go together.
 *
 * @param {String} jobID the cached job, or null/undefined to drop every cached
 *                       job (used by the manual recovery action, where the
 *                       offending ID is not necessarily known).
 * @param {Function} onDone optional, called once IndexedDB has settled -
 *                          successfully or not, because a discard we could not
 *                          complete must not hang the caller.
 */
function discardCachedJobModel(jobID, onDone) {
    var done = (typeof onDone === "function") ? onDone : function () {};

    if (window.sessionStorage) {
        try {
            sessionStorage.removeItem("jobModel");
        } catch (error) {
            console.error(Date.logFormat() + "app.js : could not remove the cached job model from sessionStorage.", error);
        }
    }

    try {
        var jobs = openPaintomicsDB().table("jobs");
        var deletion = (jobID) ? jobs.delete(jobID) : jobs.clear();
        deletion.then(function () {
            console.info(Date.logFormat() + "app.js : discarded the cached job " + (jobID || "(all)") + " from IndexedDB.");
            done();
        }).catch(function (error) {
            console.error(Date.logFormat() + "app.js : could not discard the cached job " + (jobID || "(all)") + " from IndexedDB.", error);
            done();
        });
    } catch (error) {
        // No IndexedDB at all (private mode in some browsers): the
        // sessionStorage copy is gone, which is enough to boot clean.
        console.error(Date.logFormat() + "app.js : IndexedDB is unavailable, discarded the sessionStorage copy only.", error);
        done();
    }
}

/**
 * The recovery action offered by the boot error dialog. Clears every cached
 * analysis and reloads; the jobs themselves live on the server, so nothing is
 * lost beyond the local copy.
 */
function resetPaintomicsSession() {
    if (window.sessionStorage) {
        /* The same three keys JobController.cleanStoredApplicationData() drops:
         * the view state cached alongside the model is derived from it, so
         * leaving it behind would restore half an analysis onto a clean boot. */
        try {
            sessionStorage.removeItem("pathwaysNetwork");
            sessionStorage.removeItem("visualOptions");
        } catch (error) {
            console.error(Date.logFormat() + "app.js : could not clear the cached view state.", error);
        }
    }

    discardCachedJobModel(null, function () {
        location.reload();
    });
}

/* The job this page was opened for, or null when the address names none. */
function jobIDInAddress() {
    return Ext.urlDecode(location.search.substring(1)).jobID || null;
}

/**
 * How loadModule treats a module it could not fetch. The fetch is retried
 * because a connection that drops mid-boot is usually back within moments: on
 * 2026-10-27 06:48 UTC one client's IP changed between two modules, the second
 * never reached the server, and the reload 81 s later worked. The pause is what
 * gives a retry its chance, since a dead connection fails in milliseconds.
 */
var MODULE_FETCH_ATTEMPTS = 3;
var MODULE_FETCH_RETRY_PAUSE = 500;
var MODULE_FETCH_ERROR = "ModuleFetchError";

/**
 * Waits on the main thread. Boot is synchronous, launch() uses the modules the
 * moment its loops end, so there is nothing a timer could hand back to. Only a
 * failed fetch ever comes here, and a page still booting has nothing to freeze.
 */
function pauseBoot(milliseconds) {
    var until = Date.now() + milliseconds;
    while (Date.now() < until) {
        /* wait */
    }
}

/**
 * One attempt at one module. Says which of the two ways it can fail happened,
 * because only the first is worth asking again: a request that got no usable
 * answer, or a script that arrived and then threw.
 *
 * jQuery 3.1 evaluates the fetched text through an injected <script> (DOMEval),
 * so an exception in it never reaches jQuery: it reports success and the browser
 * raises the exception on window instead. The listener is what sees it, and the
 * request being synchronous means nothing else can run while it is attached.
 * textStatus "parsererror" is how the other jQuery versions report the same
 * thing, so an upgrade does not turn it into a dropped connection.
 *
 * It also evaluates the body of an HTTP error, so nginx's 502 page raises
 * "Unexpected token '<'" here as well (measured in the browser). That is why the
 * caller asks about the request first: a failed request's body says nothing about
 * the module.
 */
function fetchModuleOnce(file) {
    var outcome = {failedRequest: null, evaluationError: null};
    var onScriptError = function (event) {
        outcome.evaluationError = event.error || new Error(event.message);
    };

    window.addEventListener("error", onScriptError);
    try {
        $.ajax({
            url: file,
            async: false, dataType: "script",
            error: function (jqXHR, textStatus, errorThrown) {
                if (textStatus === "parsererror") {
                    outcome.evaluationError = errorThrown;
                } else {
                    outcome.failedRequest = jqXHR;
                }
            }
        });
    } finally {
        window.removeEventListener("error", onScriptError);
    }
    return outcome;
}

/* The exception a module threw while evaluating, naming the module. */
function moduleEvaluationError(file, thrown) {
    var error = (thrown instanceof Error) ? thrown : new Error(String(thrown));
    error.moduleFile = file;
    return error;
}

/* A module that could not be fetched; showBootFailureMessage recognises the name. */
function moduleFetchError(file, jqXHR) {
    var httpStatus = Number(jqXHR && jqXHR.status) || 0;
    var error = new Error("Could not fetch " + file + " after " + MODULE_FETCH_ATTEMPTS + " attempts (" +
        (httpStatus > 0 ? "HTTP " + httpStatus : "no answer from the server") + ").");
    error.name = MODULE_FETCH_ERROR;
    error.moduleFile = file;
    error.httpStatus = httpStatus;
    return error;
}

/**
 * The exception as the report should carry it. Stack included, cut to what fits
 * a mail: "ReferenceError: x is not defined" with no location is a report nobody
 * can act on.
 */
function describeBootError(error) {
    if (!error || !(error.name || error.message)) {
        return "Boot failure: " + String(error);
    }
    var described = (error.name || "Error") + ": " + (error.message || "(no message)");
    if (error.moduleFile) {
        described = "While evaluating " + error.moduleFile + ": " + described;
    }
    return error.stack ? described + "\n" + String(error.stack).slice(0, 1500) : described;
}

/**
 * The dialog shown when the application cannot boot.
 *
 * It used to say that the analysis stored in this browser could not be restored,
 * whatever the failure was. restoreCachedModel contains that failure on its own,
 * so what reached this dialog was everything else, and the report of 2026-10-27
 * blamed a stored analysis for a connection that had dropped. The three cases
 * here are the three we can tell apart: a module that never arrived, where nothing
 * of the user's is involved and only a reload helps; a module that arrived and
 * threw, where launch() has not read either store yet, so discarding them could
 * not help and the exception goes with the report; and any other exception, whose
 * cause is not known, so the discard is offered as something that may help and the
 * exception goes with the report.
 *
 * It also used to advise clearing the browser cache, which is both wrong - that
 * touches neither sessionStorage nor IndexedDB, the two places a stored analysis
 * actually lives - and unactionable, so users saw the same blank page on every
 * reload.
 */
function showBootFailureMessage(error) {
    console.error(Date.logFormat() + "app.js : the application failed to start.", error);

    if (error && error.name === MODULE_FETCH_ERROR) {
        // Not reportable: the report would say what this says, and there is no
        // fault of ours behind it to act on. A file that is really gone from the
        // server keeps failing, which is what the address below is for.
        var cause = (error.httpStatus > 0) ? "the server answered HTTP " + error.httpStatus : "the connection dropped";
        showErrorMessage("PaintOmics AI could not start", {
            message: "The page could not finish loading because " + cause + " while fetching [b]" + error.moduleFile + "[/b]." +
                "</br>Your analysis and your jobs are not affected." +
                "</br><a href='#' onclick='location.reload(); return false;'>Reload the page</a> to try again." +
                "</br>If it keeps happening, contact us at <a href='mailto:paintomicsai@gmail.com'>paintomicsai@gmail.com</a>.",
            showButton: true,
            showReportButton: false
        });
        return;
    }

    // No leading </br> (it drew an empty first line), and the pointer names the
    // real menu path; a guest has no storage, so the job link comes first.
    var recovery = (error && error.moduleFile) ?
        "</br>Your analysis and your jobs are not affected." +
        "</br><a href='#' onclick='location.reload(); return false;'>Reload the page</a> to try again." :
        "</br>If you had an analysis open in this browser, this may help: " +
        "<a href='#' onclick='resetPaintomicsSession(); return false;'>Discard the stored analysis and reload</a>" +
        " &ndash; your jobs are kept on the server, so you can reopen them from their job link or, when signed in," +
        " from [b]Storage &rsaquo; My files and Jobs[/b].";
    showErrorMessage("PaintOmics AI could not start", {
        message: "An unexpected error stopped the page from starting." + recovery +
            "</br>If the error persists, press Report error or contact us at <a href='mailto:paintomicsai@gmail.com'>paintomicsai@gmail.com</a>.",
        hidden: describeBootError(error),
        showButton: true
    });
}

function Application() {
    this.models = ['JobInstanceModels', "FeatureModels", "PathwayModels"];
    this.views = [
        'MainView',
        'PathwayAcquisitionViews/PA_Step1Views',
        "PathwayAcquisitionViews/PA_Step2Views",
        "PathwayAcquisitionViews/PA_Step3Views",
        "PathwayAcquisitionViews/PA_Step4Views",
        "DataManagementViews/DM_MyDataView",
        "DataManagementViews/DM_Bed2GenesViews",
        "DataManagementViews/DM_miRNA2GenesViews",
        "UserManagementViews/UserViews"
    ];
    this.controllers = ['JobController', "PathwayController", "UserController", "DataManagementController"];
    this.controllerInstances = [];
    this.mainView = null;
	/* Current timestamp for app */
    this.timestamp = 1524491415;

    this.launch = function () {
        var modelsLocation = "app/model/";
        var viewsLocation = "app/view/";
        var controllersLocation = "app/controller/";
        for (var i = 0; i < this.models.length; i++) {
            this.loadModule(modelsLocation, this.models[i]);
        }
        for (var i = 0; i < this.views.length; i++) {
            this.loadModule(viewsLocation, this.views[i]);
        }
        for (var i = 0; i < this.controllers.length; i++) {
            this.loadModule(controllersLocation, this.controllers[i]);
        }

        this.mainView = new MainView();
        this.mainView.getComponent();

        // Try to load the jobID provided in the URL
        var URLjobID = jobIDInAddress();
        var jobInstanceModel = new JobInstance(null);

        if (window.sessionStorage && sessionStorage.getItem("jobModel") != null) {
          var sessionJobJSON = null;

          /* A cached model we cannot even parse is a cache miss, not a reason to
           * refuse to start: throw it away and boot as a first visit. */
          try {
            sessionJobJSON = JSON.parse(sessionStorage.getItem("jobModel"));
          } catch (error) {
            console.error(Date.logFormat() + "app.js : the cached job model is not valid JSON, discarding it.", error);
            discardCachedJobModel(null);
            this.continueBoot(new JobInstance(null), URLjobID);
            return;
          }

          if ((URLjobID == null || URLjobID == sessionJobJSON.jobID) && sessionJobJSON.timestamp && sessionJobJSON.timestamp >= this.timestamp) {
            if (!this.restoreCachedModel(jobInstanceModel, sessionJobJSON)) {
                /* Half of the fields may already have been copied over before the
                 * failure, so start from a fresh instance rather than reusing it. */
                jobInstanceModel = new JobInstance(null);
            }
            this.continueBoot(jobInstanceModel, URLjobID);
          } else {
            this.loadFromIndexedDB(jobInstanceModel, URLjobID);
          }
        } else {
            this.loadFromIndexedDB(jobInstanceModel, URLjobID);
        }
    };

    /**
     * Restores a cached model in place, or discards the cache and returns false.
     *
     * Restoring runs before anything is rendered, so an exception here used to
     * take the whole site down: launch()'s only caller turns it into the boot
     * error dialog, the poisoned copy stayed in sessionStorage, and every later
     * load - the plain home page included - hit the same exception. Measured
     * with a job that crashed in Step 2 and cached globalExpressionData: null.
     * Whatever the reason a cached model cannot be read, the containment is the
     * same: report the real exception, drop the cache, boot clean.
     */
    this.restoreCachedModel = function (jobInstanceModel, sessionJobJSON) {
        if (!sessionJobJSON || sessionJobJSON.cacheSchema !== PA_JOB_CACHE_SCHEMA) {
            console.info(Date.logFormat() + "app.js : the cached job model (" +
                ((sessionJobJSON && sessionJobJSON.jobID) || "unknown jobID") +
                ") predates cache schema " + PA_JOB_CACHE_SCHEMA + "; discarding it and recovering from the server.");
            if (sessionJobJSON && typeof discardCachedJobModel === "function") {
                discardCachedJobModel(sessionJobJSON.jobID, function () {});
            }
            return false;
        }
        try {
            jobInstanceModel.loadFromJSON(sessionJobJSON);
            return true;
        } catch (error) {
            console.error(Date.logFormat() + "app.js : could not restore the cached job model (" +
                (sessionJobJSON.jobID || "unknown jobID") + "), discarding it and starting clean.", error);
            discardCachedJobModel(sessionJobJSON.jobID);
            return false;
        }
    };

    this.loadFromIndexedDB = function(jobInstanceModel, URLjobID) {
        var me = this;
        var db = openPaintomicsDB();

        /* Same containment as the sessionStorage path: a row that cannot be
         * restored is dropped from BOTH caches, and the app boots without it.
         * This used to be the hole through which the poison survived - the
         * argument-less .catch() below swallowed the exception, kept the row and
         * re-ran the boot with a half-populated model. */
        var bootWith = function(sessionJobJSON) {
            if (sessionJobJSON && sessionJobJSON.timestamp >= me.timestamp) {
                if (!me.restoreCachedModel(jobInstanceModel, sessionJobJSON)) {
                    jobInstanceModel = new JobInstance(null);
                }
            }
            me.continueBoot(jobInstanceModel, URLjobID);
        };

        var onReadFailed = function(error) {
            console.error(Date.logFormat() + "app.js : could not read the cached job from IndexedDB, booting without it.", error);
            me.continueBoot(new JobInstance(null), URLjobID);
        };

        /* Two-argument then(): the rejection handler must see failures of the
         * read only. Chaining .catch() instead also catches whatever bootWith()
         * throws, and booting a second time from inside the failure of the first
         * boot is how a single bad model turned into an unrecoverable page. */
        var jobIdToLoad = URLjobID;
        if (!jobIdToLoad) {
            // If no URL ID, try to find the most recent job in DB
            db.table("jobs").orderBy('timestamp').last().then(bootWith, onReadFailed).catch(showBootFailureMessage);
        } else {
            db.table("jobs").get(jobIdToLoad).then(bootWith, onReadFailed).catch(showBootFailureMessage);
        }
    };

    this.continueBoot = function(jobInstanceModel, URLjobID) {
        /* If the job is not on the last step, avoid loading it from session */
        var loginDialog = Ext.getCmp('userViewsDialog');

        if (loginDialog == undefined) {
            if (URLjobID !== null && (jobInstanceModel.getJobID() == null || jobInstanceModel.getStepNumber() == 2)){
            this.getController("JobController").recoverPAJobHandler(URLjobID);
            } else {
            this.getController("JobController").showJobInstance(jobInstanceModel);
            }
        }

        if (Ext.util.Cookies.get("silence") != null) {
            console.log("Message already shown, ignoring.");
        } else if (URLjobID === null) {
            // Not on a job link: these share the one message dialog with the
            // job's answer, and Safari's would close that answer after 5 s. With
            // no cookie, the next bare visit still shows them and the Welcome.
            if (Ext.isIE) {
                showWarningMessage("Using Internet Explorer?", {message: "Some features of PaintOmics AI do not work in Internet Explorer.<br>Please use Chrome, Firefox or Edge.", closeTimeout: 5, showButton: true});
            } else if (Ext.isSafari) {
                showWarningMessage("Using Safari?", {message: "Some features of PaintOmics AI may not work as expected in Safari.<br>For the best experience, use Chrome or Firefox.", closeTimeout: 5, showButton: true});
            }

            //ADD A COOKIE WITH 2 HOURS EXPIRATION
            Ext.util.Cookies.set("silence", true, new Date(new Date().getTime() + 2 * 60 * 60 * 1000), location.pathname);
        }
    };

    /**
     * Fetches and runs one module, or throws: this used to have no error
     * handling, so a module that never arrived was silent until new MainView()
     * or a model constructor raised a ReferenceError nothing could explain.
     * A fetch that fails is asked again; a script that arrived and threw is not.
     */
    this.loadModule = function (location, name) {
        var file = location + name + ".js";
        var outcome = null;

        for (var attempt = 1; attempt <= MODULE_FETCH_ATTEMPTS; attempt++) {
            if (attempt > 1) {
                console.warn(Date.logFormat() + "app.js : could not fetch " + file + ", trying again (attempt " + attempt + ").");
                pauseBoot(MODULE_FETCH_RETRY_PAUSE * (attempt - 1));
            }
            outcome = fetchModuleOnce(file);
            if (outcome.failedRequest) {
                continue;
            }
            if (outcome.evaluationError) {
                throw moduleEvaluationError(file, outcome.evaluationError);
            }
            console.info(Date.logFormat() + "app.js : Loaded " + name);
            return;
        }
        throw moduleFetchError(file, outcome.failedRequest);
    };

    this.getMainView = function () {
        return this.mainView;
    };

    this.getController = function (controllerName) {
        if ($.inArray(controllerName, this.controllers) !== -1) {
            if (this.controllerInstances[controllerName] === undefined) {
                this.controllerInstances[controllerName] = new window[controllerName]();
            }
            return this.controllerInstances[controllerName];
        }
        return null;
    };
}

$(document).ready(function () {
    application = new Application();
    Ext.application({name: 'Paintomics', launch: function () {
            try {
                application.launch();
            } catch (error) {
                showBootFailureMessage(error);
            }
        }});
    Ext.form.field.File.override({
        extractFileInput: function () {
            var me = this, fileInput = me.fileInputEl.dom, clone = fileInput.cloneNode(true);

            fileInput.parentNode.replaceChild(clone, fileInput);
            me.fileInputEl = Ext.get(clone);
            return fileInput;
        }
    });
});

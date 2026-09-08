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

/**
 * The dialog shown when the application cannot boot.
 *
 * It used to advise clearing the browser cache, which is both wrong - that
 * touches neither sessionStorage nor IndexedDB, the two places the offending
 * state actually lives - and unactionable, so users saw the same blank page on
 * every reload. Offer the action that does work instead.
 */
function showBootFailureMessage(error) {
    console.error(Date.logFormat() + "app.js : the application failed to start.", error);

    showErrorMessage("Oops..Internal error!", {
        message: "</br>PaintOmics AI could not start with the analysis stored in this browser." +
            "</br><a href='#' onclick='resetPaintomicsSession(); return false;'>Discard the stored analysis and reload</a>" +
            " - your jobs are kept on the server, so you can reopen them from [b]My Jobs[/b]." +
            "</br>If the error persists, please contact your web <a href='mailto:paintomicsai@gmail.com' target='_blank'>administrator</a>.",
        showButton: true
    });
}

function Application() {
    //TODO: CARGAR COSAS A PETICION?
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
        var URLparams = Ext.urlDecode(location.search.substring(1));
        var URLjobID = (URLparams["jobID"] !== undefined) ? URLparams.jobID : null;
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
        } else {
            if (Ext.isIE) {
                showWarningMessage("Using Internet Explorer?", {message: "PaintOmics AI was developed to work on Internet Explorer, however some features could not work properly.</br>We recommend to work with Chrome or Firefox.", closeTimeout: 5, showButton: true});
//            } else if (Ext.isGecko) {
//                var version = navigator.userAgent.toLowerCase().split("firefox/");
//                if (version.length > 1) {
//                    try {
//                        var version = parseFloat(version[1]);
//                        if (version >= 22) {
//                            showWarningMessage("Using Firefox?", {message: "From version 22 of Firefox, the Paintomics application looks bigger in the screen.<br>Please, accommodate the application to your browser window using the zoom tool (Ctrl and keys +/-)", closeTimeout: 5, showButton: true});
//                        }
//                    } catch (error) {
//
//                    }
//                }
            } else if (Ext.isSafari) {
                showWarningMessage("Using Safari?", {message: "PaintOmics AI was developed to work in Safari, however some features could not work properly.<br>We recommend to work with Chrome or Firefox.", closeTimeout: 5, showButton: true});
            }

            //ADD A COOKIE WITH 2 HOURS EXPIRATION
            Ext.util.Cookies.set("silence", true, new Date(new Date().getTime() + 2 * 60 * 60 * 1000), location.pathname);
        }
    };

    this.loadModule = function (location, name) {
        $.ajax({
            url: location + name + ".js",
            async: false, dataType: "script",
            success: function () {
                console.info(Date.logFormat() + "app.js : Loaded " + name);
            }
        });
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

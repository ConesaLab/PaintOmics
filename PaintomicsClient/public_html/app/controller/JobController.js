//# sourceURL=JobController.js
/* global Ext, SERVER_URL_PA_STEP1, SERVER_URL_PA_EXAMPLE_STEP1, extJSErrorHandler, SERVER_URL_DM_FROMBED2GENES, ajaxErrorHandler */

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
*  - JobController
*
* EVENT HANDLERS MAPPING
*  - step1OnFormSubmitHandler
*  - step2OnFormSubmitHandler
*  - step3OnFormSubmitHandler
*  - step3GetPathwaysNetworkDataHandler
*  - recoverPAJobHandler
*  - backButtonOnClickHandler
*  - resetButtonClickHandler
*  - showJobInstance
*  - updateStoredVisualOptions
*  - updateStoredApplicationData
*  - cleanStoredApplicationData
*  - getCredentialsParams
*
*/
/**
* Appends the chosen example dataset to an "/example" URL.
*
* The routes are declared with Flask's <path:> converter, so an extra segment
* needs no new endpoint: "pa_step1/example" and "pa_step1/example/region-based"
* both reach the same handler. A null id keeps the bare URL, which the server
* resolves to that pipeline's default -- so the old behaviour is what happens
* when nothing was chosen.
*
* @param {string} exampleURL a URL already ending in "/example"
* @param {PA_Step1JobView} jobView
* @returns {string}
*/
function withExampleScenario(exampleURL, jobView) {
	var scenarioId = (jobView && jobView.getExampleScenarioId)
		? jobView.getExampleScenarioId() : null;
	return scenarioId ? exampleURL + "/" + encodeURIComponent(scenarioId) : exampleURL;
}

/* The reason a pre-processing job failed, as readable HTML, or "".
 *
 * JSON.parse used to be called on response.responseText unguarded, inside the
 * error handler itself. Anything that is not JSON -- a Flask HTML error page, a
 * proxy timeout, a truncated body -- threw from there, so `pendingRequests === 0`
 * was never reached: endStep1Submission() never ran, the submit lock was never
 * released, and NO dialog appeared at all. The one path whose whole job is to
 * report a failure was the one that could fail silently.
 */
function step1FailureReason(response) {
	var body = response && response.responseText;
	if (!body) { return ""; }
	var message;
	try {
		message = JSON.parse(body).message;
	} catch (notJson) {
		/* Not JSON. Show it as text rather than nothing -- a proxy's "504
		   Gateway Timeout" is a better answer than "check the form". */
		message = String(body).replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
		if (message.length > 300) { message = message.slice(0, 300) + "..."; }
	}
	if (!message) { return ""; }
	/* "Exception: AT MiRNA2GenesServlet.py: fromMiRNAtoGenes_STEP2. ERROR
	   MESSAGE: ..." -- everything before the marker is for the log, the same
	   split UserController and convert-drawer already make. The text is the
	   user's own identifiers, so it is escaped before the [b]-style markup is
	   turned into tags: "<NA>" is an id, not an element, and "A&B" stays A&B.
	   The " - " the server puts at the head of a message is a marker, not a
	   separator: it used to become a line break wherever it appeared, which
	   split "hsa-miR-1 - 5p" in two. */
	var split = String(message).split("ERROR MESSAGE:");
	message = (split.length > 1 ? split.slice(1).join("ERROR MESSAGE:") : split[0]).trim();
	message = message.replace(/^-\s*/, "");
	return message
		.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
		.replace(/\[b\]/g, "<b>").replace(/\[\/b\]/g, "</b>")
		.replace(/\[br\]/g, "</br>")
		.replace(/\[ul\]/g, "<ul>").replace(/\[\/ul\]/g, "</ul>")
		.replace(/\[li\]/g, "<li>").replace(/\[\/li\]/g, "</li>")
		.replace(/\n\s*-\s+/g, "<br/>");
}

/* The dialog shown when one or more files could not be prepared.
 *
 * It used to say "Please check the form for more information" and carry none of
 * it. The information was real -- the error handler had already parsed the
 * server's message -- but it went into a box inside the omic's own card, below
 * the fold of a long Step 1, so from where the user stands the run failed for
 * no stated reason. Reported as exactly that, on a MORE run whose actual cause
 * was a duplicate identifier the server named precisely.
 */
function showStep1PreparationFailure(jobView) {
	var reasons = (jobView && jobView.step1FailureReasons) || [];
	var detail = reasons.length
		? reasons.join("</br></br>")
		: "The server did not say why. The omic cards below may carry more detail.";
	/* failedRequests counts every omic that failed, reasons only those whose
	   body could be read; the larger of the two is the honest count. The
	   height follows the text: a 700-character explanation in a 320px box
	   was clipped (see "a panel clips revealed messages"). */
	var failed = Math.max(reasons.length, (jobView && jobView.failedRequests) || 0, 1);
	if (jobView) { jobView.step1FailureReasons = []; }
	showErrorMessage("Ops!... Something went wrong while preparing your files.", {
		message: failed + " file(s) could not be prepared.</br></br>" + detail,
		width: 620, height: Math.min(620, 200 + Math.ceil(detail.replace(/<[^>]*>/g, "").length / 85) * 22)
	});
}

/* What a Step 1 submission is told when the form holds no omic data at all.
   Both submit paths reach that state, so both say it in the same words. */
var STEP1_NO_DATA_MESSAGE = "Invalid form. <br/> Please provide at least: " +
	"<span style='color: auto;text-decoration: underline;'>Gene expression /Metabolomics /Proteomics data.</span>" +
	" Also, please make sure to <span style='color: auto;text-decoration: underline;'>select an organism.</span>";

/**
* The refusal a Step 1 submission gets when checkForm() said no.
*
* Three things can be wrong and they do not read the same:
*
*   - nothing filled in at all: there is no field to point at, so it says so
*     and does not offer Report error -- nothing here is a bug;
*   - a field is wrong: name it, say what it wants, and scroll it into view,
*     and -- like the empty form, and for the same reason -- do not offer
*     Report error. The form is three sections tall, so the field that refused
*     the submission is routinely off screen behind the dialog. The user who
*     reported this on 2026-08-26 had filled in a MORE panel in section 3 and
*     never chosen an organism in section 1 -- "please check the form errors"
*     was true and still told her nothing;
*   - a refusal with nothing marked anywhere: that is the old wording, kept as
*     a fallback, and it is the shape of a bug, so Report error stays.
*
* @param {PA_Step1JobView} jobView
*/
function showInvalidStep1FormMessage(jobView) {
	if (jobView && jobView.formIsEmpty === true) {
		showErrorMessage(STEP1_NO_DATA_MESSAGE, {height: 150, width: 400, showReportButton: false});
		return;
	}

	/* The composition -- scroll the field into view, quote its label and what
	   it wants -- lives in Util.js as showInvalidFieldMessage, shared with the
	   failure handler for the refusals ExtJS itself raises inside submit(). */
	showInvalidFieldMessage((jobView && jobView.firstFormError) ? jobView.firstFormError() : null);
}

/* Tells the server the answer for `jobID` arrived, so it can drop its copy of
   the result. Fire-and-forget: nothing waits on it, and a lost
   acknowledgement only means the server keeps the entry until its ten-minute
   TTL (Queue.DELIVERED_RESULT_TTL). */
function acknowledgeJobResult(jobID) {
	$.ajax({type: "POST", url: SERVER_URL_JOB_RESULT_ACK + "/" + jobID, error: function () {}});
}

/* The response a status poll hands its error handler once it has given up
   retrying: the shape of a server error (a JSON body with a message, an HTTP
   status) so the callers' handlers -- step1FailureReason, ajaxErrorHandler --
   read it as they read any other, and `unanswered` set so ajaxErrorHandler
   can say it was the wire rather than the code. `what` is
   describeUnansweredRequest's wording for the last attempt. The link is only
   given to polls that showed one (showURL), whose handler renders HTML;
   step1FailureReason escapes its message, so a conversion job gets text. */
function unansweredStatusResponse(jqXHR, what, jobID, jobURL) {
	var minutes = Math.round(JOB_STATUS_RETRY_BUDGET / 60000);
	var message = "The server did not answer the status check for job " + jobID +
		" (" + what + ") for " + minutes + " minutes. The job may still be running on the server" +
		(jobURL
			? ": you can reopen it later at <a href=\"" + jobURL + "\" target=\"_blank\">" + jobURL +
			  "</a>, or from My jobs, before resending the data."
			: "; check My jobs in a few minutes before resending the data.");
	var body = String((jqXHR && jqXHR.responseText) || "")
		.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 300);
	return {
		status: Number(jqXHR && jqXHR.status) || 0,
		statusText: String((jqXHR && jqXHR.statusText) || ""),
		unanswered: true,
		responseText: JSON.stringify({
			success: false, status: "unanswered", unanswered: true, message: message,
			detail: "last status answer: " + what + (body ? "; body: " + body : "")
		})
	};
}

function JobController() {
	/**
	*
	* @param {type} jobID
	* @param {type} jobView
	* @param {type} callback
	* @param {type} other
	* @returns {undefined}
	*/
	this.checkJobStatus = function (jobID, jobView, callback, other, showURL = false) {
		other = (other || {});
		var errorHandler = (other.errorHandler || ajaxErrorHandler);
		var me = this;

		// The dialog for a job the user is waiting on: its title, the link
		// back to the job, and the body. Drawn by the still-running answer
		// below, and again by a retry, which adds why the last poll got
		// nothing.
		//
		// Title and body, not one string in the title slot. The whole thing -
		// the job id, the sentence about the URL, and the URL itself - used to
		// be passed as the dialog's title, so all three lines were painted by
		// `#messageDialog.infoDialog h4`: 16px, 600 weight, centred, in the
		// info blue. A dialog whose every word is a heading has no heading,
		// and the one line that matters ("Running job X") was
		// indistinguishable from the two lines of housekeeping under it. The
		// body slot has existed on this dialog all along.
		//
		// A conversion job (regions/miRNA/MORE to genes) is named for what it
		// prepares, so the analysis job that follows -- with its own id --
		// does not read as the same job renumbered.
		var jobURL = 'https://' + window.location.host + window.location.pathname + "?jobID=" + jobID;
		var dialogTitle = other.jobLabel
			? other.jobLabel + " (conversion job " + jobID + ")..."
			: "Running job " + jobID;
		var dialogBody = showURL
			? "You can come back to this job at any time:<br/>" +
			  "<a href=\"" + jobURL + "\" target=\"_blank\">" + jobURL + "</a>"
			: "";

		console.info("Checking status for Job " + jobID);
		$.ajax({
			type: "POST",
			headers: {"Content-Encoding": "gzip"},
			url: SERVER_URL_JOB_STATUS + "/" + jobID,
			// Longer than the proxy's 60 s, so a stalled poll is ended by the
			// proxy's 504 -- which the error branch retries -- and never by the
			// browser abandoning a request the server thread is still working
			// on. See ServerConfiguration.js for the numbers.
			timeout: JOB_STATUS_REQUEST_TIMEOUT,
			success: function (response) {
				other.statusOutage = null;   // the server answered: any outage is over
				if (response.success === false) {
					if (response.status === "JobStatus.STARTED" || response.status === "started") {
						var progress = null;

						if (showURL) {
							// These were two sentences of raw seconds that the reader had to
							// divide to learn how far along the job was. Handed to the dialog
							// as numbers instead, so it can draw a bar and say how long is left.
							// `detailed` is the server's own phase/fraction report when it has
							// one; the two flat numbers remain as the fallback path.
							progress = {
								elapsed: response.timeSpent,
								estimated: response.estimatedFinishTime,
								detailed: response.progress || null
							};
						}

						showInfoMessage(dialogTitle, {message: dialogBody, logMessage: "Job " + jobID + " still running.", showSpin: true, progress: progress, append: other.multipleJobs, itemId: jobID, icon: "play"});
					}
					//Check again in N seconds
					setTimeout(function () {
						me.checkJobStatus(jobID, jobView, callback, other, showURL);
					}, CHECK_STATUS_TIMEOUT);
				} else {
					// The result is in hand: tell the server it may drop its
					// copy. Until this lands (or the server's ten-minute TTL
					// passes) a retried poll could still have collected it --
					// see Queue.deliver_result in PySiQ.py.
					acknowledgeJobResult(jobID);
					callback(response, jobID, jobView, other);
				}
			},
			error: function (response, textStatus) {
				// A READABLE answer is a verdict about the job -- "not on the
				// queue anymore", a failed run's own message, an expired
				// session -- and is final. What is not readable is nginx's
				// 502/504 page, the empty body of a dropped connection, or
				// this request's own timeout: verdicts about the wire, none
				// about the job, and every one worth asking again.
				//
				// 2026-09-11 13:07:47 UTC, paintomics.uv.es: one poll for job
				// ia5b7334Y0 took 61 s (the server was swapping), nginx
				// answered 504 at its default 60 s, this branch handed the HTML
				// page to ajaxErrorHandler -- "Oops..Internal error! Unable to
				// parse the error message" -- and the chain ended there. The
				// job finished and was stored at 13:09:42 with nobody polling.
				// The user came back five hours later and redid every step.
				if (readableAnswer(response)) {
					other.statusOutage = null;
					acknowledgeJobResult(jobID);   // a failed job's message was received too
					errorHandler(response, jobID, jobView, other);
					return;
				}

				var outage = other.statusOutage || (other.statusOutage = {since: 0, attempts: 0});
				var what = describeUnansweredRequest(response, textStatus);
				var delay = statusPollRetryDelay(outage, Date.now());
				if (delay < 0) {
					// Budget spent. Report through the same handler a failed
					// job uses, with a body that says what happened, so the
					// callers' own handlers -- the one that unlocks the Step 1
					// form, the one that counts a failed conversion -- run as
					// they would for any other failure. The custom handler is
					// reached here and above only: never for a poll that is
					// still being retried.
					other.statusOutage = null;
					console.error("Job " + jobID + ": no readable status answer for " +
						Math.round((Date.now() - outage.since) / 1000) + " s (last: " + what + "); giving up.");
					errorHandler(unansweredStatusResponse(response, what, jobID, showURL ? jobURL : null),
					             jobID, jobView, other);
					return;
				}

				console.warn("Job " + jobID + ": status check got no readable answer (" + what +
					"); trying again in " + Math.round(delay / 1000) + " s (attempt " + outage.attempts + ").");
				if (showURL || other.multipleJobs) {
					// The dialog this poll owns keeps its spinner, and says why
					// it is still there rather than freezing on the last
					// progress it saw.
					showInfoMessage(dialogTitle, {
						message: dialogBody + (dialogBody ? "<br/>" : "") +
							"<i>The server did not answer the last status check (" + what +
							"). Trying again in " + Math.round(delay / 1000) + " s\u2026</i>",
						logMessage: "Job " + jobID + " status unanswered (" + what + "), retrying.",
						showSpin: true, append: other.multipleJobs, itemId: jobID, icon: "play"
					});
				}
				setTimeout(function () {
					me.checkJobStatus(jobID, jobView, callback, other, showURL);
				}, delay);
			}
		});
	};

	/**
	* One submission at a time per Step 1 form.
	*
	* On 2026-08-17 the live server received the same STATegra example form
	* twice, three seconds apart, from one browser (jobs Ov57R61V4y and
	* yL0zFsu5xt). Nothing here refused the second POST: both jobs ran through
	* the whole of step 1, the client polled both, and whichever callback landed
	* last owned the view -- so the job id the dialog had been announcing was
	* not the one step 2 continued with. Seen from the user's side, "the job id
	* changed between step 1 and step 2".
	*
	* The lock is a flag on the job view. It is taken when a submission starts
	* (either entry point below), released when that submission's own callback
	* or error path runs, and a submit that arrives while it is held is refused
	* with a note naming the job already under way. `step1ActiveJobID` records
	* which job the view is following, so a callback for any other job -- a
	* stale poll from a submission the lock did not exist for -- cannot take
	* the view over.
	*
	* @param {JobView} jobView
	* @returns {Boolean} true when the caller may proceed
	*/
	this.beginStep1Submission = function (jobView) {
		if (jobView.step1SubmitInFlight === true) {
			var active = jobView.step1ActiveJobID;
			console.warn("Step 1 submit ignored: " + (active ? "job " + active + " is" : "a submission is") + " already in progress for this form.");
			showInfoMessage(active ? "Job " + active + " is already running" : "Your files are already being submitted", {
				message: "This form has already been submitted. Please wait for the current job to finish before submitting again.",
				logMessage: "Duplicate step 1 submission refused.",
				showSpin: true
			});
			return false;
		}
		jobView.step1SubmitInFlight = true;
		jobView.step1ActiveJobID = null;
		return true;
	};

	this.endStep1Submission = function (jobView) {
		jobView.step1SubmitInFlight = false;
	};

	/**
	* Reset abandons whatever the Step 1 form was following: the lock is
	* released and the active id cleared, so a late poll for the abandoned job
	* is dropped rather than resurrecting it, and a fresh submission is allowed.
	*/
	this.abandonStep1Submission = function () {
		var step1View = application.getMainView().getSubView("PA_Step1JobView");
		if (step1View !== undefined) {
			step1View.step1SubmitInFlight = false;
			step1View.step1ActiveJobID = null;
		}
	};

	/**
	* This function gets a list of of RegionBasedOmicViews/miRNABasedOmicView and send each item to
	* server for processing.
	*
	* @param {JobView} jobView
	* @param {Array} specialOmics
	* @returns {String} error message in case of invalid form.
	*/
	this.step1ComplexFormSubmitHandler = function (jobView, specialOmics) {
		//STEP 1. INITIALIZE THE COUNTERS
		jobView.pendingRequests = 0;
		jobView.runningRequests = 0;
		jobView.failedRequests = 0;

		var me = this;

		//CHECK FORM VALIDITY
		if (jobView.checkForm() === true) {
			if (!me.beginStep1Submission(jobView)) {
				return false;
			}

			var regionURL = SERVER_URL_DM_FROMBED2GENES;
			var miRNAURL = SERVER_URL_DM_FROMMIRNA2GENES;
			var moreURL = SERVER_URL_DM_FROMMORE2GENES;

			if (jobView.isExampleMode() === true) {
				regionURL = withExampleScenario(SERVER_URL_DM_EXAMPLE_FROMBED2GENES, jobView);
				miRNAURL = withExampleScenario(SERVER_URL_DM_EXAMPLE_FROMMIRNA2GENES, jobView);
				moreURL = withExampleScenario(SERVER_URL_DM_EXAMPLE_FROMMORE2GENES, jobView);
			}

			/**
			* Given a JobView and a list of RegionBasedOmicViews,
			* we get the first element in the list (if any) and
			* create a temporal form with the corresponding fields.
			* After that, we send the form to the server and get the corresponding
			* Genes for the given regions.
			* When we get a response, we replace the content for the
			* corresponding RegionBasedOmicView by the location of output files or an
			* error message in case that something went wrong.
			*
			* If regions are mapped correctly, then we call this function recursively
			* for the next element in the list, if exists.
			* Otherwise, we check if all regions were mapped correctly and, if so, we
			* call to the normal submission function step1OnFormSubmitHandler.
			*
			* @param jobView
			* @param genericBasedOmic list of RegionBasedOmicViews/miRNABasedOmicView elements.
			*/
			var sendRequest = function (jobView, genericBasedOmic) {
				//STEP1. TAKE THE ITEM THAT WILL BE SENT TO SERVER
				var subview = genericBasedOmic.shift();

				//STEP2. SHOW WAITING MESSAGE INSIDE THE PANEL
				subview.remove(subview.queryById("errorMessage"));

				//STEP3. CREATE A TEMPORAL FORM TO SUBMIT THE DATA
				var itemsContainer = subview.queryById("itemsContainer");
				var extraElements = {};
				var extraElementsContainer = {};

				// Append file input elements from other forms, in case something was selected
				itemsContainer.query("myFilesSelectorButton").forEach(function(formElement) {
					var itemOrigin = formElement.down("[itemId=originField]").getValue();

					if (itemOrigin.includes("_filelocation")) {
						var originalFileInput = Ext.ComponentQuery.query("[name=" + itemOrigin + "]")[0];

						if (originalFileInput != undefined) {
							var selectorButton = originalFileInput.up("myFilesSelectorButton");
							var selectorCls = selectorButton.up("container");

							// Append to elements of new form
							extraElements[itemOrigin] = selectorButton;

							// Keep track of the parent element & position to restore it after form submitting
							// or in case of error.
							extraElementsContainer[itemOrigin] = [selectorCls, selectorCls.items.indexOf(selectorButton)];
						}
					}
				});

				var temporalForm = Ext.widget({xtype: "form", items: [itemsContainer, ...Object.values(extraElements)]});

				// DEFINE THE URL BASED ON THE TYPE OF OMIC
				var formURL;
				if (subview.cls.search("regionBasedOmic") != -1) {
					formURL = regionURL;
				} else if (subview.cls.search("moreBasedOmic") != -1) {
					formURL = moreURL;
				} else {
					formURL = miRNAURL;
				}

				var _restoreElements = function() {
					subview.add(temporalForm.queryById("itemsContainer"));

					Object.keys(extraElementsContainer).forEach(function(extraItem) {
						var itemContainer = extraElementsContainer[extraItem][0];
						var itemPosition = extraElementsContainer[extraItem][1];

						itemContainer.insert(itemPosition, extraElements[extraItem]);
					});

					Ext.destroy(temporalForm);
				};

				jobView.pendingRequests++;

				temporalForm.getForm().submit({
					method: 'POST',
					url: formURL,
					success: function (form, action) {
						var response = JSON.parse(action.response.responseText);

						// These are conversion jobs (regions/miRNA/MORE to genes), each
						// with its own id, and the pathway analysis that follows gets
						// another. Said so in the dialog, because a line reading
						// "Job X is waiting" followed by "Running job Y" reads as the
						// job having changed id.
						var omicLabel = (itemsContainer.queryById("omicNameField") && itemsContainer.queryById("omicNameField").getValue()) || "input";
						showInfoMessage("Preparing " + omicLabel + " (conversion job " + response.jobID + ") is waiting at job queue...", {logMessage: "Now Job is in the queue...", showSpin: true, append: true, itemId: response.jobID, icon: "clock-o"});

						/* Restore elements used to create the temporal form */
						_restoreElements();			

						/**
						* Execute this code after the job finished at the QUEUE
						* @param {type} jobID
						* @param {type} jobView
						* @param {type} response
						* @returns {undefined}
						*/
						var callback = function (response, jobID, jobView, other) {
							showInfoMessage("Preparing " + omicLabel + " (conversion job " + jobID + ") finished successfully.", {logMessage: "Job " + jobID + " finished.", showSpin: true, append: true, itemId: jobID, icon: "check-circle-o"});

							jobView.pendingRequests--;

							other.subview.setContent("itemsContainerAlt", {
								mainFile: response.mainOutputFileName || response.mainOutputFileName_0,
								secondaryFile: response.secondOutputFileName || response.secondOutputFileName_0,
								thirdFile: response.thirdOutputFileName || response.thirdOutputFileName_0 || null,
								fourthFile: response.fourthOutputFileName || response.fourthOutputFileName_0 || null,
								title: itemsContainer.queryById("omicNameField").getValue(),
								configVars: response.description,
								enrichmentType: response.featureEnrichment,
								response: response
							});

							if (jobView.pendingRequests === 0) {
								if (jobView.failedRequests === 0) {
									// The lock taken above carries over into the pathway
									// submission; `chained` tells it not to take a second one.
									me.step1OnFormSubmitHandler(jobView, {chained: true});
								} else {
									me.endStep1Submission(jobView);
									showStep1PreparationFailure(jobView);
								}
							}
						};

						var errorHandler = function (response, jobID, jobView, other) {
							showInfoMessage("Preparing " + omicLabel + " (conversion job " + jobID + ") finished with errors.", {logMessage: "Job " + jobID + " finished.", showSpin: true, append: other.multipleJobs, itemId: jobID, icon: "times-circle-o"});

							//WHAT TO DO IN CASE OF ERROR
							jobView.failedRequests++;
							jobView.pendingRequests--;

							var parsedMessage = step1FailureReason(response);
							if (parsedMessage) {
								// Kept for the dialog, which is the surface the
								// user is on; the box below keeps the detail.
								jobView.step1FailureReasons = jobView.step1FailureReasons || [];
								jobView.step1FailureReasons.push("<b>" + omicLabel + "</b>: " + parsedMessage);
								other.subview.add(Ext.widget({xtype: "box", itemId: "errorMessage", html: '<h3 style="color: #EC696E;  font-size: 20px;"><i class="fa fa-cog fa-spin"></i> Error when processing the request file.<br><span style="font-size:14px;">' + parsedMessage + '</span></h3>'}));
							}

							if (jobView.pendingRequests === 0) {
								me.endStep1Submission(jobView);
								showStep1PreparationFailure(jobView);
							}
						};

						me.checkJobStatus(response.jobID, jobView, callback, {subview: subview, errorHandler: errorHandler, multipleJobs: true, jobLabel: "Preparing " + omicLabel});

						if (genericBasedOmic.length > 0) {
							sendRequest(jobView, genericBasedOmic);
						}
					},
					failure: function(form, responseObj) {
						/* Restore elements used to create the temporal form */
						_restoreElements();

						me.endStep1Submission(jobView);
						/* ExtJS refused inside submit(): the temporary form is gone
						   already, but its fields are back on the main form, where
						   checkForm()'s refusal knows how to name one. */
						if (responseObj && responseObj.failureType === Ext.form.action.Action.CLIENT_INVALID) {
							showInvalidStep1FormMessage(jobView);
							return;
						}
						extJSErrorHandler(form, responseObj);
					}
				});
			};

			showInfoMessage("Uploading BED/miRNA files and sending required jobs...", {logMessage: "New Job created, submitting files...", showSpin: true});

			//SEND ALL FORM TO THE QUEUE
			sendRequest(jobView, specialOmics);
		} else {
			showInvalidStep1FormMessage(jobView);
			return false;
		}
	};

	/************************************************************
	* This function...
	* @param {type} jobView
	* @returns {undefined}
	************************************************************/
	this.step1OnFormSubmitHandler = function (jobView, options) {
		options = (options || {});
		var URL = SERVER_URL_PA_STEP1;
		// Only a pathway-acquisition example submits step 1 as an example. The
		// pre-processing pipelines (regions2genes, mirna2genes, more) have
		// already run by this point and written real files into the job's own
		// directory; posting to the example endpoint here would discard that
		// output and re-read the dataset's raw inputs instead.
		if (jobView.isExampleMode() === true &&
			jobView.getExamplePipeline() === "pathway-acquisition") {
			URL = withExampleScenario(SERVER_URL_PA_EXAMPLE_STEP1, jobView);
		}

		if (jobView.checkForm() === true) {
			var me = this;
			// A chained call arrives from the pre-processing pipeline already
			// holding the lock (see beginStep1Submission); every other call
			// takes it here, and is refused if it is held.
			if (options.chained !== true && !me.beginStep1Submission(jobView)) {
				return false;
			}
			var form = jobView.getComponent().down("form").getForm();

			showInfoMessage("Uploading files...", {logMessage: "New Job created, submitting files...", showSpin: true});
			form.submit({
				method: 'POST', url: URL,
				success: function (form, action) {
					var response = JSON.parse(action.response.responseText);
					console.log("JOB " + response.jobID + " is queued ");
					// The job this view now follows. A poll callback for any
					// other id is stale and is dropped in `callback` below.
					jobView.step1ActiveJobID = response.jobID;

					showInfoMessage("Waiting at job queue...", {logMessage: "Now Job is in the queue...", showSpin: true, icon: "clock-o"});
					/**
					* Execute this code after the job finished at the QUEUE
					* @param {type} jobID
					* @param {type} jobView
					* @param {type} response
					* @returns {undefined}
					*/
					var callback = function (response, jobID, jobView) {
						if (jobView.step1ActiveJobID !== jobID) {
							console.warn("Ignoring step 1 result for job " + jobID + ": this form is following job " + jobView.step1ActiveJobID + ".");
							return;
						}
						me.endStep1Submission(jobView);
						showSuccessMessage("Done", {logMessage: "FILES PROCESSED SUCCESSFULLY"});
						//Wait 0.5 sec and update the page content with the STEP2 content
						showInfoMessage("Generating Metabolites list...", {showTimeout: 0.5, showSpin: true});

						var jobModel = jobView.getModel();
						jobModel.setStepNumber(2);         //UPDATE THE STEP NUMBER
						jobModel.setJobID(response.jobID); //UPDATE THE foundCompounds FIELD WITH RESPONSE DATA
						jobModel.setUserID(response.userID);
						jobModel.setOrganism(response.organism);  //UPDATE ORGANISM
						jobModel.setDatabases(response.databases); //UPDATE DATABASES
						jobModel.setName(response.name);
						jobModel.setClasses(response.classInfo)

						jobModel.setCompoundBasedInputOmics(response.compoundBasedInputOmics);
						jobModel.setGeneBasedInputOmics(response.geneBasedInputOmics);

						//update for PaintOmics 4
						if (response.mappingComp) {
							jobModel.setMappingComp(response.mappingComp)
						}

						if (response.classificationDict) {
							jobModel.setClassificationDict(response.classificationDict)
						}

						if (response.pValueInDict) {
							jobModel.setpValueInDict(response.pValueInDict)
						}

						if (response.exprssionMetabolites) {
							jobModel.setExprssionMetabolites(response.exprssionMetabolites)
						}

						if (response.adjustPvalue) {
							jobModel.setAdjustPvalue(response.adjustPvalue)
						}
						if (response.totalRelevantFeaturesInCategory) {
							jobModel.setTotalRelevantFeaturesInCategory(response.totalRelevantFeaturesInCategory)
						}

						if (response.compoundRegulateFeatures) {
							jobModel.setCompoundRegulateFeatures(response.compoundRegulateFeatures)
						}

						if (response.classificationMeta) {
							jobModel.setClassificationMeta(response.classificationMeta)
						}
						if (response.classActivity !== undefined) {
							jobModel.setClassActivity(response.classActivity)
						}
						if (response.featureSummary) {
							jobModel.setFeatureSummary(response.featureSummary)
						}

						if (response.globalExpressionData) {
							jobModel.setGlobalExpressionData(response.globalExpressionData)
						}

						if (response.hubAnalysisResult) {
							jobModel.setHubAnalysisResult(response.hubAnalysisResult)
						}

						if (response.regulationPerConditionData) {
							jobModel.setRegulationPerConditionData(response.regulationPerConditionData)
						}



						// Carried from step 1 so step 2 knows whether this job may use
						// the AI at all. The other two responses that send it
						// (step 3, pa_recover_job) arrive too late for step 2.
						if (response.aiConsent === true || response.aiConsent === "true") {
							jobModel.aiConsent = true;
						}

						//TODO: IF IS THE SECOND TIME THAT THE PREVIOUS STEP WAS EXECUTED AND NOTHING CHANGES, AVOID RESENDING?
						jobModel.setFoundCompounds([]);
						var matchedMetabolites = response.matchedMetabolites;
						var matchedCompound = null;
						for (var i in matchedMetabolites) {
							matchedCompound = new CompoundSet();
							matchedCompound.loadFromJSON(matchedMetabolites[i]);
							jobModel.addFoundCompound(matchedCompound);
						}

						//UPDATE SELECTED METABOLITES IN ORDER TO AVOID REPEATED SELECTIONS
						// e.g. if the user uploaded "Alanine" and "beta-Alanine" separately,
						// the beta-Alanine proposed by the "Alanine" panel will be unselected
						// by default
						var selectedCompounds = {};
						var auxCompound=null;
						for(var i in jobModel.foundCompounds){
							for(var j in jobModel.foundCompounds[i].mainCompounds){
								matchedCompound = jobModel.foundCompounds[i].mainCompounds[j];
								auxCompound = (selectedCompounds[matchedCompound.getID()] || matchedCompound) ;
								if(matchedCompound.similarity >= auxCompound.similarity){
									auxCompound.selected = false;
									matchedCompound.selected = true;
									selectedCompounds[matchedCompound.getID()] = matchedCompound;
								}else{
									matchedCompound.selected = false;
								}
							}
						}

						me.updateStoredApplicationData("jobModel", jobModel);
						me.showJobInstance(jobModel);
						showSuccessMessage("Done", {logMessage: "Generating Metabolites list...DONE", showTimeout: 1, closeTimeout: 0.5});
					};

					// A job that fails on the queue (a validation error in the
					// files, say) must hand the form back too, or the lock would
					// refuse the corrected resubmission.
					var errorHandler = function (errorResponse, jobID, jobView) {
						if (jobView.step1ActiveJobID === jobID) {
							me.endStep1Submission(jobView);
						}
						ajaxErrorHandler(errorResponse);
					};
					me.checkJobStatus(response.jobID, jobView, callback, {errorHandler: errorHandler}, true);
				},
				failure: function (form, responseObj) {
					me.endStep1Submission(jobView);
					extJSErrorHandler(form, responseObj);
				}
			});
		} else {
			if (options.chained === true) {
				// The pipeline took the lock before its conversion jobs ran;
				// a form that fails validation here must not keep it.
				this.endStep1Submission(jobView);
			}
			/* The same refusal the pre-processing path shows: an all-empty form
			   is told so, and a wrong field is named and scrolled to. This path
			   used to say "provide at least Gene expression..." unconditionally,
			   which was wrong whenever the organism was the fault. */
			showInvalidStep1FormMessage(jobView);
			return false;
		}
	};

	/************************************************************
	* This function...
	* @param {type} jobView
	* @returns {undefined}
	************************************************************/
	/**
	* "Choose for me": ask the server which KEGG compound each ambiguous name
	* meant, then apply the answer to the cards.
	*
	* Enqueue-and-poll, not one request: the server puts the work on its job
	* queue because a route that blocked on the LLM gateway would hold one of
	* the four uWSGI threads that serve the whole site.
	*
	* Nothing is saved by this. The suggestions move checkboxes in the browser's
	* own model; the analysis only changes if the user then presses Next step,
	* and Undo puts every tick back.
	*
	* @param {PA_Step2JobView} jobView
	*/
	this.step2SuggestCompoundsHandler = function (jobView) {
		var me = this;
		var jobID = jobView.getModel().getJobID();
		var attempts = 0;
		var failures = 0;

		var fail = function (message) {
			jobView.setAIButtonState("idle");
			showErrorMessage(message || "The AI could not choose the compounds.", {
				height: 200, width: 420
			});
		};

		var poll = function () {
			attempts++;
			// Ceiling rather than a timeout on the request: the server's own
			// budget stops the gateway call, and this only has to outlast it.
			if (attempts > AI_SUGGEST_MAX_POLLS) {
				fail("The AI is taking longer than expected. Your selection has "
					+ "not been changed \u2014 please choose the compounds yourself, "
					+ "or try again.");
				return;
			}

			$.ajax({
				type: "POST", url: SERVER_URL_PA_SUGGEST_COMPOUNDS_STATUS,
				data: {jobID: jobID}, dataType: "json",
				success: function (response) {
					if (!response || response.success !== true) {
						fail(response && (response.errorMessage || response.message));
						return;
					}
					if (response.status === "running" || response.status === "queued") {
						setTimeout(poll, AI_POLL_INTERVAL);
						return;
					}
					if (response.status !== "finished") {
						fail("The AI compound selection did not finish.");
						return;
					}

					// applyAISuggestions re-renders the panel, and the panel is
					// where the buttons live now -- Undo appears with it.
					var counts = jobView.applyAISuggestions(response);
					jobView.setAIButtonState("done");

					// Same verb as the banner it appears over. "Selected" would
					// also overstate it: these are the cards that CHANGED, not
					// every card the run decided.
					var changed = counts.byRule + counts.byAI;
					showSuccessMessage(
						changed > 0
							? "Changed " + changed + " compound selection" + (changed === 1 ? "" : "s")
							  + (counts.unsure > 0 ? ", " + counts.unsure + " left for you" : "")
							: "Your selection already matched \u2014 nothing changed",
						{showTimeout: 1, closeTimeout: 3});
				},
				error: function (xhr) {
					// One dropped request must not end the poll: the AI status
					// poll shipped with exactly that bug and died on a single
					// blip. Only a run of them gives up.
					//
					// Counted separately from `attempts`. Guarded on that, the
					// branch below was unreachable -- poll() returns early once
					// `attempts` passes the ceiling, so inside a running poll
					// the test was always true and a server that was down for
					// the whole window reported "taking longer than expected"
					// instead of the transport error it actually hit.
					failures++;
					if (failures <= AI_SUGGEST_MAX_TRANSPORT_FAILURES) {
						setTimeout(poll, AI_POLL_INTERVAL);
						return;
					}
					ajaxErrorHandler(xhr);
					jobView.setAIButtonState("idle");
				}
			});
		};

		$.ajax({
			type: "POST", url: SERVER_URL_PA_SUGGEST_COMPOUNDS,
			data: {jobID: jobID}, dataType: "json",
			success: function (response) {
				if (!response || response.success !== true) {
					fail(response && (response.errorMessage || response.message));
					return;
				}
				setTimeout(poll, AI_POLL_INTERVAL);
			},
			error: function (xhr) {
				ajaxErrorHandler(xhr);
				jobView.setAIButtonState("idle");
			}
		});
	};

	this.step2OnFormSubmitHandler = function (jobView) {
		if (jobView.checkForm() === true) {
			var me = this;
			showInfoMessage("Obtaining Pathways list...", {logMessage: "Sending new request (get pathway list).", showSpin: true});

			// TODO: disabled code, allow setting customValues from step2
			// Get omicNames and customValues from view
			// jobView.getModel().getCompoundBasedInputOmics().concat(jobView.getModel().getGeneBasedInputOmics());
			// var omicNames = ...
			// var omicValues = {};
			//
			// $(omicNames).each(function(omic) {
			// 		omicValues[omic] = Ext.ComponentQuery.query('[name="customslider_' + omic + '"]')[0].getValues();
			// });
			/* EVERY form on the step, not the first one. Step 2 carries two:
			   the cluster-number form and the metabolite class activity form.
			   `down("form")` posted only whichever came first, so on any job
			   with a gene omic the class activity threshold (and now the
			   factor to test) never left the browser -- the server saw no
			   value and silently ran the automatic, competitive null. */
			var formData = {};
			Ext.Array.forEach(jobView.getComponent().query("form") || [], function (form) {
				$.extend(formData, form.getForm().getValues());
			});
			$.extend(formData, {
				jobID: jobView.getModel().getJobID(),
				selectedCompounds: jobView.getSelectedCompounds()
			});

			$.ajax({
				type: "POST",
				headers: {"Content-Encoding": "gzip"},
				url: SERVER_URL_PA_STEP2,
				data: formData,
				success: function (response) {
					console.log("JOB " + response.jobID + " is queued ");

					showInfoMessage("Waiting at job queue...", {logMessage: "Now Job is in the queue...", showSpin: true});
					/**
					* Execute this code after the job finished at the QUEUE
					* @param {type} jobID
					* @param {type} jobView
					* @param {type} response
					* @returns {undefined}
					*/
					var callback = function (response, jobID, jobView) {
						if (response.success === false) {
							var errorMessage = "An error occurred getting the pathway list.</br>Please try again later.</br>If the error is repeated, please contact your web administrator.";
							if (response.errorMessage !== "") {
								errorMessage = response.errorMessage;
							}
							showErrorMessage(errorMessage);
							return;
						}

						//Wait 0.5 sec and update the page content with the STEP2 content
						showInfoMessage("Updating Pathways list...", {logMessage: "Obtaining Pathways list...DONE", showSpin: true});

						var jobModel = jobView.getModel();
						jobModel.setStepNumber(3);   //UPDATE THE STEP NUMBER
						
						if (response.compoundBasedInputOmics) {
							jobModel.setCompoundBasedInputOmics(response.compoundBasedInputOmics);
						}
						if (response.geneBasedInputOmics) {
							jobModel.setGeneBasedInputOmics(response.geneBasedInputOmics);
						}
						
						jobModel.setSummary(response.summary);
						jobModel.setOrganism(response.organism);  //UPDATE ORGANISM
						jobModel.setDatabases(response.databases);
						jobModel.setTimestamp(response.timestamp);

						/* pathwayAcquisitionStep2_PART2 sends conditionNames, and this
						   handler -- like the recover handler below -- copies the
						   response field by field, so anything not named here is
						   dropped. Only the recover path claimed it, which meant a job
						   showed "Cond 1".."Cond N" on the run that produced it and the
						   real names only after being reopened from its URL. Measured on
						   a fresh run of the per-condition-relevance example: Mongo held
						   ["T00h".."T24h"], the model held undefined, and the table drew
						   six columns called "Cond 1".."Cond 6". */
						jobModel.conditionNames = response.conditionNames || [];

						if (response.classInfo) {
							jobModel.setClasses(response.classInfo);
						}

						var pathways = response.pathwaysInfo;
						var pathway = null;

						jobModel.setPathways([]);
						for (var i in pathways) {
							pathway = new Pathway(i);
							pathway.loadFromJSON(pathways[i]);
							jobModel.addPathway(pathway);
						}
						
						if (response.omicsValuesID) {
							jobModel.setOmicsValuesID(response.omicsValuesID);
						}
						// Add metabolism classification

						if (response.mappingComp) {
							jobModel.setMappingComp(response.mappingComp)
						}

						if (response.classificationDict) {
							jobModel.setClassificationDict(response.classificationDict)
						}

						if (response.pValueInDict) {
							jobModel.setpValueInDict(response.pValueInDict)
						}

						if (response.exprssionMetabolites) {
							jobModel.setExprssionMetabolites(response.exprssionMetabolites)
						}

						if (response.adjustPvalue) {
							jobModel.setAdjustPvalue(response.adjustPvalue)
						}
						if (response.totalRelevantFeaturesInCategory) {
							jobModel.setTotalRelevantFeaturesInCategory(response.totalRelevantFeaturesInCategory)
						}

						if (response.compoundRegulateFeatures) {
							jobModel.setCompoundRegulateFeatures(response.compoundRegulateFeatures)
						}

						if (response.classificationMeta) {
							jobModel.setClassificationMeta(response.classificationMeta)
						}
						if (response.classActivity !== undefined) {
							jobModel.setClassActivity(response.classActivity)
						}
						if (response.featureSummary) {
							jobModel.setFeatureSummary(response.featureSummary)
						}

						if (response.globalExpressionData) {
							jobModel.setGlobalExpressionData(response.globalExpressionData)
						}

						if (response.hubAnalysisResult) {
							jobModel.setHubAnalysisResult(response.hubAnalysisResult)
						}

						if (response.regulationPerConditionData) {
							jobModel.setRegulationPerConditionData(response.regulationPerConditionData)
						}

						// AI Interpretation — set on model BEFORE showJobInstance so Step3 view can see it
						if (response.aiConsent === true || response.aiConsent === "true") {
							jobModel.aiConsent = true;
							jobModel.experimentDesign = response.experimentDesign || "";
							$.ajax({
								type: "POST",
								url: SERVER_URL_AI_INTERPRET_INITIATE,
								data: {
									jobID: response.jobID,
									experimentDesign: response.experimentDesign || ""
								},
								success: function(aiResponse) {
									if (aiResponse.success) {
										jobModel.aiJobID = aiResponse.aiJobID;
									}
								},
								error: function() {
									console.warn("AI initiation failed — pathway analysis unaffected");
								}
							});
						}

						me.updateStoredApplicationData("jobModel", jobModel);
						
						me.showJobInstance(jobModel);
						showSuccessMessage("Done", {logMessage: "Updating Pathways list...DONE", showTimeout: 1, closeTimeout: 0.5});
					};

					// `true` used to land in the `other` slot, leaving showURL false, so
					// step 2 drew no progress bar at all — for a phase that is ~50% of
					// the wait. The options object belongs in `other`, the flag last.
					me.checkJobStatus(response.jobID, jobView, callback, {}, true);
				},
				error: ajaxErrorHandler
			});
		} else {
			showErrorMessage("At least one compound must be selected. Please check the form.", {height: 200, width: 400});
			return false;
		}
	};
	/************************************************************
	* This function...
	* @param {type} jobView
	*
	************************************************************/
	this.step3OnFormSubmitHandler = function (jobView, pathwayID) {
		var me = this;
		var jobModel = jobView.getModel();
		var pathwayModel = jobModel.getPathway(pathwayID);

		if (pathwayModel.getGraphicalOptions() === null) {
			showInfoMessage("Fetching Pathway information...", {logMessage: "Sending new request (get pathway information).", showSpin: true});
			$.ajax({
				data: {selectedPathways: pathwayID, jobID: jobModel.getJobID()},
				method: 'POST', url: SERVER_URL_PA_STEP3,
				success: function (response) {
					var graphicalOptionsInstances = response.graphicalOptionsInstances;
					var graphicalOptionsInstance = null;
					for (var i in graphicalOptionsInstances) {
						graphicalOptionsInstance = new PathwayGraphicalData();
						graphicalOptionsInstance.loadFromJSON(graphicalOptionsInstances[i]);
						jobModel.getPathway(pathwayID).setGraphicalOptions(graphicalOptionsInstance);
					}
					var omicsValues = response.omicsValues;
					var feature = null;
					for (var i in omicsValues) {
						feature = new Feature(i);
						feature.loadFromJSON(omicsValues[i]);
						jobModel.addOmicValue(feature);
					}
					me.updateStoredApplicationData("jobModel", jobModel);
					showSuccessMessage("Done", {logMessage: "Pathway information retrieved successfully", closeTimeout: 0.4});
					me.showJobInstance(jobModel, {stepNumber: 4}).showPathwayView(pathwayID);
				},
				error: ajaxErrorHandler
			});
		} else {
			me.showJobInstance(jobModel, {stepNumber: 4}).showPathwayView(pathwayID);
		}
	};
	/************************************************************
	* This function...
	* @param {type} jobView
	*
	************************************************************/
	this.step3GetPathwaysNetworkDataHandler = function (jobView) {
		var me = this;
		// IndexDB has an async nature so we need to provide a callback
		var callback = function(networkData) {
			// Make sure that the time
			if (jobView.database == "Reactome") {
				$.getJSON(SERVER_URL_GET_PATHWAY_NETWORK_REACTOME + "/" + jobView.getModel().getOrganism(), function (pathwaysNetworkData) {
					// Set the id key as organism
					pathwaysNetworkData.id = jobView.getModel().getOrganism();
					pathwaysNetworkData.timestamp = Math.floor( Date.now() / 1000 );

					me.updateStoredApplicationDataIndexDB("networks", pathwaysNetworkData);
					jobView.generateNetwork(pathwaysNetworkData);
				});
			} else if (jobView.database == "KEGG") {
				//TODO: CHANGE URL
				$.getJSON(SERVER_URL_GET_PATHWAY_NETWORK + "/" + jobView.getModel().getOrganism(), function (pathwaysNetworkData) {
					// Set the id key as organism
					pathwaysNetworkData.id = jobView.getModel().getOrganism();
					pathwaysNetworkData.timestamp = Math.floor( Date.now() / 1000 );

					me.updateStoredApplicationDataIndexDB("networks", pathwaysNetworkData);
					jobView.generateNetwork(pathwaysNetworkData);
				});
			} else if (jobView.database == "MapMan") {
			    $.getJSON(SERVER_URL_GET_PATHWAY_NETWORK_MAPMAN + "/" + jobView.getModel().getOrganism(), function (pathwaysNetworkData) {
					// Set the id key as organism
					pathwaysNetworkData.id = jobView.getModel().getOrganism();
					pathwaysNetworkData.timestamp = Math.floor( Date.now() / 1000 );

					me.updateStoredApplicationDataIndexDB("networks", pathwaysNetworkData);
					jobView.generateNetwork(pathwaysNetworkData);
				});

			} else if (jobView.database == "OmniPath") {
			    $.getJSON(SERVER_URL_GET_PATHWAY_NETWORK_OMNIPATH + "/" + jobView.getModel().getOrganism(), function (pathwaysNetworkData) {
					pathwaysNetworkData.id = jobView.getModel().getOrganism();
					pathwaysNetworkData.timestamp = Math.floor( Date.now() / 1000 );

					me.updateStoredApplicationDataIndexDB("networks", pathwaysNetworkData);
					jobView.generateNetwork(pathwaysNetworkData);
				});

			} else {
				/* A source with no network file reached this branch silently and left
				   the tab spinning forever, which reads as a broken view rather than
				   an absent one. */
				console.warn("No pathway network available for database: " + jobView.database);
				jobView.generateNetwork({nodes: [], edges: []});
			}
		};
		
		me.getStoredApplicationDataIndexDB("networks", jobView.getModel().getOrganism(), callback);
	};
    
 	/************************************************************
	* This function...
	* @param {type} jobView
	*
	************************************************************/   
    this.step3GetUpdatedPvalues = function (pathwayTableView, pathwayPvalues, stouferrWeights = null, visiblePathways = null) {
        var me = this;
        var formData = {pValues: pathwayPvalues};
        
        if (stouferrWeights !== null) {
            formData['stoufferWeights'] = stouferrWeights;
			formData['visiblePathways'] = visiblePathways;
        }
        
        showInfoMessage("Fetching new adjusted p-values...", {logMessage: "Sending new request (get new adjusted p-values after filtering).", showSpin: true});
        
        $.ajax({
            data: JSON.stringify(formData),
            method: 'POST', 
			url: SERVER_URL_ADJUST_PVALUES,
			dataType: "json",
  			contentType : "application/json",
            success: function (response) {
				
				if (response.success) {
					/*
						For new Stouffer values, update the visualOptions of each database
						in both Stouffer and adjusted, otherwise only the adjusted p-values.
						
						The visual options databases objects should be initialized at this point.
					*/	
					var currentVisualOptions = pathwayTableView.getParent().getVisualOptions();
					
					if (response.stoufferPvalues) {
						Object.keys(response.stoufferPvalues).forEach(function(db) {
							currentVisualOptions[db]['Stouffer'] = response.stoufferPvalues[db];
							
							$.extend(true, currentVisualOptions[db], {
								'adjustedPvalues': {
									'Stouffer': response.adjustedStoufferPvalues[db]
								}
							});
						});
					} else {
						Object.keys(response.adjustedPvalues).forEach(function(db) {
							currentVisualOptions[db]['adjustedPvalues'] = response.adjustedPvalues[db];
						});
					}
					
					me.updateStoredVisualOptions(pathwayTableView.getParent().getModel().jobID, currentVisualOptions);
                	showSuccessMessage("Done", {logMessage: "Adjusted p-values retrieved successfully", closeTimeout: 0.4});
                	pathwayTableView.updatePvaluesFromStore();
				}                
            },
            error: function(response) {
				if (response.success) {
					response.responseText = "Error parsing the JSON output";
				}

				ajaxErrorHandler(response);
			}
        });
        
        
    };

	/************************************************************
	* This function recovers an instance of JOB from database by a given JobID.
	*
	* @param {String} jobID [optional], the ID for the job, if not defined, the
	* user will be prompt.
	*
	************************************************************/
	this.recoverPAJobHandler = function (jobID) {
		var me = this;

		var _recover = function (btn, jobID) {
			if (btn === "ok" && jobID !== "") {
				showInfoMessage("Loading job information...", {logMessage: "Sending new request (recover job).", showSpin: true});
				$.ajax({
					type: "POST", headers: {"Content-Encoding": "gzip"},
					url: SERVER_URL_PA_RECOVER_JOB,
					data: {jobID: jobID},
					success: function (response) {
						if (response.success === false) {
							if (response.message) {
								showInfoMessage(response.message);
							} else {
								showErrorMessage(response.errorMessage);
							}
							return;
						}
						me.cleanStoredApplicationData();

						var jobModel = new JobInstance(jobID);
						//UPDATE THE STEP NUMBER
						jobModel.setStepNumber(response.stepNumber);
						//TODO: NO ES NECESARIO DEVOLVER ESTO!!! MUY GRANDE! MEJOR CALCULARLO EN EL SERVER
						jobModel.setUserID(response.userID);
						jobModel.setCompoundBasedInputOmics(response.compoundBasedInputOmics);
						jobModel.setGeneBasedInputOmics(response.geneBasedInputOmics);
						jobModel.setSummary(response.summary);
						jobModel.setOrganism(response.organism);  //UPDATE ORGANISM
						jobModel.setDatabases(response.databases); //UPDATE DATABASES
						jobModel.setName(response.name);
						jobModel.setTimestamp(response.timestamp);
						jobModel.setAllowSharing(response.allowSharing);
						jobModel.setReadOnly(response.readOnly);
						jobModel.setClasses(response.classInfo);

						jobModel.setFoundCompounds([]);
						var matchedMetabolites = response.matchedMetabolites;
						var matchedCompound = null;
						for (var i in matchedMetabolites) {
							matchedCompound = new CompoundSet();
							matchedCompound.loadFromJSON(matchedMetabolites[i]);
							jobModel.addFoundCompound(matchedCompound);
						}

						//UPDATE SELECTED METABOLITES IN ORDER TO AVOID REPEATED SELECTIONS
						// e.g. if the user uploaded "Alanine" and "beta-Alanine" separately,
						// the beta-Alanine proposed by the "Alanine" panel will be unselected
						// by default
						var selectedCompounds = {};
						var auxCompound=null;
						for(var i in jobModel.foundCompounds){
							for(var j in jobModel.foundCompounds[i].mainCompounds){
								matchedCompound = jobModel.foundCompounds[i].mainCompounds[j];
								auxCompound = (selectedCompounds[matchedCompound.getID()] || matchedCompound) ;
								if(matchedCompound.similarity >= auxCompound.similarity){
									auxCompound.selected = false;
									matchedCompound.selected = true;
									selectedCompounds[matchedCompound.getID()] = matchedCompound;
								}else{
									matchedCompound.selected = false;
								}
							}
						}

						var pathways = response.pathwaysInfo;

						var pathway = null;
						jobModel.setPathways([]);
						for (var i in pathways) {
							pathway = new Pathway(i);
							pathway.loadFromJSON(pathways[i]);
							jobModel.addPathway(pathway);
						}
						jobModel.isRecoveredJob = true;
						
						if (response.omicsValuesID) {
							jobModel.setOmicsValuesID(response.omicsValuesID);
						}

						//update for PaintOmics 4
						if (response.mappingComp) {
							jobModel.setMappingComp(response.mappingComp)
						}

						if (response.classificationDict) {
							jobModel.setClassificationDict(response.classificationDict)
						}

						if (response.pValueInDict) {
							jobModel.setpValueInDict(response.pValueInDict)
						}

						if (response.exprssionMetabolites) {
							jobModel.setExprssionMetabolites(response.exprssionMetabolites)
						}

						if (response.adjustPvalue) {
							jobModel.setAdjustPvalue(response.adjustPvalue)
						}
						if (response.totalRelevantFeaturesInCategory) {
							jobModel.setTotalRelevantFeaturesInCategory(response.totalRelevantFeaturesInCategory)
						}

						if (response.compoundRegulateFeatures) {
							jobModel.setCompoundRegulateFeatures(response.compoundRegulateFeatures)
						}

						if (response.classificationMeta) {
							jobModel.setClassificationMeta(response.classificationMeta)
						}
						if (response.classActivity !== undefined) {
							jobModel.setClassActivity(response.classActivity)
						}
						if (response.featureSummary) {
							jobModel.setFeatureSummary(response.featureSummary)
						}

						if (response.globalExpressionData) {
							jobModel.setGlobalExpressionData(response.globalExpressionData)
						}

						if (response.hubAnalysisResult) {
							jobModel.setHubAnalysisResult(response.hubAnalysisResult)
						}

						if (response.regulationPerConditionData) {
							jobModel.setRegulationPerConditionData(response.regulationPerConditionData)
						}

						// AI Interpretation — check for recovered jobs
						if (response.aiConsent === true || response.aiConsent === "true") {
							jobModel.aiConsent = true;
						}

						// This handler copies the response field by field rather than going
						// through loadFromJSON, so anything not named here is dropped. The
						// per-condition columns fall back to "Cond 1..N" without these, which
						// is what a multi-condition job showed after being reopened by its
						// URL — the way the results page tells users to come back to a job.
						jobModel.conditionNames = response.conditionNames || [];
						jobModel.experimentDesign = response.experimentDesign || "";

						me.cleanStoredApplicationData();
						me.updateStoredApplicationData("jobModel", jobModel);

						var visualOptions = response.visualOptions;
						if(visualOptions){
							visualOptions.timestamp = response.timestamp;
							me.updateStoredApplicationData("visualOptions", visualOptions);
						}

						me.showJobInstance(jobModel, {force:true});

						showSuccessMessage("Done", {logMessage: "Getting Job information...DONE", closeTimeout: 1});
					},
					error: ajaxErrorHandler
				});
			}
		};

		if (jobID === undefined) {
			Ext.MessageBox.prompt('Job ID', 'Please enter the Job ID:', _recover);
		} else {
			_recover("ok", jobID);
		}
	};

	/************************************************************
	* This function...
	* @param {type} jobView
	* @returns {undefined}
	************************************************************/
	this.fromBed2GenesOnFormSubmitHandler = function (jobView) {
		var URL = jobView.isExampleMode() === true
			? withExampleScenario(SERVER_URL_DM_EXAMPLE_FROMBED2GENES, jobView)
			: SERVER_URL_DM_FROMBED2GENES;

		if (jobView.checkForm() === true) {
			var me = this;
			var form = jobView.getComponent().down("form").getForm();

			showInfoMessage("Uploading and processing files...", {logMessage: "New Job created, submitting files...", showSpin: true});
			form.submit({
				method: 'POST', url: URL,
				success: function (form, action) {
					var response = JSON.parse(action.response.responseText);
					console.log("JOB " + response.jobID + " is queued ");

					showInfoMessage("Waiting at job queue...", {logMessage: "Now Job is in the queue...", showSpin: true});

					/**
					* Execute this code after the job finished at the QUEUE
					* @param {type} jobID
					* @param {type} jobView
					* @param {type} response
					* @returns {undefined}
					*/
					var callback = function (response, jobID, jobView) {
						var jobId = response.jobID;
						showSuccessMessage("Bed2Genes finished successfully", {
							message: "Click on the link below to download your files.</br>" +
							"<b>Note</b> that the main output (quantification at gene level) is now available at your data section.</br>" +
							"<a href='" + window.location.pathname + SERVER_URL_DM_DOWNLOAD_FILE + "?jobID=" + jobId + "&fileName=" + response.compressedFileName + "&fileType=job_result'>Download files.</a>",
							showButton: true
						});
					};

					me.checkJobStatus(response.jobID, jobView, callback);
				},
				failure: extJSErrorHandler
			});
		} else {
			showErrorMessage("Invalid form. Please check form errors.", {height: 150, width: 400, showReportButton:false});
			return false;
		}
	};

	/************************************************************
	* This function...
	* @param {type} jobView
	* @returns {undefined}
	************************************************************/
	this.fromMiRNA2GenesOnFormSubmitHandler = function (jobView) {
		var URL = jobView.isExampleMode() === true
			? withExampleScenario(SERVER_URL_DM_EXAMPLE_FROMMIRNA2GENES, jobView)
			: SERVER_URL_DM_FROMMIRNA2GENES;

		if (jobView.checkForm() === true) {
			var me = this;
			var form = jobView.getComponent().down("form").getForm();

			showInfoMessage("Uploading and processing files...", {logMessage: "New Job created, submitting files...", showSpin: true});
			form.submit({
				method: 'POST', url: URL,
				success: function (form, action) {
					var response = JSON.parse(action.response.responseText);
					console.log("JOB " + response.jobID + " is queued ");

					showInfoMessage("Waiting at job queue...", {logMessage: "Now Job is in the queue...", showSpin: true});

					/**
					* Execute this code after the job finished at the QUEUE
					* @param {type} jobID
					* @param {type} jobView
					* @param {type} response
					* @returns {undefined}
					*/
					var callback = function (response, jobID, jobView) {
						var jobId = response.jobID;
						showSuccessMessage("miRNA2Genes finished successfully", {
							message: "Click on the link below to download your files.</br>" +
							"<b>Note</b> that the main output (quantification at gene level) is now available at your data section.</br>" +
							"<a href='" + window.location.pathname + SERVER_URL_DM_DOWNLOAD_FILE + "?jobID=" + jobId + "&fileName=" + response.compressedFileName + "&fileType=job_result'>Download files.</a>",
							showButton: true
						});
					};

					me.checkJobStatus(response.jobID, jobView, callback);
				},
				failure: extJSErrorHandler
			});
		} else {
			showErrorMessage("Invalid form. Please check form errors.", {height: 150, width: 400, showReportButton:false});
			return false;
		}
	};
	
	/************************************************************
	* This function...
	* @param {type} jobView
	* @returns {undefined}
	************************************************************/
	this.updateMetagenesSubmitHandler = function (jobView, numberClusters, omicName, databaseName) {
		if (parseInt(numberClusters) > 0) {
			var me = this;
			var jobModel = jobView.getModel();

			showInfoMessage("Sending information to generate new clusters...", {logMessage: "New Metagenes Job created...", showSpin: true});
			
			$.ajax({
				type: "POST",
				url: SERVER_URL_UPDATE_METAGENES,
				data: {
					jobID: jobModel.getJobID(),
					number: numberClusters,
					omic: omicName,
					database: databaseName
				},
				success: function (response) {
					if (response.success) {
						/**
						* Execute this code after the job finished at the QUEUE
						* @param {type} jobID
						* @param {type} jobView
						* @param {type} response
						* @returns {undefined}
						*/
						var callback = function (response, jobID, jobView) {
							if (response.success) {
								console.log("MetaGenes JOB " + response.jobID + " is queued ");

								showInfoMessage("Waiting at job queue...", {logMessage: "Now Job is in the queue...", showSpin: true});

								var jobId = response.jobID;
								
								// Override the pathway info and update stored session data
								var pathways = response.pathwaysInfo;

								var pathway = null
								jobModel.setClusterNumber(null);
								jobModel.setPathways([]);
								
								for (var i in pathways) {
									pathway = new Pathway(i);
									pathway.loadFromJSON(pathways[i]);
									jobModel.addPathway(pathway);
								}

								me.updateStoredApplicationData("jobModel", jobModel);

								// Notify to other components
								jobView.getParent().indexPathways(jobModel.getPathways());
								jobModel.setChanged();
								jobModel.notifyObservers();

								showSuccessMessage("New clusters retrieved successfully", {
									message: "The new clusters were generated for the selected omic and information was updated in the pathways",
									closeTimeout: 0.7
								});
							} else {
								showErrorMessage(response.errorMessage || response.message);
							}
						};

						me.checkJobStatus(response.jobID, jobView, callback);	
					} else {
						showErrorMessage(response.errorMessage || response.message);
					}
				},
				error: ajaxErrorHandler
			});
		} else {
			showErrorMessage("Invalid number of clusters.", {height: 150, width: 400, showReportButton:false});
			return false;
		}
	};

	/**
	*
	* @param {type} button
	* @param {type} jobView
	* @returns {undefined}
	*/
	this.backButtonClickHandler = function (jobView, update=false) {
		var jobModel = jobView.getModel();
		var me = this;
		if (jobModel.getStepNumber() > 1) {
			showInfoMessage("Loading job information...", {
				callback: function () {
					jobModel.setStepNumber(jobModel.getStepNumber() - 1);
					me.updateStoredApplicationData("jobModel", jobModel);
					me.showJobInstance(jobModel, {doUpdate: update});
					//                    showSuccessMessage("Done", {logMessage: "Getting Job information..."});
				}, closeTimeout: 1, showSpin: true
			});
		}
	};
	/**
	*
	* @param {type} button
	* @param {type} jobView
	* @returns {undefined}
	*/
	this.resetButtonClickHandler = function (jobView, force, callback) {
		var me = this;
		if (force === true) {
			me.cleanStoredApplicationData();
			me.abandonStep1Submission();
			me.showJobInstance(new JobInstance(null));
			if (callback !== undefined) {
				callback();
			}
			//location.reload();
			return;
		}
		Ext.MessageBox.confirm('Confirm', 'Are you sure you want to exit the current job?', function (opcion) {
			if (opcion === "yes") {
				me.cleanStoredApplicationData();
				me.abandonStep1Submission();
				me.showJobInstance(new JobInstance(null));
				if (callback !== undefined) {
					callback();
				}
				// location.reload();
				window.history.replaceState(null, null, window.location.pathname);
			}
		});
	};

	/**
	*
	* @param {type} jobModel
	* @param {type} callback
	* @returns {PA_Step1JobView|PA_Step3JobView|Step5View|PA_Step4JobView|PA_Step2JobView}
	*/
	this.showJobInstance = function (jobModel, options) {
		var me = this;
		options = (options || {});
		var stepNumber = (options.stepNumber || jobModel.getStepNumber());
		var doUpdate = (options.doUpdate !== false);
		var callback = options.callback;
		var force = (options.force || false);

		var jobView = application.getMainView().getSubView("PA_Step" + stepNumber + "JobView");

		if(jobView !== undefined && force){
			jobView.getModel().deleteObserver(jobView);
			jobView.loadModel(jobModel);
			jobModel.addObserver(jobView);
			doUpdate = true;
		}

		if (jobView === undefined) {
			if (stepNumber === 4) {
				jobView = new PA_Step4JobView();
			} else if (stepNumber === 3) {
				jobView = new PA_Step3JobView();
			} else if (stepNumber === 2) {
				jobView = new PA_Step2JobView();
			} else if (stepNumber === 1) {
				jobView = new PA_Step1JobView();
			}

			jobView.setController(me);
			jobView.loadModel(jobModel);
			jobModel.addObserver(jobView);

			application.getMainView().addMainView(jobView);
			doUpdate = true;
		}

		application.getMainView().changeMainView(jobView.getName());

		if (doUpdate && jobView.updateObserver !== undefined) {
			console.info(Date.logFormat() + "JobController.js : Updating jobview...");
			jobView.updateObserver();
		}

		// Update the URL adding the parameter with the jobID
		if (jobModel.getJobID() !== null && doUpdate) {
			window.history.replaceState(null, null, window.location.pathname + "?jobID=" + jobModel.getJobID());

			// Call the server to update the job's access date even when it was
			// loaded from session data.
			$.ajax({
				type: "POST",
				url: SERVER_URL_PA_TOUCH_JOB,
				data: {jobID: jobModel.getJobID()},
				success: function (response) {
					console.info(Date.logFormat() + " job's access date succesfully updated.");
				},
				error: function (response) {
					console.error(Date.logFormat() + " failed when updating job's access date.");
				},
			});
		}
		
		if (options.callback) {
			options.callback();
		}

		return jobView;
	};
	
	/**
	*
	* @param {type} jobModel
	* @returns {undefined}
	*/
	this.updateSharingOptions = function (jobModel, allowSharing, readOnly) {
		var me = this;

		$.ajax({
			method: "POST",
			url: SERVER_URL_PA_SAVE_SHARING_OPTIONS,
			data: {
				"jobID": jobModel.getJobID(),
				"allowSharing": allowSharing,
				"readOnly": readOnly
			},
			success: function (response) {
				// Update only the timestamp value when the server has properly saved the options.
				if (response.success) {
					jobModel.setAllowSharing(allowSharing);
					jobModel.setReadOnly(readOnly);
					
					me.updateStoredApplicationData("jobModel", jobModel);
				
					console.info(Date.logFormat() + "Sharing options saved succesfully.");
				}
			},
			error: function (response) {
				console.error(Date.logFormat() + "failed when saving sharing options.");
			},
		});
	};

	/**
	*
	* @param {type} jobModel
	* @returns {undefined}
	*/
	this.updateStoredVisualOptions = function (jobID, visualOptions) {
		/********************************************************/
		/* STEP 1. SAVE TO CACHE                                */
		/********************************************************/
		var me = this;
		
		this.updateStoredApplicationData("visualOptions", visualOptions);

		/********************************************************/
		/* STEP 2. SEND TO SERVER                               */
		/********************************************************/
		visualOptions.jobID = jobID;

		$.ajax({
			method: "POST",
			url: SERVER_URL_PA_SAVE_VISUAL_OPTIONS,
			data: JSON.stringify(visualOptions),
			dataType: "json",
			contentType: "application/json",
			success: function (response) {
				// Update only the timestamp value when the server has properly saved the options.
				visualOptions.timestamp = response.timestamp;
				me.updateStoredApplicationData("visualOptions", visualOptions);
				
				console.info(Date.logFormat() + "Visual options saved succesfully.");
			},
			error: function (response) {
				console.error(Date.logFormat() + "failed when saving Visual options.");
			},
		});
	};
	/**
	*
	* @param {type} jobModel
	* @returns {undefined}
	*/
	this.updateStoredApplicationData = function (key, data) {
		// Dropping the observer wiring is what makes the model serialisable at
		// all: `observers` is the back-reference that makes it circular.
		// JSON.stringify discards the model's function properties for free.
		var replacerFn = function (key, value) {
			if (key === 'observers' || key === 'changed') {
				return;
			}
			return value; // returning undefined omits the key from being serialized
		};

		// Serialised once and used by both stores below. For a six-omic job
		// this string is about 10 MB, so doing it twice is not free.
		var serialised = null;
		if (data != null) {
			try {
				serialised = JSON.stringify(data, replacerFn);
				/* The job model is stamped with the cache contract it was written
				   under (PA_JOB_CACHE_SCHEMA in app.js); the boot refuses a stale
				   stamp and recovers the job from the server instead. */
				if (data.jobID && typeof PA_JOB_CACHE_SCHEMA !== "undefined") {
					var stamped = JSON.parse(serialised);
					stamped.cacheSchema = PA_JOB_CACHE_SCHEMA;
					serialised = JSON.stringify(stamped);
				}
			} catch (err) {
				console.warn("Could not serialise " + key + " for storage.", err);
			}
		}

		if (key === "jobModel" && data != null) {
			// Ensure we have a jobID for IndexedDB primary key
			if (data.jobID) {
				// IndexedDB structured-clones whatever it is handed, and the
				// live job model cannot be cloned: 81 function properties and a
				// circular FeatureSet reference. So every save failed with
				//     DataCloneError: function(){} could not be cloned
				// and the jobs store held zero rows -- read straight out of the
				// browser's own database to check -- while the warning below
				// told the user the model had been saved there. It is written
				// on every job load, so the console carried the error every
				// time and dumped the whole object graph after it.
				//
				// The plain object sessionStorage already builds is clonable,
				// and a plain JSON round-trip is not enough on its own here:
				// without the replacer, JSON.stringify throws
				//     TypeError: Converting circular structure to JSON
				// on the same model.
				if (serialised !== null) {
					this.updateStoredApplicationDataIndexDB("jobs", JSON.parse(serialised));
				}
			} else {
				console.warn("Attempted to save jobModel to IndexedDB but jobID is missing.");
			}
		}

		if (window.sessionStorage) {
			if (data != null && serialised !== null) {
				try {
					sessionStorage.setItem(key, serialised);
				}
				catch (err) {
					if (key !== "jobModel") {
						showInfoMessage("Too much data", { message: "</br>The data to put in local storage exceeded the browser quota.</br> If you want to change the job sharing option, it will work. If not, please, try to reload the job and if this problem persists contact us.</br>Thank you.</br>", showButton: true });
					} else {
						console.warn("jobModel exceeded sessionStorage quota, but is saved in IndexedDB.");
					}
				}
			}
		}
	};
	/**
	*
	* @returns {undefined}
	*/
	this.cleanStoredApplicationData = function () {
		if (window.sessionStorage) {
			sessionStorage.removeItem("jobModel");
			sessionStorage.removeItem("pathwaysNetwork");
			sessionStorage.removeItem("visualOptions");
			sessionStorage.clear();
		}
		application.getMainView().clearSubViews();
		application.getMainView().showSignInDialog();
	};
	
	this.updateStoredApplicationDataIndexDB = function (storename, data) {
		
		var db = new Dexie("paintomics");

		/* "data" should be prepared to include the following fields */
		db.version(1).stores({
			networks: 'id',
			jobs: 'jobID'
		});		
		db.table(storename).put(data).then(function () {
			console.log("Data saved using IndexDB");
		}).catch(function (error) {
			console.error("Error saving data with IndexDB in store: " + storename, error);
			console.log("Data attempted to save:", data);
		});
		
	};
	
	this.getStoredApplicationDataIndexDB = function (storename, id, callback) {
		var db = new Dexie("paintomics");

		/* "data" should be prepared to include the following fields */
		db.version(1).stores({
			networks: 'id',
			jobs: 'jobID'
		});		
		db.table(storename).get(id).then(function (row) {
			console.log("Registry successfully retrieved from " + storename + " using the id " + id);
			return callback(row);
		}).catch(function (error) {
			console.log("There was an error retrieving from " + storename + " using the id " + id);
			return callback(null);
		});
	};

	this.getCredentialsParams = function (request_params) {
		var credentials = {};
		if (request_params != null) {
			credentials = request_params;
		}

		credentials.sessionToken = Ext.util.Cookies.get('sessionToken');
		credentials.userID = Ext.util.Cookies.get('userID');
		return credentials;
	};
}

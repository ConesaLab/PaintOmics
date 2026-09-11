//# sourceURL=Util.js
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
* - Observer
* - Observable
* - View
* - Model
*
* THIS FILE CONTAINS THE FOLLOWING FUNCTION DECLARATION
* - generateForm
* - showMessage
* - showInfoMessage
* - showSuccessMessage
* - showErrorMessage
* - ajaxErrorHandler
* - extJSErrorHandler
* - scaleValue
* - getAIMarkPaths
* - getAIMark
*
*/

/**
 * The AI mark's geometry, on its own, in a 24x24 box.
 *
 * Exists because one caller cannot use getAIMark(): the Step 1 hero diagram
 * draws the mark *inside* its own <svg>, placed by a transform in the diagram's
 * coordinate space, where a nested <svg> sized in px would not scale with the
 * viewBox. So it needs the paths without a wrapper.
 *
 * It had been meeting that need by keeping a second copy of them, retyped into a
 * 22-unit box, and the copy had drifted: a hexagon 15 units wide by 17.6 rather
 * than 13 by 15, stroke 1.5 rather than 1.4, opacity .5 rather than .45. Small
 * enough that nobody would call it a different logo, and visible when the hero's
 * card and the sentence below it are put side by side -- which is what the hero
 * does. Two copies of a mark is one copy too many however close they are, so the
 * geometry lives here and both mounts read it.
 *
 * @returns {string} the two <path> elements, in a 0 0 24 24 coordinate space.
 */
function getAIMarkPaths() {
    return '<path d="M12 4.5 L18.5 8.25 L18.5 15.75 L12 19.5 L5.5 15.75 L5.5 8.25 Z" ' +
        'fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" opacity="0.45"/>' +
        '<path d="M12 7.6 C12.37 9.73 14.27 11.63 16.4 12 C14.27 12.37 12.37 14.27 12 16.4 ' +
        'C11.63 14.27 9.73 12.37 7.6 12 C9.73 11.63 11.63 9.73 12 7.6 Z" fill="currentColor"/>';
}

/**
 * The PaintOmics AI mark.
 *
 * Every AI surface used to borrow an icon from somewhere else: the toolbar
 * button and the two Step 1 headings took Font Awesome's lightbulb, and the
 * chat launcher took the robot emoji. The design system rules both out for the
 * same reason -- a lightbulb says "idea", a robot says "chatbot", and neither
 * is what this feature does nor has anything to do with PaintOmics. The emoji
 * had a second problem: it renders in whatever colour and shape the platform's
 * font decides, so it was a different picture on every operating system.
 *
 * This is the system's mark: the brand hexagon from paintomics-mark.svg with
 * the six omic nodes removed, around a four-point spark.
 *
 * One drawing, not a family. This used to offer a second "swarm" centre -- three
 * agents converging on one answer -- which is a better description of what the
 * feature does, and it was used on the launcher and the Step 1 hero while the
 * button and the two headings kept the spark. That put two different pictures on
 * one feature, which is the thing a mark exists to prevent: nothing tells a
 * reader that the blob on the launcher and the star on the button are the same
 * offer. The spark won the tie rather than the swarm because of what the pixels
 * do, not because it describes the feature better. Rendered side by side at the
 * sizes this app actually asks for, the swarm's three 1.75r satellites and its
 * 2.4r centre merge into one dark cluster below about 20px, and four of the five
 * call sites are inline with text at 14-17px. The spark is still four clean
 * points at 14px. A mark that only survives at its largest use is not a mark.
 *
 * Sizing is derived, not chosen. The default is 1em, so the mark tracks whatever
 * text it sits beside -- an h3 heading, a button label, a sentence -- instead of
 * being hand-picked per call site. That is what stopped the five call sites from
 * drifting to 14/15/17/17px in the first place, and it is why the px argument is
 * only for the launcher, which has no text to take a size from.
 *
 * Everything is currentColor, so one mark serves white-on-blue in the launcher
 * and blue-on-white in a button without a second copy.
 *
 * @param {number} [size] px, both dimensions. Omit to size to 1em, which is
 *                        what every caller beside text should do.
 * @returns {string} SVG markup, ready to drop into an html string.
 */
function getAIMark(size) {
    // A bare number is px; anything else (undefined, null, 0) means "match the
    // surrounding text". Set through style rather than the width/height
    // attributes because those take only a bare number or a CSS length, and
    // browsers disagree about em there.
    var box = (typeof size === "number" && size > 0)
        ? size + "px"
        : "1em";

    // aria-hidden because every caller puts a readable label beside the mark;
    // announcing "PaintOmics AI" again would just repeat it.
    return '<svg class="po-ai-mark" viewBox="0 0 24 24" ' +
        'aria-hidden="true" focusable="false" ' +
        'style="display:inline-block;width:' + box + ';height:' + box + ';' +
        'vertical-align:-0.15em;flex-shrink:0">' +
        getAIMarkPaths() +
        '</svg>';
}

function Observer() {
    this.name = "";
    /**
     * This function set the name for the view
     * @chainable
     * @returns {View}
     */
    this.setName = function (name) {
        this.name = name;
        return this;
    };
    this.getName = function () {
        return this.name;
    };
    /**
     * This function updates the visual representation of the model.
     * @chainable
     * @returns {Observer}
     */
    this.updateObserver = function () {
        console.warn("Observer:updateObserver:" + this.name + ": Not implemented yet");
    };
}

function Observable() {
    this.observers = null;
    this.changed = false;
    this.getObservers = function () {
        if (this.observers === null) {
            this.observers = [];
        }
        return this.observers;
    };
    this.addObserver = function (observer) {
        if (observer.updateObserver !== undefined && this.getObservers().indexOf(observer) === -1) {
            this.getObservers().push(observer);
            console.log("OBSERVERS " + this.getObservers().length);
            if (debugging === true) {
                nObservers += 1;
                console.info("New observer added " + ((observer.name !== undefined) ? observer.name : observer.constructor.name) + " TOTAL OBSERVERS: " + nObservers);
            }
        }
    };
    this.clearChanged = function () {
        this.changed = false;
    };
    this.countObservers = function () {
        return this.getObservers().length;
    };
    this.deleteObserver = function (observer) {
        var _observers = this.getObservers();
        for (var i in _observers) {
            if (_observers[i] == observer) {
                //TODO: REMOVE THIS CODE
                if (debugging === true) {
                    nObservers -= 1;
                    console.info("Observer removed " + ((observer.name !== undefined) ? observer.name : observer.constructor.name) + " TOTAL OBSERVERS: " + nObservers);
                }
                this.getObservers().splice([i], 1);
                console.log("OBSERVERS " + this.getObservers().length);
            }
        }
    };
    this.hasChanged = function () {
        return this.changed;
    };
    this.notifyObservers = function () {
        if (this.changed) {
            var _observers = this.getObservers();
            for (var i in _observers) {
                _observers[i].updateObserver();
            }
            this.changed = false;
        }
    };
    this.setChanged = function () {
        this.changed = true;
    };
}

function View() {
    /*********************************************************************
     * ATTRIBUTES
     ***********************************************************************/
    this.component = null;
    this.model = null;
    this.controller = null;
    this.parent = null;
    /*********************************************************************
     * GETTERS AND SETTERS
     ***********************************************************************/
    this.getComponent = function (renderTo) {
        if (this.component === null) {
            this.initComponent(renderTo);
        }

        return this.component;
    };
    this.setComponent = function () {
        return this.component;
    };
    this.initComponent = function () {
        throw ("View:initComponent: Not implemented yet");
    };
    /**
     * This function load of the given model.
     * @chainable
     * @param {Model} model
     * @returns {View}
     */
    this.loadModel = function (model) {
        if (this.getModel() !== null) {
            this.model.deleteObserver(this);
        }
        this.model = model;
        this.model.addObserver(this);
        return this;
    };
    this.getModel = function () {
        return this.model;
    };
    /**
     * This function set the controller for the view
     * @chainable
     * @returns {View}
     */
    this.setController = function (controller) {
        this.controller = controller;
        return this;
    };
    this.getController = function () {
        return this.controller;
    };
    /**
     * This function set the parent for the view
     * @chainable
     * @returns {View}
     */
    this.setParent = function (parent) {
        this.parent = parent;
        return this;
    };
    this.getParent = function (parentName) {
        if (parentName !== undefined && this.parent !== null) {
            if (this.parent.getName() !== parentName) {
                return this.parent.getParent(parentName);
            }
        }
        return this.parent;
    };
    this.isVisible = function () {
        return this.getComponent().isVisible();
    };
}

View.prototype = new Observer();

function Model() {
}

Model.prototype = new Observable();

/**
 * This function shows a new Message dialog.
 * Available options are:
 *  - messageType: type of message  {info, warning or error}  [Default: info]
 *  - message: the message to show after the title. [Default: Empty string]
 *  - logMessage: the message to show at the console. [Default: the title value]
 *  - showTimeout: delay in seconds to show the message. [Default: 0]
 *  - closeTimeout: dialog will be automatically closed after n seconds. [Default: 0,  not close]
 *  - showButton: show/hide the close button [Default: false]
 *  - height, width: dimensions for the message. [Default: [70,500]]
 *  - callback: call this function after closing the dialog. [Default: null]
 *  - showSpin: show/hide the spinner [Default: false]
 *
 * @param {String} title the title for the dialog.
 * @param {Object} data contains all the different options for the dialog configuration
 *
 */

// Global variable used to show or not AJAX errors in the handlers.
ignoreOtherErrors = false;

/**
 * Seconds as something a person reads at a glance: "47s", "3m 05s", "1h 12m".
 * The server reports job times in raw seconds, which stops being legible around
 * the three-minute mark - and an example run takes longer than that.
 */
function formatDuration(totalSeconds) {
    var seconds = Math.max(0, Math.round(Number(totalSeconds) || 0));

    if (seconds < 60) {
        return seconds + "s";
    }

    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
        var rest = seconds % 60;
        return minutes + "m " + (rest < 10 ? "0" : "") + rest + "s";
    }

    return Math.floor(minutes / 60) + "h " + (minutes % 60) + "m";
}

// Highest percentage drawn so far, and the elapsed time it was drawn at. See
// the note on monotonicity in renderProgress; the pair resets when a new job
// starts, which is the only time elapsed goes backwards.
var paProgressLastPercent = 0;
var paProgressLastElapsed = 0;

/**
 * Draws the running-job bar from the two numbers the status endpoint returns.
 *
 * @param {Object} progress {elapsed, estimated} in seconds; estimated may be absent
 */
/**
 * Draws the bar from the server's own fraction, which is the modern path.
 *
 * The server now reports where the job actually is rather than a ratio of two
 * clocks, so there is nothing to reconstruct here: no max-percent ratchet (the
 * fraction is monotone at the source) and no guessing whether an absent estimate
 * means "too early to tell" or "overrunning" — the phase says which.
 *
 * @param {Object} p the server's `progress` object
 * @returns {Boolean} true if it drew; false if the payload was not usable
 */
function renderProgressDetailed(p) {
    if (!p || typeof p.fraction !== "number") {
        return false;
    }

    var container = $("#messageDialogProgress");
    var percent = Math.max(1, Math.min(99.9, p.fraction * 100));

    container.removeClass("paProgressIndeterminate");
    container.find(".paProgressFill").css("width", percent.toFixed(1) + "%");

    // "Mapping identifiers — step 2 of 3". The phase and the counts are facts;
    // only the seconds below are modelled.
    var label = (p.label || "Working") + " — step " + p.phaseIndex + " of " + p.phaseCount;
    if (p.detail) {
        label += " (" + p.detail + ")";
    }
    container.find(".paProgressElapsed").text(label);

    // A band, not a point. Identical input on an idle machine varies enough
    // run-to-run that two significant figures would be inventing precision; the
    // old single number was wrong by +58% at the end of a measured run.
    if (typeof p.remainingLow === "number") {
        container.find(".paProgressEta").text(
            "about " + formatDuration(p.remainingLow) + "–" + formatDuration(p.remainingHigh) + " left");
    } else {
        container.find(".paProgressEta").text("estimating…");
    }

    return true;
}

function renderProgress(progress) {
    // Preferred path: the job told us where it is. Falls through to the old
    // elapsed/estimate reconstruction when talking to a server that predates
    // JobProgress, or for a job type that does not report phases.
    if (renderProgressDetailed(progress.detailed)) {
        return;
    }

    var elapsed = Number(progress.elapsed) || 0;

    // `estimatedFinishTime` is the time *remaining*, not the total run time:
    // check_job_status computes an estimate, subtracts the elapsed time and
    // clamps the result at zero (see paintomicsserver.py). Reading it as a
    // total would put a job 68s into a 180s run at 61% instead of 38%, and the
    // bar would have looked entirely plausible while being wrong.
    var remaining = Number(progress.estimated) || 0;
    var total = elapsed + remaining;

    var container = $("#messageDialogProgress");
    var fill = container.find(".paProgressFill");

    // Once the estimate is spent the server clamps remaining to zero, so there
    // is nothing left to measure against. A bar pinned at 100% that keeps not
    // finishing reads as a hang, so it says so and goes back to a moving stripe.
    var determinate = remaining > 0 && total > 0;

    container.toggleClass("paProgressIndeterminate", !determinate);

    // Elapsed only goes backwards when a different job starts.
    if (elapsed < paProgressLastElapsed) {
        paProgressLastPercent = 0;
    }
    paProgressLastElapsed = elapsed;

    if (determinate) {
        // Floor at 2% so it is visibly a bar from the first poll, and cap at
        // 99% so it never claims to be finished while the job is still running.
        var percent = Math.max(2, Math.min(99, (elapsed / total) * 100));

        // The server recomputes its estimate on every poll, from job state that
        // is still changing, so the remaining time can grow - measured on a
        // live run, the bar slid from 48.7% back to 44.6%. A progress bar that
        // retreats reads as work being undone. Time only moves one way, so the
        // bar does too; the figure underneath still reports the new estimate
        // honestly when it changes.
        percent = Math.max(percent, paProgressLastPercent);
        paProgressLastPercent = percent;

        fill.css("width", percent.toFixed(1) + "%");
        container.find(".paProgressEta").text("about " + formatDuration(remaining) + " left");
    } else {
        fill.css("width", "");

        // Two different indeterminate states, and only one of them is "late".
        // The server's estimate is derived from input the job is still parsing,
        // so for the first ~30s it has nothing to estimate from and reports no
        // remaining time - that is not the job overrunning. Having drawn a real
        // percentage at some point is what tells the two apart.
        container.find(".paProgressEta").text(paProgressLastPercent > 0 ? "taking longer than estimated" : "");
    }

    container.find(".paProgressElapsed").text(elapsed > 0 ? "Running for " + formatDuration(elapsed) : "Starting…");
}

function showMessage(title, data) {
    var message = (data.message || "");
    var extra = (data.extra || "");
    var logMessage = (data.logMessage || title);
    var showTimeout = (data.showTimeout || 0);
    var closeTimeout = (data.closeTimeout || 0);
    var messageType = (data.messageType || "info");
    var showButton = (data.showButton || false);
    var showReportButton = (data.showReportButton || false);
    var height = (data.height || 180 + ((showButton === true) ? 70 : 0));
    var width = (data.width || 500);
    var callback = (data.callback || null);
    var showSpin = (data.showSpin || false);
    var progress = (data.progress || null);
    var append = (data.append || false);
    var itemId = (data.itemId || "div" + Date.now());

    title = '<i class="fa fa-' + data.icon + '"></i> ' + title;

    message = message.replace(/\[b\]/g, "<b>").replace(/\[\/b\]/g, "</b>").replace(/\[br\]/g, "</br>").replace(/\[ul\]/g, "<ul>").replace(/\[\/ul\]/g, "</ul>").replace(/\[li\]/g, "<li>").replace(/\[\/li\]/g, "</li>");
    /* Only when there IS something. `extra` carries a server stack trace on the
       dialogs that have one; the Step 1 form refusals have none, and
       JSON.stringify("") is the two-character string `""`, which went straight
       into the hidden body and from there into the report the user emails.
       That is the trailing `""` on the error report of 2026-08-26:

           Invalid Form. Please check the form errors. ""

       -- a reader's first guess is that PaintOmics failed to name the field,
       when in fact nothing was ever meant to be there. */
    extra = (extra === "") ? "" : JSON.stringify(extra);

    // The dismiss button used to be coloured by message type: red on an error,
    // amber on a warning, cyan on an info, green on a success. It does the same
    // thing in all four cases - it closes the dialog - and a filled red button
    // labelled "Close" reads as a destructive action, which is the one thing it
    // is not. The type is already stated twice on this surface, by the 4px
    // accent along the dialog's top edge and by the heading's colour, so the
    // button goes neutral and stops being a third, contradictory, statement.
    //
    // It also could not have worked as intended. The class was swapped with
    // `removeClass("btn-danger btn-success btn-warning btn-default")`, which
    // never listed btn-info - so once any info dialog had been shown, its cyan
    // stayed on the shared button for the rest of the session and every later
    // error dialog carried btn-info *and* btn-danger. The removeClass below
    // lists every class this function can add.
    var buttonClass = "btn-default";
    var color, icon, dialogClass;
    if (messageType === "error") {
        console.error(Date.logFormat() + logMessage);
        dialogClass = "errorDialog";
    } else if (messageType === "warning") {
        console.warn(Date.logFormat() + logMessage);
        dialogClass = "warningDialog";
    } else if (messageType === "info") {
        console.info(Date.logFormat() + logMessage);
        dialogClass = "infoDialog";
    } else { //success
        console.info(Date.logFormat() + logMessage);
        dialogClass = "successDialog";
    }

    var updateDialog = function (messageDialog) {
        $("#messageDialog").removeClass("errorDialog warningDialog infoDialog successDialog").addClass(dialogClass);
        if (append) {
            if ($("#messageDialogTitle #" + itemId).length > 0) {
                $("#messageDialogTitle #" + itemId).html(title);
            } else {
                $("#messageDialogTitle").append("</br><span id='" + itemId + "'>" + title + "</span>");
                height = messageDialog.getHeight() + 15;
            }
        } else {
            $("#messageDialogTitle").html(title);
        }
        $("#messageDialogBody").html(message.replace(/\n/g, "</br>"));
        // `hidden` is the half of a server message that is for the log rather
        // than the reader (see splitServerError); it travels with the report.
        $("#hiddenMessageDialogBody").text([data.hidden || "", extra].filter(Boolean).join("\n"));

        // The bar replaces the spinner rather than joining it - two things
        // moving at once to say the same "still working" is noise.
        if (progress) {
            renderProgress(progress);
            $("#messageDialogProgress").show();
            $("#messageDialogSpin").hide();
        } else {
            $("#messageDialogProgress").hide();
            if (showSpin) {
                $("#messageDialogSpin").show();
            } else {
                $("#messageDialogSpin").hide();
            }
        }

        if (showButton) {
            $("#messageDialogButton").removeClass("btn-danger btn-success btn-warning btn-info btn-default").addClass(buttonClass);
            $("#messageDialogButton").css({display: "inline-block"});
        } else {
            $("#messageDialogButton").css({display: "none"});
        }

        if (showReportButton) {
            $("#reportErrorButton").css({display: "inline-block"});
        } else {
            $("#reportErrorButton").css({display: "none"});
        }

        // Width first, then height. How tall the panel is depends on where the
        // message wraps, and it wraps at whatever width the window has at the
        // moment it is measured. Measuring before setWidth measured a window
        // that had never been given one: on the first dialog of a session
        // ExtJS had shrink-wrapped it around a body that was still empty when
        // the window was created, so the welcome message was measured at five
        // wrapped lines, and the window kept that height after setWidth
        // reflowed the text to two. That was 78px of blank space under the
        // Close button. Later dialogs reuse a window already 500 wide and so
        // were always correct, which is why this only ever looked wrong once
        // per page load.
        messageDialog.setWidth(width);

        // The panel is the whole of the content, so the window adds only its
        // own frame: everything between its outer edge and the content box its
        // body actually offers. Measuring that rather than hard-coding it keeps
        // this honest if the border changes - main.css gives #messageDialog a
        // 4px top accent and 1px elsewhere, and the window body adds 1px of its
        // own top and bottom, so it is 7px today. The constant it replaces was
        // 30, and the ~23px of that which was not frame is why the dialog still
        // sat loose even once the wrapping above was right.
        //
        // clientHeight rather than body.getHeight(): the latter is a border box
        // and counts 2px the content cannot use, which left the panel
        // overflowing by that much on every dialog. The body is overflow:hidden
        // so it was clipping rather than scrolling, absorbed by the panel's own
        // bottom padding and invisible - but it is the spinner and progress
        // dialogs, the tightest of them, that had the least padding to spare.
        // Ceil for the same reason: the panel measures fractionally (145.66 for
        // the spinner) and a truncated height clips the last pixel of it.
        var bodyDom = messageDialog.body ? messageDialog.body.dom : null;
        var frame = (bodyDom && bodyDom.clientHeight > 0)
                ? messageDialog.getHeight() - bodyDom.clientHeight : 7;
        messageDialog.setHeight(Math.ceil($("#messageDialogPanel").outerHeight()) + frame);
        messageDialog.center();
    };

    var messageDialog = Ext.getCmp("messageDialog");

    //APPEND ONLY IN CASE THAT DIALOG IS VISIBLE AND THE DIALOG CLASS IS THE SAME THAN THE REQUESTED.
    append = append && messageDialog !== undefined && messageDialog.isVisible() && $("#messageDialog").hasClass(dialogClass);

    //FIRST TIME SHOWN
    if (messageDialog === undefined) {
        messageDialog = Ext.create('Ext.window.Window', {
            id: "messageDialog", header: false, closeAction: "hide",
            modal: true, closable: false,
            /* data-guides="ignore": a centred floating dialog has no left rail
               against the page - its x is half of whatever width the viewport
               happens to have - and the guides overlay's column model cannot
               hold a modal's contents together (they scatter to the body group
               and report against a rail none of them could ever sit on). */
            html: "<div id='messageDialogPanel' data-guides='ignore'>" +
                " <h4 id='messageDialogTitle'></h4>" +
                ' <div id="messageDialogBody"></div>' +
                ' <div id="hiddenMessageDialogBody" style="display:none;"></div>' +
                // A running job reported itself as a spinning GIF plus two
                // sentences of arithmetic ("Time spent: 47 seconds", "Estimated
                // running time: 180 seconds") that the reader had to divide to
                // learn anything. The server sends both numbers, so the bar can
                // just show the answer.
                ' <div id="messageDialogProgress" class="paProgress">' +
                '   <div class="paProgressTrack"><div class="paProgressFill"></div></div>' +
                '   <div class="paProgressMeta">' +
                '     <span class="paProgressElapsed"></span>' +
                '     <span class="paProgressEta"></span>' +
                '   </div>' +
                ' </div>' +
                ' <p id="messageDialogSpin" ><img src="resources/images/loadingpaintomics2.gif"></img></p>' +
                // A flex row (main.css .messageDialogActions), not a centred
                // run of floats: with a third action -- the AI offer that
                // format-panel.js adds on a file error -- the floats wrapped
                // into a ragged 2x2 block with the offer on its own line.
                ' <div id="messageDialogActions" class="messageDialogActions">' +
                '   <a id="reportErrorButton" class="button btn-warning"><i class="fa fa-bug"></i> Report error</a>' +
                '   <a id="messageDialogButton" class="button btn-default">Close</a>' +
                " </div>" +
                "</div>",
            listeners: {
                boxready: function () {
                    updateDialog(messageDialog);

                    $("#messageDialogButton").click(function () {
                        // Restore visualizing AJAX errors
                        ignoreOtherErrors = false;

                        messageDialog.close();
                    });
                    $("#reportErrorButton").click(function () {
                        // Joined on the parts that have content, so a dialog
                        // with no stack trace does not end in a blank line.
                        sendReportMessage("error", [
                            $("#messageDialogTitle").text(),
                            $("#messageDialogBody").text(),
                            $("#hiddenMessageDialogBody").text()
                        ].filter(function (part) { return part !== ""; }).join("\n"));
                    });
                }
            }
        });
    } else {
        updateDialog(messageDialog);
    }

    $.wait(function () {
        messageDialog.show();

        if (closeTimeout !== 0) {
            $.wait(function () {
                messageDialog.hide();
            }, closeTimeout);
        }
        if (callback !== null) {
            callback();
        }
    }, showTimeout);
}

function showInfoMessage(title, data) {
    data = (data == null) ? {} : data;
    data.messageType = "info";
    data.icon = (data.icon || 'info-circle');
    showMessage(title, (data === undefined) ? {} : data);
}

function showSuccessMessage(title, data) {
    data = (data == null) ? {} : data;

    data.messageType = "success";
    data.icon = (data.icon || 'check-circle');
    showMessage(title, (data === undefined) ? {} : data);
}

function showWarningMessage(title, data) {
    data = (data == null) ? {} : data;
    data.messageType = "warning";
    data.icon = (data.icon || 'exclamation-triangle');
    showMessage(title, (data === undefined) ? {} : data);
}

function showErrorMessage(title, data) {
    data = (data == null) ? {} : data;
    data.messageType = "error";
    data.showButton = (data.showButton == null) ? true : data.showButton;
    data.showReportButton = (data.showReportButton == null) ? true : data.showReportButton;
    data.icon = (data.icon || 'exclamation-circle');
    showMessage(title, data);
}

/**
* The colour a metagene cluster is drawn in, wherever it is drawn.
*
* This lived twice, once on the Step 3 view and once on Step 4, as two copies of
* the same function differing only in their palette - the Step 4 copy carrying a
* "TODO: MOVER A OTRO LADO??" that had been asking for this since 2021.
*
* The copies had drifted, and the drift was visible. "fixed cluster issues"
* (ef2a2237, 2022-02-16) edited the Step 3 palette at indices 0, 2 and 3 and did
* not touch Step 4, so from then on a cluster changed colour when the user moved
* between the two views: cluster 3 was brown #a0512c in the Step 3 metagenes
* chart and green #33a02c in the Step 4 trend chart, cluster 2 tan #dfbd8a there
* and pale green #b2df8a here. A cluster number means nothing on its own - the
* colour is how it is recognised across the analysis - so the same cluster
* wearing two colours is the one thing this palette must not do. Observed on
* mmu01210, which draws clusters 2, 3, 9, 10 and 11 at once.
*
* Step 4 also stopped at 15 entries against Step 3's 20, so clusters 15-19 fell
* through to the grey fallback in one view and were coloured in the other.
*
* This is the Step 3 list: the newer one, and the one the 2022 fix chose.
*
* @param  {String} cluster the cluster number, optionally prefixed with letters
* @returns {String} the hexadecimal colour code, or #333 if out of range
*/
function getClusterColor(cluster) {
    var COLORS = ["#b8a6e3", "#1f78b4", "#dfbd8a", "#a0512c", "#fb9a99", "#e31a1c",
                  "#fdbf6f", "#ff7f00", "#cab2d6", "#6a3d9a", "#eded84", "#b15928",
                  "#003b46", "#f5b549", "#B38867", "#009999", "#008888", "#007777",
                  "#006666", "#005555"];

    cluster = Number.parseInt(String(cluster).replace(/[a-z]*/, ""));

    if (!Number.isNaN(cluster) && cluster < COLORS.length) {
        return COLORS[cluster];
    }

    console.warn("Unable to find a color for cluster " + cluster);
    return "#333";
}

function initializeTooltips(query, options) {
    options = (options || {side: 'bottom', interactive: true, maxWidth: 350});
    $(query).tooltipster(options);
}

function deleteCredentials() {
    ignoreOtherErrors = false;
    Ext.util.Cookies.clear("sessionToken", location.pathname);
    Ext.util.Cookies.clear("userID", location.pathname);
    Ext.util.Cookies.clear("userName", location.pathname);
    Ext.util.Cookies.clear("nologin", location.pathname);
    setTimeout(function () {
        location.reload();
    }, 2000);
}


/*
 * The answer a request got, when there is one the client can act on.
 *
 * Every handled server error is JSON with a message. nginx's 502 and 504
 * pages are HTML, a dropped connection has no body, and a request the browser
 * timed out has none either. Returns the parsed object, or null for anything
 * that is not a JSON object -- which is the whole test the status polls
 * apply: a readable answer is a verdict about the job, an unreadable one is a
 * verdict about the wire, and only the second is worth asking again.
 */
function readableAnswer(jqXHR) {
    var body = jqXHR && jqXHR.responseText;
    if (typeof body !== "string" || body === "") { return null; }
    try {
        var parsed = JSON.parse(body);
        return (parsed && typeof parsed === "object") ? parsed : null;
    } catch (notJson) {
        return null;
    }
}

/*
 * What happened to a request that got no readable answer, in words: the HTTP
 * status when there was one ("HTTP 504 Gateway Time-out"), otherwise which
 * of the two no-status cases it was. jQuery reports "timeout" as the
 * textStatus of a request its own `timeout` cut off, and a status of 0 for a
 * connection that was dropped or refused.
 */
function describeUnansweredRequest(jqXHR, textStatus) {
    var status = Number(jqXHR && jqXHR.status) || 0;
    if (status > 0) {
        var text = (jqXHR && jqXHR.statusText && jqXHR.statusText !== "error")
            ? " " + jqXHR.statusText : "";
        return "HTTP " + status + text;
    }
    if (textStatus === "timeout" || (jqXHR && jqXHR.statusText === "timeout")) {
        return "no answer within " + Math.round(JOB_STATUS_REQUEST_TIMEOUT / 1000) + " s";
    }
    return "the connection was dropped before any answer";
}

/* The line the dialogs show for an answer they cannot read. It used to be
   "Unable to parse the error message.", which describes the client's
   difficulty; the guest whose report reached us on 2026-09-11 had been shown
   exactly that for an nginx 504, and neither they nor we could tell from the
   report what had happened. Say what the server sent instead. */
function unreadableAnswerMessage(jqXHR, textStatus) {
    return "The server did not send a readable answer (" +
        describeUnansweredRequest(jqXHR, textStatus) + ").";
}

/*
 * When to try a status poll again, and when to stop.
 *
 * `outage` is the poll chain's bookkeeping for the current run of unanswered
 * requests: {since, attempts}, reset to null by the caller as soon as the
 * server answers, so a long job with the occasional blip never runs out.
 * `now` is Date.now() in the browser (the tests pass a clock). Returns the
 * delay in ms before the next attempt, or -1 once the budget is spent.
 *
 * Delays double from JOB_STATUS_RETRY_FIRST_DELAY and stop growing at
 * JOB_STATUS_RETRY_MAX_DELAY: 5, 10, 20, 40, 60, 60 s ... The budget is
 * wall-clock time since the first failure, so a retry is scheduled only when
 * it would start inside JOB_STATUS_RETRY_BUDGET; after that the caller
 * reports. Time, not a count, because each attempt can itself take
 * JOB_STATUS_REQUEST_TIMEOUT to fail.
 */
function statusPollRetryDelay(outage, now) {
    outage.attempts = (outage.attempts || 0) + 1;
    if (!outage.since) { outage.since = now; }
    var delay = Math.min(JOB_STATUS_RETRY_FIRST_DELAY * Math.pow(2, outage.attempts - 1),
                         JOB_STATUS_RETRY_MAX_DELAY);
    if ((now - outage.since) + delay > JOB_STATUS_RETRY_BUDGET) { return -1; }
    return delay;
}

function ajaxErrorHandler(responseObj) {
    if (debugging === true)
        debugger

    if (ignoreOtherErrors) {
        return;
    }

    ignoreOtherErrors = true;

    var err;
    try {
        // Parse, don't eval. The body is JSON from the server and it echoes
        // user-supplied values back (a rejected pathway ID, a file name), so
        // eval would execute whatever ends up in there if the escaping were
        // ever wrong. extJSErrorHandler below already uses JSON.parse; this
        // keeps both paths consistent and drops the eval entirely.
        err = JSON.parse(responseObj.responseText);
    } catch (error) {
        err = {message: unreadableAnswerMessage(responseObj)};
    } finally {
        err = err || {message: unreadableAnswerMessage(responseObj)};
    }

    if (err.message && err.message.indexOf("User not valid") !== -1) {
        showErrorMessage("Invalid session", {
            message: "<b>Your session is not valid.</b> Please sign-in again to continue.",
            callback: deleteCredentials,
            showButton: false,
            showReportButton: false
        });
        return;
    }

    var adminLink = "If the error persists, please contact the " +
        "<a href='mailto:paintomicsai@gmail.com' target='_blank'> administrator</a>.";

    // A status poll that ran out of retries reports through here with a body
    // it wrote itself (unansweredStatusResponse in JobController.js). The
    // failure is the wire, not the server's code, so neither the cache advice
    // nor the "happened on the server" wording below applies, and the title
    // says what it was rather than "Internal error".
    if (err.unanswered) {
        showErrorMessage("The server did not answer", {
            message: err.message + "</br>" + adminLink,
            hidden: err.detail || "",
            showButton: true
        });
        return;
    }

    // The server reports its own exceptions with an "extra" block naming the
    // Python file and exception type. Telling the user to clear their browser
    // cache for one of those sends them after something they cannot fix -- a
    // missing reference file or an expired API key on the server is not stale
    // client state. Keep that advice for errors with no server-side origin,
    // where a stale cached model genuinely can be the cause.
    var serverSide = !!(err.extra && (err.extra.file_name || err.extra.exc_type));
    var halves = splitServerError(err.message);

    showErrorMessage("Oops..Internal error!", {
        message: halves.readable + "</br>" + (serverSide
            ? "This happened on the server, so retrying in the browser will not help. " + adminLink
            : "Try running it in private mode or clear your web cache in your browser.</br>" + adminLink),
        extra: err.extra,
        hidden: halves.technical,
        showButton: true
    });
}

/*
 * The two halves of a servlet error.
 *
 * ServerErrorManager prefixes every message with where it was raised --
 *
 *     Exception: AT PathwayAcquisitionServlet.py: pathwayAcquisitionStep1_PART2.
 *     ERROR MESSAGE: Errors detected in input files, ...
 *
 * -- which is for the log. The dialog printed the whole line, so the person
 * who pressed Run read a Python file name and a function before the sentence
 * that was written for them (the 2026-08-27 screenshot opens with exactly
 * that). The readable half goes on screen; the prefix goes to the hidden body
 * that "Report error" already sends, so the report loses nothing. Same split
 * UserController, JobController and convert-drawer already make on their own
 * paths.
 */
function splitServerError(message) {
    var raw = String(message || "");
    var at = raw.indexOf("ERROR MESSAGE:");
    if (at === -1) return { readable: raw, technical: "" };
    return { readable: raw.slice(at + "ERROR MESSAGE:".length).trim() || raw,
             technical: raw.slice(0, at).trim() };
}

/* Markup stripped to plain text: tags out, whitespace collapsed, edges trimmed. */
function plainFieldText(html) {
    return String(html || "")
        .replace(/<\/li>\s*<li[^>]*>/gi, " \u2014 ")
        .replace(/<[^>]*>/g, " ")
        .replace(/\s+/g, " ")
        .replace(/^ | $/g, "");
}

/* The plain text of a field's error. ExtJS wraps several in a <ul>, and a
   custom markInvalid() message only ever appears in the active error, not in
   getErrors(), so this reads the active error and strips the markup. Several
   errors are joined with a dash rather than glued into one sentence. */
function fieldErrorText(field) {
    var error = (field && field.getActiveError) ? field.getActiveError() : "";
    return plainFieldText(error);
}

/* The first field in `fields` (an array, in DOM order) that carries an active
   error and can be shown: a hidden field is never named -- it cannot be shown
   to anybody -- and an unrendered one cannot be scrolled to. Shared by
   checkForm()'s refusal (PA_Step1JobView.firstFormError) and the failure
   handler's client-abort branch, so both name the same field. */
function firstVisibleInvalidField(fields) {
    var i;
    for (i = 0; i < fields.length; i++) {
        if (fields[i].hasActiveError && fields[i].hasActiveError() &&
            fields[i].isVisible(true) && fields[i].getEl()) {
            return fields[i];
        }
    }
    return null;
}

/* The refusal that names one field: scrolled into view first, so closing the
   dialog leaves the reader looking at it, then the dialog quoting its label
   and what it wants. Shared by checkForm()'s refusal (JobController's
   showInvalidStep1FormMessage) and by extJSErrorHandler when ExtJS's own
   submit-time validation is the one refusing. No field -- nothing marked
   anywhere, or only hidden fields -- keeps the old wording, which is the
   shape of a bug and stays reportable.

   Naming a field is NOT reportable, for the same reason an empty form is not:
   the software has just done its job. It found what was wrong, said what the
   field wants and scrolled the reader to it, so what is left is a box to fill
   in. Offering Report error there -- in an orange button sitting left of
   Close, where it reads as the primary action -- invites the reader to mail
   the developers about their own typing. Both "Error notification" mails of
   2026-08-30 09:24 UTC were this branch, thirteen seconds apart, from one
   guest who then fixed the form and ran the job (E7B07e2G5X) unaided. Every
   such mail is one more thing between the maintainers and a real report. */
/* `opts.reportable` is the one thing the two callers disagree about. See the
   note on extJSErrorHandler's CLIENT_INVALID branch: a refusal the software
   raised BEFORE it tried to submit is the user's to fix, and one raised after
   it already approved the form is the software contradicting itself. */
function showInvalidFieldMessage(field, opts) {
    if (!field) {
        showErrorMessage("Invalid Form. </br> Please check the form errors.",
            {height: 150, width: 400, showReportButton: true});
        return;
    }
    try {
        field.getEl().dom.scrollIntoView({block: "center"});
    } catch (ignored) {
        /* An unrendered field cannot be scrolled to; the message still names it. */
    }
    /* Six shipped labels carry a <br> ("Regions file <br>(BED + Quantification)"),
       and that is the label the file widget's inner textfield inherits, so the
       markup is stripped exactly as the error text is. */
    var label = plainFieldText(field.fieldLabel).replace(/\s*:\s*$/, "");
    var reason = fieldErrorText(field) || "Please check this field.";
    showErrorMessage("Invalid Form. </br>" +
        (label ? " <b>" + Ext.String.htmlEncode(label) + "</b>: " : " ") +
        Ext.String.htmlEncode(reason),
        {height: 150, width: 400, showReportButton: !!(opts && opts.reportable)});
}

function extJSErrorHandler(form, responseObj) {
    if (debugging === true)
        debugger

    /* ExtJS runs form.isValid() again inside submit(), and when one of its own
       validators refuses -- allowBlank, maxLength, a validator function -- it
       calls this handler with failureType "client" and no response at all. That
       used to be reported as "Oops..Internal error! Unable to parse the error
       message.", which reads as a server fault. Nothing reached the server:
       name the field and what it wants, the way checkForm()'s refusal does. */
    if (responseObj && responseObj.failureType === Ext.form.action.Action.CLIENT_INVALID) {
        /* The complex-path caller destroys its temporary form before calling
           this handler (its monitor is gone), and a hidden field is never the
           one named: either falls through to the generic wording. */
        /* The form panel's fields in DOM order, as checkForm()'s refusal reads
           them; the form's own Monitor keeps insertion order, and a container
           put back on the form lands last. */
        var fields = (form && form.monitor && form.owner) ? form.owner.query("field") : [];
        /* Reportable, unlike checkForm()'s refusal, and the difference is not
           cosmetic. Every path that reaches this handler has ALREADY passed
           the application's own validation -- JobController's four submits all
           gate on `checkForm() === true`, DataManagementController.submitForm
           on `form.isValid()` -- so arriving here means ExtJS's submit-time
           revalidation contradicted the check that just approved the form.
           That is a bug shape, not typing.

           It is also a bug shape we have shipped: #109's "Other data type"
           panel could never be submitted because an `allowBlank: false` on an
           optional field named a real one ("Relevant File Type: This field is
           required") while aborting the submit. It hid for five years, on
           guest sessions that leave no job and no server-side trace, and what
           finally surfaced it was a user pressing Report error on a dialog
           that named a field. Take the button off this branch and the next one
           of those is invisible again. */
        showInvalidFieldMessage(firstVisibleInvalidField(fields), {reportable: true});
        return;
    }

    var err;
    try {
        err = JSON.parse(responseObj.response.responseText);
    } catch (error) {
        err = {message: unreadableAnswerMessage(responseObj.response)};
    }

    if (err.message && err.message.indexOf("User not valid") !== -1) {
        showErrorMessage("Invalid session", {
            message: "<b>Your session is not valid.</b> Please sign-in again to continue.",
            callback: deleteCredentials,
            showButton: false,
            showReportButton: false
        });
        return;
    }

    var halves = splitServerError(err.message);
    showErrorMessage("Oops..Internal error!", {
        message: halves.readable + "</br>Please try again later.</br>If the error persists, please contact your web <a href='mailto:paintomicsai@gmail.com' target='_blank'> administrator</a>.",
        hidden: halves.technical,
        showButton: true
    });
}


/**
 * POST a report, organism request or contact message to the developers.
 *
 * `extra` is merged into the form as further fields -- the organism request
 * sends the organism as `specie` and `specieCode` beside the HTML message it
 * always sent, so the servlet can refuse one that is already installed
 * without parsing HTML. That refusal comes back as success=false with
 * installed=true, and is shown as a warning: showErrorMessage's dialog offers
 * a "report this error" button, which turned a refusal into an invitation to
 * file an error report about it.
 */
function sendReportMessage(type, message, fromEmail, fromName, extra) {
    showInfoMessage("Sending message to developers...", {logMessage: "Sending new report...", showSpin: true});
    $.ajax({
        type: "POST", headers: {"Content-Encoding": "gzip"},
        url: SERVER_URL_DM_SEND_REPORT,
        data: $.extend({type: type, message: message, fromEmail: fromEmail, fromName: fromName}, extra || {}),
        success: function (response) {
            if (response.success === false && response.installed === true) {
                showWarningMessage(response.organism + " is already installed", {
                    message: response.errorMessage, showButton: true,
                    logMessage: "Organism request refused: " + response.organism + " is installed"
                });
                return;
            }
            if (response.success === false) {
                showErrorMessage(response.errorMessage);
                return;
            }
            showSuccessMessage("Thanks for the report</br>We will contact you soon!", {
                logMessage: "Sending report... DONE",
                closeTimeout: 1
            });
        },
        error: ajaxErrorHandler
    });
}

/**
 * This function scales a given value for an intervale min/max
 */
function scaleValue(x, min, max) {
    //SCALE FROM [min, max] TO [a, b]
    //f(x) = (((b - a)*(x - min))/(max - min)) + a
    //SCALE FROM [min, max] TO [-1, 1]
    //f(x) = (((1 + 1)*(x - min))/(max - min)) - 1
    //     = ((2 * (x - min))/(max - min)) - 1
    var a = -1,
        b = 1;
    return ((x === 0) ? 0 : ((((b - a) * (x - min)) / (max - min)) + a));
};

/**
 * Builds the contents strip for the results page.
 *
 * Step 3 stacks eight independent analyses - the pathway summary, the database
 * breakdown, the classification chart, the network, the metabolite hub and
 * class analyses, the enrichment table - down roughly 2900px of scroll, with no
 * way to see what is there or to reach any of it except by scrolling past
 * everything above it. This reads the headings that are actually on the page and
 * turns them into a jump list.
 *
 * Derived from the DOM rather than from a hardcoded list on purpose: which
 * analyses appear depends on the omics submitted and on the databases chosen -
 * a run without metabolomics has no hub analysis, a KEGG-only run has no
 * Reactome tab - so a fixed list would advertise sections that are not there.
 *
 * Anchors are generated because the markup cannot supply them: two of the
 * headings already carry the *same* id ("EnrichmentSection"), so reusing the
 * existing ids would produce a link that always jumps to the first of the pair.
 */
/**
 * The contents strip for the results page.
 *
 * Step 3 stacks eight independent analyses down roughly 2900px of scroll with no
 * way to see what is there or to reach any of it except by scrolling past
 * everything above. This lists them and jumps to them.
 *
 * Split deliberately in two: buildAnalysisTOC() only writes markup, and all the
 * behaviour lives in one delegated block installed once at the bottom of this
 * section. Everything is recomputed from the DOM at event time and nothing is
 * held in a closure, because on this page nothing survives being held:
 *
 *   - Heading nodes captured at build time are detached when the classification,
 *     network and enrichment views re-render, and scrolling a detached element
 *     is silent rather than an error.
 *   - An id or data attribute stamped on a heading goes with it.
 *   - Listeners attached to the strip's own links are dropped when ExtJS
 *     rebuilds the centre panel - the strip keeps its markup and its labels, so
 *     it still looks correct while doing nothing at all.
 *
 * Sections are matched by heading text, which is the only thing that survives
 * all three.
 */
function paTocRoot() {
    return document.querySelector('#mainViewCenterPanel');
}

function paTocNorm(el) {
    return el.textContent.trim().replace(/\s+/g, ' ');
}

/**
 * The analyses currently on the page, one entry each.
 *
 * One per .contentbox rather than one per <h2>: the network view titles its own
 * side panels with h2 too - "Details", "Tools" - and those are controls inside
 * an analysis, not analyses. Read fresh every time, because which sections exist
 * depends on the omics submitted and the databases chosen.
 */
function paTocSections() {
    var root = paTocRoot();
    if (!root) { return []; }
    var claimed = [];
    var kept = Array.prototype.filter.call(root.querySelectorAll('h2'), function (h) {
        if (h.offsetParent === null || paTocNorm(h).length < 3) { return false; }
        if (h.closest('[id^="networkSettingsPanel"], [id^="networkDetailsPanel"], .lateralOptionsPanel')) {
            return false;
        }
        var box = h.closest('.contentbox');
        if (!box) { return true; }
        if (claimed.indexOf(box) !== -1) { return false; }
        claimed.push(box);
        return true;
    });

    // Sections laid out side by side are one line in the contents, not two.
    // "Pathways selection" and "Pathways summary" are the two halves of the same
    // top row and share a vertical position exactly - measured at -2548 for
    // both - so listing them separately gave two entries that scroll to the
    // same place and that no scroll-derived rule could ever tell apart.
    var lastTop = null;
    return kept.filter(function (h) {
        var top = h.getBoundingClientRect().top;
        if (lastTop !== null && Math.abs(top - lastTop) < 4) { return false; }
        lastTop = top;
        return true;
    });
}

/* The page does not scroll the window. MainView gives the centre region
   `overflowY: auto`, so that panel is the scroller and every window.scrollBy
   against it is a no-op. */
function paTocScroller() {
    var root = paTocRoot();
    var n = root;
    while (n && n !== document.body) {
        var st = window.getComputedStyle(n);
        if (/(auto|scroll)/.test(st.overflowY) && n.scrollHeight > n.clientHeight) { return n; }
        n = n.parentElement;
    }
    return null;
}

/**
 * How far below the viewport top a heading has to land to be readable.
 *
 * The strip only counts when it is a strip. Above 1200px the contents render as
 * a fixed column *beside* the results, where they occupy no vertical space at
 * all - adding their height there put the offset at ~312px instead of ~103px,
 * and every jump overshot while the highlight tracked the wrong section.
 * `position: fixed` is what distinguishes the two, set by the media query.
 */
function paTocOffset() {
    var header = document.querySelector('.mainTopToolbar');
    var nav = document.querySelector('.pa-toc');
    var navHeight = 0;
    if (nav && window.getComputedStyle(nav).position !== 'fixed') {
        navHeight = nav.offsetHeight;
    }
    return (header ? header.offsetHeight : 50) + navHeight + 12;
}

var paTocRaf = null;

/* A click wins over the scroll position for a moment afterwards.
   "Pathways selection" and "Pathways summary" are the two halves of the same
   top row, so they sit at the SAME vertical position - no scroll-derived rule
   can tell them apart, and the spy always resolved to whichever came last.
   Combined with a jump that is a no-op when you are already at the top, both
   entries appeared to do nothing at all when clicked. Pinning the clicked one
   briefly makes the click the authority, which is what a reader means by it. */
var paTocPinned = null;
var paTocPinnedUntil = 0;

function paTocJumpTo(label) {
    var target = null;
    paTocSections().forEach(function (h) { if (paTocNorm(h) === label) { target = h; } });
    if (!target) { return; }
    var sc = paTocScroller();
    if (!sc) {
        window.scrollTo(0, target.getBoundingClientRect().top + window.pageYOffset - paTocOffset());
        return;
    }
    var delta = target.getBoundingClientRect().top - sc.getBoundingClientRect().top;
    var destination = sc.scrollTop + delta - paTocOffset();

    // scrollTop assignment, not scrollTo({behavior: 'smooth'}). The smooth form
    // silently does nothing on this element - measured: every value resolves
    // correctly (scroller found, section matched, offset 103) and scrollTop moves
    // 7.5 to 8. The same element accepts an instant scroll to 2658 without
    // complaint, so something in the ExtJS scroller swallows the animated form.
    // Animated here by hand instead, which cannot be intercepted.
    // Cancel anything still in flight, so two jumps in quick succession do not
    // animate against each other.
    if (paTocRaf) { window.cancelAnimationFrame(paTocRaf); paTocRaf = null; }

    var start = sc.scrollTop;
    var change = destination - start;
    // Not a silent return when there is nowhere to go: being already at the
    // target is a successful jump, and the strip still has to say so.
    if (Math.abs(change) < 2) { paTocMarkCurrent(); return; }
    var began = null;
    var duration = 300;

    // Re-measure and correct repeatedly, not once. The grids render their rows
    // lazily, so arriving somewhere makes content above it materialise and push
    // the target down again - a single correction landed sections hundreds of
    // pixels out, and the further down the page the worse it got. This nudges
    // until the residual stops moving or the budget runs out, which is what
    // "scroll to this section" has to mean on a page that grows as you scroll
    // into it.
    function settle(attempt) {
        var residual = target.getBoundingClientRect().top - sc.getBoundingClientRect().top - paTocOffset();
        if (Math.abs(residual) > 3 && attempt < 8) {
            sc.scrollTop = sc.scrollTop + residual;
            window.setTimeout(function () { settle(attempt + 1); }, 110);
            return;
        }
        paTocMarkCurrent();
    }
    function step(now) {
        if (began === null) { began = now; }
        var t = Math.min(1, (now - began) / duration);
        // ease-out cubic
        sc.scrollTop = start + change * (1 - Math.pow(1 - t, 3));
        if (t < 1) { paTocRaf = window.requestAnimationFrame(step); return; }
        settle(0);
    }
    paTocRaf = window.requestAnimationFrame(step);
}

function paTocMarkCurrent() {
    var nav = document.querySelector('.pa-toc');
    if (!nav) { return; }
    var list = nav.querySelector('.pa-toc-list');
    var links = nav.querySelectorAll('.pa-toc-link');
    // +28, not a few pixels: paTocJumpTo parks a heading 12px *below* the strip,
    // so with a tight boundary the section just jumped to has not "passed" yet
    // and the strip highlights the one above it - disagreeing with itself the
    // moment it is used.
    // Derived from exactly where paTocJumpTo parks a heading, rather than
    // measured independently. Two separate calculations disagreed the moment
    // the strip became a sidebar: jumps landed headings at 112 while the
    // boundary sat at 78, so every jump highlighted the section above the one
    // it had just moved to. Same basis, plus a little slack, cannot drift.
    var sc = paTocScroller();
    var base = sc ? sc.getBoundingClientRect().top : 0;
    var boundary = base + paTocOffset() + 16;
    var currentLabel = null;
    paTocSections().forEach(function (h) {
        if (h.getBoundingClientRect().top <= boundary) { currentLabel = paTocNorm(h); }
    });
    if (paTocPinned && new Date().getTime() < paTocPinnedUntil) {
        currentLabel = paTocPinned;
    } else {
        paTocPinned = null;
    }
    var active = null;
    Array.prototype.forEach.call(links, function (l) {
        var isCurrent = l.getAttribute('data-toc-text') === currentLabel;
        l.classList.toggle('current', isCurrent);
        if (isCurrent) { active = l; }
    });
    // The strip is a single scrolling line, so the pill being marked can sit
    // outside it - the highlight would be doing its job where it cannot be seen.
    // Horizontal only; scrollIntoView() would move the page and fight the reader.
    if (active && list) {
        var pill = active.getBoundingClientRect();
        var view = list.getBoundingClientRect();
        if (pill.left < view.left || pill.right > view.right) {
            list.scrollLeft += (pill.left - view.left) - (view.width - pill.width) / 2;
        }
    }
}

(function paTocInstall() {
    var ticking = false;
    function onScroll() {
        if (ticking) { return; }
        ticking = true;
        window.requestAnimationFrame(function () { ticking = false; paTocMarkCurrent(); });
    }
    document.addEventListener('click', function (ev) {
        var link = ev.target && ev.target.closest ? ev.target.closest('.pa-toc-link') : null;
        if (!link) { return; }
        ev.preventDefault();
        var label = link.getAttribute('data-toc-text');
        paTocPinned = label;
        paTocPinnedUntil = new Date().getTime() + 1400;
        paTocMarkCurrent();
        paTocJumpTo(label);
    });
    // Capture, because the element that scrolls is nested and scroll does not
    // bubble.
    document.addEventListener('scroll', onScroll, {passive: true, capture: true});
    window.addEventListener('resize', onScroll, {passive: true});
}());

/**
 * Adds or removes the class that reserves the contents sidebar's column, and
 * makes ExtJS re-measure when it changes.
 *
 * ExtJS sizes its panels from the centre panel's content box once, at render,
 * and does not observe padding changes afterwards. The contents list is built
 * on a delay - it has to wait for the sections to exist - so the padding lands
 * after the layout has already run, and everything keeps the width it measured
 * when the sidebar had no column. The enrichment grid stayed 1318px wide inside
 * a 1112px card that way, pushing "External links" off the edge.
 *
 * @param {Element} root, the centre panel
 * @param {Boolean} wanted, whether a sidebar is being shown
 */
/**
 * Runs `fn` after the current task: on the next frame while the tab is visible,
 * on a timer while it is not.
 *
 * Chrome throttles requestAnimationFrame to nothing in a background tab, and
 * what is deferred through here decides layout - the contents rail, and the
 * re-measure that has to follow it. Scheduled on rAF alone those simply never
 * run while the tab is in the background: no error, no retry, and the page
 * settles at a width nobody chose. It is not a corner case either. A job takes
 * minutes, and reading something else while it finishes is the ordinary way to
 * use this application, so the tab being hidden at the moment a view swaps is
 * the common case rather than the exotic one. Measured: on a hidden tab the
 * contents rail was never built on a view swap, so Step 2 came up 138px wider
 * than the same page reached with the tab in front.
 *
 * Same failure, and the same fix, as the overlay in AlignmentGuides.js.
 *
 * @param {Function} fn, the work to defer
 */
function paDeferFrame(fn) {
    if (document.visibilityState === 'hidden') {
        setTimeout(fn, 0);
    } else {
        requestAnimationFrame(fn);
    }
}

function paTocSyncRail(root, wanted) {
    if (root.classList.contains('pa-has-toc') === wanted) {
        return;
    }

    root.classList.toggle('pa-has-toc', wanted);

    // Deferred so the new padding is in effect before Ext re-measures.
    var remeasure = function () {
        var viewport = window.Ext ? Ext.ComponentQuery.query('viewport')[0] : null;

        if (!viewport) {
            return;
        }

        /* A card layout does not re-derive its child's width on a re-layout: it
           writes the width it measured as an inline style on the child, and
           `updateLayout()` then honours that written width as if it had been
           configured. So the padding change below reaches every card on the page
           except the ones inside a tab panel, which keep the width the column
           had before the sidebar took its 158px.

           Measured on Step 3 of a KEGG+Reactome job at 1440: `tabcontainer_network`
           correctly 1114 wide, its active child still 1184 - so "Pathways
           classification" and the Details panel ran to x=1450 while every card
           outside the tabs stopped at 1380, and on a job with metabolites the
           overhang escaped as a horizontal scrollbar on the whole page.

           Clearing the width first is what makes the re-layout re-measure. Only
           tab-panel children in this column are touched, and a tab panel's child
           is sized by its container by definition, so there is no configured
           width here to destroy. */
        var staleTabs = Ext.ComponentQuery.query('tabpanel').filter(function (tabs) {
            return tabs.el && tabs.el.dom && root.contains(tabs.el.dom);
        });
        staleTabs.forEach(function (tabs) {
            tabs.items.each(function (child) {
                if (child.getWidth && child.getWidth() > 0) {
                    child.setWidth(null);
                }
            });
        });

        viewport.updateLayout();

        /* And each tab panel by name afterwards. Laying out the viewport does
           not reach the card layout inside these - the run stops at the tab
           panel, whose own width is already right, and never asks the card
           layout to re-place a child whose width was just cleared. Verified
           both ways from the console: clearing plus `viewport.updateLayout()`
           leaves the child at 1184, clearing plus the tab panel's own
           `updateLayout()` takes it to 1114. */
        staleTabs.forEach(function (tabs) {
            tabs.updateLayout();
        });
    };

    paDeferFrame(remeasure);
}

function buildAnalysisTOC(containerSelector) {
    var root = document.querySelector(containerSelector || '#mainViewCenterPanel');
    if (!root) { return null; }

    var existing = root.querySelector('.pa-toc');
    if (existing) { existing.remove(); }

    var headings = paTocSections();
    if (headings.length < 3) {
        // No sidebar means no column reserved for one - the class is what the
        // stylesheet keys the extra left padding off, so views without a
        // contents list (the pathway view) get the full width back.
        paTocSyncRail(root, false);
        return null;
    }
    paTocSyncRail(root, true);

    var nav = document.createElement('nav');
    nav.className = 'pa-toc';
    nav.setAttribute('aria-label', 'Analyses on this page');

    var list = document.createElement('ul');
    list.className = 'pa-toc-list';

    headings.forEach(function (h) {
        var label = paTocNorm(h);
        var li = document.createElement('li');
        var a = document.createElement('a');
        a.className = 'pa-toc-link';
        a.href = 'javascript:void(0)';
        a.textContent = label;
        a.setAttribute('data-toc-text', label);
        li.appendChild(a);
        list.appendChild(li);
    });

    nav.appendChild(list);
    root.insertBefore(nav, root.firstChild);
    paTocMarkCurrent();
    return nav;
}

/**
 * Relative luminance of a colour, per the WCAG 2.1 definition (sRGB with the
 * gamma expansion applied channel by channel).
 *
 * Accepts "#rgb", "#rrggbb", "rrggbb" or "rgb(r, g, b)". Returns null - not a
 * guessed value - for anything it cannot parse, so callers can fall back to
 * their previous styling rather than render an unreadable colour pair.
 */
function relativeLuminance(color) {
    var text = String(color || "").trim(),
        rgb = null,
        match;

    match = text.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
    if (match) {
        rgb = [parseInt(match[1], 10), parseInt(match[2], 10), parseInt(match[3], 10)];
    } else {
        var hex = text.replace("#", "");
        if (hex.length === 3) {
            hex = hex.charAt(0) + hex.charAt(0) + hex.charAt(1) + hex.charAt(1) + hex.charAt(2) + hex.charAt(2);
        }
        if (/^[0-9a-fA-F]{6}$/.test(hex)) {
            rgb = [parseInt(hex.substr(0, 2), 16), parseInt(hex.substr(2, 2), 16), parseInt(hex.substr(4, 2), 16)];
        }
    }
    if (rgb === null || rgb.some(isNaN)) {
        return null;
    }

    var linear = rgb.map(function (channel) {
        var c = channel / 255;
        return (c <= 0.03928) ? (c / 12.92) : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
}

/**
 * Black or white, whichever contrasts better against the supplied fill.
 *
 * The two candidates cross over at a relative luminance of
 * sqrt(1.05 * 0.05) - 0.05 = 0.1791, which is where (L+0.05)/0.05 - the ratio
 * against black - overtakes 1.05/(L+0.05), the ratio against white. Picking the
 * better of the two is what guarantees a conformant pair for an arbitrary fill
 * instead of hoping one fixed ink suits the whole palette.
 *
 * Returns null when the fill cannot be parsed.
 */
function contrastingInk(fillColor) {
    var luminance = relativeLuminance(fillColor);
    if (luminance === null) {
        return null;
    }
    return (luminance > 0.1791) ? "#000000" : "#FFFFFF";
}

/**
 * Inline style for a single-letter classification / database badge
 * (i.classificationNameBox).
 *
 * The badge used to be an outlined letter drawn in the classification's own
 * palette colour. That palette is pastel by design and, as text on white, most
 * of it sat between 1.07:1 and 3.7:1 - pure yellow was effectively invisible.
 *
 * Darkening the palette is not an option: getClassificationColor() also feeds
 * the classification pie chart and the pathway grid stripes, so a badge tuned
 * for legibility would no longer match the chart slice it indexes.
 *
 * So the colour moves from the ink to the fill. The palette value becomes the
 * chip background - a large block of colour, which is where it reads best
 * anyway - and the letter is drawn in whichever of black or white contrasts
 * better with it. Every one of the 36 palette colours clears AA that way, the
 * worst being #c1502e at 4.71:1, and the mapping keeps working for colours
 * added later.
 */
/**
 * Grid cell renderer for narrow text columns.
 *
 * The Hub Analysis and metabolite class tables give their name column around
 * 100px, because nine other columns are competing for the same two thirds of
 * the page. Chemical names do not fit that at any type size - "gamma-Aminobutyric
 * acid" and "L-2-Aminoadipic acid" both ellipsise - and widening the column only
 * moves the problem to whichever column pays for it.
 *
 * So the value is left truncated on screen and the full text is attached as a
 * quick-tip, which makes it recoverable on hover instead of lost. The value is
 * HTML-encoded on the way through: ExtJS writes cell values as raw markup when
 * no renderer is set, and these names come from user-supplied input files.
 */
function truncatableTextRenderer(value, metadata) {
    if (value === null || value === undefined || value === "") {
        return value;
    }

    var encoded = Ext.String.htmlEncode(String(value));

    // htmlEncode escapes the double quotes, so the attribute cannot be broken
    // out of by a name containing one.
    metadata.tdAttr = 'data-qtip="' + encoded + '"';

    return encoded;
}

function classificationBadgeStyle(color) {
    var ink = contrastingInk(color);
    if (ink === null) {
        // Unparseable colour: keep the historical outlined rendering rather
        // than paint an arbitrary letter onto an unknown fill.
        return "border-color:" + color + "; color:" + color + ";";
    }
    return "background-color:" + color + "; border-color:" + color + "; color:" + ink + ";";
}


/*********************************************************************************
 * COMMON FUNCTION DECLARATION
 **********************************************************************************/
/**
 *
 * @param {type} callback
 * @param {type} seconds
 * @returns {Number}
 */
$.wait = function (callback, seconds) {
    return window.setTimeout(callback, seconds * 1000);
};

Date.logFormat = function () {
    var date = new Date();
    return date.toUTCString() + " > ";
};
Object.values = function (o) {
    return Object.keys(o).map(function (k) {
        return o[k];
    });
};

Array.min = function (array) {
    return Math.min.apply(Math, array);
};

Array.intersect = function (a, b) {
    var t;
    if (b.length > a.length) t = b, b = a, a = t; // indexOf to loop over shorter
    return a.filter(function (e) {
        if (b.indexOf(e) !== -1) return true;
    });
};


Array.unique = function (array) {
    var a = array.concat();
    for (var i = 0; i < a.length; ++i) {
        for (var j = i + 1; j < a.length; ++j) {
            if (a[i] === a[j])
                a.splice(j--, 1);
        }
    }
    return a;
};

Array.binaryInsert = function (value, array, startVal, endVal) {
    var length = array.length;
    var start = typeof (startVal) != 'undefined' ? startVal : 0;
    var end = typeof (endVal) != 'undefined' ? endVal : length - 1;//!! endVal could be 0 don't use || syntax
    var m = start + Math.floor((end - start) / 2);

    if (length === 0) {
        array.push(value);
        return 0;
    }

    if (value > array[end]) {
        array.splice(end + 1, 0, value);
        return end + 1;
    }

    if (value < array[start]) {//!!
        array.splice(start, 0, value);
        return start;
    }

    if (start >= end) {
        return;
    }

    if (value < array[m]) {
        return Array.binaryInsert(value, array, start, m - 1);
    }

    if (value > array[m]) {
        return Array.binaryInsert(value, array, m + 1, end);
    }
};

String.prototype.trunc = String.prototype.trunc ||
    function (n) {
        return (this.length > n) ? this.substr(0, n - 1) + '&hellip;' : this;
    };


(function (f) {
    if (typeof exports === "object" && typeof module !== "undefined") {
        module.exports = f()
    } else if (typeof define === "function" && define.amd) {
        define([], f)
    } else {
        var g;
        if (typeof window !== "undefined") {
            g = window
        } else if (typeof global !== "undefined") {
            g = global
        } else if (typeof self !== "undefined") {
            g = self
        } else {
            g = this
        }
        g.mlPCA = f()
    }
})(function () {
    var define, module, exports;
    return (function () {
        function r(e, n, t) {
            function o(i, f) {
                if (!n[i]) {
                    if (!e[i]) {
                        var c = "function" == typeof require && require;
                        if (!f && c) return c(i, !0);
                        if (u) return u(i, !0);
                        var a = new Error("Cannot find module '" + i + "'");
                        throw a.code = "MODULE_NOT_FOUND", a
                    }
                    var p = n[i] = {exports: {}};
                    e[i][0].call(p.exports, function (r) {
                        var n = e[i][1][r];
                        return o(n || r)
                    }, p, p.exports, r, e, n, t)
                }
                return n[i].exports
            }

            for (var u = "function" == typeof require && require, i = 0; i < t.length; i++) o(t[i]);
            return o
        }

        return r
    })()({
        1: [function (require, module, exports) {
            const PCA = require('ml-pca');

            module.exports = {
                generateMetagenes: function (matrixData, varCutoff = 0.3) {
                    // Return an empty list if no data is provided
                    if (!matrixData.length) {
                        return ([]);
                    }

                    function multiply(a, b) {
                        var aNumRows = a.length, aNumCols = a[0].length,
                            bNumRows = b.length, bNumCols = b[0].length,
                            m = new Array(aNumRows);  // initialize array of rows
                        for (var r = 0; r < aNumRows; ++r) {
                            m[r] = new Array(bNumCols); // initialize the current row
                            for (var c = 0; c < bNumCols; ++c) {
                                m[r][c] = 0;             // initialize the current cell
                                for (var i = 0; i < aNumCols; ++i) {
                                    m[r][c] += a[r][i] * b[i][c];
                                }
                            }
                        }
                        return m;
                    }

                    function transpose(matrix) {
                        return matrix[0].map((col, i) => matrix.map(row => row[i]));
                    }
                    // if the matrixdata is one dimensional, we use mean value as metagene
                    if (matrixData[0].length === 1) {
                        pcaGenes = transpose(matrixData)[0];
                        // filter the outliers inside pcaGenes
                        var mean = pcaGenes.reduce((a, b) => a + b) / pcaGenes.length;
                        var std = Math.sqrt(pcaGenes.map(x => Math.pow(x - mean, 2)).reduce((a, b) => a + b) / pcaGenes.length);
                        var filteredGenes = pcaGenes.filter(x => Math.abs(x - mean) < 3 * std);
                        // make sure the filteredGenes have at least 2 elements
                        if (filteredGenes.length < 2) {
                            return [filteredGenes];
                        }
                        // calculate the mean value of filteredGenes
                        var meanValue = filteredGenes.reduce((a, b) => a + b) / filteredGenes.length;
                        return [[meanValue]];
                    }
                    pcaGenes = new PCA(transpose(matrixData));
                    varExp = pcaGenes.getExplainedVariance();
                    //Metagenes is the score
                    //Score = centered_dataset * t(loandings)
                    metagenes_raw = transpose(multiply(pcaGenes.adjustDataset, pcaGenes.getLoadings().transpose()));
                    metagenes = [];

                    // Get the loadings for gene direction revert
                    pcaLoadings = pcaGenes.getLoadings();

                    /* Assuming fac.sel = "single%" */
                    fac = varExp.filter(function (x) {
                        return x >= (varCutoff / Math.sqrt(1))
                    }).length;

                    /* Avoid returning metagenes when there are none */
                    if (fac) {
                        for (index in Array.from(Array(fac).keys())) {
                            metagenes.push(metagenes_raw[index]);
                        }

                        //check directions
                        let has_positives = 0
                        let has_positives_sum = 0
                        let has_negatives = 0
                        let has_negatives_sum = 0

                        for (let i = 0; i < fac; i++) {
                            loadings_sum = pcaLoadings[i].reduce((a, b) => Math.abs(a) + Math.abs(b), 0)
                            for (let j = 0; j < pcaLoadings[i].length; j++) {
                                if (Math.abs(pcaLoadings[i][j]) / loadings_sum > 1 / pcaLoadings[i].length) {
                                    if (pcaLoadings[i][j] > 0) {
                                        has_positives = has_positives + 1
                                        has_positives_sum = has_positives_sum + pcaLoadings[i][j]
                                    } else {
                                        has_negatives = has_negatives + 1
                                        has_negatives_sum = has_negatives_sum + pcaLoadings[i][j]
                                    }
                                }
                            }
                            if (has_negatives > has_positives) {
                                metagenes[i] = metagenes[i].map(x => x * -1)
                            } else if  (has_negatives == has_positives) {
                                if (Math.abs(has_negatives_sum) > Math.abs(has_positives_sum)) {
                                    metagenes[i] = metagenes[i].map(x => x * -1)
                                }
                            }
                        }

                    }
                    return (metagenes);
                }
            };
        }, {"ml-pca": 6}], 2: [function (require, module, exports) {
            'use strict';

            /**
             * Computes the maximum of the given values
             * @param {Array<number>} input
             * @return {number}
             */
            function max(input) {
                if (!Array.isArray(input)) {
                    throw new Error('input must be an array');
                }

                if (input.length === 0) {
                    throw new Error('input must not be empty');
                }

                var max = input[0];
                for (var i = 1; i < input.length; i++) {
                    if (input[i] > max) max = input[i];
                }
                return max;
            }

            module.exports = max;

        }, {}], 3: [function (require, module, exports) {
            'use strict';

            /**
             * Computes the minimum of the given values
             * @param {Array<number>} input
             * @return {number}
             */
            function min(input) {
                if (!Array.isArray(input)) {
                    throw new Error('input must be an array');
                }

                if (input.length === 0) {
                    throw new Error('input must not be empty');
                }

                var min = input[0];
                for (var i = 1; i < input.length; i++) {
                    if (input[i] < min) min = input[i];
                }
                return min;
            }

            module.exports = min;

        }, {}], 4: [function (require, module, exports) {
            'use strict';

            function _interopDefault(ex) {
                return (ex && (typeof ex === 'object') && 'default' in ex) ? ex['default'] : ex;
            }

            var max = _interopDefault(require('ml-array-max'));
            var min = _interopDefault(require('ml-array-min'));

            function rescale(input, options = {}) {
                if (!Array.isArray(input)) {
                    throw new TypeError('input must be an array');
                } else if (input.length === 0) {
                    throw new TypeError('input must not be empty');
                }

                let output;
                if (options.output !== undefined) {
                    if (!Array.isArray(options.output)) {
                        throw new TypeError('output option must be an array if specified');
                    }
                    output = options.output;
                } else {
                    output = new Array(input.length);
                }

                const currentMin = min(input);
                const currentMax = max(input);

                if (currentMin === currentMax) {
                    throw new RangeError('minimum and maximum input values are equal. Cannot rescale a constant array');
                }

                const {
                    min: minValue = options.autoMinMax ? currentMin : 0,
                    max: maxValue = options.autoMinMax ? currentMax : 1
                } = options;

                if (minValue >= maxValue) {
                    throw new RangeError('min option must be smaller than max option');
                }

                const factor = (maxValue - minValue) / (currentMax - currentMin);
                for (var i = 0; i < input.length; i++) {
                    output[i] = (input[i] - currentMin) * factor + minValue;
                }

                return output;
            }

            module.exports = rescale;

        }, {"ml-array-max": 2, "ml-array-min": 3}], 5: [function (require, module, exports) {
            'use strict';

            Object.defineProperty(exports, '__esModule', {value: true});

            function _interopDefault(ex) {
                return (ex && (typeof ex === 'object') && 'default' in ex) ? ex['default'] : ex;
            }

            var rescale = _interopDefault(require('ml-array-rescale'));
            var max = _interopDefault(require('ml-array-max'));

            /**
             * @class LuDecomposition
             * @link https://github.com/lutzroeder/Mapack/blob/master/Source/LuDecomposition.cs
             * @param {Matrix} matrix
             */
            class LuDecomposition$$1 {
                constructor(matrix) {
                    matrix = WrapperMatrix2D.checkMatrix(matrix);

                    var lu = matrix.clone();
                    var rows = lu.rows;
                    var columns = lu.columns;
                    var pivotVector = new Array(rows);
                    var pivotSign = 1;
                    var i, j, k, p, s, t, v;
                    var LUcolj, kmax;

                    for (i = 0; i < rows; i++) {
                        pivotVector[i] = i;
                    }

                    LUcolj = new Array(rows);

                    for (j = 0; j < columns; j++) {
                        for (i = 0; i < rows; i++) {
                            LUcolj[i] = lu.get(i, j);
                        }

                        for (i = 0; i < rows; i++) {
                            kmax = Math.min(i, j);
                            s = 0;
                            for (k = 0; k < kmax; k++) {
                                s += lu.get(i, k) * LUcolj[k];
                            }
                            LUcolj[i] -= s;
                            lu.set(i, j, LUcolj[i]);
                        }

                        p = j;
                        for (i = j + 1; i < rows; i++) {
                            if (Math.abs(LUcolj[i]) > Math.abs(LUcolj[p])) {
                                p = i;
                            }
                        }

                        if (p !== j) {
                            for (k = 0; k < columns; k++) {
                                t = lu.get(p, k);
                                lu.set(p, k, lu.get(j, k));
                                lu.set(j, k, t);
                            }

                            v = pivotVector[p];
                            pivotVector[p] = pivotVector[j];
                            pivotVector[j] = v;

                            pivotSign = -pivotSign;
                        }

                        if (j < rows && lu.get(j, j) !== 0) {
                            for (i = j + 1; i < rows; i++) {
                                lu.set(i, j, lu.get(i, j) / lu.get(j, j));
                            }
                        }
                    }

                    this.LU = lu;
                    this.pivotVector = pivotVector;
                    this.pivotSign = pivotSign;
                }

                /**
                 *
                 * @return {boolean}
                 */
                isSingular() {
                    var data = this.LU;
                    var col = data.columns;
                    for (var j = 0; j < col; j++) {
                        if (data[j][j] === 0) {
                            return true;
                        }
                    }
                    return false;
                }

                /**
                 *
                 * @param {Matrix} value
                 * @return {Matrix}
                 */
                solve(value) {
                    value = Matrix.checkMatrix(value);

                    var lu = this.LU;
                    var rows = lu.rows;

                    if (rows !== value.rows) {
                        throw new Error('Invalid matrix dimensions');
                    }
                    if (this.isSingular()) {
                        throw new Error('LU matrix is singular');
                    }

                    var count = value.columns;
                    var X = value.subMatrixRow(this.pivotVector, 0, count - 1);
                    var columns = lu.columns;
                    var i, j, k;

                    for (k = 0; k < columns; k++) {
                        for (i = k + 1; i < columns; i++) {
                            for (j = 0; j < count; j++) {
                                X[i][j] -= X[k][j] * lu[i][k];
                            }
                        }
                    }
                    for (k = columns - 1; k >= 0; k--) {
                        for (j = 0; j < count; j++) {
                            X[k][j] /= lu[k][k];
                        }
                        for (i = 0; i < k; i++) {
                            for (j = 0; j < count; j++) {
                                X[i][j] -= X[k][j] * lu[i][k];
                            }
                        }
                    }
                    return X;
                }

                /**
                 *
                 * @return {number}
                 */
                get determinant() {
                    var data = this.LU;
                    if (!data.isSquare()) {
                        throw new Error('Matrix must be square');
                    }
                    var determinant = this.pivotSign;
                    var col = data.columns;
                    for (var j = 0; j < col; j++) {
                        determinant *= data[j][j];
                    }
                    return determinant;
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get lowerTriangularMatrix() {
                    var data = this.LU;
                    var rows = data.rows;
                    var columns = data.columns;
                    var X = new Matrix(rows, columns);
                    for (var i = 0; i < rows; i++) {
                        for (var j = 0; j < columns; j++) {
                            if (i > j) {
                                X[i][j] = data[i][j];
                            } else if (i === j) {
                                X[i][j] = 1;
                            } else {
                                X[i][j] = 0;
                            }
                        }
                    }
                    return X;
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get upperTriangularMatrix() {
                    var data = this.LU;
                    var rows = data.rows;
                    var columns = data.columns;
                    var X = new Matrix(rows, columns);
                    for (var i = 0; i < rows; i++) {
                        for (var j = 0; j < columns; j++) {
                            if (i <= j) {
                                X[i][j] = data[i][j];
                            } else {
                                X[i][j] = 0;
                            }
                        }
                    }
                    return X;
                }

                /**
                 *
                 * @return {Array<number>}
                 */
                get pivotPermutationVector() {
                    return this.pivotVector.slice();
                }
            }

            function hypotenuse(a, b) {
                var r = 0;
                if (Math.abs(a) > Math.abs(b)) {
                    r = b / a;
                    return Math.abs(a) * Math.sqrt(1 + r * r);
                }
                if (b !== 0) {
                    r = a / b;
                    return Math.abs(b) * Math.sqrt(1 + r * r);
                }
                return 0;
            }

            function getFilled2DArray(rows, columns, value) {
                var array = new Array(rows);
                for (var i = 0; i < rows; i++) {
                    array[i] = new Array(columns);
                    for (var j = 0; j < columns; j++) {
                        array[i][j] = value;
                    }
                }
                return array;
            }

            /**
             * @class SingularValueDecomposition
             * @see https://github.com/accord-net/framework/blob/development/Sources/Accord.Math/Decompositions/SingularValueDecomposition.cs
             * @param {Matrix} value
             * @param {object} [options]
             * @param {boolean} [options.computeLeftSingularVectors=true]
             * @param {boolean} [options.computeRightSingularVectors=true]
             * @param {boolean} [options.autoTranspose=false]
             */
            class SingularValueDecomposition$$1 {
                constructor(value, options = {}) {
                    value = WrapperMatrix2D.checkMatrix(value);

                    var m = value.rows;
                    var n = value.columns;

                    const {
                        computeLeftSingularVectors = true,
                        computeRightSingularVectors = true,
                        autoTranspose = false
                    } = options;

                    var wantu = Boolean(computeLeftSingularVectors);
                    var wantv = Boolean(computeRightSingularVectors);

                    var swapped = false;
                    var a;
                    if (m < n) {
                        if (!autoTranspose) {
                            a = value.clone();
                            // eslint-disable-next-line no-console
                            console.warn(
                                'Computing SVD on a matrix with more columns than rows. Consider enabling autoTranspose'
                            );
                        } else {
                            a = value.transpose();
                            m = a.rows;
                            n = a.columns;
                            swapped = true;
                            var aux = wantu;
                            wantu = wantv;
                            wantv = aux;
                        }
                    } else {
                        a = value.clone();
                    }

                    var nu = Math.min(m, n);
                    var ni = Math.min(m + 1, n);
                    var s = new Array(ni);
                    var U = getFilled2DArray(m, nu, 0);
                    var V = getFilled2DArray(n, n, 0);

                    var e = new Array(n);
                    var work = new Array(m);

                    var si = new Array(ni);
                    for (let i = 0; i < ni; i++) si[i] = i;

                    var nct = Math.min(m - 1, n);
                    var nrt = Math.max(0, Math.min(n - 2, m));
                    var mrc = Math.max(nct, nrt);

                    for (let k = 0; k < mrc; k++) {
                        if (k < nct) {
                            s[k] = 0;
                            for (let i = k; i < m; i++) {
                                s[k] = hypotenuse(s[k], a[i][k]);
                            }
                            if (s[k] !== 0) {
                                if (a[k][k] < 0) {
                                    s[k] = -s[k];
                                }
                                for (let i = k; i < m; i++) {
                                    a[i][k] /= s[k];
                                }
                                a[k][k] += 1;
                            }
                            s[k] = -s[k];
                        }

                        for (let j = k + 1; j < n; j++) {
                            if (k < nct && s[k] !== 0) {
                                let t = 0;
                                for (let i = k; i < m; i++) {
                                    t += a[i][k] * a[i][j];
                                }
                                t = -t / a[k][k];
                                for (let i = k; i < m; i++) {
                                    a[i][j] += t * a[i][k];
                                }
                            }
                            e[j] = a[k][j];
                        }

                        if (wantu && k < nct) {
                            for (let i = k; i < m; i++) {
                                U[i][k] = a[i][k];
                            }
                        }

                        if (k < nrt) {
                            e[k] = 0;
                            for (let i = k + 1; i < n; i++) {
                                e[k] = hypotenuse(e[k], e[i]);
                            }
                            if (e[k] !== 0) {
                                if (e[k + 1] < 0) {
                                    e[k] = 0 - e[k];
                                }
                                for (let i = k + 1; i < n; i++) {
                                    e[i] /= e[k];
                                }
                                e[k + 1] += 1;
                            }
                            e[k] = -e[k];
                            if (k + 1 < m && e[k] !== 0) {
                                for (let i = k + 1; i < m; i++) {
                                    work[i] = 0;
                                }
                                for (let i = k + 1; i < m; i++) {
                                    for (let j = k + 1; j < n; j++) {
                                        work[i] += e[j] * a[i][j];
                                    }
                                }
                                for (let j = k + 1; j < n; j++) {
                                    let t = -e[j] / e[k + 1];
                                    for (let i = k + 1; i < m; i++) {
                                        a[i][j] += t * work[i];
                                    }
                                }
                            }
                            if (wantv) {
                                for (let i = k + 1; i < n; i++) {
                                    V[i][k] = e[i];
                                }
                            }
                        }
                    }

                    let p = Math.min(n, m + 1);
                    if (nct < n) {
                        s[nct] = a[nct][nct];
                    }
                    if (m < p) {
                        s[p - 1] = 0;
                    }
                    if (nrt + 1 < p) {
                        e[nrt] = a[nrt][p - 1];
                    }
                    e[p - 1] = 0;

                    if (wantu) {
                        for (let j = nct; j < nu; j++) {
                            for (let i = 0; i < m; i++) {
                                U[i][j] = 0;
                            }
                            U[j][j] = 1;
                        }
                        for (let k = nct - 1; k >= 0; k--) {
                            if (s[k] !== 0) {
                                for (let j = k + 1; j < nu; j++) {
                                    let t = 0;
                                    for (let i = k; i < m; i++) {
                                        t += U[i][k] * U[i][j];
                                    }
                                    t = -t / U[k][k];
                                    for (let i = k; i < m; i++) {
                                        U[i][j] += t * U[i][k];
                                    }
                                }
                                for (let i = k; i < m; i++) {
                                    U[i][k] = -U[i][k];
                                }
                                U[k][k] = 1 + U[k][k];
                                for (let i = 0; i < k - 1; i++) {
                                    U[i][k] = 0;
                                }
                            } else {
                                for (let i = 0; i < m; i++) {
                                    U[i][k] = 0;
                                }
                                U[k][k] = 1;
                            }
                        }
                    }

                    if (wantv) {
                        for (let k = n - 1; k >= 0; k--) {
                            if (k < nrt && e[k] !== 0) {
                                for (let j = k + 1; j < n; j++) {
                                    let t = 0;
                                    for (let i = k + 1; i < n; i++) {
                                        t += V[i][k] * V[i][j];
                                    }
                                    t = -t / V[k + 1][k];
                                    for (let i = k + 1; i < n; i++) {
                                        V[i][j] += t * V[i][k];
                                    }
                                }
                            }
                            for (let i = 0; i < n; i++) {
                                V[i][k] = 0;
                            }
                            V[k][k] = 1;
                        }
                    }

                    var pp = p - 1;
                    var eps = Number.EPSILON;
                    while (p > 0) {
                        let k, kase;
                        for (k = p - 2; k >= -1; k--) {
                            if (k === -1) {
                                break;
                            }
                            const alpha =
                                Number.MIN_VALUE + eps * Math.abs(s[k] + Math.abs(s[k + 1]));
                            if (Math.abs(e[k]) <= alpha || Number.isNaN(e[k])) {
                                e[k] = 0;
                                break;
                            }
                        }
                        if (k === p - 2) {
                            kase = 4;
                        } else {
                            let ks;
                            for (ks = p - 1; ks >= k; ks--) {
                                if (ks === k) {
                                    break;
                                }
                                let t =
                                    (ks !== p ? Math.abs(e[ks]) : 0) +
                                    (ks !== k + 1 ? Math.abs(e[ks - 1]) : 0);
                                if (Math.abs(s[ks]) <= eps * t) {
                                    s[ks] = 0;
                                    break;
                                }
                            }
                            if (ks === k) {
                                kase = 3;
                            } else if (ks === p - 1) {
                                kase = 1;
                            } else {
                                kase = 2;
                                k = ks;
                            }
                        }

                        k++;

                        switch (kase) {
                            case 1: {
                                let f = e[p - 2];
                                e[p - 2] = 0;
                                for (let j = p - 2; j >= k; j--) {
                                    let t = hypotenuse(s[j], f);
                                    let cs = s[j] / t;
                                    let sn = f / t;
                                    s[j] = t;
                                    if (j !== k) {
                                        f = -sn * e[j - 1];
                                        e[j - 1] = cs * e[j - 1];
                                    }
                                    if (wantv) {
                                        for (let i = 0; i < n; i++) {
                                            t = cs * V[i][j] + sn * V[i][p - 1];
                                            V[i][p - 1] = -sn * V[i][j] + cs * V[i][p - 1];
                                            V[i][j] = t;
                                        }
                                    }
                                }
                                break;
                            }
                            case 2: {
                                let f = e[k - 1];
                                e[k - 1] = 0;
                                for (let j = k; j < p; j++) {
                                    let t = hypotenuse(s[j], f);
                                    let cs = s[j] / t;
                                    let sn = f / t;
                                    s[j] = t;
                                    f = -sn * e[j];
                                    e[j] = cs * e[j];
                                    if (wantu) {
                                        for (let i = 0; i < m; i++) {
                                            t = cs * U[i][j] + sn * U[i][k - 1];
                                            U[i][k - 1] = -sn * U[i][j] + cs * U[i][k - 1];
                                            U[i][j] = t;
                                        }
                                    }
                                }
                                break;
                            }
                            case 3: {
                                const scale = Math.max(
                                    Math.abs(s[p - 1]),
                                    Math.abs(s[p - 2]),
                                    Math.abs(e[p - 2]),
                                    Math.abs(s[k]),
                                    Math.abs(e[k])
                                );
                                const sp = s[p - 1] / scale;
                                const spm1 = s[p - 2] / scale;
                                const epm1 = e[p - 2] / scale;
                                const sk = s[k] / scale;
                                const ek = e[k] / scale;
                                const b = ((spm1 + sp) * (spm1 - sp) + epm1 * epm1) / 2;
                                const c = sp * epm1 * (sp * epm1);
                                let shift = 0;
                                if (b !== 0 || c !== 0) {
                                    if (b < 0) {
                                        shift = 0 - Math.sqrt(b * b + c);
                                    } else {
                                        shift = Math.sqrt(b * b + c);
                                    }
                                    shift = c / (b + shift);
                                }
                                let f = (sk + sp) * (sk - sp) + shift;
                                let g = sk * ek;
                                for (let j = k; j < p - 1; j++) {
                                    let t = hypotenuse(f, g);
                                    if (t === 0) t = Number.MIN_VALUE;
                                    let cs = f / t;
                                    let sn = g / t;
                                    if (j !== k) {
                                        e[j - 1] = t;
                                    }
                                    f = cs * s[j] + sn * e[j];
                                    e[j] = cs * e[j] - sn * s[j];
                                    g = sn * s[j + 1];
                                    s[j + 1] = cs * s[j + 1];
                                    if (wantv) {
                                        for (let i = 0; i < n; i++) {
                                            t = cs * V[i][j] + sn * V[i][j + 1];
                                            V[i][j + 1] = -sn * V[i][j] + cs * V[i][j + 1];
                                            V[i][j] = t;
                                        }
                                    }
                                    t = hypotenuse(f, g);
                                    if (t === 0) t = Number.MIN_VALUE;
                                    cs = f / t;
                                    sn = g / t;
                                    s[j] = t;
                                    f = cs * e[j] + sn * s[j + 1];
                                    s[j + 1] = -sn * e[j] + cs * s[j + 1];
                                    g = sn * e[j + 1];
                                    e[j + 1] = cs * e[j + 1];
                                    if (wantu && j < m - 1) {
                                        for (let i = 0; i < m; i++) {
                                            t = cs * U[i][j] + sn * U[i][j + 1];
                                            U[i][j + 1] = -sn * U[i][j] + cs * U[i][j + 1];
                                            U[i][j] = t;
                                        }
                                    }
                                }
                                e[p - 2] = f;
                                break;
                            }
                            case 4: {
                                if (s[k] <= 0) {
                                    s[k] = s[k] < 0 ? -s[k] : 0;
                                    if (wantv) {
                                        for (let i = 0; i <= pp; i++) {
                                            V[i][k] = -V[i][k];
                                        }
                                    }
                                }
                                while (k < pp) {
                                    if (s[k] >= s[k + 1]) {
                                        break;
                                    }
                                    let t = s[k];
                                    s[k] = s[k + 1];
                                    s[k + 1] = t;
                                    if (wantv && k < n - 1) {
                                        for (let i = 0; i < n; i++) {
                                            t = V[i][k + 1];
                                            V[i][k + 1] = V[i][k];
                                            V[i][k] = t;
                                        }
                                    }
                                    if (wantu && k < m - 1) {
                                        for (let i = 0; i < m; i++) {
                                            t = U[i][k + 1];
                                            U[i][k + 1] = U[i][k];
                                            U[i][k] = t;
                                        }
                                    }
                                    k++;
                                }
                                p--;
                                break;
                            }
                            // no default
                        }
                    }

                    if (swapped) {
                        var tmp = V;
                        V = U;
                        U = tmp;
                    }

                    this.m = m;
                    this.n = n;
                    this.s = s;
                    this.U = U;
                    this.V = V;
                }

                /**
                 * Solve a problem of least square (Ax=b) by using the SVD. Useful when A is singular. When A is not singular, it would be better to use qr.solve(value).
                 * Example : We search to approximate x, with A matrix shape m*n, x vector size n, b vector size m (m > n). We will use :
                 * var svd = SingularValueDecomposition(A);
                 * var x = svd.solve(b);
                 * @param {Matrix} value - Matrix 1D which is the vector b (in the equation Ax = b)
                 * @return {Matrix} - The vector x
                 */
                solve(value) {
                    var Y = value;
                    var e = this.threshold;
                    var scols = this.s.length;
                    var Ls = Matrix.zeros(scols, scols);

                    for (let i = 0; i < scols; i++) {
                        if (Math.abs(this.s[i]) <= e) {
                            Ls[i][i] = 0;
                        } else {
                            Ls[i][i] = 1 / this.s[i];
                        }
                    }

                    var U = this.U;
                    var V = this.rightSingularVectors;

                    var VL = V.mmul(Ls);
                    var vrows = V.rows;
                    var urows = U.length;
                    var VLU = Matrix.zeros(vrows, urows);

                    for (let i = 0; i < vrows; i++) {
                        for (let j = 0; j < urows; j++) {
                            let sum = 0;
                            for (let k = 0; k < scols; k++) {
                                sum += VL[i][k] * U[j][k];
                            }
                            VLU[i][j] = sum;
                        }
                    }

                    return VLU.mmul(Y);
                }

                /**
                 *
                 * @param {Array<number>} value
                 * @return {Matrix}
                 */
                solveForDiagonal(value) {
                    return this.solve(Matrix.diag(value));
                }

                /**
                 * Get the inverse of the matrix. We compute the inverse of a matrix using SVD when this matrix is singular or ill-conditioned. Example :
                 * var svd = SingularValueDecomposition(A);
                 * var inverseA = svd.inverse();
                 * @return {Matrix} - The approximation of the inverse of the matrix
                 */
                inverse() {
                    var V = this.V;
                    var e = this.threshold;
                    var vrows = V.length;
                    var vcols = V[0].length;
                    var X = new Matrix(vrows, this.s.length);

                    for (let i = 0; i < vrows; i++) {
                        for (let j = 0; j < vcols; j++) {
                            if (Math.abs(this.s[j]) > e) {
                                X[i][j] = V[i][j] / this.s[j];
                            } else {
                                X[i][j] = 0;
                            }
                        }
                    }

                    var U = this.U;

                    var urows = U.length;
                    var ucols = U[0].length;
                    var Y = new Matrix(vrows, urows);

                    for (let i = 0; i < vrows; i++) {
                        for (let j = 0; j < urows; j++) {
                            let sum = 0;
                            for (let k = 0; k < ucols; k++) {
                                sum += X[i][k] * U[j][k];
                            }
                            Y[i][j] = sum;
                        }
                    }

                    return Y;
                }

                /**
                 *
                 * @return {number}
                 */
                get condition() {
                    return this.s[0] / this.s[Math.min(this.m, this.n) - 1];
                }

                /**
                 *
                 * @return {number}
                 */
                get norm2() {
                    return this.s[0];
                }

                /**
                 *
                 * @return {number}
                 */
                get rank() {
                    var tol = Math.max(this.m, this.n) * this.s[0] * Number.EPSILON;
                    var r = 0;
                    var s = this.s;
                    for (var i = 0, ii = s.length; i < ii; i++) {
                        if (s[i] > tol) {
                            r++;
                        }
                    }
                    return r;
                }

                /**
                 *
                 * @return {Array<number>}
                 */
                get diagonal() {
                    return this.s;
                }

                /**
                 *
                 * @return {number}
                 */
                get threshold() {
                    return Number.EPSILON / 2 * Math.max(this.m, this.n) * this.s[0];
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get leftSingularVectors() {
                    if (!Matrix.isMatrix(this.U)) {
                        this.U = new Matrix(this.U);
                    }
                    return this.U;
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get rightSingularVectors() {
                    if (!Matrix.isMatrix(this.V)) {
                        this.V = new Matrix(this.V);
                    }
                    return this.V;
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get diagonalMatrix() {
                    return Matrix.diag(this.s);
                }
            }

            /**
             * @private
             * Check that a row index is not out of bounds
             * @param {Matrix} matrix
             * @param {number} index
             * @param {boolean} [outer]
             */
            function checkRowIndex(matrix, index, outer) {
                var max$$1 = outer ? matrix.rows : matrix.rows - 1;
                if (index < 0 || index > max$$1) {
                    throw new RangeError('Row index out of range');
                }
            }

            /**
             * @private
             * Check that a column index is not out of bounds
             * @param {Matrix} matrix
             * @param {number} index
             * @param {boolean} [outer]
             */
            function checkColumnIndex(matrix, index, outer) {
                var max$$1 = outer ? matrix.columns : matrix.columns - 1;
                if (index < 0 || index > max$$1) {
                    throw new RangeError('Column index out of range');
                }
            }

            /**
             * @private
             * Check that the provided vector is an array with the right length
             * @param {Matrix} matrix
             * @param {Array|Matrix} vector
             * @return {Array}
             * @throws {RangeError}
             */
            function checkRowVector(matrix, vector) {
                if (vector.to1DArray) {
                    vector = vector.to1DArray();
                }
                if (vector.length !== matrix.columns) {
                    throw new RangeError(
                        'vector size must be the same as the number of columns'
                    );
                }
                return vector;
            }

            /**
             * @private
             * Check that the provided vector is an array with the right length
             * @param {Matrix} matrix
             * @param {Array|Matrix} vector
             * @return {Array}
             * @throws {RangeError}
             */
            function checkColumnVector(matrix, vector) {
                if (vector.to1DArray) {
                    vector = vector.to1DArray();
                }
                if (vector.length !== matrix.rows) {
                    throw new RangeError('vector size must be the same as the number of rows');
                }
                return vector;
            }

            function checkIndices(matrix, rowIndices, columnIndices) {
                return {
                    row: checkRowIndices(matrix, rowIndices),
                    column: checkColumnIndices(matrix, columnIndices)
                };
            }

            function checkRowIndices(matrix, rowIndices) {
                if (typeof rowIndices !== 'object') {
                    throw new TypeError('unexpected type for row indices');
                }

                var rowOut = rowIndices.some((r) => {
                    return r < 0 || r >= matrix.rows;
                });

                if (rowOut) {
                    throw new RangeError('row indices are out of range');
                }

                if (!Array.isArray(rowIndices)) rowIndices = Array.from(rowIndices);

                return rowIndices;
            }

            function checkColumnIndices(matrix, columnIndices) {
                if (typeof columnIndices !== 'object') {
                    throw new TypeError('unexpected type for column indices');
                }

                var columnOut = columnIndices.some((c) => {
                    return c < 0 || c >= matrix.columns;
                });

                if (columnOut) {
                    throw new RangeError('column indices are out of range');
                }
                if (!Array.isArray(columnIndices)) columnIndices = Array.from(columnIndices);

                return columnIndices;
            }

            function checkRange(matrix, startRow, endRow, startColumn, endColumn) {
                if (arguments.length !== 5) {
                    throw new RangeError('expected 4 arguments');
                }
                checkNumber('startRow', startRow);
                checkNumber('endRow', endRow);
                checkNumber('startColumn', startColumn);
                checkNumber('endColumn', endColumn);
                if (
                    startRow > endRow ||
                    startColumn > endColumn ||
                    startRow < 0 ||
                    startRow >= matrix.rows ||
                    endRow < 0 ||
                    endRow >= matrix.rows ||
                    startColumn < 0 ||
                    startColumn >= matrix.columns ||
                    endColumn < 0 ||
                    endColumn >= matrix.columns
                ) {
                    throw new RangeError('Submatrix indices are out of range');
                }
            }

            function sumByRow(matrix) {
                var sum = Matrix.zeros(matrix.rows, 1);
                for (var i = 0; i < matrix.rows; ++i) {
                    for (var j = 0; j < matrix.columns; ++j) {
                        sum.set(i, 0, sum.get(i, 0) + matrix.get(i, j));
                    }
                }
                return sum;
            }

            function sumByColumn(matrix) {
                var sum = Matrix.zeros(1, matrix.columns);
                for (var i = 0; i < matrix.rows; ++i) {
                    for (var j = 0; j < matrix.columns; ++j) {
                        sum.set(0, j, sum.get(0, j) + matrix.get(i, j));
                    }
                }
                return sum;
            }

            function sumAll(matrix) {
                var v = 0;
                for (var i = 0; i < matrix.rows; i++) {
                    for (var j = 0; j < matrix.columns; j++) {
                        v += matrix.get(i, j);
                    }
                }
                return v;
            }

            function checkNumber(name, value) {
                if (typeof value !== 'number') {
                    throw new TypeError(`${name} must be a number`);
                }
            }

            class BaseView extends AbstractMatrix() {
                constructor(matrix, rows, columns) {
                    super();
                    this.matrix = matrix;
                    this.rows = rows;
                    this.columns = columns;
                }

                static get [Symbol.species]() {
                    return Matrix;
                }
            }

            class MatrixTransposeView extends BaseView {
                constructor(matrix) {
                    super(matrix, matrix.columns, matrix.rows);
                }

                set(rowIndex, columnIndex, value) {
                    this.matrix.set(columnIndex, rowIndex, value);
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this.matrix.get(columnIndex, rowIndex);
                }
            }

            class MatrixRowView extends BaseView {
                constructor(matrix, row) {
                    super(matrix, 1, matrix.columns);
                    this.row = row;
                }

                set(rowIndex, columnIndex, value) {
                    this.matrix.set(this.row, columnIndex, value);
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this.matrix.get(this.row, columnIndex);
                }
            }

            class MatrixSubView extends BaseView {
                constructor(matrix, startRow, endRow, startColumn, endColumn) {
                    checkRange(matrix, startRow, endRow, startColumn, endColumn);
                    super(matrix, endRow - startRow + 1, endColumn - startColumn + 1);
                    this.startRow = startRow;
                    this.startColumn = startColumn;
                }

                set(rowIndex, columnIndex, value) {
                    this.matrix.set(
                        this.startRow + rowIndex,
                        this.startColumn + columnIndex,
                        value
                    );
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this.matrix.get(
                        this.startRow + rowIndex,
                        this.startColumn + columnIndex
                    );
                }
            }

            class MatrixSelectionView extends BaseView {
                constructor(matrix, rowIndices, columnIndices) {
                    var indices = checkIndices(matrix, rowIndices, columnIndices);
                    super(matrix, indices.row.length, indices.column.length);
                    this.rowIndices = indices.row;
                    this.columnIndices = indices.column;
                }

                set(rowIndex, columnIndex, value) {
                    this.matrix.set(
                        this.rowIndices[rowIndex],
                        this.columnIndices[columnIndex],
                        value
                    );
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this.matrix.get(
                        this.rowIndices[rowIndex],
                        this.columnIndices[columnIndex]
                    );
                }
            }

            class MatrixRowSelectionView extends BaseView {
                constructor(matrix, rowIndices) {
                    rowIndices = checkRowIndices(matrix, rowIndices);
                    super(matrix, rowIndices.length, matrix.columns);
                    this.rowIndices = rowIndices;
                }

                set(rowIndex, columnIndex, value) {
                    this.matrix.set(this.rowIndices[rowIndex], columnIndex, value);
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this.matrix.get(this.rowIndices[rowIndex], columnIndex);
                }
            }

            class MatrixColumnSelectionView extends BaseView {
                constructor(matrix, columnIndices) {
                    columnIndices = checkColumnIndices(matrix, columnIndices);
                    super(matrix, matrix.rows, columnIndices.length);
                    this.columnIndices = columnIndices;
                }

                set(rowIndex, columnIndex, value) {
                    this.matrix.set(rowIndex, this.columnIndices[columnIndex], value);
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this.matrix.get(rowIndex, this.columnIndices[columnIndex]);
                }
            }

            class MatrixColumnView extends BaseView {
                constructor(matrix, column) {
                    super(matrix, matrix.rows, 1);
                    this.column = column;
                }

                set(rowIndex, columnIndex, value) {
                    this.matrix.set(rowIndex, this.column, value);
                    return this;
                }

                get(rowIndex) {
                    return this.matrix.get(rowIndex, this.column);
                }
            }

            class MatrixFlipRowView extends BaseView {
                constructor(matrix) {
                    super(matrix, matrix.rows, matrix.columns);
                }

                set(rowIndex, columnIndex, value) {
                    this.matrix.set(this.rows - rowIndex - 1, columnIndex, value);
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this.matrix.get(this.rows - rowIndex - 1, columnIndex);
                }
            }

            class MatrixFlipColumnView extends BaseView {
                constructor(matrix) {
                    super(matrix, matrix.rows, matrix.columns);
                }

                set(rowIndex, columnIndex, value) {
                    this.matrix.set(rowIndex, this.columns - columnIndex - 1, value);
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this.matrix.get(rowIndex, this.columns - columnIndex - 1);
                }
            }

            function AbstractMatrix(superCtor) {
                if (superCtor === undefined) superCtor = Object;

                /**
                 * Real matrix
                 * @class Matrix
                 * @param {number|Array|Matrix} nRows - Number of rows of the new matrix,
                 * 2D array containing the data or Matrix instance to clone
                 * @param {number} [nColumns] - Number of columns of the new matrix
                 */
                class Matrix extends superCtor {
                    static get [Symbol.species]() {
                        return this;
                    }

                    /**
                     * Constructs a Matrix with the chosen dimensions from a 1D array
                     * @param {number} newRows - Number of rows
                     * @param {number} newColumns - Number of columns
                     * @param {Array} newData - A 1D array containing data for the matrix
                     * @return {Matrix} - The new matrix
                     */
                    static from1DArray(newRows, newColumns, newData) {
                        var length = newRows * newColumns;
                        if (length !== newData.length) {
                            throw new RangeError('Data length does not match given dimensions');
                        }
                        var newMatrix = new this(newRows, newColumns);
                        for (var row = 0; row < newRows; row++) {
                            for (var column = 0; column < newColumns; column++) {
                                newMatrix.set(row, column, newData[row * newColumns + column]);
                            }
                        }
                        return newMatrix;
                    }

                    /**
                     * Creates a row vector, a matrix with only one row.
                     * @param {Array} newData - A 1D array containing data for the vector
                     * @return {Matrix} - The new matrix
                     */
                    static rowVector(newData) {
                        var vector = new this(1, newData.length);
                        for (var i = 0; i < newData.length; i++) {
                            vector.set(0, i, newData[i]);
                        }
                        return vector;
                    }

                    /**
                     * Creates a column vector, a matrix with only one column.
                     * @param {Array} newData - A 1D array containing data for the vector
                     * @return {Matrix} - The new matrix
                     */
                    static columnVector(newData) {
                        var vector = new this(newData.length, 1);
                        for (var i = 0; i < newData.length; i++) {
                            vector.set(i, 0, newData[i]);
                        }
                        return vector;
                    }

                    /**
                     * Creates an empty matrix with the given dimensions. Values will be undefined. Same as using new Matrix(rows, columns).
                     * @param {number} rows - Number of rows
                     * @param {number} columns - Number of columns
                     * @return {Matrix} - The new matrix
                     */
                    static empty(rows, columns) {
                        return new this(rows, columns);
                    }

                    /**
                     * Creates a matrix with the given dimensions. Values will be set to zero.
                     * @param {number} rows - Number of rows
                     * @param {number} columns - Number of columns
                     * @return {Matrix} - The new matrix
                     */
                    static zeros(rows, columns) {
                        return this.empty(rows, columns).fill(0);
                    }

                    /**
                     * Creates a matrix with the given dimensions. Values will be set to one.
                     * @param {number} rows - Number of rows
                     * @param {number} columns - Number of columns
                     * @return {Matrix} - The new matrix
                     */
                    static ones(rows, columns) {
                        return this.empty(rows, columns).fill(1);
                    }

                    /**
                     * Creates a matrix with the given dimensions. Values will be randomly set.
                     * @param {number} rows - Number of rows
                     * @param {number} columns - Number of columns
                     * @param {function} [rng=Math.random] - Random number generator
                     * @return {Matrix} The new matrix
                     */
                    static rand(rows, columns, rng) {
                        if (rng === undefined) rng = Math.random;
                        var matrix = this.empty(rows, columns);
                        for (var i = 0; i < rows; i++) {
                            for (var j = 0; j < columns; j++) {
                                matrix.set(i, j, rng());
                            }
                        }
                        return matrix;
                    }

                    /**
                     * Creates a matrix with the given dimensions. Values will be random integers.
                     * @param {number} rows - Number of rows
                     * @param {number} columns - Number of columns
                     * @param {number} [maxValue=1000] - Maximum value
                     * @param {function} [rng=Math.random] - Random number generator
                     * @return {Matrix} The new matrix
                     */
                    static randInt(rows, columns, maxValue, rng) {
                        if (maxValue === undefined) maxValue = 1000;
                        if (rng === undefined) rng = Math.random;
                        var matrix = this.empty(rows, columns);
                        for (var i = 0; i < rows; i++) {
                            for (var j = 0; j < columns; j++) {
                                var value = Math.floor(rng() * maxValue);
                                matrix.set(i, j, value);
                            }
                        }
                        return matrix;
                    }

                    /**
                     * Creates an identity matrix with the given dimension. Values of the diagonal will be 1 and others will be 0.
                     * @param {number} rows - Number of rows
                     * @param {number} [columns=rows] - Number of columns
                     * @param {number} [value=1] - Value to fill the diagonal with
                     * @return {Matrix} - The new identity matrix
                     */
                    static eye(rows, columns, value) {
                        if (columns === undefined) columns = rows;
                        if (value === undefined) value = 1;
                        var min = Math.min(rows, columns);
                        var matrix = this.zeros(rows, columns);
                        for (var i = 0; i < min; i++) {
                            matrix.set(i, i, value);
                        }
                        return matrix;
                    }

                    /**
                     * Creates a diagonal matrix based on the given array.
                     * @param {Array} data - Array containing the data for the diagonal
                     * @param {number} [rows] - Number of rows (Default: data.length)
                     * @param {number} [columns] - Number of columns (Default: rows)
                     * @return {Matrix} - The new diagonal matrix
                     */
                    static diag(data, rows, columns) {
                        var l = data.length;
                        if (rows === undefined) rows = l;
                        if (columns === undefined) columns = rows;
                        var min = Math.min(l, rows, columns);
                        var matrix = this.zeros(rows, columns);
                        for (var i = 0; i < min; i++) {
                            matrix.set(i, i, data[i]);
                        }
                        return matrix;
                    }

                    /**
                     * Returns a matrix whose elements are the minimum between matrix1 and matrix2
                     * @param {Matrix} matrix1
                     * @param {Matrix} matrix2
                     * @return {Matrix}
                     */
                    static min(matrix1, matrix2) {
                        matrix1 = this.checkMatrix(matrix1);
                        matrix2 = this.checkMatrix(matrix2);
                        var rows = matrix1.rows;
                        var columns = matrix1.columns;
                        var result = new this(rows, columns);
                        for (var i = 0; i < rows; i++) {
                            for (var j = 0; j < columns; j++) {
                                result.set(i, j, Math.min(matrix1.get(i, j), matrix2.get(i, j)));
                            }
                        }
                        return result;
                    }

                    /**
                     * Returns a matrix whose elements are the maximum between matrix1 and matrix2
                     * @param {Matrix} matrix1
                     * @param {Matrix} matrix2
                     * @return {Matrix}
                     */
                    static max(matrix1, matrix2) {
                        matrix1 = this.checkMatrix(matrix1);
                        matrix2 = this.checkMatrix(matrix2);
                        var rows = matrix1.rows;
                        var columns = matrix1.columns;
                        var result = new this(rows, columns);
                        for (var i = 0; i < rows; i++) {
                            for (var j = 0; j < columns; j++) {
                                result.set(i, j, Math.max(matrix1.get(i, j), matrix2.get(i, j)));
                            }
                        }
                        return result;
                    }

                    /**
                     * Check that the provided value is a Matrix and tries to instantiate one if not
                     * @param {*} value - The value to check
                     * @return {Matrix}
                     */
                    static checkMatrix(value) {
                        return Matrix.isMatrix(value) ? value : new this(value);
                    }

                    /**
                     * Returns true if the argument is a Matrix, false otherwise
                     * @param {*} value - The value to check
                     * @return {boolean}
                     */
                    static isMatrix(value) {
                        return (value != null) && (value.klass === 'Matrix');
                    }

                    /**
                     * @prop {number} size - The number of elements in the matrix.
                     */
                    get size() {
                        return this.rows * this.columns;
                    }

                    /**
                     * Applies a callback for each element of the matrix. The function is called in the matrix (this) context.
                     * @param {function} callback - Function that will be called with two parameters : i (row) and j (column)
                     * @return {Matrix} this
                     */
                    apply(callback) {
                        if (typeof callback !== 'function') {
                            throw new TypeError('callback must be a function');
                        }
                        var ii = this.rows;
                        var jj = this.columns;
                        for (var i = 0; i < ii; i++) {
                            for (var j = 0; j < jj; j++) {
                                callback.call(this, i, j);
                            }
                        }
                        return this;
                    }

                    /**
                     * Returns a new 1D array filled row by row with the matrix values
                     * @return {Array}
                     */
                    to1DArray() {
                        var array = new Array(this.size);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                array[i * this.columns + j] = this.get(i, j);
                            }
                        }
                        return array;
                    }

                    /**
                     * Returns a 2D array containing a copy of the data
                     * @return {Array}
                     */
                    to2DArray() {
                        var copy = new Array(this.rows);
                        for (var i = 0; i < this.rows; i++) {
                            copy[i] = new Array(this.columns);
                            for (var j = 0; j < this.columns; j++) {
                                copy[i][j] = this.get(i, j);
                            }
                        }
                        return copy;
                    }

                    /**
                     * @return {boolean} true if the matrix has one row
                     */
                    isRowVector() {
                        return this.rows === 1;
                    }

                    /**
                     * @return {boolean} true if the matrix has one column
                     */
                    isColumnVector() {
                        return this.columns === 1;
                    }

                    /**
                     * @return {boolean} true if the matrix has one row or one column
                     */
                    isVector() {
                        return (this.rows === 1) || (this.columns === 1);
                    }

                    /**
                     * @return {boolean} true if the matrix has the same number of rows and columns
                     */
                    isSquare() {
                        return this.rows === this.columns;
                    }

                    /**
                     * @return {boolean} true if the matrix is square and has the same values on both sides of the diagonal
                     */
                    isSymmetric() {
                        if (this.isSquare()) {
                            for (var i = 0; i < this.rows; i++) {
                                for (var j = 0; j <= i; j++) {
                                    if (this.get(i, j) !== this.get(j, i)) {
                                        return false;
                                    }
                                }
                            }
                            return true;
                        }
                        return false;
                    }

                    /**
                     * Sets a given element of the matrix. mat.set(3,4,1) is equivalent to mat[3][4]=1
                     * @abstract
                     * @param {number} rowIndex - Index of the row
                     * @param {number} columnIndex - Index of the column
                     * @param {number} value - The new value for the element
                     * @return {Matrix} this
                     */
                    set(rowIndex, columnIndex, value) { // eslint-disable-line no-unused-vars
                        throw new Error('set method is unimplemented');
                    }

                    /**
                     * Returns the given element of the matrix. mat.get(3,4) is equivalent to matrix[3][4]
                     * @abstract
                     * @param {number} rowIndex - Index of the row
                     * @param {number} columnIndex - Index of the column
                     * @return {number}
                     */
                    get(rowIndex, columnIndex) { // eslint-disable-line no-unused-vars
                        throw new Error('get method is unimplemented');
                    }

                    /**
                     * Creates a new matrix that is a repetition of the current matrix. New matrix has rowRep times the number of
                     * rows of the matrix, and colRep times the number of columns of the matrix
                     * @param {number} rowRep - Number of times the rows should be repeated
                     * @param {number} colRep - Number of times the columns should be re
                     * @return {Matrix}
                     * @example
                     * var matrix = new Matrix([[1,2]]);
                     * matrix.repeat(2); // [[1,2],[1,2]]
                     */
                    repeat(rowRep, colRep) {
                        rowRep = rowRep || 1;
                        colRep = colRep || 1;
                        var matrix = new this.constructor[Symbol.species](this.rows * rowRep, this.columns * colRep);
                        for (var i = 0; i < rowRep; i++) {
                            for (var j = 0; j < colRep; j++) {
                                matrix.setSubMatrix(this, this.rows * i, this.columns * j);
                            }
                        }
                        return matrix;
                    }

                    /**
                     * Fills the matrix with a given value. All elements will be set to this value.
                     * @param {number} value - New value
                     * @return {Matrix} this
                     */
                    fill(value) {
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                this.set(i, j, value);
                            }
                        }
                        return this;
                    }

                    /**
                     * Negates the matrix. All elements will be multiplied by (-1)
                     * @return {Matrix} this
                     */
                    neg() {
                        return this.mulS(-1);
                    }

                    /**
                     * Returns a new array from the given row index
                     * @param {number} index - Row index
                     * @return {Array}
                     */
                    getRow(index) {
                        checkRowIndex(this, index);
                        var row = new Array(this.columns);
                        for (var i = 0; i < this.columns; i++) {
                            row[i] = this.get(index, i);
                        }
                        return row;
                    }

                    /**
                     * Returns a new row vector from the given row index
                     * @param {number} index - Row index
                     * @return {Matrix}
                     */
                    getRowVector(index) {
                        return this.constructor.rowVector(this.getRow(index));
                    }

                    /**
                     * Sets a row at the given index
                     * @param {number} index - Row index
                     * @param {Array|Matrix} array - Array or vector
                     * @return {Matrix} this
                     */
                    setRow(index, array) {
                        checkRowIndex(this, index);
                        array = checkRowVector(this, array);
                        for (var i = 0; i < this.columns; i++) {
                            this.set(index, i, array[i]);
                        }
                        return this;
                    }

                    /**
                     * Swaps two rows
                     * @param {number} row1 - First row index
                     * @param {number} row2 - Second row index
                     * @return {Matrix} this
                     */
                    swapRows(row1, row2) {
                        checkRowIndex(this, row1);
                        checkRowIndex(this, row2);
                        for (var i = 0; i < this.columns; i++) {
                            var temp = this.get(row1, i);
                            this.set(row1, i, this.get(row2, i));
                            this.set(row2, i, temp);
                        }
                        return this;
                    }

                    /**
                     * Returns a new array from the given column index
                     * @param {number} index - Column index
                     * @return {Array}
                     */
                    getColumn(index) {
                        checkColumnIndex(this, index);
                        var column = new Array(this.rows);
                        for (var i = 0; i < this.rows; i++) {
                            column[i] = this.get(i, index);
                        }
                        return column;
                    }

                    /**
                     * Returns a new column vector from the given column index
                     * @param {number} index - Column index
                     * @return {Matrix}
                     */
                    getColumnVector(index) {
                        return this.constructor.columnVector(this.getColumn(index));
                    }

                    /**
                     * Sets a column at the given index
                     * @param {number} index - Column index
                     * @param {Array|Matrix} array - Array or vector
                     * @return {Matrix} this
                     */
                    setColumn(index, array) {
                        checkColumnIndex(this, index);
                        array = checkColumnVector(this, array);
                        for (var i = 0; i < this.rows; i++) {
                            this.set(i, index, array[i]);
                        }
                        return this;
                    }

                    /**
                     * Swaps two columns
                     * @param {number} column1 - First column index
                     * @param {number} column2 - Second column index
                     * @return {Matrix} this
                     */
                    swapColumns(column1, column2) {
                        checkColumnIndex(this, column1);
                        checkColumnIndex(this, column2);
                        for (var i = 0; i < this.rows; i++) {
                            var temp = this.get(i, column1);
                            this.set(i, column1, this.get(i, column2));
                            this.set(i, column2, temp);
                        }
                        return this;
                    }

                    /**
                     * Adds the values of a vector to each row
                     * @param {Array|Matrix} vector - Array or vector
                     * @return {Matrix} this
                     */
                    addRowVector(vector) {
                        vector = checkRowVector(this, vector);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                this.set(i, j, this.get(i, j) + vector[j]);
                            }
                        }
                        return this;
                    }

                    /**
                     * Subtracts the values of a vector from each row
                     * @param {Array|Matrix} vector - Array or vector
                     * @return {Matrix} this
                     */
                    subRowVector(vector) {
                        vector = checkRowVector(this, vector);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                this.set(i, j, this.get(i, j) - vector[j]);
                            }
                        }
                        return this;
                    }

                    /**
                     * Multiplies the values of a vector with each row
                     * @param {Array|Matrix} vector - Array or vector
                     * @return {Matrix} this
                     */
                    mulRowVector(vector) {
                        vector = checkRowVector(this, vector);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                this.set(i, j, this.get(i, j) * vector[j]);
                            }
                        }
                        return this;
                    }

                    /**
                     * Divides the values of each row by those of a vector
                     * @param {Array|Matrix} vector - Array or vector
                     * @return {Matrix} this
                     */
                    divRowVector(vector) {
                        vector = checkRowVector(this, vector);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                this.set(i, j, this.get(i, j) / vector[j]);
                            }
                        }
                        return this;
                    }

                    /**
                     * Adds the values of a vector to each column
                     * @param {Array|Matrix} vector - Array or vector
                     * @return {Matrix} this
                     */
                    addColumnVector(vector) {
                        vector = checkColumnVector(this, vector);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                this.set(i, j, this.get(i, j) + vector[i]);
                            }
                        }
                        return this;
                    }

                    /**
                     * Subtracts the values of a vector from each column
                     * @param {Array|Matrix} vector - Array or vector
                     * @return {Matrix} this
                     */
                    subColumnVector(vector) {
                        vector = checkColumnVector(this, vector);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                this.set(i, j, this.get(i, j) - vector[i]);
                            }
                        }
                        return this;
                    }

                    /**
                     * Multiplies the values of a vector with each column
                     * @param {Array|Matrix} vector - Array or vector
                     * @return {Matrix} this
                     */
                    mulColumnVector(vector) {
                        vector = checkColumnVector(this, vector);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                this.set(i, j, this.get(i, j) * vector[i]);
                            }
                        }
                        return this;
                    }

                    /**
                     * Divides the values of each column by those of a vector
                     * @param {Array|Matrix} vector - Array or vector
                     * @return {Matrix} this
                     */
                    divColumnVector(vector) {
                        vector = checkColumnVector(this, vector);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                this.set(i, j, this.get(i, j) / vector[i]);
                            }
                        }
                        return this;
                    }

                    /**
                     * Multiplies the values of a row with a scalar
                     * @param {number} index - Row index
                     * @param {number} value
                     * @return {Matrix} this
                     */
                    mulRow(index, value) {
                        checkRowIndex(this, index);
                        for (var i = 0; i < this.columns; i++) {
                            this.set(index, i, this.get(index, i) * value);
                        }
                        return this;
                    }

                    /**
                     * Multiplies the values of a column with a scalar
                     * @param {number} index - Column index
                     * @param {number} value
                     * @return {Matrix} this
                     */
                    mulColumn(index, value) {
                        checkColumnIndex(this, index);
                        for (var i = 0; i < this.rows; i++) {
                            this.set(i, index, this.get(i, index) * value);
                        }
                        return this;
                    }

                    /**
                     * Returns the maximum value of the matrix
                     * @return {number}
                     */
                    max() {
                        var v = this.get(0, 0);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                if (this.get(i, j) > v) {
                                    v = this.get(i, j);
                                }
                            }
                        }
                        return v;
                    }

                    /**
                     * Returns the index of the maximum value
                     * @return {Array}
                     */
                    maxIndex() {
                        var v = this.get(0, 0);
                        var idx = [0, 0];
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                if (this.get(i, j) > v) {
                                    v = this.get(i, j);
                                    idx[0] = i;
                                    idx[1] = j;
                                }
                            }
                        }
                        return idx;
                    }

                    /**
                     * Returns the minimum value of the matrix
                     * @return {number}
                     */
                    min() {
                        var v = this.get(0, 0);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                if (this.get(i, j) < v) {
                                    v = this.get(i, j);
                                }
                            }
                        }
                        return v;
                    }

                    /**
                     * Returns the index of the minimum value
                     * @return {Array}
                     */
                    minIndex() {
                        var v = this.get(0, 0);
                        var idx = [0, 0];
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                if (this.get(i, j) < v) {
                                    v = this.get(i, j);
                                    idx[0] = i;
                                    idx[1] = j;
                                }
                            }
                        }
                        return idx;
                    }

                    /**
                     * Returns the maximum value of one row
                     * @param {number} row - Row index
                     * @return {number}
                     */
                    maxRow(row) {
                        checkRowIndex(this, row);
                        var v = this.get(row, 0);
                        for (var i = 1; i < this.columns; i++) {
                            if (this.get(row, i) > v) {
                                v = this.get(row, i);
                            }
                        }
                        return v;
                    }

                    /**
                     * Returns the index of the maximum value of one row
                     * @param {number} row - Row index
                     * @return {Array}
                     */
                    maxRowIndex(row) {
                        checkRowIndex(this, row);
                        var v = this.get(row, 0);
                        var idx = [row, 0];
                        for (var i = 1; i < this.columns; i++) {
                            if (this.get(row, i) > v) {
                                v = this.get(row, i);
                                idx[1] = i;
                            }
                        }
                        return idx;
                    }

                    /**
                     * Returns the minimum value of one row
                     * @param {number} row - Row index
                     * @return {number}
                     */
                    minRow(row) {
                        checkRowIndex(this, row);
                        var v = this.get(row, 0);
                        for (var i = 1; i < this.columns; i++) {
                            if (this.get(row, i) < v) {
                                v = this.get(row, i);
                            }
                        }
                        return v;
                    }

                    /**
                     * Returns the index of the maximum value of one row
                     * @param {number} row - Row index
                     * @return {Array}
                     */
                    minRowIndex(row) {
                        checkRowIndex(this, row);
                        var v = this.get(row, 0);
                        var idx = [row, 0];
                        for (var i = 1; i < this.columns; i++) {
                            if (this.get(row, i) < v) {
                                v = this.get(row, i);
                                idx[1] = i;
                            }
                        }
                        return idx;
                    }

                    /**
                     * Returns the maximum value of one column
                     * @param {number} column - Column index
                     * @return {number}
                     */
                    maxColumn(column) {
                        checkColumnIndex(this, column);
                        var v = this.get(0, column);
                        for (var i = 1; i < this.rows; i++) {
                            if (this.get(i, column) > v) {
                                v = this.get(i, column);
                            }
                        }
                        return v;
                    }

                    /**
                     * Returns the index of the maximum value of one column
                     * @param {number} column - Column index
                     * @return {Array}
                     */
                    maxColumnIndex(column) {
                        checkColumnIndex(this, column);
                        var v = this.get(0, column);
                        var idx = [0, column];
                        for (var i = 1; i < this.rows; i++) {
                            if (this.get(i, column) > v) {
                                v = this.get(i, column);
                                idx[0] = i;
                            }
                        }
                        return idx;
                    }

                    /**
                     * Returns the minimum value of one column
                     * @param {number} column - Column index
                     * @return {number}
                     */
                    minColumn(column) {
                        checkColumnIndex(this, column);
                        var v = this.get(0, column);
                        for (var i = 1; i < this.rows; i++) {
                            if (this.get(i, column) < v) {
                                v = this.get(i, column);
                            }
                        }
                        return v;
                    }

                    /**
                     * Returns the index of the minimum value of one column
                     * @param {number} column - Column index
                     * @return {Array}
                     */
                    minColumnIndex(column) {
                        checkColumnIndex(this, column);
                        var v = this.get(0, column);
                        var idx = [0, column];
                        for (var i = 1; i < this.rows; i++) {
                            if (this.get(i, column) < v) {
                                v = this.get(i, column);
                                idx[0] = i;
                            }
                        }
                        return idx;
                    }

                    /**
                     * Returns an array containing the diagonal values of the matrix
                     * @return {Array}
                     */
                    diag() {
                        var min = Math.min(this.rows, this.columns);
                        var diag = new Array(min);
                        for (var i = 0; i < min; i++) {
                            diag[i] = this.get(i, i);
                        }
                        return diag;
                    }

                    /**
                     * Returns the sum by the argument given, if no argument given,
                     * it returns the sum of all elements of the matrix.
                     * @param {string} by - sum by 'row' or 'column'.
                     * @return {Matrix|number}
                     */
                    sum(by) {
                        switch (by) {
                            case 'row':
                                return sumByRow(this);
                            case 'column':
                                return sumByColumn(this);
                            default:
                                return sumAll(this);
                        }
                    }

                    /**
                     * Returns the mean of all elements of the matrix
                     * @return {number}
                     */
                    mean() {
                        return this.sum() / this.size;
                    }

                    /**
                     * Returns the product of all elements of the matrix
                     * @return {number}
                     */
                    prod() {
                        var prod = 1;
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                prod *= this.get(i, j);
                            }
                        }
                        return prod;
                    }

                    /**
                     * Returns the norm of a matrix.
                     * @param {string} type - "frobenius" (default) or "max" return resp. the Frobenius norm and the max norm.
                     * @return {number}
                     */
                    norm(type = 'frobenius') {
                        var result = 0;
                        if (type === 'max') {
                            return this.max();
                        } else if (type === 'frobenius') {
                            for (var i = 0; i < this.rows; i++) {
                                for (var j = 0; j < this.columns; j++) {
                                    result = result + this.get(i, j) * this.get(i, j);
                                }
                            }
                            return Math.sqrt(result);
                        } else {
                            throw new RangeError(`unknown norm type: ${type}`);
                        }
                    }

                    /**
                     * Computes the cumulative sum of the matrix elements (in place, row by row)
                     * @return {Matrix} this
                     */
                    cumulativeSum() {
                        var sum = 0;
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                sum += this.get(i, j);
                                this.set(i, j, sum);
                            }
                        }
                        return this;
                    }

                    /**
                     * Computes the dot (scalar) product between the matrix and another
                     * @param {Matrix} vector2 vector
                     * @return {number}
                     */
                    dot(vector2) {
                        if (Matrix.isMatrix(vector2)) vector2 = vector2.to1DArray();
                        var vector1 = this.to1DArray();
                        if (vector1.length !== vector2.length) {
                            throw new RangeError('vectors do not have the same size');
                        }
                        var dot = 0;
                        for (var i = 0; i < vector1.length; i++) {
                            dot += vector1[i] * vector2[i];
                        }
                        return dot;
                    }

                    /**
                     * Returns the matrix product between this and other
                     * @param {Matrix} other
                     * @return {Matrix}
                     */
                    mmul(other) {
                        other = this.constructor.checkMatrix(other);
                        if (this.columns !== other.rows) {
                            // eslint-disable-next-line no-console
                            console.warn('Number of columns of left matrix are not equal to number of rows of right matrix.');
                        }

                        var m = this.rows;
                        var n = this.columns;
                        var p = other.columns;

                        var result = new this.constructor[Symbol.species](m, p);

                        var Bcolj = new Array(n);
                        for (var j = 0; j < p; j++) {
                            for (var k = 0; k < n; k++) {
                                Bcolj[k] = other.get(k, j);
                            }

                            for (var i = 0; i < m; i++) {
                                var s = 0;
                                for (k = 0; k < n; k++) {
                                    s += this.get(i, k) * Bcolj[k];
                                }

                                result.set(i, j, s);
                            }
                        }
                        return result;
                    }

                    strassen2x2(other) {
                        var result = new this.constructor[Symbol.species](2, 2);
                        const a11 = this.get(0, 0);
                        const b11 = other.get(0, 0);
                        const a12 = this.get(0, 1);
                        const b12 = other.get(0, 1);
                        const a21 = this.get(1, 0);
                        const b21 = other.get(1, 0);
                        const a22 = this.get(1, 1);
                        const b22 = other.get(1, 1);

                        // Compute intermediate values.
                        const m1 = (a11 + a22) * (b11 + b22);
                        const m2 = (a21 + a22) * b11;
                        const m3 = a11 * (b12 - b22);
                        const m4 = a22 * (b21 - b11);
                        const m5 = (a11 + a12) * b22;
                        const m6 = (a21 - a11) * (b11 + b12);
                        const m7 = (a12 - a22) * (b21 + b22);

                        // Combine intermediate values into the output.
                        const c00 = m1 + m4 - m5 + m7;
                        const c01 = m3 + m5;
                        const c10 = m2 + m4;
                        const c11 = m1 - m2 + m3 + m6;

                        result.set(0, 0, c00);
                        result.set(0, 1, c01);
                        result.set(1, 0, c10);
                        result.set(1, 1, c11);
                        return result;
                    }

                    strassen3x3(other) {
                        var result = new this.constructor[Symbol.species](3, 3);

                        const a00 = this.get(0, 0);
                        const a01 = this.get(0, 1);
                        const a02 = this.get(0, 2);
                        const a10 = this.get(1, 0);
                        const a11 = this.get(1, 1);
                        const a12 = this.get(1, 2);
                        const a20 = this.get(2, 0);
                        const a21 = this.get(2, 1);
                        const a22 = this.get(2, 2);

                        const b00 = other.get(0, 0);
                        const b01 = other.get(0, 1);
                        const b02 = other.get(0, 2);
                        const b10 = other.get(1, 0);
                        const b11 = other.get(1, 1);
                        const b12 = other.get(1, 2);
                        const b20 = other.get(2, 0);
                        const b21 = other.get(2, 1);
                        const b22 = other.get(2, 2);

                        const m1 = (a00 + a01 + a02 - a10 - a11 - a21 - a22) * b11;
                        const m2 = (a00 - a10) * (-b01 + b11);
                        const m3 = a11 * (-b00 + b01 + b10 - b11 - b12 - b20 + b22);
                        const m4 = (-a00 + a10 + a11) * (b00 - b01 + b11);
                        const m5 = (a10 + a11) * (-b00 + b01);
                        const m6 = a00 * b00;
                        const m7 = (-a00 + a20 + a21) * (b00 - b02 + b12);
                        const m8 = (-a00 + a20) * (b02 - b12);
                        const m9 = (a20 + a21) * (-b00 + b02);
                        const m10 = (a00 + a01 + a02 - a11 - a12 - a20 - a21) * b12;
                        const m11 = a21 * (-b00 + b02 + b10 - b11 - b12 - b20 + b21);
                        const m12 = (-a02 + a21 + a22) * (b11 + b20 - b21);
                        const m13 = (a02 - a22) * (b11 - b21);
                        const m14 = a02 * b20;
                        const m15 = (a21 + a22) * (-b20 + b21);
                        const m16 = (-a02 + a11 + a12) * (b12 + b20 - b22);
                        const m17 = (a02 - a12) * (b12 - b22);
                        const m18 = (a11 + a12) * (-b20 + b22);
                        const m19 = a01 * b10;
                        const m20 = a12 * b21;
                        const m21 = a10 * b02;
                        const m22 = a20 * b01;
                        const m23 = a22 * b22;

                        const c00 = m6 + m14 + m19;
                        const c01 = m1 + m4 + m5 + m6 + m12 + m14 + m15;
                        const c02 = m6 + m7 + m9 + m10 + m14 + m16 + m18;
                        const c10 = m2 + m3 + m4 + m6 + m14 + m16 + m17;
                        const c11 = m2 + m4 + m5 + m6 + m20;
                        const c12 = m14 + m16 + m17 + m18 + m21;
                        const c20 = m6 + m7 + m8 + m11 + m12 + m13 + m14;
                        const c21 = m12 + m13 + m14 + m15 + m22;
                        const c22 = m6 + m7 + m8 + m9 + m23;

                        result.set(0, 0, c00);
                        result.set(0, 1, c01);
                        result.set(0, 2, c02);
                        result.set(1, 0, c10);
                        result.set(1, 1, c11);
                        result.set(1, 2, c12);
                        result.set(2, 0, c20);
                        result.set(2, 1, c21);
                        result.set(2, 2, c22);
                        return result;
                    }

                    /**
                     * Returns the matrix product between x and y. More efficient than mmul(other) only when we multiply squared matrix and when the size of the matrix is > 1000.
                     * @param {Matrix} y
                     * @return {Matrix}
                     */
                    mmulStrassen(y) {
                        var x = this.clone();
                        var r1 = x.rows;
                        var c1 = x.columns;
                        var r2 = y.rows;
                        var c2 = y.columns;
                        if (c1 !== r2) {
                            // eslint-disable-next-line no-console
                            console.warn(`Multiplying ${r1} x ${c1} and ${r2} x ${c2} matrix: dimensions do not match.`);
                        }

                        // Put a matrix into the top left of a matrix of zeros.
                        // `rows` and `cols` are the dimensions of the output matrix.
                        function embed(mat, rows, cols) {
                            var r = mat.rows;
                            var c = mat.columns;
                            if ((r === rows) && (c === cols)) {
                                return mat;
                            } else {
                                var resultat = Matrix.zeros(rows, cols);
                                resultat = resultat.setSubMatrix(mat, 0, 0);
                                return resultat;
                            }
                        }


                        // Make sure both matrices are the same size.
                        // This is exclusively for simplicity:
                        // this algorithm can be implemented with matrices of different sizes.

                        var r = Math.max(r1, r2);
                        var c = Math.max(c1, c2);
                        x = embed(x, r, c);
                        y = embed(y, r, c);

                        // Our recursive multiplication function.
                        function blockMult(a, b, rows, cols) {
                            // For small matrices, resort to naive multiplication.
                            if (rows <= 512 || cols <= 512) {
                                return a.mmul(b); // a is equivalent to this
                            }

                            // Apply dynamic padding.
                            if ((rows % 2 === 1) && (cols % 2 === 1)) {
                                a = embed(a, rows + 1, cols + 1);
                                b = embed(b, rows + 1, cols + 1);
                            } else if (rows % 2 === 1) {
                                a = embed(a, rows + 1, cols);
                                b = embed(b, rows + 1, cols);
                            } else if (cols % 2 === 1) {
                                a = embed(a, rows, cols + 1);
                                b = embed(b, rows, cols + 1);
                            }

                            var halfRows = parseInt(a.rows / 2, 10);
                            var halfCols = parseInt(a.columns / 2, 10);
                            // Subdivide input matrices.
                            var a11 = a.subMatrix(0, halfRows - 1, 0, halfCols - 1);
                            var b11 = b.subMatrix(0, halfRows - 1, 0, halfCols - 1);

                            var a12 = a.subMatrix(0, halfRows - 1, halfCols, a.columns - 1);
                            var b12 = b.subMatrix(0, halfRows - 1, halfCols, b.columns - 1);

                            var a21 = a.subMatrix(halfRows, a.rows - 1, 0, halfCols - 1);
                            var b21 = b.subMatrix(halfRows, b.rows - 1, 0, halfCols - 1);

                            var a22 = a.subMatrix(halfRows, a.rows - 1, halfCols, a.columns - 1);
                            var b22 = b.subMatrix(halfRows, b.rows - 1, halfCols, b.columns - 1);

                            // Compute intermediate values.
                            var m1 = blockMult(Matrix.add(a11, a22), Matrix.add(b11, b22), halfRows, halfCols);
                            var m2 = blockMult(Matrix.add(a21, a22), b11, halfRows, halfCols);
                            var m3 = blockMult(a11, Matrix.sub(b12, b22), halfRows, halfCols);
                            var m4 = blockMult(a22, Matrix.sub(b21, b11), halfRows, halfCols);
                            var m5 = blockMult(Matrix.add(a11, a12), b22, halfRows, halfCols);
                            var m6 = blockMult(Matrix.sub(a21, a11), Matrix.add(b11, b12), halfRows, halfCols);
                            var m7 = blockMult(Matrix.sub(a12, a22), Matrix.add(b21, b22), halfRows, halfCols);

                            // Combine intermediate values into the output.
                            var c11 = Matrix.add(m1, m4);
                            c11.sub(m5);
                            c11.add(m7);
                            var c12 = Matrix.add(m3, m5);
                            var c21 = Matrix.add(m2, m4);
                            var c22 = Matrix.sub(m1, m2);
                            c22.add(m3);
                            c22.add(m6);

                            // Crop output to the desired size (undo dynamic padding).
                            var resultat = Matrix.zeros(2 * c11.rows, 2 * c11.columns);
                            resultat = resultat.setSubMatrix(c11, 0, 0);
                            resultat = resultat.setSubMatrix(c12, c11.rows, 0);
                            resultat = resultat.setSubMatrix(c21, 0, c11.columns);
                            resultat = resultat.setSubMatrix(c22, c11.rows, c11.columns);
                            return resultat.subMatrix(0, rows - 1, 0, cols - 1);
                        }

                        return blockMult(x, y, r, c);
                    }

                    /**
                     * Returns a row-by-row scaled matrix
                     * @param {number} [min=0] - Minimum scaled value
                     * @param {number} [max=1] - Maximum scaled value
                     * @return {Matrix} - The scaled matrix
                     */
                    scaleRows(min, max$$1) {
                        min = min === undefined ? 0 : min;
                        max$$1 = max$$1 === undefined ? 1 : max$$1;
                        if (min >= max$$1) {
                            throw new RangeError('min should be strictly smaller than max');
                        }
                        var newMatrix = this.constructor.empty(this.rows, this.columns);
                        for (var i = 0; i < this.rows; i++) {
                            var scaled = rescale(this.getRow(i), {min, max: max$$1});
                            newMatrix.setRow(i, scaled);
                        }
                        return newMatrix;
                    }

                    /**
                     * Returns a new column-by-column scaled matrix
                     * @param {number} [min=0] - Minimum scaled value
                     * @param {number} [max=1] - Maximum scaled value
                     * @return {Matrix} - The new scaled matrix
                     * @example
                     * var matrix = new Matrix([[1,2],[-1,0]]);
                     * var scaledMatrix = matrix.scaleColumns(); // [[1,1],[0,0]]
                     */
                    scaleColumns(min, max$$1) {
                        min = min === undefined ? 0 : min;
                        max$$1 = max$$1 === undefined ? 1 : max$$1;
                        if (min >= max$$1) {
                            throw new RangeError('min should be strictly smaller than max');
                        }
                        var newMatrix = this.constructor.empty(this.rows, this.columns);
                        for (var i = 0; i < this.columns; i++) {
                            var scaled = rescale(this.getColumn(i), {
                                min: min,
                                max: max$$1
                            });
                            newMatrix.setColumn(i, scaled);
                        }
                        return newMatrix;
                    }


                    /**
                     * Returns the Kronecker product (also known as tensor product) between this and other
                     * See https://en.wikipedia.org/wiki/Kronecker_product
                     * @param {Matrix} other
                     * @return {Matrix}
                     */
                    kroneckerProduct(other) {
                        other = this.constructor.checkMatrix(other);

                        var m = this.rows;
                        var n = this.columns;
                        var p = other.rows;
                        var q = other.columns;

                        var result = new this.constructor[Symbol.species](m * p, n * q);
                        for (var i = 0; i < m; i++) {
                            for (var j = 0; j < n; j++) {
                                for (var k = 0; k < p; k++) {
                                    for (var l = 0; l < q; l++) {
                                        result[p * i + k][q * j + l] = this.get(i, j) * other.get(k, l);
                                    }
                                }
                            }
                        }
                        return result;
                    }

                    /**
                     * Transposes the matrix and returns a new one containing the result
                     * @return {Matrix}
                     */
                    transpose() {
                        var result = new this.constructor[Symbol.species](this.columns, this.rows);
                        for (var i = 0; i < this.rows; i++) {
                            for (var j = 0; j < this.columns; j++) {
                                result.set(j, i, this.get(i, j));
                            }
                        }
                        return result;
                    }

                    /**
                     * Sorts the rows (in place)
                     * @param {function} compareFunction - usual Array.prototype.sort comparison function
                     * @return {Matrix} this
                     */
                    sortRows(compareFunction) {
                        if (compareFunction === undefined) compareFunction = compareNumbers;
                        for (var i = 0; i < this.rows; i++) {
                            this.setRow(i, this.getRow(i).sort(compareFunction));
                        }
                        return this;
                    }

                    /**
                     * Sorts the columns (in place)
                     * @param {function} compareFunction - usual Array.prototype.sort comparison function
                     * @return {Matrix} this
                     */
                    sortColumns(compareFunction) {
                        if (compareFunction === undefined) compareFunction = compareNumbers;
                        for (var i = 0; i < this.columns; i++) {
                            this.setColumn(i, this.getColumn(i).sort(compareFunction));
                        }
                        return this;
                    }

                    /**
                     * Returns a subset of the matrix
                     * @param {number} startRow - First row index
                     * @param {number} endRow - Last row index
                     * @param {number} startColumn - First column index
                     * @param {number} endColumn - Last column index
                     * @return {Matrix}
                     */
                    subMatrix(startRow, endRow, startColumn, endColumn) {
                        checkRange(this, startRow, endRow, startColumn, endColumn);
                        var newMatrix = new this.constructor[Symbol.species](endRow - startRow + 1, endColumn - startColumn + 1);
                        for (var i = startRow; i <= endRow; i++) {
                            for (var j = startColumn; j <= endColumn; j++) {
                                newMatrix[i - startRow][j - startColumn] = this.get(i, j);
                            }
                        }
                        return newMatrix;
                    }

                    /**
                     * Returns a subset of the matrix based on an array of row indices
                     * @param {Array} indices - Array containing the row indices
                     * @param {number} [startColumn = 0] - First column index
                     * @param {number} [endColumn = this.columns-1] - Last column index
                     * @return {Matrix}
                     */
                    subMatrixRow(indices, startColumn, endColumn) {
                        if (startColumn === undefined) startColumn = 0;
                        if (endColumn === undefined) endColumn = this.columns - 1;
                        if ((startColumn > endColumn) || (startColumn < 0) || (startColumn >= this.columns) || (endColumn < 0) || (endColumn >= this.columns)) {
                            throw new RangeError('Argument out of range');
                        }

                        var newMatrix = new this.constructor[Symbol.species](indices.length, endColumn - startColumn + 1);
                        for (var i = 0; i < indices.length; i++) {
                            for (var j = startColumn; j <= endColumn; j++) {
                                if (indices[i] < 0 || indices[i] >= this.rows) {
                                    throw new RangeError(`Row index out of range: ${indices[i]}`);
                                }
                                newMatrix.set(i, j - startColumn, this.get(indices[i], j));
                            }
                        }
                        return newMatrix;
                    }

                    /**
                     * Returns a subset of the matrix based on an array of column indices
                     * @param {Array} indices - Array containing the column indices
                     * @param {number} [startRow = 0] - First row index
                     * @param {number} [endRow = this.rows-1] - Last row index
                     * @return {Matrix}
                     */
                    subMatrixColumn(indices, startRow, endRow) {
                        if (startRow === undefined) startRow = 0;
                        if (endRow === undefined) endRow = this.rows - 1;
                        if ((startRow > endRow) || (startRow < 0) || (startRow >= this.rows) || (endRow < 0) || (endRow >= this.rows)) {
                            throw new RangeError('Argument out of range');
                        }

                        var newMatrix = new this.constructor[Symbol.species](endRow - startRow + 1, indices.length);
                        for (var i = 0; i < indices.length; i++) {
                            for (var j = startRow; j <= endRow; j++) {
                                if (indices[i] < 0 || indices[i] >= this.columns) {
                                    throw new RangeError(`Column index out of range: ${indices[i]}`);
                                }
                                newMatrix.set(j - startRow, i, this.get(j, indices[i]));
                            }
                        }
                        return newMatrix;
                    }

                    /**
                     * Set a part of the matrix to the given sub-matrix
                     * @param {Matrix|Array< Array >} matrix - The source matrix from which to extract values.
                     * @param {number} startRow - The index of the first row to set
                     * @param {number} startColumn - The index of the first column to set
                     * @return {Matrix}
                     */
                    setSubMatrix(matrix, startRow, startColumn) {
                        matrix = this.constructor.checkMatrix(matrix);
                        var endRow = startRow + matrix.rows - 1;
                        var endColumn = startColumn + matrix.columns - 1;
                        checkRange(this, startRow, endRow, startColumn, endColumn);
                        for (var i = 0; i < matrix.rows; i++) {
                            for (var j = 0; j < matrix.columns; j++) {
                                this[startRow + i][startColumn + j] = matrix.get(i, j);
                            }
                        }
                        return this;
                    }

                    /**
                     * Return a new matrix based on a selection of rows and columns
                     * @param {Array<number>} rowIndices - The row indices to select. Order matters and an index can be more than once.
                     * @param {Array<number>} columnIndices - The column indices to select. Order matters and an index can be use more than once.
                     * @return {Matrix} The new matrix
                     */
                    selection(rowIndices, columnIndices) {
                        var indices = checkIndices(this, rowIndices, columnIndices);
                        var newMatrix = new this.constructor[Symbol.species](rowIndices.length, columnIndices.length);
                        for (var i = 0; i < indices.row.length; i++) {
                            var rowIndex = indices.row[i];
                            for (var j = 0; j < indices.column.length; j++) {
                                var columnIndex = indices.column[j];
                                newMatrix[i][j] = this.get(rowIndex, columnIndex);
                            }
                        }
                        return newMatrix;
                    }

                    /**
                     * Returns the trace of the matrix (sum of the diagonal elements)
                     * @return {number}
                     */
                    trace() {
                        var min = Math.min(this.rows, this.columns);
                        var trace = 0;
                        for (var i = 0; i < min; i++) {
                            trace += this.get(i, i);
                        }
                        return trace;
                    }

                    /*
			 Matrix views
			 */

                    /**
                     * Returns a view of the transposition of the matrix
                     * @return {MatrixTransposeView}
                     */
                    transposeView() {
                        return new MatrixTransposeView(this);
                    }

                    /**
                     * Returns a view of the row vector with the given index
                     * @param {number} row - row index of the vector
                     * @return {MatrixRowView}
                     */
                    rowView(row) {
                        checkRowIndex(this, row);
                        return new MatrixRowView(this, row);
                    }

                    /**
                     * Returns a view of the column vector with the given index
                     * @param {number} column - column index of the vector
                     * @return {MatrixColumnView}
                     */
                    columnView(column) {
                        checkColumnIndex(this, column);
                        return new MatrixColumnView(this, column);
                    }

                    /**
                     * Returns a view of the matrix flipped in the row axis
                     * @return {MatrixFlipRowView}
                     */
                    flipRowView() {
                        return new MatrixFlipRowView(this);
                    }

                    /**
                     * Returns a view of the matrix flipped in the column axis
                     * @return {MatrixFlipColumnView}
                     */
                    flipColumnView() {
                        return new MatrixFlipColumnView(this);
                    }

                    /**
                     * Returns a view of a submatrix giving the index boundaries
                     * @param {number} startRow - first row index of the submatrix
                     * @param {number} endRow - last row index of the submatrix
                     * @param {number} startColumn - first column index of the submatrix
                     * @param {number} endColumn - last column index of the submatrix
                     * @return {MatrixSubView}
                     */
                    subMatrixView(startRow, endRow, startColumn, endColumn) {
                        return new MatrixSubView(this, startRow, endRow, startColumn, endColumn);
                    }

                    /**
                     * Returns a view of the cross of the row indices and the column indices
                     * @example
                     * // resulting vector is [[2], [2]]
                     * var matrix = new Matrix([[1,2,3], [4,5,6]]).selectionView([0, 0], [1])
                     * @param {Array<number>} rowIndices
                     * @param {Array<number>} columnIndices
                     * @return {MatrixSelectionView}
                     */
                    selectionView(rowIndices, columnIndices) {
                        return new MatrixSelectionView(this, rowIndices, columnIndices);
                    }

                    /**
                     * Returns a view of the row indices
                     * @example
                     * // resulting vector is [[1,2,3], [1,2,3]]
                     * var matrix = new Matrix([[1,2,3], [4,5,6]]).rowSelectionView([0, 0])
                     * @param {Array<number>} rowIndices
                     * @return {MatrixRowSelectionView}
                     */
                    rowSelectionView(rowIndices) {
                        return new MatrixRowSelectionView(this, rowIndices);
                    }

                    /**
                     * Returns a view of the column indices
                     * @example
                     * // resulting vector is [[2, 2], [5, 5]]
                     * var matrix = new Matrix([[1,2,3], [4,5,6]]).columnSelectionView([1, 1])
                     * @param {Array<number>} columnIndices
                     * @return {MatrixColumnSelectionView}
                     */
                    columnSelectionView(columnIndices) {
                        return new MatrixColumnSelectionView(this, columnIndices);
                    }


                    /**
                     * Calculates and returns the determinant of a matrix as a Number
                     * @example
                     *   new Matrix([[1,2,3], [4,5,6]]).det()
                     * @return {number}
                     */
                    det() {
                        if (this.isSquare()) {
                            var a, b, c, d;
                            if (this.columns === 2) {
                                // 2 x 2 matrix
                                a = this.get(0, 0);
                                b = this.get(0, 1);
                                c = this.get(1, 0);
                                d = this.get(1, 1);

                                return a * d - (b * c);
                            } else if (this.columns === 3) {
                                // 3 x 3 matrix
                                var subMatrix0, subMatrix1, subMatrix2;
                                subMatrix0 = this.selectionView([1, 2], [1, 2]);
                                subMatrix1 = this.selectionView([1, 2], [0, 2]);
                                subMatrix2 = this.selectionView([1, 2], [0, 1]);
                                a = this.get(0, 0);
                                b = this.get(0, 1);
                                c = this.get(0, 2);

                                return a * subMatrix0.det() - b * subMatrix1.det() + c * subMatrix2.det();
                            } else {
                                // general purpose determinant using the LU decomposition
                                return new LuDecomposition$$1(this).determinant;
                            }
                        } else {
                            throw Error('Determinant can only be calculated for a square matrix.');
                        }
                    }

                    /**
                     * Returns inverse of a matrix if it exists or the pseudoinverse
                     * @param {number} threshold - threshold for taking inverse of singular values (default = 1e-15)
                     * @return {Matrix} the (pseudo)inverted matrix.
                     */
                    pseudoInverse(threshold) {
                        if (threshold === undefined) threshold = Number.EPSILON;
                        var svdSolution = new SingularValueDecomposition$$1(this, {autoTranspose: true});

                        var U = svdSolution.leftSingularVectors;
                        var V = svdSolution.rightSingularVectors;
                        var s = svdSolution.diagonal;

                        for (var i = 0; i < s.length; i++) {
                            if (Math.abs(s[i]) > threshold) {
                                s[i] = 1.0 / s[i];
                            } else {
                                s[i] = 0.0;
                            }
                        }

                        // convert list to diagonal
                        s = this.constructor[Symbol.species].diag(s);
                        return V.mmul(s.mmul(U.transposeView()));
                    }

                    /**
                     * Creates an exact and independent copy of the matrix
                     * @return {Matrix}
                     */
                    clone() {
                        var newMatrix = new this.constructor[Symbol.species](this.rows, this.columns);
                        for (var row = 0; row < this.rows; row++) {
                            for (var column = 0; column < this.columns; column++) {
                                newMatrix.set(row, column, this.get(row, column));
                            }
                        }
                        return newMatrix;
                    }
                }

                Matrix.prototype.klass = 'Matrix';

                function compareNumbers(a, b) {
                    return a - b;
                }

                /*
		 Synonyms
		 */

                Matrix.random = Matrix.rand;
                Matrix.diagonal = Matrix.diag;
                Matrix.prototype.diagonal = Matrix.prototype.diag;
                Matrix.identity = Matrix.eye;
                Matrix.prototype.negate = Matrix.prototype.neg;
                Matrix.prototype.tensorProduct = Matrix.prototype.kroneckerProduct;
                Matrix.prototype.determinant = Matrix.prototype.det;

                /*
		 Add dynamically instance and static methods for mathematical operations
		 */

                var inplaceOperator = `
	(function %name%(value) {
		if (typeof value === 'number') return this.%name%S(value);
		return this.%name%M(value);
	})
	`;

                var inplaceOperatorScalar = `
	(function %name%S(value) {
		for (var i = 0; i < this.rows; i++) {
			for (var j = 0; j < this.columns; j++) {
				this.set(i, j, this.get(i, j) %op% value);
			}
		}
		return this;
	})
	`;

                var inplaceOperatorMatrix = `
	(function %name%M(matrix) {
		matrix = this.constructor.checkMatrix(matrix);
		if (this.rows !== matrix.rows ||
			this.columns !== matrix.columns) {
			throw new RangeError('Matrices dimensions must be equal');
		}
		for (var i = 0; i < this.rows; i++) {
			for (var j = 0; j < this.columns; j++) {
				this.set(i, j, this.get(i, j) %op% matrix.get(i, j));
			}
		}
		return this;
	})
	`;

                var staticOperator = `
	(function %name%(matrix, value) {
		var newMatrix = new this[Symbol.species](matrix);
		return newMatrix.%name%(value);
	})
	`;

                var inplaceMethod = `
	(function %name%() {
		for (var i = 0; i < this.rows; i++) {
			for (var j = 0; j < this.columns; j++) {
				this.set(i, j, %method%(this.get(i, j)));
			}
		}
		return this;
	})
	`;

                var staticMethod = `
	(function %name%(matrix) {
		var newMatrix = new this[Symbol.species](matrix);
		return newMatrix.%name%();
	})
	`;

                var inplaceMethodWithArgs = `
	(function %name%(%args%) {
		for (var i = 0; i < this.rows; i++) {
			for (var j = 0; j < this.columns; j++) {
				this.set(i, j, %method%(this.get(i, j), %args%));
			}
		}
		return this;
	})
	`;

                var staticMethodWithArgs = `
	(function %name%(matrix, %args%) {
		var newMatrix = new this[Symbol.species](matrix);
		return newMatrix.%name%(%args%);
	})
	`;


                var inplaceMethodWithOneArgScalar = `
	(function %name%S(value) {
		for (var i = 0; i < this.rows; i++) {
			for (var j = 0; j < this.columns; j++) {
				this.set(i, j, %method%(this.get(i, j), value));
			}
		}
		return this;
	})
	`;
                var inplaceMethodWithOneArgMatrix = `
	(function %name%M(matrix) {
		matrix = this.constructor.checkMatrix(matrix);
		if (this.rows !== matrix.rows ||
			this.columns !== matrix.columns) {
			throw new RangeError('Matrices dimensions must be equal');
		}
		for (var i = 0; i < this.rows; i++) {
			for (var j = 0; j < this.columns; j++) {
				this.set(i, j, %method%(this.get(i, j), matrix.get(i, j)));
			}
		}
		return this;
	})
	`;

                var inplaceMethodWithOneArg = `
	(function %name%(value) {
		if (typeof value === 'number') return this.%name%S(value);
		return this.%name%M(value);
	})
	`;

                var staticMethodWithOneArg = staticMethodWithArgs;

                var operators = [
                    // Arithmetic operators
                    ['+', 'add'],
                    ['-', 'sub', 'subtract'],
                    ['*', 'mul', 'multiply'],
                    ['/', 'div', 'divide'],
                    ['%', 'mod', 'modulus'],
                    // Bitwise operators
                    ['&', 'and'],
                    ['|', 'or'],
                    ['^', 'xor'],
                    ['<<', 'leftShift'],
                    ['>>', 'signPropagatingRightShift'],
                    ['>>>', 'rightShift', 'zeroFillRightShift']
                ];

                var i;
                var eval2 = eval; // eslint-disable-line no-eval
                for (var operator of operators) {
                    var inplaceOp = eval2(fillTemplateFunction(inplaceOperator, {name: operator[1], op: operator[0]}));
                    var inplaceOpS = eval2(fillTemplateFunction(inplaceOperatorScalar, {
                        name: `${operator[1]}S`,
                        op: operator[0]
                    }));
                    var inplaceOpM = eval2(fillTemplateFunction(inplaceOperatorMatrix, {
                        name: `${operator[1]}M`,
                        op: operator[0]
                    }));
                    var staticOp = eval2(fillTemplateFunction(staticOperator, {name: operator[1]}));
                    for (i = 1; i < operator.length; i++) {
                        Matrix.prototype[operator[i]] = inplaceOp;
                        Matrix.prototype[`${operator[i]}S`] = inplaceOpS;
                        Matrix.prototype[`${operator[i]}M`] = inplaceOpM;
                        Matrix[operator[i]] = staticOp;
                    }
                }

                var methods = [['~', 'not']];

                [
                    'abs', 'acos', 'acosh', 'asin', 'asinh', 'atan', 'atanh', 'cbrt', 'ceil',
                    'clz32', 'cos', 'cosh', 'exp', 'expm1', 'floor', 'fround', 'log', 'log1p',
                    'log10', 'log2', 'round', 'sign', 'sin', 'sinh', 'sqrt', 'tan', 'tanh', 'trunc'
                ].forEach(function (mathMethod) {
                    methods.push([`Math.${mathMethod}`, mathMethod]);
                });

                for (var method of methods) {
                    var inplaceMeth = eval2(fillTemplateFunction(inplaceMethod, {name: method[1], method: method[0]}));
                    var staticMeth = eval2(fillTemplateFunction(staticMethod, {name: method[1]}));
                    for (i = 1; i < method.length; i++) {
                        Matrix.prototype[method[i]] = inplaceMeth;
                        Matrix[method[i]] = staticMeth;
                    }
                }

                var methodsWithArgs = [['Math.pow', 1, 'pow']];

                for (var methodWithArg of methodsWithArgs) {
                    var args = 'arg0';
                    for (i = 1; i < methodWithArg[1]; i++) {
                        args += `, arg${i}`;
                    }
                    if (methodWithArg[1] !== 1) {
                        var inplaceMethWithArgs = eval2(fillTemplateFunction(inplaceMethodWithArgs, {
                            name: methodWithArg[2],
                            method: methodWithArg[0],
                            args: args
                        }));
                        var staticMethWithArgs = eval2(fillTemplateFunction(staticMethodWithArgs, {
                            name: methodWithArg[2],
                            args: args
                        }));
                        for (i = 2; i < methodWithArg.length; i++) {
                            Matrix.prototype[methodWithArg[i]] = inplaceMethWithArgs;
                            Matrix[methodWithArg[i]] = staticMethWithArgs;
                        }
                    } else {
                        var tmplVar = {
                            name: methodWithArg[2],
                            args: args,
                            method: methodWithArg[0]
                        };
                        var inplaceMethod2 = eval2(fillTemplateFunction(inplaceMethodWithOneArg, tmplVar));
                        var inplaceMethodS = eval2(fillTemplateFunction(inplaceMethodWithOneArgScalar, tmplVar));
                        var inplaceMethodM = eval2(fillTemplateFunction(inplaceMethodWithOneArgMatrix, tmplVar));
                        var staticMethod2 = eval2(fillTemplateFunction(staticMethodWithOneArg, tmplVar));
                        for (i = 2; i < methodWithArg.length; i++) {
                            Matrix.prototype[methodWithArg[i]] = inplaceMethod2;
                            Matrix.prototype[`${methodWithArg[i]}M`] = inplaceMethodM;
                            Matrix.prototype[`${methodWithArg[i]}S`] = inplaceMethodS;
                            Matrix[methodWithArg[i]] = staticMethod2;
                        }
                    }
                }

                function fillTemplateFunction(template, values) {
                    for (var value in values) {
                        template = template.replace(new RegExp(`%${value}%`, 'g'), values[value]);
                    }
                    return template;
                }

                return Matrix;
            }

            class Matrix extends AbstractMatrix(Array) {
                constructor(nRows, nColumns) {
                    var i;
                    if (arguments.length === 1 && typeof nRows === 'number') {
                        return new Array(nRows);
                    }
                    if (Matrix.isMatrix(nRows)) {
                        return nRows.clone();
                    } else if (Number.isInteger(nRows) && nRows > 0) {
                        // Create an empty matrix
                        super(nRows);
                        if (Number.isInteger(nColumns) && nColumns > 0) {
                            for (i = 0; i < nRows; i++) {
                                this[i] = new Array(nColumns);
                            }
                        } else {
                            throw new TypeError('nColumns must be a positive integer');
                        }
                    } else if (Array.isArray(nRows)) {
                        // Copy the values from the 2D array
                        const matrix = nRows;
                        nRows = matrix.length;
                        nColumns = matrix[0].length;
                        if (typeof nColumns !== 'number' || nColumns === 0) {
                            throw new TypeError(
                                'Data must be a 2D array with at least one element'
                            );
                        }
                        super(nRows);
                        for (i = 0; i < nRows; i++) {
                            if (matrix[i].length !== nColumns) {
                                throw new RangeError('Inconsistent array dimensions');
                            }
                            this[i] = [].concat(matrix[i]);
                        }
                    } else {
                        throw new TypeError(
                            'First argument must be a positive number or an array'
                        );
                    }
                    this.rows = nRows;
                    this.columns = nColumns;
                    return this;
                }

                set(rowIndex, columnIndex, value) {
                    this[rowIndex][columnIndex] = value;
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this[rowIndex][columnIndex];
                }

                /**
                 * Removes a row from the given index
                 * @param {number} index - Row index
                 * @return {Matrix} this
                 */
                removeRow(index) {
                    checkRowIndex(this, index);
                    if (this.rows === 1) {
                        throw new RangeError('A matrix cannot have less than one row');
                    }
                    this.splice(index, 1);
                    this.rows -= 1;
                    return this;
                }

                /**
                 * Adds a row at the given index
                 * @param {number} [index = this.rows] - Row index
                 * @param {Array|Matrix} array - Array or vector
                 * @return {Matrix} this
                 */
                addRow(index, array) {
                    if (array === undefined) {
                        array = index;
                        index = this.rows;
                    }
                    checkRowIndex(this, index, true);
                    array = checkRowVector(this, array, true);
                    this.splice(index, 0, array);
                    this.rows += 1;
                    return this;
                }

                /**
                 * Removes a column from the given index
                 * @param {number} index - Column index
                 * @return {Matrix} this
                 */
                removeColumn(index) {
                    checkColumnIndex(this, index);
                    if (this.columns === 1) {
                        throw new RangeError('A matrix cannot have less than one column');
                    }
                    for (var i = 0; i < this.rows; i++) {
                        this[i].splice(index, 1);
                    }
                    this.columns -= 1;
                    return this;
                }

                /**
                 * Adds a column at the given index
                 * @param {number} [index = this.columns] - Column index
                 * @param {Array|Matrix} array - Array or vector
                 * @return {Matrix} this
                 */
                addColumn(index, array) {
                    if (typeof array === 'undefined') {
                        array = index;
                        index = this.columns;
                    }
                    checkColumnIndex(this, index, true);
                    array = checkColumnVector(this, array);
                    for (var i = 0; i < this.rows; i++) {
                        this[i].splice(index, 0, array[i]);
                    }
                    this.columns += 1;
                    return this;
                }
            }

            class WrapperMatrix1D extends AbstractMatrix() {
                /**
                 * @class WrapperMatrix1D
                 * @param {Array<number>} data
                 * @param {object} [options]
                 * @param {object} [options.rows = 1]
                 */
                constructor(data, options = {}) {
                    const {rows = 1} = options;

                    if (data.length % rows !== 0) {
                        throw new Error('the data length is not divisible by the number of rows');
                    }
                    super();
                    this.rows = rows;
                    this.columns = data.length / rows;
                    this.data = data;
                }

                set(rowIndex, columnIndex, value) {
                    var index = this._calculateIndex(rowIndex, columnIndex);
                    this.data[index] = value;
                    return this;
                }

                get(rowIndex, columnIndex) {
                    var index = this._calculateIndex(rowIndex, columnIndex);
                    return this.data[index];
                }

                _calculateIndex(row, column) {
                    return row * this.columns + column;
                }

                static get [Symbol.species]() {
                    return Matrix;
                }
            }

            class WrapperMatrix2D extends AbstractMatrix() {
                /**
                 * @class WrapperMatrix2D
                 * @param {Array<Array<number>>} data
                 */
                constructor(data) {
                    super();
                    this.data = data;
                    this.rows = data.length;
                    this.columns = data[0].length;
                }

                set(rowIndex, columnIndex, value) {
                    this.data[rowIndex][columnIndex] = value;
                    return this;
                }

                get(rowIndex, columnIndex) {
                    return this.data[rowIndex][columnIndex];
                }

                static get [Symbol.species]() {
                    return Matrix;
                }
            }

            /**
             * @param {Array<Array<number>>|Array<number>} array
             * @param {object} [options]
             * @param {object} [options.rows = 1]
             * @return {WrapperMatrix1D|WrapperMatrix2D}
             */
            function wrap(array, options) {
                if (Array.isArray(array)) {
                    if (array[0] && Array.isArray(array[0])) {
                        return new WrapperMatrix2D(array);
                    } else {
                        return new WrapperMatrix1D(array, options);
                    }
                } else {
                    throw new Error('the argument is not an array');
                }
            }

            /**
             * @class QrDecomposition
             * @link https://github.com/lutzroeder/Mapack/blob/master/Source/QrDecomposition.cs
             * @param {Matrix} value
             */
            class QrDecomposition$$1 {
                constructor(value) {
                    value = WrapperMatrix2D.checkMatrix(value);

                    var qr = value.clone();
                    var m = value.rows;
                    var n = value.columns;
                    var rdiag = new Array(n);
                    var i, j, k, s;

                    for (k = 0; k < n; k++) {
                        var nrm = 0;
                        for (i = k; i < m; i++) {
                            nrm = hypotenuse(nrm, qr.get(i, k));
                        }
                        if (nrm !== 0) {
                            if (qr.get(k, k) < 0) {
                                nrm = -nrm;
                            }
                            for (i = k; i < m; i++) {
                                qr.set(i, k, qr.get(i, k) / nrm);
                            }
                            qr.set(k, k, qr.get(k, k) + 1);
                            for (j = k + 1; j < n; j++) {
                                s = 0;
                                for (i = k; i < m; i++) {
                                    s += qr.get(i, k) * qr.get(i, j);
                                }
                                s = -s / qr.get(k, k);
                                for (i = k; i < m; i++) {
                                    qr.set(i, j, qr.get(i, j) + s * qr.get(i, k));
                                }
                            }
                        }
                        rdiag[k] = -nrm;
                    }

                    this.QR = qr;
                    this.Rdiag = rdiag;
                }

                /**
                 * Solve a problem of least square (Ax=b) by using the QR decomposition. Useful when A is rectangular, but not working when A is singular.
                 * Example : We search to approximate x, with A matrix shape m*n, x vector size n, b vector size m (m > n). We will use :
                 * var qr = QrDecomposition(A);
                 * var x = qr.solve(b);
                 * @param {Matrix} value - Matrix 1D which is the vector b (in the equation Ax = b)
                 * @return {Matrix} - The vector x
                 */
                solve(value) {
                    value = Matrix.checkMatrix(value);

                    var qr = this.QR;
                    var m = qr.rows;

                    if (value.rows !== m) {
                        throw new Error('Matrix row dimensions must agree');
                    }
                    if (!this.isFullRank()) {
                        throw new Error('Matrix is rank deficient');
                    }

                    var count = value.columns;
                    var X = value.clone();
                    var n = qr.columns;
                    var i, j, k, s;

                    for (k = 0; k < n; k++) {
                        for (j = 0; j < count; j++) {
                            s = 0;
                            for (i = k; i < m; i++) {
                                s += qr[i][k] * X[i][j];
                            }
                            s = -s / qr[k][k];
                            for (i = k; i < m; i++) {
                                X[i][j] += s * qr[i][k];
                            }
                        }
                    }
                    for (k = n - 1; k >= 0; k--) {
                        for (j = 0; j < count; j++) {
                            X[k][j] /= this.Rdiag[k];
                        }
                        for (i = 0; i < k; i++) {
                            for (j = 0; j < count; j++) {
                                X[i][j] -= X[k][j] * qr[i][k];
                            }
                        }
                    }

                    return X.subMatrix(0, n - 1, 0, count - 1);
                }

                /**
                 *
                 * @return {boolean}
                 */
                isFullRank() {
                    var columns = this.QR.columns;
                    for (var i = 0; i < columns; i++) {
                        if (this.Rdiag[i] === 0) {
                            return false;
                        }
                    }
                    return true;
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get upperTriangularMatrix() {
                    var qr = this.QR;
                    var n = qr.columns;
                    var X = new Matrix(n, n);
                    var i, j;
                    for (i = 0; i < n; i++) {
                        for (j = 0; j < n; j++) {
                            if (i < j) {
                                X[i][j] = qr[i][j];
                            } else if (i === j) {
                                X[i][j] = this.Rdiag[i];
                            } else {
                                X[i][j] = 0;
                            }
                        }
                    }
                    return X;
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get orthogonalMatrix() {
                    var qr = this.QR;
                    var rows = qr.rows;
                    var columns = qr.columns;
                    var X = new Matrix(rows, columns);
                    var i, j, k, s;

                    for (k = columns - 1; k >= 0; k--) {
                        for (i = 0; i < rows; i++) {
                            X[i][k] = 0;
                        }
                        X[k][k] = 1;
                        for (j = k; j < columns; j++) {
                            if (qr[k][k] !== 0) {
                                s = 0;
                                for (i = k; i < rows; i++) {
                                    s += qr[i][k] * X[i][j];
                                }

                                s = -s / qr[k][k];

                                for (i = k; i < rows; i++) {
                                    X[i][j] += s * qr[i][k];
                                }
                            }
                        }
                    }
                    return X;
                }
            }

            /**
             * Computes the inverse of a Matrix
             * @param {Matrix} matrix
             * @param {boolean} [useSVD=false]
             * @return {Matrix}
             */
            function inverse$$1(matrix, useSVD = false) {
                matrix = WrapperMatrix2D.checkMatrix(matrix);
                if (useSVD) {
                    return new SingularValueDecomposition$$1(matrix).inverse();
                } else {
                    return solve$$1(matrix, Matrix.eye(matrix.rows));
                }
            }

            /**
             *
             * @param {Matrix} leftHandSide
             * @param {Matrix} rightHandSide
             * @param {boolean} [useSVD = false]
             * @return {Matrix}
             */
            function solve$$1(leftHandSide, rightHandSide, useSVD = false) {
                leftHandSide = WrapperMatrix2D.checkMatrix(leftHandSide);
                rightHandSide = WrapperMatrix2D.checkMatrix(rightHandSide);
                if (useSVD) {
                    return new SingularValueDecomposition$$1(leftHandSide).solve(rightHandSide);
                } else {
                    return leftHandSide.isSquare()
                        ? new LuDecomposition$$1(leftHandSide).solve(rightHandSide)
                        : new QrDecomposition$$1(leftHandSide).solve(rightHandSide);
                }
            }

            // function used by rowsDependencies
            function xrange(n, exception) {
                var range = [];
                for (var i = 0; i < n; i++) {
                    if (i !== exception) {
                        range.push(i);
                    }
                }
                return range;
            }

            // function used by rowsDependencies
            function dependenciesOneRow(
                error,
                matrix,
                index,
                thresholdValue = 10e-10,
                thresholdError = 10e-10
            ) {
                if (error > thresholdError) {
                    return new Array(matrix.rows + 1).fill(0);
                } else {
                    var returnArray = matrix.addRow(index, [0]);
                    for (var i = 0; i < returnArray.rows; i++) {
                        if (Math.abs(returnArray.get(i, 0)) < thresholdValue) {
                            returnArray.set(i, 0, 0);
                        }
                    }
                    return returnArray.to1DArray();
                }
            }

            /**
             * Creates a matrix which represents the dependencies between rows.
             * If a row is a linear combination of others rows, the result will be a row with the coefficients of this combination.
             * For example : for A = [[2, 0, 0, 1], [0, 1, 6, 0], [0, 3, 0, 1], [0, 0, 1, 0], [0, 1, 2, 0]], the result will be [[0, 0, 0, 0, 0], [0, 0, 0, 4, 1], [0, 0, 0, 0, 0], [0, 0.25, 0, 0, -0.25], [0, 1, 0, -4, 0]]
             * @param {Matrix} matrix
             * @param {Object} [options] includes thresholdValue and thresholdError.
             * @param {number} [options.thresholdValue = 10e-10] If an absolute value is inferior to this threshold, it will equals zero.
             * @param {number} [options.thresholdError = 10e-10] If the error is inferior to that threshold, the linear combination found is accepted and the row is dependent from other rows.
             * @return {Matrix} the matrix which represents the dependencies between rows.
             */

            function linearDependencies(matrix, options = {}) {
                const {thresholdValue = 10e-10, thresholdError = 10e-10} = options;

                var n = matrix.rows;
                var results = new Matrix(n, n);

                for (var i = 0; i < n; i++) {
                    var b = Matrix.columnVector(matrix.getRow(i));
                    var Abis = matrix.subMatrixRow(xrange(n, i)).transposeView();
                    var svd = new SingularValueDecomposition$$1(Abis);
                    var x = svd.solve(b);
                    var error = max(
                        Matrix.sub(b, Abis.mmul(x))
                            .abs()
                            .to1DArray()
                    );
                    results.setRow(
                        i,
                        dependenciesOneRow(error, x, i, thresholdValue, thresholdError)
                    );
                }
                return results;
            }

            /**
             * @class EigenvalueDecomposition
             * @link https://github.com/lutzroeder/Mapack/blob/master/Source/EigenvalueDecomposition.cs
             * @param {Matrix} matrix
             * @param {object} [options]
             * @param {boolean} [options.assumeSymmetric=false]
             */
            class EigenvalueDecomposition$$1 {
                constructor(matrix, options = {}) {
                    const {assumeSymmetric = false} = options;

                    matrix = WrapperMatrix2D.checkMatrix(matrix);
                    if (!matrix.isSquare()) {
                        throw new Error('Matrix is not a square matrix');
                    }

                    var n = matrix.columns;
                    var V = getFilled2DArray(n, n, 0);
                    var d = new Array(n);
                    var e = new Array(n);
                    var value = matrix;
                    var i, j;

                    var isSymmetric = false;
                    if (assumeSymmetric) {
                        isSymmetric = true;
                    } else {
                        isSymmetric = matrix.isSymmetric();
                    }

                    if (isSymmetric) {
                        for (i = 0; i < n; i++) {
                            for (j = 0; j < n; j++) {
                                V[i][j] = value.get(i, j);
                            }
                        }
                        tred2(n, e, d, V);
                        tql2(n, e, d, V);
                    } else {
                        var H = getFilled2DArray(n, n, 0);
                        var ort = new Array(n);
                        for (j = 0; j < n; j++) {
                            for (i = 0; i < n; i++) {
                                H[i][j] = value.get(i, j);
                            }
                        }
                        orthes(n, H, ort, V);
                        hqr2(n, e, d, V, H);
                    }

                    this.n = n;
                    this.e = e;
                    this.d = d;
                    this.V = V;
                }

                /**
                 *
                 * @return {Array<number>}
                 */
                get realEigenvalues() {
                    return this.d;
                }

                /**
                 *
                 * @return {Array<number>}
                 */
                get imaginaryEigenvalues() {
                    return this.e;
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get eigenvectorMatrix() {
                    if (!Matrix.isMatrix(this.V)) {
                        this.V = new Matrix(this.V);
                    }
                    return this.V;
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get diagonalMatrix() {
                    var n = this.n;
                    var e = this.e;
                    var d = this.d;
                    var X = new Matrix(n, n);
                    var i, j;
                    for (i = 0; i < n; i++) {
                        for (j = 0; j < n; j++) {
                            X[i][j] = 0;
                        }
                        X[i][i] = d[i];
                        if (e[i] > 0) {
                            X[i][i + 1] = e[i];
                        } else if (e[i] < 0) {
                            X[i][i - 1] = e[i];
                        }
                    }
                    return X;
                }
            }

            function tred2(n, e, d, V) {
                var f, g, h, i, j, k, hh, scale;

                for (j = 0; j < n; j++) {
                    d[j] = V[n - 1][j];
                }

                for (i = n - 1; i > 0; i--) {
                    scale = 0;
                    h = 0;
                    for (k = 0; k < i; k++) {
                        scale = scale + Math.abs(d[k]);
                    }

                    if (scale === 0) {
                        e[i] = d[i - 1];
                        for (j = 0; j < i; j++) {
                            d[j] = V[i - 1][j];
                            V[i][j] = 0;
                            V[j][i] = 0;
                        }
                    } else {
                        for (k = 0; k < i; k++) {
                            d[k] /= scale;
                            h += d[k] * d[k];
                        }

                        f = d[i - 1];
                        g = Math.sqrt(h);
                        if (f > 0) {
                            g = -g;
                        }

                        e[i] = scale * g;
                        h = h - f * g;
                        d[i - 1] = f - g;
                        for (j = 0; j < i; j++) {
                            e[j] = 0;
                        }

                        for (j = 0; j < i; j++) {
                            f = d[j];
                            V[j][i] = f;
                            g = e[j] + V[j][j] * f;
                            for (k = j + 1; k <= i - 1; k++) {
                                g += V[k][j] * d[k];
                                e[k] += V[k][j] * f;
                            }
                            e[j] = g;
                        }

                        f = 0;
                        for (j = 0; j < i; j++) {
                            e[j] /= h;
                            f += e[j] * d[j];
                        }

                        hh = f / (h + h);
                        for (j = 0; j < i; j++) {
                            e[j] -= hh * d[j];
                        }

                        for (j = 0; j < i; j++) {
                            f = d[j];
                            g = e[j];
                            for (k = j; k <= i - 1; k++) {
                                V[k][j] -= f * e[k] + g * d[k];
                            }
                            d[j] = V[i - 1][j];
                            V[i][j] = 0;
                        }
                    }
                    d[i] = h;
                }

                for (i = 0; i < n - 1; i++) {
                    V[n - 1][i] = V[i][i];
                    V[i][i] = 1;
                    h = d[i + 1];
                    if (h !== 0) {
                        for (k = 0; k <= i; k++) {
                            d[k] = V[k][i + 1] / h;
                        }

                        for (j = 0; j <= i; j++) {
                            g = 0;
                            for (k = 0; k <= i; k++) {
                                g += V[k][i + 1] * V[k][j];
                            }
                            for (k = 0; k <= i; k++) {
                                V[k][j] -= g * d[k];
                            }
                        }
                    }

                    for (k = 0; k <= i; k++) {
                        V[k][i + 1] = 0;
                    }
                }

                for (j = 0; j < n; j++) {
                    d[j] = V[n - 1][j];
                    V[n - 1][j] = 0;
                }

                V[n - 1][n - 1] = 1;
                e[0] = 0;
            }

            function tql2(n, e, d, V) {
                var g, h, i, j, k, l, m, p, r, dl1, c, c2, c3, el1, s, s2;

                for (i = 1; i < n; i++) {
                    e[i - 1] = e[i];
                }

                e[n - 1] = 0;

                var f = 0;
                var tst1 = 0;
                var eps = Number.EPSILON;

                for (l = 0; l < n; l++) {
                    tst1 = Math.max(tst1, Math.abs(d[l]) + Math.abs(e[l]));
                    m = l;
                    while (m < n) {
                        if (Math.abs(e[m]) <= eps * tst1) {
                            break;
                        }
                        m++;
                    }

                    if (m > l) {
                        do {

                            g = d[l];
                            p = (d[l + 1] - g) / (2 * e[l]);
                            r = hypotenuse(p, 1);
                            if (p < 0) {
                                r = -r;
                            }

                            d[l] = e[l] / (p + r);
                            d[l + 1] = e[l] * (p + r);
                            dl1 = d[l + 1];
                            h = g - d[l];
                            for (i = l + 2; i < n; i++) {
                                d[i] -= h;
                            }

                            f = f + h;

                            p = d[m];
                            c = 1;
                            c2 = c;
                            c3 = c;
                            el1 = e[l + 1];
                            s = 0;
                            s2 = 0;
                            for (i = m - 1; i >= l; i--) {
                                c3 = c2;
                                c2 = c;
                                s2 = s;
                                g = c * e[i];
                                h = c * p;
                                r = hypotenuse(p, e[i]);
                                e[i + 1] = s * r;
                                s = e[i] / r;
                                c = p / r;
                                p = c * d[i] - s * g;
                                d[i + 1] = h + s * (c * g + s * d[i]);

                                for (k = 0; k < n; k++) {
                                    h = V[k][i + 1];
                                    V[k][i + 1] = s * V[k][i] + c * h;
                                    V[k][i] = c * V[k][i] - s * h;
                                }
                            }

                            p = -s * s2 * c3 * el1 * e[l] / dl1;
                            e[l] = s * p;
                            d[l] = c * p;
                        } while (Math.abs(e[l]) > eps * tst1);
                    }
                    d[l] = d[l] + f;
                    e[l] = 0;
                }

                for (i = 0; i < n - 1; i++) {
                    k = i;
                    p = d[i];
                    for (j = i + 1; j < n; j++) {
                        if (d[j] < p) {
                            k = j;
                            p = d[j];
                        }
                    }

                    if (k !== i) {
                        d[k] = d[i];
                        d[i] = p;
                        for (j = 0; j < n; j++) {
                            p = V[j][i];
                            V[j][i] = V[j][k];
                            V[j][k] = p;
                        }
                    }
                }
            }

            function orthes(n, H, ort, V) {
                var low = 0;
                var high = n - 1;
                var f, g, h, i, j, m;
                var scale;

                for (m = low + 1; m <= high - 1; m++) {
                    scale = 0;
                    for (i = m; i <= high; i++) {
                        scale = scale + Math.abs(H[i][m - 1]);
                    }

                    if (scale !== 0) {
                        h = 0;
                        for (i = high; i >= m; i--) {
                            ort[i] = H[i][m - 1] / scale;
                            h += ort[i] * ort[i];
                        }

                        g = Math.sqrt(h);
                        if (ort[m] > 0) {
                            g = -g;
                        }

                        h = h - ort[m] * g;
                        ort[m] = ort[m] - g;

                        for (j = m; j < n; j++) {
                            f = 0;
                            for (i = high; i >= m; i--) {
                                f += ort[i] * H[i][j];
                            }

                            f = f / h;
                            for (i = m; i <= high; i++) {
                                H[i][j] -= f * ort[i];
                            }
                        }

                        for (i = 0; i <= high; i++) {
                            f = 0;
                            for (j = high; j >= m; j--) {
                                f += ort[j] * H[i][j];
                            }

                            f = f / h;
                            for (j = m; j <= high; j++) {
                                H[i][j] -= f * ort[j];
                            }
                        }

                        ort[m] = scale * ort[m];
                        H[m][m - 1] = scale * g;
                    }
                }

                for (i = 0; i < n; i++) {
                    for (j = 0; j < n; j++) {
                        V[i][j] = i === j ? 1 : 0;
                    }
                }

                for (m = high - 1; m >= low + 1; m--) {
                    if (H[m][m - 1] !== 0) {
                        for (i = m + 1; i <= high; i++) {
                            ort[i] = H[i][m - 1];
                        }

                        for (j = m; j <= high; j++) {
                            g = 0;
                            for (i = m; i <= high; i++) {
                                g += ort[i] * V[i][j];
                            }

                            g = g / ort[m] / H[m][m - 1];
                            for (i = m; i <= high; i++) {
                                V[i][j] += g * ort[i];
                            }
                        }
                    }
                }
            }

            function hqr2(nn, e, d, V, H) {
                var n = nn - 1;
                var low = 0;
                var high = nn - 1;
                var eps = Number.EPSILON;
                var exshift = 0;
                var norm = 0;
                var p = 0;
                var q = 0;
                var r = 0;
                var s = 0;
                var z = 0;
                var iter = 0;
                var i, j, k, l, m, t, w, x, y;
                var ra, sa, vr, vi;
                var notlast, cdivres;

                for (i = 0; i < nn; i++) {
                    if (i < low || i > high) {
                        d[i] = H[i][i];
                        e[i] = 0;
                    }

                    for (j = Math.max(i - 1, 0); j < nn; j++) {
                        norm = norm + Math.abs(H[i][j]);
                    }
                }

                while (n >= low) {
                    l = n;
                    while (l > low) {
                        s = Math.abs(H[l - 1][l - 1]) + Math.abs(H[l][l]);
                        if (s === 0) {
                            s = norm;
                        }
                        if (Math.abs(H[l][l - 1]) < eps * s) {
                            break;
                        }
                        l--;
                    }

                    if (l === n) {
                        H[n][n] = H[n][n] + exshift;
                        d[n] = H[n][n];
                        e[n] = 0;
                        n--;
                        iter = 0;
                    } else if (l === n - 1) {
                        w = H[n][n - 1] * H[n - 1][n];
                        p = (H[n - 1][n - 1] - H[n][n]) / 2;
                        q = p * p + w;
                        z = Math.sqrt(Math.abs(q));
                        H[n][n] = H[n][n] + exshift;
                        H[n - 1][n - 1] = H[n - 1][n - 1] + exshift;
                        x = H[n][n];

                        if (q >= 0) {
                            z = p >= 0 ? p + z : p - z;
                            d[n - 1] = x + z;
                            d[n] = d[n - 1];
                            if (z !== 0) {
                                d[n] = x - w / z;
                            }
                            e[n - 1] = 0;
                            e[n] = 0;
                            x = H[n][n - 1];
                            s = Math.abs(x) + Math.abs(z);
                            p = x / s;
                            q = z / s;
                            r = Math.sqrt(p * p + q * q);
                            p = p / r;
                            q = q / r;

                            for (j = n - 1; j < nn; j++) {
                                z = H[n - 1][j];
                                H[n - 1][j] = q * z + p * H[n][j];
                                H[n][j] = q * H[n][j] - p * z;
                            }

                            for (i = 0; i <= n; i++) {
                                z = H[i][n - 1];
                                H[i][n - 1] = q * z + p * H[i][n];
                                H[i][n] = q * H[i][n] - p * z;
                            }

                            for (i = low; i <= high; i++) {
                                z = V[i][n - 1];
                                V[i][n - 1] = q * z + p * V[i][n];
                                V[i][n] = q * V[i][n] - p * z;
                            }
                        } else {
                            d[n - 1] = x + p;
                            d[n] = x + p;
                            e[n - 1] = z;
                            e[n] = -z;
                        }

                        n = n - 2;
                        iter = 0;
                    } else {
                        x = H[n][n];
                        y = 0;
                        w = 0;
                        if (l < n) {
                            y = H[n - 1][n - 1];
                            w = H[n][n - 1] * H[n - 1][n];
                        }

                        if (iter === 10) {
                            exshift += x;
                            for (i = low; i <= n; i++) {
                                H[i][i] -= x;
                            }
                            s = Math.abs(H[n][n - 1]) + Math.abs(H[n - 1][n - 2]);
                            x = y = 0.75 * s;
                            w = -0.4375 * s * s;
                        }

                        if (iter === 30) {
                            s = (y - x) / 2;
                            s = s * s + w;
                            if (s > 0) {
                                s = Math.sqrt(s);
                                if (y < x) {
                                    s = -s;
                                }
                                s = x - w / ((y - x) / 2 + s);
                                for (i = low; i <= n; i++) {
                                    H[i][i] -= s;
                                }
                                exshift += s;
                                x = y = w = 0.964;
                            }
                        }

                        iter = iter + 1;

                        m = n - 2;
                        while (m >= l) {
                            z = H[m][m];
                            r = x - z;
                            s = y - z;
                            p = (r * s - w) / H[m + 1][m] + H[m][m + 1];
                            q = H[m + 1][m + 1] - z - r - s;
                            r = H[m + 2][m + 1];
                            s = Math.abs(p) + Math.abs(q) + Math.abs(r);
                            p = p / s;
                            q = q / s;
                            r = r / s;
                            if (m === l) {
                                break;
                            }
                            if (
                                Math.abs(H[m][m - 1]) * (Math.abs(q) + Math.abs(r)) <
                                eps *
                                (Math.abs(p) *
                                    (Math.abs(H[m - 1][m - 1]) +
                                        Math.abs(z) +
                                        Math.abs(H[m + 1][m + 1])))
                            ) {
                                break;
                            }
                            m--;
                        }

                        for (i = m + 2; i <= n; i++) {
                            H[i][i - 2] = 0;
                            if (i > m + 2) {
                                H[i][i - 3] = 0;
                            }
                        }

                        for (k = m; k <= n - 1; k++) {
                            notlast = k !== n - 1;
                            if (k !== m) {
                                p = H[k][k - 1];
                                q = H[k + 1][k - 1];
                                r = notlast ? H[k + 2][k - 1] : 0;
                                x = Math.abs(p) + Math.abs(q) + Math.abs(r);
                                if (x !== 0) {
                                    p = p / x;
                                    q = q / x;
                                    r = r / x;
                                }
                            }

                            if (x === 0) {
                                break;
                            }

                            s = Math.sqrt(p * p + q * q + r * r);
                            if (p < 0) {
                                s = -s;
                            }

                            if (s !== 0) {
                                if (k !== m) {
                                    H[k][k - 1] = -s * x;
                                } else if (l !== m) {
                                    H[k][k - 1] = -H[k][k - 1];
                                }

                                p = p + s;
                                x = p / s;
                                y = q / s;
                                z = r / s;
                                q = q / p;
                                r = r / p;

                                for (j = k; j < nn; j++) {
                                    p = H[k][j] + q * H[k + 1][j];
                                    if (notlast) {
                                        p = p + r * H[k + 2][j];
                                        H[k + 2][j] = H[k + 2][j] - p * z;
                                    }

                                    H[k][j] = H[k][j] - p * x;
                                    H[k + 1][j] = H[k + 1][j] - p * y;
                                }

                                for (i = 0; i <= Math.min(n, k + 3); i++) {
                                    p = x * H[i][k] + y * H[i][k + 1];
                                    if (notlast) {
                                        p = p + z * H[i][k + 2];
                                        H[i][k + 2] = H[i][k + 2] - p * r;
                                    }

                                    H[i][k] = H[i][k] - p;
                                    H[i][k + 1] = H[i][k + 1] - p * q;
                                }

                                for (i = low; i <= high; i++) {
                                    p = x * V[i][k] + y * V[i][k + 1];
                                    if (notlast) {
                                        p = p + z * V[i][k + 2];
                                        V[i][k + 2] = V[i][k + 2] - p * r;
                                    }

                                    V[i][k] = V[i][k] - p;
                                    V[i][k + 1] = V[i][k + 1] - p * q;
                                }
                            }
                        }
                    }
                }

                if (norm === 0) {
                    return;
                }

                for (n = nn - 1; n >= 0; n--) {
                    p = d[n];
                    q = e[n];

                    if (q === 0) {
                        l = n;
                        H[n][n] = 1;
                        for (i = n - 1; i >= 0; i--) {
                            w = H[i][i] - p;
                            r = 0;
                            for (j = l; j <= n; j++) {
                                r = r + H[i][j] * H[j][n];
                            }

                            if (e[i] < 0) {
                                z = w;
                                s = r;
                            } else {
                                l = i;
                                if (e[i] === 0) {
                                    H[i][n] = w !== 0 ? -r / w : -r / (eps * norm);
                                } else {
                                    x = H[i][i + 1];
                                    y = H[i + 1][i];
                                    q = (d[i] - p) * (d[i] - p) + e[i] * e[i];
                                    t = (x * s - z * r) / q;
                                    H[i][n] = t;
                                    H[i + 1][n] =
                                        Math.abs(x) > Math.abs(z) ? (-r - w * t) / x : (-s - y * t) / z;
                                }

                                t = Math.abs(H[i][n]);
                                if (eps * t * t > 1) {
                                    for (j = i; j <= n; j++) {
                                        H[j][n] = H[j][n] / t;
                                    }
                                }
                            }
                        }
                    } else if (q < 0) {
                        l = n - 1;

                        if (Math.abs(H[n][n - 1]) > Math.abs(H[n - 1][n])) {
                            H[n - 1][n - 1] = q / H[n][n - 1];
                            H[n - 1][n] = -(H[n][n] - p) / H[n][n - 1];
                        } else {
                            cdivres = cdiv(0, -H[n - 1][n], H[n - 1][n - 1] - p, q);
                            H[n - 1][n - 1] = cdivres[0];
                            H[n - 1][n] = cdivres[1];
                        }

                        H[n][n - 1] = 0;
                        H[n][n] = 1;
                        for (i = n - 2; i >= 0; i--) {
                            ra = 0;
                            sa = 0;
                            for (j = l; j <= n; j++) {
                                ra = ra + H[i][j] * H[j][n - 1];
                                sa = sa + H[i][j] * H[j][n];
                            }

                            w = H[i][i] - p;

                            if (e[i] < 0) {
                                z = w;
                                r = ra;
                                s = sa;
                            } else {
                                l = i;
                                if (e[i] === 0) {
                                    cdivres = cdiv(-ra, -sa, w, q);
                                    H[i][n - 1] = cdivres[0];
                                    H[i][n] = cdivres[1];
                                } else {
                                    x = H[i][i + 1];
                                    y = H[i + 1][i];
                                    vr = (d[i] - p) * (d[i] - p) + e[i] * e[i] - q * q;
                                    vi = (d[i] - p) * 2 * q;
                                    if (vr === 0 && vi === 0) {
                                        vr =
                                            eps *
                                            norm *
                                            (Math.abs(w) +
                                                Math.abs(q) +
                                                Math.abs(x) +
                                                Math.abs(y) +
                                                Math.abs(z));
                                    }
                                    cdivres = cdiv(
                                        x * r - z * ra + q * sa,
                                        x * s - z * sa - q * ra,
                                        vr,
                                        vi
                                    );
                                    H[i][n - 1] = cdivres[0];
                                    H[i][n] = cdivres[1];
                                    if (Math.abs(x) > Math.abs(z) + Math.abs(q)) {
                                        H[i + 1][n - 1] = (-ra - w * H[i][n - 1] + q * H[i][n]) / x;
                                        H[i + 1][n] = (-sa - w * H[i][n] - q * H[i][n - 1]) / x;
                                    } else {
                                        cdivres = cdiv(-r - y * H[i][n - 1], -s - y * H[i][n], z, q);
                                        H[i + 1][n - 1] = cdivres[0];
                                        H[i + 1][n] = cdivres[1];
                                    }
                                }

                                t = Math.max(Math.abs(H[i][n - 1]), Math.abs(H[i][n]));
                                if (eps * t * t > 1) {
                                    for (j = i; j <= n; j++) {
                                        H[j][n - 1] = H[j][n - 1] / t;
                                        H[j][n] = H[j][n] / t;
                                    }
                                }
                            }
                        }
                    }
                }

                for (i = 0; i < nn; i++) {
                    if (i < low || i > high) {
                        for (j = i; j < nn; j++) {
                            V[i][j] = H[i][j];
                        }
                    }
                }

                for (j = nn - 1; j >= low; j--) {
                    for (i = low; i <= high; i++) {
                        z = 0;
                        for (k = low; k <= Math.min(j, high); k++) {
                            z = z + V[i][k] * H[k][j];
                        }
                        V[i][j] = z;
                    }
                }
            }

            function cdiv(xr, xi, yr, yi) {
                var r, d;
                if (Math.abs(yr) > Math.abs(yi)) {
                    r = yi / yr;
                    d = yr + r * yi;
                    return [(xr + r * xi) / d, (xi - r * xr) / d];
                } else {
                    r = yr / yi;
                    d = yi + r * yr;
                    return [(r * xr + xi) / d, (r * xi - xr) / d];
                }
            }

            /**
             * @class CholeskyDecomposition
             * @link https://github.com/lutzroeder/Mapack/blob/master/Source/CholeskyDecomposition.cs
             * @param {Matrix} value
             */
            class CholeskyDecomposition$$1 {
                constructor(value) {
                    value = WrapperMatrix2D.checkMatrix(value);
                    if (!value.isSymmetric()) {
                        throw new Error('Matrix is not symmetric');
                    }

                    var a = value;
                    var dimension = a.rows;
                    var l = new Matrix(dimension, dimension);
                    var positiveDefinite = true;
                    var i, j, k;

                    for (j = 0; j < dimension; j++) {
                        var Lrowj = l[j];
                        var d = 0;
                        for (k = 0; k < j; k++) {
                            var Lrowk = l[k];
                            var s = 0;
                            for (i = 0; i < k; i++) {
                                s += Lrowk[i] * Lrowj[i];
                            }
                            Lrowj[k] = s = (a.get(j, k) - s) / l[k][k];
                            d = d + s * s;
                        }

                        d = a.get(j, j) - d;

                        positiveDefinite &= d > 0;
                        l[j][j] = Math.sqrt(Math.max(d, 0));
                        for (k = j + 1; k < dimension; k++) {
                            l[j][k] = 0;
                        }
                    }

                    if (!positiveDefinite) {
                        throw new Error('Matrix is not positive definite');
                    }

                    this.L = l;
                }

                /**
                 *
                 * @param {Matrix} value
                 * @return {Matrix}
                 */
                solve(value) {
                    value = WrapperMatrix2D.checkMatrix(value);

                    var l = this.L;
                    var dimension = l.rows;

                    if (value.rows !== dimension) {
                        throw new Error('Matrix dimensions do not match');
                    }

                    var count = value.columns;
                    var B = value.clone();
                    var i, j, k;

                    for (k = 0; k < dimension; k++) {
                        for (j = 0; j < count; j++) {
                            for (i = 0; i < k; i++) {
                                B[k][j] -= B[i][j] * l[k][i];
                            }
                            B[k][j] /= l[k][k];
                        }
                    }

                    for (k = dimension - 1; k >= 0; k--) {
                        for (j = 0; j < count; j++) {
                            for (i = k + 1; i < dimension; i++) {
                                B[k][j] -= B[i][j] * l[i][k];
                            }
                            B[k][j] /= l[k][k];
                        }
                    }

                    return B;
                }

                /**
                 *
                 * @return {Matrix}
                 */
                get lowerTriangularMatrix() {
                    return this.L;
                }
            }

            exports.default = Matrix;
            exports.Matrix = Matrix;
            exports.abstractMatrix = AbstractMatrix;
            exports.wrap = wrap;
            exports.WrapperMatrix2D = WrapperMatrix2D;
            exports.WrapperMatrix1D = WrapperMatrix1D;
            exports.solve = solve$$1;
            exports.inverse = inverse$$1;
            exports.linearDependencies = linearDependencies;
            exports.SingularValueDecomposition = SingularValueDecomposition$$1;
            exports.SVD = SingularValueDecomposition$$1;
            exports.EigenvalueDecomposition = EigenvalueDecomposition$$1;
            exports.EVD = EigenvalueDecomposition$$1;
            exports.CholeskyDecomposition = CholeskyDecomposition$$1;
            exports.CHO = CholeskyDecomposition$$1;
            exports.LuDecomposition = LuDecomposition$$1;
            exports.LU = LuDecomposition$$1;
            exports.QrDecomposition = QrDecomposition$$1;
            exports.QR = QrDecomposition$$1;

        }, {"ml-array-max": 2, "ml-array-rescale": 4}], 6: [function (require, module, exports) {
            'use strict';

            const matrixLib = require('ml-matrix');
            const Matrix = matrixLib.Matrix;
            const EVD = matrixLib.EVD;
            const SVD = matrixLib.SVD;
            const Stat = require('ml-stat/matrix');
            const mean = Stat.mean;
            const stdev = Stat.standardDeviation;

            const defaultOptions = {
                isCovarianceMatrix: false,
                center: true,
                scale: false
            };

            /**
             * Creates new PCA (Principal Component Analysis) from the dataset
             * @param {Matrix} dataset - dataset or covariance matrix
             * @param {Object} options
             * @param {boolean} [options.isCovarianceMatrix=false] - true if the dataset is a covariance matrix
             * @param {boolean} [options.center=true] - should the data be centered (subtract the mean)
             * @param {boolean} [options.scale=false] - should the data be scaled (divide by the standard deviation)
             * */
            class PCA {
                constructor(dataset, options) {
                    if (dataset === true) {
                        const model = options;
                        this.center = model.center;
                        this.scale = model.scale;
                        this.means = model.means;
                        this.stdevs = model.stdevs;
                        this.U = Matrix.checkMatrix(model.U);
                        this.S = model.S;
                        return;
                    }

                    options = Object.assign({}, defaultOptions, options);

                    this.center = false;
                    this.scale = false;
                    this.means = null;
                    this.stdevs = null;
                    this.adjustDataset = null;

                    if (options.isCovarianceMatrix) { // user provided a covariance matrix instead of dataset
                        this._computeFromCovarianceMatrix(dataset);
                        return;
                    }

                    var useCovarianceMatrix;
                    if (typeof options.useCovarianceMatrix === 'boolean') {
                        useCovarianceMatrix = options.useCovarianceMatrix;
                    } else {
                        useCovarianceMatrix = dataset.length > dataset[0].length;
                    }

                    if (useCovarianceMatrix) { // user provided a dataset but wants us to compute and use the covariance matrix
                        dataset = this._adjust(dataset, options);
                        const covarianceMatrix = dataset.transposeView().mmul(dataset).div(dataset.rows - 1);
                        this._computeFromCovarianceMatrix(covarianceMatrix);
                    } else {
                        dataset = this._adjust(dataset, options);
                        var svd = new SVD(dataset, {
                            computeLeftSingularVectors: false,
                            computeRightSingularVectors: true,
                            autoTranspose: true
                        });

                        this.U = svd.rightSingularVectors;

                        const singularValues = svd.diagonal;
                        const eigenvalues = new Array(singularValues.length);
                        for (var i = 0; i < singularValues.length; i++) {
                            eigenvalues[i] = singularValues[i] * singularValues[i] / (dataset.length - 1);
                        }
                        this.S = eigenvalues;
                    }

                    this.adjustDataset = dataset;

                }

                /**
                 * Load a PCA model from JSON
                 * @param {Object} model
                 * @return {PCA}
                 */
                static load(model) {
                    if (model.name !== 'PCA')
                        throw new RangeError('Invalid model: ' + model.name);
                    return new PCA(true, model);
                }


                /**
                 * Project the dataset into the PCA space
                 * @param {Matrix} dataset
                 * @param {Object} options
                 * @return {Matrix} dataset projected in the PCA space
                 */
                predict(dataset, options = {}) {
                    const {
                        nComponents = this.U.columns
                    } = options;

                    dataset = new Matrix(dataset);
                    if (this.center) {
                        dataset.subRowVector(this.means);
                        if (this.scale) {
                            dataset.divRowVector(this.stdevs);
                        }
                    }

                    var predictions = dataset.mmul(this.U);
                    return predictions.subMatrix(0, predictions.rows - 1, 0, nComponents - 1);
                }

                /**
                 * Returns the proportion of variance for each component
                 * @return {[number]}
                 */
                getExplainedVariance() {
                    var sum = 0;
                    for (var i = 0; i < this.S.length; i++) {
                        sum += this.S[i];
                    }
                    return this.S.map(value => value / sum);
                }

                /**
                 * Returns the cumulative proportion of variance
                 * @return {[number]}
                 */
                getCumulativeVariance() {
                    var explained = this.getExplainedVariance();
                    for (var i = 1; i < explained.length; i++) {
                        explained[i] += explained[i - 1];
                    }
                    return explained;
                }

                /**
                 * Returns the Eigenvectors of the covariance matrix
                 * @returns {Matrix}
                 */
                getEigenvectors() {
                    return this.U;
                }

                /**
                 * Returns the Eigenvalues (on the diagonal)
                 * @returns {[number]}
                 */
                getEigenvalues() {
                    return this.S;
                }

                /**
                 * Returns the standard deviations of the principal components
                 * @returns {[number]}
                 */
                getStandardDeviations() {
                    return this.S.map(x => Math.sqrt(x));
                }

                /**
                 * Returns the loadings matrix
                 * @return {Matrix}
                 */
                getLoadings() {
                    return this.U.transpose();
                }

                /**
                 * Returns the adjust dataset
                 * @return {Matrix}
                 */
                getAdjustDataset() {
                    return this.adjustDataset
                }

                /**
                 * Export the current model to a JSON object
                 * @return {Object} model
                 */
                toJSON() {
                    return {
                        name: 'PCA',
                        center: this.center,
                        scale: this.scale,
                        means: this.means,
                        stdevs: this.stdevs,
                        U: this.U,
                        S: this.S,
                    };
                }

                _adjust(dataset, options) {
                    this.center = !!options.center;
                    this.scale = !!options.scale;

                    dataset = new Matrix(dataset);

                    if (this.center) {
                        const means = mean(dataset);
                        const stdevs = this.scale ? stdev(dataset, means, true) : null;
                        this.means = means;
                        dataset.subRowVector(means);
                        if (this.scale) {
                            for (var i = 0; i < stdevs.length; i++) {
                                if (stdevs[i] === 0) {
                                    throw new RangeError('Cannot scale the dataset (standard deviation is zero at index ' + i);
                                }
                            }
                            this.stdevs = stdevs;
                            dataset.divRowVector(stdevs);
                        }
                    }

                    return dataset;
                }

                _computeFromCovarianceMatrix(dataset) {
                    const evd = new EVD(dataset, {assumeSymmetric: true});
                    this.U = evd.eigenvectorMatrix;
                    for (var i = 0; i < this.U.length; i++) {
                        this.U[i].reverse();
                    }
                    this.S = evd.realEigenvalues.reverse();
                }
            }

            module.exports = PCA;

        }, {"ml-matrix": 5, "ml-stat/matrix": 8}], 7: [function (require, module, exports) {
            'use strict';

            function compareNumbers(a, b) {
                return a - b;
            }

            /**
             * Computes the sum of the given values
             * @param {Array} values
             * @returns {number}
             */
            exports.sum = function sum(values) {
                var sum = 0;
                for (var i = 0; i < values.length; i++) {
                    sum += values[i];
                }
                return sum;
            };

            /**
             * Computes the maximum of the given values
             * @param {Array} values
             * @returns {number}
             */
            exports.max = function max(values) {
                var max = values[0];
                var l = values.length;
                for (var i = 1; i < l; i++) {
                    if (values[i] > max) max = values[i];
                }
                return max;
            };

            /**
             * Computes the minimum of the given values
             * @param {Array} values
             * @returns {number}
             */
            exports.min = function min(values) {
                var min = values[0];
                var l = values.length;
                for (var i = 1; i < l; i++) {
                    if (values[i] < min) min = values[i];
                }
                return min;
            };

            /**
             * Computes the min and max of the given values
             * @param {Array} values
             * @returns {{min: number, max: number}}
             */
            exports.minMax = function minMax(values) {
                var min = values[0];
                var max = values[0];
                var l = values.length;
                for (var i = 1; i < l; i++) {
                    if (values[i] < min) min = values[i];
                    if (values[i] > max) max = values[i];
                }
                return {
                    min: min,
                    max: max
                };
            };

            /**
             * Computes the arithmetic mean of the given values
             * @param {Array} values
             * @returns {number}
             */
            exports.arithmeticMean = function arithmeticMean(values) {
                var sum = 0;
                var l = values.length;
                for (var i = 0; i < l; i++) {
                    sum += values[i];
                }
                return sum / l;
            };

            /**
             * {@link arithmeticMean}
             */
            exports.mean = exports.arithmeticMean;

            /**
             * Computes the geometric mean of the given values
             * @param {Array} values
             * @returns {number}
             */
            exports.geometricMean = function geometricMean(values) {
                var mul = 1;
                var l = values.length;
                for (var i = 0; i < l; i++) {
                    mul *= values[i];
                }
                return Math.pow(mul, 1 / l);
            };

            /**
             * Computes the mean of the log of the given values
             * If the return value is exponentiated, it gives the same result as the
             * geometric mean.
             * @param {Array} values
             * @returns {number}
             */
            exports.logMean = function logMean(values) {
                var lnsum = 0;
                var l = values.length;
                for (var i = 0; i < l; i++) {
                    lnsum += Math.log(values[i]);
                }
                return lnsum / l;
            };

            /**
             * Computes the weighted grand mean for a list of means and sample sizes
             * @param {Array} means - Mean values for each set of samples
             * @param {Array} samples - Number of original values for each set of samples
             * @returns {number}
             */
            exports.grandMean = function grandMean(means, samples) {
                var sum = 0;
                var n = 0;
                var l = means.length;
                for (var i = 0; i < l; i++) {
                    sum += samples[i] * means[i];
                    n += samples[i];
                }
                return sum / n;
            };

            /**
             * Computes the truncated mean of the given values using a given percentage
             * @param {Array} values
             * @param {number} percent - The percentage of values to keep (range: [0,1])
             * @param {boolean} [alreadySorted=false]
             * @returns {number}
             */
            exports.truncatedMean = function truncatedMean(values, percent, alreadySorted) {
                if (alreadySorted === undefined) alreadySorted = false;
                if (!alreadySorted) {
                    values = [].concat(values).sort(compareNumbers);
                }
                var l = values.length;
                var k = Math.floor(l * percent);
                var sum = 0;
                for (var i = k; i < (l - k); i++) {
                    sum += values[i];
                }
                return sum / (l - 2 * k);
            };

            /**
             * Computes the harmonic mean of the given values
             * @param {Array} values
             * @returns {number}
             */
            exports.harmonicMean = function harmonicMean(values) {
                var sum = 0;
                var l = values.length;
                for (var i = 0; i < l; i++) {
                    if (values[i] === 0) {
                        throw new RangeError('value at index ' + i + 'is zero');
                    }
                    sum += 1 / values[i];
                }
                return l / sum;
            };

            /**
             * Computes the contraharmonic mean of the given values
             * @param {Array} values
             * @returns {number}
             */
            exports.contraHarmonicMean = function contraHarmonicMean(values) {
                var r1 = 0;
                var r2 = 0;
                var l = values.length;
                for (var i = 0; i < l; i++) {
                    r1 += values[i] * values[i];
                    r2 += values[i];
                }
                if (r2 < 0) {
                    throw new RangeError('sum of values is negative');
                }
                return r1 / r2;
            };

            /**
             * Computes the median of the given values
             * @param {Array} values
             * @param {boolean} [alreadySorted=false]
             * @returns {number}
             */
            exports.median = function median(values, alreadySorted) {
                if (alreadySorted === undefined) alreadySorted = false;
                if (!alreadySorted) {
                    values = [].concat(values).sort(compareNumbers);
                }
                var l = values.length;
                var half = Math.floor(l / 2);
                if (l % 2 === 0) {
                    return (values[half - 1] + values[half]) * 0.5;
                } else {
                    return values[half];
                }
            };

            /**
             * Computes the variance of the given values
             * @param {Array} values
             * @param {boolean} [unbiased=true] - if true, divide by (n-1); if false, divide by n.
             * @returns {number}
             */
            exports.variance = function variance(values, unbiased) {
                if (unbiased === undefined) unbiased = true;
                var theMean = exports.mean(values);
                var theVariance = 0;
                var l = values.length;

                for (var i = 0; i < l; i++) {
                    var x = values[i] - theMean;
                    theVariance += x * x;
                }

                if (unbiased) {
                    return theVariance / (l - 1);
                } else {
                    return theVariance / l;
                }
            };

            /**
             * Computes the standard deviation of the given values
             * @param {Array} values
             * @param {boolean} [unbiased=true] - if true, divide by (n-1); if false, divide by n.
             * @returns {number}
             */
            exports.standardDeviation = function standardDeviation(values, unbiased) {
                return Math.sqrt(exports.variance(values, unbiased));
            };

            exports.standardError = function standardError(values) {
                return exports.standardDeviation(values) / Math.sqrt(values.length);
            };

            /**
             * IEEE Transactions on biomedical engineering, vol. 52, no. 1, january 2005, p. 76-
             * Calculate the standard deviation via the Median of the absolute deviation
             *  The formula for the standard deviation only holds for Gaussian random variables.
             * @returns {{mean: number, stdev: number}}
             */
            exports.robustMeanAndStdev = function robustMeanAndStdev(y) {
                var mean = 0, stdev = 0;
                var length = y.length, i = 0;
                for (i = 0; i < length; i++) {
                    mean += y[i];
                }
                mean /= length;
                var averageDeviations = new Array(length);
                for (i = 0; i < length; i++)
                    averageDeviations[i] = Math.abs(y[i] - mean);
                averageDeviations.sort(compareNumbers);
                if (length % 2 === 1) {
                    stdev = averageDeviations[(length - 1) / 2] / 0.6745;
                } else {
                    stdev = 0.5 * (averageDeviations[length / 2] + averageDeviations[length / 2 - 1]) / 0.6745;
                }

                return {
                    mean: mean,
                    stdev: stdev
                };
            };

            exports.quartiles = function quartiles(values, alreadySorted) {
                if (typeof (alreadySorted) === 'undefined') alreadySorted = false;
                if (!alreadySorted) {
                    values = [].concat(values).sort(compareNumbers);
                }

                var quart = values.length / 4;
                var q1 = values[Math.ceil(quart) - 1];
                var q2 = exports.median(values, true);
                var q3 = values[Math.ceil(quart * 3) - 1];

                return {q1: q1, q2: q2, q3: q3};
            };

            exports.pooledStandardDeviation = function pooledStandardDeviation(samples, unbiased) {
                return Math.sqrt(exports.pooledVariance(samples, unbiased));
            };

            exports.pooledVariance = function pooledVariance(samples, unbiased) {
                if (typeof (unbiased) === 'undefined') unbiased = true;
                var sum = 0;
                var length = 0, l = samples.length;
                for (var i = 0; i < l; i++) {
                    var values = samples[i];
                    var vari = exports.variance(values);

                    sum += (values.length - 1) * vari;

                    if (unbiased)
                        length += values.length - 1;
                    else
                        length += values.length;
                }
                return sum / length;
            };

            exports.mode = function mode(values) {
                var l = values.length,
                    itemCount = new Array(l),
                    i;
                for (i = 0; i < l; i++) {
                    itemCount[i] = 0;
                }
                var itemArray = new Array(l);
                var count = 0;

                for (i = 0; i < l; i++) {
                    var index = itemArray.indexOf(values[i]);
                    if (index >= 0)
                        itemCount[index]++;
                    else {
                        itemArray[count] = values[i];
                        itemCount[count] = 1;
                        count++;
                    }
                }

                var maxValue = 0, maxIndex = 0;
                for (i = 0; i < count; i++) {
                    if (itemCount[i] > maxValue) {
                        maxValue = itemCount[i];
                        maxIndex = i;
                    }
                }

                return itemArray[maxIndex];
            };

            exports.covariance = function covariance(vector1, vector2, unbiased) {
                if (typeof (unbiased) === 'undefined') unbiased = true;
                var mean1 = exports.mean(vector1);
                var mean2 = exports.mean(vector2);

                if (vector1.length !== vector2.length)
                    throw 'Vectors do not have the same dimensions';

                var cov = 0, l = vector1.length;
                for (var i = 0; i < l; i++) {
                    var x = vector1[i] - mean1;
                    var y = vector2[i] - mean2;
                    cov += x * y;
                }

                if (unbiased)
                    return cov / (l - 1);
                else
                    return cov / l;
            };

            exports.skewness = function skewness(values, unbiased) {
                if (typeof (unbiased) === 'undefined') unbiased = true;
                var theMean = exports.mean(values);

                var s2 = 0, s3 = 0, l = values.length;
                for (var i = 0; i < l; i++) {
                    var dev = values[i] - theMean;
                    s2 += dev * dev;
                    s3 += dev * dev * dev;
                }
                var m2 = s2 / l;
                var m3 = s3 / l;

                var g = m3 / (Math.pow(m2, 3 / 2.0));
                if (unbiased) {
                    var a = Math.sqrt(l * (l - 1));
                    var b = l - 2;
                    return (a / b) * g;
                } else {
                    return g;
                }
            };

            exports.kurtosis = function kurtosis(values, unbiased) {
                if (typeof (unbiased) === 'undefined') unbiased = true;
                var theMean = exports.mean(values);
                var n = values.length, s2 = 0, s4 = 0;

                for (var i = 0; i < n; i++) {
                    var dev = values[i] - theMean;
                    s2 += dev * dev;
                    s4 += dev * dev * dev * dev;
                }
                var m2 = s2 / n;
                var m4 = s4 / n;

                if (unbiased) {
                    var v = s2 / (n - 1);
                    var a = (n * (n + 1)) / ((n - 1) * (n - 2) * (n - 3));
                    var b = s4 / (v * v);
                    var c = ((n - 1) * (n - 1)) / ((n - 2) * (n - 3));

                    return a * b - 3 * c;
                } else {
                    return m4 / (m2 * m2) - 3;
                }
            };

            exports.entropy = function entropy(values, eps) {
                if (typeof (eps) === 'undefined') eps = 0;
                var sum = 0, l = values.length;
                for (var i = 0; i < l; i++)
                    sum += values[i] * Math.log(values[i] + eps);
                return -sum;
            };

            exports.weightedMean = function weightedMean(values, weights) {
                var sum = 0, l = values.length;
                for (var i = 0; i < l; i++)
                    sum += values[i] * weights[i];
                return sum;
            };

            exports.weightedStandardDeviation = function weightedStandardDeviation(values, weights) {
                return Math.sqrt(exports.weightedVariance(values, weights));
            };

            exports.weightedVariance = function weightedVariance(values, weights) {
                var theMean = exports.weightedMean(values, weights);
                var vari = 0, l = values.length;
                var a = 0, b = 0;

                for (var i = 0; i < l; i++) {
                    var z = values[i] - theMean;
                    var w = weights[i];

                    vari += w * (z * z);
                    b += w;
                    a += w * w;
                }

                return vari * (b / (b * b - a));
            };

            exports.center = function center(values, inPlace) {
                if (typeof (inPlace) === 'undefined') inPlace = false;

                var result = values;
                if (!inPlace)
                    result = [].concat(values);

                var theMean = exports.mean(result), l = result.length;
                for (var i = 0; i < l; i++)
                    result[i] -= theMean;
            };

            exports.standardize = function standardize(values, standardDev, inPlace) {
                if (typeof (standardDev) === 'undefined') standardDev = exports.standardDeviation(values);
                if (typeof (inPlace) === 'undefined') inPlace = false;
                var l = values.length;
                var result = inPlace ? values : new Array(l);
                for (var i = 0; i < l; i++)
                    result[i] = values[i] / standardDev;
                return result;
            };

            exports.cumulativeSum = function cumulativeSum(array) {
                var l = array.length;
                var result = new Array(l);
                result[0] = array[0];
                for (var i = 1; i < l; i++)
                    result[i] = result[i - 1] + array[i];
                return result;
            };

        }, {}], 8: [function (require, module, exports) {
            'use strict';

            var arrayStat = require('./array');

            function compareNumbers(a, b) {
                return a - b;
            }

            exports.max = function max(matrix) {
                var max = -Infinity;
                for (var i = 0; i < matrix.length; i++) {
                    for (var j = 0; j < matrix[i].length; j++) {
                        if (matrix[i][j] > max) max = matrix[i][j];
                    }
                }
                return max;
            };

            exports.min = function min(matrix) {
                var min = Infinity;
                for (var i = 0; i < matrix.length; i++) {
                    for (var j = 0; j < matrix[i].length; j++) {
                        if (matrix[i][j] < min) min = matrix[i][j];
                    }
                }
                return min;
            };

            exports.minMax = function minMax(matrix) {
                var min = Infinity;
                var max = -Infinity;
                for (var i = 0; i < matrix.length; i++) {
                    for (var j = 0; j < matrix[i].length; j++) {
                        if (matrix[i][j] < min) min = matrix[i][j];
                        if (matrix[i][j] > max) max = matrix[i][j];
                    }
                }
                return {
                    min: min,
                    max: max
                };
            };

            exports.entropy = function entropy(matrix, eps) {
                if (typeof (eps) === 'undefined') {
                    eps = 0;
                }
                var sum = 0,
                    l1 = matrix.length,
                    l2 = matrix[0].length;
                for (var i = 0; i < l1; i++) {
                    for (var j = 0; j < l2; j++) {
                        sum += matrix[i][j] * Math.log(matrix[i][j] + eps);
                    }
                }
                return -sum;
            };

            exports.mean = function mean(matrix, dimension) {
                if (typeof (dimension) === 'undefined') {
                    dimension = 0;
                }
                var rows = matrix.length,
                    cols = matrix[0].length,
                    theMean, N, i, j;

                if (dimension === -1) {
                    theMean = [0];
                    N = rows * cols;
                    for (i = 0; i < rows; i++) {
                        for (j = 0; j < cols; j++) {
                            theMean[0] += matrix[i][j];
                        }
                    }
                    theMean[0] /= N;
                } else if (dimension === 0) {
                    theMean = new Array(cols);
                    N = rows;
                    for (j = 0; j < cols; j++) {
                        theMean[j] = 0;
                        for (i = 0; i < rows; i++) {
                            theMean[j] += matrix[i][j];
                        }
                        theMean[j] /= N;
                    }
                } else if (dimension === 1) {
                    theMean = new Array(rows);
                    N = cols;
                    for (j = 0; j < rows; j++) {
                        theMean[j] = 0;
                        for (i = 0; i < cols; i++) {
                            theMean[j] += matrix[j][i];
                        }
                        theMean[j] /= N;
                    }
                } else {
                    throw new Error('Invalid dimension');
                }
                return theMean;
            };

            exports.sum = function sum(matrix, dimension) {
                if (typeof (dimension) === 'undefined') {
                    dimension = 0;
                }
                var rows = matrix.length,
                    cols = matrix[0].length,
                    theSum, i, j;

                if (dimension === -1) {
                    theSum = [0];
                    for (i = 0; i < rows; i++) {
                        for (j = 0; j < cols; j++) {
                            theSum[0] += matrix[i][j];
                        }
                    }
                } else if (dimension === 0) {
                    theSum = new Array(cols);
                    for (j = 0; j < cols; j++) {
                        theSum[j] = 0;
                        for (i = 0; i < rows; i++) {
                            theSum[j] += matrix[i][j];
                        }
                    }
                } else if (dimension === 1) {
                    theSum = new Array(rows);
                    for (j = 0; j < rows; j++) {
                        theSum[j] = 0;
                        for (i = 0; i < cols; i++) {
                            theSum[j] += matrix[j][i];
                        }
                    }
                } else {
                    throw new Error('Invalid dimension');
                }
                return theSum;
            };

            exports.product = function product(matrix, dimension) {
                if (typeof (dimension) === 'undefined') {
                    dimension = 0;
                }
                var rows = matrix.length,
                    cols = matrix[0].length,
                    theProduct, i, j;

                if (dimension === -1) {
                    theProduct = [1];
                    for (i = 0; i < rows; i++) {
                        for (j = 0; j < cols; j++) {
                            theProduct[0] *= matrix[i][j];
                        }
                    }
                } else if (dimension === 0) {
                    theProduct = new Array(cols);
                    for (j = 0; j < cols; j++) {
                        theProduct[j] = 1;
                        for (i = 0; i < rows; i++) {
                            theProduct[j] *= matrix[i][j];
                        }
                    }
                } else if (dimension === 1) {
                    theProduct = new Array(rows);
                    for (j = 0; j < rows; j++) {
                        theProduct[j] = 1;
                        for (i = 0; i < cols; i++) {
                            theProduct[j] *= matrix[j][i];
                        }
                    }
                } else {
                    throw new Error('Invalid dimension');
                }
                return theProduct;
            };

            exports.standardDeviation = function standardDeviation(matrix, means, unbiased) {
                var vari = exports.variance(matrix, means, unbiased), l = vari.length;
                for (var i = 0; i < l; i++) {
                    vari[i] = Math.sqrt(vari[i]);
                }
                return vari;
            };

            exports.variance = function variance(matrix, means, unbiased) {
                if (typeof (unbiased) === 'undefined') {
                    unbiased = true;
                }
                means = means || exports.mean(matrix);
                var rows = matrix.length;
                if (rows === 0) return [];
                var cols = matrix[0].length;
                var vari = new Array(cols);

                for (var j = 0; j < cols; j++) {
                    var sum1 = 0, sum2 = 0, x = 0;
                    for (var i = 0; i < rows; i++) {
                        x = matrix[i][j] - means[j];
                        sum1 += x;
                        sum2 += x * x;
                    }
                    if (unbiased) {
                        vari[j] = (sum2 - ((sum1 * sum1) / rows)) / (rows - 1);
                    } else {
                        vari[j] = (sum2 - ((sum1 * sum1) / rows)) / rows;
                    }
                }
                return vari;
            };

            exports.median = function median(matrix) {
                var rows = matrix.length, cols = matrix[0].length;
                var medians = new Array(cols);

                for (var i = 0; i < cols; i++) {
                    var data = new Array(rows);
                    for (var j = 0; j < rows; j++) {
                        data[j] = matrix[j][i];
                    }
                    data.sort(compareNumbers);
                    var N = data.length;
                    if (N % 2 === 0) {
                        medians[i] = (data[N / 2] + data[(N / 2) - 1]) * 0.5;
                    } else {
                        medians[i] = data[Math.floor(N / 2)];
                    }
                }
                return medians;
            };

            exports.mode = function mode(matrix) {
                var rows = matrix.length,
                    cols = matrix[0].length,
                    modes = new Array(cols),
                    i, j;
                for (i = 0; i < cols; i++) {
                    var itemCount = new Array(rows);
                    for (var k = 0; k < rows; k++) {
                        itemCount[k] = 0;
                    }
                    var itemArray = new Array(rows);
                    var count = 0;

                    for (j = 0; j < rows; j++) {
                        var index = itemArray.indexOf(matrix[j][i]);
                        if (index >= 0) {
                            itemCount[index]++;
                        } else {
                            itemArray[count] = matrix[j][i];
                            itemCount[count] = 1;
                            count++;
                        }
                    }

                    var maxValue = 0, maxIndex = 0;
                    for (j = 0; j < count; j++) {
                        if (itemCount[j] > maxValue) {
                            maxValue = itemCount[j];
                            maxIndex = j;
                        }
                    }

                    modes[i] = itemArray[maxIndex];
                }
                return modes;
            };

            exports.skewness = function skewness(matrix, unbiased) {
                if (typeof (unbiased) === 'undefined') unbiased = true;
                var means = exports.mean(matrix);
                var n = matrix.length, l = means.length;
                var skew = new Array(l);

                for (var j = 0; j < l; j++) {
                    var s2 = 0, s3 = 0;
                    for (var i = 0; i < n; i++) {
                        var dev = matrix[i][j] - means[j];
                        s2 += dev * dev;
                        s3 += dev * dev * dev;
                    }

                    var m2 = s2 / n;
                    var m3 = s3 / n;
                    var g = m3 / Math.pow(m2, 3 / 2);

                    if (unbiased) {
                        var a = Math.sqrt(n * (n - 1));
                        var b = n - 2;
                        skew[j] = (a / b) * g;
                    } else {
                        skew[j] = g;
                    }
                }
                return skew;
            };

            exports.kurtosis = function kurtosis(matrix, unbiased) {
                if (typeof (unbiased) === 'undefined') unbiased = true;
                var means = exports.mean(matrix);
                var n = matrix.length, m = matrix[0].length;
                var kurt = new Array(m);

                for (var j = 0; j < m; j++) {
                    var s2 = 0, s4 = 0;
                    for (var i = 0; i < n; i++) {
                        var dev = matrix[i][j] - means[j];
                        s2 += dev * dev;
                        s4 += dev * dev * dev * dev;
                    }
                    var m2 = s2 / n;
                    var m4 = s4 / n;

                    if (unbiased) {
                        var v = s2 / (n - 1);
                        var a = (n * (n + 1)) / ((n - 1) * (n - 2) * (n - 3));
                        var b = s4 / (v * v);
                        var c = ((n - 1) * (n - 1)) / ((n - 2) * (n - 3));
                        kurt[j] = a * b - 3 * c;
                    } else {
                        kurt[j] = m4 / (m2 * m2) - 3;
                    }
                }
                return kurt;
            };

            exports.standardError = function standardError(matrix) {
                var samples = matrix.length;
                var standardDeviations = exports.standardDeviation(matrix);
                var l = standardDeviations.length;
                var standardErrors = new Array(l);
                var sqrtN = Math.sqrt(samples);

                for (var i = 0; i < l; i++) {
                    standardErrors[i] = standardDeviations[i] / sqrtN;
                }
                return standardErrors;
            };

            exports.covariance = function covariance(matrix, dimension) {
                return exports.scatter(matrix, undefined, dimension);
            };

            exports.scatter = function scatter(matrix, divisor, dimension) {
                if (typeof (dimension) === 'undefined') {
                    dimension = 0;
                }
                if (typeof (divisor) === 'undefined') {
                    if (dimension === 0) {
                        divisor = matrix.length - 1;
                    } else if (dimension === 1) {
                        divisor = matrix[0].length - 1;
                    }
                }
                var means = exports.mean(matrix, dimension);
                var rows = matrix.length;
                if (rows === 0) {
                    return [[]];
                }
                var cols = matrix[0].length,
                    cov, i, j, s, k;

                if (dimension === 0) {
                    cov = new Array(cols);
                    for (i = 0; i < cols; i++) {
                        cov[i] = new Array(cols);
                    }
                    for (i = 0; i < cols; i++) {
                        for (j = i; j < cols; j++) {
                            s = 0;
                            for (k = 0; k < rows; k++) {
                                s += (matrix[k][j] - means[j]) * (matrix[k][i] - means[i]);
                            }
                            s /= divisor;
                            cov[i][j] = s;
                            cov[j][i] = s;
                        }
                    }
                } else if (dimension === 1) {
                    cov = new Array(rows);
                    for (i = 0; i < rows; i++) {
                        cov[i] = new Array(rows);
                    }
                    for (i = 0; i < rows; i++) {
                        for (j = i; j < rows; j++) {
                            s = 0;
                            for (k = 0; k < cols; k++) {
                                s += (matrix[j][k] - means[j]) * (matrix[i][k] - means[i]);
                            }
                            s /= divisor;
                            cov[i][j] = s;
                            cov[j][i] = s;
                        }
                    }
                } else {
                    throw new Error('Invalid dimension');
                }

                return cov;
            };

            exports.correlation = function correlation(matrix) {
                var means = exports.mean(matrix),
                    standardDeviations = exports.standardDeviation(matrix, true, means),
                    scores = exports.zScores(matrix, means, standardDeviations),
                    rows = matrix.length,
                    cols = matrix[0].length,
                    i, j;

                var cor = new Array(cols);
                for (i = 0; i < cols; i++) {
                    cor[i] = new Array(cols);
                }
                for (i = 0; i < cols; i++) {
                    for (j = i; j < cols; j++) {
                        var c = 0;
                        for (var k = 0, l = scores.length; k < l; k++) {
                            c += scores[k][j] * scores[k][i];
                        }
                        c /= rows - 1;
                        cor[i][j] = c;
                        cor[j][i] = c;
                    }
                }
                return cor;
            };

            exports.zScores = function zScores(matrix, means, standardDeviations) {
                means = means || exports.mean(matrix);
                if (typeof (standardDeviations) === 'undefined') standardDeviations = exports.standardDeviation(matrix, true, means);
                return exports.standardize(exports.center(matrix, means, false), standardDeviations, true);
            };

            exports.center = function center(matrix, means, inPlace) {
                means = means || exports.mean(matrix);
                var result = matrix,
                    l = matrix.length,
                    i, j, jj;

                if (!inPlace) {
                    result = new Array(l);
                    for (i = 0; i < l; i++) {
                        result[i] = new Array(matrix[i].length);
                    }
                }

                for (i = 0; i < l; i++) {
                    var row = result[i];
                    for (j = 0, jj = row.length; j < jj; j++) {
                        row[j] = matrix[i][j] - means[j];
                    }
                }
                return result;
            };

            exports.standardize = function standardize(matrix, standardDeviations, inPlace) {
                if (typeof (standardDeviations) === 'undefined') standardDeviations = exports.standardDeviation(matrix);
                var result = matrix,
                    l = matrix.length,
                    i, j, jj;

                if (!inPlace) {
                    result = new Array(l);
                    for (i = 0; i < l; i++) {
                        result[i] = new Array(matrix[i].length);
                    }
                }

                for (i = 0; i < l; i++) {
                    var resultRow = result[i];
                    var sourceRow = matrix[i];
                    for (j = 0, jj = resultRow.length; j < jj; j++) {
                        if (standardDeviations[j] !== 0 && !isNaN(standardDeviations[j])) {
                            resultRow[j] = sourceRow[j] / standardDeviations[j];
                        }
                    }
                }
                return result;
            };

            exports.weightedVariance = function weightedVariance(matrix, weights) {
                var means = exports.mean(matrix);
                var rows = matrix.length;
                if (rows === 0) return [];
                var cols = matrix[0].length;
                var vari = new Array(cols);

                for (var j = 0; j < cols; j++) {
                    var sum = 0;
                    var a = 0, b = 0;

                    for (var i = 0; i < rows; i++) {
                        var z = matrix[i][j] - means[j];
                        var w = weights[i];

                        sum += w * (z * z);
                        b += w;
                        a += w * w;
                    }

                    vari[j] = sum * (b / (b * b - a));
                }

                return vari;
            };

            exports.weightedMean = function weightedMean(matrix, weights, dimension) {
                if (typeof (dimension) === 'undefined') {
                    dimension = 0;
                }
                var rows = matrix.length;
                if (rows === 0) return [];
                var cols = matrix[0].length,
                    means, i, ii, j, w, row;

                if (dimension === 0) {
                    means = new Array(cols);
                    for (i = 0; i < cols; i++) {
                        means[i] = 0;
                    }
                    for (i = 0; i < rows; i++) {
                        row = matrix[i];
                        w = weights[i];
                        for (j = 0; j < cols; j++) {
                            means[j] += row[j] * w;
                        }
                    }
                } else if (dimension === 1) {
                    means = new Array(rows);
                    for (i = 0; i < rows; i++) {
                        means[i] = 0;
                    }
                    for (j = 0; j < rows; j++) {
                        row = matrix[j];
                        w = weights[j];
                        for (i = 0; i < cols; i++) {
                            means[j] += row[i] * w;
                        }
                    }
                } else {
                    throw new Error('Invalid dimension');
                }

                var weightSum = arrayStat.sum(weights);
                if (weightSum !== 0) {
                    for (i = 0, ii = means.length; i < ii; i++) {
                        means[i] /= weightSum;
                    }
                }
                return means;
            };

            exports.weightedCovariance = function weightedCovariance(matrix, weights, means, dimension) {
                dimension = dimension || 0;
                means = means || exports.weightedMean(matrix, weights, dimension);
                var s1 = 0, s2 = 0;
                for (var i = 0, ii = weights.length; i < ii; i++) {
                    s1 += weights[i];
                    s2 += weights[i] * weights[i];
                }
                var factor = s1 / (s1 * s1 - s2);
                return exports.weightedScatter(matrix, weights, means, factor, dimension);
            };

            exports.weightedScatter = function weightedScatter(matrix, weights, means, factor, dimension) {
                dimension = dimension || 0;
                means = means || exports.weightedMean(matrix, weights, dimension);
                if (typeof (factor) === 'undefined') {
                    factor = 1;
                }
                var rows = matrix.length;
                if (rows === 0) {
                    return [[]];
                }
                var cols = matrix[0].length,
                    cov, i, j, k, s;

                if (dimension === 0) {
                    cov = new Array(cols);
                    for (i = 0; i < cols; i++) {
                        cov[i] = new Array(cols);
                    }
                    for (i = 0; i < cols; i++) {
                        for (j = i; j < cols; j++) {
                            s = 0;
                            for (k = 0; k < rows; k++) {
                                s += weights[k] * (matrix[k][j] - means[j]) * (matrix[k][i] - means[i]);
                            }
                            cov[i][j] = s * factor;
                            cov[j][i] = s * factor;
                        }
                    }
                } else if (dimension === 1) {
                    cov = new Array(rows);
                    for (i = 0; i < rows; i++) {
                        cov[i] = new Array(rows);
                    }
                    for (i = 0; i < rows; i++) {
                        for (j = i; j < rows; j++) {
                            s = 0;
                            for (k = 0; k < cols; k++) {
                                s += weights[k] * (matrix[j][k] - means[j]) * (matrix[i][k] - means[i]);
                            }
                            cov[i][j] = s * factor;
                            cov[j][i] = s * factor;
                        }
                    }
                } else {
                    throw new Error('Invalid dimension');
                }

                return cov;
            };

        }, {"./array": 7}]
    }, {}, [1])(1)
});

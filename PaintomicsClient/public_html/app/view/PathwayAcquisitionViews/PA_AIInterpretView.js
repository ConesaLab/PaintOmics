/* global Ext, $, marked, SERVER_URL_AI_INTERPRET_REPORT, SERVER_URL_AI_INTERPRET_CHAT, withAIProviderInfo, paWalkEl, paWalkResultsNode, paWalkReferencesNode, paWalkStatementsNode, paWalkLegsNode, paWalkPathwayLink, paWalkOpenPathway, paWalkHeaderNode, paWalkGatesNode, paWalkTextShown, paWalkBlockedNode, paWalkModuleSegments, paWalkAnchorGene */

if (typeof marked !== "undefined" && marked.use) {
    marked.use({
        gfm: true,
        breaks: true,
        pedantic: false,
        headerIds: false,
        mangle: false
    });
}

function PA_AIInterpretView() {

    /** Transport retries for the report fetch before giving up with a button. */
    this.REPORT_RETRIES = 2;
    this.$root = null;
    this.isExpanded = false;
    this.chatHistory = [];
    this.isWaitingResponse = false;
    this.jobID = null;
    this.reportLoaded = false;
    this.onRetry = null;
    this.isFullscreen = false;
    // id/name/source of the pathways the interpretation walk runs through,
    // used to turn pathway mentions in chat replies into links.
    this.pathwayIndex = [];

    this.init = function(jobID) {
        this.jobID = jobID;
        var me = this;

        var html =
            '<div class="ai-widget" style="display:none;">' +
            '  <div class="ai-widget-panel">' +
            '    <div class="ai-widget-header">' +
            /* The mark rides with the name here for the same reason the launcher
               is nothing but the mark: expanding the panel replaces one with the
               other, and without this the picture the user clicked disappears
               the moment they click it. */
            '      <span class="ai-widget-header-title">' + getAIMark() + 'PaintOmics AI</span>' +
            '      <div class="ai-widget-header-actions">' +
            '        <button class="ai-fullscreen-btn" title="Fullscreen">&#x26F6;</button>' +
            '        <button class="ai-minimize-btn" title="Minimize">&mdash;</button>' +
            '      </div>' +
            '    </div>' +
            '    <div class="ai-widget-progress" style="display:none;">' +
            '      <div class="ai-progress-detail">Starting...</div>' +
            '      <div class="ai-progress-track"><div class="ai-progress-fill" style="width:0%"></div></div>' +
            '      <ul class="ai-activity" style="display:none"></ul>' +
            '    </div>' +
            '    <div class="ai-widget-messages"></div>' +
            '    <div class="ai-widget-input-area">' +
            '      <textarea placeholder="Ask a follow-up question..." rows="1"></textarea>' +
            '      <button class="ai-send-btn" title="Send">&#10148;</button>' +
            '    </div>' +
            '  </div>' +
            '  <button class="ai-widget-fab" title="PaintOmics AI">' +
            '    <span class="ai-fab-icon">' + getAIMark(26) + '</span>' +
            '    <span class="ai-widget-fab-badge" style="display:none;"></span>' +
            '  </button>' +
            '</div>';

        this.$root = $(html);
        $("body").append(this.$root);

        // Bind events
        this.$root.find(".ai-widget-fab").on("click", function() {
            me.toggle();
        });

        this.$root.find(".ai-minimize-btn").on("click", function() {
            me.collapse();
        });

        this.$root.find(".ai-fullscreen-btn").on("click", function() {
            me.toggleFullscreen();
        });

        this.$root.find(".ai-send-btn").on("click", function() {
            me.sendChat();
        });

        this.$root.find(".ai-widget-input-area textarea").on("keydown", function(e) {
            if (e.keyCode === 13 && !e.shiftKey) {
                e.preventDefault();
                me.sendChat();
            }
        });

        // Delegated so it covers the pathway links in the walk and in chat
        // replies. Deliberately not an inline onclick: the sanitiser strips on*
        // attributes. A link opens the pathway's diagram in Step 4, where its
        // Walk column walks that one pathway.
        this.$root.find(".ai-widget-messages").on("click", ".ai-pathway-link, .pa-walk-pathway-link", function(e) {
            e.preventDefault();
            if (typeof paWalkOpenPathway === "function") {
                paWalkOpenPathway($(this).attr("data-pathway-id"));
            }
        });
    };

    this.show = function() {
        if (this.$root) {
            this.$root.fadeIn(300);
        }
    };

    this.hide = function() {
        if (this.$root) {
            this.$root.fadeOut(300);
        }
    };

    this.expand = function() {
        if (!this.$root) return;
        this.$root.find(".ai-widget-panel").addClass("is-expanded");
        this.isExpanded = true;
        // Auto-scroll messages
        var msgs = this.$root.find(".ai-widget-messages");
        if (msgs.length) {
            msgs.scrollTop(msgs[0].scrollHeight);
        }
        // Auto-load report if done and not loaded
        if (!this.reportLoaded && this._lastStatus === "done") {
            this.loadReport();
        }
    };

    this.collapse = function() {
        if (!this.$root) return;
        if (this.isFullscreen) {
            this.$root.find(".ai-widget-panel").removeClass("is-fullscreen");
            this.$root.find(".ai-widget-fab").show();
            this.$root.find(".ai-fullscreen-btn").html("&#x26F6;").attr("title", "Fullscreen");
            this.isFullscreen = false;
        }
        this.$root.find(".ai-widget-panel").removeClass("is-expanded");
        this.isExpanded = false;
    };

    this.toggle = function() {
        if (this.isExpanded) {
            this.collapse();
        } else {
            this.expand();
        }
    };

    this.toggleFullscreen = function() {
        if (!this.$root) return;
        var $panel = this.$root.find(".ai-widget-panel");
        var $fab = this.$root.find(".ai-widget-fab");
        var $btn = this.$root.find(".ai-fullscreen-btn");

        if (this.isFullscreen) {
            // Exit fullscreen
            $panel.removeClass("is-fullscreen");
            $fab.show();
            $btn.html("&#x26F6;").attr("title", "Fullscreen");
            this.isFullscreen = false;
        } else {
            // Enter fullscreen - make sure panel is expanded first
            if (!this.isExpanded) {
                this.expand();
            }
            $panel.addClass("is-fullscreen");
            $fab.hide();
            $btn.html("&#x2716;").attr("title", "Exit fullscreen");
            this.isFullscreen = true;
        }
        // Auto-scroll messages
        var msgs = this.$root.find(".ai-widget-messages");
        if (msgs.length) {
            msgs.scrollTop(msgs[0].scrollHeight);
        }
    };

    this._lastStatus = null;

    /* What the walker is doing, not just how far along it is: the legs it has
       walked so far, newest last, as /ai_interpret_status returns them. */
    var TOOL_LABELS = {
        step: "Walked",
        jump: "Jumped to a seed"
    };

    this._renderActivity = function(trace, total) {
        var $list = this.$root && this.$root.find(".ai-activity");
        if (!$list || !$list.length) { return; }
        if (!trace || !trace.length) { $list.hide(); return; }

        /* Built as DOM nodes with .text(), not concatenated HTML: these strings
           are node labels from the walk, and they reach this feed without
           passing the sanitiser. */
        $list.empty();
        if (total > trace.length) {
            $list.append($('<li class="ai-activity-row is-count">')
                         .text(total + " legs so far"));
        }
        trace.slice(-6).forEach(function(e) {
            var result = String(e.result === null || e.result === undefined ? "" : e.result);
            var failed = result.indexOf("ERROR") === 0;
            var detail = failed ? "failed"
                : String(e.args === null || e.args === undefined ? "" : e.args);
            var $row = $('<li class="ai-activity-row">');
            if (failed) { $row.addClass("is-failed"); }
            $row.append($('<span class="ai-activity-tool">')
                        .text(TOOL_LABELS[e.tool] || e.tool || ""));
            if (detail) {
                $row.append($('<span class="ai-activity-detail">')
                            .text(detail.slice(0, 64)));
            }
            if (e.ms > 1500) {
                $row.append($('<span class="ai-activity-ms">')
                            .text(Math.round(e.ms / 1000) + "s"));
            }
            $list.append($row);
        });
        $list.show();
    };

    this.updateProgress = function(status, percent, detail, trace, toolCalls) {
        if (!this.$root) return;
        this._renderActivity(trace, toolCalls);
        var $progress = this.$root.find(".ai-widget-progress");
        var $fab = this.$root.find(".ai-widget-fab");
        var $badge = this.$root.find(".ai-widget-fab-badge");

        this._lastStatus = status;

        if (status === "done") {
            $progress.hide();
            $fab.removeClass("is-processing");
            $badge.css("background", "#66bb6a").html("&#10003;").show();
            // Load as soon as it is ready, expanded or not, so the result is
            // there the moment the panel opens.
            if (!this.reportLoaded) {
                this.loadReport();
            }
        } else if (status === "unavailable") {
            // Failed, and retrying cannot help: the job it describes is no
            // longer on the server. Same red treatment as an error, minus the
            // Retry button, which would re-post to /ai_interpret_initiate and
            // be refused for exactly the same reason. A button that cannot
            // work is worse than no button -- it reads as "we could fix this
            // if you asked again".
            $progress.show().removeClass("is-done").addClass("is-error");
            this.$root.find(".ai-progress-fill").css("width", "100%");
            this.$root.find(".ai-progress-detail").text(detail || "Unavailable");
            $fab.removeClass("is-processing");
            $badge.css("background", "#ef5350").html("!").show();
        } else if (status === "error") {
            $progress.show().removeClass("is-done").addClass("is-error");
            this.$root.find(".ai-progress-fill").css("width", "100%");
            this.$root.find(".ai-progress-detail").html(
                (detail || "Unknown error") +
                ' <button class="ai-retry-btn">Retry</button>'
            );
            $fab.removeClass("is-processing");
            $badge.css("background", "#ef5350").html("!").show();
            // Bind retry
            var me = this;
            this.$root.find(".ai-retry-btn").on("click", function() {
                $progress.removeClass("is-error");
                me.$root.find(".ai-progress-fill").css("width", "0%");
                me.$root.find(".ai-progress-detail").text("Retrying...");
                $badge.hide();
                $fab.addClass("is-processing");
                if (typeof me.onRetry === "function") {
                    me.onRetry();
                }
            });
        } else if (status === "not_started") {
            // No walk for this job: one analysed before the walk existed, or
            // whose start after Step 2 never reached the server. Offer to start
            // it rather than showing a bar that will never move.
            $progress.show().removeClass("is-done is-error");
            this.$root.find(".ai-progress-fill").css("width", "0%");
            var $detail = this.$root.find(".ai-progress-detail").empty()
                .append(document.createTextNode("No interpretation has run for this job yet. "));
            var me2 = this;
            $('<button class="ai-retry-btn ai-start-btn">Start the graph walk</button>')
                .on("click", function() {
                    $(this).prop("disabled", true).text("Starting...");
                    if (typeof me2.onRetry === "function") { me2.onRetry(); }
                })
                .appendTo($detail);
            $fab.removeClass("is-processing");
            $badge.hide();
        } else {
            // Processing
            $progress.show().removeClass("is-done is-error");
            this.$root.find(".ai-progress-fill").css("width", percent + "%");
            this.$root.find(".ai-progress-detail").text(detail || status || "Processing...");
            $fab.addClass("is-processing");
            $badge.hide();
        }
    };

    /**
     * Fetch the finished report and show it.
     *
     * This is called ONCE, from the terminal "done" poll -- and "done" ends the
     * polling chain, so if this single request failed there was nothing left to
     * try again. The panel said "Failed to load the report. Please try again."
     * and offered no way to do so; the only real recovery was reloading the
     * page, or knowing that opening the panel happens to retry.
     *
     * A 57KB report on a single-process server is not a request that always
     * succeeds, so it now retries by itself, and the message it falls back to
     * carries a button that works.
     *
     * @param {Number} attempt  0 for the first try; used only by the retries.
     */
    this.loadReport = function(attempt) {
        var me = this;
        attempt = attempt || 0;
        // One load at a time: the "done" poll and opening the panel can both
        // ask while the first request is still carrying a large view, and two
        // answers drew the walk twice. Retries continue the load in flight.
        if (attempt === 0 && me.reportLoading) { return; }
        me.reportLoading = true;
        $.ajax({
            type: "POST",
            url: SERVER_URL_AI_INTERPRET_REPORT,
            data: { jobID: me.jobID },
            success: function(response) {
                me.reportLoading = false;
                if (response.success && response.walk) {
                    me.pathwayIndex = response.pathways || [];
                    me.displayWalk(response.walk);
                    me.reportLoaded = true;
                } else if (response.status === "error") {
                    me.addMessage("assistant",
                        "The AI interpretation failed: **" + (response.message || "Unknown error") + "**");
                } else if (/not found|Invalid Job ID/i.test(String(response.message || ""))) {
                    // The job itself is gone, so there is nothing to wait for.
                    // Say which of the two it is rather than "still in progress",
                    // which is what this used to claim for a deleted job.
                    me.addMessage("assistant",
                        "This job is no longer stored on the server, so its " +
                        "interpretation cannot be loaded. Jobs are removed after " +
                        "7 days for guests and 14 days for registered users.");
                } else {
                    me.addMessage("assistant",
                        "The graph walk is still running. Its result appears here when it is sealed.");
                }
            },
            error: function() {
                if (attempt < me.REPORT_RETRIES) {
                    // Back off and try again: this is a transport failure, and
                    // the report is large enough that a busy server drops it.
                    setTimeout(function() { me.loadReport(attempt + 1); },
                               1000 * Math.pow(2, attempt));
                    return;
                }
                me.reportLoading = false;
                // Passed as trusted HTML on purpose: it is a fixed string with
                // nothing interpolated into it, and the assistant path would
                // otherwise run it through marked and the sanitiser, which is
                // for model output and would take the button's attributes with
                // it.
                me.addMessage("assistant",
                    "Failed to load the report after " + (me.REPORT_RETRIES + 1) +
                    " attempts. <button class=\"ai-reload-report-btn\">Try again</button>",
                    true);
                // Bound to the button rather than offered as advice: "please
                // try again" with nothing to press is what this said before.
                me.$root.find(".ai-reload-report-btn").last().off("click").on("click", function() {
                    me.loadReport(0);
                });
            }
        });
    };

    this._preprocessMarkdown = function(text) {
        /* Structure tokens glued to the tail of the previous sentence:
               "...hepatic response [3, 6]. - **All ten pathways..."
               "...perturbs bile secretion. ---"
               "...biosynthetic response. ### The Flat Temporal Pattern..."
           A glued token is not markdown at all - marked renders it literally,
           which is how "---" and "###" ended up visible in reports.

           This was originally read as model behaviour. It is overwhelmingly not:
           the cause was redact_unverified_v2 on the server, which split the body
           on sentence boundaries and rejoined with " ", swallowing the newline
           before every heading and bullet that followed a full stop. Over the 56
           reports stored locally the split is clean - 29 of 29 reports with a
           redaction carry glued tokens (mean 37.6 each), 0 of 27 without a
           redaction do. The server now redacts without touching layout.

           The model does glue occasionally: running these rules over the 27
           clean reports changed the rendered structure of exactly one, an
           after-a-colon bullet. So the rules stay, for that case and as a
           compatibility shim - reports written before the server fix are still
           in the database and still open in this view.

           On well-formed markdown they are structurally neutral, checked in the
           browser over those 27 reports plus 4 outputs of the fixed redactor:
           blank lines get normalised, no heading or list item is added or lost. */
        // Headings glued after prose ("prose. ### Title" / "prose. #### Title")
        text = text.replace(/([^\n])[ \t]+(#{1,6} )/g, "$1\n\n$2");
        // Horizontal rules glued after prose, when the --- ends its line.
        // [^\n-] keeps this off spaced em-dashes ("text --- text" stays prose
        // because the lookahead requires end-of-line).
        text = text.replace(/([^\n-])[ \t]+(-{3,})[ \t]*(?=\n|$)/g, "$1\n\n$2");
        // Bullets glued after a sentence. Fires when the bullet text opens with
        // emphasis ("- **") or a capital ("- The SPLIS feedback model...") -
        // every glued bullet in the stored reports is one of the two, while a
        // dash inside prose continues lowercase and is left alone.
        text = text.replace(/([.!?:\])\*])[ \t]+- (?=\*|[A-Z])/g, "$1\n- ");
        // Numbered items glued after a sentence ("...changes. 2. **Systematic")
        text = text.replace(/([.!?:\])\*])[ \t]+(\d{1,2}\. )(?=\*|[A-Z])/g, "$1\n$2");
        // Ensure blank line before headings (required by CommonMark)
        text = text.replace(/([^\n])\n(#{1,6}\s)/g, "$1\n\n$2");
        // Ensure blank line before horizontal rules
        text = text.replace(/([^\n])\n(---+)/g, "$1\n\n$2");
        // Ensure blank line after horizontal rules
        text = text.replace(/(---+)\n([^\n])/g, "$1\n\n$2");
        // Ensure blank line before unordered list starts when preceded by non-list content
        text = text.replace(/([^\n])\n([-*+] )/g, "$1\n\n$2");
        // Ensure blank line before ordered list starts when preceded by non-list content
        text = text.replace(/([^\n])\n(\d+\. )/g, "$1\n\n$2");
        // Fix numbered headings that LLM produces like "### 1. Title" inside lists
        // Convert "N. ### Title" pattern to "### N. Title"
        text = text.replace(/^(\d+)\.\s+(#{1,6}\s)/gm, "$2$1. ");
        // Normalize excessive blank lines (3+ newlines to 2)
        text = text.replace(/\n{3,}/g, "\n\n");
        return text;
    };

    // Tags and attributes allowed to survive sanitising. Everything the report
    // legitimately uses is markdown, so this covers the full output of marked
    // plus the two link types we add ourselves.
    var SANITIZE_ALLOWED_TAGS = {
        A:1, B:1, BLOCKQUOTE:1, BR:1, CODE:1, DD:1, DEL:1, DIV:1, DL:1, DT:1,
        EM:1, H1:1, H2:1, H3:1, H4:1, H5:1, H6:1, HR:1, I:1, LI:1, OL:1, P:1,
        PRE:1, SPAN:1, STRONG:1, SUB:1, SUP:1, TABLE:1, TBODY:1, TD:1, TH:1,
        THEAD:1, TR:1, UL:1
    };
    var SANITIZE_ALLOWED_ATTRS = {
        href:1, title:1, "class":1, target:1, rel:1,
        "data-pathway-id":1, "data-pathway-name":1
    };

    /**
     * Strip anything executable from report HTML.
     *
     * The report text is model output, and the model reads uploaded data and
     * the user's experiment-design field -- so it is untrusted input that was
     * being handed to the DOM verbatim. Parsing happens in an inert document
     * (DOMParser never runs scripts or fetches subresources), then the tree is
     * walked against a whitelist: unknown elements are unwrapped rather than
     * dropped so their text survives, every on* handler is removed, and href
     * values are restricted to http/https/mailto so javascript: URLs cannot
     * get through.
     */
    this._sanitizeHtml = function(html) {
        var doc;
        try {
            doc = new DOMParser().parseFromString("<body>" + html + "</body>", "text/html");
        } catch (e) {
            return $("<div>").text(html).html();
        }

        var walk = function(node) {
            var children = Array.prototype.slice.call(node.childNodes);
            for (var i = 0; i < children.length; i++) {
                var child = children[i];
                if (child.nodeType === 3) { continue; }          // text: always safe
                if (child.nodeType !== 1) {                       // comments, CDATA, ...
                    node.removeChild(child);
                    continue;
                }
                var tag = child.tagName.toUpperCase();
                if (tag === "SCRIPT" || tag === "STYLE" || tag === "IFRAME" ||
                    tag === "OBJECT" || tag === "EMBED" || tag === "FORM") {
                    // Drop these entirely -- their text content is not worth keeping.
                    node.removeChild(child);
                    continue;
                }
                walk(child);
                if (!SANITIZE_ALLOWED_TAGS[tag]) {
                    // Unwrap: keep the text, discard the element.
                    while (child.firstChild) {
                        node.insertBefore(child.firstChild, child);
                    }
                    node.removeChild(child);
                    continue;
                }
                var attrs = Array.prototype.slice.call(child.attributes);
                for (var a = 0; a < attrs.length; a++) {
                    var name = attrs[a].name.toLowerCase();
                    var value = attrs[a].value;
                    if (!SANITIZE_ALLOWED_ATTRS[name]) {
                        child.removeAttribute(attrs[a].name);
                        continue;
                    }
                    if (name === "href" && !/^(https?:|mailto:|#)/i.test(value.replace(/\s/g, ""))) {
                        child.removeAttribute(attrs[a].name);
                    }
                }
                if (tag === "A" && child.getAttribute("target") === "_blank") {
                    child.setAttribute("rel", "noopener noreferrer");
                }
            }
        };

        walk(doc.body);
        return doc.body.innerHTML;
    };

    /**
     * Turn pathway names mentioned in the report into links that open the
     * pathway.
     *
     * Matching is done against the pathway index the server analysed, over text
     * nodes of the already-sanitised DOM -- not with a regex over the HTML
     * string. Working on text nodes means a pathway name can never be spliced
     * into a tag or an attribute, and it keeps the matcher from firing inside
     * existing links, code spans, or the References section.
     *
     * Each pathway is matched by its registered name, its ID (the reports cite
     * "(mmu00040)" and "(R-MMU-73864)" constantly), and the conservative name
     * variants from _pathwayAliases -- exact names alone left a third of the
     * mentioned pathways unlinked because the model paraphrases.
     *
     * Aliases are matched longest-first so "MAPK signaling pathway" wins over a
     * shorter pathway whose name is a prefix of it.
     *
     * Two rules keep this from burying the report in links:
     *
     * Only the first mention of each pathway is linked. Linking every mention
     * produced 73 links for 11 pathways on the example job, which reads as
     * noise rather than as citations.
     *
     * Single-word aliases (including IDs) must match the registered
     * capitalisation. Several pathways are named after the process they
     * describe -- Apoptosis, Autophagy, Efferocytosis -- and the report uses
     * those words as ordinary nouns constantly. "Apoptosis" heading a section
     * is a pathway reference; "...leading to apoptosis" in prose is not, and
     * linking it would send the user to a diagram the sentence was not talking
     * about. Multi-word names ("Cell cycle", "Intrinsic Pathway for
     * Apoptosis") are specific enough that case does not matter.
     */
    /**
     * Name variants a report is known to use for a registered pathway name.
     *
     * Derived, not exhaustive: each rule answers a paraphrase the stored
     * reports actually contain. Measured over all 23 stored reports, 43 of
     * 102 unlinked index entries had their pathway ID cited in the body
     * ("(mmu00040)") and 25 more were name paraphrases -- "Citrate cycle
     * (TCA cycle)" cited as "TCA cycle", "Pentose and glucuronate
     * interconversions" as "Pentose/glucuronate interconversions",
     * "Autophagy - animal" as plain "Autophagy", hyphens re-typed as
     * en/em dashes.
     */
    this._pathwayAliases = function(name) {
        var out = [];
        var m = name.match(/^(.{4,}?)\s*\(([^()]{3,})\)$/);
        if (m) { out.push(m[1], m[2]); }
        if (name.indexOf(" / ") !== -1) {
            out.push(name.replace(/ \/ /g, "/"));
            name.split(" / ").forEach(function(part) {
                if (part.length > 3) out.push(part);
            });
        }
        if (name.indexOf(" and ") !== -1) {
            out.push(name.replace(/ and /g, "/"));
        }
        if (name.indexOf(" - ") !== -1) {
            out.push(name.replace(/ - /g, " – "));
            out.push(name.replace(/ - /g, " — "));
            // KEGG's scoping suffix, not part of the biological name.
            var stripped = name.replace(
                / - (animal|plant|fungi|yeast|other|multiple species|general)$/i, "");
            if (stripped !== name) out.push(stripped);
        }
        return out;
    };

    this._linkifyPathways = function(rootEl, pathways) {
        if (!pathways || !pathways.length) return;

        var named = pathways.filter(function(p) { return p && p.id && p.name; });
        if (!named.length) return;

        var alreadyLinked = {};

        // Alias table: lowercased alias -> {alias, pw}, or null once two
        // pathways both claim a derived alias -- an ambiguous shorthand must
        // not link to either. A registered full name (or ID) outranks any
        // derived variant; between two full names the first keeps the key.
        var byLowerAlias = {};
        var claim = function(alias, pw, derived) {
            if (!alias || alias.length < 3) return;
            var key = alias.toLowerCase();
            var cur = byLowerAlias[key];
            if (cur === null) return;
            if (!cur) { byLowerAlias[key] = { alias: alias, pw: pw, derived: derived }; return; }
            if (cur.pw.id === pw.id) return;
            if (cur.derived && !derived) { byLowerAlias[key] = { alias: alias, pw: pw, derived: false }; return; }
            if (cur.derived && derived) { byLowerAlias[key] = null; }
        };
        var me = this;
        named.forEach(function(pw) {
            claim(pw.name, pw, false);
            // The reports routinely cite the ID itself: "(mmu00040)",
            // "(R-MMU-73864)". Nothing else in prose looks like one.
            claim(pw.id, pw, false);
            me._pathwayAliases(pw.name).forEach(function(a) { claim(a, pw, true); });
        });

        var entries = [];
        for (var key in byLowerAlias) {
            if (byLowerAlias[key]) entries.push(byLowerAlias[key]);
        }
        if (!entries.length) return;
        entries.sort(function(a, b) { return b.alias.length - a.alias.length; });
        var pattern = new RegExp("(" + entries.map(function(e) {
            return e.alias.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        }).join("|") + ")", "gi");

        var SKIP = { A:1, CODE:1, PRE:1 };
        var linked = 0;

        var walk = function(node) {
            var child = node.firstChild;
            while (child) {
                var next = child.nextSibling;
                if (child.nodeType === 1) {
                    if (!SKIP[child.tagName.toUpperCase()]) walk(child);
                } else if (child.nodeType === 3 && child.nodeValue &&
                           child.nodeValue.trim().length > 2) {
                    var text = child.nodeValue;
                    pattern.lastIndex = 0;
                    if (pattern.test(text)) {
                        pattern.lastIndex = 0;
                        var frag = document.createDocumentFragment();
                        var cursor = 0, match;
                        while ((match = pattern.exec(text)) !== null) {
                            var entry = byLowerAlias[match[1].toLowerCase()];
                            if (!entry) continue;
                            var pw = entry.pw;
                            if (alreadyLinked[pw.id]) continue;
                            if (entry.alias.indexOf(" ") === -1 && match[1] !== entry.alias) continue;
                            // Not a fragment of a longer token: "Melanoma"
                            // must not link inside "Melanomagenesis", nor an
                            // ID inside an accession-like string.
                            if (/[A-Za-z0-9]/.test(text.charAt(match.index - 1)) ||
                                /[A-Za-z0-9]/.test(text.charAt(match.index + match[1].length))) continue;
                            if (match.index > cursor) {
                                frag.appendChild(document.createTextNode(
                                    text.slice(cursor, match.index)));
                            }
                            var a = document.createElement("a");
                            a.className = "ai-pathway-link";
                            a.setAttribute("href", "#");
                            a.setAttribute("data-pathway-id", pw.id);
                            a.setAttribute("data-pathway-name", pw.name);
                            a.setAttribute("title",
                                "Open the " + pw.name + " diagram");
                            a.appendChild(document.createTextNode(match[1]));
                            frag.appendChild(a);
                            alreadyLinked[pw.id] = true;
                            cursor = match.index + match[1].length;
                            linked++;
                        }
                        if (cursor > 0) {
                            if (cursor < text.length) {
                                frag.appendChild(document.createTextNode(text.slice(cursor)));
                            }
                            node.replaceChild(frag, child);
                        }
                    }
                }
                child = next;
            }
        };

        // Stop at the References heading. Paper titles routinely contain
        // pathway names ("...link the WWC protein family to Hippo signaling"),
        // and because only the first mention is linked, a pathway the body
        // referred to by a shorter name would get its one link inside a
        // citation instead of in the analysis. marked emits a flat sequence of
        // block elements, so the heading is a direct child of the root.
        var stopAt = null;
        var top = rootEl.children;
        for (var t = 0; t < top.length; t++) {
            if (/^H[1-6]$/.test(top[t].tagName) &&
                /^\s*references\s*$/i.test(top[t].textContent || "")) {
                stopAt = t;
                break;
            }
        }

        if (stopAt === null) {
            walk(rootEl);
        } else {
            for (var b = 0; b < stopAt; b++) {
                if (top[b].nodeType === 1) walk(top[b]);
            }
        }
        return linked;
    };

    /**
     * The interpretation: the universal graph walk's checked Results section,
     * its references, its statements and its legs.
     *
     * Rendered from text nodes by the renderers the Step 4 Walk column uses
     * (PA_Step4WalkView.js), not through marked: every string is model output
     * and none of it is markup. A leg's pathway is a link that opens its
     * diagram. The attribution rides inside the bubble so a copy of the
     * write-up copies it too.
     */
    this.displayWalk = function(view) {
        if (!this.$root || typeof paWalkResultsNode !== "function") { return; }
        var $bubble = $('<div class="ai-message ai-msg-assistant ai-walk-message">' +
            '<div class="ai-msg-label">AI Assistant</div><div class="ai-msg-bubble ai-walk-bubble"></div></div>');
        var bubble = $bubble.find(".ai-walk-bubble")[0];
        var focusLeg = function(n) {
            var chain = $(bubble).find("details.pa-walk-chain").attr("open", "open");
            chain.find(".pa-walk-leg").removeClass("is-focused");
            var row = chain.find('.pa-walk-leg[data-leg="' + n + '"]').addClass("is-focused");
            if (row.length && row[0].scrollIntoView) { row[0].scrollIntoView({block: "nearest"}); }
        };
        var counts = view.counts || {};
        bubble.appendChild(paWalkEl("h3", "ai-walk-title", "Graph walk across KEGG, Reactome and OmniPath"));
        var starts = counts.segments || 1;
        bubble.appendChild(paWalkEl("p", "pa-walk-meta", "AI agents walked the network of every KEGG, Reactome and " +
            "OmniPath interaction for this organism (" + ((view.graph || {}).nodes || "?") + " nodes), with your " +
            "values on its nodes. They started from " + starts + " node" + (starts === 1 ? "" : "s") +
            " whose neighbourhoods hold surprisingly many relevant features and took " + (counts.steps || 0) +
            " steps."));
        /* The perturbation line and the five gates first; the text they judged
           only when every gate passed (or the walk predates them). A walk they
           held back shows a note in its place and opens its legs. */
        var header = paWalkHeaderNode(view);
        if (header) { bubble.appendChild(header); }
        var gates = paWalkGatesNode(view);
        if (gates) { bubble.appendChild(gates); }
        var shown = paWalkTextShown(view);
        if (shown) {
            bubble.appendChild(paWalkResultsNode(view, {onLeg: focusLeg}));
            var references = paWalkReferencesNode(view);
            if (references) { bubble.appendChild(references); }
            if ((view.statements || []).length || (view.dropped || []).length) {
                bubble.appendChild(paWalkStatementsNode(view, {onLeg: focusLeg}));
            }
        } else {
            bubble.appendChild(paWalkBlockedNode(view));
        }
        var chain = paWalkEl("details", "pa-walk-chain");
        chain.open = !shown;
        chain.appendChild(paWalkEl("summary", null, "The walk: " + (counts.steps || 0) + " steps, " +
            (counts.jumps || 0) + " jumps"));
        chain.appendChild(paWalkLegsNode(view.chain, {onLeg: focusLeg, pathwayLink: paWalkPathwayLink,
            segments: paWalkModuleSegments(view), anchorGene: paWalkAnchorGene(view)}));
        bubble.appendChild(chain);
        bubble.appendChild(paWalkEl("p", "pa-walk-meta", "A leg is an interaction a database draws, not a " +
            "finding of this experiment; the values at its ends are yours. Open a pathway to walk it on its " +
            "map, or ask about any leg below."));
        var provenance = paWalkEl("div", "ai-report-provenance",
            "Drafted by a large language model, not by a person. Check every claim and every citation " +
            "against the sources before relying on it. ");
        var model = paWalkEl("span", "ai-report-provenance-model");
        provenance.appendChild(model);
        bubble.appendChild(provenance);
        this.$root.find(".ai-widget-messages").append($bubble);
        if (typeof withAIProviderInfo === "function") {
            withAIProviderInfo(function(info) {
                var text = "Generated at " + info.host;
                if (info.summary && info.summary !== info.host) { text += " — " + info.summary; }
                model.textContent = text + ".";
            });
        }
    };

    this.addMessage = function(role, content, isHtml) {
        var $container = this.$root.find(".ai-widget-messages");
        var cssClass = role === "user" ? "ai-msg-user" : "ai-msg-assistant";
        var label = role === "user" ? "You" : "AI Assistant";
        var bubbleContent = isHtml ? content : $("<div>").text(content).html();

        if (role === "assistant" && !isHtml) {
            try {
                // Chat replies are model output too, so they get the same
                // sanitising pass as the report, and the same pathway links.
                var parsed = this._sanitizeHtml(
                    marked.parse(this._preprocessMarkdown(content)));
                var holder = document.createElement("div");
                holder.innerHTML = parsed;
                this._linkifyPathways(holder, this.pathwayIndex);
                bubbleContent = holder.innerHTML;
            } catch(e) {
                // fallback to escaped text
            }
        }

        var msgHtml = '<div class="ai-message ' + cssClass + '">' +
                      '  <div class="ai-msg-label">' + label + '</div>' +
                      '  <div class="ai-msg-bubble">' + bubbleContent + '</div>' +
                      '</div>';
        $container.append(msgHtml);
        $container.scrollTop($container[0].scrollHeight);
    };

    this.addLoadingIndicator = function() {
        var $container = this.$root.find(".ai-widget-messages");
        $container.append(
            '<div class="ai-message ai-msg-assistant ai-loading-msg">' +
            '  <div class="ai-loading"><div class="ai-loading-dots"><span></span><span></span><span></span></div> Thinking...</div>' +
            '</div>'
        );
        $container.scrollTop($container[0].scrollHeight);
    };

    this.removeLoadingIndicator = function() {
        if (this.$root) {
            this.$root.find(".ai-loading-msg").remove();
        }
    };

    this.sendChat = function() {
        var me = this;
        var $input = me.$root.find(".ai-widget-input-area textarea");
        var message = $input.val().trim();

        if (!message || me.isWaitingResponse) return;

        $input.val("");
        me.addMessage("user", message);
        me.isWaitingResponse = true;
        me.addLoadingIndicator();
        me.$root.find(".ai-send-btn").prop("disabled", true);

        $.ajax({
            type: "POST",
            url: SERVER_URL_AI_INTERPRET_CHAT,
            data: {
                jobID: me.jobID,
                message: message
            },
            success: function(response) {
                me.removeLoadingIndicator();
                me.isWaitingResponse = false;
                me.$root.find(".ai-send-btn").prop("disabled", false);

                if (response.success && response.response) {
                    me.addMessage("assistant", response.response);
                } else {
                    me.addMessage("assistant", "Sorry, I couldn't process your question. " + (response.message || ""));
                }
            },
            error: function() {
                me.removeLoadingIndicator();
                me.isWaitingResponse = false;
                me.$root.find(".ai-send-btn").prop("disabled", false);
                me.addMessage("assistant", "Failed to get a response. Please try again.");
            }
        });
    };

    this.destroy = function() {
        if (this.$root) {
            this.$root.remove();
            this.$root = null;
        }
        this.isExpanded = false;
        this.reportLoaded = false;
        this.chatHistory = [];
    };
}

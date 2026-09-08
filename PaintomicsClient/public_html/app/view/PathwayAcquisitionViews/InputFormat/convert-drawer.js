/*
 * The conversion sheet: what the user watches while the agent works, and
 * where they review what it made and steer it.
 *
 * It is a notebook of the run, not a spinner. The stage rail across the top
 * follows the agent's own state machine -- read, plan, run, check, apply,
 * review -- so the bar can only say things that are true. Below it, "What the
 * AI sees" is the description of the file that actually left this computer:
 * sheet names, column names and kinds, counts, a few example rows, and never a
 * measurement. Then every step the agent takes is named as it happens with the
 * seconds it cost, the generated script is one click away at the step that ran
 * it, and the validator's verdict is quoted rather than summarised.
 *
 * When the agent finishes, the sheet does not simply hand back "a file". A
 * real upload often holds several tables -- one per sheet, one per measurement
 * family -- and a significance column that should become the relevant-features
 * list. So the review shows every table with a preview, the columns it kept
 * and dropped, and lets the user choose which one goes into this omic box,
 * attach the relevant list, add the others as separate omics, or download any
 * of them. Nothing the file held is silently lost.
 *
 * And the user can talk back. The composer at the bottom sends an instruction
 * ("keep the flagged genes", "the first column is a KEGG ID", "use the reads
 * sheet") and the agent revises the accepted script rather than starting over.
 * The same composer answers a question in the user's own words when none of
 * the offered options fit.
 *
 * Nothing here is decorative. Showing the code is what lets a bioinformatician
 * check the transformation; showing the validator's report is what stops the
 * model grading its own work; showing the profile is what makes the privacy
 * claim checkable instead of asserted.
 */
(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory();
    else root.PaintomicsInputFormat = Object.assign(root.PaintomicsInputFormat || {}, factory());
})(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    var API = null;
    function api() { return API || (API = window.PaintomicsInputFormat); }

    /* Pyodide's filesystem takes a plain name; a user's file name may not be
       one. Keep the extension, because the reader dispatches on it. */
    function safeName(name) {
        var cleaned = String(name).replace(/[^A-Za-z0-9._-]/g, "_").replace(/^\.+/, "");
        return cleaned.length ? cleaned.slice(-80) : "input";
    }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = text;
        return n;
    }

    function decode(bytes) {
        return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
    }

    function fmtInt(n) { return Number(n).toLocaleString(); }

    function fmtSeconds(ms) {
        var s = ms / 1000;
        if (s < 10) return s.toFixed(1) + " s";
        if (s < 60) return Math.round(s) + " s";
        return Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";
    }

    function plural(n, word) { return fmtInt(n) + " " + word + (n === 1 ? "" : "s"); }

    /* ------------------------------------------------------------------ *
     * Glyphs
     *
     * Drawn, not typed. A unicode glyph is a different picture in every
     * system font; these are one picture everywhere, in currentColor so the
     * node's own colour paints them. The planning node uses the AI mark's
     * own geometry, so "the model is thinking" wears the mark.
     * ------------------------------------------------------------------ */

    var STROKE = 'fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"';

    var GLYPH = {
        profiling:  '<rect x="4" y="5" width="16" height="14" rx="2" ' + STROKE + '/><path d="M4 10h16M9 10v9" ' + STROKE + '/>',
        running:    '<path d="M8.5 5.5 L18 12 L8.5 18.5 Z" fill="currentColor"/>',
        validating: '<path d="M5 12.5 L10 17.5 L19 7.5" ' + STROKE + '/>',
        done:       '<path d="M5 12.5 L10 17.5 L19 7.5" ' + STROKE + '/>',
        finalising: '<path d="M6 6 L12 12 L6 18 M12.5 6 L18.5 12 L12.5 18" ' + STROKE + '/>',
        asking:     '<path d="M9.3 9.6a2.8 2.8 0 1 1 4.2 2.4c-1 .6-1.5 1.2-1.5 2.5" ' + STROKE + '/><circle cx="12" cy="17.6" r="1.1" fill="currentColor"/>',
        failed:     '<path d="M7.5 7.5 L16.5 16.5 M16.5 7.5 L7.5 16.5" ' + STROKE + '/>',
        user:       '<path d="M4.5 19.5 L8.5 18.5 L19 8 L16 5 L5.5 15.5 Z" ' + STROKE + '/>',
        output:     '<path d="M6 8 L10 12 L6 16 M12 16 H18" ' + STROKE + '/>',
        download:   '<path d="M12 4.5v10M7.5 10.5 12 15l4.5-4.5M5 18.5h14" ' + STROKE + '/>',
        list:       '<path d="M5 7h14M5 12h14M5 17h9" ' + STROKE + '/>',
        table:      '<rect x="4" y="5" width="16" height="14" rx="2" ' + STROKE + '/><path d="M4 10h16M10 10v9" ' + STROKE + '/>'
    };

    function svg(name) {
        var paths = name === "thinking" && typeof window.getAIMarkPaths === "function"
            ? window.getAIMarkPaths() : (GLYPH[name] || GLYPH.profiling);
        return '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + paths + '</svg>';
    }

    /* ------------------------------------------------------------------ *
     * The stage rail
     *
     * Six stages, in the order the loop visits them. A phase from the agent
     * maps onto one of them; reaching a stage lights every stage before it
     * and clears every stage after it, so a retry visibly goes back to Plan
     * rather than pretending to be further along than it is.
     * ------------------------------------------------------------------ */

    var STAGES = [
        { key: "read",   label: "Read" },
        { key: "plan",   label: "Plan" },
        { key: "run",    label: "Run" },
        { key: "check",  label: "Check" },
        { key: "apply",  label: "Apply" },
        { key: "review", label: "Review" }
    ];
    var PHASE_STAGE = {
        profiling: "read", thinking: "plan", asking: "plan", user: "plan",
        running: "run", output: "run", validating: "check", finalising: "apply", done: "review"
    };

    /* ------------------------------------------------------------------ *
     * Sandbox host
     * ------------------------------------------------------------------ */

    function createSandbox(onProgress) {
        var frame = document.createElement("iframe");
        // allow-scripts WITHOUT allow-same-origin: opaque origin, so the code
        // the model writes cannot read cookies, localStorage, this page, or the
        // network. Verified by an isolation probe in inputformat-sandbox.html.
        frame.setAttribute("sandbox", "allow-scripts");
        frame.className = "pa-convert-sandbox";
        frame.src = "inputformat-sandbox.html";
        document.body.appendChild(frame);

        var next = 1;
        var waiters = {};
        var loaded = false;
        var loadedWaiters = [];

        function onMessage(event) {
            if (event.source !== frame.contentWindow) return;
            var msg = event.data || {};
            if (msg.type === "loaded") {
                loaded = true;
                loadedWaiters.splice(0).forEach(function (fn) { fn(); });
                return;
            }
            if (msg.id && waiters[msg.id]) { waiters[msg.id](msg); delete waiters[msg.id]; }
        }
        window.addEventListener("message", onMessage);

        function ready() {
            return loaded ? Promise.resolve()
                          : new Promise(function (res) { loadedWaiters.push(res); });
        }

        function send(payload, timeoutMs) {
            return new Promise(function (resolve, reject) {
                var id = next++;
                waiters[id] = resolve;
                setTimeout(function () {
                    if (waiters[id]) { delete waiters[id]; reject(new Error("The sandbox stopped responding.")); }
                }, timeoutMs || 180000);
                payload.id = id;
                frame.contentWindow.postMessage(payload, "*");
            });
        }

        return {
            async boot() {
                await ready();
                if (onProgress) onProgress("Starting the Python sandbox");
                return send({ type: "boot" }, 240000);
            },
            run: function (code, files) {
                return send({ type: "run", code: code, files: files }, 300000).then(function (r) {
                    return r.ok ? { ok: true, stdout: r.stdout, outputs: r.outputs }
                                : { ok: false, traceback: r.traceback, stdout: r.stdout || "" };
                });
            },
            destroy: function () {
                window.removeEventListener("message", onMessage);
                // A fresh interpreter per conversion: nothing a previous run
                // defined, imported or left in /out can affect the next one.
                if (frame.parentNode) frame.parentNode.removeChild(frame);
            }
        };
    }

    /* ------------------------------------------------------------------ *
     * Transport
     * ------------------------------------------------------------------ */

    function serverTransport() {
        return async function (state) {
            var res = await fetch("input_convert/turn", {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(state)
            });
            var body = await res.json();
            if (!body || !body.ticket) {
                // ServerErrorManager answers with `message`, prefixed by the
                // file and function it was raised in; the readable half is
                // after "ERROR MESSAGE:". Same split PA_Step1Views already does,
                // so a disabled server says so plainly instead of "refused".
                var raw = (body && (body.errorMessage || body.message)) || "";
                var half = raw.split("ERROR MESSAGE:");
                var reason = (half.length > 1 ? half[1] : raw).trim();
                throw new Error(reason || "The conversion service refused the request.");
            }
            // Poll rather than hold a request open: the site has four request
            // threads and the gateway takes about a minute. The server bounds
            // its own turn (agent_turn.TURN_BUDGET_SECONDS, 150 s by default)
            // well inside these 240 polls, so a gateway that does not answer
            // comes back as `error` WITH its reason long before this loop
            // gives up -- the bare "timed out" below is for a server that
            // stopped answering the poll itself.
            for (var i = 0; i < 240; i++) {
                await new Promise(function (r) { setTimeout(r, 1000); });
                var poll = await fetch("input_convert/turn/" + encodeURIComponent(body.ticket),
                                       { credentials: "same-origin" });
                var p = await poll.json();
                if (p.state === "done") return p.action;
                if (p.state === "error") {
                    throw new Error(p.message || "The conversion service failed on that step.");
                }
                if (p.state === "unknown") {
                    // The ticket is gone before it was answered: the server
                    // restarted under it. Polling on would wait four minutes
                    // for nothing.
                    throw new Error("The server lost track of this conversion " +
                                    "(it may have restarted). Please try again.");
                }
            }
            throw new Error("The conversion timed out.");
        };
    }

    /* The server's refusal when the converter is switched off there
       (InputConvertServlet.NOT_ENABLED_MESSAGE). Recognised so the sheet
       explains and stops, instead of offering a retry that cannot help. */
    var CONVERTER_OFF = /not enabled on this server/i;

    /* Who is on the other end of the transport. The same gateway the AI
       interpretation uses, so the same endpoint answers; shown in the
       anatomy card because "your data goes to a model" should name which.
       It also says whether the converter is switched on (`inputConverter`),
       which the sheet reads BEFORE it boots a sandbox. */
    var providerPromise = null;
    function provider() {
        if (!providerPromise) {
            providerPromise = fetch("ai_provider", { credentials: "same-origin" })
                .then(function (r) { return r.json(); })
                .catch(function () { return null; });
        }
        return providerPromise;
    }

    /* ------------------------------------------------------------------ *
     * Talking to the Step 1 form
     * ------------------------------------------------------------------ */

    function setFile(inputEl, bytes, name) {
        var transfer = new DataTransfer();
        transfer.items.add(new File([bytes], name, { type: "text/plain" }));
        inputEl.files = transfer.files;
        inputEl.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function download(bytes, name) {
        var url = URL.createObjectURL(new Blob([bytes], { type: "text/tab-separated-values" }));
        var a = document.createElement("a");
        a.href = url; a.download = name;
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
    }

    function omicCardOf(inputEl) {
        return inputEl && inputEl.closest ? inputEl.closest(".omicbox") : null;
    }

    function omicComponentOf(inputEl) {
        var box = omicCardOf(inputEl);
        return (box && window.Ext && Ext.getCmp) ? Ext.getCmp(box.id) : null;
    }

    /* The omic-name field a card would submit. A region or pairwise card holds
       two with the same itemId -- the first is the disabled, empty twin of the
       "already mapped" form -- so queryById returned the one the form ignores.
       Same rule as format-panel's omicNameFieldIn. */
    function liveNameField(component) {
        if (!component || !component.query) return null;
        var fields = component.query("#omicNameField");
        if (!fields.length) return null;
        var live = fields.filter(function (f) { return !(f.isDisabled ? f.isDisabled() : f.disabled); });
        var pool = live.length ? live : fields;
        for (var i = 0; i < pool.length; i++) {
            var value = pool[i].getValue && pool[i].getValue();
            if (value && String(value).trim()) return pool[i];
        }
        return pool[0];
    }

    function fileInputIn(component, itemId) {
        var selector = component && component.queryById ? component.queryById(itemId) : null;
        var field = selector && selector.queryById ? selector.queryById("fileField") : null;
        return field && field.fileInputEl ? field.fileInputEl.dom : null;
    }

    /* The LIVE file input for a slot, by the field name that never changes.
     *
     * The DOM input does not survive a Step 1 submit: step1OnFormSubmitHandler
     * moves the inputs into a temporary form and ExtJS's filefield re-creates
     * fileInputEl afterwards. A drawer opened from the error dialog therefore
     * holds a DETACHED twin of every input it was handed, and setFile on that
     * twin writes to nothing the form will submit -- the card keeps its
     * original file and the run fails again. Measured on the reported job:
     * after "Submit anyway", the harmonised metabolomics file went to the twin
     * and the live card still held the 14-column upload. So the input is
     * resolved by name at the moment it is written, not captured when the
     * drawer opens. */
    function liveInputFor(fieldName) {
        if (!fieldName || !window.Ext || !Ext.ComponentQuery) return null;
        var field = Ext.ComponentQuery.query("filefield[name=" + fieldName + "]")[0];
        return field && field.fileInputEl ? field.fileInputEl.dom : null;
    }

    var PANEL_TYPE = {
        "gene expression": "geneexpression", "proteomics": "proteomics",
        "metabolomics": "metabolomics", "mirna-seq": "mirnaseq", "dnase-seq": "dnaseseq",
        "transcription factor": "transcriptionfactor"
    };

    /*
     * Adds a sibling omic box of the same type and puts a converted table in
     * it. Returns false when the Step 1 view is not reachable, so the caller
     * can fall back to a download rather than lose the table.
     */
    function addAsNewOmic(omicType, omicName, values, relevant) {
        try {
            var view = window.application && application.getMainView
                ? application.getMainView().getSubView("PA_Step1JobView") : null;
            if (!view || !view.addNewOmicSubmittingPanel) return false;
            var panel = view.addNewOmicSubmittingPanel(
                PANEL_TYPE[String(omicType).toLowerCase()] || "otheromic");
            if (!panel || !panel.getComponent) return false;
            var component = panel.getComponent();
            var fill = function () {
                var nameField = liveNameField(component);
                if (nameField && nameField.setValue) nameField.setValue(omicName);
                var main = fileInputIn(component, "mainFileSelector");
                if (main) setFile(main, values.bytes, values.name);
                var secondary = fileInputIn(component, "secondaryFileSelector");
                if (relevant && secondary) setFile(secondary, relevant.bytes, relevant.name);
            };
            if (component.rendered) fill(); else component.on("afterrender", fill, null, { single: true });
            return true;
        } catch (e) {
            if (window.console && console.warn) console.warn("[inputformat] add omic failed", e);
            return false;
        }
    }

    /* ------------------------------------------------------------------ *
     * Reading the manifest
     * ------------------------------------------------------------------ */

    function describeOutputs(result) {
        var manifest = result.manifest || api().parseManifest(result.outputs) || {};
        var declared = {};
        (manifest.files || []).forEach(function (f) { if (f && f.name) declared[f.name] = f; });
        var files = Object.keys(result.outputs || {})
            .filter(function (n) { return n !== "manifest.json"; })
            .map(function (name) {
                var info = declared[name] || {};
                var report = (result.reports || {})[name] || {};
                var bytes = result.outputs[name];
                var text = decode(bytes);
                var lines = text.split(/\r?\n/).filter(function (l) { return l.trim() !== ""; });
                var rows = lines.slice(0, 7).map(function (l) { return l.split("\t"); });
                var header = rows.length && !api().isPythonFloat(String(rows[0][1] || "")) ? rows[0] : null;
                return {
                    name: name, bytes: bytes, role: info.role || report.role || "values",
                    label: info.label || name, source: info.source || "", note: info.note || "",
                    kept: info.columns_kept || [], dropped: info.columns_dropped || [],
                    rowsIn: info.rows_in, rowsOut: info.rows_out,
                    recommended: !!info.recommended, relevantFor: info.relevant_for || null,
                    nRows: (report.summary && report.summary.nRows) || (lines.length - (header ? 1 : 0)),
                    nCols: (report.summary && report.summary.nCols) || (rows[0] ? rows[0].length : 0),
                    preview: { header: header, rows: header ? rows.slice(1, 6) : rows.slice(0, 5) }
                };
            });
        return { manifest: manifest, files: files,
                 values: files.filter(function (f) { return f.role === "values"; }),
                 lists: files.filter(function (f) { return f.role === "relevant"; }),
                 others: files.filter(function (f) { return f.role !== "values" && f.role !== "relevant"; }) };
    }

    /* ------------------------------------------------------------------ *
     * Shared fragments
     * ------------------------------------------------------------------ */

    function previewTable(preview, maxCols) {
        var wrap = el("div", "pa-convert-preview");
        var table = el("table");
        var limit = maxCols || 7;
        var cols = preview.header ? preview.header.length : (preview.rows[0] || []).length;
        var shown = Math.min(cols, limit);
        if (preview.header) {
            var tr = el("tr");
            preview.header.slice(0, shown).forEach(function (h) { tr.appendChild(el("th", null, h)); });
            if (cols > shown) tr.appendChild(el("th", "pa-convert-more", "+" + (cols - shown)));
            table.appendChild(tr);
        }
        preview.rows.forEach(function (r) {
            var tr = el("tr");
            r.slice(0, shown).forEach(function (c, i) { tr.appendChild(el("td", i === 0 ? "pa-convert-id" : null, c)); });
            if (cols > shown) tr.appendChild(el("td", "pa-convert-more", "…"));
            table.appendChild(tr);
        });
        wrap.appendChild(table);
        return wrap;
    }

    function iconButton(glyph, title, onClick) {
        var b = el("button", "pa-convert-iconbtn");
        b.type = "button";
        b.title = title;
        b.setAttribute("aria-label", title);
        b.innerHTML = svg(glyph);
        b.addEventListener("click", onClick);
        return b;
    }

    /*
     * "What the AI sees": the profile, rendered. This is the literal payload
     * that describes the file to the model -- names, kinds, counts, example
     * rows -- so a user who wants to check the privacy claim can.
     */
    function renderAnatomy(profile, fileName, who) {
        var card = el("section", "pa-convert-anatomy");
        card.setAttribute("aria-label", "What the agent sees");
        var head = el("div", "pa-convert-anatomy-head");
        head.appendChild(el("p", "pa-convert-eyebrow", "What the agent sees"));
        var meta = el("div", "pa-convert-anatomy-meta");
        var chars = profile.description_chars ? fmtInt(profile.description_chars) + " characters" : "structure only";
        meta.textContent = chars + (who ? " · sent to " + who : "");
        head.appendChild(meta);
        card.appendChild(head);

        var tables = profile.tables || [];
        var lead = el("p", "pa-convert-anatomy-lead");
        var container = profile.container === "workbook"
            ? "a workbook with " + plural(tables.length, "sheet")
            : (profile.separator ? profile.separator + "-separated text" : "a text table") +
              (profile.encoding ? ", " + String(profile.encoding).toUpperCase() : "") +
              (profile.preamble_lines ? ", " + plural(profile.preamble_lines, "preamble line") + " skipped" : "");
        lead.innerHTML = "<b></b> · " + container + ". Only what is listed here goes to the model; " +
                         "the measurements stay in this browser.";
        lead.querySelector("b").textContent = fileName;
        card.appendChild(lead);

        if (profile.parse_error) {
            var err = el("p", "pa-convert-anatomy-lead");
            err.textContent = "It could not be parsed as a table yet: " + profile.parse_error;
            card.appendChild(err);
        }

        var MAX_TABLES = 6, MAX_COLS = 28;
        tables.slice(0, MAX_TABLES).forEach(function (t) {
            var block = el("div", "pa-convert-table");
            var th = el("div", "pa-convert-table-head");
            th.appendChild(el("span", "pa-convert-table-name",
                t.name === "(single table)" ? "The table" : t.name));
            if (t.empty) {
                th.appendChild(el("span", "pa-convert-table-note", "empty"));
                block.appendChild(th);
                card.appendChild(block);
                return;
            }
            var rows = t.exact ? t.exact.data_rows : t.sampled_rows;
            th.appendChild(el("span", "pa-convert-table-dims",
                fmtInt(rows) + " rows × " + fmtInt(t.n_columns) + " columns" +
                (t.exact ? "" : " (sampled)")));
            if (t.header_row !== null && t.header_row !== undefined && t.header_row > 0) {
                th.appendChild(el("span", "pa-convert-table-note", "header on row " + (t.header_row + 1)));
            } else if (t.header_row === null || t.header_row === undefined) {
                th.appendChild(el("span", "pa-convert-table-note", "no header row"));
            }
            block.appendChild(th);

            var idIndex = {};
            ((t.exact && t.exact.id_candidates) || []).forEach(function (c) { idIndex[c.index] = c; });
            var strip = el("div", "pa-convert-colstrip");
            var cols = t.columns || [];
            cols.slice(0, MAX_COLS).forEach(function (c) {
                var cand = idIndex[c.index];
                var chip = el("span", "pa-convert-col " +
                    (cand ? "pa-convert-col-id" : (c.kind === "numeric" ? "pa-convert-col-numeric" : "pa-convert-col-text")));
                chip.appendChild(document.createTextNode(c.name));
                var title = c.name + " — " + (cand ? "identifier candidate" : c.kind);
                if (c.kind === "numeric" && c.min !== undefined) title += ", " + c.min + " to " + c.max;
                if (c.kind === "text" && c.distinct !== undefined) title += ", " + fmtInt(c.distinct) + " distinct";
                if (cand && cand.duplicates > 0) {
                    chip.appendChild(el("span", "pa-convert-col-dup", "×" + fmtInt(cand.duplicates) + " dup"));
                    title += ", " + fmtInt(cand.duplicates) + " repeated";
                }
                chip.title = title;
                strip.appendChild(chip);
            });
            var hidden = (t.n_columns || cols.length) - Math.min(cols.length, MAX_COLS);
            if (hidden > 0) strip.appendChild(el("span", "pa-convert-col pa-convert-col-more", "+" + fmtInt(hidden) + " more"));
            block.appendChild(strip);

            if (t.column_families && t.column_families.length) {
                var fam = el("p", "pa-convert-families");
                fam.innerHTML = t.column_families.slice(0, 3).map(function (f) {
                    return fmtInt(f.count) + " columns share <code></code>";
                }).join(" · ");
                var codes = fam.querySelectorAll("code");
                t.column_families.slice(0, 3).forEach(function (f, i) { codes[i].textContent = f.family; });
                block.appendChild(fam);
            }
            card.appendChild(block);
        });
        if (tables.length > MAX_TABLES) {
            card.appendChild(el("p", "pa-convert-families", "+" + (tables.length - MAX_TABLES) + " more sheets described the same way."));
        }

        if (tables.some(function (t) { return !t.empty; })) {
            var legend = el("div", "pa-convert-legend");
            [["is-id", "identifier candidate"], ["is-numeric", "numeric"], ["", "text"]].forEach(function (pair) {
                var s = el("span");
                s.appendChild(el("i", pair[0] || null));
                s.appendChild(document.createTextNode(pair[1]));
                legend.appendChild(s);
            });
            card.appendChild(legend);

            var first = tables.filter(function (t) { return t.first_rows && t.first_rows.length; })[0];
            if (first) {
                var rowsBox = el("details", "pa-convert-rows");
                rowsBox.appendChild(el("summary", null, "Example rows it was shown (" + first.first_rows.length +
                    (tables.length > 1 ? ", from " + first.name : "") + ")"));
                rowsBox.appendChild(previewTable({ header: null, rows: first.first_rows.slice(0, 6) }, 8));
                card.appendChild(rowsBox);
            }
        }
        return card;
    }

    /* ------------------------------------------------------------------ *
     * The sheet
     * ------------------------------------------------------------------ */

    function openDrawer(input, file, fieldName, context) {
        var overlay = el("div", "pa-convert-overlay");
        var panel = el("section", "pa-convert-panel");
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-modal", "true");
        panel.setAttribute("aria-label", "Convert " + file.name + " with the PaintOmics AI agent");
        panel.tabIndex = -1;

        // The destination omic's hue, so the sheet and its chosen result wear
        // the same bar the omic card does.
        var card = omicCardOf(input);
        var titleBar = card && card.querySelector(".omicboxTitle");
        var hue = titleBar ? getComputedStyle(titleBar).getPropertyValue("--pa-omic-color").trim() : "";
        if (hue) panel.style.setProperty("--cv-omic", hue);

        var omicLabel = (context && context.omicType && context.omicType !== "unknown") ? context.omicType : "";
        var speciesLabel = (context && context.species && context.species !== "unknown") ? context.species : "";

        /*
         * Several files at once: the values files of ONE job that are each
         * valid and disagree with each other on their number of conditions
         * (see openHarmoniseDrawer). Every target keeps its own input element,
         * file and sandbox key; the sheet is opened on the first of them.
         */
        var harmonise = (context && context.harmonise) || null;
        var targets = harmonise ? harmonise.targets.map(function (t, i) {
            return Object.assign({}, t, { key: safeName(t.file.name) || ("input" + i), bytes: null });
        }) : [];
        var seenKeys = {};
        targets.forEach(function (t, i) {
            // Two uploads may share a name; the sandbox needs distinct paths.
            if (seenKeys[t.key]) t.key = i + "_" + t.key;
            seenKeys[t.key] = true;
        });

        // ---- header -------------------------------------------------------
        var header = el("header", "pa-convert-header");
        var mark = el("span", "pa-convert-mark");
        mark.innerHTML = typeof window.getAIMark === "function" ? window.getAIMark() : svg("thinking");
        header.appendChild(mark);
        var titles = el("div", "pa-convert-titles");
        titles.appendChild(el("h2", "pa-convert-title", harmonise
            ? "Make the omics agree with the PaintOmics AI agent"
            : "Convert with the PaintOmics AI agent"));
        var subject = el("p", "pa-convert-subject");
        if (harmonise) {
            targets.forEach(function (t) {
                subject.appendChild(el("span", "pa-convert-filename", t.file.name));
            });
        } else {
            subject.appendChild(el("span", "pa-convert-filename", file.name));
        }
        if (omicLabel) subject.appendChild(el("span", "pa-convert-omic", omicLabel));
        if (speciesLabel) subject.appendChild(el("span", "pa-convert-species", speciesLabel));
        titles.appendChild(subject);
        header.appendChild(titles);
        var close = el("button", "pa-convert-close", "✕");
        close.type = "button";
        close.setAttribute("aria-label", "Close and cancel the conversion");
        header.appendChild(close);
        panel.appendChild(header);

        // ---- status band --------------------------------------------------
        var status = el("div", "pa-convert-status");
        var stages = el("ol", "pa-convert-stages");
        stages.setAttribute("aria-label", "Stages");
        var stageEls = {};
        STAGES.forEach(function (s) {
            var li = el("li", "pa-convert-stage", s.label);
            li.dataset.state = "todo";
            stageEls[s.key] = li;
            stages.appendChild(li);
        });
        status.appendChild(stages);
        var now = el("div", "pa-convert-now");
        var nowText = el("div", "pa-convert-now-text", "Starting the Python sandbox");
        nowText.setAttribute("role", "status");
        nowText.setAttribute("aria-live", "polite");
        var nowMeta = el("div", "pa-convert-now-meta");
        now.appendChild(nowText);
        now.appendChild(nowMeta);
        status.appendChild(now);
        panel.appendChild(status);

        // ---- body ---------------------------------------------------------
        var body = el("div", "pa-convert-body");
        var anatomyHost = el("div", "pa-convert-anatomyhost");
        body.appendChild(anatomyHost);
        var timeline = el("ol", "pa-convert-timeline");
        timeline.setAttribute("aria-label", "What the agent did");
        body.appendChild(timeline);
        var reviewHost = el("div", "pa-convert-reviewhost");
        body.appendChild(reviewHost);
        panel.appendChild(body);

        // ---- dock: composer, then the decision ----------------------------
        var dock = el("div", "pa-convert-dock");
        var composer = el("form", "pa-convert-composer");
        var composerInput = el("textarea", "pa-convert-composer-input");
        composerInput.rows = 2;
        composerInput.setAttribute("aria-label", "Instruction for the agent");
        var composerSend = el("button", "pa-convert-composer-send", "Revise");
        composerSend.type = "submit";
        composer.appendChild(composerInput);
        composer.appendChild(composerSend);
        dock.appendChild(composer);
        var composerHint = el("p", "pa-convert-composer-hint");
        composerHint.innerHTML = "Revises the script and re-checks it; your data stays here. " +
                                 "<kbd>Enter</kbd> sends · <kbd>Shift</kbd>+<kbd>Enter</kbd> new line.";
        dock.appendChild(composerHint);
        var actions = el("footer", "pa-convert-actions");
        dock.appendChild(actions);
        panel.appendChild(dock);

        overlay.appendChild(panel);
        document.body.appendChild(overlay);
        requestAnimationFrame(function () { overlay.classList.add("pa-convert-open"); panel.focus(); });

        // The textarea grows with what is typed, up to the stylesheet's cap.
        composerInput.addEventListener("input", function () {
            composerInput.style.height = "auto";
            composerInput.style.height = Math.min(200, composerInput.scrollHeight) + "px";
        });

        // ---- state --------------------------------------------------------
        var startedAt = Date.now();
        var currentStep = null;
        var running = false;
        var cancelled = false;
        var finished = false;
        var last = null;                     // the latest agent result
        var pendingAnswer = null;            // resolver while a question is open
        var pendingCode = null;              // code arrives before the step that runs it
        var attemptNow = 1, attemptMax = 0;
        var providerName = "";
        var profileShown = null;

        function syncMeta() {
            var bits = [];
            if (attemptMax > 1 && (attemptNow > 1 || running)) bits.push("<b>attempt " + attemptNow + "</b> of " + attemptMax);
            bits.push(fmtSeconds(Date.now() - startedAt));
            nowMeta.innerHTML = bits.join(" · ");
        }

        var timer = setInterval(function () {
            if (finished) return;
            syncMeta();
            if (currentStep && currentStep.__time && !currentStep.__frozen) {
                currentStep.__time.textContent = fmtSeconds(Date.now() - currentStep.__t0);
            }
        }, 1000);

        function setStage(key, state) {
            var reached = false;
            STAGES.forEach(function (s) {
                var li = stageEls[s.key];
                if (s.key === key) {
                    li.dataset.state = state || "current";
                    reached = true;
                } else if (!reached) {
                    li.dataset.state = "done";
                } else {
                    li.dataset.state = "todo";
                }
            });
        }

        function setNow(text) { nowText.textContent = text; syncMeta(); }

        function freezeCurrent() {
            if (!currentStep) return;
            currentStep.classList.remove("is-current");
            if (currentStep.__time && !currentStep.__frozen) {
                var spent = Date.now() - currentStep.__t0;
                currentStep.__time.textContent = spent >= 400 ? fmtSeconds(spent) : "";
                currentStep.__frozen = true;
            }
        }

        function addStep(event) {
            freezeCurrent();
            var li = el("li", "pa-convert-event is-current");
            li.dataset.phase = event.phase;
            var node = el("span", "pa-convert-node");
            node.innerHTML = svg(event.phase);
            li.appendChild(node);
            var content = el("div", "pa-convert-event-body");
            var head = el("div", "pa-convert-event-head");
            head.appendChild(el("span", "pa-convert-event-title", event.title));
            if (event.attempt > 1 && event.phase !== "user") {
                head.appendChild(el("span", "pa-convert-attempt", "attempt " + event.attempt + "/" + event.maxAttempts));
            }
            var time = el("span", "pa-convert-event-time", "");
            head.appendChild(time);
            content.appendChild(head);
            if (event.detail) {
                // The validator's report is one failure per line; keep them.
                var lines = String(event.detail).split("\n").filter(function (l) { return l.trim(); });
                if (event.phase === "validating" && lines.length && /Not accepted/i.test(event.title)) {
                    var verdict = el("div", "pa-convert-verdict");
                    var ul = el("ul");
                    lines.forEach(function (l) { ul.appendChild(el("li", null, l)); });
                    verdict.appendChild(ul);
                    content.appendChild(verdict);
                    li.classList.add("is-rejected");
                } else {
                    content.appendChild(el("p", "pa-convert-event-detail", event.detail));
                }
            }
            if (/failed/i.test(event.title) && event.phase === "running") li.classList.add("is-rejected");
            li.appendChild(content);
            li.__t0 = Date.now();
            li.__time = time;
            li.__body = content;
            currentStep = li;
            timeline.appendChild(li);
            if (pendingCode && event.phase === "running") { attachCode(pendingCode); pendingCode = null; }
            body.scrollTop = body.scrollHeight;
            return li;
        }

        function disclosureRow(step) {
            var row = step.__body.querySelector(".pa-convert-disclosures");
            if (!row) { row = el("div", "pa-convert-disclosures"); step.__body.appendChild(row); }
            return row;
        }

        function addDisclosure(step, label, small, kind, text) {
            var row = disclosureRow(step);
            var toggle = el("button", "pa-convert-disclose");
            toggle.type = "button";
            toggle.setAttribute("aria-expanded", "false");
            toggle.appendChild(document.createTextNode(label));
            if (small) { toggle.appendChild(document.createTextNode(" ")); toggle.appendChild(el("small", null, small)); }
            var box = el("div", "pa-convert-codebox");
            box.hidden = true;
            var head = el("div", "pa-convert-codebox-head");
            var kindLabel = el("span"); kindLabel.innerHTML = "<b>" + kind + "</b>";
            head.appendChild(kindLabel);
            var copy = el("button", "pa-convert-copy", "Copy");
            copy.type = "button";
            copy.addEventListener("click", function () {
                if (!navigator.clipboard) return;
                navigator.clipboard.writeText(text).then(function () {
                    copy.textContent = "Copied";
                    setTimeout(function () { copy.textContent = "Copy"; }, 1400);
                });
            });
            head.appendChild(copy);
            box.appendChild(head);
            var pre = el("pre", "pa-convert-code");
            pre.appendChild(el("code", null, text));
            box.appendChild(pre);
            toggle.addEventListener("click", function () {
                box.hidden = !box.hidden;
                toggle.setAttribute("aria-expanded", String(!box.hidden));
            });
            row.appendChild(toggle);
            // The box goes after the row so every disclosure's content stacks
            // below the buttons rather than between them.
            step.__body.appendChild(box);
        }

        function attachCode(code) {
            if (!currentStep) { pendingCode = code; return; }
            var lines = String(code).split("\n").length;
            addDisclosure(currentStep, "Script", lines + " lines", "python · runs in your browser", code);
        }

        function attachOutput(stdout) {
            if (!currentStep || !stdout || !String(stdout).trim()) return;
            var text = String(stdout).trim();
            addDisclosure(currentStep, "Output", text.split("\n").length + " lines", "what the script printed", text);
        }

        function setComposerState(state) {
            // "idle": revise the result; "asking": answer in your own words; "busy": wait.
            composerInput.disabled = state === "busy";
            composerSend.disabled = state === "busy";
            composerSend.textContent = state === "asking" ? "Answer" : "Revise";
            composerInput.placeholder = state === "asking"
                ? "…or answer in your own words"
                : state === "busy"
                    ? "The agent is working — you can steer it once it has something to show."
                    : "Tell the agent what to change — “keep the flagged genes”, “use the reads sheet, not TPM”, “column A is a KEGG ID”";
            composer.classList.toggle("pa-convert-composer-busy", state === "busy");
        }

        function askUser(question) {
            return new Promise(function (resolve) {
                var qcard = el("div", "pa-convert-question");
                qcard.appendChild(el("p", "pa-convert-question-text", question.text));
                var opts = el("div", "pa-convert-options");
                var settle = function (answer) {
                    qcard.querySelectorAll("button").forEach(function (x) { x.disabled = true; });
                    qcard.appendChild(el("div", "pa-convert-answer", "You chose: " + answer));
                    pendingAnswer = null;
                    setComposerState("busy");
                    setNow("Thanks — continuing");
                    resolve(answer);
                };
                (question.options && question.options.length
                    ? question.options : ["Use your best judgement"]).forEach(function (opt) {
                    var b = el("button", "pa-convert-option", opt);
                    b.type = "button";
                    b.addEventListener("click", function () {
                        b.classList.add("pa-convert-option-chosen");
                        settle(opt);
                    });
                    opts.appendChild(b);
                });
                qcard.appendChild(opts);
                qcard.appendChild(el("p", "pa-convert-question-hint", "Or answer in your own words in the box below."));
                currentStep.__body.appendChild(qcard);
                body.scrollTop = body.scrollHeight;
                pendingAnswer = settle;
                setComposerState("asking");
                setNow("Waiting for your answer");
                composerInput.focus();
            });
        }

        var sandbox = createSandbox(function (msg) { setNow(msg); });

        function shutdown() {
            clearInterval(timer);
            try { sandbox.destroy(); } catch (e) { /* already gone */ }
            overlay.classList.remove("pa-convert-open");
            setTimeout(function () {
                if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            }, 200);
        }

        function cancel() { cancelled = true; shutdown(); }
        close.addEventListener("click", cancel);
        // Escape closes once there is nothing in flight; mid-run it would throw
        // away a minute of work on a reflex.
        panel.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !running && !pendingAnswer) cancel();
        });

        var bytes = null;
        var fileKey = safeName(file.name) || "input";

        function allBytes() {
            var f = {};
            targets.forEach(function (t) { f[t.key] = t.bytes; });
            return f;
        }

        /* What the agent loop needs to know about each file: where it sits
           in the sandbox, which omic it is, and how wide the format check
           measured it. The profiles are added by the loop itself. */
        function harmoniseInputs() {
            return targets.map(function (t) {
                return { key: t.key, path: "/work/" + t.key, omic: t.omic || "",
                         fileName: t.file.name, role: "values", conditions: t.conditions };
            });
        }

        /* The facts the model is told up front: the refusal, when there was
           one, and every file's width as measured here -- so it does not have
           to infer from the profiles what the job was refused for. The rule
           itself and how to satisfy it live in the system prompt (MAKING THE
           OMICS AGREE). */
        function harmoniseInstructions() {
            var lines = [];
            if (harmonise.serverSaid) {
                lines.push("PaintOmics refused this job when the analysis ran. It said: " + harmonise.serverSaid);
            }
            lines.push("These are the values files of one run, and PaintOmics needs every one of " +
                       "them to have the same number of condition columns. As the format check " +
                       "measured them: " + targets.map(function (t) {
                           return (t.omic ? t.omic + " (" + t.file.name + ")" : t.file.name) + " has " +
                                  (t.conditions == null ? "an unknown number of condition columns"
                                                        : plural(t.conditions, "condition column"));
                       }).join("; ") + ".");
            return lines;
        }

        function onEvent(event) {
            if (cancelled) return;
            if (event.type === "step") {
                attemptNow = event.attempt || attemptNow;
                attemptMax = event.maxAttempts || attemptMax;
                addStep(event);
                setStage(PHASE_STAGE[event.phase] || "plan", event.phase === "done" ? "ready" : "current");
                setNow(event.title);
            } else if (event.type === "code") {
                pendingCode = event.code;
            } else if (event.type === "output") {
                attachOutput(event.stdout);
            } else if (event.type === "profile" && event.profile && event.input) {
                // One "what the AI sees" card per input, in the order read.
                profileShown = profileShown || event.profile;
                anatomyHost.appendChild(renderAnatomy(event.profile,
                    (event.input.omic ? event.input.omic + " — " : "") + event.input.fileName, providerName));
            } else if (event.type === "profile" && event.profile && !profileShown) {
                profileShown = event.profile;
                anatomyHost.innerHTML = "";
                anatomyHost.appendChild(renderAnatomy(event.profile, file.name, providerName));
            } else if (event.type === "failed") {
                setStage(PHASE_STAGE.profiling, "failed");
            }
        }

        provider().then(function (p) {
            if (!p || !p.success) return;
            providerName = (p.operator || p.provider || "") + (p.host ? " (" + p.host + ")" : "");
            anatomyHost.querySelectorAll(".pa-convert-anatomy-meta").forEach(function (meta) {
                if (!profileShown) return;
                var chars = profileShown.description_chars ? fmtInt(profileShown.description_chars) + " characters" : "structure only";
                meta.textContent = chars + " · sent to " + providerName;
            });
        });

        async function runOnce(extra) {
            running = true;
            finished = false;
            setComposerState("busy");
            actions.innerHTML = "";
            reviewHost.innerHTML = "";
            var result;
            try {
            result = await api().runAgent(Object.assign({
                api: api(),
                sandbox: sandbox,
                transport: serverTransport(),
                files: harmonise ? allBytes() : (function () { var f = {}; f[fileKey] = bytes; return f; })(),
                inputs: harmonise ? harmoniseInputs() : undefined,
                inputPath: "/work/" + fileKey,
                fileName: file.name,
                omicType: (context && context.omicType) || "unknown",
                species: (context && context.species) || "unknown",
                goal: harmonise
                    ? "These values files belong to one PaintOmics run and disagree on their number " +
                      "of condition columns. Rewrite them so that they agree, keeping as much of the " +
                      "data as possible."
                    : "Convert this file into the format PaintOmics accepts, keeping every measurement it holds.",
                /* When the SERVER is the one that refused, hand the agent what
                   it said. Without this the agent re-reads a file the client
                   checker already considers valid and finds nothing, because
                   the fault is one only the server can see -- a sample name
                   that does not line up with the design file, an identifier
                   space that does not match the other omics.

                   The job-level conversion is the OTHER answer to the same
                   observation: when the disagreement is between values files,
                   every one of them is in the sandbox and the model rewrites
                   them together, so the single-file brief below -- "you can
                   rewrite ONLY the file you were given" -- does not apply. */
                instructions: harmonise ? harmoniseInstructions() : (input && input.__paServerSaid)
                    ? ["PaintOmics refused this job when the analysis ran. It said: "
                       + input.__paServerSaid]
                        .concat(input.__paSiblings ? [input.__paSiblings] : [])
                        /* The limit of what you can DO, stated separately from
                           what you can SEE.
                         *
                           The sandbox receives exactly one file (files:
                           {fileKey: bytes}), so the other files are readable
                           context and nothing more. Without this the agent
                           offered "use the contrast as-is and adjust the design
                           to match it" -- an action it cannot perform -- then
                           edited the one file it holds, reported success, and
                           the run failed in the same place. Reported as "the AI
                           said it fixed the problem and it fails again".

                           Measured, on this job: MORE intersects sample names
                           across target, condition and every regulator file, so
                           renaming this file's column to match the target
                           leaves the design disagreeing, and renaming it to
                           match the design leaves the target disagreeing. Both
                           give 0 common samples. No edit to ONE file can
                           satisfy a three-way intersection. */
                        .concat(["You can rewrite ONLY the file you were given. "
                                 + "The other files listed above are read-only "
                                 + "context -- you cannot edit them, and you must "
                                 + "not offer to. If the remedy needs a different "
                                 + "file changed, or needs data that is not in any "
                                 + "of them, say which file and what has to change "
                                 + "and let the user do it. A fix that only makes "
                                 + "THIS file self-consistent, while the others "
                                 + "still disagree, is not a fix: it will fail "
                                 + "again in the same place."])
                        .concat(["This file may well be valid on its own -- the "
                                 + "format check passed it. Judge it against the "
                                 + "other files listed above: what has to agree "
                                 + "between them, and what does not.",
                                 /* The guard, added after watching the agent
                                    do exactly this: given the siblings, it
                                    renamed a column so the two VALUES files
                                    agreed with each other, ignored the design
                                    file entirely, and reported success. The
                                    job would have failed again in the same
                                    place. Cross-file context makes a confident
                                    wrong answer as easy as a right one, so the
                                    authority has to be named. */
                                 "The design/conditions file is the AUTHORITY on "
                                 + "sample names. Never rename a column to make two "
                                 + "values files agree with each other: rename only "
                                 + "to a name that actually appears in the design "
                                 + "file. If the values files hold CONTRASTS "
                                 + "(names like A_vs_B) while the design lists "
                                 + "individual samples, they cannot be reconciled by "
                                 + "renaming at all. In that case ASK THE USER rather "
                                 + "than converting: state what each file holds, why "
                                 + "the two cannot be matched, and that this analysis "
                                 + "needs one column per sample. Asking is a real "
                                 + "answer here; a converted file that still will not "
                                 + "run is not."])
                    : undefined,
                ask: askUser,
                onEvent: onEvent
            }, extra || {}));
            } finally {
                running = false;
            }
            if (cancelled) return result;
            last = result;
            if (result.profile && !profileShown) onEvent({ type: "profile", profile: result.profile });
            renderReview(result);
            setComposerState("idle");
            return result;
        }

        async function revise(instruction) {
            // `last` is null when no run has finished yet -- the first turn
            // was refused, or the gateway did not answer. The instruction is
            // still a start: the profile is kept, the script is written fresh.
            var base = last || {};
            addStep({ phase: "user", title: "Your instruction", detail: instruction });
            setStage("plan", "current");
            setNow(last ? "Revising with your instruction" : "Starting again with your instruction");
            var instructions = (base.instructions || []).concat([instruction]);
            await attempt({
                profile: base.profile || profileShown || undefined,
                inputProfiles: base.inputProfiles || undefined,
                instructions: instructions,
                answers: base.answers || {},
                accepted: base.code ? { code: base.code, manifest: base.manifest } : null
            });
        }

        composer.addEventListener("submit", function (event) {
            event.preventDefault();
            var text = composerInput.value.trim();
            if (!text) return;
            composerInput.value = "";
            composerInput.style.height = "";
            if (pendingAnswer) { pendingAnswer(text); return; }
            if (running) return;
            revise(text);
        });
        composerInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (composer.requestSubmit) composer.requestSubmit(); else composer.dispatchEvent(new Event("submit"));
            }
        });

        return (async function () {
            try {
                setComposerState("busy");
                setStage("read", "current");
                // Ask before booting. A server with the converter switched off
                // refuses the first TURN, and finding that out after the
                // sandbox had started and the file had been read left the user
                // with a timeline, a disabled box and nothing to click:
                // thirteen attempts on paintomics.org on 2026-09-08, then an
                // email. A status that cannot be fetched is not "off" -- the
                // turn itself says so if it must.
                var info = await provider();
                if (info && info.success && info.inputConverter === false) {
                    renderUnavailable(null);
                    return { ok: false, stage: "disabled" };
                }
                // The boot is a real step that takes a few seconds; a blank
                // timeline for that long reads as nothing happening.
                addStep({ phase: "profiling", title: "Starting the Python sandbox",
                          detail: "An isolated interpreter with no access to this page or the network." });
                await sandbox.boot();
                setNow(harmonise ? "Reading your files" : "Reading your file");
                for (var t = 0; t < targets.length; t++) {
                    targets[t].bytes = new Uint8Array(await targets[t].file.arrayBuffer());
                }
                bytes = new Uint8Array(await file.arrayBuffer());
                return await attempt();
            } catch (err) {
                if (!cancelled) failed(err);
                return { ok: false };
            }
        })();

        /* ---- one attempt, however it ends ----------------------------- */

        /*
         * Every way into the agent -- the first run, a revision from the box,
         * Try again -- goes through here, so a transport error ends in the
         * same card each time instead of in an unhandled rejection that
         * leaves the sheet frozen mid-stage.
         */
        async function attempt(extra) {
            try {
                return await runOnce(extra);
            } catch (err) {
                if (!cancelled) failed(err);
                return { ok: false };
            }
        }

        /*
         * Something other than the agent's own judgement stopped the run:
         * the server refused the turn, the gateway did not answer, the
         * sandbox would not boot. The file is not known to be wrong. Two
         * cases: a server with no converter, which no retry can change, and
         * everything else, which one click can.
         */
        function failed(err) {
            var message = String(err && err.message || err);
            if (CONVERTER_OFF.test(message)) { renderUnavailable(message); return; }
            addStep({ phase: "failed", title: "The conversion could not finish", detail: message });
            // renderFailure marks the stage that was lit; marking it here
            // first left none lit, and its fallback then failed Check for a
            // turn that died in Plan.
            renderFailure(null, message);
            // Idle, not busy: the box is one of the two ways to try again.
            // It used to stay disabled whenever the FIRST run failed, under a
            // card that said "tell the agent in the box below".
            setComposerState("idle");
        }

        /* ---- review ---------------------------------------------------- */

        function chips(label, names, cls) {
            if (!names || !names.length) return null;
            var frag = document.createDocumentFragment();
            frag.appendChild(el("span", "pa-convert-columns-label", label + " " + names.length));
            var box = el("div", "pa-convert-chips " + (cls || ""));
            names.slice(0, 12).forEach(function (n) { box.appendChild(el("span", "pa-convert-chip", n)); });
            if (names.length > 12) box.appendChild(el("span", "pa-convert-chip pa-convert-chip-more", "+" + (names.length - 12) + " more"));
            frag.appendChild(box);
            return frag;
        }

        function linkedRow(glyph, html, bytesToDownload, name) {
            var row = el("div", "pa-convert-linked");
            var icon = el("span", "pa-convert-linked-icon");
            icon.innerHTML = svg(glyph);
            row.appendChild(icon);
            var text = el("span");
            text.innerHTML = html;
            row.appendChild(text);
            row.appendChild(iconButton("download", "Download " + name, function () { download(bytesToDownload, name); }));
            return row;
        }

        function escapeHtml(s) {
            return String(s).replace(/[&<>"]/g, function (c) {
                return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
            });
        }

        /*
         * The review of a job-level conversion: one card per omic, in the
         * order of the form, each saying which file it started from, which
         * output replaces it, and whether the agent left it as it was. There
         * is nothing to choose between -- the point was to make the set
         * agree -- so the one decision is to use the set or not.
         */
        function renderHarmoniseReview(result) {
            setStage("review", "ready");
            setNow("Ready to review");

            var out = describeOutputs(result);
            var entries = ((out.manifest && out.manifest.files) || []).filter(function (f) { return f && f.name; });
            var forInput = {};
            entries.forEach(function (f) { if (f["for"]) forInput[String(f["for"])] = f; });
            var plan = targets.map(function (t) {
                var entry = forInput["/work/" + t.key] || forInput[t.key] || forInput[t.file.name] || null;
                var output = entry ? out.files.filter(function (f) { return f.name === entry.name; })[0] || null : null;
                return { target: t, entry: entry, output: output };
            });
            var ready = plan.filter(function (p) { return p.output; });

            var review = el("section", "pa-convert-review");
            review.setAttribute("aria-label", "Review the result");
            var head = el("div", "pa-convert-review-head");
            head.appendChild(el("h3", "pa-convert-review-title",
                ready.length === targets.length ? "The omics agree" : "Not every omic was answered"));
            head.appendChild(el("span", "pa-convert-review-count",
                plural(out.files.length, "file") + " · " + plural(result.attempts || 1, "attempt") + " · " + fmtSeconds(Date.now() - startedAt)));
            review.appendChild(head);
            if (out.manifest.summary) review.appendChild(el("p", "pa-convert-summary", out.manifest.summary));

            plan.forEach(function (p) {
                var unchanged = !!(p.entry && p.entry.unchanged);
                var rcard = el("article", "pa-convert-result pa-convert-result-chosen pa-convert-target");
                var rhead = el("header", "pa-convert-result-head");
                var labels = el("div", "pa-convert-result-titles");
                var title = el("div", "pa-convert-result-title");
                title.appendChild(el("span", "pa-convert-target-omic", p.target.omic || p.target.file.name));
                title.appendChild(document.createTextNode(" · " + p.target.file.name));
                if (p.output && !unchanged && p.output.name !== p.target.file.name) {
                    title.appendChild(el("span", "pa-convert-target-arrow", " → "));
                    title.appendChild(document.createTextNode(p.output.name));
                }
                labels.appendChild(title);
                var meta = p.output
                    ? [p.output.nRows != null ? plural(p.output.nRows, "row") : null,
                       p.output.nCols ? plural(p.output.nCols - 1, "condition") : null,
                       (p.output.rowsIn != null && p.output.rowsOut != null && p.output.rowsIn !== p.output.rowsOut)
                           ? fmtInt(p.output.rowsIn - p.output.rowsOut) + " rows left out" : null]
                      .filter(Boolean).join(" · ")
                    : "no file was produced for this omic";
                labels.appendChild(el("div", "pa-convert-result-meta", meta));
                rhead.appendChild(labels);
                if (p.output) {
                    rhead.appendChild(el("span", "pa-convert-badge", unchanged ? "Unchanged" : "Rewritten"));
                    rhead.appendChild(iconButton("download", "Download " + p.output.name, function () {
                        download(p.output.bytes, p.output.name);
                    }));
                }
                rcard.appendChild(rhead);
                if (p.output) {
                    rcard.appendChild(previewTable(p.output.preview));
                    var kept = chips("Kept", p.output.kept, "pa-convert-kept");
                    var dropped = chips("Left out", p.output.dropped, "pa-convert-dropped");
                    if (kept || dropped) {
                        var cols = el("div", "pa-convert-columns");
                        if (kept) cols.appendChild(kept);
                        if (dropped) cols.appendChild(dropped);
                        rcard.appendChild(cols);
                    }
                    if (p.output.note) rcard.appendChild(el("p", "pa-convert-result-note", p.output.note));
                }
                review.appendChild(rcard);
            });

            var extras = out.files.filter(function (f) {
                return !plan.some(function (p) { return p.output === f; });
            });
            if (extras.length) {
                var also = el("div", "pa-convert-extras");
                also.appendChild(el("h4", "pa-convert-extras-title", "Also produced"));
                extras.forEach(function (f) {
                    also.appendChild(linkedRow(f.role === "relevant" ? "list" : "table",
                        "<b>" + escapeHtml(f.label) + "</b>" + (f.nRows != null ? " — " + plural(f.nRows, "row") : "") +
                        (f.note ? " (" + escapeHtml(f.note) + ")" : ""), f.bytes, f.name));
                });
                review.appendChild(also);
            }

            reviewHost.innerHTML = "";
            reviewHost.appendChild(review);

            var accept = el("button", "pa-convert-accept", "Use these " + plural(ready.length, "table"));
            accept.type = "button";
            var downloadAll = el("button", "pa-convert-dismiss", "Download all");
            downloadAll.type = "button";
            downloadAll.addEventListener("click", function () {
                out.files.forEach(function (f, i) { setTimeout(function () { download(f.bytes, f.name); }, i * 150); });
            });
            var dismiss = el("button", "pa-convert-dismiss is-quiet", "Cancel");
            dismiss.type = "button";
            dismiss.addEventListener("click", cancel);
            actions.innerHTML = "";
            actions.appendChild(dismiss);
            actions.appendChild(downloadAll);
            if (ready.length === targets.length) actions.appendChild(accept);

            accept.addEventListener("click", function () {
                ready.forEach(function (p) {
                    // A file the agent left as it was stays the user's own
                    // upload: no provenance to record, nothing to re-check.
                    if (p.entry && p.entry.unchanged) return;
                    // The LIVE input, resolved now: a submit between opening the
                    // drawer and this click will have replaced the DOM node.
                    var input = liveInputFor(p.target.fieldName) || p.target.input;
                    input.__paConverted = {
                        from: p.target.file.name, original: null, fieldName: p.target.fieldName,
                        label: p.output.label, attempts: result.attempts || 1, relevant: false,
                        harmonised: true
                    };
                    setFile(input, p.output.bytes, p.output.name);
                });
                shutdown();
            });
            body.scrollTop = review.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 10;
        }

        function renderReview(result) {
            finished = true;
            freezeCurrent();
            if (!result.ok) { renderFailure(result); return; }
            if (harmonise) { renderHarmoniseReview(result); return; }
            setStage("review", "ready");
            setNow("Ready to review");

            var out = describeOutputs(result);
            var review = el("section", "pa-convert-review");
            review.setAttribute("aria-label", "Review the result");
            var head = el("div", "pa-convert-review-head");
            head.appendChild(el("h3", "pa-convert-review-title", out.values.length > 1
                ? out.values.length + " tables ready" : "Converted"));
            var attemptsUsed = result.attempts || 1;
            head.appendChild(el("span", "pa-convert-review-count",
                plural(out.files.length, "file") + " · " + plural(attemptsUsed, "attempt") + " · " + fmtSeconds(Date.now() - startedAt)));
            review.appendChild(head);
            if (out.manifest.summary) review.appendChild(el("p", "pa-convert-summary", out.manifest.summary));

            var chosen = out.values.filter(function (f) { return f.recommended; })[0] || out.values[0];
            var addOthers = false;

            /* The file that belongs in the slot the drawer was opened from.
             *
             * Everything below used to key off `out.values`, and a conversion
             * that produces no values table has none -- so the review ended
             * with Cancel and "Download all", and the user had to save the
             * file to disk and pick it again through Browse. Reported on the
             * conversion that made this obvious: a MORE Conditions file, where
             * the agent read 24 rows of sample metadata and wrote exactly the
             * 0/1 experimental design PaintOmics wanted, named it design.tab,
             * and then offered no way to put it in the field the user had
             * clicked Convert on.
             *
             * Same shape as the rest of this family: code that enumerates omic
             * files knows the plain values case and forgets design,
             * associations and relevant-associations. The slot the user
             * started from already says which role is wanted, so match on it.
             */
            var slotRole = (context && context.slotRole) || "values";
            var forSlot = chosen ? null
                : (out.files.filter(function (f) { return f.role === slotRole; })[0] || null);

            out.values.forEach(function (f) {
                var rcard = el("article", "pa-convert-result" + (f === chosen ? " pa-convert-result-chosen" : ""));
                var rhead = el("header", "pa-convert-result-head");
                var pick = el("label", "pa-convert-pick");
                var radio = document.createElement("input");
                radio.type = "radio"; radio.name = "pa-convert-choice"; radio.checked = f === chosen;
                radio.setAttribute("aria-label", "Use " + f.label + " for this omic");
                radio.addEventListener("change", function () {
                    chosen = f;
                    review.querySelectorAll(".pa-convert-result").forEach(function (c) { c.classList.remove("pa-convert-result-chosen"); });
                    rcard.classList.add("pa-convert-result-chosen");
                    syncActions();
                });
                pick.appendChild(radio);
                var labels = el("div", "pa-convert-result-titles");
                labels.appendChild(el("div", "pa-convert-result-title", f.label));
                var meta = [f.source ? "from " + f.source : null,
                            f.nRows != null ? plural(f.nRows, "row") : null,
                            f.nCols ? plural(f.nCols - 1, "condition") : null,
                            (f.rowsIn != null && f.rowsOut != null && f.rowsIn !== f.rowsOut)
                                ? fmtInt(f.rowsIn - f.rowsOut) + " rows left out" : null]
                           .filter(Boolean).join(" · ");
                labels.appendChild(el("div", "pa-convert-result-meta", meta));
                pick.appendChild(labels);
                rhead.appendChild(pick);
                if (f.recommended) rhead.appendChild(el("span", "pa-convert-badge", "Recommended"));
                rhead.appendChild(iconButton("download", "Download " + f.name, function () { download(f.bytes, f.name); }));
                rcard.appendChild(rhead);

                rcard.appendChild(previewTable(f.preview));
                var kept = chips("Kept", f.kept, "pa-convert-kept");
                var dropped = chips("Left out", f.dropped, "pa-convert-dropped");
                if (kept || dropped) {
                    var cols = el("div", "pa-convert-columns");
                    if (kept) cols.appendChild(kept);
                    if (dropped) cols.appendChild(dropped);
                    rcard.appendChild(cols);
                }
                if (f.note) rcard.appendChild(el("p", "pa-convert-result-note", f.note));
                out.lists.filter(function (l) { return l.relevantFor === f.name; }).forEach(function (l) {
                    rcard.appendChild(linkedRow("list",
                        "<b>" + escapeHtml(l.label) + "</b> — " + (l.nRows != null ? fmtInt(l.nRows) : "?") +
                        " identifiers, attached as the relevant-features list" +
                        (l.note ? " (" + escapeHtml(l.note) + ")" : ""), l.bytes, l.name));
                });
                review.appendChild(rcard);
            });

            var unlinked = out.lists.filter(function (l) {
                return !out.values.some(function (v) { return v.name === l.relevantFor; });
            });
            if (unlinked.length || out.others.length) {
                var extras = el("div", "pa-convert-extras");
                extras.appendChild(el("h4", "pa-convert-extras-title", out.values.length ? "Also produced" : "Produced"));
                unlinked.concat(out.others).forEach(function (f) {
                    extras.appendChild(linkedRow(f.role === "relevant" ? "list" : "table",
                        "<b>" + escapeHtml(f.label) + "</b>" + (f.nRows != null ? " — " + plural(f.nRows, "row") : "") +
                        (f.note ? " (" + escapeHtml(f.note) + ")" : ""), f.bytes, f.name));
                });
                review.appendChild(extras);
            }

            if (out.manifest.skipped && out.manifest.skipped.length) {
                var skipped = el("details", "pa-convert-skipped");
                skipped.appendChild(el("summary", null, "Not converted (" + out.manifest.skipped.length + ")"));
                out.manifest.skipped.forEach(function (s) {
                    var row = el("div", "pa-convert-skipped-row");
                    row.innerHTML = "<b>" + escapeHtml(s.source || "?") + "</b> — " + escapeHtml(s.reason || "");
                    skipped.appendChild(row);
                });
                review.appendChild(skipped);
            }

            if (out.values.length > 1) {
                var addLabel = el("label", "pa-convert-addothers");
                var addBox = document.createElement("input");
                addBox.type = "checkbox";
                addBox.addEventListener("change", function () { addOthers = addBox.checked; syncActions(); });
                addLabel.appendChild(addBox);
                addLabel.appendChild(el("span", null, "Also add the other " + (out.values.length - 1) +
                    " table" + (out.values.length > 2 ? "s" : "") + " as separate omics, named after their source"));
                review.appendChild(addLabel);
            }

            reviewHost.innerHTML = "";
            reviewHost.appendChild(review);

            // ---- the decision -------------------------------------------
            var accept = el("button", "pa-convert-accept", "Use this table");
            accept.type = "button";
            var downloadAll = el("button", "pa-convert-dismiss", "Download all");
            downloadAll.type = "button";
            downloadAll.addEventListener("click", function () {
                out.files.forEach(function (f, i) { setTimeout(function () { download(f.bytes, f.name); }, i * 150); });
            });
            var dismiss = el("button", "pa-convert-dismiss is-quiet", "Cancel");
            dismiss.type = "button";
            dismiss.addEventListener("click", cancel);
            actions.innerHTML = "";
            actions.appendChild(dismiss);
            actions.appendChild(downloadAll);
            var wantsValues = ((context && context.slotRole) || "values") === "values";
            if ((wantsValues && out.values.length) || forSlot) actions.appendChild(accept);

            function syncActions() {
                accept.textContent = addOthers
                    ? "Use this table + add " + (out.values.length - 1) + " more"
                    : (out.values.length > 1 ? "Use this table" : "Use this file");
            }
            syncActions();

            accept.addEventListener("click", function () {
                var slotRole = (context && context.slotRole) || "values";
                if (slotRole !== "values" && !forSlot) {
                    // The slot asked for a design / relevant / associations
                    // file and the conversion made none: a values table in a
                    // conditions slot is worse than an empty one. Leave the
                    // downloads on the review and keep the field as it was.
                    shutdown();
                    return;
                }
                if (forSlot && (!chosen || slotRole !== "values")) {
                    // The conversion produced the file this slot asked for.
                    // Put it straight back in the field.
                    input.__paConverted = { from: file.name, original: file,
                                            fieldName: fieldName, label: forSlot.label,
                                            attempts: result.attempts || 1, relevant: false };
                    setFile(input, forSlot.bytes, forSlot.name);
                    shutdown();
                    return;
                }
                if (!chosen) return;
                var omicType = (context && context.omicType) || "Gene expression";
                var linkedList = out.lists.filter(function (l) { return l.relevantFor === chosen.name; })[0];
                var component = omicComponentOf(input);

                if (addOthers) {
                    // Keep names unique across the job: the server keys omics by name.
                    var nameField = liveNameField(component);
                    if (nameField && nameField.setValue) nameField.setValue(omicType + " (" + chosen.label + ")");
                    out.values.filter(function (v) { return v !== chosen; }).forEach(function (v) {
                        var vList = out.lists.filter(function (l) { return l.relevantFor === v.name; })[0];
                        var added = addAsNewOmic(omicType, omicType + " (" + v.label + ")", v, vList);
                        if (!added) { download(v.bytes, v.name); if (vList) download(vList.bytes, vList.name); }
                    });
                }
                // Provenance for the strip in the omic card: which file this
                // table came from, so the card can say so and offer a redo.
                input.__paConverted = { from: file.name, original: file, fieldName: fieldName,
                                        label: chosen.label, attempts: result.attempts || 1,
                                        relevant: !!linkedList };
                setFile(input, chosen.bytes, chosen.name);
                var secondary = fileInputIn(component, "secondaryFileSelector");
                if (linkedList && secondary) setFile(secondary, linkedList.bytes, linkedList.name);
                shutdown();
            });
            // Bring the review's own heading to the top of the scroll area.
            // offsetTop is measured from the sheet, not from the scrolling
            // body, so it has to be taken relative to the body's rect.
            body.scrollTop = review.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 10;
        }

        /*
         * `result` is the agent's own verdict after its attempts; `message`
         * is what stopped a run from outside -- the server's refusal, the
         * gateway's silence. They read differently: after five real
         * attempts the file needs explaining, after a refused turn nothing
         * about the file is known and the honest offer is to try again.
         */
        function renderFailure(result, message) {
            finished = true;
            freezeCurrent();
            var lit = STAGES.filter(function (s) { return stageEls[s.key].dataset.state === "current"; })[0];
            setStage(lit ? lit.key : "check", "failed");
            setNow("Could not finish");
            var review = el("section", "pa-convert-review pa-convert-review-failed");
            var head = el("div", "pa-convert-review-head");
            head.appendChild(el("h3", "pa-convert-review-title", "Could not finish this one"));
            if (result && result.attempts) head.appendChild(el("span", "pa-convert-review-count", plural(result.attempts, "attempt") + " used"));
            review.appendChild(head);
            if (message) {
                review.appendChild(el("p", "pa-convert-summary", message));
                review.appendChild(el("p", "pa-convert-summary",
                    "Nothing is known to be wrong with your file: the agent never got to read it. " +
                    "Try again, or tell it what the file contains in the box below and it will start from that."));
            } else {
                review.appendChild(el("p", "pa-convert-summary",
                    "Tell the agent what the file contains in the box below and it will try again — " +
                    "which sheet matters, what the columns are, what the identifiers are. " +
                    "The script it wrote is in the timeline above; a colleague who knows pandas can take it from there."));
            }
            if (result && result.outputs) {
                var out = describeOutputs(result);
                if (out.files.length) {
                    review.appendChild(el("p", "pa-convert-summary",
                        "Its best attempt produced " + plural(out.files.length, "file") + " that did not pass the check:"));
                    var row = el("div", "pa-convert-failed-files");
                    out.files.forEach(function (f) {
                        var b = el("button", "pa-convert-disclose", "⤓ " + f.name);
                        b.type = "button";
                        b.addEventListener("click", function () { download(f.bytes, f.name); });
                        row.appendChild(b);
                    });
                    review.appendChild(row);
                }
            }
            reviewHost.innerHTML = "";
            reviewHost.appendChild(review);
            actions.innerHTML = "";
            if (message) {
                var again = el("button", "pa-convert-accept", "Try again");
                again.type = "button";
                again.addEventListener("click", function () {
                    if (running) return;
                    addStep({ phase: "user", title: "Trying again" });
                    setStage("plan", "current");
                    setNow("Trying again");
                    attempt({ profile: profileShown || undefined });
                });
                actions.appendChild(again);
            }
            var dismiss = el("button", "pa-convert-dismiss", "Close");
            dismiss.type = "button";
            dismiss.addEventListener("click", cancel);
            actions.appendChild(dismiss);
            body.scrollTop = body.scrollHeight;
        }

        /*
         * The server runs no converter. Not a failure of the file or of the
         * agent -- nothing ran -- so no retry and no box, which would both be
         * promises the server cannot keep: what the user can do instead, and
         * Close. Reached before the sandbox boots when /ai_provider says so,
         * and from the turn's refusal on an older page.
         */
        function renderUnavailable(message) {
            finished = true;
            running = false;
            freezeCurrent();
            var lit = STAGES.filter(function (s) { return stageEls[s.key].dataset.state === "current"; })[0];
            setStage(lit ? lit.key : "read", "failed");
            setNow("Not available on this server");
            addStep({ phase: "failed", title: "AI conversion is not enabled on this server",
                      detail: message || "The server reports the converter as switched off, so nothing was started and nothing left this computer." });
            var review = el("section", "pa-convert-review pa-convert-review-failed");
            var head = el("div", "pa-convert-review-head");
            head.appendChild(el("h3", "pa-convert-review-title", "AI conversion is not enabled on this server"));
            review.appendChild(head);
            review.appendChild(el("p", "pa-convert-summary",
                "Nothing is wrong with your file. This server's administrator has not switched the " +
                "converter on (AI_INPUT_CONVERTER in its deployment settings), so no conversion can run " +
                "here and trying again will not change that."));
            var how = el("p", "pa-convert-summary");
            how.appendChild(el("b", null, "What you can do now: "));
            how.appendChild(document.createTextNode(
                "save the table as tab-separated text and upload that instead. PaintOmics reads one row " +
                "per feature with the identifier in the first column and one numeric column per sample " +
                "or condition; a relevant-features list is a single column of identifiers; an " +
                "experimental design is two columns, sample then condition. In Excel: File → Save As → " +
                "Text (Tab delimited)."));
            review.appendChild(how);
            reviewHost.innerHTML = "";
            reviewHost.appendChild(review);
            // A box that says "tell the agent" under a server that runs no
            // agent is the dead end this replaces.
            composer.hidden = true;
            composerHint.hidden = true;
            actions.innerHTML = "";
            var dismiss = el("button", "pa-convert-dismiss", "Close");
            dismiss.type = "button";
            dismiss.addEventListener("click", cancel);
            actions.appendChild(dismiss);
            body.scrollTop = body.scrollHeight;
        }
    }

    /*
     * The name format-panel.js looks for. It reads the omic's own type and the
     * chosen species off the form, because the model converts a metabolomics
     * file differently from a gene-expression one and would otherwise be
     * guessing from the file alone.
     */
    function openConvertDrawer(input, file, fieldName, slotRole) {
        var prefix = String(fieldName || "").replace(/_file$/, "");
        function fieldValue(name) {
            if (!window.Ext || !Ext.ComponentQuery) return null;
            var f = Ext.ComponentQuery.query("[name=" + name + "]")[0];
            return f && f.getValue ? f.getValue() : null;
        }
        /* The card's own live name field first: "omicN_omic_name" is the plain
           panels' convention only, and MORE's slots (file_0, conditions,
           rnaseqaux) and every relevant slot resolved to "unknown". */
        var cardName = liveNameField(omicComponentOf(input));
        var typed = cardName && cardName.getValue && cardName.getValue();
        return openDrawer(input, file, fieldName, {
            omicType: (typed && String(typed).trim()) || fieldValue(prefix + "_omic_name") || "unknown",
            species: fieldValue("specie") || "unknown",
            slotRole: slotRole || "values"
        });
    }

    /*
     * Several values files of ONE job, converted together so that they agree.
     *
     * `targets` is [{input, file, fieldName, omic, conditions}] -- one per
     * omic card, from format-panel's width guard or its error-dialog hook --
     * and `options.serverSaid` is the server's refusal when there was one.
     * Opened on the first target; every target's file goes into the sandbox.
     */
    function openHarmoniseDrawer(targets, options) {
        var first = targets[0];
        function fieldValue(name) {
            if (!window.Ext || !Ext.ComponentQuery) return null;
            var f = Ext.ComponentQuery.query("[name=" + name + "]")[0];
            return f && f.getValue ? f.getValue() : null;
        }
        return openDrawer(first.input, first.file, first.fieldName, {
            species: fieldValue("specie") || "unknown",
            slotRole: "values",
            harmonise: { targets: targets, serverSaid: (options && options.serverSaid) || "" }
        });
    }

    return { openDrawer: openDrawer, openConvertDrawer: openConvertDrawer,
             openHarmoniseDrawer: openHarmoniseDrawer,
             createSandbox: createSandbox, describeOutputs: describeOutputs,
             renderAnatomy: renderAnatomy };
});

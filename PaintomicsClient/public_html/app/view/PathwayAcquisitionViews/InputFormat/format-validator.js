/*
 * Decides whether a parsed table is something PaintOmics can actually run.
 *
 * The rules are not invented here. They are transcribed from the server's own
 * validation loop in PathwayAcquisitionJob.py:660-745, including the parts that
 * are surprising, because a client check that is merely "reasonable" would
 * disagree with the server and send users in circles.
 */
(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory();
    else root.PaintomicsInputFormat = Object.assign(root.PaintomicsInputFormat || {}, factory());
})(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    var MAX_NUMBER_FEATURES = 1000000;          // src/conf/serverconf.py:16
    // Distinct first-column identifiers kept for the organism hint
    // (format-panel.js): five cannot tell mouse from rat on gene
    // symbols, a hundred and fifty can, and it bounds the lookup.
    var ID_PROBE_SIZE = 150;

    // Columns are sampled rather than scanned in full when classifying them:
    // a 500k-row file would otherwise freeze the tab, and 200 rows is ample to
    // tell an annotation column from a measurement column.
    var COLUMN_SAMPLE = 200;

    var NUMERIC = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;
    var SPECIAL = /^[+-]?(inf(inity)?|nan)$/i;

    /*
     * Python's float(), reproduced. Two traps make a naive version wrong:
     * JS Number("") is 0 rather than an error, so an empty cell would pass as a
     * measurement; and Python accepts inf/nan/infinity case-insensitively, so a
     * stricter regex would reject files the server happily takes.
     */
    function isPythonFloat(value) {
        if (typeof value !== "string") return false;
        var text = value.trim();
        if (!text) return false;
        return NUMERIC.test(text) || SPECIAL.test(text);
    }

    // A column is called numeric on a MAJORITY of its non-empty cells, not on
    // all of them. One stray row -- an Excel banner cell, a footnote, a single
    // "NA" -- would otherwise mark every column as text, which is exactly what
    // happened on the first real workbook tested against this.
    var NUMERIC_COLUMN_THRESHOLD = 0.9;

    /*
     * Classify every column after the identifier as numeric or text.
     *
     * This is ADVISORY only and deliberately more forgiving than the contract:
     * it exists so the UI can say "these six columns look like measurements and
     * these nine look like annotation" instead of repeating "line contains
     * invalid values" ten times, and so the agent has somewhere to start. The
     * pass/fail decision is made by the row loop, which is strict.
     */
    function classifyColumns(rows, width, firstDataRow, summary) {
        for (var column = 1; column < width; column++) {
            var nonEmpty = 0;
            var numeric = 0;
            var seen = 0;
            for (var r = firstDataRow; r < rows.length && seen < COLUMN_SAMPLE; r++) {
                var row = rows[r];
                if (!row || row.length <= column) continue;
                seen++;
                var cell = String(row[column]).trim();
                if (!cell) continue;          // blanks abstain rather than vote
                nonEmpty++;
                if (isPythonFloat(cell)) numeric++;
            }
            var isNumeric = nonEmpty > 0 &&
                (numeric / nonEmpty) >= NUMERIC_COLUMN_THRESHOLD;
            (isNumeric ? summary.numericColumns : summary.textColumns).push(column);
        }
    }

    function validateValues(rows) {
        var problems = [];
        var summary = {
            nRows: 0, nCols: 0, hasHeader: false,
            columnNames: [], idSample: [], idProbe: [], numericColumns: [], textColumns: []
        };

        if (!rows || !rows.length) {
            problems.push({ code: "EMPTY", line: 0, detail: "The file is empty." });
            return { ok: false, problems: problems, summary: summary };
        }

        var nConditions = -1;
        var dataLines = 0;
        var probeSeen = {};
        var erroneousCount = 0;
        var truncated = false;

        for (var nLine = 0; nLine < rows.length; nLine++) {
            var line = rows[nLine];

            /*
             * The server treats line 0 as a header only if float(line[1])
             * RAISES -- detection keys on the second column, not the first. A
             * one-column line raises IndexError, which the bare `except
             * Exception` swallows just the same, so it also counts as a header.
             * A header whose second cell happens to be numeric is parsed as
             * data by the server, and so must be here.
             */
            if (nLine === 0 && (line.length < 2 || !isPythonFloat(line[1]))) {
                summary.hasHeader = true;
                summary.columnNames = line.slice();
                continue;
            }

            // Width is fixed by the first DATA line, not by the header.
            if (nConditions === -1) {
                if (line.length < 2) {
                    problems.push({
                        code: "TOO_FEW_COLUMNS", line: nLine,
                        detail: "Expected at least 2 columns, but found one."
                    });
                    break;
                }
                nConditions = line.length;
            }

            if (nLine > MAX_NUMBER_FEATURES) {
                problems.push({
                    code: "TOO_MANY_FEATURES", line: nLine,
                    detail: "The file exceeds the maximum of " + MAX_NUMBER_FEATURES + " features."
                });
                break;
            }

            var lineHasError = false;

            if (nConditions !== line.length && line.length > 0) {
                problems.push({
                    code: "RAGGED", line: nLine,
                    detail: "Expected " + nConditions + " columns but found " + line.length + "."
                });
                lineHasError = true;
            }

            dataLines++;
            if (summary.idSample.length < 5) summary.idSample.push(line[0]);
            if (summary.idProbe.length < ID_PROBE_SIZE) {
                var probeId = String(line[0]).trim();
                if (probeId && !probeSeen[probeId]) {
                    probeSeen[probeId] = true;
                    summary.idProbe.push(probeId);
                }
            }

            // An empty first cell is a feature with no identifier. It maps to
            // nothing and pollutes the enrichment background, and it is the
            // silent failure a two-column-header metabolomics MAF produces when
            // the identifier column is coalesced wrong. The server ingested
            // these as blank-named features; flag them so a conversion that
            // leaves any behind cannot be accepted.
            if (String(line[0]).trim() === "") {
                problems.push({
                    code: "EMPTY_IDENTIFIER", line: nLine,
                    detail: "The first column (the feature identifier) is empty."
                });
                lineHasError = true;
            }

            var rest = line.slice(1);
            var allNumeric = true;
            for (var c = 0; c < rest.length; c++) {
                if (!isPythonFloat(rest[c])) { allNumeric = false; break; }
            }
            if (!allNumeric) {
                // The server special-cases this because a European Excel export
                // is by far the most common cause, and "invalid values" sends
                // people looking in the wrong place.
                var looksLikeDecimalComma = rest.join(" ").indexOf(",") > -1;
                problems.push({
                    code: looksLikeDecimalComma ? "DECIMAL_COMMA" : "NON_NUMERIC",
                    line: nLine,
                    detail: looksLikeDecimalComma
                        ? "Perhaps you are using commas instead of dots as decimal mark?"
                        : "Line contains invalid values or symbols."
                });
                lineHasError = true;
            }

            if (lineHasError) erroneousCount++;

            // The server stops after ten bad lines. Matching that keeps the two
            // reports comparable, and stops a 500k-row disaster from producing
            // half a million DOM nodes.
            if (erroneousCount > 9) { truncated = true; break; }
        }

        var width = nConditions > 0 ? nConditions : (rows[0] ? rows[0].length : 0);
        classifyColumns(rows, width, summary.hasHeader ? 1 : 0, summary);

        var tooFewColumns = problems.some(function (p) { return p.code === "TOO_FEW_COLUMNS"; });
        if (dataLines === 0 && !tooFewColumns) {
            problems.push({
                code: "NO_FEATURE_LINES", line: 0,
                detail: "The file does not seem to have any feature lines."
            });
        }

        summary.nRows = dataLines;
        summary.nCols = width;
        summary.truncated = truncated;

        return { ok: problems.length === 0, problems: problems, summary: summary };
    }

    return {
        validateValues: validateValues,
        isPythonFloat: isPythonFloat,
        MAX_NUMBER_FEATURES: MAX_NUMBER_FEATURES,
        ID_PROBE_SIZE: ID_PROBE_SIZE
    };
});

const test = require("node:test");
const assert = require("node:assert");
const { validateValues, isPythonFloat } =
  require("../../public_html/app/view/PathwayAcquisitionViews/InputFormat/format-validator.js");

test("isPythonFloat matches Python float() and never treats '' as 0", () => {
  for (const s of ["1", "-1.5", "+2", ".5", "1e5", "1E-5", " 3 ", "inf", "NaN", "5."])
    assert.ok(isPythonFloat(s), `${JSON.stringify(s)} should parse`);
  for (const s of ["", "  ", "abc", "1,5", "1.2.3", "category_default", "-", "additive"])
    assert.ok(!isPythonFloat(s), `${JSON.stringify(s)} should not parse`);
});

test("accepts a well-formed values matrix with a header", () => {
  const rows = [["#geneID", "T00h", "T02h"], ["G1", "0.77", "-0.49"], ["G2", "1", "2"]];
  const r = validateValues(rows);
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.summary.hasHeader, true);
  assert.strictEqual(r.summary.nRows, 2);
  assert.strictEqual(r.summary.nCols, 3);
});

test("treats line 0 as data when column two is numeric, matching the server", () => {
  const rows = [["G1", "1", "2"], ["G2", "3", "4"]];
  assert.strictEqual(validateValues(rows).summary.hasHeader, false);
});

test("flags a text column on every row (the interleaved-annotation case)", () => {
  const rows = [
    ["Gene", "Gene_Valence", "Valence_Source", "SCI_vs_H_10d"],
    ["Aste1", "1", "category_default", "0.531"],
    ["Ackr1", "1", "category_default", "0.022"],
  ];
  const r = validateValues(rows);
  assert.strictEqual(r.ok, false);
  assert.ok(r.problems.some((p) => p.code === "NON_NUMERIC"));
  assert.deepStrictEqual(r.summary.textColumns, [2]);
  assert.deepStrictEqual(r.summary.numericColumns, [1, 3]);
});

test("flags a banner row whose trailing cells are blank", () => {
  const rows = [
    ["Gene", "Gene_Valence", "SCI_vs_H_10d"],
    ["✓ GENI VALIDATI (138)", "", ""],
    ["Aste1", "1", "0.531"],
  ];
  const r = validateValues(rows);
  assert.ok(r.problems.some((p) => p.code === "NON_NUMERIC" && p.line === 1));
});

test("reports the decimal-comma hint the server would report", () => {
  const rows = [["Gene", "A", "B"], ["G1", "0,77", "1,20"]];
  const r = validateValues(rows);
  assert.ok(r.problems.some((p) => p.code === "DECIMAL_COMMA"));
});

test("flags ragged rows against the first data line", () => {
  const rows = [["Gene", "A", "B"], ["G1", "1", "2"], ["G2", "3"]];
  const r = validateValues(rows);
  assert.ok(r.problems.some((p) => p.code === "RAGGED" && p.line === 2));
});

test("rejects a single-column file", () => {
  const r = validateValues([["Gene"], ["G1"]]);
  assert.ok(r.problems.some((p) => p.code === "TOO_FEW_COLUMNS"));
});

test("rejects a header with no feature lines", () => {
  const r = validateValues([["#geneID", "T00h"]]);
  assert.ok(r.problems.some((p) => p.code === "NO_FEATURE_LINES"));
});

test("stops collecting after ten bad lines, like the server", () => {
  const rows = [["Gene", "A"]];
  for (let i = 0; i < 50; i++) rows.push(["G" + i, "bad"]);
  assert.ok(validateValues(rows).problems.length <= 11);
});

test("reports an empty file rather than throwing", () => {
  assert.strictEqual(validateValues([]).ok, false);
  assert.ok(validateValues([]).problems.some((p) => p.code === "EMPTY"));
});

test("collects an ID sample for the summary line", () => {
  const rows = [["Gene", "A"], ["Plaa", "1"], ["Cldn10", "2"]];
  assert.deepStrictEqual(validateValues(rows).summary.idSample, ["Plaa", "Cldn10"]);
});

test("one banner row does not mark every column as text", () => {
  // Regression: the first real workbook tested had a merged title row under
  // the header. Its blank cells made all-cells-must-be-numeric classify every
  // measurement column as annotation, which is the opposite of useful.
  const rows = [["Gene", "Gene_Valence", "Valence_Source", "SCI_vs_H_10d"],
                ["✓ GENI VALIDATI (138)", "", "", ""]];
  for (let i = 0; i < 40; i++) rows.push(["G" + i, "1", "category_default", "0.5"]);
  const summary = validateValues(rows).summary;
  assert.deepStrictEqual(summary.numericColumns, [1, 3]);
  assert.deepStrictEqual(summary.textColumns, [2]);
});

test("a column of mostly text is still text even with a few numbers", () => {
  const rows = [["Gene", "A"]];
  for (let i = 0; i < 40; i++) rows.push(["G" + i, i < 4 ? "1.5" : "additive"]);
  assert.deepStrictEqual(validateValues(rows).summary.textColumns, [1]);
});

test("rejects a value row whose identifier cell is empty", () => {
  // A mis-coalesced metabolomics identifier left 185 rows with a blank first
  // cell; the server ingested them as blank-named features. An empty
  // identifier maps to nothing and pollutes the enrichment background.
  const rows = [["metabolite", "WT_1", "WT_2"],
                ["CHEBI:123", "1.0", "2.0"],
                ["", "3.0", "4.0"]];
  const report = validateValues(rows);
  assert.ok(!report.ok);
  assert.ok(report.problems.some((p) => p.code === "EMPTY_IDENTIFIER" && p.line === 2));
});

test("a full column of clean identifiers raises no EMPTY_IDENTIFIER", () => {
  const rows = [["geneID", "A", "B"], ["Plaa", "1", "2"], ["Cldn10", "3", "4"]];
  assert.ok(validateValues(rows).problems.every((p) => p.code !== "EMPTY_IDENTIFIER"));
});

test("a region matrix with a repeated chromosome is not flagged as duplicate ids", () => {
  // The dup-identifier gate keys a region feature on chr+start+end together;
  // keying on the chromosome alone rejected a correct locus split five times.
  const base = "../../public_html/app/view/PathwayAcquisitionViews/InputFormat/";
  const { gradeOutputs } = require(base + "convert-agent.js");
  const fullapi = Object.assign({}, require(base + "format-reader.js"),
    require(base + "format-validator.js"), require(base + "format-roles.js"));
  const enc = (rows) => new TextEncoder().encode(rows.map((r) => r.join("\t")).join("\n") + "\n");
  const region = [["chromosome", "start", "end", "T0", "T1"]];
  for (let i = 0; i < 40; i++) region.push(["1", String(1000 + i * 100), String(1400 + i * 100), "0.1", "0.2"]);
  const outputs = {
    "regions_values.tab": enc(region),
    "manifest.json": new TextEncoder().encode(JSON.stringify({ files: [{ name: "regions_values.tab", role: "values" }] })),
  };
  assert.ok(gradeOutputs(outputs, fullapi, { answers: {}, instructions: [] }).ok);
});

test("keeps a distinct identifier probe for the organism hint, header excluded", () => {
  const { ID_PROBE_SIZE } =
    require("../../public_html/app/view/PathwayAcquisitionViews/InputFormat/format-validator.js");
  const rows = [["id", "log expr"]];
  for (let i = 0; i < 2 * ID_PROBE_SIZE; i++) rows.push(["G" + (i % (ID_PROBE_SIZE + 20)), "0.5"]);
  const r = validateValues(rows);
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.summary.idProbe.length, ID_PROBE_SIZE, "capped at the probe size");
  assert.strictEqual(new Set(r.summary.idProbe).size, ID_PROBE_SIZE, "distinct");
  assert.ok(!r.summary.idProbe.includes("id"), "the header is not an identifier");
  assert.deepStrictEqual(r.summary.idSample, ["G0", "G1", "G2", "G3", "G4"], "idSample unchanged");
});

test("the probe skips blank identifiers rather than counting them", () => {
  const rows = [["id", "v"], ["A", "1"], ["", "2"], ["  ", "3"], ["B", "4"]];
  const r = validateValues(rows);
  assert.deepStrictEqual(r.summary.idProbe, ["A", "B"]);
});

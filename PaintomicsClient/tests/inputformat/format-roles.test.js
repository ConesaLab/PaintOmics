const test = require("node:test");
const assert = require("node:assert");
const D = "../../public_html/app/view/PathwayAcquisitionViews/InputFormat/";
const R = require(D + "format-roles.js");

test("associations need exactly two columns", () => {
  assert.strictEqual(R.validateAssociations([["Target","Regulator"],["G1","miR-1"]]).ok, true);
  const bad = R.validateAssociations([["Target","Regulator","Source"],["G1","miR-1","TargetScan"]]);
  assert.strictEqual(bad.ok, false);
  assert.ok(bad.problems.some(p => p.code === "NOT_TWO_COLUMNS"));
});

test("relevant associations accept one or two columns", () => {
  assert.strictEqual(R.validateRelevantAssociations([["G1"],["G2"]]).ok, true);
  assert.strictEqual(R.validateRelevantAssociations([["G1","miR-1"]]).ok, true);
  assert.strictEqual(R.validateRelevantAssociations([["G1","miR-1","x"]]).ok, false);
});

test("a relevant list is one identifier per line", () => {
  assert.strictEqual(R.validateRelevant([["ENSMUSG00000000001"],["ENSMUSG00000000028"]]).ok, true);
});

test("a relevant list rejects a field that is not an identifier", () => {
  const long = "x".repeat(90);
  assert.ok(R.validateRelevant([[long]]).problems.some(p => p.code === "FIELD_TOO_LONG"));
});

test("a multi-condition relevant list must match the condition count", () => {
  assert.strictEqual(R.validateRelevant([["a","b","c"],["d","e","f"]], 3).ok, true);
  assert.ok(R.validateRelevant([["a","b","c"]], 5).problems.some(p => p.code === "CONDITION_MISMATCH"));
});

test("a two-column relevant list is accepted whatever the condition count", () => {
  // legacy TARGET/REGULATOR pairs from the regulatory workflow
  assert.strictEqual(R.validateRelevant([["G1","miR-1"]], 6).ok, true);
});

test("a design file needs 0/1 indicators with exactly one condition per sample", () => {
  const good = [["Sample","Control","Treated"],["S1","1","0"],["S2","0","1"]];
  assert.strictEqual(R.validateDesign(good).ok, true);
  assert.deepStrictEqual(R.validateDesign(good).summary.conditions, ["Control","Treated"]);
});

test("a design file rejects condition labels instead of indicators", () => {
  const bad = [["Sample","Condition"],["S1","Control"],["S2","Treated"]];
  assert.ok(R.validateDesign(bad).problems.some(p => p.code === "NOT_INDICATOR"));
});

test("a design file accepts a sample in two conditions at once", () => {
  // A multi-factor row. runMORE.R reads it, and MOREServlet._designPatternNames
  // handles it on purpose, so since 20ad645b the check refuses only what R
  // refuses: text and a repeated sample. This test still expected the older
  // one-1-per-row rule; test_conditions_file_is_held_to_r_rules pins the same
  // contract from the server side.
  const multi = [["Sample","Control","Treated"],["S1","1","1"],["S2","0","1"]];
  const report = R.validateDesign(multi);
  assert.strictEqual(report.ok, true, JSON.stringify(report.problems));
});

test("roles are taken from the file name", () => {
  assert.strictEqual(R.roleForFileName("experimental_design.tab"), "design");
  assert.strictEqual(R.roleForFileName("mirna_associations.tab"), "associations");
  assert.strictEqual(R.roleForFileName("mirna_relevant_regulators.tab"), "relevant");
  assert.strictEqual(R.roleForFileName("gene_expression_values.tab"), "values");
  assert.strictEqual(R.roleForFileName("relevant_associations.tab"), "relevant-associations");
});

test("a region matrix that lost every measurement is rejected", () => {
  // chr/start/end alone passes the plain values contract because start and end
  // are numbers; this is the check that catches it.
  const stripped = [["#CHR","start","end"],["1","40098","40498"],["1","60000","60400"]];
  assert.strictEqual(R.validateForRole("values", stripped).ok, false);
  assert.ok(R.validateForRole("values", stripped).problems
              .some(p => p.code === "REGION_HAS_NO_MEASUREMENTS"));
});

test("a region matrix with measurements is accepted", () => {
  const good = [["#CHR","start","end","T00h","T02h"],
                ["1","40098","40498","-0.11","0.15"]];
  assert.strictEqual(R.validateForRole("values", good).ok, true);
  assert.strictEqual(R.validateForRole("values", good).summary.regionBased, true);
});

test("an ordinary values matrix is not treated as region data", () => {
  const good = [["#geneID","T00h","T02h"],["G1","0.5","0.2"]];
  assert.strictEqual(R.validateForRole("values", good).ok, true);
  assert.ok(!R.validateForRole("values", good).summary.regionBased);
});

/*
 * What the walk's text shows where the Results section should be. The
 * renderers are browser globals in an ExtJS view, so the file is run in a vm
 * context with a document just large enough for paWalkEl and text nodes: no
 * DOM library, which this suite does not have.
 *
 * What it guards is job 7O521E0Y0y's hsa05146 walk (2026-10-29): six
 * statements kept, the Narrator's section dropped for its "..." paragraphs,
 * and the page saying "the statements below stand alone" above a closed list,
 * with nothing to read. The draft the server keeps (results_draft) is shown
 * under a note naming what the checks found; with no checked section above
 * them, the statements open.
 */
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const VIEW = path.join(
    __dirname, "..", "..", "public_html", "app", "view",
    "PathwayAcquisitionViews", "PA_Step4WalkView.js"
);

/* ---------------------------------------------------------------- the page */

function FakeNode(tag) {
    this.tagName = tag;
    this.childNodes = [];
    this.attributes = {};
    this.className = "";
    this.text = "";
}
FakeNode.prototype.appendChild = function (child) {
    this.childNodes.push(child);
    return child;
};
FakeNode.prototype.setAttribute = function (name, value) {
    this.attributes[name] = String(value);
};
Object.defineProperty(FakeNode.prototype, "textContent", {
    get() { return this.text + this.childNodes.map((c) => c.textContent).join(""); },
    set(value) { this.text = String(value); this.childNodes = []; },
});

const context = vm.createContext({
    document: {
        createElement: (tag) => new FakeNode(tag),
        createTextNode: (text) => ({ textContent: String(text), childNodes: [] }),
    },
    // PA_Step4WalkView.prototype = new View(): the one top-level statement
    // that is not a declaration.
    View: function View() {},
});
vm.runInContext(fs.readFileSync(VIEW, "utf8"), context, { filename: VIEW });

function find(node, className) {
    const out = [];
    (function walk(n) {
        if (String(n.className || "").split(/\s+/).indexOf(className) >= 0) { out.push(n); }
        (n.childNodes || []).forEach(walk);
    })(node);
    return out;
}

/* No onLeg: a clickable chip binds its handler through jQuery, which this
   context does not have. The chips are drawn either way. */
function render(view) {
    return context.paWalkAppendText(new FakeNode("div"), view, {});
}

/* ---------------------------------------------------------------- the walk */

const statements = [1, 2, 3, 4, 5, 6].map((n) => ({ n: n, claim: "Statement " + n + " holds.", legs: [n] }));
const reasons = ["statement 2 is not covered", "statement 3 is not covered", "statement 4 is not covered",
    "statement 5 is not covered", "statement 6 is not covered", "5 words; between 300 and 1200 are required"];

function walk(fields) {
    return Object.assign({ statements: statements, dropped: [{ n: 7, claim: "Dropped.", why: "x" }],
        papers: {}, results: null, checks: { results: reasons } }, fields);
}

/* ------------------------------------------------------------------- tests */

test("a draft that failed its checks is shown under a note naming them", () => {
    const page = render(walk({ results_draft: { title: "IL-6 signalling in this infection", summary: "",
        paragraphs: [{ text: "Il6 rose 2.1-fold.", from_statement: 1, legs: [3] }] } }));
    const notes = find(page, "pa-walk-warning");
    assert.strictEqual(notes.length, 1);
    assert.strictEqual(notes[0].attributes.role, "note");
    const said = notes[0].textContent;
    assert.match(said, /did not pass its checks: statement 2 is not covered; /);
    // the first five, then a count of the rest
    assert.match(said, /statement 6 is not covered; and 1 more\. It is shown as the AI wrote it/);
    assert.doesNotMatch(said, /5 words/);
    assert.strictEqual(find(page, "pa-walk-results-title")[0].textContent, "IL-6 signalling in this infection");
    const paragraph = find(page, "pa-walk-paragraph")[0];
    assert.match(paragraph.textContent, /^Il6 rose 2\.1-fold\. e3$/, "an untagged paragraph gets its leg chips");
    const provenance = find(page, "pa-walk-provenance")[0].textContent;
    assert.doesNotMatch(provenance, /Every number was matched/);
    assert.match(provenance, /did not pass the checks listed above/);
    // the note asks for a check against the statements: they are open
    assert.strictEqual(find(page, "pa-walk-statements")[0].open, true);
    assert.strictEqual(find(page, "pa-walk-note").length, 0);
});

test("the note names the draft's paragraphs as the page numbers them", () => {
    // results_draft.problems renumbers "paragraph N" over the paragraphs
    // shown; checks.results counts the "..." ones the draft went without.
    const page = render(walk({ checks: { results: ["paragraph 3 quotes 9.9, not in the record"] },
        results_draft: { title: "", summary: "", problems: ["paragraph 1 quotes 9.9, not in the record"],
            paragraphs: [{ text: "Il6 rose 9.9-fold.", from_statement: 1, legs: [] }] } }));
    const said = find(page, "pa-walk-warning")[0].textContent;
    assert.match(said, /did not pass its checks: paragraph 1 quotes 9\.9, not in the record\. /);
    assert.doesNotMatch(said, /paragraph 3/);
});

test("a passing section reads as before, with no note and the statements closed", () => {
    const page = render(walk({ results: { title: "Results", summary: "Il6 rose.",
        paragraphs: [{ text: "Il6 rose [e3].", from_statement: 1, legs: [3] }] },
        checks: { results: [] }, results_draft: null }));
    assert.strictEqual(find(page, "pa-walk-warning").length, 0);
    assert.match(find(page, "pa-walk-provenance")[0].textContent, /Every number was matched/);
    assert.strictEqual(find(page, "pa-walk-statements")[0].open, false);
});

test("with no text to read, the statements the note points to are open", () => {
    // A draft that was all "..." comes as null; a walk sealed before the
    // draft was kept has no field at all.
    [walk({ results_draft: null }), walk({})].forEach((view) => {
        const page = render(view);
        assert.strictEqual(find(page, "pa-walk-results-title").length, 0);
        assert.strictEqual(find(page, "pa-walk-note")[0].textContent,
            "The Results section did not pass its checks, so the statements below stand alone.");
        assert.strictEqual(find(page, "pa-walk-statements")[0].open, true);
    });
    // ...and so when the walk ran out of time before a section was written.
    const late = render(walk({ checks: { results: ["no Results section: the time budget was spent"] } }));
    assert.match(find(late, "pa-walk-note")[0].textContent, /^No Results section was written: the walk ran out of time/);
    assert.strictEqual(find(late, "pa-walk-statements")[0].open, true);
});

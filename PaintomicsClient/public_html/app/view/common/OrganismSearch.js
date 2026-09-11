/*
 * Ranks organisms by how well a typed query names them.
 *
 * Both organism pickers -- Step 1's combo and the "Request a new organism"
 * dialog's -- used the stock ExtJS local query, which keeps a row only when its
 * display name STARTS with the typed text. KEGG names organisms
 * "Genus species (common name)", so "mouse" found nothing (the row is
 * "Mus musculus (house mouse)"), "hsa" found nothing, and one typo emptied the
 * list. People type the common name, the code, the genus, a strain, or a
 * misspelling of any of those; this module scores every row for all of them
 * and the combo below lists the rows in that order, with the matched parts
 * marked.
 *
 * The ranking half has no DOM or ExtJS dependency and is exported for node, so
 * test_organism_search runs the shipped file against the production organism
 * list. The ExtJS half is defined only when Ext is present.
 *
 * How a query is scored
 * ---------------------
 * The query is split into words. Every word must match the organism somewhere
 * (so "e coli" narrows rather than widens), and the organism's score is the
 * sum of each word's best match, where a match is one of, from strongest to
 * weakest:
 *
 *   a whole common name              "rat", "human"
 *   the head word of a common name   "mouse" in "house mouse"
 *   the KEGG code exactly, or the genus   "mmu", "hsa"; "mus", "homo"
 *   a whole word                     "sapiens", "musculus"
 *   the start of a word              "arab", "homo"
 *   the start of the KEGG code       "hs"
 *   a fragment of a word             "rice" in "licorice"
 *   a misspelling of a word          "mosue", "humna", "arabadopsos"
 *
 * A common-name match edges out the same match in the scientific name, the
 * head word of a common name ("mouse" in "house mouse") edges out another
 * word of it, and the genus edges out the species epithet. A single letter
 * is a genus initial ("e coli", "c elegans"), not a word. Misspellings are
 * accepted only for words of four letters or more (one edit; two from eight
 * letters), so "cat" never becomes "rat". Ties go to the model organisms, in
 * the curated order below, then to the name: with all 11,550 KEGG organisms
 * in the list, "mouse" has six whole-word hits and Mus musculus is the one
 * meant.
 *
 * The empty query is a browse, and a browse of 12,000 organisms in the order
 * species.json happens to hold them is a wall. It lists the curated model
 * organisms first, in their curated order (human, mouse, rat, ...), and every
 * other organism after them alphabetically by name. Not by how much each is
 * studied: deploy/species/popularity.tsv holds a PubMed count per KEGG
 * organism, but the count is per binomial -- all 286 E. coli strains share
 * 336,361, "Homo sapiens" scores 5,830 because nobody writes the Latin name,
 * and 2,346 organisms score 0 -- so it would put a block of E. coli strains
 * where the reader expects the alphabet. The alphabet is what a reader can
 * predict; the ranking above is for everything else.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    root.PaintomicsOrganismSearch = api;
    if (root.Ext && root.Ext.define) api.defineCombo(root.Ext);
})(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    /* Scores for one query word. The gaps are what the ordering above relies
       on: a field bonus (+2) or a position bonus (+3/+4) must never lift a
       weaker kind of match over a stronger one.

       The code sits BELOW a whole common name (96) and the head word of one
       (88 + 2 + 4 = 94), LEVEL with a genus (88 + 4 = 92), and above
       everything else. 253 of KEGG's 11,550 codes spell a word of some other
       organism's name -- "fly" is a Flavobacterium, "dog" a Desulfobulbus,
       "cow", "cat", "bat", "fox", "rat" and "pig" are all bacteria -- and the
       animal is what was meant. Six codes spell a genus: "mus" is a banana,
       "sus" a Solibacter, "bos" a Bosea, "pan" a Podospora; level scores let
       the model-organism tie-break put the mouse, the pig, the cow and the
       chimpanzee first, and the code-holder next. A code nothing else spells,
       "hsa", still lands first. */
    var SCORE = {
        phrase: 96,
        code: 92,
        word: 88, prefix: 78, codePrefix: 70, fragment: 58, fuzzy: 48,
        fuzzyPrefix: 42,
        common: 2,      // the match is in a common name rather than the scientific one
        head: 4,        // a whole-word match on the head word of a common name, or the genus
        lead: 3,        // a prefix match on the first word of a common name, or the genus
        editCost: 12,   // per edit of a misspelling
        wholePhrase: 10 // the whole query is a whole common name
    };

    /* KEGG codes of the model organisms, in the order the browse lists them:
       the textbook eleven first, then the rest by kingdom. Two jobs. With the
       empty query it is the top of the list; with a query it breaks ties
       between equal matches -- with the full KEGG list loaded, "mouse" is a
       whole-word hit on six organisms and "yeast" on a dozen, and the
       alphabet would put Acomys and Candida first. A better match always
       beats a model organism.

       Every code here is checked against deploy/species/manifest.tsv and the
       genus it must name by test_organism_search: an earlier list carried
       "tae" for wheat, which is Tepidanaerobacter acetatoxydans (wheat is
       taes). Codes, not names, because names carry strains and change. */
    var MODEL_ORGANISMS = [
        // the classics
        "hsa", "mmu", "rno", "dre", "dme", "cel", "sce", "spo", "ath", "eco", "bsu",
        // other vertebrates: frogs, chicken, livestock, primates, CHO, medaka, anole
        "xtr", "xla", "gga", "bta", "ssc", "cfa", "ptr", "mcc", "cge", "ola", "acs",
        // other invertebrates: mosquito, bee, silkworm, beetle, water flea, sea squirt, urchin, anemone
        "aga", "ame", "bmor", "tca", "dpx", "cin", "spu", "nve",
        // other fungi
        "cal", "ncr", "ani",
        // plants: the two rice builds, maize, wheat, sorghum, brome, soybean, medicago,
        // tomato, potato, tobacco, grape, poplar, moss, and the green alga
        "osa", "dosa", "zma", "taes", "sbi", "bdi", "gmx", "mtr", "sly", "sot", "nta",
        "vvi", "pop", "ppp", "cre",
        // protists: slime mould, malaria, Toxoplasma, the trypanosomatids, Tetrahymena
        "ddi", "pfa", "tgo", "tbr", "lma", "tet",
        // other bacteria: TB, Pseudomonas, Salmonella, H. pylori, Synechocystis,
        // Caulobacter, Agrobacterium, Streptomyces
        "mtu", "pae", "stm", "hpy", "syn", "ccr", "atu", "sco"
    ];

    /* code -> position in MODEL_ORGANISMS; an organism not listed sorts after
       every one that is. */
    var MODEL_RANK = {};
    (function () {
        var i;
        for (i = 0; i < MODEL_ORGANISMS.length; i++) MODEL_RANK[MODEL_ORGANISMS[i]] = i;
    })();

    function modelRank(code) {
        return MODEL_RANK.hasOwnProperty(code) ? MODEL_RANK[code] : MODEL_ORGANISMS.length;
    }

    var COMBINING_MARKS = /[\u0300-\u036f]/g;

    /*
     * Lower-cases and strips accents, keeping a map from every character of
     * the result back to the character of `text` it came from, so a match
     * found in the normalised string can be marked in the original.
     */
    function analyse(text) {
        var norm = "", map = [], i, j, c, n;
        text = String(text == null ? "" : text);
        for (i = 0; i < text.length; i++) {
            c = text.charAt(i).toLowerCase();
            n = c.normalize ? c.normalize("NFD").replace(COMBINING_MARKS, "") : c;
            for (j = 0; j < n.length; j++) {
                norm += n.charAt(j);
                map.push(i);
            }
        }
        map.push(text.length);
        return {text: text, norm: norm, map: map};
    }

    function normalize(text) {
        return analyse(text).norm;
    }

    /*
     * The words of a normalised string, with their positions. A run joined by
     * hyphens or underscores ("K-12", "USA300_FPR3757") yields its parts and
     * the joined form, so "k12" and "K-12" both find E. coli.
     */
    function words(norm) {
        var out = [], run = /[a-z0-9]+(?:[-_][a-z0-9]+)*/g, part = /[a-z0-9]+/g, m, p, joined;
        while ((m = run.exec(norm)) !== null) {
            if (m[0].indexOf("-") < 0 && m[0].indexOf("_") < 0) {
                out.push({text: m[0], start: m.index, end: m.index + m[0].length});
                continue;
            }
            joined = m[0].replace(/[-_]/g, "");
            out.push({text: joined, start: m.index, end: m.index + m[0].length});
            part.lastIndex = 0;
            while ((p = part.exec(m[0])) !== null) {
                out.push({text: p[0], start: m.index + p.index, end: m.index + p.index + p[0].length});
            }
        }
        return out;
    }

    /* The query's words: split on whitespace and punctuation, hyphens and
       underscores removed rather than split on, so "K-12" is the word "k12". */
    function tokenize(query) {
        var norm = normalize(query), out = [], run = /[a-z0-9]+(?:[-_][a-z0-9]+)*/g, m;
        while ((m = run.exec(norm)) !== null) out.push(m[0].replace(/[-_]/g, ""));
        return out;
    }

    /*
     * Everything the scorer needs to know about one organism, computed once.
     * KEGG writes "Genus species strain (common name)"; any parenthesised
     * group is treated as a common-name phrase ("(Japanese rice) (RAPDB)" gives
     * two), and the text before the first parenthesis is the scientific name.
     */
    function index(organism) {
        var name = analyse(organism.name), norm = name.norm, code = normalize(organism.value),
            phrases = [], groups = [], depth = 0, open = -1, i, c, w, list, k, phrase;
        for (i = 0; i < norm.length; i++) {
            c = norm.charAt(i);
            if (c === "(") { if (depth++ === 0) open = i; }
            else if (c === ")" && depth > 0 && --depth === 0) { groups.push({start: open + 1, end: i}); }
        }
        if (depth > 0 && open >= 0) groups.push({start: open + 1, end: norm.length});
        list = words(norm);
        for (k = 0; k < groups.length; k++) {
            phrase = {text: norm.slice(groups[k].start, groups[k].end).replace(/\s+/g, " ").replace(/^ | $/g, ""),
                      common: true, words: []};
            for (i = 0; i < list.length; i++) {
                w = list[i];
                if (w.start >= groups[k].start && w.end <= groups[k].end) { w.common = true; phrase.words.push(w); }
            }
            phrases.push(phrase);
        }
        phrase = {text: "", common: false, words: []};
        for (i = 0; i < list.length; i++) {
            if (!list[i].common) phrase.words.push(list[i]);
        }
        phrases.unshift(phrase);
        for (k = 0; k < phrases.length; k++) {
            for (i = 0; i < phrases[k].words.length; i++) {
                w = phrases[k].words[i];
                w.first = i === 0;
                w.last = i === phrases[k].words.length - 1;
            }
        }
        return {name: name, code: code, words: list, phrases: phrases,
                model: modelRank(code)};
    }

    var cache = typeof WeakMap === "function" ? new WeakMap() : null;

    function indexOf(organism) {
        var entry;
        if (cache && organism && typeof organism === "object") {
            entry = cache.get(organism);
            if (!entry) { entry = index(organism); cache.set(organism, entry); }
            return entry;
        }
        return index(organism);
    }

    /*
     * Restricted Damerau-Levenshtein distance (insert, delete, substitute,
     * transpose adjacent), capped: returns max + 1 as soon as no row can come
     * back under the cap. Words are short, so the table is a few dozen cells.
     */
    function editDistance(a, b, max) {
        var la = a.length, lb = b.length, prev2 = null, prev = [], cur, i, j, cost, v, rowMin;
        if (Math.abs(la - lb) > max) return max + 1;
        for (j = 0; j <= lb; j++) prev[j] = j;
        for (i = 1; i <= la; i++) {
            cur = [i];
            rowMin = i;
            for (j = 1; j <= lb; j++) {
                cost = a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1;
                v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
                if (i > 1 && j > 1 && a.charAt(i - 1) === b.charAt(j - 2) && a.charAt(i - 2) === b.charAt(j - 1)) {
                    v = Math.min(v, prev2[j - 2] + 1);
                }
                cur[j] = v;
                if (v < rowMin) rowMin = v;
            }
            if (rowMin > max) return max + 1;
            prev2 = prev;
            prev = cur;
        }
        return prev[lb];
    }

    function allowedEdits(token) {
        return token.length >= 8 ? 2 : (token.length >= 4 ? 1 : 0);
    }

    /*
     * The best match of one query word against one organism: its score and,
     * when the match is in the name, the range of the name it covers. Null
     * when the word matches nothing.
     */
    function matchToken(token, entry) {
        var best = null, i, k, w, at, score, edits, maxEdits, list = entry.words, phrases = entry.phrases;

        function consider(s, start, end) {
            if (!best || s > best.score) best = {score: s, start: start, end: end};
        }

        /* One letter is an initial: "e coli", "h sapiens". As a word it would
           match a fragment of nearly every name and the "x" of a hybrid. */
        if (token.length === 1) {
            w = phrases[0].words[0];
            if (w && w.text.charAt(0) === token) consider(SCORE.prefix + SCORE.lead, w.start, w.start + 1);
            return best;
        }

        if (token === entry.code) consider(SCORE.code, -1, -1);
        for (k = 1; k < phrases.length; k++) {
            if (phrases[k].text === token) {
                w = phrases[k].words;
                consider(SCORE.phrase, w[0].start, w[w.length - 1].end);
            }
        }
        for (i = 0; i < list.length; i++) {
            w = list[i];
            if (w.text === token) {
                score = SCORE.word + (w.common ? SCORE.common : 0) + ((w.common ? w.last : w.first) ? SCORE.head : 0);
                consider(score, w.start, w.end);
            } else if (w.text.indexOf(token) === 0) {
                score = SCORE.prefix + (w.common ? SCORE.common : 0) + (w.first ? SCORE.lead : 0);
                consider(score, w.start, w.start + token.length);
            } else if ((at = w.text.indexOf(token)) > 0) {
                consider(SCORE.fragment + (w.common ? SCORE.common : 0), w.start + at, w.start + at + token.length);
            }
        }
        if (best && best.score >= SCORE.codePrefix) return best;
        if (token.length >= 2 && entry.code.indexOf(token) === 0) consider(SCORE.codePrefix, -1, -1);
        if (best && best.score >= SCORE.fragment) return best;

        maxEdits = allowedEdits(token);
        if (maxEdits === 0) return best;
        for (i = 0; i < list.length; i++) {
            w = list[i];
            if (w.text.length < 4) continue;
            edits = editDistance(token, w.text, maxEdits);
            if (edits <= maxEdits) {
                consider(SCORE.fuzzy - SCORE.editCost * edits + (w.common ? SCORE.common : 0), w.start, w.end);
            } else if (w.text.length > token.length + maxEdits) {
                /* A misspelt start of a longer word: "arabidpo" for arabidopsis. */
                edits = editDistance(token, w.text.slice(0, token.length), maxEdits);
                if (edits <= maxEdits) {
                    consider(SCORE.fuzzyPrefix - SCORE.editCost * edits + (w.common ? SCORE.common : 0), w.start, w.end);
                }
            }
        }
        return best;
    }

    /*
     * Scores one organism for a tokenised query. Null when any word of the
     * query matches nothing. `ranges` are the parts of the name to mark.
     */
    function scoreOrganism(tokens, entry) {
        var total = 0, ranges = [], i, k, m, phrase = tokens.join(" ");
        for (i = 0; i < tokens.length; i++) {
            m = matchToken(tokens[i], entry);
            if (!m) return null;
            total += m.score;
            if (m.start >= 0) ranges.push([m.start, m.end]);
        }
        for (k = 1; k < entry.phrases.length; k++) {
            if (entry.phrases[k].text === phrase) { total += SCORE.wholePhrase; break; }
        }
        return {score: total, ranges: ranges};
    }

    function byName(a, b) {
        var an = a.key, bn = b.key;
        if (an !== bn) return an < bn ? -1 : 1;
        return a.value < b.value ? -1 : (a.value > b.value ? 1 : 0);
    }

    /*
     * The organisms that match `query`, best first. Each result carries the
     * organism's name and value and its score. An empty query lists every
     * organism: the model organisms first, in MODEL_ORGANISMS order, then the
     * rest by name.
     *
     * organisms: [{name, value}], as the species endpoint serves them.
     */
    function rank(query, organisms) {
        var tokens = tokenize(query), out = [], i, entry, scored, item;
        organisms = organisms || [];
        /* "(" or "-" or a script the names are not written in: nothing can
           match, and it must not fall through to the browse-everything branch
           below -- in the request dialog that is 11,550 rows for a stray key. */
        if (!tokens.length && /\S/.test(String(query == null ? "" : query))) return out;
        for (i = 0; i < organisms.length; i++) {
            item = organisms[i];
            if (!item || item.name == null) continue;
            entry = indexOf(item);
            if (tokens.length) {
                scored = scoreOrganism(tokens, entry);
                if (!scored) continue;
            } else {
                scored = {score: 0};
            }
            out.push({name: item.name, value: item.value, score: scored.score,
                      key: entry.name.norm, model: entry.model});
        }
        out.sort(function (a, b) {
            if (a.score !== b.score) return b.score - a.score;
            /* Between equal matches, and across the whole of a browse (every
               score is 0), the curated order; then the name. */
            if (a.model !== b.model) return a.model - b.model;
            return byName(a, b);
        });
        for (i = 0; i < out.length; i++) {
            delete out[i].key;
            delete out[i].model;
        }
        return out;
    }

    function escapeHtml(text) {
        return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    /* Marks the given (normalised-index) ranges of an analysed string. */
    function markRanges(analysed, ranges) {
        var merged = [], i, r, out = "", at = 0, start, end;
        ranges = ranges.slice().sort(function (a, b) { return a[0] - b[0]; });
        for (i = 0; i < ranges.length; i++) {
            r = ranges[i];
            if (merged.length && r[0] <= merged[merged.length - 1][1]) {
                merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], r[1]);
            } else {
                merged.push([r[0], r[1]]);
            }
        }
        for (i = 0; i < merged.length; i++) {
            start = analysed.map[merged[i][0]];
            end = analysed.map[merged[i][1]];
            out += escapeHtml(analysed.text.slice(at, start)) + "<mark>" + escapeHtml(analysed.text.slice(start, end)) + "</mark>";
            at = end;
        }
        return out + escapeHtml(analysed.text.slice(at));
    }

    /*
     * The organism's name as HTML, with the parts `query` matched wrapped in
     * <mark>. The name is escaped, so a name is never markup.
     */
    function highlight(name, query, value) {
        var organism = name !== null && typeof name === "object" ? name : {name: name, value: value == null ? "" : value},
            tokens, entry, scored;
        if (organism.name == null) return "";
        tokens = tokenize(query);
        if (!tokens.length) return escapeHtml(organism.name);
        /* The list template hands over the row's own data object, the one
           rank() already indexed, so a full-list render does not re-index
           every name; a bare string is indexed on the spot. */
        entry = organism === name ? indexOf(organism) : index(organism);
        scored = scoreOrganism(tokens, entry);
        if (!scored || !scored.ranges.length) return escapeHtml(organism.name);
        return markRanges(entry.name, scored.ranges);
    }

    /* The KEGG code as HTML, marked when a word of the query is (the start of) it. */
    function highlightCode(code, query) {
        var norm = normalize(code), tokens = tokenize(query), i;
        if (code == null) return "";
        for (i = 0; i < tokens.length; i++) {
            if (tokens[i].length >= 2 && norm.indexOf(tokens[i]) === 0) {
                return "<mark>" + escapeHtml(String(code).slice(0, tokens[i].length)) + "</mark>" +
                    escapeHtml(String(code).slice(tokens[i].length));
            }
        }
        return escapeHtml(code);
    }

    /* Thousands separators, the same in every locale: "11,951". */
    function formatCount(n) {
        return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    }

    function groupLabel(text) {
        return '<li class="po-organism-group">' + text + "</li>";
    }

    /*
     * xtype 'organismcombo': an Ext.form.field.ComboBox whose local query is
     * the ranking above instead of the display-name prefix filter, and whose
     * list shows at most `maxRows` rows. Everything else about the combo --
     * the store, valueField, forceSelection, the change event -- is
     * untouched, so the two pickers keep their contracts.
     *
     * Why a cap: the list is an ExtJS BoundList, which renders one <li> per
     * row of the store, so with every KEGG organism installed a trigger click
     * built 12,000 <li>s (206 ms measured at 11,550, before any scrolling)
     * and so did every keystroke that matched widely. Nobody scrolls to row
     * 4,000 of an alphabet; they type. So the store is filtered to the first
     * `maxRows` of rank()'s order -- the model organisms and the start of the
     * alphabet for a browse, the best matches for a query -- and the list ends
     * with a row saying how many more there are. The rows are still selected
     * by value: findRecord() searches the whole snapshot, so setValue("mmu")
     * and forceSelection work whatever the list is showing.
     */
    function defineCombo(Ext) {
        var itemCls = (Ext.baseCSSPrefix || "x-") + "boundlist-item";

        /* Called from the list template with the row's data. The query is
           read from the combo rather than closed over because the template is
           built once and the query changes per keystroke. */
        function renderItem(values, comboId) {
            var combo = Ext.getCmp(comboId), query = (combo && combo.lastQuery) || "",
                code = values.value != null && values.value !== values.name ? String(values.value) : "";
            return '<span class="po-organism-row"><span class="po-organism-name">' +
                highlight(values, query) + "</span>" +
                (code ? '<span class="po-organism-code">' + highlightCode(code, query) + "</span>" : "") +
                "</span>";
        }

        /* One <li> of the list, preceded by a group label where a browse
           passes from the curated order to the alphabet. The labels and the
           footer carry no x-boundlist-item class, so the list never selects,
           highlights, counts or keyboard-navigates them. */
        function renderRow(values, xindex, comboId) {
            var combo = Ext.getCmp(comboId), shown = combo && combo.organismShown, head = "";
            if (shown && shown.groups) {
                if (xindex === 1) head = groupLabel("Model organisms");
                else if (xindex === shown.model + 1) head = groupLabel("All organisms, A to Z");
            }
            return head + '<li role="option" unselectable="on" class="' + itemCls + '">' +
                renderItem(values, comboId) + "</li>";
        }

        /* The row after the last, when the cap cut the list. */
        function renderFooter(comboId) {
            var combo = Ext.getCmp(comboId), shown = combo && combo.organismShown, text;
            if (!shown || shown.shown >= shown.matched) return "";
            text = shown.query
                ? "Showing the best " + formatCount(shown.shown) + " of " + formatCount(shown.matched) +
                    " matches. Keep typing to narrow the list."
                : "Showing " + formatCount(shown.shown) + " of " + formatCount(shown.matched) +
                    " organisms. Type to search the rest.";
            return '<li class="po-organism-more">' + text + "</li>";
        }

        if (Ext.ClassManager.get("Paintomics.form.OrganismCombo")) return;
        Ext.define("Paintomics.form.OrganismCombo", {
            extend: "Ext.form.field.ComboBox",
            alias: "widget.organismcombo",
            queryMode: "local",

            /* Rows the list shows at most, browse or query. 0 shows every row. */
            maxRows: 200,

            statics: {
                renderItem: renderItem,
                renderRow: renderRow,
                renderFooter: renderFooter
            },

            initComponent: function () {
                var me = this;
                me.listConfig = Ext.apply({
                    /* A string; BoundList compiles it. The rows are what the
                       stock template renders (BoundList.initComponent, ExtJS
                       4.2.1) plus the group labels and the footer. */
                    tpl: '<ul class="' + (Ext.plainListCls || "") + '">' +
                        '<tpl for=".">{[Paintomics.form.OrganismCombo.renderRow(values, xindex, "' + me.id + '")]}</tpl>' +
                        '{[Paintomics.form.OrganismCombo.renderFooter("' + me.id + '")]}' +
                        "</ul>"
                }, me.listConfig);
                /* Not the usual ExtJS parent call: ExtJS 4 implements that with
                   Function.caller, which this strict-mode file does not have,
                   and the failure ("Cannot read properties of null (reading
                   'apply')") takes the whole Step 1 view down with it. */
                Ext.form.field.ComboBox.prototype.initComponent.apply(me, arguments);
            },

            /* Replaces ComboBox.doLocalQuery. Same shape: install the query
               filter once, then filter, expand or collapse, afterQuery. The
               differences: the filter keeps the first `maxRows` rows of
               rank()'s order and the store is sorted by that order -- for the
               empty query too, which is how a browse comes out model
               organisms first. The store's own sorters go the first time
               through; Store.filter() re-sorts with whatever sorters it holds
               (sortOnFilter), so one sorter reading the current order is all
               it takes. */
            doLocalQuery: function (queryPlan) {
                var me = this, store = me.store, query = (queryPlan.query || "").replace(/^\s+|\s+$/g, ""),
                    limit = me.maxRows > 0 ? me.maxRows : Infinity, records, ranked, kept, order, model, i;

                if (!me.queryFilter) {
                    me.organismOrder = {};
                    me.queryFilter = new Ext.util.Filter({
                        id: me.id + "-query-filter",
                        filterFn: function (record) {
                            return me.organismOrder.hasOwnProperty(me.organismKey(record.data));
                        }
                    });
                    store.addFilter(me.queryFilter, false);
                    store.sorters.clear();
                    store.sorters.add(new Ext.util.Sorter({
                        sorterFn: function (a, b) {
                            return me.organismOrder[me.organismKey(a.data)] - me.organismOrder[me.organismKey(b.data)];
                        }
                    }));
                }

                records = (store.snapshot || store.data).items;
                ranked = rank(query, Ext.Array.map(records, function (record) { return record.data; }));
                kept = ranked.length > limit ? ranked.slice(0, limit) : ranked;
                order = {};
                for (i = 0; i < kept.length; i++) order[me.organismKey(kept[i])] = i;
                me.organismOrder = order;

                /* A browse leads with the curated organisms; the group labels
                   sit either side of them, and only when there are rows on
                   both sides. */
                model = 0;
                if (!query) {
                    while (model < kept.length && MODEL_RANK.hasOwnProperty(normalize(kept[model].value))) model++;
                }
                me.organismShown = {
                    query: query, shown: kept.length, matched: ranked.length, total: records.length,
                    model: model, groups: !query && model > 0 && model < kept.length
                };

                store.filter();

                /* The list has not arrived (autoLoad still in flight): what
                   this computed is not the browse, and doQuery caches by
                   lastQuery, so forget it -- or the first click after the
                   load re-shows the empty list. */
                if (!records.length) me.lastQuery = undefined;

                if (store.getCount()) {
                    me.expand();
                } else {
                    me.collapse();
                }
                me.afterQuery(queryPlan);
            },

            organismKey: function (data) {
                return String(data.value) + "\u0001" + String(data.name);
            },

            /* The stock lookup searches the store's filtered rows only, so
               setValue("mmu") fails while the user's last query is still
               narrowing the list -- which is exactly when example mode sets
               the organism programmatically -- and, now that a browse shows
               200 rows of 12,000, for most organisms at any time. Every row
               is still in the snapshot, so look there. */
            findRecord: function (field, value) {
                var items = (this.store.snapshot || this.store.data).items, i;
                for (i = 0; i < items.length; i++) {
                    if (items[i].get(field) === value) return items[i];
                }
                return false;
            }
        });
    }

    return {
        normalize: normalize,
        tokenize: tokenize,
        editDistance: editDistance,
        rank: rank,
        highlight: highlight,
        highlightCode: highlightCode,
        defineCombo: defineCombo,
        formatCount: formatCount,
        MODEL_ORGANISMS: MODEL_ORGANISMS,
        SCORE: SCORE
    };
});

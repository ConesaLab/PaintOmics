/**
 * PA_Step3HubNetworkView -- metabolite hub analysis as a network, not a table.
 *
 * Why this exists. The KEGG interaction graph has always been on the server and
 * never reached the browser: compoundRegulateFeatures ships node SETS with no
 * pairs, no direction, no edge types and no intermediate hops, so a client could
 * not tell whether a radius-3 gene reached the metabolite via gene X or gene Y.
 * The hub table reported numbers about a network nobody could see.
 *
 * This panel replaces that table. A metabolite LIST -- one entry per compound,
 * ranked by significance -- selects the seed; the network draws its 1..4 step
 * neighbourhood as concentric rings; the step control and the expression
 * figures live INSIDE the network stage.
 *
 * Layout rule, learned the hard way: the detail sat below a 720px canvas, so
 * clicking a node put its heatmap 448px below the fold. It drew correctly and
 * nobody could see it, which from the user's seat is identical to a dead click.
 * Anything a click produces is now docked in the stage, above the fold, and the
 * canvas shrinks to make room rather than being covered.
 *
 * Encoding, decided colour-last:
 *
 *   position (ring) -> hop distance      the layout already carries it, so
 *                                        distance deliberately takes NO colour
 *   node face       -> one wedge per condition, in that omic's heatmap colours
 *   filled/hollow   -> DE vs not DE      so identity is never colour-alone
 *   dashed stroke   -> never measured    absence shown as absence
 *   shape           -> compound / gene
 *   size            -> the seed, and DE nodes (they carry the wedges)
 *
 * The face used to be a single fill, chosen by `values[0] < 0 ? down : up`.
 * A fill is one colour, so it had to answer "up or down?" for a feature that
 * has one answer PER CONDITION, and it answered with condition 1 -- which is
 * how citric acid came out red ("up") on the STATegra job while the heatmap
 * directly beneath it fell from +0.22 at 0h to -0.34 at 24h. That is not a
 * near miss: across that job's DE features the first condition disagrees with
 * the largest-magnitude one for 26% of genes and 68% of metabolites.
 *
 * No summary rule replaces it, because none of them is right. moanin, which
 * exists to reduce a time course to one fold change, says so itself: when a
 * feature is not consistently up- or down-regulated the estimated direction
 * does not represent the observed changes. So the reduction is gone rather
 * than improved. Slicing is what the field does with this -- Pathview cuts a
 * node into one piece per state, VANTED draws a chart inside it, Escher
 * refuses to aggregate at all -- and Paintomics' own pathway diagrams already
 * draw one box per condition. This is that encoding on a round node.
 *
 * The edges follow: a lit edge says its neighbour is DE, and the neighbour's
 * own face says what it did. An edge cannot carry six values, so it does not
 * claim one.
 *
 * "Measured but not DE" is most of the graph, so those nodes stay hollow and
 * the STROKE carries their contrast: the near-white diverging midpoint was
 * rejected at 1.12:1 against the light surface.
 */
/**
 * Everything in an omic figure that is not a data row: the chart's top margin
 * plus the room the rotated condition names need under the plot area.
 *
 * The panel used `rows * 30 + 100`, which is what the pathway views use in
 * containers 400px wider. Here the axis labels are drawn at -45 degrees and
 * the last line of them was clipped by the container -- on the STATegra job
 * the "I/C_24h" chips lost their bottom half. Sized from what the axis
 * actually needs: 12 characters at 9px, rotated, is about 40px of vertical
 * run, and the chart wants ~28px above the plot area and ~20px of padding
 * below the labels.
 */
var PA_OMIC_CHART_FURNITURE = 132;

/**
 * Every connection of one node, grouped by pathway, DE neighbours first.
 *
 * The panel this replaces printed `connectedEdges().slice(0, 8)`. On the
 * STATegra job that is 8 of Ggt1's 72 -- 11% of the answer, with no count, no
 * "and 64 more", and no scroll that could ever reach the rest. 48 of that
 * graph's 161 nodes were truncated the same way, and they are exactly the hub
 * nodes the panel exists to explain: the median degree is 2, so the cap only
 * ever bit the interesting cases.
 *
 * Pure on purpose -- `describe` is injected rather than read off `me`, so the
 * ordering can be tested in node without a Cytoscape instance behind it.
 *
 *   edges     [{source, target, kind, subtype, pathway}]
 *   id        the node whose connections these are
 *   describe  id -> {name, state}
 *
 * Ordering carries the science: DE concentration is the claim the hub table
 * makes, so a differentially expressed neighbour never sorts below a gene
 * nobody measured, and a small pathway that holds the DE partners outranks a
 * bigger one that holds none.
 */
var paHubConnections = function (edges, id, describe) {
	var groups = {}, order = [];
	var states = { up: 0, down: 0, quiet: 0, absent: 0 };
	var partners = {}, byName = {};

	(edges || []).forEach(function (edge) {
		var other = (edge.source === id) ? edge.target : edge.source;
		var about = describe(other) || {};
		var state = about.state || "absent";
		var name = about.name || other;
		var key = edge.pathway || "";

		if (!groups[key]) {
			groups[key] = { pathway: key, de: 0, rows: [] };
			order.push(groups[key]);
		}
		groups[key].rows.push({
			id: other, name: name, state: state,
			// KEGG records Ggt1->Chac1 AND Chac1->Ggt1 as two separate ECrel
			// edges. Without this the two print the same line twice and the
			// list looks like it is repeating itself.
			direction: (edge.source === id) ? "out" : "in",
			kind: edge.kind || "", subtype: edge.subtype || ""
		});

		// Counted once per PARTNER, never per edge: two edges to one gene is
		// one neighbour, and counting edges would claim more DE neighbours
		// than the job actually has.
		if (!partners[other]) {
			partners[other] = 1;
			if (states[state] === undefined) { states[state] = 0; }
			states[state]++;
			(byName[name] = byName[name] || {})[other] = 1;
		}
	});

	var RANK = { de: 0, quiet: 1, absent: 2 };
	order.forEach(function (group) {
		group.de = 0;
		group.rows.forEach(function (row) {
			// One symbol can stand for several KEGG ids -- 100042314, 14857
			// and 14858 all resolve to "Gsta5" -- and three rows printing the
			// identical line reads as the panel repeating itself. A collided
			// row keeps its id so the two can be told apart.
			row.ambiguous = Object.keys(byName[row.name] || {}).length > 1;
			if (row.state === "de") { group.de++; }
		});
		group.rows.sort(function (a, b) {
			var rank = (RANK[a.state] === undefined ? 9 : RANK[a.state]) -
			           (RANK[b.state] === undefined ? 9 : RANK[b.state]);
			if (rank !== 0) { return rank; }
			return String(a.name).localeCompare(String(b.name)) ||
			       String(a.id).localeCompare(String(b.id)) ||
			       String(a.direction).localeCompare(String(b.direction));
		});
	});
	order.sort(function (a, b) {
		return b.de - a.de || b.rows.length - a.rows.length ||
		       String(a.pathway).localeCompare(String(b.pathway));
	});

	return {
		total: (edges || []).length,
		partners: Object.keys(partners).length,
		states: states,
		groups: order
	};
};

/**
 * A node face: one wedge per condition, clockwise from twelve o'clock.
 *
 * The clip is drawn HERE rather than left to cytoscape's `background-clip`,
 * for one reason worth stating: cytoscape's own `pie-*` styles draw a CIRCLE
 * whatever the node's shape is, so slicing a compound through them silently
 * erases the diamond that distinguishes it from a gene. Owning the geometry
 * keeps the shape language intact, and it also lifts cytoscape's cap of
 * sixteen slices per node.
 *
 * The wedges overshoot the box on purpose: the clip decides the outline, so a
 * diamond's corners are cut from painted area instead of being left white.
 *
 * @param {Array}  colours  one CSS colour per condition, in column order
 * @param {String} shape    "diamond" for compounds, anything else for genes
 * @returns {String} an SVG data URI
 */
var paHubWedgeImage = function (colours, shape) {
	var n = colours.length, c = 50, r = 100, faces = [], i, a, a0, a1, big;

	if (n === 1) {
		/* One condition is not a pie. Drawing it as a single 360-degree arc
		   leaves a hairline seam where the two ends meet. */
		faces.push('<rect x="0" y="0" width="100" height="100" fill="' + colours[0] + '"/>');
	} else {
		for (i = 0; i < n; i++) {
			a0 = -Math.PI / 2 + (2 * Math.PI * i) / n;
			a1 = -Math.PI / 2 + (2 * Math.PI * (i + 1)) / n;
			big = ((a1 - a0) > Math.PI) ? 1 : 0;
			faces.push('<path d="M' + c + ' ' + c +
				' L' + (c + r * Math.cos(a0)).toFixed(1) + ' ' + (c + r * Math.sin(a0)).toFixed(1) +
				' A' + r + ' ' + r + ' 0 ' + big + ' 1 ' +
				(c + r * Math.cos(a1)).toFixed(1) + ' ' + (c + r * Math.sin(a1)).toFixed(1) +
				' Z" fill="' + colours[i] + '"/>');
		}
		/* Hairlines between wedges. Two adjacent conditions can land on nearly
		   the same colour -- which is the common case, since neighbouring
		   timepoints usually agree -- and without a break they read as one
		   wedge, so the node quietly loses a condition. Dropped past twelve,
		   where the separators would take more of the face than the data. */
		if (n <= 12) {
			for (i = 0; i < n; i++) {
				a = -Math.PI / 2 + (2 * Math.PI * i) / n;
				faces.push('<line x1="' + c + '" y1="' + c +
					'" x2="' + (c + r * Math.cos(a)).toFixed(1) +
					'" y2="' + (c + r * Math.sin(a)).toFixed(1) +
					'" stroke="#ffffff" stroke-width="3"/>');
			}
		}
	}

	var clip = (shape === "diamond")
		? '<path d="M50 0 L100 50 L50 100 L0 50 Z"/>'
		: '<circle cx="50" cy="50" r="50"/>';

	return "data:image/svg+xml;base64," + btoa(
		'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" ' +
		'viewBox="0 0 100 100"><defs><clipPath id="k">' + clip + '</clipPath></defs>' +
		'<g clip-path="url(#k)">' + faces.join("") + '</g></svg>');
};

function PA_Step3HubNetworkView() {
	this.name = "PA_Step3HubNetworkView";
	// Randomised ids: Step 3 can hold more than one network panel, and Ext
	// reuses component ids across job loads. The old Paint handler used the
	// literal id "divIdComp" and collided with itself for exactly this reason.
	var salt = Math.floor(Math.random() * 1e9);
	this.canvasID = "hubNetCanvas" + salt;
	this.ringsID = "hubNetRings" + salt;
	this.noticeID = "hubNetNotice" + salt;
	this.tipID = "hubNetTip" + salt;
	this.listID = "hubNetList" + salt;
	this.searchID = "hubNetSearch" + salt;
	this.sortID = "hubNetSort" + salt;
	this.countID = "hubNetCount" + salt;
	this.stepsID = "hubNetSteps" + salt;
	this.seedNameID = "hubNetSeedName" + salt;
	this.detailID = "hubNetDetail" + salt;

	this.cy = null;
	this.level = 1;
	this.payload = null;
	this.seed = null;
	this.charts = [];        // Highcharts instances owned by the detail card
	this.metabolites = [];   // one entry per compound, with its four step rows
	this.featureCache = {};  // id|kind -> /pa_hub_feature payload
	this.compoundNames = {}; // KEGG compound id -> readable name
	this.readings = {};      // kind|id -> {state, omicName, values, headers, wedges}
	this.limitsCache = {};   // omic name -> the colour range its heatmap uses
	this.wedgeCache = {};    // colour signature -> data URI; repeats cost nothing
	this.sortKey = "padjust";
	this.detailTab = "expr";  // sticky across clicks
	this.detailFacet = null;   // "state:up" | "pathway:mmu00480"
	this.EGO_LABEL_MAX = 22;   // names stay legible up to here
	this.DETAIL_MIN = 140;
	this.detailHeight = null;  // survives a re-render, not a close
	this.query = "";

	/* ------------------------------------------------------------------ *
	 * Model                                                               *
	 * ------------------------------------------------------------------ */

	this.loadModel = function (model) {
		var me = this;
		me.model = model;
		me.buildList();
		// loadModel and afterrender race: PA_Step3JobView constructs the view,
		// calls loadModel, and only then lays the panel out. Whichever runs
		// second has to do the work.
		if (me.component && me.component.rendered && me.hasData()) {
			me.component.show();
			me.mount();
		}
	};

	/** Everything that needs the panel's DOM to exist. */
	this.mount = function () {
		this.loadNames();
		this.renderList();
		this.renderCount();
		this.renderSteps();
		this.bindControls();
		this.selectFirst();
	};

	/**
	 * Readable names for the scored compounds, in one call.
	 *
	 * The list was titled "C12145". mappingComp cannot help -- it holds the
	 * name the USER uploaded, so a metabolomics file keyed by KEGG id makes it
	 * the id again (measured: every one of this job's 213 entries). The names
	 * live in global-paintomics.kegg_compounds, server side only.
	 *
	 * Asynchronous on purpose: the panel renders from ids immediately and
	 * re-titles itself when the map lands, rather than holding an empty list
	 * behind a request.
	 */
	this.loadNames = function () {
		var me = this;
		if (!me.metabolites.length) { return; }
		// The ids come from HERE, not from the job's stored rows: jobs written
		// before the schema-2 rewrite still hold headerless lists in Mongo, and
		// only the recovery route upgrades them on the way out. This view is
		// already holding the upgraded rows.
		var ids = me.metabolites.map(function (m) { return m.ID; }).join(",");
		$.post(SERVER_URL_PA_HUB_NAMES, { jobID: me.model.getJobID(), ids: ids })
			.done(function (payload) {
				if (typeof payload === "string") {
					try { payload = JSON.parse(payload); } catch (e) { payload = null; }
				}
				if (!payload || !payload.success || !payload.names) { return; }
				me.checkSchema(payload.hubSchema);
				me.mergeNames(payload.names);
				me.buildList();
				me.renderList();
				if (me.seed) { me.setSeedName(me.nameOf(me.seed, "compound")); }
			});
	};

	/**
	 * Refuse to render rows scored under a superseded contract.
	 *
	 * HUB_SCHEMA_VERSION exists so rows that answer a DIFFERENT question are
	 * re-scored rather than served, and the server honours it on recovery --
	 * but the client caches the whole job model in IndexedDB (app.js,
	 * Dexie "paintomics"/"jobs"), so a browser that loaded this job before an
	 * upgrade never asks the server again and keeps the old numbers for ever.
	 *
	 * Measured while verifying the omic fix: the panel showed byte-identical
	 * scores after a change that moves 403 genes into the relevant set, and
	 * /pa_recover_job returned different ones for the same job. Same class as
	 * an unbumped ?v= marker -- new code, cached data.
	 *
	 * Dropping the cache and reloading is the whole repair: the job lives on
	 * the server, and the recovery path re-scores it. Guarded so it can happen
	 * at most once per job per session and cannot become a reload loop.
	 */
	this.checkSchema = function (serverSchema) {
		var me = this;
		if (!serverSchema) { return; }
		var rows = (me.model && me.model.getHubAnalysisResult()) || {};
		var keys = Object.keys(rows);
		if (!keys.length) { return; }
		var stored = rows[keys[0]] && rows[keys[0]].schema;
		var flag = "paHubSchemaReload:" + me.model.getJobID();
		if (stored === undefined || stored === serverSchema) {
			// Back in step: let a LATER upgrade heal itself too.
			try { sessionStorage.removeItem(flag); } catch (e) {}
			return;
		}
		try {
			if (sessionStorage.getItem(flag)) {
				console.warn("[hub] cached rows are schema " + stored +
					" and the server scores at " + serverSchema +
					", but the reload has already been tried this session.");
				return;
			}
			sessionStorage.setItem(flag, "1");
		} catch (e) {
			return;   // no sessionStorage: no loop guard, so do not reload
		}
		console.warn("[hub] cached rows are schema " + stored +
			", the server scores at " + serverSchema +
			"; dropping the cached job and reloading.");
		if (typeof discardCachedJobModel === "function") {
			discardCachedJobModel(me.model.getJobID(), function () {
				window.location.reload();
			});
		}
	};

	this.mergeNames = function (names) {
		for (var id in names) {
			if (names[id]) { this.compoundNames[id] = names[id]; }
		}
	};

	/**
	 * The display name for one node, or its id when nothing better is known.
	 *
	 * Compounds are named from the server map. Genes are named from
	 * globalExpressionData's `keggName`, which is the gene SYMBOL the mapper
	 * resolved (Aanat, Abca1, ...) -- so every measured gene has one, and only
	 * genes the job never measured fall back to the KEGG id. That is also
	 * every gene node this panel labels, since labels go to the seed and the
	 * DE nodes.
	 */
	this.nameOf = function (id, kind) {
		if (kind === "compound") {
			return this.compoundNames[id] ||
			       ((this.model && this.model.mappingComp) || {})[id] || id;
		}
		var entry = this.expressionFor(id, kind);
		var symbol = entry && entry.keggName;
		return (symbol && symbol !== id) ? symbol : id;
	};

	/** "Phytoceramide (C12145)", or just the id when it IS the name. */
	this.nameWithID = function (id, kind) {
		var name = this.nameOf(id, kind);
		return (name === id) ? Ext.String.htmlEncode(id)
		                     : Ext.String.htmlEncode(name) +
		                       ' <span class="pa-hub-id">' +
		                       Ext.String.htmlEncode(id) + '</span>';
	};

	/**
	 * Collapse the hub rows to ONE entry per compound.
	 *
	 * getHubAnalysisResult() is one row per (compound, radius), so every
	 * metabolite appears four times -- which is why the grid it replaces needed
	 * a step filter to be readable at all. Here the four scores become a
	 * per-step array on a single entry, the ring buttons are the step control,
	 * and the four rows are shown together in the metabolite's own summary.
	 */
	this.buildList = function () {
		var rows = (this.model && this.model.getHubAnalysisResult()) || {};
		var byID = {};
		for (var key in rows) {
			var row = paHubRow(rows[key]);
			if (!row || !row.ID) { continue; }
			var entry = byID[row.ID];
			if (!entry) {
				entry = byID[row.ID] = {
					ID: row.ID,
					name: this.nameOf(row.ID, "compound"),
					steps: {}
				};
			}
			entry.steps[row.Step] = row;
		}
		this.metabolites = [];
		for (var id in byID) {
			var item = byID[id];
			// Rank on the metabolite's BEST step -- a compound that is
			// significant at radius 2 and nowhere else is still a finding, and
			// ranking on a fixed radius would bury it.
			var best = null;
			for (var step in item.steps) {
				var candidate = item.steps[step];
				if (best === null || Number(candidate.padjust) < Number(best.padjust)) {
					best = candidate;
				}
			}
			item.best = best;
			item.bestStep = best ? Number(best.Step) : 1;
			item.padjust = best ? Number(best.padjust) : 1;
			item.pvalue = best ? Number(best.pvalue) : 1;
			item.density = best ? Number(best.Percentage) : 0;
			item.den = best ? Number(best.DEN) : 0;
			this.metabolites.push(item);
		}
		this.sortList();
	};

	this.SORTS = {
		padjust: function (a, b) { return a.padjust - b.padjust || a.name.localeCompare(b.name); },
		density: function (a, b) { return b.density - a.density || a.name.localeCompare(b.name); },
		den: function (a, b) { return b.den - a.den || a.name.localeCompare(b.name); },
		name: function (a, b) { return a.name.localeCompare(b.name); }
	};

	this.sortList = function () {
		this.metabolites.sort(this.SORTS[this.sortKey] || this.SORTS.padjust);
	};

	this.entryFor = function (compoundID) {
		for (var i = 0; i < this.metabolites.length; i++) {
			if (this.metabolites[i].ID === compoundID) { return this.metabolites[i]; }
		}
		return null;
	};

	/* ------------------------------------------------------------------ *
	 * Metabolite list                                                     *
	 * ------------------------------------------------------------------ */

	/**
	 * How much there is to look through, as one line of text.
	 *
	 * This replaces a three-tile figure band. The counts were worth keeping --
	 * "96 of 213 are significant" is the shape of the result -- but three
	 * circled icons and 48px numerals spent a quarter of the panel's height
	 * saying it, above the thing they describe.
	 */
	this.renderCount = function () {
		var host = document.getElementById(this.countID);
		if (!host) { return; }
		var total = this.metabolites.length;
		var significant = this.metabolites.filter(function (m) {
			return m.padjust < 0.05;
		}).length;
		host.innerHTML = total
			? total + " metabolites &middot; <b>" + significant + "</b> with FDR &lt; 0.05"
			: "";
	};

	this.renderList = function () {
		var me = this;
		var host = document.getElementById(this.listID);
		if (!host) { return; }
		var query = this.query.toLowerCase();
		var shown = this.metabolites.filter(function (m) {
			return !query || m.name.toLowerCase().indexOf(query) >= 0 ||
			       m.ID.toLowerCase().indexOf(query) >= 0;
		});
		if (!shown.length) {
			host.innerHTML = '<p class="pa-hub-list-empty">' +
				(this.metabolites.length ? "No metabolite matches that search."
				                         : "This job has no scored metabolites.") +
				'</p>';
			return;
		}
		host.innerHTML = shown.map(function (m) {
			var fdr = Number(m.padjust);
			// The grid tinted p-values with renderFunctionLimit; the same
			// signal survives here as a class rather than an inline colour.
			var tone = fdr < 0.05 ? " is-significant" : (fdr < 0.1 ? " is-marginal" : "");
			return '<a class="pa-hub-item' + tone +
				(m.ID === me.seed ? " is-current" : "") +
				'" data-id="' + m.ID + '" href="#">' +
				'<span class="pa-hub-item-name">' + Ext.String.htmlEncode(m.name) + '</span>' +
				// The id stays on the row: a KEGG id is what a reader carries
				// to another tool, and several compounds share a common name.
				'<span class="pa-hub-item-meta">' +
				(m.name === m.ID ? "" : m.ID + ' &middot; ') +
				'FDR ' + me.fmt(fdr) +
				' &middot; ' + m.den + ' DE &middot; best at step&nbsp;' + m.bestStep + '</span>' +
				'</a>';
		}).join("");
		Array.prototype.forEach.call(host.querySelectorAll(".pa-hub-item"), function (el) {
			el.addEventListener("click", function (event) {
				event.preventDefault();
				me.showCompound(el.getAttribute("data-id"), null, true);
			});
		});
	};

	this.fmt = function (value) {
		if (!isFinite(value)) { return "-"; }
		if (value < 0.001) { return Number(value).toExponential(1); }
		return Number(value).toFixed(3);
	};

	/* ------------------------------------------------------------------ *
	 * Step control                                                        *
	 * ------------------------------------------------------------------ */

	/**
	 * The four steps, as chips carrying their own ring size.
	 *
	 * They were four toggle buttons in the panel's bottom toolbar -- as far from
	 * the graph as the layout allows, and silent about which of them had
	 * anything to show. Most compounds run out well before radius 4, so a
	 * pressable button that changes nothing was the common case, not the edge
	 * case. A chip whose ring is empty is disabled and says "0".
	 */
	this.renderSteps = function () {
		var me = this;
		var host = document.getElementById(me.stepsID);
		if (!host) { return; }
		var counts = {};
		((me.payload && me.payload.rings) || []).forEach(function (r) {
			counts[r.step] = r.total;
		});
		host.innerHTML = [1, 2, 3, 4].map(function (n) {
			var total = counts[n];
			var known = (total !== undefined);
			var empty = known && total === 0;
			// The badge is the size of THIS ring, not of the ball -- that is
			// what makes a 0 meaningful. The button itself is cumulative (it
			// lights every ring up to n), and the two numbers differ, so the
			// tooltip says which is which rather than leaving the reader to
			// reconcile "2 1" against a table row reading 33.
			return '<button type="button" class="pa-hub-step' +
				(n === me.level ? " is-current" : "") + (empty ? " is-empty" : "") +
				'" data-step="' + n + '"' + (empty ? " disabled" : "") +
				' title="' + (empty
					? "No neighbours exactly " + n + " steps out"
					: "Show everything within " + n + " step" + (n === 1 ? "" : "s") +
					  (known ? " — " + total + " exactly " + n + " out" : "")) + '">' +
				n + (known ? '<em>' + total + '</em>' : "") +
				'</button>';
		}).join("");
		Array.prototype.forEach.call(host.querySelectorAll(".pa-hub-step"), function (el) {
			el.addEventListener("click", function () {
				me.setLevel(parseInt(el.getAttribute("data-step"), 10));
			});
		});
	};

	/* ------------------------------------------------------------------ *
	 * Network                                                             *
	 * ------------------------------------------------------------------ */

	this.showCompound = function (compoundID, level, reveal) {
		var me = this;
		var entry = me.entryFor(compoundID);
		me.seed = compoundID;
		me.level = Math.max(1, Math.min(4,
			parseInt(level, 10) || (entry ? entry.bestStep : 1)));
		me.payload = null;
		me.note("Loading the neighbourhood of " + compoundID + "…");
		me.clearDetail();
		me.renderList();
		me.renderSteps();
		me.setSeedName(me.nameOf(compoundID, "compound"));
		$.post(SERVER_URL_PA_HUB_SUBGRAPH, {
			jobID: me.model.getJobID(),
			compoundID: compoundID,
			level: 4,             // fetch all four; the control dims, never refetches
			maxEdges: 600,
			perRing: 40
		}).done(function (payload) {
			if (typeof payload === "string") {
				try { payload = JSON.parse(payload); } catch (e) { payload = null; }
			}
			if (!payload || !payload.success) {
				me.note((payload && payload.errorMessage) || "No network available.");
				return;
			}
			if (!payload.nodes || !payload.nodes.length) {
				me.note(compoundID + " has no neighbours in the KEGG network " +
				        "for this organism.");
				return;
			}
			// Ring compounds the job never measured are not in the scored
			// list, so their names arrive with the subgraph instead.
			me.mergeNames(payload.names || {});
			me.payload = payload;
			me.render(payload);
			me.renderSteps();
			// Selecting a metabolite opens the metabolite, not an empty card:
			// its four step scores are the numbers the removed grid carried,
			// and this is now the only place they are printed.
			me.showSeedDetail(reveal);
		}).fail(function () {
			me.note("Could not reach the server.");
		});
	};

	this.setSeedName = function (name) {
		var el = document.getElementById(this.seedNameID);
		if (el) { el.innerHTML = Ext.String.htmlEncode(name || ""); }
	};

	this.note = function (text) {
		var el = document.getElementById(this.noticeID);
		if (el) { el.innerHTML = text || ""; }
	};

	/**
	 * Everything the node face and its tooltip need for one feature.
	 *
	 * The colours come from getMinMax + getColor against the omic's OWN
	 * distribution summary, with the same default reference the detail card
	 * uses. That is the whole point: the node and the heatmap underneath it are
	 * now one calculation, so they cannot disagree again.
	 *
	 * `entry.relevant` is an ARRAY after OmicValue.loadFromJSON, and [] is
	 * truthy -- so testing the property directly makes "measured but not DE"
	 * unreachable and marks every measured feature DE. Ask the OmicValue
	 * instead; that is what its accessors are for.
	 */
	this.readingOf = function (id, kind) {
		var me = this, key = kind + "|" + id;
		if (key in me.readings) { return me.readings[key]; }

		var reading = { state: "absent", omicName: "", values: [], headers: [], wedges: "" };
		var entry = me.expressionFor(id, kind);

		if (entry) {
			var de = (typeof entry.isRelevant === "function")
				? (entry.isRelevant() || entry.isRelevantAssociation())
				: false;
			reading.state = de ? "de" : "quiet";
			reading.omicName = me.omicNameOf(entry, kind);

			// The mode the figures are drawn in, so a job with an applied sample
			// mapping gets one wedge per SAMPLE, matching its own heatmap rather
			// than the raw replicate columns behind it.
			var mode = me.model.getReplicateMode ? me.model.getReplicateMode() : "replicates";
			var raw = (typeof entry.getValues === "function")
				? entry.getValues(mode) : entry.values;
			reading.values = (raw || []).map(Number);

			// headers[0] names the id column, not a condition.
			reading.headers = (paOmicHeaders(me.model, reading.omicName) || [])
				.slice(1, reading.values.length + 1);

			var limits = me.limitsFor(reading.omicName);
			if (de && limits && reading.values.length) {
				var colours = reading.values.map(function (value) {
					// A gap in one condition is not a measurement, and running it
					// through the ramp would state one. It gets the surface colour.
					return isFinite(value)
						? getColor(limits, value, PA_DEFAULT_COLOR_SCALE) : "#f4f4f5";
				});
				var shape = (kind === "compound") ? "diamond" : "ellipse";
				var signature = shape + "|" + colours.join("|");
				if (!(signature in me.wedgeCache)) {
					me.wedgeCache[signature] = paHubWedgeImage(colours, shape);
				}
				reading.wedges = me.wedgeCache[signature];
			}
		}

		me.readings[key] = reading;
		return reading;
	};

	/** The DE state alone, for the callers that only branch on it. */
	this.stateOf = function (id, kind) {
		return this.readingOf(id, kind).state;
	};

	/**
	 * The colour range one omic's figures are painted against.
	 *
	 * Memoised because elements() asks per node and a radius-4 graph is
	 * thousands of them, all sharing at most two omics.
	 */
	this.limitsFor = function (omicName) {
		if (omicName in this.limitsCache) { return this.limitsCache[omicName]; }
		var summaries = (this.model && this.model.getDataDistributionSummaries()) || {};
		var limits = null;
		try {
			if (summaries[omicName]) {
				limits = getMinMax(summaries[omicName], PA_DEFAULT_COLOR_REFERENCE);
			} else {
				// Not fatal: the node keeps its heavier DE stroke and simply
				// carries no colour. Silence here would look like a render fault.
				console.warn("[hub] no distribution summary for '" + omicName +
					"', so its nodes cannot be coloured per condition.");
			}
		} catch (error) {
			console.warn("[hub] no colour range for '" + omicName + "': " + error);
		}
		this.limitsCache[omicName] = limits;
		return limits;
	};

	/**
	 * The omic a globalExpressionData entry came from.
	 *
	 * globalExpressionData is built from `omicsValues[0]` and nothing else, so
	 * it is ONE omic whatever the job uploaded -- but it never said which, and
	 * without the name there is no distribution summary to scale it against.
	 * The server sends it now. The fallback covers a job still being served
	 * from the pre-restart in-memory cache, and names the same omic by
	 * construction, since that cache was built from omicsValues[0] too.
	 */
	this.omicNameOf = function (entry, kind) {
		if (entry && entry.omicName) { return entry.omicName; }
		var names = (kind === "compound")
			? (this.model.getCompoundOmicNames ? this.model.getCompoundOmicNames() : [])
			: (this.model.getGeneOmicNames ? this.model.getGeneOmicNames() : []);
		return names[0] || "";
	};

	/**
	 * The node's own numbers, condition by condition.
	 *
	 * The face is N colours; this says which condition each one is and what it
	 * was. Without it the wedges are a picture of a movement the reader cannot
	 * name, which is the failure the single fill had, only prettier. Capped,
	 * because a 24-condition job would push the tooltip off the stage.
	 */
	this.conditionTable = function (reading) {
		if (!reading || !reading.wedges || !reading.values.length) { return ""; }
		var limits = this.limitsFor(reading.omicName);
		var rows = reading.values.slice(0, 10).map(function (value, i) {
			var swatch = (limits && isFinite(value))
				? getColor(limits, value, PA_DEFAULT_COLOR_SCALE) : "#f4f4f5";
			return '<tr><td><i class="pa-hub-cell" style="background:' + swatch +
				'"></i></td><td>' +
				Ext.String.htmlEncode(reading.headers[i] || ("condition " + (i + 1))) +
				'</td><td>' + (isFinite(value) ? value.toFixed(2) : "&mdash;") +
				'</td></tr>';
		}).join("");
		var more = reading.values.length - 10;
		return '<div class="pa-hub-tip-omic">' +
			Ext.String.htmlEncode(reading.omicName) + '</div>' +
			'<table class="pa-hub-tip-values">' + rows + '</table>' +
			(more > 0 ? '<div class="pa-hub-tip-hint">and ' + more +
			            ' more condition' + (more === 1 ? "" : "s") + '</div>' : "");
	};

	this.expressionFor = function (id, kind) {
		var data = this.model && this.model.getGlobalExpressionData();
		return data && ((kind === "compound")
			? (data.inputCompound || {})[id]
			: (data.inputGene || {})[id]);
	};

	this.elements = function (payload) {
		var me = this, out = [];
		payload.nodes.forEach(function (n) {
			// The seed is an identity marker, not a measurement: it is the thing
			// the user picked, and its own conditions are in the card below.
			var reading = (n.step === 0) ? null : me.readingOf(n.id, n.type);
			var state = reading ? reading.state : "seed";
			var name = me.nameOf(n.id, n.type);
			out.push({ group: "nodes", data: {
				id: n.id,
				// Truncated on the canvas only. Compound names run long
				// ("Ultra-long-chain omega-hydroxy fatty acid"), and a label
				// wider than its ring is worse than an id. The tooltip and the
				// detail card carry the whole name.
				label: (name.length > 24) ? name.slice(0, 23) + "\u2026" : name,
				fullName: name,
				step: n.step,
				kind: n.type,
				state: state,
				// The face, as a data URI. Empty for everything that is not a DE
				// feature with a scalable omic behind it; `hasWedges` is the
				// selector, because cytoscape cannot test a style for emptiness.
				wedges: (reading && reading.wedges) || "",
				hasWedges: (reading && reading.wedges) ? 1 : 0,
				seed: (n.step === 0) ? 1 : 0,
				// Only the seed and the DE nodes are labelled. Radius 4 can
				// reach thousands of nodes; a label on each is unreadable.
				showLabel: (n.step === 0 || state === "de") ? 1 : 0
			}});
		});
		(payload.edges || []).forEach(function (e, i) {
			var typed = (payload.source !== "legacy-json") && !!e.subtype;
			out.push({ group: "edges", data: {
				id: "e" + i, source: e.source, target: e.target,
				kind: e.kind, subtype: e.subtype || "", pathway: e.pathway || "",
				// Arrowheads ONLY from a real subtype on a real KGML parse. The
				// legacy-json fallback carries none, and drawing direction from
				// it would be inventing biology.
				directed: typed ? 1 : 0,
				inhibits: (typed && /inhibition|repression/.test(e.subtype)) ? 1 : 0
			}});
		});
		return out;
	};

	this.render = function (payload) {
		var me = this;
		me.describe(payload);

		var host = document.getElementById(me.canvasID);
		if (!host) { return; }
		// Cytoscape measures its container once, so the height must be real
		// BEFORE construction or the graph lays out into a zero-height box.
		// Height comes from the flex stage; only force a value if that produced
		// nothing, which happens while the panel is still collapsed.
		if (host.getBoundingClientRect().height === 0) { host.style.height = "420px"; }

		if (me.cy) { me.cy.destroy(); me.cy = null; }
		me.cy = cytoscape({
			container: host,
			elements: me.elements(payload),
			minZoom: 0.2,
			maxZoom: 3,
			layout: {
				name: "concentric",
				concentric: function (n) { return 5 - n.data("step"); },
				levelWidth: function () { return 1; },
				minNodeSpacing: 22,
				avoidOverlap: true,
				animate: false,
				padding: 28
			},
			style: [
				{ selector: "node", style: {
					"width": 13, "height": 13,
					"background-color": "#ffffff",
					"border-width": 1.5, "border-color": "#595959",
					"label": "", "font-size": 10,
					"color": "#18181b",
					"text-margin-y": -3,
					"text-background-color": "#ffffff",
					"text-background-opacity": 0.85,
					"text-background-padding": 2 }},
				{ selector: "node[kind = 'compound']", style: {
					"shape": "diamond", "width": 15, "height": 15 }},
				// Bigger, because the face has to carry one wedge per condition,
				// and darker-stroked, because a face built from the omic's own
				// ramp includes pale colours: on a feature whose conditions all
				// sit near zero the stroke is the only thing separating it from a
				// hollow "not DE" node. It also keeps DE legible when the omic has
				// no distribution summary and there are no wedges to draw.
				{ selector: "node[state = 'de']", style: {
					"width": 22, "height": 22,
					"border-width": 2, "border-color": "#18181b" }},
				{ selector: "node[kind = 'compound'][state = 'de']", style: {
					"width": 24, "height": 24 }},
				{ selector: "node[hasWedges = 1]", style: {
					"background-image": "data(wedges)",
					"background-fit": "cover",
					// The clip lives inside the SVG, so cytoscape must not clip
					// again: two anti-aliased passes over the same outline leave a
					// seam at the edge.
					"background-clip": "none",
					"background-image-opacity": 1 }},
				{ selector: "node[state = 'absent']", style: {
					"border-style": "dashed", "border-color": "#a1a1aa" }},
				{ selector: "node[seed = 1]", style: {
					"shape": "diamond", "width": 28, "height": 28,
					"background-color": "#ffffff",
					"border-width": 3, "border-color": "#18181b",
					"font-size": 12, "font-weight": "bold" }},
				{ selector: "node[showLabel = 1]", style: { "label": "data(label)" }},
				{ selector: "node.far", style: { "label": "" }},
				{ selector: "edge", style: {
					"width": 1, "line-color": "#d4d4d8",
					"curve-style": "bezier", "opacity": 0.75 }},
				{ selector: "edge[directed = 1]", style: {
					"target-arrow-shape": "triangle", "arrow-scale": 0.6,
					"target-arrow-color": "#d4d4d8" }},
				{ selector: "edge[inhibits = 1]", style: {
					"target-arrow-shape": "tee" }},
				{ selector: ".dim", style: { "opacity": 0.08 }},
				// Everything outside the clicked node's ego network. Declared
				// AFTER .dim so an element carrying both settles on this one,
				// and BEFORE the ego classes so a lit edge overrides it.
				{ selector: "node.pa-away", style: { "opacity": 0.15 }},
				{ selector: "edge.pa-away", style: { "opacity": 0.04 }},
				// The clicked node's own edges, so the graph answers "how does
				// this connect?" without the reader moving to a list.
				// A mid grey, not a dark one: the canvas is transparent over
				// --pa-surface, which flips with the theme, and cytoscape
				// styles are JS so they cannot read a CSS token. #71717a is
				// legible on both grounds. What actually separates a lit edge
				// from the rest is the opacity gap (0.95 against 0.04), not
				// the hue -- so this stays readable either way.
				{ selector: "edge.pa-ego-edge", style: {
					"width": 1.8, "opacity": 0.95, "line-color": "#71717a",
					"target-arrow-color": "#71717a", "z-index": 20 }},
				// An edge to a DE neighbour, weighted rather than hued. It used
				// to be painted red or blue by that neighbour's "direction",
				// which the neighbour does not have -- it has one value per
				// condition, and they can disagree. Weight says "this partner
				// carries the finding"; the partner's own face says what it did.
				{ selector: "edge.pa-ego-de", style: {
					"width": 2.4, "line-color": "#27272a",
					"target-arrow-color": "#27272a" }},
				{ selector: "node.pa-ego-node", style: { "z-index": 21 }},
				// A ring of 44 partners cannot all be labelled legibly, so the
				// label goes on the neighbours that carry the finding; a small
				// ego network gets every name (see focusEgo).
				{ selector: "node.pa-ego-label", style: { "label": "data(label)" }},
				{ selector: ".hovered", style: {
					"border-width": 3, "border-color": "#18181b" }},
				// setLevel owns .dim; a click highlight must use its own class
				// or the two fight over the same property.
				{ selector: ".picked", style: {
					"border-width": 4, "border-color": "#18181b" }}
			]
		});

		me.cy.one("layoutstop", function () {
			// fit() on afterrender runs before any data exists, so the graph
			// came up as a speck in the middle of an empty canvas. The layout
			// is the only moment the node positions are real.
			me.fitToVisible();
			me.drawRings();
		});
		me.cy.on("pan zoom resize", function () {
			me.applyLabelZoom();
			me.drawRings();
		});
		me.bindHover();
		me.bindTap();
		me.setLevel(me.level);
	};

	/**
	 * Say what was drawn and what was not.
	 *
	 * The server budgets PER RING and reports shown/total for each, so a cap can
	 * never read as "this is all there is". The first version hardcoded
	 * "Showing the 400 edges closest to X" while rings 3 and 4 were missing
	 * entirely -- true, and useless.
	 */
	this.describe = function (payload) {
		if (!payload) { this.note(""); return; }
		var lines = [];
		if (payload.source === "legacy-json") {
			lines.push('<span class="pa-hub-warn">This organism has no KGML on ' +
				'disk, so only direct neighbours are drawn and relation types ' +
				'are unavailable.</span>');
		}
		// A step whose ring is genuinely empty must SAY so. Clicking 3 and
		// getting no visible change is indistinguishable from a broken control,
		// and most compounds run out well before radius 4.
		var reach = 0;
		(payload.rings || []).forEach(function (r) {
			if (r.total > 0) { reach = Math.max(reach, r.step); }
		});
		if (this.level > reach) {
			lines.push("<b>" + Ext.String.htmlEncode(payload.seed
					? this.nameOf(payload.seed, "compound") : "This metabolite") +
				"</b> has no neighbours beyond step " + reach +
				", so there is nothing to show at step " + this.level + ".");
		}

		var sampled = (payload.rings || []).filter(function (r) {
			return r.shown < r.total && r.step <= this.level;
		}, this);
		if (sampled.length) {
			lines.push("Large rings are sampled, differentially expressed features first: " +
				sampled.map(function (r) {
					return "step " + r.step + " showing <b>" + r.shown + " of " +
					       r.total + "</b>";
				}).join(" &middot; ") + ".");
		}
		this.note(lines.join(" "));
	};

	/**
	 * Faint guide circles plus a "step N" label, so the rings READ as steps
	 * rather than as an accident of the layout.
	 *
	 * Built with createElementNS, not svg.js: svg.js 2.0.5's .path() reads
	 * pathSegList, removed in Chrome 48, which is why no diagram in this
	 * application had ever carried a vector primitive.
	 */
	this.drawRings = function () {
		var me = this, cy = me.cy;
		var svg = document.getElementById(me.ringsID);
		if (!cy || !svg) { return; }
		while (svg.firstChild) { svg.removeChild(svg.firstChild); }
		var seed = cy.nodes("[seed = 1]");
		if (!seed.length) { return; }
		var origin = seed.position(), pan = cy.pan(), zoom = cy.zoom();
		var radii = {};
		cy.nodes().forEach(function (n) {
			var step = n.data("step");
			if (!step) { return; }
			var dx = n.position("x") - origin.x, dy = n.position("y") - origin.y;
			if (!radii[step]) { radii[step] = []; }
			radii[step].push(Math.sqrt(dx * dx + dy * dy));
		});
		var cx = origin.x * zoom + pan.x, cyy = origin.y * zoom + pan.y;
		var NS = "http://www.w3.org/2000/svg";
		var counts = {};
		(me.payload && me.payload.rings || []).forEach(function (r) { counts[r.step] = r; });
		Object.keys(radii).sort().forEach(function (step) {
			var list = radii[step];
			var mean = list.reduce(function (a, b) { return a + b; }, 0) / list.length;
			var r = mean * zoom;
			var current = (String(step) === String(me.level));
			var circle = document.createElementNS(NS, "circle");
			circle.setAttribute("cx", cx);
			circle.setAttribute("cy", cyy);
			circle.setAttribute("r", r);
			circle.setAttribute("class", "pa-hub-ring" + (current ? " is-current" : ""));
			svg.appendChild(circle);
			var text = document.createElementNS(NS, "text");
			text.setAttribute("x", cx);
			text.setAttribute("y", cyy - r - 5);
			text.setAttribute("text-anchor", "middle");
			text.setAttribute("class", "pa-hub-ring-label" + (current ? " is-current" : ""));
			var info = counts[step];
			text.textContent = "step " + step +
				(info && info.shown < info.total
					? " (" + info.shown + " of " + info.total + ")" : "");
			svg.appendChild(text);
		});
	};

	this.bindHover = function () {
		var me = this;
		var tip = document.getElementById(me.tipID);
		if (!tip) { return; }
		var WORDS = { de: "differentially expressed", quiet: "measured, not DE",
		              absent: "not measured", seed: "this metabolite" };
		me.cy.on("mouseover", "node", function (event) {
			var n = event.target;
			n.addClass("hovered");
			var step = n.data("step");
			var label = n.data("fullName") || n.data("label"), id = n.id();
			tip.innerHTML = "<b>" + Ext.String.htmlEncode(label) + "</b>" +
				(label === id ? "" :
					' <span class="pa-hub-tip-hint">' +
					Ext.String.htmlEncode(id) + "</span>") + "<br>" +
				(n.data("kind") || "feature") + " · " + WORDS[n.data("state")] +
				(step ? "<br>" + step + " step" + (step === 1 ? "" : "s") + " away" : "") +
				(step ? me.conditionTable(me.readingOf(id, n.data("kind"))) : "") +
				'<span class="pa-hub-tip-hint">click for expression</span>';
			tip.style.display = "block";
		});
		me.cy.on("mouseout", "node", function (event) {
			event.target.removeClass("hovered");
			tip.style.display = "none";
		});
		me.cy.on("mouseover", "edge", function (event) {
			var e = event.target;
			tip.innerHTML = "<b>" + Ext.String.htmlEncode(me.edgeEnd(e, "source")) +
				" — " + Ext.String.htmlEncode(me.edgeEnd(e, "target")) +
				"</b><br>" + e.data("kind") +
				(e.data("subtype") ? " · " + e.data("subtype") : "") +
				(e.data("pathway") ? "<br>" + e.data("pathway") : "");
			tip.style.display = "block";
		});
		me.cy.on("mouseout", "edge", function () { tip.style.display = "none"; });
		me.cy.on("mousemove", function (event) {
			if (!event.renderedPosition) { return; }
			tip.style.left = (event.renderedPosition.x + 14) + "px";
			tip.style.top = (event.renderedPosition.y + 14) + "px";
		});
	};

	/**
	 * Light one node's own edges; wash out everything else.
	 *
	 * This is the half of "how it connects" that belongs in the GRAPH. Before
	 * it, clicking a node drew a 4px ring on that node and changed nothing
	 * else, so its 72 edges stayed indistinguishable inside a 600-edge wash --
	 * which is precisely why a text list had to carry the whole answer, and
	 * why that list being capped at eight rows lost the panel's point.
	 *
	 * Composes with the step filter rather than fighting it: `.dim` belongs to
	 * setLevel, and an element outside the chosen ring stays dimmed whether or
	 * not it touches the clicked node. This never adds or removes `.dim`.
	 *
	 * `keep` optionally narrows the lit set to one facet (a DE direction or a
	 * pathway), which is what makes a 72-edge node readable rather than merely
	 * complete.
	 */
	this.focusEgo = function (id, keep) {
		var me = this;
		if (!me.cy) { return; }
		me.cy.batch(function () {
			me.cy.elements().removeClass(
				"pa-away pa-ego-edge pa-ego-de pa-ego-node pa-ego-label");
			if (!id) { return; }
			var node = me.cy.getElementById(id);
			if (!node || !node.length) { return; }

			var lit = {}, edges = [];
			lit[id] = 1;
			node.connectedEdges().forEach(function (edge) {
				if (edge.hasClass("dim")) { return; }
				var other = (edge.data("source") === id) ? edge.target() : edge.source();
				if (keep && !keep(edge, other)) { return; }
				edges.push({ edge: edge, other: other });
				lit[other.id()] = 1;
			});

			// Every name, when the ring is small enough to read them; only the
			// DE neighbours when it is not. 44 overlapping labels is not more
			// information than 12.
			var nameAll = Object.keys(lit).length <= me.EGO_LABEL_MAX;

			me.cy.nodes().forEach(function (n) {
				if (n.hasClass("dim")) { return; }
				if (!lit[n.id()]) { n.addClass("pa-away"); return; }
				n.addClass("pa-ego-node");
				var state = n.data("state");
				if (nameAll || state === "de" || n.id() === id) {
					n.addClass("pa-ego-label");
				}
			});
			me.cy.edges().forEach(function (e) { if (!e.hasClass("dim")) { e.addClass("pa-away"); } });
			edges.forEach(function (row) {
				row.edge.removeClass("pa-away").addClass("pa-ego-edge");
				if (row.other.data("state") === "de") { row.edge.addClass("pa-ego-de"); }
			});
		});
	};

	/**
	 * Drag the card's top edge.
	 *
	 * The card is a fixed 300px, and on this job the expression figures alone
	 * measure 1046px -- so its PRIMARY content was already showing at 26% before
	 * any connection list existed. flex-basis, never height: with a height
	 * transition this item resolved to 1px and stayed there, the animation and
	 * the flex pass restarting each other while Cytoscape resized against the
	 * same box.
	 *
	 * The graph is resized on every frame but re-fitted only when the drag
	 * ENDS: re-fitting continuously churns the zoom under the cursor, and not
	 * re-fitting at all leaves the canvas clipped once it shrinks.
	 */
	this.bindResize = function (host) {
		var me = this;
		var grip = host.querySelector(".pa-hub-grip");
		if (!grip) { return; }
		var stage = host.parentNode;

		var begin = function (event) {
			event.preventDefault();
			var startY = (event.touches ? event.touches[0].clientY : event.clientY);
			var startH = host.getBoundingClientRect().height;
			var ceiling = stage ? stage.getBoundingClientRect().height - 160 : 640;

			var move = function (next) {
				var y = (next.touches ? next.touches[0].clientY : next.clientY);
				var wanted = startH + (startY - y);
				me.detailHeight = Math.max(me.DETAIL_MIN, Math.min(ceiling, wanted));
				host.style.flexBasis = me.detailHeight + "px";
				if (me.cy) { me.cy.resize(); me.drawRings(); }
			};
			var end = function () {
				document.removeEventListener("mousemove", move);
				document.removeEventListener("mouseup", end);
				document.removeEventListener("touchmove", move);
				document.removeEventListener("touchend", end);
				me.resizeGraph();
			};
			document.addEventListener("mousemove", move);
			document.addEventListener("mouseup", end);
			document.addEventListener("touchmove", move, { passive: false });
			document.addEventListener("touchend", end);
		};
		grip.addEventListener("mousedown", begin);
		grip.addEventListener("touchstart", begin, { passive: false });
	};

	/**
	 * Say, visibly, that the pane holds more than it shows.
	 *
	 * The reported bug was a row sliced through the middle at the card's edge.
	 * The content was reachable -- the pane scrolls -- but macOS draws overlay
	 * scrollbars that stay invisible until dragged, so a severed row is the
	 * only signal, and it reads as a broken render rather than as "scroll me".
	 */
	this.bindFade = function (host) {
		var pane = host.querySelector(".pa-hub-pane");
		if (!pane) { return; }
		var update = function () {
			var atEnd = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 2;
			var fits = pane.scrollHeight <= pane.clientHeight + 1;
			pane.parentNode.classList.toggle("at-end", atEnd || fits);
		};
		pane.addEventListener("scroll", update, { passive: true });
		paDeferFrame(update);
		return update;
	};

	/** An edge endpoint by name, using the node already on the graph. */
	this.edgeEnd = function (edge, which) {
		var id = edge.data(which);
		var node = this.cy && this.cy.getElementById(id);
		return (node && node.length)
			? (node.data("fullName") || node.data("label")) : id;
	};

	this.bindTap = function () {
		var me = this;
		me.cy.on("tap", "node", function (event) {
			me.cy.nodes().removeClass("picked");
			event.target.addClass("picked");
			me.showNodeDetail(event.target);
		});
		me.cy.on("tap", function (event) {
			// Tapping the background returns to the metabolite, rather than
			// emptying the card: an empty card below the graph reads as a
			// layout bug, and the seed's own numbers are always relevant here.
			if (event.target === me.cy) {
				me.cy.nodes().removeClass("picked");
				me.showSeedDetail(true);
			}
		});
	};

	/* ------------------------------------------------------------------ *
	 * The detail card: summary + one heatmap per omic                     *
	 * ------------------------------------------------------------------ */

	this.clearDetail = function () {
		// Highcharts instances keep resize and tooltip listeners; emptying the
		// container alone orphans them. The Paint handler this replaces never
		// destroyed a single chart.
		this.charts.forEach(function (chart) {
			try { if (chart && chart.destroy) { chart.destroy(); } } catch (e) {}
		});
		this.charts = [];
		var host = document.getElementById(this.detailID);
		if (host) {
			host.innerHTML = "";
			host.classList.remove("is-open");
			// The drag writes an inline flex-basis. Left behind, it outranks
			// the collapsed `flex: 0 0 0` and the closed card keeps its height.
			host.style.flexBasis = "";
		}
		this.focusEgo(null);
		this.resizeGraph();
	};

	/**
	 * Open the card, resizing the canvas so the graph is never covered.
	 *
	 * `reveal` scrolls the card into view, and only a click passes it: the
	 * panel opens its first metabolite by itself on load, and scrolling the
	 * page to Step 3's seventh section because a panel initialised would be a
	 * worse bug than the one this fixes. block:"nearest" is a no-op when the
	 * card is already fully visible, which is the common case once the reader
	 * has scrolled to the section.
	 */
	this.openDetail = function (html, reveal) {
		var me = this;
		var host = document.getElementById(me.detailID);
		if (!host) { return null; }
		host.innerHTML =
			'<button type="button" class="pa-hub-detail-close" ' +
			  'aria-label="Close">&times;</button>' + html;
		host.classList.add("is-open");
		// Switching tab or facet re-renders through clearDetail(), which drops
		// the inline flex-basis. Without this the card snapped back to 300px
		// every time the reader touched a chip, throwing away the height they
		// had just dragged out.
		if (me.detailHeight) { host.style.flexBasis = me.detailHeight + "px"; }
		var close = host.querySelector(".pa-hub-detail-close");
		if (close) {
			close.addEventListener("click", function () {
				if (me.cy) { me.cy.nodes().removeClass("picked"); }
				me.detailHeight = null;   // a close forgets the size; a re-render does not
				me.clearDetail();
			});
		}
		me.resizeGraph();
		if (reveal) { host.scrollIntoView({ block: "nearest" }); }
		return host;
	};

	/**
	 * The canvas is a flex child of the stage; opening the card resizes it.
	 *
	 * resize() alone is not enough, and the difference is visible: Cytoscape
	 * keeps its pan and zoom across a resize, so a canvas that lost 264px of
	 * height kept drawing the same extent and the bottom third of the graph
	 * was simply cut off by the card. Re-fitting costs a zoom change; being
	 * silently clipped costs the nodes.
	 */
	this.resizeGraph = function () {
		var me = this;
		if (!me.cy) { return; }
		// One frame after the class change, so the flex box has been laid out.
		// paDeferFrame, not rAF: rAF never fires in a background tab.
		paDeferFrame(function () {
			if (!me.cy) { return; }
			me.cy.resize();
			me.fitToVisible();
			me.drawRings();
		});
	};

	/**
	 * The selected metabolite: its four step scores, then its own expression.
	 *
	 * These four rows per compound are exactly what the removed grid held. The
	 * grid printed all 852 of them at once and made you filter by step to read
	 * any of them; here they are four rows about the one compound you asked
	 * about, with the step you are looking at marked.
	 */
	this.showSeedDetail = function (reveal) {
		var me = this;
		var entry = me.entryFor(me.seed);
		if (!entry) { me.clearDetail(); return; }
		me.clearDetail();

		// Every column the removed grid carried, for this compound. Percentile
		// and the raw p-value are here because they were in that grid and are
		// not derivable from the rest -- the percentile is the scorer's own
		// size-stratified ECDF rank, which is the only column that says
		// whether a density is unusual FOR A BALL THIS SIZE.
		// A row that scores the SAME genes as the row above it is not a bug,
		// and it is the single thing about this table people ask about: a
		// compound in a small component of the KEGG graph runs out of new
		// neighbours, and every step past that point repeats. C22353's rings
		// are 32, 1, 0, 0 -- so steps 3 and 4 score an identical set of 33
		// genes and print identical numbers. Unexplained, that reads as a
		// broken step control.
		var previousMeasured = null, grew = 0;
		[1, 2, 3, 4].forEach(function (step) {
			var row = entry.steps[step];
			if (!row) { return; }
			var count = Number(row.DEN) + Number(row.noDEN);
			if (previousMeasured === null || count > previousMeasured) { grew = step; }
			previousMeasured = count;
		});

		var rows = [1, 2, 3, 4].map(function (step) {
			var row = entry.steps[step];
			if (!row) {
				return '<tr class="is-absent"><td>' + step +
					'</td><td colspan="6">not scored</td></tr>';
			}
			var fdr = Number(row.padjust);
			var percentile = Number(row.Percentile);
			var repeats = (step > grew);
			return '<tr class="' + (step === me.level ? "is-current " : "") +
				(repeats ? "is-saturated" : "") + '"' +
				(repeats ? ' title="No new neighbours at this step, so it scores' +
				           ' the same genes as step ' + grew + '"' : "") + '>' +
				'<td>' + step + '</td>' +
				'<td>' + row.DEN + '</td>' +
				'<td>' + (Number(row.DEN) + Number(row.noDEN)) + '</td>' +
				'<td>' + (Number(row.Percentage) * 100).toFixed(1) + '%</td>' +
				'<td>' + (isFinite(percentile)
					? (percentile * 100).toFixed(0) + '%' : "-") + '</td>' +
				'<td>' + me.fmt(Number(row.pvalue)) + '</td>' +
				'<td' + (fdr < 0.05 ? ' class="is-significant"' : "") + '>' +
				  me.fmt(fdr) + '</td></tr>';
		}).join("");

		var host = me.openDetail(
			'<div class="pa-hub-grip" title="Drag to resize"><i></i></div>' +
			'<h3 class="pa-hub-detail-title">' + me.nameWithID(entry.ID, "compound") +
			  ' <span class="pa-hub-detail-where">' +
			  'the metabolite this network is centred on</span></h3>' +
			'<div class="pa-hub-detail-body"><div class="pa-hub-pane">' +
			  '<table class="pa-hub-steptable">' +
			    '<thead><tr><th>Step</th><th>DE</th><th>Measured</th>' +
			    '<th>% DE</th><th title="Where this density ranks among balls ' +
			    'of a similar size">Percentile</th><th>p</th>' +
			    '<th>FDR</th></tr></thead>' +
			    '<tbody>' + rows + '</tbody>' +
			  '</table>' +
			  // The two counts on screen measure different things and would
			  // otherwise look like a contradiction: the scorer counts only
			  // MEASURED genes and counts them cumulatively (scorer.py:88,
			  // `ids[measured_gene[ids]]`), while a chip counts every node --
			  // compounds and unmeasured genes included -- in one ring.
			  '<p class="pa-hub-detail-summary">' +
			    (grew < 4
			      ? '<b>' + Ext.String.htmlEncode(entry.name) + '</b> has no ' +
			        'neighbours beyond step ' + grew + ', so the greyed steps ' +
			        'score the same genes and repeat its numbers. ' : "") +
			    'Counts are cumulative and count only genes measured in your ' +
			    'data; the step chips above count every node in a single ring, ' +
			    'so the two do not add up.</p>' +
			  '<div class="pa-hub-omics"></div>' +
			'</div></div>', reveal);
		me.focusEgo(null);
		if (host) {
			me.fillOmics(host, me.seed, "compound");
			me.bindResize(host);
			me.bindFade(host);
		}
	};

	/**
	 * The clicked node's connections, as the model behind the card.
	 *
	 * Scoped to the CURRENT STEP, because everything else in this panel is:
	 * the ring chips gate the graph, the ring labels, and the notice above it.
	 * At step 1 gene 27053 has 15 edges in the fetched subgraph and exactly one
	 * of them is in view -- a card reading "Connections 15" beside a graph
	 * lighting one edge is the same kind of untruth as the eight-row cap this
	 * change removes. The count on the tab always equals what the graph lights.
	 */
	this.connectionsFor = function (node) {
		var me = this;
		var id = node.id();
		var edges = node.connectedEdges().filter(function (e) {
			return !e.hasClass("dim");
		}).map(function (e) {
			return { source: e.data("source"), target: e.data("target"),
			         kind: e.data("kind"), subtype: e.data("subtype"),
			         pathway: e.data("pathway") };
		});
		return paHubConnections(edges, id, function (other) {
			var n = me.cy && me.cy.getElementById(other);
			return (n && n.length)
				? { name: n.data("fullName") || n.data("label"), state: n.data("state") }
				: { name: other, state: "absent" };
		});
	};

	/** A facet key -> the predicate the graph and the list both filter on. */
	this.facetFilter = function () {
		var facet = this.detailFacet;
		if (!facet) { return null; }
		var split = facet.indexOf(":");
		var kind = facet.slice(0, split), value = facet.slice(split + 1);
		return function (edge, other) {
			return (kind === "state")
				? other.data("state") === value
				: (edge.data("pathway") || "") === value;
		};
	};

	/**
	 * The connection list: all of it, grouped, DE first.
	 *
	 * Every row the node has. The count in the heading is the whole point --
	 * "8 of 72" used to be true and unsaid.
	 */
	this.connectionsHTML = function (model) {
		var me = this;
		var facet = me.detailFacet;
		var shownRows = 0;

		var chips = "";
		["de", "quiet", "absent"].forEach(function (state) {
			var n = model.states[state] || 0;
			if (!n) { return; }
			var WORD = { de: "differentially expressed", quiet: "measured",
			             absent: "not measured" };
			chips += '<button type="button" class="pa-hub-facet' +
				(facet === "state:" + state ? " is-on" : "") +
				'" data-facet="state:' + state + '">' +
				'<i class="sw ' + state + '"></i>' + WORD[state] +
				' <span class="n">' + n + '</span></button>';
		});
		model.groups.forEach(function (group) {
			if (!group.pathway) { return; }
			chips += '<button type="button" class="pa-hub-facet' +
				(facet === "pathway:" + group.pathway ? " is-on" : "") +
				'" data-facet="pathway:' + Ext.String.htmlEncode(group.pathway) + '">' +
				Ext.String.htmlEncode(group.pathway) +
				' <span class="n">' + group.rows.length + '</span></button>';
		});

		var body = model.groups.map(function (group) {
			var rows = group.rows.filter(function (row) {
				if (!facet) { return true; }
				var split = facet.indexOf(":");
				return (facet.slice(0, split) === "state")
					? row.state === facet.slice(split + 1)
					: group.pathway === facet.slice(split + 1);
			});
			if (!rows.length) { return ""; }
			shownRows += rows.length;
			var items = rows.map(function (row) {
				return '<li><i class="sw ' + row.state + '"></i>' +
					'<span class="pa-hub-dir" title="' +
					  (row.direction === "out" ? "from this node" : "to this node") +
					  '">' + (row.direction === "out" ? "&rarr;" : "&larr;") + '</span>' +
					'<span class="nm">' + Ext.String.htmlEncode(row.name) +
					(row.ambiguous ? ' <span class="pa-hub-id">' +
					                 Ext.String.htmlEncode(row.id) + '</span>' : "") +
					'</span><span class="rel">' + Ext.String.htmlEncode(row.kind) +
					(row.subtype ? " &middot; " + Ext.String.htmlEncode(row.subtype) : "") +
					'</span></li>';
			}).join("");
			return '<p class="pa-hub-group">' +
				'<span class="pw">' + (group.pathway
					? Ext.String.htmlEncode(group.pathway) : "no pathway recorded") + '</span>' +
				'<span class="n">' + rows.length + '</span></p>' +
				'<ul class="pa-hub-conns">' + items + '</ul>';
		}).join("");

		return '<p class="pa-hub-countline">' +
				'<b>' + model.partners + '</b> <span>partner' +
				(model.partners === 1 ? "" : "s") + '</span> ' +
				'<b>' + model.total + '</b> <span>link' +
				(model.total === 1 ? "" : "s") + '</span> ' +
				'<b>' + model.groups.length + '</b> <span>pathway' +
				(model.groups.length === 1 ? "" : "s") + '</span></p>' +
			(chips ? '<div class="pa-hub-facets">' + chips + '</div>' : "") +
			(facet ? '<p class="pa-hub-filtered">Showing ' + shownRows + " of " +
			         model.total + ' &middot; <button type="button" ' +
			         'class="pa-hub-clearfacet">show all</button></p>' : "") +
			body;
	};

	/**
	 * A clicked node: what it is, how far, its expression, how it connects.
	 *
	 * Expression and connections are TABS, not one stacked scroll. On this job
	 * the omic figures measure 1046px and the card's pane is 269px, so stacking
	 * a second tall thing underneath them meant neither was readable -- the
	 * reported symptom was a connection row sliced in half at the card's edge.
	 * Expression stays the tab that opens, because that is what the card showed
	 * before; the connection count rides on the other tab so it is never silent.
	 */
	this.showNodeDetail = function (node) {
		var me = this;
		var id = node.id();
		var kind = node.data("kind");
		var step = node.data("step");
		if (step === 0) { me.showSeedDetail(true); return; }

		me.clearDetail();
		var seedName = me.nameOf(me.seed, "compound");
		// No direction here, and none on the node either. This feature has one
		// value per condition and the figures below print all of them; a summary
		// word would have to pick one, which is the bug this panel just lost.
		var WORDS = { de: "differentially expressed &mdash; each wedge on the node is " +
		                  "one condition, in the same colours as the heatmap below",
		              quiet: "measured, not differentially expressed",
		              absent: "not measured in any omic you uploaded" };
		var state = node.data("state");
		var model = me.connectionsFor(node);
		me.focusEgo(id, me.facetFilter());

		var pane = (me.detailTab === "conn")
			? me.connectionsHTML(model)
			: '<p class="pa-hub-detail-summary">' + (WORDS[state] || state) + '.</p>' +
			  '<div class="pa-hub-omics"></div>';

		var host = me.openDetail(
			'<div class="pa-hub-grip" title="Drag to resize"><i></i></div>' +
			'<h3 class="pa-hub-detail-title">' + me.nameWithID(id, kind) +
			  ' <span class="pa-hub-detail-where">' + kind + " &middot; " + step +
			  " step" + (step === 1 ? "" : "s") + " from " +
			  Ext.String.htmlEncode(seedName) + '</span></h3>' +
			'<div class="pa-hub-tabs">' +
			  '<button type="button" class="pa-hub-tab' +
			    (me.detailTab === "expr" ? " is-on" : "") + '" data-tab="expr">' +
			    'Expression</button>' +
			  '<button type="button" class="pa-hub-tab' +
			    (me.detailTab === "conn" ? " is-on" : "") + '" data-tab="conn">' +
			    'Connections <span class="n">' + model.total + '</span></button>' +
			'</div>' +
			'<div class="pa-hub-detail-body">' +
			  '<div class="pa-hub-pane">' + pane + '</div>' +
			'</div>', true);
		if (!host) { return; }

		if (me.detailTab === "expr") { me.fillOmics(host, id, kind); }
		me.bindResize(host);
		me.bindFade(host);

		host.querySelectorAll(".pa-hub-tab").forEach(function (button) {
			button.addEventListener("click", function () {
				me.detailTab = button.getAttribute("data-tab");
				me.showNodeDetail(node);
			});
		});
		host.querySelectorAll(".pa-hub-facet").forEach(function (button) {
			button.addEventListener("click", function () {
				var key = button.getAttribute("data-facet");
				me.detailFacet = (me.detailFacet === key) ? null : key;
				me.showNodeDetail(node);
			});
		});
		var clear = host.querySelector(".pa-hub-clearfacet");
		if (clear) {
			clear.addEventListener("click", function () {
				me.detailFacet = null;
				me.showNodeDetail(node);
			});
		}
	};

	/**
	 * Draw one heatmap + plot per omic, from /pa_hub_feature.
	 *
	 * globalExpressionData carries omicsValues[0] and nothing else, so drawing
	 * from it showed one of this job's four gene-based omics and said nothing
	 * about the other three -- while the pathway views on the same page show
	 * them all. The clicked feature's full set is a few hundred bytes, so it is
	 * fetched per click and cached.
	 */
	this.fillOmics = function (host, id, kind) {
		var me = this;
		var slot = host.querySelector(".pa-hub-omics");
		if (!slot) { return; }
		var key = kind + "|" + id;

		var draw = function (payload) {
			// The card may have been replaced while the request was in flight.
			if (!document.body.contains(slot)) { return; }
			var omics = (payload && payload.omics) || [];
			if (!omics.length) {
				slot.innerHTML = '<p class="pa-hub-detail-summary">' +
					'No expression was measured for it, so there is nothing to plot.</p>';
				return;
			}
			me.drawOmics(slot, omics);
		};

		if (me.featureCache[key]) { draw(me.featureCache[key]); return; }
		slot.innerHTML = '<p class="pa-hub-detail-summary">' +
			'<i class="fa fa-cog fa-spin"></i> Loading expression…</p>';
		$.post(SERVER_URL_PA_HUB_FEATURE, {
			jobID: me.model.getJobID(), featureID: id, featureType: kind
		}).done(function (payload) {
			if (typeof payload === "string") {
				try { payload = JSON.parse(payload); } catch (e) { payload = null; }
			}
			if (!payload || !payload.success) {
				if (!document.body.contains(slot)) { return; }
				slot.innerHTML = '<p class="pa-hub-detail-summary">' +
					((payload && payload.errorMessage) ||
					 "The expression for this feature could not be loaded.") + '</p>';
				return;
			}
			me.featureCache[key] = payload;
			draw(payload);
		}).fail(function () {
			if (!document.body.contains(slot)) { return; }
			slot.innerHTML = '<p class="pa-hub-detail-summary">' +
				'Could not reach the server for this feature’s expression.</p>';
		});
	};

	/**
	 * One box per OMIC, with every row that omic has for this feature.
	 *
	 * Grouping matters: one KEGG gene can map to several input features, and
	 * the payload carries one OmicValue per (omic, input row). Gene 100040843
	 * has seven "Gene expression" rows -- seven different Ensembl ids -- and
	 * drawing a box each produced seven single-row heatmaps under seven
	 * identical headings, which reads as a rendering fault and hides that the
	 * rows are different input features of the same gene. The pathway views
	 * group by omic name and stack the rows; so does this.
	 */
	this.drawOmics = function (slot, omics) {
		var me = this;
		var summaries = me.model.getDataDistributionSummaries() || {};

		/* NOT the parent's `visualOptions`. That object belongs to the pathway
		   NETWORK - node sizes, edge classes, p-value methods, the visible
		   pathway list - and has never had a `colorScale` or a
		   `colorReferences` on it. Reading it here handed `undefined` to both
		   painters. generateHeatmap has its own "bwr" fallback so the cells
		   came out right; paColorLegend passed the undefined straight to
		   getColor, which fell through its unknown-scale branch and returned
		   rgb(0,0,0) for every stop. That is the solid black bar in the panel.
		   Both ends now name the same defaults, which is the only arrangement
		   in which they cannot disagree again. */
		var visual = {
			colorScale: PA_DEFAULT_COLOR_SCALE,
			colorReference: PA_DEFAULT_COLOR_REFERENCE
		};

		var order = [], grouped = {};
		omics.forEach(function (o) {
			if (!(o.omicName in summaries)) { return; }
			if (!grouped[o.omicName]) { grouped[o.omicName] = []; order.push(o.omicName); }
			grouped[o.omicName].push(OmicValue.loadFromJSON(o));
		});

		if (!order.length) {
			var named = omics.map(function (o) {
				return Ext.String.htmlEncode(o.omicName);
			}).filter(function (name, index, all) {
				return all.indexOf(name) === index;
			});
			slot.innerHTML = '<p class="pa-hub-detail-summary">This job carries no ' +
				'distribution summary for ' + named.join(", ") +
				', so the heatmap cannot be scaled.</p>';
			return;
		}

		slot.innerHTML = order.map(function (omicName, index) {
			// The colour ramp these heatmaps are painted with. The charts carry
			// legend:{enabled:false}, so without this the scale is stated
			// nowhere. Guarded: a bad summary for one omic must not stop the rest.
			var legend = "";
			try {
				legend = paColorLegend(
					getMinMax(summaries[omicName], visual.colorReference),
					visual.colorScale,
					{caption: paColourReferenceLabel(visual.colorReference)});
			} catch (error) {
				console.warn("[hub] no colour legend for " + omicName + ": " + error);
			}
			var count = grouped[omicName].length;
			/* Row pitch is the pathway views' 30px, so a five-row omic here and
			   a five-row omic there are the same object. The constant on the end
			   is the chart's own furniture, and 100 was not enough for it: the
			   condition names are drawn at -45 degrees, and on this job the
			   bottom of "I/C_24h" was cut off by the container. It has to cover
			   the plot's top margin AND the rotated axis beneath it. */
			var height = (count * 30) + PA_OMIC_CHART_FURNITURE;
			// The heatmap div and the plot div must be ADJACENT SIBLINGS with
			// the heatmap first: the heatmap's point handlers reach the plot
			// with .parent().next().highcharts(). Anything between them makes
			// that undefined and hovering a cell throws. The wrapper below keeps
			// them adjacent -- it is their parent, not a separator.
			return '<div class="contentbox pa-hub-omic">' +
				'<div class="pa-hub-omic-head">' +
					'<h4>' + Ext.String.htmlEncode(omicName) +
					(count > 1 ? ' <span class="pa-hub-id">' + count +
					             ' input rows</span>' : "") + '</h4>' +
					legend +
				'</div>' +
				'<div class="pa-hub-omic-figure">' +
					'<div class="PA_step5_heatmapContainer" ' +
					  'id="' + me.detailID + '_hm' + index + '" ' +
					  'style="height:' + height + 'px"></div>' +
					'<div class="PA_step5_plotContainer" ' +
					  'id="' + me.detailID + '_pl' + index + '" ' +
					  'style="height:' + height + 'px"></div>' +
				'</div>' +
				'</div>';
		}).join("");

		order.forEach(function (omicName, index) {
			var values = grouped[omicName];
			var headers = paOmicHeaders(me.model, omicName);
			try {
				me.charts.push(generateHeatmap(me.detailID + "_hm" + index, omicName,
					values, summaries, visual, headers));
				me.charts.push(generatePlot(me.detailID + "_pl" + index, omicName,
					values, summaries, null, visual, headers));
			} catch (error) {
				// A silent guard reads as a dead click, so say what happened.
				console.warn("[hub] could not draw " + omicName + ": " + error);
				var box = document.getElementById(me.detailID + "_hm" + index);
				if (box) {
					box.innerHTML = '<p class="pa-hub-detail-summary">This omic could ' +
						'not be drawn.</p>';
				}
			}
		});
	};

	/* ------------------------------------------------------------------ *
	 * Rings and zoom                                                      *
	 * ------------------------------------------------------------------ */

	/**
	 * Fit the lit subset, but never magnify past MAX_FIT_ZOOM.
	 *
	 * fit() alone scales to fill: step 1 of a compound with five neighbours
	 * came up at 3x, with node labels rendered 60px tall and five dots spread
	 * across a 1000px canvas. Zoom is not carrying information here -- the
	 * rings are -- so it is capped and the graph simply sits in the middle of
	 * the space it does not need.
	 */
	this.MAX_FIT_ZOOM = 1.3;
	this.fitToVisible = function () {
		if (!this.cy) { return; }
		var lit = this.cy.elements().not(".dim");
		var shown = lit.length ? lit : this.cy.elements();
		this.cy.fit(shown, 40);
		if (this.cy.zoom() > this.MAX_FIT_ZOOM) {
			this.cy.zoom(this.MAX_FIT_ZOOM);
			this.cy.center(shown);
		}
		this.applyLabelZoom();
	};

	/** Labels only where they can be read. */
	this.LABEL_ZOOM = 0.55;
	this.applyLabelZoom = function () {
		if (!this.cy) { return; }
		var far = this.cy.zoom() < this.LABEL_ZOOM;
		this.cy.nodes().toggleClass("far", far);
		// The seed keeps its label at every zoom -- it is the one node whose
		// identity the panel is about.
		this.cy.nodes("[seed = 1]").removeClass("far");
	};

	/**
	 * Light the chosen ring, dim the rest. Hide-don't-remove: removing elements
	 * forces a relayout and the rings jump between steps.
	 */
	this.setLevel = function (level) {
		var me = this;
		me.level = level;
		me.renderSteps();
		if (!me.cy) { return; }
		me.cy.batch(function () {
			me.cy.nodes().forEach(function (n) {
				var step = n.data("step");
				n.toggleClass("dim", !(step === 0 || step <= level));
			});
			me.cy.edges().forEach(function (e) {
				e.toggleClass("dim",
					e.source().hasClass("dim") || e.target().hasClass("dim"));
			});
		});
		me.fitToVisible();
		me.drawRings();
		me.describe(me.payload);
		// The step table marks the step you are looking at, so it has to be
		// redrawn -- but only while the card is showing the metabolite, or
		// changing step would throw away the node you clicked.
		var host = document.getElementById(me.detailID);
		if (host && host.querySelector(".pa-hub-steptable")) {
			me.showSeedDetail();
		} else {
			// A node card is open. setLevel just rewrote .dim underneath the
			// focus, so the lit set has to be derived again or an edge can be
			// dimmed and highlighted at the same time.
			var picked = me.cy.nodes(".picked");
			if (picked.length) { me.focusEgo(picked[0].id(), me.facetFilter()); }
		}
	};

	/* ------------------------------------------------------------------ *
	 * Component                                                           *
	 * ------------------------------------------------------------------ */

	this.getComponent = function () {
		var me = this;
		var legend =
			'<div class="pa-hub-legend">' +
			  '<span><i class="sw de"></i>differentially expressed &mdash; one wedge ' +
			    'per condition, in that omic\'s heatmap colours</span>' +
			  '<span><i class="sw quiet"></i>measured, not DE</span>' +
			  '<span><i class="sw absent"></i>not measured</span>' +
			  '<span><i class="sw seed"></i>this metabolite</span>' +
			'</div>';

		this.component = Ext.create("Ext.panel.Panel", {
			// contentbox + the 10px margin every other Step 3 card uses; without
			// them this panel sits inset from its neighbours and draws no edge.
			cls: "contentbox pa-hub-net",
			border: 0,
			margin: "10 10 10 10",
			// Self-suppressing, the way the other network views are: the panel
			// starts hidden and reveals itself in afterrender only if the job
			// actually scored some metabolites. It is the whole hub UI now, not
			// a popup opened from a table, so it must not need a click to appear.
			hidden: true,
			// The heading is an <h2> in the body, NOT an Ext panel header:
			// paTocSections() queries h2 only, so an Ext header never reaches
			// the contents rail.
			html:
				'<h2 id="HubNetworkSection">Metabolite hub analysis</h2>' +
				'<p class="pa-hub-intro">Which metabolites have differentially ' +
				'expressed genes concentrated around them in the KEGG network. ' +
				'Pick a metabolite; click any node for its expression.</p>' +
				'<div class="more-net-body">' +
				  '<div class="more-net-sidepanel pa-hub-listrail">' +
				    '<div class="pa-hub-railhead">' +
				      '<input type="search" id="' + me.searchID + '" class="pa-hub-search" ' +
				        'placeholder="Search metabolites…" aria-label="Search metabolites">' +
				      '<div class="pa-hub-railsort">' +
				        '<label class="pa-hub-sortlabel" for="' + me.sortID + '">Rank by</label>' +
				        '<select id="' + me.sortID + '" class="pa-hub-sort">' +
				          '<option value="padjust">FDR</option>' +
				          '<option value="density">% DE neighbours</option>' +
				          '<option value="den">DE neighbours</option>' +
				          '<option value="name">Name</option>' +
				        '</select>' +
				      '</div>' +
				      '<p class="pa-hub-railcount" id="' + me.countID + '"></p>' +
				    '</div>' +
				    '<div class="pa-hub-listbody" id="' + me.listID + '"></div>' +
				  '</div>' +
				  '<div class="pa-hub-stage more-net-canvas">' +
				    '<div class="pa-hub-stagehead">' +
				      '<span class="pa-hub-seedname" id="' + me.seedNameID + '"></span>' +
				      '<span class="pa-hub-steplabel">steps away:</span>' +
				      '<span class="pa-hub-steps" id="' + me.stepsID + '"></span>' +
				      legend +
				    '</div>' +
				    '<div id="' + me.noticeID + '" class="pa-net-notice"></div>' +
				    '<div class="pa-hub-plot">' +
				      '<svg id="' + me.ringsID + '" class="pa-hub-rings"></svg>' +
				      '<div id="' + me.canvasID + '" class="pa-net-canvas"></div>' +
				      '<div id="' + me.tipID + '" class="pa-hub-tip"></div>' +
				    '</div>' +
				    '<div id="' + me.detailID + '" class="pa-hub-detail"></div>' +
				  '</div>' +
				'</div>',
			listeners: {
				// paDeferFrame, NOT requestAnimationFrame: rAF never runs in a
				// background tab and the panel came up permanently blank.
				afterrender: function () {
					paDeferFrame(function () {
						if (!me.hasData()) { return; }   // stays hidden
						me.component.show();
						me.mount();
						if (me.cy) { me.cy.resize(); me.fitToVisible(); me.drawRings(); }
					});
				},
				expand: function () {
					if (me.cy) { me.cy.resize(); me.fitToVisible(); me.drawRings(); }
				},
				beforedestroy: function () {
					me.clearDetail();
					if (me.cy) { me.cy.destroy(); me.cy = null; }
				}
			}
		});
		return this.component;
	};

	this.bindControls = function () {
		var me = this;
		var search = document.getElementById(me.searchID);
		if (search && !search.dataset.hubBound) {
			search.dataset.hubBound = "1";
			var timer = null;
			search.addEventListener("input", function () {
				// buffer:100 is the house debounce (ExtJS_extensions.js:209-239)
				clearTimeout(timer);
				timer = setTimeout(function () {
					me.query = search.value || "";
					me.renderList();
				}, 100);
			});
		}
		var sort = document.getElementById(me.sortID);
		if (sort && !sort.dataset.hubBound) {
			sort.dataset.hubBound = "1";
			sort.addEventListener("change", function () {
				me.sortKey = sort.value;
				me.sortList();
				me.renderList();
			});
		}
	};

	/** Open the most significant metabolite so the panel is never empty. */
	this.selectFirst = function () {
		if (this.seed || !this.metabolites.length) { return; }
		this.showCompound(this.metabolites[0].ID);
	};

	/** Whether this job has anything for the panel to show. */
	this.hasData = function () {
		return this.metabolites.length > 0;
	};
}
PA_Step3HubNetworkView.prototype = new View();

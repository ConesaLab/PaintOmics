//
// PA_Step4WalkView.js
//
// The Walk column of a Step 4 pathway, and the renderers the AI widget reuses
// for the universal walk.
//
// An Agentic Graph Walk runs on the server's queue (/ai_walk_start), never on
// a request thread: the walker, the Writer, the sense check and the Narrator
// take minutes. This column polls /ai_walk_status, lists the legs as the
// agent walks them, draws them on the diagram, and ends with the Results
// section the server checked. Everything the model wrote is inserted as text
// nodes, never as markup: a leg's reading and a statement's prose are model
// output and do not pass the report sanitiser.
//
// The layer on the diagram follows PA_Step4EvidenceOverlay's rules: geometry
// is built with createElementNS (svg.js 2.0.5 .path() is dead in Chrome), the
// scale comes from the drawn raster rather than visualOptions.adjustFactor,
// every mark sets its attributes inline so the PNG export carries it, and no
// mark takes pointer events away from the feature boxes underneath.
//

var PA_WALK_SVG_NS = "http://www.w3.org/2000/svg";

/* One hue for walked legs, one for seeds. Teal and amber: violet belongs to
   the evidence layer, and red and blue already encode value on the boxes. */
var PA_WALK_STYLE = {
	leg: "#0F766E", legFocus: "#0B4F4A", seed: "#B45309", casing: "#FFFFFF"
};

/* The kind of axis the design card read off the user's columns, in words. */
var PA_WALK_AXIS_WORDS = {
	time: "time points, in order",
	ordered: "ordered values (doses or concentrations)",
	groups: "conditions with no order",
	unlabeled: "no column labels"
};

/* Where in a paper a quoted passage sits, as the paper agent's check found it. */
var PA_WALK_SECTION_WORDS = {
	abstract: "Abstract",
	introduction: "Main text · Introduction",
	results: "Main text · Results",
	discussion: "Main text · Discussion",
	other: "Main text"
};

/* The five checks a walk passes before its text is shown, in the order the
   chips take, each with its label. The keys are the server's (service.GATES). */
var PA_WALK_GATES = [
	["artifact", "Not a graph artifact"],
	["title", "Title fits the body"],
	["direction", "Direction logic"],
	["context", "Citations in context"],
	["anchor", "Anchored to the perturbation"]
];

/* How far from the anchor a walked node counts as near (anchor.REACH_RADIUS). */
var PA_WALK_REACH_RADIUS = 2;

/* The perturbation's direction, as the design card read it, in plain words. */
var PA_WALK_DIRECTION_WORDS = {up: "induced (up)", down: "knocked out or inhibited (down)"};

/* A panel regulator's class in the direction check, in words. */
var PA_WALK_REGULATOR_WORDS = {feedback: "feedback reporter", inhibitor: "upstream inhibitor"};

function paWalkEl(tag, className, text) {
	var el = document.createElement(tag);
	if (className) { el.className = className; }
	if (text !== undefined && text !== null) { el.textContent = String(text); }
	return el;
}

function paWalkSign(sign) {
	return sign > 0 ? "+" : (sign < 0 ? "−" : "");
}

/* A leg's edge in words: "KEGG FoxO signaling pathway · expression +". A leg
   on one of the job's own miRNA edges names the file it came from. */
function paWalkEdgeText(edge) {
	if (!edge) { return "jump"; }
	if (edge.db === "job") {
		return "miRNA target, from your " + String(edge.pathway || "miRNA") + " file";
	}
	var subtype = edge.subtype ? " · " + String(edge.subtype).split(",")[0] : "";
	return String(edge.db || "") + " " + String(edge.name || edge.pathway || "") + subtype + " " + paWalkSign(edge.sign);
}

/*
 * Paragraph text with its tags turned into chips: [e3] is a leg, [Foxo1 ·
 * Gene expression] is a layer, [2] is a paper. Built from text nodes only.
 * options.onLeg(n) makes leg chips clickable; papers is the view's
 * {ref: {pmid, title}} map.
 */
function paWalkChipText(text, papers, options) {
	var holder = paWalkEl("span");
	var pattern = /\[(e\d+(?:\s*,\s*e?\d+)*)\]|\[([^\[\]]+? · [^\[\]]+?)\]|\[(\d+(?:\s*,\s*\d+)*)\]/g;
	var source = String(text || ""), last = 0, match;
	options = options || {};
	while ((match = pattern.exec(source)) !== null) {
		if (match.index > last) {
			holder.appendChild(document.createTextNode(source.slice(last, match.index)));
		}
		if (match[1]) {
			/* [e3] or [e3, e5]: each leg its own chip. */
			match[1].split(/\s*,\s*/).forEach(function (token) {
				var n = token.replace(/^e/, "");
				var leg = paWalkEl("span", "pa-walk-chip pa-walk-chip-leg", "e" + n);
				leg.setAttribute("data-leg", n);
				if (options.onLeg) {
					leg.setAttribute("role", "button");
					leg.setAttribute("tabindex", "0");
					leg.title = "Show leg e" + n + " on the map";
				}
				holder.appendChild(leg);
			});
		} else if (match[2]) {
			holder.appendChild(paWalkEl("span", "pa-walk-chip pa-walk-chip-layer", match[2]));
		} else {
			/* [3] or [3, 5]: each number its own PubMed link. */
			match[3].split(/\s*,\s*/).forEach(function (ref, index) {
				holder.appendChild(document.createTextNode(index ? " " : ""));
				var paper = (papers || {})[ref];
				if (paper && paper.pmid && /^\d+$/.test(String(paper.pmid))) {
					var link = paWalkEl("a", "pa-walk-cite", "[" + ref + "]");
					link.href = "https://pubmed.ncbi.nlm.nih.gov/" + paper.pmid + "/";
					link.target = "_blank";
					link.rel = "noopener noreferrer";
					link.title = String(paper.title || "Open in PubMed") + (paper.year ? " (" + paper.year + ")" : "") +
						paWalkQuoteTitle(paper);
					holder.appendChild(link);
				} else {
					holder.appendChild(document.createTextNode("[" + ref + "]"));
				}
			});
		}
		last = pattern.lastIndex;
	}
	if (last < source.length) {
		holder.appendChild(document.createTextNode(source.slice(last)));
	}
	if (options.onLeg) {
		$(holder).on("click keydown", ".pa-walk-chip-leg", function (event) {
			if (event.type === "keydown" && event.keyCode !== 13 && event.keyCode !== 32) { return; }
			event.preventDefault();
			options.onLeg(parseInt(this.getAttribute("data-leg"), 10));
		});
	}
	return holder;
}

/* The first confirmed passage of a paper, for a citation's hover text. */
function paWalkQuoteTitle(paper) {
	var first = ((paper && paper.evidence) || [])[0];
	if (!first || !first.quote) { return ""; }
	var quote = String(first.quote);
	return "\n" + (PA_WALK_SECTION_WORDS[first.section] || "Main text") + ": \u201c" +
		(quote.length > 240 ? quote.slice(0, 237) + "..." : quote) + "\u201d";
}

/* The passages a paper agent found in one paper: where each sits, the passage
   exactly as the paper words it, and the claim it was cited for. */
function paWalkEvidenceNodes(paper) {
	var nodes = [];
	((paper && paper.evidence) || []).forEach(function (item) {
		if (!item || !item.quote) { return; }
		var quote = paWalkEl("blockquote", "pa-walk-quote");
		quote.appendChild(paWalkEl("span", "pa-walk-quote-where", PA_WALK_SECTION_WORDS[item.section] || "Main text"));
		quote.appendChild(paWalkEl("span", "pa-walk-quote-text", "\u201c" + String(item.quote) + "\u201d"));
		nodes.push(quote);
		if (item.claim) {
			nodes.push(paWalkEl("p", "pa-walk-quote-claim", "Cited for: " + String(item.claim)));
		}
	});
	return nodes;
}

/*
 * The five gates the server filed with a walk, or null for a walk sealed
 * before the gates existed. The server's view writes `checks.gates` as an
 * empty map for those, so "absent" here means missing or empty: an old walk
 * must render exactly as it did, not as a walk that failed every check.
 */
function paWalkGates(view) {
	var gates = view && view.checks && view.checks.gates;
	if (!gates || typeof gates !== "object") { return null; }
	for (var name in gates) {
		if (Object.prototype.hasOwnProperty.call(gates, name)) { return gates; }
	}
	return null;
}

/* Whether the walk's text (Results, references, statements) may be shown:
   every gate passed, or the walk predates the gates. */
function paWalkTextShown(view) {
	return !paWalkGates(view) || (view.checks.rendered === true);
}

/* The perturbed gene the design named, or null. */
function paWalkAnchorGene(view) {
	var anchor = view && view.checks && view.checks.anchor;
	return (anchor && anchor.gene) ? String(anchor.gene) : null;
}

/* A count with its noun: "3 modules", "1 leg". */
function paWalkCount(n, noun) {
	n = Number(n) || 0;
	return n + " " + noun + (n === 1 ? "" : "s");
}

/* A number as the server rounded it, or a dash when it never computed one. */
function paWalkNumber(value) {
	return (value === null || value === undefined || !isFinite(Number(value))) ? "–" : String(Number(value));
}

/* A fraction in whole percent: 0.62 → "62%". */
function paWalkPercent(value) {
	return (value === null || value === undefined || !isFinite(Number(value))) ? "–" : Math.round(100 * Number(value)) + "%";
}

/*
 * The line above the Results: which gene the experiment perturbed, in which
 * direction, and whether the walk is anchored to it in this organism's
 * network. Null for a walk that predates the gates.
 */
function paWalkHeaderNode(view) {
	if (!paWalkGates(view)) { return null; }
	var anchor = view.checks.anchor;
	var text;
	if (anchor && anchor.gene) {
		text = "Perturbation: " + String(anchor.gene) + ", " +
			(PA_WALK_DIRECTION_WORDS[anchor.direction] || "direction not stated") + " · ";
		if (anchor.in_graph) {
			var edges = Number(anchor.edges_added) || 0;
			var sources = String(anchor.source || "").split(",")
				.map(function (s) { return s.trim(); }).filter(Boolean).join(", ");
			text += "anchored in the network (" + (edges
				? paWalkCount(edges, "target edge") + (sources ? " from " + sources : "")
				: "its own edges, no regulator targets added") + ")";
		} else {
			text += "not connected in this organism's network, so the walk is unanchored";
		}
	} else {
		text = "Perturbation: no perturbed gene named in the design · unanchored walk";
	}
	return paWalkEl("p", "pa-walk-header", text);
}

/* A gate's numbers in words, for the chip's hover text. */
function paWalkGateTitle(name, gate, state) {
	gate = gate || {};
	if (state === "na") {
		return "Not applicable: " + (gate.why || "nothing to check");
	}
	switch (name) {
	case "artifact": {
		var real = gate.real || {}, nul = gate.null_mean || {};
		return paWalkCount(real.modules, "module") + " found; permuted data gives " + paWalkNumber(nul.modules) +
			" on average (p = " + paWalkNumber(gate.p_modules) + "); seeds " +
			(Number(gate.p_heat) < 0.05 ? "hotter than chance" : "no hotter than chance") +
			" (p = " + paWalkNumber(gate.p_heat) + "); " + paWalkCount(gate.currency_legs, "leg") +
			" through currency metabolites; " + paWalkCount(gate.k, "permutation");
	}
	case "title": {
		var verbs = gate.verbs_found || [];
		var parts = [verbs.length ? "verbs: " + verbs.map(String).join(", ") : "no mechanistic verb"];
		if (gate.rewritten) { parts.push("rewritten once"); }
		if (gate.fallback) { parts.push("title replaced by code"); }
		return parts.join("; ");
	}
	case "direction":
		return paWalkCount(gate.checked, "regulator claim") + " checked, " + paWalkNumber(gate.consistent) +
			" consistent, " + paWalkNumber(gate.insensitive) + " insensitive, " + paWalkNumber(gate.dropped) + " dropped";
	case "context":
		return paWalkCount(gate.cited, "citation") + ", " + paWalkNumber(gate.in_context) + " in context, " +
			paWalkNumber(gate.qualified) + " qualified as from another system, " +
			paWalkNumber(gate.unqualified_other) + " unqualified, " + paWalkNumber(gate.unconfirmed) + " unconfirmed";
	case "anchor":
		if (!gate.in_graph) {
			return String(gate.gene || "The perturbed gene") + " is not connected in this organism's network";
		}
		return paWalkPercent(gate.reachability) + " of walked nodes within " + PA_WALK_REACH_RADIUS + " steps of " +
			String(gate.gene || "the perturbed gene") + " vs " + paWalkPercent(gate.base_rate) + " of all measured nodes";
	default:
		return gate.why || "";
	}
}

/* The labels of the gates that failed, in chip order. */
function paWalkFailedGates(view) {
	var gates = paWalkGates(view) || {};
	return PA_WALK_GATES.filter(function (entry) {
		var gate = gates[entry[0]];
		return gate && !gate.not_applicable && !gate.pass;
	}).map(function (entry) { return entry[1]; });
}

/*
 * The five checks as a row of chips, then one line per failed check saying
 * why. Null for a walk that predates the gates.
 */
function paWalkGatesNode(view) {
	var gates = paWalkGates(view);
	if (!gates) { return null; }
	var box = paWalkEl("div", "pa-walk-gates");
	var row = paWalkEl("div", "pa-walk-gate-row");
	var failed = [];
	PA_WALK_GATES.forEach(function (entry) {
		var name = entry[0], label = entry[1], gate = gates[name];
		var state = (!gate || gate.not_applicable) ? "na" : (gate.pass ? "pass" : "fail");
		var chip = paWalkEl("span", "pa-walk-gate is-" + state, label);
		chip.title = paWalkGateTitle(name, gate, state);
		row.appendChild(chip);
		if (state === "fail") { failed.push(label + ": " + String(gate.why || "failed")); }
	});
	box.appendChild(row);
	failed.forEach(function (line) { box.appendChild(paWalkEl("p", "pa-walk-gate-why", line)); });
	return box;
}

/* What stands where the Results would, when the gates held the text back. */
function paWalkBlockedNode(view) {
	var failed = paWalkFailedGates(view);
	return paWalkEl("p", "pa-walk-note pa-walk-blocked", (failed.length
		? "Not shown: this interpretation failed " + failed.length + " of the five checks (" + failed.join(", ") + ")."
		: "Not shown: this interpretation did not clear the five checks.") +
		" The walk itself is below; its legs are database relations and your values, not findings.");
}

/*
 * The walk's modules (one per seed) when they carry their distance from the
 * anchor, else null: a walk sealed before the anchor existed has segments
 * without a distance and lists its legs as it always did.
 */
function paWalkModuleSegments(view) {
	var segments = (view && view.segments) || [];
	var labelled = segments.filter(function (s) { return s && s.seed && Object.prototype.hasOwnProperty.call(s, "distance"); });
	return labelled.length ? labelled : null;
}

/* seed id → distance from the anchor, from the labelled modules. */
function paWalkSegmentDistances(segments) {
	var out = {};
	(segments || []).forEach(function (s) {
		if (s && s.seed && Object.prototype.hasOwnProperty.call(s, "distance")) { out[s.seed] = s.distance; }
	});
	return out;
}

/* The label on a module's first leg: how far its seed sits from the anchor. */
function paWalkModuleText(segment, anchorGene) {
	if (!anchorGene) { return "module"; }
	var d = segment.distance;
	return (d === null || d === undefined || !isFinite(Number(d)))
		? "module · unreachable from " + anchorGene
		: "module · d=" + Number(d) + " from " + anchorGene;
}

/* The checked Results section: title, summary, paragraphs in walk order. */
function paWalkResultsNode(view, options) {
	var results = view && view.results;
	var box = paWalkEl("section", "pa-walk-results");
	if (!results || !results.paragraphs || !results.paragraphs.length) {
		var why = (view && view.checks && view.checks.results && view.checks.results.length)
			? "The Results section did not pass its checks, so the statements below stand alone."
			: "No Results section was written: no statement survived its checks.";
		box.appendChild(paWalkEl("p", "pa-walk-note", why));
		return box;
	}
	box.appendChild(paWalkEl("h4", "pa-walk-results-title",
		(results.title && results.title !== "Results") ? results.title : "Results"));
	if (results.summary) {
		var summary = paWalkEl("p", "pa-walk-summary");
		summary.appendChild(paWalkChipText(results.summary, view.papers, options));
		box.appendChild(summary);
	}
	results.paragraphs.forEach(function (paragraph) {
		var p = paWalkEl("p", "pa-walk-paragraph");
		p.appendChild(paWalkChipText(paragraph.text, view.papers, options));
		/* The legs a paragraph rests on, as chips, when its text does not
		   already tag them: the Narrator often retells a leg without writing
		   [eN], and the reader still needs the way back to the map. */
		var tagged = /\[e\d+/.test(String(paragraph.text || ""));
		var legs = (paragraph.legs || []).filter(function (n) { return isFinite(n); });
		if (!tagged && legs.length) {
			p.appendChild(document.createTextNode(" "));
			p.appendChild(paWalkChipText(legs.map(function (n) { return "[e" + n + "]"; }).join(""),
				view.papers, options));
		}
		box.appendChild(p);
	});
	box.appendChild(paWalkEl("p", "pa-walk-provenance",
		"Written by a language model from the statements that passed the checks. Every number was " +
		"matched against your values and every leg against the walk. Each citation was kept only when an " +
		"agent reading the paper found the passage that states it; the passages are under the references."));
	return box;
}

/*
 * A paper's context as its MeSH headings give it - organism, system, scope -
 * as chips. A chip whose organism or system the design card does not share
 * is marked, so a reader sees which citations come from another system.
 * Null when the paper carries no context (a walk sealed before it was read).
 */
function paWalkContextNode(context) {
	if (!context || typeof context !== "object") { return null; }
	var match = context.match || {};
	var holder = paWalkEl("span", "pa-walk-ctx");
	["organism", "system", "scope"].forEach(function (key) {
		var value = String(context[key] || "").trim();
		if (!value || value.toLowerCase() === "unknown") { return; }
		var other = match[key] === "other";
		var chip = paWalkEl("span", "pa-walk-chip pa-walk-chip-ctx" + (other ? " is-other" : ""), value);
		if (other) { chip.title = "from another organism or system than this experiment"; }
		holder.appendChild(document.createTextNode(" "));
		holder.appendChild(chip);
	});
	return holder.childNodes.length ? holder : null;
}

/* The cited papers, numbered as the text cites them (the server renumbers them
   1..n in order of first citation). Empty when nothing is cited. */
function paWalkReferencesNode(view) {
	var papers = (view && view.papers) || {};
	var refs = Object.keys(papers).map(Number).filter(isFinite).sort(function (a, b) { return a - b; });
	if (!refs.length) { return null; }
	var box = paWalkEl("details", "pa-walk-references");
	var quoted = refs.some(function (ref) { return ((papers[ref] || {}).evidence || []).length; });
	box.appendChild(paWalkEl("summary", null, refs.length + " cited paper" + (refs.length === 1 ? "" : "s") +
		(quoted ? ", each with the passage it is cited for" : "")));
	var list = paWalkEl("ol", "pa-walk-reference-list");
	refs.forEach(function (ref) {
		var paper = papers[ref] || {};
		var item = paWalkEl("li");
		item.setAttribute("value", ref);
		if (paper.pmid && /^\d+$/.test(String(paper.pmid))) {
			var link = paWalkEl("a", "pa-walk-cite", String(paper.title || "PMID " + paper.pmid));
			link.href = "https://pubmed.ncbi.nlm.nih.gov/" + paper.pmid + "/";
			link.target = "_blank";
			link.rel = "noopener noreferrer";
			item.appendChild(link);
		} else {
			item.appendChild(document.createTextNode(String(paper.title || "")));
		}
		item.appendChild(paWalkEl("span", "pa-walk-meta", " " + [paper.journal, paper.year].filter(Boolean).join(", ") +
			(paper.pmid ? " · PMID " + paper.pmid : "")));
		var context = paWalkContextNode(paper.context);
		if (context) { item.appendChild(context); }
		paWalkEvidenceNodes(paper).forEach(function (node) { item.appendChild(node); });
		list.appendChild(item);
	});
	box.appendChild(list);
	return box;
}

/* Kept and dropped statements, each with what the pathway draws and what goes beyond it. */
function paWalkStatementsNode(view, options) {
	var box = paWalkEl("details", "pa-walk-statements");
	var kept = view.statements || [], dropped = view.dropped || [];
	box.appendChild(paWalkEl("summary", null,
		kept.length + " statement" + (kept.length === 1 ? "" : "s") + " kept" +
		(dropped.length ? ", " + dropped.length + " dropped" : "")));
	kept.forEach(function (statement) {
		var item = paWalkEl("div", "pa-walk-statement");
		var claim = paWalkEl("p", "pa-walk-claim");
		/* The statement's tier leads its claim: "mechanism" when every leg it
		   rests on is a directed relation, "association" otherwise. */
		if (statement.tier) {
			var tier = String(statement.tier);
			claim.appendChild(paWalkEl("span", "pa-walk-chip pa-walk-chip-tier is-" +
				(tier === "mechanism" ? "mechanism" : "association"), tier));
			claim.appendChild(document.createTextNode(" "));
		}
		claim.appendChild(paWalkChipText(statement.claim, view.papers, options));
		item.appendChild(claim);
		var grounded = (statement.grounded_in || []).map(function (g) { return "e" + g.leg + " " + (g.db || ""); });
		if (grounded.length) {
			item.appendChild(paWalkEl("p", "pa-walk-meta", "Drawn in the pathway: " + grounded.join(", ")));
		}
		/* The direction check's verdicts: for each panel regulator the
		   statement cites, what the values imply and what the statement claims. */
		(Array.isArray(statement.direction) ? statement.direction : []).forEach(function (verdict) {
			if (!verdict || !verdict.gene) { return; }
			var role = PA_WALK_REGULATOR_WORDS[verdict["class"]] || String(verdict["class"] || "regulator");
			var claimed = (verdict.claimed === null || verdict.claimed === undefined) ? "no claim read"
				: (verdict.claimed === "none" ? "the statement claims no direction"
				: "the statement claims " + String(verdict.claimed));
			var note = verdict.insensitive ? " · reads the same with the values reversed"
				: (verdict.consistent === false ? " · inconsistent" : "");
			item.appendChild(paWalkEl("p", "pa-walk-meta", "Direction check: " + String(verdict.gene) + " (" +
				(verdict.pathway ? String(verdict.pathway) + ", " : "") + role + "): the values imply " +
				String(verdict.implied || "no direction") + ", " + claimed + note));
		});
		(statement.beyond || []).forEach(function (beyond) {
			var line = paWalkEl("p", "pa-walk-meta");
			line.appendChild(document.createTextNode(beyond.hypothesis ? "Hypothesis: " : "Beyond the pathway: "));
			line.appendChild(paWalkChipText(String(beyond.claim || "") +
				(beyond.paper ? " [" + beyond.paper + "]" : ""), view.papers, options));
			item.appendChild(line);
		});
		box.appendChild(item);
	});
	dropped.forEach(function (statement) {
		var item = paWalkEl("div", "pa-walk-statement is-dropped");
		item.appendChild(paWalkEl("p", "pa-walk-claim", statement.claim || ""));
		item.appendChild(paWalkEl("p", "pa-walk-meta", "Dropped by the " + (statement.by || "checks") + ": " +
			(statement.why || "")));
		box.appendChild(item);
	});
	return box;
}

/*
 * The legs as a list. options.onLeg(n) focuses a leg; options.pathwayLink(edge)
 * returns an element linking the leg's pathway, or null. options.segments
 * (the walk's modules, with their distance from the anchor) labels the first
 * leg of each module; options.anchorGene names the anchor in that label.
 */
function paWalkLegsNode(chain, options) {
	options = options || {};
	var list = paWalkEl("ol", "pa-walk-legs");
	var moduleStarts = {};
	(options.segments || []).forEach(function (segment) {
		if (segment && isFinite(Number(segment.first))) { moduleStarts[Number(segment.first)] = segment; }
	});
	(chain || []).forEach(function (leg) {
		var row = paWalkEl("li", "pa-walk-leg" + (leg.kind === "jump" ? " is-jump" : "") +
			(options.focused !== null && options.focused !== undefined && options.focused === leg.n ? " is-focused" : ""));
		row.setAttribute("data-leg", leg.n);
		if (moduleStarts[leg.n]) {
			row.appendChild(paWalkEl("span", "pa-walk-module", paWalkModuleText(moduleStarts[leg.n], options.anchorGene)));
		}
		var head = paWalkEl("div", "pa-walk-leg-head");
		head.appendChild(paWalkEl("span", "pa-walk-leg-n", (leg.kind === "jump" ? "J" : "e") + leg.n));
		head.appendChild(paWalkEl("span", "pa-walk-leg-path",
			String(leg.from_label || leg.from) + (leg.kind === "jump" ? " ⇢ " : " → ") +
			String(leg.to_label || leg.to)));
		row.appendChild(head);
		var where = paWalkEl("div", "pa-walk-leg-edge");
		var link = options.pathwayLink ? options.pathwayLink(leg.edge) : null;
		if (link) {
			where.appendChild(link);
			var sub = leg.edge && leg.edge.subtype ? " · " + String(leg.edge.subtype).split(",")[0] : "";
			where.appendChild(document.createTextNode(sub + " " + paWalkSign(leg.edge ? leg.edge.sign : 0) +
				(leg.edge && leg.edge.dir === "against" ? " · against the arrow" : "")));
		} else {
			where.textContent = paWalkEdgeText(leg.edge) +
				(leg.edge && leg.edge.dir === "against" ? " · against the arrow" : "");
		}
		row.appendChild(where);
		if (leg.reading) {
			row.appendChild(paWalkEl("div", "pa-walk-leg-reading", leg.reading));
		}
		if (options.onLeg) {
			row.setAttribute("tabindex", "0");
			row.title = "Show this leg on the map";
		}
		list.appendChild(row);
	});
	if (options.onLeg) {
		$(list).on("click keydown", ".pa-walk-leg", function (event) {
			if ($(event.target).closest("a").length) { return; }
			if (event.type === "keydown" && event.keyCode !== 13 && event.keyCode !== 32) { return; }
			event.preventDefault();
			options.onLeg(parseInt(this.getAttribute("data-leg"), 10));
		});
	}
	return list;
}

/* Open a pathway's diagram in Step 4, the way a pathway link in the report
   does, without asking for its AI interpretation. False when the job has no
   such pathway (a leg can run through a pathway the job did not match). */
function paWalkOpenPathway(pathwayID) {
	try {
		var mainView = application.getMainView();
		var jobView = mainView.getSubView("PA_Step3JobView") || mainView.getLastJobView();
		if (!jobView || typeof jobView.paintSelectedPathway !== "function") { return false; }
		if (!jobView.getModel().getPathway(pathwayID)) { return false; }
		jobView.paintSelectedPathway(pathwayID);
		return true;
	} catch (error) {
		console.warn("Could not open pathway " + pathwayID, error);
		return false;
	}
}

/* A link to a leg's pathway when the job matched that pathway, else null. */
function paWalkPathwayLink(edge) {
	if (!edge || !edge.pathway || edge.db === "job") { return null; }
	var jobModel = null;
	try {
		var mainView = application.getMainView();
		var jobView = mainView.getSubView("PA_Step3JobView") || mainView.getLastJobView();
		jobModel = jobView ? jobView.getModel() : null;
	} catch (error) { jobModel = null; }
	if (!jobModel || typeof jobModel.getPathway !== "function" || !jobModel.getPathway(edge.pathway)) {
		return null;
	}
	var link = paWalkEl("a", "pa-walk-pathway-link", String(edge.db || "") + " " + String(edge.name || edge.pathway));
	link.href = "#";
	link.setAttribute("data-pathway-id", edge.pathway);
	link.title = "Open this pathway's diagram";
	return link;
}

//------------------------------------------------------------------------------------------------

function PA_Step4WalkView() {
	this.name = "PA_Step4WalkView";
	this.status = null;          // the last /ai_walk_status answer
	this.pollTimer = null;
	this.outage = null;
	this.failures = 0;
	this.focusLeg = null;
	this.layer = null;
	this.starting = false;

	this.scope = function () {
		return "pathway:" + this.getModel().getID();
	};

	this.jobModel = function () {
		return this.getParent().getParent().getModel();
	};

	this.jobID = function () {
		return this.jobModel().getJobID();
	};

	/* Whether this viewer may replace the walk or edit its design card: the
	   owner, or anyone on a job that is not read-only (the server's rule). */
	this.canEdit = function () {
		var job = this.jobModel();
		var owner = job.getUserID ? job.getUserID() : null;
		var isOwner = owner !== null && owner !== undefined &&
			String(Ext.util.Cookies.get("userID")) === String(owner);
		return isOwner || !(job.getReadOnly && job.getReadOnly());
	};

	/* Why this pathway cannot be walked, or null. */
	this.refusal = function () {
		if (this.getModel().getSource() === "MapMan") {
			return "MapMan bins group genes by function and draw no interactions, so there is nothing to walk.";
		}
		if (!this.jobModel().aiConsent) {
			return "AI interpretation was not enabled for this job, so nothing about it can be sent to the " +
				"AI service. Run the analysis again with 'Enable AI pathway interpretation' ticked to walk it.";
		}
		return null;
	};

	this.toggle = function (visible) {
		visible = (visible === undefined) ? !this.getComponent().isVisible() : visible;
		this.getComponent().setVisible(visible);
		if (visible && this.status === null) { this.refresh(); }
		this.setLayerVisible(visible);
		this.getParent().adjustChildrenWidth();
		return this;
	};

	this.body = function () {
		var cmp = this.component;
		if (!cmp || cmp.isDestroyed || !cmp.el) { return null; }
		return $(cmp.el.dom).find(".pa-walk-body");
	};

	this.alive = function () {
		return this.component && !this.component.isDestroyed;
	};

	/* ------------------------------------------------------------ server */
	this.refresh = function () {
		var me = this, refusal = this.refusal();
		if (refusal) {
			this.render({status: "refused", detail: refusal});
			return;
		}
		clearTimeout(this.pollTimer);
		$.ajax({
			type: "POST", url: SERVER_URL_AI_WALK_STATUS, timeout: JOB_STATUS_REQUEST_TIMEOUT,
			data: {jobID: this.jobID(), scope: this.scope()},
			success: function (response) { me.onStatus(response); },
			error: function (jqXHR, textStatus) { me.onStatusError(jqXHR, textStatus); }
		});
	};

	this.schedule = function (delay) {
		var me = this;
		clearTimeout(this.pollTimer);
		if (!this.alive()) { return; }
		this.pollTimer = setTimeout(function () { me.refresh(); }, delay);
	};

	this.onStatus = function (response) {
		if (!this.alive()) { return; }
		this.outage = null;
		if (!response || response.success !== true) {
			this.render({status: "unanswered", detail: (response && response.message) || "The walk status could not be read."});
			return;
		}
		this.failures = 0;
		this.status = response;
		this.render(response);
		if (response.status === "queued" || response.status === "running") {
			this.schedule(AI_POLL_INTERVAL);
		}
	};

	this.onStatusError = function (jqXHR, textStatus) {
		if (!this.alive()) { return; }
		var answer = readableAnswer(jqXHR);
		if (!answer) {
			this.outage = this.outage || {};
			var wait = statusPollRetryDelay(this.outage, Date.now());
			if (wait >= 0) { this.schedule(wait); return; }
			// Given up: the next failure starts a new outage with a full budget.
			this.outage = null;
			this.render({status: "unanswered", detail: "The server did not answer the walk's progress check (" +
				describeUnansweredRequest(jqXHR, textStatus) + "). The walk may still be running."});
			return;
		}
		this.render({status: "unanswered", detail: String(answer.message || "The walk status could not be read.")
			.replace(/^\w+: AT [^:]+: \w+\. ERROR MESSAGE: /, "")});
	};

	/* restart is true only for "Walk again" on a sealed walk: a failed walk is
	   filed again without it, and a finished walk must never be replaced by a
	   button that only meant "ask the server again". */
	this.start = function (restart) {
		var me = this;
		if (this.starting) { return; }
		this.starting = true;
		this.outage = null;
		if (restart) {
			// The old walk's arcs and focused leg go with it.
			this.clearLayer();
			this.focusLeg = null;
			this.lastChain = null;
			this.lastPlan = null;
		}
		var card = {};
		var body = this.body();
		if (body && this.canEdit()) {
			body.find("[data-card-field]").each(function () {
				var original = this.getAttribute("data-original") || "";
				if (this.value.trim() && this.value.trim() !== original.trim()) {
					card[this.getAttribute("data-card-field")] = this.value.trim();
				}
			});
		}
		this.render({status: "queued", stage: "queued", percent: 0, detail: "Filing the walk"});
		$.ajax({
			type: "POST", url: SERVER_URL_AI_WALK_START, timeout: JOB_STATUS_REQUEST_TIMEOUT,
			data: {jobID: this.jobID(), scope: this.scope(), card: Object.keys(card).length ? JSON.stringify(card) : "",
				   restart: restart ? "true" : ""},
			success: function (response) {
				me.starting = false;
				if (!response || response.success !== true) {
					me.render({status: "unanswered", detail: (response && response.message) || "The walk could not be started."});
					return;
				}
				me.refresh();
			},
			error: function (jqXHR, textStatus) {
				me.starting = false;
				me.onStatusError(jqXHR, textStatus);
			}
		});
	};

	/* ------------------------------------------------------------ render */
	this.render = function (state) {
		var body = this.body();
		if (!body) { return; }
		var me = this, root = body[0];
		body.empty();
		var status = state.status;

		if (status === "refused") {
			root.appendChild(paWalkEl("p", "pa-walk-note", state.detail));
			this.clearLayer();
			return;
		}
		if (status === "unanswered") {
			// Something between here and the walk failed -- a progress check
			// that got no answer, a refused request -- not the walk itself.
			// Asking again is the only safe button: the walk may be running,
			// or finished.
			root.appendChild(paWalkEl("p", "pa-walk-error", state.detail || "The walk status could not be read."));
			var check = paWalkEl("a", "button btn-default pa-walk-check", "Check again");
			check.href = "javascript:void(0)";
			$(check).on("click", function () { me.outage = null; me.refresh(); });
			var checkRow = paWalkEl("div", "pa-walk-actions");
			checkRow.appendChild(check);
			root.appendChild(checkRow);
			return;
		}
		if (status === "not_started" || status === "error") {
			if (status === "error") {
				root.appendChild(paWalkEl("p", "pa-walk-error", state.detail || "The walk failed."));
			}
			root.appendChild(paWalkEl("p", "pa-walk-intro",
				"An AI agent walks the interactions this pathway draws, starting from the nodes whose " +
				"neighbourhoods hold surprisingly many relevant features. It reads your values at every " +
				"stop, and a second agent writes what the chain shows as a Results section that is checked " +
				"against your data before you see it."));
			if (state.card && this.canEdit()) { root.appendChild(this.cardForm(state.card)); }
			var start = paWalkEl("a", "button btn-success pa-walk-start", status === "error" ? "Try again" : "Walk this pathway");
			start.href = "javascript:void(0)";
			$(start).on("click", function () { me.start(false); });
			var row = paWalkEl("div", "pa-walk-actions");
			row.appendChild(start);
			root.appendChild(row);
			root.appendChild(paWalkEl("p", "pa-walk-meta",
				"Takes a few minutes. The legs appear here and on the map as the agent walks them."));
			this.clearLayer();
			return;
		}
		if (status === "queued" || status === "running") {
			root.appendChild(this.progressNode(state));
			var live = state.live || {};
			if (live.plan) { root.appendChild(this.planNode(live.plan)); }
			if (live.chain && live.chain.length) {
				root.appendChild(paWalkEl("h4", null, "Legs so far"));
				root.appendChild(paWalkLegsNode(live.chain, {onLeg: function (n) { me.focus(n); }, focused: this.focusLeg}));
				this.drawLayer(live.chain, live.plan);
			} else {
				this.clearLayer();
			}
			return;
		}
		if (status === "done" && state.walk) {
			this.renderDone(state.walk);
		}
	};

	this.progressNode = function (state) {
		var box = paWalkEl("div", "pa-walk-progress");
		var words = {queued: "Waiting for a free worker", network: "Reading the network", card: "Reading the design",
					 walk: "Walking", writer: "Writing statements", sense: "Checking the statements",
					 narrate: "Writing the Results section"};
		box.appendChild(paWalkEl("div", "pa-walk-stage", words[state.stage] || "Working"));
		var track = paWalkEl("div", "pa-walk-track");
		var fill = paWalkEl("div", "pa-walk-fill");
		fill.style.width = Math.max(3, Math.min(100, Number(state.percent) || 0)) + "%";
		track.appendChild(fill);
		box.appendChild(track);
		if (state.detail) { box.appendChild(paWalkEl("div", "pa-walk-meta", state.detail)); }
		return box;
	};

	/* The plan: the seeds and the step budget. With the walk's labelled modules
	   (segments carrying a distance) each seed shows how far it sits from the
	   anchor, "Foxo1 (d=1)"; a seed no path reaches shows nothing, the module
	   label on its first leg says so. */
	this.planNode = function (plan, segments) {
		var box = paWalkEl("div", "pa-walk-plan");
		var distances = paWalkSegmentDistances(segments);
		box.appendChild(paWalkEl("span", "pa-walk-label", "Plan"));
		box.appendChild(document.createTextNode(" " + (plan.seeds || []).map(function (s) {
			var d = distances[s.id];
			return String(s.label) + ((d === null || d === undefined || !isFinite(Number(d))) ? "" : " (d=" + Number(d) + ")");
		}).join(", ") + " · " + plan.steps + " steps"));
		if (plan.reason) { box.appendChild(paWalkEl("div", "pa-walk-meta", plan.reason)); }
		return box;
	};

	this.cardForm = function (card) {
		var box = paWalkEl("details", "pa-walk-card");
		box.appendChild(paWalkEl("summary", null, "Design card: what the columns mean"));
		box.appendChild(paWalkEl("p", "pa-walk-meta",
			"The walk reads every value against these lines. Correct any that are wrong before you start."));
		var fields = [["perturbation", "Perturbation"], ["value", "A value is"], ["axis", "Columns"],
					  ["baseline", "Baseline"], ["relevant", "Relevant means"]];
		fields.forEach(function (field) {
			var label = paWalkEl("label", "pa-walk-field");
			label.appendChild(paWalkEl("span", "pa-walk-label", field[1]));
			var input = paWalkEl("textarea");
			input.rows = 2;
			input.value = String(card[field[0]] || "");
			input.setAttribute("data-card-field", field[0]);
			input.setAttribute("data-original", String(card[field[0]] || ""));
			label.appendChild(input);
			box.appendChild(label);
		});
		var kind = paWalkEl("p", "pa-walk-meta");
		kind.appendChild(paWalkEl("span", "pa-walk-label", "Read as"));
		kind.appendChild(document.createTextNode(" " + (PA_WALK_AXIS_WORDS[card.axis_kind] || "conditions") +
			(card.unlabeled && card.unlabeled.length ? " · no timing claims on " + card.unlabeled.join(", ") +
			" (no column labels)" : "")));
		box.appendChild(kind);
		return box;
	};

	this.renderDone = function (view) {
		var me = this, body = this.body();
		if (!body) { return; }
		var root = body[0];
		var onLeg = function (n) { me.focus(n); };
		/* The perturbation line and the five gates come first; the text they
		   judged follows only when every gate passed (or the walk predates
		   them). A walk they held back keeps its legs, opened, with a note. */
		var header = paWalkHeaderNode(view);
		if (header) { root.appendChild(header); }
		var gates = paWalkGatesNode(view);
		if (gates) { root.appendChild(gates); }
		var shown = paWalkTextShown(view);
		if (shown) {
			root.appendChild(paWalkResultsNode(view, {onLeg: onLeg}));
			var references = paWalkReferencesNode(view);
			if (references) { root.appendChild(references); }
			if ((view.statements || []).length || (view.dropped || []).length) {
				root.appendChild(paWalkStatementsNode(view, {onLeg: onLeg}));
			}
		} else {
			root.appendChild(paWalkBlockedNode(view));
		}
		var legs = paWalkEl("details", "pa-walk-chain");
		legs.open = !shown || !(view.results && view.results.paragraphs && view.results.paragraphs.length);
		var counts = view.counts || {};
		legs.appendChild(paWalkEl("summary", null, "The walk: " + (counts.steps || 0) + " steps, " +
			(counts.jumps || 0) + " jumps"));
		var segments = paWalkModuleSegments(view);
		if (view.plan) { legs.appendChild(this.planNode(view.plan, segments)); }
		legs.appendChild(paWalkLegsNode(view.chain, {onLeg: onLeg, focused: this.focusLeg,
			segments: segments, anchorGene: paWalkAnchorGene(view)}));
		if (view.stop_reason) {
			legs.appendChild(paWalkEl("p", "pa-walk-meta", "Stopped: " + view.stop_reason));
		}
		root.appendChild(legs);
		var drawn = this.drawLayer(view.chain, view.plan);
		var off = (view.chain || []).filter(function (leg) { return leg.kind === "step"; }).length - drawn;
		var foot = paWalkEl("p", "pa-walk-meta");
		foot.textContent = (this.hasDiagram()
			? drawn + " legs drawn on the map" + (off > 0 ? "; " + off + " run through nodes the map has no box for, such as miRNAs" : "")
			: "This pathway has no diagram, so the legs are listed here only") +
			". Model: " + (view.model_used || "unknown") + ".";
		root.appendChild(foot);
		if (this.canEdit()) {
			var again = paWalkEl("a", "button btn-default pa-walk-again", "Walk again");
			again.href = "javascript:void(0)";
			$(again).on("click", function () { me.start(true); });
			var row = paWalkEl("div", "pa-walk-actions");
			row.appendChild(again);
			root.appendChild(row);
		}
	};

	/* ------------------------------------------------------------ the map */
	this.hasDiagram = function () {
		return !!this.diagramSVG();
	};

	this.diagramSVG = function () {
		var pathwayView = this.getParent();
		var diagram = pathwayView && pathwayView.diagramPanel;
		if (!diagram || !diagram.component || diagram.component.isDestroyed || !diagram.component.el) { return null; }
		var svg = $(diagram.component.el.dom).find("svg.keggPathwaySVG")[0];
		return (svg && $(svg).find("image.keggImageBack").length) ? svg : null;
	};

	/* Box centre and half-extents in canvas units for a walker node, nearest to
	   `near` when the gene is drawn more than once; null when the map has no box. */
	this.boxFor = function (nodeID, label, near) {
		var svg = this.diagramSVG();
		var graphical = this.getModel().getGraphicalOptions();
		if (!svg || !graphical || /^mir:/.test(String(nodeID))) { return null; }
		var raster = parseFloat($(svg).find("image.keggImageBack").attr("width"));
		var factor = raster / graphical.getImageWidth();
		if (!isFinite(factor) || factor <= 0) { return null; }
		var candidates = [String(nodeID).replace(/^[gc]:/, "")];
		String(label || "").split(",").forEach(function (part) {
			part = part.trim();
			if (part && candidates.indexOf(part) === -1) { candidates.push(part); }
		});
		for (var i = 0; i < candidates.length; i++) {
			var found = graphical.findFeatureGraphicalData(String(candidates[i]));
			if (!(found instanceof Array)) { found = found ? [found] : []; }
			found = found.filter(function (gd) { return isFinite(gd.getX()) && isFinite(gd.getY()); });
			if (!found.length) { continue; }
			var best = found[0];
			if (near) {
				var bestDistance = Infinity;
				found.forEach(function (gd) {
					var d = Math.pow(gd.getX() * factor - near.cx, 2) + Math.pow(gd.getY() * factor - near.cy, 2);
					if (d < bestDistance) { bestDistance = d; best = gd; }
				});
			}
			return {cx: best.getX() * factor, cy: best.getY() * factor,
					hw: ((best.getBoxWidth() * factor) || 20) / 2, hh: ((best.getBoxHeight() * factor) || 20) / 2,
					factor: factor};
		}
		return null;
	};

	this.svgEl = function (tag, attributes) {
		var element = document.createElementNS(PA_WALK_SVG_NS, tag);
		for (var name in attributes) {
			if (attributes[name] !== null && attributes[name] !== undefined) {
				element.setAttribute(name, attributes[name]);
			}
		}
		return element;
	};

	this.perimeter = function (box, tx, ty, gap) {
		var dx = tx - box.cx, dy = ty - box.cy;
		if (dx === 0 && dy === 0) { return {x: box.cx, y: box.cy}; }
		var scale = Math.min(dx === 0 ? Infinity : box.hw / Math.abs(dx), dy === 0 ? Infinity : box.hh / Math.abs(dy));
		var extra = (gap || 0) / Math.sqrt(dx * dx + dy * dy);
		return {x: box.cx + dx * (scale + extra), y: box.cy + dy * (scale + extra)};
	};

	this.clearLayer = function () {
		if (this.layer && this.layer.parentNode) { this.layer.parentNode.removeChild(this.layer); }
		this.layer = null;
	};

	this.setLayerVisible = function (visible) {
		if (this.layer) { this.layer.style.display = visible ? "" : "none"; }
	};

	/* Draw the step legs as numbered arcs and ring the walked boxes. Returns
	   how many legs were drawn. Inserted below the evidence layer, which
	   re-appends itself last on every refresh. */
	this.drawLayer = function (chain, plan) {
		this.clearLayer();
		var svg = this.diagramSVG();
		if (!svg || !chain || !chain.length) { return 0; }
		var me = this, drawn = 0;
		var group = this.svgEl("g", {"class": "pa-walk-layer", "pointer-events": "none"});
		var boxes = {};
		var place = function (nodeID, label, near) {
			if (!(nodeID in boxes)) { boxes[nodeID] = me.boxFor(nodeID, label, near); }
			return boxes[nodeID];
		};
		var seeds = {};
		((plan && plan.seeds) || []).forEach(function (seed) { seeds[seed.id] = true; });
		var factor = null;
		chain.forEach(function (leg) {
			var from = place(leg.from, leg.from_label, null);
			var to = place(leg.to, leg.to_label, from);
			if (from) { factor = from.factor; }
			if (leg.kind !== "step" || !from || !to) { return; }
			var ink = function (px) { return px * from.factor; };
			var focused = (me.focusLeg === leg.n);
			var start = me.perimeter(from, to.cx, to.cy, 2), end = me.perimeter(to, from.cx, from.cy, 5);
			var dx = end.x - start.x, dy = end.y - start.y, length = Math.sqrt(dx * dx + dy * dy) || 1;
			var bow = Math.max(ink(10), Math.min(length * 0.18, ink(60)));
			var control = {x: (start.x + end.x) / 2 + (dy / length) * bow, y: (start.y + end.y) / 2 - (dx / length) * bow};
			var d = "M" + start.x + "," + start.y + " Q" + control.x + "," + control.y + " " + end.x + "," + end.y;
			group.appendChild(me.svgEl("path", {d: d, fill: "none", stroke: PA_WALK_STYLE.casing,
				"stroke-width": ink(focused ? 9 : 6.5), "stroke-linecap": "round", opacity: 0.8}));
			var path = me.svgEl("path", {d: d, fill: "none", stroke: focused ? PA_WALK_STYLE.legFocus : PA_WALK_STYLE.leg,
				"stroke-width": ink(focused ? 5 : 3.2), "stroke-linecap": "round", opacity: 0.95});
			var title = me.svgEl("title", {});
			title.textContent = "e" + leg.n + " · " + (leg.from_label || leg.from) + " → " + (leg.to_label || leg.to) +
				" · " + paWalkEdgeText(leg.edge);
			path.appendChild(title);
			group.appendChild(path);
			// arrowhead along the last tangent (control -> end)
			var tx = end.x - control.x, ty = end.y - control.y, tl = Math.sqrt(tx * tx + ty * ty) || 1;
			var ux = tx / tl, uy = ty / tl, size = ink(focused ? 13 : 10);
			var points = [end.x, end.y, end.x - ux * size - uy * size * 0.55, end.y - uy * size + ux * size * 0.55,
						  end.x - ux * size + uy * size * 0.55, end.y - uy * size - ux * size * 0.55];
			group.appendChild(me.svgEl("polygon", {points: points.join(","),
				fill: focused ? PA_WALK_STYLE.legFocus : PA_WALK_STYLE.leg}));
			// the leg's number at the top of the bow
			var mx = 0.25 * start.x + 0.5 * control.x + 0.25 * end.x, my = 0.25 * start.y + 0.5 * control.y + 0.25 * end.y;
			group.appendChild(me.svgEl("circle", {cx: mx, cy: my, r: ink(focused ? 12 : 10),
				fill: focused ? PA_WALK_STYLE.legFocus : PA_WALK_STYLE.leg, stroke: PA_WALK_STYLE.casing, "stroke-width": ink(2)}));
			var number = me.svgEl("text", {x: mx, y: my + ink(4.2), "text-anchor": "middle", fill: "#FFFFFF",
				"font-size": ink(12), "font-weight": "700", "font-family": "Arial, Helvetica, sans-serif"});
			number.textContent = String(leg.n);
			group.appendChild(number);
			drawn += 1;
		});
		Object.keys(boxes).forEach(function (nodeID) {
			var box = boxes[nodeID];
			if (!box) { return; }
			var seed = !!seeds[nodeID];
			group.insertBefore(me.svgEl("rect", {x: box.cx - box.hw - box.factor * 3, y: box.cy - box.hh - box.factor * 3,
				width: 2 * box.hw + box.factor * 6, height: 2 * box.hh + box.factor * 6, rx: box.factor * 4,
				fill: "none", stroke: seed ? PA_WALK_STYLE.seed : PA_WALK_STYLE.leg,
				"stroke-width": box.factor * (seed ? 4 : 2.5), opacity: 0.9}), group.firstChild);
		});
		if (!drawn && factor === null) { return 0; }
		var evidence = svg.querySelector("g.evidenceOverlay");
		if (evidence) { svg.insertBefore(group, evidence); } else { svg.appendChild(group); }
		this.layer = group;
		this.lastChain = chain;
		this.lastPlan = plan;
		this.setLayerVisible(this.getComponent().isVisible());
		return drawn;
	};

	/* Highlight one leg in the list and on the map, and centre the map on it. */
	this.focus = function (n) {
		this.focusLeg = (this.focusLeg === n) ? null : n;
		var body = this.body();
		if (body) {
			body.find(".pa-walk-leg").removeClass("is-focused");
			if (this.focusLeg !== null) {
				var row = body.find('.pa-walk-leg[data-leg="' + this.focusLeg + '"]').addClass("is-focused");
				row.closest("details").attr("open", "open");
				/* Scroll the column body only: scrollIntoView also scrolls the
				   panel around it, which pushed the column's header out of view. */
				if (row.length) {
					var top = row.position().top + body.scrollTop() - body.height() / 3;
					body.scrollTop(Math.max(0, top));
				}
			}
		}
		if (this.lastChain) { this.drawLayer(this.lastChain, this.lastPlan); }
		var leg = (this.lastChain || []).filter(function (l) { return l.n === n; })[0];
		var diagram = this.getParent().diagramPanel;
		if (leg && this.focusLeg !== null && diagram && diagram.zoomTool && diagram.zoomTool.setCenter) {
			var box = this.boxFor(leg.to, leg.to_label, this.boxFor(leg.from, leg.from_label, null));
			if (box) {
				try { diagram.zoomTool.setCenter(box.cx, box.cy, 300); } catch (error) { /* pan is a convenience */ }
			}
		}
	};

	/* ------------------------------------------------------------ component */
	this.initComponent = function () {
		var me = this;
		this.component = Ext.widget({
			xtype: "container", cls: "lateralOptionsPanel pa-walk-panel", width: 330,
			height: ($("#mainViewCenterPanel").height() - 100),
			items: [{
				xtype: "box", html:
				"<div class='lateralOptionsPanel-header' data-guides='ignore'>" +
				'  <div class="lateralOptionsPanel-toolbar">' +
				'    <a href="javascript:void(0)" class="toolbarOption btn-info helpTip pa-walk-close" title="Close this panel"><i class="fa fa-times"></i></a>' +
				'  </div>' +
				"  <h2>" + getAIMark(18) + " Graph walk</h2>" +
				"</div>" +
				"<div class='lateralOptionsPanel-body pa-walk-body'></div>"
			}],
			listeners: {
				boxready: function () {
					var el = $(me.getComponent().el.dom);
					el.find(".pa-walk-close").on("click", function () { me.toggle(false); });
					el.on("click", ".pa-walk-pathway-link", function (event) {
						event.preventDefault();
						paWalkOpenPathway(this.getAttribute("data-pathway-id"));
					});
					me.sizeBody();
					if (me.getComponent().isVisible()) { me.refresh(); }
					if (typeof initializeTooltips === "function") { initializeTooltips(".pa-walk-panel .helpTip"); }
				},
				resize: function () { me.sizeBody(); },
				beforedestroy: function () {
					clearTimeout(me.pollTimer);
					me.pollTimer = null;
					me.clearLayer();
				}
			}
		});
		return this.component;
	};

	this.sizeBody = function () {
		if (!this.alive() || !this.component.el) { return; }
		var el = $(this.component.el.dom);
		var headerHeight = el.find(".lateralOptionsPanel-header").outerHeight() + 10;
		el.find(".pa-walk-body").height($("#mainViewCenterPanel").height() - headerHeight - 100);
	};

	return this;
}
PA_Step4WalkView.prototype = new View();

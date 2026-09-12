/* global Ext, APP_VERSION, dragula */

//# sourceURL=PA_Step1Views.js
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
* THIS FILE CONTAINS THE FOLLOWING COMPONENT DECLARATION
* - PA_Step1JobView
* - OmicSubmittingPanel
* - RegionBasedOmicSubmittingPanel
*/
/* The landing hero's explainer diagram: your omic data -> mapped onto
   pathways -> ranked and painted, with the AI strip beneath.

   This replaces the graphical abstract in the hero slot. That asset is a
   1622x996 raster of a scientific figure with embedded screenshots and
   labels, and the hero renders it at 340px -- a 4.8x downscale at which
   none of its text can be read. It is still the right asset for print and
   for the docs, so the link below the diagram opens it full size.

   Ported from the design system's marketing/PaintomicsFlow, with two
   deliberate departures. The ramp is the product's own rather than the
   system's ColorBrewer values: the component's stated constraint is that
   these colours match the painted pathway diagrams, and what the product
   actually paints is getColor(..., 'bwr') in PA_Step3Views.js -- a diverging
   ramp built in OKLab, red at max, a light neutral at zero, blue at min. And the three beats are
   stacked as rows rather than set as columns, so the viewBox is 1:1 with
   the rendered width and every label is drawn at the size it is read at;
   the system's 660-wide landscape version has to shrink to 0.65 in this
   slot, which takes its 11px labels down to about 7px. */
/* The flow diagram, split into the three pieces it was always made of.
   It used to sit in the hero as one 460x356 picture whose three stacked
   panels were the same three stages the "How it works" cards describe -
   so the page drew the pipeline twice, once as a picture beside the
   title and once as text below it. Each panel now lives in the card
   that explains it, which is also what fills those cards: they were
   forced to a common 335px by the grid while their prose ran out 38,
   59 and 97px short, so the tallest one was a third empty.

   Wrapped in a translate group rather than re-numbered. Every element
   below keeps the coordinates it was authored with, and the group moves
   the origin, so nothing here can drift out of alignment with the
   artwork it came from. */
/* The hero visual: the product doing its own job.
   
   A pathway network is drawn, its features are painted with the same bwr ramp
   the application paints real data with - red high, neutral zero, blue low - and
   then the AI panel reads the painted result and writes an interpretation.
   That is literally the pipeline, so this is the one picture on the page that
   is not decoration: everything it shows, the tool actually does.

   Colours are the product's own. The node fills come from the bwr ramp in
   PA_Step3Views, the grey is the stroke KEGG diagrams use, and the AI blue is
   --pa-ai-blue. Nothing here invents a palette.

   Animation lives in main.css so it can be turned off under
   prefers-reduced-motion, where every element simply renders in its final
   state - a painted network beside a written interpretation. */
/* The hero visual: the product doing its own job.

   Two pathway networks, cross-faded. Each carries its own topology and its own
   values, painted with the same bwr ramp the application paints real data with
   - red high, neutral zero, blue low. Recolouring a single fixed network was not
   enough: the point is that you ask about a different pathway and get a
   different answer, so the network has to change too.

   Between the network and the panel sits the agent that actually does the
   work: earlier versions drew the network beside the interpretation with
   nothing between them, which read as the panel simply knowing the answer.
   It doesn't - it queries the painted values, searches the literature, and
   only then writes, and the citation pills at the bottom of the panel are
   where that search shows up. So the middle of the pipeline gets a node of
   its own: a pulse leaves the network, the agent pings while it works, a
   second pulse carries the result on to the panel, and only then does the
   panel start writing. One full pass per network, twice a cycle.

   Colours and shapes are the product's own. Circles are compounds and boxes
   are genes, exactly as the KEGG diagrams in the step cards below draw them,
   the grey is their stroke, and the agent and the citations share the AI blue
   - --pa-ai-blue. Nothing here invents a palette.

   The whole cycle is 9s and never stops. Animation lives in main.css so it can
   be turned off under prefers-reduced-motion, where the first network, an idle
   agent and a finished interpretation simply render as a still. */
var PO_HERO_VIZ_SVG =
	'<svg class="po-hero-viz" viewBox="0 0 500 300" role="img" xmlns="http://www.w3.org/2000/svg" ' +
		'aria-label="A pathway network hands its painted values to an AI agent, which searches the literature and writes a citation-grounded interpretation">' +
	'<title>From painted pathway, through the AI agent, to a citation-grounded interpretation</title>' +

	'<g class="po-viz-net po-viz-net-a">' +
	'<g stroke="#878787" stroke-opacity=".5" stroke-width="1.6" fill="none">' +
		'<path d="M42 96 L104 58"/>' +
		'<path d="M104 58 L168 92"/>' +
		'<path d="M42 96 L96 148"/>' +
		'<path d="M96 148 L168 92"/>' +
		'<path d="M168 92 L196 168"/>' +
		'<path d="M96 148 L118 210"/>' +
		'<path d="M118 210 L46 186"/>' +
		'<path d="M118 210 L196 168"/>' +
		'<path d="M46 186 L42 96"/>' +
	'</g>' +
		'<circle cx="42" cy="96" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="42" cy="96" r="11" fill="#A51F1E"/>' +
		'<circle cx="104" cy="58" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="104" cy="58" r="11" fill="#D9847B"/>' +
		'<rect x="156" y="80" width="13" height="16" rx="1.5" fill="#FFFFFF" stroke="#878787" stroke-width="1.6"/>' +
		'<rect x="156" y="80" width="13" height="16" rx="1.5" fill="#0F59A9"/>' +
		'<rect x="169" y="80" width="13" height="16" rx="1.5" fill="#FFFFFF" stroke="#878787" stroke-width="1.6"/>' +
		'<rect x="169" y="80" width="13" height="16" rx="1.5" fill="#78A1D6"/>' +
		'<circle cx="96" cy="148" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="96" cy="148" r="11" fill="#A51F1E"/>' +
		'<circle cx="196" cy="168" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="196" cy="168" r="11" fill="#78A1D6"/>' +
		'<circle cx="118" cy="210" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="118" cy="210" r="11" fill="#D9847B"/>' +
		'<circle cx="46" cy="186" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="46" cy="186" r="11" fill="#0F59A9"/>' +
	'</g>' +

	'<g class="po-viz-net po-viz-net-b">' +
	'<g stroke="#878787" stroke-opacity=".5" stroke-width="1.6" fill="none">' +
		'<path d="M58 72 L122 64"/>' +
		'<path d="M122 64 L190 92"/>' +
		'<path d="M190 92 L152 146"/>' +
		'<path d="M152 146 L72 152"/>' +
		'<path d="M72 152 L58 72"/>' +
		'<path d="M152 146 L196 196"/>' +
		'<path d="M196 196 L112 214"/>' +
		'<path d="M112 214 L72 152"/>' +
	'</g>' +
		'<circle cx="58" cy="72" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="58" cy="72" r="11" fill="#78A1D6"/>' +
		'<rect x="116" y="56" width="13" height="16" rx="1.5" fill="#FFFFFF" stroke="#878787" stroke-width="1.6"/>' +
		'<rect x="116" y="56" width="13" height="16" rx="1.5" fill="#A51F1E"/>' +
		'<rect x="129" y="56" width="13" height="16" rx="1.5" fill="#FFFFFF" stroke="#878787" stroke-width="1.6"/>' +
		'<rect x="129" y="56" width="13" height="16" rx="1.5" fill="#D9847B"/>' +
		'<circle cx="190" cy="92" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="190" cy="92" r="11" fill="#A51F1E"/>' +
		'<circle cx="152" cy="146" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="152" cy="146" r="11" fill="#FFFFFF"/>' +
		'<circle cx="72" cy="152" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="72" cy="152" r="11" fill="#D9847B"/>' +
		'<circle cx="196" cy="196" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="196" cy="196" r="11" fill="#0F59A9"/>' +
		'<circle cx="112" cy="214" r="11" fill="#FFFFFF" stroke="#878787" stroke-width="1.8"/>' +
		'<circle cx="112" cy="214" r="11" fill="#A51F1E"/>' +
	'</g>' +

	/* --- the agent: what the network hands off to, and what hands off to the
	   panel in turn. Sits in the 91-unit gap the widened viewBox opened
	   between the network (ends ~206) and the panel (now starts 298, shifted
	   +60 by the translate below rather than renumbered - same reasoning as
	   the network's own translate group elsewhere in this file: nothing that
	   already reads correctly against the panel's original 238-434 numbering
	   should have to be retyped just because the panel moved). ------------- */
	'<g class="po-viz-flow po-viz-flow-in">' +
		'<path class="po-viz-wire" d="M206 150 H233"/>' +
		'<circle class="po-viz-pulse po-viz-pulse-in" cx="206" cy="150" r="3.4"/>' +
	'</g>' +
	'<g class="po-viz-agent">' +
		'<circle class="po-viz-agent-ring po-viz-agent-ring-1" cx="253" cy="150" r="20"/>' +
		'<circle class="po-viz-agent-ring po-viz-agent-ring-2" cx="253" cy="150" r="20"/>' +
		'<circle class="po-viz-agent-body" cx="253" cy="150" r="20"/>' +
		/* getAIMarkPaths()'s 24-unit box centred on the node: translate(241,138)
		   lands its (12,12) centre on (253,150) with no rescale, leaving 8
		   units of the badge showing on every side. */
		'<g class="po-viz-agent-mark" transform="translate(241,138)" color="#4A90D9">' +
			getAIMarkPaths() +
		'</g>' +
	'</g>' +
	'<g class="po-viz-flow po-viz-flow-out">' +
		'<path class="po-viz-wire" d="M273 150 H297"/>' +
		'<circle class="po-viz-pulse po-viz-pulse-out" cx="273" cy="150" r="3.4"/>' +
	'</g>' +

	/* --- the interpretation ------------------------------------------------ */
	'<g class="po-viz-ai" transform="translate(60,0)">' +
		'<rect x="238" y="52" width="196" height="196" rx="10" class="po-viz-panel"/>' +
		/* The same mark the AI-powered highlight below the hero uses, from the
		   same source, rather than a second copy retyped into this diagram's
		   coordinates -- see getAIMarkPaths(). getAIMark() itself is no use here:
		   a nested <svg> sized in px would not scale with this viewBox.

		   The transform lands getAIMarkPaths()'s 24-unit box exactly where the
		   retyped 22-unit copy sat, so the mark occupies the same 15x17.3 of the
		   card as before: scale 15/13 for the width the copy had drawn, then a
		   translate that puts the box centre (12,12) back on (267,83.4). Checked
		   against the old numbers rather than eyeballed -- the hexagon's left and
		   right edges land on 259.5 and 274.5, which is where they already were. */
		'<g class="po-viz-mark" transform="translate(253.15,69.55) scale(1.154)" color="#4A90D9">' +
			getAIMarkPaths() +
		'</g>' +
		'<text x="284" y="88" class="po-viz-title" font-size="12.5" font-weight="600">AI interpretation</text>' +
		'<g class="po-viz-ans po-viz-ans-a">' +
			'<rect class="po-viz-line" style="--d:0.15s" x="256" y="106" width="160" height="6" rx="3"/>' +
			'<rect class="po-viz-line" style="--d:0.30s" x="256" y="122" width="142" height="6" rx="3"/>' +
			'<rect class="po-viz-line" style="--d:0.45s" x="256" y="138" width="152" height="6" rx="3"/>' +
			'<rect class="po-viz-line" style="--d:0.60s" x="256" y="154" width="118" height="6" rx="3"/>' +
			'<rect class="po-viz-cite" style="--d:0.95s" x="256" y="178" width="46" height="15" rx="7.5"/>' +
			'<rect class="po-viz-cite" style="--d:1.07s" x="308" y="178" width="46" height="15" rx="7.5"/>' +
			'<rect class="po-viz-cite" style="--d:1.19s" x="360" y="178" width="46" height="15" rx="7.5"/>' +
			'<rect class="po-viz-line" style="--d:1.47s" x="256" y="208" width="132" height="6" rx="3"/>' +
			'<rect class="po-viz-line" style="--d:1.57s" x="256" y="224" width="96" height="6" rx="3"/>' +
		'</g>' +
		'<g class="po-viz-ans po-viz-ans-b">' +
			'<rect class="po-viz-line" style="--d:0.15s" x="256" y="106" width="138" height="6" rx="3"/>' +
			'<rect class="po-viz-line" style="--d:0.30s" x="256" y="122" width="164" height="6" rx="3"/>' +
			'<rect class="po-viz-line" style="--d:0.45s" x="256" y="138" width="120" height="6" rx="3"/>' +
			'<rect class="po-viz-line" style="--d:0.60s" x="256" y="154" width="150" height="6" rx="3"/>' +
			'<rect class="po-viz-cite" style="--d:0.95s" x="256" y="178" width="46" height="15" rx="7.5"/>' +
			'<rect class="po-viz-cite" style="--d:1.07s" x="308" y="178" width="46" height="15" rx="7.5"/>' +
			'<rect class="po-viz-line" style="--d:1.35s" x="256" y="208" width="108" height="6" rx="3"/>' +
			'<rect class="po-viz-line" style="--d:1.45s" x="256" y="224" width="146" height="6" rx="3"/>' +
		'</g>' +
	'</g>' +
	'</svg>';


/* The three step diagrams share one frame: viewBox 186x96, content bleeding to
   both side edges, drawn on the same row rhythm. They did not, and that is what
   made the row of columns look unordered even though every element in it was
   correctly aligned. Measured on the deployed page, the three rendered 215, 138
   and 202px wide from viewBoxes of 186x90, 90x68 and 186x96 - so the middle one
   was a small island between two full-bleed drawings, and its network was drawn
   at scale 0.95 against the identical network in step 3 at 0.67. Same picture,
   two sizes, side by side.

   One frame means the eye compares the three as a set. */
var PO_STEP_ART_UPLOAD =
	'<svg class="po-step-art-svg" viewBox="0 0 186 96" role="img" aria-label="One row per omic type, each carrying its own values" style="font-family:var(--pa-font-sans)" xmlns="http://www.w3.org/2000/svg">' +
	/* -7 rather than -10: the four rows are 86 tall, so this centres them in the
	   96 the other two diagrams use. */
	'<g transform="translate(-258,-7)">' +
	'<rect x="258" y="12" width="186" height="20" rx="5" fill="#55C9A6" fill-opacity=".16" stroke="#55C9A6" stroke-opacity=".55"/>' +
	'<circle cx="271" cy="22" r="4.5" fill="#55C9A6"/>' +
	'<text x="283" y="26" font-size="11" fill="currentColor">Gene expression</text>' +
	'<rect x="410" y="18" width="7" height="7" rx="1" fill="#A51F1E" stroke="#878787" stroke-width="1"/>' +
	'<rect x="419" y="18" width="7" height="7" rx="1" fill="#D9847B" stroke="#878787" stroke-width="1"/>' +
	'<rect x="428" y="18" width="7" height="7" rx="1" fill="#78A1D6" stroke="#878787" stroke-width="1"/>' +
	'<rect x="258" y="34" width="186" height="20" rx="5" fill="#79B0EC" fill-opacity=".16" stroke="#79B0EC" stroke-opacity=".55"/>' +
	'<circle cx="271" cy="44" r="4.5" fill="#79B0EC"/>' +
	'<text x="283" y="48" font-size="11" fill="currentColor">Metabolomics</text>' +
	'<rect x="410" y="40" width="7" height="7" rx="1" fill="#78A1D6" stroke="#878787" stroke-width="1"/>' +
	'<rect x="419" y="40" width="7" height="7" rx="1" fill="#FFFFFF" stroke="#878787" stroke-width="1"/>' +
	'<rect x="428" y="40" width="7" height="7" rx="1" fill="#A51F1E" stroke="#878787" stroke-width="1"/>' +
	'<rect x="258" y="56" width="186" height="20" rx="5" fill="#B4A1DD" fill-opacity=".16" stroke="#B4A1DD" stroke-opacity=".55"/>' +
	'<circle cx="271" cy="66" r="4.5" fill="#B4A1DD"/>' +
	'<text x="283" y="70" font-size="11" fill="currentColor">Proteomics</text>' +
	'<rect x="410" y="62" width="7" height="7" rx="1" fill="#D9847B" stroke="#878787" stroke-width="1"/>' +
	'<rect x="419" y="62" width="7" height="7" rx="1" fill="#A51F1E" stroke="#878787" stroke-width="1"/>' +
	'<rect x="428" y="62" width="7" height="7" rx="1" fill="#FFFFFF" stroke="#878787" stroke-width="1"/>' +
	'<rect x="258" y="78" width="186" height="20" rx="5" fill="#738B9D" fill-opacity=".16" stroke="#738B9D" stroke-opacity=".55"/>' +
	'<circle cx="271" cy="88" r="4.5" fill="#738B9D"/>' +
	'<text x="283" y="92" font-size="11" fill="currentColor">+ 3 more omic types</text>' +
	'</g>' +
	'</svg>';

/* Was the same node-and-edge network step 3 draws, only unpainted and at a
   different scale - so the two adjacent columns showed one picture twice, and
   the copy that was supposed to illustrate *identifier conversion* showed no
   identifiers at all.

   This is what the card's own prose describes: the name in your file on the
   left, the Entrez or KEGG accession PaintOmics resolves it to on the right.
   Real pairs, mouse, one per omic type in step 1's colours - Gapdh/14433,
   D-Glucose/C00031 (KEGG compound), and the UniProt accession P17182 for Eno1
   resolving to Entrez 13806. A reader who knows the domain can check them. */
var PO_STEP_ART_MATCH = (function() {
	/* Grey on the left because what you upload is just text; the omic's own
	   colour on the right because that is the point at which PaintOmics knows
	   what the feature is. The three colours are step 1's, in step 1's order. */
	var rows = [
		{y: 10, from: 'Gapdh',     to: '14433',  colour: '#55C9A6'},
		{y: 38, from: 'D-Glucose', to: 'C00031', colour: '#79B0EC'},
		{y: 66, from: 'P17182',    to: '13806',  colour: '#B4A1DD'}
	];
	var svg = '<svg class="po-step-art-svg" viewBox="0 0 186 96" role="img" ' +
		'aria-label="Names and accessions in your files resolved to the identifiers the pathway databases use" ' +
		'style="font-family:var(--pa-font-sans)" xmlns="http://www.w3.org/2000/svg">';
	for (var i = 0; i < rows.length; i++) {
		var r = rows[i], mid = r.y + 10;
		svg +=
			'<rect x="0" y="' + r.y + '" width="72" height="20" rx="5" fill="#738B9D" fill-opacity=".12" stroke="#738B9D" stroke-opacity=".45"/>' +
			'<text x="10" y="' + (r.y + 14) + '" font-size="10.5" fill="currentColor">' + r.from + '</text>' +
			'<path d="M78 ' + mid + ' H105" stroke="#878787" stroke-opacity=".55" stroke-width="1.4" stroke-linecap="round"/>' +
			'<path d="M101.5 ' + (mid - 3.5) + ' L105 ' + mid + ' L101.5 ' + (mid + 3.5) + '" fill="none" stroke="#878787" stroke-opacity=".55" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>' +
			'<rect x="114" y="' + r.y + '" width="72" height="20" rx="5" fill="' + r.colour + '" fill-opacity=".16" stroke="' + r.colour + '" stroke-opacity=".55"/>' +
			'<text x="124" y="' + (r.y + 14) + '" font-size="10.5" fill="currentColor">' + r.to + '</text>';
	}
	return svg + '</svg>';
})();

/* Side by side, not stacked. Stacked, the three ranked bars took the top 25 of
   the 96 and left the network 57 to live in, which in a frame this wide meant a
   small drawing floating under three hairlines - the emptiest of the three
   plates by a distance, and the one step that has colour to show.

   The split also matches the sentence it illustrates and the screen it depicts:
   the ranked pathway list you read first on the left, the map you paint from it
   on the right. Four bars rather than three, on step 1's four-row rhythm. */
var PO_STEP_ART_EXPLORE =
	'<svg class="po-step-art-svg" viewBox="0 0 186 96" role="img" aria-label="Pathways ranked, and the network painted with your values" style="font-family:var(--pa-font-sans)" xmlns="http://www.w3.org/2000/svg">' +
	'<rect x="0" y="20" width="78" height="8" rx="4" fill="#AD5022" opacity="1.0"/>' +
	'<rect x="0" y="36" width="62" height="8" rx="4" fill="#AD5022" opacity="0.78"/>' +
	'<rect x="0" y="52" width="48" height="8" rx="4" fill="#AD5022" opacity="0.56"/>' +
	'<rect x="0" y="68" width="34" height="8" rx="4" fill="#AD5022" opacity="0.34"/>' +
	/* 0.70, up from 0.62: the network now has the right-hand 94 of the frame to
	   itself, so it can be drawn at the size the plate can carry. */
	'<g transform="translate(89.9,11.6) scale(0.70)">' +
	'<line x1="12" y1="30" x2="52" y2="12" stroke="#878787" stroke-width="1.6" stroke-opacity=".55"/>' +
	'<line x1="52" y1="12" x2="98" y2="34" stroke="#878787" stroke-width="1.6" stroke-opacity=".55"/>' +
	'<line x1="12" y1="30" x2="54" y2="56" stroke="#878787" stroke-width="1.6" stroke-opacity=".55"/>' +
	'<line x1="54" y1="56" x2="98" y2="34" stroke="#878787" stroke-width="1.6" stroke-opacity=".55"/>' +
	'<line x1="98" y1="34" x2="128" y2="74" stroke="#878787" stroke-width="1.6" stroke-opacity=".55"/>' +
	'<line x1="54" y1="56" x2="74" y2="92" stroke="#878787" stroke-width="1.6" stroke-opacity=".55"/>' +
	'<line x1="74" y1="92" x2="18" y2="78" stroke="#878787" stroke-width="1.6" stroke-opacity=".55"/>' +
	'<line x1="74" y1="92" x2="128" y2="74" stroke="#878787" stroke-width="1.6" stroke-opacity=".55"/>' +
	'<line x1="18" y1="78" x2="12" y2="30" stroke="#878787" stroke-width="1.6" stroke-opacity=".55"/>' +
	'<circle cx="12" cy="30" r="9" fill="#A51F1E" stroke="#878787" stroke-width="1.6"/>' +
	'<circle cx="52" cy="12" r="9" fill="#D9847B" stroke="#878787" stroke-width="1.6"/>' +
	'<rect x="39" y="50" width="10" height="12" fill="#0F59A9" stroke="#878787" stroke-width="1.6"/>' +
	'<rect x="49" y="50" width="10" height="12" fill="#78A1D6" stroke="#878787" stroke-width="1.6"/>' +
	'<rect x="59" y="50" width="10" height="12" fill="#FFFFFF" stroke="#878787" stroke-width="1.6"/>' +
	'<rect x="83" y="28" width="10" height="12" fill="#A51F1E" stroke="#878787" stroke-width="1.6"/>' +
	'<rect x="93" y="28" width="10" height="12" fill="#D9847B" stroke="#878787" stroke-width="1.6"/>' +
	'<rect x="103" y="28" width="10" height="12" fill="#A51F1E" stroke="#878787" stroke-width="1.6"/>' +
	'<circle cx="128" cy="74" r="9" fill="#78A1D6" stroke="#878787" stroke-width="1.6"/>' +
	'<circle cx="74" cy="92" r="9" fill="#FFFFFF" stroke="#878787" stroke-width="1.6"/>' +
	'<circle cx="18" cy="78" r="9" fill="#0F59A9" stroke="#878787" stroke-width="1.6"/>' +
	'</g>' +
	'</svg>';

/**
* The description shipped with the real STATegra example. Only used as the
* fallback when the server's catalogue is unreachable; otherwise the text is
* generated from the chosen dataset's own manifest entry.
*/
var STATEGRA_EXPERIMENT_DESIGN = "STATegra multi-omics time-course experiment in mouse B3 cell line (Mus musculus, mmu). Ikaros transcription factor expression was induced via tamoxifen treatment to trigger B-cell differentiation. Ikaros-induced vs Control samples were compared across 6 time points (0h, 2h, 6h, 12h, 18h, 24h). Values are log2 fold-changes (Ikaros/Control). Five omics layers: gene expression, proteomics, metabolomics, DNase-seq chromatin accessibility, and miRNA-seq. Goal: identify pathways and molecular mechanisms driving Ikaros-mediated B-cell differentiation across multiple regulatory levels.";

/**
* Manifest omic name -> the panel type addNewOmicSubmittingPanel builds.
*
* The manifest uses the omic names the *server* knows ("Gene expression",
* "DNase-seq"); the form uses its own panel keys ("geneexpression",
* "dnaseseq"). Matching is case-insensitive and punctuation-insensitive so a
* dataset written as "miRNA-seq", "miRNA-Seq" or "mirna seq" all land on the
* same panel -- the manifest is edited by hand and this is not worth failing on.
*
* An unrecognised name falls back to the generic "otheromic" panel rather than
* being skipped: showing a user a panel labelled with their omic's name is a
* better failure than silently loading fewer omics than the dataset contains.
*/
/**
* Put a read-only "this came from the example dataset" label in a file field.
*
* `myFilesSelectorButton.setValue(value)` defaults its origin to "mydata" and
* renders the value as `[MyData]/<value>`, which is right for a file the user
* picked out of their own storage and wrong for an example: the old panels
* displayed strings like `[MyData]/example/dnase_unmapped_values.tab`, naming a
* location that does not exist in anyone's data folder. Passing an explicit
* origin suppresses the prefix.
*
* The origin token also lands in the hidden `<prefix>_origin` field. "example"
* matches none of the branches JobInformationManager.saveFiles dispatches on
* ("client", "mydata", "inbuilt_gtf", "*filelocation*"), which is safe because
* example submissions never reach saveFiles -- the servlets resolve their files
* from the manifest and ignore the form entirely.
*
* setDisabled here greys out the Browse control inside the field and takes it
* out of the tab order -- the widget's whole interactive surface; the field
* itself stays enabled so the text can be read, copied and still posted. (A
* row its panel switches off, like the miRNA associations row under the
* correlation box, is disabled through the field instead, which also drops it
* from the post; the control follows that door too.)
*/
function setExampleLabel(field, text) {
	if (!field) { return; }
	field.setValue("[example dataset] " + text, "example");
	field.setDisabled(true);
}

/* Runs ExtJS's own validation -- allowBlank, maxLength, a validator function --
   on every field under `container`, marking each one, and says whether all
   passed. Every field, not up to the first failure: a form refused for one
   field and then again for the next is the second round checkForm() promises
   to spare the user. Shared by the four omic panels' isValid(). */
function validateAllFields(container) {
	var valid = true;
	/* One layout for the whole pass, as Ext.form.Basic.isValid does, instead
	   of one per field whose validity flips. */
	Ext.suspendLayouts();
	Ext.Array.each(container.query("field"), function(field) {
		if (!field.validate()) {
			valid = false;
		}
	});
	Ext.resumeLayouts(true);
	return valid;
}

/**
* Pipeline -> the single panel type that pipeline's example needs.
*
* These three are pre-processing steps: they convert their input into
* gene-based values and hand the result to the normal pathway analysis, so an
* example for them is one panel, not one per omic.
*/
var EXAMPLE_PANEL_FOR_PIPELINE = {
	"regions2genes": "bedbasedomic",
	"mirna2genes": "mirnabasedomic",
	"more": "moreanalysis"
};

function examplePanelTypeFor(omicName) {
	var key = String(omicName || "").toLowerCase().replace(/[^a-z]/g, "");
	var byKey = {
		"geneexpression": "geneexpression",
		"proteomics": "proteomics",
		"metabolomics": "metabolomics",
		"mirnaseq": "mirnaseq",
		"mirna": "mirnaseq",
		"mirnaunmapped": "mirnaseq",
		"dnaseseq": "dnaseseq",
		"dnase": "dnaseseq",
		"dnaseunmapped": "dnaseseq",
		"transcriptionfactor": "transcriptionfactor"
	};
	return byKey[key] || "otheromic";
}

/**
* A plain-language description of a dataset, for the AI-interpretation prompt.
*
* Generated from the manifest entry rather than stored, so it cannot drift from
* the data the way a hand-written blurb does -- the previous version quoted
* feature counts ("6337 genes, 5224 DE") that no longer matched the files.
*/
function exampleExperimentDesignFor(scenario) {
	var conditions = scenario.conditions || [];
	var parts = [scenario.title + "."];

	if (scenario.summary) { parts.push(scenario.summary); }
	parts.push("Organism: " + (scenario.organism || "mmu") + ".");
	if ((scenario.omicNames || []).length) {
		parts.push("Omics layers: " + scenario.omicNames.join(", ") + ".");
	}
	if (conditions.length) {
		parts.push("Conditions (" + conditions.length + "): " +
			conditions.join(", ") + ".");
	}
	parts.push(scenario.simulated
		? "Values are simulated log2 fold-changes with a coherent signal planted " +
		  "into a chosen set of KEGG pathways; all other features are centred on zero."
		: "Values are log2 fold-changes against the control condition.");
	return parts.join(" ");
}

/*********************************************************************
* INSTALLED PATHWAY DATABASES
*
* Which of KEGG / MapMan / Reactome a job can actually use is a property of
* the server and the chosen organism, not of the form. GET /organism_databases
* reports it, read from each organism's own MongoDB, and step 1 draws its
* checkboxes from the answer: every database it names is ticked, and every
* database it does not is disabled.
*
* Fetched once per page. The map is a few hundred bytes for a hundred
* organisms and changes only when an administrator installs a species, so
* re-reading it on every change of the organism combo would buy nothing and
* put a round trip in front of a click.
***********************************************************************/
var ORGANISM_DATABASES = null;
var ORGANISM_DATABASES_REQUEST = null;
/* Whether the endpoint actually answered, which is NOT the same question as
   whether the map has an entry for an organism. species.json is written once by
   DBManager and lists what was installed when it last ran -- this repository's
   own copy offers about a hundred species on a machine that has two -- so an
   organism can be selectable in the combo and have no pathways at all. That has
   to disable every optional database, whereas an endpoint that never answered
   has to enable them. Same missing entry, opposite correct answers. */
var ORGANISM_DATABASES_READ = false;

/**
* Resolves to {organism: [databases]}, fetching it at most once.
*
* Never rejects. A server too old to have the endpoint, or one that cannot
* reach MongoDB, resolves without setting ORGANISM_DATABASES_READ, and
* getInstalledDatabasesFor() then offers everything -- which is what this form
* did before the endpoint existed. Degrading to a form that offers too much is
* recoverable; the server drops what it cannot run, exactly as it always has.
* Degrading to a form that offers nothing would make step 1 unusable.
*/
function loadOrganismDatabases() {
	if (ORGANISM_DATABASES_REQUEST === null) {
		// A Deferred that is only ever resolved, rather than the jqXHR's own
		// promise. Returning a value from a .then() failure filter recovers the
		// chain in jQuery 3 and does NOT in jQuery 1 or 2, and this file is
		// loaded next to a jquery-migrate shim -- a promise that silently stops
		// resolving would leave every optional database disabled for good.
		var ready = $.Deferred();
		ORGANISM_DATABASES_REQUEST = ready.promise();

		$.getJSON(SERVER_URL_GET_ORGANISM_DATABASES)
			.done(function(response) {
				ORGANISM_DATABASES = (response && response.databases) || {};
				ORGANISM_DATABASES_READ = true;
			})
			.fail(function() {
				console.warn("Could not read the installed pathway databases; " +
					"every database will be offered and the server will drop " +
					"the ones it cannot run.");
				ORGANISM_DATABASES = {};
			})
			.always(function() { ready.resolve(ORGANISM_DATABASES); });
	}
	return ORGANISM_DATABASES_REQUEST;
}

/**
* The databases installed for one organism, or null for "cannot tell".
*
* null is the only answer that means "offer everything and let the server drop
* what it cannot run" -- the behaviour this form had before the endpoint
* existed. It is returned when, and only when, the map could not be read.
*
* An organism the map does not mention gets ["KEGG"]: the endpoint enumerates
* the organisms that have a MongoDB database of their own, so a name missing
* from it has no pathways installed under any database, and offering MapMan or
* Reactome for it would be the same empty promise this change exists to remove.
*/
function getInstalledDatabasesFor(organism) {
	if (!ORGANISM_DATABASES_READ) { return null; }
	if (!organism) { return ["KEGG"]; }
	var installed = ORGANISM_DATABASES[organism];
	return (installed && installed.length) ? installed : ["KEGG"];
}

function PA_Step1JobView() {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step1JobView";
	this.nFiles = 0;
	this.exampleMode = false;
	// Regulatory Omic analysis method, locked once chosen for the whole job.
	// null until the user picks; "pairwise" or "more" thereafter.
	this.regulatoryMethod = null;

	/*********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.isExampleMode = function() {
		return this.exampleMode;
	};
	/**
	* The example dataset the user picked, or null for the server's default.
	* Appended to the "/example" URL as an extra path segment; the routes use
	* Flask's <path:> converter, so no new endpoint is involved.
	*/
	this.getExampleScenarioId = function() {
		return this.exampleScenarioId || null;
	};
	/**
	* The pipeline the loaded example belongs to, or null when none is loaded.
	*
	* This is what tells the controller whether the *pathway* submission is
	* still an example submission. For a `pathway-acquisition` dataset it is:
	* step 1 reads the bundled files directly. For `regions2genes`,
	* `mirna2genes` and `more` it is NOT: those run first and produce real files
	* into the job's own directory, and step 1 must then be an ordinary upload
	* of that output. Sending step 1 to the example endpoint instead makes it
	* re-read the dataset's raw inputs and throw the conversion away -- observed
	* as "EXAMPLE 'regulatory-more' REGISTERED (2 omics)" in the step-1 log
	* after MORE had already produced its GENE:::REGULATOR values.
	*/
	this.getExamplePipeline = function() {
		return this.examplePipeline || null;
	};

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* This function remove all panels and reset the application.
	*/
	this.resetViewHandler = function() {
		this.regulatoryMethod = null;
		this.controller.resetButtonClickHandler(this);
	};
	/**
	* Opens the modal that lets the user pick between Pairwise and MORE
	* the first time they add a Regulatory Omic in this job. Once chosen,
	* `this.regulatoryMethod` is locked for the rest of the session.
	*/
	this.showRegulatoryMethodChooser = function() {
		var me = this;
		var pickHandler = function(method) {
			return function() {
				me.regulatoryMethod = method;
				win.close();
				me.addNewOmicSubmittingPanel("regulatoryomic");
			};
		};
		var win = Ext.create('Ext.window.Window', {
			title: 'Regulatory Omic — choose analysis method',
			modal: true,
			width: 720,
			closable: true,
			bodyPadding: 14,
			layout: { type: 'hbox', align: 'stretch' },
			defaults: { flex: 1, margin: 6, bodyPadding: 12, border: 1 },
			items: [{
				xtype: 'panel',
				title: 'Pairwise',
				html: '<p style="min-height: 130px;">Analyse <b>one regulatory omic at a time</b>. ' +
					'Independent correlation between each regulator and its target gene. ' +
					'Use this for the classical miRNA-target style of analysis. ' +
					'You can add several Pairwise panels (one per regulatory omic) in the same job.</p>',
				bbar: ['->', { xtype: 'button', text: 'Choose Pairwise', handler: pickHandler('pairwise') }]
			}, {
				xtype: 'panel',
				title: 'MORE',
				html: '<p style="min-height: 130px;">Joint <b>multi-omic regression</b> (PLS / MLR) over one or ' +
					'more regulators at once, filtered by VIP, &alpha; and R&sup2;. Use this when you want ' +
					'all regulators integrated into a single model. Only one MORE panel is allowed per job; ' +
					'use the &ldquo;+ Add another Regulatory Omic&rdquo; button inside the panel to stack regulators.</p>',
				bbar: ['->', { xtype: 'button', text: 'Choose MORE', handler: pickHandler('more') }]
			}]
		});
		win.show();
	};
	/**
	* This function adds a new OmicSubmittingPanel for the given type.
	* @param {string} type for the new omic panel.
	*/
	this.addNewOmicSubmittingPanel = function(type) {
		var newElem, submitForm;

		if (type === "geneexpression") {
			newElem = new OmicSubmittingPanel(this.nFiles, {
				type: "Gene expression",
				fileType: "Gene Expression file",
				relevantFileType: "Relevant Genes list"
			});
		} else if (type === "proteomics") {
			newElem = new OmicSubmittingPanel(this.nFiles, {
				type: "Proteomics",
				fileType: "Proteomic quatification",
				relevantFileType: "Relevant proteins list",
				featureEnrichment: "features"
			});
		} else if (type === "metabolomics") {
			newElem = new OmicSubmittingPanel(this.nFiles, {
				type: "Metabolomics",
				fileType: "Metabolomic quatification",
				relevantFileType: "Relevant Compound list",
				featureEnrichment: "features"
			});
		} else if (type === "mirnaseq") {
			newElem = new OmicSubmittingPanel(this.nFiles, {
				type: "miRNA-seq",
				fileType: "miRNA-Seq quatification",
				relevantFileType: "Relevant miRNA-Seq list"
			});
		} else if (type === "dnaseseq") {
			newElem = new OmicSubmittingPanel(this.nFiles, {
				type: "DNase-seq",
				fileType: "DNAse-Seq quatification",
				relevantFileType: "Relevant DNAse-Seq list"
			});
		} else if (type === "mirnabasedomic") {
			// Legacy entry point — kept for restore-from-saved-job paths.
			newElem = new MiRNAOmicSubmittingPanel(this.nFiles);
			if (this.regulatoryMethod === null) { this.regulatoryMethod = "pairwise"; }
		} else if (type === "bedbasedomic") {
			newElem = new RegionBasedOmicSubmittingPanel(this.nFiles);
		} else if (type === "otheromic") {
			newElem = new OmicSubmittingPanel(this.nFiles);
		} else if (type === "moreanalysis") {
			// Legacy entry point — kept for restore-from-saved-job paths.
			newElem = new MORESubmittingPanel(this.nFiles);
			if (this.regulatoryMethod === null) { this.regulatoryMethod = "more"; }
		} else if (type === "regulatoryomic") {
			// Unified Regulatory Omic entry. First click opens the chooser;
			// subsequent clicks route to the locked method.
			if (this.regulatoryMethod === null) {
				this.showRegulatoryMethodChooser();
				return null;
			}
			if (this.regulatoryMethod === "pairwise") {
				newElem = new MiRNAOmicSubmittingPanel(this.nFiles, { regulatoryMethod: "pairwise" });
			} else {
				// MORE: enforce a single MORE panel per job (joint analysis only).
				var existingMore = this.getComponent().queryById("submittingPanelsContainer")
					.query("container[cls=omicbox moreBasedOmic]");
				if (existingMore.length > 0) {
					showInfoMessage("MORE Analysis", {
						message: "You already have a MORE panel in this job. Use the " +
							"&ldquo;+ Add another Regulatory Omic&rdquo; button inside it to add more regulators.",
						showButton: true
					});
					return null;
				}
				newElem = new MORESubmittingPanel(this.nFiles);
			}
		} else if (type == "transcriptionfactor") {
			newElem = new OmicSubmittingPanel(this.nFiles, {
				type: "Transcription factor",
				fileType: "Transcription factor file",
				relevantFileType: "Relevant Genes list"
			});
		}
		newElem.setParent(this);

		submitForm = this.getComponent().queryById("submittingPanelsContainer");
		submitForm.insert(1, newElem.getComponent()).focus();

		// The Regulatory Omic card stays clickable in Pairwise mode (multi-add)
		// but is hidden once a MORE panel exists (single-panel rule).
		var keepCardVisible = (
			type === "otheromic" || type === "bedbasedomic" || type === "mirnabasedomic" ||
			(type === "regulatoryomic" && this.regulatoryMethod === "pairwise")
		);
		if (!keepCardVisible) {
			$("div.availableOmicsBox[title=" + type + "]").css("display", "none");
		}

		if (submitForm.items.getCount() > 2) {
			$(".dragHerePanel").fadeOut();
		}

		this.nFiles++;

		return newElem;
	};
	/**
	* This function removes a given omicSubmittingPanel and restores the omic type
	* at the availableOmics panel.
	*
	* @param {OmicSubmittingPanel} omicSubmittingPanel
	*/
	this.removeOmicSubmittingPanel = function(omicSubmittingPanel) {
		var submitForm = this.getComponent().queryById("submittingPanelsContainer");
		submitForm.remove(omicSubmittingPanel.getComponent());

		var removedType = omicSubmittingPanel.type;
		if (!this.exampleMode) {
			if (removedType === "moreanalysis") {
				// MORE panel removed — free up the unified Regulatory Omic card again.
				$("div.availableOmicsBox[title=regulatoryomic]").fadeIn();
			} else if (removedType !== undefined &&
				removedType !== "otheromic" && removedType !== "bedbasedomic" &&
				removedType !== "mirnabasedomic") {
				$("div.availableOmicsBox[title=" + removedType + "]").fadeIn();
			}
		}

		// Auto-unlock regulatory method when no Regulatory Omic panels remain,
		// so the user can pick a different method on their next add.
		var remainingRegulatory = submitForm.query(
			"container[cls=omicbox miRNABasedOmic],[cls=omicbox moreBasedOmic]"
		);
		if (remainingRegulatory.length === 0) {
			this.regulatoryMethod = null;
		}

		if (submitForm.items.getCount() === 2 && !this.exampleMode) {
			$(".dragHerePanel").fadeIn();
		}

		delete omicSubmittingPanel;
	};
	/**
	* Opens the "Load example" picker, populated from the server's catalogue.
	*
	* The catalogue lives in examplefiles/datasets/manifest.json and is the
	* single source of truth for which examples exist. Nothing about the
	* scenarios is hardcoded here: adding one to the manifest makes it appear.
	*
	* Every pipeline is listed, not just pathway-acquisition. The region,
	* miRNA and MORE examples are pre-processing steps that feed this same form,
	* and setExampleModeHandler creates the one panel each of them needs -- so
	* they are reachable here rather than only by hand-typing a URL. MORE in
	* particular had no example at all before, which is why its input format
	* (per-sample matrices plus a numeric design matrix, unlike every other
	* omic) was undocumented by example.
	*/
	this.showExampleChooser = function() {
		var me = this;

		$.ajax({
			url: SERVER_URL_EXAMPLE_DATASETS,
			type: "GET",
			dataType: "json"
		}).done(function(response) {
			var scenarios = (response && response.scenarios) ? response.scenarios : [];

			if (scenarios.length === 0) {
				// The server has no catalogue -- an incomplete deploy. Fall back
				// to the default rather than leaving the button dead: the
				// servlet resolves "example" with no id on its own.
				me.setExampleModeHandler(null);
				return;
			}
			me.renderExampleChooser(scenarios, response.defaultScenario);
		}).fail(function() {
			// Same reasoning: a failed catalogue request must not remove the
			// ability to load an example.
			me.setExampleModeHandler(null);
		});
	};
	/**
	* Draws the picker. Split from the fetch so the layout can be reasoned
	* about (and changed) without touching the request handling.
	*/
	this.renderExampleChooser = function(scenarios, defaultScenarioId) {
		var me = this;

		// Grouped by pipeline, because the groups behave differently once
		// loaded: the first submits straight to the pathway analysis, the rest
		// run a conversion step first.
		var GROUPS = [
			["pathway-acquisition", "Multi-omic pathway analysis",
			 "Loads straight into the form below and runs the pathway analysis."],
			["mirna2genes", "Regulatory omics — pairwise",
			 "Pairs each regulator with its target genes first, then runs the analysis."],
			["more", "Regulatory omics — MORE",
			 "Fits a joint multi-omic regression over per-sample data, then runs the analysis."],
			["regions2genes", "Region-based omics",
			 "Assigns genomic regions to genes with RGmatch first, then runs the analysis."]
		];

		/* One dataset, as a card.
		 *
		 * This was an `Ext.panel.Panel` per scenario: a framed panel with a
		 * titled header band and a `bbar` holding the Load button. That is three
		 * pieces of framework chrome - header, body, docked toolbar - stacked to
		 * produce something that is a card, and it looked like it: the title sat
		 * in a grey strip, the body in a second box inside it, and the button on
		 * a third strip below, each with its own border. Seven datasets meant
		 * twenty-one nested rectangles down a scrolling dialog.
		 *
		 * It is one `<article>` now, styled by `.po-example*` in main.css, so
		 * the card has one edge, one padding and a hover state, and the Load
		 * button is a button rather than a toolbar with one item in it. The
		 * click is bound in `afterrender` because the markup is a string - the
		 * same approach the rest of this file uses for its HTML boxes.
		 */
		var makeCard = function(scenario) {
			var badge = scenario.simulated
				? '<span class="po-example-badge po-example-badge-sim">simulated</span>'
				: '<span class="po-example-badge po-example-badge-real">real data</span>';

			var facts = [];
			if (scenario.omicNames && scenario.omicNames.length) {
				facts.push('<b>' + scenario.omicNames.length + '</b> omic' +
					(scenario.omicNames.length === 1 ? '' : 's') +
					': ' + scenario.omicNames.join(', '));
			}
			if (scenario.conditions && scenario.conditions.length) {
				facts.push('<b>' + scenario.conditions.length + '</b> condition' +
					(scenario.conditions.length === 1 ? '' : 's'));
			}
			if (scenario.databases && scenario.databases.length) {
				facts.push(scenario.databases.join(' + '));
			}

			var tests = (scenario.tests || []).map(function(item) {
				return '<li>' + Ext.String.htmlEncode(item) + '</li>';
			}).join('');

			var isDefault = scenario.id === defaultScenarioId;

			return {
				xtype: 'box',
				cls: 'po-example-card',
				html: '<article class="po-example">' +
					'<header class="po-example-head">' +
					'<h4 class="po-example-title">' +
					Ext.String.htmlEncode(scenario.title) + '</h4>' + badge +
					(isDefault ? '<span class="po-example-badge po-example-badge-default">default</span>' : '') +
					'</header>' +
					'<p class="po-example-summary">' +
					Ext.String.htmlEncode(scenario.summary) + '</p>' +
					'<p class="po-example-facts">' + facts.join('<span class="po-example-dot">•</span>') + '</p>' +
					(tests ? '<div class="po-example-tests">' +
						'<h5>Exercises</h5><ul>' + tests + '</ul></div>' : '') +
					'<div class="po-example-actions">' +
					'<a href="javascript:void(0)" class="button btn-inline po-example-load">Load this dataset</a>' +
					'</div>' +
					'</article>',
				listeners: {
					afterrender: function(cmp) {
						$('#' + cmp.el.id + ' .po-example-load').on('click', function() {
							win.close();
							me.setExampleModeHandler(scenario);
						});
					}
				}
			};
		};

		var items = [{
			xtype: 'box',
			cls: 'po-example-intro',
			html: '<p>Each dataset exercises a ' +
				'different part of PaintOmics. The <b>real data</b> entries are ' +
				'the published STATegra time course; the <b>simulated</b> ones ' +
				'carry a known signal planted into real KEGG pathways, so you ' +
				'can check that the analysis recovers what was put in.</p>'
		}];

		GROUPS.forEach(function(group) {
			var pipeline = group[0], heading = group[1], note = group[2];
			var inGroup = scenarios.filter(function(s) { return s.pipeline === pipeline; });
			if (inGroup.length === 0) { return; }

			items.push({
				xtype: 'box',
				cls: 'po-example-group',
				html: '<h3>' + heading + '</h3><p>' + note + '</p>'
			});
			inGroup.forEach(function(scenario) { items.push(makeCard(scenario)); });
		});

		var win = Ext.create('Ext.window.Window', {
			title: 'Load example — choose a dataset',
			cls: 'po-example-window',
			modal: true,
			width: 760,
			maxHeight: 660,
			closable: true,
			// Stated here, not in CSS: `bodyPadding` writes an inline `padding` on
			// the body element, so a stylesheet rule cannot win it - and ExtJS needs
			// the number anyway to size the cards inside.
			bodyPadding: '0 22 22 22',
			autoScroll: true,
			items: items
		});
		win.show();
	};
	/**
	* Configures the form for a chosen example dataset.
	*
	* @param {Object|null} scenario an entry from /example_datasets, or null to
	*        let the server pick its default (used when the catalogue is
	*        unreachable, so the button still works on an incomplete deploy).
	*/
	this.setExampleModeHandler = function(scenario) {
		var omicSubmittingPanels;
		this.exampleMode = true;
		this.exampleScenarioId = scenario ? scenario.id : null;
		this.examplePipeline = (scenario && scenario.pipeline) || "pathway-acquisition";

		var speciesCombo = this.getComponent().queryById("speciesCombobox");
		speciesCombo.setValue(scenario && scenario.organism ? scenario.organism : "mmu");
		speciesCombo.setReadOnly(true);

		// The databases are NOT taken from the scenario. setValue above has
		// already fired the combo's change listener, so applyDatabaseAvailability
		// has ticked whatever this server installed for the dataset's organism --
		// and that is what PathwayAcquisitionServlet will run, because its example
		// branch resolves the same way and overrides the manifest.
		//
		// The manifest's `databases` is a property of the dataset as authored and
		// cannot know what the host running it installed. Five of the seven
		// bundled scenarios declare KEGG alone while every one of them is mmu, an
		// organism that ships with Reactome, so honouring it meant an example ran
		// against half the pathways the same files would reach as an upload.

		// "~=", not "=". ComponentQuery's "=" compares the WHOLE cls string, and
		// only the plain omic panel declares cls:"omicbox" on its own; the region
		// ("omicbox regionBasedOmic"), miRNA ("omicbox miRNABasedOmic") and MORE
		// ("omicbox moreBasedOmic") panels never matched and therefore survived
		// this clear-out into the next example -- loading MORE and then a gene
		// dataset left the MORE panel sitting above the new omics and submitted it.
		// "~=" tests one entry of the whitespace-separated list, which is what the
		// cls attribute actually is.
		omicSubmittingPanels = this.getComponent().queryById("submittingPanelsContainer").query("[cls~=omicbox]");
		for (var i in omicSubmittingPanels) {
			$("#" + omicSubmittingPanels[i].el.id + " a.deleteOmicBox").click();
		}

		var me = this;
		var pipeline = (scenario && scenario.pipeline) || "pathway-acquisition";

		if (pipeline === "pathway-acquisition") {
			// One panel per omic the dataset actually contains, in the order the
			// manifest lists them. Previously this was a fixed list of six, so
			// a dataset with a different composition showed the wrong form.
			var omicNames = (scenario && scenario.omicNames) ||
				["miRNA-seq", "DNase-seq", "Proteomics",
				 "Metabolomics", "Gene expression"];
			omicNames.forEach(function(omicName) {
				var panel = me.addNewOmicSubmittingPanel(examplePanelTypeFor(omicName));
				if (panel) { panel.setExampleMode(omicName, ((scenario && scenario.designOmics) || []).indexOf(omicName) > -1); }
			});
		} else {
			// A pre-processing pipeline: regions-to-genes, miRNA-to-genes or
			// MORE. These take ONE panel, and submitting it posts to that
			// pipeline's own endpoint first; its output then feeds the normal
			// pathway analysis. submitFormHandler already routes on the panel's
			// css class, so creating the right panel is the whole wiring.
			//
			// MORE additionally needs its method locked, because the unified
			// "Regulatory Omic" card would otherwise open the method chooser.
			if (pipeline === "more") { me.regulatoryMethod = "more"; }
			var panel = me.addNewOmicSubmittingPanel(EXAMPLE_PANEL_FOR_PIPELINE[pipeline]);
			if (panel && panel.setExampleMode) {
				panel.setExampleMode(scenario);
			}
		}

		$("#availableOmicsContainer").css("display", "none");
		// The button used to hide itself here, so a second dataset could only be
		// reached by throwing the whole job away with Reset. Loading an example is
		// not a one-way door -- the clear-out above is the first thing this
		// function does -- so it stays, renamed for what it now does.
		$("#exampleButton").html('<i class="fa fa-file-text-o"></i> Load another example');

		// Ticks the AI consent checkbox, and only ever on this branch.
		//
		// This used to deliberately leave it alone, reasoning that a dataset being
		// public says nothing about whether the user wants a third-party LLM call
		// made on their behalf. That argument holds for an *upload* and is why the
		// box still defaults off there (see DEFAULT_AI_CONSENT_ENABLED at the
		// checkbox definition). It does not hold here: the example datasets are
		// published STATegra measurements and generated simulations, they contain
		// nothing of the user's, and every scenario that carries an interpretation
		// lists "AI interpretation" among the things it exists to exercise. Leaving
		// the box clear meant the flagship demo of the feature silently produced
		// "Not started", which reads as a broken build rather than a withheld
		// permission.
		//
		// What is sent is still the pathway results, matched feature values and the
		// experiment design text set just below -- all of it derived from the
		// example data, none of it from the person clicking. Pressing Reset or
		// unticking the box before Run both still work; this is a default, not a
		// lock.
		var aiConsent = this.getComponent().down('[name=aiConsent]');
		if (aiConsent) { aiConsent.setValue(true); }

		var expDesign = this.getComponent().down('[name=experimentDesign]');
		if (expDesign && scenario) {
			expDesign.setValue(exampleExperimentDesignFor(scenario));
		} else if (expDesign) {
			expDesign.setValue(STATEGRA_EXPERIMENT_DESIGN);
		}

		this.lockFormForExample(pipeline);

		// Inside the callback, so the "Databases" line names the databases the
		// job will really run rather than the ones the manifest declares. It used
		// to print the manifest's list, which after this change would understate
		// every mmu example by leaving Reactome out of a run that includes it.
		this.applyDatabaseAvailability(function(databases) {
			var facts = scenario
				? '<ul>' +
					'<li><b>Dataset:</b> ' + Ext.String.htmlEncode(scenario.title) + '</li>' +
					'<li><b>Organism:</b> ' + Ext.String.htmlEncode(scenario.organism || "mmu") + '</li>' +
					'<li><b>Omics:</b> ' + Ext.String.htmlEncode((scenario.omicNames || []).join(', ')) + '</li>' +
					'<li><b>Conditions:</b> ' + ((scenario.conditions || []).length || 1) + '</li>' +
					'<li><b>Databases:</b> ' + Ext.String.htmlEncode(databases.join(', ')) + '</li>' +
					'</ul>' +
					(scenario.simulated
						? '<p>This dataset is <b>simulated</b>: a known signal was planted ' +
						  'into real KEGG pathways. The pathways the enrichment should rank ' +
						  'highest are listed alongside the files, so you can check that ' +
						  'the analysis recovers them.</p>'
						: '<p>This is <b>real published data</b> (STATegra).</p>')
				: '<p>The bundled example dataset was loaded.</p>';

			showInfoMessage("About this example", {
				message: facts,
				showButton: true,
				height: 300
			});
		});
	};

	/**
	* Ticks every pathway database installed for `organism` and locks the rest.
	*
	* This is the whole of the rule: a database that is installed is selected,
	* because there is no reason to analyse against half of what the server has;
	* a database that is not installed cannot be selected, because
	* PathwayAcquisitionServlet would drop it and the analysis has no pathways to
	* run it against. Both halves used to be wrong in the same direction -- all
	* three boxes were always offered, Reactome was pre-ticked on localhost and
	* nowhere else, and the two that did not apply were silently discarded.
	*
	* KEGG is skipped: its box is ticked and disabled from birth and the server
	* unions it into every job regardless.
	*
	* Asynchronous because the availability map may still be in flight; ordering
	* is safe without a guard because it reads the combo's value at apply time
	* rather than closing over the organism it was called for, so two overlapping
	* calls converge on the same answer instead of racing.
	*
	* @param {Function} done optional, called with the applied database list.
	*/
	this.applyDatabaseAvailability = function(done) {
		var me = this;
		loadOrganismDatabases().then(function() {
			var combo = me.getComponent().queryById("speciesCombobox");
			var organism = combo ? combo.getValue() : null;
			var installed = getInstalledDatabasesFor(organism);
			var applied = ["KEGG"];

			Ext.each(["MapMan", "Reactome", "OmniPath"], function(database) {
				var box = me.getComponent().queryById(database.toLowerCase() + "DB");
				if (!box) { return; }

				// null means the map never arrived: offer the box rather than
				// hiding a database the server may well have.
				var available = (installed === null) ||
					(Ext.Array.indexOf(installed, database) !== -1);

				// setValue before setDisabled. A disabled checkbox is excluded
				// from getSubmitData(), so leaving a stale tick on a disabled box
				// would show a database as selected that could never be posted.
				box.setValue(available);
				box.setDisabled(!available);
				if (box.setBoxLabel) {
					// Short on purpose: the group is two columns wide, and
					// "(not installed for this organism)" wrapped the label onto
					// a second line, leaving the checkbox aligned with nothing.
					// "for this organism" is already said by the note below.
					/* A class, not `style="color:#8A8A8A"`: an inline colour is
					   unreachable by dark.css, so on the dark form this greyed-out
					   note stayed a light-theme grey on a dark surface. */
					box.setBoxLabel(available
						? database
						: database + ' <span class="po-db-tag">not installed</span>');
				}
				if (available) { applied.push(database); }
			});

			me.describeDatabaseAvailability(organism, installed, applied);
			if (done) { done(applied); }
		});
	};

	/**
	* Replaces the note under the checkboxes with what is true for this organism.
	*
	* The note it replaces said "for some species more than one database might be
	* available" and left the reader to find out which by ticking a box and
	* reading the results.
	*/
	this.describeDatabaseAvailability = function(organism, installed, applied) {
		var note = this.getComponent().queryById("databasesAvailabilityNote");
		if (!note || !note.update) { return; }

		/* Silent in both of the ordinary cases. "Choose an organism to see which
		   databases are available" and "KEGG + Reactome are installed for mmu"
		   both said what the chips beside this line already show -- one chip per
		   database, on when the organism has it -- so the note was a sentence
		   restating a control that had not moved.

		   What the chips cannot show is that the answer is a guess: when
		   /organism_databases could not be read, every box is offered and the
		   server drops what it cannot run. That is the one case worth a line. */
		if (installed !== null) {
			note.update('');
			return;
		}

		note.update('<p class="po-db-note">The list of installed databases could not be' +
			' read from the server, so all of them are offered; any that this server' +
			' cannot run for ' + Ext.String.htmlEncode(organism || 'this organism') +
			' will be left out of the analysis.</p>');
	};

	/**
	* Shows the loaded example for what it is: a fixed dataset, not a draft.
	*
	* The composition of an example job is decided on the server. For a
	* pathway-acquisition dataset the client posts a scenario id and
	* pathwayAcquisitionStep1_PART1 rebuilds the omic list, the organism and the
	* databases from examplefiles/datasets/manifest.json, ignoring every omic
	* field in this form; the pre-processing pipelines do the same in their own
	* servlets (applyScenario / applyMoreScenario). So the form was decorative
	* and did not say so: deleting the Metabolomics panel and pressing Run
	* produced a job byte-identical to an untouched one, and the progress dialog
	* still announced "mapping Metabolomics".
	*
	* Locking it is the honest half of the fix. Making the server honour the
	* edits is the other half and is not done here -- see the handoff note.
	*
	* Nothing here uses setDisabled(): a disabled field returns null from
	* getSubmitData() and never reaches the server, and the chained pipelines
	* (regions2genes, mirna2genes, more) DO post this form for real once their
	* conversion has run -- their step 1 is an ordinary upload, which is why
	* step1OnFormSubmitHandler refuses the example endpoint for them. setReadOnly
	* keeps every value on the wire while removing the invitation to change it.
	*
	* @param {string} pipeline the loaded scenario's pipeline.
	*/
	this.lockFormForExample = function(pipeline) {
		var container = this.getComponent().queryById("submittingPanelsContainer");
		var panels = container.query("[cls~=omicbox]");
		var fields, i, j;

		for (i = 0; i < panels.length; i++) {
			fields = panels[i].query("field");
			for (j = 0; j < fields.length; j++) {
				if (fields[j].setReadOnly) {
					fields[j].setReadOnly(true);
				}
			}
			// The trash icon is the most misleading control on the page: it
			// removes the panel and changes nothing about what runs. Hidden
			// rather than unbound, because the clear-out in
			// setExampleModeHandler still triggers this handler by jQuery when a
			// second example is loaded over this one.
			if (panels[i].getEl()) {
				$(panels[i].getEl().dom).find("a.deleteOmicBox").css("display", "none");
			}

			// MORE's "+ Add another Regulatory Omic" builds a block that the
			// example endpoint never reads: MOREServlet returns right after
			// applyMoreScenario, which takes every regulator from the manifest.
			var addOmicWrapper = panels[i].queryById("addOmicWrapper");
			if (addOmicWrapper) { addOmicWrapper.setVisible(false); }
		}

		// CheckboxGroup.setReadOnly forwards to each box, and Checkbox.setReadOnly
		// only marks the DOM input disabled -- the component stays enabled, so the
		// values still travel. That matters: for a chained example, step 1 is a
		// real upload and these boxes are the only place the databases come from.
		var databases = this.getComponent().queryById("databasesCheckboxGroup");
		if (databases && databases.setReadOnly) {
			databases.setReadOnly(true);
		}

		// Rebuilt rather than kept, so it sits above the panels of the example
		// that is loaded now: new panels are inserted at index 1 as well.
		var note = container.queryById("exampleFixedNote");
		if (note) { container.remove(note); }
		container.insert(1, {
			xtype: "box",
			itemId: "exampleFixedNote",
			/* 10px left and right, because .omicbox carries `margin: 5px 10px`:
			   the note sits in the same column as the panels it describes, and
			   with no left margin its blue bar started 10px left of every card
			   edge below it - the one painted edge in the column off the rail
			   the others share. */
			/* data-guides="ignore" for the same reason as the column title
			   above it: its rail-mates are the card edges below, which the
			   overlay's groups cannot offer it as company. */
			html: '<p data-guides="ignore" style="margin:0 10px 10px;padding:8px 12px;border-left:4px solid var(--pa-accent-blue);' +
				'background:rgba(38,132,255,0.08);font-size:12px;line-height:1.5;">' +
				'<b>This example dataset is fixed.</b> Its omics, organism and databases come from ' +
				'the server&rsquo;s catalogue' +
				(pipeline === "pathway-acquisition" ? "" : " for this pre-processing step") +
				', so the panels are read-only. <b>Reset</b> returns to an upload form; ' +
				'<b>Load another example</b> switches dataset.</p>'
		});
	};

	/**
	* This function is called when the user press the "Run Paintomics" button.
	* First we get all the RegionBasedOmic panels.
	* If there is one or more panel which contains to-be-processed BED files then
	* we send first those panels to server for processing "step1SubmitRegionBasedOmics".
	*
	* If there is not RegionBasedOmic panels or they contain already processed files
	* then we call to normal execution "step1OnFormSubmitHandler".
	*
	*/
	this.submitFormHandler = function() {
		var aux, omicBoxes;

		/* Which panels are in this submission is only knowable once the empty
		   ones are gone, so drop them first. A panel dropped afterwards is a
		   destroyed component still sitting in the pre-processing list, and
		   step1ComplexFormSubmitHandler throws on the first queryById of a
		   container that is no longer there. */
		this.dropEmptyOmicPanels();

		omicBoxes = this.getComponent().queryById("submittingPanelsContainer").query("container[cls=omicbox regionBasedOmic],[cls=omicbox miRNABasedOmic],[cls=omicbox moreBasedOmic]");
		for (var i = omicBoxes.length; i--;) {
			aux = omicBoxes[i].queryById("itemsContainer");
			if (aux === null || aux.isDisabled()) {
				omicBoxes.splice(i, 1);
			}
		}

		if (omicBoxes.length > 0) {
			this.controller.step1ComplexFormSubmitHandler(this, omicBoxes);
		} else {
			this.controller.step1OnFormSubmitHandler(this);
		}
	};
	/**
	* Removes the omic panels the user left empty, and returns the ones that stay.
	*
	* Step 1 opens with a Gene expression panel and a Metabolomics panel already
	* on the form, and adding a Region-based, miRNA or MORE panel is one click in
	* "Available omics". A panel with no file in it means "I did not want this
	* one", so it is taken off the form rather than reported -- and because it is
	* taken off the form, nothing downstream may treat it as part of the job.
	*
	* @returns {Array} the panels that carry a file
	*/
	this.dropEmptyOmicPanels = function() {
		var items, filled, i;

		items = this.getComponent().query("container[cls=omicbox], container[cls=omicbox regionBasedOmic],[cls=omicbox miRNABasedOmic],[cls=omicbox moreBasedOmic]");

		filled = [];
		for (i = 0; i < items.length; i++) {
			if (items[i].isEmpty() === true) {
				$(items[i].getEl().dom).find("a.deleteOmicBox").click();
			} else {
				filled.push(items[i]);
			}
		}

		return filled;
	};
	/**
	* This function checks the validity for each OmicSubmittingPanel
	*
	* Sets `formIsEmpty` as a side effect: true when the form was refused
	* because nothing was filled in, so the caller can say that instead of
	* sending the user looking for field errors that do not exist.
	*
	* @returns Boolean
	*/
	this.checkForm = function() {
		var filled, valid, i;

		/* Drop the empty panels BEFORE validating anything.

		   The two halves used to run the other way round -- validate every
		   panel, then delete the empty ones -- and disagreed. An empty panel
		   was counted as invalid and then deleted by the very next loop. The
		   plain omic panel hid it (its isValid() tested `this.isEmpty` -- the
		   method, always truthy -- so it validated nothing); the region, miRNA and MORE panels have no such guard, so
		   empty they mark their own file fields and return false. Those three
		   are also the only panels that route the submit through
		   step1ComplexFormSubmitHandler, whose refusal is the one that says
		   "please check the form errors" and offers Report error.

		   So: fill in Gene expression, add a Region-based panel, press Run --
		   the form was refused because of the region panel, the region panel
		   was deleted a moment later, and the user was left looking at one
		   clean box with nothing marked anywhere, told to check errors that no
		   longer existed. That is the 2026-08-26 report; reproduced in Chrome
		   with 0 fields marked invalid on the form it left behind. */
		filled = this.dropEmptyOmicPanels();

		this.formIsEmpty = (filled.length === 0);
		if (this.formIsEmpty) {
			return false;
		}

		/* isValid() first, so a wrong organism does not short-circuit the loop:
		   every panel is validated and marked in one pass. The dialog names the
		   first field, the rest are red on the page -- one round, not two. */
		valid = this.getComponent().queryById("speciesCombobox").isValid();
		for (i = 0; i < filled.length; i++) {
			valid = filled[i].isValid() && valid;
		}

		return valid;
	};
	/**
	* The first field on the form carrying an error, or null.
	*
	* "Please check the form errors" is only a direction if the reader can find
	* the error, and this form is three sections tall: the organism sits in
	* section 1 and the omic panels in section 3, so the field that refused the
	* submission is routinely off screen when the dialog opens over it. The
	* reporting user on 2026-08-26 had filled in a MORE panel at the bottom of
	* the page and never chosen an organism at the top -- nginx has her first
	* /organism_databases (the combo's change listener, once per page load)
	* landing 31 seconds AFTER the report -- so the one thing wrong with her
	* form was a field she had no reason to be looking at.
	*
	* Only visible fields count: a hidden one cannot be shown to anybody, and
	* naming it would be worse than saying nothing.
	*
	* @returns {Ext.form.field.Base|null}
	*/
	this.firstFormError = function() {
		return firstVisibleInvalidField(this.getComponent().query("field"));
	};

	//    this.showMyDataPanel = function () {
	//        this.controller.showMyDataPanelClickHandler(this);
	//    };

	this.initComponent = function() {
		var me = this;
		this.component = Ext.widget({
			xtype: "container",
			minHeight: 800,
			/* Vertical only. This was '10', which ExtJS writes as an inline
			   `padding: 10px` on the container's innerCt - and that horizontal
			   10px was one of two accidental insets between the page column and
			   the cards. Together with the 10px `.contentbox` carried in its own
			   margin, the cards started at x=76 while the header's wordmark
			   started at x=56: the logo did not line up with the block directly
			   beneath it, and neither did the right edge.

			   The gutter is --pa-gutter, applied once on #mainViewCenterPanel.
			   Anything added on top of it here is a second gutter that only some
			   of the page gets. */
			padding: '10 0',
			items: [
				{
				xtype: "box",
				cls: "toolbar secondTopToolbar",
				html:
				'<a class="button btn-danger btn-right" id="resetButton"><i class="fa fa-refresh"></i> Reset</a>' +
				'<a class="button btn-success btn-right" id="submitButton"><i class="fa fa-play"></i> Run PaintOmics</a>' +
				'<a class="button btn-secondary btn-right" id="exampleButton"><i class="fa fa-file-text-o"></i> Load example</a>'
			}, {
					xtype: 'box',
					cls: "contentbox",
					/* The page column is already capped and centred by --pa-page-max
					   on #mainViewCenterPanel. Capping the card again at 1300 left a
					   100px dead strip down the right of every block on this page,
					   so the three cards read as pushed to one side. */
					style: "margin-top:50px",
					html: '<div class="po-hero-section">' +
						'<div class="po-hero">' +
							'<div class="po-hero-text">' +
								/* No eyebrow badge and no version chip above the title.
								   "MULTI-OMICS INTEGRATION PLATFORM" set in uppercase restated
								   what the sentence directly below the title already says in
								   full, and the release number rendered in 36px accent beside
								   the product name read as half of the name rather than as a
								   build number. The version is chrome, not a headline: it still
								   ships, in the header bar, at the size a build number earns. */
								/* "AI" is the half of the name that is new in this release and
								   the half the whole page is about, so it is set in the blue the
								   application already uses for every AI surface. Same word, same
								   size; the colour is the only thing that separates it. */
								'<h1 class="po-hero-title">PaintOmics <span class="po-hero-ai">AI</span></h1>' +
								/* One sentence, same slot, same two lines as the one it replaces.

								   What stood here was a 24-word nominal phrase with no subject
								   and no verb - "Integrative visualization of multiple omic
								   datasets onto ... maps across ..." - which describes the
								   product the way a catalogue entry does rather than telling
								   anyone what it is for. This says the same three things in the
								   product's own verb, and says the useful one first. */
								'<p class="po-hero-desc">Paint every omic layer onto the pathway that explains it &mdash; across KEGG, Reactome and MapMan, for species from every biological kingdom.</p>' +
								/* The AI panel keeps its three lines. What changed inside them:

								   "Turns your ranked pathways into..." left the subject unsaid,
								   which read as though the page itself did it. The thing that
								   does it is an agent, and the three verbs are the three it
								   actually performs - it queries the job's own values through
								   its tools, plans and runs its own searches, and verifies each
								   citation in a sub-agent before using it. They were a comma
								   list inside the sentence; on their own line they can be read
								   at a glance, which is the only way three capabilities ever
								   are, and it costs no height because the sentence loses the
								   line they were taking up in it.

								   "A draft, for you to check" stays, on the sentence it
								   qualifies. It is the honest half of the claim and the hero is
								   worth less without it. */
								'<div class="po-hero-ai-highlight">' +
									'<div class="po-hero-ai-head"><span class="po-ai-icon">' + getAIMark() + '</span><strong>AI-powered pathway interpretation</strong></div>' +
									'<p>The <b>PaintOmics AI agent</b> writes an interpretation of your ranked pathways &mdash; a draft, for you to check.</p>' +
									'<ul class="po-hero-ai-does">' +
										'<li>queries your values</li>' +
										'<li>runs its own literature searches</li>' +
										'<li>verifies every citation</li>' +
									'</ul>' +
								'</div>' +
								/* Documentation was the primary button, which made reading about
								   the tool the loudest thing a first-time visitor could do. The
								   loudest thing should be using it: this fires the same example
								   chooser the toolbar's "Load example" opens - bound in the
								   boxready listener below, beside that button's own handler -
								   so a visitor with no data of their own can be looking at a
								   painted pathway without leaving the page.

								   The other three keep their destinations; they step down to
								   outline and to plain links, which is the order they are
								   actually wanted in. */
								'<div class="po-hero-actions">' +
									'<a href="javascript:void(0)" id="poHeroExampleButton" class="po-btn-primary">Load an example</a>' +
									'<a href="https://conesalab.github.io/PaintOmics/" target="_blank" class="po-btn-outline">Documentation</a>' +
									'<a href="https://github.com/ConesaLab/paintomics4/" target="_blank" class="po-btn-quiet">GitHub</a>' +
									'<a href="mailto:paintomicsai@gmail.com" class="po-btn-quiet">Contact</a>' +
									/* The abstract keeps its id and handler; it is a text link
									   beside the other three actions rather than a picture. */
								'</div>' +
							'</div>' +
							'<div class="po-hero-visual">' +
								PO_HERO_VIZ_SVG +
							'</div>' +
						'</div>' +
					'</div>'
			}, {
				xtype: 'box',
				cls: "contentbox po-about-section",
				style: "margin-top:4px",
				html: '<div id="about">' +
					'<h2>How it works</h2>' +
					'<div class="po-steps-grid">' +
						'<div class="po-step-card">' +
							/* The disc and the heading are one element. Loose, the disc sat
							   34px tall with 24px of air under it before any words - a
							   floating orange dot at the top of each column, reading as
							   ornament rather than as the number of the step it labels. On
							   one line it is what it says it is: "1. Upload and run". */
							'<div class="po-step-head">' +
								'<div class="po-step-number">1</div>' +
								// Not "Data uploading": the upload form's own section heading
								// two cards below is exactly that, and the two sat on one
								// screen saying the same words about different things. This
								// card is the whole of step 1, of which uploading is the
								// fourth of five actions it lists.
								// data-guides="ignore": the heading is led by the 26px disc,
								// so its text deliberately starts 37px right of the rail the
								// card's copy sits on. The disc is the thing on the rail.
								'<h3 data-guides="ignore">Upload and run</h3>' +
							'</div>' +
							/* Prose, not an <ol>. This card is numbered 1, and it used to
							   hold a list numbered 1 to 5 inside it - so the screen showed
							   a "1" containing a "1", and the two sibling cards beside it
							   were plain paragraphs, which made this one look like a
							   different kind of thing rather than the first of three.
							   Every fact the list carried is still here, in the order it
							   carried them, including both buttons.

							   "untick any you want to leave out" was not true of KEGG, which
							   is rendered `checked: true, disabled: true` and labelled "KEGG
							   (required)" - the server adds it regardless. Telling someone to
							   untick a box that cannot be unticked sends them looking for a
							   broken control, so the sentence below is careful to say which
							   databases can actually be unticked. */
							'<p>Choose your organism, then check the pathway databases: KEGG is always included, and every other database installed for it is ticked by default, so untick any of <em>those</em> you want to leave out. Decide whether to enable the AI interpretation, describing your experiment design if you do. Then upload your multi-omic data — or load an example (<a class="button btn-secondary btn-inline btn-small" href="javascript:void(0)"><i class="fa fa-file-text-o"></i> Load example</a>) to explore PaintOmics with a ready-made dataset — and click <a class="button btn-success btn-inline btn-small" href="javascript:void(0)"><i class="fa fa-play"></i> Run PaintOmics</a>.</p>' +
							'<div class="po-step-art">' + PO_STEP_ART_UPLOAD + '</div>' +
						'</div>' +
						'<div class="po-step-card">' +
							'<div class="po-step-head">' +
								'<div class="po-step-number">2</div>' +
								'<h3 data-guides="ignore">Identifier and name matching</h3>' +
							'</div>' +
							/* The opening sentence was 32 words to carry two facts: those
							   three databases are keyed on Entrez IDs, and PaintOmics does
							   the conversion for you. "requires ... for working with ...
							   so the tool will convert the names and identifiers from
							   different sources and databases in your input data" says
							   both, at length, and leads with a requirement the reader
							   never has to act on - the conversion is automatic, so the
							   useful half is the promise, not the constraint. 17 words
							   now, leading with what PaintOmics does; every term the
							   original used, Entrez included, is still here. */
							'<p>PaintOmics converts the identifiers in your files to the Entrez IDs that KEGG, Reactome and MapMan need. This screen shows the number of features successfully mapped and the data distribution used for pathway colouring. Metabolite name assignments are shown, and you can choose which one to keep when a name is ambiguous. Click <a class="button btn-success btn-inline btn-small" href="javascript:void(0)"><i class="fa fa-play"></i> Next step</a> when you are ready.</p>' +
							'<div class="po-step-art">' + PO_STEP_ART_MATCH + '</div>' +
						'</div>' +
						'<div class="po-step-card">' +
							'<div class="po-step-head">' +
								'<div class="po-step-number">3</div>' +
								'<h3 data-guides="ignore">Explore results</h3>' +
							'</div>' +
							'<p>You get a Pathways summary, a classification, a network and an enrichment analysis. Paint any of the listed pathways with <a href="javascript:void(0)" class="button btn-inline btn-small btn-paint" title="Paint this pathway"><i class="fa fa-paint-brush"></i></a>, or ask for an <b>AI-powered pathway interpretation</b> with <a href="javascript:void(0)" class="button btn-inline btn-small btn-ai">' + getAIMark() + ' AI Interpret</a>. Read more about these analyses in <a href="https://conesalab.github.io/PaintOmics/" target="_blank">our documentation</a>.</p>' +
							'<div class="po-step-art">' + PO_STEP_ART_EXPLORE + '</div>' +
						'</div>' +
					'</div>' +
					/* The "Video tutorials" block is gone. Both recordings are on
					   the channel that Resources > Paintomics tutorial video already
					   links from the header bar, so the landing page was a second
					   door to the same place - and they predate this interface, so
					   they show a product that no longer looks like this. The chip
					   styles and the click handler went with the markup rather than
					   being left behind as CSS and JS matching nothing. */
					/* The closing "Check the User guide ... email ... GitHub page"
					   line is gone. Its three links were the hero's three buttons
					   again, to the same three URLs: the user guide is the
					   Documentation button's address, the address in
					   the mailto is the Contact button's, and the GitHub page is
					   the GitHub button's repository. It restated the top of the
					   page at the bottom of it and added nothing in between.

					   Nothing is lost by dropping it: those three destinations are
					   also in the header bar, which is on screen at every scroll
					   position, and the hero row itself is one page-up away. */
				'</div>'
			}, {
				xtype: 'form',
				/* The card inset belongs on the panel, not on its body: ExtJS sizes a
				   body to the panel's content box and writes an explicit width onto
				   it, so .contentbox's own horizontal margin pushed this body 20px
				   past its panel and the upload card finished further right than the
				   two cards above it.

				   Horizontally that inset is now zero, because the thing it was
				   matching is gone. This read '0 10 0 10' to mirror the 10px
				   `.contentbox` used to carry in its own margin; that margin has been
				   removed in favour of the single page gutter on
				   #mainViewCenterPanel, so the 10 here became the last surviving
				   copy of it - and the visible symptom was "Data uploading" sitting
				   10px right of "How it works" in the card directly above, two
				   headings of the same rank on two different rails. */
				cls: "paStep1Form",
				margin: '0',
				bodyCls: "contentbox",
				layout: {type: 'vbox', align: 'stretch'},
				defaults: {labelAlign: "right", border: false},
				items: [
					{xtype: "box", flex: 1, html:'<h2>Data uploading</h2><h3>1. Organism selection </h3>'},
					/* Section 1: two controls, one row, on the same panel as the two
					   sections under it.

					   It used to be four bare form rows on the white card -- a 150px label
					   column, a 450px field, and then nothing at all from x=940 to the
					   card's right edge, while Section 2's decision column and Section 3's
					   "Works with" aside both filled that space. The first section of the
					   form was the only one with no surface under it and the only one that
					   left half its own card empty.

					   It carries no prose. A first draft explained what the organism
					   decides, how to search the picker, and how many organisms were
					   installed; the owner cut all of it. The section asks two questions
					   and the answers are two controls, so what fills the panel is the
					   controls themselves: the field takes the width it needs to show a
					   full "Genus species (common name)", and the databases take the rest
					   of the row as four equal chips. Nothing here is a paragraph.

					   Three columns, the same three the omics row under Section 3 has
					   always had: the control on the left, what you fill in in the
					   middle, what it means on the right. The widths are that row's --
					   250 for the control, 320 for the note -- so the whole form reads
					   across on one set of rails instead of each section inventing its
					   own. */
					{
						xtype: "container", cls: "po-form-panel po-organism-panel",
						layout: {type: 'hbox', align: 'stretch'},
						items: [{
							/* The control column. 250px, the width "Available omics" keeps
							   under Section 3 -- the row this form now takes its shape from:
							   what you pick from on the left, what you fill in in the middle,
							   what it means on the right. */
							/* 360, not 250. The field holds "Genus species (common name)"
							   -- "Mus musculus (house mouse)" is 27 characters and the longer
							   strain names run past 40 -- and at 250 the picker was truncating
							   the answer it had just been given. */
							xtype: "container", width: 360, layout: "anchor",
							items: [
								{
									xtype: 'organismcombo', fieldLabel: 'Organism', name: 'specie',
									/* Label on top, as "Experiment design (optional)" has it in
									   the panel below. Beside the field it spent 150px of the row
									   on one word, and every note under the field then carried a
									   hand-matched `margin-left: 196px` to line up with the field
									   rather than with the label. */
									labelAlign: 'top',
									anchor: '100%',
									itemId: "speciesCombobox",
									allowBlank: false,
									forceSelection: true,
									emptyText: 'Please choose an organism',
									displayField: 'name',
									valueField: 'value',
									queryMode: 'local',
									/* The database chips beside this field are a function of it.
									   `change` rather than `select`: setValue() fires change and
									   not select, and setExampleModeHandler sets the organism that
									   way. */
									listeners: {
										change: function() { me.applyDatabaseAvailability(); }
									},
									store: Ext.create('Ext.data.ArrayStore', {
										fields: ['name', 'value'],
										autoLoad: true,
										sortOnLoad: true,
										remoteSort: false,
										sorters: [{
									        property: 'name',
									        direction: 'ASC'
									    }],
										proxy: {
											type: 'ajax',
											url: SERVER_URL_GET_AVAILABLE_SPECIES,
											reader: {
												type: 'json',
												root: 'species',
												successProperty: 'success'
											}
										}
									})
								},
								{
									/* The request was a pink text link trailing an info tip:
									   "Not your organism? Request a new organism." -- eleven words
									   and a sentence of setup for a control, in a magenta used
									   nowhere else and written inline, where dark.css could not
									   reach it. It is an action, so it is shaped like one: the
									   same shape as "Draft this for me" in the panel below, quiet
									   until it is wanted. Keeps id="newOrganismRequest", which is
									   what opens the dialog (see the boxready handler below).

									   With a line beside it, as that button has: on its own under
									   the field it was a control with nothing to say what pressing
									   it does, and the owner read the difference between the two
									   rows as an oversight. It is: this is the only other optional
									   action on the form, and the two should read the same way.
									   The line is the dialog's own first sentence, which is the
									   fact that makes the button worth pressing -- the picker holds
									   what this server installed, and KEGG holds far more. */
									xtype: "box", cls: "po-organism-request",
									html: '<a href="javascript:void(0)" id="newOrganismRequest" class="po-ghost-action">' +
										'<i class="fa fa-plus"></i> Request an organism</a>' +
										'<span class="po-organism-request-hint">Any organism in KEGG can be added.</span>'
								},
								{
									/* The job's name, which the server takes from this field
									   (PathwayAcquisitionServlet: setName(jobDescription[:100])).
									   It was a visible "Enter a job description" box beside the
									   organism, one free-text field above another that asks for the
									   same sentence -- the experiment design below. Now hidden and
									   filled from that design's first line as it is typed, so the
									   form asks once and the job list still gets a name. */
									xtype: "hiddenfield",
									name: 'jobDescription'
								}
							]
						}, {
							xtype: "container", flex: 1, margin: '0 0 0 32', layout: "anchor",
							items: [
								{
									xtype: "box",
									/* <p>, not <div>: the alignment overlay measures where text
									   starts, and its selector deliberately excludes generic
									   containers -- so as a div this title was invisible to it and
									   the column it heads had one measurable member, the row of
									   checkbox labels. A lone member is reported as a stray, which
									   is how a column that is exactly where it should be came back
									   492px off the rail. */
									html: '<p class="po-aside-title">Pathway databases</p>'
								},
								{
									xtype: 'checkboxgroup',
									// Named so lockFormForExample can find it: in example mode
									// the databases are resolved on the server, not from here.
									itemId: "databasesCheckboxGroup",
									cls: "po-database-group",
									anchor: '100%',
									allowBlank: false,
									/* Two across, in the column the form gives its inputs. It
									   was four across a full-width row, which was right while the
									   databases had the whole panel to themselves; with the
									   information column beside them the row is ~550px, and four
									   columns there cut "MapMan not installed" in half. */
									/* One row. Four short names in two columns stacked into a
									   2x2 block with a gap down the middle; across, they read as
									   the one list they are, and Section 1 is a single row of
									   controls -- organism, then the databases it has. */
									columns: 4,
									disabled: false,
									/* The label is the title above the chips. */
									hideLabel: true,
									/* The boxes are fixed -- these are the four databases PaintOmics knows
									   how to draw a pathway from -- but which of them can be TICKED is not,
									   and is not knowable here: it depends on what this server installed for
									   the organism chosen beside it. So every optional box starts unticked
									   and disabled, and applyDatabaseAvailability turns on the ones that
									   /organism_databases reports for the selected organism.

									   They used to be permanently selectable, and two of the three were a
									   lie for nearly every organism: PathwayAcquisitionServlet has always
									   intersected the submitted selection with the databases the organism
									   actually has, so ticking MapMan for mouse or Reactome for tomato
									   changed nothing at all and said nothing about it. */
									items: [
											// Only for information, KEGG database is added always on server side
											{ boxLabel: 'KEGG <span class="po-db-tag">required</span>',
											  name: 'databases[]', inputValue: 'KEGG', checked: true, disabled: true },
											/* itemId as well as id: queryById finds either, but two ids on one page
											   is a global, and the region/miRNA/MORE flows can rebuild this form. */
											{ boxLabel: 'MapMan', name: 'databases[]', inputValue: 'MapMan',
											  checked: false, disabled: true,
											  itemId: 'mapmanDB', id: 'mapmanDB'},
											{ boxLabel: 'Reactome', name: 'databases[]', inputValue: 'Reactome',
											  checked: false, disabled: true,
											  itemId: 'reactomeDB', id: 'reactomeDB'},
											/* OmniPath ships no pathway diagrams, so its pathways open as an
											   interactive interaction network rather than a painted map. The
											   web service serves human, mouse and rat only, so for every
											   other organism this box stays disabled. */
											{ boxLabel: 'OmniPath', name: 'databases[]', inputValue: 'OmniPath',
											  checked: false, disabled: true,
											  itemId: 'omnipathDB', id: 'omnipathDB'},
									]
								},
								{
									/* Empty in the ordinary case, and that is the point: which
									   databases this organism has is on the chips above, so a
									   sentence repeating it was one more line to read every time.
									   describeDatabaseAvailability fills this only when something
									   is wrong -- when the installed list could not be read at all
									   -- which is the one state the chips cannot show. */
									xtype: "box", itemId: "databasesAvailabilityNote", html: ''
								}
							]
						}]
						/* Section 1 has no margin note. It carried one for a while --
						   "About this step", a sentence saying the organism decides which
						   databases and identifiers are available -- and the owner cut it as
						   useless, which it is: the databases beside the field already show
						   exactly that, one box per database, ticked when this organism has
						   it. A note that narrates the control next to it is a note nobody
						   needs to read twice. */
					},
					/*{
							xtype: "container",
							layout: "hbox",
							flex: 1,
							items: [{
								xtype: "textfield", 
								fieldLabel: "Enter a job description", 
								allowBlank: true,
								name: 'jobDescription',
								style: "margin: 20px 20px;",
								labelWidth: 150,
								width: 450,
								flex: 0
							}]
					}*/
					{   // AI Interpretation section
						xtype: "box", flex: 1,
						/* No "(optional)" any more: the interpretation is part of what this
						   server does with a job, and the one optional thing in the section --
						   the experiment design -- says so on its own label. The heading said
						   optional when it titled a consent checkbox. */
						html: '<h3>' + getAIMark() + ' 2. AI-powered pathway interpretation</h3>'
					},
					{
						xtype: "container", layout: {type: 'hbox', align: 'stretch'}, cls: "po-ai-section-body",
						/* The callout spans the full card but its prose is capped at a reading
						   measure, so as one column it filled half the box and left the other
						   half blank. Two columns give the explanation its measure and put the
						   two controls in the space the prose cannot use. */
						items: [{
							/* The one thing this section asks you to write, on the panel's own
							   left edge -- the same place Section 1 puts the organism field and
							   Section 3 its opening sentence. Every section of this form starts
							   with something you operate; the margin note to the right is what
							   explains it. */
							xtype: "container", flex: 1, layout: "anchor",
							items: [
								{
									/* Three things were wrong here, and the first hid the other two.
									
									   The placeholder read: e.g., "RNA-seq wildtype vs knockout mouse
									   liver, n=3 per group". ExtJS writes emptyText into a
									   placeholder="..." attribute, so the first embedded double quote
									   closed the attribute and everything after it was parsed as stray
									   markup -- on screen the hint was "e.g.," and nothing more. The
									   example is the useful half of a placeholder and nobody had ever
									   seen it. Curly quotes carry the same meaning through intact.
									
									   Then the label: labelWidth 150, right-aligned, inside a column
									   already narrowed by the callout beside it -- which left roughly
									   250px to compose prose in. It goes on top now and the input takes
									   the full column.
									
									   And 90px is three lines for free text describing an experiment.
									   It opens taller, and .po-exp-design lets it be dragged further.

									   96 now, not 132, plus growExpDesignToFit below. This field
									   is the tallest thing in the callout, so it alone sets the
									   section's height -- the left column runs out of content well
									   above it -- and for the whole time the form sits unfilled it
									   is 132px of empty box.

									   Sizing it to hold a draft was the obvious alternative and it
									   does not work: measured, a real draft off the STATegra
									   headers was 510 characters and wanted 153px, so 132 was
									   already too short for the case it was tall for. Picking a
									   height that fits the longest plausible draft would make the
									   idle form taller still, to no benefit.

									   So: short while empty -- 96 is the two-line placeholder plus
									   a line -- and grown to fit the moment there is something to
									   fit, which is the only moment the height is worth paying.
									   Dragging it still works; growing sets a height, not a cap. */
									xtype: "textarea", fieldLabel: "Experiment design (optional)",
									name: 'experimentDesign',
									/* 96, which is the two-line placeholder plus a line -- the
									   height the note above this config argues for and which the
									   field had drifted off. It sets the height of the whole
									   section while the form sits unfilled, and growExpDesignToFit
									   raises it the moment there is something to fit. */
									labelAlign: 'top', anchor: '100%', height: 96,
									cls: 'po-exp-design',
									emptyText: 'e.g. RNA-seq of wildtype vs knockout mouse liver, 3 replicates per group, sampled at 24 h',
									maxLength: 2000,
									listeners: {
										/* The one free-text field on the form also names the job:
										   its first line, cut to the 100 characters the server keeps,
										   goes into the hidden jobDescription field. On change rather
										   than on submit so whatever gathers the form's values -- the
										   submit, a draft, an example load -- finds it already set. */
										change: function(field, value) {
											var hidden = Ext.ComponentQuery.query('hiddenfield[name=jobDescription]')[0];
											if (!hidden) return;
											var firstLine = String(value || '').split(/\r?\n/)
												.map(function(l) { return l.trim(); })
												.filter(Boolean)[0] || '';
											hidden.setValue(firstLine.slice(0, 100));
										}
									}
								},
								/* A blank box asking a user to describe their own design in
								   prose is the least-filled field on this form, and the design
								   is already written down in the header row of the file they
								   are about to upload. This reads those headers in the browser
								   and asks the model to turn them into the sentence.

								   Gated twice on purpose. The button is disabled until the
								   consent box is ticked, because pressing it sends the column
								   names outward -- earlier than Run, which is the moment the
								   rest of the form treats as the boundary -- and the request
								   carries an explicit consent flag that the servlet checks, so
								   the guarantee does not rest on a disabled attribute.

								   The hint moved in here, from its own box above. It was two
								   full-width lines followed by a button on a third row, under a
								   field whose left column had already run out of content -- the
								   three tallest rows in the callout were the three carrying the
								   least. Beside the button it is one row, and it reads as a
								   caption for the control rather than a second paragraph.
								   Shortened to match: "Describe the comparison your data
								   represents. The interpretation uses it to say which direction
								   of change means what." said the same thing in two sentences,
								   and the placeholder in the field above already shows the
								   shape of an answer. */
								/* The consent, which is no longer a question this form asks.

								   It was a checkbox, in four different places over as many passes
								   (the right-hand column, the top-left of the panel, the far end of
								   the section heading, and finally here, under the field and above
								   the button it gated). The owner's call on 2026-08-21 was to stop
								   asking: the interpretation runs on hardware this project operates
								   -- an IIIA-CSIC gateway, in the EU, named on the panel beside this
								   field -- so the transfer the box was asking permission for is to
								   the same institution the data was uploaded to. What replaces it is
								   not silence: "Where your data goes" is now a heading in that
								   column, with an amber (!), the recipient printed under it, and the
								   full notice -- what is sent, what is never sent, GDPR Articles 5
								   and 9 -- one click away. Told rather than asked.

								   The field stays, as a hidden 'true', because everything downstream
								   is built on it and none of that should change: the servlet still
								   reads consent off the form (PathwayAcquisitionServlet:
								   setAIConsent), AIInterpretServlet still refuses a request whose
								   stored record says False, and Step 3 still shows the AI Interpret
								   button only for a job that carries it. Consent is still recorded
								   per job and still enforced per request -- what changed is who
								   decides it, not whether it is checked.

								   DEFAULT_AI_CONSENT_ENABLED is left in ServerConfiguration.js,
								   unread by this form. It is the switch to put the checkbox back. */
								{
									xtype: 'hiddenfield',
									name: 'aiConsent',
									value: 'true',
									listeners: {
										afterrender: function() {
											/* Names the recipient under "Where your data goes" in the
											   column beside this field, so it is on screen before anyone
											   opens the notice. The wiring hangs off this field because
											   it is the one component in the section that is guaranteed
											   to render whatever else the form is doing. */
											fillAIProvenance(document);
											setTimeout(function() {
												var icon = document.getElementById("aiGdprInfoIcon");
												if (!icon) return;
												icon.addEventListener("click", function(e) {
													e.preventDefault();
													e.stopPropagation();
													var existing = document.getElementById("aiGdprOverlay");
													if (existing) { existing.remove(); return; }
													var overlay = document.createElement("div");
													overlay.id = "aiGdprOverlay";
													overlay.className = "ai-gdpr-overlay";
													var disclaimer = document.createElement("div");
													disclaimer.id = "aiGdprDisclaimer";
													disclaimer.className = "ai-gdpr-disclaimer";
													disclaimer.innerHTML =
														'<div class="ai-gdpr-disclaimer-header">' +
														'  <strong>Where your data goes</strong>' +
														'  <button class="ai-gdpr-close" title="Close" aria-label="Close"><i class="fa fa-times"></i></button>' +
														'</div>' +
														'<div class="ai-gdpr-disclaimer-body">' +
																'  <p><strong>What this feature does:</strong> it sends a summary of your analysis to a large ' +
																'  language model, which returns a draft interpretation with citations. PaintOmics does not run ' +
																'  the model itself, so the summary leaves this server. Here is exactly what it is.</p>' +
																'  <p id="aiGdprWhere" class="ai-gdpr-where"></p>' +
																'  <div class="ai-gdpr-sent">' +
																'    <strong class="ai-gdpr-sent-title">\u26A0 What leaves this server:</strong>' +
																'    <ul style="margin:6px 0 0;padding-left:18px;">' +
																'      <li>Pathway names, identifiers and enrichment statistics (p-values)</li>' +
																'      <li>Names of the genes, proteins and metabolites matched in each pathway</li>' +
																'      <li><strong>The measured values of those features</strong>, with their condition and ' +
																'      timepoint labels &mdash; these are rows of the file you uploaded, not just summaries</li>' +
																'      <li>Your experiment design description, if you provided one</li>' +
																'    </ul>' +
																'    <p style="margin:8px 0 0;">Pathway and feature names are also sent to <strong>NCBI PubMed</strong> ' +
																'    and <strong>Europe PMC</strong> to find the literature the interpretation cites.</p>' +
																'  </div>' +
																'  <div class="ai-gdpr-notsent">' +
																'    <strong class="ai-gdpr-notsent-title">\u2705 What does not leave this server:</strong>' +
																'    <ul class="ai-gdpr-safe" style="margin:6px 0 0;padding-left:18px;">' +
																'      <li>The files you uploaded, as files</li>' +
																'      <li>Features that were not matched to one of the interpreted pathways</li>' +
																'      <li>Your login credentials or account information</li>' +
																'    </ul>' +
																'  </div>' +
																'  <hr>' +
																/* The same facts, in a register the owner asked for on 2026-08-21:
																   "be chill, it is hosted on the CSIC LLM service and we are good
																   people". The list below is unchanged in substance -- it is what
																   the GDPR says about genetic and health data, and leaving it out
																   would not make it less true -- but it is written as advice about
																   the data rather than as a prohibition on the reader. The red
																   \u26D4, the inline #c62828 and the "you formally confirm"
																   paragraph are gone: this notice is read by researchers deciding
																   what to upload, and shouting at them is not what makes them
																   careful. The same softening was applied to the AI section of
																   conditions.html, which the fine print links to. */
																'  <p><strong>What to leave out</strong></p>' +
																'  <p>Please upload only data you are free to process this way. In practice ' +
																'  that means leaving out:</p>' +
																'  <ul class="ai-gdpr-unsafe">' +
																'    <li>patient names, clinical record identifiers and other personally identifiable information;</li>' +
																'    <li>protected health information under HIPAA, the GDPR or an equivalent regulation;</li>' +
																'    <li>raw sequencing reads from identifiable human subjects;</li>' +
																'    <li>rare variants that could re-identify an individual;</li>' +
																'    <li>unpublished clinical trial data linked to patients.</li>' +
																'  </ul>' +
																'  <p>Under the <strong>GDPR</strong>, genetic and health data are a special category: ' +
																'  <strong>Article 9</strong> asks for an explicit lawful basis before they are processed, ' +
																'  and <strong>Article 5(1)(c)</strong> asks that you send only what the analysis needs.</p>' +
																'  <p id="aiGdprTransfer" class="ai-gdpr-transfer"></p>' +
																'  <p class="ai-gdpr-fineprint">' +
																'    You know your data and we do not, so what to upload is your decision. ' +
																'    The interpretation is a draft: read it, and check its citations, before ' +
																'    you rely on it. ' +
																'    For the full terms, see our <a href="conditions.html" target="_blank">Terms &amp; Conditions</a>.</p>' +
														'</div>';
													overlay.appendChild(disclaimer);
													document.body.appendChild(overlay);
													/* The notice does not exist until the icon is clicked, so its two
													   provenance paragraphs are filled here rather than at render. The
													   answer is already cached by this point in every realistic case. */
													fillAIProvenance(disclaimer);
													overlay.addEventListener("click", function(ev) {
														if (ev.target === overlay) { overlay.remove(); }
													});
													disclaimer.querySelector(".ai-gdpr-close").addEventListener("click", function() {
														overlay.remove();
													});
												});
											}, 200);
										}
									}
								},
								{
									xtype: "box", cls: 'po-exp-design-draft',
									html: '<button type="button" class="button btn-secondary btn-small po-exp-design-draft-btn" ' +
									      'id="expDesignDraftBtn" disabled>' + getAIMark() + ' Draft this for me</button>' +
									      '<span class="po-exp-design-hint">Names the job in your list and tells the interpretation which direction of change means what.</span>' +
									      '<span class="po-exp-design-draft-note" id="expDesignDraftNote"></span>',
									listeners: {
										afterrender: function() {
											var box = this;
											/* The consent checkbox and the omic panels are built by
											   other parts of this view, so the wiring waits a tick
											   for the form to finish rendering rather than reaching
											   up through a parent that may not exist yet. */
											setTimeout(function() { initExpDesignDraft(box); }, 200);
										}
									}
								}
							]
						}, {
							/* The information column, on the rail the Help panel under
							   Section 3 keeps: what the feature does, what it reads, and the
							   notice that says what leaves this server. It used to sit under
							   the consent box in a single right-hand column; as its own
							   column it is the same kind of thing as the Help note below and
							   is drawn as one. */
							/* The switch that turns the section on used to sit at the top of
							   this column; it is in the section heading now -- see there. */
							xtype: "container", cls: "po-note-col", width: 320, margin: '0 0 0 32', layout: "anchor",
							items: [
							{
								/* Named so fillAIProvenance can find it: the recipient's name
								   arrives from /ai_provider after this column has been measured,
								   and an hbox measures once. Same trap, same fix, as setNote's
								   updateLayout below -- see the note there. */
								xtype: "box", itemId: "aiWhatYouGetNote",
								html: '<div class="po-info-note">' +
									'<h5><i class="fa fa-info-circle"></i> What you get</h5>' +
									'<p class="ai-intro-copy po-ai-oneliner">A cited draft of what your results mean, written by the <b>PaintOmics AI agent</b> for you to check.</p>' +
									/* The three assurances as a sentence, not as a list.
									   They were <li>s in the .po-spec-list that Section 3 uses for
									   its formats -- an inline run separated by middots, which is
									   the right shape for nine short names on one wide line and the
									   wrong one here: in a 302px column each item wrapped onto a
									   line of its own, so the note ended in three unpunctuated
									   fragments stacked under a paragraph, with no separators
									   visible to say they were a list at all. The owner's read was
									   that it is "not good", and a list that renders as three orphan
									   lines is not a list. Prose is what this column is made of. */
									'<p class="po-ai-method">It searches PubMed and Europe PMC, reads the values you uploaded, and verifies every citation it prints.</p>' +
									/* Where the data goes, on the panel rather than only behind a
									   link. It read "Data privacy" -- a label for a notice, which
									   tells a reader the notice exists and nothing about their own
									   data. The (!) and the heading say there is something here to
									   read before enabling the feature, and #aiProviderInline (filled
									   by fillAIProvenance from /ai_provider) names the actual
									   recipient this server is configured to use, so the one fact
									   that matters is on screen without a click. The full notice --
									   what is sent, what is never sent, the GDPR articles -- is still
									   one click away.

									   Built as a second <h5> and its paragraph, the same pair as
									   "What you get" above it, rather than as one line with the icon
									   inline. Inline, the icon pushed the sentence 15.8px off the
									   column's text rail and the alignment overlay was right to
									   report it: the mark was on the rail and the words were not.
									   As a heading the icon is a flex item, the note reads as two
									   labelled blocks, and the amber tells the second from the
									   first. */
									'<h5 class="po-ai-privacy-head"><i class="fa fa-exclamation-circle"></i> ' +
									'<a href="javascript:void(0)" id="aiGdprInfoIcon" title="What is sent, to whom, and where it runs">Where your data goes</a></h5>' +
									'<p id="aiProviderInline" class="po-ai-provider-inline"></p>' +
									'</div>'
							}]
						}]
					},
					{
						xtype: "box",
						// The mark leads the heading exactly as it does on Section 2's,
						// so main.css's `div.contentbox h3 > svg.po-ai-mark` rule sizes
						// and places it, and the two AI headings on the form match.
						html: '<h3>' + getAIMark() + ' 3. Choose the files to upload</h3>'
					},
					{
						/* The standing offer, stated before any file is picked: the
						   upload step accepts files as they come out of the tools
						   people use, and the AI conversion is how.

						   On the same blue panel as the Section 2 callout (the owner
						   asked for it back once the row no longer carried a second
						   checkbox): the two AI surfaces on the form share one surface.
						   Conversion needs no box -- pressing Convert on a file is the
						   act -- so this panel only says what happens, in the
						   interpretation's own type (.ai-intro-copy), over the one line
						   of formats it handles (styles in inputformat.css). */
						xtype: "box",
						cls: "po-ai-section-body po-upload-ai",
						html:
							/* The lead runs into its own sentence rather than standing over it.
							   As two paragraphs in a 839px column this panel was 194px tall and
							   three of its four rows were empty on the left: the copy stopped at
							   y=712 and the panel ran to y=799, held open by a 320px column of
							   format names beside it. The owner's read was that it is too big for
							   what it says, and it was -- the panel is an aside, not a hero.

							   So the two columns are gone: the sentence takes the full width of
							   the panel and the formats sit under it as one line of specification.
							   Cut to one line as well, on the owner's second pass: "each file is
							   checked the moment you pick it" is the mechanism, and the sentence
							   only has to make the offer. Two rows, 194px down to 90. */
							'<p class="ai-intro-copy po-upload-ai-lead"><b>Bring your files as they are.</b> ' +
							'If a file is not in PaintOmics’ format, the <b>PaintOmics AI agent</b> offers to ' +
							'convert it in your browser and shows you the result.</p>' +
							/* The specification line: what wrote the file, and what the file is.

							   Two labelled rows in a 320px column, under an uppercase "Works with"
							   title, cost five lines and 162px of panel height. On one line they
							   cost 19px, and the two labels do the work the title was doing -- the
							   names are already legible as answers to "what does it accept?".

							   The file types are the ones the reader and the converter actually
							   admit: format-panel.js treats /\.(xlsx|xlsm|xls|ods)$/ as a workbook
							   to convert, and everything else is read as delimited text. */
							'<div class="po-upload-ai-specs">' +
								/* Each label and its names are wrapped together (a <div> inside a <dl> is
								   what HTML5 has for exactly this), so that when the row is too narrow for
								   both groups it breaks between them. Flat, the dl broke wherever it ran
								   out of room -- which on a 1512px window was between "File types" and the
								   file types. */
								'<dl class="po-works-with">' +
									'<div class="po-works-with-group">' +
										'<dt>Software</dt>' +
										'<dd><ul class="po-spec-list po-upload-ai-formats">' +
											'<li>DESeq2</li><li>edgeR</li><li>limma</li><li>MaxQuant</li>' +
											'<li>Spectronaut</li><li>DIA-NN</li><li>MetaboLights MAF</li>' +
										'</ul></dd>' +
									'</div>' +
									'<div class="po-works-with-group">' +
										'<dt>File types</dt>' +
										'<dd><ul class="po-spec-list">' +
											'<li>Excel (.xlsx, .xlsm, .xls)</li><li>OpenDocument (.ods)</li>' +
											'<li>CSV</li><li>TSV</li><li>plain text</li>' +
										'</ul></dd>' +
									'</div>' +
								'</dl>' +
								/* The example dataset, at the end of the line that says which files
								   this step takes -- because that is the question it answers: it is
								   the file to bring when you have not got one yet. It was floated
								   into the right edge of the section heading, a filled button
								   hanging off a title, attached to nothing and the only control on
								   the form outside a panel. Quiet, and shaped like the other
								   optional action on this form ("Request an organism").

								   Served from the manifest rather than a checked-in zip, so this
								   hands over the datasets "Load example" actually offers. The old
								   static file was built in 2017 and shared no filename with any
								   current scenario. */
								'<a class="po-ghost-action po-download-example" href="' + SERVER_URL_EXAMPLE_DATASETS_DOWNLOAD + '">' +
									'<i class="fa fa-download"></i> Download example data</a>' +
							'</div>'
					},
					{
						xtype: "container",
						cls: "po-step1-omics-row",
						/* The three columns were 250 + max 600 + max 300, which on a
						   1360px card left ~190px of the row unclaimed and stopped the
						   block short of the card's right edge.

						   Deliberately not align:'stretch': the selected-omic panels
						   carry flex:1, so a stretched column hands them the leftover
						   height and each omic's coloured header grows a 50px band of
						   empty colour under its title. */
						layout: {type: 'hbox'},
						items: [{
							xtype: "box",
							id: "availableOmicsContainer",
							minHeight: 400,
							width: 250,
							/* No padding on the outer side: the row already sits on the card
							   inset, so the drag sources start where the headings do. */
							padding: "10 0",
							html: '<h2 class="po-omics-col-title">Available omics</h2>' +
							'<div class="availableOmicsBox" title="geneexpression"><h4><a href="javascript:void(0)"><i class="fa fa-plus-circle"></i></a> Gene expression</h4></div>' +
							'<div class="availableOmicsBox" title="metabolomics"><h4><a href="javascript:void(0)"><i class="fa fa-plus-circle"></i></a> Metabolomics</h4></div>' +
							'<div class="availableOmicsBox" title="proteomics"><h4><a href="javascript:void(0)"><i class="fa fa-plus-circle"></i></a> Proteomics</h4></div>' +
							'<div class="availableOmicsBox" title="regulatoryomic"><h4><a href="javascript:void(0)"><i class="fa fa-plus-circle"></i></a> Regulatory Omic</h4></div>' +
							'<div class="availableOmicsBox" title="bedbasedomic"><h4><a href="javascript:void(0)"><i class="fa fa-plus-circle"></i></a> Region-based omic</h4></div>' +
							'<div class="availableOmicsBox" title="otheromic"><h4><a href="javascript:void(0)"><i class="fa fa-plus-circle"></i></a> Other omics</h4></div>'
						}, {
							xtype: "container",
							id: "submittingPanelsContainer",
							minHeight: 150,
							minWidth: 200,
							margin: 10,
							flex: 2,
							layout: {type: 'vbox',align: "stretch"},
							items: [
								// data-guides="ignore": the title's 10px padding lands its type
								// on the omic cards' edges below - a relationship the overlay's
								// column model cannot see (the cards stand as their own groups),
								// so measured raw it reports 270px off the form rail while
								// sitting exactly where it was tuned to sit.
								{xtype: 'box',html: '<h2 class="po-omics-col-title" data-guides="ignore">Selected omics</h2>'},
								{xtype: 'box',html: '<p class="dragHerePanel">Drag and drop here your selected <i>omics</i></p>'}
							]
						},
				   		{
							xtype: "container",
							id: "additionalInfoContainer",
							minHeight: 150,
							/* 302 wide and held 20px off the card edge, which is exactly
							   where Section 2's "What you get" note lands: that column is
							   320px inside a panel with 20px of padding, so its text runs
							   1049 -> 1351 on a 1512px window. This row has no panel, so
							   without the right margin it ran to the card edge and the two
							   notes -- the same kind of thing, in the same column of the
							   form, one directly under the other -- started and ended 20px
							   apart. The owner spotted it; the numbers agree.

							   It was 342 with no right margin, on the reasoning that the row
							   takes back the padding its neighbours give up. That is the
							   right rule for the drag columns, which are panels of their own
							   and do run to the edge; it is the wrong one for a note, which
							   is text and belongs on the text rail.

							   Before that it was 422, the same arithmetic against a 400px
							   right column, and the owner's read then was that the help was
							   taking room the file fields needed -- so every pixel it gives
							   up goes to the middle column, which is `flex: 2`. */
							width: 302,
							/* Left is the gutter, right is the panels' inner rail. */
							margin: "10 20 10 10",
							layout: {type: 'vbox',align: "stretch"},
							items: [
								{xtype: 'box',html: '<div class="content"><h5><i class="fa fa-info-circle"></i> Help</h5><p>Drag <i>omics</i> from <b>Available omics</b> to <b>Selected omics</b>, or click the <i class="fa fa-plus-circle"></i> button.</p><p>Remove any you do not need with <span class="po-nowrap"><i class="fa fa-trash"></i>.</span></p><p><span class="po-required-mark" role="img" aria-label="asterisk">*</span> marks the files the job needs; the rest are optional.</p><p>Files are checked as you pick them; the <b>PaintOmics AI agent</b> converts any that are not in PaintOmics’ format.</p><p>When you are done, click <b>Run PaintOmics</b> in the top-right corner.</p></div>'}
							]
						}]
					}					
				]
			}],
			listeners: {
				boxready: function() {
					$("#submitButton").click(function() {
						me.submitFormHandler();
					});
					$("#exampleButton").click(function() {
						me.showExampleChooser();
					});
					/* The hero's primary action. Same chooser as the toolbar button
					   above, deliberately: the hero is where a first-time visitor
					   looks, and "Load example" in the top-right corner is not
					   something they have been told to look for yet. */
					$("#poHeroExampleButton").click(function() {
						me.showExampleChooser();
					});
					$("#resetButton").click(function() {
						me.resetViewHandler();
					});
					$("#addOtherDataButton").click(function() {
						me.addNewOmicSubmittingPanel();
					});
					$("#newOrganismRequest").click(function() {
						application.getController("DataManagementController").requestNewSpecieHandler();
					});

					$(".availableOmicsBox a").click(function(){
						var type = $(this).parents(".availableOmicsBox").first().attr("title");
						me.addNewOmicSubmittingPanel(type);
					});
			


					var containers = [$("#availableOmicsContainer")[0], $("#submittingPanelsContainer-targetEl")[0]];

					//INITIALIZE THE DRAG AND DROP
					dragula(containers, {
						moves: function(el, container, handle) {
							// elements are always draggable by default
							return el.tagName !== "H5" && container.id !== "submittingPanelsContainer-targetEl";
						}
					}).on("drop", function(el, container, source) {
						if (container.id === "submittingPanelsContainer-targetEl") {
							var type = $(el).attr("title");
							me.addNewOmicSubmittingPanel(type);
						}
						this.cancel(true);
					});


					me.addNewOmicSubmittingPanel("metabolomics");
					me.addNewOmicSubmittingPanel("geneexpression");

					/* ...and then start at the top, the way a page does.
					   ExtJS gives every panel it adds `tabindex="-1"` and focuses it,
					   and a browser scrolls a focused element into view - so the
					   application opened 1218px down, with the whole hero and "How it
					   works" already above the fold before the first paint.
					   Measured both halves of that: focusing one of these boxes with
					   the attribute present moves the scroller to 1278, and the same
					   focus with it removed moves it to 0. So the attribute is the
					   cause, and removing it is the fix rather than scrolling back
					   afterwards - a correction would race the focus, which lands on
					   its own schedule and not in this handler.
					   Only these two, and only here. When the user adds an omic later
					   that panel keeps its tabindex, and scrolling to the thing they
					   just asked for is the right behaviour. The fields inside are
					   untouched and still take focus normally; it is the layout
					   container that had no business holding it. */
					$(".omicbox").removeAttr("tabindex");
					$("#mainViewCenterPanel").scrollTop(0);
				},
				beforedestroy: function() {
					me.getModel().deleteObserver(me);
				}
			}
		});

		return this.component;
	};

	return this;
}
PA_Step1JobView.prototype = new View();

function DefaultSubmittingPanel(nElem, options) {
	/*********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.getOmicName = function() {
		return this.omicName;
	};
	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.toogleContent = function(alternativeComponent="itemsContainerAlt") {
		var component = this.getComponent().queryById(alternativeComponent);
		var isVisible = component.isVisible();
		component.setVisible(!isVisible);
		component.setDisabled(isVisible);

		component = this.getComponent().queryById("itemsContainer");
		if (component) {
			component.setVisible(isVisible);
			component.setDisabled(!isVisible);
		}
		return this;
	};

	this.removeOmicSubmittingPanel = function() {
		this.getParent().removeOmicSubmittingPanel(this);
		return this;
	};
}
DefaultSubmittingPanel.prototype = new View;

function OmicSubmittingPanel(nElem, options) {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	options = (options || {});

	this.title = "Other data type";
	this.namePrefix = "omic" + nElem;
	this.omicName = "";
	this.mapTo = "Gene";
	this.fileType = null;
	this.relevantFileType = null;
	this.featureEnrichment = "genes";

	this.class = "otherFileBox";

	/*IF THE TYPE WAS SPECIFIED (e.g. gene_expression)*/
	if (options.type !== undefined) {
		//TODO CAPITALIZE THE FIRST LETTER
		this.omicName = options.type;
		this.title = options.type;

		if (['Metabolomics'].indexOf(options.type) !== -1) {
			this.mapTo = "Compound";
		}

		this.fileType = options.fileType;
		this.relevantFileType = options.relevantFileType;
		this.type = this.title.replace(" ", "").toLowerCase();
		this.class = this.type + "FileBox";
		this.featureEnrichment = options.featureEnrichment || "genes";
	}
	/*********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/

	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* Display-only: the fields are disabled and the server resolves the actual
	* files from the manifest. Only the labels are set here.
	*
	* This used to render `"example/" + this.type + "_example.tab"`, which named
	* a file that has never existed in any release -- so the form taught a
	* filename convention nothing on the server would recognise if a user copied
	* it. Naming the omic instead is honest about what is going on: the specific
	* files belong to the chosen dataset, not to this panel.
	*
	* @param {string} [omicName] the manifest's name for this omic.
	*/
	/* hasDesign: the scenario ships an experimental design for this omic
	   (the catalogue's designOmics). Every Metabolomics example used to get
	   the "experimental design" label, including the ones that carry none
	   and then run the binomial test. */
	this.setExampleMode = function(omicName, hasDesign){
		var component = this.getComponent();
		setExampleLabel(component.queryById("mainFileSelector"), "values");
		setExampleLabel(component.queryById("secondaryFileSelector"),
			"relevant features");
		var design = component.queryById("designFileSelector");
		var note = component.queryById("designFileNote");
		if (design && !design.hidden) {
			if (hasDesign) {
				setExampleLabel(design, "experimental design");
			} else {
				design.setVisible(false);
				if (note) note.setVisible(false);
			}
		}
	};
	/*********************************************************************
	* COMPONENT DECLARATION
	***********************************************************************/
	this.initComponent = function() {
		var me = this;

		this.component = Ext.widget({
			xtype: "container", flex: 1, cls: "omicbox",
			type: me.type, layout: {align: 'stretch',type: 'vbox'},
			items: [
				{
/* No `flex`. The title is a fixed 44px bar (.omicboxTitle min-height), and a
					   flexed item in a vbox is where the layout puts any height the card has
					   left over -- or, when the card is SHORT of its contents, where it takes
					   the shortfall from. That is what a short MORE card did to this heading:
					   allocated 0px, painted at 44px by the CSS min-height, sitting on top of
					   the first section. Nothing else in the card is flexed, so slack now
					   simply stays at the bottom where it is invisible. */
					xtype: "box", cls: "omicboxTitle " + this.class, html:
					'<h4>' +
					' <a class="deleteOmicBox" href="javascript:void(0)" style="margin: 0; float:right;  padding-right: 15px;"><i class="fa fa-trash"></i></a>' +
					this.title +
					'</h4>'
				}, {
					xtype: "container",
					layout: {align: 'stretch',type: 'vbox'},
					padding: 10,
					/* No maxWidth here, nor in the other panel types that repeat this
					   block: the "Selected omics" column is ~700px wide, so the 500px cap
					   this used to carry stopped every row well short of its own box and
					   left a band of dead white down the right of each omic panel. The
					   vbox is align:'stretch', so without the cap each field fills the
					   panel it is in. */
					defaults: {
						labelAlign: "right",
						labelWidth: 150,
						maxLength: 100
					},
					items: [
						{
							xtype: 'combo',
							fieldLabel: 'Omic Name',
							name: this.namePrefix + '_omic_name',
							value: this.omicName,
							hidden: this.omicName !== "",
							itemId: "omicNameField",
							/* A typed space is not an omic name; it would be posted as the omic's label. */
							allowOnlyWhitespace: false,
							displayField: 'name',
							valueField: 'name',
							emptyText: 'Type or choose the omic type',
							editable: true,
							allowBlank: false,
							queryMode: 'local',
							store: Ext.create('Ext.data.ArrayStore', {
								fields: ['name'],
								autoLoad: true,
								proxy: {
									type: 'ajax',
									url: 'resources/data/all_omics.json',
									reader: {
										type: 'json',
										root: 'omics',
										successProperty: 'success'
									}
								}
							})
						}, {
							xtype: "myFilesSelectorButton",
							requiredTag: "required",
							fieldLabel: 'Data file',
							namePrefix: this.namePrefix,
							itemId: "mainFileSelector",
							helpTip: "Upload the feature quantification file (Gene expression, proteomics quantification,...) or choose it from your data folder."
						}, {
							xtype: 'combo', itemId: "fileTypeSelector",
							fieldLabel: 'File Type', emptyText: 'Type or choose the file type',
							/* A sixteen-row static list: the default remote mode re-fetched
							   file_types.json on every dropdown open and every typed query. */
							queryMode: 'local',
							/* A typed space is not a file type; it would be posted as the
							   file's label. */
							allowOnlyWhitespace: false,
							name: this.namePrefix + '_file_type',
							hidden: this.omicName !== "",
							displayField: 'name', valueField: 'name',
							editable: true, allowBlank: false,
							value: (this.fileType !== null) ? this.fileType : null,
							store: Ext.create('Ext.data.ArrayStore', {
								fields: ['name', 'type'],
								autoLoad: true,
								proxy: {
									type: 'ajax',
									url: 'resources/data/file_types.json',
									reader: {type: 'json', root: 'types', successProperty: 'success'}
								},
								filterOnLoad:true,
								filters: [{property: 'type', value : 'data'}]
							}),
							helpTip: "Specify the type of data for uploaded file (Gene Expression file, Proteomic quatification,...)."
						}, {
							xtype: "myFilesSelectorButton",
							requiredTag: "optional",
							fieldLabel: 'Relevant features file',
							namePrefix: this.namePrefix + '_relevant',
							itemId: "secondaryFileSelector",
							helpTip: "Upload the list of relevant features (relevant genes, relevant proteins,...)."
						}, {
							xtype: 'combo', itemId: "relevantFileTypeSelector",
							fieldLabel: 'File Type', emptyText: 'Type or choose the file type',
							queryMode: 'local',
							name: this.namePrefix + '_relevant_file_type',
							hidden: this.omicName !== "",
							displayField: 'name', valueField: 'name',
							editable: true, allowBlank: true,
							value: (this.relevantFileType !== null) ? this.relevantFileType : null,
							/* ExtJS validates on blur as well as on submit. With blank allowed
							   (the file is optional), its validation would clear the mark
							   isValid() puts on this combo the moment the combo was focused and
							   left, while the panel still refused. Same rule as isValid(): a
							   type is needed exactly when a relevant file is attached. */
							validator: function(value) {
								var file = me.getComponent().queryById("secondaryFileSelector");
								return (file && file.getValue() !== "" && Ext.isEmpty(Ext.String.trim(value || ""))) ? "Please, specify a File type." : true;
							},
							store: Ext.create('Ext.data.ArrayStore', {
								fields: ['name', 'type'],
								autoLoad: true,
								proxy: {
									type: 'ajax',
									url: 'resources/data/file_types.json',
									reader: {type: 'json', root: 'types', successProperty: 'success'}
								},
								filterOnLoad:true,
								filters: [{property: 'type', value : 'list'}]
							}),
							helpTip: "Specify the type of data for uploaded file (Relevant Genes list, Relevant proteins list,...)."
						}, {
							/* Metabolomics only. A values file with one column per
							   SAMPLE plus this design (column -> condition) is what
							   lets the class activity test run on the replicates:
							   it asks whether a chemical class responds at all, on
							   the data's own noise, instead of whether it is more
							   changed than the rest of a panel picked to change.
							   Without it the test is a binomial on the relevant
							   list against the significance threshold used to
							   build that list. Optional, and the panel is still
							   valid without it. */
							xtype: "myFilesSelectorButton",
							requiredTag: "optional",
							fieldLabel: 'Experimental design',
							namePrefix: this.namePrefix + '_design',
							itemId: "designFileSelector",
							hidden: this.mapTo !== "Compound",
							helpTip: "Optional. A two-column file mapping each sample column of the data file to its condition " +
								"(e.g. Ctr_0H_B1<tab>Ctr_0H), or a MORE-style 0/1 design matrix. Upload it together with a " +
								"data file that has one column per sample."
						}, {
							xtype: 'box',
							itemId: "designFileNote",
							hidden: this.mapTo !== "Compound",
							/* data-guides="ignore": the note sits on the INPUT rail (150px
							   in, under the field it annotates), which the overlay lists as a
							   rail but judges this paragraph against the label column. */
							html: '<p class="paDesignNote" data-guides="ignore">Optional &mdash; runs the class test on your replicates.</p>'
						}, {
							xtype: 'combo',
							fieldLabel: 'Can be mapped to',
							name: this.namePrefix + '_match_type',
							hidden: this.omicName !== "",
							itemId: "mapToSelector",
							displayField: 'name', valueField: 'value',
							emptyText: 'Choose the file type',
							value: this.mapTo,
							editable: false,
							allowBlank: false,
							listeners: {
								/* The design file is a compound omic's. `hidden` above was read
								   once at build time, so a panel switched to Metabolites afterwards
								   never showed it and a second compound omic could carry no design. */
								change: function (combo, value) {
									var owner = combo.ownerCt;
									var isCompound = String(value || "").toLowerCase() === "compound";
									["designFileSelector", "designFileNote"].forEach(function (id) {
										var item = owner && owner.queryById(id);
										if (item) item.setVisible(isCompound);
									});
								}
							},
							store: Ext.create('Ext.data.ArrayStore', {
								fields: ['name', 'value'],
								data: [
									['Genes', 'gene'],
									['Metabolites', 'compound']
								]
							}),
							helpTip: "Defines whether the data can be assigned to Genes or to Metabolites, for example  the values of concentration for proteins that can be mapped to the corresponding codifying gene."
						},
						{
							xtype: 'combo',
							fieldLabel: 'Enrichment type',
							name: this.namePrefix + '_enrichment',
							hidden: this.omicName !== "",
							value: this.featureEnrichment.toString(),
							displayField: 'name', valueField: 'value',
							editable: false,
							allowBlank: false,
							store: Ext.create('Ext.data.ArrayStore', {
								fields: ['name', 'value'],
								data: [
									['Genes', 'genes'],
									['Features', 'features']
								]
							}),
							helpTip: "Define how the Fisher contingency table must be done: counting genes or features (i.e: microRNA, proteins...)."
						}
					]
				}
			],
			isValid: function() {
				var valid = true;

				/* isEmpty() -- the call. `this.isEmpty` alone was the method
				   reference, always truthy, so this returned true for every
				   plain panel and none of the checks below ever ran. */
				if (this.isEmpty()) {
					return true;
				}

				/* ExtJS's own rules first -- maxLength, allowBlank, a validator -- as
				   the Region-based, miRNA and MORE panels do, so they refuse through
				   checkForm() (the field named and scrolled into view) rather than
				   through the failure handler after submit() validates again. */
				valid = validateAllFields(this) && valid;

				/* Ext.isEmpty, not === "": an omic name typed and then deleted leaves
				   the combo's value null, which === "" let through and ExtJS's own
				   allowBlank then refused with no field named. */
				if (Ext.isEmpty(Ext.String.trim(this.queryById("omicNameField").getValue() || ""))) {
					valid = false;
					this.queryById("omicNameField").markInvalid("Please, specify a Omic Name.");
				}
				if (this.queryById("mainFileSelector").getValue() === "") {
					valid = false;
					this.queryById("mainFileSelector").markInvalid("Please, provide a Data file.");
				}
				if (this.queryById("fileTypeSelector").getValue() === null) {
					valid = false;
					this.queryById("fileTypeSelector").markInvalid("Please, specify a File type.");
				}
				if (this.queryById("secondaryFileSelector").getValue() !== "" && this.queryById("relevantFileTypeSelector").getValue() === null) {
					valid = false;
					this.queryById("relevantFileTypeSelector").markInvalid("Please, specify a File type.");
				}
				if (this.queryById("mapToSelector").getValue() === null) {
					valid = false;
					this.queryById("mapToSelector").markInvalid("Please, specify a this field.");
				}

				return valid;
			},
			isEmpty: function() {
				return (this.queryById("secondaryFileSelector").getValue() === "" && this.queryById("mainFileSelector").getValue() === "");
			},
			listeners: {
				boxready: function() {
					initializeTooltips(".helpTip");

					$(this.getEl().dom).find("a.deleteOmicBox").click(function() {
						me.removeOmicSubmittingPanel();
					});
				}
			}
		});

		return this.component;
	};
	return this;
}
OmicSubmittingPanel.prototype = new DefaultSubmittingPanel;

function RegionBasedOmicSubmittingPanel(nElem, options) {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	options = (options || {});

	this.title = "Region-based omic";
	this.namePrefix = "omic" + nElem;
	this.omicName = "";
	this.mapTo = "Gene";
	this.fileType = null;
	this.relevantFileType = null;
	this.featureEnrichment = "genes";

	this.allowToogle = options.allowToogle !== false;
	this.removable = options.removable !== false;

	this.class = "bedbasedFileBox";

	/*IF THE TYPE WAS SPECIFIED (e.g. gene_expression)*/
	if (options.type !== undefined) {
		//TODO CAPITALIZE THE FIRST LETTER
		this.omicName = options.type;
		this.title = options.type;

		this.fileType = options.fileType;
		this.relevantFileType = options.relevantFileType;
		this.type = this.title.replace(" ", "").toLowerCase();
		this.class = this.type + "FileBox";
	}
	/*********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/

	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* Display-only example mode. The server resolves the real files from the
	* manifest, so only labels are set here.
	*
	* The names are no longer hardcoded. They used to be literal paths
	* ("example/dnase_unmapped_values.tab"), which was already the second
	* attempt -- before that they were built from `this.type`, and the
	* Regions2Genes tool constructs this panel with no type, so the form
	* displayed "example/undefined_example.tab". Taking them from the chosen
	* scenario means they cannot go stale when the files move again.
	*
	* @param {Object} [scenario] the entry from /example_datasets.
	*/
	this.setExampleMode = function(scenario){
		var component = this.getComponent();
		//component.queryById("toogleMapRegions").setVisible(false);

		component = component.queryById("itemsContainer");

		var omicName = (scenario && scenario.omicNames && scenario.omicNames[0]) ||
			"Region-based omic";

		setExampleLabel(component.queryById("mainFileSelector"),
			"region values");
		setExampleLabel(component.queryById("secondaryFileSelector"),
			"relevant regions");
		setExampleLabel(component.queryById("tertiaryFileSelector"),
			"genome annotation (GTF)");

		var field = component.queryById("omicNameField");
		field.setValue(omicName);
		field.setDisabled(true);

		var otherFields = ["distanceField", "tssDistanceField", "promoterDistanceField", "geneAreaPercentageField", "regionAreaPercentageField", "gtfTagField", "summarizationMethodField", "reportSelector1","reportSelector2"];
		for(var i in otherFields){
			field = component.queryById(otherFields[i]);
			field.setReadOnly(true);
		}
	};
	this.setContent = function(target, values) {
		var component = this.getComponent().queryById(target);

		if (values.title) {
			component.queryById("omicNameField").setValue(values.title);
		}
		if (values.omicName) {
			component.queryById("omicNameField").setValue(values.omicName);
		}
		if (values.mainFile) {
			component.queryById("mainFileSelector").setValue(values.mainFile);
		}
		if (values.mainFileType) {
			component.queryById("fileTypeSelector").setValue(values.mainFileType);
		}
		if (values.secondaryFile) {
			component.queryById("secondaryFileSelector").setValue(values.secondaryFile);
		}
		if (values.secondaryFileType) {
			component.queryById("relevantFileTypeSelector").setValue(values.secondaryFileType);
		}
		if (values.toogleMapRegions) {
			component.queryById("toogleMapRegions").setVisible(values.toogleMapRegions === true);
		}
		if (values.configVars) {
			component.queryById("configVars").setValue(values.configVars);
		}
		if (values.enrichmentType) {
			component.queryById("enrichmentType").setValue(values.enrichmentType);
		}
		if (values.ignoreMissing) {
			component.queryById("ignoreMissing").setValue(values.ignoreMissing);
		}

		if (!component.isVisible()) {
			this.toogleContent();
		}
		return this;
	};

	/*********************************************************************
	* COMPONENT DECLARATION
	***********************************************************************/
	this.initComponent = function() {
		var me = this;
		this.component = Ext.widget({
			xtype: "container",
			flex: 1,
			type: me.type,
			cls: "omicbox regionBasedOmic",
			layout: {
				align: 'stretch',
				type: 'vbox'
			},
			items: [{
				/* No `flex` -- see the note on the first omicboxTitle above: a
				   flexed 44px header is where a short card takes its shortfall
				   from, and it landed on the section heading below. */
				xtype: "box",
				cls: "omicboxTitle " + this.class,
				html: '<h4><a class="deleteOmicBox" href="javascript:void(0)" style="margin: 0; float:right;  padding-right: 15px;">' +
				(me.removable ? ' <i class="fa fa-trash"></i></a>' : "</a>") + this.title +
				'</h4>'
			}, {
				xtype: "box",
				itemId: "toogleMapRegions",
				hidden: !this.allowToogle,
				html: '<div class="checkbox" style=" margin: 10px 50px; font-size: 16px; "><input type="checkbox" id="' + this.namePrefix + '_mapRegions"><label for="' + this.namePrefix + '_mapRegions">My regions are already mapped to Gene IDs, skip this step.</label></div>'
			},
			{
				xtype: "box",
				itemId: "toogleUseAssociations",
				hidden: !this.allowToogle,
				html: '<div class="checkbox" style=" margin: 10px 50px; font-size: 16px; "><input type="checkbox" id="' + this.namePrefix + '_useAssociations"><label for="' + this.namePrefix + '_useAssociations">Provide own associations lists.</label></div>'
			}, {
				xtype: "container",
				itemId: "itemsContainerAlt",
				layout: {
					align: 'stretch',
					type: 'vbox'
				},
				padding: 10,
				hidden: true,
				disabled: true,
				defaults: {
					labelAlign: "right",
					labelWidth: 150,
					maxLength: 100
				},
				items: [{
					xtype: 'combo',
					fieldLabel: 'Omic Name',
					name: this.namePrefix + '_omic_name',
					value: this.omicName,
					itemId: "omicNameField",
					displayField: 'name',
					valueField: 'name',
					emptyText: 'Type or choose the omic type',
					queryMode: 'local',
					hidden: this.omicName !== "",
					editable: true,
					allowBlank: false,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name'],
						autoLoad: true,
						proxy: {
							type: 'ajax',
							url: 'resources/data/all_omics.json',
							reader: {
								type: 'json',
								root: 'omics',
								successProperty: 'success'
							}
						}
					})
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: 'Data file',
					namePrefix: this.namePrefix,
					itemId: "mainFileSelector",
					helpTip: "Upload the feature quantification file (Gene expression, proteomics quantification,...) or choose it from your data folder."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_file_type',
					itemId: "fileTypeSelector",
					value: "Bed file (regions mapped to Genes)",
					hidden: true,
					helpTip: "Specify the type of data for uploaded file (Gene Expression file, Proteomic quatification,...)."
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: 'Relevant features file',
					namePrefix: this.namePrefix + '_relevant',
					itemId: "secondaryFileSelector",
					helpTip: "Upload the list of relevant features (relevant genes, relevant proteins,...)."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_relevant_file_type',
					itemId: "relevantFileTypeSelector",
					value: "Relevant regions list (mapped to Genes)",
					hidden: true,
					helpTip: "Specify the type of data for uploaded file (Relevant Genes list, Relevant proteins list,...)."
				}, {
					xtype: 'textfield',
					fieldLabel: 'Map to',
					name: this.namePrefix + '_match_type',
					itemId: "mapToSelector",
					value: this.mapTo,
					hidden: true
				},{
					xtype: 'textfield',
					name: this.namePrefix + '_config_args',
					hidden: true,
					itemId: 'configVars',
					maxLength: 1000
				}, {
					xtype: 'combo',
					itemId: 'enrichmentType',
					fieldLabel: 'Enrichment type',
					name: this.namePrefix + '_enrichment',
					hidden: this.omicName !== "",
					value: this.featureEnrichment.toString(),
					displayField: 'name', valueField: 'value',
					editable: false,
					allowBlank: false,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name', 'value'],
						data: [
							['Genes', 'genes'],
							['Features', 'features'],
							['Associations', 'associations']
						]
					}),
					helpTip: "Define how the Fisher contingency table must be done: counting genes, features (i.e: microRNA, proteins...) or associations (combination of feature & gene)."
				}]
			}, {
				xtype: "container",
				itemId: "itemsContainerAssociations",
				layout: {
					align: 'stretch',
					type: 'vbox'
				},
				disabled: true,
				defaults: {
					labelAlign: "right",
					labelWidth: 150,
					maxLength: 100
				},
				hidden: true,
				items: [{
					xtype: 'combo',
					fieldLabel: 'Omic Name',
					name: this.namePrefix + '_omic_name',
					value: this.omicName,
					itemId: "omicNameField",
					displayField: 'name',
					valueField: 'name',
					emptyText: 'Type or choose the omic type',
					queryMode: 'local',
					hidden: this.omicName !== "",
					editable: true,
					allowBlank: false,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name'],
						autoLoad: true,
						proxy: {
							type: 'ajax',
							url: 'resources/data/all_omics.json',
							reader: {
								type: 'json',
								root: 'omics',
								successProperty: 'success'
							}
						}
					})
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: 'Data file',
					namePrefix: this.namePrefix,
					itemId: "mainFileSelector",
					helpTip: "Upload the feature quantification file (Gene expression, proteomics quantification,...) or choose it from your data folder."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_file_type',
					itemId: "fileTypeSelector",
					value: "Map file (features mapped to Genes)",
					hidden: true,
					helpTip: "Specify the type of data for uploaded file (Gene Expression file, Proteomic quatification,...)."
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: 'Relevant features file',
					namePrefix: this.namePrefix + '_relevant',
					itemId: "secondaryFileSelector",
					helpTip: "Upload the list of relevant features (relevant genes, relevant proteins,...)."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_relevant_file_type',
					itemId: "relevantFileTypeSelector",
					value: "Relevant regulators list (mapped to Genes)",
					hidden: true,
					helpTip: "Specify the type of data for uploaded file (Relevant Genes list, Relevant proteins list,...)."
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: 'Associations file',
					namePrefix: this.namePrefix + '_associations',
					itemId: "mainAssociationFileSelector",
					helpTip: "Upload the 2 column association file associating genes with features or choose it from your data folder."
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: 'Relevant associations file',
					namePrefix: this.namePrefix + '_relevant_associations',
					itemId: "secondaryAssociationFileSelector",
					helpTip: "Upload the 2 column list of relevant associations (gene - feature) or choose it from your data folder."
				}, {
					xtype: 'textfield',
					fieldLabel: 'Map to',
					name: this.namePrefix + '_match_type',
					itemId: "mapToSelector",
					value: this.mapTo,
					hidden: true
				},{
					xtype: 'textfield',
					name: this.namePrefix + '_config_args',
					hidden: true,
					itemId: 'configVars',
					maxLength: 1000
				},
				{
					xtype: 'combo',
					itemId: 'enrichmentType',
					fieldLabel: 'Enrichment type',
					name: this.namePrefix + '_enrichment',
					hidden: this.omicName !== "",
					value: this.featureEnrichment.toString(),
					displayField: 'name', valueField: 'value',
					editable: false,
					allowBlank: false,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name', 'value'],
						data: [
							['Genes', 'genes'],
							['Features', 'features'],
							['Associations', 'associations']
						]
					}),
					helpTip: "Define how the Fisher contingency table must be done: counting genes, features (i.e: microRNA, proteins...) or the relevant associations (combination of genes & features)."
				}]
			}, {
				xtype: "container",
				itemId: "itemsContainer",
				layout: {
					align: 'stretch',
					type: 'vbox'
				},
				padding: 10,
				defaults: {
					labelAlign: "right",
					labelWidth: 150,
					maxLength: 100
				},
				items: [{
					xtype: 'textfield',
					name: "name_prefix",
					hidden: true,
					itemId: "namePrefix",
					value: this.namePrefix
				}, {
					xtype: 'combo',
					fieldLabel: 'Omic Name',
					name: this.namePrefix + '_omic_name',
					hidden: this.omicName !== "",
					itemId: "omicNameField",
					displayField: 'name',
					valueField: 'name',
					emptyText: 'Type or choose the omic type',
					editable: true,
					queryMode: 'local',
					allowBlank: false,
					value: (this.fileType !== null) ? this.fileType : null,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name'],
						autoLoad: true,
						proxy: {
							type: 'ajax',
							url: 'resources/data/all_omics.json',
							reader: {
								type: 'json',
								root: 'omics',
								successProperty: 'success'
							}
						}
					})
				}, {
					xtype: 'textfield',
					hidden: true,
					fieldLabel: 'Map to',
					name: this.namePrefix + '_match_type',
					itemId: "mapToSelector",
					value: this.mapTo
				},
				/*REGIONS FILE*/
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: 'Regions file',
					namePrefix: this.namePrefix,
					itemId: "mainFileSelector",
					helpTip: "Upload the regions file (BED format + Quantification) or choose it from your data folder."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_file_type',
					hidden: true,
					itemId: "fileTypeSelector",
					value: "Bed file (regions)"
				},
				/*RELEVANT REGIONS FILE*/
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: "Relevant regions file",
					namePrefix: this.namePrefix + '_relevant',
					itemId: "secondaryFileSelector",
					helpTip: "Upload the list of relevant regions (TAB format) or choose it from your data folder."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_relevant_file_type',
					hidden: true,
					itemId: "relevantFileTypeSelector",
					value: "Relevant regions list"
				},
				/*ANNOTATIONS FILE*/
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: "Annotations file (GTF)",
					namePrefix: this.namePrefix + '_annotations',
					itemId: "tertiaryFileSelector",
					extraButtons: [{
						text: 'Use a GTF from PaintOmics',
						handler: function() {
							var me = this;
							var _callback = function(selectedItem) {
								if (selectedItem !== null) {
									me.up("myFilesSelectorButton").queryById("visiblePathField").setValue("[inbuilt GTF files]/" + selectedItem[0].get("fileName"));
									me.up("myFilesSelectorButton").queryById("originField").setValue("inbuilt_gtf");
								}
							};
							Ext.widget("GTFSelectorDialog").showDialog(_callback);
						}
					}],
					helpTip: "Upload the Annotations file (GTF format), choose it from your data folder or browse the GTF files included in PaintOmics AI."
				}, {
					xtype: 'textfield',
					hidden: true,
					fieldLabel: 'File Type',
					name: this.namePrefix + '_annotations_file_type',
					itemId: "referenceFileTypeSelector",
					value: "GTF file"
				},
				/*
				* OTHER FIELDS
				*/
				//report
				{
					xtype: 'textfield',
					hidden: true,
					name: this.namePrefix + '_report',
					fieldLabel: 'Report',
					value: "gene"
				},
				// allow missing
				{
					xtype: 'checkbox',
					itemId: "ignoreMissing",
					name: this.namePrefix + '_ignoremissing',
					fieldLabel: 'Ignore missing entries',
					checked: true,
					allowBlank: false,
					helpTip: "Allow those BED regions with chromosome names not present in the GTF file to be ignored instead of throwing an error."					
				},
				//distance
				{
					xtype: 'numberfield',
					itemId: "distanceField",
					name: this.namePrefix + '_distance',
					fieldLabel: 'Distance (kb)',
					value: 10,
					minValue: 0,
					allowDecimals: false,
					allowBlank: false,
					helpTip: "Maximum distance in kb to report associations. Default: 10 (10kb)"
				},
				//tss
				{
					xtype: 'numberfield',
					itemId: "tssDistanceField",
					name: this.namePrefix + '_tss',
					fieldLabel: 'TSS region distance (bps)',
					value: 200,
					minValue: 0,
					allowDecimals: false,
					allowBlank: false,
					helpTip: "TSS region distance. Default: 200 bps"
				},
				//promoter
				{
					xtype: 'numberfield',
					itemId: "promoterDistanceField",
					name: this.namePrefix + '_promoter',
					fieldLabel: 'Promoter region distance (bps)',
					value: 1300,
					minValue: 0,
					allowDecimals: false,
					allowBlank: false,
					helpTip: "Promoter region distance. Default: 1300 bps"
				},
				//geneAreaPercentage
				{
					xtype: 'numberfield',
					itemId: "geneAreaPercentageField",
					name: this.namePrefix + '_geneAreaPercentage',
					fieldLabel: 'Overlapped gene area (%)',
					// 90, not 50: the helpTip below, Bed2GeneJob's own default
					// (Bed2GeneJob.py:50) and the servlet's fallback
					// (Bed2GenesServlet.py:147) all say 90. The field posts on every
					// request, so the 50 that sat here was silently overriding the
					// documented default for every region-based job.
					value: 90,
					minValue: 0,
					maxValue: 100,
					allowDecimals: false,
					allowBlank: false,
					helpTip: "Percentage of the area of the gene overlapped to be considered to discriminate at transcript and gene level. Default: 90 (90%)"
				},
				//regionAreaPercentage
				{
					xtype: 'numberfield',
					itemId: "regionAreaPercentageField",
					name: this.namePrefix + '_regionAreaPercentage',
					fieldLabel: 'Overlapped region area (%)',
					value: 50,
					minValue: 0,
					maxValue: 100,
					allowDecimals: false,
					allowBlank: false,
					helpTip: "Percentage of the region overlapped by the gene to be considered to discriminate at transcript and gene level. Default: 50 (50%)"
				},
				//rules //TODO
				//{xtype: 'textfield', hidden: true, fieldLabel: 'rules', name: this.namePrefix + '_report', itemId: "reportSelector", value: "gene"},
				//geneIDtag
				{
					xtype: 'textfield',
					itemId: "gtfTagField",
					name: this.namePrefix + '_geneIDtag',
					fieldLabel: 'GTF Tag for gene ID/name ',
					value: "gene_id",
					allowBlank: false,
					helpTip: "GTF tag used to get gene ids/names. Default: gene_id"
				},
				//summarization_method
				{
					xtype: 'combo',
					itemId: "summarizationMethodField",
					name: this.namePrefix + '_summarization_method',
					fieldLabel: 'Summarization method',
					editable: false,
					allowBlank: false,
					value: "mean",
					displayField: 'label',
					valueField: 'value',
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['label', 'value'],
						data: [
							["None", "none"],
							["Mean", "mean"],
							["Maximum", "max"]
						]
					}),
					helpTip: "Choose the strategy used to resolve regions mapping to the same gen region. Default: 'Mean'"
				},{
					xtype: 'combo',
					fieldLabel: 'Enrichment type',
					name: this.namePrefix + '_enrichment_pre',
					hidden: this.omicName !== "",
					value: this.featureEnrichment.toString(),
					displayField: 'name', valueField: 'value',
					editable: false,
					allowBlank: false,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name', 'value'],
						data: [
							['Genes', 'genes'],
							['Features', 'features'],
							['Associations', 'associations']
						]
					}),
					helpTip: "Define how the Fisher contingency table must be done: counting genes, features (i.e: microRNA, proteins...) or associations (combination of genes & features)."
				},{
					xtype: 'fieldcontainer',
					fieldLabel: 'Report',
					defaultType: 'radiofield',
					items: [{
						boxLabel: 'All regions',
						itemId: "reportSelector1",
						name: this.namePrefix + '_report',
						submitValue: false,
						checked: true,
						listeners: {
							change: function(radio, newValue, oldValue) {
								radio.up().queryById("reportOptionsContainer").setVisible(!newValue);
								var elems = radio.up().queryById("reportOptionsContainer").query("checkboxfield");
								for (var i in elems) {
									elems[i].setDisabled(newValue);
								}
								elems = radio.up().queryById("reportAllRegionsOption");
								elems.setDisabled(!newValue);
								elems.setValue(newValue);
							}
						}
					}, {
						boxLabel: 'Let me choose',
						itemId: "reportSelector2",
						name: this.namePrefix + '_report',
						submitValue: false,
						helpTip: "Indicates which regions will be selected from rgmatch output. E.g. Option 'First exon' will filter out all regions that do not map into the first exon of the corresponding gene."
					}, {
						xtype: 'container',
						defaultType: 'checkboxfield',
						hidden: true,
						itemId: 'reportOptionsContainer',
						items: [{
							xtype: 'label',
							text: 'Regions mapping at...'
						}, {
							boxLabel: 'All regions',
							name: this.namePrefix + '_reportRegions',
							inputValue: 'all',
							itemId: 'reportAllRegionsOption',
							checked: true,
							hidden: true
						}, {
							xtype: 'container',
							layout: 'hbox',
							defaultType: 'checkboxfield',
							defaults: {
								hideLabel: false,
								labelAlign: 'top',
								boxLabel: '',
								labelSeparator: "",
								style: 'text-align: center'
							},
							items: [{
								fieldLabel: 'Upstream',
								name: this.namePrefix + '_reportRegions',
								inputValue: 'UPSTREAM',
								labelStyle: 'padding: 2px 3px; font-size:9px; background-color: #E3CEB0;'
							}, {
								fieldLabel: 'Promoter',
								name: this.namePrefix + '_reportRegions',
								inputValue: 'PROMOTER',
								labelStyle: 'padding: 2px 3px; font-size:9px; background-color: #FFF2C0;'
							}, {
								fieldLabel: 'TSS',
								name: this.namePrefix + '_reportRegions',
								inputValue: 'TSS',
								labelAlign: 'top',
								labelStyle: 'padding: 2px 3px; font-size:9px; background-color: #FFF6D3;'
							}, {
								fieldLabel: '1st Exon',
								name: this.namePrefix + '_reportRegions',
								inputValue: '1st_EXON',
								labelStyle: 'padding: 2px 3px; font-size:9px; background-color: #FFC4AD;'
							}, {
								fieldLabel: 'Introns',
								name: this.namePrefix + '_reportRegions',
								inputValue: 'INTRON',
								labelStyle: 'padding: 2px 3px; font-size:9px; background-color: #D0DFF1;'
							}, {
								fieldLabel: 'Gene body',
								name: this.namePrefix + '_reportRegions',
								inputValue: 'GENE_BODY',
								labelStyle: 'padding: 2px 3px; font-size:9px; background-color: #FFE0D3;'
							}, {
								xtype: 'label',
								text: 'Intr.',
								style: 'padding: 5px 3px; font-size:9px; margin-top:4px; background-color: #D0DFF1;'
							}, {
								xtype: 'label',
								text: 'G.B.',
								style: 'padding: 5px 3px; font-size:9px; margin-top:4px;  background-color: #FFE0D3;'
							}, {
								fieldLabel: 'Downstream',
								name: this.namePrefix + '_reportRegions',
								inputValue: 'DOWNSTREAM',
								labelStyle: 'padding: 2px 3px; font-size:9px; background-color: #B2C9E3;'
							}]
						}]
					}]
				},
			]
		}],
		setContent: function(target, values) {
			me.setContent(target, values);
		},
		isValid: function() {
			var valid = true;
			var component = this.queryById("itemsContainerAlt");
			if (!component.isVisible()) {
				component = this.queryById("itemsContainerAssociations");

				if (!component.isVisible()) {
					component = this.queryById("itemsContainer");
				}
			}
			valid = validateAllFields(component) && valid;

			if (component.queryById("mainFileSelector").getValue() === "") {
				valid = false;
				component.queryById("mainFileSelector").markInvalid("Please, provide a Data file.");
			}
			if (component.queryById("tertiaryFileSelector") && component.queryById("tertiaryFileSelector").getValue() === "") {
				valid = false;
				component.queryById("tertiaryFileSelector").markInvalid("Please, provide a GTF file.");
			}
			/* Only reachable from "Provide own associations lists", because
			   mainAssociationFileSelector exists only in itemsContainerAssociations.
			   That mode is the user saying "I will supply the mapping myself"; with
			   no file, Job.parseAssociationsFile returns an empty dict and the omic
			   maps to nothing -- a job that succeeds and means nothing. The row is
			   tagged "required", so this is what makes the tag true. */
			if (component.queryById("mainAssociationFileSelector") && component.queryById("mainAssociationFileSelector").getValue() === "") {
				valid = false;
				component.queryById("mainAssociationFileSelector").markInvalid("Please, provide an associations file, or untick \"Provide own associations lists\".");
			}

			if (this.queryById("reportOptionsContainer").query("checkboxfield[checked=true]") < 1) {
				valid = false;
				this.queryById("reportOptionsContainer").query("checkboxfield").forEach(function(elem) {
					elem.markInvalid("Please, check at least one gene region.");
				});
			}

			return valid;
		},
		isEmpty: function() {
			var component = this.queryById("itemsContainerAlt");
			if (!component.isVisible()) {
				component = this.queryById("itemsContainerAssociations");

				if (!component.isVisible()) {
					component = this.queryById("itemsContainer");
				}
			}
			var empty = true;
			if (component.queryById("mainFileSelector").getValue() !== "") {
				empty = false;
			}
			if (component.queryById("tertiaryFileSelector") && component.queryById("tertiaryFileSelector").getValue() !== "") {
				empty = false;
			}

			return empty;
		},
		listeners: {
			boxready: function() {
				initializeTooltips(".helpTip");

				$("#" + me.namePrefix + "_mapRegions").change(function() {
					me.getComponent().queryById("toogleUseAssociations").setVisible(! $(this).is(':checked'));
					me.toogleContent();
				});

				$("#" + me.namePrefix + "_useAssociations").change(function() {
					me.getComponent().queryById("toogleMapRegions").setVisible(! $(this).is(':checked'));
					me.toogleContent("itemsContainerAssociations");
				});

				$(this.getEl().dom).find("a.deleteOmicBox").click(function() {
					me.removeOmicSubmittingPanel();
				});
			}
		}
	});

	return this.component;
};

return this;
}
RegionBasedOmicSubmittingPanel.prototype = new DefaultSubmittingPanel;

function MiRNAOmicSubmittingPanel(nElem, options) {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	options = (options || {});

	this.title = (options.regulatoryMethod === "pairwise")
		? "Regulatory Omic — Pairwise"
		: "Regulatory Omic";
	this.namePrefix = "omic" + nElem;
	this.omicName = "";
	this.mapTo = "Gene";
	this.fileType = null;
	this.relevantFileType = null;
	this.featureEnrichment = "genes";

	this.allowToogle = options.allowToogle !== false;
	this.removable = options.removable !== false;

	this.class = "miRNAbasedFileBox";

	/*IF THE TYPE WAS SPECIFIED (e.g. gene_expression)*/
	if (options.type !== undefined) {
		//TODO CAPITALIZE THE FIRST LETTER
		this.omicName = options.type;
		this.title = options.type;

		this.fileType = options.fileType;
		this.relevantFileType = options.relevantFileType;
		this.type = this.title.replace(" ", "").toLowerCase();
		this.class = this.type + "FileBox";
	}
	/*********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/

	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* Display-only example mode; the server resolves the files from the
	* manifest. `mirna_unmapped.tab` was named here for years while the file
	* that actually ships is `mirna_unmapped_values.tab` -- a label nobody could
	* act on, and exactly the drift that taking the names from the scenario
	* prevents.
	*
	* @param {Object} [scenario] the entry from /example_datasets.
	*/
	this.setExampleMode = function(scenario){
		var component = this.getComponent();
		//component.queryById("toogleMapRegions").setVisible(false);
		component = component.queryById("itemsContainer");

		var omicNames = (scenario && scenario.omicNames) || ["miRNA"];
		var regulator = omicNames[0];

		setExampleLabel(component.queryById("mainFileSelector"),
			"values");
		setExampleLabel(component.queryById("secondaryFileSelector"),
			"relevant features");
		setExampleLabel(component.queryById("mirnaTargetsFileSelector"),
			"target predictions");
		setExampleLabel(component.queryById("rnaseqauxFileSelector"),
			"target gene expression");

		var field = component.queryById("omicNameField");
		field.setValue(regulator);
		field.setDisabled(true);

		var otherFields = ["summarizationMethodField"];
		for(var i in otherFields){
			field = component.queryById(otherFields[i]);

			if (field != null) {
			    field.setReadOnly(true);
			}
		}

		// Turn on the correlation option, which is what this example actually
		// runs: MiRNA2GenesServlet's example branch supplies a transcriptomics
		// file and correlation parameters (kendall / negative_correlation) and
		// no relevant-associations file. setExampleMode already fills in
		// rnaseqauxFileSelector for that path but left the checkbox unticked,
		// so isValid() took the other branch and demanded the associations file
		// -- a field the form itself labels "(optional)". The example could
		// therefore never be submitted: it failed with "Invalid form. Please
		// check form errors." every time.
		//
		// The tick has to wait for boxready. The checkbox is raw HTML inside a
		// box component, and setExampleMode runs before the panel is added to
		// the form, so it is not in the document yet -- every other field here
		// is reached through queryById, which works on the unrendered component
		// tree. Registering the listener now also means it runs after the one
		// declared in the component config, so the change handler that enables
		// the correlation options is already bound when the event is triggered.
		// `this`, not `me`: var me = this is scoped to initComponent.
		var panel = this;
		var enableCorrelationMode = function() {
			var $corr = $("#" + panel.namePrefix + "_corrOptions");
			if (!$corr.length) return;
			$corr.prop("checked", true).trigger("change");
			$corr.prop("disabled", true);
		};

		var panelComponent = this.getComponent();
		if (panelComponent.rendered) {
			enableCorrelationMode();
		} else {
			panelComponent.on("boxready", enableCorrelationMode, null, {single: true});
		}
	};
	this.setContent = function(target, values) {
		var component = this.getComponent().queryById(target);

		if (values.title) {
			component.queryById("omicNameField").setValue(values.title);
		}
		if (values.omicName) {
			component.queryById("omicNameField").setValue(values.omicName);
		}
		if (values.mainFile) {
			component.queryById("mainFileSelector").setValue(values.mainFile);
		}
		if (values.mainFileType) {
			component.queryById("fileTypeSelector").setValue(values.mainFileType);
		}
		if (values.secondaryFile) {
			component.queryById("secondaryFileSelector").setValue(values.secondaryFile);
		}
		if (values.secondaryFileType) {
			component.queryById("relevantFileTypeSelector").setValue(values.secondaryFileType);
		}
		if (values.thirdFile) {
			component.queryById("thirdFileSelector").setValue(values.thirdFile);
		}
		if (values.thirdFileType) {
			component.queryById("associationsFileTypeSelector").setValue(values.thirdFileType);
		}
		if (values.fourthFile) {
			component.queryById("fourthFileSelector").setValue(values.fourthFile);
		}
		if (values.fourthFileType) {
			component.queryById("associationsRelevantFileTypeSelector").setValue(values.fourthFileType);
		}
		if (values.toogleMapRegions) {
			component.queryById("toogleMapRegions").setVisible(values.toogleMapRegions === true);
		}
		if (values.configVars) {
			component.queryById("configVars").setValue(values.configVars);
		}
		if (values.enrichmentType) {
			component.queryById("enrichmentType").setValue(values.enrichmentType);
		}

		if (!component.isVisible()) {
			this.toogleContent();
		}
		return this;
	};

	/*********************************************************************
	* COMPONENT DECLARATION
	***********************************************************************/
	this.initComponent = function() {
		var me = this;
		this.component = Ext.widget({
			xtype: "container",
			flex: 1,
			type: me.type,
			cls: "omicbox miRNABasedOmic",
			layout: {
				align: 'stretch',
				type: 'vbox'
			},
			items: [{
				/* No `flex` -- see the note on the first omicboxTitle above: a
				   flexed 44px header is where a short card takes its shortfall
				   from, and it landed on the section heading below. */
				xtype: "box",
				cls: "omicboxTitle " + this.class,
				html: '<h4><a class="deleteOmicBox" href="javascript:void(0)" style="margin: 0; float:right;  padding-right: 15px;">' +
				(me.removable ? ' <i class="fa fa-trash"></i></a>' : "</a>") + this.title +
				'</h4>'
			},
			{
				xtype: "box",
				itemId: "toogleMapRegions",
				hidden: !this.allowToogle,
				html: '<div class="checkbox" style=" margin: 10px 50px; font-size: 16px; "><input type="checkbox" id="' + this.namePrefix + '_mapRegions"><label for="' + this.namePrefix + '_mapRegions">My features are already mapped to Gene IDs, skip this step.</label></div>'
			},
			// {
			// 	xtype: "box",
			// 	itemId: "toogleUseAssociations",
			// 	hidden: !this.allowToogle,
			// 	html: '<div class="checkbox" style=" margin: 10px 50px; font-size: 16px; "><input type="checkbox" id="' + this.namePrefix + '_useAssociations"><label for="' + this.namePrefix + '_useAssociations">Provide own associations lists.</label></div>'
			// },
			{
				xtype: "container",
				itemId: "itemsContainerAlt",
				layout: {
					align: 'stretch',
					type: 'vbox'
				},
				disabled: true,
				defaults: {
					labelAlign: "right",
					labelWidth: 150,
					maxLength: 100
				},
				hidden: true,
				items: [{
					xtype: 'combo',
					fieldLabel: 'Omic Name',
					name: this.namePrefix + '_omic_name',
					value: this.omicName,
					itemId: "omicNameField",
					displayField: 'name',
					valueField: 'name',
					emptyText: 'Type or choose the omic type',
					queryMode: 'local',
					hidden: this.omicName !== "",
					editable: true,
					allowBlank: false,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name'],
						autoLoad: true,
						proxy: {
							type: 'ajax',
							url: 'resources/data/all_omics.json',
							reader: {
								type: 'json',
								root: 'omics',
								successProperty: 'success'
							}
						}
					})
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: 'Data file',
					namePrefix: this.namePrefix,
					itemId: "mainFileSelector",
					helpTip: "Upload the feature quantification file (Gene expression, proteomics quantification,...) or choose it from your data folder."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_file_type',
					itemId: "fileTypeSelector",
					value: "Map file (features mapped to Genes)",
					hidden: true,
					helpTip: "Specify the type of data for uploaded file (Gene Expression file, Proteomic quatification,...)."
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: 'Relevant features file',
					namePrefix: this.namePrefix + '_relevant',
					itemId: "secondaryFileSelector",
					helpTip: "Upload the list of relevant features (relevant genes, relevant proteins,...)."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_relevant_file_type',
					itemId: "relevantFileTypeSelector",
					value: "Relevant regulators list (mapped to Genes)",
					hidden: true,
					helpTip: "Specify the type of data for uploaded file (Relevant Genes list, Relevant proteins list,...)."
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: 'Regulator associations file',
					namePrefix: this.namePrefix + '_associations',
					itemId: "thirdFileSelector",
					helpTip: "Upload the association list."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_associations_file_type',
					itemId: "associationsFileTypeSelector",
					value: "Regulator associations",
					hidden: true,
					helpTip: "Specify the type of data for uploaded file (Relevant Genes list, Relevant proteins list,...)."
				}, {
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: 'Regulator relevant associations file',
					namePrefix: this.namePrefix + '_relevant_associations',
					itemId: "fourthFileSelector",
					helpTip: "Upload the relevant association list."
				}, {
					xtype: "textfield",
					fieldLabel: 'File Type',
					name: this.namePrefix + '_associations_relevant_file_type',
					itemId: "associationsRelevantFileTypeSelector",
					value: "Regulator relevant associations",
					hidden: true,
					helpTip: "Specify the type of data for uploaded file (Relevant Genes list, Relevant proteins list,...)."
				}, {
					xtype: 'textfield',
					fieldLabel: 'Map to',
					name: this.namePrefix + '_match_type',
					itemId: "mapToSelector",
					value: this.mapTo,
					hidden: true
				},{
					xtype: 'textfield',
					name: this.namePrefix + '_config_args',
					hidden: true,
					itemId: 'configVars',
					maxLength: 1000
				},
				{
					xtype: 'combo',
					itemId: 'enrichmentType',
					fieldLabel: 'Enrichment type',
					name: this.namePrefix + '_enrichment',
					hidden: this.omicName !== "",
					value: this.featureEnrichment.toString(),
					displayField: 'name', valueField: 'value',
					editable: false,
					allowBlank: false,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name', 'value'],
						data: [
							['Genes', 'genes'],
							['Features', 'features'],
							['Associations', 'associations']
						]
					}),
					helpTip: "Define how the Fisher contingency table must be done: counting genes, features (i.e: microRNA, proteins...) or associations (combination of genes & features)."
				}]
			}, {
				xtype: "container",
				itemId: "itemsContainer",
				layout: {
					align: 'stretch',
					type: 'vbox'
				},
				padding: 10,
				defaults: {
					labelAlign: "right",
					labelWidth: 150,
					maxLength: 100
				},
				items: [{
					xtype: 'textfield',
					name: "name_prefix",
					hidden: true,
					itemId: "namePrefix",
					value: this.namePrefix
				}, {
					xtype: 'combo',
					fieldLabel: 'Omic Name',
					name: this.namePrefix + '_omic_name',
					hidden: this.omicName !== "",
					itemId: "omicNameField",
					displayField: 'name',
					valueField: 'name',
					emptyText: 'Type or choose the omic type',
					editable: true,
					queryMode: 'local',
					allowBlank: false,
					value: (this.fileType !== null) ? this.fileType : null,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name'],
						autoLoad: true,
						proxy: {
							type: 'ajax',
							url: 'resources/data/all_omics.json',
							reader: {
								type: 'json',
								root: 'omics',
								successProperty: 'success'
							}
						}
					})
				}, {
					xtype: 'textfield',
					hidden: true,
					fieldLabel: 'Map to',
					name: this.namePrefix + '_match_type',
					itemId: "mapToSelector",
					value: this.mapTo
				},
				/*miRNA FILE*/
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: 'Regulators expression file',
					namePrefix: this.namePrefix,
					itemId: "mainFileSelector",
					helpTip: "Upload the quantification file (i.e. miRNA Quantification) or choose it from your data folder. See above the accepted format for the file."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_file_type',
					hidden: true,
					itemId: "fileTypeSelector",
					value: "Gene Expression file"
				},
				/*RELEVANT miRNA FILE*/
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: 'Relevant regulators file',
					namePrefix: this.namePrefix + '_relevant',
					itemId: "secondaryFileSelector",
					helpTip: "Upload the list of relevant (differentially expressed) features (TAB format) or choose it from your data folder. See above the accepted format for the file."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_relevant_file_type',
					hidden: true,
					itemId: "relevantFileTypeSelector",
					value: "Relevant gene list"
				},
				{
					xtype: 'combo',
					fieldLabel: 'Enrichment type',
					name: this.namePrefix + '_enrichment_pre',
					hidden: this.omicName !== "",
					value: this.featureEnrichment.toString(),
					displayField: 'name', valueField: 'value',
					editable: false,
					allowBlank: false,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name', 'value'],
						data: [
							['Genes', 'genes'],
							['Features', 'features'],
							['Associations', 'associations']
						]
					}),
					helpTip: "Define how the Fisher contingency table must be done: counting genes, features (i.e: microRNA, proteins...) or associations (combination of genes & features)."
				},
				/*TARGETS FILE*/
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: "Associations file",
					namePrefix: this.namePrefix + '_associations',
					itemId: "mirnaTargetsFileSelector",
					helpTip: "Upload the reference file that relates each feature (i.e. miRNA) with its potential targets. This information is usually extracted from popular databases such as miRbase for miRNAs. See above the accepted format for the file."
				}, {
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: this.namePrefix + '_associations_file_type',
					hidden: true,
					itemId: "mirnaTargetsFileTypeSelector",
					value: "Associations file"
				}, /*{
					xtype: "myFilesSelectorButton",
					fieldLabel: 'Associations file',
					namePrefix: this.namePrefix + '_associations',
					itemId: "mainAssociationFileSelector",
					helpTip: "Upload the 2 column association file associating genes with features or choose it from your data folder."
				}*/
				/*{
					xtype: "box",
					itemId: "toogleCorrOptions",
					hidden: !this.allowToogle,
					html: '<div class="checkbox" style=" margin: 10px 50px; font-size: 16px; "><input type="checkbox" id="' + this.namePrefix + '_corrOptions"><label for="' + this.namePrefix + '_corrOptions">Additional options using correlation.</label></div>'
				},*/
				/* CORRELATION OPTIONS */
				{
					xtype: 'box',
					html: '<hr><p>You can provide a relevant associations file or let the program to automatically retrieve them based on correlation with a gene expression dataset.</p>'
				},
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: 'Relevant associations file',
					namePrefix: this.namePrefix + '_relevant_associations',
					itemId: "secondaryAssociationFileSelector",
					helpTip: "Upload the 2 column list of relevant associations (gene - feature) or choose it from your data folder."
				},
				{
					xtype: "box",
					itemId: "toogleCorrOptions",
					hidden: !this.allowToogle,
					html: '<div class="checkbox" style=" margin: 10px 50px; font-size: 14px; "><input type="checkbox" id="' + this.namePrefix + '_corrOptions"><label for="' + this.namePrefix + '_corrOptions">Automatically select relevant associations using correlation.</label></div>'		
				},
				/* CORRELATION OPTIONS */
				{
					xtype: "container",
					itemId: "itemsContainerCorrOptions",
					layout: {
						align: 'stretch',
						type: 'vbox'
					},
					disabled: true,
					defaults: {
						labelAlign: "right",
						labelWidth: 150,
						maxLength: 100
					},
					items: [
						{
							xtype: 'textfield',
							fieldLabel: 'Omic Name',
							name: this.namePrefix + '_rnaseqaux_omic_name',
							hidden: true,
							itemId: "rnaseqauxOmicNameField",
							value: "Gene Expression"
						},
						{
							xtype: "myFilesSelectorButton",
							requiredTag: "optional",
							fieldLabel: 'Gene expression dataset',
							namePrefix: this.namePrefix + '_rnaseqaux',
							extraButtons: [{
								text: 'Use a file from other omic',
								handler: function() {
									var me = this;
									var _callback = function(selectedItem) {
										if (selectedItem !== null) {
											me.up("myFilesSelectorButton").queryById("visiblePathField").setValue(selectedItem[0].get("omic") + ": " + selectedItem[0].get("file"));
											me.up("myFilesSelectorButton").queryById("originField").setValue(selectedItem[0].get("name"));
										}
									};
									Ext.widget("OmicInputSelectorDialog").showDialog(_callback);
								}
							}],
							itemId: "rnaseqauxFileSelector",
							helpTip: "Upload the quantification file for the gene expression. This file is used to calculate the correlation of the expression of the genes and their associated features. Using this correlation we can filter and order the features that will be assigned to each gene. See above the accepted format for the file."
						}, {
							xtype: 'textfield',
							fieldLabel: 'File Type',
							name: this.namePrefix + '_rnaseqaux_file_type',
							hidden: true,
							itemId: "rnaseqauxFileTypeSelector",
							value: "Gene Expression file"
						},
						{
							xtype: 'textfield',
							hidden: true,
							fieldLabel: 'Map to',
							name: this.namePrefix + '_rnaseqaux_match_type',
							itemId: "rnaseqauxFileMapToSelector",
							value: 'gene'
						},
						/*
						* OTHER FIELDS
						*/
						//report
						{
							xtype: 'combo',
							itemId: "reportMethodField",
							name: this.namePrefix + '_report',
							fieldLabel: 'Report',
							editable: false,
							allowBlank: false,
							value: "all",
							displayField: 'label',
							valueField: 'value',
							store: Ext.create('Ext.data.ArrayStore', {
								fields: ['label', 'value'],
								data: [
									["All features", "all"],
									["Only relevant features (e.g. DE)", "DE"]
								]
							}),
							helpTip: "Choose between consider all features in the quantification file or just those features that are differentially expressed. Default: 'All features'"
						},
						{
							xtype: 'combo',
							itemId: "scoreMethodField",
							name: this.namePrefix + '_score_method',
							fieldLabel: 'Score method',
							editable: false,
							allowBlank: false,
							value: "kendall",
							displayField: 'label',
							valueField: 'value',
							store: Ext.create('Ext.data.ArrayStore', {
								fields: ['label', 'value'],
								data: [
									// ["Fold Change of miRNA expression", "fc"],
									["Correlation with gene expression (Spearman)", "spearman"],
									["Correlation with gene expression (Kendall)", "kendall"],
									["Correlation with gene expression (Pearson)", "pearson"]
								]
							}),
							helpTip:
							"As en example in miRNA, usually a single miRNA has multiple potential target genes, but not all targets are being " +
							"regulated by a certain miRNA at certain moment. Consequently, we need to discriminate the real targets for a miRNA."+
							"If Gene expression (GE) data is available, then we calculate the correlation between each miRNA " +
							"and each target gene and filter out all those miRNAs that has a lower correlation value than a given threadhold." +
							"If no GE is available then we filter based on the fold-change for the expression of the miRNAs." +
							"Default: 'Kendall correlation' if GE is available. 'Fold Change' in other case."
						},
						{
							xtype: 'combo',
							itemId: "selectionMethodField",
							name: this.namePrefix + '_selection_method',
							fieldLabel: 'Selection method',
							editable: false,
							allowBlank: false,
							value: "negative_correlation",
							displayField: 'label',
							valueField: 'value',
							store: Ext.create('Ext.data.ArrayStore', {
								fields: ['label', 'value'],
								data: [
									["by max. fold-change of feature expression", "fc"],
									["by absolute correlation with gene expression", "abs_correlation"],
									["by positive correlation with gene expression", "positive_correlation"],
									["by negative correlation with gene expression", "negative_correlation"]
								]
							}),
							//TODO: THIS HELP TOOL IS NOT DISPLAYED, WHY??
							helpTip:
							"Determines how we select the potential features that are regulating a certain gene. " +
							"For instance, usually miRNA act as inhibitors of gene expression so we should expect an opposite behavior " +
							"to the regulated gene. A negative correlation will fit better to this expected profile. " +
							"Default: If gene expression (GE) if avilable, select and order by 'negative correlation'. 'Max fold-change' in other case.",
							listeners:{
								change: function(elem, newValue, oldValue){
									elem = elem.nextSibling("numberfield");
									if(newValue === "negative_correlation"){
										elem.setValue(Math.abs(elem.value) * -1);
									}else{
										elem.setValue(Math.abs(elem.value));
									}
								}
							}
						},
						{
							xtype: 'numberfield',
							itemId: "cutoffField",
							name: this.namePrefix + '_cutoff',
							fieldLabel: 'Filter cutoff',
							value: -0.5,
							minValue: -1,
							maxValue: 1,
							step: 0.1,
							allowDecimals: true,
							allowBlank: false,
							// Deliberately not "features below the cutoff are removed".
							// MiRNA2GeneJob inverts both the cutoff and the score when the
							// selection method is negative correlation (lines 385-386 and
							// 429), so at the -0.5 default an association is kept when its
							// correlation is *more negative* than -0.5. Stating a numeric
							// direction here gets it backwards for the default method; the
							// direction belongs to the method, so that is what is named.
							helpTip: "Threshold for the correlation or fold change, applied in the direction of the selection method above. At the default -0.5 with negative correlation, an association is kept when its correlation is more negative than -0.5."
						}
					]
				}
		]
		}],
		setContent: function(target, values) {
			me.setContent(target, values);
		},
		isValid: function() {
			var valid = true;
			var component = this.queryById("itemsContainerAlt");
			if (!component.isVisible()) {
				component = this.queryById("itemsContainer");
			}
			valid = validateAllFields(component) && valid;

			if (component.queryById("mainFileSelector").getValue() === "") {
				valid = false;
				component.queryById("mainFileSelector").markInvalid("Please, provide a data file.");
			}
			if (component.queryById("mirnaTargetsFileSelector") && component.queryById("mirnaTargetsFileSelector").getValue() === "") {
				valid = false;
				component.queryById("mirnaTargetsFileSelector").markInvalid("Please, provide a features targets reference file.");
			}

			var corrEnabled = $("#" + me.namePrefix + "_corrOptions").is(':checked');

			if (corrEnabled && component.queryById("rnaseqauxFileSelector") && component.queryById("rnaseqauxFileSelector").getValue() === "") {
				valid = false;
				component.queryById("rnaseqauxFileSelector").markInvalid("Please, provide a transcriptomics file.");
			} else if(! corrEnabled && component.queryById("secondaryAssociationFileSelector") && component.queryById("secondaryAssociationFileSelector").getValue() === "") {
				valid = false;
				component.queryById("secondaryAssociationFileSelector").markInvalid("Please, provide a relevant associations file or enable the automatic mode instead.");
			}
			return valid;
		},
		isEmpty: function() {
			var component = this.queryById("itemsContainerAlt");
			if (!component.isVisible()) {
				component = this.queryById("itemsContainer");
			}
			var empty = true;
			if (component.queryById("mainFileSelector").getValue() !== "") {
				empty = false;
			}
			if (component.queryById("mirnaTargetsFileSelector") && component.queryById("mirnaTargetsFileSelector").getValue() !== "") {
				empty = false;
			}

			return empty;
		},
		listeners: {
			boxready: function() {
				initializeTooltips(".helpTip");

				$("#" + me.namePrefix + "_mapRegions").change(function() {
					//$("#" + me.namePrefix + "_useAssociations").prop('disabled', $(this).is(':checked'));
					// me.getComponent().queryById("toogleUseAssociations").setVisible(! $(this).is(':checked'));
					me.toogleContent();
				});

				// $("#" + me.namePrefix + "_useAssociations").change(function() {
				// 	// $("#" + me.namePrefix + "_mapRegions").prop('disabled', $(this).is(':checked'));
				// 	me.getComponent().queryById("toogleMapRegions").setVisible(! $(this).is(':checked'));
				// 	me.toogleContent("itemsContainerAssociations");
				// });

				$("#" + me.namePrefix + "_corrOptions").change(function() {
					var corrEnabled = $(this).is(':checked');
					// $("#" + me.namePrefix + "_mapRegions").prop('disabled', corrEnabled);
					me.getComponent().queryById("secondaryAssociationFileSelector").down('container').setDisabled(corrEnabled);
					me.getComponent().queryById("itemsContainerCorrOptions").setDisabled(! corrEnabled);

					/* Two of the rows on this card are required only because of this
					   checkbox, so their tag is driven by the checkbox -- and by the same
					   reading of it that isValid() makes a few lines above. Correlation on:
					   the panel needs the gene expression file and derives the relevant
					   associations itself. Correlation off: it needs the associations list.
					   Written into the label instead, one of the two would always be wrong;
					   that is what 'Relevant associations file (optional)' was, a label that
					   said optional over a field that refused the form. */
					me.getComponent().queryById("secondaryAssociationFileSelector").setRequiredTag(corrEnabled ? "optional" : "required");
					me.getComponent().queryById("rnaseqauxFileSelector").setRequiredTag(corrEnabled ? "required" : "optional");
				});

				$(this.getEl().dom).find("a.deleteOmicBox").click(function() {
					me.removeOmicSubmittingPanel();
				});
			}
		}
	});

	return this.component;
};

return this;
}
MiRNAOmicSubmittingPanel.prototype = new DefaultSubmittingPanel;

function MORESubmittingPanel(nElem, options) {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	options = (options || {});
	this.title = "Regulatory Omic — MORE";
	this.namePrefix = "omic" + nElem;
	this.omicName = "MORE Regulatory Omic";
	this.mapTo = "Gene";
	this.type = "moreanalysis";
	this.class = "moreBasedOmic";
	this.allowToogle = false;
	this.removable = options.removable !== false;

	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.setContent = function(target, values) {
		var component = this.getComponent().queryById(target);
		if (values.mainFile !== undefined) {
			component.queryById("mainFileFieldAlt").setValue(values.mainFile);
		}
		if (values.secondaryFile !== undefined) {
			component.queryById("secondaryFileFieldAlt").setValue(values.secondaryFile);
		}
		if (values.thirdFile !== undefined) {
			component.queryById("thirdFileFieldAlt").setValue(values.thirdFile);
		}
		if (values.fourthFile !== undefined && values.fourthFile !== null) {
			component.queryById("fourthFileFieldAlt").setValue(values.fourthFile);
		}
		if (values.title !== undefined) {
			component.queryById("omicNameFieldAlt").setValue(values.title);
		}
		if (values.configVars !== undefined) {
			component.queryById("configVarsFieldAlt").setValue(values.configVars);
		}
		if (values.enrichmentType !== undefined) {
			component.queryById("enrichmentTypeFieldAlt").setValue(values.enrichmentType);
		}
		this.toogleContent();
		return this;
	};

	/**
	* Display-only example mode for the MORE panel.
	*
	* MORE never had an example. Its inputs are also the least guessable in the
	* application -- a per-sample matrix with replicates rather than the log
	* ratios every other omic takes, plus a numeric 0/1 design matrix and one
	* association file per regulatory omic -- so "load an example and look at
	* the files" was the one thing a user could not do for the format that most
	* needed it.
	*
	* Only labels are set here; the server resolves the real files from the
	* manifest when the form posts to dm_fromMOREtoGenes/example/<id>. The model
	* settings (method, alpha, VIP, R2) come from the manifest too --
	* MOREServlet's example branch returns immediately after applyMoreScenario
	* and never reaches the "6. Model Parameters" block that reads the form -- so
	* lockFormForExample marks them read-only along with everything else. They
	* were left editable here on the belief that they were honoured; they are
	* not, and an editable field that changes nothing is the T1 defect in
	* miniature.
	*
	* @param {Object} scenario the entry from /example_datasets.
	*/
	this.setExampleMode = function(scenario) {
		var component = this.getComponent().queryById("itemsContainer");
		var omicNames = (scenario && scenario.omicNames) || ["Regulatory omic"];

		var label = function(itemId, text) {
			setExampleLabel(component.queryById(itemId), text);
		};

		label("conditionsFileSelector", "experimental design (samples × groups)");
		label("rnaseqauxFileSelector", "target gene expression (per sample)");
		label("mainFileSelector", omicNames[0] + " — regulator values");
		label("moreAssociationsFileSelector", omicNames[0] + " — associations");
		label("moreRelevantFileSelector", omicNames[0] + " — relevant regulators");

		// The omic name is `allowBlank: false`, so leaving it empty makes
		// checkForm() refuse to submit -- the example would load and then
		// silently fail to run. The server takes the name from the manifest
		// regardless; this is what lets the form validate.
		var nameField = component.queryById("omicNameField");
		if (nameField) {
			nameField.setValue(omicNames[0]);
			nameField.setDisabled(true);
		}

		// The manifest may carry more regulatory omics than the single block
		// the panel starts with. Rather than synthesising extra blocks -- which
		// would have to mirror the "+ Add another Regulatory Omic" handler and
		// drift from it -- say so, since the server loads all of them regardless.
		if (omicNames.length > 1) {
			this.getComponent().queryById("itemsContainer").add({
				xtype: 'box',
				html: '<p style="margin:6px 0 0 0;font-size:12px;color:#6B6B6B;">' +
					'This example also includes <b>' +
					Ext.String.htmlEncode(omicNames.slice(1).join(', ')) +
					'</b>. Every regulatory omic in the dataset is analysed; only ' +
					'the first is shown above.</p>'
			});
		}
		return this;
	};

	/*********************************************************************
	* COMPONENT DECLARATION
	***********************************************************************/
	this.initComponent = function() {
		var me = this;
		this.component = Ext.widget({
			xtype: "container",
			flex: 1,
			type: me.type,
			cls: "omicbox " + this.class,
			layout: {
				align: 'stretch',
				type: 'vbox'
			},
			items: [{
				/* No `flex` -- see the note on the first omicboxTitle above: a
				   flexed 44px header is where a short card takes its shortfall
				   from, and it landed on the section heading below. */
				xtype: "box",
				cls: "omicboxTitle moreBasedFileBox",
				html: '<h4><a class="deleteOmicBox" href="javascript:void(0)" style="margin: 0; float:right;  padding-right: 15px;">' +
				(me.removable ? ' <i class="fa fa-trash"></i></a>' : "</a>") + this.title +
				'</h4>'
			},
			{
				xtype: "container",
				itemId: "itemsContainer",
				layout: {
					align: 'stretch',
					type: 'vbox'
				},
				padding: 10,
				defaults: {
					labelAlign: "right",
					labelWidth: 150,
					maxLength: 100
				},
				items: [{
					xtype: 'textfield',
					name: "name_prefix",
					hidden: true,
					itemId: "namePrefix",
					value: this.namePrefix
				},
				{
					xtype: 'box',
					html: '<hr><h5>Experimental Design</h5>'
				},
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: 'Conditions file',
					namePrefix: 'conditions',
					itemId: "conditionsFileSelector",
					helpTip: "Upload the Experimental Design / Conditions file mapping samples to conditions."
				},
				{
					xtype: 'box',
					html: '<hr><h5>Target Omic (Gene Expression)</h5>'
				},
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: "Gene expression dataset",
					namePrefix: 'rnaseqaux',
					itemId: "rnaseqauxFileSelector",
					helpTip: "Upload the target gene expression dataset used for regulatory analysis."
				},
				{
					xtype: 'textfield',
					fieldLabel: 'File Type',
					name: 'rnaseqaux_file_type',
					hidden: true,
					itemId: "rnaseqauxFileTypeSelector",
					value: "Gene Expression file"
				},
				{
					xtype: 'box',
					html: '<hr><h5>Regulatory Omic Data</h5>'
				},
				{
					xtype: 'combo',
					fieldLabel: 'Omic Name',
					name: 'omic_name_0',
					itemId: "omicNameField",
					displayField: 'name',
					valueField: 'name',
					emptyText: 'Type or choose the omic type',
					queryMode: 'local',
					editable: true,
					allowBlank: false,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name'],
						autoLoad: true,
						proxy: {
							type: 'ajax',
							url: 'resources/data/all_omics.json',
							reader: {
								type: 'json',
								root: 'omics',
								successProperty: 'success'
							}
						}
					})
				},
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "required",
					fieldLabel: 'Regulators expression file',
					namePrefix: 'file_0',
					itemId: "mainFileSelector",
					helpTip: "Upload the quantification file (i.e. miRNA Quantification) or choose it from your data folder. See above the accepted format for the file."
				},
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: 'Relevant regulators file',
					namePrefix: 'relevant_file_0',
					itemId: "moreRelevantFileSelector",
					helpTip: "Upload the list of relevant (differentially expressed) features (TAB format) or choose it from your data folder. See above the accepted format for the file."
				},
				{
					xtype: "myFilesSelectorButton",
					requiredTag: "optional",
					fieldLabel: 'Associations file',
					namePrefix: 'assoc_file_0',
					itemId: "moreAssociationsFileSelector",
					helpTip: "Upload the reference file that relates each feature (i.e. miRNA) with its potential targets. This information is usually extracted from popular databases such as miRbase for miRNAs. See above the accepted format for the file."
				},
				{
					// Per-omic low-variation filter (MORE `minVariation`). Each
					// regulatory omic carries its own threshold so heterogeneous
					// data types (e.g. methylation vs. miRNA) can be filtered
					// independently. Default 0 = keep all but constant regulators.
					xtype: 'numberfield',
					name: 'more_minvar_0',
					fieldLabel: 'Min. variation',
					value: 0,
					minValue: 0,
					step: 0.01,
					allowDecimals: true,
					allowBlank: false,
					helpTip: "Minimum change in standard deviation (numeric regulators) or proportion (binary) a regulator must show across conditions to avoid being filtered as low-variation. Applied to this regulatory omic only. 0 = keep all but constant regulators."
				},
				{
					xtype: 'container',
					itemId: 'addOmicWrapper',
					layout: { type: 'hbox', pack: 'center' },
					margin: '5 0 15 0',
					items: [{
						xtype: 'button',
						text: '<i class="fa fa-plus-circle"></i> Add another Regulatory Omic',
						width: 250,
						handler: function(btn) {
							var container = btn.up('#itemsContainer');
							if (!container.moreOmicCount) container.moreOmicCount = 0;
							container.moreOmicCount++;
							var i = container.moreOmicCount;
							var insertIdx = container.items.indexOf(btn.up('#addOmicWrapper'));
						
						container.insert(insertIdx, [
							{
								xtype: 'box',
								html: '<hr><h5>Regulatory Omic Data ' + (i+1) + '</h5>'
							},
							{
								xtype: 'combo',
								fieldLabel: 'Omic Name',
								name: 'omic_name_' + i,
								displayField: 'name',
								valueField: 'name',
								emptyText: 'Type or choose the omic type',
								queryMode: 'local',
								editable: true,
								allowBlank: false,
								store: Ext.create('Ext.data.ArrayStore', {
									fields: ['name'],
									autoLoad: true,
									proxy: {
										type: 'ajax',
										url: 'resources/data/all_omics.json',
										reader: {
											type: 'json',
											root: 'omics',
											successProperty: 'success'
										}
									}
								})
							},
							{
								xtype: "myFilesSelectorButton",
								requiredTag: "required",
								fieldLabel: 'Regulators expression file',
								namePrefix: 'file_' + i,
								/* The same itemIds as the first regulator's
								   selectors: the input check keys its
								   contract on the slot's itemId, and without
								   one these three went completely unchecked
								   (measured: the same decimal-comma file was
								   flagged in file_0 and passed silently in
								   file_1). Siblings may share an itemId; the
								   check reaches it through up(), not
								   queryById. */
								itemId: "mainFileSelector",
								helpTip: "Upload the quantification file (i.e. miRNA Quantification) or choose it from your data folder. See above the accepted format for the file."
							},
							{
								xtype: "myFilesSelectorButton",
								requiredTag: "optional",
								fieldLabel: 'Relevant regulators file',
								namePrefix: 'relevant_file_' + i,
								itemId: "moreRelevantFileSelector",
								helpTip: "Upload the list of relevant (differentially expressed) features (TAB format) or choose it from your data folder. See above the accepted format for the file."
							},
							{
								xtype: "myFilesSelectorButton",
								requiredTag: "optional",
								fieldLabel: 'Associations file',
								namePrefix: 'assoc_file_' + i,
								itemId: "moreAssociationsFileSelector",
								helpTip: "Upload the reference file that relates each feature (i.e. miRNA) with its potential targets. This information is usually extracted from popular databases such as miRbase for miRNAs. See above the accepted format for the file."
							},
							{
								// Per-omic low-variation filter (MORE `minVariation`).
								// Mirrors the field on the first regulatory omic above.
								xtype: 'numberfield',
								name: 'more_minvar_' + i,
								fieldLabel: 'Min. variation',
								value: 0,
								minValue: 0,
								step: 0.01,
								allowDecimals: true,
								allowBlank: false,
								helpTip: "Minimum change in standard deviation (numeric regulators) or proportion (binary) a regulator must show across conditions to avoid being filtered as low-variation. Applied to this regulatory omic only. 0 = keep all but constant regulators."
							}
						]);
						setTimeout(function() { initializeTooltips(".helpTip"); }, 100);
						}
					}]
				},
				{
					xtype: 'box',
					html: '<hr><h5>MORE Algorithm Parameters</h5>'
				},
				{
					/* One control for two decisions -- which model, and which
					   implementation runs it -- because to the person choosing they
					   are one decision. The three entries come from the server
					   (/more_backends), never from a list written here: whether an
					   engine can run depends on what is installed on THIS host, and
					   the deployed image carries R without the MORE package, so a
					   hardcoded list would offer two options that fail deep inside
					   the job.

					   `more_method` is still posted alongside `more_engine`, so a
					   server that predates the picker keeps working. */
					xtype: 'combo',
					itemId: "moreEngineField",
					name: 'more_engine',
					fieldLabel: 'Regulatory model',
					editable: false,
					allowBlank: false,
					displayField: 'label',
					valueField: 'id',
					queryMode: 'local',
					store: Ext.create('Ext.data.Store', {
						fields: ['id', 'label', 'method', 'engine', 'detail',
						         'available', 'unavailableReason']
					}),
					/* The unavailable entries stay in the list, greyed, rather than
					   being filtered out. "MLR is not offered here" and "MLR does not
					   exist" look identical when the row is simply absent, and the
					   first is a fixable installation problem the operator should be
					   able to see. */
					listConfig: {
						getInnerTpl: function() {
							return '<div class="more-engine-option ' +
								'{[values.available ? "" : "more-engine-unavailable"]}">' +
								'<span class="more-engine-label">{label}</span>' +
								'<tpl if="!available">' +
								'  <span class="more-engine-why">{unavailableReason}</span>' +
								'</tpl>' +
								'</div>';
						}
					},
					helpTip: "Which regression model finds the significant regulators, and which implementation runs it. Options this server cannot run are shown greyed with the reason.",
					listeners: {
						afterrender: function(combo) {
							loadMOREEngines(combo);
						},
						/* Refuse the selection rather than the submission. An
						   unavailable engine would be refused by the server anyway,
						   but only after the user has filled in the rest of the form. */
						beforeselect: function(combo, record) {
							if (record.get('available') === false) {
								showInfoMessage("Not available on this server",
									record.get('unavailableReason'));
								return false;
							}
						},
						change: function(combo, newValue) {
							var container = combo.up('#itemsContainer');
							var record = combo.getStore().findRecord('id', newValue);
							// The method now travels with the engine, so the show/hide
							// below keys off the selected entry rather than off the
							// combo's own value.
							var method = record ? record.get('method') : 'PLS1';
							var isPLS1 = method === 'PLS1';

							var methodField = container.queryById('moreMethodField');
							if (methodField) { methodField.setValue(method); }

							var detail = container.queryById('moreEngineDetail');
							if (detail) {
								// Empty whenever the offline fallback supplied the row,
								// because it carries no prose. Render nothing at all
								// rather than an empty <p>, which would reserve a line's
								// height for a sentence that is never coming -- and
								// rather than String(undefined), which is what
								// htmlEncode would have printed into the card.
								var description = record ? (record.get('detail') || "") : "";
								detail.update(description
									? '<p class="more-engine-detail">' +
									  Ext.String.htmlEncode(description) + '</p>'
									: '');
							}

							var alphaField = container.queryById('moreAlphaField');
							var vipField = container.queryById('moreVipField');
							// Disable as well as hide. Both fields are allowBlank:false,
							// and a merely hidden ExtJS field is still validated and still
							// submitted -- so clearing Alpha and then switching to MLR left
							// the form permanently invalid with "Invalid Form. Please check
							// the form errors." and no visible field to correct.
							// Disabling excludes them from validation and from the POST;
							// MORE's GetMLR takes neither alfa nor vip, so MLR loses
							// nothing, and MOREServlet defaults them when absent.
							//
							// MLR does not merely ignore them: MORE_MLR.R builds its
							// coefficients table with a single `coefficient` column and
							// no p-value at all, so there is nothing for alpha to
							// threshold. Selection there is shrinkage alone.
							[alphaField, vipField].forEach(function(field) {
								if (!field) { return; }
								field.setVisible(isPLS1);
								field.setDisabled(!isPLS1);
							});
						}
					}
				},
				{
					/* What the chosen engine actually does, under the picker rather
					   than inside a tooltip: the difference between the two PLS1
					   entries is entirely runtime, and the difference between PLS1 and
					   MLR is reproducibility, neither of which is guessable from a
					   label. */
					xtype: 'box',
					itemId: "moreEngineDetail",
					html: ''
				},
				{
					/* Posted for a server that predates `more_engine`; kept in step
					   with the picker by its change handler above. Hidden rather than
					   removed -- two controls for one choice is what the picker
					   replaced. */
					xtype: 'hiddenfield',
					itemId: "moreMethodField",
					name: 'more_method',
					value: 'PLS1'
				},
				{
					xtype: 'numberfield',
					itemId: "moreAlphaField",
					name: 'more_alpha',
					fieldLabel: 'Alpha (Significance)',
					value: 0.05,
					minValue: 0.0001,
					maxValue: 1,
					step: 0.01,
					allowDecimals: true,
					allowBlank: false,
					helpTip: "PLS1 only. Significance threshold for each regulator's coefficient, tested by jackknife resampling. A regulator must clear BOTH this and the VIP threshold to be reported, so lowering it alone will not necessarily shrink the results."
				},
				{
					xtype: 'numberfield',
					itemId: "moreVipField",
					name: 'more_vip',
					fieldLabel: 'VIP threshold',
					value: 0.8,
					minValue: 0,
					maxValue: 10,
					step: 0.1,
					allowDecimals: true,
					allowBlank: false,
					helpTip: "PLS1 only. Variable Importance in Projection: how much each regulator contributes to the components that explain the gene. The default of 0.8 is deliberately permissive; 1.0 is the conventional cutoff and is the first thing to raise if a run returns more regulators than you can interpret."
				},
				{
					xtype: 'numberfield',
					itemId: "moreR2Field",
					name: 'more_filter_r2',
					fieldLabel: 'R2 Filter',
					value: 0.0,
					minValue: 0,
					maxValue: 1,
					step: 0.1,
					allowDecimals: true,
					allowBlank: false,
					helpTip: "Discard any gene whose fitted model explains less than this fraction of its variance, before its regulators are reported. 0 keeps every model that converged. Raising it is the cheapest way to stop poorly-fitted genes contributing regulators to the pathway results."
				}]
			},
			{
				xtype: "container",
				itemId: "itemsContainerAlt",
				hidden: true,
				disabled: true,
				layout: {
					align: 'stretch',
					type: 'vbox'
				},
				padding: 10,
				defaults: {
					labelAlign: "right",
					labelWidth: 150,
					maxLength: 100
				},
				items: [{
					xtype: 'filefield',
					name: this.namePrefix + '_file_0',
					hidden: true
				}, {
					xtype: 'textfield',
					name: this.namePrefix + '_origin_0',
					value: 'mydata',
					hidden: true
				}, {
					xtype: 'textfield',
					name: this.namePrefix + '_file_type_0',
					value: 'Gene Expression file',
					hidden: true
				}, {
					xtype: 'filefield',
					name: this.namePrefix + '_relevant_file_0',
					hidden: true
				}, {
					xtype: 'textfield',
					name: this.namePrefix + '_relevant_0_origin',
					value: 'mydata',
					hidden: true
				}, {
					xtype: 'textfield',
					name: this.namePrefix + '_relevant_file_type_0',
					value: 'Relevant gene list',
					hidden: true
				}, {
					xtype: 'filefield',
					name: this.namePrefix + '_associations_file_0',
					hidden: true
				}, {
					xtype: 'textfield',
					name: this.namePrefix + '_associations_0_origin',
					value: 'mydata',
					hidden: true
				}, {
					xtype: 'textfield',
					name: this.namePrefix + '_associations_file_type_0',
					value: 'Associations file',
					hidden: true
				}, {
					xtype: 'filefield',
					name: this.namePrefix + '_relevant_associations_file_0',
					hidden: true
				}, {
					xtype: 'textfield',
					name: this.namePrefix + '_relevant_associations_0_origin',
					value: 'mydata',
					hidden: true
				}, {
					xtype: 'textfield',
					name: this.namePrefix + '_relevant_associations_file_type_0',
					value: 'Relevant associations file',
					hidden: true
				}, {
					xtype: 'textfield',
					fieldLabel: 'Omic Name',
					name: this.namePrefix + '_omic_name_0',
					hidden: true,
					itemId: "omicNameFieldAlt",
					value: this.omicName
				}, {
					xtype: 'textfield',
					fieldLabel: 'Output File',
					name: this.namePrefix + '_filelocation_0',
					hidden: true,
					itemId: "mainFileFieldAlt"
				}, {
					xtype: 'textfield',
					fieldLabel: 'Relevant Regulators File',
					name: this.namePrefix + '_relevant_filelocation_0',
					hidden: true,
					itemId: "secondaryFileFieldAlt"
				}, {
					xtype: 'textfield',
					fieldLabel: 'Associations File',
					name: this.namePrefix + '_associations_filelocation_0',
					hidden: true,
					itemId: "thirdFileFieldAlt"
				}, {
					xtype: 'textfield',
					fieldLabel: 'Relevant Associations File',
					name: this.namePrefix + '_relevant_associations_filelocation_0',
					hidden: true,
					itemId: "fourthFileFieldAlt"
				}, {
					xtype: 'textfield',
					fieldLabel: 'Config Vars',
					name: this.namePrefix + '_config_args_0',
					hidden: true,
					itemId: "configVarsFieldAlt"
				}, {
					xtype: 'textfield',
					fieldLabel: 'Enrichment type',
					name: this.namePrefix + '_enrichment_0',
					hidden: true,
					itemId: "enrichmentTypeFieldAlt",
					value: "genes"
				}, {
					xtype: 'textfield',
					hidden: true,
					fieldLabel: 'Map to',
					name: this.namePrefix + '_match_type_0',
					itemId: "matchTypeSelectorAlt",
					value: 'gene'
				}, {
					xtype: 'box',
					html: '<h5 style="color: #689F38;"><i class="fa fa-check"></i> Files processed correctly!</h5>'
				}]
			}],
			setContent: function(target, values) {
				var component = this.queryById(target);
				if (values.mainFile !== undefined) {
					component.queryById("mainFileFieldAlt").setValue(values.mainFile);
				}
				if (values.secondaryFile !== undefined) {
					component.queryById("secondaryFileFieldAlt").setValue(values.secondaryFile);
				}
				if (values.thirdFile !== undefined) {
					component.queryById("thirdFileFieldAlt").setValue(values.thirdFile);
				}
				if (values.fourthFile !== undefined && values.fourthFile !== null) {
					component.queryById("fourthFileFieldAlt").setValue(values.fourthFile);
				}
				if (values.title !== undefined) {
					component.queryById("omicNameFieldAlt").setValue(values.title);
				}
				if (values.configVars !== undefined) {
					component.queryById("configVarsFieldAlt").setValue(values.configVars);
				}
				if (values.enrichmentType !== undefined) {
					component.queryById("enrichmentTypeFieldAlt").setValue(values.enrichmentType);
				}

				// Multi-omic propagation. `setContent` is a method of the inner Ext
				// widget, so `this` here is the component, NOT the MORESubmittingPanel
				// — `this.namePrefix` would resolve to undefined and the alt fields
				// would be named "undefined_file_1" etc. Use the closure-captured
				// `me` (the panel) so the per-omic fields share the panel's prefix
				// and saveFiles can pair each omic 1+ with its omic_name/filelocation.
				if (values.response && values.response.omicsCount > 1) {
					for (var i = 1; i < values.response.omicsCount; i++) {
						component.add([
							{ xtype: 'filefield', name: me.namePrefix + '_file_' + i, hidden: true },
							{ xtype: 'textfield', name: me.namePrefix + '_origin_' + i, value: 'mydata', hidden: true },
							{ xtype: 'textfield', name: me.namePrefix + '_file_type_' + i, value: 'Gene Expression file', hidden: true },
							{ xtype: 'filefield', name: me.namePrefix + '_relevant_file_' + i, hidden: true },
							{ xtype: 'textfield', name: me.namePrefix + '_relevant_' + i + '_origin', value: 'mydata', hidden: true },
							{ xtype: 'textfield', name: me.namePrefix + '_relevant_file_type_' + i, value: 'Relevant gene list', hidden: true },
							{ xtype: 'filefield', name: me.namePrefix + '_associations_file_' + i, hidden: true },
							{ xtype: 'textfield', name: me.namePrefix + '_associations_' + i + '_origin', value: 'mydata', hidden: true },
							{ xtype: 'textfield', name: me.namePrefix + '_associations_file_type_' + i, value: 'Associations file', hidden: true },
							{ xtype: 'filefield', name: me.namePrefix + '_relevant_associations_file_' + i, hidden: true },
							{ xtype: 'textfield', name: me.namePrefix + '_relevant_associations_' + i + '_origin', value: 'mydata', hidden: true },
							{ xtype: 'textfield', name: me.namePrefix + '_relevant_associations_file_type_' + i, value: 'Relevant associations file', hidden: true },
							{ xtype: 'textfield', name: me.namePrefix + '_omic_name_' + i, hidden: true, value: values.response['omicName_' + i] },
							{ xtype: 'textfield', name: me.namePrefix + '_filelocation_' + i, hidden: true, value: values.response['mainOutputFileName_' + i] },
							{ xtype: 'textfield', name: me.namePrefix + '_relevant_filelocation_' + i, hidden: true, value: values.response['secondOutputFileName_' + i] },
							{ xtype: 'textfield', name: me.namePrefix + '_associations_filelocation_' + i, hidden: true, value: values.response['thirdOutputFileName_' + i] },
							{ xtype: 'textfield', name: me.namePrefix + '_relevant_associations_filelocation_' + i, hidden: true, value: values.response['fourthOutputFileName_' + i] },
							{ xtype: 'textfield', name: me.namePrefix + '_config_args_' + i, hidden: true, value: values.configVars },
							{ xtype: 'textfield', name: me.namePrefix + '_enrichment_' + i, hidden: true, value: values.enrichmentType },
							{ xtype: 'textfield', name: me.namePrefix + '_match_type_' + i, hidden: true, value: 'gene' }
						]);
					}

					// Surface the multi-omic outcome to the user. Without this they
					// only see "Files processed correctly!" and have no way to know
					// whether the second omic actually came through to PA Step 1.
					var processedNames = [values.title || values.response['omicName_0']];
					for (var k = 1; k < values.response.omicsCount; k++) {
						processedNames.push(values.response['omicName_' + k]);
					}
					component.add({
						xtype: 'box',
						html: '<p style="margin: 5px 155px; font-size: 12px; color: #689F38;">' +
						      '<i class="fa fa-check-circle"></i> ' + values.response.omicsCount +
						      ' regulatory omics processed: <b>' + processedNames.join(', ') + '</b></p>'
					});
				}

				var isVisible = component.isVisible();
				component.setVisible(!isVisible);
				component.setDisabled(isVisible);
		
				var mainContainer = this.queryById("itemsContainer");
				if (mainContainer) {
					mainContainer.setVisible(isVisible);
					mainContainer.setDisabled(!isVisible);
				}
				return this;
			},
			isValid: function() {
				var valid = true;
				var component = this.queryById("itemsContainerAlt");
				if (!component || !component.isVisible()) {
					component = this.queryById("itemsContainer");
				}
				if (!component) return false;
				valid = validateAllFields(component) && valid;
		
				if (component.queryById("conditionsFileSelector") && component.queryById("conditionsFileSelector").getValue() === "") {
					valid = false;
					component.queryById("conditionsFileSelector").markInvalid("Please, provide the conditions file.");
				}
				if (component.queryById("mainFileSelector") && component.queryById("mainFileSelector").getValue() === "") {
					valid = false;
					component.queryById("mainFileSelector").markInvalid("Please, provide a regulatory data file.");
				}
				if (component.queryById("rnaseqauxFileSelector") && component.queryById("rnaseqauxFileSelector").getValue() === "") {
					valid = false;
					component.queryById("rnaseqauxFileSelector").markInvalid("Please, provide the gene expression dataset.");
				}
				
				return valid;
			},
			isEmpty: function() {
				var component = this.queryById("itemsContainerAlt");
				if (component && component.isVisible()) {
					return false; // Already processed and has results
				}
				component = this.queryById("itemsContainer");
				if (component && component.queryById("mainFileSelector") && component.queryById("mainFileSelector").getValue() !== "") {
					return false;
				}
				return true;
			},
			listeners: {
				boxready: function() {
					initializeTooltips(".helpTip");
					$(this.getEl().dom).find("a.deleteOmicBox").click(function() {
						me.removeOmicSubmittingPanel();
					});
				}
			}
		});

		return this.component;
	};

	return this;
}
MORESubmittingPanel.prototype = new DefaultSubmittingPanel;

/*********************************************************************
 * AI PROVENANCE                                    ******************
 *********************************************************************
 * Names the recipient of the data in the consent surfaces.
 *
 * The provider, its host and the model are all chosen server-side by
 * AI_LLM_PROVIDER and are all env-overridable, so the browser cannot know any
 * of them. It used to not try: the consent label said "external AI service"
 * and the notice said "external LLM servers", which is the same amount of
 * information as saying nothing. The only string in the whole product that
 * named the gateway was the missing-API-key error, so a working install told
 * the user nothing about where their data went and a broken one told them
 * everything.
 *
 * /ai_provider answers it. One request, cached for the page, and every
 * placeholder that happens to be in the DOM is filled from the same answer.
 * If the request fails the placeholders keep the wording they shipped with,
 * which is vaguer but still true -- a consent notice must not degrade into a
 * claim the server has not confirmed.
 *********************************************************************/
var AI_PROVIDER_INFO = null;
var _aiProviderRequest = null;

/**********************************************************************
 * WHICH REGULATORY ENGINES THIS SERVER CAN RUN
 *
 * The picker offers four: PLS1 and MLR, each on the Rust engine or on R.
 * Which of them a given host can actually run is not a fact the client can
 * know -- the deployed image carries /usr/bin/Rscript and none of MORE,
 * optparse, ropls or glmnet, so a list hardcoded here would offer two options
 * that pass every check in the browser and then fail deep inside the job.
 *
 * /more_backends answers it, from a probe of the R *packages* rather than the
 * interpreter. One request, cached for the page. It also carries the LABEL and
 * the DESCRIPTION of every engine, and those are not restated here: see the
 * fallback below.
 *
 * The fallback matters as much as the success path: if the request fails, the
 * picker is still filled with every engine marked available, because a server
 * that cannot answer is more likely to be an old one that has no such route
 * than one with nothing installed -- and refusing every engine on a transport
 * error would take a working feature down. The server refuses an engine it
 * cannot run anyway (MOREServlet.engineRefusal), so the cost of being
 * optimistic here is a clear message at submission rather than a mislabelled
 * dropdown.
 **********************************************************************/
var MORE_BACKENDS = null;
var _moreBackendsRequest = null;

/* Used when the server cannot be asked. It carries STRUCTURE ONLY -- the ids,
   the method each one runs and the fact that it is offered -- and deliberately
   no prose.

   The wording lives in exactly one place, MOREServlet.MORE_ENGINES, and
   reaches the browser through /more_backends. The copy that used to sit here
   was a second source for the same descriptions and had already drifted from
   the first: it dropped the speed figure and the recommendation, and it still
   described MLR's collinearity representative as an unseeded draw, which
   more.R:162 seeds -- so the sentence was false in both copies.

   `label` is the combo's displayField and a row without one renders blank, so
   it is DERIVED from the method rather than written out; a hardcoded label
   would be the duplication this list exists to avoid. `detail` is left empty,
   and the description box renders nothing rather than a stale sentence: when
   the route cannot be reached this client genuinely does not know what this
   host runs, and saying nothing is the honest form of that.

   `engine` is carried, always "rust", because the store's model declares the
   field and the server still sends it. There is one engine now; the R half of
   this list went with the R half of the catalogue. */
var MORE_ENGINES_FALLBACK = ([
	{id: "rust-pls1", method: "PLS1"},
	{id: "rust-mlr",  method: "MLR"}
]).map(function (entry) {
	entry.engine = "rust";
	entry.available = true;
	entry.unavailableReason = "";
	entry.label = entry.method;
	entry.detail = "";
	return entry;
});

/* Fills `combo` from /more_backends, and selects the server's default. */
function loadMOREEngines(combo) {
	function fill(report) {
		if (!combo || combo.isDestroyed) { return; }
		var engines = (report && report.engines && report.engines.length)
			? report.engines : MORE_ENGINES_FALLBACK;
		combo.getStore().loadData(engines);

		/* The server names the default, and it is not always rust-pls1: on a
		   host with no binary the picker has to open on something runnable.
		   Falling back to the first available entry rather than to engines[0]
		   for the same reason. */
		var chosen = (report && report.default) || null;
		if (!chosen) {
			var runnable = Ext.Array.findBy(engines, function(entry) {
				return entry.available !== false;
			});
			chosen = runnable ? runnable.id : engines[0].id;
		}
		combo.setValue(chosen);

		if (report && report.anyAvailable === false) {
			showWarningMessage("No regulatory model can be run here", {
				text: "This server has no more-rs binary installed, so a " +
				      "regulatory analysis cannot be started. Please " +
				      "contact the administrator.",
				logMessage: "GET /more_backends reported no available engine."
			});
		}
	}

	if (MORE_BACKENDS !== null) { fill(MORE_BACKENDS); return; }
	if (_moreBackendsRequest === null) {
		/* Derived when the constant is absent rather than depending on it.
		   ServerConfiguration.js is one of the twelve hand-versioned scripts,
		   so a returning browser can hold a cached copy for up to 12 hours
		   after this view starts calling the route -- which is the exact
		   failure test_versioned_assets_are_bumped exists to catch, and the
		   one case where being defensive costs a line and saves a support
		   ticket. Both forms produce the same URL. */
		var url = (typeof SERVER_URL_MORE_BACKENDS !== "undefined")
			? SERVER_URL_MORE_BACKENDS
			: SERVER_URL + "more_backends";
		_moreBackendsRequest = $.ajax({type: "GET", url: url, dataType: "json"});
	}
	_moreBackendsRequest.done(function(response) {
		if (response && response.success === true && response.engines) {
			MORE_BACKENDS = response;
		}
		fill(MORE_BACKENDS);
	}).fail(function() {
		fill(null);
	});
}

/* Runs `callback(info)` once the provider description is known, immediately if
   it already is. Never calls back on failure: the fallback copy is what the
   markup already contains. */
function withAIProviderInfo(callback) {
	if (AI_PROVIDER_INFO !== null) {
		callback(AI_PROVIDER_INFO);
		return;
	}
	if (_aiProviderRequest === null) {
		_aiProviderRequest = $.ajax({
			type: "GET", url: SERVER_URL_AI_PROVIDER, dataType: "json"
		});
	}
	_aiProviderRequest.done(function (response) {
		if (!response || response.success !== true || !response.host) {
			return;
		}
		AI_PROVIDER_INFO = response;
		callback(response);
	});
}

/* Fills whichever provenance placeholders exist under `root` (the document by
   default). Called twice with different roots: once when step 1 renders, for
   the consent label and the callout, and again when the privacy notice is
   built, because that modal does not exist until the icon is clicked. */
function fillAIProvenance(root) {
	root = root || document;

	withAIProviderInfo(function (info) {
		/* getAIProviderInfo answers for a host it has no entry for by setting
		   `operator` and `summary` to the bare hostname (AIInterpretServlet.py
		   :112-113). That is the right thing for it to do - the hostname is
		   still true - but the sentences below were written as though the two
		   were always distinct, so an unrecognised gateway printed its own name
		   twice: "llm.example.org (llm.example.org)" in the consent label and
		   "send the data to llm.example.org - llm.example.org" in the notice.
		   Reads as a bug in a paragraph whose whole job is to be trusted. */
		var operatorIsHost = !info.operator || info.operator === info.host;
		var summaryIsHost = !info.summary || info.summary === info.host;

		/* The model identifier is deliberately not shown anywhere in the
		   interface. /ai_provider still reports it -- it is a true fact about
		   the configuration and other callers may want it -- but naming a
		   specific build in consent copy invites the reader to evaluate the
		   model rather than the decision in front of them, and the string goes
		   stale the moment the gateway is repointed. Who operates the endpoint
		   and where it runs is what a consent decision actually turns on. */

		var name = root.querySelector("#aiProviderName");
		if (name) {
			/* Deliberately the operator and the host rather than a phrase like
			   "a CSIC gateway": the host is the checkable fact, and this label
			   is the one piece of consent copy every user reads. */
			name.textContent = operatorIsHost
				? info.host
				: info.operator + " (" + info.host + ")";
		}

		var inline = root.querySelector("#aiProviderInline");
		if (inline) {
			/* The short form of info.summary, which is a full sentence naming the
			   institute -- 173 characters, four lines in a 302px column, and this
			   line sits on a panel the owner has already called too tall twice.
			   The host is the checkable fact and the operator is who answers for
			   it; the sentence-length version is in the notice this line links to,
			   one click away, with the articles and the never-submit list. */
			/* Parenthesised whole, on the owner's call: it is an aside to the
			   heading above it, not a statement of its own. The operator takes a
			   dash rather than its own brackets -- "(Sent to llm.iiia.es
			   (IIIA-CSIC), in the EU)" nests two pairs in eight words. */
			inline.textContent = "(Sent to " + info.host
				+ (operatorIsHost ? "" : " \u2014 " + info.operator)
				+ (info.inEU ? ", in the EU)" : ")");
			/* The column was measured before this text existed, and an ExtJS hbox
			   measures once: without this the sentence lays out below the panel's
			   own bottom edge, in the DOM and nowhere on screen. Exactly the bug
			   documented at setNote() in initExpDesignDraft, in the column beside
			   this one. */
			var noteBox = (typeof Ext !== "undefined")
				? Ext.ComponentQuery.query("#aiWhatYouGetNote")[0]
				: null;
			if (noteBox && noteBox.updateLayout) { noteBox.updateLayout(); }
		}

		var where = root.querySelector("#aiGdprWhere");
		if (where) {
			where.innerHTML = "<strong>Where it runs:</strong> this server is configured to send "
				+ "the data to <strong>" + Ext.String.htmlEncode(info.host) + "</strong>"
				+ (summaryIsHost ? "" : " &mdash; " + Ext.String.htmlEncode(info.summary))
				+ ".";
		}

		var transfer = root.querySelector("#aiGdprTransfer");
		if (transfer) {
			/* The page used to assert GDPR Chapter V unconditionally -- Articles
			   44-49, "non-EU processors", Standard Contractual Clauses -- which
			   does not describe the default configuration at all, and reads as
			   boilerplate nobody updated. Stated from the configuration
			   instead, and left blank where the server cannot say, because a
			   guess about which legal regime applies is worse than silence. */
			if (info.inEU === true) {
				transfer.innerHTML = "<strong>Transfers:</strong> this deployment sends the data to "
					+ "an endpoint operated within the EU, so Chapter V of the GDPR "
					+ "(Articles 44&ndash;49, transfers to third countries) does not apply to it.";
			} else if (info.inEU === false) {
				transfer.innerHTML = "<strong>Transfers:</strong> this deployment sends the data "
					+ "outside the EU. Under GDPR Articles 44&ndash;49, transferring identifiable "
					+ "personal data to a non-EU processor requires appropriate safeguards, such as "
					+ "Standard Contractual Clauses.";
			}
		}
	});
}

/*****************************************************************************
**** "DRAFT THIS FOR ME" - EXPERIMENT DESIGN *********************************
*****************************************************************************/
/* Reads the header row of each file the user has picked, sends just those names
   to /ai_generate_exp_design, and writes the reply into the experiment design
   box.

   Only the first line of each file is read, and nothing is read from disk until
   the button is pressed. Every picked file is taken: one line each costs
   nothing, and each one says something about the design the others do not --
   a conditions file names the factors, an associations file says the study is
   regulatory, a values matrix names the samples. */

/* Enough of the file to be sure of catching the first newline. A header row for
   a wide matrix can be long, but not this long, and reading a fixed slice keeps
   a multi-gigabyte upload from being pulled through memory to find one line. */
var EXP_DESIGN_HEADER_BYTES = 262144;

/* Reads the first line of `file` and calls back with its tab/comma separated
   fields. Calls back with [] rather than failing: one unreadable file among
   several should still let the others through. */
function readHeaderRow(file, callback) {
	var reader = new FileReader();
	reader.onload = function(e) {
		var text = String(e.target.result || "");
		/* Whichever newline the file uses, and the first one wins -- a file
		   with no newline at all inside the slice is treated as one long
		   header, which the server's length caps then bound. */
		var line = text.split(/\r\n|\r|\n/)[0] || "";
		// A leading '#' is how these files mark the header (see the example
		// datasets: "#geneID\tT00h\t..."), and it is not part of the name.
		line = line.replace(/^#/, "");
		if (!line.trim()) { callback([]); return; }
		// Tab is the documented separator; comma is the common mistake, and
		// falling back to it costs nothing when there is no tab to split on.
		var parts = (line.indexOf("\t") !== -1) ? line.split("\t") : line.split(",");
		callback(parts);
	};
	reader.onerror = function() { callback([]); };
	reader.readAsText(file.slice(0, EXP_DESIGN_HEADER_BYTES));
}

/* Plain text out of a fieldLabel, which may carry markup
   ('Relevant regulators file<br>(optional)') and a trailing colon. */
function plainFieldLabel(text) {
	return String(text == null ? "" : text)
		.replace(/<[^>]*>/g, " ")
		.replace(/\s+/g, " ")
		.replace(/^ | $/g, "")
		.replace(/\s*:$/, "");
}

/* What one picked file is: which panel it sits in, and which slot of it.
   "Regulatory Omic - MORE / Conditions file", "Gene expression / Data file".

   Read off the component tree, never off the field's name. Every panel agrees
   on its itemIds and its labels and disagrees on its field NAMES, which is the
   whole of the bug this replaces. */
/* The omic name the user typed for this file's panel, or "".

   Two traps, both measured in Chrome. A region or pairwise card holds TWO
   #omicNameField -- the first is the disabled, empty twin of the "already
   mapped" alternative form -- so panel.down('#omicNameField') returned the
   twin and the name the user typed never reached the label. And MORE stacks
   regulators: block i names its files file_i / relevant_file_i / assoc_file_i
   and its omic name omic_name_i, with no itemId at all, so every file of the
   2nd regulator was labelled with the 1st regulator's name. The conditions and
   gene-expression (rnaseqaux) files belong to the whole MORE panel, not to a
   regulator: they take the panel heading. */
function typedOmicName(panel, field) {
	var fieldName = String((field && field.name) || "");
	var block = fieldName.match(/^(?:file|relevant_file|assoc_file)_(\d+)(?:_file)?$/);
	if (block) {
		var combos = panel.query ? panel.query('[name=omic_name_' + block[1] + ']') : [];
		var typed = (combos.length && combos[0].getValue) ? combos[0].getValue() : "";
		return typed ? String(typed).trim() : "";
	}
	if (/^(?:conditions|rnaseqaux)(?:_file)?$/.test(fieldName)) { return ""; }

	var fields = panel.query ? panel.query('#omicNameField') : [];
	var live = fields.filter(function (f) { return !(f.isDisabled ? f.isDisabled() : f.disabled); });
	var pool = live.length ? live : fields;
	for (var i = 0; i < pool.length; i++) {
		var value = pool[i].getValue && pool[i].getValue();
		if (value && String(value).trim()) { return String(value).trim(); }
	}
	return "";
}

function pickedFileLabel(field) {
	var panel = field.up('[cls~=omicbox]');
	var selector = field.up('myFilesSelectorButton');
	var slot = selector ? plainFieldLabel(selector.fieldLabel) : "";
	var omic = "";

	if (panel) {
		/* What the user typed, when they typed one; otherwise the heading the
		   panel prints, which every panel type renders the same way. */
		omic = typedOmicName(panel, field);
		if (!omic && panel.el && panel.el.dom) {
			var heading = panel.el.dom.querySelector('.omicboxTitle h4');
			omic = heading ? plainFieldLabel(heading.textContent) : "";
		}
		if (!omic) { omic = panel.type || ""; }
	}

	if (omic && slot) { return omic + " / " + slot; }
	return omic || slot || field.name || "file";
}

/* A form cannot hand over an unbounded number of files. Six omics with four
   selectors each is 24, which is past anything the form is used for. */
var EXP_DESIGN_MAX_FILES = 24;

/* Every file the user has picked, as [{omicName, file}].
 *
 * This used to keep only fields whose NAME matched /^omic\d+_file$/. That is
 * the plain, region-based and miRNA panels' convention and it is not the MORE
 * panel's: MORE names its five selectors conditions, rnaseqaux, file_0,
 * relevant_file_0 and assoc_file_0. So a form holding a MORE panel and four
 * chosen files collected NOTHING, and "Draft this for me" answered "There is
 * nothing to read yet. Add an omic ... and pick its Data file" to a user who
 * had done exactly that. Reported 2026-08-26; MORE was the only panel affected,
 * and the file it most wanted -- the conditions file -- is the experimental
 * design itself.
 *
 * Nothing about any panel's field names is encoded here now, so a fifth panel
 * type cannot go blind the same way.
 */
function collectPickedOmicFiles() {
	var picked = [];
	Ext.each(Ext.ComponentQuery.query('filefield'), function(field) {
		if (picked.length >= EXP_DESIGN_MAX_FILES) { return false; }

		var input = field.fileInputEl && field.fileInputEl.dom;
		if (!input || !input.files || input.files.length === 0) { return; }

		/* The region panel's GTF: its first line is an annotation record or a
		   "#!genome-build" comment, not column names -- nothing for a design
		   drafter to read, and the one file the old name filter excluded on
		   purpose. */
		var selector = field.up('myFilesSelectorButton');
		if (selector && selector.itemId === 'tertiaryFileSelector') { return; }

		picked.push({
			omicName: pickedFileLabel(field),
			file: input.files[0]
		});
	});
	return picked;
}

/* The readable half of a servlet error.

   ServerErrorManager answers a UserWarning with `message`, prefixed by the file
   and function it was raised in:

       "UserWarning: AT AIInterpretServlet.py: aiGenerateExpDesign. ERROR
        MESSAGE: Enable AI pathway interpretation first. ..."

   Everything before "ERROR MESSAGE:" is for the log, not for the person who
   pressed the button. Same split UserController.js already does on the sign-in
   path, with a fallback for the shapes that carry no message at all. */
function serverErrorText(response, fallback) {
	if (!response) { return fallback; }
	var raw = response.errorMessage || response.message || "";
	var split = raw.split("ERROR MESSAGE:");
	var text = (split.length > 1 ? split[1] : raw).trim();
	return text || fallback;
}

/* Grows the experiment design field to fit what is now in it.

   The field opens at 96px, which is right for an empty box and wrong for a
   draft: measured against the STATegra headers, a real one was 510 characters
   and wanted 153px, so it would have opened scrolled with its last two lines
   hidden -- and the first thing anyone does with a generated draft is read it.

   Capped, because the model is not bounded to three sentences and a 2000
   character reply would otherwise push the upload form off the screen. Past
   the cap it scrolls, which is the correct behaviour for text that long.
   `resize: vertical` in main.css still lets it be dragged either way. */
var EXP_DESIGN_MIN_HEIGHT = 96;
var EXP_DESIGN_MAX_HEIGHT = 260;

function growExpDesignToFit(field, box) {
	var input = field.inputEl && field.inputEl.dom;
	if (!input) { return; }

	/* scrollHeight only reports the content height once the element is not
	   already taller than it, so shrink to the minimum before measuring --
	   otherwise a field grown for a previous draft never shrinks back. */
	field.setHeight(EXP_DESIGN_MIN_HEIGHT);

	/* setHeight sizes the *component*, which here is the "Experiment design
	   (optional)" label stacked above the input, while scrollHeight is the
	   input's alone. Passing one to the other leaves the field short by the
	   height of the label every time: measured, a 581-character draft wanted
	   172px, got 155, and stayed scrolled. So measure the difference rather
	   than assuming a value for it -- labelAlign is a config someone may
	   change, and a hardcoded 20 would go quietly wrong when they do. */
	var chrome = field.getHeight() - input.offsetHeight;
	var needed = input.scrollHeight
	           + (input.offsetHeight - input.clientHeight)   // borders
	           + chrome;                                     // label and spacing
	field.setHeight(Math.max(EXP_DESIGN_MIN_HEIGHT,
	                         Math.min(needed, EXP_DESIGN_MAX_HEIGHT)));
	if (box && box.updateLayout) { box.updateLayout(); }
}

/* Wires the button inside `box` to the consent checkbox and to the request. */
function initExpDesignDraft(box) {
	var button = document.getElementById("expDesignDraftBtn");
	var note = document.getElementById("expDesignDraftNote");
	if (!button || !note) { return; }

	var consent = Ext.ComponentQuery.query('[name=aiConsent]')[0];
	var designField = Ext.ComponentQuery.query('[name=experimentDesign]')[0];
	if (!consent || !designField) { return; }

	/* The button says why it is off rather than just being off. An unexplained
	   disabled control on a form this long reads as a broken build.

	   `aiConsent` is a hidden field carrying the string 'true' now that the form
	   no longer asks (see the note on it in the view), so both shapes are
	   accepted: the checkbox answered with a boolean, and putting it back must
	   not silently disable this button. In the shipped configuration this is
	   always on -- the branch survives for a build that restores the box. */
	function syncEnabled() {
		var value = consent.getValue();
		var on = (value === true || value === "true");
		button.disabled = !on;
		button.title = on
			? "Reads the column names from the files you picked and asks the AI service to describe the design"
			: "Enable the AI pathway interpretation above to use this";
	}
	syncEnabled();
	consent.on('change', syncEnabled);

	button.addEventListener("click", function(e) {
		e.preventDefault();
		if (button.disabled) { return; }

		var picked = collectPickedOmicFiles();
		if (picked.length === 0) {
			/* A loaded example has no browser-readable files: its pickers are
			   disabled "[example dataset]" labels and the files live on the
			   server. Naming the scenario lets the servlet read the same
			   header rows there, instead of dead-ending the flagship demo in
			   the "choose your files" warning below. */
			var step1View = (typeof application !== "undefined" && application.getMainView)
				? application.getMainView().getSubView("PA_Step1JobView")
				: null;
			if (step1View && step1View.isExampleMode && step1View.isExampleMode()) {
				button.disabled = true;
				setNote("working", "Reading the example dataset’s column names…");
				send([], step1View.getExampleScenarioId() || "");
				return;
			}
			/* The ask the user made explicitly: say so in a window rather than
			   leaving the button to do nothing. Deliberately showWarningMessage
			   and not confirm() -- a native modal blocks the page. */
			showWarningMessage("Choose your data files first", {
				message: "There is nothing to read yet. Add an omic under " +
				         "[b]3. Choose the files to upload[/b] and pick its " +
				         "[b]Data file[/b], then press [b]Draft this for me[/b] " +
				         "again.[br][br]The draft is written from the column " +
				         "names in the first row of those files.",
				showButton: true
			});
			return;
		}

		button.disabled = true;
		setNote("working", "Reading your column names…");

		/* Each file is read asynchronously and the request waits for all of
		   them; `pending` is the join. Reading is local, so this is fast even
		   for several files. */
		var omics = [], pending = picked.length;
		Ext.each(picked, function(entry) {
			readHeaderRow(entry.file, function(columns) {
				if (columns.length) {
					omics.push({omicName: entry.omicName, columns: columns});
				}
				if (--pending === 0) { send(omics); }
			});
		});
	});

	/* `exampleScenarioId` is only ever passed on the example path: any string
	   (including "" for the server's default scenario) tells the servlet to
	   read the headers from that scenario's own files. */
	function send(omics, exampleScenarioId) {
		var fromExample = (exampleScenarioId !== undefined);
		if (omics.length === 0 && !fromExample) {
			finish("error", "Could not read a header row from those files.");
			return;
		}

		setNote("working", "Asking the AI service…");

		var combo = Ext.ComponentQuery.query('#speciesCombobox')[0];
		var url = (typeof SERVER_URL_AI_GENERATE_EXP_DESIGN !== "undefined")
			? SERVER_URL_AI_GENERATE_EXP_DESIGN
			: SERVER_URL + "ai_generate_exp_design";

		var payload = {
			aiConsent: "true",
			organism: combo ? (combo.getValue() || "") : "",
			omics: JSON.stringify(omics)
		};
		if (fromExample) {
			payload.exampleMode = "true";
			payload.exampleScenario = exampleScenarioId;
		}

		$.ajax({
			type: "POST", url: url, dataType: "json",
			data: payload
		}).done(function(response) {
			if (!response || response.success !== true || !response.suggestion) {
				finish("error", serverErrorText(response,
					"The AI service did not return a description."));
				return;
			}
			var previous = designField.getValue();
			designField.setValue(response.suggestion);
			growExpDesignToFit(designField, box);

			/* Says what left the machine, in the number the user can check
			   against their own file, and offers the box back the way it was.

			   Kept to one line. The first version added "Edit it, or add what
			   the comparison means", which wrapped the note onto a second row
			   and grew the callout by exactly as much as trimming the intro
			   paragraph had just saved -- and the caption beside the button
			   already says what the field is for. */
			var sent = 0;
			Ext.each(response.columnsSent || [], function(o) { sent += o.columns.length; });
			finish("done", "Drafted from " + sent + " column name" +
				(sent === 1 ? "" : "s") + " — no values were sent.");

			var undo = document.createElement("a");
			undo.href = "javascript:void(0)";
			undo.className = "po-exp-design-undo";
			undo.textContent = previous ? "Undo" : "Clear";
			undo.addEventListener("click", function() {
				designField.setValue(previous || "");
				/* Re-fit rather than restore a height saved before the draft.
				   Saving one was the first attempt and it crept: getHeight()
				   and setHeight() do not round-trip on this field, so every
				   draft-then-clear cycle left it 7px taller than the last --
				   measured 84, 91, 98, 105 over three cycles. Re-fitting is
				   idempotent, because it derives the height from the content
				   that is actually there now. */
				growExpDesignToFit(designField, box);
				setNote(null, "");
			});
			note.appendChild(document.createTextNode(" "));
			note.appendChild(undo);
		}).fail(function(xhr) {
			/* A UserWarning from the servlet arrives here, not in done(): the
			   error handler answers 400, so jQuery treats a perfectly
			   well-formed refusal as a transport failure. The body is the
			   readable half. */
			var parsed = null;
			try { parsed = JSON.parse(xhr.responseText); } catch (err) { /* not JSON */ }
			finish("error", serverErrorText(parsed, "Could not reach the AI service."));
		});
	}

	/* Writes the status line and lets the callout grow to hold it.

	   The updateLayout is not decoration. ExtJS's hbox measures this section
	   once, at render, and writes the result to .po-ai-section-body as an
	   inline `height: 251px` -- a stylesheet cannot reach it and `overflow:
	   visible` does not save it. The note is empty at that moment, so the two
	   lines it grows to when the draft returns were laid out 25px *below* the
	   panel's own bottom edge, outside its background and behind the block
	   underneath: the text was in the DOM, `offsetParent` was set, and it was
	   nowhere on screen. Measured before and after -- bottom 511 against a
	   panel ending at 487, and after this call 525 against 543.

	   Called on every state change rather than only on success, because the
	   line shrinks as well as grows and a panel left tall around one line of
	   "Reading your column names..." is the same bug facing the other way. */
	function setNote(state, message) {
		note.className = "po-exp-design-draft-note" + (state ? " is-" + state : "");
		note.textContent = message;
		if (box && box.updateLayout) { box.updateLayout(); }
	}

	function finish(state, message) {
		setNote(state, message);
		syncEnabled();
	}
}

//# sourceURL=PA_Step2Views.js
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
* - PA_Step2JobView
* - PA_Step2ReplicateDetectionView
* - PA_Step2CompoundSetView
* - PA_OmicSummaryPanel
*
*/
//Ext.require('Ext.chart.*');

/**
* Normalises the "mapped" slot of an omic summary (omicSummary[0]) into a
* matched-feature count per database.
*
* That slot arrives in two different shapes from the server:
*
*   - Gene based omics: a dict of feature-table name -> matched count, e.g.
*     {"mmu_kegg_genes": 5620, "mmu_reactome_genes": 2827, "Total": 6103}.
*     The table name embeds the database name, and "Total" holds the unique
*     count across every database.
*
*   - Compound based omics: a plain integer. Compounds are matched once
*     against KEGG compound IDs and that single set backs every database, so
*     the server has no per-database breakdown to report.
*
* Indexing the dict shape into an integer yields undefined, which is how the
* "Multiple databases used" table came to print "undefined (NaN%)" for the
* metabolomics omic.
*
* @param {Object|Number} mappedSummary the omicSummary[0] value
* @param {Array} databases names of the databases used in this analysis
* @returns {Object} {perDatabase: {dbname: count}, totalMapped: count}
*/
function matchedFeaturesByDatabase(mappedSummary, databases) {
	var perDatabase = {};

	// Compound based omic: one count, shared by every database.
	if (typeof mappedSummary === "number") {
		databases.forEach(function (dbname) {
			perDatabase[dbname] = mappedSummary;
		});
		return {perDatabase: perDatabase, totalMapped: mappedSummary};
	}

	var tableNames = Object.keys(mappedSummary || {});

	databases.forEach(function (dbname) {
		var featureTable = tableNames.find(function (el) {
			return el.indexOf(dbname) !== -1;
		});
		// An omic with no table for this database matched nothing in it.
		perDatabase[dbname] = (featureTable !== undefined) ? mappedSummary[featureTable] : 0;
	});

	// "Total" is the de-duplicated count across databases; with a single
	// database there is no "Total" entry and the only table is the total.
	var totalMapped = 0;
	if (mappedSummary && mappedSummary.hasOwnProperty("Total")) {
		totalMapped = mappedSummary["Total"];
	} else if (tableNames.length > 0) {
		totalMapped = mappedSummary[tableNames[0]];
	}

	return {perDatabase: perDatabase, totalMapped: totalMapped};
}

function PA_Step2JobView() {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step2JobView";
	this.items = [];

	/*********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.loadModel = function(jobModel) {
		if (this.model !== null) {
			this.model.deleteObserver(this);
		}

		this.model = jobModel;

		// JobController.showJobInstance re-loads the model into an existing view
		// on its "force" path, so the list has to be rebuilt from scratch or the
		// second load stacks a duplicate card set on top of the first.
		this.items = [];

		var foundCompounds = this.model.getFoundCompounds();
		var compoundSetView = null;
		for (var i in foundCompounds) {
			compoundSetView = new PA_Step2CompoundSetView();
			compoundSetView.loadModel(foundCompounds[i]);
			// Only the sets that still need a decision get a card. The *model*
			// keeps every set: PA_Step3Views gates Hub Analysis and Class
			// Activity on model.foundCompounds.length, and getSelectedCompounds()
			// reads the model, so the auto-resolved sets still reach the server
			// with their selection intact. Nothing observes a CompoundSet
			// (nothing ever calls notifyObservers on one), so registering 6592
			// dead observers - and logging one line per registration - is pure
			// cost and is gone.
			if (compoundSetView.needsDisambiguation()) {
				this.items.push(compoundSetView);
			}
		}
	};

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.initComponent = function() {
		var me = this;

		var dataDistribution = me.getModel().getDataDistributionSummaries(), aux = null;

		/* The per-omic cards are collected apart from the panel array and
		   inserted below, straight after "Multiple databases used". Pushed
		   inline they always ended up last, because every config box that
		   follows is spliced in at a fixed index in front of them -- so the
		   databases card's own "The diagrams below..." pointed past the
		   cluster and class-activity boxes at cards ~1600px further down. */
		var omicCards = [];

		var omicSummaryPanelComponents = [{
			xtype: 'box',
			cls: "contentbox omicSummaryBox", minHeight: 240,
			html: '<div id="about">' +
			// The heading's tip carries the long form. The body used to repeat
			// most of it across four lines -- an overview sentence that only said
			// "below is an overview", a rule of thumb, an instruction, and a
			// paragraph on comparing databases that restated this tip almost
			// word for word. Two lines say the same things once.
			'  <h2 >Feature ID/name translation summary <span class="helpTip" title="The percentage of your input features (names or identifiers) that could be translated into the identifier each database is keyed on - for example an NCBI Gene ID for KEGG, or a UniProt accession for OmniPath. A feature counts as soon as its name resolves, even if it belongs to no pathway in that database, so these figures are not a ranking - Step 3 reports pathway coverage."></h2>' +
			'  <p>' +
			'    How many of your features resolved into each database\'s identifiers. ' +
			'The more that map, the more there is to work with later &mdash; if one ' +
			'looks low, check that file\'s identifiers.<br>' +
			'    <b>Not a ranking:</b> the databases use different identifier types ' +
			'and differ in scope by design. Step 3 reports pathway coverage.<br><br>' +
			((Object.keys(dataDistribution).length > 0) ? '  <a href="javascript:void(0)" id="download_mapping_file"><i class="fa fa-download"></i> Download ID/Name mapping results.</a>' : "") +
			'  </p>' +
			'</div>'
		},
		{
			xtype: 'box',
			cls: "contentbox omicSummaryBox", minHeight: 240,
			html: '<div id="about">' +
			// Was title=" ": the icon rendered, invited a hover, and showed an
			// empty tooltip.
			'  <h2 >Data distribution summary <span class="helpTip" title="How each omic\'s values are spread across your samples. The box is the interquartile range, the red line the median, and the whiskers reach the 10th and 90th percentiles - the same two percentiles the heatmap colour scale uses by default."></h2>' +
			'  <p>' +
			'    By default, percentiles 10 and 90 set the reference range for the heatmap colours. You can change this in the pathway view: open <b>Settings</b> in the toolbar and edit <b>Reference values</b>.<br>' +
			// This figure replaces settingsbutton.png, a 2022 screenshot of the
			// old dark Bootstrap toolbar. It is not an image at all: it is the
			// step-4 toolbar's own markup, styled by the same .button rules the
			// real toolbar uses (extended to .paToolbarMiniature in main.css and
			// dark.css), so it renders as vector text at any zoom, follows the
			// theme, and cannot fall out of date when the toolbar is restyled.
			// Order matches PA_Step4Views' secondTopToolbar; the ring marks the
			// Settings button the caption above is pointing at.
			'		 <div class="paToolbarMiniature" aria-hidden="true">' +
			'			<span class="button btn-danger paMiniatureTarget"><i class="fa fa-wrench"></i> Settings</span>' +
			'			<span class="button btn-info"><i class="fa fa-search"></i> Search</span>' +
			'			<span class="button btn-secondary"><i class="fa fa-th"></i> Show Heatmap</span>' +
			'			<span class="button btn-primary"><i class="fa fa-sitemap"></i> Show Pathway</span>' +
			'			<span class="button btn-default"><i class="fa fa-arrow-left"></i> Go back</span>' +
			'		 </div>' +
			'  </p>' +
			'</div>'
		}];

		/* INFO PANEL ABOUT DATABASES USED */
		var databases = me.getModel().getDatabases();
		var compoundOmics = me.getModel().getCompoundBasedInputOmics().map(x => x.omicName);
		var matchingPerDB = {};
		var numberOfClusters = [];
		var thresholdMetaboliteClass = [];

		/* The compound omic the class activity test reads: the server takes the
		   first with values, which is the first listed. */
		var classActivityOmic = ((me.getModel().getCompoundBasedInputOmics() || [])[0] || {}).omicName || compoundOmics[0];
		for (var omicName in dataDistribution) {
			var isCompoundBased = (compoundOmics.indexOf(omicName) > -1);

			// Matched counts per database, plus the unique count across all of
			// them; handles both the gene based (dict) and compound based
			// (integer) shapes of the summary.
			var matched = matchedFeaturesByDatabase(dataDistribution[omicName][0], databases);

			// Total input features = unmapped + uniquely mapped.
			var totalFeatures = dataDistribution[omicName][1] + matched.totalMapped;

			databases.forEach(function(dbname) {
				var matchedCount = matched.perDatabase[dbname];

				matchingPerDB[dbname] = $.extend(matchingPerDB[dbname] || {}, {
					[omicName]: {
						"matched": matchedCount,
						// An omic with no input features must not divide by zero.
						"percentage": (totalFeatures > 0) ? Math.ceil(matchedCount / totalFeatures * 100) : 0
					}});
			});

			if (!isCompoundBased) {
				numberOfClusters.push({
					xtype: 'combo',
					fieldLabel: omicName,
					name: 'clusterNumber:' + omicName,
					value: 'dynamic',
					displayField: 'name', valueField: 'value',
					editable: false,
					allowBlank: false,
					/* ExtJS aligns field labels right by default, so a 300px
					   label column pushed "Gene expression:" 198px away from
					   the card's own left edge - the heading and the paragraph
					   above it start there, and the one control the card exists
					   to offer did not. Left labels put the text on that rail,
					   and 240px is the width of the longest of them
					   ("Metabolite class activity threshold") with room to
					   spare, so every row's field starts on one edge too. */
					labelAlign: 'left',
					labelWidth: 240,
					width: 300,
					store: Ext.create('Ext.data.ArrayStore', {
						fields: ['name', 'value'],
						data: [
							['Generate automatically', 'dynamic'],
							['One cluster', 1],
							['Two clusters', 2],
							['Three clusters', 3],
							['Four clusters', 4],
							['Five clusters', 5],
							['Six clusters', 6],
							['Seven clusters', 7],
							['Eight clusters', 8],
							['Nine clusters', 9],
							['Ten clusters', 10],
						]
					}),
					helpTip: "Define the number of clusters per omic or let the program calculate them dynamically using silhouette."
				});
			}

			if (isCompoundBased && omicName === classActivityOmic) {
				/* One box per JOB, not per omic. The server runs the test on one
				   compound omic (_compoundOmicForClassActivity: the first with
				   values), and two boxes posted two fields under one name, which
				   jQuery sent as thresholdMetaboliteClass[] and the servlet's
				   .get() never found -- the competitive null ran while the form
				   showed 0.05. */
				thresholdMetaboliteClass.push({
					xtype: 'container',
					itemId: 'classActivityBox',
					omicName: omicName,
					/* A vbox, not the default autocontainer layout, under which a
					   form item is placed by its INPUT and the label hung off the
					   card. `left`, not `stretch`: every item carries its own
					   width (660 / 634), because a width that is only known
					   after the layout resolves is a width the plan block's text
					   gets measured WITHOUT -- wrapped in a sliver, 901px tall,
					   and the card published that before the second pass
					   corrected it. Measured, not theorised. */
					layout: {type: 'vbox', align: 'left'},
					items: paClassActivityItems(me.getModel(), omicName)
				});
			}
			omicCards.push(new PA_OmicSummaryPanel(omicName, dataDistribution[omicName], isCompoundBased).getComponent());
		}

		if (numberOfClusters.length) {
			/* Add an empty container to restore "odd" position of next sibling elements */
			omicSummaryPanelComponents.splice(2, 0, {
				xtype: 'container',
				layout: {type: 'vbox', align: 'stretch'},
				cls: "contentbox", minHeight: 240, id: "clusternumber_box",
				items: [{
					html: '<h2 style="width: 100%;">Configure the number of clusters</h2>'
				}, {
					html: '<p>In the next step PaintOmics will calculate the clusters present in the data provided for each omic, using k-means with either an automatically calculated number of clusters or the ones you define here. You will also be able to modify them there by selecting individual omics in the network.<br><br></p>'
				},{
					xtype: 'form',
					maxWidth: 600,
					bodyCls: "divForm",
					/* 20, which reads as --pa-card-inset: ExtJS leaves 6px of row
					   spacing below the last field inside the form panel, so 26 here
					   put 33px under the last input where the two cards beside it
					   leave 25. Measured from the input's own edge, not the panel's -
					   the ink is what a reader sees the card end below. */
					style: "margin: 0 auto 20px auto;",
					layout: {type: 'vbox', align: 'stretch'},
					defaults: {labelAlign: "right", border: false},
					items: numberOfClusters
				}]
			}, {xtype: 'container', cls: 'paLayoutPad', html:'<div style="display: none;"></div>'});
		}

		if (databases.length > 1) {

			var dbs_descriptions = {
				"KEGG": '<a href="http://www.kegg.jp/kegg/" target="_blank">Kyoto Encyclopedia of Genes and Genomes</a> is a database resource for understanding high-level functions and utilities of the biological system, such as the cell, the organism and the ecosystem, from molecular-level information, especially large-scale molecular datasets generated by genome sequencing and other high-throughput experimental technologies.',
				"MapMan": 'Oriented towards plant species, in combination with <a href="http://www.gomapman.org/" target="_blank">GoMapMan</a>, it provides additional pathways as well as an improved and more consolidated annotation for the model species Arabidopsis, and several crop species (potato, tomato, rice).',
				"Reactome": '<a href="http://www.reactome.org/" target="_blank">Reactome</a> is an open-source, open access, manually curated and peer-reviewed pathway database, containing information of around 20 organisms, including human, mouse and arabidopsis, among others.',
				"OmniPath": '<a href="https://omnipathdb.org/" target="_blank">OmniPath</a> integrates over 100 resources into a prior-knowledge network of signed, directed molecular interactions, available for human, mouse and rat. Its pathways are defined by the curated SIGNOR and NetPath annotations and carry no diagram, so they open as an interactive interaction network rather than as a painted map.'
			};

			/* One matrix, not one table per database.

			   Every database used to print its own two-column table of the same
			   omics, each centred inside the 1124px card with ~370px of dead space
			   either side: three identical shapes, 1149px of card, and no way to
			   compare one omic across databases without scrolling between two
			   tables. Comparing them is the only reason this card exists, so it is
			   one table - omics down, databases across - and the answer is a row.

			   Column order follows `databases`, so it matches the order the rest
			   of the step uses. */
			var dbs_head = databases.map(function(dbname) {
				return '<th scope="col">' + Ext.String.htmlEncode(dbname) + '</th>';
			}).join('');

			/* Every database was given an entry for every omic by the loop above,
			   so any of them names the full row set; the first is as good as any.
			   Read from the map rather than from dataDistribution so the rows and
			   the cells can never disagree about which omics exist. */
			var dbs_omicNames = Object.keys(matchingPerDB[databases[0]] || {});

			var dbs_rows = dbs_omicNames.map(function(omicName) {
				/* title, because the label column is fixed at 210px and clips with an
				   ellipsis. Omic names come from Step 1 and are routinely longer than
				   that ("Transcriptomics - liver - 24 h post treatment" measures 308px
				   against the 246px it is given); in the old wrapping cell they were
				   always readable, and nothing else on this card carries the name. */
				return '<tr><th scope="row" title="' + Ext.String.htmlEncode(omicName) + '">' +
				Ext.String.htmlEncode(omicName) + '</th>' +
				databases.map(function(dbname) {
					/* A missing entry means the omic matched nothing in that
					   database - which is a zero, not an unknown. An empty cell
					   would read as "not measured", which is a different claim. */
					var cell = (matchingPerDB[dbname] || {})[omicName] || {matched: 0, percentage: 0};
					/* The share as a bar as well as a number. Four columns of bare
					   figures across 1070px is a lot of table to read one comparison
					   out of; the bar answers "which database covers this omic best"
					   at a glance and gives the card's width something to do. The
					   figures stay - the bar is the second reading, not the only one.

					   Clamped: the percentage is Math.ceil'd upstream, so a fully
					   matched omic can arrive as 100 and nothing above it should
					   ever draw past the track. */
					var share = Math.max(0, Math.min(100, Number(cell.percentage) || 0));
					return '<td><span class="paDbCell">' +
					'<span class="paDbBar"><i style="width:' + share + '%"></i></span>' +
					'<span class="paDbCount">' + Number(cell.matched || 0).toLocaleString() + '</span>' +
					'<span class="paDbPct">' + cell.percentage + '%</span>' +
					'</span></td>';
				}).join('') + '</tr>';
			}).join('');

			/* The descriptions keep every word they had; what changes is their
			   measure. Three paragraphs across 1070px ran to about 140 characters
			   a line - roughly twice a readable measure - so they sit as columns
			   beside each other instead, which is also how they read as a set. */
			var dbs_notes = databases.map(function(dbname) {
				/* A database with no blurb gets no column, rather than a headed one
				   reading "undefined". The map is a literal of the four databases the
				   installers build, but getDatabases() answers from the job, so a
				   fifth would print the word. The matrix above still counts it. */
				if (!dbs_descriptions[dbname]) {
					return '';
				}
				return '<div class="paDbNote"><h3>' + Ext.String.htmlEncode(dbname) + '</h3>' +
				'<p>' + dbs_descriptions[dbname] + '</p></div>';
			}).join('');

			var dbs_message = {
				xtype: 'box',
				cls: "contentbox", minHeight: 240, id: "dbs_message",
				html:
				'<h2>Multiple databases used</h2>' +
				'<div class="paDbBody">' +
				'  <p class="paDbLede">How many of your features carry an identifier each database ' +
				'is keyed on. Read across a row to compare one omic between databases &mdash; the ' +
				'bar is that share of the omic\'s input features.</p>' +
				'  <div class="paDbMatrixWrap">' +
				/* A floor, so the wrapper's overflow-x has something to scroll. The
				   table is `width: 100%` under `table-layout: fixed`, so without one
				   it can never be wider than its wrapper - the scroll was dead code -
				   and each database column just kept shrinking. A cell's contents do
				   NOT shrink with it: 24 (bar) + 66 (count) + 34 (share) + 24 (gaps)
				   + 36 (padding) = 184px is a hard minimum, and below that the flex
				   row overflows LEFT (`justify-content: flex-end`) into the column
				   beside it. Measured: at a 163px column the numbers already sit on
				   the omic names, at 103px they spill 63px. Reachable on a 4-database
				   organism at 1200px, and on any organism in a narrow window.

				   190 per database leaves 6px over that minimum; 210 is the label
				   column. Inline because it depends on how many databases this job
				   has, which no stylesheet knows. */
				'    <table class="paDbMatrix" style="min-width:' + (210 + databases.length * 190) + 'px">' +
				'      <thead><tr><th scope="col">Omic</th>' + dbs_head + '</tr></thead>' +
				'      <tbody>' + dbs_rows + '</tbody>' +
				'    </table>' +
				'  </div>' +
				/* No blurb for any of them, no block: the row carries a top rule, and
				   an empty one would draw a line under the table for nothing. */
				(dbs_notes ? '  <div class="paDbNotes">' + dbs_notes + '</div>' : '') +
				'  <p class="paDbFoot">The diagrams below combine the matched and unmatched features of <b>all</b> databases. Hover over a diagram for the per-database breakdown.</p>' +
				'</div>'
			};

			/* Add an empty container to restore "odd" position of next sibling elements */
			omicSummaryPanelComponents.splice(2, 0, dbs_message, {xtype: 'container', cls: 'paLayoutPad', html:'<div style="display: none;"></div>'});
		}

		// Replicate-detection card — only renders when at least one omic has
		// a complete or partial replicate detection result. Built via its own
		// view so the wiring (radios, file picker, server POST) lives in one
		// self-contained component instead of leaking into PA_Step2JobView.
		var replicateDetectionView = new PA_Step2ReplicateDetectionView();
		replicateDetectionView.loadModel(me.getModel());
		if (replicateDetectionView.hasContent()) {
			var repComponent = replicateDetectionView.initComponent();
			if (repComponent) {
				// Append after the data-distribution box; pad with an empty
				// container to keep the column-layout "odd/even" alignment
				// consistent with the existing Step-2 cards.
				omicSummaryPanelComponents.push(repComponent,
					{xtype: 'container', cls: 'paLayoutPad', html:'<div style="display: none;"></div>'});
			}
		}

		var compoundsPanelHTML = "";

		// This guard is deliberately NOT me.items.length. items only holds the
		// sets that still need disambiguation, and the threshold combo below
		// belongs to the job, not to the cards: its value is posted as
		// thresholdMetaboliteClass and PathwayAcquisitionServlet feeds it to
		// compundsClassification(). A job whose compounds all resolved
		// automatically must still be able to set it, so it is gated on the
		// model having matched compounds at all.
		if (me.getModel().getFoundCompounds().length > 0) {
			// create a box named "Configure the metabolite class activity threshold"
			omicSummaryPanelComponents.splice(5, 0, {
				xtype: 'container',
				layout: {type: 'vbox', align: 'stretch'},
				cls: "contentbox", minHeight: 240, id: "threshold_box",
				items: [{
					html: '<h2 style="width: 100%;">Metabolite class activity test</h2>'
				}, {
					/* One line. Which test will run, and on what, is the plan
					   block in the left column below; how the two tests work is
					   the right column. The old version put all of that here,
					   ahead of the controls, and read as a wall. */
					html: '<p class="paClassLede">Asks whether each KEGG BRITE class <b>responds as a whole</b>, '
						+ 'at three levels of the hierarchy, with p-values corrected within each level.</p>'
				}, {
					xtype: 'form',
					cls: 'paClassMain',
					/* Configured, not flexed: see the note on #classActivityBox. Plan
					   and controls side by side need 26 + 600 + 24 + 446 = 1096; this
					   said 1116, which was the old columns and 20px more than the row
					   it sizes. Measured at three viewports, ExtJS writes the card's
					   own inner width here regardless (1122 at 1459, 1171 at 1512), so
					   the value is the intent for the columns inside rather than the
					   width that renders - which is all the more reason for it not to
					   contradict them. */
					width: paClassColumnsSideBySide() ? 1096 : 692,
					bodyCls: "divForm",
					style: "margin: 0 0 4px 0;",
					layout: {type: 'vbox', align: 'left'},
					defaults: {labelAlign: "right", border: false},
					items: thresholdMetaboliteClass
				}, {
					/* How the two tests work, across the card. A direct child of
					   the card's vbox like the lede, so it is measured at the
					   card's width. */
					xtype: 'box',
					cls: 'paClassHowBox',
					html: paClassActivityHowItWorks(!!(((me.getModel().getCompoundBasedInputOmics() || []).filter(function (o) {
						return o.omicName === classActivityOmic;
					})[0] || {}).replicateMapping || []).length)
				}]
			}, {xtype: 'container', cls: 'paLayoutPad', html:'<div style="display: none;"></div>'});
		}

		/* Straight after the databases card and the layout pad that follows
		   it, so the cards start on a fresh row of the column layout. With a
		   single database there is no such card and they follow the two
		   summary boxes instead. Located by id, not by a counted index: what
		   sits in front depends on which of the config boxes this job needs. */
		var cardsAt = 2;
		for (var i = 0; i < omicSummaryPanelComponents.length; i++) {
			if (omicSummaryPanelComponents[i].id === "dbs_message") {
				cardsAt = i + 2;
				break;
			}
		}
		omicSummaryPanelComponents.splice.apply(omicSummaryPanelComponents, [cardsAt, 0].concat(omicCards));

		if (me.items.length > 0) {
			compoundsPanelHTML = me.renderCompoundsPanel();
		}

		/* Compounds disambiguation is a module of this step like any other, so it
		   goes in the container that holds the modules.

		   It used to live in a form of its own below #omicSummaryPanel. The two
		   only ever lined up because they happen to share `.omicSummaryContainer`'s
		   max-width - nothing structural held them on one rail, and the cards
		   inside it were laid out by the odd/even floats while every other card on
		   the step had moved to the `:has()` flex row.

		   The form it sat in carried one hidden field, jobID, and
		   JobController.step2OnFormSubmitHandler adds jobID to the payload
		   explicitly anyway; the selections are read from the MODEL
		   (getSelectedCompounds), never from this markup. So there is nothing left
		   in that form to lose.

		   Not added at all when there is nothing to decide. As a member of the
		   flex row it claims a full row of its own, so an empty one is 24px of
		   blank at the end of the step; every caller already guards for the box
		   being absent (refreshCompoundsPanel, initCompoundsPanelHandlers). */
		if (compoundsPanelHTML !== "") {
			omicSummaryPanelComponents.push({
				xtype: "box", itemId: "compoundsPanelsContainer",
				cls: "compoundsPanelsContainer",
				html: compoundsPanelHTML
			});
		}

		this.component = Ext.widget({
			xtype: "container",
			minHeight: 800,
			padding: '10',
			items: [{
				xtype: "box",
				cls: "toolbar secondTopToolbar",
				html:
				'<a href="javascript:void(0)" class="button btn-danger btn-right" id="resetButton"><i class="fa fa-refresh"></i> Reset</a>' +
				'<a href="javascript:void(0)" class="button btn-success btn-right" id="runButton"><i class="fa fa-play"></i> Next step</a>' +
				'<a href="javascript:void(0)" class="button btn-default btn-right backButton"><i class="fa fa-arrow-left"></i> Go back</a>'
			}, {
				xtype: 'container', itemId: "omicSummaryPanel",
				cls: "omicSummaryContainer",
				layout: 'column',  style: "margin-top:50px;",
				items: omicSummaryPanelComponents
			}],
			listeners: {
				boxready: function() {
					$("#runButton").click(function() {
						me.submitFormHandler();
					});
					$(".backButton").click(function() {
						me.backButtonHandler();
					});
					$("#resetButton").click(function() {
						me.resetViewHandler();
					});
					$('#download_mapping_file').click(function() {
						application.getController("DataManagementController").downloadFilesHandler(me, "mapping_results_" + me.getModel().getJobID() + ".zip", "job_result", me.getModel().getJobID());
					});
					me.initAISuggestButton();
					initializeTooltips(".helpTip");
					me.initCompoundsPanelHandlers(this.queryById("compoundsPanelsContainer"));
				},
				beforedestroy: function() {
					me.getModel().deleteObserver(me);
				}
			}
		});

		return this.component;
	};
	/**
	* Show the button only where it can actually do something.
	*
	* Three conditions, all of which have to hold: this deployment has AI
	* switched on AND has a token (`/ai_provider` answers both), the job carries
	* consent, and there is at least one card to decide. A button that appears
	* and then reports that the server has no API key is worse than no button.
	*/
	this.initAISuggestButton = function() {
		var me = this;
		if (me.items.length === 0) {
			return;
		}
		if (me.getModel().aiConsent !== true) {
			return;
		}
		if (typeof withAIProviderInfo !== "function") {
			return;
		}
		withAIProviderInfo(function(info) {
			if (!info || info.enabled !== true || info.configured !== true) {
				return;
			}
			// Kept on the view rather than only in the DOM: the panel is
			// re-rendered whenever picks are applied or undone, and the answer
			// to "may this job use the AI" must survive that.
			me.aiAvailable = true;
			$(".aiSuggestActions").show();
		});
	};

	/**
	* The AI controls, inside the card that introduces the section.
	*
	* Deliberately here rather than in the step's toolbar. The toolbar's buttons
	* act on the whole step - go back, run the next step, reset everything - and
	* this one acts on the compound cards immediately below it. Next to them it
	* can also say what it does and what it costs the user, which a toolbar
	* button has no room for.
	*
	* @returns {String}
	*/
	this.renderAIActions = function() {
		var hidden = this.aiAvailable ? "" : ' style="display:none;"';
		var undo = this.aiSnapshot
			? '<a href="javascript:void(0)" class="button btn-ai-quiet" id="aiUndoButton" ' +
			  'title="Put every tick back as it was">' +
			  '<i class="fa fa-undo"></i></a>'
			: "";

		var mark = (typeof getAIMark === "function") ? getAIMark() : "";

		return '' +
		'<div class="aiSuggestActions"' + hidden + '>' +
		'  <div class="aiSuggestActionsBody">' +
		// data-guides="ignore": the alignment overlay measures where TYPE starts,
		// and an icon-led label starts its type one icon in. The mark is on the
		// panel's rail, which is the edge a reader sees; the offset is the icon,
		// and it is declared here rather than left for the HUD to rediscover.
		'    <h3 class="aiSuggestActionsTitle" data-guides="ignore">' +
		       mark + '<span>PaintOmics AI</span></h3>' +
		'    <p class="aiSuggestHint">Picks the most likely compound for each name, from ' +
		'your organism and experiment design.</p>' +
		'  </div>' +
		'  <div class="aiSuggestActionsRow">' +
		'    <a href="javascript:void(0)" class="button btn-ai" id="aiSuggestButton">' +
		       mark + '<span>Choose for me</span></a>' +
		     undo +
		'  </div>' +
		'</div>';
	};

	/**
	* The button's four states, in one place so none of them can be half-applied.
	*
	* @param {String} state one of "idle", "working", "done"
	* @param {String} label optional text for the "done" state
	*/
	this.setAIButtonState = function(state, label) {
		var button = $("#aiSuggestButton");
		if (button.length === 0) {
			return;
		}
		var mark = (typeof getAIMark === "function") ? getAIMark() : "";

		if (state === "working") {
			button.addClass("aiWorking")
				.html('<i class="fa fa-circle-o-notch fa-spin"></i><span>Choosing\u2026</span>');
		} else if (state === "done") {
			button.removeClass("aiWorking")
				.html(mark + '<span>' + (label || "Choose again") + '</span>');
		} else {
			button.removeClass("aiWorking")
				.html(mark + '<span>Choose for me</span>');
		}
	};

	this.aiSuggestHandler = function() {
		if ($("#aiSuggestButton").hasClass("aiWorking")) {
			return;
		}
		this.setAIButtonState("working");
		this.controller.step2SuggestCompoundsHandler(this);
	};

	/**
	* The whole disambiguation panel as one string of HTML.
	*
	* It used to be a column layout holding one container per matched name, each
	* holding one Ext box per candidate plus an Ext.tip.ToolTip per candidate; a
	* job with 6592 matched names built tens of thousands of components and froze
	* the tab for minutes before the first paint. This markup reproduces the same
	* DOM (the cards are laid out by .metaboliteBox:nth-child in main.css, not by
	* the column layout) at a fraction of the cost.
	*
	* Extracted from the component definition so that accepting the AI's picks
	* can rebuild it. Re-rendering rather than patching checkboxes in place is
	* deliberate: a collapsed card's alternatives are not in the document at all,
	* so a DOM-only update would silently miss exactly the candidates the AI is
	* most likely to have turned OFF.
	*
	* @returns {String}
	*/
	this.renderCompoundsPanel = function() {
		var me = this;
		return '' +
		// `compoundsIntroBox` takes this card out of the 49%-wide odd/even
		// float grid the metabolite cards use. As one of those it was a
		// half-width block of prose with an empty half-row beside it.
		//
		// Two columns inside it: what the user has to do, and the offer to do
		// it for them. A lone button under a line of prose in a 1360px card
		// read as an afterthought -- it had no surface of its own and nothing
		// to balance against.
		'<div class="contentbox omicSummaryBox compoundsIntroBox">' +
		'  <div id="about" class="compoundsIntroLayout">' +
		'    <div class="compoundsIntroText">' +
		'      <h2>Compounds disambiguation</h2>' +
		'      <p><b>' + me.items.length + '</b> names match more than one KEGG compound. ' +
		'Tick the one you measured on each card.</p>' +
		'    </div>' +
		     me.renderAIActions() +
		'  </div>' +
		'</div>' +
		me.renderAISummary() +
		// A grid, not the odd/even floats: cards size to their content and
		// a row's two cards share one bottom edge (see .compoundsGrid).
		'<div class="compoundsGrid">' +
		me.items.map(function(compoundSetView, index) {
			return compoundSetView.renderCard(index);
		}).join("") +
		'</div>' +
		// The cards are floated; the column layout used to supply the clearfix,
		// so without this the panel would collapse to no height and the cards
		// would spill out of the step-2 form.
		'<div style="clear: both;"></div>';
	};

	/**
	* The banner that says what the AI did, or nothing at all before it has run.
	*
	* The counts are of cards actually CHANGED, not of decisions received: the
	* server ranks every compound set it has, including ones this view draws no
	* card for, and reporting those would credit the feature with work the user
	* cannot see.
	*
	* @returns {String}
	*/
	this.renderAISummary = function() {
		var summary = this.aiSummary;
		if (!summary) {
			return "";
		}

		var parts = [];
		if (summary.byRule > 0) {
			parts.push('<b>' + summary.byRule + '</b> by name');
		}
		if (summary.byAI > 0) {
			parts.push('<b>' + summary.byAI + '</b> by PaintOmics AI');
		}

		// The count is of cards CHANGED. Saying "selected" would claim the ones
		// that were already right, which is most of them on a typical job.
		var headline = parts.length
			? 'Changed ' + parts.join(' and ') + '.'
			: 'Nothing needed changing.';

		var tail = summary.unsure > 0
			? ' <b>' + summary.unsure + '</b> left for you.'
			: '';

		// The model identifier is deliberately not shown, here or anywhere else
		// in the interface -- the same rule PA_Step1Views states for the consent
		// copy. Naming a specific build invites the reader to evaluate the model
		// rather than the decision in front of them, and the string goes stale
		// the moment the gateway is repointed. What matters to a reader is that
		// the answers were checked, which is what this says.
		var model = '<div class="aiSuggestModel">' +
			'Every choice was checked against the candidates on its own card.</div>';

		return '' +
		'<div class="contentbox aiSuggestSummary">' +
		'  <div class="aiSuggestSummaryHead">' +
		'    <span class="aiSuggestSummaryMark">' +
		       ((typeof getAIMark === "function") ? getAIMark() : "") + '</span>' +
		'    <span>' + headline + tail + '</span>' +
		'  </div>' +
		model +
		'</div>';
	};

	/**
	* Accept a suggestion payload: tick what it chose, untick its rivals.
	*
	* Only ever touches candidates INSIDE a set the server named, and only sets
	* this view actually drew a card for. A decision for an input name this view
	* has no card for is dropped rather than applied blind - the server's idea of
	* which sets need a decision is deliberately more permissive than this one
	* (`selected` does not exist server-side), and the cards are what the user
	* consented to by pressing the button.
	*
	* @param {Object} payload as returned by /pa_suggest_compounds_status
	* @returns {Object} {byRule, byAI, unsure} counts of cards actually changed
	*/
	this.applyAISuggestions = function(payload) {
		var me = this;
		var byTitle = {};
		me.items.forEach(function(compoundSetView) {
			byTitle[compoundSetView.getModel().getTitle()] = compoundSetView;
		});

		// One snapshot of every tick before anything moves, so Undo is exact
		// rather than an attempt to invert the decisions one at a time.
		//
		// Taken ONCE. Overwriting it on "Choose again" replaced the user's own
		// selection with the previous run's output, so Undo restored the AI's
		// first answer while the button still promised "put every tick back as
		// it was". The snapshot is cleared by undoAISuggestions, so the next
		// run after an Undo takes a fresh one.
		me.aiSnapshot = me.aiSnapshot || me.items.map(function(compoundSetView) {
			var model = compoundSetView.getModel();
			return {
				view: compoundSetView,
				state: model.getMainCompounds().concat(model.getOtherCompounds())
					.map(function(compound) { return compound.selected === true; }),
				aiState: compoundSetView.aiState || null
			};
		});

		var counts = {byRule: 0, byAI: 0, unsure: 0};

		// Which KEGG compound is already spoken for, and by which input name.
		// Step 1 de-duplicates across boxes on purpose (JobController unselects
		// the losing copy when two names propose the same id), and the warning
		// on the checkbox exists for the same reason -- but that warning only
		// fires on a real `change` event, so applying picks set by set would
		// have re-created exactly the duplicates both guards prevent, silently,
		// and posted the same compound twice to step 3.
		var claimedBy = {};
		me.items.forEach(function(compoundSetView) {
			var model = compoundSetView.getModel();
			model.getMainCompounds().concat(model.getOtherCompounds())
				.forEach(function(compound) {
					if (compound.selected === true) {
						claimedBy[compound.getID()] = model.getTitle();
					}
				});
		});

		(payload.decisions || []).forEach(function(decision) {
			var compoundSetView = byTitle[decision.title];
			if (!compoundSetView || !decision.keggID) {
				return;
			}

			// Already selected under a DIFFERENT input name: leave this set
			// alone and say so, rather than duplicate the compound.
			var owner = claimedBy[decision.keggID];
			if (owner !== undefined && owner !== decision.title) {
				counts.unsure++;
				compoundSetView.aiState = {
					status: "unsure", keggID: null, tier: decision.tier,
					confidence: "",
					reason: "\u201c" + owner + "\u201d already uses this compound, " +
						"so it was left for you to decide"
				};
				return;
			}

			// Only a card whose ticks actually MOVED gets marked. The server
			// decides every set it has, and on a typical job most of those
			// decisions agree with what was already selected -- badging those
			// too put a chip on 52 of 47 cards, at which point the chip stops
			// meaning anything and the eye cannot find the changes.
			if (!compoundSetView.selectOnly(decision.keggID)) {
				claimedBy[decision.keggID] = decision.title;
				return;
			}

			// selectOnly cleared this set's other ticks; release their claims so
			// a later decision can legitimately take one of them.
			var model = compoundSetView.getModel();
			model.getMainCompounds().concat(model.getOtherCompounds())
				.forEach(function(compound) {
					if (claimedBy[compound.getID()] === decision.title &&
						compound.getID() !== decision.keggID) {
						delete claimedBy[compound.getID()];
					}
				});
			claimedBy[decision.keggID] = decision.title;

			if (decision.tier === "ai") { counts.byAI++; } else { counts.byRule++; }
			compoundSetView.aiState = {
				status: "picked", keggID: decision.keggID, tier: decision.tier,
				confidence: decision.confidence || "", reason: decision.reason || ""
			};
		});

		(payload.unresolved || []).forEach(function(entry) {
			var compoundSetView = byTitle[entry.title];
			if (!compoundSetView) {
				return;
			}
			counts.unsure++;
			compoundSetView.aiState = {
				status: "unsure", keggID: null, tier: "ai", confidence: "",
				reason: entry.reason || entry.detail || ""
			};
		});

		me.aiSummary = {byRule: counts.byRule, byAI: counts.byAI,
		                unsure: counts.unsure};
		me.refreshCompoundsPanel();
		return counts;
	};

	/**
	* Put every tick back exactly as it was before the button was pressed.
	*/
	this.undoAISuggestions = function() {
		var snapshot = this.aiSnapshot;
		if (!snapshot) {
			return;
		}
		snapshot.forEach(function(entry) {
			var model = entry.view.getModel();
			var compounds = model.getMainCompounds().concat(model.getOtherCompounds());
			compounds.forEach(function(compound, index) {
				compound.selected = entry.state[index];
			});
			entry.view.aiState = entry.aiState;
		});
		this.aiSnapshot = null;
		this.aiSummary = null;
		this.refreshCompoundsPanel();
	};

	/**
	* Rebuild the cards and rebind the delegated handlers they depend on.
	*/
	this.refreshCompoundsPanel = function() {
		var container = this.component && this.component.queryById
			? this.component.queryById("compoundsPanelsContainer") : null;
		if (!container) {
			return;
		}
		container.update(this.renderCompoundsPanel());
		// update() replaces the element's children, so the delegated handlers
		// bound to the OLD element are gone with it.
		this.initCompoundsPanelHandlers(container);
	};

	this.submitFormHandler = function() {
		this.controller.step2OnFormSubmitHandler(this);
	};
	this.backButtonHandler = function() {
		this.controller.backButtonClickHandler(this, update=true);
	};
	this.resetViewHandler = function() {
		this.controller.resetButtonClickHandler(this);
	};
	/**
	* Every card shares one set of delegated jQuery handlers and one tooltip,
	* bound once to the panel that contains them. Binding per candidate - and
	* creating an Ext.tip.ToolTip per candidate - was the other half of the
	* step-2 freeze: that cost scales with the number of matched compounds,
	* this does not. Delegation also covers the alternative candidates, which
	* are only inserted into the document when a card is expanded (which is why
	* the old code needed a 2 second setTimeout to re-bind them).
	*
	* @param {Ext.Component} panelComponent the compoundsPanelsContainer box
	*/
	this.initCompoundsPanelHandlers = function(panelComponent) {
		var me = this;

		if (me.items.length === 0 || !panelComponent || !panelComponent.el) {
			return;
		}

		// Scoped to this view's own element: a previous step-2 job may still be
		// in the DOM behind the card layout, and its cards index into a
		// different items array.
		var panel = $(panelComponent.el.dom);

		// Every binding below is namespaced and cleared first, because this
		// function runs again on each apply and undo.
		//
		// The assumption that made that safe was wrong. Ext's Component.update()
		// resolves to `getTargetEl().update(html)` -> `dom.innerHTML = html`, so
		// the panel ELEMENT survives and only its children are replaced --
		// delegated handlers bound to it survive with it. Re-binding therefore
		// added a second copy of each: measured after one "Choose for me", the
		// panel carried six click handlers instead of three, so
		// showOtherCompoundsHandler ran twice per click -- the first call opened
		// the alternatives and the second read `hasClass('visible')` as true and
		// closed them again. "Show" became a dead button, and every checkbox
		// fired its duplicate-compound warning twice.
		panel.off(".paStep2");

		panel.on("change.paStep2", "input[type=checkbox][name=metabolite]", function() {
			me.compoundSelectionHandler($(this));
		});

		panel.on("click.paStep2", ".showOtherCompoundsButton", function() {
			me.showOtherCompoundsHandler($(this));
		});

		// Delegated rather than bound to the buttons themselves: the AI controls
		// live inside this panel, and its HTML is replaced wholesale on every
		// apply and undo, so a handler bound to the button would go with it.
		panel.on("click.paStep2", "#aiSuggestButton", function() {
			me.aiSuggestHandler();
		});

		panel.on("click.paStep2", "#aiUndoButton", function() {
			me.undoAISuggestions();
			me.setAIButtonState("idle");
		});

		// Same story: one tooltip per panel, not one per refresh. Each call used
		// to leak another Ext.tip.ToolTip bound to the same target, which is the
		// per-candidate tooltip cost this panel was rewritten to avoid.
		if (me.compoundTooltip) {
			me.compoundTooltip.destroy();
		}
		me.compoundTooltip = Ext.create('Ext.tip.ToolTip', {
			target: panel[0],
			delegate: '.metaboliteCompound',
			listeners: {
				beforeshow: function(tip) {
					var compound = $(tip.triggerElement);
					// The browser has already decoded the attributes, so the
					// values go back through htmlEncode before being re-injected.
					var compoundID = compound.attr("data-compound-id") || "";
					var compoundName = compound.attr("data-compound-name") || "";

					tip.update(
						'<b>' + Ext.String.htmlEncode(compoundName) + '</b> (' + Ext.String.htmlEncode(compoundID) + ')' +
						'<div>' +
						'  <div style="display: block; text-align:center; padding: 20px;"><i class="fa fa-circle-o-notch fa-spin fa-fw"></i> Loading image...</div>' +
						'  <img style="display: block; margin:auto;" src="http://rest.kegg.jp/get/' + encodeURIComponent(compoundID) + '/image">' +
						'</div>');
				},
				show: function(tip) {
					// The KEGG structure image arrives late; drop the spinner and
					// re-measure the tip once it does.
					$(tip.el.dom).find("img").on('load', function() {
						$(this).prev().remove();
						tip.doLayout();
					});
				}
			}
		});
	};

	/**
	* Mirrors a checkbox onto the model, which is the only place the selection
	* is read from (see getSelectedCompounds).
	*/
	this.compoundSelectionHandler = function(checkbox) {
		var compoundID = checkbox.val();
		var selected = checkbox.is(":checked");
		var setIndex = parseInt(checkbox.closest(".metaboliteBox").attr("data-compoundset"), 10);
		var compoundSetView = this.items[setIndex];

		if (compoundSetView === undefined) {
			return;
		}

		var compoundSet = compoundSetView.getModel();
		var compound = compoundSet.findOtherCompound(compoundID) || compoundSet.findMainCompound(compoundID);

		if (compound === null) {
			return;
		}

		compound.selected = selected;

		if (selected) {
			//If the user selects a compound which is repeated and already selected, warn.
			//The duplicates are looked up in the model rather than by scanning the
			//rendered checkboxes: the resolved compound sets have no card at all and
			//the alternatives of a collapsed card are not in the document, so a DOM
			//scan would silently miss real duplicates.
			var duplicates = this.findSelectedDuplicates(compoundID, compoundSet);

			if (duplicates.length > 0) {
				var message = "<b>Also selected for:</b><ul>";
				for (var i in duplicates) {
					message += "<li>" + Ext.String.htmlEncode(duplicates[i]) + "</li>";
				}
				message += "</ul>";

				showWarningMessage("Compound already selected", {
					message : "This compound has been already selected in other box. Duplicated compounds may affect to the results in next stages.<br>" + message,
					showButton : true
				});
			}
		}
	};

	/**
	* Titles of the other compound sets that already have this KEGG compound
	* selected. Excludes ownerSet, whose selection is the one just made.
	*/
	this.findSelectedDuplicates = function(compoundID, ownerSet) {
		var foundCompounds = this.model.getFoundCompounds();
		var duplicates = [], compoundSet, compound;

		for (var i in foundCompounds) {
			compoundSet = foundCompounds[i];
			if (compoundSet === ownerSet) {
				continue;
			}

			compound = compoundSet.findMainCompound(compoundID) || compoundSet.findOtherCompound(compoundID);
			if (compound !== null && compound.selected === true) {
				duplicates.push(compoundSet.getTitle());
			}
		}

		return duplicates;
	};

	/**
	* Expands/collapses the alternative candidates of one card, building their
	* markup the first time it is opened. A name can match a hundred KEGG
	* compounds, so rendering every card's alternatives up front would put
	* hundreds of thousands of nodes in the document that nobody asked for.
	*/
	this.showOtherCompoundsHandler = function(button) {
		var card = button.closest(".metaboliteBox");
		var otherCompoundsPanel = card.find(".otherCompoundsPanel");
		var isVisible = button.hasClass("visible");

		if (!isVisible) {
			if (otherCompoundsPanel.is(":empty")) {
				var setIndex = parseInt(card.attr("data-compoundset"), 10);
				otherCompoundsPanel.html(this.items[setIndex].renderOtherCompounds());
			}
			card.addClass("expandedBox");
			button.addClass("visible").html('<i class="fa fa-chevron-down"></i> ' + button.attr("data-label"));
		} else {
			card.removeClass("expandedBox");
			button.removeClass("visible").html('<i class="fa fa-chevron-right"></i> ' + button.attr("data-label"));
		}

		otherCompoundsPanel.toggle(!isVisible);
	};

	this.checkForm = function() {
		// Asked of the model, not of the rendered checkboxes. Only the sets that
		// need disambiguation have a card, so counting checkboxes would report
		// "nothing to choose" for a job whose compounds all resolved
		// automatically - and would ignore the selections of the collapsed
		// alternatives, which are not in the document.
		return (this.model.getFoundCompounds().length === 0 || this.getSelectedCompounds().length > 0);
	};

	this.getSelectedCompounds = function() {
		var foundCompounds = this.model.getFoundCompounds();
		var checkedCompoundsIDs = [], compoundSet, compound;
		for(var i in foundCompounds){
			compoundSet = foundCompounds[i];
			for(var j in compoundSet.mainCompounds){
				compound = compoundSet.mainCompounds[j];
				if(compound.selected === true){
					checkedCompoundsIDs.push(compound.ID + "#" + compound.name + "#" + compoundSet.title);
				}
			}
			for(var j in compoundSet.otherCompounds){
				compound = compoundSet.otherCompounds[j];
				if(compound.selected === true){
					checkedCompoundsIDs.push(compound.ID + "#" + compound.name + "#" + compoundSet.title);
				}
			}
		}
		return checkedCompoundsIDs;
	};

	return this;
}
PA_Step2JobView.prototype = new View();


/**
* What the class activity test can use from a compound omic's design, or null.
*
* Mirrors src/common/DesignFile._factor_positions: a token position of the
* condition names is a factor when every name splits into the same number of
* tokens and the position takes more than one value but fewer than all. Ids
* are "factor<position>" so the server resolves the same choice.
*/
function paClassActivityDesign(omic) {
	var mapping = omic.replicateMapping || [];
	var sampleHeader = omic.sampleHeader || [];
	var columns = (omic.omicHeader || []).length - 1;
	if (!mapping.length || !sampleHeader.length || mapping.length !== columns) { return null; }
	var counts = {};
	mapping.forEach(function (m) { counts[m] = (counts[m] || 0) + 1; });
	var replicates = Object.keys(counts).map(function (k) { return counts[k]; });
	var factors = [];
	["_", "-", "."].some(function (sep) {
		var tokens = sampleHeader.map(function (name) { return name.split(sep); });
		var width = tokens[0].length;
		if (width < 2 || tokens.some(function (t) { return t.length !== width; })) { return false; }
		for (var position = 0; position < width; position++) {
			var values = [];
			tokens.forEach(function (t) { if (values.indexOf(t[position]) === -1) { values.push(t[position]); } });
			if (values.length > 1 && values.length < sampleHeader.length) {
				factors.push({id: "factor" + position, label: values.slice(0, 4).join(", ") + (values.length > 4 ? "…" : "")
					+ " (" + values.length + " levels)", levels: values.length});
			}
		}
		return factors.length > 0;
	});
	/* The server's default is the factor with the fewest levels; offer it first. */
	factors.sort(function (a, b) { return a.levels - b.levels; });
	return {
		columns: columns,
		conditions: sampleHeader.length,
		replicates: Math.min.apply(null, replicates) === Math.max.apply(null, replicates)
			? replicates[0] : (Math.min.apply(null, replicates) + "–" + Math.max.apply(null, replicates)),
		factors: factors
	};
}

/***********************************************************************
* PA_Step2ReplicateDetectionView
*
* Step-2 confirmation panel for the replicate→sample aggregation feature.
* Renders one card per omic whose values-file column headers match the
* conservative replicate-suffix detector (see ReplicateDetection.py on the
* server). Each card lets the user choose between three modes:
*
*   - Show all replicates  (the existing visualisation, no aggregation).
*   - Average replicates    (auto: use the server-detected sample grouping).
*   - Upload design file    (manual: 2-column TSV mapping each column to a
*                            biological sample label).
*
* On confirm, the panel POSTs to /pa_apply_replicate_mapping per-omic. The
* server walks every Feature, computes per-sample means, and stores
* sampleValues/sampleRelevant on each OmicValue. Step-4 reads those when
* the visualisation toggle flips to "samples" mode.
***********************************************************************/
/* The items of the metabolite class activity box for ONE omic: the design
   note and factor choice when a replicate mapping is applied, and the
   threshold combo always. A function, not inline markup, because the
   replicate-detection card on the same page can apply or clear a mapping
   after the box was drawn. */
/* ---- The class activity card: plan, controls, and how the two tests work ----
   Markup lives in <section>/<figure>/<p>/<ul> plus plain divs and spans that
   main.css exempts from `#threshold_box div, span { width:100% !important }`
   by subtree (.paClassPlan *, .paClassHow *). Figures are inline SVG on a
   124x64 box so they scale with their column; colours are tokens so the
   dark theme can restate them. */

function paFigText(x, y, text, options) {
	options = options || {};
	/* 5.6 units: the 124-unit box is drawn at up to 236px, so this is ~10.5px
	   on screen; 9.5 units read as 18px. */
	return '<text x="' + x + '" y="' + y + '" font-size="' + (options.size || 5.6) + '" font-weight="' + (options.weight || 400)
		+ '" fill="' + (options.color || "var(--pa-ink-muted)") + '" text-anchor="' + (options.anchor || "start") + '">' + text + '</text>';
}

function paFigOpen() {
	return '<svg viewBox="0 0 124 64" width="100%" height="64" preserveAspectRatio="xMidYMid meet" aria-hidden="true" class="paClassFigSvg">';
}

function paFigAccent(active) {
	return active ? "var(--pa-accent-green)" : "var(--pa-fig-faint)";
}

/* Samples: two conditions x three replicates x four metabolites. Generic
   condition labels, not the example dataset's own names: the figure is shown
   for every job, and the test is not limited to a control/treatment pair. */
function paFigPermInput() {
	var parts = [paFigOpen(), paFigText(19, 8, "Control", {anchor: "middle"}), paFigText(67, 8, "Treatment", {anchor: "middle"})];
	var control = [0.55, 0.7, 0.45, 0.62], treatment = [1, 0.9, 0.5, 0.82];
	for (var r = 0; r < 4; r++) {
		var y = 12 + r * 13;
		for (var c = 0; c < 3; c++) {
			parts.push('<rect x="' + (c * 13) + '" y="' + y + '" width="11" height="11" rx="2" fill="var(--pa-fig-cell)" opacity="' + control[r] + '"></rect>');
			parts.push('<rect x="' + (48 + c * 13) + '" y="' + y + '" width="11" height="11" rx="2" fill="var(--pa-fig-cell-strong)" opacity="' + treatment[r] + '"></rect>');
		}
	}
	parts.push('</svg>');
	return parts.join("");
}

/* An F per metabolite, and the class mean. */
function paFigPermScore(active) {
	var accent = paFigAccent(active);
	var parts = [paFigOpen()];
	[70, 34, 84, 26].forEach(function (w, r) {
		parts.push('<rect x="0" y="' + (12 + r * 13) + '" width="' + w + '" height="11" rx="2" fill="var(--pa-fig-bar-strong)"></rect>');
	});
	parts.push('<path d="M54 8V62" stroke="' + accent + '" stroke-width="1.5" stroke-dasharray="3 3"></path>');
	parts.push(paFigText(58, 8, "mean F", {color: accent, weight: 600}));
	parts.push('</svg>');
	return parts.join("");
}

/* The null from re-labelling, and where the observed mean F falls. */
function paFigPermNull(active) {
	var accent = paFigAccent(active);
	var parts = [paFigOpen()];
	[4, 10, 20, 30, 27, 20, 13, 8, 4, 2].forEach(function (h, i) {
		parts.push('<rect x="' + (i * 11) + '" y="' + (58 - h) + '" width="9" height="' + h + '" rx="1.5" fill="var(--pa-fig-bar)"></rect>');
	});
	parts.push('<path d="M0 58.5H110" stroke="var(--pa-fig-faint)" stroke-width="1"></path>');
	parts.push('<path d="M104 14V58" stroke="' + accent + '" stroke-width="2" stroke-linecap="round"></path>');
	parts.push(paFigText(101, 9, "observed", {color: accent, weight: 600, anchor: "end"}));
	parts.push('</svg>');
	return parts.join("");
}

/* The class's members, ticked when they are in the relevant list. */
function paFigBinomInput(active) {
	var dot = active ? "var(--pa-fig-ink)" : "var(--pa-fig-ink-muted)";
	var parts = [paFigOpen()];
	var filled = {0: 1, 1: 1, 2: 1, 4: 1, 6: 1};
	for (var i = 0; i < 8; i++) {
		var cx = 9 + (i % 4) * 23, cy = 20 + Math.floor(i / 4) * 24;
		if (filled[i]) {
			parts.push('<circle cx="' + cx + '" cy="' + cy + '" r="7.5" fill="' + dot + '"></circle>');
			parts.push('<path d="M' + (cx - 3.3) + ' ' + cy + 'l2.4 2.4 4.2-4.8" stroke="var(--pa-fig-tick)" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" fill="none"></path>');
		} else {
			parts.push('<circle cx="' + cx + '" cy="' + cy + '" r="7" fill="none" stroke="var(--pa-fig-faint)" stroke-width="1.2"></circle>');
		}
	}
	parts.push('</svg>');
	return parts.join("");
}

/* A number line from 0 to n members: expected at alpha against observed. */
function paFigBinomScore(active) {
	var accent = paFigAccent(active);
	var parts = [paFigOpen(), '<path d="M6 40H118" stroke="var(--pa-fig-faint)" stroke-width="1.5" stroke-linecap="round"></path>'];
	for (var k = 0; k < 9; k++) {
		parts.push('<path d="M' + (6 + k * 14) + ' 37V43" stroke="var(--pa-fig-faint)" stroke-width="1"></path>');
	}
	parts.push(paFigText(6, 56, "0", {anchor: "middle"}));
	parts.push(paFigText(118, 56, "8", {anchor: "middle"}));
	parts.push('<circle cx="11.6" cy="40" r="4.5" fill="var(--pa-surface)" stroke="var(--pa-ink-muted)" stroke-width="1.5"></circle>');
	parts.push(paFigText(4, 24, "0.4 expected"));
	parts.push('<circle cx="76" cy="40" r="5" fill="' + accent + '"></circle>');
	parts.push(paFigText(70, 24, "5 observed", {color: accent, weight: 600}));
	parts.push('</svg>');
	return parts.join("");
}

/* Binomial(n, alpha) with the tail at or beyond the observed count. */
function paFigBinomNull(active) {
	var accent = paFigAccent(active);
	var parts = [paFigOpen()];
	var tailX = 5 * 12.5 - 1.5;
	parts.push('<rect x="' + tailX + '" y="10" width="' + (112 - tailX) + '" height="48" fill="' + accent + '" opacity="0.14"></rect>');
	[46, 19, 4, 2, 1.5, 1.5, 1.5, 1.5, 1.5].forEach(function (h, k) {
		parts.push('<rect x="' + (k * 12.5) + '" y="' + (58 - h) + '" width="10" height="' + h + '" rx="1.5" fill="' + (k >= 5 ? accent : "var(--pa-fig-bar)") + '"></rect>');
	});
	parts.push('<path d="M0 58.5H112" stroke="var(--pa-fig-faint)" stroke-width="1"></path>');
	parts.push(paFigText(86, 22, "P(k ≥ 5)", {color: accent, weight: 600, anchor: "middle"}));
	parts.push('</svg>');
	return parts.join("");
}

function paClassPill(active) {
	return active
		? '<span class="paClassPill paClassPill-on"><span class="paClassPillDot"></span>Runs on this job</span>'
		: '<span class="paClassPill paClassPill-off">The other case</span>';
}

function paClassTile(value, label, wide) {
	return '<li class="paClassTile' + (wide ? ' paClassTile-wide' : '') + '"><b>' + value + '</b><span>' + label + '</span></li>';
}

/**
* What the class activity test will do on this omic: which test, the numbers
* it rests on as tiles, and one sentence of mechanism. The card's earlier
* version said the same in 70 words of prose inside a green box.
*
* @param {String} omicName
* @param {Object|null} design paClassActivityDesign(), or null without one
* @param {Object} omic the compound omic's model entry, for the ratio columns
* @returns {String}
*/
function paClassActivityPlan(omicName, design, omic) {
	var name = Ext.String.htmlEncode(omicName || "");
	var head = '<p class="paClassPlanKicker"><span>Test that will run</span>' + paClassPill(true) + '</p>';
	if (design) {
		return '<section class="paClassPlan">' + head
			+ '<p class="paClassPlanName"><i class="fa fa-check-circle paIsOn"></i>Permutation test on your replicates</p>'
			+ '<ul class="paClassTiles">'
			+ paClassTile(name, "Omic", true)
			+ paClassTile(design.columns, "Sample columns")
			+ paClassTile(design.conditions, "Conditions")
			+ paClassTile(design.replicates, "Per condition")
			+ '</ul>'
			+ '<p class="paClassPlanWhy">Each class is scored by the mean F of its metabolites for the chosen factor, '
			+ 'against re-labellings of that factor. The threshold is used only if this test cannot run.</p>'
			+ '</section>';
	}
	var ratios = Math.max(0, ((omic && omic.omicHeader) || []).length - 1);
	return '<section class="paClassPlan">' + head
		+ '<p class="paClassPlanName"><i class="fa fa-list-ul"></i>Binomial test on your relevant list</p>'
		+ '<ul class="paClassTiles">'
		+ paClassTile(name, "Omic", true)
		+ (ratios ? paClassTile(ratios, ratios === 1 ? "Ratio column" : "Ratio columns") : "")
		+ paClassTile("Step 1", "Relevant list from")
		+ '</ul>'
		+ '<p class="paClassPlanWhy">Counts how many members of each class are in your relevant list, against the '
		+ 'threshold you used to build it. Upload one column per sample and a design in Step 1 to test on your own '
		+ 'replicates instead.</p>'
		+ '</section>';
}

/**
* Whether the plan and its controls can sit side by side. Measured once per
* build of the step, from the body: the hbox that places them does not wrap.
*
* @returns {Boolean}
*/
function paClassColumnsSideBySide() {
	// 26 + 600 + 24 + 446 inside the card, plus the sidebar and gutters (a
	// 1470px viewport gives the card 1132px inside).
	return Ext.getBody().getViewSize().width >= 1440;
}

function paClassHowCell(svg, caption) {
	return '<figure class="paClassFig">' + svg + '<figcaption>' + caption + '</figcaption></figure>';
}

function paClassHowRow(name, icon, active, cells, note) {
	return '<div class="paClassHowRow' + (active ? ' paIsOn' : '') + '">'
		+ '<div class="paClassHowWho">'
		+ '<p class="paClassHowName"><i class="fa ' + icon + '"></i>' + name + '</p>'
		+ paClassPill(active)
		+ '<p class="paClassHowNote">' + note + '</p>'
		+ '</div>' + cells.join("") + '</div>';
}

/**
* How the two tests work, as one comparison across the card: the same three
* columns for both tests (input, score per class, what it is compared with),
* one row per test with the one that runs on this job first and in green.
* The difference has to be on the page because the two tests can give
* opposite answers on the same job; it used to be ~150 words of prose.
*
* @param {Boolean} design whether this job carries a design
* @returns {String}
*/
function paClassActivityHowItWorks(design) {
	var perm = paClassHowRow("Permutation test", "fa-check-circle", !!design, [
		paClassHowCell(paFigPermInput(), "One column per sample, plus a design that says which condition each column is."),
		paClassHowCell(paFigPermScore(!!design), "An F-test per metabolite for the chosen factor; the class scores its members’ mean F."),
		paClassHowCell(paFigPermNull(!!design), "The same score under re-labellings of the factor; p is the share at or above the observed.")
	], "Self-contained, and it keeps the correlation between the metabolites of a class.");
	var binom = paClassHowRow("Binomial test", "fa-list-ul", !design, [
		paClassHowCell(paFigBinomInput(!design), "Ratios and a relevant list only: which members of the class the list contains."),
		paClassHowCell(paFigBinomScore(!design), "How many members are in the list, against α × n — what a list built at α flags by chance."),
		paClassHowCell(paFigBinomNull(!design), "Binomial(n, α); p is the chance of that many or more.")
	], "Needs a list built by a statistical test at α — a fold-change cut-off has no α. “Relative to this job” compares the class with the rest of your panel instead.");
	var head = function (text, arrow) {
		return '<p class="paClassHowCol"><span>' + text + '</span>' + (arrow ? '<i class="fa fa-angle-right"></i>' : '') + '</p>';
	};
	return '<section class="paClassHow" data-guides="ignore">'
		+ '<div class="paClassHowHead">'
		+ '<p class="paClassHowTitle">How the two tests work</p>'
		+ head("Input", true) + head("Score per class", true) + head("Compared with", false)
		+ '</div>'
		+ (design ? perm + binom : binom + perm)
		+ '<p class="paClassHowFoot">Both: p-values are corrected across the classes of each BRITE level; a class with '
		+ 'fewer than three measured members is reported but marked descriptive.</p>'
		+ '</section>';
}

function paClassActivityItems(model, omicName) {
	/* The class activity test this omic can support. With a design applied
	   (replicate columns collapsed to conditions) the permutation test runs on
	   the replicates and the threshold is only the fallback; without one, the
	   binomial on the relevant list runs against that threshold. */
	var compoundOmic = (model.getCompoundBasedInputOmics() || []).filter(function (o) {
		return o.omicName === omicName;
	})[0] || {};
	var design = paClassActivityDesign(compoundOmic);
	var controls = [];
	if (design && design.factors.length > 1) {
		controls.push({
			xtype: 'combo',
			fieldLabel: 'Factor to test',
			name: 'thresholdMetaboliteClassFactor',
			value: design.factors[0].id,
			displayField: 'name', valueField: 'value',
			/* The label is built from the design's condition tokens,
			   which come from a user file; BoundList's default
			   template prints the display field raw. */
			listConfig: {
				getInnerTpl: function (displayField) { return '{' + displayField + ':htmlEncode}'; }
			},
			editable: false, allowBlank: false,
			labelAlign: 'left', labelWidth: 150, width: 380,
			store: Ext.create('Ext.data.ArrayStore', {
				fields: ['name', 'value'],
				data: design.factors.map(function (f) { return [f.label, f.id]; })
			}),
			helpTip: "Your condition names encode more than one factor. The test asks whether each class responds to this one; the others are held as strata."
		});
	}
	controls.push({
		xtype: 'combo',
		fieldLabel: design ? 'Fallback threshold' : 'Threshold of your relevant list',
		name: 'thresholdMetaboliteClass',
		value: 0.05,
		displayField: 'name', valueField: 'value',
		editable: true,
		allowBlank: false,
		/* The field is editable, and until this validator existed
		   anything outside (0,1) was accepted here and then quietly
		   discarded by the server: compundsClassification only honours
		   the value when `0 < threshold < 1`, so "30", "0", "-0.5" and
		   "abc" all fell back to the automatic null. That is not a
		   harmless fallback -- it swaps a self-contained test against
		   the number you typed for a competitive test against the rest
		   of your job, which is a different hypothesis, and nothing in
		   the results said so. */
		validator: function (value) {
			if (value === 'default' || value === 'Relative to this job (automatic)'
				|| value === '' || value === null || value === undefined) {
				return true;
			}
			var proportion = Number(value);
			if (isNaN(proportion) || proportion <= 0 || proportion >= 1) {
				return '"' + value + '" is not a threshold between 0 and 1. '
					+ 'Enter the p-value or FDR cut-off you used, such as 0.05, or choose "Relative to this job".';
			}
			return true;
		},
		labelAlign: 'left',
		labelWidth: 150,
		/* Short of the panel's content box, which is 410 (446 less 18px of
		   padding either side). ExtJS hangs the help icon off the
		   field's own table in a 15px cell and `.x-box-inner` clips at the
		   container's width, so a field measured to the full 408 had its icon cut
		   down the middle. */
		width: 380,
		store: Ext.create('Ext.data.ArrayStore', {
			fields: ['name', 'value'],
			data: [['0.01', 0.01],
				['0.05', 0.05],
				['0.10', 0.10],
				['Relative to this job (automatic)', 'default']]
			/* No 1.0. A null of 1.0 says "expect every compound in the
			   class to be significant", which makes the one-sided
			   binomial p = 1.0 for every class. */
		}),
		helpTip: "The p-value or FDR cut-off you used to build the relevant-features list. Under \"no member of this class changed\" each member is flagged only by a type-I error, at that rate, so 3 of 4 flagged is p = 0.0005 at 0.05. \"Relative to this job\" instead compares the class with the rest of your panel."
	});
	/* The controls panel says what it is and what the number does, the way the
	   plan beside it does. A combo alone in a panel is a setting with no name
	   and no consequence on the page - the consequence was in a tooltip, which
	   is the one place a reader who has not already decided to hover never
	   looks. */
	/* Not a second telling of the band below, which already says what each
	   test needs and what \u201crelative to this job\u201d does. This line is about
	   the field: which number goes in it. */
	var controlsNote = design
		? 'Used only if the permutation test cannot run on this job.'
		: 'The \u03b1 you built that list at \u2014 the p-value or FDR cut-off, not a fold-change threshold.';
	var controlItems = [{
		xtype: 'box',
		cls: 'paClassSetHead',
		width: 380,
		html: '<p class="paClassSet">What you set</p>'
	}].concat(controls, [{
		xtype: 'box',
		cls: 'paClassSetFoot',
		width: 380,
		html: '<p class="paClassSetNote">' + controlsNote + '</p>'
	}]);

	var side = paClassColumnsSideBySide();
	/* Plan on the left, its controls on the right, as two panels that end level.

	   The controls used to be a bare column: one combo at the top of 440px of
	   nothing. Giving them a panel of the same build as the plan's fills that
	   space with the thing that belongs in it and makes the pair read as one
	   instrument - what will run, and the one number you get to set.

	   Every width is configured: an html box whose width is only known after
	   the layout resolves is measured WITHOUT it -- see the note on
	   #classActivityBox. */
	return [{
		xtype: 'container',
		cls: 'paClassTop' + (side ? '' : ' paClassTop-stacked'),
		layout: {type: side ? 'hbox' : 'vbox', align: side ? 'stretch' : 'left'},
		items: [{
			/* A CONTAINER, not a box, and the container is the panel.

			   Both columns have to be the same kind of component or they do not
			   end on the same line. `align: stretch` writes a height onto a
			   container and takes its border off on the way, and writes nothing at
			   all onto a plain box - so a bordered container beside a bordered box
			   came out 2px apart, in whichever direction the taller column
			   happened to be. Measured both ways: one combo in the panel put the
			   controls 2px short, two combos put them 2px long. Two containers get
			   one formula and land on one edge whatever they hold.

			   The html inside carries its own width for the reason the note on
			   #classActivityBox gives: 600 less 18px of padding either side. The
			   panel edge is an inset shadow rather than a border, so it takes no
			   width -- see the note on .paClassPlanCol in main.css. */
			xtype: 'container',
			cls: 'paClassPlanCol',
			width: 600,
			margin: '4 0 8 26',
			/* The same layout as the controls opposite, for the same reason as the
			   same xtype: a vbox and an auto layout account for a container's frame
			   differently when `align: stretch` writes a height onto it. */
			layout: {type: 'vbox', align: 'left'},
			items: [{
				xtype: 'box',
				width: 564,
				html: paClassActivityPlan(omicName, design, compoundOmic)
			}]
		}, {
			xtype: 'container',
			cls: 'paClassControls',
			/* 26 + 600 + 24 + 446 = 1096, which lands the panel's right edge on
			   1372 - the same edge the band below it ends on, so the card has one
			   right rail rather than two. */
			/* Stacked, the two panels are one above the other on one rail, so
			   they take the same width. 440 was invisible while the controls were
			   a bare column; as a panel it drew a second right edge 160px short of
			   the plan's. */
			width: side ? 446 : 600,
			/* The same margins as the plan opposite; see the note there for why
			   both columns are containers. */
			margin: side ? '4 0 8 24' : '12 0 0 26',
			/* Centred on the cross axis, so one control sits opposite the test
			   it belongs to instead of hanging from the top of an empty column.
			   `pack`, not CSS: an ExtJS box layout writes its children's
			   positions inline, and no stylesheet outranks that. */
			layout: {type: 'vbox', align: 'left', pack: side ? 'center' : 'start'},
			defaults: {border: false},
			items: controlItems
		}]
	}];
}

function paRefreshClassActivityBox(model, omicName) {
	var box = Ext.ComponentQuery.query("#classActivityBox")[0];
	if (!box || box.omicName !== omicName) return;
	box.removeAll();
	box.add(paClassActivityItems(model, omicName));
}

function PA_Step2ReplicateDetectionView() {
	this.name = "PA_Step2ReplicateDetectionView";
	this.omics = [];

	this.loadModel = function(jobModel) {
		this.model = jobModel;
		// Pull every gene/compound omic. We surface a card whenever the server
		// produced *any* detection result — including partial — so the user
		// always has the option to upload an explicit design file.
		var allOmics = (jobModel.getGeneBasedInputOmics() || [])
			.concat(jobModel.getCompoundBasedInputOmics() || []);
		this.omics = allOmics.filter(function(o) {
			var det = o.replicateDetection;
			return det && (det.status === "complete" || det.status === "partial");
		});
	};

	this.hasContent = function() {
		return this.omics.length > 0;
	};

	/**
	 * Build the contentbox HTML for one omic card. Rendered into a single
	 * outer box so the styling matches the existing Step-2 cards (the
	 * `omicSummaryBox` / `contentbox` pattern).
	 */
	this._renderOmicCard = function(omic) {
		var det = omic.replicateDetection;
		var omicId = omic.omicName.replace(/[^a-zA-Z0-9]/g, "_");
		var nSamples = det.sampleHeader.length;
		var nReplicates = det.mapping.filter(function(m) { return m >= 0; }).length;

		// Build a small preview list — capped to keep the UI compact.
		var previewRows = det.sampleHeader.slice(0, 12).map(function(sample, idx) {
			var replicateCols = det.groups[idx].map(function(colIdx) {
				return omic.omicHeader[colIdx + 1];
			}).join(", ");
			return '<li><b>' + sample + '</b> ← ' + replicateCols + '</li>';
		}).join("");
		if (det.sampleHeader.length > 12) {
			previewRows += '<li class="repDetectionMore">…and ' + (det.sampleHeader.length - 12) + ' more</li>';
		}

		var statusNote = "";
		if (det.status === "partial") {
			var unmatchedCols = det.unmatched.map(function(idx) {
				return omic.omicHeader[idx + 1];
			}).slice(0, 5).join(", ");
			statusNote =
				'<p class="repDetectionWarn"><i class="fa fa-exclamation-triangle"></i> ' +
				'Detection is incomplete — ' + det.unmatched.length + ' column(s) ' +
				'do not look like replicates (' + unmatchedCols +
				(det.unmatched.length > 5 ? ', …' : '') +
				'). Upload a design file to confirm the grouping.</p>';
		}

		// Default radio: "auto" if complete, otherwise "off".
		var autoChecked   = (det.status === "complete") ? "checked" : "";
		var offChecked    = (det.status === "complete") ? "" : "checked";

		return '' +
			'<div class="repDetectionCard" data-omic="' + omic.omicName + '" data-omicid="' + omicId + '">' +
			'  <h3>' + omic.omicName + '</h3>' +
			'  <p class="repDetectionSummary">' +
			       nSamples + ' sample(s) detected across ' + nReplicates + ' replicate column(s).' +
			'  </p>' +
			   statusNote +
			'  <ul class="repDetectionPreview">' + previewRows + '</ul>' +
			'  <div class="repDetectionRadios">' +
			'    <label><input type="radio" name="repMode_' + omicId + '" value="off" ' + offChecked + '> Show all replicates</label>' +
			'    <label><input type="radio" name="repMode_' + omicId + '" value="auto" ' + autoChecked + ' ' +
			          (det.status === "complete" ? "" : "disabled") +
			          '> Average replicates' + (det.status === "complete" ? " <span class=\"repDetectionRecommended\">(recommended)</span>" : "") + '</label>' +
			'    <label><input type="radio" name="repMode_' + omicId + '" value="manual"> Upload design file</label>' +
			'  </div>' +
			'  <div class="repDetectionManual" style="display: none;">' +
			'    <input type="file" class="repDetectionFile" accept=".tsv,.txt,.tab,.csv" />' +
			'    <p class="repDetectionFormatHint">2 columns, tab- or comma-separated: <code>sample_column &lt;TAB&gt; sample_label</code>. Header row optional.</p>' +
			'  </div>' +
			'  <div class="repDetectionStatus"></div>' +
			'</div>';
	};

	this.initComponent = function() {
		var me = this;
		if (!this.hasContent()) {
			return null;
		}

		var cards = this.omics.map(function(o) { return me._renderOmicCard(o); }).join("");

		this.component = Ext.widget({
			xtype: 'box',
			cls: "contentbox omicSummaryBox repDetectionBox",
			minHeight: 240,
			html:
				'<div id="repDetection">' +
				'  <h2>Replicate detection ' +
				'    <span class="helpTip" title="When your values file contains technical or biological replicates of the same biological sample (e.g., Ctrl_R1, Ctrl_R2), PaintOmics AI can collapse them to one cell per sample in the pathway visualization. Choose how each omic should be handled below."></span>' +
				'  </h2>' +
				'  <p>The column headers below look like <i>replicates</i> of a smaller set of biological samples. ' +
				'     Choose how PaintOmics AI should display them in the pathway visualization. ' +
				'     You can change this later from the visualization toolbar.</p>' +
				   cards +
				'</div>',
			listeners: {
				boxready: function() {
					me._wireHandlers();
					initializeTooltips(".helpTip");
				}
			}
		});

		return this.component;
	};

	/**
	 * Per-card jQuery wiring: switching the auto/off radios applies the
	 * mode immediately (no explicit Apply step). Switching to "manual"
	 * just reveals the file picker — the apply call fires once the user
	 * actually chooses a design file.
	 */
	this._wireHandlers = function() {
		var me = this;

		$(".repDetectionCard").each(function() {
			var $card = $(this);

			$card.find("input[type=radio]").change(function() {
				var mode = $card.find("input[type=radio]:checked").val();
				$card.find(".repDetectionManual").toggle(mode === "manual");
				$card.find(".repDetectionStatus")
					.removeClass("repDetectionError repDetectionOK")
					.text("");
				if (mode === "auto" || mode === "off") {
					me._applyForCard($card);
				}
			});

			$card.find(".repDetectionFile").change(function() {
				me._applyForCard($card);
			});
		});
	};

	this._applyForCard = function($card) {
		var omicName = $card.attr("data-omic");
		var mode = $card.find("input[type=radio]:checked").val();
		var $status = $card.find(".repDetectionStatus");

		$status.removeClass("repDetectionError repDetectionOK").text("Applying…");

		var send = function(designBody) {
			$.ajax({
				type: "POST",
				url: SERVER_URL_PA_APPLY_REPLICATE_MAPPING,
				contentType: "application/json",
				data: JSON.stringify({
					jobID:    this.model.getJobID(),
					omicName: omicName,
					mode:     mode,
					design:   designBody || null
				}),
				context: this,
				success: function(response) {
					if (response.success) {
						$status.addClass("repDetectionOK");
						if (response.status === "cleared") {
							$status.text("Cleared — replicates will be shown individually.");
						} else {
							$status.text(
								"Applied — " + response.sampleHeader.length + " sample(s), " +
								response.featuresUpdated + " feature(s) updated."
							);
						}
						// Mirror the result onto the model so Step-4 picks it up
						// without an extra server round-trip.
						var omic = this._findInputOmic(omicName);
						if (omic) {
							omic.sampleHeader     = response.sampleHeader;
							omic.replicateMapping = response.mapping;
							omic.replicateSource  = response.mode;
							/* The class activity box above promised a test on
							   the mapping as it was; redraw it for the new one. */
							paRefreshClassActivityBox(this.model, omicName);
						}
					} else {
						$status.addClass("repDetectionError");
						$status.text(response.errorMessage || "Server returned an error.");
					}
				},
				error: function(xhr) {
					$status.addClass("repDetectionError");
					var msg = "Network error.";
					try {
						var parsed = JSON.parse(xhr.responseText);
						if (parsed && parsed.errorMessage) msg = parsed.errorMessage;
					} catch (e) { /* keep default */ }
					$status.text(msg);
				}
			});
		}.bind(this);

		if (mode === "manual") {
			var fileInput = $card.find(".repDetectionFile")[0];
			if (!fileInput || !fileInput.files || !fileInput.files[0]) {
				$status.addClass("repDetectionError").text("Please choose a design file first.");
				return;
			}
			var reader = new FileReader();
			reader.onload = function(e) {
				send(e.target.result);
			};
			reader.onerror = function() {
				$status.addClass("repDetectionError").text("Could not read file.");
			};
			reader.readAsText(fileInput.files[0]);
		} else {
			send(null);
		}
	};

	this._findInputOmic = function(omicName) {
		var omics = (this.model.getGeneBasedInputOmics() || [])
			.concat(this.model.getCompoundBasedInputOmics() || []);
		for (var i = 0; i < omics.length; i++) {
			if (omics[i].omicName === omicName) return omics[i];
		}
		return null;
	};

	return this;
}
PA_Step2ReplicateDetectionView.prototype = new View();

/**
* "1 KEGG match" / "4 KEGG matches": the noun agrees with the count; the caller
* names the plural. The old label read "compounds founds" - and, for a
* single match, "1 compounds founds" - since 2014.
*
* @param {Number} count how many candidates were matched
* @param {String} noun "compound" or "alternative compound"
* @returns {String}
*/
function countLabel(count, singular, plural) {
	return count + " " + ((count === 1) ? singular : (plural || singular + "s"));
}

/**
* One candidate compound as plain HTML: a checkbox, the KEGG link and the data
* attributes that the delegated handlers in PA_Step2JobView read back.
*
* This replaces PA_Step2CompoundView, which built an Ext box plus its own
* Ext.tip.ToolTip per candidate. Those components, multiplied by the number of
* matched compounds, are what froze step 2 on a compound-heavy job.
*
* @param {Compound} compound the candidate
* @returns {String}
*/
function renderCompoundCandidate(compound, aiPickedID) {
	var compoundID = compound.getID();
	var safeID = Ext.String.htmlEncode(compoundID);
	var safeName = Ext.String.htmlEncode(compound.getName());
	// Marks the one candidate the AI chose, so a card with four ticked-looking
	// rows still says WHICH row the machine is responsible for.
	var picked = (aiPickedID && compoundID === aiPickedID) ? " aiPickedCandidate" : "";

	// The id is printed. "L-Alanine", "D-Alanine" and "Alanine" are three
	// different compounds, and C00041 / C00133 / C01401 is how anyone checks
	// which one they are ticking.
	return '' +
	'<div class="metaboliteCompound' + picked + '" data-compound-id="' + safeID + '" data-compound-name="' + safeName + '">' +
	'  <input type="checkbox"' + (compound.isSelected() ? " checked" : "") + ' name="metabolite" value="' + safeID + '">' +
	'  <a href="http://www.kegg.jp/dbget-bin/www_bget?' + encodeURIComponent(compoundID) + '" target="_blank">' + safeName + '</a>' +
	'  <code class="metaboliteId">' + safeID + '</code>' +
	'</div>';
}

function PA_Step2CompoundSetView() {
	/***********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step2CompoundSetView";

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.loadModel = function(model) {
		this.model = model;
	};

	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* True when this input name actually leaves the user something to decide.
	*
	* A set with exactly one candidate, no alternatives, and that candidate
	* already selected is fully resolved: its card was a single pre-ticked
	* checkbox that could only be turned off. Skipping those is what makes a
	* job with thousands of matched names openable.
	*
	* The "already selected" half of the test is not cosmetic. The cross-box
	* de-duplicator in JobController.step1OnFormSubmitHandler unselects the
	* losing copy when two input names propose the same KEGG compound, so a
	* lone candidate can arrive with selected === false. That one renders
	* unchecked today and the user must be able to tick it, so it keeps its
	* card.
	*
	* @returns {Boolean}
	*/
	this.needsDisambiguation = function() {
		var mainCompounds = this.model.getMainCompounds();
		var otherCompounds = this.model.getOtherCompounds();

		// Nothing matched at all: there is no card to draw.
		if (mainCompounds.length + otherCompounds.length === 0) {
			return false;
		}
		if (otherCompounds.length > 0) {
			return true;
		}

		return !(mainCompounds.length === 1 && mainCompounds[0].isSelected() === true);
	};

	/**
	* The whole card as HTML. `index` is this view's position in
	* PA_Step2JobView.items and is written into the card so a delegated handler
	* can map a click back to this view without walking a component tree.
	*
	* @param {Number} index
	* @returns {String}
	*/
	/**
	* Tick exactly one candidate in this set and untick every other.
	*
	* The only mutation the AI path performs on the model. It is scoped to this
	* one compound set by construction, so a suggestion can never reach across
	* to another input name's candidates.
	*
	* @param {String} keggID the candidate to keep
	* @returns {Boolean} whether anything actually changed
	*/
	this.selectOnly = function(keggID) {
		var compounds = this.model.getMainCompounds().concat(this.model.getOtherCompounds());

		// Membership is decided BEFORE anything moves. Written the other way -
		// untick as you scan, check afterwards - an id this set does not
		// contain clears every tick in it on the way to returning false, which
		// is the worst outcome available: the user loses a selection they made,
		// to an answer that was never valid for this card.
		var found = compounds.some(function(compound) {
			return compound.getID() === keggID;
		});
		if (!found) {
			return false;
		}

		var changed = false;
		compounds.forEach(function(compound) {
			var wanted = (compound.getID() === keggID);
			if (compound.selected !== wanted) {
				compound.selected = wanted;
				changed = true;
			}
		});
		return changed;
	};

	/**
	* The "AI" / "AI unsure" chip on a card, with its reason as the tooltip.
	*
	* @returns {String}
	*/
	this.renderAIBadge = function() {
		var state = this.aiState;
		if (!state) {
			return "";
		}
		// Three states, three colours, and the deterministic one is NOT the
		// AI colour. A rule that matched a name did not consult a model, and
		// dressing it in the AI blue would claim credit the feature has not
		// earned -- the point of the chip is to say what touched this card.
		var kind = state.status === "unsure" ? "unsure"
			: (state.tier === "ai" ? "ai" : "auto");
		var label = {unsure: "AI unsure", ai: "AI", auto: "Auto"}[kind];
		var icon = {unsure: "fa-question-circle", ai: "fa-magic", auto: "fa-check"}[kind];
		var reason = Ext.String.htmlEncode(state.reason || "");

		return '<span class="aiBadge aiBadge-' + kind + '"' +
		       (reason ? ' title="' + reason + '"' : "") + '>' +
		       '<i class="fa ' + icon + '"></i> ' + label + '</span>';
	};

	/**
	* Why the machine chose what it chose, as a line on the card rather than
	* only a tooltip on the chip. The reason is the part a reader checks, and
	* a hover target is not where anyone looks for it.
	*
	* @returns {String}
	*/
	this.renderAIReason = function() {
		var state = this.aiState;
		if (!state || !state.reason) {
			return "";
		}
		var kind = state.status === "unsure" ? "unsure"
			: (state.tier === "ai" ? "ai" : "auto");
		var mark = (kind === "ai" && typeof getAIMark === "function")
			? getAIMark()
			: '<i class="fa ' + (kind === "unsure" ? "fa-question-circle" : "fa-check") + '"></i>';
		// The rule-based reasons arrive as fragments ("the only matching
		// candidate..."); on a line of their own they read as sentences.
		var reason = String(state.reason);
		reason = reason.charAt(0).toUpperCase() + reason.slice(1);
		// data-guides="ignore": icon-led, like the AI offer's title. The icon
		// sits under the checkboxes and the text under the candidate names.
		return '<p class="aiReason aiReason-' + kind + '" data-guides="ignore">' + mark +
		       '<span>' + Ext.String.htmlEncode(reason) + '</span></p>';
	};

	this.renderCard = function(index) {
		var mainCompounds = this.model.getMainCompounds();
		var otherCompounds = this.model.getOtherCompounds();

		var aiPickedID = (this.aiState && this.aiState.keggID) || null;
		var cardClass = "contentbox metaboliteBox";
		if (this.aiState) {
			cardClass += this.aiState.status === "unsure" ? " aiBox-unsure"
				: (this.aiState.tier === "ai" ? " aiBox-ai" : " aiBox-auto");
		}

		var html =
		'<div class="' + cardClass + '" data-compoundset="' + index + '">' +
		// Name and the count on one line: the count is a property of the
		// name, not a heading of its own.
		'  <div class="metaboliteHead">' +
		// The chip is a sibling of the heading, not part of the name.
		'    <h3 class="metaboliteTitle">' + Ext.String.htmlEncode(this.model.getTitle()) + '</h3>' +
		     this.renderAIBadge() +
		'    <span class="metaboliteCount">' + countLabel(mainCompounds.length, "KEGG match", "KEGG matches") + '</span>' +
		'  </div>' +
		'  <div class="mainCompoundsPanel">' +
		mainCompounds.map(function(compound) {
			return renderCompoundCandidate(compound, aiPickedID);
		}).join("") +
		'  </div>' +
		this.renderAIReason();

		// Only offer the control when there is something behind it: the old
		// markup printed "0 alternative compounds founds" with a Show link over
		// an empty container. The count travels on the link so that opening
		// and closing it swap the chevron and keep the number.
		if (otherCompounds.length > 0) {
			var more = countLabel(otherCompounds.length, "more match", "more matches");
			html +=
			'  <a class="showOtherCompoundsButton" href="javascript:void(0)" data-label="' + more + '">' +
			'<i class="fa fa-chevron-right"></i> ' + more + '</a>' +
			'  <div class="otherCompoundsPanel" style="display: none;"></div>';
		}

		return html + '</div>';
	};

	/**
	* The alternative candidates, built on demand when the card is expanded.
	*
	* @returns {String}
	*/
	this.renderOtherCompounds = function() {
		var aiPickedID = (this.aiState && this.aiState.keggID) || null;
		return this.model.getOtherCompounds().map(function(compound) {
			return renderCompoundCandidate(compound, aiPickedID);
		}).join("");
	};

	return this;
}
PA_Step2CompoundSetView.prototype = new View();

/**
* The mapping donut's own data labels cannot be trusted at this size:
* Highcharts drops any label that does not fit, and in a 327x195 chart neither
* of the two fits - "Mapped features", the number the whole panel exists to
* report, was never drawn on any omic, and an omic mapped at 100% drew a bare
* ring with no number at all. Rather than fight the label distributor, the
* counts are printed under the chart as HTML, which also gives the compound
* based omics - which have no donut, only a "See Compounds disambiguation"
* note - somewhere to state their numbers.
*
* @param {Number} mappedFeatures features matched against the databases
* @param {Number} unmappedFeatures features left unmatched
* @returns {String} the caption markup
*/
function mappingSummaryCaption(mappedFeatures, unmappedFeatures) {
	var mapped = Number(mappedFeatures) || 0;
	var unmapped = Number(unmappedFeatures) || 0;
	var total = mapped + unmapped;

	// An omic with no input features at all must not divide by zero.
	var mappedPct = (total > 0) ? Math.round(mapped / total * 100) : 0;
	var unmappedPct = (total > 0) ? (100 - mappedPct) : 0;

	var row = function(color, count, label, percentage) {
		return '<div>' +
			'<span style="display:inline-block; width:9px; height:9px; border-radius:50%; vertical-align:middle; background:' + color + ';"></span> ' +
			'<b>' + count.toLocaleString() + '</b> ' + label + ' (' + percentage + '%)' +
			'</div>';
	};

	return '<div class="mappingSummaryCaption" style="text-align:center; font-size:12px; line-height:1.6; padding:0 5px 8px;">' +
		row("rgb(106, 208, 150)", mapped, "mapped", mappedPct) +
		row("rgb(250, 112, 112)", unmapped, "unmapped", unmappedPct) +
		'</div>';
}

/**
* The mapped/unmapped ring, drawn the same way for every omic.
*
* Extracted because the compound omics were not getting one. The old code drew
* this only for gene-based omics and gave a compound omic
* `<b>See Compounds disambiguation</b>`, 60px down an otherwise empty 327x195
* box -- a slot that looked broken beside four cards that all had a chart, and
* a line that looked like a link and was not one.
*
* The reason given was that a compound omic has no per-database breakdown, and
* that is true -- but the breakdown was only ever the tooltip's `note`. The
* ring itself is mapped against unmapped, which every omic has.
*
* @param {String} divName the panel's id prefix
* @param {String} omicName series name, shown in the tooltip
* @param {Number} mapped features that resolved to an identifier
* @param {Number} unmapped features that did not
* @param {String} note per-database detail for the tooltip, "" when there is none
*/
function renderMappingDonut(divName, omicName, mapped, unmapped, note) {
	$('#' + divName + 'mapping_summary_plot').highcharts({
		chart: {type: 'pie', height: 195},
		title: {
			text: "Mapped/Unmapped features",
			style: {"fontSize": "13px"}
		},
		credits: {enabled: false},
		tooltip: {
			pointFormat: '{series.name}: <b>{point.y}</b><br/><br/>{point.options.note}<br/>'
		},
		plotOptions: {
			pie: {
				// Off on purpose: see mappingSummaryCaption. At this size
				// Highcharts hid the "Mapped features" label on every omic and
				// clipped the other one, so the ring is now the proportion and
				// the caption underneath carries the numbers. With no labels to
				// leave room for, the ring can sit in the middle of its box.
				dataLabels: {enabled: false},
				center: ['50%', '45%']
			}
		},
		series: [{
			type: 'pie',
			name: omicName,
			size: 100,
			innerSize: '30%',
			data: [{
				name: 'Unmapped features',
				y: Number.parseFloat(unmapped),
				color: "rgb(250, 112, 112)",
				note: ""
			}, {
				name: 'Mapped features',
				y: Number.parseFloat(mapped),
				color: "rgb(106, 208, 150)",
				note: note
			}]
		}]
	});
}

function PA_OmicSummaryPanel(omicName, dataDistribution, isCompoundOmic) {
	/***********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.omicName = omicName;
	//   0        1       2    3    4    5     6,   7   8      9        10
	//[MAPPED, UNMAPPED, MIN, P10, Q1, MEDIAN, Q3, P90, MAX, MIN_IR, Max_IR]
	this.dataDistribution = dataDistribution;

	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.initComponent = function() {
		var me = this;

		var divName = this.omicName.replace(/[^A-Z0-9]/ig, "_").toLowerCase() + "_";

		this.component = Ext.widget({
			xtype: "box",
			cls: "contentbox omicSummaryBox",
			html: '<h3 class = "metaboliteTitle" style="display:inline-block;margin-right: 20px;">' + this.omicName + '</h3>' +
			'<div>' +
			'  <div style="height:195px; overflow:hidden; width:50%; float: right;" id="' + divName + 'data_dstribution_plot"></div>' +
			// The chart keeps its fixed height; the caption sits below it inside
			// the same half-width column, which is what makes the counts
			// readable regardless of what the chart decides to draw.
			'  <div style="width:50%;">' +
			'    <div style="height:195px; overflow:hidden;" id="' + divName + 'mapping_summary_plot"></div>' +
			'    <div id="' + divName + 'mapping_summary_caption"></div>' +
			'  </div>' +
			'	 <div style="margin: 0 auto; text-align: center" id="customvalues_' + divName + '_summary"></div>' +
			'</div>',
			listeners: {
				boxready: function() {
					// Declared here so the caption below can read it whichever
					// branch filled it in; these three used to be implicit globals.
					var mappedFeatures, mappedInfo, added_info;

					// if (me.dataDistribution[1] !== -1 && me.dataDistribution[0] !== -1) {
					if (! isCompoundOmic) {
						// Mapped features can differ between used databases
						mappedInfo = me.dataDistribution[0];

						added_info = "";

						if (! Object.keys(mappedInfo).length) {
							mappedFeatures = mappedInfo;
						} else {
							if ("Total" in mappedInfo) {
								mappedFeatures = mappedInfo["Total"];

								added_info = Object.keys(mappedInfo).map(function(db) {
									return("• " + db + ": " + mappedInfo[db]);
								}).join('<br />');
							} else {
								mappedFeatures = mappedInfo[Object.keys(mappedInfo)[0]];
								added_info = "(KEGG database)";
							}
						}

						renderMappingDonut(divName, me.omicName, mappedFeatures,
							me.dataDistribution[1], added_info);
					} else {
						// A compound omic is matched once against KEGG compound ids, so
						// there is no per-database breakdown for the TOOLTIP -- and that
						// is all that is missing. The mapped and unmapped counts it does
						// have are the same two numbers every other omic's ring is drawn
						// from. They were being discarded, and the slot printed a bold
						// line 60px down an otherwise empty 327x195 box.
						mappedFeatures = me.dataDistribution[0];
						added_info = "";
						renderMappingDonut(divName, me.omicName, mappedFeatures,
							me.dataDistribution[1], added_info);
					}

					// A real control now. It used to be a bare <b> styled like a
					// link: it looked clickable and did nothing when clicked.
					$('#' + divName + 'mapping_summary_caption')
						.html(mappingSummaryCaption(mappedFeatures, me.dataDistribution[1]) +
							(isCompoundOmic
								? '<div class="mappingSummaryJump">' +
								  '<a href="javascript:void(0)" class="compoundsJumpLink">' +
								  'See compounds disambiguation</a></div>'
								: ''))
						.off("click.paJump")
						.on("click.paJump", ".compoundsJumpLink", function() {
							var target = document.querySelector(".compoundsIntroBox");
							if (target) {
								target.scrollIntoView({block: "start", behavior: "smooth"});
							}
						});

					//   0        1       2    3    4    5     6,   7   8      9        10
					//[MAPPED, UNMAPPED, MIN, P10, Q1, MEDIAN, Q3, P90, MAX, MIN_IR, Max_IR]
					//TODO REVISAR...
					//                    var yAxisMin = Math.floor(me.dataDistribution[9]) ;
					//                    var yAxisMax = Math.floor(me.dataDistribution[10]) + 0.5;
					//                    debugger;


					// TODO: leave this prepared in case it's needed in the frontpage
					// Ext.create('Ext.slider.MultiCustom', {
					// 		 renderTo: "customvalues_" + divName + '_summary',
					// 		 name: "customslider_" + me.omicName,
					// 		 width: 240,
					// 		 minValue: me.dataDistribution[2],
					// 		 maxValue: me.dataDistribution[8],
					// 		 customValues: [me.dataDistribution[2], me.dataDistribution[8]]
					//  });

					$('#' + divName + 'data_dstribution_plot').highcharts({
						chart: {
							type: 'boxplot',
							height: 195,
							inverted: true
						},
						credits: {enabled: false},
						title: {
							text: "Data distribution",
							style: {
								"fontSize": "13px"
							}
						},
						legend: {enabled: false},
						plotOptions: {
							boxplot: {
								medianColor: "#ff0000"
							}
						},
						xAxis: {
							labels: {
								enabled: false
							},
							title: null
						},
						tooltip: {
							formatter: function() {
								var text = '<span style="font-size:9px; text-align: right;"><em>' + me.omicName + '</em><br/>';
								text += "<b>Min (outliers inc.): </b>" + (me.dataDistribution[2]).toFixed(4) + '<br/>';
								text += "<b>Min value    : </b>" + (this.point.low / 10).toFixed(4) + '<br/>';
								text += "<b>Percentile 10: </b>" + (me.dataDistribution[3]).toFixed(4) + '<br/>';
								text += "<b>Q1           : </b>" + (this.point.q1 / 10).toFixed(4) + '<br/>';
								text += "<b>Median       : </b>" + (this.point.median / 10).toFixed(4) + '<br/>';
								text += "<b>Q3           : </b>" + (this.point.q3 / 10).toFixed(4) + '<br/>';
								text += "<b>Percentile 90: </b>" + (me.dataDistribution[7]).toFixed(4) + '<br/>';
								text += "<b>Max value    : </b>" + (this.point.high / 10).toFixed(4) + '<br/>';
								text += "<b>Max (outliers inc.): </b>" + (me.dataDistribution[8]).toFixed(4) + '<br/></span>';

								return text;
							}
						},
						yAxis: {
							labels: {
								formatter: function() {
									return this.value / 10;
								}
							},
							gridLineWidth: 0.1,
							plotLines: [{
								value: me.dataDistribution[3] * 10,
								color: '#001dff',
								width: 1,
								dashstyle: "DashDot",
								label: {
									text: 'p10',
									align: 'center',
									style: {
										color: 'gray'
									}
								}
							}, {
								value: me.dataDistribution[7] * 10,
								color: '#001dff',
								width: 1,
								dashstyle: "DashDot",
								label: {text: 'p90',align: 'center', style: {color: 'gray'}}
							}]
						},
						//   0        1       2    3    4    5     6,   7   8      9        10
						//[MAPPED, UNMAPPED, MIN, P10, Q1, MEDIAN, Q3, P90, MAX, MIN_IR, Max_IR]
						series: [{
							name: 'Values',
							data: [
								[me.dataDistribution[9] * 10, me.dataDistribution[4] * 10, me.dataDistribution[5] * 10, me.dataDistribution[6] * 10, me.dataDistribution[10] * 10]
							],
							tooltip: null
						}],
					});


				}
			}
		});

		return this.component;
	};
	return this;
}
PA_OmicSummaryPanel.prototype = new View();

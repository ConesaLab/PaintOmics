//# sourceURL=DM_miRNA2GenesJobView.js
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
* - DM_miRNA2GenesJobView
*
*/
function DM_miRNA2GenesJobView() {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "DM_miRNA2GenesJobView";
	this.nFiles = 4;
	this.exampleMode = false;
	/* The card this page shows, the same one a Step 1 Pairwise miRNA card is. No
	   "My features are already mapped to Gene IDs" box (allowToogle): mapping to
	   genes is what this tool does. The correlation box stays
	   (allowCorrelationToogle): it turns on the gene expression file, Report, score
	   method, selection method and cutoff, which this page's help describes. Both
	   boxes were hidden from the first commit (54ff9dc3), so those settings sat
	   greyed out and could not be turned on. It starts unticked, as on Step 1, so a
	   run whose box is left alone sends what it always sent. */
	var panelOptions = {removable: false, allowToogle: false, allowCorrelationToogle: true};
	/*********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.isExampleMode = function () {
		return this.exampleMode;
	};
	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.resetViewHandler = function () {
		var form = this.getComponent().queryById("omicSubmittingForm");
		form.getForm().reset();
		/* The correlation box is plain HTML, which the form's reset does not reach,
		   so a ticked box stayed ticked with its settings on. Unticked through its
		   own change handler, which greys the settings out again and flips the two
		   rows it decides between back to "relevant associations file required".
		   Not in example mode: there it is ticked and locked (setExampleMode). */
		$(form.getEl().dom).find("input[id$=_corrOptions]:checked:not(:disabled)")
			.prop("checked", false).trigger("change");
	};

	this.submitFormHandler = function () {
		this.controller.fromMiRNA2GenesOnFormSubmitHandler(this);
	};
	
	this.setExampleModeHandler = function() {
		var panel, fileField, omicSubmittingPanels;
		var omicForm = this.getComponent().queryById("omicSubmittingForm");
		var mirnaPanel = new MiRNAOmicSubmittingPanel(0, panelOptions);

		this.exampleMode = true;
		mirnaPanel.setExampleMode();
		
		// Delete old element
		omicForm.remove(omicForm.items.get(1));
		omicForm.remove(omicForm.items.get(1));
		omicForm.add(mirnaPanel.getComponent(), {xtype: "box", html: '<div style="height:150px"></div>'});
		omicForm.doLayout();
		
		$('#mainViewCenterPanel').scrollTop(omicForm.getEl().dom.offsetTop - 60);
	};

	this.checkForm = function () {
		var items = this.getComponent().query("container[cls=omicbox miRNABasedOmic]");
		var emptyFields = 0;

		for (var i in items) {
			if (items[i].isValid() !== true) {
				return false;
			}
		}
		return true;
	};

	this.showMyDataPanel = function () {
		this.controller.showMyDataPanelClickHandler(this);
	};

	this.initComponent = function () {
		var me = this;
		this.component = Ext.widget({
			xtype: "container", minHeight: 800,
			items: [{
				xtype: "box", cls: "toolbar secondTopToolbar", html:
				'<a class="button btn-danger btn-right" id="resetButton"><i class="fa fa-refresh"></i> Reset</a>' +
				'<a class="button btn-success btn-right" id="runButton"><i class="fa fa-play"></i> Run miRNA2Genes</a>' + 
				'<a class="button btn-secondary btn-right" id="exampleButton"><i class="fa fa-file-text-o"></i> Load example</a>'
			},{
				/* No max-width here. #mainViewCenterPanel already caps the page column
				   at --pa-page-max and centres it, so a second 1300px cap only pinned
				   this page's cards to the left and left a ~80px dead strip down the
				   right - the same thing that was removed from the step 1 hero. */
				xtype: "container", style: "margin-top:50px;",
				items: [
					{
						xtype: 'box', html:
						'<div id="about" class="contentbox">' +
						'   <h2>From miRNAs to Genes</h2>' +
						'   <img alt="miRNA2Genes" src="resources/images/logo_mirna2genes.png" style="width: 300px;margin:20px;">' +
						'   <div class="po-tool-intro">' +
						'       <h4>Match miRNAs to their target protein-coding genes</h4> ' +
						'       <p>This tool processes your input miRNA quantification data and assigns the expression values to the known list of target genes for each miRNA. The tool includes many options to customize the resulting gene list. See below for more information.</p>' +
						'       <p><b>More info:</b></p>'+
						'       <ul><li><a href="https://conesalab.github.io/PaintOmics/2_1_accepted_input/#matching-mirnas-to-genes-rgmatch" target="_blank">Matching miRNAs to genes</a></li>' +
						'   </div>' +
						'</div>' +
						'<div class="contentbox">' +
						'   <h3>About input data</h3>' +
						'   <p>In order to compute the associations, <strong>miRNA2Genes</strong> needs the following input data:</p>' +
						'   <ul>' +
						'     <li>A <strong>tabulated</strong> file containing the quantification values for all the miRNAs (Figure 1.A).</li>' +
						'     <li>A <strong>tabulated</strong> file containing the list of <i>miRNA &rarr; target gene</i> associations (Figure 1.C).</li>' +
						'   </ul>' +
						/* It said "Two optional files can be added": the relevant miRNAs and
						   the gene expression. The form below needs one of a relevant
						   associations file, which this help never named, or the gene
						   expression with the correlation box ticked (isValid in
						   MiRNAOmicSubmittingPanel), and that box was hidden on this page. */
						'   <p>To decide which <i>miRNA &rarr; target</i> pairs are relevant, it also needs one of these:</p>' +
						'   <ul>' +
						'     <li>A list of the pairs you already know to be relevant (<i>Relevant associations file</i>). The pairs it lists are marked relevant, and the correlation settings are not used.</li>' +
						/* It said this file "will be used for filtering the matched targets":
						   the cutoff marks pairs as relevant and drops none (see STEP 5.2 in
						   MiRNA2GeneJob.py). */
						'     <li>A mRNA-Seq quantification file (<i>Gene expression dataset</i>), after ticking <i>Automatically select relevant associations using correlation</i>. It is used to score each miRNA &rarr; target pair by the correlation between gene expression and miRNA expression, and to mark as relevant the pairs that pass the cutoff.<br>Its gene names must match those in the miRNA &rarr; target file (Figure 1.D).</li>' +
						'   </ul>' +
						'   <p>A list of relevant miRNAs, usually the differentially expressed miRNAs, can be added too (Figure 1.B).</p>' +
						'   <p>Identifiers are not restricted: any miRNA and gene naming works, as long as the quantification and miRNA &rarr; target files use the same convention (for example, if the quantification file uses "mmu-miR-XXX", the miRNA &rarr; target file must use the same names).</p>' +
						'   <img alt="Figure 1: A, miRNA quantification; B, relevant miRNAs; C, miRNA-to-target associations; D, gene expression" src="resources/images/paintomics_input_figure5b.png" style="max-width: 800px;margin: auto;display: block;">' +
						// Scoped to this converter's own datasets, which is what the
						// "Load example" button beside it runs. Previously a static
						// zip that no longer matched either.
						'	<div style="text-align: center;height: 35px;margin-top: 20px;"><a class="button btn-default btn-right" target="_blank" style="float: none;" href="' + SERVER_URL_EXAMPLE_DATASETS_DOWNLOAD + '?pipeline=mirna2genes"><i class="fa fa-download" aria-hidden="true"></i> Download example data</a></div>' +
						'   <h3>About multiple target genes</h3>' +
						'   <p>Usually, for each miRNA there are numerous known target genes. However, the presence of a miRNA does not mean that a certain target gene is being regulated for that miRNA. Hence, it is necessary to discriminate those genes that may be affected by the action of a miRNA from the complete list of potential target genes for that miRNA. With <i>Automatically select relevant associations using correlation</i> ticked, these settings of the form decide which pairs are marked relevant.</p>' +
						'   <ul>' +
						/* It said "reporting only the target genes for the relevant miRNAs
						   (e.g. the DE miRNAs), ignoring the rest". The rest were never
						   left out: every pair is in every results file under both
						   choices, and only which pairs can be marked relevant changes
						   (see STEP 5.2 in MiRNA2GeneJob.py). Named by the form's labels,
						   which this page now shows (see panelOptions). */
						'     <li><b>Report</b>: if the list of relevant miRNAs is provided, you can choose whether the pairs of every miRNA can be marked as relevant (<i>All features</i>, the default), or only the pairs of the relevant miRNAs, e.g. the DE miRNAs (<i>Only relevant features</i>). The other miRNAs are not dropped: their pairs stay in the results with their values, and are never marked relevant.</li>' +
						/* It said the cutoff "determines the selection of the genes that are
						   finally reported", and the picture's text "targets kept or dropped":
						   like Report, the cutoff only marks pairs, and the picture's Report
						   column reads True or False. */
						'     <li><b>Score method</b>, <b>Selection method</b> and <b>Filter cutoff</b>: each miRNA &rarr; target pair is scored by the correlation between the miRNA\'s values and its target gene\'s, by Kendall (the default), Spearman or Pearson. A miRNA usually represses its target, so a negative correlation is expected, and the cutoff on the correlation decides which pairs are marked as relevant. The other pairs are not dropped: they stay in the results with their scores. By default a pair is marked when its correlation is below -0.5; the selection method can instead ask for one above the cutoff (positive), or one whose size is above it, whatever its sign (absolute).</li>' +
						'   </ul>' +
						'   <img alt="Worked example: the Kendall correlation between a miRNA and each of two targets against a cutoff of -0.6, and whether each pair is marked relevant (True) or not (False)" src="resources/images/paintomics_input_figure5c.png" style=" width: 750px; margin: 20px auto; display: block; ">'+
						'</div>'
					},{
						xtype: 'form', bodyCls: "contentbox", cls: "paToolForm", itemId: "omicSubmittingForm",
						/* The inset goes on the panel, not on its body: ExtJS writes an
						   explicit width onto the body, so .contentbox's own 10px margin was
						   added on top of that width and this card finished 20px wider than
						   the two above it. See .paToolForm in main.css.

						   Zero horizontally now: the 10 mirrored the horizontal margin
						   `.contentbox` used to carry, and that margin is gone - the page
						   gutter on #mainViewCenterPanel is the only inset. Left at 10 this
						   card's "Data uploading" heading would sit 10px right of every
						   other card's heading on the page. */
						margin: '0', style: "padding-bottom:50px;",
						layout: {type: 'vbox', align: "stretch"},
						defaults: {labelAlign: "right", labelWidth: 220},
						items: [
							{xtype: "box", flex: 1, html: '<h2>Data uploading</h2><h3>1. Choose the files to upload </h3>'},
							new MiRNAOmicSubmittingPanel(0, panelOptions).getComponent(),
							{xtype: "box", html: '<div style="height:150px"></div>'},
						]
					}
				]
			},
		],
		listeners: {
			boxready: function () {
				$("#runButton").click(function () {
					me.submitFormHandler();
				});
				$("#resetButton").click(function () {
					me.resetViewHandler();
				});
				$("#exampleButton").click(function() {
					me.setExampleModeHandler();
				});
			},
			beforedestroy: function () {
				var model = me.getModel();
				
				if (model) {
					model.deleteObserver(me);
				}
			}
		}
	});
	return this.component;
};
return this;
}
DM_miRNA2GenesJobView.prototype = new View();

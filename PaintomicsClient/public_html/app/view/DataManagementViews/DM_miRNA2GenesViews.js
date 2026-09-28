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
		this.getComponent().queryById("omicSubmittingForm").getForm().reset();
	};

	this.submitFormHandler = function () {
		this.controller.fromMiRNA2GenesOnFormSubmitHandler(this);
	};
	
	this.setExampleModeHandler = function() {
		var panel, fileField, omicSubmittingPanels;
		var omicForm = this.getComponent().queryById("omicSubmittingForm");
		var mirnaPanel = new MiRNAOmicSubmittingPanel(0, {removable: false, allowToogle: false});

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
						'   <p>Two optional files can be added for more accurate results.</p>' +
						'   <ul>' +
						'     <li>A list of relevant miRNAs, usually the differentially expressed miRNAs (Figure 1.B).</li>' +
						'     <li>A mRNA-Seq quantification file that will be used for filtering the matched targets based on the correlation between gene expression and miRNA expression.<br>Its gene names must match those in the miRNA &rarr; target file (Figure 1.D).</li>' +
						'   </ul>' +
						'   <p>Identifiers are not restricted: any miRNA and gene naming works, as long as the quantification and miRNA &rarr; target files use the same convention (for example, if the quantification file uses "mmu-miR-XXX", the miRNA &rarr; target file must use the same names).</p>' +
						'   <img alt="Figure 1: A, miRNA quantification; B, relevant miRNAs; C, miRNA-to-target associations; D, gene expression" src="resources/images/paintomics_input_figure5b.png" style="max-width: 800px;margin: auto;display: block;">' +
						// Scoped to this converter's own datasets, which is what the
						// "Load example" button beside it runs. Previously a static
						// zip that no longer matched either.
						'	<div style="text-align: center;height: 35px;margin-top: 20px;"><a class="button btn-default btn-right" target="_blank" style="float: none;" href="' + SERVER_URL_EXAMPLE_DATASETS_DOWNLOAD + '?pipeline=mirna2genes"><i class="fa fa-download" aria-hidden="true"></i> Download example data</a></div>' +
						'   <h3>About multiple target genes</h3>' +
						'   <p>Usually, for each miRNA there are numerous known target genes. However, the presence of a miRNA does not mean that a certain target gene is being regulated for that miRNA. Hence, it is necessary to discriminate those genes that may be affected by the action of a miRNA from the complete list of potential target genes for that miRNA. Assuming that all files explained above are provided, miRNA2Genes includes the following selection strategies.</p>' +
						'   <ul>' +
						'     <li>If the list of relevant miRNAs is provided, you can choose between reporting the target genes for all miRNAs in the input files, or reporting only the target genes for the relevant miRNAs (e.g. the DE miRNAs), ignoring the rest.</li>' +
						'     <li>If the transcriptomics quantification file is provided, reported target genes can be discriminated based on the existing correlation between the quantification for a miRNA and the codified by target genes. Usually, it is expected a negative correlation between miRNA and target genes being regulated, and the usage of a cutoff for correlation value determines the selection of the genes that are finally reported.</li>' +
						'   </ul>' +
						'   <img alt="Worked example: targets kept or dropped by the Kendall correlation between miRNA and target expression against a cutoff" src="resources/images/paintomics_input_figure5c.png" style=" width: 750px; margin: 20px auto; display: block; ">'+
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
							new MiRNAOmicSubmittingPanel(0, {removable: false, allowToogle: false}).getComponent(),
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

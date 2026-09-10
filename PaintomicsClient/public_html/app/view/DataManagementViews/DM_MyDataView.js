/* global Ext, application, SERVER_URL_DM_UPLOAD_FILE, UPLOAD_TIMEOUT */

//# sourceURL=DM_MyDataView.js
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
*
* THIS FILE CONTAINS THE FOLLOWING COMPONENT DECLARATION
* - DM_MyDataListView
* - DM_MyDataSummaryPanel
* - DM_MyDataFileListView
* - DM_MyDataJobListView
* - DM_GTFFileListView
* - MyFilesSelectorButton
* - MyFilesSelectorDialog
* - GTFSelectorDialog
* -
*
******************************************************************************/
function DM_MyDataListView() {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "DM_MyDataListView";
	this.myDataSummaryPanel = null;
	this.myDataFileListView = null;
	this.myDataJobListView = null;

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.updateContent = function(updateFiles, updateJobs) {
		if (updateFiles !== false) {
			this.myDataFileListView.updateContent();
		}
		if (updateJobs !== false) {
			this.myDataJobListView.updateContent();
		}
	};

	this.updateSummary = function(dataSummary) {
		this.myDataSummaryPanel.updateContent(dataSummary);
	};

	this.changePassButtonClickHandler = function(){
		application.getController("UserController").changePassLinkClickHandler();
	};

	this.initComponent = function() {
		var me = this;

		this.myDataSummaryPanel = new DM_MyDataSummaryPanel();
		this.myDataFileListView = new DM_MyDataFileListView().setAllowRowRemoving(true, true).setParent(me).setController(me.getController());
		this.myDataJobListView = new DM_MyDataJobListView().setAllowRowRemoving(true, true).setParent(me).setController(me.getController());

		this.component = Ext.widget(
			{
				xtype: "container",
				padding: '10',
				border: 0,
				maxWidth: 1300,
				items: [
					{
						xtype: "box", cls: "toolbar secondTopToolbar", html:
						'<a class="button btn-secondary" id="uploadNewFilesButton"><i class="fa fa-cloud-upload"></i> Upload new files</a>'
					},
					{
						xtype: 'container',
						layout: 'column',
						style: "max-width:1300px; margin: 5px 10px; margin-top:50px;",
						items: [{
							xtype: 'box', cls: "contentbox omicSummaryBox poMyDataCard", minHeight: 230, html:
							/* The account card was a two-column table of bold labels, and one
							   of its three rows was a Password row showing twelve asterisks -
							   a value that is not the password, not the right length, and
							   tells the reader nothing they did not already know. It is gone;
							   what belongs there is the action, which is now a real button
							   rather than a "Click here to..." link.

							   Labels above values rather than beside them: the two are a pair,
							   and a right-aligned label column only earns its keep on a long
							   form. `<dl>` because that is what this is. */
							'<div id="about">'+
							'  <h2>My account</h2>'+
							'  <dl class="po-account">' +
							'    <div class="po-account-row"><dt>User name</dt><dd>' + Ext.String.htmlEncode(Ext.util.Cookies.get('userName') || '—') + '</dd></div>' +
							'    <div class="po-account-row"><dt>Email</dt><dd>' + Ext.String.htmlEncode(Ext.util.Cookies.get('lastEmail') || '—') + '</dd></div>' +
							'  </dl>' +
							'  <p data-guides="ignore" class="formActionRow po-account-actions"><a class="button btn-default btn-form-action" href="javascript:void(0)" id="changePassButton"><i class="fa fa-key" aria-hidden="true"></i> Change password</a></p>' +
							'</div>'
						},
						this.myDataSummaryPanel.getComponent()
					]
				},
				this.myDataFileListView.getComponent(),
				this.myDataJobListView.getComponent()
			],
			listeners: {
				boxready: function () {
					$("#uploadNewFilesButton").click(function(){
						application.getMainView().changeMainView("DM_MyDataUploadFilesPanel");
					});
					$("#changePassButton").click(function () {
						me.changePassButtonClickHandler();
					});
				}
			}
		});
		return this.component;
	};
	return this;
}
DM_MyDataListView.prototype = new View();

function DM_MyDataSummaryPanel() {
	/***********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.usageChart = null;
	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	*
	* @param {type} dataSummary
	* @returns {undefined}
	*/
	this.updateContent = function(dataSummary) {
		if (dataSummary.usedSpace !== undefined && dataSummary.availableSpace !== undefined) {
			/* The original guard read `usedSpace !== undefined` twice, so a
			   payload carrying usedSpace without availableSpace got through and
			   divided by undefined. Both are needed to state a proportion. */
			var toMB = function(bytes) {
				return Math.round(bytes / Math.pow(1024, 2) * 100) / 100;
			};
			var used = toMB(dataSummary.usedSpace);
			var total = toMB(dataSummary.availableSpace);
			/* Clamped: a quota that has been exceeded should fill the bar, not
			   overflow its track and paint past the card. */
			var pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;

			$('#myDataUsedSpace').text(used);
			$('#myDataAvailableSpace').text(total + " MB");
			$('#myDataUsedSpaceBar')
				.css('width', pct + '%')
				.attr('class', 'po-storage-fill ' + this.getUsageClass(dataSummary.usedSpace, dataSummary.availableSpace));

			/* The bar is decoration to a screen reader; the figure above it is
			   the real readout, so the meter carries the numbers itself. */
			$('#myDataUsedSpaceBar').closest('.po-storage-track')
				.attr({role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': total,
				       'aria-valuenow': used, 'aria-label': 'Storage used'});
		}

		if (dataSummary.totalFiles !== undefined) {
			$('#myDataTotalFiles').text(dataSummary.totalFiles);
		}

		if (dataSummary.totalJobs !== undefined) {
			$('#myDataTotalJobs').text(dataSummary.totalJobs);
		}
		return this;
	};

	/* A class rather than a hex, so dark.css can restate the three fills and so
	   the thresholds stay stated in one place. Same 60/90% breakpoints the
	   colour function used. */
	this.getUsageClass = function(value, max) {
		var ratio = max > 0 ? value / max : 0;
		if (ratio > 0.9) {
			return 'is-critical';
		} else if (ratio > 0.6) {
			return 'is-warning';
		}
		return 'is-ok';
	};

	this.initComponent = function() {
		var me = this;
		this.component = Ext.widget({
			xtype: "box",
			cls: "contentbox omicSummaryBox poMyDataCard",
			minHeight: 230,
			/* Used space was a Highcharts donut of two slices - used and free -
			   with the figure floated in the middle of the hole, plus two
			   odometer counters wearing coloured icon tiles.

			   A donut for one proportion against a known maximum is the wrong
			   form: the reader has to compare two arc lengths to answer "how
			   full am I", which a bar answers by length alone. It also pulled a
			   charting library in to draw what is a div with a width. Files and
			   Jobs are bare counts and were never charts at all, so they are
			   stat tiles now: the number leads, the word labels it.

			   The fill is a status colour (success/warning/error from the design
			   system, replacing Highcharts' #55BF3B/#DDDF0D/#DF5353 - that amber
			   was 1.6:1 on white). Status colour never travels alone here: the
			   figure above the bar states the same thing in words, so the bar
			   is confirmation rather than the only signal. */
			/* h2, not h3. This card sits beside My account, whose heading is an
			   h2 and therefore a card header band with a rule under it; at h3 this
			   one had no band at all, so two cards on one row were titled two
			   different ways. */
			html: '<h2>Used space</h2>' +
			'<div class="po-storage">' +
			'  <div class="po-storage-meter">' +
			'    <p class="po-storage-figure">' +
			'      <span id="myDataUsedSpace" class="po-storage-used">0</span><span class="po-storage-unit"> MB</span>' +
			'      <span class="po-storage-of">of <span id="myDataAvailableSpace">200 MB</span> used</span>' +
			'    </p>' +
			'    <div class="po-storage-track"><div class="po-storage-fill" id="myDataUsedSpaceBar" style="width:0%"></div></div>' +
			'  </div>' +
			'  <div class="po-storage-stats">' +
			'    <div class="po-stat"><span class="po-stat-value" id="myDataTotalFiles">0</span><span class="po-stat-label">Files</span></div>' +
			'    <div class="po-stat"><span class="po-stat-value" id="myDataTotalJobs">0</span><span class="po-stat-label">Jobs</span></div>' +
			'  </div>' +
			'</div>' +
			'<p class="po-storage-note">Files you submit are kept here so you can reuse them in later analyses. Delete old ones to free space.</p>'
			/* No boxready hook any more. It built the Highcharts donut and the two
			   Odometer instances; the meter is plain markup that updateContent
			   already fills, so there is nothing left to construct on render.
			   Odometer is still used by PA_Step3Views and stays loaded. */
		});

		return this.component;
	};
	return this;
}
DM_MyDataSummaryPanel.prototype = new View();

function DM_MyDataFileListView() {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "DM_MyDataFileListView";
	this.hideSummary = false;
	this.allowRowRemoving = true;
	this.multidelete = false;

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.setHideSummary = function(hide) {
		this.hideSummary = (hide === true);
		return this;
	};

	this.setAllowRowRemoving = function(allow, multiRemoving) {
		this.allowRowRemoving = (allow === true);
		this.multidelete = (multiRemoving === true);
		return this;
	};

	this.loadData = function(fileList, dataSummary) {
		this.getComponent().setLoading(true);
		var grid = this.getComponent().queryById("myFilesGrid");
		grid.getStore().removeAll();
		grid.getStore().loadData(fileList);

		if (this.parent !== null && this.parent.updateSummary !== undefined) {
			dataSummary.totalFiles = fileList.length;
			this.parent.updateSummary(dataSummary);
		}

		this.getComponent().setLoading(false);
		return this;
	};

	this.updateContent = function() {
		this.getController().loadMyFilesDataHandler(this);
		return this;
	};

	this.initComponent = function() {
		var me = this;
		this.component = Ext.widget({
			xtype: "container",
			itemId: "DM_MyDataFileListView",
			items: [{
				xtype: 'container',
				cls: "contentbox",
				items: [{
					xtype: "box",
					flex: 1,
					html: '<h3>My files</h3>' +
					/* Three sentences saying one thing, with "Everytime" and
					   "Paintomics's" in them, and a bolded "Keep your available
					   space in mind!" shouting at the end about a quota the Used
					   space meter directly above already shows. Reduced to what
					   the reader does not already know: files arrive here by
					   themselves, and there is a batch route. */
					'<p>Files you submit through any PaintOmics form are stored here automatically, so you can reuse them in a later analysis without uploading them again. ' +
					'To add several at once, use <a id="myFilesUploadFilesLink" href="javascript:void(0)">Upload new files</a>.</p>'
				}, {
					xtype: "livesearchgrid",
					itemId: "myFilesGrid",
					columnWidth: 300,
					searchFor: "fileName",
					/* deferEmptyText false so the message shows on the first render
					   rather than only after a store load has been and gone. */
					viewConfig: {
						deferEmptyText: false,
						emptyText: 'No files yet. Files you submit with an analysis are kept here automatically.'
					},
					border: 0,
					multidelete: this.multidelete,
					store: Ext.create('Ext.data.Store', {
						fields: [
							{name: 'selected'},
							{name: 'fileName'},
							{name: 'dataType'},
							{name: 'omicType'},
							{name: 'size'},
							{name: 'submissionDate'},
							{name: 'description'}
						],
						sorters: [{
							property: 'submissionDate',
							direction: 'DESC'
						}]
						// sorters: [{
						// 	property: 'omicType',
						// 	direction: 'ASC'
						// }, {
						// 	property: 'dataType',
						// 	direction: 'ASC'
						// }]
					}),
					columns: [{
						text: 'File Name',
						dataIndex: 'fileName',
						flex: 2
					}, {
						text: 'Omic',
						dataIndex: 'omicType',
						width: 180
					}, {
						text: 'File type',
						dataIndex: 'dataType',
						width: 180
					}, {
						text: 'Description',
						dataIndex: 'description',
						flex: 3,
						renderer: function(value, metadata, record) {
							var tooltipContent = '';
							
							if (value === '') {
								tooltipContent = "<b style='display:block; width:200px'><i>No description for this file.</i></b>";
						  	} else {
								var textLines = value.split(';').filter(x => $.trim(x).length);
								
								tooltipContent = "<b style='display:block; width:200px'>" + metadata.column.text + "</b>" + "<br>" + textLines[0];
								
								if (textLines.length > 1) {
									var secondPart = textLines.indexOf("Params:");
									
									var inputData = textLines.slice(1, secondPart);
									var paramsData = textLines.slice(secondPart + 1);
									
									tooltipContent += '<br/>Input data: <ul><li>' + inputData.join('</li><li>') + '</li></ul>';
									tooltipContent += 'Params: <ul><li>' + paramsData.join('</li><li>') + '</li></ul>';
								}
							}
									 
							metadata.tdAttr = 'data-qtip="' + tooltipContent + '"';
							return value;
						}
					}, {
						text: 'Size',
						dataIndex: 'size',
						width: 80,
						renderer: function(value, meta) {
							return Math.round(value / 1024) + "Kb";
						}
					}, {
						text: 'Submission Date',
						dataIndex: 'submissionDate',
						width: 140,
						renderer: function(value) {
							return value.substr(6, 4) + "-" + value.substr(3, 2) + "-" + value.substr(0, 2) + " " + value.substr(11, 5);
						},
						doSort: function(state) {
							var ds = this.up('tablepanel').store;
							var field = this.getSortParam();
							var parent = this.up('#myFilesGrid');
							
							ds.sort({
								property: field,
								direction: state,
								sorterFn: function sorterFunction(o1, o2) {
									return parent.sortCustomDate(o1, o2, 'submissionDate');
								}
							});
						},
						listeners:{
							afterrender: function(col, eOpts){
								col.setSortState('DESC');
							}
						}
					}, {
						xtype: 'customactioncolumn',
						text: "File Options",
						width: 200,
						hidden: !me.allowRowRemoving,
						items: [{
							icon: "fa-download",
							text: "Download",
							tooltip: 'Download this file.',
							handler: function(grid, rowIndex, colIndex) {
								me.getController().downloadFilesHandler(me, grid.getStore().getAt(rowIndex).get("fileName"), "input");
							}
						}, {
							icon: "fa-eye",
							text: "View",
							tooltip: 'View this file.',
							handler: function(grid, rowIndex, colIndex) {
								me.getController().viewFilesHandler(me, grid.getStore().getAt(rowIndex).get("fileName"), "input");
							}
						}, {
							icon: "fa-trash-o",
							text: "Delete",
							style: "color: rgb(242, 105, 105);",
							tooltip: 'Delete this file.',
							handler: function(grid, rowIndex, colIndex) {
								me.getController().deleteFilesHandler(me, grid.getStore().getAt(rowIndex).get("fileName"));
							}
						}]
					}],
					listeners: {
						cellclick: function(grid, td, cellIndex, record, tr, rowIndex) {
							var visibleColumns = grid.panel.query('gridcolumn:not([hidden]):not([isGroupHeader])').length;
							if (cellIndex === visibleColumns - 1) {
								return false; //IGNORE LAST COLUMN (EXTERNAL LINKS)
							}
							record.set("selected", record.get("selected") === true);
						}
					},
					multiDeleteHandler: function() {
						var selected = this.getSelectionModel().getSelection().map(x => x.get("fileName"));

						if (selected.length > 0) {
							me.getController().deleteFilesHandler(me, selected.join(","));
						}
					}
				}]
			}],
			listeners: {
				boxready: function() {
					me.updateContent();

					$("#myFilesUploadFilesLink").click(function() {
						application.getMainView().changeMainView("DM_MyDataUploadFilesPanel");
					});
				}
			}
		});
		return this.component;
	};
	return this;
}
DM_MyDataFileListView.prototype = new View;

function DM_MyDataJobListView() {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "DM_MyDataJobListView";
	this.hideSummary = false;
	this.allowRowRemoving = true;
	this.multidelete = false;

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.setHideSummary = function(hide) {
		this.hideSummary = (hide === true);
		return this;
	};

	this.setAllowRowRemoving = function(allow, multiRemoving) {
		this.allowRowRemoving = (allow === true);
		this.multidelete = (multiRemoving === true);
		return this;
	};

	this.loadData = function(jobList, dataSummary) {
		this.getComponent().setLoading(true);
		var grid = this.getComponent().queryById("myJobsGrid");
		grid.getStore().removeAll();
		grid.getStore().loadData(jobList);

		if (this.parent !== null && this.parent.updateSummary !== undefined) {
			this.parent.updateSummary({
				totalJobs: jobList.length
			});
		}

		this.getComponent().setLoading(false);
		return this;
	};

	this.updateContent = function() {
		this.getController().loadMyJobsDataHandler(this);
		return this;
	};

	this.initComponent = function() {
		var me = this;

		this.component = Ext.widget({
			xtype: "container",
			itemId: "DM_MyDataJobListView",
			cls: "contentbox",
			items: [{
				xtype: "box",
				flex: 1,
				html: '<h3>My jobs</h3>' +
					/* Said "jobs" three times in two sentences, capitalised two of
					   them, and closed a <br> the wrong way round. */
					'<p>Every analysis you run appears here with its status. Reopen one to carry on where you left off, or download its results.</p>'
			}, {
				xtype: "livesearchgrid",
				itemId: "myJobsGrid",
				viewConfig: {
					deferEmptyText: false,
					emptyText: 'No jobs yet. Every analysis you run appears here with its status.'
				},
				columnWidth: 300,
				searchFor: "jobID",
				border: 0,
				multidelete: this.multidelete,
				store: Ext.create('Ext.data.Store', {
					fields: [
						{name: 'selected'},
						{name: 'jobID'}, {
							name: 'jobType'
						}, {
							name: 'lastStep'
						}, {
							name: 'date'
						}, {
							name: 'accessDate'
						},{
							name: 'name'
						},{
							name: 'description'
						}],
						sorters: [{
							property: 'date',
							direction: 'DESC'
						}]
					}),
					columns: [{
						text: 'Job ID',
						dataIndex: 'jobID',
						flex: .5
					}, {
						text: 'Type',
						dataIndex: 'jobType',
						flex: 1
					}, {
						text: 'Last step',
						dataIndex: 'lastStep',
						flex: .4
					}, {
						text: 'Submission date',
						dataIndex: 'date',
						flex: .6,
						renderer: function(value) {
							return value.substr(0, 4) + "-" + value.substr(4, 2) + "-" + value.substr(6, 2) + " " + value.substr(8, 2) + ":" + value.substr(10, 2);
						},
						doSort: function(state) {
							var ds = this.up('tablepanel').store;
							var field = this.getSortParam();
							var parent = this.up('#myJobsGrid');
							
							ds.sort({
								property: field,
								direction: state,
								sorterFn: function sorterFunction(o1, o2) {
									return parent.sortCustomDate(o1, o2, 'date');
								}
							});
						},
						listeners:{
							afterrender: function(col, eOpts){
								col.setSortState('DESC');
							}
						}
					}, {
						text: 'Expiration date',
						dataIndex: 'accessDate',
						flex: .6,
						renderer: function(value) {
							var date = new Date(value.substr(0, 4) + "-" + value.substr(4, 2) + "-" + value.substr(6, 2));
							date.setDate(date.getDate() + MAX_LIVE_JOB);
							return date.toISOString().substr(0, 10);

						},
						doSort: function(state) {
							var ds = this.up('tablepanel').store;
							var field = this.getSortParam();
							var parent = this.up('#myJobsGrid');
							
							ds.sort({
								property: field,
								direction: state,
								sorterFn: function sorterFunction(o1, o2) {
									return parent.sortCustomDate(o1, o2, 'accessDate');
								}
							});
						}
					}, {
						text: 'Job name',
						dataIndex: 'name',
						flex: 1
					}, {
						text: 'Description',
						dataIndex: 'description',
						flex: 2,
						renderer: function(value, metadata, record) {
							var tooltipContent = '';
							
							if (value === '') {
								tooltipContent = "<b style='display:block; width:200px'><i>No description for this job</i></b>";
						  	} else {
								var textLines = value.split(';').filter(x => $.trim(x).length && !x.match("Example Job"));

								if (textLines[0][textLines[0].length - 1] !== ':') {
									tooltipContent += "<b style='display:block; width:200px'>Description</b><br/><ul><li>" + textLines.filter(x => !x.match("Params")).join('</li><li>') + '</li></ul>';
								} else {
									tooltipContent += '<b>Description:</b> <ul>';

									// Other lines containing omics
									for(var i = 1; i < textLines.length; i++) {

										var omicText = textLines[i];
										var omicName = /^(.*?)\[/g.exec(omicText);
										var omicConfig = /\[\[(.*)\]\]/g.exec(omicText);
										var omicFile = /\s\[([^\[]*)?\]/g.exec(omicText);

										tooltipContent += '<li><b>' + (omicName ? omicName[1] : "No name") + '</b>';
										
										if (omicFile) {
											var omicFiles = omicFile[1].split('!!');
											
											tooltipContent += '<ul><li>File used: ' + omicFiles[0] + '</li>' + 
												(omicFiles[1] ? '<li>Relevant file used: ' + omicFiles[1] + '</li>' : '');
										}

										// Check if it contains config options, inside double squared brackets
										// and separated by double !
										if (omicConfig) {
											var configParams = omicConfig[1].split("!!").filter(x => !x.match("Params") && x.length);

											tooltipContent += '<li>Config options:<ul><li>';

											tooltipContent += configParams.join('</li><li>');

											tooltipContent += '</li></ul></li>';
										}

										tooltipContent += '</li></ul></li>';
									}

									tooltipContent += '</ul>';
								}
							}
									 
							metadata.tdAttr = 'data-qtip="' + tooltipContent + '"';
							return value;
						}
					}, {
						xtype: 'customactioncolumn',
						text: "Job Options",
						width: 150,
						items: [{
							icon: "fa-repeat",
							text: "Recover",
							tooltip: 'Recover this Job.',
							handler: function(grid, rowIndex, colIndex) {
								me.getController().recoverJobsHandler(me, grid.getStore().getAt(rowIndex).get("jobID"), grid.getStore().getAt(rowIndex).get("jobType"), grid.getStore().getAt(rowIndex).get("date"));
							}
						}, {
							icon: "fa-trash-o",
							text: "Delete",
							style: "color: rgb(242, 105, 105);",
							tooltip: 'Delete this file.',
							handler: function(grid, rowIndex, colIndex) {
								me.getController().deleteJobsHandler(me, grid.getStore().getAt(rowIndex).get("jobID"), grid.getStore().getAt(rowIndex).get("jobType"));
							}
						}]
					}],
					multiDeleteHandler: function() {
						var selectedRows = this.getSelectionModel().getSelection();
						var selectedIDs = selectedRows.map(x => x.get("jobID"));
						var selectedTypes = selectedRows.map(x => x.get("jobType"));

						if (selectedIDs.length > 0) {
							me.getController().deleteJobsHandler(me, selectedIDs.join(","), selectedTypes.join(","));
						}
					}
				}],
				listeners: {
					boxready: function() {
						me.updateContent();
					}
				}
			});
			return this.component;
		};
		return this;
	}
	DM_MyDataJobListView.prototype = new View;

	/**
	* This component is used for submit simple jobs, such as Bed2Genes jobs.
	* @param {type} aViewName
	* @param {type} controller
	* @param {type} _callback
	* @returns {DM_MyDataSubmitJobPanel}
	*/
	function DM_MyDataSubmitJobPanel(aViewName, controller, _callback) {
		/*********************************************************************
		* ATTRIBUTES
		***********************************************************************/
		this.name = "DM_MyDataSubmitJobPanel";
		this.aViewName = aViewName;
		this.templatesPath = "/app/view/DataManagementViews/myDataFormTemplates/";

		/***********************************************************************
		* OTHER FUNCTIONS
		***********************************************************************/
		this.initComponent = function() {
			var me = this;
			this.component = Ext.widget('box', {
				html: "<div class='generatingFormWaitDiv'>Generating form...</div>"
			});
			var _callback = function(newComponent) {
				var parent = me.getComponent().up();
				parent.remove(me.getComponent());
				me.component = newComponent;
				parent.add(me.getComponent());
			};
			//AUTOGENERATE THE FORMULARY
			generateForm(me.templatesPath, me.aViewName, controller, _callback);
			return this.component;
		};
		return this;
	}
	DM_MyDataSubmitJobPanel.prototype = new View;

	function DM_MyDataUploadFilesPanel() {
		/*********************************************************************
		* ATTRIBUTES
		***********************************************************************/
		this.name = "DM_MyDataUploadFilesPanel";

		/***********************************************************************
		* OTHER FUNCTIONS
		***********************************************************************/
		this.initComponent = function() {
			var me = this;
			this.component = Ext.widget({
				xtype: "container",
				maxWidth: 1300,
				padding: '10',
				items: [{
					xtype: 'box',
					cls: "contentbox",
					/* The <ol> was nested inside the <p>, which the HTML parser cannot
					   do - it closes the paragraph at the list and leaves a stray </p>
					   afterwards. Two blocks now, so the markup means what it says.

					   The prose also opened by telling the reader what the page they
					   are already on is for ("Upload new files easily to your cloud
					   space using this tool"), and closed the Data type example with a
					   double full stop. What survives is the part that is not on
					   screen: what the two fields they have to fill in actually are. */
					html: '<div id="about">' +
					' <h2>Upload files</h2>' +
					' <p>Files land in <b>My files and Jobs</b>, and count against your storage quota.</p>' +
					' <ol>' +
					'  <li>Pick the files with <b>Browse</b>.</li>' +
					'  <li>Set two fields on each one:' +
					'    <ul>' +
					'      <li><b>Data type</b> &mdash; what the file holds, such as a gene expression file, a relevant compound list, or a GTF file.</li>' +
					'      <li><b>Omic type</b> &mdash; the omic family it belongs to, such as transcriptomics or metabolomics.</li>' +
					'    </ul>' +
					'  </li>' +
					'  <li>Press <b>Upload</b>.</li>' +
					' </ol>' +
					'</div>'
				},
				Ext.create('Ext.upload.Panel', {
					cls: "contentbox",
					flex: 1,
					minHeight: 400,
					uploader: "Ext.upload.uploader.FormDataUploader",
					uploaderOptions: {
						url: SERVER_URL_DM_UPLOAD_FILE,
						timeout: UPLOAD_TIMEOUT * 1000 /*2 min*/
					}
				})
			]
		});

		return this.component;
	};
	return this;

}
DM_MyDataUploadFilesPanel.prototype = new View;

function DM_GTFFileListView() {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "DM_GTFFileListView";
	this.hideSummary = false;
	this.allowRowRemoving = true;

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/* Bytes as something a human can read. The MyData grid above prints
	   Math.round(value / 1024) + "Kb", which suits the files a user uploads and
	   falls apart on a reference library: the human GRCh38 annotation renders
	   as "2334265Kb". Inbuilt GTFs run from well under a megabyte (yeast) to
	   gigabytes (human, mouse), so the unit has to move with the value. */
	var formatFileSize = function(bytes) {
		var value = Number(bytes);
		if (!isFinite(value) || value <= 0) {
			return "";
		}
		var units = ["B", "KB", "MB", "GB", "TB"];
		var unit = 0;
		while (value >= 1024 && unit < units.length - 1) {
			value = value / 1024;
			unit++;
		}
		/* One decimal while the mantissa is small (2.4 GB), none once it is not
		   (566 MB): past 10 the extra digit is noise in a column that gets
		   scanned rather than measured. */
		return (unit > 0 && value < 10 ? value.toFixed(1) : String(Math.round(value))) + " " + units[unit];
	};

	/* Show the whole cell on hover. Every text column here truncates in a
	   window this narrow -- "Staphylococcus aureus subsp. aureus strain
	   MRSA252", "GenBank: BX571856.1" -- and a clipped cell with no tooltip is
	   unreadable with no way to read it. Same mechanism as the MyData grid
	   above, but html-encoded: a value carrying a double quote would otherwise
	   close the data-qtip attribute early and spill its tail into the markup. */
	var renderWithTooltip = function(value, metadata) {
		var text = (value === null || value === undefined) ? "" : String(value);
		if (text !== "") {
			metadata.tdAttr = 'data-qtip="' + Ext.String.htmlEncode(text) + '"';
		}
		return text;
	};

	this.loadData = function(fileList) {
		this.getComponent().setLoading(true);
		var grid = this.getComponent().queryById("GTFFilesGrid");
		grid.getStore().removeAll();
		var data = [],
		dataAux;
		for (var i in fileList) {
			dataAux = [];
			dataAux.push(fileList[i]["fileName"]);
			dataAux.push((fileList[i]["otherFields"] ? fileList[i]["otherFields"]["specie"] : ""));
			dataAux.push((fileList[i]["otherFields"] ? fileList[i]["otherFields"]["version"] : ""));
			dataAux.push((fileList[i]["otherFields"] ? fileList[i]["otherFields"]["source"] : ""));
			dataAux.push(fileList[i]["description"]);
			/* dm_get_gtffiles has always returned the size and this grid has
			   always dropped it on the floor. It is the one field a user wants
			   before picking a reference file: these are hundreds of megabytes
			   each, and the choice is often between two builds of one genome. */
			dataAux.push(fileList[i]["size"]);
			data.push(dataAux);
		}
		this.totalCount = data.length;
		this.totalBytes = data.reduce(function(sum, row) {
			return sum + (Number(row[5]) || 0);
		}, 0);
		grid.getStore().loadData(data);
		/* Reloading keeps whatever the user typed, so re-apply it rather than
		   showing an unfiltered list under a filled-in filter box. */
		var filterField = grid.down("#GTFFilesFilter");
		if (filterField && filterField.getValue()) {
			this.filterData(filterField.getValue());
		} else {
			this.updateSummary();
		}
		this.getComponent().setLoading(false);
	};

	/* Free-text filter across every column. Five rows needed no filter; the
	   library is one file per supported organism now, and finding "the maize
	   one" by eye means reading every row. Ext 4.2 semantics: filterBy()
	   filters from the store's snapshot and so replaces the previous
	   predicate, and clearFilter() restores it. */
	this.filterData = function(term) {
		var store = this.getComponent().queryById("GTFFilesGrid").getStore();
		var needle = $.trim(term || "").toLowerCase();
		if (needle === "") {
			store.clearFilter();
		} else {
			store.filterBy(function(record) {
				return ["fileName", "specie", "version", "source", "description"].some(function(field) {
					return String(record.get(field) || "").toLowerCase().indexOf(needle) !== -1;
				});
			});
		}
		this.updateSummary();
	};

	/* The line under the grid: how many rows are showing out of how many, and
	   what the library costs on disk. Without it a filtered grid looks exactly
	   like a server that only has three reference files. */
	this.updateSummary = function() {
		var grid = this.getComponent().queryById("GTFFilesGrid");
		var summary = grid.down("#GTFFilesSummary");
		if (!summary) {
			return;
		}
		var total = this.totalCount || 0;
		var shown = grid.getStore().getCount();
		var text = (shown === total)
			? total + (total === 1 ? " file" : " files")
			: shown + " of " + total + " files";
		if (this.totalBytes) {
			text += " &middot; " + formatFileSize(this.totalBytes) + " on disk";
		}
		summary.setText(text);
	};

	this.updateContent = function() {
		this.getController().loadGTFFilesHandler(this);
	};

	this.initComponent = function() {
		var me = this;
		this.component = Ext.widget({
			xtype: "container",
			itemId: "DM_GTFFileListView",
			items: [{
				xtype: "box",
				flex: 1,
				html: '<h3>Inbuilt GTF files</h3>'
			}, {
				xtype: "grid",
				itemId: "GTFFilesGrid",
				columnWidth: 300,
				store: Ext.create('Ext.data.ArrayStore', {
					/* size is typed so the column sorts by magnitude. As a
					   string "9 MB" sorts above "10 GB". */
					fields: ['fileName', 'specie', 'version', 'source', 'description',
						{name: 'size', type: 'int'}],
					data: [],
					/* Species first: the question a user brings to this window
					   is "is my organism here", not "what is on disk". */
					sorters: [{property: 'specie', direction: 'ASC'},
						{property: 'fileName', direction: 'ASC'}]
				}),
				viewConfig: {
					deferEmptyText: false,
					emptyText: '<div style="padding: 10px;"><i>No inbuilt GTF files are installed on this server.</i></div>'
				},
				tbar: [{
					xtype: 'textfield',
					itemId: 'GTFFilesFilter',
					emptyText: 'Filter by species, file name, assembly or source',
					flex: 1,
					listeners: {
						change: {
							fn: function(field, value) {
								me.filterData(value);
							},
							/* Buffered: the predicate touches every row of
							   every column, and this runs per keystroke. */
							buffer: 200
						}
					}
				}],
				bbar: [{
					xtype: 'tbtext',
					itemId: 'GTFFilesSummary',
					text: ''
				}],
				columns: [{
					text: 'File Name',
					dataIndex: 'fileName',
					flex: 2,
					renderer: renderWithTooltip
				}, {
					/* "Species" -- the old header read "Specie", which is
					   money in coin form. */
					text: 'Species',
					dataIndex: 'specie',
					flex: 1,
					renderer: renderWithTooltip
				}, {
					/* The field holds "GRCh38 (Ensembl 116)", not a version
					   number, and which assembly a GTF is built on is the
					   thing that has to match the user's coordinates. */
					text: 'Assembly / release',
					dataIndex: 'version',
					flex: 1,
					renderer: renderWithTooltip
				}, {
					text: 'Source',
					dataIndex: 'source',
					flex: 1,
					renderer: renderWithTooltip
				}, {
					text: 'Size',
					dataIndex: 'size',
					width: 90,
					align: 'right',
					renderer: formatFileSize
				}, {
					text: 'Description',
					dataIndex: 'description',
					flex: 3,
					renderer: renderWithTooltip
				}]
			}],
			listeners: {
				boxready: function() {
					me.updateContent();
				}
			}
		});
		return this.component;
	};
	return this;
}
DM_GTFFileListView.prototype = new View;

Ext.define('Paintomics.view.common.MyFilesSelectorButton', {
	extend: 'Ext.container.Container',
	alias: 'widget.myFilesSelectorButton',
	fieldLabel: "label",
	namePrefix: "filefield",
	buttonText: "Browse...",
	/* The drawn width of the in-field control, per label: measured once on
	   the first row that can be measured and reused by every other row. */
	statics: {
		measuredWidths: {}
	},
	/* "required" or "optional": whether the job needs this file. Every file row on
	   Step 1 sets it, so a row that says nothing is a row somebody forgot, not a
	   row with nothing to say. Shown as the asterisk in the label; conditional
	   rows flip it with setRequiredTag(). */
	requiredTag: null,
	labelAlign: "right",
	labelWidth: 200,
	margin: "5px 0px",
	value: null,
	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	getValue: function() {
		return this.queryById("visiblePathField").getRawValue();
	},
	setValue: function(value, origin) {
		origin = (origin || "mydata");
		this.queryById("visiblePathField").setRawValue((origin === "mydata" ? "[MyData]/" : "") + value);
		this.queryById("originField").setValue(origin);
	},
	clearValue: function(){
		this.queryById("fileField").reset();
		this.queryById("visiblePathField").setRawValue("");
		this.queryById("originField").setValue("");
	},
	/* Locks the in-field control: greyed out and out of the tab order. The
	   read-only path field itself stays enabled: a disabled input is dropped
	   from the form post, and its text should stay readable. Remembered so a
	   call made before the field is rendered (the example scenarios disable
	   their rows straight away) still lands once wireBrowse() runs. */
	setDisabled: function(disabled) {
		this.browseLocked = !!disabled;
		return this.applyBrowseState();
	},
	/* Two doors, one state. The widget's own lock (example rows, through
	   setDisabled) and the path field's own disabled state (a panel switching
	   the row off through the field or its container; read from the field, not
	   mirrored) each grey the control out; either is enough, and a container
	   enable cascade -- the miRNA correlation box, the Region-based
	   own-associations toggle -- cannot lift the widget's lock, which is how
	   example rows once came back to life. */
	applyBrowseState: function() {
		var field = this.queryById("visiblePathField");
		var off = !!(this.browseLocked || (field && field.disabled));
		var buttons = this.browseButtons || [];
		var i;
		for (i = 0; i < buttons.length; i++) {
			buttons[i].disabled = off;
		}
		return this;
	},
	openFilePicker: function() {
		this.queryById("fileField").fileInputEl.el.dom.click();
	},
	/* The panels mark a row that is missing its file through the widget; a
	   Container has no markInvalid of its own, so it lands on the path field. */
	markInvalid: function(errorMessage) {
		return this.queryById("visiblePathField").markInvalid(errorMessage);
	},
	/* The menu behind the caret: the three entries the split button used to
	   carry, plus whatever the panel adds through extraButtons. Built once and
	   torn down with the widget -- a menu renders to the document body and would
	   otherwise outlive the row it belongs to.

	   The widget is named as the menu's owner. The extraButtons handlers (the
	   Region-based panel's GTF picker, the miRNA panel's "other omic" picker)
	   climb `this.up("myFilesSelectorButton")` from the menu item, and
	   Menu.getRefOwner walks parentMenu -> ownerButton -> ownerCt; the split
	   button used to be that owner, and an ownerless menu dead-ends the walk. */
	buildOptionsMenu: function() {
		var me = this;
		var menu = new Ext.menu.Menu({
			items: [
				{
					text: 'Upload file from my PC',
					handler: function() {
						me.openFilePicker();
					}
				}, {
					text: 'Use a file from My Data',
					disabled: (Ext.util.Cookies.get("userID") === null),
					handler: function() {
						var _callback = function(selectedItem) {
							if (selectedItem !== null) {
								me.clearValue();
								me.setValue(selectedItem[0].get("fileName"));
							}
						};
						Ext.widget("myFilesSelectorDialog").showDialog(_callback);
					}
				}, {
					text: 'Clear selection',
					handler: function() {
						me.clearValue();
					}
				}
			].concat(me.extraButtons || [])
		});
		menu.ownerButton = me;
		return menu;
	},
	/* Attaches the in-field control once the path field is rendered. The text
	   opens the picker; the caret opens the menu under itself (Down arrow too,
	   as the split button did) and closes it again on a second press. */
	wireBrowse: function(field) {
		var me = this;
		var control = field.bodyEl.down(".po-browse");
		var text = field.bodyEl.down(".po-browse-text");
		var caret = field.bodyEl.down(".po-browse-caret");
		if (!control || !text || !caret) {
			return;
		}
		me.browseCaret = caret;
		me.browseButtons = [text.dom, caret.dom];
		text.dom.textContent = me.buttonText;
		caret.set({"aria-label": "More options for " + me.rowName()});
		text.on("click", function() {
			me.openFilePicker();
		});
		/* Ext.menu.Manager hides every open menu on a document mousedown, so
		   the click that follows a press on the caret would show its own menu
		   straight back. The press records whether the menu was open -- the
		   caret's listener runs before the document's -- and still propagates:
		   combos and tooltips close on that same document mousedown. */
		caret.on("mousedown", function() {
			me.menuWasOpen = me.optionsMenu.isVisible();
		});
		caret.on("click", function(e) {
			/* A keyboard activation is a click with no mousedown before it
			   (detail 0); a press released off the caret leaves the flag stale,
			   so the keyboard does not read it. */
			me.toggleOptionsMenu(!!(e.browserEvent && e.browserEvent.detail === 0));
		});
		caret.on("keydown", function(e) {
			if (e.getKey() === e.DOWN) {
				e.stopEvent();
				me.showOptionsMenu();
			}
		});
		/* The split button gave focus back to itself when its menu hid. */
		me.optionsMenu.on({
			show: function() {
				caret.set({"aria-expanded": "true"});
			},
			hide: function() {
				caret.set({"aria-expanded": "false"});
				if (!caret.dom.disabled) {
					caret.focus();
				}
			}
		});
		/* The stylesheet's padding-right is a guess for the first paint; the
		   control's real width depends on the text and the font, so measure it
		   and let the input end exactly where it begins. A row rendered inside
		   a hidden container (the Region-based panel's own-associations rows)
		   measures 0 here, so a zero is never written, and the field's resize
		   -- which fires when that container is shown and laid out -- measures
		   again; an unchanged figure is not written back. */
		var widths = me.self.measuredWidths;
		var measure = function() {
			/* Every row on the page shares one label and one font, so one drawn
			   control answers for all of them: the first figure is kept and the
			   other rows read it instead of forcing a layout each. */
			var width = widths[me.buttonText] || control.getWidth();
			var padding = (width + 2) + "px";
			if (width <= 0) {
				return false;
			}
			widths[me.buttonText] = width;
			if (field.inputEl.getStyle("padding-right") !== padding) {
				field.inputEl.setStyle("padding-right", padding);
			}
			field.un("resize", measure);
			return true;
		};
		if (!measure()) {
			field.on("resize", measure);
		}
		/* The web font can land after the first paint and change the width. */
		if (document.fonts && document.fonts.ready) {
			document.fonts.ready.then(function() {
				if (!me.isDestroyed && field.rendered) {
					delete widths[me.buttonText];
					measure();
				}
			});
		}
		me.setRequiredTag(me.requiredTag);
		me.applyBrowseState();
	},
	/* The field label as words, for the caret's name: four rows on one card
	   would otherwise all announce as "More options". Six shipped labels carry
	   a <br>, so the markup comes out. */
	rowName: function() {
		return String(this.fieldLabel).replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").replace(/^ | $/g, "");
	},
	showOptionsMenu: function() {
		this.optionsMenu.showBy(this.browseCaret, "tr-br?");
	},
	toggleOptionsMenu: function(keyboard) {
		var menu = this.optionsMenu;
		var wasOpen = !keyboard && this.menuWasOpen;
		this.menuWasOpen = false;
		if (wasOpen || menu.isVisible()) {
			menu.hide();
		} else {
			this.showOptionsMenu();
		}
	},
	beforeDestroy: function() {
		Ext.destroy(this.optionsMenu);
		this.callParent(arguments);
	},
	/* Shows or hides the requiredness mark in the label. The mark is always
	   rendered (afterLabelTextTpl on the path field) and this only toggles it,
	   so the rows whose requiredness is a consequence of a choice made elsewhere
	   on the card -- the correlation checkbox on a miRNA panel, say -- can flip
	   after render. The caller passes the same expression the validator reads,
	   so the two cannot drift. Remembered when called before render; wireBrowse()
	   applies it. */
	setRequiredTag: function(tag) {
		var field = this.queryById("visiblePathField");
		var mark = (field && field.rendered && field.labelEl) ? field.labelEl.down(".po-required") : null;
		this.requiredTag = tag;
		if (mark) {
			/* setDisplayed, not setVisible: the latter keeps the box (visibility:
			   hidden) and an optional row would show a gap before its colon. */
			mark.setDisplayed(tag === "required");
		}
		return this;
	},
	/***********************************************************************
	* COMPONENT DECLARATION
	***********************************************************************/
	initComponent: function() {
		var me = this;

		me.optionsMenu = me.buildOptionsMenu();
		me.items = [{
			xtype: "container",
			layout: {
				type: "hbox",
				align: "middle"
			},
			items: [{
				xtype: "textfield",
				itemId: "originField",
				name: me.namePrefix + "_origin",
				value: (this.value ? "mydata" : ""),
				hidden: true
			}, {
				xtype: "textfield",
				flex: 1,
				name: me.namePrefix + "_filelocation",
				itemId: "visiblePathField",
				value: (this.value ? this.value : ""),
				labelAlign: me.labelAlign,
				labelWidth: me.labelWidth,
				readOnly: true,
				fieldLabel: me.fieldLabel,
				/* The requiredness mark, after the words and before the colon, the way
				   every form reader expects: a red asterisk for a row the job needs,
				   nothing for an optional one. It used to be the word "required" or
				   "optional" after the Browse button. Rendered by the label template
				   rather than written into fieldLabel, so the label text the refusal
				   dialog quotes stays the plain words; the mark carries the word for
				   readers that cannot see it. setRequiredTag() shows or hides it. ExtJS puts
				   this template after the separator, so the separator is blank and the
				   colon is written here, after the mark: "Data file *:". */
				labelSeparator: "",
				afterLabelTextTpl: '<span class="po-required">' +
					'<span class="po-required-mark" role="img" aria-label="required">*</span>' +
					'</span>:',
				cls: "po-file-path",
				/* The Browse control is part of the field. "Browse..." at the field's
				   right edge opens the file picker; the caret beside it opens the menu
				   that used to hang off a split button standing next to the field as a
				   second bordered box. The field's own template renders it, so it sits
				   inside the one border the row has, and wireBrowse() attaches the
				   handlers once the field is on the page.

				   Native buttons on purpose: Enter and Space, the disabled attribute
				   (out of the tab order, click-inert) and non-navigation come with the
				   element, and the dark theme's blanket anchor colour never applies.
				   The text is written in wireBrowse(), not here: afterSubTpl is an
				   XTemplate, and a brace in buttonText would read as a placeholder. */
				afterSubTpl: '<span class="po-browse">' +
					'<button type="button" class="po-browse-text"></button>' +
					'<button type="button" class="po-browse-caret" aria-haspopup="true" aria-expanded="false"></button>' +
					'</span>',
				listeners: {
					afterrender: function(field) {
						me.wireBrowse(field);
					},
					/* Panels disable a row through the field or its container
					   (`down('container').setDisabled(...)`), and the container
					   cascade reaches form fields and buttons only. The control
					   follows the field's own events and reads the field's own disabled
					   state, so the enable that follows cannot lift a lock set through
					   setDisabled. */
					disable: function() {
						me.applyBrowseState();
					},
					enable: function() {
						me.applyBrowseState();
					}
				},
				style: {
					"margin-right": "3px"
				}
			}, (me.helpTip !== undefined ? {
				xtype: "label",
				html: '<span class="helpTip" style="float:right;" title="' + this.helpTip + '""></span>'
			} : null)]
		}, {
			xtype: 'filefield',
			itemId: "fileField",
			name: me.namePrefix + "_file",
			buttonText: '',
			hidden: true,
			listeners: {
				change: function(item, value) {
					me.setValue(value, "client");
				}
			}
		}];
		me.callParent(arguments);
	}
});

Ext.define('Paintomics.view.common.MyFilesSelectorDialog', {
	extend: 'Ext.window.Window',
	alias: 'widget.myFilesSelectorDialog',
	autoScroll: true,
	selectedItem: null,
	_callback: null,
	buttons: [{
		text: 'Accept',
		itemId: "acceptButton",
		handler: function() {
			this.up("window").selectedItem = this.up("window").queryById("myFilesGrid").getSelectionModel().getSelection();
			this.up("window").close();
		}
	}, {
		text: 'Cancel',
		handler: function() {
			this.up("window").selectedItem = null;
			this.up("window").close();
		}
	}],
	showDialog: function(_callback) {
		this._callback = _callback;
		this.setHeight(Ext.getBody().getViewSize().height * 0.9);
		this.setWidth(Ext.getBody().getViewSize().width * 0.8);
		this.center();
		this.show();
	},
	initComponent: function() {
		var me = this;
		var myDataFileListView = new DM_MyDataFileListView().setHideSummary(true).setAllowRowRemoving(false);
		myDataFileListView.setController(application.getController("DataManagementController"));
		me.items = [myDataFileListView.getComponent()];
		me.callParent(arguments);

		me.listeners = {
			boxready: function() {
				var me = this;
				this.queryById("myFilesGrid").on({
					itemdblclick: function() {
						me.queryById("acceptButton").el.dom.click();
					}
				});
			},
			close: function() {
				if (this._callback !== null) {
					this._callback(this.selectedItem);
				}
			}
		};
	}
});

Ext.define('Paintomics.view.common.GTFSelectorDialog', {
	extend: 'Ext.window.Window',
	alias: 'widget.GTFSelectorDialog',
	selectedItem: null,
	_callback: null,
	buttons: [{
		text: 'Accept',
		itemId: "acceptButton",
		handler: function() {
			this.up("window").selectedItem = this.up("window").queryById("GTFFilesGrid").getSelectionModel().getSelection();
			this.up("window").close();
		}
	}, {
		text: 'Cancel',
		handler: function() {
			this.up("window").selectedItem = null;
			this.up("window").close();
		}
	}],
	showDialog: function(_callback) {
		this._callback = _callback;
		this.setHeight(Ext.getBody().getViewSize().height * 0.9);
		this.setWidth(Ext.getBody().getViewSize().width * 0.8);
		this.center();
		this.show();
	},
	initComponent: function() {
		var me = this;
		var myDataFileListView = new DM_GTFFileListView();
		myDataFileListView.setController(application.getController("DataManagementController"));
		me.items = [myDataFileListView.getComponent()];
		me.callParent(arguments);

		me.listeners = {
			boxready: function() {
				var me = this;
				this.queryById("GTFFilesGrid").on({
					itemdblclick: function() {
						me.queryById("acceptButton").el.dom.click();
					}
				});
			},
			close: function() {
				if (this._callback !== null) {
					this._callback(this.selectedItem);
				}
			}
		};
	}
});

Ext.define('Paintomics.view.common.OmicInputSelectorDialog', {
	extend: 'Ext.window.Window',
	alias: 'widget.OmicInputSelectorDialog',
	selectedItem: null,
	_callback: null,
	buttons: [{
		text: 'Accept',
		itemId: "acceptButton",
		handler: function() {
			this.up("window").selectedItem = this.up("window").queryById("OmicInputFilesGrid").getSelectionModel().getSelection();
			this.up("window").close();
		}
	}, {
		text: 'Cancel',
		handler: function() {
			this.up("window").selectedItem = null;
			this.up("window").close();
		}
	}],
	showDialog: function(_callback) {
		this._callback = _callback;
		this.setHeight(Ext.getBody().getViewSize().height * 0.9);
		this.setWidth(Ext.getBody().getViewSize().width * 0.8);
		this.center();
		this.show();
	},
	initComponent: function() {
		var me = this;

		var fileSelectors = Ext.ComponentQuery.query('[xtype=myFilesSelectorButton]').map(function(selectorItem) {
			var selectorOmicName = selectorItem.up("container").items.get("omicNameField");

			if (selectorOmicName) {
				var selectorOmicFile = selectorItem.down("container").items.get("visiblePathField");

				if (selectorOmicFile && selectorOmicFile.getRawValue().length) {
					// Order return: [omic, file, name]
					return [
						selectorOmicName.getValue(),
						selectorOmicFile.getRawValue(),
						selectorOmicFile.getName()
					];
				}
			}

			return(null);
		}).filter(x => x !== null);

		var DM_InputList = Ext.widget({
			xtype: "container",
			itemId: "DM_OmicInputFileListView",
			items: [{
				xtype: "box",
				flex: 1,
				html: '<h3>Other form elements</h3>'
			}, {
				xtype: "grid",
				itemId: "OmicInputFilesGrid",
				columnWidth: 300,
				viewConfig: {
					deferEmptyText: false,
					emptyText: 'No data available. You must select a file in other omic in order to use this feature.',
				},
				store: Ext.create('Ext.data.ArrayStore', {
					fields: ['omic', 'file', 'name'],
					data: fileSelectors.length ? fileSelectors : null //{"items": fileSelectors} : null
				}),
				columns: [{
					text: 'Omic name',
					dataIndex: 'omic',
					flex: 1
				}, {
					text: 'Filename',
					dataIndex: 'file',
					flex: 1
				}]
			}]/*,
			listeners: {
				boxready: function() {
					me.updateContent();
				}
			}*/
		});

		//DM_InputList.setController(application.getController("DataManagementController"));

		me.items = [DM_InputList];
		me.callParent(arguments);

		me.listeners = {
			boxready: function() {
				var me = this;
				this.queryById("OmicInputFilesGrid").on({
					itemdblclick: function() {
						me.queryById("acceptButton").el.dom.click();
					}
				});
			},
			close: function() {
				if (this._callback !== null) {
					this._callback(this.selectedItem);
				}
			}
		};
	}
});

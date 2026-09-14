//# sourceURL=PA_Step4Views.js
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
* - PA_Step4JobView
* - PA_Step4PathwayView
* - PA_Step4KeggDiagramView
* - PA_Step4KeggDiagramFeatureSetView
* - PA_Step4KeggDiagramFeatureSetTooltip
* - PA_Step4KeggDiagramFeatureView
* - PA_Step4KeggDiagramFeatureSetSVGBox
* - PA_Step4VisualOptionsView
* - PA_Step4FindFeaturesView
* - PA_Step4GlobalHeatmapView
* - PA_Step4DetailsView
* - PA_Step4DetailsFeatureSetView
* - PA_Step4DetailsOmicValueView
*
*/

function PA_Step4JobView() {
	/**
	* About this view: This view (PA_Step4JobView) shows the content for a Job in STEP 4 (Pathway Exploration)
	* The view contains multiple PA_Step4PathwayView, which are added when the user explores the pathways.
	* Those views are stored into a cache memory (max MAX_PATHWAYS_OPENED) so users can switch quickly between pathways.
	* Finally, the variable currentView indicates which is the currently opened pathway.
	* The view shows different information for the Job instance, in particular:
	*  - A secodary toolbar showing different options for the pathways
	*  - A panel (PA_Step4PathwayView) containing 3 subpanes that represents the current pathway
	*     · The interactive KEGG diagram (PA_Step4KeggDiagramView)
	*     · The secondary panel containing heatmaps or pathway details (PA_Step4GlobalHeatmapView or PA_Step4DetailsView)
	*     · The auxiliary panel containing tools for searching or customizing the view (PA_Step4FindFeaturesView or PA_Step4VisualOptionsView)
	**/
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4JobView";
	this.items = [];
	this.pathwayViews = []; //QUEUE MAX 5 LAST PATHWAYS [MAX_PATHWAYS_OPENED]
	this.currentView = null;
	this.speciesInfo = null;

	/*********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	
	/**
	* This function retrieves species data from the server and saves the info in the controller to avoid
	* asking the same more than once per session.
	*/
	this.downloadSpeciesInfo = function(callback) {
		var me = this;
		
		if (this.speciesInfo == null) {
			$.getJSON(SERVER_URL_GET_AVAILABLE_SPECIES, function(data) {
				me.speciesInfo = data;
				callback(data);
			});
			
		} else {
			callback(this.speciesInfo);
		}
	}

	/**
	* This function handles the event fired when the user clicks on the "view" button
	* for a pathway thumb panel. If the pathway was no already opened, a new panel is
	* created and added to the tab panel
	* @param    {String}    pathwayID
	* @returns  {PA_Step4PathwayView} the pathway view
	*/
	this.showPathwayView = function(pathwayID) {
		var pathwaysPanelsContainer = this.getComponent().queryById("pathwaysPanelsContainer");
		if (this.currentView !== null) {
			pathwaysPanelsContainer.remove(this.currentView.getComponent(), false);  //remove from panel but do not destroy the component.
			this.currentView.hideTooltips();
		}
		this.currentView = (this.getPathwayView(pathwayID) || this.addPathwayView(pathwayID));
		pathwaysPanelsContainer.add(this.currentView.getComponent());
		return this.currentView;
	};

	/**
	* This function finds a pathway by a given pathwayID at the cache of pathways views.
	* @param    {String} pathwayID
	* @returns  {PA_Step4PathwayView} the pathway view
	**/
	this.getPathwayView = function(pathwayID) {
		for (var i in this.pathwayViews) {
			if (this.pathwayViews[i].getModel().getID() === pathwayID) {
				return this.pathwayViews[i];
			}
		}
		return null;
	};

	/**
	* This function creates and add a new pathwayView by a given pathwayID.
	* @param    {String} pathwayID
	* @returns  {PA_Step4PathwayView} the new pathway view
	**/
	this.addPathwayView = function(pathwayID) {
		var me = this;

		/********************************************************/
		/* STEP 1: Shows the pathway instance                   */
		/********************************************************/
		var pathwayModel = this.getModel().getPathway(pathwayID);
		var pathwayView = new PA_Step4PathwayView();
		pathwayView.setParent(this);
		pathwayView.setController(application.getController("PathwayController"));
		pathwayView.loadModel(pathwayModel);

		/********************************************************/
		/* STEP 2: Update the cache for visited pathways        */
		/********************************************************/
		this.pathwayViews.push(pathwayView);
		if (this.pathwayViews.length > MAX_PATHWAYS_OPENED) {
			console.info("Removing a pathway");
			var previous_view = this.pathwayViews.shift();
			previous_view.getComponent().destroy();
		}

		/********************************************************/
		/* STEP 3: Update the History panel content             */
		/********************************************************/
		$("#pathwayHistoryContainer > div").prepend(this.createThumbnail(pathwayID, pathwayView.getModel().getName(), pathwayView.getModel().getSource()))
		.children("#" + pathwayID.replace(' ', '__') + '_thumb').click(function() {
			$(this).prependTo($("#pathwayHistoryContainer > div"));
			me.showPathwayView($(this).attr("id").replace("_thumb", "").replace('__', ' '));
			me.toogleHistoryPanel(true);
		});

		if ($("#pathwayHistoryContainer .step4ThumbContainer").length > 8) {
			$('#pathwayHistoryContainer .step4ThumbContainer:gt(7)').remove();
		}

		return pathwayView;
	};

	/**
	* This function returns the HTML code for the thumbnail for a given pathway.
	* This is necessary to create the Pathway thumbnails at the History panel.
	* @param    {String} pathwayID
	* @param    {String} pathwayName
	* @returns  {String} HTML code for the thumbnail
	**/
	this.createThumbnail = function(pathwayID, pathwayName, pathwaySource) {
		thumbnail_suffix = (pathwaySource == 'undefined' || pathwaySource == 'KEGG') ? '_thumb' : '_' + pathwaySource + '_thumb'

		return '<div class="step4ThumbContainer" id="' + pathwayID.replace(' ', '__') + '_thumb">' +
		'    <div class="step4PathwayThumbnailHover">Open</div>' +
		'    <div class="step4ThumbTitleContainer">' + pathwayName + '</div>' +
		'    <div class="step4ThumbWrapper" style="background-image: url(\'' + location.pathname + "kegg_data/" + pathwayID + thumbnail_suffix + '\')"></div>' +
		'   </div>';
	};

	/**
	* This function shows//hide the History panel (last visited pathways)
	* @chainable
	* @param  {boolean} forceHide force or not the visibility of the panel
	* @return {PA_Step4JobView}
	*/
	this.toogleHistoryPanel = function(forceHide) {
		var panel = $("#pathwayHistoryContainer");

		// Visibility used to be read back off an animating `left`, and set by
		// sliding the panel to -405px. Both were wrong: the read was an
		// intermediate pixel value for the 250ms the transition ran, and -405px
		// only clears the window if the panel is anchored at its left edge -
		// this one is anchored ~680px in, so "closed" left it fully on screen
		// over the pathway title. The class is the whole state; the stylesheet
		// decides what visible and hidden look like.
		var shouldShow = !forceHide && !panel.hasClass("step4HistoryBoxOpen");

		panel.toggleClass("step4HistoryBoxOpen", shouldShow);
		return this;
	};

	/**
	* This function controls the event when clicking the Back button.
	* @chainable
	* @return {PA_Step4JobView}
	*/
	this.backButtonHandler = function() {
		//HIDE THE HISTORY PANEL
		this.toogleHistoryPanel(true);
		
		if (this.currentView) {
			this.currentView.hideTooltips();
		}
		
		this.controller.showJobInstance(this.getModel(), {doUpdate: false, callback: function() {
			initializeTooltips(".helpTip");
			$('#pathwayEnrichmentSection').get(0).scrollIntoView();
		}});
		return this;
	};

	/**
	* This function generates the component (EXTJS) using the content of the
	* JobInstance model
	* @returns {Ext.ComponentView} The visual component
	*/
	this.initComponent = function() {
		var me = this;

		this.component = Ext.widget({
			xtype: "container",
			id: "pathwaysPanelsWrapper",
			flex:1,
			layout: {type: 'vbox',pack: 'start', align: 'stretch'},
			items: [{ //THE SECONDARY TOOLBAR
				xtype: "container", cls: "toolbar secondTopToolbar",
				items: [{
					xtype: "box", html:
					'<a href="javascript:void(0)" class="button btn-danger helpTip" id="visualSettingsButton"><i class="fa fa-wrench"></i> Settings</a>' +
					'<a href="javascript:void(0)" class="button btn-info helpTip" id="searchButton"><i class="fa fa-search"></i> Search</a>' +
					'<a href="javascript:void(0)" class="button btn-secondary helpTip" id="globalHeatmapButton"><i class="fa fa-th"></i> Show Heatmap</a>' +
					'<a href="javascript:void(0)" class="button btn-primary helpTip" id="showPathwayButton"><i class="fa fa-sitemap"></i>  Show Pathway</a></div>' +
					'<a href="javascript:void(0)" class="button btn-default backButton"><i class="fa fa-arrow-left"></i> Go back</a>' +
					'<a href="javascript:void(0)" class="button helpTip" style=" float: left; background-color: #CD435D; color: #fff;" id="showHistoryButton"><i class="fa fa-history"></i> History</a>' +
					// The panel covers the pathway it slides over and had no way out of
					// its own: closing it meant knowing to press the History button in
					// the toolbar behind it a second time.
					'<div id="pathwayHistoryContainer" class="step4HistoryBox"><h2>History<a href="javascript:void(0)" id="hideHistoryButton" class="step4HistoryClose" title="Close history"><i class="fa fa-times"></i></a></h2><div></div></div>'
				}]
			}, { //THE CONTAINER FOR THE PATHWAY VIEWS
				xtype: "container", flex:1,
				style: "padding: 5px 10px;",
				itemId: "pathwaysPanelsContainer",
				layout: 'fit',
				items: []
			}],
			listeners: {
				boxready: function() {
					/* Step 3 leaves its contents sidebar behind in the centre panel, and
					   none of the sections it lists exist here - every entry on the final
					   result page was a link that went nowhere. Rebuilding against this
					   view finds fewer than three sections and clears it, which also drops
					   the sidebar's reserved column so the pathway gets the full width. */
					buildAnalysisTOC('#mainViewCenterPanel');

					//SOME EVENT HANDLERS DECLARATION
					$(".backButton").click(function() {
						me.backButtonHandler();
					});

					$("#showPathwayButton").click(function() {
						me.currentView.showDiagramPanel();
					});

					$("#globalHeatmapButton").click(function() {
						me.currentView.showGlobalHeatmap();
					});

					$("#searchButton").click(function() {
						me.currentView.showFindFeaturesPanel();
					});

					$("#visualSettingsButton").click(function() {
						me.currentView.showVisualOptionsPanel();
					});

					$("#showHistoryButton").click(function(event) {
						event.stopPropagation();
						me.toogleHistoryPanel();
					});

					$("#hideHistoryButton").click(function(event) {
						event.stopPropagation();
						me.toogleHistoryPanel(true);
					});

					// Anything that dismisses an overlay elsewhere in the app should
					// dismiss this one: clicking away from it, or Escape. Without
					// these the panel stays over the diagram until it is toggled off
					// from the toolbar it is covering.
					$(document).on("click.paHistory", function(event) {
						if (!$(event.target).closest("#pathwayHistoryContainer").length) {
							me.toogleHistoryPanel(true);
						}
					});

					$(document).on("keydown.paHistory", function(event) {
						if (event.key === "Escape") {
							me.toogleHistoryPanel(true);
						}
					});

					initializeTooltips(".helpTip");
				},
				beforedestroy: function() {
					// The dismiss handlers are on `document`, which outlives this view.
					// Namespaced so this removes exactly the two bound above.
					$(document).off(".paHistory");

					//DESTROY ALL PA_Step4PathwayView AND SUBCOMPONENTS
					for (var i in this.pathwayViews) {
						this.pathwayViews[i].getComponent().destroy();
						delete this.pathwayViews[i];
					}
					me.getModel().deleteObserver(me);
				}
			}
		});

		return this.component;
	};

	return this;
}
PA_Step4JobView.prototype = new View();


function PA_Step4PathwayView() {
	/**
	* About this view: this view (PA_Step4PathwayView) represents a Pathway instance.
	* For each view, we store the omic data values, the list of features based on genes,
	* the list of features based on compounds and the information about data distribution (min, max, q10,...)
	* Other variables are:
	*  - visualOptions: contains the visual options defined by the user for current view.
	*  - searchFeatureIndex: this dict contains the index of features in the pathway for a quick search
	* Variables for visual subcomponents:
	*  - diagramPanel: this panel contains the KEGG diagram (PA_Step4KeggDiagramView)
	*  - globalHeatmapView: this panel contains the Heatmaps diagrams (PA_Step4GlobalHeatmapView)
	*  - featureSetDetailsPanel: this panel contains the detailed views for feature sets (PA_Step4DetailsView)
	*  - findFeaturesPanel: this panel contains the tools for search features (PA_Step4VisualOptionsView)
	*  - visualOptionsPanel: this panel contains the tools for search features (PA_Step4VisualOptionsView)
	**/
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4PathwayView";

	this.visualOptions = null;
	this.dataDistributionSummaries = null;

	this.searchFeatureIndex = null;

	//Variables for visual subcomponents:
	this.diagramPanel = null;
	this.globalHeatmapView = null;
	this.featureSetDetailsPanel = null;
	this.findFeaturesPanel = null;
	this.visualOptionsPanel = null;
	this.walkPanel = null;

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	/**
	* Load the pathway information
	*  STEP 1: SET THE MODEL
	*  STEP 2. LOAD VISUAL OPTIONS IF ANY
	*  STEP 3 CREATE THE SUBVIEWS
	* @chainable
	* @param {Pathway} model
	* @returns {PA_Step4PathwayView}
	*/
	this.loadModel = function(model) {
		/********************************************************/
		/* STEP 1: SET THE MODEL		                        */
		/********************************************************/
		if (this.model != null) {
			this.model.deleteObserver(this);
		}
		this.model = model;
		this.model.addObserver(this);
		/********************************************************/
		/* STEP 2. LOAD VISUAL OPTIONS IF ANY                   */
		/********************************************************/
		if (window.sessionStorage && sessionStorage.getItem("visualOptions") !== null) {
			this.visualOptions = JSON.parse(sessionStorage.getItem("visualOptions"));
		}else{
			this.visualOptions = {};
		}

		var update=false;
		var me = this;
		if(!this.visualOptions.colorReferences || typeof(this.visualOptions.colorReferences) === "string"){
			/* Initialize new set of color references using the default colour */
			this.visualOptions.colorReferences = {};
			var defaultColorReference = this.model.getGraphicalOptions().getColorReferences();

			$.each(me.getGeneBasedInputOmics().concat(me.getCompoundBasedInputOmics()), function(index, value) {
				me.visualOptions.colorReferences[value.omicName] = defaultColorReference;
				update=true;
			});
		}
		if(this.visualOptions.customValues) {
			/* If there are custom values set, update the data distribution summaries */
			$.each(me.visualOptions.customValues, function(omicName, omicCustomValues) {
					var omicDataDistribution = me.getDataDistributionSummaries(omicName);

					omicDataDistribution.splice(11, 2, ...omicCustomValues);

					/* TODO: remove this as the reference is the same? */
					me.setDataDistributionSummaries(omicDataDistribution, omicName);
			});
		}
		if(!this.visualOptions.visibleOmics){
			this.visualOptions.visibleOmics = this.model.getGraphicalOptions().getVisibleOmics();
			update=true;
		}
		if(!this.visualOptions.colorScale){
			this.visualOptions.colorScale = this.model.getGraphicalOptions().getColorScale();
			update=true;
		}
		if(update){
			this.getParent().getController().updateStoredVisualOptions(this.getParent().getModel().getJobID(), this.visualOptions);
		}
		/************************************************************/
		/* STEP 3 CREATE THE SUBVIEWS                               */
		/************************************************************/
		this.showDiagramPanel();
		this.showFindFeaturesPanel();
		/* The Walk column is added here, before the first layout, so the
		   diagram measures its width with the column already beside it. Only
		   for a job whose owner allowed AI interpretation: the column's one
		   action sends the job's values to the model gateway. */
		if (this.getParent().getModel().aiConsent) {
			this.showWalkPanel();
		}

		return this;
	};

	this.getDataDistributionSummaries = function(propertyName) {
		if (this.dataDistributionSummaries === null) {
			this.dataDistributionSummaries = this.getParent().getModel().getDataDistributionSummaries();
		}

		if (this.dataDistributionSummaries !== null && propertyName !== undefined) {
			return this.dataDistributionSummaries[propertyName];
		}

		return this.dataDistributionSummaries;
	};
	
	this.getMatchedFeatures = function() {
		var foundFeatures = this.getModel().getMatchedGenes().concat(this.getModel().getMatchedCompounds());
		var omicsValues = this.getOmicsValues();
		
		var matchedFeatures = {};
		this.getGeneBasedInputOmics().concat(this.getCompoundBasedInputOmics()).map(x => matchedFeatures[x.omicName] = []);
		
		foundFeatures.forEach(function(featureName) {
			var keggName = omicsValues[featureName].getName();
			
			omicsValues[featureName].getOmicsValues().forEach(function(omicValue) {
				
				matchedFeatures[omicValue.omicName][keggName] = matchedFeatures[omicValue.omicName][keggName] || {isRelevant: false, isRelevantAssociation: false, inputNames: []};
				
				matchedFeatures[omicValue.omicName][keggName] = {
					isRelevant: matchedFeatures[omicValue.omicName][keggName].isRelevant || omicValue.isRelevant(),
					isRelevantAssociation: matchedFeatures[omicValue.omicName][keggName].isRelevantAssociation || omicValue.isRelevantAssociation(),
					inputNames: matchedFeatures[omicValue.omicName][keggName].inputNames.concat(omicValue.originalName || omicValue.getInputName())
				};
			})
		});
		
		return matchedFeatures;
	};

	this.setDataDistributionSummaries = (function(dataDistributionSummaries, omicName) {
		this.getParent().getModel().setDataDistributionSummaries(dataDistributionSummaries, omicName);

		this.dataDistributionSummaries[omicName] = dataDistributionSummaries;
	}).bind(this);

	this.getVisualOptions = function(propertyName) {
		if (this.visualOptions !== null && propertyName !== undefined) {
			return this.visualOptions[propertyName];
		}
		return this.visualOptions;
	};

	this.setVisualOptions = function(propertyName, value) {
		this.visualOptions[propertyName] = value;
	};

	this.getOmicsValues = function() {
		return this.getParent().getModel().getOmicsValues();
	};
	this.getGeneBasedInputOmics = function() {
		return this.getParent().getModel().getGeneBasedInputOmics();
	};
	this.getCompoundBasedInputOmics = function() {
		return this.getParent().getModel().getCompoundBasedInputOmics();
	};



	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.showDiagramPanel = function() {
		if (this.diagramPanel === null) {
			this.diagramPanel = new PA_Step4KeggDiagramView();
			this.diagramPanel.setParent(this);
			this.searchFeatureIndex = this.diagramPanel.loadModel(this.getModel());
			this.getComponent().add(this.diagramPanel.getComponent());
		}

		this.diagramPanel.toggle(true);
	};

	this.hideDiagramPanel = function(destroy) {
		if (this.diagramPanel !== null) {
			this.diagramPanel.toggle(false);

			if (destroy === true) {
				this.diagramPanel.getComponent().destroy();
				this.diagramPanel = null;
			}
		}
	};

	this.showFindFeaturesPanel = function() {
		this.hideVisualOptionsPanel();

		if (this.findFeaturesPanel === null) {
			this.findFeaturesPanel = new PA_Step4FindFeaturesView();
			this.findFeaturesPanel.setParent(this);
			this.findFeaturesPanel.loadModel(this.getModel());
			this.getComponent().add(this.findFeaturesPanel.getComponent());
		}
		this.findFeaturesPanel.toggle(true);

		this.adjustChildrenWidth();
	};

	this.hideFindFeaturesPanel = function(destroy) {
		if (this.findFeaturesPanel !== null) {
			this.findFeaturesPanel.toggle(false);

			if (destroy === true) {
				this.findFeaturesPanel.getComponent().destroy();
				this.findFeaturesPanel = null;
			}
		}
	};

	this.showVisualOptionsPanel = function() {
		this.hideFindFeaturesPanel();

		if (this.visualOptionsPanel === null) {
			this.visualOptionsPanel = new PA_Step4VisualOptionsView();
			this.visualOptionsPanel.setParent(this);
			this.visualOptionsPanel.loadModel(this.getModel());
			this.getComponent().add(this.visualOptionsPanel.getComponent());
		}
		this.visualOptionsPanel.toggle(true);
		this.adjustChildrenWidth();
	};

	this.hideVisualOptionsPanel = function(destroy) {
		if (this.visualOptionsPanel !== null) {
			this.visualOptionsPanel.toggle(false);

			if (destroy === true) {
				this.visualOptionsPanel.getComponent().destroy();
				this.visualOptionsPanel = null;
			}
		}
	};

	/**
	* The Agentic Graph Walk column (PA_Step4WalkView.js): walks this pathway's
	* drawn interactions on the server and shows the legs and the checked
	* Results section. Created once per pathway view; closing it only hides it.
	*/
	this.showWalkPanel = function() {
		if (typeof PA_Step4WalkView !== "function") {
			return;
		}
		if (this.walkPanel === null) {
			this.walkPanel = new PA_Step4WalkView();
			this.walkPanel.setParent(this);
			this.walkPanel.loadModel(this.getModel());
			this.getComponent().add(this.walkPanel.getComponent());
		}
		this.walkPanel.toggle(true);
	};

	this.showGlobalHeatmap = function() {
		this.hideFeatureSetDetails();

		if (this.globalHeatmapView === null) {
			this.globalHeatmapView = new PA_Step4GlobalHeatmapView();
			this.globalHeatmapView.setParent(this);
			this.globalHeatmapView.loadModel(this.getModel());
			this.getComponent().insert(1, this.globalHeatmapView.getComponent());
		}
		this.globalHeatmapView.toggle(true);
	};

	this.hideGlobalHeatmapPanel = function(destroy) {
		if (this.globalHeatmapView !== null) {
			this.globalHeatmapView.toggle(false);

			if (destroy === true) {
				this.globalHeatmapView.getComponent().destroy();
				this.globalHeatmapView = null;
			}
		}
	};

	this.showFeatureSetDetails = function(targetID, targetModel) {
		this.hideGlobalHeatmapPanel();

		var addComponent = false;
		if (this.featureSetDetailsPanel === null) {
			this.featureSetDetailsPanel = new PA_Step4DetailsView();
			this.featureSetDetailsPanel.setParent(this);
			addComponent = true;
		}

		if (this.featureSetDetailsPanel.getTargetID() !== targetID) {
			this.featureSetDetailsPanel.loadModel(targetModel, this.dataDistributionSummaries, this.visualOptions);
		}

		if (addComponent) {
			this.getComponent().insert(1, this.featureSetDetailsPanel.getComponent());
		} else {
			this.featureSetDetailsPanel.toggle(true);
		}

		this.featureSetDetailsPanel.updateObserver();
	};

	this.hideFeatureSetDetails = function(destroy) {
		if (this.featureSetDetailsPanel !== null) {
			this.featureSetDetailsPanel.toggle(false);

			if (destroy === true) {
				this.featureSetDetailsPanel.getComponent().destroy();
				this.featureSetDetailsPanel = null;
			}
		}
	};

	this.setHeight = function(height) {
	};

	this.adjustChildrenWidth = function() {
		var savedSpace = 450; //min width for pathway view
		var parentSize = $("#pathwaysPanelsWrapper").width();

		if ((this.findFeaturesPanel && this.findFeaturesPanel.isVisible()) ||
		this.visualOptionsPanel && this.visualOptionsPanel.isVisible()) {
			savedSpace += 350;
		}
		if (this.walkPanel && this.walkPanel.isVisible()) {
			savedSpace += 340;
		}

		if (this.globalHeatmapView) {
			this.globalHeatmapView.getComponent().setWidth(Math.min(parentSize - savedSpace, this.globalHeatmapView.getComponent().getWidth()));
		}

		if (this.featureSetDetailsPanel) {
			this.featureSetDetailsPanel.getComponent().setWidth(Math.min(parentSize - savedSpace, this.featureSetDetailsPanel.getComponent().getWidth()));
		}

	};
	
	this.hideTooltips = function() {
		this.diagramPanel.hideTooltips();
	};

	this.updateObserver = function() {
		debugger;
		/********************************************************/
		/* STEP 1: UPDATE DIAGRAM PANEL		                    */
		/********************************************************/
		this.diagramPanel.updateObserver();
		/********************************************************/
		/* STEP 2: UPDATE HEATMAP PANEL		                    */
		/********************************************************/
		this.globalHeatmapView.updateObserver();
	};

	this.applyVisualSettings = function() {
		var me = this;

		/********************************************************/
		/* STEP 1: UPDATE DATA DISTRIBUTION	SUMMARIES           */
		/********************************************************/
		$('input[type=radio][name^=colorByCheckbox]:checked').each(function() {
			if (this.value == "custom") {
				var omicName = this.name.split(/_(.+)/)[1];
				var omicDataDistribution = me.getDataDistributionSummaries(omicName);
				var omicCustomValues = Ext.ComponentQuery.query('[name="customslider_' + omicName + '"]')[0].getValues();
				var visualOptionsCustomValues = (me.visualOptions.customValues || {});

				omicDataDistribution.splice(11, 2, ...omicCustomValues);

				visualOptionsCustomValues[omicName] = omicCustomValues;

				me.setVisualOptions("customValues", visualOptionsCustomValues)
 				me.setDataDistributionSummaries(omicDataDistribution, omicName);
			}
		});
		/********************************************************/
		/* STEP 2: UPDATE DIAGRAM PANEL		                    */
		/********************************************************/
		this.diagramPanel.updateObserver();
		/********************************************************/
		/* STEP 3: UPDATE HEATMAP  & FEATURE SET PANELS         */
		/********************************************************/
		(this.globalHeatmapView !== null && this.globalHeatmapView.updateObserver());
		(this.featureSetDetailsPanel !== null && this.featureSetDetailsPanel.updateObserver());
		/********************************************************/
		/* STEP 4. UPDATE THE CACHE
		/********************************************************/
		this.getParent().getController().updateStoredVisualOptions(this.getParent().getModel().getJobID(), this.visualOptions);

		return this;
	};

	/**
	* This function generates the component (EXTJS) using the content of the model
	* @returns {Ext.ComponentView} The visual component
	*/
	this.initComponent = function() {
		var me = this;

		this.component = Ext.widget({
			xtype: "container", flex:1, defaults: {border: false},
			layout: {type: 'hbox', pack: 'start', align: 'stretch'},
			items: [],
			listeners: {
				beforedestroy: function() {
					if (me.globalHeatmapView !== null) {
						Ext.destroy(me.globalHeatmapView.getComponent());
						me.globalHeatmapView = null;
					}

					if (me.walkPanel !== null) {
						/* First: its layer lives inside the diagram's SVG. */
						Ext.destroy(me.walkPanel.getComponent());
						me.walkPanel = null;
					}

					if (me.diagramPanel !== null) {
						Ext.destroy(me.diagramPanel.getComponent());
						me.diagramPanel = null;
					}

					if (me.searchTool !== null) {
						Ext.destroy(me.searchTool);
						me.searchTool = null;
					}

					me.getModel().deleteObserver(me);
				}
			}
		});
		return this.component;
	};

	return this;
}
PA_Step4PathwayView.prototype = new View();

//------------------------------------------------------------------------------------------------

function PA_Step4KeggDiagramView() {
	/**
	* About this view: This view displays the pathway model as a KEGG diagram combined with
	* the data submitted by the user.
	**/
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4KeggDiagramView";
	this.items = [];

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	/**
	* TODO: DOCUMENTAR
	* Load the pathway information
	*  1. For each feature id
	*      a. Get the omicValues for the feature
	*      b. Create a FeatureSetElem (association of Feature + FeatureGraphicalData)
	*      c. Add the FeatureSetElem to the table indexed by coordinates (controlling overrided elements).
	*  2. For each set of FeatureSetElems, generate a PA_Step4KeggDiagramFeatureSetView and add to the view.
	*
	* @param {type} pathway
	* @returns {undefined}
	*/
	this.loadModel = function(pathway) {
		if (this.model !== null) {
			this.model.deleteObserver(this);
		}
		this.model = pathway;
		this.model.addObserver(this);

		var xyTable = {}; //TABLE CONTAINING ALL FEATURES ORDERED BY X,Y POSITION
		var searchFeatureIndex = {}; //TABLE CONTAINING AN INDEX USED FOR FEATURE SEARCHING

		//Generates the feature views.
		searchFeatureIndex = this.generateFeaturesViews(this.getModel().getMatchedGenes(), xyTable, searchFeatureIndex);
		searchFeatureIndex = this.generateFeaturesViews(this.getModel().getMatchedCompounds(), xyTable, searchFeatureIndex);

		//FOR EACH FEATURE FAMILY, ADD A NEW FEATURESET VIEW
		var featureSets = Object.values(xyTable);
		var view = null;
		for (var i in featureSets) {
			view = new PA_Step4KeggDiagramFeatureSetView().setParent(this.getParent()).loadModel(featureSets[i], this.getModel().getID());
			featureSets[i].addObserver(view);
			this.items.push(view);
		}

		return searchFeatureIndex;
	};

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* This function changes the visibility for the component.
	* @chainable
	* @param {boolean} visible, forces the component visibility
	* @return {PA_Step4KeggDiagramView} the view
	*/
	this.toggle = function(visible) {
		visible = ((visible===undefined)? ! this.getComponent().isVisible():visible);
		this.getComponent().setVisible(visible);
		
		if (! visible) {
			this.hideTooltips();
		}
		
		return this;
	};
	
	this.hideTooltips = function() {
		for (var i in this.items) {
			this.items[i].hideTooltip(true);
		}
	};

	this.expand = function() {
		this.isExpanded = true;

		$("#expandDiagramPanelButton").hide();
		$("#shrinkDiagramPanelButton").show();
		this.getComponent().flex = 1;
		this.getParent().getComponent().doLayout();
	};

	this.shrink = function() {
		this.isExpanded = false;
		$("#expandDiagramPanelButton").show();
		$("#shrinkDiagramPanelButton").hide();

		this.getComponent().flex = 0;
		this.getParent().getComponent().doLayout();
	};

	/**
	* This function download the corresponding information for selected pathway
	* @chainable
	* @param  {String} format the desired format for downloading (png, svg,...)
	* @return {PA_Step4KeggDiagramView}
	*/
	this.download = function() {
		//TODO:format
		this.getParent().getController().downloadPathwayHandler(this.getParent(), this.getParent("PA_Step4JobView").getModel().getJobID(), "png");
		return this;
	};

	/**
	* TODO: DOCUMENTAR
	* This function generates the feature shapes by a given feature data and the feature graphical
	* information and add each feature to a matrix indexes by the coordinates.
	* This last step detects those features that share position in the diagram.
	*/
	this.generateFeaturesViews = function(featuresIDs, xyTable, searchFeatureIndex) {
		var featureSetElem, pos;
		var graphicalOptions = this.getModel().getGraphicalOptions();
		var omicsValues = this.getParent().getOmicsValues();
		var omicsValuesKeys = Object.keys(omicsValues);
		var omicsValuesKeysLower = omicsValuesKeys.map(function(x) { return x.toLowerCase(); });

		for (var i in featuresIDs) {
			var featureID = featuresIDs[i];
			var featureIDLower = featureID.toLowerCase();
			var feature = omicsValues[featureID];

			if (feature === undefined) {
				// Try case-insensitive lookup
				var indexInKeys = omicsValuesKeysLower.indexOf(featureIDLower);
				if (indexInKeys !== -1) {
					feature = omicsValues[omicsValuesKeys[indexInKeys]];
				}
			}

			//Get the coordinates etc. for each box for current feature
			var data = graphicalOptions.findFeatureGraphicalData(featureID);

			//TODO: this code should be removed in future versions, now fixes the problems with not updated species
			if (!(data instanceof Array)){
				data = [data];
			}

			for(var k in data){
				featureSetElem = new FeatureSetElem(feature, data[k]);

				//ADD THE ENTRY TO THE SEARCH TABLE (IDENTIFIER -> featureSetElem)
				searchFeatureIndex[featureID] = featureSetElem;

				if (feature !== undefined) {
					//ADD THE ENTRY TO THE SEARCH TABLE (KEGG NAME -> featureSetElem)
					if (feature.name && feature.name !== "") {
						searchFeatureIndex[feature.name] = featureSetElem;
					}
					//ADD THE ENTRY TO THE SEARCH TABLE (INPUT NAME -> featureSetElem)
					for (var j in feature.omicsValues) {
						var omicValue = feature.omicsValues[j];
						if (omicValue.inputName && omicValue.inputName !== "") {
							searchFeatureIndex[omicValue.inputName] = featureSetElem;
						}
						if (omicValue.originalName && omicValue.originalName !== "") {
							searchFeatureIndex[omicValue.originalName] = featureSetElem;
						}
					}
				}

				pos = data[k].getX() + "#" + data[k].getY();
				if (xyTable[pos] === undefined) {
					xyTable[pos] = new FeatureSet(data[k].getX(), data[k].getY());
				}
				xyTable[pos].addFeature(featureSetElem);
				featureSetElem.setParent(xyTable[pos]);
			}
		}

		return searchFeatureIndex;
	};

	/**
	* TODO: DOCUMENTAR
	* This function updates the content of the pathway
	* @chainable
	* @returns {PA_Step4KeggDiagramView}
	*/
	this.updateObserver = function() {
		//FOR EACH ITEM IN THE PATHWAY VIEW (potentially PA_Step4KeggDiagramFeatureSetView items)
		for (var i in this.items) {
			this.items[i].updateObserver();
		}
		/* This loop repaints items only. The evidence layer's satellites are
		   regenerated glyphs that live outside items, so without this they
		   keep the OLD colour scale after the user hits Apply -- stale data
		   sitting next to freshly repainted boxes. */
		if (this.evidenceOverlay) { this.evidenceOverlay.refresh(); }
		return this;
	};


	/**
	* This function apply the settings that user can change
	* for the visual representation of the model (w/o reload everything).
	* @chainable
	* @returns {PA_Step4KeggDiagramView}
	*/
	this.applyVisualSettings = function() {
		debugger;
		this.updateObserver();
		return this;
	};

	/**
	* Render an OmniPath pathway as an interactive interaction network.
	*
	* The other three sources paint their features over a diagram; OmniPath has
	* none, so its pathway is drawn as the signed, directed graph it actually
	* is. The nodes reuse the identical painted glyphs the raster views place on
	* their diagrams, so a gene carries the same colours here as on a KEGG map.
	*
	* @param {jQuery} bodyEl the panel body to render into
	* @param {Object} dataDistributionSummaries
	* @param {Object} visualOptions
	*/
	this.renderOmniPathNetwork = function(bodyEl, dataDistributionSummaries, visualOptions) {
		/* The organism lives on the job model, several views up: this view's own
		   parent is the pathway panel and carries no model of its own. */
		var organism = null, node = this;
		for (var hop = 0; hop < 6 && node && !organism; hop++) {
			try {
				var nodeModel = node.getModel && node.getModel();
				if (nodeModel && typeof nodeModel.getOrganism === "function") {
					organism = nodeModel.getOrganism();
				}
			} catch (error) { /* not this one; keep climbing */ }
			node = (node.getParent ? node.getParent() : null);
		}
		if (!organism) {
			try { organism = application.mainView.currentView.getModel().getOrganism(); }
			catch (error) { organism = null; }
		}
		if (!organism) {
			bodyEl.html('<p class="omnipath-net-status">Could not resolve the organism ' +
				'for this pathway, so its interaction network cannot be loaded.</p>');
			return;
		}

		this.omniPathNetwork = new PA_Step4OmniPathNetworkView().render(bodyEl, {
			organism: organism,
			pathwayID: this.getModel().getID(),
			items: this.items,
			summaries: dataDistributionSummaries,
			visual: visualOptions,
			maxEdges: 900
		});
	};

	/**
	* Wire the panel's own toolbar. Shared by the raster and network views: the
	* header exists either way, and without this its buttons are inert on an
	* OmniPath pathway.
	*/
	this.bindDiagramPanelControls = function() {
		var me = this;
		$(this.getComponent().el.dom).find(".pa-walk-open").on("click", function() { me.getParent().showWalkPanel(); });
		$("#hideDiagramPanelButton").click(function() { me.getParent().hideDiagramPanel(); });
		$("#expandDiagramPanelButton").click(function() { me.expand(); });
		$("#shrinkDiagramPanelButton").click(function() { me.shrink(); });
		$("#downloadDiagramPanelButton").click(function() { me.download(); });
	};


	/**
	* This function generates the component (EXTJS) using the content of the model
	* @returns {Ext.ComponentView} The visual component
	*/
	this.initComponent = function() {
		var me = this;

		//CREATE THE COMPONENT THAT CONTAINS THE SVG IMAGE, THE SPRITES WILL BE CREATED AFTER RENDERING
		this.component = Ext.widget({
			xtype: "box",
			cls: "lateralOptionsPanel",
			defaults: {border: false},
			flex: 1, minWidth: 400, previousWidth: 400, width: 400,  height: ($("#mainViewCenterPanel").height() - 100),
			html:
			'<div class="lateralOptionsPanel-header" data-guides="ignore">' +
			'   <div class="lateralOptionsPanel-toolbar">' +
			'    <a href="javascript:void(0)" class="toolbarOption btn-primary helpTip" id="hideDiagramPanelButton" title="Hide this panel"><i class="fa fa-times"></i></a>' +
			'    <a href="javascript:void(0)" class="toolbarOption btn-primary helpTip" id="expandDiagramPanelButton" style="display:none;"  title="Expand this panel"><i class="fa fa-expand"></i></a>' +
			'    <a href="javascript:void(0)" class="toolbarOption btn-primary helpTip" id="shrinkDiagramPanelButton" title="Shrink this panel"><i class="fa fa-compress"></i></a>' +
			'    <a href="javascript:void(0)" class="toolbarOption btn-default downloadTool helpTip" id="downloadDiagramPanelButton" title="Download the diagram"><i class="fa fa-download"></i> Download</a>' +
			(this.getParent().getParent().getModel().aiConsent && this.model.getSource() !== "MapMan"
				? '    <a href="javascript:void(0)" class="toolbarOption btn-default downloadTool helpTip pa-walk-open" title="Walk this pathway with the AI agent"><i class="fa fa-random"></i> Walk</a>'
				: '') +
			'   </div>' +
			'   <h2>' + this.model.getName() + '</h2>' +
			'</div>' +
			'<div class="lateralOptionsPanel-body">' +
			'  <svg xmlns="http://www.w3.org/2000/svg" class="keggPathwaySVG" version="1.1" ></svg>' +
			"</div>",
			listeners: {
				boxready: function() {
					var graphicalOptions = me.getModel().getGraphicalOptions();
					var dataDistributionSummaries = me.getParent().getDataDistributionSummaries();
					var visualOptions = me.getParent().getVisualOptions();

					/* OmniPath carries no diagram, so there is no raster to scale
					   feature boxes onto and no geometry worth preserving. Its
					   pathway IS a network, and it is rendered as one the user can
					   actually move and interrogate. */
					if (me.getModel().getSource() === "OmniPath") {
						me.renderOmniPathNetwork($(this.el.dom).find(".lateralOptionsPanel-body"),
							dataDistributionSummaries, visualOptions);
						me.bindDiagramPanelControls();
						return;
					}

					//GET THE VIEW PORT AND IF THE IMAGE IS BIGGER, CALCULATE THE ADJUST FACTOR
					var viewportWidth = $(this.el.dom).width();
					var headerHeight = $(this.el.dom).find(".lateralOptionsPanel-header").outerHeight();
					var viewportHeight = $("#mainViewCenterPanel").height() - headerHeight - 90;
					var imageWidth = graphicalOptions.getImageWidth();
					var imageHeight = graphicalOptions.getImageHeight();
					var imageProportion = imageHeight / imageWidth;
					var adjustFactor = 1;

					if (viewportWidth < imageWidth) {
						imageWidth = viewportWidth * 0.98; /*UN 95% del espacio disponible*/
						imageHeight = imageWidth * imageProportion;
						adjustFactor = imageWidth / graphicalOptions.getImageWidth();
					}

					if (viewportHeight < imageHeight) {
						imageHeight = viewportHeight * 0.98; /*UN 95% del espacio disponible*/
						imageWidth = imageHeight / imageProportion;
						adjustFactor = imageHeight / graphicalOptions.getImageHeight();
					}
					//TODO REMOVE adjustFactor
					me.getParent().setVisualOptions("adjustFactor", adjustFactor);
					me.getParent().setHeight(imageHeight + 200);

					//USING SVG.JS library
					canvas = SVG($(this.el.dom).find(".keggPathwaySVG")[0]);
					canvas.size("100%", imageHeight);
					canvas.viewbox({
						x: 0,
						y: 0,
						width: imageWidth,
						height: imageHeight
					});

					// Background image
					// For KEGG we only need to pass the digit code, but for MapMan
					// the full ID is required.
					var source = me.model.getSource();
					var is_kegg = (source == undefined || source == "KEGG");
					var canvas_dir = is_kegg ? me.model.getID().replace(/\D/g, '') : me.model.getID() + '_' + source;

					canvas.image(location.pathname + "kegg_data/" + canvas_dir, imageWidth, imageHeight).addClass("keggImageBack");

					//GENERATE THE SUBCOMPONETS VIEWS
					try {
						featureSetViews = {};
						var featuresAux = null, featureShape;

						for (var i in me.items) {
							featureShape = me.items[i].drawComponent(canvas, dataDistributionSummaries, visualOptions);

							//GET THE NAMES FOR ALL THE FEATURES IN THE FEATURE SET
							featuresAux = me.items[i].getModel().getFeatures();
							for (var j in featuresAux) {
								featureSetViews[featuresAux[j].getFeature().getName()] = featureShape;
							}
						}
					} catch (error) {
						showErrorMessage(error.message, {
							message: error.stack
						});
					}

					/* EVIDENCE OVERLAY -- drawn AFTER the feature boxes so its arcs paint
					   above the omics sprites (SVG stacking is document order). The
					   box-occupancy map is built here rather than server-side because the
					   x#y bucketing that collapses several genes into one drawn box is a
					   CLIENT rule; the overlay needs it to decide whether an edge may
					   claim an arrowhead or must settle for a badge. */
					try {
						var boxOccupancy = {}, itemsByFeatureID = {};
						for (var occupancyIdx in me.items) {
							var occupancyItem = me.items[occupancyIdx];
							var occupancyModel = occupancyItem.getModel();
							boxOccupancy[occupancyModel.getX() + "#" + occupancyModel.getY()] =
								(occupancyModel.getFeatures() || []).length;
							/* Index by the graphical ID the evidence payload speaks, not by
							   feature name: the server returns canonical IDs. */
							(occupancyModel.getFeatures() || []).forEach(function(setElem) {
								try {
									var gd = setElem.getFeatureGraphicalData();
									if (gd && gd.getID()) { itemsByFeatureID[gd.getID()] = occupancyItem; }
								} catch (indexError) { /* no graphical data: nothing to park */ }
							});
						}

						var diagramPanelEl = $(this.el.dom);
						me.evidenceOverlay = new PA_Step4EvidenceOverlay().render({
							canvas: canvas,
							/* A FUNCTION, resolved when the card is built: the
							   Pathway information column is constructed after this
							   panel, so it does not exist yet at this line. The card
							   used to go into the diagram panel's own body, which is
							   as tall as the map -- so it opened below the fold and
							   its controls were never seen. */
							panelEl: function() {
								var column = $("#mainViewCenterPanel")
									.find(".lateralOptionsPanel-body.findFeaturesContainer");
								return column.length
									? column
									: diagramPanelEl.find(".lateralOptionsPanel-body");
							},
							jobID: me.getParent("PA_Step4JobView").getModel().getJobID(),
							/* For the replicate mode: a box the layer adds paints
							   the regulator's values, and it has to collapse them
							   the same way every printed box on this map does. */
							jobView: me.getParent("PA_Step4JobView"),
							/* For the click-through: an added box opens the same
							   feature panel a printed box opens, and that panel
							   is owned by the pathway view. */
							pathwayView: me.getParent(),
							pathwayID: me.getModel().getID(),
							graphicalOptions: graphicalOptions,
							adjustFactor: adjustFactor,
							boxOccupancy: boxOccupancy,
							/* The placer needs the real geometry of every painted box to
							   find free space, and the items themselves to regenerate a
							   regulator's glyph with its gene symbol baked in. */
							items: me.items,
							itemsByID: itemsByFeatureID,
							summaries: dataDistributionSummaries,
							visualOptions: visualOptions,
							/* Positions the user dragged, kept per pathway inside the
							   job's own visualOptions -- the same store the colour
							   scale and visible omics already live in, so a nudge
							   survives closing the diagram and reopening it. */
							placement: (visualOptions.evidencePlacement || {})[me.getModel().getID()] || {},
							onPlacementChange: function(placement) {
								var pathwayView = me.getParent();
								var options = pathwayView.getVisualOptions();
								if (!options.evidencePlacement) { options.evidencePlacement = {}; }
								options.evidencePlacement[me.getModel().getID()] = placement;
								pathwayView.setVisualOptions("evidencePlacement",
															 options.evidencePlacement);
								pathwayView.getParent().getController().updateStoredVisualOptions(
									pathwayView.getParent().getModel().getJobID(), options);
							},
							maxEdges: 8
						});
					} catch (overlayError) {
						/* Additive by design: a job with no MORE analysis, or any failure
						   inside the overlay, must never take the diagram down with it. */
						console.warn("Evidence overlay unavailable:", overlayError);
					}

					//SOME EVENT HANDLERS
					$(this.el.dom).find(".pa-walk-open").on("click", function() {
						me.getParent().showWalkPanel();
					});
					$("#hideDiagramPanelButton").click(function() {
						me.getParent().hideDiagramPanel();
					});
					$("#expandDiagramPanelButton").click(function() {
						me.expand();
					});
					$("#shrinkDiagramPanelButton").click(function() {
						me.shrink();
					});
					$("#downloadDiagramPanelButton").click(function() {
						me.download();
					});
					//START PAN/ZOOM
					me.zoomTool = $(this.el.dom).find(".keggPathwaySVG").svgPanZoom({zoomFactor: 0.10, "initialViewBox" : {width:imageWidth, height:imageHeight}});
					$(this.el.dom).append(
						'<div class="zoomTool">' +
						'  <a href="javascript:void(0)" class="zoomIn" title="Zoom-in (110%)"><i class="fa fa-plus"></i></a>' +
						'  <a href="javascript:void(0)" class="zoomOut" title="Zoom-out (90%)"><i class="fa fa-minus"></i></a>' +
						'</div>'
					);

					$("a.zoomIn").click(function() {
						me.zoomTool.zoomIn();
					});
					$("a.zoomOut").click(function() {
						me.zoomTool.zoomOut();
					});
				},
				beforedestroy: function() {
					/* Cytoscape owns a canvas and a layout worker; destroying the
					   Ext panel around it leaves both running. */
					if (me.omniPathNetwork) {
						me.omniPathNetwork.destroy();
						me.omniPathNetwork = null;
					}

					if (me.evidenceOverlay) {
						/* The overlay owns an SVG group and a legend node outside the
						   Ext component's own markup; neither goes away on its own. */
						me.evidenceOverlay.destroy();
						me.evidenceOverlay = null;
					}

					//REMOVE ALL PA_Step4KeggDiagramFeatureSetView
					for (var i in me.items) {
						me.items[i].getModel().deleteObserver(me.items[i]);
						Ext.destroy(me.items[i].getComponent());
						delete me.items[i];
					}

					me.getModel().deleteObserver(me);
				},
				afterHide: function() {
					console.log("AFTER HIDE DE KEGG DIAGRAM");
				}
			}
		});

		return this.component;
	};

	return this;
}
PA_Step4KeggDiagramView.prototype = new View();

function PA_Step4KeggDiagramFeatureSetView() {
	/**
	* About this view: TODO: DOCUMENTAR
	**/
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4KeggDiagramFeatureSetView";
	this.featureView = null;
	this.adjustFactor = 1;
	this.tooltipComponent = null;
	this.metageneMode = false;

	this.isMetageneMode = function() {
		return this.metageneMode;
	};

	this.switchMetageneMode = function(metageneMode) {
		this.metageneMode = metageneMode;
	};

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.loadModel = function(featureSet, pathwayID) {
		this.model = featureSet;
		var pos = 0;
		var features = this.model.getFeatures();
		var geneOmicNames = this.getParent("PA_Step4JobView").getModel().getGeneOmicNames();
		var omicNames = this.getParent("PA_Step4JobView").getModel().getOmicNames();

		// Use of metagenes
		// If the number of features associated to the box exceeds 5,
		// we calculate the metagenes and show them instead.
		var metageneSuccess = false;

		if (features.length > 5) {
			try {
				// Cached lookup table: omicName → { mapping, nSamples }. Built
				// once per box so each metagene OmicValue can inherit the
				// parent omic's replicate→sample aggregation (when active) and
				// expose per-sample values to "Show samples" mode in Step-4.
				var jobModel = this.getParent("PA_Step4JobView").getModel();
				var inputOmicByName = {};
				jobModel.getGeneBasedInputOmics()
					.concat(jobModel.getCompoundBasedInputOmics())
					.forEach(function(o) { inputOmicByName[o.omicName] = o; });

				omicNames.forEach(function(omic) {
					// Use all values of the same omic in all features associated to the box.
					var omicValues = this.model.getAllOmicValues(omic).map(x => x.getValues());
					// It is important that the featureType contains gene or compound word, as it
					// will be used later to filter.
					var featureType = "metagene"; //geneOmicNames.includes(omic) ? "gene" : "compound";

					var inputOmic = inputOmicByName[omic];
					var mapping = (inputOmic && Array.isArray(inputOmic.replicateMapping))
						? inputOmic.replicateMapping : null;
					var nSamples = (inputOmic && Array.isArray(inputOmic.sampleHeader))
						? inputOmic.sampleHeader.length : 0;

					this.model.addOmicMetagenes(
						omic, featureType,
						mlPCA.generateMetagenes(omicValues),
						mapping, nSamples);
				}.bind(this));

				// Validate metagenes were actually generated before switching mode
				var generatedMetagenes = this.model.getMetagenes();
				if (generatedMetagenes && generatedMetagenes.length > 0 && generatedMetagenes[0]) {
					this.switchMetageneMode(true);
					this.model.setMainFeature(generatedMetagenes[0]);
					this.featureView = new PA_Step4KeggDiagramFeatureSetSVGBox().setParent(this).loadModel(this.model.getMainFeature()).setComponentID(pathwayID + "_" + this.model.getX() + "_" + this.model.getY()).setIsUnique(false);
					metageneSuccess = true;
				}
			} catch (e) {
				console.error("Error generating metagenes, falling back to standard feature display:", e);
				this.model.setMetagenes(null);
			}
		}

		if (!metageneSuccess) {
			this.switchMetageneMode(false);

			for(var i in features){
				if(features[i].getFeature().isRelevant()){
					pos = i;
					break;
				}
			}
			this.model.setMainFeature(features[pos]);

			this.featureView = new PA_Step4KeggDiagramFeatureSetSVGBox().setParent(this).loadModel(this.model.getMainFeature()).setComponentID(pathwayID + "_" + this.model.getX() + "_" + this.model.getY()).setIsUnique((features.length === 1));
		}

		return this;
	};

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.showTooltip = function(dataDistributionSummaries, visualOptions, pinned) {
		/* Create only when there is no instance */
		if (this.tooltipComponent == null) {
			this.tooltipComponent = new PA_Step4KeggDiagramFeatureSetTooltip();
			this.tooltipComponent.loadModel(this.getModel());
			this.tooltipComponent.setParent(this);
		}
		
		this.tooltipComponent.show(this.component.id, dataDistributionSummaries, visualOptions, pinned);
	};
	
	this.hideTooltip = function(force=false) {
		// TODO: destroy this component when switching tooltips?
		if (this.tooltipComponent != null) {
			this.tooltipComponent.hide(force);
		}
	};
	
	this.resetTooltip = function() {
		this.tooltipComponent = null;
	};

	this.drawComponent = function(canvas, dataDistributionSummaries, visualOptions) {
		var me = this;
		this.adjustFactor = visualOptions.adjustFactor;
		var featureAux = this.initComponent(dataDistributionSummaries, visualOptions);

		var featureShape = canvas.image(featureAux.src, featureAux.width, featureAux.height).move(featureAux.x, featureAux.y).attr("id", featureAux.id);

		var displayTooltip = function(event, pinned=false) {
			/* If in the process of closing the tooltip, remove the timer */
			if (me.hideTimer) {
				clearTimeout(me.hideTimer);
			}
			
			// Remove the timer if we are trying to pin (click instead of mouseenter)
			if (me.timer && pinned) {
				clearTimeout(me.timer);
			} 
	
			me.timer = setTimeout(function() {
				me.timer = null;
				me.showTooltip(dataDistributionSummaries, visualOptions, pinned);
			}, pinned ? 0 : 500)			
		};
		
		var removeTooltip = function() {
			clearTimeout(me.timer);
			
			me.hideTimer = setTimeout(function() {
				me.hideTimer = null;
				me.hideTooltip(false);
			}, 500);	
		};

		featureShape.on("mouseover", displayTooltip).on("click", function(event) { console.log("click");displayTooltip(event, true); }).on("mouseleave", removeTooltip);

		return featureShape;
	};

	this.updateObserver = function() {
		//Update ONLY the visible item (mainItem)
		this.featureView.loadModel(this.getModel().getMainFeature()).updateObserver();
		
		if (this.tooltipComponent) {
			this.tooltipComponent.updateObserver();
		}
	};

	/**
	* This function generates the component (EXTJS) using the content of the model
	* @returns {Ext.ComponentView} The visual component
	*/
	this.initComponent = function(dataDistributionSummaries, visualOptions) {
		var me = this;
		visualOptions.adjustFactor = this.adjustFactor;
		this.component = this.featureView.initComponent(dataDistributionSummaries, visualOptions);
		return this.component;
	};
	
	this.beforeDestroy = function() {	
		if (this.tooltipComponent) {
			this.tooltipComponent.getComponent().destroy();
		}
	};

	return this;
}
PA_Step4KeggDiagramFeatureSetView.prototype = new View();

function PA_Step4KeggDiagramFeatureSetTooltip() {
	/**
	* About this view: This class creates a new view for a given FeatureSet
	* This view is a panel containing a HEATMAP and a LINE PLOT showing an overview of
	* the feature information
	* This view is a tooltip for a PA_Step4KeggDiagramFeatureSetView item, e.g. a box in the
	* pathway SVG, so it will turn visible when the situate the mouse over the parent item.
	*/

	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4KeggDiagramFeatureSetTooltip";
	this.targetID = null;
	this.featureView = null;
	this.isPinned = false;
	this.hideTimer = null;

	/***********************************************************************
	* GETTER AND SETTERS
	***********************************************************************/

	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.show = function(targetID, dataDistributionSummaries=null, visualOptions=null, pinned=false) {
		if ( ! this.isPinned) {
			this.getComponent().showBy(targetID);
			this.targetID = targetID;
			this.updateObserver();	
			
			if (pinned) {
				this.pin();
			}
		}
	};

	this.hide = function(force) {
		if (! this.isPinned || force) {
			this.forceHide = force;
			this.getComponent().close();	
			
			// Remove the observer so it can be GC
			this.getModel().deleteObserver(this);
		}
	};
	
	this.pin = function() {
		this.getComponent().tools[0].setType('unpin');
		this.isPinned = true;
	};
	
	this.unpin = function() {
		this.getComponent().tools[0].setType('pin');
		this.isPinned = false;
	};
	
	this.plus = function() {
		this.getComponent().tools[1].setType('minus');
		this.featureView.showExpandedInfo();
	};
	
	this.minus = function() {
		this.getComponent().tools[1].setType('plus');
		this.featureView.hideExpandedInfo();
	};
	
	this.showFeatureSetDetails = function(targetID, feature) {
		this.getParent().getParent().showFeatureSetDetails(targetID, this.getModel(), feature);
	};

	/**
	* This function changes the main feature to show in the featureSetViews
	* @param  {Integer} sense indicates the direction to change (+1 next, -1 prev.)
	* @return {PA_Step4KeggDiagramFeatureSetTooltip} the view
	*/
	this.changeVisibleFeature = function(sense, changeMode=false){
		var metageneMode = this.getParent().isMetageneMode();
		var currentFeaturePos = 0;
		var newFeature;

		if (metageneMode) {
			var metagenes = this.getModel().getMetagenes();
			if (!metagenes || metagenes.length === 0) {
				console.warn("changeVisibleFeature: metagene mode is active but metagenes are null/empty. Switching off metagene mode.");
				this.getParent().switchMetageneMode(false);
				return this;
			}

			if (! changeMode) {
				currentFeaturePos = metagenes.indexOf(this.getModel().getMainFeature());
				currentFeaturePos = ((currentFeaturePos + sense) + metagenes.length) % metagenes.length;
			}

			newFeature = metagenes[currentFeaturePos];
		} else {
			if (! changeMode) {
				currentFeaturePos = this.getModel().features.indexOf(this.getModel().getMainFeature());
				currentFeaturePos = ((currentFeaturePos + sense) + this.getModel().features.length) % this.getModel().features.length;
			}

			newFeature = this.getModel().features[currentFeaturePos];
		}


		this.getModel().setMainFeature(newFeature);
		this.getModel().setChanged();
		this.getModel().notifyObservers();
		return this;
	};

	/**
	* This function updates the visual representation of the model.
	*  - STEP 1. INITIALIZE VARIABLES
	*  - STEP 2. CHECK IF THERE ARE OTHER FEATURES AT THE SAME POSITION
	*  - STEP 3. UPDATE SUBCOMPONENTS
	* @chainable
	* @returns {PA_Step4KeggDiagramFeatureSetTooltip}
	*/
	this.updateObserver = function() {
		var me = this;

		/********************************************************/
		/* STEP 1. INITIALIZE VARIABLES                         */
		/********************************************************/
		var mainFeatureSetItem = this.getModel().getMainFeature();
		var mainFeatureGraphicalData = mainFeatureSetItem.getFeatureGraphicalData();
		var featureType = mainFeatureSetItem.getFeature().getFeatureType();
		var boxTitle = mainFeatureGraphicalData.getBoxTitle();
		var geneName = mainFeatureSetItem.getFeature().getName().split(",")[0];
		var metagenes = this.getModel().getMetagenes();
		var metageneMode = this.getParent().isMetageneMode();
		var message = "";

		/********************************************************/
		/* STEP 2. CHECK IF THERE ARE OTHER FEATURES AT THE     */
		/*         SAME POSITION                                */
		/********************************************************/
		var nOtherItems = metageneMode ? metagenes.length-1 : this.model.getFeatures().length-1;
		var domEl = this.getComponent();
		var showBoxTitle = false;
		
		if (! domEl.rendered) {
			domEl.doLayout();
		}
		
		if (domEl.el) {
			domEl = domEl.el.dom;

			if (nOtherItems > 0) {
				$(domEl).find(".otherFeaturesMessage").html(nOtherItems + " more " + featureType + (nOtherItems > 1 ? "s" : "") + " at this position.");
				$(domEl).find(".otherFeaturesLabel").show();
			} else {
				$(domEl).find(".otherFeaturesLabel").hide();
			}
			
			// Customize title with feature name
			if (boxTitle != undefined) {
				var htmlTitle = "Feature: " + geneName;

				if (mainFeatureSetItem.getFeature().isRelevant()) {
					htmlTitle += "<i class='featureNameLabelRelevant relevantFeature'></i>";
				}

				if (mainFeatureSetItem.getFeature().isRelevantAssociation()) {
					htmlTitle += "<i class='featureNameLabelRelevant relevantAssociationFeature'></i>";
				}

				$(domEl).find(".boxTitleLabel span").html(htmlTitle).show();

				showBoxTitle = true;
			} else {
				// The title span shares .boxTitleLabel with the Genes/Metagenes
				// toggle: when metagenes reveal the container without a box
				// title, the template placeholder must not ride along.
				$(domEl).find(".boxTitleLabel span").hide();
			}

			var buttonWrapper = $(domEl).find(".twoOptionsButtonWrapper");

			// Enable two buttons if there are metagenes present
			if (metagenes !== null && metagenes.length) {			
				// In the first run no element will be selected. Choose one based on the presence of metagenes.
				if (! buttonWrapper.has("a.selected")) {
					var selectedButton = metageneMode ? 'metagenes' : 'genes';

					buttonWrapper.find("a[name=" + selectedButton + "]").addClass("selected");
				} else {
					metageneMode = this.metageneMode = (buttonWrapper.find("a[name=" + selectedButton + "]").attr("name") == "metagenes");
				}

				buttonWrapper.show();

				showBoxTitle = true;
			} else {
				buttonWrapper.hide();
			}
			
			$(domEl).find(".boxTitleLabel").toggle(showBoxTitle);
		}
		/********************************************************/
		/* STEP 3. UPDATE SUBCOMPONENTS                         */
		/********************************************************/
		this.featureView.loadModel(mainFeatureSetItem);
		this.featureView.updateObserver(true);//HIDE LINKS
		this.getComponent().updateLayout();
		
		// Set title
		var featureTitle = boxTitle != undefined ? boxTitle : geneName;
		var htmlTitle = "<span class='featureNameLabel'>" + featureTitle + "</span>";
		
		if (boxTitle == undefined && mainFeatureSetItem.getFeature().isRelevant()) {
			htmlTitle += "<i class='featureNameLabelRelevant relevantFeature'></i>";
		}
		if (boxTitle == undefined && mainFeatureSetItem.getFeature().isRelevantAssociation()) {
			htmlTitle += "<i class='featureNameLabelRelevant relevantAssociationFeature'></i>";
		}
		
		this.getComponent().setTitle(htmlTitle);

		return this;
	};

	/**
	* This function generates the component (EXTJS) using the content of the model
	* @returns {Ext.ComponentView} The visual component
	*/
	this.initComponent = function() {
		var me = this;
		this.featureView = new PA_Step4KeggDiagramFeatureView();
		this.featureView.setParent(me);
		this.featureView.setCollapsible(false);
		this.featureView.setClosable(true);

		this.component = Ext.create('Ext.window.Window', {
			target: "",
			layout: "auto",
			resizable: false, bodyPadding:0,
			autoHeight: true, width: 280, minHeight:240,
			closable: false,
			tools: [
				{
					type: 'pin',
					tooltip: 'Keep or not this window open',
					callback: function(panel, tool, event) {
						me[tool.type]();
					}
				},
				{
					type: 'plus',
					tooltip: 'Show or hide more information',
					callback: function(panel, tool, event) {
						me[tool.type]();
					}
				},
				{
					type: 'close',
					tooltip: 'Close this window',
					callback: function(panel, tool, event) {
						me.forceHide = true;
						me.getComponent().close();
					}
				}
			],
			items: [
				{
					xtype: "box", html:
					'<div class="boxTitleLabel" style="text-align: center; display: none;">' +
					'  <span>Unnamed event</span>' +
					'  <div class="twoOptionsButtonWrapper">' +
					'      <a href="javascript:void(0)" class="button twoOptionsButton" name="genes">Genes</a>' +
					'      <a href="javascript:void(0)" class="button twoOptionsButton" name="metagenes">Metagenes</a>' +
					'  </div>' +
					'</div>'					
				},
				{
					xtype: "box", html:
					'<div class="otherFeaturesLabel" style="text-align: center; display: block;">' +
					'  <span class="step4TooltipPrevButton tooltipDetailsSpan" style="display: inline;">' +
					'    <i class="fa fa-caret-left" style="padding-right: 3px;"></i><a href="javascript:void(0)" style="display:inline-block"> Prev.</a>' +
					'  </span>' +
					'  <span class="otherFeaturesMessage tooltipDetailsSpan" > N more Genes at this position.</span>' +
					'  <span class="step4TooltipNextButton tooltipDetailsSpan" style="display: inline;">' +
					'    <a href="javascript:void(0)" style="display:inline-block">Next</a><i class="fa fa-caret-right" style="padding-left: 3px;"></i>' +
					'  </span>' +
					'</div>'
				},
				this.featureView.getComponent(),
				{
					xtype: "box", html:
					'<div style="text-align: center;margin: 10px 0px;">'+
					'  <a href="javascript:void(0)" class="step4TooltipMoreButton button btn-primary btn-no-float"><i class="fa fa-search-plus"></i> Show details</a>'+
					'</div>'
				}
			],
			showBy: function(el, pos) {
				if (this.el == null) {
					this.show();
				}
				this.showAt(this.el.getAlignToXY(el, pos || this.defaultAlign, [20, 20]));
			},
			listeners: {
				boxready: function() {
					//SOME EVENT HANDLERS
					var domEl = me.getComponent().el.dom;

					// This window is anchored to wherever the user clicked on the
					// diagram (showBy above), never to a fixed page position, so it
					// can never land on the page's column rails - same case as
					// #messageDialogPanel in AlignmentGuides.js's ignore list.
					domEl.setAttribute("data-guides", "ignore");

					$(domEl).find(".step4TooltipMoreButton").click(function() {
						me.getComponent().hide();
						me.showFeatureSetDetails(me.targetID, me.getModel());
					});
					$(domEl).find(".step4TooltipPrevButton").click(function() {
						me.changeVisibleFeature(-1);
					});
					$(domEl).find(".step4TooltipNextButton").click(function() {
						me.changeVisibleFeature(1);
					});
					
					if (me.getParent().isMetageneMode()) {
						$(domEl).find("a.twoOptionsButton[name=metagenes]").addClass("selected");
					} else {
						$(domEl).find("a.twoOptionsButton[name!=metagenes]").addClass("selected");
					}
					
					$(domEl).find(".boxTitleLabel a.twoOptionsButton").click( function(){
						var parent = $(this).parent(".twoOptionsButtonWrapper");
	
						$(parent).find("a.twoOptionsButton.selected").removeClass("selected");
						$(this).addClass("selected");

						// Enable or disable metagene mode
						me.getParent().switchMetageneMode($(this).attr('name') == 'metagenes');
						me.changeVisibleFeature(0, true);
					});
				},
				beforehide: function() {
					if ($(me.getComponent().el.dom).is(":hover") && me.forceHide !== true) {
						return false;
					}
					delete me.forceHide;
				},
				beforedestroy: function() {
					me.featureView.getComponent().destroy();
					me.getParent().resetTooltip();
				},
				dragstart: function() {					
					if (me.hideTimer) {
						clearTimeout(me.hideTimer)
					}
					
					me.pin();
				},
				afterrender : function(win) {
					var windowEl  = win.el;
					
					var hideTimeout = function() {
						if (me.hideTimer) {
							clearTimeout(me.hideTimer)
						}
						
						if (! me.isPinned) {
							me.hideTimer = setTimeout(function() {
								me.hideTimer = null;
								me.hide(true);
							}, 500);
						}
					};
					
					var clearHiderTimeout = function() {
						if (me.hideTimer) {
							clearTimeout(me.hideTimer)
						}
					};
					
					$(windowEl.dom).hover(clearHiderTimeout, hideTimeout);
				}
			}
		});

		return this.component;
	};
	return this;
}
PA_Step4KeggDiagramFeatureSetTooltip.prototype = new View();

function PA_Step4KeggDiagramFeatureView(showButtons) {
	/**
	* About this view: this class creates a new view for a given Feature, view is a panel
	* containing a HEATMAP and a LINE PLOT showing an overview of the feature information
	* This view is the content for a a tooltip for a PA_Step4KeggDiagramFeatureSetTooltip
	* and for an overview of feature at a PA_Step4DetailsFeatureSetView
	**/
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4KeggDiagramFeatureView";
	this.collapsible = true;
	this.closable = false;
	this.showButtons = (showButtons === true);

	/***********************************************************************
	* GETTER AND SETTERS
	***********************************************************************/
	this.setCollapsible = function(collapsible){
		this.collapsible = collapsible;
	};
	this.setClosable = function(closable){
		this.closable = closable;
	};

	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* This function updates the content of the panel
	* TODO: DOCUMENTAR
	* @param {type} dataDistributionSummaries
	* @param {type} visualOptions
	* @param {type} hideLinks
	* @returns {undefined}
	*/
	this.updateObserver = function(hideLinks=false, callback=null) {
		var me = this;

		var dataDistributionSummaries = this.getParent("PA_Step4PathwayView").getDataDistributionSummaries();
		var visualOptions = this.getParent("PA_Step4PathwayView").getVisualOptions();
		var componentID = "#" + this.getComponent().getId();

		//UPDATE THE NAME OF THE FEATURE
		var componentNames = this.getModel().getFeature().getName().split(",");
		$(componentID + " .featureNameLabel").text(" " + componentNames[0]);
		$(componentID + " .featureNameLabelRelevant").toggle(this.getModel().getFeature().isRelevant()|this.getModel().getFeature().isRelevantAssociation());

		//Do not render if the component was never expanded (lazy rendering)
		if($(componentID).hasClass("neverExpanded")){
			// If callback was provided a rendering is required.
			if(me.collapsible && ! callback){
				return;
			}
			$(componentID).find(".geneInfoContainer").show(); //if it is not collapsible but is first call, expand
		}

		var featureType = this.getModel().getFeature().getFeatureType();
		var omicsValues = this.getModel().getFeature().getOmicsValues();
		var specie = application.getMainView().currentView.getModel().getOrganism();

		if (this.getModel().getFeature().isRelevant() || this.getModel().getFeature().isRelevantAssociation()) {
			$(componentID + " .relevantFeatureField").show();
		} else {
			$(componentID + " .relevantFeatureField").hide();
		}

		/*UPDATE THE HEATMAP AND THE PLOT*/
		var visibleOmics =[];
		var allOmics = null;
		if(featureType.toLowerCase().replace("meta", "") === "gene"){
			allOmics = this.getParent("PA_Step4PathwayView").getGeneBasedInputOmics();
		}else{
			allOmics = this.getParent("PA_Step4PathwayView").getCompoundBasedInputOmics();
		}

		for(var i in allOmics){
			visibleOmics.push(allOmics[i].omicName);
		}

		var divHeight = Math.max(visibleOmics.length * 40, 120);
		$(componentID + " .step4_plotwrappers").html(
			"  <div class='twoOptionsButtonWrapper'>" +
			'      <a href="javascript:void(0)" class="button twoOptionsButton selected" name="heatmap-chart">Heatmap</a>'+
			'      <a href="javascript:void(0)" class="button twoOptionsButton" name="line-chart">Line chart</a>'+
			"  </div>" +
			"  <div class='step4-tooltip-plot-container selected' name='heatmap-chart'>" +
			"    <div id='" + this.getComponent().getId() + "_heatmapcontainer' name='heatmap-chart' style='height:"+ divHeight+ "px;width: 275px;overflow:hidden;overflow-y:auto;padding-right: 15px;'></div>" +
			"  </div>" +
			"  <div class='step4-tooltip-plot-container' name='line-chart' style='display:none;'>" +
			"    <div id='" + this.getComponent().getId() + "_plotcontainer' style='height:"+ divHeight+ "px;width: 275px;'></div>" +
			"  </div>"
		);

		this.generateHeatmap(this.getComponent().getId() + "_heatmapcontainer", visibleOmics, dataDistributionSummaries, visualOptions);
		this.generatePlot(this.getComponent().getId() + "_plotcontainer", visibleOmics, dataDistributionSummaries, visualOptions);

		$("#" + me.getComponent().getId() + " a.twoOptionsButton").click( function(){
			var parent = $(this).parent(".twoOptionsButtonWrapper");
			var target = $(this).attr("name").replace("show", "");
			$(this).siblings("a.twoOptionsButton.selected").removeClass("selected");
			$(this).addClass("selected");
			parent.siblings("div.step4-tooltip-plot-container.selected").removeClass("selected").toggle();
			parent.siblings("div.step4-tooltip-plot-container[name="+ target + "]").addClass("selected").toggle();
		});

		if (hideLinks === true) {
			$(componentID + " .extraInfoPanel").hide();
		}else{
			this.generateExtraInfoPanelContent(componentID + " .extraInfoPanel", specie, componentNames, this.getModel().getFeature().getID(), featureType, callback);
		}
	};
	
	this.showExpandedInfo = function() {
		// doLayout moved at the getJSON callback.
		this.updateObserver(false);
	};
	
	this.hideExpandedInfo = function() {
		this.updateObserver(true);
		this.parent.getComponent().doLayout();
	};

	this.generateExtraInfoPanelContent = function(target, specie, componentNames, featureID, featureType, callback=null) {
		var me = this;
		var renderFunction = function(data){
			var htmlCode = "";

			var featureName = componentNames.shift();
			if(componentNames.length > 0){
				htmlCode +=
				'<p><b>Other names:</b> ' + componentNames.join(", ") + '</p>';
			}

			htmlCode+=
			"<div class='externalLinksContainer'>" +
			"<b>External links</b>" +
			"  <ul style='list-style-type: none;'>";

			var species = data.species;
			var specieName = "";
			for (var i in species) {
				if (species[i].value === specie) {
					specieName = species[i].name;
					break;
				}
			}

			var alternativeName = specieName.split("(")[1];
			alternativeName = alternativeName.substring(0,1).toUpperCase() +  alternativeName.substring(1,alternativeName.length-1);

			if(featureType.toLowerCase() === "gene"){
				htmlCode +=
				"    <li><a href='http://www.kegg.jp/dbget-bin/www_bget?" + specie + ":" + featureID + "' target='_blank'><i class='fa fa-external-link'></i> Search at KEGG Database</a></li>" +
				// Ensembl Genomes retired the /search/eg/<term> path -- it returns 404
				// for every feature, with or without https and regardless of user
				// agent, so this link had stopped working entirely. The current form
				// is taken from the search box on ensemblgenomes.org itself, which
				// posts to /search/ with the parameter named "query".
				"    <li><a class='ensemblGenomesSearch' href='https://www.ensemblgenomes.org/search/?query=" + encodeURIComponent(featureName) + "' target='_blank' rel='noopener'><i class='fa fa-external-link'></i> Search at Ensembl Genomes</a></li>" +
				"    <li><a class='ensemblSearch' href='http://www.ensembl.org/Multi/Search/Results?q=" + encodeURIComponent(featureName) + ";facet_species="+ encodeURIComponent(alternativeName) + "' target='_blank'><i class='fa fa-external-link'></i> Search at Ensembl (vertebrates)</a></li>" +
				((specie === "hsa") ? "<li><a href='http://www.genecards.org/cgi-bin/carddisp.pl?gene=" + featureName + "' target='_blank'><i class='fa fa-external-link'></i> Search at GeneCards Database</a></li>" : "") +
				"    <li><a href='http://www.ncbi.nlm.nih.gov/pubmed/?term=" + specieName + "' target='_blank'><i class='fa fa-external-link'></i> Find related publications (PubMed)</a></li>" +
				"    <li><a href='http://www.ncbi.nlm.nih.gov/gene/?term=" + encodeURIComponent("(" + featureName + "[Gene Name]) AND ()"+ alternativeName + "[Organism])") + "' target='_blank'><i class='fa fa-external-link'></i> Search at NCBI Gene</a></li>" +
				"    <li><a href='http://www.ncbi.nlm.nih.gov/gquery/?term=" + encodeURIComponent(featureName + " "+ specieName) + "' target='_blank'><i class='fa fa-external-link'></i> Search at all NCBI Databases</a></li>";
			}else{
				htmlCode +=
				"    <li><a href='http://www.kegg.jp/dbget-bin/www_bget?" + featureID + "' target='_blank'><i class='fa fa-external-link'></i>Search at KEGG Database</a></li>" +
				"    <li><a href='http://www.ncbi.nlm.nih.gov/pccompound?term=" + featureID + "' target='_blank'><i class='fa fa-external-link'></i>Search at PubChem Compound</a></li>" +
				"    <li><a href='https://www.ebi.ac.uk/chebi/advancedSearchFT.do?searchString=" + featureID + "' target='_blank'><i class='fa fa-external-link'></i>Search at ChEBI Database</a></li>";
			}

			htmlCode+= "  </ul></div>";

			if(me.showButtons === true){
				htmlCode+=
				'<div style=" text-align: center; margin: 15px 0px; ">'+
				'  <a class="button btn-info btn-sm btn-no-float findInMapButton"><i class="fa fa-map-marker"></i> Find in Pathway</a>'+
				'  <a class="button btn-default btn-sm btn-no-float moreDetailsButton"><i class="fa fa-search-plus"></i> Show details</a>'+
				'</div>';
			}

			$(target).html(htmlCode).css({"display" : "inline-block"});

			$(target).find(".findInMapButton").click( function(){
				//Reset the zoom to have a complete view of the diagram
				me.parent.diagramPanel.zoomTool.reset();
				//Iterate through all the featureSets and find those that contain the target feature
				//Note that a feature can be drawn many times in the same diagram
				var matches = [], featureSetView, featureSetElem;
				for(var i in me.parent.diagramPanel.items){
					featureSetView = me.parent.diagramPanel.items[i];
					for(var j in featureSetView.model.features){
						featureSetElem = featureSetView.model.features[j];
						if(featureSetElem.getFeature().getID() === me.model.feature.ID){
							featureSetView.model.setMainFeature(featureSetElem);
							featureSetView.updateObserver();
							matches.push($("#" + featureSetView.getComponent().id)[0]);
						}
					}
				}
				//For each found feature, show a popup indicating the location of the feature				
				$(matches).data('tooltipstercontent', me.model.feature.name.split(",")[0]);
				$(matches).tooltipster({
					side: 'bottom',
					trigger: 'custom',
					functionInit: function(instance, helper){
						var dataContent = $(helper.origin).data('tooltipstercontent');
						instance.content(dataContent);
					}
				});
				
				matches.map(x => $(x).tooltipster('open'));
				setTimeout(function() {
					matches.map(x => $(x).tooltipster('close'));
				}, 1700);
			});

			$(target).find(".moreDetailsButton").click( function(){
				me.parent.showFeatureSetDetails("", me.model.parent);
			});
			
			me.parent.getComponent().doLayout();
			
			// Call callback when completing all rendering.
			if (callback) {
				callback();
			}
		};
		
		this.getParent("PA_Step4JobView").downloadSpeciesInfo(renderFunction);

		return this;
	};

	/**
	* This function generates a HIGHCHART HEATMAP using the given data
	* TODO: DOCUMENTAR
	* @param {type} mainFeatureSetItem
	* @param {type} dataDistributionSummaries
	* @returns {PA_Step4KeggDiagramFeatureSetTooltip.generateHeatmap.heatmap}
	*/
	this.generateHeatmap = function(divID, visibleOmics, dataDistributionSummaries, visualOptions) {
		var feature = this.getModel().getFeature();
		var omicName, omicValues, position;
		var x = 0, y = 0, maxX = -1;
		var series = [], yAxisCat = [], serie, later = [], values, scaledValues, min, max;

		var jobModel = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var replicateMode = jobModel && jobModel.getReplicateMode ? jobModel.getReplicateMode() : "replicates";

		for (var i = visibleOmics.length - 1; i >= 0; i--) {
			x = 0;
			omicName = visibleOmics[i].split("#")[0];

			// Retrieve all omic values
			allOmicValues = feature.getOmicValues(omicName, true);

			if (allOmicValues !== null) {
				allOmicValues.forEach(function(omicValues) {
					x = 0;

					// Per-feature popup is tight on horizontal space, so render just the
					// best display name without the AGI tail:
					// 1. Regulator omic — originalName holds the regulator's symbol
					//    (overridden server-side); use it alone.
					// 2. Regular omic with a resolved symbol — show feature.name (e.g. ASP4).
					// 3. No symbol available — fall back to the user's inputName / AGI.
					var shownameValue;
					if (omicValues.inputName != omicValues.originalName && omicValues.originalName !== undefined) {
						shownameValue = omicValues.originalName;
					} else if (feature.name && feature.name !== omicValues.inputName) {
						shownameValue = feature.name;
					} else {
						shownameValue = omicValues.inputName;
					}
					var relevantSymbols = "";

					if (omicValues.isRelevant(undefined, replicateMode) === true) {
						relevantSymbols += "* ";
					}
					if (omicValues.isRelevantAssociation() === true) {
						relevantSymbols += "^ ";
					}

					serie = {name: relevantSymbols + omicName + "#" + shownameValue};
					yAxisCat.push(relevantSymbols + omicName + "#" + shownameValue);

					values = omicValues.getValues(replicateMode);
					serie.data = [];
					scaledValues = [];

					var limits = getMinMax(dataDistributionSummaries[omicName], visualOptions.colorReferences[omicName]);

					for (var j in values) {
						serie.data.push({
							x: x, y: y,
							value: values[j],
							color: getColor(limits, values[j], visualOptions.colorScale),
							isSignificant: omicValues.isRelevant(j, replicateMode)
						});
						x++;
						maxX = Math.max(maxX, x);
					}
					series.push(serie);
					y++;
				});
			} else {
				/* IF THERE IS NOT DATA FOR THIS OMIC FOR THIS FEATURE, WE WILL ADD
				* A GRAY ROW, BUT FIRST WE NEED SOME INFORMATION (MAX X), SO WE WILL ADD
				* LATER, NOW JUST ADD A NULL, AND REPLACE LATER*/
				later.push({
					omicName: omicName,
					position: y
				});
				yAxisCat.push(omicName);
				series.push(null);
				y++;
			}
		}

		for (var i in later) {
			x = 0;
			omicName = later[i].omicName;
			position = later[i].position;

			serie = {
				name: omicName
			};
			serie.data = [];
			for (var j = 0; j < maxX; j++) {
				serie.data.push([x, position, null]);
				x++;
			}
			series[position] = serie;
		}

		// In samples mode the labels under each cell come from the biological-
		// sample names (omic.sampleHeader) rather than the raw replicate
		// columns, so the tooltip stays consistent with what's drawn.
		var headers = this.getParent("PA_Step4JobView").getModel().getOmicHeaders(null, replicateMode);

		// Real condition names on the x axis. Unlike every other chart in these
		// two files this one stacks a row per omic under a single axis, and
		// omics are uploaded independently, so the names are only used when
		// every visible omic agrees on them - see paSharedOmicHeader().
		var visibleOmicNames = visibleOmics.map(function(entry) { return entry.split("#")[0]; });
		var xAxisConfig = paConditionAxis(maxX, paSharedOmicHeader(paJobModel(this), visibleOmicNames, maxX), {maxChars: 10});
		var xAxisCat = xAxisConfig.categories;

		// Calculate the height based on number of Y elements. The extra 34px is
		// the band the rotated condition labels now occupy: without it they eat
		// the plot area of a chart that is only 80px tall to begin with.
		var chartHeight = Math.max(y * 40, 80) + 34;

		var replaceSymbols = {
			"*": '<i class="relevantFeature"></i>',
			"^": '<i class="relevantAssociationFeature"></i>'
		};

		/* The gap that separates one cell from the next, in the colour of the
		   surface behind this chart. See paCellGap(). */
		var cellGap = paCellGap(divID);

		var heatmap = new Highcharts.Chart({
			chart: {type: 'heatmap',renderTo: divID, height: chartHeight},
			title: null,
			credits: {enabled: false},
			legend: {enabled: false},
			tooltip: {
				borderColor: "#333",
				formatter: function() {
					var title = this.point.series.name.split("#");
					var omicHeader = headers[title[0].replace(/[\*\^]/g, '').trim()] || [];

					// The tooltip is the one place with room for the untruncated
					// name, and it is the fallback for the length-capped axis
					// labels, so nothing is cut here any more.
					if (omicHeader[this.point.index + 1]) {
						title[0] += " [" + omicHeader[this.point.index + 1] + "]";
					}

					title[1] = (title.length > 1) ? title[1] : "";
					return "<b>" + title[0].replace(/[\*\^]/g, function(c) { return replaceSymbols[c]; }) + "</b><br/>" + "<i class='tooltipInputName'>" + title[1] + "</i>" + (this.point.value === null ? "No data" : this.point.value);
				},
				useHTML: true
			},
			xAxis: xAxisConfig,
			yAxis: {
				categories: yAxisCat,
				title: null,
				labels: {
					formatter: function() {
						if (this.value.split) {
							var title = this.value.split("#");
							title[1] = (title.length > 1) ? title[1] : "No data";
							return paRowLabel(title[0], title[1], {width: 70, maxChars: 12});
						}

						return null;
					},
					style: {fontSize: "9px"},
					useHTML: true
				}
			},
			series: series,
			plotOptions: {
				heatmap: {
					// A gap instead of a black grid. This one used to be a
					// translucent white, which was right for the two surfaces this
					// tooltip is drawn on but had to be judged by eye; paCellGap
					// reads the surface instead, so every heatmap in the application
					// separates its cells the same way.
					borderColor: cellGap.borderColor,
					borderWidth: cellGap.borderWidth,
					dataLabels: {
						enabled: true,
						useHTML: true,
						formatter: function() {
							if (this.point.isSignificant && maxX > 1) {
								return '<i class="fa fa-star" style="color: ' +
									paInkOnCell(this.point.color) +
									' !important; font-size: 8px; padding: 0;"></i>';
							}
						}
					}
				}
			}
		});

		return heatmap;
	};

	/**
	* This function generates a HIGHCHART PLOT using the given data
	* TODO: DOCUMENTAR
	* @param {type} divID
	* @param {type} dataDistributionSummaries
	* @param {type} visualOptions
	* @returns {PA_Step4KeggDiagramFeatureSetTooltip.generatePlot.plot}
	*/
	this.generatePlot = function(divID, visibleOmics, dataDistributionSummaries, visualOptions) {
		var feature = this.getModel().getFeature();
		var omicName,
		omicValues = null,
		values = null;
		var series = [],
		scaledValues, min, max,
		maxVal = -100000000,
		minVal = 100000000,
		tmpValue,
		yAxis = [],
		yAxisItem;

		var jobModel = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var replicateMode = jobModel && jobModel.getReplicateMode ? jobModel.getReplicateMode() : "replicates";

		//1.FILL THE STORE DATA [{name:"timepoint 1", "Gene Expression": -0.8, "Proteomics":-1.2,... },{name:"timepoint2", ...}]
		for (var i in visibleOmics) {
			omicName = visibleOmics[i].split("#")[0];
			allOmicValues = feature.getOmicValues(omicName, true);

			if (allOmicValues !== null) {
				for (var t = 0; t < allOmicValues.length; t++) {
					omicValues = allOmicValues[t];
					scaledValues = [];

					var limits = getMinMax(dataDistributionSummaries[omicName], visualOptions.colorReferences[omicName]);
					var showName =  omicValues.originalName !== undefined && omicValues.originalName !== omicValues.inputName ? omicValues.originalName : null;

					values = omicValues.getValues(replicateMode);
					for (var j in values) {
						//SCALE THE VALUE
						tmpValue = scaleValue(values[j], limits.min, limits.max);
						//UPDATE MIN MAX (TO ADJUST THE AXIS)
						maxVal = Math.max(tmpValue, maxVal);
						minVal = Math.min(tmpValue, minVal);
						//ADD THE VALUE (CUSTOM MARKER IF OUTLIER)
						scaledValues.push({
							y: tmpValue,
							marker: ((tmpValue > 1 || tmpValue < -1) ? {
								fillColor: '#ff6e00'
							} : null)
						});
					}

					series.push({
						name: allOmicValues.length > 1 ? omicName + " [" + (showName ? showName : omicValues.inputName + ' - ' + (t + 1)) + "]" : omicName,
						type: 'spline',
						startOnTick: false,
						endOnTick: false,
						data: scaledValues,
						yAxis: 0
					});				
				};
			}
		}

		maxVal = Math.ceil(Math.max(maxVal, 1));
		minVal = Math.floor(Math.min(minVal, -1));

		//TODO: SHOW ORINAL VALUES WHEN HOVERING
		var plot = new Highcharts.Chart({
			chart: {renderTo: divID},
			title: null,
			credits: {enabled: false},
			xAxis: [{labels: {enabled: false}}],
			yAxis: {
				title: null,
				min: minVal,
				max: maxVal,
				plotLines: [
					{label: {text: '-1',align: 'right', style: {color: 'gray'}},color: '#dedede',value: -1,width: 1},
					{label: {text: '0',align: 'right', style: {color: 'gray'}},color: '#dedede',value: 0,width: 1},
					{label: {text: '1',align: 'right', style: {color: 'gray'}},color: '#dedede',value: 1,width: 1}
				]},
				series: series,
				legend: {
					itemStyle: {fontSize: "9px",fontWeight: 'lighter'},
					margin: 5,
					padding: 5
				},
				tooltip: {enabled: false},

			}
		);

		plot.yAxis[0].setExtremes(minVal, maxVal);

		return plot;
	};

	/**
	* This function generates the component (EXTJS) using the content of the model
	* @returns {Ext.ComponentView} The visual component
	*/
	this.initComponent = function() {
		var me = this;
		this.component = Ext.widget({
			xtype: "box",
			cls: "contentbox mainInfoPanel neverExpanded",
			style:"margin:0;",
			html:	
			((this.collapsible)?"<h3 class='geneInfoTitle'><i class='fa fa-chevron-circle-right'></i>" +
			"  <span class='featureNameLabel'></span><i class='featureNameLabelRelevant relevantFeature'></i>"+
			"</h3>": "") +
			"<div class='geneInfoContainer' style='display:none;'>" +
			"  <div class='otherOmicsLabel' style='padding:2px 0px'></div>" +
			"  <div class='step4_plotwrappers'></div>" +
			"  <span><p class='relevantFeatureField' style='padding: 0px; margin: 0px; font-size: 10px;float: right;'><i class='relevantFeature'></i>  Relevant for this omic</p></span>" +
			"  <div class='extraInfoPanel'></div>"+
			"</div>",
			listeners: {
				boxready: function () {
					//ADD THE EVENT WHEN CLICK ON THE EXPAND LINK
					$(this.el.dom).find(".geneInfoTitle").click(function () {
						var elem = $(this);
						if(elem.parents(".mainInfoPanel").first().hasClass("neverExpanded")){
							elem.parents(".mainInfoPanel").first().removeClass("neverExpanded");
							me.updateObserver();
						}
						if (elem.hasClass("expanded")) {
							elem.removeClass("expanded");
							elem.find("i").removeClass("fa-chevron-circle-down").addClass("fa-chevron-circle-right");
						} else {
							elem.addClass("expanded");
							elem.find("i").removeClass("fa-chevron-circle-right").addClass("fa-chevron-circle-down");
						}
						$(this).siblings(".geneInfoContainer").toggle();
					});
					$(this.el.dom).find(".hideOption").click(function () {
						if(me.parent.hide !== undefined){
							me.parent.hide(true);
						}
					});
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
PA_Step4KeggDiagramFeatureView.prototype = new View();

function PA_Step4KeggDiagramFeatureSetSVGBox() {
	/**
	* About this view: This view creates a new box (heatmap) for a given FeatureSet
	* The new box will be drawn at the Pathway diagram.
	*/
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4KeggDiagramFeatureSetSVGBox";
	this.imageCode = null;
	this.componentID = null;
	this.isUnique = true;
	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.getID = function() {
		console.warn("Calling to deprecated getID method");
		return this.getComponentID();
	};
	this.getComponentID = function() {
		return this.componentID;
	};
	this.setComponentID = function(componentID) {
		this.componentID = (componentID + "_" + this.model.getFeature().getID()).replace(/\s+/g, '_');
		return this;
	};
	this.setIsUnique= function(isUnique) {
		this.isUnique = isUnique;
		return this;
	};

	/***********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	this.updateObserver = function() {
		var dataDistributionSummaries = this.getParent("PA_Step4PathwayView").getDataDistributionSummaries();
		var visualOptions = this.getParent("PA_Step4PathwayView").getVisualOptions();
		$("#" + this.getComponentID()).attr("href", this.generateBox(dataDistributionSummaries, visualOptions));
		return this;
	};

	this.drawComponent = function(dataDistributionSummaries, visualOptions) {
		return this.initComponent(dataDistributionSummaries, visualOptions);
	};

	this.generatePoint = function(dataDistributionSummaries, visualOptions, pointSize) {
		var canvas = $('<canvas>');
		canvas.attr({width: pointSize,height: pointSize});

		var context = canvas[0].getContext("2d");
		var centerX = canvas[0].width / 2;
		var centerY = canvas[0].height / 2;
		var radius = pointSize/2;

		var isRelevant = this.getModel().getFeature().isRelevant();

		context.beginPath();
		context.arc(centerX, centerY, radius, 0, 2 * Math.PI, false);
		context.fillStyle = '#337ab7';
		context.fill();
		context.lineWidth = 1;
		context.strokeStyle = '#bcbcbc';
		context.stroke();

		if (isRelevant === true) {
			context.beginPath();
			context.arc(centerX, centerY, 6, 0, 2 * Math.PI, false);
			context.fillStyle = 'red';
			context.fill();
			context.lineWidth = 5;
			context.strokeStyle = '#003300';
			context.stroke();
			context.font = "normal " + pointSize/4 + "px FontAwesome";
			context.fillStyle = '#FFFFFF';
			context.fillText('\uf005', centerX - (pointSize/8), centerY - (pointSize/8));
		}

		this.imageCode = canvas[0].toDataURL("image/png");
		return this.imageCode;
	};

	this.generateBox = function(dataDistributionSummaries, visualOptions) {
		var scaleFactor = 10;
		var boxPadding = 1;
		var boxProportion = 1.0;

		var feature = this.getModel().getFeature();
		var featureGraphicalData = this.getModel().getFeatureGraphicalData();
		var boxTitle = featureGraphicalData.getBoxTitle() != undefined ? featureGraphicalData.getBoxTitle() : feature.getName();
		var isRelevant = feature.isRelevant();
		var isRelevantAssociation = feature.isRelevantAssociation();

		// Replicate-display mode: when the user has applied a sample mapping
		// in Step 2 and toggled "Show samples" in the visual options, every
		// `getValues` lookup below collapses to per-sample means.
		var jobModel = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var replicateMode = jobModel && jobModel.getReplicateMode ? jobModel.getReplicateMode() : "replicates";

		/*FILTER THE LIST OF OMICS TO GET ONLY THE "GENE" BASED OMICS OR THE COMPOUND BASED OMICS*/
		var visibleOmics = visualOptions.visibleOmics.filter(function(elem) {
			return elem.indexOf(feature.getFeatureType().toLowerCase().replace("meta", "") + "based") > -1;
		});

		/* Whether this box is a metagene, read off the same featureType the
		   filter above already strips "meta" from -- "metagene"/"metacompound"
		   against "gene"/"compound".

		   Deliberately NOT SimpleOmicValue.isMetagene(). That name is a boolean
		   initialised to false, then overwritten by a method of the same name,
		   which setMetagene() then overwrites again with a boolean. So it is a
		   function on ordinary values -- and calling it returns the function
		   itself, which is truthy -- and a boolean on the metagenes it is
		   supposed to identify, where calling it throws. Nothing calls it today
		   and this does not start. */
		var isMetageneFeature = /^meta/i.test(feature.getFeatureType() || "");
		var setForMetagenes = (isMetageneFeature && this.getModel().getParent)
			? this.getModel().getParent() : null;

		boxPadding = 18;

		//GET THE WIDTH AND THE HEIGHT
		var width = (featureGraphicalData.getBoxWidth()  || 10 ) * scaleFactor;
		var height = (featureGraphicalData.getBoxHeight() || 10 ) * scaleFactor;

		var boxHeigth = (((height - boxPadding * 2) / visibleOmics.length) * boxProportion);
		var boxWidth = width - boxPadding * 2;
		var xPos = boxPadding,
		yPos = boxPadding;
		var canvas = $('<canvas>');

		//ADD 1 TO AVOID HIDDE WHEN OVERFLOW
		canvas.attr({width: width,height: height});
		var context = canvas[0].getContext("2d");

		var omicName,
		omicValues = null,
		values = null;

		//FOR EACH SELECTED OMIC
		for (var i in visibleOmics) {
			var baseBoxWidth = width - boxPadding * 2;
			omicName = visibleOmics[i].split("#")[0];
			omicValues = feature.getOmicValues(omicName);
			//IF THE FEATURE CONTAINS VALUES FOR THE OMIC
			if (omicValues !== null) {
				values = omicValues.getValues(replicateMode);
				
				// Calculate adaptive width
				var minSegmentWidth = 10 * scaleFactor;
				var currentSegmentWidth = baseBoxWidth / values.length;
				
				if (currentSegmentWidth < minSegmentWidth) {
					// We need to expand the canvas or at least the box drawing area
					// For now, we adjust the segment width and the total width used
					currentSegmentWidth = minSegmentWidth;
					// Note: expanding the canvas itself might be tricky due to coordinates, 
					// but we'll use the fixed segment width.
				}
				
				var boxWidth = currentSegmentWidth;

				/* A metagene box is not measuring the omic, so it must not be
				   coloured against the omic's distribution.

				   A metagene is a trend -- a component summarising how a whole
				   cluster of features moves -- and it is centred on zero, so it
				   goes negative whatever the omic did. Measured on
				   paintomics.uv.es: the Proteomics omic runs 0.79..1.41 while
				   its metagenes sit at -0.02, -0.046, -0.0037. Against the
				   omic's p10/p90 window (0.986..1.042) every one of those is
				   off the bottom of the scale, and the box came out
				   rgb(255, 255,127) -- a flat yellow that means nothing, on a
				   value that is very close to no change at all.

				   Their own range crosses zero, so paColourRange returns a
				   symmetric one and blue/white/red reads the way it should.
				   Taken across the metagenes of this feature set rather than
				   this box alone, so sibling boxes stay comparable with each
				   other and with the trend heatmap in the details panel, which
				   scales the same way. */
				var limits;
				if (isMetageneFeature) {
					limits = paMetageneLimits(
						(setForMetagenes ? (setForMetagenes.getMetagenes() || []) : [])
							.map(function (metageneElem) {
								var metageneValues = metageneElem.getFeature().getOmicValues(omicName);
								return {values: metageneValues
									? metageneValues.getValues(replicateMode) : []};
							}));
					/* A set that reports no metagenes cannot produce a range;
					   fall back to this box's own values rather than to the
					   omic's, which is the thing being corrected. */
					if (limits.min === limits.max) {
						limits = paMetageneLimits([{values: values}]);
					}
				} else {
					limits = getMinMax(dataDistributionSummaries[omicName], visualOptions.colorReferences[omicName]);
				}

				for (var j in values) {
					context.beginPath();
					context.rect(xPos, yPos, boxWidth, boxHeigth);
					context.fillStyle = getColor(limits, values[j], visualOptions.colorScale);
					context.fill();
					context.lineWidth = 1;
					context.strokeStyle = '#bcbcbc';
					context.stroke();
					
					/* REMOVED: Significance stars are now only shown in the detailed heatmap */
					/*
					if (omicValues.isRelevant(j)) {
						context.font = "normal " + (boxHeigth * 0.6) + "px FontAwesome";
						context.fillStyle = 'white';
						context.textAlign = "center";
						context.textBaseline = "middle";
						context.fillText('\uf005', xPos + (boxWidth / 2), yPos + (boxHeigth / 2));
						context.textAlign = "start";
						context.textBaseline = "alphabetic";
					}
					*/
					
					xPos += boxWidth;
				}
				//IF THE FEATURE DOES NOT CONTAIN VALUES, DRAW A GRAY BOX
			} else {
				context.beginPath();
				context.rect(xPos, yPos, width, boxHeigth);
				context.fillStyle = "#f9f9f9";
				context.fill();
			}
			yPos += boxHeigth;
			xPos = boxPadding;
		}
		//ADD THE BOX WITH THE TEXT
		var fontSize = 13;
		if (boxTitle.length > 6) {
			fontSize = 10;
		}

		context.beginPath();
		context.rect(boxPadding / 2, boxPadding / 2, width - boxPadding, height - boxPadding);
		context.lineWidth = boxPadding;
		context.strokeStyle = '#e8e8e8';

		if (visibleOmics.length === 0) {
			context.fillStyle = "#f9f9f9";
			context.fill();
		}

		if (isRelevant === true || isRelevantAssociation === true) {
			context.strokeStyle = '#000';
		}

		if(width > 80){
			context.stroke();
			context.font = "normal " + (fontSize * scaleFactor) + "px serif";
			context.fillStyle = 'black';
			// TODO: remove added space?
			context.fillText(' ' + boxTitle, 0, fontSize * scaleFactor);
		}

		//Add start glyph if relevant
		if (isRelevant === true) {
			context.beginPath();
			context.arc(xPos + width - 40, 25, 25, 0, 2 * Math.PI, false);
			context.fillStyle = 'red';
			context.fill();
			context.lineWidth = 5;
			context.strokeStyle = '#003300';
			context.stroke();
			context.font = "normal 35px FontAwesome";
			context.fillStyle = '#FFFFFF';
			context.fillText('\uf005', xPos + width - 57, 37);
		}
		if (isRelevantAssociation === true) {
			context.beginPath();
			context.arc(xPos + 30, 25, 25, 0, 2 * Math.PI, false);
			context.fillStyle = '#f4c800';
			context.fill();
			context.lineWidth = 5;
			context.strokeStyle = '#003300';
			context.stroke();
			context.font = "normal 35px FontAwesome";
			context.fillStyle = '#FFFFFF';
			context.fillText('\uf005', xPos + 12, 37);
		}
		//Add "more" glyph if not unique
		if (!this.isUnique) {
			context.beginPath();
			context.arc(xPos + width - 40, yPos-10, 25, 0, 2 * Math.PI, false);
			context.fillStyle = 'green';
			context.fill();
			context.lineWidth = 5;
			context.strokeStyle = '#003300';
			context.stroke();
			context.font = "normal 35px FontAwesome";
			context.fillStyle = '#FFFFFF';
			context.fillText('\uf067', xPos + width - 52, yPos+5);

		}
		this.imageCode = canvas[0].toDataURL("image/png");
		return this.imageCode;
	};

	this.getPopUpInformation = function(visualOptions) {
		var omicsValues = {};
		var feature = this.getModel().getFeature();
		var featureGraphicalData = this.getModel().getFeatureGraphicalData();
		var visibleOmics = visualOptions.visibleOmics.filter(function(elem) {
			return elem.indexOf(feature.getFeatureType().toLowerCase() + "based") > -1;
		});

		var jobModel = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var replicateMode = jobModel && jobModel.getReplicateMode ? jobModel.getReplicateMode() : "replicates";

		var omicName, omicValues;
		for (var i in visibleOmics) {
			omicName = visibleOmics[i].split("#")[0];
			omicValues = feature.getOmicValues(omicName);

			if (omicValues == null) {
				omicValues = "No data";
			} else {
				omicValues = omicValues.getValues(replicateMode);
			}
			omicsValues[omicName] = omicValues;
		}
		var width = this.getModel().getFeatureGraphicalData().getBoxWidth() * visualOptions.adjustFactor;
		var height = this.getModel().getFeatureGraphicalData().getBoxHeight() * visualOptions.adjustFactor;

		return {
			name: feature.getName(),
			values: omicsValues,
			x: featureGraphicalData.getX() * visualOptions.adjustFactor + width / 2,
			y: featureGraphicalData.getY() * visualOptions.adjustFactor + height / 2
		};

	};

	/**
	* This function generates the component (JavaScript Object) using the content of the model
	* @returns {Object} The visual component
	*/
	this.initComponent = function(dataDistributionSummaries, visualOptions) {
		var me = this;

		var width = (this.getModel().getFeatureGraphicalData().getBoxWidth() * visualOptions.adjustFactor || 20);
		var height = (this.getModel().getFeatureGraphicalData().getBoxHeight() * visualOptions.adjustFactor || 20);

		/* LEGACY CODE IN CASE WE WANT TO RESTORE POINT "BOXES" FOR OTHER DBS */
		if (width == 0 || height == 0) {
			var pointSize = 15;

			this.component = {
				id: me.componentID,
				type: "image",
				src: this.generatePoint(dataDistributionSummaries, visualOptions, pointSize),
				width: pointSize,
				height: pointSize,
				x: ((this.getModel().getFeatureGraphicalData().getX() * visualOptions.adjustFactor - pointSize / 2) || 0),
				y: ((this.getModel().getFeatureGraphicalData().getY() * visualOptions.adjustFactor - pointSize / 2)  || 0),
			};
		} else {
			this.component = {
				id: me.componentID,
				type: "image",
				src: this.generateBox(dataDistributionSummaries, visualOptions),
				width: width,
				height: height,
				x: ((this.getModel().getFeatureGraphicalData().getX() * visualOptions.adjustFactor - width / 2) || 0),
				y: ((this.getModel().getFeatureGraphicalData().getY() * visualOptions.adjustFactor - height / 2)  || 0),
			};
		}

		return this.component;
	};
	return this;
}
PA_Step4KeggDiagramFeatureSetSVGBox.prototype = new View();

//------------------------------------------------------------------------------------------------

function PA_Step4VisualOptionsView() {
	/**
	* About this view: this view (PA_Step4VisualOptionsView) is used to change
	* the visual options that affect  to STEP4 Views (color scale,
	* color references, etc.)
	*/
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4VisualOptionsView";

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* This function changes the visibility for the component.
	* @chainable
	* @param {boolean} visible, forces the component visibility
	* @return {PA_Step4VisualOptionsView} the view
	*/
	this.toggle = function(visible) {
		visible = ((visible===undefined)? ! this.getComponent().isVisible():visible);
		this.getComponent().setVisible(visible);
		return this;
	};

	/**
	* This function apply the settings that user can change
	* for the visual representation of the model (w/o reload everything).
	* - STEP 1. UPDATE THE visibleOmics OPTION
	* - STEP 2. UPDATE THE colorReferences OPTION
	* - STEP 3. UPDATE THE colorReferences OPTION
	* - STEP 4. NOTIFY THE CHANGES TO PARENT
	* @chainable
	* @returns {PA_Step4VisualOptionsView}
	*/
	this.applyVisualSettings = function() {
		/********************************************************/
		/* STEP 1. UPDATE THE visibleOmics OPTION               */
		/********************************************************/
		var selectedOptions = [];
		$("div.lateralOptionsSelector.omicSelector input:checked").each(function () {
			selectedOptions.push($(this).attr("id"));
		});
		this.getParent().setVisualOptions("visibleOmics" , selectedOptions);

		/********************************************************/
		/* STEP 2. UPDATE THE colorReferences OPTION               */
		/********************************************************/
		selectedOptions = {};
		$("div.lateralOptionsSelector input[name^=colorByCheckbox]:checked").each(function(index) {
			var omicName = $(this).attr("name").split(/_(.+)/)[1];

			selectedOptions[omicName] = $(this).val()
		});
		this.getParent().setVisualOptions("colorReferences" , selectedOptions);

		/********************************************************/
		/* STEP 3. UPDATE THE colorScale OPTION            */
		/********************************************************/
		selectedOptions = $("div.lateralOptionsSelector input[name=colorScaleCheckbox]:checked").first().val();
		this.getParent().setVisualOptions("colorScale" , selectedOptions);

		/********************************************************/
		/* STEP 3b. UPDATE THE REPLICATE DISPLAY MODE          */
		/* (only present when the toggle was rendered)         */
		/********************************************************/
		var $replicateRadio = $("div.lateralOptionsSelector input[name=replicateModeCheckbox]:checked").first();
		if ($replicateRadio.length) {
			var jobModel = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
			if (jobModel && jobModel.setReplicateMode) {
				jobModel.setReplicateMode($replicateRadio.val());
			}
		}

		/********************************************************/
		/* STEP 4. NOTIFY THE CHANGES TO PARENT                 */
		/********************************************************/
		this.getParent().applyVisualSettings();

		return this;
	};

	/**
	* This function generates the component (EXTJS) using the content of the model
	* @returns {Ext.ComponentView} The visual component
	*/
	this.initComponent = function() {
		var me = this;
		var visualOptions = this.getParent().getVisualOptions();

		/********************************************************/
		/* STEP 1. GENERATE THE "CHOOSE OMICS TO DRAW" SECTION  */
		/********************************************************/
		var windowContent =
		'<h4>Choose the omics to draw</h4>'+
		'<div class="lateralOptionsSelector omicSelector">'+
		'  <h5>Gene based omics</h5>';

		// `viewbox` draws these as an eye rather than a tick: main.css swaps the
		// glyph to eye-slash in red when off and eye in green when on. The variant
		// has been in the stylesheet since 2021 and had never been put on anything.
		//
		// It belongs here rather than anywhere else. This section is headed "Choose
		// the omics to draw" and each box binds to visualOptions.visibleOmics, so
		// what it toggles is whether an omic is drawn - which is what an eye says
		// and a tick does not. The product already reads eye/eye-slash that way in
		// three other places: the network cluster toggles, the alternative-compound
		// Show/Hide, and the My Data actions.
		var omicsAux = me.getParent().getGeneBasedInputOmics();
		for (var i in omicsAux) {
			windowContent +=
			' <div class="checkbox viewbox">'+
			'   <input ' + ((visualOptions.visibleOmics.indexOf(omicsAux[i].omicName + "#genebased") > -1) ? "checked" : "") + ' type="checkbox" id="' + omicsAux[i].omicName + '#genebased">'+
			'   <label for="' + omicsAux[i].omicName + '#genebased">' + omicsAux[i].omicName + '</label>'+
			' </div>';
		}

		var omicsCompounds = me.getParent().getCompoundBasedInputOmics();
		windowContent += '<h5>Compound based omics</h5>';
		for (var i in omicsCompounds) {
			windowContent +=
			' <div class="checkbox viewbox">'+
			'  <input ' + ((visualOptions.visibleOmics.indexOf(omicsCompounds[i].omicName + "#compoundbased") > -1) ? "checked" : "") + ' type="checkbox" id="' + omicsCompounds[i].omicName + '#compoundbased' + '">'+
			'  <label for="' + omicsCompounds[i].omicName + '#compoundbased">' + omicsCompounds[i].omicName + '</label>'+
			' </div>';
		}

		/********************************************************/
		/* STEP 1. GENERATE THE "COLOR BY" SECTION  */
		/********************************************************/
		windowContent +=
		'</div>' + //CLOSE "CHOOSE OMICS TO DRAW" SECTION
		'<div class="lateralOptionsSelector">' +
		'  <h4>Coloring options</h4>' +
		'  <h5>Reference values</h5>' +
		'  <div>';

		/* Add a fieldset for each omic */
		 $.each(omicsAux.concat(omicsCompounds), function(index, omicObject) {
			 var omic = omicObject.omicName;

			 windowContent +=
			 '<fieldset>' +
			 '		<legend>' + omic + '</legend>' +
			 '    <div class="radio"><input '+ ((visualOptions.colorReferences[omic] ==="p10p90")?"checked ":"") +'type="radio" id="colorByCheckbox1_' + omic + '" name="colorByCheckbox_' + omic + '" value="p10p90"><label for="colorByCheckbox1_' + omic + '">Percentiles 10 and 90</label></div>' +
			 '    <div class="radio"><input '+ ((visualOptions.colorReferences[omic] ==="absoluteMinMax")?"checked ":"") +'type="radio" id="colorByCheckbox2_' + omic + '" name="colorByCheckbox_' + omic + '" value="absoluteMinMax"><label for="colorByCheckbox2_' + omic + '">Global Min/Max (including outliers).</label></div>' +
			 '    <div class="radio"><input '+ ((visualOptions.colorReferences[omic] ==="riMinMax")?"checked ":"") +'type="radio" id="colorByCheckbox3_' + omic + '" name="colorByCheckbox_' + omic + '" value="riMinMax"><label for="colorByCheckbox3_' + omic + '">Global Min/Max (without outliers).</label></div>' +
			 '    <div class="radio"><input '+ ((visualOptions.colorReferences[omic] ==="custom")?"checked ":"") +'type="radio" id="colorByCheckbox4_' + omic + '" name="colorByCheckbox_' + omic + '" value="custom"><label for="colorByCheckbox4_' + omic + '">Custom values</label></div>' +
			 '	  <div class="radio" id="colorByCheckbox5_' + omic + '"></div>' +
			 '</fieldset>';
		 });

		//'    <div class="radio"><input type="radio" id="colorByCheckbox4" name="colorByCheckbox" value="localMinMax"><label for="colorByCheckbox4">Local Min/Max (for current pathway).</label></div>' +
		windowContent +=
		'  </div>' +
		'  <h5>Color scale</h5>' +
		'  <div>' +
		// Swatches are sampled from getColor() rather than loaded as pictures of
		// it - see paScaleThumb(). The first option is named for what it now
		// paints: its midpoint is a light neutral, not white, which is what lets
		// the heatmaps drop their black cell grid.
		'    <div class="radio">' + paScaleThumb("bwr") + '<input '+ ((visualOptions.colorScale ==="bwr")?"checked ":"") +'type="radio" id="colorScaleCheckbox1" name="colorScaleCheckbox" value="bwr"><label for="colorScaleCheckbox1">Blue-Grey-Red</label></div>' +
		'    <div class="radio">' + paScaleThumb("rbg") + '<input '+ ((visualOptions.colorScale ==="rbg")?"checked ":"") +'type="radio" id="colorScaleCheckbox3" name="colorScaleCheckbox" value="rbg"><label for="colorScaleCheckbox3">Green-Black-Red</label></div>' +
		//'    <div class="radio"><input type="radio" id="colorScaleCheckbox2" name="colorScaleCheckbox" value="bwr2"><label for="colorScaleCheckbox2">Blue-White-Red (alt.)<img class="colorScaleThumb" src="resources/images/bwr2scale_120x18.jpg"></label></div>' +
		'  </div>';

		// Replicate-display toggle. Only shown when at least one omic has had
		// a sample mapping applied — otherwise there's nothing to collapse.
		var step4JobModel = me.getParent("PA_Step4JobView") ? me.getParent("PA_Step4JobView").getModel() : null;
		if (step4JobModel && step4JobModel.hasAnyReplicateAggregation && step4JobModel.hasAnyReplicateAggregation()) {
			var currentMode = step4JobModel.getReplicateMode();
			windowContent +=
			'  <h5>Replicate display ' +
			'    <span class="helpTip" title="Switch between showing every replicate column individually or showing one cell per biological sample (mean across replicates). Configured per-omic in the Step-2 panel."></span>' +
			'  </h5>' +
			'  <div>' +
			'    <div class="radio"><input ' + ((currentMode === "samples") ? "" : "checked ") + 'type="radio" id="replicateModeCheckbox1" name="replicateModeCheckbox" value="replicates"><label for="replicateModeCheckbox1">Show all replicates</label></div>' +
			'    <div class="radio"><input ' + ((currentMode === "samples") ? "checked " : "") + 'type="radio" id="replicateModeCheckbox2" name="replicateModeCheckbox" value="samples"><label for="replicateModeCheckbox2">Show samples (averaged)</label></div>' +
			'  </div>';
		}

		windowContent += '</div>'; //advanceOptionsPanel

		this.component = Ext.widget({
			// paSettingsPanel: a column of grouped controls, so main.css tracks
			// its h4s out as group labels. The Pathway information panel further
			// down this file uses the same class *without* it, because its h4s
			// are the pathway's name and the omic's.
			xtype: "container", cls: "lateralOptionsPanel paSettingsPanel",  width: 300, height: ($("#mainViewCenterPanel").height() - 100),
			items:[{
				xtype: "box",
				html:
				"<div class='lateralOptionsPanel-header' data-guides='ignore'>" +
				'  <div class="lateralOptionsPanel-toolbar">' +
				'    <a href="javascript:void(0)" class="toolbarOption btn-danger helpTip" id="hideVisualSettingsPanelButton" title="Close this panel"><i class="fa fa-times"></i></a>' +
				'  </div>' +
				"  <h2>Visual settings</h2>" +
				"</div>" +
				"<div class='lateralOptionsPanel-body'>" +
				windowContent + '    <a href="javascript:void(0)" class="button btn-success helpTip" id="applyVisualSettingsButton" style="margin-top: 20px;margin-bottom: 20px;" title="Apply changes"><i class="fa fa-check"></i> Apply</a>' +
				"</div>",
				listeners: {
					boxready: function() {
						//SOME EVENT HANDLERS
						$("#hideVisualSettingsPanelButton").click(function() {
							me.toggle(false);
						});
						$("#applyVisualSettingsButton").click(function() {
							me.applyVisualSettings();
						});

						// CREATE CUSTOM SLIDERS FOR EACH OMIC
						var PA4View = me.getParent("PA_Step4PathwayView");
						var omicDistributions = PA4View.getDataDistributionSummaries();

						$.each(omicsAux.concat(omicsCompounds), function(index, omicObject) {
							 var omic = omicObject.omicName;
							 var omicValues = getMinMax(omicDistributions[omic], "absoluteMinMax");
							 var defaultOmicValues = [omicValues.min, omicValues.max];
							 var customOmicValues = (PA4View.visualOptions.hasOwnProperty("customValues") ?
							 (PA4View.visualOptions.customValues[omic] || defaultOmicValues) : defaultOmicValues );

							 var customSlider = Ext.create('Ext.slider.MultiCustom', {
						        renderTo: "colorByCheckbox5_" + omic,
										name: "customslider_" + omic,
						        width: 240,
						        minValue: omicValues.min,
						        maxValue: omicValues.max,
										customValues: [customOmicValues[0], customOmicValues[1]],
										disabled: ($('input[type=radio][name="colorByCheckbox_' + omic + '"]:checked').val() !== "custom"),
						   	});

								$('input[type=radio][name="colorByCheckbox_' + omic + '"]').change(function() {
									 if (this.value !== "custom") {
										 customSlider.disable();
									 }	else {
										 customSlider.enable();
									 }
								 });
							});
					},
					resize: function( view, width, height, oldWidth, oldHeight, eOpts ){
						var componentHeight = $(view.getEl().dom).outerHeight();
						var headerHeight = $(view.getEl().dom).find(".lateralOptionsPanel-header").outerHeight() + 10;
						$(view.getEl().dom).find(".lateralOptionsPanel-body").height($("#mainViewCenterPanel").height() - headerHeight - 100);
					},
					beforedestroy: function() {
						me.getModel().deleteObserver(me);
					}
				}
			}]
		});
		return this.component;
	};

	return this;
}
PA_Step4VisualOptionsView.prototype = new View();

function PA_Step4FindFeaturesView() {
	/**
	* About this view: This view displays a summary for the the current pathway
	* and a search bar. When users search for a certain text, the view is updated
	* showing the results for the search.
	*/
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4FindFeaturesView";
	this.items = null;
	this.pathwayDetailsView =null;
	this.searchResultsView =null;

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* This function changes the visibility for the component.
	* @chainable
	* @param {boolean} visible, forces the component visibility
	* @return {PA_Step4FindFeaturesView} the view
	*/
	this.toggle = function(visible) {
		visible = ((visible===undefined)? ! this.getComponent().isVisible():visible);
		this.getComponent().setVisible(visible);
		return this;
	};

	/**
	* This function finds all the features whose name matches to a given string
	* @chainable
	* @param {String} searchValue, the query text
	* @return {PA_Step4FindFeaturesView} the view
	*/
	this.searchFeatures = function(searchValue) {
		var availableTags = this.getParent().searchFeatureIndex || {};

		var results = {}, elemAux;
		for (var i in availableTags) {
			if (i.toLowerCase().indexOf(searchValue.toLowerCase()) !== -1) {
				elemAux = availableTags[i];
				var name = (elemAux.getFeature() && elemAux.getFeature().getName()) ? elemAux.getFeature().getName() : i;
				results[name] = elemAux;
			}
		}

		availableTags = Object.keys(results).sort();

		this.items = [];
		var itemAux;
		for (i in availableTags) {
			this.items.push(new PA_Step4KeggDiagramFeatureView(true).loadModel(results[availableTags[i]]).setParent(this.getParent()));
		}

		this.updateObserver();

		return this;
	};

	/**
	* TODO
	*/
	this.updateObserver = function(){
		var el = $(this.getComponent().el.dom);
		if(this.searchResultsView === null){
			this.searchResultsView = Ext.widget({xtype: 'container', renderTo: el.find(".resultsContainer")[0], items: []});
		}

		el.find(".resultsCounter").text("Found " + this.items.length + " features.");
		el.find(".searchResultsWrapper").show();

		this.searchResultsView.removeAll();
		var components = [];
		for(var i in this.items){
			components.push(this.items[i].getComponent());
		}
		this.searchResultsView.add(components);

		this.searchResultsView.setVisible(true);

		for(i in this.items){
			this.items[i].updateObserver(false, function() {
				el.find(".resultsContainer .findInMapButton").click();
			});
		}
	};

	/**
	* This function shows the detailed view for selected pathway.
	* First, creates a new view of the type PA_Step3PathwayDetailsViews and
	* then load the model.
	* @chainable
	* @return {PA_Step4FindFeaturesView} the view
	*/
	this.showPathwayDetails = function(){
		var el = $(this.getComponent().el.dom);
		if(this.pathwayDetailsView === null){
			this.pathwayDetailsView = new PA_Step3PathwayDetailsView();
			this.pathwayDetailsView.getComponent(el.find(".patwaysDetailsContainer")[0]);
			this.pathwayDetailsView.setParent(this);
		}

		var pathwayView = this.getParent("PA_Step4PathwayView");
		var jobView = this.getParent("PA_Step4JobView");
		
		if (!pathwayView || !jobView) {
			console.warn("Could not find pathway or job view for details view.");
			return this;
		}

		this.pathwayDetailsView.loadModel(pathwayView.getModel());

		var omicNames = [];
		var inputOmics = jobView.getModel().getGeneBasedInputOmics();
		for(var i in inputOmics){
			omicNames.push(inputOmics[i].omicName);
		}
		this.pathwayDetailsView.updateObserver(omicNames, jobView.getModel().getDataDistributionSummaries(), pathwayView.getVisualOptions());

		return this;
	};


	/**
	* Moved, as the TODO that stood here since 2021 asked: the palette is in
	* Util.js and Step 3 reads the same one. This copy had kept the pre-2022 list,
	* so a cluster changed colour between the two views - see getClusterColor
	* there for what that looked like.
	* @param  {String} cluster the cluster number
	* @returns	{String} the hexadecimal color code
	*/
	this.getClusterColor= function(cluster){
		return getClusterColor(cluster);
	};

	/**
	* This function generates the component (EXTJS) using the content of the model
	* @returns {Ext.ComponentView} The visual component
	*/
	this.initComponent = function() {
		var me = this, selected, omicsAux;

		this.component = Ext.widget({
			xtype: "container", cls: "lateralOptionsPanel",  width: 300, height: ($("#mainViewCenterPanel").height() - 100),
			items:[{
				xtype: "box", html:
				/* The teal that used to be inline here is gone. main.css styles
				   .lateralOptionsPanel-header as a white band with a hairline
				   under it, and the pathway card 300px to the left uses that
				   same class and got it - so the two panels either side of the
				   screen wore the same class and did not match. An inline style
				   is unreachable from any stylesheet, which is also why neither
				   the light sheet nor dark.css could correct it: in dark mode
				   this header stayed teal while everything around it moved. */
				"<div class='lateralOptionsPanel-header' data-guides='ignore'>" +
				'  <div class="lateralOptionsPanel-toolbar">' +
				'    <a href="javascript:void(0)" class="toolbarOption btn-info helpTip" id="hideFindFeaturePanelButton" title="Close this panel"><i class="fa fa-times"></i></a>' +
				'  </div>' +
				"  <h2>Pathway information</h2>" +
				"</div>" +
				"<div class='lateralOptionsPanel-body findFeaturesContainer'>" +
				'  <div>'+
				'    <h4>Search in this pathway</h4>' +
				/* One flex row (.findFeaturesRow, main.css) instead of an
				   inline-block input next to a floated button: `.button` carries
				   `float: right`, so the button dropped below the input and hung
				   off the right edge on its own line - the field and its one
				   action read as two unrelated controls. Flex ignores floats on
				   its items, which is what actually pins the two to one line and
				   one baseline; the stylesheet owns the geometry so dark.css can
				   reach every part of it. */
				'    <div class="findFeaturesRow">' +
				'      <div class="findFeaturesInput input"><input type="text" placeholder="Gene, metabolite..."></div>'+
				'      <a class="button btn-info findFeatureButton helpTip" title="Find features"><i class="fa fa-search"></i> Search</a>' +
				'    </div>' +
				'    <div class="applyWaitMessage" style="color:#4c4c4c; margin: 10px; display:none;"> Searching...<i class="fa fa-cog fa-spin" style=" float: left; margin-right: 10px; "></i></div>' +
				'  </div>'+
				'  <div class="patwaysDetailsContainer"></div>'+
				'  <div class="searchResultsWrapper" style="display:none;">'+
				'    <a href="javascript:void(0)" class="backToPathwayDetailsButton" style="margin: 5px 0px;"><i class="fa fa-long-arrow-left"></i> Back to Pathway details</a>' +
				'    <h3 class="resultsCounter">Found N features.</h3>' +
				'    <div class="resultsContainer" style="width:245px; margin-left: 10px; padding-bottom:20px;"></div>'+
				'  </div>'+
				"</div>"
			}
		],
		listeners: {
			boxready: function() {
				me.showPathwayDetails();

				var el = $(me.getComponent().el.dom);
				var index = me.getParent().searchFeatureIndex || {};
				var availableTags = Object.keys(index).sort();
				//SOME EVENT HANDLERS
				el.find("#hideFindFeaturePanelButton").click(function() {
					me.toggle(false);
				});
				/* Looked up from the container, not with `.next()`: the button
				   now lives inside .findFeaturesRow and the wait message is the
				   row's sibling, so a sibling walk from the button finds nothing
				   and the search silently never ran. */
				el.find(".findFeatureButton").click(function() {
					var waitMessage = el.find(".applyWaitMessage");
					waitMessage.fadeIn(400, function() {
						el.find(".patwaysDetailsContainer").hide();
						me.searchFeatures(el.find(".findFeaturesInput > input").val());
						waitMessage.hide();
					});
				});
				el.find(".findFeaturesInput > input").autocomplete({
					source: availableTags,
					minLength: 2
				}).on("keydown", function(event) {
					/* Enter searches. A lone text field whose Enter does nothing
					   reads as broken, and the button is the only other way in. */
					if (event.keyCode === 13) {
						el.find(".findFeatureButton").click();
					}
				});

				el.find(".backToPathwayDetailsButton").click(function() {
					el.find(".patwaysDetailsContainer").show();
					el.find(".searchResultsWrapper").hide();
				});

				initializeTooltips(".helpTip");
			},
			resize: function( view, width, height, oldWidth, oldHeight, eOpts ){
				var componentHeight = $(view.getEl().dom).outerHeight();
				var headerHeight = $(view.getEl().dom).find(".lateralOptionsPanel-header").outerHeight() + 10;
				$(view.getEl().dom).find(".lateralOptionsPanel-body").height($("#mainViewCenterPanel").height() - headerHeight - 100);
			},
			beforedestroy: function() {
				if (me.items !== null) {
					for(var i in me.items){
						Ext.destroy(me.items[i].getComponent());
					}
				}
				me.getModel().deleteObserver(me);
			}
		}
	});

	return this.component;
};

return this;
}
PA_Step4FindFeaturesView.prototype = new View();

function PA_Step4GlobalHeatmapView() {
	/**
	* About this view: TODO: DOCUMENTAR
	*/

	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4GlobalHeatmapView";
	this.showConfigurator = false;
	this.automaticUpdate = true;
	// Whether to draw the white per-condition significance stars on the
	// heatmap cells. User-toggleable from the configurator; defaults to true
	// so existing behaviour is preserved.
	this.showSignificanceStars = true;

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/

	/**
	* This function changes the visibility for the component.
	* @chainable
	* @param {boolean} visible, forces the component visibility
	* @return {PA_Step4GlobalHeatmapView} the view
	*/
	this.toggle = function(visible) {
		visible = ((visible===undefined)? ! this.getComponent().isVisible():visible);
		this.getComponent().setVisible(visible);
		return this;
	};

	this.expand = function() {
		this.isExpanded = true;

		$("#expandHeatmapButton").hide();
		$("#shrinkHeatmapButton").show();
		this.getComponent().flex = 1;
		this.getParent().getComponent().doLayout();
	};

	this.shrink = function() {
		this.isExpanded = false;
		$("#expandHeatmapButton").show();
		$("#shrinkHeatmapButton").hide();

		this.getComponent().flex = 0;
		this.getParent().getComponent().doLayout();
	};

	this.download = function() {
		throw "Not implemented"
	};

	this.updateObserver = function() {
		var start = new Date();

		//*********************************************************************************
		//STEP 0. READ THE SETTINGS
		// - READ SELECTED OMICS
		//AUXILIAR VARIABLES
		var divName, referenceOmics, featureOmicValues, omicValue,
		dataMatrix = {},
		otherDataMatrix = {},
		selectedOmics = {},
		kValues = [];

		//GET USER SELECTION FROM globalHeatmapConfigurator
		$("div.globalHeatmapConfigurator div.omicSelection input[type=checkbox]:checked").each(function() {
			var omicName = $(this).val();
			var option = this.id.replace("-check", "-radio");
			option = $("input[name=" + option + "]:checked").val();
			selectedOmics[omicName] = option;
		});

		// - CHECK IF CLUSTERIZE WAS SELECTED
		var clusterize = $("#clusterize-check").is(":checked");
		if (clusterize) {
			clusterize = $("input[name=clusterize-radio]:checked").val();
			$(".kSelection input").each(function() {
				kValues.push(this.value);
			});
		}

		// - CHECK IF FORCE ORDER WAS SELECTED
		var forceOrder = $("#order-check").is(":checked");

		// - CHECK IF PER-CONDITION SIGNIFICANCE STARS SHOULD BE DRAWN. Stored on
		//   the instance so generateHeatmap (a method on this view) can read it
		//   without threading an extra parameter through generateContent. The
		//   `.length` guard keeps the pre-render default (true) when the
		//   configurator hasn't been rendered yet.
		this.showSignificanceStars = $("#significance-stars-check").length ? $("#significance-stars-check").is(":checked") : true;

		//*********************************************************************************
		// STEP 2. CONFIGURE CLUSTERIZE OPTIONS
		if (clusterize) {
			clusterize = {
				algorithm: clusterize,
				distance: "euclidean",
				linkage: "complete",
				dendogram: ((clusterize === "hierarchical") ? {
					width: 80,
					reorder: true,
					color: "#333"
				} : false)
			};
		}

		//*********************************************************************************
		//STEP 3. INITIALIZE THE DIV CONTENT AND THE DATA MATRIX
		//CLEAR PREVIOUS CONTENT (IF ANY)
		var globalHeatmapContainer = $("#globalHeatmapContainer");
		globalHeatmapContainer.empty();
		//GENERATE ALL CONTAINERS
		// The colour ramp is drawn once per omic, under its heading, because the
		// range is per omic: the heatmaps themselves carry legend:{enabled:false}
		// (a Highcharts legend would list one entry per row, i.e. hundreds), so
		// without this nothing on the page says what red or blue means.
		var distributionSummariesGH = this.getParent().getDataDistributionSummaries();
		var visualOptionsGH = this.getParent().getVisualOptions();

		for (var omicName in selectedOmics) {
			divName = "globalHeatmapContainer-" + omicName.toLowerCase().replace(/ /g, "-");

			var legendGH = "";
			try {
				legendGH = paColorLegend(
					getMinMax(distributionSummariesGH[omicName], visualOptionsGH.colorReferences[omicName]),
					visualOptionsGH.colorScale,
					{caption: paColourReferenceLabel(visualOptionsGH.colorReferences[omicName])});
			} catch (error) {
				// A missing summary for one omic must not stop the other omics'
				// heatmaps from being drawn; the legend is decoration.
				console.error(Date.logFormat() + " could not build the colour legend for " + omicName + ".", error);
			}

			globalHeatmapContainer.append("<div class='omicHeatmapsContainer'>" + "<h3>" + omicName + "</h3>" + legendGH + "<div id='" + divName + "'></div></div>");
			dataMatrix[omicName] = {};
			otherDataMatrix[omicName] = {};
		}

		//GENERATE THE MATRIX OF DATA GROUPED BY OMIC NAME
		var matchedGenes = this.getModel().getMatchedGenes();
		var matchedCompounds = this.getModel().getMatchedCompounds();
		var omicsValues = this.getParent().getOmicsValues();
		var matchedFeatures = matchedGenes.concat(matchedCompounds);

		// Read the active replicate-display mode once and bake it into the
		// data matrix that feeds the global heatmap. Build sites that pull
		// `getValues()` directly (rather than going through the renderer's
		// mode-aware path) need to ask for the right view explicitly.
		var jobModelGH = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var replicateModeGH = jobModelGH && jobModelGH.getReplicateMode ? jobModelGH.getReplicateMode() : "replicates";

		for (var i = matchedFeatures.length; i--;) {
			//GET THE VALUES FOR CURRENT GENE
			featureOmicValues = omicsValues[matchedFeatures[i]].getOmicsValues();

			for (var j = featureOmicValues.length; j--;) {
				omicValue = featureOmicValues[j];

				//SKIP OMIC IF NOT SELECTED
				if (selectedOmics[omicValue.omicName] === undefined) {
					continue;
				}

				// Symbol + KEGG gene id (see paFeatureRowName): the symbol alone
				// does not identify the row, because one uploaded identifier can
				// be matched to several KEGG genes.
				//
				// This is also the key of the data matrix, and generateContent()
				// recovers it by parsing the reference heatmap's y-axis labels, so
				// label and key have to be the same string - they are built once,
				// here.
				var featureName = paFeatureRowName(omicsValues[matchedFeatures[i]]);

				//PUSH IF USER CHOOSE all OR IF FEATURE IS RELEVANT
				if (selectedOmics[omicValue.omicName] === "all" || omicValue.isRelevant(undefined, replicateModeGH) || omicValue.isRelevantAssociation()) {
					referenceOmics = dataMatrix;
				} else {
					referenceOmics = otherDataMatrix;
				}
				referenceOmics[omicValue.omicName][featureName] = referenceOmics[omicValue.omicName][featureName] || [];

				// For regulator omics (TF, miRNA, methylation, any omic uploaded with
				// associations), the row is conceptually "this regulator's value at
				// this target". Show the regulator as the primary identifier and the
				// target as secondary — the inverse of regular omics where the row IS
				// the gene. `omicValue.isRegulator` is set server-side whenever the
				// input line used the `targetID:::regulatorID` format. The regulator's
				// display name comes from `omicValue.originalName` — either the
				// resolved gene symbol or, when no symbol mapping exists, the raw
				// regulator ID (e.g. a miRNA name).
				//
				// `linkKey` is the canonical identifier the cross-heatmap hover handler
				// uses to find sibling rows (e.g. highlight the WRKY40 TF row when the
				// user hovers the NAC001 gene-expression row). For regular omics the
				// keggName side of the label already holds it; for swapped regulator
				// rows we set linkKey explicitly to keep the linkage. It is the
				// disambiguated `featureName` (symbol + KEGG id) rather than the bare
				// symbol, because that is what the other rows' labels now yield -
				// and because two distinct KEGG genes can share a symbol, which is
				// exactly what the linkage must not conflate.
				var targetName = omicsValues[matchedFeatures[i]].getName();
				var isRegulatorRow = !!(omicValue.isRegulator);
				// Per-condition significance, mirroring the pathway-box tooltip
				// heatmap: one boolean per cell so generateHeatmap can draw the
				// white star on significant cells in multi-condition data.
				// isRelevant(j, mode) is an O(1) array lookup (FeatureModels.js),
				// so building this list per row is effectively free.
				var ghValues = omicValue.getValues(replicateModeGH);
				var ghSignificance = [];
				for (var c = 0; c < (ghValues ? ghValues.length : 0); c++) {
					ghSignificance.push(omicValue.isRelevant(c, replicateModeGH) === true);
				}
				if (isRegulatorRow) {
					referenceOmics[omicValue.omicName][featureName].push({
						keggName: omicValue.originalName,
						inputName: targetName,
						linkKey: featureName,
						isRelevant: omicValue.isRelevant(undefined, replicateModeGH),
						isRelevantAssociation: omicValue.isRelevantAssociation(),
						significance: ghSignificance,
						values: ghValues
					});
				} else {
					referenceOmics[omicValue.omicName][featureName].push({
						keggName: featureName,
						inputName: omicValue.originalName || omicValue.getInputName(),
						isRelevant: omicValue.isRelevant(undefined, replicateModeGH),
						isRelevantAssociation: omicValue.isRelevantAssociation(),
						significance: ghSignificance,
						values: ghValues
					});
				}
			}
		}

		if (forceOrder) {
			referenceOmics = Object.keys(selectedOmics);
			if (clusterize) {
				clusterize.k = kValues[0];
			}

			this.generateContent(referenceOmics, dataMatrix, otherDataMatrix, clusterize, 0);
		} else {
			var k = 0;
			for (var omicName in selectedOmics) {
				var aux = {};
				aux[omicName] = dataMatrix[omicName];

				if (clusterize) {
					clusterize.k = kValues[k];
				}
				k++;

				this.generateContent([omicName], aux, null, clusterize, 0);
			}
		}

		//CLEAR PREVIOUS CONTENT (IF ANY)
		$(".updateMessageContainer").hide();

		start = (new Date() - start);
		this.automaticUpdate = (start < 10000);
		console.log('Rendered in ' + start + ' ms');
	};

	this.generateContent = function(referenceOmics, dataMatrix, otherDataMatrix, clusterize, level) {
		var referenceOmic = referenceOmics.shift();

		if (referenceOmic === undefined) {
			return;
		}
		//*********************************************************************************
		//STEP 1. GENERATE THE HEATMAP FOR REFERENCE OMIC
		var omicValues = Array.prototype.concat.apply([], Object.values(dataMatrix[referenceOmic]));
		var showLabels = true;
		var divName = "globalHeatmapContainer-" + referenceOmic.toLowerCase().replace(/ /g, "-");
		var divWidth = 200;

		if (omicValues.length === 0) {
			$("#" + divName).append("<h4 style='width:" + divWidth + "px;'>No data</h4>");
			return;
		}

		// 90px rather than 50px of label gutter: the row label now has to fit a
		// gene symbol AND the KEGG gene id that ties it to the identifier below
		// it (see the featureName comment in updateObserver). At 50px that pair
		// could only be shown by truncating away one of the two.
		divWidth += (showLabels) ? 90 : 0;
		divWidth += (clusterize && clusterize.dendogram) ? 80 : 0;

		// +34px for the band the rotated condition labels occupy on the x axis.
		var divHeight = (omicValues.length + 1) * 30 + 34;

		$("#" + divName).append("<div id='" + divName + "-" + level + "' class='heatmapContainer' style='width:" + divWidth + "px; height:" + divHeight + "px'></div>");
		var referenceHeatmap = this.generateHeatmap(divName + "-" + level, referenceOmic, omicValues, this.getParent().getDataDistributionSummaries(), this.getParent().getVisualOptions(), showLabels, clusterize);

		//*********************************************************************************
		//STEP 2. FOR EACH GENE IN THE REFERENCE HEATMAP, GENERATE AN AUXILIAR MATRIX
		//        FOLLOWING THE ORDER IN THE HEATMAP
		var orderedGenes = referenceHeatmap.yAxis[0].categories;
		var featureName;
		for (var omicName in referenceOmics) {
			omicName = referenceOmics[omicName];
			omicValues = [];
			//EXTRACT GENES FOR EACH REMAINING OMIC
			for (var i = orderedGenes.length; i--;) {
				// Recover the data-matrix key from the reference heatmap's row
				// label. The relevance markers are written as "* " / "^ ", so
				// stripping only the marker character left a leading space and the
				// lookup below missed for every significant feature - which showed
				// up as a "NO DATA" row in the secondary omics even when the omic
				// had data. trim() is what makes label and key the same string.
				featureName = orderedGenes[i].split("#")[0].replace(/[\*\^]/g, "").trim();

				if (dataMatrix[omicName][featureName] !== undefined) {
					dataMatrix[omicName][featureName].map(x => omicValues.push(x));
					delete dataMatrix[omicName][featureName];
				} else if (otherDataMatrix && otherDataMatrix[omicName][featureName] !== undefined) {
					otherDataMatrix[omicName][featureName].map(x => omicValues.push(x));
				} else {
					omicValues.push({
						keggName: featureName,
						inputName: "NO DATA",
						isRelevant: false,
						isRelevantAssociation: false,
						values: null
					});
				}
			}

			divName = "globalHeatmapContainer-" + omicName.toLowerCase().replace(/ /g, "-");
			divWidth = 200;
			divWidth += (showLabels) ? 90 : 0;
			$("#" + divName).append("<div id='" + divName + "-" + level + "' class='heatmapContainer' style='width:" + divWidth + "px; height:" + divHeight + "px;'></div>");
			this.generateHeatmap(divName + "-" + level, omicName, omicValues, this.getParent().getDataDistributionSummaries(), this.getParent().getVisualOptions(), true, false, referenceHeatmap.xAxis[0].categories.length);
		}
		// STEP 3. RECURSIVE CALL
		this.generateContent(referenceOmics, dataMatrix, otherDataMatrix, clusterize, level + 1);
	};

	this.generateHeatmap = function(targetID, omicName, omicsValues, dataDistributionSummaries, visualOptions, showLabels, clusterize, maxX) {
		var featureValues,
		x = 0,
		y = 0,
		series = [],
		yAxisCat = [],
		serie,
		later = [],
		position;
		maxX = (maxX || -1);

		showLabels = (showLabels === undefined) ? true : showLabels;

		// Resolve the column labels BEFORE the matrix is built, so the cells
		// are plotted in the space the axis is labelled in - see
		// paValuesForHeader().
		var jobModelGH_HM = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var replicateModeGH_HM = jobModelGH_HM && jobModelGH_HM.getReplicateMode ? jobModelGH_HM.getReplicateMode() : "replicates";
		var omicHeaderGH = paOmicHeaders(jobModelGH_HM, omicName);

		//STEP 1. GENERATE THE DATA MATRIX
		for (var i = omicsValues.length - 1; i >= 0; i--) {
			//restart the x coordinate
			x = 0;
			var limits = getMinMax(dataDistributionSummaries[omicName], visualOptions.colorReferences[omicName]);

			//Get the values and the name for the new serie
			featureValues = paValuesForHeader(omicsValues[i], omicHeaderGH);

			var shownameValue = omicsValues[i].inputName != omicsValues[i].originalName && omicsValues[i].originalName !== undefined ?
				omicsValues[i].originalName + ": " + omicsValues[i].inputName :
				omicsValues[i].inputName;

				var relevantSymbols = "";

				if (omicsValues[i].isRelevant === true) {
					relevantSymbols += "* ";
				}
				if (omicsValues[i].isRelevantAssociation === true) {
					relevantSymbols += "^ ";
				}

			serie = {
				name: relevantSymbols + omicsValues[i].keggName + "#" + shownameValue,
				data: [],
				turboThreshold: Number.MAX_VALUE,
				// linkKey carries the canonical cross-omic identifier (target symbol)
				// so the mouseOver handler can highlight sibling rows even when the
				// label's primary side is the regulator symbol (TF rows post-swap).
				linkKey: omicsValues[i].linkKey
			};
			//Add the name for the row (e.g. MagoHb or "miRNA my_mirnaid_1")
			yAxisCat.push(relevantSymbols + omicsValues[i].keggName + "#" + shownameValue);

			var featureSignificance = omicsValues[i].significance;
			if (featureValues !== null) {
				for (var j in featureValues) {
					serie.data.push({
						x: x,
						y: y,
						value: featureValues[j],
						color: getColor(limits, featureValues[j], visualOptions.colorScale),
						// Per-condition significance for the white star (see dataLabels
						// below). Falls back to false when the row carries no
						// significance array (e.g. the gray "no data" rows).
						isSignificant: featureSignificance ? featureSignificance[j] === true : false
					});
					x++;
					maxX = Math.max(maxX, x);
				}
				series.push(serie);
			} else {
				/* IF THERE IS NOT DATA FOR THIS FEATURE, WE WILL ADD
				* A GRAY ROW, BUT FIRST WE NEED SOME INFORMATION (MAX X), SO WE WILL ADD
				* LATER, NOW JUST ADD A NULL, AND REPLACE LATER*/
				later.push({
					serie: serie,
					position: y
				});
				series.push(null);
			}
			y++;
		}

		for (var i in later) {
			x = 0;
			serie = later[i].serie;
			position = later[i].position;

			for (var j = 0; j < maxX; j++) {
				serie.data.push([x, position, null]);
				x++;
			}
			series[position] = serie;
		}

		var replaceSymbols = {
			"*": '<i class="relevantFeature"></i>',
			"^": '<i class="relevantAssociationFeature"></i>'
		};

		// Resolve the per-omic column labels for the active replicate mode so
		// the tooltip can name each cell by its sample (or replicate) column
		// — matches the pathway-box tooltip behaviour. Captured in closure
		// because the Highcharts formatter loses the surrounding `this`.
		// (jobModelGH_HM / replicateModeGH_HM / omicHeaderGH are resolved at the
		// top of this function, before the values are chosen against them.)

		// The same names, now also drawn on the axis instead of only in the
		// tooltip - see paConditionAxis().
		var xAxisConfig = paConditionAxis(maxX, omicHeaderGH, {maxChars: 10});
		var xAxisCat = xAxisConfig.categories;

		// User toggle from the configurator. Captured here because the dataLabels
		// formatter below runs with `this` bound to the Highcharts point. Defaults
		// to showing stars unless explicitly turned off.
		var showStarsGH = this.showSignificanceStars !== false;

		//STEP 2. DRAW THE HEATMAP
		/* The gap that separates one cell from the next, in the colour of the
		   surface behind this chart. See paCellGap(). */
		var cellGap = paCellGap(targetID);

		var heatmap = new Highcharts.Chart({
			chart: {
				type: 'heatmap',
				renderTo: targetID
			},
			title: null,
			legend: {enabled: false},
			credits: {enabled: false},
			/* The row-under-the-pointer outline. A 3px pure-black box around a
			   30px row was the loudest mark on the figure, louder than any cell
			   it was pointing at. 2px of the app's ink still reads as a frame
			   without out-shouting the data. */
			heatmapSelector: {
				color: '#3F3F46',
				lineWidth: 2
			},
			clusterize: clusterize,
			tooltip: {
				borderColor: "#333",
				formatter: function() {
					var title = this.point.series.name.split("#");
					var headerField = omicHeaderGH[this.point.x + 1];
					// Nothing is truncated here on purpose: the tooltip is the
					// fallback for the length-capped axis labels, so it is the one
					// place that has to show the identifiers in full.
					if (headerField) {
						title[0] = title[0] + " [" + headerField + "]";
					}
					title[1] = (title.length > 1) ? title[1] : "";
					return "<b>" + title[0].replace(/[\*\^]/g, function(c) { return replaceSymbols[c]; }) + "</b><br/>" + "<i class='tooltipInputName'>" + title[1] + "</i>" + (this.point.value === null ? "No data" : this.point.value);
				},
				useHTML: true
			},
			xAxis: xAxisConfig,
			yAxis: {
				categories: yAxisCat,
				title: null,
				width: 50,
				labels: {
					formatter: function() {
						if (this.value.split !== undefined) {
							var title = this.value.split("#");
							title[1] = (title.length > 1) ? title[1] : "No data";
							// paRowLabel() keeps the regulator rows' embedded markup
							// intact and ellipsises from the front - the row now reads
							// "Calm1 (12313)" over "…SG00000036438", and the title
							// attribute carries both in full.
							return paRowLabel(title[0], title[1], {width: 90, maxChars: 14});
						}
					},
					style: {fontSize: "9px"},
					useHTML: true,
					enabled: showLabels
				}
			},
			series: series,
			plotOptions: {
				heatmap: {
					borderColor: cellGap.borderColor,
					borderWidth: cellGap.borderWidth,
					// Star on cells significant for that condition. Guarded by
					// maxX > 1 so it only shows for multi-condition data (mirrors the
					// pathway-box tooltip heatmap behaviour). Its ink comes from the
					// cell's luminance -- a white star vanished on the pale half of
					// the ramp, which is most of it.
					dataLabels: {
						enabled: true,
						useHTML: true,
						formatter: function() {
							if (showStarsGH && this.point.isSignificant && maxX > 1) {
								return '<i class="fa fa-star" style="color: ' +
									paInkOnCell(this.point.color) +
									' !important; font-size: 8px; padding: 0;"></i>';
							}
						}
					}
				},
				series: {
					point: {
						events: {
							mouseOver: function() {
								var me = this;
								// Sibling-row matching across heatmaps. Regular omics encode the
								// target identifier as the primary side of `name` (left of `#`),
								// but regulator rows put the regulator there post-swap. The
								// `linkKey` series option (set in generateHeatmap) carries the
								// canonical target identifier so cross-heatmap highlighting keeps
								// working in both directions.
								var getLinkKey = function(s) {
									if (s.options && s.options.linkKey) {
										return s.options.linkKey;
									}
									return s.name.split("#")[0].replace(/[\*\^]\s/g, "");
								};
								var keggName = getLinkKey(me.series);
								//FOR EACH HEATMAPS
								$("div.heatmapContainer").each(function() {
									var heatmap = $(this).highcharts();
									var serie = heatmap.series[me.series.index];

									if (serie !== undefined && getLinkKey(serie) === keggName) {
										serie.showHeatmapSelector(undefined, me.y);
										return true;
									} else {
										for (var i in heatmap.series) {
											if (getLinkKey(heatmap.series[i]) === keggName) {
												heatmap.series[i].showHeatmapSelector();
												return true;
											}
										}
									}
									heatmap.series[0].hideHeatmapSelector();
								});
							}
						}
					}
				}
			}
		});

		return heatmap;
	};

	/**
	* This function apply the settings that user can change
	* for the visual representation of the model (w/o reload everything).
	* - TODO: DOCUMENTAR
	* @chainable
	* @returns {PA_Step4GlobalHeatmapView}
	*/
	this.applyVisualSettings = function() {
		var me = this;
		debugger;
		if (this.automaticUpdate === false) {
			$(".updateMessageContainer").fadeIn();
			return;
		}
		$(".applyWaitMessage").fadeIn(400, function() {
			me.updateObserver();
			$(".applyWaitMessage").fadeOut();
		});

		return this;
	};

	/**
	* This function generates the component (EXTJS) using the content of the model
	* @returns {Ext.ComponentView} The visual component
	*/
	this.initComponent = function() {
		var me = this, divName;

		var htmlCode =
		"<h4>Choose the omics to draw</h4>" +
		'<span class="infoTip"><span style=" color: rgb(158, 58, 179); font-weight: bold; ">Drag and drop</span> to change the order in which heatmaps will be drawn.</span>' +
		'<div id="omicSelectionWrapper">';

		var omicNames = Object.keys(this.model.getSignificanceValues());
		//1. GENERATE THE OMIC SELECTORS (WHICH OMIC SHOULD BE PAINTED)
		for (var i in omicNames) {
			//CHECK IF WE HAVE VALUES FOR THIS OMIC IN CURRENT PATHWAY
			if (this.getModel().getSignificanceValues()[omicNames[i]][0] !== 0) {
				divName = "lateralOptionsSelector-" + omicNames[i].toLowerCase().replace(/ /g, "-");
				htmlCode +=
				'<div class="lateralOptionsSelector omicSelection">' +
				' <div class="omicPosition">' + (parseInt(i) + 1) + '</div>'+
				' <div>'+
				'   <div class="checkbox"><input checked type="checkbox" id="' + divName + '-check" value="' + omicNames[i] + '"><label for="' + divName + '-check">' + omicNames[i] + '</label></div>' +
				'   <div class="radio"><input type="radio" id="' + divName + '-radio1" name="' + divName + '-radio" value="all"><label for="' + divName + '-radio1">All features (Genes or compounds)</label></div>' +
				'   <div class="radio"><input checked type="radio" id="' + divName + '-radio2" name="' + divName + '-radio" value="relevant"><label for="' + divName + '-radio2">Only relevant features</label></div>' +
				' </div>' +
				'</div>';
			}
		}

		htmlCode +=
		'</div>' +
		"<h4>Advanced options</h4>" + //2. GENERATE ADVANCED OPTIONS
		'<span class="infoTip">Depending on the selected settings, heatmap generation can take up to 10 seconds.</span>' +
		' <div class="checkbox"><input type="checkbox" id="order-check"><label for="order-check"> Force order for features.</label></div>' + // 2.1 ENABLE / DISABLE ORDERING
		' <div class="checkbox"><input checked type="checkbox" id="significance-stars-check"><label for="significance-stars-check"> Show per-condition significance stars (<i class="fa fa-star"></i>)</label></div>' + // 2.1b SHOW / HIDE PER-CONDITION STARS
		' <div class="checkbox"><input checked type="checkbox" id="clusterize-check"><label for="clusterize-check"> Clusterize data</label></div>' + // 2.2 ENABLE / DISABLE CLUSTERING
		' <div class="lateralOptionsSelector clusterSelection">' +
		'    <div class="radio"><input checked type="radio" id="clusterize-hcluster" name="clusterize-radio" value="hierarchical"><label for="clusterize-hcluster">Hierarchical clustering </label></div>' +
		'    <div class="radio"><input  type="radio" id="clusterize-kcluster" name="clusterize-radio" value="kmeans"><label for="clusterize-kcluster">K-means clustering </label></div>' +
		'    <div id="kMeansSelectors" style="display:none; margin-left:50px;">' + // 2.3 SET VALUES FOR K FOR EACH OMIC
		'    <span class="infoTip">Choose the number of clusters (k) for each omic.</span>';
		for (var i in omicNames) {
			//CHECK IF WE HAVE VALUES FOR THIS OMIC IN CURRENT PATHWAY
			var k = this.getModel().getSignificanceValues()[omicNames[i]][0];
			if (k !== 0) {
				//DEFAULT OPTION k = SQRT(N/2), rounded upwards
				k = Math.ceil(Math.sqrt(k / 2));

				divName = "kMeansSelector-" + omicNames[i].toLowerCase().replace(/ /g, "-");
				htmlCode +=
				'      <div id="' + divName + '" class="lateralOptionsSelector kSelection">' +
				'        <label>' + omicNames[i] + ': </label> <input type="number" min="1" step="1" value="' + k + '" SIZE="6">' +
				'      </div>';
			}
		}
		htmlCode +=
		'     </div>' + //kMeansSelectors
		'</div>'; //clusterSelection

		this.component = Ext.widget({
			xtype: "box", cls: "lateralOptionsPanel", flex: 0,  height: ($("#mainViewCenterPanel").height() - 100),
			resizable: {
				handles: 'w',
				listeners: {
					beforeresize: function() {
						return !me.isExpanded;
					},
					resize: function(resizer, width, height) {
						me.getParent().adjustChildrenWidth();
					}
				}
			},
			previousWidth: 400, width: 400, minWidth: 400, html:
			'<div class="lateralOptionsPanel-header" data-guides="ignore">' +
			'  <div class="lateralOptionsPanel-toolbar">' +
			'    <a href="javascript:void(0)" class="toolbarOption btn-secondary helpTip" id="hideHeatmapPanelButton" title="Hide this panel"><i class="fa fa-times"></i></a>' +
			'    <a href="javascript:void(0)" class="toolbarOption btn-secondary helpTip" id="configureHeatmapButton" title="Configure heatmap"><i class="fa fa-cogs"></i></a>' +
			'    <a href="javascript:void(0)" class="toolbarOption btn-secondary helpTip" id="expandHeatmapButton" title="Expand this panel"><i class="fa fa-expand"></i></a>' +
			'    <a href="javascript:void(0)" class="toolbarOption btn-secondary helpTip" id="shrinkHeatmapButton" style="display:none;"  title="Shrink this panel"><i class="fa fa-compress"></i></a>' +
			// '    <a href="javascript:void(0)" class="toolbarOption helpTip" id="downloadHeatmapButton"><i class="fa fa-download"></i></a>' +
			'  </div>' +
			"  <h2>Global heatmap</h2>" +
			"</div>" +
			"<div class='lateralOptionsPanel-body globalHeatmapView-body'>" +
			'  <p>This panel contains the heatmap for all the features involved on this pathway. <br>Choose the visible omics features will be visible using the <i class="fa fa-cogs"></i> Settings button.</p>' +
			'  <div class="updateMessageContainer"> <h3>Visual changes detected! </h3> <p>Some visual settings changed recently but the Heatmap content did not change.<br>Click <a id="refreshHeatmap" href="javascript:void(0)">here</a> if you want to refresh the Heatmap content. </p> </div>' +
			'  <div class="globalHeatmapConfigurator" ' + (this.showConfigurator ? 'style="display:none"' : '') + '>' +
			htmlCode +
			'    <a href="javascript:void(0)" class="button btn-success helpTip" id="updateHeatmapButton" title="Apply changes"><i class="fa fa-check"></i> Apply</a>' +
			'    <div class="applyWaitMessage"><i class="fa fa-cog fa-spin"></i> Drawing heatmap...</div>' +
			'  </div>' +
			"  <div id='globalHeatmapContainer'></div>" +
			"</div>",
			listeners: {
				boxready: function() {
					//SOME EVENT HANDLERS
					$("#configureHeatmapButton").click(function() {
						$(".globalHeatmapConfigurator").slideToggle(400, function() {
							if ($(this).css("display") === "block") {
								$('.globalHeatmapView-body').animate({
									scrollTop: ($(".globalHeatmapConfigurator").offset().top)
								}, 500);
							}
						});
					});
					$("#hideHeatmapPanelButton").click(function() {
						me.getParent().hideGlobalHeatmapPanel();
					});
					$("#updateHeatmapButton").click(function() {
						$(this).next(".applyWaitMessage").fadeIn(400, function() {
							me.updateObserver();
							$(".globalHeatmapConfigurator").slideUp();
							$(this).hide();
						});
					});
					$("#refreshHeatmap").click(function() {
						me.updateObserver();
					});
					$("#expandHeatmapButton").click(function() {
						me.expand();
					});
					$("#shrinkHeatmapButton").click(function() {
						me.shrink();
					});
					$("#downloadHeatmapButton").click(function() {
						me.download();
					});
					$("#clusterize-check").click(function() {
						$(".clusterSelection").slideToggle();
					});

					$("input[name='clusterize-radio']").change(function() {
						if ($(this).val() !== "kmeans") {
							$("#kMeansSelectors").slideUp();
						} else {
							$("#kMeansSelectors").slideDown();
						}
					});

					//INITIALIZE THE DRAG AND DROP
					dragula($("#omicSelectionWrapper")[0], {
						moves: function(el, container, handle) {
							if (handle.tagName === "LABEL") {
								return false;
							}
							return true;
						},
					}).on("drop", function(el, container, source) {
						$(".omicPosition").each(function(index) {
							$(this).text(index + 1);
						});
					});

					initializeTooltips(".helpTip");
				},
				resize: function( view, width, height, oldWidth, oldHeight, eOpts ){
					var componentHeight = $(view.getEl().dom).outerHeight();
					var headerHeight = $(view.getEl().dom).find(".lateralOptionsPanel-header").outerHeight() + 10;
					$(view.getEl().dom).find(".lateralOptionsPanel-body").height($("#mainViewCenterPanel").height() - headerHeight - 100);
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
PA_Step4GlobalHeatmapView.prototype = new View();

//------------------------------------------------------------------------------------------------

function PA_Step4DetailsView() {
	/*********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.name = "PA_Step4DetailsView";
	this.items = null;
	this.targetID = null;

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.loadModel = function (model) {
		this.model = model;

		var features = this.getModel().getFeatures();

		this.items = [];
		for (var i in features) {
			this.items.push(new PA_Step4KeggDiagramFeatureView().loadModel(features[i]).setParent(this));
		}
		return this;
	};

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.getTargetID = function () {
		return this.targetID;
	};

	/*********************************************************************
	* OTHER FUNCTIONS
	***********************************************************************/
	/**
	* This function changes the visibility for the component.
	* @chainable
	* @param {boolean} visible, forces the component visibility
	* @return {PA_Step4DetailsView} the view
	*/
	this.toggle = function(visible) {
		visible = ((visible===undefined)? ! this.getComponent().isVisible():visible);
		this.getComponent().setVisible(visible);
		return this;
	};

	this.expand = function () {
		this.isExpanded = true;

		$("#expandFeatureSetButton").hide();
		$("#shrinkFeatureSetButton").show();

		this.getComponent().flex = 1;
		this.getParent().getComponent().doLayout();
	};

	this.shrink = function () {
		this.isExpanded = false;
		$("#expandFeatureSetButton").show();
		$("#shrinkFeatureSetButton").hide();

		this.getComponent().flex = 0;
		this.getParent().getComponent().doLayout();
	};

	this.updateObserver = function () {
		var me = this;
		var featureSetElems = this.getModel().getFeatures();
		var metagenesSetElems = this.getModel().getMetagenes();
		var featureType = featureSetElems[0].getFeature().getFeatureType();
		var entriesTable = {}, entriesTableMetagenes = {};

		// Read the active replicate-display mode once and bake it into the
		// per-row `values` we collect below. Without this, "Show details"
		// would always render the raw 16 columns even after the user picked
		// "Show samples (averaged)" in the visual-options panel.
		var jobModelDV = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var replicateModeDV = jobModelDV && jobModelDV.getReplicateMode ? jobModelDV.getReplicateMode() : "replicates";

		// Offer "Neighbouring features" only where it can resolve to something:
		// the neighbour lists are keyed by KEGG compound id. The panel is one
		// instance reused for every box, so this is set on each model load, and
		// stale output from the previous box goes with it.
		var isMetaboliteSet = String(featureType || "").toLowerCase().indexOf("compound") > -1;
		var neighbourSection = this.getComponent().queryById("neighbouringFeaturesSection");
		if (neighbourSection) {
			neighbourSection.setVisible(isMetaboliteSet);
		}
		$("#featureFamilyOverviewContainerRegulate").empty();
		$("#inputLevel").val("");

		/**
		* This function fills recursively a table ordering by omicType
		*/

		// Symbol + KEGG id, for the same reason as the global heatmap: a symbol
		// on its own does not say which KEGG gene the row belongs to. See
		// paFeatureRowName(). It reduces to the plain name when the feature has
		// no separate id (metagenes), so those rows read as before.
		var omicValues, featureName;
		for (var i in featureSetElems) {
			featureName = paFeatureRowName(featureSetElems[i].getFeature());
			omicValues = featureSetElems[i].getFeature().getOmicsValues();
			for (var j in omicValues) {
				addTableEntrie(entriesTable, omicValues[j], featureName, "", replicateModeDV);
			}
		}

		for (var i in metagenesSetElems) {
			featureName = paFeatureRowName(metagenesSetElems[i].getFeature());
			omicValues = metagenesSetElems[i].getFeature().getOmicsValues();
			for (var j in omicValues) {
				addTableEntrie(entriesTableMetagenes, omicValues[j], featureName, "", replicateModeDV);
			}
		}

		var omicNames = Object.keys(entriesTable).sort();
		var elem = $("#featureFamilyOverviewContainer");
		elem.empty();

		var divWidth = elem.width() - 400;

		var heatmap, plot, heatmap_metagenes, plot_metagenes, divId, htmlCode, legendDV;
		var distributionSummariesDV = this.getParent("PA_Step4PathwayView").getDataDistributionSummaries();
		var visualOptionsDV = this.getParent("PA_Step4PathwayView").getVisualOptions();

		for (var i in omicNames) {
			divId = omicNames[i].replace(" ", "_");

			// The colour ramp these heatmaps were painted with. The charts
			// themselves carry legend:{enabled:false} - one series per row would
			// mean hundreds of legend entries - so the scale is stated here
			// instead of nowhere. Guarded because a missing distribution summary
			// for one omic must not stop the panel from rendering.
			legendDV = "";
			try {
				legendDV = paColorLegend(
					getMinMax(distributionSummariesDV[omicNames[i].split("#")[0]], visualOptionsDV.colorReferences[omicNames[i].split("#")[0]]),
					visualOptionsDV.colorScale,
					{caption: paColourReferenceLabel(visualOptionsDV.colorReferences[omicNames[i].split("#")[0]])});
			} catch (error) {
				console.error(Date.logFormat() + " could not build the colour legend for " + omicNames[i] + ".", error);
			}

			htmlCode =
			"<div class='contentbox'>" +
			"  <h3>" + omicNames[i].replace("#", " ") + "<span><input type='checkbox' id='" + divId + "_cb_relevant' value='"+ omicNames[i] +"'/>Only relevant</span></h3>" +
			"  " + legendDV +
			"  <div class='PA_step5_heatmapContainer' id='" + divId + "_heatmapContainer'  style='height: " + ((entriesTable[omicNames[i]].length * 30) + 100) + "px'><i class='fa fa-cog fa-spin'></i> Loading..</div>" +
			"  <div class='PA_step5_plotContainer' id='" + divId + "_plotContainer'  style='width:" + divWidth + "px;height: " + ((entriesTable[omicNames[i]].length * 30) + 100) + "px'><i class='fa fa-cog fa-spin'></i> Loading..</div>" +
			"</div>";

			if (metagenesSetElems !== null && metagenesSetElems.length) {
				htmlCode += 
				"<div class='contentbox' id='" + divId + "_metagenes_box' style='" + (metagenesSetElems !== null ? '' : 'display: none;') + "'>" +
				"  <h3>" + omicNames[i].replace("#", " ") + " (metagenes) </h3>" +
				"  <div class='PA_step5_heatmapContainer' id='" + divId + "_heatmapContainer_metagenes'  style='height: " + ((entriesTableMetagenes[omicNames[i]].length * 30) + 100) + "px'><i class='fa fa-cog fa-spin'></i> Loading..</div>" +
				"  <div class='PA_step5_plotContainer' id='" + divId + "_plotContainer_metagenes'  style='width:" + divWidth + "px;height: " + ((entriesTableMetagenes[omicNames[i]].length * 30) + 100) + "px'><i class='fa fa-cog fa-spin'></i> Loading..</div>" +
				"</div>";
			}
			elem.append(htmlCode);

			//CREATE THE HEATMAP CONTAINER AND THE HEATMAP CHART
			heatmap = this.generateHeatmap(divId + "_heatmapContainer", omicNames[i].split("#")[0], entriesTable[omicNames[i]], this.getParent("PA_Step4PathwayView").getDataDistributionSummaries(), this.getParent("PA_Step4PathwayView").getVisualOptions());
			plot = this.generatePlot(divId + "_plotContainer", omicNames[i].split("#")[0], entriesTable[omicNames[i]], this.getParent("PA_Step4PathwayView").getDataDistributionSummaries(), divId + "_plotlegendContainer", this.getParent("PA_Step4PathwayView").getVisualOptions());

			if (metagenesSetElems !== null && metagenesSetElems.length) {
				heatmap_metagenes = this.generateHeatmap(divId + "_heatmapContainer_metagenes", omicNames[i].split("#")[0], entriesTableMetagenes[omicNames[i]], this.getParent("PA_Step4PathwayView").getDataDistributionSummaries(), this.getParent("PA_Step4PathwayView").getVisualOptions());
				plot_metagenes = this.generatePlot(divId + "_plotContainer_metagenes", omicNames[i].split("#")[0], entriesTableMetagenes[omicNames[i]], this.getParent("PA_Step4PathwayView").getDataDistributionSummaries(), divId + "_plotlegendContainer", this.getParent("PA_Step4PathwayView").getVisualOptions());
			}
		}

		$(".featureSetOptionsToolbar").next("h2").html(featureType + " family overview");

		// Link event individually to save heatmap reference
		$("div.contentbox h3 :checkbox").change(function() {
			var omicName = $(this).val();
			var divId = omicName.replace(" ", "_");
			var onlyRelevants = $(this).is(":checked");
	
			// Highcharts does not automatically hide Y labels when hiding series, so it is easier and faster
			// to recreate the whole graphic.
			var omicValues = entriesTable[omicName];

			if (onlyRelevants) {
				omicValues = omicValues.filter(x => x.isRelevant || x.isRelevantAssociation);
			}

			$('#' + divId + "_heatmapContainer").height(omicValues.length * 30 + 100);

			me.generateHeatmap(divId + "_heatmapContainer", omicName.split("#")[0], omicValues, me.getParent("PA_Step4PathwayView").getDataDistributionSummaries(), me.getParent("PA_Step4PathwayView").getVisualOptions());
			me.generatePlot(divId + "_plotContainer", omicName.split("#")[0], omicValues, me.getParent("PA_Step4PathwayView").getDataDistributionSummaries(), divId + "_plotlegendContainer", me.getParent("PA_Step4PathwayView").getVisualOptions());
		});

		var components = [];
		for (var i in this.items) {
			components.push(this.items[i].getComponent());
		}

		this.getComponent().queryById("itemsContainer").removeAll(false);
		this.getComponent().queryById("itemsContainer").add(components);

		for(i in this.items){
			this.items[i].updateObserver();
		}
	};

	this.generateHeatmap = function (targetID, omicName, omicsValues, dataDistributionSummaries, visualOptions) {
		var featureValues, x = 0, y = 0, maxX = -1, series = [], yAxisCat = [], serie;

		// Resolve the column labels BEFORE the value loop: whichever space the
		// axis is labelled in is the space the cells must be plotted in, and
		// the loop below picks its values to match - see paValuesForHeader().
		var jobModelDV_HM = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var replicateModeDV_HM = jobModelDV_HM && jobModelDV_HM.getReplicateMode ? jobModelDV_HM.getReplicateMode() : "replicates";
		var omicHeaderDV = paOmicHeaders(jobModelDV_HM, omicName);

		for (var i in omicsValues) {
			//restart the x coordinate
			x = 0;
			//Get the values and the name for the new serie
			featureValues = paValuesForHeader(omicsValues[i], omicHeaderDV);
			var shownameValue = omicsValues[i].inputName != omicsValues[i].originalName && omicsValues[i].originalName !== undefined ?
				omicsValues[i].originalName + ": " + omicsValues[i].inputName :
				omicsValues[i].inputName;

			var relevantSymbols = "";

			if (omicsValues[i].isRelevant === true) {
				relevantSymbols += "* ";
			}
			if (omicsValues[i].isRelevantAssociation === true) {
				relevantSymbols += "^ ";
			}

			serie = {name: relevantSymbols + omicsValues[i].keggName + "#" + shownameValue, data: []};
			//Add the name for the row (e.g. MagoHb or "miRNA my_mirnaid_1")
			yAxisCat.push(relevantSymbols + omicsValues[i].keggName + "#" + shownameValue);

			var limits = getMinMax(dataDistributionSummaries[omicName], visualOptions.colorReferences[omicName]);

			var featureSignificance = omicsValues[i].significance;
			for (var j in featureValues) {
				serie.data.push({
					x: x,
					y: y,
					value: featureValues[j],
					color: getColor(limits, featureValues[j], visualOptions.colorScale),
					// Per-condition significance for the white star (see dataLabels
					// below). Falls back to false when no significance array exists.
					isSignificant: featureSignificance ? featureSignificance[j] === true : false
				});
				x++;
				maxX = Math.max(maxX, x);
			}
			series.push(serie);
			y++;
		}

		var replaceSymbols = {
			"*": '<i class="relevantFeature"></i>',
			"^": '<i class="relevantAssociationFeature"></i>'
		};

		var clusterize = omicsValues.length > 5 ? {
			algorithm: "hierarchical",
			distance: "euclidean",
			linkage: "complete",
			dendogram: false
		} : false;

		// Resolve column labels for this omic in the active replicate mode so
		// the tooltip names the sample (or replicate) under each cell. Same
		// shape as the pathway-box tooltip and the global-heatmap tooltip.
		// (jobModelDV_HM / replicateModeDV_HM / omicHeaderDV are resolved at the
		// top of this function, before the values are chosen against them.)

		// The same names, on the axis as well as in the tooltip - see
		// paConditionAxis().
		var xAxisConfig = paConditionAxis(maxX, omicHeaderDV, {maxChars: 12});
		var xAxisCat = xAxisConfig.categories;

		/* The gap that separates one cell from the next, in the colour of the
		   surface behind this chart. See paCellGap(). */
		var cellGap = paCellGap(targetID);

		var heatmap = new Highcharts.Chart({
			chart: {type: 'heatmap', renderTo: targetID},
			heatmapSelector: {color: '#3F3F46', lineWidth: 2},
			title: null, legend: {enabled: false}, credits: {enabled: false},
			clusterize: clusterize,
			tooltip: {
				borderColor: "#333",
				formatter: function () {
					var title = this.point.series.name.split("#");
					var headerField = omicHeaderDV[this.point.x + 1];
					// Untruncated on purpose: this is the fallback for the
					// length-capped axis labels.
					if (headerField) {
						title[0] = title[0] + " [" + headerField + "]";
					}
					title[1] = (title.length > 1) ? title[1] : "";
					return "<b>" + title[0].replace(/[\*\^]/g, function(c) { return replaceSymbols[c]; }) + "</b><br/>" + "<i class='tooltipInputName'>" + title[1] + "</i>" + (this.point.value === null ? "No data" : this.point.value);
				},
				useHTML: true
			},
			xAxis: xAxisConfig,
			yAxis: {
				categories: yAxisCat, title: null, width: 100,
				labels: {
					formatter: function () {
						var title = this.value.split("#");
						title[1] = (title.length > 1) ? title[1] : "No data";
						// paRowLabel() keeps the regulator rows' embedded markup
						// intact and ellipsises from the front, so identifiers that
						// share a prefix stay distinguishable.
						return paRowLabel(title[0], title[1], {width: 100, maxChars: 16});
					},
					style: {fontSize: "9px"}, useHTML: true
				}
			},
			series: series,
			plotOptions: {
				heatmap: {
					borderColor: cellGap.borderColor,
					borderWidth: cellGap.borderWidth,
					// Star on cells significant for that condition. Guarded by
					// maxX > 1 so it only shows for multi-condition data (mirrors the
					// pathway-box tooltip and global heatmaps). Ink from the cell's
					// luminance -- see paInkOnCell.
					dataLabels: {
						enabled: true,
						useHTML: true,
						formatter: function() {
							if (this.point.isSignificant && maxX > 1) {
								return '<i class="fa fa-star" style="color: ' +
									paInkOnCell(this.point.color) +
									' !important; font-size: 8px; padding: 0;"></i>';
							}
						}
					}
				},
				series: {
					point: {
						events: {
							mouseOver: function () {
								var plot = $(this.series.chart.container).parent().next().highcharts();
								for (var i in plot.series) {
									if (plot.series[i].name !== this.series.name) {
										plot.series[i].graph && plot.series[i].graph.attr("stroke", "#E2E2E2");
										plot.series[i].markerGroup && plot.series[i].markerGroup.attr("visibility", "hidden");
									}
								}
							},
							mouseOut: function () {
								var plot = $(this.series.chart.container).parent().next().highcharts();
								for (var i in plot.series) {
									plot.series[i].graph && plot.series[i].graph.attr("stroke", plot.series[i].color);
									plot.series[i].markerGroup && plot.series[i].markerGroup.attr("visibility", "visible");
								}
							}
						}
					}
				}
			}
		});

		return heatmap;
	};

	this.generatePlot = function (targetID, omicName, omicsValues, dataDistributionSummaries, legendContainerId, visualOptions) {
		var series = [], maxX = -1;
		var yAxisItem = {title: null}, omicsValue, auxValues;

		var limits = getMinMax(dataDistributionSummaries[omicName], visualOptions.colorReferences[omicName]);

		// Resolved before the loop so the points land in the same space as the
		// axis labels chosen below - see paValuesForHeader().
		var jobModelDV_PL = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var omicHeaderDV_PL = paOmicHeaders(jobModelDV_PL, omicName);

		for (var i in omicsValues) {
			auxValues = [];
			omicsValue = omicsValues[i];
			var plottedValues = paValuesForHeader(omicsValue, omicHeaderDV_PL);
			maxX = Math.max(maxX, plottedValues.length);

			for (var j in plottedValues) {
				auxValues.push({y: plottedValues[j], marker: ((plottedValues[j] > limits.max || plottedValues[j] < limits.min) ? {fillColor: '#ff6e00'} : null)});
			}

			var relevantSymbols = "";

			if (omicsValue.isRelevant === true) {
				relevantSymbols += "* ";
			}
			if (omicsValue.isRelevantAssociation === true) {
				relevantSymbols += "^ ";
			}

			series.push({
				name: relevantSymbols + omicsValue.keggName + "#" + omicsValue.inputName,
				type: 'spline',
				data: auxValues
			});
		}

		if (limits.max !== limits.absMax && limits.min !== limits.absMin) {
			// Where the colour scale clips - see paScaleClipLine().
			yAxisItem.plotLines = [
				paScaleClipLine(limits.max, "scale max", true),
				paScaleClipLine(limits.min, "scale min", false)
			];
		}

		// Real condition names on the x axis, resolved for the active
		// replicate/sample mode exactly as the paired heatmap does.
		// (omicHeaderDV_PL is resolved above, before the points were chosen.)
		var xAxisConfig = paConditionAxis(maxX, omicHeaderDV_PL, {maxChars: 12});
		var xAxisCat = xAxisConfig.categories;

		var replaceSymbols = {
			"*": '<i class="relevantFeature"></i>',
			"^": '<i class="relevantAssociationFeature"></i>'
		};
		var plot = new Highcharts.Chart({
			chart: {renderTo: targetID},
			title: null, legend: {enabled: false}, credits: {enabled: false},
			tooltip: {
				borderColor: "#333",
				formatter: function () {
					var title = this.point.series.name.split("#");
					title[1] = (title.length > 1) ? title[1] : "";
					if (xAxisCat[this.point.x] !== undefined) {
						title[0] = title[0] + " [" + xAxisCat[this.point.x] + "]";
					}
					return "<b>" + title[0].replace(/[\*\^]/g, function(c) { return replaceSymbols[c]; }) + "</b><br/>" + "<i class='tooltipInputName'>" + title[1] + "</i>" + (this.point.y === null ? "No data" : this.point.y);
				},
				useHTML: true
			},
			xAxis: [xAxisConfig],
			yAxis: yAxisItem,
			series: series
		});

		return plot;
	};

	/**
	* Draw the neighbours of this feature set's metabolite at the requested
	* network step -- or say why there are none.
	*
	* Every empty case used to be `console.warn(...); return`, so a click that
	* resolved to nothing looked identical to a click that did nothing. The one
	* users met first is the level box, which opens blank: the very first press
	* of Show Features always landed there. See paNeighbourRequest(), which owns
	* the decision and the wording, and paNeighbourRows(), which converts the
	* OmicValues in globalExpressionData into the row shape this panel's
	* heatmap/plot pair reads.
	*/
	this.showNeighbouringFeatures = function () {
		var me = this;
		var elem = $("#featureFamilyOverviewContainerRegulate");
		elem.empty();

		var note = function (message) {
			elem.append('<div class="contentbox paEmptyNote"><p>' +
				Ext.String.htmlEncode(message) + '</p></div>');
		};

		var features = this.getModel() ? this.getModel().getFeatures() : null;
		var mainFeature = (features && features.length && features[0].getFeature)
			? features[0].getFeature() : null;

		if (!mainFeature || !mainFeature.getID()) {
			note("This box carries no identified feature, so its neighbours cannot be looked up.");
			return;
		}

		var jobModel = this.getParent("PA_Step4JobView") ? this.getParent("PA_Step4JobView").getModel() : null;
		var request = paNeighbourRequest({
			featureID: mainFeature.getID(),
			featureType: mainFeature.getFeatureType(),
			neighbourMap: jobModel ? jobModel.getCompoundRegulateFeatures() : null,
			level: $("#inputLevel").val()
		});

		if (!request.ok) {
			note(request.message);
			return;
		}

		/* Same floor the resize handler applies to every .PA_step5_plotContainer
		   in this panel: the container is about 370px wide, so the bare
		   `width - 400` this used to emit was a negative CSS length. */
		var divWidth = Math.max(300, elem.width() - 400);
		var distributionSummaries = this.getParent("PA_Step4PathwayView").getDataDistributionSummaries();
		var visualOptions = this.getParent("PA_Step4PathwayView").getVisualOptions();
		var replicateMode = (jobModel && jobModel.getReplicateMode) ? jobModel.getReplicateMode() : "replicates";
		var globalExpressionData = jobModel ? jobModel.getGlobalExpressionData() : {};

		/* One block per omic the neighbours can carry values in: neighbours are
		   gene ids, and a KEGG compound id can also name a measured metabolite. */
		var blocks = [
			{omicName: "Gene expression", divId: "Gene_expression_heatmapContainer_regulate",
			 source: globalExpressionData.inputGene},
			{omicName: "Metabolomics", divId: "Compound_expression_heatmapContainer_regulate",
			 source: globalExpressionData.inputCompound}
		];

		var painted = 0;

		for (var b = 0; b < blocks.length; b++) {
			var block = blocks[b];
			var measured = [];

			for (var i = 0; i < request.neighbours.length; i++) {
				var omicValue = block.source ? block.source[request.neighbours[i]] : undefined;
				if (omicValue) {
					measured.push(omicValue);
				}
			}

			var rows = paNeighbourRows(measured, replicateMode);
			if (rows.length === 0) {
				continue;
			}
			painted++;

			elem.append(
				"<div class='contentbox'>" +
				"  <h3>" + block.omicName + "<span><input type='checkbox' id='" + block.divId + "_cb_relevant' value='" + block.omicName + "'/>Only relevant</span></h3>" +
				"  <div class='PA_step5_heatmapContainer' id='" + block.divId + "' style='height: " + ((rows.length * 30) + 100) + "px'><i class='fa fa-cog fa-spin'></i> Loading..</div>" +
				"  <div class='PA_step5_plotContainer' id='" + block.divId + "_plotContainer' style='width:" + divWidth + "px;height: " + ((rows.length * 30) + 100) + "px'><i class='fa fa-cog fa-spin'></i> Loading..</div>" +
				"</div>");

			/* Bound per block so each checkbox redraws its own pair. Highcharts
			   appends to whatever is in renderTo, so the previous chart is
			   destroyed rather than left underneath the new one. */
			(function (block, rows) {
				var charts = [];

				var draw = function (visibleRows) {
					for (var c = 0; c < charts.length; c++) {
						charts[c] && charts[c].destroy();
					}
					$("#" + block.divId).height(visibleRows.length * 30 + 100);
					charts = [
						me.generateHeatmap(block.divId, block.omicName, visibleRows, distributionSummaries, visualOptions),
						me.generatePlot(block.divId + "_plotContainer", block.omicName, visibleRows, distributionSummaries, block.divId + "_plotlegendContainer", visualOptions)
					];
				};

				draw(rows);

				$("#" + block.divId + "_cb_relevant").change(function () {
					/* Highcharts does not hide the y labels of hidden series, so
					   redrawing beats toggling visibility. isRelevant is a
					   boolean on these rows -- paNeighbourRows() resolved the
					   OmicValue methods, which is what the old
					   `x.isRelevant || x.isRelevantAssociation` never did. */
					draw($(this).is(":checked")
						? rows.filter(function (row) { return row.isRelevant || row.isRelevantAssociation; })
						: rows);
				});
			})(block, rows);
		}

		if (painted === 0) {
			note("This metabolite has " + request.neighbours.length + " neighbour" +
				(request.neighbours.length === 1 ? "" : "s") + " at " + request.level +
				" step" + (request.level === 1 ? "" : "s") +
				", but none of them carry measured values in the omics you uploaded.");
		}
	};

	this.initComponent = function () {
		var me = this;


		this.component = Ext.widget({
			xtype: "container", cls: "lateralOptionsPanel", flex: 0, width: 400, minWidth: 400,  height: ($("#mainViewCenterPanel").height() - 100),
			resizable: {
				handles: 'w',
				listeners: {
					beforeresize: function () {
						return !me.isExpanded;
					},
					resize: function (resizer, width, height) {
						me.getParent().adjustChildrenWidth();
					}
				}
			},
			items: [{
				xtype: 'box', html:
				'<div class="lateralOptionsPanel-header" data-guides="ignore">' +
				'  <div class="lateralOptionsPanel-toolbar">' +
				'    <a class="toolbarOption btn-secondary helpTip" id="hideFeatureSetButton" title="Hide this panel"><i class="fa fa-times"></i></a>' +
				'    <a class="toolbarOption btn-secondary helpTip" id="expandFeatureSetButton" title="Expand this panel"><i class="fa fa-expand"></i></a>' +
				'    <a class="toolbarOption btn-secondary helpTip" id="shrinkFeatureSetButton" style="display:none;"  title="Shrink this panel"><i class="fa fa-compress"></i></a>' +
				'  </div>' +
				"  <h2>Feature set overview</h2>" +
				"</div>"
			},{
				xtype: "container", cls: "lateralOptionsPanel-body",
				items: [
					{xtype: "box", html: '<h2> Features in this set </h2>'},
					{xtype: "container", itemId: "itemsContainer", style:"padding:10px;", items: []},

					{xtype: "box", html: '<h2> Values by omic type </h2>'},
					{xtype: 'box', html: "<div id='featureFamilyOverviewContainer'></div>"},


					/* Metabolites only -- neighbours come from the KEGG compound
					   interaction network, so a gene box has nothing to look up.
					   Hidden by default and revealed in updateObserver() for the
					   feature sets it applies to; it used to be offered on every
					   box, where pressing it could only ever do nothing.
					   The "Searching..." spinner that sat under the button is
					   gone: div.applyWaitMessage is display:none in main.css and
					   nothing ever faded this one in, so it promised feedback
					   the button did not give. */
					{xtype: "box", itemId: "neighbouringFeaturesSection", hidden: true, html:
						'<h2>Neighbouring features</h2>' +
						'  <div>' +
						'    Please enter a level (1-4): <input type="number" min="1" max="4" style="width:80px;height:30px"  id="inputLevel">' +
						'    <a class="button btn-info helpTip" id="showFeatureButton" title="Show the neighbours of this metabolite at the given number of network steps"><i class="fa fa-search"></i> Show Features</a>' +
						'  </div>'
					},
					{xtype: 'box', html: "<div id='featureFamilyOverviewContainerRegulate'></div>"}



				]
			}],
			listeners: {
				boxready: function () {
					$("#hideFeatureSetButton").click(function () {
						me.getParent().hideFeatureSetDetails();
					});
					$("#expandFeatureSetButton").click(function () {
						me.expand();
					});
					$("#shrinkFeatureSetButton").click(function () {
						me.shrink();
					});
					$("#showFeatureButton").click(function () {
						me.showNeighbouringFeatures();
					});
					initializeTooltips(".helpTip");
				},
				beforedestroy: function () {
					me.getModel().deleteObserver(me);
				},
				resize: function (view, width) {
					var componentHeight = $(view.getEl().dom).outerHeight();
					var headerHeight = $(view.getEl().dom).find(".lateralOptionsPanel-header").outerHeight() + 10;
					$(view.getEl().dom).find(".lateralOptionsPanel-body").height($("#mainViewCenterPanel").height() - headerHeight - 100);

					$(".PA_step5_plotContainer").width(Math.max(300, width - 400));
					$(".PA_step5_plotContainer .highcharts-container").width(Math.max(300, width - 400));
					$(".PA_step5_plotContainer").each(function () {
						$(this).highcharts().reflow();
					});
				}
			}
		});

		return this.component;
	};

	return this;
}
PA_Step4DetailsView.prototype = new View();

var addTableEntrie = function (entriesValue, omicValue, featureName, entrieName, replicateMode) {
			// `replicateMode` was added so the Details panel ("Show details")
			// honours the visual-options sample/replicate toggle. For compound
			// omics — which nest OmicValues — `omicValue.getValues()` returns
			// the inner OmicValues, not numeric arrays, so we don't pass mode
			// at the outer call (no aggregation lives there).
			if (omicValue.isCompoundOmicsValue()) {
				var omicValues = omicValue.getValues();
				for (var i in omicValues) {
					addTableEntrie(entriesValue, omicValues[i], featureName, entrieName + omicValue.getName() + "#", replicateMode);
				}
			} else if (omicValue.isVisibleAtFeatureFamilyDetails()) {
				if (entrieName === "") {
					entrieName = omicValue.getOmicName();
				}
				if (entriesValue[entrieName] == null) {
					entriesValue[entrieName] = [];
				}
				// Regulator omics (TF / miRNA / methylation / any omic with associations)
				// flip the primary/secondary roles so the regulator is the row
				// identifier. Two cases for the secondary side:
				//   * Symbol resolved → show the regulator's canonical AGI there
				//     (e.g. "WRKY40#AT2G25000"). Most useful when you want to look
				//     up the regulator in an external database.
				//   * Symbol not resolved (e.g. miRNA names) → fall back to the
				//     target's symbol so the row still carries the regulator→target
				//     context (e.g. "miR156#NAC001"), matching the global heatmap.
				// `linkKey` keeps the cross-heatmap hover linkage anchored on the
				// target symbol (same as gene-expression / other-omic rows).
				var isRegulatorRow = !!(omicValue.isRegulator);
				var keggNameSuffix = (entrieName === omicValue.getOmicName()) ? "" : " " + omicValue.getOmicName();
				var hasResolvedRegulatorID = isRegulatorRow && omicValue.regulatorID && omicValue.regulatorID !== omicValue.originalName;
				// Per-condition significance (one boolean per cell) so the Details
				// heatmap can draw the white star on significant cells in
				// multi-condition data — same contract as the pathway-box tooltip
				// and global heatmaps. isRelevant(j, mode) is an O(1) array lookup.
				var dvValues = omicValue.getValues(replicateMode);
				var dvSignificance = [];
				for (var c = 0; c < (dvValues ? dvValues.length : 0); c++) {
					dvSignificance.push(omicValue.isRelevant(c, replicateMode) === true);
				}
				entriesValue[entrieName].push({
					keggName: isRegulatorRow
						? (omicValue.originalName + keggNameSuffix)
						: (featureName + keggNameSuffix),
					inputName: isRegulatorRow
						? (hasResolvedRegulatorID ? omicValue.regulatorID : featureName)
						: omicValue.inputName,
					originalName: isRegulatorRow ? undefined : omicValue.originalName,
					linkKey: featureName,
					isRelevant: omicValue.isRelevant(undefined, replicateMode),
					isRelevantAssociation: omicValue.isRelevantAssociation(),
					significance: dvSignificance,
					values: dvValues
				});
			}
		};

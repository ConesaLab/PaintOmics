//# sourceURL=JobInstanceModels.js
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
* - JobInstance
*
*/

function JobInstance(jobID) {
	this.jobID = jobID;
	this.userID;
	this.stepNumber = 1;
	this.organism = null;
	this.name = null;



	this.pathways = [];
	this.databases = null;
	this.clusters = null;
	this.summary = null;
	this.mappingSummary = null;

	this.geneBasedInputOmics = null;
	this.compoundBasedInputOmics = null;

	this.omicsHeaders = null;
	this.omicsValues = null;
	this.omicsValuesID = null;
	this.foundCompounds = [];

	// Replicate display mode for the Step-4 visualization. ``null`` means
	// "use default": getReplicateMode() returns "samples" when any omic has
	// aggregation applied, "replicates" otherwise. Becomes a string only
	// when the user explicitly toggles the visual-options switch.
	this.replicateMode = null;

	this.selectedPathway = null;
	this.timestamp = null;
	
	this.readOnly = false;
	this.allowSharing = false;

	// add Metabolism Classification Part
	this.mappingComp = null;
	this.pValueInDict = null;
	this.classificationDict = null;
	this.exprssionMetabolites = null;
	this.adjustPvalue = null;
	this.totalRelevantFeaturesInCategory = null;
	this.compoundRegulateFeatures =null;
	this.globalExpressionData = null;
	this.hubAnalysisResult = null;
	// MORE RegulationPerCondition table (Step 3 panel). Shape when populated:
	// {columns: [...], rows: [[...], ...], truncated: bool}. Stays null when
	// MORE wasn't used, so the panel hides itself.
	this.regulationPerConditionData = null;

	// PaintOmics 4
	this.classes = [];


	/*****************************
	** GETTERS AND SETTERS
	*****************************/
	this.setJobID = function (jobID) {
		this.jobID = jobID;
	};
	this.getJobID = function () {
		return this.jobID;
	};
	this.setUserID = function (userID) {
		this.userID = userID;
	};
	this.getUserID = function () {
		return this.userID;
	};
	this.setTimestamp = function(timestamp) {
		this.timestamp = timestamp;
	};
	this.getTimestamp = function() {
		return this.timestamp;	
	};
	this.setName = function (name) {
		this.name = name;
	};
	this.getName = function () {
		return this.name;
	};
	this.setReadOnly = function(readOnly) {
		this.readOnly = readOnly;
	};
	this.getReadOnly = function() {
		return this.readOnly;	
	};
	this.setAllowSharing = function(allowSharing) {
		this.allowSharing = allowSharing;
	};
	this.getAllowSharing = function() {
		return this.allowSharing;	
	};
	this.setStepNumber = function (stepNumber) {
		this.stepNumber = stepNumber;
	};
	this.getStepNumber = function () {
		return this.stepNumber;
	};
	this.setOrganism = function (organism) {
		this.organism = organism;
	};
	this.getOrganism = function () {
		return this.organism;
	};

	this.setClasses = function (classes) {
		this.classes = classes;
	}

	this.getClasses = function () {
		return this.classes;
	}

	//this.getClass = function (classID) {
	//	for (var i in this.classes) {
	//		if (classID == this.classes[i].getID()) {
	//			return this.classes[i];
	//		}
	//	}
	//	return null;
	//};

	//this.addClass = function (classID) {
	//	this.classes.push(classID);
	//};



	this.setPathways = function (pathways) {
		this.pathways = pathways;
	};
	this.getPathways = function () {
		return this.pathways;
	};
	this.getPathwaysByDB = function (db) {
		return this.pathways.filter(function(pathway) {
			return pathway.getSource() == db
		});
	};
	this.getPathway = function (pathwayID) {
		for (var i in this.pathways) {
			if (pathwayID == this.pathways[i].getID()) {
				return this.pathways[i];
			}
		}
		return null;
	};
	this.addPathway = function (pathway) {
		//TODO: CHECK CLASSES?
		this.pathways.push(pathway);
	};
    this.getOmicNames = function() {
        var omicNames = this.getGeneBasedInputOmics().concat(this.getCompoundBasedInputOmics()).map(x => x.omicName);
        
        return omicNames;
	};
	this.getGeneOmicNames = function() {
        var omicNames = this.getGeneBasedInputOmics().map(x => x.omicName);
        
        return omicNames;
	};
	this.getCompoundOmicNames = function() {
        var omicNames =this.getCompoundBasedInputOmics().map(x => x.omicName);
        
        return omicNames;
	};
	this.getDatabases = function() {
		if (! this.databases || ! this.databases.length) {
			// Check databases present in pathways
			var pathways = this.getPathways();
			// ES6/ES2015
			this.databases = [...new Set(pathways.map(item => item.getSource()))];
		}
		return this.databases.sort();
	}
	this.setDatabases = function (databases) {
		this.databases = databases;
	};
	this.getClusterNumber = function() {
		// Double check in case they are empty.
		var me = this;
		
		if (this.clusters === null || me.getDatabases().every(function(db) {
			return Object.keys(me.clusters[db]).every(function(x) { return !me.clusters[db][x].size; })})) {
			this.clusters = {};
			
			// For each database initialize a new set containing all omic names
			this.getDatabases().forEach(function(db) {
				this.clusters[db] = {};
				
				this.getOmicNames().map(x => this.clusters[db][x] = new Set());
				
				// For each pathway and each omic present in its metagene info,
				// add to the set the different clusters found.
				this.getPathwaysByDB(db).forEach(function(pathway) {
					var metagenes = pathway.getMetagenes();
					
					Object.keys(metagenes).forEach(function(omicName) {
						metagenes[omicName].map(x => this.clusters[db][omicName].add(x.cluster));
					}, this);
				}, this);
			}, this);
		}
		
		return this.clusters;
	};
	this.getClusterNumberByDbAndOmic = function(database, omic) {
		return this.getClusterNumber()[database][omic];
	};
	this.setClusterNumber = function(clusters) {
		this.clusters = clusters;
	};
	this.setSummary = function (summary) {
		this.summary = summary;
	};
	this.getSummary = function () {
		return this.summary;
	};
	this.setMappingSummary = function (mappingSummary) {
		this.mappingSummary = mappingSummary;
	};
	this.getMappingSummary = function () {
        
        if (this.mappingSummary == null) {
            var mappingSummary = {};
            
            this.getGeneBasedInputOmics().concat(this.getCompoundBasedInputOmics()).map(function(currentValue, index, omics) {
                var mappedInfo = currentValue.omicSummary[0];
                var mappedFeatures = Number.isInteger(mappedInfo) ? mappedInfo : (parseFloat(mappedInfo.Total || mappedInfo[Object.keys(mappedInfo)[0]]));
                var unmappedFeatures = parseFloat(currentValue.omicSummary[1]);
                
                mappingSummary[currentValue.omicName] = {mapped: mappedFeatures, unmapped: unmappedFeatures};
            });
            
            this.mappingSummary = mappingSummary;
        }
        
		return this.mappingSummary;
	};
	this.setGeneBasedInputOmics = function (geneBasedInputOmics) {
		this.geneBasedInputOmics = geneBasedInputOmics;
	};
	this.getGeneBasedInputOmics = function () {
		return this.geneBasedInputOmics == null ? [] : this.geneBasedInputOmics;
	};
	this.setCompoundBasedInputOmics = function (compoundBasedInputOmics) {
		this.compoundBasedInputOmics = compoundBasedInputOmics;
	};
	this.getCompoundBasedInputOmics = function () {
		return this.compoundBasedInputOmics == null ? [] : this.compoundBasedInputOmics;
	};
	this.setFoundCompounds = function (foundCompounds) {
		this.foundCompounds = foundCompounds;
	};
	this.getFoundCompounds = function () {
		return this.foundCompounds;
	};
	this.addFoundCompound = function (compoundSet) {
		//TODO: CHECK CLASSES?
		this.foundCompounds.push(compoundSet);
	};
	this.getOmicHeaders = function(omicName = null, mode = null) {
		// In "samples" mode the renderer should label cells with the
		// biological-sample names from the design (or the auto-detection),
		// not the original per-replicate column headers. We re-derive a
		// fresh dict each call in this mode because `sampleHeader` may have
		// been updated by /pa_apply_replicate_mapping after the page loaded.
		if (mode === "samples") {
			var headers = {};
			this.getGeneBasedInputOmics().concat(this.getCompoundBasedInputOmics()).forEach(function(omic) {
				if (Array.isArray(omic.sampleHeader) && omic.sampleHeader.length) {
					// Prepend a placeholder so the lookup convention from
					// the replicates path (omicHeader[idx + 1]) keeps working
					// uniformly across modes.
					headers[omic.omicName] = [""].concat(omic.sampleHeader);
				} else {
					headers[omic.omicName] = omic.omicHeader;
				}
			});
			return headers;
		}

		if (this.omicHeaders == null) {

			this.omicHeaders = {};

			this.getGeneBasedInputOmics().concat(this.getCompoundBasedInputOmics()).map(function(currentValue, index, omics) {
				this.omicHeaders[currentValue["omicName"]] = currentValue["omicHeader"];
			}.bind(this));
		}

		return this.omicHeaders;
	};

	/**
	 * Active replicate-display mode for this job ("replicates" or "samples").
	 *
	 * Default behaviour: if the user has never explicitly set a mode in this
	 * session AND at least one omic has a sample mapping applied (server-side
	 * auto-apply or manual override), default to "samples" — that's the view
	 * the user is implicitly asking for when they upload `_R1/_R2`-style data.
	 * Setting the mode explicitly via setReplicateMode() pins it for the rest
	 * of the session (otherwise toggling back to "replicates" via the visual-
	 * options panel would be undone on the next read).
	 */
	this.getReplicateMode = function() {
		if (this.replicateMode) return this.replicateMode;
		return this.hasAnyReplicateAggregation && this.hasAnyReplicateAggregation()
			? "samples"
			: "replicates";
	};
	this.setReplicateMode = function(mode) {
		this.replicateMode = (mode === "samples") ? "samples" : "replicates";
		return this;
	};

	/**
	 * Returns true if at least one omic of this job has had a replicate→sample
	 * mapping applied (auto or manual). The Step-4 toggle is only rendered
	 * when this is true; a job with no replicates can't aggregate.
	 */
	this.hasAnyReplicateAggregation = function() {
		var omics = this.getGeneBasedInputOmics() || [];
		omics = omics.concat(this.getCompoundBasedInputOmics() || []);
		for (var i = 0; i < omics.length; i++) {
			if (Array.isArray(omics[i].sampleHeader) && omics[i].sampleHeader.length > 0) {
				return true;
			}
		}
		return false;
	};




	this.updatePathway = function (pathway) {
		for (var i in this.pathways) {
			if (pathway.getID() == this.pathways[i].getID()) {
				this.pathways.splice(i, 1);
				break;
			}
		}
		this.pathways.push(pathway);
	};
	this.setSelectedPathway = function (pathwayID) {
		this.selectedPathway = pathwayID;
	};
	this.getSelectedPathway = function () {
		return this.selectedPathway;
	};
	this.setOmicsValues = function (omicsValues) {
		this.omicsValues = omicsValues;
	};
	this.getOmicsValues = function () {
		if (this.omicsValues === null) {
			this.omicsValues = {};
		}
		return this.omicsValues;
	};
	this.setOmicsValuesID = function (omicsValuesID) {
		this.omicsValuesID = omicsValuesID;
	};
	this.getOmicsValuesID = function () {
		return this.omicsValuesID;
	};
	this.addOmicValue = function (omicsValue) {
		this.getOmicsValues()[omicsValue.getID()] = omicsValue;
	};


	// add Metabolism method
	this.setMappingComp = function (mappingComp) {
		this.mappingComp = mappingComp;
	}

	this.getMappingComp = function () {
		return this.mappingComp == null ? [] : this.mappingComp;
	}


	this.setpValueInDict = function (pValueInDict) {
		this.pValueInDict = pValueInDict;
	}

	this.getpValueInDict = function () {
		return this.pValueInDict == null ? {} : this.pValueInDict;
	}

	this.setExprssionMetabolites = function (exprssionMetabolites) {
		this.exprssionMetabolites = exprssionMetabolites;
	}

	this.getExprssionMetabolites = function () {
		return this.exprssionMetabolites == null ? {} : this.exprssionMetabolites;
	}
	this.setAdjustPvalue = function (adjustPvalue) {
		this.adjustPvalue = adjustPvalue
	}
	this.getAdjustPvalue = function () {
		return this.adjustPvalue == null ? {} : this.adjustPvalue
	}
	this.setTotalRelevantFeaturesInCategory = function (totalRelevantFeaturesInCategory) {
		this.totalRelevantFeaturesInCategory = totalRelevantFeaturesInCategory
	}
	this.getTotalRelevantFeaturesInCategory = function () {
		return this.totalRelevantFeaturesInCategory == null ? {} : this.totalRelevantFeaturesInCategory
	}

	this.setCompoundRegulateFeatures = function (compoundRegulateFeatures) {
		if (typeof compoundRegulateFeatures === 'string') {
			try { this.compoundRegulateFeatures = JSON.parse(compoundRegulateFeatures); }
			catch (e) { this.compoundRegulateFeatures = {}; }
		} else {
			this.compoundRegulateFeatures = compoundRegulateFeatures || {};
		}
	}
	this.getCompoundRegulateFeatures = function () {
		return this.compoundRegulateFeatures == null ? {} : this.compoundRegulateFeatures
	}

	this.setGlobalExpressionData = function (globalExpressionData) {
		this.globalExpressionData = { "inputGene": {}, "inputCompound": {} };
		if (globalExpressionData) {
			if (globalExpressionData.inputGene) {
				for (var id in globalExpressionData.inputGene) {
					var ov = globalExpressionData.inputGene[id];
					this.globalExpressionData.inputGene[id] = (ov instanceof OmicValue) ? ov : OmicValue.loadFromJSON(ov);
				}
			}
			if (globalExpressionData.inputCompound) {
				for (var id in globalExpressionData.inputCompound) {
					var ov = globalExpressionData.inputCompound[id];
					this.globalExpressionData.inputCompound[id] = (ov instanceof OmicValue) ? ov : OmicValue.loadFromJSON(ov);
				}
			}
		}
	}
	this.getGlobalExpressionData = function () {
		return this.globalExpressionData == null ? {} : this.globalExpressionData
	}
	this.getHubAnalysisResult = function () {
		return this.hubAnalysisResult == null ? {} : this.hubAnalysisResult
	}

	this.setHubAnalysisResult = function (hubAnalysisResult) {
		this.hubAnalysisResult = hubAnalysisResult
	}

	// Returns the rpc payload as-is (null when MORE wasn't used). Step 7's view
	// branches on truthiness + rows.length, so null is the right sentinel here —
	// don't default to {} (would yield an empty grid container instead of hide).
	this.getRegulationPerConditionData = function () {
		return this.regulationPerConditionData
	}

	this.setRegulationPerConditionData = function (regulationPerConditionData) {
		this.regulationPerConditionData = regulationPerConditionData
	}

	this.setFeatureSummary = function (featureSummary) {
		this.featureSummary = featureSummary
	}
	this.getFeatureSummary = function () {
		return this.featureSummary == null ? {} : this.featureSummary
	}

	/* Class-map metadata: {parents: {class: briteLevel1}, nullProportion: [p0
	   per condition], thresholdSource: "user"|"auto"}. The parent lets the map
	   group classes by chemical family -- without it the nine steroid classes
	   read as "18-Carbon atoms".."30-Carbon atoms" -- and nullProportion is the
	   value the binomial was actually run against, which no client can derive
	   because the auto branch differs per condition. */
	this.setClassificationMeta = function (classificationMeta) {
		this.classificationMeta = classificationMeta;
	}

	this.getClassificationMeta = function () {
		return this.classificationMeta == null ? {} : this.classificationMeta;
	}

	/* Class activity at BRITE levels 1-3 with the test that ran ("permutation"
	   on replicates, or "binomial" on the relevant list), per-class statistics,
	   per-metabolite F/p and the metabolites that reached no class. null on a
	   job stored before the field existed; the Step 3 view then falls back to
	   the level-2 ranking it always drew. */
	/* A recovered job comes back through DAO.adaptBSON, which turns every
	   None leaf into the STRING "None": an untestable metabolite's F, an
	   absent alpha, a missing value in a strip. The ladder reads those as
	   numbers ((b.F || 0) is NaN, the tooltip says "NaN log2 FC"), so they
	   are put back to null here, once, at the only entry point. Text-bearing
	   keys are left alone: a metabolite really could be called "None". */
	var CLASS_ACTIVITY_TEXT_KEYS = {name: 1, key: 1, parent: 1, path: 1, members: 1, kegg: 1,
		conditions: 1, warnings: 1, unmatched: 1, unclassified: 1, levels: 1, samples: 1,
		strata: 1, labels: 1, label: 1, id: 1, test: 1, nullKind: 1, levelNames: 1};
	function classActivityUnNone(value, key) {
		if (value === "None") return null;
		if (Array.isArray(value)) {
			return CLASS_ACTIVITY_TEXT_KEYS[key] ? value : value.map(function (v) { return classActivityUnNone(v, null); });
		}
		if (value && typeof value === "object") {
			var out = {};
			Object.keys(value).forEach(function (k) {
				out[k] = (CLASS_ACTIVITY_TEXT_KEYS[k] && typeof value[k] === "string") ? value[k] : classActivityUnNone(value[k], k);
			});
			return out;
		}
		return value;
	}
	this.setClassActivity = function (classActivity) {
		this.classActivity = (classActivity && typeof classActivity === "object") ? classActivityUnNone(classActivity, null) : null;
	}
	this.getClassActivity = function () {
		return this.classActivity == null ? null : this.classActivity;
	}
	this.setClassificationDict = function (classificationDict) {
		this.classificationDict = classificationDict;
	}

	this.getClassificationDict = function () {
		return this.classificationDict == null ? [] : this.classificationDict;
	}


	this.getMultiplePvaluesMethods = function() {
		var multipleMethods = [];

		if (this.pathways.length && this.pathways[0].getAdjustedSignificanceValues) {
			var omicPvalues = this.pathways[0].getAdjustedSignificanceValues();

			multipleMethods = omicPvalues && Object.keys(omicPvalues).length ? Object.keys(omicPvalues[Object.keys(omicPvalues)[0]]) : [];
		}

		return multipleMethods;
	};
	this.getCombinedPvaluesMethods = function() {
		var combinedMethods = [];

		if (this.pathways.length && this.pathways[0].getCombinedSignificanceValues) {
			var omicPvalues = this.pathways[0].getCombinedSignificanceValues();

			combinedMethods = Object.keys(omicPvalues);
		}

		return combinedMethods;
	};

	/**
	* This function returns the values for min/max for each omic type.
	* This information is calculated during first step and returned as part of
	* the matching summary.
	* The information is stored as part of the Objects stored in geneBasedInputOmics
	* and compoundBasedInputOmics, but using this function we adapt extract only the min/max
	* (i.e. p10 and p90) and store it in an appropriate format (which PA_Step4PathwayView).
	*
	* @returns {minMaxValues}, an object containing 2 arrays: pos 0 contains the min values
	*   for each omic (indexed by omic name), and pos 1 contains max values for each omic
	*   (indexed by omic name)
	*/
	this.getDataDistributionSummaries = function (omicName) {
		//   0        1       2    3   4     5     6    7    8     9       10      11          12
		//[MAPPED, UNMAPPED, MIN, P10, Q1, MEDIAN, Q3, P90, MAX, MIN_IR, Max_IR, MIN_CUSTOM, MAX_CUSTOM]
		if(this.dataDistributionSummaries === undefined){
			this.dataDistributionSummaries = {};

			var omicsAux = this.getGeneBasedInputOmics();
			for (var i in omicsAux) {
				if (omicsAux[i].omicSummary === undefined) {
					showWarningMessage("No information about min/max available.", {
						message: "The current job instance do not include information about min/max values for each omic type.</br>" +
						"A possible explanation for this issue could be that the data was generated using an older version of PaintOmics.</br>" +
						"Instead of using the percentiles 10 and 90 for each omics as reference to obtain the colors for the heatmap, PaintOmics AI " +
						"will calculate locally the min / max for each omics for each selected pathway.", showButton: true, height: 260
					});
					return null;
				}
				//TODO: SAVE SUMMARY AS DICT??
				this.dataDistributionSummaries[omicsAux[i].omicName] = omicsAux[i].omicSummary;
			}
			omicsAux = this.getCompoundBasedInputOmics();
			for (var i in omicsAux) {
				if (omicsAux[i].omicSummary === undefined) {
					showWarningMessage("No information about min/max available.", {
						message: "The current job instance do not include information about min/max values for each omic type.</br>" +
						"A possible explanation for this issue could be that the data was generated using an older version of PaintOmics.</br>" +
						"Instead of using the percentiles 10 and 90 for each omics as reference to obtain the colors for the heatmap, PaintOmics AI" +
						" will calculate locally the min / max for each omics for each selected pathway."
					});
					return null;
				}
				//TODO: SAVE SUMMARY AS DICT??
				this.dataDistributionSummaries[omicsAux[i].omicName] = omicsAux[i].omicSummary;
			}
		}


		return (omicName? this.dataDistributionSummaries[omicName]: this.dataDistributionSummaries);
	};

	this.setDataDistributionSummaries = function(dataDistributionSummaries, omicName) {
		if (omicName) {
			this.dataDistributionSummaries[omicName] = dataDistributionSummaries;
		} else {
			this.dataDistributionSummaries = dataDistributionSummaries;
		}
 	};

	this.loadFromJSON = function (jsonObject) {
		for(var i in jsonObject){
			if(i === "geneBasedInputOmics"){
				this.geneBasedInputOmics = jsonObject.geneBasedInputOmics;
				this.geneBasedInputOmics.sort(function(a,b) {return (a.omicName > b.omicName) ? 1 : ((b.omicName > a.omicName) ? -1 : 0);} );
			}else if(i === "compoundBasedInputOmics"){
				this.compoundBasedInputOmics = jsonObject.compoundBasedInputOmics;
				this.compoundBasedInputOmics.sort(function(a,b) {return (a.omicName > b.omicName) ? 1 : ((b.omicName > a.omicName) ? -1 : 0);} );
			}else if(i === "foundCompounds"){
				this.foundCompounds = [];
				for (var i in jsonObject.foundCompounds){
					this.addFoundCompound(new CompoundSet("").loadFromJSON(jsonObject.foundCompounds[i]));
				}
			}else if(i === "pathways"){
				this.pathways = [];
				for (var i in jsonObject.pathways){
					this.addPathway(new Pathway("").loadFromJSON(jsonObject.pathways[i]));
				}
			}else if(i === "omicsValues"){
				this.omicsValues = {};
				for (var i in jsonObject.omicsValues){
					this.addOmicValue(new Feature(i).loadFromJSON(jsonObject.omicsValues[i]));
				}
			}else if(i === "globalExpressionData"){
				// Delegate to the setter, which is the one place that knows how to
				// normalise this field. Restoring used to re-implement it here and
				// branch on the *presence* of the key, then dereference the value:
				// every model cached before Step 3 stores globalExpressionData ===
				// null (see the constructor), so a restore threw
				// "Cannot read properties of null (reading 'inputGene')" inside
				// Application.launch() and every later page load died the same way
				// until sessionStorage was cleared by hand.
				// Only leaving the branch out is not enough either: the views index
				// the field without a guard (PA_Step3Views.js:5343,
				// PA_Step4Views.js:4389), so a restored model must always end up
				// with the {inputGene:{}, inputCompound:{}} shape, never with null.
				this.setGlobalExpressionData(jsonObject.globalExpressionData);
			}else{
				this[i] = jsonObject[i];
			}
		}
	};
}
JobInstance.prototype = new Model();

//# sourceURL=FeatureModels.js
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
* - Feature
* - OmicValue
* - Gene
* - Compound
* - CompoundSet
* - FeatureSet
* - FeatureSetElem
*
*/

/**
*
* @param {type} name
* @returns {Feature}
*/
function Feature(name) {
	this.ID = "";
	this.name = name;
	this.url = "";
	this.featureType = "";
	this.omicsValues = [];

	this.selected = false;

	this.isMetagene = false;

	/*****************************
	** GETTERS AND SETTERS
	*****************************/
	this.setID = function(ID) {
		this.ID = ID;

		return this;
	};
	this.getID = function() {
		return this.ID;
	};
	this.setName = function(name) {
		this.name = name;

		return this;
	};
	this.getName = function() {
		return this.name;
	};
	this.setUrl = function(url) {
		this.url = url;

		return this;
	};
	this.getUrl = function() {
		return this.url;
	};
	this.setSignificative = function(significative) {
		this.significative = significative;

		return this;
	};
	this.isSignificative = function() {
		return this.significative;
	};
	this.setFeatureType = function(featureType) {
		this.featureType = featureType;

		return this;
	};
	this.getFeatureType = function() {
		return this.featureType;
	};
	this.addOmicsValues = function(omicsValues) {
		this.omicsValues.push(omicsValues);

		return this;
	};
	this.setOmicsValues = function(omicsValues) {
		this.omicsValues = omicsValues;

		return this;
	};
	this.getOmicsValues = function() {
		return this.omicsValues;
	};
	this.getOmicValues = function(omicName, all=false) {
		var detectedValues = this.omicsValues.filter(x => x.getOmicName() === omicName);

		return detectedValues.length ? (all ? detectedValues : detectedValues[0]) : null;
	};
	this.setSelected = function(selected) {
		this.selected = selected;

		return this;
	};
	this.isSelected = function() {
		return this.selected;
	};
	this.isRelevant = function() {
		for (var i in this.omicsValues) {
			if (this.omicsValues[i].isRelevant() === true) {
				return true;
			}
		}
		return false;
	};
	this.isRelevantAssociation = function() {
		for (var i in this.omicsValues) {
			if (this.omicsValues[i].isRelevantAssociation() === true) {
				return true;
			}
		}
		return false;
	};
	this.setMetagene = function(isMetagene) {
		this.isMetagene = isMetagene;

		return this;
	};
	this.isMetagene = function() {
		return this.isMetagene;
	};
	/********************************************
	** OTHER FUNCTIONS
	********************************************/
	this.loadFromJSON = function(jsonObject) {
		for(var i in jsonObject){
			if(i === "omicsValues"){
				if (jsonObject.omicsValues !== undefined) {
					this.omicsValues = [];
					for (var i in jsonObject.omicsValues) {
						this.omicsValues.push(OmicValue.loadFromJSON(jsonObject.omicsValues[i]));
					}
				}
			}else{
				this[i] = jsonObject[i];
			}
		}
		return this;
	};
}
Feature.prototype = new Model;

/**
*
* @param {type} ID
* @returns {Gene}
*/
function Gene(ID) {
	this.ID = ID;
}
Gene.prototype = new Feature;
/**
*
* @param {type} name
* @returns {Compound}
*/
function Compound(name) {
	this.name = name;
}
Compound.prototype = new Feature;
/**
*
* @param {type} title
* @returns {CompoundSet}
*/
function CompoundSet(title) {
	this.title = title;
	this.mainCompounds = [];
	this.otherCompounds = [];

	/*****************************
	** GETTERS AND SETTERS
	*****************************/
	this.setTitle = function(title) {
		this.title = title;
	};
	this.getTitle = function() {
		return this.title;
	};
	this.setMainCompounds = function(mainCompounds) {
		this.mainCompounds = mainCompounds;
	};
	this.getMainCompounds = function() {
		return this.mainCompounds;
	};
	this.findMainCompound = function(compoundCode) {
		for (var i in this.mainCompounds) {
			if (this.mainCompounds[i].getID() === compoundCode) {
				return this.mainCompounds[i];
			}
		}
		return null;
	};
	this.setOtherCompounds = function(otherCompounds) {
		this.otherCompounds = otherCompounds;
	};
	this.getOtherCompounds = function() {
		return this.otherCompounds;
	};
	this.findOtherCompound = function(compoundCode) {
		for (var i in this.otherCompounds) {
			if (this.otherCompounds[i].getID() === compoundCode) {
				return this.otherCompounds[i];
			}
		}
		return null;
	};
	this.loadFromJSON = function(jsonObject) {
		if (jsonObject.title !== undefined) {
			this.title = jsonObject.title;
		}

		if (jsonObject.mainCompounds !== undefined) {
			var compound = null;
			for (var i in jsonObject.mainCompounds) {
				compound = new Compound();
				compound.loadFromJSON(jsonObject.mainCompounds[i]);
				this.mainCompounds.push(compound);
			}
			for (var i in jsonObject.otherCompounds) {
				compound = new Compound();
				compound.loadFromJSON(jsonObject.otherCompounds[i]);
				this.otherCompounds.push(compound);
			}
		}
		return this;
	};
}
CompoundSet.prototype = new Model;
/**
*
* @param {type} x
* @param {type} y
* @returns {FeatureSet}
*/
function FeatureSet(x, y) {
	this.x = x;
	this.y = y;
	this.mainFeature = null;
	this.medianValuesFeature = null;
	this.meanValuesFeature = null;
	this.maxValuesFeature = null;
	this.relevantFeatures = null;

	this.features = null;
	
	this.metagenes = null;

	/*****************************
	** GETTERS AND SETTERS
	*****************************/
	this.setX = function(x) {
		this.x = x;
	};
	this.getX = function() {
		return this.x;
	};
	this.setY = function(y) {
		this.y = y;
	};
	this.getY = function() {
		return this.y;
	};
	this.getMainFeature = function() {
		return this.mainFeature;
	};
	this.setMainFeature = function(mainFeature) {
		this.mainFeature = mainFeature;
	};
	this.isMainFeature = function(aFeature) {
		return this.mainFeature === aFeature;
	};

	this.getMedianValuesFeature = function() {
		return this.medianValuesFeature;
	};
	this.setMedianValuesFeature = function(medianValuesFeature) {
		this.medianValuesFeature = medianValuesFeature;
	};

	this.getMeanValuesFeature = function() {
		return this.meanValuesFeature;
	};
	this.setMeanValuesFeature = function(meanValuesFeature) {
		this.meanValuesFeature = meanValuesFeature;
	};

	this.getMaxValuesFeature = function() {
		return this.maxValuesFeature;
	};
	this.setMaxValuesFeature = function(maxValuesFeature) {
		this.maxValuesFeature = maxValuesFeature;
	};

	this.getRelevantFeatures = function() {
		return this.relevantFeatures;
	};
	this.setRelevantFeatures = function(relevantFeatures) {
		this.relevantFeatures = relevantFeatures;
	};
	this.addRelevantFeature = function(relevantFeature) {
		if (this.relevantFeatures === null) {
			this.relevantFeatures = [];
		}
		this.relevantFeatures.push(relevantFeature);
	};

	this.getFeatures = function() {
		return this.features;
	};
	this.setFeatures = function(features) {
		this.features = features;
	};
	this.addFeature = function(feature) {
		if (this.features === null) {
			this.features = [];
		}
		this.features.push(feature);
	};
	this.getMetagenes = function() {
		return this.metagenes;
	};
	this.setMetagenes = function(metagenes) {
		this.metagenes = metagenes;
	};
	this.addOmicMetagenes = function(omic, featureType, metagenes, replicateMapping, nSamples) {
		if (this.metagenes === null) {
			this.metagenes = [];
		}

		// All metagenes will share the same graphical data
		var oldFeatureGraphicalData = this.getFeatures()[0].getFeatureGraphicalData();
		var featureGraphicalData = jQuery.extend({}, oldFeatureGraphicalData).setID("Metagene_" + oldFeatureGraphicalData.getID());

		// When the parent omic has an active replicate→sample mapping (Step-2
		// auto-detection or manual design file), collapse the per-replicate
		// metagene vector to one value per biological sample and stash it on
		// OmicValue.sampleValues. The Step-4 renderer's `getValues("samples")`
		// then yields N cells for metagenes the same way it does for regular
		// features — without this the metagene tooltip stays stuck at N×k
		// cells even when the rest of the visualisation has switched to
		// "Show samples".
		var aggregate = Array.isArray(replicateMapping) && nSamples > 0;

		for (var i = 0; i < metagenes.length; i++) {

			if (this.metagenes.length < i + 1) {
				var metageneID = "Metagene " + (i + 1);
				var metageneFeature = new Feature(metageneID).setID(metageneID).setMetagene(true).setFeatureType(featureType);

				this.metagenes[i] = new FeatureSetElem(metageneFeature, featureGraphicalData).setParent(this);
			}

			var omicValue = new SimpleOmicValue()
				.setValues(metagenes[i])
				.setMetagene(true)
				.setOmicName(omic);

			if (aggregate) {
				omicValue.setSampleValues(
					collapseReplicatesByMapping(metagenes[i], replicateMapping, nSamples));
			}

			// TODO: add support for compound type in simple omic value
			this.metagenes[i].getFeature().addOmicsValues(omicValue);
		}
	};
	this.getAllOmicValues = function(omic) {
		var results = [];

		this.getFeatures().forEach(function(feature) {
			var omicValues = feature.getFeature().getOmicValues(omic, true);

			if (omicValues != undefined) {
				results.push(...omicValues);
			}
		});

		return(results);
	};
}
FeatureSet.prototype = new Model;

function FeatureSetElem(feature, featureGraphicalData) {
	this.feature = feature;
	this.featureGraphicalData = featureGraphicalData;

	/*****************************
	** GETTERS AND SETTERS
	*****************************/
	this.setFeature = function(feature) {
		this.feature = feature;

		return this;
	};
	this.getFeature = function() {
		return this.feature;
	};
	this.setFeatureGraphicalData = function() {
		this.featureGraphicalData = featureGraphicalData;

		return this;
	};
	this.getFeatureGraphicalData = function() {
		return this.featureGraphicalData;
	};
	this.setParent= function(parent) {
		this.parent = parent;

		return this;
	};
	this.getParent= function() {
		return this.parent;
	};
}
FeatureSetElem.prototype = new Model;

/**
*
* @returns {OmicValue}
*/
function OmicValue() {
	/***********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.inputName = "";
	this.omicName = "";
	this.relevant = "";
	this.relevantAssociation = false;
	this.values = null;
	this.isMetagene = false;
	// Replicate-aggregation fields. Both default to null so legacy jobs and
	// jobs without an applied sample mapping behave exactly as before — the
	// renderer falls back to `values` / `relevant`.
	this.sampleValues = null;     // list[number] — one mean per biological sample
	this.sampleRelevant = null;   // list[bool]   — OR-collapsed across replicates

	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.setInputName = function(inputName) {
		this.inputName = inputName;

		return this;
	};
	this.getInputName = function() {
		return this.inputName;
	};
	this.setRelevant = function(relevant) {
		this.relevant = relevant;

		return this;
	};
	this.isRelevant = function(index, mode) {
		// Mode-aware variant: when mode === "samples" and a sample-aggregation
		// has been computed for this OmicValue, read from `sampleRelevant`
		// (one bool per biological sample). All other shapes / modes fall
		// through to the per-replicate logic below, which is the original
		// pre-aggregation behaviour.
		if (mode === "samples" && Array.isArray(this.sampleRelevant)) {
			if (index !== undefined) {
				// Mirror the per-replicate guard at line below: a length-≤1
				// sampleRelevant carries the feature-level "relevant overall"
				// semantic and surfaces via the row-label `*`, not per-cell
				// stars. (The server emits length-1 here when the input
				// relevance file was a single-column / scalar flag.)
				if (this.sampleRelevant.length <= 1) return false;
				return this.sampleRelevant[index] === true;
			}
			return this.sampleRelevant.some(x => x === true);
		}
		if (index !== undefined && Array.isArray(this.relevant)) {
			// Per-cell stars only mean something with per-condition relevance.
			// A single-element list (single-column relevant file) means "relevant
			// overall" — surface that via the row-label `*` from the no-index
			// branch below, not a misleading first-cell star.
			if (this.relevant.length <= 1) return false;
			return this.relevant[index] === true;
		}
		if (Array.isArray(this.relevant)) {
			return this.relevant.some(x => x === true);
		}
		return this.relevant === true;
	};
	this.setRelevantAssociation = function(relevant) {
		this.relevantAssociation = relevant;

		return this;
	};
	this.isRelevantAssociation = function() {
		return this.relevantAssociation;
	};
	this.setOmicName = function(omicName) {
		this.omicName = omicName;

		return this;
	};
	this.getOmicName = function() {
		return this.omicName;
	};
	this.setValues = function(values) {
		this.values = values;

		return this;
	};
	this.getValues = function(mode) {
		// Mode-aware variant: callers that want the replicate-collapsed view
		// pass mode === "samples". When the OmicValue has a sampleValues array
		// available, return it; otherwise fall back to the raw replicate values
		// so the renderer can degrade gracefully on omics where aggregation was
		// never applied (single-condition data, time courses without reps, …).
		if (mode === "samples" && Array.isArray(this.sampleValues)) {
			return this.sampleValues;
		}
		return this.values;
	};
	this.setSampleValues = function(sampleValues) {
		this.sampleValues = sampleValues;

		return this;
	};
	this.getSampleValues = function() {
		return this.sampleValues;
	};
	this.setSampleRelevant = function(sampleRelevant) {
		this.sampleRelevant = sampleRelevant;

		return this;
	};
	this.getSampleRelevant = function() {
		return this.sampleRelevant;
	};
	this.hasSampleAggregation = function() {
		return Array.isArray(this.sampleValues);
	};
	this.isCompoundOmicsValue = function() {
		throw Error("Not implemented");
	};
	this.setMetagene = function(isMetagene) {
		this.isMetagene = isMetagene;

		return this;
	};
	this.isMetagene = function() {
		return this.isMetagene;
	};
}
OmicValue.prototype = new Model;
/********************************************
** STATIC FUNCTIONS
********************************************/
OmicValue.loadFromJSON = function(jsonObject) {
	var omicValueInstance;

	if (jsonObject.values !== undefined && isNaN(jsonObject.values[0])) {
		omicValueInstance = new CompoundOmicValue();
		omicValueInstance.values = [];
		for (var i in jsonObject.values) {
			omicValueInstance.values.push(OmicValue.loadFromJSON(jsonObject.values[i]));
		}
	} else {
		omicValueInstance = new SimpleOmicValue();
		omicValueInstance.values = [];
		for (var i in jsonObject.values) {
			omicValueInstance.values.push(parseFloat(jsonObject.values[i]));
		}
	}

	// Mirror the explicit float/bool coercion done for `values` above so the
	// aggregation arrays survive round-trips through JSON encoders that
	// stringify scalars (mongo extended-JSON, legacy serializers, …).
	if (Array.isArray(jsonObject.sampleValues)) {
		omicValueInstance.sampleValues = jsonObject.sampleValues.map(function(v) {
			return parseFloat(v);
		});
	} else {
		omicValueInstance.sampleValues = null;
	}
	if (Array.isArray(jsonObject.sampleRelevant)) {
		omicValueInstance.sampleRelevant = jsonObject.sampleRelevant.map(function(v) {
			return v === true || v === "True" || v === "true";
		});
	} else {
		omicValueInstance.sampleRelevant = null;
	}

	for(var i in jsonObject){
		if(i !== "values" && i !== "sampleValues" && i !== "sampleRelevant"){
			omicValueInstance[i] = jsonObject[i];
		}
	}

	return omicValueInstance;
};

function CompoundOmicValue() {
	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.isCompoundOmicsValue = function() {
		return true;
	};
}
CompoundOmicValue.prototype = new OmicValue;

function SimpleOmicValue() {
	/***********************************************************************
	* ATTRIBUTES
	***********************************************************************/
	this.visibleAtFeatureFamilyDetails = true;
	this.visibleAtFeatureDetails = true;
	this.visibleAtPathwayDetails = false;
	/***********************************************************************
	* GETTERS AND SETTERS
	***********************************************************************/
	this.isCompoundOmicsValue = function() {
		return false;
	};

	this.isVisibleAtFeatureFamilyDetails = function() {
		return this.visibleAtFeatureFamilyDetails;
	};

	this.isVisibleAtFeatureDetails = function() {
		return this.visibleAtFeatureDetails;
	};

	this.isVisibleAtPathwayDetails = function() {
		return this.visibleAtPathwayDetails;
	};

}
SimpleOmicValue.prototype = new OmicValue;

/**
 * Collapse a per-replicate vector to a per-sample vector by averaging the
 * elements that share a sample index. Mapping convention matches the
 * server-side aggregation: `mapping[col]` holds the sample index for that
 * replicate column, or a negative value when the column is unmatched (in
 * which case it is dropped from the mean). Non-finite inputs are skipped;
 * a sample with no contributing values yields NaN, which the renderer
 * already treats as "no data" (same fallback as feature-level sampleValues).
 *
 * Kept as a free function rather than an OmicValue method because the
 * client-side metagene path (PA_Step4Views.js) computes the aggregation
 * once per metagene vector returned by PCA, before any OmicValue exists.
 */
function collapseReplicatesByMapping(values, mapping, nSamples) {
	var sums = new Array(nSamples).fill(0);
	var counts = new Array(nSamples).fill(0);
	var n = Math.min(values.length, mapping.length);
	for (var i = 0; i < n; i++) {
		var s = mapping[i];
		if (s < 0 || s >= nSamples) continue;
		var v = Number(values[i]);
		if (!isFinite(v)) continue;
		sums[s] += v;
		counts[s] += 1;
	}
	var out = new Array(nSamples);
	for (var j = 0; j < nSamples; j++) {
		out[j] = counts[j] > 0 ? sums[j] / counts[j] : NaN;
	}
	return out;
}

#***************************************************************
#  This file is part of Paintomics v3
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#**************************************************************

from src.common.Util import Model
from collections import defaultdict

class Pathway(Model):
    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self, ID):
        self.ID = ID
        self.name = ""
        self.classification = ""
        self.source = "KEGG"
        #IDENTIFIERS OF MATCHED COMPOUNDS, THE DATA IS AT JOBINSTANCE
        self.matchedCompounds =[]
        #IDENTIFIERS OF MATCHED GENES, THE DATA IS AT JOBINSTANCE
        self.matchedGenes = []
        #METAGENES INFORMATION FOR EACH OMIC DATA TYPE
        self.metagenes = {}
        # NOTE: totalGenes and totalCompounds -- how many features of each kind
        # the pathway CONTAINS, not how many were matched -- are deliberately
        # NOT declared here. The network view divides the matched count by them
        # to get "min features in pathway", and a pathway stored before they
        # existed must be distinguishable from one that genuinely has none, so
        # that the client falls back to the installed gene count instead of
        # filtering against a zero. Absent is that distinction: toBSON is
        # self.__dict__, so an undeclared attribute is simply not a key, whereas
        # a None would reach the client as the STRING "None" (DAO.adaptBSON).
        # They are set in testPathwaySignificance, which is the only place that
        # sees both of the pathway's full feature sets.
        #SIGNIFICANCE VALUES PER OMIC in format OmicName -> [[totalFeatures, totalRelevantFeatures, pValue], ...] (one per condition)
        self.significanceValues= {}
        self.globalOmicPvalues = {}
        self.totalGlobalPvalues = {}
        self.adjustedSignificanceValues = {}
        self.combinedSignificancePvalues = {}
        self.adjustedCombinedSignificanceValues = {}
        self.masterRegulators = defaultdict(list)
        #GRAPHICAL INFORMATION
        self.graphicalOptions = None

    #******************************************************************************************************************
    # GETTERS AND SETTER
    #******************************************************************************************************************
    def setID(self, ID):
        self.ID = ID
    def getID(self):
        return self.ID

    def setName(self, name):
        self.name = name
    def getName(self):
        return self.name

    def setClassification(self, classification):
        self.classification = classification
    def setSource(self, source):
        self.source = source
    def getSource(self):
        return self.source

    def getMatchedCompounds(self):
        return self.matchedCompounds
    def addMatchedCompoundID(self, matchedCompoundID):
        self.matchedCompounds.append(matchedCompoundID)

    def getMatchedGenes(self):
        return self.matchedGenes
    def addMatchedGeneID(self, matchedGenID):
        self.matchedGenes.append(matchedGenID)

    def setTotalGenes(self, totalGenes):
        self.totalGenes = totalGenes
    def getTotalGenes(self):
        """The pathway's gene count, or None if this pathway was matched
        before the count was recorded."""
        return getattr(self, "totalGenes", None)

    def setTotalCompounds(self, totalCompounds):
        self.totalCompounds = totalCompounds
    def getTotalCompounds(self):
        """The pathway's compound count, or None if not recorded."""
        return getattr(self, "totalCompounds", None)

    def addMetagenes(self, omicName, metagene):
        if not omicName in self.metagenes:
            self.metagenes[omicName] = []
        self.metagenes[omicName].append(metagene)
    def resetMetagenes(self, omicName):
        self.metagenes[omicName] = []

    #OmicName -> [[totalFeatures, totalRelevantFeatures, pValue], ...] (one per condition)
    def getSignificanceValues(self):
        return self.significanceValues
    def addSignificanceValues(self, omicName, isRelevantFeatureList):
        # Ensure isRelevantFeatureList is a list
        if not isinstance(isRelevantFeatureList, (list, tuple)):
            isRelevantFeatureList = [isRelevantFeatureList]
        
        nConditionsInput = len(isRelevantFeatureList)
        currentValues = self.significanceValues.get(omicName)

        if currentValues is None:
            # Initialize with zeros for each condition
            currentValues = [[0, 0, -1.0] for _ in range(nConditionsInput)]
            currentLen = nConditionsInput
        else:
            currentLen = len(currentValues)
        
        # Use the maximum number of conditions seen so far for this omic
        nConditions = max(currentLen, nConditionsInput)
        
        if currentLen < nConditions:
            # New condition slots start at zero. A feature only contributes to
            # totalMatched of conditions where it actually has a value, so
            # earlier features (shorter lists) MUST NOT seed later slots.
            for _ in range(nConditions - currentLen):
                currentValues.append([0, 0, -1.0])

        for i in range(nConditions):
            # Only contribute to a condition when the feature has a slot for it.
            if i < nConditionsInput:
                currentValues[i][0] += 1 # totalMatched
                if isRelevantFeatureList[i]:
                    currentValues[i][1] += 1 # totalRelevant

        self.significanceValues[omicName] = currentValues

    def setGlobalOmicPvalue(self, omicName, pValue):
        self.globalOmicPvalues[omicName] = pValue
    def getGlobalOmicPvalues(self):
        return self.globalOmicPvalues

    def setTotalGlobalPvalues(self, pValues):
        self.totalGlobalPvalues = pValues
    def getTotalGlobalPvalues(self):
        return self.totalGlobalPvalues

    def setOmicAdjustedSignificanceValues(self, omic, adjustedSignificanceValues):
        self.adjustedSignificanceValues[omic] = adjustedSignificanceValues

    def setSignificancePvalue(self, omicName, pValues):
        # pValues is now a list of p-values for the omic (one per condition)
        if not isinstance(pValues, list):
             pValues = [pValues]
        
        for i, pVal in enumerate(pValues):
            if i < len(self.significanceValues[omicName]):
                self.significanceValues[omicName][i][2] = pVal

    def setCombinedSignificancePvalues(self, pValues):
        self.combinedSignificancePvalues = pValues
    def getCombinedSignificancePvalues(self):
        return self.combinedSignificancePvalues

    # NOTE: the populated attribute is `adjustedCombinedSignificanceValues`
    # (no "P"); the per-method setter at the bottom writes there. The bulk
    # setAdjustedCombinedSignificancePvalues() (with "P") below remained for
    # historical reasons; we route it to the same attribute so the getter
    # works regardless of which setter was used.
    def setAdjustedCombinedSignificancePvalues(self, pValues):
        self.adjustedCombinedSignificanceValues = pValues
    def getAdjustedCombinedSignificancePvalues(self):
        return self.adjustedCombinedSignificanceValues
    def setMethodAdjustedCombinedSignificanceValues(self, method, adjustedCombinedSignificanceValues):
        self.adjustedCombinedSignificanceValues[method] = adjustedCombinedSignificanceValues

    def setGraphicalOptions(self, graphicalOptions):
        self.graphicalOptions = graphicalOptions
    def getGraphicalOptions(self):
        return self.graphicalOptions

    #******************************************************************************************************************
    # OTHER FUNCTIONS
    #******************************************************************************************************************
    def parseBSON(self, bsonData):
        for (attr, value) in bsonData.items():
            setattr(self, attr, value)
        return self

    def toBSON(self):
        bson = {}
        for attr, value in list(self.__dict__.items()):
            if (attr != "graphicalOptions"):
                bson[attr] = value
        return bson
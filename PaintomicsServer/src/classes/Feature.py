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
from src.common.CompoundNameSimilarity import nameSimilarity

#**************************************************************
# CLASS Feature
#**************************************************************
class Feature(Model):
    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self, ID):
        self.ID = ID
        self.name = ""
        self.url = ""
        self.featureType= ""
        self.omicsValues = []
        self.matchingDB = ""

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

    def setOmicsValues(self, omicsValues):
        self.omicsValues = omicsValues

    def getOmicsValues(self):
        return self.omicsValues
    def addOmicValue(self, omicValue):
        self.omicsValues.append(omicValue)

    def addOmicValues(self, omicValuesList):
        for omicValue in omicValuesList:
            self.omicsValues.append(omicValue)

    def setMatchingDB(self, matchingDB):
        self.matchingDB = matchingDB

    def getMatchingDB(self):
        return self.matchingDB if self.matchingDB != '' else 'KEGG'

    #******************************************************************************************************************
    # OTHER FUNCTIONS
    #******************************************************************************************************************
    def combineData(self, otherFeature):
        if(self != otherFeature and self.ID == otherFeature.getID()):
            self.addOmicValues(otherFeature.getOmicsValues())

    def parseBSON(self, bsonData):
        bsonData.pop("_id", None);

        for (attr, value) in bsonData.items():
            if(attr == "omicsValues"):
                self.setOmicsValues([])
                for item in value:
                    self.addOmicValue(OmicValue("").parseBSON(item))
            else:
                setattr(self, attr, value)

    def toBSON(self):
        bson = {}
        for attr, value in list(self.__dict__.items()):
            if (attr == "omicsValues"):
                bson[attr] = []
                for elem in value:
                    bson[attr].append(elem.toBSON())
            else:
                bson[attr] = value
        return bson

#*****************************************************************************************************************
# CLASS OmicValue
#*****************************************************************************************************************
class OmicValue(Model):
    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self, inputName):
        self.inputName = inputName
        self.originalName = inputName
        self.omicName  = ""
        self.relevant  = []
        self.relevantAssociation = False
        self.values    = None
        # Replicate-aggregation fields. Both default to None so legacy jobs and
        # files without a detected/uploaded sample mapping behave exactly as
        # before — the renderer falls back to ``values`` / ``relevant``.
        # When populated:
        #   sampleValues   : list[float], one mean per biological sample
        #                    (length == len(inputOmic["sampleHeader"])).
        #   sampleRelevant : list[bool], OR-collapsed across the replicates
        #                    of each sample.
        self.sampleValues   = None
        self.sampleRelevant = None
        # Marks this OmicValue as a regulator-style entry — i.e. the row is
        # conceptually "this regulator's value at this target" (TF, miRNA,
        # methylation, any custom regulatory omic). Set whenever the source
        # line had the `targetID:::regulatorID` format, regardless of whether
        # the regulator's symbol could be resolved. Drives the Step 4 client to
        # show the regulator as the primary identifier in the global heatmap
        # row and the details panel.
        self.isRegulator    = False
        # Regulator's canonical ID (e.g. TAIR/AGI) when its symbol was resolved
        # via the mapper. originalName holds the display symbol; this preserves
        # the underlying ID so the details panel can render an "(AGI)" line
        # below the symbol. Empty string when no symbol mapping was found —
        # in that case originalName already equals the raw regulator ID.
        self.regulatorID    = ""

    #******************************************************************************************************************
    # GETTERS AND SETTER
    #******************************************************************************************************************
    def setInputName(self, inputName):
        self.inputName = inputName

    def getInputName(self):
        return self.inputName

    def setOriginalName(self, originalName):
        self.originalName= originalName
    def getOriginalName(self):
        return self.originalName

    def setRelevant(self, relevant):
        self.relevant = relevant
    def isRelevant(self, conditionIndex=None):
        if conditionIndex is not None and isinstance(self.relevant, list) and len(self.relevant) > conditionIndex:
            return self.relevant[conditionIndex]
        if isinstance(self.relevant, list):
            return any(self.relevant) # For backward compatibility in simple 'if isRelevant()'
        return self.relevant

    def setRelevantAssociation(self, relevant):
        self.relevantAssociation = relevant
    def isRelevantAssociation(self):
        return self.relevantAssociation

    def setOmicName(self, omicName):
        self.omicName = omicName
    def getOmicName(self):
        return self.omicName

    def setValues(self, values):
        self.values = values
    def getValues(self):
        return self.values

    def setSampleValues(self, sampleValues):
        self.sampleValues = sampleValues
    def getSampleValues(self):
        return self.sampleValues

    def setSampleRelevant(self, sampleRelevant):
        self.sampleRelevant = sampleRelevant
    def getSampleRelevant(self):
        return self.sampleRelevant

    def hasSampleAggregation(self):
        """True if a replicate-collapsed view has been computed for this OmicValue."""
        return self.sampleValues is not None

    #******************************************************************************************************************
    # OTHER FUNCTIONS
    #******************************************************************************************************************
    def parseBSON(self, bsonData):
        for (attr, value) in bsonData.items():
            if(attr == "relevant"):
                if isinstance(value, list):
                    self.relevant = [(v == "True" or v == True) for v in value]
                else:
                    self.relevant = (value == "True" or value == True)
            elif(attr == "relevantAssociation"):
                value= (value == "True" or value == True)
                setattr(self, attr, value)
            elif(attr == "sampleRelevant"):
                # Mirror the bool-coercion done for `relevant` to handle docs
                # written by older / cross-environment serializers (e.g. JSON
                # round-trips that stringify booleans).
                if value is None:
                    self.sampleRelevant = None
                elif isinstance(value, list):
                    self.sampleRelevant = [(v == "True" or v == True) for v in value]
                else:
                    self.sampleRelevant = (value == "True" or value == True)
            else:
                setattr(self, attr, value)
        return self

#*****************************************************************************************************************
# CLASS GENE
#*****************************************************************************************************************
class Gene(Feature):
    #******************************************************************************************************************
    # CONSTRUCTOR
    #******************************************************************************************************************
    def __init__(self, ID):
        super(Gene, self).__init__(ID)
        self.featureType= "Gene"

#*****************************************************************************************************************
# CLASS Compound
#*****************************************************************************************************************
class Compound(Feature):
    #******************************************************************************************************************
    # CONSTRUCTOR
    #******************************************************************************************************************
    def __init__(self, ID):
        super(Compound, self).__init__(ID)
        self.featureType= "Compound"
        self.similarity= 0

    def calculateSimilarity(self, other_name):
        """Score this candidate against the name the user uploaded, and keep it.

        The arithmetic lives in :mod:`src.common.CompoundNameSimilarity` so
        that the step-2 ranker, which rescores candidates read back out of
        MongoDB, cannot disagree with the mapper about which candidates are
        "main" ones. Setting ``self.similarity`` stays here: it is this
        object's state, and JobController reads it off the wire to break ties
        when two input names propose the same KEGG compound.
        """
        self.similarity = nameSimilarity(self.getName(), other_name)
        return self.similarity

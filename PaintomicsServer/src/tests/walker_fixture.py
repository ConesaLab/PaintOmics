"""A tiny KEGG-shaped organism and a fake job for the walker tests.

Not a test. Builds, in a temporary directory, an organism ``tst`` with two KGML
pathways and the two mapping files the network reader looks for, and a job
object exposing the four methods the overlay reads. No Mongo, no network.

    tst00001:  A -(activation)-> B -(inhibition)-> C ; A -(expression)-> D -> E ; C -> E
    tst00002:  A -(activation)-> F ; F -(binding)-> G
"""
import os
import tempfile

KGML_1 = """<?xml version="1.0"?>
<pathway name="path:tst00001" org="tst" number="00001" title="Test pathway one">
  <entry id="1" name="tst:1" type="gene"><graphics name="A" type="rectangle" x="100" y="100" width="46" height="17"/></entry>
  <entry id="2" name="tst:2" type="gene"><graphics name="B" type="rectangle" x="200" y="100" width="46" height="17"/></entry>
  <entry id="3" name="tst:3" type="gene"><graphics name="C" type="rectangle" x="300" y="100" width="46" height="17"/></entry>
  <entry id="4" name="tst:4" type="gene"><graphics name="D" type="rectangle" x="200" y="200" width="46" height="17"/></entry>
  <entry id="5" name="tst:5" type="gene"><graphics name="E" type="rectangle" x="300" y="200" width="46" height="17"/></entry>
  <entry id="9" name="path:tst00002" type="map"><graphics name="two" type="roundrectangle" x="400" y="300" width="46" height="17"/></entry>
  <relation entry1="1" entry2="2" type="PPrel"><subtype name="activation" value="--&gt;"/></relation>
  <relation entry1="2" entry2="3" type="PPrel"><subtype name="inhibition" value="--|"/></relation>
  <relation entry1="1" entry2="4" type="GErel"><subtype name="expression" value="--&gt;"/></relation>
  <relation entry1="4" entry2="5" type="PPrel"><subtype name="binding/association" value="---"/></relation>
  <relation entry1="3" entry2="5" type="PPrel"><subtype name="phosphorylation" value="+p"/></relation>
  <relation entry1="1" entry2="9" type="maplink"><subtype name="compound" value="7"/></relation>
</pathway>
"""
KGML_2 = """<?xml version="1.0"?>
<pathway name="path:tst00002" org="tst" number="00002" title="Test pathway two">
  <entry id="1" name="tst:1" type="gene"><graphics name="A" type="rectangle" x="100" y="100" width="46" height="17"/></entry>
  <entry id="2" name="tst:6" type="gene"><graphics name="F" type="rectangle" x="200" y="100" width="46" height="17"/></entry>
  <entry id="3" name="tst:7" type="gene"><graphics name="G" type="rectangle" x="300" y="100" width="46" height="17"/></entry>
  <relation entry1="1" entry2="2" type="PPrel"><subtype name="activation" value="--&gt;"/></relation>
  <relation entry1="2" entry2="3" type="PPrel"><subtype name="binding/association" value="---"/></relation>
</pathway>
"""
SYMBOLS = {"1": "Aaa", "2": "Bbb", "3": "Ccc", "4": "Ddd", "5": "Eee", "6": "Fff", "7": "Ggg"}


def make_data_dir():
    """A KEGG_DATA-shaped directory for organism tst; caller removes it."""
    root = tempfile.mkdtemp(prefix="walker_fixture_")
    org = os.path.join(root, "current", "tst")
    os.makedirs(os.path.join(org, "kgml"))
    os.makedirs(os.path.join(org, "mapping"))
    with open(os.path.join(org, "kgml", "tst00001.kgml"), "w") as handle:
        handle.write(KGML_1)
    with open(os.path.join(org, "kgml", "tst00002.kgml"), "w") as handle:
        handle.write(KGML_2)
    with open(os.path.join(org, "pathways.list"), "w") as handle:
        handle.write("path:tst00001\tTest pathway one - Test organism (tst)\n"
                     "path:tst00002\tTest pathway two - Test organism (tst)\n")
    with open(os.path.join(org, "mapping", "kegg2genesymbol.list"), "w") as handle:
        for kegg, symbol in SYMBOLS.items():
            handle.write("tst:%s\tCDS\t1:1..2\t%s; a test gene\n" % (kegg, symbol))
    with open(os.path.join(org, "mapping", "uniprot2kegg.list"), "w") as handle:
        handle.write("up:P00001\ttst:1\nup:P00002\ttst:2\n")
    return root


class FakeOmicValue(object):
    def __init__(self, omic, original, relevant, values, input_name=None):
        self._omic, self._orig, self._rel, self._values = omic, original, relevant, values
        self._input = input_name or original

    def getOmicName(self):
        return self._omic

    def getOriginalName(self):
        return self._orig

    def getInputName(self):
        return self._input

    def isRelevant(self, conditionIndex=None):
        if isinstance(self._rel, list):
            return any(self._rel)
        return self._rel

    def getValues(self):
        return self._values


class FakeFeature(object):
    def __init__(self, feature_id, omics):
        self._id, self._omics = feature_id, omics

    def getID(self):
        return self._id

    def getOmicsValues(self):
        return self._omics


class FakeJob(object):
    """Six time points; gene expression labeled, miRNA-seq unlabeled."""

    def __init__(self, genes, compounds=None, headers=None):
        self._genes, self._compounds = genes, compounds or {}
        self._headers = headers if headers is not None else {
            "Gene expression": ["#geneID", "X/Y_0h", "X/Y_2h", "X/Y_6h"],
            "Proteomics": ["Protein", "Ratio_0", "Ratio_2", "Ratio_6"],
            "miRNA-seq": None,
        }

    def getInputGenesData(self):
        return self._genes

    def getInputCompoundsData(self):
        return self._compounds

    def getGeneBasedInputOmics(self):
        return [{"omicName": name, "omicHeader": header} for name, header in self._headers.items()]

    def getCompoundBasedInputOmics(self):
        return []

    def getOrganism(self):
        return "tst"

    def getExperimentDesign(self):
        return "A test perturbation over three time points. Values are log2 fold changes."


def make_job():
    """A: relevant in expression; B: relevant only by its second condition;
    C: measured, not relevant; D: relevant; E: relevant by proteomics only;
    F: measured, not relevant; G unmeasured. miR-1 targets B and D (relevant),
    miR-2 targets C (not relevant)."""
    ge = "Gene expression"
    genes = {
        "1": FakeFeature("1", [FakeOmicValue(ge, "Aaa", [True, True, True], [0.1, 1.5, 2.2])]),
        "2": FakeFeature("2", [FakeOmicValue(ge, "Bbb", [False, True, False], [0.0, -1.2, -0.3]),
                               FakeOmicValue("miRNA-seq", "tst-miR-1", [True], [0.4, 0.9, 1.6])]),
        "3": FakeFeature("3", [FakeOmicValue(ge, "Ccc", [False, False, False], [0.05, 0.02, -0.1]),
                               FakeOmicValue("miRNA-seq", "tst-miR-2", [False], [0.1, 0.1, 0.0])]),
        "4": FakeFeature("4", [FakeOmicValue(ge, "Ddd", [True, False, False], [-2.0, -1.0, -0.5]),
                               FakeOmicValue("miRNA-seq", "tst-miR-1", [True], [0.4, 0.9, 1.6])]),
        "5": FakeFeature("5", [FakeOmicValue(ge, "Eee", [False, False, False], [0.2, 0.1, 0.0]),
                               FakeOmicValue("Proteomics", "Eee", True, [0.3, 2.1, 2.5])]),
        "6": FakeFeature("6", [FakeOmicValue(ge, "Fff", False, [0.0, 0.0, 0.1])]),
    }
    return FakeJob(genes)

#!/usr/bin/env python3
"""The context a paper carries -- organism, system, scope -- read from the MeSH
headings and publication types the PubMed client now keeps, and how the
Writer's papers are matched and ranked against the design card. Offline: the
PubMed XML is built here and never leaves the process.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_walker_context
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret import pubmed_client                 # noqa: E402
from src.classes.AIInterpret.walker import literature as lit      # noqa: E402


def _record(pmid, mesh=(), pub_types=("Journal Article",), title="A title", year="2020"):
    """One PubmedArticle as EFetch returns it, with the parts the parser reads."""
    headings = "".join(
        "<MeshHeading><DescriptorName UI=\"D0\" MajorTopicYN=\"N\">%s</DescriptorName>"
        "<QualifierName UI=\"Q0\" MajorTopicYN=\"Y\">metabolism</QualifierName></MeshHeading>" % h
        for h in mesh)
    types = "".join("<PublicationType UI=\"D0\">%s</PublicationType>" % t for t in pub_types)
    return (
        "<PubmedArticle><MedlineCitation Status=\"MEDLINE\" Owner=\"NLM\">"
        "<PMID Version=\"1\">%s</PMID>"
        "<Article PubModel=\"Print\">"
        "<Journal><Title>J Test</Title><JournalIssue><PubDate><Year>%s</Year></PubDate></JournalIssue></Journal>"
        "<ArticleTitle>%s</ArticleTitle>"
        "<Abstract><AbstractText>Pre-B cells were <i>studied</i>.</AbstractText></Abstract>"
        "<AuthorList><Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author></AuthorList>"
        "<PublicationTypeList>%s</PublicationTypeList>"
        "</Article>"
        "%s"
        "</MedlineCitation></PubmedArticle>"
    ) % (pmid, year, title, types,
         ("<MeshHeadingList>%s</MeshHeadingList>" % headings) if mesh else "")


def _xml(*records):
    return "<?xml version=\"1.0\" ?><PubmedArticleSet>%s</PubmedArticleSet>" % "".join(records)


MOUSE_B_CELL = ("Animals", "Mice", "B-Lymphocytes", "Cell Line")


class _Response(object):
    status_code = 200

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class PubMedMeshParsingTest(unittest.TestCase):
    def setUp(self):
        self.client = pubmed_client.PubMedClient()

    def test_parser_keeps_mesh_descriptors_and_publication_types(self):
        papers = self.client._parse_xml(_xml(_record("1001", MOUSE_B_CELL)))
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper["mesh"], ["Animals", "Mice", "B-Lymphocytes", "Cell Line"])
        self.assertEqual(paper["pub_types"], ["Journal Article"])
        # Qualifiers never leak into the descriptor list.
        self.assertNotIn("metabolism", paper["mesh"])
        # Nothing the parser returned before has moved.
        self.assertEqual(paper["pmid"], "1001")
        self.assertEqual(paper["title"], "A title")
        self.assertEqual(paper["abstract"], "Pre-B cells were studied.")
        self.assertEqual(paper["year"], "2020")
        self.assertEqual(paper["journal"], "J Test")
        self.assertEqual(paper["first_author"], "Jane Doe")

    def test_a_record_without_indexing_gets_empty_lists(self):
        papers = self.client._parse_xml(_xml(_record("1002", mesh=(), pub_types=())))
        self.assertEqual(papers[0]["mesh"], [])
        self.assertEqual(papers[0]["pub_types"], [])

    def test_fetch_abstracts_carries_the_fields_through(self):
        xml = _xml(_record("1001", MOUSE_B_CELL), _record("1003", ("Humans", "Cohort Studies"), ("Review",)))
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs.get("params", {})))
            return _Response(xml)

        self.client._request_with_retry = fake_request
        papers = self.client.fetch_abstracts(["1001", "1003"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]["id"], "1001,1003")
        by_pmid = {p["pmid"]: p for p in papers}
        self.assertEqual(by_pmid["1001"]["mesh"], list(MOUSE_B_CELL))
        self.assertEqual(by_pmid["1003"]["mesh"], ["Humans", "Cohort Studies"])
        self.assertEqual(by_pmid["1003"]["pub_types"], ["Review"])


class PaperContextTest(unittest.TestCase):
    def test_mouse_b_cell_line(self):
        ctx = lit.paper_context({"mesh": list(MOUSE_B_CELL), "pub_types": ["Journal Article"]})
        self.assertEqual(ctx, {"organism": "mouse", "system": "B-Lymphocytes", "scope": "in vitro"})

    def test_strain_heading_counts_as_the_species(self):
        ctx = lit.paper_context({"mesh": ["Animals", "Mice, Inbred C57BL", "Liver"], "pub_types": []})
        self.assertEqual(ctx["organism"], "mouse")
        self.assertEqual(ctx["system"], "Liver")
        self.assertEqual(ctx["scope"], "in vivo")

    def test_human_cohort_is_clinical(self):
        ctx = lit.paper_context({"mesh": ["Humans", "Cohort Studies", "Male"], "pub_types": ["Journal Article"]})
        self.assertEqual(ctx["organism"], "human")
        self.assertEqual(ctx["system"], "unknown")
        self.assertEqual(ctx["scope"], "clinical")
        ctx = lit.paper_context({"mesh": ["Humans", "Liver", "Prognosis"], "pub_types": []})
        self.assertEqual((ctx["system"], ctx["scope"]), ("Liver", "clinical"))

    def test_human_without_a_study_type_has_unknown_scope(self):
        ctx = lit.paper_context({"mesh": ["Humans", "Hepatocytes"], "pub_types": ["Journal Article"]})
        self.assertEqual(ctx, {"organism": "human", "system": "Hepatocytes", "scope": "unknown"})

    def test_review_wins_over_everything(self):
        ctx = lit.paper_context({"mesh": list(MOUSE_B_CELL), "pub_types": ["Journal Article", "Review"]})
        self.assertEqual(ctx["scope"], "review")
        self.assertEqual(lit.paper_context({"mesh": [], "pub_types": ["Systematic Review"]})["scope"], "review")

    def test_first_organism_in_document_order_wins(self):
        ctx = lit.paper_context({"mesh": ["Humans", "Animals", "Mice", "HEK293 Cells"], "pub_types": []})
        self.assertEqual(ctx["organism"], "human")
        self.assertEqual(ctx["system"], "HEK293 Cells")
        self.assertEqual(ctx["scope"], "in vitro")

    def test_system_headings(self):
        for heading in ("Precursor Cells, B-Lymphoid", "T-Lymphocytes", "Dendritic Cells", "Bone Marrow",
                        "Muscle, Skeletal", "Macrophages", "Stem Cells", "Germinal Center B-Cells"):
            ctx = lit.paper_context({"mesh": ["Animals", "Mice", heading], "pub_types": []})
            self.assertEqual(ctx["system"], heading, heading)
        # Generic materials and demographics are not a system.
        ctx = lit.paper_context({"mesh": ["Humans", "Cell Line, Tumor", "Cells, Cultured", "Adult"], "pub_types": []})
        self.assertEqual(ctx["system"], "unknown")

    def test_no_mesh_is_all_unknown(self):
        expected = {"organism": "unknown", "system": "unknown", "scope": "unknown"}
        self.assertEqual(lit.paper_context({"mesh": [], "pub_types": []}), expected)
        self.assertEqual(lit.paper_context({"pmid": "1", "title": "old record"}), expected)
        self.assertEqual(lit.paper_context(None), expected)


class ContextMatchTest(unittest.TestCase):
    CARD = {"organism": "mouse", "system": "mouse B3 pre-B cell line"}

    def test_same_organism_and_system(self):
        ctx = {"organism": "mouse", "system": "B-Lymphocytes", "scope": "in vitro"}
        self.assertEqual(lit.context_match(self.CARD, ctx), {"organism": "same", "system": "same"})
        ctx = {"organism": "Mouse", "system": "Precursor Cells, B-Lymphoid", "scope": "in vivo"}
        self.assertEqual(lit.context_match(self.CARD, ctx), {"organism": "same", "system": "same"})

    def test_other_organism_and_system(self):
        ctx = {"organism": "human", "system": "Hepatocytes", "scope": "unknown"}
        self.assertEqual(lit.context_match(self.CARD, ctx), {"organism": "other", "system": "other"})
        ctx = {"organism": "mouse", "system": "T-Lymphocytes", "scope": "in vivo"}
        self.assertEqual(lit.context_match(self.CARD, ctx)["system"], "other")

    def test_unknown_when_either_side_is_silent(self):
        ctx = {"organism": "unknown", "system": "unknown", "scope": "unknown"}
        self.assertEqual(lit.context_match(self.CARD, ctx), {"organism": "unknown", "system": "unknown"})
        ctx = {"organism": "mouse", "system": "B-Lymphocytes", "scope": "in vitro"}
        self.assertEqual(lit.context_match({}, ctx), {"organism": "unknown", "system": "unknown"})
        self.assertEqual(lit.context_match({"organism": "mouse", "system": ""}, ctx)["system"], "unknown")

    def test_a_heading_word_or_a_synonym_matches(self):
        card = {"organism": "mouse", "system": "bone marrow-derived macrophages"}
        self.assertEqual(lit.context_match(card, {"system": "Bone Marrow"})["system"], "same")
        self.assertEqual(lit.context_match(card, {"system": "Macrophages"})["system"], "same")
        card = {"organism": "human", "system": "primary liver tissue"}
        self.assertEqual(lit.context_match(card, {"system": "Hepatocytes"})["system"], "same")
        self.assertEqual(lit.context_match(card, {"system": "Liver"})["system"], "same")
        self.assertEqual(lit.context_match(card, {"system": "Fibroblasts"})["system"], "other")


class ContextLineTest(unittest.TestCase):
    def test_line(self):
        self.assertEqual(lit.context_line({"organism": "mouse", "system": "B-Lymphocytes", "scope": "in vitro"}),
                         "mouse · B-Lymphocytes · in vitro")
        self.assertEqual(lit.context_line({"organism": "human", "system": "unknown", "scope": "clinical"}),
                         "human · clinical")
        self.assertEqual(lit.context_line({"organism": "unknown", "system": "unknown", "scope": "unknown"}), "")
        self.assertEqual(lit.context_line({}), "")


class RankByContextTest(unittest.TestCase):
    CARD = {"organism": "mouse", "system": "mouse B3 pre-B cell line"}

    def test_order_and_annotation(self):
        papers = [
            {"pmid": "1", "mesh": ["Humans", "Hepatocytes"], "pub_types": []},
            {"pmid": "2", "mesh": ["Animals", "Rats", "B-Lymphocytes"], "pub_types": []},
            {"pmid": "3", "mesh": ["Animals", "Mice", "Liver"], "pub_types": []},
            {"pmid": "4", "mesh": [], "pub_types": []},
            {"pmid": "5", "mesh": ["Animals", "Mice", "B-Lymphocytes", "Cell Line"], "pub_types": []},
            {"pmid": "6", "mesh": ["Animals", "Mice", "Spleen"], "pub_types": ["Review"]},
        ]
        ranked = lit.rank_by_context(papers, self.CARD)
        # Same organism first (5, 3, 6 keep their order but 5 leads on system),
        # then the same system from another organism (2), then the rest in
        # their original order (1, 4).
        self.assertEqual([p["pmid"] for p in ranked], ["5", "3", "6", "2", "1", "4"])
        self.assertEqual(ranked[0]["context"],
                         {"organism": "mouse", "system": "B-Lymphocytes", "scope": "in vitro",
                          "match": {"organism": "same", "system": "same"}})
        self.assertEqual(ranked[2]["context"]["scope"], "review")
        self.assertEqual(ranked[-1]["context"]["match"], {"organism": "unknown", "system": "unknown"})
        # The input dicts themselves carry the annotation, so the Writer's
        # own references see it.
        self.assertIn("context", papers[0])

    def test_empty_and_odd_input(self):
        self.assertEqual(lit.rank_by_context([], self.CARD), [])
        self.assertEqual(lit.rank_by_context(None, self.CARD), [])
        self.assertEqual(lit.rank_by_context([None, {"pmid": "1"}], self.CARD)[0]["pmid"], "1")


if __name__ == "__main__":
    unittest.main()

"""The Results rewrite may reshape the prose. It may not cost a citation.

The rewrite runs on a report whose citations have been EARNED: each [N] was
checked against the paper it points at. A rewrite that drops one loses a
verified fact, and a rewrite that adds one points at a paper this text was
never verified against.

This document's two most expensive lessons are both this failure: sentence
repair was killed for -33% citations and the top-up deadline for -21%, each
having looked reasonable in every other respect. So the rule is a GUARD that
rejects the candidate, not a line in a prompt.
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(SERVER, "src", "classes", "AIInterpret", "agent_loop.py")


def _markers(text):
    """Body markers only -- the reference list repeats every one of them.

    Counting the whole document lets a rewrite drop an inline citation while
    its reference entry survives, and the guard would see no change. That is
    marker-stripping, the exact behaviour that makes "failed citations" read
    0.0 on a report whose prose lost its support.
    """
    return set(re.findall(r"\[(\d+)\]", str(text).split("### References")[0]))


def _verdict(before_text, after_text, has_refs=True):
    """The guard's logic, mirrored exactly from _write_results_section."""
    before, after = _markers(before_text), _markers(after_text)
    reasons = []
    if before - after:
        reasons.append("lost_%d" % len(before - after))
    if after - before:
        reasons.append("invented_%d" % len(after - before))
    if has_refs and "### References" not in after_text:
        reasons.append("no_references")
    if len(after_text) < 0.2 * len(before_text):
        reasons.append("truncated")
    return reasons


REPORT = ("Ikaros drives differentiation [1]. Accessibility rises [2]. "
          "DYNLL1 is sustained [3].\n\n### References\n[1] a\n[2] b\n[3] c\n") * 6


class ResultsSectionKeepsItsCitations(unittest.TestCase):

    def setUp(self):
        with open(SRC) as fh:
            self.src = fh.read()

    # ---- the guard itself -------------------------------------------------

    def test_a_rewrite_that_keeps_every_marker_is_accepted(self):
        good = REPORT.replace("Ikaros drives differentiation [1].",
                              "Ikaros induction drove differentiation [1].")
        self.assertEqual(_verdict(REPORT, good), [])

    def test_a_rewrite_that_drops_a_marker_is_rejected(self):
        bad = REPORT.replace(" [2]", "")
        self.assertIn("lost_1", _verdict(REPORT, bad),
                      "a dropped marker loses a verified fact")

    def test_a_rewrite_that_invents_a_marker_is_rejected(self):
        bad = REPORT.replace("DYNLL1 is sustained [3].",
                             "DYNLL1 is sustained [3][9].")
        self.assertIn("invented_1", _verdict(REPORT, bad),
                      "a new marker points at a paper never verified here")

    def test_a_rewrite_that_drops_the_references_is_rejected(self):
        bad = REPORT.replace("### References", "## Bibliography")
        self.assertIn("no_references", _verdict(REPORT, bad))

    def test_a_rewrite_that_truncates_is_rejected(self):
        self.assertIn("truncated",
                      _verdict(REPORT, "Ikaros drives differentiation [1]."))

    def test_shortening_is_allowed_when_the_citations_survive(self):
        """The whole point is a shorter document -- 8184 words is the problem."""
        short = ("Ikaros drove differentiation [1], with accessibility rising "
                 "ahead of transcription [2] and DYNLL1 sustained throughout "
                 "[3].\n\n### References\n[1] a\n[2] b\n[3] c\n") * 3
        self.assertLess(len(short), len(REPORT))
        self.assertEqual(_verdict(REPORT, short), [],
                         "a shorter rewrite that keeps every marker must pass")

    # ---- the wiring -------------------------------------------------------

    def test_the_guard_is_in_the_source_not_only_in_the_prompt(self):
        # Not a fixed window: the function grew when the retry was added and a
        # 4000-char slice stopped short of the guard it is checking. Take the
        # whole function instead, bounded by the next top-level def.
        block = self.src.split("async def _write_results_section")[1]
        block = block.split("\nasync def ")[0].split("\ndef ")[0]
        self.assertIn('re.findall(', block,
                      "the marker set must be computed, not asked for")
        self.assertIn('split("### References")', block,
                      "the guard must compare BODY markers; the reference list "
                      "repeats every marker and would mask a dropped citation")
        self.assertIn('stats["results_rejected"]', block)
        self.assertIn("return report, False", block,
                      "a rejected rewrite must return the ORIGINAL report")

    def test_it_runs_before_the_exit_gate(self):
        """Order is the safety property.

        After the gate, a rewrite could move a verified marker onto a claim
        nobody checked -- reintroducing exactly the overshoot the gate removes.
        """
        call = self.src.index("_write_results_section(\n")
        gate = self.src.index("# ---- The mandatory exit gate")
        self.assertLess(call, gate,
                        "the Results rewrite must precede the exit gate")

    def test_the_cluster_table_is_suppressed_when_a_section_was_written(self):
        """It was asked for as prose; a table restating it undoes that."""
        block = self.src.split("The deterministic tables ride below")[1][:1200]
        self.assertIn("if not results_written:", block)

    def test_the_pathway_table_survives(self):
        """It is data the job already holds, not a model assertion."""
        block = self.src.split("The deterministic tables ride below")[1][:1200]
        self.assertIn("render_pathway_table(pathways)", block)

    def test_the_rewrite_is_renumbered_afterwards(self):
        """Reorganising prose scrambles first-appearance order.

        The dossier arrives numbered [1,2,3...] because it was written in that
        order. A Results section reorders the material by finding, so the same
        markers now appear as [9,5,6,14,13,...] -- measured exactly that on the
        first live rewrite. No journal accepts that; citations must be numbered
        in order of first appearance.

        The writer must NOT renumber itself: its markers still have to map to
        the papers they were verified against. `renumber_citations` does the
        remap across body AND references and returns the mapping the paper list
        is then filtered by. So the ordering property depends entirely on the
        writer running BEFORE it -- which is incidental unless pinned here.
        """
        writer = self.src.index("_write_results_section(\n")
        renumber = self.src.index("report, citation_mapping = renumber_citations(report)")
        self.assertLess(writer, renumber,
                        "the Results rewrite must run BEFORE renumber_citations, "
                        "or its citations ship out of first-appearance order")
        sort_refs = self.src.index("report = sort_references_section(report)")
        self.assertLess(renumber, sort_refs,
                        "references are sorted after the remap, not before")

    def test_the_writer_is_told_not_to_renumber(self):
        """The downstream remap owns numbering, and needs the ORIGINAL indices.

        renumber_citations returns a mapping the paper list is filtered by. If
        the model renumbered first, its markers would no longer identify the
        papers they were verified against.
        """
        # Whitespace-normalised: the instruction wraps as "do not\n  renumber",
        # so a literal search finds nothing and the test fails on correct text.
        prompt = " ".join(self.src.split("RESULTS_PROMPT")[1][:3000].lower().split())
        self.assertIn("do not renumber", prompt,
                      "the model must be told not to renumber; the pipeline "
                      "does it afterwards and owns the mapping")

    def test_it_is_off_by_default(self):
        self.assertIn('os.getenv("AI_AGENT_RESULTS_SECTION", "0")', self.src,
                      "a new writing stage ships off until it is measured")


class ResultsSectionKeepsItsFindings(unittest.TestCase):
    """Citations are not findings, and guarding one does not guard the other.

    Round 66 shipped a Results stage that kept EVERY citation and still lost
    22% of the rubric's coverage: 0.571 -> 0.446. Round 64 had already measured
    citation count and rubric coverage as independent (r = 0.05), so a
    citation-only guard could never have caught it. The rubric scores
    `truth_pathways` -- a list of pathway NAMES -- which makes findings
    checkable at runtime by the same trick.
    """

    def setUp(self):
        with open(SRC) as fh:
            self.src = fh.read()

    def test_the_findings_guard_exists(self):
        self.assertIn("_named_pathways", self.src)
        self.assertIn("droppedpathways_", self.src,
                      "losing a pathway must be a rejection reason, not a note")

    def test_it_matches_on_the_name_not_the_accession(self):
        """A section reorganised by finding has no reason to keep '(mmu04060)'
        in a heading; requiring it would reject good prose. Losing the NAME is
        what loses the finding."""
        block = self.src.split("def _named_pathways")[1][:900]
        self.assertIn(".lower()", block)
        # The CODE, not the docstring: the comment explains the rule using
        # "(mmu04060)" as its example, and asserting on prose is how a test
        # fails on a correct implementation.
        code = block.split('"""')[2] if block.count('"""') >= 2 else block
        self.assertNotIn("mmu", code.lower())

    def test_there_is_no_length_target(self):
        """The first version carried one and used it as a target: 8 184 words
        to 2 957, taking 22% of the coverage with them. Removing it was NOT the
        fix -- with no target the model dropped 8 of 16 pathways -- but a length
        knob on a stage whose job is to reorganise is an instruction to
        discard."""
        self.assertNotIn("RESULTS_MAX_WORDS", self.src)
        self.assertNotIn("%(words)d", self.src)

    def test_the_rewrite_is_chunked(self):
        """One call cannot conserve the whole dossier. Measured twice: -22%
        coverage with a word target, -50% of pathways without one. The fix is a
        smaller question per call, not a better prompt."""
        self.assertIn("_results_by_chunk", self.src)
        self.assertIn("RESULTS_TARGET_SECTIONS", self.src)

    def test_a_chunk_checks_the_pathways_it_owns(self):
        """A single dropped name rejects the whole rewrite, and a chunk owns a
        known handful -- so a miss is both detectable and cheap to fix there."""
        block = self.src.split("async def _results_by_chunk")[1]
        block = block.split("\nasync def ")[0].split("\ndef ")[0]
        self.assertIn("gone = [n for n in owned", block)
        # Three attempts, not two. The third exists because the fallback
        # changed: a chunk that still will not conserve its source now keeps
        # its ORIGINAL text instead of costing the whole rewrite, so one more
        # try is cheaper than one more dossier-shaped section.
        self.assertIn("for attempt in (1, 2, 3)", block)

    def test_chunks_run_in_order_so_headings_differ(self):
        """Run in parallel they cannot see each other and converge: five
        sections opened with a variant of the same claim."""
        block = self.src.split("async def _results_by_chunk")[1]
        block = block.split("\nasync def ")[0].split("\ndef ")[0]
        self.assertNotIn("asyncio.gather", block,
                         "gathered chunks cannot see each other's headings")
        self.assertIn("taken", block)

    def test_a_failed_chunk_keeps_its_original_text(self):
        """Losing a section to a timeout would be the very failure this guards."""
        block = self.src.split("async def _results_by_chunk")[1]
        self.assertIn("return chunk, owned", block)

    def test_the_reference_list_is_carried_not_rewritten(self):
        block = self.src.split("def _split_pathway_sections")[1][:1200]
        self.assertIn('"### References"', block)

    def test_caveats_close_the_section(self):
        # The classification lives in _partition_sections since it became
        # evidence-based; it is pure so it can be replayed over real reports
        # without a gateway, which is how four production rejections were
        # reproduced offline.
        block = self.src.split("def _partition_sections")[1]
        block = block.split("\nasync def ")[0]
        self.assertIn("LAST = (", block)
        self.assertIn("closing", block)


class ResultsSectionDropsTheDossierFurniture(unittest.TestCase):
    """Found by running a LIVE job, not by testing one report.

    Job Z2x664211R came back with prose AND a "# Key Findings" bullet block, a
    17-row table, and "Limitations and Caveats" three times. Every one of these
    passed on the report this stage was developed against, because that report
    happened to use `## Key Findings` under an H1 title while the live one used
    `# Key Findings` directly. One report is not a sample.
    """

    def setUp(self):
        with open(SRC) as fh:
            self.src = fh.read()

    def test_the_splitter_sees_h1_headings_too(self):
        """An H2-only split sweeps a "# Key Findings" section into the preamble,
        which is carried verbatim -- so the one block the rewrite exists to
        remove survives untouched."""
        block = self.src.split("def _split_pathway_sections")[1][:1400]
        self.assertIn("#{1,2}", block,
                      "the section split must match H1 as well as H2")

    def test_the_pathway_table_is_dropped_when_a_section_is_written(self):
        """A Results section carries its numbers in sentences. A 17-row table
        under it is exactly the dossier furniture being removed.

        Anchored on the CONDITION, not on a sentence of the comment above it.
        This test broke once because the comment was rewritten -- and the
        comment was rewritten because it had been claiming the opposite of what
        the code did for as long as both existed.
        """
        block = self.src.split("report = report.rstrip() + \"\\n\\n\" + "
                               "render_pathway_table(pathways)")[0]
        guard = block[block.rindex("\n    if "):]
        self.assertIn("not results_written", guard)
        self.assertIn("## Enriched Pathway Summary", guard)

    def test_caveats_are_deduplicated_by_heading(self):
        """A stitched dossier carries one caveats block per stitched part, and
        the copies differ in wording -- so a body-text key keeps them all. One
        live report ended with three."""
        block = self.src.split("async def _results_by_chunk")[1]
        block = block.split("\nasync def ")[0].split("\ndef ")[0]
        self.assertIn("by_head", block)
        self.assertNotIn('[:200]', block,
                         "keying the dedup on body text is what let three "
                         "differently-worded copies through")

    def test_a_chunk_is_told_not_to_write_its_own_caveats(self):
        """Two carried copies plus one the model wrote = three."""
        prompt = " ".join(self.src.split("RESULTS_CHUNK_PROMPT")[1][:1600].lower().split())
        self.assertIn("limitations", prompt)
        self.assertIn("caveats", prompt)

    def test_the_chunk_size_is_derived_not_fixed(self):
        """A flat 4 gives a 4-section report exactly ONE subsection -- one wall
        of text under one heading, which is not a Results section."""
        block = self.src.split("async def _results_by_chunk")[1]
        block = block.split("\nasync def ")[0].split("\ndef ")[0]
        self.assertIn("RESULTS_TARGET_SECTIONS", block)
        self.assertIn("max(2,", block,
                      "a chunk of one cannot find a theme, only restate the "
                      "pathway it was given")


if __name__ == "__main__":
    # Load from the MODULE, not a named class. A second TestCase was added to
    # this file and silently never ran -- the runner reported 13/13 green while
    # the new guard was untested. Tenth time a hand-written list has rotted in
    # this repo today.
    r = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if r.wasSuccessful() else 1)

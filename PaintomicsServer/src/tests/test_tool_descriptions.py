#!/usr/bin/env python3
"""Tool descriptions are prompt text, and prompt text goes stale.

Every description in TOOLBELT rides in EVERY Decide turn of every run, so it is
both the most-read documentation in the system and a standing token cost. Two
failure modes have already happened here:

  * a claim that stopped being true. read_paper said "an unread citation is the
    kind the verifier removes". Measured over 28 runs, citations to papers the
    agent had read passed verification at 73% against 84% for ones it had not --
    so the description was telling the agent the opposite of the evidence.

  * a claim that discouraged what works. check_my_citations said it was "worth
    running once", while the runs that ran it twice improved every time.

These tests do not check that a description is true -- nothing can. They check
that it does not promise an outcome the pipeline cannot deliver, and that it
stays short enough to be worth its place in every turn.

    python -m src.tests.test_tool_descriptions
"""
from __future__ import annotations

import os
import re
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.agent_loop import TOOLBELT  # noqa: E402

_PASSED, _FAILED = [], []

# Phrases that promise a verification outcome. The gate decides that, on the
# quote it can find, and no tool can commit to it in advance.
OUTCOME_PROMISES = (
    "the verifier removes", "will survive", "guarantees", "guaranteed",
    "always passes", "never removed", "ensures the citation",
)


def test_no_description_promises_a_verification_outcome():
    offenders = []
    for tool in TOOLBELT:
        text = (tool.description or "").lower()
        for phrase in OUTCOME_PROMISES:
            if phrase in text:
                offenders.append("%s: %r" % (tool.name, phrase))
    assert not offenders, (
        "descriptions promising what the gate decides: %s" % "; ".join(offenders))


def test_every_tool_has_a_description():
    missing = [t.name for t in TOOLBELT if not (t.description or "").strip()]
    assert not missing, "tools with no description: %s" % ", ".join(missing)


def test_descriptions_stay_affordable():
    """They are re-sent on every turn; a runaway description is a per-turn tax."""
    oversized = [(t.name, len(t.description or "")) for t in TOOLBELT
                 if len(t.description or "") > 700]
    assert not oversized, ("descriptions over 700 chars: %s"
                           % ", ".join("%s=%d" % o for o in oversized))
    total = sum(len(t.description or "") for t in TOOLBELT)
    assert total < 3500, (
        "the toolbelt's descriptions total %d chars, re-sent every Decide turn; "
        "trim one before adding another" % total)


def test_a_timing_claim_belongs_only_to_a_tool_that_costs_time():
    """The free tools say "instant and free" and must not grow a seconds figure;
    the expensive ones carry one because the agent budgets with it."""
    free = {"get_experiment_overview", "get_pathway_details",
            "notebook_write", "submit_report"}
    for tool in TOOLBELT:
        says_seconds = re.search(r"\b\d+\s*(?:s\b|seconds?)", tool.description or "")
        if tool.name in free:
            assert not says_seconds, (
                "%s is a free tool but advertises %r"
                % (tool.name, says_seconds.group(0)))


def test_the_lead_prompt_does_not_contradict_itself_about_checking_citations():
    """The prompt told the agent both to run check_my_citations before
    submitting and that running it was optional, three bullets apart.

    That matters more than a tidiness point: of the 28 archived runs that called
    the tool, the 10 that ran it again after a bad result improved every time and
    none got worse. Marking the most valuable tool in the belt "optional" is the
    line most likely to stop it being used twice.
    """
    from src.classes.AIInterpret import prompts
    text = prompts.SYSTEM_PROMPT_LEAD_AGENT
    mentions = [l.strip() for l in text.split("\n") if "check_my_citations" in l]
    assert mentions, "the prompt no longer mentions check_my_citations at all"
    optional = [l for l in mentions
                if "optional" in l.lower() or "if you like" in l.lower()]
    assert not optional, ("the prompt calls check_my_citations optional while also "
                          "requiring it: %s" % optional)
    assert len(mentions) == 1, (
        "check_my_citations is described in %d separate places, which is how the "
        "contradiction arose: %s" % (len(mentions), mentions))


# Prompts that are defined but used nowhere. The two V2 prompts that sat here
# since March 2026 were deleted in the dead-code pass (reports/deadcode.md);
# a new orphan goes in here only with a reason it is being kept.
KNOWN_ORPHAN_PROMPTS = set()


def test_the_lead_prompt_only_names_tools_that_exist():
    """A tool removed from TOOLBELT must also leave the standing orders.

    compare_gene_profiles was dropped on measured evidence (13 calls in 72 runs,
    none in the last 16) but stayed in step 2 of the Lead's standing orders for
    a further round of benchmarks: the agent's own instructions told it to reach
    for a tool the API would not let it call. The model cannot emit a call to an
    undeclared tool, so this never raised -- it just spent the most expensive
    prompt in the system telling the agent to plan around something absent.

    Deleting a tool is exactly when this drifts, because the toolbelt and the
    prose that documents it live in different files.
    """
    from src.classes.AIInterpret import prompts
    known = {t.name for t in TOOLBELT}
    # Every snake_case token in the prompt that looks like one of our tools:
    # the vocabulary is closed, so match against what the package ever defined.
    ever_defined = known | {"compare_gene_profiles", "get_gene_profile",
                            "notebook_read", "delegate_literature"}
    named = {w for w in re.findall(r"[a-z_][a-z_0-9]+", prompts.SYSTEM_PROMPT_LEAD_AGENT)
             if w in ever_defined}
    ghosts = sorted(named - known)
    assert not ghosts, (
        "the Lead's standing orders instruct tools that are not in TOOLBELT: %s. "
        "Remove the instruction, or put the tool back." % ", ".join(ghosts))


def test_no_new_orphan_prompts():
    """A prompt nobody sends is dead weight that reads as live configuration.

    SYSTEM_PROMPT_DELEGATED_INTERPRET was written, measured, reverted on the
    evidence -- citations fell 5 -> 18 under the old prompt against 7 -> 3 under
    it -- and then sat in the file looking exactly like the prompt that is
    actually used. The next person to tune delegation would have edited it and
    measured nothing.
    """
    from src.classes.AIInterpret import prompts

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    sources = []
    for base, _dirs, files in os.walk(os.path.join(root, "src")):
        if "/tests" in base or "__pycache__" in base:
            continue
        for name in files:
            if name.endswith(".py") and name != "prompts.py":
                with open(os.path.join(base, name)) as handle:
                    sources.append(handle.read())
    blob = "\n".join(sources)

    orphans = sorted(name for name in dir(prompts)
                     if name.startswith("SYSTEM_PROMPT")
                     and name not in KNOWN_ORPHAN_PROMPTS
                     and name not in blob)
    assert not orphans, (
        "prompt constants nobody sends: %s -- delete them or wire them up"
        % ", ".join(orphans))


# Top-level definitions in src/classes/AIInterpret that nothing calls and that
# are kept on purpose. Empty since the 2026-09 cleanup removed the seven that
# had accumulated (the v1 redactor, verify_report, the two-pass and synthesis
# prompt builders, the interpretation executor and the Verdict model); a NEW
# one fails the build.
KNOWN_ORPHAN_DEFS = set()


def _names_read_in(source):
    """Every name this source READS, and how often, by the parser's reckoning.

    Counts ast.Name loads and attribute accesses; ignores every mention that is
    only text -- docstrings, comments, prose in a commit-worthy explanation.
    That distinction is the whole point: a function referred to in its own
    docstring is not a function anybody calls.

    One pass per FILE, not one pass per (name, file). The per-name shape this
    replaces re-parsed the whole tree for every definition it wanted to score:
    189 definitions x 209 non-test sources = 39,501 ast.parse calls for a
    question 209 parses can answer. Measured on this checkout, the suite went
    from 50.2 s to 1.5 s with no change to what it asserts -- and it was 47% of
    one unit-test shard's budget against a 10-minute cap the shard has hit
    three times.
    """
    import ast as _ast
    from collections import Counter
    read = Counter()
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return read
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name):
            read[node.id] += 1
        elif isinstance(node, _ast.Attribute):
            read[node.attr] += 1
    return read


def test_no_new_orphan_definitions_in_the_ai_package():
    """A function nobody calls reads as live code to the next person.

    Every top-level definition in src/classes/AIInterpret must be read from
    somewhere outside the tests; the exceptions live in KNOWN_ORPHAN_DEFS.
    """
    import ast
    from collections import Counter

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    package = os.path.join(root, "src", "classes", "AIInterpret")
    sources = {}
    for base, dirs, files in os.walk(os.path.join(root, "src")):
        if "__pycache__" in base:
            continue
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(base, name)
                with open(path) as handle:
                    sources[path] = handle.read()

    # Scored once, off the sources that are not tests -- a helper alive only in
    # its own test is still dead in the product.
    read_outside_tests = Counter()
    for other, body in sources.items():
        if "/tests/" not in other.replace("\\", "/"):
            read_outside_tests.update(_names_read_in(body))

    orphans = []
    for path, text in sorted(sources.items()):
        if not path.startswith(package):
            continue
        for node in ast.parse(text).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                if name.startswith("__") or name in KNOWN_ORPHAN_DEFS:
                    continue
                # Counted from the AST, not from text. A regex over sources
                # counts the name in its own docstring, in a comment, and in a
                # test -- so _writer_window scored three "uses" while having
                # zero call sites, and this test passed while the function was
                # dead. Tests are excluded on purpose: a helper alive only in
                # its own test is still dead in the product.
                if read_outside_tests[name] <= 0:
                    orphans.append("%s:%s" % (os.path.basename(path), name))
    assert not orphans, (
        "definitions nothing calls: %s -- wire them up, delete them, or add them "
        "to KNOWN_ORPHAN_DEFS with a reason" % ", ".join(orphans))


def test_the_evidence_block_separates_data_claims_from_literature_claims():
    """Round 30 shipped 16 citations across 62 161 characters of prose (0.26 per
    thousand) against the workflow arm's 24 across 26 599 (0.90). It grounds
    what it cites and then writes a great deal more that cites nothing. The
    block has to name the difference, or the writer keeps producing
    literature-flavoured prose with no passage behind it."""
    from src.classes.AIInterpret import prompts
    block = prompts.build_evidence_shelf_block({1: "A passage from a paper."})
    lowered = block.lower()
    assert "your data" in lowered and "literature" in lowered, (
        "the block does not distinguish the two kinds of sentence")
    assert "no citation belongs on these" in lowered, (
        "data claims are not excused from citation, so the writer will force one")
    assert "do not say it" in lowered or "leave it out" in lowered, (
        "unsupportable mechanism is not given an exit")
    assert len(block) < 1500, (
        "the block rides in every delegated prompt; %d chars is a tax" % len(block))


def _check(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print("PASS  %s" % name)
    except Exception:
        _FAILED.append((name, traceback.format_exc()))
        print("FAIL  %s" % name)


def main():
    for t in (test_no_description_promises_a_verification_outcome,
              test_every_tool_has_a_description,
              test_descriptions_stay_affordable,
              test_a_timing_claim_belongs_only_to_a_tool_that_costs_time,
              test_the_lead_prompt_does_not_contradict_itself_about_checking_citations,
              test_the_lead_prompt_only_names_tools_that_exist,
              test_no_new_orphan_prompts,
              test_no_new_orphan_definitions_in_the_ai_package,
              test_the_evidence_block_separates_data_claims_from_literature_claims):
        _check(t.__name__, t)
    print("\nPassed: %d / %d" % (len(_PASSED), len(_PASSED) + len(_FAILED)))
    if _FAILED:
        for name, msg in _FAILED:
            print("\n--- %s ---\n%s" % (name, msg))
        sys.exit(1)


if __name__ == "__main__":
    main()

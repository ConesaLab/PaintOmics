"""The reviewer's outcome step, and the two-job shape it depends on.

`.github/scripts/review-outcome.sh` is what turns "the action exited 0" into
"a review happened": it reads the transcript claude-code-action leaves in
RUNNER_TEMP and fails the job when nothing was reviewed, which is also the
signal that sends the pull request to the gateway job. Every branch of it is
exercised here on hand-made transcripts in the shapes the CLI really writes
(the fields are the ones the 2026-09-15 rate-limit runs and the 2026-09-17
gateway runs produced), because a script that misreads one of them either
double-reviews a diff on the gateway or, worse, calls a dead session clean.

The second half checks the workflow file for the contract the action imposes
and YAML cannot express: prepare.ts calls setupGitHubToken() before any model
credential is read, so a job without `id-token: write` dies with "Could not
fetch an OIDC token" before it reaches the gateway. The first cut of the
fallback job did exactly that. The file is read as text, not parsed: PyYAML
is not a requirement of the unit-test runner and a skipped invariant is no
invariant.

Offline, no fixtures, no server; needs bash and jq (present on every runner):

    cd PaintomicsServer && python -m src.tests.test_review_outcome_script
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCRIPT = os.path.join(REPO, ".github", "scripts", "review-outcome.sh")
WORKFLOW = os.path.join(REPO, ".github", "workflows", "code-review.yml")

# Planted in every tool result and tool input below. The script must never
# print either: tool results carry the diff, and the log is public.
SENTINEL = "DIFF-LINE-THAT-MUST-NOT-REACH-THE-LOG"

INLINE_COMMENT = "mcp__github_inline_comment__create_inline_comment"


# --- transcript records in the shapes claude-code-action writes ---------------

def result(text, is_error=False, **extra):
    record = {"type": "result", "subtype": "success", "is_error": is_error,
              "num_turns": 4, "duration_ms": 10576, "total_cost_usd": 0.25,
              "result": text}
    record.update(extra)
    return record


def tool_use(name):
    return {"type": "tool_use", "id": "toolu_1", "name": name,
            "input": {"body": SENTINEL}}


def assistant(*content, **extra):
    record = {"type": "assistant",
              "message": {"role": "assistant", "content": list(content)}}
    record.update(extra)
    return record


def tool_result(text):
    return {"type": "user",
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                     "content": text + "\n" + SENTINEL}]}}


SYSTEM = {"type": "system", "subtype": "init", "cwd": "/work",
          "model": "deepseek-ai/DeepSeek-V4-Flash-0731", "tools": []}


def run_outcome(records):
    """Run the script on `records`; None means no file, "" an empty one."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "claude-execution-output.json")
        if records == "":
            open(path, "w").close()
        elif records is not None:
            with open(path, "w") as fh:
                json.dump(records, fh)
        env = dict(os.environ, EXECUTION_FILE=path)
        proc = subprocess.run(["bash", SCRIPT], env=env, capture_output=True,
                              text=True, timeout=60)
    return proc.returncode, proc.stdout + proc.stderr


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"),
                     "the outcome script needs bash and jq")
class OutcomeScriptTest(unittest.TestCase):

    # -- the four ways a run reviews nothing --------------------------------

    def test_missing_transcript_fails_and_names_the_validation_skip(self):
        code, out = run_outcome(None)
        self.assertEqual(code, 1, out)
        self.assertIn("no transcript", out)
        self.assertIn("NOT reviewed", out)
        # The one cause a reader cannot see in the action log: the skip is a
        # warning that exits 0, and it happens on every edit of the workflow.
        self.assertIn("workflow-validation skip", out)

    def test_empty_transcript_is_the_same_as_none(self):
        code, out = run_outcome("")
        self.assertEqual(code, 1, out)
        self.assertIn("no transcript", out)

    def test_quota_rejection_fails_and_prints_the_reason(self):
        limit = "You've hit your weekly limit · resets Sep 17, 5pm (UTC)"
        code, out = run_outcome([
            assistant({"type": "text", "text": limit}, error="rate_limit"),
            result(limit, is_error=True, num_turns=1, total_cost_usd=0),
        ])
        self.assertEqual(code, 1, out)
        self.assertIn("ended in an error", out)
        # The log must NAME the cause; the action's own output never does.
        self.assertIn("weekly limit", out)
        self.assertIn("last assistant error: rate_limit", out)
        self.assertIn("is_error=true", out)

    def test_silent_empty_session_fails(self):
        # Reproduced 2026-09-17 on the gateway model: one Read that failed,
        # then the session ended with `is_error: false` and an empty result.
        code, out = run_outcome([
            SYSTEM,
            assistant(tool_use("Read")),
            tool_result("File does not exist."),
            assistant(tool_use("Bash")),
            tool_result("fatal: not a git repository"),
            result("", num_turns=5),
        ])
        self.assertEqual(code, 1, out)
        self.assertIn("no comments and no summary", out)
        self.assertIn("tool calls made:        2", out)

    # -- the two ways a run reviews something ---------------------------------

    def test_review_that_posted_comments_passes(self):
        code, out = run_outcome([
            SYSTEM,
            assistant(tool_use("Task")),
            tool_result("subagent findings"),
            assistant(tool_use(INLINE_COMMENT), tool_use(INLINE_COMMENT)),
            tool_result("comment created"),
            result("Reviewed 3 files; 2 findings posted inline."),
        ])
        self.assertEqual(code, 0, out)
        self.assertIn("a review happened", out)
        self.assertIn("inline comments posted: 2", out)
        # jq's `//` treats false as absent; the header used to print
        # `is_error=none` on every successful run.
        self.assertIn("is_error=false", out)
        self.assertNotIn("is_error=none", out)

    def test_clean_diff_with_a_summary_passes(self):
        code, out = run_outcome([
            SYSTEM,
            assistant(tool_use("Read")),
            tool_result("def f(): return 1"),
            result("No issues found in the two changed files."),
        ])
        self.assertEqual(code, 0, out)
        self.assertIn("a review happened", out)
        self.assertIn("inline comments posted: 0", out)

    def test_permission_denials_warn_but_do_not_fail(self):
        code, out = run_outcome([
            SYSTEM,
            assistant(tool_use("Read")),
            tool_result("ok"),
            result("Reviewed.", permission_denials=[
                {"tool_name": "Bash", "tool_input": {"command": SENTINEL}},
                {"tool_name": "WebFetch", "tool_input": {"url": SENTINEL}},
                {"tool_name": "Bash", "tool_input": {"command": SENTINEL}}]),
        ])
        self.assertEqual(code, 0, out)
        # Names the tools, once each, so the allowlist can be widened without
        # reading the transcript -- and never the inputs, which carry the diff.
        self.assertIn("::warning::3 permission denial(s) on Bash, WebFetch", out)
        self.assertNotIn(SENTINEL, out)

    def test_the_last_result_record_is_the_verdict(self):
        # The CLI can write more than one result record (a subagent's before
        # the orchestrator's); the transcript's verdict is the final one.
        code, out = run_outcome([
            SYSTEM,
            result("", is_error=True, num_turns=1),
            assistant(tool_use(INLINE_COMMENT)),
            tool_result("comment created"),
            result("One finding posted."),
        ])
        self.assertEqual(code, 0, out)
        self.assertIn("a review happened", out)

    # -- what it must never print ----------------------------------------------

    def test_tool_results_and_tool_inputs_never_reach_the_log(self):
        for records in (
            [SYSTEM, assistant(tool_use("Read")), tool_result("x"), result("")],
            [SYSTEM, assistant(tool_use(INLINE_COMMENT)), tool_result("x"),
             result("done")],
        ):
            _, out = run_outcome(records)
            self.assertNotIn(SENTINEL, out)


# --- the workflow's shape, read as text ---------------------------------------

def _uncommented(path=WORKFLOW):
    with open(path, encoding="utf-8") as fh:
        return [line for line in fh.read().splitlines()
                if not line.lstrip().startswith("#")]


def _job(name):
    """The lines of one job under `jobs:`, comments stripped."""
    lines = _uncommented()
    start = lines.index("jobs:")
    heads = [i for i, line in enumerate(lines[start + 1:], start + 1)
             if re.match(r"^  [a-z][a-z0-9-]*:\s*$", line)]
    for pos, head in enumerate(heads):
        if lines[head].strip() == name + ":":
            end = heads[pos + 1] if pos + 1 < len(heads) else len(lines)
            return lines[head:end]
    raise AssertionError("no job %r in %s" % (name, WORKFLOW))


def _if_expression(job):
    """The job-level `if:` of a job block, single- or multi-line, as one string."""
    for i, line in enumerate(job):
        if re.match(r"^    if:", line):
            text = line.split("if:", 1)[1].strip()
            if text in (">-", ">", "|", "|-"):
                text = ""
                for cont in job[i + 1:]:
                    if cont.startswith("      "):
                        text += " " + cont.strip()
                    else:
                        break
            return text
    raise AssertionError("no job-level if: in %s" % job[0])


class WorkflowShapeTest(unittest.TestCase):

    def test_both_jobs_can_mint_the_claude_bot_identity(self):
        # setupGitHubToken() runs before any model credential is read and
        # needs the OIDC token; a job without this dies before the gateway.
        for name in ("review", "review-fallback"):
            self.assertIn("      id-token: write", _job(name), name)

    def test_fallback_fires_on_failure_or_the_label_and_never_on_cancelled(self):
        condition = _if_expression(_job("review-fallback"))
        self.assertIn("always()", condition)
        self.assertIn("needs.review.result == 'failure'", condition)
        self.assertIn("'review:gateway'", condition)
        # `cancelled` is every force-push mid-review; a fallback on those
        # double-reviews the diff while the replacement run is still going.
        self.assertNotIn("!= 'success'", condition)
        self.assertNotIn("cancelled", condition)

    def test_the_label_skips_the_subscription(self):
        condition = _if_expression(_job("review"))
        self.assertIn("!contains(", condition)
        self.assertIn("'review:gateway'", condition)

    def test_both_jobs_share_one_tool_allowlist(self):
        lines = _uncommented()
        self.assertEqual(sum(1 for l in lines if l.startswith("  REVIEW_TOOLS:")), 1)
        for name in ("review", "review-fallback"):
            job = "\n".join(_job(name))
            self.assertIn('--allowedTools "${{ env.REVIEW_TOOLS }}"', job, name)
            self.assertNotIn(INLINE_COMMENT + ",", job, name)

    def test_gateway_job_points_every_model_alias_at_the_gateway(self):
        job = "\n".join(_job("review-fallback"))
        self.assertIn("ANTHROPIC_BASE_URL: https://llm.iiia.es", job)
        self.assertIn("anthropic_api_key: ${{ secrets.AI_CSIC_API_KEY }}", job)
        self.assertNotIn("claude_code_oauth_token", job)
        aliases = re.findall(r"ANTHROPIC_DEFAULT_(HAIKU|SONNET|OPUS)_MODEL: (\S+)", job)
        self.assertEqual(sorted(a for a, _ in aliases), ["HAIKU", "OPUS", "SONNET"])
        models = {m for _, m in aliases}
        self.assertEqual(len(models), 1, aliases)
        self.assertIn("--model %s" % models.pop(), job)

    def test_primary_job_uses_the_subscription_and_nothing_else(self):
        job = "\n".join(_job("review"))
        self.assertIn("claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}", job)
        self.assertNotIn("ANTHROPIC_BASE_URL", job)
        self.assertNotIn("anthropic_api_key", job)

    def test_outcome_step_runs_always_in_both_jobs(self):
        for name in ("review", "review-fallback"):
            job = _job(name)
            runs = [i for i, l in enumerate(job)
                    if l.strip() == "run: bash .github/scripts/review-outcome.sh"]
            self.assertEqual(len(runs), 1, name)
            step = job[max(0, runs[0] - 6):runs[0]]
            self.assertIn("        if: always()", step, name)

    def test_background_subagents_are_disabled_in_both_jobs(self):
        # A subagent launched into the background dies with the
        # non-interactive session that spawned it (run 33546158945).
        for name in ("review", "review-fallback"):
            self.assertIn('          CLAUDE_CODE_DISABLE_BACKGROUND_TASKS: "1"',
                          _job(name), name)


if __name__ == "__main__":
    unittest.main()

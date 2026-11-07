"""The client's JavaScript tests run on every pull request, all of them.

PaintomicsClient/tests holds node:test suites for the input-format checks, the
classification palette and the walk's Results draft, with a runner,
PaintomicsClient/tests/run.sh. No workflow called it, and format-roles.test.js
failed on master from 2026-08-27 (20ad645b removed the one-1-per-row design
rule on purpose; the test still expected it) without anyone being told. The PR
workflow's Lint job runs it now. This pins the ways that could quietly stop
being true:

    - the step is deleted or commented out of pr.yml;
    - the step stays but can no longer fail the job: `|| true` on the command,
      `continue-on-error` or `if:` on the step, `continue-on-error` on the job,
      or a line that only names run.sh. (An `if:` on the job is not on this
      list: Gate fails on a skipped job, so it could not pass unnoticed.);
    - the Lint job drops out of the list the required Gate job waits on;
    - a test file is added where run.sh's glob does not look. Node's --test
      does not search a directory it is given, so run.sh names the files with
      a shell glob, `PaintomicsClient/tests/*/*.test.js`; a file one level up
      or two levels down would never run, and nothing would say so.

It reads the workflow line by line because the unit-test jobs do not install a
YAML parser. Offline, no fixtures, no node:

    cd PaintomicsServer && python -m src.tests.test_client_tests_run_in_ci
"""
import glob
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PR_WORKFLOW = os.path.join(REPO, ".github", "workflows", "pr.yml")
LOCAL_INSTRUCTIONS = [os.path.join(REPO, "docs", "dev", "ci.md"),
                      os.path.join(REPO, "CONTRIBUTING.md")]
CLIENT_TESTS = os.path.join(REPO, "PaintomicsClient", "tests")
RUNNER = os.path.join(CLIENT_TESTS, "run.sh")
RUNNER_PATH = "PaintomicsClient/tests/run.sh"
COMMAND = "sh " + RUNNER_PATH


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _jobBlock(workflow, job):
    """The lines of one job under `jobs:`, from its key to the next job's."""
    lines = workflow.splitlines()
    start = lines.index("  %s:" % job)
    end = next((index for index in range(start + 1, len(lines))
                if re.match(r"^  [A-Za-z0-9_-]+:\s*$", lines[index])), len(lines))
    return lines[start:end]


def _steps(jobLines):
    """Each step of a job as its lines, with comment lines left out."""
    steps = []
    for line in jobLines:
        if line.lstrip().startswith("#"):
            continue
        if line.startswith("      - "):
            steps.append([line])
        elif steps:
            steps[-1].append(line)
    return steps


def _stepKeys(step):
    """The keys a step sets: `- name:` on its first line, the rest under it."""
    matches = (re.match(r"^ {6}(?:- | {2})([A-Za-z-]+):", line) for line in step)
    return {match.group(1) for match in matches if match}


def _lastCommand(step):
    """The last line a step runs. With it last, its exit code is the step's."""
    for position, line in enumerate(step):
        run = re.match(r"^ {6}(?:- | {2})run:\s*(.*)$", line)
        if not run:
            continue
        if run.group(1).strip() not in ("|", ">", "|-", ">-"):
            return run.group(1).strip()
        body = [text.strip() for text in step[position + 1:]
                if text.startswith(" " * 10) and text.strip()]
        return body[-1] if body else None
    return None


def _clientStepProblem(workflow):
    """Why this workflow's lint job would not fail on a failing client test,
    or None when it would."""
    job = _jobBlock(workflow, "lint")
    if any(re.match(r"^ {4}continue-on-error:", line) for line in job):
        return "pr.yml's lint job has `continue-on-error:`, so it cannot fail"
    for step in _steps(job):
        if (_lastCommand(step) == COMMAND
                and not {"if", "continue-on-error"} & _stepKeys(step)):
            return None
    return ("no step of pr.yml's lint job ends in `%s` with no `if:` or "
            "`continue-on-error:` on it" % COMMAND)


class ClientTestsRunInCiTest(unittest.TestCase):

    def test_the_lint_job_runs_the_client_test_runner(self):
        self.assertIsNone(_clientStepProblem(_read(PR_WORKFLOW)))

    def test_a_step_that_cannot_fail_the_job_does_not_count(self):
        """Each of these still names run.sh, so a check that only looks for
        the path would pass them all."""
        workflow = _read(PR_WORKFLOW)
        run = "          %s\n" % COMMAND
        step = "      - name: Client tests\n"
        job = "    name: Lint\n"
        for why, old, new in [
                ("the step is deleted", step + "        run: |\n          node --version\n" + run, ""),
                ("the command is commented out", run, "          # %s\n" % COMMAND),
                ("its failure is swallowed", run, "          %s || true\n" % COMMAND),
                ("a line only names the runner", run, "          echo skipped %s\n" % RUNNER_PATH),
                ("its exit code is thrown away", run, "          set +e\n%s          true\n" % run),
                ("the step may fail", step, step + "        continue-on-error: true\n"),
                ("the step never runs", step, step + "        if: false\n"),
                ("the job may fail", job, job + "    continue-on-error: true\n")]:
            with self.subTest(why):
                self.assertIn(old, workflow, "pr.yml no longer has this to change")
                self.assertIsNotNone(_clientStepProblem(workflow.replace(old, new)))

    def test_the_lint_job_is_one_the_gate_waits_on(self):
        needs = re.search(r"^    needs:\s*\[([^\]]*)\]", "\n".join(
            _jobBlock(_read(PR_WORKFLOW), "gate")), re.M)
        self.assertIsNotNone(needs, "the gate job has no needs: list")
        self.assertIn("lint", [name.strip() for name in needs.group(1).split(",")])

    def test_the_local_instructions_include_them(self):
        """Both pages list what to run locally before the Lint job does."""
        for path in LOCAL_INSTRUCTIONS:
            with self.subTest(os.path.relpath(path, REPO)):
                self.assertIn(COMMAND, _read(path))

    def test_every_client_test_file_is_one_the_runner_finds(self):
        command = re.search(r"^exec node --test (\S+)\s*$", _read(RUNNER), re.M)
        self.assertIsNotNone(command, "run.sh no longer ends in `exec node --test <glob>`")
        found = set(glob.glob(os.path.join(REPO, command.group(1))))
        present = set(glob.glob(os.path.join(CLIENT_TESTS, "**", "*.test.js"),
                                recursive=True))
        self.assertTrue(present, "no client test files at all under %s" % CLIENT_TESTS)
        self.assertEqual(sorted(os.path.relpath(path, REPO) for path in present - found), [],
                         "test files run.sh's glob %r never runs" % command.group(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)

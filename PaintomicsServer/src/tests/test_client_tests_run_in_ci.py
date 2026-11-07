"""The client's JavaScript tests run on every pull request, all of them.

PaintomicsClient/tests holds node:test suites for the input-format checks, the
classification palette and the walk's Results draft, with a runner,
PaintomicsClient/tests/run.sh. No workflow called it, and format-roles.test.js
failed on master from 2026-08-27 (20ad645b removed the one-1-per-row design
rule on purpose; the test still expected it) without anyone being told. The PR
workflow's Lint job runs it now. This pins three ways that could quietly stop
being true:

    - the step is deleted or commented out of pr.yml;
    - the Lint job drops out of the list the required Gate job waits on;
    - a test file is added where run.sh's glob does not look. Node's --test
      does not search a directory it is given, so run.sh names the files with
      a shell glob, `PaintomicsClient/tests/*/*.test.js`; a file one level up
      or two levels down would never run, and nothing would say so.

Offline, no fixtures, no node:

    cd PaintomicsServer && python -m src.tests.test_client_tests_run_in_ci
"""
import glob
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PR_WORKFLOW = os.path.join(REPO, ".github", "workflows", "pr.yml")
CLIENT_TESTS = os.path.join(REPO, "PaintomicsClient", "tests")
RUNNER = os.path.join(CLIENT_TESTS, "run.sh")
RUNNER_PATH = "PaintomicsClient/tests/run.sh"


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


class ClientTestsRunInCiTest(unittest.TestCase):

    def test_the_lint_job_runs_the_client_test_runner(self):
        """Not in a comment: a step line that calls run.sh."""
        live = [line for line in _jobBlock(_read(PR_WORKFLOW), "lint")
                if RUNNER_PATH in line and not line.lstrip().startswith("#")]
        self.assertTrue(live, "no step of pr.yml's lint job runs %s" % RUNNER_PATH)

    def test_the_lint_job_is_one_the_gate_waits_on(self):
        needs = re.search(r"^    needs:\s*\[([^\]]*)\]", "\n".join(
            _jobBlock(_read(PR_WORKFLOW), "gate")), re.M)
        self.assertIsNotNone(needs, "the gate job has no needs: list")
        self.assertIn("lint", [name.strip() for name in needs.group(1).split(",")])

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

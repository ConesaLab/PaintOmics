#!/usr/bin/env python3
"""A job whose access date could not be updated says so.

Why this exists
---------------
The browser posts /pa_touch_job every time it shows a job, and retention reads
the access date this writes: a job not opened for 7 days (guests) or 14 days
(registered users) is removed. pathwayAcquisitionTouchJob handed its failures to

    handleException(response, ex, __file__, "pathwayAcquisitionTouchJob", jobID=jobID)

and handleException takes no jobID, so that call raised TypeError. The `return`
in the `finally` then discarded the TypeError, and the route answered 200 with an
empty body: the browser logged that the access date had been updated, the server
logged nothing, and the job kept its old date.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_touch_job_reports_its_own_error
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src import paintomicsserver as server
from src.conf.serverconf import SERVER_SUBDOMAIN
from src.paintomicsserver import Response
from src.servlets import PathwayAcquisitionServlet as servlet


class _Request(object):
    cookies = {}

    def __init__(self, jobID):
        self.form = {"jobID": jobID}


def _manager(failure=None):
    manager = mock.MagicMock()
    if failure is not None:
        manager.return_value.touchAccessDate.side_effect = failure
    return mock.patch.object(servlet, "JobInformationManager", manager)


class TouchJobTest(unittest.TestCase):

    def test_a_failed_touch_answers_its_error(self):
        with _manager(RuntimeError("the jobs collection is not reachable")), \
                self.assertLogs(level="ERROR") as logged:
            response = servlet.pathwayAcquisitionTouchJob(_Request("J1"), Response())
        self.assertEqual(response.status, 400)
        self.assertIs(response.content["success"], False)
        self.assertIn("the jobs collection is not reachable", response.content["message"])
        self.assertIn("pathwayAcquisitionTouchJob", response.content["message"])
        self.assertTrue(any("the jobs collection is not reachable" in line for line in logged.output),
                        "the server log does not have it")

    def test_a_touch_that_works_still_says_so(self):
        with _manager() as manager:
            response = servlet.pathwayAcquisitionTouchJob(_Request("J1"), Response())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content, {"success": True})
        manager.return_value.touchAccessDate.assert_called_once_with("J1")

    def test_the_route_delivers_the_error(self):
        with mock.patch.object(server, "configureLogging"), \
                mock.patch.object(server, "KeggInformationManager"), \
                mock.patch.object(server, "JobInformationManager"), \
                mock.patch.object(server.Application, "ensureIndexes"), \
                mock.patch.object(server.Application, "startScheludeTasks"):
            application = server.Application()
        with _manager(RuntimeError("the jobs collection is not reachable")):
            answer = application.app.test_client().post(SERVER_SUBDOMAIN + "/pa_touch_job", data={"jobID": "J1"})
        self.assertEqual(answer.status_code, 400)
        body = json.loads(answer.get_data(as_text=True))
        self.assertIs(body["success"], False)
        self.assertIn("the jobs collection is not reachable", body["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

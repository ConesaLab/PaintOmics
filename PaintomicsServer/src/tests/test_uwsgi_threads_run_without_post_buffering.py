"""deploy/uwsgi.ini must not combine harakiri, threads and post-buffering.

uWSGI keeps ONE harakiri deadline per worker, shared by all its threads. While
it post-buffers a request body it adds the harakiri timeout to that deadline
before every chunk (core/reader.c, inc_harakiri: `harakiri += 300`), and every
thread that finishes a request resets it to 0. When a request finishes while
another thread's upload is still being read, the next chunk computes 0 + 300:
a deadline in January 1970. The master sees it expired and SIGKILLs the worker
within a second.

On paintomics.org (processes = 1, threads = 16) that killed the only worker
nine times from 2026-09-22 to 09-24, always one or two seconds into a
POST /pa_step1, /pa_save_visual_options or /pa_adjust_pvalues, and every
request, queued job and cached job on the server went with it. Two users
reported the upload that tripped it as "the connection was dropped before any
answer". Reproduced against uWSGI 2.0.31 outside the app: a 200 KB upload
sent in 8 KB pieces, with one other request a second, killed the worker 3 of
3 times with post-buffering = 8192 and 0 of 3 times without it.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_uwsgi_threads_run_without_post_buffering
"""
import os
import unittest

UWSGI_INI = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "deploy", "uwsgi.ini"))


def read_ini(path):
    """uWSGI's ini as {key: value}; the last occurrence wins, as in uWSGI."""
    options = {}
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.split("#", 1)[0].split(";", 1)[0].strip()
            if not line or line.startswith("[") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            options[key.strip()] = value.strip()
    return options


class UwsgiThreadsRunWithoutPostBuffering(unittest.TestCase):

    def setUp(self):
        if not os.path.exists(UWSGI_INI):
            self.skipTest("deploy/uwsgi.ini is not in this checkout")
        self.options = read_ini(UWSGI_INI)

    def test_the_premise_threads_share_one_harakiri(self):
        # If either stops being true the rule below is moot; say so rather
        # than pass for the wrong reason.
        self.assertGreater(int(self.options.get("threads", "1")), 1)
        self.assertGreater(int(self.options.get("harakiri", "0")), 0)

    def test_no_post_buffering(self):
        value = self.options.get("post-buffering", "0")
        self.assertEqual(int(value), 0,
                         "post-buffering = %s with threads and harakiri: a finished "
                         "request lets the next body chunk set a 1970 harakiri "
                         "deadline and the worker is killed" % value)


if __name__ == "__main__":
    unittest.main()

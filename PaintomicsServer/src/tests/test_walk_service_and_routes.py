#!/usr/bin/env python3
"""The walk as a server job: the shared pipeline, the stored progress and
view, the chat's walk tool, the Reactome expansion, and the two routes.

Offline: the synthetic organism from walker_fixture, scripted policies only,
an in-memory stand-in for aiWalkCollection and for the queue. What the model
says is not under test here; the contract around it is -- a refusal is stored
where the browser reads it, a second click never files a second walk, a walk
never starts without consent, and the chain reaches the browser while the walk
is still running. A walk whose job is deleted stops and leaves nothing behind;
a read-only job's walk is its owner's to replace; walks are capped per job and
across the server so the analyses keep a worker.

    cd PaintomicsServer && PYTHONPATH=. python -m src.tests.test_walk_service_and_routes
"""
import glob
import json
import os
import shutil
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.classes.AIInterpret.walker import network as net_mod    # noqa: E402
from src.classes.AIInterpret.walker import policies              # noqa: E402
from src.classes.AIInterpret.walker import service               # noqa: E402
from src.classes.AIInterpret.walker import overlay as ov_mod     # noqa: E402
from src.classes.AIInterpret.walker.walk import Walker, params_for  # noqa: E402
from src.tests import walker_fixture as fx                       # noqa: E402


class FakeWalkDAO(object):
    """aiWalkCollection in a dict, shared by every instance like the real one."""
    store = {}

    def save_progress(self, job_id, scope, data):
        doc = FakeWalkDAO.store.setdefault((job_id, scope), {"jobID": job_id, "scope": scope})
        doc.update(data)
        doc["updatedAt"] = datetime.utcnow()

    def update_existing(self, job_id, scope, data):
        doc = FakeWalkDAO.store.get((job_id, scope))
        if doc is None:
            return False
        doc.update(data)
        doc["updatedAt"] = datetime.utcnow()
        return True

    def find(self, job_id, scope):
        doc = FakeWalkDAO.store.get((job_id, scope))
        return dict(doc) if doc else None

    def closeConnection(self):
        return True


def _job_with_id(job_id="J1"):
    job = fx.make_job()
    job.getJobID = lambda: job_id
    return job


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = fx.make_data_dir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def setUp(self):
        FakeWalkDAO.store = {}

    def test_the_scope_rule_admits_pathway_ids_and_nothing_else(self):
        for scope in ("network", "pathway:mmu04068", "pathway:R-MMU-9614085", "pathway:opneBcellreceptorBCR"):
            self.assertEqual(service.check_scope(scope), scope)
        for scope in ("", "pathway:", "pathway:../x", "KEGG:mmu04068", "pathway:a b", "network2"):
            with self.assertRaises(service.WalkError, msg=scope):
                service.check_scope(scope)
        self.assertNotEqual(service.queue_id("J", "network"), service.queue_id("J", "pathway:mmu04068"))

    def test_a_card_edit_keeps_the_five_fields_trimmed(self):
        card = service.clean_card(json.dumps({"perturbation": "  Ikaros on  ", "axis": "x" * 400,
                                              "columns": "not editable", "value": 3}))
        self.assertEqual(card, {"perturbation": "Ikaros on", "axis": "x" * service.CARD_FIELD_MAX})
        self.assertIsNone(service.clean_card(""))
        self.assertIsNone(service.clean_card("{}"))
        with self.assertRaises(service.WalkError):
            service.clean_card("{not json")

    def test_the_pipeline_reports_every_stage_and_the_growing_chain(self):
        events = []

        def progress(stage, percent, detail, walker):
            events.append((stage, percent, len(walker.chain) if walker is not None else None))

        rec, _network, _graph, tag = service.run(_job_with_id(), "J1", "pathway:tst00001", policy="greedy",
                                                 data_dir=self.data_dir, use_mongo=False, progress=progress)
        self.assertEqual(tag, "KEGG:tst00001")
        stages = [e[0] for e in events]
        for stage in ("network", "card", "walk"):
            self.assertIn(stage, stages)
        legs = [e[2] for e in events if e[0] == "walk" and e[2] is not None]
        self.assertEqual(legs, sorted(legs), "the chain shown while walking went backwards")
        self.assertEqual(legs[-1], len(rec["walk"]["chain"]))
        self.assertTrue(all(0 <= e[1] <= 100 for e in events))
        view = service.view(rec)
        json.dumps(view)
        self.assertEqual(view["design_card"]["axis_kind"], "time")
        self.assertEqual(view["design_card"]["unlabeled"], ["miRNA-seq"])
        first = view["chain"][0]
        self.assertTrue(first["from_label"] and first["to_label"])
        for leg in view["chain"]:
            self.assertIn(leg["to"], view["nodes"])

    def test_a_user_edit_of_the_card_reaches_the_walk(self):
        rec, *_ = service.run(_job_with_id(), "J1", "pathway:tst00001", policy="greedy", data_dir=self.data_dir,
                              use_mongo=False, card_override={"perturbation": "drug X against vehicle"})
        self.assertEqual(rec["design_card"]["perturbation"], "drug X against vehicle")
        self.assertEqual(rec["design_card"]["source"], "user")

    def _run_job(self, scope, job=None, filed=True, dao=FakeWalkDAO, policy="greedy"):
        """run_job as the queue calls it; ``filed`` stands for the route's
        queued document, which exists before any worker starts."""
        import src.common.DAO.AIWalkDAO as dao_module
        import src.common.JobInformationManager as jim_module
        saved = (dao_module.AIWalkDAO, jim_module.JobInformationManager, service.KEGG_DATA_DIR, service.mongo_for)

        class _JIM(object):
            def loadJobInstance(self, job_id):
                return job

        if filed:
            FakeWalkDAO().save_progress("J1", scope, {"status": "queued", "percent": 0})
        dao_module.AIWalkDAO = dao
        jim_module.JobInformationManager = _JIM
        service.KEGG_DATA_DIR = self.data_dir
        service.mongo_for = lambda organism: None
        try:
            service.run_job("J1", scope, None, policy)
        finally:
            (dao_module.AIWalkDAO, jim_module.JobInformationManager, service.KEGG_DATA_DIR,
             service.mongo_for) = saved
        return FakeWalkDAO.store.get(("J1", scope))

    def test_the_job_stores_a_view_when_done(self):
        doc = self._run_job("pathway:tst00001", _job_with_id())
        self.assertEqual(doc["status"], "done")
        self.assertIsNone(doc["liveJSON"])
        view = json.loads(doc["viewJSON"])
        self.assertTrue(view["chain"])
        self.assertIn("legs walked", doc["detail"])

    def test_a_refusal_is_stored_where_the_browser_reads_it(self):
        doc = self._run_job("pathway:tst99999", _job_with_id())
        self.assertEqual(doc["status"], "error")
        self.assertIn("tst99999", doc["detail"])
        gone = self._run_job("network", None)
        self.assertEqual(gone["status"], "error")
        self.assertIn("no longer exists", gone["detail"])

    def test_a_gateway_that_fails_is_an_error_the_user_can_retry(self):
        """A model loop that died on the transport is not a walk that found
        nothing: stored as done it was final, with no Retry and chat on an
        empty record."""
        from src.classes.AIInterpret.walker import sdk

        class _DeadClient(object):
            def complete_json(self, *args, **kwargs):
                raise RuntimeError("401 Malformed API Key")

        async def _refused(*args, **kwargs):
            raise RuntimeError("401 Malformed API Key")

        saved = (sdk.Runner.run, service.llm_client)
        sdk.Runner.run = staticmethod(_refused)
        service.llm_client = lambda: _DeadClient()
        try:
            doc = self._run_job("pathway:tst00001", _job_with_id(), policy="model")
        finally:
            sdk.Runner.run, service.llm_client = saved
        self.assertEqual(doc["status"], "error", doc.get("detail"))
        self.assertIn("AI service failed", doc["detail"])
        self.assertIn("Malformed API Key", doc["detail"])

    def test_a_walk_whose_document_is_gone_writes_nothing(self):
        self.assertIsNone(self._run_job("pathway:tst00001", _job_with_id(), filed=False),
                          "the worker re-created a walk document nobody filed")

    def test_a_job_deleted_mid_walk_stops_the_walk_and_stays_deleted(self):
        class _DeletedMidWalk(FakeWalkDAO):
            writes = 0

            def update_existing(self, job_id, scope, data):
                _DeletedMidWalk.writes += 1
                if _DeletedMidWalk.writes == 3:       # the user deletes the job
                    FakeWalkDAO.store.pop((job_id, scope), None)
                return FakeWalkDAO.update_existing(self, job_id, scope, data)

        self.assertIsNone(self._run_job("pathway:tst00001", _job_with_id(), dao=_DeletedMidWalk))
        self.assertLess(_DeletedMidWalk.writes, 8, "the walk kept writing after its document was deleted")

    def test_a_cancelled_walk_stops_at_its_next_turn(self):
        full, *_ = service.run(_job_with_id(), "J1", "network", policy="greedy", data_dir=self.data_dir,
                               use_mongo=False)
        self.assertGreater(len(full["walk"]["chain"]), 1)
        seen = {}

        def progress(stage, percent, detail, walker):
            if walker is not None:
                seen["walker"] = walker

        with self.assertRaises(service.WalkCancelled):
            service.run(_job_with_id(), "J1", "network", policy="greedy", data_dir=self.data_dir,
                        use_mongo=False, progress=progress,
                        cancelled=lambda: "walker" in seen and len(seen["walker"].chain) >= 1)
        walker = seen["walker"]
        self.assertTrue(walker.done)
        self.assertIn("cancelled", walker.stop_reason)
        self.assertLess(len(walker.chain), len(full["walk"]["chain"]))

    def test_the_chat_walks_from_a_named_gene_with_no_model(self):
        job = _job_with_id()
        out = service.quick_walk(job, "Aaa", 4, data_dir=self.data_dir, use_mongo=False)
        self.assertIn("Aaa", out)
        self.assertIn("layers", out)
        self.assertIn("No node named", service.quick_walk(job, "Nope", 4, data_dir=self.data_dir, use_mongo=False))

    def test_a_walk_can_start_from_a_node_that_is_not_a_seed_candidate(self):
        net = net_mod.build_network("tst", self.data_dir)
        ov = ov_mod.overlay_job(net, fx.make_job())
        walker = Walker(net, ov, "network", params_for("network"))
        policies.greedy_from(walker, "g:6", 500)
        self.assertEqual(walker.plan["seeds"], ["g:6"])
        self.assertEqual(walker.plan["steps"], walker.params["ceiling"], "steps are not clamped to the ceiling")
        self.assertTrue(walker.done)
        refused = Walker(net, ov, "network", params_for("network"))
        self.assertIn("REFUSED", refused.start_at("g:nope", 3))

    def test_the_stored_walk_reads_as_chat_text_in_every_state(self):
        self.assertIn("No universal walk", service.stored_walk_text(None))
        self.assertIn("still running", service.stored_walk_text({"status": "running", "stage": "walk", "percent": 40}))
        self.assertIn("failed", service.stored_walk_text({"status": "error", "detail": "boom"}))
        rec, *_ = service.run(_job_with_id(), "J1", "pathway:tst00001", policy="greedy", data_dir=self.data_dir,
                              use_mongo=False)
        text = service.stored_walk_text({"status": "done", "viewJSON": json.dumps(service.view(rec))})
        self.assertIn("LEGS:", text)
        self.assertIn("Test pathway one", text)


class ReactomeExpansionTest(unittest.TestCase):
    def setUp(self):
        self.data_dir = fx.make_data_dir()
        org = os.path.join(self.data_dir, "current", "tst")
        os.makedirs(os.path.join(org, "reactome"))
        graph = {
            "dbId": 1, "stId": "R-TST-1", "nodes": [
                {"dbId": 11, "schemaClass": "EntityWithAccessionedSequence", "identifier": "P00001"},
                {"dbId": 12, "schemaClass": "EntityWithAccessionedSequence", "identifier": "P00002"},
                {"dbId": 20, "schemaClass": "Complex", "children": [21]},
                {"dbId": 21, "schemaClass": "DefinedSet", "children": [11, 20]},     # a cycle back to 20
                {"dbId": 30, "schemaClass": "CandidateSet", "children": [12]},
            ],
            "edges": [
                {"dbId": 100, "schemaClass": "Reaction", "inputs": [20], "outputs": [30]},
            ],
        }
        inhibited = {"dbId": 2, "stId": "R-TST-2", "nodes": graph["nodes"],
                     "edges": [{"dbId": 101, "schemaClass": "Reaction", "inputs": [], "inhibitors": [30],
                                "outputs": [20]}]}
        for name, g in (("R-TST-1", graph), ("R-TST-2", inhibited)):
            with open(os.path.join(org, "reactome", name + ".graph.json"), "w") as handle:
                json.dump(g, handle)

    def tearDown(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def test_a_complex_and_a_set_are_read_as_their_proteins(self):
        net = net_mod.build_network("tst", self.data_dir)
        edge = net.edges.get(("g:1", "g:2"))
        self.assertIsNotNone(edge, "the complex's protein never reached the set's protein")
        self.assertIn("Reactome:R-TST-1", edge["tags"])
        self.assertEqual(edge["sign"], 1)
        back = net.edges.get(("g:2", "g:1"))
        self.assertIsNotNone(back)
        self.assertEqual(back["sign"], -1)
        self.assertIn("Reactome:R-TST-2", back["tags"])

    def test_a_reaction_over_the_fan_cap_adds_nothing(self):
        saved = net_mod.REACTOME_FAN_MAX
        net_mod.REACTOME_FAN_MAX = 0
        try:
            net = net_mod.build_network("tst", self.data_dir)
        finally:
            net_mod.REACTOME_FAN_MAX = saved
        self.assertFalse([e for e in net.edges.values() if any(t.startswith("Reactome:") for t in e["tags"])])

    def test_the_cache_is_written_whole_under_its_own_name(self):
        net_mod.load_or_build("tst", self.data_dir)
        org = os.path.join(self.data_dir, "current", "tst")
        self.assertTrue(os.path.isfile(os.path.join(org, "universal_network.v%d.json.gz" % net_mod.CACHE_VERSION)))
        self.assertEqual(glob.glob(os.path.join(org, "*.tmp")), [])


def _mongo_reachable():
    try:
        from pymongo import MongoClient
        from src.conf.serverconf import MONGODB_HOST, MONGODB_PORT
        MongoClient(MONGODB_HOST, MONGODB_PORT, serverSelectionTimeoutMS=1500).admin.command("ping")
        return True
    except Exception:                                                 # noqa: BLE001
        return False


class WalkDAODriverTest(unittest.TestCase):
    """The DAO's writes as the real driver sees them. FakeWalkDAO stands in for
    it everywhere else, and it once accepted an update whose "$set" had been
    lost: pymongo refused every progress write of a live walk with "update
    only works with $ operators", and the walk never stored its result."""

    class _Collection(object):
        def __init__(self):
            self.calls = []

        def update_one(self, query, update, upsert=False):
            from pymongo.common import validate_ok_for_update
            validate_ok_for_update(update)
            self.calls.append((query, update, upsert))
            return type("Result", (), {"matched_count": 1})()

    class _Manager(object):
        def __init__(self, collection):
            self.collection = collection

        def getCollection(self, name):
            return self.collection

        def closeConnection(self):
            return True

    def test_both_writes_are_update_documents_and_only_filing_upserts(self):
        from src.common.DAO.AIWalkDAO import AIWalkDAO
        collection = self._Collection()
        dao = AIWalkDAO(dbManager=self._Manager(collection))
        dao.save_progress("J1", "network", {"status": "queued"})
        self.assertTrue(dao.update_existing("J1", "network", {"status": "running"}))
        (_, filed, filed_upsert), (_, update, update_upsert) = collection.calls
        self.assertTrue(filed_upsert)
        self.assertFalse(update_upsert, "the worker's write may never create a walk")
        self.assertEqual(update["$set"]["status"], "running")

    @unittest.skipUnless(_mongo_reachable(), "MongoDB not reachable")
    def test_against_mongodb_an_update_never_recreates_a_deleted_walk(self):
        from src.common.DAO.AIWalkDAO import AIWalkDAO
        dao, job = AIWalkDAO(), "test-walk-dao-%d" % os.getpid()
        collection = dao.dbManager.getCollection(dao.collectionName)
        try:
            self.assertFalse(dao.update_existing(job, "network", {"status": "running"}))
            self.assertIsNone(dao.find(job, "network"), "an update created a walk")
            dao.save_progress(job, "network", {"status": "queued", "percent": 0})
            self.assertTrue(dao.update_existing(job, "network", {"status": "running", "percent": 40}))
            doc = dao.find(job, "network")
            self.assertEqual((doc["status"], doc["percent"]), ("running", 40))
            self.assertIn("createdAt", doc)
            collection.delete_many({"jobID": job})             # the job is deleted
            self.assertFalse(dao.update_existing(job, "network", {"status": "done"}))
            self.assertIsNone(dao.find(job, "network"))
        finally:
            collection.delete_many({"jobID": job})
            dao.closeConnection()


class FakeQueueJob(object):
    def __init__(self, status):
        self.status = status


class FakeQueue(object):
    def __init__(self):
        self.jobs, self.filed, self.taken = {}, [], []

    def fetch_job(self, job_id):
        return self.jobs.get(job_id)

    def enqueue(self, fn, args, job_id="", timeout=600):
        from src.common.PySiQ import JobStatus
        self.filed.append((fn, args, job_id, timeout))
        self.jobs[job_id] = FakeQueueJob(JobStatus.QUEUED)

    def get_result(self, job_id, remove=True):
        self.taken.append(job_id)
        self.jobs.pop(job_id, None)

    def count_active(self, prefix=""):
        from src.common.PySiQ import JobStatus
        return sum(1 for job_id, job in self.jobs.items()
                   if job_id.startswith(prefix) and job.status in (JobStatus.QUEUED, JobStatus.STARTED))


class _Request(object):
    def __init__(self, form=None, cookies=None):
        self.form, self.cookies = form or {}, cookies or {}


class RouteTest(unittest.TestCase):
    def setUp(self):
        import src.servlets.AIInterpretServlet as servlet
        self.servlet = servlet
        FakeWalkDAO.store = {}
        self.consent, self.read_only, self.pathways = True, False, None
        test = self

        class _Job(object):
            def getAIConsent(self):
                return test.consent

            def getReadOnly(self):
                return test.read_only

            def getUserID(self):
                return "owner"

            def getMatchedPathways(self):
                return test.pathways

        class _Sessions(object):
            def isValidUser(self, *args):
                return True

        self.saved = {name: getattr(servlet, name) for name in (
            "AIWalkDAO", "_requireJobAccess", "_requireLLMCredentials", "UserSessionManager",
            "AI_INTERPRETATION_ENABLED")}
        self.saved_card = servlet.card_mod.job_card
        servlet.AIWalkDAO = FakeWalkDAO
        servlet._requireJobAccess = lambda jobID, userID: _Job()
        servlet._requireLLMCredentials = lambda: None
        servlet.UserSessionManager = _Sessions
        servlet.AI_INTERPRETATION_ENABLED = True
        servlet.card_mod.job_card = lambda job: {"axis_kind": "time", "perturbation": "p"}
        self.queue = FakeQueue()

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.servlet, name, value)
        self.servlet.card_mod.job_card = self.saved_card

    def _response(self):
        from src.paintomicsserver import Response
        return Response()

    def start(self, user=None, **form):
        cookies = {"userID": user} if user else {}
        return self.servlet.aiWalkStart(_Request(dict(form), cookies), self._response(), self.queue).content

    def status(self, **form):
        return self.servlet.aiWalkStatus(_Request(dict(form)), self._response(), self.queue).content

    def test_a_bad_scope_is_refused_before_anything_is_filed(self):
        out = self.start(jobID="J1", scope="pathway:../../etc")
        self.assertFalse(out["success"])
        self.assertIn("scope", out["message"])
        self.assertEqual(self.queue.filed, [])

    def test_no_consent_no_walk(self):
        self.consent = False
        out = self.start(jobID="J1", scope="network")
        self.assertFalse(out["success"])
        self.assertIn("AI interpretation was not enabled", out["message"])
        self.assertEqual(self.queue.filed, [])

    def test_a_second_click_never_files_a_second_walk(self):
        first = self.start(jobID="J1", scope="pathway:mmu04068", card=json.dumps({"axis": "0h · 24h"}))
        self.assertEqual(first["status"], "queued")
        fn, args, job_id, _timeout = self.queue.filed[0]
        self.assertIs(fn, self.servlet.walk_service.run_job)
        self.assertEqual(args, ("J1", "pathway:mmu04068", {"axis": "0h · 24h"}, "model"))
        self.assertEqual(job_id, self.servlet.walk_service.queue_id("J1", "pathway:mmu04068"))
        self.assertEqual(self.start(jobID="J1", scope="pathway:mmu04068")["status"], "already_running")
        self.assertEqual(len(self.queue.filed), 1)

    def test_a_finished_walk_is_kept_unless_restarted(self):
        FakeWalkDAO().save_progress("J1", "network", {"status": "done", "viewJSON": "{}"})
        self.assertEqual(self.start(jobID="J1", scope="network")["status"], "already_finished")
        self.assertEqual(self.queue.filed, [])
        self.assertEqual(self.start(jobID="J1", scope="network", restart="true")["status"], "queued")
        self.assertEqual(len(self.queue.filed), 1)

    def test_a_read_only_viewer_cannot_replace_or_steer_a_walk(self):
        self.read_only = True
        FakeWalkDAO().save_progress("J1", "network", {"status": "done", "viewJSON": "{\"chain\": [1]}"})
        out = self.start(user="viewer", jobID="J1", scope="network", restart="true")
        self.assertFalse(out["success"])
        self.assertIn("read-only", out["message"])
        out = self.start(user="viewer", jobID="J1", scope="pathway:mmu04068", card=json.dumps({"axis": "x"}))
        self.assertFalse(out["success"])
        self.assertEqual(self.queue.filed, [])
        self.assertEqual(FakeWalkDAO.store[("J1", "network")]["viewJSON"], "{\"chain\": [1]}",
                         "a viewer's request wiped the owner's walk")
        self.assertEqual(self.start(user="viewer", jobID="J1", scope="pathway:mmu04068")["status"], "queued",
                         "a first walk on the owner's own design is not a rewrite")
        self.assertEqual(self.start(user="owner", jobID="J1", scope="network", restart="true")["status"], "queued")

    def test_a_pathway_the_job_never_matched_is_refused_before_the_queue(self):
        self.pathways = {"mmu04068": object()}
        out = self.start(jobID="J1", scope="pathway:mmu99999")
        self.assertFalse(out["success"])
        self.assertIn("mmu99999", out["message"])
        self.assertEqual(self.queue.filed, [])
        self.assertEqual(self.start(jobID="J1", scope="pathway:mmu04068")["status"], "queued")
        self.assertEqual(self.start(jobID="J1", scope="network")["status"], "queued")

    def test_walks_are_capped_per_job_and_across_the_server(self):
        from src.common.PySiQ import JobStatus
        self.assertEqual(self.start(jobID="J1", scope="network")["status"], "queued")
        self.assertEqual(self.start(jobID="J1", scope="pathway:mmu04068")["status"], "queued")
        out = self.start(jobID="J1", scope="pathway:mmu04010")
        self.assertFalse(out["success"])
        self.assertIn("already has 2 walks", out["message"])
        self.assertEqual(len(self.queue.filed), 2)
        self.queue.jobs.clear()
        saved = self.servlet.AI_WALK_MAX_ACTIVE
        self.servlet.AI_WALK_MAX_ACTIVE = 2
        try:
            for other in ("J2", "J3"):
                self.queue.jobs[self.servlet.walk_service.queue_id(other, "network")] = FakeQueueJob(JobStatus.STARTED)
            self.queue.jobs["J4"] = FakeQueueJob(JobStatus.STARTED)      # an analysis, not a walk
            out = self.start(jobID="J1", scope="pathway:mmu04010")
            self.assertFalse(out["success"])
            self.assertIn("free worker", out["message"])
            self.assertNotIn(("J1", "pathway:mmu04010"), FakeWalkDAO.store, "a refused walk left a queued document")
        finally:
            self.servlet.AI_WALK_MAX_ACTIVE = saved
        self.assertGreaterEqual(self.servlet.AI_WALK_MAX_ACTIVE, 1)

    def test_the_queue_counts_only_live_jobs_under_a_prefix(self):
        from src.common.PySiQ import Queue, JobStatus
        queue = Queue()
        queue.enqueue(fn=len, args=([],), job_id="walk_A_network")
        queue.enqueue(fn=len, args=([],), job_id="walk_A_pathway_mmu04068")
        queue.enqueue(fn=len, args=([],), job_id="A")
        self.assertEqual(queue.count_active("walk_A_"), 2)
        self.assertEqual(queue.count_active("walk_B_"), 0)
        self.assertEqual(queue.count_active("walk_"), 2)
        queue.fetch_job("walk_A_network").status = JobStatus.FINISHED
        self.assertEqual(queue.count_active("walk_"), 1)

    def test_status_before_a_walk_offers_the_card(self):
        out = self.status(jobID="J1", scope="pathway:mmu04068")
        self.assertTrue(out["success"])
        self.assertEqual(out["status"], "not_started")
        self.assertEqual(out["card"]["axis_kind"], "time")

    def test_status_carries_the_live_chain_then_the_view(self):
        dao = FakeWalkDAO()
        dao.save_progress("J1", "network", {"status": "running", "stage": "walk", "percent": 30,
                                            "liveJSON": json.dumps({"chain": [{"n": 1}]})})
        out = self.status(jobID="J1", scope="network")
        self.assertEqual(out["live"]["chain"], [{"n": 1}])
        self.assertNotIn("walk", out)
        dao.save_progress("J1", "network", {"status": "done", "liveJSON": None,
                                            "viewJSON": json.dumps({"chain": [{"n": 1}, {"n": 2}]})})
        from src.common.PySiQ import JobStatus
        queue_id = self.servlet.walk_service.queue_id("J1", "network")
        self.queue.jobs[queue_id] = FakeQueueJob(JobStatus.FINISHED)
        out = self.status(jobID="J1", scope="network")
        self.assertEqual(len(out["walk"]["chain"]), 2)
        self.assertNotIn("live", out)
        self.assertEqual(self.queue.taken, [queue_id], "a finished walk's queue entry was left behind")

    def test_a_walk_that_stopped_writing_progress_is_marked_interrupted(self):
        FakeWalkDAO().save_progress("J1", "network", {"status": "running", "stage": "writer"})
        FakeWalkDAO.store[("J1", "network")]["updatedAt"] = datetime.utcnow() - timedelta(minutes=11)
        out = self.status(jobID="J1", scope="network")
        self.assertEqual(out["status"], "error")
        self.assertIn("interrupted", out["detail"])


if __name__ == "__main__":
    unittest.main()

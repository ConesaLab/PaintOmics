"""A name off a request names one file in the user's folder, or the request is refused.

Uploads were saved as "<folder>/<name>" with the name as the request gave it, so an
upload named "../../x" was written two folders up, as the app's user. Browsers send
the bare name of the file picked, so only a hand-made request does this, and one
extra request for a guest session is all it takes. The same held, in other shapes,
for every name a request hands the file system:

- the five upload routes: /pa_step1, the converters /dm_fromBEDtoGenes,
  /dm_fromMiRNAtoGenes and /dm_fromMOREtoGenes, and My Data's /dm_upload_file;
- a file a form names instead of uploading ("[MyData]/<name>", an inbuilt GTF),
  which the job later joins to the folder and reads, MORE also transcoding it in place;
- an omic's name, which Step 1 writes files after ("<temporal dir>/<omic>_matched.txt");
- the job id of /dm_downloadFile, which picks the folder the file name is then
  confined to;
- the organism of /kegg_data/pathway_network*/<organism>, which picks a folder;
- the user id an administrator deletes, whose folder is removed recursively.

Util.fileInFolder is the one gate: a separator, "." or "..", a NUL, or a path that
resolves (symlinks followed) outside the folder is refused as InputFileError, which the
page words as "Please check your input files". The routes are driven through Flask's
test client; each refusal is checked to leave no file outside the folder, and each
legitimate name (spaces, dots, letters of any script, a long name) to still be saved.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_upload_names_stay_in_the_users_folder
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../..")))

from werkzeug.formparser import parse_form_data
from werkzeug.test import EnvironBuilder

from src import paintomicsserver as server
from src.common.ServerErrorManager import InputFileError
# Through the module, so that the route tests below also run, and fail, on a tree
# from before the gate existed.
from src.common import Util
from src.conf.serverconf import SERVER_SUBDOMAIN
from src.servlets import AdminServlet
from src.servlets import Bed2GenesServlet
from src.servlets import DataManagementServlet
from src.servlets import MiRNA2GenesServlet
from src.servlets import MOREServlet
from src.servlets import PathwayAcquisitionServlet
from src.tests.test_echoed_request_values_are_text import _Routes

USER = "1"
CONTENT = b"#id\tc1\nA\t1\n"
STEP1 = {"specie": "mmu", "omic0_omic_name": "Gene expression", "omic0_origin": "client",
         "omic0_file_type": "Gene expression file", "omic0_match_type": "gene",
         "omic0_relevant_origin": "none"}
CONVERTER = dict(STEP1, name_prefix="omic0")
MORE = {"more_method": "PLS1", "omic_name_0": "TF"}
# route, the form that goes with the file, the part that carries it
UPLOADS = (("/pa_step1", STEP1, "omic0_file"),
           ("/dm_fromBEDtoGenes", CONVERTER, "omic0_file"),
           ("/dm_fromMiRNAtoGenes", CONVERTER, "omic0_file"),
           ("/dm_fromMOREtoGenes", MORE, "rnaseqaux_file"),
           ("/dm_upload_file", {}, "file"))
# Names a person's file can have: all are kept as they are.
FINE = ("my data (v2) final.tab", "a.b.c.tab", "...leading dots.tab",
        "Expresión génica ñ.tab", "基因表达 数据.tab", "x" * 200 + ".tab",
        # Percent signs are characters; nothing decodes them after this point.
        "%2e%2e%2fliteral.tab")


class TheGateTest(unittest.TestCase):
    """Util.unsafeNameReason, fileInFolder and requireOmicNameForFiles."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="paintomics_names_")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.folder = os.path.join(self.base, "user", "inputData")
        os.makedirs(self.folder)
        os.makedirs(os.path.join(self.base, "outside"))

    def test_names_people_give_their_files_pass(self):
        for name in FINE:
            with self.subTest(name=name):
                self.assertIsNone(Util.unsafeNameReason(name))
                self.assertEqual(Util.fileInFolder(self.folder, name),
                                 os.path.join(os.path.realpath(self.folder), name))

    def test_anything_but_one_name_is_refused(self):
        for name in ("../escape.tab", "../../escape.tab", "sub/x.tab", "/etc/passwd",
                     self.base + "/outside/x.tab", "..", ".", "a\\b.tab", "..\\..\\x.tab",
                     "x.tab\x00.png", "", "   ", None):
            with self.subTest(name=name):
                self.assertIsNotNone(Util.unsafeNameReason(name))
                with self.assertRaises(InputFileError) as caught:
                    Util.fileInFolder(self.folder, name, "uploaded file")
                self.assertIn("The uploaded file name '", str(caught.exception))
                self.assertIn("Rename the file and try again.", str(caught.exception))

    def test_a_link_that_leads_out_is_refused_even_before_its_target_exists(self):
        """open() would have created the target: the link is followed, not the name."""
        os.symlink(os.path.join(self.base, "outside", "new.tab"), os.path.join(self.folder, "link.tab"))
        with self.assertRaises(InputFileError) as caught:
            Util.fileInFolder(self.folder, "link.tab")
        self.assertIn("points outside its folder", str(caught.exception))

    def test_a_link_that_stays_inside_is_fine(self):
        open(os.path.join(self.folder, "real.tab"), "w").close()
        os.symlink(os.path.join(self.folder, "real.tab"), os.path.join(self.folder, "alias.tab"))
        self.assertEqual(Util.fileInFolder(self.folder, "alias.tab"),
                         os.path.join(os.path.realpath(self.folder), "real.tab"))

    def test_an_omic_name_is_refused_only_for_what_makes_it_a_path(self):
        for name in ("Gene expression", "miRNA-seq", "Proteomics (TMT) .. v2", "..", ".", "",
                     "<img src=x onerror=1>", "A&B = C"):
            with self.subTest(name=name):
                self.assertEqual(Util.requireOmicNameForFiles(name), name)
        for name in ("../../escaped", "Phospho/Proteomics", "a\\b", "a\x00b"):
            with self.subTest(name=name):
                with self.assertRaises(InputFileError) as caught:
                    Util.requireOmicNameForFiles(name)
                self.assertIn("The omic name '", str(caught.exception))


class _RouteCase(unittest.TestCase):
    """The real application's routes, with the session check passed, the job folders under
    a temporary root, nothing queued and nothing written to Mongo."""

    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="paintomics_routes_"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = os.path.join(self.base, "root") + "/"
        self.inputs = os.path.join(self.root, USER, "inputData")
        self.outside = os.path.join(self.base, "outside")
        for folder in (self.inputs, os.path.join(self.root, USER, "jobsData"), self.outside):
            os.makedirs(folder)
        for module in (PathwayAcquisitionServlet, Bed2GenesServlet, MiRNA2GenesServlet,
                       MOREServlet, DataManagementServlet):
            self._patch(module, "UserSessionManager", mock.MagicMock())
        for module in (server, PathwayAcquisitionServlet, Bed2GenesServlet, MiRNA2GenesServlet,
                       MOREServlet, DataManagementServlet):
            self._patch(module, "CLIENT_TMP_DIR", self.root)
        self._patch(PathwayAcquisitionServlet, "DatabaseAvailability", mock.MagicMock(
            resolveDatabases=mock.MagicMock(return_value=["KEGG"])))
        self._patch(MOREServlet, "moreBinary", mock.MagicMock(return_value="/x/more-rs"))
        self._patch(DataManagementServlet, "FileDAO", mock.MagicMock())
        self.queued = mock.MagicMock()
        self._patch(_Routes.get().queue, "enqueue", self.queued)
        self.client = _Routes.get().app.test_client()
        self.client.set_cookie("userID", USER)

    def _patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def answer(self, response):
        return response.status_code, json.loads(response.get_data(as_text=True))

    def upload(self, route, fields, part, fileName):
        data = dict(fields)
        data[part] = (io.BytesIO(CONTENT), fileName)
        return self.answer(self.client.post(SERVER_SUBDOMAIN + route, data=data,
                                            content_type="multipart/form-data"))

    def outsideFiles(self):
        """Every file the requests left anywhere but the user's inputData folder,
        other than the ones a test planted on purpose."""
        found = []
        for folder, _, files in os.walk(self.base):
            for name in files:
                path = os.path.join(folder, name)
                if not path.startswith(self.inputs + os.sep) and not name.startswith("planted"):
                    found.append(os.path.relpath(path, self.base))
        return found

    def assertRefusedAsInput(self, status, body, quoted=None):
        self.assertIs(body.get("success"), False, body)
        self.assertEqual(status, 400, body)
        self.assertIs((body.get("extra") or {}).get("input_error"), True,
                      "refused as a fault of ours, not of the request: %s" % body.get("message"))
        self.assertIn("cannot be used", body["message"])
        if quoted is not None:
            self.assertIn(quoted, body["message"])


class UploadedFileNamesTest(_RouteCase):

    def test_a_parent_walk_is_refused_on_every_route(self):
        # A "\" cannot be sent this way: the parser reads it as the quoted-string
        # escape of the next character. filename*= below sends one.
        for route, fields, part in UPLOADS:
            for name in ("../escaped-up.tab", "../../escaped-up2.tab"):
                with self.subTest(route=route, name=name):
                    status, body = self.upload(route, fields, part, name)
                    self.assertRefusedAsInput(status, body, name)
                    self.assertEqual(self.outsideFiles(), [])

    def test_an_absolute_path_is_refused_on_every_route(self):
        target = os.path.join(self.outside, "escaped-absolute.tab")
        for route, fields, part in UPLOADS:
            with self.subTest(route=route):
                status, body = self.upload(route, fields, part, target)
                self.assertRefusedAsInput(status, body, target)
                self.assertFalse(os.path.exists(target))
                self.assertEqual(self.outsideFiles(), [])

    def test_a_name_the_multipart_parser_decodes_is_refused_on_every_route(self):
        """filename*=UTF-8''%2e%2e%2f... arrives at the route already decoded to "../"."""
        for encoded, decoded in (("%2e%2e%2f%2e%2e%2fescaped-decoded.tab", "../../escaped-decoded.tab"),
                                 ("%2e%2e%5c%2e%2e%5cescaped-decoded-win.tab", "..\\..\\escaped-decoded-win.tab")):
            for route, fields, part in UPLOADS:
                with self.subTest(route=route, name=decoded):
                    body, contentType = _multipartWithEncodedName(fields, part, encoded)
                    self.assertEqual(_parsedName(body, contentType, part), decoded,
                                     "the parser no longer decodes filename*: this case tests nothing")
                    status, answer = self.answer(self.client.post(SERVER_SUBDOMAIN + route, data=body,
                                                                  content_type=contentType))
                    self.assertRefusedAsInput(status, answer, decoded)
                    self.assertEqual(self.outsideFiles(), [])

    def test_a_link_out_of_the_folder_is_refused_on_every_route(self):
        """A dangling link: isfile() is False, so the save opened it and created its target."""
        target = os.path.join(self.outside, "escaped-through-link.tab")
        os.symlink(target, os.path.join(self.inputs, "link.tab"))
        for route, fields, part in UPLOADS:
            with self.subTest(route=route):
                status, body = self.upload(route, fields, part, "link.tab")
                self.assertRefusedAsInput(status, body, "link.tab")
                self.assertFalse(os.path.exists(target), "the upload was written through the link")

    def test_names_people_give_their_files_are_saved_as_they_are(self):
        for route, fields, part in UPLOADS:
            for name in FINE:
                with self.subTest(route=route, name=name):
                    status, body = self.upload(route, fields, part, name)
                    self.assertFalse((body.get("extra") or {}).get("input_error"), body)
                    self.assertTrue(os.path.isfile(os.path.join(self.inputs, name)),
                                    "%s was not saved in the user's folder: %s" % (name, body))
                    os.remove(os.path.join(self.inputs, name))
        self.assertEqual(self.outsideFiles(), [])

    def test_a_second_file_of_the_same_name_gets_a_suffix_in_the_folder(self):
        for _ in range(2):
            status, body = self.upload("/dm_upload_file", {}, "file", "same name.tab")
            self.assertIs(body["success"], True, body)
        self.assertEqual(sorted(os.listdir(self.inputs)), ["same name.tab", "same name1.tab"])


class FilesAFormPointsToTest(_RouteCase):
    """"[MyData]/<name>" and "[inbuilt GTF files]/<name>": names of files already there."""

    def setUp(self):
        super().setUp()
        self.secret = os.path.join(self.outside, "planted-secret.tab")
        with open(self.secret, "wb") as handle:
            handle.write(CONTENT)
        with open(os.path.join(self.inputs, "mine.tab"), "wb") as handle:
            handle.write(CONTENT)

    def step1(self, location):
        data = dict(STEP1, omic0_origin="mydata", omic0_filelocation=location,
                    omic0_file=(io.BytesIO(b""), ""))
        return self.answer(self.client.post(SERVER_SUBDOMAIN + "/pa_step1", data=data,
                                            content_type="multipart/form-data"))

    def test_my_data_names_that_leave_the_folder_are_refused(self):
        os.symlink(self.secret, os.path.join(self.inputs, "link-out.tab"))
        for location in ("[MyData]/../../../outside/planted-secret.tab", "[MyData]/" + self.secret,
                         self.secret, "[MyData]/link-out.tab", "[MyData]/sub/x.tab"):
            with self.subTest(location=location):
                status, body = self.step1(location)
                self.assertRefusedAsInput(status, body)
                self.queued.assert_not_called()

    def test_a_my_data_file_is_still_used(self):
        status, body = self.step1("[MyData]/mine.tab")
        self.assertIs(body["success"], True, body)
        job = self.queued.call_args.kwargs["args"][0]
        self.assertEqual(job.getGeneBasedInputOmics()[0]["inputDataFile"], "mine.tab")

    def test_more_refuses_a_file_it_would_read_and_transcode_elsewhere(self):
        for field in ("rnaseqaux_filelocation", "conditions_filelocation", "file_0_filelocation",
                      "assoc_file_0_filelocation", "relevant_file_0_filelocation"):
            for location in (self.secret, "[MyData]/../../../outside/planted-secret.tab"):
                with self.subTest(field=field, location=location):
                    data = dict(MORE, rnaseqaux_filelocation="[MyData]/mine.tab",
                                conditions_filelocation="[MyData]/mine.tab",
                                file_0_filelocation="[MyData]/mine.tab")
                    data[field] = location
                    status, body = self.answer(self.client.post(SERVER_SUBDOMAIN + "/dm_fromMOREtoGenes", data=data))
                    self.assertRefusedAsInput(status, body)
                    self.queued.assert_not_called()

    def test_more_still_takes_its_files_from_my_data(self):
        data = dict(MORE, rnaseqaux_filelocation="[MyData]/mine.tab",
                    conditions_filelocation="[MyData]/mine.tab", file_0_filelocation="[MyData]/mine.tab")
        status, body = self.answer(self.client.post(SERVER_SUBDOMAIN + "/dm_fromMOREtoGenes", data=data))
        self.assertIs(body["success"], True, body)
        job = self.queued.call_args.kwargs["args"][0]
        self.assertEqual(job.targetExpressionFile, "mine.tab")

    def test_an_inbuilt_gtf_outside_the_gtf_folder_is_refused(self):
        data = dict(CONVERTER, omic0_file=(io.BytesIO(CONTENT), "regions.bed"),
                    omic0_annotations_file=(io.BytesIO(b""), ""),
                    omic0_annotations_origin="inbuilt_gtf", omic0_annotations_file_type="GTF file",
                    omic0_annotations_filelocation="[inbuilt GTF files]/../../paintomicsserver.py")
        status, body = self.answer(self.client.post(SERVER_SUBDOMAIN + "/dm_fromBEDtoGenes", data=data,
                                                    content_type="multipart/form-data"))
        self.assertRefusedAsInput(status, body, "../../paintomicsserver.py")
        self.queued.assert_not_called()


class OmicNamesTest(_RouteCase):

    def test_an_omic_named_as_a_path_is_refused(self):
        """Step 1 writes "<temporal dir>/<omic>_matched.txt"; MORE "MORE_output_<omic>_<date>.tab"."""
        for route, fields, part, field in (("/pa_step1", STEP1, "omic0_file", "omic0_omic_name"),
                                           ("/dm_fromBEDtoGenes", CONVERTER, "omic0_file", "omic0_omic_name"),
                                           ("/dm_fromMiRNAtoGenes", CONVERTER, "omic0_file", "omic0_omic_name"),
                                           ("/dm_fromMOREtoGenes", MORE, "rnaseqaux_file", "omic_name_0")):
            with self.subTest(route=route):
                status, body = self.upload(route, dict(fields, **{field: "../../escaped-omic"}), part, "data.tab")
                self.assertRefusedAsInput(status, body, "The omic name '../../escaped-omic'")
                self.queued.assert_not_called()

    def test_an_omic_name_with_other_punctuation_is_kept(self):
        name = "<img src=x onerror=1> & co. (v2) .."
        status, body = self.upload("/pa_step1", dict(STEP1, omic0_omic_name=name), "omic0_file", "data.tab")
        self.assertIs(body["success"], True, body)
        job = self.queued.call_args.kwargs["args"][0]
        self.assertEqual(job.getGeneBasedInputOmics()[0]["omicName"], name)


class FolderPickedByTheRequestTest(_RouteCase):

    def test_a_download_job_id_cannot_move_the_folder_to_another_user(self):
        victim = os.path.join(self.root, "2", "jobsData", "victimJob", "output")
        os.makedirs(victim)
        with open(os.path.join(victim, "planted-result.tab"), "w") as handle:
            handle.write("another user's result")
        for serve in ("true", "false"):
            with self.subTest(serve=serve):
                answer = self.client.get(SERVER_SUBDOMAIN + "/dm_downloadFile", query_string={
                    "fileType": "job_result", "jobID": "../../2/jobsData/victimJob",
                    "fileName": "planted-result.tab", "serve": serve})
                self.assertNotIn("another user's result", answer.get_data(as_text=True))
                self.assertIn("File not found", answer.get_data(as_text=True))

    def test_the_users_own_job_result_still_downloads(self):
        own = os.path.join(self.root, USER, "jobsData", "myJob", "output")
        os.makedirs(own)
        with open(os.path.join(own, "result.tab"), "w") as handle:
            handle.write("my result\n")
        answer = self.client.get(SERVER_SUBDOMAIN + "/dm_downloadFile", query_string={
            "fileType": "job_result", "jobID": "myJob", "fileName": "result.tab", "serve": "true"})
        self.assertIn("my result", answer.get_data(as_text=True))

    def test_the_organism_of_a_network_route_cannot_name_another_folder(self):
        kegg = os.path.join(self.base, "kegg") + "/"
        os.makedirs(kegg + "current/mmu")
        with open(kegg + "current/mmu/pathways_network.json", "w") as handle:
            handle.write('{"mmu": true}')
        os.makedirs(os.path.join(self.base, "elsewhere"))
        with open(os.path.join(self.base, "elsewhere", "pathways_network.json"), "w") as handle:
            handle.write('{"planted": true}')
        self._patch(server, "KEGG_DATA_DIR", kegg)
        answer = self.client.get(SERVER_SUBDOMAIN + "/kegg_data/pathway_network/" + quote("../../elsewhere", safe=""))
        self.assertNotIn("planted", answer.get_data(as_text=True))
        self.assertEqual(answer.status_code, 404)
        self.assertIn('"mmu": true', self.client.get(SERVER_SUBDOMAIN + "/kegg_data/pathway_network/mmu").get_data(as_text=True))

    def test_deleting_a_user_removes_that_users_folder_only(self):
        victim = os.path.join(self.base, "victim")
        os.makedirs(victim)
        os.makedirs(os.path.join(self.root, "7"))
        request = mock.MagicMock(cookies={"userID": "0", "sessionToken": "t", "userName": "admin"})
        with mock.patch.object(AdminServlet, "UserSessionManager", mock.MagicMock()), \
                mock.patch.object(AdminServlet, "CLIENT_TMP_DIR", self.root), \
                mock.patch.object(AdminServlet, "JobDAO", mock.MagicMock()), \
                mock.patch.object(AdminServlet, "FileDAO", mock.MagicMock()), \
                mock.patch.object(AdminServlet, "UserDAO", mock.MagicMock()):
            AdminServlet.adminServletDeleteUser(request, mock.MagicMock(), "../victim")
            self.assertTrue(os.path.isdir(victim), "the admin route removed a folder beside CLIENT_TMP")
            AdminServlet.adminServletDeleteUser(request, mock.MagicMock(), "7")
        self.assertFalse(os.path.exists(os.path.join(self.root, "7")))


def _multipartWithEncodedName(fields, part, encodedName):
    """A body whose file part names itself with RFC 2231's filename*=."""
    boundary = "pathcheckboundary"
    lines = []
    for key, value in fields.items():
        lines += ["--" + boundary, 'Content-Disposition: form-data; name="%s"' % key, "", value]
    lines += ["--" + boundary,
              "Content-Disposition: form-data; name=\"%s\"; filename*=UTF-8''%s" % (part, encodedName),
              "Content-Type: text/plain", "", CONTENT.decode().rstrip("\n"),
              "--" + boundary + "--", ""]
    return "\r\n".join(lines).encode("utf-8"), "multipart/form-data; boundary=" + boundary


def _parsedName(body, contentType, part):
    environ = EnvironBuilder(method="POST", data=body, content_type=contentType).get_environ()
    _, _, files = parse_form_data(environ)
    name = files[part].filename
    for storage in files.values():
        storage.close()
    return name


if __name__ == "__main__":
    unittest.main()

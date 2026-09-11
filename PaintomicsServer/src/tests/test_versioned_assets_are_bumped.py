#!/usr/bin/env python3
"""A cache-busted asset must get a new ?v= whenever its contents change.

Why this exists
---------------
Five assets in index.html are cache-busted by hand:

    <script src="app/view/common/Util.js?v=0.8"></script>

and the server deliberately gives them a long max-age -- `revalidateEntryDocument`
says so in as many words: "Versioned assets keep their long max-age -- that is
what versioning them is for." The whole scheme rests on one manual step: bump the
number when the file changes. Nothing enforced it, and it was missed.

`1fcc5b7b` added `getClusterColor` to Util.js and left the marker at v=0.7. Every
browser that had ever loaded the site kept its cached v=0.7, which has no such
function, while getting the new PA_Step4Views.js that calls it. Step 4's pathway
details panel then threw

    ReferenceError: getClusterColor is not defined
        at PA_Step4FindFeaturesView.getClusterColor (PA_Step4Views.js:3048)
        at PA_Step3PathwayDetailsView.generatePlot (PA_Step3Views.js:3328)

so opening any pathway lost its plot. Measured in the browser, same URL, one
request with the HTTP cache and one with `cache: no-store`:

    cached: 292088 bytes, contains getClusterColor: False
    fresh:  294066 bytes, contains getClusterColor: True

A hard reload hides it, which is exactly why it survives development and only
reaches people who have used the site before -- that is, everyone except the
developer testing it in a fresh profile.

This is the second time. `revalidateEntryDocument` was written for the identical
failure with `truncatableTextRenderer`, also added to Util.js, also invisible
after a hard reload. That fix made index.html itself revalidate, which was
necessary but not sufficient: the freshly-fetched index.html still asked for
?v=0.7, so the stale Util.js stayed pinned. The remaining hole is the manual bump,
and this test closes it.

How it works: the digest of each versioned file is recorded below against the
version it was published under. Change the file and the digest stops matching,
so the test fails and tells you to bump the marker and update the entry. That is
the point -- the failure is the reminder. Updating this table without bumping
index.html defeats it, so the message says so.

Everything here is read from HEAD via `git show`, not from the working tree,
and that is a correction to how this started. Comparing the working tree meant
the test went red the moment anyone opened one of these files and stayed red
until they were finished -- which, on the client, is most of a working day. It
fired that way twice in one afternoon on edits that were still in progress. A
guard that is red while you type is one that gets ignored or deleted, and it
was never the right question anyway: the failure being prevented is a *shipped*
file whose marker did not move, and what ships is what is committed. Uncommitted
work is nobody's problem yet.

When git is unavailable or a file is not tracked, the check skips rather than
inventing a verdict.

Usage:
    cd PaintomicsServer
    python -m src.tests.test_versioned_assets_are_bumped
"""
import hashlib
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

CLIENT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "../../../PaintomicsClient/public_html"))
INDEX_HTML = os.path.join(CLIENT_ROOT, "index.html")

# path in index.html -> (version it is published under, sha256 of that content)
#
# A digest is carried for the application scripts, which change and are what
# break each other. The vendor libraries carry None: they change perhaps once a
# year, and recording a digest for a 300KB minified bundle only creates churn.
PUBLISHED = {
    # The upload format checker and the AI conversion agent. Recorded like any
    # other shipped script: a returning browser holds the old copy until the
    # marker changes, and a checker running against a stale validator would
    # disagree with the server it is supposed to mirror.
    "app/view/PathwayAcquisitionViews/InputFormat/format-reader.js": (
        "0.1", "0e455d115c4fc2ffe478c941ab49a757e9270a170382fd07c6896f42e8e477f1"),
    "app/view/PathwayAcquisitionViews/InputFormat/format-validator.js": (
        "0.2", "10700289348a691f3efa5743dff60de793adc97095372250ec7d179e229567d5"),
    "app/view/PathwayAcquisitionViews/InputFormat/format-repair.js": (
        "0.1", "944c929f1496258aba56f026e7c30e4affb7052dcbc127b06982eaadff205cd2"),
    "app/view/PathwayAcquisitionViews/InputFormat/format-panel.js": (
        "3.7", "10b80dd305fb72695651b841a71ebf6dda28e80c1d365727673930a6790a9ccf"),
    "app/view/PathwayAcquisitionViews/InputFormat/format-roles.js": (
        "0.8", "c150e5858c4b5a33c1bde34489b7a25f3c25fe1100be8fe512e94fe2a93d8e34"),
    "app/view/PathwayAcquisitionViews/InputFormat/convert-profiler.js": (
        "0.3", "d7f2846c58226fdfe334fefc68c8d6519cc4eae393265e15dd047e5babded4f3"),
    "app/view/PathwayAcquisitionViews/InputFormat/convert-agent.js": (
        "0.7", "f7046f6383282d5a3e45b96519c9191bb639bd732ffbf225b08b09f5da867d87"),
    "app/view/PathwayAcquisitionViews/InputFormat/convert-drawer.js": (
        "1.7", "34668ccdd3c668c41af10765e518ea0f71b602821e9b0f30d9da70f4c6c3a52e"),
    # v=2.2 makes fitPlotPanel measure the figures it drew instead of the
    # height:100% wrapper it drew them into. The old reading was a function of
    # the panel height it was computing, so the panel could only grow: a
    # 40-compound metabolite class pinned it at the 820 cap and every smaller
    # class after that kept the cap as blank space.
    # v=2.5 drops revealPlotPanel and fitPlotPanel: the metabolite class
    # detail opens inside its own card now, so nothing reveals or fits a
    # separate plot panel any more.
    # v=2.6 names the field when ExtJS's own submit-time validation refuses
    # (the client-abort branch of extJSErrorHandler) and carries the refusal
    # composition, plainFieldText and fieldErrorText, shared with checkForm().
    # v=2.7 stops offering Report error on the branch that names the field:
    # a refusal that says which box to fill in is the software working, not a
    # fault to mail to the developers.
    # v=2.8 keeps the button on the one named-field refusal that is not the
    # user's doing -- extJSErrorHandler's CLIENT_INVALID, which is only reached
    # after checkForm()/isValid() already approved the form, so it means ExtJS
    # refused something the app had just passed. That is #109's shape.
    # v=2.9 lets sendReportMessage carry extra form fields (the organism
    # request sends specie/specieCode beside its HTML) and shows the
    # servlet's "already installed" refusal as a warning, not the error
    # dialog with its report button. That is #130's shape.
    "app/view/common/Util.js": (
        "2.9", "69c3bdaa628db402fd8e707c9a1d679b331df548301bf26316c8bebdfcddafaf"),
    # v=1.1 lists the model organisms first and renders at most 200 rows
    # (PR #156): a cached v=1.0 would still work, but would show a wall of
    # 12,000 rows in species.json order.
    "app/view/common/OrganismSearch.js": (
        "1.1", "6469f5a3c48973a5470c2f97467bf722bca20d4670ce58ef514594d37dedd86c"),
    # v=0.8 turns off ExtJS's own shadow element under every window
    # (`shadow: false` on Ext.window.Window): main.css draws the window's
    # shadow, and the framework's 5px-radius one only showed as a grey box
    # under each 8px corner. That is #132's shape.
    "app/view/common/ExtJS_extensions.js": (
        "0.8", "c463dffb7cf4a73991e61fdc6d5f2a367ae1c9a5710a017cf1d89b90ef355ca3"),
    "app/view/common/CookieConsent.js": (
        "1.0", "e170c539dea089b0d88e090becd968d43a699aa319dbd02e681b75384a57f403"),
    "app/view/common/upload/Panel.js": (
        "0.3", "d674eb13f512d72d71c662ec03305eb2c1a0b721cade230c6808c110b173e583"),
    # A development overlay, inert until ctrl+alt+G, and still recorded here:
    # it ships to every visitor as a script tag like any other, so a change to
    # it with an unbumped marker leaves returning browsers running the old
    # copy exactly as it would for view code.
    "app/view/common/AlignmentGuides.js": (
        "4.2", "ce2ba85063e8059eb2cc4c8e1992744854a14ad0db6f866213b8df1136650a59"),
    # v=1.3: the report fetch retries instead of dead-ending, and a job that no
    # longer exists is named as such rather than reported as "still in
    # progress".
    "app/view/PathwayAcquisitionViews/PA_AIInterpretView.js": (
        "1.3", "595b18822379aa51b7962357402663b19a7938ca6200bd78b8d77c3a3ea220a1"),
    "app/view/PathwayAcquisitionViews/PA_Step3RegTargetNetworkView.js": (
        "0.7", "b135712a9564f8ae0eac94daf9c567ef275c4c748dbc270fdbdeb7d25fc79e34"),
    # OmniPath ships no diagram, so its pathways render as an interactive graph
    # rather than as boxes painted over a raster; this is that view.
    "app/view/PathwayAcquisitionViews/PA_Step4OmniPathNetworkView.js": (
        "0.1", "fbcdaeb4087e5edb65c09fa347ac22f0d001331c502fedfbd973ba9893d50e15"),
    # The whole hub UI: a metabolite list, the hop-ring network, and a
    # node-click expression panel. v=0.6 replaced the nine-column grid, fixed
    # stateOf (entry.relevant is an ARRAY, so [] was truthy and "measured, not
    # DE" was unreachable), and announces both sampled and empty rings.
    # v=1.8 greys the steps whose ball stopped growing and says why.
    # v=1.7 drops the cached job when the server's HUB_SCHEMA_VERSION moves
    # past the cached rows', and groups the card's heatmaps by omic.
    # v=1.5 titles everything by NAME -- Phytoceramide, Krt5 -- from
    # /pa_hub_names plus globalExpressionData's keggName.
    # v=1.3 moves the node detail INSIDE the network stage. A browser holding
    # the v=0.6 copy would run the old view against network-views.css v=1.5,
    # whose .pa-hub-detail is `flex: 0 0 0` and only opens on .is-open -- a
    # class the old view never adds. The card would be permanently 0px: the
    # same invisible-heatmap bug this release exists to fix, reintroduced by
    # the cache for everyone who had already loaded the site.
    # v=1.9 fixes the solid black colour legend (it read colorScale off the
    # network options object, which has none) and retimes the omic figure's
    # height. It ships with main.css v=1.61 and network-views.css v=1.8, which
    # is where .paColorLegend and .pa-hub-omic-figure now live -- a browser
    # keeping the v=1.8 script would build the old single-row markup those
    # rules no longer describe.
    # v=2.5 moves the connection answer into the graph: focusEgo() lights a
    # clicked node's own edges, and the card gains tabs, facet chips and a
    # drag handle. It ships with network-views.css v=2.2, which is where
    # .pa-hub-tab, .pa-hub-facet, .pa-hub-conns and the scroll fade live -- a
    # browser keeping the v=1.9 script would build markup those rules describe
    # while a browser keeping the old CSS would render the new markup unstyled.
    # v=2.6 removes the single-fill DE direction. A node carries one wedge per
    # condition now, painted by getColor against that omic's own distribution
    # summary, so it agrees with the heatmap in the card by construction. It
    # ships with network-views.css v=2.3, which is where .sw.de and the
    # tooltip's value table live -- a browser keeping the old CSS would show
    # the legend's wedge swatch as a plain hollow circle.
    "app/view/PathwayAcquisitionViews/PA_Step3HubNetworkView.js": (
        "2.6", "41a98d56055e548d69fb1adc90914059dd1c93665da83fcaab6989509f8fed22"),
    # v=0.9 adds SERVER_URL_PA_PATHWAY_EVIDENCE. The endpoint was added at
    # v=0.8 WITHOUT a bump, which this guard caught: a returning browser keeps
    # this file for up to 12 hours, so the evidence overlay would have POSTed
    # to `undefined` for everyone who had already loaded the site.
    # v=1.0 adds AI_POLL_MAX_FAILURES. A browser keeping the old copy would
    # read it as undefined, and `failures >= undefined` is false for ever --
    # which is precisely the endless poll this release exists to stop.
    # v=1.2: the step-2 compound-suggestion endpoints, and a poll ceiling that
    # actually outlasts the server's own worst case (400 x 3s) plus a separate
    # transport-failure budget. A browser holding the old copy would poll two
    # URLs it has no constants for.
    # v=1.5 adds SERVER_URL_PA_HUB_NAMES; same failure mode again.
    # v=1.4 adds SERVER_URL_PA_HUB_FEATURE. Same failure mode as v=0.9 above:
    # a browser keeping the old copy POSTs every node click to `undefined`.
    "resources/ServerConfiguration.js": (
        "1.5", "c8421cf25b6789f1e59f5182db72bbb9761d818cd9b34fd210b818ac8c2e2887"),
    # The evidence layer itself: MORE relationships drawn on the diagram and
    # classified against KEGG, Reactome and OmniPath.
    # v=2.8 draws regulators the map does not print, colours them from the
    # regulator's own values and ends every edge with a direction mark. A
    # browser keeping the v=1.6 copy would ask a server that now returns
    # off-map edges to be drawn by code that has no branch for them: it reads
    # rasterBox(regulatorID) as null and falls through to an arc between two
    # boxes, one of which does not exist, so the edge silently disappears.
    "app/view/PathwayAcquisitionViews/PA_Step4EvidenceOverlay.js": (
        "2.8", "b56c3ce3224ba610e1268b85339eaed7dbfea1eab8d70b6284895cfbade81f86"),
    "js/libs/linkurious/sigma.min.js": ("0.1", None),
    "js/libs/linkurious/plugins.js": ("0.2", None),
    # Versioned by its release rather than by a counter. A vendored library is
    # replaced wholesale at a known version, never edited in place, so the
    # upstream number is both the more informative marker and the one that
    # cannot drift from what is on disk.
    "js/libs/cytoscape/cytoscape.min.js": ("3.34.0", None),
    # 0.5 since #113. index.html has said 0.5 since that merge while this table
    # still said 0.4, so the gate that exists to catch an unbumped marker was
    # itself red on master -- caught by run_all on this branch, fixed here.
    # 0.6 -- the three browser-support warnings and the stale-analysis
    # message named the product "Paintomics"; they now say "PaintOmics AI".
    # Digest, not None. app.js is an application script edited in place, not a
    # vendored library replaced wholesale, so "record the version and trust it"
    # leaves the one thing this table exists to catch uncaught: an edit that
    # skips the bump. Recorded as None, an edit to the boot copy or to
    # `this.controllers` passed every check here.
    "app.js": (
        "0.6", "4969e8969606e5c3974f05371bc32a76a607add43a7e28f24ec54a1401639d23"),
}

_SRC = re.compile(r'src="([^"]+?)\?v=([0-9.]+)"')


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
_TRACKED_PREFIX = "PaintomicsClient/public_html/"


def _committed(relativePath):
    """The file as HEAD has it, or None when that cannot be determined.

    Deliberately not the working tree. This check compares content against a
    recorded digest, and a working-tree comparison fails continuously for as
    long as someone is editing one of these files -- which is most of a working
    day on the client. A guard that is red while you type is a guard that gets
    ignored, and the thing it is actually protecting against is a *shipped*
    change with an unbumped marker. What ships is what is committed.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["git", "show", "HEAD:" + _TRACKED_PREFIX + relativePath],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ImportError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _digest(relativePath):
    content = _committed(relativePath)
    if content is None:
        return None
    return hashlib.sha256(content).hexdigest()


def _markersInIndex():
    content = _committed("index.html")
    if content is None:
        return None
    return dict(_SRC.findall(content.decode("utf-8", "replace")))


class VersionedAssetTest(unittest.TestCase):

    def setUp(self):
        if not os.path.isfile(INDEX_HTML):
            self.skipTest("client tree not present at %s" % CLIENT_ROOT)
        self.markers = _markersInIndex()
        if self.markers is None:
            self.skipTest("index.html could not be read from HEAD (no git?)")

    def test_every_versioned_asset_is_recorded(self):
        """A new ?v= asset must be added to PUBLISHED or it is unguarded."""
        missing = sorted(set(self.markers) - set(PUBLISHED))

        self.assertEqual(missing, [],
                         "index.html cache-busts %s, but they are not recorded "
                         "in PUBLISHED, so a forgotten bump would go unnoticed"
                         % missing)

    def test_recorded_versions_match_index_html(self):
        for path, (version, _) in PUBLISHED.items():
            if path not in self.markers:
                continue
            self.assertEqual(
                self.markers[path], version,
                "index.html serves %s at v=%s but this test records v=%s. If "
                "you bumped the marker, update PUBLISHED (version and digest) "
                "to match." % (path, self.markers[path], version))

    def test_every_recorded_asset_still_carries_its_marker(self):
        """Deleting a ?v= must fail, not pass quietly.

        test_recorded_versions_match_index_html skips any path missing from
        index.html, and test_every_script_tag_asset_can_be_cache_busted only
        looks at `src="app/..."`. Between them, `src="app.js?v=0.6"` could be
        changed to `src="app.js"` with all five tests green -- and the same for
        resources/ServerConfiguration.js, whose own note above records that
        this guard once caught precisely that omission.
        """
        vanished = sorted(set(PUBLISHED) - set(self.markers))
        self.assertEqual(vanished, [],
                         "these are recorded in PUBLISHED but index.html no longer "
                         "cache-busts them, so a change to one is unbustable and "
                         "returning browsers keep the old copy: %s" % vanished)

    def test_content_matches_the_published_version(self):
        """Every application script, pinned by digest."""
        for path, (version, expected) in sorted(PUBLISHED.items()):
            if expected is None:
                continue

            actual = _digest(path)
            if actual is None:
                continue

            self.assertEqual(
                actual, expected,
                "%s has changed since it was published as v=%s.\n"
                "Bump the marker in index.html AND update the digest here to:\n"
                "    %s\n"
                "Updating the digest alone defeats the purpose -- returning "
                "browsers keep the old file for up to 12 hours and run new "
                "code against it, which is how getClusterColor and "
                "truncatableTextRenderer both broke."
                % (path, version, actual))

    def test_every_script_tag_asset_can_be_cache_busted(self):
        """A plain <script src> with no ?v= has no lever to pull.

        Files reached through `Application.loadModule` are exempt: that is
        `$.ajax({dataType: "script"})`, and jQuery defaults `cache: false` for
        script requests, so it appends its own `?_=<epoch>` and those are always
        fresh. Confirmed in the browser -- PA_Step4Views.js arrives as
        `?_=1786198859735` while the tags below arrive as written.

        That asymmetry is the whole bug: always-fresh view code running against
        a cached script-tag file. So every script tag needs a marker, or a
        change to it is unbustable and returning users get up to 12 hours of
        the old copy.
        """
        unbustable = []
        with open(INDEX_HTML, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = re.search(r'src="(app/[^"?]+\.js)"', line)
                if match:
                    unbustable.append(match.group(1))

        self.assertEqual(unbustable, [],
                         "these are loaded as plain script tags with no ?v= "
                         "marker, so a change to them cannot be pushed to a "
                         "returning browser: %s" % unbustable)

    def test_the_functions_that_broke_are_present(self):
        """Cheap check that Util.js still defines what other views call.

        Independent of caching: if either disappears, the same ReferenceError
        returns for everyone, cache or no cache.
        """
        committed = _committed("app/view/common/Util.js")
        if committed is None:
            self.skipTest("Util.js could not be read from HEAD")
        source = committed.decode("utf-8", "replace")

        for name in ("getClusterColor", "truncatableTextRenderer"):
            self.assertIn("function %s" % name, source,
                          "Util.js no longer defines %s, which other views "
                          "call as a global" % name)


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

#***************************************************************
#  This file is part of Paintomics v4
#
#  Paintomics is free software: you can redistribute it and/or
#  modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of
#  the License, or (at your option) any later version.
#
#  Paintomics is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Paintomics.  If not, see <http://www.gnu.org/licenses/>.
#
#  More info http://bioinfo.cipf.es/paintomics
#  Technical contact paintomicsai@gmail.com
#**************************************************************
"""The "Choose for me" button behind step 2's compound cards.

Two routes, enqueue-and-poll rather than one blocking call. That is not
politeness: uWSGI serves this whole site with ``processes=1 threads=4``, so a
route that waits on the LLM gateway takes a quarter of the site's capacity with
it, and four concurrent clicks are an outage. The work goes on the same
in-process queue every other long step uses, and the browser polls.

Nothing here writes to the job. The response is advice the user can accept,
adjust or ignore; ``pathwayAcquisitionStep2`` remains the only thing that ever
stores a compound selection.
"""

import logging
from html import escape

from src.common.JobInformationManager import JobInformationManager
from src.common.PySiQ import JobStatus
from src.common.UserSessionManager import UserSessionManager
from src.classes.CompoundDisambiguation import ranker, resolver
from src.servlets.AIInterpretServlet import _requireJobAccess, getAIProviderInfo
# serverconf.py is gitignored: it is generated once per deployment from
# resources/example_serverconf.py and then never overwritten. Deploying this
# code to a server that already has one therefore lands a serverconf without
# this setting, and a plain `from ... import` would ImportError the whole
# application at startup over a feature flag. Default on, matching the template.
try:
    from src.conf.serverconf import AI_COMPOUND_SUGGESTIONS_ENABLED
except ImportError:
    AI_COMPOUND_SUGGESTIONS_ENABLED = True

from src.common.ServerErrorManager import handleException, quotable

#: Queue ids are namespaced so a suggestion run and the step-2 run it advises
#: can never collide on the same job.
QUEUE_PREFIX = "cs_"


def _queueID(jobID):
    return QUEUE_PREFIX + str(jobID)


def _requireCapability():
    """Refuse early when this deployment cannot answer at all.

    Distinct from consent and from access: this is "is the feature switched on
    and is there a token", which the browser also asks before drawing the
    button. Checked again here because a disabled button is a convenience, not
    an authorisation.
    """
    if not AI_COMPOUND_SUGGESTIONS_ENABLED:
        raise UserWarning("AI compound selection is switched off on this server.")

    info = getAIProviderInfo()
    if not info.get("enabled"):
        raise UserWarning("AI features are switched off on this server.")
    if not info.get("configured"):
        raise UserWarning("This server has no API key for its AI provider, so "
                          "compound selection cannot run. Ask the administrator "
                          "to configure one.")


def _panelNames(jobInstance):
    """Every compound name the user's files carried, for context.

    With no experiment design written this is the strongest signal available:
    a panel of amino acids and TCA intermediates identifies itself as primary
    metabolism without anyone saying so.
    """
    names = []
    for compoundSet in ranker.coerceCompoundSets(jobInstance.getFoundCompounds()):
        title = (compoundSet.getTitle() or "").strip()
        if title and title not in names:
            names.append(title)
    return names


def _omicNames(jobInstance):
    omics = []
    for entry in (list(jobInstance.getGeneBasedInputOmics() or []) +
                  list(jobInstance.getCompoundBasedInputOmics() or [])):
        name = (entry or {}).get("omicName") if isinstance(entry, dict) else None
        if name and name not in omics:
            omics.append(name)
    return omics


def buildContext(jobInstance):
    """Everything the model is told about the experiment, all of it already stored."""
    return {
        "organism": jobInstance.getOrganism(),
        "organismLabel": "",
        "jobDescription": jobInstance.getName(),
        "experimentDesign": jobInstance.getExperimentDesign(),
        "omics": _omicNames(jobInstance),
        "panel": _panelNames(jobInstance),
    }


def runCompoundSuggestion(jobID):
    """The queued half: rank deterministically, then ask the model for the rest.

    Returned rather than stored. The queue keeps a job's return value and hands
    it to the status route below, which is enough for advice that the user is
    about to accept or discard; persisting it would add a schema the job does
    not need and a second copy of the truth to keep in step with the ticks.

    @param {String} jobID
    @returns {Dict} the payload the browser applies
    """
    logging.info("COMPOUND SUGGESTION - job %s starting", jobID)

    jobInstance = JobInformationManager().loadJobInstance(jobID)
    if jobInstance is None:
        # Removed while it waited on the queue. The id is the request's; worded
        # as _requireJobAccess words it.
        shownJobID = quotable(jobID)
        raise UserWarning(("Job " + shownJobID if shownJobID is not None else "This job")
                          + " was not found at database.")

    # Reopened jobs hand back plain Feature objects with no accessors;
    # see coerceCompoundSets for why that is normalised here.
    compoundSets = ranker.coerceCompoundSets(jobInstance.getFoundCompounds())
    if not compoundSets:
        return {"decisions": [], "unresolved": [],
                "stats": {"deterministic": 0, "ai": 0, "unresolved": 0, "rejected": 0}}

    organism = jobInstance.getOrganism()
    onMapIDs = ranker.loadOrganismCompoundIDs(organism)
    synonymsByID = ranker.loadCompoundSynonyms(ranker.collectKeggIDs(compoundSets))

    resolved, residual, skipped = ranker.partitionCompoundSets(
        compoundSets, onMapIDs, organism, synonymsByID)

    logging.info("COMPOUND SUGGESTION - job %s: %d sets, %d settled by rule, "
                 "%d to the model, %d with no card (%d compounds on %s maps)",
                 jobID, len(compoundSets), len(resolved), len(residual),
                 len(skipped), len(onMapIDs), organism)

    suggestions = resolver.suggestCompounds(
        residual, buildContext(jobInstance), jobID=jobID)

    decisions = [{"title": decision["title"], "keggID": decision["keggID"],
                  "tier": "deterministic", "confidence": "", "reason": decision["reason"],
                  "candidates": decision.get("candidates", [])}
                 for decision in resolved]
    decisions.extend(suggestions["accepted"])

    unresolved = [{"title": entry["title"],
                   "reason": entry.get("reason", ""),
                   "detail": entry.get("detail", ""),
                   "candidates": entry.get("candidates", [])}
                  for entry in suggestions["abstained"]]

    # The model identifier is deliberately NOT in this payload. The interface
    # never names a specific build (see PA_Step1Views on the consent copy), and
    # the surest way to keep it that way is not to hand the browser the string.
    # It stays in the server log, where the audit trail wants it.
    return {
        "decisions": decisions,
        "unresolved": unresolved,
        "stats": {
            "deterministic": len(resolved),
            "ai": len(suggestions["accepted"]),
            "unresolved": len(unresolved),
            "rejected": len(suggestions["rejected"]),
        },
    }


def compoundSuggestionInitiate(REQUEST, RESPONSE, QUEUE_INSTANCE):
    """POST /pa_suggest_compounds -- queue a suggestion run for a job."""
    userID = None
    try:
        userID = REQUEST.cookies.get('userID')
        sessionToken = REQUEST.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        jobID = REQUEST.form.get("jobID")
        if not jobID:
            raise UserWarning("Missing jobID parameter for compound suggestion.")

        _requireCapability()

        jobInstance = _requireJobAccess(jobID, userID)

        # Read off the STORED record, never off the request.
        # test_ai_consent_enforced.py asserts that at the AST level, and the
        # reason is that a request-borne flag is the caller asserting their own
        # consent. The upload form now submits a hidden 'true', so this passes
        # for anything created through the UI and still refuses a job whose
        # record predates that or was created with the box cleared.
        if not jobInstance.getAIConsent():
            raise UserWarning(
                "AI features were not enabled for this job. Re-run the analysis "
                "if you want its compound names sent to the AI service.")

        queueID = _queueID(jobID)
        existing = QUEUE_INSTANCE.fetch_job(queueID)
        if existing is not None:
            if existing.status in (JobStatus.QUEUED, JobStatus.STARTED):
                RESPONSE.setContent({"success": True, "jobID": jobID, "status": "running"})
                return RESPONSE
            # A finished or failed run from a previous click is spent: drop it
            # so this click gets a fresh answer rather than the old one.
            QUEUE_INSTANCE.get_result(queueID, remove=True)

        QUEUE_INSTANCE.enqueue(
            fn=runCompoundSuggestion,
            args=(jobID,),
            timeout=600,
            job_id=queueID)

        RESPONSE.setContent({"success": True, "jobID": jobID, "status": "queued"})
    except Exception as ex:
        handleException(RESPONSE, ex, __file__, "compoundSuggestionInitiate", userID=userID)
    finally:
        return RESPONSE


def compoundSuggestionStatus(REQUEST, RESPONSE, QUEUE_INSTANCE):
    """POST /pa_suggest_compounds_status -- poll, and collect the result once."""
    userID = None
    try:
        userID = REQUEST.cookies.get('userID')
        sessionToken = REQUEST.cookies.get('sessionToken')
        UserSessionManager().isValidUser(userID, sessionToken)

        jobID = REQUEST.form.get("jobID")
        if not jobID:
            raise UserWarning("Missing jobID parameter for compound suggestion status.")

        # Same entitlement rule as the initiate route. This one returns the
        # job's compound names, so it is not a free read either.
        _requireJobAccess(jobID, userID)

        queueID = _queueID(jobID)
        status = QUEUE_INSTANCE.check_status(queueID)

        if status == JobStatus.FINISHED:
            payload = QUEUE_INSTANCE.get_result(queueID, remove=True)
            if not isinstance(payload, dict):
                raise UserWarning("The compound suggestion finished without a result.")
            RESPONSE.setContent(dict({"success": True, "jobID": jobID,
                                      "status": "finished"}, **payload))
        elif status == JobStatus.FAILED:
            message = QUEUE_INSTANCE.get_error_message(queueID) or "unknown error"
            QUEUE_INSTANCE.get_result(queueID, remove=True)
            logging.warning("COMPOUND SUGGESTION - job %s failed: %s", jobID, message)
            # The worker's exception text, which can quote the job's compound
            # names; the page shows errorMessage through showErrorMessage, as HTML.
            RESPONSE.setContent({"success": False, "jobID": jobID, "status": "failed",
                                 "errorMessage": "AI compound selection failed: " + escape(str(message))})
        elif status == JobStatus.NOT_QUEUED:
            RESPONSE.setContent({"success": True, "jobID": jobID, "status": "not_queued"})
        else:
            RESPONSE.setContent({"success": True, "jobID": jobID, "status": "running"})
    except Exception as ex:
        handleException(RESPONSE, ex, __file__, "compoundSuggestionStatus", userID=userID)
    finally:
        return RESPONSE

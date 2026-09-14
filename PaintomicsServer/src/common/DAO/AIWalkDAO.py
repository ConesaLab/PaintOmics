from datetime import datetime

from src.common.DAO.DAO import DAO


class AIWalkDAO(DAO):
    """One document per (job, walk scope): the progress of an Agentic Graph
    Walk and, once it is sealed, the browser-sized view of its record.

    A collection of its own rather than a field of aiInterpretationCollection:
    the report's status poll reads that whole document every three seconds,
    and a universal walk's view is several hundred kilobytes. The walk and its
    live chain are stored as JSON strings, because node ids and pathway ids
    are external data and a key holding "." or a leading "$" is refused by
    MongoDB. Deleted with the job (PathwayAcquisitionJobDAO.remove and the
    retention sweep in clean_databases).
    """

    COLLECTION = "aiWalkCollection"

    def __init__(self, *args, **kwargs):
        super(AIWalkDAO, self).__init__(*args, **kwargs)
        self.collectionName = self.COLLECTION

    def save_progress(self, job_id, scope, data):
        """Upsert the fields of one walk; stamps updatedAt for stale detection.

        Only the routes file a walk with this; the worker uses update_existing."""
        collection = self.dbManager.getCollection(self.collectionName)
        fields = dict(data)
        fields["updatedAt"] = datetime.utcnow()
        collection.update_one(
            {"jobID": job_id, "scope": scope},
            {"$set": fields, "$setOnInsert": {"createdAt": datetime.utcnow()}},
            upsert=True)

    def update_existing(self, job_id, scope, data):
        """Update a walk that is still filed; never create one. Returns whether
        a document matched.

        The worker writes with this. A job deleted mid-walk takes its walk
        document with it, and an upsert from the still-running worker would
        put the document back -- values and all -- under a job id that no
        sweep will ever look up again. No match tells the worker to stop."""
        collection = self.dbManager.getCollection(self.collectionName)
        fields = dict(data)
        fields["updatedAt"] = datetime.utcnow()
        result = collection.update_one({"jobID": job_id, "scope": scope}, {"$set": fields})
        return result.matched_count > 0

    def find(self, job_id, scope):
        collection = self.dbManager.getCollection(self.collectionName)
        return collection.find_one({"jobID": job_id, "scope": scope})

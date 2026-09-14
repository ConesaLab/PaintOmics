from src.common.DAO.DAO import DAO
from datetime import datetime


class AIInterpretDAO(DAO):
    """The AI chat's conversation with a job, one document per job.

    The interpretation itself is a graph walk and lives in aiWalkCollection
    (AIWalkDAO). Documents written by the previous interpreter may still carry
    its report fields; nothing reads them, and they go with the job.
    """

    def __init__(self, *args, **kwargs):
        super(AIInterpretDAO, self).__init__(*args, **kwargs)
        self.collectionName = "aiInterpretationCollection"

    def find_by_job_id(self, job_id):
        collection = self.dbManager.getCollection(self.collectionName)
        return collection.find_one({"jobID": job_id})

    def append_chat(self, job_id, role, content):
        """Append a message to the conversation, creating the document if needed."""
        collection = self.dbManager.getCollection(self.collectionName)
        collection.update_one(
            {"jobID": job_id},
            {"$push": {"conversation": {"role": role, "content": content,
                                         "timestamp": datetime.utcnow()}},
             "$setOnInsert": {"createdAt": datetime.utcnow()}},
            upsert=True
        )

    def clear_chat(self, job_id):
        """Forget the conversation: it was grounded in an interpretation that a
        new walk is about to replace."""
        collection = self.dbManager.getCollection(self.collectionName)
        collection.update_one({"jobID": job_id}, {"$unset": {"conversation": ""}})

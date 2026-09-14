import logging

from src.common.DAO.DAO import DAO
from src.common.DAO.FeatureDAO import FeatureDAO
from src.common.DAO.FoundFeatureDAO import FoundFeatureDAO
from src.common.DAO.PathwayDAO import PathwayDAO
from src.classes.JobInstances.PathwayAcquisitionJob import PathwayAcquisitionJob
from time import strftime as formatDate

from src.conf.serverconf import CLIENT_TMP_DIR

class PathwayAcquisitionJobDAO(DAO):
    #******************************************************************************************************************
    # CONSTRUCTORS
    #******************************************************************************************************************
    def __init__(self, *args, **kwargs):
        super(PathwayAcquisitionJobDAO, self).__init__(*args, **kwargs)
        self.collectionName = "jobInstanceCollection"

    #******************************************************************************************************************
    # GETTERS AND SETTER
    #******************************************************************************************************************
    def findByID(self, id, otherParams=None):
        jobInstance = None
        collection = self.dbManager.getCollection(self.collectionName)
        match = collection.find_one({"jobID" : id})
        if(match != None):
            match = self.adaptBSON(match)
            jobInstance = PathwayAcquisitionJob(id, "", "")
            jobInstance.parseBSON(match)
            jobInstance.setDirectories(CLIENT_TMP_DIR)

            auxDAO = FoundFeatureDAO(dbManager=self.dbManager)
            match = auxDAO.findAll({"jobID": id})
            for feature in match:
                jobInstance.addFoundCompound(feature)

            # One pass over the job's features instead of two.
            #
            # This used to be findAll({jobID, featureType:"Gene"}) followed by
            # findAll({jobID, featureType:"Compound"}): two cursors, two index
            # walks and two full adaptBSON/parseBSON passes over the same jobID
            # slice, which on a large job is 40k documents' worth of setup paid
            # twice. Both scans walk the same jobID range in the same direction,
            # so partitioning one cursor on the featureType the document itself
            # carries yields each type in exactly the order its own query did,
            # and genes are still added before compounds -- the insertion order
            # of inputGenesData / inputCompoundsData is unchanged.
            #
            # A document with any other featureType (or none) was returned by
            # neither query and is still added to neither dict.
            auxDAO = FeatureDAO(dbManager=self.dbManager)
            match = auxDAO.findAll({"jobID": id})
            geneFeatures = []
            compoundFeatures = []
            for feature in match:
                featureType = getattr(feature, "featureType", None)
                if featureType == "Gene":
                    geneFeatures.append(feature)
                elif featureType == "Compound":
                    compoundFeatures.append(feature)

            for feature in geneFeatures:
                jobInstance.addInputGeneData(feature)
            for feature in compoundFeatures:
                jobInstance.addInputCompoundData(feature)

            auxDAO = PathwayDAO(dbManager=self.dbManager)
            match = auxDAO.findAll({"jobID":id})
            for feature in match:
                # Reactome classes share this collection with pathways and are
                # tagged when written. Anything without the tag -- including
                # every document stored before classes were persisted at all --
                # is a pathway, so old jobs load exactly as before.
                if getattr(feature, "isReactomeClass", False):
                    jobInstance.addMatchedClass(feature)
                else:
                    jobInstance.addMatchedPathway(feature)

        return jobInstance

    def touch(self, jobID):
        collection = self.dbManager.getCollection(self.collectionName)

        collection.update_one({"jobID": jobID}, {'$set': {"accessDate": formatDate("%Y%m%d%H%M")},
                                                 '$unset': {"reminderSent": 1}}, upsert=False)

    def insert(self, instance, otherParams=None):
        jobInstance=instance
        collection = self.dbManager.getCollection(self.collectionName)
        instanceBSON = jobInstance.toBSON(recursive= False)

        instanceBSON["jobType"] = "PathwayAcquisitionJob"

        collection.insert_one(instanceBSON)

        # Save foundCompounds to be able to retrieve the job from database
        if (len(jobInstance.getFoundCompounds()) > 0):
            auxDAO = FoundFeatureDAO(dbManager=self.dbManager)
            auxDAO.insertAll(jobInstance.getFoundCompounds(), {"jobID": jobInstance.getJobID()})

        auxDAO = FeatureDAO(dbManager=self.dbManager)
        if(len(jobInstance.getInputGenesData()) > 0):
            auxDAO.insertAll(jobInstance.getInputGenesData().values(), {"jobID": jobInstance.getJobID()})
        if(len(jobInstance.getInputCompoundsData()) > 0):
            auxDAO.insertAll(jobInstance.getInputCompoundsData().values(), {"jobID": jobInstance.getJobID()})

        # Increase stats
        for counterID in ["jobID", jobInstance.getOrganism()]:
            self.dbManager.getCollection("counters").update_one({'_id': counterID}, {'$inc': { 'counter': 1}}, upsert = True)

        return True

    def update(self, instance, otherParams=None):
        jobInstance=instance
        collection = self.dbManager.getCollection(self.collectionName)
        instanceBSON = jobInstance.toBSON(recursive= False)

        if(otherParams.get("fieldList", None) != None):
            setFields = {}
            for i in otherParams.get("fieldList"):
                setFields[i] = instanceBSON.get(i)

            collection.update_one({"jobID" :jobInstance.getJobID()}, {'$set': setFields})
            return True


        collection.replace_one({"jobID" :jobInstance.getJobID()}, instanceBSON)

        #SHOULD NOT CHANGE
        if(otherParams.get("recursive", None) == True):
            auxDAO = FeatureDAO()
            auxDAO.updateAll(jobInstance.getInputGenesData().values()  , {"jobId": jobInstance.getJobID()})
            auxDAO.updateAll(jobInstance.getInputCompoundsData().values(), {"jobId": jobInstance.getJobID()})
            auxDAO = PathwayDAO(dbManager=self.dbManager)
            auxDAO.updateAll(jobInstance.getMatchedPathways().values(), {"jobID": jobInstance.getJobID()})

        return True


    #******************************************************************************************************************
    # DELETE INSTANCES
    #******************************************************************************************************************
    def remove(self, id, otherParams=None):
        if(otherParams == None or not "userID" in otherParams):
            return False

        collection = self.dbManager.getCollection(self.collectionName)
        deleted = collection.delete_many(
            {"jobID": id, "userID": otherParams.get("userID")})

        # The two cascades below matched on jobID alone, while the delete above
        # is scoped to the owner. So a request to delete someone else's job
        # removed nothing from jobInstanceCollection -- correctly -- and then
        # deleted every feature and every pathway belonging to it anyway.
        #
        # Confirmed against a running server with no cookies at all, since
        # isValidUser lets the anonymous "nologin" case through and
        # dm_delete_job asks for nothing more:
        #
        #     before                  job=1 features=5 pathways=3
        #     after anonymous delete  job=1 features=0 pathways=0
        #     HTTP response           success: True
        #
        # The owner is left holding a job record whose contents are gone. Job
        # ids travel: the results page prints "You can access this job using
        # the URL ...?jobID=...", and there is a sharing feature, so anyone who
        # has ever been given a link had everything needed to do this.
        #
        # Gating on deleted_count ties the cascade to the ownership check that
        # was already there. An anonymous job stores userID None and matches
        # normally, so ordinary deletion is unaffected -- every job on this
        # machine is one of those, and they still delete.
        if deleted.deleted_count == 0:
            logging.warning(
                "REFUSED to delete job %s: no job with that id belongs to "
                "userID %r, so its features and pathways were left alone",
                id, otherParams.get("userID"))
            return False

        FeatureDAO().removeAll({"jobID": id})
        PathwayDAO(dbManager=self.dbManager).removeAll({"jobID": id})

        # The cascade stopped at features and pathways, so deleting a job left
        # three collections holding its data. aiInterpretationCollection is the
        # one that matters: 88KB per record carrying the report, the cited
        # papers and the user's conversation with the agent. On
        # paintomics.uv.es 366 of 437 AI records belonged to jobs that had
        # already been deleted -- and since this is the path a user takes when
        # they press Delete in My Jobs, every one of those is someone who asked
        # for their work to be removed and had the most revealing part of it
        # kept.
        #
        # Done here rather than in the servlet so it is tied to the ownership
        # check above: everything below this point only runs when a job
        # belonging to this caller was actually deleted.
        for collectionName in ("aiInterpretationCollection",
                               "foundFeaturesCollection",
                               "visualOptionsCollection"):
            self.dbManager.getCollection(collectionName).delete_many({"jobID": id})

        return True

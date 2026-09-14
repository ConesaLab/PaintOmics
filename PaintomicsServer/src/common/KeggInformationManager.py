import logging.handlers
import logging

from collections import deque, defaultdict
from threading import RLock as threading_lock
from src.common.Util import Singleton

from src.conf.serverconf import KEGG_CACHE_MAX_SIZE

class KeggInformationManager(metaclass=Singleton):

    def __init__(self, KEGG_DATA_DIR=""):
        logging.info("CREATING NEW INSTANCE FOR KeggInformationManager...")
        self.lock = threading_lock()
        # Separate from self.lock on purpose: getKeggData holds this across a
        # MongoDB read, and the translation cache must not wait behind it.
        self.organismLock = threading_lock()
        self.lastOrganisms = deque([])
        self.translationCache = {}

        #TODO: READ FROM CONF
        self.KEGG_DATA_DIR = KEGG_DATA_DIR + "current/common/"

    #*************************************************************************************
    #   _______  _____             _   _   _____  _              _______  ______
    #  |__   __||  __ \     /\    | \ | | / ____|| |         /\ |__   __||  ____|
    #     | |   | |__) |   /  \   |  \| || (___  | |        /  \   | |   | |__
    #     | |   |  _  /   / /\ \  | . ` | \___ \ | |       / /\ \  | |   |  __|
    #     | |   | | \ \  / ____ \ | |\  | ____) || |____  / ____ \ | |   | |____
    #     |_|   |_|  \_\/_/    \_\|_| \_||_____/ |______|/_/    \_\|_|   |______|
    #*************************************************************************************
    def createTranslationCache(self, jobID):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE

            self.translationCache[jobID] = defaultdict(lambda: {"id": {}, "symbol": {}, "compound": {}})

            return self
        except Exception as ex:
                # Was `raise exTran`, a name that exists nowhere in this file or
                # any other. So the handler that exists to propagate a failure
                # raised NameError instead, and the real cause never surfaced:
                #
                #   createTranslationCache(unhashableJobID)
                #     -> NameError: name 'exTran' is not defined
                #
                # rather than the TypeError that actually happened. Every other
                # method here re-raises `ex`; this one was the outlier.
                raise ex
        finally:
                self.lock.release() #UNLOCK CACHE

    def findBatchInTranslationCache(self, jobID, featureIDs, type="id", dbID = "global"):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE

            if self.translationCache.get(jobID) == None:
                return {}

            selectedDict = self.translationCache.get(jobID)[dbID][type]

            return {featureID: selectedDict.get(featureID) for featureID in featureIDs if featureID in selectedDict}
        except Exception as ex:
            raise ex
        finally:
            self.lock.release() #UNLOCK CACHE

    def updateTranslationCache(self, jobID, newDataTable, type="id", dbID = "global"):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE

            if self.translationCache.get(jobID) != None:
                # In place: the previous `{**old, **new}` rebuilt (and rehashed)
                # the whole table on every 250-name batch, O(cache) per call.
                # Same result -- the newer entry wins in both spellings.
                self.translationCache.get(jobID)[dbID][type].update(newDataTable)
            return True
        except Exception as ex:
                raise ex
        finally:
                self.lock.release() #UNLOCK CACHE

    def clearTranslationCache(self, jobID):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE

            if self.translationCache.get(jobID) != None:
                del self.translationCache[jobID]
            return True
        except Exception as ex:
                raise ex
        finally:
                self.lock.release() #UNLOCK CACHE

    #*************************************************************************************
    #  _____       _______  _    _ __          __   __     __ _____
    # |  __ \  /\ |__   __|| |  | |\ \        / //\ \ \   / // ____|
    # | |__) |/  \   | |   | |__| | \ \  /\  / //  \ \ \_/ /| (___
    # |  ___// /\ \  | |   |  __  |  \ \/  \/ // /\ \ \   /  \___ \
    # | |   / ____ \ | |   | |  | |   \  /\  // ____ \ | |   ____) |
    # |_|  /_/    \_\|_|   |_|  |_|    \/  \//_/    \_\|_|  |_____/
    #*************************************************************************************
    def getAllPathwaysByOrganism(self, organism):
        return self.getKeggData(organism).get("pathways")

    def getPathwayNameByID(self, organism, pathwayID):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE

            pathway = self.getKeggData(organism).get("pathways").get(pathwayID, None)
            if pathway != None:
                return pathway.get("name")
            return "Unknown Pathway " + pathwayID
        finally:
                self.lock.release() #UNLOCK CACHE

    def getPathwayClassificationByID(self, organism, pathwayID):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE
            pathway = self.getKeggData(organism).get("pathways").get(pathwayID, None)
            if pathway != None:
                return pathway.get("classification")
            return "Unknown Pathway " + pathwayID
        finally:
                self.lock.release() #UNLOCK CACHE

    def getPathwaySourceByID(self, organism, pathwayID):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE
            pathway = self.getKeggData(organism).get("pathways").get(pathwayID, None)
            if pathway != None:
                return pathway.get("source", "KEGG")
            return "Unknown Pathway " + pathwayID
        finally:
                self.lock.release() #UNLOCK CACHE

    def getPathwayCanvasSizeByID(self, organism, pathwayID):
        """Canvas a diagram-less source laid its pathway out on, or None.

        Sources with a drawn diagram have their size measured from the PNG.
        A source that has no diagram stores the canvas its installer computed
        the layout on, so the viewer still has a coordinate space to scale the
        feature boxes into.
        """
        try:
            self.lock.acquire()  # LOCK CACHE

            pathway = self.getKeggData(organism).get("pathways").get(pathwayID, None)
            if pathway is None:
                return None
            width, height = pathway.get("imageWidth"), pathway.get("imageHeight")
            if isinstance(width, int) and isinstance(height, int) and width and height:
                return (width, height)
            return None
        finally:
            self.lock.release()  # UNLOCK CACHE

    def getAllFeatureIDsByPathwayID(self, organism, pathwayID):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE
            pathway = self.getKeggData(organism).get("pathways").get(pathwayID, None)
            if pathway != None:
                if not "geneIDList" in pathway:
                    pathway["geneIDList"]=set([])
                    for feature in pathway.get("genes", []):
                        pathway["geneIDList"].add(feature.get("id"))
                if not "compoundIDList" in pathway:
                    pathway["compoundIDList"]=set([])
                    for feature in pathway.get("compounds", []):
                        pathway["compoundIDList"].add(feature.get("id"))

                return pathway["geneIDList"], pathway["compoundIDList"]
            else:
                return [],[]
        finally:
                self.lock.release() #UNLOCK CACHE

    def getAllFeaturesByPathwayID(self, organism, pathwayID):
        """
        This function...

        @param {type}
        @return {type}
        """
        try:
            self.lock.acquire() #LOCK CACHE

            pathway = self.getKeggData(organism).get("pathways").get(pathwayID, None)
            if pathway != None:
                return pathway.get("genes", []), pathway.get("compounds", [])
            else:
                # Keep unpacking stable even if the pathway is missing
                return [], []
        finally:
                self.lock.release() #UNLOCK CACHE

    #*************************************************************************************
    #   ____  _______  _    _  ______  _____
    #  / __ \|__   __|| |  | ||  ____||  __ \
    # | |  | |  | |   | |__| || |__   | |__) |
    # | |  | |  | |   |  __  ||  __|  |  _  /
    # | |__| |  | |   | |  | || |____ | | \ \
    #  \____/   |_|   |_|  |_||______||_|  \_\
    #*************************************************************************************
    def getKeggData(self, organism):
        """
        This function load the KEGG information from database for the
        given organism code

        @param organism, the organism code e.g. mmu
        @returns an object containing all the KEGG information for the specie
        """
        # This was the one method in the class holding no lock while it touched
        # shared state -- it scanned lastOrganisms, loaded on a miss, and
        # appended, all unguarded. Six concurrent callers asking for the same
        # organism therefore ran loadOrganismData six times and left six copies
        # of it in a cache bounded at 25 entries, evicting every other
        # organism. The expensive read repeated exactly when the server was
        # busiest, and the cache degraded under the load it exists to absorb.
        #
        # A lock of its own, not self.lock: loadOrganismData reads pathways from
        # MongoDB, and holding the shared lock across that would stall every
        # translation-cache operation for the duration -- trading a duplicate
        # load for a pause in unrelated work.
        #
        # Held across the load as well as the scan, so the second caller waits
        # and then finds the entry rather than loading its own copy. Serialising
        # loads of the same organism is the point; after warm-up there is
        # nothing to contend over.
        self.organismLock.acquire()
        try:
            for organismData in self.lastOrganisms:
                if organismData.get("name") == organism:
                    return organismData

            #If we are here is because the organism was not in the list
            organismData = self.loadOrganismData(organism)

            #A SIZE LIMITED STACK TO KEEP TEMPORALY THE ORGANISMS DATA
            if len(self.lastOrganisms) >= KEGG_CACHE_MAX_SIZE:
                self.lastOrganisms.popleft()
            self.lastOrganisms.append(organismData)

            return organismData
        finally:
            self.organismLock.release()

    def loadOrganismData(self, organism):
        client, db  = self.getConnectionByOrganismCode(organism)

        try:
            organismData = {
                "name"    : organism,
                "pathways": defaultdict(dict)
            }
            #GET THE KEGG DATA FOR THE GIVEN ORGANISM FROM DATABASE
            cursor=db.kegg.find()
            for item in cursor:
                organismData["pathways"][item["ID"]] = item

            #PROCESS THE DATA AND GENERATE THE TABLES

            #RETURN THE DATA
            return organismData

        except Exception as ex:
            raise ex
        finally:
            client.close()

    def getConnectionByOrganismCode(self, organism):
        """
        Devuelve la conexion a la base de datos del organismo correspondiente asi como el nombre de la tabla
        que se usara para realizar la conversion para dicho organismo y un cursor asociado a ella

        @param {String} organism
        @returns
        """
        from src.conf.serverconf import MONGODB_HOST, MONGODB_PORT
        from src.common.DBmanager import getSharedClient, SharedClientHandle

        # The shared, pid-keyed process client instead of a fresh MongoClient
        # per organism load. Same host, same port, same database, same queries.
        #
        # It is handed back wrapped because every caller of this method owns
        # what it is given and closes it in a finally -- and the process client
        # is not this caller's to close: other threads are serving requests on
        # it. The wrapper forwards everything except close(), which becomes the
        # reference drop it now means. `db` is taken from the real client, so
        # queries never go through the wrapper at all.
        client = getSharedClient(MONGODB_HOST, MONGODB_PORT)
        db = client[organism + "-paintomics"]

        return SharedClientHandle(client), db

    def getKeggDataDir(self):
        return self.KEGG_DATA_DIR

    def getDataDir(self, sourceDB):
        KeggDataDir = self.getKeggDataDir()

        if sourceDB != "KEGG":
            KeggDataDir += "../" + sourceDB.lower() + "/"

        return KeggDataDir

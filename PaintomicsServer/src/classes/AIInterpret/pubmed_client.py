import os
import threading
import time
import requests
import xml.etree.ElementTree as ET
import logging
import re
from src.conf.serverconf import (
    AI_PUBMED_EMAIL, AI_PUBMED_API_KEY,
    AI_MAX_SECTION_CHARS, AI_EUROPEPMC_DELAY,
)

logger = logging.getLogger(__name__)

# GLOBAL rate limiter — shared across ALL threads
_pubmed_lock = threading.Lock()
_pubmed_last_request = 0.0

# Section classification keywords
_SECTION_KEYWORDS = {
    "introduction": {"introduction", "background", "overview"},
    "results": {"result", "finding", "observation", "analysis", "characterization",
                "expression", "profiling", "identification"},
    "discussion": {"discussion", "conclusion", "interpretation", "implication", "significance"},
}
_SKIP_SECTIONS = {"method", "material", "experimental", "supplementary", "supplement", "appendix"}


# ---------------------------------------------------------------------------
# Retrieval guard (agentevolve)
# ---------------------------------------------------------------------------
# This client can fetch the full text of the paper a benchmark is using as its
# answer key. Verified: an esearch for "STATegra" returns both source papers at
# rank 1, and both are open access in PMC. A report that simply read the answer
# is indistinguishable, downstream, from one that derived it -- no scorer, no
# shuffle control and no held-out split catches it, because the text really
# does say the right things.
#
# So the block happens here, at the only place the bytes can enter. Two rules:
#
#   blocklist     every identifier in the study's cluster. STATegra spans five
#                 Europe PMC records -- journal papers, preprints, a companion
#                 data descriptor -- so blocking one PMID closes nothing.
#   date ceiling  nothing published after the study's earliest public version.
#                 Catches the papers that cite it, which restate its findings.
#
# Configured per fold by the harness, via the environment so the guard survives
# into the worker processes the pipeline forks.
_RETRIEVAL_GUARD = {"blocked": frozenset(), "ceiling": None, "loaded": False}

# Every id this client actually returned, for after-the-fact audit. A guard
# nobody can check is a guard nobody should trust.
RETRIEVAL_LOG = []


def _norm_id(value):
    """PMID, PMCID and DOI onto one comparable form."""
    v = str(value or "").strip().lower()
    if v.startswith("pmc"):
        v = v[3:]
    if v.startswith("https://doi.org/"):
        v = v[len("https://doi.org/"):]
    return v.rstrip(".")


def set_retrieval_guard(blocked_ids=(), date_ceiling=None):
    """Refuse these identifiers, and anything published after `date_ceiling`."""
    _RETRIEVAL_GUARD["blocked"] = frozenset(_norm_id(b) for b in blocked_ids if b)
    _RETRIEVAL_GUARD["ceiling"] = date_ceiling
    _RETRIEVAL_GUARD["loaded"] = True
    logger.info("retrieval guard: %d id(s) blocked, ceiling=%s",
                len(_RETRIEVAL_GUARD["blocked"]), date_ceiling)


def _guard():
    if not _RETRIEVAL_GUARD["loaded"]:
        raw = os.environ.get("AGENTEVOLVE_BLOCKLIST", "")
        set_retrieval_guard(
            [x for x in re.split(r"[,\s]+", raw) if x],
            os.environ.get("AGENTEVOLVE_DATE_CEILING") or None)
    return _RETRIEVAL_GUARD


def is_blocked(*ids):
    g = _guard()
    return any(_norm_id(i) in g["blocked"] for i in ids if i)


def _after_ceiling(year):
    g = _guard()
    if not g["ceiling"] or not year:
        return False
    try:
        return int(str(year)[:4]) > int(str(g["ceiling"])[:4])
    except (TypeError, ValueError):
        return False


def _filter_ids(pmids):
    kept = []
    for pmid in pmids or []:
        if is_blocked(pmid):
            logger.warning("retrieval guard: refused blocked id %s", pmid)
            continue
        kept.append(pmid)
        RETRIEVAL_LOG.append(str(pmid))
    return kept



class PubMedClient:
    ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    IDCONV_URL = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
    PMC_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    EUROPEPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    def __init__(self):
        # NCBI's documented ceiling is 3/s unkeyed, but it throttles over a
        # sliding window, so pacing exactly at the limit still draws 429s.
        # Unkeyed runs sit just under it; a key buys both headroom and the
        # higher 10/s ceiling.
        self.rate_limit = 0.11 if AI_PUBMED_API_KEY else 0.40

    def _throttle(self):
        global _pubmed_last_request
        with _pubmed_lock:
            elapsed = time.time() - _pubmed_last_request
            if elapsed < self.rate_limit:
                time.sleep(self.rate_limit - elapsed)
            _pubmed_last_request = time.time()

    def _base_params(self):
        params = {"tool": "paintomics4", "email": AI_PUBMED_EMAIL}
        if AI_PUBMED_API_KEY:
            params["api_key"] = AI_PUBMED_API_KEY
        return params

    def _request_with_retry(self, method, url, max_retries=None, **kwargs):
        """HTTP request with retry on transient errors (429, 5xx, connection/timeout).

        Returns the Response object. Raises on persistent failure.

        Retry depth depends on whether we hold an API key. Without one NCBI caps
        us at 3 req/s and throttles bursts hard: measured runs lost 4-6 of 15
        pathway searches to 429 even with the client's own spacing, and every
        lost search is literature the report never sees. Since retrieval quality
        is the binding constraint on report quality, an unkeyed client backs off
        longer and tries more times rather than dropping the search.
        """
        if max_retries is None:
            max_retries = 2 if AI_PUBMED_API_KEY else 4
        kwargs.setdefault("timeout", 30)
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                self._throttle()
                r = requests.request(method, url, **kwargs)
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt < max_retries:
                        # Honour Retry-After when NCBI sends one; otherwise
                        # exponential with a floor, since 1s is below the window
                        # NCBI actually throttles over.
                        delay = min(2 ** attempt, 16)
                        try:
                            delay = max(delay, int(r.headers.get("Retry-After", 0)))
                        except (AttributeError, TypeError, ValueError):
                            pass
                        logger.warning(f"Retry {attempt+1}/{max_retries} for {url} (HTTP {r.status_code}), waiting {delay}s")
                        time.sleep(delay)
                        continue
                return r
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                if attempt < max_retries:
                    delay = 2 ** attempt
                    logger.warning(f"Retry {attempt+1}/{max_retries} for {url} ({type(e).__name__}), waiting {delay}s")
                    time.sleep(delay)
                    continue
                raise
        raise last_exc  # should not reach here

    # NOTE: both of these went through a bare requests.get + raise_for_status,
    # bypassing _request_with_retry entirely -- so the retry/backoff logic
    # existed but the two hottest paths in the client never used it. Every 429
    # dropped a search outright, and measured runs lost 5-9 searches each,
    # costing literature the report then could not cite. They now go through
    # the retrying wrapper like every other call. (_request_with_retry does its
    # own throttling, so the explicit _throttle() calls are gone.)

    def search(self, query, max_results=5, sort=None):
        """ESearch: returns list of PMIDs. ``sort="relevance"`` asks PubMed for
        its Best Match order; the default is newest first, which for a
        well-studied gene returns this month's papers, not the ones that
        established the claim."""
        params = {**self._base_params(), "db": "pubmed", "term": query,
                  "retmax": max_results, "retmode": "json"}
        if sort:
            params["sort"] = sort
        r = self._request_with_retry("GET", self.ESEARCH_URL, params=params, timeout=15)
        r.raise_for_status()
        return _filter_ids(r.json().get("esearchresult", {}).get("idlist", []))

    def fetch_abstracts(self, pmids):
        """EFetch: returns list of {pmid, title, abstract, year, journal,
        first_author, mesh, pub_types}."""
        if not pmids:
            return []
        pmids = _filter_ids(pmids)
        if not pmids:
            return []
        params = {**self._base_params(), "db": "pubmed", "id": ",".join(pmids),
                  "retmode": "xml", "rettype": "abstract"}
        r = self._request_with_retry("GET", self.EFETCH_URL, params=params, timeout=15)
        r.raise_for_status()
        # The ceiling needs the year, which only exists after parsing -- so it
        # is applied here rather than alongside the id filter above.
        papers = self._parse_xml(r.text)
        kept = [p for p in papers
                if not is_blocked(p.get("pmid"), p.get("doi"))
                and not _after_ceiling(p.get("year"))]
        if len(kept) != len(papers):
            logger.warning("retrieval guard: dropped %d paper(s) after parsing",
                           len(papers) - len(kept))
        return kept

    def _parse_xml(self, xml_text):
        papers = []
        root = ET.fromstring(xml_text)
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID", "")
            # itertext, not .text: .text stops at the first inline element, so
            # "(IP<sub>3</sub>) receptor" came back as "(IP" and a title with
            # an italic gene name lost everything after it.
            title_el = article.find(".//ArticleTitle")
            title = _inline_text(title_el) if title_el is not None else ""
            abstract_parts = article.findall(".//AbstractText")
            abstract = " ".join(_inline_text(el) for el in abstract_parts) if abstract_parts else ""
            year = article.findtext(".//PubDate/Year", "")
            journal = article.findtext(".//Journal/Title", "")
            authors_el = article.findall(".//Author")
            first_author = ""
            if authors_el:
                ln = authors_el[0].findtext("LastName", "")
                fn = authors_el[0].findtext("ForeName", "")
                first_author = f"{fn} {ln}".strip()
            # What the paper studied, as indexed: MeSH descriptor names (no
            # qualifiers) in document order, and the publication types. Both
            # lists are empty for a record PubMed has not indexed yet.
            mesh = _texts(article.findall(".//MeshHeadingList/MeshHeading/DescriptorName"))
            pub_types = _texts(article.findall(".//PublicationTypeList/PublicationType"))
            papers.append({"pmid": pmid, "title": title, "abstract": abstract or "",
                          "year": year, "journal": journal, "first_author": first_author,
                          "mesh": mesh, "pub_types": pub_types})
        return papers

    # ------------------------------------------------------------------
    # Multi-tier full-text fetching
    # ------------------------------------------------------------------

    def convert_pmids_to_pmcids(self, pmids):
        """Batch PMID -> PMCID via NCBI ID Converter API. Returns {pmid: pmcid_or_None}.

        Keys are always strings, whatever the caller or the API supplied --
        see the coercion below for what mixing the two silently cost.
        """
        pmids = [str(p) for p in pmids]
        result = {p: None for p in pmids}
        if not pmids:
            return result

        # Batch up to 200 per request
        for i in range(0, len(pmids), 200):
            batch = pmids[i:i + 200]
            try:
                r = self._request_with_retry("GET", self.IDCONV_URL, params={
                    "tool": "paintomics4", "email": AI_PUBMED_EMAIL,
                    "ids": ",".join(batch), "format": "json",
                })
                r.raise_for_status()
                data = r.json()
                for rec in data.get("records", []):
                    # The converter answers "pmid" as a JSON *number*. The
                    # result dict is keyed by the caller's string PMIDs, so an
                    # unconverted assignment added an integer key beside the
                    # string key it was meant to update -- and every lookup in
                    # fetch_papers then found None. Measured effect: 0 papers
                    # with full text across every AI job ever stored here; the
                    # whole three-tier fetch below this was unreachable.
                    pmid = str(rec.get("pmid") or "")
                    pmcid = rec.get("pmcid")
                    if pmid and pmcid:
                        result[pmid] = pmcid
            except Exception as e:
                logger.warning(f"PMID->PMCID conversion failed for batch: {e}")
        return result

    def fetch_pmc_full_text(self, pmcid):
        """Fetch full text XML from PMC EFetch, parse into sections. Returns dict or None."""
        if is_blocked(pmcid):
            logger.warning("retrieval guard: refused full text for blocked %s", pmcid)
            return None
        try:
            params = {**self._base_params(), "db": "pmc", "id": pmcid, "retmode": "xml"}
            r = self._request_with_retry("GET", self.PMC_EFETCH_URL, params=params)
            r.raise_for_status()
            return self._parse_pmc_xml(r.text)
        except Exception as e:
            logger.warning(f"PMC full-text fetch failed for {pmcid}: {e}")
            return None

    def _parse_pmc_xml(self, xml_text):
        """Parse PMC JATS XML into sections dict. Returns {section_name: text} or None on failure."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"PMC XML parse error: {e}")
            return None

        sections = {}

        # Try to find <body> sections
        body = root.find(".//body")
        if body is None:
            return None

        for sec in body.findall(".//sec"):
            title_el = sec.find("title")
            if title_el is None:
                continue
            title_text = (title_el.text or "").strip().lower()

            # Classify section by title keywords
            section_key = self._classify_section(title_text)
            if section_key is None:
                continue  # skip methods and unrecognized sections

            # Extract all text recursively, strip tags
            text = self._extract_element_text(sec)
            if not text:
                continue

            # Truncate to max chars
            text = text[:AI_MAX_SECTION_CHARS]

            # Merge into existing section (multiple sub-sections may map to same key)
            if section_key in sections:
                remaining = AI_MAX_SECTION_CHARS - len(sections[section_key])
                if remaining > 100:
                    sections[section_key] += "\n\n" + text[:remaining]
            else:
                sections[section_key] = text

        return sections if sections else None

    @staticmethod
    def _classify_section(title_lower):
        """Map a section title to a section key, or None to skip."""
        for skip_kw in _SKIP_SECTIONS:
            if skip_kw in title_lower:
                return None
        for key, keywords in _SECTION_KEYWORDS.items():
            for kw in keywords:
                if kw in title_lower:
                    return key
        return "other"  # unrecognized non-skip → keep as "other"

    @staticmethod
    def _extract_element_text(element):
        """Recursively join all text content from an XML element, stripping tags."""
        parts = []
        for text in element.itertext():
            stripped = text.strip()
            if stripped:
                parts.append(stripped)
        return " ".join(parts)

    def fetch_europepmc_full_text(self, pmcid):
        """Tier 2 fallback: fetch full text XML from Europe PMC using PMCID. Returns sections dict or None."""
        if is_blocked(pmcid):
            logger.warning("retrieval guard: refused full text for blocked %s", pmcid)
            return None
        time.sleep(AI_EUROPEPMC_DELAY)
        try:
            url = f"{self.EUROPEPMC_URL}/{pmcid}/fullTextXML"
            r = self._request_with_retry("GET", url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return self._parse_pmc_xml(r.text)
        except Exception as e:
            logger.warning(f"Europe PMC full-text fetch failed for {pmcid}: {e}")
            return None

    def fetch_papers(self, pmids):
        """Multi-tier paper fetching: abstracts + full text (PMC -> Europe PMC -> abstract only).

        Returns list of enhanced paper dicts with sections and metadata.
        """
        if not pmids:
            return []

        # Step 1: Fetch base metadata via PubMed abstracts
        base_papers = self.fetch_abstracts(pmids)
        if not base_papers:
            return []

        # Build pmid -> paper lookup
        paper_map = {}
        for p in base_papers:
            p["pmcid"] = None
            p["full_text_available"] = False
            p["fetch_tier"] = "abstract_only"
            p["sections"] = {"abstract": p.get("abstract", "")}
            p["full_text_char_count"] = len(p.get("abstract", ""))
            p["authors_short"] = _format_authors_short(p.get("first_author", ""))
            p["pathways"] = []
            paper_map[p["pmid"]] = p

        # Step 2: Batch convert PMIDs to PMCIDs
        pmcid_map = self.convert_pmids_to_pmcids(pmids)

        # Step 3: Try to fetch full text for each paper
        for pmid, paper in paper_map.items():
            pmcid = pmcid_map.get(pmid)
            if pmcid:
                paper["pmcid"] = pmcid

            # Tier 1: PMC EFetch (requires PMCID)
            if pmcid:
                sections = self.fetch_pmc_full_text(pmcid)
                if sections:
                    paper["sections"].update(sections)
                    paper["full_text_available"] = True
                    paper["fetch_tier"] = "pmc"
                    paper["full_text_char_count"] = sum(len(v) for v in paper["sections"].values())
                    logger.info(f"Paper PMID={pmid}: Tier 1 (PMC EFetch) OK — {len(sections)} sections")
                    continue

            # Tier 2: Europe PMC (also requires PMCID — fullTextXML endpoint uses PMCID)
            if pmcid:
                sections = self.fetch_europepmc_full_text(pmcid)
                if sections:
                    paper["sections"].update(sections)
                    paper["full_text_available"] = True
                    paper["fetch_tier"] = "europepmc"
                    paper["full_text_char_count"] = sum(len(v) for v in paper["sections"].values())
                    logger.info(f"Paper PMID={pmid}: Tier 2 (Europe PMC) OK — {len(sections)} sections")
                    continue

            # Tier 3: abstract only (already set as default)
            logger.info(f"Paper PMID={pmid}: Tier 3 (abstract only) — no PMCID or full text unavailable")

        return list(paper_map.values())


def _inline_text(element):
    """An element's text with every inline child (<i>, <sub>, <sup>) kept, in
    one line."""
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def _texts(elements):
    """The non-empty, whitespace-normalised text of each element, in order."""
    out = []
    for el in elements:
        text = _inline_text(el)
        if text:
            out.append(text)
    return out


def _format_authors_short(first_author):
    """'John Smith' -> 'Smith, J. et al.' or pass-through on failure."""
    if not first_author:
        return "Unknown"
    parts = first_author.strip().split()
    if len(parts) >= 2:
        last = parts[-1]
        initials = ".".join(p[0].upper() for p in parts[:-1] if p) + "."
        return f"{last}, {initials} et al."
    return f"{first_author} et al."

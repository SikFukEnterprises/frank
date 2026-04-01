import io
import re
import threading
import time
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

import config

_STRIP_TAGS = [
    "script", "style", "nav", "header", "footer", "aside",
    "form", "noscript", "advertisement", "ads", "cookie",
]
_CONTENT_TAGS = [
    "article", "main", "section", "[role=main]",
    "div#content", "div#main", "div.content", "div.main",
    "div.post", "div.article", "div.entry",
]
_FETCH_TIMEOUT = 10
_DELAY_BETWEEN_FETCHES = 1.0   # minimum gap between any two fetches (shared semaphore)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
_GITHUB_HEADERS = {
    "User-Agent": "frank-research-agent/1.0",
    "Accept": "application/vnd.github.v3+json",
}
# Semaphore limits parallel fetches to avoid ban / overload
_FETCH_SEM = threading.Semaphore(3)
_LAST_FETCH_LOCK = threading.Lock()
_last_fetch_time: float = 0.0

# Query variant suffixes, indexed deterministically via hash
_QUERY_VARIANTS = [
    "{topic} tutorial",
    "{topic} example",
    "{topic} documentation",
    "{topic} specification",
    "{topic} github",
    "{topic} forum",
    "{topic} guide",
    "{topic} reference",
    "{topic} howto",
    "{topic} wiki",
]


def _rate_limited_get(url: str, headers: dict | None = None, timeout: int | None = None) -> requests.Response:
    """GET with a global minimum gap between fetches."""
    global _last_fetch_time
    with _LAST_FETCH_LOCK:
        now = time.monotonic()
        wait = _DELAY_BETWEEN_FETCHES - (now - _last_fetch_time)
        if wait > 0:
            time.sleep(wait)
        _last_fetch_time = time.monotonic()
    h = headers if headers is not None else _HEADERS
    t = timeout if timeout is not None else _FETCH_TIMEOUT
    return requests.get(url, timeout=t, headers=h)


_BOILERPLATE_RE = re.compile(
    r"(?:skip to (?:main )?content|table of contents|cookie|"
    r"share this (?:article|page|post)|follow us on|subscribe to|"
    r"sign up for|newsletter|accept all cookies|privacy policy|"
    r"terms of (?:service|use)|all rights reserved|"
    r"advertisement|sponsored content|related articles|"
    r"read more:|click here to)",
    re.IGNORECASE,
)


def _strip_boilerplate(text: str) -> str:
    """Remove common boilerplate lines before truncation so the char budget
    is spent on actual content."""
    lines = text.split("\n")
    cleaned = [line for line in lines if not _BOILERPLATE_RE.search(line)]
    return "\n".join(cleaned)


def _extract_main_content(soup: BeautifulSoup) -> str:
    """
    Try to find the main content area before falling back to full body text.
    Checks semantic HTML5 elements and common CMS class patterns.
    """
    for selector in [
        ("article", {}),
        ("main", {}),
        ("div", {"id": "content"}),
        ("div", {"id": "main"}),
        ("div", {"id": "main-content"}),
        ("div", {"class": "content"}),
        ("div", {"class": "main"}),
        ("div", {"class": "post-content"}),
        ("div", {"class": "entry-content"}),
        ("div", {"class": "article-body"}),
    ]:
        tag, attrs = selector
        el = soup.find(tag, attrs)
        if el:
            return el.get_text(separator=" ", strip=True)
    return soup.get_text(separator=" ", strip=True)


def _extract_pdf_text(content: bytes) -> str:
    """Extract text from PDF bytes using pypdf. Returns empty string on failure."""
    try:
        import pypdf  # noqa: PLC0415
    except ImportError:
        return ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                pass
        return " ".join(parts)
    except Exception as e:
        print(f"[WARN] PDF extraction failed: {e}")
        return ""


def _is_pdf_url(url: str) -> bool:
    """Return True if the URL path ends with .pdf (case-insensitive)."""
    return url.lower().split("?")[0].rstrip("/").endswith(".pdf")


def _try_wayback(url: str) -> str:
    """
    Attempt to retrieve the most recent Wayback Machine snapshot for a URL.
    Returns the snapshot URL string, or empty string if unavailable.
    """
    try:
        api_url = f"https://archive.org/wayback/available?url={quote_plus(url)}"
        resp = requests.get(api_url, timeout=8, headers=_HEADERS)
        if resp.status_code == 200:
            data = resp.json()
            snapshot = data.get("archived_snapshots", {}).get("closest", {})
            if snapshot.get("available") and snapshot.get("url"):
                return snapshot["url"]
    except Exception as e:
        print(f"[WARN] Wayback Machine lookup failed for {url}: {e}")
    return ""


class WebSearcher:
    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Return up to max_results DuckDuckGo results as {url, title, snippet}."""
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return [
                {
                    "url": r.get("href", ""),
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                }
                for r in results
                if r.get("href")
            ]
        except Exception as e:
            print(f"[WARN] DuckDuckGo search failed for '{query}': {e}")
            return []

    def fetch_page(self, url: str) -> str:
        """
        Fetch a URL and return cleaned body text, truncated to MAX_PAGE_CHARS.
        Handles PDF URLs (if ENABLE_PDF_FETCH) and falls back to Wayback Machine
        (if ENABLE_WAYBACK_FALLBACK) on errors.
        Uses smart content extraction to prefer the main article area.
        """
        with _FETCH_SEM:
            # --- PDF fast path (URL heuristic) ---
            if config.ENABLE_PDF_FETCH and _is_pdf_url(url):
                return self._fetch_pdf(url)

            try:
                resp = _rate_limited_get(url)

                # --- PDF via Content-Type ---
                if config.ENABLE_PDF_FETCH:
                    ct = resp.headers.get("Content-Type", "")
                    if "application/pdf" in ct:
                        text = _extract_pdf_text(resp.content)
                        text = re.sub(r"\s+", " ", text).strip()
                        return text[: config.MAX_PAGE_CHARS]

                if resp.status_code != 200:
                    print(f"[WARN] HTTP {resp.status_code} fetching {url}")
                    if config.ENABLE_WAYBACK_FALLBACK:
                        return self._fetch_via_wayback(url)
                    return ""

                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(_STRIP_TAGS):
                    tag.decompose()
                text = _extract_main_content(soup)
                text = _strip_boilerplate(text)
                text = re.sub(r"\s+", " ", text).strip()
                return text[: config.MAX_PAGE_CHARS]

            except requests.HTTPError as e:
                print(f"[WARN] HTTP {e.response.status_code} fetching {url}")
                if config.ENABLE_WAYBACK_FALLBACK:
                    return self._fetch_via_wayback(url)
                return ""
            except Exception as e:
                print(f"[WARN] Failed to fetch {url}: {e}")
                if config.ENABLE_WAYBACK_FALLBACK:
                    return self._fetch_via_wayback(url)
                return ""

    def _fetch_pdf(self, url: str) -> str:
        """Fetch a URL as PDF and extract text. Returns empty string on any failure."""
        try:
            resp = _rate_limited_get(url)
            resp.raise_for_status()
            text = _extract_pdf_text(resp.content)
            text = re.sub(r"\s+", " ", text).strip()
            return text[: config.MAX_PAGE_CHARS]
        except Exception as e:
            print(f"[WARN] PDF fetch failed for {url}: {e}")
            if config.ENABLE_WAYBACK_FALLBACK:
                return self._fetch_via_wayback(url)
            return ""

    def _fetch_via_wayback(self, original_url: str) -> str:
        """
        Look up original_url in the Wayback Machine and fetch the snapshot.
        Returns extracted page text or empty string.
        """
        snapshot_url = _try_wayback(original_url)
        if not snapshot_url:
            return ""
        try:
            print(f"[INFO] Using Wayback snapshot: {snapshot_url}")
            resp = _rate_limited_get(snapshot_url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(_STRIP_TAGS):
                tag.decompose()
            text = _extract_main_content(soup)
            text = _strip_boilerplate(text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[: config.MAX_PAGE_CHARS]
        except Exception as e:
            print(f"[WARN] Wayback fetch failed for {snapshot_url}: {e}")
            return ""

    def fetch_pages_parallel(self, urls: list[str]) -> list[tuple[str, str]]:
        """
        Fetch multiple pages in parallel. Returns list of (url, text) pairs
        in the same order as input, with empty string for failures.
        """
        results: list[tuple[str, str]] = [("", "")] * len(urls)
        threads = []

        def _worker(idx: int, url: str) -> None:
            text = self.fetch_page(url)
            results[idx] = (url, text)

        for i, url in enumerate(urls):
            t = threading.Thread(target=_worker, args=(i, url), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=_FETCH_TIMEOUT + 5)

        return results

    def search_reddit(self, topic: str, max_results: int = 3) -> list[dict]:
        """
        Search DuckDuckGo restricted to reddit.com for topic.
        Rewrites www.reddit.com URLs to old.reddit.com for cleaner HTML parsing.
        Returns list of {url, title, snippet} same as search().
        """
        query = f"site:reddit.com {topic}"
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
            results = []
            for r in raw:
                url = r.get("href", "")
                if not url:
                    continue
                # Prefer old.reddit.com for cleaner HTML
                url = url.replace("www.reddit.com", "old.reddit.com")
                results.append({
                    "url": url,
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                })
            return results
        except Exception as e:
            print(f"[WARN] Reddit search failed for '{topic}': {e}")
            return []

    def crawl_github(self, topic: str) -> list[tuple[str, str]]:
        """
        Search GitHub for repositories related to topic, fetch their READMEs
        and open issues summaries.
        Returns list of (url, text) pairs.
        Only runs if config.ENABLE_GITHUB_CRAWL is True.
        """
        if not config.ENABLE_GITHUB_CRAWL:
            return []

        results: list[tuple[str, str]] = []
        try:
            search_url = (
                f"https://api.github.com/search/repositories"
                f"?q={quote_plus(topic)}&sort=stars&per_page=3"
            )
            resp = requests.get(search_url, timeout=_FETCH_TIMEOUT, headers=_GITHUB_HEADERS)
            if resp.status_code == 403:
                print("[WARN] GitHub API rate limit hit")
                return []
            if resp.status_code != 200:
                print(f"[WARN] GitHub search returned {resp.status_code}")
                return []
            data = resp.json()
            repos = data.get("items", [])
        except Exception as e:
            print(f"[WARN] GitHub search failed for '{topic}': {e}")
            return []

        for repo in repos:
            owner = repo.get("owner", {}).get("login", "")
            name = repo.get("name", "")
            html_url = repo.get("html_url", f"https://github.com/{owner}/{name}")
            if not owner or not name:
                continue

            # --- README ---
            readme_text = self._fetch_github_readme(owner, name)
            if readme_text:
                results.append((html_url, readme_text[: config.MAX_PAGE_CHARS]))

            # --- Open issues summary ---
            issues_text = self._fetch_github_issues(owner, name)
            if issues_text:
                issues_url = f"https://github.com/{owner}/{name}/issues"
                results.append((issues_url, issues_text[: config.MAX_PAGE_CHARS]))

        return results

    def _fetch_github_readme(self, owner: str, name: str) -> str:
        """Fetch raw README from a GitHub repo, trying main then master branch."""
        for branch in ("main", "master"):
            url = f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/README.md"
            try:
                resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers=_GITHUB_HEADERS)
                if resp.status_code == 200:
                    return resp.text
            except Exception:
                pass
        return ""

    def _fetch_github_issues(self, owner: str, name: str) -> str:
        """Fetch open issues from a GitHub repo and return a text summary."""
        url = (
            f"https://api.github.com/repos/{owner}/{name}"
            f"/issues?state=open&per_page=5"
        )
        try:
            resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers=_GITHUB_HEADERS)
            if resp.status_code == 403:
                print(f"[WARN] GitHub API rate limit on issues for {owner}/{name}")
                return ""
            if resp.status_code != 200:
                return ""
            issues = resp.json()
            if not issues:
                return ""
            lines = []
            for issue in issues:
                title = issue.get("title", "")
                body = (issue.get("body") or "")[:200]
                lines.append(f"Issue: {title}\n{body}")
            return "\n\n".join(lines)
        except Exception as e:
            print(f"[WARN] GitHub issues fetch failed for {owner}/{name}: {e}")
            return ""

    def fetch_rss_topics(
        self, feeds: list[str], visited_urls: set
    ) -> list[tuple[str, str, str]]:
        """
        Parse RSS/Atom feeds and return new (title, url, summary) entries.
        Returns at most 10 total items across all feeds.
        Only runs if config.ENABLE_RSS_MONITOR is True and feedparser is available.
        """
        if not config.ENABLE_RSS_MONITOR:
            return []

        try:
            import feedparser  # noqa: PLC0415
        except ImportError:
            print("[WARN] feedparser not installed — RSS monitoring disabled")
            return []

        items: list[tuple[str, str, str]] = []
        for feed_url in feeds:
            if len(items) >= 10:
                break
            try:
                parsed = feedparser.parse(feed_url)
                for entry in parsed.entries:
                    if len(items) >= 10:
                        break
                    url = entry.get("link", "")
                    if not url or url in visited_urls:
                        continue
                    title = entry.get("title", "")
                    summary = entry.get("summary", entry.get("description", ""))
                    # Strip HTML from summary
                    if summary:
                        soup = BeautifulSoup(summary, "html.parser")
                        summary = soup.get_text(separator=" ", strip=True)[:500]
                    items.append((title, url, summary))
            except Exception as e:
                print(f"[WARN] RSS feed parse failed for {feed_url}: {e}")
                continue

        return items

    def _get_query_variants(self, topic: str, n: int) -> list[str]:
        """
        Return n deterministic query variants for topic by cycling through
        _QUERY_VARIANTS starting at an offset based on the topic's hash.
        """
        if n <= 0:
            return []
        offset = abs(hash(topic)) % len(_QUERY_VARIANTS)
        variants = []
        for i in range(n):
            template = _QUERY_VARIANTS[(offset + i) % len(_QUERY_VARIANTS)]
            variants.append(template.format(topic=topic))
        return variants

    def research_topic(
        self, topic: str, visited_urls: set | None = None
    ) -> tuple[list[str], list[str]]:
        """
        Search for topic and fetch top pages in parallel.
        Also queries GitHub, Reddit, RSS feeds, and variant search queries for
        broader coverage. Skips URLs already in visited_urls.
        Returns (page_texts, source_urls).
        """
        if visited_urls is None:
            visited_urls = set()

        # ── Primary search ──────────────────────────────────────────────────
        primary_results = self.search(topic, max_results=config.PAGES_TO_FETCH + 4)

        # ── Variant query searches ──────────────────────────────────────────
        variant_results: list[dict] = []
        variants = self._get_query_variants(topic, config.SEARCH_QUERY_VARIANTS)
        for variant_query in variants:
            variant_results.extend(
                self.search(variant_query, max_results=config.PAGES_TO_FETCH + 2)
            )

        # Merge primary + variant results, deduplicating by URL
        seen_urls: set[str] = set()
        all_web_results: list[dict] = []
        for r in primary_results + variant_results:
            u = r.get("url", "")
            if u and u not in seen_urls:
                seen_urls.add(u)
                all_web_results.append(r)

        # ── Reddit results ──────────────────────────────────────────────────
        reddit_results = self.search_reddit(topic, max_results=3)
        for r in reddit_results:
            u = r.get("url", "")
            if u and u not in seen_urls:
                seen_urls.add(u)
                all_web_results.append(r)

        # ── RSS feed items ──────────────────────────────────────────────────
        rss_items = self.fetch_rss_topics(config.RSS_FEEDS, visited_urls)
        for (title, url, summary) in rss_items:
            if url not in seen_urls:
                seen_urls.add(url)
                all_web_results.append({"url": url, "title": title, "snippet": summary})

        # ── Prioritise unvisited results ────────────────────────────────────
        fresh = [r for r in all_web_results if r["url"] not in visited_urls]
        stale = [r for r in all_web_results if r["url"] in visited_urls]
        ordered_results = fresh + stale

        # ── GitHub crawl — produces (url, text) pairs directly ──────────────
        github_pairs: list[tuple[str, str]] = self.crawl_github(topic)
        # Filter github pairs against visited_urls
        github_pairs = [(u, t) for (u, t) in github_pairs if u not in visited_urls]

        # ── Build candidate URL pool for web fetches ────────────────────────
        # We want config.PAGES_TO_FETCH total, leaving room for github_pairs
        github_slots = min(len(github_pairs), max(0, config.PAGES_TO_FETCH // 2))
        web_slots = config.PAGES_TO_FETCH - github_slots
        # Top web candidates
        candidates = ordered_results[:web_slots + 4]  # fetch a few extra for fallback
        urls_to_fetch = [r["url"] for r in candidates]
        snippet_map = {r["url"]: r.get("snippet", "") for r in candidates}

        # ── Parallel fetch web pages ────────────────────────────────────────
        fetched = self.fetch_pages_parallel(urls_to_fetch)

        page_texts: list[str] = []
        source_urls: list[str] = []
        web_count = 0

        for url, text in fetched:
            if web_count >= web_slots:
                break
            if not text:
                snippet = snippet_map.get(url, "").strip()
                if snippet:
                    text = snippet[: config.MAX_PAGE_CHARS]
            if text:
                page_texts.append(text)
                source_urls.append(url)
                web_count += 1

        # ── Add GitHub results ──────────────────────────────────────────────
        for url, text in github_pairs[:github_slots]:
            if text:
                page_texts.append(text)
                source_urls.append(url)

        return page_texts, source_urls

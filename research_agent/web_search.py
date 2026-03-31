import re
import threading
import time

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

import config

_STRIP_TAGS  = ["script", "style", "nav", "header", "footer", "aside",
                "form", "noscript", "advertisement", "ads", "cookie"]
_CONTENT_TAGS = ["article", "main", "section", "[role=main]",
                 "div#content", "div#main", "div.content", "div.main",
                 "div.post", "div.article", "div.entry"]
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
# Semaphore limits parallel fetches to avoid ban / overload
_FETCH_SEM = threading.Semaphore(3)
_LAST_FETCH_LOCK = threading.Lock()
_last_fetch_time: float = 0.0


def _rate_limited_get(url: str) -> requests.Response:
    """GET with a global minimum gap between fetches."""
    global _last_fetch_time
    with _LAST_FETCH_LOCK:
        now = time.monotonic()
        wait = _DELAY_BETWEEN_FETCHES - (now - _last_fetch_time)
        if wait > 0:
            time.sleep(wait)
        _last_fetch_time = time.monotonic()
    return requests.get(url, timeout=_FETCH_TIMEOUT, headers=_HEADERS)


def _extract_main_content(soup: BeautifulSoup) -> str:
    """
    Try to find the main content area before falling back to full body text.
    Checks semantic HTML5 elements and common CMS class patterns.
    """
    # Try semantic / role attributes first
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


class WebSearcher:
    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Return up to max_results DuckDuckGo results as {url, title, snippet}."""
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return [
                {"url": r.get("href", ""), "title": r.get("title", ""), "snippet": r.get("body", "")}
                for r in results
                if r.get("href")
            ]
        except Exception as e:
            print(f"[WARN] DuckDuckGo search failed for '{query}': {e}")
            return []

    def fetch_page(self, url: str) -> str:
        """
        Fetch a URL and return cleaned body text, truncated to MAX_PAGE_CHARS.
        Uses smart content extraction to prefer the main article area.
        """
        with _FETCH_SEM:
            try:
                resp = _rate_limited_get(url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(_STRIP_TAGS):
                    tag.decompose()
                text = _extract_main_content(soup)
                text = re.sub(r"\s+", " ", text).strip()
                return text[: config.MAX_PAGE_CHARS]
            except requests.HTTPError as e:
                print(f"[WARN] HTTP {e.response.status_code} fetching {url}")
                return ""
            except Exception as e:
                print(f"[WARN] Failed to fetch {url}: {e}")
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

    def research_topic(
        self, topic: str, visited_urls: set | None = None
    ) -> tuple[list[str], list[str]]:
        """
        Search for topic and fetch top pages in parallel.
        Skips URLs already in visited_urls to avoid re-fetching known content.
        Returns (page_texts, source_urls).
        """
        results = self.search(topic, max_results=config.PAGES_TO_FETCH + 4)
        if not results:
            return [], []

        if visited_urls:
            fresh = [r for r in results if r["url"] not in visited_urls]
            stale = [r for r in results if r["url"] in visited_urls]
            results = fresh + stale

        # Select candidate URLs up to PAGES_TO_FETCH
        candidates = results[: config.PAGES_TO_FETCH]
        urls = [r["url"] for r in candidates]
        snippet_map = {r["url"]: r.get("snippet", "") for r in candidates}

        # Parallel fetch
        fetched = self.fetch_pages_parallel(urls)

        page_texts: list[str] = []
        source_urls: list[str] = []

        for url, text in fetched:
            if not text:
                snippet = snippet_map.get(url, "").strip()
                if snippet:
                    text = snippet[: config.MAX_PAGE_CHARS]
            if text:
                page_texts.append(text)
                source_urls.append(url)

        return page_texts, source_urls

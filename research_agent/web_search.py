import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

import config

_STRIP_TAGS = ["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]
_FETCH_TIMEOUT = 10
_PAGES_TO_FETCH = 3
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


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
        """Fetch a URL and return cleaned body text, truncated to MAX_PAGE_CHARS."""
        try:
            resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers=_HEADERS)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(_STRIP_TAGS):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            # Collapse whitespace
            import re
            text = re.sub(r"\s+", " ", text).strip()
            return text[: config.MAX_PAGE_CHARS]
        except Exception as e:
            print(f"[WARN] Failed to fetch {url}: {e}")
            return ""

    def research_topic(self, topic: str) -> tuple[list[str], list[str]]:
        """
        Search for topic and fetch top pages.
        Returns (page_texts, source_urls).
        """
        results = self.search(topic)
        if not results:
            return [], []

        page_texts: list[str] = []
        source_urls: list[str] = []

        for r in results[:_PAGES_TO_FETCH]:
            url = r["url"]
            text = self.fetch_page(url)
            if text:
                page_texts.append(text)
                source_urls.append(url)
            if len(page_texts) >= _PAGES_TO_FETCH:
                break

        return page_texts, source_urls

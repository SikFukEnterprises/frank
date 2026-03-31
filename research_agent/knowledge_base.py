import json
import os
import re
from datetime import datetime, timezone, timedelta

import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(topic: str) -> str:
    return topic.strip().lower()


# ── Semantic fact normalisation ────────────────────────────────────────────────
_ARTICLES_RE = re.compile(r"\b(the|a|an)\b", re.IGNORECASE)
_PUNCT_RE    = re.compile(r"[^\w\s]")
_WS_RE       = re.compile(r"\s+")

def _normalize_fact(content: str) -> str:
    """Normalise a fact string for semantic deduplication."""
    s = content.lower()
    s = _ARTICLES_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


# ── Source quality scoring ─────────────────────────────────────────────────────
_HIGH_QUALITY_DOMAINS = {
    "github.com", "stackoverflow.com", "stackexchange.com",
    "arxiv.org", "docs.python.org", "developer.mozilla.org",
    "wiki.openstreetmap.org", "en.wikipedia.org",
    # automotive / embedded
    "romraider.com", "opengarages.org", "can-bus.org",
    "csselectronics.com", "kvaser.com", "peak-system.com",
    "ecu.edu.au", "automotive.wiki",
}
_LOW_QUALITY_PATTERNS = re.compile(
    r"(blogspot\.com|wordpress\.com/\d|\.blogspot\.|/amp/|clickbait|"
    r"top-\d+-|best-\d+-|\d+-ways-)",
    re.IGNORECASE,
)

def _source_quality(url: str) -> str:
    """Return 'high', 'medium', or 'low' for a source URL."""
    if not url:
        return "medium"
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lstrip("www.")
    except Exception:
        domain = ""
    if domain in _HIGH_QUALITY_DOMAINS or domain.endswith(".edu") or domain.endswith(".gov"):
        return "high"
    if _LOW_QUALITY_PATTERNS.search(url):
        return "low"
    return "medium"


# ── Confidence helpers ─────────────────────────────────────────────────────────
_CONF_RANK = {"high": 0, "medium": 1, "low": 2}
_CONF_UP   = {"low": "medium", "medium": "high", "high": "high"}
_CONF_DOWN = {"high": "medium", "medium": "low", "low": "low"}

def _upgrade_confidence(conf: str) -> str:
    return _CONF_UP.get(conf, conf)

def _downgrade_confidence(conf: str) -> str:
    return _CONF_DOWN.get(conf, conf)


class KnowledgeBase:
    def __init__(self):
        self._data: dict = {}
        self._dirty = False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def init_fresh(self, seed_topic: str) -> None:
        self._data = {
            "meta": {
                "created": _now(),
                "last_updated": _now(),
                "topics_researched": 0,
                "total_facts": 0,
                "seed_topic": seed_topic,
            },
            "research_queue": [],
            "completed_topics": [],
            "visited_urls": [],
            "dry_topics": {},      # topic_key → consecutive zero-fact cycles
            "follow_up_mentions": {},  # topic_key → times proposed as follow-up
            "knowledge": {},
        }
        self.add_to_queue(seed_topic, priority=1, source_topic="seed")
        self._ensure_data_dir()
        self.save()

    def load(self) -> bool:
        """Load from disk. Returns True if loaded, False if file missing."""
        if not os.path.exists(config.KNOWLEDGE_FILE):
            return False
        try:
            with open(config.KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            # Back-fill keys added in newer versions
            self._data.setdefault("dry_topics", {})
            self._data.setdefault("follow_up_mentions", {})
            self._data.setdefault("visited_urls", [])
            return True
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] Failed to load knowledge base: {e}")
            return False

    def save(self) -> None:
        self._ensure_data_dir()
        self._data["meta"]["last_updated"] = _now()
        try:
            with open(config.KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"[ERROR] Failed to save knowledge base: {e}")

    def _ensure_data_dir(self) -> None:
        os.makedirs(os.path.dirname(config.KNOWLEDGE_FILE), exist_ok=True)

    # ------------------------------------------------------------------
    # Visited URL tracking
    # ------------------------------------------------------------------

    def mark_url_visited(self, url: str) -> None:
        visited = self._data.setdefault("visited_urls", [])
        if url not in visited:
            visited.append(url)

    def get_visited_urls(self) -> set:
        return set(self._data.get("visited_urls", []))

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def add_to_queue(self, topic: str, priority: int = 2, source_topic: str = "") -> None:
        key = _normalize(topic)
        if self.is_completed(key):
            return
        if self.is_dry(key):
            return
        queue = self._data["research_queue"]
        if len(queue) >= config.MAX_QUEUE_SIZE:
            return

        # Track mention frequency; boost priority for frequently requested topics
        mentions = self._data.setdefault("follow_up_mentions", {})
        if source_topic and source_topic not in ("seed", "gap_analysis", "manual"):
            mentions[key] = mentions.get(key, 0) + 1
            # Every 3 mentions, bump priority up by 1 (floor 1)
            mention_boost = mentions[key] // 3
            priority = max(1, priority - mention_boost)

        # Deduplicate: if already queued, upgrade priority if this one is better
        for item in queue:
            if _normalize(item["topic"]) == key:
                if priority < item["priority"]:
                    item["priority"] = priority
                    queue.sort(key=lambda x: x["priority"])
                return

        queue.append({
            "topic": topic,
            "priority": priority,
            "added_at": _now(),
            "source_topic": source_topic,
        })
        queue.sort(key=lambda x: x["priority"])

    def remove_from_queue(self, topic: str) -> bool:
        """Remove a topic from the queue. Returns True if found and removed."""
        key = _normalize(topic)
        queue = self._data["research_queue"]
        before = len(queue)
        self._data["research_queue"] = [
            item for item in queue if _normalize(item["topic"]) != key
        ]
        return len(self._data["research_queue"]) < before

    def pop_next_topic(self) -> dict | None:
        queue = self._data["research_queue"]
        if not queue:
            return None
        return queue.pop(0)

    def queue_size(self) -> int:
        return len(self._data["research_queue"])

    # ------------------------------------------------------------------
    # Dry topic tracking (items that repeatedly return zero facts)
    # ------------------------------------------------------------------

    def increment_dry_count(self, topic: str) -> int:
        """Increment zero-fact counter for a topic. Returns new count."""
        key = _normalize(topic)
        dry = self._data.setdefault("dry_topics", {})
        dry[key] = dry.get(key, 0) + 1
        return dry[key]

    def reset_dry_count(self, topic: str) -> None:
        key = _normalize(topic)
        self._data.get("dry_topics", {}).pop(key, None)

    def is_dry(self, topic: str) -> bool:
        key = _normalize(topic)
        count = self._data.get("dry_topics", {}).get(key, 0)
        return count >= config.DRY_TOPIC_THRESHOLD

    # ------------------------------------------------------------------
    # Completion tracking
    # ------------------------------------------------------------------

    def is_completed(self, topic: str) -> bool:
        return _normalize(topic) in [_normalize(t) for t in self._data.get("completed_topics", [])]

    def mark_completed(self, topic: str) -> None:
        key = _normalize(topic)
        completed = self._data.setdefault("completed_topics", [])
        if key not in [_normalize(t) for t in completed]:
            completed.append(topic)
        self._data["meta"]["topics_researched"] = len(completed)

    # ------------------------------------------------------------------
    # Re-research scheduling (item 7)
    # ------------------------------------------------------------------

    def get_stale_topics_for_requeue(self) -> list[str]:
        """
        Return topics that are old enough and important enough to re-research.
        A topic qualifies if:
          - last_researched is older than RERESEARCH_DAYS
          - has at least 2 facts or has been researched 2+ times (worth keeping fresh)
          - is not currently in the queue
        """
        if not config.RERESEARCH_DAYS:
            return []
        threshold = datetime.now(timezone.utc) - timedelta(days=config.RERESEARCH_DAYS)
        queued_keys = {_normalize(item["topic"]) for item in self._data.get("research_queue", [])}
        candidates = []
        for key, entry in self._data.get("knowledge", {}).items():
            if key in queued_keys:
                continue
            last = entry.get("last_researched", "")
            if not last:
                continue
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if last_dt < threshold:
                fact_count = len(entry.get("facts", []))
                research_count = entry.get("research_count", 0)
                if fact_count >= 2 or research_count >= 2:
                    candidates.append(key)
        return candidates

    # ------------------------------------------------------------------
    # Knowledge read/write
    # ------------------------------------------------------------------

    def get_topic(self, topic: str) -> dict | None:
        return self._data["knowledge"].get(_normalize(topic))

    def update_topic(self, topic: str, extracted: dict) -> tuple[int, int]:
        """
        Merge extracted knowledge into the knowledge base.
        Returns (new_facts_added, conflicts_found).

        Improvements over original:
        - Semantic deduplication (normalised content key)
        - Multi-source corroboration: source_count tracked per fact, confidence upgraded
        - Source quality stored per fact
        - Confidence decay on conflict: contradicted facts downgraded
        """
        key = _normalize(topic)
        knowledge = self._data.setdefault("knowledge", {})
        existing = knowledge.get(key)

        if existing is None:
            existing = {
                "summary": "",
                "summary_model_rank": 999,
                "weak_research_count": 0,
                "facts": [],
                "conflicts": [],
                "related_topics": [],
                "last_researched": _now(),
                "research_count": 0,
            }

        # ── Summary with corroboration guard ──────────────────────────────────
        new_rank = extracted.get("_model_rank", 999)
        existing_rank = existing.get("summary_model_rank", 999)
        new_summary = extracted.get("summary", "")

        if new_summary:
            if new_rank <= existing_rank:
                existing["summary"] = new_summary
                existing["summary_model_rank"] = new_rank
                existing["weak_research_count"] = 0
            else:
                rank_diff = new_rank - existing_rank
                wrc = existing.get("weak_research_count", 0) + 1
                existing["weak_research_count"] = wrc
                if wrc >= rank_diff + 1:
                    existing["summary"] = new_summary
                    existing["summary_model_rank"] = new_rank
                    existing["weak_research_count"] = 0

        # ── Fact merge with semantic dedup + multi-source corroboration ────────
        # Index existing facts by their normalised content for semantic matching
        existing_by_norm: dict[str, dict] = {
            _normalize_fact(f["content"]): f for f in existing["facts"]
        }
        facts_before = len(existing["facts"])

        for fact in extracted.get("facts", []):
            content = fact.get("content", "")
            if not content:
                continue
            norm = _normalize_fact(content)

            # Annotate source quality
            sq = _source_quality(fact.get("source_url", ""))
            fact.setdefault("source_quality", sq)
            fact.setdefault("source_count", 1)

            if norm not in existing_by_norm:
                existing_by_norm[norm] = fact
            else:
                old = existing_by_norm[norm]
                # Increment corroboration count
                old["source_count"] = old.get("source_count", 1) + 1
                # Upgrade confidence based on corroboration
                sc = old["source_count"]
                if sc >= 3:
                    old["confidence"] = "high"
                elif sc >= 2 and _CONF_RANK.get(old.get("confidence", "low"), 2) > 0:
                    old["confidence"] = _upgrade_confidence(old["confidence"])
                # Upgrade confidence for high-quality sources
                if sq == "high" and old.get("source_quality") != "high":
                    old["confidence"] = _upgrade_confidence(old["confidence"])
                    old["source_quality"] = "high"
                # Keep stronger-model version of content/metadata
                old_rank = old.get("model_rank", 999)
                if fact.get("model_rank", 999) < old_rank:
                    # Preserve corroboration fields when upgrading
                    sc_saved = old["source_count"]
                    sq_saved = old.get("source_quality", "medium")
                    existing_by_norm[norm] = {**fact,
                                              "source_count": sc_saved,
                                              "source_quality": sq_saved}

        existing["facts"] = list(existing_by_norm.values())
        new_facts_added = len(existing["facts"]) - facts_before

        # ── Conflicts: add new ones + decay confidence of contradicted facts ───
        new_conflicts = extracted.get("conflicts", [])
        if new_conflicts:
            existing["conflicts"].extend(new_conflicts)
            # Downgrade confidence of any existing facts that appear in a conflict
            conflict_snippets = set()
            for c in new_conflicts:
                for field in ("fact_a", "fact_b"):
                    snippet = c.get(field, "")
                    if snippet:
                        conflict_snippets.add(_normalize_fact(snippet))
            for norm_key, fact in existing_by_norm.items():
                if norm_key in conflict_snippets:
                    fact["confidence"] = _downgrade_confidence(fact.get("confidence", "medium"))
                    fact["needs_verification"] = True

        # ── Related topics (deduplicate) ──────────────────────────────────────
        for rt in extracted.get("related_topics", []):
            if rt not in existing["related_topics"]:
                existing["related_topics"].append(rt)

        existing["last_researched"] = _now()
        existing["research_count"] = existing.get("research_count", 0) + 1

        knowledge[key] = existing

        total = sum(len(v["facts"]) for v in knowledge.values())
        self._data["meta"]["total_facts"] = total

        return new_facts_added, len(new_conflicts)

    def update_related_topics(self, cross_ref: dict) -> None:
        """Apply cross-reference results to update related_topics fields."""
        connections = cross_ref.get("connections", [])
        for conn in connections:
            topic_a = _normalize(conn.get("topic_a", ""))
            topic_b = _normalize(conn.get("topic_b", ""))
            if topic_a in self._data["knowledge"] and topic_b:
                rt = self._data["knowledge"][topic_a].setdefault("related_topics", [])
                if topic_b not in rt:
                    rt.append(topic_b)
            if topic_b in self._data["knowledge"] and topic_a:
                rt = self._data["knowledge"][topic_b].setdefault("related_topics", [])
                if topic_a not in rt:
                    rt.append(topic_a)

    def get_all_summaries(self) -> dict:
        """Return {topic_key: summary} for all researched topics."""
        return {
            k: v.get("summary", "")
            for k, v in self._data["knowledge"].items()
        }

    def topic_count(self) -> int:
        return len(self._data.get("knowledge", {}))

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        meta = self._data.get("meta", {})
        return {
            "topics_researched": meta.get("topics_researched", 0),
            "queue_size": self.queue_size(),
            "total_facts": meta.get("total_facts", 0),
            "seed_topic": meta.get("seed_topic", ""),
        }

    def get_seed_topic(self) -> str:
        return self._data.get("meta", {}).get("seed_topic", "")

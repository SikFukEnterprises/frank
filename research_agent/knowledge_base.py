import json
import os
from datetime import datetime, timezone

import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(topic: str) -> str:
    return topic.strip().lower()


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
    # Queue management
    # ------------------------------------------------------------------

    def add_to_queue(self, topic: str, priority: int = 2, source_topic: str = "") -> None:
        key = _normalize(topic)
        if self.is_completed(key):
            return
        queue = self._data["research_queue"]
        if len(queue) >= config.MAX_QUEUE_SIZE:
            return
        # Avoid duplicates
        for item in queue:
            if _normalize(item["topic"]) == key:
                return
        queue.append({
            "topic": topic,
            "priority": priority,
            "added_at": _now(),
            "source_topic": source_topic,
        })
        # Keep sorted: lowest priority number = highest priority
        queue.sort(key=lambda x: x["priority"])

    def pop_next_topic(self) -> dict | None:
        queue = self._data["research_queue"]
        if not queue:
            return None
        return queue.pop(0)

    def queue_size(self) -> int:
        return len(self._data["research_queue"])

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
    # Knowledge read/write
    # ------------------------------------------------------------------

    def get_topic(self, topic: str) -> dict | None:
        return self._data["knowledge"].get(_normalize(topic))

    def update_topic(self, topic: str, extracted: dict) -> None:
        key = _normalize(topic)
        knowledge = self._data.setdefault("knowledge", {})
        existing = knowledge.get(key)

        if existing is None:
            existing = {
                "summary": "",
                "facts": [],
                "conflicts": [],
                "related_topics": [],
                "last_researched": _now(),
                "research_count": 0,
            }

        # Merge facts (deduplicate by content)
        existing_contents = {f["content"] for f in existing["facts"]}
        new_facts = extracted.get("facts", [])
        for fact in new_facts:
            if fact.get("content") and fact["content"] not in existing_contents:
                existing["facts"].append(fact)
                existing_contents.add(fact["content"])

        # Merge conflicts
        existing["conflicts"].extend(extracted.get("conflicts", []))

        # Merge related topics (deduplicate)
        for rt in extracted.get("related_topics", []):
            if rt not in existing["related_topics"]:
                existing["related_topics"].append(rt)

        existing["summary"] = extracted.get("summary", existing["summary"])
        existing["last_researched"] = _now()
        existing["research_count"] = existing.get("research_count", 0) + 1

        knowledge[key] = existing

        # Update total_facts count
        total = sum(len(v["facts"]) for v in knowledge.values())
        self._data["meta"]["total_facts"] = total

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

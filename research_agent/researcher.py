import threading
import time
from datetime import datetime, timezone

import config
from groq_client import GroqClient
from knowledge_base import KnowledgeBase
from web_search import WebSearcher


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


class ResearchLoop:
    def __init__(self, kb: KnowledgeBase, searcher: WebSearcher, groq: GroqClient):
        self._kb = kb
        self._searcher = searcher
        self._groq = groq
        self._stop_event = threading.Event()
        self._log_lock = threading.Lock()
        self._log_file = None
        self._open_log()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _open_log(self) -> None:
        import os
        os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
        try:
            self._log_file = open(config.LOG_FILE, "a", encoding="utf-8", buffering=1)
        except OSError as e:
            print(f"[WARN] Cannot open log file: {e}")

    def _log(self, msg: str) -> None:
        line = f"[{_ts()}] {msg}"
        with self._log_lock:
            print(line)
            if self._log_file:
                try:
                    self._log_file.write(line + "\n")
                except OSError:
                    pass

    def _close_log(self) -> None:
        if self._log_file:
            try:
                self._log_file.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def signal_stop(self) -> None:
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_cycle()
            except Exception as e:
                self._log(f"[ERROR] Unexpected error in research cycle: {e}")
                time.sleep(5)
        self._close_log()

    def _run_cycle(self) -> None:
        # Step 1 — Topic selection
        topic_item = self._kb.pop_next_topic()
        if topic_item is None:
            self._log("Queue empty — generating new topics from knowledge gaps...")
            self._generate_gap_topics()
            topic_item = self._kb.pop_next_topic()
            if topic_item is None:
                self._log("Still no topics after gap generation. Sleeping 30s...")
                time.sleep(30)
                return

        topic = topic_item["topic"]
        self._log(f"Researching: {topic}")

        # Step 2 — Web search
        page_texts, source_urls = self._searcher.research_topic(topic)
        total_chars = sum(len(t) for t in page_texts)
        self._log(f"Fetched {len(page_texts)} pages ({total_chars:,} chars)")

        if not page_texts:
            self._log(f"[SKIP] No web content found for '{topic}' — skipping")
            self._kb.mark_completed(topic)
            self._kb.save()
            return

        # Step 3 — Knowledge extraction
        existing = self._kb.get_topic(topic)
        existing_summary = existing.get("summary", "") if existing else ""

        extracted = None
        try:
            extracted = self._groq.extract_knowledge(topic, existing_summary, page_texts, source_urls)
        except Exception as e:
            self._log(f"[ERROR] Groq extraction failed for '{topic}': {e}. Retrying in 60s...")
            time.sleep(60)
            try:
                extracted = self._groq.extract_knowledge(topic, existing_summary, page_texts, source_urls)
            except Exception as e2:
                self._log(f"[ERROR] Retry failed for '{topic}': {e2}. Skipping.")
                return

        if extracted is None:
            self._log(f"[SKIP] JSON parse failed for '{topic}' — skipping")
            return

        facts_count = len(extracted.get("facts", []))
        conflicts_count = len(extracted.get("conflicts", []))
        follow_ups = extracted.get("follow_up_topics", [])
        self._log(
            f"Extracted {facts_count} facts | "
            f"{conflicts_count} conflict(s) detected | "
            f"{len(follow_ups)} new topics queued"
        )

        # Step 4 — Cross reference
        all_summaries = self._kb.get_all_summaries()
        cross_ref = None
        try:
            cross_ref = self._groq.cross_reference(topic, extracted.get("facts", []), all_summaries)
        except Exception as e:
            self._log(f"[WARN] Cross-reference call failed: {e}")

        # Step 5 — Knowledge base update
        self._kb.update_topic(topic, extracted)
        if cross_ref:
            self._kb.update_related_topics(cross_ref)
            for gap in cross_ref.get("knowledge_gaps", [])[:config.MAX_TOPICS_PER_CYCLE]:
                self._kb.add_to_queue(gap, priority=2, source_topic=topic)

        # Add follow-up topics from extraction
        for ft in follow_ups[:config.MAX_TOPICS_PER_CYCLE]:
            # High-confidence source → priority 1, otherwise 2
            priority = 1 if any(f.get("confidence") == "high" for f in extracted.get("facts", [])) else 2
            self._kb.add_to_queue(ft, priority=priority, source_topic=topic)

        self._kb.mark_completed(topic)

        try:
            self._kb.save()
        except Exception as e:
            self._log(f"[ERROR] Failed to save knowledge base: {e}. Continuing in memory.")

        stats = self._kb.get_stats()
        self._log(
            f"Knowledge base updated. "
            f"Topics done: {stats['topics_researched']} | "
            f"Queue: {stats['queue_size']} | "
            f"Facts: {stats['total_facts']}"
        )

        # Step 6 — Sleep between cycles
        if not self._stop_event.is_set():
            time.sleep(config.SLEEP_BETWEEN_CYCLES)

    # ------------------------------------------------------------------
    # Gap topic generation
    # ------------------------------------------------------------------

    def _generate_gap_topics(self) -> None:
        all_summaries = self._kb.get_all_summaries()
        try:
            topics = self._groq.generate_gap_topics(all_summaries)
        except Exception as e:
            self._log(f"[WARN] Gap topic generation failed: {e}")
            return
        for t in topics:
            self._kb.add_to_queue(t, priority=3, source_topic="gap_analysis")
        if topics:
            self._log(f"Generated {len(topics)} new topics from knowledge gaps")

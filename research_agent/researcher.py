import threading
import time
from datetime import datetime, timezone

import config
import ui
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
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()   # set = paused
        self._log_lock    = threading.Lock()
        self._log_file    = None
        self._current_topic: str = ""
        self._consecutive_barren: int = 0       # cycles with 0 new facts (adaptive sleep)
        self._open_log()

    def get_current_topic(self) -> str:
        return self._current_topic

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _open_log(self) -> None:
        import os
        os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
        try:
            self._log_file = open(config.LOG_FILE, "a", encoding="utf-8", buffering=1)
        except OSError as e:
            ui.log("warn", f"Cannot open log file: {e}")

    def _log(self, msg: str, level: str = "info") -> None:
        with self._log_lock:
            ui.log(level, msg)
            if self._log_file:
                ts = _ts()
                try:
                    self._log_file.write(f"[{ts}] {msg}\n")
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
        self._pause_event.clear()   # unblock if paused

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def pause(self) -> None:
        self._pause_event.set()
        self._log("Research paused — type [cyan]resume[/cyan] to continue.", "warn")

    def resume(self) -> None:
        self._pause_event.clear()
        self._log("Research resumed.", "ok")

    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        while not self._stop_event.is_set():
            # Honour pause: spin-wait cheaply
            if self._pause_event.is_set():
                time.sleep(0.5)
                continue
            try:
                self._run_cycle()
            except Exception as e:
                self._log(f"Unexpected error in research cycle: {e}", "error")
                time.sleep(5)
        self._close_log()

    def _run_cycle(self) -> None:
        # Step 1 — Topic selection
        topic_item = self._kb.pop_next_topic()
        if topic_item is None:
            self._log("Queue empty — generating new topics from knowledge gaps...", "gap")
            # Re-queue stale important topics before generating new ones
            self._requeue_stale_topics()
            self._generate_gap_topics()
            topic_item = self._kb.pop_next_topic()
            if topic_item is None:
                self._log("Still no topics after gap generation. Sleeping 30s...", "warn")
                time.sleep(30)
                return

        topic = topic_item["topic"]
        self._current_topic = topic
        self._log(f"Researching: [bold]{topic}[/bold]", "info")

        # Step 2 — Web search (skip already-visited URLs)
        visited = self._kb.get_visited_urls()
        page_texts, source_urls = self._searcher.research_topic(topic, visited_urls=visited)
        fresh_count = sum(1 for u in source_urls if u not in visited)
        total_chars = sum(len(t) for t in page_texts)
        self._log(
            f"Fetched {len(page_texts)} pages ({total_chars:,} chars)"
            + (f"  [dim]{fresh_count} fresh URL(s)[/dim]" if source_urls else ""),
            "search",
        )
        for url in source_urls:
            self._kb.mark_url_visited(url)

        if not page_texts:
            self._log(f"No web content for '{topic}' — re-queuing", "skip")
            self._kb.add_to_queue(topic, priority=4, source_topic=topic_item.get("source_topic", ""))
            self._kb.save()
            time.sleep(config.SLEEP_BETWEEN_CYCLES)
            return

        # Step 3 — Knowledge extraction
        existing = self._kb.get_topic(topic)
        existing_summary = existing.get("summary", "") if existing else ""

        extracted = None
        try:
            extracted = self._groq.extract_knowledge(topic, existing_summary, page_texts, source_urls)
        except Exception as e:
            self._log(f"Groq extraction failed for '{topic}': {e}. Retrying in 60s...", "error")
            time.sleep(60)
            try:
                extracted = self._groq.extract_knowledge(topic, existing_summary, page_texts, source_urls)
            except Exception as e2:
                self._log(f"Retry failed for '{topic}': {e2}. Skipping.", "error")
                return

        if extracted is None:
            self._log(f"JSON parse failed for '{topic}' — skipping", "skip")
            return

        raw_facts    = extracted.get("facts", [])
        new_conflicts = extracted.get("conflicts", [])
        follow_ups   = extracted.get("follow_up_topics", [])

        # Alert on conflicts immediately
        if new_conflicts:
            for c in new_conflicts:
                note = c.get("note", "conflicting sources")
                self._log(f"Conflict: {note}", "warn")

        # Step 4 — Cross-reference (skip if KB is too small to be meaningful)
        cross_ref = None
        if self._kb.topic_count() >= config.CROSS_REF_MIN_TOPICS:
            all_summaries = self._kb.get_all_summaries()
            try:
                cross_ref = self._groq.cross_reference(topic, raw_facts, all_summaries)
            except Exception as e:
                self._log(f"Cross-reference call failed: {e}", "warn")
        else:
            self._log(
                f"[dim]Skipping cross-ref (KB has {self._kb.topic_count()} topics, "
                f"threshold {config.CROSS_REF_MIN_TOPICS})[/dim]",
                "save",
            )

        # Step 5 — Knowledge base update
        new_facts_added, conflicts_stored = self._kb.update_topic(topic, extracted)
        if cross_ref:
            self._kb.update_related_topics(cross_ref)
            for gap in cross_ref.get("knowledge_gaps", [])[:config.MAX_TOPICS_PER_CYCLE]:
                self._kb.add_to_queue(gap, priority=2, source_topic=topic)

        # Add follow-up topics
        for ft in follow_ups[:config.MAX_TOPICS_PER_CYCLE]:
            priority = 1 if any(f.get("confidence") == "high" for f in raw_facts) else 2
            self._kb.add_to_queue(ft, priority=priority, source_topic=topic)

        # Dry-topic tracking: if 0 new facts added, increment counter
        if new_facts_added == 0 and len(raw_facts) == 0:
            dry_count = self._kb.increment_dry_count(topic)
            if dry_count >= config.DRY_TOPIC_THRESHOLD:
                self._log(
                    f"[dim]'{topic}' marked dry after {dry_count} barren cycles[/dim]",
                    "skip",
                )
        else:
            self._kb.reset_dry_count(topic)

        self._kb.mark_completed(topic)

        try:
            self._kb.save()
        except Exception as e:
            self._log(f"Failed to save knowledge base: {e}. Continuing in memory.", "error")

        # Log with net-new fact delta + conflict count
        stats = self._kb.get_stats()
        fact_delta = f"[green]+{new_facts_added}[/green]" if new_facts_added > 0 else "[dim]+0[/dim]"
        conf_note  = f"  [yellow]{conflicts_stored} conflict(s)[/yellow]" if conflicts_stored else ""
        self._log(
            f"[dim]done={stats['topics_researched']}  "
            f"queue={stats['queue_size']}  "
            f"facts={stats['total_facts']}  {fact_delta} new[/dim]{conf_note}",
            "save",
        )

        # Adaptive sleep: slow down if research is coming up dry repeatedly
        if new_facts_added == 0:
            self._consecutive_barren += 1
        else:
            self._consecutive_barren = 0

        sleep_time = config.SLEEP_BETWEEN_CYCLES
        if self._consecutive_barren >= config.ADAPTIVE_SLEEP_THRESHOLD:
            sleep_time = min(sleep_time * 2, 60)
            self._log(
                f"[dim]{self._consecutive_barren} barren cycles — sleeping {sleep_time}s[/dim]",
                "rate",
            )

        if not self._stop_event.is_set():
            time.sleep(sleep_time)

    # ------------------------------------------------------------------
    # Gap topic generation (item 15: larger batch)
    # ------------------------------------------------------------------

    def _generate_gap_topics(self) -> None:
        all_summaries = self._kb.get_all_summaries()
        seed = self._kb.get_seed_topic()
        try:
            topics = self._groq.generate_gap_topics(all_summaries, seed_topic=seed)
        except Exception as e:
            self._log(f"Gap topic generation failed: {e}", "warn")
            return
        for t in topics:
            self._kb.add_to_queue(t, priority=3, source_topic="gap_analysis")
        if topics:
            self._log(f"Generated {len(topics)} gap topics", "gap")

    # ------------------------------------------------------------------
    # Re-research stale topics (item 7)
    # ------------------------------------------------------------------

    def _requeue_stale_topics(self) -> None:
        stale = self._kb.get_stale_topics_for_requeue()
        for key in stale[:5]:   # cap to avoid flooding queue on first run
            self._kb.add_to_queue(key, priority=3, source_topic="reresearch")
        if stale:
            self._log(f"Re-queued {min(len(stale), 5)} stale topic(s) for refresh", "gap")

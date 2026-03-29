#!/usr/bin/env python3
"""
Autonomous Research Agent — entry point.

Usage:
    python agent.py

On first run you will be prompted for a seed topic.
On subsequent runs the agent resumes from the saved knowledge base.
Type 'quit' at any time to save and exit cleanly.
"""

import sys
import threading

import config
from groq_client import GroqClient
from knowledge_base import KnowledgeBase
from researcher import ResearchLoop
from web_search import WebSearcher


def _print_banner(resuming: bool, stats: dict) -> None:
    print("=" * 60)
    print("  Autonomous Research Agent")
    print("=" * 60)
    if resuming:
        print(
            f"  Resuming research on: {stats['seed_topic']}\n"
            f"  Topics completed : {stats['topics_researched']}\n"
            f"  Queue size       : {stats['queue_size']}\n"
            f"  Facts collected  : {stats['total_facts']}"
        )
    else:
        print(f"  Starting fresh research on: {stats['seed_topic']}")
    print("=" * 60)
    print("  Type 'quit' to save and exit cleanly.")
    print("=" * 60)
    print()


def _quit_listener(loop: ResearchLoop, research_thread: threading.Thread) -> None:
    """Runs in main thread — waits for user to type 'quit'."""
    while True:
        try:
            line = input()
        except EOFError:
            # stdin closed (e.g. non-interactive); just block until research done
            research_thread.join()
            return
        if line.strip().lower() == "quit":
            print("\n[INFO] Quit received — finishing current cycle then shutting down...")
            loop.signal_stop()
            research_thread.join()
            return


def main() -> None:
    kb = KnowledgeBase()
    resuming = kb.load()

    if not resuming:
        # Fresh start — prompt for seed topic
        print("No existing knowledge base found.")
        try:
            seed = input("Enter a seed topic to research: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nNo seed topic provided. Exiting.")
            sys.exit(0)
        if not seed:
            print("Seed topic cannot be empty. Exiting.")
            sys.exit(1)
        kb.init_fresh(seed)

    stats = kb.get_stats()
    _print_banner(resuming, stats)

    searcher = WebSearcher()
    groq = GroqClient()
    loop = ResearchLoop(kb, searcher, groq)

    research_thread = threading.Thread(target=loop.run, name="research-loop", daemon=True)
    research_thread.start()

    try:
        _quit_listener(loop, research_thread)
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted — finishing current cycle then shutting down...")
        loop.signal_stop()
        research_thread.join()

    # Final save and stats
    kb.save()
    final_stats = kb.get_stats()
    print()
    print("=" * 60)
    print("  Research session complete.")
    print(f"  Topics researched : {final_stats['topics_researched']}")
    print(f"  Facts collected   : {final_stats['total_facts']}")
    print(f"  Queue remaining   : {final_stats['queue_size']}")
    print(f"  Knowledge saved to: {config.KNOWLEDGE_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()

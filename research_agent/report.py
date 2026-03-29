#!/usr/bin/env python3
"""
report.py — Human-readable dump of the research knowledge base.

Usage:
    python report.py              # full report
    python report.py --topic foo  # single topic
    python report.py --queue      # show pending queue
    python report.py --facts      # facts only, all topics
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import config

CONF_ICONS = {"high": "[H]", "medium": "[M]", "low": "[L]"}
SEP = "-" * 60
WIDE = "=" * 60


def load() -> dict:
    if not os.path.exists(config.KNOWLEDGE_FILE):
        print(f"No knowledge base found at {config.KNOWLEDGE_FILE}")
        sys.exit(1)
    with open(config.KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def print_meta(data: dict) -> None:
    meta = data.get("meta", {})
    print(WIDE)
    print(f"  Research Knowledge Base")
    print(f"  Seed topic : {meta.get('seed_topic', 'unknown')}")
    print(f"  Created    : {meta.get('created', '?')[:19].replace('T', ' ')}")
    print(f"  Updated    : {meta.get('last_updated', '?')[:19].replace('T', ' ')}")
    print(f"  Topics done: {meta.get('topics_researched', 0)}")
    print(f"  Total facts: {meta.get('total_facts', 0)}")
    print(f"  Queue size : {len(data.get('research_queue', []))}")
    print(WIDE)
    print()


def print_topic(key: str, entry: dict) -> None:
    print(WIDE)
    print(f"  TOPIC: {key.upper()}")
    print(WIDE)

    summary = entry.get("summary", "").strip()
    if summary:
        print(f"\nSummary:\n  {summary}\n")

    facts = entry.get("facts", [])
    if facts:
        print(f"Facts ({len(facts)}):")
        for f in facts:
            conf = CONF_ICONS.get(f.get("confidence", ""), "[?]")
            print(f"  {conf} {f.get('content', '')}")
            url = f.get("source_url", "")
            if url:
                print(f"       {url}")
        print()

    conflicts = entry.get("conflicts", [])
    if conflicts:
        print(f"Conflicts ({len(conflicts)}):")
        for c in conflicts:
            print(f"  ! {c.get('note', '')}")
            print(f"    A: {c.get('fact_a', '')}")
            print(f"    B: {c.get('fact_b', '')}")
        print()

    related = entry.get("related_topics", [])
    if related:
        print(f"Related topics:")
        for r in related:
            print(f"  -> {r}")
        print()

    print(
        f"  [Researched {entry.get('research_count', 0)}x | "
        f"Last: {entry.get('last_researched', '?')[:19].replace('T', ' ')}]"
    )
    print()


def print_queue(data: dict) -> None:
    queue = data.get("research_queue", [])
    print(WIDE)
    print(f"  RESEARCH QUEUE ({len(queue)} topics)")
    print(WIDE)
    if not queue:
        print("  (empty)")
    for i, item in enumerate(queue, 1):
        print(f"  {i:>3}. [P{item.get('priority', '?')}] {item.get('topic', '')}")
        src = item.get("source_topic", "")
        if src:
            print(f"        from: {src}")
    print()


def print_facts_only(data: dict) -> None:
    knowledge = data.get("knowledge", {})
    print(WIDE)
    print("  ALL FACTS")
    print(WIDE)
    for key, entry in sorted(knowledge.items()):
        facts = entry.get("facts", [])
        if not facts:
            continue
        print(f"\n{SEP}")
        print(f"  {key.upper()}")
        print(SEP)
        for f in facts:
            conf = CONF_ICONS.get(f.get("confidence", ""), "[?]")
            print(f"  {conf} {f.get('content', '')}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Research knowledge base report")
    parser.add_argument("--topic", metavar="NAME", help="Show a single topic")
    parser.add_argument("--queue", action="store_true", help="Show the research queue")
    parser.add_argument("--facts", action="store_true", help="Show all facts only")
    args = parser.parse_args()

    data = load()
    print_meta(data)

    if args.queue:
        print_queue(data)
        return

    if args.facts:
        print_facts_only(data)
        return

    knowledge = data.get("knowledge", {})

    if args.topic:
        key = args.topic.strip().lower()
        entry = knowledge.get(key)
        if not entry:
            # fuzzy match
            matches = [k for k in knowledge if args.topic.lower() in k]
            if len(matches) == 1:
                entry = knowledge[matches[0]]
                key = matches[0]
            elif matches:
                print(f"Multiple matches: {matches}")
                return
            else:
                print(f"Topic '{args.topic}' not found in knowledge base.")
                print(f"Known topics: {sorted(knowledge.keys())}")
                return
        print_topic(key, entry)
        return

    # Full report
    if not knowledge:
        print("Knowledge base is empty — nothing to report yet.")
        return

    for key in sorted(knowledge.keys()):
        print_topic(key, knowledge[key])

    print_queue(data)


if __name__ == "__main__":
    main()

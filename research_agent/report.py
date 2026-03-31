#!/usr/bin/env python3
"""
report.py — Rich-formatted knowledge base report.

Usage (CLI):
    python report.py              # full report for auto-selected session
    python report.py --topic foo  # single topic
    python report.py --queue      # pending research queue
    python report.py --facts      # facts only, all topics
    python report.py --sessions   # list sessions and exit
    python report.py --session SLUG

Usage (from agent.py command loop):
    import report
    report.generate(kb)           # prints inline without loading from disk
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import config
import sessions as session_manager

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)

# Confidence display
_CONF_STYLE = {
    "high":   ("H", "bold green"),
    "medium": ("M", "yellow"),
    "low":    ("L", "dim red"),
}

# Model rank labels — derived dynamically from the model catalog at render time
def _rank_label_for(rank: int) -> tuple[str, str]:
    """Return (label, style) for a model rank, pulling short name from catalog."""
    try:
        import config
        m = next((m for m in config.MODEL_CATALOG if m["rank"] == rank), None)
        stars = "★" * max(0, 5 - rank + 1) + "☆" * max(0, rank - 1)
        label = f"{m['short'][:8]:<8} {stars}" if m else f"Rank {rank}   {stars}"
    except Exception:
        label = f"Rank {rank}"
    styles = {1: "bold cyan", 2: "cyan", 3: "blue", 4: "dim blue", 5: "dim"}
    return label, styles.get(rank, "dim")


def _conf_badge(conf: str) -> Text:
    letter, style = _CONF_STYLE.get(conf, ("?", "dim"))
    t = Text()
    t.append(f" {letter} ", style=f"bold {style}")
    return t


def _rank_badge(rank: int | None) -> Text:
    if rank is None:
        return Text("")
    label, style = _rank_label_for(rank)
    return Text(label, style=style)


def _fmt_dt(iso: str) -> str:
    return iso[:19].replace("T", " ") if iso else "?"


# ── Section renderers ──────────────────────────────────────────────────────────

def render_header(data: dict) -> None:
    meta = data.get("meta", {})
    t = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2))
    t.add_column(style="bold cyan", width=14)
    t.add_column()
    t.add_row("Seed topic",   meta.get("seed_topic", "unknown"))
    t.add_row("Created",      _fmt_dt(meta.get("created", "")))
    t.add_row("Last updated", _fmt_dt(meta.get("last_updated", "")))
    t.add_row("Topics done",  str(meta.get("topics_researched", 0)))
    t.add_row("Total facts",  str(meta.get("total_facts", 0)))
    t.add_row("Queue size",   str(len(data.get("research_queue", []))))
    console.print()
    console.print(Panel(t, title="[bold cyan]Research Knowledge Base[/bold cyan]",
                        border_style="cyan", padding=(0, 1)))
    console.print()


def render_topic(key: str, entry: dict) -> None:
    lines: list = []

    # ── Summary ──────────────────────────────────────────────────────────────
    summary = entry.get("summary", "").strip()
    if summary:
        lines.append(Text("Summary", style="bold"))
        lines.append(Text(f"  {summary}", style="dim"))
        lines.append(Text(""))

    # ── Facts table ──────────────────────────────────────────────────────────
    facts = entry.get("facts", [])
    if facts:
        tbl = Table(
            box=box.SIMPLE,
            show_header=True,
            header_style="bold dim",
            pad_edge=False,
            padding=(0, 1),
        )
        tbl.add_column("Conf",   width=4, no_wrap=True)
        tbl.add_column("Model",  width=10, no_wrap=True)
        tbl.add_column("Fact",   ratio=1)
        tbl.add_column("Source", style="dim", max_width=40)

        for f in facts:
            conf = f.get("confidence", "")
            letter, style = _CONF_STYLE.get(conf, ("?", "dim"))
            conf_text = Text(f" {letter} ", style=f"bold {style}")

            rank = f.get("model_rank")
            rank_text = _rank_badge(rank)

            content = f.get("content", "")
            url = f.get("source_url", "")
            # Truncate very long URLs to keep the table readable
            short_url = url if len(url) <= 38 else url[:35] + "..."

            tbl.add_row(conf_text, rank_text, content, short_url)

        lines.append(Text(f"Facts ({len(facts)})", style="bold"))
        lines.append(tbl)
        lines.append(Text(""))

    # ── Conflicts ─────────────────────────────────────────────────────────────
    conflicts = entry.get("conflicts", [])
    if conflicts:
        lines.append(Text(f"Conflicts ({len(conflicts)})", style="bold yellow"))
        for c in conflicts:
            note = c.get("note", "")
            lines.append(Text(f"  ⚠  {note}", style="yellow"))
            lines.append(Text(f"     A: {c.get('fact_a', '')}", style="dim"))
            lines.append(Text(f"     B: {c.get('fact_b', '')}", style="dim"))
        lines.append(Text(""))

    # ── Related topics ────────────────────────────────────────────────────────
    related = entry.get("related_topics", [])
    if related:
        lines.append(Text("Related topics", style="bold"))
        for r in related:
            lines.append(Text(f"  → {r}", style="dim"))
        lines.append(Text(""))

    # ── Footer ────────────────────────────────────────────────────────────────
    rc = entry.get("research_count", 0)
    last = _fmt_dt(entry.get("last_researched", ""))
    smr = entry.get("summary_model_rank")
    rank_note = f"  summary by rank-{smr} model" if smr and smr < 999 else ""
    lines.append(Text(
        f"Researched {rc}x  │  last {last}{rank_note}",
        style="dim"
    ))

    # Build the panel content from mixed Text/Table objects
    from rich.console import Group
    console.print(Panel(
        Group(*lines),
        title=f"[bold cyan]{key.upper()}[/bold cyan]",
        border_style="blue",
        padding=(0, 2),
    ))
    console.print()


def render_queue(data: dict) -> None:
    queue = data.get("research_queue", [])
    if not queue:
        console.print(Panel("[dim](empty)[/dim]",
                            title="[bold cyan]Research Queue[/bold cyan]",
                            border_style="cyan"))
        return

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim",
                pad_edge=False, padding=(0, 1))
    tbl.add_column("#",        width=4, style="bold")
    tbl.add_column("Pri",      width=4)
    tbl.add_column("Topic",    ratio=1)
    tbl.add_column("From",     style="dim", max_width=30)

    pri_style = {1: "bold green", 2: "green", 3: "yellow", 4: "dim", 5: "dim red"}
    for i, item in enumerate(queue, 1):
        p = item.get("priority", 2)
        tbl.add_row(
            str(i),
            Text(str(p), style=pri_style.get(p, "dim")),
            item.get("topic", ""),
            item.get("source_topic", ""),
        )

    console.print(Panel(tbl, title=f"[bold cyan]Research Queue ({len(queue)} topics)[/bold cyan]",
                        border_style="cyan", padding=(0, 1)))
    console.print()


def render_facts_only(data: dict) -> None:
    """Compact all-facts view: one table per topic."""
    knowledge = data.get("knowledge", {})
    total = 0

    for key in sorted(knowledge.keys()):
        entry = knowledge[key]
        facts = entry.get("facts", [])
        if not facts:
            continue
        total += len(facts)

        tbl = Table(box=box.SIMPLE, show_header=False, pad_edge=False, padding=(0, 1))
        tbl.add_column("Conf",  width=4, no_wrap=True)
        tbl.add_column("Model", width=10, no_wrap=True)
        tbl.add_column("Fact",  ratio=1)

        for f in facts:
            conf = f.get("confidence", "")
            letter, style = _CONF_STYLE.get(conf, ("?", "dim"))
            conf_text = Text(f" {letter} ", style=f"bold {style}")
            tbl.add_row(conf_text, _rank_badge(f.get("model_rank")), f.get("content", ""))

        console.print(Panel(tbl, title=f"[bold]{key.upper()}[/bold]",
                            border_style="dim", padding=(0, 1)))

    console.print(f"[dim]  {total} facts across {len(knowledge)} topics[/dim]\n")


# ── Entry points ───────────────────────────────────────────────────────────────

def render_synthesis(text: str) -> None:
    """Render a synthesized report string as Rich markdown in a panel."""
    from rich.markdown import Markdown
    console.print()
    console.print(Panel(
        Markdown(text),
        title="[bold cyan]Synthesized Report[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()


def generate(kb) -> None:
    """
    Generate a full report from a live KnowledgeBase object (used by agent.py).
    Reads data directly from the kb instance rather than loading from disk.
    """
    data = kb._data  # access internal dict directly
    render_header(data)
    knowledge = data.get("knowledge", {})
    if not knowledge:
        console.print("[dim]  Knowledge base is empty — nothing to report yet.[/dim]\n")
        return
    for key in sorted(knowledge.keys()):
        render_topic(key, knowledge[key])
    render_queue(data)


def export_obsidian(kb, output_dir: str) -> int:
    """
    Export the knowledge base as an Obsidian vault — one .md file per topic
    with [[wikilinks]] between related topics. Returns count of files written.
    """
    os.makedirs(output_dir, exist_ok=True)
    data = kb._data
    knowledge = data.get("knowledge", {})
    if not knowledge:
        console.print("[dim]  Nothing to export.[/dim]")
        return 0

    # Index page
    meta = data.get("meta", {})
    index_lines = [
        f"# {meta.get('seed_topic', 'Research Notes')}",
        "",
        f"**Topics researched:** {meta.get('topics_researched', 0)}  ",
        f"**Total facts:** {meta.get('total_facts', 0)}  ",
        f"**Last updated:** {meta.get('last_updated', '')[:19]}",
        "",
        "## Topics",
        "",
    ]
    for key in sorted(knowledge.keys()):
        facts_count = len(knowledge[key].get("facts", []))
        index_lines.append(f"- [[{key}]] ({facts_count} facts)")

    with open(os.path.join(output_dir, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines))

    count = 1
    for key, entry in knowledge.items():
        lines = [f"# {key}", ""]
        summary = entry.get("summary", "").strip()
        if summary:
            lines += [f"> {summary}", ""]

        facts = entry.get("facts", [])
        if facts:
            lines += ["## Facts", ""]
            for fact in facts:
                conf = fact.get("confidence", "low")[0].upper()
                sc = fact.get("source_count", 1)
                src = fact.get("source_url", "")
                src_tag = f" ([source]({src}))" if src else ""
                verified = " ✅" if fact.get("hardware_verified") else ""
                lines.append(f"- `[{conf}]`{'×'+str(sc) if sc>1 else ''}{verified} {fact.get('content','')}{src_tag}")
            lines.append("")

        conflicts = entry.get("conflicts", [])
        if conflicts:
            lines += ["## Conflicts", ""]
            for c in conflicts:
                lines.append(f"- ⚠ {c.get('note', '')}")
            lines.append("")

        related = entry.get("related_topics", [])
        if related:
            lines += ["## Related", ""]
            for r in related:
                lines.append(f"- [[{r}]]")
            lines.append("")

        lines.append(f"*Researched {entry.get('research_count', 0)}× — last {entry.get('last_researched', '')[:10]}*")

        safe_name = re.sub(r'[\\/:*?"<>|]', "_", key)
        with open(os.path.join(output_dir, f"{safe_name}.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        count += 1

    console.print(f"[green]Obsidian vault exported:[/green] {output_dir}  ({count} files)")
    return count


def export_csv(kb, output_path: str) -> int:
    """
    Export all facts as a CSV file with columns:
    topic, fact, confidence, source_count, source_url, hardware_verified, needs_verification
    Returns count of rows written.
    """
    import csv
    data = kb._data
    knowledge = data.get("knowledge", {})
    rows = []
    for key, entry in knowledge.items():
        for fact in entry.get("facts", []):
            rows.append({
                "topic":               key,
                "fact":                fact.get("content", ""),
                "confidence":          fact.get("confidence", ""),
                "source_count":        fact.get("source_count", 1),
                "source_url":          fact.get("source_url", ""),
                "source_quality":      fact.get("source_quality", ""),
                "hardware_verified":   fact.get("hardware_verified", False),
                "needs_verification":  fact.get("needs_verification", False),
                "model_rank":          fact.get("model_rank", ""),
            })

    if not rows:
        console.print("[dim]  No facts to export.[/dim]")
        return 0

    fieldnames = list(rows[0].keys())
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    console.print(f"[green]CSV exported:[/green] {output_path}  ({len(rows)} facts)")
    return len(rows)


def generate_auto_digest(kb, groq_client, output_path: str | None = None) -> str:
    """
    Generate and save an auto-digest (timestamped synthesis report).
    If output_path is None, saves to the session dir as digest_YYYYMMDD_HHMMSS.md.
    Returns the path written.
    """
    from datetime import datetime, timezone
    data = kb._data
    seed_topic = data.get("meta", {}).get("seed_topic", "")
    if not data.get("knowledge"):
        return ""

    console.print("[dim]  Generating auto-digest...[/dim]")
    text = groq_client.generate_synthesis_report(data, seed_topic, config.RESEARCH_FOCUS)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if output_path is None:
        session_dir = os.path.dirname(config.KNOWLEDGE_FILE)
        output_path = os.path.join(session_dir, f"digest_{ts}.md")

    header = (
        f"# Auto-Digest — {seed_topic}\n"
        f"*Generated: {ts}  |  "
        f"Topics: {data['meta'].get('topics_researched',0)}  |  "
        f"Facts: {data['meta'].get('total_facts',0)}*\n\n"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + text)

    console.print(f"[green]Digest saved:[/green] {output_path}")
    return output_path


def generate_synthesized(kb, groq_client) -> None:
    """
    Use the best available Groq model to synthesize all KB facts into a readable
    report. Used by the 'synthesize' command in agent.py.
    """
    import config
    data = kb._data
    seed_topic = data.get("meta", {}).get("seed_topic", "")
    knowledge = data.get("knowledge", {})
    if not knowledge:
        console.print("[dim]  Knowledge base is empty — nothing to synthesize yet.[/dim]\n")
        return
    console.print("[dim]  Calling model to synthesize report...[/dim]")
    text = groq_client.generate_synthesis_report(data, seed_topic, config.RESEARCH_FOCUS)
    render_synthesis(text)


def _load() -> dict:
    if not os.path.exists(config.KNOWLEDGE_FILE):
        console.print(f"[red]No knowledge base found at {config.KNOWLEDGE_FILE}[/red]")
        sys.exit(1)
    with open(config.KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _pick_session(slug: str | None) -> None:
    existing = session_manager.list_sessions()
    if not existing:
        return  # fall back to legacy data/ dir

    if slug:
        match = next((s for s in existing if s["slug"] == slug or s["name"] == slug), None)
        if not match:
            console.print(f"[red]Session '{slug}' not found.[/red]")
            console.print("Available sessions:")
            for s in existing:
                console.print(f"  [cyan]{s['slug']}[/cyan]  {s['name']}")
            sys.exit(1)
        session_manager.activate_session(match)
        return

    if len(existing) == 1:
        session_manager.activate_session(existing[0])
        return

    console.print("[yellow]Multiple sessions found. Use --session SLUG to select one.[/yellow]\n")
    from rich.table import Table as _T
    t = _T(box=box.SIMPLE, show_header=True, header_style="bold cyan", pad_edge=False)
    t.add_column("Slug", style="cyan")
    t.add_column("Name")
    t.add_column("Topics", width=7)
    t.add_column("Facts",  width=7)
    t.add_column("Last updated", width=12)
    for s in existing:
        t.add_row(s["slug"], s["name"], str(s["topics_done"]),
                  str(s["total_facts"]), s["last_updated"][:10] if s["last_updated"] else "?")
    console.print(t)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Research knowledge base report")
    parser.add_argument("--session",   metavar="SLUG", help="Session slug (omit to auto-select)")
    parser.add_argument("--sessions",  action="store_true", help="List all sessions and exit")
    parser.add_argument("--topic",     metavar="NAME", help="Show a single topic")
    parser.add_argument("--queue",     action="store_true", help="Show the research queue")
    parser.add_argument("--facts",     action="store_true", help="Show all facts (compact)")
    parser.add_argument("--synthesize", action="store_true",
                        help="Use Groq to generate a readable synthesized report")
    args = parser.parse_args()

    if args.sessions:
        existing = session_manager.list_sessions()
        if not existing:
            console.print("[dim]No sessions found.[/dim]")
            return
        session_manager.print_session_list(existing)
        return

    _pick_session(args.session)
    data = _load()
    render_header(data)

    if args.synthesize:
        from groq_client import GroqClient
        groq = GroqClient()
        seed_topic = data.get("meta", {}).get("seed_topic", "")
        console.print("[dim]  Calling model to synthesize report...[/dim]")
        text = groq.generate_synthesis_report(data, seed_topic, config.RESEARCH_FOCUS)
        render_synthesis(text)
        return

    if args.queue:
        render_queue(data)
        return

    if args.facts:
        render_facts_only(data)
        return

    knowledge = data.get("knowledge", {})

    if args.topic:
        key = args.topic.strip().lower()
        entry = knowledge.get(key)
        if not entry:
            matches = [k for k in knowledge if args.topic.lower() in k]
            if len(matches) == 1:
                key, entry = matches[0], knowledge[matches[0]]
            elif matches:
                console.print(f"[yellow]Multiple matches:[/yellow] {matches}")
                return
            else:
                console.print(f"[red]Topic '{args.topic}' not found.[/red]")
                console.print(f"Known topics: {sorted(knowledge.keys())}")
                return
        render_topic(key, entry)
        return

    # Full report
    if not knowledge:
        console.print("[dim]Knowledge base is empty — nothing to report yet.[/dim]")
        return

    for key in sorted(knowledge.keys()):
        render_topic(key, knowledge[key])
    render_queue(data)


if __name__ == "__main__":
    main()

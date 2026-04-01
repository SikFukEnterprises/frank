#!/usr/bin/env python3
"""
Autonomous Research Agent — entry point.

Usage:
    python agent.py
"""

import json
import os
import sys
import threading

import config
import ui
from groq_client import GroqClient
from knowledge_base import KnowledgeBase
from researcher import ResearchLoop
import sessions as session_manager
from web_search import WebSearcher


# ── Export / Import helpers ────────────────────────────────────────────────────

def _export_session(kb: KnowledgeBase, session: dict) -> None:
    """Export all facts to JSON and Markdown files in the session directory."""
    data = kb._data
    knowledge = data.get("knowledge", {})
    session_dir = session["dir"]

    # ── JSON export ──────────────────────────────────────────────────────
    export_json = os.path.join(session_dir, "export.json")
    payload = {
        "session": session["name"],
        "seed_topic": data.get("meta", {}).get("seed_topic", ""),
        "exported_at": __import__("datetime").datetime.utcnow().isoformat(),
        "topics": {},
    }
    for key, entry in knowledge.items():
        payload["topics"][key] = {
            "summary": entry.get("summary", ""),
            "facts": [
                {
                    "content":       f.get("content", ""),
                    "confidence":    f.get("confidence", ""),
                    "source_count":  f.get("source_count", 1),
                    "source_url":    f.get("source_url", ""),
                }
                for f in entry.get("facts", [])
            ],
            "conflicts": entry.get("conflicts", []),
        }
    with open(export_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    # ── Markdown export ──────────────────────────────────────────────────
    export_md = os.path.join(session_dir, "export.md")
    lines = [
        f"# {session['name']}",
        f"**Seed topic:** {payload['seed_topic']}  ",
        f"**Exported:** {payload['exported_at']}",
        "",
    ]
    for key, entry in sorted(knowledge.items()):
        facts = entry.get("facts", [])
        if not facts:
            continue
        lines.append(f"## {key}")
        if entry.get("summary"):
            lines.append(f"*{entry['summary']}*")
            lines.append("")
        for f in facts:
            conf = f.get("confidence", "?")[0].upper()
            sc = f.get("source_count", 1)
            src_tag = f" (×{sc})" if sc > 1 else ""
            lines.append(f"- [{conf}]{src_tag} {f.get('content', '')}")
        for c in entry.get("conflicts", []):
            lines.append(f"- ⚠ **Conflict:** {c.get('note', '')}")
        lines.append("")

    with open(export_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    ui.log("ok", f"Exported → [cyan]{export_json}[/cyan]")
    ui.log("ok", f"Exported → [cyan]{export_md}[/cyan]")


def _import_facts(kb: KnowledgeBase, path: str) -> None:
    """Import facts from a previously exported JSON file into the live KB."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        ui.log("error", f"Could not read import file: {e}")
        return

    topics = payload.get("topics", {})
    imported = 0
    for key, entry in topics.items():
        for fact in entry.get("facts", []):
            content = fact.get("content", "").strip()
            if not content:
                continue
            # Inject as a minimal extracted dict so update_topic handles it
            synthetic = {
                "_model_rank": 4,   # treat as lowest rank (weakest claim)
                "summary": "",
                "facts": [fact],
                "conflicts": [],
                "follow_up_topics": [],
                "related_topics": [],
            }
            kb.update_topic(key, synthetic)
            imported += 1

    kb.save()
    ui.log("ok", f"Imported {imported} facts from [cyan]{path}[/cyan]")


# ── Research command loop ──────────────────────────────────────────────────────

def _help_str() -> str:
    n = len(config.MODEL_CATALOG)
    return (
        "[dim]Commands:[/dim]\n"
        "  [bold cyan]Research:[/bold cyan]  "
        "[cyan]add: <topic>[/cyan]  [cyan]remove: <topic>[/cyan]  "
        "[cyan]pause[/cyan] / [cyan]resume[/cyan]  "
        f"[cyan]speed <tier>[/cyan]  [cyan]model <1-{n}>[/cyan]  "
        "[cyan]lanes <1-3>[/cyan]\n"
        "  [bold cyan]Reports:[/bold cyan]   "
        "[cyan]queue[/cyan]  [cyan]status[/cyan]  [cyan]report[/cyan]  "
        "[cyan]synthesize[/cyan]  [cyan]tree[/cyan]  [cyan]diff[/cyan]\n"
        "  [bold cyan]Knowledge:[/bold cyan] "
        "[cyan]objectives[/cyan]  [cyan]hypothesis add <text>[/cyan]  "
        "[cyan]hypothesis list[/cyan]  [cyan]similar <query>[/cyan]\n"
        "  [bold cyan]Export:[/bold cyan]    "
        "[cyan]export[/cyan]  [cyan]obsidian[/cyan]  [cyan]csv[/cyan]  "
        "[cyan]import <path>[/cyan]  [cyan]romraider[/cyan]\n"
        "  [bold cyan]Nav:[/bold cyan]       "
        "[cyan]plan[/cyan]  [cyan]webui[/cyan]  [cyan]menu[/cyan]  [cyan]quit[/cyan]"
    )


def _command_loop(loop: ResearchLoop, kb: KnowledgeBase, groq: GroqClient,
                  session: dict, display: ui.LiveDisplay,
                  research_thread: threading.Thread,
                  extra_loops: list | None = None) -> str:
    """
    Main thread — handles commands while research runs in background.
    Returns 'menu' or 'quit'.
    """
    ui.console.print(_help_str() + "\n")

    cmd_input = ui.create_command_prompt(display)
    cmd_input.start()

    try:
        return _command_loop_inner(loop, kb, groq, session, display,
                                  research_thread, extra_loops, cmd_input)
    finally:
        cmd_input.stop()


def _command_loop_inner(loop: ResearchLoop, kb: KnowledgeBase, groq: GroqClient,
                        session: dict, display: ui.LiveDisplay,
                        research_thread: threading.Thread,
                        extra_loops: list | None,
                        cmd_input) -> str:
    """Inner command loop — separated so the outer function manages prompt lifecycle."""
    while True:
        try:
            raw = cmd_input.prompt()
        except KeyboardInterrupt:
            continue  # Ctrl-C clears the input line
        except EOFError:
            loop.signal_stop()
            for el in (extra_loops or []):
                el.signal_stop()
            research_thread.join()
            display.stop()
            return "quit"

        cmd = raw.lower()

        # ── Navigation ────────────────────────────────────────────────────
        if cmd == "quit":
            display.stop()
            ui.log("info", "Shutting down after current cycle...")
            loop.signal_stop()
            for el in (extra_loops or []):
                el.signal_stop()
            research_thread.join()
            return "quit"

        elif cmd == "menu":
            display.stop()
            ui.log("info", "Pausing after current cycle — returning to menu...")
            loop.signal_stop()
            for el in (extra_loops or []):
                el.signal_stop()
            research_thread.join()
            return "menu"

        # ── Queue manipulation ────────────────────────────────────────────
        elif cmd.startswith("add:"):
            topic = raw[4:].strip()
            if topic:
                kb.add_to_queue(topic, priority=1, source_topic="manual")
                kb.save()
                ui.log("ok", f"Queued: '[bold]{topic}[/bold]' (priority 1)")
                display.update(queue=kb.queue_size())
            else:
                ui.console.print("  Usage: [cyan]add: <topic>[/cyan]")

        elif cmd.startswith("remove:"):
            topic = raw[7:].strip()
            if topic:
                removed = kb.remove_from_queue(topic)
                if removed:
                    kb.save()
                    ui.log("ok", f"Removed '[bold]{topic}[/bold]' from queue")
                    display.update(queue=kb.queue_size())
                else:
                    ui.log("warn", f"'{topic}' not found in queue")
            else:
                ui.console.print("  Usage: [cyan]remove: <topic>[/cyan]")

        # ── Pause / Resume ────────────────────────────────────────────────
        elif cmd == "pause":
            loop.pause()
            for el in (extra_loops or []):
                el.pause()
            display.update(paused=True)

        elif cmd == "resume":
            loop.resume()
            for el in (extra_loops or []):
                el.resume()
            display.update(paused=False)

        # ── Speed tier ────────────────────────────────────────────────────
        elif cmd.startswith("speed"):
            parts = cmd.split()
            tier_name = parts[1] if len(parts) > 1 else ""
            if tier_name in config.SPEED_TIERS:
                config.apply_speed_tier(tier_name)
                ui.log("ok", f"Speed tier → [cyan]{tier_name}[/cyan]")
                display.update(tier=tier_name)
            else:
                ui.console.print(f"  Valid tiers: {', '.join(config.SPEED_TIERS.keys())}")

        # ── Model switch ──────────────────────────────────────────────────
        elif cmd.startswith("model"):
            parts = cmd.split()
            try:
                idx = int(parts[1]) - 1
                if 0 <= idx < len(config.MODEL_CATALOG):
                    groq.set_preferred_model(idx)
                    name = config.MODEL_CATALOG[idx]["short"]
                    ui.log("ok", f"Model → [yellow]{name}[/yellow]")
                    display.update(model=name)
                else:
                    ui.console.print(f"  Valid range: 1-{len(config.MODEL_CATALOG)}")
            except (IndexError, ValueError):
                n = len(config.MODEL_CATALOG)
                ui.console.print(f"  Usage: [cyan]model <1-{n}>[/cyan]")

        # ── Lane count ────────────────────────────────────────────────────
        elif cmd.startswith("lanes"):
            parts = cmd.split()
            try:
                n = int(parts[1])
                if 1 <= n <= 3:
                    config.RESEARCH_LANES = n
                    ui.log("ok", f"Lane count → {n} (restarts on next session)")
                    display.update(lanes=n)
                else:
                    ui.console.print("  Valid: lanes 1, lanes 2, lanes 3")
            except (IndexError, ValueError):
                ui.console.print("  Usage: [cyan]lanes <1-3>[/cyan]")

        # ── Research plan ─────────────────────────────────────────────────
        elif cmd == "plan":
            ui.log("info", "Generating research plan from seed topic...")
            try:
                from researcher import seed_from_plan
                added = seed_from_plan(kb, groq)
                display.update(queue=kb.queue_size())
                if added == 0:
                    ui.log("warn", "No plan topics generated")
            except Exception as e:
                ui.log("error", f"Plan generation failed: {e}")

        # ── Reporting ─────────────────────────────────────────────────────
        elif cmd == "queue":
            import report as report_module
            report_module.render_queue(kb._data)

        elif cmd == "status":
            stats = kb.get_stats()
            ui.print_status(
                stats,
                token_stats=groq.get_usage_stats(),
                tier=config.CURRENT_SPEED_TIER,
                current_topic=loop.get_current_topic(),
                model_name=groq.get_active_model_name(),
            )

        elif cmd == "report":
            ui.log("info", "Generating report...")
            try:
                import report as report_module
                report_module.generate(kb)
            except Exception as e:
                ui.log("error", f"Report failed: {e}")

        elif cmd == "synthesize":
            ui.log("info", "Synthesizing report with best available model...")
            try:
                import report as report_module
                report_module.generate_synthesized(kb, groq)
            except Exception as e:
                ui.log("error", f"Synthesis failed: {e}")

        elif cmd == "tree":
            seed = kb.get_seed_topic()
            ui.print_topic_tree(kb._data, seed)

        elif cmd == "diff":
            snaps = kb.get_snapshots()
            if len(snaps) < 2:
                ui.console.print("  [dim]Not enough snapshots yet (need 2+). Snapshots are saved automatically.[/dim]")
            else:
                before = snaps[-2]["data"]
                after  = snaps[-1]["data"]
                d = kb.diff_snapshots(before, after)
                ui.console.print(f"  Added topics:   [green]{d['added_topics']}[/green]")
                ui.console.print(f"  Removed topics: [red]{d['removed_topics']}[/red]")
                ui.console.print(f"  Facts delta:    [cyan]+{d['total_facts_delta']}[/cyan]")
                for ch in d["changed_topics"][:10]:
                    ui.console.print(
                        f"  [dim]{ch['topic']}[/dim]  "
                        f"facts [cyan]{ch['facts_delta']:+d}[/cyan]  "
                        f"conf {ch['confidence_delta']:+.2f}"
                    )

        elif cmd == "objectives":
            kb.update_objective_scores()
            ui.print_objectives(kb.get_objectives(), kb.get_objective_coverage())

        # ── Hypotheses ────────────────────────────────────────────────────
        elif cmd.startswith("hypothesis"):
            parts = raw.split(None, 2)
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub == "add" and len(parts) > 2:
                idx = kb.add_hypothesis(parts[2])
                kb.save()
                ui.log("ok", f"Hypothesis #{idx+1} added: {parts[2][:60]}")
            elif sub == "list":
                ui.print_hypotheses(kb.get_hypotheses())
            else:
                ui.console.print("  Usage: [cyan]hypothesis add <text>[/cyan]  or  [cyan]hypothesis list[/cyan]")

        elif cmd.startswith("similar"):
            query = raw[7:].strip()
            if query:
                results = kb.get_similar_facts(query)
                if results:
                    for r in results:
                        ui.console.print(
                            f"  [dim]{r['similarity']:.2f}[/dim] "
                            f"[cyan]{r['topic']}[/cyan]: {r['content'][:100]}"
                        )
                else:
                    ui.console.print("  [dim]No results (embeddings disabled or no facts yet)[/dim]")
            else:
                ui.console.print("  Usage: [cyan]similar <query text>[/cyan]")

        # ── Export / Import ───────────────────────────────────────────────
        elif cmd == "export":
            _export_session(kb, session)

        elif cmd == "obsidian":
            import report as report_module
            vault_dir = os.path.join(session["dir"], "obsidian_vault")
            report_module.export_obsidian(kb, vault_dir)

        elif cmd == "csv":
            import report as report_module
            csv_path = os.path.join(session["dir"], "facts.csv")
            report_module.export_csv(kb, csv_path)

        elif cmd.startswith("import"):
            parts = raw.split(None, 1)
            if len(parts) < 2:
                ui.console.print("  Usage: [cyan]import <path/to/export.json>[/cyan]")
            else:
                _import_facts(kb, parts[1])
                display.update(facts=kb.get_stats()["total_facts"])

        # ── RomRaider ─────────────────────────────────────────────────────
        elif cmd == "romraider":
            try:
                import romraider
                params = romraider.fetch_and_parse_all()
                if params:
                    count = romraider.ingest_into_kb(params, kb)
                    kb.save()
                    ui.log("ok", f"RomRaider: ingested [bold]{count}[/bold] parameters as facts")
                    display.update(facts=kb.get_stats()["total_facts"])
                else:
                    ui.log("warn", "RomRaider: no parameters found")
            except ImportError:
                ui.log("error", "romraider.py not found")
            except Exception as e:
                ui.log("error", f"RomRaider ingestion failed: {e}")

        # ── Web UI ────────────────────────────────────────────────────────
        elif cmd == "webui":
            try:
                import subprocess
                port = config.WEB_DASHBOARD_PORT
                subprocess.Popen(
                    ["python", os.path.join(os.path.dirname(__file__), "web_app.py"),
                     "--port", str(port)],
                    start_new_session=True,
                )
                ui.log("ok", f"Web dashboard started at [cyan]http://localhost:{port}[/cyan]")
            except Exception as e:
                ui.log("error", f"Failed to start web UI: {e}")

        elif cmd in ("help", "?", "h"):
            ui.console.print(_help_str())

        elif cmd:
            ui.console.print(f"  [dim]Unknown command.[/dim]  Type [cyan]help[/cyan] for all options.")


# ── Priority queue seeding ─────────────────────────────────────────────────────

def _seed_priority_topics(kb: KnowledgeBase, focus: str) -> int:
    """
    Parse any PRIORITY QUEUE: line from the research focus and add those topics
    to the queue at priority 1 (highest), skipping already-completed topics.

    Format in research focus:
        PRIORITY QUEUE: topic one | topic two | topic three

    Returns the number of topics seeded.
    """
    import re
    match = re.search(r"PRIORITY QUEUE\s*:\s*(.+?)(?:\n|$)", focus, re.IGNORECASE)
    if not match:
        return 0

    raw = match.group(1)
    topics = [t.strip() for t in raw.split("|") if t.strip()]
    completed = set(kb._data.get("knowledge", {}).keys())
    seeded = 0
    for topic in topics:
        key = topic.lower().strip()
        if key not in completed:
            kb.add_to_queue(topic, priority=1, source_topic="focus_directive")
            seeded += 1

    if seeded:
        kb.save()
        ui.log("ok", f"Seeded [bold]{seeded}[/bold] priority topic(s) from research focus")
    return seeded


# ── Research section ───────────────────────────────────────────────────────────

def run_research() -> str:
    """Session → model → tier → research loop. Returns 'menu' or 'quit'."""
    migrated = session_manager.migrate_legacy_session()
    existing = session_manager.list_sessions()

    if migrated:
        chosen = migrated
    else:
        chosen = session_manager.prompt_session_choice(existing)

    session_manager.activate_session(chosen)

    kb = KnowledgeBase()
    resuming = kb.load()
    if not resuming:
        kb.init_fresh(chosen["seed_topic"])

    # Always re-seed priority topics — skips already-completed ones automatically
    _seed_priority_topics(kb, config.RESEARCH_FOCUS)

    stats = kb.get_stats()

    # Save a snapshot at session start for later diffing
    kb.save_snapshot(label="session_start")

    model_idx = ui.select_model()
    config.set_active_model_index(model_idx)

    tier = ui.select_speed_tier(stats["queue_size"])

    ui.print_banner(
        session_name=chosen["name"],
        seed_topic=stats["seed_topic"],
        tier=tier,
        stats=stats,
        resuming=resuming,
        model_name=config.MODEL_CATALOG[model_idx]["short"],
    )

    searcher = WebSearcher()
    groq     = GroqClient()

    # Start live display first so patching works
    display = ui.LiveDisplay(initial_queue=stats["queue_size"])
    display.update(
        done=stats["topics_researched"],
        facts=stats["total_facts"],
        model=config.MODEL_CATALOG[model_idx]["short"],
        tier=tier,
        lanes=config.RESEARCH_LANES,
    )
    display.start()

    # Primary loop
    loop = ResearchLoop(kb, searcher, groq, lane_id=0, display=display)

    def _make_patched_log(lp: ResearchLoop) -> None:
        _orig = lp._log
        def _patched(msg: str, level: str = "info") -> None:
            _orig(msg, level)
            s = kb.get_stats()
            display.update(
                topic=loop.get_current_topic(),
                done=s["topics_researched"],
                queue=s["queue_size"],
                facts=s["total_facts"],
                model=groq.get_active_model_name(),
                tokens=groq.get_usage_stats()["tokens_used"],
                calls=groq.get_usage_stats()["calls_made"],
            )
        lp._log = _patched

    _make_patched_log(loop)

    # Additional lanes
    extra_loops: list[ResearchLoop] = []
    extra_threads: list[threading.Thread] = []
    for lane in range(1, config.RESEARCH_LANES):
        el = ResearchLoop(kb, WebSearcher(), GroqClient(), lane_id=lane, display=display)
        _make_patched_log(el)
        extra_loops.append(el)
        t = threading.Thread(target=el.run, name=f"research-lane-{lane}", daemon=True)
        extra_threads.append(t)
        t.start()

    research_thread = threading.Thread(target=loop.run, name="research-loop", daemon=True)
    research_thread.start()

    try:
        result = _command_loop(loop, kb, groq, chosen, display, research_thread,
                               extra_loops=extra_loops)
    except KeyboardInterrupt:
        display.stop()
        ui.log("info", "Interrupted — finishing current cycle...")
        loop.signal_stop()
        for el in extra_loops:
            el.signal_stop()
        research_thread.join()
        result = "menu"

    # Save end-of-session snapshot for diffing next time
    kb.save_snapshot(label="session_end")

    # Final save and summary
    kb.save()
    final_stats  = kb.get_stats()
    token_stats  = groq.get_usage_stats()

    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    t = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2))
    t.add_column(style="bold cyan")
    t.add_column()
    t.add_row("Session",           chosen["name"])
    t.add_row("Topics researched", str(final_stats["topics_researched"]))
    t.add_row("Facts collected",   str(final_stats["total_facts"]))
    t.add_row("Queue remaining",   str(final_stats["queue_size"]))
    t.add_row("API calls",         str(token_stats["calls_made"]))
    t.add_row("Tokens used",       f"{token_stats['tokens_used']:,}")
    t.add_row("  Prompt tokens",   f"{token_stats.get('prompt_tokens', 0):,}")
    t.add_row("  Completion tokens", f"{token_stats.get('completion_tokens', 0):,}")
    t.add_row("Saved to",          config.KNOWLEDGE_FILE)
    ui.console.print()
    ui.console.print(Panel(t, title="[bold cyan]Session Paused[/bold cyan]",
                           border_style="cyan", padding=(0, 1)))
    ui.console.print()

    return result


# ── Reports section ────────────────────────────────────────────────────────────

def run_reports() -> str:
    """Interactive report browser. Returns 'menu' when done."""
    import report as report_module
    from rich.prompt import Prompt
    from rich import box
    from rich.panel import Panel
    from rich.table import Table

    existing = session_manager.list_sessions()
    if not existing:
        ui.console.print("\n  [dim]No sessions found. Run Research first.[/dim]\n")
        return "menu"

    if len(existing) == 1:
        session = existing[0]
    else:
        session_manager.print_session_list(existing)
        choices = [str(i) for i in range(1, len(existing) + 1)]
        choice = Prompt.ask("  [cyan]Select session[/cyan]", choices=choices, default="1")
        session = existing[int(choice) - 1]

    session_manager.activate_session(session)

    kb = KnowledgeBase()
    if not kb.load():
        ui.console.print("\n  [dim]No knowledge base found for that session.[/dim]\n")
        return "menu"

    while True:
        ui.console.print()
        tbl = Table(box=box.SIMPLE, show_header=False, pad_edge=False, padding=(0, 3))
        tbl.add_column(style="bold cyan", width=3)
        tbl.add_column(style="bold", width=14)
        tbl.add_column(style="dim")
        tbl.add_row("1", "Synthesize",  "AI-generated narrative report (best model)")
        tbl.add_row("2", "Full report", "All topics with facts and summaries")
        tbl.add_row("3", "Facts only",  "Compact flat list of all facts")
        tbl.add_row("4", "Queue",       "Pending research topics")
        tbl.add_row("5", "Export",      "Export to JSON + Markdown files")
        tbl.add_row("6", "Back",        "Return to main menu")

        ui.console.print(Panel(
            tbl,
            title=f"[bold cyan]Reports[/bold cyan]  [dim]{session['name']}[/dim]",
            border_style="cyan",
            padding=(0, 1),
        ))

        r = Prompt.ask("  [cyan]Select[/cyan]",
                       choices=["1", "2", "3", "4", "5", "6"], default="1")

        if r == "1":
            groq = GroqClient()
            report_module.generate_synthesized(kb, groq)
        elif r == "2":
            report_module.generate(kb)
        elif r == "3":
            report_module.render_facts_only(kb._data)
        elif r == "4":
            report_module.render_queue(kb._data)
        elif r == "5":
            _export_session(kb, session)
        elif r == "6":
            return "menu"


# ── Main loop ──────────────────────────────────────────────────────────────────

def main() -> None:
    while True:
        action = ui.print_main_menu()

        if action == "quit":
            ui.console.print("\n  [dim]Goodbye.[/dim]\n")
            break
        elif action == "research":
            result = run_research()
            if result == "quit":
                ui.console.print("\n  [dim]Goodbye.[/dim]\n")
                break
        elif action == "reports":
            result = run_reports()
            if result == "quit":
                ui.console.print("\n  [dim]Goodbye.[/dim]\n")
                break


if __name__ == "__main__":
    main()

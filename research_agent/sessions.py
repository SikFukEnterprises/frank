"""
sessions.py — session management helpers for the research agent.

Each session lives in:
    research_agent/sessions/<slug>/
        session.json     — name, research_focus, created
        knowledge.json   — knowledge base
        queue.json       — research queue (legacy, unused directly)
        log.txt          — activity log
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from rich.prompt import Prompt

import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "session"


def _sessions_dir() -> str:
    return config.SESSIONS_DIR


def list_sessions() -> list[dict]:
    """Return a list of session dicts sorted by last_updated desc."""
    base = _sessions_dir()
    if not os.path.isdir(base):
        return []
    sessions = []
    for entry in os.scandir(base):
        if not entry.is_dir():
            continue
        meta_path = os.path.join(entry.path, "session.json")
        if not os.path.exists(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        # Pull quick stats from knowledge.json if it exists
        kb_path = os.path.join(entry.path, "knowledge.json")
        topics_done = 0
        total_facts = 0
        last_updated = meta.get("created", "")
        seed_topic = meta.get("seed_topic", "")
        if os.path.exists(kb_path):
            try:
                with open(kb_path, "r", encoding="utf-8") as f:
                    kb = json.load(f)
                kb_meta = kb.get("meta", {})
                topics_done = kb_meta.get("topics_researched", 0)
                total_facts = kb_meta.get("total_facts", 0)
                last_updated = kb_meta.get("last_updated", last_updated)
                if not seed_topic:
                    seed_topic = kb_meta.get("seed_topic", "")
            except (json.JSONDecodeError, OSError):
                pass

        sessions.append({
            "name": meta.get("name", entry.name),
            "slug": entry.name,
            "dir": entry.path,
            "seed_topic": seed_topic,
            "research_focus": meta.get("research_focus", ""),
            "created": meta.get("created", ""),
            "last_updated": last_updated,
            "topics_done": topics_done,
            "total_facts": total_facts,
        })

    sessions.sort(key=lambda s: s["last_updated"], reverse=True)
    return sessions


def create_session(name: str, seed_topic: str, research_focus: str) -> dict:
    """Create a new session directory and return its info dict."""
    base = _sessions_dir()
    slug = _slugify(name)

    # Avoid collisions
    candidate = slug
    counter = 2
    while os.path.exists(os.path.join(base, candidate)):
        candidate = f"{slug}-{counter}"
        counter += 1
    slug = candidate

    session_dir = os.path.join(base, slug)
    os.makedirs(session_dir, exist_ok=True)

    meta = {
        "name": name,
        "slug": slug,
        "seed_topic": seed_topic,
        "research_focus": research_focus,
        "created": _now(),
    }
    with open(os.path.join(session_dir, "session.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return {**meta, "dir": session_dir, "topics_done": 0, "total_facts": 0, "last_updated": meta["created"]}


def load_session_meta(session_dir: str) -> dict:
    path = os.path.join(session_dir, "session.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def activate_session(session: dict) -> None:
    """Point config paths and RESEARCH_FOCUS at this session."""
    config.set_session_dir(session["dir"])
    if session.get("research_focus"):
        config.set_research_focus(session["research_focus"])


def print_session_list(sessions: list[dict]) -> None:
    import ui
    ui.print_session_list(sessions)


def _legacy_data_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "data")


def migrate_legacy_session() -> dict | None:
    """
    If the legacy data/ dir has a knowledge base but no sessions exist yet,
    offer to import it as a named session. Returns the new session dict or None.
    """
    legacy_kb = os.path.join(_legacy_data_dir(), "knowledge.json")
    if not os.path.exists(legacy_kb):
        return None
    if list_sessions():
        return None  # sessions already exist, skip migration prompt

    import ui
    from rich.prompt import Prompt, Confirm

    ui.console.print()
    ui.console.print("  [dim]Found an existing knowledge base in the legacy data/ directory.[/dim]")
    if not Confirm.ask("  Import it as a named session?", default=True):
        return None

    try:
        with open(legacy_kb, "r", encoding="utf-8") as f:
            kb_data = json.load(f)
        seed = kb_data.get("meta", {}).get("seed_topic", "legacy")
    except (json.JSONDecodeError, OSError):
        seed = "legacy"

    name = Prompt.ask("  Session name", default=seed)
    focus = Prompt.ask("  Research focus (leave blank to keep default)", default="") or config.RESEARCH_FOCUS

    session = create_session(name, seed, focus)
    session_dir = session["dir"]

    # Copy knowledge.json (and log/queue if they exist)
    import shutil
    for fname in ("knowledge.json", "log.txt", "queue.json"):
        src = os.path.join(_legacy_data_dir(), fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(session_dir, fname))

    ui.log("ok", f"Imported as session '[bold]{name}[/bold]' (slug: {session['slug']})")
    session["is_new"] = False
    return session


def delete_session(session: dict) -> None:
    """Permanently remove a session directory from disk."""
    import shutil
    shutil.rmtree(session["dir"])


def prompt_session_choice(sessions: list[dict]) -> dict:
    """
    Interactive session picker.
    Returns the chosen/created session dict with 'is_new' key set.

    Commands at the prompt:
      <number>   — resume that session
      N          — create a new session
      D<number>  — delete that session (with confirmation)
    """
    import ui
    from rich.prompt import Confirm

    ui.print_logo()

    while sessions:
        print_session_list(sessions)
        n = len(sessions)
        try:
            ui.console.print(
                f"  [dim]Enter[/dim] [cyan]1-{n}[/cyan] [dim]to resume  │[/dim]  "
                "[cyan]N[/cyan] [dim]for new  │[/dim]  "
                "[cyan]D<num>[/cyan] [dim]to delete[/dim]"
            )
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            ui.console.print()
            sys.exit(0)

        if not raw:
            raw = "1"

        # Delete command: D1, d2, D 3, etc.
        low = raw.lower()
        if low.startswith("d"):
            num_part = low[1:].strip()
            try:
                didx = int(num_part)
            except ValueError:
                ui.console.print("  [dim]Usage: D<number>  e.g. D2[/dim]")
                continue
            if not (1 <= didx <= n):
                ui.console.print(f"  [dim]Please enter a number 1-{n}.[/dim]")
                continue
            target = sessions[didx - 1]
            ui.console.print(
                f"\n  [yellow]Delete session '[bold]{target['name']}[/bold]'?[/yellow]  "
                f"[dim]({target['topics_done']} topics, {target['total_facts']} facts)[/dim]"
            )
            if Confirm.ask("  This cannot be undone", default=False):
                delete_session(target)
                ui.log("ok", f"Deleted session '[bold]{target['name']}[/bold]'")
            else:
                ui.log("info", "Deletion cancelled")
            sessions = list_sessions()
            ui.console.print()
            continue

        if low == "n":
            break

        try:
            idx = int(raw)
            if 1 <= idx <= n:
                session = sessions[idx - 1]
                session["is_new"] = False
                return session
        except ValueError:
            pass
        ui.console.print(f"  [dim]Please enter 1-{n}, N, or D<num>.[/dim]")

    if not sessions:
        ui.console.print("  [dim]No existing sessions. Starting a new one.[/dim]")

    # --- New session ---
    ui.console.print()
    try:
        name = Prompt.ask("  Session name", default="Unnamed session")
        seed = Prompt.ask("  Seed topic to research")
        if not seed:
            ui.log("error", "Seed topic cannot be empty. Exiting.")
            sys.exit(1)

        ui.console.print()
        ui.console.print(
            "  [dim]Research focus — tells the agent what facts are worth keeping and what to ignore.[/dim]\n"
            "  [dim]Example: 'Only extract wiring diagrams, part numbers, and failure modes for the FA20DIT engine'[/dim]\n"
            "  [dim]Leave blank to research the seed topic broadly.[/dim]"
        )
        focus = Prompt.ask("  [cyan]Focus directive[/cyan]", default="")
        if not focus:
            focus = (
                f"FOCUS: Research '{seed}' thoroughly. "
                "Extract specific, actionable facts. "
                "Skip general background, definitions, and introductory content."
            )

    except (EOFError, KeyboardInterrupt):
        ui.console.print()
        sys.exit(0)

    session = create_session(name, seed, focus)
    session["is_new"] = True
    return session

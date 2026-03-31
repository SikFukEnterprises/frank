"""
ui.py — Rich-based terminal UI for Frank.

Provides:
  - SFE logo banner
  - Colored, icon-tagged log output
  - Speed tier selection with time estimates
  - Formatted banners and status panels
  - Session list display
"""

import threading
import time
from datetime import timedelta
from typing import Optional

from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich import box

console = Console(highlight=False)

_BRAND   = "SIK FUK ENTERPRISES"
_PRODUCT = "FRANK  ·  Autonomous Research Agent"

# ── Log level icons & styles ───────────────────────────────────────────────────
_LEVELS = {
    "info":    ("[cyan]●[/cyan]",       ""),
    "ok":      ("[bold green]✓[/bold green]", ""),
    "warn":    ("[bold yellow]▲[/bold yellow]", "yellow"),
    "error":   ("[bold red]✗[/bold red]",   "red"),
    "search":  ("[blue]⌕[/blue]",       ""),
    "extract": ("[magenta]◈[/magenta]", ""),
    "save":    ("[dim]▪[/dim]",         "dim"),
    "gap":     ("[yellow]◌[/yellow]",   ""),
    "rate":    ("[yellow]⏸[/yellow]",  "yellow"),
    "skip":    ("[dim]⊘[/dim]",         "dim"),
}


def print_logo() -> None:
    """Print the SFE branding header."""
    console.print()
    console.print(f"  [bold white]{_BRAND}[/bold white]")
    console.print(f"  [cyan]{_PRODUCT}[/cyan]")
    console.print()


def log(level: str, msg: str) -> None:
    """Print a timestamped, color-coded log line."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    icon, style = _LEVELS.get(level, ("●", ""))
    if style:
        console.print(f"[dim]{ts}[/dim] {icon} [{style}]{msg}[/{style}]")
    else:
        console.print(f"[dim]{ts}[/dim] {icon} {msg}")


# ── Live dashboard ─────────────────────────────────────────────────────────────

class LiveDisplay:
    """
    Renders a live-updating stats bar above the scrolling research log.
    Usage:
        display = LiveDisplay(total_topics)
        display.start()
        ...
        display.update(topic=..., facts=..., model=...)
        ...
        display.stop()
    """

    def __init__(self, initial_queue: int = 0):
        self._lock  = threading.Lock()
        self._live  = None
        self._stats = {
            "topic":    "",
            "done":     0,
            "queue":    initial_queue,
            "facts":    0,
            "model":    "",
            "tier":     "",
            "tokens":   0,
            "calls":    0,
            "paused":   False,
        }
        self._start_time = time.monotonic()

    def _build(self) -> Panel:
        s = self._stats
        elapsed = int(time.monotonic() - self._start_time)
        elapsed_str = _fmt_duration(elapsed)

        done  = s["done"]
        total = done + s["queue"]
        pct   = done / total if total else 0

        tier_colors = {"slow": "dim", "normal": "cyan", "fast": "yellow", "turbo": "bold red"}
        tc = tier_colors.get(s["tier"], "cyan")
        status_label = "[bold yellow]PAUSED[/bold yellow]" if s["paused"] else f"[{tc}]{s['tier'].upper()}[/{tc}]"

        # Progress bar (manual, avoids Progress widget complexity in Live)
        bar_width = 30
        filled = int(bar_width * pct)
        bar = f"[cyan]{'█' * filled}[/cyan][dim]{'░' * (bar_width - filled)}[/dim]"

        topic_display = f"[italic]{s['topic']}[/italic]" if s["topic"] else "[dim]—[/dim]"

        line = (
            f" {bar}  [bold]{done}[/bold]/[dim]{total}[/dim] topics  "
            f"[magenta]{s['facts']}[/magenta] facts  "
            f"[yellow]{s['model']}[/yellow]  "
            f"{status_label}  "
            f"[dim]{s['tokens']:,} tok  {elapsed_str}[/dim]"
        )

        return Panel(
            f"{line}\n [dim]→[/dim] {topic_display}",
            title=f"[bold white]{_BRAND}[/bold white]  [dim cyan]{_PRODUCT}[/dim cyan]",
            border_style="cyan",
            padding=(0, 1),
        )

    def start(self) -> None:
        self._live = Live(
            self._build(),
            console=console,
            auto_refresh=False,   # no background timer — only redraws when we say so
            transient=False,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    def update(self, **kwargs) -> None:
        with self._lock:
            self._stats.update(kwargs)
            if self._live:
                self._live.update(self._build(), refresh=True)


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    td = timedelta(seconds=int(seconds))
    h, rem = divmod(td.seconds, 3600)
    m = rem // 60
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def select_model() -> int:
    """
    Show model selection table and return the chosen index into config.MODEL_CATALOG.
    The agent will start with this model and cascade down under rate limits.
    """
    import config

    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold blue",
        pad_edge=False,
        row_styles=["", "dim"],
    )
    table.add_column("#",           style="bold cyan", width=3)
    table.add_column("Model",       style="bold", width=16)
    table.add_column("Quality",     width=10)
    table.add_column("RPM",         width=5)
    table.add_column("Description", style="dim")

    stars = ["★★★★★", "★★★★☆", "★★★☆☆", "★★☆☆☆", "★☆☆☆☆"]
    star_styles = ["bold yellow", "yellow", "cyan", "dim cyan", "dim"]
    for i, m in enumerate(config.MODEL_CATALOG):
        s = stars[i] if i < len(stars) else ""
        ss = star_styles[i] if i < len(star_styles) else "dim"
        table.add_row(
            str(i + 1),
            m["short"],
            Text(s, style=ss),
            str(m["rpm"]),
            m["description"],
        )

    console.print(Panel(
        table,
        title="[bold cyan]◈  Select Starting Model[/bold cyan]",
        subtitle="[dim]Cascades to weaker models under rate limits, upgrades back when cleared[/dim]",
        border_style="cyan",
        padding=(0, 1),
    ))

    choices = [str(i) for i in range(1, len(config.MODEL_CATALOG) + 1)]
    choice = Prompt.ask("  [cyan]Starting model[/cyan]", choices=choices, default="1")
    return int(choice) - 1


def select_speed_tier(queue_size: int) -> str:
    """
    Show speed tier options with time estimates and prompt the user.
    Returns the chosen tier name: 'slow' | 'normal' | 'fast' | 'turbo'.
    """
    import config

    API_LATENCY = 3.0  # seconds per call, rough estimate

    tiers = [
        ("1", "slow",   "Conservative — minimal rate-limit risk"),
        ("2", "normal", "Balanced — recommended default"),
        ("3", "fast",   "Aggressive — occasional rate-limit waits"),
        ("4", "turbo",  "Maximum — rate limiter throttles as needed"),
    ]

    tier_colors = {
        "slow":   "dim",
        "normal": "cyan",
        "fast":   "yellow",
        "turbo":  "bold red",
    }

    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold blue",
        pad_edge=False,
    )
    table.add_column("#",                style="bold cyan", width=3)
    table.add_column("Tier",             width=8)
    table.add_column("Sleep",            width=7)
    table.add_column("RPM",              width=5)
    table.add_column("Est. time/cycle",  width=17)
    table.add_column("Est. total",       width=12)
    table.add_column("Notes",            style="dim")

    for key, name, notes in tiers:
        t = config.SPEED_TIERS[name]
        sleep = t["sleep"]
        rpm = t["rpm"]
        rate_gap = max(0.0, (2 / rpm) * 60 - API_LATENCY * 2)
        cycle_time = API_LATENCY * 2 + rate_gap + sleep
        total_secs = queue_size * cycle_time if queue_size > 0 else 0
        color = tier_colors.get(name, "cyan")

        table.add_row(
            str(key),
            Text(name, style=color),
            f"{sleep}s",
            str(rpm),
            f"~{_fmt_duration(cycle_time)}",
            f"~{_fmt_duration(total_secs)}" if queue_size > 0 else "[dim]—[/dim]",
            notes,
        )

    subtitle = (
        f"[bold]{queue_size} topics in queue[/bold]"
        if queue_size > 0
        else "[dim italic]Queue empty — topics will be generated[/dim italic]"
    )

    console.print(Panel(
        table,
        title="[bold cyan]⏱  Select Research Speed[/bold cyan]",
        subtitle=subtitle,
        border_style="cyan",
        padding=(0, 1),
    ))

    while True:
        choice = Prompt.ask(
            "  [cyan]Speed tier[/cyan]",
            choices=["1", "2", "3", "4"],
            default="2",
        )
        name = tiers[int(choice) - 1][1]
        config.apply_speed_tier(name)
        return name


def print_banner(session_name: str, seed_topic: str, tier: str, stats: dict,
                 resuming: bool, token_stats: Optional[dict] = None,
                 model_name: Optional[str] = None) -> None:
    """Print the main startup banner."""
    tier_colors = {"slow": "dim", "normal": "cyan", "fast": "yellow", "turbo": "bold red"}
    tier_color = tier_colors.get(tier, "cyan")

    # Stats row
    if resuming:
        state_line = (
            f"[bold]{stats['topics_researched']}[/bold] topics done  "
            f"[bold]{stats['queue_size']}[/bold] queued  "
            f"[bold]{stats['total_facts']}[/bold] facts"
        )
    else:
        state_line = "[dim]Starting fresh[/dim]"

    model_tag = f"[dim]{model_name}[/dim]  " if model_name else ""
    tier_tag = f"[[{tier_color}]{tier.upper()}[/{tier_color}]]"

    lines = [
        f"  {model_tag}{tier_tag}",
        "",
        f"  [bold]Session[/bold]  [cyan]{session_name}[/cyan]",
        f"  [bold]Topic[/bold]    [italic]{seed_topic}[/italic]",
        f"  {state_line}",
    ]

    if token_stats and token_stats.get("calls_made", 0) > 0:
        lines.append(
            f"  [dim]Tokens: {token_stats['tokens_used']:,}  "
            f"Calls: {token_stats['calls_made']}[/dim]"
        )

    console.print()
    console.print(Panel(
        "\n".join(lines),
        title=f"[bold white]{_BRAND}[/bold white]  [dim cyan]{_PRODUCT}[/dim cyan]",
        subtitle="[dim]menu  ·  add/remove  ·  pause/resume  ·  speed/model  ·  report  ·  synthesize[/dim]",
        border_style="cyan",
        padding=(0, 2),
    ))
    console.print()


def print_status(stats: dict, token_stats: Optional[dict] = None,
                 tier: Optional[str] = None, current_topic: Optional[str] = None,
                 model_name: Optional[str] = None) -> None:
    """Print a compact status panel."""
    tier_colors = {"slow": "dim", "normal": "cyan", "fast": "yellow", "turbo": "bold red"}

    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2))
    table.add_column(style="bold cyan", width=14)
    table.add_column()

    table.add_row("Topics done",  str(stats["topics_researched"]))
    table.add_row("Queue",        str(stats["queue_size"]))
    table.add_row("Facts",        str(stats["total_facts"]))

    if current_topic:
        table.add_row("Researching", f"[italic]{current_topic}[/italic]")

    if tier:
        color = tier_colors.get(tier, "cyan")
        table.add_row("Speed tier",  f"[{color}]{tier}[/{color}]")

    if model_name:
        table.add_row("Model",       f"[yellow]{model_name}[/yellow]")

    if token_stats and token_stats.get("calls_made", 0) > 0:
        table.add_row("API calls",   str(token_stats["calls_made"]))
        table.add_row("Tokens used", f"{token_stats['tokens_used']:,}")

    console.print(Panel(
        table,
        title="[bold cyan]◈  Status[/bold cyan]",
        border_style="blue",
        padding=(0, 1),
    ))


def print_main_menu() -> str:
    """
    Show the main menu and return the chosen action:
    'research' | 'reports' | 'quit'
    """
    from rich.rule import Rule

    console.print()
    console.print(f"  [bold white]{_BRAND}[/bold white]")
    console.print(f"  [dim]{_PRODUCT}[/dim]")
    console.print()

    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False, padding=(0, 3))
    table.add_column(style="bold cyan", width=3)
    table.add_column(style="bold", width=12)
    table.add_column(style="dim")
    table.add_row("1", "Research",  "Start or resume a research session")
    table.add_row("2", "Reports",   "Browse and synthesize session knowledge")
    table.add_row("3", "Quit",      "Exit Frank")

    console.print(Panel(table, border_style="cyan", padding=(0, 1)))

    _map = {"1": "research", "2": "reports", "3": "quit"}
    choice = Prompt.ask("  [cyan]Select[/cyan]", choices=list(_map.keys()), default="1")
    return _map[choice]


def print_session_list(sessions: list[dict]) -> None:
    """Print sessions as a Rich table."""
    if not sessions:
        return

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        pad_edge=False,
        row_styles=["", "dim"],
    )
    table.add_column("#",            style="bold cyan", width=3)
    table.add_column("Name",         style="bold")
    table.add_column("Seed topic",   style="italic", max_width=38)
    table.add_column("Done",         width=6, style="green")
    table.add_column("Facts",        width=7, style="magenta")
    table.add_column("Last updated", width=12, style="dim")

    for i, s in enumerate(sessions, 1):
        last = s["last_updated"][:10] if s["last_updated"] else "?"
        table.add_row(
            str(i),
            s["name"],
            s.get("seed_topic", ""),
            str(s["topics_done"]),
            str(s["total_facts"]),
            last,
        )

    console.print(Panel(
        table,
        title="[bold cyan]◈  Sessions[/bold cyan]",
        border_style="cyan",
        padding=(0, 1),
    ))

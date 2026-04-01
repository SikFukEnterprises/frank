# FRANK — Autonomous Research Agent
## User Manual

**SIK FUK ENTERPRISES**

---

## Table of Contents

1. [Overview](#overview)
2. [Installation](#installation)
3. [Quick Start](#quick-start)
4. [Session Management](#session-management)
5. [Model Selection](#model-selection)
6. [Speed Tiers](#speed-tiers)
7. [Research Focus Directive](#research-focus-directive)
8. [Live Dashboard](#live-dashboard)
9. [Interactive Commands](#interactive-commands)
10. [Knowledge Base](#knowledge-base)
11. [Research Sources](#research-sources)
12. [Reporting](#reporting)
13. [Export Formats](#export-formats)
14. [Web Dashboard](#web-dashboard)
15. [RomRaider Integration](#romraider-integration)
16. [ELM327 PID Validator](#elm327-pid-validator)
17. [Advanced Features](#advanced-features)
18. [Configuration Reference](#configuration-reference)
19. [File Layout](#file-layout)
20. [Troubleshooting](#troubleshooting)

---

## Overview

Frank is an autonomous research agent that continuously researches a topic by:

1. Searching the web (DuckDuckGo, GitHub, Reddit, RSS, PDFs)
2. Extracting structured knowledge via Groq LLMs
3. Cross-referencing new facts against existing knowledge
4. Maintaining a persistent, corroborated knowledge base
5. Generating synthesized reports on demand

Frank runs in your terminal while you work. It is headless — you type commands into the same terminal while research runs in the background. A live stats bar keeps you informed.

Frank was designed for technical reverse-engineering work (specifically CAN/OBD2 data for Subaru vehicles) but can research any topic.

---

## Installation

### Requirements

- Python 3.11+
- A Groq API key (free tier available at console.groq.com)

### Install dependencies

```bash
cd frank
pip install -r requirements.txt
```

**requirements.txt includes:**
```
groq
ddgs
requests
beautifulsoup4
lxml
rich
pypdf
feedparser
fastapi
uvicorn[standard]
jinja2
networkx
pyserial
```

**Optional (large install, ~500MB):**
```bash
pip install sentence-transformers
```
Required only for semantic similarity search (`similar <query>` command). Set `ENABLE_EMBEDDINGS = True` in `config.py` after installing.

### API Key

Set your Groq API key as an environment variable:

```bash
export GROQ_API_KEY=gsk_your_key_here
```

Or edit `config.py` to set it as the fallback default (dev/testing only — do not commit real keys).

---

## Quick Start

```bash
cd frank/research_agent
python agent.py
```

**Startup flow:**

1. **Main menu** — choose Research, Reports, or Quit
2. **Session selection** — resume an existing session or create a new one
3. **Research focus** — describe what facts to keep and what to ignore
4. **Model selection** — choose your starting LLM (Frank cascades automatically)
5. **Speed tier** — choose research pace
6. **Research begins** — runs in background; type commands in the foreground

---

## Session Management

Each research session is isolated with its own knowledge base, queue, and log.

### Creating a session

When no sessions exist, or you select **N** at the session prompt:

- **Session name** — a short name for the session (e.g., `SFEDash CAN Research`)
- **Seed topic** — the starting research topic (e.g., `Subaru FA20DIT ELM327 OBD2 extended PIDs`)
- **Focus directive** — tells Frank what to extract and what to ignore (see [Research Focus Directive](#research-focus-directive))

### Resuming a session

Select by number at the session prompt. Frank continues where it left off — queue, facts, and visited URLs are all preserved.

### Session list

```bash
python report.py --sessions
```

Shows all sessions with topic count, fact count, and last updated date.

### Deleting a session

At the session prompt, type `D<number>` (e.g., `D2`). You will be asked to confirm. **This is permanent.**

### Session data location

```
research_agent/sessions/<slug>/
    session.json     — name, seed topic, research focus, created date
    knowledge.json   — all facts, summaries, conflicts, queue
    log.txt          — full activity log
    export.json      — last JSON export (if run)
    export.md        — last Markdown export (if run)
    obsidian_vault/  — last Obsidian export (if run)
    facts.csv        — last CSV export (if run)
    digest_*.md      — auto-digest reports
```

---

## Model Selection

Frank uses a cascade of Groq models. At startup, you select the **preferred** (starting) model. Frank automatically falls back to weaker models when rate limits are hit, then upgrades back when the preferred model clears.

| # | Model | Quality | RPM | Notes |
|---|-------|---------|-----|-------|
| 1 | Llama 3.3 70B | ★★★★★ | 30 | Best reasoning, strongest JSON compliance |
| 2 | GPT-OSS 120B | ★★★★☆ | 30 | High quality |
| 3 | Qwen3 32B | ★★★☆☆ | 60 | 2× RPM — best throughput on free tier |
| 4 | Llama 4 Scout | ★★☆☆☆ | 30 | Fast, capable |
| 5 | GPT-OSS 20B | ★☆☆☆☆ | 30 | Fast fallback |
| 6 | Llama 3.1 8B | ☆☆☆☆☆ | 30 | Last-resort, minimal latency |

**Total cascade capacity: 210 RPM** across all 6 models.

**Model corroboration rules:**
- A stronger model (lower rank) always overwrites a weaker model's summary immediately.
- A weaker model must research the same topic `(rank_diff + 1)` times before its summary overwrites a stronger model's version.
- Facts are deduplicated semantically — the stronger model's wording is kept.

**Mid-session model switch:**
```
model 2
```
Changes the preferred model without stopping research.

---

## Speed Tiers

| Tier | Sleep | RPM limit | Topics/cycle | Use when |
|------|-------|-----------|--------------|----------|
| slow | 20s | 15 | 2 | Minimal rate-limit risk |
| normal | 5s | 28 | 3 | Recommended default |
| fast | 1s | 28 | 5 | Aggressive, occasional waits |
| turbo | 0s | 28 | 5 | Maximum — rate limiter handles throttling |

Change mid-session:
```
speed fast
speed normal
```

**Adaptive sleep:** If Frank finds zero new facts for 4+ consecutive cycles, it automatically doubles the sleep time (up to 60s). It resets when facts are found again.

---

## Research Focus Directive

The focus directive is a text string injected into every Groq prompt. It tells Frank what facts to extract and what to ignore. This is the single most important setting for research quality.

### Setting a focus

When creating a new session, you are prompted for a focus directive. Leave blank for a generic broad-research focus.

### Example focus (SFEDash / Subaru OBD2)

```
FOCUS: Extract specific OBD2 and SSM parameter data for a 2015-2019 Subaru WRX/Forester XT
with FA20DIT engine, accessed via ELM327 Bluetooth OBD2 adapter. Only extract:
(1) Standard Mode 01 PID hex codes with exact scaling formulas and units;
(2) Subaru-specific Mode 22 PID hex codes for boost pressure, knock count, fuel trims, oil temp;
(3) SSM parameter memory addresses accessible via ISO 14230 K-Line or CAN;
(4) ELM327 AT command sequences for extended/manufacturer-specific PIDs on Subaru;
(5) Any confirmed working PID or SSM address verified against FA20DIT specifically.
Ignore: general OBD2 explanations, how CAN works, non-Subaru vehicles, unconfirmed values.
PRIORITY QUEUE: Subaru SSM protocol init sequence ELM327 | FA20DIT knock retard OBD2 PID Mode 22 | Subaru boost pressure PID 0x22 | RomRaider logger XML FA20 parameters | OpenPort 2.0 Subaru SSM parameter list
```

### PRIORITY QUEUE directive

Any `PRIORITY QUEUE:` line in the focus is parsed at session start. Topics listed (separated by `|`) are automatically added to the research queue at priority 1, skipping any already researched.

```
PRIORITY QUEUE: topic one | topic two | topic three
```

This means Frank starts on your most important topics immediately, without needing manual `add:` commands.

---

## Live Dashboard

While research runs, a panel at the top of the terminal updates continuously:

```
╭─ SIK FUK ENTERPRISES  FRANK · Autonomous Research Agent ─────────────────╮
│ ████████████░░░░░░░░░░░░░░░░  12/47 topics  [magenta]284[/magenta] facts ▁▂▃▅▆▄  Llama 3.3 70B  NORMAL  1,204 tok  4m │
│  → Subaru SSM boost pressure PID Mode 22                                    │
╰─────────────────────────────────────────────────────────────────────────────╯
```

**Elements:**
- **Progress bar** — topics done / total (done + queued)
- **Facts count** — total facts in knowledge base
- **Sparkline** — facts-per-cycle history (last 60 cycles) as a unicode graph
- **Model** — currently active model
- **Tier** — current speed tier (color-coded: dim=slow, cyan=normal, yellow=fast, red=turbo)
- **Lanes** — shown if running 2+ parallel research lanes
- **Tokens** — total tokens used this session
- **Elapsed** — session wall-clock time
- **Current topic** — what Frank is researching right now

The display only redraws when Frank logs something, so typing commands does not cause flickering.

---

## Interactive Commands

Type commands directly in the terminal while research runs. Press Enter to submit.

### Navigation

| Command | Action |
|---------|--------|
| `menu` | Stop research and return to main menu |
| `quit` | Stop research and exit Frank |
| `help` | Print all available commands |

### Queue management

| Command | Action |
|---------|--------|
| `add: <topic>` | Add a topic to the queue at priority 1 (highest) |
| `remove: <topic>` | Remove a topic from the queue |
| `queue` | Print the current research queue |

**Example:**
```
add: Subaru FA20DIT Mode 22 boost PID 0x2222
remove: CAN bus history
```

### Research control

| Command | Action |
|---------|--------|
| `pause` | Pause after the current cycle (research suspends) |
| `resume` | Resume paused research |
| `speed <tier>` | Change speed: `slow` `normal` `fast` `turbo` |
| `model <N>` | Switch preferred model (1–6) |
| `lanes <N>` | Set parallel research lane count (1–3) |
| `plan` | Generate a 10–15 step research plan via LLM and seed queue |

### Reporting and knowledge

| Command | Action |
|---------|--------|
| `status` | Print a stats panel (topics, facts, queue, model, tokens) |
| `report` | Full inline knowledge base report |
| `synthesize` | AI-generated narrative report using best available model |
| `tree` | Render topic relationship tree |
| `diff` | Show what changed between the last two session snapshots |
| `objectives` | Show research objective completion scores |

### Hypotheses

| Command | Action |
|---------|--------|
| `hypothesis add <text>` | Track a hypothesis (e.g., `hypothesis add The boost PID is 0x2222`) |
| `hypothesis list` | Show all hypotheses and their current status |

Frank evaluates open hypotheses against KB facts every 10 research cycles and updates their status to `confirmed`, `refuted`, or `uncertain` with a confidence score.

### Semantic search

| Command | Action |
|---------|--------|
| `similar <query>` | Find the most semantically similar facts in the KB |

Requires `sentence-transformers` installed and `ENABLE_EMBEDDINGS = True` in `config.py`.

### Export

| Command | Action |
|---------|--------|
| `export` | Export to `export.json` + `export.md` in session dir |
| `obsidian` | Export to Obsidian vault (interlinked `.md` files) |
| `csv` | Export flat CSV of all facts |
| `import <path>` | Import facts from a previous `export.json` |

### Tools

| Command | Action |
|---------|--------|
| `romraider` | Fetch RomRaider XML from GitHub and ingest as KB facts |
| `webui` | Launch the web dashboard in the background |

---

## Knowledge Base

### How facts are stored

Each fact has:

| Field | Description |
|-------|-------------|
| `content` | The fact text |
| `confidence` | `high` / `medium` / `low` |
| `source_url` | URL where the fact was found |
| `source_count` | How many independent sources confirmed this fact |
| `source_quality` | `high` / `medium` / `low` (based on domain) |
| `model_rank` | Which model tier extracted this (lower = more trusted) |
| `hardware_verified` | `true` if confirmed by live ELM327 query |
| `needs_verification` | `true` if the fact was involved in a detected conflict |

### Deduplication

Frank normalises fact text before comparing (lowercased, articles removed, punctuation stripped, whitespace collapsed). This catches paraphrases like:

> "The CAN bus runs at 500 kbps" ≡ "CAN bus operates at 500kbps"

Duplicate facts are not added — instead, `source_count` is incremented on the existing fact.

### Confidence upgrades

- 2 independent sources → confidence upgraded one level
- 3+ independent sources → confidence forced to `high`
- High-quality source (e.g., `github.com`, `.edu`, `romraider.com`) → confidence upgraded

### Conflict detection

When the LLM detects that new information contradicts an existing fact:
- A conflict is stored with both fact versions and source URLs
- Both involved facts have confidence **downgraded** one level
- Both are flagged `needs_verification: true`
- A warning is logged immediately in the terminal

### Dry topic tracking

If a topic returns zero new facts for 3+ consecutive research cycles, it is marked `dry` and removed from future queue consideration. This prevents Frank from wasting cycles on topics with no web coverage.

### Stale topic re-research

Topics with 2+ facts that haven't been researched in 14 days are automatically re-queued when the queue empties, to pick up new information.

### Source quality domains

High quality domains (confidence-boosted): `github.com`, `stackoverflow.com`, `arxiv.org`, `romraider.com`, `csselectronics.com`, `kvaser.com`, `.edu`, `.gov`

Low quality patterns (confidence-penalised): `blogspot.com`, WordPress personal blogs, AMP pages, SEO-style URLs

---

## Research Sources

Frank draws from multiple sources each cycle:

### DuckDuckGo (primary)

Standard web search. Fetches the top `PAGES_TO_FETCH` (default: 4) results for the current topic.

### Query variants

For each topic, Frank generates `SEARCH_QUERY_VARIANTS` (default: 2) additional phrasings:
- `{topic} specification`
- `{topic} tutorial`
- `{topic} example code`
- `{topic} documentation`
- `{topic} github`
- `{topic} forum discussion`
- etc.

Variant selection is deterministic based on a hash of the topic name.

### GitHub crawler

Searches GitHub for repos matching the topic. For each result, fetches:
- The README (tries `main` branch, then `master`)
- Top 5 open issues as a text summary

Enabled via `ENABLE_GITHUB_CRAWL = True` (default: on).

### Reddit

Searches DuckDuckGo with `site:reddit.com {topic}`. Results are re-pointed to `old.reddit.com` for cleaner HTML parsing.

### PDF ingestion

When a search result URL ends in `.pdf` or the server returns `Content-Type: application/pdf`, Frank uses `pypdf` to extract text from the PDF (up to 10 pages). Enabled via `ENABLE_PDF_FETCH = True` (default: on).

### RSS feeds

Parse configured RSS/Atom feeds and inject new entries as research topics. Configure via:
```python
# config.py
ENABLE_RSS_MONITOR = True
RSS_FEEDS = [
    "https://example.com/feed.xml",
]
```

### Wayback Machine fallback

When a page fetch fails (404, 403, connection error), Frank checks the Wayback Machine for a recent snapshot. Enabled via `ENABLE_WAYBACK_FALLBACK = True` (default: on).

### Page content extraction

For HTML pages, Frank finds the main content area before truncating:
- Tries `<article>`, `<main>`, `div#content`, `div#main`, `div.post-content`, `div.entry-content`, `div.article-body`
- Falls back to full `<body>` text if none found
- Strips: `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`, `<aside>`, forms, ads

---

## Reporting

### Inline report

```
report
```

Prints the full knowledge base in the terminal — one panel per topic, with facts table, conflicts, related topics, and research metadata.

### Synthesized report

```
synthesize
```

Uses the best available Groq model to read all facts and write a structured narrative report. The model focuses on what is actionable and specific, ignoring background definitions.

For CAN/OBD2 research, the synthesized report is organized into:
1. Confirmed Frame IDs & Signals
2. OBD2 / SSM PIDs
3. Hardware & Tools
4. Conflicts & Uncertainties
5. Gaps — What We Still Need

### Topic tree

```
tree
```

Renders the topic relationship graph as a nested tree using `rich.Tree`. Shows how Frank's research branched from the seed topic through follow-up topics.

### Session diff

```
diff
```

Shows what changed between the last two session snapshots (saved automatically at session start and end):
- Topics added / removed
- Per-topic fact deltas
- Confidence changes

### Objectives

```
objectives
```

Shows each research objective with a score bar (0–100%) and completion status. Objectives are scored by keyword overlap between the objective text and high/medium-confidence facts in the KB.

Set objectives in `config.py`:
```python
RESEARCH_OBJECTIVES = [
    "Find the Mode 22 PID address for boost pressure with confirmed scaling formula",
    "Confirm SSM init sequence for ELM327 Bluetooth adapter",
    "Identify knock retard PID and verify units",
]
```

### CLI report tool

```bash
python report.py                  # full report, most recent session
python report.py --session subu   # specific session by slug
python report.py --topic "ssm"    # single topic (partial match)
python report.py --facts          # compact all-facts view
python report.py --queue          # pending research queue
python report.py --sessions       # list all sessions
python report.py --synthesize     # generate synthesized report
```

---

## Export Formats

### JSON export

```
export
```

Saves `export.json` to the session directory:

```json
{
  "session": "SFEDash CAN Research",
  "seed_topic": "...",
  "exported_at": "...",
  "topics": {
    "subaru ssm boost pressure": {
      "summary": "...",
      "facts": [
        {
          "content": "...",
          "confidence": "high",
          "source_count": 3,
          "source_url": "..."
        }
      ],
      "conflicts": []
    }
  }
}
```

Also saves `export.md` — a Markdown version suitable for sharing.

### Obsidian vault

```
obsidian
```

Exports to `sessions/<slug>/obsidian_vault/` as a collection of `.md` files:

- `INDEX.md` — list of all topics with links
- One file per topic with facts, conflicts, related topics, and `[[wikilinks]]` to related topics

Drop the vault folder into Obsidian to get a linked knowledge graph view.

### CSV

```
csv
```

Saves `facts.csv` to the session directory with columns:

```
topic, fact, confidence, source_count, source_url, source_quality,
hardware_verified, needs_verification, model_rank
```

Suitable for spreadsheet analysis or import into other tools.

### Import

```
import /path/to/export.json
```

Ingests facts from a previous `export.json` into the current session. Imported facts are treated as rank-4 weight (conservative — they won't overwrite stronger-model research without corroboration).

---

## Web Dashboard

Frank includes a browser-based live dashboard.

### Start the dashboard

```
webui
```

(runs while Frank is also running)

Or standalone:

```bash
python web_app.py                     # most recent session, port 8765
python web_app.py --session subu      # specific session
python web_app.py --port 9000         # custom port
```

Then open `http://localhost:8765` in your browser.

### Dashboard features

- **Stats row** — topics researched, total facts, queue size, objective coverage %
- **Progress bar** — visual done/total topics
- **Facts table** — all facts sortable/filterable by text search; confidence color-coded (green=high, yellow=medium, red=low); hardware-verified marked ✅
- **Queue panel** — pending topics with priority badges
- **D3 force graph** — interactive topic relationship graph; nodes sized by fact count; drag to rearrange
- **SSE live updates** — stats refresh every 3 seconds via Server-Sent Events; graph refreshes every 30 seconds
- **Dark theme** — monospace font, cyan accents

The dashboard is **read-only** — it never writes to the knowledge base.

---

## RomRaider Integration

RomRaider is an open-source ECU tuning tool with comprehensive XML logger definition files covering hundreds of Subaru SSM parameters with confirmed addresses and scaling formulas.

### Ingest from Frank command prompt

```
romraider
```

Fetches the following RomRaider logger XML files from GitHub and ingests all parameters as high-confidence facts:
- `logger_EJ_FA_Subaru.xml` — FA20/EJ series parameters
- `logger_WRX_STi_EJ257_15-21.xml` — WRX/STI parameters
- `logger_Subaru_TCM.xml` — Transmission control module

Each parameter becomes a fact like:
```
Engine Speed: address 0x0CE (len=2) units=rpm scaling=x*0.25 — Engine RPM
```

### Standalone CLI

```bash
python romraider.py                      # print summary table
python romraider.py --output pids.json   # save SFEDash config JSON
python romraider.py --ingest             # ingest into most recent session
python romraider.py --session subu       # target specific session
python romraider.py --list               # list all found parameters
```

### SFEDash config format

`--output pids.json` generates:

```json
{
  "version": "1.0",
  "source": "RomRaider",
  "generated_at": "...",
  "parameters": [
    {
      "name": "Boost Pressure (Actual)",
      "pid_address": "0x1C",
      "protocol": "SSM",
      "units": "psi",
      "scaling": "x*0.00328",
      "display_format": "0.00",
      "romraider_id": "P200"
    }
  ]
}
```

---

## ELM327 PID Validator

Validates discovered PIDs by querying the live ECU through an ELM327 adapter.

### Usage

```bash
python elm327.py --port /dev/ttyUSB0
python elm327.py --port /dev/rfcomm0 --session subu    # Bluetooth
python elm327.py --port COM3                           # Windows
python elm327.py --dry-run                             # no connection
python elm327.py --scan --port /dev/ttyUSB0            # scan Mode 01 support
```

### What it does

1. Scans the knowledge base for PID patterns in fact text (hex addresses, Mode 22 PIDs, SSM addresses)
2. Connects to the ELM327 adapter and initialises it (`ATZ`, `ATE0`, `ATL0`, `ATSP0`)
3. Queries each discovered PID or SSM address
4. Reports which PIDs responded vs returned NO DATA
5. Marks responding PIDs as `hardware_verified: true` in the knowledge base

### Dry run

```bash
python elm327.py --dry-run
```

Shows which PIDs were found in the KB and would be tested, without connecting to anything. Useful for checking what Frank has discovered before plugging in the adapter.

### PID scan

```bash
python elm327.py --scan --port /dev/ttyUSB0
```

Queries Mode 01 PID support bitmasks (0x00, 0x20, 0x40, 0x60) to enumerate all supported standard OBD PIDs.

### Hardware verified flag

After running the validator, facts with `hardware_verified: true` are visible:
- In the Obsidian export with ✅
- In the CSV export
- In the web dashboard facts table

---

## Advanced Features

### Parallel research lanes

Frank can run 2–3 simultaneous research loops, each working on different topics from the shared queue. This multiplies throughput when you have high rate-limit headroom.

```
lanes 2
```

Or set in `config.py`:
```python
RESEARCH_LANES = 2
```

Each lane gets its own `WebSearcher` and `GroqClient` instance. The knowledge base is shared (with thread-safe writes). The stats bar shows "×2 lanes" when active.

**Note:** Multiple lanes multiply API usage. Use with the turbo tier and model 3 (Qwen3 60 RPM) for best results.

### Research plan generation

```
plan
```

Asks the best available Groq model to generate a structured 10–15 step research plan from the session's seed topic and focus directive. The plan is seeded into the queue in priority order — foundational topics first, then specifics, then edge cases.

This is particularly useful at session start when the queue is empty, to get a purposeful research sequence rather than letting Frank discover topics organically.

### Hypothesis tracking

Track specific claims you want Frank to confirm or refute:

```
hypothesis add The Subaru FA20DIT boost pressure SSM address is 0x001C
hypothesis add Mode 22 PID 0x2222 returns knock count on FA20DIT
hypothesis list
```

Every 10 research cycles, Frank evaluates each open hypothesis against all collected facts using the Groq model. Status updates to:
- `confirmed` — multiple facts support the hypothesis
- `refuted` — facts contradict it
- `uncertain` — insufficient evidence either way

### Knowledge graph

The knowledge base maintains a `networkx.DiGraph` where:
- Nodes = researched topics (sized by fact count)
- Edges = related-topic relationships

Access via `tree` command in Frank, or the web dashboard graph panel.

Programmatically:
```python
from knowledge_base import KnowledgeBase
kb = KnowledgeBase()
kb.load()
G = kb.get_graph()                    # networkx DiGraph
data = kb.get_graph_data()            # JSON-serializable dict
```

### Session snapshots and diff

Snapshots are saved automatically at session start and end. Each snapshot records:
- Fact count per topic
- Average confidence per topic
- Summary hash per topic

```
diff
```

Shows what changed between the two most recent snapshots. Useful after long sessions to see where Frank made progress.

Save a manual snapshot at any time:
```python
kb.save_snapshot(label="after_romraider_ingest")
```

### Auto-digest

Frank automatically generates a synthesis report every N research cycles:

```python
# config.py
AUTO_DIGEST_CYCLES = 50   # 0 = disabled
```

Digests are saved to the session directory as `digest_YYYYMMDD_HHMMSS.md`. Useful for long-running sessions where you check in periodically.

### Semantic similarity search

Find facts most relevant to a query:

```
similar boost pressure scaling formula
similar SSM init sequence ELM327
```

Requires:
```bash
pip install sentence-transformers
```

And in `config.py`:
```python
ENABLE_EMBEDDINGS = True
```

Uses `all-MiniLM-L6-v2` (local, no API calls). Encodes all facts on first query (can be slow with large KBs).

---

## Configuration Reference

All configuration is in `research_agent/config.py`. Settings are module-level globals — do not pass them as objects.

### API and models

| Setting | Default | Description |
|---------|---------|-------------|
| `GROQ_API_KEY` | env var | Groq API key |
| `MODEL_CATALOG` | 6 models | Model list with rank, RPM |
| `ACTIVE_MODEL_INDEX` | 0 | Starting model (0 = best) |
| `MAX_TOKENS` | 1024 | Max tokens per extraction call |
| `TEMPERATURE` | 0.3 | LLM temperature |

### Research behaviour

| Setting | Default | Description |
|---------|---------|-------------|
| `SLEEP_BETWEEN_CYCLES` | 5s | Pause between research cycles |
| `MAX_PAGE_CHARS` | 5000 | Characters to read per page |
| `PAGES_TO_FETCH` | 4 | Pages fetched per topic per cycle |
| `MAX_QUEUE_SIZE` | 500 | Maximum queue length |
| `MAX_TOPICS_PER_CYCLE` | 3 | Follow-up topics added per cycle |
| `CROSS_REF_MIN_TOPICS` | 5 | Min KB topics before cross-referencing |
| `GAP_TOPICS_BATCH` | 15 | Gap topics generated when queue empties |
| `RERESEARCH_DAYS` | 14 | Re-queue stale topics after N days (0=off) |
| `DRY_TOPIC_THRESHOLD` | 3 | Mark topic dry after N zero-fact cycles |
| `ADAPTIVE_SLEEP_THRESHOLD` | 4 | Slow down after N consecutive barren cycles |
| `RESEARCH_LANES` | 1 | Parallel research loops (1–3) |
| `AUTO_DIGEST_CYCLES` | 50 | Generate digest every N cycles (0=off) |
| `HYPOTHESIS_EVAL_CYCLES` | 10 | Re-evaluate hypotheses every N cycles |

### Speed tiers

| Tier | Sleep | RPM | Max topics |
|------|-------|-----|------------|
| slow | 20s | 15 | 2 |
| normal | 5s | 28 | 3 |
| fast | 1s | 28 | 5 |
| turbo | 0s | 28 | 5 |

### Source types

| Setting | Default | Description |
|---------|---------|-------------|
| `ENABLE_PDF_FETCH` | `True` | Fetch and parse PDFs |
| `ENABLE_GITHUB_CRAWL` | `True` | Crawl GitHub repos |
| `ENABLE_RSS_MONITOR` | `False` | Monitor RSS feeds |
| `ENABLE_WAYBACK_FALLBACK` | `True` | Wayback Machine on failure |
| `SEARCH_QUERY_VARIANTS` | 2 | Extra query phrasings per topic |
| `RSS_FEEDS` | `[]` | List of RSS feed URLs |

### Knowledge and objectives

| Setting | Default | Description |
|---------|---------|-------------|
| `RESEARCH_OBJECTIVES` | `[]` | List of objective strings |
| `ENABLE_EMBEDDINGS` | `False` | sentence-transformers semantic search |

### Web dashboard

| Setting | Default | Description |
|---------|---------|-------------|
| `WEB_DASHBOARD_PORT` | 8765 | Port for `python web_app.py` |

---

## File Layout

```
frank/
├── requirements.txt          # pip dependencies
├── MANUAL.md                 # this file
└── research_agent/
    ├── agent.py              # entry point, interactive command loop
    ├── researcher.py         # background research loop(s)
    ├── groq_client.py        # Groq API wrapper, model cascade, rate limiting
    ├── knowledge_base.py     # persistent KB, graph, dedup, corroboration
    ├── web_search.py         # DuckDuckGo, GitHub, Reddit, PDF, RSS, Wayback
    ├── report.py             # Rich reports, Obsidian/CSV export, auto-digest
    ├── sessions.py           # session create/load/list/delete
    ├── ui.py                 # Rich terminal UI, sparkline, tree, panels
    ├── config.py             # all configuration (module-level globals)
    ├── web_app.py            # FastAPI web dashboard
    ├── romraider.py          # RomRaider XML parser + SFEDash config gen
    ├── elm327.py             # ELM327 serial PID validator
    ├── CLAUDE.md             # developer notes for Claude Code
    └── sessions/
        └── <slug>/
            ├── session.json
            ├── knowledge.json
            └── log.txt
```

---

## Troubleshooting

### Rate limits / slow research

Frank handles Groq rate limits automatically by cascading through weaker models. If all 6 models are simultaneously rate-limited, Frank waits for the preferred model to clear. This is expected on the free tier during heavy usage.

Mitigations:
- Use **model 3 (Qwen3 32B)** as your starting model — it has 60 RPM vs 30 RPM for others
- Use **normal** or **slow** tier during initial seeding
- Use **turbo** once the queue is large and rate limits are the bottleneck

### "No web content for topic"

The topic search returned no usable pages. Possible causes:
- Topic is too obscure or specific — try broader phrasing via `add:` command
- DuckDuckGo temporary rate limit — Frank will re-queue and retry
- All search results were previously visited — Frank will still try cached content

### JSON parse failures

Groq models occasionally return malformed JSON. Frank automatically retries once with a stricter prompt. If both attempts fail, the cycle is skipped. This is normal and self-correcting.

### Live display not updating

The display only redraws when Frank logs something. In quiet periods (sleeping between cycles), the display is static. This is intentional — background redraws conflicted with terminal input.

### "All modules import OK" test

Verify the installation is complete:

```bash
cd research_agent
python -c "import config, knowledge_base, web_search, groq_client, researcher, agent, report, sessions, ui, romraider, elm327, web_app; print('OK')"
```

### Resetting a session

To start fresh within an existing session (keep the session, clear the KB):

Delete `knowledge.json` from the session directory. Frank will call `init_fresh()` and start over on next run.

To completely remove a session: type `D<num>` at the session selection prompt.

### ELM327 connection issues

- Ensure the adapter is paired (Bluetooth) or plugged in (USB) before running
- Try different baud rates: `--baud 9600` or `--baud 115200`
- On Linux, you may need to add your user to the `dialout` group: `sudo usermod -a -G dialout $USER`
- Bluetooth serial adapters appear as `/dev/rfcomm0` after pairing. If not: `sudo rfcomm bind 0 <MAC>`
- Run `--dry-run` first to confirm Frank found PIDs in the KB before connecting

---

*Frank — SIK FUK ENTERPRISES — Autonomous Research Agent*

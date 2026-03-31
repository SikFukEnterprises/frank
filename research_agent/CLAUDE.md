# Frank — Autonomous Research Agent

## What this is

Frank is an autonomous research agent that continuously researches a seed topic by searching the web, extracting structured knowledge via Groq LLMs, and maintaining a persistent knowledge base. Currently configured for CAN bus reverse engineering on a 2016 Subaru Forester XT (FA20DIT engine).

## Running the agent

```bash
cd research_agent
python agent.py
```

Startup flow: session selection → model selection (1–4) → speed tier (slow/normal/fast/turbo) → research loop starts in background thread.

**Interactive commands while running:**
- `quit` — graceful stop after current cycle
- `add: <topic>` — inject topic into queue at priority 1
- `status` — current stats and active model
- `report` — inline knowledge report

**Standalone reporting:**
```bash
python report.py                  # full report for active session
python report.py --topic <name>   # single topic
python report.py --facts          # compact all-facts view
python report.py --sessions       # list sessions
python report.py --session <slug> # specific session
```

## Environment

Requires `GROQ_API_KEY` environment variable. A dev/demo key is also hardcoded in `config.py` as fallback — do not commit a real key there.

```bash
pip install -r ../requirements.txt
```

Dependencies: `groq`, `ddgs`, `requests`, `beautifulsoup4`, `lxml`, `rich`

## Architecture

| File | Role |
|------|------|
| `agent.py` | Entry point, command loop (main thread) |
| `researcher.py` | Research loop (background daemon thread) |
| `groq_client.py` | Groq API wrapper with model cascade + rate limiting |
| `knowledge_base.py` | Persistent JSON knowledge store, corroboration logic |
| `web_search.py` | DuckDuckGo search + page fetch/parse |
| `report.py` | Rich-formatted reporting (inline + CLI) |
| `sessions.py` | Multi-session management |
| `ui.py` | Rich terminal UI, model/tier selection |
| `config.py` | Central config — models, speed tiers, paths, research focus |

## Key design patterns

**Model cascade on rate limits:** When a 429 is hit, `groq_client.py` cascades to the next weaker model. Each model has a 62s penalty box. After a successful call on a weaker model, it tries to upgrade back to the preferred model.

**Model rank corroboration:** Stronger models (lower rank) overwrite weaker summaries immediately. Weaker models must research the same topic `(rank_diff + 1)` times to overwrite a stronger model's summary. Facts are deduplicated by content; stronger-model version is kept.

**Research cycle (researcher.py):**
1. Pop topic from queue (or generate gap topics via LLM if queue empty)
2. DuckDuckGo search + fetch top 2 pages
3. Groq: extract facts, summary, conflicts, follow-up topics
4. Groq: cross-reference new facts against existing KB
5. Update KB, queue follow-ups
6. Sleep (tier-dependent)

## Session data layout

```
sessions/
  <slug>/
    session.json    # metadata (name, seed_topic, research_focus, created)
    knowledge.json  # facts, summaries, conflicts, related topics, queue
    log.txt         # activity log
data/               # legacy location (auto-migrated to sessions/ on first run)
```

## config.py globals

Config uses module-level globals mutated at runtime by `set_active_model_index()`, `apply_speed_tier()`, and `set_session_dir()`. When adding features, follow this pattern — don't try to pass config as objects.

## Current research focus (config.py `RESEARCH_FOCUS`)

All Groq prompts include a directive restricting extraction to CAN frame IDs, byte/bit offsets, signal scaling, OBD2 PIDs, SSM protocol, and hardware setups (MCP2515, ESP32). General engine info is explicitly excluded. Target vehicle: 2016 Subaru Forester XT.

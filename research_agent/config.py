import os


GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "GROQ_API_KEY_REMOVED")

# ── Model catalogue ────────────────────────────────────────────────────────────
# rank 1 = best quality.  Models are tried in rank order when cascading on 429s.
# A weaker model (higher rank) needs more corroboration to overwrite stronger ones.
MODEL_CATALOG: list[dict] = [
    {
        "id":          "llama-3.3-70b-versatile",
        "rank":        1,
        "rpm":         30,
        "short":       "Llama 3.3 70B",
        "description": "Best quality — strongest reasoning, best JSON compliance",
    },
    {
        "id":          "openai/gpt-oss-120b",
        "rank":        2,
        "rpm":         30,
        "short":       "GPT-OSS 120B",
        "description": "High quality — strong reasoning",
    },
    {
        "id":          "qwen/qwen3-32b",
        "rank":        3,
        "rpm":         60,   # 60 RPM — highest free-tier limit available
        "short":       "Qwen3 32B",
        "description": "Good quality — 60 RPM (2× others), best throughput",
    },
    {
        "id":          "meta-llama/llama-4-scout-17b-16e-instruct",
        "rank":        4,
        "rpm":         30,
        "short":       "Llama 4 Scout",
        "description": "Fast, capable — 30K TPM, good for high-volume cycles",
    },
    {
        "id":          "openai/gpt-oss-20b",
        "rank":        5,
        "rpm":         30,
        "short":       "GPT-OSS 20B",
        "description": "Fast — 1000 T/s, good for high-throughput cycles",
    },
    {
        "id":          "llama-3.1-8b-instant",
        "rank":        6,
        "rpm":         30,
        "short":       "Llama 3.1 8B",
        "description": "Fastest — last-resort fallback, minimal latency",
    },
]

# Active model index into MODEL_CATALOG (set at startup by user selection).
ACTIVE_MODEL_INDEX: int = 0

# Convenience string kept in sync with ACTIVE_MODEL_INDEX.
MODEL: str = MODEL_CATALOG[ACTIVE_MODEL_INDEX]["id"]


def set_active_model_index(idx: int) -> None:
    global ACTIVE_MODEL_INDEX, MODEL
    ACTIVE_MODEL_INDEX = idx
    MODEL = MODEL_CATALOG[idx]["id"]


MAX_TOKENS = 1024
TEMPERATURE = 0.3
SLEEP_BETWEEN_CYCLES = 5
MAX_PAGE_CHARS = 5000       # raised from 2000 — more content per page
PAGES_TO_FETCH = 4          # raised from 2 — broader coverage per topic
MAX_QUEUE_SIZE = 500
MAX_TOPICS_PER_CYCLE = 3

# Efficiency thresholds
CROSS_REF_MIN_TOPICS = 5    # skip cross-reference until KB has this many topics
GAP_TOPICS_BATCH = 15       # how many gap topics to generate when queue empties
RERESEARCH_DAYS = 14        # re-queue important topics older than this (0 = off)
DRY_TOPIC_THRESHOLD = 3     # mark topic dry after this many zero-fact cycles
ADAPTIVE_SLEEP_THRESHOLD = 4  # slow down after this many consecutive barren cycles

# Speed tiers — each entry overrides SLEEP_BETWEEN_CYCLES and MAX_TOPICS_PER_CYCLE.
SPEED_TIERS: dict = {
    "slow":   {"sleep": 20, "rpm": 15, "max_topics": 2},
    "normal": {"sleep": 5,  "rpm": 28, "max_topics": 3},
    "fast":   {"sleep": 1,  "rpm": 28, "max_topics": 5},
    "turbo":  {"sleep": 0,  "rpm": 28, "max_topics": 5},
}

CURRENT_SPEED_TIER: str = "normal"


def apply_speed_tier(name: str) -> None:
    """Apply a speed tier — mutates the live config values."""
    global SLEEP_BETWEEN_CYCLES, MAX_TOPICS_PER_CYCLE, CURRENT_SPEED_TIER
    tier = SPEED_TIERS[name]
    SLEEP_BETWEEN_CYCLES = tier["sleep"]
    MAX_TOPICS_PER_CYCLE = tier["max_topics"]
    CURRENT_SPEED_TIER = name


# Injected into every Groq prompt to keep research on-topic.
# Overridden at runtime by the active session's research_focus field.
#
# Sessions can embed a PRIORITY QUEUE: line to auto-seed the KB queue on startup.
# Format:  PRIORITY QUEUE: topic one | topic two | topic three
# Topics listed here are added to the queue at priority 1, skipping any already done.
RESEARCH_FOCUS = (
    "FOCUS: Research the given topic thoroughly. "
    "Extract all relevant facts, data, and insights. "
    "Prioritize specific, actionable information over general background or definitions."
)


def set_research_focus(focus: str) -> None:
    global RESEARCH_FOCUS
    RESEARCH_FOCUS = focus

# ── Research lane count ────────────────────────────────────────────────────────
RESEARCH_LANES: int = 1          # 1–3 parallel research loops

# ── Auto-digest ────────────────────────────────────────────────────────────────
AUTO_DIGEST_CYCLES: int = 50     # generate a digest every N research cycles (0 = off)

# ── Search diversity ───────────────────────────────────────────────────────────
SEARCH_QUERY_VARIANTS: int = 2   # number of varied query phrasings to try per topic

# ── Source types ───────────────────────────────────────────────────────────────
ENABLE_PDF_FETCH: bool   = True
ENABLE_GITHUB_CRAWL: bool = True
ENABLE_RSS_MONITOR: bool  = False   # on by default but no feeds configured until set
ENABLE_WAYBACK_FALLBACK: bool = True

# ── RSS feeds to monitor (list of feed URLs, any topic) ───────────────────────
RSS_FEEDS: list[str] = []

# ── Objectives ─────────────────────────────────────────────────────────────────
# List of strings describing research goals. Frank tracks % coverage.
RESEARCH_OBJECTIVES: list[str] = []

# ── Hypothesis tracking ────────────────────────────────────────────────────────
HYPOTHESIS_EVAL_CYCLES: int = 10  # re-evaluate hypotheses every N cycles

# ── Vector embeddings (requires sentence-transformers) ────────────────────────
ENABLE_EMBEDDINGS: bool = False   # set True after: pip install sentence-transformers

# ── Web dashboard ──────────────────────────────────────────────────────────────
WEB_DASHBOARD_PORT: int = 8765

_dir = os.path.join(os.path.dirname(__file__), "data")
KNOWLEDGE_FILE = os.path.join(_dir, "knowledge.json")
QUEUE_FILE = os.path.join(_dir, "queue.json")
LOG_FILE = os.path.join(_dir, "log.txt")

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")


def set_session_dir(session_dir: str) -> None:
    """Update all file paths to point at the given session directory."""
    global KNOWLEDGE_FILE, QUEUE_FILE, LOG_FILE
    KNOWLEDGE_FILE = os.path.join(session_dir, "knowledge.json")
    QUEUE_FILE = os.path.join(session_dir, "queue.json")
    LOG_FILE = os.path.join(session_dir, "log.txt")

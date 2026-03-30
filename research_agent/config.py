import os

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "your_key_here")
MODEL = "compound-beta"
MAX_TOKENS = 1024
TEMPERATURE = 0.3
RATE_LIMIT_RPM = 20
SLEEP_BETWEEN_CYCLES = 30
MAX_PAGE_CHARS = 2000
MAX_QUEUE_SIZE = 500
MAX_TOPICS_PER_CYCLE = 3

# Injected into every Groq prompt to keep research on-topic.
# Edit this to change what the agent focuses on.
RESEARCH_FOCUS = (
    "FOCUS: You are exclusively interested in CAN bus reverse engineering data for the Subaru FA20 engine. "
    "Only extract facts about: CAN frame IDs (hex addresses), byte/bit offsets within frames, "
    "signal scaling and units, OBD2 PID mappings, SSM (Subaru Select Monitor) protocol details, "
    "known CAN bus tools and sniffers used with Subaru vehicles, and hardware setups (MCP2515, ESP32, etc). "
    "IGNORE and DO NOT extract: general engine specs, reliability, maintenance, oil changes, "
    "horsepower figures, turbo upgrades, or anything unrelated to CAN/OBD2 data acquisition."
)

_dir = os.path.join(os.path.dirname(__file__), "data")
KNOWLEDGE_FILE = os.path.join(_dir, "knowledge.json")
QUEUE_FILE = os.path.join(_dir, "queue.json")
LOG_FILE = os.path.join(_dir, "log.txt")

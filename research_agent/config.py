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

_dir = os.path.join(os.path.dirname(__file__), "data")
KNOWLEDGE_FILE = os.path.join(_dir, "knowledge.json")
QUEUE_FILE = os.path.join(_dir, "queue.json")
LOG_FILE = os.path.join(_dir, "log.txt")

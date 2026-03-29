import os

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "your_key_here")
MODEL = "llama-3.3-70b-versatile"
MAX_TOKENS = 2048
TEMPERATURE = 0.3
RATE_LIMIT_RPM = 25
SLEEP_BETWEEN_CYCLES = 10
MAX_PAGE_CHARS = 6000
MAX_QUEUE_SIZE = 500
MAX_TOPICS_PER_CYCLE = 3

_dir = os.path.join(os.path.dirname(__file__), "data")
KNOWLEDGE_FILE = os.path.join(_dir, "knowledge.json")
QUEUE_FILE = os.path.join(_dir, "queue.json")
LOG_FILE = os.path.join(_dir, "log.txt")

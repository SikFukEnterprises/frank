import json
import time
from collections import deque
from datetime import datetime, timezone

from groq import Groq

import config

_EXTRACTION_SCHEMA = """{
  "summary": "concise summary of findings",
  "facts": [
    {"content": "specific fact", "confidence": "high|medium|low", "source_url": "url", "added_at": "ISO timestamp"}
  ],
  "conflicts": [
    {"fact_a": "string", "fact_b": "string", "source_a": "url", "source_b": "url", "note": "description"}
  ],
  "follow_up_topics": ["topic1", "topic2", "topic3"],
  "related_topics": ["topic1", "topic2"]
}"""

_CROSS_REF_SCHEMA = """{
  "connections": [
    {"topic_a": "string", "topic_b": "string", "relationship": "description"}
  ],
  "contradictions": [
    {"topic": "string", "description": "description"}
  ],
  "knowledge_gaps": ["gap1", "gap2"]
}"""

_GAP_TOPICS_SCHEMA = """{
  "topics": ["topic1", "topic2", "topic3"]
}"""


class GroqClient:
    def __init__(self):
        self._client = Groq(api_key=config.GROQ_API_KEY)
        self._call_times: deque = deque()

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        now = time.monotonic()
        window = 60.0
        # Remove timestamps older than 1 minute
        while self._call_times and now - self._call_times[0] > window:
            self._call_times.popleft()
        if len(self._call_times) >= config.RATE_LIMIT_RPM:
            sleep_for = window - (now - self._call_times[0]) + 0.5
            if sleep_for > 0:
                print(f"[RATE LIMIT] Sleeping {sleep_for:.1f}s to stay within Groq limits")
                time.sleep(sleep_for)
        self._call_times.append(time.monotonic())

    # ------------------------------------------------------------------
    # Internal call wrapper
    # ------------------------------------------------------------------

    def _call(self, system: str, user: str) -> str:
        self._rate_limit()
        response = self._client.chat.completions.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            temperature=config.TEMPERATURE,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content.strip()

    def _call_json(self, system: str, user: str, retry_strict: bool = True) -> dict | None:
        """Call Groq and parse JSON. Retries once with stricter prompt on parse error."""
        raw = self._call(system, user)
        try:
            return self._parse_json(raw)
        except (json.JSONDecodeError, ValueError):
            if not retry_strict:
                return None
            strict_system = system + "\n\nCRITICAL: Your response MUST be valid JSON only. No text before or after the JSON object. No markdown code fences."
            raw2 = self._call(strict_system, user)
            try:
                return self._parse_json(raw2)
            except (json.JSONDecodeError, ValueError):
                return None

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Extract JSON from response, stripping markdown fences if present."""
        text = text.strip()
        # Strip ```json ... ``` or ``` ... ```
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first and last fence lines
            inner = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
            text = "\n".join(inner).strip()
        return json.loads(text)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_knowledge(
        self,
        topic: str,
        existing_summary: str,
        page_contents: list[str],
        source_urls: list[str],
    ) -> dict | None:
        """
        Groq call 1: extract structured knowledge from fetched pages.
        Returns parsed dict or None on failure.
        """
        now = datetime.now(timezone.utc).isoformat()
        combined_pages = "\n\n---\n\n".join(
            f"[Source: {url}]\n{text}"
            for url, text in zip(source_urls, page_contents)
        )

        system = (
            "You are a research assistant extracting structured knowledge. "
            "Always respond in valid JSON only. No preamble or explanation.\n\n"
            f"{config.RESEARCH_FOCUS}"
        )
        user = (
            f"Topic: {topic}\n\n"
            f"Existing knowledge on this topic: {existing_summary or 'None'}\n\n"
            f"New source material:\n{combined_pages}\n\n"
            f"Current timestamp: {now}\n\n"
            f"Extract only facts relevant to the research focus above:\n"
            f"1. Key facts (with confidence high/medium/low, include source_url and added_at)\n"
            f"2. Conflicts with existing knowledge\n"
            f"3. 3-5 follow-up research topics directly related to CAN/OBD2 data\n"
            f"4. A concise summary of on-topic findings only\n"
            f"5. Related topics already in my knowledge base\n\n"
            f"Return as JSON matching this schema:\n{_EXTRACTION_SCHEMA}"
        )

        result = self._call_json(system, user)
        if result is None:
            return None

        # Ensure required fields exist with defaults
        result.setdefault("summary", "")
        result.setdefault("facts", [])
        result.setdefault("conflicts", [])
        result.setdefault("follow_up_topics", [])
        result.setdefault("related_topics", [])
        return result

    def cross_reference(self, topic: str, new_facts: list[dict], related_knowledge: dict) -> dict | None:
        """
        Groq call 2: identify connections, contradictions, and gaps.
        related_knowledge is {topic_key: summary}.
        Returns parsed dict or None on failure.
        """
        if not related_knowledge:
            return {"connections": [], "contradictions": [], "knowledge_gaps": []}

        facts_text = "\n".join(f"- {f.get('content', '')}" for f in new_facts[:8])
        related_text = "\n".join(
            f"[{k}]: {v}" for k, v in list(related_knowledge.items())[:5]
        )

        system = (
            "You are a research analyst identifying connections between pieces of knowledge. "
            "Always respond in valid JSON only. No preamble or explanation.\n\n"
            f"{config.RESEARCH_FOCUS}"
        )
        user = (
            f"New facts about '{topic}':\n{facts_text}\n\n"
            f"Existing knowledge summaries:\n{related_text}\n\n"
            f"Identify:\n"
            f"1. Connections between the new facts and existing knowledge\n"
            f"2. Contradictions between new and existing knowledge\n"
            f"3. Knowledge gaps about CAN/OBD2 data that should be researched next\n\n"
            f"Return as JSON matching this schema:\n{_CROSS_REF_SCHEMA}"
        )

        result = self._call_json(system, user)
        if result is None:
            return {"connections": [], "contradictions": [], "knowledge_gaps": []}

        result.setdefault("connections", [])
        result.setdefault("contradictions", [])
        result.setdefault("knowledge_gaps", [])
        return result

    def generate_gap_topics(self, all_summaries: dict, seed_topic: str = "") -> list[str]:
        """
        When the queue is empty, ask Groq to suggest new research topics
        based on gaps in existing knowledge (or from the seed topic if KB is empty).
        Returns list of topic strings.
        """
        system = (
            "You are a research strategist identifying knowledge gaps. "
            "Always respond in valid JSON only. No preamble or explanation.\n\n"
            f"{config.RESEARCH_FOCUS}"
        )

        if not all_summaries:
            # KB is empty — bootstrap from seed topic
            if not seed_topic:
                return []
            user = (
                f"I am starting research on the topic: '{seed_topic}'\n\n"
                f"Suggest {config.MAX_TOPICS_PER_CYCLE} specific CAN/OBD2-focused sub-topics "
                f"that would be most valuable to research first.\n\n"
                f"Return as JSON matching this schema:\n{_GAP_TOPICS_SCHEMA}"
            )
        else:
            summaries_text = "\n".join(
                f"[{k}]: {v}" for k, v in list(all_summaries.items())[:8]
            )
            user = (
                f"I have researched the following topics:\n{summaries_text}\n\n"
                f"Suggest {config.MAX_TOPICS_PER_CYCLE} new topics strictly about CAN frame data, "
                f"OBD2 PIDs, SSM protocol, or related hardware/software that would expand "
                f"this knowledge base.\n\n"
                f"Return as JSON matching this schema:\n{_GAP_TOPICS_SCHEMA}"
            )

        result = self._call_json(system, user)
        if result is None:
            return []
        return result.get("topics", [])[:config.MAX_TOPICS_PER_CYCLE]

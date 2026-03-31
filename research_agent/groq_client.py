import json
import time
from collections import deque
from datetime import datetime, timezone

import groq as groq_sdk
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

# How long (seconds) to put a model in the penalty box after a 429.
_RATE_LIMIT_PENALTY = 62.0

# Generic patterns that indicate a fact is background/definitional rather than specific data.
# These are topic-agnostic — they catch "X stands for Y", "was developed by", etc.
import re as _re
_TRIVIAL_RE = _re.compile(
    r"\bstands for\b"
    r"|\bis an? (acronym|abbreviation) for\b"
    r"|\bwas (invented|developed|introduced|created|designed) by\b"
    r"|\bwas (first|originally) (introduced|developed|created|invented|used)\b"
    r"|\bis defined as\b"
    r"|\boriginally developed\b"
    r"|\bcommonly (used|known|referred)\b.{0,30}\bfor\b.{0,20}\b(its|the|a)\b",
    _re.IGNORECASE,
)


def _is_trivial_fact(content: str) -> bool:
    """Return True if the fact looks like generic background/definitional content."""
    return bool(_TRIVIAL_RE.search(content))


class GroqClient:
    def __init__(self):
        self._client = Groq(api_key=config.GROQ_API_KEY)

        n = len(config.MODEL_CATALOG)
        # Per-model sliding window of call timestamps for self-imposed rate limiting.
        self._model_call_times: list[deque] = [deque() for _ in range(n)]
        # Monotonic time after which each model is usable again (0 = usable now).
        self._model_rl_until: list[float] = [0.0] * n

        # preferred_idx = user's choice at startup; never cascade above this.
        self._preferred_idx: int = config.ACTIVE_MODEL_INDEX
        # active_idx = currently used model (may cascade down under rate limits).
        self._active_idx: int = config.ACTIVE_MODEL_INDEX

        self._tokens_used: int = 0
        self._calls_made: int = 0

    # ------------------------------------------------------------------
    # Public stats / model info
    # ------------------------------------------------------------------

    def get_usage_stats(self) -> dict:
        return {"tokens_used": self._tokens_used, "calls_made": self._calls_made}

    def set_preferred_model(self, idx: int) -> None:
        """Change the preferred model mid-session."""
        self._preferred_idx = idx
        self._active_idx = idx
        config.set_active_model_index(idx)

    def get_active_model_rank(self) -> int:
        return config.MODEL_CATALOG[self._active_idx]["rank"]

    def get_active_model_name(self) -> str:
        return config.MODEL_CATALOG[self._active_idx]["short"]

    # ------------------------------------------------------------------
    # Model cascade helpers
    # ------------------------------------------------------------------

    def _model_has_headroom(self, idx: int) -> bool:
        """Return True if the model has at least one free slot in its RPM window."""
        call_times = self._model_call_times[idx]
        rpm = config.MODEL_CATALOG[idx]["rpm"]
        now = time.monotonic()
        while call_times and now - call_times[0] > 60.0:
            call_times.popleft()
        return len(call_times) < rpm

    def _try_upgrade(self) -> None:
        """
        Step back toward the preferred model only when it is genuinely ready:
        both the 429-penalty box has cleared AND there is RPM headroom.
        Skipping this check was causing upgrades that immediately hit the self-imposed
        rate limiter and slept, creating noticeable delays.
        """
        if self._active_idx <= self._preferred_idx:
            return
        now = time.monotonic()
        for idx in range(self._preferred_idx, self._active_idx):
            if self._model_rl_until[idx] > now:
                continue  # still in 429 penalty box
            if not self._model_has_headroom(idx):
                continue  # RPM window still full — upgrading would block immediately
            m = config.MODEL_CATALOG[idx]
            import ui
            ui.log("ok", f"Model upgrade → {m['short']}")
            self._active_idx = idx
            config.set_active_model_index(idx)
            break

    def _per_model_rate_limit(self, idx: int) -> None:
        """Enforce self-imposed per-model RPM limit (sliding 60s window)."""
        call_times = self._model_call_times[idx]
        rpm = config.MODEL_CATALOG[idx]["rpm"]
        now = time.monotonic()
        window = 60.0
        while call_times and now - call_times[0] > window:
            call_times.popleft()
        if len(call_times) >= rpm:
            sleep_for = window - (now - call_times[0]) + 0.5
            if sleep_for > 0:
                import ui
                ui.log("rate", f"{config.MODEL_CATALOG[idx]['short']} — sleeping {sleep_for:.1f}s")
                time.sleep(sleep_for)
        call_times.append(time.monotonic())

    # ------------------------------------------------------------------
    # Internal call wrapper with cascade
    # ------------------------------------------------------------------

    def _call(self, system: str, user: str, max_tokens: int | None = None) -> str:
        """
        Call the active model with automatic cascade on 429s.

        Tries models from active_idx downward (weaker).  If all are rate-limited,
        waits for the preferred model to clear.  After each successful call,
        tries to upgrade back toward the preferred model.
        """
        self._try_upgrade()
        catalog = config.MODEL_CATALOG
        now = time.monotonic()

        for idx in range(self._active_idx, len(catalog)):
            if self._model_rl_until[idx] > now:
                continue  # still in penalty box

            try:
                self._per_model_rate_limit(idx)
                response = self._client.chat.completions.create(
                    model=catalog[idx]["id"],
                    max_tokens=max_tokens if max_tokens is not None else config.MAX_TOKENS,
                    temperature=config.TEMPERATURE,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                )
                # Record cascade if we moved to a different model.
                if idx != self._active_idx:
                    m = catalog[idx]
                    import ui
                    ui.log("rate", f"Cascaded to {m['short']} (rank {m['rank']})")
                    self._active_idx = idx
                    config.set_active_model_index(idx)

                if response.usage:
                    self._tokens_used += response.usage.total_tokens
                self._calls_made += 1
                return response.choices[0].message.content.strip()

            except groq_sdk.RateLimitError:
                import ui
                self._model_rl_until[idx] = time.monotonic() + _RATE_LIMIT_PENALTY
                ui.log("rate", f"{catalog[idx]['short']} rate-limited — trying next model")
                now = time.monotonic()
                continue

        # Every model is in the penalty box.  Wait for the preferred one to clear.
        import ui
        wait_until = self._model_rl_until[self._preferred_idx]
        wait_for = max(1.0, wait_until - time.monotonic() + 0.5)
        m = catalog[self._preferred_idx]
        ui.log("rate", f"All models rate-limited — waiting {wait_for:.0f}s for {m['short']}")
        time.sleep(wait_for)
        self._model_rl_until[self._preferred_idx] = 0.0
        return self._call(system, user, max_tokens=max_tokens)  # retry

    def _call_json(self, system: str, user: str, retry_strict: bool = True) -> dict | None:
        """Call and parse JSON. Retries once with stricter prompt on parse error."""
        raw = self._call(system, user)
        try:
            return self._parse_json(raw)
        except (json.JSONDecodeError, ValueError):
            if not retry_strict:
                return None
            strict_system = (
                system
                + "\n\nCRITICAL: Your response MUST be valid JSON only. "
                "No text before or after the JSON object. No markdown code fences."
            )
            raw2 = self._call(strict_system, user)
            try:
                return self._parse_json(raw2)
            except (json.JSONDecodeError, ValueError):
                return None

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Extract JSON from response, stripping markdown fences if present."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
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
        Tags result with _model_rank so KnowledgeBase can apply corroboration rules.
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
            f"{config.RESEARCH_FOCUS}\n\n"
            "STRICT FILTERING RULES — only extract facts that are SPECIFIC and ACTIONABLE "
            "for the research focus above. A good fact is concrete and particular: it contains "
            "specific values, names, identifiers, measurements, or configurations. "
            "DO NOT extract: background definitions, general history, how-it-works overviews, "
            "or facts so generic they would appear in a Wikipedia introduction to the topic."
        )
        user = (
            f"Topic: {topic}\n\n"
            f"Existing knowledge on this topic: {existing_summary or 'None'}\n\n"
            f"New source material:\n{combined_pages}\n\n"
            f"Current timestamp: {now}\n\n"
            f"Extract ONLY facts that are specific and relevant to the research focus:\n"
            f"1. Key facts (with confidence high/medium/low, include source_url and added_at) — "
            f"skip generic definitions and background explanations\n"
            f"2. Conflicts with existing knowledge\n"
            f"3. 3-5 targeted follow-up research topics\n"
            f"4. A concise summary of on-topic findings only (empty string if nothing specific found)\n"
            f"5. Related topics already in my knowledge base\n\n"
            f"Return as JSON matching this schema:\n{_EXTRACTION_SCHEMA}"
        )

        result = self._call_json(system, user)
        if result is None:
            return None

        result.setdefault("summary", "")
        result.setdefault("facts", [])
        result.setdefault("conflicts", [])
        result.setdefault("follow_up_topics", [])
        result.setdefault("related_topics", [])

        # Tag with the model rank that produced this result.
        model_rank = config.MODEL_CATALOG[self._active_idx]["rank"]
        result["_model_rank"] = model_rank
        for fact in result["facts"]:
            fact.setdefault("model_rank", model_rank)

        # Post-extraction filter: drop facts that look like generic definitions
        result["facts"] = [f for f in result["facts"] if not _is_trivial_fact(f.get("content", ""))]

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
            f"3. Specific knowledge gaps that should be researched next\n\n"
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

        batch = config.GAP_TOPICS_BATCH

        if not all_summaries:
            if not seed_topic:
                return []
            user = (
                f"I am starting research on the topic: '{seed_topic}'\n\n"
                f"Suggest {batch} specific sub-topics "
                f"that would be most valuable to research first.\n\n"
                f"Return as JSON matching this schema:\n{_GAP_TOPICS_SCHEMA}"
            )
        else:
            summaries_text = "\n".join(
                f"[{k}]: {v}" for k, v in list(all_summaries.items())[:12]
            )
            user = (
                f"I have researched the following topics:\n{summaries_text}\n\n"
                f"Suggest {batch} new topics that would expand "
                f"this knowledge base and fill in the most important gaps.\n\n"
                f"Return as JSON matching this schema:\n{_GAP_TOPICS_SCHEMA}"
            )

        result = self._call_json(system, user)
        if result is None:
            return []
        return result.get("topics", [])[:batch]

    def generate_research_plan(self, seed_topic: str, research_focus: str) -> list[dict]:
        """
        Generate a structured research plan from a seed topic.
        Returns list of {phase: int, topic: str, rationale: str, priority: int}.
        """
        schema = """{
  "plan": [
    {"phase": 1, "topic": "specific sub-topic", "rationale": "why this first", "priority": 1},
    {"phase": 2, "topic": "another sub-topic", "rationale": "builds on phase 1", "priority": 2}
  ]
}"""
        system = (
            "You are a research strategist. Create a focused, ordered research plan. "
            "Each topic should be specific and searchable — concrete enough to find real sources. "
            "Return valid JSON only.\n\n"
            f"{research_focus}"
        )
        user = (
            f"Seed topic: {seed_topic}\n\n"
            f"Create a 10–15 step research plan. Order phases so foundational "
            f"knowledge comes first, then specific technical details, then edge cases. "
            f"Each topic should be something you could search for directly.\n\n"
            f"Return as JSON:\n{schema}"
        )
        result = self._call_json(system, user)
        if result is None:
            return []
        return result.get("plan", [])

    def evaluate_hypothesis(
        self, hypothesis: str, kb_facts: list[str]
    ) -> dict:
        """
        Evaluate a hypothesis against collected KB facts.
        Returns {status, confidence, evidence_for, evidence_against, reasoning}.
        """
        schema = """{
  "status": "confirmed|refuted|uncertain",
  "confidence": 0.0,
  "evidence_for": ["fact snippet..."],
  "evidence_against": ["fact snippet..."],
  "reasoning": "brief explanation"
}"""
        facts_text = "\n".join(f"- {f}" for f in kb_facts[:40])
        system = (
            "You are a research analyst evaluating hypotheses against evidence. "
            "Be rigorous — only mark confirmed if multiple high-confidence facts support it. "
            "Return valid JSON only."
        )
        user = (
            f"Hypothesis: {hypothesis}\n\n"
            f"Available facts:\n{facts_text}\n\n"
            f"Evaluate whether this hypothesis is supported, refuted, or uncertain "
            f"based on the facts above. Confidence should be 0.0–1.0.\n\n"
            f"Return as JSON:\n{schema}"
        )
        result = self._call_json(system, user)
        if result is None:
            return {"status": "uncertain", "confidence": 0.0,
                    "evidence_for": [], "evidence_against": [], "reasoning": "evaluation failed"}
        result.setdefault("status", "uncertain")
        result.setdefault("confidence", 0.0)
        result.setdefault("evidence_for", [])
        result.setdefault("evidence_against", [])
        result.setdefault("reasoning", "")
        return result

    def generate_synthesis_report(self, kb_data: dict, seed_topic: str, research_focus: str) -> str:
        """
        Use the best available model to synthesize all collected facts into a
        readable, categorized report. Returns plain text (not JSON).
        """
        knowledge = kb_data.get("knowledge", {})
        if not knowledge:
            return "No facts collected yet."

        # Collect all facts, sorted by confidence (high first), capped to avoid token overflow
        all_facts: list[str] = []
        for topic_key, entry in knowledge.items():
            for f in entry.get("facts", []):
                conf = f.get("confidence", "low")
                content = f.get("content", "").strip()
                if content:
                    all_facts.append(f"[{conf.upper()}] ({topic_key}) {content}")
        # Sort by confidence priority
        _conf_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        all_facts.sort(key=lambda x: _conf_order.get(x.split("]")[0].lstrip("["), 3))

        # Cap at ~80 facts to stay within token budget
        facts_text = "\n".join(all_facts[:80])
        if len(all_facts) > 80:
            facts_text += f"\n... ({len(all_facts) - 80} additional lower-confidence facts omitted)"

        # Also collect notable conflicts
        conflicts: list[str] = []
        for entry in knowledge.values():
            for c in entry.get("conflicts", []):
                note = c.get("note", "")
                if note:
                    conflicts.append(f"  - {note}")
        conflicts_text = "\n".join(conflicts[:10]) if conflicts else "  None identified."

        system = (
            "You are a technical research analyst. "
            "Write a clear, structured report for an engineer who wants to read CAN bus data "
            "from a specific vehicle. Do not repeat background definitions. "
            "Focus on what is actionable and specific."
        )
        user = (
            f"Research focus: {research_focus}\n\n"
            f"Collected facts (tagged with confidence and source topic):\n{facts_text}\n\n"
            f"Identified conflicts:\n{conflicts_text}\n\n"
            "Write a concise technical report organized into these sections:\n"
            "1. **Confirmed Frame IDs & Signals** — specific hex IDs and what they carry\n"
            "2. **OBD2 / SSM PIDs** — specific PID numbers, their meaning and scaling\n"
            "3. **Hardware & Tools** — specific tools, chipsets, wiring, or configurations confirmed to work\n"
            "4. **Conflicts & Uncertainties** — where sources disagree or data is unverified\n"
            "5. **Gaps — What We Still Need** — specific unknowns that would be most valuable to find\n\n"
            "Use plain text with markdown headers. Be direct and specific. "
            "Omit any section that has no relevant data. "
            "Do not include general background about CAN or OBD2."
        )

        # Use the preferred (best) model for synthesis; temporarily override active
        saved_active = self._active_idx
        self._active_idx = self._preferred_idx
        try:
            report_text = self._call(system, user, max_tokens=4096)
        finally:
            self._active_idx = saved_active

        return report_text

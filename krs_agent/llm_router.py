"""Cost-aware LLM routing over OpenRouter.

Every task starts on the cheapest model in the ladder. The call escalates to
the next (bigger) model when any of these happen:

1. transport/API error or empty response,
2. the model self-reports low confidence by replying ``ESCALATE: <reason>``
   (the router injects this instruction on every tier except the last),
3. the caller-supplied validator rejects the output (e.g. malformed JSON).

The last tier gets no escape hatch — it must answer.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

ESCALATE_PREFIX = "ESCALATE:"
ESCALATE_INSTRUCTION = (
    "You are one model in a cost-aware escalation ladder. If you are NOT "
    "confident you can complete this task correctly and completely, reply with "
    f"exactly '{ESCALATE_PREFIX} <one-line reason>' and nothing else, so a more "
    "capable model can take over. Never produce a partial or guessed answer."
)


class LLMError(RuntimeError):
    pass


@dataclass
class Attempt:
    task: str
    model: str
    outcome: str  # ok | escalated_self | escalated_invalid | escalated_error
    detail: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    seconds: float = 0.0


@dataclass
class Router:
    api_key: str
    ladder: list[str]
    app_title: str = "KRS-Investigator"
    temperature: float = 0.2
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _call(self, model: str, messages: list[dict], json_mode: bool) -> tuple[str, dict]:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "usage": {"include": True},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = httpx.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Title": self.app_title,
            },
            json=payload,
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise LLMError(str(data["error"]))
        content = (data["choices"][0]["message"].get("content") or "").strip()
        if not content:
            raise LLMError("empty response")
        return content, data.get("usage") or {}

    def run(
        self,
        task: str,
        messages: list[dict],
        validator=None,
        json_mode: bool = False,
        start_tier: int = 0,
    ) -> str:
        """Run `task` up the ladder, returning the first accepted output."""
        if not self.available:
            raise LLMError("OPENROUTER_API_KEY is not set")
        last_error = "no tiers tried"
        for i, model in enumerate(self.ladder[start_tier:], start=start_tier):
            is_last = i == len(self.ladder) - 1
            msgs = list(messages)
            if not is_last:
                msgs = [{"role": "system", "content": ESCALATE_INSTRUCTION}] + msgs
            t0 = time.time()
            attempt = Attempt(task=task, model=model, outcome="ok")
            try:
                content, usage = self._call(model, msgs, json_mode)
                attempt.prompt_tokens = usage.get("prompt_tokens", 0)
                attempt.completion_tokens = usage.get("completion_tokens", 0)
                attempt.cost = usage.get("cost", 0.0) or 0.0
            except Exception as exc:  # transport / API failure -> escalate
                attempt.outcome = "escalated_error"
                attempt.detail = str(exc)[:200]
                last_error = attempt.detail
                self._finish(attempt, t0)
                continue

            if content.startswith(ESCALATE_PREFIX):
                attempt.outcome = "escalated_self"
                attempt.detail = content[len(ESCALATE_PREFIX):].strip()[:200]
                last_error = f"{model} escalated: {attempt.detail}"
                self._finish(attempt, t0)
                continue

            if validator is not None:
                ok, err = validator(content)
                if not ok:
                    attempt.outcome = "escalated_invalid"
                    attempt.detail = err[:200]
                    last_error = f"{model} invalid output: {err}"
                    self._finish(attempt, t0)
                    continue

            self._finish(attempt, t0)
            return content
        raise LLMError(f"all tiers exhausted for task '{task}': {last_error}")

    def _finish(self, attempt: Attempt, t0: float) -> None:
        attempt.seconds = round(time.time() - t0, 2)
        self.attempts.append(attempt)
        note = f" ({attempt.detail})" if attempt.detail else ""
        print(
            f"[router] {attempt.task}: {attempt.model} -> {attempt.outcome}{note} "
            f"[{attempt.completion_tokens} tok, {attempt.seconds}s]",
            file=sys.stderr,
        )

    def summary(self) -> dict:
        return {
            "attempts": len(self.attempts),
            "escalations": sum(1 for a in self.attempts if a.outcome != "ok"),
            "total_cost_usd": round(sum(a.cost for a in self.attempts), 6),
            "by_model": {
                m: sum(1 for a in self.attempts if a.model == m)
                for m in {a.model for a in self.attempts}
            },
        }


def json_validator(required_keys: list[str] | None = None):
    """Validator factory: output must be a JSON object with the given keys."""

    def validate(content: str) -> tuple[bool, str]:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):text.rfind("}") + 1]
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            return False, f"not valid JSON: {exc}"
        if not isinstance(obj, dict):
            return False, "expected a JSON object"
        missing = [k for k in (required_keys or []) if k not in obj]
        if missing:
            return False, f"missing keys: {missing}"
        return True, ""

    return validate


def parse_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):text.rfind("}") + 1]
    return json.loads(text)

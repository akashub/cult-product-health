"""Per-review issue labelling with a judge.

For each review, one at a time:
  1. classifier (Haiku) -> issue codes + an evidence quote per code + severity
  2. code checks        -> quote must appear in the review; codes must exist
  3. judge (Sonnet)     -> agree / disagree / unsure
  4. status: 'auto' only if checks pass AND the judge agrees; otherwise 'queue'
     for a person. A person's decision is stored as human_verdict and becomes
     the gold set used to report agreement."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from ..config import env

PROMPT_VERSION = "v1"


class Issue(BaseModel):
    code: str = Field(description="One code from the taxonomy")
    evidence: str = Field(description="Exact words copied from the review that show this issue")


class Label(BaseModel):
    issues: list[Issue] = Field(description="Every product/service problem the review explicitly mentions; empty if none")
    sentiment: Literal["positive", "mixed", "negative"]
    severity: Literal["none", "low", "medium", "high", "safety"] = Field(
        description="safety = burning, overheating, shock, battery swelling, injury")


class Verdict(BaseModel):
    verdict: Literal["agree", "disagree", "unsure"]
    reason: str
    missing_codes: list[str] = Field(description="Codes the label should have included; empty if none")
    wrong_codes: list[str] = Field(description="Codes the label should not have included; empty if none")


def taxonomy_text(taxonomy: list[dict]) -> str:
    return "\n".join(f"- {t['code']}: {t['description']}" for t in taxonomy)


def review_text(r: dict) -> str:
    return f"Rating: {r['rating']}/5\nTitle: {r.get('title') or ''}\nReview: {r.get('body') or ''}"


def body_hash(r: dict) -> str:
    return hashlib.sha256(f"{r.get('title') or ''}\n{r.get('body') or ''}".encode()).hexdigest()[:16]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip(" .,!?\"'")


def check_label(label: Label, r: dict, codes: set[str]) -> list[str]:
    """Deterministic checks: every code exists and every quote is really in the review."""
    errs, text = [], _norm(f"{r.get('title') or ''} {r.get('body') or ''}")
    for i in label.issues:
        if i.code not in codes:
            errs.append(f"unknown code {i.code!r}")
        if not i.evidence.strip() or _norm(i.evidence) not in text:
            errs.append(f"evidence for {i.code} not found in review: {i.evidence!r}")
    if len({i.code for i in label.issues}) != len(label.issues):
        errs.append("duplicate codes")
    return errs


CLASSIFY_PROMPT = """You label one customer review of a {category} product sold online in India (Amazon, Flipkart, ...).

Issue taxonomy:
{taxonomy}

Rules:
- Only include issues the review explicitly states. Do not infer.
- For each issue, copy the exact words from the review as evidence (verbatim, no paraphrase).
- A review can have several issues or none. Praise is not an issue.
- Reviews may mix Hindi/Hinglish and English; quote them as written.

{review}"""

JUDGE_PROMPT = """You are checking another model's issue labels for one customer review.

Issue taxonomy:
{taxonomy}

{review}

Proposed label:
{label}

Agree only if every code is supported by the review and no clearly stated issue is missing.
Say "unsure" if the review is ambiguous."""


class Labeler:
    def __init__(self, taxonomy: list[dict], classifier_model: str, judge_model: str, client=None,
                 category: str = "massager / weighing scale", provider: str = "anthropic"):
        self.taxonomy = taxonomy
        self.codes = {t["code"] for t in taxonomy}
        self.classifier_model = classifier_model
        self.judge_model = judge_model
        self.category = category
        self.provider = provider
        self.client = client or make_client(provider=provider)
        self._tool_mode: set[str] = set()

    def _parse(self, model: str, prompt: str, schema):
        """Structured output first; if the API rejects the schema (400), fall back to
        a forced tool call with the same schema, and keep using it for this model."""
        messages = [{"role": "user", "content": prompt}]
        if self.provider == "openai":
            return self.client.responses.parse(model=model, input=messages, text_format=schema).output_parsed
        if model not in self._tool_mode:
            try:
                msg = self.client.messages.parse(model=model, max_tokens=1024, output_format=schema, messages=messages)
                return msg.parsed_output
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status_code", None) != 400:
                    raise
                self._tool_mode.add(model)
        msg = self.client.messages.create(
            model=model, max_tokens=1024, messages=messages,
            tools=[{"name": "submit", "description": f"Submit the {schema.__name__}",
                    "input_schema": schema.model_json_schema()}],
            tool_choice={"type": "tool", "name": "submit"})
        block = next(b for b in msg.content if b.type == "tool_use")
        return schema.model_validate(block.input)

    def label(self, r: dict) -> dict:
        tax = taxonomy_text(self.taxonomy)
        lab: Label = self._parse(self.classifier_model, CLASSIFY_PROMPT.format(
            category=self.category, taxonomy=tax, review=review_text(r)), Label)
        errs = check_label(lab, r, self.codes)
        ver: Verdict = self._parse(self.judge_model, JUDGE_PROMPT.format(
            taxonomy=tax, review=review_text(r), label=lab.model_dump_json(indent=1)), Verdict)
        status = "auto" if not errs and ver.verdict == "agree" else "queue"
        return {
            "review_id": r["review_id"], "prompt_version": PROMPT_VERSION, "body_hash": body_hash(r),
            "issues": json.dumps([i.model_dump() for i in lab.issues], ensure_ascii=False),
            "codes": ",".join(sorted({i.code for i in lab.issues})),
            "sentiment": lab.sentiment, "severity": lab.severity,
            "classifier_model": self.classifier_model, "judge_model": self.judge_model,
            "judge_verdict": ver.verdict, "judge_reason": ver.reason,
            "judge_missing": ",".join(ver.missing_codes), "judge_wrong": ",".join(ver.wrong_codes),
            "check_errors": "; ".join(errs), "status": status,
        }


def make_client(http_client=None, provider: str = "anthropic"):
    if provider == "openai":
        import openai

        key = env("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("No OPENAI_API_KEY: run `cultph setup` or put it in data/.env")
        return openai.OpenAI(api_key=key, http_client=http_client) if http_client else openai.OpenAI(api_key=key)

    import anthropic

    key = env("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("No ANTHROPIC_API_KEY: run `cultph setup` or put it in data/.env")
    return anthropic.Anthropic(api_key=key, http_client=http_client) if http_client else anthropic.Anthropic(api_key=key)

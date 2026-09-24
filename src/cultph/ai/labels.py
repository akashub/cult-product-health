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
import os
import re
from typing import Literal

from pydantic import BaseModel, Field

from ..config import DATA_DIR

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
    missing_codes: list[str] = Field(default_factory=list, description="Codes the label should have included")
    wrong_codes: list[str] = Field(default_factory=list, description="Codes the label should not have included")


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


CLASSIFY_PROMPT = """You label one customer review of a {category} product sold on Amazon India.

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
                 category: str = "massager / weighing scale"):
        self.taxonomy = taxonomy
        self.codes = {t["code"] for t in taxonomy}
        self.classifier_model = classifier_model
        self.judge_model = judge_model
        self.category = category
        self.client = client or make_client()

    def _parse(self, model: str, prompt: str, schema):
        msg = self.client.messages.parse(model=model, max_tokens=1024, output_format=schema,
                                         messages=[{"role": "user", "content": prompt}])
        return msg.parsed_output

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


def make_client():
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY")
    env_file = DATA_DIR / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        raise RuntimeError("Set ANTHROPIC_API_KEY or put it in data/.env")
    return anthropic.Anthropic(api_key=key)

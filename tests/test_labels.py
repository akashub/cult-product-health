"""Labelling pipeline with a fake model client (no API calls)."""

from types import SimpleNamespace

from cultph.ai import store as label_store
from cultph.ai.labels import PROMPT_VERSION, Issue, Label, Labeler, check_label
from cultph.ai.labels import Verdict as _Verdict


def Verdict(**kw):  # noqa: N802 - test helper mirroring the model with empty code lists
    return _Verdict(**{"missing_codes": [], "wrong_codes": [], **kw})
from cultph.amazon import store as amazon_store

TAX = [{"code": "charging", "description": "does not charge"}, {"code": "noise_vibration", "description": "noisy"}]
REVIEW = {"review_id": "R1", "rating": 1, "title": "Stopped working",
          "body": "Not  charging after a week. Makes a rattling noise."}


class FakeClient:
    """Returns queued outputs for the classifier and judge calls, in order."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.messages = self

    def parse(self, model, max_tokens, output_format, messages):
        self.calls.append((model, output_format.__name__, messages[0]["content"]))
        return SimpleNamespace(parsed_output=self.outputs.pop(0))


def labeler(outputs):
    return Labeler(TAX, "haiku", "sonnet", client=FakeClient(outputs))


GOOD = Label(issues=[Issue(code="charging", evidence="not charging after a week")], sentiment="negative", severity="medium")


def test_auto_when_checks_pass_and_judge_agrees():
    lab = labeler([GOOD, Verdict(verdict="agree", reason="ok")]).label(REVIEW)
    assert lab["status"] == "auto" and lab["codes"] == "charging" and lab["check_errors"] == ""


def test_queue_when_judge_disagrees():
    lab = labeler([GOOD, Verdict(verdict="disagree", reason="missed noise", missing_codes=["noise_vibration"])]).label(REVIEW)
    assert lab["status"] == "queue" and lab["judge_missing"] == "noise_vibration"


def test_queue_when_evidence_is_invented_even_if_judge_agrees():
    bad = Label(issues=[Issue(code="charging", evidence="battery exploded")], sentiment="negative", severity="safety")
    lab = labeler([bad, Verdict(verdict="agree", reason="ok")]).label(REVIEW)
    assert lab["status"] == "queue" and "not found in review" in lab["check_errors"]


def test_check_label_rules():
    codes = {"charging", "noise_vibration"}
    assert check_label(GOOD, REVIEW, codes) == []  # whitespace/case-insensitive quote match
    unknown = Label(issues=[Issue(code="overheat", evidence="rattling noise")], sentiment="negative", severity="low")
    assert any("unknown code" in e for e in check_label(unknown, REVIEW, codes))
    dup = Label(issues=[Issue(code="charging", evidence="charging"), Issue(code="charging", evidence="week")],
                sentiment="negative", severity="low")
    assert "duplicate codes" in check_label(dup, REVIEW, codes)


def test_one_review_per_call_and_models_used():
    fl = labeler([GOOD, Verdict(verdict="agree", reason="ok")])
    fl.label(REVIEW)
    (m1, s1, p1), (m2, s2, p2) = fl.client.calls
    assert (m1, s1, m2, s2) == ("haiku", "Label", "sonnet", "Verdict")
    assert "Not  charging after a week" in p1 and "charging" in p2


def test_store_relabels_changed_reviews_and_resets_human(tmp_path):
    con = amazon_store.connect(tmp_path / "a.db")
    amazon_store.upsert_reviews(con, "A1", "Gun A", [{**REVIEW, "review_date": None, "country": None,
                                                      "verified": True, "variant": None, "helpful_votes": 0}], "x")
    assert [r["review_id"] for r in label_store.pending_reviews(con, PROMPT_VERSION)] == ["R1"]
    lab = labeler([GOOD, Verdict(verdict="agree", reason="ok")]).label(REVIEW)
    label_store.save_label(con, lab)
    label_store.set_human(con, "R1", PROMPT_VERSION, "correct")
    assert label_store.pending_reviews(con, PROMPT_VERSION) == []
    con.execute("UPDATE review SET body = body || ' Update: now dead.' WHERE review_id='R1'")
    todo = label_store.pending_reviews(con, PROMPT_VERSION)
    assert [r["review_id"] for r in todo] == ["R1"]
    label_store.save_label(con, labeler([GOOD, Verdict(verdict="agree", reason="ok")]).label(todo[0]))
    assert con.execute("SELECT human_verdict FROM review_label").fetchone()[0] is None


def test_audit_sample_draws_only_auto_labels(tmp_path):
    con = amazon_store.connect(tmp_path / "a.db")
    base = {"review_date": None, "country": None, "verified": True, "variant": None, "helpful_votes": 0}
    reviews = [{**REVIEW, **base, "review_id": f"R{i}"} for i in range(40)]
    amazon_store.upsert_reviews(con, "A1", "Gun A", reviews, "x")
    con.execute("UPDATE review SET product = 'Gun A'")
    for i, r in enumerate(reviews):
        verdict = "agree" if i < 30 else "disagree"          # 30 auto, 10 queue
        label_store.save_label(con, labeler([GOOD, Verdict(verdict=verdict, reason="")]).label(r))
    n = label_store.sample_audits(con, PROMPT_VERSION, rate=0.10, min_per_group=3, seed=1)
    assert n == 3                                             # max(3, ceil(0.1*30))
    rows = con.execute("SELECT status FROM review_label WHERE audit = 1").fetchall()
    assert rows and all(st == "auto" for (st,) in rows)
    assert label_store.sample_audits(con, PROMPT_VERSION, rate=0.10, min_per_group=3, seed=2) == 0  # tops up only


# ---- through the real Anthropic SDK, with the network mocked ----
import json as _json

import anthropic
import httpx2


def _msg(content):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "m", "content": content,
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}


LABEL_JSON = {"issues": [{"code": "charging", "evidence": "not charging after a week"}],
              "sentiment": "negative", "severity": "medium"}
VERDICT_JSON = {"verdict": "agree", "reason": "ok", "missing_codes": [], "wrong_codes": []}


def _sdk(handler):
    return anthropic.Anthropic(api_key="test", max_retries=0,
                               http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def test_real_sdk_structured_output_path():
    sent = []

    def handler(req):
        body = _json.loads(req.content)
        sent.append(body)
        is_label = body["output_config"]["format"]["schema"]["title"] == "Label"
        return httpx2.Response(200, json=_msg([{"type": "text", "text": _json.dumps(LABEL_JSON if is_label else VERDICT_JSON)}]))

    lab = Labeler(TAX, "claude-haiku-4-5-20251001", "claude-sonnet-5", client=_sdk(handler)).label(REVIEW)
    assert lab["status"] == "auto" and lab["codes"] == "charging"
    assert [b["model"] for b in sent] == ["claude-haiku-4-5-20251001", "claude-sonnet-5"]
    for b in sent:  # strict-friendly schemas: every property required, no extras
        sch = b["output_config"]["format"]["schema"]
        assert sch["additionalProperties"] is False and set(sch["required"]) == set(sch["properties"])


def test_real_sdk_falls_back_to_tool_use_on_schema_400():
    sent = []

    def handler(req):
        body = _json.loads(req.content)
        sent.append(body)
        if "output_config" in body:
            return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error",
                                                                          "message": "output_config: unsupported schema"}})
        name = body["tools"][0]["input_schema"]["title"]
        return httpx2.Response(200, json=_msg([{"type": "tool_use", "id": "tu_1", "name": "submit",
                                                 "input": LABEL_JSON if name == "Label" else VERDICT_JSON}]))

    lb = Labeler(TAX, "h", "s", client=_sdk(handler))
    assert lb.label(REVIEW)["status"] == "auto"
    assert lb.label(REVIEW)["status"] == "auto"
    # first call per model tries structured output once, then tool mode sticks
    assert sum("output_config" in b for b in sent) == 2 and sum("tools" in b for b in sent) == 4

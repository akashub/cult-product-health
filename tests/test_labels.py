"""Labelling pipeline with a fake model client (no API calls)."""

from types import SimpleNamespace

from cultph.ai import store as label_store
from cultph.ai.labels import PROMPT_VERSION, Issue, Label, Labeler, Verdict, check_label
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

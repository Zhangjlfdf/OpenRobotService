"""归档派审桥接：不连库的纯函数单测。"""
from __future__ import annotations

from app.modules.tasks.services.archive_review_dispatch import assignment_log_detail


class _FakeAssignment:
    domain = "调度系统"
    reviewer_id = "rev_b"
    candidate_ids = ["rev_a", "rev_b"]
    load_snapshot = {"rev_a": 2, "rev_b": 0}
    probability_snapshot = {"rev_a": 0.3333, "rev_b": 0.6667}
    reason = "demo"


def test_assignment_log_detail_shape():
    detail = assignment_log_detail(_FakeAssignment())
    assert detail["domain"] == "调度系统"
    assert detail["reviewer"] == "rev_b"
    assert detail["source"] == "archive_review_dispatch"
    assert detail["load_snapshot"]["rev_b"] == 0
    assert detail["probability_snapshot"]["rev_b"] == 0.6667

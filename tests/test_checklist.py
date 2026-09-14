from dbe.checklist import approval_gate, missing_uploads, next_step, render_checklist

EMPTY = {"phase": "collecting", "adapter": None, "benchmark": None, "manifest_sha256": "a" * 64, "approvals": {}, "run": None}
BENCH = {"sha256": "b" * 64, "size": 10, "count": 10, "scored": False}
ADAPTER = {"sha256": "c" * 64, "size": 5, "lora_name": "private-adapter"}


def test_next_step_walks_the_sequence():
    assert "benchmark owner to upload" in next_step(EMPTY)
    s = {**EMPTY, "benchmark": BENCH}
    assert "model owner to upload" in next_step(s) and "Approve only after" in next_step(s)
    s = {**s, "adapter": ADAPTER}
    assert "either order" in next_step(s)
    s = {**s, "approvals": {"model-owner": True}}
    assert "Approval 1 of 2" in next_step(s) and "benchmark owner" in next_step(s)
    s = {**s, "run": {"status": "running", "completed": 3, "total": 10}}
    assert "Running: 3/10" in next_step(s)
    s = {**s, "run": {"status": "done"}}
    assert "dbe results" in next_step(s) and "dbe receipt get" in next_step(s)


def test_approval_gate_refuses_early_approvals():
    ok, msg = approval_gate(EMPTY, "model-owner")
    assert not ok and "benchmark owner has not uploaded" in msg
    ok, msg = approval_gate({**EMPTY, "benchmark": BENCH}, "benchmark-owner")
    assert not ok and "--without-adapter" in msg
    ok, _ = approval_gate({**EMPTY, "benchmark": BENCH}, "benchmark-owner", allow_without_adapter=True)
    assert ok
    ready = {**EMPTY, "benchmark": BENCH, "adapter": ADAPTER}
    assert approval_gate(ready, "benchmark-owner") == (True, "")
    ok, msg = approval_gate({**ready, "approvals": {"benchmark-owner": True}}, "benchmark-owner")
    assert not ok and "already approved" in msg
    ok, msg = approval_gate({**ready, "run": {"status": "running"}}, "model-owner")
    assert not ok and "in progress" in msg


def test_checklist_renders_boxes():
    text = render_checklist({**EMPTY, "benchmark": BENCH, "approvals": {"benchmark-owner": True}}, "enc.example")
    assert "[x] prompt set uploaded" in text and "[ ] adapter uploaded" in text
    assert "[x] benchmark owner approved" in text and "[ ] model owner approved" in text
    assert text.startswith("enc.example") and "Next:" in text
    assert missing_uploads(EMPTY) == ["benchmark-owner", "model-owner"]

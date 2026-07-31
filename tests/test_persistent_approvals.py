"""Persistent pre-approvals in the PermissionEngine: grants in the ApprovalStore
behave as an always-on session allowlist — with the spec's scope rule (external
send tools never get blanket grants, only target pins) enforced on read AND on
write (`grant_persistent`)."""

from types import SimpleNamespace

from coworker.approval_store import ApprovalStore
from coworker.permissions import Mode, PermissionEngine

# delete_scheduled_task-shaped: external risk (requires_approval) but NOT a connector
# and with no declared target argument — the blanket-eligible case.
AUTOMATION_META = SimpleNamespace(requires_approval=True, category="")
CONNECTOR_META = SimpleNamespace(requires_approval=True, category="connector")
TARGET = "whatsapp_evolution:5511999999999"


def _engine(tmp_path, **kwargs):
    return PermissionEngine(
        workspace_root=tmp_path,
        mode=Mode.INTERACTIVE,
        persistent=ApprovalStore(tmp_path / "approvals.json"),
        **kwargs,
    )


def test_permanent_tool_grant_skips_approval(tmp_path):
    engine = _engine(tmp_path)
    before = engine.evaluate("delete_scheduled_task", {"id": "task-1"}, AUTOMATION_META)
    assert before.needs_user

    engine.persistent.grant_tool("delete_scheduled_task")
    after = engine.evaluate("delete_scheduled_task", {"id": "task-1"}, AUTOMATION_META)
    assert after.allowed and "permanently" in after.reason


def test_permanent_command_grant_skips_approval(tmp_path):
    engine = _engine(tmp_path)
    engine.persistent.grant_command("git status")
    ok = engine.evaluate("run_shell", {"command": "git status"}, None)
    assert ok.allowed and "permanently" in ok.reason
    other = engine.evaluate("run_shell", {"command": "git push"}, None)
    assert other.needs_user


def test_blanket_grant_for_connector_send_tool_is_ignored(tmp_path):
    engine = _engine(tmp_path)
    engine.persistent.grant_tool("send_message")  # e.g. hand-edited file
    decision = engine.evaluate("send_message", {"target": TARGET}, CONNECTOR_META)
    assert decision.needs_user


def test_blanket_grant_for_external_targeted_tool_is_ignored(tmp_path):
    # Even without the connector category, external risk + a declared target argument
    # makes a blanket grant ineligible (spec scope rule).
    engine = _engine(tmp_path)
    engine.persistent.grant_tool("send_message")
    meta = SimpleNamespace(requires_approval=True, category="")
    assert engine.evaluate("send_message", {"target": TARGET}, meta).needs_user


def test_permanent_target_grant_allows_exact_target_only(tmp_path):
    engine = _engine(tmp_path)
    engine.persistent.grant_target("send_message", TARGET)

    hit = engine.evaluate("send_message", {"target": TARGET}, CONNECTOR_META)
    assert hit.allowed and hit.rule == f"send_message → {TARGET}"

    miss = engine.evaluate(
        "send_message", {"target": "whatsapp_evolution:5511888888888"}, CONNECTOR_META
    )
    assert miss.needs_user


def test_grant_persistent_routes_by_scope(tmp_path):
    engine = _engine(tmp_path)

    assert (
        engine.grant_persistent("run_shell", {"command": "git status"}, None)
        == "command: git status"
    )
    assert engine.persistent.allow_commands() == {"git status"}

    assert (
        engine.grant_persistent("send_message", {"target": TARGET}, CONNECTOR_META)
        == f"send_message → {TARGET}"
    )
    assert engine.persistent.allow_targets() == {"send_message": {TARGET}}
    assert "send_message" not in engine.persistent.allow_tools()

    # A targeted external tool with no actual target: refused outright.
    assert engine.grant_persistent("send_message", {}, CONNECTOR_META) == ""
    assert engine.persistent.allow_tools() == set()

    assert (
        engine.grant_persistent("delete_scheduled_task", {"id": "x"}, AUTOMATION_META)
        == "tool: delete_scheduled_task"
    )
    assert engine.persistent.allow_tools() == {"delete_scheduled_task"}


def test_grants_survive_a_fresh_engine(tmp_path):
    _engine(tmp_path).grant_persistent(
        "delete_scheduled_task", {"id": "x"}, AUTOMATION_META
    )
    fresh = _engine(tmp_path)
    ok = fresh.evaluate("delete_scheduled_task", {"id": "y"}, AUTOMATION_META)
    assert ok.allowed


def test_engine_without_store_behaves_as_before(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE)
    assert engine.grant_persistent("delete_scheduled_task", {}, AUTOMATION_META) == ""
    decision = engine.evaluate("delete_scheduled_task", {}, AUTOMATION_META)
    assert decision.needs_user

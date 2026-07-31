"""ApprovalStore — the persistent "always allow" file. Grants must survive process
restarts, tolerate a corrupt file, and pick up external edits (mtime reload) so a
change from the Settings screen applies to live sessions without a restart."""

import json

from coworker.approval_store import ApprovalStore


def test_grants_round_trip_across_instances(tmp_path):
    path = tmp_path / "approvals.json"
    store = ApprovalStore(path)
    store.grant_tool("delete_scheduled_task")
    store.grant_command("git status")
    store.grant_target("send_message", "whatsapp_evolution:5511999999999")

    reopened = ApprovalStore(path)
    assert reopened.allow_tools() == {"delete_scheduled_task"}
    assert reopened.allow_commands() == {"git status"}
    assert reopened.allow_targets() == {
        "send_message": {"whatsapp_evolution:5511999999999"}
    }


def test_revoke_each_kind(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.json")
    store.grant_tool("delete_scheduled_task")
    store.grant_command("git status")
    store.grant_target("send_message", "whatsapp_evolution:5511999999999")

    store.revoke_tool("delete_scheduled_task")
    store.revoke_command("git status")
    store.revoke_target("send_message", "whatsapp_evolution:5511999999999")

    assert store.allow_tools() == set()
    assert store.allow_commands() == set()
    assert store.allow_targets() == {}


def test_missing_and_corrupt_files_read_as_empty(tmp_path):
    assert ApprovalStore(tmp_path / "nope.json").allow_tools() == set()

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    store = ApprovalStore(bad)
    assert store.allow_tools() == set()
    assert store.allow_commands() == set()
    assert store.allow_targets() == {}

    # non-dict payloads are ignored too
    bad.write_text(json.dumps(["list"]), encoding="utf-8")
    assert ApprovalStore(bad).allow_tools() == set()


def test_external_edit_is_picked_up(tmp_path):
    path = tmp_path / "approvals.json"
    store = ApprovalStore(path)
    assert store.allow_tools() == set()

    path.write_text(
        json.dumps({"allow_tools": ["delete_scheduled_task"]}), encoding="utf-8"
    )
    import os

    os.utime(path, (0, 2**31 - 1))  # force a different mtime than any cached one
    assert store.allow_tools() == {"delete_scheduled_task"}


def test_grant_is_idempotent_and_file_stays_valid_json(tmp_path):
    path = tmp_path / "approvals.json"
    store = ApprovalStore(path)
    store.grant_tool("delete_scheduled_task")
    store.grant_tool("delete_scheduled_task")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["allow_tools"] == ["delete_scheduled_task"]


def test_snapshot_shape(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.json")
    store.grant_target("send_message", "whatsapp_evolution:5511999999999")
    snap = store.snapshot()
    assert snap == {
        "allow_tools": [],
        "allow_commands": [],
        "allow_targets": {"send_message": ["whatsapp_evolution:5511999999999"]},
    }

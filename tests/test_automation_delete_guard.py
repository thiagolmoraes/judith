"""Deleting a scheduled task must be verifiable and hard to do by accident.

Real incident (2026-07-31): "Pode deletar, já fiz" (about a medicine reminder) was
applied to an eye-doctor appointment reminder six days in the future — the most recently
created task. The tool answered `{"ok": true, "id": "task-98e09ad63f"}`, which named
nothing the user could recognize, so the mistake surfaced only days later.

Two properties fix that: the result must SAY what was destroyed, and a task that is
unlikely to be the intended target must be named back to the user before it is gone.
"""

from coworker.automation.models import ScheduledTask, Schedule
from coworker.automation.store import TaskStore
from coworker.automation.tools import scheduling_tools


def _tools(tmp_path):
    store = TaskStore(str(tmp_path / "automation.db"))
    fns = scheduling_tools(
        store,
        origin={"surface": "assistant", "session_id": "s1"},
        default_workspace=str(tmp_path),
    )
    return store, {f.__name__: f for f in fns}


def _task(store, title, cron="0 9 * * *"):
    task = ScheduledTask(
        title=title,
        instructions=f"Enviar '{title}'",
        schedule=Schedule(kind="cron", cron=cron, timezone="America/Sao_Paulo"),
        workspace="/tmp",
        agent="assistant",
    )
    store.save(task)
    return task


def test_delete_names_what_it_destroyed(tmp_path):
    # `{"ok": true, "id": "task-98e09ad63f"}` told nobody WHAT was deleted. The title and
    # schedule in the result are what let the model — and the person reading its reply —
    # notice the wrong thing went.
    store, tools = _tools(tmp_path)
    task = _task(store, "Lembrete de consulta oftalmológica")

    result = tools["delete_scheduled_task"](id=task.id)

    assert result["ok"] is True
    assert result["title"] == "Lembrete de consulta oftalmológica"
    assert result["schedule"]
    assert store.get(task.id) is None


def test_deleting_an_unknown_id_is_an_explicit_failure(tmp_path):
    store, tools = _tools(tmp_path)
    result = tools["delete_scheduled_task"](id="task-nope")
    assert result["ok"] is False
    assert "no such task" in result["error"]


def test_delete_without_an_id_lists_the_candidates_instead_of_guessing(tmp_path):
    # The failure mode itself: an ambiguous "delete it" must come back as a question
    # naming the options, never as a destructive guess.
    store, tools = _tools(tmp_path)
    _task(store, "Lembrete de medicamento")
    _task(store, "Lembrete de consulta oftalmológica")

    result = tools["delete_scheduled_task"](id="")

    assert result["ok"] is False
    assert result.get("needs_disambiguation") is True
    titles = [c["title"] for c in result["candidates"]]
    assert "Lembrete de medicamento" in titles
    assert "Lembrete de consulta oftalmológica" in titles
    assert len(store.list()) == 2  # nothing destroyed while asking


def test_disambiguation_with_a_single_task_still_requires_the_id(tmp_path):
    # Even with one candidate the tool refuses to infer: the caller names it, or asks.
    store, tools = _tools(tmp_path)
    _task(store, "Bom dia diário")

    result = tools["delete_scheduled_task"](id=None)

    assert result["ok"] is False
    assert len(store.list()) == 1



# -- a wake in the past ---------------------------------------------------------
def test_sleep_until_refuses_a_past_timestamp(tmp_path):
    """A user asked for "good morning at 8am"; the model computed a date three months
    back, the timer fired instantly, and the message arrived in the middle of the
    night. Nothing checked that the target was in the future.

    Refused rather than clamped: firing immediately looks to the user like the agent
    ignored the time they asked for. The error carries the current time so the model
    can correct itself on the next call."""
    from coworker.selfwake import WakeStore, selfwake_tools

    tools = {t.__name__: t for t in selfwake_tools(WakeStore(tmp_path / "w.json"), "s1")}
    result = tools["sleep_until"]("2020-01-01T08:00:00+00:00")
    assert "error" in result
    assert "in the past" in result["error"]
    assert "It is now" in result["error"]
    assert "wake_id" not in result


def test_sleep_until_still_accepts_the_future(tmp_path):
    from datetime import datetime, timedelta, timezone

    from coworker.selfwake import WakeStore, selfwake_tools

    tools = {t.__name__: t for t in selfwake_tools(WakeStore(tmp_path / "w.json"), "s1")}
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert tools["sleep_until"](when)["ok"] is True


def test_sleep_until_reports_an_unparseable_timestamp(tmp_path):
    """It used to raise ValueError out of the tool call. An error the model can read
    lets it retry with a real timestamp."""
    from coworker.selfwake import WakeStore, selfwake_tools

    tools = {t.__name__: t for t in selfwake_tools(WakeStore(tmp_path / "w.json"), "s1")}
    result = tools["sleep_until"]("tomorrow morning")
    assert "error" in result and "ISO-8601" in result["error"]

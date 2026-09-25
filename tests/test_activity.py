from observability.activity import recent_events, record_event, reset_events


def test_activity_is_process_local_and_sanitized_by_caller():
    reset_events()
    event = record_event("mcp.tools.discover", "completed", tool_count=7, latency_ms=12)

    assert event["sequence"] == 1
    assert recent_events()[0]["metadata"] == {"tool_count": 7, "latency_ms": 12}


def test_activity_drops_unapproved_metadata():
    reset_events()
    record_event(
        "mcp.tools.discover",
        "error",
        reason="connection_failed",
        prompt="forbidden-marker",
        path="/forbidden-marker",
        exception="forbidden-marker",
    )

    assert recent_events()[0]["metadata"] == {"reason": "connection_failed"}

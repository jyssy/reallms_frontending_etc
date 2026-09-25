import json

import pytest

from observability.contract import ContractError, parse_observed_result, parse_trace_event


def valid_event(**overrides):
    event = {
        "contract_version": 1,
        "run_id": "00000000-0000-4000-8000-000000000001",
        "sequence": 1,
        "timestamp": "2026-09-24T12:00:00+00:00",
        "event_type": "provider.attempt",
        "component": "provider",
        "status": "success",
        "duration_ms": 4,
        "metadata": {"provider": "remote", "attempt": 1},
    }
    event.update(overrides)
    return event


def test_trace_parser_rebuilds_from_allowlist():
    event = valid_event(
        metadata={
            "provider": "remote",
            "attempt": 1,
            "prompt": "forbidden-marker",
            "path": "/forbidden-marker",
            "exception": "forbidden-marker",
        },
        completion="forbidden-marker",
    )

    parsed = parse_trace_event(event)

    assert parsed["metadata"] == {"provider": "remote", "attempt": 1}
    assert "forbidden-marker" not in json.dumps(parsed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract_version", 2),
        ("run_id", "not-a-uuid"),
        ("sequence", 0),
        ("timestamp", "not-a-time"),
        ("event_type", "prompt.received"),
        ("component", "filesystem"),
        ("status", "unknown"),
        ("duration_ms", -1),
    ],
)
def test_trace_parser_rejects_invalid_contract_fields(field, value):
    with pytest.raises(ContractError):
        parse_trace_event(valid_event(**{field: value}))


def test_result_projection_discards_content_paths_policy_and_raw_errors():
    result = parse_observed_result(
        {
            "status": "success",
            "task_type": "coding",
            "model_roles": {
                "reviewer": "safe-model",
                "judge": "bad model name with spaces",
            },
            "final": "forbidden-marker",
            "draft": "forbidden-marker",
            "repo_root": "/forbidden-marker",
            "policy_fingerprint": "forbidden-marker",
            "error": {"message": "forbidden-marker"},
        }
    )

    assert result == {
        "status": "success",
        "task_type": "coding",
        "model_roles": {"reviewer": "safe-model"},
    }
    assert "forbidden-marker" not in json.dumps(result)

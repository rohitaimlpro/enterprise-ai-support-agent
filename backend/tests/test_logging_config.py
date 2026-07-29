import json
import logging

from app.observability.logging_config import JsonFormatter, get_logger, log_event


def test_log_event_emits_valid_json_with_extra_fields(caplog):
    logger = get_logger("test.logger")
    formatter = JsonFormatter()

    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="chat_turn",
        args=(),
        exc_info=None,
    )
    record.extra_fields = {"latency_ms": 120, "tool_calls": ["lookup_order"]}

    payload = json.loads(formatter.format(record))
    assert payload["message"] == "chat_turn"
    assert payload["latency_ms"] == 120
    assert payload["tool_calls"] == ["lookup_order"]
    assert payload["level"] == "INFO"

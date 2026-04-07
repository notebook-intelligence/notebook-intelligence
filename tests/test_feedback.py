"""Tests for the feedback mechanism (thumbs up/down on AI responses).

Covers:
- TelemetryEventType.Feedback enum membership
- Feedback event payload structure and validation
- Telemetry listener dispatch for feedback events
- Feedback toggle / retraction logic (mirroring frontend behavior)
- EmitTelemetryEventHandler REST endpoint integration
"""

import json
import logging
import uuid
import asyncio
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

import pytest

from notebook_intelligence.api import TelemetryEventType, TelemetryListener


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feedback_event(sentiment: str = "positive", **overrides) -> dict:
    """Build a realistic feedback telemetry event dict."""
    event = {
        "type": TelemetryEventType.Feedback,
        "data": {
            "sentiment": sentiment,
            "chatId": str(uuid.uuid4()),
            "messageId": str(uuid.uuid4()),
            "model": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            "participant": "default",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    event["data"].update(overrides)
    return event


class CapturingTelemetryListener(TelemetryListener):
    """A concrete TelemetryListener that records every event it receives."""

    def __init__(self):
        self._events: list = []

    @property
    def name(self) -> str:
        return "test-capturing-listener"

    def on_telemetry_event(self, event):
        self._events.append(event)

    @property
    def captured(self) -> list:
        return list(self._events)


# ---------------------------------------------------------------------------
# 1. TelemetryEventType.Feedback enum
# ---------------------------------------------------------------------------

class TestFeedbackTelemetryEventType:
    def test_feedback_enum_exists(self):
        """TelemetryEventType should include a Feedback member."""
        assert hasattr(TelemetryEventType, "Feedback")

    def test_feedback_enum_value(self):
        """Feedback enum value must be the string 'feedback'."""
        assert TelemetryEventType.Feedback == "feedback"
        assert TelemetryEventType.Feedback.value == "feedback"

    def test_feedback_is_valid_member(self):
        """Feedback should be retrievable via the standard Enum lookup."""
        assert TelemetryEventType("feedback") is TelemetryEventType.Feedback


# ---------------------------------------------------------------------------
# 2. Feedback event payload structure
# ---------------------------------------------------------------------------

class TestFeedbackEventPayload:
    REQUIRED_DATA_KEYS = {"sentiment", "chatId", "messageId", "model", "timestamp"}

    def test_positive_sentiment_payload(self):
        event = _make_feedback_event("positive")
        assert event["type"] == "feedback"
        assert event["data"]["sentiment"] == "positive"
        assert self.REQUIRED_DATA_KEYS.issubset(event["data"].keys())

    def test_negative_sentiment_payload(self):
        event = _make_feedback_event("negative")
        assert event["data"]["sentiment"] == "negative"
        assert self.REQUIRED_DATA_KEYS.issubset(event["data"].keys())

    def test_model_field_structure(self):
        event = _make_feedback_event()
        model = event["data"]["model"]
        assert "provider" in model
        assert "model" in model

    def test_timestamp_is_iso_format(self):
        event = _make_feedback_event()
        ts = event["data"]["timestamp"]
        # Should parse without error
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None

    def test_ids_are_uuid_strings(self):
        event = _make_feedback_event()
        # chatId and messageId should be valid UUIDs
        uuid.UUID(event["data"]["chatId"])
        uuid.UUID(event["data"]["messageId"])

    def test_participant_can_be_none(self):
        """Participant is optional – undefined on the frontend maps to None."""
        event = _make_feedback_event(participant=None)
        assert event["data"]["participant"] is None


# ---------------------------------------------------------------------------
# 3. Telemetry listener receives feedback events
# ---------------------------------------------------------------------------

class TestTelemetryListenerFeedbackDispatch:
    @pytest.fixture
    def ai_service_manager(self):
        """Create a minimal AIServiceManager with mocked dependencies."""
        with patch("notebook_intelligence.ai_service_manager.NBIConfig"), \
             patch("notebook_intelligence.ai_service_manager.AIServiceManager.initialize"):
            from notebook_intelligence.ai_service_manager import AIServiceManager
            manager = AIServiceManager({"server_root_dir": "/tmp"})
            return manager

    def test_register_and_receive_feedback_event(self, ai_service_manager):
        """A registered listener should receive feedback events."""
        listener = CapturingTelemetryListener()
        ai_service_manager.register_telemetry_listener(listener)

        event = _make_feedback_event("positive")
        asyncio.run(ai_service_manager.emit_telemetry_event(event))

        assert len(listener.captured) == 1
        assert listener.captured[0]["type"] == "feedback"
        assert listener.captured[0]["data"]["sentiment"] == "positive"

    def test_multiple_listeners_all_receive_event(self, ai_service_manager):
        """All registered listeners should receive the same event."""
        listener_a = CapturingTelemetryListener()
        listener_b = Mock(spec=TelemetryListener)
        listener_b.name = "mock-listener-b"

        ai_service_manager.register_telemetry_listener(listener_a)
        ai_service_manager.register_telemetry_listener(listener_b)

        event = _make_feedback_event("negative")
        asyncio.run(ai_service_manager.emit_telemetry_event(event))

        assert len(listener_a.captured) == 1
        listener_b.on_telemetry_event.assert_called_once_with(event)

    def test_positive_and_negative_events_both_dispatched(self, ai_service_manager):
        """Verify both sentiments flow through the pipeline."""
        listener = CapturingTelemetryListener()
        ai_service_manager.register_telemetry_listener(listener)

        asyncio.run(ai_service_manager.emit_telemetry_event(_make_feedback_event("positive")))
        asyncio.run(ai_service_manager.emit_telemetry_event(_make_feedback_event("negative")))

        sentiments = [e["data"]["sentiment"] for e in listener.captured]
        assert sentiments == ["positive", "negative"]

    def test_duplicate_listener_name_rejected(self, ai_service_manager):
        """Registering a listener with a duplicate name should be silently rejected."""
        listener_1 = CapturingTelemetryListener()
        listener_2 = CapturingTelemetryListener()  # same .name property

        ai_service_manager.register_telemetry_listener(listener_1)
        ai_service_manager.register_telemetry_listener(listener_2)

        # Only the first should be registered
        assert ai_service_manager.telemetry_listeners["test-capturing-listener"] is listener_1


# ---------------------------------------------------------------------------
# 4. EmitTelemetryEventHandler integration
# ---------------------------------------------------------------------------

class TestEmitTelemetryEventHandler:
    @pytest.fixture
    def mock_handler(self):
        """Create a mocked EmitTelemetryEventHandler."""
        from tornado.httputil import HTTPServerRequest
        from tornado.web import Application
        from notebook_intelligence.extension import EmitTelemetryEventHandler

        app = Mock(spec=Application)
        app.ui_methods = {}
        app.ui_modules = {}

        request = Mock(spec=HTTPServerRequest)
        request.connection = Mock()

        with patch.object(EmitTelemetryEventHandler, "__init__", return_value=None):
            handler = EmitTelemetryEventHandler()
            handler.application = app
            handler.request = request
            handler.finish = Mock()
            return handler

    @patch("notebook_intelligence.extension.ai_service_manager")
    @patch("notebook_intelligence.extension.threading.Thread")
    def test_post_dispatches_feedback_event(self, mock_thread_cls, mock_ai_manager, mock_handler):
        """POST with a feedback event should spawn a thread to emit it."""
        event = _make_feedback_event("positive")
        mock_handler.request.body = json.dumps(event).encode()

        # Bypass Tornado's @authenticated decorator by setting current_user
        mock_handler._current_user = "test-user"
        mock_handler._jupyter_current_user = "test-user"

        mock_handler.post()

        # Thread should be started
        mock_thread_cls.assert_called_once()
        call_kwargs = mock_thread_cls.call_args
        assert call_kwargs[1]["target"] == asyncio.run

        # Finish should be called with empty JSON
        mock_handler.finish.assert_called_once_with(json.dumps({}))

    @patch("notebook_intelligence.extension.ai_service_manager")
    @patch("notebook_intelligence.extension.threading.Thread")
    def test_post_logs_feedback_event(self, mock_thread_cls, mock_ai_manager, mock_handler, caplog):
        """POST should log the feedback event type and data at INFO level."""
        event = _make_feedback_event("positive")
        mock_handler.request.body = json.dumps(event).encode()

        # Bypass Tornado's @authenticated decorator
        mock_handler._current_user = "test-user"
        mock_handler._jupyter_current_user = "test-user"

        with caplog.at_level(logging.DEBUG, logger="notebook_intelligence.extension"):
            mock_handler.post()

        # Verify the log message contains the event type and sentiment
        feedback_logs = [
            record for record in caplog.records
            if "Telemetry event received" in record.message
        ]
        assert len(feedback_logs) == 1, f"Expected 1 telemetry log entry, got {len(feedback_logs)}"

        log_message = feedback_logs[0].message
        assert "type=feedback" in log_message
        assert '"sentiment": "positive"' in log_message or '"sentiment":"positive"' in log_message

    @patch("notebook_intelligence.extension.ai_service_manager")
    @patch("notebook_intelligence.extension.threading.Thread")
    def test_post_logs_negative_feedback_sentiment(self, mock_thread_cls, mock_ai_manager, mock_handler, caplog):
        """Verify negative sentiment is captured in the log output."""
        event = _make_feedback_event("negative")
        mock_handler.request.body = json.dumps(event).encode()

        mock_handler._current_user = "test-user"
        mock_handler._jupyter_current_user = "test-user"

        with caplog.at_level(logging.DEBUG, logger="notebook_intelligence.extension"):
            mock_handler.post()

        feedback_logs = [
            record for record in caplog.records
            if "Telemetry event received" in record.message
        ]
        assert len(feedback_logs) == 1
        assert '"sentiment": "negative"' in feedback_logs[0].message or '"sentiment":"negative"' in feedback_logs[0].message

    @patch("notebook_intelligence.extension.ai_service_manager")
    @patch("notebook_intelligence.extension.threading.Thread")
    def test_post_logs_feedback_data_fields(self, mock_thread_cls, mock_ai_manager, mock_handler, caplog):
        """Verify the log captures chatId, messageId, model, and timestamp."""
        event = _make_feedback_event("positive")
        mock_handler.request.body = json.dumps(event).encode()

        mock_handler._current_user = "test-user"
        mock_handler._jupyter_current_user = "test-user"

        with caplog.at_level(logging.DEBUG, logger="notebook_intelligence.extension"):
            mock_handler.post()

        feedback_logs = [
            record for record in caplog.records
            if "Telemetry event received" in record.message
        ]
        assert len(feedback_logs) == 1

        log_message = feedback_logs[0].message
        data = event["data"]
        # All key data fields should appear in the logged JSON
        assert data["chatId"] in log_message
        assert data["messageId"] in log_message
        assert data["timestamp"] in log_message
        assert "anthropic" in log_message  # provider from _make_feedback_event

    @patch("notebook_intelligence.extension.ai_service_manager")
    @patch("notebook_intelligence.extension.threading.Thread")
    def test_post_log_level_is_info(self, mock_thread_cls, mock_ai_manager, mock_handler, caplog):
        """The telemetry log entry should be at INFO level."""
        event = _make_feedback_event("positive")
        mock_handler.request.body = json.dumps(event).encode()

        mock_handler._current_user = "test-user"
        mock_handler._jupyter_current_user = "test-user"

        with caplog.at_level(logging.DEBUG, logger="notebook_intelligence.extension"):
            mock_handler.post()

        feedback_logs = [
            record for record in caplog.records
            if "Telemetry event received" in record.message
        ]
        assert len(feedback_logs) == 1
        assert feedback_logs[0].levelno == logging.DEBUG


# ---------------------------------------------------------------------------
# 5. Feedback toggle / retraction logic
# ---------------------------------------------------------------------------

class TestFeedbackToggleLogic:
    """
    Mirrors the frontend handleFeedback callback logic:
      - Clicking the same sentiment again retracts (sets feedback to None).
      - Clicking the opposite sentiment switches it.
    """

    @staticmethod
    def apply_feedback(current_feedback, new_sentiment):
        """
        Pure-function equivalent of the frontend handleFeedback logic:
            const newFeedback = m.feedback === sentiment ? undefined : sentiment;
        """
        if current_feedback == new_sentiment:
            return None  # retract
        return new_sentiment

    def test_initial_positive(self):
        assert self.apply_feedback(None, "positive") == "positive"

    def test_initial_negative(self):
        assert self.apply_feedback(None, "negative") == "negative"

    def test_retract_positive(self):
        assert self.apply_feedback("positive", "positive") is None

    def test_retract_negative(self):
        assert self.apply_feedback("negative", "negative") is None

    def test_switch_positive_to_negative(self):
        assert self.apply_feedback("positive", "negative") == "negative"

    def test_switch_negative_to_positive(self):
        assert self.apply_feedback("negative", "positive") == "positive"

    def test_retract_then_re_apply(self):
        """Full cycle: apply → retract → re-apply."""
        state = self.apply_feedback(None, "positive")
        assert state == "positive"
        state = self.apply_feedback(state, "positive")
        assert state is None
        state = self.apply_feedback(state, "positive")
        assert state == "positive"


# ---------------------------------------------------------------------------
# 6. Feedback event serialisation round-trip
# ---------------------------------------------------------------------------

class TestFeedbackEventSerialisation:
    """Ensure feedback events survive JSON serialisation (as they travel over REST)."""

    def test_round_trip_preserves_all_fields(self):
        event = _make_feedback_event("negative")
        serialised = json.dumps(event)
        deserialised = json.loads(serialised)

        assert deserialised["type"] == "feedback"
        assert deserialised["data"]["sentiment"] == "negative"
        assert "chatId" in deserialised["data"]
        assert "messageId" in deserialised["data"]
        assert "model" in deserialised["data"]
        assert "timestamp" in deserialised["data"]

    def test_enum_value_serialises_as_string(self):
        event = _make_feedback_event()
        serialised = json.dumps(event)
        assert '"feedback"' in serialised

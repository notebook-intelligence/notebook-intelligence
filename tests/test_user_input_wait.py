"""Regression coverage for the user-input wait accounting on ChatResponse.

#381: a tool approval that the user takes a long time to confirm used to
count toward the Claude response timeout, so a slow human reply timed out the
turn and left the worker mid-request, wedging the next prompt. ChatResponse now
tracks the time it spends blocked on user input so the timeout can exclude it.
"""

import asyncio
import time

from notebook_intelligence import api as api_module
from notebook_intelligence.api import ChatResponse


class _Response(ChatResponse):
    @property
    def message_id(self) -> str:
        return "msg-1"

    def stream(self, data, finish: bool = False) -> None:
        pass

    def finish(self) -> None:
        pass


class TestUserInputWaitSeconds:
    def test_starts_at_zero(self):
        assert _Response().user_input_wait_seconds == 0.0

    def test_counts_a_wait_in_progress(self):
        resp = _Response()
        resp._user_input_wait_count = 1
        resp._user_input_wait_started = time.monotonic() - 5
        assert resp.user_input_wait_seconds >= 5

    def test_accumulates_completed_waits(self):
        resp = _Response()
        resp._user_input_wait_total = 12.0
        # No wait in progress -> just the accumulated total.
        assert resp.user_input_wait_seconds == 12.0
        # A wait in progress adds on top of the accumulated total.
        resp._user_input_wait_count = 1
        resp._user_input_wait_started = time.monotonic() - 3
        assert resp.user_input_wait_seconds >= 15

    def test_concurrent_waits_count_one_contiguous_interval(self, monkeypatch):
        resp = _Response()
        clock = {"now": 0.0}
        monkeypatch.setattr(
            api_module.time,
            "monotonic",
            lambda: clock["now"],
        )

        async def run():
            first = resp.prepare_chat_user_input("first")
            second = resp.prepare_chat_user_input("second")
            first_task = asyncio.create_task(
                resp.wait_for_chat_user_input(resp, "first", first)
            )
            second_task = asyncio.create_task(
                resp.wait_for_chat_user_input(resp, "second", second)
            )
            await asyncio.sleep(0)
            assert resp._user_input_wait_count == 2

            clock["now"] = 5.0
            resp.on_user_input({"callback_id": "first", "data": "first"})
            assert await first_task == "first"
            assert resp.user_input_wait_seconds == 5.0

            clock["now"] = 8.0
            resp.on_user_input({"callback_id": "second", "data": "second"})
            assert await second_task == "second"

        asyncio.run(run())

        assert resp._user_input_wait_count == 0
        assert resp._user_input_wait_started is None
        assert resp.user_input_wait_seconds == 8.0


class TestWaitForChatUserInput:
    def _drive(self, confirmed_after: float):
        resp = _Response()

        async def run():
            task = asyncio.create_task(
                ChatResponse.wait_for_chat_user_input(resp, "cb-1")
            )
            await asyncio.sleep(confirmed_after)
            resp.on_user_input({"callback_id": "cb-1", "data": {"confirmed": True}})
            return await task

        result = asyncio.run(run())
        return resp, result

    def test_records_wait_time_and_clears_in_progress(self):
        resp, result = self._drive(0.25)
        assert result == {"confirmed": True}
        # The wait was accounted for ...
        assert resp.user_input_wait_seconds >= 0.2
        # ... and is no longer in progress, so the value is now stable.
        assert resp._user_input_wait_started is None
        first = resp.user_input_wait_seconds
        time.sleep(0.05)
        assert resp.user_input_wait_seconds == first

    def test_disconnects_listener_after_answer(self):
        resp, _ = self._drive(0.05)
        # The wait's listener was removed (the finally disconnect), so no
        # dangling subscription survives the answer. This fails if the
        # disconnect ever regresses out of the finally.
        assert resp.user_input_signal._listeners == []
        # A late, unmatched input must not raise.
        resp.on_user_input({"callback_id": "cb-1", "data": {"confirmed": False}})

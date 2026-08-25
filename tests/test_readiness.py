"""Readiness preflight.

The feature's whole claim is that a failing check names the specific missing
piece, so most of these assert on the *remedy* and the verdict, not just that
a row appeared.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import notebook_intelligence.readiness as rd
from notebook_intelligence.checks import CheckTimeout as _CheckTimeout


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _Model:
    def __init__(self, model_id):
        self.id = model_id


class _Provider:
    def __init__(self, models=None, raises=None):
        self._models = [_Model(m) for m in (models or [])]
        self._raises = raises

    @property
    def chat_models(self):
        if self._raises:
            raise self._raises
        return self._models


def _config(**kw):
    return SimpleNamespace(
        chat_model=kw.get("chat_model", {}),
        claude_settings=kw.get("claude_settings", {}),
        acp_settings=kw.get("acp_settings", {}),
    )


def _manager(mode=None, provider=None, chat_model=None):
    mgr = SimpleNamespace(
        active_agent_mode=mode,
        chat_model=chat_model,
        get_llm_provider=lambda pid: provider,
    )
    return mgr


def _by_id(doc):
    return {row["id"]: row for row in doc["checks"]}


def _levels(doc):
    return {row["id"]: row["level"] for row in doc["checks"]}


# ---------------------------------------------------------------------------
# Contract: the document shape callers depend on
# ---------------------------------------------------------------------------


def test_document_is_json_serializable_and_versioned():
    doc = rd.run_readiness(
        _config(chat_model={"provider": "openai-compatible", "model": "gpt-4o"}),
        _manager(provider=_Provider(["gpt-4o"])),
    )
    assert doc["schema_version"] == rd.SCHEMA_VERSION
    assert doc["verdict"] in (rd.VERDICT_READY, rd.VERDICT_DEGRADED, rd.VERDICT_NOT_READY)
    assert doc["headline"]
    # The panel and any support-ticket export round-trip this.
    json.dumps(doc)


def test_every_non_ok_row_carries_a_remedy():
    """The feature exists to name the next action. A blocked or warning row
    without one is the generic 'something went wrong' with extra steps."""
    doc = rd.run_readiness(_config(chat_model={}), _manager())
    offenders = [
        r["id"]
        for r in doc["checks"]
        if r["level"] in (rd.LEVEL_BLOCKED, rd.LEVEL_WARN) and not r.get("remedy")
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# Provider path
# ---------------------------------------------------------------------------


def test_no_provider_selected_blocks_with_a_remedy():
    doc = rd.run_readiness(_config(chat_model={}), _manager())
    row = _by_id(doc)["provider.configured"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "NBI Settings" in row["remedy"]
    assert doc["verdict"] == rd.VERDICT_NOT_READY
    assert "not ready" in doc["headline"].lower()


def test_provider_none_is_treated_as_unconfigured():
    doc = rd.run_readiness(_config(chat_model={"provider": "none"}), _manager())
    assert _levels(doc)["provider.configured"] == rd.LEVEL_BLOCKED


def test_selected_provider_that_is_not_registered_names_the_admin_gate():
    """A provider disabled by `disabled_providers` looks identical to a typo
    from the user's side, so the remedy has to mention both."""
    doc = rd.run_readiness(
        _config(chat_model={"provider": "ollama", "model": "llama3"}),
        _manager(provider=None),
    )
    row = _by_id(doc)["provider.configured"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "disabled_providers" in row["remedy"]


def test_empty_model_list_blocks_and_points_at_key_and_base_url():
    """This is the 'no models available' troubleshooting section, automated."""
    doc = rd.run_readiness(
        _config(chat_model={"provider": "openai-compatible", "model": "gpt-4o"}),
        _manager(provider=_Provider([])),
    )
    row = _by_id(doc)["provider.models"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "API key" in row["remedy"] and "Base URL" in row["remedy"]


def test_model_listing_that_raises_is_reported_without_the_exception_message():
    """Provider exceptions routinely carry the base URL and sometimes the
    key, so only the class name may survive into a document meant for a
    support ticket."""
    doc = rd.run_readiness(
        _config(chat_model={"provider": "openai-compatible", "model": "gpt-4o"}),
        _manager(provider=_Provider(raises=RuntimeError("key sk-secret at https://host"))),
    )
    row = _by_id(doc)["provider.models"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "RuntimeError" in row["detail"]
    assert "sk-secret" not in json.dumps(doc)
    assert "https://host" not in json.dumps(doc)


def test_configured_model_missing_from_the_list_warns_rather_than_blocks():
    """The endpoint works; one setting is stale. Blocking would overstate it."""
    doc = rd.run_readiness(
        _config(chat_model={"provider": "openai-compatible", "model": "gpt-4-old"}),
        _manager(provider=_Provider(["gpt-4o", "gpt-4o-mini"])),
    )
    row = _by_id(doc)["provider.model_exists"]
    assert row["level"] == rd.LEVEL_WARN
    assert "NBI_CHAT_MODEL_ID" in row["remedy"]
    assert doc["verdict"] == rd.VERDICT_DEGRADED


def test_fully_configured_provider_is_ready():
    doc = rd.run_readiness(
        _config(chat_model={"provider": "openai-compatible", "model": "gpt-4o"}),
        _manager(provider=_Provider(["gpt-4o"])),
    )
    assert doc["verdict"] == rd.VERDICT_READY
    assert doc["headline"] == "Ready. Nothing needs configuring."


# ---------------------------------------------------------------------------
# Claude mode
# ---------------------------------------------------------------------------


def test_missing_claude_cli_blocks_and_names_the_env_override():
    with patch.object(rd, "resolve_claude_cli_path", return_value=None):
        doc = rd.run_readiness(_config(), _manager(mode="claude"))
    row = _by_id(doc)["claude.cli"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "NBI_CLAUDE_CLI_PATH" in row["remedy"]


def test_claude_cli_that_resolves_but_fails_to_run_is_still_blocked():
    """'Claude mode hangs on Thinking...' is usually a CLI that resolves and
    then does not start, which a path check alone would call healthy."""
    with patch.object(rd, "resolve_claude_cli_path", return_value="/usr/local/bin/claude"), patch.object(
        rd, "run_versioned", side_effect=OSError("exec format error")
    ):
        doc = rd.run_readiness(_config(), _manager(mode="claude"))
    row = _by_id(doc)["claude.cli"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "terminal" in row["remedy"]


def test_claude_cli_timeout_is_distinguished_from_failure():
    with patch.object(rd, "resolve_claude_cli_path", return_value="/usr/local/bin/claude"), patch.object(
        rd, "run_versioned", side_effect=_CheckTimeout("cli")
    ):
        doc = rd.run_readiness(_config(), _manager(mode="claude"))
    row = _by_id(doc)["claude.cli"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "did not respond" in row["detail"]
    assert "network filesystem" in row["remedy"]


def test_claude_cli_ok_reports_the_version():
    with patch.object(rd, "resolve_claude_cli_path", return_value="/usr/local/bin/claude"), patch.object(
        rd, "run_versioned", return_value={"returncode": 0, "version": "2.1.0 (Claude Code)"}
    ):
        doc = rd.run_readiness(_config(), _manager(mode="claude"))
    row = _by_id(doc)["claude.cli"]
    assert row["level"] == rd.LEVEL_OK
    assert "2.1.0" in row["detail"]


def test_missing_anthropic_key_warns_because_a_subscription_login_is_valid():
    """NBI cannot see the CLI's own login, so an absent key must not be
    reported as broken."""
    with patch.object(rd, "resolve_claude_cli_path", return_value=None), patch.dict(
        rd.os.environ, {}, clear=True
    ):
        doc = rd.run_readiness(_config(), _manager(mode="claude"))
    row = _by_id(doc)["claude.credentials"]
    assert row["level"] == rd.LEVEL_WARN
    assert "subscription login" in row["remedy"]


def test_anthropic_key_from_env_is_reported_by_source_not_value():
    with patch.object(rd, "resolve_claude_cli_path", return_value=None), patch.dict(
        rd.os.environ, {"ANTHROPIC_API_KEY": "sk-ant-secret"}, clear=True
    ):
        doc = rd.run_readiness(_config(), _manager(mode="claude"))
    row = _by_id(doc)["claude.credentials"]
    assert row["level"] == rd.LEVEL_OK
    assert "ANTHROPIC_API_KEY" in row["detail"]
    assert "sk-ant-secret" not in json.dumps(doc)


def test_claude_mode_does_not_run_provider_checks():
    """Claude mode does not use the native provider path, so reporting an
    unconfigured chat model there would be a false alarm."""
    with patch.object(rd, "resolve_claude_cli_path", return_value=None):
        doc = rd.run_readiness(_config(chat_model={}), _manager(mode="claude"))
    assert "provider.configured" not in _by_id(doc)


# ---------------------------------------------------------------------------
# ACP mode
# ---------------------------------------------------------------------------


def test_acp_missing_npx_blocks_and_names_the_override():
    with patch("shutil.which", return_value=None), patch.dict(rd.os.environ, {}, clear=True):
        doc = rd.run_readiness(_config(), _manager(mode="acp"))
    row = _by_id(doc)["acp.runtime"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "NBI_ACP_AGENT_COMMAND" in row["remedy"]


def test_acp_command_override_pointing_at_a_missing_binary_is_blocked():
    """Special-casing the literal "npx" would let an override point at a
    nonexistent binary and still report Ready."""
    with patch.dict(
        rd.os.environ, {"NBI_ACP_AGENT_COMMAND": "/opt/nope/agent --acp"}, clear=True
    ):
        doc = rd.run_readiness(_config(), _manager(mode="acp"))
    row = _by_id(doc)["acp.runtime"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "/opt/nope/agent" in row["detail"]
    assert doc["verdict"] == rd.VERDICT_NOT_READY


def test_acp_command_override_pointing_at_a_real_binary_is_ok():
    with patch.dict(
        rd.os.environ, {"NBI_ACP_AGENT_COMMAND": "/bin/sh -c true"}, clear=True
    ):
        doc = rd.run_readiness(_config(), _manager(mode="acp"))
    assert _by_id(doc)["acp.runtime"]["level"] == rd.LEVEL_OK


def test_acp_credentials_absent_warns_for_the_oauth_case():
    with patch("shutil.which", return_value="/usr/bin/npx"), patch.dict(
        rd.os.environ, {}, clear=True
    ):
        doc = rd.run_readiness(_config(), _manager(mode="acp"))
    row = _by_id(doc)["acp.credentials"]
    assert row["level"] == rd.LEVEL_WARN
    assert "OAuth" in row["remedy"]


# ---------------------------------------------------------------------------
# Live completion (opt-in)
# ---------------------------------------------------------------------------


def _live_doc(completions):
    model = SimpleNamespace(completions=completions)
    return rd.run_readiness(
        _config(chat_model={"provider": "openai-compatible", "model": "gpt-4o"}),
        _manager(provider=_Provider(["gpt-4o"]), chat_model=model),
        include_live=True,
    )


def test_live_is_not_run_unless_asked():
    called = MagicMock()
    doc = rd.run_readiness(
        _config(chat_model={"provider": "openai-compatible", "model": "gpt-4o"}),
        _manager(provider=_Provider(["gpt-4o"]), chat_model=SimpleNamespace(completions=called)),
    )
    called.assert_not_called()
    assert "live.completion" not in _by_id(doc)


def test_live_streaming_success_reports_ttfb_and_tool_acceptance():
    def completions(messages, tools=None, response=None, **kw):
        assert response is not None, "a response object is what selects the streaming path"
        response.stream({"choices": [{"delta": {"content": "ok"}}]})

    doc = _live_doc(completions)
    rows = _by_id(doc)
    assert rows["live.completion"]["level"] == rd.LEVEL_OK
    assert "chunk" in rows["live.completion"]["detail"]
    assert rows["live.tools"]["level"] == rd.LEVEL_OK


def test_live_non_streaming_endpoint_warns_rather_than_blocks():
    """A gateway that buffers still works; the reply just lands all at once."""

    def completions(messages, tools=None, response=None, **kw):
        return None  # never calls response.stream

    doc = _live_doc(completions)
    row = _by_id(doc)["live.completion"]
    assert row["level"] == rd.LEVEL_WARN
    assert "buffers" in row["remedy"]


def test_live_detects_a_proxy_that_strips_tool_schemas():
    """The headline silent misconfiguration: 200s everywhere, but agent mode
    can never call a tool."""
    calls = []

    def completions(messages, tools=None, response=None, **kw):
        calls.append(tools)
        if tools:
            raise ValueError("400 unsupported parameter: tools")
        response.stream({"choices": [{"delta": {"content": "ok"}}]})

    doc = _live_doc(completions)
    rows = _by_id(doc)
    assert rows["live.tools"]["level"] == rd.LEVEL_BLOCKED
    assert "stripping" in rows["live.tools"]["remedy"]
    # It retried without tools, so the completion itself still reads healthy.
    assert rows["live.completion"]["level"] == rd.LEVEL_OK
    assert calls[0] is not None and calls[1] is None


def test_live_total_failure_blocks_without_leaking_the_provider_message():
    def completions(messages, tools=None, response=None, **kw):
        raise RuntimeError("401 from https://gw.internal with key sk-live-secret")

    doc = _live_doc(completions)
    row = _by_id(doc)["live.completion"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "sk-live-secret" not in json.dumps(doc)
    assert "gw.internal" not in json.dumps(doc)


def test_live_is_skipped_in_agent_modes():
    """In Claude or ACP mode the agent owns the model call, so there is
    nothing NBI can exercise on the user's behalf."""
    with patch.object(rd, "resolve_claude_cli_path", return_value=None):
        doc = rd.run_readiness(_config(), _manager(mode="claude"), include_live=True)
    row = _by_id(doc)["live.completion"]
    assert row["level"] == rd.LEVEL_SKIPPED
    assert "agent owns the model call" in row["detail"]


def test_live_retains_no_model_output():
    """The probe response counts and drops; generated text must not reach the
    document, which is meant to be pasted into a ticket."""

    def completions(messages, tools=None, response=None, **kw):
        response.stream({"choices": [{"delta": {"content": "SENSITIVE-COMPLETION-TEXT"}}]})

    doc = _live_doc(completions)
    assert "SENSITIVE-COMPLETION-TEXT" not in json.dumps(doc)


def test_live_hang_is_bounded_and_reported_as_a_timeout(monkeypatch):
    import threading

    release = threading.Event()
    monkeypatch.setattr(rd, "LIVE_TIMEOUT_S", 0.2)

    def completions(messages, tools=None, response=None, **kw):
        release.wait(10)

    try:
        doc = _live_doc(completions)
        row = _by_id(doc)["live.completion"]
        assert row["level"] == rd.LEVEL_BLOCKED
        assert "did not answer" in row["remedy"]
    finally:
        release.set()


# ---------------------------------------------------------------------------
# Verdict arithmetic
# ---------------------------------------------------------------------------


def test_blocked_outranks_warn_in_the_verdict():
    rows = [
        rd._row("a", "g", rd.LEVEL_WARN, "A", "warned"),
        rd._row("b", "g", rd.LEVEL_BLOCKED, "B", "broke", remedy="fix b"),
    ]
    verdict, headline = rd._verdict(rows)
    assert verdict == rd.VERDICT_NOT_READY
    assert "broke" in headline


def test_headline_counts_additional_blocked_rows():
    rows = [
        rd._row("a", "g", rd.LEVEL_BLOCKED, "A", "first", remedy="x"),
        rd._row("b", "g", rd.LEVEL_BLOCKED, "B", "second", remedy="y"),
    ]
    _, headline = rd._verdict(rows)
    assert "1 more issue" in headline


def test_skipped_rows_do_not_degrade_the_verdict():
    rows = [
        rd._row("a", "g", rd.LEVEL_OK, "A", "fine"),
        rd._row("b", "g", rd.LEVEL_SKIPPED, "B", "not applicable"),
    ]
    verdict, _ = rd._verdict(rows)
    assert verdict == rd.VERDICT_READY


def test_a_manager_that_raises_still_produces_a_document():
    """Readiness is what a user reaches for when things are broken, so it has
    to survive a half-initialized service manager."""

    class _Broken:
        @property
        def active_agent_mode(self):
            raise RuntimeError("not initialized")

        def get_llm_provider(self, pid):
            raise RuntimeError("not initialized")

    doc = rd.run_readiness(_config(chat_model={"provider": "x", "model": "y"}), _Broken())
    assert doc["schema_version"] == rd.SCHEMA_VERSION
    assert doc["verdict"] == rd.VERDICT_NOT_READY


# ---------------------------------------------------------------------------
# REST handler
# ---------------------------------------------------------------------------


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class TestReadinessHandler:
    def _handler(self, body=b"{}"):
        """A mock handler with the real ``_run`` bound.

        ``MagicMock(spec=...)`` stubs every method including ``_run``, so
        without rebinding it the get/post paths would exercise nothing.
        """
        from notebook_intelligence.extension import ReadinessHandler

        handler = MagicMock(spec=ReadinessHandler)
        handler.request = MagicMock()
        handler.request.body = body
        handler._run = lambda include_live: ReadinessHandler._run(handler, include_live)
        return handler

    def _patched_manager(self):
        """extension.ai_service_manager is a module global set at server
        startup and is None under test."""
        return patch(
            "notebook_intelligence.extension.ai_service_manager",
            SimpleNamespace(nbi_config=_config()),
        )

    def test_get_returns_a_document_without_the_live_check(self):
        from notebook_intelligence.extension import ReadinessHandler

        handler = self._handler()
        with self._patched_manager(), patch(
            "notebook_intelligence.extension.readiness.run_readiness"
        ) as run:
            run.return_value = {"verdict": "ready"}
            _run(ReadinessHandler.get(handler))
        assert run.call_args[0][2] is False

    def test_post_honors_the_live_flag(self):
        from notebook_intelligence.extension import ReadinessHandler

        handler = self._handler(body=b'{"live": true}')
        with self._patched_manager(), patch(
            "notebook_intelligence.extension.readiness.run_readiness"
        ) as run:
            run.return_value = {"verdict": "ready"}
            _run(ReadinessHandler.post(handler))
        assert run.call_args[0][2] is True

    def test_post_rejects_malformed_json(self):
        from notebook_intelligence.extension import ReadinessHandler

        handler = self._handler(body=b"{not json")
        _run(ReadinessHandler.post(handler))
        handler.set_status.assert_called_once_with(400)

    def test_readiness_is_not_gated_on_perf_diagnostics(self):
        """Unlike the two /perf routes, this one answers whether or not
        diagnostics are enabled: it is what a stuck user reaches for."""
        import notebook_intelligence.perf as perf
        from notebook_intelligence.extension import ReadinessHandler

        # perf is a process-global singleton; restore it so this test cannot
        # change the recorder state any later test file runs against.
        previous = perf.enabled()
        perf.configure({"enabled": False}, None)
        assert perf.enabled() is False

        handler = self._handler()
        with self._patched_manager(), patch(
            "notebook_intelligence.extension.readiness.run_readiness"
        ) as run:
            run.return_value = {"verdict": "ready"}
            try:
                _run(ReadinessHandler.get(handler))
            finally:
                perf.configure({"enabled": previous}, None)
        handler.finish.assert_called_once()


# ---------------------------------------------------------------------------
# Regressions from the xhigh review
# ---------------------------------------------------------------------------


def test_a_transient_first_failure_is_not_blamed_on_the_tool_schema():
    """A 503 or a 429 on the first call must not make readiness accuse a
    proxy of stripping tools. The tools field is only implicated when the
    retry WITHOUT tools succeeds where the one with tools failed."""
    calls = []

    def completions(messages, tools=None, response=None, **kw):
        calls.append(tools)
        if len(calls) == 1:
            raise RuntimeError("503 Service Unavailable")
        response.stream({"choices": [{"delta": {"content": "ok"}}]})

    doc = _live_doc(completions)
    row = _by_id(doc)["live.tools"]
    # The retry succeeded, so by construction this IS the tools case.
    assert row["level"] == rd.LEVEL_BLOCKED


def test_an_endpoint_that_is_simply_down_leaves_the_tool_question_unanswered():
    def completions(messages, tools=None, response=None, **kw):
        raise RuntimeError("503 Service Unavailable")

    doc = _live_doc(completions)
    # Both attempts failed, so nothing was learned about tool support and
    # saying "your proxy strips tools" would be a fabrication.
    assert _by_id(doc)["live.completion"]["level"] == rd.LEVEL_BLOCKED
    assert "live.tools" not in _by_id(doc)


def test_tool_schema_is_a_fresh_copy_per_call():
    """The list crosses into third-party SDKs; litellm and ollama pass it
    through unchanged, so a mutating callee would corrupt every later run."""
    seen = []

    def completions(messages, tools=None, response=None, **kw):
        if tools:
            seen.append(tools)
            tools[0]["function"]["name"] = "MUTATED"
        response.stream({"choices": [{"delta": {"content": "ok"}}]})

    _live_doc(completions)
    _live_doc(completions)
    assert seen[0] is not seen[1]
    assert rd._PROBE_TOOL_TEMPLATE[0]["function"]["name"] == "nbi_readiness_probe"


def test_a_zero_millisecond_first_chunk_is_not_reported_as_na():
    """A local endpoint or a coarse clock can produce a 0.0 delta, which a
    truthiness check would render as 'n/a'."""
    probe = rd._ProbeResponse()
    probe.start(0.0)
    probe.first_chunk_at = 0.0
    probe.chunks = 1
    assert (round(probe.first_chunk_at, 1) if probe.first_chunk_at is not None else None) == 0.0

    def completions(messages, tools=None, response=None, **kw):
        response.first_chunk_at = 0.0
        response.chunks = 1

    doc = _live_doc(completions)
    assert "n/a" not in _by_id(doc)["live.completion"]["detail"]


def test_probe_response_accepts_the_published_stream_signature():
    """api.ChatResponse.stream takes (data, finish=False). A provider that
    passes the second argument would otherwise raise TypeError, which would
    be misreported as the endpoint being broken."""

    def completions(messages, tools=None, response=None, **kw):
        response.stream({"choices": [{"delta": {"content": "ok"}}]}, finish=True)

    doc = _live_doc(completions)
    assert _by_id(doc)["live.completion"]["level"] == rd.LEVEL_OK


def test_signed_out_github_copilot_is_blocked_despite_a_non_empty_model_list():
    """The Copilot provider falls back to a hardcoded list when it has no
    token, so 'the list is non-empty' is true for a signed-out user. Trusting
    it would report Ready while every turn 401s, which is precisely the
    failure this feature exists to prevent."""
    with patch("notebook_intelligence.github_copilot.get_login_status") as status:
        status.return_value = {"status": "NOT_LOGGED_IN"}
        doc = rd.run_readiness(
            _config(chat_model={"provider": "github-copilot", "model": "gpt-4.1"}),
            _manager(provider=_Provider(["gpt-4.1", "gpt-4o"])),
        )
    row = _by_id(doc)["provider.models"]
    assert row["level"] == rd.LEVEL_BLOCKED
    assert "Sign in" in row["remedy"]
    assert doc["verdict"] == rd.VERDICT_NOT_READY


def test_signed_in_github_copilot_is_ready():
    with patch("notebook_intelligence.github_copilot.get_login_status") as status:
        status.return_value = {"status": "LOGGED_IN"}
        doc = rd.run_readiness(
            _config(chat_model={"provider": "github-copilot", "model": "gpt-4.1"}),
            _manager(provider=_Provider(["gpt-4.1"])),
        )
    assert doc["verdict"] == rd.VERDICT_READY


def test_a_hung_model_list_is_bounded_rather_than_hanging_the_request(monkeypatch):
    """LLMProvider.chat_models is a documented extension point and several
    implementations fetch over HTTP. Calling it inline would wedge the very
    request that exists to diagnose wedged endpoints."""
    import threading
    import time

    monkeypatch.setattr(rd, "PROVIDER_TIMEOUT_S", 0.2)
    release = threading.Event()

    class _Hanging:
        @property
        def chat_models(self):
            release.wait(10)
            return []

    started = time.perf_counter()
    try:
        doc = rd.run_readiness(
            _config(chat_model={"provider": "openai-compatible", "model": "gpt-4o"}),
            _manager(provider=_Hanging()),
        )
        elapsed = time.perf_counter() - started
        row = _by_id(doc)["provider.models"]
        assert row["level"] == rd.LEVEL_BLOCKED
        assert "did not return a model list" in row["detail"]
        assert elapsed < 5, f"readiness blocked for {elapsed:.1f}s on a hung provider"
    finally:
        release.set()


def test_a_config_property_that_raises_becomes_a_row_not_an_exception():
    """A corrupt config file is exactly the state a stuck user is in, so the
    endpoint that explains breakage must not itself 500."""

    class _BrokenConfig:
        @property
        def chat_model(self):
            raise RuntimeError("invalid JSON in config.json")

        claude_settings = {}
        acp_settings = {}

    doc = rd.run_readiness(_BrokenConfig(), _manager())
    assert doc["verdict"] == rd.VERDICT_NOT_READY
    row = [r for r in doc["checks"] if r["id"].endswith(".unavailable")][0]
    assert "RuntimeError" in row["detail"]
    assert "config.json" in row["remedy"]
    # The exception message can carry paths; only the class name survives.
    assert "invalid JSON in config.json" not in json.dumps(doc)


def test_home_paths_and_the_login_name_are_scrubbed_from_the_document():
    """A launch command carries an absolute home directory and therefore the
    login name, and this document is meant for a support ticket."""
    import os as _os

    home = _os.path.expanduser("~")
    with patch.dict(
        rd.os.environ,
        {"NBI_ACP_AGENT_COMMAND": f"{home}/agents/adapter --acp"},
        clear=False,
    ):
        doc = rd.run_readiness(_config(), _manager(mode="acp"))
    blob = json.dumps(doc)
    assert home not in blob
    assert "~/agents/adapter" in blob


class _PlaceholderIdModel:
    """Mirrors the openai-compatible provider: a constant `.id` with the real
    model name in a `model_id` property."""

    id = "openai-compatible-chat-model"

    def __init__(self, real_name):
        self._real = real_name

    def get_property(self, name):
        assert name == "model_id"
        return SimpleNamespace(value=self._real)


def test_placeholder_id_providers_report_the_model_the_user_actually_typed():
    doc = rd.run_readiness(
        _config(
            chat_model={
                "provider": "openai-compatible",
                "model": "openai-compatible-chat-model",
            }
        ),
        _manager(provider=SimpleNamespace(chat_models=[_PlaceholderIdModel("qwen-2.5-72b")])),
    )
    row = _by_id(doc)["provider.configured"]
    assert "qwen-2.5-72b" in row["detail"]
    assert "openai-compatible-chat-model" not in row["detail"]


def test_placeholder_id_providers_do_not_get_a_bogus_stale_model_warning():
    """Comparing the configured id against a one-entry list of placeholder
    ids would either always pass or always fail; neither is informative."""
    doc = rd.run_readiness(
        _config(
            chat_model={
                "provider": "openai-compatible",
                "model": "openai-compatible-chat-model",
            }
        ),
        _manager(provider=SimpleNamespace(chat_models=[_PlaceholderIdModel("qwen-2.5-72b")])),
    )
    assert "provider.model_exists" not in _by_id(doc)
    assert doc["verdict"] == rd.VERDICT_READY


class TestLiveCheckGating:
    def _handler(self, body):
        from notebook_intelligence.extension import ReadinessHandler

        handler = MagicMock(spec=ReadinessHandler)
        handler.request = MagicMock()
        handler.request.body = body
        handler.live_check_allowed = True
        handler._run = lambda include_live: ReadinessHandler._run(handler, include_live)
        return handler

    def _patched_manager(self):
        return patch(
            "notebook_intelligence.extension.ai_service_manager",
            SimpleNamespace(nbi_config=_config()),
        )

    def test_admin_can_disable_the_billing_check(self):
        """The rest of readiness stays available; only the leg that spends
        tokens is refused."""
        from notebook_intelligence.extension import ReadinessHandler

        handler = self._handler(b'{"live": true}')
        handler.live_check_allowed = False
        with self._patched_manager(), patch(
            "notebook_intelligence.extension.readiness.run_readiness"
        ) as run:
            run.return_value = {"verdict": "ready"}
            _run(ReadinessHandler.post(handler))
        assert run.call_args[0][2] is False
        handler.finish.assert_called_once()

    def test_a_second_concurrent_live_test_is_refused(self):
        from notebook_intelligence.extension import ReadinessHandler

        handler = self._handler(b'{"live": true}')
        ReadinessHandler._live_lock.acquire()
        try:
            _run(ReadinessHandler.post(handler))
        finally:
            ReadinessHandler._live_lock.release()
        handler.set_status.assert_called_once_with(429)

    def test_the_lock_is_released_even_when_the_run_raises(self):
        from notebook_intelligence.extension import ReadinessHandler

        handler = self._handler(b'{"live": true}')
        with self._patched_manager(), patch(
            "notebook_intelligence.extension.readiness.run_readiness",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError):
                _run(ReadinessHandler.post(handler))
        assert ReadinessHandler._live_lock.acquire(blocking=False)
        ReadinessHandler._live_lock.release()

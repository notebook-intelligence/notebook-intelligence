"""Tests for Claude model fetching and caching in notebook_intelligence.claude."""

from unittest.mock import ANY, Mock, patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the model cache (and its endpoint marker) around each test."""
    import notebook_intelligence.claude as claude_module
    claude_module._claude_models_cache.clear()
    claude_module._claude_models_cache_endpoint = None
    yield
    claude_module._claude_models_cache.clear()
    claude_module._claude_models_cache_endpoint = None


class TestGetClaudeModels:
    def test_returns_empty_list_initially(self):
        from notebook_intelligence.claude import get_claude_models
        assert get_claude_models() == []

    def test_returns_cached_models_after_fetch(self):
        from notebook_intelligence.claude import get_claude_models, _claude_models_cache
        _claude_models_cache.extend([
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context_window": 200000},
        ])
        result = get_claude_models()
        assert len(result) == 1
        assert result[0]["id"] == "claude-sonnet-4-6"

    def test_returns_same_list_object(self):
        """get_claude_models returns a reference to the cache, not a copy."""
        from notebook_intelligence.claude import get_claude_models, _claude_models_cache
        assert get_claude_models() is _claude_models_cache


class TestModelInfoFromId:
    def test_returns_cached_model_when_found(self):
        from notebook_intelligence.claude import model_info_from_id, _claude_models_cache
        _claude_models_cache.extend([
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context_window": 200000},
        ])
        result = model_info_from_id("claude-sonnet-4-6")
        assert result["name"] == "Claude Sonnet 4.6"

    def test_returns_default_for_unknown_model(self):
        from notebook_intelligence.claude import model_info_from_id
        result = model_info_from_id("claude-unknown-99")
        assert result["id"] == "claude-unknown-99"
        assert result["name"] == "claude-unknown-99"
        assert result["context_window"] == 200000


class TestGetContextWindow:
    @patch("litellm.get_model_info")
    def test_returns_litellm_value(self, mock_get_model_info):
        from notebook_intelligence.claude import _get_context_window
        mock_get_model_info.return_value = {"max_input_tokens": 150000}
        assert _get_context_window("claude-sonnet-4-6") == 150000

    @patch("litellm.get_model_info")
    def test_falls_back_on_missing_key(self, mock_get_model_info):
        from notebook_intelligence.claude import _get_context_window
        mock_get_model_info.return_value = {}
        assert _get_context_window("claude-sonnet-4-6") == 200000

    @patch("litellm.get_model_info", side_effect=Exception("unknown model"))
    def test_falls_back_on_exception(self, mock_get_model_info):
        from notebook_intelligence.claude import _get_context_window
        assert _get_context_window("unknown-model") == 200000


class TestFetchClaudeModels:
    def _make_mock_model(self, model_id, display_name):
        m = Mock()
        m.id = model_id
        m.display_name = display_name
        return m

    @patch("notebook_intelligence.claude._get_context_window", return_value=200000)
    @patch("anthropic.Anthropic")
    def test_fetches_and_caches_models(self, mock_anthropic_cls, mock_ctx_window):
        from notebook_intelligence.claude import fetch_claude_models, get_claude_models

        mock_page = Mock()
        mock_page.data = [
            self._make_mock_model("claude-sonnet-4-6", "Claude Sonnet 4.6"),
            self._make_mock_model("claude-haiku-4-5", "Claude Haiku 4.5"),
        ]
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        result = fetch_claude_models(api_key="test-key")

        assert len(result) == 2
        assert result[0]["id"] == "claude-sonnet-4-6"
        assert result[1]["id"] == "claude-haiku-4-5"
        # Cache is also updated
        assert len(get_claude_models()) == 2

    @patch("notebook_intelligence.claude._get_context_window", return_value=150000)
    @patch("anthropic.Anthropic")
    def test_uses_litellm_context_window(self, mock_anthropic_cls, mock_ctx_window):
        from notebook_intelligence.claude import fetch_claude_models

        mock_page = Mock()
        mock_page.data = [self._make_mock_model("claude-sonnet-4-6", "Claude Sonnet 4.6")]
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        result = fetch_claude_models(api_key="test-key")

        assert result[0]["context_window"] == 150000
        mock_ctx_window.assert_called_once_with("claude-sonnet-4-6")

    @patch("notebook_intelligence.claude._get_context_window", return_value=200000)
    @patch("anthropic.Anthropic")
    def test_cache_is_mutated_in_place(self, mock_anthropic_cls, mock_ctx_window):
        """Verify the cache list is mutated, not replaced, so importers keep a valid reference."""
        from notebook_intelligence.claude import fetch_claude_models, _claude_models_cache

        original_list = _claude_models_cache

        mock_page = Mock()
        mock_page.data = [self._make_mock_model("claude-sonnet-4-6", "Claude Sonnet 4.6")]
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        fetch_claude_models(api_key="test-key")

        assert _claude_models_cache is original_list
        assert len(original_list) == 1

    @patch("notebook_intelligence.claude._get_context_window", return_value=200000)
    @patch("anthropic.Anthropic")
    def test_clears_old_cache_on_refresh(self, mock_anthropic_cls, mock_ctx_window):
        from notebook_intelligence.claude import fetch_claude_models, get_claude_models, _claude_models_cache

        _claude_models_cache.extend([
            {"id": "old-model", "name": "Old Model", "context_window": 200000},
        ])

        mock_page = Mock()
        mock_page.data = [self._make_mock_model("new-model", "New Model")]
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        fetch_claude_models(api_key="test-key")

        models = get_claude_models()
        assert len(models) == 1
        assert models[0]["id"] == "new-model"

    @patch("anthropic.Anthropic")
    def test_returns_existing_cache_on_api_failure(self, mock_anthropic_cls):
        from notebook_intelligence.claude import fetch_claude_models, get_claude_models, _claude_models_cache

        _claude_models_cache.extend([
            {"id": "cached-model", "name": "Cached", "context_window": 200000},
        ])
        mock_anthropic_cls.return_value.models.list.side_effect = Exception("API error")

        result = fetch_claude_models(api_key="test-key")

        assert len(result) == 1
        assert result[0]["id"] == "cached-model"

    @patch("notebook_intelligence.claude._get_context_window", return_value=200000)
    @patch("anthropic.Anthropic")
    def test_empty_api_key_passed_as_none(self, mock_anthropic_cls, mock_ctx_window):
        from notebook_intelligence.claude import fetch_claude_models

        mock_page = Mock()
        mock_page.data = []
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        fetch_claude_models(api_key="  ", base_url="")

        mock_anthropic_cls.assert_called_once_with(
            api_key=None, base_url=None, default_headers=ANY
        )


class TestModelDefaults:
    """Pin the fallback model choices. Chat falls back to the current
    Sonnet tier; autocomplete falls back to the fastest/cheapest tier —
    a suggestion has to beat the user's next keystroke, and it fires on
    every pause in typing."""

    def test_default_chat_model_is_current_sonnet(self):
        from notebook_intelligence.claude import CLAUDE_DEFAULT_CHAT_MODEL
        assert CLAUDE_DEFAULT_CHAT_MODEL == "claude-sonnet-5"

    def test_default_inline_completion_model_is_haiku(self):
        from notebook_intelligence.claude import CLAUDE_DEFAULT_INLINE_COMPLETION_MODEL
        assert CLAUDE_DEFAULT_INLINE_COMPLETION_MODEL == "claude-haiku-4-5"

    def test_inline_completion_max_tokens_is_bounded(self):
        # Autocomplete output is a few dozen lines at most; the old
        # 10K-token ceiling let a rambling response generate for many
        # seconds before extraction threw most of it away.
        from notebook_intelligence.claude import CLAUDE_INLINE_COMPLETION_MAX_TOKENS
        assert 0 < CLAUDE_INLINE_COMPLETION_MAX_TOKENS <= 4096

    @patch("anthropic.Anthropic")
    def test_inline_completion_request_uses_bounded_max_tokens(self, mock_anthropic_cls):
        from notebook_intelligence.claude import (
            CLAUDE_INLINE_COMPLETION_MAX_TOKENS,
            ClaudeCodeInlineCompletionModel,
        )

        mock_message = Mock()
        mock_message.content = []
        mock_anthropic_cls.return_value.messages.create.return_value = mock_message

        model = ClaudeCodeInlineCompletionModel("", api_key="test-key")
        assert model.id == "claude-haiku-4-5"

        cancel_token = Mock()
        cancel_token.is_cancel_requested = False
        model.inline_completions("prefix", "suffix", "python", "nb.ipynb", None, cancel_token)

        kwargs = mock_anthropic_cls.return_value.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == CLAUDE_INLINE_COMPLETION_MAX_TOKENS
        assert kwargs["model"] == "claude-haiku-4-5"
        assert "code completion assistant" in kwargs["system"]

    @patch("anthropic.Anthropic")
    def test_chatbook_inline_completion_uses_natural_language_prompt(self, mock_anthropic_cls):
        from notebook_intelligence.claude import ClaudeCodeInlineCompletionModel

        mock_message = Mock()
        mock_message.content = []
        mock_anthropic_cls.return_value.messages.create.return_value = mock_message

        model = ClaudeCodeInlineCompletionModel("", api_key="test-key")
        cancel_token = Mock()
        cancel_token.is_cancel_requested = False
        model.inline_completions(
            "plot sales", "", "chatbook", "nb.ipynb", None, cancel_token
        )

        kwargs = mock_anthropic_cls.return_value.messages.create.call_args.kwargs
        assert "natural-language" in kwargs["system"]
        assert "Do not suggest Python" in kwargs["system"]
        assert "Do not write code" in kwargs["messages"][0]["content"]


class TestFetchClaudeModelsContextWindow:
    def _make_mock_model(self, model_id, display_name, max_input_tokens=None):
        m = Mock()
        m.id = model_id
        m.display_name = display_name
        m.max_input_tokens = max_input_tokens
        return m

    @patch("notebook_intelligence.claude._get_context_window", return_value=150000)
    @patch("anthropic.Anthropic")
    def test_prefers_models_api_context_window(self, mock_anthropic_cls, mock_ctx_window):
        """When the Models API reports max_input_tokens, litellm's static
        database (which lags new releases) must not be consulted."""
        from notebook_intelligence.claude import fetch_claude_models

        mock_page = Mock()
        mock_page.data = [
            self._make_mock_model("claude-sonnet-5", "Claude Sonnet 5", max_input_tokens=1000000)
        ]
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        result = fetch_claude_models(api_key="test-key")

        assert result[0]["context_window"] == 1000000
        mock_ctx_window.assert_not_called()

    @patch("notebook_intelligence.claude._get_context_window", return_value=150000)
    @patch("anthropic.Anthropic")
    def test_falls_back_to_litellm_when_api_omits_window(self, mock_anthropic_cls, mock_ctx_window):
        from notebook_intelligence.claude import fetch_claude_models

        mock_page = Mock()
        mock_page.data = [
            self._make_mock_model("claude-sonnet-4-6", "Claude Sonnet 4.6", max_input_tokens=None)
        ]
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        result = fetch_claude_models(api_key="test-key")

        assert result[0]["context_window"] == 150000
        mock_ctx_window.assert_called_once_with("claude-sonnet-4-6")


class TestBoundedIntEnv:
    """NBI_CLAUDE_INLINE_COMPLETION_MAX_TOKENS is parsed at import time —
    malformed values must degrade to the default (never raise and block
    startup), and out-of-range values are clamped so an operator typo
    can't restore unbounded autocomplete output or break API requests."""

    def _parse(self, monkeypatch, raw):
        from notebook_intelligence.claude import _bounded_int_env
        if raw is None:
            monkeypatch.delenv('NBI_TEST_INT', raising=False)
        else:
            monkeypatch.setenv('NBI_TEST_INT', raw)
        return _bounded_int_env('NBI_TEST_INT', 1024, 1, 4096)

    def test_unset_returns_default(self, monkeypatch):
        assert self._parse(monkeypatch, None) == 1024

    def test_valid_value_parsed(self, monkeypatch):
        assert self._parse(monkeypatch, '2048') == 2048

    def test_empty_string_returns_default(self, monkeypatch):
        assert self._parse(monkeypatch, '  ') == 1024

    def test_malformed_value_returns_default(self, monkeypatch):
        assert self._parse(monkeypatch, '1k') == 1024

    def test_too_high_is_clamped_to_maximum(self, monkeypatch):
        # 10000 would restore the old unbounded behavior the cap prevents.
        assert self._parse(monkeypatch, '10000') == 4096

    def test_zero_is_clamped_to_minimum(self, monkeypatch):
        # max_tokens=0 would fail the Anthropic request outright.
        assert self._parse(monkeypatch, '0') == 1

    def test_negative_is_clamped_to_minimum(self, monkeypatch):
        assert self._parse(monkeypatch, '-5') == 1


class TestResolveDefaultModel:
    """Defaults are validated against the fetched model list so a custom
    base_url that serves a different catalog never gets a model id it
    doesn't recognize. The cache is only authoritative for the endpoint
    it was fetched from; an empty or foreign-endpoint cache trusts the
    constant — first-party endpoints always serve the current aliases."""

    def _fill_cache(self, models, api_key=None, base_url=None):
        import notebook_intelligence.claude as claude_module
        claude_module._claude_models_cache.extend(models)
        claude_module._claude_models_cache_endpoint = (
            claude_module._endpoint_identity(api_key, base_url)
        )

    def test_empty_cache_trusts_the_constant(self):
        from notebook_intelligence.claude import resolve_default_model
        assert resolve_default_model("claude-sonnet-5", "claude-sonnet") == "claude-sonnet-5"

    def test_preferred_id_used_when_endpoint_lists_it(self):
        from notebook_intelligence.claude import resolve_default_model
        self._fill_cache([
            {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "context_window": 1000000},
            {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "context_window": 200000},
        ])
        assert resolve_default_model("claude-sonnet-5", "claude-sonnet") == "claude-sonnet-5"

    def test_falls_back_to_newest_same_tier_model(self):
        from notebook_intelligence.claude import resolve_default_model
        self._fill_cache([
            {"id": "claude-sonnet-4-5", "name": "Claude Sonnet 4.5", "context_window": 200000},
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context_window": 200000},
            {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "context_window": 200000},
        ])
        assert resolve_default_model("claude-sonnet-5", "claude-sonnet") == "claude-sonnet-4-6"

    def test_falls_back_to_first_listed_model_without_tier_match(self):
        from notebook_intelligence.claude import resolve_default_model
        self._fill_cache([
            {"id": "custom-proxy-model", "name": "Proxy", "context_window": 128000},
        ])
        assert resolve_default_model("claude-haiku-4-5", "claude-haiku") == "custom-proxy-model"

    def test_cache_from_another_endpoint_is_not_authoritative(self):
        # Endpoint A's catalog must not pick the default for endpoint B:
        # the id might not be served there at all. The constant is the
        # safer bet until B's own fetch lands.
        from notebook_intelligence.claude import resolve_default_model
        self._fill_cache(
            [{"id": "endpoint-a-only-model", "name": "A", "context_window": 128000}],
            base_url="https://endpoint-a.example.com",
        )
        # The stale-cache path spawns a background catalog refresh;
        # patch it so the test doesn't leak a real network thread that
        # holds the single-flight lock into later tests.
        with patch("notebook_intelligence.claude.threading.Thread"):
            resolved = resolve_default_model(
                "claude-sonnet-5", "claude-sonnet",
                base_url="https://endpoint-b.example.com",
            )
        assert resolved == "claude-sonnet-5"

    def test_credential_normalization_applies_to_identity(self):
        # '' and None are the same identity (settings-panel empty fields
        # save as ''), so a cache fetched with no explicit credentials is
        # authoritative for a constructor called with empty strings.
        from notebook_intelligence.claude import resolve_default_model
        self._fill_cache(
            [{"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context_window": 200000}],
            api_key=None, base_url=None,
        )
        resolved = resolve_default_model(
            "claude-sonnet-5", "claude-sonnet", api_key="", base_url="  ",
        )
        assert resolved == "claude-sonnet-4-6"

    def test_chat_model_constructor_resolves_against_cache(self):
        from unittest.mock import patch as _patch
        from notebook_intelligence.claude import ClaudeChatModel
        self._fill_cache(
            [{"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context_window": 200000}],
            api_key="test-key",
        )
        with _patch("anthropic.Anthropic"):
            model = ClaudeChatModel("", api_key="test-key")
        assert model.id == "claude-sonnet-4-6"

    @patch("notebook_intelligence.claude._get_context_window", return_value=200000)
    @patch("anthropic.Anthropic")
    def test_fetch_stamps_the_endpoint_identity(self, mock_anthropic_cls, mock_ctx_window):
        import notebook_intelligence.claude as claude_module
        from notebook_intelligence.claude import fetch_claude_models

        mock_model = Mock()
        mock_model.id = "claude-sonnet-4-6"
        mock_model.display_name = "Claude Sonnet 4.6"
        mock_model.max_input_tokens = None
        mock_page = Mock()
        mock_page.data = [mock_model]
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        fetch_claude_models(api_key="key-a", base_url="https://a.example.com")

        assert claude_module._claude_models_cache_endpoint == (
            "key-a", "https://a.example.com"
        )


class TestResolveDefaultModelRefreshAndOrdering:
    def _fill_cache(self, models, api_key=None, base_url=None):
        import notebook_intelligence.claude as claude_module
        claude_module._claude_models_cache.extend(models)
        claude_module._claude_models_cache_endpoint = (
            claude_module._endpoint_identity(api_key, base_url)
        )

    def test_foreign_endpoint_triggers_background_refresh(self):
        from notebook_intelligence.claude import resolve_default_model
        self._fill_cache(
            [{"id": "endpoint-a-model", "name": "A", "context_window": 128000}],
            base_url="https://endpoint-a.example.com",
        )
        with patch("notebook_intelligence.claude.threading.Thread") as mock_thread:
            resolved = resolve_default_model(
                "claude-sonnet-5", "claude-sonnet",
                base_url="https://endpoint-b.example.com",
            )
        assert resolved == "claude-sonnet-5"
        mock_thread.assert_called_once()
        kwargs = mock_thread.call_args.kwargs
        assert kwargs["kwargs"] == {
            "api_key": None, "base_url": "https://endpoint-b.example.com"
        }
        mock_thread.return_value.start.assert_called_once()

    def test_matching_endpoint_triggers_no_refresh(self):
        from notebook_intelligence.claude import resolve_default_model
        self._fill_cache(
            [{"id": "claude-sonnet-5", "name": "S", "context_window": 1000000}]
        )
        with patch("notebook_intelligence.claude.threading.Thread") as mock_thread:
            resolve_default_model("claude-sonnet-5", "claude-sonnet")
        mock_thread.assert_not_called()

    def test_empty_cache_with_matching_identity_triggers_no_refresh(self):
        import notebook_intelligence.claude as claude_module
        from notebook_intelligence.claude import resolve_default_model
        claude_module._claude_models_cache_endpoint = (
            claude_module._endpoint_identity(None, None)
        )
        with patch("notebook_intelligence.claude.threading.Thread") as mock_thread:
            resolved = resolve_default_model("claude-sonnet-5", "claude-sonnet")
        assert resolved == "claude-sonnet-5"
        mock_thread.assert_not_called()

    def test_never_stamped_cache_triggers_no_refresh(self):
        # Nothing fetched yet at all (marker is None): the startup fetch
        # that update_models_from_config fires owns that case. Spawning
        # here too would make every model constructor a network call in
        # fresh processes (and in tests).
        from notebook_intelligence.claude import resolve_default_model
        with patch("notebook_intelligence.claude.threading.Thread") as mock_thread:
            resolved = resolve_default_model(
                "claude-haiku-4-5", "claude-haiku", api_key="test-key"
            )
        assert resolved == "claude-haiku-4-5"
        mock_thread.assert_not_called()

    def test_tier_fallback_orders_versions_numerically(self):
        # Lexicographic order would pick 4-6 over 4-10; numeric version
        # comparison must pick 4-10 (and a bare 5 over both).
        from notebook_intelligence.claude import resolve_default_model
        self._fill_cache([
            {"id": "claude-sonnet-4-10", "name": "S", "context_window": 200000},
            {"id": "claude-sonnet-4-6", "name": "S", "context_window": 200000},
        ])
        assert resolve_default_model("claude-sonnet-9", "claude-sonnet") == "claude-sonnet-4-10"

    def test_bare_major_version_beats_dated_snapshot(self):
        from notebook_intelligence.claude import _model_version_key
        ordered = sorted(
            ["claude-sonnet-4-5-20250929", "claude-sonnet-5", "claude-sonnet-4-10"],
            key=_model_version_key,
        )
        assert ordered == [
            "claude-sonnet-4-5-20250929", "claude-sonnet-4-10", "claude-sonnet-5"
        ]

"""Tests for Claude model fetching and caching in notebook_intelligence.claude."""

from unittest.mock import ANY, Mock, patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the model cache before and after each test."""
    from notebook_intelligence.claude import _claude_models_cache
    _claude_models_cache.clear()
    yield
    _claude_models_cache.clear()


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
    @patch("notebook_intelligence.claude.Anthropic")
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
    @patch("notebook_intelligence.claude.Anthropic")
    def test_uses_litellm_context_window(self, mock_anthropic_cls, mock_ctx_window):
        from notebook_intelligence.claude import fetch_claude_models

        mock_page = Mock()
        mock_page.data = [self._make_mock_model("claude-sonnet-4-6", "Claude Sonnet 4.6")]
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        result = fetch_claude_models(api_key="test-key")

        assert result[0]["context_window"] == 150000
        mock_ctx_window.assert_called_once_with("claude-sonnet-4-6")

    @patch("notebook_intelligence.claude._get_context_window", return_value=200000)
    @patch("notebook_intelligence.claude.Anthropic")
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
    @patch("notebook_intelligence.claude.Anthropic")
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

    @patch("notebook_intelligence.claude.Anthropic")
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
    @patch("notebook_intelligence.claude.Anthropic")
    def test_empty_api_key_passed_as_none(self, mock_anthropic_cls, mock_ctx_window):
        from notebook_intelligence.claude import fetch_claude_models

        mock_page = Mock()
        mock_page.data = []
        mock_anthropic_cls.return_value.models.list.return_value = mock_page

        fetch_claude_models(api_key="  ", base_url="")

        mock_anthropic_cls.assert_called_once_with(
            api_key=None, base_url=None, default_headers=ANY
        )

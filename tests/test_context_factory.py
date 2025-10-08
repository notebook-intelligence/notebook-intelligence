import pytest
from notebook_intelligence.context_factory import RuleContextFactory
from notebook_intelligence.ruleset import RuleContext


class TestRuleContextFactory:
    def test_create(self):
        """Test creating RuleContext from WebSocket data."""
        filename = "test.ipynb"
        language = "python"
        chat_mode_id = "ask"
        root_dir = "/workspace"
        
        context = RuleContextFactory.create(
            filename=filename,
            language=language,
            chat_mode_id=chat_mode_id,
            root_dir=root_dir
        )
        
        assert context.filename == filename
        assert context.kernel == language
        assert context.mode == chat_mode_id
        assert context.directory == "/workspace"
    
    def test_create_with_subdirectory(self):
        """Test creating RuleContext with file in subdirectory."""
        filename = "notebooks/analysis.ipynb"
        language = "python"
        chat_mode_id = "agent"
        root_dir = "/workspace"
        
        context = RuleContextFactory.create(
            filename=filename,
            language=language,
            chat_mode_id=chat_mode_id,
            root_dir=root_dir
        )
        
        assert context.filename == filename
        assert context.kernel == language
        assert context.mode == chat_mode_id
        assert context.directory == "/workspace/notebooks"

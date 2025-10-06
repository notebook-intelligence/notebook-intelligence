import pytest
from notebook_intelligence.context_factory import NotebookContextFactory
from notebook_intelligence.ruleset import NotebookContext


class TestNotebookContextFactory:
    def test_from_websocket_data(self):
        """Test creating NotebookContext from WebSocket data."""
        filename = "test.ipynb"
        language = "python"
        chat_mode_id = "ask"
        root_dir = "/workspace"
        
        context = NotebookContextFactory.from_websocket_data(
            filename=filename,
            language=language,
            chat_mode_id=chat_mode_id,
            root_dir=root_dir
        )
        
        assert context.filename == filename
        assert context.kernel == language
        assert context.mode == chat_mode_id
        assert context.directory == "/workspace"
    
    def test_from_websocket_data_with_subdirectory(self):
        """Test creating NotebookContext with file in subdirectory."""
        filename = "notebooks/analysis.ipynb"
        language = "python"
        chat_mode_id = "agent"
        root_dir = "/workspace"
        
        context = NotebookContextFactory.from_websocket_data(
            filename=filename,
            language=language,
            chat_mode_id=chat_mode_id,
            root_dir=root_dir
        )
        
        assert context.filename == filename
        assert context.kernel == language
        assert context.mode == chat_mode_id
        assert context.directory == "/workspace/notebooks"
    
    def test_from_dict_with_defaults(self):
        """Test creating NotebookContext from dictionary with defaults."""
        context = NotebookContextFactory.from_dict({})
        
        assert context.filename == "test.ipynb"
        assert context.kernel == "python3"
        assert context.mode == "ask"
        assert context.directory == "/test"
    
    def test_from_dict_with_custom_values(self):
        """Test creating NotebookContext from dictionary with custom values."""
        data = {
            'filename': 'custom.py',
            'kernel': 'python3',
            'mode': 'agent',
            'directory': '/custom/path'
        }
        
        context = NotebookContextFactory.from_dict(data)
        
        assert context.filename == "custom.py"
        assert context.kernel == "python3"
        assert context.mode == "agent"
        assert context.directory == "/custom/path"
    
    def test_from_dict_partial_data(self):
        """Test creating NotebookContext from dictionary with partial data."""
        data = {
            'filename': 'partial.ipynb',
            'mode': 'inline-chat'
        }
        
        context = NotebookContextFactory.from_dict(data)
        
        assert context.filename == "partial.ipynb"
        assert context.kernel == "python3"  # default
        assert context.mode == "inline-chat"
        assert context.directory == "/test"  # default

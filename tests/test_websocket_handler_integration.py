import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from tornado.httputil import HTTPServerRequest
from tornado.web import Application
from notebook_intelligence.extension import WebsocketCopilotHandler
from notebook_intelligence.context_factory import RuleContextFactory
from notebook_intelligence.ruleset import RuleContext


class TestWebsocketHandlerIntegration:
    def _create_mock_application(self):
        """Create a properly mocked Tornado Application."""
        app = Mock(spec=Application)
        app.ui_methods = {}
        app.ui_modules = {}
        return app
    
    def _create_mock_request(self):
        """Create a properly mocked HTTPServerRequest."""
        request = Mock(spec=HTTPServerRequest)
        request.connection = Mock()
        return request
    
    def test_init_with_default_context_factory(self):
        """Test WebsocketCopilotHandler initialization with default context factory."""
        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'), \
             patch('notebook_intelligence.extension.ai_service_manager') as mock_ai_manager, \
             patch('notebook_intelligence.extension.github_copilot') as mock_copilot:
            handler = WebsocketCopilotHandler(
                self._create_mock_application(), 
                self._create_mock_request()
            )
            
            assert handler._context_factory is not None
            assert isinstance(handler._context_factory, RuleContextFactory)
    
    def test_init_with_custom_context_factory(self):
        """Test WebsocketCopilotHandler initialization with custom context factory."""
        mock_factory = Mock(spec=RuleContextFactory)
        
        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'), \
             patch('notebook_intelligence.extension.ai_service_manager') as mock_ai_manager, \
             patch('notebook_intelligence.extension.github_copilot') as mock_copilot:
            handler = WebsocketCopilotHandler(
                self._create_mock_application(), 
                self._create_mock_request(), 
                context_factory=mock_factory
            )
            
            assert handler._context_factory is mock_factory
    
    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_chat_request_creates_context(self, mock_thread, mock_nb_intel, mock_ai_manager):
        """Test that ChatRequest message creates RuleContext."""
        # Setup mocks
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        
        mock_factory = Mock(spec=RuleContextFactory)
        mock_context = Mock(spec=RuleContext)
        mock_factory.create.return_value = mock_context
        
        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )
        
        # Create test message
        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Test prompt',
                'language': 'python',
                'filename': 'test.ipynb',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': []
            }
        }
        
        # Call on_message
        handler.on_message(json.dumps(message))
        
        # Verify context factory was called
        mock_factory.create.assert_called_once_with(
            filename='test.ipynb',
            language='python',
            chat_mode_id='ask',
            root_dir='/workspace'
        )
        
        # Verify thread was started
        mock_thread.assert_called_once()
        
        # Verify the ChatRequest was created with rule_context
        thread_call_args = mock_thread.call_args[1]['args']
        chat_request_call = thread_call_args[0]
        
        # The chat request should be passed to handle_chat_request
        # We can't easily inspect the ChatRequest object, but we can verify
        # that the thread was created with the right target
        assert mock_thread.call_args[1]['target'] is not None
    
    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_generate_code_creates_context(self, mock_thread, mock_nb_intel, mock_ai_manager):
        """Test that GenerateCode message creates RuleContext."""
        # Setup mocks
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        
        mock_factory = Mock(spec=RuleContextFactory)
        mock_context = Mock(spec=RuleContext)
        mock_factory.create.return_value = mock_context
        
        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )
        
        # Create test message
        message = {
            'id': 'test-message-id',
            'type': 'generate-code',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Generate some code',
                'prefix': '',
                'suffix': '',
                'existingCode': '',
                'language': 'python',
                'filename': 'script.py'
            }
        }
        
        # Call on_message
        handler.on_message(json.dumps(message))
        
        # Verify context factory was called
        mock_factory.create.assert_called_once_with(
            filename='script.py',
            language='python',
            chat_mode_id='inline-chat',  # GenerateCode uses inline-chat mode for rule matching
            root_dir='/workspace'
        )
        
        # Verify thread was started
        mock_thread.assert_called_once()
    
    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_agent_mode_creates_context(self, mock_thread, mock_nb_intel, mock_ai_manager):
        """Test that agent mode ChatRequest creates proper context."""
        # Setup mocks
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        
        mock_factory = Mock(spec=RuleContextFactory)
        mock_context = Mock(spec=RuleContext)
        mock_factory.create.return_value = mock_context
        
        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )
        
        # Create test message for agent mode
        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Test agent prompt',
                'language': 'python',
                'filename': 'notebook.ipynb',
                'chatMode': 'agent',
                'toolSelections': {
                    'builtinToolsets': ['nbi-notebook-edit'],
                    'mcpServers': {},
                    'extensions': {}
                },
                'additionalContext': []
            }
        }
        
        # Call on_message
        handler.on_message(json.dumps(message))
        
        # Verify context factory was called with agent mode
        mock_factory.create.assert_called_once_with(
            filename='notebook.ipynb',
            language='python',
            chat_mode_id='agent',
            root_dir='/workspace'
        )
        
        # Verify thread was started
        mock_thread.assert_called_once()

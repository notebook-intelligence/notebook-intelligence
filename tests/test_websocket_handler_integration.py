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
        """Create a properly mocked Tornado Application.

        ``WebsocketCopilotHandler`` now inherits from ``JupyterHandler`` so
        ``set_default_headers`` reaches into ``application.settings`` /
        ``ui_methods`` / ``ui_modules``. Provide each attribute so the
        construction in tests doesn't blow up on the mock.
        """
        app = Mock(spec=Application)
        app.settings = {"jinja2_env": None, "headers": {}}
        app.ui_methods = {}
        app.ui_modules = {}
        app.transforms = []
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
            kernel_name='',
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
        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        assert chat_request.language == 'python'
        assert chat_request.kernel_name == ''
    
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
                'kernelName': 'python3',
                'filename': 'script.py'
            }
        }
        
        # Call on_message
        handler.on_message(json.dumps(message))
        
        # Verify context factory was called
        mock_factory.create.assert_called_once_with(
            filename='script.py',
            language='python',
            kernel_name='python3',
            chat_mode_id='inline-chat',  # GenerateCode uses inline-chat mode for rule matching
            root_dir='/workspace'
        )
        
        # Verify thread was started
        mock_thread.assert_called_once()
        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        assert chat_request.language == 'python'
        assert chat_request.kernel_name == 'python3'
    
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
            kernel_name='',
            chat_mode_id='agent',
            root_dir='/workspace'
        )
        
        # Verify thread was started
        mock_thread.assert_called_once()

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_additional_context_includes_file_contents(self, mock_thread, mock_nb_intel, mock_ai_manager):
        """Test that additional context file contents are forwarded into chat history."""
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = False
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Summarize the attached files',
                'language': 'python',
                'filename': 'notebook.ipynb',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': 'src/example.py',
                        'content': 'def greet():\n    return "hi"\n',
                        'currentCellContents': None,
                        'startLine': 1,
                        'endLine': 2
                    }
                ]
            }
        }

        handler.on_message(json.dumps(message))

        mock_ai_manager.handle_chat_request.assert_called_once()
        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]

        assert len(chat_request.chat_history) == 1
        context_message = chat_request.chat_history[0]["content"]
        assert "src/example.py" in context_message
        assert "File contents:" in context_message
        assert 'def greet()' in context_message
        assert 'return "hi"' in context_message

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_claude_mode_emits_at_mention_not_contents(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        """In Claude Code mode, workspace file context becomes an @-mention
        prose message so the agent's Read tool decides how much to read.
        Pins the issue-326 behavior so a future refactor can't silently
        regress to client-side content injection (which truncates large
        files and rejects binary files outright).
        """
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        # Synthesize a 10MB content blob that the non-Claude path would
        # truncate via _truncate_context_content; the @-mention branch
        # must not echo it back into the prompt.
        large_blob = 'x' * (10 * 1024 * 1024)
        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Summarize the attached file',
                'language': 'python',
                'filename': 'notebook.ipynb',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': 'src/example.py',
                        'content': large_blob,
                        'currentCellContents': None,
                        'startLine': 1,
                        'endLine': 1
                    }
                ]
            }
        }

        handler.on_message(json.dumps(message))

        mock_ai_manager.handle_chat_request.assert_called_once()
        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]

        assert len(chat_request.chat_history) == 1
        context_message = chat_request.chat_history[0]["content"]
        assert "@src/example.py" in context_message
        assert "File contents:" not in context_message
        assert large_blob not in context_message
        # Generous upper bound: the prose envelope is ~80 chars; anything
        # remotely close to the blob size means content leaked through.
        assert len(context_message) < 500

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_claude_mode_image_branch_unchanged(
        self, mock_thread, mock_nb_intel, mock_ai_manager, tmp_path
    ):
        """Pasted images in Claude mode keep the existing path-based
        message format. The new @-mention branch must not shadow the
        is_image branch (the latter already does the right thing for
        pasted-image uploads and changing its wording is out of scope).
        """
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        upload_root = tmp_path / "nbi-uploads"
        upload_root.mkdir()
        uploaded_image = upload_root / "pasted.png"
        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'What is in this image?',
                'language': 'python',
                'filename': 'notebook.ipynb',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': str(uploaded_image),
                        'content': '',
                        'currentCellContents': None,
                        'startLine': 1,
                        'endLine': 1,
                        'isUpload': True,
                        'isImage': True,
                        'mimeType': 'image/png'
                    }
                ]
            }
        }

        with patch('notebook_intelligence.extension._upload_dir', str(upload_root)):
            handler.on_message(json.dumps(message))

        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        assert len(chat_request.chat_history) == 1
        context_message = chat_request.chat_history[0]["content"]
        assert "The user pasted an image" in context_message
        assert str(uploaded_image) in context_message
        # Sanity-check the new @-mention branch didn't intercept this case.
        assert f"@{uploaded_image}" not in context_message

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_claude_mode_rejects_out_of_workspace_path(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        """The pre-existing sandbox check at extension.py rejects
        out-of-workspace paths before the new @-mention branch runs. Pin
        it so a future refactor that reorders the branches can't produce
        ``@../../etc/passwd`` in the prompt.
        """
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Read this file',
                'language': 'python',
                'filename': 'notebook.ipynb',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': '../../etc/passwd',
                        'content': '',
                        'currentCellContents': None,
                        'startLine': 1,
                        'endLine': 1
                    }
                ]
            }
        }

        handler.on_message(json.dumps(message))

        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        # No context message produced; the sandbox rejected the path
        # before the @-mention branch could format it.
        assert chat_request.chat_history == []

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_claude_mode_upload_non_image_uses_absolute_path(
        self, mock_thread, mock_nb_intel, mock_ai_manager, tmp_path
    ):
        """Uploaded text/binary files (non-image) reach the @-mention
        branch with an absolute path; the relpath-against-workspace
        computation must not fire for is_upload=True.
        """
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        upload_root = tmp_path / "nbi-uploads"
        upload_root.mkdir()
        uploaded_file = upload_root / "report.pdf"
        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'What does this PDF say?',
                'language': 'python',
                'filename': 'notebook.ipynb',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': str(uploaded_file),
                        'content': '',
                        'currentCellContents': None,
                        'startLine': 1,
                        'endLine': 1,
                        'isUpload': True
                    }
                ]
            }
        }

        with patch('notebook_intelligence.extension._upload_dir', str(upload_root)):
            handler.on_message(json.dumps(message))

        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        assert len(chat_request.chat_history) == 1
        context_message = chat_request.chat_history[0]["content"]
        assert f"@{uploaded_file}" in context_message
        assert "Read it if relevant" in context_message

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_rejects_forged_upload_path_outside_upload_dir(
        self, mock_thread, mock_nb_intel, mock_ai_manager, tmp_path
    ):
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        upload_root = tmp_path / "nbi-uploads"
        upload_root.mkdir()
        outside_upload_root = tmp_path / "outside-secret.txt"
        outside_upload_root.write_text("secret")
        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Read this upload',
                'language': 'python',
                'filename': 'notebook.ipynb',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': str(outside_upload_root),
                        'content': '',
                        'currentCellContents': None,
                        'startLine': 1,
                        'endLine': 1,
                        'isUpload': True
                    }
                ]
            }
        }

        with patch('notebook_intelligence.extension._upload_dir', str(upload_root)):
            handler.on_message(json.dumps(message))

        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        assert chat_request.chat_history == []

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_claude_mode_rejects_control_char_filename(
        self, mock_thread, mock_nb_intel, mock_ai_manager, tmp_path
    ):
        """A filename containing newlines / bidi-override controls /
        other text-rendering hazards is dropped before being embedded
        in the @-mention prose. Pins the prompt-injection guard so a
        future refactor can't reintroduce the seam.
        """
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        upload_root = tmp_path / "nbi-uploads"
        upload_root.mkdir()
        # Upload path keeps the raw frontend-supplied filePath, so we
        # don't have to fight the workspace sandbox to exercise the
        # codepoint guard. Try newline, line-separator, and a bidi
        # override in sequence; all three must be rejected.
        hazardous_paths = [
            str(upload_root / "bad\nname.pdf"),
            str(upload_root / "bad name.pdf"),
            str(upload_root / "bad‮name.pdf"),
        ]
        for hazardous in hazardous_paths:
            mock_ai_manager.handle_chat_request.reset_mock()
            message = {
                'id': 'test-message-id',
                'type': 'chat-request',
                'data': {
                    'chatId': 'test-chat-id',
                    'prompt': 'Summarize',
                    'language': 'python',
                    'filename': 'notebook.ipynb',
                    'chatMode': 'ask',
                    'toolSelections': {},
                    'additionalContext': [
                        {
                            'filePath': hazardous,
                            'content': '',
                            'currentCellContents': None,
                            'startLine': 1,
                            'endLine': 1,
                            'isUpload': True
                        }
                    ]
                }
            }

            with patch('notebook_intelligence.extension._upload_dir', str(upload_root)):
                handler.on_message(json.dumps(message))

            chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
            assert chat_request.chat_history == [], (
                f"Hazardous path {hazardous!r} produced chat_history "
                f"entry: {chat_request.chat_history!r}"
            )

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_claude_mode_preserves_notebook_cell_pointer(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        """When a notebook cell is active in Claude mode, the cell's
        input/output prose must flow through alongside the @-mention so
        the agent knows which cell the user is asking about. Without
        this the agent sees only `@notebook.ipynb` and 'this cell'
        questions lose their referent.
        """
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Explain this cell',
                'language': 'python',
                'filename': 'analysis.ipynb',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': 'analysis.ipynb',
                        'content': '',
                        'currentCellContents': {
                            'input': 'df.groupby("col").mean()',
                            'output': '       col2\ncol\nA    1.5'
                        },
                        'cellIndex': 3,
                        'startLine': 1,
                        'endLine': 1
                    }
                ]
            }
        }

        handler.on_message(json.dumps(message))

        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        assert len(chat_request.chat_history) == 1
        context_message = chat_request.chat_history[0]["content"]
        assert "@analysis.ipynb" in context_message
        assert "currently selected cell input" in context_message
        assert 'df.groupby("col").mean()' in context_message
        assert "currently selected cell output" in context_message
        assert "col2" in context_message
        assert "'this' cell" in context_message

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_claude_mode_preserves_selection_line_range(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        """When the user has a multi-line text selection in Claude mode,
        the @-mention message gets a 'Their selection spans lines N-M'
        prose pointer so 'why is this broken' has a referent. The bulk
        selection text is NOT echoed back — the agent reads the file
        itself via @-mention and is told where to look.
        """
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        # The frontend passes `content` populated with the selection
        # text. The Claude branch must ignore the bulk and emit only the
        # range pointer.
        selection_text = "def broken():\n    return undefined_thing\n" * 50
        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Why is this broken?',
                'language': 'python',
                'filename': 'src/foo.py',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': 'src/foo.py',
                        'content': selection_text,
                        'currentCellContents': None,
                        'startLine': 42,
                        'endLine': 78
                    }
                ]
            }
        }

        handler.on_message(json.dumps(message))

        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        assert len(chat_request.chat_history) == 1
        context_message = chat_request.chat_history[0]["content"]
        assert "@src/foo.py" in context_message
        assert "lines 42-78" in context_message
        # Bulk selection content must NOT have leaked into the message.
        assert "undefined_thing" not in context_message
        assert "def broken" not in context_message

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_on_message_claude_mode_no_selection_no_range_pointer(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        """When the user has the file open with no selection (startLine
        == endLine), the @-mention message stays clean — no spurious
        'lines 1-1' pointer that would confuse the agent into thinking
        the user wants only the first line.
        """
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.is_acp_mode = False
        mock_ai_manager.chat_model = Mock()
        mock_ai_manager.chat_model.context_window = 4096

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'What is this file about?',
                'language': 'python',
                'filename': 'src/foo.py',
                'chatMode': 'ask',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': 'src/foo.py',
                        'content': '',
                        'currentCellContents': None,
                        'startLine': 1,
                        'endLine': 1
                    }
                ]
            }
        }

        handler.on_message(json.dumps(message))

        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        assert len(chat_request.chat_history) == 1
        context_message = chat_request.chat_history[0]["content"]
        assert "@src/foo.py" in context_message
        assert "lines" not in context_message
        assert "selection" not in context_message


class TestPermissionModeClamp:
    """The websocket boundary is the sole arbiter for bypass (issue #359).

    Hiding the option in the UI is convenience; a hand-rolled request must
    still be clamped server-side against the resolved bypass policy.
    """

    def _create_mock_application(self):
        app = Mock(spec=Application)
        app.settings = {"jinja2_env": None, "headers": {}}
        app.ui_methods = {}
        app.ui_modules = {}
        app.transforms = []
        return app

    def _create_mock_request(self):
        request = Mock(spec=HTTPServerRequest)
        request.connection = Mock()
        return request

    def _handler(self, *, bypass_allowed):
        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)
        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory,
            )
        handler.claude_bypass_permissions_allowed = bypass_allowed
        return handler

    def _send(self, handler, mock_ai_manager, mock_nb_intel, mode):
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        data = {
            'chatId': 'c',
            'prompt': 'hi',
            'language': 'python',
            'filename': 'x.ipynb',
            'chatMode': 'ask',
            'toolSelections': {},
            'additionalContext': [],
        }
        if mode is not None:
            data['permissionMode'] = mode
        message = {'id': 'm', 'type': 'chat-request', 'data': data}
        handler.on_message(json.dumps(message))
        return mock_ai_manager.handle_chat_request.call_args[0][0]

    def test_class_default_fails_closed(self):
        # Before _setup_handlers runs, the gate must be closed.
        assert WebsocketCopilotHandler.claude_bypass_permissions_allowed is False

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_bypass_clamped_to_default_when_policy_forbids(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        handler = self._handler(bypass_allowed=False)
        req = self._send(
            handler, mock_ai_manager, mock_nb_intel, 'bypassPermissions'
        )
        assert req.permission_mode == 'default'

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_bypass_passes_through_when_policy_allows(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        handler = self._handler(bypass_allowed=True)
        req = self._send(
            handler, mock_ai_manager, mock_nb_intel, 'bypassPermissions'
        )
        assert req.permission_mode == 'bypassPermissions'

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_normal_mode_passes_through(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        handler = self._handler(bypass_allowed=False)
        req = self._send(handler, mock_ai_manager, mock_nb_intel, 'plan')
        assert req.permission_mode == 'plan'

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_unknown_mode_clamped_to_default(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        handler = self._handler(bypass_allowed=True)
        req = self._send(handler, mock_ai_manager, mock_nb_intel, 'dontAsk')
        assert req.permission_mode == 'default'

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_missing_mode_defaults(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        handler = self._handler(bypass_allowed=True)
        req = self._send(handler, mock_ai_manager, mock_nb_intel, None)
        assert req.permission_mode == 'default'


class TestContextTokenLimit:
    """The attachment/output-context budget must come from the Claude model
    in Claude Code mode, not from the (possibly ``None``) legacy chat_model.

    Regression guard: on a Claude-only setup ``chat_model`` is ``None``, and
    the old ``100 if chat_model is None else ...`` expression shrank the
    budget to 80 tokens — cell-output context was skipped and every
    attachment after the first was dropped by the budget break.
    """

    def _mock_manager(self, claude_mode: bool, chat_model):
        manager = Mock()
        manager.is_claude_code_mode = claude_mode
        manager.chat_model = chat_model
        manager.nbi_config.claude_settings = {'chat_model': ''}
        return manager

    def test_non_claude_mode_without_model_keeps_floor(self):
        from notebook_intelligence.extension import _resolve_context_token_limit
        assert _resolve_context_token_limit(self._mock_manager(False, None)) == 100

    def test_non_claude_mode_uses_model_context_window(self):
        from notebook_intelligence.extension import _resolve_context_token_limit
        chat_model = Mock()
        chat_model.context_window = 4096
        assert _resolve_context_token_limit(self._mock_manager(False, chat_model)) == 4096

    def test_claude_mode_ignores_missing_legacy_model(self):
        from notebook_intelligence.extension import _resolve_context_token_limit
        with patch(
            'notebook_intelligence.extension.model_info_from_id',
            return_value={'id': '', 'name': '', 'context_window': 123456},
        ) as mock_info:
            assert _resolve_context_token_limit(self._mock_manager(True, None)) == 123456
        mock_info.assert_called_once_with('')

    def test_claude_mode_passes_configured_model_id(self):
        from notebook_intelligence.extension import _resolve_context_token_limit
        manager = self._mock_manager(True, None)
        manager.nbi_config.claude_settings = {'chat_model': ' claude-sonnet-4-5 '}
        with patch(
            'notebook_intelligence.extension.model_info_from_id',
            return_value={'id': 'claude-sonnet-4-5', 'name': 'Sonnet', 'context_window': 200000},
        ) as mock_info:
            assert _resolve_context_token_limit(manager) == 200000
        mock_info.assert_called_once_with('claude-sonnet-4-5')

    def test_claude_mode_default_window_without_model_cache(self):
        # No patching: the real model_info_from_id falls back to a 200K
        # window for an unknown/default id, so the budget is never the
        # legacy 100-token floor in Claude Code mode.
        from notebook_intelligence.extension import _resolve_context_token_limit
        assert _resolve_context_token_limit(self._mock_manager(True, None)) >= 200000

    @patch('notebook_intelligence.extension.ai_service_manager')
    @patch('notebook_intelligence.extension.NotebookIntelligence')
    @patch('notebook_intelligence.extension.threading.Thread')
    def test_claude_mode_output_context_survives_without_legacy_model(
        self, mock_thread, mock_nb_intel, mock_ai_manager
    ):
        """A cell-output attachment larger than the legacy 80-token budget
        must still reach chat history in Claude Code mode."""
        mock_nb_intel.root_dir = "/workspace"
        mock_ai_manager.handle_chat_request = Mock()
        mock_ai_manager.is_claude_code_mode = True
        mock_ai_manager.chat_model = None  # Claude-only setup
        mock_ai_manager.nbi_config.claude_settings = {'chat_model': ''}

        mock_factory = Mock(spec=RuleContextFactory)
        mock_factory.create.return_value = Mock(spec=RuleContext)

        with patch('notebook_intelligence.extension.ThreadSafeWebSocketConnector'):
            handler = WebsocketCopilotHandler(
                self._create_mock_application(),
                self._create_mock_request(),
                context_factory=mock_factory
            )

        message = {
            'id': 'test-message-id',
            'type': 'chat-request',
            'data': {
                'chatId': 'test-chat-id',
                'prompt': 'Explain this output',
                'language': 'python',
                'filename': 'notebook.ipynb',
                'chatMode': 'agent',
                'toolSelections': {},
                'additionalContext': [
                    {
                        'filePath': 'notebook.ipynb',
                        'outputContext': {
                            'cellSource': 'df.describe()',
                            'mimeBundles': [
                                {
                                    'mimeType': 'text/plain',
                                    'data': 'count 100\nmean 4.2',
                                    # Over the legacy 80-token budget: the
                                    # old code path skipped this bundle.
                                    'sizeTokens': 500,
                                }
                            ],
                            'isError': False,
                            'truncated': False,
                        },
                    }
                ]
            }
        }

        handler.on_message(json.dumps(message))

        mock_ai_manager.handle_chat_request.assert_called_once()
        chat_request = mock_ai_manager.handle_chat_request.call_args[0][0]
        history_text = json.dumps(chat_request.chat_history)
        assert 'df.describe()' in history_text
        assert 'mean 4.2' in history_text

    def _create_mock_application(self):
        app = Mock(spec=Application)
        app.settings = {"jinja2_env": None, "headers": {}}
        app.ui_methods = {}
        app.ui_modules = {}
        app.transforms = []
        return app

    def _create_mock_request(self):
        request = Mock(spec=HTTPServerRequest)
        request.connection = Mock()
        return request

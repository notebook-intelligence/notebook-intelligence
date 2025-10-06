# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import os
from typing import Dict, Any
from notebook_intelligence.ruleset import NotebookContext


class NotebookContextFactory:
    """Factory for creating NotebookContext from various sources."""
    
    @staticmethod
    def from_websocket_data(filename: str, language: str, chat_mode_id: str, root_dir: str) -> NotebookContext:
        """Create NotebookContext from WebSocket message data."""
        return NotebookContext(
            filename=filename,
            kernel=language,
            mode=chat_mode_id,
            directory=os.path.dirname(os.path.join(root_dir, filename))
        )
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> NotebookContext:
        """Create NotebookContext from dictionary (for testing)."""
        return NotebookContext(
            filename=data.get('filename', 'test.ipynb'),
            kernel=data.get('kernel', 'python3'),
            mode=data.get('mode', 'ask'),
            directory=data.get('directory', '/test')
        )

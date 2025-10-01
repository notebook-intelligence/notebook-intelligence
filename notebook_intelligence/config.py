# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
import logging
import os
import sys

log = logging.getLogger(__name__)

class NBIConfig:
    def __init__(self, options: dict = {}):
        self.options = options

        self.deprecated_env_config_file = os.path.join(sys.prefix, "share", "jupyter", "nbi-config.json")
        self.deprecated_user_config_file = os.path.join(os.path.expanduser('~'), ".jupyter", "nbi-config.json")

        self.nbi_env_dir = os.path.join(sys.prefix, "share", "jupyter", "nbi")
        self.nbi_user_dir = os.path.join(os.path.expanduser('~'), ".jupyter", "nbi")
        self.env_config_file = os.path.join(self.nbi_env_dir, "config.json")
        self.user_config_file = os.path.join(self.nbi_user_dir, "config.json")
        self.env_mcp_file = os.path.join(self.nbi_env_dir, "mcp.json")
        self.user_mcp_file = os.path.join(self.nbi_user_dir, "mcp.json")
        self.env_config = {}
        self.user_config = {}
        self.env_mcp = {}
        self.user_mcp = {}
        self.load()

        # TODO: Remove after 12/2025
        if os.path.exists(self.deprecated_env_config_file):
            log.warning(f"Deprecated config file found: {self.deprecated_env_config_file}. Use {self.env_config_file} and {self.env_mcp_file} instead.")
        if os.path.exists(self.deprecated_user_config_file):
            log.warning(f"Deprecated config file found: {self.deprecated_user_config_file}. Use {self.user_config_file} and {self.user_mcp_file} instead.")
        if self.env_mcp.get("participants") is not None or self.user_mcp.get("participants") is not None:
            log.warning("MCP participants configuration is deprecated. Users should use Agent mode to select MCP tools.")

    @property
    def server_root_dir(self):
        return self.options.get('server_root_dir', '')

    def load(self):
        if os.path.exists(self.env_config_file):
            with open(self.env_config_file, 'r') as file:
                self.env_config = json.load(file)
        elif os.path.exists(self.deprecated_env_config_file):
            with open(self.deprecated_env_config_file, 'r') as file:
                self.env_config = json.load(file)
                self.env_mcp = {}
                if 'mcp' in self.env_config:
                    self.env_mcp = self.env_config.get('mcp', {})
                    del self.env_config['mcp']
        else:
            self.env_config = {}

        if os.path.exists(self.user_config_file):
            with open(self.user_config_file, 'r') as file:
                self.user_config = json.load(file)
        elif os.path.exists(self.deprecated_user_config_file):
            with open(self.deprecated_user_config_file, 'r') as file:
                self.user_config = json.load(file)
                self.user_mcp = {}
                if 'mcp' in self.user_config:
                    self.user_mcp = self.user_config.get('mcp', {})
                    del self.user_config['mcp']
        else:
            self.user_config = {}

        if os.path.exists(self.env_mcp_file):
            with open(self.env_mcp_file, 'r') as file:
                self.env_mcp = json.load(file)

        if os.path.exists(self.user_mcp_file):
            with open(self.user_mcp_file, 'r') as file:
                self.user_mcp = json.load(file)

    def save(self):
        # TODO: save only diff
        os.makedirs(self.nbi_user_dir, exist_ok=True)

        with open(self.user_config_file, 'w') as file:
            json.dump(self.user_config, file, indent=2)

        with open(self.user_mcp_file, 'w') as file:
            json.dump(self.user_mcp, file, indent=2)

    def get(self, key, default=None):
        return self.user_config.get(key, self.env_config.get(key, default))

    def set(self, key, value):
        self.user_config[key] = value
        self.save()

    @property
    def default_chat_mode(self):
        return self.get('default_chat_mode', 'ask')

    @property
    def chat_model(self):
        return self.get('chat_model', {'provider': 'github-copilot', 'model': 'gpt-4.1'})

    @property
    def inline_completion_model(self):
        return self.get('inline_completion_model', {'provider': 'github-copilot', 'model': 'gpt-4o-copilot'})

    @property
    def embedding_model(self):
        return self.get('embedding_model', {})

    @property
    def mcp(self):
        mcp_config = self.env_mcp.copy()
        mcp_config.update(self.user_mcp)
        return mcp_config

    @property
    def store_github_access_token(self):
        return self.get('store_github_access_token', False)

    @property
    def using_github_copilot_service(self) -> bool:
        return self.chat_model.get("provider") == 'github-copilot' or \
            self.inline_completion_model.get("provider") == 'github-copilot'

    @property
    def rules_enabled(self) -> bool:
        """Check if the ruleset system is enabled."""
        return self.get('rules_enabled', True)

    @property
    def rules_directory(self) -> str:
        """Get the rules directory path."""
        return os.path.join(self.nbi_user_dir, 'rules')

    @property
    def active_rules(self) -> dict:
        """Get dictionary of active rule states (filename -> bool)."""
        return self.get('active_rules', {})

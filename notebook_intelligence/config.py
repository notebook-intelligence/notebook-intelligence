# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
import os
import sys

class NBIConfig:
    def __init__(self, options: dict = {}):
        self.options = options
        self.env_config_file = os.path.join(sys.prefix, "share", "jupyter", "nbi-config.json")
        self.user_config_file = os.path.join(os.path.expanduser('~'), ".jupyter", "nbi-config.json")
        self.env_config = {}
        self.user_config = {}
        self.load()

    @property
    def server_root_dir(self):
        return self.options.get('server_root_dir', '')

    def load(self):
        if os.path.exists(self.env_config_file):
            with open(self.env_config_file, 'r') as file:
                self.env_config = json.load(file)
        else:
            self.env_config = {}

        if os.path.exists(self.user_config_file):
            with open(self.user_config_file, 'r') as file:
                self.user_config = json.load(file)
        else:
            self.user_config = {}

    def save(self):
        # TODO: save only diff
        with open(self.user_config_file, 'w') as file:
            json.dump(self.user_config, file, indent=4)

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
        return self.get('mcp', {})

    @property
    def store_github_access_token(self):
        return self.get('store_github_access_token', False)

    @property
    def using_github_copilot_service(self) -> bool:
        return self.chat_model.get("provider") == 'github-copilot' or \
            self.inline_completion_model.get("provider") == 'github-copilot'

# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
import logging
import os
import stat
import sys
import tempfile
from typing import Optional

from notebook_intelligence.util import get_claude_config_dir
from notebook_intelligence.feature_flags import (
    ACP_SETTINGS_OVERRIDES,
    CHAT_MODEL_OVERRIDES,
    CLAUDE_SETTINGS_OVERRIDES,
    INLINE_COMPLETION_MODEL_OVERRIDES,
    apply_acp_policies,
    apply_claude_policies,
    apply_string_overrides,
)

log = logging.getLogger(__name__)

class NBIConfig:
    # Class-level defaults so a test that patches __init__ still sees empty
    # dicts instead of AttributeError. Per-instance values are bound in
    # __init__ so concurrent instances don't share state.
    _feature_policies: dict = {}
    _string_overrides: dict = {}

    def __init__(self, options: Optional[dict] = None):
        self.options = dict(options) if options is not None else {}
        self._feature_policies = {}
        self._string_overrides = {}

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

        _atomic_write_json(self.user_config_file, self.user_config)
        _atomic_write_json(self.user_mcp_file, self.user_mcp)

    def get(self, key, default=None):
        return self.user_config.get(key, self.env_config.get(key, default))

    def set(self, key, value):
        self.user_config[key] = value
        self.save()

    @property
    def default_chat_mode(self):
        return self.get('default_chat_mode', 'ask')

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
    def inline_completion_debouncer_delay(self):
        return self.get('inline_completion_debouncer_delay', 200)

    @property
    def using_github_copilot_service(self) -> bool:
        return self.chat_model.get("provider") == 'github-copilot' or \
            self.inline_completion_model.get("provider") == 'github-copilot'

    @property
    def mcp_server_settings(self):
        return self.get('mcp_server_settings', {})

    @property
    def additional_skipped_workspace_directories(self) -> list:
        """Extra directory names to skip when enumerating workspace files
        for the chat-sidebar @-mention picker. Layered like every other
        NBI-config value: the env-prefix file (admin baseline) and the
        user file (per-user extension) both contribute. The traitlet of
        the same name and the NBI_ADDITIONAL_SKIPPED_WORKSPACE_DIRECTORIES
        env var add further layers; the final merge happens in
        ``extension._setup_handlers``. Returns `[]` when neither layer
        sets it, or whichever layer's value is a non-list.
        """
        env_list = self.env_config.get('additional_skipped_workspace_directories', [])
        user_list = self.user_config.get('additional_skipped_workspace_directories', [])
        merged = []
        for layer in (env_list, user_list):
            if isinstance(layer, list):
                merged.extend(name for name in layer if isinstance(name, str) and name.strip())
        # Dedupe while preserving first-seen order.
        return list(dict.fromkeys(merged))

    def set_feature_policies(self, policies: dict, string_overrides: dict) -> None:
        """Adopt the resolved admin policies + string overrides."""
        self._feature_policies = dict(policies)
        self._string_overrides = dict(string_overrides)

    @property
    def claude_settings(self):
        resolved = apply_claude_policies(
            self.get('claude_settings', {}), self._feature_policies
        )
        return apply_string_overrides(
            resolved, self._string_overrides, CLAUDE_SETTINGS_OVERRIDES
        )

    @property
    def acp_settings(self):
        resolved = apply_acp_policies(
            self.get('acp_settings', {}), self._feature_policies
        )
        return apply_string_overrides(
            resolved, self._string_overrides, ACP_SETTINGS_OVERRIDES
        )

    @property
    def chat_model(self):
        raw = self.get('chat_model', {'provider': 'github-copilot', 'model': 'gpt-4.1'})
        return apply_string_overrides(
            raw, self._string_overrides, CHAT_MODEL_OVERRIDES
        )

    @property
    def inline_completion_model(self):
        raw = self.get('inline_completion_model', {'provider': 'github-copilot', 'model': 'gpt-4o-copilot'})
        return apply_string_overrides(
            raw, self._string_overrides, INLINE_COMPLETION_MODEL_OVERRIDES
        )

    @property
    def rules_enabled(self) -> bool:
        """Check if the ruleset system is enabled."""
        return self.get('rules_enabled', True)

    @property
    def enable_explain_error(self) -> bool:
        """User preference for the inline error-explanation pill (default on)."""
        return bool(self.get('enable_explain_error', True))

    @property
    def enable_output_followup(self) -> bool:
        """User preference for the cell output follow-up affordance (default on)."""
        return bool(self.get('enable_output_followup', True))

    @property
    def enable_output_toolbar(self) -> bool:
        """User preference for the hover toolbar over cell outputs (default on)."""
        return bool(self.get('enable_output_toolbar', True))

    @property
    def refresh_open_files_on_disk_change(self) -> bool:
        """User preference for the open-files refresh watcher (default on)."""
        return bool(self.get('refresh_open_files_on_disk_change', True))

    @property
    def rules_directory(self) -> str:
        """Get the rules directory path."""
        return os.path.join(self.nbi_user_dir, 'rules')

    @property
    def user_skills_directory(self) -> str:
        # Mirrors Claude's own CLAUDE_CONFIG_DIR override so the extension picks up
        # skills from the same directory Claude is using.
        return os.path.join(get_claude_config_dir(), 'skills')

    def project_skills_directory(self, project_root: str) -> str:
        return os.path.join(project_root, '.claude', 'skills')

    @property
    def active_rules(self) -> dict:
        """Get dictionary of active rule states (filename -> bool)."""
        return self.get('active_rules', {})
    
    def set_rule_active(self, filename: str, active: bool):
        """Set the active state of a rule."""
        active_rules = self.active_rules.copy()
        active_rules[filename] = active
        self.set('active_rules', active_rules)


def _atomic_write_json(target: str, payload: dict, *, mode: Optional[int] = None) -> None:
    """Crash-safe replacement for ``open(target, 'w') + json.dump``.

    Plain truncating writes leave the destination empty (or partial)
    if the process is killed mid-write — the next launch then chokes on
    a corrupt config. Write to a sibling tempfile, ``fsync``, and
    ``os.replace`` so the swap is atomic on POSIX and atomic-ish on
    Windows.

    Symlink-preserving: a user who symlinks ``~/.jupyter/nbi/config.json``
    to a shared config file expects ``save()`` to update the link's
    target, not replace the link itself. Resolve via ``realpath`` first.

    Mode handling:
      - ``mode`` argument set: the file is written with exactly that mode,
        regardless of any existing mode. Callers that handle secrets (the
        encrypted GitHub token at ``~/.jupyter/nbi/user-data.json``) pass
        ``0o600`` so the file is never world-readable, even if a prior
        umask-default write or a manual chmod widened the perms.
      - ``mode=None`` (default): ``mkstemp`` returns a 0o600 file; if the
        existing target has different permissions (e.g. 0o644 for a
        shared install), re-apply them after the swap so the user's
        chmod isn't silently undone.

    Durability: ``fsync`` the tempfile, then ``fsync`` the target's
    parent directory on POSIX so the rename itself survives a crash.
    """
    real_target = os.path.realpath(target)
    target_dir = os.path.dirname(real_target) or '.'
    existing_mode = None
    try:
        existing_mode = stat.S_IMODE(os.stat(real_target).st_mode)
    except FileNotFoundError:
        pass
    fd, tmp_path = tempfile.mkstemp(
        prefix='.' + os.path.basename(real_target) + '.',
        suffix='.tmp',
        dir=target_dir,
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        # Decide what mode the post-rename file should carry:
        #   - explicit mode overrides everything (secrets case)
        #   - else preserve existing target mode (shared-install case)
        #   - else leave mkstemp's 0o600
        effective_mode = mode if mode is not None else existing_mode
        if effective_mode is not None:
            try:
                os.chmod(tmp_path, effective_mode)
            except OSError:
                # Some filesystems (FAT, certain network mounts) reject chmod;
                # the swap itself is still safe, just lose the mode bits.
                pass
        os.replace(tmp_path, real_target)
    except Exception:
        # Best-effort cleanup — if replace failed the tempfile is dangling.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # ``os.replace`` makes the new inode visible, but until the directory
    # entry is fsynced a power-loss can still leave the rename unrecorded
    # on POSIX. Windows has no ``O_RDONLY`` directory descriptor, so skip
    # there and rely on its rename semantics.
    if hasattr(os, 'O_DIRECTORY'):
        try:
            dir_fd = os.open(target_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

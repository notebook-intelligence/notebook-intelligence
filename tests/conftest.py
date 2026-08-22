import asyncio
import io
import pytest
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from notebook_intelligence.ruleset import RuleContext
from notebook_intelligence.config import NBIConfig


@pytest.fixture(autouse=True)
def _no_real_managed_settings(monkeypatch):
    """Stop the Claude managed-settings probe from reading real OS paths.

    ``_build_feature_policies_response`` and ``resolve_permission_mode`` now
    consult Claude Code's enterprise managed-settings files (issue #359). On
    a developer machine or CI image that actually has one installed, that
    would couple unrelated capabilities/policy tests to the host. Default
    every test to "unmanaged" (empty path tuple); the permission-mode tests
    re-point this to their own tmp file where they need a managed file.
    """
    import notebook_intelligence.claude as claude_module

    monkeypatch.setattr(claude_module, "_MANAGED_SETTINGS_PATHS", ())


def stub_claude_subprocess(
    monkeypatch,
    *,
    captured: dict,
    stdout: bytes = b"",
    returncode: int = 0,
):
    """Patch ``asyncio.create_subprocess_exec`` for Claude-CLI shellouts.

    Used by both the Claude-MCP and plugin manager tests; lives here to keep
    the fake's signature consistent across files. ``captured`` is a dict the
    test inspects after the call; ``stdout``/``returncode`` shape the fake
    process's outputs.
    """
    out_bytes = stdout
    rc = returncode

    async def fake_subprocess(*argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        proc = MagicMock()

        async def communicate():
            return (out_bytes, b"")

        proc.communicate = communicate
        proc.returncode = rc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)


def build_tarball(file_map: dict) -> bytes:
    """Build a gzipped tarball with the given {path: content} mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in file_map.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def temp_rules_directory(tmp_path):
    """Create temporary rules directory for testing."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    
    # Create modes subdirectory structure
    modes_dir = rules_dir / "modes"
    modes_dir.mkdir()
    
    for mode in ['ask', 'agent', 'inline-chat', 'chatbook']:
        (modes_dir / mode).mkdir()
    
    return str(rules_dir)


@pytest.fixture
def sample_rule_context():
    """Mock rule context for testing."""
    return RuleContext(
        filename="test.ipynb",
        language="python",
        kernel_name="python3",
        mode="ask"
    )


@pytest.fixture
def python_file_context():
    """Mock Python file context for testing."""
    return RuleContext(
        filename="test.py",
        language="python",
        kernel_name="python3",
        mode="agent"
    )


@pytest.fixture
def mock_nbi_config(tmp_path):
    """Mock NBIConfig for testing."""
    config_data = {
        'rules_enabled': True,
        'rules_auto_reload': True,
        'active_rules': {
            '01-test-global.md': True,
            '02-test-security.md': False
        }
    }
    
    with patch.object(NBIConfig, '__init__', return_value=None):
        config = NBIConfig()
        config.nbi_user_dir = str(tmp_path / "nbi")
        config.user_config = config_data
        config.env_config = {}
        config.user_config_file = str(tmp_path / "nbi" / "config.json")
        config.user_mcp_file = str(tmp_path / "nbi" / "mcp.json")
        config.user_mcp = {}
        yield config


@pytest.fixture
def populated_rules_directory(temp_rules_directory):
    """Create a rules directory with sample test rules."""
    rules_path = Path(temp_rules_directory)
    
    # Global rules
    global_rule_1 = """---
apply: always
scope:
  file_patterns:
    - "*.ipynb"
    - "*.py"
  languages:
    - python
  kernel_names:
    - python3
active: true
priority: 0
---
# Python Best Practices
- Use type hints
- Follow PEP 8
- Add docstrings"""
    
    global_rule_2 = """---
apply: auto
scope:
  file_patterns:
    - "*.py"
active: true
priority: 1
---
# Security Rules
- No hardcoded secrets
- Validate inputs"""
    
    with open(rules_path / "01-python.md", 'w') as f:
        f.write(global_rule_1)
    
    with open(rules_path / "02-security.md", 'w') as f:
        f.write(global_rule_2)
    
    # Mode-specific rules
    ask_rule = """---
apply: always
scope:
  file_patterns:
    - "*.ipynb"
active: true
---
# Ask Mode Guidelines
- Provide explanations
- Suggest alternatives"""
    
    with open(rules_path / "modes" / "ask" / "01-exploration.md", 'w') as f:
        f.write(ask_rule)
    
    agent_rule = """---
apply: always
scope:
  file_patterns:
    - "*.ipynb"
    - "*.py"
active: true
---
# Agent Mode Standards
- Production-ready code
- Comprehensive error handling"""
    
    with open(rules_path / "modes" / "agent" / "01-production.md", 'w') as f:
        f.write(agent_rule)
    
    return temp_rules_directory


@pytest.fixture
def invalid_rules_directory(temp_rules_directory):
    """Create a rules directory with invalid rules for error testing."""
    rules_path = Path(temp_rules_directory)
    
    # Invalid YAML that will actually cause parsing error
    invalid_yaml = """---
apply: always
scope:
  file_patterns:
    - "*.py"
  invalid_key: [unclosed_list
active: true
---
# Invalid YAML Rule"""
    
    with open(rules_path / "invalid-yaml.md", 'w') as f:
        f.write(invalid_yaml)
    
    # Missing frontmatter
    no_frontmatter = """# Rule without frontmatter
This rule has no YAML frontmatter."""
    
    with open(rules_path / "no-frontmatter.md", 'w') as f:
        f.write(no_frontmatter)
    
    return temp_rules_directory
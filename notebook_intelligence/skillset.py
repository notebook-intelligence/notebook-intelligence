from dataclasses import dataclass, field
from typing import Dict, List, Any, Literal
from pathlib import Path
import logging
import re
import yaml

log = logging.getLogger(__name__)

SkillScope = Literal["user", "project"]

SKILL_ENTRY_FILE = "SKILL.md"

# Single source of truth for valid skill names. Mirrored to frontend as SKILL_NAME_PATTERN
# in src/components/skills-panel.tsx and to Tornado routes in extension.py.
SKILL_NAME_REGEX = r"[a-z0-9][a-z0-9-]{0,63}"
SKILL_NAME_PATTERN = re.compile(f"^{SKILL_NAME_REGEX}$")
SKILL_NAME_REQUIREMENT = (
    "Must be lowercase letters, digits, or hyphens (starting with a letter or digit), "
    "1-64 chars"
)


@dataclass
class Skill:
    """A Claude skill bundle: a directory containing SKILL.md and optional supporting files."""
    name: str
    scope: SkillScope
    root_path: Path
    description: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    body: str = ""
    source: str = ""
    managed_source: str = ""
    managed_ref: str = ""

    @classmethod
    def from_path(cls, dir_path: Path, scope: SkillScope) -> 'Skill':
        dir_path = Path(dir_path)
        skill_md = dir_path / SKILL_ENTRY_FILE
        if not skill_md.exists():
            raise FileNotFoundError(f"Bundle missing {SKILL_ENTRY_FILE}: {dir_path}")
        frontmatter, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"), skill_md)
        name = frontmatter.get("name") or dir_path.name
        return cls(
            name=name,
            scope=scope,
            root_path=dir_path,
            description=frontmatter.get("description", ""),
            allowed_tools=list(frontmatter.get("allowed-tools", []) or []),
            body=body,
            source=frontmatter.get("source", "") or "",
            managed_source=frontmatter.get("managed_source", "") or "",
            managed_ref=frontmatter.get("managed_ref", "") or "",
        )

    @property
    def managed(self) -> bool:
        return bool(self.managed_source)

    def skill_md_path(self) -> Path:
        return self.root_path / SKILL_ENTRY_FILE

    def list_files(self) -> List[str]:
        """List files inside the bundle, relative to root_path."""
        return list_bundle_files(self.root_path)

    def resolve_bundle_path(self, rel_path: str) -> Path:
        """Resolve a bundle-internal path, blocking traversal outside root_path."""
        if not rel_path or rel_path.startswith("/") or "\\" in rel_path:
            raise ValueError(f"Invalid bundle path: {rel_path}")
        resolved = (self.root_path / rel_path).resolve()
        root_resolved = self.root_path.resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            raise ValueError(f"Path escapes bundle root: {rel_path}")
        return resolved

    def to_dict(self, include_body: bool = False, include_files: bool = True) -> Dict[str, Any]:
        data = {
            "name": self.name,
            "scope": self.scope,
            "description": self.description,
            "allowed_tools": self.allowed_tools,
            "root_path": str(self.root_path),
            "source": self.source,
            "managed": self.managed,
            "managed_source": self.managed_source,
            "managed_ref": self.managed_ref,
        }
        if include_files:
            data["files"] = self.list_files()
        if include_body:
            data["body"] = self.body
        return data


def list_bundle_files(root: Path) -> List[str]:
    """List files inside a bundle directory, relative to root. Skips dotfiles and __pycache__."""
    results: List[str] = []

    def walk(dir_path: Path) -> None:
        for entry in sorted(dir_path.iterdir()):
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue
            if entry.is_dir():
                walk(entry)
            elif entry.is_file():
                results.append(str(entry.relative_to(root)))

    walk(root)
    return results


def _parse_frontmatter(content: str, source_path: Path) -> tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown file. Returns (frontmatter_dict, body_string)."""
    if not content.startswith("---\n"):
        log.warning(f"Skill file {source_path} has no YAML frontmatter")
        return {}, content

    parts = content.split("---\n", 2)
    if len(parts) < 3:
        log.warning(f"Skill file {source_path} has malformed frontmatter")
        return {}, content

    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as e:
        log.error(f"Invalid YAML in skill file {source_path}: {e}")
        raise ValueError(f"Invalid YAML frontmatter in {source_path}: {e}")

    if not isinstance(frontmatter, dict):
        log.warning(f"Skill frontmatter in {source_path} is not a mapping; ignoring")
        frontmatter = {}

    return frontmatter, parts[2].lstrip("\n")


def serialize_skill_md(
    name: str,
    description: str,
    allowed_tools: List[str],
    body: str,
    source: str = "",
    *,
    managed_source: str = "",
    managed_ref: str = "",
) -> str:
    """Render a SKILL.md markdown string from its fields."""
    frontmatter: Dict[str, Any] = {"name": name, "description": description}
    if allowed_tools:
        frontmatter["allowed-tools"] = list(allowed_tools)
    if source:
        frontmatter["source"] = source
    if managed_source:
        frontmatter["managed_source"] = managed_source
    if managed_ref:
        frontmatter["managed_ref"] = managed_ref
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False).strip()
    body_text = body.rstrip() + "\n" if body.strip() else ""
    return f"---\n{yaml_text}\n---\n\n{body_text}"

"""Loads the managed-skills manifest (YAML/JSON) from a URL or local file path.

The manifest describes the set of Claude skills that should be installed on this
notebook. See the README for the schema; briefly:

    skills:
      - url: https://github.com/org/repo/tree/main/skills/data-eda
        name: data-eda        # optional override of the installed skill name
        scope: user           # optional, "user" or "project" (default "user")
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

from notebook_intelligence.skill_github_import import _get_github_token
from notebook_intelligence.skillset import SKILL_NAME_PATTERN

log = logging.getLogger(__name__)

MAX_MANIFEST_BYTES = 1 * 1024 * 1024  # 1 MB
FETCH_TIMEOUT_SECONDS = 15.0
_VALID_SCOPES = ("user", "project")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_GITHUB_HOSTS = (
    "github.com",
    "www.github.com",
    "api.github.com",
    "raw.githubusercontent.com",
)


def _is_github_host(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return False
    return host in _GITHUB_HOSTS or host.endswith(".githubusercontent.com")


class ManifestError(ValueError):
    """Raised when a manifest cannot be loaded or fails validation."""


@dataclass
class ManifestEntry:
    url: str
    name: Optional[str] = None
    scope: str = "user"


@dataclass
class SkillsManifest:
    entries: List[ManifestEntry]


def load_manifest(source: str, *, token: Optional[str] = None) -> SkillsManifest:
    """Load and validate a manifest from a URL or local filesystem path.

    URLs use `Authorization: Bearer {token}` when token is provided; useful for
    manifests hosted on private GitHub repos via raw.githubusercontent.com.
    """
    if not isinstance(source, str) or not source.strip():
        raise ManifestError("Manifest source is empty")
    source = source.strip()

    raw = _read_source(source, token=token)
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ManifestError(f"Manifest is not valid YAML/JSON: {e}")

    if not isinstance(parsed, dict):
        raise ManifestError("Manifest root must be a mapping")
    raw_entries = parsed.get("skills")
    if raw_entries is None:
        raise ManifestError("Manifest must have a top-level 'skills' list")
    if not isinstance(raw_entries, list):
        raise ManifestError("'skills' must be a list")

    entries: List[ManifestEntry] = []
    for idx, item in enumerate(raw_entries):
        entries.append(_parse_entry(item, idx))

    return SkillsManifest(entries=entries)


def _parse_entry(item, index: int) -> ManifestEntry:
    if not isinstance(item, dict):
        raise ManifestError(f"skills[{index}] must be a mapping")
    url = item.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ManifestError(f"skills[{index}].url is required")

    name = item.get("name")
    if name is not None:
        if not isinstance(name, str) or not SKILL_NAME_PATTERN.match(name):
            raise ManifestError(
                f"skills[{index}].name '{name}' is not a valid skill name"
            )

    scope = item.get("scope", "user")
    if scope not in _VALID_SCOPES:
        raise ManifestError(
            f"skills[{index}].scope must be one of {_VALID_SCOPES}, got {scope!r}"
        )

    return ManifestEntry(url=url.strip(), name=name, scope=scope)


def _read_source(source: str, *, token: Optional[str]) -> str:
    if _URL_RE.match(source):
        return _fetch_url(source, token=token)
    # Treat as a filesystem path (e.g., k8s ConfigMap mount).
    path = Path(source).expanduser()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise ManifestError(f"Manifest file not found: {source}")
    except OSError as e:
        raise ManifestError(f"Could not read manifest file {source}: {e}")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"Manifest file exceeds {MAX_MANIFEST_BYTES} bytes"
        )
    return data.decode("utf-8", errors="replace")


def _fetch_url(url: str, *, token: Optional[str]) -> str:
    headers = {
        "User-Agent": "notebook-intelligence-skills-manifest",
        "Accept": "application/x-yaml, application/json, text/plain, */*",
    }
    # Explicit NBI_MANAGED_SKILLS_TOKEN wins. Otherwise, for github-hosted
    # manifests, fall back to the same GITHUB_TOKEN / GH_TOKEN / `gh auth token`
    # chain used for fetching skill tarballs — private-repo manifests should
    # just work when the user already has a GitHub login.
    effective_token = token
    if not effective_token and _is_github_host(url):
        effective_token = _get_github_token()
    if effective_token:
        headers["Authorization"] = f"Bearer {effective_token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
            data = resp.read(MAX_MANIFEST_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise ManifestError(f"Manifest fetch failed (HTTP {e.code}): {e.reason}")
    except urllib.error.URLError as e:
        raise ManifestError(f"Could not reach manifest URL {url}: {e.reason}")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"Manifest at {url} exceeds {MAX_MANIFEST_BYTES} bytes"
        )
    return data.decode("utf-8", errors="replace")

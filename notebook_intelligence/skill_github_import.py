"""Fetch and validate Claude skill bundles from public GitHub repositories.

URL formats supported:
  - https://github.com/{owner}/{repo}
  - https://github.com/{owner}/{repo}.git
  - https://github.com/{owner}/{repo}/tree/{ref}/{subpath}
  - https://github.com/{owner}/{repo}/tree/{ref}

Uses the GitHub tarball API over HTTPS; no git binary required. Auth is
optional — a token is picked up from GITHUB_TOKEN / GH_TOKEN / `gh auth token`
when available (needed for private repos, SSO, and higher rate limits).
"""
import io
import json
import logging
import os
import re
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from notebook_intelligence.skillset import (
    SKILL_ENTRY_FILE,
    SKILL_NAME_PATTERN,
    _parse_frontmatter,
    list_bundle_files,
)

log = logging.getLogger(__name__)

MAX_ARCHIVE_BYTES = 10 * 1024 * 1024  # 10 MB on-wire
MAX_EXTRACTED_BYTES = 20 * 1024 * 1024  # 20 MB after decompression
FETCH_TIMEOUT_SECONDS = 30


@dataclass
class GitHubRef:
    owner: str
    repo: str
    ref: Optional[str]  # branch/tag/sha; None → default branch
    subpath: str        # "" if skill is at repo root


@dataclass
class StagedSkill:
    """A validated skill extracted into a temporary directory."""
    name: str
    description: str
    allowed_tools: list
    body: str
    files: list  # all file paths relative to skill_root, excluding SKILL.md
    skill_root: Path
    tmp_root: Path  # temp dir created for this import; caller cleans up
    source_url: str
    canonical_url: str  # URL with resolved ref, for storing as `source`


def parse_github_url(url: str) -> GitHubRef:
    """Parse a GitHub URL into (owner, repo, ref, subpath). Raises ValueError."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError as e:
        raise ValueError(f"Invalid URL: {e}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use https://")
    if parsed.netloc not in ("github.com", "www.github.com"):
        raise ValueError("Only github.com URLs are supported")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(
            "URL must reference a repo: https://github.com/<owner>/<repo>"
        )
    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    ref: Optional[str] = None
    subpath = ""
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        ref = parts[3]
        subpath = "/".join(parts[4:])
    return GitHubRef(owner=owner, repo=repo, ref=ref, subpath=subpath)


def _tarball_url(owner: str, repo: str, ref: Optional[str]) -> str:
    ref_path = ref if ref else "HEAD"
    return f"https://api.github.com/repos/{owner}/{repo}/tarball/{ref_path}"


def _get_github_token() -> Optional[str]:
    """Look up a GitHub token for API auth.

    Order matches the gh CLI's own precedence: GITHUB_TOKEN, then GH_TOKEN,
    then whatever `gh auth token` returns. The gh-CLI fallback lets users in
    corporate/SSO environments inherit their existing login without setting
    an env var.
    """
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(var)
        if token and token.strip():
            return token.strip()
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    stdout = result.stdout.strip()
    if result.returncode == 0 and stdout:
        return stdout
    return None


def _github_api_headers(override_token: Optional[str] = None) -> dict:
    """Standard headers for GitHub API requests, with Bearer auth when available.

    If `override_token` is provided it is used directly (no fallback). Otherwise
    the standard `GITHUB_TOKEN` / `GH_TOKEN` / `gh auth token` chain is consulted.
    Managed-skill operations pass the deployment's scoped token explicitly so it
    doesn't get overridden by whatever the user has in their environment.
    """
    headers = {
        "User-Agent": "notebook-intelligence-skills-import",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = override_token if override_token is not None else _get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_tarball(
    owner: str, repo: str, ref: Optional[str], *, token: Optional[str] = None
) -> bytes:
    url = _tarball_url(owner, repo, ref)
    headers = _github_api_headers(override_token=token)
    has_token = "Authorization" in headers
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as response:
            data = response.read(MAX_ARCHIVE_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            if has_token:
                raise ValueError(
                    f"GitHub rejected the token (HTTP {e.code}). Check that "
                    "GITHUB_TOKEN / GH_TOKEN / `gh auth` has access to "
                    f"github.com/{owner}/{repo}."
                )
            raise ValueError(
                f"GitHub requires authentication (HTTP {e.code}). "
                "Set GITHUB_TOKEN or run `gh auth login`, then retry."
            )
        if e.code == 404:
            # GitHub returns 404 (not 403) for private repos to unauth'd callers,
            # so we can't distinguish "missing" from "private" — hint at both.
            hint = (
                ""
                if has_token
                else " (private repos require GITHUB_TOKEN or `gh auth login`)"
            )
            raise ValueError(
                f"Repo or ref not found: github.com/{owner}/{repo}"
                + (f" @ {ref}" if ref else "")
                + hint
            )
        raise ValueError(f"GitHub returned HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise ValueError(f"Could not reach GitHub: {e.reason}")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError(
            f"Archive exceeds size limit ({MAX_ARCHIVE_BYTES // (1024 * 1024)} MB)"
        )
    return data


def get_latest_commit_sha(
    owner: str,
    repo: str,
    ref: Optional[str],
    subpath: str,
    *,
    token: Optional[str] = None,
) -> Optional[str]:
    """Return the SHA of the most recent commit touching `subpath` on `ref`.

    Used by the managed-skills reconciler to skip re-downloading tarballs when
    nothing has changed. Returns None on any error (network, rate-limit, auth);
    callers should fall back to fetching the tarball.
    """
    params = {"per_page": "1"}
    if ref:
        params["sha"] = ref
    if subpath:
        params["path"] = subpath
    query = urllib.parse.urlencode(params)
    url = f"https://api.github.com/repos/{owner}/{repo}/commits?{query}"
    req = urllib.request.Request(url, headers=_github_api_headers(override_token=token))
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as response:
            data = response.read(64 * 1024)  # commits list is small; hard cap prevents abuse
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        log.warning("GitHub commits API probe failed for %s/%s: %s", owner, repo, e)
        return None
    try:
        parsed = json.loads(data)
    except (ValueError, TypeError) as e:
        log.warning("GitHub commits API returned invalid JSON: %s", e)
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    sha = parsed[0].get("sha") if isinstance(parsed[0], dict) else None
    return sha if isinstance(sha, str) and sha else None


def _extract_skill(
    tarball_bytes: bytes, subpath: str, into: Path
) -> Path:
    """Extract into `into`, validate, return the skill root directory."""
    total_bytes = 0
    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
        members = tar.getmembers()
        if not members:
            raise ValueError("Empty archive")
        # GitHub tarballs wrap every entry in a single `{repo}-{sha}/` top-level directory.
        # The first member is that directory itself, so its name is our wrapper prefix.
        top_dir = members[0].name.split("/")[0]
        safe_members = []
        for m in members:
            if m.issym() or m.islnk():
                # Skip symlinks for safety — they can escape the extraction dir.
                continue
            name = m.name.lstrip("/")
            parts = Path(name).parts
            if any(p == ".." for p in parts):
                raise ValueError(f"Unsafe path in archive: {m.name}")
            if m.isfile():
                total_bytes += m.size
                if total_bytes > MAX_EXTRACTED_BYTES:
                    raise ValueError(
                        "Extracted archive exceeds size limit "
                        f"({MAX_EXTRACTED_BYTES // (1024 * 1024)} MB)"
                    )
            safe_members.append(m)
        tar.extractall(into, members=safe_members)

    extracted = into / top_dir
    if not extracted.is_dir():
        raise ValueError("Unexpected archive layout")
    skill_root = extracted / subpath if subpath else extracted
    if not skill_root.is_dir():
        raise ValueError(f"Path '{subpath}' not found in repo")
    if not (skill_root / SKILL_ENTRY_FILE).exists():
        location = subpath if subpath else "repo root"
        raise ValueError(f"No {SKILL_ENTRY_FILE} found at {location}")
    return skill_root


def _slug(value: str) -> str:
    """Normalize an arbitrary string into a valid skill-name candidate."""
    s = value.lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:64] if s else ""


def _derive_name(
    frontmatter_name: Optional[str], ref: GitHubRef
) -> str:
    """Pick a default skill name: frontmatter > subpath basename > repo name."""
    candidates = []
    if frontmatter_name:
        candidates.append(frontmatter_name)
    if ref.subpath:
        candidates.append(Path(ref.subpath).name)
    candidates.append(ref.repo)
    for c in candidates:
        slug = _slug(c)
        if SKILL_NAME_PATTERN.match(slug):
            return slug
    raise ValueError(
        "Could not derive a valid skill name from the repo; "
        "specify one explicitly."
    )


def stage_skill_from_github(url: str, *, token: Optional[str] = None) -> StagedSkill:
    """Fetch, extract, and validate a skill. Returns a StagedSkill.

    Caller is responsible for cleaning up `staged.tmp_root` when done. An
    explicit `token` overrides the standard GITHUB_TOKEN / GH_TOKEN / gh-CLI
    chain — used by the managed-skills reconciler to scope tarball fetches to
    the deployment's provided token.
    """
    import shutil

    ref = parse_github_url(url)
    tarball = _fetch_tarball(ref.owner, ref.repo, ref.ref, token=token)
    tmp_root = Path(tempfile.mkdtemp(prefix="nbi-skill-import-"))
    try:
        skill_root = _extract_skill(tarball, ref.subpath, tmp_root)
        skill_md = skill_root / SKILL_ENTRY_FILE
        try:
            frontmatter, body = _parse_frontmatter(
                skill_md.read_text(encoding="utf-8"), skill_md
            )
        except Exception as e:
            raise ValueError(f"Invalid {SKILL_ENTRY_FILE}: {e}")

        description = frontmatter.get("description") or ""
        allowed_tools = list(frontmatter.get("allowed-tools", []) or [])
        name = _derive_name(frontmatter.get("name"), ref)
        files = [f for f in list_bundle_files(skill_root) if f != SKILL_ENTRY_FILE]
    except Exception:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise

    canonical = (
        f"https://github.com/{ref.owner}/{ref.repo}"
        + (f"/tree/{ref.ref}" if ref.ref else "")
        + (f"/{ref.subpath}" if ref.subpath else "")
    )

    return StagedSkill(
        name=name,
        description=description,
        allowed_tools=allowed_tools,
        body=body,
        files=files,
        skill_root=skill_root,
        tmp_root=tmp_root,
        source_url=url,
        canonical_url=canonical,
    )

"""Background reconciliation of managed Claude skills against a manifest.

Reads a manifest (`NBI_SKILLS_MANIFEST`) listing Claude skills to install from
GitHub, installs/updates them, and removes managed skills that have been dropped
from the manifest. User-authored skills are never touched.

See `skill_manifest.py` for the manifest schema.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, cast

from notebook_intelligence.skill_github_import import (
    get_latest_commit_sha,
    parse_github_url,
)
from notebook_intelligence.skill_manager import SkillManager
from notebook_intelligence.skill_manifest import (
    ManifestEntry,
    ManifestError,
    load_manifest,
)
from notebook_intelligence.skillset import Skill, SkillScope

log = logging.getLogger(__name__)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class ReconcileResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: List[str] = field(default_factory=list)

    def mutated(self) -> bool:
        return self.added or self.updated or self.removed

    def to_dict(self) -> dict:
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "errors": list(self.errors),
        }


class SkillReconciler:
    """Reconciles installed managed skills against a manifest on a schedule."""

    def __init__(
        self,
        skill_manager: SkillManager,
        manifest_source: str,
        interval_seconds: int,
        managed_token: Optional[str] = None,
    ):
        self._skill_manager = skill_manager
        self._manifest_source = manifest_source
        self._interval_seconds = max(int(interval_seconds), 1)
        # Used for manifest fetch, commits-API probe, and tarball download — the
        # whole managed pathway. User-initiated imports do not see this token.
        self._managed_token = managed_token
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def reconcile(self) -> ReconcileResult:
        """One pass: load manifest, apply installs/updates/removes. Synchronous."""
        result = ReconcileResult()
        try:
            manifest = load_manifest(
                self._manifest_source, token=self._managed_token
            )
        except ManifestError as e:
            msg = f"Could not load manifest: {e}"
            log.error(msg)
            result.errors.append(msg)
            return result

        # Snapshot managed skills once per cycle. Both the per-entry apply and
        # the stale-removal pass read from this dict, avoiding an O(N*M) disk
        # re-scan for every manifest entry.
        managed_by_url: Dict[str, Skill] = {
            s.managed_source: s
            for s in self._skill_manager.list_managed_skills()
            if s.managed_source
        }
        manifest_urls = {entry.url for entry in manifest.entries}

        for entry in manifest.entries:
            try:
                self._apply_entry(entry, managed_by_url, result)
            except Exception as e:  # noqa: BLE001 — per-entry isolation
                msg = f"{entry.url}: {e}"
                log.exception("Failed to reconcile skill %s", entry.url)
                result.errors.append(msg)

        self._remove_stale(manifest_urls, managed_by_url, result)

        # Intermediate install/delete calls each fire the skill_manager's listeners
        # already; the downstream Claude client update is debounced, so per-entry
        # notifications coalesce into a single reconnect.

        return result

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            name="Skill Reconciler",
            target=self._run_loop,
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        thread = self._thread
        self._stop.set()
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    # -- internals ----------------------------------------------------------

    def _run_loop(self) -> None:
        # First pass runs immediately, then we sleep between reconciles.
        while not self._stop.is_set():
            try:
                self.reconcile()
            except Exception:  # noqa: BLE001 — thread must survive any error
                log.exception("Reconciler pass raised; continuing")
            if self._stop.wait(self._interval_seconds):
                return

    def _apply_entry(
        self,
        entry: ManifestEntry,
        managed_by_url: Dict[str, Skill],
        result: ReconcileResult,
    ) -> None:
        ref_info = parse_github_url(entry.url)
        existing = managed_by_url.get(entry.url)
        desired_sha = self._resolve_desired_sha(ref_info)

        # Already installed and the SHA still matches — nothing to do.
        if existing is not None and desired_sha and existing.managed_ref == desired_sha:
            result.unchanged += 1
            return

        # Probe failed (None) and we already have this skill installed — leave it
        # alone rather than re-downloading the tarball every cycle on transient
        # commits-API errors. A genuine upstream update will produce a concrete
        # SHA on a later cycle.
        if existing is not None and desired_sha is None:
            result.unchanged += 1
            return

        self._skill_manager.install_managed_from_github(
            url=entry.url,
            scope=cast(SkillScope, entry.scope),
            managed_source=entry.url,
            managed_ref=desired_sha or "",
            name_override=entry.name,
            token=self._managed_token,
        )
        if existing is not None:
            result.updated += 1
        else:
            result.added += 1

    def _resolve_desired_sha(self, ref_info) -> Optional[str]:
        # If the ref is already a full SHA, no API probe needed.
        if ref_info.ref and _SHA_RE.match(ref_info.ref):
            return ref_info.ref
        return get_latest_commit_sha(
            ref_info.owner,
            ref_info.repo,
            ref_info.ref,
            ref_info.subpath,
            token=self._managed_token,
        )

    def _remove_stale(
        self,
        manifest_urls: set,
        managed_by_url: Dict[str, Skill],
        result: ReconcileResult,
    ) -> None:
        for url, skill in managed_by_url.items():
            if url in manifest_urls:
                continue
            try:
                self._skill_manager.delete_skill(skill.scope, skill.name)
                result.removed += 1
            except Exception as e:  # noqa: BLE001
                msg = f"Could not remove stale managed skill {skill.name}: {e}"
                log.exception(msg)
                result.errors.append(msg)

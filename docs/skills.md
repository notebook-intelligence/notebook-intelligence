# Claude Skills

When [Claude mode](../README.md#claude-mode) is enabled, NBI exposes a **Skills** tab in the settings panel for viewing, creating, editing, and deleting the skills that Claude can invoke.

Skills are Claude Agent SDK artifacts stored on disk:

- **User skills**: `~/.claude/skills/`
- **Project skills**: `<project>/.claude/skills/`

Each skill lives in a directory named after the skill, containing a `SKILL.md` entry file (with YAML frontmatter for `name`, `description`, and `allowed-tools`) plus any helper files the skill references.

## The Skills tab

From the tab you can:

- **Add** a new skill in either scope, editing `SKILL.md` and helper files inline.
- **Rename** a skill — NBI updates both the bundle directory and the frontmatter `name`.
- **Duplicate** a skill into the same or opposite scope under a new name.
- **Delete** a skill, with an undo toast that restores the full bundle if clicked within eight seconds.
- Open or delete additional files in the bundle (`SKILL.md` itself is protected from deletion).

When a skill is saved, added, or removed — through the UI or directly on disk — NBI transparently reloads the Claude SDK session (preserving conversation history via session resume), and a **"Skills reloaded"** banner briefly appears in the chat sidebar.

## Importing from GitHub

Click **Import from GitHub** in the Skills tab to install a skill from a public repository.

Paste either:

- A repository URL: `https://github.com/<owner>/<repo>` — imports the repo root if it contains `SKILL.md`.
- A deep link: `https://github.com/<owner>/<repo>/tree/<ref>/<subpath>` — imports the directory at `<subpath>`.

Pick the target scope (user or project) and NBI fetches, validates, and installs the bundle. The canonical source URL is recorded in the skill's frontmatter as `source:` so you can trace where each imported skill came from.

GitHub auth for imports uses, in order: `GITHUB_TOKEN` → `GH_TOKEN` → `gh` CLI auth. Public-repo imports work without auth.

## Managed skills via an org manifest

For organization-wide deployments (e.g., Kubeflow notebooks), NBI can install and keep a curated set of Claude skills in sync from a YAML or JSON manifest. Skills installed this way are marked **Managed** in the UI — they are read-only (edit, rename, and delete are disabled) and refreshed on a schedule.

### Configuration

Configure via environment variables (also available as traitlets on `NotebookIntelligence`):

| Variable                       | Description                                                                                        |
| ------------------------------ | -------------------------------------------------------------------------------------------------- |
| `NBI_SKILLS_MANIFEST`          | URL (`https://...`) or local filesystem path to the manifest. Empty or unset disables the feature. |
| `NBI_SKILLS_MANIFEST_INTERVAL` | Seconds between reconciles. Default `86400` (24 hours). Reconciliation also runs once at startup.  |
| `NBI_MANAGED_SKILLS_TOKEN`     | Optional bearer token used for **all** managed-skills GitHub operations (see below).               |

When set, `NBI_MANAGED_SKILLS_TOKEN` scopes to fetching the manifest, probing commits, and downloading skill tarballs. User-initiated imports (the Import-from-GitHub dialog, `POST /skills/import`) do **not** see this token and continue to use `GITHUB_TOKEN` → `GH_TOKEN` → `gh` CLI auth. When `NBI_MANAGED_SKILLS_TOKEN` is unset, managed operations fall back to that same chain. When it is set and a managed operation fails with an auth error, it fails loudly (no retry with the fallback chain) so misconfigured or expired tokens stay visible to the admin. The minimum required GitHub scope is `contents:read`.

### Manifest schema

```yaml
skills:
  - url: https://github.com/org/repo/tree/main/skills/data-eda
    name: data-eda # optional: override the installed skill name
    scope: user # optional: "user" (default) or "project"
  - url: https://github.com/org/repo/tree/main/skills/ml-recipes
```

JSON is also accepted (the parser is `yaml.safe_load`).

### Reconciler behavior

- The reconciler probes GitHub's commits API for each entry's `subpath` and `ref`, and skips fetching the tarball when the installed `managed_ref` matches the latest SHA. Full-SHA URLs skip the probe.
- Managed skills present in the install but missing from the manifest are **removed**.
- User-authored skills are never touched. If a user-authored skill has the same name as a manifest entry, the reconciler leaves it alone and reports a per-entry error.
- A manual **Sync managed skills** button appears in the Skills panel when any managed skill is installed.
- A `POST /notebook-intelligence/skills/reconcile` endpoint is available for scripted triggers.
- If the manifest cannot be loaded (network failure, bad YAML, missing `skills:` list), the reconciler logs the error and leaves all managed skills in place rather than mass-deleting on a transient failure.

### Multi-tenant scoping

Different JupyterHub profiles or spawner configurations can point at different manifests by setting `NBI_SKILLS_MANIFEST` per profile. Within a single user's install, skills are namespaced by name — so two profiles that both install a skill called `data-eda` will collide if a user moves between them. Use distinct skill names across teams to avoid this.

### Disabling user-initiated GitHub imports

There is currently no flag that disables the **Import from GitHub** button. If your deployment cannot allow users to pull arbitrary public repos, gate egress to `github.com` and `api.github.com` at the network layer and rely on `NBI_SKILLS_MANIFEST` with a private internal manifest URL. Tracked as a future feature.

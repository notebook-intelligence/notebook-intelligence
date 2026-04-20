// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState
} from 'react';
import { Dialog, showDialog } from '@jupyterlab/apputils';
import {
  ISkillDetail,
  ISkillImportPreview,
  ISkillsContext,
  ISkillSummary,
  NBIAPI,
  SkillScope
} from '../api';

// Must match SKILL_NAME_PATTERN in notebook_intelligence/skillset.py
const SKILL_NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const SKILL_NAME_REQUIREMENT =
  'Must be lowercase letters, digits, or hyphens (starting with a letter or digit), max 64 chars.';
const SKILL_ENTRY_FILE = 'SKILL.md';
const COMMON_TOOLS = [
  'Read',
  'Write',
  'Edit',
  'Bash',
  'Glob',
  'Grep',
  'Task',
  'TodoWrite',
  'WebFetch',
  'WebSearch',
  'NotebookEdit'
];

type ViewMode =
  | { kind: 'list' }
  | { kind: 'editor'; scope: SkillScope; name: string | null };

type PromptMode =
  | null
  | { kind: 'rename'; skill: ISkillSummary }
  | { kind: 'duplicate'; skill: ISkillSummary };

interface IUndoState {
  detail: ISkillDetail;
  bundleFiles: { path: string; content: string }[];
  timerId: number;
}

export function SettingsPanelComponentSkills(_props: any): JSX.Element {
  const [skills, setSkills] = useState<ISkillSummary[]>([]);
  const [context, setContext] = useState<ISkillsContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>({ kind: 'list' });
  const [prompt, setPrompt] = useState<PromptMode>(null);
  const [undo, setUndo] = useState<IUndoState | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const hasManagedSkills = skills.some(s => s.managed);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await NBIAPI.listSkills();
      setSkills(list);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    NBIAPI.getSkillsContext()
      .then(setContext)
      .catch(() => {
        // Non-fatal — panel still works without the context hints.
      });
    const listener = () => {
      refresh();
    };
    NBIAPI.skillsReloaded.connect(listener);
    return () => {
      NBIAPI.skillsReloaded.disconnect(listener);
    };
  }, []);

  const undoRef = useRef<IUndoState | null>(null);
  undoRef.current = undo;

  const dismissUndo = () => {
    setUndo(prev => {
      if (prev) {
        window.clearTimeout(prev.timerId);
      }
      return null;
    });
  };

  useEffect(() => {
    return () => {
      if (undoRef.current) {
        window.clearTimeout(undoRef.current.timerId);
      }
    };
  }, []);

  const handleSyncManaged = async () => {
    setSyncing(true);
    setSyncMessage(null);
    setError(null);
    try {
      const r = await NBIAPI.reconcileManagedSkills();
      const summary = `Sync complete — ${r.added} added, ${r.updated} updated, ${r.removed} removed, ${r.unchanged} unchanged.`;
      setSyncMessage(
        r.errors.length ? `${summary} (${r.errors.length} error(s))` : summary
      );
      if (r.errors.length) {
        setError(r.errors.join('\n'));
      }
      await refresh();
    } catch (e: any) {
      setError(`Sync failed: ${e?.message ?? e}`);
    } finally {
      setSyncing(false);
    }
  };

  const handleDelete = async (skill: ISkillSummary) => {
    const result = await showDialog({
      title: 'Delete skill?',
      body: `"${skill.name}" will be deleted.`,
      buttons: [Dialog.cancelButton(), Dialog.warnButton({ label: 'Delete' })]
    });
    if (!result.button.accept) {
      return;
    }
    // Snapshot the full bundle (SKILL.md + helper files) *before* deleting so the Undo
    // toast can recreate it byte-for-byte. The backend only exposes a shallow delete
    // API, so restoration is the client's job.
    let detail: ISkillDetail;
    try {
      detail = await NBIAPI.readSkill(skill.scope, skill.name);
    } catch (e: any) {
      setError(`Failed to read skill: ${e?.message ?? e}`);
      return;
    }
    const pathsToSnapshot = (detail.files ?? []).filter(
      p => p !== SKILL_ENTRY_FILE
    );
    const snapshots = await Promise.all(
      pathsToSnapshot.map(async path => {
        try {
          const content = await NBIAPI.readBundleFile(
            skill.scope,
            skill.name,
            path
          );
          return { path, content };
        } catch {
          // Best-effort snapshot — skip unreadable files.
          return null;
        }
      })
    );
    const bundleFiles = snapshots.filter(
      (s): s is { path: string; content: string } => s !== null
    );
    try {
      await NBIAPI.deleteSkill(skill.scope, skill.name);
      await refresh();
    } catch (e: any) {
      setError(`Failed to delete skill: ${e?.message ?? e}`);
      return;
    }
    dismissUndo();
    const timerId = window.setTimeout(() => {
      setUndo(null);
    }, 8000);
    setUndo({ detail, bundleFiles, timerId });
  };

  const handleUndoDelete = async () => {
    if (!undo) {
      return;
    }
    const { detail, bundleFiles } = undo;
    dismissUndo();
    try {
      await NBIAPI.createSkill({
        scope: detail.scope,
        name: detail.name,
        description: detail.description,
        allowedTools: detail.allowedTools,
        body: detail.body
      });
      await Promise.all(
        bundleFiles.map(({ path, content }) =>
          NBIAPI.writeBundleFile(
            detail.scope,
            detail.name,
            path,
            content
          ).catch(() => {
            // Non-fatal — skill is restored even if a bundle file fails.
          })
        )
      );
      await refresh();
    } catch (e: any) {
      setError(`Failed to restore skill: ${e?.message ?? e}`);
    }
  };

  const handleRename = (skill: ISkillSummary) => {
    setPrompt({ kind: 'rename', skill });
  };

  const handleDuplicate = (skill: ISkillSummary) => {
    setPrompt({ kind: 'duplicate', skill });
  };

  const commitRename = async (skill: ISkillSummary, newName: string) => {
    await NBIAPI.renameSkill(skill.scope, skill.name, newName);
    setPrompt(null);
    await refresh();
  };

  const commitDuplicate = async (
    skill: ISkillSummary,
    targetScope: SkillScope,
    newName: string
  ) => {
    const detail = await NBIAPI.readSkill(skill.scope, skill.name);
    await NBIAPI.createSkill({
      scope: targetScope,
      name: newName,
      description: detail.description,
      allowedTools: detail.allowedTools,
      body: detail.body
    });
    const filesToCopy = detail.files.filter(f => f !== SKILL_ENTRY_FILE);
    await Promise.all(
      filesToCopy.map(async file => {
        const content = await NBIAPI.readBundleFile(
          skill.scope,
          skill.name,
          file
        );
        await NBIAPI.writeBundleFile(targetScope, newName, file, content);
      })
    );
    setPrompt(null);
    await refresh();
  };

  if (view.kind === 'editor') {
    return (
      <SkillEditor
        scope={view.scope}
        name={view.name}
        onClose={async () => {
          await refresh();
          setView({ kind: 'list' });
        }}
      />
    );
  }

  const userSkills = skills.filter(s => s.scope === 'user');
  const projectSkills = skills.filter(s => s.scope === 'project');

  return (
    <div className="config-dialog-body nbi-skills-panel">
      <div className="nbi-skills-header">
        <div className="nbi-skills-title">Claude Skills</div>
        <div className="nbi-skills-header-actions">
          {hasManagedSkills && (
            <button
              className="jp-Dialog-button jp-mod-reject jp-mod-styled"
              onClick={handleSyncManaged}
              disabled={syncing}
              title="Reconcile managed skills against the org manifest"
            >
              <div className="jp-Dialog-buttonLabel">
                {syncing ? 'Syncing…' : 'Sync managed skills'}
              </div>
            </button>
          )}
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={() => setImportOpen(true)}
            title="Import from GitHub"
          >
            <div className="jp-Dialog-buttonLabel">
              {hasManagedSkills ? 'Import' : 'Import from GitHub'}
            </div>
          </button>
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={() =>
              setView({ kind: 'editor', scope: 'user', name: null })
            }
          >
            <div className="jp-Dialog-buttonLabel">New Skill</div>
          </button>
        </div>
      </div>
      {syncMessage && (
        <div className="nbi-skills-sync-message" role="status">
          {syncMessage}
        </div>
      )}
      {error && (
        <div className="nbi-skills-error" role="alert">
          {error}
        </div>
      )}
      <SkillScopeSection
        scope="user"
        label="USER"
        pathHint={context?.userSkillsDir || '~/.claude/skills/'}
        skills={userSkills}
        loading={loading}
        onEdit={s => setView({ kind: 'editor', scope: s.scope, name: s.name })}
        onNew={() => setView({ kind: 'editor', scope: 'user', name: null })}
        onRename={handleRename}
        onDuplicate={handleDuplicate}
        onDelete={handleDelete}
      />
      <SkillScopeSection
        scope="project"
        label={
          context?.projectName ? `PROJECT · ${context.projectName}` : 'PROJECT'
        }
        pathHint={context?.projectSkillsDir || '<project>/.claude/skills/'}
        skills={projectSkills}
        loading={loading}
        onEdit={s => setView({ kind: 'editor', scope: s.scope, name: s.name })}
        onNew={() => setView({ kind: 'editor', scope: 'project', name: null })}
        onRename={handleRename}
        onDuplicate={handleDuplicate}
        onDelete={handleDelete}
      />
      {prompt && (
        <SkillPromptDialog
          prompt={prompt}
          existingNames={skills}
          onCancel={() => setPrompt(null)}
          onRename={commitRename}
          onDuplicate={commitDuplicate}
        />
      )}
      {undo && (
        <UndoToast
          message={`Deleted "${undo.detail.name}"`}
          onUndo={handleUndoDelete}
          onDismiss={dismissUndo}
        />
      )}
      {importOpen && (
        <GitHubImportDialog
          onCancel={() => setImportOpen(false)}
          onImported={async () => {
            setImportOpen(false);
            await refresh();
          }}
        />
      )}
    </div>
  );
}

function GitHubImportDialog(props: {
  onCancel: () => void;
  onImported: () => Promise<void> | void;
}): JSX.Element {
  type Step = 'url' | 'preview';
  const [step, setStep] = useState<Step>('url');
  const [url, setUrl] = useState('');
  const [scope, setScope] = useState<SkillScope>('user');
  const [preview, setPreview] = useState<ISkillImportPreview | null>(null);
  const [nameOverride, setNameOverride] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveName = (nameOverride.trim() || preview?.name || '').trim();
  const nameValid = SKILL_NAME_PATTERN.test(effectiveName);
  const collides =
    preview !== null &&
    ((scope === 'user' && preview.existsInUserScope) ||
      (scope === 'project' && preview.existsInProjectScope)) &&
    effectiveName === preview.name;

  const canFetchPreview = !busy && url.trim().length > 0;
  const canInstall =
    !busy && preview !== null && nameValid && (!collides || overwrite);

  const handleFetchPreview = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canFetchPreview) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const p = await NBIAPI.previewSkillImport(url.trim());
      setPreview(p);
      setNameOverride('');
      setOverwrite(false);
      setStep('preview');
    } catch (err: any) {
      setError(err?.message ?? String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleInstall = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canInstall || !preview) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await NBIAPI.importSkill({
        url: url.trim(),
        scope,
        name: effectiveName !== preview.name ? effectiveName : undefined,
        overwrite: collides ? true : undefined
      });
      await props.onImported();
    } catch (err: any) {
      setError(err?.message ?? String(err));
      setBusy(false);
    }
  };

  return (
    <div className="nbi-modal-backdrop" onClick={props.onCancel}>
      <form
        className="nbi-modal-card"
        onClick={e => e.stopPropagation()}
        onSubmit={step === 'url' ? handleFetchPreview : handleInstall}
      >
        <div className="nbi-modal-title">Import skill from GitHub</div>
        <div className="nbi-modal-body">
          {step === 'url' && (
            <>
              <div className="nbi-form-field">
                <label htmlFor="nbi-import-url">GitHub repo URL</label>
                <input
                  id="nbi-import-url"
                  type="text"
                  autoFocus
                  value={url}
                  onChange={e => setUrl(e.target.value)}
                  placeholder="https://github.com/owner/repo or .../tree/main/path/to/skill"
                  onKeyDown={e => {
                    if (e.key === 'Escape') {
                      e.preventDefault();
                      props.onCancel();
                    }
                  }}
                />
                <div className="nbi-form-hint">
                  Public repos only. Link to the repo root, a branch, or a
                  subdirectory containing <code>SKILL.md</code>.
                </div>
              </div>
              <div className="nbi-form-field">
                <label htmlFor="nbi-import-scope">Install into</label>
                <select
                  id="nbi-import-scope"
                  value={scope}
                  onChange={e => setScope(e.target.value as SkillScope)}
                >
                  <option value="user">User (available in all projects)</option>
                  <option value="project">Project (this project only)</option>
                </select>
              </div>
            </>
          )}
          {step === 'preview' && preview && (
            <>
              <div className="nbi-import-preview">
                <div className="nbi-import-preview-row">
                  <span className="nbi-import-preview-label">Name</span>
                  <span className="nbi-import-preview-value">
                    {preview.name}
                  </span>
                </div>
                {preview.description && (
                  <div className="nbi-import-preview-row">
                    <span className="nbi-import-preview-label">
                      Description
                    </span>
                    <span className="nbi-import-preview-value">
                      {preview.description}
                    </span>
                  </div>
                )}
                {preview.allowedTools.length > 0 && (
                  <div className="nbi-import-preview-row">
                    <span className="nbi-import-preview-label">
                      Allowed tools
                    </span>
                    <span className="nbi-import-preview-value">
                      {preview.allowedTools.join(', ')}
                    </span>
                  </div>
                )}
                <div className="nbi-import-preview-row">
                  <span className="nbi-import-preview-label">Files</span>
                  <span className="nbi-import-preview-value">
                    SKILL.md
                    {preview.files.length > 0 &&
                      ` + ${preview.files.length} other${preview.files.length === 1 ? '' : 's'}`}
                  </span>
                </div>
                <div className="nbi-import-preview-row">
                  <span className="nbi-import-preview-label">Source</span>
                  <span className="nbi-import-preview-value nbi-import-preview-source">
                    {preview.canonicalUrl}
                  </span>
                </div>
              </div>
              <div className="nbi-form-field">
                <label htmlFor="nbi-import-name">
                  Name (optional override)
                </label>
                <input
                  id="nbi-import-name"
                  type="text"
                  value={nameOverride}
                  onChange={e => setNameOverride(e.target.value)}
                  placeholder={preview.name}
                  aria-invalid={
                    effectiveName.length > 0 && !nameValid ? true : undefined
                  }
                />
                {effectiveName.length > 0 && !nameValid && (
                  <div className="nbi-form-field-error">
                    {SKILL_NAME_REQUIREMENT}
                  </div>
                )}
              </div>
              {collides && (
                <div className="nbi-form-field">
                  <label className="nbi-checkbox-label">
                    <input
                      type="checkbox"
                      checked={overwrite}
                      onChange={e => setOverwrite(e.target.checked)}
                    />
                    Overwrite existing {scope} skill "{preview.name}"
                  </label>
                </div>
              )}
            </>
          )}
        </div>
        {error && (
          <div className="nbi-skills-error" role="alert">
            {error}
          </div>
        )}
        <div className="nbi-modal-actions">
          {step === 'preview' && (
            <button
              type="button"
              className="jp-Dialog-button jp-mod-reject jp-mod-styled"
              onClick={() => {
                setStep('url');
                setPreview(null);
                setError(null);
              }}
            >
              <div className="jp-Dialog-buttonLabel">Back</div>
            </button>
          )}
          <button
            type="button"
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={props.onCancel}
          >
            <div className="jp-Dialog-buttonLabel">Cancel</div>
          </button>
          {step === 'url' ? (
            <button
              type="submit"
              className="jp-Dialog-button jp-mod-accept jp-mod-styled"
              disabled={!canFetchPreview}
            >
              <div className="jp-Dialog-buttonLabel">
                {busy ? 'Fetching…' : 'Next'}
              </div>
            </button>
          ) : (
            <button
              type="submit"
              className="jp-Dialog-button jp-mod-accept jp-mod-styled"
              disabled={!canInstall}
            >
              <div className="jp-Dialog-buttonLabel">
                {busy ? 'Installing…' : 'Install'}
              </div>
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

function SkillScopeSection(props: {
  scope: SkillScope;
  label: string;
  pathHint: string;
  skills: ISkillSummary[];
  loading: boolean;
  onEdit: (skill: ISkillSummary) => void;
  onNew: () => void;
  onRename: (skill: ISkillSummary) => void;
  onDuplicate: (skill: ISkillSummary) => void;
  onDelete: (skill: ISkillSummary) => void;
}): JSX.Element {
  return (
    <div className="nbi-skills-section">
      <div className="nbi-skills-section-caption" title={props.pathHint}>
        {props.label} · {props.skills.length}
      </div>
      {props.loading && props.skills.length === 0 && (
        <div className="nbi-skills-empty">Loading…</div>
      )}
      {!props.loading && props.skills.length === 0 && (
        <div className="nbi-skills-empty">
          <span>
            No {props.scope} skills. They live in <code>{props.pathHint}</code>.
          </span>
          <button
            className="jp-toast-button jp-mod-small jp-Button"
            onClick={props.onNew}
          >
            <div className="jp-Dialog-buttonLabel">
              + New {props.scope} skill
            </div>
          </button>
        </div>
      )}
      {props.skills.map(skill => (
        <SkillRow
          key={`${skill.scope}:${skill.name}`}
          skill={skill}
          onEdit={() => props.onEdit(skill)}
          onRename={() => props.onRename(skill)}
          onDuplicate={() => props.onDuplicate(skill)}
          onDelete={() => props.onDelete(skill)}
        />
      ))}
    </div>
  );
}

function ManagedBadge(props: { source?: string }): JSX.Element {
  return (
    <span
      className="nbi-skill-managed-badge"
      title={`Managed by org manifest (${props.source ?? ''})`}
    >
      Managed
    </span>
  );
}

function SkillRow(props: {
  skill: ISkillSummary;
  onEdit: () => void;
  onRename: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}): JSX.Element {
  const { skill } = props;
  const stopAnd = (fn: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    fn();
  };
  return (
    <div
      className="nbi-skill-row"
      onClick={props.onEdit}
      role="button"
      tabIndex={0}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          props.onEdit();
        }
      }}
    >
      <div className="nbi-skill-row-main">
        <div className="nbi-skill-row-name">
          {skill.name}
          {skill.managed && <ManagedBadge source={skill.managedSource} />}
        </div>
        {skill.description && (
          <div className="nbi-skill-row-description">{skill.description}</div>
        )}
      </div>
      <div className="nbi-skill-row-actions" onClick={e => e.stopPropagation()}>
        <button
          type="button"
          className="nbi-icon-button"
          aria-label={skill.managed ? 'View skill' : 'Edit skill'}
          title={skill.managed ? 'View (managed, read-only)' : 'Edit'}
          onClick={stopAnd(props.onEdit)}
        >
          ✎
        </button>
        {!skill.managed && (
          <button
            type="button"
            className="nbi-icon-button"
            aria-label="Rename skill"
            title="Rename"
            onClick={stopAnd(props.onRename)}
          >
            Aa
          </button>
        )}
        <button
          type="button"
          className="nbi-icon-button"
          aria-label="Duplicate skill"
          title="Duplicate"
          onClick={stopAnd(props.onDuplicate)}
        >
          ⧉
        </button>
        {!skill.managed && (
          <button
            type="button"
            className="nbi-icon-button danger"
            aria-label="Delete skill"
            title="Delete"
            onClick={stopAnd(props.onDelete)}
          >
            🗑
          </button>
        )}
      </div>
    </div>
  );
}

function SkillPromptDialog(props: {
  prompt: Exclude<PromptMode, null>;
  existingNames: ISkillSummary[];
  onCancel: () => void;
  onRename: (skill: ISkillSummary, newName: string) => Promise<void>;
  onDuplicate: (
    skill: ISkillSummary,
    scope: SkillScope,
    newName: string
  ) => Promise<void>;
}): JSX.Element {
  const { prompt } = props;
  const isRename = prompt.kind === 'rename';
  const initialName = isRename
    ? prompt.skill.name
    : `${prompt.skill.name}-copy`;
  const initialScope: SkillScope = isRename
    ? prompt.skill.scope
    : prompt.skill.scope === 'user'
      ? 'project'
      : 'user';
  const [name, setName] = useState(initialName);
  const [scope, setScope] = useState<SkillScope>(initialScope);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const trimmed = name.trim();
  const nameValid = SKILL_NAME_PATTERN.test(trimmed);
  const isUnchangedRename = isRename && trimmed === prompt.skill.name;
  const conflict = props.existingNames.some(
    s => s.scope === scope && s.name === trimmed && !isUnchangedRename
  );
  const canSubmit = !busy && nameValid && !conflict && !isUnchangedRename;

  const title = isRename ? 'Rename skill' : 'Duplicate skill';
  const submitLabel = isRename ? 'Rename' : 'Duplicate';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (isRename) {
        await props.onRename(prompt.skill, trimmed);
      } else {
        await props.onDuplicate(prompt.skill, scope, trimmed);
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
      setBusy(false);
    }
  };

  return (
    <div className="nbi-modal-backdrop" onClick={props.onCancel}>
      <form
        className="nbi-modal-card"
        onClick={e => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <div className="nbi-modal-title">{title}</div>
        <div className="nbi-modal-body">
          {!isRename && (
            <div className="nbi-form-field">
              <label htmlFor="nbi-dup-scope">Target scope</label>
              <select
                id="nbi-dup-scope"
                value={scope}
                onChange={e => setScope(e.target.value as SkillScope)}
              >
                <option value="user">User</option>
                <option value="project">Project</option>
              </select>
            </div>
          )}
          <div className="nbi-form-field">
            <label htmlFor="nbi-prompt-name">New name</label>
            <input
              id="nbi-prompt-name"
              type="text"
              autoFocus
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Escape') {
                  e.preventDefault();
                  props.onCancel();
                }
              }}
              aria-invalid={
                (!nameValid && trimmed.length > 0) || conflict
                  ? true
                  : undefined
              }
            />
            {trimmed.length > 0 && !nameValid && (
              <div className="nbi-form-field-error">
                {SKILL_NAME_REQUIREMENT}
              </div>
            )}
            {conflict && (
              <div className="nbi-form-field-error">
                A {scope} skill named "{trimmed}" already exists.
              </div>
            )}
          </div>
        </div>
        {error && (
          <div className="nbi-skills-error" role="alert">
            {error}
          </div>
        )}
        <div className="nbi-modal-actions">
          <button
            type="button"
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={props.onCancel}
          >
            <div className="jp-Dialog-buttonLabel">Cancel</div>
          </button>
          <button
            type="submit"
            className="jp-Dialog-button jp-mod-accept jp-mod-styled"
            disabled={!canSubmit}
          >
            <div className="jp-Dialog-buttonLabel">
              {busy ? 'Working…' : submitLabel}
            </div>
          </button>
        </div>
      </form>
    </div>
  );
}

function UndoToast(props: {
  message: string;
  onUndo: () => void;
  onDismiss: () => void;
}): JSX.Element {
  return (
    <div className="nbi-undo-toast" role="status">
      <span className="nbi-undo-toast-message">{props.message}</span>
      <button
        type="button"
        className="nbi-undo-toast-action"
        onClick={props.onUndo}
      >
        Undo
      </button>
      <button
        type="button"
        className="nbi-undo-toast-close"
        aria-label="Dismiss"
        onClick={props.onDismiss}
      >
        ×
      </button>
    </div>
  );
}

interface IFileBuffer {
  content: string;
  saved: string;
  loaded: boolean;
}

function SkillEditor(props: {
  scope: SkillScope;
  name: string | null;
  onClose: () => void;
}): JSX.Element {
  const isNew = props.name === null;
  const [scope, setScope] = useState<SkillScope>(props.scope);
  const [name, setName] = useState(props.name ?? '');
  const [description, setDescription] = useState('');
  const [allowedTools, setAllowedTools] = useState<string[]>([]);
  const [savedMeta, setSavedMeta] = useState({
    description: '',
    allowedTools: [] as string[]
  });
  // Bundle files are lazy-loaded when the user switches to their tab (see the fetch effect
  // below). `loaded: false` means we know the file exists on disk but haven't fetched its
  // content yet. SKILL.md is always loaded eagerly in loadSkill().
  const [buffers, setBuffers] = useState<Map<string, IFileBuffer>>(
    new Map([[SKILL_ENTRY_FILE, { content: '', saved: '', loaded: true }]])
  );
  const orderedFileList = useMemo(
    () => [
      SKILL_ENTRY_FILE,
      ...Array.from(buffers.keys())
        .filter(f => f !== SKILL_ENTRY_FILE)
        .sort()
    ],
    [buffers]
  );
  const [activeFile, setActiveFile] = useState<string>(SKILL_ENTRY_FILE);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [addFileDraft, setAddFileDraft] = useState('');
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasCreated, setHasCreated] = useState(false);
  const [managed, setManaged] = useState(false);
  const [managedSource, setManagedSource] = useState('');
  const errorRef = useRef<HTMLDivElement>(null);

  const effectiveName = isNew && !hasCreated ? name : (props.name ?? name);
  const effectiveIsNew = isNew && !hasCreated;

  const loadSkill = async (s: SkillScope, n: string) => {
    const skill: ISkillDetail = await NBIAPI.readSkill(s, n);
    setDescription(skill.description);
    setAllowedTools(skill.allowedTools ?? []);
    setManaged(skill.managed);
    setManagedSource(skill.managedSource ?? '');
    const skillMdBody = skill.body ?? '';
    setSavedMeta({
      description: skill.description,
      allowedTools: skill.allowedTools ?? []
    });
    const newBuffers = new Map<string, IFileBuffer>();
    newBuffers.set(SKILL_ENTRY_FILE, {
      content: skillMdBody,
      saved: skillMdBody,
      loaded: true
    });
    for (const file of skill.files ?? []) {
      if (file !== SKILL_ENTRY_FILE) {
        newBuffers.set(file, { content: '', saved: '', loaded: false });
      }
    }
    setBuffers(newBuffers);
    setActiveFile(SKILL_ENTRY_FILE);
  };

  useEffect(() => {
    if (isNew) {
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await loadSkill(props.scope, props.name as string);
      } catch (e: any) {
        if (!cancelled) {
          setError(e?.message ?? String(e));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isNew, props.scope, props.name]);

  useEffect(() => {
    if (isNew && !hasCreated) {
      return;
    }
    const onReload = () => {
      const skillName = props.name ?? (hasCreated ? name : null);
      if (!skillName) {
        return;
      }
      loadSkill(props.scope, skillName).catch((e: any) => {
        setError(e?.message ?? String(e));
      });
    };
    NBIAPI.skillsReloaded.connect(onReload);
    return () => {
      NBIAPI.skillsReloaded.disconnect(onReload);
    };
  }, [isNew, hasCreated, props.scope, props.name, name]);

  useEffect(() => {
    if (error) {
      errorRef.current?.scrollIntoView({ block: 'nearest' });
    }
  }, [error]);

  const metaDirty =
    description !== savedMeta.description ||
    allowedTools.length !== savedMeta.allowedTools.length ||
    allowedTools.some((t, i) => t !== savedMeta.allowedTools[i]);
  const fileDirty = (path: string): boolean => {
    const b = buffers.get(path);
    return b !== undefined && b.content !== b.saved;
  };
  const anyFileDirty = Array.from(buffers.keys()).some(fileDirty);
  const anyDirty = metaDirty || anyFileDirty;

  const nameValid = SKILL_NAME_PATTERN.test(name);
  const descriptionValid = description.trim().length > 0;
  const canSave =
    !saving && !managed && (!effectiveIsNew || nameValid) && descriptionValid;

  const updateBuffer = (path: string, content: string) => {
    setBuffers(prev => {
      const next = new Map(prev);
      const existing = next.get(path) ?? {
        content: '',
        saved: '',
        loaded: true
      };
      next.set(path, { ...existing, content });
      return next;
    });
  };

  const currentBuffer = buffers.get(activeFile)?.content ?? '';

  const handleSave = async () => {
    setError(null);
    if (effectiveIsNew && !nameValid) {
      setError(`Invalid name. ${SKILL_NAME_REQUIREMENT}`);
      return;
    }
    setSaving(true);
    try {
      if (effectiveIsNew) {
        const initialBody = buffers.get(SKILL_ENTRY_FILE)?.content ?? '';
        await NBIAPI.createSkill({
          scope,
          name,
          description,
          allowedTools,
          body: initialBody
        });
        const extraFiles = Array.from(buffers.entries()).filter(
          ([p]) => p !== SKILL_ENTRY_FILE
        );
        for (const [path, buf] of extraFiles) {
          try {
            await NBIAPI.writeBundleFile(scope, name, path, buf.content);
          } catch (e: any) {
            setError(`${path}: ${e?.message ?? String(e)}`);
          }
        }
        setHasCreated(true);
        await loadSkill(scope, name);
      } else {
        const skillName = effectiveName;
        const dirtyFilePaths = Array.from(buffers.keys()).filter(
          p => p !== SKILL_ENTRY_FILE && fileDirty(p)
        );
        const skillMdBody = buffers.get(SKILL_ENTRY_FILE)?.content ?? '';
        const skillMdDirty = fileDirty(SKILL_ENTRY_FILE);
        const savedPaths = new Set<string>();
        const fileResults = await Promise.allSettled(
          dirtyFilePaths.map(p =>
            NBIAPI.writeBundleFile(scope, skillName, p, buffers.get(p)!.content)
          )
        );
        const errors: string[] = [];
        fileResults.forEach((result, i) => {
          const p = dirtyFilePaths[i];
          if (result.status === 'fulfilled') {
            savedPaths.add(p);
          } else {
            errors.push(`${p}: ${result.reason?.message ?? result.reason}`);
          }
        });
        let metaSaved = !(metaDirty || skillMdDirty);
        if (metaDirty || skillMdDirty) {
          try {
            await NBIAPI.updateSkill(scope, skillName, {
              description,
              allowedTools,
              body: skillMdBody
            });
            metaSaved = true;
            if (skillMdDirty) {
              savedPaths.add(SKILL_ENTRY_FILE);
            }
          } catch (e: any) {
            errors.push(e?.message ?? String(e));
          }
        }
        setBuffers(prev => {
          const next = new Map(prev);
          for (const path of savedPaths) {
            const b = next.get(path);
            if (b) {
              next.set(path, {
                content: b.content,
                saved: b.content,
                loaded: true
              });
            }
          }
          return next;
        });
        if (metaSaved) {
          setSavedMeta({ description, allowedTools });
        }
        if (errors.length > 0) {
          setError(errors.join('\n'));
        }
      }
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleBack = async () => {
    if (anyDirty) {
      const result = await showDialog({
        title: 'Discard unsaved changes?',
        body: 'This skill has unsaved changes. Discard them?',
        buttons: [
          Dialog.cancelButton({ label: 'Keep editing' }),
          Dialog.warnButton({ label: 'Discard' })
        ]
      });
      if (!result.button.accept) {
        return;
      }
    }
    props.onClose();
  };

  const handleSelectFile = (path: string) => {
    if (path === activeFile) {
      return;
    }
    setActiveFile(path);
  };

  const handleAddFile = async () => {
    const relPath = addFileDraft.trim();
    if (!relPath) {
      return;
    }
    if (buffers.has(relPath)) {
      setError(`"${relPath}" already exists in this bundle.`);
      return;
    }
    try {
      if (!effectiveIsNew) {
        await NBIAPI.writeBundleFile(scope, effectiveName, relPath, '');
      }
      setBuffers(prev => {
        const next = new Map(prev);
        // For new skills, mark as dirty (saved='' vs content='' — equal, so not dirty;
        // but the initial empty file write happens on skill creation save).
        next.set(relPath, { content: '', saved: '', loaded: true });
        return next;
      });
      setActiveFile(relPath);
      setAddFileDraft('');
    } catch (e: any) {
      setError(e?.message ?? String(e));
    }
  };

  const handleDeleteFile = async (path: string) => {
    const result = await showDialog({
      title: 'Delete file?',
      body: `"${path}" will be permanently deleted.`,
      buttons: [Dialog.cancelButton(), Dialog.warnButton({ label: 'Delete' })]
    });
    if (!result.button.accept) {
      return;
    }
    try {
      if (!effectiveIsNew) {
        await NBIAPI.deleteBundleFile(scope, effectiveName, path);
      }
      setBuffers(prev => {
        const next = new Map(prev);
        next.delete(path);
        return next;
      });
      if (activeFile === path) {
        setActiveFile(SKILL_ENTRY_FILE);
      }
    } catch (e: any) {
      setError(e?.message ?? String(e));
    }
  };

  const handleBeginRename = (path: string) => {
    setRenaming(path);
    setRenameDraft(path);
  };

  const handleCommitRename = async () => {
    if (renaming === null) {
      return;
    }
    const newPath = renameDraft.trim();
    if (!newPath || newPath === renaming) {
      setRenaming(null);
      return;
    }
    try {
      if (!effectiveIsNew) {
        await NBIAPI.renameBundleFile(scope, effectiveName, renaming, newPath);
      }
      setBuffers(prev => {
        const next = new Map(prev);
        const b = next.get(renaming!);
        if (b) {
          next.set(newPath, b);
          next.delete(renaming!);
        }
        return next;
      });
      if (activeFile === renaming) {
        setActiveFile(newPath);
      }
      setRenaming(null);
      setRenameDraft('');
    } catch (e: any) {
      setError(e?.message ?? String(e));
      setRenaming(null);
    }
  };

  useEffect(() => {
    if (effectiveIsNew || activeFile === SKILL_ENTRY_FILE) {
      return;
    }
    const existing = buffers.get(activeFile);
    if (existing?.loaded) {
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const content = await NBIAPI.readBundleFile(
          scope,
          effectiveName,
          activeFile
        );
        if (cancelled) {
          return;
        }
        setBuffers(prev => {
          const existing = prev.get(activeFile);
          // Don't clobber user edits that happened while the fetch was in flight.
          if (existing?.loaded) {
            return prev;
          }
          const next = new Map(prev);
          next.set(activeFile, { content, saved: content, loaded: true });
          return next;
        });
      } catch (e: any) {
        if (!cancelled) {
          setError(e?.message ?? String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeFile, effectiveIsNew, scope, effectiveName]);

  const nameError =
    effectiveIsNew && name && !nameValid ? SKILL_NAME_REQUIREMENT : null;
  const descriptionError =
    !descriptionValid && (description.length > 0 || !effectiveIsNew)
      ? 'Description is required.'
      : null;

  const editingSkillMd = activeFile === SKILL_ENTRY_FILE;
  const bodyLanguageHint = editingSkillMd ? 'markdown' : activeFile;

  return (
    <form
      className="nbi-skill-editor"
      onSubmit={e => {
        e.preventDefault();
        if (canSave && (effectiveIsNew || anyDirty)) {
          handleSave();
        }
      }}
    >
      <div className="nbi-skill-editor-header">
        <nav className="nbi-skill-editor-breadcrumb" aria-label="Breadcrumb">
          <button
            type="button"
            className="nbi-breadcrumb-link"
            onClick={handleBack}
          >
            Skills
          </button>
          <span className="nbi-breadcrumb-separator" aria-hidden="true">
            /
          </span>
          <span className="nbi-breadcrumb-current">
            {effectiveIsNew ? 'New skill' : effectiveName}
            {managed && <ManagedBadge source={managedSource} />}
          </span>
        </nav>
        <div className="nbi-skill-editor-actions">
          <button
            type="button"
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={handleBack}
          >
            <div className="jp-Dialog-buttonLabel">
              {managed ? 'Close' : 'Cancel'}
            </div>
          </button>
          {!managed && (
            <button
              type="submit"
              className="jp-Dialog-button jp-mod-accept jp-mod-styled"
              disabled={!canSave || (!effectiveIsNew && !anyDirty)}
            >
              <div className="jp-Dialog-buttonLabel">
                {saving ? 'Saving…' : 'Save'}
                {anyDirty && (
                  <span
                    className="nbi-dirty-marker"
                    aria-label="Unsaved changes"
                  >
                    {' '}
                    •
                  </span>
                )}
              </div>
            </button>
          )}
        </div>
      </div>
      {managed && (
        <div className="nbi-skills-managed-banner" role="note">
          This skill is managed by the organization manifest and is read-only.
          Changes will be overwritten on the next sync.
        </div>
      )}

      {loading ? (
        <div className="nbi-skill-editor-loading">Loading skill…</div>
      ) : (
        <>
          {error && (
            <div className="nbi-skills-error" role="alert" ref={errorRef}>
              {error}
            </div>
          )}

          <div className="nbi-skill-editor-meta">
            <div className="nbi-form-row">
              <div className="nbi-form-row-inline">
                <div className="nbi-form-field">
                  <label>Scope</label>
                  <select
                    value={scope}
                    disabled={!effectiveIsNew}
                    onChange={e => setScope(e.target.value as SkillScope)}
                  >
                    <option value="user">User</option>
                    <option value="project">Project</option>
                  </select>
                </div>
                <div className="nbi-form-field">
                  <label>Name</label>
                  <input
                    type="text"
                    value={name}
                    disabled={!effectiveIsNew}
                    onChange={e => setName(e.target.value)}
                    placeholder="my-skill-name"
                    aria-invalid={nameError ? true : undefined}
                  />
                  {nameError && (
                    <div className="nbi-form-field-error">{nameError}</div>
                  )}
                </div>
              </div>
              {!effectiveIsNew && (
                <div className="nbi-form-hint">
                  Scope and name are set at creation and can't be changed.
                  Delete and recreate to change them.
                </div>
              )}
            </div>

            <div className="nbi-form-field nbi-form-field-wide">
              <label>Description</label>
              <textarea
                rows={3}
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="What this skill does, shown to Claude when deciding whether to use it"
                aria-invalid={descriptionError ? true : undefined}
                required
              />
              {descriptionError ? (
                <div className="nbi-form-field-error">{descriptionError}</div>
              ) : (
                <div className="nbi-form-hint">
                  Required. Claude uses this to decide when to apply the skill.
                </div>
              )}
            </div>

            <div className="nbi-form-field nbi-form-field-wide">
              <label>Allowed tools</label>
              <AllowedToolsPicker
                value={allowedTools}
                onChange={setAllowedTools}
              />
              <div className="nbi-form-hint">
                Quick-add pills cover common tools. Type patterns like{' '}
                <code>Bash(git:*)</code> or <code>Read(./docs/**)</code> for
                finer-grained permissions.
              </div>
            </div>
          </div>

          <BundleFileTabs
            files={orderedFileList}
            activeFile={activeFile}
            renaming={renaming}
            renameDraft={renameDraft}
            addFileDraft={addFileDraft}
            fileDirty={fileDirty}
            onSelect={handleSelectFile}
            onBeginRename={handleBeginRename}
            onCommitRename={handleCommitRename}
            onCancelRename={() => setRenaming(null)}
            onRenameDraftChange={setRenameDraft}
            onDelete={handleDeleteFile}
            onAddFileDraftChange={setAddFileDraft}
            onAddFile={handleAddFile}
          />
          <div className="nbi-skill-editor-body">
            <div className="nbi-skill-editor-pane">
              <AutoGrowTextarea
                value={currentBuffer}
                onChange={v => updateBuffer(activeFile, v)}
                minRows={18}
                languageHint={bodyLanguageHint}
              />
            </div>
          </div>
        </>
      )}
    </form>
  );
}

function BundleFileTabs(props: {
  files: string[];
  activeFile: string;
  renaming: string | null;
  renameDraft: string;
  addFileDraft: string;
  fileDirty: (path: string) => boolean;
  onSelect: (path: string) => void;
  onBeginRename: (path: string) => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onRenameDraftChange: (v: string) => void;
  onDelete: (path: string) => void;
  onAddFileDraftChange: (v: string) => void;
  onAddFile: () => void;
}): JSX.Element {
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    if (!openMenu) {
      return;
    }
    const onDocClick = () => setOpenMenu(null);
    document.addEventListener('click', onDocClick);
    return () => document.removeEventListener('click', onDocClick);
  }, [openMenu]);

  const commitAdd = () => {
    if (props.addFileDraft.trim()) {
      props.onAddFile();
    }
    setAdding(false);
  };

  return (
    <div className="nbi-skill-editor-tabs" role="tablist">
      {props.files.map(file => {
        const active = file === props.activeFile;
        const dirty = props.fileDirty(file);
        const isRenaming = props.renaming === file;
        const canModify = file !== SKILL_ENTRY_FILE;
        return (
          <div
            key={file}
            role="tab"
            aria-selected={active}
            className={`nbi-skill-editor-tab${active ? ' active' : ''}`}
            onClick={() => !isRenaming && props.onSelect(file)}
          >
            {isRenaming ? (
              <input
                type="text"
                autoFocus
                value={props.renameDraft}
                onChange={e => props.onRenameDraftChange(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    props.onCommitRename();
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    props.onCancelRename();
                  }
                }}
                onBlur={props.onCancelRename}
                onClick={e => e.stopPropagation()}
                className="nbi-skill-editor-tab-rename-input"
              />
            ) : (
              <>
                <span className="nbi-skill-editor-tab-label">
                  {file}
                  {dirty && <span className="nbi-dirty-marker"> •</span>}
                </span>
                {canModify && (
                  <div
                    className="nbi-skill-editor-tab-kebab-wrap"
                    onClick={e => e.stopPropagation()}
                  >
                    <button
                      type="button"
                      className="nbi-icon-button"
                      aria-label={`Actions for ${file}`}
                      aria-haspopup="menu"
                      aria-expanded={openMenu === file}
                      onClick={e => {
                        e.stopPropagation();
                        setOpenMenu(openMenu === file ? null : file);
                      }}
                    >
                      ⋯
                    </button>
                    {openMenu === file && (
                      <div className="nbi-skill-editor-tab-menu" role="menu">
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setOpenMenu(null);
                            props.onBeginRename(file);
                          }}
                        >
                          Rename
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          className="danger"
                          onClick={() => {
                            setOpenMenu(null);
                            props.onDelete(file);
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        );
      })}
      {adding ? (
        <div className="nbi-skill-editor-tab adding">
          <input
            type="text"
            autoFocus
            placeholder="new-file.md"
            value={props.addFileDraft}
            onChange={e => props.onAddFileDraftChange(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitAdd();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                props.onAddFileDraftChange('');
                setAdding(false);
              }
            }}
            onBlur={commitAdd}
            className="nbi-skill-editor-tab-rename-input"
          />
        </div>
      ) : (
        <button
          type="button"
          className="nbi-skill-editor-tab-add"
          aria-label="Add file"
          title="Add file"
          onClick={() => setAdding(true)}
        >
          +
        </button>
      )}
    </div>
  );
}

function AllowedToolsPicker(props: {
  value: string[];
  onChange: (next: string[]) => void;
}): JSX.Element {
  const [draft, setDraft] = useState('');

  const commit = (raw: string) => {
    const parts = raw
      .split(',')
      .map(t => t.trim())
      .filter(t => t.length > 0 && !props.value.includes(t));
    if (parts.length === 0) {
      setDraft('');
      return;
    }
    props.onChange([...props.value, ...parts]);
    setDraft('');
  };

  const toggle = (tool: string) => {
    if (props.value.includes(tool)) {
      props.onChange(props.value.filter(t => t !== tool));
    } else {
      props.onChange([...props.value, tool]);
    }
  };

  const remove = (tool: string) => {
    props.onChange(props.value.filter(t => t !== tool));
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      if (draft.trim()) {
        e.preventDefault();
        commit(draft);
      }
    } else if (e.key === 'Backspace' && !draft && props.value.length > 0) {
      remove(props.value[props.value.length - 1]);
    }
  };

  return (
    <div>
      <div className="nbi-tools-picker-input">
        {props.value.map(tool => (
          <span key={tool} className="pill-item checked">
            {tool}
            <button
              onClick={() => remove(tool)}
              aria-label={`Remove ${tool}`}
              className="nbi-pill-remove"
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => draft.trim() && commit(draft)}
          placeholder={props.value.length === 0 ? 'e.g. Bash(git:*)' : ''}
        />
      </div>
      <div className="nbi-tools-picker-suggestions">
        {COMMON_TOOLS.map(tool => (
          <span
            key={tool}
            className={`pill-item${props.value.includes(tool) ? ' checked' : ''}`}
            onClick={() => toggle(tool)}
          >
            {tool}
          </span>
        ))}
      </div>
    </div>
  );
}

function AutoGrowTextarea(props: {
  value: string;
  onChange: (v: string) => void;
  minRows: number;
  languageHint?: string;
}): JSX.Element {
  const ref = useRef<HTMLTextAreaElement>(null);
  const lineHeightRef = useRef<number | null>(null);

  useLayoutEffect(() => {
    const ta = ref.current;
    if (!ta) {
      return;
    }
    if (lineHeightRef.current === null) {
      const computed = window.getComputedStyle(ta);
      const parsed = parseFloat(computed.lineHeight);
      // CSS "normal" resolves to NaN here; 1.4× font-size is a standard approximation.
      lineHeightRef.current = Number.isNaN(parsed)
        ? parseFloat(computed.fontSize) * 1.4
        : parsed;
    }
    ta.style.height = 'auto';
    const next = Math.max(
      ta.scrollHeight,
      props.minRows * lineHeightRef.current
    );
    ta.style.height = `${next}px`;
  }, [props.value, props.minRows]);

  return (
    <textarea
      ref={ref}
      className="nbi-skill-editor-textarea"
      value={props.value}
      onChange={e => props.onChange(e.target.value)}
      spellCheck={false}
      data-language={props.languageHint}
    />
  );
}

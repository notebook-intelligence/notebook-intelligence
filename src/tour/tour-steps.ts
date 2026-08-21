// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * Step definitions for the first-run tour.
 *
 * The copy (titles, descriptions, button labels, command palette
 * label) lives in `tour-defaults.json`, which uses the same schema as
 * an admin override file (see `docs/admin-tour-config.md`). The
 * structural wiring (DOM anchor ids, placements, capability gates)
 * lives in TypeScript because it isn't user-facing copy.
 *
 * Each step anchors to a DOM element by `data-tour-id` (added on the
 * matching React element). Using a dedicated attribute rather than a
 * CSS class makes the contract explicit and decouples the tour from
 * styling changes.
 *
 * The `requires` predicate filters out steps that don't apply to the
 * current deployment (e.g. the Claude history step is skipped when
 * Claude CLI is not installed). Predicates close over the snapshot of
 * capabilities at tour start, so a CLI being installed mid-tour does
 * not retroactively insert a step.
 */

import { NBIAPI } from '../api';
import {
  applyTourOverrides,
  ITourOverrides,
  launcherTileTemplates
} from './tour-config';
import { TOUR_ANCHOR } from './tour-anchors';
import defaultsJson from './tour-defaults.json';

export type TourPlacement = 'top' | 'bottom' | 'left' | 'right' | 'center';

export interface ITourStep {
  id: string;
  title: string;
  // Description can be a string OR a thunk that's evaluated when the
  // step list is built. The thunk form lets a step adapt its copy to
  // the deployment (e.g. the launcher-tiles step lists only the CLIs
  // that are actually installed on this machine).
  description: string | (() => string);
  // Element selector: tour anchors to `[data-tour-id="<anchorId>"]`.
  // null means a centered modal step (welcome / completion).
  anchorId: string | null;
  placement: TourPlacement;
  // Optional predicate. The tour fires this once when computing the
  // step list; a return of false skips the step entirely. Keep
  // predicates pure and cheap.
  requires?: () => boolean;
}

interface IDefaultsSchema {
  command?: { label?: string };
  ui?: { skip?: string; next?: string; back?: string; done?: string };
  steps?: Record<
    string,
    {
      title?: string;
      description?: string;
      description_singular?: string;
      description_plural?: string;
    }
  >;
}

// Bundled defaults file. Same schema as an admin override; the
// override applier overlays the admin layer on top of these.
export const TOUR_DEFAULTS: IDefaultsSchema = defaultsJson as IDefaultsSchema;

function defaultText(stepId: string, key: 'title' | 'description'): string {
  const step = TOUR_DEFAULTS.steps?.[stepId];
  const value = step?.[key];
  return typeof value === 'string' ? value : '';
}

// Map every Coding Agent launcher id to (a) the capability flag that
// decides whether the tile shows up at all and (b) the display name
// the tour should use. Order matches what users see in the JL
// Launcher.
const CODING_AGENT_LAUNCHERS: ReadonlyArray<{
  id: string;
  label: string;
  available: () => boolean;
}> = [
  {
    id: 'claude-code',
    label: 'Claude Code',
    available: () => NBIAPI.config.isClaudeCliAvailable
  },
  {
    id: 'codex',
    label: 'Codex',
    available: () => NBIAPI.config.isCodexCliAvailable
  },
  {
    id: 'opencode',
    label: 'opencode',
    available: () => NBIAPI.config.isOpenCodeCliAvailable
  },
  {
    id: 'pi',
    label: 'Pi',
    available: () => NBIAPI.config.isPiCliAvailable
  },
  {
    id: 'github-copilot-cli',
    label: 'GitHub Copilot CLI',
    available: () => NBIAPI.config.isGitHubCopilotCliAvailable
  }
];

function visibleCodingAgentLaunchers(): string[] {
  return CODING_AGENT_LAUNCHERS.filter(
    l =>
      l.available() &&
      !NBIAPI.config.isCodingAgentLauncherDisabledByPolicy(l.id)
  ).map(l => l.label);
}

function formatLauncherList(names: string[]): string {
  if (names.length === 0) {
    return '';
  }
  if (names.length === 1) {
    return names[0];
  }
  if (names.length === 2) {
    return `${names[0]} and ${names[1]}`;
  }
  return `${names.slice(0, -1).join(', ')}, and ${names[names.length - 1]}`;
}

function launcherTilesDescription(): string {
  const names = visibleCodingAgentLaunchers();
  const list = formatLauncherList(names);
  // Admin overrides win; defaults supply the fallback templates.
  const overrideTemplates = launcherTileTemplates(
    NBIAPI.config.tourOverrides as ITourOverrides
  );
  const defaultStep = TOUR_DEFAULTS.steps?.['launcher-tiles'];
  const template =
    names.length === 1
      ? (overrideTemplates.singular ?? defaultStep?.description_singular)
      : (overrideTemplates.plural ?? defaultStep?.description_plural);
  return template ? template.replace(/\{launchers\}/g, list) : '';
}

export const ALL_TOUR_STEPS: readonly ITourStep[] = Object.freeze([
  {
    id: 'welcome',
    title: defaultText('welcome', 'title'),
    description: defaultText('welcome', 'description'),
    anchorId: null,
    placement: 'center'
  },
  {
    id: 'new-chat',
    title: defaultText('new-chat', 'title'),
    description: defaultText('new-chat', 'description'),
    anchorId: TOUR_ANCHOR.newChat,
    placement: 'bottom',
    // The + button is only rendered in Claude mode (it restarts the
    // Claude client). Skip when not in Claude mode so the tour doesn't
    // describe a missing affordance.
    requires: () => NBIAPI.config.isInClaudeCodeMode
  },
  {
    id: 'claude-history',
    title: defaultText('claude-history', 'title'),
    description: defaultText('claude-history', 'description'),
    anchorId: TOUR_ANCHOR.claudeHistory,
    placement: 'bottom',
    // Visible only when Claude CLI is available AND Claude mode is on.
    // Without the second guard the anchor isn't even rendered, so the
    // tour would silently skip; the `requires` keeps the skip
    // explicit.
    requires: () =>
      NBIAPI.config.isClaudeCliAvailable && NBIAPI.config.isInClaudeCodeMode
  },
  {
    id: 'settings-gear',
    title: defaultText('settings-gear', 'title'),
    description: defaultText('settings-gear', 'description'),
    anchorId: TOUR_ANCHOR.settingsGear,
    placement: 'bottom'
  },
  {
    id: 'slash-commands',
    title: defaultText('slash-commands', 'title'),
    description: defaultText('slash-commands', 'description'),
    anchorId: TOUR_ANCHOR.slashCommands,
    placement: 'top'
  },
  {
    id: 'add-context',
    title: defaultText('add-context', 'title'),
    description: defaultText('add-context', 'description'),
    anchorId: TOUR_ANCHOR.addContext,
    placement: 'top'
  },
  {
    id: 'upload-file',
    title: defaultText('upload-file', 'title'),
    description: defaultText('upload-file', 'description'),
    anchorId: TOUR_ANCHOR.uploadFile,
    placement: 'top'
  },
  {
    id: 'drag-and-drop',
    title: defaultText('drag-and-drop', 'title'),
    description: defaultText('drag-and-drop', 'description'),
    anchorId: TOUR_ANCHOR.promptInput,
    placement: 'top'
  },
  {
    id: 'chat-mode',
    title: defaultText('chat-mode', 'title'),
    description: defaultText('chat-mode', 'description'),
    anchorId: TOUR_ANCHOR.chatMode,
    placement: 'top',
    // Mode picker is hidden in Claude and ACP modes (the agent owns its
    // own loop), so skip the step there rather than target a missing anchor.
    requires: () =>
      !NBIAPI.config.isInClaudeCodeMode && !NBIAPI.config.isInAcpMode
  },
  {
    id: 'launcher-tiles',
    title: defaultText('launcher-tiles', 'title'),
    // Dynamic description so the step only mentions the CLI tools the
    // user actually has installed. Skipped entirely when zero are
    // available (see the requires predicate below). Both the default
    // templates and admin overrides flow through this thunk so the
    // {launchers} placeholder gets substituted with the same
    // comma-joined list either way.
    description: launcherTilesDescription,
    anchorId: null,
    placement: 'center',
    // Skip the step on machines where no coding-agent CLI is installed
    // (or admin policy hides every tile) so the tour doesn't promise an
    // affordance the user can't reach.
    requires: () => visibleCodingAgentLaunchers().length > 0
  },
  {
    id: 'done',
    title: defaultText('done', 'title'),
    description: defaultText('done', 'description'),
    anchorId: null,
    placement: 'center'
  }
]);

// What the overlay actually consumes: every thunk has already been
// resolved to a plain string. Exposing this separately keeps the
// overlay's render code type-safe without the thunk variant.
export type IResolvedTourStep = Omit<ITourStep, 'description'> & {
  description: string;
};

export function activeTourSteps(): IResolvedTourStep[] {
  // Pipeline:
  //   1. Apply admin overrides (title/description rewrites, per-step
  //      enabled:false drops). The default-overrides path returns a
  //      shallow copy so the rest of the pipeline doesn't mutate the
  //      ALL_TOUR_STEPS module-level constant.
  //   2. Filter on the per-step `requires()` predicate (capability /
  //      mode gates).
  //   3. Resolve any thunk descriptions to plain strings so the
  //      overlay can render them directly. Doing this here (rather
  //      than at render time) matches the "snapshot on mount"
  //      semantics: a capability change mid-tour doesn't reshape step
  //      copy under the user.
  const overrides = NBIAPI.config.tourOverrides as ITourOverrides;
  return applyTourOverrides(ALL_TOUR_STEPS, overrides)
    .filter(step => (step.requires ? step.requires() : true))
    .map(step => ({
      ...step,
      description:
        typeof step.description === 'function'
          ? step.description()
          : step.description
    }));
}

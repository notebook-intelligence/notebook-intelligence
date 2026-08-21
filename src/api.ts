// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { ServerConnection } from '@jupyterlab/services';
import { requestAPI } from './handler';
import { URLExt } from '@jupyterlab/coreutils';
import { Signal } from '@lumino/signaling';
import {
  GITHUB_COPILOT_PROVIDER_ID,
  IChatCompletionResponseEmitter,
  IChatParticipant,
  IContextItem,
  ITelemetryEvent,
  IToolSelections,
  RequestDataType,
  BackendMessageType,
  AssistantMode
} from './tokens';

export enum GitHubCopilotLoginStatus {
  NotLoggedIn = 'NOT_LOGGED_IN',
  ActivatingDevice = 'ACTIVATING_DEVICE',
  LoggingIn = 'LOGGING_IN',
  LoggedIn = 'LOGGED_IN'
}

export interface IDeviceVerificationInfo {
  verificationURI: string;
  userCode: string;
}

export enum ClaudeModelType {
  None = 'none',
  Inherit = 'inherit',
  Default = ''
}

export interface IClaudeModelInfo {
  id: string;
  name: string;
  contextWindow: number;
}

export interface IClaudeSessionInfo {
  session_id: string;
  path: string;
  modified_at: number;
  created_at: number;
  preview: string;
  cwd: string;
}

export interface IClaudeSessionList {
  sessions: IClaudeSessionInfo[];
  // The realpath-resolved JupyterLab working directory. `claude --resume
  // <id>` is cwd-scoped, so the frontend pairs this with the session id to
  // produce a copyable shell command.
  currentCwd: string;
}

export type ClaudeSessionScope = 'cwd' | 'all';

export enum ClaudeToolType {
  ClaudeCodeTools = 'claude-code:built-in-tools',
  JupyterUITools = 'nbi:built-in-jupyter-ui-tools'
}

export type SkillScope = 'user' | 'project';

export type ClaudeMCPScope = 'user' | 'project' | 'local';
export type ClaudeMCPTransport = 'stdio' | 'sse' | 'http';

export type PluginScope = 'user' | 'project' | 'local';

// Claude's `claude plugin list --json` output schema isn't formally
// documented; we forward the raw object and let the panel fish out fields
// it cares about. Defining only the fields we observe today as optional
// keeps newer Claude releases from getting truncated.
export interface IPluginInfo {
  id?: string;
  name?: string;
  scope?: PluginScope | string;
  enabled?: boolean;
  marketplace?: string;
  version?: string;
  description?: string;
  [key: string]: unknown;
}

export interface IPluginMarketplaceInfo {
  name?: string;
  source?: string;
  scope?: PluginScope | string;
  description?: string;
  version?: string;
  plugin_count?: number;
  plugin_names?: string[];
  [key: string]: unknown;
}

export interface IPluginMarketplacePluginInfo extends IPluginInfo {
  source?: unknown;
  category?: string;
  tags?: string[];
}

export interface IClaudeMCPServer {
  name: string;
  scope: ClaudeMCPScope;
  transport: ClaudeMCPTransport | string;
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  headers: Record<string, string>;
  disabledForWorkspace: boolean;
}

export interface IClaudeMCPAddInput {
  name: string;
  scope: ClaudeMCPScope;
  transport: ClaudeMCPTransport;
  commandOrUrl: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
}

function claudeMCPServerFromWire(wire: any): IClaudeMCPServer {
  return {
    name: String(wire?.name ?? ''),
    scope: (wire?.scope ?? 'user') as ClaudeMCPScope,
    transport: String(wire?.transport ?? 'stdio'),
    command: String(wire?.command ?? ''),
    args: Array.isArray(wire?.args) ? wire.args.map(String) : [],
    env:
      wire?.env && typeof wire.env === 'object'
        ? Object.fromEntries(
            Object.entries(wire.env).map(([k, v]) => [String(k), String(v)])
          )
        : {},
    url: String(wire?.url ?? ''),
    headers:
      wire?.headers && typeof wire.headers === 'object'
        ? Object.fromEntries(
            Object.entries(wire.headers).map(([k, v]) => [String(k), String(v)])
          )
        : {},
    disabledForWorkspace: Boolean(wire?.disabled_for_workspace)
  };
}

export interface ISkillSummary {
  scope: SkillScope;
  name: string;
  description: string;
  allowedTools: string[];
  rootPath: string;
  files: string[];
  source: string;
  managed: boolean;
  managedSource: string;
  managedRef: string;
  // User-imported GitHub skills that opted into auto-sync. Distinct
  // from `managed`: tracking skills are editable and never auto-removed,
  // only manually replaced when the user clicks Sync.
  tracksUpstream: boolean;
  trackingRef: string;
}

export interface IReconcileResult {
  added: number;
  updated: number;
  removed: number;
  unchanged: number;
  errors: string[];
}

export interface ISkillDetail extends ISkillSummary {
  body: string;
}

export interface ISkillsContext {
  projectRoot: string;
  projectName: string;
  userSkillsDir: string;
  projectSkillsDir: string;
}

export interface ISkillImportPreview {
  name: string;
  description: string;
  allowedTools: string[];
  body: string;
  files: string[];
  sourceUrl: string;
  canonicalUrl: string;
  existsInUserScope: boolean;
  existsInProjectScope: boolean;
}

// Exported for direct testing of the wire-format contract. The snake_case
// keys it consumes (managed_source, managed_ref, tracks_upstream,
// tracking_ref, allowed_tools) are the load-bearing JSON shape between
// the Tornado handlers and the panel; a typo here would silently corrupt
// user state ("I toggled it on but it didn't stick").
export function skillFromWire(wire: any): ISkillDetail {
  return {
    scope: wire.scope,
    name: wire.name,
    description: wire.description,
    allowedTools: wire.allowed_tools ?? [],
    rootPath: wire.root_path,
    files: wire.files ?? [],
    source: wire.source ?? '',
    managed: Boolean(wire.managed),
    managedSource: wire.managed_source ?? '',
    managedRef: wire.managed_ref ?? '',
    tracksUpstream: Boolean(wire.tracks_upstream),
    trackingRef: wire.tracking_ref ?? '',
    body: wire.body ?? ''
  };
}

export interface ISyncSkillResult {
  updated: boolean;
  ref: string;
}

export interface ISyncAllTrackingEntry {
  scope: SkillScope;
  name: string;
  updated?: boolean;
  ref?: string;
  error?: string;
}

function claudeModelFromWire(wire: any): IClaudeModelInfo {
  return {
    id: wire.id,
    name: wire.name,
    contextWindow: wire.context_window
  };
}

export interface ICellOutputFeatureFlag {
  enabled: boolean;
  locked: boolean;
}

export interface ICellOutputFeatures {
  explain_error: ICellOutputFeatureFlag;
  output_followup: ICellOutputFeatureFlag;
  output_toolbar: ICellOutputFeatureFlag;
}

// Per-action flags (the whole-toolbar gate `output_toolbar` is checked
// separately by callers).
export type CellOutputActionFlag = Exclude<
  keyof ICellOutputFeatures,
  'output_toolbar'
>;

// Boolean admin policies covering settings panel toggles. Mirrors
// FEATURE_POLICY_NAMES in extension.py — keep them in sync.
export type FeaturePolicyName =
  | 'explain_error'
  | 'output_followup'
  | 'output_toolbar'
  | 'claude_mode'
  | 'acp_mode'
  | 'acp_full_access'
  | 'claude_continue_conversation'
  | 'claude_code_tools'
  | 'claude_jupyter_ui_tools'
  | 'claude_setting_source_user'
  | 'claude_setting_source_project'
  | 'store_github_access_token'
  | 'skills_management'
  | 'claude_mcp_management'
  | 'claude_plugins_management'
  | 'claude_bypass_permissions'
  | 'terminal_drag_drop'
  | 'refresh_open_files_on_disk_change';

export type IFeaturePolicies = Record<
  FeaturePolicyName,
  ICellOutputFeatureFlag
>;

// Non-boolean settings whose value is locked when an admin sets the
// corresponding env var. The value itself is served via its existing
// capabilities field; this only carries the locked flag.
export type SettingLockName =
  | 'chat_model_provider'
  | 'chat_model_id'
  | 'inline_completion_model_provider'
  | 'inline_completion_model_id'
  | 'claude_chat_model'
  | 'claude_inline_completion_model'
  | 'claude_api_key'
  | 'claude_base_url'
  | 'acp_chat_model'
  | 'acp_api_key'
  | 'acp_base_url';

export type ISettingLocks = Record<SettingLockName, { locked: boolean }>;

// Shared frozen object returned by NBIConfig.tourOverrides when no
// admin overrides are present. Stable identity matters for downstream
// consumers (memoized command-palette label, useMemo deps).
const EMPTY_TOUR_OVERRIDES: Readonly<Record<string, any>> = Object.freeze({});

export class NBIConfig {
  get userHomeDir(): string {
    return this.capabilities.user_home_dir;
  }

  get userConfigDir(): string {
    return this.capabilities.nbi_user_config_dir;
  }

  get llmProviders(): [any] {
    return this.capabilities.llm_providers;
  }

  get chatModels(): [any] {
    return this.capabilities.chat_models;
  }

  get inlineCompletionModels(): [any] {
    return this.capabilities.inline_completion_models;
  }

  get defaultChatMode(): string {
    return this.capabilities.default_chat_mode;
  }

  get chatModel(): any {
    return this.capabilities.chat_model;
  }

  get chatModelSupportsVision(): boolean {
    return this.capabilities.chat_model_supports_vision === true;
  }

  get inlineCompletionModel(): any {
    return this.capabilities.inline_completion_model;
  }

  get usingGitHubCopilotModel(): boolean {
    return (
      this.chatModel.provider === GITHUB_COPILOT_PROVIDER_ID ||
      this.inlineCompletionModel.provider === GITHUB_COPILOT_PROVIDER_ID
    );
  }

  get storeGitHubAccessToken(): boolean {
    return this.capabilities.store_github_access_token === true;
  }

  get inlineCompletionDebouncerDelay(): number {
    return Number.isInteger(this.capabilities.inline_completion_debouncer_delay)
      ? this.capabilities.inline_completion_debouncer_delay
      : 200;
  }

  get toolConfig(): any {
    return this.capabilities.tool_config;
  }

  get mcpServers(): any {
    return this.toolConfig.mcpServers;
  }

  getMCPServer(serverId: string): any {
    return this.toolConfig.mcpServers.find(
      (server: any) => server.id === serverId
    );
  }

  getMCPServerPrompt(serverId: string, promptName: string): any {
    const server = this.getMCPServer(serverId);
    if (server) {
      return server.prompts.find((prompt: any) => prompt.name === promptName);
    }
    return null;
  }

  get mcpServerSettings(): any {
    return this.capabilities.mcp_server_settings;
  }

  get claudeSettings(): any {
    return this.capabilities.claude_settings;
  }

  get acpSettings(): any {
    return this.capabilities.acp_settings ?? {};
  }

  // The agent types selectable in ACP mode (settings dropdown).
  get acpAgents(): { id: string; label: string }[] {
    return this.capabilities.acp_agents ?? [];
  }

  // The single agent mode currently handling chat ('claude' | 'acp'), or
  // null when the native provider path is in use. Claude mode and ACP mode
  // are mutually exclusive.
  get activeAgentMode(): string | null {
    return this.capabilities.active_agent_mode ?? null;
  }

  get isInAcpMode(): boolean {
    return this.activeAgentMode === 'acp';
  }

  get spinnerVerbs(): { mode: string; verbs: string[] } | null {
    return this.capabilities.spinner_verbs ?? null;
  }

  get claudeModels(): IClaudeModelInfo[] {
    return (this.capabilities.claude_models ?? []).map(claudeModelFromWire);
  }

  get isInClaudeCodeMode(): boolean {
    return this.activeAgentMode === 'claude';
  }

  get isClaudeCliAvailable(): boolean {
    return this.capabilities.claude_cli_available === true;
  }

  get claudePermissionDefaultMode(): string {
    return this.capabilities.claude_permission_default_mode ?? 'default';
  }

  get isOpenCodeCliAvailable(): boolean {
    return this.capabilities.opencode_cli_available === true;
  }

  get isPiCliAvailable(): boolean {
    return this.capabilities.pi_cli_available === true;
  }

  get isGitHubCopilotCliAvailable(): boolean {
    return this.capabilities.github_copilot_cli_available === true;
  }

  get isCodexCliAvailable(): boolean {
    return this.capabilities.codex_cli_available === true;
  }

  isCodingAgentLauncherDisabledByPolicy(launcherId: string): boolean {
    // Fail closed when the field is missing or malformed: an admin denylist
    // must not silently disappear if capabilities haven't loaded yet or a
    // backend regression drops the field. The companion `is*CliAvailable`
    // getters already default to false until capabilities arrive, so on
    // first paint the tile is hidden regardless; this just ensures the
    // policy gate stays in effect even if a future change pre-seeds those
    // flags.
    const list = this.capabilities.disabled_coding_agent_launchers;
    if (Array.isArray(list)) {
      return list.includes(launcherId);
    }
    return true;
  }

  get chatFeedbackEnabled(): boolean {
    return this.capabilities.chat_feedback_enabled === true;
  }

  get chatFeedbackAlwaysVisible(): boolean {
    return this.capabilities.chat_feedback_always_visible === true;
  }

  // Admin-supplied tour-copy overrides, served from the capabilities
  // response after server-side validation. Returns the raw dict; the
  // tour module decides how to apply it. Defaults to a shared frozen
  // empty object so callers can spread/access keys without
  // null-checking AND the getter doesn't allocate a fresh `{}` on every
  // read (the JupyterLab command palette polls a command's label
  // thunk on every keystroke, so identity stability matters).
  get tourOverrides(): Record<string, any> {
    const v = this.capabilities.tour_overrides;
    return v && typeof v === 'object' ? v : EMPTY_TOUR_OVERRIDES;
  }

  get allowGithubSkillImport(): boolean {
    return this.capabilities.allow_github_skill_import !== false;
  }

  get additionalSkippedWorkspaceDirectories(): string[] {
    const v = this.capabilities.additional_skipped_workspace_directories;
    return Array.isArray(v) ? v : [];
  }

  get allowGithubPluginImport(): boolean {
    // Default-open: missing/undefined means the org hasn't gated this, so
    // older backends without the flag continue to allow the GitHub
    // affordance. Mirrors `cellOutputFeatures` polarity.
    return this.capabilities.allow_github_plugin_import !== false;
  }

  get cellOutputFeatures(): ICellOutputFeatures {
    const v = this.capabilities.cell_output_features ?? {};
    return {
      explain_error: {
        enabled: v.explain_error?.enabled !== false,
        locked: v.explain_error?.locked === true
      },
      output_followup: {
        enabled: v.output_followup?.enabled !== false,
        locked: v.output_followup?.locked === true
      },
      output_toolbar: {
        enabled: v.output_toolbar?.enabled !== false,
        locked: v.output_toolbar?.locked === true
      }
    };
  }

  get featurePolicies(): IFeaturePolicies {
    const v = this.capabilities.feature_policies ?? {};
    const names: FeaturePolicyName[] = [
      'explain_error',
      'output_followup',
      'output_toolbar',
      'claude_mode',
      'acp_mode',
      'acp_full_access',
      'claude_continue_conversation',
      'claude_code_tools',
      'claude_jupyter_ui_tools',
      'claude_setting_source_user',
      'claude_setting_source_project',
      'store_github_access_token',
      'skills_management',
      'claude_mcp_management',
      'claude_plugins_management',
      'claude_bypass_permissions',
      'terminal_drag_drop',
      'refresh_open_files_on_disk_change'
    ];
    // Policies that default *open* when the capability field is missing,
    // covering two cases: admin-only management gates (no user toggle) where
    // a new frontend on an older backend must keep the tab visible, and the
    // open-files refresh watcher whose documented default is on so its
    // first ticks before capabilities land don't silently no-op. The other
    // policies pair with a user toggle and default closed (missing means
    // "no user pref recorded yet, treat as off").
    const defaultOpen: ReadonlySet<FeaturePolicyName> = new Set([
      'skills_management',
      'claude_mcp_management',
      'claude_plugins_management',
      'refresh_open_files_on_disk_change'
    ]);
    const result = {} as IFeaturePolicies;
    for (const name of names) {
      const entry = v[name];
      // Strict polarity: only default-open when the entry is wholly absent
      // (old backend). A malformed entry (string "false", null, missing
      // `enabled` field) falls through to closed for default-closed gates
      // and stays open only when the field is explicitly true for default-open
      // gates — never silently land in the open bucket.
      let enabled: boolean;
      if (entry === undefined) {
        enabled = defaultOpen.has(name);
      } else {
        enabled = entry.enabled === true;
      }
      result[name] = {
        enabled,
        locked: entry?.locked === true
      };
    }
    return result;
  }

  get settingLocks(): ISettingLocks {
    const v = this.capabilities.setting_locks ?? {};
    const names: SettingLockName[] = [
      'chat_model_provider',
      'chat_model_id',
      'inline_completion_model_provider',
      'inline_completion_model_id',
      'claude_chat_model',
      'claude_inline_completion_model',
      'claude_api_key',
      'claude_base_url',
      'acp_chat_model',
      'acp_api_key',
      'acp_base_url'
    ];
    const result = {} as ISettingLocks;
    for (const name of names) {
      result[name] = { locked: v[name]?.locked === true };
    }
    return result;
  }

  capabilities: any = {};
  chatParticipants: IChatParticipant[] = [];

  changed = new Signal<this, void>(this);
}

export class NBIAPI {
  static _loginStatus = GitHubCopilotLoginStatus.NotLoggedIn;
  static _deviceVerificationInfo: IDeviceVerificationInfo = {
    verificationURI: '',
    userCode: ''
  };
  static _webSocket: WebSocket;
  static _messageReceived = new Signal<unknown, any>(this);
  static config = new NBIConfig();
  static configChanged = this.config.changed;
  static githubLoginStatusChanged = new Signal<unknown, void>(this);
  static skillsReloaded = new Signal<unknown, void>(this);
  // Emits each time the Claude agent sends its 20s keepalive (#252 follow-up).
  // The chat sidebar uses it to drive the "Generating" indicator's pulse
  // and to swap to a "server may be slow" copy when the gap stretches.
  static claudeCodeHeartbeat = new Signal<unknown, void>(this);
  static claudePermissionModeChanged = new Signal<
    unknown,
    { mode: string; reset: boolean }
  >(this);

  static async initialize() {
    await this.fetchCapabilities();
    this.updateGitHubLoginStatus();

    NBIAPI.initializeWebsocket();

    this._messageReceived.connect((_, msg) => {
      msg = JSON.parse(msg);
      if (
        msg.type === BackendMessageType.MCPServerStatusChange ||
        msg.type === BackendMessageType.ClaudeCodeStatusChange
      ) {
        this.fetchCapabilities();
      } else if (
        msg.type === BackendMessageType.GitHubCopilotLoginStatusChange
      ) {
        // The Copilot chat-model catalogue is fetched lazily once the bearer
        // token is minted (issue #258), so the model dropdown depends on a
        // capabilities refresh in addition to the login-status update.
        Promise.all([
          this.updateGitHubLoginStatus(),
          this.fetchCapabilities()
        ]).then(() => {
          this.githubLoginStatusChanged.emit();
        });
      } else if (msg.type === BackendMessageType.SkillsReloaded) {
        this.skillsReloaded.emit();
      } else if (msg.type === BackendMessageType.ClaudeCodeHeartbeat) {
        this.claudeCodeHeartbeat.emit();
      } else if (msg.type === BackendMessageType.ClaudePermissionModeChange) {
        this.claudePermissionModeChanged.emit({
          mode: msg.data?.mode ?? 'default',
          reset: msg.data?.reset ?? false
        });
      }
    });
  }

  static async initializeWebsocket() {
    const serverSettings = ServerConnection.makeSettings();
    const wsUrl = URLExt.join(
      serverSettings.wsUrl,
      'notebook-intelligence',
      'copilot'
    );

    this._webSocket = new serverSettings.WebSocket(wsUrl);
    this._webSocket.onmessage = msg => {
      this._messageReceived.emit(msg.data);
    };

    this._webSocket.onerror = msg => {
      console.error(`Websocket error: ${msg}. Closing...`);
      this._webSocket.close();
    };

    this._webSocket.onclose = msg => {
      console.log(`Websocket is closed: ${msg.reason}. Reconnecting...`);
      setTimeout(() => {
        NBIAPI.initializeWebsocket();
      }, 1000);
    };
  }

  static getLoginStatus(): GitHubCopilotLoginStatus {
    return this._loginStatus;
  }

  static getDeviceVerificationInfo(): IDeviceVerificationInfo {
    return this._deviceVerificationInfo;
  }

  static getGHLoginRequired() {
    return (
      this.config.usingGitHubCopilotModel &&
      NBIAPI.getLoginStatus() === GitHubCopilotLoginStatus.NotLoggedIn
    );
  }

  static getChatEnabled() {
    return (
      this.config.isInClaudeCodeMode ||
      this.config.isInAcpMode ||
      (this.config.chatModel.provider === GITHUB_COPILOT_PROVIDER_ID
        ? !this.getGHLoginRequired()
        : this.config.llmProviders.find(
            provider => provider.id === this.config.chatModel.provider
          ))
    );
  }

  static getInlineCompletionEnabled() {
    return (
      this.config.isInClaudeCodeMode ||
      (this.config.inlineCompletionModel.provider === GITHUB_COPILOT_PROVIDER_ID
        ? !this.getGHLoginRequired()
        : this.config.llmProviders.find(
            provider =>
              provider.id === this.config.inlineCompletionModel.provider
          ))
    );
  }

  static async loginToGitHub() {
    this._loginStatus = GitHubCopilotLoginStatus.ActivatingDevice;
    return new Promise((resolve, reject) => {
      requestAPI<any>('gh-login', { method: 'POST' })
        .then(data => {
          resolve({
            verificationURI: data.verification_uri,
            userCode: data.user_code
          });
          this.updateGitHubLoginStatus();
        })
        .catch(reason => {
          console.error(`Failed to login to GitHub Copilot.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async logoutFromGitHub() {
    this._loginStatus = GitHubCopilotLoginStatus.ActivatingDevice;
    return new Promise((resolve, reject) => {
      requestAPI<any>('gh-logout', { method: 'GET' })
        .then(data => {
          this.updateGitHubLoginStatus().then(() => {
            resolve(data);
          });
        })
        .catch(reason => {
          console.error(`Failed to logout from GitHub Copilot.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async updateGitHubLoginStatus() {
    return new Promise<void>((resolve, reject) => {
      requestAPI<any>('gh-login-status')
        .then(response => {
          this._loginStatus = response.status;
          this._deviceVerificationInfo.verificationURI =
            response.verification_uri || '';
          this._deviceVerificationInfo.userCode = response.user_code || '';
          resolve();
        })
        .catch(reason => {
          console.error(
            `Failed to fetch GitHub Copilot login status.\n${reason}`
          );
          reject(reason);
        });
    });
  }

  static async fetchCapabilities(): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      requestAPI<any>('capabilities', { method: 'GET' })
        .then(data => {
          const oldConfig = {
            capabilities: structuredClone(this.config.capabilities),
            chatParticipants: structuredClone(this.config.chatParticipants)
          };
          this.config.capabilities = structuredClone(data);
          this.config.chatParticipants = structuredClone(
            data.chat_participants
          );
          const newConfig = {
            capabilities: structuredClone(this.config.capabilities),
            chatParticipants: structuredClone(this.config.chatParticipants)
          };
          if (JSON.stringify(newConfig) !== JSON.stringify(oldConfig)) {
            this.configChanged.emit();
          }
          resolve();
        })
        .catch(reason => {
          console.error(`Failed to get extension capabilities.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async setConfig(config: any) {
    requestAPI<any>('config', {
      method: 'POST',
      body: JSON.stringify(config)
    })
      .then(data => {
        NBIAPI.fetchCapabilities();
      })
      .catch(reason => {
        console.error(`Failed to set NBI config.\n${reason}`);
      });
  }

  static async updateOllamaModelList(): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      requestAPI<any>('update-provider-models', {
        method: 'POST',
        body: JSON.stringify({ provider: 'ollama' })
      })
        .then(async data => {
          await NBIAPI.fetchCapabilities();
          resolve();
        })
        .catch(reason => {
          console.error(`Failed to update ollama model list.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async updateClaudeModelList(): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      requestAPI<any>('update-provider-models', {
        method: 'POST',
        body: JSON.stringify({ provider: 'claude' })
      })
        .then(async data => {
          await NBIAPI.fetchCapabilities();
          resolve();
        })
        .catch(reason => {
          console.error(`Failed to update Claude model list.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async getMCPConfigFile(): Promise<any> {
    return new Promise<any>((resolve, reject) => {
      requestAPI<any>('mcp-config-file', { method: 'GET' })
        .then(async data => {
          resolve(data);
        })
        .catch(reason => {
          console.error(`Failed to get MCP config file.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async setMCPConfigFile(config: any): Promise<any> {
    return new Promise<any>((resolve, reject) => {
      requestAPI<any>('mcp-config-file', {
        method: 'POST',
        body: JSON.stringify(config)
      })
        .then(async data => {
          resolve(data);
        })
        .catch(reason => {
          console.error(`Failed to set MCP config file.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async listSkills(): Promise<ISkillSummary[]> {
    const data = await requestAPI<any>('skills', { method: 'GET' });
    return (data.skills ?? []).map(skillFromWire);
  }

  static async getSkillsContext(): Promise<ISkillsContext> {
    const data = await requestAPI<any>('skills/context', { method: 'GET' });
    return {
      projectRoot: data.project_root ?? '',
      projectName: data.project_name ?? '',
      userSkillsDir: data.user_skills_dir ?? '',
      projectSkillsDir: data.project_skills_dir ?? ''
    };
  }

  static async readSkill(
    scope: SkillScope,
    name: string
  ): Promise<ISkillDetail> {
    const data = await requestAPI<any>(
      `skills/${scope}/${encodeURIComponent(name)}`,
      { method: 'GET' }
    );
    return skillFromWire(data.skill);
  }

  static async createSkill(payload: {
    scope: SkillScope;
    name: string;
    description: string;
    allowedTools: string[];
    body: string;
  }): Promise<ISkillDetail> {
    const data = await requestAPI<any>('skills', {
      method: 'POST',
      body: JSON.stringify({
        scope: payload.scope,
        name: payload.name,
        description: payload.description,
        allowed_tools: payload.allowedTools,
        body: payload.body
      })
    });
    return skillFromWire(data.skill);
  }

  static async updateSkill(
    scope: SkillScope,
    name: string,
    payload: {
      description?: string;
      allowedTools?: string[];
      body?: string;
      tracksUpstream?: boolean;
    }
  ): Promise<ISkillDetail> {
    const wire: any = {};
    if (payload.description !== undefined) {
      wire.description = payload.description;
    }
    if (payload.allowedTools !== undefined) {
      wire.allowed_tools = payload.allowedTools;
    }
    if (payload.body !== undefined) {
      wire.body = payload.body;
    }
    if (payload.tracksUpstream !== undefined) {
      wire.tracks_upstream = payload.tracksUpstream;
    }
    const data = await requestAPI<any>(
      `skills/${scope}/${encodeURIComponent(name)}`,
      {
        method: 'PUT',
        body: JSON.stringify(wire)
      }
    );
    return skillFromWire(data.skill);
  }

  static async deleteSkill(scope: SkillScope, name: string): Promise<void> {
    await requestAPI<any>(`skills/${scope}/${encodeURIComponent(name)}`, {
      method: 'DELETE'
    });
  }

  static async previewSkillImport(url: string): Promise<ISkillImportPreview> {
    const data = await requestAPI<any>('skills/import/preview', {
      method: 'POST',
      body: JSON.stringify({ url })
    });
    const p = data.preview;
    return {
      name: p.name,
      description: p.description ?? '',
      allowedTools: p.allowed_tools ?? [],
      body: p.body ?? '',
      files: p.files ?? [],
      sourceUrl: p.source_url ?? '',
      canonicalUrl: p.canonical_url ?? '',
      existsInUserScope: p.exists_in_user_scope === true,
      existsInProjectScope: p.exists_in_project_scope === true
    };
  }

  static async importSkill(payload: {
    url: string;
    scope: SkillScope;
    name?: string;
    overwrite?: boolean;
    tracksUpstream?: boolean;
  }): Promise<ISkillDetail> {
    const wire: any = { url: payload.url, scope: payload.scope };
    if (payload.name) {
      wire.name = payload.name;
    }
    if (payload.overwrite) {
      wire.overwrite = true;
    }
    if (payload.tracksUpstream) {
      wire.tracks_upstream = true;
    }
    const data = await requestAPI<any>('skills/import', {
      method: 'POST',
      body: JSON.stringify(wire)
    });
    return skillFromWire(data.skill);
  }

  static async syncTrackingSkill(
    scope: SkillScope,
    name: string
  ): Promise<ISyncSkillResult> {
    const data = await requestAPI<any>(
      `skills/${scope}/${encodeURIComponent(name)}/sync`,
      { method: 'POST' }
    );
    return {
      updated: Boolean(data.updated),
      ref: data.ref ?? ''
    };
  }

  static async syncAllTrackingSkills(): Promise<ISyncAllTrackingEntry[]> {
    const data = await requestAPI<any>('skills/sync-all-tracking', {
      method: 'POST'
    });
    if (!Array.isArray(data?.results)) {
      return [];
    }
    return data.results.map((r: any) => ({
      scope: r.scope,
      name: r.name,
      updated: typeof r.updated === 'boolean' ? r.updated : undefined,
      ref: typeof r.ref === 'string' ? r.ref : undefined,
      error: typeof r.error === 'string' ? r.error : undefined
    }));
  }

  static async listClaudeMCPServers(): Promise<IClaudeMCPServer[]> {
    const data = await requestAPI<any>('claude-mcp');
    return Array.isArray(data?.servers)
      ? data.servers.map(claudeMCPServerFromWire)
      : [];
  }

  static async addClaudeMCPServer(
    input: IClaudeMCPAddInput
  ): Promise<IClaudeMCPServer> {
    const body: any = {
      name: input.name,
      scope: input.scope,
      transport: input.transport,
      command_or_url: input.commandOrUrl
    };
    if (input.args && input.args.length) {
      body.args = input.args;
    }
    if (input.env && Object.keys(input.env).length) {
      body.env = input.env;
    }
    if (input.headers && Object.keys(input.headers).length) {
      body.headers = input.headers;
    }
    const data = await requestAPI<any>('claude-mcp', {
      method: 'POST',
      body: JSON.stringify(body)
    });
    return claudeMCPServerFromWire(data.server);
  }

  static async removeClaudeMCPServer(
    name: string,
    scope: ClaudeMCPScope
  ): Promise<void> {
    await requestAPI<any>(`claude-mcp/${scope}/${encodeURIComponent(name)}`, {
      method: 'DELETE'
    });
  }

  static async setClaudeMCPServerDisabled(
    name: string,
    scope: ClaudeMCPScope,
    disabled: boolean
  ): Promise<IClaudeMCPServer> {
    const data = await requestAPI<any>(
      `claude-mcp/${scope}/${encodeURIComponent(name)}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ disabled_for_workspace: disabled })
      }
    );
    return claudeMCPServerFromWire(data.server);
  }

  static async listPlugins(): Promise<IPluginInfo[]> {
    const data = await requestAPI<any>('plugins');
    return Array.isArray(data?.plugins) ? (data.plugins as IPluginInfo[]) : [];
  }

  static async installPlugin(
    plugin: string,
    scope: PluginScope = 'user'
  ): Promise<void> {
    await requestAPI<any>('plugins', {
      method: 'POST',
      body: JSON.stringify({ plugin, scope })
    });
  }

  static async uninstallPlugin(
    plugin: string,
    scope: PluginScope = 'user'
  ): Promise<void> {
    await requestAPI<any>(`plugins/${scope}/${encodeURIComponent(plugin)}`, {
      method: 'DELETE'
    });
  }

  static async setPluginEnabled(
    plugin: string,
    scope: PluginScope,
    enabled: boolean
  ): Promise<void> {
    await requestAPI<any>(`plugins/${scope}/${encodeURIComponent(plugin)}`, {
      method: 'POST',
      body: JSON.stringify({ action: enabled ? 'enable' : 'disable' })
    });
  }

  static async listPluginMarketplaces(): Promise<IPluginMarketplaceInfo[]> {
    const data = await requestAPI<any>('plugins/marketplace');
    return Array.isArray(data?.marketplaces)
      ? (data.marketplaces as IPluginMarketplaceInfo[])
      : [];
  }

  static async listPluginMarketplacePlugins(
    marketplace: string
  ): Promise<IPluginMarketplacePluginInfo[]> {
    const data = await requestAPI<any>(
      `plugins/marketplace/${encodeURIComponent(marketplace)}/plugins`
    );
    return Array.isArray(data?.plugins)
      ? (data.plugins as IPluginMarketplacePluginInfo[])
      : [];
  }

  static async addPluginMarketplace(
    source: string,
    scope: PluginScope = 'user'
  ): Promise<void> {
    await requestAPI<any>('plugins/marketplace', {
      method: 'POST',
      body: JSON.stringify({ source, scope })
    });
  }

  static async removePluginMarketplace(name: string): Promise<void> {
    await requestAPI<any>(`plugins/marketplace/${encodeURIComponent(name)}`, {
      method: 'DELETE'
    });
  }

  static async updatePluginMarketplace(name: string): Promise<void> {
    await requestAPI<any>(
      `plugins/marketplace/${encodeURIComponent(name)}/update`,
      {
        method: 'POST',
        body: '{}'
      }
    );
  }

  static async reconcileManagedSkills(): Promise<IReconcileResult> {
    const data = await requestAPI<any>('skills/reconcile', {
      method: 'POST'
    });
    return {
      added: Number(data.added ?? 0),
      updated: Number(data.updated ?? 0),
      removed: Number(data.removed ?? 0),
      unchanged: Number(data.unchanged ?? 0),
      errors: Array.isArray(data.errors) ? data.errors.map(String) : []
    };
  }

  static async renameSkill(
    scope: SkillScope,
    name: string,
    newName: string
  ): Promise<ISkillDetail> {
    const data = await requestAPI<any>(
      `skills/${scope}/${encodeURIComponent(name)}/rename`,
      {
        method: 'POST',
        body: JSON.stringify({ new_name: newName })
      }
    );
    return skillFromWire(data.skill);
  }

  static async readBundleFile(
    scope: SkillScope,
    name: string,
    path: string
  ): Promise<string> {
    const data = await requestAPI<any>(
      `skills/${scope}/${encodeURIComponent(name)}/files?path=${encodeURIComponent(path)}`,
      { method: 'GET' }
    );
    return data.content;
  }

  static async writeBundleFile(
    scope: SkillScope,
    name: string,
    path: string,
    content: string
  ): Promise<void> {
    await requestAPI<any>(
      `skills/${scope}/${encodeURIComponent(name)}/files?path=${encodeURIComponent(path)}`,
      {
        method: 'PUT',
        body: JSON.stringify({ content })
      }
    );
  }

  static async deleteBundleFile(
    scope: SkillScope,
    name: string,
    path: string
  ): Promise<void> {
    await requestAPI<any>(
      `skills/${scope}/${encodeURIComponent(name)}/files?path=${encodeURIComponent(path)}`,
      { method: 'DELETE' }
    );
  }

  static async renameBundleFile(
    scope: SkillScope,
    name: string,
    from: string,
    to: string
  ): Promise<void> {
    await requestAPI<any>(
      `skills/${scope}/${encodeURIComponent(name)}/files/rename`,
      {
        method: 'POST',
        body: JSON.stringify({ from, to })
      }
    );
  }

  /**
   * Subscribe to inbound websocket messages for a single request, forwarding
   * them to `responseEmitter`. The subscription auto-disconnects when the
   * server emits StreamEnd, preventing per-request listener accumulation.
   */
  private static _subscribeUntilStreamEnd(
    messageId: string,
    responseEmitter: IChatCompletionResponseEmitter
  ): void {
    const handler = (_: unknown, msg: any) => {
      const parsed = JSON.parse(msg);
      if (parsed.id !== messageId) {
        return;
      }
      responseEmitter.emit(parsed);
      if (parsed.type === BackendMessageType.StreamEnd) {
        this._messageReceived.disconnect(handler);
      }
    };
    this._messageReceived.connect(handler);
  }

  static async chatRequest(
    messageId: string,
    chatId: string,
    prompt: string,
    language: string,
    kernelName: string,
    kernelDisplayName: string,
    currentDirectory: string,
    filename: string,
    additionalContext: IContextItem[],
    chatMode: string,
    toolSelections: IToolSelections,
    responseEmitter: IChatCompletionResponseEmitter,
    permissionMode: string = 'default'
  ) {
    this._subscribeUntilStreamEnd(messageId, responseEmitter);
    this._webSocket.send(
      JSON.stringify({
        id: messageId,
        type: RequestDataType.ChatRequest,
        data: {
          chatId,
          prompt,
          language,
          kernelName,
          kernelDisplayName,
          currentDirectory,
          filename,
          additionalContext,
          chatMode,
          toolSelections,
          permissionMode
        }
      })
    );
  }

  static async reloadMCPServers(): Promise<any> {
    return new Promise<any>((resolve, reject) => {
      requestAPI<any>('reload-mcp-servers', { method: 'POST' })
        .then(async data => {
          await NBIAPI.fetchCapabilities();
          resolve(data);
        })
        .catch(reason => {
          console.error(`Failed to reload MCP servers.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async generateCode(
    messageId: string,
    chatId: string,
    prompt: string,
    prefix: string,
    suffix: string,
    existingCode: string,
    language: string,
    kernelName: string,
    filename: string,
    responseEmitter: IChatCompletionResponseEmitter
  ) {
    this._subscribeUntilStreamEnd(messageId, responseEmitter);
    this._webSocket.send(
      JSON.stringify({
        id: messageId,
        type: RequestDataType.GenerateCode,
        data: {
          chatId,
          prompt,
          prefix,
          suffix,
          existingCode,
          language,
          kernelName,
          filename
        }
      })
    );
  }

  static async sendChatUserInput(messageId: string, data: any) {
    this._webSocket.send(
      JSON.stringify({
        id: messageId,
        type: RequestDataType.ChatUserInput,
        data
      })
    );
  }

  static async sendWebSocketMessage(
    messageId: string,
    messageType: RequestDataType,
    data: any
  ) {
    this._webSocket.send(
      JSON.stringify({ id: messageId, type: messageType, data })
    );
  }

  static async inlineCompletionsRequest(
    chatId: string,
    messageId: string,
    prefix: string,
    suffix: string,
    language: string,
    filename: string,
    responseEmitter: IChatCompletionResponseEmitter
  ) {
    this._subscribeUntilStreamEnd(messageId, responseEmitter);
    this._webSocket.send(
      JSON.stringify({
        id: messageId,
        type: RequestDataType.InlineCompletionRequest,
        data: {
          chatId,
          prefix,
          suffix,
          language,
          filename
        }
      })
    );
  }

  static async uploadFile(
    file: File
  ): Promise<{ serverPath: string; filename: string }> {
    const formData = new FormData();
    formData.append('file', file, file.name);
    return requestAPI<{ serverPath: string; filename: string }>('upload-file', {
      method: 'POST',
      body: formData
    });
  }

  static async listClaudeSessions(
    scope: ClaudeSessionScope = 'all'
  ): Promise<IClaudeSessionList> {
    interface IWireResponse {
      sessions?: IClaudeSessionInfo[];
      current_cwd?: string;
    }
    return new Promise<IClaudeSessionList>((resolve, reject) => {
      requestAPI<IWireResponse>(`claude-sessions?scope=${scope}`, {
        method: 'GET'
      })
        .then(data => {
          resolve({
            sessions: data.sessions ?? [],
            currentCwd: data.current_cwd ?? ''
          });
        })
        .catch(reason => {
          console.error(`Failed to list Claude sessions.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async resumeClaudeSession(sessionId: string): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      requestAPI<any>('claude-sessions/resume', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId })
      })
        .then(() => {
          resolve();
        })
        .catch(reason => {
          console.error(`Failed to resume Claude session.\n${reason}`);
          reject(reason);
        });
    });
  }

  static async listAcpSessions(): Promise<IClaudeSessionList> {
    interface IWireResponse {
      sessions?: IClaudeSessionInfo[];
      current_cwd?: string;
      error?: string;
    }
    const data = await requestAPI<IWireResponse>('acp-sessions', {
      method: 'GET'
    });
    if (data.error) {
      throw new Error(data.error);
    }
    return {
      sessions: data.sessions ?? [],
      currentCwd: data.current_cwd ?? ''
    };
  }

  static async resumeAcpSession(sessionId: string): Promise<void> {
    await requestAPI<any>('acp-sessions/resume', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId })
    });
  }

  static async emitTelemetryEvent(event: ITelemetryEvent): Promise<void> {
    const assistantMode = this.config.isInClaudeCodeMode
      ? AssistantMode.Claude
      : AssistantMode.Default;

    event.data = {
      ...(event.data || {}),
      assistantMode
    };

    return new Promise<void>((resolve, reject) => {
      requestAPI<any>('emit-telemetry-event', {
        method: 'POST',
        body: JSON.stringify(event)
      })
        .then(async data => {
          resolve();
        })
        .catch(reason => {
          console.error(`Failed to emit telemetry event.\n${reason}`);
          reject(reason);
        });
    });
  }
}

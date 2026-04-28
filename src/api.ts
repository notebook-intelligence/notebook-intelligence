// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { ServerConnection } from '@jupyterlab/services';
import { requestAPI } from './handler';
import { URLExt } from '@jupyterlab/coreutils';
import { UUID } from '@lumino/coreutils';
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
}

export enum ClaudeToolType {
  ClaudeCodeTools = 'claude-code:built-in-tools',
  JupyterUITools = 'nbi:built-in-jupyter-ui-tools'
}

export type SkillScope = 'user' | 'project';

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

function skillFromWire(wire: any): ISkillDetail {
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
    body: wire.body ?? ''
  };
}

function claudeModelFromWire(wire: any): IClaudeModelInfo {
  return {
    id: wire.id,
    name: wire.name,
    contextWindow: wire.context_window
  };
}

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

  get claudeModels(): IClaudeModelInfo[] {
    return (this.capabilities.claude_models ?? []).map(claudeModelFromWire);
  }

  get isInClaudeCodeMode(): boolean {
    return this.claudeSettings.enabled === true;
  }

  get chatFeedbackEnabled(): boolean {
    return this.capabilities.chat_feedback_enabled === true;
  }

  get cellOutputFeatures(): {
    explain_error: { enabled: boolean; locked: boolean };
    output_followup: { enabled: boolean; locked: boolean };
  } {
    const v = this.capabilities.cell_output_features ?? {};
    return {
      explain_error: {
        enabled: v.explain_error?.enabled !== false,
        locked: v.explain_error?.locked === true
      },
      output_followup: {
        enabled: v.output_followup?.enabled !== false,
        locked: v.output_followup?.locked === true
      }
    };
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
        this.updateGitHubLoginStatus().then(() => {
          this.githubLoginStatusChanged.emit();
        });
      } else if (msg.type === BackendMessageType.SkillsReloaded) {
        this.skillsReloaded.emit();
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
  }): Promise<ISkillDetail> {
    const wire: any = { url: payload.url, scope: payload.scope };
    if (payload.name) {
      wire.name = payload.name;
    }
    if (payload.overwrite) {
      wire.overwrite = true;
    }
    const data = await requestAPI<any>('skills/import', {
      method: 'POST',
      body: JSON.stringify(wire)
    });
    return skillFromWire(data.skill);
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
    currentDirectory: string,
    filename: string,
    additionalContext: IContextItem[],
    chatMode: string,
    toolSelections: IToolSelections,
    responseEmitter: IChatCompletionResponseEmitter
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
          currentDirectory,
          filename,
          additionalContext,
          chatMode,
          toolSelections
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
    chatId: string,
    prompt: string,
    prefix: string,
    suffix: string,
    existingCode: string,
    language: string,
    filename: string,
    responseEmitter: IChatCompletionResponseEmitter
  ) {
    const messageId = UUID.uuid4();
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

  static async listClaudeSessions(): Promise<IClaudeSessionInfo[]> {
    return new Promise<IClaudeSessionInfo[]>((resolve, reject) => {
      requestAPI<any>('claude-sessions', { method: 'GET' })
        .then(data => {
          resolve(data.sessions ?? []);
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

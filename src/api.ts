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
  BackendMessageType
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

  static async initialize() {
    await this.fetchCapabilities();
    this.updateGitHubLoginStatus();

    NBIAPI.initializeWebsocket();

    this._messageReceived.connect((_, msg) => {
      msg = JSON.parse(msg);
      if (msg.type === BackendMessageType.MCPServerStatusChange) {
        this.fetchCapabilities();
      } else if (
        msg.type === BackendMessageType.GitHubCopilotLoginStatusChange
      ) {
        this.updateGitHubLoginStatus().then(() => {
          this.githubLoginStatusChanged.emit();
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

  static async chatRequest(
    messageId: string,
    chatId: string,
    prompt: string,
    language: string,
    filename: string,
    additionalContext: IContextItem[],
    chatMode: string,
    toolSelections: IToolSelections,
    responseEmitter: IChatCompletionResponseEmitter
  ) {
    this._messageReceived.connect((_, msg) => {
      msg = JSON.parse(msg);
      if (msg.id === messageId) {
        responseEmitter.emit(msg);
      }
    });
    this._webSocket.send(
      JSON.stringify({
        id: messageId,
        type: RequestDataType.ChatRequest,
        data: {
          chatId,
          prompt,
          language,
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
    this._messageReceived.connect((_, msg) => {
      msg = JSON.parse(msg);
      if (msg.id === messageId) {
        responseEmitter.emit(msg);
      }
    });
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
    this._messageReceived.connect((_, msg) => {
      msg = JSON.parse(msg);
      if (msg.id === messageId) {
        responseEmitter.emit(msg);
      }
    });
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

  static async emitTelemetryEvent(event: ITelemetryEvent): Promise<void> {
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

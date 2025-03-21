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
  RequestDataType
} from './tokens';

const LOGIN_STATUS_UPDATE_INTERVAL = 2000;
const WEBSOCKET_STATUS_CHECK_INTERVAL = 2000;

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
  get llmProviders(): [any] {
    return this.capabilities.llm_providers;
  }

  get chatModels(): [any] {
    return this.capabilities.chat_models;
  }

  get inlineCompletionModels(): [any] {
    return this.capabilities.inline_completion_models;
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

  static async initialize() {
    await this.fetchCapabilities();
    this.updateGitHubLoginStatus();

    setInterval(() => {
      this.updateGitHubLoginStatus();
    }, LOGIN_STATUS_UPDATE_INTERVAL);

    setInterval(() => {
      if (!this._webSocket || this._webSocket.readyState !== WebSocket.OPEN) {
        console.warn('WebSocket is not open. Opening...');
        NBIAPI.initializeWebsocket();
      }
    }, WEBSOCKET_STATUS_CHECK_INTERVAL);
  }

  static async initializeWebsocket() {
    const serverSettings = ServerConnection.makeSettings();
    const wsUrl = URLExt.join(
      serverSettings.wsUrl,
      'notebook-intelligence',
      'copilot'
    );

    this._webSocket = new WebSocket(wsUrl);
    this._webSocket.onmessage = msg => {
      this._messageReceived.emit(msg.data);
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
          this.config.capabilities = structuredClone(data);
          this.config.chatParticipants = structuredClone(
            data.chat_participants
          );
          this.configChanged.emit();
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

  static async chatRequest(
    messageId: string,
    chatId: string,
    prompt: string,
    language: string,
    filename: string,
    additionalContext: IContextItem[],
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
        data: { chatId, prompt, language, filename, additionalContext }
      })
    );
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

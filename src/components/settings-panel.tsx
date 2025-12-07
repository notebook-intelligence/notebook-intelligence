// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, { useEffect, useRef, useState } from 'react';
import { ReactWidget } from '@jupyterlab/apputils';
import { VscWarning } from 'react-icons/vsc';
import * as path from 'path';

import copySvgstr from '../../style/icons/copy.svg';
import { NBIAPI } from '../api';
import { CheckBoxItem } from './checkbox';
import { PillItem } from './pill';
import { mcpServerSettingsToEnabledState } from './mcp-util';

const OPENAI_COMPATIBLE_CHAT_MODEL_ID = 'openai-compatible-chat-model';
const LITELLM_COMPATIBLE_CHAT_MODEL_ID = 'litellm-compatible-chat-model';
const OPENAI_COMPATIBLE_INLINE_COMPLETION_MODEL_ID =
  'openai-compatible-inline-completion-model';
const LITELLM_COMPATIBLE_INLINE_COMPLETION_MODEL_ID =
  'litellm-compatible-inline-completion-model';

export class SettingsPanel extends ReactWidget {
  constructor(options: {
    onSave: () => void;
    onEditMCPConfigClicked: () => void;
  }) {
    super();

    this._onSave = options.onSave;
    this._onEditMCPConfigClicked = options.onEditMCPConfigClicked;
  }

  render(): JSX.Element {
    return (
      <SettingsPanelComponent
        onSave={this._onSave}
        onEditMCPConfigClicked={this._onEditMCPConfigClicked}
      />
    );
  }

  private _onSave: () => void;
  private _onEditMCPConfigClicked: () => void;
}

function SettingsPanelComponent(props: any) {
  const [activeTab, setActiveTab] = useState('general');

  const onTabSelected = (tab: string) => {
    setActiveTab(tab);
  };

  return (
    <div className="nbi-settings-panel">
      <div className="nbi-settings-panel-tabs">
        <SettingsPanelTabsComponent
          onTabSelected={onTabSelected}
          activeTab={activeTab}
        />
      </div>
      <div className="nbi-settings-panel-tab-content">
        {activeTab === 'general' && (
          <SettingsPanelComponentGeneral
            onSave={props.onSave}
            onEditMCPConfigClicked={props.onEditMCPConfigClicked}
          />
        )}
        {activeTab === 'mcp-servers' && (
          <SettingsPanelComponentMCPServers
            onEditMCPConfigClicked={props.onEditMCPConfigClicked}
          />
        )}
      </div>
    </div>
  );
}

function SettingsPanelTabsComponent(props: any) {
  const [activeTab, setActiveTab] = useState(props.activeTab);

  return (
    <div>
      <div
        className={`nbi-settings-panel-tab ${activeTab === 'general' ? 'active' : ''}`}
        onClick={() => {
          setActiveTab('general');
          props.onTabSelected('general');
        }}
      >
        General
      </div>
      <div
        className={`nbi-settings-panel-tab ${activeTab === 'mcp-servers' ? 'active' : ''}`}
        onClick={() => {
          setActiveTab('mcp-servers');
          props.onTabSelected('mcp-servers');
        }}
      >
        MCP Servers
      </div>
    </div>
  );
}

function SettingsPanelComponentGeneral(props: any) {
  const nbiConfig = NBIAPI.config;
  const llmProviders = nbiConfig.llmProviders;
  const [chatModels, setChatModels] = useState([]);
  const [inlineCompletionModels, setInlineCompletionModels] = useState([]);

  const handleSaveSettings = async () => {
    const config: any = {
      default_chat_mode: defaultChatMode,
      chat_model: {
        provider: chatModelProvider,
        model: chatModel,
        properties: chatModelProperties
      },
      inline_completion_model: {
        provider: inlineCompletionModelProvider,
        model: inlineCompletionModel,
        properties: inlineCompletionModelProperties
      }
    };

    if (
      chatModelProvider === 'github-copilot' ||
      inlineCompletionModelProvider === 'github-copilot'
    ) {
      config.store_github_access_token = storeGitHubAccessToken;
    }

    await NBIAPI.setConfig(config);

    props.onSave();
  };

  const handleRefreshOllamaModelListClick = async () => {
    await NBIAPI.updateOllamaModelList();
    updateModelOptionsForProvider(chatModelProvider, 'chat');
  };

  const [chatModelProvider, setChatModelProvider] = useState(
    nbiConfig.chatModel.provider || 'none'
  );
  const [inlineCompletionModelProvider, setInlineCompletionModelProvider] =
    useState(nbiConfig.inlineCompletionModel.provider || 'none');
  const [defaultChatMode, setDefaultChatMode] = useState<string>(
    nbiConfig.defaultChatMode
  );
  const [chatModel, setChatModel] = useState<string>(nbiConfig.chatModel.model);
  const [chatModelProperties, setChatModelProperties] = useState<any[]>([]);
  const [inlineCompletionModelProperties, setInlineCompletionModelProperties] =
    useState<any[]>([]);
  const [inlineCompletionModel, setInlineCompletionModel] = useState(
    nbiConfig.inlineCompletionModel.model
  );
  const [storeGitHubAccessToken, setStoreGitHubAccessToken] = useState(
    nbiConfig.storeGitHubAccessToken
  );

  const updateModelOptionsForProvider = (
    providerId: string,
    modelType: 'chat' | 'inline-completion'
  ) => {
    if (modelType === 'chat') {
      setChatModelProvider(providerId);
    } else {
      setInlineCompletionModelProvider(providerId);
    }
    const models =
      modelType === 'chat'
        ? nbiConfig.chatModels
        : nbiConfig.inlineCompletionModels;
    const selectedModelId =
      modelType === 'chat'
        ? nbiConfig.chatModel.model
        : nbiConfig.inlineCompletionModel.model;

    const providerModels = models.filter(
      (model: any) => model.provider === providerId
    );
    if (modelType === 'chat') {
      setChatModels(providerModels);
    } else {
      setInlineCompletionModels(providerModels);
    }
    let selectedModel = providerModels.find(
      (model: any) => model.id === selectedModelId
    );
    if (!selectedModel) {
      selectedModel = providerModels?.[0];
    }
    if (selectedModel) {
      if (modelType === 'chat') {
        setChatModel(selectedModel.id);
        setChatModelProperties(selectedModel.properties);
      } else {
        setInlineCompletionModel(selectedModel.id);
        setInlineCompletionModelProperties(selectedModel.properties);
      }
    } else {
      if (modelType === 'chat') {
        setChatModelProperties([]);
      } else {
        setInlineCompletionModelProperties([]);
      }
    }
  };

  const onModelPropertyChange = (
    modelType: 'chat' | 'inline-completion',
    propertyId: string,
    value: string
  ) => {
    const modelProperties =
      modelType === 'chat'
        ? chatModelProperties
        : inlineCompletionModelProperties;
    const updatedProperties = modelProperties.map((property: any) => {
      if (property.id === propertyId) {
        return { ...property, value };
      }
      return property;
    });
    if (modelType === 'chat') {
      setChatModelProperties(updatedProperties);
    } else {
      setInlineCompletionModelProperties(updatedProperties);
    }
  };

  useEffect(() => {
    updateModelOptionsForProvider(chatModelProvider, 'chat');
    updateModelOptionsForProvider(
      inlineCompletionModelProvider,
      'inline-completion'
    );
  }, []);

  useEffect(() => {
    handleSaveSettings();
  }, [
    defaultChatMode,
    chatModelProvider,
    chatModel,
    chatModelProperties,
    inlineCompletionModelProvider,
    inlineCompletionModel,
    inlineCompletionModelProperties,
    storeGitHubAccessToken
  ]);

  return (
    <div className="config-dialog">
      <div className="config-dialog-body">
        <div className="model-config-section">
          <div className="model-config-section-header">Default chat mode</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>
                  <select
                    className="jp-mod-styled"
                    value={defaultChatMode}
                    onChange={event => setDefaultChatMode(event.target.value)}
                  >
                    <option value="ask">Ask</option>
                    <option value="agent">Agent</option>
                  </select>
                </div>
              </div>
              <div className="model-config-section-column"> </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">Chat model</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>Provider</div>
                <div>
                  <select
                    className="jp-mod-styled"
                    onChange={event =>
                      updateModelOptionsForProvider(event.target.value, 'chat')
                    }
                  >
                    {llmProviders.map((provider: any, index: number) => (
                      <option
                        key={index}
                        value={provider.id}
                        selected={provider.id === chatModelProvider}
                      >
                        {provider.name}
                      </option>
                    ))}
                    <option
                      key={-1}
                      value="none"
                      selected={
                        chatModelProvider === 'none' ||
                        !llmProviders.find(
                          provider => provider.id === chatModelProvider
                        )
                      }
                    >
                      None
                    </option>
                  </select>
                </div>
              </div>
              {!['openai-compatible', 'litellm-compatible', 'none'].includes(
                chatModelProvider
              ) &&
                chatModels.length > 0 && (
                  <div className="model-config-section-column">
                    <div>Model</div>
                    {![
                      OPENAI_COMPATIBLE_CHAT_MODEL_ID,
                      LITELLM_COMPATIBLE_CHAT_MODEL_ID
                    ].includes(chatModel) &&
                      chatModels.length > 0 && (
                        <div>
                          <select
                            className="jp-mod-styled"
                            onChange={event => setChatModel(event.target.value)}
                          >
                            {chatModels.map((model: any, index: number) => (
                              <option
                                key={index}
                                value={model.id}
                                selected={model.id === chatModel}
                              >
                                {model.name}
                              </option>
                            ))}
                          </select>
                        </div>
                      )}
                  </div>
                )}
            </div>

            <div className="model-config-section-row">
              <div className="model-config-section-column">
                {chatModelProvider === 'ollama' && chatModels.length === 0 && (
                  <div className="ollama-warning-message">
                    No Ollama models found! Make sure{' '}
                    <a href="https://ollama.com/" target="_blank">
                      Ollama
                    </a>{' '}
                    is running and models are downloaded to your computer.{' '}
                    <a
                      href="javascript:void(0)"
                      onClick={handleRefreshOllamaModelListClick}
                    >
                      Try again
                    </a>{' '}
                    once ready.
                  </div>
                )}
              </div>
            </div>

            <div className="model-config-section-row">
              <div className="model-config-section-column">
                {chatModelProperties.map((property: any, index: number) => (
                  <div className="form-field-row" key={index}>
                    <div className="form-field-description">
                      {property.name} {property.optional ? '(optional)' : ''}
                    </div>
                    <input
                      name="chat-model-id-input"
                      placeholder={property.description}
                      className="jp-mod-styled"
                      spellCheck={false}
                      value={property.value}
                      onChange={event =>
                        onModelPropertyChange(
                          'chat',
                          property.id,
                          event.target.value
                        )
                      }
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">Auto-complete model</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>Provider</div>
                <div>
                  <select
                    className="jp-mod-styled"
                    onChange={event =>
                      updateModelOptionsForProvider(
                        event.target.value,
                        'inline-completion'
                      )
                    }
                  >
                    {llmProviders.map((provider: any, index: number) => (
                      <option
                        key={index}
                        value={provider.id}
                        selected={provider.id === inlineCompletionModelProvider}
                      >
                        {provider.name}
                      </option>
                    ))}
                    <option
                      key={-1}
                      value="none"
                      selected={
                        inlineCompletionModelProvider === 'none' ||
                        !llmProviders.find(
                          provider =>
                            provider.id === inlineCompletionModelProvider
                        )
                      }
                    >
                      None
                    </option>
                  </select>
                </div>
              </div>
              {!['openai-compatible', 'litellm-compatible', 'none'].includes(
                inlineCompletionModelProvider
              ) && (
                <div className="model-config-section-column">
                  <div>Model</div>
                  {![
                    OPENAI_COMPATIBLE_INLINE_COMPLETION_MODEL_ID,
                    LITELLM_COMPATIBLE_INLINE_COMPLETION_MODEL_ID
                  ].includes(inlineCompletionModel) && (
                    <div>
                      <select
                        className="jp-mod-styled"
                        onChange={event =>
                          setInlineCompletionModel(event.target.value)
                        }
                      >
                        {inlineCompletionModels.map(
                          (model: any, index: number) => (
                            <option
                              key={index}
                              value={model.id}
                              selected={model.id === inlineCompletionModel}
                            >
                              {model.name}
                            </option>
                          )
                        )}
                      </select>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="model-config-section-row">
              <div className="model-config-section-column">
                {inlineCompletionModelProperties.map(
                  (property: any, index: number) => (
                    <div className="form-field-row" key={index}>
                      <div className="form-field-description">
                        {property.name} {property.optional ? '(optional)' : ''}
                      </div>
                      <input
                        name="inline-completion-model-id-input"
                        placeholder={property.description}
                        className="jp-mod-styled"
                        spellCheck={false}
                        value={property.value}
                        onChange={event =>
                          onModelPropertyChange(
                            'inline-completion',
                            property.id,
                            event.target.value
                          )
                        }
                      />
                    </div>
                  )
                )}
              </div>
            </div>
          </div>
        </div>

        {(chatModelProvider === 'github-copilot' ||
          inlineCompletionModelProvider === 'github-copilot') && (
          <div className="model-config-section">
            <div className="model-config-section-header access-token-config-header">
              GitHub Copilot login{' '}
              <a
                href="https://github.com/notebook-intelligence/notebook-intelligence/blob/main/README.md#remembering-github-copilot-login"
                target="_blank"
              >
                {' '}
                <VscWarning
                  className="access-token-warning"
                  title="Click to learn more about security implications"
                />
              </a>
            </div>
            <div className="model-config-section-body">
              <div className="model-config-section-row">
                <div className="model-config-section-column">
                  <label>
                    <input
                      type="checkbox"
                      checked={storeGitHubAccessToken}
                      onChange={event => {
                        setStoreGitHubAccessToken(event.target.checked);
                      }}
                    />
                    Remember my GitHub Copilot access token
                  </label>
                </div>
              </div>
            </div>
          </div>
        )}

        <div className="model-config-section">
          <div className="model-config-section-header">Config file path</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <span
                  className="user-code-span"
                  onClick={() => {
                    navigator.clipboard.writeText(
                      path.join(NBIAPI.config.userConfigDir, 'config.json')
                    );
                    return true;
                  }}
                >
                  {path.join(NBIAPI.config.userConfigDir, 'config.json')}{' '}
                  <span
                    className="copy-icon"
                    dangerouslySetInnerHTML={{ __html: copySvgstr }}
                  ></span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SettingsPanelComponentMCPServers(props: any) {
  const nbiConfig = NBIAPI.config;
  const mcpServersRef = useRef<any>(nbiConfig.toolConfig.mcpServers);
  const mcpServerSettingsRef = useRef<any>(nbiConfig.mcpServerSettings);
  const [renderCount, setRenderCount] = useState(1);

  const [mcpServerEnabledState, setMCPServerEnabledState] = useState(
    new Map<string, Set<string>>(
      mcpServerSettingsToEnabledState(
        mcpServersRef.current,
        mcpServerSettingsRef.current
      )
    )
  );

  const mcpServerEnabledStateToMcpServerSettings = () => {
    const mcpServerSettings: any = {};
    for (const mcpServer of mcpServersRef.current) {
      if (mcpServerEnabledState.has(mcpServer.id)) {
        const disabledTools = [];
        for (const tool of mcpServer.tools) {
          if (!mcpServerEnabledState.get(mcpServer.id).has(tool.name)) {
            disabledTools.push(tool.name);
          }
        }
        mcpServerSettings[mcpServer.id] = {
          disabled: false,
          disabled_tools: disabledTools
        };
      } else {
        mcpServerSettings[mcpServer.id] = { disabled: true };
      }
    }
    return mcpServerSettings;
  };

  const syncSettingsToServerState = () => {
    NBIAPI.setConfig({
      mcp_server_settings: mcpServerSettingsRef.current
    });
  };

  const handleReloadMCPServersClick = async () => {
    await NBIAPI.reloadMCPServers();
  };

  useEffect(() => {
    syncSettingsToServerState();
  }, [mcpServerSettingsRef.current]);

  useEffect(() => {
    mcpServerSettingsRef.current = mcpServerEnabledStateToMcpServerSettings();
    setRenderCount(renderCount => renderCount + 1);
  }, [mcpServerEnabledState]);

  const setMCPServerEnabled = (serverId: string, enabled: boolean) => {
    const currentState = new Map(mcpServerEnabledState);
    if (enabled) {
      if (!(serverId in currentState)) {
        currentState.set(
          serverId,
          new Set<string>(
            mcpServersRef.current
              .find((server: any) => server.id === serverId)
              ?.tools.map((tool: any) => tool.name)
          )
        );
      }
    } else {
      currentState.delete(serverId);
    }

    setMCPServerEnabledState(currentState);
  };

  const getMCPServerEnabled = (serverId: string) => {
    return mcpServerEnabledState.has(serverId);
  };

  const getMCPServerToolEnabled = (serverId: string, toolName: string) => {
    return (
      mcpServerEnabledState.has(serverId) &&
      mcpServerEnabledState.get(serverId).has(toolName)
    );
  };

  const setMCPServerToolEnabled = (
    serverId: string,
    toolName: string,
    enabled: boolean
  ) => {
    const currentState = new Map(mcpServerEnabledState);
    const serverState = currentState.get(serverId);
    if (enabled) {
      serverState.add(toolName);
    } else {
      serverState.delete(toolName);
    }

    setMCPServerEnabledState(currentState);
  };

  useEffect(() => {
    NBIAPI.configChanged.connect(() => {
      mcpServersRef.current = nbiConfig.toolConfig.mcpServers;
      mcpServerSettingsRef.current = nbiConfig.mcpServerSettings;
      setRenderCount(renderCount => renderCount + 1);
    });
  }, []);

  return (
    <div className="config-dialog">
      <div className="config-dialog-body">
        <div className="model-config-section">
          <div
            className="model-config-section-header"
            style={{ display: 'flex' }}
          >
            <div style={{ flexGrow: 1 }}>MCP Servers</div>
            <div>
              <button
                className="jp-toast-button jp-mod-small jp-Button"
                onClick={handleReloadMCPServersClick}
              >
                <div className="jp-Dialog-buttonLabel">Reload</div>
              </button>
            </div>
          </div>
          <div className="model-config-section-body">
            {mcpServersRef.current.length === 0 && renderCount > 0 && (
              <div className="model-config-section-row">
                <div className="model-config-section-column">
                  <div>
                    No MCP servers found. Add MCP servers in the configuration
                    file.
                  </div>
                </div>
              </div>
            )}
            {mcpServersRef.current.length > 0 && renderCount > 0 && (
              <div className="model-config-section-row">
                <div className="model-config-section-column">
                  {mcpServersRef.current.map((server: any) => (
                    <div key={server.id}>
                      <div style={{ display: 'flex', alignItems: 'center' }}>
                        <CheckBoxItem
                          header={true}
                          label={server.id}
                          checked={getMCPServerEnabled(server.id)}
                          onClick={() => {
                            setMCPServerEnabled(
                              server.id,
                              !getMCPServerEnabled(server.id)
                            );
                          }}
                        ></CheckBoxItem>
                        <div
                          className={`server-status-indicator ${server.status}`}
                          title={server.status}
                        ></div>
                      </div>
                      {getMCPServerEnabled(server.id) && (
                        <div>
                          {server.tools.length > 0 && (
                            <div className="mcp-server-tools">
                              <div className="mcp-server-tools-header">
                                Tools
                              </div>
                              <div>
                                {server.tools.map((tool: any) => (
                                  <PillItem
                                    label={tool.name}
                                    title={tool.description}
                                    checked={getMCPServerToolEnabled(
                                      server.id,
                                      tool.name
                                    )}
                                    onClick={() => {
                                      setMCPServerToolEnabled(
                                        server.id,
                                        tool.name,
                                        !getMCPServerToolEnabled(
                                          server.id,
                                          tool.name
                                        )
                                      );
                                    }}
                                  ></PillItem>
                                ))}
                              </div>
                            </div>
                          )}
                          {server.prompts.length > 0 && (
                            <div className="mcp-server-prompts">
                              <div className="mcp-server-prompts-header">
                                Prompts
                              </div>
                              <div>
                                {server.prompts.map((prompt: any) => (
                                  <PillItem
                                    label={prompt.name}
                                    title={prompt.description}
                                    checked={true}
                                  ></PillItem>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="model-config-section-row">
              <div
                className="model-config-section-column"
                style={{ flexGrow: 'initial' }}
              >
                <button
                  className="jp-Dialog-button jp-mod-accept jp-mod-styled"
                  style={{ width: 'max-content' }}
                  onClick={props.onEditMCPConfigClicked}
                >
                  <div className="jp-Dialog-buttonLabel">Add / Edit</div>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

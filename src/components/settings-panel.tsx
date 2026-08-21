// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, { useEffect, useRef, useState } from 'react';
import { ReactWidget } from '@jupyterlab/apputils';
import { VscWarning } from '../icons';
import * as path from 'path';

import copySvgstr from '../../style/icons/copy.svg';
import claudeSvgStr from '../../style/icons/claude.svg';
import openaiSvgStr from '../../style/icons/openai.svg';
import {
  ClaudeModelType,
  ClaudeToolType,
  ICellOutputFeatureFlag,
  IClaudeModelInfo,
  NBIAPI
} from '../api';
import { CheckBoxItem } from './checkbox';
import { PillItem } from './pill';
import { mcpServerSettingsToEnabledState } from './mcp-util';
import { SettingsPanelComponentSkills } from './skills-panel';
import { SettingsPanelComponentClaudeMCP } from './claude-mcp-panel';
import { SettingsPanelComponentPlugins } from './plugins-panel';
import { writeTextToClipboard } from '../utils';

const lockedTip = (locked: boolean): string =>
  locked ? 'Locked by your administrator' : '';

// Stable id helper so the tab and its panel agree on aria-controls /
// aria-labelledby without scattering string concatenation through the
// component.
const tabId = (prefix: string, id: string): string => `${prefix}-${id}`;

type TablistOrientation = 'vertical' | 'horizontal';

// WAI-ARIA tablist arrow-key navigation. Same shape for both the
// vertical (Up/Down) main tabs and the horizontal (Left/Right) Claude
// subtabs — the orientation flag picks which keys move the cursor.
// Returns an ``onKeyDown`` for the tablist container; callers decide
// what to do with each id (typically: select + focus).
function useTablistArrowKeys<T extends { id: string }>(
  tabs: T[],
  activeId: string,
  onSelect: (id: string) => void,
  orientation: TablistOrientation,
  domIdFor: (id: string) => string
): (e: React.KeyboardEvent<HTMLDivElement>) => void {
  return (e: React.KeyboardEvent<HTMLDivElement>) => {
    const key = e.key;
    const prevKey = orientation === 'vertical' ? 'ArrowUp' : 'ArrowLeft';
    const nextKey = orientation === 'vertical' ? 'ArrowDown' : 'ArrowRight';
    if (key !== prevKey && key !== nextKey && key !== 'Home' && key !== 'End') {
      return;
    }
    e.preventDefault();
    const idx = tabs.findIndex(t => t.id === activeId);
    let next = idx;
    if (key === nextKey) {
      next = (idx + 1) % tabs.length;
    } else if (key === prevKey) {
      next = (idx - 1 + tabs.length) % tabs.length;
    } else if (key === 'Home') {
      next = 0;
    } else if (key === 'End') {
      next = tabs.length - 1;
    }
    onSelect(tabs[next].id);
    document.getElementById(domIdFor(tabs[next].id))?.focus();
  };
}

// When a boolean policy is locked the panel shows the policy-resolved value;
// otherwise it shows the user's local toggle state.
const checkedValue = (
  policy: ICellOutputFeatureFlag,
  userValue: boolean
): boolean => (policy.locked ? policy.enabled : userValue);

function useNbiPolicies() {
  const [featurePolicies, setFeaturePolicies] = useState(
    NBIAPI.config.featurePolicies
  );
  const [settingLocks, setSettingLocks] = useState(NBIAPI.config.settingLocks);
  useEffect(() => {
    const handler = () => {
      setFeaturePolicies(NBIAPI.config.featurePolicies);
      setSettingLocks(NBIAPI.config.settingLocks);
    };
    NBIAPI.configChanged.connect(handler);
    return () => {
      NBIAPI.configChanged.disconnect(handler);
    };
  }, []);
  return { featurePolicies, settingLocks };
}

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

// Tab declaration. Adding a new tab is one entry here plus an icon
// (optional). The `visible` predicate runs against {featurePolicies,
// isInClaudeCodeMode, isClaudeCliAvailable} so policy / mode changes
// propagate through the registry without wiring extra props.
type TabSpec = {
  id: string;
  label: string;
  icon?: () => JSX.Element;
  visible: (ctx: TabVisibilityContext) => boolean;
  render: (props: any) => JSX.Element;
};
type TabVisibilityContext = {
  featurePolicies: import('../api').IFeaturePolicies;
  isInClaudeCodeMode: boolean;
  isClaudeCliAvailable: boolean;
};

const TABS: TabSpec[] = [
  {
    id: 'general',
    label: 'General',
    visible: () => true,
    render: props => (
      <SettingsPanelComponentGeneral
        onSave={props.onSave}
        onEditMCPConfigClicked={props.onEditMCPConfigClicked}
      />
    )
  },
  {
    id: 'claude',
    label: 'Claude',
    icon: () => (
      <span
        className="claude-icon"
        dangerouslySetInnerHTML={{ __html: claudeSvgStr }}
      ></span>
    ),
    visible: () => true,
    render: props => (
      <SettingsPanelComponentClaude
        onEditMCPConfigClicked={props.onEditMCPConfigClicked}
      />
    )
  },
  {
    id: 'acp',
    label: 'ACP',
    icon: () => (
      <span
        className="acp-agent-icon"
        dangerouslySetInnerHTML={{ __html: openaiSvgStr }}
      ></span>
    ),
    // Hidden unless an admin has unlocked the experimental ACP mode
    // (acp_mode defaults to force-off, which reads as locked + disabled).
    visible: ctx =>
      ctx.featurePolicies.acp_mode.enabled ||
      !ctx.featurePolicies.acp_mode.locked,
    render: () => <SettingsPanelComponentAcp />
  },
  {
    id: 'mcp-servers',
    label: 'MCP Servers',
    visible: ctx => !ctx.isInClaudeCodeMode,
    render: props => (
      <SettingsPanelComponentMCPServers
        onEditMCPConfigClicked={props.onEditMCPConfigClicked}
      />
    )
  },
  {
    id: 'claude-mcp',
    label: 'Claude MCP',
    visible: ctx =>
      ctx.featurePolicies.claude_mcp_management.enabled &&
      ctx.isInClaudeCodeMode &&
      ctx.isClaudeCliAvailable,
    render: () => <SettingsPanelComponentClaudeMCP />
  },
  {
    id: 'plugins',
    label: 'Plugins',
    visible: ctx =>
      ctx.featurePolicies.claude_plugins_management.enabled &&
      ctx.isInClaudeCodeMode &&
      ctx.isClaudeCliAvailable,
    render: () => <SettingsPanelComponentPlugins />
  },
  {
    id: 'skills',
    label: 'Skills',
    visible: ctx => ctx.featurePolicies.skills_management.enabled,
    render: () => <SettingsPanelComponentSkills />
  }
];

function SettingsPanelComponent(props: any) {
  const [activeTab, setActiveTab] = useState('general');
  const { featurePolicies } = useNbiPolicies();
  const [isInClaudeCodeMode, setIsInClaudeCodeMode] = useState(
    NBIAPI.config.isInClaudeCodeMode
  );
  const [isClaudeCliAvailable, setIsClaudeCliAvailable] = useState(
    NBIAPI.config.isClaudeCliAvailable
  );

  useEffect(() => {
    const handler = () => {
      setIsInClaudeCodeMode(NBIAPI.config.isInClaudeCodeMode);
      setIsClaudeCliAvailable(NBIAPI.config.isClaudeCliAvailable);
    };
    NBIAPI.configChanged.connect(handler);
    return () => {
      NBIAPI.configChanged.disconnect(handler);
    };
  }, []);

  const ctx: TabVisibilityContext = {
    featurePolicies,
    isInClaudeCodeMode,
    isClaudeCliAvailable
  };
  const visibleTabs = TABS.filter(t => t.visible(ctx));
  const activeTabSpec = visibleTabs.find(t => t.id === activeTab);

  // Bounce off a tab that just disappeared (admin policy flip, mode toggle).
  useEffect(() => {
    if (!activeTabSpec) {
      setActiveTab('general');
    }
  }, [activeTabSpec]);

  return (
    <div className="nbi-settings-panel">
      <SettingsPanelTabsComponent
        tabs={visibleTabs}
        activeTab={activeTab}
        onTabSelected={setActiveTab}
      />
      <div
        className="nbi-settings-panel-tab-content"
        role="tabpanel"
        id={tabId('nbi-settings-tabpanel', activeTab)}
        aria-labelledby={tabId('nbi-settings-tab', activeTab)}
      >
        {activeTabSpec && activeTabSpec.render(props)}
      </div>
    </div>
  );
}

function SettingsPanelTabsComponent(props: {
  tabs: TabSpec[];
  activeTab: string;
  onTabSelected: (tab: string) => void;
}) {
  const onKeyDown = useTablistArrowKeys(
    props.tabs,
    props.activeTab,
    props.onTabSelected,
    'vertical',
    id => tabId('nbi-settings-tab', id)
  );

  return (
    <div
      className="nbi-settings-panel-tabs"
      role="tablist"
      aria-orientation="vertical"
      aria-label="Settings sections"
      onKeyDown={onKeyDown}
    >
      {props.tabs.map(tab => {
        const selected = tab.id === props.activeTab;
        return (
          <button
            type="button"
            key={tab.id}
            id={tabId('nbi-settings-tab', tab.id)}
            className={`nbi-settings-panel-tab ${selected ? 'active' : ''}`}
            role="tab"
            aria-selected={selected}
            aria-controls={tabId('nbi-settings-tabpanel', tab.id)}
            tabIndex={selected ? 0 : -1}
            onClick={() => props.onTabSelected(tab.id)}
          >
            {tab.icon && tab.icon()}
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

function SettingsPanelComponentGeneral(props: any) {
  const nbiConfig = NBIAPI.config;
  const llmProviders = nbiConfig.llmProviders;
  const [chatModels, setChatModels] = useState([]);
  const [inlineCompletionModels, setInlineCompletionModels] = useState([]);
  const isInClaudeCodeMode = nbiConfig.isInClaudeCodeMode;

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
      },
      inline_completion_debouncer_delay: inlineCompletionDebouncerDelay
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
  const [inlineCompletionDebouncerDelay, setInlineCompletionDebouncerDelay] =
    useState(nbiConfig.inlineCompletionDebouncerDelay);
  const { featurePolicies, settingLocks } = useNbiPolicies();

  const toggleExplainError = () => {
    NBIAPI.setConfig({
      enable_explain_error: !featurePolicies.explain_error.enabled
    });
  };

  const toggleOutputFollowup = () => {
    NBIAPI.setConfig({
      enable_output_followup: !featurePolicies.output_followup.enabled
    });
  };

  const toggleOutputToolbar = () => {
    NBIAPI.setConfig({
      enable_output_toolbar: !featurePolicies.output_toolbar.enabled
    });
  };

  const toggleRefreshOpenFilesOnDiskChange = () => {
    NBIAPI.setConfig({
      refresh_open_files_on_disk_change:
        !featurePolicies.refresh_open_files_on_disk_change.enabled
    });
  };

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
    storeGitHubAccessToken,
    inlineCompletionDebouncerDelay
  ]);

  return (
    <div className="config-dialog">
      <div className="config-dialog-body">
        {!isInClaudeCodeMode && (
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
        )}

        {!isInClaudeCodeMode && (
          <div className="model-config-section">
            <div className="model-config-section-header">Chat model</div>
            <div className="model-config-section-body">
              <div className="model-config-section-row">
                <div className="model-config-section-column">
                  <div>Provider</div>
                  <div
                    title={lockedTip(settingLocks.chat_model_provider.locked)}
                  >
                    <select
                      className="jp-mod-styled"
                      disabled={settingLocks.chat_model_provider.locked}
                      onChange={event =>
                        updateModelOptionsForProvider(
                          event.target.value,
                          'chat'
                        )
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
                          <div
                            title={lockedTip(settingLocks.chat_model_id.locked)}
                          >
                            <select
                              className="jp-mod-styled"
                              disabled={settingLocks.chat_model_id.locked}
                              onChange={event =>
                                setChatModel(event.target.value)
                              }
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
                  {chatModelProvider === 'ollama' &&
                    chatModels.length === 0 && (
                      <div className="ollama-warning-message">
                        No Ollama models found! Make sure{' '}
                        <a href="https://ollama.com/" target="_blank">
                          Ollama
                        </a>{' '}
                        is running and models are downloaded to your computer.{' '}
                        <button
                          type="button"
                          className="link-button"
                          onClick={handleRefreshOllamaModelListClick}
                        >
                          Try again
                        </button>{' '}
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
        )}

        <div className="model-config-section">
          <div className="model-config-section-header">Auto-complete model</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>Provider</div>
                <div
                  title={lockedTip(
                    settingLocks.inline_completion_model_provider.locked
                  )}
                >
                  <select
                    className="jp-mod-styled"
                    disabled={
                      settingLocks.inline_completion_model_provider.locked
                    }
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
                    <div
                      title={lockedTip(
                        settingLocks.inline_completion_model_id.locked
                      )}
                    >
                      <select
                        className="jp-mod-styled"
                        disabled={
                          settingLocks.inline_completion_model_id.locked
                        }
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

        <div className="model-config-section-row" style={{ width: '50%' }}>
          <div className="model-config-section-column">
            <div className="form-field-row" style={{ paddingLeft: '10px' }}>
              <div className="form-field-description">
                Auto-complete debouncer delay (ms)
              </div>
              <input
                name="inline-completion-debouncer-delay-input"
                placeholder="Auto-complete debouncer delay (milliseconds)"
                className="jp-mod-styled"
                spellCheck={false}
                value={inlineCompletionDebouncerDelay}
                type="number"
                onChange={event =>
                  setInlineCompletionDebouncerDelay(Number(event.target.value))
                }
              />
            </div>
          </div>
        </div>

        {!isInClaudeCodeMode &&
          (chatModelProvider === 'github-copilot' ||
            inlineCompletionModelProvider === 'github-copilot') && (
            <div className="model-config-section">
              <div className="model-config-section-header access-token-config-header">
                GitHub Copilot login{' '}
                <a
                  href="https://github.com/plmbr/notebook-intelligence/blob/main/README.md#remembering-github-copilot-login"
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
                    <label
                      title={lockedTip(
                        featurePolicies.store_github_access_token.locked
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checkedValue(
                          featurePolicies.store_github_access_token,
                          storeGitHubAccessToken
                        )}
                        disabled={
                          featurePolicies.store_github_access_token.locked
                        }
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
          <div className="model-config-section-header">
            Cell output features
          </div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <CheckBoxItem
                  label="Explain cell errors"
                  title="Show a 'Troubleshoot errors in output' context-menu item on failed cells"
                  checked={featurePolicies.explain_error.enabled}
                  disabled={featurePolicies.explain_error.locked}
                  tooltip={lockedTip(featurePolicies.explain_error.locked)}
                  onClick={toggleExplainError}
                />
              </div>
            </div>
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <CheckBoxItem
                  label="Ask about cell outputs"
                  title="Right-click a cell output to attach it to the chat"
                  checked={featurePolicies.output_followup.enabled}
                  disabled={featurePolicies.output_followup.locked}
                  tooltip={lockedTip(featurePolicies.output_followup.locked)}
                  onClick={toggleOutputFollowup}
                />
              </div>
            </div>
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <CheckBoxItem
                  label="Show output toolbar"
                  title="Show a hover toolbar over cell outputs with Explain / Ask / Troubleshoot buttons"
                  checked={featurePolicies.output_toolbar.enabled}
                  disabled={featurePolicies.output_toolbar.locked}
                  tooltip={lockedTip(featurePolicies.output_toolbar.locked)}
                  onClick={toggleOutputToolbar}
                />
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">External changes</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <CheckBoxItem
                  label="Refresh open files when changed on disk"
                  title="Automatically reload notebook and file editor tabs when an external process (terminal command, sync client, or AI agent) edits the file. Skipped when the tab has unsaved local edits."
                  checked={
                    featurePolicies.refresh_open_files_on_disk_change.enabled
                  }
                  disabled={
                    featurePolicies.refresh_open_files_on_disk_change.locked
                  }
                  tooltip={lockedTip(
                    featurePolicies.refresh_open_files_on_disk_change.locked
                  )}
                  onClick={toggleRefreshOpenFilesOnDiskChange}
                />
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">Config file path</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <span
                  className="user-code-span"
                  onClick={() => {
                    void writeTextToClipboard(
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
    const handler = () => {
      mcpServersRef.current = nbiConfig.toolConfig.mcpServers;
      mcpServerSettingsRef.current = nbiConfig.mcpServerSettings;
      setRenderCount(renderCount => renderCount + 1);
    };
    NBIAPI.configChanged.connect(handler);
    return () => {
      NBIAPI.configChanged.disconnect(handler);
    };
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

function SettingsPanelComponentClaude(props: any) {
  const nbiConfig = NBIAPI.config;
  const claudeSettingsRef = useRef<any>(nbiConfig.claudeSettings);
  const [_renderCount, setRenderCount] = useState(1);
  const [claudeEnabled, setClaudeEnabled] = useState(
    nbiConfig.claudeSettings?.enabled === true
  );
  const [chatModel, setChatModel] = useState(
    nbiConfig.claudeSettings.chat_model ?? ClaudeModelType.Default
  );
  const [inlineCompletionModel, setInlineCompletionModel] = useState(
    nbiConfig.claudeSettings.inline_completion_model ?? ClaudeModelType.Default
  );
  const [apiKey, setApiKey] = useState(nbiConfig.claudeSettings.api_key ?? '');
  const [baseUrl, setBaseUrl] = useState(
    nbiConfig.claudeSettings.base_url ?? ''
  );
  const [settingSources, setSettingSources] = useState(
    nbiConfig.claudeSettings.setting_sources ?? []
  );
  const [tools, setTools] = useState(
    nbiConfig.claudeSettings.tools ?? [
      ClaudeToolType.ClaudeCodeTools,
      ClaudeToolType.JupyterUITools
    ]
  );
  const [continueConversation, setContinueConversation] = useState(
    nbiConfig.claudeSettings.continue_conversation ?? false
  );
  const [showTurnUsage, setShowTurnUsage] = useState(
    nbiConfig.claudeSettings.show_turn_usage ?? false
  );
  const [claudeModels, setClaudeModels] = useState<IClaudeModelInfo[]>(
    nbiConfig.claudeModels
  );
  const [loadingModels, setLoadingModels] = useState(false);
  const { featurePolicies, settingLocks } = useNbiPolicies();

  useEffect(() => {
    const handler = () => {
      claudeSettingsRef.current = nbiConfig.claudeSettings;
      setClaudeModels(nbiConfig.claudeModels);
      setRenderCount(renderCount => renderCount + 1);
    };
    NBIAPI.configChanged.connect(handler);
    return () => {
      NBIAPI.configChanged.disconnect(handler);
    };
  }, []);

  const refreshClaudeModels = async () => {
    setLoadingModels(true);
    try {
      await NBIAPI.updateClaudeModelList();
      const models = nbiConfig.claudeModels;
      console.log('claude_models after refresh:', models);
      setClaudeModels(models);
    } finally {
      setLoadingModels(false);
    }
  };

  const syncSettingsToServerState = () => {
    NBIAPI.setConfig({
      claude_settings: {
        enabled: claudeEnabled,
        chat_model: chatModel,
        inline_completion_model: inlineCompletionModel,
        api_key: apiKey,
        base_url: baseUrl,
        setting_sources: settingSources,
        tools: tools,
        continue_conversation: continueConversation,
        show_turn_usage: showTurnUsage
      }
    });
  };

  useEffect(() => {
    syncSettingsToServerState();
  }, [
    claudeEnabled,
    chatModel,
    inlineCompletionModel,
    apiKey,
    baseUrl,
    settingSources,
    tools,
    continueConversation,
    showTurnUsage
  ]);

  return (
    <div className="config-dialog claude-mode-config-dialog">
      <div className="config-dialog-body">
        <div className="model-config-section">
          <div className="model-config-section-header">Enable Claude mode</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <span>
                This requires a{' '}
                <a href="https://claude.ai" target="_blank">
                  Claude
                </a>{' '}
                account and{' '}
                <a href="https://code.claude.com/" target="_blank">
                  Claude Code
                </a>{' '}
                installed in your system.
              </span>
            </div>
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>
                  <CheckBoxItem
                    header={true}
                    label="Enable Claude mode"
                    checked={checkedValue(
                      featurePolicies.claude_mode,
                      claudeEnabled
                    )}
                    disabled={featurePolicies.claude_mode.locked}
                    tooltip={lockedTip(featurePolicies.claude_mode.locked)}
                    onClick={() => {
                      setClaudeEnabled(!claudeEnabled);
                    }}
                  ></CheckBoxItem>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div
            className="model-config-section-header"
            style={{ display: 'flex' }}
          >
            <div style={{ flexGrow: 1 }}>Models</div>
            <div>
              <button
                className="jp-toast-button jp-mod-small jp-Button"
                onClick={refreshClaudeModels}
                disabled={loadingModels}
              >
                <div className="jp-Dialog-buttonLabel">
                  {loadingModels ? 'Loading...' : 'Refresh'}
                </div>
              </button>
            </div>
          </div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div id="nbi-claude-chat-model-label">Chat model</div>
                <div title={lockedTip(settingLocks.claude_chat_model.locked)}>
                  <select
                    className="jp-mod-styled"
                    aria-labelledby="nbi-claude-chat-model-label"
                    aria-describedby={
                      settingLocks.claude_chat_model.locked
                        ? 'nbi-claude-chat-model-lock-reason'
                        : undefined
                    }
                    disabled={settingLocks.claude_chat_model.locked}
                    value={chatModel}
                    onChange={event => setChatModel(event.target.value)}
                  >
                    <option value={ClaudeModelType.Default}>
                      Default (recommended)
                    </option>
                    {/* Placeholder for a persisted model id that hasn't
                        landed in `claudeModels` yet (empty cache, no api
                        key, mid-fetch). Rendered just after Default so
                        the active value sits near the top of the list. */}
                    {chatModel !== ClaudeModelType.Default &&
                      !claudeModels.some(m => m.id === chatModel) && (
                        <option key={chatModel} value={chatModel}>
                          {chatModel}
                        </option>
                      )}
                    {claudeModels.map(model => (
                      <option key={model.id} value={model.id}>
                        {model.name}
                      </option>
                    ))}
                  </select>
                  {settingLocks.claude_chat_model.locked && (
                    <span
                      id="nbi-claude-chat-model-lock-reason"
                      className="nbi-sr-only"
                    >
                      Locked by your administrator
                    </span>
                  )}
                </div>
              </div>
              <div className="model-config-section-column">
                <div id="nbi-claude-inline-model-label">
                  Auto-complete model
                </div>
                <div
                  title={lockedTip(
                    settingLocks.claude_inline_completion_model.locked
                  )}
                >
                  <select
                    className="jp-mod-styled"
                    aria-labelledby="nbi-claude-inline-model-label"
                    aria-describedby={
                      settingLocks.claude_inline_completion_model.locked
                        ? 'nbi-claude-inline-model-lock-reason'
                        : undefined
                    }
                    disabled={
                      settingLocks.claude_inline_completion_model.locked
                    }
                    value={inlineCompletionModel}
                    onChange={event =>
                      setInlineCompletionModel(event.target.value)
                    }
                  >
                    <option value={ClaudeModelType.None}>None</option>
                    <option value={ClaudeModelType.Inherit}>
                      Inherit from general settings
                    </option>
                    <option value={ClaudeModelType.Default}>
                      Default (recommended)
                    </option>
                    {![
                      ClaudeModelType.None,
                      ClaudeModelType.Inherit,
                      ClaudeModelType.Default
                    ].includes(inlineCompletionModel as ClaudeModelType) &&
                      !claudeModels.some(
                        m => m.id === inlineCompletionModel
                      ) && (
                        <option
                          key={inlineCompletionModel}
                          value={inlineCompletionModel}
                        >
                          {inlineCompletionModel}
                        </option>
                      )}
                    {claudeModels.map(model => (
                      <option key={model.id} value={model.id}>
                        {model.name}
                      </option>
                    ))}
                  </select>
                  {settingLocks.claude_inline_completion_model.locked && (
                    <span
                      id="nbi-claude-inline-model-lock-reason"
                      className="nbi-sr-only"
                    >
                      Locked by your administrator
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">
            Chat Agent setting sources
          </div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>
                  <CheckBoxItem
                    header={true}
                    label="User"
                    checked={checkedValue(
                      featurePolicies.claude_setting_source_user,
                      settingSources.includes('user')
                    )}
                    disabled={featurePolicies.claude_setting_source_user.locked}
                    tooltip={lockedTip(
                      featurePolicies.claude_setting_source_user.locked
                    )}
                    onClick={() => {
                      setSettingSources(
                        settingSources.includes('user')
                          ? settingSources.filter(
                              (source: string) => source !== 'user'
                            )
                          : [...settingSources, 'user']
                      );
                    }}
                  ></CheckBoxItem>
                </div>
              </div>
              <div className="model-config-section-column">
                <div>
                  <CheckBoxItem
                    header={true}
                    label="Project (Jupyter root directory)"
                    checked={checkedValue(
                      featurePolicies.claude_setting_source_project,
                      settingSources.includes('project')
                    )}
                    disabled={
                      featurePolicies.claude_setting_source_project.locked
                    }
                    tooltip={lockedTip(
                      featurePolicies.claude_setting_source_project.locked
                    )}
                    onClick={() => {
                      setSettingSources(
                        settingSources.includes('project')
                          ? settingSources.filter(
                              (source: string) => source !== 'project'
                            )
                          : [...settingSources, 'project']
                      );
                    }}
                  ></CheckBoxItem>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">Chat Agent tools</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>
                  <CheckBoxItem
                    header={true}
                    label="Claude Code tools"
                    checked={checkedValue(
                      featurePolicies.claude_code_tools,
                      tools.includes(ClaudeToolType.ClaudeCodeTools)
                    )}
                    disabled={true}
                    tooltip={lockedTip(
                      featurePolicies.claude_code_tools.locked
                    )}
                    onClick={() => {
                      setTools(
                        tools.includes(ClaudeToolType.ClaudeCodeTools)
                          ? tools.filter(
                              (tool: string) =>
                                tool !== ClaudeToolType.ClaudeCodeTools
                            )
                          : [...tools, ClaudeToolType.ClaudeCodeTools]
                      );
                    }}
                  ></CheckBoxItem>
                </div>
              </div>
              <div className="model-config-section-column">
                <div>
                  <CheckBoxItem
                    header={true}
                    label="Jupyter UI tools"
                    checked={checkedValue(
                      featurePolicies.claude_jupyter_ui_tools,
                      tools.includes(ClaudeToolType.JupyterUITools)
                    )}
                    disabled={featurePolicies.claude_jupyter_ui_tools.locked}
                    tooltip={lockedTip(
                      featurePolicies.claude_jupyter_ui_tools.locked
                    )}
                    onClick={() => {
                      setTools(
                        tools.includes(ClaudeToolType.JupyterUITools)
                          ? tools.filter(
                              (tool: string) =>
                                tool !== ClaudeToolType.JupyterUITools
                            )
                          : [...tools, ClaudeToolType.JupyterUITools]
                      );
                    }}
                  ></CheckBoxItem>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">
            Conversation History
          </div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>
                  <CheckBoxItem
                    header={true}
                    label="Remember conversation history"
                    checked={checkedValue(
                      featurePolicies.claude_continue_conversation,
                      continueConversation
                    )}
                    disabled={
                      featurePolicies.claude_continue_conversation.locked
                    }
                    tooltip={lockedTip(
                      featurePolicies.claude_continue_conversation.locked
                    )}
                    onClick={() => {
                      setContinueConversation(!continueConversation);
                    }}
                  ></CheckBoxItem>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">Usage footer</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>
                  <CheckBoxItem
                    header={true}
                    label="Show usage after each turn (time / tokens / cost)"
                    title="Adds a small footer under each reply."
                    checked={showTurnUsage}
                    onClick={() => {
                      setShowTurnUsage(!showTurnUsage);
                    }}
                  ></CheckBoxItem>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">Claude account</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div className="form-field-row">
                  <div className="form-field-description">
                    API Key (optional)
                  </div>
                  <input
                    name="chat-model-id-input"
                    placeholder={
                      settingLocks.claude_api_key.locked
                        ? 'Locked by ANTHROPIC_API_KEY'
                        : 'API Key'
                    }
                    className="jp-mod-styled"
                    spellCheck={false}
                    value={settingLocks.claude_api_key.locked ? '' : apiKey}
                    disabled={settingLocks.claude_api_key.locked}
                    title={lockedTip(settingLocks.claude_api_key.locked)}
                    onChange={event => setApiKey(event.target.value)}
                  />
                </div>
                <div className="form-field-row">
                  <div className="form-field-description">
                    Base URL (optional)
                  </div>
                  <input
                    name="chat-model-id-input"
                    placeholder={
                      settingLocks.claude_base_url.locked
                        ? 'Locked by ANTHROPIC_BASE_URL'
                        : 'https://api.anthropic.com'
                    }
                    className="jp-mod-styled"
                    spellCheck={false}
                    value={baseUrl}
                    disabled={settingLocks.claude_base_url.locked}
                    title={lockedTip(settingLocks.claude_base_url.locked)}
                    onChange={event => setBaseUrl(event.target.value)}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SettingsPanelComponentAcp(props: any) {
  const nbiConfig = NBIAPI.config;
  const acpAgents = nbiConfig.acpAgents;
  const [acpEnabled, setAcpEnabled] = useState(
    nbiConfig.acpSettings?.enabled === true
  );
  const [agent, setAgent] = useState(
    nbiConfig.acpSettings.agent ?? acpAgents[0]?.id ?? 'codex'
  );
  const [chatModel, setChatModel] = useState(
    nbiConfig.acpSettings.chat_model ?? ''
  );
  const [apiKey, setApiKey] = useState(nbiConfig.acpSettings.api_key ?? '');
  const [baseUrl, setBaseUrl] = useState(nbiConfig.acpSettings.base_url ?? '');
  const [fullAccess, setFullAccess] = useState(
    nbiConfig.acpSettings.full_access === true
  );
  const { featurePolicies, settingLocks } = useNbiPolicies();

  const syncSettingsToServerState = () => {
    NBIAPI.setConfig({
      acp_settings: {
        enabled: acpEnabled,
        agent: agent,
        chat_model: chatModel,
        api_key: apiKey,
        base_url: baseUrl,
        full_access: fullAccess
      }
    });
  };

  useEffect(() => {
    syncSettingsToServerState();
  }, [acpEnabled, agent, chatModel, apiKey, baseUrl, fullAccess]);

  return (
    <div className="config-dialog acp-mode-config-dialog">
      <div className="config-dialog-body">
        <div className="model-config-section">
          <div className="model-config-section-header">Enable ACP mode</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <span>
                Experimental. Routes chat through an external coding agent over
                the{' '}
                <a
                  href="https://agentclientprotocol.com/"
                  target="_blank"
                  rel="noreferrer"
                >
                  Agent Client Protocol
                </a>
                . Requires <code>npx</code> available on the server. ACP mode
                and Claude mode are mutually exclusive: enabling one turns the
                other off.
              </span>
            </div>
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>
                  <CheckBoxItem
                    header={true}
                    label="Enable ACP mode"
                    checked={checkedValue(featurePolicies.acp_mode, acpEnabled)}
                    disabled={featurePolicies.acp_mode.locked}
                    tooltip={lockedTip(featurePolicies.acp_mode.locked)}
                    onClick={() => {
                      setAcpEnabled(!acpEnabled);
                    }}
                  ></CheckBoxItem>
                </div>
              </div>
            </div>
            <div className="model-config-section-row">
              <span id="acp-full-access-warning" className="config-warning">
                By default the agent asks before anything beyond trusted
                read-only commands. Full access lets it run tools (edits, shell)
                without asking. Content the agent reads can steer what it runs.
              </span>
            </div>
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div>
                  <CheckBoxItem
                    label="Full access (run without asking)"
                    ariaDescribedBy="acp-full-access-warning"
                    checked={checkedValue(
                      featurePolicies.acp_full_access,
                      fullAccess
                    )}
                    disabled={featurePolicies.acp_full_access.locked}
                    tooltip={lockedTip(featurePolicies.acp_full_access.locked)}
                    onClick={() => {
                      setFullAccess(!fullAccess);
                    }}
                  ></CheckBoxItem>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">Agent</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div className="form-field-row">
                  <div
                    className="form-field-description"
                    id="acp-agent-select-label"
                  >
                    Agent type
                  </div>
                  <select
                    name="acp-agent-select"
                    aria-labelledby="acp-agent-select-label"
                    className="jp-mod-styled"
                    value={agent}
                    onChange={event => setAgent(event.target.value)}
                  >
                    {acpAgents.map(a => (
                      <option key={a.id} value={a.id}>
                        {a.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field-row">
                  <div className="form-field-description">
                    Chat model (optional)
                  </div>
                  <input
                    name="acp-chat-model-input"
                    placeholder={
                      settingLocks.acp_chat_model.locked
                        ? 'Locked by NBI_ACP_CHAT_MODEL'
                        : "Agent default (e.g. 'gpt-5-codex')"
                    }
                    className="jp-mod-styled"
                    spellCheck={false}
                    value={settingLocks.acp_chat_model.locked ? '' : chatModel}
                    disabled={settingLocks.acp_chat_model.locked}
                    title={lockedTip(settingLocks.acp_chat_model.locked)}
                    onChange={event => setChatModel(event.target.value)}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="model-config-section">
          <div className="model-config-section-header">Agent account</div>
          <div className="model-config-section-body">
            <div className="model-config-section-row">
              <div className="model-config-section-column">
                <div className="form-field-row">
                  <div className="form-field-description">
                    API Key (optional)
                  </div>
                  <input
                    name="acp-api-key-input"
                    placeholder={
                      settingLocks.acp_api_key.locked
                        ? 'Locked by OPENAI_API_KEY'
                        : 'API Key'
                    }
                    className="jp-mod-styled"
                    spellCheck={false}
                    value={settingLocks.acp_api_key.locked ? '' : apiKey}
                    disabled={settingLocks.acp_api_key.locked}
                    title={lockedTip(settingLocks.acp_api_key.locked)}
                    onChange={event => setApiKey(event.target.value)}
                  />
                </div>
                <div className="form-field-row">
                  <div className="form-field-description">
                    Base URL (optional)
                  </div>
                  <input
                    name="acp-base-url-input"
                    placeholder={
                      settingLocks.acp_base_url.locked
                        ? 'Locked by OPENAI_BASE_URL'
                        : 'https://api.openai.com/v1'
                    }
                    className="jp-mod-styled"
                    spellCheck={false}
                    value={settingLocks.acp_base_url.locked ? '' : baseUrl}
                    disabled={settingLocks.acp_base_url.locked}
                    title={lockedTip(settingLocks.acp_base_url.locked)}
                    onChange={event => setBaseUrl(event.target.value)}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

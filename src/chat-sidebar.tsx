// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, {
  ChangeEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  memo
} from 'react';
import { ReactWidget } from '@jupyterlab/apputils';
import { UUID } from '@lumino/coreutils';

import * as monaco from 'monaco-editor/esm/vs/editor/editor.api.js';

import { NBIAPI, GitHubCopilotLoginStatus } from './api';
import {
  BackendMessageType,
  BuiltinToolsetType,
  ContextType,
  GITHUB_COPILOT_PROVIDER_ID,
  IActiveDocumentInfo,
  ICellContents,
  IChatCompletionResponseEmitter,
  IChatParticipant,
  IContextItem,
  ITelemetryEmitter,
  IToolSelections,
  RequestDataType,
  ResponseStreamDataType,
  TelemetryEventType
} from './tokens';
import { JupyterFrontEnd } from '@jupyterlab/application';
import { LabIcon, closeIcon } from '@jupyterlab/ui-components';
import { MarkdownRenderer as OriginalMarkdownRenderer } from './markdown-renderer';
const MarkdownRenderer = memo(OriginalMarkdownRenderer);

import copySvgstr from '../style/icons/copy.svg';
import copilotSvgstr from '../style/icons/copilot.svg';
import copilotWarningSvgstr from '../style/icons/copilot-warning.svg';
import {
  VscSend,
  VscStopCircle,
  VscEye,
  VscEyeClosed,
  VscTriangleRight,
  VscTriangleDown,
  VscPassFilled,
  VscTools,
  VscTrash
} from 'react-icons/vsc';

import { extractLLMGeneratedCode, isDarkTheme } from './utils';
import { CheckBoxItem } from './components/checkbox';
import { mcpServerSettingsToEnabledState } from './components/mcp-util';
import sendSvgstr from '../style/icons/send.svg';
import { sparkleIcon } from './index';

export enum RunChatCompletionType {
  Chat,
  ExplainThis,
  FixThis,
  GenerateCode,
  ExplainThisOutput,
  TroubleshootThisOutput
}

export interface IRunChatCompletionRequest {
  messageId: string;
  chatId: string;
  type: RunChatCompletionType;
  content: string;
  language?: string;
  filename?: string;
  prefix?: string;
  suffix?: string;
  existingCode?: string;
  additionalContext?: IContextItem[];
  chatMode: string;
  toolSelections?: IToolSelections;
}

export interface IChatSidebarOptions {
  getActiveDocumentInfo: () => IActiveDocumentInfo;
  getActiveSelectionContent: () => string;
  getCurrentCellContents: () => ICellContents;
  openFile: (path: string) => void;
  getApp: () => JupyterFrontEnd;
  getTelemetryEmitter: () => ITelemetryEmitter;
}

export class ChatSidebar extends ReactWidget {
  constructor(options: IChatSidebarOptions) {
    super();

    this._options = options;
    this.node.style.height = '100%';
  }

  render(): JSX.Element {
    return (
      <SidebarComponent
        getActiveDocumentInfo={this._options.getActiveDocumentInfo}
        getActiveSelectionContent={this._options.getActiveSelectionContent}
        getCurrentCellContents={this._options.getCurrentCellContents}
        openFile={this._options.openFile}
        getApp={this._options.getApp}
        getTelemetryEmitter={this._options.getTelemetryEmitter}
      />
    );
  }

  private _options: IChatSidebarOptions;
}

export interface IInlinePromptWidgetOptions {
  prompt: string;
  existingCode: string;
  prefix: string;
  suffix: string;
  language?: string;
  filename?: string;
  onRequestSubmitted: (prompt: string) => void;
  onRequestCancelled: () => void;
  onContentStream: (content: string) => void;
  onContentStreamEnd: () => void;
  onUpdatedCodeChange: (content: string) => void;
  onUpdatedCodeAccepted: () => void;
  onUpdatedCodeAcceptedAndRun: () => void;
  telemetryEmitter: ITelemetryEmitter;
}

const sendIcon = new LabIcon({
  name: 'notebook-intelligence:send-icon',
  svgstr: sendSvgstr
});

export class InlinePromptWidget extends ReactWidget {
  constructor(rect: DOMRect, options: IInlinePromptWidgetOptions) {
    super();

    this.node.classList.add('inline-prompt-widget');
    this.node.style.top = `${rect.top + 32}px`;
    this.node.style.left = `${rect.left + 16}px`;
    this.node.style.width = `${rect.width - 36}px`;
    this.node.style.height = '48px';
    this._options = options;

    this.node.addEventListener('focusout', (event: any) => {
      if (this.node.contains(event.relatedTarget)) {
        return;
      }

      // this._options.onRequestCancelled();
    });
  }

  updatePosition(rect: DOMRect) {
    this.node.style.top = `${rect.top + 32}px`;
    this.node.style.left = `${rect.left + 16}px`;
    this.node.style.width = `${rect.width - 36}px`;
  }

  _onResponse(response: any) {
    if (response.type === BackendMessageType.StreamMessage) {
      const delta = response.data['choices']?.[0]?.['delta'];
      if (!delta) {
        return;
      }
      const responseMessage =
        response.data['choices']?.[0]?.['delta']?.['content'];
      if (!responseMessage) {
        return;
      }
      this._options.onContentStream(responseMessage);
    } else if (response.type === BackendMessageType.StreamEnd) {
      this._options.onContentStreamEnd();
      const timeElapsed =
        (new Date().getTime() - this._requestTime.getTime()) / 1000;
      this._options.telemetryEmitter.emitTelemetryEvent({
        type: TelemetryEventType.InlineChatResponse,
        data: {
          chatModel: {
            provider: NBIAPI.config.chatModel.provider,
            model: NBIAPI.config.chatModel.model
          },
          timeElapsed
        }
      });
    }
  }
  _onRequestCancelled() {
    this._options.onRequestCancelled();
  }
  _onRequestSubmitted(prompt: string) {
    // code update
    if (this._options.existingCode !== '') {
      this.node.style.height = '300px';
    }
    // save the prompt in case of a rerender
    this._options.prompt = prompt;
    this._options.onRequestSubmitted(prompt);
    this._requestTime = new Date();
    this._options.telemetryEmitter.emitTelemetryEvent({
      type: TelemetryEventType.InlineChatRequest,
      data: {
        chatModel: {
          provider: NBIAPI.config.chatModel.provider,
          model: NBIAPI.config.chatModel.model
        },
        prompt: prompt
      }
    });
  }

  render(): JSX.Element {
    return (
      <InlinePopoverComponent
        prompt={this._options.prompt}
        existingCode={this._options.existingCode}
        onRequestSubmitted={this._onRequestSubmitted.bind(this)}
        onRequestCancelled={this._options.onRequestCancelled}
        onResponseEmit={this._onResponse.bind(this)}
        prefix={this._options.prefix}
        suffix={this._options.suffix}
        onUpdatedCodeChange={this._options.onUpdatedCodeChange}
        onUpdatedCodeAccepted={this._options.onUpdatedCodeAccepted}
        onUpdatedCodeAcceptedAndRun={this._options.onUpdatedCodeAcceptedAndRun}
      />
    );
  }

  private _options: IInlinePromptWidgetOptions;
  private _requestTime: Date;
}

export class GitHubCopilotStatusBarItem extends ReactWidget {
  constructor(options: { getApp: () => JupyterFrontEnd }) {
    super();

    this._getApp = options.getApp;
  }

  render(): JSX.Element {
    return <GitHubCopilotStatusComponent getApp={this._getApp} />;
  }

  private _getApp: () => JupyterFrontEnd;
}

export class GitHubCopilotLoginDialogBody extends ReactWidget {
  constructor(options: { onLoggedIn: () => void }) {
    super();

    this._onLoggedIn = options.onLoggedIn;
  }

  render(): JSX.Element {
    return (
      <GitHubCopilotLoginDialogBodyComponent
        onLoggedIn={() => this._onLoggedIn()}
      />
    );
  }

  private _onLoggedIn: () => void;
}

interface IChatMessageContent {
  id: string;
  type: ResponseStreamDataType;
  content: any;
  contentDetail?: any;
  created: Date;
  reasoningContent?: string;
  reasoningFinished?: boolean;
  reasoningTime?: number;
}

interface IChatMessage {
  id: string;
  parentId?: string;
  date: Date;
  from: string; // 'user' | 'copilot';
  contents: IChatMessageContent[];
  notebookLink?: string;
  participant?: IChatParticipant;
}

const answeredForms = new Map<string, string>();

function ChatResponseHTMLFrame(props: any) {
  const iframSrc = useMemo(
    () => URL.createObjectURL(new Blob([props.source], { type: 'text/html' })),
    []
  );
  return (
    <div className="chat-response-html-frame" key={`key-${props.index}`}>
      <iframe
        className="chat-response-html-frame-iframe"
        height={props.height}
        sandbox="allow-scripts"
        src={iframSrc}
      ></iframe>
    </div>
  );
}

// Memoize ChatResponse for performance
function ChatResponse(props: any) {
  const [renderCount, setRenderCount] = useState(0);
  const msg: IChatMessage = props.message;
  // const timestamp = msg.date.toLocaleTimeString('en-US', { hour12: false });

  const openNotebook = (event: any) => {
    const notebookPath = event.target.dataset['ref'];
    props.openFile(notebookPath);
  };

  const markFormConfirmed = (contentId: string) => {
    answeredForms.set(contentId, 'confirmed');
    setRenderCount(prev => prev + 1);
  };
  const markFormCanceled = (contentId: string) => {
    answeredForms.set(contentId, 'canceled');
    setRenderCount(prev => prev + 1);
  };

  const runCommand = (commandId: string, args: any) => {
    props.getApp().commands.execute(commandId, args);
  };

  // group messages by type
  const groupedContents: IChatMessageContent[] = [];
  let lastItemType: ResponseStreamDataType | undefined;

  const extractReasoningContent = (item: IChatMessageContent) => {
    let currentContent = item.content as string;
    if (typeof currentContent !== 'string') {
      return false;
    }

    let reasoningContent = '';
    let reasoningStartTime = new Date();
    const reasoningEndTime = new Date();

    const startPos = currentContent.indexOf('<think>');

    const hasStart = startPos >= 0;
    reasoningStartTime = new Date(item.created);

    if (hasStart) {
      currentContent = currentContent.substring(startPos + 7);
    }

    const endPos = currentContent.indexOf('</think>');
    const hasEnd = endPos >= 0;

    if (hasEnd) {
      reasoningContent += currentContent.substring(0, endPos);
      currentContent = currentContent.substring(endPos + 8);
    } else {
      if (hasStart) {
        reasoningContent += currentContent;
        currentContent = '';
      }
    }

    item.content = currentContent;
    item.reasoningContent = reasoningContent;
    item.reasoningFinished = hasEnd;
    item.reasoningTime =
      (reasoningEndTime.getTime() - reasoningStartTime.getTime()) / 1000;

    return hasStart && !hasEnd; // is thinking
  };

  for (let i = 0; i < msg.contents.length; i++) {
    const item = msg.contents[i];
    if (
      item.type === lastItemType &&
      lastItemType === ResponseStreamDataType.MarkdownPart
    ) {
      const lastItem = groupedContents[groupedContents.length - 1];
      lastItem.content += item.content;
    } else {
      groupedContents.push(structuredClone(item));
      lastItemType = item.type;
    }
  }

  const [thinkingInProgress, setThinkingInProgress] = useState(false);

  for (const item of groupedContents) {
    const isThinking = extractReasoningContent(item);
    if (isThinking && !thinkingInProgress) {
      setThinkingInProgress(true);
    }
  }

  useEffect(() => {
    let intervalId: any = undefined;
    if (thinkingInProgress) {
      intervalId = setInterval(() => {
        setRenderCount(prev => prev + 1);
        setThinkingInProgress(false);
      }, 1000);
    }

    return () => clearInterval(intervalId);
  }, [thinkingInProgress]);

  const onExpandCollapseClick = (event: any) => {
    const parent = event.currentTarget.parentElement;
    if (parent.classList.contains('expanded')) {
      parent.classList.remove('expanded');
    } else {
      parent.classList.add('expanded');
    }
  };

  return (
    <div className={'chat-message-wrapper'}>
      <div
        className={`chat-message chat-message-${msg.from}`}
        data-render-count={renderCount}
      >
        <div className="chat-message-header">
          <div className="chat-message-from">
            {msg.participant?.iconPath && (
              <div
                className={`chat-message-from-icon ${msg.participant?.id === 'default' ? 'chat-message-from-icon-default' : ''} ${isDarkTheme() ? 'dark' : ''}`}
              >
                <img src={msg.participant.iconPath} />
              </div>
            )}
            <div className="chat-message-from-title">
              {msg.from === 'user' ? '' : msg.participant?.name || 'Softie AI'}
            </div>
            <div
              className="chat-message-from-progress"
              style={{
                display: `${props.showGenerating ? 'visible' : 'none'}`
              }}
            >
              <div className="loading-ellipsis">Generating</div>
            </div>
          </div>
          {/*<div className="chat-message-timestamp">{timestamp}</div>*/}
        </div>
        <div className="chat-message-content">
          {groupedContents.map((item, index) => {
            switch (item.type) {
              case ResponseStreamDataType.Markdown:
              case ResponseStreamDataType.MarkdownPart:
                return (
                  <>
                    {item.reasoningContent && (
                      <div className="expandable-content">
                        <div
                          className="expandable-content-title"
                          onClick={(event: any) => onExpandCollapseClick(event)}
                        >
                          <VscTriangleRight className="collapsed-icon"></VscTriangleRight>
                          <VscTriangleDown className="expanded-icon"></VscTriangleDown>{' '}
                          {item.reasoningFinished
                            ? 'Thought'
                            : `Thinking (${Math.floor(item.reasoningTime)} s)`}
                        </div>
                        <div className="expandable-content-text">
                          <MarkdownRenderer
                            key={`key-${index}`}
                            getApp={props.getApp}
                            getActiveDocumentInfo={props.getActiveDocumentInfo}
                          >
                            {item.reasoningContent}
                          </MarkdownRenderer>
                        </div>
                      </div>
                    )}
                    <MarkdownRenderer
                      key={`key-${index}`}
                      getApp={props.getApp}
                      getActiveDocumentInfo={props.getActiveDocumentInfo}
                    >
                      {item.content}
                    </MarkdownRenderer>
                    {item.contentDetail ? (
                      <div className="expandable-content">
                        <div
                          className="expandable-content-title"
                          onClick={(event: any) => onExpandCollapseClick(event)}
                        >
                          <VscTriangleRight className="collapsed-icon"></VscTriangleRight>
                          <VscTriangleDown className="expanded-icon"></VscTriangleDown>{' '}
                          {item.contentDetail.title}
                        </div>
                        <div className="expandable-content-text">
                          <MarkdownRenderer
                            key={`key-${index}`}
                            getApp={props.getApp}
                            getActiveDocumentInfo={props.getActiveDocumentInfo}
                          >
                            {item.contentDetail.content}
                          </MarkdownRenderer>
                        </div>
                      </div>
                    ) : null}
                  </>
                );
              case ResponseStreamDataType.Image:
                return (
                  <div className="chat-response-img" key={`key-${index}`}>
                    <img src={item.content} />
                  </div>
                );
              case ResponseStreamDataType.HTMLFrame:
                return (
                  <ChatResponseHTMLFrame
                    index={index}
                    source={item.content.source}
                    height={item.content.height}
                  />
                );
              case ResponseStreamDataType.Button:
                return (
                  <div className="chat-response-button" key={`key-${index}`}>
                    <button
                      className="jp-Dialog-button jp-mod-accept jp-mod-styled"
                      onClick={() =>
                        runCommand(item.content.commandId, item.content.args)
                      }
                    >
                      <div className="jp-Dialog-buttonLabel">
                        {item.content.title}
                      </div>
                    </button>
                  </div>
                );
              case ResponseStreamDataType.Anchor:
                return (
                  <div className="chat-response-anchor" key={`key-${index}`}>
                    <a href={item.content.uri} target="_blank">
                      {item.content.title}
                    </a>
                  </div>
                );
              case ResponseStreamDataType.Progress:
                // show only if no more message available
                return index === groupedContents.length - 1 ? (
                  <div className="chat-response-progress" key={`key-${index}`}>
                    &#x2713; {item.content}
                  </div>
                ) : null;
              case ResponseStreamDataType.Confirmation:
                return answeredForms.get(item.id) ===
                  'confirmed' ? null : answeredForms.get(item.id) ===
                  'canceled' ? (
                  <div>&#10006; Canceled</div>
                ) : (
                  <div className="chat-confirmation-form" key={`key-${index}`}>
                    {item.content.title ? (
                      <div>
                        <b>{item.content.title}</b>
                      </div>
                    ) : null}
                    {item.content.message ? (
                      <div>{item.content.message}</div>
                    ) : null}
                    <button
                      className="jp-Dialog-button jp-mod-accept jp-mod-styled"
                      onClick={() => {
                        markFormConfirmed(item.id);
                        runCommand(
                          'notebook-intelligence:chat-user-input',
                          item.content.confirmArgs
                        );
                      }}
                    >
                      <div className="jp-Dialog-buttonLabel">
                        {item.content.confirmLabel}
                      </div>
                    </button>
                    <button
                      className="jp-Dialog-button jp-mod-reject jp-mod-styled"
                      onClick={() => {
                        markFormCanceled(item.id);
                        runCommand(
                          'notebook-intelligence:chat-user-input',
                          item.content.cancelArgs
                        );
                      }}
                    >
                      <div className="jp-Dialog-buttonLabel">
                        {item.content.cancelLabel}
                      </div>
                    </button>
                  </div>
                );
            }
            return null;
          })}

          {msg.notebookLink && (
            <a
              className="copilot-generated-notebook-link"
              data-ref={msg.notebookLink}
              onClick={openNotebook}
            >
              open notebook
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
const MemoizedChatResponse = memo(ChatResponse);

async function submitCompletionRequest(
  request: IRunChatCompletionRequest,
  responseEmitter: IChatCompletionResponseEmitter
): Promise<any> {
  switch (request.type) {
    case RunChatCompletionType.Chat:
      return NBIAPI.chatRequest(
        request.messageId,
        request.chatId,
        request.content,
        request.language || 'python',
        request.filename || 'Untitled.ipynb',
        request.additionalContext || [],
        request.chatMode,
        request.toolSelections || {},
        responseEmitter
      );
    case RunChatCompletionType.ExplainThis:
    case RunChatCompletionType.FixThis:
    case RunChatCompletionType.ExplainThisOutput:
    case RunChatCompletionType.TroubleshootThisOutput: {
      return NBIAPI.chatRequest(
        request.messageId,
        request.chatId,
        request.content,
        request.language || 'python',
        request.filename || 'Untitled.ipynb',
        [],
        'ask',
        {},
        responseEmitter
      );
    }
    case RunChatCompletionType.GenerateCode:
      return NBIAPI.generateCode(
        request.chatId,
        request.content,
        request.prefix || '',
        request.suffix || '',
        request.existingCode || '',
        request.language || 'python',
        request.filename || 'Untitled.ipynb',
        responseEmitter
      );
  }
}

function SidebarComponent(props: any) {
  const [chatMessages, setChatMessages] = useState<IChatMessage[]>([]);
  const [prompt, setPrompt] = useState<string>('');
  const [draftPrompt, setDraftPrompt] = useState<string>('');
  const messagesEndRef = useRef<null | HTMLDivElement>(null);
  const [ghLoginStatus, setGHLoginStatus] = useState(
    GitHubCopilotLoginStatus.NotLoggedIn
  );
  const [loginClickCount, _setLoginClickCount] = useState(0);
  const [copilotRequestInProgress, setCopilotRequestInProgress] =
    useState(false);
  const [showPopover, setShowPopover] = useState(false);
  const [originalPrefixes, setOriginalPrefixes] = useState<string[]>([]);
  const [prefixSuggestions, setPrefixSuggestions] = useState<string[]>([]);
  const [selectedPrefixSuggestionIndex, setSelectedPrefixSuggestionIndex] =
    useState(0);
  const promptInputRef = useRef<HTMLTextAreaElement>(null);
  const [promptHistory, setPromptHistory] = useState<string[]>([]);
  // position on prompt history stack
  const [promptHistoryIndex, setPromptHistoryIndex] = useState(0);
  const [chatId, setChatId] = useState(UUID.uuid4());
  const lastMessageId = useRef<string>('');
  const lastRequestTime = useRef<Date>(new Date());
  const [contextOn, setContextOn] = useState(false);
  const [activeDocumentInfo, setActiveDocumentInfo] =
    useState<IActiveDocumentInfo | null>(null);
  const [currentFileContextTitle, setCurrentFileContextTitle] = useState('');
  const telemetryEmitter: ITelemetryEmitter = props.getTelemetryEmitter();
  const [chatMode, setChatMode] = useState(NBIAPI.config.defaultChatMode);

  const [toolSelectionTitle, setToolSelectionTitle] =
    useState('Tool selection');
  const [selectedToolCount, setSelectedToolCount] = useState(0);
  const [notebookExecuteToolSelected, setNotebookExecuteToolSelected] =
    useState(false);

  const [renderCount, setRenderCount] = useState(1);
  const toolConfigRef = useRef({
    builtinToolsets: [
      { id: BuiltinToolsetType.NotebookEdit, name: 'Notebook edit' },
      { id: BuiltinToolsetType.NotebookExecute, name: 'Notebook execute' }
    ],
    mcpServers: [],
    extensions: []
  });
  const mcpServerSettingsRef = useRef(NBIAPI.config.mcpServerSettings);
  const [mcpServerEnabledState, setMCPServerEnabledState] = useState(
    new Map<string, Set<string>>(
      mcpServerSettingsToEnabledState(
        toolConfigRef.current.mcpServers,
        mcpServerSettingsRef.current
      )
    )
  );

  const [showModeTools, setShowModeTools] = useState(false);
  const toolSelectionsInitial: any = {
    builtinToolsets: [BuiltinToolsetType.NotebookEdit],
    mcpServers: {},
    extensions: {}
  };
  const toolSelectionsEmpty: any = {
    builtinToolsets: [],
    mcpServers: {},
    extensions: {}
  };
  const [toolSelections, setToolSelections] = useState(
    structuredClone(toolSelectionsInitial)
  );
  const [hasExtensionTools, setHasExtensionTools] = useState(false);
  const [lastScrollTime, setLastScrollTime] = useState(0);
  const [scrollPending, setScrollPending] = useState(false);

  useEffect(() => {
    NBIAPI.configChanged.connect(() => {
      toolConfigRef.current = NBIAPI.config.toolConfig;
      mcpServerSettingsRef.current = NBIAPI.config.mcpServerSettings;
      const newMcpServerEnabledState = mcpServerSettingsToEnabledState(
        toolConfigRef.current.mcpServers,
        mcpServerSettingsRef.current
      );
      setMCPServerEnabledState(newMcpServerEnabledState);
      setToolSelections(structuredClone(toolSelectionsInitial));
      setRenderCount(renderCount => renderCount + 1);
    });
  }, []);

  useEffect(() => {
    let hasTools = false;
    for (const extension of toolConfigRef.current.extensions) {
      if (extension.toolsets.length > 0) {
        hasTools = true;
        break;
      }
    }
    setHasExtensionTools(hasTools);
  }, [toolConfigRef.current]);

  useEffect(() => {
    const builtinToolSelCount = toolSelections.builtinToolsets.length;
    let mcpServerToolSelCount = 0;
    let extensionToolSelCount = 0;

    for (const serverId in toolSelections.mcpServers) {
      const mcpServerTools = toolSelections.mcpServers[serverId];
      mcpServerToolSelCount += mcpServerTools.length;
    }

    for (const extensionId in toolSelections.extensions) {
      const extensionToolsets = toolSelections.extensions[extensionId];
      for (const toolsetId in extensionToolsets) {
        const toolsetTools = extensionToolsets[toolsetId];
        extensionToolSelCount += toolsetTools.length;
      }
    }

    const typeCounts = [];
    if (builtinToolSelCount > 0) {
      typeCounts.push(`${builtinToolSelCount} built-in`);
    }
    if (mcpServerToolSelCount > 0) {
      typeCounts.push(`${mcpServerToolSelCount} mcp`);
    }
    if (extensionToolSelCount > 0) {
      typeCounts.push(`${extensionToolSelCount} ext`);
    }

    setSelectedToolCount(
      builtinToolSelCount + mcpServerToolSelCount + extensionToolSelCount
    );
    setNotebookExecuteToolSelected(
      toolSelections.builtinToolsets.includes(
        BuiltinToolsetType.NotebookExecute
      )
    );
    setToolSelectionTitle(
      typeCounts.length === 0
        ? 'Tool selection'
        : `Tool selection (${typeCounts.join(', ')})`
    );
  }, [toolSelections]);

  const onClearToolsButtonClicked = () => {
    setToolSelections(toolSelectionsEmpty);
  };

  const getBuiltinToolsetState = (toolsetName: string): boolean => {
    return toolSelections.builtinToolsets.includes(toolsetName);
  };

  const setBuiltinToolsetState = (toolsetName: string, enabled: boolean) => {
    const newConfig = { ...toolSelections };
    if (enabled) {
      if (!toolSelections.builtinToolsets.includes(toolsetName)) {
        newConfig.builtinToolsets.push(toolsetName);
      }
    } else {
      const index = newConfig.builtinToolsets.indexOf(toolsetName);
      if (index !== -1) {
        newConfig.builtinToolsets.splice(index, 1);
      }
    }
    setToolSelections(newConfig);
  };

  const anyMCPServerToolSelected = (id: string) => {
    if (!(id in toolSelections.mcpServers)) {
      return false;
    }

    return toolSelections.mcpServers[id].length > 0;
  };

  const getMCPServerState = (id: string): boolean => {
    if (!(id in toolSelections.mcpServers)) {
      return false;
    }

    const mcpServer = toolConfigRef.current.mcpServers.find(
      server => server.id === id
    );

    const selectedServerTools: string[] = toolSelections.mcpServers[id];

    for (const tool of mcpServer.tools) {
      if (!selectedServerTools.includes(tool.name)) {
        return false;
      }
    }

    return true;
  };

  const onMCPServerClicked = (id: string) => {
    if (anyMCPServerToolSelected(id)) {
      const newConfig = { ...toolSelections };
      delete newConfig.mcpServers[id];
      setToolSelections(newConfig);
    } else {
      const mcpServer = toolConfigRef.current.mcpServers.find(
        server => server.id === id
      );
      const newConfig = { ...toolSelections };
      newConfig.mcpServers[id] = structuredClone(
        mcpServer.tools
          .filter((tool: any) =>
            mcpServerEnabledState.get(mcpServer.id).has(tool.name)
          )
          .map((tool: any) => tool.name)
      );
      setToolSelections(newConfig);
    }
  };

  const getMCPServerToolState = (serverId: string, toolId: string): boolean => {
    if (!(serverId in toolSelections.mcpServers)) {
      return false;
    }

    const selectedServerTools: string[] = toolSelections.mcpServers[serverId];

    return selectedServerTools.includes(toolId);
  };

  const setMCPServerToolState = (
    serverId: string,
    toolId: string,
    checked: boolean
  ) => {
    const newConfig = { ...toolSelections };

    if (checked && !(serverId in newConfig.mcpServers)) {
      newConfig.mcpServers[serverId] = [];
    }

    const selectedServerTools: string[] = newConfig.mcpServers[serverId];

    if (checked) {
      selectedServerTools.push(toolId);
    } else {
      const index = selectedServerTools.indexOf(toolId);
      if (index !== -1) {
        selectedServerTools.splice(index, 1);
      }
    }

    setToolSelections(newConfig);
  };

  // all toolsets and tools of the extension are selected
  const getExtensionState = (extensionId: string): boolean => {
    if (!(extensionId in toolSelections.extensions)) {
      return false;
    }

    const extension = toolConfigRef.current.extensions.find(
      extension => extension.id === extensionId
    );

    for (const toolset of extension.toolsets) {
      if (!getExtensionToolsetState(extensionId, toolset.id)) {
        return false;
      }
    }

    return true;
  };

  const getExtensionToolsetState = (
    extensionId: string,
    toolsetId: string
  ): boolean => {
    if (!(extensionId in toolSelections.extensions)) {
      return false;
    }

    if (!(toolsetId in toolSelections.extensions[extensionId])) {
      return false;
    }

    const extension = toolConfigRef.current.extensions.find(
      ext => ext.id === extensionId
    );
    const extensionToolset = extension.toolsets.find(
      (toolset: any) => toolset.id === toolsetId
    );

    const selectedToolsetTools: string[] =
      toolSelections.extensions[extensionId][toolsetId];

    for (const tool of extensionToolset.tools) {
      if (!selectedToolsetTools.includes(tool.name)) {
        return false;
      }
    }

    return true;
  };

  const anyExtensionToolsetSelected = (extensionId: string) => {
    if (!(extensionId in toolSelections.extensions)) {
      return false;
    }

    return Object.keys(toolSelections.extensions[extensionId]).length > 0;
  };

  const onExtensionClicked = (extensionId: string) => {
    if (anyExtensionToolsetSelected(extensionId)) {
      const newConfig = { ...toolSelections };
      delete newConfig.extensions[extensionId];
      setToolSelections(newConfig);
    } else {
      const newConfig = { ...toolSelections };
      const extension = toolConfigRef.current.extensions.find(
        ext => ext.id === extensionId
      );
      if (extensionId in newConfig.extensions) {
        delete newConfig.extensions[extensionId];
      }
      newConfig.extensions[extensionId] = {};
      for (const toolset of extension.toolsets) {
        newConfig.extensions[extensionId][toolset.id] = structuredClone(
          toolset.tools.map((tool: any) => tool.name)
        );
      }
      setToolSelections(newConfig);
    }
  };

  const anyExtensionToolsetToolSelected = (
    extensionId: string,
    toolsetId: string
  ) => {
    if (!(extensionId in toolSelections.extensions)) {
      return false;
    }

    if (!(toolsetId in toolSelections.extensions[extensionId])) {
      return false;
    }

    return toolSelections.extensions[extensionId][toolsetId].length > 0;
  };

  const onExtensionToolsetClicked = (
    extensionId: string,
    toolsetId: string
  ) => {
    if (anyExtensionToolsetToolSelected(extensionId, toolsetId)) {
      const newConfig = { ...toolSelections };
      if (toolsetId in newConfig.extensions[extensionId]) {
        delete newConfig.extensions[extensionId][toolsetId];
      }
      setToolSelections(newConfig);
    } else {
      const extension = toolConfigRef.current.extensions.find(
        ext => ext.id === extensionId
      );
      const extensionToolset = extension.toolsets.find(
        (toolset: any) => toolset.id === toolsetId
      );
      const newConfig = { ...toolSelections };
      if (!(extensionId in newConfig.extensions)) {
        newConfig.extensions[extensionId] = {};
      }
      newConfig.extensions[extensionId][toolsetId] = structuredClone(
        extensionToolset.tools.map((tool: any) => tool.name)
      );
      setToolSelections(newConfig);
    }
  };

  const getExtensionToolsetToolState = (
    extensionId: string,
    toolsetId: string,
    toolId: string
  ): boolean => {
    if (!(extensionId in toolSelections.extensions)) {
      return false;
    }

    const selectedExtensionToolsets: any =
      toolSelections.extensions[extensionId];

    if (!(toolsetId in selectedExtensionToolsets)) {
      return false;
    }

    const selectedServerTools: string[] = selectedExtensionToolsets[toolsetId];

    return selectedServerTools.includes(toolId);
  };

  const setExtensionToolsetToolState = (
    extensionId: string,
    toolsetId: string,
    toolId: string,
    checked: boolean
  ) => {
    const newConfig = { ...toolSelections };

    if (checked && !(extensionId in newConfig.extensions)) {
      newConfig.extensions[extensionId] = {};
    }

    if (checked && !(toolsetId in newConfig.extensions[extensionId])) {
      newConfig.extensions[extensionId][toolsetId] = [];
    }

    const selectedTools: string[] =
      newConfig.extensions[extensionId][toolsetId];

    if (checked) {
      selectedTools.push(toolId);
    } else {
      const index = selectedTools.indexOf(toolId);
      if (index !== -1) {
        selectedTools.splice(index, 1);
      }
    }

    setToolSelections(newConfig);
  };

  useEffect(() => {
    const prefixes: string[] = [];
    if (chatMode !== 'ask') {
      prefixes.push('/clear');
      setOriginalPrefixes(prefixes);
      setPrefixSuggestions(prefixes);
      return;
    }

    const chatParticipants = NBIAPI.config.chatParticipants;
    for (const participant of chatParticipants) {
      const id = participant.id;
      const commands = participant.commands;
      const participantPrefix = id === 'default' ? '' : `@${id}`;
      if (participantPrefix !== '') {
        prefixes.push(participantPrefix);
      }
      const commandPrefix =
        participantPrefix === '' ? '' : `${participantPrefix} `;
      for (const command of commands) {
        prefixes.push(`${commandPrefix}/${command}`);
      }
    }

    const mcpServers = NBIAPI.config.toolConfig.mcpServers;
    const mcpServerSettings = NBIAPI.config.mcpServerSettings;
    for (const mcpServer of mcpServers) {
      if (mcpServerSettings[mcpServer.id]?.disabled !== true) {
        for (const prompt of mcpServer.prompts) {
          prefixes.push(`/mcp:${mcpServer.id}:${prompt.name}`);
        }
      }
    }

    setOriginalPrefixes(prefixes);
    setPrefixSuggestions(prefixes);
  }, [chatMode, renderCount]);

  useEffect(() => {
    const fetchData = () => {
      setGHLoginStatus(NBIAPI.getLoginStatus());
    };

    fetchData();

    const intervalId = setInterval(fetchData, 1000);

    return () => clearInterval(intervalId);
  }, [loginClickCount]);

  useEffect(() => {
    setSelectedPrefixSuggestionIndex(0);
  }, [prefixSuggestions]);

  const onPromptChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const newPrompt = event.target.value;
    setPrompt(newPrompt);
    const trimmedPrompt = newPrompt.trimStart();
    if (trimmedPrompt === '@' || trimmedPrompt === '/') {
      setShowPopover(true);
      filterPrefixSuggestions(trimmedPrompt);
    } else if (
      trimmedPrompt.startsWith('@') ||
      trimmedPrompt.startsWith('/') ||
      trimmedPrompt === ''
    ) {
      filterPrefixSuggestions(trimmedPrompt);
    } else {
      setShowPopover(false);
    }
  };

  const applyPrefixSuggestion = async (prefix: string) => {
    let mcpArguments = '';
    if (prefix.startsWith('/mcp:')) {
      mcpArguments = ':';
      const serverId = prefix.split(':')[1];
      const promptName = prefix.split(':')[2];
      const promptConfig = NBIAPI.config.getMCPServerPrompt(
        serverId,
        promptName
      );
      if (
        promptConfig &&
        promptConfig.arguments &&
        promptConfig.arguments.length > 0
      ) {
        const result = await props
          .getApp()
          .commands.execute('notebook-intelligence:show-form-input-dialog', {
            title: 'Input Parameters',
            fields: promptConfig.arguments
          });
        const argumentValues: string[] = [];
        for (const argument of promptConfig.arguments) {
          if (result[argument.name] !== undefined) {
            argumentValues.push(`${argument.name}=${result[argument.name]}`);
          }
        }
        mcpArguments = `(${argumentValues.join(', ')}):`;
      }
    }

    if (prefix.includes(prompt)) {
      setPrompt(`${prefix}${mcpArguments} `);
    } else {
      setPrompt(`${prefix} ${prompt}${mcpArguments} `);
    }
    setShowPopover(false);
    promptInputRef.current?.focus();
    setSelectedPrefixSuggestionIndex(0);
  };

  const prefixSuggestionSelected = (event: any) => {
    const prefix = event.target.dataset['value'];
    applyPrefixSuggestion(prefix);
  };

  const handleSubmitStopChatButtonClick = async () => {
    setShowModeTools(false);
    if (!copilotRequestInProgress) {
      handleUserInputSubmit();
    } else {
      handleUserInputCancel();
    }
  };

  /*const handleSettingsButtonClick = async () => {
    setShowModeTools(false);
    props
      .getApp()
      .commands.execute('notebook-intelligence:open-configuration-dialog');
  };*/

  const handleChatToolsButtonClick = async () => {
    if (!showModeTools) {
      NBIAPI.fetchCapabilities().then(() => {
        toolConfigRef.current = NBIAPI.config.toolConfig;
        mcpServerSettingsRef.current = NBIAPI.config.mcpServerSettings;
        const newMcpServerEnabledState = mcpServerSettingsToEnabledState(
          toolConfigRef.current.mcpServers,
          mcpServerSettingsRef.current
        );
        setMCPServerEnabledState(newMcpServerEnabledState);
        setRenderCount(renderCount => renderCount + 1);
      });
    }
    setShowModeTools(!showModeTools);
  };

  const handleUserInputSubmit = async () => {
    setPromptHistoryIndex(promptHistory.length + 1);
    setPromptHistory([...promptHistory, prompt]);
    setShowPopover(false);

    const promptPrefixParts = [];
    const promptParts = prompt.split(' ');
    if (promptParts.length > 1) {
      for (let i = 0; i < Math.min(promptParts.length, 2); i++) {
        const part = promptParts[i];
        if (part.startsWith('@') || part.startsWith('/')) {
          promptPrefixParts.push(part);
        }
      }
    }

    lastMessageId.current = UUID.uuid4();
    lastRequestTime.current = new Date();

    const newList = [
      ...chatMessages,
      {
        id: lastMessageId.current,
        date: new Date(),
        from: 'user',
        contents: [
          {
            id: UUID.uuid4(),
            type: ResponseStreamDataType.Markdown,
            content: prompt,
            created: new Date()
          }
        ]
      }
    ];
    setChatMessages(newList);

    if (prompt.startsWith('/clear')) {
      setChatMessages([]);
      setPrompt('');
      resetChatId();
      resetPrefixSuggestions();
      setPromptHistory([]);
      setPromptHistoryIndex(0);
      NBIAPI.sendWebSocketMessage(
        UUID.uuid4(),
        RequestDataType.ClearChatHistory,
        { chatId }
      );
      return;
    }

    setCopilotRequestInProgress(true);

    const activeDocInfo: IActiveDocumentInfo = props.getActiveDocumentInfo();
    const extractedPrompt = prompt;
    const contents: IChatMessageContent[] = [];
    const app = props.getApp();
    const additionalContext: IContextItem[] = [];
    if (contextOn && activeDocumentInfo?.filename) {
      const selection = activeDocumentInfo.selection;
      const textSelected =
        selection &&
        !(
          selection.start.line === selection.end.line &&
          selection.start.column === selection.end.column
        );
      additionalContext.push({
        type: ContextType.CurrentFile,
        content: props.getActiveSelectionContent(),
        currentCellContents: textSelected
          ? null
          : props.getCurrentCellContents(),
        filePath: activeDocumentInfo.filePath,
        cellIndex: activeDocumentInfo.activeCellIndex,
        startLine: selection ? selection.start.line + 1 : 1,
        endLine: selection ? selection.end.line + 1 : 1
      });
    }

    submitCompletionRequest(
      {
        messageId: lastMessageId.current,
        chatId,
        type: RunChatCompletionType.Chat,
        content: extractedPrompt,
        language: activeDocInfo.language,
        filename: activeDocInfo.filePath,
        additionalContext,
        chatMode,
        toolSelections: toolSelections
      },
      {
        emit: async response => {
          if (response.id !== lastMessageId.current) {
            return;
          }

          let responseMessage = '';
          if (response.type === BackendMessageType.StreamMessage) {
            const delta = response.data['choices']?.[0]?.['delta'];
            if (!delta) {
              return;
            }
            if (delta['nbiContent']) {
              const nbiContent = delta['nbiContent'];
              contents.push({
                id: UUID.uuid4(),
                type: nbiContent.type,
                content: nbiContent.content,
                contentDetail: nbiContent.detail,
                created: new Date(response.created)
              });
            } else {
              responseMessage =
                response.data['choices']?.[0]?.['delta']?.['content'];
              if (!responseMessage) {
                return;
              }
              contents.push({
                id: UUID.uuid4(),
                type: ResponseStreamDataType.MarkdownPart,
                content: responseMessage,
                created: new Date(response.created)
              });
            }
          } else if (response.type === BackendMessageType.StreamEnd) {
            setCopilotRequestInProgress(false);
            const timeElapsed =
              (new Date().getTime() - lastRequestTime.current.getTime()) / 1000;
            telemetryEmitter.emitTelemetryEvent({
              type: TelemetryEventType.ChatResponse,
              data: {
                chatModel: {
                  provider: NBIAPI.config.chatModel.provider,
                  model: NBIAPI.config.chatModel.model
                },
                timeElapsed
              }
            });
          } else if (response.type === BackendMessageType.RunUICommand) {
            const messageId = response.id;
            const result = await app.commands.execute(
              response.data.commandId,
              response.data.args
            );

            const data = {
              callback_id: response.data.callback_id,
              result: result || 'void'
            };

            try {
              JSON.stringify(data);
            } catch (error) {
              data.result = 'Could not serialize the result';
            }

            NBIAPI.sendWebSocketMessage(
              messageId,
              RequestDataType.RunUICommandResponse,
              data
            );
          }
          setChatMessages([
            ...newList,
            {
              id: UUID.uuid4(),
              date: new Date(),
              from: 'copilot',
              contents: contents,
              participant: NBIAPI.config.chatParticipants.find(participant => {
                return participant.id === response.participant;
              })
            }
          ]);
        }
      }
    );

    const newPrompt = '';

    setPrompt(newPrompt);
    filterPrefixSuggestions(newPrompt);

    telemetryEmitter.emitTelemetryEvent({
      type: TelemetryEventType.ChatRequest,
      data: {
        chatMode,
        chatModel: {
          provider: NBIAPI.config.chatModel.provider,
          model: NBIAPI.config.chatModel.model
        },
        prompt: extractedPrompt
      }
    });
  };

  const handleUserInputCancel = async () => {
    NBIAPI.sendWebSocketMessage(
      lastMessageId.current,
      RequestDataType.CancelChatRequest,
      { chatId }
    );

    lastMessageId.current = '';
    setCopilotRequestInProgress(false);
  };

  const filterPrefixSuggestions = (prmpt: string) => {
    const userInput = prmpt.trimStart();
    if (userInput === '') {
      setPrefixSuggestions(originalPrefixes);
    } else {
      setPrefixSuggestions(
        originalPrefixes.filter(prefix => prefix.includes(userInput))
      );
    }
  };

  const resetPrefixSuggestions = () => {
    setPrefixSuggestions(originalPrefixes);
    setSelectedPrefixSuggestionIndex(0);
  };
  const resetChatId = () => {
    setChatId(UUID.uuid4());
  };

  const onPromptKeyDown = async (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter') {
      event.stopPropagation();
      event.preventDefault();
      if (showPopover) {
        applyPrefixSuggestion(prefixSuggestions[selectedPrefixSuggestionIndex]);
        return;
      }

      setSelectedPrefixSuggestionIndex(0);
      handleSubmitStopChatButtonClick();
    } else if (event.key === 'Tab') {
      if (showPopover) {
        event.stopPropagation();
        event.preventDefault();
        applyPrefixSuggestion(prefixSuggestions[selectedPrefixSuggestionIndex]);
        return;
      }
    } else if (event.key === 'Escape') {
      event.stopPropagation();
      event.preventDefault();
      setShowPopover(false);
      setShowModeTools(false);
      setSelectedPrefixSuggestionIndex(0);
    } else if (event.key === 'ArrowUp') {
      event.stopPropagation();
      event.preventDefault();

      if (showPopover) {
        setSelectedPrefixSuggestionIndex(
          (selectedPrefixSuggestionIndex - 1 + prefixSuggestions.length) %
            prefixSuggestions.length
        );
        return;
      }

      setShowPopover(false);
      // first time up key press
      if (
        promptHistory.length > 0 &&
        promptHistoryIndex === promptHistory.length
      ) {
        setDraftPrompt(prompt);
      }

      if (
        promptHistory.length > 0 &&
        promptHistoryIndex > 0 &&
        promptHistoryIndex <= promptHistory.length
      ) {
        const prevPrompt = promptHistory[promptHistoryIndex - 1];
        const newIndex = promptHistoryIndex - 1;
        setPrompt(prevPrompt);
        setPromptHistoryIndex(newIndex);
      }
    } else if (event.key === 'ArrowDown') {
      event.stopPropagation();
      event.preventDefault();

      if (showPopover) {
        setSelectedPrefixSuggestionIndex(
          (selectedPrefixSuggestionIndex + 1 + prefixSuggestions.length) %
            prefixSuggestions.length
        );
        return;
      }

      setShowPopover(false);
      if (
        promptHistory.length > 0 &&
        promptHistoryIndex >= 0 &&
        promptHistoryIndex < promptHistory.length
      ) {
        if (promptHistoryIndex === promptHistory.length - 1) {
          setPrompt(draftPrompt);
          setPromptHistoryIndex(promptHistory.length);
          return;
        }
        const prevPrompt = promptHistory[promptHistoryIndex + 1];
        const newIndex = promptHistoryIndex + 1;
        setPrompt(prevPrompt);
        setPromptHistoryIndex(newIndex);
      }
    }
  };

  // Throttle scrollMessagesToBottom to only scroll every 500ms
  const SCROLL_THROTTLE_TIME = 1000;
  const scrollMessagesToBottom = () => {
    const now = Date.now();
    if (now - lastScrollTime >= SCROLL_THROTTLE_TIME) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
      setLastScrollTime(now);
    } else if (!scrollPending) {
      setScrollPending(true);
      setTimeout(
        () => {
          messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
          setLastScrollTime(Date.now());
          setScrollPending(false);
        },
        SCROLL_THROTTLE_TIME - (now - lastScrollTime)
      );
    }
  };

  const handleConfigurationClick = async () => {
    props
      .getApp()
      .commands.execute('notebook-intelligence:open-configuration-dialog');
  };

  const handleLoginClick = async () => {
    props
      .getApp()
      .commands.execute(
        'notebook-intelligence:open-github-copilot-login-dialog'
      );
  };

  useEffect(() => {
    scrollMessagesToBottom();
  }, [chatMessages]);

  const promptRequestHandler = useCallback(
    (eventData: any) => {
      const request: IRunChatCompletionRequest = eventData.detail;
      request.chatId = chatId;
      let message = '';
      switch (request.type) {
        case RunChatCompletionType.ExplainThis:
          message = `Explain this code:\n\`\`\`\n${request.content}\n\`\`\`\n`;
          break;
        case RunChatCompletionType.FixThis:
          message = `Fix this code:\n\`\`\`\n${request.content}\n\`\`\`\n`;
          break;
        case RunChatCompletionType.ExplainThisOutput:
          message = `Explain this notebook cell output: \n\`\`\`\n${request.content}\n\`\`\`\n`;
          break;
        case RunChatCompletionType.TroubleshootThisOutput:
          message = `Troubleshoot errors reported in the notebook cell output: \n\`\`\`\n${request.content}\n\`\`\`\n`;
          break;
      }
      const messageId = UUID.uuid4();
      request.messageId = messageId;
      const newList = [
        ...chatMessages,
        {
          id: messageId,
          date: new Date(),
          from: 'user',
          contents: [
            {
              id: messageId,
              type: ResponseStreamDataType.Markdown,
              content: message,
              created: new Date()
            }
          ]
        }
      ];
      setChatMessages(newList);

      setCopilotRequestInProgress(true);

      const contents: IChatMessageContent[] = [];

      submitCompletionRequest(request, {
        emit: response => {
          if (response.type === BackendMessageType.StreamMessage) {
            const delta = response.data['choices']?.[0]?.['delta'];
            if (!delta) {
              return;
            }

            const responseMessage =
              response.data['choices']?.[0]?.['delta']?.['content'];
            if (!responseMessage) {
              return;
            }
            contents.push({
              id: response.id,
              type: ResponseStreamDataType.MarkdownPart,
              content: responseMessage,
              created: new Date(response.created)
            });
          } else if (response.type === BackendMessageType.StreamEnd) {
            setCopilotRequestInProgress(false);
          }
          const messageId = UUID.uuid4();
          setChatMessages([
            ...newList,
            {
              id: messageId,
              date: new Date(),
              from: 'copilot',
              contents: contents,
              participant: NBIAPI.config.chatParticipants.find(participant => {
                return participant.id === response.participant;
              })
            }
          ]);
        }
      });
    },
    [chatMessages]
  );

  useEffect(() => {
    document.addEventListener('copilotSidebar:runPrompt', promptRequestHandler);

    return () => {
      document.removeEventListener(
        'copilotSidebar:runPrompt',
        promptRequestHandler
      );
    };
  }, [chatMessages]);

  const activeDocumentChangeHandler = (eventData: any) => {
    // if file changes reset the context toggle
    if (
      eventData.detail.activeDocumentInfo?.filePath !==
      activeDocumentInfo?.filePath
    ) {
      setContextOn(false);
    }
    setActiveDocumentInfo({
      ...eventData.detail.activeDocumentInfo,
      ...{ activeWidget: null }
    });
    setCurrentFileContextTitle(
      getActiveDocumentContextTitle(eventData.detail.activeDocumentInfo)
    );
  };

  useEffect(() => {
    document.addEventListener(
      'copilotSidebar:activeDocumentChanged',
      activeDocumentChangeHandler
    );

    return () => {
      document.removeEventListener(
        'copilotSidebar:activeDocumentChanged',
        activeDocumentChangeHandler
      );
    };
  }, [activeDocumentInfo]);

  const getActiveDocumentContextTitle = (
    activeDocumentInfo: IActiveDocumentInfo
  ): string => {
    if (!activeDocumentInfo?.filename) {
      return '';
    }
    const wholeFile =
      !activeDocumentInfo.selection ||
      (activeDocumentInfo.selection.start.line ===
        activeDocumentInfo.selection.end.line &&
        activeDocumentInfo.selection.start.column ===
          activeDocumentInfo.selection.end.column);
    let cellAndLineIndicator = '';

    if (!wholeFile) {
      if (activeDocumentInfo.filename.endsWith('.ipynb')) {
        cellAndLineIndicator = ` · Cell ${activeDocumentInfo.activeCellIndex + 1}`;
      }
      if (
        activeDocumentInfo.selection.start.line ===
        activeDocumentInfo.selection.end.line
      ) {
        cellAndLineIndicator += `:${activeDocumentInfo.selection.start.line + 1}`;
      } else {
        cellAndLineIndicator += `:${activeDocumentInfo.selection.start.line + 1}-${activeDocumentInfo.selection.end.line + 1}`;
      }
    }

    return `${activeDocumentInfo.filename}${cellAndLineIndicator}`;
  };

  const nbiConfig = NBIAPI.config;
  const getGHLoginRequired = () => {
    return (
      nbiConfig.usingGitHubCopilotModel &&
      NBIAPI.getLoginStatus() === GitHubCopilotLoginStatus.NotLoggedIn
    );
  };
  const getChatEnabled = () => {
    return nbiConfig.chatModel.provider === GITHUB_COPILOT_PROVIDER_ID
      ? !getGHLoginRequired()
      : nbiConfig.llmProviders.find(
          provider => provider.id === nbiConfig.chatModel.provider
        );
  };

  const [ghLoginRequired, setGHLoginRequired] = useState(getGHLoginRequired());
  const [chatEnabled, setChatEnabled] = useState(getChatEnabled());

  useEffect(() => {
    NBIAPI.configChanged.connect(() => {
      setGHLoginRequired(getGHLoginRequired());
      setChatEnabled(getChatEnabled());
    });
  }, []);

  useEffect(() => {
    setGHLoginRequired(getGHLoginRequired());
    setChatEnabled(getChatEnabled());
  }, [ghLoginStatus]);

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-title">
          <LabIcon.resolveReact icon={sparkleIcon} tag="span" />
          <span>Softie AI</span>
        </div>
        {/*<div
          className="user-input-footer-button"
          onClick={() => handleSettingsButtonClick()}
        >
          <VscSettingsGear />
        </div>*/}
      </div>
      {!chatEnabled && !ghLoginRequired && (
        <div className="sidebar-login-info">
          Chat is disabled as you don't have a model configured.
          <button
            className="jp-Dialog-button jp-mod-accept jp-mod-styled"
            onClick={handleConfigurationClick}
          >
            <div className="jp-Dialog-buttonLabel">Configure models</div>
          </button>
        </div>
      )}
      {ghLoginRequired && (
        <div className="sidebar-login-info">
          <div>
            You are not logged in to GitHub Copilot. Please login now to
            activate chat.
          </div>
          <div className="sidebar-login-buttons">
            <button
              className="jp-Dialog-button jp-mod-accept jp-mod-styled"
              onClick={handleLoginClick}
            >
              <div className="jp-Dialog-buttonLabel">
                Login to GitHub Copilot
              </div>
            </button>

            <button
              className="jp-Dialog-button jp-mod-reject jp-mod-styled"
              onClick={handleConfigurationClick}
            >
              <div className="jp-Dialog-buttonLabel">Change provider</div>
            </button>
          </div>
        </div>
      )}

      {chatEnabled &&
        (chatMessages.length === 0 ? (
          <div className="sidebar-messages">
            <div className="sidebar-greeting">
              <span>Welcome! How can I assist you today?</span>
            </div>
          </div>
        ) : (
          <div className="sidebar-messages">
            {chatMessages.map((msg, index) => (
              <MemoizedChatResponse
                key={msg.id}
                message={msg}
                openFile={props.openFile}
                getApp={props.getApp}
                getActiveDocumentInfo={props.getActiveDocumentInfo}
                showGenerating={
                  index === chatMessages.length - 1 &&
                  msg.from === 'copilot' &&
                  copilotRequestInProgress
                }
              />
            ))}
            <div ref={messagesEndRef} />
          </div>
        ))}
      {chatEnabled && (
        <div
          className={`sidebar-user-input ${copilotRequestInProgress ? 'generating' : ''}`}
        >
          <textarea
            ref={promptInputRef}
            rows={3}
            onChange={onPromptChange}
            onKeyDown={onPromptKeyDown}
            placeholder="Ask Softie..."
            spellCheck={false}
            value={prompt}
          />
          <div className="user-input-footer">
            {chatMode === 'ask' && (
              <div>
                <a
                  href="javascript:void(0)"
                  onClick={() => {
                    setShowPopover(true);
                    promptInputRef.current?.focus();
                  }}
                  title="Select chat participant"
                >
                  @
                </a>
              </div>
            )}
            {activeDocumentInfo?.filename && (
              <div className="user-input-context-row">
                <div
                  className={`user-input-context user-input-context-active-file ${contextOn ? 'on' : 'off'}`}
                >
                  <div>{currentFileContextTitle}</div>
                  {contextOn ? (
                    <div
                      className="user-input-context-toggle"
                      onClick={() => setContextOn(!contextOn)}
                    >
                      <VscEye title="Use as context" />
                    </div>
                  ) : (
                    <div
                      className="user-input-context-toggle"
                      onClick={() => setContextOn(!contextOn)}
                    >
                      <VscEyeClosed title="Don't use as context" />
                    </div>
                  )}
                </div>
              </div>
            )}
            <div style={{ flexGrow: 1 }}></div>
            <div className="chat-mode-widgets-container">
              <div>
                <select
                  className="chat-mode-select"
                  title="Chat mode"
                  value={chatMode}
                  onChange={event => {
                    if (event.target.value === 'ask') {
                      setToolSelections(toolSelectionsEmpty);
                    } else if (event.target.value === 'agent') {
                      setToolSelections(structuredClone(toolSelectionsInitial));
                    }
                    setShowModeTools(false);
                    setChatMode(event.target.value);
                  }}
                >
                  <option value="ask">Ask</option>
                  <option value="agent">Agent</option>
                </select>
              </div>
              {chatMode !== 'ask' && (
                <div
                  className={`user-input-footer-button tools-button ${notebookExecuteToolSelected ? 'tools-button-warning' : selectedToolCount > 0 ? 'tools-button-active' : ''}`}
                  onClick={() => handleChatToolsButtonClick()}
                  title={
                    notebookExecuteToolSelected
                      ? `Notebook execute tool selected!\n${toolSelectionTitle}`
                      : toolSelectionTitle
                  }
                >
                  <VscTools />
                  {selectedToolCount > 0 && <>{selectedToolCount}</>}
                </div>
              )}
            </div>
            <div>
              <button
                className="jp-Dialog-button jp-mod-accept jp-mod-styled send-button"
                onClick={() => handleSubmitStopChatButtonClick()}
                disabled={prompt.length === 0 && !copilotRequestInProgress}
              >
                {copilotRequestInProgress ? <VscStopCircle /> : <VscSend />}
              </button>
            </div>
          </div>
          {showPopover && prefixSuggestions.length > 0 && (
            <div className="user-input-autocomplete">
              {prefixSuggestions.map((prefix, index) => (
                <div
                  key={`key-${index}`}
                  className={`user-input-autocomplete-item ${index === selectedPrefixSuggestionIndex ? 'selected' : ''}`}
                  data-value={prefix}
                  onClick={event => prefixSuggestionSelected(event)}
                >
                  {prefix}
                </div>
              ))}
            </div>
          )}
          {showModeTools && (
            <div
              className="mode-tools-popover"
              tabIndex={1}
              autoFocus={true}
              onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
                if (event.key === 'Escape' || event.key === 'Enter') {
                  event.stopPropagation();
                  event.preventDefault();
                  setShowModeTools(false);
                }
              }}
            >
              <div className="mode-tools-popover-header">
                <div className="mode-tools-popover-header-icon">
                  <VscTools />
                </div>
                <div className="mode-tools-popover-title">
                  {toolSelectionTitle}
                </div>
                <div
                  className="mode-tools-popover-clear-tools-button"
                  style={{
                    visibility: selectedToolCount > 0 ? 'visible' : 'hidden'
                  }}
                >
                  <div>
                    <VscTrash />
                  </div>
                  <div>
                    <a
                      href="javascript:void(0);"
                      onClick={onClearToolsButtonClicked}
                    >
                      clear
                    </a>
                  </div>
                </div>
                <div
                  className="mode-tools-popover-close-button"
                  onClick={() => setShowModeTools(false)}
                >
                  {/* <button
                    className="jp-Dialog-button jp-mod-accept jp-mod-styled send-button"
                  > */}
                  <div>
                    <VscPassFilled />
                  </div>
                  {/* </button> */}
                  <div>Done</div>
                </div>
              </div>
              <div className="mode-tools-popover-tool-list">
                <div className="mode-tools-group-header">Built-in</div>
                <div className="mode-tools-group mode-tools-group-built-in">
                  {toolConfigRef.current.builtinToolsets.map((toolset: any) => (
                    <CheckBoxItem
                      key={toolset.id}
                      label={toolset.name}
                      checked={getBuiltinToolsetState(toolset.id)}
                      header={true}
                      onClick={() => {
                        setBuiltinToolsetState(
                          toolset.id,
                          !getBuiltinToolsetState(toolset.id)
                        );
                      }}
                    />
                  ))}
                </div>
                {renderCount > 0 &&
                  mcpServerEnabledState.size > 0 &&
                  toolConfigRef.current.mcpServers.length > 0 && (
                    <div className="mode-tools-group-header">
                      MCP Server Tools
                    </div>
                  )}
                {renderCount > 0 &&
                  toolConfigRef.current.mcpServers
                    .filter(mcpServer =>
                      mcpServerEnabledState.has(mcpServer.id)
                    )
                    .map((mcpServer, index: number) => (
                      <div className="mode-tools-group">
                        <CheckBoxItem
                          label={mcpServer.id}
                          header={true}
                          checked={getMCPServerState(mcpServer.id)}
                          onClick={() => onMCPServerClicked(mcpServer.id)}
                        />
                        {mcpServer.tools
                          .filter((tool: any) =>
                            mcpServerEnabledState
                              .get(mcpServer.id)
                              .has(tool.name)
                          )
                          .map((tool: any, index: number) => (
                            <CheckBoxItem
                              label={tool.name}
                              title={tool.description}
                              indent={1}
                              checked={getMCPServerToolState(
                                mcpServer.id,
                                tool.name
                              )}
                              onClick={() =>
                                setMCPServerToolState(
                                  mcpServer.id,
                                  tool.name,
                                  !getMCPServerToolState(
                                    mcpServer.id,
                                    tool.name
                                  )
                                )
                              }
                            />
                          ))}
                      </div>
                    ))}
                {hasExtensionTools && (
                  <div className="mode-tools-group-header">Extension tools</div>
                )}
                {toolConfigRef.current.extensions.map(
                  (extension, index: number) => (
                    <div className="mode-tools-group">
                      <CheckBoxItem
                        label={`${extension.name} (${extension.id})`}
                        header={true}
                        checked={getExtensionState(extension.id)}
                        onClick={() => onExtensionClicked(extension.id)}
                      />
                      {extension.toolsets.map((toolset: any, index: number) => (
                        <>
                          <CheckBoxItem
                            label={`${toolset.name} (${toolset.id})`}
                            title={toolset.description}
                            indent={1}
                            checked={getExtensionToolsetState(
                              extension.id,
                              toolset.id
                            )}
                            onClick={() =>
                              onExtensionToolsetClicked(
                                extension.id,
                                toolset.id
                              )
                            }
                          />
                          {toolset.tools.map((tool: any, index: number) => (
                            <CheckBoxItem
                              label={tool.name}
                              title={tool.description}
                              indent={2}
                              checked={getExtensionToolsetToolState(
                                extension.id,
                                toolset.id,
                                tool.name
                              )}
                              onClick={() =>
                                setExtensionToolsetToolState(
                                  extension.id,
                                  toolset.id,
                                  tool.name,
                                  !getExtensionToolsetToolState(
                                    extension.id,
                                    toolset.id,
                                    tool.name
                                  )
                                )
                              }
                            />
                          ))}
                        </>
                      ))}
                    </div>
                  )
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InlinePopoverComponent(props: any) {
  const [modifiedCode, setModifiedCode] = useState<string>('');
  const [promptSubmitted, setPromptSubmitted] = useState(false);
  const originalOnRequestSubmitted = props.onRequestSubmitted;
  const originalOnResponseEmit = props.onResponseEmit;

  const onRequestSubmitted = (prompt: string) => {
    setModifiedCode('');
    setPromptSubmitted(true);
    originalOnRequestSubmitted(prompt);
  };

  const onResponseEmit = (response: any) => {
    if (response.type === BackendMessageType.StreamMessage) {
      const delta = response.data['choices']?.[0]?.['delta'];
      if (!delta) {
        return;
      }
      const responseMessage =
        response.data['choices']?.[0]?.['delta']?.['content'];
      if (!responseMessage) {
        return;
      }
      setModifiedCode((modifiedCode: string) => modifiedCode + responseMessage);
    } else if (response.type === BackendMessageType.StreamEnd) {
      setModifiedCode((modifiedCode: string) =>
        extractLLMGeneratedCode(modifiedCode)
      );
    }

    originalOnResponseEmit(response);
  };

  return (
    <div className="inline-popover">
      <div className={'inline-popover-container-wrapper'}>
        <InlinePromptComponent
          {...props}
          onRequestSubmitted={onRequestSubmitted}
          onResponseEmit={onResponseEmit}
          onUpdatedCodeAccepted={props.onUpdatedCodeAccepted}
          onUpdatedCodeAcceptedAndRun={props.onUpdatedCodeAcceptedAndRun}
          limitHeight={props.existingCode !== '' && promptSubmitted}
        />
        <div
          className={'inline-popover-send-icon'}
          onClick={props.onRequestCancelled}
        >
          <LabIcon.resolveReact icon={sendIcon} tag="span" />
        </div>
        <div
          className={'inline-popover-close-icon'}
          onClick={props.onRequestCancelled}
        >
          <LabIcon.resolveReact icon={closeIcon} tag="span" />
        </div>
      </div>
      {props.existingCode !== '' && promptSubmitted && (
        <>
          <InlineDiffViewerComponent {...props} modifiedCode={modifiedCode} />
          <div className="inline-popover-footer">
            <div>
              <button
                className="jp-Button jp-mod-styled jp-mod-small"
                onClick={() => props.onUpdatedCodeAccepted()}
              >
                Accept
              </button>
            </div>
            <div>
              <button
                className="jp-Button jp-mod-reject jp-mod-styled jp-mod-small"
                onClick={() => props.onRequestCancelled()}
              >
                Cancel
              </button>
            </div>
            <div>
              <button
                className="jp-Button jp-mod-reject jp-mod-styled jp-mod-small"
                onClick={() => props.onUpdatedCodeAcceptedAndRun()}
              >
                Accept & Run
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function InlineDiffViewerComponent(props: any) {
  const editorContainerRef = useRef<HTMLDivElement>(null);
  const [diffEditor, setDiffEditor] =
    useState<monaco.editor.IStandaloneDiffEditor>(null);

  useEffect(() => {
    const editorEl = editorContainerRef.current;
    editorEl.className = 'monaco-editor-container';

    const existingModel = monaco.editor.createModel(
      props.existingCode,
      'python'
    );
    const modifiedModel = monaco.editor.createModel(
      props.modifiedCode,
      'python'
    );

    const editor = monaco.editor.createDiffEditor(editorEl, {
      originalEditable: false,
      automaticLayout: true,
      theme: isDarkTheme() ? 'vs-dark' : 'vs'
    });

    monaco.editor.defineTheme('my-diff-theme', {
      base: isDarkTheme() ? 'vs-dark' : 'vs',
      inherit: true,
      rules: [],
      colors: {}
    });

    editor.setModel({
      original: existingModel,
      modified: modifiedModel
    });
    modifiedModel.onDidChangeContent(() => {
      props.onUpdatedCodeChange(modifiedModel.getValue());
    });
    setDiffEditor(editor);
  }, []);

  useEffect(() => {
    diffEditor?.getModifiedEditor().getModel()?.setValue(props.modifiedCode);
  }, [props.modifiedCode]);

  return (
    <div ref={editorContainerRef} className="monaco-editor-container"></div>
  );
}

function InlinePromptComponent(props: any) {
  const [prompt, setPrompt] = useState<string>(props.prompt);
  const promptInputRef = useRef<HTMLTextAreaElement>(null);
  const [inputSubmitted, setInputSubmitted] = useState(false);

  const onPromptChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const newPrompt = event.target.value;
    setPrompt(newPrompt);
  };

  const handleUserInputSubmit = async () => {
    const promptPrefixParts = [];
    const promptParts = prompt.split(' ');
    if (promptParts.length > 1) {
      for (let i = 0; i < Math.min(promptParts.length, 2); i++) {
        const part = promptParts[i];
        if (part.startsWith('@') || part.startsWith('/')) {
          promptPrefixParts.push(part);
        }
      }
    }

    submitCompletionRequest(
      {
        messageId: UUID.uuid4(),
        chatId: UUID.uuid4(),
        type: RunChatCompletionType.GenerateCode,
        content: prompt,
        language: props.language || 'python',
        filename: props.filename || 'Untitled.ipynb',
        prefix: props.prefix,
        suffix: props.suffix,
        existingCode: props.existingCode,
        chatMode: 'ask'
      },
      {
        emit: async response => {
          props.onResponseEmit(response);
        }
      }
    );

    setInputSubmitted(true);
  };

  const onPromptKeyDown = async (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter') {
      event.stopPropagation();
      event.preventDefault();
      if (inputSubmitted && (event.metaKey || event.ctrlKey)) {
        props.onUpdatedCodeAccepted();
      } else {
        props.onRequestSubmitted(prompt);
        handleUserInputSubmit();
      }
    } else if (event.key === 'Escape') {
      event.stopPropagation();
      event.preventDefault();
      props.onRequestCancelled();
    }
  };

  useEffect(() => {
    const input = promptInputRef.current;
    if (input) {
      input.select();
      promptInputRef.current?.focus();
    }
  }, []);

  return (
    <div
      className="inline-prompt-container"
      style={{ height: props.limitHeight ? '40px' : '100%' }}
    >
      <textarea
        ref={promptInputRef}
        rows={1}
        onChange={onPromptChange}
        onKeyDown={onPromptKeyDown}
        placeholder="Ask Softie to generate Python code..."
        spellCheck={false}
        value={prompt}
      />
    </div>
  );
}

function GitHubCopilotStatusComponent(props: any) {
  const [ghLoginStatus, setGHLoginStatus] = useState(
    GitHubCopilotLoginStatus.NotLoggedIn
  );
  const [loginClickCount, _setLoginClickCount] = useState(0);

  useEffect(() => {
    const fetchData = () => {
      setGHLoginStatus(NBIAPI.getLoginStatus());
    };

    fetchData();

    const intervalId = setInterval(fetchData, 1000);

    return () => clearInterval(intervalId);
  }, [loginClickCount]);

  const onStatusClick = () => {
    props
      .getApp()
      .commands.execute(
        'notebook-intelligence:open-github-copilot-login-dialog'
      );
  };

  return (
    <div
      title={`GitHub Copilot: ${ghLoginStatus === GitHubCopilotLoginStatus.LoggedIn ? 'Logged in' : 'Not logged in'}`}
      className="github-copilot-status-bar"
      onClick={() => onStatusClick()}
      dangerouslySetInnerHTML={{
        __html:
          ghLoginStatus === GitHubCopilotLoginStatus.LoggedIn
            ? copilotSvgstr
            : copilotWarningSvgstr
      }}
    ></div>
  );
}

function GitHubCopilotLoginDialogBodyComponent(props: any) {
  const [ghLoginStatus, setGHLoginStatus] = useState(
    GitHubCopilotLoginStatus.NotLoggedIn
  );
  const [loginClickCount, setLoginClickCount] = useState(0);
  const [loginClicked, setLoginClicked] = useState(false);
  const [deviceActivationURL, setDeviceActivationURL] = useState('');
  const [deviceActivationCode, setDeviceActivationCode] = useState('');

  useEffect(() => {
    const fetchData = () => {
      const status = NBIAPI.getLoginStatus();
      setGHLoginStatus(status);
      if (status === GitHubCopilotLoginStatus.LoggedIn && loginClicked) {
        setTimeout(() => {
          props.onLoggedIn();
        }, 1000);
      }
    };

    fetchData();

    const intervalId = setInterval(fetchData, 1000);

    return () => clearInterval(intervalId);
  }, [loginClickCount]);

  const handleLoginClick = async () => {
    const response = await NBIAPI.loginToGitHub();
    setDeviceActivationURL((response as any).verificationURI);
    setDeviceActivationCode((response as any).userCode);
    setLoginClickCount(loginClickCount + 1);
    setLoginClicked(true);
  };

  const handleLogoutClick = async () => {
    await NBIAPI.logoutFromGitHub();
    setLoginClickCount(loginClickCount + 1);
  };

  const loggedIn = ghLoginStatus === GitHubCopilotLoginStatus.LoggedIn;

  return (
    <div className="github-copilot-login-dialog">
      <div className="github-copilot-login-status">
        <h4>
          Login status:{' '}
          <span
            className={`github-copilot-login-status-text ${loggedIn ? 'logged-in' : ''}`}
          >
            {loggedIn
              ? 'Logged in'
              : ghLoginStatus === GitHubCopilotLoginStatus.LoggingIn
                ? 'Logging in...'
                : ghLoginStatus === GitHubCopilotLoginStatus.ActivatingDevice
                  ? 'Activating device...'
                  : ghLoginStatus === GitHubCopilotLoginStatus.NotLoggedIn
                    ? 'Not logged in'
                    : 'Unknown'}
          </span>
        </h4>
      </div>

      {ghLoginStatus === GitHubCopilotLoginStatus.NotLoggedIn && (
        <>
          <div>
            Your code and data are directly transferred to GitHub Copilot as
            needed without storing any copies other than keeping in the process
            memory.
          </div>
          <div>
            <a href="https://github.com/features/copilot" target="_blank">
              GitHub Copilot
            </a>{' '}
            requires a subscription and it has a free tier. GitHub Copilot is
            subject to the{' '}
            <a
              href="https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features"
              target="_blank"
            >
              GitHub Terms for Additional Products and Features
            </a>
            .
          </div>
          <div>
            <h4>Privacy and terms</h4>
            By using Softie with GitHub Copilot subscription you agree to{' '}
            <a
              href="https://docs.github.com/en/copilot/responsible-use-of-github-copilot-features/responsible-use-of-github-copilot-chat-in-your-ide"
              target="_blank"
            >
              GitHub Copilot chat terms
            </a>
            . Review the terms to understand about usage, limitations and ways
            to improve GitHub Copilot. Please review{' '}
            <a
              href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement"
              target="_blank"
            >
              Privacy Statement
            </a>
            .
          </div>
          <div>
            <button
              className="jp-Dialog-button jp-mod-accept jp-mod-reject jp-mod-styled"
              onClick={handleLoginClick}
            >
              <div className="jp-Dialog-buttonLabel">
                Login using your GitHub account
              </div>
            </button>
          </div>
        </>
      )}

      {loggedIn && (
        <div>
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={handleLogoutClick}
          >
            <div className="jp-Dialog-buttonLabel">Logout</div>
          </button>
        </div>
      )}

      {ghLoginStatus === GitHubCopilotLoginStatus.ActivatingDevice &&
        deviceActivationURL &&
        deviceActivationCode && (
          <div>
            <div className="copilot-activation-message">
              Copy code{' '}
              <span
                className="user-code-span"
                onClick={() => {
                  navigator.clipboard.writeText(deviceActivationCode);
                  return true;
                }}
              >
                <b>
                  {deviceActivationCode}{' '}
                  <span
                    className="copy-icon"
                    dangerouslySetInnerHTML={{ __html: copySvgstr }}
                  ></span>
                </b>
              </span>{' '}
              and enter at{' '}
              <a href={deviceActivationURL} target="_blank">
                {deviceActivationURL}
              </a>{' '}
              to allow access to GitHub Copilot from this app. Activation could
              take up to a minute after you enter the code.
            </div>
          </div>
        )}

      {ghLoginStatus === GitHubCopilotLoginStatus.ActivatingDevice && (
        <div style={{ marginTop: '10px' }}>
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={handleLogoutClick}
          >
            <div className="jp-Dialog-buttonLabel">Cancel activation</div>
          </button>
        </div>
      )}
    </div>
  );
}

export class FormInputDialogBody extends ReactWidget {
  constructor(options: { fields: any; onDone: (formData: any) => void }) {
    super();

    this._fields = options.fields || [];
    this._onDone = options.onDone || (() => {});
  }

  render(): JSX.Element {
    return (
      <FormInputDialogBodyComponent
        fields={this._fields}
        onDone={this._onDone}
      />
    );
  }

  private _fields: any;
  private _onDone: (formData: any) => void;
}

function FormInputDialogBodyComponent(props: any) {
  const [formData, setFormData] = useState<any>({});

  const handleInputChange = (event: any) => {
    setFormData({ ...formData, [event.target.name]: event.target.value });
  };

  return (
    <div className="form-input-dialog-body">
      <div className="form-input-dialog-body-content">
        <div className="form-input-dialog-body-content-title">
          {props.title}
        </div>
        <div className="form-input-dialog-body-content-fields">
          {props.fields.map((field: any) => (
            <div
              className="form-input-dialog-body-content-field"
              key={field.name}
            >
              <label
                className="form-input-dialog-body-content-field-label jp-mod-styled"
                htmlFor={field.name}
              >
                {field.name}
                {field.required ? ' (required)' : ''}
              </label>
              <input
                className="form-input-dialog-body-content-field-input jp-mod-styled"
                type={field.type}
                id={field.name}
                name={field.name}
                onChange={handleInputChange}
                value={formData[field.name] || ''}
              />
            </div>
          ))}
        </div>
        <div>
          <div style={{ marginTop: '10px' }}>
            <button
              className="jp-Dialog-button jp-mod-accept jp-mod-styled"
              onClick={() => props.onDone(formData)}
            >
              <div className="jp-Dialog-buttonLabel">Done</div>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

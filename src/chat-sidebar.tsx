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
import { Notification, ReactWidget } from '@jupyterlab/apputils';
import { UUID } from '@lumino/coreutils';

import * as monaco from 'monaco-editor/esm/vs/editor/editor.api.js';

import { NBIAPI, GitHubCopilotLoginStatus } from './api';
import { injectTaskTargetNotebook } from './task-target-notebook';
import {
  formatElapsedSeconds,
  isHeartbeatStale
} from './chat-progress-feedback';
import {
  BackendMessageType,
  BuiltinToolsetType,
  CLAUDE_CODE_CHAT_PARTICIPANT_ID,
  ContextType,
  IActiveDocumentInfo,
  ICellContents,
  IChatCompletionResponseEmitter,
  IChatParticipant,
  IContextItem,
  IOutputContextItem,
  ITelemetryEmitter,
  IToolSelections,
  RequestDataType,
  ResponseStreamDataType,
  TelemetryEventType
} from './tokens';
import { JupyterFrontEnd } from '@jupyterlab/application';
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
  VscAdd,
  VscClose,
  VscHistory,
  VscTriangleRight,
  VscTriangleDown,
  VscSettingsGear,
  VscPassFilled,
  VscTools,
  VscTrash,
  VscThumbsup,
  VscThumbsdown,
  VscThumbsupFilled,
  VscThumbsdownFilled,
  VscCloudUpload,
  VscFile,
  VscRefresh
} from './icons';
import type { Contents } from '@jupyterlab/services';

import {
  extractLLMGeneratedCode,
  isDarkTheme,
  writeTextToClipboard
} from './utils';
import { CheckBoxItem } from './components/checkbox';
import { SafeAnchor } from './components/safe-anchor';
import { mcpServerSettingsToEnabledState } from './components/mcp-util';
import claudeSvgStr from '../style/icons/claude.svg';
import { AskUserQuestion } from './components/ask-user-question';
import { ClaudeSessionPicker } from './components/claude-session-picker';
import {
  BYPASS_PERMISSIONS_MODE,
  nextPermissionModeOnNotification,
  PermissionModeSelect
} from './components/permission-mode-select';
import { ToolCallGroup } from './components/tool-call-group';
import { upsertToolCallContent } from './tool-call-stream';
import { TourOverlay } from './tour/tour-overlay';
import { TOUR_ANCHOR } from './tour/tour-anchors';
import { TOUR_START_EVENT, TOUR_STOP_EVENT } from './tour/tour-events';
import { hasCompletedTour } from './tour/tour-state';
import { IClaudeSessionInfo } from './api';
import {
  NOTEBOOK_GENERATION_PROGRESS_EVENT,
  type INotebookGenerationProgressDetail
} from './notebook-generation';

export enum RunChatCompletionType {
  Chat,
  ExplainThis,
  FixThis,
  GenerateCode,
  NotebookGeneration
}

export interface IRunChatCompletionRequest {
  messageId: string;
  chatId: string;
  type: RunChatCompletionType;
  content: string;
  language?: string;
  kernelName?: string;
  kernelDisplayName?: string;
  currentDirectory?: string;
  filename?: string;
  prefix?: string;
  suffix?: string;
  existingCode?: string;
  additionalContext?: IContextItem[];
  chatMode: string;
  toolSelections?: IToolSelections;
  permissionMode?: string;
  // Optional id used by external listeners (e.g. the notebook toolbar
  // generation popover) to track progress when the chat sidebar is hidden.
  externalRequestId?: string;
  // When true, skip rendering this turn in the chat history. The request
  // still streams through the backend; only the visible transcript is
  // suppressed. Used by the notebook-generation toolbar's "silent" mode.
  hideInChat?: boolean;
}

export interface IChatSidebarOptions {
  getCurrentDirectory: () => string;
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
        getCurrentDirectory={this._options.getCurrentDirectory}
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
  kernelName?: string;
  filename?: string;
  onRequestSubmitted: (prompt: string) => void;
  onRequestCancelled: () => void;
  onContentStream: (content: string) => void;
  // streamError is the backend's structured nbi_stream_error field, set
  // when ClaudeChatModel.completions interrupts mid-stream. Auto-insert
  // callers should skip applying generated content when it is non-null.
  onContentStreamEnd: (streamError?: string | null) => void;
  onUpdatedCodeChange: (content: string) => void;
  onUpdatedCodeAccepted: () => void;
  telemetryEmitter: ITelemetryEmitter;
}

export class InlinePromptWidget extends ReactWidget {
  // Pass `rect` for floating mode (file editor). Pass null for inline mode
  // (notebook cell), where CodeMirror owns the in-flow block placement.
  constructor(rect: DOMRect | null, options: IInlinePromptWidgetOptions) {
    super();

    this.node.classList.add('inline-prompt-widget');
    if (rect) {
      this._floating = true;
      this.node.classList.add('inline-prompt-widget-floating');
      this.node.style.top = `${rect.top + 32}px`;
      this.node.style.left = `${rect.left}px`;
      this.node.style.width = rect.width + 'px';
      this.node.style.height = '48px';
    } else {
      this.node.classList.add('inline-prompt-widget-inline');
      this.node.style.height = '48px';
    }
    this._options = options;

    if (this._floating) {
      this.node.addEventListener('focusout', (event: any) => {
        if (this.node.contains(event.relatedTarget)) {
          return;
        }

        window.setTimeout(() => {
          if (this.node.contains(document.activeElement)) {
            return;
          }
          this._options.onRequestCancelled();
        }, 0);
      });
    }
  }

  updatePosition(rect: DOMRect) {
    if (!this._floating) {
      return;
    }
    this.node.style.top = `${rect.top + 32}px`;
    this.node.style.left = `${rect.left}px`;
    this.node.style.width = rect.width + 'px';
  }

  _onResponse(response: any) {
    if (response.type === BackendMessageType.StreamMessage) {
      // Backend sets nbi_stream_error alongside the [Stream interrupted]
      // marker delta. Capture it so onContentStreamEnd can tell the
      // auto-insert path to skip writing the partial buffer.
      if (typeof response.data?.nbi_stream_error === 'string') {
        this._streamError = response.data.nbi_stream_error;
      }
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
      this._options.onContentStreamEnd(this._streamError);
      this._streamError = null;
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
        language={this._options.language}
        kernelName={this._options.kernelName}
        filename={this._options.filename}
        onUpdatedCodeChange={this._options.onUpdatedCodeChange}
        onUpdatedCodeAccepted={this._options.onUpdatedCodeAccepted}
      />
    );
  }

  private _options: IInlinePromptWidgetOptions;
  private _requestTime: Date;
  private _streamError: string | null = null;
  private _floating = false;
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
  reasoningTag?: string;
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
  feedback?: 'positive' | 'negative';
  chatModel?: { provider: string; model: string };
}

interface IWorkspaceFileOption {
  name: string;
  path: string;
  type: string;
}

interface ISelectedContextFile {
  content: string;
  lineCount: number;
  path: string;
  type: string;
  source?: 'workspace' | 'upload';
  serverPath?: string;
  isImage?: boolean;
  imageDataUrl?: string;
  mimeType?: string;
  outputContext?: IOutputContextItem;
  cellIndex?: number;
  notebookFilename?: string;
}

const MAX_ATTACHED_FILES = 10;

const TEXT_MIME_PREFIXES = [
  'text/',
  'application/json',
  'application/xml',
  'application/x-yaml',
  'application/yaml'
];

const TEXT_EXTENSIONS = new Set([
  '.py',
  '.js',
  '.ts',
  '.tsx',
  '.jsx',
  '.json',
  '.yaml',
  '.yml',
  '.md',
  '.txt',
  '.csv',
  '.html',
  '.css',
  '.sql',
  '.sh',
  '.r',
  '.ipynb',
  '.xml',
  '.toml',
  '.cfg',
  '.ini',
  '.env',
  '.gitignore',
  '.dockerfile',
  '.svg',
  '.rb',
  '.go',
  '.rs',
  '.java',
  '.c',
  '.cpp',
  '.h',
  '.hpp',
  '.swift',
  '.kt',
  '.scala',
  '.lua',
  '.pl',
  '.m',
  '.mm'
]);

function isLikelyTextFile(file: File): boolean {
  if (TEXT_MIME_PREFIXES.some(prefix => file.type.startsWith(prefix))) {
    return true;
  }
  const ext = '.' + file.name.split('.').pop()?.toLowerCase();
  return TEXT_EXTENSIONS.has(ext);
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

const MAX_VISIBLE_WORKSPACE_FILES = 50;
const MAX_WORKSPACE_FILE_SCAN_COUNT = 1500;
const SKIPPED_WORKSPACE_DIRECTORIES = new Set(['__pycache__', 'node_modules']);
// Bounded parallelism for the workspace tree walk. The Jupyter Contents API
// is per-directory, so a directory-heavy workspace with strictly serial
// fetches gets dominated by HTTP roundtrip latency. Eight in flight at a
// time stays well under the server's default tornado handler pool while
// recovering most of the easy speedup; further parallelism is bounded by
// the tree's width and the slowest fetch in each batch.
const WORKSPACE_SCAN_CONCURRENCY = 8;
// Coalesce window for the Contents-API `fileChanged` storm that fires when
// the user (or an agent) creates a directory, drops a folder of files, or
// renames a tree. Without coalescing, one drag-drop of N items would
// schedule N rescans; with a 300ms window every realistic bulk operation
// settles into a single rescan.
const WORKSPACE_FILE_REFRESH_DEBOUNCE_MS = 300;

function countContentLines(content: string): number {
  if (content === '') {
    return 1;
  }

  return content.split('\n').length;
}

function serializeWorkspaceFileContent(model: any): string {
  if (model.type === 'directory') {
    throw new Error('Directories cannot be attached as chat context.');
  }

  if (model.format === 'base64') {
    throw new Error('Binary files cannot be attached as chat context.');
  }

  if (typeof model.content === 'string') {
    return model.content;
  }

  if (model.content === null || model.content === undefined) {
    return '';
  }

  return JSON.stringify(model.content, null, 2);
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
  const shuffledOrder = useRef<number[]>([]);
  const shufflePos = useRef(0);

  const _spinnerVerbs = NBIAPI.config.isInClaudeCodeMode
    ? (NBIAPI.config.spinnerVerbs ?? null)
    : null;
  const hasCustomVerbs =
    _spinnerVerbs?.mode === 'replace' &&
    Array.isArray(_spinnerVerbs.verbs) &&
    _spinnerVerbs.verbs.length > 0;

  // Fisher-Yates shuffle. When `avoidFirst` is set, swap it out of
  // position 0 so the new first verb is never the same as the last shown
  // (prevents an identical repeat at the wrap point between passes).
  const shuffleVerbs = (verbs: any[], avoidFirst?: number): number[] => {
    const order = verbs.map((_, i) => i);
    for (let i = order.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [order[i], order[j]] = [order[j], order[i]];
    }
    if (
      avoidFirst !== undefined &&
      order[0] === avoidFirst &&
      order.length > 1
    ) {
      [order[0], order[1]] = [order[1], order[0]];
    }
    return order;
  };

  // Initialize the shuffle synchronously in useState so the correct verb
  // is shown on the very first paint. A useEffect-based init fires after
  // paint and causes a single-frame flash of verbs[0] before correcting.
  const [verbIndex, setVerbIndex] = useState(() => {
    const sv = NBIAPI.config.isInClaudeCodeMode
      ? (NBIAPI.config.spinnerVerbs ?? null)
      : null;
    if (
      !sv ||
      sv.mode !== 'replace' ||
      !Array.isArray(sv.verbs) ||
      sv.verbs.length === 0
    ) {
      return 0;
    }
    const order = shuffleVerbs(sv.verbs);
    shuffledOrder.current = order;
    shufflePos.current = 0;
    return order[0];
  });

  useEffect(() => {
    if (!props.showGenerating || !hasCustomVerbs) {
      return;
    }

    const verbs = _spinnerVerbs!.verbs;

    // Shuffle already initialized by useState on mount. Only re-initialize
    // if hasCustomVerbs just became true after a capabilities refresh
    // (shuffledOrder would be empty because the lazy init found no verbs).
    if (shuffledOrder.current.length === 0) {
      shuffledOrder.current = shuffleVerbs(verbs);
      shufflePos.current = 0;
      setVerbIndex(shuffledOrder.current[0]);
    }

    let id: ReturnType<typeof setTimeout>;
    const scheduleNext = () => {
      const delay = 4000 + Math.random() * 3000;
      id = setTimeout(() => {
        shufflePos.current++;
        if (shufflePos.current >= shuffledOrder.current.length) {
          const lastShown =
            shuffledOrder.current[shuffledOrder.current.length - 1];
          shuffledOrder.current = shuffleVerbs(verbs, lastShown);
          shufflePos.current = 0;
        }
        setVerbIndex(shuffledOrder.current[shufflePos.current]);
        scheduleNext();
      }, delay);
    };
    scheduleNext();
    return () => clearTimeout(id);
  }, [props.showGenerating, hasCustomVerbs]);

  const msg: IChatMessage = props.message;
  const timestamp = msg.date.toLocaleTimeString('en-US', { hour12: false });

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
  const responseDetailTags = [
    '<think>',
    '</think>',
    '<terminal-output>',
    '</terminal-output>'
  ];

  const extractReasoningContent = (item: IChatMessageContent) => {
    let currentContent = item.content as string;
    if (typeof currentContent !== 'string') {
      return item.reasoningContent && !item.reasoningFinished;
    }

    let reasoningContent = '';
    const reasoningStartTime = new Date(item.created);
    const reasoningEndTime = new Date();

    let startPos = -1;
    let startTag = '';
    for (const tag of responseDetailTags) {
      startPos = currentContent.indexOf(tag);
      if (startPos >= 0) {
        startTag = tag;
        break;
      }
    }

    const hasStart = startPos >= 0;

    if (hasStart) {
      currentContent = currentContent.substring(startPos + startTag.length);
    }

    let endPos = -1;
    let endTag = '';
    for (const tag of responseDetailTags) {
      endPos = currentContent.indexOf(tag);
      if (endPos >= 0) {
        endTag = tag;
        break;
      }
    }
    const hasEnd = endPos >= 0;

    if (hasEnd) {
      reasoningContent += currentContent.substring(0, endPos);
      currentContent = currentContent.substring(endPos + endTag.length);
    } else {
      if (hasStart) {
        reasoningContent += currentContent;
        currentContent = '';
      }
    }

    if (hasStart) {
      item.content = currentContent;
      item.reasoningTag = startTag;
      item.reasoningContent = (item.reasoningContent || '') + reasoningContent;
      item.reasoningFinished = hasEnd;
    }

    if (item.reasoningContent) {
      item.reasoningTime =
        (reasoningEndTime.getTime() - reasoningStartTime.getTime()) / 1000;
    }

    return hasStart && !hasEnd; // is thinking extracted now
  };

  for (let i = 0; i < msg.contents.length; i++) {
    const item = msg.contents[i];
    if (
      item.type === lastItemType &&
      lastItemType === ResponseStreamDataType.MarkdownPart
    ) {
      const lastItem = groupedContents[groupedContents.length - 1];
      lastItem.content += item.content || '';
      if (item.reasoningContent) {
        lastItem.reasoningContent =
          (lastItem.reasoningContent || '') + item.reasoningContent;
      }
      if (item.reasoningFinished) {
        lastItem.reasoningFinished = true;
      }
    } else if (
      item.type === ResponseStreamDataType.ToolCall &&
      lastItemType === ResponseStreamDataType.ToolCall
    ) {
      // Bundle a run of consecutive tool calls into one group item so the
      // renderer can collapse them; ToolCallGroup unwraps content.toolCalls.
      const lastItem = groupedContents[groupedContents.length - 1];
      lastItem.content.toolCalls.push(structuredClone(item.content));
    } else if (item.type === ResponseStreamDataType.ToolCall) {
      const grouped = structuredClone(item);
      grouped.content = { toolCalls: [structuredClone(item.content)] };
      groupedContents.push(grouped);
      lastItemType = item.type;
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

  const getReasoningTitle = (item: IChatMessageContent) => {
    if (item.reasoningTag === '<terminal-output>') {
      return item.reasoningFinished
        ? 'Output'
        : `Running (${Math.floor(item.reasoningTime)} s)`;
    }
    return item.reasoningFinished
      ? 'Thought'
      : `Thinking (${Math.floor(item.reasoningTime)} s)`;
  };

  const chatParticipantId = msg.participant?.id || 'default';

  return (
    <div
      className={`chat-message chat-message-${msg.from}`}
      data-render-count={renderCount}
    >
      <div className="chat-message-header">
        <div className="chat-message-from">
          {msg.participant?.iconPath && (
            <div
              className={`chat-message-from-icon chat-message-from-icon-${chatParticipantId} ${isDarkTheme() ? 'dark' : ''}`}
            >
              <img src={msg.participant.iconPath} alt="" />
            </div>
          )}
          <div className="chat-message-from-title">
            {msg.from === 'user'
              ? 'User'
              : msg.participant?.name || 'AI Assistant'}
          </div>
          <div
            className="chat-message-from-progress"
            style={{ display: `${props.showGenerating ? 'visible' : 'none'}` }}
          >
            <span
              // Key on the heartbeat tick so React re-mounts the dot on
              // every beat; CSS-animation restart from an attribute-only
              // change is not reliable across browsers.
              key={props.heartbeatTick}
              className={`generating-pulse${
                props.isStalled ? ' is-stalled' : ''
              }`}
              aria-hidden="true"
            />
            <div className="generating-label" aria-hidden="true">
              {props.isStalled
                ? 'Still working, server may be slow'
                : hasCustomVerbs
                  ? _spinnerVerbs!.verbs[verbIndex]
                  : 'Generating'}
              {props.showGenerating && props.elapsedSeconds > 0
                ? ` (${formatElapsedSeconds(props.elapsedSeconds)})`
                : ''}
            </div>
            {/* aria-live region contains only the verb — no elapsed suffix —
                so screen readers announce only on verb changes, not on every
                elapsed-seconds tick. */}
            <div className="nbi-sr-only" aria-live="polite" aria-atomic="true">
              {props.isStalled
                ? 'Still working, server may be slow'
                : hasCustomVerbs
                  ? _spinnerVerbs!.verbs[verbIndex]
                  : 'Generating'}
            </div>
          </div>
        </div>
        <div className="chat-message-timestamp">{timestamp}</div>
      </div>
      <div className="chat-message-content">
        {groupedContents.map((item, index) => {
          switch (item.type) {
            case ResponseStreamDataType.Markdown:
            case ResponseStreamDataType.MarkdownPart:
              return (
                <>
                  {item.reasoningContent &&
                    typeof item.reasoningContent === 'string' && (
                      <div
                        className={`expandable-content ${!item.reasoningFinished ? 'expanded' : ''}`}
                      >
                        <button
                          type="button"
                          className="expandable-content-title"
                          onClick={(event: any) => onExpandCollapseClick(event)}
                          aria-expanded={!item.reasoningFinished}
                        >
                          <VscTriangleRight
                            className="collapsed-icon"
                            aria-hidden="true"
                          />
                          <VscTriangleDown
                            className="expanded-icon"
                            aria-hidden="true"
                          />{' '}
                          {getReasoningTitle(item)}
                        </button>
                        <div className="expandable-content-text">
                          <MarkdownRenderer
                            key={`reasoning-${index}`}
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
                    {/* fix for newlines in user input; scoped to user
                        messages only so it doesn't inject trailing
                        whitespace into AI-streamed fenced code blocks */}
                    {msg.from === 'user'
                      ? item.content.replace(/\n/gi, '  \n')
                      : item.content}
                  </MarkdownRenderer>
                  {item.contentDetail ? (
                    <div className="expandable-content expanded">
                      <button
                        type="button"
                        className="expandable-content-title"
                        onClick={(event: any) => onExpandCollapseClick(event)}
                        aria-expanded={true}
                      >
                        <VscTriangleRight
                          className="collapsed-icon"
                          aria-hidden="true"
                        />
                        <VscTriangleDown
                          className="expanded-icon"
                          aria-hidden="true"
                        />{' '}
                        {item.contentDetail.title}
                      </button>
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
                  <img src={item.content} alt="Chat response image" />
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
            case ResponseStreamDataType.Anchor: {
              return (
                <div className="chat-response-anchor" key={`key-${index}`}>
                  <SafeAnchor href={item.content.uri}>
                    {item.content.title}
                  </SafeAnchor>
                </div>
              );
            }
            case ResponseStreamDataType.Progress:
              // Render only the most recent progress entry, and only while
              // the request is still in flight — once the assistant has
              // finished, transient activity markers disappear. The icon
              // is part of the streamed text so backend callers can pick
              // an appropriate symbol (e.g. ↻ for in-progress, ✓ for done,
              // ✗ for error) rather than forcing a single rendering here.
              return index === groupedContents.length - 1 &&
                props.showGenerating ? (
                <div className="chat-response-progress" key={`key-${index}`}>
                  {item.content}
                </div>
              ) : null;
            case ResponseStreamDataType.ToolCall:
              // Unlike Progress, tool-call cards persist after the turn ends,
              // so they render regardless of `showGenerating`. The grouping
              // pass bundled this run's calls into content.toolCalls; the
              // group renders a single card or a collapsible group.
              //
              // Key by the group item's own id (assigned once when the run's
              // first call streamed in) so the group's identity follows its
              // content, not its array position. An index key would remount the
              // group (re-running its collapse heuristic) if anything were ever
              // inserted ahead of it; belt-and-suspenders with the stable
              // message key that is the primary #363 fix.
              return (
                <ToolCallGroup
                  key={item.id}
                  toolCalls={item.content.toolCalls}
                />
              );
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
                  {item.content.confirmSessionArgs ? (
                    <button
                      className="jp-Dialog-button jp-mod-accept jp-mod-styled"
                      onClick={() => {
                        markFormConfirmed(item.id);
                        runCommand(
                          'notebook-intelligence:chat-user-input',
                          item.content.confirmSessionArgs
                        );
                      }}
                    >
                      <div className="jp-Dialog-buttonLabel">
                        {item.content.confirmSessionLabel}
                      </div>
                    </button>
                  ) : null}
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
            case ResponseStreamDataType.AskUserQuestion:
              return answeredForms.get(item.id) ===
                'confirmed' ? null : answeredForms.get(item.id) ===
                'canceled' ? (
                <div>&#10006; Canceled</div>
              ) : (
                <div
                  className="chat-confirmation-form ask-user-question"
                  key={`key-${index}`}
                >
                  <AskUserQuestion
                    userQuestions={item}
                    onSubmit={(selectedAnswers: any) => {
                      markFormConfirmed(item.id);
                      runCommand('notebook-intelligence:chat-user-input', {
                        id: item.content.identifier.id,
                        data: {
                          callback_id: item.content.identifier.callback_id,
                          data: {
                            confirmed: true,
                            selectedAnswers
                          }
                        }
                      });
                    }}
                    onCancel={() => {
                      markFormCanceled(item.id);
                      runCommand('notebook-intelligence:chat-user-input', {
                        id: item.content.identifier.id,
                        data: {
                          callback_id: item.content.identifier.callback_id,
                          data: { confirmed: false }
                        }
                      });
                    }}
                  />
                </div>
              );
          }
          return null;
        })}

        {msg.notebookLink && (
          <button
            type="button"
            className="copilot-generated-notebook-link"
            data-ref={msg.notebookLink}
            aria-label={`Open notebook ${msg.notebookLink}`}
            onClick={openNotebook}
          >
            open notebook
          </button>
        )}
      </div>
      {msg.from === 'copilot' &&
        (NBIAPI.config.chatFeedbackAlwaysVisible || !props.showGenerating) &&
        NBIAPI.config.chatFeedbackEnabled && (
          <div
            className={`chat-message-feedback${
              NBIAPI.config.chatFeedbackAlwaysVisible ? ' always-visible' : ''
            }`}
          >
            <button
              className={`chat-feedback-btn ${msg.feedback === 'positive' ? 'selected' : ''}`}
              onClick={() => {
                props.onFeedback(msg.id, 'positive');
                if (msg.feedback !== 'positive') {
                  props.telemetryEmitter.emitTelemetryEvent({
                    type: TelemetryEventType.Feedback,
                    data: {
                      sentiment: 'positive',
                      chatId: props.chatId,
                      messageId: msg.id,
                      model: msg.chatModel,
                      participant: msg.participant?.id,
                      timestamp: new Date().toISOString()
                    }
                  });
                }
              }}
              aria-label="Rate response as good"
              aria-pressed={msg.feedback === 'positive'}
              title="Good response"
            >
              {msg.feedback === 'positive' ? (
                <VscThumbsupFilled />
              ) : (
                <VscThumbsup />
              )}
            </button>
            <button
              className={`chat-feedback-btn ${msg.feedback === 'negative' ? 'selected' : ''}`}
              onClick={() => {
                props.onFeedback(msg.id, 'negative');
                if (msg.feedback !== 'negative') {
                  props.telemetryEmitter.emitTelemetryEvent({
                    type: TelemetryEventType.Feedback,
                    data: {
                      sentiment: 'negative',
                      chatId: props.chatId,
                      messageId: msg.id,
                      model: msg.chatModel,
                      participant: msg.participant?.id,
                      timestamp: new Date().toISOString()
                    }
                  });
                }
              }}
              aria-label="Rate response as bad"
              aria-pressed={msg.feedback === 'negative'}
              title="Bad response"
            >
              {msg.feedback === 'negative' ? (
                <VscThumbsdownFilled />
              ) : (
                <VscThumbsdown />
              )}
            </button>
          </div>
        )}
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
    case RunChatCompletionType.NotebookGeneration:
      return NBIAPI.chatRequest(
        request.messageId,
        request.chatId,
        request.content,
        request.language || 'python',
        request.kernelName || '',
        request.kernelDisplayName || '',
        request.currentDirectory || '',
        request.filename || '',
        request.additionalContext || [],
        request.chatMode,
        request.toolSelections || {},
        responseEmitter,
        request.permissionMode || 'default'
      );
    case RunChatCompletionType.ExplainThis:
    case RunChatCompletionType.FixThis: {
      return NBIAPI.chatRequest(
        request.messageId,
        request.chatId,
        request.content,
        request.language || 'python',
        request.kernelName || '',
        request.kernelDisplayName || '',
        request.currentDirectory || '',
        request.filename || '',
        [],
        'ask',
        {},
        responseEmitter
      );
    }
    case RunChatCompletionType.GenerateCode:
      return NBIAPI.generateCode(
        request.messageId,
        request.chatId,
        request.content,
        request.prefix || '',
        request.suffix || '',
        request.existingCode || '',
        request.language || 'python',
        request.kernelName || '',
        request.filename || '',
        responseEmitter
      );
  }
}

function getActiveChatModel(): { provider: string; model: string } {
  if (NBIAPI.config.isInClaudeCodeMode) {
    return {
      provider: 'anthropic',
      model: NBIAPI.config.claudeSettings?.chat_model?.trim() || 'default'
    };
  }
  return {
    provider: NBIAPI.config.chatModel.provider,
    model: NBIAPI.config.chatModel.model
  };
}

function SidebarComponent(props: any) {
  const [chatMessages, setChatMessages] = useState<IChatMessage[]>([]);
  const [prompt, setPrompt] = useState<string>('');
  const [draftPrompt, setDraftPrompt] = useState<string>('');
  // The first-run tour is rendered inside the sidebar so it can anchor
  // to DOM elements that only exist when the sidebar is mounted. Auto-
  // show once per browser (state in localStorage); the JupyterLab
  // command `notebook-intelligence:show-tour` dispatches the start
  // event so the same component handles both flows.
  const [tourVisible, setTourVisible] = useState<boolean>(false);
  const sidebarRootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (hasCompletedTour()) {
      return;
    }
    // The sidebar React tree mounts even when the Lumino panel is
    // hidden (lm-mod-hidden). Firing the tour while hidden anchors
    // resolve to 0x0 rects which clamps the tooltip into the viewport
    // corner over unrelated UI. Poll on each animation frame until the
    // root is actually laid out, then fire. Cap the poll so a sidebar
    // that's never opened doesn't keep an rAF tick warm for the whole
    // session.
    let cancelled = false;
    let rafId = 0;
    let attempts = 0;
    const MAX_ATTEMPTS = 1800; // ~30s at 60Hz
    const tick = () => {
      if (cancelled) {
        return;
      }
      if (hasCompletedTour()) {
        // The command palette replay path may have completed the tour
        // out of band; stop polling.
        return;
      }
      const root = sidebarRootRef.current;
      if (root && root.offsetParent !== null && root.offsetWidth > 0) {
        setTourVisible(true);
        return;
      }
      attempts += 1;
      if (attempts >= MAX_ATTEMPTS) {
        return;
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
    };
  }, []);
  useEffect(() => {
    const start = () => setTourVisible(true);
    const stop = () => setTourVisible(false);
    document.addEventListener(TOUR_START_EVENT, start);
    document.addEventListener(TOUR_STOP_EVENT, stop);
    return () => {
      document.removeEventListener(TOUR_START_EVENT, start);
      document.removeEventListener(TOUR_STOP_EVENT, stop);
    };
  }, []);
  const messagesEndRef = useRef<null | HTMLDivElement>(null);
  const [ghLoginStatus, setGHLoginStatus] = useState(
    GitHubCopilotLoginStatus.NotLoggedIn
  );
  const [loginClickCount, _setLoginClickCount] = useState(0);
  const [copilotRequestInProgress, setCopilotRequestInProgress] =
    useState(false);
  // sr-only announcement string driven by request-in-progress transitions.
  // Wrapping the whole transcript in aria-live would queue a polite
  // announcement per streamed token — hostile for screen reader users.
  // Instead announce at request boundaries: "Generating response" when
  // a request starts, "Response complete" when it ends.
  const [chatStatusAnnouncement, setChatStatusAnnouncement] = useState('');
  const prevCopilotRequestInProgressRef = useRef(false);
  useEffect(() => {
    const prev = prevCopilotRequestInProgressRef.current;
    if (!prev && copilotRequestInProgress) {
      setChatStatusAnnouncement('Generating response.');
    } else if (prev && !copilotRequestInProgress) {
      setChatStatusAnnouncement('Response complete.');
    }
    prevCopilotRequestInProgressRef.current = copilotRequestInProgress;
  }, [copilotRequestInProgress]);
  const [showPopover, setShowPopover] = useState(false);
  const [originalPrefixes, setOriginalPrefixes] = useState<string[]>([]);
  const [prefixSuggestions, setPrefixSuggestions] = useState<string[]>([]);
  const [selectedPrefixSuggestionIndex, setSelectedPrefixSuggestionIndex] =
    useState(0);
  const promptInputRef = useRef<HTMLTextAreaElement>(null);
  const autocompleteRef = useRef<HTMLDivElement>(null);
  const atButtonRef = useRef<HTMLButtonElement>(null);
  // Refs on the popover wrappers so we can move focus into the dialog
  // when it opens (replaces the broken autoFocus={true} pattern, which
  // only works on form controls). Paired with focus-restore refs that
  // remember which element triggered the open so we can put focus back
  // on close, regardless of which exit path the user took.
  const workspaceFilePopoverRef = useRef<HTMLDivElement>(null);
  const modeToolsPopoverRef = useRef<HTMLDivElement>(null);
  const workspaceFilePickerOpenerRef = useRef<HTMLElement | null>(null);
  const modeToolsOpenerRef = useRef<HTMLElement | null>(null);
  const slashPopoverOpenerRef = useRef<HTMLElement | null>(null);
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
  const [selectedContextFiles, setSelectedContextFiles] = useState<
    ISelectedContextFile[]
  >([]);
  const [showWorkspaceFilePicker, setShowWorkspaceFilePicker] = useState(false);
  const [workspaceFiles, setWorkspaceFiles] = useState<IWorkspaceFileOption[]>(
    []
  );
  const [workspaceFileSearch, setWorkspaceFileSearch] = useState('');
  const [workspaceFilesLoaded, setWorkspaceFilesLoaded] = useState(false);
  const [workspaceFilesLoading, setWorkspaceFilesLoading] = useState(false);
  const [showClaudeSessionPicker, setShowClaudeSessionPicker] = useState(false);
  const [workspaceFilesError, setWorkspaceFilesError] = useState('');
  const [workspaceScanLimitReached, setWorkspaceScanLimitReached] =
    useState(false);
  const [workspaceFileActionPath, setWorkspaceFileActionPath] = useState('');
  // Scan-generation counter — incremented when the picker closes, on
  // unmount, or when a fresh scan starts. The in-flight BFS reads this
  // before each batch and before its terminal `setState` calls; if the
  // generation has changed, the scan abandons silently so a slow tree
  // walk can't land stale results on a reopened picker.
  const workspaceFilesLoadingRef = useRef(false);
  const workspaceScanGenerationRef = useRef(0);
  // Path of the notebook that was active when the current agent task
  // started. Threaded into every notebook-cell RunUICommand so tools keep
  // targeting the right notebook after the user switches tabs mid-task
  // (issue #252). Cleared / reset on every new chat submission.
  const taskTargetNotebookPathRef = useRef<string | null>(null);

  // Progress-feedback state for the "Generating" indicator.
  // `elapsedSeconds` ticks every 1s while a request is in flight (so the
  // user can see at a glance how long they've been waiting).
  // `lastHeartbeatAtRef` tracks the most recent ClaudeCodeHeartbeat from
  // the server; when the gap exceeds HEARTBEAT_STALE_MS the indicator
  // copy flips to a "may be slow" variant. `heartbeatTick` increments on
  // each heartbeat to drive a brief CSS pulse on the indicator dot. None
  // of these matter outside Claude mode because heartbeats only fire
  // there, but the elapsed counter is a useful signal regardless of
  // provider.
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const requestStartedAtRef = useRef<number | null>(null);
  const lastHeartbeatAtRef = useRef<number | null>(null);
  const [heartbeatTick, setHeartbeatTick] = useState(0);
  const [isStalled, setIsStalled] = useState(false);

  const [isDragOver, setIsDragOver] = useState(false);
  const [isUploadingFiles, setIsUploadingFiles] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const telemetryEmitter: ITelemetryEmitter = props.getTelemetryEmitter();
  const [chatMode, setChatMode] = useState(NBIAPI.config.defaultChatMode);
  const [permissionMode, setPermissionMode] = useState(
    NBIAPI.config.claudePermissionDefaultMode
  );
  const [bypassPermissionsAllowed, setBypassPermissionsAllowed] = useState(
    NBIAPI.config.featurePolicies.claude_bypass_permissions.enabled
  );
  // Ref mirror so memoized request handlers read the live mode without
  // adding it to their dependency arrays.
  const permissionModeRef = useRef(permissionMode);

  const [toolSelectionTitle, setToolSelectionTitle] =
    useState('Tool selection');
  const [selectedToolCount, setSelectedToolCount] = useState(0);
  const [unsafeToolSelected, setUnsafeToolSelected] = useState(false);

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
    builtinToolsets: [],
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
  const selectedContextFilePaths = useMemo(
    () => new Set(selectedContextFiles.map(file => file.path)),
    [selectedContextFiles]
  );
  const visibleWorkspaceFiles = useMemo(() => {
    const search = workspaceFileSearch.trim().toLowerCase();
    const filteredFiles =
      search === ''
        ? workspaceFiles
        : workspaceFiles.filter(file =>
            file.path.toLowerCase().includes(search)
          );

    return filteredFiles.slice(0, MAX_VISIBLE_WORKSPACE_FILES);
  }, [workspaceFileSearch, workspaceFiles]);

  const loadWorkspaceFiles = useCallback(async () => {
    if (workspaceFilesLoadingRef.current) {
      return;
    }
    workspaceFilesLoadingRef.current = true;
    const generation = ++workspaceScanGenerationRef.current;
    const isCanceled = () => workspaceScanGenerationRef.current !== generation;

    setWorkspaceFilesLoading(true);
    setWorkspaceFilesError('');

    const discoveredFiles: IWorkspaceFileOption[] = [];
    const directoriesToScan = [''];
    // Path-string dedupe: prevents a directory enqueued under the same
    // logical path from being fetched twice. Symlinks reached via two
    // different parent paths (e.g., `a/link` and `b/link` both pointing
    // at `foo/`) have distinct path strings and will still be walked
    // twice — the Contents API doesn't expose the resolved inode for
    // a true canonical dedupe.
    const visitedDirectories = new Set<string>(['']);
    let limitReached = false;
    let totalFulfilled = 0;
    // Boolean rather than `lastRejection !== undefined`: a rejection
    // whose reason is itself `undefined` should still surface as an
    // error, not be silently treated as success.
    let sawRejection = false;
    let lastRejection: unknown;

    try {
      const contentsManager = props.getApp().serviceManager.contents;

      // Merge built-in skips with the admin-configured
      // `additional_skipped_workspace_directories` traitlet so both layers
      // gate enqueueing before we issue an HTTP request for the subdir.
      const skipDirectoryNames = new Set<string>([
        ...SKIPPED_WORKSPACE_DIRECTORIES,
        ...NBIAPI.config.additionalSkippedWorkspaceDirectories
      ]);

      // BFS the tree in bounded-parallel batches. Per-directory failures
      // (deleted mid-scan, permission-denied mount) are skipped so one bad
      // directory doesn't kill the whole picker — but if no fetch in the
      // entire walk succeeds (offline, server down), the all-rejected case
      // bubbles to the catch below as a real error.
      while (
        directoriesToScan.length > 0 &&
        discoveredFiles.length < MAX_WORKSPACE_FILE_SCAN_COUNT
      ) {
        if (isCanceled()) {
          return;
        }
        // Sort the queue before slicing so cap-truncated walks pick a
        // stable, alphabetical subset across runs. Without this the
        // "first 1500 files" the user sees depends on which HTTP
        // responses happened to resolve first.
        directoriesToScan.sort();
        const batch = directoriesToScan.splice(0, WORKSPACE_SCAN_CONCURRENCY);
        const results = await Promise.allSettled(
          batch.map(dir => contentsManager.get(dir, { content: true }))
        );
        if (isCanceled()) {
          return;
        }

        for (const result of results) {
          if (limitReached) {
            break;
          }
          if (result.status !== 'fulfilled') {
            sawRejection = true;
            lastRejection = result.reason;
            continue;
          }
          totalFulfilled += 1;
          const model: any = result.value;
          if (model.type !== 'directory' || !Array.isArray(model.content)) {
            continue;
          }

          // Sort entries within a directory so cap-truncated picks remain
          // deterministic when the cap fires mid-directory.
          const entries = [...model.content].sort((lhs, rhs) =>
            lhs.path.localeCompare(rhs.path)
          );
          for (const entry of entries) {
            if (!entry?.path || !entry?.name) {
              continue;
            }
            if (entry.name.startsWith('.')) {
              continue;
            }
            if (entry.type === 'directory') {
              if (
                !skipDirectoryNames.has(entry.name) &&
                !visitedDirectories.has(entry.path)
              ) {
                visitedDirectories.add(entry.path);
                directoriesToScan.push(entry.path);
              }
              continue;
            }
            if (entry.type === 'file' || entry.type === 'notebook') {
              discoveredFiles.push({
                name: entry.name,
                path: entry.path,
                type: entry.type
              });
              if (discoveredFiles.length >= MAX_WORKSPACE_FILE_SCAN_COUNT) {
                limitReached = true;
                break;
              }
            }
          }
        }
      }

      if (isCanceled()) {
        return;
      }
      if (totalFulfilled === 0 && sawRejection) {
        // Every fetch rejected — bubble out the first failure so the
        // popover surfaces an error instead of an empty list.
        throw lastRejection;
      }
      discoveredFiles.sort((lhs, rhs) => lhs.path.localeCompare(rhs.path));
      setWorkspaceFiles(discoveredFiles);
      setWorkspaceFilesLoaded(true);
      setWorkspaceScanLimitReached(limitReached);
    } catch (error: any) {
      // Log before the cancel guard so a fully-failed scan that the user
      // then closed/unmounted still leaves a diagnostic trail.
      console.error('Failed to load workspace files.', error);
      if (isCanceled()) {
        return;
      }
      setWorkspaceFilesError(
        error?.message || 'Failed to load workspace files.'
      );
    } finally {
      workspaceFilesLoadingRef.current = false;
      if (!isCanceled()) {
        setWorkspaceFilesLoading(false);
      }
    }
  }, [props]);

  // Latest references for the fileChanged subscription handler to read
  // without re-binding the effect on every render.
  const loadWorkspaceFilesRef = useRef(loadWorkspaceFiles);
  useEffect(() => {
    loadWorkspaceFilesRef.current = loadWorkspaceFiles;
  }, [loadWorkspaceFiles]);
  const showWorkspaceFilePickerRef = useRef(showWorkspaceFilePicker);
  useEffect(() => {
    showWorkspaceFilePickerRef.current = showWorkspaceFilePicker;
  }, [showWorkspaceFilePicker]);

  // Focus management for the popovers: move focus into the popover on
  // open so keyboard users land inside it, then restore focus to the
  // trigger on close. Restoration runs on the close transition
  // regardless of which exit path (close button, Escape, outside click)
  // dismissed the popover.
  //
  // Restoration is guarded by:
  //   (a) `document.contains(opener)` so a trigger that was unmounted
  //       between open and close (e.g., chat-mode flipped) doesn't
  //       throw; and
  //   (b) "the user hasn't moved focus elsewhere intentionally" — we
  //       only steal focus back when the activeElement is still inside
  //       the popover that's closing (i.e., the close came from the
  //       popover itself, not from the user clicking a sibling control).
  //       Without this guard, dismissing one popover by clicking the
  //       trigger of another would yank focus to the first popover's
  //       trigger right after the second popover focused itself.
  const prevWorkspaceFilePickerRef = useRef(false);
  useEffect(() => {
    if (showWorkspaceFilePicker && !prevWorkspaceFilePickerRef.current) {
      workspaceFilePopoverRef.current?.focus();
    } else if (!showWorkspaceFilePicker && prevWorkspaceFilePickerRef.current) {
      const opener = workspaceFilePickerOpenerRef.current;
      const popover = workspaceFilePopoverRef.current;
      workspaceFilePickerOpenerRef.current = null;
      const active = document.activeElement as HTMLElement | null;
      const focusInsidePopover = popover ? popover.contains(active) : false;
      const focusOnBody = active === document.body || active === null;
      if (
        (focusInsidePopover || focusOnBody) &&
        opener &&
        document.contains(opener)
      ) {
        opener.focus();
      }
    }
    prevWorkspaceFilePickerRef.current = showWorkspaceFilePicker;
  }, [showWorkspaceFilePicker]);

  const prevModeToolsRef = useRef(false);
  useEffect(() => {
    if (showModeTools && !prevModeToolsRef.current) {
      modeToolsPopoverRef.current?.focus();
    } else if (!showModeTools && prevModeToolsRef.current) {
      const opener = modeToolsOpenerRef.current;
      const popover = modeToolsPopoverRef.current;
      modeToolsOpenerRef.current = null;
      const active = document.activeElement as HTMLElement | null;
      const focusInsidePopover = popover ? popover.contains(active) : false;
      const focusOnBody = active === document.body || active === null;
      if (
        (focusInsidePopover || focusOnBody) &&
        opener &&
        document.contains(opener)
      ) {
        opener.focus();
      }
    }
    prevModeToolsRef.current = showModeTools;
  }, [showModeTools]);

  // Slash-popover restoration: only the close transition matters; the
  // popover lives next to the textarea and the textarea retains focus
  // while it's open, so there's nothing to focus *into* on open.
  const prevShowPopoverRef = useRef(false);
  useEffect(() => {
    if (!showPopover && prevShowPopoverRef.current) {
      const opener = slashPopoverOpenerRef.current;
      const popover = autocompleteRef.current;
      slashPopoverOpenerRef.current = null;
      const active = document.activeElement as HTMLElement | null;
      const focusInsidePopover = popover ? popover.contains(active) : false;
      // The textarea always stays focusable next to the popover and the
      // user may have been typing inside it the whole time, so leave
      // focus alone if it's on the textarea. Same "user moved focus
      // elsewhere intentionally" guard as the other popovers.
      const focusOnTextarea = active === promptInputRef.current;
      const focusOnBody = active === document.body || active === null;
      if (
        (focusInsidePopover || focusOnBody) &&
        !focusOnTextarea &&
        opener &&
        document.contains(opener)
      ) {
        opener.focus();
      }
    }
    prevShowPopoverRef.current = showPopover;
  }, [showPopover]);
  // Set when a refresh arrives mid-scan. The in-flight scan's drain loop
  // honors one more pass at completion, bounding the storm to "at most one
  // scan running + one queued" instead of cascading cancellations.
  const pendingRescanRef = useRef(false);

  const runWorkspaceFileScan = useCallback(async () => {
    if (workspaceFilesLoadingRef.current) {
      pendingRescanRef.current = true;
      return;
    }
    do {
      pendingRescanRef.current = false;
      await loadWorkspaceFilesRef.current();
    } while (pendingRescanRef.current && showWorkspaceFilePickerRef.current);
  }, []);

  const runWorkspaceFileScanRef = useRef(runWorkspaceFileScan);
  useEffect(() => {
    runWorkspaceFileScanRef.current = runWorkspaceFileScan;
  }, [runWorkspaceFileScan]);

  // Subscribe to Contents-API changes so a notebook or file created outside
  // the picker (manually in the file browser, via terminal, or by a Claude
  // tool that round-trips through the Contents API) shows up without
  // requiring a full lab reload. The contents manager is a singleton for
  // the app's lifetime; depending on `[]` avoids `props`-identity churn that
  // would otherwise reconnect the signal on every parent render and reset
  // the in-flight debounce timer.
  const appRef = useRef(props.getApp());
  useEffect(() => {
    const contents = appRef.current.serviceManager.contents;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const onFileChanged = (_sender: unknown, change: Contents.IChangedArgs) => {
      // 'save' fires on every edit-and-save; the picker doesn't care about
      // content changes, only the file set.
      if (change.type === 'save') {
        return;
      }
      if (timer !== null) {
        clearTimeout(timer);
      }
      timer = setTimeout(() => {
        timer = null;
        if (showWorkspaceFilePickerRef.current) {
          runWorkspaceFileScanRef.current();
        } else {
          // Picker is closed — mark stale so the next open re-scans.
          setWorkspaceFilesLoaded(false);
        }
      }, WORKSPACE_FILE_REFRESH_DEBOUNCE_MS);
    };

    contents.fileChanged.connect(onFileChanged);
    return () => {
      contents.fileChanged.disconnect(onFileChanged);
      if (timer !== null) {
        clearTimeout(timer);
      }
    };
  }, []);

  const refreshWorkspaceFiles = useCallback(() => {
    runWorkspaceFileScanRef.current();
  }, []);

  const handleWorkspaceFilePickerClick = async () => {
    setShowPopover(false);
    setShowModeTools(false);
    const nextState = !showWorkspaceFilePicker;
    // Capture the trigger element before re-render so we can restore
    // focus to it when the popover closes (D030).
    if (nextState) {
      workspaceFilePickerOpenerRef.current =
        (document.activeElement as HTMLElement | null) ?? null;
    }
    setShowWorkspaceFilePicker(nextState);
    // Sync the ref synchronously so a debounced refresh that fires during
    // the awaited scan below honors the now-open picker state immediately,
    // rather than waiting for the post-render useEffect to catch up.
    showWorkspaceFilePickerRef.current = nextState;

    if (nextState && !workspaceFilesLoaded) {
      await runWorkspaceFileScan();
    }
  };

  const handleWorkspaceFileSelection = async (file: IWorkspaceFileOption) => {
    if (selectedContextFilePaths.has(file.path)) {
      setSelectedContextFiles(previousFiles =>
        previousFiles.filter(
          selectedFile =>
            selectedFile.source === 'upload' || selectedFile.path !== file.path
        )
      );
      return;
    }

    setWorkspaceFilesError('');
    setWorkspaceFileActionPath(file.path);

    try {
      // In Claude Code mode, the agent reads the file itself via the
      // server's @-mention path. Skip the contents fetch so binary files
      // (images, PDFs, notebooks) are picker-eligible and large files
      // aren't truncated by the client-side content-injection budget.
      let content = '';
      let lineCount = 0;
      if (!NBIAPI.config.isInClaudeCodeMode) {
        const contentsManager = props.getApp().serviceManager.contents;
        const model: any = await contentsManager.get(file.path, {
          content: true
        });
        content = serializeWorkspaceFileContent(model);
        if (content.trim() === '') {
          throw new Error('Empty files do not provide useful context.');
        }
        lineCount = countContentLines(content);
      }

      const nextSelectedFile: ISelectedContextFile = {
        content,
        lineCount,
        path: file.path,
        type: file.type
      };

      setSelectedContextFiles(previousFiles =>
        [...previousFiles, nextSelectedFile].sort((lhs, rhs) =>
          lhs.path.localeCompare(rhs.path)
        )
      );
    } catch (error: any) {
      console.error(`Failed to attach workspace file '${file.path}'.`, error);
      setWorkspaceFilesError(
        error?.message || `Failed to attach workspace file '${file.path}'.`
      );
    } finally {
      setWorkspaceFileActionPath('');
    }
  };

  const removeSelectedContextFile = (fileKey: string) => {
    setSelectedContextFiles(previousFiles =>
      previousFiles.filter(file => (file.serverPath ?? file.path) !== fileKey)
    );
  };

  const handleDragOver = (event: React.DragEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (
      !isDragOver &&
      chatEnabled &&
      event.dataTransfer.types.includes('Files')
    ) {
      setIsDragOver(true);
    }
  };

  const handleDragLeave = (event: React.DragEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (!event.currentTarget.contains(event.relatedTarget as Node)) {
      setIsDragOver(false);
    }
  };

  const processDroppedFile = async (
    file: File
  ): Promise<ISelectedContextFile | null> => {
    if (isLikelyTextFile(file)) {
      const content = await readFileAsText(file);
      if (content.trim() === '') {
        throw new Error(`'${file.name}' is empty`);
      }
      return {
        content,
        lineCount: countContentLines(content),
        path: file.name,
        type: 'file',
        source: 'upload'
      };
    }

    if (file.type.startsWith('image/')) {
      const [imageDataUrl, { serverPath, filename }] = await Promise.all([
        readFileAsDataURL(file),
        NBIAPI.uploadFile(file)
      ]);
      return {
        content: '',
        lineCount: 0,
        path: filename,
        type: 'file',
        source: 'upload',
        serverPath,
        isImage: true,
        imageDataUrl,
        mimeType: file.type
      };
    }

    const { serverPath, filename } = await NBIAPI.uploadFile(file);
    return {
      content: '',
      lineCount: 0,
      path: filename,
      type: 'file',
      source: 'upload',
      serverPath
    };
  };

  const addSystemNotice = (message: string) => {
    setChatMessages(prev => [
      ...prev,
      {
        id: UUID.uuid4(),
        date: new Date(),
        from: 'notice',
        participant: { name: 'Notice' } as any,
        contents: [
          {
            id: UUID.uuid4(),
            type: ResponseStreamDataType.Markdown,
            content: message,
            created: new Date()
          }
        ]
      }
    ]);
  };

  const processAndAttachFiles = async (files: File[]) => {
    if (files.length === 0) {
      return;
    }

    const uploadedFiles = selectedContextFiles.filter(
      f => f.source === 'upload'
    );

    // Duplicate detection: skip files already attached
    const existingNames = new Set(uploadedFiles.map(f => f.path));
    const uniqueFiles = files.filter(f => !existingNames.has(f.name));
    const duplicateCount = files.length - uniqueFiles.length;

    // Enforce file count limit
    const currentUploadCount = uploadedFiles.length;
    const available = MAX_ATTACHED_FILES - currentUploadCount;
    const filesToProcess = uniqueFiles.slice(0, Math.max(0, available));
    const skippedCount = uniqueFiles.length - filesToProcess.length;

    if (filesToProcess.length === 0) {
      const parts: string[] = [];
      if (duplicateCount > 0) {
        parts.push(`${duplicateCount} already attached`);
      }
      if (skippedCount > 0) {
        parts.push(`limit of ${MAX_ATTACHED_FILES} files reached`);
      }
      addSystemNotice(`No files added: ${parts.join('; ')}.`);
      return;
    }

    setIsUploadingFiles(true);
    try {
      const results = await Promise.allSettled(
        filesToProcess.map(file => processDroppedFile(file))
      );

      const newContextFiles: ISelectedContextFile[] = [];
      const errors: string[] = [];

      for (const result of results) {
        if (result.status === 'fulfilled' && result.value) {
          newContextFiles.push(result.value);
        } else if (result.status === 'rejected') {
          errors.push(String(result.reason?.message ?? result.reason));
        }
      }

      const notices: string[] = [];
      if (errors.length > 0) {
        notices.push(`Could not attach: ${errors.join('; ')}`);
      }
      if (duplicateCount > 0) {
        notices.push(
          `${duplicateCount} duplicate${duplicateCount > 1 ? 's' : ''} skipped`
        );
      }
      if (skippedCount > 0) {
        notices.push(
          `${skippedCount} file${skippedCount > 1 ? 's' : ''} skipped (limit of ${MAX_ATTACHED_FILES})`
        );
      }
      if (notices.length > 0) {
        addSystemNotice(notices.join('. ') + '.');
      }

      if (newContextFiles.length > 0) {
        setSelectedContextFiles(prev => [...prev, ...newContextFiles]);
        // Same reason as the lm-drop path: the gesture that initiated this
        // (HTML5 drop, file dialog, image paste) often leaves focus outside
        // the chat composer, so the next Enter goes somewhere unintended.
        promptInputRef.current?.focus();
      }
    } finally {
      setIsUploadingFiles(false);
    }
  };

  const handleDrop = async (event: React.DragEvent) => {
    event.preventDefault();
    event.stopPropagation();
    setIsDragOver(false);
    if (!chatEnabled) {
      return;
    }
    await processAndAttachFiles(Array.from(event.dataTransfer.files));
  };

  const handleFileInputChange = async (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    await processAndAttachFiles(files);
  };

  const handlePaste = async (
    event: React.ClipboardEvent<HTMLTextAreaElement>
  ) => {
    const items = Array.from(event.clipboardData.items);
    const imageItem = items.find(item => item.type.startsWith('image/'));
    if (!imageItem || !chatEnabled) {
      return;
    }
    const file = imageItem.getAsFile();
    if (!file) {
      return;
    }
    event.preventDefault();
    const ext = (imageItem.type.split('/')[1] ?? 'png').split('+')[0];
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const namedFile = new File([file], `screenshot-${timestamp}.${ext}`, {
      type: imageItem.type
    });
    await processAndAttachFiles([namedFile]);
  };

  const cleanupRemovedToolsFromToolSelections = () => {
    const newToolSelections = { ...toolSelections };
    // if servers or tool is not in mcpServerEnabledState, remove it from newToolSelections
    for (const serverId in newToolSelections.mcpServers) {
      if (!mcpServerEnabledState.has(serverId)) {
        delete newToolSelections.mcpServers[serverId];
      } else {
        for (const tool of newToolSelections.mcpServers[serverId]) {
          if (!mcpServerEnabledState.get(serverId).has(tool)) {
            newToolSelections.mcpServers[serverId].splice(
              newToolSelections.mcpServers[serverId].indexOf(tool),
              1
            );
          }
        }
      }
    }
    for (const extensionId in newToolSelections.extensions) {
      if (!mcpServerEnabledState.has(extensionId)) {
        delete newToolSelections.extensions[extensionId];
      } else {
        for (const toolsetId in newToolSelections.extensions[extensionId]) {
          for (const tool of newToolSelections.extensions[extensionId][
            toolsetId
          ]) {
            if (!mcpServerEnabledState.get(extensionId).has(tool)) {
              newToolSelections.extensions[extensionId][toolsetId].splice(
                newToolSelections.extensions[extensionId][toolsetId].indexOf(
                  tool
                ),
                1
              );
            }
          }
        }
      }
    }
    setToolSelections(newToolSelections);
    setRenderCount(renderCount => renderCount + 1);
  };

  useEffect(() => {
    cleanupRemovedToolsFromToolSelections();
  }, [mcpServerEnabledState]);

  // JupyterLab file-browser drag uses Lumino's lm-* CustomEvents (mime
  // 'application/x-jupyter-icontents' carrying workspace-relative paths),
  // not native HTML5 drag. We listen at the document level with capture
  // phase + a containment check so intermediate widgets that
  // stopPropagation in target phase can't swallow the event.
  useEffect(() => {
    const FILE_BROWSER_MIME = 'application/x-jupyter-icontents';

    const isInsideSidebar = (event: Event): boolean => {
      const root = sidebarRootRef.current;
      if (!root) {
        return false;
      }
      const target = event.target;
      return target instanceof Node && root.contains(target);
    };

    const hasPaths = (event: Event): boolean => {
      const mimeData = (
        event as unknown as {
          mimeData?: { hasData?: (key: string) => boolean };
        }
      ).mimeData;
      return mimeData?.hasData?.(FILE_BROWSER_MIME) === true;
    };

    const dragEnter = (event: Event) => {
      if (
        !NBIAPI.getChatEnabled() ||
        !hasPaths(event) ||
        !isInsideSidebar(event)
      ) {
        return;
      }
      setIsDragOver(true);
      event.preventDefault();
      event.stopPropagation();
    };

    const dragOver = (event: Event) => {
      if (
        !NBIAPI.getChatEnabled() ||
        !hasPaths(event) ||
        !isInsideSidebar(event)
      ) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      // Mirror the source's proposedAction. The JL file browser starts
      // its Drag with supportedActions: 'move'; setting dropAction to a
      // value outside that set falls through validateAction to 'none'
      // and Lumino skips lm-drop on pointerup.
      const drag = event as unknown as {
        proposedAction?: string;
        dropAction: string;
      };
      drag.dropAction = drag.proposedAction || 'move';
    };

    const dragLeave = (event: Event) => {
      if (!NBIAPI.getChatEnabled() || !isInsideSidebar(event)) {
        return;
      }
      // Only clear the overlay when the drag genuinely leaves the
      // sidebar; ignore leaves that cross internal child boundaries.
      const related = (event as unknown as { relatedTarget?: Node | null })
        .relatedTarget;
      if (related && sidebarRootRef.current?.contains(related)) {
        return;
      }
      setIsDragOver(false);
    };

    const drop = (event: Event) => {
      if (
        !NBIAPI.getChatEnabled() ||
        !hasPaths(event) ||
        !isInsideSidebar(event)
      ) {
        return;
      }
      const dragEvent = event as unknown as {
        mimeData: { getData: (key: string) => unknown };
        proposedAction?: string;
        dropAction: string;
      };
      const raw = dragEvent.mimeData.getData(FILE_BROWSER_MIME);
      if (!Array.isArray(raw) || raw.length === 0) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      dragEvent.dropAction = dragEvent.proposedAction || 'move';
      setIsDragOver(false);
      const paths = raw.filter((p): p is string => typeof p === 'string');
      void attachWorkspacePaths(paths);
    };

    document.addEventListener('lm-dragenter', dragEnter, true);
    document.addEventListener('lm-dragover', dragOver, true);
    document.addEventListener('lm-dragleave', dragLeave, true);
    document.addEventListener('lm-drop', drop, true);
    return () => {
      document.removeEventListener('lm-dragenter', dragEnter, true);
      document.removeEventListener('lm-dragover', dragOver, true);
      document.removeEventListener('lm-dragleave', dragLeave, true);
      document.removeEventListener('lm-drop', drop, true);
    };
  }, []);

  // Attach a list of workspace-relative paths (from JL file browser
  // drag) as chat context. Images are attached as image context (with a
  // base64 dataURL thumbnail) so the model can see them; text and
  // notebook files are attached as content context.
  const attachWorkspacePaths = async (paths: string[]) => {
    if (paths.length === 0) {
      return;
    }
    const contentsManager = props.getApp().serviceManager.contents;
    const additions: ISelectedContextFile[] = [];
    const errors: string[] = [];
    await Promise.all(
      paths.map(async path => {
        try {
          const model: any = await contentsManager.get(path, { content: true });
          const mimetype: string = model.mimetype || '';
          // Image branch: file is already on the server, so build a
          // data URL from the base64 content for the thumbnail and let
          // the backend resolve the workspace path. No upload needed.
          if (model.format === 'base64' && mimetype.startsWith('image/')) {
            additions.push({
              content: '',
              lineCount: 0,
              path,
              type: 'file',
              isImage: true,
              imageDataUrl: `data:${mimetype};base64,${model.content}`,
              mimeType: mimetype
            });
            return;
          }
          const content = serializeWorkspaceFileContent(model);
          if (content.trim() === '') {
            throw new Error(`'${path}' has no content to attach.`);
          }
          additions.push({
            content,
            lineCount: countContentLines(content),
            path,
            type: model.type === 'notebook' ? 'notebook' : 'file'
          });
        } catch (error: any) {
          errors.push(error?.message || `Failed to attach '${path}'.`);
        }
      })
    );
    if (additions.length > 0) {
      // De-dupe inside the functional updater so it reads the freshest
      // state. A stale closure on `selectedContextFiles` here would let
      // the same path land twice when the user drops it a second time.
      setSelectedContextFiles(previous => {
        const existing = new Set(previous.map(f => f.path));
        const fresh = additions.filter(a => !existing.has(a.path));
        if (fresh.length === 0) {
          return previous;
        }
        return [...previous, ...fresh].sort((lhs, rhs) =>
          lhs.path.localeCompare(rhs.path)
        );
      });
      // Move keyboard focus into the prompt so the user can immediately
      // describe what they want done with the attached files.
      promptInputRef.current?.focus();
    }
    if (errors.length > 0) {
      // Match the terminal-drag error path (Notification toast) instead
      // of slipping a chat-message-notice into the transcript, which
      // can scroll out of view.
      Notification.warning(`Could not attach: ${errors.join('; ')}`);
    }
  };

  useEffect(() => {
    const handler = () => {
      toolConfigRef.current = NBIAPI.config.toolConfig;
      mcpServerSettingsRef.current = NBIAPI.config.mcpServerSettings;
      const newMcpServerEnabledState = mcpServerSettingsToEnabledState(
        toolConfigRef.current.mcpServers,
        mcpServerSettingsRef.current
      );
      setMCPServerEnabledState(newMcpServerEnabledState);
      setRenderCount(renderCount => renderCount + 1);
    };
    NBIAPI.configChanged.connect(handler);
    return () => {
      NBIAPI.configChanged.disconnect(handler);
    };
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

  // Subscribe to the Claude agent's 20s keepalive. Each beat resets the
  // staleness window, kicks a pulse on the indicator dot, and clears the
  // "server may be slow" copy.
  useEffect(() => {
    const handler = () => {
      lastHeartbeatAtRef.current = Date.now();
      setHeartbeatTick(tick => tick + 1);
      setIsStalled(false);
    };
    NBIAPI.claudeCodeHeartbeat.connect(handler);
    return () => {
      NBIAPI.claudeCodeHeartbeat.disconnect(handler);
    };
  }, []);

  useEffect(() => {
    permissionModeRef.current = permissionMode;
  }, [permissionMode]);

  // Track the bypass policy across capability refreshes (an armed mode must
  // not outlive a policy flip) and follow server-driven mode changes (plan
  // approval, the hidden plan-mode slash aliases, client restart) so the
  // selector always shows what the next turn will use.
  useEffect(() => {
    const configHandler = () => {
      const allowed =
        NBIAPI.config.featurePolicies.claude_bypass_permissions.enabled;
      setBypassPermissionsAllowed(allowed);
      if (!allowed) {
        setPermissionMode(mode =>
          mode === BYPASS_PERMISSIONS_MODE ? 'default' : mode
        );
      }
    };
    const modeHandler = (
      _: unknown,
      notification: { mode: string; reset: boolean }
    ) => {
      setPermissionMode(current =>
        nextPermissionModeOnNotification(current, notification)
      );
    };
    NBIAPI.configChanged.connect(configHandler);
    NBIAPI.claudePermissionModeChanged.connect(modeHandler);
    return () => {
      NBIAPI.configChanged.disconnect(configHandler);
      NBIAPI.claudePermissionModeChanged.disconnect(modeHandler);
    };
  }, []);

  // Drive the elapsed-time counter while a request is in flight. The same
  // interval re-evaluates whether the heartbeat has gone stale so the
  // indicator copy can swap to "Still working..." without a second timer.
  useEffect(() => {
    if (!copilotRequestInProgress) {
      requestStartedAtRef.current = null;
      setElapsedSeconds(0);
      setIsStalled(false);
      return;
    }
    requestStartedAtRef.current = Date.now();
    lastHeartbeatAtRef.current = Date.now();
    setElapsedSeconds(0);
    setIsStalled(false);
    const tick = () => {
      const started = requestStartedAtRef.current;
      if (started === null) {
        return;
      }
      setElapsedSeconds(Math.floor((Date.now() - started) / 1000));
      // Heartbeats only fire in Claude mode; suppress the staleness check
      // for other providers so they don't get a permanent "may be slow"
      // banner just because no heartbeats arrive there.
      if (NBIAPI.config.isInClaudeCodeMode) {
        setIsStalled(isHeartbeatStale(lastHeartbeatAtRef.current, Date.now()));
      }
    };
    tick();
    const intervalId = window.setInterval(tick, 1000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [copilotRequestInProgress]);

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
    setUnsafeToolSelected(
      toolSelections.builtinToolsets.some((toolsetName: string) =>
        [
          BuiltinToolsetType.NotebookEdit,
          BuiltinToolsetType.NotebookExecute,
          BuiltinToolsetType.PythonFileEdit,
          BuiltinToolsetType.FileEdit,
          BuiltinToolsetType.CommandExecute
        ].includes(toolsetName as unknown as BuiltinToolsetType)
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

    if (NBIAPI.config.isInClaudeCodeMode) {
      const claudeChatParticipant = NBIAPI.config.chatParticipants.find(
        participant => participant.id === CLAUDE_CODE_CHAT_PARTICIPANT_ID
      );
      if (claudeChatParticipant) {
        const commands = claudeChatParticipant.commands;
        for (const command of commands) {
          prefixes.push(`/${command}`);
        }
      }
      // /enter-plan-mode and /exit-plan-mode were replaced by the
      // permission-mode selector; they still work when typed (hidden
      // aliases, one-release deprecation) but no longer autocomplete.
    } else {
      if (chatMode === 'ask') {
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
      } else {
        prefixes.push('/clear');
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

  useEffect(() => {
    if (!showPopover) {
      return;
    }
    const handleClickOutside = (event: MouseEvent) => {
      if (
        !autocompleteRef.current?.contains(event.target as Node) &&
        !promptInputRef.current?.contains(event.target as Node) &&
        !atButtonRef.current?.contains(event.target as Node)
      ) {
        setShowPopover(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showPopover]);

  const onPromptChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const newPrompt = event.target.value;
    setPrompt(newPrompt);
    const trimmedPrompt = newPrompt.trimStart();
    if (trimmedPrompt === '@' || trimmedPrompt === '/') {
      // D030: remember which element opened the popover so focus returns
      // there on close. For the typing path that's the textarea itself.
      slashPopoverOpenerRef.current =
        (document.activeElement as HTMLElement | null) ?? null;
      setShowPopover(true);
      filterPrefixSuggestions(trimmedPrompt);
    } else if (trimmedPrompt.startsWith('@') || trimmedPrompt.startsWith('/')) {
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

  const handleSettingsButtonClick = async () => {
    setShowModeTools(false);
    setShowWorkspaceFilePicker(false);
    props
      .getApp()
      .commands.execute('notebook-intelligence:open-configuration-dialog');
  };

  const handleChatToolsButtonClick = async () => {
    setShowWorkspaceFilePicker(false);
    if (!showModeTools) {
      // D030: remember the trigger so focus returns there on close.
      modeToolsOpenerRef.current =
        (document.activeElement as HTMLElement | null) ?? null;
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

  const handleUserInputSubmit = async (options?: {
    promptOverride?: string;
    extraOutputContext?: ISelectedContextFile;
  }) => {
    const submitPrompt = options?.promptOverride ?? prompt;
    const isAutoSubmit = options?.promptOverride !== undefined;

    if (!isAutoSubmit) {
      setPromptHistoryIndex(promptHistory.length + 1);
      setPromptHistory([...promptHistory, prompt]);
    }
    setShowPopover(false);

    const promptPrefixParts = [];
    const promptParts = submitPrompt.split(' ');
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
            content: submitPrompt,
            created: new Date()
          }
        ]
      }
    ];
    setChatMessages(newList);

    if (submitPrompt.startsWith('/clear')) {
      startNewChatSession();
      return;
    }

    setCopilotRequestInProgress(true);

    const activeDocInfo: IActiveDocumentInfo = props.getActiveDocumentInfo();
    // Snapshot the active notebook so cell-targeting tools the agent fires
    // later in this run keep hitting the right notebook even after the
    // user switches tabs (issue #252). Non-notebook contexts clear the
    // ref so a stale path from a previous run doesn't bleed through.
    taskTargetNotebookPathRef.current = activeDocInfo?.filePath?.endsWith(
      '.ipynb'
    )
      ? activeDocInfo.filePath
      : null;
    const extractedPrompt = submitPrompt;
    const contents: IChatMessageContent[] = [];
    // One id for this turn's response message, reused on every streamed delta.
    // setChatMessages rebuilds the message object per delta; a fresh id each
    // time changes its React key and remounts the whole response subtree,
    // which re-runs ToolCallGroup's collapse heuristic and flickers the group
    // open/closed as calls stream in (issue #363).
    const responseMessageId = UUID.uuid4();
    const app = props.getApp();
    const additionalContext: IContextItem[] = [];
    let currentFileUsesWholeDocument = false;
    if (contextOn && activeDocumentInfo?.filename) {
      const selection = activeDocumentInfo.selection;
      const textSelected =
        selection &&
        !(
          selection.start.line === selection.end.line &&
          selection.start.column === selection.end.column
        );
      currentFileUsesWholeDocument = !textSelected;
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

    for (const file of selectedContextFiles) {
      if (file.outputContext) {
        additionalContext.push({
          type: ContextType.OutputContext,
          content: '',
          currentCellContents: null,
          filePath: file.path,
          cellIndex: file.cellIndex,
          outputContext: file.outputContext
        });
        continue;
      }

      if (
        currentFileUsesWholeDocument &&
        activeDocumentInfo?.filePath === file.path
      ) {
        continue;
      }

      const contextItem: IContextItem & { isUpload?: boolean } = {
        type: ContextType.Custom,
        content: file.content,
        currentCellContents: null,
        filePath:
          file.source === 'upload' ? (file.serverPath ?? file.path) : file.path,
        startLine: 1,
        endLine: file.lineCount
      };
      if (file.source === 'upload') {
        contextItem.isUpload = true;
      }
      if (file.isImage) {
        contextItem.isImage = true;
        contextItem.mimeType = file.mimeType ?? 'image/png';
      }
      additionalContext.push(contextItem);
    }

    // Auto-submit caller (e.g. Explain/Troubleshoot menu items) passes the
    // freshly-attached output bundle directly: the matching pill is queued
    // via setSelectedContextFiles in the same tick, so reading it from state
    // here would still be empty. Dedup against any pre-existing pill for the
    // same cell to avoid double-bundling.
    if (options?.extraOutputContext) {
      const extra = options.extraOutputContext;
      const alreadyAttached = selectedContextFiles.some(
        f => f.path === extra.path && f.outputContext
      );
      if (!alreadyAttached && extra.outputContext) {
        additionalContext.push({
          type: ContextType.OutputContext,
          content: '',
          currentCellContents: null,
          filePath: extra.path,
          cellIndex: extra.cellIndex,
          outputContext: extra.outputContext
        });
      }
    }

    setShowWorkspaceFilePicker(false);

    submitCompletionRequest(
      {
        messageId: lastMessageId.current,
        chatId,
        type: RunChatCompletionType.Chat,
        content: extractedPrompt,
        language: activeDocInfo.language,
        kernelName: activeDocInfo.kernelName,
        kernelDisplayName: activeDocInfo.kernelDisplayName,
        currentDirectory: props.getCurrentDirectory(),
        filename: activeDocInfo.filePath,
        additionalContext,
        chatMode,
        toolSelections: toolSelections,
        permissionMode: permissionModeRef.current
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
              if (nbiContent.type === ResponseStreamDataType.ToolCall) {
                // A tool call streams twice under one id (start, then finish);
                // merge by id so it stays one persistent card. See
                // upsertToolCallContent.
                upsertToolCallContent(
                  contents,
                  nbiContent.content,
                  new Date(response.created)
                );
              } else {
                contents.push({
                  id: UUID.uuid4(),
                  type: nbiContent.type,
                  content: nbiContent.content || '',
                  reasoningContent: nbiContent.reasoning_content || '',
                  reasoningTag: nbiContent.reasoning_content
                    ? '<think>'
                    : undefined,
                  reasoningFinished:
                    nbiContent.type === ResponseStreamDataType.Markdown &&
                    nbiContent.reasoning_content
                      ? true
                      : false,
                  contentDetail: nbiContent.detail,
                  created: new Date(response.created)
                });
              }
            } else {
              responseMessage =
                response.data['choices']?.[0]?.['delta']?.['content'];
              const reasoningContent =
                response.data['choices']?.[0]?.['delta']?.['reasoning_content'];
              if (!responseMessage && !reasoningContent) {
                return;
              }

              // If we have existing reasoning content and now we get normal content, mark reasoning as finished
              const lastMarkdownItem = contents
                .filter(c => c.type === ResponseStreamDataType.MarkdownPart)
                .pop();
              if (
                lastMarkdownItem &&
                lastMarkdownItem.reasoningContent &&
                responseMessage &&
                !lastMarkdownItem.reasoningFinished
              ) {
                lastMarkdownItem.reasoningFinished = true;
              }

              contents.push({
                id: UUID.uuid4(),
                type: ResponseStreamDataType.MarkdownPart,
                content: responseMessage || '',
                reasoningContent: reasoningContent || '',
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
            const patchedArgs = injectTaskTargetNotebook(
              response.data.commandId,
              response.data.args,
              taskTargetNotebookPathRef.current
            );
            let result = 'void';
            try {
              result = await app.commands.execute(
                response.data.commandId,
                patchedArgs
              );
            } catch (error) {
              result = `Error executing command: ${error}`;
            }

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
              id: responseMessageId,
              date: new Date(),
              from: 'copilot',
              contents: contents,
              participant: NBIAPI.config.chatParticipants.find(participant => {
                return participant.id === response.participant;
              }),
              chatModel: getActiveChatModel()
            }
          ]);
        }
      }
    );

    if (!isAutoSubmit) {
      const newPrompt = '';
      setPrompt(newPrompt);
      filterPrefixSuggestions(newPrompt);
    }

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

  // Refresh the ref so listeners registered with stable identity (e.g.
  // addOutputContextHandler) always invoke the latest closure of submit
  // and see current chat state.
  const handleUserInputSubmitRef = useRef(handleUserInputSubmit);
  handleUserInputSubmitRef.current = handleUserInputSubmit;

  const handleUserInputCancel = async () => {
    NBIAPI.sendWebSocketMessage(
      lastMessageId.current,
      RequestDataType.CancelChatRequest,
      { chatId }
    );

    lastMessageId.current = '';
    setCopilotRequestInProgress(false);
  };

  const handleFeedback = useCallback(
    (messageId: string, sentiment: 'positive' | 'negative') => {
      setChatMessages(prev =>
        prev.map(m => {
          if (m.id !== messageId) {
            return m;
          }
          const newFeedback = m.feedback === sentiment ? undefined : sentiment;
          return { ...m, feedback: newFeedback };
        })
      );
    },
    []
  );

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

  const handleClaudeSessionResumed = (session: IClaudeSessionInfo) => {
    setShowClaudeSessionPicker(false);
    // Reset local chat view so the user starts from a clean slate in the
    // UI; the Claude Code backend retains the resumed transcript and will
    // answer subsequent prompts with full prior context.
    setChatMessages([
      {
        id: UUID.uuid4(),
        date: new Date(),
        from: 'copilot',
        contents: [
          {
            id: UUID.uuid4(),
            type: ResponseStreamDataType.Markdown,
            content: `Resumed Claude session \`${session.session_id.slice(0, 8)}\`${
              session.preview ? ` \u2014 _${session.preview}_` : ''
            }.`,
            created: new Date()
          }
        ]
      }
    ]);
    setPrompt('');
    setSelectedContextFiles([]);
    setShowWorkspaceFilePicker(false);
    resetChatId();
    resetPrefixSuggestions();
    setPromptHistory([]);
    setPromptHistoryIndex(0);
    setPermissionMode(NBIAPI.config.claudePermissionDefaultMode);
  };

  const onPromptKeyDown = async (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
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
      setShowWorkspaceFilePicker(false);
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
    setShowWorkspaceFilePicker(false);
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
      // Consolidated here so every entrypoint (sidebar input, toolbar
      // popovers, extension-fired prompts) uses the selector's mode.
      request.permissionMode = permissionModeRef.current;
      let message = '';
      switch (request.type) {
        case RunChatCompletionType.ExplainThis:
          message = `Explain this code:\n\`\`\`\n${request.content}\n\`\`\`\n`;
          break;
        case RunChatCompletionType.FixThis:
          message = `Fix this code:\n\`\`\`\n${request.content}\n\`\`\`\n`;
          break;
        case RunChatCompletionType.NotebookGeneration:
          // The notebook-toolbar popover already prefixed the prompt; pass it
          // through verbatim so the message displayed in chat matches what
          // was sent to the backend.
          message = request.content;
          // Notebook generation requires agent mode with the notebook-edit
          // and notebook-execute toolsets — without them the LLM can't
          // actually mutate cells regardless of how it answers. Override
          // whatever the user has in the sidebar so the toolbar button
          // works out-of-the-box in non-Claude modes too (issue #229).
          request.chatMode = 'agent';
          request.toolSelections = {
            builtinToolsets: [
              BuiltinToolsetType.NotebookEdit,
              BuiltinToolsetType.NotebookExecute
            ],
            mcpServers: {},
            extensions: {}
          };
          break;
      }
      const messageId = UUID.uuid4();
      request.messageId = messageId;
      request.content = message;
      const externalRequestId = request.externalRequestId;
      const emitProgress = (inProgress: boolean, error?: string) => {
        if (!externalRequestId) {
          return;
        }
        const detail: INotebookGenerationProgressDetail = {
          requestId: externalRequestId,
          inProgress
        };
        if (error) {
          detail.error = error;
        }
        document.dispatchEvent(
          new CustomEvent(NOTEBOOK_GENERATION_PROGRESS_EVENT, { detail })
        );
      };
      emitProgress(true);
      // Snapshot the notebook the agent should target for this externally-
      // triggered request (e.g., notebook-toolbar generation). The
      // `RunUICommand` handler below uses this for the same tab-switch
      // resilience covered by the main submit flow (issue #252).
      const externalActiveDocInfo = props.getActiveDocumentInfo();
      taskTargetNotebookPathRef.current =
        externalActiveDocInfo?.filePath?.endsWith('.ipynb')
          ? externalActiveDocInfo.filePath
          : null;
      request.language = request.language || externalActiveDocInfo?.language;
      request.kernelName =
        request.kernelName || externalActiveDocInfo?.kernelName;
      request.kernelDisplayName =
        request.kernelDisplayName || externalActiveDocInfo?.kernelDisplayName;
      const hideInChat = !!request.hideInChat;
      const newList = hideInChat
        ? chatMessages
        : [
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
      if (!hideInChat) {
        setChatMessages(newList);
        setCopilotRequestInProgress(true);
      }

      const contents: IChatMessageContent[] = [];
      // Stable id for this turn's response message; see the matching note in
      // handleUserInputSubmit. Reused on every delta so the message keeps one
      // React key and the response subtree is not remounted each chunk (#363).
      const responseMessageId = UUID.uuid4();

      submitCompletionRequest(request, {
        emit: async response => {
          if (response.type === BackendMessageType.StreamMessage) {
            const delta = response.data['choices']?.[0]?.['delta'];
            if (!delta) {
              return;
            }

            if (delta['nbiContent']) {
              const nbiContent = delta['nbiContent'];
              if (nbiContent.type === ResponseStreamDataType.ToolCall) {
                // A tool call streams twice under one id (start, then finish);
                // merge by id so it stays one persistent card. See
                // upsertToolCallContent.
                upsertToolCallContent(
                  contents,
                  nbiContent.content,
                  new Date(response.created)
                );
              } else {
                contents.push({
                  id: UUID.uuid4(),
                  type: nbiContent.type,
                  content: nbiContent.content || '',
                  reasoningContent: nbiContent.reasoning_content || '',
                  reasoningTag: nbiContent.reasoning_content
                    ? '<think>'
                    : undefined,
                  reasoningFinished:
                    nbiContent.type === ResponseStreamDataType.Markdown &&
                    nbiContent.reasoning_content
                      ? true
                      : false,
                  contentDetail: nbiContent.detail,
                  created: new Date(response.created)
                });
              }
            } else {
              const responseMessage =
                response.data['choices']?.[0]?.['delta']?.['content'];
              const reasoningContent =
                response.data['choices']?.[0]?.['delta']?.['reasoning_content'];
              if (!responseMessage && !reasoningContent) {
                return;
              }

              // If we have existing reasoning content and now we get normal content, mark reasoning as finished
              const lastMarkdownItem = contents
                .filter(c => c.type === ResponseStreamDataType.MarkdownPart)
                .pop();
              if (
                lastMarkdownItem &&
                lastMarkdownItem.reasoningContent &&
                responseMessage &&
                !lastMarkdownItem.reasoningFinished
              ) {
                lastMarkdownItem.reasoningFinished = true;
              }

              contents.push({
                id: response.id,
                type: ResponseStreamDataType.MarkdownPart,
                content: responseMessage || '',
                reasoningContent: reasoningContent || '',
                created: new Date(response.created)
              });
            }
          } else if (response.type === BackendMessageType.StreamEnd) {
            if (!hideInChat) {
              setCopilotRequestInProgress(false);
            }
            emitProgress(false);
          } else if (response.type === BackendMessageType.RunUICommand) {
            const runUiMessageId = response.id;
            const patchedArgs = injectTaskTargetNotebook(
              response.data.commandId,
              response.data.args,
              taskTargetNotebookPathRef.current
            );
            let result = 'void';
            try {
              result = await props
                .getApp()
                .commands.execute(response.data.commandId, patchedArgs);
            } catch (error) {
              result = `Error executing command: ${error}`;
            }

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
              runUiMessageId,
              RequestDataType.RunUICommandResponse,
              data
            );
          }
          if (hideInChat) {
            return;
          }
          setChatMessages([
            ...newList,
            {
              id: responseMessageId,
              date: new Date(),
              from: 'copilot',
              contents: contents,
              participant: NBIAPI.config.chatParticipants.find(participant => {
                return participant.id === response.participant;
              }),
              chatModel: getActiveChatModel()
            }
          ]);
        }
      });
    },
    [chatMessages, chatMode]
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

  // copilotSidebar:focusPrompt is dispatched from the global keybinding
  // (Ctrl/Cmd+Shift+L) registered in index.ts. activateById can take
  // several frames to mount the sidebar when it was collapsed, so the
  // handler retries up to ~10 frames waiting for the textarea ref to be
  // populated and on-screen before calling focus(). One frame isn't
  // enough on a cold-open of the left rail.
  useEffect(() => {
    const handler = () => {
      let attempts = 0;
      const MAX_ATTEMPTS = 10;
      const tryFocus = () => {
        const el = promptInputRef.current;
        if (el && el.offsetParent !== null) {
          el.focus();
          return;
        }
        attempts += 1;
        if (attempts < MAX_ATTEMPTS) {
          requestAnimationFrame(tryFocus);
        }
      };
      tryFocus();
    };
    document.addEventListener('copilotSidebar:focusPrompt', handler);
    return () =>
      document.removeEventListener('copilotSidebar:focusPrompt', handler);
  }, []);

  const addOutputContextHandler = useCallback((eventData: any) => {
    const detail = eventData?.detail;
    if (!detail || !detail.outputContext) {
      return;
    }
    const cellIndex: number | undefined = detail.cellIndex;
    const notebookFilename: string | undefined = detail.notebookFilename;
    const cellId: string | undefined = detail.cellId;
    const autoSubmitPrompt: string | undefined = detail.autoSubmitPrompt;
    // Cell IDs are stable across cell moves/renames, so two right-clicks on
    // the same cell collapse to one attachment. Fall back to the (notebook,
    // index) tuple only when the platform doesn't expose an ID.
    const path = cellId
      ? `nbi://output/cell/${cellId}`
      : `nbi://output/${notebookFilename ?? 'notebook'}/${cellIndex ?? 0}`;

    const attached: ISelectedContextFile = {
      content: '',
      lineCount: 0,
      path,
      type: 'output',
      outputContext: detail.outputContext as IOutputContextItem,
      cellIndex,
      notebookFilename
    };

    setSelectedContextFiles(prev => {
      if (prev.some(file => file.path === path)) {
        return prev;
      }
      if (prev.length >= MAX_ATTACHED_FILES) {
        return prev;
      }
      return [...prev, attached];
    });

    if (autoSubmitPrompt) {
      handleUserInputSubmitRef.current({
        promptOverride: autoSubmitPrompt,
        extraOutputContext: attached
      });
    }
  }, []);

  useEffect(() => {
    document.addEventListener(
      'copilotSidebar:addOutputContext',
      addOutputContextHandler
    );
    return () => {
      document.removeEventListener(
        'copilotSidebar:addOutputContext',
        addOutputContextHandler
      );
    };
  }, [addOutputContextHandler]);

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

  useEffect(() => {
    if (!showWorkspaceFilePicker) {
      // Abandon any in-flight scan so its terminal setState calls don't
      // land on a closed picker (or, if the user re-opens, on a new
      // generation's render).
      workspaceScanGenerationRef.current += 1;
      workspaceFilesLoadingRef.current = false;
      setWorkspaceFilesLoading(false);
      setWorkspaceFilesError('');
      setWorkspaceFileSearch('');
    }
  }, [showWorkspaceFilePicker]);

  // Abandon any in-flight scan on unmount; setState after unmount is a
  // React anti-pattern and the parallel BFS makes the race window wider.
  useEffect(
    () => () => {
      workspaceScanGenerationRef.current += 1;
    },
    []
  );

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

  const [ghLoginRequired, setGHLoginRequired] = useState(
    NBIAPI.getGHLoginRequired()
  );
  const [chatEnabled, setChatEnabled] = useState(NBIAPI.getChatEnabled());
  const [skillsReloadedVisible, setSkillsReloadedVisible] = useState(false);
  // Visible for a few seconds after the user starts a new chat session
  // (either via the header button or `/clear`). The aria-live region
  // below announces it to assistive tech.
  const [newChatNoticeVisible, setNewChatNoticeVisible] = useState(false);
  const newChatNoticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );

  useEffect(() => {
    return () => {
      if (newChatNoticeTimerRef.current) {
        clearTimeout(newChatNoticeTimerRef.current);
      }
    };
  }, []);

  const startNewChatSession = useCallback(() => {
    // Reset every piece of per-conversation UI state and tell the server
    // to drop its conversation history. Functionally equivalent to typing
    // `/clear`, but reachable from the header button so the user doesn't
    // have to remember the slash command (issue #237). Also useful when
    // the Claude SDK client is wedged — restarting the session reconnects
    // the agent.
    if (copilotRequestInProgress) {
      // Cancel any in-flight response before clearing local state. Without
      // this, stream deltas tied to the old messageId keep arriving against
      // an empty chat-messages list and silently re-populate it from the
      // old conversation.
      NBIAPI.sendWebSocketMessage(
        lastMessageId.current,
        RequestDataType.CancelChatRequest,
        { chatId }
      );
      lastMessageId.current = '';
      setCopilotRequestInProgress(false);
    }
    setChatMessages([]);
    setPrompt('');
    setSelectedContextFiles([]);
    setShowWorkspaceFilePicker(false);
    resetChatId();
    resetPrefixSuggestions();
    setPromptHistory([]);
    setPromptHistoryIndex(0);
    NBIAPI.sendWebSocketMessage(
      UUID.uuid4(),
      RequestDataType.ClearChatHistory,
      {
        chatId
      }
    );
    setNewChatNoticeVisible(true);
    if (newChatNoticeTimerRef.current) {
      clearTimeout(newChatNoticeTimerRef.current);
    }
    newChatNoticeTimerRef.current = setTimeout(() => {
      setNewChatNoticeVisible(false);
      newChatNoticeTimerRef.current = null;
    }, 3000);
    // Move focus to the prompt textarea so the user can immediately type
    // their first message in the fresh session. Defer past the React
    // commit so the input has re-rendered with the cleared prompt value.
    window.requestAnimationFrame(() => {
      promptInputRef.current?.focus();
    });
  }, [chatId, copilotRequestInProgress, resetChatId, resetPrefixSuggestions]);

  useEffect(() => {
    const handler = () => {
      setGHLoginRequired(NBIAPI.getGHLoginRequired());
      setChatEnabled(NBIAPI.getChatEnabled());
    };
    NBIAPI.configChanged.connect(handler);
    return () => {
      NBIAPI.configChanged.disconnect(handler);
    };
  }, []);

  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout> | null = null;
    const listener = () => {
      setSkillsReloadedVisible(true);
      if (timeout) {
        clearTimeout(timeout);
      }
      timeout = setTimeout(() => {
        setSkillsReloadedVisible(false);
      }, 4000);
    };
    NBIAPI.skillsReloaded.connect(listener);
    return () => {
      NBIAPI.skillsReloaded.disconnect(listener);
      if (timeout) {
        clearTimeout(timeout);
      }
    };
  }, []);

  useEffect(() => {
    setGHLoginRequired(NBIAPI.getGHLoginRequired());
    setChatEnabled(NBIAPI.getChatEnabled());
  }, [ghLoginStatus]);

  return (
    <div
      ref={sidebarRootRef}
      className={`sidebar${isDragOver ? ' drag-over' : ''}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Visually-hidden skip link as the first focusable child of the
          sidebar. After an assistant reply lands, keyboard users would
          otherwise have to Tab through every code block, copy / insert
          button, and feedback button before reaching the prompt; this
          shortcut lands them straight on the textarea. The link is
          revealed only when focused (the .nbi-skip-link CSS) so it
          stays out of the sighted-user's way. Rendered only when
          chatEnabled is true so the target textarea exists. */}
      {chatEnabled && (
        <a
          href="#sidebar-user-input"
          className="nbi-sr-only nbi-skip-link"
          onClick={event => {
            event.preventDefault();
            promptInputRef.current?.focus();
          }}
        >
          Skip to message input
        </a>
      )}
      {isDragOver && (
        <div className="drop-zone-overlay">
          <span>Drop files to attach as context</span>
        </div>
      )}
      {tourVisible && <TourOverlay onClose={() => setTourVisible(false)} />}
      <div className="sidebar-header">
        <div className="sidebar-title">Notebook Intelligence</div>
        {NBIAPI.config.isInClaudeCodeMode && (
          <>
            <button
              type="button"
              className="user-input-footer-button"
              data-tour-id={TOUR_ANCHOR.newChat}
              onClick={() => startNewChatSession()}
              title="Start a new chat session (restarts the Claude client)"
              aria-label="Start a new chat session"
            >
              <VscAdd />
            </button>
            <button
              type="button"
              className="user-input-footer-button"
              data-tour-id={TOUR_ANCHOR.claudeHistory}
              onClick={() => setShowClaudeSessionPicker(true)}
              aria-label="Resume previous Claude session"
              title="Resume a Claude session you started earlier in this workspace"
            >
              <VscHistory />
            </button>
          </>
        )}
        <button
          type="button"
          className="user-input-footer-button"
          data-tour-id={TOUR_ANCHOR.settingsGear}
          onClick={() => handleSettingsButtonClick()}
          aria-label="Open Notebook Intelligence settings"
          title="Configure providers, API keys, MCP servers, and skills"
        >
          <VscSettingsGear />
        </button>
      </div>
      <div className="nbi-status-banner-live" aria-live="polite">
        {skillsReloadedVisible && (
          <div className="nbi-status-banner">
            Skills reloaded — applied to the current session.
          </div>
        )}
        {newChatNoticeVisible && (
          <div className="nbi-status-banner">New chat session started.</div>
        )}
      </div>
      {/* sr-only polite region for chat-status boundary announcements.
          The string toggles on copilotRequestInProgress transitions so
          screen readers announce "Generating response." when a stream
          starts and "Response complete." when it ends, without
          re-announcing every streamed token. */}
      <div className="nbi-sr-only" role="status" aria-live="polite">
        {chatStatusAnnouncement}
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
      {!NBIAPI.config.isInClaudeCodeMode && ghLoginRequired && (
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
          <div
            className="sidebar-messages"
            role="log"
            aria-label="Chat transcript"
          >
            <div className="sidebar-greeting">
              Welcome! How can I assist you today?
            </div>
          </div>
        ) : (
          <div
            className="sidebar-messages"
            role="log"
            aria-label="Chat transcript"
          >
            {chatMessages.map((msg, index) => {
              // Only the most recent copilot message owns the live
              // progress-feedback state. Non-active messages receive
              // stable primitives so React.memo can prune them; otherwise
              // the 1Hz elapsed-time tick would re-render every message
              // in the chat history every second.
              const isActiveCopilotMessage =
                index === chatMessages.length - 1 &&
                msg.from === 'copilot' &&
                copilotRequestInProgress;
              return (
                <MemoizedChatResponse
                  key={msg.id}
                  message={msg}
                  openFile={props.openFile}
                  getApp={props.getApp}
                  getActiveDocumentInfo={props.getActiveDocumentInfo}
                  showGenerating={isActiveCopilotMessage}
                  elapsedSeconds={isActiveCopilotMessage ? elapsedSeconds : 0}
                  heartbeatTick={isActiveCopilotMessage ? heartbeatTick : 0}
                  isStalled={isActiveCopilotMessage ? isStalled : false}
                  onFeedback={handleFeedback}
                  chatId={chatId}
                  telemetryEmitter={telemetryEmitter}
                />
              );
            })}
            <div ref={messagesEndRef} />
          </div>
        ))}
      {chatEnabled && (
        <div
          id="sidebar-user-input"
          data-tour-id={TOUR_ANCHOR.promptInput}
          className={`sidebar-user-input ${copilotRequestInProgress ? 'generating' : ''}`}
        >
          <textarea
            ref={promptInputRef}
            rows={3}
            onChange={onPromptChange}
            onKeyDown={onPromptKeyDown}
            onPaste={handlePaste}
            placeholder="Ask Notebook Intelligence..."
            spellCheck={false}
            value={prompt}
          />
          {(activeDocumentInfo?.filename ||
            selectedContextFiles.length > 0 ||
            isUploadingFiles) && (
            <div className="user-input-context-row">
              {activeDocumentInfo?.filename && (
                <div
                  className={`user-input-context user-input-context-active-file ${contextOn ? 'on' : 'off'}`}
                >
                  <div>{currentFileContextTitle}</div>
                  <button
                    type="button"
                    className="user-input-context-toggle"
                    onClick={() => setContextOn(!contextOn)}
                    aria-label={
                      contextOn
                        ? 'Stop using current file as context'
                        : 'Use current file as context'
                    }
                    aria-pressed={contextOn}
                    title={
                      contextOn ? 'Use as context' : "Don't use as context"
                    }
                  >
                    {contextOn ? (
                      <VscEye aria-hidden="true" />
                    ) : (
                      <VscEyeClosed aria-hidden="true" />
                    )}
                  </button>
                </div>
              )}
              {selectedContextFiles.map(file => {
                const isOutput = !!file.outputContext;
                const cellLabel =
                  typeof file.cellIndex === 'number'
                    ? `Cell ${file.cellIndex + 1} output`
                    : 'Cell output';
                const label = isOutput
                  ? file.notebookFilename
                    ? `${cellLabel} (${file.notebookFilename})`
                    : cellLabel
                  : file.path;
                const titleText = isOutput
                  ? label
                  : file.source === 'upload'
                    ? `Uploaded: ${file.path}`
                    : file.path;
                return (
                  <div
                    key={file.serverPath ?? file.path}
                    className={`user-input-context user-input-context-selected-file on${file.source === 'upload' ? ' uploaded-file' : ''}${file.isImage ? ' image-file' : ''}${isOutput ? ' output-context' : ''}`}
                    title={titleText}
                  >
                    <div>
                      {file.isImage && file.imageDataUrl ? (
                        <>
                          <span className="context-pill-thumbnail-wrap">
                            <img
                              src={file.imageDataUrl}
                              className="context-pill-thumbnail"
                              alt={file.path}
                            />
                            <img
                              src={file.imageDataUrl}
                              className="context-pill-thumbnail-preview"
                              alt=""
                              aria-hidden="true"
                            />
                          </span>
                          {file.path}
                        </>
                      ) : file.source === 'upload' ? (
                        <>
                          <VscCloudUpload /> {file.path}
                        </>
                      ) : (
                        label
                      )}
                    </div>
                    <button
                      type="button"
                      className="user-input-context-toggle"
                      onClick={event => {
                        // The row this button lives in is about to disappear
                        // from the DOM, which would otherwise drop focus to
                        // ``<body>``. Hand focus to the next remove button
                        // in the row (preferred) or back to the textarea so
                        // keyboard users keep their place.
                        const target = event.currentTarget;
                        const row =
                          target.closest('.user-input-context-row') ?? null;
                        const buttons = row
                          ? Array.from(
                              row.querySelectorAll<HTMLButtonElement>(
                                'button.user-input-context-toggle'
                              )
                            )
                          : [];
                        const idx = buttons.indexOf(target);
                        const next = buttons[idx + 1] ?? buttons[idx - 1];
                        removeSelectedContextFile(file.serverPath ?? file.path);
                        // Defer the focus move past the React re-render that
                        // unmounts ``target``.
                        window.requestAnimationFrame(() => {
                          if (next && document.contains(next)) {
                            next.focus();
                          } else {
                            promptInputRef.current?.focus();
                          }
                        });
                      }}
                      aria-label={`Remove attached file ${label}`}
                      title="Remove attached file"
                    >
                      <VscClose aria-hidden="true" />
                    </button>
                  </div>
                );
              })}
              {isUploadingFiles && (
                // The trailing-dots animation runs entirely in CSS
                // (`.loading-ellipsis::after`), so the live region's DOM
                // text stays the literal string "Uploading" and screen
                // readers announce it exactly once on insertion — not on
                // every dot tick. If a future change moves the dots into
                // React state, restore the once-announce behavior with a
                // separate sr-only label.
                <div
                  className="user-input-context uploading-indicator"
                  role="status"
                  aria-live="polite"
                  aria-atomic="true"
                  aria-busy="true"
                >
                  <div className="loading-ellipsis">Uploading</div>
                </div>
              )}
            </div>
          )}
          <div className="user-input-footer">
            {chatMode === 'ask' && (
              <button
                type="button"
                ref={atButtonRef}
                data-tour-id={TOUR_ANCHOR.slashCommands}
                className="user-input-footer-button user-input-footer-slash-button"
                onClick={() => {
                  if (!showPopover) {
                    // D030: remember the button so focus returns to it
                    // on close. Capture before the state flip so we
                    // record the click target, not whatever the focus
                    // shift below leaves behind. (Pure: side effects
                    // belong outside the state updater.)
                    slashPopoverOpenerRef.current = atButtonRef.current;
                  }
                  setShowPopover(prev => !prev);
                  promptInputRef.current?.focus();
                }}
                title="Slash commands"
                aria-label="Open slash commands"
              >
                /
              </button>
            )}
            <button
              type="button"
              data-tour-id={TOUR_ANCHOR.addContext}
              className={`user-input-footer-button ${selectedContextFiles.length > 0 ? 'tools-button tools-button-active' : ''}`}
              onClick={() => handleWorkspaceFilePickerClick()}
              title="Add workspace file as context"
              aria-label="Add workspace file as context"
            >
              <VscFile />
              {selectedContextFiles.length > 0 && (
                <>{selectedContextFiles.length}</>
              )}
            </button>
            <button
              type="button"
              data-tour-id={TOUR_ANCHOR.uploadFile}
              className="user-input-footer-button"
              onClick={() => fileInputRef.current?.click()}
              title="Upload file from computer"
              aria-label="Upload file from computer"
            >
              <VscCloudUpload />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              style={{ display: 'none' }}
              onChange={handleFileInputChange}
            />
            <div style={{ flexGrow: 1 }}></div>
            <div className="chat-mode-widgets-container">
              {!NBIAPI.config.isInClaudeCodeMode && (
                <div data-tour-id={TOUR_ANCHOR.chatMode}>
                  <select
                    className="chat-mode-select"
                    title="Chat mode"
                    value={chatMode}
                    onChange={event => {
                      if (event.target.value === 'ask') {
                        setToolSelections(toolSelectionsEmpty);
                      }
                      setShowModeTools(false);
                      setChatMode(event.target.value);
                    }}
                  >
                    <option value="ask">Ask</option>
                    <option value="agent">Agent</option>
                  </select>
                </div>
              )}
              {chatMode !== 'ask' && !NBIAPI.config.isInClaudeCodeMode && (
                <button
                  type="button"
                  className={`user-input-footer-button tools-button ${unsafeToolSelected ? 'tools-button-warning' : selectedToolCount > 0 ? 'tools-button-active' : ''}`}
                  onClick={() => handleChatToolsButtonClick()}
                  title={
                    unsafeToolSelected
                      ? `Tool selection can cause irreversible changes! Review each tool execution carefully.\n${toolSelectionTitle}`
                      : toolSelectionTitle
                  }
                  aria-label={
                    unsafeToolSelected
                      ? 'Configure tools (warning: irreversible tools selected)'
                      : 'Configure tools'
                  }
                >
                  <VscTools />
                  {selectedToolCount > 0 && <>{selectedToolCount}</>}
                </button>
              )}
              {NBIAPI.config.isInClaudeCodeMode && (
                <PermissionModeSelect
                  value={permissionMode}
                  bypassAllowed={bypassPermissionsAllowed}
                  onModeChange={setPermissionMode}
                />
              )}
              {NBIAPI.config.isInClaudeCodeMode && (
                <span
                  title="Claude mode"
                  className="claude-icon"
                  dangerouslySetInnerHTML={{ __html: claudeSvgStr }}
                ></span>
              )}
            </div>
            <div>
              <button
                type="button"
                className={`jp-Dialog-button jp-mod-styled send-button${
                  copilotRequestInProgress
                    ? ' jp-mod-warn send-button-stop'
                    : ' jp-mod-accept'
                }`}
                onClick={() => handleSubmitStopChatButtonClick()}
                disabled={prompt.length === 0 && !copilotRequestInProgress}
                aria-label={
                  copilotRequestInProgress ? 'Stop generating' : 'Send message'
                }
                title={
                  copilotRequestInProgress ? 'Stop generating' : 'Send message'
                }
              >
                {copilotRequestInProgress ? (
                  <VscStopCircle aria-hidden="true" />
                ) : (
                  <VscSend aria-hidden="true" />
                )}
              </button>
            </div>
          </div>
          {showPopover && prefixSuggestions.length > 0 && (
            <div className="user-input-autocomplete" ref={autocompleteRef}>
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
          {showClaudeSessionPicker && (
            <ClaudeSessionPicker
              onResume={handleClaudeSessionResumed}
              onClose={() => setShowClaudeSessionPicker(false)}
            />
          )}
          {showWorkspaceFilePicker && (
            <div
              ref={workspaceFilePopoverRef}
              className="workspace-file-popover"
              tabIndex={-1}
              role="dialog"
              aria-labelledby="nbi-workspace-popover-title"
              onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
                if (event.key === 'Escape') {
                  event.stopPropagation();
                  event.preventDefault();
                  setShowWorkspaceFilePicker(false);
                }
              }}
            >
              <div className="mode-tools-popover-header">
                <div className="mode-tools-popover-header-icon">
                  <VscAdd />
                </div>
                <div
                  className="mode-tools-popover-title"
                  id="nbi-workspace-popover-title"
                >
                  Add files as context
                </div>
                <div style={{ flexGrow: 1 }}></div>
                <button
                  type="button"
                  className={
                    'mode-tools-popover-button mode-tools-popover-refresh-button' +
                    (workspaceFilesLoading ? ' is-loading' : '')
                  }
                  title="Refresh file list"
                  aria-label="Refresh workspace file list"
                  aria-busy={workspaceFilesLoading}
                  aria-disabled={workspaceFilesLoading}
                  onClick={() => {
                    if (!workspaceFilesLoading) {
                      refreshWorkspaceFiles();
                    }
                  }}
                >
                  <VscRefresh />
                </button>
                <button
                  type="button"
                  className="mode-tools-popover-button mode-tools-popover-close-button"
                  title="Close"
                  aria-label="Close file picker"
                  onClick={() => setShowWorkspaceFilePicker(false)}
                >
                  <VscClose />
                </button>
              </div>
              <div className="workspace-file-popover-body">
                <input
                  className="workspace-file-search-input"
                  type="text"
                  placeholder="Search files by path"
                  value={workspaceFileSearch}
                  onChange={event => setWorkspaceFileSearch(event.target.value)}
                  onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
                    // Let Escape bubble to the popover's Escape handler so
                    // the dialog closes even when focus is in the search
                    // input (issue #262). Other keys still stop here so the
                    // chat sidebar's keyboard shortcuts don't fire while
                    // the user is typing a filter.
                    if (event.key !== 'Escape') {
                      event.stopPropagation();
                    }
                  }}
                />
                {workspaceFilesError && (
                  <div className="workspace-file-popover-status error">
                    {workspaceFilesError}
                  </div>
                )}
                {workspaceScanLimitReached && (
                  <div className="workspace-file-popover-status">
                    Showing the first {MAX_WORKSPACE_FILE_SCAN_COUNT} files
                    found in the workspace.
                  </div>
                )}
                {workspaceFilesLoading ? (
                  <div className="workspace-file-popover-status">
                    Loading workspace files...
                  </div>
                ) : visibleWorkspaceFiles.length > 0 ? (
                  <div className="mode-tools-popover-tool-list">
                    {visibleWorkspaceFiles.map(file => (
                      <CheckBoxItem
                        key={file.path}
                        checked={selectedContextFilePaths.has(file.path)}
                        disabled={workspaceFileActionPath === file.path}
                        label={file.path}
                        onClick={() => handleWorkspaceFileSelection(file)}
                        tooltip={
                          file.type === 'notebook'
                            ? 'Notebook file'
                            : 'Text file'
                        }
                      />
                    ))}
                  </div>
                ) : (
                  <div className="workspace-file-popover-status">
                    {workspaceFilesLoaded
                      ? 'No matching files found.'
                      : 'No workspace files available.'}
                  </div>
                )}
              </div>
            </div>
          )}
          {showModeTools && (
            <div
              ref={modeToolsPopoverRef}
              className="mode-tools-popover"
              tabIndex={-1}
              role="dialog"
              aria-labelledby="nbi-mode-tools-popover-title"
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
                <div
                  className="mode-tools-popover-title"
                  id="nbi-mode-tools-popover-title"
                >
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
                    <button
                      type="button"
                      className="link-button"
                      onClick={onClearToolsButtonClicked}
                    >
                      clear
                    </button>
                  </div>
                </div>
                <button
                  type="button"
                  className="mode-tools-popover-button mode-tools-popover-done-button"
                  aria-label="Close tools picker"
                  onClick={() => setShowModeTools(false)}
                >
                  <div>
                    <VscPassFilled />
                  </div>
                  <div>Done</div>
                </button>
              </div>
              <div className="mode-tools-popover-tool-list">
                <div className="mode-tools-group-header">Built-in</div>
                <div className="mode-tools-group mode-tools-group-built-in">
                  {toolConfigRef.current.builtinToolsets.map((toolset: any) => (
                    <CheckBoxItem
                      key={toolset.id}
                      label={toolset.name}
                      checked={getBuiltinToolsetState(toolset.id)}
                      tooltip={toolset.description}
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

export function InlinePopoverComponent(props: any) {
  const [modifiedCode, setModifiedCode] = useState<string>('');
  const [promptSubmitted, setPromptSubmitted] = useState(false);
  // Tracks the in-flight backend request so Escape / Cancel / Accept can
  // stop it via CancelChatRequest. Cleared on StreamEnd so a stale cancel
  // doesn't fire after the response already finished.
  const inflightMessageIdRef = useRef<string | null>(null);
  // Mirrors InlinePromptWidget._streamError so this component can branch
  // on it independently. Set when the backend tags a delta with
  // nbi_stream_error; cleared on the next submit / StreamEnd.
  const streamErrorRef = useRef<string | null>(null);
  const originalOnRequestSubmitted = props.onRequestSubmitted;
  const originalOnResponseEmit = props.onResponseEmit;
  const originalOnRequestCancelled = props.onRequestCancelled;
  const originalOnUpdatedCodeAccepted = props.onUpdatedCodeAccepted;

  const cancelInflightRequest = () => {
    const messageId = inflightMessageIdRef.current;
    if (!messageId) {
      return;
    }
    // Backend matches cancellations by websocket message id (see
    // WebsocketCopilotHandler.on_message). Without this send the request
    // keeps streaming server-side after the popover dismisses, burning
    // tokens and wasting an inference.
    NBIAPI.sendWebSocketMessage(
      messageId,
      RequestDataType.CancelChatRequest,
      {}
    );
    inflightMessageIdRef.current = null;
  };

  // Hand the cancel function up to the host so non-React dismissal paths
  // (e.g. outside-click on the block widget) can cancel without invoking
  // onRequestCancelled — that path also restores editor focus, which is
  // wrong when the user is mid-click on a different target.
  useEffect(() => {
    props.registerCancel?.(cancelInflightRequest);
    return () => props.registerCancel?.(null);
  }, []);

  const onRequestSubmitted = (prompt: string) => {
    setModifiedCode('');
    setPromptSubmitted(true);
    streamErrorRef.current = null;
    originalOnRequestSubmitted(prompt);
  };

  const onResponseEmit = (response: any) => {
    if (response.type === BackendMessageType.StreamMessage) {
      if (typeof response.data?.nbi_stream_error === 'string') {
        streamErrorRef.current = response.data.nbi_stream_error;
      }
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
      // Only fence-strip on a clean stream. On error the marker has to
      // stay visible in the diff pane so the modify-existing user has a
      // persistent failure signal before deciding to Accept the
      // truncated result.
      if (!streamErrorRef.current) {
        setModifiedCode((modifiedCode: string) =>
          extractLLMGeneratedCode(modifiedCode)
        );
      }
      // streamErrorRef intentionally outlives StreamEnd: Accept fires
      // afterwards and needs to know the stream errored so it can
      // dismiss instead of writing an empty buffer over the user's
      // selection. Cleared on the next submit (see onRequestSubmitted).
      inflightMessageIdRef.current = null;
    }

    originalOnResponseEmit(response);
  };

  const onRequestCancelled = () => {
    cancelInflightRequest();
    originalOnRequestCancelled();
  };

  // Accept on a partial diff used to leave the backend stream running
  // off-screen, spending tokens on output the UI no longer used. Cancel
  // the in-flight request before applying so a mid-stream Accept
  // releases the upstream call too. When the stream errored the buffer
  // is at best truncated and at worst marker-only, which would write an
  // empty string over the user's selection — extractLLMGeneratedCode
  // strips the marker and the remainder is just whitespace. Treat
  // Accept as cancel in that case so the selection survives.
  const onUpdatedCodeAccepted = () => {
    if (streamErrorRef.current) {
      onRequestCancelled();
      return;
    }
    cancelInflightRequest();
    originalOnUpdatedCodeAccepted();
  };

  return (
    <div className="inline-popover">
      <InlinePromptComponent
        {...props}
        onRequestSubmitted={onRequestSubmitted}
        onResponseEmit={onResponseEmit}
        onRequestCancelled={onRequestCancelled}
        onMessageIdChange={(id: string) => {
          inflightMessageIdRef.current = id;
        }}
        onUpdatedCodeAccepted={onUpdatedCodeAccepted}
        limitHeight={props.existingCode !== '' && promptSubmitted}
      />
      {props.existingCode !== '' && promptSubmitted && (
        <>
          <InlineDiffViewerComponent {...props} modifiedCode={modifiedCode} />
          <div className="inline-popover-footer">
            <div>
              <button
                className="jp-Button jp-mod-accept jp-mod-styled jp-mod-small"
                onClick={() => onUpdatedCodeAccepted()}
              >
                Accept
              </button>
            </div>
            <div>
              <button
                className="jp-Button jp-mod-reject jp-mod-styled jp-mod-small"
                onClick={() => onRequestCancelled()}
              >
                Cancel
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
      'text/plain'
    );
    const modifiedModel = monaco.editor.createModel(
      props.modifiedCode,
      'text/plain'
    );

    const editor = monaco.editor.createDiffEditor(editorEl, {
      originalEditable: false,
      automaticLayout: true,
      theme: isDarkTheme() ? 'vs-dark' : 'vs'
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

    const messageId = UUID.uuid4();
    // Hand the id back to the popover so its cancel handler can send a
    // CancelChatRequest with the matching id.
    props.onMessageIdChange?.(messageId);

    submitCompletionRequest(
      {
        messageId,
        chatId: UUID.uuid4(),
        type: RunChatCompletionType.GenerateCode,
        content: prompt,
        language: props.language || 'python',
        kernelName: props.kernelName || '',
        filename: props.filename || '',
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
    event.stopPropagation();

    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (inputSubmitted && (event.metaKey || event.ctrlKey)) {
        props.onUpdatedCodeAccepted();
      } else {
        props.onRequestSubmitted(prompt);
        handleUserInputSubmit();
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      props.onRequestCancelled();
    }
  };

  const focusPromptInput = () => {
    const input = promptInputRef.current;
    if (!input) {
      return;
    }

    input.focus({ preventScroll: true });
    input.select();
  };

  useEffect(() => {
    focusPromptInput();
    const animationFrame = requestAnimationFrame(focusPromptInput);
    const timeout = window.setTimeout(focusPromptInput, 0);

    return () => {
      cancelAnimationFrame(animationFrame);
      window.clearTimeout(timeout);
    };
  }, []);

  return (
    <div
      className="inline-prompt-container"
      style={{ height: props.limitHeight ? '40px' : '100%' }}
    >
      <textarea
        ref={promptInputRef}
        rows={3}
        onChange={onPromptChange}
        onClick={event => {
          event.stopPropagation();
          focusPromptInput();
        }}
        onKeyDown={onPromptKeyDown}
        onMouseDown={event => event.stopPropagation()}
        placeholder="Ask Notebook Intelligence to generate Python code..."
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
            <SafeAnchor href="https://github.com/features/copilot">
              GitHub Copilot
            </SafeAnchor>{' '}
            requires a subscription and it has a free tier. GitHub Copilot is
            subject to the{' '}
            <SafeAnchor href="https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features">
              GitHub Terms for Additional Products and Features
            </SafeAnchor>
            .
          </div>
          <div>
            <h4>Privacy and terms</h4>
            By using Notebook Intelligence with GitHub Copilot subscription you
            agree to{' '}
            <SafeAnchor href="https://docs.github.com/en/copilot/responsible-use-of-github-copilot-features/responsible-use-of-github-copilot-chat-in-your-ide">
              GitHub Copilot chat terms
            </SafeAnchor>
            . Review the terms to understand about usage, limitations and ways
            to improve GitHub Copilot. Please review{' '}
            <SafeAnchor href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">
              Privacy Statement
            </SafeAnchor>
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
                  void writeTextToClipboard(deviceActivationCode);
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
              <SafeAnchor href={deviceActivationURL}>
                {deviceActivationURL}
              </SafeAnchor>{' '}
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

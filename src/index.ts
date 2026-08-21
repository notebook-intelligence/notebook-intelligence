// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin,
  JupyterLab
} from '@jupyterlab/application';

import { IDocumentManager } from '@jupyterlab/docmanager';
import { DocumentWidget, IDocumentWidget } from '@jupyterlab/docregistry';

import {
  Dialog,
  ICommandPalette,
  MainAreaWidget,
  Notification
} from '@jupyterlab/apputils';
import { dispatchShowTour } from './tour/tour-events';
import { resetTour } from './tour/tour-state';
import { TOUR_DEFAULTS } from './tour/tour-steps';
import { commandLabel } from './tour/tour-config';
import { IMainMenu } from '@jupyterlab/mainmenu';

import { IEditorLanguageRegistry } from '@jupyterlab/codemirror';
import { Extension, StateEffect, StateField } from '@codemirror/state';
import {
  Decoration,
  DecorationSet,
  EditorView,
  WidgetType
} from '@codemirror/view';

import { CodeCell } from '@jupyterlab/cells';
import { ISharedNotebook } from '@jupyter/ydoc';

import { ISettingRegistry } from '@jupyterlab/settingregistry';

import {
  CompletionHandler,
  ICompletionProviderManager,
  IInlineCompletionContext,
  IInlineCompletionItem,
  IInlineCompletionList,
  IInlineCompletionProvider,
  IInlineCompleterFactory
} from '@jupyterlab/completer';

import { NotebookActions, NotebookPanel } from '@jupyterlab/notebook';
import { CodeEditor } from '@jupyterlab/codeeditor';
import { FileEditorWidget } from '@jupyterlab/fileeditor';

import { FileBrowserModel, IDefaultFileBrowser } from '@jupyterlab/filebrowser';

import { ContentsManager, KernelSpecManager } from '@jupyterlab/services';

import { LabIcon, terminalIcon } from '@jupyterlab/ui-components';

import { Menu, Panel, Widget } from '@lumino/widgets';
import { CommandRegistry } from '@lumino/commands';
import { IStatusBar } from '@jupyterlab/statusbar';
import { ILauncher } from '@jupyterlab/launcher';
import { IDisposable } from '@lumino/disposable';
import React from 'react';
import { ReactWidget } from '@jupyterlab/apputils';
import { LauncherPicker } from './components/launcher-picker';
import stripAnsi from 'strip-ansi';
import {
  ChatSidebar,
  FormInputDialogBody,
  GitHubCopilotLoginDialogBody,
  IInlinePromptWidgetOptions,
  InlinePopoverComponent,
  GitHubCopilotStatusBarItem,
  RunChatCompletionType
} from './chat-sidebar';
import {
  CellOutputActionFlag,
  NBIAPI,
  GitHubCopilotLoginStatus,
  IClaudeSessionInfo
} from './api';
import { CellOutputHoverToolbar } from './cell-output-toolbar';
import { attachOpenFileRefreshWatcher } from './open-file-refresh-watcher';
import { buildRefreshWatcherEnv } from './open-file-refresh-watcher-env';
import {
  wrapInlineCompleterFactory,
  NBI_INLINE_COMPLETION_PROVIDER_ID
} from './inline-completer-telemetry';
import {
  BackendMessageType,
  GITHUB_COPILOT_PROVIDER_ID,
  IActiveDocumentInfo,
  ICellContents,
  INotebookIntelligence,
  ITelemetryEmitter,
  ITelemetryEvent,
  ITelemetryListener,
  RequestDataType,
  TelemetryEventType
} from './tokens';
import sparklesSvgstr from '../style/icons/sparkles.svg';
import copilotSvgstr from '../style/icons/copilot.svg';
import sparklesWarningSvgstr from '../style/icons/sparkles-warning.svg';
import claudeSvgstr from '../style/icons/claude.svg';
import openaiSvgstr from '../style/icons/openai.svg';
import opencodeSvgstr from '../style/icons/opencode.svg';

import {
  applyCodeToSelectionInEditor,
  cellOutputAsText,
  cellOutputHasError,
  chooseWorkspaceDirectory,
  compareSelections,
  buildResumeCommand,
  extractLLMGeneratedCode,
  getSelectionInEditor,
  getTokenCount,
  getWholeNotebookContent,
  isSelectionEmpty,
  markdownToComment,
  waitForDuration
} from './utils';
import { cellOutputAsContextBundle } from './cell-output-bundle';
import { UUID } from '@lumino/coreutils';

import * as path from 'path';
import { createRoot, Root } from 'react-dom/client';
import { SettingsPanel } from './components/settings-panel';
import { ITerminalConnection } from '@jupyterlab/services/lib/terminal/terminal';
import { ITerminalTracker } from '@jupyterlab/terminal';
import { Token } from '@lumino/coreutils';
import { NotebookGenerationToolbarExtension } from './notebook-generation-toolbar';
import { attachTerminalDragDrop } from './terminal-drag';
import {
  DEFAULT_NOTEBOOK_KERNEL,
  NotebookKernelNotFoundError,
  findKernelProfile,
  listKernelProfiles,
  normalizeNotebookLanguage
} from './notebook-kernels';

import { CommandIDs } from './command-ids';

const addInlinePromptEffect = StateEffect.define<{
  pos: number;
  widget: InlinePromptBlockWidget;
}>();
const removeInlinePromptEffect = StateEffect.define<void>();

const inlinePromptField = StateField.define<DecorationSet>({
  create() {
    return Decoration.none;
  },
  update(decorations, transaction) {
    decorations = decorations.map(transaction.changes);

    for (const effect of transaction.effects) {
      if (effect.is(removeInlinePromptEffect)) {
        decorations = Decoration.none;
      } else if (effect.is(addInlinePromptEffect)) {
        decorations = Decoration.set([
          Decoration.widget({
            block: true,
            side: 1,
            widget: effect.value.widget
          }).range(effect.value.pos)
        ]);
      }
    }

    return decorations;
  },
  provide: field => EditorView.decorations.from(field)
});

class InlinePromptBlockWidget extends WidgetType {
  constructor(
    private readonly _content: React.ReactElement,
    private readonly _onNodeCreated: (node: HTMLElement) => void,
    private readonly _onDismissRequested: () => void
  ) {
    super();
  }

  eq(other: WidgetType): boolean {
    return other === this;
  }

  toDOM(): HTMLElement {
    const host = document.createElement('div');
    host.className = 'nbi-inline-prompt-block';
    host.contentEditable = 'false';

    const node = document.createElement('div');
    node.className = 'inline-prompt-widget inline-prompt-widget-inline';
    node.style.height = '48px';
    host.appendChild(node);

    // Bubble-phase stopPropagation so JupyterLab's document-level
    // keybindings (Ctrl+S, etc.) don't intercept keys typed in the
    // textarea. CM's own handling is already opted out via ignoreEvent;
    // this is purely about JL keymap.
    const stopEditorEvent = (event: Event) => event.stopPropagation();
    for (const eventName of [
      'keydown',
      'keypress',
      'keyup',
      'mousedown',
      'mouseup',
      'click',
      'pointerdown',
      'pointerup',
      'input',
      'beforeinput'
    ]) {
      host.addEventListener(eventName, stopEditorEvent);
    }

    // While the popover is mounted, suppress JupyterLab's notebook-panel
    // _evtFocusIn handler for focus events inside the owning cell. Without
    // this, JL's _ensureFocus reacts to every focusin on the textarea by
    // refocusing .cm-content, which then triggers our own refocus, which
    // re-fires JL — an infinite loop until the stack overflows.
    //
    // Capture-phase listener on document fires before JL's bubble-phase
    // handler on the notebook panel; stopPropagation here is sufficient.
    // The owning .cm-content / .jp-Cell are resolved inside the handler
    // because at toDOM() time the host is created but not yet inserted.
    this._focusinGuard = (event: FocusEvent) => {
      if (!host.isConnected) {
        return;
      }
      const ownCmContent = host.closest('.cm-content');
      if (!ownCmContent) {
        return;
      }
      const ownCell = ownCmContent.closest('.jp-Cell');
      const target = event.target as HTMLElement | null;
      if (!ownCell || !target || !ownCell.contains(target)) {
        return;
      }

      event.stopPropagation();

      if (target === ownCmContent) {
        const ta = node.querySelector('textarea');
        if (ta && document.activeElement !== ta) {
          ta.focus({ preventScroll: true });
        }
      }
    };
    document.addEventListener('focusin', this._focusinGuard, true);

    // Outside-click / focus-leave dismissal. The previous floating-widget
    // path relied on InlinePromptWidget's own focusout listener; the
    // block-widget path mounts the React tree directly so we replicate
    // it here. Focus moving within the popover (textarea -> Accept /
    // Cancel) is ignored.
    host.addEventListener('focusout', (event: FocusEvent) => {
      const next = event.relatedTarget as Node | null;
      if (next && host.contains(next)) {
        return;
      }
      this._onDismissRequested();
    });

    this._root = createRoot(node);
    this._root.render(this._content);
    this._onNodeCreated(node);
    return host;
  }

  ignoreEvent(): boolean {
    return true;
  }

  destroy(): void {
    if (this._focusinGuard) {
      document.removeEventListener('focusin', this._focusinGuard, true);
      this._focusinGuard = null;
    }
    this._root?.unmount();
    this._root = null;
  }

  private _root: Root | null = null;
  private _focusinGuard: ((event: FocusEvent) => void) | null = null;
}

function getCodeMirrorView(editor: CodeEditor.IEditor): EditorView | null {
  const codeMirrorEditor = editor as CodeEditor.IEditor & {
    editor?: EditorView;
  };
  return codeMirrorEditor.editor ?? null;
}

function ensureInlinePromptExtension(view: EditorView): void {
  if (view.state.field(inlinePromptField, false)) {
    return;
  }
  view.dispatch({
    effects: StateEffect.appendConfig.of([inlinePromptField] as Extension[])
  });
}

function getLineEndOffset(editor: CodeEditor.IEditor, offset: number): number {
  const position = editor.getPositionAt(offset);
  return editor.getOffsetAt({
    line: position.line,
    column: editor.getLine(position.line).length
  });
}

const DOCUMENT_WATCH_INTERVAL = 1000;
const MAX_TOKENS = 4096;
const githubCopilotIcon = new LabIcon({
  name: 'notebook-intelligence:github-copilot-icon',
  svgstr: copilotSvgstr
});

const sparkleIcon = new LabIcon({
  name: 'notebook-intelligence:sparkles-icon',
  svgstr: sparklesSvgstr
});

const claudeIcon = new LabIcon({
  name: 'notebook-intelligence:claude-icon',
  svgstr: claudeSvgstr
});

const openaiIcon = new LabIcon({
  name: 'notebook-intelligence:openai-icon',
  svgstr: openaiSvgstr
});

const opencodeIcon = new LabIcon({
  name: 'notebook-intelligence:opencode-icon',
  svgstr: opencodeSvgstr
});

const sparkleWarningIcon = new LabIcon({
  name: 'notebook-intelligence:sparkles-warning-icon',
  svgstr: sparklesWarningSvgstr
});
const emptyNotebookContent: any = {
  cells: [],
  metadata: {},
  nbformat: 4,
  nbformat_minor: 5
};

const BACKEND_TELEMETRY_LISTENER_NAME = 'backend-telemetry-listener';

class ActiveDocumentWatcher {
  static initialize(
    app: JupyterLab,
    languageRegistry: IEditorLanguageRegistry,
    fileBrowser: IDefaultFileBrowser
  ) {
    ActiveDocumentWatcher._languageRegistry = languageRegistry;

    app.shell.currentChanged?.connect((_sender, args) => {
      ActiveDocumentWatcher.watchDocument(args.newValue);
    });

    ActiveDocumentWatcher.activeDocumentInfo.activeWidget =
      app.shell.currentWidget;
    ActiveDocumentWatcher.handleWatchDocument();

    if (fileBrowser) {
      const onPathChanged = (model: FileBrowserModel) => {
        ActiveDocumentWatcher.currentDirectory = model.path;
      };
      fileBrowser.model.pathChanged.connect(onPathChanged);
    }
  }

  static watchDocument(widget: Widget) {
    if (ActiveDocumentWatcher.activeDocumentInfo.activeWidget === widget) {
      return;
    }
    clearInterval(ActiveDocumentWatcher._watchTimer);
    ActiveDocumentWatcher.activeDocumentInfo.activeWidget = widget;

    ActiveDocumentWatcher._watchTimer = setInterval(() => {
      ActiveDocumentWatcher.handleWatchDocument();
    }, DOCUMENT_WATCH_INTERVAL);

    ActiveDocumentWatcher.handleWatchDocument();
  }

  static handleWatchDocument() {
    const activeDocumentInfo = ActiveDocumentWatcher.activeDocumentInfo;
    const previousDocumentInfo = {
      ...activeDocumentInfo,
      ...{ activeWidget: null }
    };

    const activeWidget = activeDocumentInfo.activeWidget;
    if (activeWidget instanceof NotebookPanel) {
      const np = activeWidget as NotebookPanel;
      activeDocumentInfo.filename = np.sessionContext.name;
      activeDocumentInfo.filePath = np.sessionContext.path;
      const kernelspec = np.model?.sharedModel?.metadata?.kernelspec as
        | {
            language?: string;
            name?: string;
            display_name?: string;
          }
        | undefined;
      activeDocumentInfo.language = normalizeNotebookLanguage(
        kernelspec?.language
      );
      activeDocumentInfo.kernelName =
        kernelspec?.name || DEFAULT_NOTEBOOK_KERNEL.kernelName;
      activeDocumentInfo.kernelDisplayName =
        kernelspec?.display_name || DEFAULT_NOTEBOOK_KERNEL.displayName;
      const { activeCellIndex, activeCell } = np.content;
      activeDocumentInfo.activeCellIndex = activeCellIndex;
      activeDocumentInfo.selection = activeCell?.editor?.getSelection();
    } else if (activeWidget) {
      const dw = activeWidget as DocumentWidget;
      const contentsModel = dw.context?.contentsModel;
      if (contentsModel?.format === 'text') {
        const fileName = contentsModel.name;
        const filePath = contentsModel.path;
        const language =
          ActiveDocumentWatcher._languageRegistry.findByMIME(
            contentsModel.mimetype
          ) || ActiveDocumentWatcher._languageRegistry.findByFileName(fileName);
        activeDocumentInfo.language = language?.name || 'unknown';
        activeDocumentInfo.kernelName = undefined;
        activeDocumentInfo.kernelDisplayName = undefined;
        activeDocumentInfo.filename = fileName;
        activeDocumentInfo.filePath = filePath;
        if (activeWidget instanceof FileEditorWidget) {
          const fe = activeWidget as FileEditorWidget;
          activeDocumentInfo.selection = fe.content.editor?.getSelection();
        } else {
          activeDocumentInfo.selection = undefined;
        }
      } else {
        activeDocumentInfo.filename = '';
        activeDocumentInfo.filePath = '';
        activeDocumentInfo.language = '';
        activeDocumentInfo.kernelName = undefined;
        activeDocumentInfo.kernelDisplayName = undefined;
      }
    }

    if (
      ActiveDocumentWatcher.documentInfoChanged(
        previousDocumentInfo,
        activeDocumentInfo
      )
    ) {
      ActiveDocumentWatcher.fireActiveDocumentChangedEvent();
    }
  }

  private static documentInfoChanged(
    lhs: IActiveDocumentInfo,
    rhs: IActiveDocumentInfo
  ): boolean {
    if (!lhs || !rhs) {
      return true;
    }

    return (
      lhs.filename !== rhs.filename ||
      lhs.filePath !== rhs.filePath ||
      lhs.language !== rhs.language ||
      lhs.kernelName !== rhs.kernelName ||
      lhs.kernelDisplayName !== rhs.kernelDisplayName ||
      lhs.activeCellIndex !== rhs.activeCellIndex ||
      !compareSelections(lhs.selection, rhs.selection)
    );
  }

  static getActiveSelectionContent(): string {
    const activeDocumentInfo = ActiveDocumentWatcher.activeDocumentInfo;
    const activeWidget = activeDocumentInfo.activeWidget;

    if (activeWidget instanceof NotebookPanel) {
      const np = activeWidget as NotebookPanel;
      const editor = np.content.activeCell.editor;
      if (isSelectionEmpty(editor.getSelection())) {
        return getWholeNotebookContent(np);
      } else {
        return getSelectionInEditor(editor);
      }
    } else if (activeWidget instanceof FileEditorWidget) {
      const fe = activeWidget as FileEditorWidget;
      const editor = fe.content.editor;
      if (isSelectionEmpty(editor.getSelection())) {
        return editor.model.sharedModel.getSource();
      } else {
        return getSelectionInEditor(editor);
      }
    } else {
      const dw = activeWidget as DocumentWidget;
      const content = dw?.context?.model?.toString();
      const maxContext = 0.5 * MAX_TOKENS;
      return content.substring(0, maxContext);
    }
  }

  static getCurrentCellContents(): ICellContents {
    const activeDocumentInfo = ActiveDocumentWatcher.activeDocumentInfo;
    const activeWidget = activeDocumentInfo.activeWidget;

    if (activeWidget instanceof NotebookPanel) {
      const np = activeWidget as NotebookPanel;
      const activeCell = np.content.activeCell;
      const input = activeCell.model.sharedModel.source.trim();
      let output = '';
      if (activeCell instanceof CodeCell) {
        output = cellOutputAsText(np.content.activeCell as CodeCell);
      }

      return { input, output };
    }

    return null;
  }

  static fireActiveDocumentChangedEvent() {
    document.dispatchEvent(
      new CustomEvent('copilotSidebar:activeDocumentChanged', {
        detail: {
          activeDocumentInfo: ActiveDocumentWatcher.activeDocumentInfo
        }
      })
    );
  }

  static currentDirectory: string = '';

  static activeDocumentInfo: IActiveDocumentInfo = {
    language: 'python',
    kernelName: DEFAULT_NOTEBOOK_KERNEL.kernelName,
    kernelDisplayName: DEFAULT_NOTEBOOK_KERNEL.displayName,
    filename: 'nb-doesnt-exist.ipynb',
    filePath: 'nb-doesnt-exist.ipynb',
    activeWidget: null,
    activeCellIndex: -1,
    selection: null
  };
  private static _watchTimer: any;
  private static _languageRegistry: IEditorLanguageRegistry;
}

class NBIInlineCompletionProvider
  implements IInlineCompletionProvider<IInlineCompletionItem>
{
  constructor(telemetryEmitter: TelemetryEmitter) {
    this._telemetryEmitter = telemetryEmitter;
  }

  get schema(): ISettingRegistry.IProperty {
    return {
      default: {
        debouncerDelay: NBIAPI.config.inlineCompletionDebouncerDelay,
        timeout: 15000
      }
    };
  }

  fetch(
    request: CompletionHandler.IRequest,
    context: IInlineCompletionContext
  ): Promise<IInlineCompletionList<IInlineCompletionItem>> {
    let preContent = '';
    let postContent = '';
    const preCursor = request.text.substring(0, request.offset);
    const postCursor = request.text.substring(request.offset);
    let language = ActiveDocumentWatcher.activeDocumentInfo.language;

    let editorType = 'file-editor';

    if (context.widget instanceof NotebookPanel) {
      editorType = 'notebook';
      const activeCell = context.widget.content.activeCell;
      if (activeCell.model.sharedModel.cell_type === 'markdown') {
        language = 'markdown';
      }
      let activeCellReached = false;

      for (const cell of context.widget.content.widgets) {
        const cellModel = cell.model.sharedModel;
        if (cell === activeCell) {
          activeCellReached = true;
        } else if (!activeCellReached) {
          if (cellModel.cell_type === 'code') {
            preContent += cellModel.source + '\n';
          } else if (cellModel.cell_type === 'markdown') {
            preContent += markdownToComment(cellModel.source) + '\n';
          }
        } else {
          if (cellModel.cell_type === 'code') {
            postContent += cellModel.source + '\n';
          } else if (cellModel.cell_type === 'markdown') {
            postContent += markdownToComment(cellModel.source) + '\n';
          }
        }
      }
    }

    const nbiConfig = NBIAPI.config;
    const inlineCompletionsEnabled =
      nbiConfig.isInClaudeCodeMode ||
      (nbiConfig.inlineCompletionModel.provider === GITHUB_COPILOT_PROVIDER_ID
        ? NBIAPI.getLoginStatus() === GitHubCopilotLoginStatus.LoggedIn
        : nbiConfig.inlineCompletionModel.provider !== 'none');

    this._telemetryEmitter.emitTelemetryEvent({
      type: TelemetryEventType.InlineCompletionRequest,
      data: {
        inlineCompletionModel: {
          provider: NBIAPI.config.inlineCompletionModel.provider,
          model: NBIAPI.config.inlineCompletionModel.model
        },
        editorType
      }
    });

    return new Promise((resolve, reject) => {
      const items: IInlineCompletionItem[] = [];

      if (!inlineCompletionsEnabled) {
        resolve({ items });
        return;
      }

      if (this._lastRequestInfo) {
        NBIAPI.sendWebSocketMessage(
          this._lastRequestInfo.messageId,
          RequestDataType.CancelInlineCompletionRequest,
          { chatId: this._lastRequestInfo.chatId }
        );
      }

      const messageId = UUID.uuid4();
      const chatId = UUID.uuid4();
      this._lastRequestInfo = { chatId, messageId, requestTime: new Date() };

      NBIAPI.inlineCompletionsRequest(
        chatId,
        messageId,
        preContent + preCursor,
        postCursor + postContent,
        language,
        ActiveDocumentWatcher.activeDocumentInfo.filename,
        {
          emit: (response: any) => {
            if (
              response.type === BackendMessageType.StreamMessage &&
              response.id === this._lastRequestInfo.messageId
            ) {
              items.push({
                insertText: response.data.completions
              });

              const timeElapsed =
                (new Date().getTime() -
                  this._lastRequestInfo.requestTime.getTime()) /
                1000;
              this._telemetryEmitter.emitTelemetryEvent({
                type: TelemetryEventType.InlineCompletionResponse,
                data: {
                  inlineCompletionModel: {
                    provider: NBIAPI.config.inlineCompletionModel.provider,
                    model: NBIAPI.config.inlineCompletionModel.model
                  },
                  timeElapsed
                }
              });

              resolve({ items });
            } else {
              reject();
            }
          }
        }
      );
    });
  }

  get name(): string {
    return 'Notebook Intelligence';
  }

  get identifier(): string {
    return NBI_INLINE_COMPLETION_PROVIDER_ID;
  }

  get icon(): LabIcon.ILabIcon {
    const isClaudeModel =
      NBIAPI.config.isInClaudeCodeMode &&
      NBIAPI.config.claudeSettings.inline_completion_model !== 'none' &&
      NBIAPI.config.claudeSettings.inline_completion_model !== 'inherit';
    return isClaudeModel
      ? claudeIcon
      : NBIAPI.config.usingGitHubCopilotModel
        ? githubCopilotIcon
        : sparkleIcon;
  }

  private _lastRequestInfo: {
    chatId: string;
    messageId: string;
    requestTime: Date;
  } = null;
  private _telemetryEmitter: TelemetryEmitter;
}

class TelemetryEmitter implements ITelemetryEmitter {
  registerTelemetryListener(listener: ITelemetryListener) {
    const listenerName = listener.name;

    if (listenerName !== BACKEND_TELEMETRY_LISTENER_NAME) {
      console.warn(
        `Notebook Intelligence telemetry listener '${listenerName}' registered. Make sure it is from a trusted source.`
      );
    }

    let listenerAlreadyExists = false;
    this._listeners.forEach(existingListener => {
      if (existingListener.name === listenerName) {
        listenerAlreadyExists = true;
      }
    });

    if (listenerAlreadyExists) {
      console.error(
        `Notebook Intelligence telemetry listener '${listenerName}' already exists!`
      );
      return;
    }

    this._listeners.add(listener);
  }

  unregisterTelemetryListener(listener: ITelemetryListener) {
    this._listeners.delete(listener);
  }

  emitTelemetryEvent(event: ITelemetryEvent) {
    this._listeners.forEach(listener => {
      listener.onTelemetryEvent(event);
    });
  }

  private _listeners: Set<ITelemetryListener> = new Set<ITelemetryListener>();
}

class MCPConfigEditor {
  constructor(docManager: IDocumentManager) {
    this._docManager = docManager;
  }

  async open() {
    const contents = new ContentsManager();
    const newJSONFile = await contents.newUntitled({
      ext: '.json'
    });
    const mcpConfig = await NBIAPI.getMCPConfigFile();

    try {
      await contents.delete(this._tmpMCPConfigFilename);
    } catch (error) {
      // ignore
    }

    await contents.save(newJSONFile.path, {
      content: JSON.stringify(mcpConfig, null, 2),
      format: 'text',
      type: 'file'
    });
    await contents.rename(newJSONFile.path, this._tmpMCPConfigFilename);
    this._docWidget = this._docManager.openOrReveal(
      this._tmpMCPConfigFilename,
      'Editor'
    );
    this._addListeners();
    // tab closed
    this._docWidget.disposed.connect((_, args) => {
      this._removeListeners();
      contents.delete(this._tmpMCPConfigFilename);
    });
    this._isOpen = true;
  }

  close() {
    if (!this._isOpen) {
      return;
    }
    this._isOpen = false;
    this._docWidget.dispose();
    this._docWidget = null;
  }

  get isOpen(): boolean {
    return this._isOpen;
  }

  private _addListeners() {
    this._docWidget.context.model.stateChanged.connect(
      this._onStateChanged,
      this
    );
  }

  private _removeListeners() {
    this._docWidget.context.model.stateChanged.disconnect(
      this._onStateChanged,
      this
    );
  }

  private _onStateChanged(model: any, args: any) {
    if (args.name === 'dirty' && args.newValue === false) {
      this._onSave();
    }
  }

  private async _onSave() {
    const mcpConfig = this._docWidget.context.model.toJSON();
    try {
      await NBIAPI.setMCPConfigFile(mcpConfig);
    } catch (reason: any) {
      // Surface server-side validation rejections (400 from the
      // shape validator, 500 from a downstream save / reconcile
      // failure) to the user. Without this, the document model
      // goes clean on save and the user has no signal that their
      // edit did not actually persist. ServerConnection.ResponseError
      // carries the handler's JSON `message` field on reason.message.
      Notification.error(
        `Failed to save MCP config: ${reason?.message ?? reason}`,
        { autoClose: 5000 }
      );
      return;
    }
    await NBIAPI.fetchCapabilities();
  }

  private _docManager: IDocumentManager;
  private _docWidget: IDocumentWidget = null;
  private _tmpMCPConfigFilename = 'nbi.mcp.temp.json';
  private _isOpen = false;
}

/**
 * Initialization data for the @plmbr/notebook-intelligence extension.
 */
const plugin: JupyterFrontEndPlugin<INotebookIntelligence> = {
  id: '@plmbr/notebook-intelligence:plugin',
  description: 'Notebook Intelligence',
  autoStart: true,
  requires: [
    ICompletionProviderManager,
    IDocumentManager,
    IDefaultFileBrowser,
    IEditorLanguageRegistry,
    ICommandPalette,
    IMainMenu
  ],
  // @jupyterlab/terminal nests its own @lumino/coreutils copy, so its
  // Token class is structurally identical but nominally distinct from
  // ours. Cast through the top-level Token type to keep the plugin
  // declaration well-typed for the other optionals.
  optional: [
    IStatusBar,
    ILauncher,
    ITerminalTracker as unknown as Token<unknown>,
    IInlineCompleterFactory
  ],
  provides: INotebookIntelligence,
  activate: async (
    app: JupyterFrontEnd,
    completionManager: ICompletionProviderManager,
    docManager: IDocumentManager,
    defaultBrowser: IDefaultFileBrowser,
    languageRegistry: IEditorLanguageRegistry,
    palette: ICommandPalette,
    mainMenu: IMainMenu,
    statusBar: IStatusBar | null,
    launcher: ILauncher | null,
    terminalTracker: ITerminalTracker | null,
    defaultInlineCompleterFactory: IInlineCompleterFactory | null
  ) => {
    console.log(
      'JupyterLab extension @plmbr/notebook-intelligence is activated!'
    );

    const telemetryEmitter = new TelemetryEmitter();

    telemetryEmitter.registerTelemetryListener({
      name: BACKEND_TELEMETRY_LISTENER_NAME,
      onTelemetryEvent: event => {
        NBIAPI.emitTelemetryEvent(event);
      }
    });

    const extensionService: INotebookIntelligence = {
      registerTelemetryListener: (listener: ITelemetryListener) => {
        telemetryEmitter.registerTelemetryListener(listener);
      },
      unregisterTelemetryListener: (listener: ITelemetryListener) => {
        telemetryEmitter.unregisterTelemetryListener(listener);
      }
    };

    await NBIAPI.initialize();

    if (terminalTracker) {
      attachTerminalDragDrop({
        tracker: terminalTracker,
        // dragover fires at ~60Hz, so we read straight off the
        // capabilities object (cheap property lookup) instead of going
        // through the `featurePolicies` getter (which rebuilds an
        // 11-key object every call). Re-evaluated per event so a future
        // capabilities reload (policy flipped server-side) takes effect
        // without tearing down listeners.
        isEnabled: () => {
          const policy =
            NBIAPI.config.capabilities?.feature_policies?.terminal_drag_drop;
          // Default-enabled when the key is absent: an older backend or
          // a future capability schema bump shouldn't silently disable
          // the feature.
          if (!policy) {
            return true;
          }
          return policy.enabled !== false;
        }
      });
    }

    let closeOpenPopover: (() => void) | null = null;
    let mcpConfigEditor: MCPConfigEditor | null = null;

    completionManager.registerInlineProvider(
      new NBIInlineCompletionProvider(telemetryEmitter)
    );

    // Wrap JupyterLab's default inline-completer factory to add acceptance
    // telemetry. We install it on app.started: that resolves after every
    // plugin has activated (so JupyterLab's own factory is already set and we
    // win as the last writer) but before layout restoration creates the per
    // editor completer handlers, which capture the factory at creation time and
    // are never regenerated afterwards. Deferring to app.restored would be too
    // late: the restored notebook's handler would already hold the default
    // widget. Guarded on the optional factory so NBI still loads if JupyterLab
    // ever stops providing it (acceptance telemetry simply won't be emitted).
    if (defaultInlineCompleterFactory) {
      const wrappedFactory = wrapInlineCompleterFactory(
        defaultInlineCompleterFactory,
        telemetryEmitter,
        () => ({
          provider: NBIAPI.config.inlineCompletionModel.provider,
          model: NBIAPI.config.inlineCompletionModel.model
        })
      );
      app.started
        .then(() => {
          completionManager.setInlineCompleterFactory(wrappedFactory);
        })
        .catch(() => {
          /* best-effort: acceptance telemetry just won't be emitted */
        });
    }

    // JL plugins have no deactivate hook, so the watcher runs for the
    // lifetime of the Lab session and the returned teardown is intentionally
    // discarded (it exists for test ergonomics).
    attachOpenFileRefreshWatcher({
      env: buildRefreshWatcherEnv(app, app.serviceManager.contents),
      isEnabled: () =>
        NBIAPI.config.featurePolicies.refresh_open_files_on_disk_change.enabled
    });

    const waitForFileToBeActive = async (
      filePath: string
    ): Promise<boolean> => {
      const isNotebook = filePath.endsWith('.ipynb');

      return new Promise<boolean>((resolve, reject) => {
        const checkIfActive = () => {
          const activeFilePath =
            ActiveDocumentWatcher.activeDocumentInfo.filePath;
          const filePathToCheck = filePath;
          const currentWidget = app.shell.currentWidget;
          if (
            activeFilePath === filePathToCheck &&
            ((isNotebook &&
              currentWidget instanceof NotebookPanel &&
              currentWidget.content.activeCell &&
              currentWidget.content.activeCell.node.contains(
                document.activeElement
              )) ||
              (!isNotebook &&
                currentWidget instanceof FileEditorWidget &&
                currentWidget.content.editor.hasFocus()))
          ) {
            resolve(true);
          } else {
            setTimeout(checkIfActive, 200);
          }
        };
        checkIfActive();

        waitForDuration(10000).then(() => {
          resolve(false);
        });
      });
    };

    const panel = new Panel();
    panel.id = 'notebook-intelligence-tab';
    panel.title.caption = 'Notebook Intelligence';
    const sidebarIcon = new LabIcon({
      name: 'notebook-intelligence:sidebar-icon',
      svgstr: sparklesSvgstr
    });
    panel.title.icon = sidebarIcon;
    const sidebar = new ChatSidebar({
      getCurrentDirectory: (): string => {
        return ActiveDocumentWatcher.currentDirectory;
      },
      getActiveDocumentInfo: (): IActiveDocumentInfo => {
        return ActiveDocumentWatcher.activeDocumentInfo;
      },
      getActiveSelectionContent: (): string => {
        return ActiveDocumentWatcher.getActiveSelectionContent();
      },
      getCurrentCellContents: (): ICellContents => {
        return ActiveDocumentWatcher.getCurrentCellContents();
      },
      openFile: (path: string) => {
        docManager.openOrReveal(path);
      },
      getApp(): JupyterFrontEnd {
        return app;
      },
      getTelemetryEmitter(): ITelemetryEmitter {
        return telemetryEmitter;
      }
    });
    panel.addWidget(sidebar);
    app.shell.add(panel, 'left', { rank: 1000 });
    app.shell.activateById(panel.id);

    // Global focus shortcut. Activates the NBI sidebar (revealing it if
    // collapsed) and lands focus on the prompt textarea, so keyboard-
    // first users can start typing without mousing through panel tabs.
    // Ctrl/Cmd+Shift+L mirrors common "focus search / focus input"
    // bindings used by other editors and doesn't collide with any
    // built-in JupyterLab shortcut.
    //
    // Implementation: dispatch a CustomEvent the React sidebar listens
    // for. The sidebar owns `promptInputRef` and can focus the
    // textarea reliably regardless of whether the panel was collapsed
    // (the event fires after the React tree has been mounted by the
    // activate path), so we don't have to race the Lumino layout with
    // a DOM-id `querySelector`. Matches the existing
    // `copilotSidebar:*` event pattern used elsewhere in this file.
    app.commands.addCommand(CommandIDs.focusChatInput, {
      label: 'Focus Notebook Intelligence chat input',
      caption: 'Open the NBI sidebar and move focus to the prompt textarea',
      execute: () => {
        app.shell.activateById(panel.id);
        document.dispatchEvent(new CustomEvent('copilotSidebar:focusPrompt'));
      }
    });
    app.commands.addKeyBinding({
      command: CommandIDs.focusChatInput,
      keys: ['Accel Shift L'],
      selector: 'body'
    });

    app.docRegistry.addWidgetExtension(
      'Notebook',
      new NotebookGenerationToolbarExtension({
        app,
        icon: sparkleIcon,
        chatSidebarId: panel.id
      })
    );

    const updateSidebarIcon = () => {
      if (NBIAPI.getChatEnabled()) {
        panel.title.icon = sidebarIcon;
      } else {
        panel.title.icon = sparkleWarningIcon;
      }
    };

    NBIAPI.githubLoginStatusChanged.connect((_, args) => {
      updateSidebarIcon();
    });

    NBIAPI.configChanged.connect((_, args) => {
      updateSidebarIcon();
    });

    setTimeout(() => {
      updateSidebarIcon();
    }, 2000);

    app.commands.addCommand(CommandIDs.chatuserInput, {
      execute: args => {
        NBIAPI.sendChatUserInput(args.id as string, args.data);
      }
    });

    app.commands.addCommand(CommandIDs.insertAtCursor, {
      execute: args => {
        const currentWidget = app.shell.currentWidget;
        if (currentWidget instanceof NotebookPanel) {
          const activeCell = currentWidget.content.activeCell;
          if (activeCell) {
            applyCodeToSelectionInEditor(
              activeCell.editor,
              args.code as string
            );
            return;
          }
        } else if (currentWidget instanceof FileEditorWidget) {
          applyCodeToSelectionInEditor(
            currentWidget.content.editor,
            args.code as string
          );
          return;
        }

        app.commands.execute('apputils:notify', {
          message:
            'Failed to insert at cursor. Open a notebook or file to insert the code.',
          type: 'error',
          options: { autoClose: true }
        });
      }
    });

    app.commands.addCommand(CommandIDs.addCodeAsNewCell, {
      execute: args => {
        const currentWidget = app.shell.currentWidget;
        if (currentWidget instanceof NotebookPanel) {
          let activeCellIndex = currentWidget.content.activeCellIndex;
          activeCellIndex =
            activeCellIndex === -1
              ? currentWidget.content.widgets.length
              : activeCellIndex + 1;

          currentWidget.model?.sharedModel.insertCell(activeCellIndex, {
            cell_type: 'code',
            metadata: { trusted: true },
            source: args.code as string
          });
          currentWidget.content.activeCellIndex = activeCellIndex;
        } else {
          app.commands.execute('apputils:notify', {
            message: 'Open a notebook to insert the code as new cell',
            type: 'error',
            options: { autoClose: true }
          });
        }
      }
    });

    app.commands.addCommand(CommandIDs.createNewFile, {
      execute: async args => {
        const contents = new ContentsManager();
        const newPyFile = await contents.newUntitled({
          ext: '.py',
          path: defaultBrowser?.model.path
        });
        contents.save(newPyFile.path, {
          content: extractLLMGeneratedCode(args.code as string),
          format: 'text',
          type: 'file'
        });
        docManager.openOrReveal(newPyFile.path);

        await waitForFileToBeActive(newPyFile.path);

        return newPyFile;
      }
    });

    app.commands.addCommand(CommandIDs.showTour, {
      label: () =>
        commandLabel(
          NBIAPI.config.tourOverrides,
          TOUR_DEFAULTS.command?.label ?? 'Show NBI tour'
        ),
      caption:
        'Replay the first-run tour highlighting the chat sidebar affordances',
      execute: async () => {
        // Make sure the sidebar is open before the tour fires; the
        // overlay anchors to elements that only exist when the
        // sidebar widget is mounted. Activate the left-rail panel
        // directly (the same id used in app.shell.add above).
        app.shell.activateById(panel.id);
        // Reset persistence so re-runs always fire.
        resetTour();
        // activateById is fire-and-forget: the panel can still be
        // mid-layout when this returns, and dispatching synchronously
        // would race the anchor measurement. Wait for the panel to
        // actually be visible before firing, then dispatch on a frame
        // boundary so the sidebar's React tree has committed.
        const fire = () => {
          requestAnimationFrame(() => dispatchShowTour());
        };
        if (panel.isVisible) {
          fire();
        } else {
          // Use a bounded rAF poll waiting for the panel to become
          // visible. activateById is asynchronous and we can't
          // synchronously observe completion from outside the widget.
          let attempts = 0;
          const tick = () => {
            attempts += 1;
            if (panel.isVisible || attempts >= 30) {
              fire();
              return;
            }
            requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        }
      }
    });
    palette.addItem({
      command: CommandIDs.showTour,
      category: 'Notebook Intelligence'
    });
    // The label thunk above reads from NBIAPI.config.tourOverrides,
    // which arrives asynchronously over the capabilities call. The
    // command palette caches labels until told otherwise, so tell it.
    NBIAPI.configChanged.connect(() => {
      app.commands.notifyCommandChanged(CommandIDs.showTour);
    });

    app.commands.addCommand(CommandIDs.showFormInputDialog, {
      execute: async args => {
        const title = args.title as string;
        const fields = args.fields;

        return new Promise<any>((resolve, reject) => {
          let dialog: Dialog<unknown> | null = null;
          const dialogBody = new FormInputDialogBody({
            fields: fields,
            onDone: (formData: any) => {
              dialog.dispose();
              resolve(formData);
            }
          });
          dialog = new Dialog({
            title: title,
            hasClose: true,
            body: dialogBody,
            buttons: []
          });

          dialog
            .launch()
            .then((result: any) => {
              reject();
            })
            .catch(() => {
              reject(new Error('Failed to show form input dialog'));
            });
        });
      }
    });

    app.commands.addCommand(CommandIDs.createNewNotebook, {
      execute: async args => {
        const contents = new ContentsManager();
        const kernels = new KernelSpecManager();
        await kernels.ready;
        let profile;
        try {
          profile = findKernelProfile(kernels.specs?.kernelspecs, {
            language: args.language as string | undefined,
            kernelName: args.kernelName as string | undefined
          });
        } catch (error) {
          if (error instanceof NotebookKernelNotFoundError) {
            app.commands.execute('apputils:notify', {
              message: error.message,
              type: 'error',
              options: { autoClose: true }
            });
          }
          throw error;
        }

        const newNBFile = await contents.newUntitled({
          ext: '.ipynb',
          path: defaultBrowser?.model.path
        });
        const nbFileContent = structuredClone(emptyNotebookContent);
        nbFileContent.metadata = {
          kernelspec: {
            language: profile.language,
            name: profile.kernelName,
            display_name: profile.displayName
          },
          language_info: {
            name: profile.language
          }
        };

        if (args.code) {
          nbFileContent.cells.push({
            cell_type: 'code',
            metadata: { trusted: true },
            source: [args.code as string],
            outputs: []
          });
        }

        contents.save(newNBFile.path, {
          content: nbFileContent,
          format: 'json',
          type: 'notebook'
        });
        docManager.openOrReveal(newNBFile.path);

        await waitForFileToBeActive(newNBFile.path);

        return newNBFile;
      }
    });

    app.commands.addCommand(CommandIDs.listAvailableNotebookKernels, {
      execute: async () => {
        const kernels = new KernelSpecManager();
        await kernels.ready;
        return {
          kernels: listKernelProfiles(kernels.specs?.kernelspecs)
        };
      }
    });

    app.commands.addCommand(CommandIDs.renameNotebook, {
      execute: async args => {
        const activeWidget = app.shell.currentWidget;
        if (activeWidget instanceof NotebookPanel) {
          const oldPath = activeWidget.context.path;
          const oldParentPath = path.dirname(oldPath);
          let newPath = path.join(oldParentPath, args.newName as string);
          if (path.extname(newPath) !== '.ipynb') {
            newPath += '.ipynb';
          }

          if (path.dirname(newPath) !== oldParentPath) {
            return 'Failed to rename notebook. New path is outside the old parent directory';
          }

          try {
            await app.serviceManager.contents.rename(oldPath, newPath);
            return 'Successfully renamed notebook';
          } catch (error) {
            return `Failed to rename notebook: ${error}`;
          }
        } else {
          return 'Cannot rename non notebook files';
        }
      }
    });

    app.commands.addCommand(CommandIDs.runCommandInTerminal, {
      execute: async args => {
        const command = args.command as string;
        const terminal = await app.commands.execute('terminal:create-new', {
          cwd: (args.cwd as string) || ActiveDocumentWatcher.currentDirectory
        });

        const session: ITerminalConnection = terminal?.content?.session;

        if (!session) {
          return 'Failed to execute command in Jupyter terminal';
        }

        return new Promise<string>((resolve, reject) => {
          let lastMessageReceivedTime = Date.now();
          let lastMessageCheckInterval: NodeJS.Timeout | null = null;
          const messageCheckTimeout = 5000;
          const messageCheckInterval = 1000;
          let output = '';
          const messageReceivedHandler = (sender: any, message: any) => {
            const content = stripAnsi(message.content.join(''));
            output += content;
            lastMessageReceivedTime = Date.now();
          };
          session.messageReceived.connect(messageReceivedHandler);

          session.send({
            type: 'stdin',
            content: [command + '\n'] // Add newline to execute the command
          });

          // wait for the messageCheckInterval and if no message received, return the output.
          // otherwise wait for the next message.
          lastMessageCheckInterval = setInterval(() => {
            if (Date.now() - lastMessageReceivedTime > messageCheckTimeout) {
              clearInterval(lastMessageCheckInterval);
              session.messageReceived.disconnect(messageReceivedHandler);
              resolve(
                `Command executed in Jupyter terminal, output: ${output}`
              );
            }
          }, messageCheckInterval);
        });
      }
    });

    // Claude Code launcher tile: shows a session picker backed by history.jsonl
    // (all projects), then opens a terminal at the session's project directory.

    // Waits for bash's first prompt before sending, avoiding the race condition
    // where the command is sent before the shell has started.
    const launchCliInTerminal = async (
      command: string,
      cwd?: string
    ): Promise<void> => {
      const mgr = app.serviceManager.terminals;
      const before = new Set([...mgr.running()].map((s: any) => s.name));
      try {
        await app.commands.execute('terminal:create-new', cwd ? { cwd } : {});
      } catch {
        return;
      }
      const newModel = [...mgr.running()].find((s: any) => !before.has(s.name));
      if (!newModel) {
        return;
      }
      const conn: any = mgr.connectTo({ model: newModel });
      let sent = false;
      const sendCommand = () => {
        if (sent) {
          return;
        }
        sent = true;
        conn.messageReceived.disconnect(handler);
        conn.send({ type: 'stdin', content: [command + '\r'] });
      };
      const handler = (_: any, msg: any) => {
        if (msg.type === 'stdout') {
          sendCommand();
        }
      };
      conn.messageReceived.connect(handler);
      setTimeout(sendCommand, 3000);
    };

    app.commands.addCommand(CommandIDs.openClaudeCodeLauncher, {
      label: 'Claude Code',
      caption: 'Resume or start a Claude Code session',
      icon: claudeIcon,
      // The launcher tile opens a Jupyter terminal that runs the
      // `claude` CLI directly — it doesn't depend on NBI being in
      // Claude Code chat mode (which gates the chat-sidebar SDK
      // backend). CLI presence on PATH is the only real prerequisite
      // (issue #230). Honor the admin policy on the same gate as the
      // four CLI-launcher tiles so an admin force-off blocks the
      // command palette too, not just the launcher tile.
      isVisible: () =>
        NBIAPI.config.isClaudeCliAvailable &&
        !NBIAPI.config.isCodingAgentLauncherDisabledByPolicy('claude-code'),
      execute: async () => {
        class PickerWidget extends ReactWidget {
          getValue(): void {
            return;
          }
          render() {
            return React.createElement(LauncherPicker, {
              onSessionSelected: (session: IClaudeSessionInfo) => {
                dialog.close();
                launchCliInTerminal(
                  buildResumeCommand(session.cwd ?? '', session.session_id)
                );
              }
            });
          }
        }

        const picker = new PickerWidget();
        picker.addClass('nbi-claude-code-picker');
        const dialog = new Dialog({
          title: 'Claude Code terminal session',
          body: picker,
          buttons: [
            Dialog.cancelButton({ label: 'Cancel' }),
            Dialog.okButton({ label: '＋ New Session' })
          ],
          hasClose: true
        });
        const result = await dialog.launch();
        if (result.button.accept) {
          // New Session: let the user choose a start directory (defaulting
          // to the file browser's current path). If they cancel the folder
          // picker, abort rather than silently opening the terminal in an
          // unexpected location.
          const cwd = await chooseWorkspaceDirectory(
            docManager,
            'Choose start directory for Claude Code',
            defaultBrowser?.model.path
          );
          if (cwd === undefined) {
            return;
          }
          launchCliInTerminal('claude', cwd);
        }
      }
    });

    // Add or dispose a launcher entry based on a live availability check.
    // The launcher renders every item in its model regardless of the
    // backing command's `isVisible`, so gating tile visibility requires
    // adding only when available and disposing when not. Re-evaluates on
    // every NBIAPI.configChanged so a late capabilities load (or a
    // future hot-reload of the CLI on PATH) takes effect without a
    // browser refresh.
    const syncLauncherEntry = (
      commandId: string,
      itemOptions: Omit<ILauncher.IItemOptions, 'command'>,
      isAvailable: () => boolean
    ) => {
      if (!launcher) {
        return;
      }
      let entry: IDisposable | null = null;
      const sync = () => {
        const available = isAvailable();
        if (available && !entry) {
          entry = launcher.add({ command: commandId, ...itemOptions });
        } else if (!available && entry) {
          entry.dispose();
          entry = null;
        }
      };
      sync();
      NBIAPI.configChanged.connect(sync);
    };

    syncLauncherEntry(
      CommandIDs.openClaudeCodeLauncher,
      { category: 'Coding Agent', rank: -1 },
      () =>
        NBIAPI.config.isClaudeCliAvailable &&
        !NBIAPI.config.isCodingAgentLauncherDisabledByPolicy('claude-code')
    );

    // Additional coding-agent CLIs (issue #260). First-phase scope: detect
    // the binary on PATH (backend exposes `<agent>_cli_available`), show a
    // tile when present, click opens a terminal in the file-browser's
    // current directory and types the CLI command. No session picker.
    const registerAgentCliLauncher = (config: {
      commandId: string;
      label: string;
      caption: string;
      icon: LabIcon;
      cliCommand: string;
      isAvailable: () => boolean;
    }) => {
      app.commands.addCommand(config.commandId, {
        label: config.label,
        caption: config.caption,
        icon: config.icon,
        isVisible: () => config.isAvailable(),
        execute: async () => {
          const cwd = await chooseWorkspaceDirectory(
            docManager,
            `Choose start directory for ${config.label}`,
            defaultBrowser?.model.path
          );
          if (cwd === undefined) {
            return;
          }
          launchCliInTerminal(config.cliCommand, cwd);
        }
      });
      syncLauncherEntry(
        config.commandId,
        { category: 'Coding Agent' },
        config.isAvailable
      );
      NBIAPI.configChanged.connect(() => {
        app.commands.notifyCommandChanged(config.commandId);
      });
    };

    registerAgentCliLauncher({
      commandId: CommandIDs.openOpenCodeLauncher,
      label: 'opencode',
      caption: 'Start an opencode session in a Jupyter terminal',
      icon: opencodeIcon,
      cliCommand: 'opencode',
      isAvailable: () =>
        NBIAPI.config.isOpenCodeCliAvailable &&
        !NBIAPI.config.isCodingAgentLauncherDisabledByPolicy('opencode')
    });

    registerAgentCliLauncher({
      commandId: CommandIDs.openPiLauncher,
      label: 'Pi',
      caption: 'Start a Pi session in a Jupyter terminal',
      icon: terminalIcon,
      cliCommand: 'pi',
      isAvailable: () =>
        NBIAPI.config.isPiCliAvailable &&
        !NBIAPI.config.isCodingAgentLauncherDisabledByPolicy('pi')
    });

    registerAgentCliLauncher({
      commandId: CommandIDs.openGitHubCopilotCliLauncher,
      label: 'GitHub Copilot CLI',
      caption: 'Start a GitHub Copilot CLI session in a Jupyter terminal',
      icon: githubCopilotIcon,
      cliCommand: 'copilot',
      isAvailable: () =>
        NBIAPI.config.isGitHubCopilotCliAvailable &&
        !NBIAPI.config.isCodingAgentLauncherDisabledByPolicy(
          'github-copilot-cli'
        )
    });

    registerAgentCliLauncher({
      commandId: CommandIDs.openCodexLauncher,
      label: 'Codex',
      caption: 'Start an OpenAI Codex CLI session in a Jupyter terminal',
      icon: openaiIcon,
      cliCommand: 'codex',
      isAvailable: () =>
        NBIAPI.config.isCodexCliAvailable &&
        !NBIAPI.config.isCodingAgentLauncherDisabledByPolicy('codex')
    });

    // Refresh the Claude Code command's palette-visibility state when the
    // user installs/uninstalls the CLI. The launcher tile is already gated
    // via syncLauncherEntry; this is for the command palette only.
    NBIAPI.configChanged.connect(() => {
      app.commands.notifyCommandChanged(CommandIDs.openClaudeCodeLauncher);
    });

    const isNewEmptyNotebook = (model: ISharedNotebook) => {
      return (
        model.cells.length === 1 &&
        model.cells[0].cell_type === 'code' &&
        model.cells[0].source === ''
      );
    };

    const githubLoginRequired = () => {
      return (
        NBIAPI.config.usingGitHubCopilotModel &&
        NBIAPI.getLoginStatus() === GitHubCopilotLoginStatus.NotLoggedIn
      );
    };

    const isChatEnabled = (): boolean => {
      return (
        NBIAPI.config.isInClaudeCodeMode ||
        NBIAPI.config.isInAcpMode ||
        (NBIAPI.config.chatModel.provider === GITHUB_COPILOT_PROVIDER_ID
          ? !githubLoginRequired()
          : NBIAPI.config.chatModel.provider !== 'none')
      );
    };

    const isActiveCellCodeCell = (): boolean => {
      if (!(app.shell.currentWidget instanceof NotebookPanel)) {
        return false;
      }
      const np = app.shell.currentWidget as NotebookPanel;
      const activeCell = np.content.activeCell;
      return activeCell instanceof CodeCell;
    };

    const isCurrentWidgetFileEditor = (): boolean => {
      return app.shell.currentWidget instanceof FileEditorWidget;
    };

    const addCellToNotebook = (
      filePath: string,
      cellType: 'code' | 'markdown',
      source: string
    ): boolean => {
      const widget = docManager.findWidget(filePath);
      const notebook =
        widget instanceof NotebookPanel && widget.model ? widget : null;
      if (!notebook) {
        app.commands.execute('apputils:notify', {
          message: `Failed to access the notebook: ${filePath}`,
          type: 'error',
          options: { autoClose: true }
        });
        return false;
      }

      const model = notebook.model.sharedModel;

      const newCellIndex = isNewEmptyNotebook(model)
        ? 0
        : model.cells.length - 1;
      model.insertCell(newCellIndex, {
        cell_type: cellType,
        metadata: { trusted: true },
        source
      });

      return true;
    };

    app.commands.addCommand(CommandIDs.addCodeCellToNotebook, {
      execute: args => {
        return addCellToNotebook(
          args.path as string,
          'code',
          args.code as string
        );
      }
    });

    app.commands.addCommand(CommandIDs.addMarkdownCellToNotebook, {
      execute: args => {
        return addCellToNotebook(
          args.path as string,
          'markdown',
          args.markdown as string
        );
      }
    });

    // Resolve the notebook a cell-targeting command should operate on.
    //
    // When the chat sidebar dispatches a RunUICommand on behalf of the
    // agent it injects `notebookPath` — the notebook that was active at
    // chat-request time. We look that one up via the doc manager so the
    // command keeps targeting the right notebook even after the user
    // switches tabs mid-task. If a `notebookPath` was supplied but no
    // longer resolves (target tab was closed, or the notebook was
    // renamed), we *do not* silently fall through to `currentWidget` —
    // that would let the agent mutate a different notebook than it
    // believed it was operating on. Surface the error and bail.
    //
    // For manually-invoked commands (palette, menu, toolbar) there's no
    // `notebookPath`; the current widget is the intended target and the
    // fallback applies.
    const resolveTargetNotebook = (
      args: { notebookPath?: string } | null | undefined
    ): NotebookPanel | null => {
      const requestedPath = args?.notebookPath;
      if (requestedPath) {
        const widget = docManager.findWidget(requestedPath);
        if (widget instanceof NotebookPanel && widget.model) {
          return widget;
        }
        app.commands.execute('apputils:notify', {
          message: `Failed to find notebook: ${requestedPath}`,
          type: 'error',
          options: { autoClose: true }
        });
        return null;
      }
      const currentWidget = app.shell.currentWidget;
      if (currentWidget instanceof NotebookPanel && currentWidget.model) {
        return currentWidget;
      }
      app.commands.execute('apputils:notify', {
        message: 'Failed to find active notebook',
        type: 'error',
        options: { autoClose: true }
      });
      return null;
    };

    const ensureAFileEditorIsActive = (): boolean => {
      const currentWidget = app.shell.currentWidget;
      const textFileOpen = currentWidget instanceof FileEditorWidget;
      if (!textFileOpen) {
        app.commands.execute('apputils:notify', {
          message: 'Failed to find active file',
          type: 'error',
          options: { autoClose: true }
        });
        return false;
      }

      return true;
    };

    app.commands.addCommand(CommandIDs.addMarkdownCellToActiveNotebook, {
      execute: args => {
        const np = resolveTargetNotebook(args);
        if (!np) {
          return false;
        }
        const model = np.model.sharedModel;

        const newCellIndex = isNewEmptyNotebook(model)
          ? 0
          : model.cells.length - 1;
        model.insertCell(newCellIndex, {
          cell_type: 'markdown',
          metadata: { trusted: true },
          source: args.source as string
        });

        return { cellIndex: newCellIndex };
      }
    });

    app.commands.addCommand(CommandIDs.addCodeCellToActiveNotebook, {
      execute: args => {
        const np = resolveTargetNotebook(args);
        if (!np) {
          return false;
        }
        const model = np.model.sharedModel;

        const newCellIndex = isNewEmptyNotebook(model)
          ? 0
          : model.cells.length - 1;
        model.insertCell(newCellIndex, {
          cell_type: 'code',
          metadata: { trusted: true },
          source: args.source as string
        });

        return { cellIndex: newCellIndex };
      }
    });

    app.commands.addCommand(CommandIDs.getCellTypeAndSource, {
      execute: args => {
        const np = resolveTargetNotebook(args);
        if (!np) {
          return false;
        }
        const model = np.model.sharedModel;

        return {
          type: model.cells[args.cellIndex as number].cell_type,
          source: model.cells[args.cellIndex as number].source
        };
      }
    });

    app.commands.addCommand(CommandIDs.setCellTypeAndSource, {
      execute: args => {
        const np = resolveTargetNotebook(args);
        if (!np) {
          return false;
        }
        const model = np.model.sharedModel;

        const cellIndex = args.cellIndex as number;
        const cellType = args.cellType as 'code' | 'markdown';
        const cell = model.getCell(cellIndex);

        model.deleteCell(cellIndex);
        model.insertCell(cellIndex, {
          cell_type: cellType,
          metadata: cell.metadata,
          source: args.source as string
        });

        return true;
      }
    });

    app.commands.addCommand(CommandIDs.getNumberOfCells, {
      execute: args => {
        const np = resolveTargetNotebook(args);
        if (!np) {
          return false;
        }
        const model = np.model.sharedModel;

        return model.cells.length;
      }
    });

    app.commands.addCommand(CommandIDs.getCellOutput, {
      execute: args => {
        const np = resolveTargetNotebook(args);
        if (!np) {
          return false;
        }
        const cellIndex = args.cellIndex as number;

        const cell = np.content.widgets[cellIndex];

        if (!(cell instanceof CodeCell)) {
          return '';
        }

        const content = cellOutputAsText(cell as CodeCell);

        return content;
      }
    });

    app.commands.addCommand(CommandIDs.insertCellAtIndex, {
      execute: args => {
        const np = resolveTargetNotebook(args);
        if (!np) {
          return false;
        }
        const model = np.model.sharedModel;
        const cellIndex = args.cellIndex as number;
        const cellType = args.cellType as 'code' | 'markdown';

        model.insertCell(cellIndex, {
          cell_type: cellType,
          metadata: { trusted: true },
          source: args.source as string
        });

        return true;
      }
    });

    app.commands.addCommand(CommandIDs.deleteCellAtIndex, {
      execute: args => {
        const np = resolveTargetNotebook(args);
        if (!np) {
          return false;
        }
        const model = np.model.sharedModel;
        const cellIndex = args.cellIndex as number;

        model.deleteCell(cellIndex);

        return true;
      }
    });

    app.commands.addCommand(CommandIDs.runCellAtIndex, {
      execute: async args => {
        const np = resolveTargetNotebook(args);
        if (!np) {
          return false;
        }
        np.content.activeCellIndex = args.cellIndex as number;

        // Drive the cell run via NotebookActions directly rather than the
        // app-level `notebook:run-cell` command. The command operates on the
        // currently-focused notebook; calling NotebookActions.run with our
        // resolved target avoids stealing focus from whichever tab the user
        // is on while the agent is running.
        await NotebookActions.run(np.content, np.sessionContext);
      }
    });

    app.commands.addCommand(CommandIDs.getCurrentFileContent, {
      execute: async args => {
        if (!ensureAFileEditorIsActive()) {
          return false;
        }

        const currentWidget = app.shell.currentWidget as FileEditorWidget;
        const editor = currentWidget.content.editor;
        return editor.model.sharedModel.getSource();
      }
    });

    app.commands.addCommand(CommandIDs.setCurrentFileContent, {
      execute: async args => {
        if (!ensureAFileEditorIsActive()) {
          return false;
        }

        const currentWidget = app.shell.currentWidget as FileEditorWidget;
        const editor = currentWidget.content.editor;
        editor.model.sharedModel.setSource(args.content as string);
        return editor.model.sharedModel.getSource();
      }
    });

    app.commands.addCommand(CommandIDs.openGitHubCopilotLoginDialog, {
      execute: args => {
        let dialog: Dialog<unknown> | null = null;
        const dialogBody = new GitHubCopilotLoginDialogBody({
          onLoggedIn: () => dialog?.dispose()
        });
        dialog = new Dialog({
          title: 'GitHub Copilot Status',
          hasClose: true,
          body: dialogBody,
          buttons: []
        });

        dialog.launch();
      }
    });

    const createNewSettingsWidget = () => {
      const settingsPanel = new SettingsPanel({
        onSave: () => {
          NBIAPI.fetchCapabilities();
        },
        onEditMCPConfigClicked: () => {
          app.commands.execute('notebook-intelligence:open-mcp-config-editor');
        }
      });

      const widget = new MainAreaWidget({ content: settingsPanel });
      widget.id = 'nbi-settings';
      widget.title.label = 'NBI Settings';
      widget.title.closable = true;

      return widget;
    };

    let settingsWidget = createNewSettingsWidget();

    app.commands.addCommand(CommandIDs.openConfigurationDialog, {
      label: 'Notebook Intelligence Settings',
      execute: args => {
        if (settingsWidget.isDisposed) {
          settingsWidget = createNewSettingsWidget();
        }
        if (!settingsWidget.isAttached) {
          app.shell.add(settingsWidget, 'main');
        }
        app.shell.activateById(settingsWidget.id);
      }
    });

    app.commands.addCommand(CommandIDs.openMCPConfigEditor, {
      label: 'Open MCP Config Editor',
      execute: args => {
        if (mcpConfigEditor && mcpConfigEditor.isOpen) {
          mcpConfigEditor.close();
        }
        mcpConfigEditor = new MCPConfigEditor(docManager);
        mcpConfigEditor.open();
      }
    });

    palette.addItem({
      command: CommandIDs.openConfigurationDialog,
      category: 'Notebook Intelligence'
    });
    palette.addItem({
      command: CommandIDs.focusChatInput,
      category: 'Notebook Intelligence'
    });

    mainMenu.settingsMenu.addGroup([
      {
        command: CommandIDs.openConfigurationDialog
      }
    ]);

    const getPrefixAndSuffixForActiveCell = (): {
      prefix: string;
      suffix: string;
    } => {
      let prefix = '';
      let suffix = '';
      const currentWidget = app.shell.currentWidget;
      if (
        !(
          currentWidget instanceof NotebookPanel &&
          currentWidget.content.activeCell
        )
      ) {
        return { prefix, suffix };
      }

      const activeCellIndex = currentWidget.content.activeCellIndex;
      const numCells = currentWidget.content.widgets.length;
      const maxContext = 0.7 * MAX_TOKENS;

      for (let d = 1; d < numCells; ++d) {
        const above = activeCellIndex - d;
        const below = activeCellIndex + d;
        if (
          (above < 0 && below >= numCells) ||
          getTokenCount(`${prefix} ${suffix}`) >= maxContext
        ) {
          break;
        }

        if (above >= 0) {
          const aboveCell = currentWidget.content.widgets[above];
          const cellModel = aboveCell.model.sharedModel;

          if (cellModel.cell_type === 'code') {
            prefix = cellModel.source + '\n' + prefix;
          } else if (cellModel.cell_type === 'markdown') {
            prefix = markdownToComment(cellModel.source) + '\n' + prefix;
          }
        }

        if (below < numCells) {
          const belowCell = currentWidget.content.widgets[below];
          const cellModel = belowCell.model.sharedModel;

          if (cellModel.cell_type === 'code') {
            suffix += cellModel.source + '\n';
          } else if (cellModel.cell_type === 'markdown') {
            suffix += markdownToComment(cellModel.source) + '\n';
          }
        }
      }

      return { prefix, suffix };
    };

    const getPrefixAndSuffixForFileEditor = (): {
      prefix: string;
      suffix: string;
    } => {
      let prefix = '';
      let suffix = '';
      const currentWidget = app.shell.currentWidget;
      if (!(currentWidget instanceof FileEditorWidget)) {
        return { prefix, suffix };
      }

      const fe = currentWidget as FileEditorWidget;

      const cursor = fe.content.editor.getCursorPosition();
      const offset = fe.content.editor.getOffsetAt(cursor);
      const source = fe.content.editor.model.sharedModel.getSource();
      prefix = source.substring(0, offset);
      suffix = source.substring(offset);

      return { prefix, suffix };
    };

    const generateCodeForCellOrFileEditor = () => {
      const isCodeCell = isActiveCellCodeCell();
      const currentWidget = app.shell.currentWidget;
      let editor: CodeEditor.IEditor;
      let codeInput: HTMLElement | null = null;
      if (isCodeCell) {
        const np = currentWidget as NotebookPanel;
        const activeCell = np.content.activeCell;
        codeInput = activeCell.node.querySelector('.jp-InputArea-editor');
        if (!codeInput) {
          return;
        }
        editor = activeCell.editor;
      } else {
        const fe = currentWidget as FileEditorWidget;
        editor = fe.content.editor;
      }

      const editorView = getCodeMirrorView(editor);
      if (!editorView) {
        return;
      }

      let blockPromptView: EditorView | null = null;
      let removed = false;
      // Acceptance telemetry state: whether generated code was ever shown to
      // the user, and whether they applied it. A teardown that happens after
      // code was shown but never applied is a dismissal.
      let generatedCodeShown = false;
      let codeApplied = false;
      // A backend stream error surfaces a visible [Stream interrupted] marker
      // but no usable code, so tearing the popover down afterwards is a failure,
      // not a user dismissal. Tracked separately from generatedCodeShown because
      // the review path re-renders the marker into the diff (which re-sets
      // generatedCodeShown) after the stream-end callback has run.
      let streamErrored = false;
      const removePopover = () => {
        // Cleared outside the `removed` guard so the auto-insert path's
        // second call still removes the class added between calls.
        if (isCodeCell) {
          codeInput?.classList.remove('generating');
        }

        if (removed) {
          return;
        }
        removed = true;
        closeOpenPopover = null;

        if (generatedCodeShown && !codeApplied && !streamErrored) {
          telemetryEmitter.emitTelemetryEvent({
            type: TelemetryEventType.InlineChatDismissed,
            data: {
              chatModel: {
                provider: NBIAPI.config.chatModel.provider,
                model: NBIAPI.config.chatModel.model
              }
            }
          });
        }

        if (blockPromptView) {
          blockPromptView.dispatch({
            effects: removeInlinePromptEffect.of()
          });
          blockPromptView = null;
        }
      };

      let userPrompt = '';
      let existingCode = '';
      let generatedContent = '';

      let prefix = '',
        suffix = '';
      if (isCodeCell) {
        const ps = getPrefixAndSuffixForActiveCell();
        prefix = ps.prefix;
        suffix = ps.suffix;
      } else {
        const ps = getPrefixAndSuffixForFileEditor();
        prefix = ps.prefix;
        suffix = ps.suffix;
      }
      const selection = editor.getSelection();

      const startOffset = editor.getOffsetAt(selection.start);
      const endOffset = editor.getOffsetAt(selection.end);
      const source = editor.model.sharedModel.getSource();

      if (isCodeCell) {
        prefix += '\n' + source.substring(0, startOffset);
        existingCode = source.substring(startOffset, endOffset);
        suffix = source.substring(endOffset) + '\n' + suffix;
      } else {
        existingCode = source.substring(startOffset, endOffset);
      }

      const applyGeneratedCode = () => {
        codeApplied = true;
        telemetryEmitter.emitTelemetryEvent({
          type: TelemetryEventType.InlineChatAccepted,
          data: {
            chatModel: {
              provider: NBIAPI.config.chatModel.provider,
              model: NBIAPI.config.chatModel.model
            },
            // 'review' = user accepted the diff for a selection; 'auto-insert'
            // = no selection, so the generated code was inserted directly.
            mode: existingCode !== '' ? 'review' : 'auto-insert'
          }
        });
        generatedContent = extractLLMGeneratedCode(generatedContent);
        // extractLLMGeneratedCode preserves the newline that sits before
        // the closing ``` in fenced LLM output. If the user's selection
        // didn't already end with a newline (or there's no selection at
        // all in the auto-insert path), inserting that trailing \n leaves
        // an extra blank line below the generated code. Strip one
        // trailing newline in those cases so the result matches the
        // selection's original line-break state.
        if (!existingCode.endsWith('\n') && generatedContent.endsWith('\n')) {
          generatedContent = generatedContent.slice(0, -1);
        }
        applyCodeToSelectionInEditor(editor, generatedContent);
        generatedContent = '';
        removePopover();
      };

      closeOpenPopover?.();

      const promptOptions: IInlinePromptWidgetOptions = {
        prompt: userPrompt,
        existingCode,
        prefix: prefix,
        suffix: suffix,
        language: ActiveDocumentWatcher.activeDocumentInfo.language,
        kernelName: ActiveDocumentWatcher.activeDocumentInfo.kernelName,
        filename: ActiveDocumentWatcher.activeDocumentInfo.filePath,
        onRequestSubmitted: (prompt: string) => {
          userPrompt = prompt;
          generatedContent = '';
          // Reset per-generation telemetry state: the review popover survives
          // across re-submits, so a fresh prompt must not inherit the prior
          // run's "shown"/"errored" flags (e.g. an errored attempt followed by
          // a successful one the user then dismisses).
          generatedCodeShown = false;
          streamErrored = false;
          if (existingCode !== '') {
            return;
          }
          removePopover();
          if (isCodeCell) {
            codeInput?.classList.add('generating');
          }
        },
        onRequestCancelled: () => {
          removePopover();
          editor.focus();
        },
        onContentStream: (content: string) => {
          if (existingCode !== '') {
            return;
          }
          generatedContent += content;
          if (generatedContent !== '') {
            generatedCodeShown = true;
          }
        },
        onContentStreamEnd: (streamError?: string | null) => {
          if (streamError) {
            // Recorded before the auto-insert-only early return below so both
            // paths see it: the review path keeps the errored diff on screen
            // for the user to discard, which must not count as a dismissal.
            streamErrored = true;
          }
          if (existingCode !== '') {
            return;
          }
          if (streamError) {
            // The backend tagged this stream as interrupted. Discard the
            // partial buffer rather than auto-inserting truncated code (or
            // worse, the [Stream interrupted] marker text itself) into the
            // user's cell, and surface the failure as a toast.
            generatedContent = '';
            removePopover();
            app.commands.execute('apputils:notify', {
              message: `Inline chat failed: ${streamError}`,
              type: 'error',
              options: { autoClose: true }
            });
            editor.focus();
            return;
          }
          applyGeneratedCode();
          editor.focus();
        },
        onUpdatedCodeChange: (content: string) => {
          generatedContent = content;
          if (content !== '') {
            generatedCodeShown = true;
          }
        },
        onUpdatedCodeAccepted: () => {
          applyGeneratedCode();
          editor.focus();
        },
        telemetryEmitter: telemetryEmitter
      };

      let requestTime: Date | null = null;
      let streamError: string | null = null;
      let blockPromptNode: HTMLElement | null = null;
      const onRequestSubmitted = (prompt: string) => {
        if (blockPromptNode && existingCode !== '') {
          blockPromptNode.style.height = '300px';
        }
        promptOptions.prompt = prompt;
        promptOptions.onRequestSubmitted(prompt);
        requestTime = new Date();
        telemetryEmitter.emitTelemetryEvent({
          type: TelemetryEventType.InlineChatRequest,
          data: {
            chatModel: {
              provider: NBIAPI.config.chatModel.provider,
              model: NBIAPI.config.chatModel.model
            },
            prompt: prompt
          }
        });
      };
      const onResponseEmit = (response: any) => {
        if (response.type === BackendMessageType.StreamMessage) {
          if (typeof response.data?.nbi_stream_error === 'string') {
            streamError = response.data.nbi_stream_error;
          }
          const responseMessage =
            response.data['choices']?.[0]?.['delta']?.['content'];
          if (responseMessage) {
            promptOptions.onContentStream(responseMessage);
          }
        } else if (response.type === BackendMessageType.StreamEnd) {
          promptOptions.onContentStreamEnd(streamError);
          streamError = null;
          const timeElapsed =
            requestTime === null
              ? 0
              : (new Date().getTime() - requestTime.getTime()) / 1000;
          telemetryEmitter.emitTelemetryEvent({
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
      };
      // Handed up by InlinePopoverComponent on mount so this scope can
      // cancel the in-flight WS request from non-React dismissal paths
      // (focus-leave) without going through onRequestCancelled (which
      // would also yank focus back to the editor).
      let cancelInflightRequest: (() => void) | null = null;
      const widget = new InlinePromptBlockWidget(
        React.createElement(InlinePopoverComponent, {
          prompt: promptOptions.prompt,
          existingCode: promptOptions.existingCode,
          onRequestSubmitted,
          onRequestCancelled: promptOptions.onRequestCancelled,
          onResponseEmit,
          prefix: promptOptions.prefix,
          suffix: promptOptions.suffix,
          language: promptOptions.language,
          kernelName: promptOptions.kernelName,
          filename: promptOptions.filename,
          onUpdatedCodeChange: promptOptions.onUpdatedCodeChange,
          onUpdatedCodeAccepted: promptOptions.onUpdatedCodeAccepted,
          registerCancel: (fn: (() => void) | null) => {
            cancelInflightRequest = fn;
          }
        }),
        node => {
          blockPromptNode = node;
        },
        // Focus-leave dismissal: cancel the request so the backend stops
        // streaming, but don't call onRequestCancelled — that would steal
        // focus back from whatever the user clicked.
        () => {
          cancelInflightRequest?.();
          removePopover();
        }
      );
      const anchorOffset = getLineEndOffset(
        editor,
        Math.max(startOffset, endOffset)
      );
      ensureInlinePromptExtension(editorView);
      blockPromptView = editorView;
      // Replace-on-reopen path: when a second Ctrl+G fires while this
      // popover is still here, cancel our in-flight request before the
      // widget is torn down so the backend stops streaming for the
      // discarded prompt.
      closeOpenPopover = () => {
        cancelInflightRequest?.();
        removePopover();
      };
      editorView.dispatch({
        effects: [
          addInlinePromptEffect.of({
            pos: anchorOffset,
            widget
          }),
          EditorView.scrollIntoView(anchorOffset, { y: 'center' })
        ]
      });

      telemetryEmitter.emitTelemetryEvent({
        type: TelemetryEventType.GenerateCodeRequest,
        data: {
          chatModel: {
            provider: NBIAPI.config.chatModel.provider,
            model: NBIAPI.config.chatModel.model
          },
          editorType: isCodeCell ? 'notebook' : 'file-editor'
        }
      });
    };

    const generateCellCodeCommand: CommandRegistry.ICommandOptions = {
      execute: args => {
        generateCodeForCellOrFileEditor();
      },
      label: 'Generate code',
      isEnabled: () =>
        isChatEnabled() &&
        (isActiveCellCodeCell() || isCurrentWidgetFileEditor())
    };
    app.commands.addCommand(
      CommandIDs.editorGenerateCode,
      generateCellCodeCommand
    );

    const copilotMenuCommands = new CommandRegistry();
    copilotMenuCommands.addCommand(
      CommandIDs.editorGenerateCode,
      generateCellCodeCommand
    );
    copilotMenuCommands.addCommand(CommandIDs.editorExplainThisCode, {
      execute: () => {
        const np = app.shell.currentWidget as NotebookPanel;
        const activeCell = np.content.activeCell;
        const content = activeCell?.model.sharedModel.source || '';
        document.dispatchEvent(
          new CustomEvent('copilotSidebar:runPrompt', {
            detail: {
              type: RunChatCompletionType.ExplainThis,
              content,
              language: ActiveDocumentWatcher.activeDocumentInfo.language,
              filename: ActiveDocumentWatcher.activeDocumentInfo.filename
            }
          })
        );

        app.commands.execute('tabsmenu:activate-by-id', { id: panel.id });

        telemetryEmitter.emitTelemetryEvent({
          type: TelemetryEventType.ExplainThisRequest,
          data: {
            chatModel: {
              provider: NBIAPI.config.chatModel.provider,
              model: NBIAPI.config.chatModel.model
            }
          }
        });
      },
      label: 'Explain code',
      isEnabled: () => isChatEnabled() && isActiveCellCodeCell()
    });
    copilotMenuCommands.addCommand(CommandIDs.editorFixThisCode, {
      execute: () => {
        const np = app.shell.currentWidget as NotebookPanel;
        const activeCell = np.content.activeCell;
        const content = activeCell?.model.sharedModel.source || '';
        document.dispatchEvent(
          new CustomEvent('copilotSidebar:runPrompt', {
            detail: {
              type: RunChatCompletionType.FixThis,
              content,
              language: ActiveDocumentWatcher.activeDocumentInfo.language,
              filename: ActiveDocumentWatcher.activeDocumentInfo.filename
            }
          })
        );

        app.commands.execute('tabsmenu:activate-by-id', { id: panel.id });

        telemetryEmitter.emitTelemetryEvent({
          type: TelemetryEventType.FixThisCodeRequest,
          data: {
            chatModel: {
              provider: NBIAPI.config.chatModel.provider,
              model: NBIAPI.config.chatModel.model
            }
          }
        });
      },
      label: 'Fix code',
      isEnabled: () => isChatEnabled() && isActiveCellCodeCell()
    });
    const registerOutputContextCommand = (opts: {
      commandId: string;
      label: string;
      telemetryType: TelemetryEventType;
      autoSubmitPrompt?: string;
      featureFlag?: CellOutputActionFlag;
      requireError?: boolean;
    }) => {
      const isFlagOn = () =>
        !opts.featureFlag ||
        NBIAPI.config.cellOutputFeatures[opts.featureFlag].enabled;

      copilotMenuCommands.addCommand(opts.commandId, {
        execute: () => {
          const np = app.shell.currentWidget as NotebookPanel;
          const activeCell = np.content.activeCell;
          if (!(activeCell instanceof CodeCell)) {
            return;
          }
          const outputContext = cellOutputAsContextBundle(
            activeCell as CodeCell,
            { supportsVision: NBIAPI.config.chatModelSupportsVision }
          );
          document.dispatchEvent(
            new CustomEvent('copilotSidebar:addOutputContext', {
              detail: {
                outputContext,
                cellIndex: np.content.activeCellIndex,
                notebookFilename: np.sessionContext.name,
                cellId: activeCell.model.id,
                autoSubmitPrompt: opts.autoSubmitPrompt
              }
            })
          );
          app.commands.execute('tabsmenu:activate-by-id', { id: panel.id });
          telemetryEmitter.emitTelemetryEvent({
            type: opts.telemetryType,
            data: {
              chatModel: {
                provider: NBIAPI.config.chatModel.provider,
                model: NBIAPI.config.chatModel.model
              }
            }
          });
        },
        label: opts.label,
        isEnabled: () => {
          if (
            !(
              isChatEnabled() &&
              app.shell.currentWidget instanceof NotebookPanel
            )
          ) {
            return false;
          }
          if (!isFlagOn()) {
            return false;
          }
          const np = app.shell.currentWidget as NotebookPanel;
          const activeCell = np.content.activeCell;
          if (!(activeCell instanceof CodeCell)) {
            return false;
          }
          if (activeCell.outputArea.model.length === 0) {
            return false;
          }
          if (opts.requireError) {
            return cellOutputHasError(activeCell);
          }
          return true;
        },
        isVisible: opts.featureFlag ? isFlagOn : undefined
      });
    };

    registerOutputContextCommand({
      commandId: CommandIDs.editorExplainThisOutput,
      label: 'Explain output',
      telemetryType: TelemetryEventType.ExplainThisOutputRequest,
      autoSubmitPrompt: "Explain this cell's output.",
      featureFlag: 'output_followup'
    });
    registerOutputContextCommand({
      commandId: CommandIDs.editorAskAboutThisOutput,
      label: 'Ask about this output',
      telemetryType: TelemetryEventType.OutputFollowUpRequest,
      featureFlag: 'output_followup'
    });
    registerOutputContextCommand({
      commandId: CommandIDs.editorTroubleshootThisOutput,
      label: 'Troubleshoot errors in output',
      telemetryType: TelemetryEventType.TroubleshootThisOutputRequest,
      autoSubmitPrompt: "Troubleshoot the error in this cell's output.",
      featureFlag: 'explain_error',
      requireError: true
    });

    const copilotContextMenu = new Menu({ commands: copilotMenuCommands });
    copilotContextMenu.id = 'notebook-intelligence:editor-context-menu';
    copilotContextMenu.title.label = 'Notebook Intelligence';
    copilotContextMenu.title.icon = sidebarIcon;
    copilotContextMenu.addItem({ command: CommandIDs.editorGenerateCode });
    copilotContextMenu.addItem({ command: CommandIDs.editorExplainThisCode });
    copilotContextMenu.addItem({ command: CommandIDs.editorFixThisCode });
    copilotContextMenu.addItem({ command: CommandIDs.editorExplainThisOutput });
    copilotContextMenu.addItem({
      command: CommandIDs.editorAskAboutThisOutput
    });
    copilotContextMenu.addItem({
      command: CommandIDs.editorTroubleshootThisOutput
    });

    app.contextMenu.addItem({
      type: 'submenu',
      submenu: copilotContextMenu,
      selector: '.jp-Editor',
      rank: 1
    });

    app.contextMenu.addItem({
      type: 'submenu',
      submenu: copilotContextMenu,
      selector: '.jp-OutputArea-child',
      rank: 1
    });

    new CellOutputHoverToolbar(app, copilotMenuCommands);

    if (statusBar) {
      const githubCopilotStatusBarItem = new GitHubCopilotStatusBarItem({
        getApp: () => app
      });

      statusBar.registerStatusItem(
        'notebook-intelligence:github-copilot-status',
        {
          item: githubCopilotStatusBarItem,
          align: 'right',
          rank: 100,
          isActive: () =>
            !NBIAPI.config.isInClaudeCodeMode &&
            NBIAPI.config.usingGitHubCopilotModel
        }
      );

      NBIAPI.configChanged.connect(() => {
        if (
          !NBIAPI.config.isInClaudeCodeMode &&
          NBIAPI.config.usingGitHubCopilotModel
        ) {
          githubCopilotStatusBarItem.show();
        } else {
          githubCopilotStatusBarItem.hide();
        }
      });
    }

    const jlabApp = app as JupyterLab;
    ActiveDocumentWatcher.initialize(jlabApp, languageRegistry, defaultBrowser);

    return extensionService;
  }
};

export * from './tokens';

export default plugin;

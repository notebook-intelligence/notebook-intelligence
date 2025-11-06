// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin,
  JupyterLab
} from '@jupyterlab/application';

import { IDocumentManager } from '@jupyterlab/docmanager';
import { DocumentWidget, IDocumentWidget } from '@jupyterlab/docregistry';

import { Dialog, ICommandPalette, MainAreaWidget } from '@jupyterlab/apputils';
import { IMainMenu } from '@jupyterlab/mainmenu';

import { IEditorLanguageRegistry } from '@jupyterlab/codemirror';

import { CodeCell } from '@jupyterlab/cells';
import { ISharedNotebook } from '@jupyter/ydoc';

import { ISettingRegistry } from '@jupyterlab/settingregistry';

import {
  CompletionHandler,
  ICompletionProviderManager,
  IInlineCompletionContext,
  IInlineCompletionItem,
  IInlineCompletionList,
  IInlineCompletionProvider
} from '@jupyterlab/completer';

import { NotebookPanel } from '@jupyterlab/notebook';
import { CodeEditor } from '@jupyterlab/codeeditor';
import { FileEditorWidget } from '@jupyterlab/fileeditor';

import { IDefaultFileBrowser } from '@jupyterlab/filebrowser';

import { ContentsManager, KernelSpecManager } from '@jupyterlab/services';

import { LabIcon } from '@jupyterlab/ui-components';

import { Menu, Panel, Widget } from '@lumino/widgets';
import { CommandRegistry } from '@lumino/commands';
import { IStatusBar } from '@jupyterlab/statusbar';

import {
  ChatSidebar,
  FormInputDialogBody,
  GitHubCopilotLoginDialogBody,
  GitHubCopilotStatusBarItem,
  InlinePromptWidget,
  RunChatCompletionType
} from './chat-sidebar';
import { NBIAPI, GitHubCopilotLoginStatus } from './api';
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

import {
  applyCodeToSelectionInEditor,
  cellOutputAsText,
  compareSelections,
  extractLLMGeneratedCode,
  getSelectionInEditor,
  getTokenCount,
  getWholeNotebookContent,
  isSelectionEmpty,
  markdownToComment,
  waitForDuration
} from './utils';
import { UUID } from '@lumino/coreutils';

import * as path from 'path';
import { SettingsPanel } from './components/settings-panel';

namespace CommandIDs {
  export const chatuserInput = 'notebook-intelligence:chat-user-input';
  export const insertAtCursor = 'notebook-intelligence:insert-at-cursor';
  export const addCodeAsNewCell = 'notebook-intelligence:add-code-as-new-cell';
  export const createNewFile = 'notebook-intelligence:create-new-file';
  export const createNewNotebookFromPython =
    'notebook-intelligence:create-new-notebook-from-py';
  export const renameNotebook = 'notebook-intelligence:rename-notebook';
  export const addCodeCellToNotebook =
    'notebook-intelligence:add-code-cell-to-notebook';
  export const addMarkdownCellToNotebook =
    'notebook-intelligence:add-markdown-cell-to-notebook';
  export const editorGenerateCode =
    'notebook-intelligence:editor-generate-code';
  export const editorExplainThisCode =
    'notebook-intelligence:editor-explain-this-code';
  export const editorFixThisCode = 'notebook-intelligence:editor-fix-this-code';
  export const editorExplainThisOutput =
    'notebook-intelligence:editor-explain-this-output';
  export const editorTroubleshootThisOutput =
    'notebook-intelligence:editor-troubleshoot-this-output';
  export const openGitHubCopilotLoginDialog =
    'notebook-intelligence:open-github-copilot-login-dialog';
  export const openConfigurationDialog =
    'notebook-intelligence:open-configuration-dialog';
  export const addMarkdownCellToActiveNotebook =
    'notebook-intelligence:add-markdown-cell-to-active-notebook';
  export const addCodeCellToActiveNotebook =
    'notebook-intelligence:add-code-cell-to-active-notebook';
  export const deleteCellAtIndex = 'notebook-intelligence:delete-cell-at-index';
  export const insertCellAtIndex = 'notebook-intelligence:insert-cell-at-index';
  export const getCellTypeAndSource =
    'notebook-intelligence:get-cell-type-and-source';
  export const setCellTypeAndSource =
    'notebook-intelligence:set-cell-type-and-source';
  export const getNumberOfCells = 'notebook-intelligence:get-number-of-cells';
  export const getCellOutput = 'notebook-intelligence:get-cell-output';
  export const runCellAtIndex = 'notebook-intelligence:run-cell-at-index';
  export const getCurrentFileContent =
    'notebook-intelligence:get-current-file-content';
  export const setCurrentFileContent =
    'notebook-intelligence:set-current-file-content';
  export const openMCPConfigEditor =
    'notebook-intelligence:open-mcp-config-editor';
  export const showFormInputDialog =
    'notebook-intelligence:show-form-input-dialog';
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
    languageRegistry: IEditorLanguageRegistry
  ) {
    ActiveDocumentWatcher._languageRegistry = languageRegistry;

    app.shell.currentChanged?.connect((_sender, args) => {
      ActiveDocumentWatcher.watchDocument(args.newValue);
    });

    ActiveDocumentWatcher.activeDocumentInfo.activeWidget =
      app.shell.currentWidget;
    ActiveDocumentWatcher.handleWatchDocument();
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
      activeDocumentInfo.language =
        (np.model?.sharedModel?.metadata?.kernelspec?.language as string) ||
        'python';
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

  static activeDocumentInfo: IActiveDocumentInfo = {
    language: 'python',
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
        debouncerDelay: 200,
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
      nbiConfig.inlineCompletionModel.provider === GITHUB_COPILOT_PROVIDER_ID
        ? NBIAPI.getLoginStatus() === GitHubCopilotLoginStatus.LoggedIn
        : nbiConfig.inlineCompletionModel.provider !== 'none';

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
    return '@notebook-intelligence/notebook-intelligence';
  }

  get icon(): LabIcon.ILabIcon {
    return NBIAPI.config.usingGitHubCopilotModel
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
    await NBIAPI.setMCPConfigFile(mcpConfig);
    await NBIAPI.fetchCapabilities();
  }

  private _docManager: IDocumentManager;
  private _docWidget: IDocumentWidget = null;
  private _tmpMCPConfigFilename = 'nbi.mcp.temp.json';
  private _isOpen = false;
}

/**
 * Initialization data for the @notebook-intelligence/notebook-intelligence extension.
 */
const plugin: JupyterFrontEndPlugin<INotebookIntelligence> = {
  id: '@notebook-intelligence/notebook-intelligence:plugin',
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
  optional: [ISettingRegistry, IStatusBar],
  provides: INotebookIntelligence,
  activate: async (
    app: JupyterFrontEnd,
    completionManager: ICompletionProviderManager,
    docManager: IDocumentManager,
    defaultBrowser: IDefaultFileBrowser,
    languageRegistry: IEditorLanguageRegistry,
    palette: ICommandPalette,
    mainMenu: IMainMenu,
    settingRegistry: ISettingRegistry | null,
    statusBar: IStatusBar | null
  ) => {
    console.log(
      'JupyterLab extension @notebook-intelligence/notebook-intelligence is activated!'
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

    let openPopover: InlinePromptWidget | null = null;
    let mcpConfigEditor: MCPConfigEditor | null = null;

    completionManager.registerInlineProvider(
      new NBIInlineCompletionProvider(telemetryEmitter)
    );

    if (settingRegistry) {
      settingRegistry
        .load(plugin.id)
        .then(settings => {
          //
        })
        .catch(reason => {
          console.error(
            'Failed to load settings for @notebook-intelligence/notebook-intelligence.',
            reason
          );
        });
    }

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
      name: 'ui-components:palette',
      svgstr: sparklesSvgstr
    });
    panel.title.icon = sidebarIcon;
    const sidebar = new ChatSidebar({
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

    app.commands.addCommand(CommandIDs.createNewNotebookFromPython, {
      execute: async args => {
        let pythonKernelSpec = null;
        const contents = new ContentsManager();
        const kernels = new KernelSpecManager();
        await kernels.ready;
        const kernelspecs = kernels.specs?.kernelspecs;
        if (kernelspecs) {
          for (const key in kernelspecs) {
            const kernelspec = kernelspecs[key];
            if (kernelspec?.language === 'python') {
              pythonKernelSpec = kernelspec;
              break;
            }
          }
        }

        const newNBFile = await contents.newUntitled({
          ext: '.ipynb',
          path: defaultBrowser?.model.path
        });
        const nbFileContent = structuredClone(emptyNotebookContent);
        if (pythonKernelSpec) {
          nbFileContent.metadata = {
            kernelspec: {
              language: 'python',
              name: pythonKernelSpec.name,
              display_name: pythonKernelSpec.display_name
            }
          };
        }

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
      return NBIAPI.config.chatModel.provider === GITHUB_COPILOT_PROVIDER_ID
        ? !githubLoginRequired()
        : NBIAPI.config.chatModel.provider !== 'none';
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
      const currentWidget = app.shell.currentWidget;
      const notebookOpen =
        currentWidget instanceof NotebookPanel &&
        currentWidget.sessionContext.path === filePath &&
        currentWidget.model;
      if (!notebookOpen) {
        app.commands.execute('apputils:notify', {
          message: `Failed to access the notebook: ${filePath}`,
          type: 'error',
          options: { autoClose: true }
        });
        return false;
      }

      const model = currentWidget.model.sharedModel;

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

    const ensureANotebookIsActive = (): boolean => {
      const currentWidget = app.shell.currentWidget;
      const notebookOpen =
        currentWidget instanceof NotebookPanel && currentWidget.model;
      if (!notebookOpen) {
        app.commands.execute('apputils:notify', {
          message: 'Failed to find active notebook',
          type: 'error',
          options: { autoClose: true }
        });
        return false;
      }

      return true;
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
        if (!ensureANotebookIsActive()) {
          return false;
        }

        const np = app.shell.currentWidget as NotebookPanel;
        const model = np.model.sharedModel;

        const newCellIndex = isNewEmptyNotebook(model)
          ? 0
          : model.cells.length - 1;
        model.insertCell(newCellIndex, {
          cell_type: 'markdown',
          metadata: { trusted: true },
          source: args.source as string
        });

        return true;
      }
    });

    app.commands.addCommand(CommandIDs.addCodeCellToActiveNotebook, {
      execute: args => {
        if (!ensureANotebookIsActive()) {
          return false;
        }

        const np = app.shell.currentWidget as NotebookPanel;
        const model = np.model.sharedModel;

        const newCellIndex = isNewEmptyNotebook(model)
          ? 0
          : model.cells.length - 1;
        model.insertCell(newCellIndex, {
          cell_type: 'code',
          metadata: { trusted: true },
          source: args.source as string
        });

        return true;
      }
    });

    app.commands.addCommand(CommandIDs.getCellTypeAndSource, {
      execute: args => {
        if (!ensureANotebookIsActive()) {
          return false;
        }

        const np = app.shell.currentWidget as NotebookPanel;
        const model = np.model.sharedModel;

        return {
          type: model.cells[args.cellIndex as number].cell_type,
          source: model.cells[args.cellIndex as number].source
        };
      }
    });

    app.commands.addCommand(CommandIDs.setCellTypeAndSource, {
      execute: args => {
        if (!ensureANotebookIsActive()) {
          return false;
        }

        const np = app.shell.currentWidget as NotebookPanel;
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
        if (!ensureANotebookIsActive()) {
          return false;
        }

        const np = app.shell.currentWidget as NotebookPanel;
        const model = np.model.sharedModel;

        return model.cells.length;
      }
    });

    app.commands.addCommand(CommandIDs.getCellOutput, {
      execute: args => {
        if (!ensureANotebookIsActive()) {
          return false;
        }

        const np = app.shell.currentWidget as NotebookPanel;
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
        if (!ensureANotebookIsActive()) {
          return false;
        }

        const np = app.shell.currentWidget as NotebookPanel;
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
        if (!ensureANotebookIsActive()) {
          return false;
        }

        const np = app.shell.currentWidget as NotebookPanel;
        const model = np.model.sharedModel;
        const cellIndex = args.cellIndex as number;

        model.deleteCell(cellIndex);

        return true;
      }
    });

    app.commands.addCommand(CommandIDs.runCellAtIndex, {
      execute: async args => {
        if (!ensureANotebookIsActive()) {
          return false;
        }

        const currentWidget = app.shell.currentWidget as NotebookPanel;
        currentWidget.content.activeCellIndex = args.cellIndex as number;

        await app.commands.execute('notebook:run-cell');
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
      let codeInput: HTMLElement = null;
      let scrollEl: HTMLElement = null;
      if (isCodeCell) {
        const np = currentWidget as NotebookPanel;
        const activeCell = np.content.activeCell;
        codeInput = activeCell.node.querySelector('.jp-InputArea-editor');
        if (!codeInput) {
          return;
        }
        scrollEl = np.node.querySelector('.jp-WindowedPanel-outer');
        editor = activeCell.editor;
      } else {
        const fe = currentWidget as FileEditorWidget;
        codeInput = fe.node;
        scrollEl = fe.node.querySelector('.cm-scroller');
        editor = fe.content.editor;
      }

      const getRectAtCursor = (): DOMRect => {
        const selection = editor.getSelection();
        const line = Math.min(selection.end.line + 1, editor.lineCount - 1);
        const coords = editor.getCoordinateForPosition({
          line,
          column: selection.end.column
        });
        const editorRect = codeInput.getBoundingClientRect();
        if (!coords) {
          return editorRect;
        }
        const yOffset = 30;
        const diffViewHeight = 300;

        let top = coords.top - yOffset;
        const height = coords.bottom - coords.top;

        // adjust top to fit in file editor rect
        if (!isCodeCell) {
          if (top + height + diffViewHeight > editorRect.bottom) {
            top = editorRect.bottom - height - diffViewHeight;
            if (top < editorRect.top) {
              top = editorRect.top;
            }
          }
        }

        const rect: DOMRect = new DOMRect(
          editorRect.left,
          top,
          editorRect.right - editorRect.left,
          height
        );

        return rect;
      };

      const rect = getRectAtCursor();

      const updatePopoverPosition = () => {
        if (openPopover !== null) {
          const rect = getRectAtCursor();
          openPopover.updatePosition(rect);
        }
      };

      const inputResizeObserver = new ResizeObserver(updatePopoverPosition);

      const addPositionListeners = () => {
        inputResizeObserver.observe(codeInput);
        if (scrollEl) {
          scrollEl.addEventListener('scroll', updatePopoverPosition);
        }
      };

      const removePositionListeners = () => {
        inputResizeObserver.unobserve(codeInput);
        if (scrollEl) {
          scrollEl.removeEventListener('scroll', updatePopoverPosition);
        }
      };

      const removePopover = () => {
        if (openPopover !== null) {
          removePositionListeners();
          openPopover = null;
          Widget.detach(inlinePrompt);
        }

        if (isCodeCell) {
          codeInput?.classList.remove('generating');
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
        generatedContent = extractLLMGeneratedCode(generatedContent);
        applyCodeToSelectionInEditor(editor, generatedContent);
        generatedContent = '';
        removePopover();
      };

      removePopover();

      const inlinePrompt = new InlinePromptWidget(rect, {
        prompt: userPrompt,
        existingCode,
        prefix: prefix,
        suffix: suffix,
        language: ActiveDocumentWatcher.activeDocumentInfo.language,
        filename: ActiveDocumentWatcher.activeDocumentInfo.filePath,
        onRequestSubmitted: (prompt: string) => {
          userPrompt = prompt;
          generatedContent = '';
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
        },
        onContentStreamEnd: () => {
          if (existingCode !== '') {
            return;
          }
          applyGeneratedCode();
          editor.focus();
        },
        onUpdatedCodeChange: (content: string) => {
          generatedContent = content;
        },
        onUpdatedCodeAccepted: () => {
          applyGeneratedCode();
          editor.focus();
        },
        telemetryEmitter: telemetryEmitter
      });
      openPopover = inlinePrompt;
      addPositionListeners();
      Widget.attach(inlinePrompt, document.body);

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
    copilotMenuCommands.addCommand(CommandIDs.editorExplainThisOutput, {
      execute: () => {
        const np = app.shell.currentWidget as NotebookPanel;
        const activeCell = np.content.activeCell;
        if (!(activeCell instanceof CodeCell)) {
          return;
        }
        const content = cellOutputAsText(activeCell as CodeCell);
        document.dispatchEvent(
          new CustomEvent('copilotSidebar:runPrompt', {
            detail: {
              type: RunChatCompletionType.ExplainThisOutput,
              content,
              language: ActiveDocumentWatcher.activeDocumentInfo.language,
              filename: ActiveDocumentWatcher.activeDocumentInfo.filename
            }
          })
        );

        app.commands.execute('tabsmenu:activate-by-id', { id: panel.id });

        telemetryEmitter.emitTelemetryEvent({
          type: TelemetryEventType.ExplainThisOutputRequest,
          data: {
            chatModel: {
              provider: NBIAPI.config.chatModel.provider,
              model: NBIAPI.config.chatModel.model
            }
          }
        });
      },
      label: 'Explain output',
      isEnabled: () => {
        if (
          !(isChatEnabled() && app.shell.currentWidget instanceof NotebookPanel)
        ) {
          return false;
        }
        const np = app.shell.currentWidget as NotebookPanel;
        const activeCell = np.content.activeCell;
        if (!(activeCell instanceof CodeCell)) {
          return false;
        }
        const outputs = activeCell.outputArea.model.toJSON();
        return Array.isArray(outputs) && outputs.length > 0;
      }
    });
    copilotMenuCommands.addCommand(CommandIDs.editorTroubleshootThisOutput, {
      execute: () => {
        const np = app.shell.currentWidget as NotebookPanel;
        const activeCell = np.content.activeCell;
        if (!(activeCell instanceof CodeCell)) {
          return;
        }
        const content = cellOutputAsText(activeCell as CodeCell);
        document.dispatchEvent(
          new CustomEvent('copilotSidebar:runPrompt', {
            detail: {
              type: RunChatCompletionType.TroubleshootThisOutput,
              content,
              language: ActiveDocumentWatcher.activeDocumentInfo.language,
              filename: ActiveDocumentWatcher.activeDocumentInfo.filename
            }
          })
        );

        app.commands.execute('tabsmenu:activate-by-id', { id: panel.id });

        telemetryEmitter.emitTelemetryEvent({
          type: TelemetryEventType.TroubleshootThisOutputRequest,
          data: {
            chatModel: {
              provider: NBIAPI.config.chatModel.provider,
              model: NBIAPI.config.chatModel.model
            }
          }
        });
      },
      label: 'Troubleshoot errors in output',
      isEnabled: () => {
        if (
          !(isChatEnabled() && app.shell.currentWidget instanceof NotebookPanel)
        ) {
          return false;
        }
        const np = app.shell.currentWidget as NotebookPanel;
        const activeCell = np.content.activeCell;
        if (!(activeCell instanceof CodeCell)) {
          return false;
        }
        const outputs = activeCell.outputArea.model.toJSON();
        return (
          Array.isArray(outputs) &&
          outputs.length > 0 &&
          outputs.some(output => output.output_type === 'error')
        );
      }
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
          isActive: () => NBIAPI.config.usingGitHubCopilotModel
        }
      );

      NBIAPI.configChanged.connect(() => {
        if (NBIAPI.config.usingGitHubCopilotModel) {
          githubCopilotStatusBarItem.show();
        } else {
          githubCopilotStatusBarItem.hide();
        }
      });
    }

    const jlabApp = app as JupyterLab;
    ActiveDocumentWatcher.initialize(jlabApp, languageRegistry);

    return extensionService;
  }
};

export * from './tokens';

export default plugin;

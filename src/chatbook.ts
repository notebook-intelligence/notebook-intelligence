// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { CodeCell } from '@jupyterlab/cells';
import { IEditorLanguageRegistry } from '@jupyterlab/codemirror';
import { EditorView } from '@codemirror/view';
import { ISessionContext, Notification } from '@jupyterlab/apputils';
import { PageConfig, URLExt } from '@jupyterlab/coreutils';
import { INotebookTracker, NotebookPanel } from '@jupyterlab/notebook';
import { Contents, Kernel } from '@jupyterlab/services';
import { JSONObject } from '@lumino/coreutils';
import { IDisposable } from '@lumino/disposable';

import { INotebookKernelProfile } from './notebook-kernels';
import {
  CHATBOOK_LANGUAGE,
  CHATBOOK_MSG_TYPE,
  buildExecuteChatbookMeta,
  buildPythonNotebookFromChatbook,
  canSwitchChatbookCellMode,
  getChatbookCellMode,
  getChatbookCellMeta,
  getChatbookCellOrigin,
  hasChatbookPrompt,
  hasLegacyChatbookCodeView,
  isChatbookKernelName,
  mergeChatbookCellMeta,
  pythonExportNotebookPath,
  resolveChatbookPrompt,
  resolveChatbookPython,
  sha256Hex,
  snapshotChatbookContextCell,
  splitNotebookContext,
  switchChatbookCellMode,
  withoutLegacyChatbookSourceView,
  type ChatbookCellMode,
  type IChatbookCellMeta,
  type IChatbookNotebookContext
} from './chatbook-core';
import { NBIAPI } from './api';
import { setChatbookMentionsEnabled } from './chatbook-mentions';
import { cellOutputAsText } from './utils';

export {
  CHATBOOK_CONVERT_TARGETS,
  CHATBOOK_KERNEL_NAME,
  CHATBOOK_LANGUAGE,
  CHATBOOK_MSG_TYPE,
  getChatbookCellMeta,
  getChatbookCellMode,
  isChatbookConvertTargetId,
  isChatbookKernelName,
  isChatbookPromptInlineCompletion
} from './chatbook-core';

let codeCellExecutePatched = false;

export function isChatbookSession(
  sessionContext: ISessionContext | null | undefined
): boolean {
  if (!sessionContext) {
    return false;
  }
  const kernelName =
    sessionContext.session?.kernel?.name ||
    sessionContext.kernelPreference?.name ||
    '';
  return isChatbookKernelName(kernelName);
}

export function registerChatbookLanguage(
  languageRegistry: IEditorLanguageRegistry
): void {
  if (languageRegistry.findByName(CHATBOOK_LANGUAGE)) {
    return;
  }
  const markdown =
    languageRegistry.findByName('markdown') ||
    languageRegistry.findByName('Markdown') ||
    languageRegistry.findByMIME('text/x-markdown') ||
    languageRegistry.findByMIME('text/markdown');
  languageRegistry.addLanguage({
    name: CHATBOOK_LANGUAGE,
    displayName: 'Chatbook',
    mime: 'text/x-chatbook',
    extensions: ['chatbook'],
    async load() {
      if (markdown) {
        return markdown.load();
      }
      throw new Error('Markdown CodeMirror language is not registered');
    }
  });
}

function chatbookGenerateUrl(): string {
  return URLExt.join(
    PageConfig.getBaseUrl(),
    'notebook-intelligence',
    'chatbook',
    'generate'
  );
}

export function patchCodeCellExecute(): void {
  if (codeCellExecutePatched) {
    return;
  }
  codeCellExecutePatched = true;
  const original = CodeCell.execute.bind(CodeCell);
  CodeCell.execute = async (
    cell: CodeCell,
    sessionContext: ISessionContext,
    metadata?: JSONObject
  ) => {
    if (!isChatbookSession(sessionContext)) {
      return original(cell, sessionContext, metadata);
    }
    const cellMeta = getChatbookCellMeta(cell.model.metadata);
    const notebook = cell.parent?.parent as NotebookPanel | undefined;
    const source = cell.model.sharedModel.getSource();
    const mode = getChatbookCellMode(cellMeta);
    if (mode === 'python') {
      const python = resolveChatbookPython(source, cellMeta);
      writeChatbookCellMeta(cell, {
        mode: 'python',
        origin: getChatbookCellOrigin(cellMeta),
        pythonSource: python,
        generatedCode: python
      });
      const execution = await original(cell, sessionContext, {
        ...(metadata || {}),
        cellId:
          (typeof metadata?.cellId === 'string' && metadata.cellId) ||
          cell.model.id,
        nbi_chatbook: {
          cellId: cell.model.id,
          executeMode: 'python'
        }
      });
      const status = (
        execution as unknown as { content?: { status?: string } } | undefined
      )?.content?.status;
      // A run is the only moment we write the English side of a Python cell,
      // and only when the cell has none yet.
      if (status !== 'error') {
        void summarizePythonCell(cell, python);
      }
      return execution;
    }
    const prompt = resolveChatbookPrompt(source, cellMeta);
    writeChatbookCellMeta(cell, {
      prompt,
      origin: getChatbookCellOrigin(cellMeta)
    });
    const notebookContext = snapshotNotebookContext(notebook, cell);
    const promptHash = await sha256Hex(prompt);
    const contextHash = notebookContext
      ? await sha256Hex(JSON.stringify(notebookContext))
      : undefined;
    const nbiChatbook = buildExecuteChatbookMeta({
      cellId: cell.model.id,
      prompt,
      promptHash,
      cellMeta: getChatbookCellMeta(cell.model.metadata),
      generateUrl: chatbookGenerateUrl(),
      notebookContext,
      contextHash
    }) as JSONObject;
    return original(cell, sessionContext, {
      ...(metadata || {}),
      cellId:
        (typeof metadata?.cellId === 'string' && metadata.cellId) ||
        cell.model.id,
      nbi_chatbook: nbiChatbook
    });
  };
}

/**
 * Fill in the English representation of a Python cell. A cell that already has
 * one keeps it: the summary is generated only for an empty English side, or on
 * explicit request through `force`.
 */
export async function summarizePythonCell(
  cell: IChatbookEditableCell,
  python?: string,
  options: { notifyOnError?: boolean; force?: boolean } = {}
): Promise<string | undefined> {
  const source = python ?? cell.model.sharedModel.getSource();
  if (!source.trim()) {
    return '';
  }
  const meta = getChatbookCellMeta(cell.model.metadata);
  if (!options.force && hasChatbookPrompt(meta)) {
    return meta.prompt;
  }
  const codeHash = await sha256Hex(source);
  writeChatbookCellMeta(cell, {
    mode: 'python',
    origin: getChatbookCellOrigin(meta),
    pythonSource: source,
    generatedCode: source,
    codeHash,
    summaryError: undefined
  });
  try {
    const prompt = await NBIAPI.summarizeChatbookCell(source);
    const latestSource = cell.model.sharedModel.getSource();
    const latestHash = await sha256Hex(latestSource);
    if (
      latestHash !== codeHash ||
      getChatbookCellMode(getChatbookCellMeta(cell.model.metadata)) !== 'python'
    ) {
      return undefined;
    }
    writeChatbookCellMeta(cell, {
      prompt,
      codeHash,
      summaryError: undefined
    });
    return prompt;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    writeChatbookCellMeta(cell, { codeHash, summaryError: message });
    if (options.notifyOnError) {
      Notification.warning(
        `Could not generate English representation: ${message}`
      );
    }
    return undefined;
  }
}

/**
 * Show the cell's other side. Switching only moves what the cell already
 * stores; neither representation is generated here.
 */
export function setChatbookCellMode(
  cell: IChatbookEditableCell,
  nextMode: ChatbookCellMode
): void {
  const meta = getChatbookCellMeta(cell.model.metadata);
  if (!canSwitchChatbookCellMode(meta, nextMode)) {
    return;
  }
  const result = switchChatbookCellMode({
    source: cell.model.sharedModel.getSource(),
    meta,
    nextMode
  });
  writeChatbookCellMeta(cell, result.meta);
  if (result.source !== cell.model.sharedModel.getSource()) {
    cell.model.sharedModel.setSource(result.source);
  }
}

export function toggleActiveChatbookCellMode(panel: NotebookPanel): void {
  const cell = panel.content.activeCell;
  if (!cell || cell.model.type !== 'code') {
    return;
  }
  const mode = getChatbookCellMode(getChatbookCellMeta(cell.model.metadata));
  setChatbookCellMode(cell, mode === 'python' ? 'prompt' : 'python');
}

/**
 * Mode every cell would land on if the notebook were switched as a whole: back
 * to prompts once every cell already shows its Python.
 */
export function nextChatbookNotebookMode(
  panel: NotebookPanel
): ChatbookCellMode {
  let total = 0;
  let python = 0;
  forEachCodeCell(panel, cell => {
    total += 1;
    if (
      getChatbookCellMode(getChatbookCellMeta(cell.model.metadata)) === 'python'
    ) {
      python += 1;
    }
  });
  return total > 0 && python === total ? 'prompt' : 'python';
}

export function setAllChatbookCellModes(
  panel: NotebookPanel,
  nextMode: ChatbookCellMode
): void {
  forEachCodeCell(panel, cell => {
    setChatbookCellMode(cell, nextMode);
  });
}

export function toggleAllChatbookCellModes(
  panel: NotebookPanel
): ChatbookCellMode {
  const nextMode = nextChatbookNotebookMode(panel);
  setAllChatbookCellModes(panel, nextMode);
  return nextMode;
}

export function attachChatbookNotebooks(
  tracker: INotebookTracker
): IDisposable {
  const attached = new WeakSet<NotebookPanel>();

  const attach = (panel: NotebookPanel) => {
    if (attached.has(panel)) {
      return;
    }
    attached.add(panel);
    let kernelConnection: Kernel.IKernelConnection | null = null;
    const syncCellBadges = () => {
      const isChatbook = isChatbookSession(panel.sessionContext);
      for (const widget of panel.content.widgets) {
        const editorView = (
          widget.editor as unknown as { editor?: EditorView } | null
        )?.editor;
        // The badge lives in the input area, next to JupyterLab's own cell
        // toolbar, so both share the same layout box and stay aligned.
        const badgeHost =
          (widget as unknown as { inputArea?: { node: HTMLElement } | null })
            .inputArea?.node ?? widget.node;
        const existing = widget.node.querySelector<HTMLButtonElement>(
          '.nbi-chatbook-cell-mode'
        );
        if (!isChatbook || widget.model.type !== 'code') {
          if (editorView) {
            setChatbookMentionsEnabled(editorView, false);
          }
          existing?.remove();
          widget.node.classList.remove(
            'nbi-chatbook-cell-prompt',
            'nbi-chatbook-cell-python'
          );
          continue;
        }
        const mode = getChatbookCellMode(
          getChatbookCellMeta(widget.model.metadata)
        );
        if (editorView) {
          setChatbookMentionsEnabled(editorView, mode === 'prompt');
        }
        const desiredMime =
          mode === 'python' ? 'text/x-python' : 'text/x-chatbook';
        const cellModel = widget.model as unknown as { mimeType: string };
        if (cellModel.mimeType !== desiredMime) {
          cellModel.mimeType = desiredMime;
        }
        widget.node.classList.toggle(
          'nbi-chatbook-cell-prompt',
          mode === 'prompt'
        );
        widget.node.classList.toggle(
          'nbi-chatbook-cell-python',
          mode === 'python'
        );
        const button = existing || document.createElement('button');
        button.className = 'nbi-chatbook-cell-mode';
        button.type = 'button';
        button.textContent = mode === 'python' ? 'Py' : 'NL';
        button.title =
          mode === 'python'
            ? 'Python cell — switch to its English representation'
            : 'Natural-language cell — switch to Python';
        button.setAttribute('aria-label', button.title);
        if (!existing) {
          button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            panel.content.activeCellIndex =
              panel.content.widgets.indexOf(widget);
            const currentMode = getChatbookCellMode(
              getChatbookCellMeta(widget.model.metadata)
            );
            setChatbookCellMode(
              widget,
              currentMode === 'python' ? 'prompt' : 'python'
            );
            syncCellBadges();
          });
          badgeHost.appendChild(button);
        } else if (button.parentElement !== badgeHost) {
          badgeHost.appendChild(button);
        }
      }
    };
    const onAnyMessage = (
      _sender: Kernel.IKernelConnection,
      args: Kernel.IAnyMessageArgs
    ) => {
      if (args.direction !== 'recv') {
        return;
      }
      const msg = args.msg;
      if ((msg.header.msg_type as string) !== CHATBOOK_MSG_TYPE) {
        return;
      }
      applyChatbookPayload(panel, msg.content as Record<string, unknown>);
    };
    const connectKernel = () => {
      if (kernelConnection) {
        kernelConnection.anyMessage.disconnect(onAnyMessage);
        kernelConnection = null;
      }
      const kernel = panel.sessionContext.session?.kernel;
      if (!kernel || !isChatbookKernelName(kernel.name)) {
        return;
      }
      kernelConnection = kernel;
      kernel.anyMessage.connect(onAnyMessage);
      syncCellBadges();
    };
    panel.sessionContext.kernelChanged.connect(connectKernel);
    panel.model?.contentChanged.connect(syncCellBadges);
    panel.content.activeCellChanged.connect(syncCellBadges);
    void panel.sessionContext.ready.then(connectKernel);
    void panel.context.ready.then(() => {
      migrateLegacyCodeView(panel);
      syncCellBadges();
    });
    syncCellBadges();
    panel.disposed.connect(() => {
      if (kernelConnection) {
        kernelConnection.anyMessage.disconnect(onAnyMessage);
        kernelConnection = null;
      }
      panel.model?.contentChanged.disconnect(syncCellBadges);
      panel.content.activeCellChanged.disconnect(syncCellBadges);
    });
  };

  tracker.widgetAdded.connect((_, panel) => {
    attach(panel);
  });
  tracker.forEach(panel => {
    attach(panel);
  });

  return {
    dispose: () => {
      /* listeners are tied to panel lifetime */
    },
    get isDisposed() {
      return false;
    }
  };
}

function snapshotNotebookContext(
  notebook: NotebookPanel | undefined,
  activeCell: CodeCell
): IChatbookNotebookContext | undefined {
  const parent = activeCell.parent as {
    widgets?: readonly (typeof activeCell)[];
  } | null;
  const cells = notebook?.content?.widgets ?? parent?.widgets;
  if (!cells) {
    return undefined;
  }
  const cursorIndex = cells.findIndex(
    widget => widget === activeCell || widget.model.id === activeCell.model.id
  );
  if (cursorIndex < 0) {
    return undefined;
  }
  const snapshots = cells.map((widget, index) => {
    const output = widget instanceof CodeCell ? cellOutputAsText(widget) : '';
    return snapshotChatbookContextCell({
      index,
      cellType: widget.model.type,
      source: widget.model.sharedModel.getSource(),
      cellMeta: getChatbookCellMeta(widget.model.metadata),
      output
    });
  });
  return splitNotebookContext(snapshots, cursorIndex);
}

function applyChatbookPayload(
  panel: NotebookPanel,
  content: Record<string, unknown>
): void {
  const cellId = String(content.cellId || '');
  const generatedCode = String(content.generatedCode || '');
  const patch: IChatbookCellMeta = {
    generatedCode,
    // The run that just finished defines the cell's Python, so both fields move
    // together.
    pythonSource: generatedCode,
    promptHash: content.promptHash ? String(content.promptHash) : undefined,
    contextHash: content.contextHash ? String(content.contextHash) : undefined,
    generatedAt: content.generatedAt ? String(content.generatedAt) : undefined,
    cacheHit: Boolean(content.cacheHit)
  };
  // An empty prompt generates nothing; the Python the cell already has stands.
  if (!generatedCode) {
    return;
  }

  const cells = panel.content.widgets;
  const cell = cellId
    ? cells.find(widget => widget.model.id === cellId)
    : panel.content.activeCell;
  if (cell) {
    if (
      getChatbookCellMode(getChatbookCellMeta(cell.model.metadata)) === 'python'
    ) {
      return;
    }
    writeChatbookCellMeta(cell, patch);
  }
}

interface IChatbookEditableCell {
  model: {
    type: string;
    metadata: unknown;
    setMetadata: (key: string, value: unknown) => void;
    sharedModel: {
      getSource: () => string;
      setSource: (value: string) => void;
    };
  };
}

function writeChatbookCellMeta(
  cell: IChatbookEditableCell,
  patch: IChatbookCellMeta
): void {
  const merged = mergeChatbookCellMeta(cell.model.metadata, patch);
  if (merged.nbi) {
    cell.model.setMetadata('nbi', merged.nbi);
  }
}

function forEachCodeCell(
  panel: NotebookPanel,
  visit: (cell: IChatbookEditableCell) => void
): void {
  for (const widget of panel.content.widgets) {
    if (widget.model.type === 'code') {
      visit(widget);
    }
  }
}

/**
 * Notebooks saved while the old notebook-wide code view was on hold generated
 * Python in cells still marked as prompts. Those cells are Python cells now.
 */
function migrateLegacyCodeView(panel: NotebookPanel): void {
  if (!panel.model || !hasLegacyChatbookCodeView(panel.model.metadata)) {
    return;
  }
  forEachCodeCell(panel, cell => {
    const meta = getChatbookCellMeta(cell.model.metadata);
    const source = cell.model.sharedModel.getSource();
    if (
      getChatbookCellMode(meta) === 'python' ||
      !source.trim() ||
      source.trim() !== (meta.generatedCode || '').trim()
    ) {
      return;
    }
    writeChatbookCellMeta(cell, {
      mode: 'python',
      origin: getChatbookCellOrigin(meta),
      pythonSource: source
    });
  });
  const metadata = withoutLegacyChatbookSourceView(panel.model.metadata);
  if (metadata.nbi) {
    panel.model.setMetadata('nbi', metadata.nbi);
  }
}

export async function exportChatbookNotebookAsPython(
  panel: NotebookPanel,
  profile: INotebookKernelProfile,
  contents: Contents.IManager
): Promise<string> {
  if (!panel.model) {
    throw new Error('Notebook has no model to export');
  }
  const notebook = structuredClone(
    panel.model.toJSON() as Record<string, unknown>
  );
  const content = buildPythonNotebookFromChatbook(notebook, {
    name: profile.kernelName,
    display_name: profile.displayName,
    language: profile.language
  });
  let attempt = 0;
  let path = pythonExportNotebookPath(panel.context.path, attempt);
  while (await contentsPathExists(contents, path)) {
    attempt += 1;
    path = pythonExportNotebookPath(panel.context.path, attempt);
  }
  await contents.save(path, {
    type: 'notebook',
    format: 'json',
    content
  });
  return path;
}

async function contentsPathExists(
  contents: Contents.IManager,
  path: string
): Promise<boolean> {
  try {
    await contents.get(path, { content: false });
    return true;
  } catch {
    return false;
  }
}

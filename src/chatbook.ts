// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { CodeCell } from '@jupyterlab/cells';
import { IEditorLanguageRegistry } from '@jupyterlab/codemirror';
import { EditorView } from '@codemirror/view';
import { ISessionContext, Notification } from '@jupyterlab/apputils';
import { INotebookTracker, NotebookPanel } from '@jupyterlab/notebook';
import { Contents, Kernel } from '@jupyterlab/services';
import { JSONObject } from '@lumino/coreutils';
import { IDisposable } from '@lumino/disposable';

import {
  INotebookKernelProfile,
  mimeTypeForNotebookLanguage,
  resolveChatbookBackendProfile,
  sharedKernelSpecManager
} from './notebook-kernels';
import {
  CHATBOOK_LANGUAGE,
  CHATBOOK_MSG_TYPE,
  buildCodeNotebookFromChatbook,
  buildExecuteChatbookMeta,
  canSwitchChatbookCellMode,
  getChatbookCellMode,
  getChatbookCellMeta,
  getChatbookCellOrigin,
  hasChatbookPrompt,
  isChatbookKernelName,
  mergeChatbookCellMeta,
  chatbookExportNotebookPath,
  resolveChatbookPrompt,
  sha256Hex,
  snapshotChatbookContextCell,
  splitNotebookContext,
  switchChatbookCellMode,
  chatbookExecutionModeSummary,
  chatbookNeedsConfirm,
  type ChatbookCellMode,
  type ChatbookDangerLevel,
  type ChatbookExecutionMode,
  type IChatbookCellMeta,
  type IChatbookNotebookContext
} from './chatbook-core';
import { NBIAPI } from './api';
import { setChatbookMentionsEnabled } from './chatbook-mentions';
import { cellOutputAsText } from './utils';

export {
  CHATBOOK_KERNEL_NAME,
  CHATBOOK_LANGUAGE,
  CHATBOOK_MSG_TYPE,
  getChatbookCellMeta,
  getChatbookCellMode,
  isChatbookKernelName,
  isChatbookPromptInlineCompletion
} from './chatbook-core';

let codeCellExecutePatched = false;
const executedPromptByCell = new WeakMap<object, string>();
const pendingConfirmByCell = new WeakMap<object, IChatbookPendingConfirm>();
let openChatbookSettings: (() => void) | undefined;
let chatbookBackendLanguage = 'python';
let chatbookBackendDisplayName = 'Python';
let chatbookLanguageRegistry: IEditorLanguageRegistry | undefined;

export async function refreshChatbookBackendProfile(): Promise<INotebookKernelProfile> {
  const kernels = sharedKernelSpecManager();
  await kernels.ready;
  const profile = resolveChatbookBackendProfile(
    kernels.specs?.kernelspecs,
    NBIAPI.config.chatbookBackendKernel
  );
  chatbookBackendLanguage = profile.language;
  chatbookBackendDisplayName = profile.displayName;
  return profile;
}

function chatbookCodeMimeType(): string {
  const language = chatbookBackendLanguage || 'python';
  if (chatbookLanguageRegistry) {
    const found = chatbookLanguageRegistry.findByName(language);
    const mime = found?.mime;
    if (typeof mime === 'string' && mime) {
      return mime;
    }
    if (Array.isArray(mime) && typeof mime[0] === 'string' && mime[0]) {
      return mime[0];
    }
  }
  return mimeTypeForNotebookLanguage(language);
}

export function getChatbookBackendLanguage(): string {
  return chatbookBackendLanguage || 'python';
}

interface IChatbookPendingConfirm {
  code: string;
  prompt: string;
  promptHash: string;
  reasons: string[];
  mode: ChatbookExecutionMode;
}

function nbiChatbookFromMetadata(
  metadata?: JSONObject
): Record<string, unknown> {
  const value = metadata?.nbi_chatbook;
  return value && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : {};
}

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
    const incoming = nbiChatbookFromMetadata(metadata);
    const forceCode = incoming.executeMode === 'code';
    if (mode === 'code' || forceCode) {
      // A forced code run must never fall back to file-persisted
      // `generatedCode`; if codeSource is absent, execute only visible source.
      const code =
        forceCode && typeof incoming.codeSource === 'string'
          ? incoming.codeSource
          : source;
      if (mode === 'code') {
        writeChatbookCellMeta(cell, {
          mode: 'code',
          origin: getChatbookCellOrigin(cellMeta),
          codeSource: code,
          generatedCode: code
        });
      }
      const execution = await original(cell, sessionContext, {
        ...(metadata || {}),
        cellId:
          (typeof metadata?.cellId === 'string' && metadata.cellId) ||
          cell.model.id,
        nbi_chatbook: {
          cellId: cell.model.id,
          executeMode: 'code',
          codeSource: code
        }
      });
      const status = (
        execution as unknown as { content?: { status?: string } } | undefined
      )?.content?.status;
      if (mode === 'code' && status !== 'error') {
        void summarizeCodeCell(cell, code);
      }
      if (status !== 'error') {
        const promptHash =
          getChatbookCellMeta(cell.model.metadata).promptHash || '';
        if (promptHash) {
          executedPromptByCell.set(cell.model, promptHash);
        }
      }
      hideChatbookConfirmBar(cell);
      return execution;
    }
    const prompt = resolveChatbookPrompt(source, cellMeta);
    writeChatbookCellMeta(cell, {
      prompt,
      origin: getChatbookCellOrigin(cellMeta)
    });
    const promptHash = await sha256Hex(prompt);
    const executionMode = NBIAPI.config.chatbookExecutionMode;
    const alreadyExecuted = executedPromptByCell.get(cell.model) === promptHash;
    const cachedCode = getChatbookCellMeta(cell.model.metadata).generatedCode;
    if (alreadyExecuted && cachedCode) {
      return CodeCell.execute(cell, sessionContext, {
        ...(metadata || {}),
        nbi_chatbook: {
          cellId: cell.model.id,
          executeMode: 'code',
          codeSource: cachedCode
        }
      });
    }
    const notebookContext = snapshotNotebookContext(notebook, cell);
    const notebookPath = notebook?.context.path || '';
    const contextHash = notebookContext
      ? await sha256Hex(JSON.stringify({ notebookPath, notebookContext }))
      : undefined;
    const hasMentionContext = /(?:^|\s)@[^\s@]+/u.test(prompt);
    const nbiChatbook = buildExecuteChatbookMeta({
      cellId: cell.model.id,
      prompt,
      promptHash,
      cellMeta: getChatbookCellMeta(cell.model.metadata),
      notebookPath,
      notebookContext,
      contextHash,
      executionPolicy: executionMode,
      llmDangerScan: NBIAPI.config.chatbookLlmDangerScan,
      allowCachedCode:
        alreadyExecuted &&
        !NBIAPI.config.chatbookHasContextProviders &&
        !NBIAPI.config.chatbookHasGuidelines &&
        !hasMentionContext
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
 * Fill in the English representation of a code cell. A cell that already has
 * one keeps it: the summary is generated only for an empty English side, or on
 * explicit request through `force`.
 */
export async function summarizeCodeCell(
  cell: IChatbookEditableCell,
  code?: string,
  options: { notifyOnError?: boolean; force?: boolean } = {}
): Promise<string | undefined> {
  const source = code ?? cell.model.sharedModel.getSource();
  if (!source.trim()) {
    return '';
  }
  const meta = getChatbookCellMeta(cell.model.metadata);
  if (!options.force && hasChatbookPrompt(meta)) {
    return meta.prompt;
  }
  const codeHash = await sha256Hex(source);
  writeChatbookCellMeta(cell, {
    mode: 'code',
    origin: getChatbookCellOrigin(meta),
    codeSource: source,
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
      getChatbookCellMode(getChatbookCellMeta(cell.model.metadata)) !== 'code'
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
  setChatbookCellMode(cell, mode === 'code' ? 'prompt' : 'code');
}

/**
 * Mode every cell would land on if the notebook were switched as a whole: back
 * to prompts once every cell already shows its code.
 */
export function nextChatbookNotebookMode(
  panel: NotebookPanel
): ChatbookCellMode {
  let total = 0;
  let codeCells = 0;
  forEachCodeCell(panel, cell => {
    total += 1;
    if (
      getChatbookCellMode(getChatbookCellMeta(cell.model.metadata)) === 'code'
    ) {
      codeCells += 1;
    }
  });
  return total > 0 && codeCells === total ? 'prompt' : 'code';
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

function debounceAnimationFrame(fn: () => void): () => void {
  let frame = 0;
  return () => {
    if (frame) {
      return;
    }
    frame = requestAnimationFrame(() => {
      frame = 0;
      fn();
    });
  };
}

export function attachChatbookNotebooks(
  tracker: INotebookTracker,
  options: {
    onOpenSettings?: () => void;
    languageRegistry?: IEditorLanguageRegistry;
  } = {}
): IDisposable {
  openChatbookSettings = options.onOpenSettings;
  chatbookLanguageRegistry = options.languageRegistry;
  const attached = new WeakSet<NotebookPanel>();
  const resyncBadges: Array<() => void> = [];
  void refreshChatbookBackendProfile();
  NBIAPI.configChanged.connect(() => {
    void refreshChatbookBackendProfile().then(() => {
      for (const sync of resyncBadges) {
        sync();
      }
    });
  });

  const attach = (panel: NotebookPanel) => {
    if (attached.has(panel)) {
      return;
    }
    attached.add(panel);
    let kernelConnection: Kernel.IKernelConnection | null = null;
    let contentChangedConnected = false;
    const syncCellBadges = () => {
      if (panel.isDisposed) {
        return;
      }
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
            setChatbookMentionsEnabled(editorView, false, panel.context.path);
          }
          existing?.remove();
          widget.node.classList.remove(
            'nbi-chatbook-cell-prompt',
            'nbi-chatbook-cell-code'
          );
          continue;
        }
        const mode = getChatbookCellMode(
          getChatbookCellMeta(widget.model.metadata)
        );
        if (editorView) {
          setChatbookMentionsEnabled(
            editorView,
            mode === 'prompt',
            panel.context.path
          );
        }
        const desiredMime =
          mode === 'code' ? chatbookCodeMimeType() : 'text/x-chatbook';
        const cellModel = widget.model as unknown as { mimeType: string };
        if (cellModel.mimeType !== desiredMime) {
          cellModel.mimeType = desiredMime;
        }
        widget.node.classList.toggle(
          'nbi-chatbook-cell-prompt',
          mode === 'prompt'
        );
        widget.node.classList.toggle('nbi-chatbook-cell-code', mode === 'code');
        const button = existing || document.createElement('button');
        button.className = 'nbi-chatbook-cell-mode';
        button.type = 'button';
        const languageLabel = chatbookBackendDisplayName || 'code';
        button.textContent = mode === 'code' ? 'Cd' : 'NL';
        button.title =
          mode === 'code'
            ? `Code cell (${languageLabel}) — switch to natural language`
            : `Natural-language cell — switch to ${languageLabel} code`;
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
              currentMode === 'code' ? 'prompt' : 'code'
            );
            syncCellBadges();
          });
          badgeHost.appendChild(button);
        } else if (button.parentElement !== badgeHost) {
          badgeHost.appendChild(button);
        }
      }
      syncChatbookConfirmBars(panel);
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
    const debouncedSyncCellBadges = debounceAnimationFrame(syncCellBadges);
    const setContentChangedListening = (listen: boolean) => {
      if (listen === contentChangedConnected) {
        return;
      }
      if (listen) {
        panel.model?.contentChanged.connect(debouncedSyncCellBadges);
        panel.content.activeCellChanged.connect(debouncedSyncCellBadges);
      } else {
        panel.model?.contentChanged.disconnect(debouncedSyncCellBadges);
        panel.content.activeCellChanged.disconnect(debouncedSyncCellBadges);
      }
      contentChangedConnected = listen;
    };
    const connectKernel = () => {
      if (panel.isDisposed) {
        return;
      }
      if (kernelConnection) {
        kernelConnection.anyMessage.disconnect(onAnyMessage);
        kernelConnection = null;
      }
      const isChatbook = isChatbookSession(panel.sessionContext);
      setContentChangedListening(isChatbook);
      const kernel = panel.sessionContext.session?.kernel;
      if (!kernel || !isChatbookKernelName(kernel.name)) {
        syncCellBadges();
        return;
      }
      kernelConnection = kernel;
      kernel.anyMessage.connect(onAnyMessage);
      syncCellBadges();
    };
    panel.sessionContext.kernelChanged.connect(connectKernel);
    resyncBadges.push(syncCellBadges);
    void panel.sessionContext.ready.then(connectKernel);
    void panel.context.ready.then(() => {
      if (!panel.isDisposed) {
        syncCellBadges();
      }
    });
    connectKernel();
    panel.disposed.connect(() => {
      if (kernelConnection) {
        kernelConnection.anyMessage.disconnect(onAnyMessage);
        kernelConnection = null;
      }
      panel.sessionContext.kernelChanged.disconnect(connectKernel);
      setContentChangedListening(false);
      const index = resyncBadges.indexOf(syncCellBadges);
      if (index >= 0) {
        resyncBadges.splice(index, 1);
      }
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
    // The run that just finished defines the cell's code, so both fields move
    // together.
    codeSource: generatedCode,
    promptHash: content.promptHash ? String(content.promptHash) : undefined,
    contextHash: content.contextHash ? String(content.contextHash) : undefined,
    generatedAt: content.generatedAt ? String(content.generatedAt) : undefined,
    cacheHit: Boolean(content.cacheHit)
  };
  // An empty prompt generates nothing; the code the cell already has stands.
  if (!generatedCode) {
    return;
  }

  const cells = panel.content.widgets;
  const cell = cellId
    ? cells.find(widget => widget.model.id === cellId)
    : panel.content.activeCell;
  if (cell) {
    if (
      getChatbookCellMode(getChatbookCellMeta(cell.model.metadata)) === 'code'
    ) {
      return;
    }
    writeChatbookCellMeta(cell, patch);
    maybeShowChatbookConfirm(panel, cell, {
      code: generatedCode,
      promptHash: String(content.promptHash || ''),
      reasons: Array.isArray(content.dangerReasons)
        ? content.dangerReasons.map(item => String(item)).filter(Boolean)
        : [],
      level:
        content.dangerLevel === 'risky'
          ? 'risky'
          : ('clean' as ChatbookDangerLevel)
    });
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

function maybeShowChatbookConfirm(
  panel: NotebookPanel,
  cell: {
    model: {
      id: string;
      metadata: unknown;
      sharedModel: { getSource: () => string };
    };
    node: HTMLElement;
  },
  options: {
    code: string;
    promptHash: string;
    reasons: string[];
    level: ChatbookDangerLevel;
  }
): void {
  const mode = NBIAPI.config.chatbookExecutionMode;
  const prompt = cell.model.sharedModel.getSource();
  if (
    !chatbookNeedsConfirm(mode, options.level, {
      alreadyExecutedThisSession:
        executedPromptByCell.get(cell.model) === options.promptHash
    })
  ) {
    executedPromptByCell.set(cell.model, options.promptHash);
    hideChatbookConfirmBar(cell);
    return;
  }
  pendingConfirmByCell.set(cell.model, {
    code: options.code,
    prompt,
    promptHash: options.promptHash,
    reasons: options.reasons,
    mode
  });
  renderChatbookConfirmBar(panel, cell);
}

function syncChatbookConfirmBars(panel: NotebookPanel): void {
  for (const widget of panel.content.widgets) {
    if (widget.model.type !== 'code') {
      continue;
    }
    const pending = pendingConfirmByCell.get(widget.model);
    if (
      !pending ||
      getChatbookCellMode(getChatbookCellMeta(widget.model.metadata)) === 'code'
    ) {
      hideChatbookConfirmBar(widget);
      continue;
    }
    if (widget.model.sharedModel.getSource() !== pending.prompt) {
      pendingConfirmByCell.delete(widget.model);
      hideChatbookConfirmBar(widget);
      continue;
    }
    renderChatbookConfirmBar(panel, widget);
  }
}

function hideChatbookConfirmBar(cell: {
  node: HTMLElement;
  model?: object;
}): void {
  cell.node.querySelector('.nbi-chatbook-confirm')?.remove();
  if (cell.model) {
    pendingConfirmByCell.delete(cell.model);
  }
}

function renderChatbookConfirmBar(
  panel: NotebookPanel,
  cell: {
    node: HTMLElement;
    model: { id: string; sharedModel: { getSource: () => string } };
  }
): void {
  const pending = pendingConfirmByCell.get(cell.model);
  if (!pending) {
    hideChatbookConfirmBar(cell);
    return;
  }
  const existing = cell.node.querySelector(
    '.nbi-chatbook-confirm'
  ) as HTMLElement | null;
  const signature = chatbookConfirmSignature(pending);
  if (existing) {
    // Both `contentChanged` and `activeCellChanged` land here, and the second
    // one fires while the pointer is still down on Run. Rebuilding the bar at
    // that point destroys the button before it sees the click, which costs the
    // user a second click, so an unchanged bar is left in place.
    if (existing.dataset.nbiConfirmSignature === signature) {
      return;
    }
    existing.remove();
  }
  const bar = document.createElement('div');
  bar.className = 'nbi-chatbook-confirm';
  bar.dataset.nbiConfirmSignature = signature;
  const input = cell.node.querySelector('.jp-Cell-inputWrapper');
  if (input?.parentElement) {
    input.parentElement.insertBefore(bar, input.nextSibling);
  } else {
    cell.node.appendChild(bar);
  }
  const reasons = pending.reasons.length
    ? `<ul class="nbi-chatbook-confirm-reasons">${pending.reasons
        .map(reason => `<li>${escapeChatbookHtml(reason)}</li>`)
        .join('')}</ul>`
    : '';
  bar.innerHTML = `
    <div class="nbi-chatbook-confirm-header">${escapeChatbookHtml(
      'Review generated code before running it in this kernel.'
    )}</div>
    <pre class="nbi-chatbook-confirm-code">${escapeChatbookHtml(pending.code)}</pre>
    ${reasons}
    <div class="nbi-chatbook-confirm-footer">
      <div class="nbi-chatbook-confirm-actions"></div>
      <div class="nbi-chatbook-confirm-hint">
        <span>${escapeChatbookHtml(chatbookExecutionModeSummary(pending.mode))}</span>
      </div>
    </div>
  `;
  const actions = bar.querySelector(
    '.nbi-chatbook-confirm-actions'
  ) as HTMLElement;
  const run = document.createElement('button');
  run.type = 'button';
  run.className = 'jp-mod-styled jp-mod-accept nbi-chatbook-confirm-run';
  run.textContent = 'Run';
  run.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    const current = pendingConfirmByCell.get(cell.model);
    hideChatbookConfirmBar(cell);
    if (!current) {
      return;
    }
    void CodeCell.execute(cell as unknown as CodeCell, panel.sessionContext, {
      nbi_chatbook: {
        cellId: cell.model.id,
        executeMode: 'code',
        codeSource: current.code
      }
    } as JSONObject);
  });
  actions.appendChild(run);
  const discard = document.createElement('button');
  discard.type = 'button';
  discard.className = 'jp-mod-styled nbi-chatbook-confirm-discard';
  discard.textContent = "Don't run";
  discard.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    hideChatbookConfirmBar(cell);
  });
  actions.appendChild(discard);
  const hint = bar.querySelector('.nbi-chatbook-confirm-hint') as HTMLElement;
  const settingsLink = document.createElement('button');
  settingsLink.type = 'button';
  settingsLink.className = 'nbi-chatbook-confirm-settings-link';
  settingsLink.textContent = 'Change in NBI Settings';
  settingsLink.title =
    'Open the Chatbook tab of Notebook Intelligence Settings';
  settingsLink.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    openChatbookSettings?.();
  });
  hint.appendChild(settingsLink);
}

function chatbookConfirmSignature(pending: IChatbookPendingConfirm): string {
  return JSON.stringify([
    pending.mode,
    pending.promptHash,
    pending.code,
    pending.reasons
  ]);
}

function escapeChatbookHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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

export async function exportChatbookNotebookAsCode(
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
  const content = await buildCodeNotebookFromChatbook(notebook, {
    name: profile.kernelName,
    display_name: profile.displayName,
    language: profile.language
  });
  let attempt = 0;
  let path = chatbookExportNotebookPath(
    panel.context.path,
    profile.language,
    attempt
  );
  while (await contentsPathExists(contents, path)) {
    attempt += 1;
    path = chatbookExportNotebookPath(
      panel.context.path,
      profile.language,
      attempt
    );
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

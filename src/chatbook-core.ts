// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

export const CHATBOOK_KERNEL_NAME = 'chatbook';
export const CHATBOOK_MSG_TYPE = 'nbi_chatbook_code';
export const CHATBOOK_LANGUAGE = 'chatbook';

export type ChatbookSourceView = 'prompt' | 'code';
export type ChatbookCellMode = 'prompt' | 'python';
export type ChatbookConvertTargetId = 'python';

export const CHATBOOK_CONTEXT_MAX_FIELD_CHARS = 8000;
export const CHATBOOK_CONTEXT_MAX_OUTPUT_CHARS = 4000;

export interface IChatbookCellMeta {
  mode?: ChatbookCellMode;
  prompt?: string;
  promptHash?: string;
  generatedCode?: string;
  pythonSource?: string;
  codeHash?: string;
  summarizedCodeHash?: string;
  summaryError?: string;
  contextHash?: string;
  nuiSessionId?: string;
  nuiRunId?: string;
  generatedAt?: string;
  cacheHit?: boolean;
}

export interface IChatbookContextCell {
  index: number;
  cellType: string;
  mode?: ChatbookCellMode;
  prompt?: string;
  generatedCode?: string;
  source?: string;
  output?: string;
}

export interface IChatbookNotebookContext {
  prefix: IChatbookContextCell[];
  current: IChatbookContextCell;
  suffix: IChatbookContextCell[];
}

export interface INotebookChatbookMeta {
  nuiSessionId?: string;
  sourceView?: ChatbookSourceView;
}

export interface IChatbookConvertTarget {
  id: ChatbookConvertTargetId;
  label: string;
  language: string;
  defaultKernelName: string;
}

export const CHATBOOK_CONVERT_TARGETS: Record<
  ChatbookConvertTargetId,
  IChatbookConvertTarget
> = {
  python: {
    id: 'python',
    label: 'Python notebook',
    language: 'python',
    defaultKernelName: 'python3'
  }
};

export interface IChatbookExecuteMeta {
  cellId?: string;
  executeMode?: ChatbookCellMode;
  promptHash?: string;
  contextHash?: string;
  cachedCode?: string;
  generateUrl?: string;
  workingDir?: string;
  notebookContext?: IChatbookNotebookContext;
}

export function isChatbookKernelName(name: string | undefined | null): boolean {
  return (name ?? '').trim() === CHATBOOK_KERNEL_NAME;
}

export function isChatbookPromptInlineCompletion(
  kernelName: string | undefined | null,
  sourceView: ChatbookSourceView,
  cellMode: ChatbookCellMode = 'prompt'
): boolean {
  return (
    isChatbookKernelName(kernelName) &&
    sourceView !== 'code' &&
    cellMode === 'prompt'
  );
}

export function getChatbookCellMode(meta: IChatbookCellMeta): ChatbookCellMode {
  return meta.mode === 'python' ? 'python' : 'prompt';
}

export function getChatbookCellMeta(cellMetadata: unknown): IChatbookCellMeta {
  if (!cellMetadata || typeof cellMetadata !== 'object') {
    return {};
  }
  const nbi = (cellMetadata as { nbi?: { chatbook?: IChatbookCellMeta } }).nbi;
  const chatbook = nbi?.chatbook;
  return chatbook && typeof chatbook === 'object' ? { ...chatbook } : {};
}

export function mergeChatbookCellMeta(
  cellMetadata: unknown,
  patch: IChatbookCellMeta
): Record<string, unknown> {
  const current =
    cellMetadata && typeof cellMetadata === 'object'
      ? { ...(cellMetadata as Record<string, unknown>) }
      : {};
  const nbi =
    current.nbi && typeof current.nbi === 'object'
      ? { ...(current.nbi as Record<string, unknown>) }
      : {};
  const chatbook = {
    ...getChatbookCellMeta(current),
    ...patch
  };
  nbi.chatbook = chatbook;
  current.nbi = nbi;
  return current;
}

export function getNotebookChatbookMeta(
  notebookMetadata: unknown
): INotebookChatbookMeta {
  if (!notebookMetadata || typeof notebookMetadata !== 'object') {
    return {};
  }
  const nbi = (
    notebookMetadata as { nbi?: { chatbook?: INotebookChatbookMeta } }
  ).nbi;
  const chatbook = nbi?.chatbook;
  if (!chatbook || typeof chatbook !== 'object') {
    return {};
  }
  const meta: INotebookChatbookMeta = {};
  if (typeof chatbook.nuiSessionId === 'string') {
    meta.nuiSessionId = chatbook.nuiSessionId;
  }
  if (chatbook.sourceView === 'code' || chatbook.sourceView === 'prompt') {
    meta.sourceView = chatbook.sourceView;
  }
  return meta;
}

export function getNotebookNuiSessionId(notebookMetadata: unknown): string {
  return (getNotebookChatbookMeta(notebookMetadata).nuiSessionId ?? '').trim();
}

export function getNotebookSourceView(
  notebookMetadata: unknown
): ChatbookSourceView {
  return getNotebookChatbookMeta(notebookMetadata).sourceView === 'code'
    ? 'code'
    : 'prompt';
}

export function mergeNotebookChatbookMeta(
  notebookMetadata: unknown,
  patch: INotebookChatbookMeta
): Record<string, unknown> {
  const current =
    notebookMetadata && typeof notebookMetadata === 'object'
      ? { ...(notebookMetadata as Record<string, unknown>) }
      : {};
  const nbi =
    current.nbi && typeof current.nbi === 'object'
      ? { ...(current.nbi as Record<string, unknown>) }
      : {};
  const chatbook = {
    ...getNotebookChatbookMeta(current),
    ...patch
  };
  nbi.chatbook = chatbook;
  current.nbi = nbi;
  return current;
}

export function mergeNotebookNuiSessionId(
  notebookMetadata: unknown,
  nuiSessionId: string
): Record<string, unknown> {
  return mergeNotebookChatbookMeta(notebookMetadata, { nuiSessionId });
}

export function isChatbookConvertTargetId(
  value: unknown
): value is ChatbookConvertTargetId {
  return typeof value === 'string' && value in CHATBOOK_CONVERT_TARGETS;
}

export function resolveChatbookPrompt(
  source: string,
  meta: IChatbookCellMeta,
  sourceView: ChatbookSourceView
): string {
  if (sourceView === 'code' && typeof meta.prompt === 'string') {
    return meta.prompt;
  }
  return source;
}

export function resolveChatbookPython(
  source: string,
  meta: IChatbookCellMeta,
  sourceView: ChatbookSourceView
): string {
  const mode = getChatbookCellMode(meta);
  if (mode === 'python') {
    return source;
  }
  return sourceView === 'code' ? source : meta.generatedCode || '';
}

export function promptAsHashComment(prompt: string): string {
  const text = prompt.replace(/\s+$/u, '');
  if (!text) {
    return '# <empty Chatbook prompt>';
  }
  return text
    .split('\n')
    .map(line => (line.length ? `# ${line}` : '#'))
    .join('\n');
}

function snapshotChatbookCell(options: {
  source: string;
  meta: IChatbookCellMeta;
  currentView: ChatbookSourceView;
}): {
  prompt: string;
  generatedCode: string;
  pythonSource: string;
  mode: ChatbookCellMode;
} {
  const mode = getChatbookCellMode(options.meta);
  if (mode === 'python') {
    const pythonSource = options.source;
    const prompt = options.meta.prompt || '';
    return {
      prompt,
      generatedCode: pythonSource,
      pythonSource,
      mode
    };
  }
  if (options.currentView === 'code') {
    return {
      prompt: options.meta.prompt ?? '',
      generatedCode: options.source,
      pythonSource: options.source,
      mode
    };
  }
  return {
    prompt: options.source,
    generatedCode: options.meta.generatedCode ?? '',
    pythonSource: options.meta.generatedCode ?? '',
    mode
  };
}

export function applySourceViewToCell(options: {
  source: string;
  meta: IChatbookCellMeta;
  currentView: ChatbookSourceView;
  nextView: ChatbookSourceView;
}): { source: string; meta: IChatbookCellMeta } {
  const snapshot = snapshotChatbookCell(options);
  if (snapshot.mode === 'python') {
    return {
      source: options.source,
      meta: {
        ...options.meta,
        mode: 'python',
        pythonSource: options.source,
        generatedCode: options.source
      }
    };
  }
  const meta: IChatbookCellMeta = {
    ...options.meta,
    mode: snapshot.mode,
    prompt: snapshot.prompt
  };
  if (snapshot.generatedCode) {
    meta.generatedCode = snapshot.generatedCode;
  }
  if (options.nextView === 'code') {
    const python = snapshot.pythonSource || snapshot.generatedCode;
    if (!python) {
      return { source: options.source, meta };
    }
    return { source: python, meta };
  }
  return { source: snapshot.prompt, meta };
}

export function switchChatbookCellMode(options: {
  source: string;
  meta: IChatbookCellMeta;
  sourceView: ChatbookSourceView;
  nextMode: ChatbookCellMode;
}): { source: string; meta: IChatbookCellMeta } {
  const snapshot = snapshotChatbookCell({
    source: options.source,
    meta: options.meta,
    currentView: options.sourceView
  });
  const pythonSource = snapshot.pythonSource || snapshot.generatedCode;
  const meta: IChatbookCellMeta = {
    ...options.meta,
    mode: options.nextMode,
    prompt: snapshot.prompt
  };
  if (pythonSource) {
    meta.generatedCode = pythonSource;
    meta.pythonSource = pythonSource;
  }
  const source =
    options.nextMode === 'python'
      ? pythonSource || (snapshot.mode === 'python' ? options.source : '')
      : snapshot.prompt;
  return { source, meta };
}

export function convertChatbookCellToPython(options: {
  source: string;
  meta: IChatbookCellMeta;
  currentView: ChatbookSourceView;
}): { source: string; meta: IChatbookCellMeta } {
  const snapshot = snapshotChatbookCell(options);
  if (snapshot.mode === 'python') {
    const python = snapshot.pythonSource || snapshot.generatedCode;
    return {
      source: python || options.source,
      meta: {
        ...options.meta,
        mode: 'python',
        prompt: snapshot.prompt,
        pythonSource: python || options.source
      }
    };
  }
  const meta: IChatbookCellMeta = {
    ...options.meta,
    prompt: snapshot.prompt || options.source
  };
  if (snapshot.generatedCode.trim()) {
    meta.generatedCode = snapshot.generatedCode;
    return { source: snapshot.generatedCode, meta };
  }
  return { source: promptAsHashComment(meta.prompt || ''), meta };
}

export interface IChatbookKernelSpec {
  name: string;
  display_name: string;
  language: string;
}

export function cellSourceToString(source: unknown): string {
  if (typeof source === 'string') {
    return source;
  }
  if (Array.isArray(source)) {
    return source.map(part => (typeof part === 'string' ? part : '')).join('');
  }
  return '';
}

export function pythonExportNotebookPath(
  sourcePath: string,
  attempt = 0
): string {
  const normalized = sourcePath.replace(/\\/g, '/');
  const slash = normalized.lastIndexOf('/');
  const dir = slash >= 0 ? normalized.slice(0, slash) : '';
  const file = slash >= 0 ? normalized.slice(slash + 1) : normalized;
  const stem = file.replace(/\.ipynb$/i, '') || 'notebook';
  const suffix = attempt > 0 ? `-python-${attempt}` : '-python';
  const name = `${stem}${suffix}.ipynb`;
  return dir ? `${dir}/${name}` : name;
}

export function buildPythonNotebookFromChatbook(
  notebook: Record<string, unknown>,
  kernelspec: IChatbookKernelSpec
): Record<string, unknown> {
  const currentView = getNotebookSourceView(notebook.metadata);
  const cells = Array.isArray(notebook.cells)
    ? notebook.cells.map(cell => convertNotebookCellToPython(cell, currentView))
    : [];
  const metadata = mergeNotebookChatbookMeta(notebook.metadata || {}, {
    sourceView: 'code'
  });
  metadata.kernelspec = kernelspec;
  metadata.language_info = { name: kernelspec.language };
  return {
    ...notebook,
    cells,
    metadata
  };
}

function convertNotebookCellToPython(
  cell: unknown,
  currentView: ChatbookSourceView
): unknown {
  if (!cell || typeof cell !== 'object') {
    return cell;
  }
  const next = { ...(cell as Record<string, unknown>) };
  if (next.cell_type !== 'code') {
    return next;
  }
  const converted = convertChatbookCellToPython({
    source: cellSourceToString(next.source),
    meta: getChatbookCellMeta(next.metadata),
    currentView
  });
  next.source = converted.source;
  next.metadata = mergeChatbookCellMeta(next.metadata, converted.meta);
  return next;
}

export function truncateChatbookContextField(
  text: string,
  maxChars: number
): string {
  if (!text || text.length <= maxChars) {
    return text || '';
  }
  return `${text.slice(0, maxChars)}\n...[truncated]`;
}

export function snapshotChatbookContextCell(options: {
  index: number;
  cellType: string;
  source: string;
  cellMeta: IChatbookCellMeta;
  sourceView: ChatbookSourceView;
  output?: string;
}): IChatbookContextCell {
  const cell: IChatbookContextCell = {
    index: options.index,
    cellType: options.cellType
  };
  if (options.cellType !== 'code') {
    if (options.source) {
      cell.source = truncateChatbookContextField(
        options.source,
        CHATBOOK_CONTEXT_MAX_FIELD_CHARS
      );
    }
    return cell;
  }
  const mode = getChatbookCellMode(options.cellMeta);
  cell.mode = mode;
  if (mode === 'python') {
    const python = resolveChatbookPython(
      options.source,
      options.cellMeta,
      options.sourceView
    );
    const prompt =
      options.sourceView === 'prompt'
        ? options.source
        : options.cellMeta.prompt || '';
    if (prompt) {
      cell.prompt = truncateChatbookContextField(
        prompt,
        CHATBOOK_CONTEXT_MAX_FIELD_CHARS
      );
    }
    if (python) {
      cell.generatedCode = truncateChatbookContextField(
        python,
        CHATBOOK_CONTEXT_MAX_FIELD_CHARS
      );
    }
    if (options.output) {
      cell.output = truncateChatbookContextField(
        options.output,
        CHATBOOK_CONTEXT_MAX_OUTPUT_CHARS
      );
    }
    return cell;
  }
  const prompt = resolveChatbookPrompt(
    options.source,
    options.cellMeta,
    options.sourceView
  );
  const generated = options.cellMeta.generatedCode || '';
  if (prompt) {
    cell.prompt = truncateChatbookContextField(
      prompt,
      CHATBOOK_CONTEXT_MAX_FIELD_CHARS
    );
  }
  if (generated) {
    cell.generatedCode = truncateChatbookContextField(
      generated,
      CHATBOOK_CONTEXT_MAX_FIELD_CHARS
    );
  }
  if (
    options.source &&
    options.source !== prompt &&
    options.source !== generated
  ) {
    cell.source = truncateChatbookContextField(
      options.source,
      CHATBOOK_CONTEXT_MAX_FIELD_CHARS
    );
  }
  if (options.output) {
    cell.output = truncateChatbookContextField(
      options.output,
      CHATBOOK_CONTEXT_MAX_OUTPUT_CHARS
    );
  }
  return cell;
}

export function splitNotebookContext(
  cells: IChatbookContextCell[],
  cursorIndex: number
): IChatbookNotebookContext {
  const current = cells.find(cell => cell.index === cursorIndex) ||
    cells[cursorIndex] || { index: cursorIndex, cellType: 'code' };
  return {
    prefix: cells.filter(cell => cell.index < cursorIndex),
    current,
    suffix: cells.filter(cell => cell.index > cursorIndex)
  };
}

export function buildExecuteChatbookMeta(options: {
  cellId: string;
  prompt: string;
  promptHash: string;
  cellMeta: IChatbookCellMeta;
  generateUrl?: string;
  workingDir?: string;
  notebookContext?: IChatbookNotebookContext;
  contextHash?: string;
  executeMode?: ChatbookCellMode;
}): IChatbookExecuteMeta {
  const meta: IChatbookExecuteMeta = {
    cellId: options.cellId,
    promptHash: options.promptHash,
    executeMode: options.executeMode || 'prompt'
  };
  if (meta.executeMode === 'python') {
    return meta;
  }
  const contextMatches =
    !options.contextHash ||
    options.cellMeta.contextHash === options.contextHash;
  if (
    options.cellMeta.generatedCode &&
    options.cellMeta.promptHash === options.promptHash &&
    contextMatches
  ) {
    meta.cachedCode = options.cellMeta.generatedCode;
  }
  if (options.generateUrl) {
    meta.generateUrl = options.generateUrl;
  }
  if (options.workingDir) {
    meta.workingDir = options.workingDir;
  }
  if (options.notebookContext) {
    meta.notebookContext = options.notebookContext;
  }
  if (options.contextHash) {
    meta.contextHash = options.contextHash;
  }
  return meta;
}

export async function sha256Hex(text: string): Promise<string> {
  const encoded = new TextEncoder().encode(text);
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw new Error('SHA-256 is not available (crypto.subtle missing)');
  }
  const digest = await subtle.digest('SHA-256', encoded);
  return Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('');
}

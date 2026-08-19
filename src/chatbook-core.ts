// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

export const CHATBOOK_KERNEL_NAME = 'chatbook';
export const CHATBOOK_MSG_TYPE = 'nbi_chatbook_code';
export const CHATBOOK_LANGUAGE = 'chatbook';

export type ChatbookSourceView = 'prompt' | 'code';
export type ChatbookConvertTargetId = 'python';

export interface IChatbookCellMeta {
  prompt?: string;
  promptHash?: string;
  generatedCode?: string;
  nuiSessionId?: string;
  nuiRunId?: string;
  generatedAt?: string;
  cacheHit?: boolean;
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
  promptHash?: string;
  cachedCode?: string;
  nuiSessionId?: string;
  workingDir?: string;
}

export function isChatbookKernelName(name: string | undefined | null): boolean {
  return (name ?? '').trim() === CHATBOOK_KERNEL_NAME;
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
}): { prompt: string; generatedCode: string } {
  if (options.currentView === 'code') {
    return {
      prompt: options.meta.prompt ?? '',
      generatedCode: options.source
    };
  }
  return {
    prompt: options.source,
    generatedCode: options.meta.generatedCode ?? ''
  };
}

export function applySourceViewToCell(options: {
  source: string;
  meta: IChatbookCellMeta;
  currentView: ChatbookSourceView;
  nextView: ChatbookSourceView;
}): { source: string; meta: IChatbookCellMeta } {
  const snapshot = snapshotChatbookCell(options);
  const meta: IChatbookCellMeta = {
    ...options.meta,
    prompt: snapshot.prompt
  };
  if (snapshot.generatedCode) {
    meta.generatedCode = snapshot.generatedCode;
  }
  if (options.nextView === 'code') {
    if (!snapshot.generatedCode) {
      return { source: options.source, meta };
    }
    return { source: snapshot.generatedCode, meta };
  }
  return { source: snapshot.prompt, meta };
}

export function convertChatbookCellToPython(options: {
  source: string;
  meta: IChatbookCellMeta;
  currentView: ChatbookSourceView;
}): { source: string; meta: IChatbookCellMeta } {
  const snapshot = snapshotChatbookCell(options);
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

export function buildExecuteChatbookMeta(options: {
  cellId: string;
  prompt: string;
  promptHash: string;
  cellMeta: IChatbookCellMeta;
  nuiSessionId?: string;
  workingDir?: string;
}): IChatbookExecuteMeta {
  const meta: IChatbookExecuteMeta = {
    cellId: options.cellId,
    promptHash: options.promptHash
  };
  if (
    options.cellMeta.generatedCode &&
    options.cellMeta.promptHash === options.promptHash
  ) {
    meta.cachedCode = options.cellMeta.generatedCode;
  }
  const sessionId = options.nuiSessionId || options.cellMeta.nuiSessionId;
  if (sessionId) {
    meta.nuiSessionId = sessionId;
  }
  if (options.workingDir) {
    meta.workingDir = options.workingDir;
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

// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

export const CHATBOOK_KERNEL_NAME = 'chatbook';
export const CHATBOOK_MSG_TYPE = 'nbi_chatbook_code';
export const CHATBOOK_LANGUAGE = 'chatbook';

export interface IChatbookCellMeta {
  promptHash?: string;
  generatedCode?: string;
  nuiSessionId?: string;
  nuiRunId?: string;
  generatedAt?: string;
  cacheHit?: boolean;
}

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

export function getNotebookNuiSessionId(notebookMetadata: unknown): string {
  if (!notebookMetadata || typeof notebookMetadata !== 'object') {
    return '';
  }
  const nbi = (
    notebookMetadata as { nbi?: { chatbook?: { nuiSessionId?: string } } }
  ).nbi;
  return (nbi?.chatbook?.nuiSessionId ?? '').trim();
}

export function mergeNotebookNuiSessionId(
  notebookMetadata: unknown,
  nuiSessionId: string
): Record<string, unknown> {
  const current =
    notebookMetadata && typeof notebookMetadata === 'object'
      ? { ...(notebookMetadata as Record<string, unknown>) }
      : {};
  const nbi =
    current.nbi && typeof current.nbi === 'object'
      ? { ...(current.nbi as Record<string, unknown>) }
      : {};
  const chatbook =
    nbi.chatbook && typeof nbi.chatbook === 'object'
      ? { ...(nbi.chatbook as Record<string, unknown>) }
      : {};
  chatbook.nuiSessionId = nuiSessionId;
  nbi.chatbook = chatbook;
  current.nbi = nbi;
  return current;
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

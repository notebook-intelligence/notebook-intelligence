// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

export const CHATBOOK_KERNEL_NAME = 'chatbook';
export const CHATBOOK_MSG_TYPE = 'nbi_chatbook_code';
export const CHATBOOK_LANGUAGE = 'chatbook';

export type ChatbookCellMode = 'prompt' | 'python';
export type ChatbookConvertTargetId = 'python';
export type ChatbookExecutionMode =
  | 'generate-only'
  | 'always-confirm'
  | 'confirm-if-risky'
  | 'auto-run';
export type ChatbookDangerLevel = 'clean' | 'risky';

export const CHATBOOK_EXECUTION_MODES: readonly ChatbookExecutionMode[] = [
  'generate-only',
  'always-confirm',
  'confirm-if-risky',
  'auto-run'
];
export const DEFAULT_CHATBOOK_EXECUTION_MODE: ChatbookExecutionMode =
  'always-confirm';
export const DEFAULT_CHATBOOK_MAX_EXECUTION_MODE: ChatbookExecutionMode =
  'auto-run';

const CHATBOOK_EXECUTION_MODE_RANK: Record<ChatbookExecutionMode, number> = {
  'generate-only': 0,
  'always-confirm': 1,
  'confirm-if-risky': 2,
  'auto-run': 3
};

export function parseChatbookExecutionMode(
  value: unknown,
  fallback: ChatbookExecutionMode = DEFAULT_CHATBOOK_EXECUTION_MODE
): ChatbookExecutionMode {
  return CHATBOOK_EXECUTION_MODES.includes(value as ChatbookExecutionMode)
    ? (value as ChatbookExecutionMode)
    : fallback;
}

export function clampChatbookExecutionMode(
  mode: unknown,
  maxMode: unknown
): ChatbookExecutionMode {
  const chosen = parseChatbookExecutionMode(mode);
  const cap = parseChatbookExecutionMode(
    maxMode,
    DEFAULT_CHATBOOK_MAX_EXECUTION_MODE
  );
  return CHATBOOK_EXECUTION_MODE_RANK[chosen] >
    CHATBOOK_EXECUTION_MODE_RANK[cap]
    ? cap
    : chosen;
}

export function chatbookNeedsConfirm(
  mode: ChatbookExecutionMode,
  scanLevel: ChatbookDangerLevel,
  options: { alreadyExecutedThisSession?: boolean } = {}
): boolean {
  if (options.alreadyExecutedThisSession && mode !== 'generate-only') {
    return false;
  }
  if (mode === 'always-confirm' || mode === 'generate-only') {
    return true;
  }
  if (mode === 'confirm-if-risky') {
    return scanLevel !== 'clean';
  }
  return false;
}

export function chatbookCanConfirmRun(mode: ChatbookExecutionMode): boolean {
  return mode !== 'generate-only';
}

const CHATBOOK_EXECUTION_MODE_SUMMARIES: Record<ChatbookExecutionMode, string> =
  {
    'generate-only': 'Chatbook is set to generate Python without running it.',
    'always-confirm': 'Chatbook is set to confirm every natural-language run.',
    'confirm-if-risky':
      'Chatbook is set to confirm only when the scan flags a risk.',
    'auto-run': 'Chatbook is set to run generated Python immediately.'
  };

/**
 * One-line reminder of the mode that produced a confirmation, so the bar can
 * point at the setting behind it.
 */
export function chatbookExecutionModeSummary(
  mode: ChatbookExecutionMode
): string {
  return CHATBOOK_EXECUTION_MODE_SUMMARIES[mode];
}

export const CHATBOOK_CONTEXT_MAX_FIELD_CHARS = 8000;
export const CHATBOOK_CONTEXT_MAX_OUTPUT_CHARS = 4000;

export interface IChatbookCellMeta {
  mode?: ChatbookCellMode;
  /** Input type the cell was authored in, kept across mode switches. */
  origin?: ChatbookCellMode;
  prompt?: string;
  promptHash?: string;
  generatedCode?: string;
  pythonSource?: string;
  codeHash?: string;
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
  notebookPath?: string;
  notebookContext?: IChatbookNotebookContext;
  pythonSource?: string;
  executionPolicy?: ChatbookExecutionMode;
  llmDangerScan?: boolean;
}

export function isChatbookKernelName(name: string | undefined | null): boolean {
  return (name ?? '').trim() === CHATBOOK_KERNEL_NAME;
}

export function isChatbookPromptInlineCompletion(
  kernelName: string | undefined | null,
  cellMode: ChatbookCellMode = 'prompt'
): boolean {
  return isChatbookKernelName(kernelName) && cellMode === 'prompt';
}

export function getChatbookCellMode(meta: IChatbookCellMeta): ChatbookCellMode {
  return meta.mode === 'python' ? 'python' : 'prompt';
}

/**
 * Both modes are always reachable. A cell with no Python yet shows an empty
 * Python editor, which the user can either run the prompt to fill or type into.
 */
export function canSwitchChatbookCellMode(
  meta: IChatbookCellMeta,
  nextMode: ChatbookCellMode
): boolean {
  return getChatbookCellMode(meta) !== nextMode;
}

export function getChatbookCellOrigin(
  meta: IChatbookCellMeta
): ChatbookCellMode {
  if (meta.origin === 'python' || meta.origin === 'prompt') {
    return meta.origin;
  }
  return getChatbookCellMode(meta);
}

export function hasChatbookPrompt(meta: IChatbookCellMeta): boolean {
  return Boolean((meta.prompt ?? '').trim());
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
  const chatbook: Record<string, unknown> = {
    ...getChatbookCellMeta(current),
    ...patch
  };
  // An explicit `undefined` in the patch clears the field rather than writing
  // an unserializable value into the notebook.
  for (const key of Object.keys(chatbook)) {
    if (chatbook[key] === undefined) {
      delete chatbook[key];
    }
  }
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
  return meta;
}

export function getNotebookNuiSessionId(notebookMetadata: unknown): string {
  return (getNotebookChatbookMeta(notebookMetadata).nuiSessionId ?? '').trim();
}

/**
 * Notebooks written before cell mode became the only state carry a notebook-wide
 * `sourceView`; in `code` view their prompt cells hold generated Python.
 */
export function hasLegacyChatbookCodeView(notebookMetadata: unknown): boolean {
  if (!notebookMetadata || typeof notebookMetadata !== 'object') {
    return false;
  }
  const chatbook = (
    notebookMetadata as { nbi?: { chatbook?: { sourceView?: unknown } } }
  ).nbi?.chatbook;
  return Boolean(chatbook) && chatbook?.sourceView === 'code';
}

export function withoutLegacyChatbookSourceView(
  notebookMetadata: unknown
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
  delete chatbook.sourceView;
  nbi.chatbook = chatbook;
  current.nbi = nbi;
  return current;
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
  meta: IChatbookCellMeta
): string {
  if (getChatbookCellMode(meta) === 'python') {
    return meta.prompt ?? '';
  }
  return source;
}

export function resolveChatbookPython(
  source: string,
  meta: IChatbookCellMeta
): string {
  if (getChatbookCellMode(meta) === 'python') {
    return source;
  }
  return meta.generatedCode || '';
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
}): {
  prompt: string;
  generatedCode: string;
  pythonSource: string;
  mode: ChatbookCellMode;
} {
  const mode = getChatbookCellMode(options.meta);
  if (mode === 'python') {
    const pythonSource = options.source;
    return {
      prompt: options.meta.prompt || '',
      generatedCode: pythonSource,
      pythonSource,
      mode
    };
  }
  // A run refreshes `generatedCode` alone, so `pythonSource` left over from an
  // earlier stint as a Python cell can be stale.
  const generatedCode = options.meta.generatedCode ?? '';
  return {
    prompt: options.source,
    generatedCode,
    pythonSource: generatedCode,
    mode
  };
}

export function switchChatbookCellMode(options: {
  source: string;
  meta: IChatbookCellMeta;
  nextMode: ChatbookCellMode;
}): { source: string; meta: IChatbookCellMeta } {
  const snapshot = snapshotChatbookCell(options);
  const pythonSource = snapshot.pythonSource || snapshot.generatedCode;
  const meta: IChatbookCellMeta = {
    ...options.meta,
    origin: options.meta.origin ?? snapshot.mode,
    mode: options.nextMode,
    prompt: snapshot.prompt,
    generatedCode: pythonSource,
    pythonSource
  };
  const source =
    options.nextMode === 'python'
      ? pythonSource || (snapshot.mode === 'python' ? options.source : '')
      : snapshot.prompt;
  return { source, meta };
}

export function convertChatbookCellToPython(options: {
  source: string;
  meta: IChatbookCellMeta;
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
  const cells = Array.isArray(notebook.cells)
    ? notebook.cells.map(cell => convertNotebookCellToPython(cell))
    : [];
  const metadata = withoutLegacyChatbookSourceView(notebook.metadata || {});
  metadata.kernelspec = kernelspec;
  metadata.language_info = { name: kernelspec.language };
  return {
    ...notebook,
    cells,
    metadata
  };
}

function convertNotebookCellToPython(cell: unknown): unknown {
  if (!cell || typeof cell !== 'object') {
    return cell;
  }
  const next = { ...(cell as Record<string, unknown>) };
  if (next.cell_type !== 'code') {
    return next;
  }
  const converted = convertChatbookCellToPython({
    source: cellSourceToString(next.source),
    meta: getChatbookCellMeta(next.metadata)
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
    const python = resolveChatbookPython(options.source, options.cellMeta);
    const prompt = options.cellMeta.prompt || '';
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
  const prompt = resolveChatbookPrompt(options.source, options.cellMeta);
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
  notebookPath?: string;
  notebookContext?: IChatbookNotebookContext;
  contextHash?: string;
  executeMode?: ChatbookCellMode;
  allowCachedCode?: boolean;
  pythonSource?: string;
  executionPolicy?: ChatbookExecutionMode;
  llmDangerScan?: boolean;
}): IChatbookExecuteMeta {
  const meta: IChatbookExecuteMeta = {
    cellId: options.cellId,
    promptHash: options.promptHash,
    executeMode: options.executeMode || 'prompt'
  };
  if (meta.executeMode === 'python') {
    if (options.pythonSource) {
      meta.pythonSource = options.pythonSource;
    }
    return meta;
  }
  if (options.executionPolicy) {
    meta.executionPolicy = options.executionPolicy;
  }
  if (options.llmDangerScan) {
    meta.llmDangerScan = true;
  }
  // The stored Python belongs to the prompt that produced it, so re-running an
  // unchanged prompt reuses it. Notebook context deliberately does not count:
  // it carries cell outputs, which this very cell changes when it runs, and
  // would make every re-run a miss.
  if (
    options.cellMeta.generatedCode &&
    options.cellMeta.promptHash === options.promptHash &&
    options.allowCachedCode !== false
  ) {
    meta.cachedCode = options.cellMeta.generatedCode;
  }
  if (options.generateUrl) {
    meta.generateUrl = options.generateUrl;
  }
  if (options.workingDir) {
    meta.workingDir = options.workingDir;
  }
  if (options.notebookPath) {
    meta.notebookPath = options.notebookPath;
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

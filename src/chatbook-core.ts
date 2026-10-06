// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

export const CHATBOOK_KERNEL_NAME = 'chatbook';
export const CHATBOOK_MSG_TYPE = 'nbi_chatbook_code';
export const CHATBOOK_LANGUAGE = 'chatbook';

export type ChatbookCellMode = 'prompt' | 'code';
export type ChatbookExecutionMode =
  | 'always-confirm'
  | 'confirm-if-risky'
  | 'auto-run';
export type ChatbookDangerLevel = 'clean' | 'risky';

export const CHATBOOK_EXECUTION_MODES: readonly ChatbookExecutionMode[] = [
  'always-confirm',
  'confirm-if-risky',
  'auto-run'
];
export const DEFAULT_CHATBOOK_EXECUTION_MODE: ChatbookExecutionMode =
  'always-confirm';
export const DEFAULT_CHATBOOK_MAX_EXECUTION_MODE: ChatbookExecutionMode =
  'auto-run';

/**
 * Detects whether a prompt carries a mention, the way `MENTION_TOKEN_RE` in
 * chatbook_mentions.py does. A quoted mention always also matches this unquoted
 * form, so presence needs nothing more. JS `\w` is ASCII-only, so after a
 * non-ASCII letter this can see a mention the server does not, which only costs
 * a regeneration.
 */
export const CHATBOOK_MENTION_TOKEN_RE = /(?<![\w@])@[^\s@]+/u;

export function promptHasChatbookMention(prompt: string): boolean {
  return CHATBOOK_MENTION_TOKEN_RE.test(prompt);
}

export function chatbookAllowsSessionCachedCode(options: {
  alreadyExecutedThisSession: boolean;
  hasMentionContext: boolean;
  hasContextProviders: boolean;
  hasGuidelines: boolean;
}): boolean {
  return (
    options.alreadyExecutedThisSession &&
    !options.hasMentionContext &&
    !options.hasContextProviders &&
    !options.hasGuidelines
  );
}

const CHATBOOK_EXECUTION_MODE_RANK: Record<ChatbookExecutionMode, number> = {
  'always-confirm': 0,
  'confirm-if-risky': 1,
  'auto-run': 2
};

export function parseChatbookExecutionMode(
  value: unknown,
  fallback: ChatbookExecutionMode = DEFAULT_CHATBOOK_EXECUTION_MODE
): ChatbookExecutionMode {
  const text = value === 'generate-only' ? 'always-confirm' : value;
  return CHATBOOK_EXECUTION_MODES.includes(text as ChatbookExecutionMode)
    ? (text as ChatbookExecutionMode)
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
  options: { codeAlreadyApproved?: boolean } = {}
): boolean {
  if (options.codeAlreadyApproved) {
    return false;
  }
  if (mode === 'always-confirm') {
    return true;
  }
  if (mode === 'confirm-if-risky') {
    return scanLevel !== 'clean';
  }
  return false;
}

const CHATBOOK_EXECUTION_MODE_SUMMARIES: Record<ChatbookExecutionMode, string> =
  {
    'always-confirm': 'Chatbook is set to confirm every natural-language run.',
    'confirm-if-risky':
      'Chatbook is set to confirm only when the scan flags a risk.',
    'auto-run': 'Chatbook is set to run generated code immediately.'
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
  codeSource?: string;
  codeHash?: string;
  // Hash of the English description together with the code it describes.
  summaryHash?: string;
  summaryError?: string;
  contextHash?: string;
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

export interface IChatbookExecuteMeta {
  cellId?: string;
  executeMode?: ChatbookCellMode;
  promptHash?: string;
  contextHash?: string;
  cachedCode?: string;
  workingDir?: string;
  notebookPath?: string;
  notebookContext?: IChatbookNotebookContext;
  codeSource?: string;
  executionPolicy?: ChatbookExecutionMode;
  llmDangerScan?: boolean;
  /**
   * Code the user already approved for this cell in this session. The kernel
   * runs it in the same request when regeneration returns it unchanged.
   */
  approvedCode?: string;
}

export function isChatbookLanguage(
  language: string | undefined | null
): boolean {
  return (language ?? '').trim().toLowerCase() === CHATBOOK_LANGUAGE;
}

/**
 * Names of the installed kernelspecs that run the Chatbook kernel. A custom
 * kernelspec manager can list Chatbook under its own name (nb_conda_kernels
 * lists it as `conda-base-chatbook`), so the name alone does not identify it;
 * the kernelspec's language does.
 */
const chatbookKernelNames = new Set<string>([CHATBOOK_KERNEL_NAME]);

/** Record which installed kernelspecs are Chatbook's, by language. */
export function setChatbookKernelSpecs(
  kernelspecs:
    | Record<string, { language?: string } | undefined>
    | null
    | undefined
): void {
  chatbookKernelNames.clear();
  chatbookKernelNames.add(CHATBOOK_KERNEL_NAME);
  for (const [name, spec] of Object.entries(kernelspecs ?? {})) {
    if (isChatbookLanguage(spec?.language)) {
      chatbookKernelNames.add(name);
    }
  }
}

export function isChatbookKernelName(name: string | undefined | null): boolean {
  return chatbookKernelNames.has((name ?? '').trim());
}

export function isChatbookPromptInlineCompletion(
  kernelName: string | undefined | null,
  cellMode: ChatbookCellMode = 'prompt'
): boolean {
  return isChatbookKernelName(kernelName) && cellMode !== 'code';
}

export function getChatbookCellMode(meta: IChatbookCellMeta): ChatbookCellMode {
  return meta.mode === 'code' ? 'code' : 'prompt';
}

/**
 * Both modes are always reachable. A cell with no code yet shows an empty
 * code editor, which the user can either run the prompt to fill or type into.
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
  if (meta.origin === 'prompt' || meta.origin === 'code') {
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

export function resolveChatbookPrompt(
  source: string,
  meta: IChatbookCellMeta
): string {
  if (getChatbookCellMode(meta) === 'code') {
    return meta.prompt ?? '';
  }
  return source;
}

export function resolveChatbookCode(
  source: string,
  meta: IChatbookCellMeta
): string {
  if (getChatbookCellMode(meta) === 'code') {
    return source;
  }
  return meta.generatedCode || '';
}

const LINE_COMMENT_PREFIX: Record<string, string> = {
  python: '#',
  py: '#',
  r: '#',
  ruby: '#',
  perl: '#',
  bash: '#',
  sh: '#',
  julia: '#',
  yaml: '#',
  toml: '#',
  sql: '--',
  lua: '--',
  haskell: '--',
  javascript: '//',
  js: '//',
  typescript: '//',
  ts: '//',
  java: '//',
  scala: '//',
  kotlin: '//',
  groovy: '//',
  csharp: '//',
  'c#': '//',
  fsharp: '//',
  'f#': '//',
  dart: '//',
  go: '//',
  c: '//',
  cpp: '//',
  'c++': '//',
  rust: '//',
  swift: '//',
  matlab: '%',
  octave: '%',
  clojure: ';',
  scheme: ';',
  lisp: ';',
  erlang: '%',
  fortran: '!'
};

export function lineCommentPrefix(language: string): string {
  const key = (language || '').trim().toLowerCase();
  if (/^(?:c|gnu)?\+\+(?:\d+)?$/u.test(key) || /^c\+\+\d+$/u.test(key)) {
    return '//';
  }
  const prefix = LINE_COMMENT_PREFIX[key];
  if (!prefix) {
    throw new Error(
      `Cannot export unrun Chatbook cells: no safe line comment for ${language || 'the selected language'}`
    );
  }
  return prefix;
}

function normalizeCommentNewlines(text: string): string {
  return text.replace(/\u2028|\u2029/gu, '\n').replace(/\r\n?/g, '\n');
}

export function promptAsHashComment(
  prompt: string,
  language = 'python'
): string {
  const prefix = lineCommentPrefix(language);
  const text = normalizeCommentNewlines(prompt).replace(/\s+$/u, '');
  if (!text) {
    return `${prefix} <empty Chatbook prompt>`;
  }
  return text
    .split('\n')
    .map(line => (line.length ? `${prefix} ${line}` : prefix))
    .join('\n');
}

function snapshotChatbookCell(options: {
  source: string;
  meta: IChatbookCellMeta;
}): {
  prompt: string;
  generatedCode: string;
  codeSource: string;
  mode: ChatbookCellMode;
} {
  const mode = getChatbookCellMode(options.meta);
  if (mode === 'code') {
    const codeSource = options.source;
    return {
      prompt: options.meta.prompt || '',
      generatedCode: codeSource,
      codeSource,
      mode
    };
  }
  // A run refreshes `generatedCode` alone, so `codeSource` left over from an
  // earlier stint as a code cell can be stale.
  const generatedCode = options.meta.generatedCode ?? '';
  return {
    prompt: options.source,
    generatedCode,
    codeSource: generatedCode,
    mode
  };
}

export function switchChatbookCellMode(options: {
  source: string;
  meta: IChatbookCellMeta;
  nextMode: ChatbookCellMode;
}): { source: string; meta: IChatbookCellMeta } {
  const snapshot = snapshotChatbookCell(options);
  const codeSource = snapshot.codeSource || snapshot.generatedCode;
  const nextMode = options.nextMode;
  const meta: IChatbookCellMeta = {
    ...options.meta,
    origin: options.meta.origin ?? snapshot.mode,
    mode: nextMode,
    prompt: snapshot.prompt,
    generatedCode: codeSource,
    codeSource
  };
  const source =
    nextMode === 'code'
      ? codeSource || (snapshot.mode === 'code' ? options.source : '')
      : snapshot.prompt;
  return { source, meta };
}

export async function convertChatbookCellToCode(options: {
  source: string;
  meta: IChatbookCellMeta;
  language?: string;
}): Promise<{ source: string; meta: IChatbookCellMeta }> {
  const snapshot = snapshotChatbookCell(options);
  if (snapshot.mode === 'code') {
    const code = snapshot.codeSource || snapshot.generatedCode;
    return {
      source: code || options.source,
      meta: {
        ...options.meta,
        mode: 'code',
        prompt: snapshot.prompt,
        codeSource: code || options.source
      }
    };
  }
  const meta: IChatbookCellMeta = {
    ...options.meta,
    prompt: snapshot.prompt || options.source
  };
  // `meta.prompt` records the prompt that produced `generatedCode`. If the
  // visible prompt has changed since that run, exporting the old code would
  // silently create a notebook that does something different from the cell.
  const generatedMatchesPrompt =
    Boolean(options.meta.promptHash) &&
    options.meta.promptHash === (await sha256Hex(snapshot.prompt));
  if (snapshot.generatedCode.trim() && generatedMatchesPrompt) {
    meta.generatedCode = snapshot.generatedCode;
    return { source: snapshot.generatedCode, meta };
  }
  // A code cell switched to natural language has no matching `promptHash`
  // until it runs as a prompt: its English was written from the code, not the
  // other way round. Its code still stands while the English is the
  // description made from exactly this code, or is empty on a cell that has
  // only ever been code.
  const code = snapshot.generatedCode;
  const describesCode =
    code.trim() &&
    ((Boolean(options.meta.summaryHash) &&
      options.meta.summaryHash ===
        (await chatbookSummaryHash(snapshot.prompt, code))) ||
      (!snapshot.prompt.trim() &&
        !options.meta.promptHash &&
        getChatbookCellOrigin(options.meta) === 'code'));
  if (describesCode) {
    meta.generatedCode = code;
    return { source: code, meta };
  }
  meta.generatedCode = undefined;
  meta.promptHash = undefined;
  meta.contextHash = undefined;
  return {
    source: promptAsHashComment(meta.prompt || '', options.language),
    meta
  };
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

export function chatbookExportNotebookPath(
  sourcePath: string,
  language: string,
  attempt = 0
): string {
  const normalized = sourcePath.replace(/\\/g, '/');
  const slash = normalized.lastIndexOf('/');
  const dir = slash >= 0 ? normalized.slice(0, slash) : '';
  const file = slash >= 0 ? normalized.slice(slash + 1) : normalized;
  const stem = file.replace(/\.ipynb$/i, '') || 'notebook';
  const slug = language.trim().toLowerCase() || 'code';
  const suffix = attempt > 0 ? `-${slug}-${attempt}` : `-${slug}`;
  const name = `${stem}${suffix}.ipynb`;
  return dir ? `${dir}/${name}` : name;
}

export async function buildCodeNotebookFromChatbook(
  notebook: Record<string, unknown>,
  kernelspec: IChatbookKernelSpec
): Promise<Record<string, unknown>> {
  const cells = Array.isArray(notebook.cells)
    ? await Promise.all(
        notebook.cells.map(cell =>
          convertNotebookCellToCode(cell, kernelspec.language)
        )
      )
    : [];
  const metadata: Record<string, unknown> = {
    ...((notebook.metadata as Record<string, unknown>) || {})
  };
  metadata.kernelspec = kernelspec;
  metadata.language_info = { name: kernelspec.language };
  return {
    ...notebook,
    cells,
    metadata
  };
}

/** A notebook's own kernel, read from its metadata rather than its session. */
export interface IChatbookSourceKernel {
  name: string;
  displayName: string;
  language: string;
}

export function chatbookSourceKernel(metadata: unknown): IChatbookSourceKernel {
  const record =
    metadata && typeof metadata === 'object'
      ? (metadata as Record<string, unknown>)
      : {};
  const spec =
    record.kernelspec && typeof record.kernelspec === 'object'
      ? (record.kernelspec as Record<string, unknown>)
      : {};
  const info =
    record.language_info && typeof record.language_info === 'object'
      ? (record.language_info as Record<string, unknown>)
      : {};
  return {
    name: String(spec.name ?? '').trim(),
    displayName: String(spec.display_name ?? '').trim(),
    language: chatbookLanguageId(String(spec.language || info.name || ''))
  };
}

/**
 * A kernel language as a comparable id: `Python` and `py` are `python`. An
 * unknown language stays empty.
 */
export function chatbookLanguageId(raw: string): string {
  const language = raw.trim().toLowerCase();
  return language === 'py' ? 'python' : language;
}

/**
 * How a notebook's kernel relates to the Chatbook backend it would run on
 * after conversion. The backend is one user setting shared by every Chatbook,
 * so a notebook in another language would become a Chatbook whose code cells
 * all fail.
 */
export type ChatbookConversionFit =
  | 'same-kernel'
  | 'same-language'
  | 'unknown'
  | 'different-language';

export function chatbookConversionFit(
  source: IChatbookSourceKernel,
  backend: { kernelName: string; language: string }
): ChatbookConversionFit {
  if (source.name && source.name === backend.kernelName) {
    return 'same-kernel';
  }
  if (!source.language) {
    return 'unknown';
  }
  return source.language === chatbookLanguageId(backend.language)
    ? 'same-language'
    : 'different-language';
}

/**
 * A code cell as a Chatbook code cell. An English description is kept only
 * when it is known to describe this exact code, as for a Chatbook that was
 * exported and is converted back; anything else describes different code.
 * A natural-language cell from a Chatbook whose kernel was switched holds its
 * prompt, not code, and is left as it is. An exported natural-language cell
 * holds code or a comment instead, and is converted.
 */
export async function convertCodeCellToChatbook(
  cell: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const previous = getChatbookCellMeta(cell.metadata);
  const source = cellSourceToString(cell.source);
  if (
    getChatbookCellMode(previous) === 'prompt' &&
    source.trim() &&
    source === previous.prompt
  ) {
    return cell;
  }
  const meta: IChatbookCellMeta = {
    mode: 'code',
    origin: 'code',
    codeSource: source,
    generatedCode: source
  };
  const prompt = previous.prompt ?? '';
  // A failed refresh leaves the old description beside the new code's hash.
  if (prompt.trim() && !previous.summaryError) {
    const codeHash = await sha256Hex(source);
    const describesThisCode =
      previous.codeHash === codeHash ||
      // An exported natural-language cell carries the code its prompt made.
      (getChatbookCellMode(previous) === 'prompt' &&
        previous.generatedCode === source &&
        Boolean(previous.promptHash) &&
        previous.promptHash === (await sha256Hex(prompt)));
    if (describesThisCode) {
      // Only a recorded prompt origin survives: this cell arrives as code.
      meta.origin = previous.origin === 'prompt' ? 'prompt' : 'code';
      meta.prompt = prompt;
      meta.codeHash = codeHash;
      if (previous.promptHash) {
        meta.promptHash = previous.promptHash;
      }
    }
  }
  const metadata: Record<string, unknown> =
    cell.metadata && typeof cell.metadata === 'object'
      ? { ...(cell.metadata as Record<string, unknown>) }
      : {};
  const nbi: Record<string, unknown> =
    metadata.nbi && typeof metadata.nbi === 'object'
      ? { ...(metadata.nbi as Record<string, unknown>) }
      : {};
  nbi.chatbook = meta;
  metadata.nbi = nbi;
  return { ...cell, metadata };
}

/**
 * A copy of a code notebook as a Chatbook: every code cell becomes a Chatbook
 * code cell, and everything else (markdown, outputs, execution counts, ids,
 * attachments, other notebook metadata) is kept. The kernelspec becomes
 * Chatbook's, and the source kernel's name, when it has one, is recorded.
 */
export async function buildChatbookFromCodeNotebook(
  notebook: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const cells = Array.isArray(notebook.cells)
    ? await Promise.all(
        notebook.cells.map(cell =>
          cell &&
          typeof cell === 'object' &&
          (cell as Record<string, unknown>).cell_type === 'code'
            ? convertCodeCellToChatbook(cell as Record<string, unknown>)
            : cell
        )
      )
    : [];
  const metadata: Record<string, unknown> = {
    ...((notebook.metadata as Record<string, unknown>) || {})
  };
  const source = chatbookSourceKernel(metadata);
  metadata.kernelspec = {
    name: CHATBOOK_KERNEL_NAME,
    display_name: 'Chatbook',
    language: CHATBOOK_LANGUAGE
  };
  metadata.language_info = { name: CHATBOOK_LANGUAGE };
  if (source.name) {
    const nbi =
      metadata.nbi && typeof metadata.nbi === 'object'
        ? { ...(metadata.nbi as Record<string, unknown>) }
        : {};
    const chatbook =
      nbi.chatbook && typeof nbi.chatbook === 'object'
        ? { ...(nbi.chatbook as Record<string, unknown>) }
        : {};
    chatbook.sourceKernel = source.name;
    nbi.chatbook = chatbook;
    metadata.nbi = nbi;
  }
  return { ...notebook, cells, metadata };
}

async function convertNotebookCellToCode(
  cell: unknown,
  language: string
): Promise<unknown> {
  if (!cell || typeof cell !== 'object') {
    return cell;
  }
  const next = { ...(cell as Record<string, unknown>) };
  if (next.cell_type !== 'code') {
    return next;
  }
  const converted = await convertChatbookCellToCode({
    source: cellSourceToString(next.source),
    meta: getChatbookCellMeta(next.metadata),
    language
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
  if (mode === 'code') {
    const code = resolveChatbookCode(options.source, options.cellMeta);
    const prompt = options.cellMeta.prompt || '';
    if (prompt) {
      cell.prompt = truncateChatbookContextField(
        prompt,
        CHATBOOK_CONTEXT_MAX_FIELD_CHARS
      );
    }
    if (code) {
      cell.generatedCode = truncateChatbookContextField(
        code,
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
  workingDir?: string;
  notebookPath?: string;
  notebookContext?: IChatbookNotebookContext;
  contextHash?: string;
  executeMode?: ChatbookCellMode;
  allowCachedCode?: boolean;
  codeSource?: string;
  executionPolicy?: ChatbookExecutionMode;
  llmDangerScan?: boolean;
  approvedCode?: string;
}): IChatbookExecuteMeta {
  const meta: IChatbookExecuteMeta = {
    cellId: options.cellId,
    promptHash: options.promptHash,
    executeMode: options.executeMode === 'code' ? 'code' : 'prompt'
  };
  if (meta.executeMode === 'code') {
    if (options.codeSource) {
      meta.codeSource = options.codeSource;
    }
    return meta;
  }
  if (options.executionPolicy) {
    meta.executionPolicy = options.executionPolicy;
  }
  if (options.llmDangerScan) {
    meta.llmDangerScan = true;
  }
  if (options.approvedCode) {
    meta.approvedCode = options.approvedCode;
  }
  // Cached code is session-opt-in. Notebook files are not a trust boundary:
  // persisted `generatedCode` can be attacker-authored, so the client must
  // have already run this prompt in this session (`allowCachedCode: true`).
  // Notebook context deliberately does not count: it carries cell outputs,
  // which this very cell changes when it runs, and would make every re-run
  // a miss.
  if (
    options.allowCachedCode === true &&
    options.cellMeta.generatedCode &&
    options.cellMeta.promptHash === options.promptHash
  ) {
    meta.cachedCode = options.cellMeta.generatedCode;
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

/** Ties an English description to the exact code it was written from. */
export function chatbookSummaryHash(
  prompt: string,
  code: string
): Promise<string> {
  return sha256Hex(JSON.stringify([prompt, code]));
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

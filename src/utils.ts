// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { CodeCell } from '@jupyterlab/cells';
import { PartialJSONObject } from '@lumino/coreutils';
import { CodeEditor } from '@jupyterlab/codeeditor';
import { IDocumentManager } from '@jupyterlab/docmanager';
import { FileDialog } from '@jupyterlab/filebrowser';
import { encoding_for_model } from 'tiktoken';
import { NotebookPanel } from '@jupyterlab/notebook';
import stripAnsi from 'strip-ansi';

import { shellSingleQuote } from './shell-utils';
import { isControlOrBidiCodepoint } from './text-codepoints';

const tiktoken_encoding = encoding_for_model('gpt-4o');

export async function waitForDuration(duration: number): Promise<void> {
  return new Promise(resolve => {
    setTimeout(() => {
      resolve();
    }, duration);
  });
}

export function extractLLMGeneratedCode(code: string): string {
  // Strip our backend-emitted stream-interruption marker. The Claude inline
  // handler pushes it into the same text channel so the diff pane shows
  // what went wrong, but we never want it landing verbatim in the user's
  // file when fresh-generation auto-inserts the result (or when the user
  // accepts a truncated diff). The pattern is anchored to end-of-string
  // because the backend always emits the marker as the last delta, and
  // its closing bracket is required so legitimate generated code that
  // happens to contain the phrase mid-buffer (e.g.
  // ``print("[Stream interrupted: demo]")``) is not stripped. Greedy
  // ``[^\n]*\]`` backtracks to the last ``]`` on the marker line, so
  // bracketed exception strings such as
  // ``[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate``
  // and ``[Errno 11001] getaddrinfo failed`` are still matched in full.
  code = code.replace(/\n*\[Stream interrupted:[^\n]*\]\n*$/, '');
  if (code.endsWith('```')) {
    code = code.slice(0, -3);
  }

  const lines = code.split('\n');
  if (lines.length < 2) {
    return code;
  }

  const numLines = lines.length;
  let startLine = -1;
  let endLine = numLines;

  for (let i = 0; i < numLines; i++) {
    if (startLine === -1) {
      if (lines[i].trimStart().startsWith('```')) {
        startLine = i;
        continue;
      }
    } else {
      if (lines[i].trimStart().startsWith('```')) {
        endLine = i;
        break;
      }
    }
  }

  if (startLine !== -1) {
    return lines.slice(startLine + 1, endLine).join('\n');
  }

  return code;
}

export function isDarkTheme(): boolean {
  return document.body.getAttribute('data-jp-theme-light') === 'false';
}

export function markdownToComment(source: string): string {
  return source
    .split('\n')
    .map(line => `# ${line}`)
    .join('\n');
}

export function formatJupyterError(output: any): string {
  const head = `${output.ename ?? 'Error'}: ${output.evalue ?? ''}`.trim();
  const tb = Array.isArray(output.traceback)
    ? output.traceback.map((line: string) => stripAnsi(line)).join('\n')
    : '';
  return tb ? `${head}\n${tb}` : head;
}

// True when the output area contains at least one error output. Avoids the
// full toJSON() serialization callers used to do for a 1-bit check.
export function cellOutputHasError(cell: CodeCell): boolean {
  const model = cell.outputArea.model;
  for (let i = 0; i < model.length; i++) {
    if (model.get(i).type === 'error') {
      return true;
    }
  }
  return false;
}

export function cellOutputAsText(cell: CodeCell): string {
  let content = '';
  const outputs = cell.outputArea.model.toJSON();
  for (const output of outputs) {
    if (output.output_type === 'execute_result') {
      const data =
        typeof output.data === 'object' && output.data !== null
          ? (output.data as PartialJSONObject)['text/plain']
          : undefined;
      content += joinMultilineString(data);
    } else if (output.output_type === 'stream') {
      content += joinMultilineString(output.text) + '\n';
    } else if (output.output_type === 'error') {
      // Skip errors without a traceback to match historical behavior of this
      // function; the head-only case is intentional here.
      if (Array.isArray(output.traceback)) {
        content += formatJupyterError(output) + '\n';
      }
    }
  }

  return content;
}

// nbformat allows text-shaped output fields (stream `text`, `data['text/plain']`,
// `data['text/html']`, etc.) to be either a single string or a list of strings,
// joined with the empty string. Some kernels (e.g. older IPython, R) emit the
// list form for multi-line output. Plain `String([...])` coerces a list to
// `"a,b,c"` — wrong for both display and tokenization. Centralize the join.
export function joinMultilineString(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  if (Array.isArray(value)) {
    return value
      .map(v => (v === null || v === undefined ? '' : String(v)))
      .join('');
  }
  return String(value);
}

export function getTokenCount(source: string): number {
  const tokens = tiktoken_encoding.encode(source);
  return tokens.length;
}

// Encode once, slice the token array, decode back. Avoids the O(log n)
// re-encoding a binary search would do on every truncation. Returns
// `truncated: true` when the input exceeded the cap so callers don't need a
// second `getTokenCount` pass to detect truncation.
export function truncateToTokenCount(
  text: string,
  maxTokens: number
): { text: string; size: number; truncated: boolean } {
  if (maxTokens <= 0 || text.length === 0) {
    return { text: '', size: 0, truncated: text.length > 0 };
  }
  const tokens = tiktoken_encoding.encode(text);
  if (tokens.length <= maxTokens) {
    return { text, size: tokens.length, truncated: false };
  }
  const sliced = tokens.slice(0, maxTokens);
  const bytes = tiktoken_encoding.decode(sliced);
  const decoded = new TextDecoder('utf-8').decode(bytes);
  return { text: decoded, size: sliced.length, truncated: true };
}

export function compareSelectionPoints(
  lhs: CodeEditor.IPosition,
  rhs: CodeEditor.IPosition
): boolean {
  return lhs.line === rhs.line && lhs.column === rhs.column;
}

export function compareSelections(
  lhs: CodeEditor.IRange,
  rhs: CodeEditor.IRange
): boolean {
  // if one undefined
  if ((!lhs || !rhs) && !(!lhs && !rhs)) {
    return true;
  }

  return (
    lhs === rhs ||
    (compareSelectionPoints(lhs.start, rhs.start) &&
      compareSelectionPoints(lhs.end, rhs.end))
  );
}

export function isSelectionEmpty(selection: CodeEditor.IRange): boolean {
  return (
    selection.start.line === selection.end.line &&
    selection.start.column === selection.end.column
  );
}

export function getSelectionInEditor(editor: CodeEditor.IEditor): string {
  const selection = editor.getSelection();
  const startOffset = editor.getOffsetAt(selection.start);
  const endOffset = editor.getOffsetAt(selection.end);
  return editor.model.sharedModel.getSource().substring(startOffset, endOffset);
}

export function getWholeNotebookContent(np: NotebookPanel): string {
  let content = '';
  for (const cell of np.content.widgets) {
    const cellModel = cell.model.sharedModel;
    if (cellModel.cell_type === 'code') {
      content += cellModel.source + '\n';
    } else if (cellModel.cell_type === 'markdown') {
      content += markdownToComment(cellModel.source) + '\n';
    }
  }

  return content;
}

export function applyCodeToSelectionInEditor(
  editor: CodeEditor.IEditor,
  code: string
) {
  const selection = editor.getSelection();
  const selectionStartOffset = editor.getOffsetAt(selection.start);
  const selectionEndOffset = editor.getOffsetAt(selection.end);
  const startOffset = Math.min(selectionStartOffset, selectionEndOffset);
  const endOffset = Math.max(selectionStartOffset, selectionEndOffset);
  const cursorOffset = startOffset + code.length;
  const codeMirrorEditor = editor as CodeEditor.IEditor & {
    editor?: {
      dispatch: (spec: {
        changes: { from: number; to: number; insert: string };
        selection: { anchor: number };
        scrollIntoView: boolean;
      }) => void;
    };
  };

  if (codeMirrorEditor.editor?.dispatch) {
    codeMirrorEditor.editor.dispatch({
      changes: { from: startOffset, to: endOffset, insert: code },
      selection: { anchor: cursorOffset },
      scrollIntoView: true
    });
  } else {
    editor.model.sharedModel.updateSource(startOffset, endOffset, code);
  }

  const cursorLine = Math.min(
    editor.getPositionAt(cursorOffset).line,
    editor.lineCount - 1
  );
  const cursorColumn = editor.getLine(cursorLine)?.length || 0;
  editor.setCursorPosition({
    line: cursorLine,
    column: cursorColumn
  });
}

export { shellSingleQuote };

const SAFE_ANCHOR_SCHEMES = new Set(['http', 'https', 'mailto']);
const SCHEME_RE = /^([A-Za-z][A-Za-z0-9+.-]*):/;
// Hard cap on URI length to short-circuit pathological inputs. Mirrors
// the Python side; modern browsers truncate URLs well below this and an
// anchor URI any longer is almost certainly hostile or malformed.
const MAX_ANCHOR_URI_LEN = 8192;

function isDisallowedUriCodepoint(code: number): boolean {
  // C0 + DEL are stripped from the scheme by some browser URL parsers
  // ahead of evaluation, so a tab/newline inside "javascript" would unmask.
  // C1 (0x80-0x9F) plus the Unicode format/BiDi/zero-width marks listed
  // below do not un-mask a forbidden scheme in modern browsers, but they
  // can visually impersonate the URI in the title, so reject them too.
  // Ranges intentionally mirror the Python ``_DISALLOWED_URI_CODEPOINTS``
  // set so a URI rejected on one side is rejected on the other.
  if (isControlOrBidiCodepoint(code)) {
    return true;
  }
  if (code === 0x00a0 || code === 0xfeff) {
    return true;
  }
  if (code >= 0x200b && code <= 0x200f) {
    return true;
  }
  if (code >= 0x206a && code <= 0x206f) {
    return true;
  }
  return false;
}

/**
 * True when `s` contains any codepoint in the same set `safeAnchorUri`
 * rejects (C0/DEL/C1, NEL/NBSP/LS/PS/BOM, ZWSP, bidi-override controls).
 * Mirrors the Python `has_dangerous_text_codepoints`. Used to scrub the
 * `title` attribute on rendered anchors so an LLM-emitted hover tooltip
 * can't visually impersonate the link via bidi-reorder or zero-width
 * tricks.
 */
export function hasDangerousTextCodepoints(
  s: string | undefined | null
): boolean {
  if (typeof s !== 'string') {
    return false;
  }
  for (let i = 0; i < s.length; i++) {
    if (isDisallowedUriCodepoint(s.charCodeAt(i))) {
      return true;
    }
  }
  return false;
}

/**
 * Return `uri` if its scheme is in the chat-anchor allowlist, else null.
 * Mirrors the server-side `safe_anchor_uri` check so that anchor parts
 * coming from arbitrary LLM/tool output cannot render `javascript:`,
 * `data:`, `vbscript:`, `blob:`, or other dangerous schemes through React's
 * `href` attribute. The server applies the same filter at emit time; this
 * is defense in depth for stream replays, persisted history, and any path
 * that injects anchor parts directly into the React tree.
 */
export function safeAnchorUri(uri: string | undefined | null): string | null {
  if (typeof uri !== 'string') {
    return null;
  }
  if (uri.length > MAX_ANCHOR_URI_LEN) {
    return null;
  }
  // Scan the original input. String.prototype.trim() drops NBSP, NEL, LS,
  // PS, BOM, and other Unicode whitespace, so a check after trim would let
  // those codepoints slip past as a trailing edge.
  for (let i = 0; i < uri.length; i++) {
    if (isDisallowedUriCodepoint(uri.charCodeAt(i))) {
      return null;
    }
  }
  const stripped = uri.trim();
  if (stripped.length === 0) {
    return null;
  }
  const match = SCHEME_RE.exec(stripped);
  if (!match) {
    return null;
  }
  if (!SAFE_ANCHOR_SCHEMES.has(match[1].toLowerCase())) {
    return null;
  }
  return stripped;
}

/**
 * Build a `claude --resume <id>` command wrapped in `cd <cwd>` so the
 * resulting one-liner works from any terminal. `claude --resume` is
 * cwd-scoped — it looks up the transcript under the encoded form of the
 * user's CURRENT shell cwd — so the bare id alone only works when the
 * user happens to be in the JupyterLab working directory.
 */
export function buildResumeCommand(cwd: string, sessionId: string): string {
  const quotedSessionId = shellSingleQuote(sessionId);
  if (!cwd) {
    return `claude --resume ${quotedSessionId}`;
  }
  return `cd ${shellSingleQuote(cwd)} && claude --resume ${quotedSessionId}`;
}

/**
 * Write `text` to the system clipboard. Falls back to a hidden textarea +
 * `document.execCommand('copy')` when the async Clipboard API is unavailable
 * or rejects (e.g. missing permission, insecure context).
 */
export async function writeTextToClipboard(text: string): Promise<boolean> {
  try {
    if (
      typeof navigator !== 'undefined' &&
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === 'function'
    ) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to legacy path
  }

  if (typeof document === 'undefined') {
    return false;
  }
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'absolute';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

// Prompt the user for a start directory for a coding-agent terminal.
// Returns the chosen path relative to the Jupyter server root (`''`
// means the root itself — a valid selection, not a sentinel), or
// `undefined` if the user cancelled the dialog.
export async function chooseWorkspaceDirectory(
  docManager: IDocumentManager,
  label: string,
  defaultPath?: string
): Promise<string | undefined> {
  const result = await FileDialog.getExistingDirectory({
    manager: docManager,
    title: label,
    label,
    defaultPath
  });
  if (!result.button.accept) {
    return undefined;
  }
  // JupyterLab's FileDialog falls back to the file browser's current
  // path when nothing is selected, so result.value is normally a
  // single-element array. The empty-array branch is purely defensive.
  const value = result.value;
  return value && value.length > 0 ? value[0].path : (defaultPath ?? '');
}

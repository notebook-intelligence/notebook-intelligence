// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { CodeCell } from '@jupyterlab/cells';
import { PartialJSONObject } from '@lumino/coreutils';
import { CodeEditor } from '@jupyterlab/codeeditor';
import { encoding_for_model } from 'tiktoken';
import { NotebookPanel } from '@jupyterlab/notebook';

const tiktoken_encoding = encoding_for_model('gpt-4o');

export function removeAnsiChars(str: string): string {
  return str.replace(
    // eslint-disable-next-line no-control-regex
    /[\u001b\u009b][[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]/g,
    ''
  );
}

export async function waitForDuration(duration: number): Promise<void> {
  return new Promise(resolve => {
    setTimeout(() => {
      resolve();
    }, duration);
  });
}

export function moveCodeSectionBoundaryMarkersToNewLine(
  source: string
): string {
  const existingLines = source.split('\n');
  const newLines = [];
  for (const line of existingLines) {
    if (line.length > 3 && line.startsWith('```')) {
      newLines.push('```');
      let remaining = line.substring(3);
      if (remaining.startsWith('python')) {
        if (remaining.length === 6) {
          continue;
        }
        remaining = remaining.substring(6);
      }
      if (remaining.endsWith('```')) {
        newLines.push(remaining.substring(0, remaining.length - 3));
        newLines.push('```');
      } else {
        newLines.push(remaining);
      }
    } else if (line.length > 3 && line.endsWith('```')) {
      newLines.push(line.substring(0, line.length - 3));
      newLines.push('```');
    } else {
      newLines.push(line);
    }
  }
  return newLines.join('\n');
}

export function extractLLMGeneratedCode(code: string): string {
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
    ? output.traceback.map((line: string) => removeAnsiChars(line)).join('\n')
    : '';
  return tb ? `${head}\n${tb}` : head;
}

export function cellOutputAsText(cell: CodeCell): string {
  let content = '';
  const outputs = cell.outputArea.model.toJSON();
  for (const output of outputs) {
    if (output.output_type === 'execute_result') {
      content +=
        typeof output.data === 'object' && output.data !== null
          ? (output.data as PartialJSONObject)['text/plain']
          : '' + '\n';
    } else if (output.output_type === 'stream') {
      content += output.text + '\n';
    } else if (output.output_type === 'error') {
      // Preserve the existing behavior of skipping errors without a
      // traceback array; the bundle formatter does include the head alone.
      if (Array.isArray(output.traceback)) {
        content += formatJupyterError(output) + '\n';
      }
    }
  }

  return content;
}

export function getTokenCount(source: string): number {
  const tokens = tiktoken_encoding.encode(source);
  return tokens.length;
}

// Encode once, slice the token array, decode back. Avoids the O(log n)
// re-encoding a binary search would do on every truncation.
export function truncateToTokenCount(
  text: string,
  maxTokens: number
): { text: string; size: number } {
  if (maxTokens <= 0 || text.length === 0) {
    return { text: '', size: 0 };
  }
  const tokens = tiktoken_encoding.encode(text);
  if (tokens.length <= maxTokens) {
    return { text, size: tokens.length };
  }
  const sliced = tokens.slice(0, maxTokens);
  const bytes = tiktoken_encoding.decode(sliced);
  const decoded = new TextDecoder('utf-8').decode(bytes);
  return { text: decoded, size: sliced.length };
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
  const startOffset = editor.getOffsetAt(selection.start);
  const endOffset = editor.getOffsetAt(selection.end);

  editor.model.sharedModel.updateSource(startOffset, endOffset, code);
  const numAddedLines = code.split('\n').length;
  const cursorLine = Math.min(
    selection.start.line + numAddedLines - 1,
    editor.lineCount - 1
  );
  const cursorColumn = editor.getLine(cursorLine)?.length || 0;
  editor.setCursorPosition({
    line: cursorLine,
    column: cursorColumn
  });
}

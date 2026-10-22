// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Pure helpers extracted from `terminal-drag.ts` so they can be unit
// tested without JupyterLab / Lumino imports (which pull DOM globals
// that jsdom doesn't provide, like DragEvent).

import { shellSingleQuote } from './shell-utils';
import { isControlOrBidiCodepoint } from './text-codepoints';

export type DragMode = 'mention' | 'raw';

/**
 * Detect characters that terminal input can interpret as keys or control
 * sequences, plus Unicode line/paragraph separators and bidirectional
 * formatting controls that can make a path look misleading. Shell quoting
 * does not protect every program that may be running in a terminal.
 * This check does not escape shell syntax; mention mode still emits bare
 * Claude Code @-mentions, while raw mode quotes paths for POSIX shells.
 */
export function hasTerminalControlCharacters(path: string): boolean {
  for (let i = 0; i < path.length; i++) {
    if (isControlOrBidiCodepoint(path.charCodeAt(i))) {
      return true;
    }
  }
  return false;
}

/**
 * Format a list of paths for injection per mode. @-mention prefixes each
 * path with "@" (Claude Code syntax, no quoting); raw mode shell-escapes
 * absolute paths for non-Claude shell sessions. Single-space separator
 * is intentional; the trailing space is appended by the caller.
 */
export function formatForMode(paths: string[], mode: DragMode): string {
  if (mode === 'mention') {
    return paths.map(p => `@${p}`).join(' ');
  }
  return paths.map(shellSingleQuote).join(' ');
}

export function invertMode(mode: DragMode, shouldInvert: boolean): DragMode {
  if (!shouldInvert) {
    return mode;
  }
  return mode === 'mention' ? 'raw' : 'mention';
}

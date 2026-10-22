// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * Identify controls unsafe in terminal input or security-sensitive labels.
 * C0, DEL, and C1 can act as terminal key controls. Unicode line/paragraph
 * separators and bidi embeddings, overrides, and isolates can disguise how
 * text is displayed; they are rejected for display spoofing, not key handling.
 */
export function isControlOrBidiCodepoint(code: number): boolean {
  return (
    (code >= 0x00 && code <= 0x1f) ||
    (code >= 0x7f && code <= 0x9f) ||
    (code >= 0x2028 && code <= 0x202e) ||
    (code >= 0x2066 && code <= 0x2069)
  );
}

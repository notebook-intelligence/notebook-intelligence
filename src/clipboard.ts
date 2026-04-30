// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * Write `text` to the system clipboard. Falls back to a hidden textarea +
 * `document.execCommand('copy')` when the async Clipboard API is unavailable
 * or rejects (e.g. missing permission, insecure context). Returns `true` on
 * success.
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

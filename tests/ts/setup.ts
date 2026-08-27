// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import '@testing-library/jest-dom';
import { TextDecoder, TextEncoder } from 'util';
import { webcrypto } from 'crypto';

// jsdom doesn't expose TextDecoder/TextEncoder; the encoder helpers in
// utils.ts use them.
if (typeof globalThis.TextDecoder === 'undefined') {
  globalThis.TextDecoder = TextDecoder as typeof globalThis.TextDecoder;
}
if (typeof globalThis.TextEncoder === 'undefined') {
  globalThis.TextEncoder = TextEncoder as typeof globalThis.TextEncoder;
}
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, 'crypto', {
    value: webcrypto,
    configurable: true
  });
}

// jsdom doesn't expose DragEvent; @lumino/dragdrop references it at module
// load. A subclass of MouseEvent keeps the `instanceof` chain one-way: a
// real DragEvent is also a MouseEvent, but a plain MouseEvent isn't a
// DragEvent, so tests that use `event instanceof DragEvent` to discriminate
// drag from click still get the right answer.
if (typeof (globalThis as { DragEvent?: unknown }).DragEvent === 'undefined') {
  class DragEventShim extends MouseEvent {}
  (globalThis as { DragEvent: unknown }).DragEvent = DragEventShim;
}

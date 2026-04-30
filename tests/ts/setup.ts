// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import '@testing-library/jest-dom';
import { TextDecoder, TextEncoder } from 'util';

// jsdom doesn't expose TextDecoder/TextEncoder; the encoder helpers in
// utils.ts use them.
if (typeof globalThis.TextDecoder === 'undefined') {
  globalThis.TextDecoder = TextDecoder as typeof globalThis.TextDecoder;
}
if (typeof globalThis.TextEncoder === 'undefined') {
  globalThis.TextEncoder = TextEncoder as typeof globalThis.TextEncoder;
}

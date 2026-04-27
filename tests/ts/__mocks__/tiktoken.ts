// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Lightweight tiktoken stand-in for unit tests. The real package wraps a
// WebAssembly module that doesn't initialize under jsdom and isn't needed
// to validate the TypeScript wrappers around it. Token count here is "one
// per whitespace-delimited word" — deterministic and good enough for tests
// that only care that the wrapper returns a sensible number.

export function encoding_for_model(_model: string): {
  encode: (text: string) => number[];
} {
  return {
    encode(text: string): number[] {
      if (text === '') {
        return [];
      }
      // Token IDs aren't asserted on — only the count is. Return a fixed
      // value per token so the array length matches whitespace-delimited
      // word count.
      return text
        .split(/\s+/)
        .filter(Boolean)
        .map(() => 0);
    }
  };
}

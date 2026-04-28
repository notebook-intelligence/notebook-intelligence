// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Lightweight tiktoken stand-in for unit tests. The real package wraps a
// WebAssembly module that doesn't initialize under jsdom and isn't needed
// to validate the TypeScript wrappers around it. Token count here is "one
// per whitespace-delimited word" — deterministic and good enough for tests
// that only care that the wrapper returns a sensible number.

// Per-encoder state holds the original token-to-text mapping so `decode`
// can round-trip what `encode` produced. This is sufficient for tests that
// truncate to N tokens and check the resulting text.
export function encoding_for_model(_model: string): {
  encode: (text: string) => number[];
  decode: (tokens: number[]) => Uint8Array;
} {
  const tokenToWord: string[] = [];
  return {
    encode(text: string): number[] {
      if (text === '') {
        return [];
      }
      // Token count = whitespace-delimited word count. Each occurrence
      // gets a fresh ID so `decode([id])` reproduces the original word.
      const ids: number[] = [];
      const words = text.split(/(\s+)/).filter(Boolean);
      for (const word of words) {
        const id = tokenToWord.length;
        tokenToWord.push(word);
        ids.push(id);
      }
      return ids.filter((_, i) => /\S/.test(tokenToWord[ids[i]]));
    },
    decode(tokens: number[]): Uint8Array {
      const text = tokens.map(t => tokenToWord[t] ?? '').join(' ');
      return new TextEncoder().encode(text);
    }
  };
}

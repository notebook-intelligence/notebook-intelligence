// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  buildExecuteChatbookMeta,
  getChatbookCellMeta,
  isChatbookKernelName,
  mergeChatbookCellMeta,
  mergeNotebookNuiSessionId,
  sha256Hex
} from '../../src/chatbook-core';

describe('chatbook-core', () => {
  it('recognizes the chatbook kernel name', () => {
    expect(isChatbookKernelName('chatbook')).toBe(true);
    expect(isChatbookKernelName('python3')).toBe(false);
    expect(isChatbookKernelName('')).toBe(false);
  });

  it('reads and merges cell metadata under nbi.chatbook', () => {
    expect(getChatbookCellMeta({ trusted: true })).toEqual({});
    const merged = mergeChatbookCellMeta(
      { trusted: true },
      { generatedCode: 'print(1)', promptHash: 'abc' }
    );
    expect(merged.trusted).toBe(true);
    expect((merged.nbi as any).chatbook.generatedCode).toBe('print(1)');
    expect(getChatbookCellMeta(merged).promptHash).toBe('abc');
  });

  it('stores nui session id on the notebook', () => {
    const merged = mergeNotebookNuiSessionId({}, 'sess-1');
    expect((merged.nbi as any).chatbook.nuiSessionId).toBe('sess-1');
  });

  it('passes cachedCode only when the prompt hash matches', () => {
    const hit = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: 'plot',
      promptHash: 'aaa',
      cellMeta: { generatedCode: 'x = 1', promptHash: 'aaa' }
    });
    expect(hit.cachedCode).toBe('x = 1');

    const miss = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: 'plot',
      promptHash: 'bbb',
      cellMeta: { generatedCode: 'x = 1', promptHash: 'aaa' }
    });
    expect(miss.cachedCode).toBeUndefined();
  });

  it('hashes prompts with sha-256', async () => {
    const digest = await sha256Hex('hello');
    expect(digest).toBe(
      '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    );
  });
});

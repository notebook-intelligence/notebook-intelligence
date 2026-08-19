// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  applySourceViewToCell,
  buildExecuteChatbookMeta,
  buildPythonNotebookFromChatbook,
  convertChatbookCellToPython,
  getChatbookCellMeta,
  getNotebookSourceView,
  isChatbookConvertTargetId,
  isChatbookKernelName,
  mergeChatbookCellMeta,
  mergeNotebookNuiSessionId,
  promptAsHashComment,
  pythonExportNotebookPath,
  resolveChatbookPrompt,
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

  it('toggles cell source between prompt and generated code', () => {
    const toCode = applySourceViewToCell({
      source: 'plot sales',
      meta: { generatedCode: 'print(1)' },
      currentView: 'prompt',
      nextView: 'code'
    });
    expect(toCode.source).toBe('print(1)');
    expect(toCode.meta.prompt).toBe('plot sales');

    const noCode = applySourceViewToCell({
      source: 'plot sales',
      meta: {},
      currentView: 'prompt',
      nextView: 'code'
    });
    expect(noCode.source).toBe('plot sales');
    expect(noCode.meta.prompt).toBe('plot sales');

    const toPrompt = applySourceViewToCell({
      source: 'print(2)',
      meta: { prompt: 'plot sales', generatedCode: 'print(1)' },
      currentView: 'code',
      nextView: 'prompt'
    });
    expect(toPrompt.source).toBe('plot sales');
    expect(toPrompt.meta.generatedCode).toBe('print(2)');
  });

  it('converts cells to python, commenting prompts without generated code', () => {
    const withCode = convertChatbookCellToPython({
      source: 'plot sales',
      meta: { generatedCode: 'print(1)' },
      currentView: 'prompt'
    });
    expect(withCode.source).toBe('print(1)');
    expect(withCode.meta.prompt).toBe('plot sales');

    const commented = convertChatbookCellToPython({
      source: 'plot sales\nby region',
      meta: {},
      currentView: 'prompt'
    });
    expect(commented.source).toBe('# plot sales\n# by region');

    expect(promptAsHashComment('')).toBe('# <empty Chatbook prompt>');
    expect(
      resolveChatbookPrompt('print(1)', { prompt: 'plot sales' }, 'code')
    ).toBe('plot sales');
    expect(
      getNotebookSourceView({ nbi: { chatbook: { sourceView: 'code' } } })
    ).toBe('code');
    expect(isChatbookConvertTargetId('python')).toBe(true);
    expect(isChatbookConvertTargetId('ruby')).toBe(false);
  });

  it('builds a python notebook copy without mutating the source', () => {
    const source = {
      nbformat: 4,
      cells: [
        { cell_type: 'markdown', source: '# hi', metadata: {} },
        {
          cell_type: 'code',
          source: ['plot sales'],
          metadata: { nbi: { chatbook: { generatedCode: 'print(1)' } } },
          outputs: []
        }
      ],
      metadata: {}
    };
    const out = buildPythonNotebookFromChatbook(source, {
      name: 'python3',
      display_name: 'Python 3',
      language: 'python'
    });
    expect((source.cells[1] as { source: string[] }).source).toEqual([
      'plot sales'
    ]);
    expect((out.cells as any)[0].source).toBe('# hi');
    expect((out.cells as any)[1].source).toBe('print(1)');
    expect((out.metadata as any).kernelspec.name).toBe('python3');
    expect(pythonExportNotebookPath('analysis.ipynb')).toBe(
      'analysis-python.ipynb'
    );
    expect(pythonExportNotebookPath('work/analysis.ipynb', 1)).toBe(
      'work/analysis-python-1.ipynb'
    );
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

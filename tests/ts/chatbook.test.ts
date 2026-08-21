// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  applySourceViewToCell,
  buildExecuteChatbookMeta,
  buildPythonNotebookFromChatbook,
  snapshotChatbookContextCell,
  splitNotebookContext,
  canRegenerateChatbookPrompt,
  convertChatbookCellToPython,
  getChatbookCellMeta,
  getChatbookCellMode,
  getChatbookCellOrigin,
  getNotebookSourceView,
  hasChatbookPrompt,
  isChatbookConvertTargetId,
  isChatbookKernelName,
  isChatbookPromptInlineCompletion,
  mergeChatbookCellMeta,
  mergeNotebookNuiSessionId,
  promptAsHashComment,
  pythonExportNotebookPath,
  resolveChatbookPrompt,
  sha256Hex,
  switchChatbookCellMode
} from '../../src/chatbook-core';

describe('chatbook-core', () => {
  it('recognizes the chatbook kernel name', () => {
    expect(isChatbookKernelName('chatbook')).toBe(true);
    expect(isChatbookKernelName('python3')).toBe(false);
    expect(isChatbookKernelName('')).toBe(false);
    expect(isChatbookPromptInlineCompletion('chatbook', 'prompt')).toBe(true);
    expect(isChatbookPromptInlineCompletion('chatbook', 'code')).toBe(false);
    expect(isChatbookPromptInlineCompletion('python3', 'prompt')).toBe(false);
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
    expect(getChatbookCellMode({})).toBe('prompt');
    expect(getChatbookCellMode({ mode: 'python' })).toBe('python');
  });

  it('switches between natural language and Python representations', () => {
    const python = switchChatbookCellMode({
      source: 'calculate a total',
      meta: { generatedCode: 'total = sum(values)' },
      sourceView: 'prompt',
      nextMode: 'python'
    });
    expect(python.source).toBe('total = sum(values)');
    expect(python.meta.mode).toBe('python');
    expect(python.meta.prompt).toBe('calculate a total');

    const prompt = switchChatbookCellMode({
      source: 'total = sum(values)',
      meta: {
        mode: 'python',
        prompt: 'calculate a total',
        pythonSource: 'total = sum(values)'
      },
      sourceView: 'prompt',
      nextMode: 'prompt'
    });
    expect(prompt.source).toBe('calculate a total');
    expect(prompt.meta.mode).toBe('prompt');
    expect(prompt.meta.generatedCode).toBe('total = sum(values)');
  });

  it('records the authoring input type and keeps it across switches', () => {
    const toPython = switchChatbookCellMode({
      source: 'calculate a total',
      meta: { generatedCode: 'total = sum(values)' },
      sourceView: 'prompt',
      nextMode: 'python'
    });
    expect(toPython.meta.origin).toBe('prompt');

    const backToPrompt = switchChatbookCellMode({
      source: toPython.source,
      meta: toPython.meta,
      sourceView: 'prompt',
      nextMode: 'prompt'
    });
    expect(backToPrompt.meta.origin).toBe('prompt');
    expect(backToPrompt.source).toBe('calculate a total');

    expect(getChatbookCellOrigin({})).toBe('prompt');
    expect(getChatbookCellOrigin({ mode: 'python' })).toBe('python');
    expect(getChatbookCellOrigin({ mode: 'prompt', origin: 'python' })).toBe(
      'python'
    );
  });

  it('only regenerates prompts it generated itself', () => {
    expect(hasChatbookPrompt({})).toBe(false);
    expect(hasChatbookPrompt({ prompt: '   ' })).toBe(false);
    expect(hasChatbookPrompt({ prompt: 'plot sales' })).toBe(true);

    // Nothing stored yet: a summary is the only way to get a prompt.
    expect(canRegenerateChatbookPrompt({ mode: 'python' })).toBe(true);
    // Written by the user, then switched to Python: keep it verbatim.
    expect(
      canRegenerateChatbookPrompt({
        mode: 'python',
        origin: 'prompt',
        prompt: 'plot sales'
      })
    ).toBe(false);
    // Previously summarized from Python: safe to refresh.
    expect(
      canRegenerateChatbookPrompt({
        mode: 'python',
        origin: 'python',
        prompt: 'sums the values',
        summarizedCodeHash: 'abc'
      })
    ).toBe(true);
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

  it('exports Python-authored cells without rewriting their source', () => {
    const source = {
      nbformat: 4,
      cells: [
        {
          cell_type: 'code',
          source: 'value = 42',
          metadata: {
            nbi: {
              chatbook: {
                mode: 'python',
                prompt: 'Set value to 42',
                pythonSource: 'value = 42'
              }
            }
          },
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
    expect((out.cells as any)[0].source).toBe('value = 42');
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

  it('marks direct Python execution without a codegen cache', () => {
    const meta = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: '',
      promptHash: '',
      executeMode: 'python',
      cellMeta: {
        mode: 'python',
        generatedCode: 'value = 42',
        promptHash: 'old'
      }
    });
    expect(meta.executeMode).toBe('python');
    expect(meta.cachedCode).toBeUndefined();
  });

  it('skips cache when notebook context hash changed', () => {
    const miss = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: 'plot',
      promptHash: 'aaa',
      contextHash: 'ctx-new',
      cellMeta: {
        generatedCode: 'x = 1',
        promptHash: 'aaa',
        contextHash: 'ctx-old'
      }
    });
    expect(miss.cachedCode).toBeUndefined();

    const hit = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: 'plot',
      promptHash: 'aaa',
      contextHash: 'ctx-same',
      cellMeta: {
        generatedCode: 'x = 1',
        promptHash: 'aaa',
        contextHash: 'ctx-same'
      }
    });
    expect(hit.cachedCode).toBe('x = 1');
  });

  it('splits notebook cells into prefix, cursor, and suffix', () => {
    const cells = [
      snapshotChatbookContextCell({
        index: 0,
        cellType: 'code',
        source: 'what is 2+2?',
        cellMeta: { generatedCode: 'print(4)' },
        sourceView: 'prompt',
        output: '4\n'
      }),
      snapshotChatbookContextCell({
        index: 1,
        cellType: 'code',
        source: 'what did I ask?',
        cellMeta: {},
        sourceView: 'prompt'
      }),
      snapshotChatbookContextCell({
        index: 2,
        cellType: 'markdown',
        source: '# notes',
        cellMeta: {},
        sourceView: 'prompt'
      })
    ];
    const ctx = splitNotebookContext(cells, 1);
    expect(ctx.prefix).toHaveLength(1);
    expect(ctx.prefix[0].prompt).toBe('what is 2+2?');
    expect(ctx.prefix[0].generatedCode).toBe('print(4)');
    expect(ctx.prefix[0].output).toBe('4\n');
    expect(ctx.current.prompt).toBe('what did I ask?');
    expect(ctx.suffix).toHaveLength(1);
    expect(ctx.suffix[0].source).toBe('# notes');
  });

  it('hashes prompts with sha-256', async () => {
    const digest = await sha256Hex('hello');
    expect(digest).toBe(
      '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    );
  });
});

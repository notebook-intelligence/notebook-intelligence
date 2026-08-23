// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  buildExecuteChatbookMeta,
  buildPythonNotebookFromChatbook,
  snapshotChatbookContextCell,
  splitNotebookContext,
  canSwitchChatbookCellMode,
  convertChatbookCellToPython,
  getChatbookCellMeta,
  getChatbookCellMode,
  getChatbookCellOrigin,
  hasChatbookPrompt,
  hasLegacyChatbookCodeView,
  isChatbookConvertTargetId,
  isChatbookKernelName,
  isChatbookPromptInlineCompletion,
  mergeChatbookCellMeta,
  mergeNotebookNuiSessionId,
  promptAsHashComment,
  pythonExportNotebookPath,
  resolveChatbookPrompt,
  sha256Hex,
  switchChatbookCellMode,
  withoutLegacyChatbookSourceView,
  clampChatbookExecutionMode,
  chatbookCanConfirmRun,
  chatbookExecutionModeSummary,
  chatbookNeedsConfirm,
  parseChatbookExecutionMode,
  CHATBOOK_EXECUTION_MODES
} from '../../src/chatbook-core';

describe('chatbook-core', () => {
  it('recognizes the chatbook kernel name', () => {
    expect(isChatbookKernelName('chatbook')).toBe(true);
    expect(isChatbookKernelName('python3')).toBe(false);
    expect(isChatbookKernelName('')).toBe(false);
    expect(isChatbookPromptInlineCompletion('chatbook', 'prompt')).toBe(true);
    expect(isChatbookPromptInlineCompletion('chatbook', 'python')).toBe(false);
    expect(isChatbookPromptInlineCompletion('python3', 'prompt')).toBe(false);
  });

  it('lets a cell switch modes whether or not it has Python yet', () => {
    // No code generated yet: the Python side is simply empty.
    expect(canSwitchChatbookCellMode({}, 'python')).toBe(true);
    expect(
      canSwitchChatbookCellMode({ generatedCode: 'print(1)' }, 'python')
    ).toBe(true);
    expect(canSwitchChatbookCellMode({ mode: 'python' }, 'prompt')).toBe(true);
    // Already in the requested mode.
    expect(canSwitchChatbookCellMode({ mode: 'python' }, 'python')).toBe(false);
    expect(canSwitchChatbookCellMode({}, 'prompt')).toBe(false);
  });

  it('shows an empty editor for a side the cell does not have yet', () => {
    const toPython = switchChatbookCellMode({
      source: 'calculate a total',
      meta: {},
      nextMode: 'python'
    });
    expect(toPython.source).toBe('');
    expect(toPython.meta.mode).toBe('python');
    // The prompt is kept so switching back restores what the user wrote.
    expect(toPython.meta.prompt).toBe('calculate a total');

    // Switching never generates, so a Python cell with no English shows none.
    const toPrompt = switchChatbookCellMode({
      source: 'total = sum(values)',
      meta: { mode: 'python' },
      nextMode: 'prompt'
    });
    expect(toPrompt.source).toBe('');
    expect(toPrompt.meta.generatedCode).toBe('total = sum(values)');
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
      nextMode: 'prompt'
    });
    expect(prompt.source).toBe('calculate a total');
    expect(prompt.meta.mode).toBe('prompt');
    expect(prompt.meta.generatedCode).toBe('total = sum(values)');
  });

  it('round-trips a cell through both modes without losing either side', () => {
    const original = { source: 'calculate a total', meta: {} };
    const generated = {
      source: original.source,
      meta: { ...original.meta, generatedCode: 'total = sum(values)' }
    };

    const toPython = switchChatbookCellMode({
      ...generated,
      nextMode: 'python'
    });
    const backToPrompt = switchChatbookCellMode({
      source: toPython.source,
      meta: toPython.meta,
      nextMode: 'prompt'
    });
    expect(backToPrompt.source).toBe('calculate a total');

    const toPythonAgain = switchChatbookCellMode({
      source: backToPrompt.source,
      meta: backToPrompt.meta,
      nextMode: 'python'
    });
    expect(toPythonAgain.source).toBe('total = sum(values)');
    expect(toPythonAgain.meta.mode).toBe('python');
  });

  it('records the authoring input type and keeps it across switches', () => {
    const toPython = switchChatbookCellMode({
      source: 'calculate a total',
      meta: { generatedCode: 'total = sum(values)' },
      nextMode: 'python'
    });
    expect(toPython.meta.origin).toBe('prompt');

    const backToPrompt = switchChatbookCellMode({
      source: toPython.source,
      meta: toPython.meta,
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

  it('tells an empty prompt from one worth keeping', () => {
    expect(hasChatbookPrompt({})).toBe(false);
    expect(hasChatbookPrompt({ prompt: '   ' })).toBe(false);
    expect(hasChatbookPrompt({ prompt: 'plot sales' })).toBe(true);
  });

  it('treats an undefined patch field as a removal', () => {
    const merged = mergeChatbookCellMeta(
      {
        nbi: { chatbook: { prompt: 'plot sales', summaryError: 'timed out' } }
      },
      { summaryError: undefined }
    );
    const chatbook = (merged.nbi as any).chatbook;
    expect('summaryError' in chatbook).toBe(false);
    expect(chatbook.prompt).toBe('plot sales');
  });

  it('stores nui session id on the notebook', () => {
    const merged = mergeNotebookNuiSessionId({}, 'sess-1');
    expect((merged.nbi as any).chatbook.nuiSessionId).toBe('sess-1');
  });

  it('drops the notebook-wide source view left by older notebooks', () => {
    const legacy = { nbi: { chatbook: { sourceView: 'code' } } };
    expect(hasLegacyChatbookCodeView(legacy)).toBe(true);
    expect(hasLegacyChatbookCodeView({ nbi: { chatbook: {} } })).toBe(false);
    expect(hasLegacyChatbookCodeView({})).toBe(false);

    const cleaned = withoutLegacyChatbookSourceView(legacy);
    expect((cleaned.nbi as any).chatbook.sourceView).toBeUndefined();
    // The original is left untouched for callers holding on to it.
    expect(legacy.nbi.chatbook.sourceView).toBe('code');
  });

  it('converts cells to python, commenting prompts without generated code', () => {
    const withCode = convertChatbookCellToPython({
      source: 'plot sales',
      meta: { generatedCode: 'print(1)' }
    });
    expect(withCode.source).toBe('print(1)');
    expect(withCode.meta.prompt).toBe('plot sales');

    const commented = convertChatbookCellToPython({
      source: 'plot sales\nby region',
      meta: {}
    });
    expect(commented.source).toBe('# plot sales\n# by region');

    expect(promptAsHashComment('')).toBe('# <empty Chatbook prompt>');
    expect(resolveChatbookPrompt('plot sales', {})).toBe('plot sales');
    expect(
      resolveChatbookPrompt('print(1)', {
        mode: 'python',
        prompt: 'plot sales'
      })
    ).toBe('plot sales');
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

    const dynamic = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: 'plot',
      promptHash: 'aaa',
      notebookPath: 'reports/analysis.ipynb',
      allowCachedCode: false,
      cellMeta: { generatedCode: 'x = 1', promptHash: 'aaa' }
    });
    expect(dynamic.cachedCode).toBeUndefined();
    expect(dynamic.notebookPath).toBe('reports/analysis.ipynb');
  });

  it('marks direct Python execution without a codegen cache', () => {
    const meta = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: '',
      promptHash: '',
      executeMode: 'python',
      pythonSource: 'value = 42',
      cellMeta: {
        mode: 'python',
        generatedCode: 'value = 42',
        promptHash: 'old'
      }
    });
    expect(meta.executeMode).toBe('python');
    expect(meta.pythonSource).toBe('value = 42');
    expect(meta.cachedCode).toBeUndefined();
  });

  it('reuses code for an unchanged prompt even as notebook context moves', () => {
    // Running the cell changes its own output, and so the context hash. That
    // must not count as a reason to regenerate.
    const hit = buildExecuteChatbookMeta({
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
    expect(hit.cachedCode).toBe('x = 1');
    expect(hit.contextHash).toBe('ctx-new');
  });

  it('splits notebook cells into prefix, cursor, and suffix', () => {
    const cells = [
      snapshotChatbookContextCell({
        index: 0,
        cellType: 'code',
        source: 'what is 2+2?',
        cellMeta: { generatedCode: 'print(4)' },
        output: '4\n'
      }),
      snapshotChatbookContextCell({
        index: 1,
        cellType: 'code',
        source: 'what did I ask?',
        cellMeta: {}
      }),
      snapshotChatbookContextCell({
        index: 2,
        cellType: 'markdown',
        source: '# notes',
        cellMeta: {}
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

  it('clamps execution modes and decides when to confirm', () => {
    expect(parseChatbookExecutionMode('nope')).toBe('always-confirm');
    expect(clampChatbookExecutionMode('auto-run', 'always-confirm')).toBe(
      'always-confirm'
    );
    expect(chatbookCanConfirmRun('generate-only')).toBe(false);
    expect(chatbookNeedsConfirm('always-confirm', 'clean')).toBe(true);
    expect(chatbookNeedsConfirm('confirm-if-risky', 'clean')).toBe(false);
    expect(chatbookNeedsConfirm('confirm-if-risky', 'risky')).toBe(true);
    expect(
      chatbookNeedsConfirm('always-confirm', 'risky', {
        alreadyExecutedThisSession: true
      })
    ).toBe(false);
    expect(
      chatbookNeedsConfirm('generate-only', 'clean', {
        alreadyExecutedThisSession: true
      })
    ).toBe(true);
  });

  it('sends the NL execution policy with generate metadata', () => {
    const meta = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: 'plot',
      promptHash: 'aaa',
      executionPolicy: 'always-confirm',
      llmDangerScan: true,
      cellMeta: {}
    });
    expect(meta.executionPolicy).toBe('always-confirm');
    expect(meta.llmDangerScan).toBe(true);
    expect(meta.executeMode).toBe('prompt');
  });

  it('summarizes every execution mode for the confirm bar', () => {
    for (const mode of CHATBOOK_EXECUTION_MODES) {
      expect(chatbookExecutionModeSummary(mode)).toMatch(
        /^Chatbook is set to /
      );
    }
    expect(chatbookExecutionModeSummary('confirm-if-risky')).toContain('risk');
  });
});

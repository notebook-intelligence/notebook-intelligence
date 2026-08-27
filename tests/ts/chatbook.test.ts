// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  buildCodeNotebookFromChatbook,
  buildExecuteChatbookMeta,
  snapshotChatbookContextCell,
  splitNotebookContext,
  canSwitchChatbookCellMode,
  convertChatbookCellToCode,
  getChatbookCellMeta,
  getChatbookCellMode,
  getChatbookCellOrigin,
  hasChatbookPrompt,
  isChatbookKernelName,
  isChatbookPromptInlineCompletion,
  mergeChatbookCellMeta,
  promptAsHashComment,
  chatbookExportNotebookPath,
  resolveChatbookPrompt,
  sha256Hex,
  switchChatbookCellMode,
  clampChatbookExecutionMode,
  chatbookExecutionModeSummary,
  chatbookNeedsConfirm,
  parseChatbookExecutionMode,
  CHATBOOK_EXECUTION_MODES
} from '../../src/chatbook-core';
import {
  mimeTypeForNotebookLanguage,
  resolveChatbookBackendProfile
} from '../../src/notebook-kernels';
import { NBIConfig } from '../../src/api';

describe('chatbook-core', () => {
  it('recognizes the chatbook kernel name', () => {
    expect(isChatbookKernelName('chatbook')).toBe(true);
    expect(isChatbookKernelName('python3')).toBe(false);
    expect(isChatbookKernelName('')).toBe(false);
    expect(isChatbookPromptInlineCompletion('chatbook', 'prompt')).toBe(true);
    expect(isChatbookPromptInlineCompletion('chatbook', 'code')).toBe(false);
    expect(isChatbookPromptInlineCompletion('python3', 'prompt')).toBe(false);
  });

  it('lets a cell switch modes whether or not it has code yet', () => {
    // Nothing generated yet: the code side is simply empty.
    expect(canSwitchChatbookCellMode({}, 'code')).toBe(true);
    expect(
      canSwitchChatbookCellMode({ generatedCode: 'print(1)' }, 'code')
    ).toBe(true);
    expect(canSwitchChatbookCellMode({ mode: 'code' }, 'prompt')).toBe(true);
    // Already in the requested mode.
    expect(canSwitchChatbookCellMode({ mode: 'code' }, 'code')).toBe(false);
    expect(canSwitchChatbookCellMode({}, 'prompt')).toBe(false);
  });

  it('shows an empty editor for a side the cell does not have yet', () => {
    const toCode = switchChatbookCellMode({
      source: 'calculate a total',
      meta: {},
      nextMode: 'code'
    });
    expect(toCode.source).toBe('');
    expect(toCode.meta.mode).toBe('code');
    // The prompt is kept so switching back restores what the user wrote.
    expect(toCode.meta.prompt).toBe('calculate a total');

    // Switching never generates, so a code cell with no English shows none.
    const toPrompt = switchChatbookCellMode({
      source: 'total = sum(values)',
      meta: { mode: 'code' },
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
    expect(getChatbookCellMode({ mode: 'code' })).toBe('code');
  });

  it('switches between natural language and code representations', () => {
    const code = switchChatbookCellMode({
      source: 'calculate a total',
      meta: { generatedCode: 'total = sum(values)' },
      nextMode: 'code'
    });
    expect(code.source).toBe('total = sum(values)');
    expect(code.meta.mode).toBe('code');
    expect(code.meta.prompt).toBe('calculate a total');

    const prompt = switchChatbookCellMode({
      source: 'total = sum(values)',
      meta: {
        mode: 'code',
        prompt: 'calculate a total',
        codeSource: 'total = sum(values)'
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

    const toCode = switchChatbookCellMode({
      ...generated,
      nextMode: 'code'
    });
    const backToPrompt = switchChatbookCellMode({
      source: toCode.source,
      meta: toCode.meta,
      nextMode: 'prompt'
    });
    expect(backToPrompt.source).toBe('calculate a total');

    const toCodeAgain = switchChatbookCellMode({
      source: backToPrompt.source,
      meta: backToPrompt.meta,
      nextMode: 'code'
    });
    expect(toCodeAgain.source).toBe('total = sum(values)');
    expect(toCodeAgain.meta.mode).toBe('code');
  });

  it('records the authoring input type and keeps it across switches', () => {
    const toCode = switchChatbookCellMode({
      source: 'calculate a total',
      meta: { generatedCode: 'total = sum(values)' },
      nextMode: 'code'
    });
    expect(toCode.meta.origin).toBe('prompt');

    const backToPrompt = switchChatbookCellMode({
      source: toCode.source,
      meta: toCode.meta,
      nextMode: 'prompt'
    });
    expect(backToPrompt.meta.origin).toBe('prompt');
    expect(backToPrompt.source).toBe('calculate a total');

    expect(getChatbookCellOrigin({})).toBe('prompt');
    expect(getChatbookCellOrigin({ mode: 'code' })).toBe('code');
    expect(getChatbookCellOrigin({ mode: 'prompt', origin: 'code' })).toBe(
      'code'
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

  it('converts cells to code, commenting prompts without generated code', async () => {
    const promptHash = await sha256Hex('plot sales');
    const withCode = await convertChatbookCellToCode({
      source: 'plot sales',
      meta: {
        prompt: 'plot sales',
        promptHash,
        generatedCode: 'print(1)'
      }
    });
    expect(withCode.source).toBe('print(1)');
    expect(withCode.meta.prompt).toBe('plot sales');

    const commented = await convertChatbookCellToCode({
      source: 'plot sales\nby region',
      meta: {}
    });
    expect(commented.source).toBe('# plot sales\n# by region');

    expect(promptAsHashComment('')).toBe('# <empty Chatbook prompt>');
    expect(promptAsHashComment('plot sales', 'javascript')).toBe(
      '// plot sales'
    );
    expect(promptAsHashComment('plot sales', 'C++17')).toBe('// plot sales');
    expect(() => promptAsHashComment('plot sales', 'unknown-lang')).toThrow(
      'no safe line comment'
    );
    expect(promptAsHashComment('plot sales\rprint(1)')).toBe(
      '# plot sales\n# print(1)'
    );
    expect(resolveChatbookPrompt('plot sales', {})).toBe('plot sales');
    expect(
      resolveChatbookPrompt('print(1)', {
        mode: 'code',
        prompt: 'plot sales'
      })
    ).toBe('plot sales');
  });

  it('builds a code notebook copy without mutating the source', async () => {
    const promptHash = await sha256Hex('plot sales');
    const source = {
      nbformat: 4,
      cells: [
        { cell_type: 'markdown', source: '# hi', metadata: {} },
        {
          cell_type: 'code',
          source: ['plot sales'],
          metadata: {
            nbi: {
              chatbook: {
                prompt: 'plot sales',
                promptHash,
                generatedCode: 'print(1)'
              }
            }
          },
          outputs: [] as unknown[]
        }
      ],
      metadata: {}
    };
    const out = await buildCodeNotebookFromChatbook(source, {
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
    const js = await buildCodeNotebookFromChatbook(
      {
        nbformat: 4,
        cells: [
          {
            cell_type: 'code',
            source: 'plot the sales by region',
            metadata: {},
            outputs: [] as unknown[]
          }
        ],
        metadata: {}
      },
      {
        name: 'javascript',
        display_name: 'JavaScript',
        language: 'javascript'
      }
    );
    expect((js.cells as any)[0].source).toBe('// plot the sales by region');
    expect(chatbookExportNotebookPath('analysis.ipynb', 'python')).toBe(
      'analysis-python.ipynb'
    );
    expect(chatbookExportNotebookPath('work/analysis.ipynb', 'python', 1)).toBe(
      'work/analysis-python-1.ipynb'
    );
    expect(chatbookExportNotebookPath('analysis.ipynb', 'R')).toBe(
      'analysis-r.ipynb'
    );
  });

  it('comments an edited prompt instead of exporting stale generated code', async () => {
    const oldPromptHash = await sha256Hex('show me the first 5 rows');
    const converted = await convertChatbookCellToCode({
      source: 'drop the customers table',
      meta: {
        prompt: 'show me the first 5 rows',
        promptHash: oldPromptHash,
        generatedCode: 'df.head(5)'
      }
    });
    expect(converted.source).toBe('# drop the customers table');
    expect(converted.meta.prompt).toBe('drop the customers table');
    expect(converted.meta.generatedCode).toBeUndefined();
    expect(converted.meta.promptHash).toBeUndefined();
  });

  it('exports code-authored cells without rewriting their source', async () => {
    const source = {
      nbformat: 4,
      cells: [
        {
          cell_type: 'code',
          source: 'value = 42',
          metadata: {
            nbi: {
              chatbook: {
                mode: 'code',
                prompt: 'Set value to 42',
                codeSource: 'value = 42'
              }
            }
          },
          outputs: [] as unknown[]
        }
      ],
      metadata: {}
    };
    const out = await buildCodeNotebookFromChatbook(source, {
      name: 'python3',
      display_name: 'Python 3',
      language: 'python'
    });
    expect((out.cells as any)[0].source).toBe('value = 42');
  });

  it('passes cachedCode only when this session opts in and the hash matches', () => {
    const hit = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: 'plot',
      promptHash: 'aaa',
      allowCachedCode: true,
      cellMeta: { generatedCode: 'x = 1', promptHash: 'aaa' }
    });
    expect(hit.cachedCode).toBe('x = 1');

    const fromDisk = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: 'plot',
      promptHash: 'aaa',
      cellMeta: { generatedCode: 'x = 1', promptHash: 'aaa' }
    });
    expect(fromDisk.cachedCode).toBeUndefined();

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

  it('marks direct code execution without a codegen cache', () => {
    const meta = buildExecuteChatbookMeta({
      cellId: 'c1',
      prompt: '',
      promptHash: '',
      executeMode: 'code',
      codeSource: 'value = 42',
      cellMeta: {
        mode: 'code',
        generatedCode: 'value = 42',
        promptHash: 'old'
      }
    });
    expect(meta.executeMode).toBe('code');
    expect(meta.codeSource).toBe('value = 42');
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
      allowCachedCode: true,
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
    expect(parseChatbookExecutionMode('generate-only')).toBe('always-confirm');
    expect(clampChatbookExecutionMode('auto-run', 'generate-only')).toBe(
      'always-confirm'
    );
    expect(clampChatbookExecutionMode('auto-run', 'always-confirm')).toBe(
      'always-confirm'
    );
    expect(chatbookNeedsConfirm('always-confirm', 'clean')).toBe(true);
    expect(chatbookNeedsConfirm('confirm-if-risky', 'clean')).toBe(false);
    expect(chatbookNeedsConfirm('confirm-if-risky', 'risky')).toBe(true);
    expect(
      chatbookNeedsConfirm('always-confirm', 'risky', {
        alreadyExecutedThisSession: true
      })
    ).toBe(false);
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

  it('resolves a Chatbook backend kernelspec and MIME type', () => {
    const specs = {
      chatbook: {
        name: 'chatbook',
        language: 'chatbook',
        display_name: 'Chatbook'
      },
      python3: {
        name: 'python3',
        language: 'python',
        display_name: 'Python 3'
      },
      ir: { name: 'ir', language: 'R', display_name: 'R' }
    } as any;
    expect(resolveChatbookBackendProfile(specs, 'ir').kernelName).toBe('ir');
    expect(resolveChatbookBackendProfile(specs, 'chatbook').kernelName).toBe(
      'python3'
    );
    expect(mimeTypeForNotebookLanguage('R')).toBe('text/x-rsrc');
    expect(mimeTypeForNotebookLanguage('python')).toBe('text/x-python');
  });
});

describe('NBIConfig.chatbookEnabled', () => {
  it('defaults on when capabilities omit the flag', () => {
    const config = new NBIConfig();
    expect(config.chatbookEnabled).toBe(true);
  });

  it('is false only when capabilities explicitly disable Chatbook', () => {
    const config = new NBIConfig();
    config.capabilities = { chatbook_enabled: false };
    expect(config.chatbookEnabled).toBe(false);
    config.capabilities = { chatbook_enabled: true };
    expect(config.chatbookEnabled).toBe(true);
  });
});

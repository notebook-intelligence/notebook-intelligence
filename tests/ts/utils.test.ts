// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  removeAnsiChars,
  moveCodeSectionBoundaryMarkersToNewLine,
  extractLLMGeneratedCode,
  markdownToComment,
  compareSelectionPoints,
  compareSelections,
  isSelectionEmpty,
  isDarkTheme,
  getTokenCount,
  cellOutputAsText,
  applyCodeToSelectionInEditor
} from '../../src/utils';

describe('removeAnsiChars', () => {
  it('strips colour escape sequences', () => {
    const colored = '\u001b[31merror\u001b[0m: oops';
    expect(removeAnsiChars(colored)).toBe('error: oops');
  });

  it('strips cursor-control escape sequences', () => {
    expect(removeAnsiChars('hi\u001b[2Athere')).toBe('hithere');
  });

  it('returns plain strings unchanged', () => {
    expect(removeAnsiChars('plain text')).toBe('plain text');
  });

  it('handles empty input', () => {
    expect(removeAnsiChars('')).toBe('');
  });
});

describe('moveCodeSectionBoundaryMarkersToNewLine', () => {
  it('splits an opening fence that has trailing content', () => {
    const input = '```pythonprint("hi")';
    expect(moveCodeSectionBoundaryMarkersToNewLine(input)).toBe(
      '```\nprint("hi")'
    );
  });

  it('splits a fence that opens and closes on a single line', () => {
    const input = '```pythonprint("hi")```';
    expect(moveCodeSectionBoundaryMarkersToNewLine(input)).toBe(
      '```\nprint("hi")\n```'
    );
  });

  it('drops a redundant language tag when nothing follows it', () => {
    expect(moveCodeSectionBoundaryMarkersToNewLine('```python')).toBe('```');
  });

  it('moves a trailing fence onto its own line', () => {
    const input = 'print("hi")```';
    expect(moveCodeSectionBoundaryMarkersToNewLine(input)).toBe(
      'print("hi")\n```'
    );
  });

  it('strips a redundant python language tag from a well-formed fence', () => {
    const input = '```python\nprint("hi")\n```';
    expect(moveCodeSectionBoundaryMarkersToNewLine(input)).toBe(
      '```\nprint("hi")\n```'
    );
  });
});

describe('extractLLMGeneratedCode', () => {
  it('extracts the body between matched fences', () => {
    const wrapped = '```python\nprint("hi")\n```';
    expect(extractLLMGeneratedCode(wrapped)).toBe('print("hi")\n');
  });

  it('extracts the body when only an opening fence is present', () => {
    const wrapped = '```python\nprint("hi")\nmore code';
    expect(extractLLMGeneratedCode(wrapped)).toBe('print("hi")\nmore code');
  });

  it('strips a trailing fence even without an opening fence', () => {
    expect(extractLLMGeneratedCode('print("hi")```')).toBe('print("hi")');
  });

  it('passes plain code through unchanged', () => {
    expect(extractLLMGeneratedCode('print("hi")')).toBe('print("hi")');
  });

  it('passes single-line input through unchanged', () => {
    expect(extractLLMGeneratedCode('one line')).toBe('one line');
  });

  it('tolerates leading whitespace before the fence', () => {
    const wrapped = '   ```\nprint("hi")\n```';
    expect(extractLLMGeneratedCode(wrapped)).toBe('print("hi")\n');
  });
});

describe('markdownToComment', () => {
  it('prefixes every line with "# "', () => {
    expect(markdownToComment('one\ntwo')).toBe('# one\n# two');
  });

  it('prefixes blank lines too', () => {
    expect(markdownToComment('one\n\ntwo')).toBe('# one\n# \n# two');
  });

  it('handles a single-line input', () => {
    expect(markdownToComment('hello')).toBe('# hello');
  });
});

describe('compareSelectionPoints', () => {
  it('returns true when both line and column match', () => {
    expect(
      compareSelectionPoints({ line: 1, column: 2 }, { line: 1, column: 2 })
    ).toBe(true);
  });

  it('returns false when lines differ', () => {
    expect(
      compareSelectionPoints({ line: 1, column: 2 }, { line: 2, column: 2 })
    ).toBe(false);
  });

  it('returns false when columns differ', () => {
    expect(
      compareSelectionPoints({ line: 1, column: 2 }, { line: 1, column: 3 })
    ).toBe(false);
  });
});

describe('compareSelections', () => {
  const range = (sl: number, sc: number, el: number, ec: number) => ({
    start: { line: sl, column: sc },
    end: { line: el, column: ec }
  });

  it('returns true for two selections with matching endpoints', () => {
    expect(compareSelections(range(0, 0, 1, 2), range(0, 0, 1, 2))).toBe(true);
  });

  it('returns false when endpoints differ', () => {
    expect(compareSelections(range(0, 0, 1, 2), range(0, 0, 1, 3))).toBe(false);
  });

  it('returns true when both selections are undefined', () => {
    expect(compareSelections(undefined as any, undefined as any)).toBe(true);
  });

  it('returns true when exactly one side is undefined', () => {
    expect(compareSelections(undefined as any, range(0, 0, 1, 2))).toBe(true);
    expect(compareSelections(range(0, 0, 1, 2), undefined as any)).toBe(true);
  });
});

describe('isSelectionEmpty', () => {
  it('returns true for a zero-length selection', () => {
    expect(
      isSelectionEmpty({
        start: { line: 3, column: 5 },
        end: { line: 3, column: 5 }
      })
    ).toBe(true);
  });

  it('returns false when the selection spans columns', () => {
    expect(
      isSelectionEmpty({
        start: { line: 3, column: 5 },
        end: { line: 3, column: 9 }
      })
    ).toBe(false);
  });

  it('returns false when the selection spans lines', () => {
    expect(
      isSelectionEmpty({
        start: { line: 3, column: 5 },
        end: { line: 4, column: 5 }
      })
    ).toBe(false);
  });
});

describe('applyCodeToSelectionInEditor', () => {
  const makeEditor = (dispatch?: jest.Mock) => {
    const updateSource = jest.fn();
    const setCursorPosition = jest.fn();
    const editor: any = {
      getSelection: jest.fn(() => ({
        start: { line: 0, column: 1 },
        end: { line: 0, column: 4 }
      })),
      getOffsetAt: jest.fn(position => position.column),
      getPositionAt: jest.fn(offset => ({ line: 0, column: offset })),
      lineCount: 1,
      getLine: jest.fn(() => 'abcXYZdef'),
      setCursorPosition,
      model: {
        sharedModel: {
          updateSource
        }
      }
    };
    if (dispatch) {
      editor.editor = { dispatch };
    }
    return { editor, updateSource, setCursorPosition };
  };

  it('uses CodeMirror dispatch when available so edits enter the undo path', () => {
    const dispatch = jest.fn();
    const { editor, updateSource, setCursorPosition } = makeEditor(dispatch);

    applyCodeToSelectionInEditor(editor, 'XYZ');

    expect(dispatch).toHaveBeenCalledWith({
      changes: { from: 1, to: 4, insert: 'XYZ' },
      selection: { anchor: 4 },
      scrollIntoView: true
    });
    expect(updateSource).not.toHaveBeenCalled();
    expect(setCursorPosition).toHaveBeenCalledWith({
      line: 0,
      column: 'abcXYZdef'.length
    });
  });

  it('falls back to shared model updates for non-CodeMirror editors', () => {
    const { editor, updateSource } = makeEditor();

    applyCodeToSelectionInEditor(editor, 'XYZ');

    expect(updateSource).toHaveBeenCalledWith(1, 4, 'XYZ');
  });
});

describe('isDarkTheme', () => {
  afterEach(() => {
    document.body.removeAttribute('data-jp-theme-light');
  });

  it('returns true when JupyterLab marks the theme as not light', () => {
    document.body.setAttribute('data-jp-theme-light', 'false');
    expect(isDarkTheme()).toBe(true);
  });

  it('returns false when the theme is light', () => {
    document.body.setAttribute('data-jp-theme-light', 'true');
    expect(isDarkTheme()).toBe(false);
  });

  it('returns false when the attribute is missing', () => {
    expect(isDarkTheme()).toBe(false);
  });
});

describe('getTokenCount', () => {
  it('returns 0 for empty input', () => {
    expect(getTokenCount('')).toBe(0);
  });

  it('returns a positive count that grows with input length', () => {
    const shorter = getTokenCount('one two');
    const longer = getTokenCount('one two three four five');
    expect(shorter).toBeGreaterThan(0);
    expect(longer).toBeGreaterThan(shorter);
  });
});

describe('cellOutputAsText', () => {
  // cellOutputAsText only touches `cell.outputArea.model.toJSON()`, so a
  // duck-typed stub avoids pulling in the JupyterLab cell widget machinery.
  const makeCell = (outputs: any[]) =>
    ({
      outputArea: { model: { toJSON: () => outputs } }
    }) as any;

  it('returns the empty string for a cell with no outputs', () => {
    expect(cellOutputAsText(makeCell([]))).toBe('');
  });

  it('renders execute_result text/plain payloads', () => {
    const cell = makeCell([
      {
        output_type: 'execute_result',
        data: { 'text/plain': '42' }
      }
    ]);
    expect(cellOutputAsText(cell)).toBe('42');
  });

  it('renders stream output with a trailing newline', () => {
    const cell = makeCell([{ output_type: 'stream', text: 'hello world' }]);
    expect(cellOutputAsText(cell)).toBe('hello world\n');
  });

  it('renders error output with name, value, and ansi-stripped traceback', () => {
    const cell = makeCell([
      {
        output_type: 'error',
        ename: 'ValueError',
        evalue: 'bad input',
        traceback: ['\u001b[31mTraceback line 1\u001b[0m', 'Traceback line 2']
      }
    ]);
    expect(cellOutputAsText(cell)).toBe(
      'ValueError: bad input\nTraceback line 1\nTraceback line 2\n'
    );
  });

  it('skips error output when traceback is missing', () => {
    const cell = makeCell([
      {
        output_type: 'error',
        ename: 'ValueError',
        evalue: 'bad input',
        traceback: undefined
      }
    ]);
    expect(cellOutputAsText(cell)).toBe('');
  });

  it('concatenates outputs of mixed types', () => {
    const cell = makeCell([
      { output_type: 'stream', text: 'first' },
      {
        output_type: 'execute_result',
        data: { 'text/plain': 'second' }
      }
    ]);
    expect(cellOutputAsText(cell)).toBe('first\nsecond');
  });
});

// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  captureInlineCompletionOrigin,
  isInlineCompletionOriginCurrent
} from '../../src/inline-completion-origin';

function makeCell(id: string, source: string) {
  let text = source;
  return {
    model: {
      id,
      sharedModel: {
        getSource: () => text,
        setSource: (value: string) => {
          text = value;
        }
      }
    }
  };
}

function makeNotebook(activeCell: ReturnType<typeof makeCell> | null) {
  return {
    isDisposed: false,
    content: { activeCell }
  };
}

describe('inline completion origin', () => {
  it('keeps a reply for the cell it was requested for', () => {
    const cell = makeCell('cell-1', 'hi');
    const notebook = makeNotebook(cell);

    const origin = captureInlineCompletionOrigin(notebook, 'hi');

    expect(isInlineCompletionOriginCurrent(origin)).toBe(true);
  });

  it('drops a reply that arrives after focus moved to another cell', () => {
    const cell = makeCell('cell-1', 'hi');
    const notebook = makeNotebook(cell);
    const origin = captureInlineCompletionOrigin(notebook, 'hi');

    // Running a cell inserts and activates the next one below it.
    notebook.content.activeCell = makeCell('cell-2', '');

    expect(isInlineCompletionOriginCurrent(origin)).toBe(false);
  });

  it('drops a reply whose cell text changed while the request was in flight', () => {
    const cell = makeCell('cell-1', 'hi');
    const notebook = makeNotebook(cell);
    const origin = captureInlineCompletionOrigin(notebook, 'hi');

    cell.model.sharedModel.setSource('print(1)');

    expect(isInlineCompletionOriginCurrent(origin)).toBe(false);
  });

  it('drops a reply for a notebook that is gone or has no active cell', () => {
    const cell = makeCell('cell-1', 'hi');
    const disposed = makeNotebook(cell);
    const disposedOrigin = captureInlineCompletionOrigin(disposed, 'hi');
    disposed.isDisposed = true;
    expect(isInlineCompletionOriginCurrent(disposedOrigin)).toBe(false);

    const emptied = makeNotebook(cell);
    const emptiedOrigin = captureInlineCompletionOrigin(emptied, 'hi');
    emptied.content.activeCell = null;
    expect(isInlineCompletionOriginCurrent(emptiedOrigin)).toBe(false);
  });

  it('leaves non-notebook editors unguarded', () => {
    const origin = captureInlineCompletionOrigin(null, 'hi');

    expect(origin.cellId).toBeNull();
    expect(isInlineCompletionOriginCurrent(origin)).toBe(true);
  });
});

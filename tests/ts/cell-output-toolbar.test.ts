// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Stubs replace the JupyterLab modules so the widget's `instanceof`
// checks resolve against test-controlled classes; the real packages pull
// in DOM- and signal-heavy dependencies that don't initialize cleanly
// under jsdom.
class FakeNotebookPanel {
  content: { widgets: any[]; activeCellIndex: number };
  constructor(widgets: any[]) {
    this.content = { widgets, activeCellIndex: -1 };
  }
}

class FakeCodeCell {
  node: HTMLElement;
  outputArea: { model: { toJSON: () => any[] } };
  constructor(node: HTMLElement, outputs: any[] = []) {
    this.node = node;
    this.outputArea = { model: { toJSON: () => outputs } };
  }
}

jest.mock('@jupyterlab/application', () => ({}), { virtual: true });
jest.mock('@jupyterlab/cells', () => ({ CodeCell: FakeCodeCell }), {
  virtual: true
});
jest.mock(
  '@jupyterlab/notebook',
  () => ({ NotebookPanel: FakeNotebookPanel }),
  { virtual: true }
);

const featuresState = {
  explain_error: { enabled: true, locked: false },
  output_followup: { enabled: true, locked: false },
  output_toolbar: { enabled: true, locked: false }
};
jest.mock('../../src/api', () => ({
  NBIAPI: {
    config: {
      get cellOutputFeatures() {
        return featuresState;
      }
    }
  }
}));

import { CellOutputHoverToolbar } from '../../src/cell-output-toolbar';

interface IBuiltCell {
  cellNode: HTMLElement;
  outputArea: HTMLElement;
  cell: FakeCodeCell;
}

const buildCell = (outputs: any[] = []): IBuiltCell => {
  const cellNode = document.createElement('div');
  cellNode.className = 'jp-Cell';
  const outputArea = document.createElement('div');
  outputArea.className = 'jp-Cell-outputArea';
  cellNode.appendChild(outputArea);
  document.body.appendChild(cellNode);
  return { cellNode, outputArea, cell: new FakeCodeCell(cellNode, outputs) };
};

const fireMouseOver = (target: HTMLElement) => {
  // mouseover bubbles, so the body-level delegated handler picks it up.
  target.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
};

const fireMouseLeave = (target: HTMLElement) => {
  // mouseleave does NOT bubble; dispatching directly mirrors how the
  // browser fires it on the listener's element.
  target.dispatchEvent(new MouseEvent('mouseleave'));
};

describe('CellOutputHoverToolbar', () => {
  let toolbar: CellOutputHoverToolbar;
  let executeSpy: jest.Mock;
  let app: any;
  let commands: any;

  beforeEach(() => {
    document.body.innerHTML = '';
    featuresState.explain_error = { enabled: true, locked: false };
    featuresState.output_followup = { enabled: true, locked: false };
    featuresState.output_toolbar = { enabled: true, locked: false };
    executeSpy = jest.fn().mockResolvedValue(undefined);
    app = {
      shell: { currentWidget: null }
    };
    commands = { execute: executeSpy };
  });

  afterEach(() => {
    if (toolbar && !toolbar.isDisposed) {
      toolbar.dispose();
    }
  });

  it('appends a toolbar with Explain + Ask on hover when there is no error', () => {
    const built = buildCell([
      { output_type: 'execute_result', data: { 'text/plain': '42' } }
    ]);
    const panel = new FakeNotebookPanel([built.cell]);
    app.shell.currentWidget = panel;

    toolbar = new CellOutputHoverToolbar(app, commands);
    fireMouseOver(built.outputArea);

    const rendered = built.outputArea.querySelector('.nbi-cell-output-toolbar');
    expect(rendered).not.toBeNull();
    const labels = Array.from(
      rendered!.querySelectorAll('.nbi-cell-output-toolbar-button-label')
    ).map(el => el.textContent);
    expect(labels).toEqual(['Explain', 'Ask']);
  });

  it('shows the Troubleshoot button only when the cell has an error output', () => {
    const built = buildCell([
      {
        output_type: 'error',
        ename: 'ValueError',
        evalue: 'bad input',
        traceback: ['boom']
      }
    ]);
    const panel = new FakeNotebookPanel([built.cell]);
    app.shell.currentWidget = panel;

    toolbar = new CellOutputHoverToolbar(app, commands);
    fireMouseOver(built.outputArea);

    const labels = Array.from(
      built.outputArea.querySelectorAll('.nbi-cell-output-toolbar-button-label')
    ).map(el => el.textContent);
    expect(labels).toContain('Troubleshoot');
  });

  it('removes the toolbar when the cursor leaves the output area', () => {
    const built = buildCell();
    app.shell.currentWidget = new FakeNotebookPanel([built.cell]);
    toolbar = new CellOutputHoverToolbar(app, commands);

    fireMouseOver(built.outputArea);
    expect(
      built.outputArea.querySelector('.nbi-cell-output-toolbar')
    ).not.toBeNull();

    fireMouseLeave(built.outputArea);
    expect(
      built.outputArea.querySelector('.nbi-cell-output-toolbar')
    ).toBeNull();
  });

  it('does not render when the output_toolbar feature flag is disabled', () => {
    featuresState.output_toolbar = { enabled: false, locked: true };
    const built = buildCell();
    app.shell.currentWidget = new FakeNotebookPanel([built.cell]);
    toolbar = new CellOutputHoverToolbar(app, commands);

    fireMouseOver(built.outputArea);
    expect(
      built.outputArea.querySelector('.nbi-cell-output-toolbar')
    ).toBeNull();
  });

  it('hides per-action buttons when their feature flag is disabled', () => {
    featuresState.output_followup = { enabled: false, locked: false };
    featuresState.explain_error = { enabled: true, locked: false };
    const built = buildCell([
      { output_type: 'error', ename: 'E', evalue: 'v', traceback: ['t'] }
    ]);
    app.shell.currentWidget = new FakeNotebookPanel([built.cell]);
    toolbar = new CellOutputHoverToolbar(app, commands);

    fireMouseOver(built.outputArea);
    const labels = Array.from(
      built.outputArea.querySelectorAll('.nbi-cell-output-toolbar-button-label')
    ).map(el => el.textContent);
    expect(labels).toEqual(['Troubleshoot']);
  });

  it('returns no toolbar when every action is gated off', () => {
    featuresState.output_followup = { enabled: false, locked: false };
    featuresState.explain_error = { enabled: false, locked: false };
    const built = buildCell();
    app.shell.currentWidget = new FakeNotebookPanel([built.cell]);
    toolbar = new CellOutputHoverToolbar(app, commands);

    fireMouseOver(built.outputArea);
    expect(
      built.outputArea.querySelector('.nbi-cell-output-toolbar')
    ).toBeNull();
  });

  it('activates the hovered cell and runs the right command on click', () => {
    const cellA = buildCell();
    const cellB = buildCell();
    const panel = new FakeNotebookPanel([cellA.cell, cellB.cell]);
    panel.content.activeCellIndex = 0;
    app.shell.currentWidget = panel;

    toolbar = new CellOutputHoverToolbar(app, commands);
    fireMouseOver(cellB.outputArea);

    const explainButton = cellB.outputArea.querySelector(
      '.nbi-cell-output-toolbar-button'
    ) as HTMLButtonElement;
    explainButton.click();

    expect(panel.content.activeCellIndex).toBe(1);
    expect(executeSpy).toHaveBeenCalledWith(
      'notebook-intelligence:editor-explain-this-output'
    );
  });

  it('clears any active toolbar on dispose', () => {
    const built = buildCell();
    app.shell.currentWidget = new FakeNotebookPanel([built.cell]);
    toolbar = new CellOutputHoverToolbar(app, commands);
    fireMouseOver(built.outputArea);
    expect(
      built.outputArea.querySelector('.nbi-cell-output-toolbar')
    ).not.toBeNull();

    toolbar.dispose();
    expect(toolbar.isDisposed).toBe(true);
    expect(
      built.outputArea.querySelector('.nbi-cell-output-toolbar')
    ).toBeNull();
  });
});

// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  cellOutputAsContextBundle,
  IOutputContextBundle
} from '../../src/cell-output-bundle';

// Duck-typed cell stub. The bundle helper only reaches into `outputArea.model.toJSON()`
// for outputs and `model.sharedModel.getSource()` for the cell source.
const makeCell = (outputs: any[], source = '') =>
  ({
    outputArea: { model: { toJSON: () => outputs } },
    model: { sharedModel: { getSource: () => source } }
  }) as any;

describe('cellOutputAsContextBundle', () => {
  describe('cell source', () => {
    it('captures the source text from the cell model', () => {
      const cell = makeCell([], 'x = 1\nprint(x)');
      const bundle = cellOutputAsContextBundle(cell);
      expect(bundle.cellSource).toBe('x = 1\nprint(x)');
    });

    it('falls back to empty string when no source is available', () => {
      const cell = { outputArea: { model: { toJSON: () => [] } } } as any;
      expect(cellOutputAsContextBundle(cell).cellSource).toBe('');
    });
  });

  describe('text/plain outputs', () => {
    it('captures execute_result text/plain payloads', () => {
      const cell = makeCell([
        { output_type: 'execute_result', data: { 'text/plain': '42' } }
      ]);
      const bundle = cellOutputAsContextBundle(cell);
      expect(bundle.mimeBundles).toEqual([
        expect.objectContaining({ mimeType: 'text/plain', data: '42' })
      ]);
      expect(bundle.isError).toBe(false);
      expect(bundle.truncated).toBe(false);
    });

    it('captures stream text', () => {
      const cell = makeCell([{ output_type: 'stream', text: 'hello world' }]);
      const bundle = cellOutputAsContextBundle(cell);
      expect(bundle.mimeBundles[0]).toEqual(
        expect.objectContaining({
          mimeType: 'text/plain',
          data: 'hello world'
        })
      );
    });
  });

  describe('error outputs', () => {
    it('captures ename, evalue, ANSI-stripped traceback', () => {
      const cell = makeCell([
        {
          output_type: 'error',
          ename: 'ValueError',
          evalue: 'bad input',
          traceback: ['\u001b[31mTraceback line 1\u001b[0m', 'Traceback line 2']
        }
      ]);
      const bundle = cellOutputAsContextBundle(cell);
      expect(bundle.isError).toBe(true);
      expect(bundle.mimeBundles[0].mimeType).toBe(
        'application/vnd.jupyter.error'
      );
      expect(bundle.mimeBundles[0].data).toContain('ValueError: bad input');
      expect(bundle.mimeBundles[0].data).toContain('Traceback line 1');
      expect(bundle.mimeBundles[0].data).toContain('Traceback line 2');
      expect(bundle.mimeBundles[0].data).not.toContain('\u001b[31m');
    });
  });

  describe('text/html DataFrames', () => {
    it('prefers the text/plain alternative when both are present', () => {
      const cell = makeCell([
        {
          output_type: 'execute_result',
          data: {
            'text/html': '<table><tr><td>1</td></tr></table>',
            'text/plain': '   col\n0    1'
          }
        }
      ]);
      const bundle = cellOutputAsContextBundle(cell);
      const types = bundle.mimeBundles.map(b => b.mimeType);
      expect(types).toContain('text/plain');
      expect(types).not.toContain('text/html');
      expect(bundle.mimeBundles[0].data).toContain('col');
    });

    it('falls back to stripped HTML when only text/html is present', () => {
      const cell = makeCell([
        {
          output_type: 'execute_result',
          data: { 'text/html': '<p>hello <b>world</b></p>' }
        }
      ]);
      const bundle = cellOutputAsContextBundle(cell);
      expect(bundle.mimeBundles[0].mimeType).toBe('text/html');
      expect(bundle.mimeBundles[0].data).toContain('hello');
      expect(bundle.mimeBundles[0].data).toContain('world');
      expect(bundle.mimeBundles[0].data).not.toContain('<p>');
    });
  });

  describe('image outputs', () => {
    it('attaches raw base64 image data when the model supports vision', () => {
      const cell = makeCell([
        {
          output_type: 'display_data',
          data: { 'image/png': 'iVBORw0KGgo' }
        }
      ]);
      const bundle = cellOutputAsContextBundle(cell, { supportsVision: true });
      // Server constructs the data URL from the validated base64 + mime,
      // so the client only sends the raw payload.
      expect(bundle.mimeBundles[0]).toEqual(
        expect.objectContaining({
          mimeType: 'image/png',
          data: 'iVBORw0KGgo'
        })
      );
    });

    it('emits a placeholder when the model does not support vision', () => {
      const cell = makeCell([
        {
          output_type: 'display_data',
          data: { 'image/png': 'iVBORw0KGgo' }
        }
      ]);
      const bundle = cellOutputAsContextBundle(cell, { supportsVision: false });
      expect(bundle.mimeBundles[0].mimeType).toBe('image/png');
      expect(bundle.mimeBundles[0].data).toMatch(/<image omitted/);
    });

    it('emits a placeholder when an image is too large to inline', () => {
      const big = 'a'.repeat(300_000);
      const cell = makeCell([
        { output_type: 'display_data', data: { 'image/png': big } }
      ]);
      const bundle = cellOutputAsContextBundle(cell, { supportsVision: true });
      expect(bundle.mimeBundles[0].data).toMatch(/<image omitted/);
      expect(bundle.truncated).toBe(true);
    });
  });

  describe('Plotly outputs', () => {
    it('summarizes a Plotly figure to type + axis labels', () => {
      const cell = makeCell([
        {
          output_type: 'display_data',
          data: {
            'application/vnd.plotly.v1+json': {
              data: [{ type: 'scatter' }],
              layout: {
                xaxis: { title: { text: 'time' } },
                yaxis: { title: { text: 'value' } }
              }
            }
          }
        }
      ]);
      const bundle = cellOutputAsContextBundle(cell);
      expect(bundle.mimeBundles[0].mimeType).toBe(
        'application/vnd.plotly.v1+json'
      );
      const summary = bundle.mimeBundles[0].data;
      expect(summary).toContain('scatter');
      expect(summary).toContain('time');
      expect(summary).toContain('value');
    });
  });

  describe('application/json', () => {
    it('pretty-prints JSON output', () => {
      const cell = makeCell([
        {
          output_type: 'execute_result',
          data: { 'application/json': { a: 1, b: [2, 3] } }
        }
      ]);
      const bundle = cellOutputAsContextBundle(cell);
      const jsonBundle = bundle.mimeBundles.find(
        b => b.mimeType === 'application/json'
      )!;
      expect(jsonBundle.data).toContain('"a": 1');
      expect(jsonBundle.data).toContain('"b"');
    });
  });

  describe('truncation and budget', () => {
    it('truncates oversize text/plain output and marks truncated=true', () => {
      // Mock tiktoken counts whitespace words; build a 5000-word string.
      const big = Array(5000).fill('word').join(' ');
      const cell = makeCell([{ output_type: 'stream', text: big }]);
      const bundle = cellOutputAsContextBundle(cell, {
        maxTokensPerOutput: 100,
        maxTokensPerTurn: 1000
      });
      expect(bundle.truncated).toBe(true);
      expect(bundle.mimeBundles[0].sizeTokens).toBeLessThanOrEqual(100);
      expect(bundle.mimeBundles[0].data.length).toBeLessThan(big.length);
    });

    it('stops collecting outputs once the per-turn budget is exhausted', () => {
      const big = Array(2000).fill('word').join(' ');
      const cell = makeCell([
        { output_type: 'stream', text: big },
        { output_type: 'stream', text: 'short trailing message' }
      ]);
      const bundle = cellOutputAsContextBundle(cell, {
        maxTokensPerOutput: 4000,
        maxTokensPerTurn: 1500
      });
      expect(bundle.truncated).toBe(true);
      const totalTokens = bundle.mimeBundles.reduce(
        (sum, b) => sum + b.sizeTokens,
        0
      );
      expect(totalTokens).toBeLessThanOrEqual(1500);
    });
  });

  describe('empty cells', () => {
    it('returns an empty bundle for a cell with no outputs', () => {
      const bundle = cellOutputAsContextBundle(makeCell([]));
      expect(bundle.mimeBundles).toEqual([]);
      expect(bundle.isError).toBe(false);
      expect(bundle.truncated).toBe(false);
    });
  });

  it('returns the documented bundle shape', () => {
    const bundle: IOutputContextBundle = cellOutputAsContextBundle(
      makeCell([{ output_type: 'stream', text: 'hi' }])
    );
    expect(bundle).toMatchObject({
      cellSource: expect.any(String),
      mimeBundles: expect.any(Array),
      isError: expect.any(Boolean),
      truncated: expect.any(Boolean)
    });
  });
});

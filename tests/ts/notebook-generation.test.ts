// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  buildNotebookGenerationPrompt,
  NOTEBOOK_GENERATION_PROGRESS_EVENT,
  NOTEBOOK_GENERATION_PROMPT_PREFIX
} from '../../src/notebook-generation';

describe('buildNotebookGenerationPrompt', () => {
  it('prepends the templated prefix described in the issue', () => {
    const result = buildNotebookGenerationPrompt('plot a sine wave');
    expect(result).toBe(
      'Create a new notebook or update active notebook based on this request: plot a sine wave'
    );
  });

  it('uses the exported constant for the prefix', () => {
    const result = buildNotebookGenerationPrompt('foo');
    expect(result.startsWith(NOTEBOOK_GENERATION_PROMPT_PREFIX)).toBe(true);
  });

  it('trims surrounding whitespace from the user prompt', () => {
    const result = buildNotebookGenerationPrompt('   summarise the data\n');
    expect(result).toBe(
      `${NOTEBOOK_GENERATION_PROMPT_PREFIX}summarise the data`
    );
  });

  it('handles empty input gracefully', () => {
    const result = buildNotebookGenerationPrompt('');
    expect(result).toBe(NOTEBOOK_GENERATION_PROMPT_PREFIX);
  });

  it('handles undefined input gracefully', () => {
    const result = buildNotebookGenerationPrompt(
      undefined as unknown as string
    );
    expect(result).toBe(NOTEBOOK_GENERATION_PROMPT_PREFIX);
  });
});

describe('notebook generation surface area', () => {
  it('exposes a stable progress event name for external listeners', () => {
    expect(NOTEBOOK_GENERATION_PROGRESS_EVENT).toBe(
      'copilotSidebar:notebookGenerationProgress'
    );
  });
});

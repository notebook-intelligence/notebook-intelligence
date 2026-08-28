// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { resolveCodeClassName } from '../../src/markdown-code-class';

describe('resolveCodeClassName', () => {
  it('marks single-line content (true inline code) with inline-code', () => {
    expect(resolveCodeClassName('inline_var')).toBe('inline-code');
  });

  it('preserves an existing className alongside inline-code', () => {
    expect(resolveCodeClassName('inline_var', 'some-class')).toBe(
      'inline-code some-class'
    );
  });

  it('does not mark multi-line content (a highlighted fenced block)', () => {
    expect(
      resolveCodeClassName('import json\nimport logging\n', 'language-python')
    ).toBe('language-python');
  });

  it('does not mark multi-line content with no language class (unmatched fence)', () => {
    expect(resolveCodeClassName('plain text block\n')).toBeUndefined();
  });

  it('does not mark a single-line fenced block that still carries a trailing newline', () => {
    expect(resolveCodeClassName('x\n', 'language-text')).toBe('language-text');
  });

  it('does not mark an empty string, defensively — the real pipeline never actually produces this shape', () => {
    expect(resolveCodeClassName('')).toBeUndefined();
    expect(resolveCodeClassName('', 'language-python')).toBe('language-python');
  });

  it('does not mark undefined children, which is what react-markdown actually passes for an empty code node mid-stream (a fence before its language token arrives)', () => {
    expect(resolveCodeClassName(undefined)).toBeUndefined();
    expect(resolveCodeClassName(undefined, 'language-python')).toBe(
      'language-python'
    );
  });

  it('does not mark null children, defensively — the real pipeline never actually passes this either', () => {
    expect(resolveCodeClassName(null)).toBeUndefined();
    expect(resolveCodeClassName(null, 'language-python')).toBe(
      'language-python'
    );
  });

  it('marks inline code that spans a source newline, since remark normalizes it to a space first', () => {
    expect(resolveCodeClassName('multi word')).toBe('inline-code');
  });

  it('does not mark an empty fence whose info string has no word-only language token, since /language-(\\w+)/ cannot match it', () => {
    expect(resolveCodeClassName(undefined, 'language-{.python')).toBe(
      'language-{.python'
    );
  });
});

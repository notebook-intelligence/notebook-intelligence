// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { writeTextToClipboard } from '../../src/clipboard';

describe('writeTextToClipboard', () => {
  const originalClipboard = (navigator as any).clipboard;
  const originalExecCommand = (document as any).execCommand;

  afterEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: originalClipboard,
      configurable: true,
      writable: true
    });
    Object.defineProperty(document, 'execCommand', {
      value: originalExecCommand,
      configurable: true,
      writable: true
    });
  });

  it('writes text via the async Clipboard API when available', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
      writable: true
    });

    const ok = await writeTextToClipboard('abc-123');

    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith('abc-123');
  });

  it('falls back to execCommand when the Clipboard API rejects', async () => {
    const writeText = jest.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
      writable: true
    });
    const execSpy = jest.fn().mockReturnValue(true);
    Object.defineProperty(document, 'execCommand', {
      value: execSpy,
      configurable: true,
      writable: true
    });

    const ok = await writeTextToClipboard('fallback-id');

    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith('fallback-id');
    expect(execSpy).toHaveBeenCalledWith('copy');
  });

  it('falls back to execCommand when the Clipboard API is missing', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      configurable: true,
      writable: true
    });
    const execSpy = jest.fn().mockReturnValue(true);
    Object.defineProperty(document, 'execCommand', {
      value: execSpy,
      configurable: true,
      writable: true
    });

    const ok = await writeTextToClipboard('missing-api-id');

    expect(ok).toBe(true);
    expect(execSpy).toHaveBeenCalledWith('copy');
  });

  it('returns false when both paths fail', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      configurable: true,
      writable: true
    });
    const execSpy = jest.fn().mockReturnValue(false);
    Object.defineProperty(document, 'execCommand', {
      value: execSpy,
      configurable: true,
      writable: true
    });

    const ok = await writeTextToClipboard('nope');

    expect(ok).toBe(false);
  });
});

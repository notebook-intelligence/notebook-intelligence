// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { Notification } from '@jupyterlab/apputils';

import { NBIAPI } from '../../src/api';
import { attachTerminalDragDrop } from '../../src/terminal-drag';
import {
  formatForMode,
  hasTerminalControlCharacters,
  invertMode
} from '../../src/terminal-drag-format';

describe('formatForMode', () => {
  it('prefixes each path with @ in mention mode', () => {
    expect(formatForMode(['/tmp/a.txt', '/tmp/b.txt'], 'mention')).toBe(
      '@/tmp/a.txt @/tmp/b.txt'
    );
  });

  it('shell-escapes each path in raw mode', () => {
    expect(formatForMode(['/tmp/a.txt', '/tmp/with space.txt'], 'raw')).toBe(
      "'/tmp/a.txt' '/tmp/with space.txt'"
    );
  });

  it('returns empty string for empty input in either mode', () => {
    expect(formatForMode([], 'mention')).toBe('');
    expect(formatForMode([], 'raw')).toBe('');
  });

  it('handles a single path in mention mode', () => {
    expect(formatForMode(['/tmp/only.txt'], 'mention')).toBe('@/tmp/only.txt');
  });

  it('does not quote the @-prefix in mention mode', () => {
    // Mention mode emits agent @<path> syntax. Raw mode provides POSIX-shell
    // quoting; both modes reject terminal controls before formatting.
    expect(formatForMode(['/tmp/a b.txt'], 'mention')).toBe('@/tmp/a b.txt');
  });
});

const unicodeFormattingCodePoints = [
  0x2028, 0x2029, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067,
  0x2068, 0x2069
];

describe('hasTerminalControlCharacters', () => {
  const controlCodePoints = [
    ...Array.from({ length: 0x20 }, (_, index) => index),
    ...Array.from({ length: 0x21 }, (_, index) => 0x7f + index),
    ...unicodeFormattingCodePoints
  ].map(codePoint => ({
    label: `U+${codePoint.toString(16).toUpperCase().padStart(4, '0')}`,
    character: String.fromCharCode(codePoint)
  }));

  it.each(controlCodePoints)(
    'rejects $label anywhere in a path',
    ({ character }) => {
      expect(hasTerminalControlCharacters(`${character}/tmp/file.txt`)).toBe(
        true
      );
      expect(hasTerminalControlCharacters(`/tmp/a${character}b.txt`)).toBe(
        true
      );
      expect(hasTerminalControlCharacters(`/tmp/file.txt${character}`)).toBe(
        true
      );
    }
  );

  it.each([
    '/tmp/with space.txt',
    "/tmp/it's.txt",
    '/tmp/données/ファイル.csv',
    '/tmp/~\u00a0.txt',
    '/tmp/العربية/עברית.txt',
    '/tmp/👩\u200d💻.txt',
    '/tmp/\u2027\u202f\u2065\u206a.txt'
  ])('accepts an ordinary path %p', path => {
    expect(hasTerminalControlCharacters(path)).toBe(false);
  });
});

describe('invertMode', () => {
  it('returns the original mode when shouldInvert is false', () => {
    expect(invertMode('mention', false)).toBe('mention');
    expect(invertMode('raw', false)).toBe('raw');
  });

  it('flips mention to raw and back when shouldInvert is true', () => {
    expect(invertMode('mention', true)).toBe('raw');
    expect(invertMode('raw', true)).toBe('mention');
  });
});

describe('attachTerminalDragDrop', () => {
  type ConnectSlot = (sender: unknown, widget: unknown) => void;
  type DisposedSlot = () => void;

  interface ITestWidgetEnv {
    mock: any;
    host: HTMLElement;
    paste: jest.Mock;
    activate: jest.Mock;
    fireDisposed: () => void;
  }

  function setupTracker(): {
    tracker: any;
    fireWidgetAdded: (widget: unknown) => void;
  } {
    let widgetAddedSlot: ConnectSlot | null = null;
    const tracker = {
      widgetAdded: {
        connect: (slot: ConnectSlot) => {
          widgetAddedSlot = slot;
        }
      },
      forEach: (_fn: (widget: unknown) => void) => undefined
    };
    return {
      tracker,
      fireWidgetAdded: (widget: unknown) => {
        if (!widgetAddedSlot) {
          throw new Error('widgetAdded was never wired');
        }
        widgetAddedSlot(null, widget);
      }
    };
  }

  function buildWidget(): ITestWidgetEnv {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const paste = jest.fn();
    const activate = jest.fn();
    let isDisposed = false;
    const disposedSlots: DisposedSlot[] = [];
    const mock = {
      node: host,
      content: { paste },
      toolbar: { addItem: jest.fn().mockReturnValue(true) },
      disposed: {
        connect: (slot: DisposedSlot) => {
          disposedSlots.push(slot);
        }
      },
      activate,
      get isDisposed() {
        return isDisposed;
      }
    };
    return {
      mock,
      host,
      paste,
      activate,
      fireDisposed: () => {
        isDisposed = true;
        disposedSlots.forEach(slot => slot());
      }
    };
  }

  function dispatchLmDrop(
    target: EventTarget,
    paths: string[],
    shiftHeld = false
  ): void {
    const event: any = new Event('lm-drop', {
      bubbles: true,
      cancelable: true
    });
    event.mimeData = {
      hasData: (key: string) => key === 'application/x-jupyter-icontents',
      getData: (key: string) =>
        key === 'application/x-jupyter-icontents' ? paths : null
    };
    event.proposedAction = 'move';
    event.dropAction = 'none';
    event.shiftKey = shiftHeld;
    target.dispatchEvent(event);
  }

  function dispatchNativeDrop(
    target: EventTarget,
    files: File[],
    shiftHeld = false
  ): void {
    const event = new DragEvent('drop', {
      bubbles: true,
      cancelable: true,
      shiftKey: shiftHeld
    });
    Object.defineProperty(event, 'dataTransfer', {
      value: { files, types: ['Files'] }
    });
    target.dispatchEvent(event);
  }

  function setRawMode(env: ITestWidgetEnv): void {
    const [, buttonWidget] = env.mock.toolbar.addItem.mock.calls[0];
    buttonWidget.node.querySelector('button').click();
  }

  // Each test must dispose its widget so the document-level lm-* listeners
  // attached by setupTerminal don't leak across tests in this file.
  const envs: ITestWidgetEnv[] = [];
  afterEach(() => {
    envs.splice(0).forEach(env => {
      env.fireDisposed();
      if (env.host.parentNode) {
        env.host.parentNode.removeChild(env.host);
      }
    });
    jest.restoreAllMocks();
  });

  function newEnv(): ITestWidgetEnv {
    const env = buildWidget();
    envs.push(env);
    return env;
  }

  function wiredEnv(toolbarRaw = false): ITestWidgetEnv {
    const env = newEnv();
    const { tracker, fireWidgetAdded } = setupTracker();
    attachTerminalDragDrop({ tracker, isEnabled: () => true });
    fireWidgetAdded(env.mock);
    if (toolbarRaw) {
      setRawMode(env);
    }
    return env;
  }

  const modes = [
    { name: 'mention', toolbarRaw: false, shiftHeld: false, raw: false },
    {
      name: 'Shift-inverted mention',
      toolbarRaw: false,
      shiftHeld: true,
      raw: true
    },
    { name: 'toolbar raw', toolbarRaw: true, shiftHeld: false, raw: true },
    {
      name: 'Shift-inverted raw',
      toolbarRaw: true,
      shiftHeld: true,
      raw: false
    }
  ];
  const acceptedPaths = [
    '/tmp/first file.txt',
    "/tmp/it's.txt",
    '/tmp/données/ファイル.csv'
  ];
  const mentionText =
    "@/tmp/first file.txt @/tmp/it's.txt @/tmp/données/ファイル.csv ";
  const rawText =
    "'/tmp/first file.txt' '/tmp/it'\\''s.txt' '/tmp/données/ファイル.csv' ";

  it.each([
    { name: 'LF', path: '/tmp/report\nprintf harmless\n.csv' },
    { name: 'CR', path: '/tmp/report\rprintf harmless\r.csv' },
    { name: 'CRLF', path: '/tmp/report\r\nprintf harmless\r\n.csv' },
    { name: 'escape sequence', path: '/tmp/report\u001b]0;harmless\u0007.csv' },
    {
      name: 'bracketed-paste boundary',
      path: '/tmp/report\u001b[201~printf harmless\u001b[200~.csv'
    }
  ])(
    'rejects a file-browser path containing $name without echoing it',
    ({ path }) => {
      const env = wiredEnv();

      dispatchLmDrop(env.host, [path]);

      expect(env.paste).not.toHaveBeenCalled();
      expect(env.activate).not.toHaveBeenCalled();
      expect(Notification.warning).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledWith(
        'Terminal drop skipped 1 path containing control or invisible formatting characters. Rename them before dropping.'
      );
    }
  );

  it.each(modes)(
    'keeps accepted file-browser paths in order in $name mode',
    ({ toolbarRaw, shiftHeld, raw }) => {
      const env = wiredEnv(toolbarRaw);

      dispatchLmDrop(
        env.host,
        [
          acceptedPaths[0],
          '/tmp/private\nfilename',
          acceptedPaths[1],
          '/tmp/private\u009bfilename',
          acceptedPaths[2]
        ],
        shiftHeld
      );

      expect(env.paste).toHaveBeenCalledTimes(1);
      expect(env.paste).toHaveBeenCalledWith(raw ? rawText : mentionText);
      expect(env.activate).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledWith(
        'Terminal drop skipped 2 paths containing control or invisible formatting characters. Rename them before dropping.'
      );

      // Shift affects only this drop; it must not change the toolbar's mode.
      dispatchLmDrop(env.host, ['/tmp/next.txt']);
      expect(env.paste).toHaveBeenLastCalledWith(
        toolbarRaw ? "'/tmp/next.txt' " : '@/tmp/next.txt '
      );
      expect(Notification.warning).toHaveBeenCalledTimes(1);
    }
  );

  it.each(modes)(
    'does not paste or activate when all file-browser paths are rejected in $name mode',
    ({ toolbarRaw, shiftHeld }) => {
      const env = wiredEnv(toolbarRaw);

      dispatchLmDrop(env.host, ['/tmp/a\rb', '/tmp/c\u001b[201~d'], shiftHeld);

      expect(env.paste).not.toHaveBeenCalled();
      expect(env.activate).not.toHaveBeenCalled();
      expect(Notification.warning).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledWith(
        'Terminal drop skipped 2 paths containing control or invisible formatting characters. Rename them before dropping.'
      );
    }
  );

  it.each(modes)(
    'checks uploaded server paths and preserves accepted order in $name mode',
    async ({ toolbarRaw, shiftHeld, raw }) => {
      const env = wiredEnv(toolbarRaw);
      const serverPaths = [
        acceptedPaths[0],
        '/tmp/server\r\nprintf harmless',
        acceptedPaths[1],
        '/tmp/server\u001b[201~printf harmless',
        acceptedPaths[2]
      ];
      const files = serverPaths.map(
        (_, index) => new File(['contents'], `local-${index}.txt`)
      );
      const uploads = serverPaths.map((serverPath, index) =>
        Promise.resolve({
          serverPath,
          filename: files[index].name
        })
      );
      const uploadFile = jest.spyOn(NBIAPI, 'uploadFile');
      uploads.forEach(upload => uploadFile.mockReturnValueOnce(upload));

      dispatchNativeDrop(env.host, files, shiftHeld);
      await Promise.all(uploads);

      expect(uploadFile).toHaveBeenCalledTimes(files.length);
      files.forEach((file, index) => {
        expect(uploadFile).toHaveBeenNthCalledWith(index + 1, file);
      });
      expect(env.paste).toHaveBeenCalledTimes(1);
      expect(env.paste).toHaveBeenCalledWith(raw ? rawText : mentionText);
      expect(env.activate).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledWith(
        'Terminal drop skipped 2 paths containing control or invisible formatting characters. Rename them before dropping.'
      );
      expect(Notification.error).not.toHaveBeenCalled();
    }
  );

  it.each(modes)(
    'does not paste or activate when all uploaded paths are rejected in $name mode',
    async ({ toolbarRaw, shiftHeld }) => {
      const env = wiredEnv(toolbarRaw);
      const upload = Promise.resolve({
        serverPath: '/tmp/upload\u0085private.txt',
        filename: 'ordinary-local-name.txt'
      });
      jest.spyOn(NBIAPI, 'uploadFile').mockReturnValue(upload);

      dispatchNativeDrop(
        env.host,
        [new File(['contents'], 'ordinary-local-name.txt')],
        shiftHeld
      );
      await Promise.all([upload]);

      expect(env.paste).not.toHaveBeenCalled();
      expect(env.activate).not.toHaveBeenCalled();
      expect(Notification.warning).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledWith(
        'Terminal drop skipped 1 path containing control or invisible formatting characters. Rename them before dropping.'
      );
    }
  );

  const unicodeDropCases = modes.flatMap(mode =>
    unicodeFormattingCodePoints.map(code => ({
      ...mode,
      label: `U+${code.toString(16).toUpperCase()}`,
      character: String.fromCharCode(code)
    }))
  );

  it.each(unicodeDropCases)(
    'skips $label in a file-browser drop in $name mode',
    ({ toolbarRaw, shiftHeld, raw, character }) => {
      const env = wiredEnv(toolbarRaw);
      const rejected = `/tmp/private${character}name.txt`;

      dispatchLmDrop(
        env.host,
        [acceptedPaths[0], rejected, ...acceptedPaths.slice(1)],
        shiftHeld
      );

      expect(env.paste).toHaveBeenCalledTimes(1);
      expect(env.paste).toHaveBeenCalledWith(raw ? rawText : mentionText);
      expect(env.activate).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledWith(
        'Terminal drop skipped 1 path containing control or invisible formatting characters. Rename them before dropping.'
      );
      expect(Notification.error).not.toHaveBeenCalled();
    }
  );

  it.each(unicodeDropCases)(
    'skips $label in uploaded server paths in $name mode',
    async ({ toolbarRaw, shiftHeld, raw, character }) => {
      const env = wiredEnv(toolbarRaw);
      const serverPaths = [
        acceptedPaths[0],
        `/tmp/private${character}name.txt`,
        ...acceptedPaths.slice(1)
      ];
      const files = serverPaths.map(
        (_, index) => new File(['contents'], `local-${index}.txt`)
      );
      const uploads = serverPaths.map((serverPath, index) =>
        Promise.resolve({ serverPath, filename: files[index].name })
      );
      const uploadFile = jest.spyOn(NBIAPI, 'uploadFile');
      uploads.forEach(upload => uploadFile.mockReturnValueOnce(upload));

      dispatchNativeDrop(env.host, files, shiftHeld);
      await Promise.all(uploads);

      expect(uploadFile).toHaveBeenCalledTimes(files.length);
      expect(env.paste).toHaveBeenCalledTimes(1);
      expect(env.paste).toHaveBeenCalledWith(raw ? rawText : mentionText);
      expect(env.activate).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledTimes(1);
      expect(Notification.warning).toHaveBeenCalledWith(
        'Terminal drop skipped 1 path containing control or invisible formatting characters. Rename them before dropping.'
      );
      expect(Notification.error).not.toHaveBeenCalled();
    }
  );

  it('reports upload failures separately while filtering successful server paths', async () => {
    const env = wiredEnv();
    const files = ['first.txt', 'failed.txt', 'rejected.txt', 'last.txt'].map(
      name => new File(['contents'], name)
    );
    const uploads = [
      Promise.resolve({ serverPath: '/tmp/first.txt', filename: 'first.txt' }),
      Promise.reject(new Error('Upload unavailable')),
      Promise.resolve({
        serverPath: '/tmp/rejected\nprivate.txt',
        filename: 'rejected.txt'
      }),
      Promise.resolve({ serverPath: '/tmp/last.txt', filename: 'last.txt' })
    ];
    const uploadFile = jest.spyOn(NBIAPI, 'uploadFile');
    uploads.forEach(upload => uploadFile.mockReturnValueOnce(upload));

    dispatchNativeDrop(env.host, files);
    await Promise.allSettled(uploads);

    expect(env.paste).toHaveBeenCalledTimes(1);
    expect(env.paste).toHaveBeenCalledWith('@/tmp/first.txt @/tmp/last.txt ');
    expect(env.activate).toHaveBeenCalledTimes(1);
    expect(Notification.error).toHaveBeenCalledTimes(1);
    expect(Notification.error).toHaveBeenCalledWith(
      'Terminal drop upload failed for failed.txt: Upload unavailable'
    );
    expect(Notification.warning).toHaveBeenCalledTimes(1);
    expect(Notification.warning).toHaveBeenCalledWith(
      'Terminal drop skipped 1 path containing control or invisible formatting characters. Rename them before dropping.'
    );
  });

  it.each(['/tmp/safe.txt', '/tmp/rejected\nprivate.txt'])(
    'ignores a late upload after the terminal is disposed: %p',
    async serverPath => {
      const env = wiredEnv();
      let finishUpload!: (result: {
        serverPath: string;
        filename: string;
      }) => void;
      const upload = new Promise<{ serverPath: string; filename: string }>(
        resolve => {
          finishUpload = resolve;
        }
      );
      jest.spyOn(NBIAPI, 'uploadFile').mockReturnValue(upload);
      dispatchNativeDrop(env.host, [new File(['contents'], 'local.txt')]);
      env.fireDisposed();

      finishUpload({ serverPath, filename: 'local.txt' });
      await Promise.all([upload]);

      expect(env.paste).not.toHaveBeenCalled();
      expect(env.activate).not.toHaveBeenCalled();
      expect(Notification.warning).not.toHaveBeenCalled();
      expect(Notification.error).not.toHaveBeenCalled();
    }
  );

  it('activates the widget after pasting so the next Enter goes to xterm, not the file-browser', () => {
    const env = newEnv();
    const { tracker, fireWidgetAdded } = setupTracker();
    attachTerminalDragDrop({ tracker, isEnabled: () => true });
    fireWidgetAdded(env.mock);

    dispatchLmDrop(env.host, ['/tmp/file.txt']);

    expect(env.paste).toHaveBeenCalledTimes(1);
    expect(env.paste).toHaveBeenCalledWith('@/tmp/file.txt ');
    expect(env.activate).toHaveBeenCalledTimes(1);
    // Activate must run after paste so the terminal owns focus before the
    // user presses Enter; otherwise the file-browser's "open selected item"
    // handler eats the first keypress.
    expect(env.activate.mock.invocationCallOrder[0]).toBeGreaterThan(
      env.paste.mock.invocationCallOrder[0]
    );
  });

  it('handles drops on a descendant of the terminal host, not just the host itself', () => {
    const env = newEnv();
    const inner = document.createElement('div');
    env.host.appendChild(inner);
    const { tracker, fireWidgetAdded } = setupTracker();
    attachTerminalDragDrop({ tracker, isEnabled: () => true });
    fireWidgetAdded(env.mock);

    dispatchLmDrop(inner, ['/tmp/nested.txt']);

    expect(env.paste).toHaveBeenCalledWith('@/tmp/nested.txt ');
    expect(env.activate).toHaveBeenCalled();
  });
});

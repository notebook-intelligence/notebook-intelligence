// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  Extension,
  Prec,
  StateEffect,
  StateField,
  TransactionSpec
} from '@codemirror/state';
import { EditorView, ViewPlugin, ViewUpdate, keymap } from '@codemirror/view';

import { NBIAPI } from './api';

export interface IChatbookMentionItem {
  label: string;
  value: string;
  kind: 'root' | 'file' | 'dir' | 'reference' | string;
  hasChildren: boolean;
  description?: string;
}

export interface IChatbookMentionTrigger {
  from: number;
  to: number;
  query: string;
}

export function detectChatbookMentionTrigger(
  text: string,
  cursor: number
): IChatbookMentionTrigger | null {
  const before = text.slice(0, Math.max(0, cursor));
  const match = /(?:^|\s)@([^\s@]*)$/u.exec(before);
  if (!match) {
    return null;
  }
  const query = match[1] || '';
  return {
    from: cursor - query.length - 1,
    to: cursor,
    query
  };
}

export function applyChatbookMention(
  text: string,
  trigger: IChatbookMentionTrigger,
  value: string
): string {
  const suffix = text.slice(trigger.to);
  const spacer = /^\s/u.test(suffix) ? '' : ' ';
  return `${text.slice(0, trigger.from)}@${value}${spacer}${suffix}`;
}

const MENU_KEYS = new Set([
  'ArrowDown',
  'ArrowUp',
  'ArrowLeft',
  'Backspace',
  'Enter',
  'Escape',
  'Tab'
]);

/**
 * Keys the mention menu claims while it is open. Modified shortcuts
 * (Shift+Tab, Shift+Enter, ...) stay with the editor and the notebook.
 */
export function isChatbookMentionMenuKey(
  event: Pick<KeyboardEvent, 'key' | 'altKey' | 'ctrlKey' | 'metaKey'> & {
    shiftKey?: boolean;
  }
): boolean {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
    return false;
  }
  return MENU_KEYS.has(event.key);
}

const setMentionEnabled = StateEffect.define<boolean>();
const setMentionNotebookPath = StateEffect.define<string>();
const mentionEnabledField = StateField.define<boolean>({
  create: () => false,
  update(value, transaction) {
    for (const effect of transaction.effects) {
      if (effect.is(setMentionEnabled)) {
        return effect.value;
      }
    }
    return value;
  }
});
const mentionNotebookPathField = StateField.define<string>({
  create: () => '',
  update(value, transaction) {
    for (const effect of transaction.effects) {
      if (effect.is(setMentionNotebookPath)) {
        return effect.value;
      }
    }
    return value;
  }
});

class ChatbookMentionMenu {
  constructor(readonly view: EditorView) {}

  update(update: ViewUpdate): void {
    const enabled = update.state.field(mentionEnabledField);
    const wasEnabled = update.startState.field(mentionEnabledField);
    if (!enabled) {
      this.close();
      return;
    }
    if (!wasEnabled || update.docChanged || update.selectionSet) {
      this.schedule();
    }
  }

  destroy(): void {
    this.close();
  }

  handleKey(event: KeyboardEvent): boolean {
    if (!this._menu || this._items.length === 0) {
      return false;
    }
    if (!isChatbookMentionMenuKey(event)) {
      return false;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const delta = event.key === 'ArrowDown' ? 1 : -1;
      this._active =
        (this._active + delta + this._items.length) % this._items.length;
      this.render();
      return true;
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      this.select(this._items[this._active]);
      return true;
    }
    if (event.key === 'Escape') {
      this.close();
      return true;
    }
    if (
      (event.key === 'Backspace' || event.key === 'ArrowLeft') &&
      this._parent &&
      this._trigger?.query === ''
    ) {
      this._parent = '';
      this.schedule(0);
      return true;
    }
    return false;
  }

  private schedule(delay = 150): void {
    if (this._timer !== null) {
      window.clearTimeout(this._timer);
    }
    this._timer = window.setTimeout(() => {
      this._timer = null;
      void this.refresh();
    }, delay);
  }

  private async refresh(): Promise<void> {
    const selection = this.view.state.selection.main;
    if (!selection.empty) {
      this.close();
      return;
    }
    const trigger = detectChatbookMentionTrigger(
      this.view.state.doc.toString(),
      selection.head
    );
    if (!trigger) {
      this.close();
      return;
    }
    this._trigger = trigger;
    this._request?.abort();
    const request = new AbortController();
    this._request = request;
    try {
      const response = await NBIAPI.listChatbookMentions(
        this._parent,
        trigger.query,
        100,
        this.view.state.field(mentionNotebookPathField),
        request.signal
      );
      if (request.signal.aborted || this._request !== request) {
        return;
      }
      this._items = response.items;
      this._active = Math.min(
        this._active,
        Math.max(0, this._items.length - 1)
      );
      this.render();
    } catch (error) {
      if (!request.signal.aborted) {
        console.warn('Could not list Chatbook mentions', error);
        this.close();
      }
    }
  }

  private select(item: IChatbookMentionItem): void {
    if (item.hasChildren) {
      this._parent = item.value;
      this._active = 0;
      this.schedule(0);
      return;
    }
    const trigger = this._trigger;
    if (!trigger) {
      return;
    }
    const transaction: TransactionSpec = {
      // Avoid doubling a space when completion is accepted before existing
      // whitespace in the middle of a sentence.
      changes: {
        from: trigger.from,
        to: trigger.to,
        insert: `@${item.value}${
          /^\s/u.test(this.view.state.doc.sliceString(trigger.to)) ? '' : ' '
        }`
      },
      selection: {
        anchor:
          trigger.from +
          item.value.length +
          1 +
          (/^\s/u.test(this.view.state.doc.sliceString(trigger.to)) ? 0 : 1)
      },
      scrollIntoView: true
    };
    this.view.dispatch(transaction);
    this.close();
    this.view.focus();
  }

  private render(): void {
    if (this._items.length === 0) {
      this.close();
      return;
    }
    if (!this._menu) {
      this._menu = document.createElement('div');
      this._menu.id = `nbi-chatbook-mentions-${Math.random()
        .toString(36)
        .slice(2)}`;
      this._menu.className = 'nbi-chatbook-mention-menu';
      this._menu.setAttribute('role', 'listbox');
      document.body.appendChild(this._menu);
      window.addEventListener('keydown', this._onKeyDown, true);
      this.view.dom.setAttribute('aria-controls', this._menu.id);
    }
    this._menu.textContent = '';
    this._items.forEach((item, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.id = `${this._menu!.id}-option-${index}`;
      option.className = 'nbi-chatbook-mention-option';
      option.classList.toggle('active', index === this._active);
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(index === this._active));
      const icon = item.kind === 'root' ? '⌂' : item.kind === 'dir' ? '▸' : '·';
      option.textContent = `${icon} ${item.label}`;
      option.addEventListener('mousedown', event => {
        event.preventDefault();
        this._active = index;
        this.select(item);
      });
      this._menu!.appendChild(option);
    });
    const activeId = `${this._menu.id}-option-${this._active}`;
    this.view.dom.setAttribute('aria-activedescendant', activeId);
    const position = this.view.coordsAtPos(
      this._trigger?.to ?? this.view.state.selection.main.head
    );
    if (position) {
      this._menu.style.left = `${position.left}px`;
      this._menu.style.top = `${position.bottom + 4}px`;
    }
  }

  /**
   * JupyterLab binds Tab on the document (completer invoke, inline completion
   * accept), so the editor keymap alone never sees it while the menu is open.
   * Window capture runs ahead of those bindings.
   */
  private _onKeyDown = (event: KeyboardEvent): void => {
    if (!this._menu || !isChatbookMentionMenuKey(event)) {
      return;
    }
    const target = event.target as Node | null;
    if (!target || !this.view.dom.contains(target)) {
      return;
    }
    if (this.handleKey(event)) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
    }
  };

  private close(): void {
    const idle =
      this._timer === null && this._request === null && this._menu === null;
    if (this._timer !== null) {
      window.clearTimeout(this._timer);
      this._timer = null;
    }
    this._request?.abort();
    this._request = null;
    if (idle) {
      return;
    }
    window.removeEventListener('keydown', this._onKeyDown, true);
    this._menu?.remove();
    this._menu = null;
    this._items = [];
    this._active = 0;
    this._parent = '';
    this._trigger = null;
    this.view.dom.removeAttribute('aria-controls');
    this.view.dom.removeAttribute('aria-activedescendant');
  }

  private _active = 0;
  private _items: IChatbookMentionItem[] = [];
  private _menu: HTMLDivElement | null = null;
  private _parent = '';
  private _request: AbortController | null = null;
  private _timer: number | null = null;
  private _trigger: IChatbookMentionTrigger | null = null;
}

const mentionPlugin = ViewPlugin.fromClass(ChatbookMentionMenu);
const mentionKeymap = Prec.highest(
  keymap.of([
    {
      key: 'ArrowDown',
      run: view =>
        view
          .plugin(mentionPlugin)
          ?.handleKey(new KeyboardEvent('keydown', { key: 'ArrowDown' })) ??
        false
    },
    {
      key: 'ArrowUp',
      run: view =>
        view
          .plugin(mentionPlugin)
          ?.handleKey(new KeyboardEvent('keydown', { key: 'ArrowUp' })) ?? false
    },
    {
      key: 'Enter',
      run: view =>
        view
          .plugin(mentionPlugin)
          ?.handleKey(new KeyboardEvent('keydown', { key: 'Enter' })) ?? false
    },
    {
      key: 'Tab',
      run: view =>
        view
          .plugin(mentionPlugin)
          ?.handleKey(new KeyboardEvent('keydown', { key: 'Tab' })) ?? false
    },
    {
      key: 'Escape',
      run: view =>
        view
          .plugin(mentionPlugin)
          ?.handleKey(new KeyboardEvent('keydown', { key: 'Escape' })) ?? false
    },
    {
      key: 'Backspace',
      run: view =>
        view
          .plugin(mentionPlugin)
          ?.handleKey(new KeyboardEvent('keydown', { key: 'Backspace' })) ??
        false
    },
    {
      key: 'ArrowLeft',
      run: view =>
        view
          .plugin(mentionPlugin)
          ?.handleKey(new KeyboardEvent('keydown', { key: 'ArrowLeft' })) ??
        false
    }
  ])
);

const mentionExtension: Extension = [
  mentionEnabledField,
  mentionNotebookPathField,
  mentionPlugin,
  mentionKeymap
];

export function setChatbookMentionsEnabled(
  view: EditorView,
  enabled: boolean,
  notebookPath = ''
): void {
  const currentEnabled = view.state.field(mentionEnabledField, false);
  const currentPath = view.state.field(mentionNotebookPathField, false);
  if (currentEnabled === undefined) {
    if (!enabled) {
      return;
    }
    view.dispatch({
      effects: StateEffect.appendConfig.of(mentionExtension)
    });
  } else if (currentEnabled === enabled && currentPath === notebookPath) {
    return;
  }
  view.dispatch({
    effects: [
      setMentionEnabled.of(enabled),
      setMentionNotebookPath.of(notebookPath)
    ]
  });
}

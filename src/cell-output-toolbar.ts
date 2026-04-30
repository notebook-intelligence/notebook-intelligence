// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { JupyterFrontEnd } from '@jupyterlab/application';
import { CodeCell } from '@jupyterlab/cells';
import { NotebookPanel } from '@jupyterlab/notebook';
import { CommandRegistry } from '@lumino/commands';
import { IDisposable } from '@lumino/disposable';
import { NBIAPI } from './api';

interface IToolbarAction {
  id: string;
  label: string;
  title: string;
  iconSvg: string;
  command: string;
  /** Hide the button when this feature flag is disabled. */
  featureFlag: 'explain_error' | 'output_followup';
  /** Only show when the cell has at least one error output. */
  requireError?: boolean;
}

const TOOLBAR_CLASS = 'nbi-cell-output-toolbar';
const BUTTON_CLASS = 'nbi-cell-output-toolbar-button';

// Inline SVGs use `currentColor` so the icons follow theme foreground.
const SPARKLES_ICON =
  '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" fill="currentColor" aria-hidden="true"><path d="M7 1l1.5 3.5L12 6 8.5 7.5 7 11 5.5 7.5 2 6l3.5-1.5zM12 9l.8 1.7L14.5 11.5l-1.7.8L12 14l-.8-1.7L9.5 11.5l1.7-.8z"/></svg>';
const CHAT_ICON =
  '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" fill="currentColor" aria-hidden="true"><path d="M8 2C4.13 2 1 4.46 1 7.5c0 1.4.66 2.67 1.74 3.62L2 14l3.13-1.34c.9.22 1.86.34 2.87.34 3.87 0 7-2.46 7-5.5S11.87 2 8 2zm-3 6.5a1 1 0 110-2 1 1 0 010 2zm3 0a1 1 0 110-2 1 1 0 010 2zm3 0a1 1 0 110-2 1 1 0 010 2z"/></svg>';
const BUG_ICON =
  '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" fill="currentColor" aria-hidden="true"><path d="M8 1a3 3 0 00-3 3v.4A4.5 4.5 0 003 8.5v.5H1v1h2v1.16A4.5 4.5 0 008 15a4.5 4.5 0 005-4.84V10h2V9h-2v-.5a4.5 4.5 0 00-2-4.1V4a3 3 0 00-3-3zm-2 3a2 2 0 014 0v.07a4.5 4.5 0 00-4 0V4z"/></svg>';

const ACTIONS: IToolbarAction[] = [
  {
    id: 'explain',
    label: 'Explain',
    title: "Explain this cell's output",
    iconSvg: SPARKLES_ICON,
    command: 'notebook-intelligence:editor-explain-this-output',
    featureFlag: 'output_followup'
  },
  {
    id: 'ask',
    label: 'Ask',
    title: 'Ask about this output',
    iconSvg: CHAT_ICON,
    command: 'notebook-intelligence:editor-ask-about-this-output',
    featureFlag: 'output_followup'
  },
  {
    id: 'troubleshoot',
    label: 'Troubleshoot',
    title: 'Troubleshoot the error in this cell',
    iconSvg: BUG_ICON,
    command: 'notebook-intelligence:editor-troubleshoot-this-output',
    featureFlag: 'explain_error',
    requireError: true
  }
];

/**
 * Show a hover toolbar over Jupyter cell outputs that surfaces the existing
 * Explain / Ask / Troubleshoot commands as one-click buttons.
 *
 * The toolbar respects the `output_toolbar` feature flag (whole-toolbar
 * gate) and the per-action `explain_error` / `output_followup` flags so a
 * locked-off feature stays locked off here too.
 */
export class CellOutputHoverToolbar implements IDisposable {
  private _app: JupyterFrontEnd;
  private _commands: CommandRegistry;
  private _disposed = false;
  private _activeArea: HTMLElement | null = null;
  private _onMouseOver: (event: MouseEvent) => void;
  private _onMouseLeave: () => void;

  constructor(app: JupyterFrontEnd, commands: CommandRegistry) {
    this._app = app;
    // The Explain / Ask / Troubleshoot commands live on the context-menu's
    // private CommandRegistry, not on `app.commands`, so callers must pass
    // the same registry the menu uses.
    this._commands = commands;
    this._onMouseOver = this._handleMouseOver.bind(this);
    // mouseleave only fires when the cursor exits the area entirely —
    // descendants (including the toolbar itself) don't trigger it.
    this._onMouseLeave = this._removeActiveToolbar.bind(this);
    document.body.addEventListener('mouseover', this._onMouseOver);
  }

  get isDisposed(): boolean {
    return this._disposed;
  }

  dispose(): void {
    if (this._disposed) {
      return;
    }
    this._disposed = true;
    document.body.removeEventListener('mouseover', this._onMouseOver);
    this._removeActiveToolbar();
  }

  private _handleMouseOver(event: MouseEvent): void {
    if (!NBIAPI.config.cellOutputFeatures.output_toolbar.enabled) {
      this._removeActiveToolbar();
      return;
    }
    const target = event.target as HTMLElement | null;
    if (!target) {
      return;
    }
    const area = target.closest<HTMLElement>('.jp-Cell-outputArea');
    if (!area) {
      return;
    }
    if (area === this._activeArea) {
      return;
    }
    this._removeActiveToolbar();
    const cellEl = area.closest<HTMLElement>('.jp-Cell');
    if (!cellEl) {
      return;
    }
    const located = this._locateCell(cellEl);
    if (!located) {
      return;
    }
    const toolbar = this._buildToolbar(
      located.panel,
      located.cellIndex,
      located.cell
    );
    if (!toolbar) {
      return;
    }
    area.appendChild(toolbar);
    this._activeArea = area;
    area.addEventListener('mouseleave', this._onMouseLeave);
  }

  private _removeActiveToolbar(): void {
    if (!this._activeArea) {
      return;
    }
    this._activeArea.removeEventListener('mouseleave', this._onMouseLeave);
    const existing = this._activeArea.querySelector(`.${TOOLBAR_CLASS}`);
    if (existing) {
      existing.remove();
    }
    this._activeArea = null;
  }

  private _locateCell(
    cellEl: HTMLElement
  ): { panel: NotebookPanel; cell: CodeCell; cellIndex: number } | null {
    const widget = this._app.shell.currentWidget;
    if (!(widget instanceof NotebookPanel)) {
      return null;
    }
    const widgets = widget.content.widgets;
    for (let i = 0; i < widgets.length; i++) {
      const cell = widgets[i];
      if (cell.node === cellEl && cell instanceof CodeCell) {
        return { panel: widget, cell, cellIndex: i };
      }
    }
    return null;
  }

  private _buildToolbar(
    panel: NotebookPanel,
    cellIndex: number,
    cell: CodeCell
  ): HTMLElement | null {
    const features = NBIAPI.config.cellOutputFeatures;
    const hasError = cell.outputArea.model
      .toJSON()
      .some(o => o.output_type === 'error');

    const visible = ACTIONS.filter(a => {
      if (!features[a.featureFlag].enabled) {
        return false;
      }
      if (a.requireError && !hasError) {
        return false;
      }
      return true;
    });
    if (visible.length === 0) {
      return null;
    }

    const toolbar = document.createElement('div');
    toolbar.className = TOOLBAR_CLASS;
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', 'Notebook Intelligence cell actions');

    for (const action of visible) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = BUTTON_CLASS;
      button.title = action.title;
      button.setAttribute('aria-label', action.title);
      button.innerHTML = `${action.iconSvg}<span class="${BUTTON_CLASS}-label">${action.label}</span>`;
      button.addEventListener('click', event => {
        event.stopPropagation();
        // The editor commands act on the active cell, so activate the
        // hovered one first.
        panel.content.activeCellIndex = cellIndex;
        void this._commands.execute(action.command);
      });
      toolbar.appendChild(button);
    }
    return toolbar;
  }
}

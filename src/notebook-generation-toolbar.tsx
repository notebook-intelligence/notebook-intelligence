// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { JupyterFrontEnd } from '@jupyterlab/application';
import { ReactWidget } from '@jupyterlab/apputils';
import { DocumentRegistry } from '@jupyterlab/docregistry';
import { INotebookModel, NotebookPanel } from '@jupyterlab/notebook';
import { LabIcon, ToolbarButton } from '@jupyterlab/ui-components';
import { IDisposable, DisposableDelegate } from '@lumino/disposable';
import { Widget } from '@lumino/widgets';
import { UUID } from '@lumino/coreutils';
import React from 'react';

import {
  IRunChatCompletionRequest,
  RunChatCompletionType
} from './chat-sidebar';
import {
  buildNotebookGenerationPrompt,
  INotebookGenerationProgressDetail,
  NOTEBOOK_GENERATION_PROGRESS_EVENT
} from './notebook-generation';
import { NotebookGenerationPopover } from './components/notebook-generation-popover';

const TOOLBAR_BUTTON_NAME = 'nbi-generate-notebook';
const TOOLBAR_BUTTON_RANK = 11;

interface INotebookGenerationToolbarOptions {
  app: JupyterFrontEnd;
  icon: LabIcon;
  chatSidebarId: string;
}

class NotebookGenerationPopoverWidget extends ReactWidget {
  constructor(options: {
    initialShowInChat: boolean;
    onSubmit: (prompt: string, showInChat: boolean) => void;
    onClose: () => void;
  }) {
    super();
    this.addClass('nbi-notebook-generation-popover-host');
    this._options = options;
  }

  protected onAfterAttach(): void {
    document.addEventListener('mousedown', this._onDocumentMouseDown, true);
  }

  protected onBeforeDetach(): void {
    document.removeEventListener('mousedown', this._onDocumentMouseDown, true);
  }

  private _onDocumentMouseDown = (event: MouseEvent): void => {
    const target = event.target as Node | null;
    if (target && this.node.contains(target)) {
      return;
    }
    this._options.onClose();
  };

  positionAt(rect: DOMRect): void {
    const popoverWidth = 360;
    const margin = 8;
    let left = rect.left;
    if (left + popoverWidth + margin > window.innerWidth) {
      left = Math.max(margin, window.innerWidth - popoverWidth - margin);
    }
    const top = rect.bottom + 4;
    this.node.style.position = 'fixed';
    this.node.style.left = `${left}px`;
    this.node.style.top = `${top}px`;
    this.node.style.width = `${popoverWidth}px`;
    this.node.style.zIndex = '10000';
  }

  render(): JSX.Element {
    return (
      <NotebookGenerationPopover
        initialShowInChat={this._options.initialShowInChat}
        onSubmit={this._options.onSubmit}
        onClose={this._options.onClose}
      />
    );
  }

  private _options: {
    initialShowInChat: boolean;
    onSubmit: (prompt: string, showInChat: boolean) => void;
    onClose: () => void;
  };
}

class NotebookGenerationToolbarController {
  constructor(
    options: INotebookGenerationToolbarOptions,
    panel: NotebookPanel
  ) {
    this._app = options.app;
    this._chatSidebarId = options.chatSidebarId;
    this._panel = panel;
  }

  openPopover(button: ToolbarButton): void {
    if (this._popover) {
      this.closePopover();
      return;
    }
    const buttonRect = button.node.getBoundingClientRect();
    this._popover = new NotebookGenerationPopoverWidget({
      initialShowInChat: NotebookGenerationToolbarController._showInChat,
      onSubmit: (prompt, showInChat) => {
        NotebookGenerationToolbarController._showInChat = showInChat;
        this._submitPrompt(prompt, showInChat);
        this.closePopover();
      },
      onClose: () => this.closePopover()
    });
    Widget.attach(this._popover, document.body);
    // ReactWidget renders its tree in response to an update-request message;
    // Widget.attach by itself doesn't queue one, so without this call the
    // popover host appears empty in the DOM.
    this._popover.update();
    this._popover.positionAt(buttonRect);
  }

  closePopover(): void {
    if (!this._popover) {
      return;
    }
    this._popover.dispose();
    this._popover = null;
  }

  dispose(): void {
    this.closePopover();
    if (this._activeProgressRequestId) {
      document.removeEventListener(
        NOTEBOOK_GENERATION_PROGRESS_EVENT,
        this._onProgress
      );
      this._activeProgressRequestId = null;
    }
    this._setStatus(null);
  }

  private _submitPrompt(rawPrompt: string, showInChat: boolean): void {
    const prefixedPrompt = buildNotebookGenerationPrompt(rawPrompt);
    const externalRequestId = UUID.uuid4();
    const request: Partial<IRunChatCompletionRequest> = {
      type: RunChatCompletionType.NotebookGeneration,
      content: prefixedPrompt,
      chatMode: '',
      externalRequestId,
      hideInChat: !showInChat
    };

    if (!showInChat) {
      this._setStatus('Generating notebook…');
      this._activeProgressRequestId = externalRequestId;
      document.addEventListener(
        NOTEBOOK_GENERATION_PROGRESS_EVENT,
        this._onProgress
      );
    }

    document.dispatchEvent(
      new CustomEvent('copilotSidebar:runPrompt', { detail: request })
    );

    if (showInChat) {
      this._app.commands.execute('tabsmenu:activate-by-id', {
        id: this._chatSidebarId
      });
    }
  }

  private _onProgress = (event: Event): void => {
    const detail = (event as CustomEvent<INotebookGenerationProgressDetail>)
      .detail;
    if (!detail || detail.requestId !== this._activeProgressRequestId) {
      return;
    }
    if (!detail.inProgress) {
      document.removeEventListener(
        NOTEBOOK_GENERATION_PROGRESS_EVENT,
        this._onProgress
      );
      this._activeProgressRequestId = null;
      if (detail.error) {
        this._setStatus(`Generation failed: ${detail.error}`);
        setTimeout(() => this._setStatus(null), 4000);
      } else {
        this._setStatus('Notebook generation complete');
        setTimeout(() => this._setStatus(null), 2500);
      }
    }
  };

  private _setStatus(message: string | null): void {
    if (this._panel.isDisposed) {
      return;
    }
    if (!message) {
      if (this._statusEl && this._statusEl.parentElement) {
        this._statusEl.parentElement.removeChild(this._statusEl);
      }
      this._statusEl = null;
      return;
    }
    if (!this._statusEl) {
      this._statusEl = document.createElement('div');
      this._statusEl.className = 'nbi-notebook-generation-status';
      this._panel.toolbar.node.appendChild(this._statusEl);
    }
    this._statusEl.textContent = message;
  }

  // Persist the toggle across popover invocations (per-tab). The issue
  // requires the toggle to default ON the first time, then remember the
  // user's last choice.
  private static _showInChat = true;

  private _app: JupyterFrontEnd;
  private _chatSidebarId: string;
  private _panel: NotebookPanel;
  private _popover: NotebookGenerationPopoverWidget | null = null;
  private _activeProgressRequestId: string | null = null;
  private _statusEl: HTMLDivElement | null = null;
}

export class NotebookGenerationToolbarExtension
  implements DocumentRegistry.IWidgetExtension<NotebookPanel, INotebookModel>
{
  constructor(options: INotebookGenerationToolbarOptions) {
    this._options = options;
  }

  createNew(
    panel: NotebookPanel,
    _context: DocumentRegistry.IContext<INotebookModel>
  ): IDisposable {
    const controller = new NotebookGenerationToolbarController(
      this._options,
      panel
    );
    const button: ToolbarButton = new ToolbarButton({
      icon: this._options.icon,
      onClick: () => controller.openPopover(button),
      tooltip: 'Generate or update notebook with AI'
    });
    button.addClass('nbi-notebook-generation-toolbar-button');
    panel.toolbar.insertItem(TOOLBAR_BUTTON_RANK, TOOLBAR_BUTTON_NAME, button);
    return new DisposableDelegate(() => {
      controller.dispose();
      button.dispose();
    });
  }

  private _options: INotebookGenerationToolbarOptions;
}

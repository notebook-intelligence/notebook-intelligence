// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { JupyterFrontEnd } from '@jupyterlab/application';
import { Dialog, showDialog } from '@jupyterlab/apputils';
import { DocumentRegistry } from '@jupyterlab/docregistry';
import { INotebookModel, NotebookPanel } from '@jupyterlab/notebook';
import { LabIcon, ToolbarButton } from '@jupyterlab/ui-components';
import { IDisposable, DisposableDelegate } from '@lumino/disposable';

import {
  exportChatbookNotebookAsCode,
  isChatbookSession,
  nextChatbookNotebookMode,
  toggleAllChatbookCellModes
} from './chatbook';
import { NBIAPI } from './api';
import {
  NotebookKernelNotFoundError,
  findKernelProfile,
  resolveChatbookBackendProfile,
  sharedKernelSpecManager
} from './notebook-kernels';
import switchSvgstr from '../style/icons/chatbook-switch.svg';
import exportSvgstr from '../style/icons/chatbook-export.svg';

const SHOW_CODE_BUTTON_NAME = 'nbi-chatbook-show-code';
const CONVERT_BUTTON_NAME = 'nbi-chatbook-convert';

const switchIcon = new LabIcon({
  name: 'notebook-intelligence:chatbook-switch',
  svgstr: switchSvgstr
});
const exportIcon = new LabIcon({
  name: 'notebook-intelligence:chatbook-export',
  svgstr: exportSvgstr
});

export async function confirmConvertChatbookNotebook(
  app: JupyterFrontEnd,
  panel: NotebookPanel
): Promise<void> {
  if (!isChatbookSession(panel.sessionContext)) {
    return;
  }
  const kernels = sharedKernelSpecManager();
  await kernels.ready;
  const profile = resolveChatbookBackendProfile(
    kernels.specs?.kernelspecs,
    NBIAPI.config.chatbookBackendKernel
  );
  const result = await showDialog({
    title: `Export as ${profile.language} notebook`,
    body: `Create a new ${profile.displayName} notebook from this Chatbook. The original Chatbook is left unchanged. Cells that have not been run become comments.`,
    buttons: [Dialog.cancelButton(), Dialog.okButton({ label: 'Export' })]
  });
  if (!result.button.accept) {
    return;
  }

  let resolved = profile;
  try {
    resolved = findKernelProfile(kernels.specs?.kernelspecs, {
      kernelName: profile.kernelName
    });
  } catch (error) {
    if (error instanceof NotebookKernelNotFoundError) {
      void app.commands.execute('apputils:notify', {
        message: error.message,
        type: 'error',
        options: { autoClose: true }
      });
      return;
    }
    throw error;
  }

  let path: string;
  try {
    path = await exportChatbookNotebookAsCode(
      panel,
      resolved,
      app.serviceManager.contents
    );
  } catch (error) {
    void app.commands.execute('apputils:notify', {
      message: `Export failed: ${error instanceof Error ? error.message : error}`,
      type: 'error',
      options: { autoClose: true }
    });
    return;
  }
  await app.commands.execute('docmanager:open', { path });
  void app.commands.execute('apputils:notify', {
    message: `Exported ${path}`,
    type: 'success',
    options: { autoClose: true }
  });
}

class ChatbookToolbarController {
  constructor(app: JupyterFrontEnd, panel: NotebookPanel) {
    this._app = app;
    this._panel = panel;
    this._showCodeButton = new ToolbarButton({
      icon: switchIcon,
      tooltip: 'Switch every cell to code',
      onClick: () => {
        toggleAllChatbookCellModes(this._panel);
        this.sync();
      }
    });
    this._showCodeButton.addClass('nbi-chatbook-toolbar-button');
    this._convertButton = new ToolbarButton({
      icon: exportIcon,
      tooltip: 'Export as a notebook for the Chatbook backend kernel',
      onClick: () => {
        void confirmConvertChatbookNotebook(this._app, this._panel);
      }
    });
    this._convertButton.addClass('nbi-chatbook-toolbar-button');
    this._showCodeButton.hide();
    this._convertButton.hide();
    panel.toolbar.insertAfter(
      'cellType',
      SHOW_CODE_BUTTON_NAME,
      this._showCodeButton
    );
    panel.toolbar.insertAfter(
      SHOW_CODE_BUTTON_NAME,
      CONVERT_BUTTON_NAME,
      this._convertButton
    );
    panel.sessionContext.kernelChanged.connect(this.sync, this);
    panel.sessionContext.sessionChanged.connect(this.sync, this);
    void panel.sessionContext.ready.then(() => this.sync());
    this.sync();
  }

  sync(): void {
    const isChatbook = isChatbookSession(this._panel.sessionContext);
    if (isChatbook && !this._contentChangedConnected) {
      this._panel.model?.contentChanged.connect(this._scheduleSync);
      this._contentChangedConnected = true;
    } else if (!isChatbook && this._contentChangedConnected) {
      this._panel.model?.contentChanged.disconnect(this._scheduleSync);
      this._contentChangedConnected = false;
    }
    if (isChatbook) {
      this._showCodeButton.show();
      this._convertButton.show();
    } else {
      this._showCodeButton.hide();
      this._convertButton.hide();
    }
    const allPython = nextChatbookNotebookMode(this._panel) === 'prompt';
    this._showCodeButton.pressed = allPython;
    this._showCodeButton.node.title = allPython
      ? 'Switch every cell to natural language'
      : 'Switch every cell to code';
  }

  dispose(): void {
    this._panel.sessionContext.kernelChanged.disconnect(this.sync, this);
    this._panel.sessionContext.sessionChanged.disconnect(this.sync, this);
    if (this._contentChangedConnected) {
      this._panel.model?.contentChanged.disconnect(this._scheduleSync);
      this._contentChangedConnected = false;
    }
    if (this._syncFrame) {
      cancelAnimationFrame(this._syncFrame);
      this._syncFrame = 0;
    }
    this._showCodeButton.dispose();
    this._convertButton.dispose();
  }

  private _app: JupyterFrontEnd;
  private _panel: NotebookPanel;
  private _showCodeButton: ToolbarButton;
  private _convertButton: ToolbarButton;
  private _contentChangedConnected = false;
  private _syncFrame = 0;
  private _scheduleSync = (): void => {
    if (this._syncFrame) {
      return;
    }
    this._syncFrame = requestAnimationFrame(() => {
      this._syncFrame = 0;
      this.sync();
    });
  };
}

export class ChatbookToolbarExtension
  implements DocumentRegistry.IWidgetExtension<NotebookPanel, INotebookModel>
{
  constructor(app: JupyterFrontEnd) {
    this._app = app;
  }

  createNew(
    panel: NotebookPanel,
    _context: DocumentRegistry.IContext<INotebookModel>
  ): IDisposable {
    const controller = new ChatbookToolbarController(this._app, panel);
    return new DisposableDelegate(() => {
      controller.dispose();
    });
  }

  private _app: JupyterFrontEnd;
}

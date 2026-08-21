// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { JupyterFrontEnd } from '@jupyterlab/application';
import { Dialog, showDialog } from '@jupyterlab/apputils';
import { DocumentRegistry } from '@jupyterlab/docregistry';
import { INotebookModel, NotebookPanel } from '@jupyterlab/notebook';
import { KernelSpecManager } from '@jupyterlab/services';
import { LabIcon, ToolbarButton } from '@jupyterlab/ui-components';
import { IDisposable, DisposableDelegate } from '@lumino/disposable';

import {
  CHATBOOK_CONVERT_TARGETS,
  exportChatbookNotebookAsPython,
  isChatbookSession,
  nextChatbookNotebookMode,
  toggleAllChatbookCellModes
} from './chatbook';
import {
  NotebookKernelNotFoundError,
  findKernelProfile
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
  const target = CHATBOOK_CONVERT_TARGETS.python;
  const result = await showDialog({
    title: `Export as ${target.label}`,
    body: 'Create a new Python notebook from this Chatbook. The original Chatbook is left unchanged. Cells that have not been run become comments.',
    buttons: [Dialog.cancelButton(), Dialog.okButton({ label: 'Export' })]
  });
  if (!result.button.accept) {
    return;
  }

  const kernels = new KernelSpecManager();
  await kernels.ready;
  let profile;
  try {
    try {
      profile = findKernelProfile(kernels.specs?.kernelspecs, {
        kernelName: target.defaultKernelName
      });
    } catch (error) {
      if (!(error instanceof NotebookKernelNotFoundError)) {
        throw error;
      }
      profile = findKernelProfile(kernels.specs?.kernelspecs, {
        language: target.language
      });
    }
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

  const path = await exportChatbookNotebookAsPython(
    panel,
    profile,
    app.serviceManager.contents
  );
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
      tooltip: 'Switch every cell to Python',
      onClick: () => {
        void toggleAllChatbookCellModes(this._panel).finally(() => this.sync());
      }
    });
    this._showCodeButton.addClass('nbi-chatbook-toolbar-button');
    this._convertButton = new ToolbarButton({
      icon: exportIcon,
      tooltip: 'Export as a new Python notebook',
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
    panel.model?.contentChanged.connect(this.sync, this);
    void panel.sessionContext.ready.then(() => this.sync());
    this.sync();
  }

  sync(): void {
    const isChatbook = isChatbookSession(this._panel.sessionContext);
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
      : 'Switch every cell to Python';
  }

  dispose(): void {
    this._panel.sessionContext.kernelChanged.disconnect(this.sync, this);
    this._panel.sessionContext.sessionChanged.disconnect(this.sync, this);
    this._panel.model?.contentChanged.disconnect(this.sync, this);
    this._showCodeButton.dispose();
    this._convertButton.dispose();
  }

  private _app: JupyterFrontEnd;
  private _panel: NotebookPanel;
  private _showCodeButton: ToolbarButton;
  private _convertButton: ToolbarButton;
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

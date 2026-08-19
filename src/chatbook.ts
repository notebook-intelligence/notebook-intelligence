// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { CodeCell } from '@jupyterlab/cells';
import { IEditorLanguageRegistry } from '@jupyterlab/codemirror';
import { ISessionContext } from '@jupyterlab/apputils';
import { INotebookTracker, NotebookPanel } from '@jupyterlab/notebook';
import { Kernel } from '@jupyterlab/services';
import { JSONObject } from '@lumino/coreutils';
import { IDisposable } from '@lumino/disposable';

import {
  CHATBOOK_LANGUAGE,
  CHATBOOK_MSG_TYPE,
  buildExecuteChatbookMeta,
  getChatbookCellMeta,
  getNotebookNuiSessionId,
  isChatbookKernelName,
  mergeChatbookCellMeta,
  mergeNotebookNuiSessionId,
  sha256Hex,
  type IChatbookCellMeta
} from './chatbook-core';

export {
  CHATBOOK_KERNEL_NAME,
  CHATBOOK_LANGUAGE,
  CHATBOOK_MSG_TYPE,
  getChatbookCellMeta,
  isChatbookKernelName
} from './chatbook-core';

let codeCellExecutePatched = false;

export function isChatbookSession(
  sessionContext: ISessionContext | null | undefined
): boolean {
  if (!sessionContext) {
    return false;
  }
  const kernelName =
    sessionContext.session?.kernel?.name ||
    sessionContext.kernelPreference?.name ||
    '';
  return isChatbookKernelName(kernelName);
}

export function registerChatbookLanguage(
  languageRegistry: IEditorLanguageRegistry
): void {
  if (languageRegistry.findByName(CHATBOOK_LANGUAGE)) {
    return;
  }
  const markdown =
    languageRegistry.findByName('markdown') ||
    languageRegistry.findByName('Markdown') ||
    languageRegistry.findByMIME('text/x-markdown') ||
    languageRegistry.findByMIME('text/markdown');
  languageRegistry.addLanguage({
    name: CHATBOOK_LANGUAGE,
    displayName: 'Chatbook',
    mime: 'text/x-chatbook',
    extensions: ['chatbook'],
    async load() {
      if (markdown) {
        return markdown.load();
      }
      throw new Error('Markdown CodeMirror language is not registered');
    }
  });
}

export function patchCodeCellExecute(): void {
  if (codeCellExecutePatched) {
    return;
  }
  codeCellExecutePatched = true;
  const original = CodeCell.execute.bind(CodeCell);
  CodeCell.execute = async (
    cell: CodeCell,
    sessionContext: ISessionContext,
    metadata?: JSONObject
  ) => {
    if (!isChatbookSession(sessionContext)) {
      return original(cell, sessionContext, metadata);
    }
    const prompt = cell.model.sharedModel.getSource();
    const promptHash = await sha256Hex(prompt);
    const cellMeta = getChatbookCellMeta(cell.model.metadata);
    const notebook = cell.parent?.parent as NotebookPanel | undefined;
    const notebookMeta = notebook?.model?.metadata;
    const nuiSessionId = getNotebookNuiSessionId(notebookMeta);
    const nbiChatbook: JSONObject = {
      ...buildExecuteChatbookMeta({
        cellId: cell.model.id,
        prompt,
        promptHash,
        cellMeta,
        nuiSessionId
      })
    };
    return original(cell, sessionContext, {
      ...(metadata || {}),
      cellId:
        (typeof metadata?.cellId === 'string' && metadata.cellId) ||
        cell.model.id,
      nbi_chatbook: nbiChatbook
    });
  };
}

export function attachChatbookNotebooks(
  tracker: INotebookTracker
): IDisposable {
  const attached = new WeakSet<NotebookPanel>();

  const attach = (panel: NotebookPanel) => {
    if (attached.has(panel)) {
      return;
    }
    attached.add(panel);
    let kernelConnection: Kernel.IKernelConnection | null = null;
    const onAnyMessage = (
      _sender: Kernel.IKernelConnection,
      args: Kernel.IAnyMessageArgs
    ) => {
      if (args.direction !== 'recv') {
        return;
      }
      const msg = args.msg;
      if ((msg.header.msg_type as string) !== CHATBOOK_MSG_TYPE) {
        return;
      }
      applyChatbookPayload(panel, msg.content as Record<string, unknown>);
    };
    const connectKernel = () => {
      if (kernelConnection) {
        kernelConnection.anyMessage.disconnect(onAnyMessage);
        kernelConnection = null;
      }
      const kernel = panel.sessionContext.session?.kernel;
      if (!kernel || !isChatbookKernelName(kernel.name)) {
        return;
      }
      kernelConnection = kernel;
      kernel.anyMessage.connect(onAnyMessage);
    };
    panel.sessionContext.kernelChanged.connect(connectKernel);
    void panel.sessionContext.ready.then(connectKernel);
    panel.disposed.connect(() => {
      if (kernelConnection) {
        kernelConnection.anyMessage.disconnect(onAnyMessage);
        kernelConnection = null;
      }
    });
  };

  tracker.widgetAdded.connect((_, panel) => {
    attach(panel);
  });
  tracker.forEach(panel => {
    attach(panel);
  });

  return {
    dispose: () => {
      /* listeners are tied to panel lifetime */
    },
    get isDisposed() {
      return false;
    }
  };
}

function applyChatbookPayload(
  panel: NotebookPanel,
  content: Record<string, unknown>
): void {
  const cellId = String(content.cellId || '');
  const patch: IChatbookCellMeta = {
    generatedCode: String(content.generatedCode || ''),
    promptHash: content.promptHash ? String(content.promptHash) : undefined,
    nuiSessionId: content.nuiSessionId
      ? String(content.nuiSessionId)
      : undefined,
    nuiRunId: content.nuiRunId ? String(content.nuiRunId) : undefined,
    generatedAt: content.generatedAt ? String(content.generatedAt) : undefined,
    cacheHit: Boolean(content.cacheHit)
  };
  if (!patch.generatedCode) {
    return;
  }

  const cells = panel.content.widgets;
  const cell = cellId
    ? cells.find(widget => widget.model.id === cellId)
    : panel.content.activeCell;
  if (cell) {
    const merged = mergeChatbookCellMeta(cell.model.metadata, patch);
    if (merged.nbi) {
      cell.model.setMetadata('nbi', merged.nbi);
    }
  }

  if (patch.nuiSessionId && panel.model) {
    const notebookMerged = mergeNotebookNuiSessionId(
      panel.model.metadata,
      patch.nuiSessionId
    );
    const nbi = notebookMerged.nbi;
    if (nbi) {
      panel.model.setMetadata('nbi', nbi);
    }
  }
}

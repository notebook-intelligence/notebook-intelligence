// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

/**
 * Minimal structural view of a notebook panel, so the origin check can be
 * exercised without building a full NotebookPanel.
 */
export interface IInlineCompletionNotebook {
  isDisposed?: boolean;
  content: {
    activeCell?: {
      model: {
        id: string;
        sharedModel: { getSource(): string };
      };
    } | null;
  };
}

export interface IInlineCompletionOrigin {
  notebook: IInlineCompletionNotebook | null;
  cellId: string | null;
  source: string;
}

export function captureInlineCompletionOrigin(
  notebook: IInlineCompletionNotebook | null,
  requestText: string
): IInlineCompletionOrigin {
  return {
    notebook: notebook ?? null,
    cellId: notebook?.content.activeCell?.model.id ?? null,
    source: requestText
  };
}

/**
 * A completion request can outlive the editor state that produced it. Running
 * a cell moves focus to the next one, and JupyterLab's completion handler only
 * invalidates a pending inline request when a newer request is made, not when
 * the active editor changes, so a late reply is rendered as ghost text in
 * whichever cell the cursor landed in. Results are dropped unless the cell they
 * were requested for is still active and still holds the same text.
 */
export function isInlineCompletionOriginCurrent(
  origin: IInlineCompletionOrigin
): boolean {
  const notebook = origin.notebook;
  if (!notebook) {
    return true;
  }
  if (notebook.isDisposed) {
    return false;
  }
  const activeCell = notebook.content.activeCell;
  if (!activeCell || activeCell.model.id !== origin.cellId) {
    return false;
  }
  return activeCell.model.sharedModel.getSource() === origin.source;
}

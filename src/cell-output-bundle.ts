// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { CodeCell } from '@jupyterlab/cells';
import {
  formatJupyterError,
  getTokenCount,
  truncateToTokenCount
} from './utils';
import { IOutputContextItem } from './tokens';

export const MAX_TOKENS_PER_OUTPUT = 4000;
export const MAX_TOKENS_PER_TURN = 8000;
// Above this raw base64 length (~150 KiB decoded), we drop the image and
// emit a placeholder rather than blow up the context window.
const MAX_IMAGE_BASE64_BYTES = 200_000;

export type IMimeBundle = IOutputContextItem['mimeBundles'][number];
export type IOutputContextBundle = IOutputContextItem;

export interface IBundleOptions {
  maxTokensPerOutput?: number;
  maxTokensPerTurn?: number;
  supportsVision?: boolean;
}

interface ICellLike {
  outputArea: { model: { toJSON: () => any[] } };
  model?: { sharedModel?: { getSource: () => string } };
}

export function cellOutputAsContextBundle(
  cell: CodeCell | ICellLike,
  opts: IBundleOptions = {}
): IOutputContextBundle {
  const maxOut = opts.maxTokensPerOutput ?? MAX_TOKENS_PER_OUTPUT;
  const maxTurn = opts.maxTokensPerTurn ?? MAX_TOKENS_PER_TURN;
  const supportsVision = opts.supportsVision ?? false;

  const cellLike = cell as ICellLike;
  const cellSource = cellLike.model?.sharedModel?.getSource() ?? '';
  const outputs = cellLike.outputArea.model.toJSON();

  const mimeBundles: IMimeBundle[] = [];
  let truncated = false;
  let isError = false;
  let totalTokens = 0;

  const remaining = (): number => Math.max(0, maxTurn - totalTokens);

  const pushBundle = (mimeType: string, data: string): void => {
    let text = data;
    let size = getTokenCount(text);
    const perOutCap = Math.min(maxOut, remaining());
    if (size > perOutCap) {
      ({ text, size } = truncateToTokenCount(text, perOutCap));
      truncated = true;
    }
    if (size === 0 && text.length === 0) {
      return;
    }
    mimeBundles.push({ mimeType, data: text, sizeTokens: size });
    totalTokens += size;
  };

  for (const output of outputs) {
    if (remaining() === 0) {
      truncated = true;
      break;
    }

    if (output.output_type === 'error') {
      isError = true;
      pushBundle('application/vnd.jupyter.error', formatJupyterError(output));
      continue;
    }

    if (output.output_type === 'stream') {
      pushBundle('text/plain', String(output.text ?? ''));
      continue;
    }

    // execute_result and display_data
    const data = (output.data ?? {}) as Record<string, any>;

    // DataFrames render as both text/html and text/plain; the plain form is the
    // formatted ASCII table — cheaper, and an LLM reads it just fine.
    if (data['text/html'] && data['text/plain']) {
      pushBundle('text/plain', String(data['text/plain']));
    } else if (data['text/plain']) {
      pushBundle('text/plain', String(data['text/plain']));
    } else if (data['text/html']) {
      pushBundle('text/html', stripHtml(String(data['text/html'])));
    }

    if (data['application/vnd.plotly.v1+json']) {
      pushBundle(
        'application/vnd.plotly.v1+json',
        summarizePlotly(data['application/vnd.plotly.v1+json'])
      );
    } else if (data['application/json']) {
      pushBundle(
        'application/json',
        JSON.stringify(data['application/json'], null, 2)
      );
    }

    for (const imgMime of ['image/png', 'image/jpeg']) {
      if (!data[imgMime]) {
        continue;
      }
      const raw = String(data[imgMime]);
      if (!supportsVision) {
        pushBundle(imgMime, '<image omitted: model lacks vision support>');
      } else if (raw.length > MAX_IMAGE_BASE64_BYTES) {
        pushBundle(imgMime, '<image omitted: too large for inline attachment>');
        truncated = true;
      } else if (!isValidBase64(raw)) {
        pushBundle(imgMime, '<image omitted: invalid base64 payload>');
      } else {
        // Send raw base64; the server constructs the data URL after
        // re-validating, so a forged POST can't inject markdown.
        pushBundle(imgMime, raw);
      }
    }
  }

  return { cellSource, mimeBundles, isError, truncated };
}

function isValidBase64(s: string): boolean {
  // Standard base64 alphabet only. No URL-safe chars; no characters that
  // could break out of a markdown image URL on the server side.
  const compact = s.replace(/\s+/g, '');
  return /^[A-Za-z0-9+/]+=*$/.test(compact);
}

function stripHtml(html: string): string {
  // NOTE: not a sanitizer. Output is intended only for LLM context; never
  // route this back into the DOM without a real sanitizer.
  // Replace block-level closers with a newline so structure survives, then
  // strip the remaining tags. Cheap and good enough for LLM consumption.
  return html
    .replace(/<\/(p|div|tr|li|h[1-6]|br)\s*\/?>/gi, '\n')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function summarizePlotly(payload: any): string {
  const lines: string[] = [];
  const traces = Array.isArray(payload?.data) ? payload.data : [];
  const types = traces.map((t: any) => t?.type ?? 'trace').join(', ');
  if (types) {
    lines.push(`Plotly figure traces: ${types}`);
  }
  const layout = payload?.layout ?? {};
  if (layout.title?.text) {
    lines.push(`Title: ${layout.title.text}`);
  }
  if (layout.xaxis?.title?.text) {
    lines.push(`X axis: ${layout.xaxis.title.text}`);
  }
  if (layout.yaxis?.title?.text) {
    lines.push(`Y axis: ${layout.yaxis.title.text}`);
  }
  return lines.join('\n');
}

// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { KernelSpec, KernelSpecManager } from '@jupyterlab/services';

export interface INotebookKernelProfile {
  language: string;
  kernelName: string;
  displayName: string;
}

export class NotebookKernelNotFoundError extends Error {
  readonly requestedLanguage: string;
  readonly requestedKernelName: string;

  constructor(options?: { language?: string; kernelName?: string }) {
    const requestedLanguage = normalizeNotebookLanguage(options?.language);
    const requestedKernelName = (options?.kernelName ?? '').trim();
    const detail = requestedKernelName
      ? `kernel "${requestedKernelName}"`
      : `language "${requestedLanguage}"`;
    super(`No installed Jupyter kernel matches ${detail}.`);
    this.name = 'NotebookKernelNotFoundError';
    this.requestedLanguage = requestedLanguage;
    this.requestedKernelName = requestedKernelName;
  }
}

export const DEFAULT_NOTEBOOK_KERNEL: INotebookKernelProfile = Object.freeze({
  language: 'python',
  kernelName: 'python3',
  displayName: 'Python 3 (ipykernel)'
});

export const CHATBOOK_KERNEL: INotebookKernelProfile = Object.freeze({
  language: 'chatbook',
  kernelName: 'chatbook',
  displayName: 'Chatbook'
});

let sharedSpecs: KernelSpecManager | null = null;

/**
 * One KernelSpecManager for Chatbook UI. Each `new KernelSpecManager()`
 * starts a poller until `dispose()`, so constructing one per config change
 * leaks GET /api/kernelspecs forever.
 */
export function sharedKernelSpecManager(): KernelSpecManager {
  if (!sharedSpecs || sharedSpecs.isDisposed) {
    sharedSpecs = new KernelSpecManager();
  }
  return sharedSpecs;
}

export function normalizeNotebookLanguage(raw: string | undefined): string {
  const language = (raw ?? '').trim().toLowerCase();
  if (!language) {
    return DEFAULT_NOTEBOOK_KERNEL.language;
  }
  if (language === 'py') {
    return 'python';
  }
  return language;
}

export function findKernelProfile(
  specs: Record<string, KernelSpec.ISpecModel> | undefined,
  options?: { language?: string; kernelName?: string }
): INotebookKernelProfile {
  const requestedKernelName = (options?.kernelName ?? '').trim();
  if (requestedKernelName) {
    const spec = specs?.[requestedKernelName];
    if (!spec) {
      throw new NotebookKernelNotFoundError(options);
    }
    return {
      language: normalizeNotebookLanguage(spec.language),
      kernelName: spec.name,
      displayName: spec.display_name
    };
  }

  const requestedLanguage = normalizeNotebookLanguage(options?.language);
  if (specs) {
    for (const key of Object.keys(specs)) {
      const spec = specs[key];
      if (normalizeNotebookLanguage(spec.language) === requestedLanguage) {
        return {
          language: normalizeNotebookLanguage(spec.language),
          kernelName: spec.name,
          displayName: spec.display_name
        };
      }
    }
  }
  if (!specs && requestedLanguage === DEFAULT_NOTEBOOK_KERNEL.language) {
    return DEFAULT_NOTEBOOK_KERNEL;
  }

  throw new NotebookKernelNotFoundError(options);
}

export function listKernelProfiles(
  specs: Record<string, KernelSpec.ISpecModel> | undefined
): INotebookKernelProfile[] {
  if (!specs) {
    return [];
  }

  return Object.keys(specs)
    .sort((lhs, rhs) => lhs.localeCompare(rhs))
    .map(kernelName => {
      const spec = specs[kernelName];
      return {
        language: normalizeNotebookLanguage(spec.language),
        kernelName: spec.name,
        displayName: spec.display_name
      };
    });
}

export function listChatbookBackendProfiles(
  specs: Record<string, KernelSpec.ISpecModel> | undefined
): INotebookKernelProfile[] {
  return listKernelProfiles(specs).filter(
    profile => profile.kernelName !== CHATBOOK_KERNEL.kernelName
  );
}

export function resolveChatbookBackendProfile(
  specs: Record<string, KernelSpec.ISpecModel> | undefined,
  preferredKernelName?: string
): INotebookKernelProfile {
  const backends = listChatbookBackendProfiles(specs);
  const wanted = (preferredKernelName ?? '').trim();
  const named =
    wanted && wanted !== CHATBOOK_KERNEL.kernelName
      ? backends.find(profile => profile.kernelName === wanted)
      : undefined;
  if (named) {
    return named;
  }
  const python3 = backends.find(
    profile => profile.kernelName === DEFAULT_NOTEBOOK_KERNEL.kernelName
  );
  if (python3) {
    return python3;
  }
  const python = backends.find(
    profile => profile.language === DEFAULT_NOTEBOOK_KERNEL.language
  );
  if (python) {
    return python;
  }
  if (backends[0]) {
    return backends[0];
  }
  return DEFAULT_NOTEBOOK_KERNEL;
}

export function mimeTypeForNotebookLanguage(language: string): string {
  const lang = normalizeNotebookLanguage(language);
  const mimeByLanguage: Record<string, string> = {
    python: 'text/x-python',
    r: 'text/x-rsrc',
    julia: 'text/x-julia',
    javascript: 'text/javascript',
    typescript: 'text/typescript',
    scala: 'text/x-scala',
    sql: 'text/x-sql'
  };
  return mimeByLanguage[lang] || `text/x-${lang}`;
}

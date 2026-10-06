// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  isChatbookKernelName,
  isChatbookPromptInlineCompletion,
  setChatbookKernelSpecs
} from '../../src/chatbook-core';
import {
  chatbookKernelProfile,
  listChatbookBackendProfiles,
  resolveChatbookBackendProfile
} from '../../src/notebook-kernels';

// How nb_conda_kernels lists the kernels of a conda environment: Chatbook
// keeps its language under a new name, and there is no `python3`.
const CONDA_SPECS = {
  'conda-base-chatbook': {
    name: 'conda-base-chatbook',
    language: 'chatbook',
    display_name: 'Chatbook [conda env:base] *'
  },
  'conda-base-py': {
    name: 'conda-base-py',
    language: 'python',
    display_name: 'Python [conda env:base] *'
  },
  'conda-base-ir': {
    name: 'conda-base-ir',
    language: 'R',
    display_name: 'R [conda env:base] *'
  }
} as any;

const PLAIN_SPECS = {
  chatbook: {
    name: 'chatbook',
    language: 'chatbook',
    display_name: 'Chatbook'
  },
  python3: { name: 'python3', language: 'python', display_name: 'Python 3' }
} as any;

describe('Chatbook kernelspecs under other names', () => {
  afterEach(() => {
    setChatbookKernelSpecs(undefined);
  });

  it('recognizes a kernelspec in Chatbook language whatever it is named', () => {
    expect(isChatbookKernelName('conda-base-chatbook')).toBe(false);

    setChatbookKernelSpecs(CONDA_SPECS);

    expect(isChatbookKernelName('conda-base-chatbook')).toBe(true);
    expect(isChatbookKernelName('chatbook')).toBe(true);
    expect(isChatbookKernelName('conda-base-py')).toBe(false);
    expect(isChatbookPromptInlineCompletion('conda-base-chatbook')).toBe(true);
  });

  it('forgets a renamed Chatbook once it is no longer installed', () => {
    setChatbookKernelSpecs(CONDA_SPECS);
    setChatbookKernelSpecs(PLAIN_SPECS);

    expect(isChatbookKernelName('conda-base-chatbook')).toBe(false);
    expect(isChatbookKernelName('chatbook')).toBe(true);
  });

  it('never offers a renamed Chatbook as its own backend', () => {
    expect(
      listChatbookBackendProfiles(CONDA_SPECS).map(p => p.kernelName)
    ).toEqual(['conda-base-ir', 'conda-base-py']);
    expect(resolveChatbookBackendProfile(CONDA_SPECS).kernelName).toBe(
      'conda-base-py'
    );
    expect(
      resolveChatbookBackendProfile(CONDA_SPECS, 'conda-base-chatbook')
        .kernelName
    ).toBe('conda-base-py');
  });

  it('opens a new Chatbook with the installed Chatbook kernelspec', () => {
    expect(chatbookKernelProfile(CONDA_SPECS).kernelName).toBe(
      'conda-base-chatbook'
    );
    expect(
      chatbookKernelProfile({ ...CONDA_SPECS, ...PLAIN_SPECS }).kernelName
    ).toBe('chatbook');
    expect(chatbookKernelProfile(undefined).kernelName).toBe('chatbook');
  });
});

// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Centralized command-id constants for the JupyterLab plugin. Hoisted out
// of `src/index.ts` so non-JL modules (and Jest tests) can import them
// without pulling the JupyterLab packages into their dependency graph.

export namespace CommandIDs {
  export const chatuserInput = 'notebook-intelligence:chat-user-input';
  export const insertAtCursor = 'notebook-intelligence:insert-at-cursor';
  export const addCodeAsNewCell = 'notebook-intelligence:add-code-as-new-cell';
  export const createNewFile = 'notebook-intelligence:create-new-file';
  export const createNewNotebook = 'notebook-intelligence:create-new-notebook';
  export const listAvailableNotebookKernels =
    'notebook-intelligence:list-available-notebook-kernels';
  export const renameNotebook = 'notebook-intelligence:rename-notebook';
  export const addCodeCellToNotebook =
    'notebook-intelligence:add-code-cell-to-notebook';
  export const addMarkdownCellToNotebook =
    'notebook-intelligence:add-markdown-cell-to-notebook';
  export const editorGenerateCode =
    'notebook-intelligence:editor-generate-code';
  export const editorExplainThisCode =
    'notebook-intelligence:editor-explain-this-code';
  export const editorFixThisCode = 'notebook-intelligence:editor-fix-this-code';
  export const editorExplainThisOutput =
    'notebook-intelligence:editor-explain-this-output';
  export const editorTroubleshootThisOutput =
    'notebook-intelligence:editor-troubleshoot-this-output';
  export const editorAskAboutThisOutput =
    'notebook-intelligence:editor-ask-about-this-output';
  export const openGitHubCopilotLoginDialog =
    'notebook-intelligence:open-github-copilot-login-dialog';
  export const openConfigurationDialog =
    'notebook-intelligence:open-configuration-dialog';
  export const addMarkdownCellToActiveNotebook =
    'notebook-intelligence:add-markdown-cell-to-active-notebook';
  export const addCodeCellToActiveNotebook =
    'notebook-intelligence:add-code-cell-to-active-notebook';
  export const deleteCellAtIndex = 'notebook-intelligence:delete-cell-at-index';
  export const insertCellAtIndex = 'notebook-intelligence:insert-cell-at-index';
  export const getCellTypeAndSource =
    'notebook-intelligence:get-cell-type-and-source';
  export const setCellTypeAndSource =
    'notebook-intelligence:set-cell-type-and-source';
  export const getNumberOfCells = 'notebook-intelligence:get-number-of-cells';
  export const getCellOutput = 'notebook-intelligence:get-cell-output';
  export const runCellAtIndex = 'notebook-intelligence:run-cell-at-index';
  export const getCurrentFileContent =
    'notebook-intelligence:get-current-file-content';
  export const setCurrentFileContent =
    'notebook-intelligence:set-current-file-content';
  export const openMCPConfigEditor =
    'notebook-intelligence:open-mcp-config-editor';
  export const showFormInputDialog =
    'notebook-intelligence:show-form-input-dialog';
  export const runCommandInTerminal =
    'notebook-intelligence:run-command-in-terminal';
  export const openClaudeCodeLauncher =
    'notebook-intelligence:open-claude-code-launcher';
  export const openOpenCodeLauncher =
    'notebook-intelligence:open-opencode-launcher';
  export const openPiLauncher = 'notebook-intelligence:open-pi-launcher';
  export const openGitHubCopilotCliLauncher =
    'notebook-intelligence:open-github-copilot-cli-launcher';
  export const openCodexLauncher = 'notebook-intelligence:open-codex-launcher';
  export const showTour = 'notebook-intelligence:show-tour';
  export const focusChatInput = 'notebook-intelligence:focus-chat-input';
  export const createChatbookNotebook =
    'notebook-intelligence:create-chatbook-notebook';
  export const showChatbookGeneratedCode =
    'notebook-intelligence:show-chatbook-generated-code';
  export const toggleAllChatbookCellModes =
    'notebook-intelligence:toggle-all-chatbook-cell-modes';
  export const convertChatbookNotebook =
    'notebook-intelligence:convert-chatbook-notebook';
  export const toggleChatbookCellMode =
    'notebook-intelligence:toggle-chatbook-cell-mode';
  export const refreshChatbookEnglish =
    'notebook-intelligence:refresh-chatbook-english';
}

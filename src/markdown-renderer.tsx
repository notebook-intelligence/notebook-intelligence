// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import {
  oneLight,
  oneDark
} from 'react-syntax-highlighter/dist/cjs/styles/prism';
import { JupyterFrontEnd } from '@jupyterlab/application';
import { isDarkTheme } from './utils';
import { IActiveDocumentInfo } from './tokens';
import { LabIcon, copyIcon } from '@jupyterlab/ui-components';
import insertAtCursorSvgstr from '../style/icons/insert-at-cursor.svg';
import addBelowSvgstr from '../style/icons/add-below.svg';
import addNewFileSvgstr from '../style/icons/add-new-file.svg';
import addNewNotebookSvgstr from '../style/icons/add-new-notebook.svg';

const insertAtCursorIcon = new LabIcon({
  name: 'notebook-intelligence:insert-at-cursor',
  svgstr: insertAtCursorSvgstr
});

const addBelowIcon = new LabIcon({
  name: 'notebook-intelligence:add-below',
  svgstr: addBelowSvgstr
});

const addNewFileIcon = new LabIcon({
  name: 'notebook-intelligence:add-new-file',
  svgstr: addNewFileSvgstr
});

const addNewNotebookIcon = new LabIcon({
  name: 'notebook-intelligence:add-new-notebook',
  svgstr: addNewNotebookSvgstr
});

type MarkdownRendererProps = {
  children: string;
  getApp: () => JupyterFrontEnd;
  getActiveDocumentInfo(): IActiveDocumentInfo;
};

export function MarkdownRenderer({
  children: markdown,
  getApp,
  getActiveDocumentInfo
}: MarkdownRendererProps) {
  const app = getApp();
  const activeDocumentInfo = getActiveDocumentInfo();
  const isNotebook = activeDocumentInfo.filename.endsWith('.ipynb');

  return (
    <Markdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ node, inline, className, children, getApp, ...props }: any) {
          const match = /language-(\w+)/.exec(className || '');
          const codeString = String(children).replace(/\n$/, '');
          const language = match ? match[1] : 'text';

          const handleCopyClick = () => {
            navigator.clipboard.writeText(codeString);
          };

          const handleInsertAtCursorClick = () => {
            app.commands.execute('notebook-intelligence:insert-at-cursor', {
              language,
              code: codeString
            });
          };

          const handleAddCodeAsNewCell = () => {
            app.commands.execute('notebook-intelligence:add-code-as-new-cell', {
              language,
              code: codeString
            });
          };

          const handleCreateNewFileClick = () => {
            app.commands.execute('notebook-intelligence:create-new-file', {
              language,
              code: codeString
            });
          };

          const handleCreateNewNotebookClick = () => {
            app.commands.execute(
              'notebook-intelligence:create-new-notebook-from-py',
              { language, code: codeString }
            );
          };

          return !inline && match ? (
            <div>
              <div className="code-block-header">
                <div className="code-block-header-language">
                  <span>{language}</span>
                </div>
                <div
                  className="code-block-header-button"
                  onClick={() => handleCopyClick()}
                >
                  <LabIcon.resolveReact
                    icon={copyIcon}
                    elementSize={'normal'}
                    tag="div"
                    className={'code-block-header-button-icon'}
                    title="Copy to clipboard"
                  />
                </div>
                <div
                  className="code-block-header-button"
                  onClick={() => handleInsertAtCursorClick()}
                >
                  <LabIcon.resolveReact
                    icon={insertAtCursorIcon}
                    elementSize={'normal'}
                    tag="div"
                    className={'code-block-header-button-icon'}
                    title="Insert at cursor"
                  />
                </div>
                {isNotebook && (
                  <div
                    className="code-block-header-button"
                    onClick={() => handleAddCodeAsNewCell()}
                  >
                    <LabIcon.resolveReact
                      icon={addBelowIcon}
                      elementSize={'normal'}
                      tag="div"
                      className={'code-block-header-button-icon'}
                      title="Add as new cell"
                    />
                  </div>
                )}
                <div
                  className="code-block-header-button"
                  onClick={() => handleCreateNewFileClick()}
                >
                  <LabIcon.resolveReact
                    icon={addNewFileIcon}
                    elementSize={'normal'}
                    tag="div"
                    className={'code-block-header-button-icon'}
                    title="New file"
                  />
                </div>
                {language === 'python' && (
                  <div
                    className="code-block-header-button"
                    onClick={() => handleCreateNewNotebookClick()}
                  >
                    <LabIcon.resolveReact
                      icon={addNewNotebookIcon}
                      elementSize={'normal'}
                      tag="div"
                      className={'code-block-header-button-icon'}
                      title="New notebook"
                    />
                  </div>
                )}
              </div>
              <SyntaxHighlighter
                style={isDarkTheme() ? oneDark : oneLight}
                PreTag="div"
                customStyle={
                  isDarkTheme() ? { background: 'var(--jp-layout-color1)' } : ''
                }
                language={language}
                {...props}
              >
                {codeString}
              </SyntaxHighlighter>
            </div>
          ) : (
            <code className={className} {...props}>
              {children}
            </code>
          );
        }
      }}
    >
      {markdown}
    </Markdown>
  );
}

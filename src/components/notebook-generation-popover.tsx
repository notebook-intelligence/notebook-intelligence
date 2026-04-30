// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, {
  ChangeEvent,
  KeyboardEvent,
  useEffect,
  useRef,
  useState
} from 'react';
import { VscClose, VscSend, VscSparkle } from 'react-icons/vsc';

import { CheckBoxItem } from './checkbox';

export interface INotebookGenerationPopoverProps {
  initialShowInChat?: boolean;
  onSubmit: (prompt: string, showInChat: boolean) => void;
  onClose: () => void;
}

export function NotebookGenerationPopover(
  props: INotebookGenerationPopoverProps
): JSX.Element {
  const [prompt, setPrompt] = useState('');
  const [showInChat, setShowInChat] = useState<boolean>(
    props.initialShowInChat ?? true
  );
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // Auto-focus the textarea on mount.
    textareaRef.current?.focus();
  }, []);

  const trimmed = prompt.trim();
  const canSubmit = trimmed.length > 0;

  const handleSubmit = () => {
    if (!canSubmit) {
      return;
    }
    props.onSubmit(trimmed, showInChat);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      event.preventDefault();
      props.onClose();
      return;
    }
  };

  const handleTextareaKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div
      className="notebook-generation-popover"
      tabIndex={-1}
      onKeyDown={handleKeyDown}
    >
      <div className="notebook-generation-popover-header">
        <div className="notebook-generation-popover-header-icon">
          <VscSparkle />
        </div>
        <div className="notebook-generation-popover-title">
          Generate or update notebook
        </div>
        <div style={{ flexGrow: 1 }}></div>
        <div
          className="notebook-generation-popover-close-button"
          title="Close"
          onClick={props.onClose}
        >
          <VscClose />
        </div>
      </div>
      <div className="notebook-generation-popover-body">
        <textarea
          ref={textareaRef}
          className="notebook-generation-popover-input"
          rows={4}
          placeholder="Describe the notebook you want to create or how to update the active one..."
          value={prompt}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
            setPrompt(event.target.value)
          }
          onKeyDown={handleTextareaKeyDown}
        />
        <CheckBoxItem
          checked={showInChat}
          label="Show in chat"
          tooltip={
            'When enabled, the prompt opens the Notebook Intelligence ' +
            'chat sidebar. Disable to keep the chat hidden and only ' +
            'show progress on the notebook toolbar.'
          }
          onClick={() => setShowInChat(value => !value)}
        />
        <div className="notebook-generation-popover-actions">
          <button
            type="button"
            className="notebook-generation-popover-submit"
            disabled={!canSubmit}
            onClick={handleSubmit}
            title="Generate (Enter)"
          >
            <VscSend />
            <span>Generate</span>
          </button>
        </div>
      </div>
    </div>
  );
}

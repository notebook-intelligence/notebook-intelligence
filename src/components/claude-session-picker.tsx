// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, {
  KeyboardEvent,
  MouseEvent,
  useEffect,
  useRef,
  useState
} from 'react';
import { VscCheck, VscClose, VscCopy, VscHistory } from 'react-icons/vsc';

import { IClaudeSessionInfo, NBIAPI } from '../api';
import { writeTextToClipboard } from '../clipboard';

export interface IClaudeSessionPickerProps {
  onResume: (session: IClaudeSessionInfo) => void;
  onClose: () => void;
}

function formatTimestamp(epochSeconds: number): string {
  if (!epochSeconds) {
    return '';
  }
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  return date.toLocaleString();
}

export function ClaudeSessionPicker(
  props: IClaudeSessionPickerProps
): JSX.Element {
  const [sessions, setSessions] = useState<IClaudeSessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [resuming, setResuming] = useState(false);
  const [error, setError] = useState('');
  const [copyFeedback, setCopyFeedback] = useState<{
    sessionId: string;
    status: 'copied' | 'failed';
  } | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    NBIAPI.listClaudeSessions()
      .then(result => {
        if (cancelled) {
          return;
        }
        setSessions(result);
        setLoading(false);
      })
      .catch(reason => {
        if (cancelled) {
          return;
        }
        setError(String(reason?.message ?? reason ?? 'Unknown error'));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCopySessionId = async (
    event: MouseEvent<HTMLButtonElement>,
    session: IClaudeSessionInfo
  ) => {
    event.stopPropagation();
    event.preventDefault();
    const ok = await writeTextToClipboard(session.session_id);
    setCopyFeedback({
      sessionId: session.session_id,
      status: ok ? 'copied' : 'failed'
    });
    if (copyTimerRef.current !== null) {
      clearTimeout(copyTimerRef.current);
    }
    copyTimerRef.current = setTimeout(() => {
      setCopyFeedback(null);
      copyTimerRef.current = null;
    }, 1500);
  };

  const handleResume = async (session: IClaudeSessionInfo) => {
    if (resuming) {
      return;
    }
    setResuming(true);
    try {
      await NBIAPI.resumeClaudeSession(session.session_id);
      props.onResume(session);
    } catch (reason) {
      setError(String((reason as Error)?.message ?? reason ?? 'Unknown error'));
      setResuming(false);
    }
  };

  return (
    <div
      className="workspace-file-popover claude-session-picker"
      tabIndex={1}
      autoFocus={true}
      onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          event.preventDefault();
          props.onClose();
        }
      }}
    >
      <div className="mode-tools-popover-header">
        <div className="mode-tools-popover-header-icon">
          <VscHistory />
        </div>
        <div className="mode-tools-popover-title">Resume Claude session</div>
        <div style={{ flexGrow: 1 }}></div>
        <div
          className="mode-tools-popover-button mode-tools-popover-close-button"
          title="Close"
          onClick={props.onClose}
        >
          <VscClose />
        </div>
      </div>
      <div className="workspace-file-popover-body">
        {error && (
          <div className="workspace-file-popover-status error">{error}</div>
        )}
        {loading ? (
          <div className="workspace-file-popover-status">
            Loading sessions&#8230;
          </div>
        ) : sessions.length === 0 ? (
          <div className="workspace-file-popover-status">
            No previous Claude sessions found for this working directory.
          </div>
        ) : (
          <ul className="claude-session-picker-list">
            {sessions.map(session => {
              const feedback =
                copyFeedback && copyFeedback.sessionId === session.session_id
                  ? copyFeedback.status
                  : null;
              const buttonLabel =
                feedback === 'copied'
                  ? 'Session ID copied'
                  : feedback === 'failed'
                    ? 'Failed to copy session ID'
                    : 'Copy session ID';
              return (
                <li
                  key={session.session_id}
                  className={`claude-session-picker-item${resuming ? ' busy' : ''}`}
                  onClick={() => handleResume(session)}
                >
                  <div className="claude-session-picker-item-preview">
                    {session.preview || '(no preview available)'}
                  </div>
                  <div className="claude-session-picker-item-meta">
                    <span>{formatTimestamp(session.modified_at)}</span>
                    <span>&middot;</span>
                    <span
                      className="claude-session-picker-item-id"
                      title={session.session_id}
                    >
                      {session.session_id.slice(0, 8)}
                    </span>
                    <button
                      type="button"
                      className={`claude-session-picker-item-copy${
                        feedback ? ` ${feedback}` : ''
                      }`}
                      title={buttonLabel}
                      aria-label={buttonLabel}
                      onClick={event => handleCopySessionId(event, session)}
                    >
                      {feedback === 'copied' ? <VscCheck /> : <VscCopy />}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

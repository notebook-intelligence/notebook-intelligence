// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, { KeyboardEvent, useEffect, useState } from 'react';
import { VscClose, VscHistory } from 'react-icons/vsc';

import { IClaudeSessionInfo, NBIAPI } from '../api';

export interface IClaudeSessionPickerProps {
  onResume: (session: IClaudeSessionInfo) => void;
  onClose: () => void;
  fetchSessions?: () => Promise<IClaudeSessionInfo[]>;
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

  useEffect(() => {
    let cancelled = false;
    const fetch = props.fetchSessions ?? (() => NBIAPI.listClaudeSessions());
    fetch()
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

  const handleResume = async (session: IClaudeSessionInfo) => {
    if (resuming) {
      return;
    }
    setResuming(true);
    // When a custom fetchSessions is provided the caller owns the resume
    // lifecycle (e.g. the launcher tile opens a terminal directly), so skip
    // the NBI sidebar API call which requires Claude Code mode to be active.
    if (props.fetchSessions) {
      props.onResume(session);
      return;
    }
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
            {sessions.map(session => (
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
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

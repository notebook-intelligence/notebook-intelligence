// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, { useEffect, useRef, useState } from 'react';
import { requestAPI } from '../handler';
import { VscRefresh, VscWarning } from '../icons';

type Level = 'ok' | 'warn' | 'blocked' | 'skipped';
type Verdict = 'ready' | 'degraded' | 'not_ready';

interface IReadinessCheck {
  id: string;
  group: string;
  level: Level;
  title: string;
  detail: string;
  // Present on every blocked or warning row: the specific next action. The
  // whole point of the feature is that the user is never left with "something
  // went wrong" and no idea what to do about it.
  remedy?: string;
}

interface IReadinessDoc {
  schema_version: number;
  generated_at: string;
  verdict: Verdict;
  headline: string;
  checks: IReadinessCheck[];
}

const GROUP_LABELS: Record<string, string> = {
  mode: 'Chat path',
  provider: 'Model provider',
  claude: 'Claude mode',
  acp: 'ACP agent',
  live: 'Live endpoint check'
};

const VERDICT_LABELS: Record<Verdict, string> = {
  ready: 'Ready',
  degraded: 'Ready, with warnings',
  not_ready: 'Not ready'
};

// The settings dialog unmounts and remounts this component on every tab
// switch and every reopen, and a readiness run is not free: it walks PATH,
// lists models, and in Claude mode forks `claude --version`. Cache the last
// document briefly so reopening the dialog is instant and does not re-fork.
const CACHE_TTL_MS = 30_000;
let cachedDoc: IReadinessDoc | null = null;
let cachedAt = 0;

export function _resetReadinessCacheForTests(): void {
  cachedDoc = null;
  cachedAt = 0;
}

function readCache(): IReadinessDoc | null {
  if (cachedDoc && Date.now() - cachedAt < CACHE_TTL_MS) {
    return cachedDoc;
  }
  return null;
}

function writeCache(doc: IReadinessDoc | null): void {
  cachedDoc = doc;
  cachedAt = doc ? Date.now() : 0;
}

function errorMessage(error: any): string {
  return error?.message ?? String(error);
}

export function ReadinessCard(_props: any): JSX.Element {
  const [doc, setDoc] = useState<IReadinessDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [liveRunning, setLiveRunning] = useState(false);
  const [awaitingConfirm, setAwaitingConfirm] = useState(false);

  // Monotonic request id. Both buttons are disabled while a run is in
  // flight, so the UI cannot start two itself; this guards the case where a
  // future caller (or a re-render) does, and pairs with the unmount guard
  // below, which IS reachable: closing the dialog mid-request would
  // otherwise setState on an unmounted component.
  const requestSeq = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = async (live: boolean) => {
    const seq = ++requestSeq.current;
    if (live) {
      setLiveRunning(true);
    } else {
      setLoading(true);
    }
    setError(null);
    setAwaitingConfirm(false);
    try {
      const data = live
        ? await requestAPI<IReadinessDoc>('readiness', {
            method: 'POST',
            body: JSON.stringify({ live: true })
          })
        : await requestAPI<IReadinessDoc>('readiness');
      if (seq !== requestSeq.current || !mounted.current) {
        return;
      }
      setDoc(data);
      writeCache(data);
    } catch (e: any) {
      if (seq !== requestSeq.current || !mounted.current) {
        return;
      }
      // Drop the previous document too. Leaving it would render a green
      // "Ready" pill and stale rows next to "Readiness check failed", which
      // is the one thing this card must never do: a check that could not run
      // must not read as a pass.
      setDoc(null);
      writeCache(null);
      setError(errorMessage(e));
    } finally {
      if (seq === requestSeq.current && mounted.current) {
        setLoading(false);
        setLiveRunning(false);
      }
    }
  };

  useEffect(() => {
    // Served from a short-lived cache when the dialog is reopened or the
    // user switches tabs and comes back: each run walks PATH and, in Claude
    // mode, forks `claude --version`.
    const cached = readCache();
    if (cached) {
      setDoc(cached);
      setLoading(false);
      return;
    }
    load(false);
    // Only on mount; the buttons drive subsequent runs.
  }, []);

  const groups: string[] = [];
  for (const check of doc?.checks ?? []) {
    if (!groups.includes(check.group)) {
      groups.push(check.group);
    }
  }

  return (
    <div className="nbi-readiness">
      <div className="nbi-readiness-header">
        <div
          className={`nbi-readiness-verdict nbi-readiness-verdict-${doc?.verdict ?? 'unknown'}`}
        >
          {doc
            ? VERDICT_LABELS[doc.verdict]
            : loading
              ? 'Checking…'
              : 'Unknown'}
        </div>
        <div className="nbi-readiness-headline">
          {error
            ? `Readiness check failed: ${error}`
            : (doc?.headline ?? 'Checking configuration…')}
        </div>
        <div className="nbi-readiness-actions">
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={() => load(false)}
            disabled={loading || liveRunning}
          >
            <VscRefresh />
            <div className="jp-Dialog-buttonLabel">
              {loading ? 'Checking…' : 'Re-check'}
            </div>
          </button>
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={() => setAwaitingConfirm(true)}
            disabled={loading || liveRunning}
            title="Sends one short request to your configured model to verify streaming and tool support."
          >
            <div className="jp-Dialog-buttonLabel">
              {liveRunning ? 'Running…' : 'Test the endpoint'}
            </div>
          </button>
        </div>
      </div>

      {awaitingConfirm && (
        <div className="nbi-readiness-confirm">
          <VscWarning />
          <span>
            This sends one short request to your configured model. It costs a
            few tokens and is the only way to confirm the endpoint really
            streams and really accepts tool calls.
          </span>
          <button
            className="jp-Dialog-button jp-mod-accept jp-mod-styled"
            onClick={() => load(true)}
          >
            <div className="jp-Dialog-buttonLabel">Run it</div>
          </button>
          <button
            className="jp-Dialog-button jp-mod-reject jp-mod-styled"
            onClick={() => setAwaitingConfirm(false)}
          >
            <div className="jp-Dialog-buttonLabel">Cancel</div>
          </button>
        </div>
      )}

      {groups.map(group => (
        <div className="nbi-readiness-group" key={group}>
          <div className="nbi-readiness-group-title">
            {GROUP_LABELS[group] ?? group}
          </div>
          {(doc?.checks ?? [])
            .filter(c => c.group === group)
            .map(check => (
              <div className="nbi-readiness-row" key={check.id}>
                <div className="nbi-readiness-row-head">
                  <span
                    className={`nbi-readiness-badge nbi-readiness-badge-${check.level}`}
                  >
                    {check.level}
                  </span>
                  <span className="nbi-readiness-title">{check.title}</span>
                  <span className="nbi-readiness-detail">{check.detail}</span>
                </div>
                {check.remedy && (
                  <div className="nbi-readiness-remedy">{check.remedy}</div>
                )}
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}

// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, { useEffect, useState } from 'react';
import { NBIAPI } from '../api';
import { requestAPI } from '../handler';
import { writeTextToClipboard } from '../utils';
import { VscCheck, VscCopy, VscRefresh, VscWarning } from '../icons';

type AttrDetail = 'redacted' | 'full';

interface IPerfDiagnosticsConfig {
  enabled: boolean;
  log_to_file: boolean;
  log_dir: string;
  attr_detail: AttrDetail;
  ring_buffer_turns: number;
}

const DEFAULT_PERF_CONFIG: IPerfDiagnosticsConfig = {
  enabled: false,
  log_to_file: false,
  log_dir: '',
  attr_detail: 'redacted',
  ring_buffer_turns: 50
};

interface IPerfSpan {
  name: string;
  dur_ms: number;
  status: string;
  attrs?: Record<string, unknown>;
}

interface IPerfEvent {
  name: string;
  t_ms: number;
  attrs?: Record<string, unknown>;
}

interface IPerfTokens {
  input: number;
  output: number;
}

interface IPerfSdkStats {
  duration_ms: number;
  duration_api_ms: number;
  num_turns: number;
}

interface IPerfTurn {
  turn_id: string;
  message_id: string;
  mode: string;
  model: string;
  status: string;
  t_wall: number;
  total_ms: number;
  active_ms: number;
  spans: IPerfSpan[];
  events: IPerfEvent[];
  tokens: IPerfTokens;
  sdk: IPerfSdkStats;
}

interface IPerfReport {
  schema_version: number;
  turns: IPerfTurn[];
  aggregates: Record<string, unknown>;
  probe_target?: string;
}

interface IPerfProbeCheck {
  id: string;
  group: string;
  status: 'ok' | 'timed_out' | 'error' | 'skipped';
  detail: Record<string, unknown>;
}

interface IPerfProbeDocument {
  schema_version: number;
  generated_at: string;
  checks: IPerfProbeCheck[];
}

// Duplicated from settings-panel.tsx rather than exported from there, since
// this panel is meant to stay independent of the rest of that file.
const lockedTip = (locked: boolean): string =>
  locked ? 'Locked by your administrator' : '';

function isNotFoundError(error: any): boolean {
  return error?.response?.status === 404;
}

function errorMessage(error: any): string {
  return error?.message ?? String(error);
}

function findSpanMs(turn: IPerfTurn, name: string): number | undefined {
  const span = (turn.spans ?? []).find(s => s.name === name);
  return span ? span.dur_ms : undefined;
}

function firstTokenMs(turn: IPerfTurn): number | undefined {
  // Recorded as an event mark (offset from turn start), never as a span.
  const ev = (turn.events ?? []).find(e => e.name === 'first_token');
  return ev ? ev.t_ms : undefined;
}

function toolsMs(turn: IPerfTurn): number {
  return (turn.spans ?? [])
    .filter(s => s.name.startsWith('tool:'))
    .reduce((sum, s) => sum + s.dur_ms, 0);
}

function fmtMs(value: number | undefined): string {
  return value === undefined ? 'n/a' : `${Math.round(value)} ms`;
}

function CopyButton(props: {
  label: string;
  getValue: () => unknown;
}): JSX.Element {
  const [copied, setCopied] = useState(false);

  const onClick = async () => {
    const ok = await writeTextToClipboard(
      JSON.stringify(props.getValue(), null, 2)
    );
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <button
      className="jp-Dialog-button jp-mod-reject jp-mod-styled nbi-perf-copy-button"
      onClick={onClick}
    >
      {copied ? <VscCheck /> : <VscCopy />}
      <div className="jp-Dialog-buttonLabel">
        {copied ? 'Copied' : props.label}
      </div>
    </button>
  );
}

function PerfTurnsTable(props: { turns: IPerfTurn[] }): JSX.Element {
  if (props.turns.length === 0) {
    return <div className="nbi-perf-empty">No turns recorded yet.</div>;
  }

  return (
    <div className="nbi-perf-table-wrap">
      <table className="nbi-perf-table">
        <thead>
          <tr>
            <th>Mode</th>
            <th>Model</th>
            <th>Status</th>
            <th>Total</th>
            <th>Active</th>
            <th>Spawn</th>
            <th>First token</th>
            <th>Stream</th>
            <th>Tools</th>
            <th>Tokens in</th>
            <th>Tokens out</th>
            <th>API ms</th>
          </tr>
        </thead>
        <tbody>
          {props.turns.map(turn => (
            <tr key={turn.turn_id}>
              <td>{turn.mode}</td>
              <td>{turn.model}</td>
              <td className={`nbi-perf-status-${turn.status}`}>
                {turn.status}
              </td>
              <td>{fmtMs(turn.total_ms)}</td>
              <td>{fmtMs(turn.active_ms)}</td>
              <td>{fmtMs(findSpanMs(turn, 'connect'))}</td>
              <td>{fmtMs(firstTokenMs(turn))}</td>
              <td>{fmtMs(findSpanMs(turn, 'stream'))}</td>
              <td>{fmtMs(toolsMs(turn))}</td>
              <td>{turn.tokens?.input ?? 'n/a'}</td>
              <td>{turn.tokens?.output ?? 'n/a'}</td>
              <td>{fmtMs(turn.sdk?.duration_api_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SettingsPanelComponentPerf(_props: any): JSX.Element {
  const [config, setConfig] = useState<IPerfDiagnosticsConfig>(
    NBIAPI.config.capabilities?.perf_diagnostics ?? DEFAULT_PERF_CONFIG
  );
  const [locks, setLocks] = useState<
    Record<string, { locked: boolean } | undefined>
  >(NBIAPI.config.settingLocks as any);

  const [report, setReport] = useState<IPerfReport | null>(null);
  const [reportLoading, setReportLoading] = useState(true);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportDisabled, setReportDisabled] = useState(false);

  const [includeNetwork, setIncludeNetwork] = useState(false);
  const [awaitingConfirm, setAwaitingConfirm] = useState(false);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<IPerfProbeDocument | null>(
    null
  );
  const [probeError, setProbeError] = useState<string | null>(null);

  useEffect(() => {
    const handler = () => {
      setConfig(
        NBIAPI.config.capabilities?.perf_diagnostics ?? DEFAULT_PERF_CONFIG
      );
      setLocks(NBIAPI.config.settingLocks as any);
    };
    NBIAPI.configChanged.connect(handler);
    return () => {
      NBIAPI.configChanged.disconnect(handler);
    };
  }, []);

  const loadReport = async () => {
    setReportLoading(true);
    setReportError(null);
    setReportDisabled(false);
    try {
      const data = await requestAPI<IPerfReport>('perf/report');
      setReport(data);
    } catch (e: any) {
      if (isNotFoundError(e)) {
        setReportDisabled(true);
        setReport(null);
      } else {
        setReportError(errorMessage(e));
      }
    } finally {
      setReportLoading(false);
    }
  };

  useEffect(() => {
    loadReport();
    // Only on mount; the refresh button drives subsequent loads.
  }, []);

  // The server serves perf_diagnostics as {enabled, locked} alongside every
  // other named policy (see _build_feature_policies_response in
  // extension.py); `locked` is what force-on/force-off both surface as.
  const perfPolicy = NBIAPI.config.featurePolicies?.perf_diagnostics;
  const perfLocked =
    !!locks?.perf_diagnostics_enabled?.locked || perfPolicy?.locked === true;

  const updateConfig = (patch: Partial<IPerfDiagnosticsConfig>) => {
    const next = { ...config, ...patch };
    setConfig(next);
    NBIAPI.setConfig({ perf_diagnostics: next });
  };

  const runProbe = async (network: boolean) => {
    setAwaitingConfirm(false);
    setProbing(true);
    setProbeError(null);
    try {
      const data = await requestAPI<IPerfProbeDocument>('perf/probe', {
        method: 'POST',
        body: JSON.stringify({ network })
      });
      setProbeResult(data);
    } catch (e: any) {
      setProbeError(errorMessage(e));
    } finally {
      setProbing(false);
    }
  };

  const onIncludeNetworkChange = (checked: boolean) => {
    setIncludeNetwork(checked);
    setAwaitingConfirm(checked);
    setProbeError(null);
  };

  // Defaults open (matches the backend's own default and the codebase's
  // fail-open convention for missing capability fields); only an explicit
  // `false` from NBI_PERF_PROBE_NETWORK=off disables the checkbox.
  const networkProbeAllowed =
    (NBIAPI.config.capabilities as any)?.perf_probe_network_allowed !== false;

  const onRunProbeClicked = () => {
    if (includeNetwork) {
      setAwaitingConfirm(true);
      return;
    }
    runProbe(false);
  };

  return (
    <div className="nbi-perf-panel">
      <div className="nbi-perf-section">
        <div className="nbi-perf-section-header">
          <div className="nbi-perf-title">Diagnostics</div>
        </div>
        <div className="nbi-perf-controls">
          <label className="nbi-perf-control" title={lockedTip(perfLocked)}>
            <input
              type="checkbox"
              checked={config.enabled}
              disabled={perfLocked}
              onChange={e => updateConfig({ enabled: e.target.checked })}
            />
            Enabled
          </label>
          <label className="nbi-perf-control" title={lockedTip(perfLocked)}>
            <input
              type="checkbox"
              checked={config.log_to_file}
              disabled={perfLocked}
              onChange={e => updateConfig({ log_to_file: e.target.checked })}
            />
            Log to file
          </label>
          <label className="nbi-perf-control" title={lockedTip(perfLocked)}>
            Attribute detail
            <select
              className="jp-mod-styled"
              value={config.attr_detail}
              disabled={perfLocked}
              onChange={e =>
                updateConfig({ attr_detail: e.target.value as AttrDetail })
              }
            >
              <option value="redacted">redacted</option>
              <option value="full">full</option>
            </select>
          </label>
        </div>
      </div>

      <div className="nbi-perf-section">
        <div className="nbi-perf-section-header">
          <div className="nbi-perf-title">Recent turns</div>
          <div className="nbi-perf-header-actions">
            {report && (
              <CopyButton
                label="Copy as JSON"
                getValue={() => {
                  // probe_target carries the configured gateway host; docs
                  // promise hostnames are never recorded in exported
                  // reports, so it stays out of the copied JSON even though
                  // it's used verbatim in the confirm dialog above.
                  const { probe_target: _probeTarget, ...rest } = report;
                  return rest;
                }}
              />
            )}
            <button
              className="jp-Dialog-button jp-mod-reject jp-mod-styled"
              onClick={loadReport}
              disabled={reportLoading}
            >
              <VscRefresh />
              <div className="jp-Dialog-buttonLabel">
                {reportLoading ? 'Refreshing…' : 'Refresh'}
              </div>
            </button>
          </div>
        </div>

        {reportDisabled ? (
          <div className="nbi-perf-empty">
            Performance diagnostics are disabled. Enable them above to start
            collecting turn data.
          </div>
        ) : reportError ? (
          <div className="nbi-perf-error" role="alert">
            Failed to load report: {reportError}
            <button
              className="jp-Dialog-button jp-mod-reject jp-mod-styled"
              onClick={loadReport}
            >
              <div className="jp-Dialog-buttonLabel">Retry</div>
            </button>
          </div>
        ) : reportLoading && !report ? (
          <div className="nbi-perf-empty">Loading…</div>
        ) : (
          <PerfTurnsTable turns={report?.turns ?? []} />
        )}
      </div>

      <div className="nbi-perf-section">
        <div className="nbi-perf-section-header">
          <div className="nbi-perf-title">Probe</div>
        </div>
        <div className="nbi-perf-probe-actions">
          <button
            className="jp-Dialog-button jp-mod-accept jp-mod-styled"
            onClick={onRunProbeClicked}
            disabled={probing}
          >
            <div className="jp-Dialog-buttonLabel">
              {probing ? 'Running…' : 'Run probe'}
            </div>
          </button>
          <label
            className="nbi-perf-control"
            title={
              networkProbeAllowed
                ? ''
                : 'Disabled by your administrator (NBI_PERF_PROBE_NETWORK)'
            }
          >
            <input
              type="checkbox"
              checked={includeNetwork}
              disabled={!networkProbeAllowed}
              onChange={e => onIncludeNetworkChange(e.target.checked)}
            />
            Include network check
          </label>
        </div>

        {awaitingConfirm && (
          <div className="nbi-perf-confirm">
            <VscWarning />
            <span>
              {report?.probe_target
                ? `This will send one unauthenticated request to ${report.probe_target} to check network connectivity from this machine.`
                : 'This will contact an external endpoint to check network connectivity from this machine.'}
            </span>
            <button
              className="jp-Dialog-button jp-mod-accept jp-mod-styled"
              onClick={() => runProbe(true)}
              disabled={probing}
            >
              <div className="jp-Dialog-buttonLabel">Confirm and run</div>
            </button>
            <button
              className="jp-Dialog-button jp-mod-reject jp-mod-styled"
              onClick={() => {
                setAwaitingConfirm(false);
                setIncludeNetwork(false);
              }}
            >
              <div className="jp-Dialog-buttonLabel">Cancel</div>
            </button>
          </div>
        )}

        {probeError && (
          <div className="nbi-perf-error" role="alert">
            Probe failed: {probeError}
          </div>
        )}

        {probeResult && (
          <div className="nbi-perf-probe-result">
            <div className="nbi-perf-header-actions">
              <CopyButton label="Copy as JSON" getValue={() => probeResult} />
            </div>
            <pre className="nbi-perf-pre">
              {JSON.stringify(probeResult, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

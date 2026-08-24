// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React, { useEffect, useState } from 'react';
import { NBIAPI } from '../api';
import { requestAPI } from '../handler';
import { writeTextToClipboard } from '../utils';
import {
  VscCheck,
  VscChevronDown,
  VscChevronRight,
  VscCopy,
  VscRefresh,
  VscWarning
} from '../icons';

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
  dropped_spans?: number;
  dropped_attrs?: number;
}

interface IPerfReport {
  schema_version: number;
  turns: IPerfTurn[];
  aggregates: Record<string, unknown>;
  probe_target?: string;
}

type ProbeStatus = 'ok' | 'timed_out' | 'error' | 'skipped';

interface IPerfProbeCheck {
  id: string;
  group: string;
  status: ProbeStatus;
  detail: Record<string, unknown>;
}

interface IPerfProbeDocument {
  schema_version: number;
  generated_at: string;
  checks: IPerfProbeCheck[];
}

type Band = 'ok' | 'warn' | 'bad';

interface IProbeReading {
  // One line of already-formatted numbers.
  headline: string;
  // Only set when the reading is interpretable against a threshold; a purely
  // informational check (interpreter version, npm cache path) has none.
  band?: Band;
  // Rendered only for warn/bad, so a healthy probe stays quiet.
  note?: string;
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

function slowestTool(turn: IPerfTurn): IPerfSpan | undefined {
  return (turn.spans ?? [])
    .filter(s => s.name.startsWith('tool:'))
    .reduce<
      IPerfSpan | undefined
    >((best, s) => (!best || s.dur_ms > best.dur_ms ? s : best), undefined);
}

function stallCount(turn: IPerfTurn): number {
  return (turn.events ?? []).filter(e => e.name === 'stall').length;
}

function fmtMs(value: number | undefined): string {
  return value === undefined ? 'n/a' : `${Math.round(value)} ms`;
}

// Probe latency medians on a local disk are routinely well under 1 ms, and
// rounding those to "0 ms" reads as "not measured" rather than "fast".
function fmtLatency(value: number | undefined): string {
  if (value === undefined) {
    return 'n/a';
  }
  if (value < 10) {
    return `${value.toFixed(2)} ms`;
  }
  return `${Math.round(value)} ms`;
}

function fmtSeconds(value: number | undefined): string {
  return value === undefined ? 'n/a' : `${(value / 1000).toFixed(1)}s`;
}

function fmtClock(tWall: number | undefined): string {
  if (typeof tWall !== 'number' || !isFinite(tWall)) {
    return 'n/a';
  }
  // The backend sends time.time(), i.e. epoch seconds as a float.
  return new Date(tWall * 1000).toLocaleTimeString();
}

function fmtBytes(value: number | undefined): string {
  if (value === undefined) {
    return 'n/a';
  }
  if (value >= 1024 * 1024 * 1024) {
    return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  }
  if (value >= 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (value >= 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${value} B`;
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' && isFinite(value) ? value : undefined;
}

function band(
  value: number | undefined,
  warnAt: number,
  badAt: number
): Band | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (value >= badAt) {
    return 'bad';
  }
  if (value >= warnAt) {
    return 'warn';
  }
  return 'ok';
}

function bandDescending(
  value: number | undefined,
  warnBelow: number,
  badBelow: number
): Band | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (value < badBelow) {
    return 'bad';
  }
  if (value < warnBelow) {
    return 'warn';
  }
  return 'ok';
}

function worseBand(a?: Band, b?: Band): Band | undefined {
  const rank: Record<Band, number> = { ok: 0, warn: 1, bad: 2 };
  if (a === undefined) {
    return b;
  }
  if (b === undefined) {
    return a;
  }
  return rank[a] >= rank[b] ? a : b;
}

function attrsText(attrs: Record<string, unknown> | undefined): string {
  return Object.entries(attrs ?? {})
    .map(([k, v]) => `${k}=${String(v)}`)
    .join('  ');
}

// ---------------------------------------------------------------------------
// Turn verdict
// ---------------------------------------------------------------------------

interface IVerdict {
  label: string;
  detail: string;
}

// Derived entirely from what perf/report already returns; the backend records
// no verdict field. The denominator is active_ms rather than total_ms because
// total_ms includes time the turn spent waiting on the user, which is not a
// performance problem.
function turnVerdict(turn: IPerfTurn): IVerdict {
  if (turn.status === 'error') {
    return { label: 'Failed', detail: 'The turn ended in an error.' };
  }
  if (turn.status === 'cancelled') {
    return {
      label: 'Cancelled',
      detail: 'Stopped before completing, so these timings are partial.'
    };
  }

  const active = turn.active_ms;
  if (!active || active <= 0) {
    return { label: 'No timing recorded', detail: '' };
  }

  const api = num(turn.sdk?.duration_api_ms);
  const connect = findSpanMs(turn, 'connect');
  const contextPrep = findSpanMs(turn, 'context_prep');
  const tools = toolsMs(turn);
  const stalls = stallCount(turn);

  if (api !== undefined && api >= 0.7 * active) {
    return {
      label: 'Model or gateway',
      detail: `The SDK attributes ${fmtSeconds(api)} to the API against ${fmtSeconds(active)} active, so the time is on the far side of the network rather than in NBI.`
    };
  }
  if (tools >= 0.4 * active) {
    const worst = slowestTool(turn);
    const which = worst
      ? ` Slowest: ${worst.name} at ${fmtSeconds(worst.dur_ms)}.`
      : '';
    return {
      label: 'Tool execution',
      detail: `Tools accounted for ${fmtSeconds(tools)} of ${fmtSeconds(active)} active.${which}`
    };
  }
  if (connect !== undefined && connect >= 0.25 * active) {
    return {
      label: 'Agent cold start',
      detail: `Connect and spawn took ${fmtSeconds(connect)}. That is subprocess and CLI startup, usually node plus bundle reads, which is where slow storage shows up.`
    };
  }
  if (contextPrep !== undefined && contextPrep >= 0.25 * active) {
    return {
      label: 'Context preparation',
      detail: `Rule and skill discovery took ${fmtSeconds(contextPrep)}. That work is filesystem-bound on the server's home directory.`
    };
  }
  if (stalls > 0) {
    return {
      label: 'Mid-stream stalls',
      detail: `${stalls} gap${stalls === 1 ? '' : 's'} during streaming. Each stall event below is tagged with what preceded it.`
    };
  }
  return {
    label: 'No single hotspot',
    detail: `${fmtSeconds(active)} active with no phase dominating.`
  };
}

// ---------------------------------------------------------------------------
// Turns table
// ---------------------------------------------------------------------------

interface IColumn {
  key: string;
  label: string;
  tip: string;
}

const TURN_COLUMNS: IColumn[] = [
  { key: 'time', label: 'Time', tip: 'When the turn started, in local time.' },
  { key: 'mode', label: 'Mode', tip: 'Which backend served the turn.' },
  {
    key: 'model',
    label: 'Model',
    tip: 'Model name, hashed when attribute detail is set to redacted.'
  },
  {
    key: 'status',
    label: 'Status',
    tip: 'ok, error, or cancelled. A cancelled turn is recorded as cancelled rather than as a fast success.'
  },
  {
    key: 'total',
    label: 'Total',
    tip: 'Wall time from the request arriving to the response finishing, including any time spent waiting on you.'
  },
  {
    key: 'active',
    label: 'Active',
    tip: 'Total minus time the turn spent blocked on your input. This is the number to judge performance by.'
  },
  {
    key: 'spawn',
    label: 'Spawn',
    tip: 'The connect span: agent subprocess and CLI startup. Large values usually mean node startup plus bundle reads on slow storage.'
  },
  {
    key: 'first_token',
    label: 'First token',
    tip: 'Offset from the start of the turn to the first content-bearing chunk. Recorded as an event, so local progress spinners do not count toward it.'
  },
  {
    key: 'stream',
    label: 'Stream',
    tip: 'First content chunk to last. Excludes connect.'
  },
  {
    key: 'tools',
    label: 'Tools',
    tip: 'Sum of every tool span in the turn. Expand the row to see them individually.'
  },
  {
    key: 'stalls',
    label: 'Stalls',
    tip: 'Gaps between stream chunks longer than the stall threshold. Each is tagged with whether it followed tool use or text.'
  },
  {
    key: 'tokens_in',
    label: 'Tokens in',
    tip: 'Input tokens including cache reads and cache writes, summed the same way the chat usage footer sums them.'
  },
  { key: 'tokens_out', label: 'Tokens out', tip: 'Output tokens.' },
  {
    key: 'api',
    label: 'API ms',
    tip: 'Duration the SDK attributes to the model API. Compare against Active: close together means the time is in the gateway or the model, far apart means it is local.'
  }
];

function PerfTurnDetail(props: { turn: IPerfTurn }): JSX.Element {
  const { turn } = props;
  const spans = turn.spans ?? [];
  const events = [...(turn.events ?? [])].sort((a, b) => a.t_ms - b.t_ms);
  const maxDur = spans.reduce((m, s) => Math.max(m, s.dur_ms), 0);
  const verdict = turnVerdict(turn);
  const droppedSpans = turn.dropped_spans ?? 0;
  const droppedAttrs = turn.dropped_attrs ?? 0;

  return (
    <div className="nbi-perf-detail">
      {verdict.detail && (
        <div className="nbi-perf-detail-verdict">{verdict.detail}</div>
      )}

      {spans.length === 0 ? (
        <div className="nbi-perf-empty">No spans recorded for this turn.</div>
      ) : (
        <div className="nbi-perf-spans">
          {spans.map((span, i) => (
            <div className="nbi-perf-span-row" key={`${span.name}-${i}`}>
              <div className="nbi-perf-span-name" title={span.name}>
                {span.name}
              </div>
              <div className="nbi-perf-span-bar-track">
                <div
                  className={`nbi-perf-span-bar nbi-perf-span-${span.status}`}
                  style={{
                    width: `${maxDur > 0 ? (span.dur_ms / maxDur) * 100 : 0}%`
                  }}
                />
              </div>
              <div className="nbi-perf-span-value">{fmtMs(span.dur_ms)}</div>
              <div className="nbi-perf-span-attrs">{attrsText(span.attrs)}</div>
            </div>
          ))}
        </div>
      )}

      {events.length > 0 && (
        <div className="nbi-perf-events">
          <div className="nbi-perf-subtitle">Events</div>
          {events.map((ev, i) => (
            <div className="nbi-perf-event-row" key={`${ev.name}-${i}`}>
              <span className="nbi-perf-event-offset">{fmtMs(ev.t_ms)}</span>
              <span className="nbi-perf-event-name">{ev.name}</span>
              <span className="nbi-perf-span-attrs">{attrsText(ev.attrs)}</span>
            </div>
          ))}
        </div>
      )}

      {droppedSpans + droppedAttrs > 0 && (
        <div className="nbi-perf-detail-note">
          {droppedSpans} span(s) and {droppedAttrs} attribute(s) were dropped by
          the per-turn caps, so this timeline is incomplete.
        </div>
      )}
    </div>
  );
}

function PerfTurnsTable(props: { turns: IPerfTurn[] }): JSX.Element {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  if (props.turns.length === 0) {
    return (
      <div className="nbi-perf-empty">
        Recording is on, but no turns have been captured yet. Run a chat turn,
        then press Refresh.
      </div>
    );
  }

  const toggle = (id: string) => {
    setExpanded(prev => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <div className="nbi-perf-table-wrap">
      <table className="nbi-perf-table">
        <thead>
          <tr>
            <th>
              <span className="nbi-perf-visually-hidden">Expand</span>
            </th>
            {TURN_COLUMNS.map(col => (
              <th key={col.key} title={col.tip}>
                {col.label}
              </th>
            ))}
            <th title="A one-line reading of where this turn's time went, derived from the columns to the left.">
              Verdict
            </th>
          </tr>
        </thead>
        <tbody>
          {props.turns.map(turn => {
            const open = !!expanded[turn.turn_id];
            const verdict = turnVerdict(turn);
            return (
              <React.Fragment key={turn.turn_id}>
                <tr>
                  <td className="nbi-perf-expander">
                    <button
                      className="nbi-perf-expand-button"
                      aria-expanded={open}
                      aria-label={
                        open
                          ? `Hide timeline for the turn at ${fmtClock(turn.t_wall)}`
                          : `Show timeline for the turn at ${fmtClock(turn.t_wall)}`
                      }
                      onClick={() => toggle(turn.turn_id)}
                    >
                      {open ? <VscChevronDown /> : <VscChevronRight />}
                    </button>
                  </td>
                  <td>{fmtClock(turn.t_wall)}</td>
                  <td>{turn.mode}</td>
                  <td>{turn.model || 'unknown'}</td>
                  <td className={`nbi-perf-status-${turn.status}`}>
                    {turn.status}
                  </td>
                  <td>{fmtMs(turn.total_ms)}</td>
                  <td>{fmtMs(turn.active_ms)}</td>
                  <td>{fmtMs(findSpanMs(turn, 'connect'))}</td>
                  <td>{fmtMs(firstTokenMs(turn))}</td>
                  <td>{fmtMs(findSpanMs(turn, 'stream'))}</td>
                  <td>{fmtMs(toolsMs(turn))}</td>
                  <td>{stallCount(turn)}</td>
                  <td>{turn.tokens?.input ?? 'n/a'}</td>
                  <td>{turn.tokens?.output ?? 'n/a'}</td>
                  <td>{fmtMs(turn.sdk?.duration_api_ms)}</td>
                  <td className="nbi-perf-verdict">{verdict.label}</td>
                </tr>
                {open && (
                  <tr>
                    <td
                      className="nbi-perf-detail-cell"
                      colSpan={TURN_COLUMNS.length + 2}
                    >
                      <PerfTurnDetail turn={turn} />
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Probe rendering
// ---------------------------------------------------------------------------

const PROBE_GROUP_LABELS: Record<string, string> = {
  filesystem: 'Filesystem',
  subprocess: 'Subprocess startup',
  runtime: 'Process and host',
  network: 'Network'
};

const PROBE_GROUP_ORDER = ['filesystem', 'subprocess', 'runtime', 'network'];

const NETWORK_FS_TYPES = [
  'nfs',
  'nfs4',
  'efs',
  'cifs',
  'smbfs',
  'afpfs',
  'fuse',
  'lustre',
  'gpfs',
  'glusterfs'
];

const FS_TARGET_LABELS: Record<string, string> = {
  claude_home: '~/.claude',
  nbi_user_dir: 'NBI config directory',
  jupyter_root: 'Jupyter root directory'
};

const FS_KIND_LABELS: Record<string, string> = {
  latency: 'small-file latency',
  sustained_io: 'sustained throughput',
  mount: 'mount',
  session_scan: 'session tree size'
};

const CHECK_LABELS: Record<string, string> = {
  'subprocess.node_version': 'node --version',
  'subprocess.claude_cli_version': 'claude --version',
  'subprocess.npm_cache_path': 'npm config get cache',
  'runtime.process_rss': 'Server process peak memory',
  'runtime.loadavg': 'Host load average',
  'runtime.cgroup_cpu': 'cgroup CPU throttling',
  'runtime.interpreter': 'Python interpreter',
  'network.endpoint': 'Configured endpoint'
};

function checkLabel(check: IPerfProbeCheck): string {
  if (check.id.startsWith('fs.')) {
    const parts = check.id.split('.');
    const target = parts[1];
    const kind = parts.slice(2).join('.');
    return `${FS_TARGET_LABELS[target] ?? target}: ${FS_KIND_LABELS[kind] ?? kind}`;
  }
  return CHECK_LABELS[check.id] ?? check.id;
}

function latencyReading(detail: Record<string, unknown>): IProbeReading {
  const stat = (detail.stat_ms ?? {}) as Record<string, unknown>;
  const write = (detail.write_fsync_unlink_ms ?? {}) as Record<string, unknown>;
  const statMedian = num(stat.median_ms);
  const writeMedian = num(write.median_ms);
  const level = worseBand(band(statMedian, 1, 5), band(writeMedian, 10, 50));
  return {
    headline: `stat ${fmtLatency(statMedian)}, write+fsync ${fmtLatency(writeMedian)} (median of ${num(detail.n_completed) ?? 0})`,
    band: level,
    note:
      level === 'bad' || level === 'warn'
        ? 'fsync latency in this range is the signature of a network filesystem. If this is ~/.claude or the Jupyter home, every agent start and every rule scan pays it.'
        : undefined
  };
}

function sustainedReading(detail: Record<string, unknown>): IProbeReading {
  const writeMbs = num(detail.write_mb_s);
  const readMbs = num(detail.read_mb_s);
  const level = bandDescending(writeMbs, 50, 10);
  const fmt = (v: number | undefined) =>
    v === undefined ? 'n/a' : `${v.toFixed(1)} MB/s`;
  return {
    headline: `write ${fmt(writeMbs)}, read ${fmt(readMbs)}`,
    band: level,
    note:
      level === 'bad'
        ? 'Single-digit MB/s alongside high fsync latency is the EFS burst-credit-exhaustion signature. Confirm from CloudWatch BurstCreditBalance rather than from inside the pod.'
        : level === 'warn'
          ? 'Below what local storage delivers. Worth comparing against a known-good node.'
          : undefined
  };
}

function mountReading(detail: Record<string, unknown>): IProbeReading {
  const fstype = typeof detail.fstype === 'string' ? detail.fstype : undefined;
  const options =
    typeof detail.options === 'string' ? detail.options : undefined;
  if (!fstype) {
    return {
      headline: typeof detail.note === 'string' ? detail.note : 'not determined'
    };
  }
  const isNetwork = NETWORK_FS_TYPES.some(
    t => fstype === t || fstype.startsWith(`${t}.`)
  );
  return {
    headline: options ? `${fstype} (${options})` : fstype,
    band: isNetwork ? 'warn' : 'ok',
    note: isNetwork
      ? 'A network filesystem. Read every latency number in this group against that, and point NBI_PERF_LOG_DIR somewhere local.'
      : undefined
  };
}

function sessionScanReading(detail: Record<string, unknown>): IProbeReading {
  const parts: string[] = [];
  let worstCount = 0;
  let truncated = false;
  for (const key of ['projects', 'sessions']) {
    const entry = detail[key] as Record<string, unknown> | undefined;
    if (!entry) {
      continue;
    }
    const count = num(entry.file_count) ?? 0;
    worstCount = Math.max(worstCount, count);
    truncated = truncated || entry.truncated === true;
    parts.push(`${key} ${count} files, ${fmtBytes(num(entry.total_bytes))}`);
  }
  if (parts.length === 0) {
    return { headline: 'no projects/ or sessions/ directory' };
  }
  const level = band(worstCount, 5000, 20000);
  return {
    headline: `${parts.join('; ')}${truncated ? ' (scan truncated)' : ''}`,
    band: level,
    note:
      level === 'bad' || level === 'warn'
        ? 'A large ~/.claude tree makes every agent start pay to walk it. On a network filesystem this is often most of the spawn cost.'
        : undefined
  };
}

function subprocessReading(detail: Record<string, unknown>): IProbeReading {
  const wall = num(detail.wall_ms);
  const level = band(wall, 500, 2000);
  const version =
    typeof detail.version === 'string' && detail.version ? detail.version : '';
  const path =
    typeof detail.path === 'string' && detail.path ? detail.path : '';
  const extra = version || path;
  return {
    headline: extra ? `${fmtLatency(wall)} (${extra})` : fmtLatency(wall),
    band: level,
    note:
      level === 'bad' || level === 'warn'
        ? 'Cold start here is dominated by reading the install tree. This is the number that turns into the Spawn column on every turn.'
        : undefined
  };
}

function networkReading(detail: Record<string, unknown>): IProbeReading {
  const timings = (detail.timings_ms ?? {}) as Record<string, unknown>;
  const tls = (detail.tls ?? {}) as Record<string, unknown>;
  const http = (detail.http ?? {}) as Record<string, unknown>;
  const dns = num(timings.dns_ms) ?? num(timings.proxy_dns_ms);
  const tcp = num(timings.tcp_connect_ms) ?? num(timings.proxy_tcp_connect_ms);
  const handshake = num(timings.tls_handshake_ms);

  const pieces = [
    `dns ${fmtLatency(dns)}`,
    `tcp ${fmtLatency(tcp)}`,
    `tls ${fmtLatency(handshake)}`
  ];
  if (num(http.ttfb_ms) !== undefined) {
    pieces.push(`ttfb ${fmtLatency(num(http.ttfb_ms))}`);
  }
  if (detail.path === 'via_proxy') {
    pieces.push('via proxy');
  }
  const headline = pieces.join(', ');

  if (typeof detail.tls_error === 'string') {
    return {
      headline: `${headline}; TLS handshake failed (${detail.tls_error})`,
      band: 'bad',
      note: 'The handshake did not complete. Behind an intercepting proxy this usually means the interception certificate is not in the trust store this process uses.'
    };
  }
  if (tls.verified_against_default_bundle === false) {
    return {
      headline: `${headline}; issuer ${String(tls.issuer_cn ?? 'unknown')}`,
      band: 'bad',
      note: 'The presented certificate does not verify against the default trust store, which means TLS is being intercepted. The issuer CN names the interceptor.'
    };
  }
  const skew = num(http.clock_skew_s);
  if (skew !== undefined && Math.abs(skew) > 60) {
    return {
      headline: `${headline}; clock skew ${skew.toFixed(0)}s`,
      band: 'warn',
      note: 'This host disagrees with the endpoint about the time by more than a minute, which breaks token expiry and certificate validity windows.'
    };
  }
  const level = band(Math.max(dns ?? 0, tcp ?? 0, handshake ?? 0), 200, 1000);
  return {
    headline: tls.issuer_cn
      ? `${headline}; issuer ${String(tls.issuer_cn)}`
      : headline,
    band: level,
    note:
      level === 'bad' || level === 'warn'
        ? 'Connection setup alone costs this much before any model work starts, and every cold connection to the gateway pays it.'
        : undefined
  };
}

function runtimeReading(check: IPerfProbeCheck): IProbeReading {
  const detail = check.detail ?? {};
  if (check.id === 'runtime.process_rss') {
    const kb = num(detail.max_rss_kb);
    return {
      headline:
        kb === undefined
          ? 'n/a'
          : `${(kb / 1024).toFixed(0)} MB peak since server start`
    };
  }
  if (check.id === 'runtime.loadavg') {
    const fmt = (v: unknown) => num(v)?.toFixed(2) ?? 'n/a';
    return {
      headline: `1m ${fmt(detail.load1)}, 5m ${fmt(detail.load5)}, 15m ${fmt(detail.load15)}`
    };
  }
  if (check.id === 'runtime.cgroup_cpu') {
    const throttled = Number(detail.nr_throttled ?? 0);
    return {
      headline: `nr_throttled ${String(detail.nr_throttled ?? 'n/a')}`,
      band: throttled > 0 ? 'warn' : 'ok',
      note:
        throttled > 0
          ? 'This container has been CPU-throttled by its cgroup quota, so local phases (context preparation, spawn) can look slow for reasons unrelated to storage or the network.'
          : undefined
    };
  }
  if (check.id === 'runtime.interpreter') {
    return {
      headline: `${String(detail.python_version ?? '')} on ${String(detail.platform ?? '')}`
    };
  }
  return { headline: JSON.stringify(detail) };
}

function probeReading(check: IPerfProbeCheck): IProbeReading {
  if (check.status === 'skipped') {
    return {
      headline: `skipped: ${String(check.detail?.reason ?? 'not applicable')}`
    };
  }
  if (check.status === 'timed_out') {
    return {
      headline: `timed out after ${String(check.detail?.timeout_s ?? '?')}s`,
      band: 'bad',
      note: 'The check did not return within its budget. A hung stat on a network filesystem looks exactly like this, and is itself the finding.'
    };
  }
  if (check.status === 'error') {
    return {
      headline: `failed (${String(check.detail?.exception_class ?? 'error')})`,
      band: 'warn'
    };
  }

  const detail = check.detail ?? {};
  if (check.id.endsWith('.latency')) {
    return latencyReading(detail);
  }
  if (check.id.endsWith('.sustained_io')) {
    return sustainedReading(detail);
  }
  if (check.id.endsWith('.mount')) {
    return mountReading(detail);
  }
  if (check.id.endsWith('.session_scan')) {
    return sessionScanReading(detail);
  }
  if (check.group === 'subprocess') {
    return subprocessReading(detail);
  }
  if (check.group === 'runtime') {
    return runtimeReading(check);
  }
  if (check.group === 'network') {
    return networkReading(detail);
  }
  return { headline: JSON.stringify(detail) };
}

function badgeText(check: IPerfProbeCheck, reading: IProbeReading): string {
  if (check.status !== 'ok') {
    return check.status === 'timed_out' ? 'timed out' : check.status;
  }
  return reading.band ?? 'info';
}

function ProbeChecksTable(props: { checks: IPerfProbeCheck[] }): JSX.Element {
  const groups = new Map<string, IPerfProbeCheck[]>();
  for (const check of props.checks) {
    const list = groups.get(check.group) ?? [];
    list.push(check);
    groups.set(check.group, list);
  }
  const ordered = [
    ...PROBE_GROUP_ORDER.filter(g => groups.has(g)),
    ...[...groups.keys()].filter(g => !PROBE_GROUP_ORDER.includes(g))
  ];

  return (
    <div className="nbi-perf-probe-groups">
      {ordered.map(group => (
        <div className="nbi-perf-probe-group" key={group}>
          <div className="nbi-perf-subtitle">
            {PROBE_GROUP_LABELS[group] ?? group}
          </div>
          {(groups.get(group) ?? []).map(check => {
            const reading = probeReading(check);
            const badge = badgeText(check, reading);
            return (
              <div className="nbi-perf-check" key={check.id}>
                <div className="nbi-perf-check-head">
                  <span
                    className={`nbi-perf-badge nbi-perf-badge-${reading.band ?? 'none'}`}
                    title={`Check status: ${check.status}`}
                  >
                    {badge}
                  </span>
                  <span className="nbi-perf-check-label">
                    {checkLabel(check)}
                  </span>
                  <span className="nbi-perf-check-value">
                    {reading.headline}
                  </span>
                </div>
                {reading.note && (
                  <div className="nbi-perf-check-note">{reading.note}</div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
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
  const [showRawProbe, setShowRawProbe] = useState(false);

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
        <div className="nbi-perf-lede">
          Records where each chat turn spends its time, so a slow deployment can
          be told apart from a slow model. Off by default, and when off it costs
          one boolean check per turn. Prompts, responses, file contents, and
          hostnames are never recorded.
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
          <label
            className="nbi-perf-control"
            title={
              perfLocked
                ? lockedTip(perfLocked)
                : 'Also append each turn as JSON Lines under the perf log directory, for collecting across sessions or machines.'
            }
          >
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
        <div className="nbi-perf-hint">
          {config.attr_detail === 'redacted'
            ? 'Redacted: file basenames and model, tool, and server names are hashed. Timings and counts are unaffected. Keep this when the report will leave your machine.'
            : 'Full: file basenames and model, tool, and server names are recorded as written. Easier to read locally; check it before pasting into a ticket.'}
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
          <>
            <div className="nbi-perf-hint">
              Compare <strong>Active</strong> against <strong>API ms</strong>:
              close together means the time is in the model or the gateway, far
              apart means it is local. Expand a row for that turn&apos;s phase
              breakdown.
            </div>
            <PerfTurnsTable turns={report?.turns ?? []} />
          </>
        )}
      </div>

      <div className="nbi-perf-section">
        <div className="nbi-perf-section-header">
          <div className="nbi-perf-title">Probe</div>
        </div>
        <div className="nbi-perf-lede">
          Measures this machine rather than a turn: filesystem latency and
          throughput for the config and <code>~/.claude</code> directories,
          interpreter and CLI cold-start cost, and host contention. It writes
          and deletes small temporary files in the directories it measures.
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
                ? 'Adds connections to your configured endpoint to time DNS, TCP, and TLS and to capture the presented certificate.'
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

        {probing && (
          <div className="nbi-perf-empty">
            Running the filesystem, subprocess, and contention checks one at a
            time so they do not measure each other
            {includeNetwork ? ', plus the network check' : ''}. This takes a few
            seconds, longer on a slow filesystem.
          </div>
        )}

        {awaitingConfirm && (
          <div className="nbi-perf-confirm">
            <VscWarning />
            <span>
              {report?.probe_target
                ? `This opens connections to ${report.probe_target} from this machine: two unauthenticated TLS connections to time the handshake and check the certificate, and one unauthenticated HTTP request.`
                : 'This opens unauthenticated connections to your configured endpoint from this machine.'}
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
              <button
                className="jp-Dialog-button jp-mod-reject jp-mod-styled"
                aria-expanded={showRawProbe}
                onClick={() => setShowRawProbe(!showRawProbe)}
              >
                <div className="jp-Dialog-buttonLabel">
                  {showRawProbe ? 'Hide raw output' : 'Show raw output'}
                </div>
              </button>
              <CopyButton label="Copy as JSON" getValue={() => probeResult} />
            </div>
            <ProbeChecksTable checks={probeResult.checks ?? []} />
            {showRawProbe && (
              <pre className="nbi-perf-pre">
                {JSON.stringify(probeResult, null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

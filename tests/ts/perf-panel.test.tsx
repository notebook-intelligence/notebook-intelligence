// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('../../src/handler', () => ({
  requestAPI: jest.fn()
}));

jest.mock('../../src/api', () => ({
  NBIAPI: {
    config: {
      capabilities: {},
      settingLocks: {},
      featurePolicies: {}
    },
    configChanged: {
      connect: jest.fn(),
      disconnect: jest.fn()
    },
    setConfig: jest.fn()
  }
}));

jest.mock('../../src/utils', () => ({
  writeTextToClipboard: jest.fn().mockResolvedValue(true)
}));

import { requestAPI } from '../../src/handler';
import { NBIAPI } from '../../src/api';
import { writeTextToClipboard } from '../../src/utils';
import { SettingsPanelComponentPerf } from '../../src/components/perf-panel';

const mockRequestAPI = requestAPI as jest.Mock;
const mockWriteTextToClipboard = writeTextToClipboard as jest.Mock;

const sampleTurn = {
  turn_id: 't1',
  message_id: 'm1',
  mode: 'ask',
  model: 'claude-x',
  status: 'ok',
  t_wall: 1755907200.123, // backend sends time.time(), not an ISO string
  total_ms: 1200,
  active_ms: 1100,
  spans: [
    { name: 'connect', dur_ms: 50, status: 'ok', attrs: {} },
    { name: 'stream', dur_ms: 700, status: 'ok', attrs: {} },
    { name: 'tool:read_file', dur_ms: 40, status: 'ok', attrs: {} },
    { name: 'tool:write_file', dur_ms: 60, status: 'ok', attrs: {} }
  ],
  // first_token is an event mark from the backend, never a span.
  events: [{ name: 'first_token', t_ms: 300 }],
  tokens: { input: 123, output: 456 },
  sdk: { duration_ms: 1150, duration_api_ms: 900, num_turns: 1 }
};

const sampleReport = {
  schema_version: 1,
  turns: [sampleTurn],
  aggregates: {}
};

// The probe target rides the capabilities response, not the report: the
// report is what users paste into tickets and is documented as carrying no
// hostname.
const PROBE_TARGET = 'https://internal-gateway.example.corp:8443';

describe('SettingsPanelComponentPerf', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockWriteTextToClipboard.mockResolvedValue(true);
    (NBIAPI as any).config.capabilities = {
      perf_probe_target: PROBE_TARGET
    };
    (NBIAPI as any).config.settingLocks = {};
    (NBIAPI as any).config.featurePolicies = {};
  });

  it('renders the turns table from a fixture report matching the TurnDoc contract', async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);

    await screen.findByText('claude-x');
    expect(screen.getByText('ask')).toBeInTheDocument();
    expect(screen.getByText('ok')).toBeInTheDocument();
    expect(screen.getByText('1200 ms')).toBeInTheDocument(); // total_ms
    expect(screen.getByText('1100 ms')).toBeInTheDocument(); // active_ms
    expect(screen.getByText('50 ms')).toBeInTheDocument(); // spawn (connect)
    expect(screen.getByText('300 ms')).toBeInTheDocument(); // first token
    expect(screen.getByText('700 ms')).toBeInTheDocument(); // stream
    expect(screen.getByText('100 ms')).toBeInTheDocument(); // tools sum
    expect(screen.getByText('123')).toBeInTheDocument(); // tokens in
    expect(screen.getByText('456')).toBeInTheDocument(); // tokens out
    expect(screen.getByText('900 ms')).toBeInTheDocument(); // sdk.duration_api_ms
    expect(mockRequestAPI).toHaveBeenCalledWith('perf/report');
  });

  it('shows a friendly empty state when the report endpoint 404s', async () => {
    const notFound: any = new Error('Not Found');
    notFound.response = { status: 404 };
    mockRequestAPI.mockRejectedValueOnce(notFound);

    render(<SettingsPanelComponentPerf />);

    await screen.findByText(/Performance diagnostics are disabled/);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows a retry button on a network error and reloads on click', async () => {
    mockRequestAPI.mockRejectedValueOnce(new Error('network down'));
    render(<SettingsPanelComponentPerf />);

    await screen.findByText(/Failed to load report: network down/);

    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await screen.findByText('claude-x');
  });

  it('requires an explicit confirmation before probing with network included', async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    fireEvent.click(screen.getByLabelText('Include network check'));

    expect(screen.getByText(/opens connections to/)).toBeInTheDocument();
    // Checking the box alone must not have triggered the probe yet.
    expect(mockRequestAPI).toHaveBeenCalledTimes(1);

    const probeDoc = {
      schema_version: 1,
      generated_at: '2026-08-23T00:00:00Z',
      checks: [{ id: 'fs', group: 'fs', status: 'ok', detail: {} }]
    };
    mockRequestAPI.mockResolvedValueOnce(probeDoc);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm and run' }));

    await waitFor(() =>
      expect(mockRequestAPI).toHaveBeenCalledWith('perf/probe', {
        method: 'POST',
        body: JSON.stringify({ network: true })
      })
    );
  });

  it('runs a probe without the network flag when the box is unchecked', async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    const probeDoc = {
      schema_version: 1,
      generated_at: '2026-08-23T00:00:00Z',
      checks: [{ id: 'fs', group: 'fs', status: 'ok', detail: {} }]
    };
    mockRequestAPI.mockResolvedValueOnce(probeDoc);
    fireEvent.click(screen.getByRole('button', { name: 'Run probe' }));

    await waitFor(() =>
      expect(mockRequestAPI).toHaveBeenCalledWith('perf/probe', {
        method: 'POST',
        body: JSON.stringify({ network: false })
      })
    );
    expect(
      screen.queryByText(
        /unauthenticated connections to your configured endpoint/
      )
    ).not.toBeInTheDocument();
  });

  it('renders a locked state when settingLocks.perf_diagnostics_enabled is present', async () => {
    (NBIAPI as any).config.settingLocks = {
      perf_diagnostics_enabled: { locked: true }
    };
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    const enabledCheckbox = screen.getByLabelText('Enabled');
    expect(enabledCheckbox).toBeDisabled();
  });

  it('degrades gracefully when settingLocks has no perf_diagnostics_enabled key', async () => {
    (NBIAPI as any).config.settingLocks = {};
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    const enabledCheckbox = screen.getByLabelText('Enabled');
    expect(enabledCheckbox).not.toBeDisabled();
  });

  it('locks the checkbox when the perf_diagnostics feature policy is locked, even with no settingLocks entry', async () => {
    (NBIAPI as any).config.settingLocks = {};
    (NBIAPI as any).config.featurePolicies = {
      perf_diagnostics: { enabled: true, locked: true }
    };
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    const enabledCheckbox = screen.getByLabelText('Enabled');
    expect(enabledCheckbox).toBeDisabled();
  });

  it('copies a turns report that carries no hostname', async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    fireEvent.click(screen.getByRole('button', { name: 'Copy as JSON' }));

    await waitFor(() =>
      expect(mockWriteTextToClipboard).toHaveBeenCalledTimes(1)
    );
    const copiedText = mockWriteTextToClipboard.mock.calls[0][0] as string;
    expect(copiedText).not.toContain('probe_target');
    expect(copiedText).not.toContain('internal-gateway');
    const copied = JSON.parse(copiedText);
    expect(copied.turns).toHaveLength(1);
  });

  it('names the probe target from capabilities in the confirm prompt', async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    fireEvent.click(screen.getByLabelText('Include network check'));

    expect(screen.getByText(new RegExp(PROBE_TARGET))).toBeInTheDocument();
  });

  it('falls back to a generic confirm prompt when capabilities name no target', async () => {
    (NBIAPI as any).config.capabilities = {};
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    fireEvent.click(screen.getByLabelText('Include network check'));

    expect(
      screen.getByText(
        /unauthenticated connections to your configured endpoint/
      )
    ).toBeInTheDocument();
  });

  it('disables the network check checkbox when perf_probe_network_allowed is false', async () => {
    (NBIAPI as any).config.capabilities = {
      perf_probe_network_allowed: false,
      perf_probe_target: PROBE_TARGET
    };
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    const networkCheckbox = screen.getByLabelText('Include network check');
    expect(networkCheckbox).toBeDisabled();

    // Clicking a disabled checkbox must not flip it or open the confirm
    // dialog; jsdom still delivers the event, so the component itself must
    // guard against it.
    fireEvent.click(networkCheckbox);
    expect(
      screen.queryByText(
        /unauthenticated connections to your configured endpoint/
      )
    ).not.toBeInTheDocument();
  });

  it('leaves the network check checkbox enabled when perf_probe_network_allowed is absent (fail-open default)', async () => {
    (NBIAPI as any).config.capabilities = {};
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    const networkCheckbox = screen.getByLabelText('Include network check');
    expect(networkCheckbox).not.toBeDisabled();
  });

  it('renders a verdict naming the gateway when api time dominates active time', async () => {
    // sampleTurn: active_ms 1100, duration_api_ms 900. 900 >= 0.7 * 1100, so
    // the time is on the far side of the network.
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    expect(screen.getByText('Model or gateway')).toBeInTheDocument();
  });

  it('attributes a turn to agent cold start when connect dominates', async () => {
    const coldTurn = {
      ...sampleTurn,
      turn_id: 't-cold',
      active_ms: 4000,
      total_ms: 4000,
      spans: [{ name: 'connect', dur_ms: 3000, status: 'ok', attrs: {} }],
      sdk: { duration_ms: 4000, duration_api_ms: 200, num_turns: 1 }
    };
    mockRequestAPI.mockResolvedValueOnce({
      ...sampleReport,
      turns: [coldTurn]
    });
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    expect(screen.getByText('Agent cold start')).toBeInTheDocument();
  });

  it('counts stall events into the Stalls column', async () => {
    const stallTurn = {
      ...sampleTurn,
      turn_id: 't-stall',
      events: [
        { name: 'first_token', t_ms: 300 },
        { name: 'stall', t_ms: 600, attrs: { after: 'tool_use' } },
        { name: 'stall', t_ms: 900, attrs: { after: 'text' } }
      ]
    };
    mockRequestAPI.mockResolvedValueOnce({
      ...sampleReport,
      turns: [stallTurn]
    });
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    // The stall count is rendered as a bare number in its own cell.
    const cells = screen.getAllByRole('cell').map(c => c.textContent);
    expect(cells).toContain('2');
  });

  it("expands a row into that turn's spans and events", async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    // Span names live only in the collapsed detail, never in the summary row.
    expect(screen.queryByText('tool:read_file')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Show timeline/ }));

    expect(screen.getByText('tool:read_file')).toBeInTheDocument();
    expect(screen.getByText('tool:write_file')).toBeInTheDocument();
    // first_token is an event, listed under Events with its offset.
    expect(screen.getByText('Events')).toBeInTheDocument();
    expect(screen.getByText('first_token')).toBeInTheDocument();
  });

  it('tells the user to run a turn when recording is on but nothing is captured', async () => {
    mockRequestAPI.mockResolvedValueOnce({ ...sampleReport, turns: [] });
    render(<SettingsPanelComponentPerf />);

    await screen.findByText(/no turns have been captured yet/);
    // The disabled-state message is a different one and must not appear.
    expect(
      screen.queryByText(/Performance diagnostics are disabled/)
    ).not.toBeInTheDocument();
  });

  it('groups probe checks and flags a slow filesystem with a plain-language note', async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    const probeDoc = {
      schema_version: 1,
      generated_at: '2026-08-23T00:00:00Z',
      checks: [
        {
          id: 'fs.claude_home.latency',
          group: 'filesystem',
          status: 'ok',
          detail: {
            stat_ms: { median_ms: 8.2 },
            write_fsync_unlink_ms: { median_ms: 120.5 },
            n_completed: 20
          }
        },
        {
          id: 'fs.claude_home.mount',
          group: 'filesystem',
          status: 'ok',
          detail: { fstype: 'nfs4', options: 'rw,relatime' }
        },
        {
          id: 'subprocess.node_version',
          group: 'subprocess',
          status: 'ok',
          detail: { wall_ms: 42.0, returncode: 0, version: 'v20.11.0' }
        },
        {
          id: 'runtime.cgroup_cpu',
          group: 'runtime',
          status: 'skipped',
          detail: { reason: 'cgroup cpu.stat not readable' }
        }
      ]
    };
    mockRequestAPI.mockResolvedValueOnce(probeDoc);
    fireEvent.click(screen.getByRole('button', { name: 'Run probe' }));

    await screen.findByText('Filesystem');
    expect(screen.getByText('Subprocess startup')).toBeInTheDocument();
    expect(screen.getByText('Process and host')).toBeInTheDocument();

    // Check ids are translated into something a human can act on.
    expect(
      screen.getByText('~/.claude: small-file latency')
    ).toBeInTheDocument();
    expect(screen.getByText('node --version')).toBeInTheDocument();

    // A 120ms fsync median is over the bad threshold and carries the note.
    expect(
      screen.getByText(/signature of a network filesystem/)
    ).toBeInTheDocument();
    // nfs4 is called out rather than left as a bare string.
    expect(screen.getByText('nfs4 (rw,relatime)')).toBeInTheDocument();

    // A skipped check reads as skipped, not as a healthy one.
    expect(
      screen.getByText(/skipped: cgroup cpu.stat not readable/)
    ).toBeInTheDocument();

    // The raw JSON is available but not in the way by default.
    expect(screen.queryByText(/"schema_version"/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show raw output' }));
    expect(screen.getByText(/"schema_version"/)).toBeInTheDocument();
  });

  it('renders a null API duration as n/a rather than 0 ms', async () => {
    // TurnHandle._sdk starts as all-None and only the Claude ResultMessage
    // branch fills it, so every copilot-mode and ACP-mode turn serializes
    // duration_api_ms as null. Rendering that as "0 ms" in the column the
    // docs call the most useful comparison reads as "the model contributed
    // nothing".
    const copilotTurn = {
      ...sampleTurn,
      turn_id: 't-copilot',
      mode: 'copilot',
      sdk: { duration_ms: null, duration_api_ms: null, num_turns: null }
    };
    mockRequestAPI.mockResolvedValueOnce({
      ...sampleReport,
      turns: [copilotTurn]
    });
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    const cells = screen.getAllByRole('cell').map(c => c.textContent);
    expect(cells).toContain('n/a');
    expect(cells).not.toContain('0 ms');
  });

  it('indents nested spans using parent_id', async () => {
    const nestedTurn = {
      ...sampleTurn,
      turn_id: 't-nested',
      spans: [
        {
          name: 'dispatch',
          span_id: 1,
          parent_id: null,
          dur_ms: 1000,
          status: 'ok',
          attrs: {}
        },
        {
          name: 'connect',
          span_id: 2,
          parent_id: 1,
          dur_ms: 50,
          status: 'ok',
          attrs: {}
        }
      ]
    };
    mockRequestAPI.mockResolvedValueOnce({
      ...sampleReport,
      turns: [nestedTurn]
    });
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');
    fireEvent.click(screen.getByRole('button', { name: /Show timeline/ }));

    // Without the depth, a wrapper span sits alongside the phases it
    // contains and its bar swamps them.
    expect(screen.getByText('dispatch')).toHaveStyle({ paddingLeft: '0px' });
    expect(screen.getByText('connect')).toHaveStyle({ paddingLeft: '12px' });
  });

  it('labels the directory-level claude_home check without a dangling colon', async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    mockRequestAPI.mockResolvedValueOnce({
      schema_version: 1,
      generated_at: '2026-08-23T00:00:00Z',
      checks: [
        {
          id: 'fs.claude_home',
          group: 'filesystem',
          status: 'timed_out',
          detail: { timeout_s: 2.0 }
        }
      ]
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run probe' }));

    await screen.findByText('Filesystem');
    // The probe lede also mentions ~/.claude in a <code>, so scope to the
    // check label. The point is that it is not "~/.claude: ".
    const label = document.querySelector('.nbi-perf-check-label');
    expect(label?.textContent).toBe('~/.claude');
    // A hung mount is the finding, not an error to bury.
    expect(screen.getByText(/timed out after 2/)).toBeInTheDocument();
    expect(
      screen.getByText(/hung stat on a network filesystem/)
    ).toBeInTheDocument();
  });

  it('calls out TLS interception and names the issuer', async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    mockRequestAPI.mockResolvedValueOnce({
      schema_version: 1,
      generated_at: '2026-08-23T00:00:00Z',
      checks: [
        {
          id: 'network.endpoint',
          group: 'network',
          status: 'ok',
          detail: {
            target: { scheme: 'https', host: 'gw.corp', port: 443 },
            path: 'via_proxy',
            timings_ms: {
              proxy_dns_ms: 3.1,
              proxy_tcp_connect_ms: 8.4,
              tls_handshake_ms: 41.2
            },
            tls: {
              issuer_cn: 'Acme Interception CA',
              subject_cn: 'gw.corp',
              fingerprint_sha256: ['abc'],
              verified_against_default_bundle: false
            },
            // The HTTP leg verifies, so it fails against an interception
            // certificate; that must not hide the finding above it.
            http: { error: 'URLError' }
          }
        }
      ]
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run probe' }));

    await screen.findByText('Network');
    expect(screen.getByText(/Acme Interception CA/)).toBeInTheDocument();
    expect(screen.getByText(/via proxy/)).toBeInTheDocument();
    expect(
      screen.getByText(/does not verify against the default trust store/)
    ).toBeInTheDocument();
  });

  it('does not warn about a local filesystem that is performing normally', async () => {
    mockRequestAPI.mockResolvedValueOnce(sampleReport);
    render(<SettingsPanelComponentPerf />);
    await screen.findByText('claude-x');

    mockRequestAPI.mockResolvedValueOnce({
      schema_version: 1,
      generated_at: '2026-08-23T00:00:00Z',
      checks: [
        {
          id: 'fs.nbi_user_dir.latency',
          group: 'filesystem',
          status: 'ok',
          detail: {
            stat_ms: { median_ms: 0.02 },
            write_fsync_unlink_ms: { median_ms: 1.1 },
            n_completed: 20
          }
        },
        {
          id: 'fs.nbi_user_dir.mount',
          group: 'filesystem',
          status: 'ok',
          detail: { fstype: 'apfs', options: 'local' }
        }
      ]
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run probe' }));

    await screen.findByText('Filesystem');
    // The probe's own target label, not the raw check id.
    expect(
      screen.getByText('NBI config directory: small-file latency')
    ).toBeInTheDocument();
    // Sub-millisecond medians keep their precision instead of rounding to
    // "0 ms", which would read as "not measured" rather than "fast".
    expect(screen.getByText(/stat 0\.02 ms/)).toBeInTheDocument();
    expect(
      screen.queryByText(/signature of a network filesystem/)
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Read every latency number in this group/)
    ).not.toBeInTheDocument();
  });
});

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
  aggregates: {},
  probe_target: 'https://internal-gateway.example.corp:8443'
};

describe('SettingsPanelComponentPerf', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockWriteTextToClipboard.mockResolvedValue(true);
    (NBIAPI as any).config.capabilities = {};
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

    expect(
      screen.getByText(/send one unauthenticated request to/)
    ).toBeInTheDocument();
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
      screen.queryByText(/contact an external endpoint/)
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

  it('excludes probe_target from the copied turns report JSON', async () => {
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
    expect(copied.probe_target).toBeUndefined();
    expect(copied.turns).toHaveLength(1);
  });

  it('disables the network check checkbox when perf_probe_network_allowed is false', async () => {
    (NBIAPI as any).config.capabilities = {
      perf_probe_network_allowed: false
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
      screen.queryByText(/contact an external endpoint/)
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
});

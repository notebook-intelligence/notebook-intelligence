// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('../../src/handler', () => ({
  requestAPI: jest.fn()
}));

import { requestAPI } from '../../src/handler';
import {
  ReadinessCard,
  _resetReadinessCacheForTests
} from '../../src/components/readiness-card';

const mockRequestAPI = requestAPI as jest.Mock;

const readyDoc = {
  schema_version: 1,
  generated_at: '2026-08-25T00:00:00Z',
  verdict: 'ready',
  headline: 'Ready. Nothing needs configuring.',
  checks: [
    {
      id: 'mode.active',
      group: 'mode',
      level: 'ok',
      title: 'Active chat path',
      detail: 'Chat is served by Claude mode.'
    }
  ]
};

const brokenDoc = {
  schema_version: 1,
  generated_at: '2026-08-25T00:00:00Z',
  verdict: 'not_ready',
  headline: 'Not ready: claude code cli. The claude CLI was not found.',
  checks: [
    {
      id: 'mode.active',
      group: 'mode',
      level: 'ok',
      title: 'Active chat path',
      detail: 'Chat is served by Claude mode.'
    },
    {
      id: 'claude.cli',
      group: 'claude',
      level: 'blocked',
      title: 'Claude Code CLI',
      detail: 'The claude CLI was not found.',
      remedy: 'Install the Claude Code CLI and make sure it is on the PATH.'
    }
  ]
};

describe('ReadinessCard', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    _resetReadinessCacheForTests();
  });

  it('runs the non-billing check on mount and shows the verdict', async () => {
    mockRequestAPI.mockResolvedValueOnce(readyDoc);
    render(<ReadinessCard />);

    await screen.findByText('Ready');
    expect(
      screen.getByText('Ready. Nothing needs configuring.')
    ).toBeInTheDocument();
    // GET, not the POST that bills.
    expect(mockRequestAPI).toHaveBeenCalledWith('readiness');
  });

  it('shows the remedy for a blocked check, not just the failure', async () => {
    mockRequestAPI.mockResolvedValueOnce(brokenDoc);
    render(<ReadinessCard />);

    await screen.findByText('Not ready');
    expect(
      screen.getByText('The claude CLI was not found.')
    ).toBeInTheDocument();
    // The remedy is the reason this feature exists.
    expect(screen.getByText(/Install the Claude Code CLI/)).toBeInTheDocument();
  });

  it('groups checks under human-readable headings', async () => {
    mockRequestAPI.mockResolvedValueOnce(brokenDoc);
    render(<ReadinessCard />);

    await screen.findByText('Not ready');
    expect(screen.getByText('Chat path')).toBeInTheDocument();
    expect(screen.getByText('Claude mode')).toBeInTheDocument();
  });

  it('requires confirmation before the billing endpoint test', async () => {
    mockRequestAPI.mockResolvedValueOnce(readyDoc);
    render(<ReadinessCard />);
    await screen.findByText('Ready');

    fireEvent.click(screen.getByRole('button', { name: /Test the endpoint/ }));

    expect(screen.getByText(/costs a few tokens/)).toBeInTheDocument();
    // Still only the mount GET: clicking the button must not bill.
    expect(mockRequestAPI).toHaveBeenCalledTimes(1);

    mockRequestAPI.mockResolvedValueOnce(readyDoc);
    fireEvent.click(screen.getByRole('button', { name: 'Run it' }));

    await waitFor(() => expect(mockRequestAPI).toHaveBeenCalledTimes(2));
    expect(mockRequestAPI).toHaveBeenLastCalledWith('readiness', {
      method: 'POST',
      body: JSON.stringify({ live: true })
    });
  });

  it('cancelling the confirmation runs nothing', async () => {
    mockRequestAPI.mockResolvedValueOnce(readyDoc);
    render(<ReadinessCard />);
    await screen.findByText('Ready');

    fireEvent.click(screen.getByRole('button', { name: /Test the endpoint/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByText(/costs a few tokens/)).not.toBeInTheDocument();
    expect(mockRequestAPI).toHaveBeenCalledTimes(1);
  });

  it('re-check re-runs the non-billing path', async () => {
    mockRequestAPI.mockResolvedValueOnce(readyDoc);
    render(<ReadinessCard />);
    await screen.findByText('Ready');

    mockRequestAPI.mockResolvedValueOnce(brokenDoc);
    fireEvent.click(screen.getByRole('button', { name: /Re-check/ }));

    await screen.findByText('Not ready');
    expect(mockRequestAPI).toHaveBeenLastCalledWith('readiness');
  });

  it('surfaces its own failure rather than rendering a false verdict', async () => {
    mockRequestAPI.mockRejectedValueOnce(new Error('server unreachable'));
    render(<ReadinessCard />);

    await screen.findByText(/Readiness check failed: server unreachable/);
    // "Unknown", never "Ready": a check that could not run must not read as a pass.
    expect(screen.getByText('Unknown')).toBeInTheDocument();
    expect(screen.queryByText('Ready')).not.toBeInTheDocument();
  });
  it('a failed re-check clears the stale verdict instead of showing Ready', async () => {
    mockRequestAPI.mockResolvedValueOnce(readyDoc);
    render(<ReadinessCard />);
    await screen.findByText('Ready');

    mockRequestAPI.mockRejectedValueOnce(new Error('server unreachable'));
    fireEvent.click(screen.getByRole('button', { name: /Re-check/ }));

    await screen.findByText(/Readiness check failed: server unreachable/);
    // The one thing this card must never do: a check that could not run
    // reading as a pass, with stale rows under it.
    expect(screen.queryByText('Ready')).not.toBeInTheDocument();
    expect(screen.getByText('Unknown')).toBeInTheDocument();
    expect(screen.queryByText('Active chat path')).not.toBeInTheDocument();
  });

  it('remounting within the cache window does not refetch', async () => {
    mockRequestAPI.mockResolvedValueOnce(readyDoc);
    const first = render(<ReadinessCard />);
    await screen.findByText('Ready');
    expect(mockRequestAPI).toHaveBeenCalledTimes(1);

    // Switching settings tabs unmounts and remounts the card; each real run
    // walks PATH and can fork `claude --version`.
    first.unmount();
    render(<ReadinessCard />);
    await screen.findByText('Ready');
    expect(mockRequestAPI).toHaveBeenCalledTimes(1);
  });
});

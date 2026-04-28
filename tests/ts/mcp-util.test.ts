// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import {
  mcpServerSettingsToEnabledState,
  mcpServerSettingsToServerToolEnabledState
} from '../../src/components/mcp-util';

const SERVERS = [
  {
    id: 'fs',
    tools: [{ name: 'read' }, { name: 'write' }, { name: 'delete' }]
  },
  {
    id: 'web',
    tools: [{ name: 'fetch' }, { name: 'search' }]
  }
];

describe('mcpServerSettingsToServerToolEnabledState', () => {
  it('enables every tool when no settings exist for the server', () => {
    const enabled = mcpServerSettingsToServerToolEnabledState(
      SERVERS,
      {},
      'fs'
    );
    expect(Array.from(enabled!).sort()).toEqual(['delete', 'read', 'write']);
  });

  it('returns null when the server is disabled', () => {
    const enabled = mcpServerSettingsToServerToolEnabledState(
      SERVERS,
      { fs: { disabled: true } },
      'fs'
    );
    expect(enabled).toBeNull();
  });

  it('omits any tool listed in disabled_tools', () => {
    const enabled = mcpServerSettingsToServerToolEnabledState(
      SERVERS,
      { fs: { disabled: false, disabled_tools: ['delete'] } },
      'fs'
    );
    expect(Array.from(enabled!).sort()).toEqual(['read', 'write']);
  });

  it('returns null for an unknown server id', () => {
    expect(
      mcpServerSettingsToServerToolEnabledState(SERVERS, {}, 'missing')
    ).toBeNull();
  });

  it('treats absent disabled_tools the same as an empty list', () => {
    const enabled = mcpServerSettingsToServerToolEnabledState(
      SERVERS,
      { fs: { disabled: false } },
      'fs'
    );
    expect(Array.from(enabled!).sort()).toEqual(['delete', 'read', 'write']);
  });
});

describe('mcpServerSettingsToEnabledState', () => {
  it('builds a per-server map from servers without explicit settings', () => {
    const map = mcpServerSettingsToEnabledState(SERVERS, {});
    expect(map.size).toBe(2);
    expect(Array.from(map.get('fs')!).sort()).toEqual([
      'delete',
      'read',
      'write'
    ]);
    expect(Array.from(map.get('web')!).sort()).toEqual(['fetch', 'search']);
  });

  it('omits servers that are disabled', () => {
    const map = mcpServerSettingsToEnabledState(SERVERS, {
      fs: { disabled: true }
    });
    expect(map.has('fs')).toBe(false);
    expect(Array.from(map.get('web')!).sort()).toEqual(['fetch', 'search']);
  });

  it('respects per-server disabled_tools', () => {
    const map = mcpServerSettingsToEnabledState(SERVERS, {
      web: { disabled: false, disabled_tools: ['fetch'] }
    });
    expect(Array.from(map.get('web')!)).toEqual(['search']);
  });

  it('returns an empty map when there are no servers', () => {
    expect(mcpServerSettingsToEnabledState([], {}).size).toBe(0);
  });
});

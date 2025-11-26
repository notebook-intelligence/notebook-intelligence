// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

export function mcpServerSettingsToEnabledState(
  mcpServers: any,
  mcpServerSettings: any
) {
  const mcpServerEnabledState = new Map<string, Set<string>>();
  for (const server of mcpServers) {
    const mcpServerToolEnabledState = mcpServerSettingsToServerToolEnabledState(
      mcpServers,
      mcpServerSettings,
      server.id
    );
    if (mcpServerToolEnabledState) {
      mcpServerEnabledState.set(server.id, mcpServerToolEnabledState);
    }
  }

  return mcpServerEnabledState;
}

export function mcpServerSettingsToServerToolEnabledState(
  mcpServers: any,
  mcpServerSettings: any,
  serverId: string
) {
  const server = mcpServers.find((server: any) => server.id === serverId);

  let mcpServerToolEnabledState: Set<string> | null = null;

  if (!server) {
    return mcpServerToolEnabledState;
  }

  if (mcpServerSettings[server.id]) {
    const serverSettings = mcpServerSettings[server.id];
    if (!serverSettings.disabled) {
      mcpServerToolEnabledState = new Set<string>();
      for (const tool of server.tools) {
        if (!serverSettings.disabled_tools?.includes(tool.name)) {
          mcpServerToolEnabledState.add(tool.name);
        }
      }
    }
  } else {
    mcpServerToolEnabledState = new Set<string>();
    for (const tool of server.tools) {
      mcpServerToolEnabledState.add(tool.name);
    }
  }

  return mcpServerToolEnabledState;
}

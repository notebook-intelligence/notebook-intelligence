// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import { NBIConfig } from '../../src/api';

describe('NBIConfig.cellOutputFeatures', () => {
  it('defaults to enabled+unlocked when capabilities are absent', () => {
    const config = new NBIConfig();
    expect(config.cellOutputFeatures).toEqual({
      explain_error: { enabled: true, locked: false },
      output_followup: { enabled: true, locked: false },
      output_toolbar: { enabled: true, locked: false }
    });
  });

  it('reflects the resolved enabled+locked state from capabilities', () => {
    const config = new NBIConfig();
    config.capabilities = {
      cell_output_features: {
        explain_error: { enabled: false, locked: true },
        output_followup: { enabled: true, locked: false },
        output_toolbar: { enabled: false, locked: true }
      }
    };
    expect(config.cellOutputFeatures.explain_error).toEqual({
      enabled: false,
      locked: true
    });
    expect(config.cellOutputFeatures.output_followup).toEqual({
      enabled: true,
      locked: false
    });
    expect(config.cellOutputFeatures.output_toolbar).toEqual({
      enabled: false,
      locked: true
    });
  });

  it('treats missing per-feature data as enabled+unlocked', () => {
    const config = new NBIConfig();
    config.capabilities = { cell_output_features: {} };
    expect(config.cellOutputFeatures).toEqual({
      explain_error: { enabled: true, locked: false },
      output_followup: { enabled: true, locked: false },
      output_toolbar: { enabled: true, locked: false }
    });
  });
});

# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Tests for the capabilities-response helper that surfaces the
explain_error / output_followup / output_toolbar feature gates to the
frontend."""

from types import SimpleNamespace

from notebook_intelligence.extension import _build_cell_output_features_response
from notebook_intelligence.feature_flags import (
    POLICY_FORCE_OFF,
    POLICY_FORCE_ON,
    POLICY_USER_CHOICE,
)


def _config(*, explain=True, followup=True, toolbar=True):
    return SimpleNamespace(
        enable_explain_error=explain,
        enable_output_followup=followup,
        enable_output_toolbar=toolbar,
    )


class TestBuildCellOutputFeaturesResponse:
    def test_user_choice_reflects_user_preferences_and_unlocked(self):
        response = _build_cell_output_features_response(
            POLICY_USER_CHOICE,
            POLICY_USER_CHOICE,
            POLICY_USER_CHOICE,
            _config(explain=True, followup=False, toolbar=True),
        )
        assert response == {
            "explain_error": {"enabled": True, "locked": False},
            "output_followup": {"enabled": False, "locked": False},
            "output_toolbar": {"enabled": True, "locked": False},
        }

    def test_force_on_overrides_user_off_and_locks(self):
        response = _build_cell_output_features_response(
            POLICY_USER_CHOICE,
            POLICY_USER_CHOICE,
            POLICY_FORCE_ON,
            _config(toolbar=False),
        )
        assert response["output_toolbar"] == {"enabled": True, "locked": True}

    def test_force_off_overrides_user_on_and_locks(self):
        response = _build_cell_output_features_response(
            POLICY_FORCE_OFF,
            POLICY_USER_CHOICE,
            POLICY_USER_CHOICE,
            _config(explain=True),
        )
        assert response["explain_error"] == {"enabled": False, "locked": True}

    def test_each_feature_resolved_independently(self):
        response = _build_cell_output_features_response(
            POLICY_FORCE_ON,
            POLICY_FORCE_OFF,
            POLICY_USER_CHOICE,
            _config(explain=False, followup=True, toolbar=False),
        )
        assert response == {
            "explain_error": {"enabled": True, "locked": True},
            "output_followup": {"enabled": False, "locked": True},
            "output_toolbar": {"enabled": False, "locked": False},
        }

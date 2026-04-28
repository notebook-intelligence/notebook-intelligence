# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from notebook_intelligence.feature_flags import (
    POLICY_FORCE_OFF,
    POLICY_FORCE_ON,
    POLICY_USER_CHOICE,
    VALID_POLICIES,
    resolve_feature_flag,
)


class TestResolveFeatureFlag:
    def test_user_choice_returns_user_setting_unlocked(self):
        assert resolve_feature_flag(POLICY_USER_CHOICE, True) == (True, False)
        assert resolve_feature_flag(POLICY_USER_CHOICE, False) == (False, False)

    def test_force_on_returns_enabled_locked_regardless_of_user_pref(self):
        assert resolve_feature_flag(POLICY_FORCE_ON, True) == (True, True)
        assert resolve_feature_flag(POLICY_FORCE_ON, False) == (True, True)

    def test_force_off_returns_disabled_locked_regardless_of_user_pref(self):
        assert resolve_feature_flag(POLICY_FORCE_OFF, True) == (False, True)
        assert resolve_feature_flag(POLICY_FORCE_OFF, False) == (False, True)

    def test_unknown_policy_falls_back_to_user_choice(self):
        # Fail open: a typo'd policy must not lock users out of features.
        assert resolve_feature_flag("nonsense", True) == (True, False)
        assert resolve_feature_flag("", False) == (False, False)


class TestPolicyConstants:
    def test_valid_policies_lists_the_three_known_values(self):
        assert set(VALID_POLICIES) == {
            POLICY_USER_CHOICE,
            POLICY_FORCE_ON,
            POLICY_FORCE_OFF,
        }

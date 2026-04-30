# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from typing import Tuple

POLICY_USER_CHOICE = "user-choice"
POLICY_FORCE_ON = "force-on"
POLICY_FORCE_OFF = "force-off"
VALID_POLICIES = (POLICY_USER_CHOICE, POLICY_FORCE_ON, POLICY_FORCE_OFF)


def resolve_feature_flag(policy: str, user_setting: bool) -> Tuple[bool, bool]:
    """Return ``(enabled, locked)`` for a feature.

    Unknown policy strings fall through to user-choice so a config typo
    fails open rather than locking the user out.
    """
    if policy == POLICY_FORCE_ON:
        return True, True
    if policy == POLICY_FORCE_OFF:
        return False, True
    return bool(user_setting), False

# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>
#
# GitHub auth and inline completion sections are derivative of https://github.com/B00TK1D/copilot-api

import base64
from dataclasses import dataclass
from enum import Enum
import os, json, stat, time, requests, threading
from typing import Any
import uuid
import secrets
import sseclient
import datetime as dt
import logging
from notebook_intelligence.api import BackendMessageType, CancelToken, ChatResponse, CompletionContext, MarkdownData
from notebook_intelligence.config import _atomic_write_json
from notebook_intelligence.inline_completion import (
    chatbook_inline_prefix_hint,
    copilot_inline_language,
    is_chatbook_inline_language,
)
from notebook_intelligence.util import decrypt_with_password, encrypt_with_password, ThreadSafeWebSocketConnector

from ._version import __version__ as NBI_VERSION

log = logging.getLogger(__name__)

GHE_SUBDOMAIN = os.getenv("NBI_GHE_SUBDOMAIN", "")
GH_WEB_BASE_URL = "https://github.com" if GHE_SUBDOMAIN == "" else f"https://{GHE_SUBDOMAIN}.ghe.com"
GH_REST_API_BASE_URL = "https://api.github.com" if GHE_SUBDOMAIN == "" else f"https://api.{GHE_SUBDOMAIN}.ghe.com"

EDITOR_VERSION = f"NotebookIntelligence/{NBI_VERSION}"
EDITOR_PLUGIN_VERSION = f"NotebookIntelligence/{NBI_VERSION}"
USER_AGENT = f"NotebookIntelligence/{NBI_VERSION}"
CLIENT_ID = "Iv1.b507a08c87ecfe98"
MACHINE_ID = secrets.token_hex(33)[0:65]

API_ENDPOINT = "https://api.githubcopilot.com"
PROXY_ENDPOINT = "https://copilot-proxy.githubusercontent.com"
TOKEN_REFRESH_INTERVAL = 1500
ACCESS_TOKEN_THREAD_SLEEP_INTERVAL = 5
TOKEN_THREAD_SLEEP_INTERVAL = 3
TOKEN_FETCH_INTERVAL = 15
NL = '\n'

LoginStatus = Enum('LoginStatus', ['NOT_LOGGED_IN', 'ACTIVATING_DEVICE', 'LOGGING_IN', 'LOGGED_IN'])

github_auth = {
    "verification_uri": None,
    "user_code": None,
    "device_code": None,
    "access_token": None,
    "status" : LoginStatus.NOT_LOGGED_IN,
    "token": None,
    "token_expires_at": dt.datetime.now()
}

# Populated from https://api.githubcopilot.com/models once the user has a
# valid Copilot bearer token. Each entry is a normalized dict:
# {"id": str, "name": str, "context_window": int}. Empty until the first
# successful fetch; the LLM provider falls back to its hardcoded list in
# that case so the settings UI is never blank. Mutated in place by
# `fetch_copilot_models` so module-level `from ... import` references
# stay current.
copilot_models_cache: list[dict] = []

# Per-model HTTP path under API_ENDPOINT. Codex-class models advertise only
# `/responses` (the OpenAI Responses API mirror) and 400 on `/chat/completions`;
# everything else stays on `/chat/completions`. Populated by
# `fetch_copilot_models` from each model entry's `supported_endpoints` field.
# Lookup falls back to `/chat/completions` for unknown ids so the hardcoded
# fallback model list keeps working before the first live fetch.
_COPILOT_CHAT_ENDPOINT = "/chat/completions"
_COPILOT_RESPONSES_ENDPOINT = "/responses"
copilot_model_endpoints: dict[str, str] = {}

# Conservative floor when the Copilot API omits limits for a model. Picked
# to match the smallest token cap any Copilot-served model has historically
# carried, so the token-budget meter (extension.py: `remaining_token_budget`)
# doesn't zero out and strip all additional context.
_COPILOT_DEFAULT_CONTEXT_WINDOW = 4096

stop_requested = False
get_access_code_thread = None
get_token_thread = None
last_token_fetch_time = dt.datetime.now() + dt.timedelta(seconds=-TOKEN_FETCH_INTERVAL)
remember_github_access_token = False
github_access_token_provided = None

websocket_connector: ThreadSafeWebSocketConnector = None
github_login_status_change_updater_enabled = False

def enable_github_login_status_change_updater(enabled: bool):
    global github_login_status_change_updater_enabled
    github_login_status_change_updater_enabled = enabled

def emit_github_login_status_change():
    if github_login_status_change_updater_enabled and websocket_connector is not None:
        websocket_connector.write_message({
            "type": BackendMessageType.GitHubCopilotLoginStatusChange,
            "data": {
                "status": github_auth["status"].name
            }
        })

def get_login_status():
    global github_auth

    response = {
        "status": github_auth["status"].name
    }
    if github_auth["status"] is LoginStatus.ACTIVATING_DEVICE:
        response.update({
            "verification_uri": github_auth["verification_uri"],
            "user_code": github_auth["user_code"]
        })

    return response

deprecated_user_data_file = os.path.join(os.path.expanduser('~'), ".jupyter", "nbi-data.json")
user_data_file = os.path.join(os.path.expanduser('~'), ".jupyter", "nbi", "user-data.json")
DEFAULT_ACCESS_TOKEN_PASSWORD = "nbi-access-token-password"
access_token_password = os.getenv("NBI_GH_ACCESS_TOKEN_PASSWORD", DEFAULT_ACCESS_TOKEN_PASSWORD)


def _is_default_token_password() -> bool:
    return access_token_password == DEFAULT_ACCESS_TOKEN_PASSWORD


# Shared vocab matches _resolve_bool_with_env in extension.py so admins
# don't have to remember a different set of "yes" tokens for this env.
_BOOL_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _BOOL_TRUE_VALUES


def _user_data_dir_is_shared() -> bool:
    """Return True when the directory that holds user-data.json is
    readable or writable by group or other.

    POSIX-only; on Windows the helper returns False (ACL semantics
    don't map onto S_IRGRP/S_IROTH and the deployment threat model is
    different). A True return means at least one read or write bit
    beyond owner is set, so another tenant on a shared filesystem
    could read the file once written, or plant a token by replacing
    the file via a writable parent dir.
    """
    if os.name != "posix":
        return False
    target_dir = os.path.dirname(user_data_file)
    try:
        st = os.stat(target_dir)
    except OSError:
        return False
    risky_bits = stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH
    return bool(st.st_mode & risky_bits)


_default_password_warned = False


def _warn_default_password_once() -> None:
    """Emit the default-password WARNING at most once per process.

    The audit recommends a startup warning on every server boot until
    the password is overridden, but a hot endpoint that calls the
    read/write helpers on every request would spam the log. Once per
    process is the right cadence; operators see it on boot and on
    log rotation.
    """
    global _default_password_warned
    if _default_password_warned or not _is_default_token_password():
        return
    _default_password_warned = True
    if _user_data_dir_is_shared():
        log.warning(
            "Storing the GitHub Copilot token under the default "
            "NBI_GH_ACCESS_TOKEN_PASSWORD on a directory that is "
            "readable by group or other (%s). Set a per-user "
            "NBI_GH_ACCESS_TOKEN_PASSWORD, or set "
            "NBI_ALLOW_DEFAULT_TOKEN_PASSWORD=1 to acknowledge the "
            "risk explicitly.",
            os.path.dirname(user_data_file),
        )
    else:
        log.warning(
            "Storing the GitHub Copilot token under the default "
            "NBI_GH_ACCESS_TOKEN_PASSWORD. Set a per-user password "
            "in multi-tenant deployments."
        )


def _refuse_write_on_shared_default() -> bool:
    """Return True when the write should be refused.

    Refusal is opt-in (``NBI_REFUSE_DEFAULT_TOKEN_PASSWORD_ON_SHARED_FS=1``)
    because flipping the default to refuse would break existing
    single-user deployments where the directory's mode is incidental.
    Once a deployment opts in, the refusal triggers only when both the
    password is the documented default AND the target directory is
    group/other accessible; an admin who knowingly relaxed perms can
    still opt out with ``NBI_ALLOW_DEFAULT_TOKEN_PASSWORD=1``.
    """
    if _env_truthy("NBI_ALLOW_DEFAULT_TOKEN_PASSWORD"):
        return False
    if not _env_truthy("NBI_REFUSE_DEFAULT_TOKEN_PASSWORD_ON_SHARED_FS"):
        return False
    return _is_default_token_password() and _user_data_dir_is_shared()

def read_stored_github_access_token() -> str:
    _warn_default_password_once()
    try:
        if os.path.exists(user_data_file):
            with open(user_data_file, 'r') as file:
                user_data = json.load(file)
        elif os.path.exists(deprecated_user_data_file):
            with open(deprecated_user_data_file, 'r') as file:
                user_data = json.load(file)
        else:
            user_data = {}

        base64_access_token = user_data.get('github_access_token')

        if base64_access_token is not None:
            base64_bytes = base64.b64decode(base64_access_token.encode('utf-8'))
            return decrypt_with_password(access_token_password, base64_bytes).decode('utf-8')
    except Exception as e:
        log.error(f"Failed to read GitHub access token: {e}")

    return None

def _save_user_data(user_data: dict) -> None:
    """Single write path for ``~/.jupyter/nbi/user-data.json``.

    The file is a secret (encrypted GitHub Copilot token). Force 0o600 on
    every write so the file is never group/world-readable, even if a
    prior write under a permissive umask or a manual chmod widened the
    perms. Routing both write sites through this helper keeps the mode
    contract in one place; a future third writer can't accidentally drop
    it.
    """
    _atomic_write_json(user_data_file, user_data, mode=0o600)


def write_github_access_token(access_token: str) -> bool:
    _warn_default_password_once()
    if _refuse_write_on_shared_default():
        log.error(
            "Refusing to write %s: the default NBI_GH_ACCESS_TOKEN_PASSWORD "
            "is in use on a directory that is readable by group or other, "
            "and NBI_REFUSE_DEFAULT_TOKEN_PASSWORD_ON_SHARED_FS is set. "
            "Set NBI_GH_ACCESS_TOKEN_PASSWORD to a per-user secret, "
            "tighten the directory mode, or set "
            "NBI_ALLOW_DEFAULT_TOKEN_PASSWORD=1 to opt out.",
            user_data_file,
        )
        return False
    try:
        encrypted_access_token = encrypt_with_password(access_token_password, access_token.encode())
        base64_bytes = base64.b64encode(encrypted_access_token)
        base64_access_token = base64_bytes.decode('utf-8')

        if os.path.exists(user_data_file):
            with open(user_data_file, 'r') as file:
                user_data = json.load(file)
        else:
            user_data = {}

        user_data.update({
            'github_access_token': base64_access_token
        })
        _save_user_data(user_data)
        return True
    except Exception as e:
        log.error(f"Failed to write GitHub access token: {e}")

    return False

def delete_stored_github_access_token() -> bool:
    if not os.path.exists(user_data_file):
        return False

    try:
        user_data = {}
        with open(user_data_file, 'r') as file:
            user_data = json.load(file)
        user_data.pop('github_access_token', None)

        _save_user_data(user_data)
        return True
    except Exception as e:
        log.error(f"Failed to delete GitHub access token: {e}")

    return False

def login_with_existing_credentials(store_access_token: bool):
    global github_access_token_provided, remember_github_access_token

    if github_auth["status"] is not LoginStatus.NOT_LOGGED_IN:
        return

    if store_access_token:
        github_access_token_provided = read_stored_github_access_token()
        remember_github_access_token = True
    else:
        delete_stored_github_access_token()

    if github_access_token_provided is not None:
        login()
        if os.path.exists(deprecated_user_data_file):
            # TODO: remove after 12/2025
            log.warning(f"Deprecated user data file found: {deprecated_user_data_file}. Removing it now. Use {user_data_file} instead.")
            store_github_access_token()
            os.remove(deprecated_user_data_file)

def store_github_access_token():
    access_token = github_auth["access_token"]
    if access_token is not None:
        if not write_github_access_token(access_token):
            log.error("Failed to store GitHub access token")

def login():
    login_info = get_device_verification_info()
    if login_info is not None:
        wait_for_tokens()
    return login_info

def logout():
    global github_auth, github_access_token_provided
    github_access_token_provided = None
    github_auth.update({
        "verification_uri": None,
        "user_code": None,
        "device_code": None,
        "access_token": None,
        "status" : LoginStatus.NOT_LOGGED_IN,
        "token": None
    })
    emit_github_login_status_change()

    return {
        "status": github_auth["status"].name
    }

def handle_stop_request():
    global stop_requested
    stop_requested = True

def get_device_verification_info():
    global github_auth
    data = {
        "client_id": CLIENT_ID,
        "scope": "read:user"
    }
    try:
        resp = requests.post(f'{GH_WEB_BASE_URL}/login/device/code',
            headers={
                'accept': 'application/json',
                'editor-version': EDITOR_VERSION,
                'editor-plugin-version': EDITOR_PLUGIN_VERSION,
                'content-type': 'application/json',
                'user-agent': USER_AGENT,
                'accept-encoding': 'gzip,deflate,br'
            },
            data=json.dumps(data)
        )

        resp_json = resp.json()
        github_auth["verification_uri"] = resp_json.get('verification_uri')
        github_auth["user_code"] = resp_json.get('user_code')
        github_auth["device_code"] = resp_json.get('device_code')

        github_auth["status"] = LoginStatus.ACTIVATING_DEVICE
        emit_github_login_status_change()
    except Exception as e:
        log.error(f"Failed to get device verification info: {e}")
        return None

    # user needs to visit the verification_uri and enter the user_code
    return {
        "verification_uri": github_auth["verification_uri"],
        "user_code": github_auth["user_code"]
    }

def wait_for_user_access_token_thread_func():
    global github_auth, get_access_code_thread

    if github_access_token_provided is not None:
        log.info("Using existing GitHub access token")
        github_auth["access_token"] = github_access_token_provided
        get_access_code_thread = None
        return

    while True:
        # terminate thread if logged out or stop requested
        if stop_requested or github_auth["access_token"] is not None or github_auth["device_code"] is None or github_auth["status"] == LoginStatus.NOT_LOGGED_IN:
            get_access_code_thread = None
            break
        data = {
            "client_id": CLIENT_ID,
            "device_code": github_auth["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
        }
        try:
            resp = requests.post(f'{GH_WEB_BASE_URL}/login/oauth/access_token',
                headers={
                'accept': 'application/json',
                'editor-version': EDITOR_VERSION,
                'editor-plugin-version': EDITOR_PLUGIN_VERSION,
                'content-type': 'application/json',
                'user-agent': USER_AGENT,
                'accept-encoding': 'gzip,deflate,br'
                },
                data=json.dumps(data)
            )

            resp_json = resp.json()
            access_token = resp_json.get('access_token')

            if access_token:
                github_auth["access_token"] = access_token
                get_token()
                get_access_code_thread = None
                if remember_github_access_token:
                    store_github_access_token()
                break
        except Exception as e:
            log.error(f"Failed to get access token from GitHub Copilot: {e}")

        time.sleep(ACCESS_TOKEN_THREAD_SLEEP_INTERVAL)

def get_token():
    global github_auth, github_access_token_provided, API_ENDPOINT, PROXY_ENDPOINT, TOKEN_REFRESH_INTERVAL
    access_token = github_auth["access_token"]

    if access_token is None:
        return

    github_auth["status"] = LoginStatus.LOGGING_IN
    emit_github_login_status_change()

    try:
        resp = requests.get(f'{GH_REST_API_BASE_URL}/copilot_internal/v2/token', headers={
            'authorization': f'token {access_token}',
            'editor-version': EDITOR_VERSION,
            'editor-plugin-version': EDITOR_PLUGIN_VERSION,
            'user-agent': USER_AGENT
        })

        resp_json = resp.json()

        if resp.status_code == 401:
            github_access_token_provided = None
            logout()
            wait_for_tokens()
            return

        if resp.status_code != 200:
            log.error(f"Failed to get token from GitHub Copilot: {resp_json}")
            return

        token = resp_json.get('token')
        github_auth["token"] = token
        expires_at = resp_json.get('expires_at')
        if expires_at is not None:
            github_auth["token_expires_at"] = dt.datetime.fromtimestamp(expires_at)
        else:
            github_auth["token_expires_at"] = dt.datetime.now() + dt.timedelta(seconds=TOKEN_REFRESH_INTERVAL)
        github_auth["verification_uri"] = None
        github_auth["user_code"] = None
        github_auth["status"] = LoginStatus.LOGGED_IN

        endpoints = resp_json.get('endpoints', {})
        API_ENDPOINT = endpoints.get('api', API_ENDPOINT)
        PROXY_ENDPOINT = endpoints.get('proxy', PROXY_ENDPOINT)
        TOKEN_REFRESH_INTERVAL = resp_json.get('refresh_in', TOKEN_REFRESH_INTERVAL)

        # Populate the chat-model catalogue before announcing LOGGED_IN so
        # the frontend's capabilities refetch (triggered by the status
        # change) picks up the dynamic list in the same round trip.
        # Best-effort: failure logs and falls back to the provider's
        # hardcoded list so the settings UI is never blank.
        fetch_copilot_models()

        emit_github_login_status_change()
    except Exception as e:
        log.error(f"Failed to get token from GitHub Copilot: {e}")


def fetch_copilot_models() -> list[dict]:
    """Fetch the chat-model catalogue from https://api.githubcopilot.com/models.

    Filters to chat models with `model_picker_enabled = true` (the same
    surface GitHub's official clients expose in their model picker), and
    normalizes each entry to {"id": str, "name": str, "context_window": int}.
    Stores the result in ``copilot_models_cache`` (mutated in place) so the
    LLM provider's `chat_models` property reflects the live list on the
    next access. Returns the new list; returns the existing cache unchanged
    on error so a transient outage doesn't blank the dropdown.
    """
    token = github_auth.get("token")
    if not token:
        return list(copilot_models_cache)
    try:
        resp = requests.get(
            f"{API_ENDPOINT}/models",
            headers={
                "authorization": f"Bearer {token}",
                "editor-version": EDITOR_VERSION,
                "editor-plugin-version": EDITOR_PLUGIN_VERSION,
                "user-agent": USER_AGENT,
                # vscode-chat anchors the response to the same model set
                # GitHub's official picker shows. Changing this string would
                # bias the catalogue toward a different surface (inline,
                # JetBrains, etc.); leave it unless that's the intent.
                "copilot-integration-id": "vscode-chat",
            },
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Failed to fetch Copilot models: {e}")
        return list(copilot_models_cache)

    if resp.status_code != 200:
        log.warning(f"Copilot models endpoint returned {resp.status_code}: {resp.text[:200]}")
        return list(copilot_models_cache)

    try:
        payload = resp.json()
    except ValueError as e:
        log.warning(f"Copilot models response was not JSON: {e}")
        return list(copilot_models_cache)

    raw_models = payload.get("data", []) if isinstance(payload, dict) else []
    normalized: list[dict] = []
    endpoints: dict[str, str] = {}
    seen_ids: set[str] = set()
    for entry in raw_models:
        if not isinstance(entry, dict):
            continue
        if not entry.get("model_picker_enabled"):
            continue
        caps = entry.get("capabilities", {}) or {}
        supported = entry.get("supported_endpoints") or []
        if not isinstance(supported, list):
            supported = []
        # Codex-class models advertise only `/responses` and may report a
        # non-chat capability `type`. Accept them when the endpoint list says
        # they're chattable, so they reach the dispatcher in `chat(...)`.
        is_chat_type = caps.get("type") == "chat"
        has_responses = _COPILOT_RESPONSES_ENDPOINT in supported
        if not (is_chat_type or has_responses):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id or model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        name = entry.get("name") or model_id
        limits = caps.get("limits", {}) or {}
        context_window = (
            limits.get("max_context_window_tokens")
            or limits.get("max_prompt_tokens")
            or _COPILOT_DEFAULT_CONTEXT_WINDOW
        )
        try:
            context_window = int(context_window)
        except (TypeError, ValueError):
            context_window = _COPILOT_DEFAULT_CONTEXT_WINDOW
        if context_window <= 0:
            context_window = _COPILOT_DEFAULT_CONTEXT_WINDOW
        # Prefer `/chat/completions` whenever the model advertises it; only
        # route through `/responses` when that's the model's sole supported
        # path. Mirrors codecompanion.nvim's Copilot adapter: dual-listing
        # exists for forward compatibility, but established chat flows are
        # still cheaper on the older endpoint.
        if has_responses and _COPILOT_CHAT_ENDPOINT not in supported:
            endpoints[model_id] = _COPILOT_RESPONSES_ENDPOINT
        else:
            endpoints[model_id] = _COPILOT_CHAT_ENDPOINT
        normalized.append({
            "id": model_id,
            "name": str(name),
            "context_window": context_window,
        })

    # Preserve the existing cache when the API returns an empty (but
    # well-formed) response. Replacing a known-good list with [] would
    # silently flip the provider back to its hardcoded fallback.
    if not normalized and copilot_models_cache:
        log.warning("Copilot models endpoint returned no entries; keeping previous cache.")
        return list(copilot_models_cache)

    copilot_models_cache.clear()
    copilot_models_cache.extend(normalized)
    copilot_model_endpoints.clear()
    copilot_model_endpoints.update(endpoints)
    return list(copilot_models_cache)


def invalidate_copilot_models_cache() -> None:
    """Clear the memoized Copilot chat-model list. Mostly used by tests."""
    copilot_models_cache.clear()
    copilot_model_endpoints.clear()


def _model_chat_endpoint(model_id: str) -> str:
    """Pick the HTTP path for a Copilot chat request.

    Trusts the live catalogue first: `fetch_copilot_models` populates
    `copilot_model_endpoints` after login from each model's
    `supported_endpoints` field. For ids the cache doesn't know (the
    hardcoded fallback list, an offline session, a /models fetch that
    failed), falls back to a substring heuristic: any id containing `codex`
    routes to `/responses`, since the entire Codex family only accepts that
    endpoint. Everything else stays on `/chat/completions`.
    """
    if model_id in copilot_model_endpoints:
        return copilot_model_endpoints[model_id]
    if "codex" in model_id.lower():
        return _COPILOT_RESPONSES_ENDPOINT
    return _COPILOT_CHAT_ENDPOINT

def get_token_thread_func():
    global github_auth, get_token_thread, last_token_fetch_time
    while True:
        # terminate thread if logged out or stop requested
        if stop_requested or github_auth["status"] == LoginStatus.NOT_LOGGED_IN:
            get_token_thread = None
            return
        token = github_auth["token"]
        # update token if 10 seconds or less left to expiration
        if github_auth["access_token"] is not None and (token is None or (dt.datetime.now() - github_auth["token_expires_at"]).total_seconds() > -10):
            if (dt.datetime.now() - last_token_fetch_time).total_seconds() > TOKEN_FETCH_INTERVAL:
                log.info("Refreshing GitHub token")
                get_token()
                last_token_fetch_time = dt.datetime.now()

        time.sleep(TOKEN_THREAD_SLEEP_INTERVAL)

def wait_for_tokens():
    global get_access_code_thread, get_token_thread
    if get_access_code_thread is None:
        get_access_code_thread = threading.Thread(target=wait_for_user_access_token_thread_func)
        get_access_code_thread.start()

    if get_token_thread is None:
        get_token_thread = threading.Thread(target=get_token_thread_func)
        get_token_thread.start()

def generate_copilot_headers():
    global github_auth
    token = github_auth['token']

    return {
        'authorization': f'Bearer {token}',
        'editor-version': EDITOR_VERSION,
        'editor-plugin-version': EDITOR_PLUGIN_VERSION,
        'user-agent': USER_AGENT,
        'content-type': 'application/json',
        'openai-intent': 'conversation-panel',
        'openai-organization': 'github-copilot',
        'copilot-integration-id': 'vscode-chat',
        'x-request-id': str(uuid.uuid4()),
        'vscode-sessionid': str(uuid.uuid4()),
        'vscode-machineid': MACHINE_ID,
    }

def inline_completions(model_id, prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
    global github_auth
    token = github_auth['token']

    prompt = f"# Path: {filename}"

    if cancel_token.is_cancel_requested:
        return ''

    if is_chatbook_inline_language(language):
        prompt += f"{NL}{chatbook_inline_prefix_hint()}"

    if context is not None:
        for item in context.items:
            context_file = f"Compare this snippet from {item.filePath if item.filePath is not None else 'undefined'}:{NL}{item.content}{NL}"
            prompt += "\n# " + "\n# ".join(context_file.split('\n'))

    prompt += f"{NL}{prefix}"

    try:
        if cancel_token.is_cancel_requested:
            return ''
        resp = requests.post(f"{PROXY_ENDPOINT}/v1/engines/{model_id}/completions",
            headers={'authorization': f'Bearer {token}'},
                json={
                'prompt': prompt,
                'suffix': suffix,
                'min_tokens': 500,
                'max_tokens': 2000,
                'temperature': 0,
                'top_p': 1,
                'n': 1,
                'stop': ['<END>', '```'],
                'nwo': 'NotebookIntelligence',
                'stream': True,
                'extra': {
                    'language': copilot_inline_language(language),
                    'next_indent': 0,
                    'trim_by_indentation': True
                }
            }
        )
    except Exception as e:
        log.error(f"Failed to get inline completions: {e}")
        return ''

    if cancel_token.is_cancel_requested:
        return ''

    result = ''

    decoded_response = resp.content.decode()

    resp_text = decoded_response.split('\n')
    for line in resp_text:
        if line.startswith('data: {'):
            json_completion = json.loads(line[6:])
            completion = json_completion.get('choices')[0].get('text')
            if completion:
                result += completion
            # else:
            #     result += '\n'
    
    return result

def _aggregate_streaming_response(client: sseclient.SSEClient) -> dict:
    final_tool_calls = []
    final_content = ''

    def _format_llm_response():
        for tool_call in final_tool_calls:
            if 'arguments' in tool_call['function'] and tool_call['function']['arguments'] == '':
                tool_call['function']['arguments'] = '{}'

        # if tool_call doesnt have name remove (Claude Sonnet 4 issue)
        tool_calls = [tool_call for tool_call in final_tool_calls if 'name' in tool_call['function'] and tool_call['function']]

        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": tool_calls if len(tool_calls) > 0 else None,
                        "content": final_content,
                        "role": "assistant"
                    }
                }
            ]
        }

    for event in client.events():
        if event.data == '[DONE]':
            return _format_llm_response()

        chunk = json.loads(event.data)
        if len(chunk['choices']) == 0:
            continue

        content_chunk = chunk['choices'][0]['delta'].get('content')
        if content_chunk:
            final_content += content_chunk

        for tool_call in chunk['choices'][0]['delta'].get('tool_calls', []):
            if 'index' not in tool_call:
                continue

            index = tool_call['index']

            if index >= len(final_tool_calls):
                tc = tool_call.copy()
                if 'arguments' not in tc:
                    tc['function']['arguments'] = ''
                final_tool_calls.append(tc)
            else:
                if 'arguments' in tool_call['function']:
                    final_tool_calls[index]['function']['arguments'] += tool_call['function']['arguments']

    return _format_llm_response()

def _messages_to_responses_input(messages: list[dict]) -> tuple[list[dict], str | None]:
    """Translate an OpenAI chat-completions `messages` array into the shape
    Copilot's `/responses` endpoint accepts.

    System messages collapse into the top-level ``instructions`` field
    (newline-joined when more than one is present). Assistant tool calls
    expand to `function_call` items, tool results to `function_call_output`
    items, and the remaining role/content pairs pass through with their
    original role. Matches what codecompanion.nvim sends and what OpenAI's
    own Responses migration guide documents.
    """
    instructions_parts: list[str] = []
    input_items: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            if isinstance(content, str) and content:
                instructions_parts.append(content)
            continue
        if role == "tool":
            output = content if isinstance(content, str) else json.dumps(content)
            input_items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id"),
                "output": output,
            })
            continue
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                call_id = tc.get("id") if isinstance(tc, dict) else None
                input_items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments", ""),
                })
            if isinstance(content, str) and content:
                input_items.append({"role": role, "content": content})
            continue
        input_items.append({"role": role, "content": content if content is not None else ""})
    instructions = "\n".join(instructions_parts) if instructions_parts else None
    return input_items, instructions


def _chat_tools_to_responses_tools(tools: list[dict] | None) -> list[dict] | None:
    """Flatten chat-completions tool schemas to Responses-API shape.

    Chat completions nests under ``{type: "function", function: {name, ...}}``;
    Responses expects the function fields at the top level. The transform is
    purely structural so a caller can keep emitting one schema upstream.
    """
    if not tools:
        return None
    out: list[dict] = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            out.append(tool)
            continue
        fn = tool.get("function") or {}
        out.append({
            "type": "function",
            "name": fn.get("name"),
            "description": fn.get("description"),
            "parameters": fn.get("parameters"),
        })
    return out


# Responses-API SSE event types that mean the server gave up mid-stream.
# Treated as hard failures so callers see an exception instead of a silent
# empty assistant turn; `response.incomplete` (max-tokens cap, etc.) is
# included because Codex emits it in place of `response.completed` and the
# partial content alone isn't enough signal for the chat sidebar.
_RESPONSES_TERMINAL_ERROR_EVENTS = frozenset({
    "response.failed",
    "response.error",
    "response.incomplete",
    "error",
})


def _responses_error_message(chunk: dict) -> str:
    """Extract the most descriptive error string a `response.failed` /
    `response.error` event carries. Falls back to the raw type when the
    server omits a message field."""
    if not isinstance(chunk, dict):
        return "GitHub Copilot returned an unparseable error event."
    response_obj = chunk.get("response") or {}
    for source in (chunk, response_obj):
        if not isinstance(source, dict):
            continue
        err = source.get("error") if isinstance(source.get("error"), dict) else None
        if err:
            msg = err.get("message") or err.get("code")
            if isinstance(msg, str) and msg:
                return msg
        msg = source.get("message")
        if isinstance(msg, str) and msg:
            return msg
    return f"GitHub Copilot Responses stream emitted {chunk.get('type', 'unknown')!r} without a message."


def _function_call_item_to_chat_tool(item: dict, *, fallback_arguments: str = "") -> dict | None:
    """Turn a Responses-API `function_call` output item into the
    chat-completions tool_call shape the rest of the codebase expects.
    Returns None if the item should be skipped (wrong type, in-progress
    status).
    """
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return None
    if item.get("status") not in (None, "completed"):
        return None
    return {
        "id": item.get("call_id") or item.get("id"),
        "type": "function",
        "function": {
            "name": item.get("name") or "",
            "arguments": item.get("arguments") or fallback_arguments or "{}",
        },
    }


def _aggregate_responses_streaming(client: sseclient.SSEClient) -> dict:
    """Drain a `/responses` SSE stream and return the same chat-completions
    shape `_aggregate_streaming_response` emits, so callers see one wire
    format regardless of which endpoint served the model.
    """
    final_content = ""
    final_tool_calls: list[dict] = []
    # Some Codex variants stream function-call arguments incrementally as
    # `response.function_call_arguments.delta` events and only ship a
    # zero-byte `arguments` field in the terminal `response.completed`
    # item. Accumulate by `item_id` so we can stitch them back together
    # if the terminal payload comes through empty.
    arg_buffers: dict[str, str] = {}
    for event in client.events():
        if not event.data:
            continue
        try:
            chunk = json.loads(event.data)
        except (ValueError, json.JSONDecodeError):
            continue
        event_type = chunk.get("type")
        if event_type in _RESPONSES_TERMINAL_ERROR_EVENTS:
            raise Exception(_responses_error_message(chunk))
        if event_type == "response.output_text.delta":
            delta = chunk.get("delta")
            if isinstance(delta, str):
                final_content += delta
        elif event_type == "response.function_call_arguments.delta":
            item_id = chunk.get("item_id")
            delta = chunk.get("delta")
            if isinstance(item_id, str) and isinstance(delta, str):
                arg_buffers[item_id] = arg_buffers.get(item_id, "") + delta
        elif event_type == "response.completed":
            resp_obj = chunk.get("response") or {}
            for item in resp_obj.get("output") or []:
                if not isinstance(item, dict):
                    continue
                buffered = arg_buffers.get(item.get("id") or "")
                tc = _function_call_item_to_chat_tool(item, fallback_arguments=buffered or "")
                if tc is not None:
                    final_tool_calls.append(tc)
            break
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": final_content,
                "tool_calls": final_tool_calls if final_tool_calls else None,
            }
        }]
    }


def responses(model_id, messages, tools = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
    """Send a chat turn through Copilot's `/responses` endpoint.

    Required for Codex-class models, which 400 on `/chat/completions`. The
    aggregate and streaming return shapes match `completions()` so callers
    don't need an endpoint branch above this layer.
    """
    aggregate = response is None

    try:
        input_items, instructions = _messages_to_responses_input(messages)
        data: dict[str, Any] = {
            "model": model_id,
            "input": input_items,
            "stream": True,
        }
        if instructions:
            data["instructions"] = instructions
        responses_tools = _chat_tools_to_responses_tools(tools)
        if responses_tools:
            data["tools"] = responses_tools
        if "tool_choice" in options:
            data["tool_choice"] = options["tool_choice"]

        if cancel_token is not None and cancel_token.is_cancel_requested:
            if response is not None:
                response.finish()
            return

        request = requests.post(
            f"{API_ENDPOINT}{_COPILOT_RESPONSES_ENDPOINT}",
            headers=generate_copilot_headers(),
            json=data,
            stream=True,
        )

        if request.status_code != 200:
            msg = f"Failed to get responses from GitHub Copilot: [{request.status_code}]: {request.text}"
            log.error(msg)
            if response is not None:
                response.stream(MarkdownData(msg))
                response.finish()
            raise Exception(msg)

        client = sseclient.SSEClient(request)
        if aggregate:
            return _aggregate_responses_streaming(client)

        # Streaming: translate typed Responses events into chat-completions
        # delta chunks the existing frontend consumer already knows how to
        # render. The shape `{choices: [{delta: {role, content}}]}` matches
        # what `_aggregate_streaming_response` and `response.stream(...)`
        # below produce for `/chat/completions`.
        arg_buffers: dict[str, str] = {}
        for event in client.events():
            if cancel_token is not None and cancel_token.is_cancel_requested:
                response.finish()
                return
            if not event.data:
                continue
            try:
                chunk = json.loads(event.data)
            except (ValueError, json.JSONDecodeError):
                continue
            event_type = chunk.get("type")
            if event_type in _RESPONSES_TERMINAL_ERROR_EVENTS:
                msg = _responses_error_message(chunk)
                log.error(f"GitHub Copilot Responses stream failed: {msg}")
                response.stream(MarkdownData(msg))
                response.finish()
                raise Exception(msg)
            if event_type == "response.output_text.delta":
                delta = chunk.get("delta")
                if isinstance(delta, str) and delta:
                    response.stream({
                        "choices": [{
                            "delta": {"role": "assistant", "content": delta}
                        }]
                    })
            elif event_type == "response.function_call_arguments.delta":
                item_id = chunk.get("item_id")
                delta = chunk.get("delta")
                if isinstance(item_id, str) and isinstance(delta, str):
                    arg_buffers[item_id] = arg_buffers.get(item_id, "") + delta
            elif event_type == "response.completed":
                resp_obj = chunk.get("response") or {}
                tool_calls: list[dict] = []
                for idx, item in enumerate(resp_obj.get("output") or []):
                    if not isinstance(item, dict):
                        continue
                    buffered = arg_buffers.get(item.get("id") or "")
                    tc = _function_call_item_to_chat_tool(item, fallback_arguments=buffered or "")
                    if tc is None:
                        continue
                    tc["index"] = idx
                    tool_calls.append(tc)
                if tool_calls:
                    response.stream({
                        "choices": [{
                            "delta": {"role": "assistant", "tool_calls": tool_calls}
                        }]
                    })
                response.finish()
                return
        response.finish()
        return
    except requests.exceptions.ConnectionError:
        raise Exception("Connection error")
    except Exception as e:
        log.error(f"Failed to get responses from GitHub Copilot: {e}")
        raise e


def chat(model_id, messages, tools = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
    """Endpoint-aware chat entry point. Routes Codex-class models to
    `/responses` and everything else to `/chat/completions`. Callers from
    the LLM provider should go through this rather than `completions(...)`
    directly so per-model dispatch stays in one place.
    """
    if _model_chat_endpoint(model_id) == _COPILOT_RESPONSES_ENDPOINT:
        return responses(model_id, messages, tools, response, cancel_token, options)
    return completions(model_id, messages, tools, response, cancel_token, options)


def completions(model_id, messages, tools = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
    aggregate = response is None

    try:
        data = {
            'model': model_id,
            'messages': messages,
            'tools': tools,
            'temperature': 0,
            'top_p': 1,
            'n': 1,
            'nwo': 'NotebookIntelligence',
            'stream': True
        }

        if not (model_id == 'gpt-5' or model_id == 'gpt-5-mini'):
            data['stop'] = ['<END>']

        if 'tool_choice' in options:
            data['tool_choice'] = options['tool_choice']

        if cancel_token is not None and cancel_token.is_cancel_requested:
            if response is not None:
                response.finish()
            return

        request = requests.post(
            f"{API_ENDPOINT}/chat/completions",
            headers = generate_copilot_headers(),
            json = data,
            stream = True
        )

        if request.status_code != 200:
            msg = f"Failed to get completions from GitHub Copilot: [{request.status_code}]: {request.text}"
            log.error(msg)
            if response is not None:
                response.stream(MarkdownData(msg))
                response.finish()
            raise Exception(msg)

        client = sseclient.SSEClient(request)
        if aggregate:
            return _aggregate_streaming_response(client)
        else:
            for event in client.events():
                if cancel_token is not None and cancel_token.is_cancel_requested:
                    response.finish()
                    return
                if event.data == '[DONE]':
                    response.finish()
                else:
                    response.stream(json.loads(event.data))
        return
    except requests.exceptions.ConnectionError:
        raise Exception("Connection error")
    except Exception as e:
        log.error(f"Failed to get completions from GitHub Copilot: {e}")
        raise e

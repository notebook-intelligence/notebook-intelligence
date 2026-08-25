# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Configuration readiness preflight.

Answers one question: **is this deployment configured to work, and if not,
which specific piece is missing?**

The problem it exists to solve is that a misconfigured NBI fails at the far
end of a chat turn, where the only thing the user sees is a generic apology
suggesting they rewrite their prompt. The real cause (an expired key, a base
URL pointing at the wrong path, a CLI that is not on PATH, a model id the
endpoint no longer serves) is in the server log, which a notebook user never
reads. Four sections of ``docs/troubleshooting.md`` are the same class of
diagnosis written out as manual steps; every one of them is a predicate a
machine can evaluate, which is what this module does.

Two deliberate constraints:

- **Every failing check carries a remedy.** A check that can only say
  "something is wrong" is the generic apology with extra steps. If a check
  cannot name the next action, it should not be a check.
- **The default run bills nothing.** Resolving config, reading credentials,
  probing a CLI, and listing models are free or near-free. The one check that
  costs money (a real completion, which is the only way to prove streaming
  and tool-schema acceptance actually work end to end) is opt-in per run,
  exactly like the perf probe's network leg.

Unlike the perf diagnostics endpoints, readiness is **not** gated behind an
opt-in setting. A user who cannot tell "the admin has not set this up" from
"I did something wrong" needs the answer whether or not diagnostics are on.
"""

from __future__ import annotations

import concurrent.futures
import copy
import datetime
import os
from typing import Any, Optional

from notebook_intelligence.api import ChatResponse
from notebook_intelligence.checks import (
    DEFAULT_TIMEOUT_S,
    SLOW_CHECK_MARGIN_S,
    run_check,
    run_versioned,
    scrub,
)
from notebook_intelligence.util import resolve_claude_cli_path

SCHEMA_VERSION = 1

# Budget for the opt-in live completion. Generous relative to the local
# checks: it crosses a network, and a slow-but-working gateway is a different
# finding from a broken one.
LIVE_TIMEOUT_S = 20.0

# Budget for listing models. Longer than a local check because it can cross a
# network, shorter than the live completion because it does no generation.
PROVIDER_TIMEOUT_S = 8.0

# Every pooled check here goes through run_check, which blocks, so at most
# one task is ever live. A single worker keeps the abandoned-thread cost of a
# hung check to one thread per run rather than up to four.
_POOL_SIZE = 1

# Severity of a check result, independent of whether the check itself ran.
# "blocked" means chat will not work until it is fixed; "warn" means it works
# but something is off; "ok" and "skipped" are self-explanatory.
LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_BLOCKED = "blocked"
LEVEL_SKIPPED = "skipped"

VERDICT_READY = "ready"
VERDICT_DEGRADED = "degraded"
VERDICT_NOT_READY = "not_ready"


def _row(
    check_id: str,
    group: str,
    level: str,
    title: str,
    detail: str,
    remedy: Optional[str] = None,
) -> dict:
    """One readiness row.

    ``title`` names what was checked, ``detail`` says what was found, and
    ``remedy`` (required for anything not ok or skipped) says what to do.
    """
    row = {
        "id": check_id,
        "group": group,
        "level": level,
        "title": title,
        "detail": detail,
    }
    if remedy:
        row["remedy"] = remedy
    return row


def _credential_source(settings_value: Any, env_var: str) -> Optional[str]:
    """Where a credential is coming from, or None if it is absent.

    Returns a source label rather than the value: this document is meant to
    be pasted into a support ticket.
    """
    if isinstance(settings_value, str) and settings_value.strip():
        return "settings"
    if os.environ.get(env_var, "").strip():
        return f"environment ({env_var})"
    return None


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


def _mode_rows(ai_service_manager: Any) -> tuple[list, str]:
    """Which path is actually serving chat. Returns (rows, mode)."""
    try:
        mode = ai_service_manager.active_agent_mode
    except Exception:
        mode = None

    label = {
        "claude": "Claude mode",
        "acp": "ACP agent mode",
    }.get(mode, "the configured chat model provider")

    return (
        [
            _row(
                "mode.active",
                "mode",
                LEVEL_OK,
                "Active chat path",
                f"Chat is served by {label}.",
            )
        ],
        mode or "provider",
    )


# ---------------------------------------------------------------------------
# Native provider path
# ---------------------------------------------------------------------------


def _effective_model_name(models: list, configured_id: str) -> str:
    """The model name a human would recognize, from an already-fetched list.

    For providers whose single ChatModel carries a constant placeholder id
    (openai-compatible, litellm-compatible), the real name is in the model's
    "model_id" property. Takes the list rather than the provider on purpose:
    calling ``provider.chat_models`` again here would re-introduce the
    unbounded network fetch the caller just paid to bound.
    """
    if len(models) == 1:
        try:
            prop = models[0].get_property("model_id")
        except Exception:
            prop = None
        value = getattr(prop, "value", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return configured_id


def _copilot_signed_in() -> Optional[bool]:
    """True/False for GitHub Copilot's login state, None if undeterminable.

    Load-bearing: the Copilot provider falls back to a hardcoded model list
    when it has no token, so "the model list is non-empty" is true for a
    signed-out user and readiness would otherwise report Ready while every
    turn 401s. That is the exact failure this feature exists to prevent, so
    the model-list check is not trusted for this provider without it.
    """
    try:
        from notebook_intelligence import github_copilot

        return github_copilot.get_login_status().get("status") == "LOGGED_IN"
    except Exception:
        return None


def _provider_rows(
    nbi_config: Any, ai_service_manager: Any, pool: concurrent.futures.ThreadPoolExecutor
) -> list:
    rows = []
    chat_model = nbi_config.chat_model or {}
    provider_id = (chat_model.get("provider") or "").strip()
    # nbi_config.chat_model only ever carries provider/model/properties.
    model_id = (chat_model.get("model") or "").strip()

    if not provider_id or provider_id == "none":
        rows.append(
            _row(
                "provider.configured",
                "provider",
                LEVEL_BLOCKED,
                "Chat model",
                "No chat model provider is selected.",
                "Open NBI Settings and pick a provider and model under General, "
                "or enable Claude mode.",
            )
        )
        return rows

    provider = None
    try:
        provider = ai_service_manager.get_llm_provider(provider_id)
    except Exception:
        provider = None

    if provider is None:
        rows.append(
            _row(
                "provider.configured",
                "provider",
                LEVEL_BLOCKED,
                "Chat model provider",
                f"Provider '{provider_id}' is selected but not registered.",
                "The provider may be disabled by an administrator "
                "(see disabled_providers) or supplied by an extension that is "
                "not installed. Pick a different provider in NBI Settings.",
            )
        )
        return rows

    # Model list. An empty list is the single most common symptom of a bad
    # key or an unreachable base URL, and it is what "no models available"
    # in the troubleshooting guide is really about.
    # Bounded: LLMProvider.chat_models is a documented extension point and
    # several implementations fetch the list over HTTP. Calling it inline
    # would let a hung endpoint wedge the very request that exists to
    # diagnose hung endpoints.
    listed = run_check(
        pool,
        "provider.models",
        "provider",
        PROVIDER_TIMEOUT_S,
        # Return the model objects, not just ids: the effective-name lookup
        # below needs them and must not trigger a second fetch.
        lambda p=provider: {"models": list(p.chat_models)},
    )
    if listed["status"] == "timed_out":
        rows.append(
            _row(
                "provider.models",
                "provider",
                LEVEL_BLOCKED,
                "Model list",
                f"The provider did not return a model list within {PROVIDER_TIMEOUT_S:.0f}s.",
                "The endpoint accepted the connection but did not answer. "
                "Check the Base URL and whether it is reachable from the "
                "Jupyter server process.",
            )
        )
        return rows
    if listed["status"] != "ok":
        rows.append(
            _row(
                "provider.models",
                "provider",
                LEVEL_BLOCKED,
                "Model list",
                f"Listing models failed ({listed['detail'].get('exception_class', 'error')}).",
                "Check the API key and Base URL for this provider in NBI "
                "Settings, and that the endpoint is reachable from the "
                "Jupyter server process.",
            )
        )
        return rows
    model_objects = listed["detail"]["models"]
    models = [m.id for m in model_objects]
    # The openai-compatible and litellm-compatible providers expose a single
    # model whose `.id` is a constant placeholder; the model the user actually
    # typed lives in a "model_id" property. Reporting the placeholder tells a
    # support engineer nothing.
    effective_model = _effective_model_name(model_objects, model_id)
    rows.append(
        _row(
            "provider.configured",
            "provider",
            LEVEL_OK,
            "Chat model provider",
            f"{provider_id}"
            + (f", model '{effective_model}'" if effective_model else ", no model selected"),
        )
    )

    if not models:
        rows.append(
            _row(
                "provider.models",
                "provider",
                LEVEL_BLOCKED,
                "Model list",
                "The provider returned no models.",
                "Usually an expired or missing API key, or a Base URL that "
                "does not point at the provider's API root. For GitHub "
                "Copilot, sign in again from NBI Settings.",
            )
        )
        return rows

    if provider_id == "github-copilot":
        signed_in = _copilot_signed_in()
        if signed_in is False:
            rows.append(
                _row(
                    "provider.models",
                    "provider",
                    LEVEL_BLOCKED,
                    "GitHub Copilot sign-in",
                    "Not signed in. The model list shown in Settings is a "
                    "built-in fallback, not the models this account can use.",
                    "Sign in from NBI Settings under GitHub Copilot. Until "
                    "then every chat turn will fail with a 401.",
                )
            )
            return rows
        if signed_in is None:
            rows.append(
                _row(
                    "provider.models",
                    "provider",
                    LEVEL_WARN,
                    "GitHub Copilot sign-in",
                    "Sign-in state could not be determined.",
                    "If turns fail with a 401, sign in again from NBI "
                    "Settings under GitHub Copilot.",
                )
            )
            return rows

    rows.append(
        _row(
            "provider.models",
            "provider",
            LEVEL_OK,
            "Model list",
            f"{len(models)} model(s) available.",
        )
    )

    placeholder_id_provider = len(models) == 1 and effective_model != model_id
    if model_id and not placeholder_id_provider and model_id not in models:
        rows.append(
            _row(
                "provider.model_exists",
                "provider",
                LEVEL_WARN,
                "Selected model",
                f"'{model_id}' is not in the list this endpoint serves.",
                "Pick a model from the dropdown in NBI Settings. A pinned "
                "NBI_CHAT_MODEL_ID that the endpoint no longer serves will "
                "fail on every turn.",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Claude mode
# ---------------------------------------------------------------------------


def _claude_rows(nbi_config: Any, pool: concurrent.futures.ThreadPoolExecutor) -> list:
    rows = []
    settings = nbi_config.claude_settings or {}

    cli_path = resolve_claude_cli_path()
    if not cli_path:
        rows.append(
            _row(
                "claude.cli",
                "claude",
                LEVEL_BLOCKED,
                "Claude Code CLI",
                "The claude CLI was not found.",
                "Install the Claude Code CLI and make sure it is on the PATH "
                "of the process running JupyterLab, or set NBI_CLAUDE_CLI_PATH "
                "to its absolute path and restart.",
            )
        )
    else:
        # Resolving the path only proves a file exists. Claude mode hanging on
        # "Thinking..." is usually a CLI that resolves but cannot start.
        result = run_check(
            pool,
            "claude.cli",
            "claude",
            DEFAULT_TIMEOUT_S + SLOW_CHECK_MARGIN_S,
            lambda p=cli_path: run_versioned([p, "--version"]),
        )
        if result["status"] == "ok" and result["detail"].get("returncode") == 0:
            rows.append(
                _row(
                    "claude.cli",
                    "claude",
                    LEVEL_OK,
                    "Claude Code CLI",
                    f"{result['detail'].get('version') or 'present'}.",
                )
            )
        elif result["status"] == "timed_out":
            rows.append(
                _row(
                    "claude.cli",
                    "claude",
                    LEVEL_BLOCKED,
                    "Claude Code CLI",
                    "The CLI did not respond to --version within its budget.",
                    "The binary resolves but does not start. On a network "
                    "filesystem this is often the install tree being slow to "
                    "read; otherwise check it runs from a terminal.",
                )
            )
        else:
            rows.append(
                _row(
                    "claude.cli",
                    "claude",
                    LEVEL_BLOCKED,
                    "Claude Code CLI",
                    "The CLI resolved but --version failed.",
                    "Run 'claude --version' in a terminal as the Jupyter user "
                    "to see the underlying error.",
                )
            )

    source = _credential_source(settings.get("api_key"), "ANTHROPIC_API_KEY")
    if source:
        rows.append(
            _row(
                "claude.credentials",
                "claude",
                LEVEL_OK,
                "Anthropic credentials",
                f"API key present from {source}.",
            )
        )
    else:
        # Not blocked: the CLI may hold a subscription login of its own, which
        # NBI cannot see and must not call broken.
        rows.append(
            _row(
                "claude.credentials",
                "claude",
                LEVEL_WARN,
                "Anthropic credentials",
                "No API key is configured in NBI.",
                "This is fine if the Claude CLI is signed in with a "
                "subscription login. If turns fail with a 401, add a key in "
                "NBI Settings under Claude or set ANTHROPIC_API_KEY.",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# ACP mode
# ---------------------------------------------------------------------------


def _acp_rows(nbi_config: Any) -> list:
    from notebook_intelligence.acp_registry import (
        resolve_acp_agent,
        resolve_acp_agent_command,
    )

    rows = []
    settings = nbi_config.acp_settings or {}
    spec = resolve_acp_agent(settings.get("agent"))
    command = resolve_acp_agent_command(spec)

    rows.append(
        _row(
            "acp.agent",
            "acp",
            LEVEL_OK,
            "ACP agent",
            f"{spec.label}, launched as: {' '.join(command)}",
        )
    )

    # Whatever argv[0] is, it has to exist and be executable. Special-casing
    # the literal "npx" would let an NBI_ACP_AGENT_COMMAND override point at a
    # nonexistent binary and still report Ready.
    if command:
        binary = command[0]
        from shutil import which

        resolved = which(binary) if not os.path.sep in binary else (
            binary if os.access(binary, os.X_OK) else None
        )
        if resolved is None:
            hint = (
                "The default adapter is an npm package, so this usually means "
                "Node.js is not installed in the environment running "
                "JupyterLab. "
                if binary == "npx"
                else ""
            )
            rows.append(
                _row(
                    "acp.runtime",
                    "acp",
                    LEVEL_BLOCKED,
                    "Adapter runtime",
                    f"'{binary}' was not found or is not executable.",
                    hint + "Install it, or set NBI_ACP_AGENT_COMMAND to a "
                    "command that does exist.",
                )
            )
        else:
            rows.append(
                _row(
                    "acp.runtime",
                    "acp",
                    LEVEL_OK,
                    "Adapter runtime",
                    f"'{binary}' is available.",
                )
            )

    source = _credential_source(settings.get("api_key"), spec.api_key_env)
    if source:
        rows.append(
            _row(
                "acp.credentials",
                "acp",
                LEVEL_OK,
                "Agent credentials",
                f"API key present from {source}.",
            )
        )
    else:
        rows.append(
            _row(
                "acp.credentials",
                "acp",
                LEVEL_WARN,
                "Agent credentials",
                f"No API key is configured ({spec.api_key_env} is unset).",
                "This is fine if the agent is signed in with its own OAuth "
                "login. Otherwise set the key in NBI Settings under ACP.",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Live completion (opt-in, bills)
# ---------------------------------------------------------------------------


class _ProbeResponse(ChatResponse):
    """A ChatResponse that records what the endpoint did and keeps nothing.

    Passing a response object is what puts the provider on its streaming
    path, so this both proves streaming works and times the first chunk.
    Everything it receives is counted and dropped: no model output is
    retained, which keeps the readiness document free of generated text.
    """

    def __init__(self):
        self.chunks = 0
        self.first_chunk_at: Optional[float] = None
        self._t0 = None

    def start(self, t0: float) -> None:
        self._t0 = t0

    def stream(self, data: Any, finish: bool = False) -> None:
        # Signature matches api.ChatResponse.stream. A third-party provider
        # calling stream(chunk, finish=True) would otherwise raise TypeError
        # inside completions, which the caller would then misreport as the
        # endpoint being broken.
        import time

        self.chunks += 1
        if self.first_chunk_at is None and self._t0 is not None:
            self.first_chunk_at = (time.perf_counter() - self._t0) * 1000.0

    def finish(self) -> None:
        pass


_PROBE_TOOL_TEMPLATE = [
    {
        "type": "function",
        "function": {
            "name": "nbi_readiness_probe",
            "description": "Never called. Present only to test whether the "
            "endpoint accepts a tool schema.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _probe_tool() -> list:
    """A fresh copy per call.

    The list is handed to provider code and on to third-party SDKs. Only the
    openai-compatible provider deep-copies before normalizing; litellm and
    ollama pass it straight through, so a callee that rewrites it in place
    would corrupt the probe for the life of the process.
    """
    return copy.deepcopy(_PROBE_TOOL_TEMPLATE)


def _probe_messages() -> list:
    return [{"role": "user", "content": "Reply with the single word: ok"}]


def _live_completion(chat_model: Any) -> dict:
    """One minimal streaming completion, with a tool schema attached.

    Two things are only knowable from a real request. A proxy can accept the
    connection, return 200s, and still not stream (so the UI shows nothing
    until the turn ends), and it can strip or reject the tools field (so
    agent mode silently never calls a tool). Both look like a working
    endpoint to every cheaper check.
    """
    import time

    probe = _ProbeResponse()
    t0 = time.perf_counter()
    probe.start(t0)

    tool_schema_accepted: Optional[bool] = True
    first_error: Optional[str] = None
    try:
        chat_model.completions(
            _probe_messages(),
            tools=_probe_tool(),
            response=probe,
        )
    except Exception as e:
        # A failure here is not by itself evidence that the endpoint rejected
        # the tool schema: a 429, a cold-start 503, or a reset lands in the
        # same place. Retry WITHOUT tools, and only if that succeeds is the
        # tools field implicated. If the retry fails too, the endpoint is
        # simply down and the tool question is unanswered, not answered "no".
        first_error = type(e).__name__
        tool_schema_accepted = None
        probe = _ProbeResponse()
        t0 = time.perf_counter()
        probe.start(t0)
        chat_model.completions(_probe_messages(), response=probe)
        # The retry succeeded where the tools request failed, so the tools
        # field is the difference.
        tool_schema_accepted = False

    return {
        "streamed": probe.chunks > 0,
        "chunks": probe.chunks,
        "ttfb_ms": (
            round(probe.first_chunk_at, 1) if probe.first_chunk_at is not None else None
        ),
        "total_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        "tool_schema_accepted": tool_schema_accepted,
        "first_error": first_error,
    }


def _live_rows(ai_service_manager: Any, pool: concurrent.futures.ThreadPoolExecutor) -> list:
    chat_model = None
    try:
        chat_model = ai_service_manager.chat_model
    except Exception:
        chat_model = None

    if chat_model is None:
        return [
            _row(
                "live.completion",
                "live",
                LEVEL_SKIPPED,
                "Live completion",
                "No chat model is resolved, so there is nothing to call.",
            )
        ]

    result = run_check(
        pool, "live.completion", "live", LIVE_TIMEOUT_S, lambda: _live_completion(chat_model)
    )

    if result["status"] == "timed_out":
        return [
            _row(
                "live.completion",
                "live",
                LEVEL_BLOCKED,
                "Live completion",
                f"No response within {LIVE_TIMEOUT_S:.0f}s.",
                "The endpoint accepted the request but did not answer. Check "
                "the gateway and whether it is queueing behind a cold model.",
            )
        ]
    if result["status"] != "ok":
        return [
            _row(
                "live.completion",
                "live",
                LEVEL_BLOCKED,
                "Live completion",
                f"The request failed ({result['detail'].get('exception_class', 'error')}).",
                "Check the API key, the Base URL, and that the selected model "
                "id is one this endpoint serves. The server log carries the "
                "provider's own error text.",
            )
        ]

    detail = result["detail"]
    rows = []
    if detail["streamed"]:
        rows.append(
            _row(
                "live.completion",
                "live",
                LEVEL_OK,
                "Live completion",
                f"{detail['chunks']} chunk(s), first at "
                f"{detail['ttfb_ms'] if detail['ttfb_ms'] is not None else 'n/a'} ms, "
                f"{detail['total_ms']} ms total.",
            )
        )
    else:
        rows.append(
            _row(
                "live.completion",
                "live",
                LEVEL_WARN,
                "Live completion",
                "The request succeeded but nothing streamed.",
                "The endpoint answered in one piece. Replies will appear only "
                "when the turn finishes rather than as they are generated; a "
                "proxy that buffers responses is the usual cause.",
            )
        )

    if detail["tool_schema_accepted"] is None:
        rows.append(
            _row(
                "live.tools",
                "live",
                LEVEL_WARN,
                "Tool schema",
                f"Not determined: the request failed ({detail.get('first_error') or 'error'}) "
                "for a reason unrelated to tools.",
                "Re-run the endpoint test once the endpoint is answering "
                "again. A transient failure cannot tell us whether tool "
                "calling works.",
            )
        )
    elif detail["tool_schema_accepted"]:
        rows.append(
            _row(
                "live.tools",
                "live",
                LEVEL_OK,
                "Tool schema",
                "The endpoint accepted a request carrying a tool schema.",
            )
        )
    else:
        rows.append(
            _row(
                "live.tools",
                "live",
                LEVEL_BLOCKED,
                "Tool schema",
                "The endpoint rejected a request carrying a tool schema, but "
                "accepted the same request without one.",
                "Agent mode and every built-in tool need tool calling. The "
                "model may not support it, or a proxy in front of the endpoint "
                "may be stripping the tools field.",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _verdict(rows: list) -> tuple:
    """Overall verdict plus the one-line headline a banner shows."""
    blocked = [r for r in rows if r.get("level") == LEVEL_BLOCKED]
    warned = [r for r in rows if r.get("level") == LEVEL_WARN]

    if blocked:
        first = blocked[0]
        headline = f"Not ready: {first['title'].lower()}. {first['detail']}"
        if len(blocked) > 1:
            headline += f" ({len(blocked) - 1} more issue(s).)"
        return VERDICT_NOT_READY, headline
    if warned:
        first = warned[0]
        return (
            VERDICT_DEGRADED,
            f"Ready, with {len(warned)} warning(s). {first['title']}: {first['detail']}",
        )
    return VERDICT_READY, "Ready. Nothing needs configuring."


def run_readiness(
    nbi_config: Any, ai_service_manager: Any, include_live: bool = False
) -> dict:
    """Evaluate configuration readiness and return a scrubbed document.

    Blocking: callers on an event loop must run this via
    ``loop.run_in_executor``. Never raises for a check-level failure; a check
    that cannot run becomes a row saying so.
    """
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=_POOL_SIZE, thread_name_prefix="nbi-readiness"
    )
    rows: list = []
    try:
        mode_rows, mode = _mode_rows(ai_service_manager)
        rows += mode_rows

        # Each group is guarded separately. Readiness is what a stuck user
        # reaches for, so a config property that raises (a corrupt config
        # file, a failing override) has to become a row rather than a 500 on
        # the endpoint that exists to explain the breakage.
        try:
            if mode == "claude":
                rows += _claude_rows(nbi_config, pool)
            elif mode == "acp":
                rows += _acp_rows(nbi_config)
            else:
                rows += _provider_rows(nbi_config, ai_service_manager, pool)
        except Exception as e:
            rows.append(
                _row(
                    f"{mode}.unavailable",
                    mode if mode in ("claude", "acp") else "provider",
                    LEVEL_BLOCKED,
                    "Configuration",
                    f"Reading the configuration failed ({type(e).__name__}).",
                    "The stored configuration could not be read. Check "
                    "~/.jupyter/nbi/config.json for invalid JSON, and the "
                    "Jupyter server log for the underlying error.",
                )
            )

        if include_live:
            # The live probe calls whatever the native provider path would
            # call. In an agent mode the CLI owns the model call, so there is
            # nothing here NBI can exercise on the user's behalf.
            if mode in ("claude", "acp"):
                rows.append(
                    _row(
                        "live.completion",
                        "live",
                        LEVEL_SKIPPED,
                        "Live completion",
                        f"Not applicable: the agent owns the model call in {mode} mode.",
                    )
                )
            else:
                try:
                    rows += _live_rows(ai_service_manager, pool)
                except Exception as e:
                    rows.append(
                        _row(
                            "live.completion",
                            "live",
                            LEVEL_BLOCKED,
                            "Live completion",
                            f"The endpoint test could not run ({type(e).__name__}).",
                            "Check the Jupyter server log for the underlying "
                            "error.",
                        )
                    )
    finally:
        # Abandoned rather than joined, for the same reason the perf probe
        # abandons: a hung check has already been reported and waiting for its
        # thread would defeat the budget that reported it.
        pool.shutdown(wait=False, cancel_futures=True)

    verdict, headline = _verdict(rows)
    # Scrubbed on the way out: a launch command or a CLI banner carries an
    # absolute home directory and therefore the login name, and this document
    # is meant to be pasted into a support ticket.
    return scrub(
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "verdict": verdict,
            "headline": headline,
            "checks": rows,
        }
    )

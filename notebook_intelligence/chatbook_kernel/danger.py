# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Static danger scan for Chatbook-generated Python. UX gating, not a sandbox."""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Iterable, Optional

DANGER_LEVEL_CLEAN = "clean"
DANGER_LEVEL_RISKY = "risky"

_DANGEROUS_MODULES = {
    "os",
    "subprocess",
    "socket",
    "requests",
    "http",
    "urllib",
    "ctypes",
    "importlib",
    "webbrowser",
    "pickle",
}

_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}
_DESTRUCTIVE_ATTRS = {"unlink", "rmdir", "rmtree", "move", "remove", "rename"}
_WRITE_ATTRS = {"to_csv", "to_parquet", "to_sql"}
_SHELL_ATTRS = {"system", "getoutput", "run_line_magic", "run_cell_magic", "run_cell"}
_WRITE_OPEN_MODES = set("wax")
_MAGIC_NAMES = {
    "run",
    "env",
    "set_env",
    "pip",
    "conda",
    "bash",
    "sh",
    "script",
    "sx",
    "system",
}
_MAGIC_LINE_RE = re.compile(r"^%%?(?P<name>[A-Za-z_]\w*)")


def scan_generated_python(source: str) -> dict[str, Any]:
    """Return ``{level, reasons}``. Parse failure is risky (fail closed)."""
    reasons: list[str] = []
    text = source or ""
    reasons.extend(_scan_magics(text))
    try:
        tree = ast.parse(text)
    except SyntaxError:
        reasons.append("Generated Python could not be parsed")
        return _result(reasons)
    reasons.extend(_scan_ast(tree))
    return _result(reasons)


def scan_generated_code(source: str, language: str = "python") -> dict[str, Any]:
    """Scan generated source. Non-Python languages fail closed as risky."""
    lang = (language or "python").strip().lower()
    if lang in {"", "python", "py"}:
        return scan_generated_python(source)
    label = (language or "code").strip() or "code"
    return {
        "level": DANGER_LEVEL_RISKY,
        "reasons": [f"No static scanner for {label} code"],
    }


def merge_danger_scans(*scans: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Combine scans. Any risky result wins; reasons are de-duplicated."""
    reasons: list[str] = []
    seen: set[str] = set()
    for scan in scans:
        if not scan:
            continue
        for reason in scan.get("reasons") or []:
            text = str(reason).strip()
            if text and text not in seen:
                seen.add(text)
                reasons.append(text)
    return _result(reasons)


def parse_llm_danger_response(text: str) -> dict[str, Any]:
    """Parse a classifier JSON object. Invalid output is risky (fail closed)."""
    payload = _extract_json_object(text)
    if not isinstance(payload, dict):
        return {
            "level": DANGER_LEVEL_RISKY,
            "reasons": ["Danger classifier returned unreadable output"],
        }
    risky = bool(payload.get("risky"))
    reasons = [
        str(item).strip()
        for item in (payload.get("reasons") or [])
        if str(item).strip()
    ]
    if risky and not reasons:
        reasons = ["Classifier flagged the generated code as risky"]
    if risky:
        return {"level": DANGER_LEVEL_RISKY, "reasons": reasons}
    return {"level": DANGER_LEVEL_CLEAN, "reasons": []}


def _result(reasons: Iterable[str]) -> dict[str, Any]:
    items = [str(reason).strip() for reason in reasons if str(reason).strip()]
    return {
        "level": DANGER_LEVEL_RISKY if items else DANGER_LEVEL_CLEAN,
        "reasons": items,
    }


def _scan_magics(source: str) -> list[str]:
    reasons: list[str] = []
    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("!"):
            reasons.append("Shell command (!)")
            continue
        match = _MAGIC_LINE_RE.match(line)
        if not match:
            continue
        name = match.group("name").lower()
        if name in _MAGIC_NAMES:
            reasons.append(f"IPython magic %{name}")
    return reasons


def _scan_ast(tree: ast.AST) -> list[str]:
    reasons: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = _root_module(alias.name)
                if module in _DANGEROUS_MODULES:
                    reasons.append(f"Import of {module}")
        elif isinstance(node, ast.ImportFrom):
            module = _root_module(node.module or "")
            if module in _DANGEROUS_MODULES:
                reasons.append(f"Import of {module}")
        elif isinstance(node, ast.Call):
            reasons.extend(_call_reasons(node))
    return reasons


def _call_reasons(node: ast.Call) -> list[str]:
    reasons: list[str] = []
    name = _call_name(node.func)
    attr = _attr_name(node.func)
    if name in _DANGEROUS_CALLS:
        reasons.append(f"Call to {name}")
    if attr in _DESTRUCTIVE_ATTRS:
        reasons.append(f"Filesystem call .{attr}()")
    if attr in _WRITE_ATTRS:
        reasons.append(f"Data write .{attr}()")
    if attr in _SHELL_ATTRS:
        reasons.append(f"Shell/IPython call .{attr}()")
    if name == "open" or attr == "open":
        mode = _open_mode(node)
        if mode and mode[:1] in _WRITE_OPEN_MODES:
            reasons.append("File opened for write")
    return reasons


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _attr_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _open_mode(node: ast.Call) -> str:
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        value = node.args[1].value
        if isinstance(value, str):
            return value
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            if isinstance(value, str):
                return value
    return ""


def _root_module(name: str) -> str:
    return (name or "").split(".", 1)[0]


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None

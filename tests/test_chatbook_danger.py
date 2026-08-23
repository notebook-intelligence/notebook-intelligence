# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from notebook_intelligence.chatbook_kernel.danger import (
    merge_danger_scans,
    parse_llm_danger_response,
    scan_generated_python,
)
from notebook_intelligence.chatbook_kernel.execution import (
    clamp_execution_mode,
    parse_execution_mode,
    should_execute_generated,
)
from notebook_intelligence.chatbook_generate import classify_generated_python_danger


def test_scan_flags_subprocess_and_shell():
    scan = scan_generated_python("import subprocess\nsubprocess.run(['ls'])\n")
    assert scan["level"] == "risky"
    assert any("subprocess" in reason for reason in scan["reasons"])

    magic = scan_generated_python("%pip install pandas\n")
    assert magic["level"] == "risky"
    assert any("pip" in reason for reason in magic["reasons"])

    bang = scan_generated_python("!rm -rf tmp\n")
    assert bang["level"] == "risky"


def test_scan_flags_writes_and_eval():
    write = scan_generated_python('open("out.csv", "w").write("a")\n')
    assert write["level"] == "risky"
    assert any("write" in reason.lower() for reason in write["reasons"])

    frame = scan_generated_python("df.to_csv('out.csv')\n")
    assert frame["level"] == "risky"

    dyn = scan_generated_python("eval('1')\n")
    assert dyn["level"] == "risky"


def test_scan_clean_analysis_code():
    scan = scan_generated_python("total = sum(values)\ntotal\n")
    assert scan == {"level": "clean", "reasons": []}


def test_scan_parse_failure_is_risky():
    scan = scan_generated_python("def (\n")
    assert scan["level"] == "risky"
    assert scan["reasons"]


def test_merge_and_llm_parse():
    static = {"level": "risky", "reasons": ["Import of os"]}
    llm_clean = parse_llm_danger_response('{"risky": false, "reasons": []}')
    assert llm_clean["level"] == "clean"
    merged = merge_danger_scans(static, llm_clean)
    assert merged["level"] == "risky"
    assert "Import of os" in merged["reasons"]

    llm_bad = parse_llm_danger_response("not json")
    assert llm_bad["level"] == "risky"

    llm_raise = parse_llm_danger_response(
        'Sure.\n{"risky": true, "reasons": ["network"]}\n'
    )
    assert llm_raise["level"] == "risky"
    assert llm_raise["reasons"] == ["network"]


def test_execution_mode_clamp_and_run_policy():
    assert parse_execution_mode("nope") == "always-confirm"
    assert clamp_execution_mode("auto-run", "always-confirm") == "always-confirm"
    assert clamp_execution_mode("generate-only", "auto-run") == "generate-only"
    assert should_execute_generated("auto-run", "risky") is True
    assert should_execute_generated("confirm-if-risky", "clean") is True
    assert should_execute_generated("confirm-if-risky", "risky") is False
    assert should_execute_generated("always-confirm", "clean") is False
    assert should_execute_generated("generate-only", "clean") is False


def test_llm_classifier_uses_json_only():
    class Model:
        def completions(self, messages, tools=None, response=None, cancel_token=None, options=None):
            assert "untrusted" not in messages[1]["content"]
            response.stream({
                "choices": [{"delta": {"content": '{"risky": true, "reasons": ["shell"]}'}}]
            })
            response.finish()

    class Manager:
        chat_model = Model()

    scan = classify_generated_python_danger(Manager(), "print(1)")
    assert scan["level"] == "risky"
    assert scan["reasons"] == ["shell"]

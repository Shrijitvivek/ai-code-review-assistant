import json
from pathlib import Path
from types import SimpleNamespace

from src import main as cli


def _request(**updates):
    return {
        "review_id": "R001",
        "commit_id": "demo-a1",
        "files": ["cart.py"],
        "message": "Review the requested file.",
        **updates,
    }


def _pipeline_result(
    *, test_status="passed", missing_files=None, include_references=True,
    findings="valid",
):
    static_findings = [
        {
            "file": "cart.py",
            "line": 5,
            "rule": "example-rule",
            "message": "Static-analysis observation.",
        }
    ]
    if include_references:
        static_findings[0]["tool_reference"] = "static_analysis:example-rule:cart.py:1"
    if findings != "valid":
        static_findings = findings
    return {
        "review_id": "R001",
        "commit_id": "demo-a1",
        "files": ["cart.py"],
        "missing_files": missing_files or [],
        "checks": {
            "inventory": {"status": "completed", "files": ["cart.py"]},
            "static_analysis": {
                "status": "completed",
                "findings": static_findings,
            },
            "test_results": {
                "status": test_status,
                "return_code": 1 if test_status == "failed" else 0,
                "stdout": (
                    "4 failed in 0.01s"
                    if test_status == "failed"
                    else "4 passed in 0.01s"
                    if test_status == "passed"
                    else ""
                ),
            },
        },
        "release_decision": "PASS",
    }


def _autogen_result(
    *,
    test_status="passed",
    analyst_status=None,
    test_tool_status=None,
    include_security=True,
    include_test_analyst=True,
    valid=True,
):
    source_content = (
        Path(__file__).resolve().parents[1] / "sample_repo" / "cart.py"
    ).read_text(encoding="utf-8")
    agent_finding = {
        "file": "cart.py",
        "line": 5,
        "category": "correctness",
        "severity": "LOW",
        "description": "AutoGen observation.",
        "evidence": [
            "Source line: return price + quantity"
        ],
        "status": "confirmed",
        "fix": "Review the cited behavior.",
        "tool_reference": "read_repository_file:cart.py",
    }
    report = {
        "findings": [agent_finding],
        "reviewer_summary": "Reviewed requested code and reconciled tool-supported findings.",
        "test_status": test_status,
    }
    content = (
        json.dumps(report) + "\nREVIEW_COMPLETE"
        if valid
        else "not a valid synthesized report"
    )
    messages = [
        SimpleNamespace(
            source="CorrectnessReviewer",
            content=json.dumps({
                "findings": [agent_finding],
                "reviewer_summary": (
                    "Reviewed correctness and reported the observed behavior."
                ),
            }),
        )
    ]
    if include_security:
        messages.append(
            SimpleNamespace(
                source="SecurityReviewer",
                content=json.dumps({
                    "findings": [],
                    "reviewer_summary": (
                        "Reviewed input handling and found no security issues."
                    ),
                }),
            )
        )
    if include_test_analyst:
        messages.append(
            SimpleNamespace(
                source="TestAnalyst",
                content=(
                    json.dumps({
                        "test_status": analyst_status or test_status,
                        "test_evidence": "Fixed test target returned 4 passing tests.",
                    })
                ),
            )
        )
    messages.append(SimpleNamespace(
        source="run_repository_tests",
        content=json.dumps({
            "status": test_tool_status or test_status,
            "stdout": "4 passed",
        }),
    ))
    for reviewer in ("CorrectnessReviewer", "SecurityReviewer"):
        if reviewer == "SecurityReviewer" and not include_security:
            continue
        messages.append(SimpleNamespace(
            source=reviewer,
            content=[
                SimpleNamespace(
                    name="read_repository_file",
                    call_id=f"{reviewer}-read-cart",
                    content=json.dumps({
                        "status": "ok",
                        "path": "cart.py",
                        "content": source_content,
                        "tool_reference": "read_repository_file:cart.py",
                    }),
                )
            ],
        ))
    messages.append(SimpleNamespace(source="ReportSynthesizer", content=content))
    return SimpleNamespace(messages=messages)


def _write_requests(tmp_path, *requests):
    path = tmp_path / "requests.json"
    path.write_text(json.dumps(list(requests)), encoding="utf-8")
    return path


def _configure_cli(monkeypatch, *, pipeline=None, team=None):
    monkeypatch.setattr(cli, "prepare_review", pipeline or (lambda request: _pipeline_result()))

    async def run_team(request):
        result = (team or (lambda _: _autogen_result()))(request)
        if hasattr(result, "__await__"):
            return await result
        return result

    monkeypatch.setattr(cli, "run_review_team", run_team)
    monkeypatch.setattr(
        cli,
        "load_review_policy",
        lambda: {
            "required_stages": ["source_scan", "correctness", "security", "testing"],
            "block_if_test_fails": True,
            "block_if_confirmed_critical": True,
            "unknown_check_policy": "NEEDS_HUMAN_REVIEW",
            "severity_levels": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        },
    )


def test_cli_combines_findings_and_both_test_results(monkeypatch, tmp_path, capsys):
    _configure_cli(monkeypatch)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert len(report["findings"]) == 2
    assert {finding["source"] for finding in report["findings"]} == {
        "langchain_static_analysis",
        "autogen",
    }
    assert report["evidence_validation"]["verified"] is True
    assert report["findings"][1]["tool_reference"] == "read_repository_file:cart.py"
    assert report["checks"]["langchain_tests"]["status"] == "passed"
    assert report["checks"]["autogen_test_status"] == "passed"
    assert report["checks"]["stages"]["correctness"]["status"] == "completed"
    assert report["checks"]["stages"]["security"]["status"] == "completed"
    assert report["release_decision"] == "PASS"
    assert set(report["validated_report"]) == {
        "review_id",
        "commit_id",
        "scan_status",
        "tests_passed",
        "tests_failed",
        "findings",
        "release_decision",
        "blockers",
        "missing_checks",
        "reviewer_summary",
    }
    assert report["validated_report"]["release_decision"] == "PASS"
    assert report["validated_report"]["tests_passed"] == 4
    assert report["validated_report"]["tests_failed"] == 0
    assert set(report["autogen"]["reviewer_outputs"]) == {
        "CorrectnessReviewer",
        "SecurityReviewer",
        "TestAnalyst",
        "ReportSynthesizer",
    }


def test_cli_does_not_complete_missing_reviewer_stage(monkeypatch, tmp_path, capsys):
    _configure_cli(
        monkeypatch,
        team=lambda request: _autogen_result(include_security=False),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["checks"]["stages"]["correctness"]["status"] == "completed"
    assert report["checks"]["stages"]["security"]["status"] == "unavailable"
    assert report["checks"]["stages"]["testing"]["status"] == "completed"
    assert report["autogen"]["reviewer_outputs"]["ReportSynthesizer"]["status"] == "unavailable"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_cli_handles_api_configuration_failure(monkeypatch, tmp_path, capsys):
    async def unavailable_team(request):
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    _configure_cli(monkeypatch, team=unavailable_team)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 1
    report = json.loads(capsys.readouterr().out)[0]

    assert report["errors"] == [
        {
            "source": "autogen",
            "message": "RuntimeError: OPENAI_API_KEY is not configured.",
        }
    ]
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_cli_handles_invalid_synthesized_report(monkeypatch, tmp_path, capsys):
    _configure_cli(
        monkeypatch,
        team=lambda request: _autogen_result(valid=False),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 1
    report = json.loads(capsys.readouterr().out)[0]

    assert report["autogen"]["report"] is None
    assert report["errors"][0]["source"] == "autogen"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_cli_keeps_unavailable_requested_files_from_passing(
    monkeypatch, tmp_path, capsys
):
    _configure_cli(
        monkeypatch,
        pipeline=lambda request: _pipeline_result(missing_files=["missing.py"]),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["checks"]["missing_files"] == ["missing.py"]
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_missing_requested_source_does_not_run_or_report_autogen_results(
    monkeypatch, tmp_path, capsys
):
    team_calls = []
    _configure_cli(
        monkeypatch,
        pipeline=lambda request: _pipeline_result(
            missing_files=["missing.py"], findings=[]
        ),
        team=lambda request: team_calls.append(request),
    )
    path = _write_requests(tmp_path, _request(files=["missing.py"]))

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert team_calls == []
    assert report["findings"] == []
    assert report["autogen"]["report"] is None
    assert report["checks"]["autogen_test_status"] == "unavailable"
    assert report["checks"]["stages"]["testing"]["status"] == "unavailable"
    assert report["checks"]["missing_files"] == ["missing.py"]
    assert all(
        output["status"] == "unavailable"
        for output in report["autogen"]["reviewer_outputs"].values()
    )
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_missing_test_analyst_output_keeps_testing_unavailable(
    monkeypatch, tmp_path, capsys
):
    _configure_cli(
        monkeypatch,
        team=lambda request: _autogen_result(include_test_analyst=False),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["autogen"]["reviewer_outputs"]["TestAnalyst"]["status"] == "unavailable"
    assert report["checks"]["stages"]["testing"]["status"] == "completed"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_unavailable_tests_cannot_pass(monkeypatch, tmp_path, capsys):
    _configure_cli(
        monkeypatch,
        pipeline=lambda request: _pipeline_result(test_status="unavailable"),
        team=lambda request: _autogen_result(test_status="unavailable"),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["checks"]["stages"]["testing"]["status"] == "unavailable"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_findings_without_genuine_references_cannot_pass(
    monkeypatch, tmp_path, capsys
):
    _configure_cli(
        monkeypatch,
        pipeline=lambda request: _pipeline_result(include_references=False),
        team=lambda request: _autogen_result(),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["evidence_validation"]["verified"] is True
    assert all(
        finding.get("source") != "langchain_static_analysis"
        for finding in report["findings"]
        if isinstance(finding, dict)
    )
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"
    assert all(
        "supporting_reference" not in finding
        for finding in report["findings"]
        if isinstance(finding, dict)
    )


def test_llm_reference_is_removed_when_no_read_tool_execution_exists(
    monkeypatch, tmp_path, capsys
):
    def team(request):
        result = _autogen_result()
        result.messages = [
            message for message in result.messages
            if not (
                message.source == "CorrectnessReviewer"
                and isinstance(message.content, list)
            )
        ]
        return result

    _configure_cli(monkeypatch, team=team)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    parsed = report["autogen"]["reviewer_outputs"]["CorrectnessReviewer"]["parsed"]
    assert parsed["findings"][0]["tool_reference"] is None
    assert report["autogen"]["reviewer_outputs"]["CorrectnessReviewer"]["status"] == "unavailable"
    assert report["evidence_validation"]["verified"] is False
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_pipeline_evidence_from_another_commit_is_not_trusted(
    monkeypatch, tmp_path, capsys
):
    mismatched_pipeline = _pipeline_result()
    mismatched_pipeline["commit_id"] = "different-commit"
    _configure_cli(monkeypatch, pipeline=lambda request: mismatched_pipeline)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 1
    report = json.loads(capsys.readouterr().out)[0]

    assert report["checks"]["stages"]["source_scan"]["status"] == "unavailable"
    assert report["checks"]["langchain_tests"]["status"] == "unavailable"
    assert report["errors"][-1]["source"] == "langchain"
    assert report["evidence_validation"]["verified"] is False
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_invalid_reviewer_output_does_not_complete_its_stage(
    monkeypatch, tmp_path, capsys
):
    def team(request):
        result = _autogen_result()
        result.messages[0].content = '{"findings": []}'
        return result

    _configure_cli(monkeypatch, team=team)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert (
        report["autogen"]["reviewer_outputs"]["CorrectnessReviewer"]["status"]
        == "unavailable"
    )
    assert report["checks"]["stages"]["correctness"]["status"] == "unavailable"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_reviewer_line_is_reconciled_to_the_quoted_source_line():
    result = _autogen_result()
    result.messages[0].content = json.dumps({
        "findings": [{
            **json.loads(result.messages[0].content)["findings"][0],
            "line": 4,
        }],
        "reviewer_summary": "Reviewed correctness and reported the observed behavior.",
    })

    outputs = cli._reviewer_outputs(result, requested_files=["cart.py"])

    assert outputs["CorrectnessReviewer"]["status"] == "completed"
    assert outputs["CorrectnessReviewer"]["parsed"]["findings"][0]["line"] == 5
    assert outputs["CorrectnessReviewer"]["line_corrections"] == [
        {"reported_line": 4, "verified_line": 5}
    ]


def test_synthesized_line_is_reconciled_to_the_quoted_source_line(
    monkeypatch, tmp_path, capsys
):
    def team(request):
        result = _autogen_result()
        synthesized = json.loads(result.messages[-1].content.removesuffix(
            "\nREVIEW_COMPLETE"
        ))
        synthesized["findings"][0]["line"] = 4
        result.messages[-1].content = (
            json.dumps(synthesized) + "\nREVIEW_COMPLETE"
        )
        return result

    _configure_cli(monkeypatch, team=team)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["autogen"]["reviewer_outputs"]["ReportSynthesizer"]["status"] == "completed"
    assert report["autogen"]["reviewer_outputs"]["ReportSynthesizer"]["line_corrections"] == [
        {"reported_line": 4, "verified_line": 5}
    ]


def test_security_reviewer_cannot_report_plain_business_logic():
    result = _autogen_result()
    result.messages[1].content = json.dumps({
        "findings": [{
            **json.loads(result.messages[0].content)["findings"][0],
            "category": "security",
            "description": "Arithmetic could produce an incorrect subtotal.",
        }],
        "reviewer_summary": "Reviewed requested code for security issues and found a concern.",
    })

    outputs = cli._reviewer_outputs(result, requested_files=["cart.py"])

    assert outputs["SecurityReviewer"]["status"] == "unavailable"
    assert "security classification" in outputs["SecurityReviewer"]["error"]


def test_failed_tests_block_under_configured_policy(monkeypatch, tmp_path, capsys):
    _configure_cli(
        monkeypatch,
        pipeline=lambda request: _pipeline_result(test_status="failed"),
        team=lambda request: _autogen_result(test_status="failed"),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["checks"]["stages"]["testing"]["status"] == "completed"
    assert report["release_decision"] == "BLOCK"


def test_test_analyst_requires_explicit_status(monkeypatch, tmp_path, capsys):
    def team(request):
        result = _autogen_result()
        result.messages[2].content = "The tests seem to have passed."
        return result

    _configure_cli(monkeypatch, team=team)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["autogen"]["reviewer_outputs"]["TestAnalyst"]["status"] == "unavailable"
    assert report["checks"]["stages"]["testing"]["status"] == "completed"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_test_analyst_status_must_match_test_runner(monkeypatch, tmp_path, capsys):
    _configure_cli(
        monkeypatch,
        team=lambda request: _autogen_result(
            test_status="passed",
            analyst_status="passed",
            test_tool_status="failed",
        ),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["autogen"]["reviewer_outputs"]["TestAnalyst"]["status"] == "unavailable"
    assert report["checks"]["stages"]["testing"]["status"] == "completed"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_test_analyst_cannot_complete_without_test_runner_output(
    monkeypatch, tmp_path, capsys
):
    def team(request):
        result = _autogen_result()
        result.messages = [
            message for message in result.messages
            if message.source != "run_repository_tests"
        ]
        return result

    _configure_cli(monkeypatch, team=team)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["autogen"]["reviewer_outputs"]["TestAnalyst"]["status"] == "unavailable"
    assert report["checks"]["stages"]["testing"]["status"] == "completed"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_generic_reviewer_output_is_not_meaningful(monkeypatch, tmp_path, capsys):
    def team(request):
        result = _autogen_result()
        result.messages[0].content = '{"findings": []}'
        return result

    _configure_cli(monkeypatch, team=team)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert (
        report["autogen"]["reviewer_outputs"]["CorrectnessReviewer"]["status"]
        == "unavailable"
    )
    assert report["checks"]["stages"]["correctness"]["status"] == "unavailable"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_synthesizer_test_status_must_agree_with_test_analyst(
    monkeypatch, tmp_path, capsys
):
    _configure_cli(
        monkeypatch,
        team=lambda request: _autogen_result(
            test_status="failed", analyst_status="passed"
        ),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["checks"]["stages"]["testing"]["status"] == "completed"
    assert report["checks"]["stages"]["report_synthesis"]["status"] == "unavailable"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_synthesized_findings_must_agree_with_reviewer_findings(
    monkeypatch, tmp_path, capsys
):
    def team(request):
        result = _autogen_result()
        result.messages[-1].content = json.dumps({
            "findings": [],
            "reviewer_summary": "Review completed without actionable findings.",
            "test_status": "passed",
        }) + "\nREVIEW_COMPLETE"
        return result

    _configure_cli(monkeypatch, team=team)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["checks"]["stages"]["correctness"]["status"] == "completed"
    assert report["checks"]["stages"]["security"]["status"] == "completed"
    assert report["checks"]["stages"]["report_synthesis"]["status"] == "unavailable"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_duplicate_reviewer_findings_are_deduplicated_across_categories(
    monkeypatch, tmp_path, capsys
):
    duplicate = {
        "file": "cart.py",
        "line": 2,
        "category": "security",
        "severity": "LOW",
        "description": "AutoGen observation.",
        "evidence": [
            "Second reviewer evidence: return Path(path).read_text()"
        ],
        "status": "confirmed",
        "fix": "Review the cited behavior.",
        "tool_reference": "read_repository_file:cart.py",
    }

    def team(request):
        result = _autogen_result()
        result.messages[0].content = json.dumps({
            "findings": [{
                **json.loads(result.messages[0].content)["findings"][0],
                "line": 2,
                "evidence": ["Source line: return Path(path).read_text()"],
            }],
            "reviewer_summary": "Reviewed correctness and reported the observed behavior.",
        })
        result.messages[1].content = json.dumps({
            "findings": [duplicate],
            "reviewer_summary": "Reviewed requested files for credible exploitable security issues.",
        })
        result.messages[-1].content = json.dumps({
            "findings": [{
                **json.loads(result.messages[0].content)["findings"][0],
                "evidence": [
                    "Source line: return Path(path).read_text()",
                    "Second reviewer evidence: return Path(path).read_text()",
                ],
            }],
            "reviewer_summary": "Reviewers reconciled their duplicate source findings.",
            "test_status": "passed",
        }) + "\nREVIEW_COMPLETE"
        return result

    _configure_cli(monkeypatch, team=team)
    monkeypatch.setattr(
        cli,
        "_read_tool_sources",
        lambda messages, reviewer: {
            "cart.py": "def read_text(path):\n    return Path(path).read_text()\n"
        },
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    findings = [
        finding for finding in report["findings"]
        if finding.get("source") == "autogen"
    ]
    assert len(findings) == 1
    assert findings[0]["category"] == "correctness"
    assert findings[0]["reviewers"] == [
        "CorrectnessReviewer",
        "SecurityReviewer",
    ]


def test_synthesizer_cannot_add_findings_not_reported_by_reviewers(
    monkeypatch, tmp_path, capsys
):
    def team(request):
        result = _autogen_result()
        extra_finding = dict(
            json.loads(result.messages[0].content)["findings"][0]
        )
        extra_finding["description"] = "Unreported synthesized finding."
        result.messages[-1].content = json.dumps({
            "findings": [extra_finding],
            "reviewer_summary": "Reviewers reconciled their structured findings.",
            "test_status": "passed",
        }) + "\nREVIEW_COMPLETE"
        return result

    _configure_cli(monkeypatch, team=team)
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["checks"]["stages"]["correctness"]["status"] == "completed"
    assert report["checks"]["stages"]["security"]["status"] == "completed"
    assert report["checks"]["stages"]["report_synthesis"]["status"] == "unavailable"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_malformed_static_analysis_findings_are_not_dropped(
    monkeypatch, tmp_path, capsys
):
    _configure_cli(
        monkeypatch,
        pipeline=lambda request: _pipeline_result(findings={"unexpected": "shape"}),
    )
    path = _write_requests(tmp_path, _request())

    assert cli.main([str(path)]) == 0
    report = json.loads(capsys.readouterr().out)[0]

    assert report["findings"][0] == {"unexpected": "shape"}
    assert report["checks"]["stages"]["correctness"]["status"] == "unavailable"
    assert report["release_decision"] == "NEEDS_HUMAN_REVIEW"


def test_cli_reports_missing_request_file_as_json(capsys, tmp_path):
    assert cli.main([str(tmp_path / "missing.json")]) == 2
    output = json.loads(capsys.readouterr().out)

    assert output["reports"] == []
    assert "FileNotFoundError" in output["error"]


def test_cli_can_select_one_request_from_a_batch(monkeypatch, tmp_path, capsys):
    def pipeline(request):
        return {
            **_pipeline_result(),
            "review_id": request["review_id"],
            "commit_id": request["commit_id"],
        }

    _configure_cli(monkeypatch, pipeline=pipeline)
    path = _write_requests(
        tmp_path,
        _request(review_id="R001"),
        _request(review_id="R002", commit_id="demo-b2"),
    )

    assert cli.main([str(path), "--review-id", "R002"]) == 0
    reports = json.loads(capsys.readouterr().out)

    assert [report["review_id"] for report in reports] == ["R002"]

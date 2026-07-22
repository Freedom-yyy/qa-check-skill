#!/usr/bin/env python3
"""Validate qa-check report artifacts without judging business correctness."""

import argparse
import json
import re
import sys
from pathlib import Path


ALLOWED_RULE_STATUS = {"Executed", "NotApplicable", "BlockedByMissingMaterials", "Skipped"}
ALLOWED_RULE_RESULT = {"IssueFound", "NoIssueFound", "CannotDetermine"}
ALLOWED_SEVERITY = {"High", "Medium", "Low"}
ALLOWED_EVIDENCE_LEVEL = {
    "L1-CodeEvidence", "L2-RuntimeVerified", "L3-SuspectedRisk", "L4-TestSuggestion"
}
ALLOWED_STATUS = {
    "DraftRisk", "PendingVerification", "Verified", "NotReproduced", "Fixed",
    "StillOpen", "PartiallyFixed", "CannotVerify", "NeedConfirm", "KnownIssue", "WontFix"
}
ALLOWED_REPORT_STAGE = {"draft", "final", "recheck"}
ALLOWED_REPRO_TYPE = {"Theoretical", "Verified", "CannotVerify"}
ALLOWED_REPRO_STATUS = {"未实测", "已实测复现", "尝试复现但未复现", "无法实测"}
ALLOWED_EVIDENCE_TYPE = {"Code", "Video", "Screenshot", "Log", "CommandOutput", "APIResponse", "None"}
ALLOWED_ISSUE_ORIGIN = {
    "NewInThisRound", "IntroducedByThisChange", "BypassChange",
    "ExistingRiskExposed", "InsufficientMaterial"
}
ID_PATTERN = re.compile(r"^QC-\d{3,}$")
ISSUE_ID_PATTERN = re.compile(r"\bQC-\d{3,}\b")
REMOTE_PATTERN = re.compile(
    r"(?:\b(?:src|href)\s*=\s*[\"'](?:https?:)?//|"
    r"url\(\s*[\"']?(?:https?:)?//)"
)


def fail(errors, message):
    errors.append(message)


def read_utf8(path, errors):
    try:
        data = path.read_bytes()
        if not data:
            fail(errors, f"empty file: {path}")
            return ""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            fail(errors, f"not valid UTF-8: {path} ({exc})")
            return ""
    except OSError as exc:
        fail(errors, f"cannot read {path}: {exc}")
        return ""


def resolve_stage_dir(report_dir, stage):
    """Accept either a stage directory or the run directory containing it."""
    if report_dir.name == stage:
        return report_dir
    stage_dir = report_dir / stage
    return stage_dir if stage_dir.is_dir() else report_dir


def find_artifact(report_dir, suffix):
    candidates = sorted(report_dir.glob(f"*{suffix}"))
    if not candidates:
        return None
    return candidates[0]


def load_registered_rule_ids():
    registry = Path(__file__).resolve().parents[1] / "references" / "rule-registry.md"
    if not registry.is_file():
        return set()
    return {
        match.group(1)
        for line in registry.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^##\s+([A-Za-z0-9][A-Za-z0-9.-]*)\s*$", line))
    }


def validate_required_field(value, field, prefix, errors):
    if field not in value:
        fail(errors, f"{prefix} is missing required field: {field}")
        return False
    return True


def validate_json(data, json_path, stage, report_dir, errors, previous_json=None):
    if not isinstance(data, dict):
        fail(errors, "JSON root must be an object")
        return
    for field in ("requirementName", "requirementDir", "runDir", "branch", "baseBranch", "commit", "generatedAt", "mode", "diffFiles", "selectedRuleIds", "ruleExecutionSummary"):
        validate_required_field(data, field, "JSON", errors)
    if data.get("reportStage") != stage:
        fail(errors, f"JSON reportStage must be {stage!r}")
    if not isinstance(data.get("issues"), list):
        fail(errors, "JSON issues must be an array")
        return
    if not isinstance(data.get("ruleExecutionSummary"), list):
        fail(errors, "JSON ruleExecutionSummary must be an array")
    selected_rule_ids = data.get("selectedRuleIds")
    if not isinstance(selected_rule_ids, list):
        fail(errors, "JSON selectedRuleIds must be an array")
        selected_rule_ids = []
    if len(selected_rule_ids) != len(set(selected_rule_ids)):
        fail(errors, "JSON selectedRuleIds must not contain duplicates")

    ids = set()
    registered_rule_ids = load_registered_rule_ids()
    all_issue_ids = set()
    for index, issue in enumerate(data["issues"], 1):
        prefix = f"issue {index}"
        if not isinstance(issue, dict):
            fail(errors, f"{prefix} must be an object")
            continue
        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not ID_PATTERN.fullmatch(issue_id):
            fail(errors, f"{prefix} has invalid id: {issue_id!r}")
        elif issue_id in ids:
            fail(errors, f"duplicate issue id: {issue_id}")
        else:
            ids.add(issue_id)
            all_issue_ids.add(issue_id)
        for field in (
            "title", "module", "severity", "evidenceLevel", "status", "reportStage",
            "firstFoundBranch", "firstFoundCommit", "currentBranch", "currentCommit",
            "sourceFiles", "reason", "impact", "theoreticalReproSteps", "verifiedReproSteps",
            "suggestedTestScenarios", "requiredTestMaterials", "reproType", "reproStatus",
            "verificationStatus", "evidenceType", "evidenceFiles", "cannotVerifyReason",
            "issueOrigin", "ruleIds", "ruleDomains", "notes"
        ):
            validate_required_field(issue, field, prefix, errors)
        for field in ("ruleIds", "ruleDomains", "evidenceFiles", "sourceFiles", "theoreticalReproSteps", "verifiedReproSteps", "suggestedTestScenarios", "requiredTestMaterials"):
            if not isinstance(issue.get(field), list):
                fail(errors, f"{prefix}.{field} must be an array")
        if isinstance(issue.get("ruleIds"), list) and not issue["ruleIds"]:
            fail(errors, f"{prefix}.ruleIds must contain at least one registered rule id")
        if issue.get("severity") not in ALLOWED_SEVERITY:
            fail(errors, f"{prefix}.severity has invalid value")
        if issue.get("evidenceLevel") not in ALLOWED_EVIDENCE_LEVEL:
            fail(errors, f"{prefix}.evidenceLevel has invalid value")
        if issue.get("status") not in ALLOWED_STATUS:
            fail(errors, f"{prefix}.status has invalid value")
        if issue.get("reportStage") != stage:
            fail(errors, f"{prefix}.reportStage must be {stage!r}")
        if issue.get("reproType") not in ALLOWED_REPRO_TYPE:
            fail(errors, f"{prefix}.reproType has invalid value")
        if issue.get("reproStatus") not in ALLOWED_REPRO_STATUS:
            fail(errors, f"{prefix}.reproStatus has invalid value")
        if issue.get("evidenceType") not in ALLOWED_EVIDENCE_TYPE:
            fail(errors, f"{prefix}.evidenceType has invalid value")
        if issue.get("issueOrigin") not in ALLOWED_ISSUE_ORIGIN:
            fail(errors, f"{prefix}.issueOrigin has invalid value")
        for rule_id in issue.get("ruleIds", []):
            if rule_id not in registered_rule_ids:
                fail(errors, f"{prefix} references unregistered rule id: {rule_id}")
        evidence_files = issue.get("evidenceFiles", [])
        for evidence in evidence_files if isinstance(evidence_files, list) else []:
            evidence_path = Path(evidence)
            if not evidence_path.is_absolute():
                evidence_path = report_dir / evidence_path
            try:
                evidence_path.resolve().relative_to(report_dir.resolve())
            except ValueError:
                fail(errors, f"evidence file is outside current report directory: {evidence}")
                continue
            if not evidence_path.is_file() or evidence_path.stat().st_size == 0:
                fail(errors, f"missing or empty evidence file: {evidence}")
        if issue.get("evidenceType") == "None" and evidence_files:
            fail(errors, f"{prefix} evidenceType=None requires empty evidenceFiles")
        if stage == "draft" and issue.get("evidenceLevel") == "L2-RuntimeVerified":
            fail(errors, f"{prefix} draft report cannot use L2-RuntimeVerified")
        if issue.get("status") == "CannotVerify" and not issue.get("cannotVerifyReason"):
            fail(errors, f"{prefix} CannotVerify requires cannotVerifyReason")

    summary = data.get("ruleExecutionSummary", [])
    summary_ids = set()
    for index, item in enumerate(summary, 1):
        prefix = f"ruleExecutionSummary {index}"
        if not isinstance(item, dict):
            continue
        for field in ("ruleId", "domain", "triggerEvidence", "status", "result", "checks", "issuesFound", "notes"):
            validate_required_field(item, field, prefix, errors)
        rule_id = item.get("ruleId")
        if rule_id not in registered_rule_ids:
            fail(errors, f"{prefix} references unregistered rule id: {rule_id}")
        if item.get("status") not in ALLOWED_RULE_STATUS:
            fail(errors, f"{prefix}.status has invalid value")
        if item.get("result") not in ALLOWED_RULE_RESULT:
            fail(errors, f"{prefix}.result has invalid value")
        for field in ("triggerEvidence", "checks", "issuesFound"):
            if not isinstance(item.get(field), list):
                fail(errors, f"{prefix}.{field} must be an array")
        if item.get("status") == "Executed" and not item.get("checks"):
            fail(errors, f"{prefix} Executed requires non-empty checks")
        if item.get("status") == "BlockedByMissingMaterials" and not item.get("notes"):
            fail(errors, f"{prefix} BlockedByMissingMaterials requires notes")
        summary_ids.add(rule_id)
        issue_ids = set(item.get("issuesFound", [])) if isinstance(item.get("issuesFound"), list) else set()
        unknown = issue_ids - all_issue_ids
        if unknown:
            fail(errors, f"{prefix}.issuesFound references unknown issue ids: {', '.join(sorted(unknown))}")
        if item.get("result") == "IssueFound" and not issue_ids:
            fail(errors, f"{prefix} IssueFound requires issuesFound")
        for issue_id in issue_ids:
            issue = next((candidate for candidate in data["issues"] if isinstance(candidate, dict) and candidate.get("id") == issue_id), None)
            if issue is not None and rule_id not in issue.get("ruleIds", []):
                fail(errors, f"{prefix}.issuesFound contains {issue_id}, but that issue does not reference {rule_id}")

    for rule_id in selected_rule_ids:
        if rule_id not in registered_rule_ids:
            fail(errors, f"selectedRuleIds references unregistered rule id: {rule_id}")
    selected_ids = set(selected_rule_ids)
    missing_selected = selected_ids - summary_ids
    if missing_selected:
        fail(errors, f"selected rules missing from ruleExecutionSummary: {', '.join(sorted(missing_selected))}")
    unselected_summary = summary_ids - selected_ids
    if unselected_summary:
        fail(errors, f"ruleExecutionSummary contains rules absent from selectedRuleIds: {', '.join(sorted(unselected_summary))}")
    if data.get("diffFiles") and "general.diff-review" not in selected_ids:
        fail(errors, "non-empty diffFiles requires selectedRuleIds to include general.diff-review")

    issue_rule_ids = {
        rule_id
        for issue in data["issues"] if isinstance(issue, dict)
        for rule_id in issue.get("ruleIds", []) if isinstance(issue.get("ruleIds"), list)
    }
    missing_summary = issue_rule_ids - summary_ids
    if missing_summary:
        fail(errors, f"issue ruleIds missing from ruleExecutionSummary: {', '.join(sorted(missing_summary))}")
    if data.get("diffFiles") and not summary:
        fail(errors, "non-empty diffFiles requires at least one ruleExecutionSummary record")

    if stage == "recheck" and previous_json:
        previous_ids = {
            issue.get("id") for issue in previous_json.get("issues", [])
            if isinstance(issue, dict) and isinstance(issue.get("id"), str)
        }
        current_ids = all_issue_ids
        if not previous_ids.issubset(current_ids):
            fail(errors, f"recheck report dropped previous issue ids: {', '.join(sorted(previous_ids - current_ids))}")
        previous_numbers = [int(value[3:]) for value in previous_ids if ID_PATTERN.fullmatch(value)]
        for issue_id in current_ids - previous_ids:
            if ID_PATTERN.fullmatch(issue_id) and previous_numbers and int(issue_id[3:]) <= max(previous_numbers):
                fail(errors, f"recheck new issue id must be greater than previous max: {issue_id}")


def validate_cross_artifact_consistency(data, md_text, html_text, stage, errors):
    """Check that the three representations describe the same issue set."""
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        return

    json_issues = {
        issue.get("id"): issue
        for issue in data["issues"]
        if isinstance(issue, dict) and isinstance(issue.get("id"), str)
    }
    json_ids = set(json_issues)
    md_ids = set(ISSUE_ID_PATTERN.findall(md_text))
    html_ids = set(ISSUE_ID_PATTERN.findall(html_text))

    for label, ids in (("Markdown", md_ids), ("HTML", html_ids)):
        missing = sorted(json_ids - ids)
        extra = sorted(ids - json_ids)
        if missing:
            fail(errors, f"{label} is missing JSON issue ids: {', '.join(missing)}")
        if extra:
            fail(errors, f"{label} contains issue ids absent from JSON: {', '.join(extra)}")

    stage_labels = {
        "draft": ("Draft", "初版"),
        "final": ("Final", "终版"),
        "recheck": ("Recheck", "复测"),
    }
    if not any(label in md_text for label in stage_labels[stage]):
        fail(errors, f"Markdown does not identify report stage {stage!r}")

    for issue_id, issue in json_issues.items():
        title = issue.get("title")
        if isinstance(title, str) and title:
            if title not in md_text:
                fail(errors, f"Markdown is missing title for {issue_id}: {title}")
            if title not in html_text:
                fail(errors, f"HTML is missing title for {issue_id}: {title}")
        for field in ("severity", "status", "evidenceLevel"):
            value = issue.get(field)
            if isinstance(value, str) and value:
                for label, text in (("Markdown", md_text), ("HTML", html_text)):
                    blocks = re.split(r"(?=\bQC-\d{3,}\b)", text)
                    block = next((part for part in blocks if issue_id in part), "")
                    if value not in block:
                        fail(errors, f"{label} is missing {field}={value!r} for {issue_id}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=("draft", "final", "recheck"))
    parser.add_argument("--previous-json", type=Path, help="Previous report JSON required for recheck ID validation")
    args = parser.parse_args()
    errors = []
    if not args.report_dir.is_dir():
        print(f"ERROR: report directory does not exist: {args.report_dir}", file=sys.stderr)
        return 1

    report_dir = resolve_stage_dir(args.report_dir, args.stage)
    if not report_dir.is_dir():
        print(f"ERROR: stage directory does not exist: {report_dir}", file=sys.stderr)
        return 1

    md_path = find_artifact(report_dir, ".md")
    json_path = find_artifact(report_dir, ".json")
    html_path = find_artifact(report_dir, ".html")
    for path, label in ((md_path, "Markdown"), (json_path, "JSON"), (html_path, "HTML")):
        if path is None:
            fail(errors, f"missing {label} artifact in {report_dir}")

    md_text = read_utf8(md_path, errors) if md_path else ""
    html_text = read_utf8(html_path, errors) if html_path else ""
    json_text = read_utf8(json_path, errors) if json_path else ""

    data = None
    if json_path and json_text:
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            fail(errors, f"invalid JSON: {exc}")
        if data is not None:
            previous_data = None
            if args.previous_json:
                previous_text = read_utf8(args.previous_json, errors)
                if previous_text:
                    try:
                        previous_data = json.loads(previous_text)
                    except json.JSONDecodeError as exc:
                        fail(errors, f"invalid previous JSON: {exc}")
            validate_json(data, json_path, args.stage, report_dir, errors, previous_data)
            validate_cross_artifact_consistency(data, md_text, html_text, args.stage, errors)

    if args.stage == "draft" and any(phrase in md_text for phrase in ("已实测复现", "已实测确认")):
        fail(errors, "draft Markdown must not claim runtime verification")
    if REMOTE_PATTERN.search(html_text):
        fail(errors, "HTML contains a remote resource reference")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: validated {args.stage} report artifacts in {report_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

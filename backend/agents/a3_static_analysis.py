import math
from pathlib import Path
from typing import NamedTuple

from backend.agents.base import AgentBase
from backend.models.findings import Finding, StaticAnalysisReport
from backend.services.repo_layout import (
    bandit_exclude_arg,
    get_scan_targets,
    iter_python_files,
    resolve_source_roots,
    semgrep_exclude_args,
)
from backend.services.sig_helpers import get_file_criticality, get_sig_or_defaults
from backend.services.subprocess_runner import parse_json_safe, run_command
from backend.state.schema import RunStateModel


class ScanOutcome(NamedTuple):
    """Result of one scanner invocation.

    `executed` separates the two cases the agent previously conflated: a scanner
    that ran and legitimately found nothing, versus a scanner that never ran.
    Only the latter may fall back to heuristics, and only in stub mode — a clean
    repository must produce an empty finding list, never a synthesized one.
    """

    executed: bool
    findings: list[dict]
    detail: str = ""

    @classmethod
    def clean(cls, findings: list[dict]) -> "ScanOutcome":
        return cls(executed=True, findings=findings)

    @classmethod
    def failed(cls, detail: str) -> "ScanOutcome":
        return cls(executed=False, findings=[], detail=detail)


class A3StaticAnalysisAgent(AgentBase):
    agent_id = "A3"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Running consensus static analysis")
        repo = Path(state.repo_clone_path or state.repo_path).resolve()

        # Soft read SIG — never block on A1
        sig_data = await self.store.get_json(state.run_id, "sig")
        sig, default_crit = get_sig_or_defaults(sig_data)
        scan_targets = get_scan_targets(state, repo, sig_data)
        source_roots = resolve_source_roots(repo, state.source_roots or None, sig_data)

        raw_findings: list[dict] = []
        baseline: dict = {"bandit": [], "semgrep": [], "ruff": []}

        bandit = self._resolve_outcome(
            await self._run_bandit(repo, scan_targets),
            lambda: self._stub_bandit(repo, scan_targets),
        )
        semgrep = self._resolve_outcome(
            await self._run_semgrep(repo, scan_targets),
            lambda: self._stub_semgrep(repo, scan_targets),
        )
        ruff = self._resolve_outcome(await self._run_ruff(repo, scan_targets), list)

        scanner_status = {
            "bandit": self._status_label(bandit),
            "semgrep": self._status_label(semgrep),
            "ruff": self._status_label(ruff),
        }

        baseline["bandit"] = bandit.findings
        baseline["semgrep"] = semgrep.findings
        baseline["ruff"] = ruff.findings

        for tool, outcome in (("bandit", bandit), ("semgrep", semgrep), ("ruff", ruff)):
            # Only bandit derives severity from the tool's own issue category;
            # semgrep/ruff assign a fixed constant. A stub-mode fallback finding
            # carries the tool's name but is not a real bandit measurement
            # either, so `outcome.executed` gates this, not the tool name alone.
            measured = tool == "bandit" and outcome.executed
            for f in outcome.findings:
                raw_findings.append({**f, "tool": tool, "severity_measured": measured})

        clustered = self._cluster_findings(raw_findings)
        prioritized = self._rank_findings(clustered, sig, default_crit)[:8]

        report = StaticAnalysisReport(
            raw_count=len(raw_findings),
            prioritized=prioritized,
            baseline_json=baseline,
            scanner_status=scanner_status,
        )
        report_dict = report.model_dump(mode="json")
        await self.store.set_json(state.run_id, "static", report_dict)
        state.static_report = report_dict
        await self.emit_status(
            state,
            "completed",
            f"Distilled {len(raw_findings)} raw findings to {len(prioritized)} prioritized",
            {
                "prioritized_count": len(prioritized),
                "source_roots": source_roots,
                "scan_targets": [str(p.resolve().relative_to(repo)) for p in scan_targets],
                "scanner_status": scanner_status,
            },
        )
        return state

    def _resolve_outcome(self, outcome: ScanOutcome, stub) -> ScanOutcome:
        """Apply the stub fallback only to a scanner that failed to execute.

        A scanner that ran and reported nothing yields an empty list. Fabricating
        findings for a clean repository would give A4 a root cause to explain and
        A7 a file to patch that no tool ever flagged.
        """
        if outcome.executed:
            return outcome
        if self.settings.stub_mode:
            return ScanOutcome(executed=False, findings=stub(), detail=outcome.detail)
        return outcome

    @staticmethod
    def _status_label(outcome: ScanOutcome) -> str:
        if outcome.executed:
            return "ok" if outcome.findings else "ok_no_findings"
        return "stubbed" if outcome.findings else "unavailable"

    async def _run_bandit(self, repo: Path, scan_targets: list[Path]) -> ScanOutcome:
        if not scan_targets:
            return ScanOutcome.failed("no scan targets resolved")
        cmd = ["bandit", "-f", "json", "-q", "-x", bandit_exclude_arg()]
        for target in scan_targets:
            cmd.extend(["-r", str(target)])
        code, stdout, stderr = await run_command(cmd, cwd=repo, timeout=60)
        if code == -1:
            return ScanOutcome.failed(stderr.strip() or "bandit could not be executed")

        data = parse_json_safe(stdout)
        # bandit exits 0 with no issues and 1 with issues; anything else that
        # also produced no JSON envelope means the scan itself did not complete.
        if not isinstance(data, dict) or "results" not in data:
            if code not in (0, 1):
                return ScanOutcome.failed(stderr.strip() or f"bandit exited {code}")
            if not stdout.strip():
                return ScanOutcome.failed("bandit produced no output")
            return ScanOutcome.failed("bandit output could not be parsed")

        results = []
        for r in data.get("results", []):
            results.append({
                "file": r.get("filename", "").replace(str(repo) + "/", ""),
                "line": r.get("line_number", 0),
                "message": r.get("issue_text", ""),
                "severity": {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.3}.get(r.get("issue_severity", "LOW"), 0.3),
            })
        return ScanOutcome.clean(results)

    def _roots_from_targets(self, repo: Path, scan_targets: list[Path]) -> list[str]:
        repo = repo.resolve()
        if not scan_targets:
            return [""]
        roots: list[str] = []
        for target in scan_targets:
            try:
                rel = str(target.resolve().relative_to(repo)).replace("\\", "/")
                roots.append(rel + "/" if rel else "")
            except ValueError:
                roots.append("")
        return roots

    def _stub_bandit(self, repo: Path, scan_targets: list[Path]) -> list[dict]:
        findings = []
        roots = self._roots_from_targets(repo, scan_targets)
        for py in iter_python_files(repo, roots):
            content = py.read_text(encoding="utf-8")
            rel = str(py.relative_to(repo))
            if "pickle" in content:
                findings.append({"file": rel, "line": 1, "message": "pickle usage", "severity": 0.9})
            if "secret" in content.lower() and "=" in content:
                findings.append({"file": rel, "line": 1, "message": "hardcoded secret", "severity": 0.8})
        return findings

    async def _run_semgrep(self, repo: Path, scan_targets: list[Path]) -> ScanOutcome:
        if not scan_targets:
            return ScanOutcome.failed("no scan targets resolved")
        cmd = ["semgrep", "--config=auto", "--json", *semgrep_exclude_args()]
        cmd.extend(str(t) for t in scan_targets)
        code, stdout, stderr = await run_command(cmd, cwd=repo, timeout=90)
        if code == -1:
            return ScanOutcome.failed(stderr.strip() or "semgrep could not be executed")
        if not stdout.strip():
            return ScanOutcome.failed(stderr.strip() or "semgrep produced no output")

        data = parse_json_safe(stdout)
        if not isinstance(data, dict) or "results" not in data:
            return ScanOutcome.failed("semgrep output could not be parsed")

        results = []
        for r in data.get("results", []):
            results.append({
                "file": r.get("path", "").replace(str(repo) + "/", ""),
                "line": r.get("start", {}).get("line", 0),
                "message": r.get("extra", {}).get("message", ""),
                "severity": 0.7,
            })
        return ScanOutcome.clean(results)

    def _stub_semgrep(self, repo: Path, scan_targets: list[Path]) -> list[dict]:
        findings = []
        roots = self._roots_from_targets(repo, scan_targets)
        for py in iter_python_files(repo, roots):
            try:
                content = py.read_text(encoding="utf-8")
            except OSError:
                continue
            if 'f"SELECT' in content or "f'SELECT" in content:
                rel = str(py.relative_to(repo))
                line = content.find("SELECT")
                line_no = content[: max(line, 0)].count("\n") + 1 if line >= 0 else 1
                findings.append({
                    "file": rel,
                    "line": line_no,
                    "message": "SQL injection",
                    "severity": 0.95,
                })
        return findings

    async def _run_ruff(self, repo: Path, scan_targets: list[Path]) -> ScanOutcome:
        if not scan_targets:
            return ScanOutcome.failed("no scan targets resolved")
        cmd = ["ruff", "check", "--output-format=json"]
        cmd.extend(str(t) for t in scan_targets)
        code, stdout, stderr = await run_command(cmd, cwd=repo, timeout=60)
        if code == -1:
            return ScanOutcome.failed(stderr.strip() or "ruff could not be executed")

        data = parse_json_safe(stdout) if stdout.strip() else []
        if isinstance(data, dict):
            data = data.get("results", [])
        results = []
        for r in data if isinstance(data, list) else []:
            results.append({
                "file": r.get("filename", "").replace(str(repo) + "/", ""),
                "line": r.get("location", {}).get("row", 0),
                "message": r.get("message", ""),
                "severity": 0.4,
            })
        return ScanOutcome.clean(results)

    def _cluster_findings(self, raw: list[dict]) -> list[dict]:
        clusters: dict[str, dict] = {}
        for f in raw:
            file = f.get("file", "")
            line = f.get("line", 0)
            key = f"{file}:{line // 5 * 5}"
            tool = f.get("tool", "unknown")
            severity = f.get("severity", 0.5)
            measured = bool(f.get("severity_measured", False))
            if key not in clusters:
                clusters[key] = {
                    "file": file,
                    "line": line,
                    "message": f.get("message", ""),
                    "tools": [],
                    "severity": severity,
                    # Whether the finding currently holding `severity` (the
                    # cluster's max) came from a real measurement. Tracked
                    # alongside the value itself so `_rank_findings` can say
                    # whether the number on screen was measured or assigned.
                    "severity_measured": measured,
                }
            if tool not in clusters[key]["tools"]:
                clusters[key]["tools"].append(tool)
            if severity > clusters[key]["severity"]:
                clusters[key]["severity"] = severity
                clusters[key]["severity_measured"] = measured
        for c in clusters.values():
            c["consensus"] = len(c["tools"]) >= 2
        return list(clusters.values())

    def _rank_findings(self, clustered: list[dict], sig, default_crit: float) -> list[Finding]:
        ranked: list[Finding] = []
        for i, c in enumerate(clustered):
            crit = get_file_criticality(sig, c["file"], default_crit)
            churn = 0.5
            if sig and c["file"] in sig.files:
                churn = sig.files[c["file"]].churn_weight
            blast = c["severity"] * crit * math.log(max(1, len(c["tools"]) + 1)) * (1 + churn)
            ranked.append(
                Finding(
                    id=f"finding-{i}",
                    file=c["file"],
                    line=c["line"],
                    message=c["message"],
                    tools=c["tools"],
                    severity=c["severity"],
                    blast_radius_score=blast,
                    consensus=c["consensus"],
                    criticality=crit,
                    churn_weight=churn,
                    severity_measured=bool(c.get("severity_measured", False)),
                )
            )
        ranked.sort(key=lambda x: x.blast_radius_score, reverse=True)
        return ranked

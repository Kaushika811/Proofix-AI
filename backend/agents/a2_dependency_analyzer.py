from pathlib import Path

from backend.agents.base import AgentBase
from backend.models.cve import CVERecord, CVEReachabilityReport
from backend.services.osv_client import (
    parse_pyproject_dependencies,
    parse_requirements,
    query_osv,
)
from backend.services.sig_helpers import (
    get_sig_or_defaults,
    is_module_reachable,
    reachable_modules,
)
from backend.state.schema import RunStateModel


class A2DependencyAnalyzerAgent(AgentBase):
    agent_id = "A2"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Analyzing dependencies for CVE reachability")
        repo = Path(state.repo_clone_path or state.repo_path)

        # Soft read SIG — never block on A1
        sig_data = await self.store.get_json(state.run_id, "sig")
        sig, _ = get_sig_or_defaults(sig_data)

        manifest_name, ecosystem, packages = self._resolve_packages(repo)
        findings: list[CVERecord] = []
        critical_queue: list[str] = []

        for package, version in packages:
            vulns = await query_osv(package, version)
            for vuln in vulns:
                cve_id = vuln.get("id", "UNKNOWN")
                severity = "HIGH"
                for sev in vuln.get("severity", []):
                    if sev.get("type") == "CVSS_V3":
                        severity = sev.get("score", "HIGH")

                reachable = is_module_reachable(sig, package)
                reach_path = reachable_modules(sig, package) if reachable else None
                if reachable is None:
                    classification = "Unknown"
                elif reachable:
                    classification = "Critical"
                    critical_queue.append(cve_id)
                else:
                    classification = "Informational"

                findings.append(
                    CVERecord(
                        package=package,
                        cve_id=cve_id,
                        severity=str(severity),
                        installed_version=version,
                        affected_symbol=None,
                        reachable=reachable,
                        reach_path=reach_path,
                        classification=classification,  # type: ignore[arg-type]
                    )
                )

        report = CVEReachabilityReport(
            findings=findings,
            critical_queue=critical_queue,
            total_dependencies=len(packages),
            manifest=manifest_name,
            ecosystem=ecosystem,
        )
        report_dict = report.model_dump(mode="json")
        await self.store.set_json(state.run_id, "cve", report_dict)
        state.cve_report = report_dict
        await self.emit_status(
            state,
            "completed",
            f"Found {len(findings)} CVE records, {len(critical_queue)} critical",
            {
                "critical_count": len(critical_queue),
                "unknown_count": sum(1 for f in findings if f.classification == "Unknown"),
                "source_roots": (sig.source_roots if sig else []),
            },
        )
        return state

    @staticmethod
    def _resolve_packages(repo: Path) -> tuple[str | None, str | None, list[tuple[str, str]]]:
        requirements = repo / "requirements.txt"
        if requirements.exists():
            return "requirements.txt", "PyPI", parse_requirements(requirements)

        pyproject = repo / "pyproject.toml"
        if pyproject.exists():
            return "pyproject.toml", "PyPI", parse_pyproject_dependencies(pyproject)

        return None, None, []

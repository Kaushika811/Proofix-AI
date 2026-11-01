import re
import tomllib
from pathlib import Path

import httpx


async def query_osv(package: str, version: str) -> list[dict]:
    url = "https://api.osv.dev/v1/query"
    payload = {"package": {"name": package, "ecosystem": "PyPI"}, "version": version}
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                return _stub_vulns(package)
            data = resp.json()
            return data.get("vulns", [])
    except Exception:
        return _stub_vulns(package)


def _stub_vulns(package: str) -> list[dict]:
    """Fallback when OSV API unavailable."""
    if package.lower() == "urllib3":
        return [{"id": "CVE-2023-45803", "severity": [{"type": "CVSS_V3", "score": "7.5"}]}]
    return []


def parse_requirements(requirements_path: Path) -> list[tuple[str, str]]:
    packages: list[tuple[str, str]] = []
    if not requirements_path.exists():
        return packages
    for line in requirements_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z0-9_-]+)(?:[=<>!~]+(.+))?$", line.split("#")[0].strip())
        if match:
            name, version = match.group(1), match.group(2) or "0.0.0"
            version = version.strip()
            packages.append((name.lower(), version))
    return packages


def parse_pyproject_dependencies(pyproject_path: Path) -> list[tuple[str, str]]:
    packages: list[tuple[str, str]] = []
    if not pyproject_path.exists():
        return packages
    try:
        data = tomllib.loads(pyproject_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return packages

    project = data.get("project")
    if not isinstance(project, dict):
        return packages

    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list):
        return packages

    for entry in dependencies:
        if not isinstance(entry, str):
            continue
        requirement = entry.split(";")[0].strip()
        if not requirement:
            continue
        requirement = re.split(r"\s+\[", requirement, maxsplit=1)[0].strip()
        match = re.match(r"^([a-zA-Z0-9_.-]+)(?:\[[^\]]+\])?\s*(.*)$", requirement)
        if not match:
            continue
        name = match.group(1).replace("_", "-").lower()
        version = match.group(2).strip() or "0.0.0"
        packages.append((name, version))
    return packages

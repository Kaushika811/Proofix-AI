
from backend.models.sig import FileNode, SemanticIntentGraph
from backend.services.sig_helpers import is_module_reachable, reclassify_cve_report


def test_is_module_reachable_false():
    sig = SemanticIntentGraph(
        repo_path="/tmp",
        source_roots=["src/myapp/"],
        files={
            "src/myapp/api.py": FileNode(
                path="src/myapp/api.py",
                role="public-api",
                imports=["sqlite3"],
                criticality=0.85,
            )
        },
    )
    assert is_module_reachable(sig, "urllib3") is False


def test_is_module_reachable_unknown():
    assert is_module_reachable(None, "urllib3") is None


def test_reclassify_cve_report():
    sig = {
        "repo_path": "/tmp",
        "source_roots": ["src/myapp/"],
        "files": {
            "src/myapp/api.py": {
                "path": "src/myapp/api.py",
                "role": "public-api",
                "imports": ["requests"],
                "imported_by": [],
                "churn_weight": 0.5,
                "criticality": 0.85,
            }
        },
        "edges": [],
    }
    cve = {
        "findings": [
            {
                "package": "urllib3",
                "cve_id": "CVE-TEST",
                "severity": "HIGH",
                "reachable": None,
                "classification": "Unknown",
            }
        ],
        "critical_queue": [],
    }
    result = reclassify_cve_report(sig, cve)
    assert result["findings"][0]["classification"] == "Informational"

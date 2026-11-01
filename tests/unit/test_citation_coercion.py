from backend.models.root_cause import EvidenceReference
from backend.services.citation_verifier import coerce_llm_citations


def test_coerce_llm_citations_drops_null_fields():
    result = coerce_llm_citations(
        [
            {"file": None, "line": None, "claim": "bad"},
            {"file": "vulnapi/auth.py", "line": 12, "claim": "missing check"},
        ]
    )
    assert result == [{"file": "vulnapi/auth.py", "line": 12, "claim": "missing check"}]


def test_coerce_llm_citations_falls_back_to_evidence_refs():
    refs = [
        EvidenceReference(
            source="finding",
            file="vulnapi/api.py",
            line=44,
            claim="SQL injection risk",
        )
    ]
    result = coerce_llm_citations([{"file": None, "line": None, "claim": "bad"}], refs)
    assert result == [{"file": "vulnapi/api.py", "line": 44, "claim": "SQL injection risk"}]

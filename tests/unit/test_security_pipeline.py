"""Isolation, sanitization, compliance, and the mandatory end-to-end gate.

The load-bearing tests here are the ones proving the gate cannot be bypassed:
`LLMGateway.complete` refuses to reach a provider without an approval, and the
prompt that reaches the provider is the sanitized one, not the caller's original.
"""

import ast

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.config import Settings
from backend.models.context import ContextPackage, ExtractedSymbol
from backend.security.compliance_engine import (
    SUPPORTED_FRAMEWORKS,
    ComplianceContext,
    ComplianceEngine,
)
from backend.security.policy_engine import BUILTIN_POLICIES
from backend.security.privacy_guard import ContextPrivacyGuard, GuardOptions
from backend.security.repository_isolation import (
    IsolationViolation,
    create_workspace,
    guard_for,
    scrub_environment,
)
from backend.security.sanitizer import SanitizerConfig, new_allocator, sanitize
from backend.services.llm_gateway import LLMGateway, SecurityRejection
from backend.services.security_pipeline import (
    SecurityPipeline,
    SecurityRequest,
    get_security_pipeline,
    reset_security_pipeline,
)
from backend.state.redis_store import RedisStore


@pytest.fixture(autouse=True)
def _clean_pipeline():
    reset_security_pipeline()
    yield
    reset_security_pipeline()


@pytest_asyncio.fixture
async def store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisStore(client, Settings(stub_mode=True))
    await client.aclose()


def settings(**overrides) -> Settings:
    base = {"anthropic_api_key": "k", "llm_provider": "anthropic", "stub_mode": False}
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def pipeline(store):
    return SecurityPipeline(settings(), store)


# ===================================================== repository isolation


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "auth.py").write_text("def login():\n    return 1\n")
    return guard_for(tmp_path)


def test_file_inside_workspace_is_allowed(workspace):
    assert workspace.is_safe("pkg/auth.py")


def test_absolute_path_outside_is_refused(workspace):
    with pytest.raises(IsolationViolation, match="outside the workspace"):
        workspace.check("/etc/passwd")


def test_traversal_is_refused_after_normalization(workspace):
    """Checking the literal string for `..` misses `a/b/../../../etc`."""
    with pytest.raises(IsolationViolation):
        workspace.check("pkg/../../../etc/passwd")


def test_harmless_dotdot_inside_the_workspace_is_allowed(workspace):
    assert workspace.is_safe("pkg/../pkg/auth.py")


def test_symlink_escape_is_refused(tmp_path):
    """The subtle case: path inside, target outside."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")

    inside = tmp_path / "repo"
    inside.mkdir()
    (inside / "link.txt").symlink_to(outside / "secret.txt")

    guard = guard_for(inside)
    with pytest.raises(IsolationViolation):
        guard.check("link.txt")


def test_symlink_inside_the_workspace_is_allowed(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("x = 1\n")
    (repo / "link.py").symlink_to(repo / "pkg" / "a.py")
    assert guard_for(repo).is_safe("link.py")


def test_symlink_escapes_are_listed_not_removed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "escape").symlink_to(outside)

    guard = guard_for(repo)
    assert "escape" in guard.list_symlink_escapes()
    assert (repo / "escape").exists()  # not deleted


@pytest.mark.parametrize(
    "path", [".env", ".git-credentials", ".netrc", "id_rsa", ".npmrc", ".pypirc"]
)
def test_credential_files_are_refused(workspace, path):
    with pytest.raises(IsolationViolation, match="credential"):
        workspace.check(path)


@pytest.mark.parametrize("directory", [".ssh", ".aws", ".kube", ".gnupg"])
def test_credential_directories_are_refused(workspace, directory):
    with pytest.raises(IsolationViolation, match="credential"):
        workspace.check(f"{directory}/config")


def test_read_only_workspace_refuses_writes(tmp_path):
    guard = guard_for(tmp_path, allow_writes=False)
    with pytest.raises(IsolationViolation, match="read-only"):
        guard.check("new.py", for_write=True)


def test_read_only_workspace_still_permits_reads(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert guard_for(tmp_path, allow_writes=False).is_safe("a.py")


def test_safe_read_returns_content(workspace):
    assert "def login" in workspace.safe_read("pkg/auth.py")


def test_safe_read_refuses_an_escape(workspace):
    with pytest.raises(IsolationViolation):
        workspace.safe_read("/etc/hosts")


def test_relativize_removes_the_host_path(workspace):
    assert workspace.relativize(workspace.root / "pkg" / "auth.py") == "pkg/auth.py"


def test_relativize_masks_unknown_host_paths(workspace):
    assert "/Users/" not in workspace.relativize("/Users/alice/other/file.py")


def test_scrub_paths_removes_workspace_prefix(workspace):
    text = f'File "{workspace.root}/pkg/auth.py", line 3'
    assert str(workspace.root) not in workspace.scrub_paths(text)


def test_scrub_paths_masks_home_directories(workspace):
    assert "/Users/alice" not in workspace.scrub_paths('File "/Users/alice/a.py"')


def test_scrub_environment_masks_variables():
    scrubbed = scrub_environment("PATH=/usr/bin:/bin\nHOME=/Users/alice\nx = 1")
    assert "/usr/bin" not in scrubbed
    assert "/Users/alice" not in scrubbed
    assert "x = 1" in scrubbed


def test_violations_are_recorded(workspace):
    workspace.is_safe("/etc/passwd")
    assert workspace.violations
    assert workspace.status()["violation_count"] == 1


def test_create_workspace_is_isolated():
    guard = create_workspace()
    assert guard.root.is_dir()
    assert not guard.is_safe("/etc/passwd")


# ===================================================== sanitizer


@pytest.fixture
def config():
    return SanitizerConfig(
        company_identifiers=("acme",),
        internal_domains=("acme.net",),
        private_package_prefixes=("acme_core",),
    )


def test_internal_url_is_aliased(config):
    result = sanitize('API = "https://api.acme.net/v1"', "x.py", config, new_allocator())
    assert "acme.net" not in result.text
    assert "example.internal" in result.text


def test_public_url_is_preserved(config):
    result = sanitize('URL = "https://github.com/org/repo"', "x.py", config, new_allocator())
    assert "github.com" in result.text


def test_internal_suffix_hostname_is_aliased(config):
    result = sanitize('DB = "db-primary.internal"', "x.py", config, new_allocator())
    assert "db-primary" not in result.text


def test_private_package_keeps_its_structure(config):
    """The module hierarchy the repair reasons about must survive."""
    result = sanitize("import acme_core.billing.invoice", "x.py", config, new_allocator())
    assert "acme_core" not in result.text
    assert ".billing.invoice" in result.text


def test_sanitized_code_still_parses(config):
    source = 'import acme_core.billing\n\nURL = "https://api.acme.net"\n\n\ndef go():\n    return URL\n'
    ast.parse(sanitize(source, "x.py", config, new_allocator()).text)


def test_confidential_comment_block_is_removed(config):
    source = "# CONFIDENTIAL: pricing model\n# details follow\nx = 1\n"
    result = sanitize(source, "x.py", config, new_allocator())
    assert "pricing model" not in result.text
    assert "x = 1" in result.text


@pytest.mark.parametrize("marker", ["CONFIDENTIAL", "PROPRIETARY", "INTERNAL ONLY", "TRADE SECRET"])
def test_every_confidentiality_marker(config, marker):
    result = sanitize(f"# {marker}: detail here\nx = 1\n", "x.py", config, new_allocator())
    assert "detail here" not in result.text


def test_confidential_stripping_can_be_disabled():
    cfg = SanitizerConfig(strip_confidential_comments=False)
    source = "# CONFIDENTIAL: detail\nx = 1\n"
    assert "detail" in sanitize(source, "x.py", cfg, new_allocator()).text


def test_string_mentioning_confidential_is_not_stripped(config):
    source = 'MESSAGE = "This document is CONFIDENTIAL"\n'
    assert "CONFIDENTIAL" in sanitize(source, "x.py", config, new_allocator()).text


def test_aliases_are_consistent_across_files(config):
    allocator = new_allocator()
    first = sanitize('h = "db.acme.net"', "a.py", config, allocator)
    second = sanitize('h = "db.acme.net"', "b.py", config, allocator)
    assert first.text == second.text


def test_different_hosts_get_different_aliases(config):
    allocator = new_allocator()
    result = sanitize('a = "one.acme.net"\nb = "two.acme.net"', "x.py", config, allocator)
    assert "host0" in result.text and "host1" in result.text


def test_repository_name_redaction_is_opt_in():
    off = SanitizerConfig(repository_names=("secret-repo",))
    on = SanitizerConfig(repository_names=("secret-repo",), redact_repository_names=True)
    assert "secret-repo" in sanitize("repo: secret-repo", "x", off, new_allocator()).text
    assert "secret-repo" not in sanitize("repo: secret-repo", "x", on, new_allocator()).text


def test_empty_config_changes_nothing():
    source = 'import os\n\nURL = "https://github.com"\n'
    assert sanitize(source, "x.py", SanitizerConfig(), new_allocator()).text == source


def test_findings_record_category_and_line(config):
    result = sanitize('x = 1\nAPI = "https://api.acme.net"\n', "x.py", config, new_allocator())
    finding = next(f for f in result.findings if f.category == "internal_url")
    assert finding.line == 2


# ===================================================== privacy guard


def package(**overrides) -> ContextPackage:
    base = dict(
        target_file="pkg/auth.py",
        root_cause_summary="token compared against secret 'hunter2SuperSecret'",
        focused_context='def login():\n    password = "P@ssw0rd12345"\n    return True\n',
        relevant_functions=[
            ExtractedSymbol(
                name="login", file="pkg/auth.py", kind="target_function",
                source='def login():\n    key = "ghp_' + "a" * 36 + '"\n',
            )
        ],
        runtime_evidence={"traceback": 'File "/Users/alice/pkg/auth.py", line 3'},
        acceptance_criteria=["contact ada@corp.example if unclear"],
    )
    base.update(overrides)
    return ContextPackage(**base)


@pytest.fixture
def guard():
    return ContextPrivacyGuard(GuardOptions())


def test_package_secrets_are_redacted(guard):
    outcome = guard.sanitize_package(package())
    assert outcome.approved
    assert "P@ssw0rd12345" not in outcome.package.focused_context
    assert outcome.report.secret_count > 0


def test_package_symbol_sources_are_redacted(guard):
    outcome = guard.sanitize_package(package())
    assert "ghp_" not in outcome.package.relevant_functions[0].source


def test_package_pii_is_redacted(guard):
    outcome = guard.sanitize_package(package())
    assert "ada@corp.example" not in str(outcome.package.acceptance_criteria)
    assert outcome.report.pii_count > 0


def test_runtime_evidence_host_paths_are_removed(guard):
    outcome = guard.sanitize_package(package())
    assert "/Users/alice" not in str(outcome.package.runtime_evidence)


def test_original_package_is_never_mutated(guard):
    """A5.5's stored artifact must remain what A5.5 produced."""
    original = package()
    before = original.focused_context
    guard.sanitize_package(original)
    assert original.focused_context == before


def test_clean_package_reports_clean(guard):
    clean = package(
        root_cause_summary="off-by-one in the loop bound",
        focused_context="def add(a, b):\n    return a + b\n",
        relevant_functions=[],
        runtime_evidence={},
        acceptance_criteria=["the test must pass"],
    )
    outcome = guard.sanitize_package(clean)
    assert outcome.report.status == "clean"
    assert outcome.package.privacy_guard_status == "clean"


def test_ranking_data_is_untouched(guard):
    """Security validates and sanitizes; it never re-ranks."""
    original = package()
    outcome = guard.sanitize_package(original)
    assert outcome.package.ranked_files == original.ranked_files
    assert outcome.package.metrics == original.metrics
    assert outcome.package.ranking_version == original.ranking_version


def test_guard_failure_withholds_the_package():
    """Fail closed: a broken guard yields nothing, never unsanitized content."""

    class Exploding(ContextPrivacyGuard):
        def sanitize_text(self, text, file, report):
            raise RuntimeError("detector crashed")

    outcome = Exploding(GuardOptions()).sanitize_package(package())
    assert outcome.package is None
    assert not outcome.approved
    assert outcome.report.status == "failed"


def test_detectors_can_be_disabled_individually():
    guard = ContextPrivacyGuard(GuardOptions(detect_pii=False))
    outcome = guard.sanitize_package(package())
    assert outcome.report.pii_count == 0
    assert outcome.report.secret_count > 0


# ===================================================== pipeline


@pytest.mark.asyncio
async def test_clean_request_is_approved(pipeline):
    approved = await pipeline.approve(SecurityRequest(prompt="def f():\n    return 1", run_id="r1"))
    assert approved.approved
    assert approved.routing.provider == "anthropic"


@pytest.mark.asyncio
async def test_secrets_are_removed_before_egress(pipeline):
    approved = await pipeline.approve(
        SecurityRequest(prompt='key = "ghp_' + "a" * 36 + '"', run_id="r1")
    )
    assert approved.approved
    assert "ghp_" not in approved.prompt
    assert approved.sanitization.secret_count > 0


@pytest.mark.asyncio
async def test_pii_is_removed_before_egress(pipeline):
    approved = await pipeline.approve(SecurityRequest(prompt="mail ada@corp.example", run_id="r1"))
    assert "ada@corp.example" not in approved.prompt


@pytest.mark.asyncio
async def test_host_paths_are_scrubbed(pipeline):
    approved = await pipeline.approve(
        SecurityRequest(prompt='File "/Users/alice/a.py", line 1', run_id="r1")
    )
    assert "/Users/alice" not in approved.prompt


@pytest.mark.asyncio
async def test_confidential_without_local_provider_is_rejected(pipeline):
    approved = await pipeline.approve(
        SecurityRequest(prompt="x = 1", run_id="r1", classification="CONFIDENTIAL")
    )
    assert not approved.approved
    assert approved.rejection_reason


@pytest.mark.asyncio
async def test_confidential_with_local_provider_is_approved(store):
    pipeline = SecurityPipeline(settings(ollama_base_url="http://localhost:11434"), store)
    approved = await pipeline.approve(
        SecurityRequest(prompt="x = 1", run_id="r1", classification="CONFIDENTIAL")
    )
    assert approved.approved
    assert approved.routing.is_local


@pytest.mark.asyncio
async def test_repository_dump_is_rejected(pipeline):
    prompt = " ".join(f"pkg/module_{i}.py" for i in range(60))
    approved = await pipeline.approve(SecurityRequest(prompt=prompt, run_id="r1"))
    assert not approved.approved
    assert "whole_repository" in approved.firewall.rules_failed


@pytest.mark.asyncio
async def test_private_key_is_rejected(pipeline):
    pem = "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"
    approved = await pipeline.approve(SecurityRequest(prompt=pem, run_id="r1"))
    assert not approved.approved


@pytest.mark.asyncio
async def test_rejection_is_audited(pipeline):
    await pipeline.approve(
        SecurityRequest(prompt="x", run_id="r1", classification="AIR_GAPPED")
    )
    events = pipeline.audit.events("r1")
    assert events
    assert events[-1].result == "rejected"


@pytest.mark.asyncio
async def test_approval_and_completion_are_audited(pipeline):
    approved = await pipeline.approve(SecurityRequest(prompt="x = 1", run_id="r1"))
    await pipeline.record_completion(approved, response="ok", total_tokens=10, estimated_cost_usd=0.01)
    assert pipeline.audit.events("r1")[-1].result == "success"


@pytest.mark.asyncio
async def test_audit_chain_stays_intact_across_calls(pipeline):
    for i in range(4):
        approved = await pipeline.approve(SecurityRequest(prompt=f"x = {i}", run_id="r1"))
        if approved.approved:
            await pipeline.record_completion(approved)
    intact, _detail = pipeline.audit.verify_chain()
    assert intact


@pytest.mark.asyncio
async def test_metrics_accumulate(pipeline):
    approved = await pipeline.approve(
        SecurityRequest(prompt='k = "ghp_' + "a" * 36 + '"', run_id="r1")
    )
    await pipeline.record_completion(approved, estimated_cost_usd=0.002)
    assert pipeline.metrics.secrets_detected > 0
    assert pipeline.metrics.llm_calls == 1
    assert pipeline.metrics.estimated_cost_usd == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_dashboard_exposes_every_required_metric(pipeline):
    await pipeline.approve(SecurityRequest(prompt="x = 1", run_id="r1"))
    dashboard = pipeline.dashboard()
    for key in (
        "secrets_detected", "pii_detected", "contexts_sanitized", "policies_applied",
        "policy_violations", "llm_calls", "provider_usage", "estimated_cost_usd",
        "average_prompt_chars", "rejected_requests", "compliance", "routing_matrix",
        "secret_categories", "encryption", "audit",
    ):
        assert key in dashboard, key


@pytest.mark.asyncio
async def test_approval_is_deterministic(pipeline):
    request = SecurityRequest(prompt='k = "ghp_' + "a" * 36 + '"', run_id="r1")
    first = await pipeline.approve(request)
    second = await pipeline.approve(request)
    assert first.prompt == second.prompt
    assert first.approved == second.approved


# ===================================================== gateway gate


def gateway_with(monkeypatch, **overrides) -> LLMGateway:
    gateway = LLMGateway(settings(**overrides))

    async def unreachable(*args, **kwargs):
        raise AssertionError("provider reached without an approved context")

    monkeypatch.setattr(gateway, "_dispatch", unreachable)
    return gateway


@pytest.mark.asyncio
async def test_gateway_refuses_a_rejected_prompt(monkeypatch):
    """No bypass: the provider is never reached without approval."""
    gateway = gateway_with(monkeypatch)
    with pytest.raises(SecurityRejection):
        await gateway.complete(
            "-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----", "system"
        )


@pytest.mark.asyncio
async def test_gateway_rejection_names_the_failing_rules(monkeypatch):
    gateway = gateway_with(monkeypatch)
    with pytest.raises(SecurityRejection) as excinfo:
        await gateway.complete(" ".join(f"m{i}.py" for i in range(60)), "system")
    assert excinfo.value.rules


@pytest.mark.asyncio
async def test_gateway_sends_the_sanitized_prompt(monkeypatch):
    """The provider receives the approved text, not the caller's original."""
    captured: dict = {}
    gateway = LLMGateway(settings())

    async def capture(prompt, system, *, json_mode):
        captured["prompt"] = prompt
        return "ok", None

    monkeypatch.setattr(gateway, "_dispatch", capture)
    await gateway.complete('key = "ghp_' + "a" * 36 + '"', "system")

    assert "ghp_" not in captured["prompt"]
    assert "<REDACTED_GITHUB_TOKEN>" in captured["prompt"]


@pytest.mark.asyncio
async def test_gateway_audits_the_completion(monkeypatch):
    gateway = LLMGateway(settings())

    async def respond(prompt, system, *, json_mode):
        return "patched", None

    monkeypatch.setattr(gateway, "_dispatch", respond)
    await gateway.complete("x = 1", "system", run_id="r1")

    assert get_security_pipeline(gateway.settings).audit.events("r1")


@pytest.mark.asyncio
async def test_gateway_can_be_disabled_by_configuration(monkeypatch):
    """Explicit opt-out, so the control is visible in configuration."""
    captured: dict = {}
    gateway = LLMGateway(settings(security_enabled=False))

    async def capture(prompt, system, *, json_mode):
        captured["prompt"] = prompt
        return "ok", None

    monkeypatch.setattr(gateway, "_dispatch", capture)
    await gateway.complete('key = "ghp_' + "a" * 36 + '"', "system")
    assert "ghp_" in captured["prompt"]


@pytest.mark.asyncio
async def test_security_failure_fails_closed(monkeypatch):
    gateway = gateway_with(monkeypatch)

    async def explode(self, request):
        raise RuntimeError("control fault")

    monkeypatch.setattr(SecurityPipeline, "approve", explode)
    with pytest.raises(SecurityRejection, match="failed closed"):
        await gateway.complete("x = 1", "system")


# ===================================================== compliance


def compliance_context(**overrides) -> ComplianceContext:
    base = dict(events=[], policies=BUILTIN_POLICIES, encryption_enabled=True)
    base.update(overrides)
    return ComplianceContext(**base)


@pytest.mark.parametrize("framework", SUPPORTED_FRAMEWORKS)
def test_every_framework_produces_a_report(framework):
    report = ComplianceEngine(compliance_context()).report(framework)
    assert report.framework == framework
    assert report.controls


def test_unknown_framework_is_refused():
    with pytest.raises(ValueError, match="unknown framework"):
        ComplianceEngine(compliance_context()).report("MADE_UP")


def test_missing_encryption_fails_the_encryption_control():
    report = ComplianceEngine(compliance_context(encryption_enabled=False)).report("SOC2")
    failed = [c.control_id for c in report.failed]
    assert failed
    assert all(c.recommendation for c in report.failed)


def test_broken_audit_chain_fails_integrity():
    context = compliance_context(audit_chain_intact=False, audit_chain_detail="broken at 3")
    report = ComplianceEngine(context).report("ISO27001")
    assert not report.compliant


def test_raw_prompt_storage_fails_gdpr_retention():
    report = ComplianceEngine(compliance_context(raw_prompts_stored=True)).report("GDPR")
    assert "Art.5(1)(e)" in [c.control_id for c in report.failed]


def test_isolation_violations_fail_the_isolation_control():
    report = ComplianceEngine(compliance_context(isolation_violations=2)).report("SOC2")
    assert "CC7.4" in [c.control_id for c in report.failed]


def test_organisational_controls_are_marked_not_applicable():
    """Not silently counted as passes."""
    report = ComplianceEngine(compliance_context()).report("SOC2")
    assert any(c.status == "not_applicable" for c in report.controls)


def test_not_applicable_controls_are_excluded_from_the_score():
    report = ComplianceEngine(compliance_context()).report("SOC2")
    applicable = [c for c in report.controls if c.status != "not_applicable"]
    assert report.score == pytest.approx(len(report.passed) / len(applicable))


def test_clean_configuration_is_compliant_everywhere():
    summary = ComplianceEngine(compliance_context()).summary()
    assert all(entry["compliant"] for entry in summary.values())


def test_every_control_carries_evidence():
    for framework in SUPPORTED_FRAMEWORKS:
        for control in ComplianceEngine(compliance_context()).report(framework).controls:
            assert control.evidence, f"{framework}:{control.control_id}"


def test_summary_covers_every_framework():
    assert set(ComplianceEngine(compliance_context()).summary()) == set(SUPPORTED_FRAMEWORKS)


def test_reports_are_deterministic():
    engine = ComplianceEngine(compliance_context())
    first = engine.report("GDPR")
    second = engine.report("GDPR")
    assert [c.status for c in first.controls] == [c.status for c in second.controls]


# ===================================================== API surface


def test_security_routes_are_registered():
    from backend.main import create_app

    paths = set(create_app().openapi()["paths"])
    for path in (
        "/api/security/dashboard",
        "/api/security/metrics",
        "/api/security/policies",
        "/api/security/routing",
        "/api/security/timeline",
        "/api/security/compliance",
        "/api/security/encryption",
    ):
        assert path in paths, path


def test_existing_routes_are_untouched():
    from backend.main import create_app

    paths = set(create_app().openapi()["paths"])
    assert "/api/runs" in paths
    assert any(p.startswith("/api/knowledge") for p in paths)


def test_all_security_routes_are_namespaced():
    from backend.api.routes import security

    assert all(r.path.startswith("/api/security") for r in security.router.routes)

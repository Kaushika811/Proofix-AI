from backend.services.subprocess_runner import repository_subprocess_env


def test_repository_subprocess_env_removes_proofix_service_config(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://proofix-orpin.vercel.app")
    monkeypatch.setenv("MISTRAL_API_KEY", "secret")
    monkeypatch.setenv("SARVAM_API_KEY", "secret")
    monkeypatch.setenv("STUB_MODE", "false")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = repository_subprocess_env()

    assert "CORS_ORIGINS" not in env
    assert "MISTRAL_API_KEY" not in env
    assert "SARVAM_API_KEY" not in env
    assert "STUB_MODE" not in env
    assert env["PATH"] == "/usr/bin"


def test_repository_subprocess_env_allows_explicit_overrides(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "bad")

    env = repository_subprocess_env({"CORS_ORIGINS": "[]", "CUSTOM_FLAG": "1"})

    assert env["CORS_ORIGINS"] == "[]"
    assert env["CUSTOM_FLAG"] == "1"

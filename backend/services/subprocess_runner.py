import asyncio
import json
import os
import signal
import sys
from pathlib import Path

#: The interpreter every Python subprocess runs under.
#:
#: Was the bare string `"python"` at seven call sites — the probe, A3.5, the
#: three in scoped validation and the two mutmut calls. That resolves through
#: `PATH`, which is whatever the server process inherited and is *not* the
#: interpreter ProoFix itself is running under. On a machine where ProoFix runs
#: from a virtualenv and `PATH` leads to a system Python, the probe reported
#: `No module named 'pytest'` while ProoFix's own interpreter had pytest
#: installed — and the message it printed claimed to be describing "the
#: interpreter ProoFix would run its tests with", which was the one thing it was
#: not.
#:
#: `sys.executable` is that interpreter, by definition. Using it also keeps the
#: probe and reproduction honest with each other: the probe's whole value is
#: answering the question A3.5 will later ask, and two different interpreters
#: are two different questions.
#:
#: The fallback exists because `sys.executable` is documented as possibly empty
#: when Python is embedded.
PYTHON = sys.executable or "python3"


def tool_executable(name: str) -> str:
    """Resolve a tool beside the interpreter running ProoFix."""
    sibling = Path(PYTHON).parent / name
    return str(sibling) if sibling.is_file() else name


# Target repository commands must not inherit ProoFix's service configuration.
# Real example: a target repo using pydantic-settings crashed during pytest
# collection because Railway's `CORS_ORIGINS` value from ProoFix was parsed by
# the target app as its own setting. Secrets are also stripped for safety.
_BLOCKED_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "AUDIT_STORE_RAW_PROMPTS",
    "CORS_ORIGINS",
    "ENCRYPTION_KEY",
    "ENCRYPTION_PREVIOUS_KEYS",
    "GEMINI_API_KEY",
    "GITHUB_DRY_RUN",
    "GITHUB_REPO_NAME",
    "GITHUB_REPO_OWNER",
    "GITHUB_TOKEN",
    "LLM_PROVIDER",
    "MISTRAL_API_KEY",
    "OPENAI_API_KEY",
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
    "RAILWAY_SERVICE_NAME",
    "RAILWAY_STATIC_URL",
    "RAILWAY_VOLUME_MOUNT_PATH",
    "REDIS_URL",
    "SARVAM_API_KEY",
    "STUB_MODE",
}


def repository_subprocess_env(extra: dict | None = None) -> dict[str, str]:
    """Environment for tools executed inside an arbitrary target repository."""
    clean = {key: value for key, value in os.environ.items() if key not in _BLOCKED_ENV_KEYS}
    if extra:
        clean.update({str(key): str(value) for key, value in extra.items()})
    return clean


async def run_command(
    cmd: list[str],
    cwd: str | Path | None = None,
    timeout: int = 120,
    env: dict | None = None,
) -> tuple[int, str, str]:
    process_env = repository_subprocess_env(env)
    executable = tool_executable(cmd[0]) if cmd else ""
    if executable != (cmd[0] if cmd else ""):
        cmd = [executable, *cmd[1:]]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
            start_new_session=(os.name == "posix"),
        )
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    communication = asyncio.create_task(proc.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        # Mutation tools fork workers. Killing only the parent leaves workers
        # executing against a workspace that may already have been removed.
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        await communication
        if isinstance(exc, asyncio.CancelledError):
            raise
        return -1, "", "timeout"
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


def parse_json_safe(text: str) -> dict | list:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return {}

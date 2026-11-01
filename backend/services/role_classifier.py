"""Three-stage semantic role classification for A1."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from backend.config import Settings
from backend.models.sig import FileRole
from backend.services.python_ast_parser import ParsedModule

RoleSource = Literal["filename", "ast", "llm"]

DEFAULT_ALWAYS_LLM_FILENAMES: frozenset[str] = frozenset(
    {
        "engine.py",
        "core.py",
        "handler.py",
        "processor.py",
        "manager.py",
        "service.py",
        "common.py",
        "base.py",
        "internal.py",
        "framework.py",
        "utils.py",
    }
)

VALID_ROLES: frozenset[str] = frozenset(
    {
        "auth-boundary",
        "data-access",
        "public-api",
        "config-surface",
        "test-only",
        "internal-util",
    }
)


class RolePrediction(BaseModel):
    role: FileRole
    confidence: float
    role_source: RoleSource


def always_llm_filenames(settings: Settings) -> frozenset[str]:
    if settings.always_llm_filenames.strip():
        return frozenset(
            name.strip().lower()
            for name in settings.always_llm_filenames.split(",")
            if name.strip()
        )
    return DEFAULT_ALWAYS_LLM_FILENAMES


def is_ambiguous_basename(path: str, settings: Settings) -> bool:
    return Path(path).name.lower() in always_llm_filenames(settings)


def accept_local_prediction(prediction: RolePrediction, path: str, settings: Settings) -> bool:
    if is_ambiguous_basename(path, settings):
        return prediction.confidence >= settings.role_high_confidence_threshold
    return prediction.confidence >= settings.role_confidence_threshold


def classify_file_role(
    file_path: Path | str,
    parsed: ParsedModule,
    *,
    settings: Settings,
) -> RolePrediction:
    rel = str(file_path).replace("\\", "/")
    lower = rel.lower()

    if "test" in lower:
        return RolePrediction(role="test-only", confidence=1.0, role_source="filename")

    ambiguous = is_ambiguous_basename(rel, settings)

    if not ambiguous:
        stage1 = _stage1_filename(rel)
        if stage1 is not None:
            return stage1

    stage2 = _stage2_ast(parsed)
    if stage2 is not None:
        return stage2

    return RolePrediction(role="internal-util", confidence=0.4, role_source="ast")


def _stage1_filename(path: str) -> RolePrediction | None:
    filename = Path(path).name.lower()
    parent = Path(path).parent.as_posix().lower()

    if any(k in filename for k in ("auth",)) or "middleware" in filename or "middleware" in parent:
        return RolePrediction(role="auth-boundary", confidence=1.0, role_source="filename")
    if filename.startswith("api") or "route" in filename:
        return RolePrediction(role="public-api", confidence=1.0, role_source="filename")
    if any(k in filename for k in ("database", "models", "model.py")) or filename == "db.py":
        return RolePrediction(role="data-access", confidence=1.0, role_source="filename")
    if "config" in filename or "settings" in filename:
        return RolePrediction(role="config-surface", confidence=1.0, role_source="filename")
    return None


def _stage2_ast(parsed: ParsedModule) -> RolePrediction | None:
    signals: list[tuple[FileRole, float]] = []
    blob = " ".join(
        [
            " ".join(parsed.imports),
            " ".join(parsed.functions),
            " ".join(parsed.classes),
            " ".join(parsed.decorators),
            " ".join(parsed.bases),
            " ".join(parsed.exported_symbols),
            parsed.docstring or "",
        ]
    ).lower()

    def has(*terms: str) -> bool:
        return any(t in blob for t in terms)

    if has("jwt", "oauth", "validate_token", "adminmiddleware", "bearer", "auth"):
        signals.append(("auth-boundary", 0.85))
    if has("fastapi", "router", "blueprint", "flask", "endpoint", "route"):
        signals.append(("public-api", 0.8))
    if has("sqlite", "sqlalchemy", "database", "databasesession", "execute", "orm"):
        signals.append(("data-access", 0.8))
    if has("environ", "getenv", "secret", "api_key", "settings"):
        signals.append(("config-surface", 0.75))
    if has("pickle", "subprocess", "eval"):
        signals.append(("internal-util", 0.65))

    if not signals:
        return None

    role, score = max(signals, key=lambda x: x[1])
    return RolePrediction(role=role, confidence=min(0.9, score), role_source="ast")


def coerce_file_role(value: str) -> FileRole:
    if value in VALID_ROLES:
        return value  # type: ignore[return-value]
    return "internal-util"

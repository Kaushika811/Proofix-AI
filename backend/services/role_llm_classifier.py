"""Batch LLM role classification for ambiguous A1 files."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from backend.config import Settings
from backend.models.sig import FileRole
from backend.services.llm import LLMService
from backend.services.python_ast_parser import ParsedModule
from backend.services.role_classifier import RolePrediction, coerce_file_role

logger = logging.getLogger(__name__)


class AmbiguousFileContext(BaseModel):
    path: str
    imports: list[str] = Field(default_factory=list)
    exported_classes: list[str] = Field(default_factory=list)
    exported_functions: list[str] = Field(default_factory=list)
    module_docstring: str | None = None
    top_level_comments: list[str] = Field(default_factory=list)


class BatchRoleFileResult(BaseModel):
    path: str
    role: FileRole
    confidence: float


class BatchRoleLLMOutput(BaseModel):
    files: list[BatchRoleFileResult]


def build_ambiguous_context(path: str, parsed: ParsedModule) -> AmbiguousFileContext:
    return AmbiguousFileContext(
        path=path,
        imports=parsed.imports,
        exported_classes=parsed.classes,
        exported_functions=parsed.functions,
        module_docstring=parsed.docstring,
        top_level_comments=parsed.top_level_comments,
    )


def _build_batch_prompt(contexts: list[AmbiguousFileContext]) -> str:
    lines = ["Classify each file's semantic role using metadata only.\n"]
    for ctx in contexts:
        lines.append(f"## File: {ctx.path}")
        lines.append(f"imports: {ctx.imports}")
        lines.append(f"classes: {ctx.exported_classes}")
        lines.append(f"functions: {ctx.exported_functions}")
        if ctx.module_docstring:
            lines.append(f"docstring: {ctx.module_docstring[:500]}")
        if ctx.top_level_comments:
            lines.append(f"comments: {ctx.top_level_comments[:5]}")
        lines.append("")
    return "\n".join(lines)


async def classify_ambiguous_batch(
    queue: list[tuple[str, ParsedModule]],
    *,
    settings: Settings,
    ast_predictions: dict[str, RolePrediction],
    run_id: str = "",
    agent_id: str = "A1",
) -> dict[str, RolePrediction]:
    if not queue:
        return {}

    contexts = [build_ambiguous_context(path, parsed) for path, parsed in queue]

    if settings.stub_mode or not settings.llm_configured():
        return _fallback_predictions(queue, ast_predictions)

    llm = LLMService(settings, run_id=run_id, agent_id=agent_id)
    prompt = _build_batch_prompt(contexts)
    system = (
        "You are a security-focused code analysis assistant. "
        "Classify semantic responsibility only. "
        "Allowed roles: auth-boundary, data-access, public-api, config-surface, test-only, internal-util. "
        "Respond with valid JSON only. No explanations."
    )

    try:
        result = await llm.structured(prompt, BatchRoleLLMOutput, system=system)
        out: dict[str, RolePrediction] = {}
        by_path = {item.path: item for item in result.files}
        for path, _parsed in queue:
            item = by_path.get(path)
            if item:
                out[path] = RolePrediction(
                    role=coerce_file_role(item.role),
                    confidence=item.confidence,
                    role_source="llm",
                )
            else:
                out[path] = _fallback_single(path, ast_predictions.get(path))
        return out
    except Exception as exc:
        logger.warning(
            "a1_llm_classification_failed",
            extra={"error": str(exc), "ambiguous_count": len(queue)},
        )
        return _fallback_predictions(queue, ast_predictions)


def _fallback_predictions(
    queue: list[tuple[str, ParsedModule]],
    ast_predictions: dict[str, RolePrediction],
) -> dict[str, RolePrediction]:
    return {path: _fallback_single(path, ast_predictions.get(path)) for path, _ in queue}


def _fallback_single(path: str, prior: RolePrediction | None) -> RolePrediction:
    if prior:
        return RolePrediction(
            role=prior.role,
            confidence=0.50,
            role_source="ast",
        )
    return RolePrediction(role="internal-util", confidence=0.50, role_source="ast")

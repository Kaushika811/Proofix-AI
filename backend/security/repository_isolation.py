"""Confine every run to a workspace it cannot escape.

The pipeline already clones into a temp directory. That is containment by
convention: nothing stops a path derived from a traceback, a citation, or an LLM
response from resolving outside it. This module makes containment checkable, and
gives every caller one function to ask "may I read this?".

Four escapes are closed:

**Absolute paths.** `/etc/passwd` arriving as a "file to read" resolves outside
the workspace and is refused.

**Traversal.** `../../secrets.env` is refused *after* normalization, not before —
checking the literal string for `..` misses `a/b/../../../etc` and rejects the
legitimate `a/../b`.

**Symlinks.** A link inside the workspace pointing out of it is the subtle one:
the path is inside, the target is not. Resolution follows links, so the check is
on the real path.

**Host disclosure.** Absolute workspace paths leak the operating system, the
user account and the deployment layout. `relativize` converts them back to
repo-relative form for anything heading toward a prompt.

Read-only enforcement is advisory rather than kernel-level: the pipeline must
write to the clone (A7 patches files), so the guarantee offered is *containment*,
not immutability. That distinction is stated here rather than implied — a
`chmod -R a-w` would break the repair, and claiming read-only while permitting
writes would be worse than claiming nothing.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Paths that must never be read regardless of where they resolve, because they
# are credential stores rather than source.
FORBIDDEN_BASENAMES = frozenset({
    ".env", ".git-credentials", ".netrc", "_netrc", "id_rsa", "id_ed25519",
    "id_ecdsa", "id_dsa", "credentials", ".npmrc", ".pypirc", ".dockercfg",
})

FORBIDDEN_DIRECTORIES = frozenset({".ssh", ".aws", ".kube", ".gnupg", ".docker"})

# Host path prefixes whose appearance in outbound text discloses deployment
# layout and the operating user. Public: the privacy guard applies it too.
HOST_PATH_RE = re.compile(
    r"(?:/Users/[^/\s]+|/home/[^/\s]+|/root|/private/var/folders/[^\s]*?|"
    r"C:\\Users\\[^\\\s]+|/tmp/[A-Za-z0-9_]+)"
)


class IsolationViolation(RuntimeError):
    """Raised when a path escapes the workspace or names forbidden content."""


@dataclass
class WorkspaceGuard:
    """A resolved workspace root plus the rules for staying inside it."""

    root: Path
    allow_writes: bool = True
    violations: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()

    # -- containment -----------------------------------------------------

    def contains(self, candidate: Path | str) -> bool:
        """True when a path resolves inside the workspace, symlinks followed."""
        try:
            resolved = self._resolve(candidate)
        except (OSError, RuntimeError, ValueError):
            return False
        return resolved == self.root or self.root in resolved.parents

    def _resolve(self, candidate: Path | str) -> Path:
        """Resolve relative to the workspace, following symlinks.

        `strict=False` so a path that does not exist yet still normalizes — A7
        may legitimately create a file, and refusing to reason about paths until
        they exist would make the guard unusable for writes.
        """
        path = Path(candidate)
        if not path.is_absolute():
            path = self.root / path
        return path.resolve(strict=False)

    def is_forbidden(self, candidate: Path | str) -> str:
        """Reason a path is forbidden by name, or "" when it is not."""
        path = Path(candidate)
        if path.name in FORBIDDEN_BASENAMES:
            return f"'{path.name}' is a credential file"
        for part in path.parts:
            if part in FORBIDDEN_DIRECTORIES:
                return f"'{part}/' is a credential directory"
        return ""

    def check(self, candidate: Path | str, *, for_write: bool = False) -> Path:
        """Resolve and validate a path, or raise `IsolationViolation`."""
        raw = str(candidate)

        forbidden = self.is_forbidden(candidate)
        if forbidden:
            self.violations.append(f"forbidden path refused: {forbidden}")
            raise IsolationViolation(f"refused {raw!r}: {forbidden}")

        try:
            resolved = self._resolve(candidate)
        except (OSError, RuntimeError, ValueError) as exc:
            self.violations.append(f"unresolvable path refused: {raw}")
            raise IsolationViolation(f"refused {raw!r}: cannot be resolved") from exc

        if not self.contains(resolved):
            kind = "symlink escape" if Path(raw).is_symlink() else "path escape"
            self.violations.append(f"{kind} refused: {self.relativize(raw)}")
            raise IsolationViolation(
                f"refused {self.relativize(raw)!r}: resolves outside the workspace"
            )

        if for_write and not self.allow_writes:
            self.violations.append("write refused: workspace is read-only")
            raise IsolationViolation(f"refused write to {self.relativize(raw)!r}: read-only workspace")

        return resolved

    def safe_read(self, candidate: Path | str, encoding: str = "utf-8") -> str:
        """Read a file only if it passes every containment rule."""
        return self.check(candidate).read_text(encoding=encoding)

    def is_safe(self, candidate: Path | str, *, for_write: bool = False) -> bool:
        """Non-raising form of `check`, for filtering candidate lists."""
        try:
            self.check(candidate, for_write=for_write)
            return True
        except IsolationViolation:
            return False

    # -- disclosure ------------------------------------------------------

    def relativize(self, candidate: Path | str) -> str:
        """Repo-relative form, so no host path reaches a prompt or a log."""
        text = str(candidate)
        try:
            resolved = self._resolve(candidate)
            if resolved == self.root:
                return "."
            if self.root in resolved.parents:
                return str(resolved.relative_to(self.root)).replace("\\", "/")
        except (OSError, RuntimeError, ValueError):
            pass
        return HOST_PATH_RE.sub("<PATH>", text)

    def scrub_paths(self, text: str) -> str:
        """Replace workspace and host paths in free text — tracebacks, mostly."""
        if not text:
            return text
        result = text.replace(str(self.root) + os.sep, "").replace(str(self.root), ".")
        return HOST_PATH_RE.sub("<PATH>", result)

    # -- inspection ------------------------------------------------------

    def list_symlink_escapes(self) -> list[str]:
        """Every symlink in the workspace whose target leaves it.

        Reported rather than removed: deleting a developer's symlink would
        change the repository under repair. The pipeline refuses to read
        through them, which is the control that matters.
        """
        escapes: list[str] = []
        if not self.root.is_dir():
            return escapes
        for path in sorted(self.root.rglob("*")):
            try:
                if path.is_symlink() and not self.contains(path):
                    escapes.append(str(path.relative_to(self.root)).replace("\\", "/"))
            except (OSError, ValueError):
                continue
        return escapes

    def status(self) -> dict:
        return {
            "workspace": self.root.name,
            "allow_writes": self.allow_writes,
            "violations": list(self.violations),
            "violation_count": len(self.violations),
            "symlink_escapes": self.list_symlink_escapes(),
        }


def create_workspace(prefix: str = "proofix_ws_") -> WorkspaceGuard:
    """A fresh isolated workspace directory."""
    return WorkspaceGuard(root=Path(tempfile.mkdtemp(prefix=prefix)))


def guard_for(repo_path: Path | str, allow_writes: bool = True) -> WorkspaceGuard:
    """Guard around an existing clone."""
    return WorkspaceGuard(root=Path(repo_path), allow_writes=allow_writes)


def scrub_environment(text: str) -> str:
    """Remove environment-variable dumps from text heading outward."""
    if not text:
        return text
    return re.sub(
        r"(?im)^\s*(PATH|HOME|USER|USERNAME|LOGNAME|PWD|SHELL|LD_LIBRARY_PATH|"
        r"VIRTUAL_ENV|CONDA_PREFIX|AWS_PROFILE|GOPATH)\s*=\s*\S+.*$",
        r"\1=<REDACTED_ENV>",
        text,
    )

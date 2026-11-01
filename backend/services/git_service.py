import hashlib
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from git import Repo


#: Marks a directory as one this process created and may therefore delete.
#: `discard_clone` refuses to remove anything not carrying it, so a
#: `repo_clone_path` that was never a clone — a misconfiguration pointing at the
#: user's own checkout — cannot be deleted by cleanup.
CLONE_PREFIX = "proofix_"


def clone_or_copy_repo(repo_path: str) -> str:
    """Clone repo to temp dir or copy local path."""
    path = Path(repo_path)
    tmp = tempfile.mkdtemp(prefix=CLONE_PREFIX)
    if path.exists() and path.is_dir():
        shutil.copytree(path, tmp, dirs_exist_ok=True)
        return tmp
    Repo.clone_from(repo_path, tmp)
    return tmp


def discard_clone(clone_path: str | None) -> bool:
    """Delete a working copy this process created. Returns whether it did.

    Every run copied an entire repository into a temp directory and left it
    there, so disk grew without bound with run count × repository size (B-B14).

    Deleting directories from a background job earns paranoia, so this refuses
    unless the path is inside the system temp directory *and* its own name
    carries `CLONE_PREFIX`. The case that matters is a misconfigured
    `repo_clone_path` pointing at a real checkout: that lives outside the temp
    root and is left alone regardless of what it is called.

    What this deliberately does *not* claim is that the directory was created by
    this process. A `proofix_`-prefixed directory sitting in the system temp
    root is indistinguishable from one of ours, and treating it as ours is the
    right trade: a registry of paths we created would be process-scoped, so it
    would silently stop cleaning up after a restart — which is exactly when the
    leak matters most.

    Never raises: a run that finished must not be reported as failed because its
    temp directory could not be removed.
    """
    if not clone_path:
        return False

    try:
        path = Path(clone_path).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return False

    if not path.is_dir():
        return False
    if not path.name.startswith(CLONE_PREFIX):
        return False
    if not path.is_relative_to(temp_root):
        return False

    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return True


def get_churn_weights(repo_path: Path, days: int = 90) -> dict[str, float]:
    """Parse git log for bug-fix commit density per file."""
    weights: dict[str, int] = {}
    try:
        repo = Repo(repo_path)
        since = datetime.now() - timedelta(days=days)
        for commit in repo.iter_commits(since=since.isoformat()):
            msg = (commit.message or "").lower()
            if not any(kw in msg for kw in ("fix", "bug", "patch", "hotfix")):
                continue
            for item in commit.stats.files:
                weights[item] = weights.get(item, 0) + 1
    except Exception:
        pass
    if not weights:
        return {}
    max_count = max(weights.values())
    return {f: min(1.0, c / max_count) for f, c in weights.items()}


def get_head_sha(repo_path: Path) -> str:
    """Return full HEAD commit SHA for the repo clone."""
    try:
        repo = Repo(repo_path, search_parent_directories=True)
        return repo.head.commit.hexsha
    except Exception:
        return ""


def get_worktree_diff_hash(repo_path: Path) -> str:
    """Hash of unstaged + staged changes for cache invalidation on uncommitted edits."""
    try:
        repo = Repo(repo_path, search_parent_directories=True)
        unstaged = repo.git.diff("HEAD") or ""
        staged = repo.git.diff("--cached") or ""
        combined = f"{unstaged}\n{staged}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def get_style_exemplar(repo_path: Path, file_path: str, max_commits: int = 3) -> tuple[str | None, str]:
    """Get recent commit hash and diff for style exemplar."""
    try:
        repo = Repo(repo_path, search_parent_directories=True)
        commits = list(repo.iter_commits(paths=file_path, max_count=max_commits))
        if not commits:
            return None, ""
        commit = commits[0]
        diff = repo.git.show(commit.hexsha, "--", file_path)
        return commit.hexsha, diff
    except Exception:
        return None, ""

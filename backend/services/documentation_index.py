"""Index prose documentation into structured, queryable entries.

Sources: README files, everything under `docs/`, root-level markdown, plus the
docstrings and top-level comments the AST parser already collected. Nothing here
embeds, summarises, or infers meaning — it extracts the identifiers and paths a
document *names*, and links them to repository files.

The useful output is `by_module`: given a file, which documents talk about it.
A5.5 uses that as a weak tie-break signal, on the reasoning that a documented
module is one a human has thought about, so showing its contract to the patch
model costs little and occasionally helps.

Deliberately not done here: relevance scoring by term frequency, stemming, or
similarity. Those are embedding-shaped problems and this layer has none.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from backend.models.repository_graph import DocumentationIndex, DocumentEntry
from backend.services.python_ast_parser import ParsedModule

# Directories swept for markdown, relative to the repository root.
DOC_DIRECTORIES = ("docs", "doc", "documentation")

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt"})

# Root-level filenames indexed even when they sit outside a docs directory.
ROOT_DOC_STEMS = frozenset({
    "readme",
    "architecture",
    "design",
    "contributing",
    "changelog",
    "security",
    "api",
})

# Caps. Documentation is a tie-break signal, not evidence; it must never
# dominate the index build's wall-clock or memory budget.
MAX_DOCUMENTS = 200
MAX_FILE_BYTES = 400_000
MAX_TOPICS_PER_DOC = 25
MAX_ENTITIES_PER_DOC = 60
EXCERPT_CHARS = 400

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_RST_HEADING = re.compile(r"^(.+)\n[=\-~^\"']{3,}\s*$", re.MULTILINE)
_INLINE_CODE = re.compile(r"`([^`\n]{1,120})`")
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_DOTTED_PATH = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z_][A-Za-z0-9_]*)+(?:\.py)?)\b")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

# Words that appear in every codebase's prose and identify nothing.
_STOP_ENTITIES = frozenset({
    "true", "false", "none", "null", "python", "json", "yaml", "http", "https",
    "get", "post", "put", "delete", "patch", "bash", "sh", "pip", "npm", "git",
    "note", "warning", "example", "usage", "install", "run", "test", "tests",
})


def _normalize_topic(text: str) -> str:
    """Headings become lowercase topics with punctuation stripped."""
    cleaned = re.sub(r"[^A-Za-z0-9 _\-/.]+", " ", text).strip().lower()
    return re.sub(r"\s+", " ", cleaned)[:80]


def discover_documentation_files(repo_path: Path) -> list[str]:
    """Repo-relative documentation paths, deterministic in order."""
    repo = repo_path.resolve()
    found: list[str] = []
    seen: set[str] = set()

    if repo.is_dir():
        for child in sorted(repo.iterdir()):
            if not child.is_file() or child.suffix.lower() not in MARKDOWN_SUFFIXES:
                continue
            if child.stem.lower() in ROOT_DOC_STEMS or child.suffix.lower() in (".md", ".markdown"):
                if child.name not in seen:
                    seen.add(child.name)
                    found.append(child.name)

    for directory in DOC_DIRECTORIES:
        doc_root = repo / directory
        if not doc_root.is_dir():
            continue
        for path in sorted(doc_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in MARKDOWN_SUFFIXES:
                continue
            try:
                rel = str(path.relative_to(repo)).replace("\\", "/")
            except ValueError:
                continue
            if rel not in seen:
                seen.add(rel)
                found.append(rel)

    return found[:MAX_DOCUMENTS]


def extract_topics(text: str) -> list[str]:
    """Headings, in document order, as normalized topic strings."""
    topics: list[str] = []
    for _hashes, heading in _HEADING.findall(text):
        topic = _normalize_topic(heading)
        if topic and topic not in topics:
            topics.append(topic)
    for heading in _RST_HEADING.findall(text):
        topic = _normalize_topic(heading)
        if topic and topic not in topics:
            topics.append(topic)
    return topics[:MAX_TOPICS_PER_DOC]


def extract_entities(text: str) -> list[str]:
    """Identifier-shaped tokens the document names explicitly.

    Only inline-code spans count. Prose capitalisation is far too noisy — a
    sentence starting with "Repository" is not a reference to a class.
    """
    entities: list[str] = []
    for token in _INLINE_CODE.findall(text):
        candidate = token.strip().rstrip("()")
        if not candidate or candidate.lower() in _STOP_ENTITIES:
            continue
        if _IDENTIFIER.match(candidate) or "/" in candidate:
            if candidate not in entities:
                entities.append(candidate)
    return entities[:MAX_ENTITIES_PER_DOC]


def extract_referenced_functions(text: str) -> list[str]:
    """Names written as calls, inside code spans or fenced blocks only."""
    names: list[str] = []
    regions = _INLINE_CODE.findall(text) + _FENCE.findall(text)
    for region in regions:
        for name in _CALL.findall(region):
            if name.lower() in _STOP_ENTITIES or name in names:
                continue
            names.append(name)
    return names[:MAX_ENTITIES_PER_DOC]


def _module_lookup(files: list[str]) -> dict[str, str]:
    """Every way a document might name a file -> the repo-relative path."""
    lookup: dict[str, str] = {}
    ambiguous: set[str] = set()

    for rel in files:
        posix = rel.replace("\\", "/")
        lookup.setdefault(posix, posix)

        stem = posix[:-3] if posix.endswith(".py") else posix
        lookup.setdefault(stem.replace("/", "."), posix)

        name = PurePosixPath(posix).name
        base = name[:-3] if name.endswith(".py") else name
        for alias in (name, base):
            if alias in lookup and lookup[alias] != posix:
                ambiguous.add(alias)
            else:
                lookup.setdefault(alias, posix)

    # A bare `auth` naming three different files identifies none of them.
    for alias in ambiguous:
        lookup.pop(alias, None)

    return lookup


def extract_linked_modules(text: str, lookup: dict[str, str]) -> list[str]:
    """Repository files a document names, via inline code or path-shaped tokens."""
    linked: list[str] = []

    candidates: list[str] = []
    for token in _INLINE_CODE.findall(text):
        candidates.append(token.strip().rstrip("()"))
    for match in _DOTTED_PATH.findall(text):
        candidates.append(match)

    for candidate in candidates:
        cleaned = candidate.strip().strip("`").lstrip("./")
        resolved = lookup.get(cleaned)
        if resolved and resolved not in linked:
            linked.append(resolved)

    return linked


def _excerpt(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:EXCERPT_CHARS]
    return text.strip()[:EXCERPT_CHARS]


def _title(rel_path: str, text: str) -> str:
    match = _HEADING.search(text)
    if match:
        return match.group(2).strip()[:120]
    return PurePosixPath(rel_path).name


def build_document_entry(rel_path: str, text: str, lookup: dict[str, str]) -> DocumentEntry:
    """Parse one markdown/rst document into an entry."""
    kind = "readme" if PurePosixPath(rel_path).stem.lower() == "readme" else "markdown"
    return DocumentEntry(
        path=rel_path,
        kind=kind,  # type: ignore[arg-type]
        title=_title(rel_path, text),
        topics=extract_topics(text),
        entities=extract_entities(text),
        linked_modules=extract_linked_modules(text, lookup),
        referenced_functions=extract_referenced_functions(text),
        excerpt=_excerpt(text),
    )


def build_docstring_entry(rel_path: str, parsed: ParsedModule) -> DocumentEntry | None:
    """Collapse a module's docstrings into one entry addressed to that file.

    One entry per file rather than per symbol: the consumer asks "is this module
    documented", never "which paragraph mentions it".
    """
    module_doc = (parsed.docstring or "").strip()
    class_docs = [(c.name, (c.docstring or "").strip()) for c in parsed.class_spans]
    fn_docs = [(f.qualname or f.name, (f.docstring or "").strip()) for f in parsed.function_spans]

    documented_classes = [name for name, doc in class_docs if doc]
    documented_functions = [name for name, doc in fn_docs if doc]

    if not module_doc and not documented_classes and not documented_functions:
        return None

    topics = extract_topics(module_doc) if module_doc else []
    if not topics and module_doc:
        topics = [_normalize_topic(module_doc.splitlines()[0])]

    return DocumentEntry(
        path=rel_path,
        kind="docstring",
        title=module_doc.splitlines()[0][:120] if module_doc else PurePosixPath(rel_path).name,
        topics=[t for t in topics if t][:MAX_TOPICS_PER_DOC],
        entities=(documented_classes + documented_functions)[:MAX_ENTITIES_PER_DOC],
        linked_modules=[rel_path],
        referenced_functions=documented_functions[:MAX_ENTITIES_PER_DOC],
        excerpt=module_doc[:EXCERPT_CHARS],
    )


def build_comment_entry(rel_path: str, parsed: ParsedModule) -> DocumentEntry | None:
    """Top-level comments, which often carry the intent a docstring omits."""
    comments = [c for c in parsed.top_level_comments if c]
    if not comments:
        return None
    joined = "\n".join(comments)
    return DocumentEntry(
        path=rel_path,
        kind="comment",
        title=comments[0][:120],
        topics=[],
        entities=extract_entities(joined),
        linked_modules=[rel_path],
        referenced_functions=[],
        excerpt=joined[:EXCERPT_CHARS],
    )


def build_documentation_index(
    repo_path: Path,
    files: list[str] | None = None,
    parsed_modules: dict[str, ParsedModule] | None = None,
) -> DocumentationIndex:
    """Index prose and docstrings into a `DocumentationIndex`.

    `files` scopes module resolution to the repository's indexed Python files, so
    a document mentioning `requests` links nothing while one mentioning
    `services/auth.py` links that file.
    """
    repo = repo_path.resolve()
    index = DocumentationIndex()
    lookup = _module_lookup(list(files or []))

    for rel in discover_documentation_files(repo):
        full = repo / rel
        try:
            if full.stat().st_size > MAX_FILE_BYTES:
                continue
            text = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        index.entries.append(build_document_entry(rel, text, lookup))

    for rel, parsed in sorted((parsed_modules or {}).items()):
        entry = build_docstring_entry(rel, parsed)
        if entry is not None:
            index.entries.append(entry)
        comment_entry = build_comment_entry(rel, parsed)
        if comment_entry is not None:
            index.entries.append(comment_entry)

    _build_reverse_index(index)
    return index


def _build_reverse_index(index: DocumentationIndex) -> None:
    for position, entry in enumerate(index.entries):
        for module in entry.linked_modules:
            index.by_module.setdefault(module, []).append(position)
        for topic in entry.topics:
            index.topics[topic] = index.topics.get(topic, 0) + 1


def documentation_signal(index: DocumentationIndex, file: str) -> float:
    """0..1 — how well documented a file is. A tie-break, never evidence."""
    return index.relevance_for(file)

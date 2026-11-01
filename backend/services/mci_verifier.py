import re
from difflib import unified_diff
from pathlib import PurePosixPath

from backend.services.path_resolution import normalize_path_token, paths_equivalent

FILE_PATH_RE = re.compile(r"[\w/\\.-]+\.py", re.IGNORECASE)
DIFF_FILE_HEADER_RE = re.compile(r"^(?:---|\+\+\+) [ab]/(.+)$", re.MULTILINE)


def strip_code_blocks(text: str) -> str:
    return re.sub(r"```[\s\S]*?```", " ", text)


def is_plausible_path_entity(entity: str) -> bool:
    token = entity.strip().strip("`\"'")
    if not token or "\n" in token or len(token) > 260:
        return False
    return token.lower().endswith(".py")


def generate_diff_from_patches(patches: list[dict]) -> str:
    lines = []
    for p in patches:
        orig_lines = p.get("original", "").splitlines(keepends=True)
        new_lines = p.get("patched", "").splitlines(keepends=True)
        diff = unified_diff(
            orig_lines,
            new_lines,
            fromfile=f"a/{p['file']}",
            tofile=f"b/{p['file']}",
        )
        lines.extend(diff)
    return "".join(lines)


def file_paths_equivalent(desc_path: str, diff_path: str) -> bool:
    """Return True when two path references denote the same file.

    Thin alias over the shared path service, kept as the name MCI code and its
    tests already use.
    """
    return paths_equivalent(desc_path, diff_path)


def extract_diff_file_paths(diff_text: str) -> set[str]:
    return {match.group(1) for match in DIFF_FILE_HEADER_RE.finditer(diff_text)}


def extract_description_file_paths(description: str) -> set[str]:
    prose = strip_code_blocks(description)
    paths: set[str] = set()
    for match in FILE_PATH_RE.finditer(prose):
        paths.add(match.group(0))
    for match in re.finditer(r"`([^`\n]+)`", prose):
        token = match.group(1).strip()
        if is_plausible_path_entity(token):
            paths.add(token)
    return paths


def extract_diff_entities(diff_text: str) -> set[str]:
    entities: set[str] = set()
    for path in extract_diff_file_paths(diff_text):
        entities.add(normalize_path_token(path))
        entities.add(PurePosixPath(normalize_path_token(path)).name)
    for match in re.finditer(r"def\s+(\w+)", diff_text):
        entities.add(match.group(1))
    for match in re.finditer(r"class\s+(\w+)", diff_text):
        entities.add(match.group(1))
    return entities


def extract_description_entities(description: str) -> set[str]:
    prose = strip_code_blocks(description)
    entities: set[str] = set()
    try:
        import spacy

        nlp = spacy.load("en_core_web_sm")
        doc = nlp(prose)
        entities.update(
            ent.text
            for ent in doc.ents
            if len(ent.text) > 2 and "\n" not in ent.text and len(ent.text) <= 260
        )
    except Exception:
        pass

    entities.update(extract_description_file_paths(prose))
    for match in re.finditer(r"`([^`\n]+)`", prose):
        token = match.group(1).strip()
        if token and not is_plausible_path_entity(token):
            entities.add(token)
    return entities


def find_phantom_file_references(description_files: set[str], diff_files: set[str]) -> set[str]:
    phantoms: set[str] = set()
    for desc_file in description_files:
        if not any(file_paths_equivalent(desc_file, diff_file) for diff_file in diff_files):
            phantoms.add(desc_file)
    return phantoms


def generate_description_from_diff(diff_text: str) -> str:
    if not diff_text.strip():
        return "No changes detected."
    files = set(re.findall(r"^[+-]{3} b/(.+)$", diff_text, re.MULTILINE))
    lines = ["## Changes", ""]
    for f in sorted(files):
        lines.append(f"- Modified `{f}`")
    lines.append("")
    lines.append("## Diff Summary")
    lines.append("```diff")
    lines.append(diff_text[:3000])
    if len(diff_text) > 3000:
        lines.append("... (truncated)")
    lines.append("```")
    return "\n".join(lines)


def verify_mci(description: str, diff_text: str) -> tuple[bool, set[str]]:
    """Cross-check description entities against diff. Returns (fidelity_ok, phantoms)."""
    desc_entities = extract_description_entities(description)
    diff_entities = extract_diff_entities(diff_text)
    diff_files = extract_diff_file_paths(diff_text)

    desc_files = {entity for entity in desc_entities if is_plausible_path_entity(entity)}
    desc_non_files = desc_entities - desc_files

    file_phantoms = find_phantom_file_references(desc_files, diff_files)
    non_file_phantoms = {
        entity
        for entity in desc_non_files
        if entity not in diff_entities and not entity.startswith("CWE")
    }

    phantoms = file_phantoms | non_file_phantoms
    return len(phantoms) == 0, phantoms

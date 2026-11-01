"""Learn a repository's coding conventions by counting, never by inference.

Every property is decided by tallying what the code already does and taking the
dominant form. `function_naming = "snake_case"` means "213 of 254 function names
matched the snake_case shape", and the distribution is kept so a reviewer can
see the 41 that did not.

**No LLM, by construction** — this module imports nothing that could call one.
The spec is explicit that style must never be inferred by a model, and there is
a good reason beyond determinism: a model asked "what style is this?" answers
from its priors as much as from the sample, so a repository that consistently
does something unusual would be told to do the common thing instead.

Two decisions worth stating:

**A dominant form needs a majority *and* a sample.** One function named
`doThing` in a repository with three functions is not a camelCase convention.
`MIN_OBSERVATIONS` and `DOMINANCE_THRESHOLD` both have to be met, otherwise the
property reports `mixed` — which is itself useful information, since it tells
the patch generator not to impose a convention the repository does not have.

**Test files are excluded from style learning.** Test naming conventions differ
systematically from production ones (`test_should_reject_expired_token` is not
a naming failure), and including them shifts every distribution.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from backend.models.learning import StyleObservation, StyleProfile, StyleValue
from backend.services.python_ast_parser import ParsedModule
from backend.services.repository_graph import is_test_file

# A convention needs this share of observations to be called dominant.
DOMINANCE_THRESHOLD = 0.6

# ...and at least this many observations for the share to mean anything.
MIN_OBSERVATIONS = 5

# Files sampled. Style is repository-wide; reading everything is wasted work.
MAX_FILES_SAMPLED = 300

_SNAKE = re.compile(r"^[a-z_][a-z0-9_]*$")
_SCREAMING = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CAMEL = re.compile(r"^[a-z]+(?:[A-Z][a-z0-9]*)+$")
_PASCAL = re.compile(r"^(?:[A-Z][a-z0-9]*)+$")

_SINGLE_QUOTED = re.compile(r"(?<![\"\\])'(?:[^'\\\n]|\\.)*'")
_DOUBLE_QUOTED = re.compile(r'(?<![\'\\])"(?:[^"\\\n]|\\.)*"')

_INDENT = re.compile(r"^( +)\S", re.MULTILINE)


def classify_name(name: str) -> StyleValue:
    """Which naming shape a single identifier matches."""
    if not name or name.startswith("__"):
        return "unknown"
    stripped = name.lstrip("_")
    if not stripped:
        return "unknown"
    if _SCREAMING.match(stripped):
        return "SCREAMING_SNAKE"
    if _CAMEL.match(stripped):
        return "camelCase"
    if _PASCAL.match(stripped):
        return "PascalCase"
    if _SNAKE.match(stripped):
        return "snake_case"
    return "unknown"


# Properties where one observation is a whole file rather than one symbol.
# A repository's indentation or logging library is decided by four files far
# more strongly than its naming convention is decided by four function names,
# so these need a lower bar to be called.
FILE_LEVEL_MIN_OBSERVATIONS = 2


def _dominant(
    counter: Counter,
    default: str = "unknown",
    min_observations: int = MIN_OBSERVATIONS,
) -> tuple[str, float, int]:
    """Dominant value, its share, and the sample size behind it."""
    total = sum(counter.values())
    if not total:
        return default, 0.0, 0
    value, count = counter.most_common(1)[0]
    share = count / total
    if total < min_observations or share < DOMINANCE_THRESHOLD:
        return "mixed", round(share, 4), total
    return value, round(share, 4), total


def detect_docstring_style(docstring: str) -> str:
    """Which docstring convention a body follows.

    Checked most-specific first: a Google-style docstring often also contains a
    colon-terminated line that a looser Sphinx test would claim.
    """
    if not docstring or not docstring.strip():
        return "none"
    text = docstring
    if re.search(r"^\s*(Args|Returns|Raises|Yields|Attributes):\s*$", text, re.MULTILINE):
        return "google"
    if re.search(r"^\s*(Parameters|Returns|Raises)\s*\n\s*-{3,}", text, re.MULTILINE):
        return "numpy"
    if re.search(r":(?:param|returns?|rtype|raises)\b", text):
        return "sphinx"
    return "plain"


class StyleLearner:
    """Accumulates observations across files, then resolves them into a profile."""

    def __init__(self, repository_id: str = ""):
        self.repository_id = repository_id
        self.functions: Counter = Counter()
        self.classes: Counter = Counter()
        self.constants: Counter = Counter()
        self.variables: Counter = Counter()
        self.quotes: Counter = Counter()
        self.docstrings: Counter = Counter()
        self.indents: Counter = Counter()
        self.logging_calls: Counter = Counter()
        self.exception_bases: Counter = Counter()
        self.import_groups: Counter = Counter()

        self.callables_seen = 0
        self.callables_documented = 0
        self.callables_annotated = 0
        self.callables_async = 0
        self.files_analyzed = 0
        self.longest_line = 0

    # -- observation -----------------------------------------------------

    def observe(self, path: str, parsed: ParsedModule, source: str = "") -> None:
        """Record every countable property of one module."""
        if is_test_file(path):
            return

        self.files_analyzed += 1

        for span in parsed.function_spans:
            if span.is_method and span.name.startswith("__"):
                continue  # dunders are named by the language, not the repository
            self.callables_seen += 1
            shape = classify_name(span.name)
            if shape != "unknown":
                self.functions[shape] += 1
            if span.docstring:
                self.callables_documented += 1
                self.docstrings[detect_docstring_style(span.docstring)] += 1
            if span.is_async:
                self.callables_async += 1

        for span in parsed.class_spans:
            shape = classify_name(span.name)
            if shape != "unknown":
                self.classes[shape] += 1
            if span.docstring:
                self.docstrings[detect_docstring_style(span.docstring)] += 1
            # Defining `class PaymentError(Exception)` *is* a custom hierarchy —
            # the builtin appears as the base precisely because the repository is
            # extending it. Classifying on the base rather than on the act of
            # defining gets this exactly backwards.
            if span.name.endswith(("Error", "Exception")) and span.bases:
                self.exception_bases["custom"] += 1

        for span in parsed.constant_spans:
            shape = classify_name(span.name)
            if shape != "unknown":
                (self.constants if shape == "SCREAMING_SNAKE" else self.variables)[shape] += 1
            if span.annotation:
                self.callables_annotated += 1

        for module in parsed.imports:
            if module in _LOGGING_LIBRARIES:
                self.logging_calls[_LOGGING_LIBRARIES[module]] += 1

        if source:
            self._observe_source(source)

    def _observe_source(self, source: str) -> None:
        """Textual properties the AST discards: quotes, indentation, line length."""
        self.quotes["single"] += len(_SINGLE_QUOTED.findall(source))
        self.quotes["double"] += len(_DOUBLE_QUOTED.findall(source))

        widths = [len(m.group(1)) for m in _INDENT.finditer(source)]
        if widths:
            # The smallest indent is one level; larger ones are multiples of it.
            self.indents[min(widths)] += 1

        for line in source.splitlines():
            self.longest_line = max(self.longest_line, len(line))

        if "print(" in source and "logging" not in source:
            self.logging_calls["print"] += 1

        # Annotated signatures are the strongest typing signal available without
        # re-walking the AST, and `->` appears in nothing else.
        self.callables_annotated += source.count("->")

        self.import_groups["grouped" if re.search(r"\nimport [^\n]+\n\n", source) else "flat"] += 1

    # -- resolution ------------------------------------------------------

    def profile(self) -> StyleProfile:
        """Resolve accumulated counts into a profile."""
        observations: list[StyleObservation] = []

        def resolve(
            name: str,
            counter: Counter,
            default: str = "unknown",
            min_observations: int = MIN_OBSERVATIONS,
        ) -> str:
            value, share, total = _dominant(counter, default, min_observations)
            if total:
                observations.append(
                    StyleObservation(
                        property=name,
                        value=value,
                        confidence=share,
                        observations=total,
                        distribution=dict(sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))),
                    )
                )
            return value

        function_naming = resolve("function_naming", self.functions)
        class_naming = resolve("class_naming", self.classes)
        constant_naming = resolve("constant_naming", self.constants)
        variable_naming = resolve("variable_naming", self.variables)
        quote_style = resolve("quote_style", self.quotes)
        docstring_style = resolve("docstring_style", self.docstrings, "none")
        logging_style = resolve("logging_style", self.logging_calls, "none", FILE_LEVEL_MIN_OBSERVATIONS)

        exception_value, _share, exception_total = _dominant(
            self.exception_bases, min_observations=FILE_LEVEL_MIN_OBSERVATIONS
        )
        exception_style = {
            "custom": "custom_hierarchy",
            "builtin": "builtin",
            "mixed": "mixed",
        }.get(exception_value, "unknown") if exception_total else "unknown"

        indent_value, _s, indent_total = _dominant(
            self.indents, "4", min_observations=1
        )
        indent = int(indent_value) if str(indent_value).isdigit() else 4

        import_value, _s2, import_total = _dominant(
            self.import_groups, "flat", min_observations=FILE_LEVEL_MIN_OBSERVATIONS
        )
        import_style = "grouped" if import_value == "grouped" else "flat" if import_total else "unknown"

        seen = max(1, self.callables_seen)
        profile = StyleProfile(
            repository_id=self.repository_id,
            function_naming=_as_style(function_naming),
            class_naming=_as_style(class_naming),
            constant_naming=_as_style(constant_naming),
            variable_naming=_as_style(variable_naming),
            quote_style=quote_style if quote_style in ("single", "double", "mixed") else "unknown",  # type: ignore[arg-type]
            docstring_style=docstring_style if docstring_style in ("google", "numpy", "sphinx", "plain", "none") else "unknown",  # type: ignore[arg-type]
            docstring_coverage=round(self.callables_documented / seen, 4),
            type_hint_coverage=round(min(1.0, self.callables_annotated / seen), 4),
            async_ratio=round(self.callables_async / seen, 4),
            logging_style=logging_style if logging_style in ("logging_module", "print", "structlog", "loguru", "none") else "unknown",  # type: ignore[arg-type]
            exception_style=exception_style,  # type: ignore[arg-type]
            import_style=import_style,  # type: ignore[arg-type]
            indent=indent,
            max_line_length=self.longest_line,
            observations=sorted(observations, key=lambda o: (-o.observations, o.property)),
            files_analyzed=self.files_analyzed,
        )
        profile.confidence = _profile_confidence(profile)
        return profile


_LOGGING_LIBRARIES = {
    "logging": "logging_module",
    "structlog": "structlog",
    "loguru": "loguru",
}


def _as_style(value: str) -> StyleValue:
    return value if value in ("snake_case", "camelCase", "PascalCase", "SCREAMING_SNAKE", "mixed") else "unknown"  # type: ignore[return-value]


def _profile_confidence(profile: StyleProfile) -> float:
    """How much of this profile is actually decided, weighted by sample size."""
    decided = [o for o in profile.observations if o.value not in ("unknown", "mixed")]
    if not profile.observations:
        return 0.0
    share_decided = len(decided) / len(profile.observations)
    sample = min(1.0, profile.files_analyzed / 20.0)
    return round(share_decided * sample, 4)


def learn_style(
    repo_path: Path | None,
    parsed_modules: dict[str, ParsedModule],
    repository_id: str = "",
    read_sources: bool = True,
) -> StyleProfile:
    """Build a style profile from already-parsed modules.

    Reuses the repository index's parse rather than re-parsing — the AST work is
    already done once per run, and doing it again would double the cost of a
    layer that must stay under 100 ms.
    """
    learner = StyleLearner(repository_id)

    for path, parsed in sorted(parsed_modules.items())[:MAX_FILES_SAMPLED]:
        source = ""
        if read_sources and repo_path is not None:
            try:
                source = (Path(repo_path) / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                source = ""
        learner.observe(path, parsed, source)

    return learner.profile()


def style_match_score(profile: StyleProfile, parsed: ParsedModule) -> float:
    """0..1 — how well one module conforms to the learned profile.

    Used as a scoring component after a repair, to detect a patch that ignored
    the repository's conventions.
    """
    if profile.function_naming in ("unknown", "mixed") or not parsed.function_spans:
        return 0.0

    matching = sum(
        1 for span in parsed.function_spans
        if not span.name.startswith("__") and classify_name(span.name) == profile.function_naming
    )
    considered = sum(1 for span in parsed.function_spans if not span.name.startswith("__"))
    return round(matching / considered, 4) if considered else 0.0

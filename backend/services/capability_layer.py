"""Infer logical business capabilities from deterministic surface signals.

The knowledge graph understands code. It does not understand that `login()`,
`jwt.py` and `middleware.py` are one feature. This layer proposes that grouping
from five kinds of evidence, all of them things the repository literally says:

    filename        a path segment or basename matches the capability vocabulary
    import          the file imports a library associated with the capability
    route           an API node in the file is named for the capability
    documentation   a document about the capability DESCRIBES the file
    configuration   a configuration file names the capability

Nothing is inferred from meaning. A file is never assigned to "Payments" because
its code *looks* transactional — only because something in the repository writes
the word down. That makes the output shallow and occasionally incomplete, which
is the correct trade: a hallucinated capability map is worse than none, because
it would silently mis-scope a repair.

`confidence` is driven by how many *independent kinds* of signal agree, not by
how many times one kind fires. Ten files named `auth_*.py` is still one kind of
evidence, and is reported as such.
"""

from __future__ import annotations

import re

from backend.models.knowledge_graph import Capability, Evidence, Explanation
from backend.services.knowledge_graph import RepositoryKnowledgeGraph
from backend.services.repository_graph import node_id

# Generic software-domain vocabulary. Deliberately domain-level, never
# repository-specific: these terms mean the same thing in any Python codebase,
# which is what keeps this layer portable.
CAPABILITY_VOCABULARY: dict[str, dict[str, tuple[str, ...]]] = {
    "authentication": {
        "terms": ("auth", "login", "logout", "signin", "signup", "session", "credential", "password", "identity", "sso", "oauth", "jwt", "token"),
        "libraries": ("jwt", "pyjwt", "authlib", "oauthlib", "passlib", "bcrypt", "argon2", "itsdangerous"),
    },
    "authorization": {
        "terms": ("authz", "permission", "role", "policy", "acl", "scope", "grant", "rbac"),
        "libraries": ("casbin", "oso"),
    },
    "payments": {
        "terms": ("payment", "checkout", "invoice", "billing", "charge", "refund", "subscription", "price", "cart", "order"),
        "libraries": ("stripe", "braintree", "paypal", "adyen", "square"),
    },
    "persistence": {
        "terms": ("model", "schema", "repository", "dao", "store", "database", "migration", "query", "orm", "entity"),
        "libraries": ("sqlalchemy", "psycopg2", "asyncpg", "pymongo", "redis", "alembic", "peewee", "tortoise"),
    },
    "api": {
        "terms": ("api", "route", "endpoint", "controller", "handler", "view", "resource", "rest", "graphql"),
        "libraries": ("fastapi", "flask", "django", "starlette", "aiohttp", "sanic", "falcon", "strawberry"),
    },
    "messaging": {
        "terms": ("queue", "broker", "consumer", "producer", "publish", "subscribe", "event", "stream", "topic"),
        "libraries": ("kafka", "pika", "celery", "kombu", "nats", "pulsar", "rabbitmq"),
    },
    "notification": {
        "terms": ("notification", "email", "mail", "sms", "push", "alert", "webhook", "digest"),
        "libraries": ("sendgrid", "twilio", "mailgun", "smtplib", "ses"),
    },
    "storage": {
        "terms": ("storage", "upload", "download", "file", "blob", "bucket", "asset", "media", "attachment"),
        "libraries": ("boto3", "minio", "s3fs", "azure", "gcsfs"),
    },
    "configuration": {
        "terms": ("config", "settings", "environment", "feature_flag", "toggle", "constant"),
        "libraries": ("pydantic_settings", "dynaconf", "environs", "dotenv"),
    },
    "observability": {
        "terms": ("log", "logging", "metric", "trace", "telemetry", "monitor", "audit", "instrument"),
        "libraries": ("opentelemetry", "prometheus_client", "sentry_sdk", "structlog", "datadog", "statsd"),
    },
    "scheduling": {
        "terms": ("schedule", "cron", "job", "worker", "task", "batch", "periodic", "timer"),
        "libraries": ("apscheduler", "schedule", "rq", "dramatiq"),
    },
    "search": {
        "terms": ("search", "index", "query", "rank", "facet", "suggest"),
        "libraries": ("elasticsearch", "opensearchpy", "whoosh", "meilisearch", "typesense"),
    },
    "security": {
        "terms": ("security", "encrypt", "decrypt", "crypto", "signature", "certificate", "secret", "vault", "sanitize"),
        "libraries": ("cryptography", "nacl", "hashlib", "secrets", "hvac"),
    },
    "reporting": {
        "terms": ("report", "export", "analytics", "dashboard", "aggregate", "summary", "statistics"),
        "libraries": ("pandas", "matplotlib", "openpyxl", "reportlab"),
    },
}

# -- signal weights --------------------------------------------------------
# Each is the confidence contributed by one *kind* of evidence agreeing.
# A single filename match tops out well below certainty on purpose.

W_FILENAME = 0.30
W_IMPORT = 0.30
W_ROUTE = 0.20
W_DOCUMENTATION = 0.15
W_CONFIGURATION = 0.05

# A capability supported by only one weak signal across only one file is noise.
MIN_CONFIDENCE = 0.25
MIN_FILES = 1

# A "capability" spanning most of the repository has not identified a feature —
# it has matched vocabulary that happens to be generic in this codebase. Terms
# like `store`, `model` and `query` are ubiquitous in some repositories and
# specific in others, and only the coverage tells the two cases apart. Above
# this share of files, confidence is scaled down in proportion to the dilution
# rather than the capability being dropped, so the caller still sees the
# grouping and sees that it is weak.
DILUTION_THRESHOLD = 0.35

# Dilution is a statistical argument and needs a sample to be one. In a
# four-file repository every capability covers a large share by arithmetic, not
# because the vocabulary is generic, so the penalty is not applied below this
# many files.
MIN_FILES_FOR_DILUTION = 8

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Lowercase word tokens, with snake/camel boundaries split."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return {t for t in _WORD_SPLIT.split(spaced.lower()) if t}


def _matches(tokens: set[str], terms: tuple[str, ...]) -> list[str]:
    """Terms present as whole tokens, allowing a regular plural.

    Substring matching is deliberately not used: `latest` must not match `test`,
    and `information` must not match `format`. But prose headings pluralise
    ("## Payments" for a `payment` capability), so a term also matches its `-s`
    and `-es` forms. That is the full extent of the morphology here — no
    stemming, no lemmatisation, nothing that could match a word the repository
    did not write.
    """
    matched: list[str] = []
    for term in terms:
        if term in tokens or f"{term}s" in tokens or f"{term}es" in tokens:
            matched.append(term)
    return sorted(matched)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def infer_capabilities(
    graph: RepositoryKnowledgeGraph,
    min_confidence: float = MIN_CONFIDENCE,
) -> list[Capability]:
    """Group files into capabilities. Deterministic and fully explained."""
    # capability -> file -> signal kind -> evidence details
    hits: dict[str, dict[str, dict[str, list[str]]]] = {
        name: {} for name in CAPABILITY_VOCABULARY
    }

    def record(capability: str, file: str, kind: str, detail: str) -> None:
        hits[capability].setdefault(file, {}).setdefault(kind, []).append(detail)

    _score_filenames(graph, record)
    _score_imports(graph, record)
    _score_routes(graph, record)
    _score_documentation(graph, record)
    _score_configuration(graph, record)

    total_files = len(graph.nodes_of_type("file")) or 1

    capabilities: list[Capability] = []
    for name, files in hits.items():
        if not files:
            continue
        capability = _assemble(graph, name, files, total_files)
        # Filtered on evidence strength *before* dilution. Dilution says "this
        # grouping is broad", not "this grouping is unsupported" — suppressing
        # on the diluted figure would hide a real, if weak, grouping entirely,
        # which contradicts what this layer promises its caller.
        base = capability.signal_counts and sum(
            weight
            for kind, weight in (
                ("filename", W_FILENAME),
                ("import", W_IMPORT),
                ("route", W_ROUTE),
                ("documentation", W_DOCUMENTATION),
                ("configuration", W_CONFIGURATION),
            )
            if kind in capability.signal_counts
        )
        if (base or 0.0) >= min_confidence and len(capability.files) >= MIN_FILES:
            capabilities.append(capability)

    capabilities.sort(key=lambda c: (-c.confidence, -len(c.files), c.slug))
    return capabilities


def _score_filenames(graph: RepositoryKnowledgeGraph, record) -> None:
    for node in graph.nodes_of_type("file"):
        tokens = _tokens(node.file)
        for capability, vocabulary in CAPABILITY_VOCABULARY.items():
            matched = _matches(tokens, vocabulary["terms"])
            if matched:
                record(capability, node.file, "filename", f"path names {', '.join(matched)}")


def _score_imports(graph: RepositoryKnowledgeGraph, record) -> None:
    """External dependencies are the strongest single signal available.

    A file importing `stripe` is doing payments regardless of what it is called.
    """
    for file, parsed in graph.intelligence.parsed_modules.items():
        modules = {m.split(".")[0].lower() for m in parsed.imports if m}
        if not modules:
            continue
        node = graph.node(node_id("file", file))
        if node is None:
            continue
        for capability, vocabulary in CAPABILITY_VOCABULARY.items():
            matched = sorted(modules & set(vocabulary["libraries"]))
            if matched:
                record(capability, node.file, "import", f"imports {', '.join(matched)}")


def _score_routes(graph: RepositoryKnowledgeGraph, record) -> None:
    for node in graph.nodes_of_type("api"):
        tokens = _tokens(node.qualname)
        for capability, vocabulary in CAPABILITY_VOCABULARY.items():
            matched = _matches(tokens, vocabulary["terms"])
            if matched:
                record(
                    capability,
                    node.file,
                    "route",
                    f"exposes endpoint {node.qualname} naming {', '.join(matched)}",
                )


def _score_documentation(graph: RepositoryKnowledgeGraph, record) -> None:
    for document in graph.nodes_of_type("document"):
        text = " ".join([document.name, *document.attributes.get("topics", [])])
        tokens = _tokens(text)
        described = [
            e.target
            for e in graph.out_edges(document.id, "DESCRIBES")
            if e.target in graph.nodes
        ]
        if not described:
            continue
        for capability, vocabulary in CAPABILITY_VOCABULARY.items():
            matched = _matches(tokens, vocabulary["terms"])
            if not matched:
                continue
            for target in described:
                target_node = graph.nodes[target]
                if target_node.file:
                    record(
                        capability,
                        target_node.file,
                        "documentation",
                        f"documented in {document.file} under '{document.name}'",
                    )


def _score_configuration(graph: RepositoryKnowledgeGraph, record) -> None:
    """Configuration names a capability for the files that already matched it.

    A config file mentioning "payments" does not make every file a payment file;
    it corroborates the ones another signal already implicated.
    """
    config_tokens: set[str] = set()
    for node in graph.nodes_of_type("config"):
        config_tokens |= _tokens(node.file)

    if not config_tokens:
        return

    for capability, vocabulary in CAPABILITY_VOCABULARY.items():
        matched = _matches(config_tokens, vocabulary["terms"])
        if matched:
            for node in graph.nodes_of_type("config"):
                record(capability, node.file, "configuration", f"configuration names {', '.join(matched)}")


def _assemble(
    graph: RepositoryKnowledgeGraph,
    name: str,
    files: dict[str, dict[str, list[str]]],
    total_files: int,
) -> Capability:
    """Build one capability, with confidence from distinct agreeing signal kinds."""
    weights = {
        "filename": W_FILENAME,
        "import": W_IMPORT,
        "route": W_ROUTE,
        "documentation": W_DOCUMENTATION,
        "configuration": W_CONFIGURATION,
    }

    kinds_present: dict[str, int] = {}
    for signals in files.values():
        for kind in signals:
            kinds_present[kind] = kinds_present.get(kind, 0) + 1

    confidence = min(1.0, sum(weights[k] for k in kinds_present))

    # Dilution: a grouping covering most of the repository has matched generic
    # vocabulary rather than isolated a feature.
    coverage = len(files) / total_files if total_files else 0.0
    dilution = 1.0
    if total_files >= MIN_FILES_FOR_DILUTION and coverage > DILUTION_THRESHOLD:
        dilution = round(max(0.1, DILUTION_THRESHOLD / coverage), 4)
        confidence *= dilution
    confidence = round(confidence, 4)

    evidence: list[Evidence] = []
    if dilution < 1.0:
        evidence.append(
            Evidence(
                signal="coverage_dilution",
                value=round(coverage, 4),
                contribution=-round(1.0 - dilution, 4),
                detail=(
                    f"spans {coverage:.0%} of the repository — the vocabulary for '{name}' "
                    "appears generic here, so confidence is scaled down"
                ),
                provenance="capability",
            )
        )
    for kind, count in sorted(kinds_present.items(), key=lambda kv: -weights[kv[0]]):
        samples = sorted(
            detail
            for signals in files.values()
            for detail in signals.get(kind, [])
        )[:3]
        evidence.append(
            Evidence(
                signal=kind,
                value=float(count),
                contribution=weights[kind],
                detail=f"{count} file(s) matched by {kind}: " + "; ".join(samples),
                provenance="capability",
            )
        )

    member_files = sorted(files)
    entry_points = sorted(
        node.qualname
        for node in graph.nodes_of_type("api")
        if node.file in files
    )

    return Capability(
        name=name.replace("_", " ").title(),
        slug=_slug(name),
        files=member_files,
        entry_points=entry_points,
        confidence=confidence,
        signal_counts=dict(sorted(kinds_present.items())),
        explanation=Explanation(
            summary=(
                f"{len(member_files)} file(s) grouped as '{name}' from "
                f"{len(kinds_present)} independent signal kind(s)"
            ),
            evidence=evidence,
        ),
    )


def attach_capabilities(
    graph: RepositoryKnowledgeGraph,
    capabilities: list[Capability],
) -> None:
    """Add capability nodes and PART_OF edges into the graph.

    Done as a separate step so capability inference stays a pure function over
    an already-built graph, and so a caller that does not want the grouping
    simply does not call this.
    """
    from backend.models.knowledge_graph import KGNode
    from backend.services.knowledge_graph import capability_id

    for capability in capabilities:
        node = capability_id(capability.slug)
        graph.add_node(
            KGNode(
                id=node,
                type="capability",
                name=capability.name,
                attributes={
                    "confidence": capability.confidence,
                    "signal_counts": capability.signal_counts,
                },
            )
        )
        for file in capability.files:
            graph.add_edge(
                node_id("file", file),
                node,
                "PART_OF",
                weight=capability.confidence,
                provenance="capability",
                evidence=(
                    f"grouped into {capability.name} "
                    f"(confidence {capability.confidence:.2f}, "
                    f"signals: {', '.join(sorted(capability.signal_counts))})"
                ),
            )


def capability_for_file(capabilities: list[Capability], file: str) -> Capability | None:
    """The highest-confidence capability containing a file, or None."""
    matching = [c for c in capabilities if file in c.files]
    if not matching:
        return None
    return max(matching, key=lambda c: (c.confidence, -len(c.files)))

"""Detect frameworks and the conventions they imply.

Detection is evidence-weighted, not first-match: a repository importing both
`flask` and `fastapi` reports both, ranked by how much evidence supports each,
because a migration in progress is a real state and picking one silently would
give the patch generator the wrong conventions half the time.

Three evidence kinds, deliberately weighted differently:

* **Imports** are the strongest signal — a file importing `fastapi` is using it.
* **Manifest dependencies** are weaker: a declared dependency may be unused, or
  used only in one corner of a monorepo.
* **File and directory markers** (`urls.py`, `settings.py`, `pages/`) are
  weakest and only corroborate; on their own they are coincidence.

The conventions each framework implies are a fixed table, not learned. That is
the honest design: FastAPI's routing convention is a property of FastAPI, and
mining it from three repositories would produce a worse answer than writing it
down. What *is* learned is which framework this repository uses and how
confidently — the part that varies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from backend.models.learning import FrameworkConvention, FrameworkProfile

W_IMPORT = 1.0
W_MANIFEST = 0.6
W_MARKER = 0.3

# Evidence weight at which a framework is considered confidently present.
# An import plus a manifest declaration (1.6) should read as near-certain, so
# saturation sits just above that rather than demanding every possible signal.
CONFIDENCE_SATURATION = 1.8


@dataclass(frozen=True)
class FrameworkSpec:
    """How to detect one framework, and what it implies."""

    name: str
    language: str
    imports: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    conventions: tuple[tuple[str, str], ...] = ()


FRAMEWORKS: tuple[FrameworkSpec, ...] = (
    FrameworkSpec(
        "FastAPI", "python",
        imports=("fastapi",),
        packages=("fastapi",),
        markers=("routers/", "dependencies.py"),
        conventions=(
            ("routing", "APIRouter with decorator-based path operations"),
            ("validation", "Pydantic models for request and response bodies"),
            ("dependency_injection", "Depends() in the signature, not module globals"),
            ("testing", "TestClient / httpx.AsyncClient against the app"),
            ("auth", "security dependencies (OAuth2PasswordBearer, APIKeyHeader)"),
            ("config", "pydantic-settings BaseSettings"),
        ),
    ),
    FrameworkSpec(
        "Flask", "python",
        imports=("flask",),
        packages=("flask",),
        markers=("app.py", "wsgi.py", "blueprints/"),
        conventions=(
            ("routing", "@app.route or Blueprint decorators"),
            ("validation", "explicit request parsing, or marshmallow schemas"),
            ("testing", "app.test_client()"),
            ("config", "app.config from an object or environment"),
        ),
    ),
    FrameworkSpec(
        "Django", "python",
        imports=("django",),
        packages=("django",),
        markers=("manage.py", "urls.py", "settings.py", "migrations/", "models.py"),
        conventions=(
            ("routing", "urls.py path() entries mapping to views"),
            ("orm", "Django ORM models with managers and querysets"),
            ("validation", "Django forms or DRF serializers"),
            ("testing", "django.test.TestCase with the test database"),
            ("auth", "django.contrib.auth and permission classes"),
            ("config", "settings.py module"),
        ),
    ),
    FrameworkSpec(
        "SQLAlchemy", "python",
        imports=("sqlalchemy",),
        packages=("sqlalchemy",),
        markers=("alembic/", "alembic.ini"),
        conventions=(
            ("orm", "declarative models with a Session per unit of work"),
            ("testing", "transactional test fixtures rolled back per test"),
        ),
    ),
    FrameworkSpec(
        "Express", "javascript",
        imports=("express",),
        packages=("express",),
        markers=("routes/", "server.js", "app.js"),
        conventions=(
            ("routing", "express.Router() with middleware chains"),
            ("validation", "middleware validators (joi, express-validator)"),
            ("testing", "supertest against the app instance"),
        ),
    ),
    FrameworkSpec(
        "NestJS", "typescript",
        imports=("@nestjs/common", "@nestjs/core"),
        packages=("@nestjs/core",),
        markers=(".module.ts", ".controller.ts", ".service.ts"),
        conventions=(
            ("routing", "@Controller and @Get/@Post decorators"),
            ("dependency_injection", "constructor injection with @Injectable providers"),
            ("validation", "class-validator DTOs with a global ValidationPipe"),
            ("testing", "Test.createTestingModule"),
        ),
    ),
    FrameworkSpec(
        "React", "javascript",
        imports=("react",),
        packages=("react",),
        markers=("components/", ".jsx", ".tsx"),
        conventions=(
            ("routing", "react-router route definitions"),
            ("testing", "React Testing Library, queries by role"),
        ),
    ),
    FrameworkSpec(
        "Next.js", "javascript",
        imports=("next",),
        packages=("next",),
        markers=("pages/", "app/", "next.config.js"),
        conventions=(
            ("routing", "file-system routing under pages/ or app/"),
            ("config", "next.config.js"),
        ),
    ),
    FrameworkSpec(
        "Angular", "typescript",
        imports=("@angular/core",),
        packages=("@angular/core",),
        markers=("angular.json", ".component.ts", ".module.ts"),
        conventions=(
            ("dependency_injection", "constructor injection with @Injectable"),
            ("testing", "TestBed configured per spec"),
        ),
    ),
    FrameworkSpec(
        "Vue", "javascript",
        imports=("vue",),
        packages=("vue",),
        markers=(".vue", "vue.config.js"),
        conventions=(("testing", "@vue/test-utils mount/shallowMount"),),
    ),
    FrameworkSpec(
        "Spring", "java",
        imports=("org.springframework",),
        packages=("spring-boot-starter",),
        markers=("pom.xml", "build.gradle", "application.properties", "application.yml"),
        conventions=(
            ("routing", "@RestController with @RequestMapping"),
            ("dependency_injection", "constructor injection with @Autowired components"),
            ("orm", "Spring Data repositories"),
            ("testing", "@SpringBootTest with MockMvc"),
        ),
    ),
    FrameworkSpec(
        "ASP.NET", "csharp",
        imports=("Microsoft.AspNetCore",),
        packages=("Microsoft.AspNetCore.App",),
        markers=("Startup.cs", "Program.cs", ".csproj"),
        conventions=(
            ("routing", "attribute routing on ControllerBase"),
            ("dependency_injection", "IServiceCollection registration"),
            ("orm", "Entity Framework Core DbContext"),
        ),
    ),
    FrameworkSpec(
        "Laravel", "php",
        imports=("Illuminate",),
        packages=("laravel/framework",),
        markers=("artisan", "routes/web.php", "app/Http/"),
        conventions=(
            ("routing", "routes/web.php and routes/api.php"),
            ("orm", "Eloquent models"),
            ("validation", "form request classes"),
        ),
    ),
)

MANIFESTS = ("requirements.txt", "pyproject.toml", "package.json", "pom.xml", "build.gradle", "composer.json")

_PY_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9._-]+)", re.MULTILINE)


def read_manifest_packages(repo_path: Path) -> tuple[set[str], list[str]]:
    """Declared dependencies across every manifest found, plus which were read."""
    repo = Path(repo_path)
    packages: set[str] = set()
    read: list[str] = []

    for manifest in MANIFESTS:
        path = repo / manifest
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        read.append(manifest)

        if manifest == "package.json":
            try:
                data = json.loads(text)
                for section in ("dependencies", "devDependencies", "peerDependencies"):
                    packages.update(data.get(section, {}) or {})
            except (json.JSONDecodeError, AttributeError):
                continue
        else:
            packages.update(name.lower() for name in _PY_REQUIREMENT.findall(text) if name)

    return packages, read


def detect_frameworks(
    repo_path: Path | None,
    imports: set[str],
    file_paths: tuple[str, ...] = (),
) -> FrameworkProfile:
    """Score every known framework against the available evidence."""
    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}

    packages, manifests_read = read_manifest_packages(repo_path) if repo_path else (set(), [])
    lowered_imports = {i.lower() for i in imports}
    joined_paths = " ".join(file_paths).lower()

    for spec in FRAMEWORKS:
        score = 0.0
        why: list[str] = []
        substantive = False  # an import or a declared dependency, not just a marker

        for module in spec.imports:
            root = module.split(".")[0].lower()
            if root in lowered_imports or module.lower() in lowered_imports:
                score += W_IMPORT
                substantive = True
                why.append(f"imports {module}")

        for package in spec.packages:
            if package.lower() in packages:
                score += W_MANIFEST
                substantive = True
                why.append(f"declares {package}")

        for marker in spec.markers:
            if marker.lower() in joined_paths:
                score += W_MARKER
                why.append(f"has {marker}")

        # Markers corroborate; they never establish. `models.py` and `routes/`
        # are ordinary directory names, and treating them as evidence on their
        # own reports Django in every repository that has a models module.
        if score > 0 and substantive:
            scores[spec.name] = round(score, 4)
            evidence[spec.name] = why

    if not scores:
        return FrameworkProfile(detected_from=manifests_read)

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    primary_name, primary_score = ranked[0]
    primary_spec = next(s for s in FRAMEWORKS if s.name == primary_name)

    confidence = round(min(1.0, primary_score / CONFIDENCE_SATURATION), 4)
    conventions = [
        FrameworkConvention(
            aspect=aspect,
            convention=convention,
            evidence=evidence[primary_name],
            confidence=confidence,
        )
        for aspect, convention in primary_spec.conventions
    ]

    return FrameworkProfile(
        primary_framework=primary_name,
        frameworks={
            name: round(min(1.0, value / CONFIDENCE_SATURATION), 4) for name, value in ranked
        },
        conventions=conventions,
        detected_from=manifests_read + sorted(evidence[primary_name]),
        confidence=confidence,
    )


def learn_framework(
    repo_path: Path | None,
    parsed_modules: dict,
    repository_id: str = "",
    file_paths: tuple[str, ...] = (),
) -> FrameworkProfile:
    """Detect frameworks from an already-parsed repository index."""
    imports: set[str] = set()
    for parsed in parsed_modules.values():
        imports.update(parsed.imports)

    paths = file_paths or tuple(parsed_modules)
    profile = detect_frameworks(repo_path, imports, paths)
    profile.repository_id = repository_id
    return profile


def framework_match_score(profile: FrameworkProfile, imports: set[str]) -> float:
    """0..1 — whether a patch used the repository's framework rather than another.

    A patch that introduces Flask into a FastAPI repository scores 0 regardless
    of whether it works, which is exactly the signal a reviewer would raise.
    """
    if profile.primary_framework == "unknown":
        return 0.0

    lowered = {i.lower() for i in imports}
    primary = next((s for s in FRAMEWORKS if s.name == profile.primary_framework), None)
    if primary is None:
        return 0.0

    uses_primary = any(m.split(".")[0].lower() in lowered for m in primary.imports)
    competing = [
        spec for spec in FRAMEWORKS
        if spec.name != profile.primary_framework
        and spec.language == primary.language
        and spec.conventions
        and any(m.split(".")[0].lower() in lowered for m in spec.imports)
    ]

    if competing and not uses_primary:
        return 0.0
    if uses_primary:
        return 1.0
    return 0.5  # framework-agnostic change: neither conforming nor conflicting

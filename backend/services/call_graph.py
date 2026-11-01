"""Interprocedural call graph over the repository's callables.

Python resolves calls dynamically, so a fully sound static call graph is not
achievable. This builder is deliberately explicit about that: a call is resolved
only when the AST gives enough evidence, and everything else is recorded in
`unresolved_calls` rather than guessed at. Over-linking would corrupt the fan-in
and fan-out figures that A5.5 ranks on.

Bare calls (``helper()``) resolve in this order:

1. a callable defined in the same module
2. a name the module explicitly imported, mapped to its defining module
3. a repository-unique, non-ambiguous name in a module this file imports
4. otherwise unresolved

Attribute calls (``store.get_events()``) skip steps 1 and 2 entirely. The
receiver's type is unknown, so module scope proves nothing: a local
``def get_events`` does not make ``store.get_events()`` a self-call. Only step 3
applies, which is why the parser records bare and attribute invocations
separately.
"""

from __future__ import annotations

from collections import deque

from backend.models.repository_graph import CallGraph, CallGraphNode, CallSite
from backend.services.python_ast_parser import FunctionSpan, ParsedModule

# Guard against pathological graphs when computing reachability depth.
MAX_DEPTH_ITERATIONS = 10_000

# Method names that overwhelmingly denote builtin container or stdlib protocol
# operations. A repository that happens to define exactly one `get` must not
# absorb every `dict.get()` in the codebase as an inbound call — that inflates
# fan-in, which A5.5 ranks on, and the resulting graph is worse than no graph.
AMBIGUOUS_METHOD_NAMES = frozenset({
    "add", "append", "clear", "close", "copy", "count", "decode", "encode",
    "endswith", "extend", "format", "get", "index", "insert", "items", "join",
    "keys", "load", "lower", "pop", "read", "remove", "replace", "reverse",
    "run", "set", "sort", "split", "startswith", "strip", "update", "upper",
    "values", "write",
})


def callable_id(file: str, qualname: str) -> str:
    return f"{file}::{qualname}"


def graph_file_of(identifier: str) -> str:
    """File portion of a callable id."""
    return identifier.split("::", 1)[0]


def _decorator_calls(span: FunctionSpan) -> set[str]:
    return set(span.decorators)


class _Resolver:
    """Name -> callable id, using progressively weaker evidence."""

    def __init__(
        self,
        parsed_modules: dict[str, ParsedModule],
        module_index: dict[str, str],
    ):
        self.module_index = module_index

        # Per-module: bare name -> id (module-level functions and methods).
        self.local: dict[str, dict[str, str]] = {}
        # Repository-wide: bare name -> [ids]
        self.global_names: dict[str, list[str]] = {}
        # Per-module: imported alias -> source module path
        self.imports: dict[str, dict[str, str]] = {}
        # Per-module: the set of repository files it can reach via imports.
        self.imported_files: dict[str, set[str]] = {}

        for file, parsed in parsed_modules.items():
            local: dict[str, str] = {}
            for span in parsed.function_spans:
                identifier = callable_id(file, span.qualname)
                local.setdefault(span.name, identifier)
                self.global_names.setdefault(span.name, []).append(identifier)
            self.local[file] = local

            self.imported_files.setdefault(file, set()).add(file)
            alias_map: dict[str, str] = {}
            for span in parsed.import_spans:
                if span.module:
                    resolved = self._module_to_file(span.module)
                    for name in span.names:
                        alias_map[name.split(".")[0]] = resolved or ""
                    if resolved:
                        self.imported_files[file].add(resolved)
                else:
                    for name in span.names:
                        resolved = self._module_to_file(name)
                        alias_map[name.split(".")[0]] = resolved or ""
                        if resolved:
                            self.imported_files[file].add(resolved)
            self.imports[file] = alias_map

    def _module_to_file(self, module: str) -> str | None:
        dotted = module.replace("/", ".")
        if dotted in self.module_index:
            return self.module_index[dotted]
        parts = dotted.split(".")
        for cut in range(len(parts), 0, -1):
            prefix = ".".join(parts[:cut])
            if prefix in self.module_index:
                return self.module_index[prefix]
        return None

    def resolve(self, file: str, name: str) -> str | None:
        """Strong-evidence resolution only: same module, or an explicit import.

        The weaker repository-unique fallback is applied by the caller, which
        can also enforce the ambiguity and import-reachability guards.
        """
        local = self.local.get(file, {})
        if name in local:
            return local[name]

        source_file = self.imports.get(file, {}).get(name)
        if source_file and name in self.local.get(source_file, {}):
            return self.local[source_file][name]

        return None

    def reaches(self, file: str, identifier: str) -> bool:
        """True when `file` imports the module defining `identifier`."""
        return graph_file_of(identifier) in self.imported_files.get(file, set())


def _compute_depths(nodes: dict[str, CallGraphNode]) -> None:
    """Longest call chain reaching each node, from any entry point.

    Entry points are callables nothing calls. Cycles are broken by visiting each
    node at most once per relaxation pass, bounded by MAX_DEPTH_ITERATIONS.
    """
    roots = [nid for nid, node in nodes.items() if not node.incoming_calls]
    if not roots:
        roots = sorted(nodes)  # fully cyclic graph: every node is an entry

    queue: deque[tuple[str, int]] = deque((r, 0) for r in sorted(roots))
    best: dict[str, int] = {}
    iterations = 0

    while queue and iterations < MAX_DEPTH_ITERATIONS:
        iterations += 1
        current, depth = queue.popleft()
        if best.get(current, -1) >= depth:
            continue
        best[current] = depth
        for callee in nodes[current].outgoing_calls:
            if callee in nodes:
                queue.append((callee, depth + 1))

    for nid, node in nodes.items():
        node.call_depth = best.get(nid, 0)


def build_call_graph(
    parsed_modules: dict[str, ParsedModule],
    module_index: dict[str, str],
) -> CallGraph:
    """Resolve every call site the AST recorded into a directed graph."""
    graph = CallGraph()
    resolver = _Resolver(parsed_modules, module_index)

    # Attribute dispatch. Two conditions must both hold before an attribute call
    # is linked: the name must have exactly one definition in the repository, and
    # it must not be a common container/stdlib operation. Even then the callee's
    # file must be import-reachable from the caller (checked per call site), so a
    # unique name in an unrelated module cannot absorb unrelated calls.
    unique_methods = {
        name: ids[0]
        for name, ids in resolver.global_names.items()
        if len(ids) == 1 and name not in AMBIGUOUS_METHOD_NAMES
    }

    for file, parsed in parsed_modules.items():
        for span in parsed.function_spans:
            identifier = callable_id(file, span.qualname)
            graph.nodes[identifier] = CallGraphNode(
                id=identifier,
                name=span.name,
                qualname=span.qualname,
                file=file,
                is_async=span.is_async,
                is_method=span.is_method,
                decorators=list(span.decorators),
            )

    for file, parsed in parsed_modules.items():
        for span in parsed.function_spans:
            caller = callable_id(file, span.qualname)
            decorators = _decorator_calls(span)

            bare = list(span.calls) + sorted(decorators)
            attribute = list(span.attribute_calls)

            for called in bare + attribute:
                is_attribute = called in attribute and called not in bare

                if is_attribute:
                    # The receiver's type is unknown, so module scope tells us
                    # nothing. Only a repository-unique, non-ambiguous name in an
                    # imported module is enough evidence to link.
                    candidate = unique_methods.get(called)
                    callee = candidate if candidate and resolver.reaches(file, candidate) else None
                else:
                    callee = resolver.resolve(file, called)
                    if callee is None:
                        candidate = unique_methods.get(called)
                        if candidate and resolver.reaches(file, candidate):
                            callee = candidate

                if callee is None or callee not in graph.nodes:
                    graph.unresolved_calls[called] = graph.unresolved_calls.get(called, 0) + 1
                    continue

                is_recursive = callee == caller
                site = CallSite(
                    caller=caller,
                    callee=callee,
                    is_async=graph.nodes[callee].is_async,
                    via_decorator=called in decorators,
                    via_attribute=is_attribute,
                    is_recursive=is_recursive,
                )
                graph.call_sites.append(site)

                caller_node = graph.nodes[caller]
                callee_node = graph.nodes[callee]
                if callee not in caller_node.outgoing_calls:
                    caller_node.outgoing_calls.append(callee)
                if caller not in callee_node.incoming_calls:
                    callee_node.incoming_calls.append(caller)
                if is_recursive:
                    caller_node.is_recursive = True

    for node in graph.nodes.values():
        node.fan_in = len(node.incoming_calls)
        node.fan_out = len(node.outgoing_calls)

    _compute_depths(graph.nodes)
    return graph


def file_fan_metrics(graph: CallGraph) -> dict[str, dict[str, int]]:
    """Per-file fan-in/fan-out totals, the form A5.5 ranks on."""
    metrics: dict[str, dict[str, int]] = {}
    for node in graph.nodes.values():
        entry = metrics.setdefault(node.file, {"fan_in": 0, "fan_out": 0, "callables": 0})
        entry["fan_in"] += node.fan_in
        entry["fan_out"] += node.fan_out
        entry["callables"] += 1
    return metrics

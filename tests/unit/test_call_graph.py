"""Call graph: what resolves, what deliberately does not, and the fan metrics.

The tests that matter most here are the negative ones. Over-linking inflates
fan-in, which A5.5 ranks on, so a call the AST cannot justify must land in
`unresolved_calls` rather than being guessed at.
"""

import pytest

from backend.services.call_graph import (
    build_call_graph,
    callable_id,
    file_fan_metrics,
    graph_file_of,
)
from backend.services.python_ast_parser import parse_source
from backend.services.repository_graph import build_module_index

UTIL = '''def helper(v):
    return v + 1


def unused():
    return 0
'''

AUTH = '''from pkg.util import helper


def validate(token):
    return helper(token)


def login(user):
    return validate(user)


def recurse(n):
    if n:
        return recurse(n - 1)
    return 0


async def fetch():
    return validate(1)
'''

DISPATCH = '''class Store:
    def get(self, key):
        return key


def read(store):
    # `store.get(...)` is a container-shaped attribute call on an unknown
    # receiver. It must not resolve to Store.get.
    return store.get("k")
'''


def build(sources: dict[str, str]):
    parsed = {path: parse_source(text) for path, text in sources.items()}
    parsed = {p: m for p, m in parsed.items() if m is not None}
    return build_call_graph(parsed, build_module_index(list(sources)))


@pytest.fixture
def graph():
    return build({"pkg/util.py": UTIL, "pkg/auth.py": AUTH})


def test_callable_id_round_trips_to_its_file():
    assert graph_file_of(callable_id("pkg/auth.py", "validate")) == "pkg/auth.py"


def test_same_module_call_resolves(graph):
    login = graph.nodes[callable_id("pkg/auth.py", "login")]
    assert callable_id("pkg/auth.py", "validate") in login.outgoing_calls


def test_imported_call_resolves_across_modules(graph):
    validate = graph.nodes[callable_id("pkg/auth.py", "validate")]
    assert callable_id("pkg/util.py", "helper") in validate.outgoing_calls


def test_incoming_and_fan_counts(graph):
    validate = graph.nodes[callable_id("pkg/auth.py", "validate")]
    assert validate.fan_in == len(validate.incoming_calls)
    assert validate.fan_out == len(validate.outgoing_calls)
    # login and fetch both call validate.
    assert validate.fan_in == 2


def test_uncalled_function_has_zero_fan_in(graph):
    assert graph.nodes[callable_id("pkg/util.py", "unused")].fan_in == 0


def test_recursion_is_flagged(graph):
    node = graph.nodes[callable_id("pkg/auth.py", "recurse")]
    assert node.is_recursive
    assert any(s.is_recursive for s in graph.call_sites if s.caller == node.id)


def test_async_callable_is_marked(graph):
    assert graph.nodes[callable_id("pkg/auth.py", "fetch")].is_async


def test_call_into_async_callee_marks_the_site():
    graph = build({
        "pkg/a.py": "async def work():\n    return 1\n\n\ndef caller():\n    return work()\n",
    })
    site = next(s for s in graph.call_sites if s.callee.endswith("::work"))
    assert site.is_async


def test_decorator_is_recorded_as_a_call():
    graph = build({
        "pkg/a.py": "def deco(fn):\n    return fn\n\n\n@deco\ndef target():\n    return 1\n",
    })
    site = next(s for s in graph.call_sites if s.callee.endswith("::deco"))
    assert site.via_decorator


def test_ambiguous_method_name_does_not_resolve():
    """`store.get()` must not bind to a repository method merely named `get`."""
    graph = build({"pkg/store.py": DISPATCH})
    read = graph.nodes[callable_id("pkg/store.py", "read")]
    assert callable_id("pkg/store.py", "Store.get") not in read.outgoing_calls
    assert "get" in graph.unresolved_calls


def test_unique_attribute_call_resolves_only_when_import_reachable():
    sources = {
        "pkg/impl.py": "def distinctive_operation():\n    return 1\n",
        # No import of pkg.impl, so the unique name is not reachable from here.
        "pkg/caller.py": "def go(obj):\n    return obj.distinctive_operation()\n",
    }
    graph = build(sources)
    go = graph.nodes[callable_id("pkg/caller.py", "go")]
    assert go.outgoing_calls == []
    assert "distinctive_operation" in graph.unresolved_calls


def test_method_flag_and_qualname():
    graph = build({"pkg/a.py": "class C:\n    def m(self):\n        return 1\n"})
    node = graph.nodes[callable_id("pkg/a.py", "C.m")]
    assert node.is_method
    assert node.qualname == "C.m"


def test_call_depth_increases_along_the_chain(graph):
    login = graph.nodes[callable_id("pkg/auth.py", "login")]
    helper = graph.nodes[callable_id("pkg/util.py", "helper")]
    assert helper.call_depth > login.call_depth


def test_cyclic_graph_terminates():
    graph = build({
        "pkg/a.py": "def a():\n    return b()\n\n\ndef b():\n    return a()\n",
    })
    assert set(graph.nodes) == {callable_id("pkg/a.py", "a"), callable_id("pkg/a.py", "b")}


def test_file_fan_metrics_aggregate_per_file(graph):
    metrics = file_fan_metrics(graph)
    assert metrics["pkg/auth.py"]["callables"] == 4
    assert metrics["pkg/util.py"]["fan_in"] >= 1


def test_empty_input_produces_empty_graph():
    graph = build({})
    assert graph.nodes == {}
    assert graph.call_sites == []

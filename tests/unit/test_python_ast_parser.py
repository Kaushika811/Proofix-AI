import ast
from pathlib import Path

from backend.services.python_ast_parser import parse_python_file, parsed_module_from_tree


def test_parse_python_file_extracts_symbols(tmp_path: Path):
    src = tmp_path / "auth.py"
    src.write_text(
        '''"""Auth module."""
import jwt

def validate_token(x):
    pass

class AdminMiddleware:
    pass
''',
        encoding="utf-8",
    )
    parsed = parse_python_file(tmp_path, "auth.py")
    assert parsed is not None
    assert "jwt" in parsed.imports
    assert "validate_token" in parsed.functions
    assert "AdminMiddleware" in parsed.classes
    assert parsed.docstring == "Auth module."


def test_parsed_module_from_tree_exported_symbols():
    source = "def public_fn(): pass\n__all__ = ['public_fn']\n"
    tree = ast.parse(source)
    parsed = parsed_module_from_tree(tree, source)
    assert parsed.exported_symbols == ["public_fn"]

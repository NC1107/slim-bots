"""Every docstring in the repo is at most 2 lines; longer explanation belongs in docs/ or a README."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXCLUDE_DIR_NAMES = {".git", "__pycache__", "node_modules"}
MAX_DOCSTRING_LINES = 2


def _python_files():
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in EXCLUDE_DIR_NAMES or part.endswith(".egg-info") for part in path.parts):
            continue
        yield path


def _long_docstrings(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = []
    nodes = [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in nodes:
        doc = ast.get_docstring(node)
        if doc is None:
            continue
        lines = [line for line in doc.strip().splitlines()]
        if len(lines) > MAX_DOCSTRING_LINES:
            name = getattr(node, "name", "<module>")
            lineno = getattr(node, "lineno", 1)
            violations.append(f"{path}:{lineno}: `{name}` has a {len(lines)}-line docstring")
    return violations


def test_no_docstring_exceeds_two_lines():
    violations = []
    for path in _python_files():
        violations.extend(_long_docstrings(path))
    assert not violations, "docstrings over 2 lines (move the rest into docs/ or a README):\n" + "\n".join(violations)

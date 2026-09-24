"""A ported bot's config and command triggers are the framework's job now, not `os`/`re`; bot-ping stays library-free."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXEMPT_BOTS = {"bot-ping"}
BANNED_MODULES = {"os", "re"}


def _bot_files():
    for path in sorted(REPO_ROOT.glob("bot-*/bot.py")):
        if path.parent.name not in EXEMPT_BOTS:
            yield path


def _banned_imports(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name in BANNED_MODULES)
        elif isinstance(node, ast.ImportFrom) and node.module in BANNED_MODULES:
            found.add(node.module)
    return found


def test_ported_bots_do_not_import_os_or_re():
    violations = []
    for path in _bot_files():
        banned = _banned_imports(path)
        if banned:
            violations.append(f"{path}: imports {', '.join(sorted(banned))}")
    assert not violations, "config (os) and command triggers (re) belong to the framework now:\n" + "\n".join(violations)

"""Guards the architectural property that makes the C++ port mechanical:
core/ is pure functions on float dataclasses, stdlib-only, no clock, no I/O.
"""

import ast
from pathlib import Path

CORE_DIR = Path(__file__).parent.parent / "core"

# Deliberately narrow: any addition here is a review decision, not an accident.
ALLOWED_IMPORTS = {"dataclasses", "math"}

CLOCK_CALL_NAMES = {
    "perf_counter",
    "perf_counter_ns",
    "monotonic",
    "monotonic_ns",
    "time",
    "time_ns",
    "now",
    "today",
}

IO_CALL_NAMES = {"open", "print", "input"}


def _core_files() -> list[Path]:
    return sorted(CORE_DIR.rglob("*.py"))


def _root_module(dotted: str) -> str:
    return dotted.split(".")[0]


def _is_allowed_module(dotted: str, level: int) -> bool:
    if level > 0:
        return True  # a relative import always targets core.* by construction
    root = _root_module(dotted)
    return root in ALLOWED_IMPORTS or root == "core"


def _check_imports(tree: ast.AST, path: Path) -> list[str]:
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _is_allowed_module(alias.name, level=0):
                    violations.append(
                        f"{path}:{node.lineno}: import '{alias.name}' is not in the "
                        f"core/ whitelist (dataclasses, math, core.*) -- it would drag "
                        f"a non-portable dependency into the future C++ port."
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not _is_allowed_module(module, node.level):
                violations.append(
                    f"{path}:{node.lineno}: import from '{module}' is not in the "
                    f"core/ whitelist (dataclasses, math, core.*) -- it would drag "
                    f"a non-portable dependency into the future C++ port."
                )
    return violations


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _check_clock_and_io(tree: ast.AST, path: Path) -> list[str]:
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in CLOCK_CALL_NAMES:
            violations.append(
                f"{path}:{node.lineno}: call to '{name}' reads a clock -- core/ takes "
                f"time only as a now_ms parameter, never by reading it itself."
            )
        elif name in IO_CALL_NAMES:
            violations.append(
                f"{path}:{node.lineno}: call to '{name}' performs I/O -- core/ must "
                f"stay pure functions with no side effects."
            )
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"stdout", "stderr"}:
            if isinstance(node.value, ast.Name) and node.value.id == "sys":
                violations.append(
                    f"{path}:{node.lineno}: use of 'sys.{node.attr}' performs I/O -- "
                    f"core/ must stay pure functions with no side effects."
                )
    return violations


def _check_async(tree: ast.AST, path: Path) -> list[str]:
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith)):
            violations.append(
                f"{path}:{node.lineno}: {type(node).__name__} makes core/ asynchronous "
                f"-- it must stay callable synchronously, with state passed in and out."
            )
        if isinstance(node, ast.Await):
            violations.append(
                f"{path}:{node.lineno}: 'await' makes core/ asynchronous -- it must "
                f"stay callable synchronously, with state passed in and out."
            )
    return violations


def test_core_files_exist():
    assert _core_files(), "core/ has no .py files -- the discovery below would pass vacuously"


def test_core_only_imports_the_whitelist():
    violations = []
    for path in _core_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        violations += _check_imports(tree, path)
    assert not violations, "\n".join(violations)


def test_core_reads_no_clock_and_does_no_io():
    violations = []
    for path in _core_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        violations += _check_clock_and_io(tree, path)
    assert not violations, "\n".join(violations)


def test_core_has_no_async():
    violations = []
    for path in _core_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        violations += _check_async(tree, path)
    assert not violations, "\n".join(violations)

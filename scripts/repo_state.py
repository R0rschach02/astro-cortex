#!/usr/bin/env python3
"""
Astro Cortex - Repo State Verifier

Run this BEFORE writing or handing off any spec to an LLM.
It produces a markdown summary of the current repo state that
every LLM should reference to avoid stale assumptions.

Usage:
    python scripts/repo_state.py [--output PATH]

Default output: docs/REPO_STATE.md (overwritten each run)
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories to scan (relative to repo root)
SCAN_DIRS = ["app", "scripts", "tests"]

# Directories to skip
SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", "build", "dist"}

# Patterns that mark a function as "stub"
STUB_PATTERNS = [
    "NotImplementedError",
    "TODO",
    "FIXME",
    "STUB",
    "not yet implemented",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FunctionInfo:
    name: str
    line: int
    is_stub: bool = False
    stub_reason: str = ""
    has_todo: bool = False


@dataclass
class ModuleInfo:
    path: Path
    line_count: int
    function_count: int
    stub_count: int
    todo_count: int
    functions: list[FunctionInfo] = field(default_factory=list)
    is_empty: bool = False
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analyzers
# ---------------------------------------------------------------------------


def is_stub_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[bool, str]:
    """Detect if a function is a stub.

    A function is considered a stub if:
    - Its body is just `pass`
    - Its body is just `raise NotImplementedError(...)`
    - Its body is just `return None` with a TODO in docstring
    """
    body = node.body
    if not body:
        return True, "empty body"

    # Single-statement bodies
    if len(body) == 1:
        stmt = body[0]
        if isinstance(stmt, ast.Pass):
            return True, "pass-only"
        if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
            func = stmt.exc.func
            if isinstance(func, ast.Name) and func.id == "NotImplementedError":
                return True, "raises NotImplementedError"
        if isinstance(stmt, ast.Return) and (
            stmt.value is None
            or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None)
        ):
            # Check docstring for TODO
            docstring = ast.get_docstring(node) or ""
            if "TODO" in docstring.upper() or "STUB" in docstring.upper():
                return True, "returns None with TODO"

    return False, ""


def analyze_module(path: Path) -> ModuleInfo:
    """Parse a Python module and extract structure info."""
    content = path.read_text(encoding="utf-8", errors="replace")
    line_count = content.count("\n") + 1

    info = ModuleInfo(path=path, line_count=line_count, function_count=0, stub_count=0, todo_count=0)

    if not content.strip():
        info.is_empty = True
        info.notes.append("empty file")
        return info

    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError as e:
        info.notes.append(f"syntax error: {e}")
        return info

    # Count TODO/FIXME in comments
    for match in re.finditer(r"#\s*(TODO|FIXME|STUB)", content, re.IGNORECASE):
        info.todo_count += 1

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info.function_count += 1
            is_stub, reason = is_stub_function(node)
            func_info = FunctionInfo(
                name=node.name,
                line=node.lineno,
                is_stub=is_stub,
                stub_reason=reason,
                has_todo=bool(
                    re.search(r"\b(TODO|FIXME|STUB)\b", ast.get_docstring(node) or "", re.IGNORECASE)
                ),
            )
            info.functions.append(func_info)
            if is_stub:
                info.stub_count += 1

    return info


def collect_modules() -> list[ModuleInfo]:
    """Walk SCAN_DIRS and analyze every .py file."""
    modules: list[ModuleInfo] = []
    for scan_dir in SCAN_DIRS:
        root = REPO_ROOT / scan_dir
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            # Skip __pycache__
            if any(part in SKIP_DIRS for part in py_file.parts):
                continue
            modules.append(analyze_module(py_file))
    return modules


def git_status() -> str:
    """Get current git status (branch, dirty state, last commit)."""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except subprocess.CalledProcessError:
        branch = "unknown"

    try:
        last_commit = subprocess.check_output(
            ["git", "log", "-1", "--oneline"],
            cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except subprocess.CalledProcessError:
        last_commit = "no commits"

    try:
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip())
    except subprocess.CalledProcessError:
        dirty = False

    state = "dirty" if dirty else "clean"
    return f"branch=`{branch}`, state={state}, last commit: `{last_commit}`"


def sql_table_inventory() -> list[str]:
    """Find all CREATE TABLE statements in .sql files."""
    tables: list[str] = []
    for sql_file in (REPO_ROOT / "app" / "db").rglob("*.sql"):
        content = sql_file.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", content, re.IGNORECASE):
            tables.append(f"{sql_file.name}::{match.group(1)}")
    return tables


def systemd_units() -> list[str]:
    """List systemd unit files."""
    units = []
    systemd_dir = REPO_ROOT / "systemd"
    if systemd_dir.exists():
        for unit_file in sorted(systemd_dir.glob("*.service")):
            units.append(unit_file.name)
        for unit_file in sorted(systemd_dir.glob("*.timer")):
            units.append(unit_file.name)
    return units


# ---------------------------------------------------------------------------
# Markdown generator
# ---------------------------------------------------------------------------


def generate_report(modules: list[ModuleInfo]) -> str:
    """Generate the REPO_STATE.md markdown report."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = git_status()
    tables = sql_table_inventory()
    units = systemd_units()

    total_funcs = sum(m.function_count for m in modules)
    total_stubs = sum(m.stub_count for m in modules)
    total_todos = sum(m.todo_count for m in modules)
    empty_files = [m for m in modules if m.is_empty]

    lines = [
        "# Repo State Snapshot",
        "",
        f"**Generated:** {now}",
        f"**Git:** {status}",
        "",
        "## Summary",
        "",
        f"- Python modules scanned: {len(modules)}",
        f"- Total functions: {total_funcs}",
        f"- Stub functions (need implementation): {total_stubs}",
        f"- TODO/FIXME comments: {total_todos}",
        f"- Empty files: {len(empty_files)}",
        f"- DB tables defined: {len(tables)}",
        f"- systemd units: {len(units)}",
        "",
        "## Module Inventory",
        "",
        "| Path | Lines | Functions | Stubs | TODOs | Status |",
        "|------|-------|-----------|-------|-------|--------|",
    ]

    for m in modules:
        rel_path = m.path.relative_to(REPO_ROOT)
        status_parts: list[str] = []
        if m.is_empty:
            status_parts.append("EMPTY")
        if m.stub_count > 0:
            status_parts.append(f"{m.stub_count} stubs")
        if m.todo_count > 0:
            status_parts.append(f"{m.todo_count} TODOs")
        if m.notes:
            status_parts.extend(m.notes)
        status_str = ", ".join(status_parts) if status_parts else "implemented"
        lines.append(
            f"| `{rel_path}` | {m.line_count} | {m.function_count} | "
            f"{m.stub_count} | {m.todo_count} | {status_str} |"
        )

    # Stub detail section
    stubs = [(m, f) for m in modules for f in m.functions if f.is_stub]
    if stubs:
        lines.extend([
            "",
            "## Stub Functions (must be implemented before use)",
            "",
            "| Module | Function | Line | Reason |",
            "|--------|----------|------|--------|",
        ])
        for m, f in stubs:
            rel_path = m.path.relative_to(REPO_ROOT)
            lines.append(f"| `{rel_path}` | `{f.name}` | {f.line} | {f.stub_reason} |")

    # Empty files section
    if empty_files:
        lines.extend([
            "",
            "## Empty Files (suspicious — should not exist)",
            "",
        ])
        for m in empty_files:
            rel_path = m.path.relative_to(REPO_ROOT)
            lines.append(f"- `{rel_path}`")

    # DB tables
    if tables:
        lines.extend([
            "",
            "## Database Tables",
            "",
        ])
        for t in tables:
            lines.append(f"- `{t}`")

    # systemd units
    if units:
        lines.extend([
            "",
            "## systemd Units",
            "",
        ])
        for u in units:
            lines.append(f"- `{u}`")

    # How to use this report
    lines.extend([
        "",
        "## How to Use This Report",
        "",
        "**For spec writers (LLM or human):** Before assuming a module exists or is implemented,",
        "check this report. If a function is listed as a stub, it MUST be implemented before",
        "any spec can rely on its behavior. Do not write specs that assume stub functionality.",
        "",
        "**For code reviewers:** Verify that the report matches the actual repo state.",
        "If it doesn't, the report is stale and should be regenerated.",
        "",
        "**Regenerate:** `python scripts/repo_state.py`",
        "",
        "---",
        f"_This file is auto-generated. Do not edit manually._",
    ])

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate repo state snapshot for LLM consumption")
    parser.add_argument(
        "--output", "-o",
        default=str(REPO_ROOT / "docs" / "REPO_STATE.md"),
        help="Output path (default: docs/REPO_STATE.md)",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    modules = collect_modules()
    report = generate_report(modules)
    output_path.write_text(report, encoding="utf-8")

    # Also print summary to stdout
    total_funcs = sum(m.function_count for m in modules)
    total_stubs = sum(m.stub_count for m in modules)
    print(f"✓ Repo state snapshot written to: {output_path}")
    print(f"  Modules: {len(modules)}")
    print(f"  Functions: {total_funcs}")
    print(f"  Stubs: {total_stubs}")
    print(f"  TODOs: {sum(m.todo_count for m in modules)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

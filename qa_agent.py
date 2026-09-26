"""Local QA checks for Python projects. Commands are fixed and never use a shell."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(
    os.environ.get("QA_AGENT_STATE_DIR", Path.home() / ".autonomous_qa_agent")
)
HISTORY_FILE = STATE_DIR / "history.json"
IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
}
MAX_OUTPUT_CHARS = 30_000
HISTORY_LIMIT = 30
_history_lock = threading.Lock()


class ProjectPathError(ValueError):
    """Raised when a requested project path is invalid."""


def resolve_project_path(raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ProjectPathError("Enter a project folder path.")
    path = Path(raw_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ProjectPathError("That path does not exist or is not a folder.")
    return path


def _walk_project(root: Path):
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in IGNORED_DIRS and not (Path(current) / d).is_symlink()
        )
        yield Path(current), sorted(files)


def inspect_project(raw_path: str) -> dict[str, Any]:
    root = resolve_project_path(raw_path)
    file_count = 0
    directory_count = 0
    total_size_bytes = 0
    file_type_counts: dict[str, int] = {}
    python_files: list[Path] = []
    test_files: list[Path] = []
    syntax_issues: list[dict[str, str]] = []
    largest_files: list[dict[str, Any]] = []
    for folder, names in _walk_project(root):
        if folder != root:
            directory_count += 1
        for name in names:
            file_path = folder / name
            if file_path.is_symlink():
                continue
            file_count += 1
            file_size = file_path.stat().st_size
            total_size_bytes += file_size
            extension = file_path.suffix.lower()
            file_type_counts[extension] = file_type_counts.get(extension, 0) + 1
            largest_files.append(
                {"path": str(file_path.relative_to(root)), "size_bytes": file_size}
            )
            if file_path.suffix.lower() != ".py":
                continue
            python_files.append(file_path)
            is_test = (
                name.startswith("test_")
                or name.endswith("_test.py")
                or "tests" in file_path.relative_to(root).parts
            )
            if is_test:
                test_files.append(file_path)
            try:
                ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
            except UnicodeDecodeError:
                syntax_issues.append(
                    {
                        "file": str(file_path.relative_to(root)),
                        "message": "File is not valid UTF-8 text.",
                    }
                )
            except (SyntaxError, ValueError) as error:
                syntax_issues.append(
                    {
                        "file": str(file_path.relative_to(root)),
                        "message": str(error),
                    }
                )
    largest_files.sort(key=lambda item: item["size_bytes"], reverse=True)
    return {
        "project_path": str(root),
        "file_count": file_count,
        "directory_count": directory_count,
        "total_size_bytes": total_size_bytes,
        "file_type_counts": dict(sorted(file_type_counts.items())),
        "largest_files": largest_files[:10],
        "python_file_count": len(python_files),
        "test_file_count": len(test_files),
        "syntax_issue_count": len(syntax_issues),
        "syntax_issues": syntax_issues[:100],
        "available_checks": ["pytest", "ruff", "python_syntax"],
    }


def _run_command(name: str, command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
        )
        output = (
            completed.stdout
            + ("\n" if completed.stdout and completed.stderr else "")
            + completed.stderr
        ).strip()
        status = "passed" if completed.returncode == 0 else "failed"
        return {
            "name": name,
            "status": status,
            "exit_code": completed.returncode,
            "duration_seconds": round(time.perf_counter() - started, 2),
            "output": output[-MAX_OUTPUT_CHARS:],
            "truncated": len(output) > MAX_OUTPUT_CHARS,
        }
    except subprocess.TimeoutExpired as error:
        parts = [part for part in (error.stdout, error.stderr) if part]
        output = "\n".join(
            part.decode(errors="replace") if isinstance(part, bytes) else part
            for part in parts
        )
        return {
            "name": name,
            "status": "timeout",
            "exit_code": None,
            "duration_seconds": round(time.perf_counter() - started, 2),
            "output": (output + f"\nCheck stopped after {timeout} seconds.")[
                -MAX_OUTPUT_CHARS:
            ],
            "truncated": False,
        }
    except OSError as error:
        return {
            "name": name,
            "status": "error",
            "exit_code": None,
            "duration_seconds": round(time.perf_counter() - started, 2),
            "output": str(error),
            "truncated": False,
        }


def _project_python(root: Path) -> str:
    candidates = (
        root / ".venv" / "Scripts" / "python.exe",
        root / "venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        root / "venv" / "bin" / "python",
    )
    return str(
        next((candidate for candidate in candidates if candidate.is_file()), Path(sys.executable))
    )


def suggest_next_steps(results: list[dict[str, Any]]) -> list[str]:
    suggestions: list[str] = []
    for result in results:
        output = result.get("output", "").lower()
        name = result.get("name")
        if name == "pytest" and result.get("status") in {"failed", "unavailable"}:
            if "no module named pytest" in output or "no module named 'pytest'" in output:
                suggestions.append(
                    "Install the project's test dependencies, then run the checks again."
                )
            elif "no tests ran" in output:
                suggestions.append(
                    "Pytest found no tests. Add files named test_*.py or *_test.py."
                )
            else:
                suggestions.append(
                    "Open the failing test output, reproduce the first failure, "
                    "and add a regression test after fixing it."
                )
        elif name == "ruff" and result.get("status") in {"failed", "error", "unavailable"}:
            if "no module named ruff" in output:
                suggestions.append(
                    "Ruff is not installed in this Python environment. Install the "
                    "optional dev tools to enable lint checks."
                )
            else:
                suggestions.append(
                    "Review Ruff's file and rule codes; apply a targeted fix, "
                    "then rerun linting."
                )
        elif result.get("status") == "timeout":
            suggestions.append(
                f"The {name} check exceeded its time limit. Check for a hanging "
                "test or increase the timeout setting."
            )
    if not suggestions and results and all(
        result.get("status") == "passed" for result in results
    ):
        suggestions.append(
            "All selected checks passed. Keep the checks in your regular "
            "development workflow."
        )
    return list(dict.fromkeys(suggestions))


def _read_history() -> list[dict[str, Any]]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def get_history() -> list[dict[str, Any]]:
    with _history_lock:
        return _read_history()


def run_checks(
    raw_path: str,
    selected_checks: list[str] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    root = resolve_project_path(raw_path)
    allowed = {"pytest", "ruff", "python_syntax"}
    checks = list(dict.fromkeys(selected_checks or ["pytest", "ruff", "python_syntax"]))
    unknown = set(checks) - allowed
    if unknown:
        raise ValueError(f"Unsupported check(s): {', '.join(sorted(unknown))}")
    timeout = max(5, min(int(timeout), 600))
    check_results: list[dict[str, Any]] = []
    project_python = _project_python(root)
    for check in checks:
        if check == "pytest":
            result = _run_command(
                "pytest",
                [project_python, "-m", "pytest", "-q"],
                root,
                timeout,
            )
            if (
                "no module named" in result["output"].lower()
                and "pytest" in result["output"].lower()
            ):
                result["status"] = "unavailable"
            check_results.append(result)
        elif check == "ruff":
            result = _run_command(
                "ruff",
                [
                    project_python,
                    "-m",
                    "ruff",
                    "check",
                    ".",
                    "--output-format",
                    "concise",
                ],
                root,
                timeout,
            )
            if (
                "no module named" in result["output"].lower()
                and "ruff" in result["output"].lower()
            ):
                result["status"] = "unavailable"
            check_results.append(result)
        elif check == "python_syntax":
            inspection = inspect_project(str(root))
            issues = inspection["syntax_issues"]
            output = (
                "Python syntax check passed."
                if not issues
                else "\n".join(f"{item['file']}: {item['message']}" for item in issues)
            )
            check_results.append(
                {
                    "name": "python_syntax",
                    "status": "passed" if not issues else "failed",
                    "exit_code": 0 if not issues else 1,
                    "duration_seconds": 0,
                    "output": output,
                    "truncated": False,
                }
            )
    failed = [
        item for item in check_results if item["status"] in {"failed", "error", "timeout"}
    ]
    unavailable = [item for item in check_results if item["status"] == "unavailable"]
    overall = "failed" if failed else "warning" if unavailable else "passed"
    report = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_path": str(root),
        "status": overall,
        "checks": check_results,
        "suggestions": suggest_next_steps(check_results),
        "duration_seconds": round(
            sum(item["duration_seconds"] for item in check_results), 2
        ),
    }
    with _history_lock:
        history = _read_history()
        history.insert(0, report)
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(
                json.dumps(history[:HISTORY_LIMIT], indent=2),
                encoding="utf-8",
            )
        except OSError:
            # A successful run should still be returned if history storage is unavailable.
            pass
    return report

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from app import _is_valid_basic_auth
from qa_agent import (
    ProjectPathError,
    _project_python,
    inspect_project,
    resolve_project_path,
    suggest_next_steps,
)


class QAAgentTests(unittest.TestCase):
    def test_basic_auth_accepts_matching_credentials(self) -> None:
        encoded = base64.b64encode(b"owner:secret").decode("ascii")

        self.assertTrue(_is_valid_basic_auth(f"Basic {encoded}", "owner", "secret"))

    def test_basic_auth_rejects_invalid_or_wrong_credentials(self) -> None:
        encoded = base64.b64encode(b"owner:wrong").decode("ascii")

        self.assertFalse(_is_valid_basic_auth(None, "owner", "secret"))
        self.assertFalse(_is_valid_basic_auth("Basic !!!", "owner", "secret"))
        self.assertFalse(_is_valid_basic_auth(f"Basic {encoded}", "owner", "secret"))

    def test_resolve_project_path_rejects_empty_input(self) -> None:
        with self.assertRaisesRegex(ProjectPathError, "Enter a project folder path"):
            resolve_project_path("  ")

    def test_resolve_project_path_rejects_missing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProjectPathError, "does not exist"):
                resolve_project_path(str(Path(directory) / "missing"))

    def test_inspect_project_counts_python_and_skips_ignored_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / ".venv" / "Lib").mkdir(parents=True)
            (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "tests" / "test_main.py").write_text(
                "def test_ok():\n    assert True\n",
                encoding="utf-8",
            )
            (root / ".venv" / "Lib" / "ignored.py").write_text(
                "invalid python !",
                encoding="utf-8",
            )
            (root / "notes.txt").write_text("hello", encoding="utf-8")

            result = inspect_project(str(root))

            self.assertEqual(result["file_count"], 3)
            self.assertEqual(result["python_file_count"], 2)
            self.assertEqual(result["test_file_count"], 1)
            self.assertEqual(result["syntax_issue_count"], 0)

    def test_inspect_project_reports_python_syntax_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

            result = inspect_project(str(root))

            self.assertEqual(result["syntax_issue_count"], 1)
            self.assertEqual(result["syntax_issues"][0]["file"], "broken.py")
            self.assertIn("invalid syntax", result["syntax_issues"][0]["message"])

    def test_inspect_project_includes_rich_file_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
            (root / "tests" / "test_main.py").write_text(
                "def test_ok():\n    assert True\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")

            result = inspect_project(str(root))

            self.assertEqual(result["file_type_counts"][".py"], 2)
            self.assertEqual(result["file_type_counts"][".md"], 1)
            self.assertGreater(result["total_size_bytes"], 0)
            self.assertEqual(result["directory_count"], 2)

    def test_project_python_prefers_local_virtual_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / ".venv" / "Scripts" / "python.exe"
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()

            self.assertEqual(_project_python(root), str(interpreter))

    def test_suggest_next_steps_for_missing_pytest(self) -> None:
        results = [{"name": "pytest", "status": "failed", "output": "No module named pytest"}]

        suggestions = suggest_next_steps(results)

        self.assertEqual(len(suggestions), 1)
        self.assertIn("Install", suggestions[0])

    def test_suggest_next_steps_when_all_checks_pass(self) -> None:
        results = [{"name": "python_syntax", "status": "passed", "output": "OK"}]

        self.assertIn("All selected checks passed.", suggest_next_steps(results)[0])

    def test_suggest_next_steps_when_pytest_is_unavailable(self) -> None:
        results = [{"name": "pytest", "status": "unavailable", "output": "No module named pytest"}]

        self.assertIn("Install", suggest_next_steps(results)[0])


if __name__ == "__main__":
    unittest.main()

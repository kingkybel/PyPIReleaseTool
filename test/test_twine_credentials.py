"""Unit tests for Twine credential resolution."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypi_release_tool.release_tool import PyPIReleaseTool


class TwineCredentialsTests(unittest.TestCase):
    """Tests for ~/.secrets parsing and runtime credential fallback."""

    def test_load_twine_credentials_from_secrets_file(self) -> None:
        """Credentials are parsed from ~/.secrets with export and quoted values."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_home = Path(temp_dir)
            secrets_file = temp_home / ".secrets"
            secrets_file.write_text(
                "\n".join(
                    [
                        "# comment",
                        "export TWINE_USERNAME='__token__'",
                        'TWINE_PASSWORD = "pypi-token-123"',
                    ]
                )
            )

            with patch.object(Path, "home", return_value=temp_home):
                username, password = PyPIReleaseTool._load_twine_credentials_from_secrets()

            self.assertEqual(username, "__token__")
            self.assertEqual(password, "pypi-token-123")

    def test_resolve_twine_credentials_uses_secrets_without_prompt(self) -> None:
        """When both values exist in ~/.secrets, prompt is skipped."""
        tool = PyPIReleaseTool([])

        with patch.object(tool, "_load_twine_credentials_from_secrets", return_value=("user-from-secrets", "pass-from-secrets")):
            with patch("getpass.getpass") as mock_getpass:
                username, password = tool._resolve_twine_credentials()

        self.assertEqual(username, "user-from-secrets")
        self.assertEqual(password, "pass-from-secrets")
        mock_getpass.assert_not_called()

    def test_resolve_twine_credentials_prompts_and_warns_when_missing_in_secrets(self) -> None:
        """Missing ~/.secrets values triggers warning log and secure prompt."""
        tool = PyPIReleaseTool([])

        with patch.object(tool, "_load_twine_credentials_from_secrets", return_value=(None, None)):
            with patch.dict(os.environ, {"TWINE_USERNAME": "env-user"}, clear=False):
                with patch.object(tool, "log") as mock_log:
                    with patch("getpass.getpass", return_value="prompted-pass"):
                        username, password = tool._resolve_twine_credentials()

        self.assertEqual(username, "env-user")
        self.assertEqual(password, "prompted-pass")
        mock_log.assert_called()
        self.assertIn("not found in ~/.secrets", mock_log.call_args.args[0])

    def test_resolve_twine_credentials_defaults_username_to_token(self) -> None:
        """Username defaults to __token__ when unavailable and prompt is used."""
        tool = PyPIReleaseTool([])

        with patch.object(tool, "_load_twine_credentials_from_secrets", return_value=(None, None)):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TWINE_USERNAME", None)
                with patch("getpass.getpass", return_value="prompted-pass"):
                    username, password = tool._resolve_twine_credentials()

        self.assertEqual(username, "__token__")
        self.assertEqual(password, "prompted-pass")

    def test_resolve_twine_credentials_raises_when_prompt_empty(self) -> None:
        """Empty prompt input fails with clear error."""
        tool = PyPIReleaseTool([])

        with patch.object(tool, "_load_twine_credentials_from_secrets", return_value=(None, None)):
            with patch("getpass.getpass", return_value="   "):
                with self.assertRaisesRegex(ValueError, "required for upload"):
                    tool._resolve_twine_credentials()


if __name__ == "__main__":
    unittest.main()

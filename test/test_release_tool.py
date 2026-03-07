"""Unit tests for the PyPIReleaseTool class."""

from __future__ import annotations

import unittest
from unittest.mock import patch, mock_open

from pypi_release_tool.release_tool import PyPIReleaseTool


class ReleaseToolTests(unittest.TestCase):
    """Tests for the PyPIReleaseTool class."""

    def test_get_version_components(self):
        """Test that version components are parsed correctly."""
        self.assertEqual(PyPIReleaseTool.get_version_components("1.2.3"), (1, 2, 3))
        self.assertEqual(PyPIReleaseTool.get_version_components("v1.2.3"), (1, 2, 3))
        with self.assertRaises(ValueError):
            PyPIReleaseTool.get_version_components("1.2")
        with self.assertRaises(ValueError):
            PyPIReleaseTool.get_version_components("a.b.c")

    def test_increment_version(self):
        """Test that version is incremented correctly."""
        self.assertEqual(PyPIReleaseTool.increment_version("1.2.3", "patch"), "1.2.4")
        self.assertEqual(PyPIReleaseTool.increment_version("1.2.3", "minor"), "1.3.0")
        self.assertEqual(PyPIReleaseTool.increment_version("1.2.3", "major"), "2.0.0")
        with self.assertRaises(ValueError):
            PyPIReleaseTool.increment_version("1.2.3", "invalid")

    def test_get_package_name(self):
        """Test that package name is read from pyproject.toml."""
        toml_content = """
[project]
name = "my-package"
"""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value=toml_content):
                self.assertEqual(PyPIReleaseTool.get_package_name(), "my-package")

    def test_get_package_dir(self):
        """Test that package directory is read from pyproject.toml."""
        toml_content = """
[tool.setuptools.dynamic.version]
attr = "my_package.__version__"
"""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value=toml_content):
                self.assertEqual(PyPIReleaseTool.get_package_dir(), "my_package")


if __name__ == "__main__":
    unittest.main()

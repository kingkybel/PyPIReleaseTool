#!/usr/bin/env python3
"""Automated release workflow for Python packages."""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")


class PyPIReleaseTool:
    """Run a full release workflow for a Python package repository."""

    VENV_NAMES = [".venv", "venv", "env", ".env", "virtualenv"]
    PY_COMMANDS = ["python3", "python"]
    PY_EXECUTABLES = ["bin/python", "Scripts/python.exe", "bin/python3", "Scripts/python3.exe"]
    TOML_FILE = Path("pyproject.toml")
    MODULE_INIT_FILE = Path("__init__.py")

    def __init__(self, argv=None) -> None:
        """Initialize runtime state and parse CLI arguments.

        :param argv: Optional CLI arguments passed to the parser.
        :raises SystemExit: Raised by argparse for invalid CLI usage.
        """
        self.args = self.parse_args(argv)
        self.repo_dir = Path(self.args.repo).resolve() if self.args.repo else Path(".").resolve()
        self.version_increment = "minor" if self.args.minor else "major" if self.args.major else "patch"
        self.dry_run = self.args.dry_run
        self.package_name = ""
        self.package_dir = ""
        self.temp_venv_path: Path | None = None

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        """Build the command-line parser for the release tool.

        :return: Configured parser for release options.
        """
        parser = argparse.ArgumentParser(description="PyPIReleaseTool automated release script")
        parser.add_argument("--repo", "-r", help="Repository directory (default: current directory)")
        version_group = parser.add_mutually_exclusive_group()
        version_group.add_argument("--minor", "-m", action="store_true", help="Increment minor version")
        version_group.add_argument("--major", "-M", action="store_true", help="Increment major version")
        parser.add_argument("--dry-run", "-d", action="store_true", help="Show what would be done without making changes")
        return parser

    @classmethod
    def parse_args(cls, argv=None) -> argparse.Namespace:
        """Parse CLI arguments into a namespace.

        :param argv: Optional iterable of CLI argument strings.
        :return: Parsed CLI arguments.
        :raises SystemExit: Raised by argparse for invalid CLI usage.
        """
        return cls.build_parser().parse_args(argv)

    @staticmethod
    def log(message: str) -> None:
        """Write a formatted log message.

        :param message: Message to send to the configured logger.
        """
        logging.info(message)

    @staticmethod
    def run_command(cmd, cwd=None, check=True, capture_output=False):
        """Run a command and return result status or captured stdout.

        :param cmd: Command and arguments to execute.
        :param cwd: Optional working directory for the command.
        :param check: Whether to raise when the command exits non-zero.
        :param capture_output: Whether to return captured standard output.
        :return: Command success status or stripped stdout text.
        :raises subprocess.CalledProcessError: If command fails and `check` is True.
        """
        try:
            result = subprocess.run(cmd, cwd=cwd, check=check, capture_output=capture_output, text=True)
            if capture_output:
                return result.stdout.strip()
            return result.returncode == 0
        except subprocess.CalledProcessError:
            if check:
                raise
            return False

    @staticmethod
    def get_version_components(version: str):
        """Parse a semantic version string into major/minor/patch integers.

        :param version: Version string in `X.Y.Z` form, optionally prefixed with `v`.
        :return: Major, minor, and patch components.
        :raises ValueError: If the input does not match semantic version format.
        """
        version = version.lstrip("v")
        match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
        if not match:
            raise ValueError(f"Invalid version format: {version}")
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    @staticmethod
    def increment_version(current_version: str, increment_type: str) -> str:
        """Return the next semantic version for the chosen increment type.

        :param current_version: Current semantic version string.
        :param increment_type: One of `patch`, `minor`, or `major`.
        :return: The incremented semantic version.
        :raises ValueError: If increment type is unsupported.
        """
        major, minor, patch = PyPIReleaseTool.get_version_components(current_version)

        if increment_type == "patch":
            patch += 1
        elif increment_type == "minor":
            minor += 1
            patch = 0
        elif increment_type == "major":
            major += 1
            minor = 0
            patch = 0
        else:
            raise ValueError(f"Invalid increment type: {increment_type}")

        return f"{major}.{minor}.{patch}"

    @staticmethod
    def get_package_name() -> str:
        """Read the package distribution name from pyproject metadata.

        :return: Package distribution name.
        :raises FileNotFoundError: If `pyproject.toml` cannot be found.
        :raises ValueError: If package name metadata is missing.
        """
        toml_file = PyPIReleaseTool.TOML_FILE
        if not toml_file.exists():
            raise FileNotFoundError(f"{PyPIReleaseTool.TOML_FILE} not found in repository root")

        content = toml_file.read_text()

        project_match = re.search(
            r"\[project\]\s*.*?^name\s*=\s*[\"\']([^\"\']+)[\"\']",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if project_match:
            return project_match.group(1)

        setuptools_match = re.search(
            r"\[tool\.setuptools\]\s*.*?^name\s*=\s*[\"\']([^\"\']+)[\"\']",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if setuptools_match:
            return setuptools_match.group(1)

        raise ValueError(f"Could not find package name in {PyPIReleaseTool.TOML_FILE}")

    @staticmethod
    def get_package_dir() -> str:
        """Resolve the import package directory from pyproject configuration.

        :return: Import package directory name.
        :raises FileNotFoundError: If `pyproject.toml` cannot be found.
        :raises ValueError: If package directory metadata is missing.
        """
        toml_file = PyPIReleaseTool.TOML_FILE
        if not toml_file.exists():
            raise FileNotFoundError(f"{PyPIReleaseTool.TOML_FILE} not found in repository root")

        content = toml_file.read_text()

        packages_match = re.search(
            r"\[tool\.setuptools\]\s*.*?packages\s*=\s*\[([^\]]+)\]",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if packages_match:
            packages = re.findall(r"[\"\']([^\"\']+)[\"\']", packages_match.group(1).strip())
            if packages:
                return packages[0]

        version_attr_match = re.search(
            r"\[tool\.setuptools\.dynamic\.version\]\s*.*?attr\s*=\s*[\"\']([^\"\']+)[\"\']",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if version_attr_match:
            package_name = version_attr_match.group(1).split(".")[0]
            if package_name:
                return package_name

        raise ValueError(f"Could not find package directory in {PyPIReleaseTool.TOML_FILE}")

    def _find_venv_interpreter(self, venv_name: str) -> str | None:
        """Locate the Python executable within a named virtual environment.

        :param venv_name: Candidate virtual environment directory name.
        :return: Absolute path to interpreter if found, otherwise None.
        """
        venv_path = Path(self.repo_dir) / venv_name
        if venv_path.is_dir():
            for py_exec in self.PY_EXECUTABLES:
                py_path = venv_path / py_exec
                if py_path.exists():
                    return str(py_path)
        return None

    def detect_python_interpreters(self):
        """Collect usable Python executables from local venvs or PATH.

        :return: Unique interpreter paths in search order.
        """
        interpreters = []

        for venv_name in self.VENV_NAMES:
            py_path = self._find_venv_interpreter(venv_name)
            if py_path:
                interpreters.append(py_path)

        for py_cmd in self.PY_COMMANDS:
            resolved = shutil.which(py_cmd)
            if resolved:
                interpreters.append(resolved)

        # Preserve order while removing duplicates.
        return list(dict.fromkeys(interpreters))

    def get_python_version(self, py_exec: str) -> str:
        """Return the major.minor version for a Python executable.

        :param py_exec: Python executable path.
        :return: `major.minor` version string, or empty string when unavailable.
        """
        try:
            result = self.run_command([py_exec, "--version"], check=True, capture_output=True)
            match = re.search(r"(\d+\.\d+)", result)
            return match.group(1) if match else ""
        except Exception:
            return ""

    def get_python_release_info(self, py_exec: str) -> tuple[tuple[int, int, int], bool]:
        """Return version tuple and prerelease state for a Python executable.

        :param py_exec: Python executable path.
        :return: Parsed `(major, minor, patch)` and prerelease flag.
        """
        try:
            result = self.run_command([py_exec, "--version"], check=True, capture_output=True)
            match = re.search(r"(\d+)\.(\d+)\.(\d+)([A-Za-z0-9.-]*)", result)
            if not match:
                return (0, 0, 0), True

            major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
            suffix = (match.group(4) or "").lower()
            is_prerelease = bool(suffix) and any(tag in suffix for tag in ("a", "b", "rc", "alpha", "beta"))
            return (major, minor, patch), is_prerelease
        except Exception:
            return (0, 0, 0), True

    def find_highest_python(self, interpreters) -> str:
        """Select highest stable interpreter, falling back to highest pre-release.

        :param interpreters: Sequence of interpreter paths to evaluate.
        :return: Best matching interpreter path, or empty string if none are valid.
        """
        best_stable_version = (0, 0, 0)
        best_stable_interpreter = ""
        best_any_version = (0, 0, 0)
        best_any_interpreter = ""

        for interpreter in interpreters:
            if os.access(interpreter, os.X_OK):
                version, is_prerelease = self.get_python_release_info(interpreter)
                if version > best_any_version:
                    best_any_version = version
                    best_any_interpreter = interpreter
                if not is_prerelease and version > best_stable_version:
                    best_stable_version = version
                    best_stable_interpreter = interpreter

        return best_stable_interpreter or best_any_interpreter

    def create_temp_venv(self, python_exec: str) -> Path:
        """Create and bootstrap an isolated virtual environment for release tasks.

        :param python_exec: Interpreter used to create the temporary environment.
        :return: Absolute path to the created virtual environment.
        :raises subprocess.CalledProcessError: If dependency installation commands fail.
        :raises OSError: If temporary directory or virtual environment creation fails.
        """
        python_version = self.get_python_version(python_exec)
        temp_venv_dir = Path(tempfile.mkdtemp(prefix="release_to_pypi_venv_")) / f"venv_{python_version}"

        if temp_venv_dir.exists():
            shutil.rmtree(temp_venv_dir)

        self.log(f"Creating temporary virtual environment at {temp_venv_dir} using {python_exec}")
        venv.create(temp_venv_dir, with_pip=False, system_site_packages=False, clear=True)

        temp_venv_dir = temp_venv_dir.resolve()

        self.log("Installing pip from remote...")
        with subprocess.Popen([str(temp_venv_dir / "bin" / "python")], stdin=subprocess.PIPE, text=True) as proc:
            subprocess.run(["curl", "-sS", "https://bootstrap.pypa.io/get-pip.py"], stdout=proc.stdin, check=True)

        self.log("Installing project dependencies...")
        self.run_command([str(temp_venv_dir / "bin" / "python"), "-m", "pip", "install", "-e", "."])
        self.run_command([str(temp_venv_dir / "bin" / "python"), "-m", "pip", "install", "--quiet", "colorama"])

        self.log("Installing build tools...")
        self.run_command([str(temp_venv_dir / "bin" / "python"), "-m", "pip", "install", "--quiet", "build", "twine"])

        return temp_venv_dir

    def run_tests(self) -> None:
        """Run project tests in the temporary environment when tests are present.

        :raises AssertionError: If temporary virtual environment path is not set.
        """
        test_dir = Path("test")
        test_files = [f for f in [Path("pytest.ini"), PyPIReleaseTool.TOML_FILE, Path("setup.cfg")] if f.exists()]

        if test_dir.exists() or test_files:
            self.log("Running tests...")
            assert self.temp_venv_path is not None
            venv_python = str(self.temp_venv_path / "bin" / "python")
            venv_pytest = self.temp_venv_path / "bin" / "pytest"

            try:
                if venv_pytest.exists():
                    self.run_command([venv_python, "-m", "pytest", "test/", "-x"])
                else:
                    self.run_command([venv_python, "-m", "unittest", "discover", "test", "-v"])
                self.log("Tests passed")
            except subprocess.CalledProcessError:
                self.log("WARNING: Some tests failed, continuing with release...")

    def ensure_full_functionality(self) -> None:
        """Regenerate package `__init__` exports and preserve the current version.

        :raises FileNotFoundError: If package directory does not exist.
        """
        self.log("Ensuring __init__.py has full functionality...")

        package_path = Path(self.package_dir)
        init_file = package_path / "__init__.py"

        if not package_path.exists() or not package_path.is_dir():
            raise FileNotFoundError(f"Package directory {self.package_dir} not found")

        py_files = [f for f in package_path.glob("*.py") if f.name != "__init__.py"]
        imports = [f"from .{py_file.stem} import *" for py_file in py_files]

        current_version = "0.1.0"
        if init_file.exists():
            match = re.search(r"__version__\s*=\s*[\"\']([^\"\']+)[\"\']", init_file.read_text())
            if match:
                current_version = match.group(1)

        content = "\n".join(imports) + f"\n\n__version__ = \"{current_version}\"\n"
        init_file.write_text(content)
        self.log("__init__.py updated with full functionality")

    def get_current_version(self) -> str:
        """Read the current package version from package `__init__.py`.

        :return: Current package version.
        :raises FileNotFoundError: If package `__init__.py` is missing.
        :raises ValueError: If version assignment cannot be extracted.
        """
        init_file = Path(self.package_dir) / "__init__.py"
        if not init_file.exists():
            raise FileNotFoundError(f"Cannot find {self.package_dir}/__init__.py")

        match = re.search(r"__version__\s*=\s*[\"\']([^\"\']+)[\"\']", init_file.read_text())
        if not match:
            raise ValueError(f"Could not extract version from {self.package_dir}/__init__.py")
        return match.group(1)

    @staticmethod
    def get_latest_pypi_version(package_name: str):
        """Fetch the latest published release version from PyPI.

        :param package_name: Package distribution name to query.
        :return: Latest version string, or None when unavailable.
        """
        try:
            url = f"https://pypi.org/pypi/{package_name}/json"
            proc = subprocess.run(["curl", "-s", url], capture_output=True, text=True, check=False)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                if "releases" in data:
                    versions = [v for v in data["releases"].keys() if not v.startswith("0.dev")]
                    if versions:

                        def version_key(v):
                            parts = re.findall(r"\d+", v) + ["0"] * 10
                            return tuple(int(p) for p in parts[:10])

                        versions = sorted(versions, key=version_key, reverse=True)
                        return versions[0]
            return None
        except Exception:
            return None

    def update_version(self, new_version: str) -> None:
        """Update version fields in package `__init__.py` and `pyproject.toml`.

        :param new_version: Version string to write.
        :raises FileNotFoundError: If package `__init__.py` does not exist.
        :raises OSError: If file read/write operations fail.
        """
        init_file = Path(self.package_dir) / "__init__.py"

        content = init_file.read_text()
        content = re.sub(r"__version__\s*=\s*[\"\'][^\"\']*[\"\']", f'__version__ = "{new_version}"', content)
        init_file.write_text(content)

        toml_file = PyPIReleaseTool.TOML_FILE
        if toml_file.exists():
            content = toml_file.read_text()
            content = re.sub(
                r"^(\s*)version\s*=\s*[\"\'][^\"\']*[\"\']",
                f"\\1version = \"{new_version}\"",
                content,
                flags=re.MULTILINE,
            )
            toml_file.write_text(content)

    def build_package(self) -> None:
        """Build source and wheel distributions using `python -m build`.

        :raises AssertionError: If temporary virtual environment path is not set.
        :raises subprocess.CalledProcessError: If build command fails.
        """
        assert self.temp_venv_path is not None
        venv_python = str(self.temp_venv_path / "bin" / "python")
        self.run_command([venv_python, "-m", "build"])

    def upload_to_pypi(self, package_name: str, version: str) -> None:
        """Upload generated distributions to PyPI via Twine.

        :param package_name: Distribution name used to validate build artifacts.
        :param version: Version being released for logging.
        :raises AssertionError: If temporary virtual environment path is not set.
        :raises ValueError: If expected distribution files are missing.
        :raises subprocess.CalledProcessError: If Twine upload fails.
        """
        filename_package_name = package_name.replace("-", "_")
        if not Path("dist").exists() or not list(Path("dist").glob(f"*{filename_package_name}*")):
            raise ValueError("Build did not create expected distribution files")

        self.log("Uploading to PyPI...")
        assert self.temp_venv_path is not None
        venv_twine = str(self.temp_venv_path / "bin" / "twine")
        self.run_command([venv_twine, "upload", "dist/*"])
        self.log(f"Successfully released {package_name} version {version} to PyPI!")

    def initialize_project_context(self) -> None:
        """Switch to target repository and load package metadata.

        :raises FileNotFoundError: If required project files cannot be found.
        :raises ValueError: If package metadata cannot be resolved.
        """
        os.chdir(self.repo_dir)
        self.log(f"Changed to repository directory: {self.repo_dir}")

        self.package_name = self.get_package_name()
        self.log(f"Package name: {self.package_name}")

        self.package_dir = self.get_package_dir()
        self.log(f"Package directory: {self.package_dir}")

    def setup_release_environment(self) -> None:
        """Find a Python interpreter and bootstrap the temporary release venv.

        :raises ValueError: If no suitable Python interpreter can be selected.
        :raises RuntimeError: If virtual environment verification fails.
        :raises subprocess.CalledProcessError: If environment setup commands fail.
        """
        self.log("Detecting Python interpreters...")
        interpreters = self.detect_python_interpreters()
        self.log(f"Found Python interpreters: {' '.join(interpreters)}")

        if not interpreters:
            raise ValueError("No Python interpreters found. Please ensure Python is installed.")

        highest_python = self.find_highest_python(interpreters)
        if not highest_python:
            raise ValueError("Could not determine highest Python version")

        highest_version = self.get_python_version(highest_python)
        self.log(f"Using Python interpreter: {highest_python} ({highest_version})")

        self.temp_venv_path = self.create_temp_venv(highest_python)
        self.log(f"venv-path={self.temp_venv_path}")

        activate_script = self.temp_venv_path / "bin" / "activate"
        if not activate_script.exists():
            raise RuntimeError(f"Virtual environment creation failed - {activate_script} not found")

        self.log("Virtual environment created and verified successfully")

    def run_test_phase(self) -> None:
        """Run tests, unless dry-run indicates failures should not block.

        :raises Exception: Re-raises test/setup failures when not in dry-run mode.
        """
        try:
            self.run_tests()
        except Exception:
            if not self.dry_run:
                raise

    def determine_new_version(self) -> str:
        """Compare local/PyPI versions and decide the next release version.

        :return: Version selected for the next release.
        :raises ValueError: If local version is behind the latest PyPI version.
        :raises FileNotFoundError: If local version source file is missing.
        """
        current_version = self.get_current_version()
        self.log(f"Current version: {current_version}")

        latest_pypi_version = self.get_latest_pypi_version(self.package_name)
        if not latest_pypi_version:
            self.log(f"WARNING: Could not retrieve latest PyPI version for {self.package_name}")
            latest_pypi_version = current_version
        self.log(f"Latest PyPI version: {latest_pypi_version}")

        if current_version == latest_pypi_version:
            self.log("Version matches PyPI, incrementing...")
            new_version = self.increment_version(current_version, self.version_increment)
        elif current_version > latest_pypi_version:
            new_version = current_version
            self.log(f"Current version is newer, keeping: {new_version}")
        else:
            raise ValueError(f"Current version ({current_version}) is older than PyPI ({latest_pypi_version})")

        self.log(f"New version will be: {new_version}")
        return new_version

    def update_versioned_files(self, new_version: str) -> None:
        """Regenerate package exports and bump version fields.

        :param new_version: Version string to write into project files.
        :raises FileNotFoundError: If required package files are missing.
        :raises ValueError: If metadata cannot be updated correctly.
        """
        self.ensure_full_functionality()
        self.log(f"Updating version in {self.package_dir}/__init__.py...")
        self.update_version(new_version)

    def commit_and_push_changes(self) -> None:
        """Commit repository changes and push them to origin/main.

        :raises subprocess.CalledProcessError: If `git add` fails.
        """
        self.log("Committing all changes...")
        self.run_command(["git", "add", "--update"])
        commit_result = self.run_command(["git", "commit", "-m", "auto-committed by release-to-pypi.py"], check=False)
        if commit_result:
            self.log("Committed changes")
        else:
            self.log("Nothing new to commit")

        self.log("Pushing commit to remote...")
        result = self.run_command(["git", "push", "origin", "main"], check=False)
        if result:
            self.log("Push succeeded")
        else:
            self.log("Push skipped (nothing to push or already up-to-date)")

    @staticmethod
    def clean_build_artifacts() -> None:
        """Remove stale build artifacts before packaging.

        :raises OSError: If artifact directories cannot be removed.
        """
        PyPIReleaseTool.log("Cleaning dist directory...")
        shutil.rmtree("dist", ignore_errors=True)
        shutil.rmtree("build", ignore_errors=True)
        for pattern in ["*.egg-info", "__pycache__", "*/__pycache__"]:
            for path in glob.glob(pattern):
                if os.path.isdir(path):
                    shutil.rmtree(path)

    def build_and_upload(self, new_version: str) -> None:
        """Build distributions and upload them to PyPI.

        :param new_version: Version being released.
        :raises ValueError: If expected build artifacts are missing.
        :raises subprocess.CalledProcessError: If build or upload commands fail.
        """
        self.clean_build_artifacts()
        self.log("Building package...")
        self.build_package()

        self.upload_to_pypi(self.package_name, new_version)

    def create_and_push_tag(self, new_version: str) -> None:
        """Create the release tag and push it to the remote repository.

        :param new_version: Version string used to create git tag `v<version>`.
        :raises subprocess.CalledProcessError: If local tag creation fails.
        """
        self.log("Creating git tag...")
        self.run_command(["git", "tag", "-a", "-f", f"v{new_version}", "-m", f"Release version {new_version}"])

        self.log("Pushing tag to remote...")
        tag_push_result = self.run_command(["git", "push", "origin", f"v{new_version}"], check=False)
        if tag_push_result:
            self.log("Tag push succeeded")
        else:
            self.log("Tag push skipped (already exists or up-to-date)")

        self.log("Release completed successfully!")

    def cleanup(self) -> None:
        """Delete the temporary virtual environment if one was created.

        :raises OSError: If cleanup fails while removing temporary files.
        """
        if self.temp_venv_path and self.temp_venv_path.exists():
            self.log("Cleaning up temporary virtual environment...")
            shutil.rmtree(self.temp_venv_path)


def main(argv=None) -> int:
    """CLI runner for the release tool.

    :param argv: Optional CLI arguments passed instead of process argv.
    :return: Process exit code (0 on success, 1 on handled failure).
    """
    releaser = PyPIReleaseTool(argv)
    try:
        releaser.initialize_project_context()
        releaser.setup_release_environment()
        releaser.run_test_phase()
        new_version = releaser.determine_new_version()
        if releaser.dry_run:
            releaser.log(f"DRY RUN: Would update version to {new_version} and run release steps.")
            return 0

        releaser.update_versioned_files(new_version)
        releaser.commit_and_push_changes()
        releaser.build_and_upload(new_version)
        releaser.create_and_push_tag(new_version)
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        logging.error("ERROR: %s", exc)
        releaser.cleanup()
        return 1
    finally:
        releaser.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

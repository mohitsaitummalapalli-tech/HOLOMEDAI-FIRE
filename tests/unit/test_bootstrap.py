"""Unit test verifying HoloMed foundational package bootstrap, contracts, and metadata."""

import importlib
import importlib.metadata
from pathlib import Path
import tomllib
import pytest


def test_import_holomed():
    """Verify holomed root package imports deterministically with exact metadata."""
    import holomed

    assert holomed is not None
    assert hasattr(holomed, "__version__")
    assert isinstance(holomed.__version__, str)
    assert holomed.__version__ == "0.1.0"
    assert hasattr(holomed, "__app_name__")
    assert holomed.__app_name__ == "HoloMed AI"


@pytest.mark.parametrize(
    "subpackage",
    [
        "holomed.common",
        "holomed.configuration",
        "holomed.protocol",
        "holomed.runtime",
        "holomed.core",
        "holomed.devices",
    ],
)
def test_import_subpackages(subpackage: str):
    """Verify all foundational subpackages can be imported without side-effects."""
    mod = importlib.import_module(subpackage)
    assert mod is not None


def test_zero_runtime_dependencies_contract():
    """Verify pyproject.toml explicitly declares zero runtime dependencies for M00.1."""
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml not found at {pyproject_path}"

    with open(pyproject_path, "rb") as f:
        pyproject_data = tomllib.load(f)

    project = pyproject_data.get("project", {})
    assert project.get("dependencies") == [], "dependencies list in pyproject.toml must be empty for M00.1"
    assert project.get("license") == "Apache-2.0", "PEP 639 license expression must be Apache-2.0"
    assert project.get("license-files") == ["LICENSE"], "PEP 639 license-files must be ['LICENSE']"

    # Verify optional test dependencies contain only pytest
    optional_deps = project.get("optional-dependencies", {})
    assert set(optional_deps.keys()) == {"test"}
    assert len(optional_deps["test"]) == 1
    assert optional_deps["test"][0].startswith("pytest")


def test_installed_metadata_zero_runtime_dependencies():
    """Verify installed package distribution metadata confirms zero required runtime dependencies."""
    installed_requires = importlib.metadata.requires("holomed")
    # importlib.metadata.requires returns None if there are no dependencies, or only extra-conditional dependencies
    if installed_requires is not None:
        mandatory_requires = [
            req for req in installed_requires if "extra ==" not in req
        ]
        assert mandatory_requires == [], f"Found unexpected mandatory runtime dependencies: {mandatory_requires}"

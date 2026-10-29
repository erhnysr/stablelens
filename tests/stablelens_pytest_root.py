"""pytest plugin (loaded with ``-p`` from pyproject.toml): collect the repository root as a plain
directory, not as a package.

The root is the plugin package itself (Hermes loads ``__init__.py`` from it), so pytest would
otherwise import it under the checkout directory's name, and a name like ``stablelens-main`` is
not importable. ``tests/conftest.py`` loads the plugin as ``stablelens`` instead.
"""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.hookimpl(tryfirst=True)
def pytest_collect_directory(path, parent):
    if pathlib.Path(path).resolve() == ROOT:
        return pytest.Dir.from_parent(parent, path=pathlib.Path(path))
    return None

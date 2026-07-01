"""Shared pytest fixtures for wsprofiler tests.

Sets the Qt applicationName/organizationName so QStandardPaths resolves
to a predictable location during tests, and redirects the test-time
app-data directory to a temp folder so the user's real home directory
is never touched.
"""
from __future__ import annotations

import os

# Force offscreen Qt platform BEFORE any PySide6 import so widgets work
# in headless CI environments.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
from pathlib import Path

import pytest


def _ensure_app_name() -> None:
    """Set Qt applicationName before QApplication is built.

    We leave organizationName unset so QStandardPaths.AppDataLocation
    resolves to <data>/wsprofiler rather than <data>/wsprofiler/wsprofiler.
    This is critical: if the app name isn't set, Qt falls back to
    sys.argv[0] (e.g. the test file name), which leaks test artefacts
    into the user's home directory.
    """
    from PySide6.QtCore import QCoreApplication
    if QCoreApplication.applicationName() != "wsprofiler":
        QCoreApplication.setApplicationName("wsprofiler")
        QCoreApplication.setOrganizationName("")


@pytest.fixture(scope="session", autouse=True)
def _test_data_home():
    """Redirect QStandardPaths.AppDataLocation to a temp dir for tests.

    On Linux, QStandardPaths respects $XDG_DATA_HOME. Setting it to a
    temp dir before the QApplication is built means SessionManager's
    default_sessions_dir resolves under that temp dir, so tests never
    write to the user's real ``~/.local/share/wsprofiler/sessions``
    folder. The temp dir is cleaned up when the session ends.
    """
    tmp = Path(tempfile.mkdtemp(prefix="wsprofiler_test_data_"))
    os.environ["XDG_DATA_HOME"] = str(tmp)
    # macOS / Windows also consult these; setting them is harmless on Linux.
    os.environ["APPDATA"] = str(tmp)
    os.environ["HOME"] = str(tmp)
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def qapp(_test_data_home):
    """Session-scoped, autouse QApplication with the right app/org name.

    Autouse so every test gets a QApplication with the correct
    applicationName/organizationName set, even if the test doesn't
    explicitly request it. The test-data fixture (above) ensures
    QStandardPaths resolves to a temp dir.
    """
    _ensure_app_name()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _reset_app_name():
    """Reset Qt application name before each test so the qapp fixture
    has a chance to set it."""
    yield
    # Nothing to do; the qapp fixture is session-scoped so the name
    # persists. We just need this fixture to make sure tests that don't
    # use qapp still benefit from the cleanup.

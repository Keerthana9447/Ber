"""
pytest configuration for the BEREV backend test suite.

Resets the Python logging subsystem before each test so that calls to
``configure_logging()`` inside ``create_app()`` (which clears root handlers)
do not break ``assertLogs`` in later test classes.
"""
from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def reset_logging():
    """
    Restore the root logger to a known state before every test.

    ``app.logging_config.configure_logging`` calls ``root.handlers.clear()``,
    which removes pytest's own log-capture handler and breaks
    ``unittest.TestCase.assertLogs`` in test classes that run *after* any test
    that calls ``create_app()``.  Resetting propagation and re-attaching a
    minimal handler before each test prevents that cross-test interference.
    """
    root = logging.getLogger()
    # Snapshot existing handlers so we can restore them after the test.
    old_handlers = root.handlers[:]
    old_level = root.level

    # Ensure the root logger is at a permissive level during each test so
    # assertLogs() can capture ERROR-level messages from child loggers.
    root.setLevel(logging.DEBUG)
    # Make sure every named logger we care about propagates to root.
    for name in ("berev", "berev.api", "berev.preflight"):
        logging.getLogger(name).propagate = True

    yield

    # Restore root to what it was (or what configure_logging set it to)
    # so we do not silently suppress legitimate test output.
    root.handlers[:] = old_handlers
    root.setLevel(old_level)


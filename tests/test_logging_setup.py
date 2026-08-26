from __future__ import annotations

import logging
from pathlib import Path

from agent_bridge import logging_setup


def test_setup_logging_retries_without_partial_configuration(monkeypatch, tmp_path: Path):
    logger = logging.Logger("agent-bridge-test-logging")
    logger.setLevel(logging.WARNING)
    file_handler_calls = 0

    class TestFileHandler(logging.Handler):
        def __init__(self, filename: Path):
            super().__init__()
            self.filename = filename

    def create_file_handler(filename, **_kwargs):
        nonlocal file_handler_calls
        file_handler_calls += 1
        if file_handler_calls == 1:
            raise OSError("simulated file handler initialization failure")
        return TestFileHandler(filename)

    monkeypatch.setattr(logging_setup.logging, "getLogger", lambda: logger)
    monkeypatch.setattr(logging_setup, "RotatingFileHandler", create_file_handler)
    monkeypatch.setattr(logging_setup, "log_path", lambda home: tmp_path / "bridge.log")

    try:
        try:
            logging_setup.setup_logging(tmp_path)
        except OSError as exc:
            assert str(exc) == "simulated file handler initialization failure"
        else:
            raise AssertionError("expected file handler initialization to fail")

        assert logger.handlers == []
        assert logger.level == logging.WARNING
        assert not getattr(logger, "_agent_bridge_configured", False)

        logging_setup.setup_logging(tmp_path)
        handlers = list(logger.handlers)

        assert len(handlers) == 2
        assert logger.level == logging.INFO
        assert getattr(logger, "_agent_bridge_configured", False)
        assert isinstance(handlers[0], logging.StreamHandler)
        assert isinstance(handlers[1], TestFileHandler)
        assert all(handler.formatter is not None for handler in handlers)
        assert all(handler.formatter._fmt == "%(asctime)s %(levelname)s %(name)s: %(message)s" for handler in handlers)

        logging_setup.setup_logging(tmp_path)

        assert logger.handlers == handlers
        assert file_handler_calls == 2
    finally:
        for handler in logger.handlers:
            handler.close()

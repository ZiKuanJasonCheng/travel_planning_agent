import importlib
import logging

import services.langfuse_client as langfuse_client_module


def _reload_with_env(monkeypatch, public_key=None, secret_key=None, host=None):
    if public_key is None:
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    else:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", public_key)
    if secret_key is None:
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", secret_key)
    if host is None:
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    else:
        monkeypatch.setenv("LANGFUSE_HOST", host)
    importlib.reload(langfuse_client_module)
    return langfuse_client_module


def test_get_langfuse_client_returns_none_when_keys_missing(monkeypatch):
    module = _reload_with_env(monkeypatch, public_key=None, secret_key=None)
    assert module.get_langfuse_client() is None


def test_get_callback_handler_returns_none_when_keys_missing(monkeypatch):
    module = _reload_with_env(monkeypatch, public_key=None, secret_key=None)
    assert module.get_callback_handler() is None


def test_missing_keys_logs_one_warning(monkeypatch, caplog):
    module = _reload_with_env(monkeypatch, public_key=None, secret_key=None)
    with caplog.at_level(logging.WARNING):
        module.get_langfuse_client()
        module.get_langfuse_client()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_get_langfuse_client_returns_instance_when_keys_present(monkeypatch):
    module = _reload_with_env(
        monkeypatch, public_key="pk-test", secret_key="sk-test", host="https://cloud.langfuse.com"
    )
    client = module.get_langfuse_client()
    assert client is not None

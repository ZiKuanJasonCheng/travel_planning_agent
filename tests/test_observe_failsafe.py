import importlib
import logging

import services.langfuse_client as langfuse_client_module


def _reload_unconfigured(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    importlib.reload(langfuse_client_module)
    return langfuse_client_module


def test_observe_does_not_spam_auth_warnings_when_unconfigured(monkeypatch, caplog):
    module = _reload_unconfigured(monkeypatch)

    @module.observe()
    def f(x):
        return x + 1

    with caplog.at_level(logging.WARNING):
        for i in range(5):
            assert f(i) == i + 1

    langfuse_auth_warnings = [
        r for r in caplog.records if r.name.startswith("langfuse") and "Authentication error" in r.getMessage()
    ]
    assert langfuse_auth_warnings == []


def test_observe_with_as_type_kwarg_still_calls_through_unconfigured(monkeypatch, caplog):
    module = _reload_unconfigured(monkeypatch)

    @module.observe(as_type="generation")
    def g(x):
        return x * 2

    with caplog.at_level(logging.WARNING):
        for i in range(3):
            assert g(i) == i * 2

    langfuse_auth_warnings = [
        r for r in caplog.records if r.name.startswith("langfuse") and "Authentication error" in r.getMessage()
    ]
    assert langfuse_auth_warnings == []


def test_observe_preserves_wrapped_attribute_when_unconfigured(monkeypatch):
    module = _reload_unconfigured(monkeypatch)

    @module.observe()
    def h(x):
        return x

    assert hasattr(h, "__wrapped__")


def test_observe_propagates_exceptions_when_unconfigured(monkeypatch):
    module = _reload_unconfigured(monkeypatch)

    @module.observe()
    def raises():
        raise ValueError("boom")

    try:
        raises()
        assert False, "expected ValueError to propagate"
    except ValueError as exc:
        assert str(exc) == "boom"

"""Safety net for the component cases when Main elects to execute them."""

import socket

import pytest


@pytest.fixture(autouse=True)
def component_only(monkeypatch):
    from bestseller.services import llm

    def forbidden(*args, **kwargs):
        raise AssertionError("F0 component cases forbid provider/network calls")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)

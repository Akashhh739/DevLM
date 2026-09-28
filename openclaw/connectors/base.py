"""Connector interface + registry.

A connector pulls a time window of data from one platform and normalizes it to
`Signal`s. New sources implement `fetch()` and register with `@register("name")`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol, runtime_checkable

from openclaw.models import Signal

# name -> connector class
REGISTRY: dict[str, type] = {}


def register(name: str):
    def deco(cls):
        cls.name = name
        REGISTRY[name] = cls
        return cls

    return deco


@runtime_checkable
class Connector(Protocol):
    name: str

    def fetch(self, window: timedelta) -> list[Signal]:
        """Return normalized signals from the last `window` of time."""
        ...

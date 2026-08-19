from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from nekograph.models import InboundMessageEvent
from nekograph.protocols.onebot_v11 import parse_message_event

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def event_factory() -> Callable[[str], InboundMessageEvent]:
    def load(name: str) -> InboundMessageEvent:
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        return parse_message_event(payload)

    return load

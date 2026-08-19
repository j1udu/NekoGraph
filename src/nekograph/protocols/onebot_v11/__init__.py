"""OneBot v11 ingress and egress adapter."""

from nekograph.protocols.onebot_v11.parser import parse_message_event, parse_onebot_event
from nekograph.protocols.onebot_v11.segments import parse_cq_message

__all__ = ["parse_cq_message", "parse_message_event", "parse_onebot_event"]

"""Group wake-up policy evaluated before commands or the agent."""

from dataclasses import dataclass

from nekograph.models import ChatKind, InboundMessageEvent


@dataclass(frozen=True, slots=True)
class WakeupDecision:
    awake: bool
    text: str = ""
    reason: str = "ignored"


@dataclass(frozen=True, slots=True)
class WakeupPolicy:
    prefixes: tuple[str, ...] = ("neko",)

    def evaluate(self, event: InboundMessageEvent) -> WakeupDecision:
        message = event.message
        text = message.plain_text.strip()
        if message.chat.kind is ChatKind.PRIVATE:
            return WakeupDecision(awake=True, text=text, reason="private")
        if text.startswith("/"):
            return WakeupDecision(awake=True, text=text, reason="command")

        mentioned = any(
            segment.kind == "at" and str(segment.data.get("qq")) == event.bot_id
            for segment in message.segments
        )
        if mentioned:
            without_mentions = "".join(
                segment.text_content
                for segment in message.segments
                if not (segment.kind == "at" and str(segment.data.get("qq")) == event.bot_id)
            ).strip()
            return WakeupDecision(awake=True, text=without_mentions, reason="mention")

        for prefix in self.prefixes:
            if prefix and text.startswith(prefix):
                return WakeupDecision(
                    awake=True,
                    text=text.removeprefix(prefix).lstrip(" :：,，"),
                    reason="prefix",
                )
        return WakeupDecision(awake=False)

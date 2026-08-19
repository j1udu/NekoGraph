# Fixture provenance

`private_message.json` and `group_message.json` are synthetic, sanitized fixtures built from the
standard OneBot v11 message-event shape. Their IDs, names, timestamps, and message text are fixed test
values. They are not retained copies of a real QQ conversation.

NapCat `4.18.19` private text messaging was manually validated end to end, but the raw inbound event
was not retained and the current NapCat log directory contains no recoverable message payload. A real
group event has not been captured. Until new events are deliberately captured and sanitized, tests
must not describe these files as real NapCat fixtures or promote NapCat extension fields into the
stable internal model.

When capturing replacements, remove or replace at least bot ID, user ID, group ID, message ID,
nickname/card, timestamps, access tokens, and conversation text before committing the fixture. Keep
only fields observed in the pinned NapCat version and preserve message-segment structure.

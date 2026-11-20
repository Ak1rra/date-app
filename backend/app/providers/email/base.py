from typing import Protocol


class EmailDeliveryError(Exception):
    """The email provider refused or failed to deliver a message."""


class EmailSender(Protocol):
    async def send(
        self,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
        link: str | None = None,
    ) -> None:
        """Send one message. `link` is the single call-to-action URL in it (e.g. a magic
        link); real senders ignore it (it is already in the body), the console sender
        records it so the dev outbox can expose it."""
        ...

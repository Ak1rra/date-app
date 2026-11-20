"""DEMO_MODE / dev email sender: logs the message and keeps it in an in-memory outbox."""

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Newest last. Bounded so a long-running dev server can't grow without limit.
OUTBOX: deque[dict[str, Any]] = deque(maxlen=50)


class ConsoleEmailSender:
    async def send(
        self,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
        link: str | None = None,
    ) -> None:
        OUTBOX.append({"to": to, "subject": subject, "link": link, "sent_at": datetime.now(UTC)})
        # The link is logged on purpose: the log file is local to the host, and it is how the
        # owner signs in before real email exists. Don't ship these logs anywhere shared.
        logger.info("email (console)", extra={"to": to, "subject": subject, "link": link})

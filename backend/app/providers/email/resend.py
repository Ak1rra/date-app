import httpx

from app.providers.email.base import EmailDeliveryError

RESEND_URL = "https://api.resend.com/emails"


class ResendEmailSender:
    def __init__(
        self, api_key: str, from_address: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._api_key = api_key
        self._from = from_address
        self._client = client

    async def send(
        self,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
        link: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "from": self._from,
            "to": [to],
            "subject": subject,
            "text": text,
        }
        if html is not None:
            payload["html"] = html
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            if self._client is not None:
                response = await self._client.post(
                    RESEND_URL, json=payload, headers=headers, timeout=10
                )
            else:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(RESEND_URL, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Never include the response body or payload: it may contain the magic link.
            raise EmailDeliveryError(f"resend delivery failed: {type(exc).__name__}") from exc

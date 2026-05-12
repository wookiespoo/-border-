"""
Phantom Client
Route your internet traffic through Border relay nodes.
For people in censored regions — or anyone who values privacy.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from .obfuscate import BorderObfuscator, BorderSession

logger = logging.getLogger("phantom.client")


class BorderClient:
    """
    A Phantom protocol client.

    Routes HTTP requests through relay nodes using obfuscated,
    encrypted tunnels that look like normal HTTPS traffic.

    Usage:
        client = BorderClient(
            client_id="my-device-001",
            relay_url="http://relay.example.com",
        )
        response = await client.get("https://example.com")
        print(response["body"])
    """

    def __init__(
        self,
        client_id: str,
        relay_url: str,
        timeout: int = 30,
    ):
        self.client_id = client_id
        self.relay_url = relay_url.rstrip("/")
        self.timeout = timeout
        self.obfuscator = BorderObfuscator()
        self.session: Optional[BorderSession] = None
        self._bytes_received = 0
        self._requests_made = 0

    async def connect(self) -> bool:
        """Establish an encrypted session with the relay node."""
        self.session = BorderSession.create()

        handshake_payload = {
            "type": "HANDSHAKE",
            "client_id": self.client_id,
            "version": "0.1",
        }

        try:
            response = await self._send_raw(handshake_payload)
            if response.get("type") == "HANDSHAKE_OK":
                logger.info(f"[Client] Connected to relay {self.relay_url}")
                logger.info(f"[Client] Session: {self.session.session_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"[Client] Handshake failed: {e}")
            return False

    async def get(self, url: str, headers: Optional[dict] = None) -> dict:
        """Fetch a URL through the Border relay."""
        return await self._proxy_request("GET", url, headers=headers)

    async def post(self, url: str, body: str = "", headers: Optional[dict] = None) -> dict:
        """POST to a URL through the Border relay."""
        return await self._proxy_request("POST", url, body=body, headers=headers)

    async def ping(self) -> dict:
        """Ping the relay node."""
        if not self.session:
            await self.connect()
        return await self._send_raw({"type": "PING", "client_id": self.client_id})

    async def _proxy_request(
        self,
        method: str,
        url: str,
        body: str = "",
        headers: Optional[dict] = None,
    ) -> dict:
        """Route a request through the relay."""
        if not self.session or not self.session.shared_key:
            connected = await self.connect()
            if not connected:
                return {"error": "Could not connect to relay", "status_code": 0}

        payload = {
            "type": "PROXY",
            "client_id": self.client_id,
            "method": method,
            "url": url,
            "headers": headers or {},
            "body": body,
        }

        start = time.time()
        response = await self._send_raw(payload)
        duration = time.time() - start

        if "bytes" in response:
            self._bytes_received += response["bytes"]
        self._requests_made += 1

        logger.info(
            f"[Client] {method} {url[:50]}... "
            f"→ {response.get('status_code', '?')} "
            f"({duration:.2f}s)"
        )

        return response

    async def _send_raw(self, payload: dict) -> dict:
        """Send a payload to the relay and return the unwrapped response."""
        if not self.session:
            self.session = BorderSession.create()

        # Wrap in obfuscation layer
        wrapped = self.obfuscator.wrap_request(payload, self.session)
        headers = self.obfuscator.get_cover_headers()

        endpoint = f"{self.relay_url}/api/v1/data"

        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.post(endpoint, json=wrapped, headers=headers)
            resp.raise_for_status()
            envelope = resp.json()

        # Complete key exchange on first response if needed
        if not self.session.shared_key:
            # For handshake responses, the relay sends its public key unencrypted
            relay_pubkey_b64 = envelope.get("relay_pubkey") or (
                envelope.get("data", "")
            )
            if relay_pubkey_b64:
                import base64
                try:
                    relay_pubkey = base64.b64decode(relay_pubkey_b64)
                    self.session.complete_handshake(relay_pubkey)
                except Exception:
                    pass

            # Return raw envelope for handshake
            return {
                "type": envelope.get("status", "HANDSHAKE_OK"),
                **envelope,
            }

        # Unwrap encrypted response
        return self.obfuscator.unwrap_response(envelope, self.session)

    @property
    def stats(self) -> dict:
        return {
            "client_id": self.client_id,
            "relay": self.relay_url,
            "requests_made": self._requests_made,
            "bytes_received": self._bytes_received,
            "session_active": self.session is not None and self.session.shared_key is not None,
        }


async def quick_fetch(url: str, relay_url: str) -> str:
    """
    One-liner to fetch a URL through a Border relay.

    Usage:
        content = await quick_fetch("https://example.com", "http://relay.example.com")
    """
    import uuid
    client = BorderClient(
        client_id=f"quick_{uuid.uuid4().hex[:8]}",
        relay_url=relay_url,
    )
    response = await client.get(url)
    return response.get("body", "")

"""A small JSON-RPC client for Arc's public endpoints, with failover and chain-id checks.

- Every endpoint is asked for ``eth_chainId`` before its first real answer is used, and is dropped
  for the rest of the process if it reports another chain: a misconfigured URL can never return
  another network's balances.
- A transport failure, an HTTP error, a non-JSON body, an oversized body or a rate-limit /
  internal JSON-RPC error moves on to the next endpoint. Other JSON-RPC errors (a revert, an
  invalid parameter) are the chain's answer and are returned to the caller as RPCError.
- Every request has a socket timeout and every tool call has an overall deadline, so a stalled
  endpoint cannot hang an unattended Hermes run.
- The endpoint that last answered is tried first; state is shared across threads under a lock.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from .codec import hex_to_int

USER_AGENT = "stablelens-hermes-plugin/0.1.0"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# JSON-RPC errors that say "this endpoint could not answer", not "the chain says no".
_FAILOVER_RPC_CODES = {-32005, -32603, -32601, 429}


class RPCError(Exception):
    """The chain answered with a JSON-RPC error (for example, a reverted call)."""

    def __init__(self, code: int | None, message: str):
        super().__init__(message)
        self.code = code


class NetworkUnavailable(Exception):
    """No endpoint gave a usable answer before the deadline."""


@dataclass
class _Response:
    status: int
    body: bytes
    encoding: str | None = None  # the Content-Encoding header, if any


Transport = Callable[[str, bytes, float], _Response]


def http_transport(url: str, payload: bytes, timeout: float) -> _Response:
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (https only, checked)
            body = response.read(MAX_RESPONSE_BYTES + 1)
            return _Response(response.status, body, response.headers.get("Content-Encoding"))
    except urllib.error.HTTPError as err:
        body = err.read(MAX_RESPONSE_BYTES + 1) if err.fp else b""
        return _Response(err.code, body, err.headers.get("Content-Encoding") if err.headers else None)


def decode_body(body: bytes, encoding: str | None) -> bytes:
    """Undo gzip or deflate content encoding, producing at most MAX_RESPONSE_BYTES + 1 bytes.

    Some endpoints send gzip without labelling it, so the gzip magic bytes are recognised too. A
    body that does not decode is returned unchanged, and the JSON check then fails it over.
    """
    label = (encoding or "").strip().lower()
    if label in ("gzip", "x-gzip") or body[:2] == b"\x1f\x8b":
        attempts = (16 + zlib.MAX_WBITS,)
    elif label == "deflate":
        attempts = (zlib.MAX_WBITS, -zlib.MAX_WBITS)  # zlib-wrapped, then raw deflate
    else:
        return body
    for wbits in attempts:
        decoder = zlib.decompressobj(wbits)
        try:
            out = decoder.decompress(body, MAX_RESPONSE_BYTES + 1)
        except zlib.error:
            continue
        if len(out) > MAX_RESPONSE_BYTES or decoder.eof:
            return out  # complete, or too large (which the caller refuses)
    return body


def validate_endpoint(url: str) -> str:
    """An endpoint URL Hermes may call: https, or http only to this machine (a local node)."""
    url = url.strip()
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ValueError("rpc_url must not contain spaces or control characters")
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1"):
        return url
    raise ValueError("rpc_url must be an https:// URL (or http:// to localhost for a local node)")


def _redact(text: str, url: str) -> str:
    """Remove anything of an endpoint URL but its host name from an error message: a private
    endpoint carries its provider key in the path, query or user info."""
    parts = urlsplit(url)
    secrets = [url, parts.netloc, parts.path, parts.query, parts.username or "", parts.password or ""]
    secrets += [segment for segment in parts.path.split("/") if len(segment) >= 4]
    secrets += [value for pair in parts.query.split("&") for value in pair.split("=")[1:] if len(value) >= 4]
    for secret in sorted({s for s in secrets if s and s != "/" and s != parts.hostname}, key=len, reverse=True):
        text = text.replace(secret, "…")
    return text


class ArcRPC:
    def __init__(
        self,
        urls: tuple[str, ...],
        chain_id: int,
        *,
        transport: Transport = http_transport,
        timeout: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not urls:
            raise ValueError("at least one RPC URL is required")
        self.urls = tuple(validate_endpoint(u) for u in urls)
        self.chain_id = chain_id
        self.timeout = timeout
        self._transport = transport
        self._clock = clock
        self._lock = threading.Lock()
        self._preferred = 0
        self._verified: set[str] = set()
        self._wrong_chain: set[str] = set()
        self._next_id = 0
        self.last_endpoint: str | None = None

    def _request_id(self) -> int:
        with self._lock:
            self._next_id += 1
            return self._next_id

    def _order(self) -> list[str]:
        with self._lock:
            start = self._preferred
            usable = [u for u in self.urls if u not in self._wrong_chain]
        ordered = list(self.urls[start:]) + list(self.urls[:start])
        return [u for u in ordered if u in usable]

    def _post(self, url: str, method: str, params: list, deadline: float) -> Any:
        """One request to one endpoint. Returns the result, raises RPCError for a chain answer,
        or raises _Failover when the endpoint could not answer."""
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise _Failover("deadline reached")
        payload = json.dumps({"jsonrpc": "2.0", "id": self._request_id(), "method": method, "params": params}).encode()
        try:
            response = self._transport(url, payload, min(self.timeout, remaining))
        except Exception as err:  # timeouts, DNS, TLS, connection resets
            raise _Failover(f"{type(err).__name__}: {err}") from err
        body = decode_body(response.body, response.encoding)
        if len(body) > MAX_RESPONSE_BYTES:
            raise _Failover("response too large")
        if response.status != 200:
            raise _Failover(f"HTTP {response.status}")
        try:
            message = json.loads(body)
        except (ValueError, UnicodeDecodeError) as err:
            raise _Failover("response is not JSON") from err
        if not isinstance(message, dict):
            raise _Failover("response is not a JSON-RPC object")
        if "error" in message and message["error"] is not None:
            error = message["error"] if isinstance(message["error"], dict) else {}
            code = error.get("code")
            text = str(error.get("message") or "JSON-RPC error")[:500]
            if code in _FAILOVER_RPC_CODES or "rate limit" in text.lower():
                raise _Failover(f"RPC error {code}: {text}")
            raise RPCError(code if isinstance(code, int) else None, text)
        if "result" not in message:
            raise _Failover("response has no result")
        return message["result"]

    def _ensure_chain(self, url: str, deadline: float) -> None:
        with self._lock:
            if url in self._verified:
                return
        try:
            answer = self._post(url, "eth_chainId", [], deadline)
        except RPCError as err:  # an endpoint that cannot say its chain id is not used
            raise _Failover(f"eth_chainId failed: {err}") from err
        try:
            reported = hex_to_int(answer, "chain id")
        except ValueError as err:
            raise _Failover(str(err)) from err
        if reported != self.chain_id:
            with self._lock:
                self._wrong_chain.add(url)
            raise _Failover(f"endpoint reports chain id {reported}, expected {self.chain_id}")
        with self._lock:
            self._verified.add(url)

    def call(self, method: str, params: list | None = None, *, deadline: float | None = None) -> Any:
        """Send one JSON-RPC request, trying each endpoint in turn until one answers."""
        params = params or []
        deadline = deadline if deadline is not None else self._clock() + 3 * self.timeout
        failures: list[str] = []
        for url in self._order():
            try:
                self._ensure_chain(url, deadline)
                result = self._post(url, method, params, deadline)
            except _Failover as err:
                failures.append(f"{urlsplit(url).hostname}: {_redact(str(err), url)}")
                continue
            with self._lock:
                self._preferred = self.urls.index(url)
                self.last_endpoint = urlsplit(url).hostname
            return result
        if not failures:
            failures.append("every configured endpoint reported a different chain id")
        raise NetworkUnavailable("no Arc RPC endpoint answered (" + "; ".join(failures) + ")")


class _Failover(Exception):
    pass

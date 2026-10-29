"""Input validation, amount formatting and the small slice of the Solidity ABI this plugin reads.

Amounts stay integers end to end: a raw on-chain integer is formatted into a decimal string by
integer arithmetic, never through float, so 18-decimal values keep every digit.
"""

from __future__ import annotations

import re

from .keccak import keccak256, to_checksum_address

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]*")

ZERO_ADDRESS = "0x" + "0" * 40
TRANSFER_TOPIC = "0x" + keccak256(b"Transfer(address,address,uint256)").hex()

# Function selectors (first 4 bytes of keccak256 of the signature).
SELECTOR = {
    name: "0x" + keccak256(signature.encode()).hex()[:8]
    for name, signature in {
        "balanceOf": "balanceOf(address)",
        "decimals": "decimals()",
        "symbol": "symbol()",
        "name": "name()",
        "totalSupply": "totalSupply()",
    }.items()
}


class InputError(ValueError):
    """The caller passed a value the tool cannot use; the message says how to fix it."""


def parse_address(value: object, field: str = "address") -> str:
    """A 20-byte address in EIP-55 form. Rejects a mixed-case address whose checksum is wrong,
    which is how a mistyped character is caught."""
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value.strip()):
        raise InputError(f"{field} must be 0x followed by 40 hex characters")
    value = value.strip()
    checksummed = to_checksum_address(value)
    body = value[2:]
    if body != body.lower() and body != body.upper() and value != checksummed:
        raise InputError(f"{field} has an invalid EIP-55 checksum (a character may be mistyped); expected {checksummed}")
    return checksummed


def parse_tx_hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value.strip()):
        raise InputError("tx_hash must be 0x followed by 64 hex characters")
    return value.strip().lower()


def parse_block(value: object) -> str:
    """A block tag for JSON-RPC: 'latest', 'safe', 'finalized', or a non-negative number."""
    if value is None or value == "":
        return "latest"
    if isinstance(value, bool):
        raise InputError("block must be a non-negative integer or one of latest, safe, finalized")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        text = value.strip().lower()
        if text in ("latest", "safe", "finalized"):
            return text
        if _HEX_RE.fullmatch(text) and len(text) > 2:
            number = int(text, 16)
        elif text.isdigit():
            number = int(text)
        else:
            raise InputError("block must be a non-negative integer or one of latest, safe, finalized")
    else:
        raise InputError("block must be a non-negative integer or one of latest, safe, finalized")
    if number < 0 or number >= 2**63:
        raise InputError("block number is out of range")
    return hex(number)


def hex_to_int(value: object, field: str) -> int:
    """A JSON-RPC quantity (0x-prefixed hex) as an int; raises ValueError on anything else."""
    if not isinstance(value, str) or not _HEX_RE.fullmatch(value) or len(value) < 3:
        raise ValueError(f"RPC returned a malformed {field}: {value!r}")
    return int(value, 16)


def format_units(raw: int, decimals: int) -> str:
    """``raw / 10**decimals`` as an exact decimal string without trailing zeros (e.g. '1.5', '0')."""
    if decimals < 0:
        raise ValueError("decimals must be non-negative")
    sign = "-" if raw < 0 else ""
    whole, frac = divmod(abs(raw), 10**decimals)
    if decimals == 0 or frac == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{str(frac).rjust(decimals, '0').rstrip('0')}"


def amount(raw: int, decimals: int, symbol: str | None = None) -> dict:
    """The JSON shape every amount takes: the raw integer as a string, and the formatted value."""
    out = {"raw": str(raw), "decimals": decimals, "formatted": format_units(raw, decimals)}
    if symbol:
        out["symbol"] = symbol
    return out


def encode_address_arg(address: str) -> str:
    return address[2:].lower().rjust(64, "0")


def call_data(selector_name: str, *address_args: str) -> str:
    return SELECTOR[selector_name] + "".join(encode_address_arg(a) for a in address_args)


def _words(data: str) -> bytes:
    if not isinstance(data, str) or not _HEX_RE.fullmatch(data) or len(data) % 2:
        raise ValueError("call returned malformed data")
    return bytes.fromhex(data[2:])


def decode_uint(data: str) -> int:
    raw = _words(data)
    if len(raw) < 32:
        raise ValueError("call returned fewer than 32 bytes")
    return int.from_bytes(raw[:32], "big")


def decode_small_uint(data: str, limit: int = 255) -> int:
    value = decode_uint(data)
    if value > limit:
        raise ValueError(f"value {value} is larger than {limit}")
    return value


def decode_string(data: str, max_length: int = 256) -> str:
    """An ABI ``string`` return value, or a ``bytes32`` one (some older tokens use that)."""
    raw = _words(data)
    if len(raw) == 32:  # bytes32: right-padded with zero bytes
        return raw.rstrip(b"\x00").decode("utf-8", "replace")[:max_length]
    if len(raw) < 64:
        raise ValueError("call returned a malformed string")
    offset = int.from_bytes(raw[:32], "big")
    if offset + 32 > len(raw):
        raise ValueError("call returned a malformed string")
    length = int.from_bytes(raw[offset : offset + 32], "big")
    if offset + 32 + length > len(raw):
        raise ValueError("call returned a malformed string")
    return raw[offset + 32 : offset + 32 + min(length, max_length * 4)].decode("utf-8", "replace")[:max_length]


def topic_to_address(topic: str) -> str:
    raw = _words(topic)
    if len(raw) != 32 or any(raw[:12]):
        raise ValueError("topic is not an address")
    return to_checksum_address("0x" + raw[12:].hex())

"""Keccak-256 and EIP-55 address checksums, in pure Python.

Ethereum hashes with the original Keccak padding (0x01), not the SHA3-256 padding (0x06) that
``hashlib.sha3_256`` uses, so the standard library cannot produce these digests. This module has
no dependencies; ``tests/test_keccak.py`` checks it against published test vectors.
"""

from __future__ import annotations

_MASK = (1 << 64) - 1

# Round constants of Keccak-f[1600].
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

# Rotation offsets r[x][y].
_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)

_RATE = 136  # bytes: 1600 - 2 * 256 bits


def _rol(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & _MASK if shift else value


def _permute(state: list[int]) -> None:
    """Keccak-f[1600] on 25 lanes indexed x + 5 * y, in place."""
    for rc in _RC:
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        a = [state[i] ^ d[i % 5] for i in range(25)]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(a[x + 5 * y], _ROT[x][y])
        for y in range(5):
            row = b[5 * y : 5 * y + 5]
            for x in range(5):
                state[x + 5 * y] = row[x] ^ (~row[(x + 1) % 5] & _MASK & row[(x + 2) % 5])
        state[0] ^= rc


def _sponge256(data: bytes, pad: int) -> bytes:
    """A 256-bit Keccak sponge: ``pad`` 0x01 is Keccak-256, 0x06 is SHA3-256 (the tests use that)."""
    padded = bytearray(data)
    padded.append(pad)
    padded.extend(b"\x00" * (-len(padded) % _RATE))
    padded[-1] |= 0x80
    state = [0] * 25
    for offset in range(0, len(padded), _RATE):
        block = padded[offset : offset + _RATE]
        for i in range(_RATE // 8):
            state[i] ^= int.from_bytes(block[8 * i : 8 * i + 8], "little")
        _permute(state)
    return b"".join(lane.to_bytes(8, "little") for lane in state[:4])


def keccak256(data: bytes) -> bytes:
    """The 32-byte Keccak-256 digest of ``data``."""
    return _sponge256(data, 0x01)


def to_checksum_address(address: str) -> str:
    """EIP-55 mixed-case form of a 20-byte hex address (``0x`` + 40 hex digits, any case)."""
    body = address[2:].lower()
    digest = keccak256(body.encode("ascii")).hex()
    return "0x" + "".join(ch.upper() if ch.isalpha() and int(digest[i], 16) >= 8 else ch for i, ch in enumerate(body))

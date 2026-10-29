import hashlib
import random

import pytest

from stablelens import chain
from stablelens.codec import (
    SELECTOR,
    TRANSFER_TOPIC,
    InputError,
    decode_string,
    format_units,
    parse_address,
    parse_block,
    parse_tx_hash,
    topic_to_address,
)
from stablelens.keccak import _sponge256, keccak256, to_checksum_address


def test_keccak256_published_vectors():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"


def test_permutation_matches_hashlib_sha3_for_every_length_and_random_input():
    # SHA3-256 is the same sponge with a different pad byte, so this checks the permutation and
    # multi-block absorption against the standard library.
    for n in range(0, 600):
        data = bytes(range(256)) * 3
        assert _sponge256(data[:n], 0x06) == hashlib.sha3_256(data[:n]).digest()
    rng = random.Random(7)
    for _ in range(100):
        data = rng.randbytes(rng.randrange(0, 2000))
        assert _sponge256(data, 0x06) == hashlib.sha3_256(data).digest()


def test_well_known_selectors_and_topic():
    assert TRANSFER_TOPIC == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    assert SELECTOR == {
        "balanceOf": "0x70a08231",
        "decimals": "0x313ce567",
        "symbol": "0x95d89b41",
        "name": "0x06fdde03",
        "totalSupply": "0x18160ddd",
    }


@pytest.mark.parametrize(
    "address",
    [
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ],
)
def test_eip55_vectors(address):
    assert to_checksum_address(address.lower()) == address
    assert parse_address(address.lower()) == address
    assert parse_address(address.upper().replace("0X", "0x")) == address


def test_every_published_contract_address_has_a_valid_checksum():
    # Catches a mistyped character in chain.py: Circle publishes these in EIP-55 form.
    for network in chain.NETWORKS.values():
        for address in [t.address for t in network.tokens] + list(network.contracts):
            body = address[2:]
            if body != body.lower():
                assert to_checksum_address(address) == address, address


def test_parse_address_rejects_bad_input():
    with pytest.raises(InputError, match="40 hex"):
        parse_address("0x123")
    with pytest.raises(InputError, match="40 hex"):
        parse_address(None)
    with pytest.raises(InputError, match="checksum"):
        parse_address("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD")  # last char's case flipped


def test_parse_tx_hash_and_block():
    assert parse_tx_hash("0x" + "AB" * 32) == "0x" + "ab" * 32
    with pytest.raises(InputError):
        parse_tx_hash("0x" + "ab" * 31)
    assert parse_block(None) == "latest"
    assert parse_block("Finalized") == "finalized"
    assert parse_block("28500000") == hex(28_500_000)
    assert parse_block(5) == "0x5"
    assert parse_block("0x10") == "0x10"
    for bad in (True, -1, "12a", "0x", "-3", 3.5):
        with pytest.raises(InputError):
            parse_block(bad)


def test_format_units_is_exact():
    assert format_units(0, 18) == "0"
    assert format_units(2_500_000, 6) == "2.5"
    assert format_units(2_500_000 * 10**12 + 123, 18) == "2.500000000000000123"
    assert format_units(1, 18) == "0.000000000000000001"
    assert format_units(10**30, 6) == "1000000000000000000000000"
    assert format_units(-15, 1) == "-1.5"
    assert format_units(7, 0) == "7"


def test_decode_string_abi_and_bytes32():
    from conftest import abi_string

    assert decode_string(abi_string("EURC")) == "EURC"
    assert decode_string("0x" + b"MKR".ljust(32, b"\x00").hex()) == "MKR"
    with pytest.raises(ValueError):
        decode_string("0x" + "00" * 40)
    with pytest.raises(ValueError):
        decode_string("0x" + "00" * 31 + "40" + "00" * 32)  # offset past the end


def test_topic_to_address_requires_zero_padding():
    assert topic_to_address("0x" + "0" * 24 + "5aaeb6053f3e94c9b9a09f33669435e7ef1beaed") == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    with pytest.raises(ValueError):
        topic_to_address("0x" + "1" * 64)

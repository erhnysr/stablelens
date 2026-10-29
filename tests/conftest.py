"""Load the plugin as the package ``stablelens`` whatever the checkout directory is called, and
provide a fake Arc chain that answers JSON-RPC without any network access."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # lets tests import these helpers

if "stablelens" not in sys.modules:
    spec = importlib.util.spec_from_file_location("stablelens", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["stablelens"] = module
    spec.loader.exec_module(module)

from stablelens import chain, rpc  # noqa: E402
from stablelens.codec import SELECTOR, TRANSFER_TOPIC  # noqa: E402

HOLDER = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
OTHER = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"
EURC = "0xbEf5f6d51CB62b58e6A8f77868681825C6fe21c1"
UNKNOWN_TOKEN = "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB"
TX = "0x" + "ab" * 32
BLOCK = 28_500_000
TIMESTAMP = 1_793_000_000


def word(value: int) -> str:
    return value.to_bytes(32, "big").hex()


def abi_string(text: str) -> str:
    raw = text.encode()
    padded = raw + b"\x00" * (-len(raw) % 32)
    return "0x" + word(32) + word(len(raw)) + padded.hex()


def topic(address: str) -> str:
    return "0x" + address[2:].lower().rjust(64, "0")


class FakeArc:
    """A JSON-RPC server in memory. ``behaviour[url]`` makes one endpoint misbehave."""

    def __init__(self, chain_id: int = 5042):
        self.chain_id = chain_id
        self.calls: list[tuple[str, str, list]] = []
        self.behaviour: dict[str, object] = {}
        self.native = {HOLDER.lower(): 2_500_000 * 10**12 + 123}  # 2.500000000000000123 USDC
        self.erc20 = {
            (chain.USDC_INTERFACE.lower(), HOLDER.lower()): 2_500_000,
            (EURC.lower(), HOLDER.lower()): 10_000_000,
            (UNKNOWN_TOKEN.lower(), HOLDER.lower()): 42 * 10**9,
        }
        self.unknown_token = {"decimals": 9, "symbol": "TOKN", "name": "Test Token", "supply": 10**18}
        self.code = {EURC.lower(): "0x6080", UNKNOWN_TOKEN.lower(): "0x6080", chain.USDC_INTERFACE.lower(): "0x6080"}
        self.tx = None
        self.receipt = None

    def block(self, number: int = BLOCK) -> dict:
        return {
            "number": hex(number),
            "hash": "0x" + "cd" * 32,
            "timestamp": hex(TIMESTAMP),
            "transactions": ["0x" + "01" * 32, "0x" + "02" * 32],
            "gasUsed": hex(1_000_000),
            "gasLimit": hex(30_000_000),
            "baseFeePerGas": hex(20 * 10**9),
            "miner": OTHER.lower(),
        }

    def answer(self, method: str, params: list):
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_blockNumber":
            return hex(BLOCK)
        if method == "eth_gasPrice":
            return hex(25 * 10**9)
        if method == "eth_getBlockByNumber":
            tag = params[0]
            if tag.startswith("0x") and int(tag, 16) > BLOCK:
                return None
            return self.block(BLOCK if not tag.startswith("0x") else int(tag, 16))
        if method == "eth_getBalance":
            return hex(self.native.get(params[0].lower(), 0))
        if method == "eth_getCode":
            return self.code.get(params[0].lower(), "0x")
        if method == "eth_getTransactionCount":
            return hex(7)
        if method == "eth_getTransactionByHash":
            return self.tx
        if method == "eth_getTransactionReceipt":
            return self.receipt
        if method == "eth_call":
            return self.eth_call(params[0]["to"].lower(), params[0]["data"])
        raise AssertionError(f"unexpected method {method}")

    def eth_call(self, to: str, data: str):
        selector, args = data[:10], data[10:]
        if selector == SELECTOR["balanceOf"]:
            holder = "0x" + args[24:64]
            return "0x" + word(self.erc20.get((to, holder), 0))
        if to == UNKNOWN_TOKEN.lower():
            if selector == SELECTOR["decimals"]:
                return "0x" + word(self.unknown_token["decimals"])
            if selector == SELECTOR["symbol"]:
                return abi_string(self.unknown_token["symbol"])
            if selector == SELECTOR["name"]:
                return abi_string(self.unknown_token["name"])
            if selector == SELECTOR["totalSupply"]:
                return "0x" + word(self.unknown_token["supply"])
        if to == EURC.lower() and selector == SELECTOR["totalSupply"]:
            return "0x" + word(123_456_789)
        if to == EURC.lower() and selector in (SELECTOR["name"], SELECTOR["symbol"]):
            return abi_string("Euro Coin" if selector == SELECTOR["name"] else "EURC")
        return ("revert", "execution reverted")

    def transport(self, url: str, payload: bytes, timeout: float) -> rpc._Response:
        request = json.loads(payload)
        self.calls.append((url, request["method"], request["params"]))
        behaviour = self.behaviour.get(url)
        if behaviour == "down":
            raise OSError("connection refused")
        if behaviour == "timeout":
            raise TimeoutError("timed out")
        if behaviour == "http500":
            return rpc._Response(500, b"oops")
        if behaviour == "html":
            return rpc._Response(200, b"<html>Just a moment...</html>")
        if behaviour == "ratelimit":
            return rpc._Response(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32005, "message": "rate limit"}}).encode())
        if behaviour == "wrong_chain" and request["method"] == "eth_chainId":
            return rpc._Response(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": "0x1"}).encode())
        result = self.answer(request["method"], request["params"])
        if isinstance(result, tuple) and result[0] == "revert":
            body = {"jsonrpc": "2.0", "id": request["id"], "error": {"code": 3, "message": result[1]}}
        else:
            body = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        return rpc._Response(200, json.dumps(body).encode())


@pytest.fixture
def fake():
    return FakeArc()


@pytest.fixture(autouse=True)
def fresh_clients(monkeypatch):
    from stablelens import tools

    monkeypatch.setattr(tools, "_CLIENTS", tools._Clients())


@pytest.fixture
def call(fake):
    """Run a tool against the fake chain and return its decoded JSON."""
    from stablelens import tools

    def run(name: str, args: dict | None = None, settings: dict | None = None, secrets: dict | None = None):
        values = settings or {}
        stored = secrets or {}
        handler = tools.make_handler(
            name, lambda key, default: values.get(key, default), fake.transport, lambda key: stored.get(key, "")
        )
        return json.loads(handler(args or {}))

    return run

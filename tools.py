"""Tool handlers. Each takes the tool arguments and returns a JSON string, and never raises.

All reads in one tool call are pinned to one block, so a balance list or a transaction summary is
a consistent snapshot even while new blocks arrive.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import chain
from .codec import (
    TRANSFER_TOPIC,
    InputError,
    amount,
    call_data,
    decode_small_uint,
    decode_string,
    decode_uint,
    hex_to_int,
    parse_address,
    parse_block,
    parse_tx_hash,
    topic_to_address,
)
from .rpc import ArcRPC, NetworkUnavailable, RPCError, Transport, http_transport, validate_endpoint

logger = logging.getLogger(__name__)

TOOL_DEADLINE_SECONDS = 25.0
MAX_EXTRA_TOKENS = 10
MAX_DECODED_TRANSFERS = 50
MAX_TOKEN_LOOKUPS = 10
NATIVE_TRANSFER_GAS = 21_000

Settings = Callable[[str, Any], Any]
Secrets = Callable[[str], str]

# A private endpoint URL usually carries the provider's API key, so it is a Hermes secret (stored
# in the profile's .env, read through Hermes's secret scope), never a config.yaml value.
RPC_URL_SECRETS = {"mainnet": "STABLELENS_MAINNET_RPC_URL", "testnet": "STABLELENS_TESTNET_RPC_URL"}


def _no_settings(_key: str, default: Any) -> Any:
    return default


def _no_secrets(_name: str) -> str:
    return ""


class _Clients:
    """One ArcRPC per (network, endpoint list), so failover state survives across tool calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[tuple, ArcRPC] = {}

    def get(self, network: chain.Network, urls: tuple[str, ...], timeout: float, transport: Transport) -> ArcRPC:
        key = (network.key, urls, timeout, transport)
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = ArcRPC(urls, network.chain_id, transport=transport, timeout=timeout)
                self._clients[key] = client
            return client


_CLIENTS = _Clients()


class _Session:
    def __init__(self, args: dict, settings: Settings, secrets: Secrets, transport: Transport):
        requested = args.get("network")
        if requested in (None, ""):
            requested = settings("network", "mainnet")
        if not isinstance(requested, str) or requested.strip().lower() not in chain.NETWORKS:
            raise InputError("network must be 'mainnet' or 'testnet'")
        self.network = chain.NETWORKS[requested.strip().lower()]

        timeout = settings("timeout_seconds", 10)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            timeout = 10
        urls = list(self.network.rpc_urls)
        secret_name = RPC_URL_SECRETS[self.network.key]
        private = secrets(secret_name) or ""  # a failing secret lookup raises: never fall back silently
        if private:
            try:
                urls.insert(0, validate_endpoint(str(private)))
            except ValueError as err:
                raise InputError(f"{secret_name}: {err}") from err
        self.rpc = _CLIENTS.get(self.network, tuple(urls), float(timeout), transport)
        self.deadline = time.monotonic() + TOOL_DEADLINE_SECONDS

    def call(self, method: str, params: list) -> Any:
        return self.rpc.call(method, params, deadline=self.deadline)

    def eth_call(self, to: str, data: str, block: str) -> str:
        return self.call("eth_call", [{"to": to, "data": data}, block])

    def explorer(self, kind: str, value: str) -> str:
        return f"{self.network.explorer}/{kind}/{value}"

    def base(self) -> dict:
        return {"network": self.network.key, "chain_id": self.network.chain_id}


def _run(
    name: str, args: Any, body: Callable[[_Session, dict], dict], settings: Settings, secrets: Secrets, transport: Transport
) -> str:
    if not isinstance(args, dict):
        args = {}
    try:
        session = _Session(args, settings, secrets, transport)
        result = body(session, args)
        result["rpc_endpoint"] = session.rpc.last_endpoint
        return json.dumps(result)
    except InputError as err:
        return json.dumps({"error": str(err), "error_type": "invalid_input"})
    except NetworkUnavailable as err:
        return json.dumps({"error": str(err), "error_type": "network_unavailable"})
    except RPCError as err:
        return json.dumps({"error": f"Arc RPC error: {err}", "error_type": "rpc_error", "rpc_code": err.code})
    except Exception as err:  # never let a tool call raise into Hermes
        logger.exception("stablelens %s failed", name)
        return json.dumps({"error": f"{type(err).__name__}: {err}", "error_type": "internal_error"})


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _block_header(session: _Session, tag: str) -> dict:
    block = session.call("eth_getBlockByNumber", [tag, False])
    if block is None:
        raise InputError(f"block {tag if not tag.startswith('0x') else int(tag, 16)} does not exist yet")
    if not isinstance(block, dict):
        raise ValueError("RPC returned a malformed block")
    return block


# ---------------------------------------------------------------- arc_network_status


def _network_status(session: _Session, _args: dict) -> dict:
    block = _block_header(session, "latest")
    number = hex_to_int(block.get("number"), "block number")
    timestamp = hex_to_int(block.get("timestamp"), "block timestamp")
    gas_price = hex_to_int(session.call("eth_gasPrice", []), "gas price")
    base_fee = block.get("baseFeePerGas")
    out = session.base()
    out.update(
        {
            "name": session.network.name,
            "latest_block": number,
            "latest_block_time": _iso(timestamp),
            "seconds_since_latest_block": max(0, int(time.time()) - timestamp),
            "gas_price": amount(gas_price, chain.NATIVE_DECIMALS, "USDC") | {"unit": "USDC per gas"},
            "base_fee_per_gas": amount(hex_to_int(base_fee, "base fee"), chain.NATIVE_DECIMALS, "USDC") if base_fee else None,
            "native_transfer_fee": amount(NATIVE_TRANSFER_GAS * gas_price, chain.NATIVE_DECIMALS, "USDC")
            | {"gas": NATIVE_TRANSFER_GAS},
            "gas_token": "USDC (native, 18 decimals)",
            "explorer": session.network.explorer,
        }
    )
    return out


# ---------------------------------------------------------------- arc_balance


def _token_metadata(session: _Session, address: str, block: str) -> dict:
    """symbol and decimals of an ERC-20, from Circle's list when known, else from the chain."""
    known = chain.token_by_address(session.network, address)
    if known is not None:
        return {"symbol": known.symbol, "decimals": known.decimals, "name": known.name}
    try:
        decimals = decode_small_uint(session.eth_call(address, call_data("decimals"), block))
    except (RPCError, ValueError) as err:
        raise InputError(f"{address} does not answer decimals(); it may not be an ERC-20 token") from err
    try:
        symbol = decode_string(session.eth_call(address, call_data("symbol"), block), 32)
    except (RPCError, ValueError):
        symbol = None
    return {"symbol": symbol, "decimals": decimals, "name": None}


def _balance(session: _Session, args: dict) -> dict:
    address = parse_address(args.get("address"))
    extra = args.get("tokens") or []
    if not isinstance(extra, list) or not all(isinstance(t, str) for t in extra):
        raise InputError("tokens must be a list of token symbols or contract addresses")
    if len(extra) > MAX_EXTRA_TOKENS:
        raise InputError(f"at most {MAX_EXTRA_TOKENS} extra tokens per call")
    contracts: list[str] = [t.address for t in session.network.tokens]
    for item in extra:
        known = chain.token_by_symbol(session.network, item)
        resolved = known.address if known else parse_address(item, "token")
        if resolved not in contracts:
            contracts.append(resolved)

    block = _block_header(session, "latest")
    tag = block.get("number")
    hex_to_int(tag, "block number")
    native = hex_to_int(session.call("eth_getBalance", [address, tag]), "balance")

    balances = []
    for contract in contracts:
        meta = _token_metadata(session, contract, tag)
        entry = {"token": contract, "symbol": meta["symbol"]}
        if meta.get("name"):
            entry["name"] = meta["name"]
        try:
            raw = decode_uint(session.eth_call(contract, call_data("balanceOf", address), tag))
            entry["balance"] = amount(raw, meta["decimals"], meta["symbol"])
        except (RPCError, ValueError) as err:  # one token that cannot answer leaves the rest intact
            entry["error"] = f"balanceOf failed: {err}"
        balances.append(entry)

    out = session.base()
    out.update(
        {
            "address": address,
            "block": int(tag, 16),
            "native_usdc": amount(native, chain.NATIVE_DECIMALS, "USDC")
            | {"note": "Gas balance (18 decimals). The USDC ERC-20 entry below is the same balance at 6 decimals."},
            "tokens": balances,
            "explorer_url": session.explorer("address", address),
        }
    )
    return out


# ---------------------------------------------------------------- arc_transaction


def _decode_transfers(session: _Session, logs: list, block: str) -> tuple[list, bool]:
    transfers = []
    metadata: dict[str, dict | None] = {}
    truncated = False
    for log in logs:
        if not isinstance(log, dict):
            continue
        topics = log.get("topics") or []
        if len(topics) != 3 or str(topics[0]).lower() != TRANSFER_TOPIC:
            continue  # ERC-721 Transfer has 4 topics; only fungible transfers are decoded
        if len(transfers) >= MAX_DECODED_TRANSFERS:
            truncated = True
            break
        try:
            token = parse_address(log.get("address"), "log address")
            value = decode_uint(log.get("data") or "0x")
            sender, recipient = topic_to_address(topics[1]), topic_to_address(topics[2])
        except (InputError, ValueError):
            continue
        if token not in metadata:
            if len(metadata) >= MAX_TOKEN_LOOKUPS:
                metadata[token] = None
            else:
                try:
                    metadata[token] = _token_metadata(session, token, block)
                except (InputError, RPCError):
                    metadata[token] = None
        meta = metadata[token]
        entry = {"token": token, "from": sender, "to": recipient}
        entry["amount"] = amount(value, meta["decimals"], meta["symbol"]) if meta else {"raw": str(value)}
        label = chain.label_of(session.network, token)
        if label:
            entry["token_label"] = label
        transfers.append(entry)
    return transfers, truncated


def _transaction(session: _Session, args: dict) -> dict:
    tx_hash = parse_tx_hash(args.get("tx_hash"))
    tx = session.call("eth_getTransactionByHash", [tx_hash])
    out = session.base()
    out.update({"tx_hash": tx_hash, "explorer_url": session.explorer("tx", tx_hash)})
    if tx is None:
        out["status"] = "not_found"
        out["note"] = f"No transaction with this hash on {session.network.name}. Check the network and the hash."
        return out
    if not isinstance(tx, dict):
        raise ValueError("RPC returned a malformed transaction")

    sender = parse_address(tx.get("from"), "from")
    recipient = parse_address(tx["to"], "to") if tx.get("to") else None
    value = hex_to_int(tx.get("value", "0x0"), "value")
    out.update(
        {
            "from": sender,
            "to": recipient,
            "to_label": chain.label_of(session.network, recipient) if recipient else None,
            "value": amount(value, chain.NATIVE_DECIMALS, "USDC"),
            "nonce": hex_to_int(tx.get("nonce", "0x0"), "nonce"),
            "method_selector": (tx.get("input") or "0x")[:10] if len(tx.get("input") or "0x") >= 10 else None,
        }
    )
    if tx.get("blockNumber") is None:
        out["status"] = "pending"
        return out

    receipt = session.call("eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        out["status"] = "pending"
        return out
    if not isinstance(receipt, dict):
        raise ValueError("RPC returned a malformed receipt")
    block_hex = receipt.get("blockNumber")
    block_number = hex_to_int(block_hex, "block number")
    status = hex_to_int(receipt.get("status", "0x1"), "status")
    gas_used = hex_to_int(receipt.get("gasUsed"), "gas used")
    gas_price = hex_to_int(receipt.get("effectiveGasPrice") or tx.get("gasPrice"), "gas price")
    header = _block_header(session, block_hex)
    transfers, truncated = _decode_transfers(session, receipt.get("logs") or [], block_hex)
    out.update(
        {
            "status": "success" if status == 1 else "reverted",
            "block": block_number,
            "block_time": _iso(hex_to_int(header.get("timestamp"), "block timestamp")),
            "gas_used": gas_used,
            "effective_gas_price": amount(gas_price, chain.NATIVE_DECIMALS, "USDC"),
            "fee": amount(gas_used * gas_price, chain.NATIVE_DECIMALS, "USDC"),
            "contract_created": parse_address(receipt["contractAddress"], "contract") if receipt.get("contractAddress") else None,
            "token_transfers": transfers,
            "log_count": len(receipt.get("logs") or []),
        }
    )
    if truncated:
        out["token_transfers_truncated"] = True
    return out


# ---------------------------------------------------------------- arc_address


def _address(session: _Session, args: dict) -> dict:
    address = parse_address(args.get("address"))
    block = _block_header(session, "latest")
    tag = block.get("number")
    code = session.call("eth_getCode", [address, tag])
    if not isinstance(code, str) or not code.startswith("0x"):
        raise ValueError("RPC returned malformed code")
    code_size = (len(code) - 2) // 2
    out = session.base()
    out.update(
        {
            "address": address,
            "block": hex_to_int(tag, "block number"),
            "type": "contract" if code_size else "account",
            "code_size_bytes": code_size,
            "nonce": hex_to_int(session.call("eth_getTransactionCount", [address, tag]), "nonce"),
            "native_usdc": amount(hex_to_int(session.call("eth_getBalance", [address, tag]), "balance"), chain.NATIVE_DECIMALS, "USDC"),
            "known_as": chain.label_of(session.network, address),
            "explorer_url": session.explorer("address", address),
        }
    )
    return out


# ---------------------------------------------------------------- arc_token


def _token(session: _Session, args: dict) -> dict:
    raw = args.get("token")
    known = chain.token_by_symbol(session.network, raw) if isinstance(raw, str) else None
    address = known.address if known else parse_address(raw, "token")
    block = _block_header(session, "latest")
    tag = block.get("number")
    code = session.call("eth_getCode", [address, tag])
    if not isinstance(code, str) or len(code) <= 2:
        raise InputError(f"{address} has no contract code on {session.network.name}")
    meta = _token_metadata(session, address, tag)

    def text(selector: str) -> str | None:
        try:
            return decode_string(session.eth_call(address, call_data(selector), tag))
        except (RPCError, ValueError):
            return None

    try:
        supply = amount(decode_uint(session.eth_call(address, call_data("totalSupply"), tag)), meta["decimals"], meta["symbol"])
    except (RPCError, ValueError):
        supply = None
    out = session.base()
    out.update(
        {
            "token": address,
            "block": hex_to_int(tag, "block number"),
            "name": text("name") or meta.get("name"),
            "symbol": text("symbol") or meta["symbol"],
            "decimals": meta["decimals"],
            "total_supply": supply,
            "known_as": chain.label_of(session.network, address),
            "explorer_url": session.explorer("token", address),
        }
    )
    return out


# ---------------------------------------------------------------- arc_block


def _block(session: _Session, args: dict) -> dict:
    tag = parse_block(args.get("block"))
    block = _block_header(session, tag)
    number = hex_to_int(block.get("number"), "block number")
    txs = block.get("transactions") or []
    out = session.base()
    out.update(
        {
            "block": number,
            "hash": block.get("hash"),
            "time": _iso(hex_to_int(block.get("timestamp"), "block timestamp")),
            "transaction_count": len(txs),
            "gas_used": hex_to_int(block.get("gasUsed"), "gas used"),
            "gas_limit": hex_to_int(block.get("gasLimit"), "gas limit"),
            "base_fee_per_gas": amount(hex_to_int(block["baseFeePerGas"], "base fee"), chain.NATIVE_DECIMALS, "USDC")
            if block.get("baseFeePerGas")
            else None,
            "proposer": parse_address(block["miner"], "miner") if block.get("miner") else None,
            "explorer_url": session.explorer("block", str(number)),
        }
    )
    return out


# ---------------------------------------------------------------- public handlers

_BODIES = {
    "arc_network_status": _network_status,
    "arc_balance": _balance,
    "arc_transaction": _transaction,
    "arc_address": _address,
    "arc_token": _token,
    "arc_block": _block,
}


def make_handler(
    name: str, settings: Settings = _no_settings, transport: Transport = http_transport, secrets: Secrets = _no_secrets
):
    body = _BODIES[name]

    def handler(args: dict, **_kwargs: Any) -> str:
        return _run(name, args, body, settings, secrets, transport)

    handler.__name__ = name
    return handler

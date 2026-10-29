import json

import pytest

from conftest import BLOCK, EURC, HOLDER, OTHER, TIMESTAMP, TX, UNKNOWN_TOKEN, topic
from stablelens import chain, tools
from stablelens.codec import TRANSFER_TOPIC

MAINNET_URLS = chain.MAINNET.rpc_urls


def test_network_status(call):
    out = call("arc_network_status")
    assert out["chain_id"] == 5042 and out["network"] == "mainnet"
    assert out["latest_block"] == BLOCK
    assert out["latest_block_time"] == "2026-10-26T07:33:20Z"
    assert out["gas_price"]["formatted"] == "0.000000025"
    assert out["native_transfer_fee"] == {
        "raw": str(21_000 * 25 * 10**9),
        "decimals": 18,
        "formatted": "0.000525",
        "symbol": "USDC",
        "gas": 21_000,
    }
    assert out["rpc_endpoint"] == "rpc.mainnet.arc.io"


def test_balance_reads_every_token_at_one_block(call, fake):
    out = call("arc_balance", {"address": HOLDER.lower(), "tokens": [UNKNOWN_TOKEN]})
    assert out["address"] == HOLDER and out["block"] == BLOCK
    assert out["native_usdc"]["formatted"] == "2.500000000000000123"
    by_symbol = {t["symbol"]: t["balance"]["formatted"] for t in out["tokens"]}
    assert by_symbol == {"USDC": "2.5", "EURC": "10", "USYC": "0", "cirBTC": "0", "WETH": "0", "TOKN": "42"}
    pinned = {p[1] for _, m, p in fake.calls if m in ("eth_call", "eth_getBalance")}
    assert pinned == {hex(BLOCK)}
    assert out["explorer_url"] == f"https://explorer.arc.io/address/{HOLDER}"


def test_balance_rejects_bad_inputs(call):
    assert call("arc_balance", {"address": "0x12"})["error_type"] == "invalid_input"
    assert call("arc_balance", {"address": HOLDER, "tokens": "EURC"})["error_type"] == "invalid_input"
    assert call("arc_balance", {"address": HOLDER, "tokens": [OTHER] * 11})["error_type"] == "invalid_input"
    out = call("arc_balance", {"address": HOLDER, "tokens": [OTHER]})
    assert out["error_type"] == "invalid_input" and "decimals()" in out["error"]


def test_testnet_uses_its_own_chain_id_and_explorer(call, fake):
    fake.chain_id = 5042002
    out = call("arc_network_status", {"network": "testnet"})
    assert out["chain_id"] == 5042002
    assert out["explorer"] == "https://explorer.testnet.arc.io"
    assert out["rpc_endpoint"] == "rpc.testnet.arc.io"
    assert call("arc_network_status", {"network": "devnet"})["error_type"] == "invalid_input"


def test_settings_default_network_and_private_rpc_secret(call, fake):
    fake.chain_id = 5042002
    keyed = "https://my.node.example/v2/SECRET-KEY-123"
    out = call("arc_network_status", settings={"network": "testnet"}, secrets={"STABLELENS_TESTNET_RPC_URL": keyed})
    assert out["rpc_endpoint"] == "my.node.example"
    assert fake.calls[0][0] == keyed  # the private endpoint is tried first
    bad = call("arc_network_status", secrets={"STABLELENS_MAINNET_RPC_URL": "http://evil.example/KEY"})
    assert bad["error_type"] == "invalid_input" and "STABLELENS_MAINNET_RPC_URL" in bad["error"]
    assert "KEY" not in json.dumps(bad)


def test_a_private_endpoint_key_never_appears_in_output(call, fake):
    keyed = "https://my.node.example/v2/SECRET-KEY-123"
    fake.behaviour[keyed] = "http500"
    for url in MAINNET_URLS:
        fake.behaviour[url] = "down"
    out = call("arc_balance", {"address": HOLDER}, secrets={"STABLELENS_MAINNET_RPC_URL": keyed})
    assert out["error_type"] == "network_unavailable" and "my.node.example: HTTP 500" in out["error"]
    assert "SECRET-KEY-123" not in json.dumps(out)


def test_a_failing_secret_lookup_is_an_error_not_a_crash(fake):
    def unscoped(_name):
        raise RuntimeError("no profile secret scope active")

    handler = tools.make_handler("arc_network_status", transport=fake.transport, secrets=unscoped)
    out = json.loads(handler({}))
    assert out["error_type"] == "internal_error" and "secret scope" in out["error"]
    assert fake.calls == []  # never silently falls back to the public endpoints


def test_wrong_chain_everywhere_is_reported_not_answered(call, fake):
    fake.chain_id = 1
    out = call("arc_balance", {"address": HOLDER})
    assert out["error_type"] == "network_unavailable" and "expected 5042" in out["error"]


def test_failover_is_invisible_to_the_tool(call, fake):
    fake.behaviour[MAINNET_URLS[0]] = "html"
    out = call("arc_balance", {"address": HOLDER})
    assert out["tokens"][0]["balance"]["formatted"] == "2.5"
    assert out["rpc_endpoint"] == "rpc.blockdaemon.mainnet.arc.io"


def _transfer_log(token, sender, recipient, value):
    return {"address": token.lower(), "topics": [TRANSFER_TOPIC, topic(sender), topic(recipient)], "data": "0x" + value.to_bytes(32, "big").hex()}


def test_transaction_success_with_transfers(call, fake):
    fake.tx = {
        "hash": TX, "from": HOLDER.lower(), "to": EURC.lower(), "value": "0x0", "nonce": "0x7",
        "input": "0xa9059cbb" + "00" * 64, "blockNumber": hex(BLOCK), "gasPrice": hex(25 * 10**9),
    }
    fake.receipt = {
        "status": "0x1", "blockNumber": hex(BLOCK), "gasUsed": hex(52_000), "effectiveGasPrice": hex(25 * 10**9),
        "contractAddress": None,
        "logs": [
            _transfer_log(EURC, HOLDER, OTHER, 1_250_000),
            _transfer_log(UNKNOWN_TOKEN, OTHER, HOLDER, 5 * 10**9),
            {"address": EURC.lower(), "topics": [TRANSFER_TOPIC, topic(HOLDER), topic(OTHER), "0x" + "00" * 32], "data": "0x"},
            {"address": EURC.lower(), "topics": ["0x" + "11" * 32], "data": "0x"},
        ],
    }
    out = call("arc_transaction", {"tx_hash": TX.upper().replace("0X", "0x")})
    assert out["status"] == "success" and out["block"] == BLOCK
    assert out["from"] == HOLDER and out["to"] == EURC and out["to_label"] == "EURC (Euro Coin)"
    assert out["fee"]["formatted"] == "0.0013"
    assert out["method_selector"] == "0xa9059cbb"
    assert out["log_count"] == 4
    assert [t["amount"]["formatted"] for t in out["token_transfers"]] == ["1.25", "5"]
    assert out["token_transfers"][1]["amount"]["symbol"] == "TOKN"
    assert out["explorer_url"] == f"https://explorer.arc.io/tx/{TX}"


def test_transaction_reverted_pending_and_missing(call, fake):
    fake.tx = {"from": HOLDER.lower(), "to": None, "value": hex(10**18), "nonce": "0x0", "input": "0x", "blockNumber": hex(BLOCK)}
    fake.receipt = {"status": "0x0", "blockNumber": hex(BLOCK), "gasUsed": hex(21_000), "effectiveGasPrice": hex(10**9),
                    "contractAddress": OTHER.lower(), "logs": []}
    out = call("arc_transaction", {"tx_hash": TX})
    assert out["status"] == "reverted" and out["contract_created"] == OTHER and out["to"] is None
    assert out["value"]["formatted"] == "1"

    fake.tx = dict(fake.tx, blockNumber=None)
    assert call("arc_transaction", {"tx_hash": TX})["status"] == "pending"

    fake.tx = None
    out = call("arc_transaction", {"tx_hash": TX})
    assert out["status"] == "not_found" and "Arc Mainnet" in out["note"]

    assert call("arc_transaction", {"tx_hash": "0xdead"})["error_type"] == "invalid_input"


def test_address_contract_and_account(call):
    out = call("arc_address", {"address": "0x77777777dcc4d5a8b6e418fd04d8997ef11000ee"})
    assert out["type"] == "account" and out["known_as"] == "Circle Gateway Wallet"
    out = call("arc_address", {"address": EURC})
    assert out["type"] == "contract" and out["code_size_bytes"] == 2 and out["known_as"] == "EURC (Euro Coin)"
    assert out["nonce"] == 7


def test_token_by_symbol_address_and_non_contract(call):
    out = call("arc_token", {"token": "eurc"})
    assert out["token"] == EURC and out["symbol"] == "EURC" and out["decimals"] == 6
    assert out["total_supply"]["formatted"] == "123.456789"
    out = call("arc_token", {"token": UNKNOWN_TOKEN})
    assert (out["name"], out["symbol"], out["decimals"], out["total_supply"]["formatted"]) == ("Test Token", "TOKN", 9, "1000000000")
    assert call("arc_token", {"token": OTHER})["error_type"] == "invalid_input"


def test_block_latest_number_and_future(call):
    out = call("arc_block")
    assert out["block"] == BLOCK and out["transaction_count"] == 2 and out["proposer"] == OTHER
    assert out["time"] == "2026-10-26T07:33:20Z" and TIMESTAMP
    assert call("arc_block", {"block": str(BLOCK - 5)})["block"] == BLOCK - 5
    future = call("arc_block", {"block": str(BLOCK + 1)})
    assert future["error_type"] == "invalid_input" and "does not exist yet" in future["error"]


def test_handlers_never_raise(fake):
    def broken(url, payload, timeout):
        raise RuntimeError("boom")

    for name in tools._BODIES:
        handler = tools.make_handler(name, transport=broken)
        for args in (None, [], {"address": HOLDER, "tx_hash": TX, "token": EURC}, {"network": 5}):
            result = json.loads(handler(args))
            assert "error" in result and "error_type" in result


def test_malformed_rpc_values_become_internal_errors_not_crashes(call, fake):
    original = fake.answer

    def garbled(method, params):
        if method == "eth_gasPrice":
            return "not-hex"
        return original(method, params)

    fake.answer = garbled
    out = call("arc_network_status")
    assert out["error_type"] == "internal_error" and "gas price" in out["error"]


def test_one_token_that_cannot_answer_balance_of_does_not_sink_the_snapshot(call, fake):
    original = fake.eth_call

    def picky(to, data):
        if to == EURC.lower() and data.startswith("0x70a08231"):
            return ("revert", "execution reverted")
        return original(to, data)

    fake.eth_call = picky
    out = call("arc_balance", {"address": HOLDER})
    eurc = next(t for t in out["tokens"] if t["symbol"] == "EURC")
    assert "balance" not in eurc and "reverted" in eurc["error"]
    usdc = next(t for t in out["tokens"] if t["symbol"] == "USDC")
    assert usdc["balance"]["formatted"] == "2.5"

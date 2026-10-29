"""Checks against the real Arc networks. Skipped unless STABLELENS_LIVE=1.

These confirm the hand-maintained tables in chain.py against the chain itself: every token's
symbol and decimals, and that every listed contract has code. Run them before a release.
"""

import json
import os

import pytest

from stablelens import chain, tools
from stablelens.codec import call_data, decode_small_uint, decode_string

pytestmark = pytest.mark.skipif(os.environ.get("STABLELENS_LIVE") != "1", reason="set STABLELENS_LIVE=1 to run")


def run(name, args):
    return json.loads(tools.make_handler(name)(args))


@pytest.mark.parametrize("network", list(chain.NETWORKS))
def test_status(network):
    out = run("arc_network_status", {"network": network})
    assert "error" not in out, out
    assert out["chain_id"] == chain.NETWORKS[network].chain_id
    assert out["latest_block"] > 0 and out["seconds_since_latest_block"] < 300


@pytest.mark.parametrize("network", list(chain.NETWORKS))
def test_every_endpoint_answers_with_the_right_chain(network):
    net = chain.NETWORKS[network]
    for url in net.rpc_urls:
        client = tools.ArcRPC((url,), net.chain_id, timeout=15)
        assert int(client.call("eth_blockNumber"), 16) > 0, url


@pytest.mark.parametrize("network", list(chain.NETWORKS))
def test_token_table_matches_the_chain(network):
    net = chain.NETWORKS[network]
    client = tools.ArcRPC(net.rpc_urls, net.chain_id, timeout=15)
    for token in net.tokens:
        decimals = decode_small_uint(client.call("eth_call", [{"to": token.address, "data": call_data("decimals")}, "latest"]))
        symbol = decode_string(client.call("eth_call", [{"to": token.address, "data": call_data("symbol")}, "latest"]))
        assert (symbol, decimals) == (token.symbol, token.decimals), (network, token)


@pytest.mark.parametrize("network", list(chain.NETWORKS))
def test_every_listed_contract_has_code(network):
    net = chain.NETWORKS[network]
    client = tools.ArcRPC(net.rpc_urls, net.chain_id, timeout=15)
    missing = [label for address, label in net.contracts.items() if client.call("eth_getCode", [address, "latest"]) in ("0x", "0x0")]
    assert not missing, (network, missing)


def test_balance_and_token_tools_end_to_end():
    out = run("arc_balance", {"address": "0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE"})
    assert "error" not in out, out
    usdc = next(t for t in out["tokens"] if t["symbol"] == "USDC")
    # The native (18-decimal) and ERC-20 (6-decimal) views are the same balance.
    native = int(out["native_usdc"]["raw"])
    assert int(usdc["balance"]["raw"]) == native // 10**12
    token = run("arc_token", {"token": "EURC"})
    assert token["symbol"] == "EURC" and token["decimals"] == 6 and int(token["total_supply"]["raw"]) > 0


def test_a_real_transaction_from_the_latest_blocks():
    client = tools.ArcRPC(chain.MAINNET.rpc_urls, chain.MAINNET.chain_id, timeout=15)
    latest = int(client.call("eth_blockNumber"), 16)
    for number in range(latest, latest - 50, -1):
        block = client.call("eth_getBlockByNumber", [hex(number), False])
        if block["transactions"]:
            out = run("arc_transaction", {"tx_hash": block["transactions"][0]})
            assert out["status"] in ("success", "reverted"), out
            assert out["block"] == number
            return
    pytest.skip("no transactions in the last 50 blocks")

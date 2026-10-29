import json
import threading

import pytest

from stablelens import rpc

URLS = ("https://a.example", "https://b.example", "https://c.example")


def client(fake, **kw):
    return rpc.ArcRPC(URLS, 5042, transport=fake.transport, **kw)


def test_verifies_chain_id_once_per_endpoint(fake):
    c = client(fake)
    assert c.call("eth_blockNumber") == hex(28_500_000)
    assert c.call("eth_gasPrice") == hex(25 * 10**9)
    methods = [m for _, m, _ in fake.calls]
    assert methods == ["eth_chainId", "eth_blockNumber", "eth_gasPrice"]


@pytest.mark.parametrize("behaviour", ["down", "timeout", "http500", "html", "ratelimit"])
def test_fails_over_when_an_endpoint_cannot_answer(fake, behaviour):
    fake.behaviour[URLS[0]] = behaviour
    c = client(fake)
    assert c.call("eth_blockNumber") == hex(28_500_000)
    assert c.last_endpoint == "b.example"
    # The endpoint that answered is tried first next time.
    fake.calls.clear()
    c.call("eth_gasPrice")
    assert fake.calls[0][0] == URLS[1]


def test_an_endpoint_on_another_chain_is_never_used(fake):
    fake.behaviour[URLS[0]] = "wrong_chain"
    c = client(fake)
    assert c.call("eth_blockNumber") == hex(28_500_000)
    assert URLS[0] in c._wrong_chain
    fake.calls.clear()
    c._preferred = 0
    c.call("eth_gasPrice")
    assert all(url != URLS[0] for url, _, _ in fake.calls)


def test_all_endpoints_down_raises_network_unavailable(fake):
    for url in URLS:
        fake.behaviour[url] = "down"
    with pytest.raises(rpc.NetworkUnavailable, match="a.example.*b.example.*c.example"):
        client(fake).call("eth_blockNumber")


def test_all_endpoints_on_the_wrong_chain(fake):
    fake.chain_id = 1
    with pytest.raises(rpc.NetworkUnavailable, match="expected 5042"):
        client(fake).call("eth_blockNumber")


def test_a_chain_error_is_returned_not_retried(fake):
    c = client(fake)
    with pytest.raises(rpc.RPCError, match="reverted") as err:
        c.call("eth_call", [{"to": "0x" + "11" * 20, "data": "0x12345678"}, "latest"])
    assert err.value.code == 3
    assert [u for u, m, _ in fake.calls if m == "eth_call"] == [URLS[0]]


def test_deadline_stops_the_failover_loop(fake):
    now = [0.0]

    def slow(url, payload, timeout):
        now[0] += 6
        raise TimeoutError("slow")

    c = rpc.ArcRPC(URLS, 5042, transport=slow, timeout=5, clock=lambda: now[0])
    with pytest.raises(rpc.NetworkUnavailable, match="deadline"):
        c.call("eth_blockNumber", deadline=10)


def test_oversized_response_fails_over(fake):
    def big(url, payload, timeout):
        if url == URLS[0]:
            return rpc._Response(200, b"x" * (rpc.MAX_RESPONSE_BYTES + 1))
        return fake.transport(url, payload, timeout)

    c = rpc.ArcRPC(URLS, 5042, transport=big)
    assert c.call("eth_blockNumber") == hex(28_500_000)


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://rpc.example.org", True),
        ("http://127.0.0.1:8545", True),
        ("http://localhost:8545", True),
        ("http://rpc.example.org", False),
        ("ftp://x", False),
        ("https://", False),
    ],
)
def test_endpoint_validation(url, ok):
    if ok:
        assert rpc.validate_endpoint(url) == url
    else:
        with pytest.raises(ValueError):
            rpc.validate_endpoint(url)


def test_concurrent_calls_share_one_client_safely(fake):
    fake.behaviour[URLS[0]] = "down"
    c = client(fake)
    errors = []

    def worker():
        try:
            for _ in range(20):
                assert c.call("eth_blockNumber") == hex(28_500_000)
        except Exception as err:  # pragma: no cover - surfaced below
            errors.append(err)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert c.last_endpoint == "b.example"
    chain_checks = [u for u, m, _ in fake.calls if m == "eth_chainId" and u == URLS[1]]
    assert 1 <= len(chain_checks) <= 8  # verified once per racing thread at most, then cached
    ids = [json.loads(json.dumps(p)) for _, _, p in fake.calls]
    assert ids  # requests were made


# Seen live: rpc.blockdaemon.testnet.arc.io sometimes answers with a gzip body that it labels
# application/json, even when the request did not offer gzip.
import gzip  # noqa: E402
import zlib  # noqa: E402

ANSWER = b'{"jsonrpc":"2.0","id":1,"result":"0x4cef52"}'


@pytest.mark.parametrize(
    "body,encoding",
    [
        (gzip.compress(ANSWER), "gzip"),
        (gzip.compress(ANSWER), None),  # unlabelled gzip, recognised by its magic bytes
        (zlib.compress(ANSWER), "deflate"),
        (ANSWER, None),
        (ANSWER, "identity"),
    ],
)
def test_compressed_bodies_are_decoded(body, encoding):
    assert rpc.decode_body(body, encoding) == ANSWER


def test_a_compression_bomb_is_cut_off_not_expanded():
    bomb = gzip.compress(b"0" * (rpc.MAX_RESPONSE_BYTES * 4))
    assert len(rpc.decode_body(bomb, "gzip")) == rpc.MAX_RESPONSE_BYTES + 1


def test_a_corrupt_gzip_body_is_left_as_is_and_fails_over(fake):
    corrupt = b"\x1f\x8b" + b"\x00" * 20
    assert rpc.decode_body(corrupt, "gzip") == corrupt

    def broken(url, payload, timeout):
        if url == URLS[0]:
            return rpc._Response(200, corrupt)
        return fake.transport(url, payload, timeout)

    c = rpc.ArcRPC(URLS, 5042, transport=broken)
    assert c.call("eth_blockNumber") == hex(28_500_000)
    assert c.last_endpoint == "b.example"


def test_an_endpoint_answering_in_gzip_is_used_not_skipped(fake):
    def gzipped(url, payload, timeout):
        plain = fake.transport(url, payload, timeout)
        return rpc._Response(plain.status, gzip.compress(plain.body)) if url == URLS[0] else plain

    c = rpc.ArcRPC(URLS, 5042, transport=gzipped)
    assert c.call("eth_blockNumber") == hex(28_500_000)
    assert c.last_endpoint == "a.example"


def test_an_endpoint_key_in_an_error_message_is_redacted(fake):
    keyed = "https://user:pass@my.node.example/v2/SECRET-KEY-123?apikey=ALSO-SECRET"

    def leaky(url, payload, timeout):
        raise OSError(f"cannot reach {url} (path /v2/SECRET-KEY-123?apikey=ALSO-SECRET)")

    with pytest.raises(rpc.NetworkUnavailable) as err:
        rpc.ArcRPC((keyed,), 5042, transport=leaky).call("eth_blockNumber")
    text = str(err.value)
    assert "my.node.example" in text
    for secret in ("SECRET-KEY-123", "ALSO-SECRET", "pass"):
        assert secret not in text


@pytest.mark.parametrize("url", ["https://a.example/k y", "https://a.example/\nx", "https://a.example/\x00"])
def test_endpoint_urls_with_whitespace_or_control_characters_are_refused(url):
    with pytest.raises(ValueError):
        rpc.validate_endpoint(url)

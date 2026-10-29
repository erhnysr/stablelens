"""The plugin as Hermes sees it: register(), the manifest, and the secret route."""

import json
import pathlib
import re
import sys
import types

import stablelens
from stablelens import schemas

ROOT = pathlib.Path(__file__).resolve().parents[1]


class FakeContext:
    def __init__(self, config=None, broken_config=False):
        self.tools = {}
        self.config = config or {}
        self.broken_config = broken_config

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False,
                      description="", emoji="", override=False):
        assert name not in self.tools
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "override": override}

    def get_config(self, key, default=None):
        if self.broken_config:
            raise RuntimeError("no plugin settings on this host")
        return self.config.get(key, default)


def manifest_list(field):
    text = (ROOT / "plugin.yaml").read_text()
    block = re.search(rf"^{field}:\n((?:  - .+\n)+)", text, re.M)
    return [line.strip()[2:] for line in block.group(1).splitlines()]


def test_registers_exactly_the_declared_tools(monkeypatch):
    def no_network(*_a, **_k):
        raise AssertionError("registration must not touch the network")

    monkeypatch.setattr(stablelens.rpc.urllib.request, "urlopen", no_network)
    ctx = FakeContext()
    stablelens.register(ctx)
    assert sorted(ctx.tools) == sorted(manifest_list("provides_tools")) == sorted(s["name"] for s in schemas.ALL)
    for name, entry in ctx.tools.items():
        assert entry["toolset"] == "stablelens" and entry["override"] is False
        assert entry["schema"]["name"] == name and entry["schema"]["parameters"]["type"] == "object"


def test_schemas_are_plain_json():
    for schema in schemas.ALL:
        assert json.loads(json.dumps(schema)) == schema
        assert len(schema["description"]) < 1024


def test_a_host_without_settings_still_registers_and_answers_input_errors():
    ctx = FakeContext(broken_config=True)
    stablelens.register(ctx)
    out = json.loads(ctx.tools["arc_balance"]["handler"]({"address": "nope"}))
    assert out["error_type"] == "invalid_input"


def test_private_endpoint_is_read_through_hermes_secret_scope(monkeypatch):
    asked = []
    agent = types.ModuleType("agent")
    scope = types.ModuleType("agent.secret_scope")

    def get_secret(name, default=None):
        asked.append(name)
        return "http://not-allowed.example/KEY" if name == "STABLELENS_TESTNET_RPC_URL" else default

    scope.get_secret = get_secret
    agent.secret_scope = scope
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)

    ctx = FakeContext(config={"network": "testnet"})
    stablelens.register(ctx)
    out = json.loads(ctx.tools["arc_network_status"]["handler"]({}))
    assert asked == ["STABLELENS_TESTNET_RPC_URL"]
    assert out["error_type"] == "invalid_input" and "STABLELENS_TESTNET_RPC_URL" in out["error"]


def test_manifest_secrets_match_the_names_the_code_reads():
    text = (ROOT / "plugin.yaml").read_text()
    declared = set(re.findall(r"^    env: (\S+)$", text, re.M))
    assert declared == set(stablelens.tools.RPC_URL_SECRETS.values())


def test_versions_agree():
    text = (ROOT / "plugin.yaml").read_text()
    version = re.search(r"^version: (\S+)$", text, re.M).group(1)
    assert stablelens.rpc.USER_AGENT.endswith("/" + version)
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert f'version = "{version}"' in pyproject

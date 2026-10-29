"""Tool schemas: what the model reads to decide when and how to call each tool."""

_NETWORK = {
    "type": "string",
    "enum": ["mainnet", "testnet"],
    "description": "Arc Network to read: 'mainnet' (chain 5042) or 'testnet' (chain 5042002). Defaults to the plugin setting, mainnet unless changed.",
}

_ADDRESS = {"type": "string", "description": "A 0x-prefixed 20-byte address (40 hex characters)."}

NETWORK_STATUS = {
    "name": "arc_network_status",
    "description": (
        "Live status of Arc Network, the stablecoin-native L1 where USDC is the gas token: latest "
        "block and its time, current gas price, and what a plain transfer costs in USDC. Use it "
        "when the user asks whether Arc is up, how fast blocks are, or what fees cost. Read-only."
    ),
    "parameters": {"type": "object", "properties": {"network": _NETWORK}, "required": []},
}

BALANCE = {
    "name": "arc_balance",
    "description": (
        "Balances of an address on Arc Network: native USDC (the gas balance) and the ERC-20 "
        "balances of Circle's tokens on Arc (USDC, EURC, USYC, cirBTC, WETH), all read at one "
        "block. Add other ERC-20 tokens by symbol or contract address with 'tokens'. Read-only; "
        "amounts are exact decimal strings."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "address": _ADDRESS,
            "tokens": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 10,
                "description": "Extra ERC-20 tokens to include, as contract addresses (or known symbols).",
            },
            "network": _NETWORK,
        },
        "required": ["address"],
    },
}

TRANSACTION = {
    "name": "arc_transaction",
    "description": (
        "Look up a transaction on Arc Network by hash: success, reverted or pending; block and "
        "time; sender and recipient (with Circle contract names such as CCTP or Gateway when "
        "known); the fee paid in USDC; and the ERC-20 token transfers it made, with amounts. "
        "Returns an explorer link. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tx_hash": {"type": "string", "description": "The 0x-prefixed 32-byte transaction hash (64 hex characters)."},
            "network": _NETWORK,
        },
        "required": ["tx_hash"],
    },
}

ADDRESS = {
    "name": "arc_address",
    "description": (
        "What an address is on Arc Network: a contract or a plain account, its nonce, its native "
        "USDC balance, and Circle's name for it when it is one of Circle's or Arc's published "
        "contracts (USDC, EURC, CCTP, Gateway, ERC-8004 registries, ...). Read-only."
    ),
    "parameters": {"type": "object", "properties": {"address": _ADDRESS, "network": _NETWORK}, "required": ["address"]},
}

TOKEN = {
    "name": "arc_token",
    "description": (
        "ERC-20 token details on Arc Network: name, symbol, decimals and total supply, read from "
        "the contract. Accepts a contract address, or the symbol of one of Circle's tokens on Arc "
        "(USDC, EURC, USYC, cirBTC, WETH). Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "token": {"type": "string", "description": "Token contract address, or a Circle token symbol such as EURC."},
            "network": _NETWORK,
        },
        "required": ["token"],
    },
}

BLOCK = {
    "name": "arc_block",
    "description": (
        "A block on Arc Network: number, hash, time, transaction count, gas used and base fee. "
        "Pass a block number, or 'latest', 'safe' or 'finalized'. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "block": {
                "type": "string",
                "description": "Block number in decimal (e.g. '28500000'), or 'latest' (default), 'safe' or 'finalized'.",
            },
            "network": _NETWORK,
        },
        "required": [],
    },
}

ALL = (NETWORK_STATUS, BALANCE, TRANSACTION, ADDRESS, TOKEN, BLOCK)

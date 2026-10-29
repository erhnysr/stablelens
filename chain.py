"""Arc Network constants: chain ids, public RPC endpoints, explorers and Circle's contracts.

Sources, checked when this file was written (2026-10-29):
- RPC endpoints and explorers: viem's chain definitions `arc` and `arcTestnet`.
- Contract addresses and decimals: Arc's contract address reference (docs.arc.io).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Token:
    symbol: str
    name: str
    address: str  # EIP-55
    decimals: int


@dataclass(frozen=True)
class Network:
    key: str
    name: str
    chain_id: int
    rpc_urls: tuple[str, ...]
    explorer: str
    tokens: tuple[Token, ...]
    contracts: dict[str, str] = field(default_factory=dict)  # EIP-55 address -> label


# Native USDC is the gas token (18 decimals). The ERC-20 interface at 0x3600...0000 reads the same
# balance with 6 decimals. Arc's docs advise reading balances and amounts through the ERC-20
# interface and calling decimals(); the native 18-decimal value is reported for gas and fees.
NATIVE_DECIMALS = 18
USDC_INTERFACE = "0x3600000000000000000000000000000000000000"

_SHARED_CONTRACTS = {
    "0xcA11bde05977b3631167028862bE2a173976CA11": "Multicall3",
    "0x4e59b44847b379578588920cA78FbF26c0B4956C": "CREATE2 Factory (Arachnid)",
    "0x000000000022D473030F116dDEE9F6B43aC78BA3": "Permit2",
    "0x5294E9927c3306DcBaDb03fe70b92e01cCede505": "Memo",
    "0x522fAf9A91c41c443c66765030741e4AaCe147D0": "Multicall3From",
}

MAINNET = Network(
    key="mainnet",
    name="Arc Mainnet",
    chain_id=5042,
    rpc_urls=(
        "https://rpc.mainnet.arc.io",
        "https://rpc.blockdaemon.mainnet.arc.io",
        "https://rpc.drpc.mainnet.arc.io",
        "https://rpc.quicknode.mainnet.arc.io",
    ),
    explorer="https://explorer.arc.io",
    tokens=(
        Token("USDC", "USD Coin", USDC_INTERFACE, 6),
        Token("EURC", "Euro Coin", "0xbEf5f6d51CB62b58e6A8f77868681825C6fe21c1", 6),
        Token("USYC", "US Yield Coin", "0x8a5D989Bbb96929F689B0200f435f53dA42bF490", 6),
        Token("cirBTC", "Circle Bitcoin", "0x171A4217b86A807A64eB94757Db6849fb4bDbAA0", 8),
        Token("WETH", "Wrapped Ether (bridged)", "0x128cC466B61f542da60c70e3aA11c10e19B84EDB", 18),
    ),
    contracts={
        **_SHARED_CONTRACTS,
        "0xb69ecb156Dc0028198028c501340d5367845ca72": "USYC Entitlements",
        "0x51A8CE47dC08ba5CD19c7aa84EA6fD6664f60f9b": "USYC Teller",
        "0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d": "CCTP TokenMessengerV2",
        "0x71f54F818671cD0D7ea140Da213e5C8b5C92a408": "CCTP TokenMessengerWithFees",
        "0x81D40F21F12A8F0E3252Bccb954D722d4c464B64": "CCTP MessageTransmitterV2",
        "0xfd78EE919681417d192449715b2594ab58f5D002": "CCTP TokenMinterV2",
        "0xec546b6B005471ECf012e5aF77FBeC07e0FD8f78": "CCTP MessageV2",
        "0x431871229103b780868f8C6BB820cd16ECf942BC": "CrossChainTokenService",
        "0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE": "Circle Gateway Wallet",
        "0x2222222d7164433c4C09B0b0D809a9b52C04C205": "Circle Gateway Minter",
        "0xe2E5F173576B513d994073CCbDaCBE027d43DFe6": "StableFX FxEscrow",
        "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432": "ERC-8004 IdentityRegistry",
        "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63": "ERC-8004 ReputationRegistry",
        "0x8004Cc8439f36fd5F9F049D9fF86523Df6dAAB58": "ERC-8004 ValidationRegistry",
    },
)

TESTNET = Network(
    key="testnet",
    name="Arc Testnet",
    chain_id=5042002,
    rpc_urls=(
        "https://rpc.testnet.arc.io",
        "https://rpc.blockdaemon.testnet.arc.io",
        "https://rpc.drpc.testnet.arc.io",
        "https://rpc.quicknode.testnet.arc.io",
    ),
    explorer="https://explorer.testnet.arc.io",
    tokens=(
        Token("USDC", "USD Coin", USDC_INTERFACE, 6),
        Token("EURC", "Euro Coin", "0x89B50855Aa3bE2F677cD6303Cec089B5F319D72a", 6),
        Token("USYC", "US Yield Coin", "0xe9185F0c5F296Ed1797AaE4238D26CCaBEadb86C", 6),
        Token("cirBTC", "Circle Bitcoin", "0xf0C4a4CE82A5746AbAAd9425360Ab04fbBA432BF", 8),
        Token("WETH", "Wrapped Ether (bridged)", "0x2c4047028a72803939b6fb674D01bC059B5C4961", 18),
    ),
    contracts={
        **_SHARED_CONTRACTS,
        "0xcc205224862c7641930c87679e98999d23c26113": "USYC Entitlements",
        "0x9fdF14c5B14173D74C08Af27AebFf39240dC105A": "USYC Teller",
        "0x8FE6B999Dc680CcFDD5Bf7EB0974218be2542DAA": "CCTP TokenMessengerV2",
        "0x8745D906D67C346E5eb1aEEED38Eb87F34DF0C0A": "CCTP TokenMessengerWithFees",
        "0xE737e5cEBEEBa77EFE34D4aa090756590b1CE275": "CCTP MessageTransmitterV2",
        "0xb43db544E2c27092c107639Ad201b3dEfAbcF192": "CCTP TokenMinterV2",
        "0xbaC0179bB358A8936169a63408C8481D582390C4": "CCTP MessageV2",
        "0x63753E722bd2C2A5DF6EE19C5106662208B81077": "CrossChainTokenService",
        "0x0077777d7EBA4688BDeF3E311b846F25870A19B9": "Circle Gateway Wallet",
        "0x0022222ABE238Cc2C7Bb1f21003F0a260052475B": "Circle Gateway Minter",
        "0xd68256f4D69C6BbEcB873D8588AE0Dc6B8E22E10": "StableFX FxEscrow",
        "0x8004A818BFB912233c491871b3d84c89A494BD9e": "ERC-8004 IdentityRegistry",
        "0x8004B663056A597Dffe9eCcC1965A193B7388713": "ERC-8004 ReputationRegistry",
        "0x8004Cb1BF31DAf7788923b405b754f57acEB4272": "ERC-8004 ValidationRegistry",
        "0x0747EEf0706327138c69792bF28Cd525089e4583": "ERC-8183 AgenticCommerce",
    },
)

NETWORKS = {MAINNET.key: MAINNET, TESTNET.key: TESTNET}


def token_by_symbol(network: Network, symbol: str) -> Token | None:
    wanted = symbol.strip().lower()
    return next((t for t in network.tokens if t.symbol.lower() == wanted), None)


def token_by_address(network: Network, address: str) -> Token | None:
    wanted = address.lower()
    return next((t for t in network.tokens if t.address.lower() == wanted), None)


def label_of(network: Network, address: str) -> str | None:
    """Circle's or Arc's name for a known contract on this network, or None."""
    wanted = address.lower()
    token = token_by_address(network, address)
    if token is not None:
        return f"{token.symbol} ({token.name})"
    return next((label for addr, label in network.contracts.items() if addr.lower() == wanted), None)

"""Explicit controller inventory; do not count BaseRegistrar/NameWrapper events as fees."""
from dataclasses import dataclass

from eth_utils import keccak


def signature(text: str) -> str:
    return "0x" + keccak(text=text).hex()


@dataclass(frozen=True)
class Controller:
    name: str
    address: str
    start: int
    registration_shape: str
    refund_bug: bool = False


# 7M intentionally precedes both 2019 deployments; it is a scan floor, not a
# claimed deployment block. Later deployment blocks come from ENS / ens-indexer.
CONTROLLERS = (
    Controller("launch_2019", "0xf0ad5cad05e10572efceb849f6ff0c68f9700455", 7_000_000, "combined"),
    Controller("updated_2019", "0xb22c1c159d12461ea124b0deb4b5b93020e6ad16", 7_000_000, "combined"),
    Controller("legacy_2020", "0x283af0b28c62c092c9727f1ee09c02ca627eb7f5", 9_380_471, "combined"),
    Controller("wrapped_2023", "0x253553366da8546fc250f225fe3d25d0c782303b", 16_925_618, "split", True),
    Controller("unwrapped_2025", "0x59e16fccd424cc24e280be16e11bcd56fb0ce547", 22_764_821, "referrer"),
    # ENSv2 (ensdomains/contracts-v2 deployments/mainnet, tag v2-mainnet-deployment-1).
    # Fees are ERC-20 transfers to the beneficiary; events report exact token amounts.
    Controller("eth_registrar_v2", "0xd6d8369e55c94ae8afa714e6cf7ab5be54d5cc81", 26_183_999, "v2"),
    Controller("eth_renewer_v1_v2", "0x56c447f308f463a01121c41e72b0e7e7557fcb68", 26_184_001, "v2"),
)
BY_ADDRESS = {c.address: c for c in CONTROLLERS}
LEGACY_CONTROLLERS = tuple(c for c in CONTROLLERS if c.registration_shape != "v2")
V2_CONTROLLERS = tuple(c for c in CONTROLLERS if c.registration_shape == "v2")
# DAO execution 0xed4ea910…a741f removed the v1 controllers from the BaseRegistrar and
# handed its ownership to ETHRenewerV1, so v1 controllers cannot collect fees after
# this block. v1 logs, expiry history and ETH/USD prices are indexed through it
# (inclusive) and no further. Later wrapped-controller NameRenewed events are
# zero-value side effects of ETHRenewerV1.syncWrapper(); the v2 renewer reports the fee.
V1_HANDOVER_BLOCK = 26_282_117
REGISTERED = {
    "combined": signature("NameRegistered(string,bytes32,address,uint256,uint256)"),
    "split": signature("NameRegistered(string,bytes32,address,uint256,uint256,uint256)"),
    "referrer": signature("NameRegistered(string,bytes32,address,uint256,uint256,uint256,bytes32)"),
}
RENEWED = signature("NameRenewed(string,bytes32,uint256,uint256)")
RENEWED_REFERRER = signature("NameRenewed(string,bytes32,uint256,uint256,bytes32)")
RENEW = signature("renew(string,uint256)")[:10]
RENEW_REFERRER = signature("renew(string,uint256,bytes32)")[:10]
PRICE = signature("price(string,uint256,uint256)")[:10]
SOURCES = ("registration_base", "registration_premium", "registration_combined_legacy", "renewal")
BASE_REGISTRARS = (
    ("0xfac7bea255a6990f749363002136af6556b31e04", 7_000_000),
    ("0x57f1887a8bf19b14fc0df6fd9b2acc9af147ea85", 9_380_410),
)
BASE_REGISTERED = signature("NameRegistered(uint256,address,uint256)")
BASE_MIGRATED = signature("NameMigrated(uint256,address,uint256)")
BASE_RENEWED = signature("NameRenewed(uint256,uint256)")
BASE_FOR_CONTROLLER = {c.address: BASE_REGISTRARS[0 if i < 2 else 1][0] for i, c in enumerate(LEGACY_CONTROLLERS)}
V2_REGISTERED = signature("NameRegistered(uint256,string,address,address,address,uint64,address,bytes32,uint256,uint256)")
V2_RENEWED = signature("NameRenewed(uint256,string,uint64,uint64,address,bytes32,uint256)")
V2_SOURCES = ("v2_registration_base", "v2_registration_premium", "v2_renewal")
# The oracle's initial whitelist. Stablecoins are valued at face value ($1). Any
# other token added via PaymentTokenUpdated fails the chunk until reviewed here.
PAYMENT_TOKENS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
}
USD_DECIMALS = 18

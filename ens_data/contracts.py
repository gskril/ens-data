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
)
BY_ADDRESS = {c.address: c for c in CONTROLLERS}
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
BASE_FOR_CONTROLLER = {c.address: BASE_REGISTRARS[0 if i < 2 else 1][0] for i, c in enumerate(CONTROLLERS)}

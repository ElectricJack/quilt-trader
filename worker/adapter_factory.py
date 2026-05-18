from typing import Iterable

from worker.alpaca_adapter import AlpacaAdapter
from worker.broker_adapter import BrokerAdapter
from worker.tradier_adapter import TradierAdapter


class CredentialError(ValueError):
    """Raised when broker credentials are missing or malformed."""


REQUIRED_CREDENTIAL_FIELDS: dict[str, tuple[str, ...]] = {
    "alpaca": ("api_key", "secret_key"),
    "tradier": ("access_token", "account_id"),
    "polygon": ("api_key",),
    "thetadata": ("username", "password"),
}


def _require(creds: dict, fields: Iterable[str], broker: str) -> None:
    missing = [f for f in fields if not creds.get(f)]
    if missing:
        raise CredentialError(
            f"{broker}: missing required credential field(s): {', '.join(missing)}"
        )


def make_broker_adapter(
    broker_type: str,
    environment: str,
    credentials: dict,
) -> BrokerAdapter:
    if environment not in ("paper", "live"):
        raise ValueError(f"Unsupported environment: {environment}")

    bt = (broker_type or "").lower()
    if bt == "alpaca":
        _require(credentials, REQUIRED_CREDENTIAL_FIELDS["alpaca"], "alpaca")
        return AlpacaAdapter(
            api_key=credentials["api_key"],
            secret_key=credentials["secret_key"],
            paper=(environment == "paper"),
        )
    if bt == "tradier":
        _require(credentials, REQUIRED_CREDENTIAL_FIELDS["tradier"], "tradier")
        return TradierAdapter(
            access_token=credentials["access_token"],
            account_id=credentials["account_id"],
            sandbox=(environment == "paper"),
        )
    if bt == "polygon":
        _require(credentials, REQUIRED_CREDENTIAL_FIELDS["polygon"], "polygon")
        from worker.polygon_stream_adapter import PolygonStreamAdapter
        return PolygonStreamAdapter(api_key=credentials["api_key"])
    if bt == "thetadata":
        _require(credentials, REQUIRED_CREDENTIAL_FIELDS["thetadata"], "thetadata")
        from worker.thetadata_stream_adapter import ThetaDataStreamAdapter
        return ThetaDataStreamAdapter(
            username=credentials["username"],
            password=credentials["password"],
        )
    if bt == "interactive_brokers":
        raise NotImplementedError("Interactive Brokers adapter is not yet implemented")
    raise ValueError(f"Unknown broker_type: {broker_type}")

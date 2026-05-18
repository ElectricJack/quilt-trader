import pytest
from unittest.mock import MagicMock
from coordinator.services.lifecycle import LifecycleService, CompatibilityError


@pytest.fixture
def lifecycle():
    return LifecycleService(
        scraper_manager=MagicMock(),
    )


def test_check_compatibility_passes():
    result = LifecycleService.check_compatibility(
        {
            "supported_asset_types": ["equities", "options"],
            "options_level": 3,
            "account_features": ["margin", "short_selling"],
            "broker_type": "alpaca",
        },
        {
            "required_asset_types": ["equities", "options"],
            "required_options_level": 2,
            "required_account_features": ["margin"],
            "supported_brokers": ["alpaca", "tradier"],
        },
    )
    assert result.compatible is True


def test_check_compatibility_missing_asset_type():
    result = LifecycleService.check_compatibility(
        {
            "supported_asset_types": ["equities"],
            "options_level": None,
            "account_features": [],
            "broker_type": "alpaca",
        },
        {
            "required_asset_types": ["equities", "options"],
            "required_options_level": None,
            "required_account_features": [],
            "supported_brokers": None,
        },
    )
    assert result.compatible is False
    assert any("options" in m for m in result.mismatches)


def test_check_compatibility_insufficient_options_level():
    result = LifecycleService.check_compatibility(
        {
            "supported_asset_types": ["equities", "options"],
            "options_level": 1,
            "account_features": [],
            "broker_type": "alpaca",
        },
        {
            "required_asset_types": ["equities"],
            "required_options_level": 3,
            "required_account_features": [],
            "supported_brokers": None,
        },
    )
    assert result.compatible is False
    assert any("options level" in m.lower() for m in result.mismatches)


def test_check_compatibility_missing_feature():
    result = LifecycleService.check_compatibility(
        {
            "supported_asset_types": ["equities"],
            "options_level": None,
            "account_features": [],
            "broker_type": "alpaca",
        },
        {
            "required_asset_types": ["equities"],
            "required_options_level": None,
            "required_account_features": ["margin"],
            "supported_brokers": None,
        },
    )
    assert result.compatible is False
    assert any("margin" in m for m in result.mismatches)


def test_check_compatibility_unsupported_broker():
    result = LifecycleService.check_compatibility(
        {
            "supported_asset_types": ["equities"],
            "options_level": None,
            "account_features": [],
            "broker_type": "alpaca",
        },
        {
            "required_asset_types": ["equities"],
            "required_options_level": None,
            "required_account_features": [],
            "supported_brokers": ["tradier"],
        },
    )
    assert result.compatible is False
    assert any("broker" in m.lower() for m in result.mismatches)


def test_check_compatibility_any_broker_ok():
    result = LifecycleService.check_compatibility(
        {
            "supported_asset_types": ["equities"],
            "options_level": None,
            "account_features": [],
            "broker_type": "alpaca",
        },
        {
            "required_asset_types": ["equities"],
            "required_options_level": None,
            "required_account_features": [],
            "supported_brokers": None,
        },
    )
    assert result.compatible is True


@pytest.mark.asyncio
async def test_pre_start_checks_account_locked(lifecycle):
    account = MagicMock()
    account.locked_by = "other-instance"
    with pytest.raises(CompatibilityError, match="locked"):
        await lifecycle.pre_start_checks(account, MagicMock(), MagicMock(id="inst-1"))


@pytest.mark.asyncio
async def test_pre_start_checks_pass(lifecycle):
    account = MagicMock(
        locked_by=None,
        supported_asset_types=["equities"],
        options_level=None,
        account_features=[],
        broker_type="alpaca",
    )
    algorithm = MagicMock(
        required_asset_types=["equities"],
        required_options_level=None,
        required_account_features=[],
        supported_brokers=None,
        assets=None,
    )
    await lifecycle.pre_start_checks(account, algorithm, MagicMock(id="inst-1"))

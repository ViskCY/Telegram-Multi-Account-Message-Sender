"""Tests for the Account model."""

from app.models import Account


def test_account_premium_flag_defaults_to_false():
    account = Account(
        name="Test Account",
        phone_number="+1234567890",
        api_id=12345,
        api_hash="hash",
        session_path="/tmp/test.session",
    )

    assert account.is_premium is False


def test_account_premium_flag_can_be_updated():
    account = Account(
        name="Premium Account",
        phone_number="+10987654321",
        api_id=67890,
        api_hash="hash",
        session_path="/tmp/premium.session",
        is_premium=False,
    )

    account.is_premium = True

    assert account.is_premium is True

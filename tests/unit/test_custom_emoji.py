"""Unit tests for the custom emoji helpers."""

import asyncio
from types import SimpleNamespace

from telethon.tl import types

from app.core.custom_emoji_service import (
    CUSTOM_EMOJI_PLACEHOLDER,
    CustomEmojiMetadata,
    CustomEmojiService,
)
from app.models import Account


class StaticEmojiCache:
    """Simple cache stub that returns predetermined metadata."""

    def __init__(self, mapping):
        # mapping -> account_id -> set([emoji_ids])
        self.mapping = mapping

    async def get_metadata(self, client, account_id, emoji_ids):
        available = self.mapping.get(account_id, set())
        return {
            emoji_id: CustomEmojiMetadata(
                emoji_id=emoji_id,
                document_id=emoji_id,
                alt=f"alt-{emoji_id}",
            )
            for emoji_id in emoji_ids
            if emoji_id in available
        }


class DummyContext:
    """Asynchronous context manager returning a client wrapper stub."""

    def __init__(self, account_id):
        self.account_id = account_id
        self.client = SimpleNamespace()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def build_account(account_id: int) -> Account:
    account = Account(
        name=f"Account {account_id}",
        phone_number=f"+100000000{account_id}",
        api_id=account_id,
        api_hash=f"hash{account_id}",
        session_path=f"session{account_id}.session",
    )
    account.id = account_id
    return account


def test_extract_custom_emoji_ids_unique_order():
    service = CustomEmojiService(cache=StaticEmojiCache({}))
    ids = service.extract_custom_emoji_ids("Hello [emoji:123] [emoji:456] [emoji:123]")
    assert ids == [123, 456]


def test_prepare_message_text_builds_entities():
    service = CustomEmojiService(cache=StaticEmojiCache({1: {111}}))
    text, entities, missing = asyncio.run(
        service.prepare_message_text(SimpleNamespace(), 1, "Hi [emoji:111] there")
    )

    assert text == f"Hi {CUSTOM_EMOJI_PLACEHOLDER} there"
    assert missing == []
    assert len(entities) == 1
    assert isinstance(entities[0], types.MessageEntityCustomEmoji)
    assert entities[0].document_id == 111


def test_prepare_message_text_missing_metadata_uses_fallback():
    service = CustomEmojiService(cache=StaticEmojiCache({}))
    text, entities, missing = asyncio.run(
        service.prepare_message_text(SimpleNamespace(), 1, "Hi [emoji:222] there")
    )

    assert ":emoji-222:" in text
    assert entities == []
    assert missing == [222]


def test_validate_custom_emoji_ids_across_accounts(monkeypatch):
    mapping = {1: {101, 102}, 2: {102}}
    service = CustomEmojiService(
        cache=StaticEmojiCache(mapping),
        account_client_provider=lambda account: DummyContext(account.id),
    )

    accounts = [build_account(1), build_account(2)]
    monkeypatch.setattr(service, "_get_active_accounts", lambda: accounts)

    result = service.validate_custom_emoji_ids([101, 102, 999])

    assert result.valid_ids == {101, 102}
    assert result.missing_ids == {999}
    assert result.account_matches[102] == {1, 2}
    assert result.accounts_checked == [1, 2]


class FakeTelethonClient:
    """Minimal Telethon client stub used to exercise send_message flow."""

    def __init__(self):
        self.messages = []

    async def __call__(self, request):
        return [
            SimpleNamespace(
                id=555,
                attributes=[
                    types.DocumentAttributeCustomEmoji(
                        alt="grin", stickerset=types.InputStickerSetID(id=1, access_hash=2)
                    )
                ],
            )
        ]

    async def get_entity(self, peer):
        return SimpleNamespace(id=42)

    async def send_message(self, entity, message, **kwargs):
        self.messages.append((entity, message, kwargs))
        return SimpleNamespace(id=999)


def test_wrapper_send_message_injects_custom_emoji_entities():
    from app.core.telethon_client import TelegramClientWrapper

    account = build_account(7)
    wrapper = TelegramClientWrapper(account)
    wrapper.client = FakeTelethonClient()
    wrapper._connected = True
    wrapper._authorized = True

    result = asyncio.run(wrapper.send_message("@user", "Hello [emoji:555] there"))

    assert result["success"] is True
    assert wrapper.client.messages  # type: ignore[union-attr]
    _, message_text, kwargs = wrapper.client.messages[0]  # type: ignore[union-attr]
    assert message_text == f"Hello {CUSTOM_EMOJI_PLACEHOLDER} there"
    assert "entities" in kwargs
    assert kwargs["entities"][0].document_id == 555

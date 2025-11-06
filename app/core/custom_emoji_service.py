"""Utility helpers for working with custom Telegram emojis.

This module provides caching for custom emoji metadata, helpers for parsing
emoji placeholders inside templates, and validation utilities that integrate
with configured Telegram accounts.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Coroutine, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from telethon.tl import functions, types
from telethon.utils import add_surrogate

from sqlmodel import select

from ..models import Account
from ..services.db import get_session
from ..services.logger import get_logger


# Public constants -----------------------------------------------------------------

#: Placeholder pattern users can embed in templates: ``[emoji:<custom_emoji_id>]``
CUSTOM_EMOJI_PATTERN = re.compile(r"\[emoji:(?P<emoji_id>\d+)\]")

#: Character inserted into outgoing messages for each custom emoji. Telegram will
#: replace it with the custom emoji when accompanied by a
#: ``MessageEntityCustomEmoji`` entity. Using the object replacement character
#: keeps the rendered message clean while still working with surrogate offsets.
CUSTOM_EMOJI_PLACEHOLDER = "\uFFFC"

#: Default cache lifetime for emoji metadata (in seconds).
DEFAULT_CACHE_TTL = 60 * 60  # one hour


# Data objects ---------------------------------------------------------------------


@dataclass(frozen=True)
class CustomEmojiMetadata:
    """Metadata returned from Telegram for a custom emoji."""

    emoji_id: int
    document_id: int
    sticker_set_id: Optional[int] = None
    alt: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[int]]:
        """Serialize metadata to a dictionary for logging/testing purposes."""

        return {
            "emoji_id": self.emoji_id,
            "document_id": self.document_id,
            "sticker_set_id": self.sticker_set_id,
            "alt": self.alt,
        }


@dataclass
class _CachedEmoji:
    """Internal cache wrapper storing metadata and expiry timestamp."""

    metadata: CustomEmojiMetadata
    expires_at: datetime


@dataclass
class CustomEmojiValidationResult:
    """Result container describing validation outcome for a set of emoji IDs."""

    valid_ids: Set[int]
    missing_ids: Set[int]
    account_matches: Dict[int, Set[int]]
    accounts_checked: List[int]

    @property
    def is_successful(self) -> bool:
        """Return ``True`` when every emoji ID was located on an account."""

        return not self.missing_ids


# Cache implementation -------------------------------------------------------------


class CustomEmojiCache:
    """Simple in-memory cache keyed by ``account_id`` and ``emoji_id``."""

    def __init__(self, ttl_seconds: int = DEFAULT_CACHE_TTL):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[int, Dict[int, _CachedEmoji]] = {}
        self._lock = Lock()
        self.logger = get_logger()

    async def get_metadata(
        self,
        client: Any,
        account_id: int,
        emoji_ids: Sequence[int],
    ) -> Dict[int, CustomEmojiMetadata]:
        """Return metadata for the requested custom emojis.

        Args:
            client: Connected Telethon client capable of resolving emoji IDs.
            account_id: The account whose emoji set should be queried.
            emoji_ids: Iterable of emoji/document IDs to resolve.
        """

        if not emoji_ids:
            return {}

        unique_ids = list(dict.fromkeys(int(eid) for eid in emoji_ids))
        now = datetime.now(timezone.utc)
        result: Dict[int, CustomEmojiMetadata] = {}
        to_fetch: List[int] = []

        with self._lock:
            account_cache = self._cache.setdefault(account_id, {})
            for emoji_id in unique_ids:
                cached = account_cache.get(emoji_id)
                if cached and cached.expires_at > now:
                    result[emoji_id] = cached.metadata
                else:
                    to_fetch.append(emoji_id)

        if to_fetch:
            fetched = await self._fetch_from_api(client, to_fetch)
            with self._lock:
                account_cache = self._cache.setdefault(account_id, {})
                expiry = now + timedelta(seconds=self.ttl_seconds)
                for emoji_id, metadata in fetched.items():
                    account_cache[emoji_id] = _CachedEmoji(metadata=metadata, expires_at=expiry)
                    result[emoji_id] = metadata

        return result

    async def _fetch_from_api(
        self,
        client: Any,
        emoji_ids: Sequence[int],
    ) -> Dict[int, CustomEmojiMetadata]:
        """Query Telegram for custom emoji metadata using the Telethon client."""

        try:
            documents = await client(
                functions.messages.GetCustomEmojiDocumentsRequest(document_id=list(emoji_ids))
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.error(f"Failed to fetch custom emoji metadata: {exc}")
            return {}

        metadata: Dict[int, CustomEmojiMetadata] = {}
        for document in documents:
            emoji_id = int(getattr(document, "id", 0))
            if not emoji_id:
                continue

            alt: Optional[str] = None
            sticker_set_id: Optional[int] = None

            for attribute in getattr(document, "attributes", []):
                if isinstance(attribute, types.DocumentAttributeCustomEmoji):
                    alt = getattr(attribute, "alt", None)
                    stickerset = getattr(attribute, "stickerset", None)
                    sticker_set_id = getattr(stickerset, "id", None) if stickerset else None
                    break

            metadata[emoji_id] = CustomEmojiMetadata(
                emoji_id=emoji_id,
                document_id=emoji_id,
                sticker_set_id=sticker_set_id,
                alt=alt,
            )

        missing = set(int(eid) for eid in emoji_ids) - set(metadata.keys())
        if missing:
            missing_text = ", ".join(map(str, missing))
            self.logger.warning(f"Custom emoji IDs not returned by Telegram: {missing_text}")

        return metadata


# Service helpers ------------------------------------------------------------------


class CustomEmojiService:
    """High-level helper orchestrating cache lookups and template validation."""

    def __init__(
        self,
        cache: Optional[CustomEmojiCache] = None,
        account_client_provider=None,
    ):
        self.cache = cache or CustomEmojiCache()
        self.account_client_provider = account_client_provider or self._default_account_client_provider
        self.logger = get_logger()

    # Parsing ------------------------------------------------------------------

    @staticmethod
    def extract_custom_emoji_ids(text: str) -> List[int]:
        """Return ordered unique emoji IDs referenced in the provided text."""

        if not text:
            return []

        ids: List[int] = []
        seen: Set[int] = set()
        for match in CUSTOM_EMOJI_PATTERN.finditer(text):
            emoji_id = int(match.group("emoji_id"))
            if emoji_id not in seen:
                ids.append(emoji_id)
                seen.add(emoji_id)
        return ids

    # Message preparation ------------------------------------------------------

    async def prepare_message_text(
        self,
        client: Any,
        account_id: int,
        text: str,
    ) -> Tuple[str, List[types.MessageEntityCustomEmoji], List[int]]:
        """Replace placeholders with real entities and return the rendered message.

        Returns the transformed text, the list of ``MessageEntityCustomEmoji`` to
        pass to Telethon, and a list of emoji IDs that were missing metadata.
        """

        if not text or "[emoji:" not in text:
            return text, [], []

        emoji_ids = self.extract_custom_emoji_ids(text)
        if not emoji_ids:
            return text, [], []

        metadata_map = await self.cache.get_metadata(client, account_id, emoji_ids)
        transformed, entities, missing = self._build_message_with_entities(text, metadata_map)

        if missing:
            missing_text = ", ".join(map(str, missing))
            self.logger.warning(f"Missing custom emoji metadata for IDs: {missing_text}")

        return transformed, entities, missing

    # Validation ----------------------------------------------------------------

    def validate_custom_emoji_ids(self, emoji_ids: Iterable[int]) -> CustomEmojiValidationResult:
        """Ensure the provided emoji IDs exist on at least one configured account."""

        ids = list(dict.fromkeys(int(eid) for eid in emoji_ids))
        if not ids:
            return CustomEmojiValidationResult(set(), set(), {}, [])

        accounts = self._get_active_accounts()
        if not accounts:
            return CustomEmojiValidationResult(set(), set(ids), {}, [])

        return self._run_async(self._validate_async(ids, accounts))

    async def _validate_async(
        self,
        emoji_ids: Sequence[int],
        accounts: Sequence[Account],
    ) -> CustomEmojiValidationResult:
        matches: Dict[int, Set[int]] = {}
        checked_accounts: List[int] = []

        for account in accounts:
            if account.id is None:
                continue
            context = self.account_client_provider(account)
            async with context as client_wrapper:
                if not client_wrapper:
                    continue

                checked_accounts.append(account.id)
                metadata = await self.cache.get_metadata(
                    client_wrapper.client,
                    account.id,
                    emoji_ids,
                )

                for emoji_id in metadata.keys():
                    matches.setdefault(emoji_id, set()).add(account.id)

        missing = set(emoji_ids) - set(matches.keys())
        return CustomEmojiValidationResult(
            valid_ids=set(matches.keys()),
            missing_ids=missing,
            account_matches=matches,
            accounts_checked=checked_accounts,
        )

    # Internal helpers ---------------------------------------------------------

    def _build_message_with_entities(
        self,
        text: str,
        metadata_map: Dict[int, CustomEmojiMetadata],
    ) -> Tuple[str, List[types.MessageEntityCustomEmoji], List[int]]:
        segments: List[str] = []
        placeholders: List[int] = []
        cursor = 0

        for match in CUSTOM_EMOJI_PATTERN.finditer(text):
            start, end = match.span()
            segments.append(text[cursor:start])
            placeholders.append(int(match.group("emoji_id")))
            cursor = end
        segments.append(text[cursor:])

        if not placeholders:
            return text, [], []

        transformed_parts: List[str] = []
        entities: List[types.MessageEntityCustomEmoji] = []
        missing: List[int] = []
        current_text = ""

        for index, segment in enumerate(segments):
            transformed_parts.append(segment)
            current_text += segment

            if index >= len(placeholders):
                continue

            emoji_id = placeholders[index]
            metadata = metadata_map.get(emoji_id)

            if metadata:
                offset = len(add_surrogate(current_text))
                transformed_parts.append(CUSTOM_EMOJI_PLACEHOLDER)
                current_text += CUSTOM_EMOJI_PLACEHOLDER
                length = len(add_surrogate(CUSTOM_EMOJI_PLACEHOLDER))
                entities.append(
                    types.MessageEntityCustomEmoji(
                        offset=offset,
                        length=length,
                        document_id=metadata.document_id,
                    )
                )
            else:
                fallback = self._build_fallback_text(emoji_id, metadata)
                transformed_parts.append(fallback)
                current_text += fallback
                missing.append(emoji_id)

        return "".join(transformed_parts), entities, missing

    @staticmethod
    def _build_fallback_text(emoji_id: int, metadata: Optional[CustomEmojiMetadata]) -> str:
        alt_text = metadata.alt if metadata and metadata.alt else None
        if alt_text:
            return alt_text
        return f":emoji-{emoji_id}:"

    def _get_active_accounts(self) -> List[Account]:
        session = get_session()
        try:
            statement = select(Account).where(Account.is_deleted == False, Account.is_active == True)
            accounts = session.exec(statement).all()
            return accounts
        finally:
            session.close()

    def _run_async(
        self,
        coro: Coroutine[Any, Any, CustomEmojiValidationResult],
    ) -> CustomEmojiValidationResult:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():  # pragma: no cover - GUI environments
            return asyncio.run_coroutine_threadsafe(coro, loop).result()

        return asyncio.run(coro)

    def _default_account_client_provider(self, account: Account):
        service_logger = self.logger

        class _WrapperContext:
            def __init__(self, acc: Account):
                from .telethon_client import TelegramClientWrapper  # Local import to avoid cycles

                self.account = acc
                self.wrapper = TelegramClientWrapper(acc)

            async def __aenter__(self):
                if not await self.wrapper.connect():
                    service_logger.warning(
                        "Unable to connect account %s for custom emoji validation", self.account.id
                    )
                    return None

                status = self.wrapper.get_status()
                if not status.get("authorized"):
                    service_logger.warning(
                        "Account %s is not authorized; skipping custom emoji validation", self.account.id
                    )
                    return None

                return self.wrapper

            async def __aexit__(self, exc_type, exc, tb):
                await self.wrapper.disconnect()

        return _WrapperContext(account)


# Singleton helper ---------------------------------------------------------------


_SERVICE_INSTANCE: Optional[CustomEmojiService] = None


def get_custom_emoji_service() -> CustomEmojiService:
    """Return a module-level singleton of :class:`CustomEmojiService`."""

    global _SERVICE_INSTANCE
    if _SERVICE_INSTANCE is None:
        _SERVICE_INSTANCE = CustomEmojiService()
    return _SERVICE_INSTANCE


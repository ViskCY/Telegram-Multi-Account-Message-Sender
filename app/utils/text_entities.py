"""Utility helpers for converting template span metadata into Telethon entities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from telethon.tl import types as tl_types


@dataclass
class RenderedMessage:
    """Container representing a composed message and its entities."""

    text: str
    entities: List[tl_types.TypeMessageEntity]

    def __str__(self) -> str:  # pragma: no cover - convenience for legacy usage
        return self.text


START_MARKER_TEMPLATE = "\uFFF0{index}\uFFF1"
END_MARKER_TEMPLATE = "\uFFF2{index}\uFFF3"


def parse_span_metadata(raw_metadata: Optional[Any]) -> List[Dict[str, Any]]:
    """Normalise span metadata stored as JSON or python objects."""

    if raw_metadata is None:
        return []

    if isinstance(raw_metadata, list):
        return [item for item in raw_metadata if isinstance(item, dict)]

    if isinstance(raw_metadata, str):
        try:
            data = json.loads(raw_metadata)
        except (TypeError, json.JSONDecodeError):
            return []
        return parse_span_metadata(data)

    return []


def mark_text_spans(text: str, spans: Sequence[Dict[str, Any]]) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """Wrap span segments with sentinel markers so offsets can be recovered later."""

    if not spans:
        return text, {}

    marked_text = text
    markers: Dict[str, Dict[str, Any]] = {}

    for index, span in enumerate(spans):
        placeholder = span.get("text") or span.get("placeholder")
        start_marker = START_MARKER_TEMPLATE.format(index=index)
        end_marker = END_MARKER_TEMPLATE.format(index=index)

        if placeholder and placeholder in marked_text:
            marked_text = marked_text.replace(
                placeholder, f"{start_marker}{placeholder}{end_marker}", 1
            )
            markers[start_marker] = {"end": end_marker, "span": span}
            continue

        # Fall back to offset/length based replacement if explicit text is unavailable.
        offset = span.get("offset") or span.get("start")
        length = span.get("length")
        if length is None and span.get("end") is not None and offset is not None:
            length = int(span["end"]) - int(offset)

        if offset is None or length in (None, 0):
            continue

        offset = int(offset)
        length = int(length)
        segment = marked_text[offset : offset + length]
        marked_text = (
            marked_text[:offset]
            + f"{start_marker}{segment}{end_marker}"
            + marked_text[offset + length :]
        )
        markers[start_marker] = {"end": end_marker, "span": span}

    return marked_text, markers


def resolve_marked_spans(text: str, markers: Dict[str, Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """Remove sentinel markers and yield spans with concrete offsets and lengths."""

    if not markers:
        return text, []

    resolved_spans: List[Dict[str, Any]] = []
    cleaned_text_chars: List[str] = []
    i = 0

    # Pre-compute lookup of end markers for quick access.
    marker_lookup = {start: data["end"] for start, data in markers.items()}

    while i < len(text):
        matched_marker = False
        for start_marker, end_marker in marker_lookup.items():
            if text.startswith(start_marker, i):
                matched_marker = True
                marker_data = markers[start_marker]
                span = marker_data["span"].copy()
                i += len(start_marker)
                start_offset = len(cleaned_text_chars)
                content_chars: List[str] = []

                while i < len(text) and not text.startswith(end_marker, i):
                    character = text[i]
                    content_chars.append(character)
                    cleaned_text_chars.append(character)
                    i += 1

                content = "".join(content_chars)
                length = len(content)
                if i < len(text) and text.startswith(end_marker, i):
                    i += len(end_marker)

                # Update span offset and length to reflect the processed content.
                span["offset"] = start_offset
                span["length"] = length
                resolved_spans.append(span)
                break

        if not matched_marker:
            cleaned_text_chars.append(text[i])
            i += 1

    return "".join(cleaned_text_chars), resolved_spans


def _utf16_length(text: str) -> int:
    """Return the length of a string in UTF-16 code units."""

    return len(text.encode("utf-16-le")) // 2


def _utf16_position(text: str, position: int) -> int:
    """Convert a code-point based index to UTF-16 offset."""

    return _utf16_length(text[:position])


def build_telethon_entities(
    text: str, spans: Iterable[Dict[str, Any]]
) -> List[tl_types.TypeMessageEntity]:
    """Create Telethon message entities from resolved span metadata."""

    entities: List[tl_types.TypeMessageEntity] = []

    for span in spans:
        start = span.get("offset")
        length = span.get("length")
        entity_type = (span.get("type") or "").lower()

        if start is None or length in (None, 0) or not entity_type:
            continue

        start = int(start)
        length = int(length)
        utf16_offset = _utf16_position(text, start)
        utf16_length = _utf16_length(text[start : start + length])

        data: Dict[str, Any] = span.get("data") or {}
        url = span.get("url") or data.get("url")
        language = span.get("language") or data.get("language") or ""
        user_id = span.get("user_id") or data.get("user_id")
        custom_emoji_id = span.get("custom_emoji_id") or data.get("custom_emoji_id")

        entity: Optional[tl_types.TypeMessageEntity] = None

        if entity_type in {"bold"}:
            entity = tl_types.MessageEntityBold(utf16_offset, utf16_length)
        elif entity_type in {"italic"}:
            entity = tl_types.MessageEntityItalic(utf16_offset, utf16_length)
        elif entity_type in {"underline"}:
            entity = tl_types.MessageEntityUnderline(utf16_offset, utf16_length)
        elif entity_type in {"strikethrough", "strike"}:
            entity = tl_types.MessageEntityStrike(utf16_offset, utf16_length)
        elif entity_type in {"code", "monospace"}:
            entity = tl_types.MessageEntityCode(utf16_offset, utf16_length)
        elif entity_type == "pre":
            entity = tl_types.MessageEntityPre(utf16_offset, utf16_length, language=language)
        elif entity_type in {"spoiler"}:
            entity = tl_types.MessageEntitySpoiler(utf16_offset, utf16_length)
        elif entity_type in {"text_url", "text-url", "link"} and url:
            entity = tl_types.MessageEntityTextUrl(utf16_offset, utf16_length, url=url)
        elif entity_type == "url":
            entity = tl_types.MessageEntityUrl(utf16_offset, utf16_length)
        elif entity_type == "email":
            entity = tl_types.MessageEntityEmail(utf16_offset, utf16_length)
        elif entity_type == "phone":
            entity = tl_types.MessageEntityPhone(utf16_offset, utf16_length)
        elif entity_type == "hashtag":
            entity = tl_types.MessageEntityHashtag(utf16_offset, utf16_length)
        elif entity_type == "cashtag":
            entity = tl_types.MessageEntityCashtag(utf16_offset, utf16_length)
        elif entity_type in {"bot_command", "command"}:
            entity = tl_types.MessageEntityBotCommand(utf16_offset, utf16_length)
        elif entity_type in {"mention_name", "mention-name"} and user_id is not None:
            entity = tl_types.MessageEntityMentionName(utf16_offset, utf16_length, int(user_id))
        elif entity_type == "custom_emoji" and custom_emoji_id is not None:
            entity = tl_types.MessageEntityCustomEmoji(
                utf16_offset, utf16_length, int(custom_emoji_id)
            )

        if entity is not None:
            entities.append(entity)

    return sorted(entities, key=lambda item: item.offset)


def compose_rich_text(
    text: str, spans: Sequence[Dict[str, Any]]
) -> RenderedMessage:
    """Return text and Telethon entities generated from span metadata."""

    marked_text, markers = mark_text_spans(text, spans)
    cleaned_text, resolved_spans = resolve_marked_spans(marked_text, markers)
    entities = build_telethon_entities(cleaned_text, resolved_spans)
    return RenderedMessage(cleaned_text, entities)


def compose_personalized_rich_text(
    text: str,
    spans: Sequence[Dict[str, Any]],
    replacements: Optional[Dict[str, str]] = None,
    spintax_processor: Optional[Any] = None,
    use_spintax: bool = False,
) -> RenderedMessage:
    """Compose text applying replacements and spintax while preserving spans."""

    marked_text, markers = mark_text_spans(text, spans)

    processed_text = marked_text
    if use_spintax and spintax_processor is not None:
        processed_text = spintax_processor.process(processed_text).text

    if replacements:
        for placeholder, value in replacements.items():
            processed_text = processed_text.replace(placeholder, value)

    cleaned_text, resolved_spans = resolve_marked_spans(processed_text, markers)
    entities = build_telethon_entities(cleaned_text, resolved_spans)
    return RenderedMessage(cleaned_text, entities)


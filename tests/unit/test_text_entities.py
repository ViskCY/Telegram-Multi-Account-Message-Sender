"""Unit tests for template span to Telethon entity conversion."""

from telethon.tl import types as tl_types

from app.core.spintax import SpintaxProcessor
from app.utils.text_entities import compose_personalized_rich_text


def test_compose_personalized_rich_text_creates_entities():
    """Ensure spans are converted into Telethon entities with UTF-16 offsets."""

    text = "Hello {name}, visit our site"
    spans = [
        {"type": "bold", "text": "Hello {name}"},
        {"type": "text_url", "text": "our site", "url": "https://example.com"},
    ]

    replacements = {"{name}": "Alice"}
    rendered = compose_personalized_rich_text(text, spans, replacements=replacements)

    assert rendered.text == "Hello Alice, visit our site"
    assert len(rendered.entities) == 2

    bold_entity = rendered.entities[0]
    link_entity = rendered.entities[1]

    assert isinstance(bold_entity, tl_types.MessageEntityBold)
    assert isinstance(link_entity, tl_types.MessageEntityTextUrl)
    assert rendered.text[bold_entity.offset : bold_entity.offset + bold_entity.length] == "Hello Alice"
    assert link_entity.url == "https://example.com"


def test_spintax_processing_preserves_markers():
    """Spintax replacement should not disturb span placeholders."""

    processor = SpintaxProcessor(seed=1)
    text = "{greeting|Hi} {name}!"
    spans = [{"type": "italic", "text": "{greeting|Hi} {name}"}]

    replacements = {"{name}": "Bob"}
    rendered = compose_personalized_rich_text(
        text,
        spans,
        replacements=replacements,
        spintax_processor=processor,
        use_spintax=True,
    )

    assert "Bob" in rendered.text
    assert any(isinstance(entity, tl_types.MessageEntityItalic) for entity in rendered.entities)

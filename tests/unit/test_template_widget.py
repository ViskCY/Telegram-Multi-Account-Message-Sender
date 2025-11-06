"""Tests for template widget interactions with custom emojis."""

import importlib
import sys
import types

import pytest
from PyQt5.QtWidgets import QApplication


class FakeMessageTemplate:
    """Lightweight stand-in for the SQLModel-backed message template."""

    def __init__(
        self,
        name: str,
        description: str | None = None,
        body: str = "",
        use_spintax: bool = False,
        spintax_text: str | None = None,
    ) -> None:
        self.id: int | None = None
        self.name = name
        self.description = description
        self.body = body
        self.entity_spans = None
        self.use_spintax = use_spintax
        self.spintax_text = spintax_text
        self.tags = None
        self._tags_list: list[str] = []

    def get_tags_list(self) -> list[str]:
        return list(self._tags_list)

    def set_tags_list(self, tags: list[str]) -> None:
        self._tags_list = list(tags)
        self.tags = tags if tags else None


class FakeAccount:
    """Placeholder account model required by the widget module."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _install_model_stubs() -> None:
    """Provide stub modules so the widget can be imported without SQLModel."""

    models_module = types.ModuleType("app.models")
    models_module.MessageTemplate = FakeMessageTemplate
    sys.modules.setdefault("app.models", models_module)

    account_module = types.ModuleType("app.models.account")
    account_module.Account = FakeAccount
    sys.modules.setdefault("app.models.account", account_module)


_install_model_stubs()
template_widget = importlib.import_module("app.gui.widgets.template_widget")
TemplateDialog = template_widget.TemplateDialog


class DummyValidationResult:
    """Simple namespace representing a successful validation response."""

    def __init__(self):
        self.accounts_checked = [1]
        self.missing_ids = set()
        self.valid_ids = {123}
        self.account_matches = {123: {1}}


class DummyEmojiService:
    """Stub custom emoji service used to capture message bodies."""

    def __init__(self):
        self.extracted_messages: list[str] = []

    def extract_custom_emoji_ids(self, message: str):
        self.extracted_messages.append(message)
        return [123] if "[emoji:123]" in message else []

    def validate_custom_emoji_ids(self, emoji_ids):
        assert emoji_ids == [123]
        return DummyValidationResult()


class DummySession:
    """Minimal session stub preventing database interactions."""

    def __init__(self):
        self.added = []

    def add(self, template):
        self.added.append(template)
        template.id = template.id or 1

    def merge(self, template):
        self.added.append(template)

    def commit(self):
        return None

    def close(self):
        return None


@pytest.fixture(scope="module")
def qt_app():
    """Ensure a QApplication instance exists for widget tests."""

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_save_template_with_custom_emoji_does_not_raise(qt_app, monkeypatch):
    """Saving a template containing custom emoji markup should not raise."""

    emoji_service = DummyEmojiService()
    monkeypatch.setattr(
        template_widget, "get_custom_emoji_service", lambda: emoji_service
    )
    monkeypatch.setattr(template_widget, "get_session", lambda: DummySession())

    dialog = TemplateDialog()
    dialog.name_edit.setText("Emoji Template")
    dialog.message_editor.set_plain_text("[emoji:123] Hello")

    dialog.save_template()

    assert dialog.template is not None
    assert dialog.template.body == "[emoji:123] Hello"
    assert emoji_service.extracted_messages == ["[emoji:123] Hello"]

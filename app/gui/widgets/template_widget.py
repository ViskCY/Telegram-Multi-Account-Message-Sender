"""
Template management widgets.
"""

import asyncio
import base64
from typing import Optional, List, Dict, Any, Callable
from uuid import uuid4

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QComboBox, QCheckBox, QSpinBox,
    QMessageBox, QDialog, QDialogButtonBox, QFormLayout,
    QTextEdit, QFileDialog, QAbstractItemView, QToolButton,
    QScrollArea, QApplication
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QUrl, QSize
from PyQt5.QtGui import (
    QFont, QIcon, QColor, QImage, QPixmap, QTextCursor,
    QTextDocument, QTextCharFormat, QTextImageFormat, QTextFormat
)

from ...models import MessageTemplate
from ...models.account import Account
from ...services import get_logger
from ...services.db import get_session
from ...services.translation import _, get_translation_manager
from ...core import SpintaxProcessor, get_custom_emoji_service
from telethon import TelegramClient, functions, types
from sqlmodel import select


CUSTOM_EMOJI_ENTITY_TYPE = "custom_emoji"


class CustomEmojiPickerDialog(QDialog):
    """Dialog that displays available custom emojis for insertion."""

    def __init__(self, parent: Optional[QWidget], emojis: List[Dict[str, Any]]):
        super().__init__(parent)
        self.setWindowTitle("Select Custom Emoji")
        self.setModal(True)
        self.selected_emoji: Optional[Dict[str, Any]] = None

        layout = QVBoxLayout(self)
        description = QLabel(
            "Click a custom emoji to insert it into your template."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(8)
        scroll.setWidget(container)

        columns = 6
        for index, emoji in enumerate(emojis):
            button = QToolButton()
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setAutoRaise(True)
            button.setIconSize(QSize(48, 48))
            pixmap = self._build_pixmap(emoji)
            if pixmap is not None:
                button.setIcon(QIcon(pixmap))
            else:
                button.setText(emoji.get("shortcode") or emoji.get("emoji", "✨"))
            button.setToolTip(emoji.get("shortcode") or str(emoji.get("custom_emoji_id")))
            button.clicked.connect(lambda checked=False, data=emoji: self._select_emoji(data))
            grid.addWidget(button, index // columns, index % columns)

        layout.addWidget(scroll)

        footer = QDialogButtonBox(QDialogButtonBox.Cancel)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    @staticmethod
    def _build_pixmap(emoji: Dict[str, Any]) -> Optional[QPixmap]:
        """Build a pixmap from emoji data if possible."""
        image_data = emoji.get("image_data")
        if not image_data:
            return None
        try:
            raw = base64.b64decode(image_data)
        except (ValueError, TypeError):
            return None

        pixmap = QPixmap()
        if pixmap.loadFromData(raw):
            return pixmap
        return None

    def _select_emoji(self, emoji: Dict[str, Any]) -> None:
        self.selected_emoji = emoji
        self.accept()


class TelethonEntityEditor(QWidget):
    """Rich text editor that tracks Telethon entities."""

    ENTITY_TYPE_PROPERTY = QTextFormat.UserProperty + 201
    ENTITY_ID_PROPERTY = QTextFormat.UserProperty + 202
    ENTITY_META_PROPERTY = QTextFormat.UserProperty + 203

    textChanged = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._custom_emoji_handler: Optional[Callable[[], Optional[Dict[str, Any]]]] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(6)

        self.custom_emoji_button = QToolButton()
        self.custom_emoji_button.setText("Insert Custom Emoji")
        self.custom_emoji_button.setIcon(QIcon.fromTheme("face-smile"))
        self.custom_emoji_button.clicked.connect(self._handle_custom_emoji_click)
        toolbar_layout.addWidget(self.custom_emoji_button)
        toolbar_layout.addStretch()

        layout.addLayout(toolbar_layout)

        self.text_edit = QTextEdit()
        self.text_edit.setAcceptRichText(True)
        self.text_edit.textChanged.connect(self.textChanged)
        layout.addWidget(self.text_edit)

    def set_custom_emoji_handler(self, handler: Callable[[], Optional[Dict[str, Any]]]) -> None:
        """Set a callback that returns emoji data for insertion."""
        self._custom_emoji_handler = handler

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - Qt API
        self.text_edit.setPlaceholderText(text)

    def setMinimumHeight(self, height: int) -> None:  # noqa: N802 - Qt API
        self.text_edit.setMinimumHeight(height)

    def to_plain_text(self) -> str:
        return self.text_edit.toPlainText()

    def set_plain_text(self, text: str) -> None:
        self.text_edit.setPlainText(text)

    def set_content(self, text: str, spans: Optional[List[Dict[str, Any]]] = None) -> None:
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(text or "")
        self.text_edit.blockSignals(False)
        if spans:
            self.apply_entity_spans(spans)

    def apply_entity_spans(self, spans: List[Dict[str, Any]]) -> None:
        if not spans:
            return

        for span in sorted(spans, key=lambda item: item.get("start", 0)):
            if span.get("type") != CUSTOM_EMOJI_ENTITY_TYPE:
                continue

            position = span.get("start", 0)
            emoji_meta = {
                "custom_emoji_id": span.get("custom_emoji_id"),
                "shortcode": span.get("shortcode"),
                "emoji": span.get("emoji"),
                "cdn_url": span.get("cdn_url"),
                "image_data": span.get("image_data"),
                "is_animated": span.get("is_animated", False),
            }
            self.insert_custom_emoji(emoji_meta, position)

    def insert_custom_emoji(self, emoji: Dict[str, Any], position: Optional[int] = None) -> None:
        image = QImage()
        image_data = emoji.get("image_data")
        if image_data:
            try:
                raw = base64.b64decode(image_data)
                image.loadFromData(raw)
            except (ValueError, TypeError):
                image = QImage()

        cursor = QTextCursor(self.text_edit.document())
        if position is not None:
            plain_length = len(self.text_edit.toPlainText())
            pos = max(0, min(position, plain_length))
            cursor.setPosition(pos)
            if pos < plain_length:
                cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 1)
        else:
            cursor = self.text_edit.textCursor()

        if image.isNull():
            shortcode = emoji.get("shortcode") or emoji.get("emoji") or "[emoji]"
            cursor.insertText(shortcode)
            return

        resource_name = f"custom-emoji://{emoji.get('custom_emoji_id')}-{uuid4()}"
        document: QTextDocument = self.text_edit.document()
        document.addResource(QTextDocument.ImageResource, QUrl(resource_name), image)

        image_format = QTextImageFormat()
        image_format.setName(resource_name)
        target_size = 32
        if image.width() > 0 and image.height() > 0:
            scale = target_size / max(image.width(), image.height())
            image_format.setWidth(image.width() * scale)
            image_format.setHeight(image.height() * scale)
        else:
            image_format.setWidth(target_size)
            image_format.setHeight(target_size)

        image_format.setProperty(self.ENTITY_TYPE_PROPERTY, CUSTOM_EMOJI_ENTITY_TYPE)
        image_format.setProperty(self.ENTITY_ID_PROPERTY, emoji.get("custom_emoji_id"))
        serializable_meta = {
            "custom_emoji_id": emoji.get("custom_emoji_id"),
            "shortcode": emoji.get("shortcode"),
            "emoji": emoji.get("emoji"),
            "cdn_url": emoji.get("cdn_url"),
            "image_data": emoji.get("image_data"),
            "is_animated": emoji.get("is_animated", False),
        }
        image_format.setProperty(self.ENTITY_META_PROPERTY, serializable_meta)

        cursor.insertImage(image_format)

    def get_entity_spans(self) -> List[Dict[str, Any]]:
        spans: List[Dict[str, Any]] = []
        document = self.text_edit.document()
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.Start)

        while True:
            cursor = document.find("\uFFFC", cursor)
            if cursor.isNull():
                break

            start = cursor.selectionStart()
            char_format: QTextCharFormat = cursor.charFormat()
            if not char_format.isImageFormat():
                continue

            if char_format.property(self.ENTITY_TYPE_PROPERTY) != CUSTOM_EMOJI_ENTITY_TYPE:
                continue

            meta = char_format.property(self.ENTITY_META_PROPERTY) or {}
            spans.append({
                "start": start,
                "end": start + 1,
                "type": CUSTOM_EMOJI_ENTITY_TYPE,
                "custom_emoji_id": char_format.property(self.ENTITY_ID_PROPERTY),
                "shortcode": meta.get("shortcode"),
                "emoji": meta.get("emoji"),
                "cdn_url": meta.get("cdn_url"),
                "image_data": meta.get("image_data"),
                "is_animated": meta.get("is_animated", False),
            })

        return spans

    def _handle_custom_emoji_click(self) -> None:
        if not self._custom_emoji_handler:
            QMessageBox.information(
                self,
                "Custom Emojis",
                "No custom emoji provider is configured."
            )
            return

        emoji = self._custom_emoji_handler()
        if emoji:
            self.insert_custom_emoji(emoji)

class TemplateDialog(QDialog):
    """Dialog for creating/editing templates."""
    
    template_saved = pyqtSignal(int)
    
    def __init__(self, parent=None, template: Optional[MessageTemplate] = None):
        super().__init__(parent)
        self.template = template
        self.logger = get_logger()
        self.spintax_processor = SpintaxProcessor()
        self._emoji_cache: Dict[int, List[Dict[str, Any]]] = {}
        self._account_lookup: Dict[int, Account] = {}
        self.custom_emoji_service = get_custom_emoji_service()
        self.setup_ui()

        if template:
            self.load_template_data()
    
    def setup_ui(self):
        """Set up the dialog UI."""
        self.setWindowTitle(_("templates.add_template") if not self.template else _("templates.edit_template"))
        self.setModal(True)
        self.resize(600, 500)
        
        # Enable help button
        self.setWindowFlags(self.windowFlags() | Qt.WindowContextHelpButtonHint)
        
        layout = QVBoxLayout(self)
        
        # Basic Information
        basic_group = QGroupBox(_("templates.basic_information"))
        basic_layout = QFormLayout(basic_group)
        
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("templates.template_name_placeholder"))
        basic_layout.addRow(_("common.name") + ":", self.name_edit)

        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText(_("templates.template_description_placeholder"))
        basic_layout.addRow(_("common.description") + ":", self.description_edit)

        self.account_combo = QComboBox()
        self.account_combo.setEditable(False)
        basic_layout.addRow("Authoring account:", self.account_combo)

        layout.addWidget(basic_group)

        # Message Content
        message_group = QGroupBox(_("templates.message_content"))
        message_layout = QVBoxLayout(message_group)

        # Message text
        message_layout.addWidget(QLabel(_("common.message_text") + ":"))
        self.message_editor = TelethonEntityEditor(self)
        self.message_editor.setPlaceholderText(_("templates.message_template_placeholder"))
        self.message_editor.setMinimumHeight(150)
        self.message_editor.set_custom_emoji_handler(self.open_custom_emoji_picker)
        message_layout.addWidget(self.message_editor)

        # Variables help
        variables_help = QLabel(_("templates.available_variables"))
        variables_help.setStyleSheet("color: #888888; font-style: italic;")
        message_layout.addWidget(variables_help)

        # Variables vs Spintax explanation
        explanation = QLabel(_("templates.variables_explanation") +
                              "\n\nCustom emojis are stored with their Telegram ID. "
                              "When used inside spintax blocks, ensure each variation keeps a "
                              "compatible emoji placeholder so entity mappings remain valid.")
        explanation.setStyleSheet("color: #4CAF50; font-weight: bold; padding: 8px; background-color: #1a1a1a; border: 1px solid #4CAF50; border-radius: 4px;")
        explanation.setWordWrap(True)
        message_layout.addWidget(explanation)

        custom_emoji_tip = QLabel(
            "Custom emojis preview inline using cached media. If a cached preview is missing, "
            "the emoji will still send using its custom_emoji_id."
        )
        custom_emoji_tip.setWordWrap(True)
        custom_emoji_tip.setStyleSheet("color: #FFA726; font-size: 11px;")
        message_layout.addWidget(custom_emoji_tip)

        layout.addWidget(message_group)
        
        # Spintax Settings
        spintax_group = QGroupBox(_("templates.spintax_settings"))
        spintax_layout = QFormLayout(spintax_group)
        
        self.use_spintax_check = QCheckBox(_("templates.enable_spintax"))
        self.use_spintax_check.toggled.connect(self.toggle_spintax_settings)
        spintax_layout.addRow(self.use_spintax_check)
        
        self.spintax_example_edit = QLineEdit()
        self.spintax_example_edit.setPlaceholderText(_("templates.spintax_example_placeholder"))
        spintax_layout.addRow(_("templates.spintax_example") + ":", self.spintax_example_edit)
        
        # Spintax preview button
        self.preview_spintax_button = QPushButton(_("templates.preview_spintax"))
        self.preview_spintax_button.clicked.connect(self.preview_spintax)
        self.preview_spintax_button.setEnabled(False)
        spintax_layout.addRow("", self.preview_spintax_button)
        
        layout.addWidget(spintax_group)
        
        # Tags
        tags_group = QGroupBox("Tags")
        tags_layout = QFormLayout(tags_group)
        
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("welcome, onboarding, marketing (comma-separated)")
        tags_layout.addRow("Tags:", self.tags_edit)
        
        layout.addWidget(tags_group)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.save_template)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.populate_authoring_accounts()

        # Initialize spintax settings as disabled
        self.toggle_spintax_settings(False)

    def event(self, event):
        """Handle events including help button clicks."""
        if event.type() == event.EnterWhatsThisMode:
            self.show_help()
            return True
        return super().event(event)

    def populate_authoring_accounts(self) -> None:
        """Populate the account selector used for emoji retrieval."""
        self.account_combo.clear()
        self._account_lookup.clear()

        placeholder = _("testing.select_account")
        self.account_combo.addItem(placeholder, None)

        session = get_session()
        try:
            accounts = session.exec(select(Account).where(Account.is_deleted == False)).all()  # noqa: E712
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.error(f"Failed to load accounts: {exc}")
            self.account_combo.clear()
            self.account_combo.addItem("No connected accounts", None)
            self.account_combo.setEnabled(False)
            return
        finally:
            session.close()

        if not accounts:
            self.account_combo.clear()
            self.account_combo.addItem("No connected accounts", None)
            self.account_combo.setEnabled(False)
            return

        self.account_combo.setEnabled(True)
        for account in accounts:
            self._account_lookup[account.id] = account
            display_name = f"{account.name}{' ⭐' if account.is_premium else ''}"
            self.account_combo.addItem(display_name, account.id)

        if self.account_combo.count() > 1:
            self.account_combo.setCurrentIndex(1)

    def get_selected_account(self) -> Optional[Account]:
        """Return the currently selected account object."""
        account_id = self.account_combo.currentData()
        if not account_id:
            return None

        account = self._account_lookup.get(account_id)
        if account:
            return account

        session = get_session()
        try:
            account = session.exec(select(Account).where(Account.id == account_id)).first()
            if account:
                self._account_lookup[account_id] = account
            return account
        finally:
            session.close()

    def _run_async(self, coroutine_factory: Callable[[], Any]) -> Any:
        """Execute an async coroutine from the GUI thread."""
        try:
            return asyncio.run(coroutine_factory())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(coroutine_factory())
            finally:
                asyncio.set_event_loop(None)
                loop.close()

    def open_custom_emoji_picker(self) -> Optional[Dict[str, Any]]:
        """Fetch custom emojis for the selected account and show the picker."""
        account = self.get_selected_account()
        if not account:
            QMessageBox.warning(
                self,
                "Custom Emojis",
                "Select an authoring account to load custom emojis."
            )
            return None

        emojis = self._emoji_cache.get(account.id)
        if emojis is None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                emojis = self._run_async(lambda: self._fetch_custom_emojis_async(account))
                self._emoji_cache[account.id] = emojis or []
            except Exception as exc:  # pragma: no cover - UI feedback path
                self.logger.error(f"Failed to fetch custom emojis: {exc}")
                QMessageBox.critical(
                    self,
                    "Custom Emojis",
                    f"Unable to load custom emojis for {account.name}: {exc}"
                )
                emojis = []
            finally:
                QApplication.restoreOverrideCursor()

        if not emojis:
            QMessageBox.information(
                self,
                "Custom Emojis",
                "No custom emojis available for this account yet."
            )
            return None

        dialog = CustomEmojiPickerDialog(self, emojis)
        if dialog.exec_() == QDialog.Accepted and dialog.selected_emoji:
            return dialog.selected_emoji
        return None

    async def _fetch_custom_emojis_async(self, account: Account) -> List[Dict[str, Any]]:
        """Fetch custom emojis for an account via Telethon."""
        client = TelegramClient(account.session_path, account.api_id, account.api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("Account session is not authorized. Please sign in before fetching emojis.")

            groups = await client(functions.messages.GetEmojiStickerGroupsRequest(hash=0))
            emoticons: List[str] = []
            if isinstance(groups, types.messages.EmojiGroups):
                for group in groups.groups:
                    if getattr(group, "emoticons", None):
                        emoticons.extend(group.emoticons)

            doc_ids: List[int] = []
            emoji_map: Dict[int, str] = {}
            seen_ids = set()
            max_results = 200

            for emoticon in emoticons:
                if len(doc_ids) >= max_results:
                    break
                try:
                    results = await client(functions.messages.SearchCustomEmojiRequest(emoticon=emoticon, hash=0))
                except Exception:
                    continue

                if isinstance(results, types.EmojiList):
                    for doc_id in results.document_id:
                        if doc_id in seen_ids:
                            continue
                        seen_ids.add(doc_id)
                        doc_ids.append(doc_id)
                        emoji_map[int(doc_id)] = emoticon
                        if len(doc_ids) >= max_results:
                            break

            if not doc_ids:
                return []

            documents = await client(functions.messages.GetCustomEmojiDocumentsRequest(document_id=doc_ids))
            emojis: List[Dict[str, Any]] = []

            for document in documents:
                image_bytes = None
                thumbs = getattr(document, "thumbs", None)
                if thumbs:
                    for thumb in thumbs:
                        if getattr(thumb, "bytes", None):
                            image_bytes = thumb.bytes
                            break

                if image_bytes is None:
                    try:
                        image_bytes = await client.download_media(document, bytes)
                    except Exception:
                        image_bytes = None

                base64_image = None
                if image_bytes:
                    try:
                        base64_image = base64.b64encode(image_bytes).decode("ascii")
                    except Exception:
                        base64_image = None

                custom_emoji_id = int(getattr(document, "id", 0))
                mime_type = getattr(document, "mime_type", "") or ""
                is_animated = mime_type in {"application/x-tgsticker", "video/webm"}

                emojis.append({
                    "custom_emoji_id": custom_emoji_id,
                    "emoji": emoji_map.get(custom_emoji_id),
                    "shortcode": emoji_map.get(custom_emoji_id),
                    "cdn_url": f"https://t.me/i/emoji/{custom_emoji_id}.webp",
                    "image_data": base64_image,
                    "is_animated": is_animated,
                })

            return emojis
        finally:
            await client.disconnect()

    def show_help(self):
        """Show help dialog."""
        help_text = """
        <h3>Template Creation Help</h3>
        
        <h4>Basic Information:</h4>
        <ul>
        <li><b>Name:</b> A unique identifier for your template</li>
        <li><b>Description:</b> Brief description of the template's purpose</li>
        </ul>
        
        <h4>Message Content:</h4>
        <ul>
        <li><b>Message Text:</b> Your main message template</li>
        <li><b>Variables:</b> Use {name}, {email}, {phone}, {company}, {date}, {time} for personalization</li>
        <li><b>Custom Emojis:</b> Use the "Insert Custom Emoji" toolbar button after choosing an authoring account. Each emoji keeps its Telegram custom_emoji_id and behaves like a single character inside spintax.</li>
        </ul>

        <h4>⚠️ IMPORTANT: Variables vs Spintax</h4>
        <p><b>VARIABLES</b> (for personalization - what you probably want):</p>
        <ul>
        <li>{name}, {email}, {company} - Replaced with actual values</li>
        <li>Example: "Hello {name}!" becomes "Hello John!"</li>
        </ul>
        
        <p><b>SPINTAX PATTERNS</b> (for variations - random text selection):</p>
        <ul>
        <li>{option1|option2|option3} - Creates random variations</li>
        <li>Example: "Hello {friend|buddy|pal}!" becomes "Hello friend!" or "Hello buddy!"</li>
        </ul>
        
        <h4>Spintax Settings:</h4>
        <ul>
        <li><b>Enable Spintax:</b> Check to enable message variations</li>
        <li><b>Spintax Example:</b> Use {option1|option2|option3} syntax for variations</li>
        <li><b>Example:</b> Hello {friend|buddy|pal}, welcome to {our company|our service}!</li>
        <li><b>Custom Emojis &amp; Spintax:</b> Keep emoji placeholders consistent across variations so each option keeps a valid custom_emoji_id.</li>
        </ul>
        
        <h4>Tags:</h4>
        <ul>
        <li>Comma-separated keywords for organizing templates</li>
        <li>Example: welcome, onboarding, marketing</li>
        </ul>
        
        <h4>Spintax Syntax:</h4>
        <ul>
        <li>Use {option1|option2|option3} for random selection</li>
        <li>Nested spintax: {Hello {name|friend}|Hi {buddy|pal}}</li>
        <li>Empty options: {|option1|option2} (includes empty string)</li>
        </ul>
        """
        
        msg = QMessageBox(self)
        msg.setWindowTitle(_("templates.help"))
        msg.setTextFormat(Qt.RichText)
        msg.setText(help_text)
        msg.setIcon(QMessageBox.Information)
        msg.exec_()
    
    def toggle_spintax_settings(self, enabled: bool):
        """Toggle spintax settings visibility."""
        self.spintax_example_edit.setEnabled(enabled)
        self.preview_spintax_button.setEnabled(enabled)
        
        # If enabling spintax, validate the current message for spintax syntax
        if enabled:
            # Set a helpful example if the field is empty
            if not self.spintax_example_edit.text().strip():
                self.spintax_example_edit.setText("Hello {name|friend|buddy}, welcome to {our company|our service}!")
            self.validate_spintax_syntax()
    
    def validate_spintax_syntax(self):
        """Validate spintax syntax in the message."""
        message_text = self.message_editor.to_plain_text()
        if not message_text.strip():
            return True
        
        try:
            # Validate spintax syntax
            validation_result = self.spintax_processor.validate_spintax(message_text)
            
            if validation_result["patterns_count"] == 0:
                # Check if message contains variables but no spintax patterns
                message_text = self.message_editor.to_plain_text()
                has_variables = any(var in message_text for var in ['{name}', '{email}', '{phone}', '{company}', '{date}', '{time}'])
                
                if has_variables:
                    # Message has variables but no spintax patterns
                    QMessageBox.information(
                        self, _("templates.spintax_validation"),
                        _("templates.variables_help") + "\n\n"
                        "VARIABLES (what you have):\n"
                        "• {name}, {email}, {company} - These are replaced with actual values\n"
                        "• Example: 'Hello {name}!' becomes 'Hello John!'\n\n"
                        "SPINTAX PATTERNS (for variations):\n"
                        "• {option1|option2|option3} - Creates random variations\n"
                        "• Example: 'Hello {friend|buddy|pal}!' becomes 'Hello friend!' or 'Hello buddy!'\n\n"
                        "To create message variations, change your variables to spintax:\n"
                        "• Instead of: 'Hello {name}!'\n"
                        "• Use: 'Hello {friend|buddy|pal}!'\n\n"
                        "Your current message will be sent as-is with variables replaced."
                    )
                else:
                    # No variables or spintax patterns
                    QMessageBox.information(
                        self, _("templates.spintax_validation"),
                        _("templates.no_patterns_found") + "\n\n"
                        "To use spintax, add patterns like:\n"
                        "• {option1|option2|option3}\n"
                        "• Hello {name|friend|buddy}\n"
                        "• Get {20%|25%|30%} off\n\n"
                        "The message will be sent as-is without variations."
                    )
                return True
            
            if not validation_result["valid"]:
                error_msg = "Invalid spintax syntax:\n\n" + "\n".join(validation_result["errors"])
                QMessageBox.warning(
                    self, _("templates.spintax_validation"),
                    f"{error_msg}\n\n{_('templates.spintax_help')}"
                )
                return False
            return True
        except Exception as e:
            QMessageBox.warning(
                self, _("templates.spintax_validation"),
                f"Error validating spintax syntax:\n\n{str(e)}\n\n"
                "Please check your spintax syntax. Use {{option1|option2|option3}} format."
            )
            return False
    
    def preview_spintax(self):
        """Preview spintax generation."""
        message_text = self.message_editor.to_plain_text()
        if not message_text.strip():
            QMessageBox.warning(self, _("templates.spintax_preview"), _("templates.no_message_text"))
            return
        
        try:
            # Check if text contains spintax patterns
            validation_result = self.spintax_processor.validate_spintax(message_text)
            
            if validation_result["patterns_count"] == 0:
                # Check if message contains variables but no spintax patterns
                has_variables = any(var in message_text for var in ['{name}', '{email}', '{phone}', '{company}', '{date}', '{time}'])
                
                if has_variables:
                    # Message has variables but no spintax patterns
                    QMessageBox.information(
                        self, _("templates.spintax_preview"),
                        _("templates.variables_help") + "\n\n"
                        "VARIABLES (what you have):\n"
                        "• {name}, {email}, {company} - These are replaced with actual values\n"
                        "• Example: 'Hello {name}!' becomes 'Hello John!'\n\n"
                        "SPINTAX PATTERNS (for variations):\n"
                        "• {option1|option2|option3} - Creates random variations\n"
                        "• Example: 'Hello {friend|buddy|pal}!' becomes 'Hello friend!' or 'Hello buddy!'\n\n"
                        "To create message variations, change your variables to spintax:\n"
                        "• Instead of: 'Hello {name}!'\n"
                        "• Use: 'Hello {friend|buddy|pal}!'\n\n"
                        "Current message:\n" + message_text
                    )
                else:
                    # No variables or spintax patterns
                    QMessageBox.information(
                        self, _("templates.spintax_preview"),
                        _("templates.no_patterns_found") + "\n\n"
                        "To use spintax, add patterns like:\n"
                        "• {option1|option2|option3}\n"
                        "• Hello {name|friend|buddy}\n"
                        "• Get {20%|25%|30%} off\n\n"
                        "Current message:\n" + message_text
                    )
                return
            
            # Generate multiple variations using the correct method
            variations = self.spintax_processor.get_preview_samples(message_text, count=5)
            
            # Check if all variations are the same (no actual spintax)
            unique_variations = list(set(variations))
            if len(unique_variations) == 1:
                QMessageBox.information(
                    self, _("templates.spintax_preview"),
                    "No variations generated. This might be because:\n\n"
                    "• Spintax patterns are malformed\n"
                    "• All options in patterns are identical\n"
                    "• Nested spintax is not supported\n\n"
                    "Original message:\n" + message_text
                )
                return
            
            preview_text = f"Spintax Preview ({len(unique_variations)} unique variations):\n\n"
            for i, variation in enumerate(variations, 1):
                preview_text += f"Variation {i}: {variation}\n\n"
            
            msg = QMessageBox(self)
            msg.setWindowTitle(_("templates.spintax_preview"))
            msg.setText(preview_text)
            msg.setIcon(QMessageBox.Information)
            msg.exec_()
            
        except Exception as e:
            QMessageBox.warning(
                self, _("templates.spintax_preview"),
                f"Error generating spintax preview:\n\n{str(e)}\n\n"
                "Please check your spintax syntax."
            )
    
    def load_template_data(self):
        """Load template data into the form."""
        if not self.template:
            return
        
        self.name_edit.setText(self.template.name)
        self.description_edit.setText(self.template.description or "")
        spans = []
        if hasattr(self.template, "entity_spans") and self.template.entity_spans:
            spans = self.template.entity_spans
        self.message_editor.set_content(self.template.body or "", spans)
        self.use_spintax_check.setChecked(self.template.use_spintax)
        self.spintax_example_edit.setText(self.template.spintax_text or "")

        # Load tags
        if self.template.tags:
            tags_list = self.template.get_tags_list()
            self.tags_edit.setText(", ".join(tags_list))
    
    def save_template(self):
        """Save template data."""
        try:
            # Validate required fields
            if not self.name_edit.text().strip():
                QMessageBox.warning(self, _("common.error"), _("templates.name_required"))
                return
            
            if not self.message_editor.to_plain_text().strip():
                QMessageBox.warning(self, _("common.error"), _("templates.message_required"))
                return

            # Validate custom emoji references
            emoji_ids = self.custom_emoji_service.extract_custom_emoji_ids(message_body)
            if emoji_ids:
                validation = self.custom_emoji_service.validate_custom_emoji_ids(emoji_ids)

                if not validation.accounts_checked:
                    QMessageBox.warning(
                        self,
                        "Custom Emoji Warning",
                        "No active Telegram accounts could be checked for custom emojis. The template will be saved, "
                        "but sending may fail if the emojis are unavailable.",
                    )
                elif validation.missing_ids:
                    missing_ids = ", ".join(str(eid) for eid in sorted(validation.missing_ids))
                    QMessageBox.warning(
                        self,
                        "Custom Emoji Warning",
                        "The template references custom emoji IDs that are not available on any configured account:\n"
                        f"Missing IDs: {missing_ids}\n\nPlease update the template or upload the emojis before saving.",
                    )
                    return

            # Validate spintax if enabled
            if self.use_spintax_check.isChecked():
                if not self.validate_spintax_syntax():
                    return
                
                # Validate spintax example if provided
                spintax_example = self.spintax_example_edit.text().strip()
                if spintax_example:
                    try:
                        validation_result = self.spintax_processor.validate_spintax(spintax_example)
                        if not validation_result["valid"]:
                            error_msg = "Invalid spintax syntax in example:\n\n" + "\n".join(validation_result["errors"])
                            QMessageBox.warning(
                                self, _("templates.spintax_validation"),
                                f"{error_msg}\n\n{_('templates.spintax_help')}"
                            )
                            return
                    except Exception as e:
                        QMessageBox.warning(
                            self, _("templates.spintax_validation"),
                            f"Error validating spintax example:\n\n{str(e)}\n\n"
                            "Please check your spintax syntax. Use {{option1|option2|option3}} format."
                        )
                        return
            
            # Create or update template
            if self.template:
                # Update existing template
                self.template.name = self.name_edit.text().strip()
                self.template.description = self.description_edit.text().strip() or None
                self.template.body = self.message_editor.to_plain_text().strip()
                self.template.use_spintax = self.use_spintax_check.isChecked()
                self.template.spintax_text = self.spintax_example_edit.text().strip() or None
            else:
                # Create new template
                self.template = MessageTemplate(
                    name=self.name_edit.text().strip(),
                    description=self.description_edit.text().strip() or None,
                    body=self.message_editor.to_plain_text().strip(),
                    use_spintax=self.use_spintax_check.isChecked(),
                    spintax_text=self.spintax_example_edit.text().strip() or None
                )

            spans = self.message_editor.get_entity_spans()
            self.template.entity_spans = spans if spans else None
            
            # Update tags
            tags_text = self.tags_edit.text().strip()
            if tags_text:
                tags_list = [tag.strip() for tag in tags_text.split(",") if tag.strip()]
                self.template.set_tags_list(tags_list)
            else:
                self.template.set_tags_list([])
            
            # Save to database
            session = get_session()
            try:
                if self.template.id is None:
                    session.add(self.template)
                else:
                    session.merge(self.template)
                session.commit()
                
                # Get the saved template ID before closing session
                template_id = self.template.id
                template_name = self.template.name
            finally:
                session.close()
            
            self.logger.info(f"Template saved: {template_name}")
            self.template_saved.emit(template_id)
            self.accept()
            
        except Exception as e:
            self.logger.error(f"Error saving template: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save template: {e}")


class TemplateListWidget(QWidget):
    """Widget for displaying and managing templates."""
    
    template_selected = pyqtSignal(int)
    template_updated = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = get_logger()
        self.translation_manager = get_translation_manager()
        
        # Connect language change signal
        self.translation_manager.language_changed.connect(self.on_language_changed)
        
        self.setup_ui()
        self.load_templates()
        
    
    def setup_ui(self):
        """Set up the UI."""
        layout = QVBoxLayout(self)
        
        # Header
        header_layout = QHBoxLayout()
        
        title_label = QLabel("Message Templates")
        title_label.setFont(QFont("Arial", 14, QFont.Bold))
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        self.add_button = QPushButton("Add Template")
        self.add_button.clicked.connect(self.add_template)
        header_layout.addWidget(self.add_button)
        
        self.import_button = QPushButton("Import CSV")
        self.import_button.clicked.connect(self.import_csv)
        header_layout.addWidget(self.import_button)
        
        self.export_button = QPushButton("Export CSV")
        self.export_button.clicked.connect(self.export_csv)
        header_layout.addWidget(self.export_button)
        
        self.edit_button = QPushButton("Edit Template")
        self.edit_button.clicked.connect(self.edit_template)
        self.edit_button.setEnabled(False)
        header_layout.addWidget(self.edit_button)
        
        self.delete_button = QPushButton("Delete Template")
        self.delete_button.clicked.connect(self.delete_template)
        self.delete_button.setEnabled(False)
        header_layout.addWidget(self.delete_button)
        
        layout.addLayout(header_layout)
        
        # Search field
        search_layout = QHBoxLayout()
        search_label = QLabel("Search:")
        search_label.setStyleSheet("color: white; font-weight: bold;")
        search_layout.addWidget(search_label)
        
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search templates by name, description, tags, or content...")
        self.search_edit.textChanged.connect(self.filter_templates)
        self.search_edit.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 2px solid #404040;
                border-radius: 4px;
                background-color: #2d2d2d;
                color: white;
            }
            QLineEdit:focus {
                border-color: #0078d4;
            }
        """)
        search_layout.addWidget(self.search_edit)
        search_layout.addStretch()
        
        layout.addLayout(search_layout)
        
        # Templates table
        self.templates_table = QTableWidget()
        self.templates_table.setColumnCount(6)
        self.templates_table.setHorizontalHeaderLabels([
            "Name", "Description", "Message Preview", "Spintax", "Tags", "Actions"
        ])
        
        # Configure table
        header = self.templates_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        
        self.templates_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.templates_table.setSelectionMode(QTableWidget.SingleSelection)
        self.templates_table.setAlternatingRowColors(True)
        self.templates_table.itemSelectionChanged.connect(self.on_selection_changed)
        
        # Set custom styling for black and gray alternating rows
        self.templates_table.setStyleSheet("""
            QTableWidget {
                alternate-background-color: #2d2d2d;
                background-color: #1a1a1a;
                gridline-color: #404040;
                color: white;
                selection-background-color: #0078d4;
                selection-color: white;
            }
            QTableWidget::item {
                padding: 8px;
                border: none;
            }
            QTableWidget::item:selected {
                background-color: #0078d4 !important;
                color: white !important;
            }
            QTableWidget::item:alternate {
                background-color: #2d2d2d;
            }
            QTableWidget::item:alternate:selected {
                background-color: #0078d4 !important;
                color: white !important;
            }
        """)
        
        # Connect cell clicked signal for actions
        self.templates_table.cellClicked.connect(self.on_cell_clicked)
        
        layout.addWidget(self.templates_table)
        
        # Status bar
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)
    
    def load_templates(self):
        """Load templates from database."""
        try:
            session = get_session()
            try:
                from ...models import MessageTemplate
                from sqlmodel import select
                templates = session.exec(select(MessageTemplate).where(MessageTemplate.is_deleted == False)).all()
            finally:
                session.close()
            
            self.templates_table.setRowCount(len(templates))
            
            for row, template in enumerate(templates):
                # Name - Disabled text field
                name_item = QTableWidgetItem(template.name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable | Qt.ItemIsSelectable)
                # Store template ID in the name item for selection handling
                name_item.setData(Qt.UserRole, template.id)
                self.templates_table.setItem(row, 0, name_item)
                
                # Description - Disabled text field
                description_item = QTableWidgetItem(template.description or "")
                description_item.setFlags(description_item.flags() & ~Qt.ItemIsEditable | Qt.ItemIsSelectable)
                self.templates_table.setItem(row, 1, description_item)
                
                # Message Preview - Disabled text field
                message_preview = template.body[:100] + "..." if len(template.body) > 100 else template.body
                message_item = QTableWidgetItem(message_preview)
                message_item.setFlags(message_item.flags() & ~Qt.ItemIsEditable | Qt.ItemIsSelectable)
                self.templates_table.setItem(row, 2, message_item)
                
                # Spintax - Enhanced button-like appearance
                spintax_item = QTableWidgetItem("Yes" if template.use_spintax else "No")
                spintax_item.setFlags(spintax_item.flags() & ~Qt.ItemIsEditable | Qt.ItemIsSelectable)
                
                # Set spintax-specific styling
                if template.use_spintax:
                    spintax_item.setBackground(QColor(34, 197, 94))  # Green
                    spintax_item.setForeground(Qt.white)
                else:
                    spintax_item.setBackground(QColor(107, 114, 128))  # Gray
                    spintax_item.setForeground(Qt.white)
                
                # Center align spintax text
                spintax_item.setTextAlignment(Qt.AlignCenter)
                self.templates_table.setItem(row, 3, spintax_item)
                
                # Tags - Disabled text field
                tags_list = template.get_tags_list()
                tags_text = ", ".join(tags_list) if tags_list else "No tags"
                tags_item = QTableWidgetItem(tags_text)
                tags_item.setFlags(tags_item.flags() & ~Qt.ItemIsEditable | Qt.ItemIsSelectable)
                self.templates_table.setItem(row, 4, tags_item)
                
                # Actions - Create action buttons
                actions_item = QTableWidgetItem("Edit | Delete | Preview")
                actions_item.setFlags(actions_item.flags() & ~Qt.ItemIsEditable | Qt.ItemIsSelectable)
                actions_item.setTextAlignment(Qt.AlignCenter)
                actions_item.setData(Qt.UserRole, template.id)  # Store template ID for actions
                self.templates_table.setItem(row, 5, actions_item)
            
            self.status_label.setText(f"Loaded {len(templates)} templates")
            
            # Apply search filter if there's search text
            self.filter_templates()
            
        except Exception as e:
            self.logger.error(f"Error loading templates: {e}")
            self.status_label.setText(f"Error loading templates: {e}")
    
    def on_cell_clicked(self, row, column):
        """Handle cell click events."""
        if column == 5:  # Actions column
            template_id = self.templates_table.item(row, 0).data(Qt.UserRole)
            if template_id is not None:
                self.show_action_menu(row, column, template_id)
        else:
            # For other columns, ensure the row is selected
            self.templates_table.selectRow(row)
            # Also trigger selection changed manually
            self.on_selection_changed()
    
    def show_action_menu(self, row, column, template_id):
        """Show action menu for template actions."""
        from PyQt5.QtWidgets import QMenu
        
        # Get template name for display
        template_name = self.templates_table.item(row, 0).text()
        
        # Create context menu
        menu = QMenu(self)
        
        # Edit action
        edit_action = menu.addAction("✏️ Edit")
        edit_action.triggered.connect(lambda: self.edit_template_by_id(template_id))
        
        # Delete action
        delete_action = menu.addAction("🗑️ Delete")
        delete_action.triggered.connect(lambda: self.delete_template_by_id(template_id))
        
        # Preview action
        preview_action = menu.addAction("👁️ Preview")
        preview_action.triggered.connect(lambda: self.preview_template_by_id(template_id))
        
        # Show menu at cursor position
        menu.exec_(self.templates_table.mapToGlobal(
            self.templates_table.visualItemRect(self.templates_table.item(row, column)).bottomLeft()
        ))
    
    def edit_template_by_id(self, template_id):
        """Edit template by ID."""
        session = get_session()
        try:
            from ...models import MessageTemplate
            from sqlmodel import select
            template = session.exec(select(MessageTemplate).where(MessageTemplate.id == template_id)).first()
        finally:
            session.close()
        
        if template:
            dialog = TemplateDialog(self, template)
            if dialog.exec_() == QDialog.Accepted:
                self.load_templates()
    
    def delete_template_by_id(self, template_id):
        """Delete template by ID."""
        session = get_session()
        try:
            from ...models import MessageTemplate
            from sqlmodel import select
            template = session.exec(select(MessageTemplate).where(MessageTemplate.id == template_id)).first()
        finally:
            session.close()
        
        if template:
            reply = QMessageBox.question(
                self, 
                "Delete Template", 
                f"Are you sure you want to delete template '{template.name}'?",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                try:
                    template.soft_delete()
                    session.commit()
                    self.logger.info(f"Template deleted: {template.name}")
                    self.load_templates()
                except Exception as e:
                    self.logger.error(f"Error deleting template: {e}")
                    QMessageBox.critical(self, "Error", f"Failed to delete template: {e}")
    
    def preview_template_by_id(self, template_id):
        """Preview template by ID."""
        session = get_session()
        try:
            from ...models import MessageTemplate
            from sqlmodel import select
            template = session.exec(select(MessageTemplate).where(MessageTemplate.id == template_id)).first()
        finally:
            session.close()
        
        if template:
            preview_text = f"Template: {template.name}\n\n"
            preview_text += f"Description: {template.description or 'No description'}\n\n"
            preview_text += f"Message Text:\n{template.body}\n\n"
            preview_text += f"Spintax: {'Yes' if template.use_spintax else 'No'}\n"
            if template.use_spintax and template.spintax_text:
                preview_text += f"Spintax Example: {template.spintax_text}\n"
            preview_text += f"Tags: {', '.join(template.get_tags_list()) if template.get_tags_list() else 'No tags'}"
            
            QMessageBox.information(self, f"Template Preview - {template.name}", preview_text)
    
    def on_selection_changed(self):
        """Handle selection change."""
        selected_rows = self.templates_table.selectionModel().selectedRows()
        has_selection = len(selected_rows) > 0
        
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        
        if has_selection:
            row = selected_rows[0].row()
            # Try to get template ID from the first column (Name column)
            name_item = self.templates_table.item(row, 0)
            if name_item:
                template_id = name_item.data(Qt.UserRole)
                if template_id is not None:
                    # Emit signal with template ID for further processing
                    self.template_selected.emit(template_id)
                else:
                    self.logger.warning(f"No template ID found for row {row}")
            else:
                self.logger.warning(f"No name item found for row {row}")
    
    def add_template(self):
        """Add new template."""
        dialog = TemplateDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_templates()
    
    def edit_template(self):
        """Edit selected template."""
        selected_rows = self.templates_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        
        row = selected_rows[0].row()
        template_id = self.templates_table.item(row, 0).data(Qt.UserRole)
        
        # Load template from database
        session = get_session()
        try:
            from ...models import MessageTemplate
            from sqlmodel import select
            template = session.exec(select(MessageTemplate).where(MessageTemplate.id == template_id)).first()
        finally:
            session.close()
        
        if template:
            dialog = TemplateDialog(self, template)
            if dialog.exec_() == QDialog.Accepted:
                self.load_templates()
    
    def delete_template(self):
        """Delete selected template."""
        selected_rows = self.templates_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        
        row = selected_rows[0].row()
        template_name = self.templates_table.item(row, 0).text()
        template_id = self.templates_table.item(row, 0).data(Qt.UserRole)
        
        reply = QMessageBox.question(
            self, 
            "Delete Template", 
            f"Are you sure you want to delete template '{template_name}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                session = get_session()
                try:
                    from ...models import MessageTemplate
                    from sqlmodel import select
                    template = session.exec(select(MessageTemplate).where(MessageTemplate.id == template_id)).first()
                    if template:
                        template.soft_delete()
                        session.commit()
                finally:
                    session.close()
                
                self.logger.info(f"Template deleted: {template_name}")
                self.load_templates()
                
            except Exception as e:
                self.logger.error(f"Error deleting template: {e}")
                QMessageBox.critical(self, "Error", f"Failed to delete template: {e}")
    
    def import_csv(self):
        """Import templates from CSV file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Templates from CSV", "", "CSV Files (*.csv)"
        )
        
        if not file_path:
            return
        
        try:
            import json
            import pandas as pd
            
            # Read CSV file
            df = pd.read_csv(file_path)
            
            # Validate required columns
            required_columns = ['name', 'description', 'body']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                QMessageBox.warning(
                    self, "Invalid CSV", 
                    f"Missing required columns: {', '.join(missing_columns)}\n"
                    f"Required columns: {', '.join(required_columns)}"
                )
                return
            
            # Import templates
            session = get_session()
            imported_count = 0
            
            try:
                for _, row in df.iterrows():
                    # Check if template already exists
                    existing = session.query(MessageTemplate).filter(
                        MessageTemplate.name == row['name']
                    ).first()
                    
                    if existing:
                        self.logger.warning(f"Template '{row['name']}' already exists, skipping")
                        continue
                    
                    # Create new template
                    template = MessageTemplate(
                        name=row['name'],
                        description=row.get('description', ''),
                        body=row['body'],
                        use_spintax=row.get('use_spintax', False),
                        spintax_text=row.get('spintax_text', ''),
                        category=row.get('category', 'general'),
                        is_active=row.get('is_active', True)
                    )

                    if 'entity_spans' in row and pd.notna(row['entity_spans']):
                        try:
                            spans_value = row['entity_spans']
                            if isinstance(spans_value, str):
                                spans = json.loads(spans_value)
                            else:
                                spans = spans_value
                            template.entity_spans = spans if spans else None
                        except (TypeError, ValueError, json.JSONDecodeError):
                            self.logger.warning("Could not parse entity spans from CSV; skipping spans for this row.")

                    # Handle tags
                    if 'tags' in row and pd.notna(row['tags']):
                        tags = [tag.strip() for tag in str(row['tags']).split(',') if tag.strip()]
                        template.set_tags_list(tags)
                    
                    session.add(template)
                    imported_count += 1
                
                session.commit()
                self.logger.info(f"Imported {imported_count} templates from CSV")
                QMessageBox.information(
                    self, "Import Successful", 
                    f"Successfully imported {imported_count} templates from CSV file."
                )
                self.load_templates()
                
            finally:
                session.close()
                
        except Exception as e:
            self.logger.error(f"Error importing CSV: {e}")
            QMessageBox.critical(self, "Import Error", f"Failed to import CSV: {e}")
    
    def export_csv(self):
        """Export templates to CSV file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Templates to CSV", "templates.csv", "CSV Files (*.csv)"
        )
        
        if not file_path:
            return
        
        try:
            import json
            import pandas as pd
            
            # Get all templates
            session = get_session()
            try:
                templates = session.query(MessageTemplate).filter(
                    MessageTemplate.deleted_at.is_(None)
                ).all()
                
                if not templates:
                    QMessageBox.information(self, "No Data", "No templates to export.")
                    return
                
                # Prepare data for export
                data = []
                for template in templates:
                    data.append({
                        'name': template.name,
                        'description': template.description or '',
                        'body': template.body,
                        'use_spintax': template.use_spintax,
                        'spintax_text': template.spintax_text or '',
                        'category': template.category,
                        'is_active': template.is_active,
                        'tags': ', '.join(template.get_tags_list()) if template.get_tags_list() else '',
                        'entity_spans': json.dumps(template.entity_spans) if template.entity_spans else '',
                        'created_at': template.created_at.isoformat() if template.created_at else '',
                        'updated_at': template.updated_at.isoformat() if template.updated_at else ''
                    })
                
                # Create DataFrame and export
                df = pd.DataFrame(data)
                df.to_csv(file_path, index=False)
                
                self.logger.info(f"Exported {len(templates)} templates to CSV")
                QMessageBox.information(
                    self, "Export Successful", 
                    f"Successfully exported {len(templates)} templates to CSV file."
                )
                
            finally:
                session.close()
                
        except Exception as e:
            self.logger.error(f"Error exporting CSV: {e}")
            QMessageBox.critical(self, "Export Error", f"Failed to export CSV: {e}")
    
    def filter_templates(self):
        """Filter templates based on search text."""
        search_text = self.search_edit.text().lower().strip()
        
        if not search_text:
            # Show all templates
            for row in range(self.templates_table.rowCount()):
                self.templates_table.setRowHidden(row, False)
            return
        
        # Filter templates (exclude Actions column - column 5)
        for row in range(self.templates_table.rowCount()):
            should_show = False
            
            # Check all columns except Actions column for search text
            for col in range(self.templates_table.columnCount() - 1):  # Exclude last column (Actions)
                item = self.templates_table.item(row, col)
                if item and search_text in item.text().lower():
                    should_show = True
                    break
            
            self.templates_table.setRowHidden(row, not should_show)
    
    def on_language_changed(self, language: str):
        """Handle language change."""
        self.logger.info(f"Language changed to: {language}")
        # Recreate the UI with new translations
        self.setup_ui()
        self.load_templates()


class TemplateWidget(QWidget):
    """Main template management widget."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = get_logger()
        self.translation_manager = get_translation_manager()
        
        # Connect language change signal
        self.translation_manager.language_changed.connect(self.on_language_changed)
        
        self.setup_ui()
    
    def setup_ui(self):
        """Set up the UI."""
        layout = QVBoxLayout(self)
        
        # Template list
        self.template_list = TemplateListWidget()
        layout.addWidget(self.template_list)
        
        # Connect signals
        self.template_list.template_selected.connect(self.on_template_selected)
        self.template_list.template_updated.connect(self.on_template_updated)
    
    def on_template_selected(self, template_id):
        """Handle template selection."""
        # This could show template details in a side panel
        pass
    
    def on_template_updated(self, template_id):
        """Handle template update."""
        # Refresh the list
        self.template_list.load_templates()
    
    def on_language_changed(self, language: str):
        """Handle language change."""
        self.logger.info(f"Language changed to: {language}")
        # The template_list widget will handle its own language change
        # No need to recreate the UI since it only contains the template_list

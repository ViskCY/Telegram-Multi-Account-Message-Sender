"""Template models for managing message templates."""

from datetime import datetime
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

try:
    from pydantic import model_validator
except ImportError:  # Pydantic v1 fallback
    model_validator = None  # type: ignore
    from pydantic import root_validator
from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship

from .base import BaseModel, SoftDeleteMixin, JSONFieldMixin
from app.utils.text_entities import (
    RenderedMessage,
    compose_personalized_rich_text,
    parse_span_metadata,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.core.spintax import SpintaxProcessor


class TemplateType(str, Enum):
    """Template type enumeration."""
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    STICKER = "sticker"
    ANIMATION = "animation"


class TemplateCategory(str, Enum):
    """Template category enumeration."""
    GENERAL = "general"
    MARKETING = "marketing"
    NOTIFICATION = "notification"
    WELCOME = "welcome"
    FOLLOW_UP = "follow_up"
    REMINDER = "reminder"
    PROMOTIONAL = "promotional"


class MessageTemplate(BaseModel, SoftDeleteMixin, JSONFieldMixin, table=True):
    """Message template model."""
    
    __tablename__ = "templates"
    
    # Basic info
    name: str = Field(index=True)
    description: Optional[str] = Field(default=None)
    template_type: TemplateType = Field(default=TemplateType.TEXT)
    category: TemplateCategory = Field(default=TemplateCategory.GENERAL)
    
    # Content
    subject: Optional[str] = Field(default=None)
    body: str
    entity_spans: Optional[List[Dict[str, Any]]] = Field(default=None, sa_column=JSON)
    media_path: Optional[str] = Field(default=None)
    caption: Optional[str] = Field(default=None)
    subject_span_metadata: Optional[str] = Field(default=None, sa_column=JSON)
    body_span_metadata: Optional[str] = Field(default=None, sa_column=JSON)
    caption_span_metadata: Optional[str] = Field(default=None, sa_column=JSON)
    rich_body: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Ordered spans describing the template body",
    )
    rich_caption: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Ordered spans describing the template caption",
    )
    
    # Variables and personalization
    variables: Optional[str] = Field(default=None, sa_column=JSON)
    variable_descriptions: Optional[str] = Field(default=None, sa_column=JSON)
    use_spintax: bool = Field(default=False)
    spintax_text: Optional[str] = Field(default=None)
    
    # A/B Testing
    use_ab_testing: bool = Field(default=False)
    ab_variants: Optional[str] = Field(default=None, sa_column=JSON)
    
    # Organization
    tags: Optional[str] = Field(default=None, sa_column=JSON)
    notes: Optional[str] = Field(default=None)
    
    # Usage statistics
    usage_count: int = Field(default=0)
    last_used: Optional[datetime] = Field(default=None)
    success_rate: float = Field(default=0.0)
    
    # Settings
    is_active: bool = Field(default=True)
    is_public: bool = Field(default=False)
    created_by: Optional[str] = Field(default=None)
    
    # Relationships
    # campaigns: List["Campaign"] = Relationship(back_populates="template")
    
    _SPAN_FLAGS = (
        "bold",
        "italic",
        "underline",
        "strikethrough",
        "code",
        "spoiler",
    )

    @classmethod
    def _default_span(cls, fallback_text: str = "", emoji_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a default rich text span."""
        span: Dict[str, Any] = {
            "emoji_id": emoji_id,
            "fallback_text": fallback_text,
            "link": None,
        }
        for flag in cls._SPAN_FLAGS:
            span[flag] = False
        return span

    @classmethod
    def _normalize_spans(cls, spans: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Normalize spans ensuring required keys exist and order is preserved."""
        if not spans:
            return []

        normalized: List[Dict[str, Any]] = []
        for span in spans:
            if not isinstance(span, dict):
                continue
            normalized_span = cls._default_span()
            normalized_span.update(span)
            fallback = normalized_span.get("fallback_text")
            if fallback is None:
                fallback = span.get("text", "")  # Legacy compatibility
            normalized_span["fallback_text"] = fallback or ""
            normalized.append(normalized_span)
        return normalized

    @classmethod
    def _spans_from_plain_text(cls, text: Optional[str]) -> List[Dict[str, Any]]:
        """Create a single-span representation from plain text."""
        if not text:
            return []
        return [cls._default_span(fallback_text=text)]

    @staticmethod
    def _spans_to_plain_text(spans: Optional[Iterable[Dict[str, Any]]]) -> str:
        """Concatenate fallback text from spans into a plain string."""
        if not spans:
            return ""
        return "".join(str(span.get("fallback_text", "")) for span in spans)

    def _ensure_rich_body(self) -> None:
        """Synchronize body text and rich span data."""
        if self.rich_body:
            normalized = self._normalize_spans(self.rich_body)
            self.rich_body = normalized or None
            self.body = self._spans_to_plain_text(normalized)
        else:
            spans = self._spans_from_plain_text(self.body)
            self.rich_body = spans or None

    def _ensure_rich_caption(self) -> None:
        """Synchronize caption text and rich span data."""
        if self.rich_caption:
            normalized = self._normalize_spans(self.rich_caption)
            self.rich_caption = normalized or None
            if normalized:
                text = self._spans_to_plain_text(normalized)
                self.caption = text or None
        elif self.caption:
            spans = self._spans_from_plain_text(self.caption)
            self.rich_caption = spans or None

    if model_validator is not None:

        @model_validator(mode="after")
        def _sync_rich_fields(self) -> "MessageTemplate":
            """Ensure rich content stays synchronized with plain text fields."""
            self._ensure_rich_body()
            self._ensure_rich_caption()
            return self

    else:  # Pydantic v1 fallback

        @root_validator(pre=False)
        def _sync_rich_fields(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            """Synchronize rich text structures when loading/saving models."""
            body = values.get("body")
            rich_body = values.get("rich_body")
            if rich_body:
                normalized = cls._normalize_spans(rich_body)
                values["rich_body"] = normalized or None
                values["body"] = cls._spans_to_plain_text(normalized) or body
            else:
                spans = cls._spans_from_plain_text(body)
                values["rich_body"] = spans or None

            caption = values.get("caption")
            rich_caption = values.get("rich_caption")
            if rich_caption:
                normalized_caption = cls._normalize_spans(rich_caption)
                values["rich_caption"] = normalized_caption or None
                if normalized_caption:
                    values["caption"] = cls._spans_to_plain_text(normalized_caption)
            elif caption:
                spans = cls._spans_from_plain_text(caption)
                values["rich_caption"] = spans or None

            return values

    def get_body_spans(self) -> List[Dict[str, Any]]:
        """Return the body as structured rich text spans."""
        self._ensure_rich_body()
        return list(self.rich_body or [])

    def set_body_spans(self, spans: Iterable[Dict[str, Any]]) -> None:
        """Set the body using structured rich text spans."""
        normalized = self._normalize_spans(spans)
        self.rich_body = normalized or None
        self.body = self._spans_to_plain_text(normalized)

    def get_body_text(self) -> str:
        """Return the body as plain text."""
        self._ensure_rich_body()
        return self.body or ""

    def set_body_text(self, text: str) -> None:
        """Set the body as plain text while keeping rich data synced."""
        self.body = text or ""
        spans = self._spans_from_plain_text(self.body)
        self.rich_body = spans or None

    def get_caption_spans(self) -> List[Dict[str, Any]]:
        """Return the caption as structured rich text spans."""
        self._ensure_rich_caption()
        return list(self.rich_caption or [])

    def set_caption_spans(self, spans: Iterable[Dict[str, Any]]) -> None:
        """Set the caption using structured rich text spans."""
        normalized = self._normalize_spans(spans)
        self.rich_caption = normalized or None
        self.caption = self._spans_to_plain_text(normalized) or None

    def get_caption_text(self) -> str:
        """Return the caption as plain text."""
        self._ensure_rich_caption()
        return self.caption or ""

    def set_caption_text(self, text: Optional[str]) -> None:
        """Set the caption as plain text while keeping rich data synced."""
        self.caption = text or None
        spans = self._spans_from_plain_text(self.caption)
        self.rich_caption = spans or None

    def get_available_variables(self) -> List[str]:
        """Get list of available variables in the template."""
        return self.variables.copy()
    
    def add_variable(self, variable: str, description: str = "") -> None:
        """Add a variable to the template."""
        if variable not in self.variables:
            self.variables.append(variable)
            if description:
                self.variable_descriptions[variable] = description
    
    def remove_variable(self, variable: str) -> None:
        """Remove a variable from the template."""
        if variable in self.variables:
            self.variables.remove(variable)
            self.variable_descriptions.pop(variable, None)
    
    def _get_span_metadata(self, field_name: str) -> List[Dict[str, Any]]:
        """Return parsed span metadata for the given field."""

        metadata_map = {
            "subject": self.subject_span_metadata,
            "body": self.body_span_metadata,
            "caption": self.caption_span_metadata,
        }

        return parse_span_metadata(metadata_map.get(field_name))

    def render_template(self, variables: Dict[str, str]) -> Dict[str, str]:
        """Render template with provided variables."""
        rendered = {
            "subject": self.subject,
            "body": self.get_body_text(),
            "caption": self.get_caption_text() or None,
        }
        return rendered

    def render_template(
        self,
        variables: Dict[str, str],
        spintax_processor: Optional["SpintaxProcessor"] = None,
    ) -> Dict[str, RenderedMessage]:
        """Render template with provided variables returning text and entities."""

        replacements = {f"{{{{{var}}}}}": str(value) for var, value in variables.items()}
        rendered: Dict[str, RenderedMessage] = {}

        for field in ["subject", "body", "caption"]:
            text_value = getattr(self, field) or ""
            span_metadata = self._get_span_metadata(field)
            rendered[field] = compose_personalized_rich_text(
                text_value,
                span_metadata,
                replacements=replacements,
                spintax_processor=spintax_processor,
                use_spintax=self.use_spintax,
            )

        return rendered
    
    def validate_variables(self, variables: Dict[str, str]) -> List[str]:
        """Validate that all required variables are provided."""
        missing = []
        for var in self.variables:
            if var not in variables or not variables[var]:
                missing.append(var)
        return missing
    
    def get_preview_text(self, max_length: int = 100) -> str:
        """Get preview text for the template."""
        preview = self.get_body_text()
        if len(preview) > max_length:
            preview = preview[:max_length] + "..."
        return preview
    
    def increment_usage(self, success: bool = True) -> None:
        """Increment usage statistics."""
        self.usage_count += 1
        self.last_used = datetime.utcnow()
        
        # Update success rate (simple moving average)
        if success:
            self.success_rate = (self.success_rate * (self.usage_count - 1) + 100) / self.usage_count
        else:
            self.success_rate = (self.success_rate * (self.usage_count - 1) + 0) / self.usage_count
    
    def get_tags_list(self) -> List[str]:
        """Get tags as a list."""
        if self.tags:
            import json
            if isinstance(self.tags, str):
                try:
                    return json.loads(self.tags)
                except (json.JSONDecodeError, TypeError):
                    return []
            return self.tags if isinstance(self.tags, list) else []
        return []
    
    def set_tags_list(self, tags: List[str]) -> None:
        """Set tags from a list."""
        import json
        self.tags = json.dumps(tags) if tags else None
    
    def get_ab_variant(self, recipient_id: int) -> Dict[str, Any]:
        """Get A/B test variant for a recipient."""
        if not self.use_ab_testing or not self.ab_variants:
            return {
                "subject": self.subject,
                "body": self.get_body_text(),
                "media_path": self.media_path,
                "caption": self.get_caption_text() or None,
            }

        # Simple round-robin assignment based on recipient_id
        variant_index = recipient_id % len(self.ab_variants)
        return self.ab_variants[variant_index]
    
    def is_usable(self) -> bool:
        """Check if template can be used."""
        return (
            self.is_active
            and not self.is_deleted
            and bool(self.get_body_text().strip())
        )

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    consent_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("draft", "active", "paused", "archived", name="campaign_status"),
        default="draft",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    questions: Mapped[list["Question"]] = relationship(
        "Question",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    rules: Mapped[list["BranchRule"]] = relationship(
        "BranchRule",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    participants: Mapped[list["Participant"]] = relationship(
        "Participant",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(
        Enum("rating", "mcq", "free_text", name="question_type"),
        nullable=False,
    )
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    campaign: Mapped[Campaign] = relationship("Campaign", back_populates="questions")

    __table_args__ = (
        UniqueConstraint("campaign_id", "key", name="uq_campaign_question_key"),
    )


class BranchRule(Base):
    __tablename__ = "branch_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    source_question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    operator: Mapped[str] = mapped_column(
        Enum("equals", "not_equals", "contains", "gt", "lt", name="rule_operator"),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(String(256), nullable=False)
    action: Mapped[str] = mapped_column(
        Enum("goto", "end", "escalate", name="rule_action"),
        nullable=False,
    )
    target_question_id: Mapped[int | None] = mapped_column(
        ForeignKey("questions.id"),
        nullable=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=100)

    campaign: Mapped[Campaign] = relationship("Campaign", back_populates="rules")


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "contacted",
            "completed",
            "failed",
            name="participant_status",
        ),
        default="pending",
        nullable=False,
    )
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    campaign: Mapped[Campaign] = relationship("Campaign", back_populates="participants")

    __table_args__ = (
        UniqueConstraint("campaign_id", "phone_number", name="uq_campaign_phone"),
    )

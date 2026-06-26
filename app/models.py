from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
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

class CampaignExecution(Base):
    __tablename__ = "campaign_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id"),
        unique=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        Enum("idle", "running", "paused", "stopped", name="execution_state"),
        default="idle",
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CallingPolicy(Base):
    __tablename__ = "calling_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id"),
        unique=True,
        index=True,
    )
    window_start_hour: Mapped[int] = mapped_column(Integer, default=9, nullable=False)
    window_end_hour: Mapped[int] = mapped_column(Integer, default=18, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    retry_delay_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    cooldown_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    max_calls_per_minute: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CallAttempt(Base):
    __tablename__ = "call_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(
        Enum("success", "failed", name="call_outcome"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class CallLog(Base):
    """SAA-101/SAA-105: Persisted record of every voice call session."""

    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    participant_id: Mapped[int | None] = mapped_column(
        ForeignKey("participants.id"), nullable=True, index=True
    )
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "completed", "not_now", "failed", "escalated", name="call_status"),
        default="active",
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    turns_count: Mapped[int] = mapped_column(Integer, default=0)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    rapport_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    campaign: Mapped["Campaign"] = relationship("Campaign")
    structured_answers: Mapped[list["Answer"]] = relationship(
        "Answer", back_populates="call_log", cascade="all, delete-orphan"
    )
    turns: Mapped[list["ConversationTurn"]] = relationship(
        "ConversationTurn", back_populates="call_log", cascade="all, delete-orphan",
        order_by="ConversationTurn.turn_index",
    )


class Interviewee(Base):
    """Persistent person profile identified by phone number — survives across campaigns."""

    __tablename__ = "interviewees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    phone_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    demographics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    answers: Mapped[list["Answer"]] = relationship("Answer", back_populates="interviewee")


class Answer(Base):
    """One structured answer per question per session — enables analytics queries."""

    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("call_logs.session_id"), index=True, nullable=False
    )
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    question_id: Mapped[int | None] = mapped_column(
        ForeignKey("questions.id"), nullable=True, index=True
    )
    interviewee_id: Mapped[int | None] = mapped_column(
        ForeignKey("interviewees.id"), nullable=True, index=True
    )
    question_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(String(256), nullable=True)
    answer_type: Mapped[str] = mapped_column(
        Enum("rating", "mcq", "free_text", "unknown", name="answer_type"),
        default="unknown",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    call_log: Mapped["CallLog"] = relationship("CallLog", back_populates="structured_answers")
    question: Mapped["Question"] = relationship("Question")
    interviewee: Mapped["Interviewee"] = relationship("Interviewee", back_populates="answers")


class ConversationTurn(Base):
    """SAA-114/115: One turn in a voice session — caller speech or bot response."""

    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("call_logs.session_id"), index=True, nullable=False
    )
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(
        Enum("bot", "caller", name="turn_speaker"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    stt_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    dialogue_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    call_log: Mapped["CallLog"] = relationship("CallLog", back_populates="turns")


class FreeTextLabel(Base):
    """SAA-118: Taxonomy label for free-text normalization."""

    __tablename__ = "free_text_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    question_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("campaign_id", "question_key", "label", name="uq_label_per_question"),
    )


class AnswerLabel(Base):
    """SAA-119/120: Mapping of a free-text Answer to a FreeTextLabel with confidence."""

    __tablename__ = "answer_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), index=True, nullable=False)
    label_id: Mapped[int] = mapped_column(ForeignKey("free_text_labels.id"), index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    method: Mapped[str] = mapped_column(
        Enum("keyword", "llm", "manual", name="label_method"), default="keyword", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    answer: Mapped["Answer"] = relationship("Answer")
    label: Mapped["FreeTextLabel"] = relationship("FreeTextLabel")


class EntityMention(Base):
    """Named-entity mention extracted from a conversation turn (NER feature)."""

    __tablename__ = "entity_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("call_logs.session_id"), index=True, nullable=False
    )
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    turn_id: Mapped[int | None] = mapped_column(ForeignKey("conversation_turns.id"), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(
        Enum("PERSON", "PLACE", "ORG", "DATE", "NUMBER", "OTHER", name="entity_type"),
        nullable=False,
    )
    entity_value: Mapped[str] = mapped_column(String(256), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    method: Mapped[str] = mapped_column(
        Enum("rule", "llm", name="ner_method"), default="rule", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class FreeTextAnalysis(Base):
    """Deep analysis of a free-text Answer: sentiment, intent, topics, key insights."""

    __tablename__ = "free_text_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), unique=True, index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    question_key: Mapped[str] = mapped_column(String(128), nullable=False)
    sentiment: Mapped[str] = mapped_column(
        Enum("positive", "negative", "neutral", "mixed", name="sentiment_type"),
        nullable=False,
        default="neutral",
    )
    intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key_insights: Mapped[list] = mapped_column(JSON, default=list)
    topics: Mapped[list] = mapped_column(JSON, default=list)
    method: Mapped[str] = mapped_column(
        Enum("keyword", "llm", name="analysis_method"), default="keyword", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    answer: Mapped["Answer"] = relationship("Answer")


class CrossSurveyMatch(Base):
    """An answer from one campaign that satisfies a question in another campaign."""

    __tablename__ = "cross_survey_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), index=True, nullable=False)
    source_campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    source_question_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_question_prompt: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    target_question_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_question_prompt: Mapped[str | None] = mapped_column(String(512), nullable=True)
    matched_topics: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_answer_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    source_answer: Mapped["Answer"] = relationship("Answer")

    __table_args__ = (
        UniqueConstraint(
            "source_answer_id", "target_campaign_id", "target_question_key",
            name="uq_cross_survey_match"
        ),
    )


class AnswerFactCheck(Base):
    """Fact-check result for a single answer — was the claim verifiable and correct?"""

    __tablename__ = "answer_fact_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), unique=True, index=True, nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(
        Enum("true", "false", "uncertain", "not_checkable", name="fact_verdict"),
        nullable=False,
        default="not_checkable",
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(
        Enum("rule", "llm", name="factcheck_method"), default="rule", nullable=False
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    answer: Mapped["Answer"] = relationship("Answer")


class DemographicWeight(Base):
    """SAA-123/124: Weighting factor per demographic cell for bias correction."""

    __tablename__ = "demographic_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    demographic_key: Mapped[str] = mapped_column(String(64), nullable=False)
    demographic_value: Mapped[str] = mapped_column(String(128), nullable=False)
    target_percent: Mapped[float] = mapped_column(Float, nullable=False)
    actual_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "demographic_key", "demographic_value",
            name="uq_demographic_weight"
        ),
    )

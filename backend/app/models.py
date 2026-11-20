"""Database models. One conversation == one case."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), default="New consultation")
    status: Mapped[str] = mapped_column(String(32), default="intake")  # intake | ready | out_of_scope
    language: Mapped[str] = mapped_column(String(16), default="en")  # en | hi | hinglish

    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    purchase_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_paid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_communication: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    desired_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_information: Mapped[list | None] = mapped_column(JSON, nullable=True)

    guidance: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # last generated guidance
    # Fields the user edited by hand; automatic extraction never overwrites these.
    locked_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    messages: Mapped[list["Message"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="Message.id"
    )
    events: Mapped[list["CaseEvent"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="CaseEvent.position"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="Document.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)  # retrieved chunks cited in this reply
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # citation checks, scope, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    case: Mapped[Case] = relationship(back_populates="messages")


class CaseEvent(Base):
    """A timeline entry. `date` is kept as free text exactly as the user stated it (never normalised/invented)."""

    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(16), default="extracted")  # extracted | user
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    case: Mapped[Case] = relationship(back_populates="events")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="en")
    checks: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    case: Mapped[Case] = relationship(back_populates="documents")

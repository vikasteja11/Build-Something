"""
Database setup and ORM model for the reviewer-agent.

Uses the same 'tasks' table schema as the coder-agent so both
services can read/write the shared database.
"""

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://multiagent:multiagent@localhost:5432/multiagent",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class Task(Base):
    """Stores the full spec → code → review pipeline result."""

    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spec = Column(Text, nullable=False)
    generated_code = Column(Text, nullable=True)
    review_comments = Column(Text, nullable=True)
    verdict = Column(String(20), nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


def init_db():
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(bind=engine)

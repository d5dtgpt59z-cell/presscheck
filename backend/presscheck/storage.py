import os
from pathlib import Path
from sqlalchemy import create_engine, String, JSON, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from datetime import datetime, timezone

ROOT = Path(os.getenv("PRESSCHECK_DATA", "data")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    os.getenv("DATABASE_URL", f"sqlite:///{ROOT}/presscheck.db"),
    **(
        {"connect_args": {"check_same_thread": False}}
        if not os.getenv("DATABASE_URL")
        or os.getenv("DATABASE_URL", "").startswith("sqlite")
        else {}
    ),
)
Session = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    info: Mapped[dict] = mapped_column(JSON)


class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, default="queued", index=True)
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


Base.metadata.create_all(engine)

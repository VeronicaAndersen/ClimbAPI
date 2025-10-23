from datetime import datetime, date
from enum import Enum as PyEnum
from typing import List, Optional
from sqlalchemy import Enum as PgEnum, String
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    BigInteger,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    pass


class CompType(PyEnum):
    QUALIFIER = "QUALIFIER"
    FINAL = "FINAL"

class UserScope(str, PyEnum):
    climber = "climber"
    admin = "admin"


class Climber(Base):
    __tablename__ = "climber"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    password: Mapped[str] = mapped_column(Text, nullable=False)  # store a hash
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user_scope: Mapped[UserScope] = mapped_column(
        PgEnum(UserScope, name="user_scope_t", native_enum=True),
        nullable=False,
        default=UserScope.climber,
    )

    registrations: Mapped[List["Registration"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    problem_scores: Mapped[List["ProblemScore"]] = relationship(back_populates="user", cascade="all, delete-orphan")



class Season(Base):
    __tablename__ = "season"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    competitions: Mapped[List["Competition"]] = relationship(back_populates="season", cascade="all, delete-orphan")


class Competition(Base):
    __tablename__ = "competition"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    comp_type: Mapped[CompType] = mapped_column(Enum(CompType, name="comp_type"), nullable=False)
    comp_date: Mapped[date] = mapped_column(Date, nullable=False)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id", ondelete="CASCADE"), nullable=False)
    round_no: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    season: Mapped["Season"] = relationship(back_populates="competitions")
    registrations: Mapped[List["Registration"]] = relationship(back_populates="competition",
                                                               cascade="all, delete-orphan")
    problems: Mapped[List["Problem"]] = relationship(back_populates="competition", cascade="all, delete-orphan")
    problem_scores: Mapped[List["ProblemScore"]] = relationship(back_populates="competition",
                                                                cascade="all, delete-orphan")


class Registration(Base):
    __tablename__ = "registration"
    __table_args__ = (
        CheckConstraint("level BETWEEN 1 AND 8", name="level_range"),
        UniqueConstraint("comp_id", "user_id", name="registration_pk"),
        Index("reg_comp_level_idx", "comp_id", "level"),
        Index("reg_user_idx", "user_id"),
    )

    comp_id: Mapped[int] = mapped_column(ForeignKey("competition.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("climber.id", ondelete="CASCADE"), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    competition: Mapped["Competition"] = relationship(back_populates="registrations")
    user: Mapped["Climber"] = relationship(back_populates="registrations")


class Problem(Base):
    __tablename__ = "problem"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id", ondelete="CASCADE"), nullable=False)
    level_no: Mapped[int] = mapped_column(Integer, nullable=False)
    problem_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    competition: Mapped["Competition"] = relationship(back_populates="problems")
    scores: Mapped[List["ProblemScore"]] = relationship(back_populates="problem", cascade="all, delete-orphan")


class ProblemScore(Base):
    __tablename__ = "problem_score"

    # Composite PK (problem_id, user_id)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id", ondelete="CASCADE"), nullable=False)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("climber.id", ondelete="CASCADE"), primary_key=True)

    attempts_total: Mapped[int] = mapped_column(Integer, nullable=False)
    got_bonus: Mapped[bool] = mapped_column(Boolean, nullable=False)
    got_top: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempts_to_bonus: Mapped[Optional[int]] = mapped_column(Integer)
    attempts_to_top: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    competition: Mapped["Competition"] = relationship(back_populates="problem_scores")
    problem: Mapped["Problem"] = relationship(back_populates="scores")
    user: Mapped["Climber"] = relationship(back_populates="problem_scores")

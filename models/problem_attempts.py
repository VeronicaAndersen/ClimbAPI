from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class Problem(Base):
    __tablename__ = "problems"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    grade = Column(String(10), nullable=False)

    # Relationships
    attempts = relationship("ProblemAttempt", back_populates="problem")


class ProblemAttempt(Base):
    __tablename__ = "problem_attempts"

    id = Column(Integer, primary_key=True, index=True)
    attempts = Column(Integer, default=0)
    bonus = Column(Integer, default=0)
    top = Column(Integer, default=0)

    climber_id = Column(String(100), ForeignKey("climbers.id"), nullable=False)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    problem_id = Column(Integer, ForeignKey("problems.id"), nullable=False)

    # Relationships
    climber = relationship("Climber", back_populates="attempts")
    competition = relationship("Competition", back_populates="attempts")
    problem = relationship("Problem", back_populates="attempts")

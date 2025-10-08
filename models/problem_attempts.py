from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class ProblemAttempt(Base):
    __tablename__ = "problem_attempts"

    id = Column(Integer, primary_key=True, index=True)
    climber_id = Column(String(100), ForeignKey("climbers.id"), nullable=False)
    problem_id = Column(Integer, ForeignKey("problems.id"), nullable=False)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)

    attempts = Column(Integer, default=0)
    top = Column(Integer, default=0)
    bonus = Column(Integer, default=0)

    # Relationships
    climber = relationship("Climber", back_populates="attempts")
    problem = relationship("Problem", back_populates="attempts")
    competition = relationship("Competition", back_populates="attempts")

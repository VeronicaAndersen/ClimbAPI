from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class Problem(Base):
    __tablename__ = "problems"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)
    grade = Column(String(50), nullable=False)
    visible = Column(Boolean, default=True)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)

    attempts = relationship("ProblemAttempt", back_populates="problem")
    competition = relationship("Competition", back_populates="problems")

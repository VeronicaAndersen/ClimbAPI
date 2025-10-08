from sqlalchemy import Column, Integer, String, Date, Boolean
from sqlalchemy.orm import relationship
from database import Base

class Competition(Base):
    __tablename__ = "competitions"

    id = Column(Integer, primary_key=True)
    compname = Column(String(100), nullable=False)
    compdate = Column(Date, nullable=False)
    comppart = Column(Integer)
    visible = Column(Boolean, default=True)

    problems = relationship("Problem", back_populates="competition")
    participations = relationship("Participation", back_populates="competition")
    attempts = relationship("ProblemAttempt", back_populates="competition")


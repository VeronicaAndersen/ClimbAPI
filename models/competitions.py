from sqlalchemy import Column, Integer, String, Date, Boolean
from sqlalchemy.orm import relationship
from database import Base

class Competition(Base):
    __tablename__ = "competitions"

    id = Column(Integer, primary_key=True)
    compname = Column(String(100), nullable=False)
    compdate = Column(Date, nullable=False)
    visible = Column(Boolean, default=True)

    attempts = relationship("ProblemAttempt", back_populates="competition")


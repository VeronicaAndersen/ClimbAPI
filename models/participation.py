from sqlalchemy import Column, String, Integer, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class Participation(Base):
    __tablename__ = "participations"

    id = Column(Integer, primary_key=True, index=True)
    climber_id = Column(String(36), ForeignKey("climbers.id"), nullable=False)
    competition_id = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    grade = Column(String(50), nullable=False) 

    climber = relationship("Climber", back_populates="participations")
    competition = relationship("Competition", back_populates="participations")

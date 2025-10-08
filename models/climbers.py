from sqlalchemy import Column, String
from sqlalchemy.orm import relationship
from database import Base
from passlib.context import CryptContext
import uuid

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class Climber(Base):
    __tablename__ = "climbers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), nullable=False, unique=True)
    selected_grade = Column(String(20))
    password_hash = Column(String(255))

    # relationships
    attempts = relationship("ProblemAttempt", back_populates="climber")
    participations = relationship("Participation", back_populates="climber")

    # password helpers
    def set_password(self, password: str):
        """Hash and store a password safely."""
        self.password_hash = pwd_context.hash(password)

    def verify_password(self, password: str) -> bool:
        """Check password against stored hash."""
        return pwd_context.verify(password, self.password_hash)

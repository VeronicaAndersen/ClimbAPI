from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()  # load .env automatically

class Settings(BaseSettings):
    DATABASE_URL: str

    class Config:
        env_file = ".env"

settings = Settings()
engine = create_engine(settings.DATABASE_URL, echo=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# --- Dependency for FastAPI routes ---
def get_db():
    """Dependency for providing a database session to routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
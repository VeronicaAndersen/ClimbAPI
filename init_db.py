from database import Base, engine
from sqlalchemy import text

# ✅ Import all models to register them with Base.metadata
from models import climbers, competitions, participation, problems, problem_attempts

print("Dropping all tables...")

with engine.begin() as conn:  # `begin()` keeps same connection for all statements
    conn.execute(text("SET FOREIGN_KEY_CHECKS=0;"))

    # Explicitly drop all tables from metadata
    for table in reversed(Base.metadata.sorted_tables):
        print(f"Dropping table {table.name}...")
        conn.execute(text(f"DROP TABLE IF EXISTS {table.name};"))

    conn.execute(text("SET FOREIGN_KEY_CHECKS=1;"))

print("Done drop.\nCreating all tables...")

# ✅ Create tables normally
Base.metadata.create_all(bind=engine)

print("✅ Done create.")

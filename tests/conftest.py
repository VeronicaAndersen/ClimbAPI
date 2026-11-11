import os

# Use minimal Argon2 parameters so hashing tests run fast.
# These must be set before security.hashing is first imported.
os.environ["ARGON2_TIME_COST"] = "1"
os.environ["ARGON2_MEMORY_KIB"] = "8"
os.environ["ARGON2_PARALLELISM"] = "1"

# Required before schema.setting is imported. This is a test-only value.
os.environ["JWT_SECRET"] = "test-jwt-secret-at-least-32-bytes!!"

# Provide a dummy DB URL so db.config doesn't crash at import time when
# modules like security.deps pull it in transitively. No real connection
# is made in these unit tests.
os.environ.setdefault("ASYNC_DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/testdb")

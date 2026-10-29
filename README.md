# ClimbAPI

FastAPI backend for Greppmästerskapen. It uses PostgreSQL, SQLAlchemy, and dbmate migrations.

For the complete local setup, including starting the frontend, see the [workspace startup guide](../ClimbApp/README.md#start-both-applications-locally).

## Run the backend

Prerequisites: Python 3.10+, a running PostgreSQL server, and [dbmate](https://github.com/amacneil/dbmate) (on macOS: `brew install dbmate`). Create a local database, for example `grepp`, and configure a local `.env` file:

```dotenv
DATABASE_URL="postgresql://postgres:YOUR_PASSWORD@127.0.0.1:5432/grepp?sslmode=disable"
ASYNC_DATABASE_URL="postgresql+asyncpg://postgres:YOUR_PASSWORD@127.0.0.1:5432/grepp"
PASSWORD_PEPPER="replace-with-a-long-random-secret"
FRONTEND_URL="http://localhost:5173"
```

Use the username, password, and database name configured for your local PostgreSQL installation. Keep `.env` out of version control.

Create and activate a virtual environment, install dependencies, load the environment, and run migrations:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
set -a
source .env
set +a
dbmate up
```

Start the API in the same terminal so it inherits the environment variables:

```sh
python main.py
```

The API listens at `http://127.0.0.1:8080`. OpenAPI documentation is available at [http://127.0.0.1:8080/docs](http://127.0.0.1:8080/docs). The app's SQLite fallback is intended for simple local/test use; the documented database migration workflow uses PostgreSQL.

To run backend tests, install the development dependencies and execute pytest:

```sh
python -m pip install -r requirements-dev.txt
pytest -q
```

Mail settings are optional for local startup. Without configured mail credentials, password-reset links are printed for development.
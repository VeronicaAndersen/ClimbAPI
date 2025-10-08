from flask_cors import CORS
from flask import Flask
from flask_restx import Api
from extensions import db, migrate
from routes.climbers import climbers_ns
from routes.competitions import competitions_ns
from routes.problems import problems_ns
from routes.problem_attempts import problem_attempts_ns
from routes.grades import grades_ns
import os

DEV_ORIGINS = ["http://localhost:8080", "http://127.0.0.1:8080"]

authorizations = {
    "Bearer Auth": {
        "type": "apiKey",
        "in": "header",
        "name": "Authorization",
        "description": "Enter token like: **Bearer &lt;your_JWT_token&gt;**",
    }
}

def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv('SQLALCHEMY_DATABASE_URI')
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    CORS(
        app,
        resources={r"/*": {"origins": DEV_ORIGINS}},
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["Content-Type"],
    )

    db.init_app(app)
    migrate.init_app(app, db)

    api = Api(
        app,
        title="Grepp API",
        version="1.0",
        description="API for managing climbers, problems, and competitions",
        authorizations=authorizations,
        security="Bearer Auth",
        doc="/docs",
    )

    api.add_namespace(climbers_ns)
    api.add_namespace(competitions_ns)
    api.add_namespace(problems_ns)
    api.add_namespace(problem_attempts_ns)
    api.add_namespace(grades_ns)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)

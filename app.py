from functools import wraps
from flask import Flask, request, abort
from flask_sqlalchemy import SQLAlchemy
from flask_restx import Api, Resource, fields
from flask_cors import CORS  # Import CORS
from werkzeug.exceptions import BadRequest
import uuid
from dotenv import load_dotenv
import os
load_dotenv()

# --- APP & DB SETUP ---
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Load your secret token from env
SECRET_TOKEN = os.getenv("API_SECRET_TOKEN")

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

authorizations = {
    'Bearer': {
        'type': 'apiKey',
        'in': 'header',
        'name': 'Authorization',
        'description': 'Add token like: **Bearer &lt;your-token&gt;**'
    }
}
db = SQLAlchemy(app)
api = Api(
    app,
    version='1.0',
    title='Climber Data API',
    description='API for user registrations and climbing attempts',
    authorizations=authorizations,
    security='Bearer'  # applies to all endpoints unless overridden
)
ns = api.namespace('Climbers', description='Data submission operations')


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            abort(401, description="Missing Authorization Header")
        
        try:
            scheme, token = auth_header.split()
            if scheme.lower() != 'bearer' or token != SECRET_TOKEN:
                abort(401, description="Invalid or missing token")
        except ValueError:
            abort(401, description="Invalid Authorization format. Use 'Bearer <token>'.")

        return f(*args, **kwargs)
    return decorated


# --- DB MODELS ---
class Climber(db.Model):
    id = db.Column(db.String(100), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    selected_grade = db.Column(db.Integer, nullable=False)

    attempts = db.relationship('ProblemAttempt', backref='climber', lazy=True)

class ProblemAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    problem_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    attempts = db.Column(db.Integer)
    bonus_attempt = db.Column(db.Integer)
    top_attempt = db.Column(db.Integer)

    climber_id = db.Column(db.String(100), db.ForeignKey('climber.id'), nullable=False)

# --- SWAGGER MODELS ---
problem_attempt_model = api.model('ProblemAttempt', {
    'id': fields.Integer(required=True),
    'name': fields.String(required=True),
    'attempts': fields.Integer,
    'bonusAttempt': fields.Raw,
    'topAttempt': fields.Raw
})

climber_model = api.model('RegisteredClimber', {
    'id': fields.String(required=True),
    'name': fields.String(required=True),
    'email': fields.String(required=True),
    'date': fields.String(required=True),
    'selectedGrade': fields.Integer(required=True)
})

submission_model = api.model('ClimberPayload', {
    'registeredClimbers': fields.List(fields.Nested(climber_model), required=True),
    'problemAttempts': fields.Raw(required=True)
})

# --- ROUTES ---
@ns.route('/')
class Submission(Resource):
    @ns.expect(submission_model)
    @token_required
    def post(self):
        try:
            if not request.is_json:
                raise BadRequest("Invalid JSON payload")

            data = request.json
            if 'registeredClimbers' not in data or 'problemAttempts' not in data:
                raise BadRequest("Missing required fields")

            climbers = data['registeredClimbers']
            attempts = data['problemAttempts']

            for climber in climbers:
                climber_id = climber['id']
                db_climber = Climber(
                    id=climber_id,
                    name=climber['name'],
                    email=climber['email'],
                    date=climber['date'],
                    selected_grade=climber['selectedGrade']
                )
                db.session.add(db_climber)

                for attempt in attempts.get(climber_id, []):
                    db_attempt = ProblemAttempt(
                        problem_id=attempt['id'],
                        name=attempt['name'],
                        attempts=attempt.get('attempts'),
                        bonus_attempt=attempt.get('bonusAttempt'),
                        top_attempt=attempt.get('topAttempt'),
                        climber_id=climber_id
                    )
                    db.session.add(db_attempt)

            db.session.commit()
            return {"message": "Data saved to database"}, 201

        except BadRequest as e:
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            return {"error": "Unexpected error", "details": str(e)}, 500

    @token_required
    def get(self):
        """Fetch all stored climbers and attempts"""
        climber_list = []
        climbers = Climber.query.all()
        for climber in climbers:
            climber_dict = {
                "id": climber.id,
                "name": climber.name,
                "email": climber.email,
                "date": climber.date,
                "selectedGrade": climber.selected_grade,
                "attempts": [
                    {
                        "id": a.problem_id,
                        "name": a.name,
                        "attempts": a.attempts,
                        "bonusAttempt": a.bonus_attempt,
                        "topAttempt": a.top_attempt
                    } for a in climber.attempts
                ]
            }
            climber_list.append(climber_dict)
        return {"climbers": climber_list}, 200

# --- INIT DB ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)

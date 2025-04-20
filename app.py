from functools import wraps
from flask import Flask, request, abort
from flask_sqlalchemy import SQLAlchemy
from flask_restx import Api, Resource, fields
from flask_cors import CORS
from werkzeug.exceptions import BadRequest
import bcrypt  # Correct import for bcrypt
import jwt  # Correct import for JWT
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
user_ns = api.namespace('Users', description='User operations')


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            abort(401, description="Missing Authorization Header")
        
        try:
            scheme, token = auth_header.split()
            if scheme.lower() != 'bearer':
                abort(401, description="Invalid Authorization scheme")

            # Decode the token
            decoded_token = jwt.decode(token, SECRET_TOKEN, algorithms=["HS256"])
            request.user = decoded_token['username']  # Attach the username to the request

            # Fetch the user's role from the database
            user = User.query.filter_by(username=request.user).first()
            if not user:
                abort(401, description="Invalid token")
            request.user_role = user.role  # Attach the user's role to the request
        except (ValueError, jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            abort(401, description="Invalid or expired token")

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

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='climber')  # Default role is 'climber'

# --- SWAGGER MODELS ---
problem_attempt_model = api.model('ProblemAttempt', {
    'id': fields.Integer,
    'name': fields.String,
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
    'problemAttempts': fields.Raw
})

user_model = api.model('User', {
    'username': fields.String(required=True),
    'password': fields.String(required=True)
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

@ns.route('/<string:climber_id>/attempts')
class ClimberAttemptsResource(Resource):
    @ns.expect(api.model('UpdateAttemptsPayload', {
        'attempts': fields.List(fields.Nested(problem_attempt_model), required=True)
    }))
    @token_required
    def put(self, climber_id):
        """Update attempts for a specific climber"""
        try:
            if not request.is_json:
                raise BadRequest("Invalid JSON payload")

            data = request.json
            if 'attempts' not in data:
                raise BadRequest("Missing 'attempts' field in payload")

            attempts = data['attempts']

            # Fetch the climber to ensure they exist
            climber = Climber.query.get(climber_id)
            if not climber:
                return {"error": "Climber not found"}, 404

            # Update or create attempts
            for attempt in attempts:
                problem_id = attempt['id']
                db_attempt = ProblemAttempt.query.filter_by(
                    climber_id=climber_id, problem_id=problem_id
                ).first()

                if db_attempt:
                    # Update existing attempt
                    db_attempt.name = attempt.get('name', db_attempt.name)
                    db_attempt.attempts = attempt.get('attempts', db_attempt.attempts)
                    db_attempt.bonus_attempt = attempt.get('bonusAttempt', db_attempt.bonus_attempt)
                    db_attempt.top_attempt = attempt.get('topAttempt', db_attempt.top_attempt)
                else:
                    # Create new attempt if it doesn't exist
                    db_attempt = ProblemAttempt(
                        problem_id=problem_id,
                        name=attempt['name'],
                        attempts=attempt.get('attempts'),
                        bonus_attempt=attempt.get('bonusAttempt'),
                        top_attempt=attempt.get('topAttempt'),
                        climber_id=climber_id
                    )
                    db.session.add(db_attempt)

            # Commit changes
            db.session.commit()
            return {"message": "Attempts updated successfully"}, 200

        except BadRequest as e:
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            return {"error": "Unexpected error", "details": str(e)}, 500

@ns.route('/<string:climber_id>')
class ClimberResource(Resource):
    @token_required
    def get(self, climber_id):
        """Fetch a climber's data (admin or the climber themselves)"""
        climber = Climber.query.get(climber_id)
        if not climber:
            return {"error": "Climber not found"}, 404

        # Allow access if the user is an admin or the climber themselves
        if request.user_role != 'admin' and climber_id != request.user:
            return {"error": "Access forbidden: You can only view your own data"}, 403

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
        return climber_dict, 200

    @ns.expect(api.model('UpdateClimberPayload', {
        'name': fields.String,
        'email': fields.String,
        'date': fields.String,
        'selectedGrade': fields.Integer
    }))
    @token_required
    def put(self, climber_id):
        """Update a climber's data"""
        try:
            if not request.is_json:
                raise BadRequest("Invalid JSON payload")

            data = request.json

            # Fetch the climber by ID
            climber = Climber.query.get(climber_id)
            if not climber:
                return {"error": "Climber not found"}, 404

            # Update climber fields
            climber.name = data.get('name', climber.name)
            climber.email = data.get('email', climber.email)
            climber.date = data.get('date', climber.date)
            climber.selected_grade = data.get('selectedGrade', climber.selected_grade)

            # Commit changes
            db.session.commit()
            return {"message": "Climber updated successfully"}, 200

        except BadRequest as e:
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            return {"error": "Unexpected error", "details": str(e)}, 500

    @token_required
    def delete(self, climber_id):
        """Delete a climber and their associated attempts"""
        try:
            # Fetch the climber by ID
            climber = Climber.query.get(climber_id)
            if not climber:
                return {"error": "Climber not found"}, 404

            # Delete the climber and their associated attempts
            db.session.delete(climber)
            db.session.commit()
            return {"message": "Climber and associated attempts deleted successfully"}, 200

        except Exception as e:
            db.session.rollback()
            return {"error": "Unexpected error", "details": str(e)}, 500

@ns.route('/all-climbers')
class AllClimbers(Resource):
    @token_required
    def get(self):
        """Admin-only: Fetch all climbers"""
        if request.user_role != 'admin':
            return {"error": "Access forbidden: Admins only"}, 403

        climbers = Climber.query.all()
        climber_list = [
            {
                "id": climber.id,
                "name": climber.name,
                "email": climber.email,
                "date": climber.date,
                "selectedGrade": climber.selected_grade
            } for climber in climbers
        ]
        return {"climbers": climber_list}, 200

@user_ns.route('/register')
class UserRegister(Resource):
    @user_ns.expect(api.model('UserRegister', {
        'username': fields.String(required=True),
        'password': fields.String(required=True),
        'role': fields.String(default='climber', enum=['admin', 'climber'])  # Optional role field
    }))
    @token_required
    def post(self):
        """Register a new user (admin-only for creating admins)"""
        try:
            data = request.json
            username = data['username']
            password = data['password']
            role = data.get('role', 'climber')

            # Ensure only admins can create other admins
            if request.user_role != 'admin' and role == 'admin':
                return {"error": "Only admins can create admin users"}, 403

            # Check if the username already exists
            if User.query.filter_by(username=username).first():
                return {"error": "Username already exists"}, 400

            # Hash the password and create a new user
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            user = User(username=username, password=hashed_password.decode('utf-8'), role=role)
            db.session.add(user)
            db.session.commit()

            return {"message": "User registered successfully"}, 201

        except Exception as e:
            db.session.rollback()
            return {"error": "Unexpected error", "details": str(e)}, 500


@user_ns.route('/login')
class UserLogin(Resource):
    @user_ns.expect(user_model)
    def post(self):
        """Log in a user and return a token"""
        try:
            data = request.json
            username = data['username']
            password = data['password']

            # Fetch the user by username
            user = User.query.filter_by(username=username).first()
            if not user or not bcrypt.checkpw(password.encode('utf-8'), user.password.encode('utf-8')):
                return {"error": "Invalid username or password"}, 401

            # Generate a token with the user's role
            token = jwt.encode({"username": username, "role": user.role}, SECRET_TOKEN, algorithm="HS256")
            return {"token": token}, 200

        except Exception as e:
            return {"error": "Unexpected error", "details": str(e)}, 500

@user_ns.route('/all')
class GetAllUsers(Resource):
    @token_required
    def get(self):
        """Admin-only: Fetch all users"""
        if request.user_role != 'admin':
            return {"error": "Access forbidden: Admins only"}, 403

        users = User.query.all()
        user_list = [
            {
                "id": user.id,
                "username": user.username,
                "role": user.role
            } for user in users
        ]
        return {"users": user_list}, 200

# --- INIT DB ---
with app.app_context():
    db.create_all()

api.add_namespace(user_ns, path='/Users')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
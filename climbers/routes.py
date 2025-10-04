import uuid
from flask_restx import Namespace, Resource, fields
from flask import request
from grades.routes import GRADES
from werkzeug.exceptions import BadRequest
from extensions import db
from climbers.models import Climber, ProblemAttempt
from functools import wraps
import jwt
from werkzeug.exceptions import Unauthorized
import os

# Define the token_required decorator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            raise Unauthorized("Token is missing")
        try:
            decoded_token = jwt.decode(token, os.getenv('SECRET_KEY'), algorithms=["HS256"])
            current_user = decoded_token.get('user')
            if not current_user:
                raise Unauthorized("Invalid token")
        except jwt.ExpiredSignatureError:
            raise Unauthorized("Token has expired")
        except jwt.InvalidTokenError:
            raise Unauthorized("Invalid token")
        return f(current_user=current_user, *args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated_function(current_user, *args, **kwargs):
        # Check if the user has the 'admin' role
        if current_user.roles != 'Admin':
            return {"error": "Access denied. Admins only."}, 403
        print(current_user.roles)
        return f(current_user, *args, **kwargs)
    return decorated_function

climbers_ns = Namespace('Climbers', description='Climber operations')

# Swagger models
problem_attempt_model = climbers_ns.model('ProblemAttempt', {
    'name': fields.String(required=True, description="Name of the problem"),
    'attempts': fields.Integer(required=False, description="Number of attempts"),
    'bonusAttempt': fields.Integer(required=False, description="Number of bonus attempts"),
    'topAttempt': fields.Integer(required=False, description="Number of top attempts")
})

climber_registration_model = climbers_ns.model('ClimberRegistration', {
    'name': fields.String(required=True, description="Name of the climber"),
    'password': fields.String(required=True, description="Password for the climber"),
    'roles': fields.String(required=True, description="Role of the user (e.g., 'Climber', 'Admin')"),
    'grade': fields.String(required=True, description=f"Grade of the climber (e.g., {', '.join(GRADES)})")
})

climber_login_model = climbers_ns.model('ClimberLogin', {
    'name': fields.String(required=True, description="Name of the climber"),
    'password': fields.String(required=True, description="Password for the climber")
})

climber_update_model = climbers_ns.model('ClimberUpdate', {
    'grade': fields.String(required=True, description=f"Updated grade of the climber (e.g., {', '.join(GRADES)})"),
    'problemAttempts': fields.List(fields.Nested(problem_attempt_model), required=True, description="List of problem attempts")
})

# Routes
@climbers_ns.route('/Register')
class ClimberRegistration(Resource):
    @climbers_ns.expect(climber_registration_model)
    @climbers_ns.response(201, "Climber registered successfully", model=climber_registration_model)
    @climbers_ns.response(400, "Invalid input or climber already exists")
    def post(self):
        """Register a new climber"""
        try:
            if not request.is_json:
                raise BadRequest("Invalid JSON payload")

            data = request.json
            id = str(uuid.uuid4())
            name = data.get('name')
            password = data.get('password')
            roles = data.get('roles')
            grade = data.get('grade')

            # Validate role
            valid_roles = {'Climber', 'Admin'}
            if roles not in valid_roles:
                raise BadRequest(f"Invalid role. Valid roles are: {', '.join(valid_roles)}")

            # Validate grade
            if grade not in GRADES:
                raise BadRequest(f"Invalid grade. Valid grades are: {', '.join(GRADES)}")

            # Check if the climber already exists
            if Climber.query.filter_by(name=name).first():
                return {"error": "Climber with this name already exists"}, 400

            # Create a new climber
            new_climber = Climber(
                id = id,
                name=name,
                password='',  # Will be set using set_password
                roles=roles,
                selected_grade=grade
            )
            new_climber.set_password(password)  # Hash and set the password
            db.session.add(new_climber)
            db.session.commit()

            return {
                "message": "Climber registered successfully",
                "climber": {
                    "id": str(new_climber.id),   
                    "name": new_climber.name,
                    "roles": roles,
                    "grade": new_climber.selected_grade
                }
}, 201

        except BadRequest as e:
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            return {"error": "Unexpected error", "details": str(e)}, 500

@climbers_ns.route('/Login')
class ClimberLogin(Resource):
    @climbers_ns.expect(climber_login_model)
    @climbers_ns.response(200, "Login successful")
    @climbers_ns.response(401, "Invalid name or password")
    def post(self):
        """Login a climber"""
        try:
            if not request.is_json:
                raise BadRequest("Invalid JSON payload")

            data = request.json
            name = data.get('name')
            password = data.get('password')

            # Check if the climber exists
            climber = Climber.query.filter_by(name=name).first()
            id = str(climber.id)
            if not climber:
                return {"error": "Invalid name or password"}, 401

            # Verify the password
            if not climber.check_password(password):
                return {"error": "Invalid name or password"}, 401

            # Generate a JWT token
            secret_key = os.getenv('SECRET_KEY', 'default_secret')
            token = jwt.encode({'name': climber.name}, secret_key, algorithm='HS256')

            return {"message": "Login successful","id": id, "name": name, "token": token}, 200

        except BadRequest as e:
            return {"error": str(e)}, 400
        except Exception as e:
            return {"error": "Unexpected error", "details": str(e)}, 500

@climbers_ns.route('/Update')
class ClimberUpdate(Resource):
    @climbers_ns.expect(climber_update_model)
    @climbers_ns.response(200, "Climber's grade and problem attempts updated successfully")
    @climbers_ns.response(400, "Invalid input")
    @climbers_ns.response(401, "Unauthorized")
    @token_required
    def put(self, current_user):
        """Update the logged-in climber's grade and problem attempts"""
        try:
            if not request.is_json:
                raise BadRequest("Invalid JSON payload")

            data = request.json
            grade = data.get('grade')
            problem_attempts = data.get('problemAttempts')

            # Validate grade
            if grade not in GRADES:
                raise BadRequest(f"Invalid grade. Valid grades are: {', '.join(GRADES)}")

            # Update the climber's grade
            current_user.selected_grade = grade

            # Update or create problem attempts
            for attempt in problem_attempts:
                problem_id = attempt.get('id')
                db_attempt = ProblemAttempt.query.filter_by(climber_id=current_user.id, problem_id=problem_id).first()

                if db_attempt:
                    # Update existing attempt
                    db_attempt.attempts = attempt.get('attempts', db_attempt.attempts)
                    db_attempt.bonus_attempt = attempt.get('bonusAttempt', db_attempt.bonus_attempt)
                    db_attempt.top_attempt = attempt.get('topAttempt', db_attempt.top_attempt)
                else:
                    # Create a new attempt if it doesn't exist
                    new_attempt = ProblemAttempt(
                        problem_id=problem_id,
                        name=attempt.get('name'),
                        attempts=attempt.get('attempts'),
                        bonus_attempt=attempt.get('bonusAttempt'),
                        top_attempt=attempt.get('topAttempt'),
                        climber_id=current_user.id
                    )
                    db.session.add(new_attempt)

            db.session.commit()
            return {
                "message": "Climber's grade and problem attempts updated successfully",
                "climber": {
                    "name": current_user.name,
                    "grade": current_user.selected_grade,
                    "problemAttempts": [
                        {
                            "id": str(attempt.problem_id),
                            "name": attempt.name,
                            "attempts": attempt.attempts,
                            "bonusAttempt": attempt.bonus_attempt,
                            "topAttempt": attempt.top_attempt
                        }
                        for attempt in current_user.attempts
                    ]
                }
            }, 200

        except BadRequest as e:
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            return {"error": "Unexpected error", "details": str(e)}, 500

@climbers_ns.route('/all')
class GetAllClimbers(Resource):
    # @token_required
    # @admin_required
    @climbers_ns.response(200, "List of all climbers retrieved successfully")
    # @climbers_ns.response(403, "Access denied. Admins only.")
    def get(self):
        """Get all climbers (Admins only)"""
        try:
            climbers = Climber.query.all()
            climber_list = [
                {
                    "id": str(climber.id),
                    "name": climber.name,
                    "selected_grade": climber.selected_grade,
                    "problemAttempts": [
                        {
                            "id": str(attempt.problem_id),
                            "name": attempt.name,
                            "attempts": attempt.attempts,
                            "bonusAttempt": attempt.bonus_attempt,
                            "topAttempt": attempt.top_attempt
                        }
                        for attempt in climber.attempts
                    ]
                }
                for climber in climbers
            ]
            return {"climbers": climber_list}, 200

        except Exception as e:
            return {"error": "Unexpected error", "details": str(e)}, 500

@climbers_ns.route('/Climber/<string:climber_id>')
class GetClimberByID(Resource):
    @climbers_ns.response(200, "Climber details retrieved successfully")
    @climbers_ns.response(404, "Climber not found")
    def get(self, climber_id):
        """Get climber details by ID"""
        try:
            climber = Climber.query.filter_by(id=climber_id).first()

            if not climber:
                return {"error": "Climber not found"}, 404

            climber_data = {
                "id": str(climber.id),
                "name": climber.name,
                "selected_grade": climber.selected_grade,
                "problemAttempts": [
                    {
                        "id": str(attempt.problem_id),
                        "name": attempt.name,
                        "attempts": attempt.attempts,
                        "bonusAttempt": attempt.bonus_attempt,
                        "topAttempt": attempt.top_attempt
                    }
                    for attempt in climber.attempts
    ]
}
            return {"climber": climber_data}, 200

        except Exception as e:
            return {"error": "Unexpected error", "details": str(e)}, 500
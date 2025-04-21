from flask_restx import Namespace, Resource, fields
from flask import request
from werkzeug.exceptions import BadRequest
from extensions import db
from competitions.models import Competition
from functools import wraps
import os

competitions_ns = Namespace('Competitions', description='Operations related to competitions')

# Swagger model for competition creation
competition_model = competitions_ns.model('Competition', {
    'compname': fields.String(required=True, description="Name of the competition"),
    'compdate': fields.String(required=True, description="Date of the competition (e.g., YYYY-MM-DD)"),
    'comppart': fields.Integer(required=True, description="Number of participants")
})

competition_visibility_model = competitions_ns.model('CompetitionVisibility', {
    'visible': fields.Boolean(required=True, description="Set competition visibility (true for visible, false for hidden)")
})

# Admin-only decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return {"error": "Access denied. No token provided."}, 403

        try:
            # Decode the token (replace 'your_secret_key' with your actual secret key)
            import jwt
            decoded_token = jwt.decode(token, "your_secret_key", algorithms=["HS256"])
            roles = decoded_token.get("roles", [])
            if "admin" not in roles:
                return {"error": "Access denied. Admins only."}, 403
        except jwt.ExpiredSignatureError:
            return {"error": "Access denied. Token has expired."}, 403
        except jwt.InvalidTokenError:
            return {"error": "Access denied. Invalid token."}, 403

        return f(*args, **kwargs)
    return decorated_function

@competitions_ns.route('/create')
class CreateCompetition(Resource):
    @competitions_ns.expect(competition_model)
    @competitions_ns.response(201, "Competition created successfully")
    @competitions_ns.response(400, "Invalid input")
    @competitions_ns.response(403, "Access denied. Admins only.")
    # @admin_required
    def post(self):
        """Create a new competition (Admins only)"""
        try:
            if not request.is_json:
                raise BadRequest("Invalid JSON payload")

            data = request.json
            compname = data.get('compname')
            compdate = data.get('compdate')
            comppart = data.get('comppart')

            # Validate input
            if not compname or not compdate or not isinstance(comppart, int):
                raise BadRequest("Invalid input. Ensure all fields are provided and valid.")

            # Create a new competition
            new_competition = Competition(compname=compname, compdate=compdate, comppart=comppart)
            db.session.add(new_competition)
            db.session.commit()

            return {"message": "Competition created successfully", "competition": {
                "id": new_competition.id,
                "compname": new_competition.compname,
                "compdate": new_competition.compdate,
                "comppart": new_competition.comppart
            }}, 201

        except BadRequest as e:
            return {"error": str(e)}, 400
        except Exception as e:
            db.session.rollback()
            return {"error": "Unexpected error", "details": str(e)}, 500

@competitions_ns.route('/<int:competition_id>/visibility')
class UpdateCompetitionVisibility(Resource):
    @competitions_ns.expect(competition_visibility_model)
    @competitions_ns.response(200, "Competition visibility updated successfully")
    @competitions_ns.response(404, "Competition not found")
    @competitions_ns.response(403, "Access denied. Admins only.")
    @admin_required
    def patch(self, competition_id):
        """Update the visibility of a competition (Admins only)"""
        try:
            data = request.json
            visible = data.get('visible')

            # Find the competition
            competition = Competition.query.get(competition_id)
            if not competition:
                return {"error": "Competition not found"}, 404

            # Update visibility
            competition.visible = visible
            db.session.commit()

            return {"message": "Competition visibility updated successfully", "competition": {
                "id": competition.id,
                "compname": competition.compname,
                "compdate": competition.compdate,
                "comppart": competition.comppart,
                "visible": competition.visible
            }}, 200

        except Exception as e:
            db.session.rollback()
            return {"error": "Unexpected error", "details": str(e)}, 500

@competitions_ns.route('/visible')
class GetVisibleCompetitions(Resource):
    @competitions_ns.response(200, "List of visible competitions retrieved successfully")
    def get(self):
        """Get all visible competitions"""
        try:
            competitions = Competition.query.filter_by(visible=True).all()
            competition_list = [
                {
                    "id": competition.id,
                    "compname": competition.compname,
                    "compdate": competition.compdate,
                    "comppart": competition.comppart,
                    "visible": competition.visible
                }
                for competition in competitions
            ]
            return {"competitions": competition_list}, 200

        except Exception as e:
            return {"error": "Unexpected error", "details": str(e)}, 500
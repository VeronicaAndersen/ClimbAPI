from flask_restx import Namespace, Resource

grades_ns = Namespace('Grades', description='Operations related to climbing grades')

# List of grades
GRADES = ["Lila", "Rosa", "Orange", "Gul", "Grön", "Vit", "Svart"]

@grades_ns.route('/')
class GradeList(Resource):
    def get(self):
        """Get the list of climbing grades"""
        return {"grades": GRADES}, 200
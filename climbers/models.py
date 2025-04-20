import uuid
from extensions import db
import bcrypt
from grades.routes import GRADES


class Climber(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False)
    selected_grade = db.Column(db.String(20), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='climber') 

    attempts = db.relationship('ProblemAttempt', backref='climber', lazy=True)

    def set_password(self, raw_password):
        """Hash and set the password."""
        self.password = bcrypt.hashpw(raw_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def check_password(self, raw_password):
        """Check if the provided password matches the stored hash."""
        return bcrypt.checkpw(raw_password.encode('utf-8'), self.password.encode('utf-8'))

    def set_grade(self, grade):
        """Set the climber's grade if it is valid."""
        if grade not in GRADES:
            raise ValueError(f"Invalid grade. Valid grades are: {', '.join(GRADES)}")
        self.selected_grade = grade


class ProblemAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    problem_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    attempts = db.Column(db.Integer, nullable=True)
    bonus_attempt = db.Column(db.Integer, nullable=True)
    top_attempt = db.Column(db.Integer, nullable=True)

    # Foreign key to associate with a climber
    climber_id = db.Column(db.String(36), db.ForeignKey('climber.id'), nullable=False)
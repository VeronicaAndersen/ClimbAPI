from extensions import db

class Competition(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)  # Unique ID
    compname = db.Column(db.String(100), nullable=False)
    compdate = db.Column(db.String(20), nullable=False)
    comppart = db.Column(db.Integer, nullable=False)  # Number of participants
    visible = db.Column(db.Boolean, nullable=False, default=True)  # Visibility flag
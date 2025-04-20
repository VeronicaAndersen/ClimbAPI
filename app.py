from climbers.models import Climber
from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate
from extensions import db, api
from climbers.routes import climbers_ns
from grades.routes import grades_ns  # Import the grades namespace
import os
from dotenv import load_dotenv

load_dotenv()

# --- APP SETUP ---
app = Flask(__name__, instance_relative_config=True)  # Enable instance folder
CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)  # Initialize Flask-Migrate
api.init_app(app)

# Register namespaces
api.add_namespace(climbers_ns, path='/Climbers')
api.add_namespace(grades_ns, path='/Grades')  # Register the grades namespace

# --- INIT DB ---
with app.app_context():
    db.create_all()  # Create database tables
    # db.drop_all()  # Uncomment this line to drop all tables (use with caution)

    # Add default climbers
    default_climbers = [
        Climber(name='v', role="admin", selected_grade="Orange", password='v'),
    ]

    for climber in default_climbers:
        # Hash the password before saving
        climber.set_password(climber.password)
        db.session.add(climber)

    db.session.commit()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=True, host='0.0.0.0', port=port)
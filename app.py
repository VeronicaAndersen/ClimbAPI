from climbers.models import Climber
from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate
from extensions import db, api
from climbers.routes import climbers_ns
from grades.routes import grades_ns  # Import the grades namespace
from competitions.routes import competitions_ns  # Import the competitions namespace
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
api.add_namespace(grades_ns, path='/Grades')
api.add_namespace(competitions_ns, path='/Competitions')  # Register the competitions namespace

# --- INIT DB ---
with app.app_context():
    # db.drop_all()  # Uncomment this line to drop all tables (use with caution)
    db.create_all()  # Create database tables

    # Add climber with admin role
    # default_climber = Climber(name='v', selected_grade='Gul', roles='admin')
    # default_climber.set_password('v')
    # db.session.add(default_climber)

    db.session.commit()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=True, host='0.0.0.0', port=port)
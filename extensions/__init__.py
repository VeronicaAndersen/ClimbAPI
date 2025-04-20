from flask_sqlalchemy import SQLAlchemy
from flask_restx import Api

db = SQLAlchemy()

# Add security definitions for Swagger
authorizations = {
    'Bearer': {
        'type': 'apiKey',
        'in': 'header',
        'name': 'Authorization',
        'description': 'Enter your Bearer token in the format **Bearer &lt;token&gt;**'
    }
}

api = Api(
    title='ClimbAPI',
    version='1.0',
    description='API for managing climbers ',
    authorizations=authorizations,
    security='Bearer'  # Apply Bearer token globally
)
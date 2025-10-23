# Climb API Backend service
The service consist of a fastapi app and a postgresql database.

## Get started
Before you do anything, make sure to create a new venv and activate it. 
Then run 
```
pip install -r requirements.txt
```
to install the required packages. 

### Database and environment variables
For testing and playing around, create a local database.
Then create a .env file and add the following variables:
```
LOCAL = "postgres@127.0.0.1:5432/<name-of-ypur-db>"

# DB Connection
DATABASE_URL="postgresql://${LOCAL}?sslmode=disable" # <- dbmate will look for this one
ASYNC_DATABASE_URL="postgresql+asyncpg://${LOCAL}" # <- api will look for this one

PASSWORD_PEPPER="super-secret-password" # <- This is for salting the passwords

```
The pepper-password can be anything when you're testing locally. For the production password, ask your local dealer. 

The database migrations are run by dbmate. To get the status of your current db run:
```
dbmate status
```
and apply all non-applied changes with 
```
dbmate up
```
if you have an empty database this will create all tables etc for you.
More on dbmate commands here: https://github.com/amacneil/dbmate

### Starting the api
- Make sure your venv is active.
- Make sure the database is up and running.
- Then just run the main file:
```
python main.py
```

The swagger docs should be visible at http://127.0.0.1:8000/docs
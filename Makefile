# Load .env file if it exists and export all variables to the shell.
# This lets targets like 'revision' and 'db-shell' use POSTGRES_USER etc.
# Note: values in .env must NOT be quoted (use POSTGRES_USER=clickpost not POSTGRES_USER="clickpost")
-include .env
export

.PHONY: up up-d down build migrate revision logs shell db-shell

# use this only when we want to build complete app + db , it will bild app image + start app + starts db and terminal
# will be blocked state 
up:
	docker compose up --build

# its same as above up , but the terminal will be in detached state
up-d:
	docker compose up --build -d

# stop all containers. DB data volume is preserved , no data loss.
# use this when we're done for the day or want a clean restart.
down:
	docker compose down

# use this when we have changed some app code and dont want to restart db
build:
	docker compose build app

# Apply all pending Alembic migrations to the database.
# Run this after pulling new code that contains new migration files,
# or after writing a new migration yourself (make revision name="...").
migrate:
	docker compose run --rm app alembic upgrade head

# generate a new migration file by comparing models to the current DB schema.
# requires db container to be running (make up-d first).
# uses localhost because this runs on your machine, not inside Docker.
# usage: make revision name="add users table"
revision:
	DATABASE_URL=postgresql+asyncpg://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@localhost:5432/$${POSTGRES_DB} \
		alembic revision --autogenerate -m "$(name)"

# follow live app logs. Useful when running in background (make up-d).
# Ctrl+C exits log view but does NOT stop the containers.
logs:
	docker compose logs -f app

# open a bash shell inside the running app container.
# use this to run one-off Python scripts, inspect files, or debug inside the container.
shell:
	docker compose exec app bash

# open a psql shell inside the running db container.
# use this to inspect tables, run raw SQL queries, or verify migrations applied correctly.
db-shell:
	docker compose exec db psql -U $${POSTGRES_USER} -d $${POSTGRES_DB}

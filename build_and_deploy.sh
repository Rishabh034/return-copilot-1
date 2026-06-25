# echo Stopping all running containers
# sudo docker stop $(sudo docker ps -q)

# echo Cleaning dangling resources
# sudo docker system prune -f

# echo Building docker image
# sudo docker build -t ai-agent-recom .

# echo Running docker container in background
# sudo docker run --env-file .env -p 8000:8000 -d ai-agent-recom


#!/bin/bash
set -e   # stop immediately if any command fails

echo "=== Pulling latest code ==="
git pull

echo "=== Building new image ==="
sudo docker compose build app

# alembic is used as migration tool and run will start a fresh container as new image and 
# --rm will remove it immediately after it exists just to test if there are any errors in migration files if there are
# any error our script will exit and old container will keep on serving traffic, else we continue to next steps.
echo "=== Running migrations ==="
sudo docker compose run --rm app alembic upgrade head

# here we are making our app service only Up , not postgres
echo "=== Deploying app ==="
sudo docker compose up -d --no-deps app

echo "=== Cleaning dangling images ==="
sudo docker system prune -f

echo "=== Done ==="
sudo docker compose ps

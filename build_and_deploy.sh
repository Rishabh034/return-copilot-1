echo Stopping all running containers
sudo docker stop $(sudo docker ps -q)

echo Cleaning dangling resources
sudo docker system prune -f

echo Building docker image
sudo docker build -t ai-agent-recom .

echo Running docker container in background
sudo docker run --env-file .env -p 8000:8000 -d ai-agent-recom
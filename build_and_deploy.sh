#!/bin/bash
set -e   # stop immediately if any command fails

# Only master is deployed. `git pull` deploys whatever is checked out, so a
# server left on a feature branch would silently ship that branch.
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" != "master" ]; then
    echo "Refusing to deploy: checked out on '$BRANCH', expected 'master'."
    exit 1
fi

# The commit currently live, so a rollback knows where to go back to.
PREV_SHA=$(git rev-parse HEAD)

echo "=== Pulling latest code ==="
git pull

# Image compose builds for the app service (e.g. clickpost-ai-projects-app).
APP_IMAGE=$(sudo docker compose config --images | grep -- '-app$' | head -1)
if [ -z "$APP_IMAGE" ]; then
    echo "Could not work out the app image name from docker compose config."
    exit 1
fi

# Label the image that is serving traffic right now as :previous before the
# build takes over its name. Without this, `docker system prune` at the end
# deletes it and there is nothing to roll back to.
echo "=== Keeping current image as $APP_IMAGE:previous ==="
if sudo docker image inspect "$APP_IMAGE" >/dev/null 2>&1; then
    sudo docker tag "$APP_IMAGE" "$APP_IMAGE:previous"
else
    echo "No existing image (first deploy?) - nothing to keep."
fi

echo "=== Building new image ==="
sudo docker compose build app

# Back up the database only when this deploy brings a migration. Revision ids
# are read from the NEW image, so HEAD is what `alembic upgrade head` will
# apply. If either id cannot be read, back up anyway rather than guess.
CURRENT=$(sudo docker compose run --rm --no-deps app alembic current 2>/dev/null | grep -oE '^[0-9a-f]{12}' | head -1 || true)
HEAD=$(sudo docker compose run --rm --no-deps app alembic heads 2>/dev/null | grep -oE '^[0-9a-f]{12}' | head -1 || true)

if [ -z "$CURRENT" ] || [ -z "$HEAD" ] || [ "$CURRENT" != "$HEAD" ]; then
    echo "=== Migration pending (${CURRENT:-unknown} -> ${HEAD:-unknown}): backing up database ==="
    mkdir -p ~/backups
    BACKUP_FILE=~/backups/pre_${HEAD:-unknown}_$(date +%F_%H%M).dump
    # The redirect runs as the deploying user on purpose, so the dump lands in
    # their ~/backups owned by them rather than by root.
    # shellcheck disable=SC2024
    sudo docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$BACKUP_FILE"
    if [ ! -s "$BACKUP_FILE" ]; then
        echo "Backup is empty - stopping before any migration runs."
        exit 1
    fi
    echo "Backup written: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
    # keep the latest 10 dumps
    ls -t ~/backups/*.dump | tail -n +11 | xargs -r rm --
else
    echo "=== No pending migrations ($CURRENT), skipping backup ==="
fi

# alembic is used as migration tool and run will start a fresh container as new image and
# --rm will remove it immediately after it exists just to test if there are any errors in migration files if there are
# any error our script will exit and old container will keep on serving traffic, else we continue to next steps.
# --no-deps: never recreate/restart the db container as part of a deploy;
# it must already be running (first-time setup: sudo docker compose up -d db)
echo "=== Running migrations ==="
sudo docker compose run --rm --no-deps app alembic upgrade head

# here we are making our app service only Up , not postgres
echo "=== Deploying app ==="
sudo docker compose up -d --no-deps app

# `up -d` returns as soon as the container starts, even if the app then
# crashes on boot. Wait for /health before calling the deploy good. This does
# not roll back by itself: a person should decide, since the database may
# already be migrated.
echo "=== Waiting for /health ==="
HEALTHY=false
for _ in $(seq 1 60); do
    # Match the body, not just the HTTP code: the response envelope answers 200
    # on the wire even for errors and carries the real status in meta.status.
    if curl -s http://127.0.0.1:8000/health 2>/dev/null | grep -q '"status": "ok"'; then
        HEALTHY=true
        break
    fi
    sleep 1
done

if [ "$HEALTHY" != "true" ]; then
    echo ""
    echo "!!! HEALTH CHECK FAILED - the new app did not answer /health within 60s."
    echo "!!! Logs:      sudo docker compose logs --tail 100 app"
    echo "!!! Roll back the code (database stays as it is):"
    echo "      git checkout $PREV_SHA"
    echo "      sudo docker tag $APP_IMAGE:previous $APP_IMAGE"
    echo "      sudo docker compose up -d --no-deps --force-recreate app"
    exit 1
fi
echo "App is healthy."

# Only after the new app is confirmed healthy. :previous is tagged, so it is
# not dangling and survives this.
echo "=== Cleaning dangling images ==="
sudo docker system prune -f

echo "=== Done ==="
echo "Deployed $(git rev-parse --short HEAD) (previous: ${PREV_SHA:0:7}). To roll back:"
echo "      git checkout $PREV_SHA"
echo "      sudo docker tag $APP_IMAGE:previous $APP_IMAGE"
echo "      sudo docker compose up -d --no-deps --force-recreate app"
sudo docker compose ps

#!/bin/bash
set -e

echo "=== Starting deployment ==="

cd /root

# Pull latest changes
echo "Pulling latest changes from git..."
git pull origin main

# Activate virtual environment
source whisper_env/bin/activate

# Install/update dependencies
echo "Installing dependencies..."
if [ -f requirements.txt ]; then
    pip install -r requirements.txt --quiet --upgrade
fi

# Run migrations
echo "Running database migrations..."
python manage.py migrate --noinput

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Restart services
echo "Restarting services..."
systemctl restart whisper-transcribe.service || true
systemctl reload nginx || true

echo "=== Deployment completed successfully ==="

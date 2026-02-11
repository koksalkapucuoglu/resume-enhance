#!/bin/bash

# Stop on any error
set -e

echo "🚀 Starting deployment..."

# Pull the latest code
echo "📦 Pulling latest code from git..."
git pull origin main

# Build and start containers
echo "🐳 Building and starting containers..."
docker compose -f docker-compose.prod.yml up -d --build

# Run migrations
echo "🗄️ Running database migrations..."
docker compose -f docker-compose.prod.yml exec web python manage.py migrate

# Collect static files
echo "🎨 Collecting static files..."
docker compose -f docker-compose.prod.yml exec web python manage.py collectstatic --noinput

echo "✅ Deployment completed successfully!"

DOCKER_COMPOSE := $(shell docker compose version > /dev/null 2>&1 && echo 'docker compose' || echo 'docker-compose')

build:
	$(DOCKER_COMPOSE) build

run:
	$(DOCKER_COMPOSE) up

down:
	$(DOCKER_COMPOSE) down

makemigrations:
	$(DOCKER_COMPOSE) run web python manage.py makemigrations

migrate:
	$(DOCKER_COMPOSE) run web python manage.py migrate

user:
	$(DOCKER_COMPOSE) run web python manage.py createsuperuser

build_force:
	$(DOCKER_COMPOSE) build --no-cache

run_command:
	$(DOCKER_COMPOSE) run web python manage.py $(command)

recreate:
	$(DOCKER_COMPOSE) up -d --force-recreate web

logs:
	$(DOCKER_COMPOSE) logs -f enhance_resume_app
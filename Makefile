.PHONY: up upd down stop build logs worker test lint format bash init-migrations migrate exec-migration layers-plan layers-urls load-national-layers layers-size load-layers stripe-listen

up:              ## Start app + worker + db + redis (foreground)
	docker compose up

upd:             ## Start in detached mode
	docker compose up -d

down:            ## Stop and remove containers
	docker compose down

stop:            ## Stop without removing
	docker compose stop

build:           ## Rebuild images
	docker compose build

logs:            ## Tail app + worker logs
	docker compose logs -f app worker

worker:          ## Run the Celery worker in the foreground
	docker compose run --rm worker

test:            ## Run tests
	docker compose run --rm app uv run pytest

lint:            ## Ruff check + autofix
	docker compose run --rm app uv run ruff check --fix .

format:          ## Ruff format
	docker compose run --rm app uv run ruff format .

bash:            ## Shell into the app container
	docker compose exec app bash

init-migrations: ## Alembic init (first time only; this repo already ships alembic/)
	docker compose run --rm app uv run alembic init -t async alembic

migrate:         ## Create a migration: make migrate msg="add x"
	docker compose run --rm app uv run alembic revision --autogenerate -m "$(msg)"

exec-migration:  ## Apply pending migrations
	docker compose run --rm app uv run alembic upgrade head

layers-plan:     ## What would be downloaded and how big, without downloading anything
	docker compose run --rm app uv run python scripts/fetch_layers.py --plan

layers-urls:     ## The 6 URLs to open in a browser (MITECO gates them behind a captcha)
	docker compose run --rm app uv run python scripts/fetch_layers.py --urls

load-national-layers: ## Load the zips already downloaded into data/
	docker compose run --rm app uv run python scripts/fetch_layers.py --skip-download

layers-size:     ## How much the loaded layers actually take in PostGIS
	docker compose exec db psql -U $${POSTGRES_USER:-postgres} -d $${POSTGRES_DB:-informefinca} \
	  -c "SELECT layer_code, count(*) AS features, pg_size_pretty(sum(pg_column_size(geom))) AS geoms FROM layer_features GROUP BY 1 ORDER BY sum(pg_column_size(geom)) DESC;" \
	  -c "SELECT pg_size_pretty(pg_total_relation_size('layer_features')) AS table_with_indexes;"

load-layers:     ## Import a manually downloaded layer (ZFP, montes UP) with ogr2ogr
	docker compose run --rm app bash scripts/load_layers.sh

stripe-listen:   ## Forward Stripe events to the local webhook (needs the stripe CLI on the host)
	stripe listen --forward-to localhost:8000/api/v1/payments/stripe/webhook

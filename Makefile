.PHONY: dev up down logs build

# Development with docker-compose
up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

# Backend only (for local dev)
backend-dev:
	cd backend && uvicorn app.main:app --reload --port 8000

# Frontend only (for local dev)
frontend-dev:
	cd frontend && npm run dev

# Run both locally
dev:
	@echo "Start backend: make backend-dev"
	@echo "Start frontend: make frontend-dev"
	@echo "Or use docker: make up"

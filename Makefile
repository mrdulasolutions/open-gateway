.PHONY: test build ui serve serve-lan check install package docker docker-up sync-static

install:
	uv sync --all-extras
	cd webapp && bun install

test:
	uv run pytest tests/ -q

# Vite production build → webapp/dist, then ship into the Python package
ui:
	cd webapp && bun run build
	$(MAKE) sync-static

sync-static:
	rm -rf src/opengateway/static
	mkdir -p src/opengateway/static
	cp -R webapp/dist/. src/opengateway/static/
	@test -f src/opengateway/static/index.html
	@echo "OK — UI synced to src/opengateway/static/"

build: test ui

check: build
	@echo "OK — tests green, webapp/dist + package static built"

# Wheel + sdist for uv tool install / offline
package: check
	rm -rf dist
	uv build -o dist
	@ls -la dist/
	@echo "Install:  uv tool install dist/opengateway-*.whl"

docker:
	docker build -t opengateway:0.0.5 -t opengateway:latest .

docker-up:
	@test -n "$$OPENGATEWAY_AUTH_TOKEN" || (echo "Set OPENGATEWAY_AUTH_TOKEN first" && exit 1)
	docker compose up -d --build

serve:
	uv run opengateway serve

# Example LAN production-style serve (set TOKEN in env)
serve-lan:
	@test -n "$$OPENGATEWAY_AUTH_TOKEN" || (echo "Set OPENGATEWAY_AUTH_TOKEN" && exit 1)
	uv run opengateway serve --mode public --via open --network lan \
		--host 0.0.0.0 --token "$$OPENGATEWAY_AUTH_TOKEN" \
		--public-url "$${OPENGATEWAY_PUBLIC_URL:-http://127.0.0.1:8765}"

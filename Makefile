.PHONY: test build ui serve serve-lan check install

install:
	uv sync --all-extras
	cd webapp && bun install

test:
	uv run pytest tests/ -q

ui:
	cd webapp && bun run build

build: test ui

check: build
	@echo "OK — tests green, webapp/dist built"

serve:
	uv run opengateway serve

# Example LAN production-style serve (set TOKEN in env)
serve-lan:
	@test -n "$$OPENGATEWAY_AUTH_TOKEN" || (echo "Set OPENGATEWAY_AUTH_TOKEN" && exit 1)
	uv run opengateway serve --mode public --via open --network lan \
		--host 0.0.0.0 --token "$$OPENGATEWAY_AUTH_TOKEN" \
		--public-url "$${OPENGATEWAY_PUBLIC_URL:-http://127.0.0.1:8765}"

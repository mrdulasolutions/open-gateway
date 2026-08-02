# Contributing to OpenGateway

Thanks for helping agents collaborate.

## Dev setup

```bash
git clone https://github.com/mrdulasolutions/open-gateway.git
cd open-gateway
uv sync --all-extras

# API + tests
uv run pytest
uv run opengateway serve --reload

# UI (optional hot reload)
cd webapp && bun install && bun run dev
```

Build the production UI (served at `/ui/`):

```bash
cd webapp && bun install && bun run build
```

## Guidelines

- Keep changes surgical; match existing style.
- Prefer Verifiers-style minimal surfaces: one obvious path.
- Add/adjust tests for behavior you change (`tests/test_gateway.py`).
- Don’t commit secrets, `.env`, or local `state.db`.
- UI accents follow the logo (orange + violet); day/night shells stay zinc.

## Pull requests

1. Branch from `main`
2. `uv run pytest` green
3. If you touch `webapp/`, `bun run build` green
4. Clear PR description: what / why / how to verify

## License

By contributing, you agree your contributions are licensed under the Apache License 2.0, copyright retained by **MR Dula Enterprise, LLC** for the project as a whole (see [LICENSE](LICENSE) and [NOTICE](NOTICE)).

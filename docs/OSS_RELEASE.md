# OSS release pipeline (not SaaS)

This repo ships **OpenGateway OSS self-host** only.

| Channel | Name | Notes |
|---------|------|--------|
| **PyPI** | [`open-gateway`](https://pypi.org/project/open-gateway/) | `uv tool install open-gateway` — CLI entry `opengateway` |
| **GHCR** | `ghcr.io/mrdulasolutions/open-gateway` | Tag push `v*` |
| **Git** | `mrdulasolutions/open-gateway` | Tags `v0.1.0`, … |

**Not this repo:** hosted SaaS uses PyPI package **`opengateways`** and separate infra. Do not point OSS trusted publishers at SaaS projects.

---

## One-time: clean PyPI trusted publishers

If the GitHub **`release`** environment was wired to the **SaaS** app:

1. **SaaS project (`opengateways` on PyPI)** — remove trusted publishers that reference `mrdulasolutions/open-gateway` / `publish.yml` if they were copied here by mistake.
2. **OSS project (`open-gateway` on PyPI)** — remove any stale publishers (wrong workflow, wrong environment `release`, wrong repo).
3. **Add OSS publisher** on **`open-gateway`** only:

| Field | Value |
|-------|--------|
| Owner | `mrdulasolutions` |
| Repository name | `open-gateway` |
| Workflow name | `publish.yml` |
| Environment name | **`oss-release`** |

4. **GitHub** — use environment **`oss-release`** (not `release`). Delete the old `release` environment in repo Settings → Environments if it only existed for SaaS.

No `PYPI_API_TOKEN` secret is required when trusted publishing is configured.

---

## Publish a version

```bash
# 1. Bump version in pyproject.toml + src/opengateway/__init__.py
# 2. CHANGELOG, make ui, pytest
git commit -m "Release v0.1.x"
git tag v0.1.x
git push origin main --tags
```

On tag push, **Publish OSS** workflow:

- builds + uploads **`open-gateway`** to PyPI
- pushes **`ghcr.io/mrdulasolutions/open-gateway:<semver>`** and `:latest`

### PyPI only (no new tag)

```bash
gh workflow run "Publish OSS" --ref main -f publish_pypi=true
```

### GitHub Release (manual)

```bash
gh release create v0.1.x --title "OpenGateway" --notes-file CHANGELOG_SNIPPET.md
```

---

## Verify

```bash
curl -sS https://pypi.org/pypi/open-gateway/json | jq -r '.info.version'
docker pull ghcr.io/mrdulasolutions/open-gateway:0.1.0
uv tool install open-gateway==0.1.0
opengateway --version
```

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `invalid-publisher` | PyPI publisher must match **project `open-gateway`**, environment **`oss-release`**, workflow **`publish.yml`** |
| GHCR `tag is needed` | GHCR job runs on **tag push only** — not on manual PyPI-only dispatch |
| Wrong package on install | `opengateways` = SaaS; OSS = **`open-gateway`** |

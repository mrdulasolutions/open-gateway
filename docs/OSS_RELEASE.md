# OSS release pipeline

| Channel | Name | Install |
|---------|------|---------|
| **PyPI** | [`opengateways`](https://pypi.org/project/opengateways/) | `uv tool install opengateways` |
| **CLI** | `opengateways` | alias: `opengateway` |
| **Import** | `opengateway` | unchanged |
| **GHCR** | `ghcr.io/mrdulasolutions/open-gateway` | tag `v*` |
| **Git** | `mrdulasolutions/open-gateway` | tags `v0.1.2`, … |

PyPI rejects the name `open-gateway` (too similar to unrelated `opengateway`). OSS ships on **`opengateways`**.

## PyPI trusted publisher

Project **`opengateways`** on PyPI:

| Field | Value |
|-------|--------|
| Owner | `mrdulasolutions` |
| Repository | `open-gateway` |
| Workflow | `publish.yml` |
| Environment | `oss-release` |

## Publish

```bash
git tag v0.1.2 && git push origin main --tags
# or PyPI only:
gh workflow run "Publish OSS" --ref main -f publish_pypi=true
```

## Verify

```bash
curl -sS https://pypi.org/pypi/opengateways/json | jq -r '.info.version'
uv tool install opengateways==0.1.2
opengateways --version
```

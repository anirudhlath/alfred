# Deployment

Self-host Alfred as a production instance. The goal is **clone → run**: one image,
one `.env`, external state in volumes. This guide is the canonical onboarding path; for
the deeper containerization design (build context, runtime matrix, volume seams) see
[`containerization.md`](containerization.md).

## TL;DR

```bash
git clone https://github.com/anirudhlath/alfred && cd alfred
uv venv --python 3.13 && uv pip install -e ".[dev]"

cp .env.example .env          # fill in OPENROUTER_API_KEY (+ HA_TOKEN for home control)
uv run alfredctl doctor       # validate your .env before starting
uv run alfredctl up           # build the image + start everything, prints the URL
```

`alfredctl up` builds the fat image (auto-cloning the `home-service` sibling if needed),
starts the container, and prints the reachable URL (default `http://localhost:8081`).

## What you must configure

Only one value is strictly required to get a reasoning assistant:

| Setting | Required for | How to get it |
|---------|--------------|---------------|
| `OPENROUTER_API_KEY` | System 2 (reasoning, conversation) | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `HA_TOKEN` | Home Assistant control | HA → your profile → Security → **Long-lived access tokens** → Create |
| local inference | System 1 (fast reflex path) | [Ollama](https://ollama.com) (`ollama pull gpt-oss:20b`) or any OpenAI-compatible server (vLLM/LM Studio) |

Everything else in `.env.example` under **OPTIONAL** has a working default. In particular:

- **Memory embeddings work out of the box** — the default model is ungated and needs no
  Hugging Face token or license. (Set `EMBEDDING_MODEL` to a gated model like
  `google/embeddinggemma-300m` only if you also provide `HF_TOKEN` and accept its license.)
- **Secrets are self-managing** — the keyring passphrase is generated and persisted in the
  data volume on first boot. Set `ALFRED_SECRETS_PASSPHRASE` only to pin your own. Either
  way, **back up the data dir** — losing it loses your stored integration credentials.
- **Host services just work with `localhost`** — `OLLAMA_HOST`, `OPENAI_COMPAT_HOST`,
  `HA_HOST`, etc. may point at `localhost`; the container rewrites it to the host gateway
  automatically (both `alfredctl up` and plain `docker compose`).

## `alfredctl doctor` — preflight

Run before starting to catch config gaps early instead of discovering them in the logs:

```bash
uv run alfredctl doctor            # validates .env + live-probes OpenRouter/HA/inference
uv run alfredctl doctor --offline  # shape checks only, no network
```

It reports a pass/warn/fail row per subsystem (System 2, System 1, Home Assistant, memory
embeddings, the home-service sibling). `up` also prints any non-pass rows before building.

## Two ways to run

### A) `alfredctl` (recommended)

```bash
uv run alfredctl up                # persistent (production) — state survives restarts
uv run alfredctl up --mode seed    # demo/QA — dummy fixtures, thrown away on teardown
uv run alfredctl logs -f
uv run alfredctl down
```

### B) Docker Compose (compose-of-one)

```bash
cp .env.example .env               # fill it in
uv run alfredctl build --tag alfred:latest
docker compose up -d
```

No passphrase or host-networking flags to remember — the compose file needs only your
`.env`. (See [`docker-compose.yml`](../docker-compose.yml).)

## Verify it works

```bash
uv run alfredctl smoke             # boots seed mode, health-checks infra + SPA, tears down
uv run alfredctl smoke --deep      # ALSO drives a real request through System 2 (needs a
                                   # valid OPENROUTER_API_KEY) and confirms a reply comes back
```

`--deep` is the check that actually exercises the cloud-LLM path end to end — use it after
setting your key to confirm reasoning is live, not just that the web server is up.

## Host tuning (Linux)

In-container Redis logs a warning unless the host allows memory overcommit. Set it once:

```bash
sudo sysctl -w vm.overcommit_memory=1
echo 'vm.overcommit_memory = 1' | sudo tee /etc/sysctl.d/99-redis-overcommit.conf
```

This lets Redis background-save reliably under memory pressure. It's a host-level setting
(the container can't set it for you) and is otherwise cosmetic.

## Access from your LAN

WebAuthn passkey registration and the admin API are gated to trusted networks. By default
Alfred trusts **loopback, private LAN (RFC1918), and Tailscale** — so localhost, the Docker
bridge, and your own home network all work with no configuration. To reach it from another
device, browse to the host's LAN IP on port 8081 and register a passkey.

- Add extra ranges with `ALFRED_TRUSTED_NETWORKS=10.1.2.0/24,…` (comma-separated).
- Lock it down with `ALFRED_TRUSTED_NETWORKS_STRICT=1` to trust **only** loopback,
  Tailscale, and the CIDRs you list explicitly (passkeys remain the primary auth either way).

A rejected request returns a 403 that names the offending IP and how to allow it.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `alfredctl doctor` shows System 2 ✗ | `OPENROUTER_API_KEY` missing/placeholder — set it in `.env`. |
| Memory recall disabled in logs | You set a gated `EMBEDDING_MODEL` without `HF_TOKEN`. Use the ungated default or provide the token + accept the license. |
| 403 registering a passkey | Your client IP isn't trusted — add its subnet to `ALFRED_TRUSTED_NETWORKS` (the 403 message names the IP). |
| `home-service repo not found` | `alfredctl build` auto-clones it; if you build by hand, `git clone https://github.com/anirudhlath/alfred-home-service ../home-service`. |
| Signal delivery disabled | Optional — install `signal-cli` to enable it. |
| Redis overcommit warning | Set `vm.overcommit_memory=1` on the host (see Host tuning). |

More runtime-specific troubleshooting (Apple `container`, Podman) is in
[`containerization.md` §13](containerization.md).

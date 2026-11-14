# Dev environment (16 GB laptop)

Docker runs a Linux VM that keeps its memory. Keep it small and off when you don't need it.

## Docker Desktop settings (Settings → Resources)
- **CPU limit: 4**, **Memory limit: 6 GB**, **Swap: 1 GB**
- **Resource Saver: on**

Applying them restarts Docker Desktop.

## Which target for which work
| Work | Command | Containers |
|---|---|---|
| Lint, types, unit tests | `make ci` | none |
| Integration tests | `make up-test`, then `make test` | postgres, redis (~1.3 GB) |
| Running the app, demo, evals | `make up` | + api, workers, beat (~4.4 GB limit) |
| Reranker measurements | `make up-rerank` | + reranker (768 MB) |
| Observability (Grafana/Tempo) | `make up-full` | + otel-lgtm (1 GB), heavy |
| Stop everything | `make down` | |

Rules: Docker off unless a task needs it; one heavy command at a time; check `uptime` (load average) first.
Every service has `mem_limit` and `cpus` in `docker-compose.yml`; Postgres runs with `fsync=off` (dev/test only).

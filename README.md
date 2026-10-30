# cloudObsAI

Assistente de diagnóstico somente leitura para uma stack de observabilidade (Grafana, Prometheus, Alertmanager, Loki, Jaeger), acionado pelo Google Chat. Requisitos e arquitetura em [docs/SPEC.md](docs/SPEC.md).

**Estado:** em construção. Por enquanto a stack sobe o MCP do Grafana e roda as validações; o serviço do Chat ainda não existe.

## Rodar

Requer Docker Compose, na mesma máquina e rede Docker do Grafana.

```
cp example.env .env   # preencher; o arquivo é ignorado pelo git
docker compose up -d mcp-grafana
docker compose run --rm --build app python scripts/validate_stack.py
```

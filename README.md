# cloudObsAI

Assistente de diagnóstico somente leitura para uma stack de observabilidade (Grafana, Prometheus, Alertmanager, Loki, Jaeger), acionado pelo Google Chat. Requisitos e arquitetura em [docs/SPEC.md](docs/SPEC.md).

**Estado:** em construção. O serviço do Chat responde a menções e a DMs; o recebimento de alertas depende de uma rota no Alertmanager.

## Rodar

Requer Docker Compose, na mesma máquina e rede Docker do Grafana.

```
cp example.env .env   # preencher; o arquivo é ignorado pelo git
docker compose up -d --build
docker compose run --rm app python scripts/validate_stack.py
```

## Como adicionar um space

1. No space, abra **Apps e integrações → Adicionar apps** e escolha o app. Só quem está na visibilidade do app consegue fazer isso.
2. Descubra o ID do space, que é o trecho final da URL, ou liste os spaces onde o app está:
   ```
   docker compose exec app python scripts/list_spaces.py
   ```
3. No `.env`, acrescente o ID, separado por vírgula e com o prefixo `spaces/`:
   ```
   CLOUDOBSAI_ALLOWED_SPACES=spaces/AAAA,spaces/NOVO_ID
   ```
4. Aplique a mudança, o que reinicia o app:
   ```
   docker compose up -d app
   ```

Um space na lista responde a menções. Para receber alertas pelo app, ele também precisa estar em `CLOUDOBSAI_ALERT_SPACES` e ter uma rota no Alertmanager.

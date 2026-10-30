# cloudObsAI: especificação da v1

**Status:** aprovada · **Data:** 2026-10-30

Agente de diagnóstico para uma stack de observabilidade (Grafana, Prometheus, Alertmanager, Loki, Jaeger), acionado pelo Google Chat. Explica alertas e investiga incidentes, **somente leitura**.

Convenção de estados nas tabelas: **Decidido** (o dono do projeto declarou), **Inferido** (premissa nossa, sujeita a correção), **Verificado** (fato externo, com fonte na seção 13), **Em aberto** (pendência, na seção 12).

---

## 1. Objetivo

Quando um alerta chega num space do Google Chat, entender o que está acontecendo exige abrir Grafana, Loki e Jaeger manualmente. O cloudObsAI responde, na própria thread do alerta, a um pedido por menção: explica o alerta e, se pedido, investiga, usando evidência real da stack. Também atende perguntas livres em chat direto (DM).

O chat é só o transporte. O que torna o agente útil é saber **qual alerta** está em questão e **quais consultas** ele pode fazer.

## 2. Escopo da v1

**Dentro**
- Postar os alertas do Alertmanager no space, com o contexto guardado por thread.
- Responder a menções em spaces permitidos e a mensagens em DM, de um grupo permitido.
- Consultar métricas, logs, traces e alertas, somente leitura.
- Respostas curtas, um passo por vez.

**Fora**
- Qualquer ação que altere ambiente (silence, anotação, reinício, escala): fica para depois (seção 14).
- Responder em space sem ser mencionado.
- Login OAuth com conta Claude (a Anthropic não permite em serviços de terceiros; ver seção 13).
- APIs diretas de nuvem (Azure, GCP) além da stack acima.
- Alterar o receiver atual do Alertmanager durante o piloto.

## 3. Usuários e canais

| Perfil | Como aciona | O que pode |
|---|---|---|
| Membro de um space permitido | @menção numa thread de alerta ou em mensagem solta | Pedir explicação ou investigação, perguntas livres |
| Pessoa do grupo de acesso ao DM | Qualquer mensagem no DM com o app | Perguntas livres |
| Operador (dono do projeto) | Configuração e implantação | Administrar o serviço |

Em spaces, só a @menção dispara o app (Verificado). No DM, toda mensagem dispara (Verificado).

## 4. Requisitos funcionais

| # | Requisito | Estado |
|---|---|---|
| RF1 | Os alertas continuam sendo postados no space pelo caminho atual do Alertmanager. O app não posta alertas. O contexto vem da mensagem citada: ao responder citando um alerta e mencionar o app, ele identifica o alerta pelo texto e busca a evidência na stack | Decidido |
| RF2 | Menção citando um alerta basta, mesmo sem texto. Menção numa thread sem citar não tem o contexto, porque o Chat só entrega ao app a mensagem que o mencionou | Decidido |
| RF3 | Menção em space fora de thread de alerta: responde como pergunta livre | Decidido |
| RF4 | DM: responde como pergunta livre, sem contexto de alerta | Decidido |
| RF5 | Dois modos: **explicar** (passada curta, poucas consultas) e **investigar** (mais consultas, orçamento maior). O padrão é explicar; investigar só quando o pedido indicar | Decidido (modos) / Inferido (padrão e critério) |
| RF6 | **Brevidade:** toda resposta é curta e direta, mesmo com pedidos genéricos. Se a resposta tem várias etapas, entrega um passo curto por vez e espera o usuário pedir mais | Decidido |
| RF7 | Na primeira resposta de cada thread em space, o serviço anexa um aviso fixo: para continuar, o usuário deve **sempre** mencionar o app. Não vai ao DM | Decidido (aviso) / Inferido (alcance: por thread) |
| RF8 | O agente nunca altera o ambiente na v1 | Decidido |
| RF9 | Falha em qualquer etapa (modelo, Grafana, Chat): mensagem curta de erro na thread, sem expor detalhes internos | Inferido |
| RF10 | Cada pedido atendido gera registro de auditoria: quem, onde, quando, modo, ferramentas chamadas | Inferido |
| RF11 | Eventos reentregues pelo Chat não geram respostas duplicadas | Verificado (reentrega) / Inferido (tratamento) |
| RF12 | **Memória de incidentes:** ao ser mencionado sobre um alerta, o agente associa o alerta a ocorrências anteriores e informa o que já se sabe delas (seção 7.9) | Decidido (requisito) / Inferido (desenho) |
| RF13 | A memória distingue **hipótese do agente** de **fato confirmado por uma pessoa**. Só uma pessoa confirma, por menção explícita ao app, e o registro guarda quem e quando | Decidido |
| RF14 | O README documenta o uso do app para pessoas e operadores: como usar, o que a memória guarda, como é usada | Decidido |

## 5. Requisitos não-funcionais

| Item | Requisito | Estado |
|---|---|---|
| Volume | Baixo: poucos spaces e poucos usuários de DM | Inferido |
| Tempo de resposta | O Chat exige confirmação do evento em 30 s; a análise é assíncrona e a resposta chega depois pela API do Chat. Limite de tempo por modo: a definir | Verificado (30 s) / Em aberto (limites) |
| Autorização | Space: qualquer membro de space permitido (lista de IDs de space no serviço). DM: visibilidade do app restrita a um Google Group | Decidido |
| Dados sensíveis | Sem restrição declarada. O tráfego ao modelo passa pelo gateway de IA da organização | Decidido |
| Disponibilidade | Se o serviço cair, perde-se a análise, não o alerta, porque o caminho atual do Alertmanager continua intacto | Inferido |
| Persistência | O contexto por thread, o estado de "o que já foi entregue" e a memória de incidentes sobrevivem a reinício e a recriação do contêiner | Decidido (memória) / Inferido (demais) |
| Operação | O próprio dono opera; roda na mesma VM do Grafana e do Alertmanager | Decidido (VM) / Inferido (operador) |
| Custo | Modelos básicos; economizar tokens; cota do gateway é finita e compartilhada com quem usar a chave | Decidido |
| Distribuição | Mesmo código para dois remotes (organização e GitHub pessoal); repositório limpo de qualquer dado específico da organização | Decidido |

## 6. Arquitetura

### 6.1 Componentes

```
 Alertmanager ──webhook──▶ [Receptor de alertas] ──▶ Chat API (posta na thread) 
                                   │
                                   ▼
                          [Estado por thread]
                                   ▲
 Google Chat ──Pub/Sub──▶ [Entrada do Chat] ──▶ [Núcleo] ──▶ [Agente (Agent SDK)]
      ▲                                           │              │
      └──────────── Chat API (resposta) ◀─────────┘              ├─▶ MCP do Grafana (leitura)
                                                                 └─▶ Ferramenta Jaeger (leitura)
                                                                          │
                                                          Gateway de IA ◀─┘  (modelo)
```

Um único serviço implantado na VM da GCP, com cinco responsabilidades:

1. **Receptor de alertas:** recebe o webhook do Alertmanager, posta no space e persiste o contexto.
2. **Entrada do Chat:** consome os eventos de menção e de DM entregues por Pub/Sub.
3. **Núcleo:** valida o space (allowlist), identifica o contexto (thread de alerta ou pergunta livre), aplica brevidade e o aviso da primeira resposta, controla idempotência e auditoria.
4. **Agente:** executa a análise com o Agent SDK, restrito às ferramentas de leitura.
5. **Saída do Chat:** responde na mesma thread (ou no DM) pela API do Chat, autenticada como app.

### 6.2 Fluxos

- **Alerta novo:** Alertmanager → receptor → posta no space numa thread identificada por chave do grupo de alertas → guarda o contexto.
- **Menção em thread de alerta:** Chat → Pub/Sub → entrada → núcleo (space permitido? evento duplicado?) → agente com o contexto do alerta → resposta curta na thread. Primeira vez na thread: aviso fixo anexado.
- **Menção solta / DM:** mesmo caminho, sem contexto de alerta.
- **Continuação:** em space, nova menção; no DM, nova mensagem. O núcleo recupera o que já foi entregue e continua do passo seguinte.

### 6.3 Decisões de arquitetura

| Decisão | Escolha | Alternativa descartada e motivo | Estado |
|---|---|---|---|
| Como saber qual é o alerta | Mensagem citada pelo usuário, entregue no evento do Chat, sem escopo extra | O app receber e postar os alertas: descartado pelo dono, os alertas continuam vindo do Alertmanager. Ler a thread pela API: exige escopo com aprovação de admin | Decidido |
| Entrega das menções | Pub/Sub, resposta assíncrona | Endpoint HTTP público: expõe a VM à internet | Inferido |
| Runtime do agente | Agent SDK | Client SDK com laço próprio: superfície menor, mas o dono escolheu o Agent SDK | Decidido |
| Acesso a dados | MCP do Grafana somente leitura + ferramenta própria para Jaeger | MCP com permissão ampla e ferramenta genérica de API: contorna as guardas de consulta | Decidido (MCP) / Inferido (restrições) |
| Jaeger | Ferramenta própria, só leitura, via datasource do Grafana | O MCP do Grafana não tem Jaeger (Verificado) | Inferido |
| Autorização do DM | Google Group na visibilidade do app | Domínio inteiro: não documentado | Decidido |
| Modelo | API compatível com Anthropic, por configuração (endpoint, modelo, chave) | Login OAuth da conta Claude: não permitido em serviços de terceiros | Decidido |

## 7. O agente (Agent SDK)

O Agent SDK roda o binário do Claude Code como processo filho e herda as capacidades dele, incluindo ferramentas de arquivo e comando, carregamento de configuração do disco e memória automática. Num agente que deve ser somente leitura e atender várias pessoas, **o trabalho de configuração é restringir essa herança**. Cada requisito abaixo existe por um fato verificado na seção 13.

### 7.1 Isolamento da configuração
- Não carregar fontes de configuração do disco (usuário, projeto, local). Por padrão o SDK carrega todas, inclusive `CLAUDE.md` e `CLAUDE.local.md` do diretório de trabalho.
- Executar a partir de um diretório de trabalho dedicado e vazio, fora do repositório.
- Desligar a memória automática do SDK.
- Autenticar só por chave de API (gateway ou Console), nunca por login do claude.ai. Isso também evita que os conectores MCP do claude.ai sejam carregados; além disso, desligá-los explicitamente.
- A configuração global do usuário do sistema (`~/.claude.json`) é sempre lida: deslocá-la para um diretório do serviço.

### 7.2 Superfície de ferramentas
- **Remover todas as ferramentas embutidas** (arquivo, comando, busca na web, subagentes), pelo nome. Remover pelo nome tira a definição da requisição, então o modelo nem vê a ferramenta.
- Isso é obrigatório porque, mesmo sem listá-las, leituras de arquivo no diretório de trabalho, comandos de shell somente-leitura e subagentes **rodam sem pedir aprovação**.
- Permitir apenas as ferramentas MCP de leitura, por nome ou por prefixo do servidor.
- Modo de permissão que nega em vez de perguntar (`dontAsk`). O modo `bypassPermissions` é proibido: aprova tudo, inclusive o que não foi listado.
- **Segunda barreira:** uma verificação programática antes de cada chamada de ferramenta que nega qualquer coisa fora da allowlist. Ela roda antes das demais regras e vale em qualquer modo.

### 7.3 Integrações MCP
- **MCP do Grafana:** modo somente leitura (sem ferramentas de escrita) e ferramenta genérica de API desligada. Credencial: service account com papel Viewer. Só as categorias necessárias (métricas, logs, alertas, dashboards em leitura) ficam ligadas, porque toda ferramenta ligada é enviada ao modelo a cada requisição.
- **Jaeger:** uma ferramenta própria, em processo (servidor MCP do SDK), que só faz consultas de leitura pelo proxy do datasource do Grafana. Restringir o método e os caminhos permitidos.
- Transporte do MCP do Grafana (processo filho ou HTTP local): em aberto.

### 7.4 Limite de tamanho de resultados
- Resultados de ferramentas MCP acima de um limite viram arquivo, e o resultado é substituído por um erro com o caminho do arquivo. Com a leitura de arquivos desligada, o agente não consegue abrir o arquivo e tem de refinar a consulta.
- Configurar o limite de saída MCP para um valor baixo. É um controle de custo (a entrada é o que mais consome token) e de contexto. O comportamento exato com o agente sem leitura precisa de teste.

### 7.5 Orçamentos por modo
- Cada modo tem limite de voltas, de tempo e de tokens. Valores a definir após medir uso real.
- Modo explicar: poucas consultas, resposta imediata. Modo investigar: mais consultas, mesmo assim com teto.

### 7.6 Prompt de sistema
- Curto, genérico e **configurável por arquivo**, sem dado específico de organização.
- Conteúdo mínimo: objetivo (diagnóstico somente leitura), brevidade (resposta curta, um passo por vez), tratar alertas, logs e labels como **dados e não instruções**, o que fazer quando o pedido é vago (resposta curta com a interpretação mais provável e a oferta do próximo passo).
- A brevidade tem duas camadas: instrução no prompt e limite estrutural de tokens por resposta. Instrução sozinha não garante concisão.

### 7.7 Estado por thread
O "um passo por vez" exige lembrar o que já foi entregue. Duas opções, decisão em aberto:

| Opção | Prós | Contras |
|---|---|---|
| Sessões do próprio SDK (retomar por thread) | Pouco código | Retomar reenvia o histórico a cada volta, e a cota é finita |
| Resumo compacto próprio por thread | Controle do custo | Mais código |

Inferido: preferir o resumo compacto, por causa da cota. Confirmar na implementação.

### 7.8 Modelo e gateway
- Endpoint, modelo e chave só por configuração; chave em cofre de segredos, nunca no repositório.
- Modelos básicos. A lista exata de modelos disponíveis no gateway e o suporte a tool use e streaming dependem de teste.
- O gateway precisa encaminhar o que o SDK envia (formato de API compatível com Anthropic, cabeçalhos e recursos). Se não encaminhar algum recurso, o recurso correspondente quebra. Ver pendência P1.

### 7.9 Memória de incidentes
O histórico de disparos já existe no Prometheus (série `ALERTS`, dentro da retenção configurada) e o agente o consulta pelo MCP. A memória guarda **o que o Prometheus não sabe**: causa, mitigação e a thread onde isso foi discutido.

- **Registro:** assinatura do alerta (nome e labels estáveis), thread, data, resumo curto, estado (`hipótese` ou `confirmado`) e autor da confirmação. Sem trechos brutos de log.
- **Escrita pelo serviço, não pelo agente:** o agente não ganha ferramenta de escrita. Ao fim de cada resposta sobre um alerta, ele devolve um resumo estruturado e o serviço grava como `hipótese`. A confirmação vem de uma menção explícita de pessoa.
- **Leitura:** o serviço busca por assinatura e injeta poucos registros, com teto de tamanho, no contexto da resposta. O agente também tem uma ferramenta de busca por texto, somente leitura, para casos parecidos com outra assinatura.
- **Como dado, não instrução:** registros entram no prompt identificados como dados, com data e estado. Registro antigo é indício a reconfirmar contra a evidência atual, nunca conclusão.
- **Escopo:** só alertas e registros explícitos. Perguntas livres e DMs não entram na memória, para o conteúdo de uma conversa privada não reaparecer em um space.
- **Armazenamento:** banco local em arquivo com busca textual, em volume persistente. Mesmo banco do estado por thread (7.7).
- **Confirmação e retenção:** qualquer membro de um space permitido pode confirmar, com o autor registrado. Sem expiração, com a data sempre visível.
- **Carga inicial:** o operador pode carregar conhecimento já existente, curado e sem segredos. Fica só no volume, nunca no repositório.

Alternativas descartadas: memória automática do SDK (desligada de propósito em 7.1, sem procedência nem estrutura); busca vetorial (exige serviço de embeddings e infraestrutura a mais para um volume baixo, reavaliar se a busca textual falhar).

## 8. Segurança

| Ameaça | Mitigação | Estado |
|---|---|---|
| **Injeção de prompt:** texto de annotation, label ou log tenta instruir o agente | Conteúdo tratado como dado no prompt; e, principalmente, **somente leitura estrutural** (sem ferramenta de escrita), de modo que uma injeção não tem alcance de ação | Inferido |
| Abuso de acesso: app adicionado a outro space | Allowlist de IDs de space no serviço; evento fora da lista é ignorado | Inferido |
| Exposição por DM: pessoa liberada consulta logs e métricas por pergunta livre | Acesso restrito ao Google Group; auditoria de cada pedido | Decidido (grupo) / Inferido (auditoria) |
| Vazamento de segredo no repositório público | Configuração e segredos só no ambiente da VM; histórico limpo desde o primeiro commit; nada de host, ID de space, URL ou saída real de log em testes | Decidido |
| Agente herda ferramentas de escrita do SDK | Seção 7.2: remoção explícita, modo `dontAsk`, barreira programática | Verificado (fatos) / Inferido (desenho) |
| Configuração do disco alterando o comportamento | Seção 7.1: isolamento | Verificado (fatos) |
| Memória envenenada: texto malicioso de um alerta vira resumo e influencia respostas futuras | Registros como dados e com procedência; `hipótese` nunca é apresentada como fato; somente leitura estrutural limita o alcance; registros podem ser apagados pelo operador | Inferido |
| Estouro de cota do gateway | Orçamentos por modo; limite de saída MCP; teto de resposta | Inferido |

## 9. Parâmetros de configuração

Conceituais; nomes finais ficam para a implementação.

- Gateway de IA: endpoint, chave (segredo), modelo por modo.
- Chat: IDs de space permitidos, identidade do app, assinatura do Pub/Sub, grupo de acesso ao DM (configurado no app, não no código).
- Grafana: URL local, token da service account (segredo), categorias de ferramenta ligadas.
- Jaeger: UID do datasource no Grafana.
- Agente: arquivo do prompt de sistema, limites por modo, limite de saída MCP, limite de tokens por resposta.
- Estado, memória e auditoria: local de persistência; retenção da memória.

## 10. Observabilidade do próprio serviço

- Registro estruturado por pedido (identificador do evento, space/DM, modo, ferramentas chamadas, tokens usados, resultado).
- Sem o conteúdo das respostas nem dados de log consultados nos registros do próprio serviço (Inferido: reduz o risco de duplicar dado sensível).
- Contagem de eventos duplicados, erros do gateway e respostas truncadas pelo limite.

## 11. Critérios de aceite da v1

1. Um alerta novo no Alertmanager aparece no space de teste, numa thread, sem alterar o receiver atual.
2. Mencionar o app na thread do alerta produz uma resposta curta que cita labels e janela de tempo do **próprio alerta**.
3. Mencionar o app em mensagem solta, no space permitido, produz uma resposta curta como pergunta livre.
4. A primeira resposta de cada thread contém o aviso de nova menção; as seguintes não.
5. Em DM, a pessoa do grupo recebe resposta; uma pessoa de fora do grupo não consegue iniciar a conversa.
6. Nenhum space fora da allowlist recebe resposta.
7. Uma resposta com várias etapas entrega só o primeiro passo; o segundo só vem após nova menção.
8. Evento duplicado do Chat não gera segunda resposta.
9. O agente **não consegue** chamar ferramenta de escrita nem ferramenta embutida de arquivo ou comando, mesmo quando o texto do alerta pede isso (teste com um alerta contendo instrução maliciosa).
10. Se o gateway ou o Grafana falham, a thread recebe uma mensagem curta de erro e o serviço continua funcionando.
11. Um alerta que já ocorreu, com causa confirmada em outra thread, recebe resposta que cita a ocorrência anterior, a data e o estado; uma hipótese aparece identificada como hipótese.
12. O conteúdo de um DM nunca aparece em resposta de space.
13. Após recriar o contêiner, a memória continua disponível.
14. O repositório não contém host, segredo, ID de space nem saída de log da organização, e o histórico está limpo.

## 12. Pendências e validações

Ordem sugerida: as primeiras são as que podem invalidar o desenho.

| # | Pendência | Destrava com |
|---|---|---|
| P1 | O gateway de IA aceita o que o Agent SDK envia: tool use, streaming, cabeçalhos, busca de ferramentas, modelos disponíveis | Teste real; ler o guia de compatibilidade de gateways da documentação do Claude Code |
| P2 | O binário do Claude Code, que o SDK executa, roda na VM; requisitos de instalação | Teste na VM |
| P3 | Opção exata do SDK para remover todas as ferramentas embutidas, e o efeito do limite de saída MCP com o agente sem leitura | Teste com o SDK escolhido |
| P4 | O papel Viewer consegue usar o proxy do datasource Jaeger? | Teste no Grafana; senão, acesso direto ao Jaeger |
| P5 | Autenticação do Pub/Sub e da resposta do app, incluindo eventos de DM | Documentação do Chat e teste |
| P6 | Duplicidade de alertas no piloto (o receiver novo posta uma segunda mensagem por alerta) | Piloto num space de teste; o corte em produção substitui o receiver atual (alto risco, reversível por configuração) |
| P7 | Cota do gateway compartilhada com a chave pessoal do dono | Pedir ao admin do gateway uma chave de serviço ou mais cota |
| P8 | Fixar a versão do MCP do Grafana | Usar a imagem versionada (`grafana/mcp-grafana:2.1.1` em 2026-10-30), nunca `latest` |
| P9 | Estado por thread: resumo próprio ou sessões do SDK (7.7) | Decisão na implementação |
| P10 | Limites concretos: tamanho da resposta, tempo, voltas e tokens por modo | Medir uso real e decidir |
| P11 | Linguagem de implementação: Python é a premissa (Inferido); o Agent SDK existe em Python e TypeScript | Confirmação do dono |
| P12 | Criar o Google Group de acesso ao DM e ligá-lo à visibilidade do app | O dono |
| P13 | Alcance do aviso de nova menção: por thread (padrão) ou por usuário | Confirmação do dono |
| P14 | Como o modo é escolhido: por palavras do pedido ou pelo próprio modelo | Decisão na implementação |
| P15 | Quem fora da lista de visibilidade consegue mencionar o app num space onde ele já está | Teste com outra pessoa no space piloto. Se não conseguir, usar Google Group, que exige o app na lista de permissões do admin do Workspace |

## 13. Fatos verificados

Consultas feitas entre 2026-10-22 e 2026-10-29. Reconsultar antes de construir sobre eles, porque as documentações mudam.

**MCP do Grafana** ([github.com/grafana/mcp-grafana](https://github.com/grafana/mcp-grafana))
- Cobre Prometheus, Loki, Tempo, alertas, incidentes e dashboards; transportes stdio, SSE e HTTP.
- `--disable-write` remove as ferramentas de escrita.
- Não há ferramenta de Jaeger.
- A ferramenta genérica `grafana_api_request` consulta o proxy de datasources e contorna as guardas de consulta; é desligada por `--disable-api`.
- Há releases e imagem Docker versionados (v2.1.1, de 2026-10-20; consultado em 2026-10-30). Corrige a consulta anterior, que não encontrou releases.

**Google Chat**
- Conexão do app por endpoint HTTP, Pub/Sub, Apps Script ou Dialogflow; em spaces o gatilho é @menção, no DM toda mensagem; confirmação em 30 s; resposta assíncrona pela API; pode haver reentrega ([receber interações](https://developers.google.com/workspace/chat/receive-respond-interactions)).
- Visibilidade do app: até 5 pessoas ou Google Groups; visibilidade para o domínio inteiro não é documentada ([configurar o app](https://developers.google.com/workspace/chat/configure-chat-api)).
- Criar mensagem: resposta em thread por `thread.thread_key` ou `thread.name`; `REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD` responde na thread ou abre outra; `requestId` torna a criação idempotente; autenticação de app usa o escopo `chat.bot`; a opção `messageReplyOption` só vale em spaces nomeados ([messages.create](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/create)).
- Leitura de mensagens por app exige `chat.app.messages.readonly` (aprovação de admin) e só vê mensagens públicas ([messages.list](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/list)).

**Agent SDK** ([visão geral](https://code.claude.com/docs/en/agent-sdk/overview), [permissões](https://code.claude.com/docs/en/agent-sdk/permissions), [configuração do disco](https://code.claude.com/docs/en/agent-sdk/claude-code-features), [MCP](https://code.claude.com/docs/en/agent-sdk/mcp))
- Roda o binário do Claude Code como processo filho, com ferramentas embutidas, MCP, permissões, hooks e sessões.
- Sem restrição, carrega configuração de usuário, projeto e local, incluindo `CLAUDE.md` e `CLAUDE.local.md`. Passar uma lista vazia de fontes desliga isso. A configuração global (`~/.claude.json`) e a memória automática continuam sendo lidas, e a memória é desligável por variável de ambiente.
- Remover uma ferramenta embutida pelo nome tira a definição da requisição. Chamadas que não precisam de aprovação (leituras no diretório de trabalho, comandos de shell somente-leitura, subagentes) rodam mesmo sem estarem listadas.
- `allowedTools` não limita o modo `bypassPermissions`, que aprova tudo. O modo `dontAsk` nega o que não está permitido. Verificações programáticas antes da chamada de ferramenta rodam antes de todas as outras regras e podem negar em qualquer modo.
- Ferramentas MCP têm nome `mcp__<servidor>__<ferramenta>`, com curinga permitido depois do prefixo do servidor. Resultados MCP acima de 25.000 tokens viram arquivo e erro; o limite é configurável.
- Servidores MCP em processo (SDK MCP) permitem ferramentas próprias.
- Conectores MCP do claude.ai só carregam com login do claude.ai.

**Política de uso** ([legal e compliance](https://code.claude.com/docs/en/legal-and-compliance))
- Quem constrói serviços sobre o Claude, inclusive com o Agent SDK, deve usar chave de API do Console ou de um provedor de nuvem suportado. Não é permitido oferecer login do claude.ai, rotear requisições por credenciais de planos Free, Pro ou Max em nome de usuários, nem coletar ou intermediar credenciais dessas contas.

**Gateways** ([visão geral de gateways](https://code.claude.com/docs/en/llm-gateway))
- Qualquer gateway com um formato de API suportado funciona; um gateway que não encaminha um recurso novo quebra o recurso correspondente. Com uma credencial de gateway ativa, ela substitui o login do claude.ai e a cobrança é por token para o dono da credencial.

**Alertmanager:** `googlechat_configs` e opção de thread não foram confirmados e não são necessários, porque o desenho usa o webhook genérico.

## 14. Depois da v1: ações (nível 2)

Fora da v1, registrado para não se perder o raciocínio:
- Catálogo fechado de ações nomeadas, cada uma com credencial própria e separada da de leitura. Nunca o mesmo agente com permissão maior, porque uma injeção de prompt passaria a ter alcance de escrita.
- Confirmação humana antes de executar, por proprietário ou gerente do space, verificada no momento. Os papéis existem em spaces nomeados (proprietário, gerente, membro). Se a API não expuser o papel para um app, usar uma lista fixa de aprovadores.
- Ações candidatas: anotação no Grafana e silence curto no Alertmanager.

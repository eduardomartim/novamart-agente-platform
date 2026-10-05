"""Portuguese (pt-BR) catalogue for the dashboard.

Generated as a pair with its sibling: every key here exists there, and
`tests/integration/test_i18n.py` fails the build if that stops being true.
A key missing from one catalogue, or blank in one of them, shows the reader
the wrong thing -- so both are asserted rather than assumed.

Edit both files together. `t()` falls back to Portuguese for a key the English
catalogue lacks, which keeps a page readable but is a bug, not a feature.
"""

from __future__ import annotations

from typing import Final

STRINGS: Final[dict[str, str]] = {
    "overview.lede": (
        "NovaMart é um orquestrador empresarial de agentes de IA. Ele lê uma "
        "solicitação em linguagem natural, decide quais agentes especializados "
        "entram em ação, alcança ferramentas e dados, e um "
        "<strong>Policy Engine</strong> decide o que pode rodar — antes que "
        "qualquer ferramenta rode."
    ),
    "hero.chip_autonomy": "Autonomia",
    "hero.chip_governance": "Governança",
    "hero.chip_security": "Segurança",
    "hero.chip_observability": "Observabilidade",
    "hero.chip_scale": "Escalabilidade",
    "scene.live": "Ao vivo (demo)",
    "ov.env_title": "Ambiente de demonstração",
    "ov.tech_title": "Tecnologias utilizadas",
    "ov.state_title": "Estado desta instalação",
    "ov.state_mode": "Modo",
    "ov.state_requests": "Requisições registradas",
    "ov.state_provider": "Chamadas ao provedor no ledger",
    "ov.state_rules": "Políticas ativas",
    "ov.state_note": (
        "Números lidos do banco e do código desta instalação. Não há uptime, "
        "SLA nem contagem de usuários aqui porque este projeto não tem nenhum "
        "dos três."
    ),
    "hero.headline": "Plataforma de Orquestração de Agentes de IA Empresarial",
    "hero.sub": (
        "O NovaMart orquestra agentes especializados para executar tarefas de "
        "negócio de forma governada. O orquestrador decide quem entra em ação, "
        "um <strong>Policy Engine</strong> autoriza antes de qualquer execução, "
        "um gateway é o único caminho até as ferramentas, e cada passo fica "
        "registrado."
    ),
    "hero.cta_explore": "Explorar a plataforma",
    "hero.cta_arch": "Ver a arquitetura",
    "metric.rules": "políticas",
    "metric.rules_sub": "Decidem antes de qualquer execução",
    "metric.agents": "agentes",
    "metric.agents_sub": "Router, researcher, executor, validator, answerer",
    "metric.tools": "ferramentas",
    "metric.tools_sub": "Alcançadas só pelo gateway",
    "metric.audit": "Auditoria",
    "metric.audit_head": "por requisição",
    "metric.audit_sub": "Id, passos cronometrados e decisão gravados",
    "scene.title": "Arquitetura em ação",
    "scene.trace_title": "Execução em tempo real",
    "scene.demo_tag": "Demonstração",
    "scene.now": "Cenário",
    "scene.note": (
        "Dez workflows encenados, em repetição. Os agentes especializados "
        "representam a capacidade de especialização da arquitetura — o backend "
        "tem cinco agentes, nomeados em Arquitetura. Os sistemas de integração "
        "são pontos possíveis de conexão; nenhum está conectado, e nada aqui faz "
        "uma chamada externa. Para uma execução real, use o Orquestrador."
    ),
    "scene.idle": "As mensagens dos agentes aparecem aqui.",
    "scene.verdict_idle": "O resultado aparece quando o cenário termina.",
    "scene.leg_running": "Executando",
    "scene.leg_success": "Concluído",
    "scene.leg_waiting": "Aguardando aprovação",
    "scene.leg_blocked": "Bloqueado",
    "scene.caption": (
        "Uma plataforma processando workflows diferentes — não um chatbot "
        "respondendo perguntas."
    ),
    "overview.problem": "O problema que ele resolve",
    "overview.problem_body": (
        "Uma equipe de operações recebe o dia inteiro pedidos escritos por "
        "pessoas: consultas simples, alterações que não podem acontecer sem "
        "aprovação humana, e tentativas de convencer o sistema a fazer o que "
        "ele deveria recusar. Um modelo de linguagem sozinho não separa os "
        "três, porque quem interpreta o pedido é também quem decidiria "
        "atendê-lo. O NovaMart separa as duas coisas: os agentes propõem, e a "
        "autorização é decidida fora deles, a partir dos metadados da "
        "ferramenta — nunca do que o modelo afirma."
    ),
    "overview.demo_env": (
        "Demonstrado sobre a <strong>HDstore</strong>, uma varejista fictícia "
        "criada só para isso. Os dados são simulados; a plataforma é real."
    ),
    "overview.how_it_works": "Como funciona",
    "overview.same_path": (
        "O mesmo caminho toda vez. Nenhum agente executa uma ferramenta: eles "
        "propõem, e o Policy Engine decide."
    ),
    "overview.try_it": "Experimente",
    "overview.try_lede": "Uma pergunta em linguagem natural, e a decisão que o sistema tomou.",
    "overview.run_this": "Executar esta pergunta",
    "overview.ask_any_language": (
        "Pergunte em português ou em inglês — o roteador entende os dois. "
        "A resposta segue o idioma da interface."
    ),
    "overview.demonstrates": "O que este projeto demonstra",
    "overview.stack_note": (
        "RAG · MCP · Kubernetes · TLS · Prometheus · 840+ testes de segurança — "
        "detalhados em **Arquitetura**."
    ),
    "overview.what_holds": "O que sustenta isso",
    "overview.not_built": "O que não foi construído",
    "overview.not_built_lede": (
        "As limitações ficam na mesma tela que as afirmações. Um projeto que só"
        " lista o que faz bem não é verificável."
    ),
    "overview.see_architecture": "Ver a arquitetura",
    "company.eyebrow": "Contexto da demonstração",
    "company.lede": "O ambiente empresarial contra o qual a plataforma é testada.",
    "company.fictional_title": "A HDstore é uma empresa fictícia",
    "company.fictional_body": (
        "Ela foi criada exclusivamente para demonstrar o funcionamento e as "
        "capacidades do NovaMart. **Não é uma empresa real e não é um cliente "
        "real.** Todos os clientes, pedidos, produtos, tickets, remessas e "
        "artigos abaixo são gerados e determinísticos. Existem para dar ao "
        "orquestrador um contexto empresarial realista onde ser testado — e "
        "para que as perguntas que você fizer tenham uma resposta verificável."
    ),
    "company.tab_customers": "Clientes",
    "company.tab_orders": "Pedidos",
    "company.tab_products": "Produtos",
    "company.tab_tickets": "Tickets",
    "company.tab_shipments": "Remessas",
    "company.tab_kb": "Base de conhecimento",
    "company.customers": "Clientes",
    "company.orders": "Pedidos",
    "company.products": "Produtos",
    "company.open_tickets": "Tickets abertos",
    "company.the_data": "Os dados",
    "company.what_you_can_test": "O que você pode testar",
    "company.ids_are_real": (
        "Os identificadores acima são reais dentro da simulação. Use-os nas "
        "perguntas — o sistema responde sobre eles."
    ),
    "company.ask": "Perguntar",
    "result.question_label": "**Pergunta**",
    "result.answer_label": "**Resposta**",
    "result.out_of_scope": (
        "**Pergunta fora do alcance desta demonstração.** Nenhuma ferramenta "
        "disponível responde a ela, e nada foi inventado."
    ),
    "result.how_processed": "Como a solicitação foi processada",
    "result.decision": "DECISÃO",
    "result.agent": "AGENTE",
    "result.tool": "FERRAMENTA",
    "result.time": "TEMPO",
    "confirm.bound_to_args": (
        "O que você aprova fica preso a estes argumentos exatos: uma "
        "confirmação não pode ser reaproveitada para outra ação."
    ),
    "confirm.approve": "Aprovar",
    "confirm.decline": "Recusar",
    "next.try_forbidden": "Tentar algo proibido",
    "next.contrast": (
        "Essa foi permitida. O contraste é o ponto: agora peça uma exclusão e "
        "veja o Policy Engine recusar antes de qualquer ferramenta rodar."
    ),
    "next.see_policies": "Ver as políticas",
    "next.rule_listed": (
        "A regra que recusou esta operação está listada lá, junto com a matriz "
        "de quem pode chamar o quê."
    ),
    "orch.eyebrow": "Demonstração principal",
    "orch.title": "Experimente o orquestrador",
    "orch.lede": (
        "Faça uma pergunta de negócio e observe o NovaMart interpretar a "
        "solicitação, escolher a rota e as ferramentas, coordenar os agentes, "
        "consultar os dados, aplicar governança e construir a resposta. O que "
        "aparece abaixo é a execução real — nenhuma etapa é encenada."
    ),
    "orch.question": "Pergunta",
    "orch.run": "Executar",
    "orch.examples": "Exemplos",
    "security.eyebrow": "Controles aplicados em execução",
    "security.title": "Segurança",
    "security.lede": (
        "Segurança não está apenas documentada; ela é aplicada durante a "
        "execução. O motor de políticas é a única autoridade."
    ),
    "security.four_pillars": "Quatro pilares",
    "security.three_decisions": "As três decisões",
    "security.try_hostile": "Experimente, do simples ao hostil",
    "security.scenarios_lede": (
        "Cinco cenários em ordem crescente de dificuldade. Cada um roda de "
        "verdade — o resultado é o que a plataforma faz, não uma descrição do "
        "que ela faria."
    ),
    "security.rules_in_full": "As regras de política, na íntegra",
    "security.who_may_call": "Quem pode chamar o quê",
    "security.matrix_lede": (
        "A matriz de capacidades. Uma ferramenta ausente da linha de um agente "
        "não é alcançável por ele, sob nenhuma circunstância."
    ),
    "security.what_to_watch": "O que observar",
    "arch.eyebrow": "Como o sistema é montado",
    "arch.title": "Arquitetura",
    "arch.lede": (
        "Um caminho de requisição, uma autoridade, e a infraestrutura que "
        "sustenta os dois."
    ),
    "arch.request_path": "Caminho da requisição",
    "arch.platform": "Plataforma",
    "arch.current": "Demo atual",
    "arch.current_lede": (
        "O que roda nesta instalação, hoje. Cada caixa existe no código e é "
        "exercitada pelos testes."
    ),
    "arch.production": "Arquitetura de produção",
    "arch.production_lede": (
        "Como a mesma plataforma se conectaria a uma empresa real. "
        "<strong>Nada nesta linha está implementado</strong> — é a arquitetura "
        "de integração pretendida, desenhada aqui para mostrar onde os sistemas "
        "de um cliente entrariam. O que muda é a origem dos dados; o miolo "
        "— agentes, política, gateway — é o mesmo da linha acima."
    ),
    "flow.simdata": "Dados simulados",
    "flow.tools_plural": "Ferramentas",
    "flow.realco": "Empresa real",
    "flow.sources": "APIs · Webhooks · Bancos · ERP · CRM · SaaS · Documentos",
    "flow.layer": "Camada de integração e dados",
    "flow.authtools": "Ferramentas autorizadas",
    "arch.the_agents": "Agentes",
    "arch.group_governance": "Governança e serviços",
    "arch.group_governance_lede": (
        "Decidem e executam. Nenhum dos três é um agente, e é essa separação "
        "que faz a autorização não depender do que o modelo disser."
    ),
    "arch.group_data": "Dados",
    "arch.group_data_lede": "O que os agentes conseguem alcançar — e nada além disso.",
    "arch.group_infra": "Infraestrutura e estado",
    "arch.group_observability": "Observabilidade",
    "svc.policy": "Policy Engine",
    "svc.policy_body": (
        "Onze regras decidem ALLOW, CONFIRM ou DENY a partir dos metadados da "
        "ferramenta. Nunca lê o prompt."
    ),
    "svc.gateway": "Gateway",
    "svc.gateway_body": (
        "O único caminho até uma ferramenta. Uma chamada direta levanta "
        "DirectToolInvocationError em vez de rodar."
    ),
    "svc.mcp": "MCP",
    "svc.mcp_body": (
        "As ferramentas ficam atrás de um limite de processo, alcançadas por "
        "grants assinados de uso único."
    ),
    "data.dataset": "Dataset HDstore",
    "data.dataset_body": (
        "Clientes, pedidos, produtos, tickets e remessas — gerados, "
        "determinísticos, em memória."
    ),
    "data.kb": "Base de conhecimento / RAG",
    "data.kb_body": (
        "Índice vetorial com proveniência verificada na carga, fundido com "
        "ranqueamento lexical BM25."
    ),
    "obsv.tracing": "Tracing",
    "obsv.tracing_body": (
        "Cada requisição tem id e trace id; cada passo vira um evento "
        "persistido, em ordem."
    ),
    "obsv.metrics": "Métricas",
    "obsv.metrics_body": "Contadores e latências expostos no formato Prometheus.",
    "obsv.logs": "Logs",
    "obsv.logs_body": (
        "Logs estruturados em JSON, correlacionados pelo id da requisição."
    ),
    "arch.agents_lede": "Cinco papéis especializados. Nenhum deles executa uma ferramenta.",
    "arch.policy_not_agent": (
        "O **Policy Engine** não é um agente. Ele é a autoridade que decide o "
        "que qualquer agente pode fazer, e não pode ser persuadido por texto."
    ),
    "arch.also_demonstrated": "Também demonstrado",
    "arch.also_lede": "O que a tela inicial cita sem descrever.",
    "obs.title": "Observabilidade",
    "obs.lede": (
        "Toda requisição tem um identificador, passos cronometrados e uma "
        "decisão registrada."
    ),
    "obs.requests": "Requisições",
    "obs.blocked": "Bloqueadas",
    "obs.tool_calls": "Chamadas de ferramenta",
    "obs.median_latency": "Latência mediana · {mode}",
    "obs.median_latency_help": (
        "Mediana de {samples} requisições registradas em modo {mode}. "
        "Requisições do outro modo não entram no cálculo."
    ),
    "obs.median_latency_none": (
        "Menos de {minimum} requisições registradas em modo {mode}. Amostra "
        "insuficiente para uma mediana — e misturar os dois modos daria um "
        "número que não descreve nenhum deles."
    ),
    "obs.real_vs_demo": (
        "Tudo nesta página é medido, não estimado: sai dos eventos que as "
        "requisições realmente gravaram. O que é de demonstração é a origem "
        "das requisições — perguntas feitas nesta instalação, sobre um dataset "
        "fictício. Não há uptime, SLA ou contagem de usuários aqui porque este "
        "projeto não tem nenhum dos três."
    ),
    "obs.recent_requests": "Requisições recentes",
    "obs.col_request": "Requisição",
    "obs.col_status": "Status",
    "obs.col_route": "Rota",
    "obs.col_retries": "Retries",
    "obs.col_ms": "ms",
    "obs.none_recorded": "Nenhuma requisição registrada ainda.",
    "obs.no_recent": "Sem requisições recentes.",
    "obs.details": "Detalhes",
    "obs.counters_lede": "Contadores por tipo de evento, lidos do stream de eventos persistido.",
    "obs.latency_caption": "Latência por requisição (ms), da mais antiga à mais recente",
    "obs.no_latency": (
        "Nenhuma requisição registrou latência ainda, então não há série para "
        "desenhar."
    ),
    "tech.see_details": "Ver detalhes técnicos",
    "tech.correlation_id": "Correlation ID",
    "tech.policy_decision": "Decisão de política",
    "tech.agents": "Agentes",
    "tech.tools": "Ferramentas",
    "tech.full_event_sequence": "Sequência completa de eventos",
    "sidebar.status": "Plataforma ativa",
    "sidebar.group_env": "Ambiente",
    "sidebar.env_fictional": "{company} — ambiente fictício de demonstração",
    "nav.demo_group": "Demonstração",
    "nav.page": "Página",
    "nav.platform_group": "Plataforma — os instrumentos operacionais",
    "nav.overview": "Visão geral",
    "flow.agents": "Agentes",
    "nav.company": "Empresa",
    "nav.orchestrator": "Orquestrador",
    "nav.security": "Segurança",
    "nav.architecture": "Arquitetura",
    "nav.observability": "Observabilidade",
    "nav.language": "Idioma",
    "mode.live_caption": "As requisições são atendidas por um provedor real.",
    "mode.stub_caption": (
        "Nenhum modelo está sendo chamado. As respostas são determinísticas e "
        "geradas localmente."
    ),
    "table.no_rows": "Sem registros para exibir.",
    "orch.placeholder": "Qual o status do pedido de Ana Ribeiro?",
    "mode.live_banner": (
        "<strong>Modo live.</strong> As requisições são processadas pelo "
        "provedor configurado (<code>{model}</code>)."
    ),
    "mode.stub_banner": (
        "<strong>Simulação local determinística.</strong> Nenhum provedor de IA"
        " externo está sendo chamado, e nada aqui é atribuído a um."
    ),
    "flow.user": "Usuário",
    "flow.router": "Router",
    "flow.agent": "Agente especializado",
    "flow.policy": "Policy Engine",
    "flow.tool": "Ferramenta",
    "flow.validator": "Validador",
    "flow.answer": "Resposta",
    "holds.policy": "Política",
    "holds.policy_body": "11 regras decidem antes de qualquer execução.",
    "holds.auth": "Autenticação",
    "holds.auth_body": "A API recusa com 401 sem credencial.",
    "holds.audit": "Auditoria",
    "holds.audit_body": "Cada requisição tem id, passos e decisão gravados.",
    "holds.cost": "Custo",
    "holds.cost_body": "Limite de {budget} chamadas por dia ao provedor, aplicado em modo live.",
    "holds.real_provider": "Provedor real",
    "holds.real_provider_body": (
        "{calls} chamadas ao Gemini já registradas no ledger físico desta "
        "instalação."
    ),
    "tools.name": "Ferramenta",
    "tools.risk": "Risco",
    "tools.proposed_by": "Proposta por",
    "tools.decision": "Decisão",
    "tools.execution": "Execução",
    "result.model_calls": "Chamadas de modelo nesta requisição: {n}",
    "confirm.needs_human": (
        "**Um humano precisa aprovar isto.** O executor propôs `{tool}` (risco "
        "{risk}). Nada foi executado — a requisição está suspensa até você "
        "decidir."
    ),
    "block.policy_title": "Operação bloqueada pela política de segurança.",
    "block.policy_body": (
        "A ação solicitada não tem autorização suficiente. Nenhuma ferramenta "
        "foi executada, e a recusa ficou registrada."
    ),
    "block.rate_title": "Limite de requisições atingido.",
    "block.rate_body": (
        "A demonstração limita quantas solicitações um mesmo usuário faz por "
        "minuto. Aguarde alguns instantes e tente de novo — nada foi recusado "
        "por motivo de segurança."
    ),
    "block.budget_title": "Orçamento diário de chamadas ao provedor esgotado.",
    "block.budget_body": (
        "Esta demonstração define o próprio teto de chamadas ao modelo e parou "
        "antes de gastar mais. O provedor está de pé; foi uma decisão de custo."
    ),
    "block.circuit_title": "Circuito aberto após falhas seguidas do provedor.",
    "block.circuit_body": (
        "A plataforma parou de tentar depois de erros repetidos, para não "
        "insistir contra um serviço indisponível. Ela volta a tentar sozinha."
    ),
    "block.limits_title": "Teto de recursos da requisição atingido.",
    "block.limits_body": (
        "A requisição excedeu um limite de passos, tempo ou tamanho definido "
        "por requisição. É um controle de custo e de latência, não de "
        "segurança."
    ),
    "block.generic_title": "Operação interrompida por um controle da plataforma.",
    "block.generic_body": (
        "A execução parou antes de concluir. O rastro abaixo mostra em que "
        "ponto e por quê."
    ),
    "examples.read_note": "Somente leitura. Apenas respondem.",
    "examples.action_note": "Propõem uma mudança, então param e esperam por você.",
    "examples.security_note": "São recusadas. Observe qual regra dispara.",
    "examples.read_tab": "Consultar",
    "examples.action_tab": "Alterar algo",
    "examples.security_tab": "Tentar quebrar",
    "tabs.customers": "Clientes",
    "tabs.orders": "Pedidos",
    "tabs.products": "Produtos",
    "tabs.tickets": "Tickets",
    "table.see_all": "Ver todos ({n})",
    "pillar.auth": "Autenticação",
    "pillar.auth_body": (
        "A API exige credencial. Sem ela, 401 — e ela nunca vem do corpo da "
        "requisição."
    ),
    "pillar.authz": "Autorização",
    "pillar.authz_body": "Escopos independentes: quem pede uma ação de risco não é quem a aprova.",
    "pillar.policy": "Policy Engine",
    "pillar.policy_body": (
        "Onze regras decidem ALLOW, DENY ou CONFIRM antes de qualquer "
        "ferramenta rodar."
    ),
    "pillar.audit": "Auditoria",
    "pillar.audit_body": (
        "Cada decisão é gravada com quem aprovou, autenticado — não "
        "auto-declarado."
    ),
    "decision.allow_body": "A ação roda. Risco baixo e dentro do que o agente pode fazer.",
    "decision.confirm_body": (
        "A execução para e espera um humano. Nada acontece até alguém decidir."
    ),
    "decision.deny_body": "Recusada. Nenhuma ferramenta é chamada, e a recusa fica registrada.",
    "platform.k8s": "Kubernetes",
    "platform.k8s_body": "Deployment com probes, NetworkPolicy default-deny e HPA.",
    "platform.redis": "Redis",
    "platform.redis_body": "Confirmações, checkpoints e limites compartilhados entre réplicas.",
    "platform.postgres": "PostgreSQL",
    "platform.postgres_body": "Requisições, eventos e gasto — um ledger para todas as réplicas.",
    "platform.observability": "Observabilidade",
    "platform.observability_body": "Logs JSON, métricas Prometheus e correlation IDs.",
    "platform.netpol": "Network Policies",
    "platform.netpol_body": "Nada alcança a API além do que foi declarado.",
    "platform.tls": "TLS / Ingress",
    "platform.tls_body": "Terminação na borda; o Service permanece ClusterIP.",
    "obs.latency_note": (
        "Mediana {median} ms · máximo {peak} ms. Um pico isolado costuma ser "
        "uma requisição que parou para confirmação humana: o relógio continua "
        "correndo enquanto ela espera a decisão."
    ),
    "obs.metric": "Métrica",
    "obs.value": "Valor",
    "step.request_started": "Requisição recebida",
    "step.input_flagged": "Entrada com forma de injeção",
    "step.input_sensitive": "Entrada contém dado sensível",
    "step.input_rejected": "Entrada rejeitada",
    "step.rate_limited": "Limite de taxa aplicado",
    "step.route_selected": "Roteador classificou a requisição",
    "step.agent_started": "Agente iniciou",
    "step.action_proposed": "Ação proposta",
    "step.llm_retry": "Tentativa ao provedor falhou, repetindo",
    "step.llm_failed": "Provedor falhou em todas as tentativas",
    "step.policy_decision": "Motor de políticas decidiu",
    "step.confirmation_requested": "Aguardando aprovação humana",
    "step.confirmation_resolved": "Decisão humana registrada",
    "step.tool_call": "Ferramenta executada",
    "step.validation": "Resultado validado",
    "step.resource_limit": "Teto de recursos atingido",
    "step.circuit_open": "Circuito do provedor aberto",
    "step.prompt_redacted": "Credenciais removidas antes da saída",
    "step.output_redacted": "Resposta redigida",
    "step.request_completed": "Resposta devolvida",
    "step.request_failed": "Requisição falhou",
    "step.agent_named_started": "{agent} iniciou",
    "budget.exhausted": (
        "**A capacidade de hoje foi gasta.** Esta demonstração define o próprio"
        " limite diário de chamadas ao provedor de IA, e o sistema parou "
        "*antes* de fazer mais uma.  O provedor está de pé e nada quebrou — "
        "este ambiente apenas escolheu não gastar mais hoje. A simulação "
        "determinística segue inteira: mesmo grafo de orquestração, mesmo motor"
        " de políticas, mesmos controles de segurança."
    ),
    "budget.simulation": (
        "{used} de {budget} chamadas de modelo hoje. Em modo simulação elas são"
        " locais e não consomem cota de provedor nenhum; o contador é real e o "
        "limite só passa a valer em modo live."
    ),
    "budget.running_low": (
        "**A capacidade da demonstração está acabando.** Restam {remaining} de "
        "{budget} chamadas ao provedor hoje, cerca de {requests} requisições."
    ),
    "budget.live_ok": (
        "Modo live. Restam {remaining} de {budget} chamadas ao provedor no "
        "limite de hoje, cerca de {requests} requisições."
    ),
    "progress.router": "Classificando sua pergunta...",
    "progress.researcher": "Consultando os dados...",
    "progress.executor": "Preparando a ação...",
    "progress.validator": "Validando a resposta...",
    "progress.finishing": "Finalizando...",
    "progress.done": "Concluído",

    # Run states, table headings and data values, translated at display
    # time: the records keep their English keys and stored values.
    "state.not_reached": "NÃO ALCANÇADO",
    "state.running": "EXECUTANDO",
    "state.waiting": "AGUARDANDO",
    "state.success": "SUCESSO",
    "state.blocked": "BLOQUEADO",
    "state.failed": "FALHOU",
    "state.out_of_scope": "SEM RESPOSTA",
    "hint.populate": "Popule o banco com `agent-platform demo`.",
    "tech.rules": "regras",
    "tech.col_agent": "Agente",
    "tech.col_state": "Estado",
    "tech.col_detail": "Detalhe",
    "tech.col_event": "Evento",
    "tech.col_tool": "Ferramenta",
    "tech.col_status": "Status",
    "col.ID": "ID",
    "col.Name": "Nome",
    "col.Tier": "Segmento",
    "col.City": "Cidade",
    "col.State": "Estado",
    "col.Customer since": "Cliente desde",
    "col.Customer": "Cliente",
    "col.Status": "Status",
    "col.Placed on": "Data do pedido",
    "col.Items": "Itens",
    "col.Total (R$)": "Total (R$)",
    "col.SKU": "SKU",
    "col.Category": "Categoria",
    "col.Price (R$)": "Preço (R$)",
    "col.Warranty (months)": "Garantia (meses)",
    "col.Order": "Pedido",
    "col.Carrier": "Transportadora",
    "col.Shipped on": "Enviado em",
    "col.Delivered on": "Entregue em",
    "col.Article": "Artigo",
    "col.Characters": "Caracteres",
    "col.Subject": "Assunto",
    "col.Priority": "Prioridade",
    "col.Opened on": "Aberto em",
    "col.Ticket": "Ticket",
    "col.Shipped": "Enviado",
    "col.Order status": "Status do pedido",
    "val.processing": "em processamento",
    "val.shipped": "enviado",
    "val.delivered": "entregue",
    "val.cancelled": "cancelado",
    "val.returned": "devolvido",
    "val.open": "aberto",
    "val.escalated": "escalado",
    "val.resolved": "resolvido",
    "val.high": "alta",
    "val.normal": "normal",
    "val.low": "baixa",
    "val.gold": "Ouro",
    "val.platinum": "Platinum",
    "val.standard": "Standard",
    "val.in_transit": "em trânsito",
    "val.returned_to_sender": "devolvido ao remetente",
    "val.accessories": "acessórios",
    "val.audio": "áudio",
    "val.displays": "monitores",
    "val.furniture": "mobiliário",
    "val.peripherals": "periféricos",
    "val.storage": "armazenamento",
    "step.detail_tool": "ferramenta {tool}",
    "step.detail_risk": "risco {risk}",
    "step.detail_rule": "regra {rules}",
    "risk.low": "baixo",
    "risk.medium": "médio",
    "risk.high": "alto",
    "risk.critical": "crítico",
}

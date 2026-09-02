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
        "A NovaMart usa agentes de IA especializados para consultar clientes, "
        "pedidos e suporte. Um orquestrador decide quais agentes entram em "
        "ação, e um <strong>Policy Engine</strong> bloqueia operações de risco "
        "antes que qualquer ferramenta rode."
    ),
    "overview.how_it_works": "Como funciona",
    "overview.same_path": (
        "O mesmo caminho toda vez. Nenhum agente executa uma ferramenta: eles "
        "propõem, e o Policy Engine decide."
    ),
    "overview.try_it": "Experimente",
    "overview.try_lede": "Uma pergunta em linguagem natural, e a decisão que o sistema tomou.",
    "overview.run_this": "Executar esta pergunta",
    "overview.why_english": (
        "As perguntas estão em inglês porque é o idioma que o roteador e o "
        "conjunto de dados usam — é literalmente o texto que entra no sistema."
    ),
    "overview.demonstrates": "O que este projeto demonstra",
    "overview.stack_note": (
        "RAG · MCP · Kubernetes · TLS · Prometheus · 727 testes de segurança — "
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
    "company.lede": (
        "Empresa fictícia de varejo e e-commerce. Ambiente simulado usado para "
        "demonstrar como agentes de IA podem consultar clientes, pedidos, "
        "produtos e tickets e executar ações protegidas por políticas."
    ),
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
        "Faça uma pergunta em linguagem natural. O sistema decide quais agentes"
        " e ferramentas são necessários."
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
    "arch.the_agents": "Os agentes",
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
    "obs.avg_latency": "Latência média",
    "obs.recent_requests": "Requisições recentes",
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
    "nav.demo_group": "**DEMONSTRAÇÃO**",
    "nav.page": "Página",
    "nav.platform_group": "⚙ **PLATAFORMA** — os instrumentos operacionais.",
    "nav.overview": "Visão geral",
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
    "orch.placeholder": "What is the status of order ORD-1001?",
    "sidebar.simulated": (
        "{company} é uma empresa simulada. Todas as ferramentas operam em "
        "memória; nenhum sistema externo é contatado."
    ),
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
}

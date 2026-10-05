"""Os dez workflows encenados na Visão Geral, e os rótulos do diagrama. PT-BR.

Nada aqui é executado. É um roteiro: cada cenário diz por quais nós o trabalho
passa, em que estado cada nó fica, e o que o painel de rastro escreve. O
espelho em inglês é `arch_en.py`, e `test_architecture_scene.py` compara os
dois -- mesmos ids de cenário, mesmos nós, mesma sequência -- porque duas
listas de dez workflows escritas à mão divergem sozinhas.

Os agentes especializados desenhados aqui (Support, Refund, Finance...) **não
são componentes independentes deste backend**. A plataforma tem cinco agentes:
router, researcher, executor, validator e answerer. O que o diagrama representa
é a capacidade de especialização, e a cena se rotula como representação.
"""

from __future__ import annotations

from typing import Any, Final

INTEGRATION: Final[str] = "Ponto de integração"

ARCH_NODES: Final[dict[str, dict[str, str]]] = {
    "ev_ticket": {"name": "Novo ticket", "role": "Evento"},
    "ev_order": {"name": "Pedido realizado", "role": "Evento"},
    "ev_stock": {"name": "Estoque baixo", "role": "Evento"},
    "ev_refund": {"name": "Pedido de reembolso", "role": "Evento"},
    "ev_campaign": {"name": "Campanha programada", "role": "Evento"},
    "ag_support": {"name": "Support Agent", "role": "Atendimento"},
    "ag_order": {"name": "Order Agent", "role": "Pedidos"},
    "ag_inventory": {"name": "Inventory Agent", "role": "Estoque"},
    "ag_refund": {"name": "Refund Agent", "role": "Reembolsos"},
    "ag_finance": {"name": "Finance Agent", "role": "Financeiro"},
    "ag_comm": {"name": "Communication Agent", "role": "Comunicação"},
    "ag_marketing": {"name": "Marketing Agent", "role": "Marketing"},
    "ag_content": {"name": "Content Agent", "role": "Conteúdo"},
    "ag_analytics": {"name": "Analytics Agent", "role": "Análise"},
    "ag_research": {"name": "Research Agent", "role": "Pesquisa"},
    "policy": {"name": "Policy Engine", "role": "Decide antes de executar"},
    "human": {"name": "Aprovação humana", "role": "Quando a política exige"},
    "orchestrator": {"name": "NovaMart", "role": "Orquestrador"},
    "gateway": {"name": "Gateway", "role": "Único caminho até a ferramenta"},
    "validator": {"name": "Validator", "role": "Confere o resultado"},
    "t_db": {"name": "Banco de dados", "role": INTEGRATION},
    "t_mail": {"name": "E-mail / SMS", "role": INTEGRATION},
    "t_chat": {"name": "Mensageria", "role": INTEGRATION},
    "t_erp": {"name": "ERP / Financeiro", "role": INTEGRATION},
    "t_api": {"name": "APIs externas", "role": INTEGRATION},
    "t_social": {"name": "Mídias sociais", "role": INTEGRATION},
    "t_store": {"name": "Armazenamento", "role": INTEGRATION},
}

ARCH_HEADINGS: Final[dict[str, str]] = {
    "events": "Eventos / gatilhos",
    "agents": "Agentes especializados",
    "core": "Orquestração e governança",
    "tools": "Sistemas (integração)",
    "aria": "Diagrama da arquitetura do NovaMart: eventos, agentes "
            "especializados, orquestrador, Policy Engine, gateway, validador e "
            "pontos de integração.",
}

ARCH_SCENARIOS: Final[list[dict[str, Any]]] = [
    {
        "id": "refund",
        "title": "Reembolso",
        "trigger": "ev_refund",
        "steps": [
            {"node": "ag_support", "state": "running",
             "note": "Recebe a solicitação e identifica o cliente."},
            {"node": "ag_order", "state": "running",
             "note": "Localiza o pedido e o valor."},
            {"node": "ag_refund", "state": "waiting",
             "note": "Monta a proposta de reembolso.",
             "bubble": "Cliente solicitou reembolso. Posso aprovar?"},
            {"node": "orchestrator", "state": "running",
             "note": "Encaminha a proposta para decisão.",
             "bubble": "•••", "bubbleKind": "think"},
            {"node": "policy", "state": "blocked",
             "note": "Regra: reembolso acima de R$ 200 exige evidências."},
            {"node": "orchestrator", "state": "blocked",
             "note": "Devolve a decisão ao agente.",
             "bubble": "Não aprovo o reembolso. Exija imagens e vídeo do produto.",
             "bubbleKind": "verdict"},
            {"node": "validator", "state": "success",
             "note": "Confirma que nada foi executado."},
        ],
        "verdict": {"state": "blocked", "label": "Reembolso não aprovado",
                    "why": "A política exige evidências antes de devolver "
                           "valores acima do limite. Nenhuma ferramenta rodou."},
    },
    {
        "id": "stock",
        "title": "Estoque crítico",
        "trigger": "ev_stock",
        "steps": [
            {"node": "ag_inventory", "state": "running",
             "note": "Detecta o nível abaixo do mínimo."},
            {"node": "ag_analytics", "state": "running",
             "note": "Projeta a demanda das próximas semanas."},
            {"node": "ag_finance", "state": "running",
             "note": "Calcula o custo da reposição.",
             "bubble": "Reposição dentro do orçamento do trimestre."},
            {"node": "orchestrator", "state": "running",
             "note": "Consolida e propõe a compra."},
            {"node": "policy", "state": "success",
             "note": "Valor dentro do limite delegado. Autorizado."},
            {"node": "gateway", "state": "running",
             "note": "Emite a autorização de uso da ferramenta."},
            {"node": "t_erp", "state": "success",
             "note": "Ponto de integração: ordem de compra."},
            {"node": "validator", "state": "success",
             "note": "Quantidade e fornecedor conferem."},
        ],
        "verdict": {"state": "success", "label": "Reposição autorizada",
                    "why": "Dentro do limite delegado, então a política liberou "
                           "sem pedir aprovação humana."},
    },
    {
        "id": "ticket",
        "title": "Ticket de suporte",
        "trigger": "ev_ticket",
        "steps": [
            {"node": "ag_support", "state": "running",
             "note": "Classifica o assunto do ticket."},
            {"node": "ag_order", "state": "running",
             "note": "Recupera o histórico do pedido."},
            {"node": "ag_research", "state": "running",
             "note": "Busca o artigo aplicável na base de conhecimento."},
            {"node": "orchestrator", "state": "running",
             "note": "Redige a resposta a partir do que foi encontrado."},
            {"node": "validator", "state": "success",
             "note": "Confere que a resposta cita apenas dados recuperados."},
            {"node": "ag_comm", "state": "success",
             "note": "Prepara a resposta ao cliente.",
             "bubble": "Resposta pronta, com o artigo de garantia anexado."},
        ],
        "verdict": {"state": "success", "label": "Ticket respondido",
                    "why": "A resposta se apoia em registros recuperados; o "
                           "validador recusa o que não tem fonte."},
    },
    {
        "id": "sales",
        "title": "Análise de vendas",
        "trigger": None,
        "steps": [
            {"node": "orchestrator", "state": "running", "role": "Router",
             "note": "Classifica como consulta somente leitura."},
            {"node": "ag_analytics", "state": "running",
             "note": "Agrega receita por categoria."},
            {"node": "ag_research", "state": "running",
             "note": "Compara com o período anterior."},
            {"node": "validator", "state": "success",
             "note": "Números batem com a origem."},
            {"node": "orchestrator", "state": "success", "role": "Answerer",
             "note": "Escreve a resposta final.",
             "bubble": "Três categorias concentram a maior parte da receita."},
        ],
        "verdict": {"state": "success", "label": "Consulta respondida",
                    "why": "Caminho somente leitura: nenhuma ferramenta de "
                           "escrita foi proposta, então nada precisou ser "
                           "aprovado."},
    },
    {
        "id": "order",
        "title": "Processamento de pedido",
        "trigger": "ev_order",
        "steps": [
            {"node": "ag_order", "state": "running",
             "note": "Valida itens e endereço."},
            {"node": "ag_finance", "state": "running",
             "note": "Confere o pagamento."},
            {"node": "orchestrator", "state": "running",
             "note": "Encaminha para execução."},
            {"node": "gateway", "state": "running",
             "note": "Autoriza a escrita, uso único."},
            {"node": "t_db", "state": "success",
             "note": "Ponto de integração: estado do pedido."},
            {"node": "validator", "state": "success",
             "note": "Estado final consistente com o pedido."},
            {"node": "ag_comm", "state": "success",
             "note": "Confirmação preparada para o cliente."},
        ],
        "verdict": {"state": "success", "label": "Pedido confirmado",
                    "why": "A escrita passou pelo gateway com autorização de "
                           "uso único, e não pelo agente."},
    },
    {
        "id": "finance",
        "title": "Operação financeira",
        "trigger": "ev_order",
        "steps": [
            {"node": "ag_finance", "state": "running",
             "note": "Propõe um estorno acima do limite.",
             "bubble": "Estorno de R$ 4.180. Acima do meu limite."},
            {"node": "orchestrator", "state": "running",
             "note": "Encaminha para decisão."},
            {"node": "policy", "state": "waiting",
             "note": "Valor exige confirmação humana."},
            {"node": "human", "state": "waiting",
             "note": "Aguardando uma pessoa decidir.",
             "bubble": "Requer aprovação humana antes de executar."},
            {"node": "gateway", "state": "success",
             "note": "Aprovado: autorização emitida para esta ação."},
            {"node": "validator", "state": "success",
             "note": "Valor executado igual ao valor aprovado."},
        ],
        "verdict": {"state": "waiting", "label": "Parou e esperou uma pessoa",
                    "why": "Autonomia governada: acima do limite, o sistema "
                           "não decide sozinho."},
    },
    {
        "id": "campaign",
        "title": "Campanha",
        "trigger": "ev_campaign",
        "steps": [
            {"node": "ag_analytics", "state": "running",
             "note": "Seleciona o público pelo histórico de compra."},
            {"node": "ag_marketing", "state": "running",
             "note": "Define a oferta e o canal."},
            {"node": "ag_content", "state": "running",
             "note": "Redige as peças.",
             "bubble": "Três variações prontas para revisão."},
            {"node": "validator", "state": "success",
             "note": "Confere claims e preços contra o catálogo."},
            {"node": "t_mail", "state": "success",
             "note": "Ponto de integração: disparo."},
        ],
        "verdict": {"state": "success", "label": "Campanha pronta",
                    "why": "O validador compara cada afirmação com o catálogo "
                           "antes de qualquer disparo."},
    },
    {
        "id": "delivery",
        "title": "Problema de entrega",
        "trigger": "ev_ticket",
        "steps": [
            {"node": "ag_support", "state": "running",
             "note": "Cliente relata atraso."},
            {"node": "ag_order", "state": "running",
             "note": "Localiza a remessa e a transportadora."},
            {"node": "ag_research", "state": "running",
             "note": "Verifica a política de prazos."},
            {"node": "orchestrator", "state": "running",
             "note": "Decide o encaminhamento."},
            {"node": "ag_comm", "state": "success",
             "note": "Informa o novo prazo ao cliente.",
             "bubble": "Remessa em trânsito. Novo prazo comunicado."},
        ],
        "verdict": {"state": "success", "label": "Cliente informado",
                    "why": "Nenhuma alteração de registro foi necessária, "
                           "então nada precisou de autorização."},
    },
    {
        "id": "business",
        "title": "Consulta empresarial",
        "trigger": None,
        "steps": [
            {"node": "orchestrator", "state": "running", "role": "Router",
             "note": "Classifica a intenção da pergunta."},
            {"node": "ag_research", "state": "running",
             "note": "Recupera os registros relevantes."},
            {"node": "ag_analytics", "state": "running",
             "note": "Agrega o que foi recuperado."},
            {"node": "validator", "state": "success",
             "note": "Cada número tem origem rastreável."},
            {"node": "orchestrator", "state": "success", "role": "Answerer",
             "note": "Responde em linguagem natural."},
        ],
        "verdict": {"state": "success", "label": "Respondido a partir dos dados",
                    "why": "Este é o caminho que a página Orquestrador executa "
                           "de verdade, com o backend real."},
    },
    {
        "id": "blocked",
        "title": "Operação bloqueada",
        "trigger": None,
        "steps": [
            {"node": "orchestrator", "state": "running", "role": "Executor",
             "note": "Propõe apagar registros.",
             "bubble": "Excluir os pedidos do último trimestre."},
            {"node": "policy", "state": "blocked",
             "note": "Ferramenta destrutiva: DENY, sem exceção."},
            {"node": "gateway", "state": "blocked",
             "note": "Nenhuma autorização emitida."},
            {"node": "validator", "state": "success",
             "note": "Confirma que o dado continua intacto."},
        ],
        "verdict": {"state": "blocked", "label": "Recusado antes de executar",
                    "why": "A decisão vem dos metadados da ferramenta, não do "
                           "texto do pedido — não há como convencê-la."},
    },
]

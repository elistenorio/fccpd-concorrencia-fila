"""
Eventos e regras de alerta.

Um evento é um fato com horário: "a memória do container worker-1 passou de
80%", "a api voltou ao ar", "o postgres foi morto por falta de memória".
As regras ficam todas aqui, separadas da coleta, para serem testáveis sem
Docker e sem ``/proc``.

Severidade: ``info`` < ``alerta`` < ``critico``.
"""

import time

# Acima deste percentual de uso de memória, o container entra em alerta.
LIMITE_MEMORIA_PCT = 80.0
# Idem para a memória da máquina (nível 1).
LIMITE_MEMORIA_HOST_PCT = 90.0


class Evento:
    """
    Fato observado pelo coletor.

    ``chave`` identifica a origem (``"container:worker-1"``,
    ``"servico:api"``, ``"host"``) e serve para não repetir o mesmo alerta a
    cada amostra: o coletor só registra um evento quando a situação muda.
    """

    __slots__ = ("timestamp", "nivel", "chave", "severidade", "mensagem", "dados")

    def __init__(self, nivel, chave, severidade, mensagem, timestamp=None, dados=None):
        self.timestamp = time.time() if timestamp is None else timestamp
        self.nivel = nivel
        self.chave = chave
        self.severidade = severidade
        self.mensagem = mensagem
        self.dados = dados or {}

    def __repr__(self):
        return f"<Evento {self.severidade} {self.chave}: {self.mensagem}>"

    def para_dict(self):
        return {
            "timestamp": self.timestamp,
            "nivel": self.nivel,
            "chave": self.chave,
            "severidade": self.severidade,
            "mensagem": self.mensagem,
            "dados": self.dados,
        }


def _formatar_bytes(valor):
    if valor is None:
        return "?"
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if abs(valor) < 1024 or unidade == "TB":
            return f"{valor:.0f} {unidade}" if unidade == "B" else f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{valor:.1f} TB"


def eventos_do_host(amostra, estado_anterior, limite_pct=LIMITE_MEMORIA_HOST_PCT):
    """
    Alertas do nível 1. ``estado_anterior`` é o dict com a última situação
    conhecida do host (ou ``None`` na primeira amostra), usado para só gerar
    evento quando o estado muda.
    """
    eventos = []
    memoria = amostra.get("memoria") or {}
    uso = memoria.get("uso_pct")
    if uso is None:
        return eventos, {"memoria_em_alerta": False}

    em_alerta = uso >= limite_pct
    antes = bool((estado_anterior or {}).get("memoria_em_alerta"))
    if em_alerta and not antes:
        eventos.append(
            Evento(
                nivel=1,
                chave="host",
                severidade="alerta",
                mensagem=(
                    f"Memória da máquina em {uso}% (limite {limite_pct:.0f}%), "
                    f"{_formatar_bytes(memoria.get('disponivel_bytes'))} disponíveis"
                ),
                timestamp=amostra.get("timestamp"),
                dados={"uso_pct": uso},
            )
        )
    elif antes and not em_alerta:
        eventos.append(
            Evento(
                nivel=1,
                chave="host",
                severidade="info",
                mensagem=f"Memória da máquina voltou ao normal ({uso}%)",
                timestamp=amostra.get("timestamp"),
                dados={"uso_pct": uso},
            )
        )
    return eventos, {"memoria_em_alerta": em_alerta}


def eventos_do_container(container, estado_anterior, limite_pct=LIMITE_MEMORIA_PCT):
    """
    Alertas do nível 2 para um container: memória acima do limite, OOMKilled,
    reinício e mudança de status. Devolve (eventos, nova_situacao).
    """
    eventos = []
    chave = f"container:{container['nome']}"
    anterior = estado_anterior or {}
    memoria = container.get("memoria") or {}
    uso = memoria.get("uso_pct")

    em_alerta = uso is not None and uso >= limite_pct
    if em_alerta and not anterior.get("memoria_em_alerta"):
        eventos.append(
            Evento(
                nivel=2,
                chave=chave,
                severidade="alerta",
                mensagem=(
                    f"{container['nome']}: memória em {uso}% do limite "
                    f"({_formatar_bytes(memoria.get('usada_bytes'))} de {_formatar_bytes(memoria.get('limite_bytes'))})"
                ),
                timestamp=container.get("timestamp"),
                dados={"uso_pct": uso},
            )
        )
    elif anterior.get("memoria_em_alerta") and uso is not None and not em_alerta:
        eventos.append(
            Evento(
                nivel=2,
                chave=chave,
                severidade="info",
                mensagem=f"{container['nome']}: memória voltou ao normal ({uso}%)",
                timestamp=container.get("timestamp"),
                dados={"uso_pct": uso},
            )
        )

    # OOMKilled: o kernel matou o container por falta de memória. É o evento
    # mais grave do nível 2, e fica marcado no estado até o container reiniciar.
    if container.get("oom_killed") and not anterior.get("oom_killed"):
        eventos.append(
            Evento(
                nivel=2,
                chave=chave,
                severidade="critico",
                mensagem=f"{container['nome']}: morto por falta de memória (OOMKilled)",
                timestamp=container.get("timestamp"),
                dados={"limite_bytes": memoria.get("limite_bytes")},
            )
        )

    reinicios = container.get("reinicios")
    reinicios_antes = anterior.get("reinicios")
    if reinicios is not None and reinicios_antes is not None and reinicios > reinicios_antes:
        eventos.append(
            Evento(
                nivel=2,
                chave=chave,
                severidade="alerta",
                mensagem=f"{container['nome']}: reiniciou ({reinicios}ª vez)",
                timestamp=container.get("timestamp"),
                dados={"reinicios": reinicios},
            )
        )

    status = container.get("status")
    status_antes = anterior.get("status")
    if status_antes is not None and status != status_antes:
        severidade = "info" if status == "running" else "alerta"
        eventos.append(
            Evento(
                nivel=2,
                chave=chave,
                severidade=severidade,
                mensagem=f"{container['nome']}: {status_antes} -> {status}",
                timestamp=container.get("timestamp"),
                dados={"status": status},
            )
        )

    situacao = {
        "memoria_em_alerta": em_alerta,
        "oom_killed": bool(container.get("oom_killed")),
        "reinicios": reinicios if reinicios is not None else reinicios_antes,
        "status": status,
    }
    return eventos, situacao


def eventos_do_servico(servico):
    """
    Alertas do nível 3. O próprio coletor do nível 3 já detecta a transição
    (campo ``mudou``), porque é ele que guarda desde quando o serviço está
    naquele estado.
    """
    estado = servico["estado"]
    primeira = servico.get("primeira_observacao")
    # Primeira observação só gera evento se o serviço já estiver com problema:
    # "subiu e está no ar" não é notícia, "subiu e está fora" é.
    if not servico.get("mudou") and not (primeira and estado != "no ar"):
        return []

    severidade = "info" if estado == "no ar" else "critico"
    detalhe = f" ({servico['detalhe']})" if servico.get("detalhe") else ""
    if primeira:
        mensagem = f"{servico['nome']}: {estado} no primeiro health check{detalhe}"
    else:
        mensagem = f"{servico['nome']}: {servico['estado_anterior']} -> {estado}{detalhe}"
    return [
        Evento(
            nivel=3,
            chave=f"servico:{servico['nome']}",
            severidade=severidade,
            mensagem=mensagem,
            timestamp=servico.get("desde"),
            dados={"codigo": servico.get("codigo"), "tempo_resposta_ms": servico.get("tempo_resposta_ms")},
        )
    ]

"""
Estado do monitor: uso das estruturas próprias (janela fixa, hash, árvore de
eventos, fila de alertas) e as regras de alerta.
"""

import math
import time

from monitor.estado import EstadoMonitor
from monitor.eventos import Evento, eventos_do_container, eventos_do_host, eventos_do_servico


def amostra_host(uso_pct, timestamp=1000.0):
    total = 8 * 1024**3
    usada = int(total * uso_pct / 100)
    return {
        "timestamp": timestamp,
        "memoria": {
            "total_bytes": total,
            "usada_bytes": usada,
            "disponivel_bytes": total - usada,
            "uso_pct": uso_pct,
        },
    }


def amostra_containers(containers, timestamp=1000.0):
    return {"timestamp": timestamp, "disponivel": True, "motivo": None, "containers": containers}


def container(nome="worker-1", uso_pct=10.0, status="running", reinicios=0, oom=False):
    limite = 512 * 1024**2
    return {
        "id": "abc123",
        "nome": nome,
        "servico": nome.rsplit("-", 1)[0],
        "status": status,
        "reinicios": reinicios,
        "oom_killed": oom,
        "cpu_pct": 1.0,
        "memoria": {
            "usada_bytes": int(limite * uso_pct / 100),
            "limite_bytes": limite,
            "uso_pct": uso_pct,
        },
    }


# ------------------------------------------------------------------- estruturas


def test_janela_de_amostras_nao_cresce_alem_da_capacidade():
    # É o motivo de usar FilaCircular: o monitor roda por horas sem estourar
    # a memória, descartando a amostra mais antiga.
    estado = EstadoMonitor(janela=5)
    for i in range(20):
        estado.aplicar(1, amostra_host(10.0, timestamp=1000.0 + i))
    historico = estado.historico(1)
    assert len(historico) == 5
    assert [a["timestamp"] for a in historico] == [1015.0, 1016.0, 1017.0, 1018.0, 1019.0]


def test_estado_do_container_e_acessado_por_chave():
    estado = EstadoMonitor()
    estado.aplicar(2, amostra_containers([container("api-1"), container("worker-2")]))
    assert estado.container("api-1")["nome"] == "api-1"
    assert estado.container("worker-2")["servico"] == "worker"
    assert estado.container("nao-existe") is None


def test_container_removido_sai_do_estado():
    estado = EstadoMonitor()
    estado.aplicar(2, amostra_containers([container("api-1"), container("worker-2")]))
    estado.aplicar(2, amostra_containers([container("api-1")]))
    assert estado.container("worker-2") is None
    assert estado.container("api-1") is not None


def test_containers_vem_ordenados_por_memoria():
    estado = EstadoMonitor()
    estado.aplicar(
        2,
        amostra_containers(
            [container("a", uso_pct=20.0), container("b", uso_pct=70.0), container("c", uso_pct=45.0)]
        ),
    )
    nomes = [c["nome"] for c in estado.containers(ordenar_por="memoria")]
    assert nomes == ["b", "c", "a"]


def test_busca_binaria_acha_o_corte_do_historico():
    estado = EstadoMonitor(janela=50)
    for i in range(10):
        estado.aplicar(1, amostra_host(10.0, timestamp=1000.0 + i))
    # Horário que existe na janela.
    assert [a["timestamp"] for a in estado.historico(1, desde=1007.0)] == [1007.0, 1008.0, 1009.0]
    # Horário que não existe: pega a primeira amostra posterior.
    assert [a["timestamp"] for a in estado.historico(1, desde=1006.5)] == [1007.0, 1008.0, 1009.0]
    # Depois da última: nada.
    assert estado.historico(1, desde=2000.0) == []


def test_processo_e_encontrado_por_nome_na_ultima_amostra():
    estado = EstadoMonitor()
    estado.aplicar(
        1,
        {
            "timestamp": 1000.0,
            "memoria": {"uso_pct": 10.0, "total_bytes": 100, "usada_bytes": 10, "disponivel_bytes": 90},
            "processos": [{"pid": 1, "nome": "uvicorn"}, {"pid": 2, "nome": "postgres"}],
        },
    )
    assert estado.processo_por_nome("postgres")["pid"] == 2
    assert estado.processo_por_nome("mysql") is None


# ---------------------------------------------------------------------- eventos


def test_eventos_com_o_mesmo_horario_nao_se_sobrescrevem():
    # A chave da árvore é (horário, sequência): dois eventos no mesmo instante
    # são chaves diferentes, senão um apagaria o outro.
    estado = EstadoMonitor()
    for i in range(3):
        estado.registrar_evento_proprio(
            Evento(nivel=1, chave="host", severidade="alerta", mensagem=f"evento {i}", timestamp=1000.0)
        )
    assert estado.resumo()["eventos_guardados"] == 3


def test_consulta_de_eventos_por_intervalo():
    estado = EstadoMonitor()
    for i in range(10):
        estado.registrar_evento_proprio(
            Evento(nivel=1, chave="host", severidade="info", mensagem=f"e{i}", timestamp=1000.0 + i)
        )
    no_intervalo = estado.eventos(desde=1003.0, ate=1005.0)
    assert [e.mensagem for e in no_intervalo] == ["e5", "e4", "e3"]  # mais recente primeiro


def test_eventos_do_mesmo_instante_mantem_a_ordem_de_chegada():
    # merge sort é estável: com o mesmo timestamp, quem chegou primeiro
    # continua primeiro (aqui, na ordem decrescente, aparece por último).
    estado = EstadoMonitor()
    for i in range(4):
        estado.registrar_evento_proprio(
            Evento(nivel=2, chave="c", severidade="info", mensagem=f"e{i}", timestamp=1000.0)
        )
    assert [e.mensagem for e in estado.eventos()] == ["e0", "e1", "e2", "e3"]


def test_arvore_de_eventos_nao_degenera_com_horarios_crescentes():
    # Timestamps sempre crescentes transformariam a BST numa lista (altura = n).
    # O rebalanceamento mantém a altura perto do log2(n).
    estado = EstadoMonitor(max_eventos=300)
    for i in range(200):
        estado.registrar_evento_proprio(
            Evento(nivel=1, chave="host", severidade="info", mensagem=f"e{i}", timestamp=1000.0 + i)
        )
    resumo = estado.resumo()
    assert resumo["eventos_guardados"] == 200
    assert resumo["altura_arvore_eventos"] <= 2 * math.ceil(math.log2(200)) + 2
    # E a consulta continua devolvendo tudo em ordem.
    assert len(estado.eventos(limite=None)) == 200


def test_eventos_antigos_saem_quando_passa_do_limite():
    estado = EstadoMonitor(max_eventos=10)
    for i in range(25):
        estado.registrar_evento_proprio(
            Evento(nivel=1, chave="host", severidade="info", mensagem=f"e{i}", timestamp=1000.0 + i)
        )
    guardados = [e.mensagem for e in estado.eventos(limite=None)]
    assert len(guardados) == 10
    assert guardados[0] == "e24"  # os mais recentes ficam
    assert "e0" not in guardados


def test_alertas_saem_da_fila_uma_vez_so():
    estado = EstadoMonitor()
    estado.aplicar(1, amostra_host(95.0))
    primeira = estado.alertas_pendentes()
    assert len(primeira) == 1
    assert "95" in primeira[0].mensagem
    assert estado.alertas_pendentes() == []  # já foi entregue


def test_evento_info_nao_entra_na_fila_de_alertas():
    estado = EstadoMonitor()
    estado.registrar_evento_proprio(
        Evento(nivel=1, chave="host", severidade="info", mensagem="tudo bem", timestamp=1000.0)
    )
    assert estado.alertas_pendentes() == []


# ------------------------------------------------------------- regras de alerta


def test_alerta_de_memoria_do_host_dispara_uma_vez_e_depois_normaliza():
    anterior = None
    eventos, anterior = eventos_do_host(amostra_host(50.0), anterior)
    assert eventos == []

    eventos, anterior = eventos_do_host(amostra_host(95.0), anterior)
    assert len(eventos) == 1 and eventos[0].severidade == "alerta"

    # Continua alto: não repete o alerta a cada amostra.
    eventos, anterior = eventos_do_host(amostra_host(96.0), anterior)
    assert eventos == []

    eventos, anterior = eventos_do_host(amostra_host(40.0), anterior)
    assert len(eventos) == 1 and eventos[0].severidade == "info"


def test_alerta_de_memoria_do_container():
    eventos, situacao = eventos_do_container(container(uso_pct=85.0), None)
    assert len(eventos) == 1
    assert eventos[0].severidade == "alerta"
    assert "85" in eventos[0].mensagem
    assert situacao["memoria_em_alerta"] is True


def test_oom_killed_gera_evento_critico():
    eventos, _ = eventos_do_container(container(oom=True), {"status": "running", "reinicios": 0})
    severidades = [e.severidade for e in eventos]
    assert "critico" in severidades
    assert any("OOMKilled" in e.mensagem for e in eventos)


def test_reinicio_de_container_gera_alerta():
    anterior = {"status": "running", "reinicios": 2, "memoria_em_alerta": False, "oom_killed": False}
    eventos, _ = eventos_do_container(container(reinicios=3), anterior)
    assert any("reiniciou" in e.mensagem for e in eventos)


def test_mudanca_de_status_de_container():
    anterior = {"status": "running", "reinicios": 0, "memoria_em_alerta": False, "oom_killed": False}
    eventos, _ = eventos_do_container(container(status="exited"), anterior)
    assert any("running -> exited" in e.mensagem and e.severidade == "alerta" for e in eventos)


def test_primeira_amostra_de_container_nao_gera_evento_de_status():
    eventos, _ = eventos_do_container(container(), None)
    assert eventos == []


def test_servico_que_ja_nasce_fora_do_ar_aparece_na_linha_do_tempo():
    # Sem transição anterior, mas é exatamente o caso que precisa aparecer.
    eventos = eventos_do_servico(
        {
            "nome": "api",
            "estado": "fora",
            "mudou": False,
            "primeira_observacao": True,
            "detalhe": "sem resposta em 2s",
            "desde": 1000.0,
        }
    )
    assert len(eventos) == 1
    assert eventos[0].severidade == "critico"
    assert "primeiro health check" in eventos[0].mensagem


def test_servico_no_ar_desde_o_inicio_nao_gera_evento():
    eventos = eventos_do_servico(
        {"nome": "api", "estado": "no ar", "mudou": False, "primeira_observacao": True, "desde": 1000.0}
    )
    assert eventos == []


def test_queda_e_volta_de_servico_geram_eventos():
    caidos = eventos_do_servico(
        {
            "nome": "recomendacoes",
            "estado": "fora",
            "estado_anterior": "no ar",
            "mudou": True,
            "primeira_observacao": False,
            "desde": 1000.0,
        }
    )
    voltou = eventos_do_servico(
        {
            "nome": "recomendacoes",
            "estado": "no ar",
            "estado_anterior": "fora",
            "mudou": True,
            "primeira_observacao": False,
            "desde": 1030.0,
        }
    )
    assert caidos[0].severidade == "critico" and "no ar -> fora" in caidos[0].mensagem
    assert voltou[0].severidade == "info" and "fora -> no ar" in voltou[0].mensagem


def test_resumo_tem_os_numeros_do_painel():
    estado = EstadoMonitor()
    estado.aplicar(1, amostra_host(10.0, timestamp=time.time()))
    resumo = estado.resumo()
    assert resumo["amostras_recebidas"] == 1
    assert resumo["janela_amostras"] > 0

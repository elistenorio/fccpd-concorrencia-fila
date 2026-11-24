"""
Nível 3 — serviços: health check, tempo de resposta e tempo no ar.

Cada serviço monitorado é um ``GET`` numa URL de saúde. O que interessa não é
só o estado atual, mas a transição: "a api caiu às 16:02 e voltou às 16:03" é o
que alimenta a linha do tempo de eventos do painel. Por isso o coletor guarda,
por serviço, desde quando ele está no estado atual.
"""

import time

import httpx

# Serviços padrão deste projeto. Sobrescrito por MONITOR_SERVICOS no ambiente
# ("nome=url,nome=url").
SERVICOS_PADRAO = (
    ("api", "http://api:8000/"),
    ("recomendacoes", "http://recomendacoes:8001/"),
)


def parse_servicos(texto):
    """``"api=http://api:8000/,rec=http://rec:8001/"`` -> tupla de pares."""
    servicos = []
    for item in texto.split(","):
        item = item.strip()
        if not item:
            continue
        nome, _, url = item.partition("=")
        if url:
            servicos.append((nome.strip(), url.strip()))
    return tuple(servicos)


class ColetorServicos:
    """
    Uma amostra do nível 3 por chamada de :meth:`coletar`.

    O timeout é curto (2 s) de propósito: o coletor não pode ficar preso num
    serviço fora do ar, senão para de medir os outros. Um serviço que não
    responde dentro do timeout conta como fora, que é como o usuário o vê.
    """

    def __init__(self, servicos=SERVICOS_PADRAO, timeout=2.0):
        self._servicos = tuple(servicos)
        self._timeout = timeout
        self._cliente = httpx.Client(timeout=timeout)
        # nome -> (estado, instante em que entrou nesse estado)
        self._desde = {}

    def fechar(self):
        self._cliente.close()

    def coletar(self):
        agora = time.time()
        resultados = []
        for nome, url in self._servicos:
            resultados.append(self._verificar(nome, url, agora))
        return {"timestamp": agora, "servicos": resultados}

    def _verificar(self, nome, url, agora):
        inicio = time.perf_counter()
        estado, detalhe, codigo = "fora", None, None
        try:
            resposta = self._cliente.get(url)
            codigo = resposta.status_code
            estado = "no ar" if resposta.status_code < 500 else "degradado"
        except httpx.TimeoutException:
            detalhe = f"sem resposta em {self._timeout:.0f}s"
        except httpx.HTTPError as erro:
            detalhe = type(erro).__name__
        tempo_ms = round((time.perf_counter() - inicio) * 1000, 1)

        anterior, desde = self._desde.get(nome, (None, agora))
        mudou = anterior is not None and anterior != estado
        if anterior != estado:
            desde = agora
            self._desde[nome] = (estado, desde)

        return {
            "nome": nome,
            "url": url,
            "estado": estado,
            "codigo": codigo,
            "detalhe": detalhe,
            "tempo_resposta_ms": tempo_ms,
            "desde": desde,
            "tempo_no_ar_s": round(agora - desde, 1),
            "estado_anterior": anterior if mudou else None,
            "mudou": mudou,
            # Serviço que já estava fora quando o monitor subiu não teve
            # transição, mas precisa aparecer na linha do tempo: sem isto, a
            # tela mostraria "fora" sem nenhum evento explicando desde quando.
            "primeira_observacao": anterior is None,
        }

"""
Sondas do nível 3 (serviços): cada uma fala o protocolo real do serviço e mede
o tempo de resposta. Só biblioteca padrão.

    http://host:porta/caminho   GET; ok se o status for 2xx
    redis://host:porta          comando PING do protocolo RESP; ok se vier +PONG
    pg://host:porta             SSLRequest do protocolo do PostgreSQL; ok se vier 'S' ou 'N'
    tcp://host:porta            só o connect()

A resolução de nome entra na medida. Num container parado, o DNS do Docker
demora ~7 s e ignora o timeout do socket (visto na U2, app/recomendacoes_cliente.py),
então quem chama roda a sonda numa thread com prazo (ver Coletor._produtor_servicos).
"""

import http.client
import socket
import struct
import time
from urllib.parse import urlsplit

SSL_REQUEST = struct.pack("!ii", 8, 80877103)  # tamanho + código mágico do SSLRequest


class Sonda:
    __slots__ = ("nome", "url", "esquema", "host", "porta", "caminho")

    def __init__(self, nome, url):
        partes = urlsplit(url)
        self.nome = nome
        self.url = url
        self.esquema = partes.scheme
        self.host = partes.hostname
        self.porta = partes.port or {"http": 80, "redis": 6379, "pg": 5432}.get(partes.scheme, 0)
        self.caminho = partes.path or "/"

    def medir(self, timeout=1.0):
        """(ok, latência em ms, detalhe). Nunca levanta exceção."""
        inicio = time.perf_counter()
        try:
            detalhe = self._executar(timeout)
            return True, (time.perf_counter() - inicio) * 1000, detalhe
        except Exception as erro:  # qualquer falha conta como perda
            return False, (time.perf_counter() - inicio) * 1000, f"{type(erro).__name__}: {erro}"[:120]

    def _executar(self, timeout):
        if self.esquema == "http":
            conexao = http.client.HTTPConnection(self.host, self.porta, timeout=timeout)
            try:
                conexao.request("GET", self.caminho, headers={"User-Agent": "origem-monitor"})
                resposta = conexao.getresponse()
                resposta.read()
                if not 200 <= resposta.status < 300:
                    raise RuntimeError(f"HTTP {resposta.status}")
                return f"HTTP {resposta.status}"
            finally:
                conexao.close()
        with socket.create_connection((self.host, self.porta), timeout=timeout) as s:
            if self.esquema == "redis":
                s.sendall(b"PING\r\n")
                resposta = s.recv(64)
                if not resposta.startswith(b"+PONG"):
                    raise RuntimeError(f"resposta {resposta[:20]!r}")
                return "+PONG"
            if self.esquema == "pg":
                s.sendall(SSL_REQUEST)
                resposta = s.recv(1)
                if resposta not in (b"S", b"N"):
                    raise RuntimeError(f"resposta {resposta!r}")
                return f"SSLRequest → {resposta.decode()}"
            return "connect ok"


def sondas_da_config(texto):
    """'api=http://api:8000/,redis=redis://redis:6379' -> array de Sonda."""
    itens = [parte.strip() for parte in texto.split(",") if parte.strip()]
    resultado = [None] * len(itens)
    for i, item in enumerate(itens):
        nome, url = item.split("=", 1)
        resultado[i] = Sonda(nome.strip(), url.strip())
    return resultado

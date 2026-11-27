#!/usr/bin/env bash
# Degrada a rede de um serviço do compose com tc netem (atraso, jitter, perda).
#
#   rede/degradar.sh recomendacoes                       # padrão: 200 ms ± 100 ms, 10% de perda
#   rede/degradar.sh recomendacoes "delay 50ms 10ms"     # regra netem qualquer
#   rede/degradar.sh recomendacoes limpar                # volta ao normal
#
# Como funciona: sobe um container descartável no MESMO namespace de rede do
# serviço (--net container:<id>) com NET_ADMIN e aplica a regra na eth0. A regra
# fica na interface do serviço depois que o auxiliar sai; as imagens não mudam.
# netem age na SAÍDA da interface: no recomendacoes, atrasa e perde as respostas
# que ele manda para a api (e para as sondas do monitor).
set -euo pipefail

servico="${1:?uso: rede/degradar.sh <servico> [regra netem | limpar]}"
shift
regra="${*:-delay 200ms 100ms distribution normal loss 10%}"

id="$(docker compose ps -q "$servico" | head -n 1)"
[ -n "$id" ] || { echo "serviço $servico não está rodando" >&2; exit 1; }
imagem="$(docker inspect -f '{{.Config.Image}}' "$id")"

if [ "$regra" = "limpar" ]; then
  comando="tc qdisc del dev eth0 root 2>/dev/null || true"
else
  comando="tc qdisc replace dev eth0 root netem $regra"
fi

docker run --rm --net "container:$id" --cap-add NET_ADMIN --entrypoint sh "$imagem" -c "
  command -v tc >/dev/null || (apt-get update -qq && apt-get install -y -qq iproute2 >/dev/null)
  $comando
  tc qdisc show dev eth0"

#!/usr/bin/env bash
#
# monitor.sh — Lab 09 (PI3, Prática 1): monitoramento básico de um processo.
#
# Mede CPU e memória (via top) e RSS, threads e estado (via ps) de um processo
# a cada intervalo, grava tudo em CSV, registra alertas quando a CPU passa do
# limite e imprime um resumo no final.
#
# Uso:
#   ./monitor.sh <PID|nome> [intervalo_s] [amostras] [limite_cpu]
#   ./monitor.sh postgres            # 30 amostras de 1 s, alerta acima de 80%
#   ./monitor.sh 1234 0.5 60 50      # PID 1234, a cada 0,5 s, 60 amostras, alerta acima de 50%
#
# Saída (no diretório atual):
#   medicoes_<nome>_<data>.csv  timestamp,pid,cpu,mem,rss_kb,threads,estado
#   alertas.log                 uma linha por amostra acima do limite (acumula entre execuções)

# Números com ponto decimal, independente do idioma do sistema; sem isso o
# top/ps em pt_BR escrevem "12,5" e a vírgula quebra as colunas do CSV.
export LC_ALL=C

set -u

if [ $# -lt 1 ]; then
    echo "Uso: $0 <PID|nome> [intervalo_s] [amostras] [limite_cpu]" >&2
    exit 1
fi

ALVO=$1
INTERVALO=${2:-1}
AMOSTRAS=${3:-30}
LIMITE_CPU=${4:-80}
ALERTAS=alertas.log

# --- Descobrir o PID ---
if [[ $ALVO =~ ^[0-9]+$ ]]; then
    PID=$ALVO
else
    # -o: o processo mais antigo com esse nome (o "pai", em programas com vários processos).
    PID=$(pgrep -o "$ALVO")
fi

if [ -z "${PID:-}" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "Processo '$ALVO' não encontrado." >&2
    exit 1
fi

NOME=$(ps -p "$PID" -o comm= 2>/dev/null | tr -d ' ')
CSV="medicoes_${NOME}_$(date +%Y%m%d_%H%M%S).csv"
echo "timestamp,pid,cpu,mem,rss_kb,threads,estado" > "$CSV"

echo "Monitorando $NOME (PID $PID): $AMOSTRAS amostras a cada ${INTERVALO}s, alerta acima de ${LIMITE_CPU}% de CPU"
echo "Gravando em $CSV"

# --- Resumo (também roda se o usuário apertar Ctrl+C) ---
resumo() {
    awk -F, 'NR > 1 {
        n++; cpu += $3; rss += $5
        if (n == 1 || $3 > cpu_max) cpu_max = $3
        if ($6 > thr_max) thr_max = $6
    }
    END {
        print ""
        print "===== Resumo ====="
        if (n == 0) { print "Nenhuma amostra coletada."; exit }
        printf "Amostras:            %d\n", n
        printf "CPU média:           %.2f%%\n", cpu / n
        printf "CPU máxima:          %.2f%%\n", cpu_max
        printf "RSS médio:           %.0f KB\n", rss / n
        printf "Máximo de threads:   %d\n", thr_max
    }' "$CSV"
}
trap 'resumo; exit 0' INT TERM

# --- Coleta ---
for ((i = 1; i <= AMOSTRAS; i++)); do
    # O top precisa de duas leituras para calcular a CPU (a 1ª mostra a média
    # desde que o processo nasceu). Com -n 2 -d INTERVALO, a espera entre as
    # duas leituras É o intervalo de amostragem, então não precisa de sleep.
    # -w 512: largura fixa; sem terminal (cron, pipe, container) o top não
    # consegue descobrir a largura e reclama ou corta a linha.
    LINHA_TOP=$(top -b -w 512 -n 2 -d "$INTERVALO" -p "$PID" 2>/dev/null | awk -v pid="$PID" '$1 == pid { ultima = $0 } END { print ultima }')

    if [ -z "$LINHA_TOP" ] || ! kill -0 "$PID" 2>/dev/null; then
        echo "Processo $PID terminou; encerrando a coleta."
        break
    fi

    # Colunas padrão do top: PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND
    CPU=$(echo "$LINHA_TOP" | awk '{ print $9 }')
    MEM=$(echo "$LINHA_TOP" | awk '{ print $10 }')

    read -r RSS THREADS ESTADO <<< "$(ps -p "$PID" -o rss=,nlwp=,stat= 2>/dev/null)"
    if [ -z "${RSS:-}" ]; then
        echo "Processo $PID terminou; encerrando a coleta."
        break
    fi

    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "$TIMESTAMP,$PID,$CPU,$MEM,$RSS,$THREADS,$ESTADO" >> "$CSV"
    printf "[%2d/%d] %s cpu=%6s%% mem=%5s%% rss=%8s KB threads=%3s estado=%s\n" \
        "$i" "$AMOSTRAS" "$TIMESTAMP" "$CPU" "$MEM" "$RSS" "$THREADS" "$ESTADO"

    # Comparação de decimais: o bash só compara inteiros, então quem decide é o awk.
    if awk -v c="$CPU" -v l="$LIMITE_CPU" 'BEGIN { exit !(c > l) }'; then
        ALERTA="$TIMESTAMP ALERTA $NOME pid=$PID cpu=${CPU}% acima do limite de ${LIMITE_CPU}%"
        echo "$ALERTA" >> "$ALERTAS"
        echo "  >> $ALERTA"
    fi
done

resumo

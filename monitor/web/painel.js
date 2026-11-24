// Comum às três telas: busca na API, formatação, faixa de alertas e gráficos.
// Sem framework e sem build: o painel é servido como arquivo estático pelo
// próprio serviço do monitor.

const INTERVALO_MS = 2000;

async function buscar(caminho) {
  const resposta = await fetch(caminho, { cache: "no-store" });
  if (!resposta.ok) throw new Error(`${caminho} -> ${resposta.status}`);
  return resposta.json();
}

function formatarBytes(valor) {
  if (valor === null || valor === undefined) return "—";
  const unidades = ["B", "KB", "MB", "GB", "TB"];
  let v = valor;
  let i = 0;
  while (Math.abs(v) >= 1024 && i < unidades.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 ? v.toFixed(0) : v.toFixed(1)} ${unidades[i]}`;
}

function formatarDuracao(segundos) {
  if (segundos === null || segundos === undefined) return "—";
  const s = Math.floor(segundos);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}min ${s % 60}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}min`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

function formatarHora(timestamp) {
  if (!timestamp) return "—";
  return new Date(timestamp * 1000).toLocaleTimeString("pt-BR");
}

function pct(valor) {
  return valor === null || valor === undefined ? "—" : `${valor.toFixed(1)}%`;
}

function classePorUso(valor, limite) {
  if (valor === null || valor === undefined) return "";
  if (valor >= limite) return "critico";
  if (valor >= limite * 0.75) return "alerta";
  return "";
}

function barra(valor, limite = 80) {
  const largura = valor === null || valor === undefined ? 0 : Math.min(100, valor);
  return `<span class="barra"><i class="${classePorUso(valor, limite)}" style="width:${largura}%"></i></span>`;
}

function escapar(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Gráfico de linha em SVG, desenhado à mão: a série vem como [[ts, valor], ...].
function desenharGrafico(elemento, pontos, { maximo = 100, sufixo = "%", decimais = 1 } = {}) {
  const titulo = elemento.querySelector("b");
  if (!pontos || pontos.length === 0) {
    if (titulo) titulo.textContent = "—";
    elemento.querySelector("svg").innerHTML = "";
    return;
  }
  const ultimo = pontos[pontos.length - 1][1];
  if (titulo) titulo.textContent = `${ultimo.toFixed(decimais)}${sufixo}`;

  const teto = Math.max(maximo, ...pontos.map((p) => p[1])) || 1;
  const largura = 100;
  const altura = 100;
  const passo = pontos.length > 1 ? largura / (pontos.length - 1) : 0;
  const coordenadas = pontos.map((p, i) => [i * passo, altura - (p[1] / teto) * altura]);
  const linha = coordenadas.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `${linha} L${largura},${altura} L0,${altura} Z`;

  const svg = elemento.querySelector("svg");
  svg.setAttribute("viewBox", `0 0 ${largura} ${altura}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.innerHTML =
    `<path d="${area}" fill="rgba(91,156,248,0.15)" />` +
    `<path d="${linha}" fill="none" stroke="#5b9cf8" stroke-width="1.2" vector-effect="non-scaling-stroke" />`;
}

// Faixa de alertas: /api/alertas consome a fila, então cada alerta aparece uma
// vez. Mantemos os últimos na tela por um tempo para dar para ler.
const alertasNaTela = [];

async function atualizarAlertas() {
  const faixa = document.getElementById("faixa-alertas");
  if (!faixa) return;
  try {
    const { alertas } = await buscar("/api/alertas");
    for (const alerta of alertas) alertasNaTela.unshift(alerta);
    while (alertasNaTela.length > 5) alertasNaTela.pop();
    faixa.innerHTML = alertasNaTela
      .map(
        (a) => `<div class="aviso ${a.severidade === "critico" ? "critico" : ""}">
          <time>${formatarHora(a.timestamp)}</time>
          <span>${escapar(a.mensagem)}</span>
        </div>`
      )
      .join("");
  } catch (erro) {
    // Painel sem alertas é melhor que painel quebrado.
  }
}

async function atualizarEstadoColeta() {
  const alvo = document.getElementById("estado-coleta");
  if (!alvo) return;
  try {
    const { coletor, estado } = await buscar("/api/resumo");
    const vivas = coletor.threads.filter((t) => t.viva).length;
    alvo.innerHTML =
      `<b>${vivas}/${coletor.threads.length}</b> threads · ` +
      `buffer <b>${coletor.buffer.ocupado}/${coletor.buffer.capacidade}</b> · ` +
      `<b>${estado.amostras_recebidas}</b> amostras · ` +
      `<b>${estado.eventos_guardados}</b> eventos (altura ${estado.altura_arvore_eventos}) · ` +
      `no ar ${formatarDuracao(estado.monitor_no_ar_s)}`;
  } catch (erro) {
    alvo.innerHTML = '<b style="color:#f85149">coletor sem resposta</b>';
  }
}

function marcarNavegacao() {
  const atual = window.location.pathname.replace(/\/$/, "") || "/painel";
  document.querySelectorAll("nav a").forEach((link) => {
    const destino = link.getAttribute("href").replace(/\/$/, "");
    if (destino === atual) link.setAttribute("aria-current", "page");
  });
}

// Cada tela passa a própria função de atualização.
function iniciarPainel(atualizarTela) {
  marcarNavegacao();
  const ciclo = async () => {
    await Promise.all([atualizarTela(), atualizarAlertas(), atualizarEstadoColeta()]);
  };
  ciclo();
  setInterval(ciclo, INTERVALO_MS);
}

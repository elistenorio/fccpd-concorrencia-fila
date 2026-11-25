"use strict";

/* ================================================================ utilidades */

const $ = (sel, raiz = document) => raiz.querySelector(sel);
const $$ = (sel, raiz = document) => [...raiz.querySelectorAll(sel)];
const SVG = "http://www.w3.org/2000/svg";

function el(tag, attrs = {}, ...filhos) {
  const e = tag.startsWith("svg:") ? document.createElementNS(SVG, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.setAttribute("class", v);
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const f of filhos.flat()) if (f != null) e.append(f instanceof Node ? f : document.createTextNode(f));
  return e;
}

async function api(metodo, url) {
  const r = await fetch(url, { method: metodo });
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(corpo.detail || `HTTP ${r.status}`);
  return corpo;
}

const fmt = {
  num: (n) => n.toLocaleString("pt-BR"),
  pct: (n) => `${n.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`,
  mem: (kb) => (kb >= 1048576 ? `${(kb / 1048576).toFixed(1)} GB` : kb >= 1024 ? `${(kb / 1024).toFixed(0)} MB` : `${kb} kB`),
  hora: (ts) => new Date(ts * 1000).toLocaleTimeString("pt-BR"),
  ns: (ns) => (ns >= 1e6 ? `${(ns / 1e6).toFixed(2)} ms` : ns >= 1e3 ? `${(ns / 1e3).toFixed(1)} µs` : `${ns} ns`),
};

const cor = (nome) => getComputedStyle(document.documentElement).getPropertyValue(`--${nome}`).trim();

/** Desenha linhas (e áreas) num <svg> usando o tamanho real em pixels. */
function grafico(svg, series, { min = 0, max = 100, xs = null, grades = [0, 0.5, 1], rotulos = null, progresso = 1 } = {}) {
  const w = svg.clientWidth || 600;
  const h = svg.clientHeight || 200;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.removeAttribute("preserveAspectRatio");
  svg.replaceChildren();
  const pad = rotulos ? 34 : 0;
  const larg = w - pad;
  const n = Math.max(...series.map((s) => s.valores.length), 2);
  const xMax = xs ? xs[xs.length - 1] || 1 : n - 1;
  const X = (i) => pad + (xs ? xs[i] / xMax : i / (n - 1)) * larg * 1;
  const Y = (v) => h - 2 - ((v - min) / (max - min || 1)) * (h - 6);
  for (const g of grades) {
    const y = Y(min + g * (max - min));
    svg.append(el("svg:line", { x1: pad, x2: w, y1: y, y2: y, class: "grade" }));
    if (rotulos) svg.append(el("svg:text", { x: 0, y: y + 4, text: rotulos(min + g * (max - min)) }));
  }
  const limite = Math.max(1, Math.round(n * progresso));
  for (const s of series) {
    const vs = s.valores.slice(0, limite);
    if (!vs.length) continue;
    const pts = vs.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`);
    if (s.areaAte) {
      const base = s.areaAte.slice(0, limite).map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).reverse();
      svg.append(el("svg:polygon", { points: [...pts, ...base].join(" "), fill: s.preenchimento, stroke: "none" }));
    } else if (s.area) {
      svg.append(el("svg:polygon", {
        points: `${X(0)},${Y(min)} ${pts.join(" ")} ${X(vs.length - 1)},${Y(min)}`,
        fill: s.cor, opacity: 0.14, stroke: "none",
      }));
    }
    if (!s.soArea) svg.append(el("svg:polyline", { points: pts.join(" "), class: "linha", stroke: s.cor }));
  }
}

/* ================================================================ abas */

$$(".abas button").forEach((b) =>
  b.addEventListener("click", () => {
    $$(".abas button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    $$(".aba").forEach((s) => (s.hidden = s.id !== `aba-${b.dataset.aba}`));
    if (b.dataset.aba === "aed") abrirEstrutura(estruturaAtual);
    if (b.dataset.aba === "monitor") desenharMonitor();
  })
);

/* ================================================================ MONITOR */

const mon = { dados: null, historico: [], ordem: "cpu", janela: 300, ultimoTs: null, falhas: 0 };

const DESC_ORDEM = {
  cpu: "Ordenados por <code>quick_sort</code> (pivô mediana de três), CPU decrescente.",
  mem: "Ordenados por <code>quick_sort</code>, memória (RSS) decrescente.",
  nome: "Ordenados por <code>merge_sort</code>: estável, então nomes iguais mantêm a ordem de PID.",
  pid: "Ordenados por <code>quick_sort</code>, PID crescente.",
};

async function atualizarMonitor() {
  try {
    const qs = new URLSearchParams({ ordem: mon.ordem, limite: 40 });
    if (mon.ultimoTs != null) qs.set("desde", mon.ultimoTs);
    const d = await api("GET", `/api/monitor?${qs}`);
    if (d.historico_completo) mon.historico = d.historico;
    else mon.historico = mon.historico.concat(d.historico).slice(-120);
    if (mon.historico.length) mon.ultimoTs = mon.historico[mon.historico.length - 1].ts;
    mon.dados = d;
    mon.falhas = 0;
    const st = $("#status");
    if (!d.disponivel) { st.className = "pilula alerta"; st.textContent = "sem /proc"; }
    else if (d.pausado) { st.className = "pilula alerta"; st.textContent = "consumidor pausado"; }
    else { st.className = "pilula ok"; st.textContent = "coletor rodando"; }
    if (!$("#aba-monitor").hidden) desenharMonitor();
  } catch (e) {
    mon.falhas++;
    const st = $("#status");
    st.className = "pilula erro";
    st.textContent = "servidor fora do ar";
  }
}

function classeThread(estado) {
  if (estado.startsWith("bloqueado")) return "bloqueada";
  if (estado === "pausado") return "pausada";
  if (estado.startsWith("dormindo")) return "dormindo";
  return "ativa";
}

function desenharMonitor() {
  const d = mon.dados;
  if (!d) return;
  $("#sem-proc").hidden = d.disponivel;

  const ult = mon.historico[mon.historico.length - 1];
  $("#k-cpu").textContent = ult ? fmt.pct(ult.cpu) : "–";
  $("#k-nucleos").textContent = `${d.nucleos} núcleos lógicos`;
  $("#k-mem").textContent = ult ? fmt.pct(ult.mem) : "–";
  $("#k-memtotal").textContent = ult ? `de ${fmt.mem(ult.mem_total_kb)}` : "";
  $("#k-proc").textContent = fmt.num(d.total_processos);
  $("#k-amostras").textContent = fmt.num(d.contadores.consumidas || 0);
  $("#k-amostras-sub").textContent = `consumidas de ${fmt.num(d.contadores.produzidas || 0)} produzidas`;

  // threads
  for (const t of d.threads) {
    const card = $(`.thread[data-thread="${t.nome}"]`);
    if (!card) continue;
    card.className = `thread ${classeThread(t.estado)}`;
    $(".estado", card).textContent = `${t.estado} · ${t.desde.toFixed(1)} s`;
  }
  $("#pausar").textContent = d.pausado ? "Retomar consumidor" : "Pausar consumidor";
  $("#pausar").classList.toggle("perigo", d.pausado);
  $("#pausar").classList.toggle("primario", !d.pausado);

  desenharAnelBuffer(d.buffer);

  const e = d.estruturas;
  $("#destinos").replaceChildren(
    el("li", {}, el("b", { text: "TabelaHash" }), el("span", { text: `${e.processos_tabela.itens} PIDs · ${e.processos_tabela.baldes} baldes` })),
    el("li", {}, el("b", { text: "FilaCircular" }), el("span", { text: `histórico ${e.historico_fila.itens}/${e.historico_fila.capacidade}` })),
    el("li", {}, el("b", { text: "ArvoreBusca" }), el("span", { text: `${e.eventos_arvore.itens} eventos · altura ${e.eventos_arvore.altura}` })),
    el("li", {}, el("b", { text: "Fila" }), el("span", { text: `feed ${e.feed_fila.itens} eventos` })),
  );
  const c = d.contadores;
  $("#contadores").replaceChildren(
    el("span", {}, el("b", { text: fmt.num(c.produzidas || 0) }), "amostras produzidas"),
    el("span", {}, el("b", { text: fmt.num(c.consumidas || 0) }), "consumidas"),
    el("span", {}, el("b", { text: `${d.buffer.ocupacao}/${d.buffer.capacidade}` }), "no buffer"),
    el("span", {}, el("b", { text: fmt.num(c.bloqueios || 0) }), "vezes que um produtor dormiu com o buffer cheio"),
  );

  // gráfico do sistema
  const h = mon.historico;
  grafico($("#grafico-sistema"), [
    { valores: h.map((a) => a.cpu), cor: cor("s1"), area: true },
    { valores: h.map((a) => a.mem), cor: cor("s2") },
  ], { grades: [0, 0.25, 0.5, 0.75, 1], rotulos: (v) => `${v}%` });
  $("#gx-ini").textContent = h.length ? `−${Math.round(h[h.length - 1].ts - h[0].ts)} s` : "";

  // top 5
  $("#n-top").textContent = fmt.num(d.total_processos);
  const maxTop = Math.max(100, ...d.top.map((t) => t.cpu));
  $("#top").replaceChildren(...(d.top.length ? d.top.map((t) =>
    el("li", {},
      el("span", {}, t.nome, " ", el("small", { text: `PID ${t.pid}` })),
      el("b", { text: fmt.pct(t.cpu) }),
      el("span", { class: "barra" }, el("i", { style: `width:${(t.cpu / maxTop) * 100}%` })))
  ) : [el("li", { class: "vazio", text: "Esperando a segunda varredura para calcular a CPU…" })]));

  // serviços
  const servs = d.servicos;
  if (!servs.length) {
    $("#servicos").replaceChildren(el("div", { class: "vazio" },
      "Nenhum processo do Origem encontrado. Suba os serviços com ", el("code", { text: "docker compose up -d" }),
      " — o serviço ", el("code", { text: "monitor" }), " usa ", el("code", { text: "pid: host" }), " para enxergar os outros containers."));
  } else {
    $("#servicos").replaceChildren(...servs.map((s) => {
      const svg = el("svg:svg", { viewBox: "0 0 200 36" });
      const card = el("div", { class: "servico" },
        el("header", {}, el("b", { text: s.servico }), el("span", { text: `${s.processos} proc.` })),
        el("div", { class: "valores" }, el("span", { text: `CPU ${fmt.pct(s.cpu)}` }), el("span", { text: `RSS ${fmt.mem(s.rss_kb)}` })),
        svg);
      requestAnimationFrame(() => grafico(svg, [{ valores: s.serie, cor: cor("s1"), area: true }],
        { max: Math.max(25, ...s.serie), grades: [] }));
      return card;
    }));
  }

  // processos
  $("#desc-ordem").innerHTML = `${DESC_ORDEM[mon.ordem]} Mostrando ${d.processos.length} de ${fmt.num(d.total_processos)}.`;
  $("#processos").replaceChildren(...d.processos.map((p) =>
    el("tr", { class: p.servico ? "destaque" : null, title: p.comando },
      el("td", { class: "num", text: p.pid }),
      el("td", { text: p.nome }),
      el("td", {}, p.servico ? el("span", { class: "etiqueta", text: p.servico }) : ""),
      el("td", { class: "num", text: p.cpu.toFixed(1) }),
      el("td", { class: "num", text: fmt.mem(p.rss_kb) }),
      el("td", { class: "num", text: p.threads }),
      el("td", { text: p.estado }))));

  // eventos (filtra a janela no cliente também, para trocar sem esperar o próximo ciclo)
  const desde = d.agora - mon.janela;
  const evs = d.eventos.filter((ev) => ev.ts >= desde).reverse();
  const arv = d.estruturas.eventos_arvore;
  $("#nota-arvore").innerHTML = arv.itens > 2
    ? `Altura ${arv.altura} para ${arv.itens} eventos: os timestamps chegam em ordem crescente, então a árvore degenera em lista (O(n) por inserção). Por isso o coletor guarda no máximo 500.`
    : "";
  $("#eventos").replaceChildren(...(evs.length ? evs.map((ev) =>
    el("li", { class: ev.tipo }, el("time", { text: fmt.hora(ev.ts) }), el("span", { text: ev.texto }))
  ) : [el("li", { class: "vazio", text: "Nenhum evento na janela. Pause o consumidor para gerar alguns." })]));
}

function desenharAnelBuffer(b) {
  const svg = $("#anel");
  svg.replaceChildren();
  const cx = 110, cy = 110, r = 78, n = b.capacidade;
  svg.append(el("svg:circle", { cx, cy, r, fill: "none", stroke: cor("borda"), "stroke-width": 26 }));
  for (let i = 0; i < n; i++) {
    const a = (i / n) * 2 * Math.PI - Math.PI / 2;
    const x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
    const item = b.posicoes[i];
    svg.append(el("svg:circle", {
      cx: x, cy: y, r: 15,
      fill: item ? cor(item.tipo === "sistema" ? "s2" : "s1") : cor("superficie"),
      stroke: i === b.inicio ? cor("texto") : cor("texto-3"),
      "stroke-width": i === b.inicio ? 3 : 1,
    }));
    svg.append(el("svg:text", {
      x, y: y + 4, "text-anchor": "middle", "font-size": 11, "font-family": "monospace",
      fill: item ? "#fff" : cor("texto-3"), text: i,
    }));
  }
  svg.append(el("svg:text", { x: cx, y: cy - 4, "text-anchor": "middle", "font-size": 34, "font-family": "'Origem Titulo', sans-serif", fill: cor("acento"), text: `${b.ocupacao}/${n}` }));
  svg.append(el("svg:text", { x: cx, y: cy + 16, "text-anchor": "middle", "font-size": 11, fill: cor("texto-3"), text: b.ocupacao === n ? "cheio" : b.ocupacao ? "ocupado" : "vazio" }));
}

$("#pausar").addEventListener("click", async () => {
  await api("POST", `/api/monitor/consumidor?pausado=${!(mon.dados && mon.dados.pausado)}`);
  atualizarMonitor();
});
$("#intervalo").addEventListener("change", (e) => api("POST", `/api/monitor/intervalo?segundos=${e.target.value}`));
$$("#ordem button").forEach((b) => b.addEventListener("click", () => {
  mon.ordem = b.dataset.ordem;
  $$("#ordem button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  atualizarMonitor();
}));
$("#janela").addEventListener("change", (e) => { mon.janela = Number(e.target.value); desenharMonitor(); });
window.addEventListener("resize", () => { if (!$("#aba-monitor").hidden) desenharMonitor(); });

(function laco() {
  atualizarMonitor().finally(() => setTimeout(laco, mon.falhas ? 3000 : 1000));
})();

/* ================================================================ CORRIDA */

$("#form-corrida").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const botao = $("button", ev.target);
  botao.disabled = true;
  botao.textContent = "Rodando threads…";
  try {
    const qs = new URLSearchParams(new FormData(ev.target));
    const r = await api("POST", `/api/corrida?${qs}`);
    animarCorrida(r);
  } catch (e) {
    alert(`Falhou: ${e.message}`);
  } finally {
    botao.disabled = false;
    botao.textContent = "Rodar as duas versões";
  }
});

function animarCorrida(r) {
  const esperado = r.esperado.total;
  const paineis = [[$("#c-sem"), r.sem_lock], [$("#c-com"), r.com_lock]];
  const inicio = performance.now();
  const duracao = 1600;
  function quadro(agora) {
    // Aba oculta não roda requestAnimationFrame: desenha direto o resultado final.
    const p = document.hidden ? 1 : Math.min(1, (agora - inicio) / duracao);
    for (const [painel, res] of paineis) {
      const s = res.serie;
      const idx = Math.max(0, Math.round((s.length - 1) * p));
      const pt = s[idx];
      $(".selo", painel).hidden = p < 1;
      const placar = $(".placar", painel);
      placar.classList.remove("vazio");
      placar.replaceChildren(
        el("div", { class: "grande" }, fmt.num(pt[3]), el("small", { text: ` / ${fmt.num(esperado)} contados` })),
        el("dl", {},
          el("dt", { text: "retirados" }), el("dd", { text: fmt.num(pt[2]) }),
          el("dt", { text: "perdidos" }), el("dd", { text: `${fmt.num(pt[2] - pt[3])}${p === 1 ? ` (${((res.perdidas / esperado) * 100).toFixed(1)}%)` : ""}` }),
          el("dt", { text: "tempo" }), el("dd", { text: `${pt[0]} ms` })),
      );
      const xs = s.map((q) => q[0]);
      grafico($(".grafico:not(.mini)", painel), [
        { valores: s.map((q) => q[2]), areaAte: s.map((q) => q[3]), preenchimento: cor("erro-suave"), soArea: true },
        { valores: s.map((q) => q[2]), cor: cor("s3") },
        { valores: s.map((q) => q[3]), cor: cor("s1") },
      ], { max: esperado, xs, progresso: p, grades: [0, 0.5, 1], rotulos: (v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v) });
      grafico($(".grafico.mini", painel), [
        { valores: s.map((q) => q[1]), cor: cor("s2"), area: true },
      ], { max: r.parametros.capacidade, xs, progresso: p, grades: [0, 1] });
    }
    if (p < 1) requestAnimationFrame(quadro);
  }
  if (document.hidden) quadro(performance.now());
  else requestAnimationFrame(quadro);
}

/* ================================================================ AED */

let estruturaAtual = "lista";

const ESTRUTURAS = {
  lista: {
    titulo: "Lista encadeada", arquivo: "estruturas/lista.py", complex: "início/fim O(1) · busca O(n)",
    desc: "O ponteiro _fim é o que deixa inserir_fim em O(1). Usada para copiar entradas de tamanho desconhecido (ordenação, coletor).",
    ops: [["inserir_inicio", true], ["inserir_fim", true], ["remover_inicio", false], ["remover", true], ["contem", true]],
  },
  pilha: {
    titulo: "Pilha (LIFO)", arquivo: "estruturas/pilha.py", complex: "empilhar/desempilhar O(1)",
    desc: "Tudo acontece no nó do topo. A ArvoreBusca usa esta pilha para percorrer sem recursão.",
    ops: [["empilhar", true], ["desempilhar", false]],
  },
  fila: {
    titulo: "Fila (FIFO)", arquivo: "estruturas/fila.py", complex: "enfileirar/desenfileirar O(1)",
    desc: "Entra pelo _fim, sai pelo _inicio. É o feed de eventos do monitor.",
    ops: [["enfileirar", true], ["desenfileirar", false]],
  },
  anel: {
    titulo: "Fila circular (ring buffer)", arquivo: "estruturas/fila_circular.py", complex: "O(1) · memória fixa",
    desc: "Array de tamanho fixo; os índices dão a volta com módulo. É o buffer do produtor-consumidor e o histórico do monitor.",
    ops: [["enfileirar", true], ["desenfileirar", false]],
  },
  hash: {
    titulo: "Tabela hash", arquivo: "estruturas/tabela_hash.py", complex: "O(1) médio · O(n) pior",
    desc: "Encadeamento separado; hash polinomial base 31 para texto. Dobra os baldes quando o fator de carga passa de 0,75.",
    ops: [["inserir", true], ["obter", true], ["remover", true]],
  },
  arvore: {
    titulo: "Árvore binária de busca", arquivo: "estruturas/arvore_busca.py", complex: "O(h): ~log n ou n",
    desc: "Sem balanceamento: chaves em ordem aleatória dão h ≈ log n; em ordem crescente, a árvore vira uma lista.",
    ops: [["inserir", true], ["buscar", true], ["remover", true]],
  },
  top: {
    titulo: "Top-K", arquivo: "estruturas/top_k.py", complex: "O(1) descarte · O(k) entrada",
    desc: "Guarda os k maiores num array fixo. Score abaixo do limiar é descartado em O(1). Usado nas recomendações e no monitor.",
    ops: [["oferecer", true]],
  },
  ordenacao: {
    titulo: "Ordenação", arquivo: "estruturas/ordenacao.py", complex: "O(n²) × O(n log n)",
    desc: "Os quatro algoritmos rodam no servidor sobre a mesma entrada. As comparações são contadas pelo parâmetro chave, sem mexer no algoritmo.",
  },
  busca: {
    titulo: "Busca sequencial × binária", arquivo: "estruturas/busca.py", complex: "O(n) × O(log n)",
    desc: "Mesmo array ordenado, mesmo alvo. Os índices visitados vêm do Python (registrados pela função chave).",
  },
};

$$("#sub-aed button").forEach((b) => b.addEventListener("click", () => abrirEstrutura(b.dataset.est)));

function logLab(texto, classe) {
  const log = $("#lab-log");
  log.prepend(el("div", { class: classe, text: texto }));
  while (log.children.length > 30) log.lastChild.remove();
}

async function abrirEstrutura(nome) {
  estruturaAtual = nome;
  $$("#sub-aed button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.est === nome)));
  const def = ESTRUTURAS[nome];
  $("#lab-titulo").replaceChildren(def.titulo, " ", el("code", { text: def.arquivo }));
  $("#lab-desc").textContent = def.desc;
  $("#lab-complex").textContent = def.complex;
  $("#lab-log").replaceChildren();
  const ctrl = $("#lab-controles");
  ctrl.replaceChildren();
  $("#lab-desenho").replaceChildren();

  if (nome === "ordenacao") return montarOrdenacao(ctrl);
  if (nome === "busca") return montarBusca(ctrl);

  const entrada = el("input", { type: nome === "arvore" || nome === "top" ? "number" : "text", placeholder: nome === "top" ? "score (vazio = aleatório)" : "valor", id: "lab-valor", style: nome === "top" ? "width:190px" : null });
  ctrl.append(el("label", { class: "campo" }, "Valor", entrada));
  for (const [op, usaValor] of def.ops) {
    ctrl.append(el("button", { class: "botao", type: "button", text: `${op}(${usaValor ? "v" : ""})`, onclick: () => operar(nome, op, usaValor) }));
  }
  if (nome === "arvore") {
    ctrl.append(el("button", { class: "botao", type: "button", text: "15 chaves aleatórias", onclick: () => operar(nome, "inserir_aleatorio", false, 15) }));
    ctrl.append(el("button", { class: "botao", type: "button", text: "8 chaves em ordem (degenera)", onclick: () => operar(nome, "inserir_ordenado", false, 8) }));
  }
  if (nome === "hash") {
    ctrl.append(el("button", { class: "botao", type: "button", text: "+ 4 nomes", onclick: async () => {
      for (const n of sortear(NOMES, 4)) await operar(nome, "inserir", false, n);
    } }));
  }
  if (nome === "anel") {
    const chk = el("input", { type: "checkbox", id: "chk-sobrescrever" });
    chk.addEventListener("change", () => operar(nome, "sobrescrever", false, chk.checked ? 1 : 0));
    ctrl.append(el("label", { class: "campo", style: "flex-direction:row;display:flex;gap:6px;align-items:center;padding-bottom:6px" }, chk, "sobrescrever quando cheia"));
  }
  if (nome === "top") {
    ctrl.append(el("button", { class: "botao", type: "button", text: "oferecer 10 aleatórios", onclick: async () => {
      for (let i = 0; i < 10; i++) await operar(nome, "oferecer", false, null, true);
    } }));
  }
  ctrl.append(el("button", { class: "botao perigo", type: "button", text: "reiniciar", onclick: async () => {
    desenharEstrutura(nome, await api("POST", `/api/aed/${nome}/reiniciar`));
    logLab(`${nome} reiniciada`);
  } }));
  entrada.addEventListener("keydown", (e) => { if (e.key === "Enter") operar(nome, def.ops[0][0], true); });

  const r = await api("GET", `/api/aed/${nome}`);
  if (nome === "anel") $("#chk-sobrescrever").checked = r.sobrescrever;
  desenharEstrutura(nome, r);
}

const NOMES = ["vaso", "cesto", "tapete", "colar", "rede", "caneca", "manta", "bolsa", "prato", "luminaria", "boneca", "quadro", "jarro", "pulseira", "almofada", "brinco"];
const sortear = (arr, n) => [...arr].sort(() => Math.random() - 0.5).slice(0, n);
let contadorAuto = 1;

async function operar(nome, op, usaValor, valorFixo = undefined, aleatorio = false) {
  let valor = valorFixo;
  if (usaValor) {
    valor = $("#lab-valor").value.trim();
    if (!valor && nome === "top") valor = null;
    else if (!valor) valor = nome === "arvore" ? Math.floor(Math.random() * 99) + 1 : (nome === "hash" ? sortear(NOMES, 1)[0] : contadorAuto++);
  }
  if (aleatorio) valor = null;
  const antes = nome === "top" ? ultimoTop : null;
  const qs = valor != null && valor !== "" ? `?valor=${encodeURIComponent(valor)}` : "";
  try {
    const r = await api("POST", `/api/aed/${nome}/${op}${qs}`);
    const chamada = `${nome}.${op}(${valor ?? ""})`;
    let extra = r.resultado !== null && r.resultado !== undefined ? ` → ${JSON.stringify(r.resultado)}` : "";
    if (nome === "top" && op === "oferecer") {
      const entrou = r.itens.some((i) => i.score === r.resultado) && (!antes || r.itens.length > antes.itens.length || antes.limiar === null || r.resultado > antes.limiar);
      extra += entrou ? `  entrou (deslocou os menores)` : `  ≤ limiar ${antes?.limiar}: descartado em O(1)`;
    }
    if (nome === "hash" && valor != null) extra += `  · hash % ${r.capacidade} = ${hashTexto(String(valor)) % r.capacidade}`;
    logLab(`> ${chamada}${extra}  · ${fmt.ns(r.duracao_ns)}`, "ok");
    if ($("#lab-valor") && usaValor) $("#lab-valor").select();
    desenharEstrutura(nome, r, valor);
  } catch (e) {
    logLab(`> ${nome}.${op}(${valor ?? ""}) ✗ ${e.message}`, "erro");
  }
}

/** Mesmo hash de estruturas/tabela_hash.py (polinomial, base 31, 32 bits). */
function hashTexto(s) {
  let h = 0;
  for (const c of s) h = (h * 31 + c.codePointAt(0)) % 4294967296;
  return h;
}

let ultimoTop = null;

function desenharEstrutura(nome, r, destaque) {
  const alvo = $("#lab-desenho");
  alvo.replaceChildren();
  if (nome === "lista" || nome === "fila") {
    const rotIni = nome === "lista" ? "_inicio" : "_inicio (sai)";
    const rotFim = nome === "lista" ? "_fim" : "_fim (entra)";
    if (!r.valores.length) { alvo.append(el("div", { class: "vazio", text: `${rotIni} = ${rotFim} = None` })); return; }
    const cadeia = el("div", { class: "cadeia" });
    r.valores.forEach((v, i) => {
      const rotulos = [i === 0 && rotIni, i === r.valores.length - 1 && rotFim].filter(Boolean).join(" · ");
      cadeia.append(el("div", { class: "com-ponteiro" },
        el("span", { class: "ponteiro", text: rotulos || " " }),
        el("div", { class: `no${String(v) === String(destaque) ? " marcado" : ""}` }, el("b", { text: v }), el("i"))));
      cadeia.append(el("div", { class: "ligacao", style: "margin-top:20px" }));
    });
    cadeia.append(el("span", { class: "nulo", style: "margin-top:20px", text: "None" }));
    alvo.append(cadeia, el("p", { class: "nota", style: "margin-top:12px", text: `tamanho = ${r.tamanho}` }));
  } else if (nome === "pilha") {
    if (!r.valores.length) { alvo.append(el("div", { class: "vazio", text: "_topo = None" })); return; }
    const p = el("div", { class: "pilha-vis" }, el("span", { class: "ponteiro", text: "_topo ↓" }));
    r.valores.forEach((v, i) => {
      if (i) p.append(el("div", { class: "ligacao-v" }));
      p.append(el("div", { class: `no vertical${i === 0 ? " marcado" : ""}` }, el("b", { text: v }), el("i")));
    });
    p.append(el("div", { class: "ligacao-v" }), el("span", { class: "nulo", text: "None" }));
    alvo.append(p);
  } else if (nome === "anel") {
    alvo.append(desenharAnel(r));
  } else if (nome === "hash") {
    alvo.append(el("p", { class: "nota" },
      `${r.tamanho} chaves em ${r.capacidade} baldes · fator de carga ${r.fator_carga.toLocaleString("pt-BR")} `,
      r.fator_carga > 0.6 ? "(perto de 0,75: a próxima inserção pode dobrar a tabela)" : ""));
    const lista = el("div", { class: "baldes" });
    r.baldes.forEach((cadeia, i) => {
      const linha = el("div", { class: `balde${cadeia.length > 1 ? " colisao" : ""}` },
        el("span", { class: "idx", text: `[${i}]` }), el("span", { class: `slot${cadeia.length ? " cheio" : ""}` }));
      cadeia.forEach((ch, j) => {
        if (j) linha.append(el("div", { class: "ligacao", style: "width:20px" }));
        linha.append(el("div", { class: `no${ch === String(destaque) ? " marcado" : ""}` }, el("b", { text: ch }), el("i")));
      });
      lista.append(linha);
    });
    alvo.append(lista);
  } else if (nome === "arvore") {
    alvo.append(desenharArvore(r, destaque));
  } else if (nome === "top") {
    ultimoTop = r;
    const caixa = el("div", { class: "topk" });
    for (let i = 0; i < r.k; i++) {
      const it = r.itens[i];
      caixa.append(it
        ? el("div", { class: `casa${i === r.k - 1 ? " ultima" : ""}` }, el("small", { text: `[${i}]` }), el("b", { text: it.score }))
        : el("div", { class: "casa vazia" }, el("small", { text: `[${i}]` }), el("b", { text: "—" })));
    }
    alvo.append(caixa, el("p", { class: "nota", style: "margin-top:12px" },
      r.limiar === null ? "Ainda não encheu: tudo entra." : `limiar = ${r.limiar} (menor guardado). Qualquer score ≤ limiar é descartado sem tocar no array.`));
  }
}

function desenharAnel(r) {
  const n = r.capacidade, cx = 150, cy = 140, raio = 95;
  const svg = el("svg:svg", { viewBox: "0 0 620 290", width: 620, height: 290 });
  const fim = (r.inicio + r.tamanho) % n;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * 2 * Math.PI - Math.PI / 2;
    const x = cx + raio * Math.cos(a), y = cy + raio * Math.sin(a);
    const v = r.posicoes[i];
    svg.append(el("svg:circle", { cx: x, cy: y, r: 24, fill: v != null ? cor("acento-suave") : cor("superficie"), stroke: i === r.inicio && r.tamanho ? cor("acento") : cor("texto-3"), "stroke-width": i === r.inicio && r.tamanho ? 3 : 1.5 }));
    svg.append(el("svg:text", { x, y: y + 5, "text-anchor": "middle", "font-family": "monospace", "font-size": 14, "font-weight": 600, fill: cor("texto"), text: v ?? "" }));
    const xr = cx + (raio + 38) * Math.cos(a), yr = cy + (raio + 38) * Math.sin(a);
    svg.append(el("svg:text", { x: xr, y: yr + 4, "text-anchor": "middle", "font-family": "monospace", "font-size": 11, fill: cor("texto-3"), text: i }));
  }
  svg.append(el("svg:text", { x: cx, y: cy - 2, "text-anchor": "middle", "font-size": 30, "font-family": "'Origem Titulo', sans-serif", fill: cor("acento"), text: `${r.tamanho}/${n}` }));
  svg.append(el("svg:text", { x: cx, y: cy + 18, "text-anchor": "middle", "font-size": 11, fill: cor("texto-3"), text: r.tamanho === n ? "cheia" : "ocupadas" }));
  // array linear ao lado
  const x0 = 330, y0 = 70, lw = 34;
  svg.append(el("svg:text", { x: x0, y: y0 - 18, "font-size": 12, fill: cor("texto-2"), text: "_dados (o mesmo array, em linha)" }));
  for (let i = 0; i < n; i++) {
    const v = r.posicoes[i];
    svg.append(el("svg:rect", { x: x0 + i * lw, y: y0, width: lw - 2, height: 36, rx: 4, fill: v != null ? cor("acento-suave") : cor("superficie"), stroke: cor("texto-3") }));
    svg.append(el("svg:text", { x: x0 + i * lw + lw / 2 - 1, y: y0 + 23, "text-anchor": "middle", "font-family": "monospace", "font-size": 13, fill: cor("texto"), text: v ?? "" }));
    svg.append(el("svg:text", { x: x0 + i * lw + lw / 2 - 1, y: y0 + 52, "text-anchor": "middle", "font-family": "monospace", "font-size": 10, fill: cor("texto-3"), text: i }));
  }
  const seta = (i, rot, dy) => {
    const x = x0 + i * lw + lw / 2 - 1;
    svg.append(el("svg:text", { x, y: y0 + 80 + dy, "text-anchor": "middle", "font-family": "monospace", "font-size": 11, "font-weight": 700, fill: cor("acento"), text: `↑ ${rot}` }));
  };
  seta(r.inicio, `_inicio=${r.inicio}`, 0);
  if (fim !== r.inicio || r.tamanho === 0) seta(fim, `próx.=${fim}`, 18);
  svg.append(el("svg:text", { x: x0, y: y0 + 150, "font-size": 12, fill: cor("texto-2"), text: `próxima posição = (_inicio + _tamanho) % ${n} = (${r.inicio} + ${r.tamanho}) % ${n} = ${fim}` }));
  svg.append(el("svg:text", { x: x0, y: y0 + 172, "font-size": 12, fill: cor("texto-3"), text: r.sobrescrever ? "Cheia: o novo ocupa o lugar do mais antigo." : "Cheia: enfileirar levanta FilaCheiaError." }));
  return svg;
}

function desenharArvore(r, destaque) {
  if (!r.nos.length) return el("div", { class: "vazio", text: "_raiz = None" });
  const dx = 46, dy = 62, pad = 30;
  const w = pad * 2 + (r.nos.length - 1) * dx;
  const h = pad * 2 + (r.altura - 1) * dy + 40;
  const svg = el("svg:svg", { viewBox: `0 0 ${Math.max(w, 300)} ${h}`, width: Math.max(w, 300), height: h });
  const pos = new Map(r.nos.map((n) => [n.chave, [pad + n.x * dx, pad + n.y * dy]]));
  const noCaminho = new Set(r.caminho);
  for (const [a, b] of r.arestas) {
    const [x1, y1] = pos.get(a), [x2, y2] = pos.get(b);
    const ativa = noCaminho.has(a) && noCaminho.has(b);
    svg.append(el("svg:line", { x1, y1, x2, y2, stroke: ativa ? cor("acento") : cor("texto-3"), "stroke-width": ativa ? 3 : 1.5 }));
  }
  for (const n of r.nos) {
    const [x, y] = pos.get(n.chave);
    const ativo = noCaminho.has(n.chave);
    const alvo = r.caminho[r.caminho.length - 1] === n.chave && String(n.chave) === String(destaque);
    svg.append(el("svg:circle", { cx: x, cy: y, r: 18, fill: alvo ? cor("acento") : ativo ? cor("acento-suave") : cor("superficie"), stroke: ativo ? cor("acento") : cor("texto-2"), "stroke-width": 2 }));
    svg.append(el("svg:text", { x, y: y + 4, "text-anchor": "middle", "font-family": "monospace", "font-size": 12, "font-weight": 600, fill: alvo ? "#fff" : cor("texto"), text: n.chave }));
  }
  const ideal = Math.ceil(Math.log2(r.tamanho + 1));
  const caixa = el("div", {});
  caixa.append(el("p", { class: "nota" },
    `${r.tamanho} nós · altura ${r.altura} (mínimo possível: ${ideal}). `,
    r.altura === r.tamanho && r.tamanho > 3 ? "Degenerou: cada busca percorre todos os nós, O(n)." : "",
    r.caminho.length ? ` Caminho da última operação: ${r.caminho.join(" → ")} (${r.caminho.length} comparações).` : ""), svg);
  return caixa;
}

/* ---------- ordenação ---------- */

const CORES_ALG = { insertion: "s1", selection: "s4", merge: "s2", quick: "s3" };

function montarOrdenacao(ctrl) {
  const n = el("select", { id: "ord-n" }, ...[100, 300, 1000, 2000].map((v) => el("option", { value: v, selected: v === 300, text: v })));
  const tipo = el("select", { id: "ord-tipo" },
    el("option", { value: "aleatoria", text: "aleatória" }), el("option", { value: "quase", text: "quase ordenada (5% fora)" }),
    el("option", { value: "ordenada", text: "já ordenada" }), el("option", { value: "invertida", text: "invertida" }));
  const botao = el("button", { class: "botao primario", type: "button", text: "Ordenar com os 4" });
  ctrl.append(el("label", { class: "campo" }, "n", n), el("label", { class: "campo" }, "Entrada", tipo), botao);
  const rodar = async () => {
    botao.disabled = true;
    try { desenharOrdenacao(await api("GET", `/api/aed-ordenacao?n=${n.value}&tipo=${tipo.value}`)); }
    catch (e) { logLab(`✗ ${e.message}`, "erro"); }
    finally { botao.disabled = false; }
  };
  botao.addEventListener("click", rodar);
  rodar();
}

function desenharOrdenacao(r) {
  const alvo = $("#lab-desenho");
  const maxA = Math.max(...r.amostra);
  const barras = el("div", { class: "barras", title: "primeiros elementos da entrada" }, ...r.amostra.map((v) => el("i", { style: `height:${(v / maxA) * 100}%` })));
  const maxComp = Math.max(...Object.values(r.resultados).map((x) => x.comparacoes), 1);
  const cards = el("div", { class: "resultado-ord" }, ...Object.entries(r.resultados).map(([nome, x]) =>
    el("div", {},
      el("b", { text: `${nome}_sort` }), " ", x.correto ? el("small", { text: "✓ ordenado" }) : el("small", { style: `color:${cor("erro")}`, text: "✗" }),
      el("span", { class: "n", text: fmt.num(x.comparacoes) }),
      el("small", { text: `comparações · ${x.ms.toLocaleString("pt-BR")} ms` }),
      el("div", { class: "barra" }, el("i", { style: `width:${(x.comparacoes / maxComp) * 100}%;background:${cor(CORES_ALG[nome])}` })))));
  const svg = el("svg:svg", { class: "grafico", style: "height:230px" });
  const legenda = el("div", { class: "legenda" }, ...Object.keys(r.curvas).map((nome) =>
    el("span", {}, el("i", { class: "traco", style: `background:${cor(CORES_ALG[nome])}` }), nome)));
  alvo.replaceChildren(
    el("p", { class: "nota", text: `Entrada (${r.tipo}, primeiros ${r.amostra.length} de ${r.n}):` }), barras, cards,
    el("p", { class: "nota", text: `Comparações × n (de ${r.tamanhos[0]} a ${r.tamanhos[r.tamanhos.length - 1]}), entrada ${r.tipo}. As curvas O(n²) explodem; merge e quick ficam rentes ao chão.` }),
    legenda, svg, el("div", { class: "eixo-x" }, ...r.tamanhos.map((t) => el("span", { text: `n=${t}` }))));
  const maxCurva = Math.max(...Object.values(r.curvas).flat());
  requestAnimationFrame(() => grafico(svg, Object.entries(r.curvas).map(([nome, pts]) => ({ valores: pts, cor: cor(CORES_ALG[nome]) })),
    { max: maxCurva, grades: [0, 0.25, 0.5, 0.75, 1], rotulos: (v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : Math.round(v)) }));
  logLab(`> ordenou n=${r.n} (${r.tipo}) com os 4 algoritmos`, "ok");
}

/* ---------- busca ---------- */

function montarBusca(ctrl) {
  const n = el("select", { id: "busca-n" }, ...[32, 64, 128].map((v) => el("option", { value: v, selected: v === 64, text: v })));
  const alvoIn = el("input", { type: "number", placeholder: "vazio = sorteia", style: "width:150px" });
  const botao = el("button", { class: "botao primario", type: "button", text: "Buscar" });
  ctrl.append(el("label", { class: "campo" }, "n", n), el("label", { class: "campo" }, "Alvo", alvoIn), botao);
  const rodar = async () => {
    const qs = new URLSearchParams({ n: n.value });
    if (alvoIn.value) qs.set("alvo", alvoIn.value);
    try { animarBusca(await api("GET", `/api/aed-busca?${qs}`)); } catch (e) { logLab(`✗ ${e.message}`, "erro"); }
  };
  botao.addEventListener("click", rodar);
  alvoIn.addEventListener("keydown", (e) => { if (e.key === "Enter") rodar(); });
  rodar();
}

let animacaoBusca = null;

function animarBusca(r) {
  clearInterval(animacaoBusca);
  const alvo = $("#lab-desenho");
  const trilhas = {};
  const bloco = el("div", { class: "trilha" });
  for (const [nome, rot] of [["sequencial", "busca_sequencial"], ["binaria", "busca_binaria"]]) {
    const cont = el("span", { text: "0 comparações" });
    const cel = r.valores.map((v, i) => el("div", { class: "celula" }, el("small", { text: i }), v));
    bloco.append(el("div", {}, el("h3", {}, rot, cont), el("div", { class: "celulas" }, ...cel)));
    trilhas[nome] = { cel, cont, visitados: r[nome].visitados, indice: r[nome].indice, ini: 0, fim: r.valores.length - 1 };
  }
  alvo.replaceChildren(el("p", { class: "nota", text: `Procurando ${r.alvo}. A binária continua pela esquerda mesmo depois de achar, para devolver a primeira ocorrência.` }), bloco);
  let passo = 0;
  const total = Math.max(trilhas.sequencial.visitados.length, trilhas.binaria.visitados.length);
  animacaoBusca = setInterval(() => {
    for (const [nome, t] of Object.entries(trilhas)) {
      if (passo >= t.visitados.length) {
        if (passo === t.visitados.length) {
          t.cel[t.visitados[passo - 1]].classList.replace("atual", "visitada");
          if (t.indice >= 0) t.cel[t.indice].className = "celula achou";
        }
        continue;
      }
      const i = t.visitados[passo];
      if (passo > 0) t.cel[t.visitados[passo - 1]].classList.replace("atual", "visitada");
      t.cel[i].classList.add("atual");
      t.cont.textContent = `${passo + 1} comparaç${passo ? "ões" : "ão"}`;
      if (nome === "binaria") {
        const v = r.valores[i];
        if (v >= r.alvo) t.fim = i - 1; else t.ini = i + 1;
        t.cel.forEach((c, j) => c.classList.toggle("fora", (j < t.ini || j > t.fim) && j !== i));
      }
    }
    passo++;
    if (passo > total) {
      clearInterval(animacaoBusca);
      logLab(`> alvo ${r.alvo}: sequencial ${trilhas.sequencial.visitados.length} comparações, binária ${trilhas.binaria.visitados.length} (log₂ ${r.valores.length} ≈ ${Math.log2(r.valores.length).toFixed(1)})`, "ok");
    }
  }, r.valores.length > 64 ? 45 : 90);
}

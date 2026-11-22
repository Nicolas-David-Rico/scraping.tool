(() => {
  const state = {
    stores: [],
    selectedStoreIds: new Set(),
    settings: null,
    history: [],
    activeComparison: null,
    followupHistory: [],
    composerMode: "search", // "search" | "followup"
    isBusy: false,
  };

  const els = {
    sidebar: document.getElementById("sidebar"),
    openSidebarBtn: document.getElementById("openSidebarBtn"),
    closeSidebarBtn: document.getElementById("closeSidebarBtn"),
    storeToggles: document.getElementById("storeToggles"),
    toggleAllStoresBtn: document.getElementById("toggleAllStoresBtn"),
    historyList: document.getElementById("historyList"),
    newChatBtn: document.getElementById("newChatBtn"),
    welcomeView: document.getElementById("welcomeView"),
    messagesContainer: document.getElementById("messagesContainer"),
    chatFeed: document.getElementById("chatFeed"),
    chatForm: document.getElementById("chatForm"),
    chatInput: document.getElementById("chatInput"),
    sendBtn: document.getElementById("sendBtn"),
    sendBtnText: document.getElementById("sendBtnText"),
    topbarTitle: document.getElementById("topbarTitle"),
    exportCsvBtn: document.getElementById("exportCsvBtn"),
    composerModeBar: document.getElementById("composerModeBar"),
    modeSearchBtn: document.getElementById("modeSearchBtn"),
    modeFollowupBtn: document.getElementById("modeFollowupBtn"),
    aiStatusDot: document.getElementById("aiStatusDot"),
    aiStatusLabel: document.getElementById("aiStatusLabel"),
    // Settings modal
    settingsModal: document.getElementById("settingsModal"),
    openSettingsBtn: document.getElementById("openSettingsBtn"),
    topSettingsBtn: document.getElementById("topSettingsBtn"),
    closeSettingsBtn: document.getElementById("closeSettingsBtn"),
    cancelSettingsBtn: document.getElementById("cancelSettingsBtn"),
    saveSettingsBtn: document.getElementById("saveSettingsBtn"),
    testAiBtn: document.getElementById("testAiBtn"),
    testFirecrawlBtn: document.getElementById("testFirecrawlBtn"),
    aiTestResult: document.getElementById("aiTestResult"),
    geminiFields: document.getElementById("geminiFields"),
    openaiFields: document.getElementById("openaiFields"),
    geminiApiKey: document.getElementById("geminiApiKey"),
    geminiModel: document.getElementById("geminiModel"),
    geminiKeyStatus: document.getElementById("geminiKeyStatus"),
    openaiApiKey: document.getElementById("openaiApiKey"),
    openaiModel: document.getElementById("openaiModel"),
    openaiKeyStatus: document.getElementById("openaiKeyStatus"),
    firecrawlApiKey: document.getElementById("firecrawlApiKey"),
    firecrawlMode: document.getElementById("firecrawlMode"),
    firecrawlKeyStatus: document.getElementById("firecrawlKeyStatus"),
    maxResultsInput: document.getElementById("maxResultsInput"),
    timeoutInput: document.getElementById("timeoutInput"),
    playwrightCheckbox: document.getElementById("playwrightCheckbox"),
  };

  let modalSelectedProvider = "gemini";

  function formatCOP(value) {
    if (value == null || isNaN(value)) return "N/A";
    return "$" + Math.round(Number(value)).toLocaleString("es-CO");
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Lightweight safe Markdown renderer for AI analysis
  function renderMarkdown(md) {
    if (!md) return "";
    let safe = escapeHtml(md);

    // Headings
    safe = safe.replace(/^####\s+(.+)$/gm, "<h4>$1</h4>");
    safe = safe.replace(/^###\s+(.+)$/gm, "<h3>$1</h3>");
    safe = safe.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");

    // Blockquotes
    safe = safe.replace(/^&gt;\s+(.+)$/gm, "<blockquote>$1</blockquote>");

    // Links [text](https://...)
    safe = safe.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1 ↗</a>'
    );

    // Bold & italic
    safe = safe.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    safe = safe.replace(/\*([^*]+)\*/g, "<em>$1</em>");

    // Bullet lists
    const lines = safe.split("\n");
    const out = [];
    let inList = false;
    for (const line of lines) {
      const m = line.match(/^\s*[-•]\s+(.+)$/);
      const mNum = line.match(/^\s*\d+\.\s+(.+)$/);
      if (m || mNum) {
        if (!inList) {
          out.push("<ul>");
          inList = true;
        }
        out.push(`<li>${(m ? m[1] : mNum[1])}</li>`);
      } else {
        if (inList) {
          out.push("</ul>");
          inList = false;
        }
        if (line.trim() === "") {
          out.push("");
        } else if (/^<(h3|h4|blockquote)/.test(line)) {
          out.push(line);
        } else {
          out.push(`<p>${line}</p>`);
        }
      }
    }
    if (inList) out.push("</ul>");
    return out.join("\n");
  }

  function scrollToBottom() {
    els.chatFeed.scrollTo({ top: els.chatFeed.scrollHeight, behavior: "smooth" });
  }

  async function init() {
    await Promise.all([loadStores(), loadSettings(), loadHistory()]);
    bindEvents();
  }

  async function loadStores() {
    const res = await fetch("/api/stores");
    state.stores = await res.json();
    state.selectedStoreIds = new Set(state.stores.map((s) => s.id));
    renderStoreToggles();
  }

  function renderStoreToggles() {
    els.storeToggles.innerHTML = "";
    state.stores.forEach((store) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "store-toggle" + (state.selectedStoreIds.has(store.id) ? " active" : "");
      btn.innerHTML = `<span class="dot" style="background:${store.color}"></span>${escapeHtml(store.name)}`;
      btn.addEventListener("click", () => {
        if (state.selectedStoreIds.has(store.id)) {
          if (state.selectedStoreIds.size > 1) state.selectedStoreIds.delete(store.id);
        } else {
          state.selectedStoreIds.add(store.id);
        }
        renderStoreToggles();
      });
      els.storeToggles.appendChild(btn);
    });
  }

  async function loadSettings() {
    const res = await fetch("/api/settings");
    state.settings = await res.json();
    updateAiStatusBadge();
  }

  function updateAiStatusBadge() {
    const s = state.settings;
    if (!s) return;
    const hasGemini = s.gemini_configured;
    const hasOpenAI = s.openai_configured;
    if (s.ai_provider === "gemini" && hasGemini) {
      els.aiStatusDot.classList.add("online");
      els.aiStatusLabel.textContent = `Gemini (${s.gemini_model})`;
    } else if (s.ai_provider === "openai" && hasOpenAI) {
      els.aiStatusDot.classList.add("online");
      els.aiStatusLabel.textContent = `OpenAI (${s.openai_model})`;
    } else if (hasGemini || hasOpenAI) {
      els.aiStatusDot.classList.add("online");
      els.aiStatusLabel.textContent = hasGemini ? `Gemini activo` : `OpenAI activo`;
    } else {
      els.aiStatusDot.classList.remove("online");
      els.aiStatusLabel.textContent = "Sin API Key (modo local)";
    }
  }

  async function loadHistory() {
    const res = await fetch("/api/history");
    state.history = await res.json();
    renderHistory();
  }

  function renderHistory() {
    if (!state.history.length) {
      els.historyList.innerHTML = `<div class="empty-hint">Tus búsquedas aparecerán aquí</div>`;
      return;
    }
    els.historyList.innerHTML = "";
    state.history.forEach((item) => {
      const div = document.createElement("div");
      div.className = "history-item" + (state.activeComparison?.search_id === item.search_id ? " active" : "");
      div.innerHTML = `
        <div style="min-width:0;flex:1">
          <div class="history-title">${escapeHtml(item.search_query || item.raw_message)}</div>
          <div class="history-meta">
            ${item.min_price ? `Desde ${formatCOP(item.min_price)}` : "Sin ofertas"} · ${item.total_offers || 0} res.
          </div>
        </div>
        <button class="history-del" title="Eliminar">✕</button>
      `;
      div.addEventListener("click", async (e) => {
        if (e.target.closest(".history-del")) return;
        await openHistoricalComparison(item.search_id);
        els.sidebar.classList.remove("open");
      });
      div.querySelector(".history-del").addEventListener("click", async (e) => {
        e.stopPropagation();
        await fetch(`/api/history/${item.search_id}`, { method: "DELETE" });
        await loadHistory();
      });
      els.historyList.appendChild(div);
    });
  }

  async function openHistoricalComparison(searchId) {
    const res = await fetch(`/api/history/${searchId}`);
    if (!res.ok) return;
    const comp = await res.json();
    state.activeComparison = comp;
    state.followupHistory = [];
    els.welcomeView.classList.add("hidden");
    els.messagesContainer.innerHTML = "";
    appendUserBubble(comp.raw_message);
    const box = document.createElement("div");
    box.className = "msg-assistant";
    renderComparisonInto(box, comp);
    els.messagesContainer.appendChild(box);
    activateComparisonControls(comp);
    renderHistory();
    scrollToBottom();
  }

  function activateComparisonControls(comp) {
    els.topbarTitle.textContent = `Comparativa: ${comp.intent?.search_query || comp.raw_message}`;
    els.exportCsvBtn.classList.toggle("hidden", !comp.offers || comp.offers.length === 0);
    els.composerModeBar.classList.remove("hidden");
  }

  function setComposerMode(mode) {
    state.composerMode = mode;
    els.modeSearchBtn.classList.toggle("active", mode === "search");
    els.modeFollowupBtn.classList.toggle("active", mode === "followup");
    if (mode === "followup") {
      els.chatInput.placeholder = "Pregunta sobre las ofertas actuales (ej: ¿Cuál conviene más? ¿Alguna tiene descuento con tarjeta?)...";
      els.sendBtnText.textContent = "Preguntar";
    } else {
      els.chatInput.placeholder = "Ej: Televisor LG 55 OLED, referencia SM-S928B, o 'nevera Haceb no frost menos de 2 millones'...";
      els.sendBtnText.textContent = "Buscar";
    }
  }

  function appendUserBubble(text) {
    const div = document.createElement("div");
    div.className = "msg-user";
    div.textContent = text;
    els.messagesContainer.appendChild(div);
  }

  async function runSearchStream(message) {
    if (state.isBusy) return;
    state.isBusy = true;
    els.sendBtn.disabled = true;
    els.welcomeView.classList.add("hidden");

    appendUserBubble(message);

    const assistantBox = document.createElement("div");
    assistantBox.className = "msg-assistant";
    assistantBox.innerHTML = `
      <div class="progress-panel">
        <div class="progress-header">
          <span class="spinner"></span>
          <span class="progress-title">Analizando tu consulta...</span>
        </div>
        <div class="store-progress-grid"></div>
      </div>
    `;
    els.messagesContainer.appendChild(assistantBox);
    scrollToBottom();

    const progressTitle = assistantBox.querySelector(".progress-title");
    const progressGrid = assistantBox.querySelector(".store-progress-grid");
    const storePills = new Map();

    const selected = Array.from(state.selectedStoreIds);
    selected.forEach((sid) => {
      const st = state.stores.find((s) => s.id === sid);
      if (!st) return;
      const pill = document.createElement("div");
      pill.className = "store-pill";
      pill.innerHTML = `<span class="dot" style="width:7px;height:7px;border-radius:50%;background:${st.color}"></span><strong>${escapeHtml(st.name)}</strong> <span class="st-msg">en espera</span>`;
      progressGrid.appendChild(pill);
      storePills.set(sid, pill);
    });

    try {
      const resp = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, stores: selected }),
      });

      if (!resp.ok || !resp.body) {
        throw new Error(`Error del servidor (${resp.status})`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";

        for (const chunk of chunks) {
          const line = chunk.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          const payload = JSON.parse(line.slice(5).trim());

          if (payload.stage === "understanding") {
            progressTitle.textContent = "Interpretando referencia y filtros...";
          } else if (payload.stage === "intent_ready") {
            progressTitle.textContent = `${payload.intent.intent_summary} — consultando ${selected.length} tiendas...`;
            storePills.forEach((pill) => {
              pill.querySelector(".st-msg").textContent = "buscando...";
            });
          } else if (payload.stage === "store_done") {
            const s = payload.store;
            const pill = storePills.get(s.store_id);
            if (pill) {
              pill.className = `store-pill ${s.status}`;
              const secs = (s.elapsed_ms / 1000).toFixed(1);
              if (s.status === "ok") {
                pill.querySelector(".st-msg").textContent = `✓ ${s.count} ofertas (${secs}s)`;
              } else if (s.status === "empty") {
                pill.querySelector(".st-msg").textContent = `0 resultados (${secs}s)`;
              } else {
                pill.querySelector(".st-msg").textContent = `⚠ ${s.status} (${secs}s)`;
              }
            }
          } else if (payload.stage === "analyzing") {
            progressTitle.textContent = `Comparando ${payload.total_raw} productos y generando recomendación...`;
          } else if (payload.stage === "complete") {
            const comp = payload.comparison;
            state.activeComparison = comp;
            state.followupHistory = [];
            renderComparisonInto(assistantBox, comp);
            activateComparisonControls(comp);
            await loadHistory();
            scrollToBottom();
          } else if (payload.stage === "error") {
            assistantBox.innerHTML = `<div class="msg-error">⚠ Error: ${escapeHtml(payload.message)}</div>`;
          }
        }
      }
    } catch (err) {
      assistantBox.innerHTML = `<div class="msg-error">⚠ No se pudo completar la búsqueda: ${escapeHtml(err.message)}</div>`;
    } finally {
      state.isBusy = false;
      els.sendBtn.disabled = false;
    }
  }

  async function runFollowup(question) {
    if (state.isBusy || !state.activeComparison) return;
    state.isBusy = true;
    els.sendBtn.disabled = true;

    appendUserBubble(question);
    const box = document.createElement("div");
    box.className = "msg-assistant";
    box.innerHTML = `<div class="progress-header"><span class="spinner"></span><span>Analizando ofertas actuales...</span></div>`;
    els.messagesContainer.appendChild(box);
    scrollToBottom();

    try {
      const resp = await fetch("/api/chat/followup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          search_id: state.activeComparison.search_id,
          question,
          history: state.followupHistory,
        }),
      });
      const data = await resp.json();
      state.followupHistory.push({ role: "user", content: question });
      state.followupHistory.push({ role: "assistant", content: data.answer || "" });

      const badge = data.ai_used
        ? `Respuesta con ${escapeHtml(data.ai_provider || "IA")}`
        : `Respuesta local`;
      box.innerHTML = `
        <div class="ai-box" style="margin-bottom:0">
          <div class="ai-box-header"><span class="ai-badge">${badge}</span></div>
          <div class="ai-content">${renderMarkdown(data.answer)}</div>
        </div>
      `;
    } catch (err) {
      box.innerHTML = `<div class="msg-error">⚠ Error en consulta: ${escapeHtml(err.message)}</div>`;
    } finally {
      state.isBusy = false;
      els.sendBtn.disabled = false;
      scrollToBottom();
    }
  }

  function renderComparisonInto(container, comp) {
    const offers = comp.offers || [];
    const stats = comp.stats || {};
    const best = comp.best_offer;
    const bestCard = comp.best_card_offer;
    const bestDisc = comp.best_discount_offer;

    const kpiHtml = `
      <div class="kpi-grid">
        <div class="kpi-card highlight">
          <div class="kpi-label">Mejor precio encontrado</div>
          <div class="kpi-value">${best ? formatCOP(best.price) : "Sin resultados"}</div>
          <div class="kpi-sub">
            <span>${best ? escapeHtml(best.store_name) : "—"}</span>
            ${best ? `<a class="kpi-link" href="${escapeHtml(best.url)}" target="_blank" rel="noopener">Visitar ↗</a>` : ""}
          </div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Ahorro potencial</div>
          <div class="kpi-value">${stats.savings_vs_max ? formatCOP(stats.savings_vs_max) : "$0"}</div>
          <div class="kpi-sub">
            <span>Promedio: ${formatCOP(stats.avg_price)}</span>
            <span>${stats.total_offers || 0} ofertas</span>
          </div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Mayor descuento</div>
          <div class="kpi-value">${bestDisc?.discount_percent ? `-${bestDisc.discount_percent}%` : "N/A"}</div>
          <div class="kpi-sub">
            <span>${bestDisc ? `${escapeHtml(bestDisc.store_name)} · ${formatCOP(bestDisc.price)}` : "Sin descuento listado"}</span>
          </div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Mejor precio tarjeta</div>
          <div class="kpi-value">${bestCard?.card_price ? formatCOP(bestCard.card_price) : "N/A"}</div>
          <div class="kpi-sub">
            <span>${bestCard ? `${escapeHtml(bestCard.store_name)} (${escapeHtml(bestCard.card_promo_text || "Tarjeta")})` : "Sin promo tarjeta detectada"}</span>
          </div>
        </div>
      </div>
    `;

    const aiBadge = comp.ai_used
      ? `Análisis comparativo con ${(comp.ai_provider || "IA").toUpperCase()}`
      : `Resumen comparativo automático`;

    const aiErrorNote = comp.ai_error
      ? `<div class="ai-error-note">Nota IA: ${escapeHtml(comp.ai_error)}</div>`
      : "";

    const aiHtml = `
      <div class="ai-box">
        <div class="ai-box-header">
          <span class="ai-badge">${aiBadge}</span>
          <span class="ai-query-meta">Consulta: <strong>${escapeHtml(comp.intent?.search_query || comp.raw_message)}</strong></span>
        </div>
        <div class="ai-content">${renderMarkdown(comp.ai_analysis)}</div>
        ${aiErrorNote}
      </div>
    `;

    const uniqueStores = Array.from(new Map(offers.map((o) => [o.store_id, o.store_name])).entries());
    const storeOptions = uniqueStores
      .map(([id, name]) => `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`)
      .join("");

    container.innerHTML = `
      ${kpiHtml}
      ${aiHtml}
      <div class="results-toolbar">
        <div class="tabs">
          <button type="button" class="tab-btn active" data-tab="cards">Tarjetas (${offers.length})</button>
          <button type="button" class="tab-btn" data-tab="table">Tabla comparativa</button>
          <button type="button" class="tab-btn" data-tab="diag">Tiendas (${(comp.store_statuses || []).length})</button>
        </div>
        <div class="filter-controls">
          <input type="text" class="filter-input" placeholder="Filtrar título o marca..." />
          <select class="filter-select store-filter">
            <option value="all">Todas las tiendas</option>
            ${storeOptions}
          </select>
          <select class="filter-select sort-filter">
            <option value="price_asc">Menor precio</option>
            <option value="price_desc">Mayor precio</option>
            <option value="discount_desc">Mayor descuento</option>
            <option value="relevance_desc">Más relevante</option>
          </select>
        </div>
      </div>
      <div class="tab-pane pane-cards"></div>
      <div class="tab-pane pane-table hidden"></div>
      <div class="tab-pane pane-diag hidden"></div>
    `;

    const paneCards = container.querySelector(".pane-cards");
    const paneTable = container.querySelector(".pane-table");
    const paneDiag = container.querySelector(".pane-diag");
    const filterInput = container.querySelector(".filter-input");
    const storeFilter = container.querySelector(".store-filter");
    const sortFilter = container.querySelector(".sort-filter");

    function getFilteredOffers() {
      const q = filterInput.value.trim().toLowerCase();
      const st = storeFilter.value;
      const sort = sortFilter.value;

      let list = offers.filter((o) => {
        if (st !== "all" && o.store_id !== st) return false;
        if (q && !`${o.title} ${o.brand} ${o.store_name}`.toLowerCase().includes(q)) return false;
        return true;
      });

      list = [...list];
      if (sort === "price_asc") list.sort((a, b) => a.price - b.price);
      else if (sort === "price_desc") list.sort((a, b) => b.price - a.price);
      else if (sort === "discount_desc") list.sort((a, b) => (b.discount_percent || 0) - (a.discount_percent || 0));
      else if (sort === "relevance_desc") list.sort((a, b) => (b.relevance_score || 0) - (a.relevance_score || 0));
      return list;
    }

    function renderViews() {
      const list = getFilteredOffers();
      if (!list.length) {
        const empty = `<div class="empty-hint" style="padding:20px;text-align:center">No hay ofertas que coincidan con el filtro seleccionado.</div>`;
        paneCards.innerHTML = empty;
        paneTable.innerHTML = empty;
      } else {
        paneCards.innerHTML = `<div class="offers-grid">${list
          .map((o) => {
            const isBest = best && o.id === best.id;
            const promos = (o.promo_badges || [])
              .slice(0, 3)
              .map((p) => `<span class="promo-pill">${escapeHtml(p)}</span>`)
              .join("");
            const ship = o.shipping_info
              ? `<span class="promo-pill">🚚 ${escapeHtml(o.shipping_info)}</span>`
              : "";
            return `
              <article class="offer-card ${isBest ? "best-deal" : ""}">
                <div class="offer-img-wrap">
                  <span class="store-tag"><span class="store-tag-dot" style="background:${escapeHtml(o.store_color)}"></span>${escapeHtml(o.store_name)}</span>
                  ${o.discount_percent ? `<span class="discount-tag">-${o.discount_percent}%</span>` : ""}
                  ${isBest ? `<span class="best-ribbon">Mejor precio</span>` : ""}
                  ${
                    o.image_url
                      ? `<img src="${escapeHtml(o.image_url)}" alt="${escapeHtml(o.title)}" loading="lazy" onerror="this.style.display='none'" />`
                      : `<div class="offer-img-placeholder">${escapeHtml(o.store_name)}</div>`
                  }
                </div>
                <div class="offer-body">
                  <div class="offer-title" title="${escapeHtml(o.title)}">${escapeHtml(o.title)}</div>
                  <div class="price-block">
                    ${o.list_price && o.list_price > o.price ? `<span class="price-list">${formatCOP(o.list_price)}</span>` : ""}
                    <span class="price-now">${formatCOP(o.price)}</span>
                  </div>
                  ${
                    o.card_price
                      ? `<div class="card-promo-box">💳 ${formatCOP(o.card_price)} · ${escapeHtml(o.card_promo_text || "Con tarjeta aliada")}</div>`
                      : ""
                  }
                  <div class="promo-pills">${promos}${ship}</div>
                  <div class="offer-footer">
                    <a class="visit-btn" href="${escapeHtml(o.url)}" target="_blank" rel="noopener noreferrer">Visitar en ${escapeHtml(o.store_name)} ↗</a>
                  </div>
                </div>
              </article>
            `;
          })
          .join("")}</div>`;

        paneTable.innerHTML = `
          <div class="table-wrap">
            <table class="comp-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Tienda</th>
                  <th>Producto</th>
                  <th>Precio oferta</th>
                  <th>Precio antes</th>
                  <th>Dto.</th>
                  <th>Promo tarjeta / extra</th>
                  <th>Enlace</th>
                </tr>
              </thead>
              <tbody>
                ${list
                  .map((o, idx) => {
                    const isBest = best && o.id === best.id;
                    const promoText = [
                      o.card_price ? `💳 ${formatCOP(o.card_price)} (${o.card_promo_text || "Tarjeta"})` : "",
                      ...(o.promo_badges || []).slice(0, 2),
                    ]
                      .filter(Boolean)
                      .join(" · ");
                    return `
                      <tr class="${isBest ? "top-row" : ""}">
                        <td><strong>${idx + 1}</strong></td>
                        <td>
                          <span class="store-chip-sm">
                            <span class="dot" style="background:${escapeHtml(o.store_color)}"></span>
                            ${escapeHtml(o.store_name)}
                          </span>
                        </td>
                        <td style="max-width:340px">
                          <div class="table-product-title">${escapeHtml(o.title)}</div>
                          ${o.seller ? `<div class="table-seller">Vendido por: ${escapeHtml(o.seller)}</div>` : ""}
                        </td>
                        <td><strong class="table-price">${formatCOP(o.price)}</strong></td>
                        <td class="table-list-price">${o.list_price && o.list_price > o.price ? formatCOP(o.list_price) : "—"}</td>
                        <td>${o.discount_percent ? `<strong class="table-discount">-${o.discount_percent}%</strong>` : "—"}</td>
                        <td class="table-promo">${escapeHtml(promoText || "—")}</td>
                        <td><a class="table-link" href="${escapeHtml(o.url)}" target="_blank" rel="noopener noreferrer">Abrir ↗</a></td>
                      </tr>
                    `;
                  })
                  .join("")}
              </tbody>
            </table>
          </div>
        `;
      }

      paneDiag.innerHTML = `
        <div class="diag-grid">
          ${(comp.store_statuses || [])
            .map(
              (s) => `
              <div class="diag-card">
                <div class="diag-head">
                  <span class="store-chip-sm">
                    <span class="dot" style="background:${escapeHtml(s.store_color)}"></span>
                    ${escapeHtml(s.store_name)}
                  </span>
                  <span class="promo-pill">${escapeHtml(s.status.toUpperCase())}</span>
                </div>
                <div class="diag-meta">
                  Ofertas extraídas: <strong>${s.count}</strong> · Tiempo: <strong>${(s.elapsed_ms / 1000).toFixed(2)}s</strong> · Método: <code>${escapeHtml(s.method)}</code>
                </div>
                ${s.error_message ? `<div class="diag-error">${escapeHtml(s.error_message)}</div>` : ""}
              </div>
            `
            )
            .join("")}
        </div>
      `;
    }

    renderViews();

    container.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        container.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const tab = btn.dataset.tab;
        paneCards.classList.toggle("hidden", tab !== "cards");
        paneTable.classList.toggle("hidden", tab !== "table");
        paneDiag.classList.toggle("hidden", tab !== "diag");
      });
    });

    filterInput.addEventListener("input", renderViews);
    storeFilter.addEventListener("change", renderViews);
    sortFilter.addEventListener("change", renderViews);
  }

  // Settings Modal logic
  function openSettingsModal() {
    const s = state.settings || {};
    modalSelectedProvider = s.ai_provider || "gemini";
    syncProviderTabs();
    els.geminiApiKey.value = s.gemini_key_preview || "";
    els.geminiModel.value = s.gemini_model || "gemini-2.5-flash";
    els.geminiKeyStatus.textContent = s.gemini_configured ? "✓ Clave guardada" : "";
    els.openaiApiKey.value = s.openai_key_preview || "";
    els.openaiModel.value = s.openai_model || "gpt-4o-mini";
    els.openaiKeyStatus.textContent = s.openai_configured ? "✓ Clave guardada" : "";
    els.firecrawlApiKey.value = s.firecrawl_key_preview || "";
    els.firecrawlMode.value = s.firecrawl_mode || "fallback";
    els.firecrawlKeyStatus.textContent = s.firecrawl_configured ? "✓ Clave guardada" : "";
    els.maxResultsInput.value = s.max_results_per_store || 10;
    els.timeoutInput.value = s.store_timeout_seconds || 25;
    els.playwrightCheckbox.checked = s.use_playwright_fallback !== false;
    els.aiTestResult.classList.add("hidden");
    els.settingsModal.classList.remove("hidden");
  }

  function syncProviderTabs() {
    document.querySelectorAll(".provider-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.provider === modalSelectedProvider);
    });
    els.geminiFields.classList.toggle("hidden", modalSelectedProvider !== "gemini");
    els.openaiFields.classList.toggle("hidden", modalSelectedProvider !== "openai");
  }

  async function saveSettings() {
    const payload = {
      ai_provider: modalSelectedProvider,
      gemini_api_key: els.geminiApiKey.value.trim(),
      gemini_model: els.geminiModel.value.trim() || "gemini-2.5-flash",
      openai_api_key: els.openaiApiKey.value.trim(),
      openai_model: els.openaiModel.value.trim() || "gpt-4o-mini",
      firecrawl_api_key: els.firecrawlApiKey.value.trim(),
      firecrawl_mode: els.firecrawlMode.value || "fallback",
      use_playwright_fallback: els.playwrightCheckbox.checked,
      max_results_per_store: Number(els.maxResultsInput.value) || 10,
      store_timeout_seconds: Number(els.timeoutInput.value) || 25,
    };
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.settings = await res.json();
    updateAiStatusBadge();
    els.settingsModal.classList.add("hidden");
  }

  async function testAiConnection() {
    els.aiTestResult.className = "test-result";
    els.aiTestResult.textContent = "Probando conexión con el proveedor de IA...";
    els.aiTestResult.classList.remove("hidden");

    const apiKey =
      modalSelectedProvider === "gemini"
        ? els.geminiApiKey.value.trim()
        : els.openaiApiKey.value.trim();
    const model =
      modalSelectedProvider === "gemini"
        ? els.geminiModel.value.trim()
        : els.openaiModel.value.trim();

    const res = await fetch("/api/settings/test-ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: modalSelectedProvider, api_key: apiKey, model }),
    });
    const data = await res.json();
    els.aiTestResult.className = "test-result " + (data.ok ? "ok" : "err");
    els.aiTestResult.textContent = data.message || (data.ok ? "Conexión exitosa" : "Error de conexión");
  }

  async function testFirecrawlConnection() {
    els.aiTestResult.className = "test-result";
    els.aiTestResult.textContent = "Probando conexión con Firecrawl...";
    els.aiTestResult.classList.remove("hidden");

    const res = await fetch("/api/settings/test-firecrawl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: els.firecrawlApiKey.value.trim() }),
    });
    const data = await res.json();
    els.aiTestResult.className = "test-result " + (data.ok ? "ok" : "err");
    els.aiTestResult.textContent = data.message || (data.ok ? "Firecrawl activo" : "Error de conexión");
  }

  function bindEvents() {
    els.openSidebarBtn.addEventListener("click", () => els.sidebar.classList.add("open"));
    els.closeSidebarBtn.addEventListener("click", () => els.sidebar.classList.remove("open"));

    els.toggleAllStoresBtn.addEventListener("click", () => {
      if (state.selectedStoreIds.size === state.stores.length) {
        state.selectedStoreIds = new Set([state.stores[0].id]);
      } else {
        state.selectedStoreIds = new Set(state.stores.map((s) => s.id));
      }
      renderStoreToggles();
    });

    const themeToggleBtn = document.getElementById("themeToggleBtn");
    function syncThemeLabel() {
      if (!themeToggleBtn) return;
      const isDark = document.documentElement.getAttribute("data-theme") === "dark";
      themeToggleBtn.textContent = isDark ? "Modo claro" : "Modo oscuro";
      themeToggleBtn.setAttribute("aria-pressed", String(isDark));
    }
    syncThemeLabel();
    if (themeToggleBtn) {
      themeToggleBtn.addEventListener("click", () => {
        const isDark = document.documentElement.getAttribute("data-theme") === "dark";
        const next = isDark ? "light" : "dark";
        if (next === "dark") {
          document.documentElement.setAttribute("data-theme", "dark");
        } else {
          document.documentElement.removeAttribute("data-theme");
        }
        localStorage.setItem("ofertaco-theme", next);
        syncThemeLabel();
      });
    }

    const globalBrandLink = document.getElementById("globalBrandLink");
    if (globalBrandLink) {
      globalBrandLink.addEventListener("click", (e) => {
        e.preventDefault();
        els.newChatBtn.click();
      });
    }

    els.newChatBtn.addEventListener("click", () => {
      state.activeComparison = null;
      state.followupHistory = [];
      setComposerMode("search");
      els.composerModeBar.classList.add("hidden");
      els.exportCsvBtn.classList.add("hidden");
      els.topbarTitle.textContent = "Comparador de ofertas en Colombia";
      els.messagesContainer.innerHTML = "";
      els.welcomeView.classList.remove("hidden");
      els.chatInput.focus();
      renderHistory();
      els.sidebar.classList.remove("open");
    });

    document.querySelectorAll(".example-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        setComposerMode("search");
        runSearchStream(chip.dataset.prompt);
      });
    });

    els.modeSearchBtn.addEventListener("click", () => setComposerMode("search"));
    els.modeFollowupBtn.addEventListener("click", () => setComposerMode("followup"));

    els.chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = els.chatInput.value.trim();
      if (!text || state.isBusy) return;
      els.chatInput.value = "";
      els.chatInput.style.height = "auto";
      if (state.composerMode === "followup" && state.activeComparison) {
        runFollowup(text);
      } else {
        runSearchStream(text);
      }
    });

    els.chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        els.chatForm.dispatchEvent(new Event("submit"));
      }
    });

    els.chatInput.addEventListener("input", () => {
      els.chatInput.style.height = "auto";
      els.chatInput.style.height = Math.min(els.chatInput.scrollHeight, 120) + "px";
    });

    els.exportCsvBtn.addEventListener("click", () => {
      if (!state.activeComparison) return;
      window.location.href = `/api/export/${state.activeComparison.search_id}`;
    });

    // Settings modal
    els.openSettingsBtn.addEventListener("click", openSettingsModal);
    els.topSettingsBtn.addEventListener("click", openSettingsModal);
    els.closeSettingsBtn.addEventListener("click", () => els.settingsModal.classList.add("hidden"));
    els.cancelSettingsBtn.addEventListener("click", () => els.settingsModal.classList.add("hidden"));
    els.settingsModal.addEventListener("click", (e) => {
      if (e.target === els.settingsModal) els.settingsModal.classList.add("hidden");
    });
    document.querySelectorAll(".provider-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        modalSelectedProvider = btn.dataset.provider;
        syncProviderTabs();
      });
    });
    els.saveSettingsBtn.addEventListener("click", saveSettings);
    els.testAiBtn.addEventListener("click", testAiConnection);
    els.testFirecrawlBtn.addEventListener("click", testFirecrawlConnection);

    document.getElementById("logoutBtn").addEventListener("click", async () => {
      await fetch("/api/auth/logout", { method: "POST" });
      location.replace("/login");
    });
    document.getElementById("changePwdBtn").addEventListener("click", async () => {
      const current = prompt("Contraseña actual:");
      if (!current) return;
      const next = prompt("Nueva contraseña (mín. 8 caracteres, letras y números):");
      if (!next) return;
      const res = await fetch("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      const data = await res.json().catch(() => ({}));
      alert(res.ok ? "Contraseña actualizada." : data.detail || "No se pudo cambiar la contraseña.");
    });
  }

  init();
})();

/* Livecaster UI. Plain ES2020, no build step. */
(() => {
  "use strict";

  const state = {
    session: null,
    outline: [],
    nodesById: new Map(),      // node id -> outline node
    states: {},                // node id -> NodeState
    elements: new Map(),       // node id -> {root, row, text, badge, reason, segue, frac}
    order: [],                 // coverable node ids in document order (for j/k)
    selected: null,
    segments: [],
    buildId: window.BUILD_ID,
    status: {},
    connected: false,
  };

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  const hms = (s) => {
    if (s === null || s === undefined) return "--:--:--";
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return [h, m, sec].map((x) => String(x).padStart(2, "0")).join(":");
  };

  const inlineMd = (s) =>
    (window.marked && window.marked.parseInline) ? window.marked.parseInline(s) : escapeHtml(s);

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // ---------------------------------------------------------------- websocket

  let ws = null;
  let backoff = 500;

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => {
      backoff = 500;
      state.connected = true;
      setPill($("#llm-status"), "LLM connecting…", "");
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      handle(msg);
    };
    ws.onclose = () => {
      state.connected = false;
      setPill($("#llm-status"), "disconnected", "err");
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 10000);
    };
    ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
  }

  function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  }

  function handle(msg) {
    switch (msg.type) {
      case "hello":
        if (msg.build_id && state.buildId && msg.build_id !== state.buildId) {
          location.reload();
          return;
        }
        state.buildId = msg.build_id;
        document.title = `Livecaster — ${msg.session_id}`;
        break;
      case "state":
        applyState(msg.session);
        break;
      case "patch":
        applyPatch(msg);
        break;
      case "segment":
        addSegment(msg.segment);
        break;
      case "status":
        applyStatus(msg);
        break;
      case "toast":
        toast(msg.level, msg.text);
        break;
      case "done":
        showDone(msg);
        break;
    }
  }

  // ------------------------------------------------------------------- state

  function applyState(session) {
    state.session = session;
    state.outline = session.outline || [];
    state.states = session.nodes || {};
    state.nodesById = new Map(state.outline.map((n) => [n.id, n]));
    buildOutline();
    renderSide();
    renderStatusBar();
  }

  function applyPatch(patch) {
    if (patch.nodes) {
      for (const [id, st] of Object.entries(patch.nodes)) {
        state.states[id] = st;
        updateNode(id);
      }
      updateAncestorFractions(Object.keys(patch.nodes));
    }
    if (patch.suggestions) {
      state.session.suggestions = patch.suggestions;
      markCurrent();
    }
    if (patch.mentions) state.session.mentions = patch.mentions;
    if (patch.usage) state.session.usage = patch.usage;
    if (patch.language) state.session.language = patch.language;
    if (patch.session_status) {
      state.session.status = patch.session_status;
      renderStatusBar();
    }
    renderSide();
    updateEdges();
  }

  // ----------------------------------------------------------------- outline

  function buildOutline() {
    const root = $("#outline");
    root.innerHTML = "";
    state.elements.clear();
    state.order = [];
    for (const node of state.outline) {
      if (node.kind === "meta") continue;
      const wrapper = el("div", `node ${node.kind}`);
      if (node.kind === "heading") wrapper.classList.add(`h${node.level}`);
      else wrapper.classList.add(`depth-${Math.min(node.level + 1, 4)}`);
      wrapper.dataset.id = node.id;

      const row = el("div", "row");
      const keycap = el("span", "keycap hidden");
      const text = el("span", "text");
      text.innerHTML = inlineMd(node.text_md || node.text || "");
      const frac = el("span", "frac");
      const badge = el("span", "badge-time");
      row.append(keycap, text, frac, badge);

      const reason = el("div", "reason hidden");
      const segue = el("div", "segue hidden");
      wrapper.append(row, reason, segue);
      root.appendChild(wrapper);

      state.elements.set(node.id, { root: wrapper, row, text, badge, reason, segue, frac, keycap });
      if (node.coverable) state.order.push(node.id);

      row.addEventListener("click", (e) => {
        select(node.id);
        if (e.altKey) togglePin(node.id);
      });
      row.addEventListener("dblclick", () => toggleCovered(node.id));
    }
    for (const id of state.elements.keys()) updateNode(id);
    updateAncestorFractions(Array.from(state.elements.keys()));
    markCurrent();
    if (!state.selected && state.order.length) select(state.order[0], false);
    updateEdges();
  }

  function updateNode(id) {
    const parts = state.elements.get(id);
    if (!parts) return;
    const st = state.states[id] || {};
    const cls = parts.root.classList;
    cls.remove("warm", "touched", "covered", "skipped", "hot", "pinned");
    if (st.warm > 0 && st.status !== "covered") cls.add("warm");
    if (st.status === "touched") cls.add("touched");
    if (st.status === "covered") cls.add("covered");
    if (st.status === "skipped") cls.add("skipped");
    if (st.pinned) cls.add("pinned");

    parts.badge.textContent = st.status === "covered" && st.covered_at != null ? hms(st.covered_at) : "";

    if (st.hot && st.status !== "covered" && st.status !== "skipped") {
      cls.add("hot");
      parts.keycap.textContent = st.hot.rank && st.hot.rank <= 3 ? String(st.hot.rank) : "";
      parts.keycap.classList.toggle("hidden", !(st.hot.rank && st.hot.rank <= 3));
      parts.reason.textContent = st.hot.reason || "";
      parts.reason.classList.toggle("hidden", !st.hot.reason);
      parts.segue.textContent = st.hot.segue || "";
      parts.segue.classList.toggle("hidden", !st.hot.segue);
    } else {
      parts.keycap.classList.add("hidden");
      parts.reason.classList.add("hidden");
      parts.segue.classList.add("hidden");
    }
  }

  function coverableUnder(id) {
    const node = state.nodesById.get(id);
    if (!node) return [];
    const out = [];
    const stack = [...(node.children || [])];
    while (stack.length) {
      const child = state.nodesById.get(stack.shift());
      if (!child) continue;
      if (child.coverable) out.push(child.id);
      stack.push(...(child.children || []));
    }
    return out;
  }

  function updateAncestorFractions(changedIds) {
    const headings = new Set();
    for (const node of state.outline) if (node.kind === "heading") headings.add(node.id);
    for (const id of headings) {
      const parts = state.elements.get(id);
      if (!parts) continue;
      const leaves = coverableUnder(id);
      if (!leaves.length) { parts.frac.textContent = ""; continue; }
      const done = leaves.filter((l) => {
        const s = state.states[l];
        return s && (s.status === "covered" || s.status === "skipped");
      }).length;
      parts.frac.textContent = `${done}/${leaves.length}`;
    }
    void changedIds;
  }

  function markCurrent() {
    for (const parts of state.elements.values()) parts.root.classList.remove("current");
    const cur = state.session && state.session.suggestions && state.session.suggestions.current;
    if (cur && cur.node_id) {
      const parts = state.elements.get(cur.node_id);
      if (parts) parts.root.classList.add("current");
    }
  }

  function select(id, scroll = true) {
    if (state.selected) {
      const prev = state.elements.get(state.selected);
      if (prev) prev.root.classList.remove("selected");
    }
    state.selected = id;
    const parts = state.elements.get(id);
    if (parts) {
      parts.root.classList.add("selected");
      if (scroll) parts.root.scrollIntoView({ block: "nearest" });
    }
    send({ type: "select", node_id: id });
  }

  function moveSelection(delta) {
    if (!state.order.length) return;
    const i = state.order.indexOf(state.selected);
    const next = Math.min(state.order.length - 1, Math.max(0, (i < 0 ? 0 : i) + delta));
    select(state.order[next]);
  }

  function toggleCovered(id) {
    const st = state.states[id] || {};
    send({ type: "mark", node_id: id, status: st.status === "covered" ? "untouched" : "covered" });
  }
  function toggleSkipped(id) {
    const st = state.states[id] || {};
    send({ type: "mark", node_id: id, status: st.status === "skipped" ? "untouched" : "skipped" });
  }
  function togglePin(id) {
    const st = state.states[id] || {};
    send({ type: "pin", node_id: id, pinned: !st.pinned });
  }

  // -------------------------------------------------------- edge indicators

  function updateEdges() {
    const pane = $("#outline");
    const rect = pane.getBoundingClientRect();
    let above = 0, below = 0, firstAbove = null, firstBelow = null;
    for (const [id, st] of Object.entries(state.states)) {
      if (!st.hot || st.status === "covered" || st.status === "skipped") continue;
      const parts = state.elements.get(id);
      if (!parts) continue;
      const r = parts.root.getBoundingClientRect();
      if (r.bottom < rect.top) { above++; firstAbove = firstAbove || parts.root; }
      else if (r.top > rect.bottom) { below++; firstBelow = firstBelow || parts.root; }
    }
    const up = $("#edge-up"), down = $("#edge-down");
    up.querySelector("span").textContent = String(above);
    down.querySelector("span").textContent = String(below);
    up.classList.toggle("hidden", above === 0);
    down.classList.toggle("hidden", below === 0);
    up.onclick = () => firstAbove && firstAbove.scrollIntoView({ block: "center", behavior: "smooth" });
    down.onclick = () => firstBelow && firstBelow.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  // -------------------------------------------------------------- side panel

  function renderSide() {
    const s = state.session;
    if (!s) return;
    const sug = s.suggestions || {};

    const now = $("#now-summary");
    now.textContent = (sug.current && sug.current.summary) || "Waiting for the first tick…";
    now.classList.toggle("muted", !(sug.current && sug.current.summary));

    const next = $("#next-list");
    next.innerHTML = "";
    for (const item of sug.next || []) {
      const node = state.nodesById.get(item.node_id);
      const li = el("li");
      const head = el("div");
      head.append(el("span", "rank", `${"①②③④⑤"[(item.rank || 1) - 1] || "•"} `));
      const title = el("span", "title");
      title.innerHTML = inlineMd(node ? (node.text_md || node.text) : item.node_id);
      head.append(title);
      li.append(head);
      if (item.reason) li.append(el("div", "why", item.reason));
      if (item.segue) li.append(el("div", "segue", `„${item.segue}“`));
      li.addEventListener("click", () => select(item.node_id));
      next.append(li);
    }

    const ql = $("#questions-list");
    ql.innerHTML = "";
    for (const q of sug.questions || []) {
      const li = el("li");
      li.append(el("div", "", q.text));
      if (q.why) li.append(el("span", "why", q.why));
      if (q.node_id) li.addEventListener("click", () => select(q.node_id));
      ql.append(li);
    }

    const ml = $("#mentions-list");
    ml.innerHTML = "";
    const icons = { person: "👤", book: "📖", article: "📰", link: "🔗", tool: "🛠", product: "📦", place: "📍", event: "🎪", concept: "💡", promise: "🤝", other: "•" };
    for (const m of s.mentions || []) {
      const li = el("li");
      li.append(el("span", "", `${icons[m.kind] || "•"} `));
      li.append(el("strong", "", m.text));
      if (m.url) {
        const a = el("a", "", " 🔗");
        a.href = m.url; a.target = "_blank"; a.rel = "noreferrer";
        li.append(a);
      } else if (m.needs_link) {
        li.append(el("span", "todo", " 🔗 TODO"));
      }
      ml.append(li);
    }

    const nl = $("#newtopics-list");
    nl.innerHTML = "";
    for (const t of sug.new_topics || []) {
      const li = el("li");
      li.append(el("strong", "", t.title));
      if (t.summary) li.append(el("div", "muted", t.summary));
      nl.append(li);
    }

    setCount("questions", (sug.questions || []).length);
    setCount("mentions", (s.mentions || []).length);
    setCount("new", (sug.new_topics || []).length);
  }

  function setCount(tab, n) {
    const btn = document.querySelector(`.tab[data-tab="${tab}"]`);
    if (!btn) return;
    let c = btn.querySelector(".count");
    if (!c) { c = el("span", "count"); btn.append(c); }
    c.textContent = n ? ` ${n}` : "";
  }

  const speakerColors = new Map();
  function addSegment(seg) {
    state.segments.push(seg);
    const box = $("#transcript");
    const div = el("div", "seg");
    div.append(el("span", "t", hms(seg.t0)));
    if (seg.speaker) {
      if (!speakerColors.has(seg.speaker)) speakerColors.set(seg.speaker, speakerColors.size % 3);
      div.append(el("span", `sp sp-${speakerColors.get(seg.speaker)}`, seg.speaker));
    }
    div.append(document.createTextNode(seg.text));
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    box.append(div);
    if (atBottom) box.scrollTop = box.scrollHeight;
    while (box.children.length > 600) box.removeChild(box.firstChild);
  }

  // ------------------------------------------------------------- status bar

  function setPill(node, text, cls) {
    node.textContent = text;
    node.className = `pill${cls ? " " + cls : ""}`;
  }

  function applyStatus(st) {
    state.status = st;
    $("#clock").textContent = hms(st.clock);
    renderMeters(st.audio || {});

    const stt = st.stt || {};
    setPill($("#stt-status"),
      `STT ${stt.engine || "—"} q:${stt.queue ?? 0}${stt.last_latency_ms ? " " + stt.last_latency_ms + "ms" : ""}`,
      stt.error ? "err" : "");

    const llm = st.llm || {};
    let text = "LLM —", cls = "";
    if (llm.state === "ok" || llm.state === "ticking") {
      const age = llm.last_tick_t != null ? Math.max(0, st.clock - llm.last_tick_t) : null;
      text = `LLM ${llm.state === "ticking" ? "ticking" : "ok"}` +
        (age != null ? ` ${Math.round(age)}s ago` : "") +
        (llm.last_latency_ms ? ` ${(llm.last_latency_ms / 1000).toFixed(1)}s` : "");
      cls = "ok";
    } else if (llm.state === "error" || llm.state === "backoff") {
      text = `LLM ${llm.state}${llm.error ? ": " + llm.error.slice(0, 40) : ""}`;
      cls = "err";
    } else if (llm.state === "idle") {
      text = "LLM idle";
    }
    setPill($("#llm-status"), text, cls);

    const u = (state.session && state.session.usage) || {};
    const cached = u.prompt_tokens ? Math.round((u.cached_tokens / u.prompt_tokens) * 100) : 0;
    setPill($("#cost-status"),
      `$${(u.cost_usd || 0).toFixed(3)} · ${u.ticks || 0} ticks · ${cached}% cached`, "clickable");
    drawSparkline(llm.latencies_ms || []);

    if (state.session) state.session.status = st.session_status;
    if ((st.sync_marks || []).length) {
      const badge = $("#sync-badge");
      badge.textContent = `sync ${hms(st.sync_marks[st.sync_marks.length - 1])}`;
      badge.classList.remove("hidden");
    }
    renderStatusBar();
  }

  function drawSparkline(latencies) {
    const svg = $("#sparkline");
    const line = $("#spark-line");
    if (!latencies.length) { svg.classList.add("hidden"); return; }
    svg.classList.remove("hidden");
    const points = latencies.slice(-20);
    const max = Math.max(...points, 1000);
    const step = points.length > 1 ? 120 / (points.length - 1) : 120;
    line.setAttribute("points", points.map((ms, i) => `${(i * step).toFixed(1)},${(23 - (ms / max) * 22).toFixed(1)}`).join(" "));
    svg.classList.toggle("slow", points[points.length - 1] > 15000);
    svg.setAttribute("title", `last tick ${(points[points.length - 1] / 1000).toFixed(1)}s, worst ${(max / 1000).toFixed(1)}s`);
  }

  function showUsage() {
    const u = (state.session && state.session.usage) || {};
    const rows = Object.entries(u.by_model || {}).map(([model, m]) => `
      <tr>
        <td>${escapeHtml(model)}</td>
        <td class="num">${m.calls}</td>
        <td class="num">${m.prompt_tokens.toLocaleString()}</td>
        <td class="num">${m.prompt_tokens ? Math.round((m.cached_tokens / m.prompt_tokens) * 100) : 0}%</td>
        <td class="num">${m.completion_tokens.toLocaleString()}</td>
        <td class="num">$${m.cost_usd.toFixed(4)}</td>
      </tr>`).join("");
    $("#usage-body").innerHTML = `
      <table>
        <tr><th>model</th><th>calls</th><th>prompt</th><th>cached</th><th>output</th><th>cost</th></tr>
        ${rows || '<tr><td colspan="6">No calls yet.</td></tr>'}
        <tr><td><strong>total</strong></td><td class="num">${u.ticks || 0} ticks</td>
            <td class="num">${(u.prompt_tokens || 0).toLocaleString()}</td>
            <td class="num">${u.prompt_tokens ? Math.round((u.cached_tokens / u.prompt_tokens) * 100) : 0}%</td>
            <td class="num">${(u.completion_tokens || 0).toLocaleString()}</td>
            <td class="num"><strong>$${(u.cost_usd || 0).toFixed(4)}</strong></td></tr>
      </table>
      <p class="muted">${u.failures || 0} failed tick${(u.failures || 0) === 1 ? "" : "s"}.</p>`;
    $("#usage").showModal();
  }

  function renderMeters(levels) {
    const box = $("#meters");
    for (const [name, db] of Object.entries(levels)) {
      let meter = box.querySelector(`[data-ch="${CSS.escape(name)}"]`);
      if (!meter) {
        meter = el("div", "meter");
        meter.dataset.ch = name;
        meter.append(el("span", "", name));
        const bars = el("span", "bars");
        for (let i = 0; i < 7; i++) bars.append(el("span", "bar"));
        meter.append(bars);
        box.append(meter);
      }
      const level = Math.max(0, Math.min(7, Math.round((db + 60) / 60 * 7)));
      meter.querySelectorAll(".bar").forEach((bar, i) => {
        bar.classList.toggle("on", i < level);
        bar.classList.toggle("hot", i < level && i >= 6);
      });
    }
  }

  function renderStatusBar() {
    const status = (state.session && state.session.status) || "idle";
    const dot = $("#rec-dot");
    dot.className = "dot" + (status === "running" ? " rec" : status === "paused" ? " paused" : status === "finished" ? " done" : "");
    $("#btn-start").classList.toggle("hidden", status !== "idle");
    $("#btn-pause").classList.toggle("hidden", status !== "running");
    $("#btn-resume").classList.toggle("hidden", status !== "paused");
    $("#btn-finish").classList.toggle("hidden", status === "finished" || status === "finishing");
  }

  // ---------------------------------------------------------------- toasts

  function toast(level, text) {
    const node = el("div", `toast ${level || "info"}`, text);
    $("#toasts").append(node);
    setTimeout(() => node.remove(), 6000);
  }

  function showDone(msg) {
    const body = $("#done-body");
    body.innerHTML = "";
    if (msg.error) body.append(el("p", "", `Wrap-up failed: ${msg.error}`));
    body.append(el("p", "", `Duration ${hms(msg.duration_s)} · ${msg.ticks} ticks · $${(msg.cost_usd || 0).toFixed(3)}`));
    for (const [name, path] of Object.entries(msg.paths || {})) {
      body.append(el("code", "", `${name}: ${path}`));
    }
    $("#done").showModal();
  }

  // -------------------------------------------------------------- keyboard

  document.addEventListener("keydown", (e) => {
    const target = e.target;
    if (target instanceof Element && target.matches("input, textarea, [contenteditable]")) return;
    if (document.querySelector("dialog[open]") && e.key !== "?" && e.key !== "Escape") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const key = e.key;
    if (key === "j") { moveSelection(1); e.preventDefault(); }
    else if (key === "k") { moveSelection(-1); e.preventDefault(); }
    else if (key === "c" && state.selected) { toggleCovered(state.selected); e.preventDefault(); }
    else if (key === "x" && state.selected) { toggleSkipped(state.selected); e.preventDefault(); }
    else if (key === "p" && state.selected) { togglePin(state.selected); e.preventDefault(); }
    else if (key === "m") { send({ type: "sync_mark" }); e.preventDefault(); }
    else if (key === "t") { showTab("transcript"); e.preventDefault(); }
    else if (key === " ") {
      const status = (state.session && state.session.status) || "idle";
      const action = status === "running" ? "pause" : status === "paused" ? "resume" : "start";
      send({ type: "control", action });
      e.preventDefault();
    } else if (key === "?") { $("#help").showModal(); e.preventDefault(); }
    else if (["1", "2", "3"].includes(key)) {
      const sug = (state.session && state.session.suggestions) || {};
      const item = (sug.next || []).find((x) => String(x.rank) === key);
      if (item) select(item.node_id);
      e.preventDefault();
    }
  });

  // ------------------------------------------------------------------ chrome

  function showTab(name) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
  }

  document.addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (tab) { showTab(tab.dataset.tab); return; }
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "sync_mark") send({ type: "sync_mark" });
    else send({ type: "control", action });
  });

  $("#btn-help").addEventListener("click", () => $("#help").showModal());
  $("#cost-status").addEventListener("click", showUsage);
  $("#btn-theme").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = cur;
    try { localStorage.setItem("livecaster.theme", cur); } catch { /* ignore */ }
  });

  try {
    const saved = localStorage.getItem("livecaster.theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch { /* ignore */ }

  let scrollTimer = null;
  $("#outline").addEventListener("scroll", () => {
    if (scrollTimer) return;
    scrollTimer = setTimeout(() => { scrollTimer = null; updateEdges(); }, 150);
  });
  window.addEventListener("resize", updateEdges);

  fetch("/api/state").then((r) => r.json()).then((data) => {
    if (data.build_id && state.buildId && data.build_id !== state.buildId) location.reload();
    applyState(data.session);
    applyStatus(data.status);
  }).catch(() => { /* the websocket will deliver it */ });

  connect();
})();

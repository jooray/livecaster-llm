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
    resultText: "",
    // Live, the host reads with one eye. Everything the model says beyond a label
    // is hidden until asked for; `d` flips the whole surface to the full text.
    dense: localStorage.getItem("lc.dense") === "1",
    expanded: null,
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

  const shorten = (s, max) => (s.length <= max ? s : s.slice(0, max).replace(/\s+\S*$/, "") + "…");

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
    $("#outline-empty").classList.toggle("hidden", state.outline.length > 0);
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
      const prep = el("div", "prep hidden");
      wrapper.append(row, reason, segue, prep);
      root.appendChild(wrapper);

      state.elements.set(node.id, { root: wrapper, row, text, badge, reason, segue, frac, keycap, prep });
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
      // The map marks; it does not explain. The words live in Now, one click away.
      const show = state.dense || state.expanded === id || state.selected === id;
      parts.reason.textContent = st.hot.reason || "";
      parts.reason.classList.toggle("hidden", !(show && st.hot.reason));
      parts.segue.textContent = st.hot.segue || "";
      parts.segue.classList.toggle("hidden", !(show && st.hot.segue));
    } else {
      parts.keycap.classList.add("hidden");
      parts.reason.classList.add("hidden");
      parts.segue.classList.add("hidden");
    }
  }

  // The outline is written for reading before the show; live it has to fit a glance.
  // Cut at the first real break — em dash, colon, bracket, sentence end.
  function glance(text, max = 46) {
    let t = (text || "").replace(/\s+/g, " ").trim();
    const cut = t.search(/\s+[—–-]\s+|:\s|\s\(|[.?!]\s/);
    if (cut > 12) t = t.slice(0, cut);
    if (t.length > max) t = t.slice(0, max - 1).replace(/[\s,;.]+$/, "") + "…";
    return t;
  }

  function expand(id) {
    state.expanded = state.expanded === id ? null : id;
    renderSide();
    for (const nid of state.elements.keys()) updateNode(nid);
  }

  function setDense(on) {
    state.dense = on;
    localStorage.setItem("lc.dense", on ? "1" : "0");
    document.body.classList.toggle("dense", on);
    const btn = $("#btn-density");
    if (btn) btn.textContent = on ? "▤" : "▥";
    renderSide();
    for (const id of state.elements.keys()) updateNode(id);
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
      if (prev) {
        prev.root.classList.remove("selected");
        prev.prep.classList.add("hidden");
      }
    }
    state.selected = id;
    const parts = state.elements.get(id);
    if (parts) {
      parts.root.classList.add("selected");
      renderPreflight(id, parts);
      if (scroll) parts.root.scrollIntoView({ block: "nearest" });
    }
    send({ type: "select", node_id: id });
  }

  function renderPreflight(id, parts) {
    const pf = state.session && state.session.preflight;
    const node = pf && pf.nodes && pf.nodes[id];
    if (!node || (!node.questions.length && !node.related.length)) {
      parts.prep.classList.add("hidden");
      return;
    }
    parts.prep.innerHTML = "";
    for (const q of node.questions) parts.prep.append(el("div", "prep-q", q));
    if (node.related.length) {
      const related = node.related
        .map((r) => shorten(((state.nodesById.get(r) || {}).text || r), 48))
        .join(" · ");
      parts.prep.append(el("div", "prep-rel", `↔ ${related}`));
    }
    parts.prep.classList.remove("hidden");
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
      const head = el("div", "head");
      head.append(el("span", "rank", `${"①②③④⑤"[(item.rank || 1) - 1] || "•"}`));
      head.append(el("span", "title", item.label || glance(node ? (node.text || node.text_md) : item.node_id)));
      li.append(head);
      const open = state.dense || state.expanded === item.node_id;
      if (item.reason || item.segue) {
        const more = el("div", "more" + (open ? "" : " hidden"));
        if (item.segue) more.append(el("div", "segue", `„${item.segue}“`));
        if (item.reason) more.append(el("div", "why", item.reason));
        li.append(more);
        li.classList.add("expandable");
      }
      li.classList.toggle("open", open);
      li.addEventListener("click", () => { expand(item.node_id); select(item.node_id); });
      next.append(li);
    }

    const ql = $("#questions-list");
    ql.innerHTML = "";
    for (const q of sug.questions || []) {
      const li = el("li");
      li.append(el("div", "qtext", q.text));
      if (q.why && state.dense) li.append(el("span", "why", q.why));
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
    const pill = $("#llm-status");
    setPill(pill, text, cls);
    pill.classList.add("clickable");
    pill.title = llm.interval_s
      ? `A tick every ${llm.interval_s}s once ${llm.min_new_words} new words were said, `
        + `and immediately past ${llm.burst_words}. Click to change.`
      : "Click to change how often the model is asked.";

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

  // ---------------------------------------------------------------- language

  const LANGS = [
    ["auto", "Auto-detect"], ["sk", "Slovenčina"], ["cs", "Čeština"], ["en", "English"],
    ["de", "Deutsch"], ["es", "Español"], ["fr", "Français"], ["pl", "Polski"],
    ["hu", "Magyar"], ["uk", "Українська"],
  ];

  function renderLanguagePill() {
    const stt = state.status.stt || {};
    const cur = stt.language || (state.session && state.session.language) || "auto";
    const forced = cur !== "auto" && stt.can_force_language;
    const pill = $("#lang-status");
    if (!pill) return;
    pill.textContent = `${cur === "auto" ? "🌐" : forced ? "🔒" : "🌐"} ${cur}`;
    pill.classList.toggle("warn", cur !== "auto" && stt.can_force_language === false);
    let title = `Transcription language: ${cur}. Click to change.`;
    if (cur !== "auto" && stt.can_force_language === false) {
      title = `${stt.engine || "this engine"} detects the language itself — ${cur} is a hint, not a lock. Click to change.`;
    }
    if (stt.dropped_language) title += ` ${stt.dropped_language} wrong-alphabet line(s) dropped.`;
    pill.title = title;
  }

  function showLanguage() {
    const stt = state.status.stt || {};
    const cur = stt.language || (state.session && state.session.language) || "auto";
    const note = $("#lang-note");
    note.textContent = stt.can_force_language === false
      ? `${stt.engine || "The engine"} detects the language per utterance and cannot be forced. `
        + "Setting one here fixes the language of the notes and drops lines in the wrong alphabet. "
        + "For a hard lock, restart with --set stt.engine=faster-whisper."
      : `${stt.engine || "The engine"} will be told to transcribe in this language.`;
    const box = $("#lang-choices");
    box.innerHTML = "";
    for (const [code, label] of LANGS) {
      const b = el("button", "chip" + (code === cur ? " active" : ""), `${code} · ${label}`);
      b.addEventListener("click", () => {
        send({ type: "set_language", language: code });
        $("#lang").close();
      });
      box.append(b);
    }
    $("#lang").showModal();
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
    renderLanguagePill();
    const status = (state.session && state.session.status) || "idle";
    const dot = $("#rec-dot");
    dot.className = "dot" + (status === "running" ? " rec" : status === "paused" ? " paused" : status === "finished" ? " done" : "");
    // Finished is not the end: a second take reuses the same clock and outline,
    // and re-runs the wrap-up over everything.
    const start = $("#btn-start");
    start.classList.toggle("hidden", status !== "idle" && status !== "finished");
    start.textContent = status === "finished" ? "Record again" : "Start";
    $("#btn-pause").classList.toggle("hidden", status !== "running");
    $("#btn-resume").classList.toggle("hidden", status !== "paused");
    $("#btn-finish").classList.toggle("hidden", status === "finished" || status === "finishing");
    $("#busy").classList.toggle("hidden", status !== "finishing");
  }

  // ---------------------------------------------------------------- toasts

  function toast(level, text) {
    const node = el("div", `toast ${level || "info"}`, text);
    $("#toasts").append(node);
    setTimeout(() => node.remove(), 6000);
  }

  // ------------------------------------------------------------------ result

  const RESULT_ORDER = ["show_notes", "outline_annotated", "transcript_md", "transcript_srt", "chapters"];
  const RESULT_LABEL = {
    show_notes: "Show notes",
    outline_annotated: "Outline",
    transcript_md: "Transcript",
    transcript_srt: "SRT",
    chapters: "Chapters",
    final_analysis: "JSON",
  };

  function resultNames(paths) {
    const names = Object.keys(paths || {});
    return names.sort((a, b) => {
      const ia = RESULT_ORDER.indexOf(a), ib = RESULT_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
  }

  // Notes come back in the language of the podcast, so sections are found by heading
  // level and never by their words. Everything inside a fence is text: a transcript
  // quoting "## " must not open a section.
  // Self-contained on purpose — tests/test_ui_sections.py runs this function alone.
  function splitSections(markdown) {
    const ATX = /^ {0,3}(#{2,3})\s+(.*?)\s*$/;
    const FENCE = /^ {0,3}(`{3,}|~{3,})/;
    const tidy = (s) => s.replace(/^(?:[ \t]*\n)+/, "").replace(/\s+$/, "");
    const lines = String(markdown || "").split("\n");

    const cuts = [];
    let fence = null;
    for (let i = 0; i < lines.length; i++) {
      const f = lines[i].match(FENCE);
      if (fence) {
        if (f && f[1][0] === fence[0] && f[1].length >= fence.length) fence = null;
        continue;
      }
      if (f) { fence = f[1]; continue; }
      const m = lines[i].match(ATX);
      if (m) cuts.push({ level: m[1].length, heading: m[2].replace(/\s+#+$/, ""), line: i });
    }

    const blocks = [];
    const lead = tidy(lines.slice(0, cuts.length ? cuts[0].line : lines.length).join("\n"));
    if (lead) blocks.push({ level: 0, heading: "", body: lead, text: "", children: [] });

    let open = null;   // the ## a ### belongs to
    for (let i = 0; i < cuts.length; i++) {
      const cut = cuts[i];
      let end = lines.length;
      for (let j = i + 1; j < cuts.length; j++) {
        if (cuts[j].level <= cut.level) { end = cuts[j].line; break; }
      }
      // A parent renders only what it holds itself; its children render themselves.
      const next = cuts[i + 1];
      const bodyEnd = next && next.line < end && next.level > cut.level ? next.line : end;
      const node = {
        level: cut.level,
        heading: cut.heading,
        body: tidy(lines.slice(cut.line + 1, bodyEnd).join("\n")),
        // What the clipboard gets: the section without its heading, because nobody
        // pastes "## Social post" into a social post.
        text: tidy(lines.slice(cut.line + 1, end).join("\n")),
        children: [],
      };
      if (cut.level === 2) { blocks.push(node); open = node; }
      else if (open) open.children.push(node);
      else blocks.push(node);
    }
    return blocks;
  }

  // The move at the end of an episode is to take one block somewhere else — the
  // social post into a scheduler, the mentions into the episode page.
  const sectionBlocks = new WeakMap();

  function sectionNode(block) {
    if (!block.level) {
      const lead = el("div", "md-lead");
      lead.innerHTML = window.marked.parse(block.body);
      return lead;
    }
    const sec = el("section", block.text ? "md-sec copyable" : "md-sec");
    const head = el("div", "md-sec-head");
    const h = el(`h${block.level}`);
    h.innerHTML = inlineMd(block.heading);
    head.append(h);
    if (block.text) {
      const chip = el("button", "md-copy", "⧉");
      chip.type = "button";
      chip.title = "Copy this section";
      chip.setAttribute("aria-label", `Copy “${block.heading}”`);
      head.append(chip);
      sectionBlocks.set(sec, block);
    }
    sec.append(head);
    if (block.body) {
      const bodyEl = el("div", "md-sec-body");
      bodyEl.innerHTML = window.marked.parse(block.body);
      sec.append(bodyEl);
    }
    for (const child of block.children) sec.append(sectionNode(child));
    return sec;
  }

  function copySection(sec) {
    const block = sectionBlocks.get(sec);
    if (!block) return;
    navigator.clipboard?.writeText(block.text).then(
      () => {
        toast("success", `Copied “${shorten(block.heading, 40)}”`);
        sec.classList.remove("copied");
        void sec.offsetWidth;   // restart the flash when the same block is copied twice
        sec.classList.add("copied");
        setTimeout(() => sec.classList.remove("copied"), 600);
      },
      () => toast("error", "The browser refused clipboard access"),
    );
  }

  $("#result-body").addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof Element)) return;
    if (target.closest("a")) return;   // a link in the notes or the outline is a link first
    const sec = target.closest(".md-sec.copyable");
    if (!sec) return;
    // A click that ends a drag over the text meant to select it, not to copy the block.
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim()) return;
    copySection(sec);
  });

  async function showResult(name) {
    const body = $("#result-body");
    for (const b of document.querySelectorAll("#result-files .chip[data-name]")) {
      b.classList.toggle("active", b.dataset.name === name);
    }
    body.textContent = "loading…";
    state.resultText = "";
    try {
      const text = await (await fetch(`/api/final/${encodeURIComponent(name)}`)).text();
      state.resultText = text;
      body.innerHTML = "";
      if (name.endsWith("srt") || name === "transcript_srt" || !window.marked) {
        body.append(el("pre", "", text));
      } else {
        for (const block of splitSections(text)) body.append(sectionNode(block));
      }
    } catch (err) {
      body.textContent = `could not read ${name}: ${err}`;
    }
  }

  function renderResult(paths, dir) {
    const files = $("#result-files");
    files.innerHTML = "";
    const names = resultNames(paths);
    if (!names.length) return false;
    for (const name of names) {
      const b = el("button", "chip", RESULT_LABEL[name] || name);
      b.dataset.name = name;
      b.title = paths[name];
      b.addEventListener("click", () => showResult(name));
      files.append(b);
    }
    // Show notes exist to be pasted somewhere else; do not make that a trip to disk.
    const copy = el("button", "chip copy", "⧉ Copy");
    copy.title = "Copy the file shown below";
    copy.addEventListener("click", () => {
      if (!state.resultText) return;
      navigator.clipboard?.writeText(state.resultText).then(
        () => toast("success", "Copied to the clipboard"),
        () => toast("error", "The browser refused clipboard access"),
      );
    });
    files.append(copy);
    if (dir) {
      const p = el("div", "result-dir", dir);
      p.title = "click to copy";
      p.addEventListener("click", () => {
        navigator.clipboard?.writeText(dir).then(() => toast("info", "Path copied"), () => {});
      });
      files.append(p);
    }
    $("#tab-result").classList.remove("hidden");
    showResult(names[0]);
    return true;
  }

  function showDone(msg) {
    const body = $("#done-body");
    body.innerHTML = "";
    if (msg.error) body.append(el("p", "warn", `Wrap-up failed: ${msg.error}`));
    body.append(el("p", "", `Duration ${hms(msg.duration_s)} · ${msg.ticks} ticks · $${(msg.cost_usd || 0).toFixed(3)}`));
    if (renderResult(msg.paths, msg.dir)) {
      const open = el("button", "primary", "Open show notes");
      open.addEventListener("click", () => { $("#done").close(); showTab("result"); });
      body.append(open);
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
    else if (key === "t") { toggleTab("transcript"); e.preventDefault(); }
    else if (key === "d") { setDense(!state.dense); e.preventDefault(); }
    else if (key === " ") {
      const status = (state.session && state.session.status) || "idle";
      const action = status === "running" ? "pause" : status === "paused" ? "resume" : "start";
      send({ type: "control", action });
      e.preventDefault();
    } else if (key === "?") { $("#help").showModal(); e.preventDefault(); }
    else if (key === ",") { showSettings(); e.preventDefault(); }
    else if (["1", "2", "3"].includes(key)) {
      const sug = (state.session && state.session.suggestions) || {};
      const item = (sug.next || []).find((x) => String(x.rank) === key);
      if (item) select(item.node_id);
      e.preventDefault();
    }
  });

  // ------------------------------------------------------------------ chrome

  function toggleTab(name) {
    const active = document.querySelector(".tab.active");
    showTab(active && active.dataset.tab === name ? "now" : name);
  }

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

  // ---------------------------------------------------------------- splitter

  const SIDE_MIN = 260, SIDE_DEFAULT = 380;

  function setSideWidth(px) {
    const max = Math.max(SIDE_MIN, window.innerWidth - 420);
    const width = Math.round(Math.min(max, Math.max(SIDE_MIN, px)));
    document.documentElement.style.setProperty("--side", width + "px");
    try { localStorage.setItem("lc.side", String(width)); } catch { /* ignore */ }
  }

  (function initSplitter() {
    try {
      const saved = Number(localStorage.getItem("lc.side"));
      if (saved) setSideWidth(saved);
    } catch { /* ignore */ }

    const bar = $("#splitter");
    let dragging = false;
    bar.addEventListener("pointerdown", (e) => {
      dragging = true;
      bar.setPointerCapture(e.pointerId);
      bar.classList.add("dragging");
      document.body.classList.add("resizing");
      e.preventDefault();
    });
    bar.addEventListener("pointermove", (e) => {
      if (dragging) setSideWidth(window.innerWidth - e.clientX);
    });
    const end = (e) => {
      if (!dragging) return;
      dragging = false;
      try { bar.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
      bar.classList.remove("dragging");
      document.body.classList.remove("resizing");
      updateEdges();
    };
    bar.addEventListener("pointerup", end);
    bar.addEventListener("pointercancel", end);
    bar.addEventListener("dblclick", () => { setSideWidth(SIDE_DEFAULT); updateEdges(); });
  })();

  // ---------------------------------------------------------------- tick settings

  function showTicks() {
    const llm = state.status.llm || {};
    $("#tick-interval").value = llm.interval_s ?? 25;
    $("#tick-min-words").value = llm.min_new_words ?? 25;
    $("#tick-burst").value = llm.burst_words ?? 120;
    $("#ticks").showModal();
  }

  $("#tick-form").addEventListener("submit", (e) => {
    e.preventDefault();
    send({
      type: "set_ticks",
      interval_s: Number($("#tick-interval").value),
      min_new_words: Number($("#tick-min-words").value),
      burst_words: Number($("#tick-burst").value),
    });
    $("#ticks").close();
  });

  // ---------------------------------------------------------------- settings

  // The Settings dialog is built from one /api/settings round-trip. Nothing is
  // sent until Apply, so a half-edited channel never reaches the engine.
  let settings = null;

  const AUTO_SOURCE = "device:auto";
  const CUSTOM = "__custom__";

  async function showSettings() {
    $("#settings").showModal();
    await loadSettings();
    // The provider's own list is a nicety; the dialog is already usable without it.
    fetch("/api/models").then((r) => r.json()).then(fillModelList).catch(() => { /* offline */ });
  }

  async function loadSettings() {
    try {
      settings = await fetch("/api/settings").then((r) => r.json());
    } catch {
      toast("error", "Could not read the settings");
      return;
    }
    renderChannels(settings.audio.channels);
    renderModelFields();
    renderTargetField();
    renderOutlineNote();
    fillModelList({ models: settings.llm.known_models });
  }

  function sourceOptions() {
    const audio = (settings && settings.audio) || { devices: [], audiotee: [] };
    const out = [[AUTO_SOURCE, "Auto — whichever mic is plugged in"]];
    for (const d of audio.devices || []) {
      const notes = [`${d.channels}ch`, `${d.samplerate} Hz`];
      if (d.is_default) notes.push("system default");
      if (d.headset_mode) notes.push("headset mode");
      out.push([`device:${d.name}`, `${d.name} · ${notes.join(" · ")}`]);
    }
    for (const a of audio.audiotee || []) {
      out.push([`audiotee:${a.name}`, `${a.name} — system audio (AudioTee)`]);
    }
    return out;
  }

  function renderChannels(channels) {
    const box = $("#settings-channels");
    box.innerHTML = "";
    for (const ch of channels) box.append(channelRow(ch));
    renderAudioNote();
  }

  function channelRow(ch) {
    const row = el("div", "channel-row");

    const name = el("input", "name");
    name.value = ch.name || "Host";
    name.maxLength = 40;
    name.setAttribute("aria-label", "Channel name");

    const select = el("select", "source");
    const options = sourceOptions();
    for (const [value, label] of options) {
      const opt = el("option", "", label);
      opt.value = value;
      select.append(opt);
    }
    // A source the machine cannot offer right now — an unplugged headset, a
    // `file:` replay — still has to be selectable, or opening the dialog would
    // quietly rewrite it.
    const known = options.some(([value]) => value === ch.source);
    if (ch.source && !known) {
      const opt = el("option", "", `${ch.source} — not connected`);
      opt.value = ch.source;
      select.insertBefore(opt, select.firstChild);
    }
    const customOpt = el("option", "", "Custom…");
    customOpt.value = CUSTOM;
    select.append(customOpt);
    select.value = ch.source || AUTO_SOURCE;

    const custom = el("input", "custom hidden");
    custom.placeholder = "device:Name · audiotee:App · file:/path.wav";
    custom.value = ch.source || "";
    select.addEventListener("change", () => {
      custom.classList.toggle("hidden", select.value !== CUSTOM);
      if (select.value === CUSTOM) custom.focus();
      renderAudioNote();
    });

    const direct = el("label", "direct");
    const box = el("input");
    box.type = "checkbox";
    box.checked = !!ch.is_direct;
    box.title = "This channel wins the cross-talk dedupe against the microphones";
    direct.append(box, el("span", "", "direct"));

    const drop = el("button", "drop-channel", "✕");
    drop.type = "button";
    drop.title = "Remove this channel";
    drop.addEventListener("click", () => {
      if ($("#settings-channels").children.length <= 1) {
        toast("warn", "A session needs at least one channel");
        return;
      }
      row.remove();
      renderAudioNote();
    });

    row.append(name, select, custom, direct, drop);
    return row;
  }

  function readChannels() {
    return Array.from($("#settings-channels").children).map((row) => {
      const select = row.querySelector("select.source");
      const custom = row.querySelector("input.custom");
      const source = select.value === CUSTOM ? custom.value.trim() : select.value;
      return {
        name: row.querySelector("input.name").value.trim(),
        source: source || AUTO_SOURCE,
        is_direct: row.querySelector(".direct input").checked,
        record: true,
      };
    });
  }

  function renderAudioNote() {
    const note = $("#settings-audio-note");
    const audio = (settings && settings.audio) || {};
    const chosen = readChannels().map((c) => c.source);
    const lines = [];
    if (audio.device_error) lines.push(`Devices could not be listed: ${audio.device_error}`);
    const headsets = (audio.devices || [])
      .filter((d) => d.headset_mode && chosen.includes(`device:${d.name}`))
      .map((d) => d.name);
    if (headsets.length) {
      lines.push(
        `${headsets.join(", ")} is in Bluetooth headset mode: opening the microphone drops the ` +
        "whole link to 16 kHz mono, so what you hear sounds like a phone call. Fine for a test, " +
        "wrong for an episode.");
    }
    if (!audio.audiotee_available && chosen.some((c) => c.startsWith("audiotee:"))) {
      lines.push("The audiotee helper is not built — run ./helpers/audiotee/build.sh.");
    }
    if (!lines.length && (settings || {}).session_status === "running") {
      lines.push("Applying reopens the microphones; the clock and the transcript carry on.");
    }
    note.textContent = lines.join(" ");
    note.classList.toggle("warn", headsets.length > 0 || !!audio.device_error);
  }

  function renderModelFields() {
    const llm = settings.llm, stt = settings.stt;
    $("#settings-tick-model").value = llm.tick_model;
    $("#settings-final-model").value = llm.final_model;
    $("#settings-stt-model").value = stt.model || "";
    const select = $("#settings-stt-engine");
    select.innerHTML = "";
    for (const engine of stt.engines) {
      const bits = [engine.key];
      if (engine.note) bits.push(engine.note);
      else if (engine.can_force_language) bits.push("can lock the language");
      const opt = el("option", "", bits.join(" — "));
      opt.value = engine.key;
      opt.disabled = !engine.installed;
      select.append(opt);
    }
    select.value = stt.engine;
    $("#settings-model-note").textContent =
      `A bare name goes to ${llm.default_provider}; prefix another one ` +
      `(${llm.providers.join(", ")}) to send it elsewhere.` +
      (settings.session_status === "running" ? " A new speech engine loads at the next Start." : "");
  }

  function fillModelList(payload) {
    const list = $("#settings-models");
    const seen = new Set(Array.from(list.options).map((o) => o.value));
    for (const id of payload.models || []) {
      if (seen.has(id)) continue;
      seen.add(id);
      const opt = document.createElement("option");
      opt.value = id;
      list.append(opt);
    }
  }

  function renderTargetField() {
    // The session owns it, not /api/settings: it changes mid-session like the clock.
    const target = (state.session || {}).target_minutes;
    $("#settings-target").value = target == null ? "" : String(target);
  }

  function renderOutlineNote() {
    const outline = settings.outline || {};
    $("#settings-outline-note").textContent = outline.path
      ? `${outline.items} item${outline.items === 1 ? "" : "s"} from ${outline.path}`
      : "No outline loaded. The map stays empty and the model has nothing to track.";
  }

  function applySettings() {
    const channels = readChannels();
    if (channels.some((c) => !c.name)) { toast("error", "Every channel needs a name"); return; }
    send({ type: "set_audio", channels });
    send({
      type: "set_models",
      tick_model: $("#settings-tick-model").value,
      final_model: $("#settings-final-model").value,
      stt_engine: $("#settings-stt-engine").value,
      stt_model: $("#settings-stt-model").value,
    });
    const raw = $("#settings-target").value.trim();
    const target = raw === "" ? null : Number(raw);
    if (target !== null && !(target > 0)) { toast("error", "A target length is a number of minutes"); return; }
    send({ type: "set_target", target_minutes: target });
    $("#settings").close();
  }

  $("#btn-settings").addEventListener("click", showSettings);
  $("#meters").addEventListener("click", showSettings);
  $("#settings-apply").addEventListener("click", applySettings);
  $("#settings-rescan").addEventListener("click", loadSettings);
  $("#settings-add-channel").addEventListener("click", () => {
    const used = readChannels().map((c) => c.name);
    const name = ["Guest", "Guest 2", "Guest 3"].find((n) => !used.includes(n)) || `Channel ${used.length + 1}`;
    $("#settings-channels").append(channelRow({ name, source: AUTO_SOURCE, is_direct: used.length > 0 }));
    renderAudioNote();
  });

  // ----------------------------------------------------------------- outline

  async function uploadOutline(file) {
    if (!file) return;
    let text;
    try {
      text = await file.text();
    } catch {
      toast("error", "That file could not be read");
      return;
    }
    let response;
    try {
      response = await fetch("/api/outline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, text }),
      });
    } catch {
      toast("error", "The upload did not reach the server");
      return;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      toast("error", data.error || `Upload failed (${response.status})`);
      return;
    }
    if (settings) { await loadSettings(); }
  }

  const pickOutline = () => $("#outline-file").click();
  $("#outline-file").addEventListener("change", (e) => {
    uploadOutline(e.target.files[0]);
    e.target.value = "";   // the same file again must still fire `change`
  });
  $("#btn-pick-outline").addEventListener("click", pickOutline);
  $("#settings-pick-outline").addEventListener("click", pickOutline);

  (function initOutlineDrop() {
    const pane = $("#outline-pane");
    const hint = $("#outline-drop");
    let depth = 0;   // dragenter/dragleave fire per child, so count instead of toggling

    const hasFile = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");
    pane.addEventListener("dragenter", (e) => {
      if (!hasFile(e)) return;
      e.preventDefault();
      depth += 1;
      hint.classList.remove("hidden");
    });
    pane.addEventListener("dragover", (e) => { if (hasFile(e)) e.preventDefault(); });
    pane.addEventListener("dragleave", () => {
      depth = Math.max(0, depth - 1);
      if (!depth) hint.classList.add("hidden");
    });
    pane.addEventListener("drop", (e) => {
      if (!hasFile(e)) return;
      e.preventDefault();
      depth = 0;
      hint.classList.add("hidden");
      uploadOutline(e.dataTransfer.files[0]);
    });
    // A file dropped anywhere else would otherwise navigate away from the UI.
    for (const type of ["dragover", "drop"]) {
      window.addEventListener(type, (e) => { if (hasFile(e) && !pane.contains(e.target)) e.preventDefault(); });
    }
  })();

  $("#btn-help").addEventListener("click", () => $("#help").showModal());
  $("#llm-status").addEventListener("click", showTicks);
  $("#btn-density").addEventListener("click", () => setDense(!state.dense));
  setDense(state.dense);
  $("#cost-status").addEventListener("click", showUsage);
  $("#lang-status").addEventListener("click", showLanguage);
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
    // Reloading a finished session (or resuming one) must still show its exports.
    if (data.session && Object.keys(data.session.final_paths || {}).length) {
      fetch("/api/final").then((r) => r.json())
        .then((f) => renderResult(f.paths, f.dir))
        .catch(() => renderResult(data.session.final_paths, null));
    }
  }).catch(() => { /* the websocket will deliver it */ });

  connect();
})();

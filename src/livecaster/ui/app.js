/* Livecaster UI. Plain ES2020, no build step.

   The outline is drawn as a score: each top-level section is a system with a
   lettered rehearsal mark in the margin, and that letter is the jump key.
   Covered lines take the engraver's cut, the reachable one is the passage to
   play now, and a line the host bolded in the outline carries a marcato.

   Nothing on this screen is meant to be read while talking. Transcript,
   questions, mentions and new topics are keys that overlay and leave. */
(() => {
  "use strict";

  const state = {
    session: null,
    outline: [],
    nodesById: new Map(),      // node id -> outline node
    states: {},                // node id -> NodeState
    elements: new Map(),       // node id -> {root, body, t, mk, prep, rank}
    systems: [],               // {letter, headingId, el, nodeIds}
    letters: new Map(),        // letter -> system
    order: [],                 // coverable node ids in document order (j/k)
    selected: null,
    segments: [],
    buildId: window.BUILD_ID,
    status: {},
    connected: false,
    resultText: "",
    overlay: null,             // "transcript" | "questions" | "mentions" | "new"
    llmFails: 0,               // consecutive failed ticks
    lastTickAt: null,          // clock of the last tick that landed
    wrapOpen: false,
    openRest: new Set(),       // rested systems the host has opened back up
    // Live, the host reads with one eye. Everything the model says beyond the
    // one passage is hidden until asked for; `d` flips the whole score to full.
    dense: localStorage.getItem("lc.dense") === "1",
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

  // A, B … Z, then AA, AB. An outline past 26 sections has other problems.
  function letterFor(i) {
    let out = "";
    i += 1;
    while (i > 0) {
      const r = (i - 1) % 26;
      out = String.fromCharCode(65 + r) + out;
      i = Math.floor((i - 1) / 26);
    }
    return out;
  }

  const isOpen = (st) => !st || (st.status !== "covered" && st.status !== "skipped");

  // Drawn, not typed. A marcato set as U+2227 in a text face is a caret pointing
  // at nothing to anyone who does not read music, and invisible at two metres.
  const MARCATO =
    '<svg viewBox="0 0 16 12" width="16" height="12" aria-hidden="true" focusable="false">' +
    '<path d="M1.5 10.5 8 2l6.5 8.5" fill="none" stroke="currentColor" ' +
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const PINNED =
    '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">' +
    '<path d="M3 8.6 6.4 12 13 3.8" fill="none" stroke="currentColor" ' +
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  // ---------------------------------------------------------------- websocket

  let ws = null;
  let backoff = 500;

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws${location.search}`);
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
      case "state": applyState(msg.session); break;
      case "patch": applyPatch(msg); break;
      case "segment": addSegment(msg.segment); break;
      case "status": applyStatus(msg); break;
      case "toast": toast(msg.level, msg.text); break;
      case "done": showDone(msg); break;
    }
  }

  // ------------------------------------------------------------------- state

  function applyState(session) {
    state.session = session;
    state.outline = session.outline || [];
    state.states = session.nodes || {};
    state.nodesById = new Map(state.outline.map((n) => [n.id, n]));
    buildScore();
    renderBudget();
    renderOverlay();
    renderStatusBar();
  }

  function applyPatch(patch) {
    const shape = shapeKey();
    if (patch.nodes) {
      for (const [id, st] of Object.entries(patch.nodes)) state.states[id] = st;
    }
    if (patch.suggestions) state.session.suggestions = patch.suggestions;
    if (patch.mentions) state.session.mentions = patch.mentions;
    if (patch.usage) state.session.usage = patch.usage;
    if (patch.language) state.session.language = patch.language;
    if (patch.target_minutes !== undefined) state.session.target_minutes = patch.target_minutes;
    if (patch.session_status) state.session.status = patch.session_status;

    // A system that just came to rest, or a passage that just became the one to
    // play, changes the shape of the score. Anything else only recolours lines,
    // and replacing the DOM under someone mid-glance is the one thing this
    // structure must never do.
    if (patch.nodes || patch.suggestions) {
      if (shapeKey() !== shape) buildScore();
      else {
        for (const id of Object.keys(patch.nodes || {})) updateStave(id);
        if (patch.suggestions) for (const id of state.elements.keys()) updateStave(id);
        updateFractions();
      }
    }
    if (patch.session_status) renderStatusBar();
    renderBudget();
    renderOverlay();
    updateEdges();
  }

  // =================================================================== score

  function systemLevel() {
    const counts = new Map();
    for (const n of state.outline) {
      if (n.kind === "heading") counts.set(n.level, (counts.get(n.level) || 0) + 1);
    }
    if (!counts.size) return 1;
    const levels = [...counts.keys()].sort((a, b) => a - b);
    // "# My episode" followed by "## 1 …" "## 2 …" means the sections are the
    // systems and the h1 is the title of the whole score.
    return levels.find((l) => counts.get(l) > 1) ?? levels[0];
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

  // The one passage to play now: rank 1 among the hot items still open.
  function livePassage() {
    const sug = (state.session && state.session.suggestions) || {};
    const ranked = (sug.next || [])
      .filter((x) => isOpen(state.states[x.node_id]) && state.nodesById.has(x.node_id))
      .sort((a, b) => (a.rank || 9) - (b.rank || 9));
    return ranked[0] || null;
  }

  // Group the flat outline into systems: one per heading at the shallowest
  // heading level, plus an unnamed opening system for anything before the first.
  function groupSystems() {
    const top = systemLevel();
    const groups = [];
    let current = null;
    for (const node of state.outline) {
      if (node.kind === "meta") continue;
      if (node.kind === "heading" && node.level === top) {
        current = { heading: node, rows: [] };
        groups.push(current);
        continue;
      }
      if (!current) {
        current = { heading: null, rows: [] };
        groups.push(current);
      }
      current.rows.push(node);
    }
    return groups;
  }

  // Two facts decide the score's shape. Everything else is a line's colour.
  function shapeKey() {
    const live = livePassage();
    const groups = groupSystems();
    const resting = restingKeys(groups, live);
    const rest = groups.map((g) => (resting.has(groupKey(g)) ? "1" : "0")).join("");
    return `${live ? live.node_id : ""}|${rest}`;
  }

  function leavesOf(group) {
    return group.heading
      ? coverableUnder(group.heading.id)
      : group.rows.filter((n) => n.coverable).map((n) => n.id);
  }

  const groupKey = (g) => (g.heading ? g.heading.id : "");

  // Which systems are folded to their heading line. Finished ones rest because
  // they are behind you. Unstarted ones more than one system from the passage
  // you are playing rest too: without that, an unread section spends its full
  // height on lines nobody has reached and pushes the one thing that matters
  // off the bottom of the screen. Autoscroll is an anti-goal, so compression
  // is what has to keep the passage in reach.
  function restingKeys(groups, live) {
    let anchor = -1;
    groups.forEach((g, i) => {
      const leaves = leavesOf(g);
      if (live && leaves.includes(live.node_id)) anchor = i;
    });
    if (anchor < 0 && state.selected) {
      groups.forEach((g, i) => { if (leavesOf(g).includes(state.selected)) anchor = i; });
    }
    const out = new Set();
    groups.forEach((g, i) => {
      const key = groupKey(g);
      if (state.openRest.has(key)) return;
      const leaves = leavesOf(g);
      if (!leaves.length) return;
      const done = leaves.filter((id) => !isOpen(state.states[id])).length;
      if (done === leaves.length) { out.add(key); return; }
      if (done === 0 && anchor >= 0 && Math.abs(i - anchor) > 1) out.add(key);
    });
    return out;
  }

  function updateFractions() {
    for (const sys of state.systems) {
      if (!sys.frac) continue;
      const done = sys.leaves.filter((id) => !isOpen(state.states[id])).length;
      sys.frac.textContent = restful(sys.leaves, done);
    }
  }

  function buildScore() {
    const root = $("#systems");
    const keepScroll = $("#score").scrollTop;
    root.innerHTML = "";
    $("#score-empty").classList.toggle("hidden", state.outline.length > 0);
    state.elements.clear();
    state.systems = [];
    state.letters.clear();
    state.order = [];

    const live = livePassage();
    let groups = groupSystems();
    const resting = restingKeys(groups, live);

    if (groups.length && !groups[0].heading && !groups[0].rows.some((n) => n.coverable)) {
      for (const node of groups[0].rows) {
        const title = el("div", "score-title");
        title.innerHTML = inlineMd(node.text_md || node.text || "");
        root.append(title);
      }
      groups = groups.slice(1);
    }

    groups.forEach((group, index) => {
      const letter = letterFor(index);
      const sys = el("section", "system");
      const margin = el("div", "margin");
      const mark = el("button", "rmark", letter);
      mark.type = "button";
      mark.title = `Jump here — press ${letter}`;
      mark.setAttribute("aria-label", `Jump to ${group.heading ? group.heading.text : "the opening"}`);
      margin.append(mark);

      const col = el("div");
      const leaves = leavesOf(group);
      const done = leaves.filter((id) => !isOpen(state.states[id])).length;
      const key = group.heading ? group.heading.id : "";

      let frac = null;
      if (group.heading) {
        const name = el("div", "sysname");
        const h = el("h2");
        h.innerHTML = inlineMd(group.heading.text_md || group.heading.text || "");
        name.append(h);
        if (leaves.length) {
          frac = el("span", "frac num", restful(leaves, done));
          if (!done) frac.textContent = `${leaves.length} ahead`;
          name.append(frac);
        }
        col.append(name);
        // A rested system still has to be reachable: a mark made inside it can
        // only be undone if the lines can be brought back.
        name.style.cursor = "pointer";
        name.addEventListener("click", () => {
          if (state.openRest.has(key)) state.openRest.delete(key);
          else state.openRest.add(key);
          buildScore();
        });
      }

      // A system whose every line is behind you rests to one line.
      const isLive = !!(live && leaves.includes(live.node_id));
      const rests = resting.has(key) && !isLive;
      if (rests) sys.classList.add("is-rest");
      if (isLive) sys.classList.add("is-live");
      if (rests && done === 0) sys.classList.add("is-ahead");

      if (!rests) {
        for (const node of group.rows) {
          if (node.kind === "heading" && node.level < systemLevel()) {
            const title = el("div", "score-title");
            title.innerHTML = inlineMd(node.text_md || node.text || "");
            col.append(title);
            continue;
          }
          if (node.kind === "heading") {
            const sub = el("div", "substave");
            sub.innerHTML = inlineMd(node.text_md || node.text || "");
            col.append(sub);
            continue;
          }
          if (live && node.id === live.node_id) col.append(playNode(node, live));
          else col.append(staveNode(node));
        }
      }

      sys.append(margin, col);
      root.append(sys);

      const entry = { letter, heading: group.heading, el: sys, mark, frac, leaves };
      state.systems.push(entry);
      state.letters.set(letter, entry);
      mark.addEventListener("click", () => jumpTo(letter));
    });

    for (const id of state.elements.keys()) updateStave(id);
    if (!state.selected || !state.elements.has(state.selected)) {
      if (state.order.length) select(state.order[0], false);
    } else {
      const parts = state.elements.get(state.selected);
      if (parts) parts.root.classList.add("selected");
    }
    $("#score").scrollTop = keepScroll;
    updateEdges();
  }

  // "3/7" while there is something left, the times once there is not.
  function restful(leaves, done) {
    if (done < leaves.length) return `${done}/${leaves.length}`;
    const times = leaves
      .map((id) => (state.states[id] || {}).covered_at)
      .filter((t) => t != null)
      .sort((a, b) => a - b);
    if (!times.length) return `all ${leaves.length}`;
    const span = times.length > 1 && times[0] !== times[times.length - 1]
      ? `${hms(times[0]).slice(0, 5)}–${hms(times[times.length - 1]).slice(0, 5)}`
      : hms(times[0]).slice(0, 5);
    return `all ${leaves.length} · ${span}`;
  }

  function staveNode(node) {
    const row = el("div", "stave");
    row.dataset.id = node.id;
    const t = el("span", "t num");
    const mk = el("span", "mk");
    const body = el("span", "body");
    body.innerHTML = inlineMd(node.text_md || node.text || "");
    const rank = el("span", "rank hidden");
    const pin = el("span", "pin");
    row.append(t, mk, body, pin, rank);

    const prep = el("div", "prep hidden");
    const holder = el("div");
    holder.append(row, prep);

    state.elements.set(node.id, { root: row, holder, t, mk, body, prep, rank, pin });
    if (node.coverable) state.order.push(node.id);

    row.addEventListener("click", (e) => {
      select(node.id);
      if (e.altKey) togglePin(node.id);
    });
    row.addEventListener("dblclick", () => toggleCovered(node.id));
    return holder;
  }

  // The passage to play now, in conductor's pencil, with its cue note.
  function playNode(node, item) {
    const wrap = el("div", "play");
    wrap.dataset.id = node.id;
    if (item.rank) wrap.append(el("span", "rank num", String(item.rank)));

    const line = el("p", "line");
    line.innerHTML = inlineMd(node.text_md || node.text || "");
    wrap.append(line);

    if (item.reason || item.segue) {
      const cue = el("div", "cue");
      if (item.reason) cue.append(el("p", "why", item.reason));
      if (item.segue) cue.append(el("p", "segue", `„${item.segue}“`));
      wrap.append(cue);
    }

    const prep = el("div", "prep hidden");
    wrap.append(prep);

    state.elements.set(node.id, { root: wrap, holder: wrap, t: null, mk: null, body: line, prep, rank: null, pin: null });
    if (node.coverable) state.order.push(node.id);
    line.addEventListener("click", () => select(node.id));
    wrap.addEventListener("dblclick", () => toggleCovered(node.id));
    return wrap;
  }

  function updateStave(id) {
    const parts = state.elements.get(id);
    if (!parts) return;
    const node = state.nodesById.get(id) || {};
    const st = state.states[id] || {};
    const cls = parts.root.classList;
    cls.remove("warm", "touched", "cut", "skipped", "hot", "pinned", "accent", "current");

    if (st.warm > 0 && isOpen(st)) cls.add("warm");
    if (st.status === "touched") cls.add("warm");
    if (st.status === "covered") cls.add("cut");
    if (st.status === "skipped") cls.add("skipped");
    if (st.pinned) cls.add("pinned");
    if (node.must) cls.add("accent");
    if (st.hot && isOpen(st)) cls.add("hot");

    if (parts.mk) {
      const marked = node.must && isOpen(st);
      parts.mk.innerHTML = marked ? MARCATO : "";
      parts.mk.title = marked ? "You marked this one" : "";
    }
    if (parts.pin) parts.pin.innerHTML = st.pinned ? PINNED : "";
    if (parts.t) {
      parts.t.textContent = st.status === "covered" && st.covered_at != null
        ? hms(st.covered_at).slice(0, 5)
        : st.status === "skipped" ? "tacet" : "";
    }
    if (parts.rank) {
      const r = st.hot && st.hot.rank && st.hot.rank <= 3 ? String(st.hot.rank) : "";
      parts.rank.textContent = r;
      parts.rank.classList.toggle("hidden", !r);
    }

    const cur = state.session && state.session.suggestions && state.session.suggestions.current;
    if (cur && cur.node_id === id) cls.add("current");

    renderPrep(id, parts);
  }

  function renderPrep(id, parts) {
    const show = state.dense || state.selected === id;
    const pf = state.session && state.session.preflight;
    const node = pf && pf.nodes && pf.nodes[id];
    if (!show || !node || (!node.questions.length && !node.related.length)) {
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

  function select(id, scroll = true) {
    if (state.selected) {
      const prev = state.elements.get(state.selected);
      if (prev) { prev.root.classList.remove("selected"); renderPrep(state.selected, prev); }
    }
    state.selected = id;
    const parts = state.elements.get(id);
    if (parts) {
      parts.root.classList.add("selected");
      renderPrep(id, parts);
      if (scroll) parts.root.scrollIntoView({ block: "nearest" });
    }
    send({ type: "select", node_id: id });
  }

  // Type the capital you see in the margin.
  function jumpTo(letter) {
    const sys = state.letters.get(letter);
    if (!sys) return;
    sys.el.scrollIntoView({ block: "start", behavior: "smooth" });
    const first = state.order.find((id) => sys.el.contains((state.elements.get(id) || {}).root));
    if (first) select(first, false);
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

  function setDense(on) {
    state.dense = on;
    localStorage.setItem("lc.dense", on ? "1" : "0");
    for (const id of state.elements.keys()) updateStave(id);
  }

  // ================================================================= budget

  // Time against plan. One line. It informs; it never regroups the score.
  function renderBudget() {
    const s = state.session;
    const box = $("#budget");
    const of = $("#of");
    if (!s) { box.textContent = ""; return; }

    const open = state.order.filter((id) => isOpen(state.states[id]));
    const marked = open.filter((id) => (state.nodesById.get(id) || {}).must);

    const target = s.target_minutes;
    box.innerHTML = "";
    if (target) {
      of.textContent = `of ${hms(target * 60).replace(/^00:/, "")}`;
      of.classList.remove("hidden");
      const left = Math.round(target * 60 - (state.status.clock || 0));
      const span = el("span");
      if (left >= 0) {
        span.append(el("b", "", String(Math.max(0, Math.round(left / 60)))), document.createTextNode(" min left"));
      } else {
        span.append(el("b", "over", String(Math.round(-left / 60))), document.createTextNode(" min over"));
      }
      box.append(span);
    } else {
      of.classList.add("hidden");
    }

    if (state.order.length) {
      const span = el("span");
      span.append(el("b", "", String(open.length)), document.createTextNode(" unasked"));
      box.append(span);
    }
    if (marked.length) {
      const span = el("span", "marked");
      span.textContent = `${marked.length} you marked`;
      span.title = marked.map((id) => shorten((state.nodesById.get(id) || {}).text || "", 60)).join(" · ");
      box.append(span);
    }
  }

  // ---------------------------------------------------------- edge markers

  function systemOf(node) {
    return state.systems.find((sys) => sys.el.contains(node)) || null;
  }

  // The passage you are playing is the only thing worth an edge marker, and the
  // marker says which letter reaches it — so it is the answer and the key at once.
  function updateEdges() {
    const pane = $("#score");
    const rect = pane.getBoundingClientRect();
    const up = $("#edge-up"), down = $("#edge-down");
    const live = livePassage();
    const parts = live && state.elements.get(live.node_id);

    let target = null, dir = null;
    if (parts) {
      const r = parts.root.getBoundingClientRect();
      if (r.bottom < rect.top + 4) dir = "up";
      else if (r.top > rect.bottom - 4) dir = "down";
      target = parts.root;
    }

    for (const [edge, want] of [[up, "up"], [down, "down"]]) {
      const on = dir === want;
      edge.classList.toggle("hidden", !on);
      if (!on) continue;
      const sys = systemOf(target);
      const letter = sys ? sys.letter : "";
      edge.querySelector("span").textContent = letter
        ? `${letter} — the one to go to, ${want === "up" ? "above" : "below"}`
        : `the one to go to, ${want === "up" ? "above" : "below"}`;
      edge.onclick = () => target.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  // ================================================================ overlay

  const OVERLAY_TITLE = {
    transcript: "Transcript",
    questions: "Questions worth asking",
    mentions: "Mentions and links",
    new: "Not in the outline",
  };

  function toggleOverlay(name) {
    state.overlay = state.overlay === name ? null : name;
    renderOverlay();
  }

  function closeOverlay() {
    state.overlay = null;
    renderOverlay();
  }

  function renderOverlay() {
    const panel = $("#overlay");
    panel.classList.toggle("hidden", !state.overlay);
    if (!state.overlay) return;
    $("#overlay-title").textContent = OVERLAY_TITLE[state.overlay] || "";
    const body = $("#overlay-body");
    body.innerHTML = "";
    const s = state.session || {};
    const sug = s.suggestions || {};

    if (state.overlay === "transcript") {
      for (const seg of state.segments) body.append(segmentNode(seg));
      body.scrollTop = body.scrollHeight;
      return;
    }

    const list = el("ul");
    if (state.overlay === "questions") {
      for (const q of sug.questions || []) {
        const li = el("li");
        li.append(el("span", "", q.text));
        if (q.why) li.append(el("span", "why", q.why));
        if (q.node_id) {
          li.classList.add("clickable");
          li.addEventListener("click", () => { select(q.node_id); closeOverlay(); });
        }
        list.append(li);
      }
      if (!list.children.length) list.append(el("li", "empty", "Nothing yet. The model proposes these as it listens."));
    } else if (state.overlay === "mentions") {
      for (const m of s.mentions || []) {
        const li = el("li");
        li.append(el("strong", "", m.text));
        if (m.kind) li.append(el("span", "kind", ` ${m.kind}`));
        if (m.url) {
          const a = el("a", "", m.url);
          a.href = m.url; a.target = "_blank"; a.rel = "noreferrer";
          li.append(document.createElement("br"), a);
        } else if (m.needs_link) {
          li.append(document.createElement("br"), el("span", "todo", "needs a link"));
        }
        list.append(li);
      }
      if (!list.children.length) list.append(el("li", "empty", "Nothing named yet."));
    } else if (state.overlay === "new") {
      for (const t of sug.new_topics || []) {
        const li = el("li");
        li.append(el("strong", "", t.title));
        if (t.summary) li.append(el("span", "why", t.summary));
        list.append(li);
      }
      if (!list.children.length) list.append(el("li", "empty", "Everything so far was on the plan."));
    }
    body.append(list);
  }

  const speakerColors = new Map();

  function segmentNode(seg) {
    const div = el("div", "seg");
    div.append(el("span", "t num", hms(seg.t0).slice(0, 8)));
    if (seg.speaker) {
      if (!speakerColors.has(seg.speaker)) speakerColors.set(seg.speaker, speakerColors.size % 3);
      div.append(el("span", `sp sp-${speakerColors.get(seg.speaker)}`, seg.speaker));
    }
    div.append(document.createTextNode(seg.text));
    return div;
  }

  function addSegment(seg) {
    state.segments.push(seg);
    while (state.segments.length > 900) state.segments.shift();
    if (state.overlay !== "transcript") return;
    const body = $("#overlay-body");
    const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
    body.append(segmentNode(seg));
    if (atBottom) body.scrollTop = body.scrollHeight;
  }

  // ============================================================= status bar

  function setPill(node, text, cls) {
    node.textContent = text;
    node.className = `pill${cls ? " " + cls : ""}`;
    if (node.id === "llm-status" || node.id === "cost-status" || node.id === "lang-status") {
      node.classList.add("clickable");
    }
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
    } else if (llm.state === "error" || llm.state === "backoff") {
      text = llm.error ? humanError(llm.error) : `LLM ${llm.state}`;
      cls = "err";
    } else if (llm.state === "idle") {
      text = "LLM idle";
    }
    const pill = $("#llm-status");
    setPill(pill, text, cls);
    // Coverage is the model's work. Two failed ticks in a row and the map is no
    // longer tracking the conversation, so it must stop looking like it is:
    // an untouched section and a section nobody has discussed render alike.
    if (llm.state === "error" || llm.state === "backoff") state.llmFails += 1;
    else if (llm.state === "ok" || llm.state === "ticking") {
      state.llmFails = 0;
      if (llm.last_tick_t != null) state.lastTickAt = llm.last_tick_t;
    }
    renderTracking();
    pill.title = (llm.error ? llm.error + " · " : "") + (llm.interval_s
      ? `A tick every ${llm.interval_s}s once ${llm.min_new_words} new words were said, `
        + `and immediately past ${llm.burst_words}. Click to change.`
      : "Click to change how often the model is asked.");

    const u = (state.session && state.session.usage) || {};
    const cached = u.prompt_tokens ? Math.round((u.cached_tokens / u.prompt_tokens) * 100) : 0;
    setPill($("#cost-status"),
      `$${(u.cost_usd || 0).toFixed(3)} · ${u.ticks || 0} ticks · ${cached}% cached`, "");

    if (state.session) state.session.status = st.session_status;
    if ((st.sync_marks || []).length) {
      const badge = $("#sync-badge");
      badge.textContent = `sync ${hms(st.sync_marks[st.sync_marks.length - 1])}`;
      badge.classList.remove("hidden");
    }
    renderBudget();
    renderStatusBar();
  }

  const LANGS = [
    ["auto", "Auto-detect"], ["sk", "Slovenčina"], ["cs", "Čeština"], ["en", "English"],
    ["de", "Deutsch"], ["es", "Español"], ["fr", "Français"], ["pl", "Polski"],
    ["hu", "Magyar"], ["uk", "Українська"],
  ];

  function renderLanguagePill() {
    const stt = state.status.stt || {};
    const cur = stt.language || (state.session && state.session.language) || "auto";
    const pill = $("#lang-status");
    if (!pill) return;
    pill.textContent = cur;
    pill.className = "pill clickable" + (cur !== "auto" && stt.can_force_language === false ? " warn" : "");
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
    $("#lang-note").textContent = stt.can_force_language === false
      ? `${stt.engine || "The engine"} detects the language per utterance and cannot be forced. `
        + "Setting one here fixes the language of the notes and drops lines in the wrong alphabet. "
        + "For a hard lock, restart with --set stt.engine=faster-whisper."
      : `${stt.engine || "The engine"} will be told to transcribe in this language.`;
    const box = $("#lang-choices");
    box.innerHTML = "";
    for (const [code, label] of LANGS) {
      const b = el("button", "chip" + (code === cur ? " active" : ""), `${code} · ${label}`);
      b.addEventListener("click", () => { send({ type: "set_language", language: code }); $("#lang").close(); });
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
        meter = el("span", "meter");
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

  function renderTracking() {
    const stalled = state.llmFails >= 2;
    document.body.classList.toggle("not-tracking", stalled);
    const note = $("#tracking");
    note.classList.toggle("hidden", !stalled);
    if (stalled) {
      note.textContent = state.lastTickAt != null
        ? `coverage stopped at ${hms(state.lastTickAt)} · transcription still running`
        : "coverage never started · transcription still running";
    }
  }

  function renderStatusBar() {
    renderLanguagePill();
    const status = (state.session && state.session.status) || "idle";
    $("#clock").classList.toggle("is-rec", status === "running");
    // Finished is not the end: a second take reuses the same clock and outline.
    const start = $("#btn-start");
    start.classList.toggle("hidden", status !== "idle" && status !== "finished");
    start.textContent = status === "finished" ? "Record again" : "Start";
    $("#btn-pause").classList.toggle("hidden", status !== "running");
    $("#btn-resume").classList.toggle("hidden", status !== "paused");
    $("#btn-finish").classList.toggle("hidden", status === "finished" || status === "finishing");
    $("#busy").classList.toggle("hidden", status !== "finishing");
  }

  // ================================================================= toasts

  // Errors reach the host mid-interview. Name the problem and the recovery; the
  // provider's raw JSON belongs in the log and the settings dialog.
  function humanError(raw) {
    const text = String(raw || "");
    if (/\b401\b|authentication failed|invalid api key/i.test(text)) {
      return "The provider rejected the API key — open settings";
    }
    if (/\b429\b|rate limit/i.test(text)) return "The provider is rate-limiting — ticks will retry";
    if (/\b(5\d\d)\b|timeout|timed out/i.test(text)) return "The provider is not answering — ticks will retry";
    if (/model .*not found|unknown model/i.test(text)) return "That model does not exist at this provider";
    const clean = text.replace(/^\w*Error:\s*/, "").replace(/\s*\{[\s\S]*$/, "").trim();
    return clean || "The model call failed";
  }

  function toast(level, text) {
    const shown = level === "error" ? humanError(text) : text;
    const node = el("div", `toast ${level || "info"}`, shown);
    node.title = String(text || "");
    $("#toasts").append(node);
    setTimeout(() => node.remove(), 6000);
  }

  // =================================================================== wrap

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
    return Object.keys(paths || {}).sort((a, b) => {
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

  const COPY_MARK =
    '<svg viewBox="0 0 18 18" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<rect x="6.2" y="6.2" width="9.3" height="9.3" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
    '<path d="M11.8 3.6H4.4a1.8 1.8 0 0 0-1.8 1.8v7.4" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round"/></svg>';

  // Each note is a system of its own, with its own mark in the margin — the
  // same grammar as the score, read at rest instead of at a glance.
  function sectionNode(block, mark) {
    if (!block.level) {
      const lead = el("div", "md-lead");
      lead.innerHTML = window.marked.parse(block.body);
      return lead;
    }
    const sec = el("section", block.text ? "md-sec copyable" : "md-sec");
    if (block.level === 2 && mark) {
      const margin = el("div", "md-margin");
      margin.append(el("span", "rmark", mark));
      sec.append(margin);
      sec.classList.add("md-system");
    }
    const head = el("div", "md-sec-head");
    const h = el(`h${block.level}`);
    h.innerHTML = inlineMd(block.heading);
    head.append(h);
    if (block.text) {
      const chip = el("button", "md-copy");
      chip.type = "button";
      chip.innerHTML = COPY_MARK;
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
        let n = 0;
        for (const block of splitSections(text)) {
          body.append(sectionNode(block, block.level === 2 ? letterFor(n++) : null));
        }
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
    const copy = el("button", "chip copy", "⧉ Copy the file");
    copy.title = "Copy everything shown below";
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
    $("#btn-wrap").classList.remove("hidden");
    showResult(names[0]);
    return true;
  }

  function showWrap(on) {
    state.wrapOpen = on;
    $("#wrap").classList.toggle("hidden", !on);
    if (on) closeOverlay();
  }

  function showDone(msg) {
    for (const t of document.querySelectorAll("#toasts .toast")) t.remove();
    const body = $("#done-body");
    body.innerHTML = "";
    if (msg.error) body.append(el("p", "warn", `The wrap-up failed: ${msg.error}`));
    body.append(el("p", "", `Duration ${hms(msg.duration_s)} · ${msg.ticks} ticks · $${(msg.cost_usd || 0).toFixed(3)}`));
    if (renderResult(msg.paths, msg.dir)) {
      const open = el("button", "primary", "Open the notes");
      open.addEventListener("click", () => { $("#done").close(); showWrap(true); });
      body.append(open);
    }
    $("#done").showModal();
  }

  // =============================================================== keyboard

  // Lower-case keys that already mean something; a section whose letter is one
  // of these keeps the capital as its only binding rather than stealing it.
  const RESERVED_KEYS = new Set(["j", "k", "c", "x", "p", "m", "t", "q", "l", "n", "w", "d"]);

  document.addEventListener("keydown", (e) => {
    const target = e.target;
    if (target instanceof Element && target.matches("input, textarea, [contenteditable]")) return;
    if (document.querySelector("dialog[open]") && e.key !== "?" && e.key !== "Escape") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const key = e.key;

    // The margin shows a capital and typing that capital always works. The
    // lower-case letter works too wherever nothing else claims it, so the
    // central move of this design does not require finding a modifier first.
    const upper = key.length === 1 ? key.toUpperCase() : "";
    const freeLower = /^[a-z]$/.test(key) && !RESERVED_KEYS.has(key);
    if ((/^[A-Z]$/.test(key) || freeLower) && state.letters.has(upper)) {
      const jump = upper;
      showWrap(false);
      jumpTo(jump);
      e.preventDefault();
      return;
    }

    if (key === "Escape") {
      if (state.wrapOpen) { showWrap(false); e.preventDefault(); }
      else if (state.overlay) { closeOverlay(); e.preventDefault(); }
      return;
    }
    if (key === "j") { moveSelection(1); e.preventDefault(); }
    else if (key === "k") { moveSelection(-1); e.preventDefault(); }
    else if (key === "c" && state.selected) { toggleCovered(state.selected); e.preventDefault(); }
    else if (key === "x" && state.selected) { toggleSkipped(state.selected); e.preventDefault(); }
    else if (key === "p" && state.selected) { togglePin(state.selected); e.preventDefault(); }
    else if (key === "m") { send({ type: "sync_mark" }); e.preventDefault(); }
    else if (key === "t") { toggleOverlay("transcript"); e.preventDefault(); }
    else if (key === "q") { toggleOverlay("questions"); e.preventDefault(); }
    else if (key === "l") { toggleOverlay("mentions"); e.preventDefault(); }
    else if (key === "n") { toggleOverlay("new"); e.preventDefault(); }
    else if (key === "w") {
      if (!$("#btn-wrap").classList.contains("hidden")) showWrap(!state.wrapOpen);
      e.preventDefault();
    }
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
      if (item) { showWrap(false); select(item.node_id); }
      e.preventDefault();
    }
  });

  // ================================================================= chrome

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "sync_mark") send({ type: "sync_mark" });
    else send({ type: "control", action });
  });

  $("#overlay-close").addEventListener("click", closeOverlay);
  $("#wrap-back").addEventListener("click", () => showWrap(false));
  $("#btn-wrap").addEventListener("click", () => showWrap(!state.wrapOpen));

  // ------------------------------------------------------------ tick settings

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
      : "No outline loaded. The score stays empty and the model has nothing to track.";
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

  // ================================================================= outline

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
    const pane = $("#score-pane");
    const hint = $("#score-drop");
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

  // =================================================================== boot

  $("#btn-help").addEventListener("click", () => $("#help").showModal());
  $("#llm-status").addEventListener("click", showTicks);
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
  $("#score").addEventListener("scroll", () => {
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

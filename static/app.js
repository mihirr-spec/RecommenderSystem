/* Folio — frontend. Plain JS, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const state = {
  meta: null,
  genres: [],
  discover: { genre: null, sort: "popular", offset: 0 },
  user: null,
  userControls: null,
  reader: store.get("folio.reader", { ratings: {}, books: {}, genres: [] }),
  newControls: null,
  rateOffset: 0,
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

const debounce = (fn, ms = 250) => {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "k" : String(n);
const hash = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) | 0; return Math.abs(h); };

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 2200);
}

/* ---------------- covers & cards ---------------- */
const PALETTES = [
  ["#7c2d12", "#c2410c"], ["#1e3a5f", "#3b6ea5"], ["#14532d", "#3f7d58"], ["#4c1d95", "#7c5cc4"],
  ["#713f12", "#b7791f"], ["#1f2937", "#4b5563"], ["#831843", "#be4b84"], ["#134e4a", "#2c8c84"],
];

function genCover(b) {
  const [a, c] = PALETTES[hash(b.title) % PALETTES.length];
  const author = (b.authors || "").split(",")[0];
  return `<div class="gen-cover" style="background:linear-gradient(160deg,${a},${c})"><b>${esc(b.title.replace(/\s*\(.*\)$/, ""))}</b><span>${esc(author)}</span></div>`;
}

function cover(b) {
  if (!b.cover) return genCover(b);
  return `<img loading="lazy" src="${esc(b.cover)}" alt="" data-fallback="${b.id}">`;
}

// Replace broken cover images with a generated cover.
document.addEventListener("error", (e) => {
  const img = e.target;
  if (img.tagName === "IMG" && img.dataset.fallback) {
    const b = bookCache.get(+img.dataset.fallback);
    if (b) img.outerHTML = genCover(b);
  }
}, true);

const bookCache = new Map();
const remember = (books) => { books.forEach((b) => bookCache.set(b.id, b)); return books; };

const starText = (r) => "★".repeat(Math.round(r)) + "☆".repeat(5 - Math.round(r));

function bookCard(b, opt = {}) {
  const badge = opt.match && b.match != null ? `<span class="badge match" title="Taste match: how closely this book's tags match your taste profile, relative to your single best match">${b.match}% match</span>` : "";
  const meta = [];
  if (opt.userRating) meta.push(`<span class="your-rating">${starText(b.user_rating)}</span>`);
  else meta.push(`<span><span class="stars">★</span> ${b.avg_rating.toFixed(2)}</span>`);
  if (opt.predicted && b.predicted_rating) meta.push(`<span title="SVD predicted rating">pred. ${b.predicted_rating.toFixed(1)}</span>`);
  else if (!opt.userRating) meta.push(`<span>${fmt(b.ratings_count)} ratings</span>`);
  if (opt.similarity && b.shared_tags?.length) meta.push(`<span class="shared">${esc(b.shared_tags.join(" · "))}</span>`);
  const reason = opt.reason && b.reason
    ? `<div class="reason ${b.reason.type}" ${b.reason.book_id ? `data-book="${b.reason.book_id}"` : ""}>${esc(b.reason.text)}</div>` : "";
  return `
    <article class="book" data-book="${b.id}">
      <div class="cover-wrap">${badge}<div class="cover">${cover(b)}</div></div>
      <div>
        <div class="book-title">${esc(b.title)}</div>
        <div class="book-author">${esc(b.authors)}</div>
      </div>
      <div class="book-meta">${meta.join("")}</div>
      ${reason}
    </article>`;
}

function renderGrid(el, books, opt = {}) {
  remember(books);
  el.innerHTML = books.length ? books.map((b) => bookCard(b, opt)).join("") : `<div class="empty">${opt.empty || "Nothing here yet."}</div>`;
}

function skeleton(el, n = 12) {
  el.innerHTML = Array.from({ length: n }, () =>
    `<div class="book skel"><div class="cover"></div><div class="line"></div><div class="line short"></div></div>`).join("");
}

// Any element with data-book opens the detail modal.
document.addEventListener("click", (e) => {
  const reason = e.target.closest(".reason[data-book]");
  const el = reason || e.target.closest("[data-book]");
  if (!el || e.target.closest(".star-input")) return;
  if (el.closest(".rate-item") && !e.target.closest(".mini-cover")) return;
  e.stopPropagation();
  openBook(+el.dataset.book);
});

/* ---------------- star input ---------------- */
function starInput(value, onRate) {
  const wrap = document.createElement("div");
  wrap.className = "star-input";
  for (let i = 1; i <= 5; i++) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = "★";
    b.title = ["Did not like it", "It was ok", "Liked it", "Really liked it", "It was amazing"][i - 1];
    if (i <= value) b.classList.add("on");
    b.onclick = (e) => { e.stopPropagation(); onRate(i === value ? 0 : i); };
    wrap.appendChild(b);
  }
  return wrap;
}

function setReaderRating(book, stars) {
  const r = state.reader;
  if (stars) { r.ratings[book.id] = stars; r.books[book.id] = book; }
  else { delete r.ratings[book.id]; delete r.books[book.id]; }
  store.set("folio.reader", r);
  updateReaderStatus();
  refreshNewRecs();
}

/* ---------------- modal ---------------- */
async function openBook(id) {
  const modal = $("#modal");
  const body = $("#modal-body");
  body.innerHTML = `<div class="detail"><div class="skel"><div class="cover"></div></div><div><div class="skel"><div class="line"></div></div></div></div>`;
  modal.hidden = false;
  document.body.style.overflow = "hidden";
  try {
    const b = await api(`/api/books/${id}`);
    remember([b, ...b.similar]);
    body.innerHTML = `
      <div class="detail">
        <div class="cover-wrap"><div class="cover">${cover(b)}</div></div>
        <div>
          ${b.genres.length ? `<p class="eyebrow">${esc(b.genres.join(" · "))}</p>` : ""}
          <h2>${esc(b.title)}</h2>
          <div class="by">by ${esc(b.authors)}${b.year ? ` · ${b.year < 0 ? -b.year + " BC" : b.year}` : ""}</div>
          <div class="facts">
            <div class="fact"><b><span class="stars">★</span> ${b.avg_rating.toFixed(2)}</b><span>Goodreads average</span></div>
            <div class="fact"><b>${fmt(b.ratings_count)}</b><span>Goodreads ratings</span></div>
            <div class="fact"><b>${fmt(b.n_ratings_dataset)}</b><span>ratings in dataset</span></div>
          </div>
          <div class="tag-row">${b.tags.slice(0, 10).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
          <div class="detail-actions">
            <span class="muted small">Your rating:</span>
            <span id="modal-stars"></span>
            <a class="btn ghost small" href="${b.goodreads_url}" target="_blank" rel="noopener">View on Goodreads ↗</a>
          </div>
        </div>
      </div>
      <div class="similar-head"><h3>Readers who liked this also liked</h3><span class="muted small">co-readership + tag similarity</span></div>
      <div class="grid" id="modal-similar"></div>`;
    const mount = () => {
      const s = $("#modal-stars");
      s.innerHTML = "";
      s.appendChild(starInput(state.reader.ratings[b.id] || 0, (v) => {
        setReaderRating(b, v);
        toast(v ? `Rated ${v}★, added to your New Reader profile` : "Rating removed");
        mount();
      }));
    };
    mount();
    renderGrid($("#modal-similar"), b.similar, { similarity: true });
  } catch (err) {
    body.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

function closeModal() {
  $("#modal").hidden = true;
  document.body.style.overflow = "";
}
$$("[data-close]").forEach((el) => (el.onclick = closeModal));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* ---------------- search ---------------- */
function setupSearch() {
  const input = $("#search-input");
  const box = $("#search-results");
  let active = -1;
  const run = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { box.hidden = true; return; }
    const res = remember(await api(`/api/search?q=${encodeURIComponent(q)}&n=10`));
    active = -1;
    box.innerHTML = res.length ? res.map((b) => `
      <div class="sr-item" data-id="${b.id}">
        <div class="mini-cover">${cover(b)}</div>
        <div><b>${esc(b.title)}</b><span>${esc(b.authors)} · ★ ${b.avg_rating.toFixed(2)}</span></div>
      </div>`).join("") : `<div class="sr-item"><span>No books match "${esc(q)}"</span></div>`;
    box.hidden = false;
  }, 200);
  input.addEventListener("input", run);
  input.addEventListener("focus", () => { if (box.innerHTML && input.value.trim().length > 1) box.hidden = false; });
  input.addEventListener("keydown", (e) => {
    const items = $$(".sr-item[data-id]", box);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((it, i) => it.classList.toggle("active", i === active));
    } else if (e.key === "Enter" && items[Math.max(active, 0)]) {
      openBook(+items[Math.max(active, 0)].dataset.id);
      box.hidden = true;
    }
  });
  box.addEventListener("click", (e) => {
    const it = e.target.closest(".sr-item[data-id]");
    if (it) { openBook(+it.dataset.id); box.hidden = true; }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest("#search")) box.hidden = true; });
}

/* ---------------- routing ---------------- */
const views = ["discover", "foryou", "new", "insights"];
const loaded = {};

function route() {
  const [view, arg] = (location.hash.slice(1) || "discover").split("/");
  if (view === "book" && +arg) {
    // Shareable link to a book: show it over the Discover page.
    history.replaceState(null, "", "#discover");
    openBook(+arg);
    return route();
  }
  const v = views.includes(view) ? view : "discover";
  views.forEach((x) => ($(`#view-${x}`).hidden = x !== v));
  $$("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.view === v));
  window.scrollTo({ top: 0 });
  if (!loaded[v]) { loaded[v] = true; ({ discover: initDiscover, foryou: initForYou, new: initNew, insights: initInsights })[v](); }
  if (v === "foryou" && arg && +arg !== state.user) loadUser(+arg);
}
window.addEventListener("hashchange", route);

/* ---------------- discover ---------------- */
function genreChips(el, selected, onToggle, { multi = false, all = true } = {}) {
  const sel = new Set(multi ? selected : selected ? [selected] : []);
  el.innerHTML = (all && !multi ? `<button class="chip ${sel.size ? "" : "on"}" data-g="">All</button>` : "") +
    state.genres.map((g) => `<button class="chip ${sel.has(g.name) ? "on" : ""}" data-g="${esc(g.name)}">${esc(g.name)}<small>${fmt(g.count)}</small></button>`).join("");
  el.onclick = (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    const g = c.dataset.g || null;
    if (multi) { sel.has(g) ? sel.delete(g) : sel.add(g); onToggle([...sel]); }
    else onToggle(g);
    genreChips(el, multi ? [...sel] : g, onToggle, { multi, all });
  };
}

async function loadDiscover(append = false) {
  const d = state.discover;
  const grid = $("#discover-grid");
  if (!append) { d.offset = 0; skeleton(grid); }
  const params = new URLSearchParams({ sort: d.sort, n: 24, offset: d.offset });
  if (d.genre) params.set("genre", d.genre);
  const books = remember(await api(`/api/popular?${params}`));
  const html = books.map((b) => bookCard(b)).join("");
  if (append) grid.insertAdjacentHTML("beforeend", html); else grid.innerHTML = html;
  $("#discover-more").hidden = books.length < 24;
  $("#discover-title").textContent = `${d.sort === "popular" ? "Most popular" : "Top rated"}${d.genre ? " in " + d.genre : ""}`;
}

function initDiscover() {
  const m = state.meta.dataset;
  $("#hero-ratings").textContent = fmt(m.n_ratings);
  $("#hero-users").textContent = fmt(m.n_users);
  $("#hero-books").textContent = fmt(m.n_books);
  genreChips($("#genre-chips"), null, (g) => { state.discover.genre = g; loadDiscover(); });
  $("#sort-seg").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("#sort-seg button").forEach((x) => x.classList.toggle("on", x === b));
    state.discover.sort = b.dataset.sort;
    loadDiscover();
  };
  $("#discover-more").onclick = () => { state.discover.offset += 24; loadDiscover(true); };
  loadDiscover();
}

/* ---------------- recommendation controls ---------------- */
function makeControls(el, onChange) {
  const def = state.meta.hybrid_default;
  const c = { alpha: def.alpha, svd_weight: def.svd_weight, pop_weight: def.pop_weight, genre: "" };
  el.innerHTML = `
    <div class="slider">
      <label>Strategy <span id="${el.id}-a"></span></label>
      <input type="range" min="0" max="1" step="0.05" value="${c.alpha}" data-k="alpha">
      <div class="ends"><span>Content (tags)</span><span>Collaborative (co-reading)</span></div>
    </div>
    <div class="slider">
      <label>Star-rating model <span id="${el.id}-s"></span></label>
      <input type="range" min="0" max="1" step="0.05" value="${c.svd_weight}" data-k="svd_weight">
      <div class="ends"><span>Off</span><span>Strong SVD influence</span></div>
    </div>
    <div class="slider">
      <label>Popularity <span id="${el.id}-p"></span></label>
      <input type="range" min="0" max="1" step="0.05" value="${c.pop_weight}" data-k="pop_weight">
      <div class="ends"><span>Hidden gems</span><span>Bestsellers</span></div>
    </div>
    <div class="slider">
      <label>Only show genre</label>
      <select data-k="genre"><option value="">Any genre</option>${state.genres.map((g) => `<option>${esc(g.name)}</option>`).join("")}</select>
    </div>
    <div class="reset"><span>Defaults were tuned on validation users to maximise NDCG@10 (α = ${def.alpha}, SVD = ${def.svd_weight}, popularity = ${def.pop_weight}).</span><button class="link" type="button">Reset to tuned</button></div>`;
  const label = () => {
    $(`#${el.id}-a`).textContent = c.alpha === 0 ? "pure content" : c.alpha === 1 ? "pure co-reading" : `${Math.round(c.alpha * 100)}% co-reading · ${Math.round((1 - c.alpha) * 100)}% content`;
    $(`#${el.id}-s`).textContent = c.svd_weight.toFixed(2);
    $(`#${el.id}-p`).textContent = c.pop_weight.toFixed(2);
  };
  const fire = debounce(() => onChange({ ...c }), 220);
  el.oninput = (e) => {
    const k = e.target.dataset.k;
    if (!k) return;
    c[k] = k === "genre" ? e.target.value : +e.target.value;
    label();
    fire();
  };
  $(".reset .link", el).onclick = () => {
    c.alpha = def.alpha; c.svd_weight = def.svd_weight; c.pop_weight = def.pop_weight; c.genre = "";
    $("[data-k=alpha]", el).value = c.alpha;
    $("[data-k=svd_weight]", el).value = c.svd_weight;
    $("[data-k=pop_weight]", el).value = c.pop_weight;
    $("[data-k=genre]", el).value = "";
    label();
    fire();
  };
  label();
  return c;
}

const recQuery = (c, n = 24) => {
  const p = new URLSearchParams({ n, alpha: c.alpha, svd_weight: c.svd_weight, pop_weight: c.pop_weight });
  if (c.genre) p.set("genre", c.genre);
  return p;
};

/* ---------------- for you ---------------- */
const avatarColor = (id) => PALETTES[hash("u" + id) % PALETTES.length][1];

async function initForYou() {
  state.userControls = makeControls($("#foryou-controls"), () => loadUserRecs());
  const users = await api("/api/users/sample");
  $("#user-list").innerHTML = users.map((u) => `
    <button class="user-card" data-uid="${u.user_id}">
      <span class="avatar" style="background:${avatarColor(u.user_id)}">${String(u.user_id).slice(-2)}</span>
      <div><b>Reader #${u.user_id}</b><span>${u.n_ratings} ratings · ${esc(u.top_genres.slice(0, 2).join(", ") || "Mixed")}</span></div>
    </button>`).join("");
  $("#user-list").onclick = (e) => {
    const c = e.target.closest(".user-card");
    if (c) location.hash = `foryou/${c.dataset.uid}`;
  };
  $("#user-form").onsubmit = (e) => {
    e.preventDefault();
    const id = +$("#user-id").value;
    if (id) location.hash = `foryou/${id}`;
  };
  const arg = location.hash.split("/")[1];
  if (!arg) location.hash = `foryou/${users[0].user_id}`;
}

async function loadUser(uid) {
  state.user = uid;
  $$(".user-card").forEach((c) => c.classList.toggle("on", +c.dataset.uid === uid));
  $("#user-panel").hidden = false;
  try {
    const p = await api(`/api/users/${uid}`);
    remember([...p.rated, ...p.to_read]);
    $("#p-avatar").textContent = String(uid).slice(-2);
    $("#p-avatar").style.background = avatarColor(uid);
    $("#p-name").textContent = `Reader #${uid}`;
    $("#p-meta").textContent = `${p.n_ratings} books rated · average ${p.avg_rating}★ · ${p.n_to_read} on to-read shelf${p.top_genres.length ? " · loves " + p.top_genres.join(", ") : ""}`;
    $("#p-tags").innerHTML = p.taste_tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("");
    const max = Math.max(...Object.values(p.rating_histogram), 1);
    $("#p-hist").innerHTML = [1, 2, 3, 4, 5].map((s) => {
      const v = p.rating_histogram[s] || 0;
      return `<div class="hbar"><span>${v}</span><i style="height:${(v / max) * 60}px"></i><span>${s}★</span></div>`;
    }).join("");
    $("#p-favs").innerHTML = p.rated.slice(0, 16).map((b) => bookCard(b, { userRating: true })).join("");
    loadUserRecs();
  } catch (err) {
    $("#p-name").textContent = "Reader not found";
    $("#p-meta").textContent = err.message;
    $("#p-favs").innerHTML = "";
    $("#foryou-grid").innerHTML = "";
  }
}

async function loadUserRecs() {
  if (!state.user) return;
  const grid = $("#foryou-grid");
  skeleton(grid);
  const t0 = performance.now();
  const recs = await api(`/api/users/${state.user}/recommendations?${recQuery(state.userControls)}`);
  renderGrid(grid, recs, { match: true, reason: true, predicted: true, empty: "No recommendations for this filter." });
  $("#foryou-note").textContent = `scored all 10,000 books in ${Math.round(performance.now() - t0)} ms`;
}

/* ---------------- new reader ---------------- */
function updateReaderStatus() {
  const n = Object.keys(state.reader.ratings).length;
  $("#rated-count").textContent = `${n} rated`;
  const conf = Math.round((n / (n + 3)) * 100);
  $("#conf-bar").style.width = conf + "%";
  $("#conf-val").textContent = conf + "%";
}

function rateItem(b) {
  const el = document.createElement("div");
  const v = state.reader.ratings[b.id] || 0;
  el.className = "rate-item" + (v ? " rated" : "");
  el.innerHTML = `<div class="mini-cover" data-book="${b.id}">${cover(b)}</div><div class="info"><b title="${esc(b.title)}">${esc(b.title)}</b><small>${esc(b.authors)}</small></div>`;
  $(".info", el).appendChild(starInput(v, (s) => {
    setReaderRating(b, s);
    el.replaceWith(rateItem(b));
  }));
  return el;
}

async function loadRateGrid() {
  const grid = $("#rate-grid");
  const q = $("#rate-search").value.trim();
  let books;
  if (q.length > 1) {
    books = await api(`/api/search?q=${encodeURIComponent(q)}&n=18`);
  } else {
    const gs = state.reader.genres;
    if (gs.length) {
      const per = Math.ceil(18 / gs.length);
      const lists = await Promise.all(gs.map((g) => api(`/api/popular?genre=${encodeURIComponent(g)}&n=${per}&offset=${Math.floor(state.rateOffset / gs.length)}`)));
      const seen = new Set();
      books = [];
      for (let i = 0; i < per; i++) for (const l of lists) if (l[i] && !seen.has(l[i].id)) { seen.add(l[i].id); books.push(l[i]); }
    } else {
      books = await api(`/api/popular?n=18&offset=${state.rateOffset}`);
    }
    const rated = Object.values(state.reader.books).filter((b) => !books.some((x) => x.id === b.id));
    books = [...rated, ...books];
  }
  remember(books);
  grid.innerHTML = "";
  books.forEach((b) => grid.appendChild(rateItem(b)));
  if (!books.length) grid.innerHTML = `<div class="empty">No books found.</div>`;
}

const refreshNewRecs = debounce(async () => {
  if (!state.newControls) return;
  const grid = $("#new-grid");
  skeleton(grid, 12);
  const c = state.newControls;
  const recs = await api("/api/recommend", {
    method: "POST",
    body: {
      ratings: state.reader.ratings,
      genres: state.reader.genres,
      n: 24,
      alpha: c.alpha,
      svd_weight: c.svd_weight,
      pop_weight: c.pop_weight,
      genre: c.genre || null,
    },
  });
  const n = Object.keys(state.reader.ratings).length;
  renderGrid(grid, recs, { match: n > 0, reason: true, predicted: n > 0, empty: "No books match this filter." });
  $("#new-note").textContent = n === 0
    ? (state.reader.genres.length ? "Showing top-rated books in your genres. Rate a few to personalise." : "Pick genres or rate books to personalise.")
    : `personalised from ${n} rating${n > 1 ? "s" : ""}`;
}, 350);

function initNew() {
  genreChips($("#new-genres"), state.reader.genres, (gs) => {
    state.reader.genres = gs;
    store.set("folio.reader", state.reader);
    state.rateOffset = 0;
    loadRateGrid();
    refreshNewRecs();
  }, { multi: true, all: false });
  state.newControls = makeControls($("#new-controls"), () => refreshNewRecs());
  $("#rate-search").addEventListener("input", debounce(loadRateGrid, 250));
  $("#rate-more").onclick = () => { state.rateOffset += 18; loadRateGrid(); };
  $("#clear-ratings").onclick = () => {
    state.reader.ratings = {};
    state.reader.books = {};
    store.set("folio.reader", state.reader);
    updateReaderStatus();
    loadRateGrid();
    refreshNewRecs();
  };
  updateReaderStatus();
  loadRateGrid();
  refreshNewRecs();
}

/* ---------------- insights ---------------- */
function barCell(v, max, digits = 4) {
  return `<div class="cellbar"><i style="width:${Math.max(4, (v / max) * 90)}px"></i><span>${v.toFixed(digits)}</span></div>`;
}

function initInsights() {
  const m = state.meta;
  const d = m.dataset;
  const k = m.ranking_protocol.k;
  const nd = `NDCG@${k}`;
  const rm = m.rating_metrics;
  const bestRmse = Math.min(...rm.map((r) => r.RMSE));
  const rk = m.ranking_metrics;
  const cols = [`Precision@${k}`, `Recall@${k}`, nd, `HitRate@${k}`, "Coverage"];
  const best = Object.fromEntries(cols.map((c) => [c, Math.max(...rk.map((r) => r[c]))]));
  const dist = d.rating_distribution;
  const maxDist = Math.max(...Object.values(dist));
  const H = m.hybrid;
  const byName = (s) => rk.find((r) => r.model.startsWith(s));
  const hyb = byName("Hybrid"), knn = byName("Item-kNN"), cb = byName("Content"), svd = byName("SVD"), pop = byName("Most Popular");
  const sv = rm.find((r) => r.model.startsWith("SVD (tuned)"));

  // Hybrid grid heatmap at the chosen SVD weight: rows = alpha, cols = popularity weight.
  const grid = m.hybrid_grid.filter((g) => g.svd_weight === H.svd_weight);
  const alphas = [...new Set(grid.map((g) => g.alpha))].sort((a, b) => a - b);
  const pops = [...new Set(grid.map((g) => g.pop_weight))].sort((a, b) => a - b);
  const vals = grid.map((g) => g[nd]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const cell = (a, p) => {
    const g = grid.find((x) => x.alpha === a && x.pop_weight === p);
    const t = (g[nd] - lo) / (hi - lo || 1);
    const isBest = a === H.alpha && p === H.pop_weight;
    return `<div class="cell" style="background:color-mix(in srgb, var(--good) ${Math.round(8 + t * 72)}%, var(--paper));color:${t > .55 ? "#fff" : "var(--ink)"};${isBest ? "outline:2px solid var(--ink);" : ""}">${g[nd].toFixed(3)}</div>`;
  };
  const fold = m.fold_in.grid;
  const foldMax = Math.max(...fold.map((f) => f.RMSE));
  const foldMin = Math.min(...fold.map((f) => f.RMSE));
  const lift = ((hyb[nd] / cb[nd] - 1) * 100).toFixed(0);

  $("#insights-body").innerHTML = `
    <div class="kpis">
      <div class="card kpi"><b>${hyb[nd].toFixed(3)}</b><span>NDCG@${k} of the hybrid (best model)</span></div>
      <div class="card kpi"><b>${(hyb[`HitRate@${k}`] * 100).toFixed(0)}%</b><span>of test readers get a book they loved in their top ${k}</span></div>
      <div class="card kpi"><b>${(hyb.Coverage * 100).toFixed(0)}%</b><span>of the catalogue gets recommended to someone</span></div>
      <div class="card kpi"><b>${sv.RMSE.toFixed(3)}</b><span>star-rating RMSE of the tuned SVD</span></div>
    </div>

    <div class="ins-grid">
      <div class="card panel wide">
        <h3>How it works</h3>
        <p class="sub">A hybrid recommender. Four signals are scored over all ${d.n_books.toLocaleString()} books, standardised to z-scores, then blended with weights tuned on validation readers.</p>
        <div class="pipeline">
          <div class="pipe"><b>Item-kNN (co-reading)</b><p>Cosine similarity between books based on who read them, keeping ${m.knn_neighbours} neighbours per book. Each book a reader rated votes for its neighbours.</p></div>
          <div class="pipe"><b>Content-based</b><p>TF-IDF over ${d.n_content_features.toLocaleString()} Goodreads tags and authors. A reader's profile is the rating-weighted sum of the books they read.</p></div>
          <div class="pipe"><b>SVD star-rating model</b><p>Matrix factorisation with ${m.svd_params.n_factors} latent factors, tuned by cross-validation. It predicts the star rating shown on each card.</p></div>
          <div class="pipe"><b>Popularity + explain</b><p>Goodreads-wide popularity as an optional prior. Each pick is explained by the most similar book the reader loved.</p></div>
        </div>
        <div class="formula">score = α·z(kNN) + (1−α)·z(content) + w<sub>svd</sub>·z(r̂<sub>SVD</sub>) + w<sub>pop</sub>·z(log pop) &nbsp;&nbsp;|&nbsp;&nbsp; tuned: α = ${H.alpha}, w<sub>svd</sub> = ${H.svd_weight}, w<sub>pop</sub> = ${H.pop_weight}</div>
        <p class="sub" style="margin:0">New readers work straight away: kNN and content need only their ratings. The SVD vector is <b>folded in</b> by ridge regression against the fixed book factors (λ = ${m.fold_in.reg}), and its weight grows with confidence n/(n+3).</p>
      </div>

      <div class="card panel wide">
        <h3>Top-${k} recommendation quality</h3>
        <p class="sub">For ${m.ranking_protocol.n_test_users.toLocaleString()} test readers, each model ranks <b>every book they haven't rated</b>. A hit is a hidden book they rated ≥ ${m.ranking_protocol.relevance_threshold}★. Higher is better. A random ranking scores about 0.0004.</p>
        <table>
          <tr><th>Model</th>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>
          ${rk.map((r) => `<tr class="${r.model.startsWith("Hybrid") ? "hl" : ""}"><td>${esc(r.model)}</td>${cols.map((c) => `<td class="num ${r[c] === best[c] ? "best" : ""}">${barCell(r[c], best[c], c === "Coverage" ? 3 : 4)}</td>`).join("")}</tr>`).join("")}
        </table>
        <div class="callout">The hybrid improves NDCG@${k} by <b>${lift}%</b> over content alone (${cb[nd].toFixed(3)} → ${hyb[nd].toFixed(3)}). Item-kNN alone scores ${knn[nd].toFixed(3)}.
          Two findings about this data: <b>popularity is useless here</b> (NDCG ${pop[nd].toFixed(4)}), because Goodbooks-10k was sampled to about 100 ratings per book, so bestsellers are no more likely to appear in a user's history.
          <b>SVD ranks poorly</b> (NDCG ${svd[nd].toFixed(4)}) despite the best RMSE: predicting <i>how many stars</i> is a different task from predicting <i>which books</i> someone reads.</div>
      </div>

      <div class="card panel">
        <h3>Rating prediction</h3>
        <p class="sub">Error predicting held-out star ratings. Lower is better.</p>
        <table>
          <tr><th>Model</th><th>RMSE</th><th>MAE</th></tr>
          ${rm.map((r) => `<tr class="${r.RMSE === bestRmse ? "hl" : ""}"><td>${esc(r.model)}</td><td class="num ${r.RMSE === bestRmse ? "best" : ""}">${r.RMSE.toFixed(4)}</td><td class="num">${r.MAE.toFixed(4)}</td></tr>`).join("")}
        </table>
      </div>

      <div class="card panel">
        <h3>Ratings in the dataset</h3>
        <p class="sub">${fmt(d.n_ratings)} ratings from ${fmt(d.n_users)} readers, and ${(d.sparsity * 100).toFixed(2)}% of the user × book matrix is empty. Readers rate generously.</p>
        <div class="chart-bars">
          ${Object.entries(dist).map(([s, v]) => `<div class="col"><em>${fmt(v)}</em><i style="height:${(v / maxDist) * 130}px"></i><small>${s}★</small></div>`).join("")}
        </div>
      </div>

      <div class="card panel">
        <h3>Tuning the hybrid blend</h3>
        <p class="sub">NDCG@${k} on ${m.ranking_protocol.n_val_users.toLocaleString()} validation readers (separate from the test readers), at w<sub>svd</sub> = ${H.svd_weight}. Rows: α (co-reading share). Columns: popularity weight. The outlined cell is the one chosen.</p>
        <div class="heat" style="grid-template-columns: 48px repeat(${pops.length}, 1fr)">
          <div class="lab">α \\ pop</div>${pops.map((p) => `<div class="lab">${p}</div>`).join("")}
          ${alphas.map((a) => `<div class="lab">${a}</div>${pops.map((p) => cell(a, p)).join("")}`).join("")}
        </div>
      </div>

      <div class="card panel">
        <h3>Cold-start fold-in</h3>
        <p class="sub">Known readers were treated as new: their SVD vectors were rebuilt from training ratings only. The chart shows test RMSE for each ridge strength λ. Chosen λ = ${m.fold_in.reg}.</p>
        <div class="chart-bars">
          ${fold.map((f) => `<div class="col"><em>${f.RMSE.toFixed(3)}</em><i style="height:${20 + ((f.RMSE - foldMin) / (foldMax - foldMin || 1)) * 110}px;${f.reg === m.fold_in.reg ? "background:var(--good)" : ""}"></i><small>λ=${f.reg}</small></div>`).join("")}
        </div>
      </div>

      <div class="card panel wide">
        <h3>SVD hyper-parameter search</h3>
        <p class="sub">Randomised search with 3-fold cross-validation on a 300k-rating sample of the training set.</p>
        <table>
          <tr><th>Factors</th><th>Epochs</th><th>Learning rate</th><th>Regularisation</th><th>CV RMSE</th><th>CV MAE</th></tr>
          ${m.svd_tuning.slice(0, 6).map((t, i) => `<tr class="${i === 0 ? "hl" : ""}"><td>${t.param_n_factors}</td><td>${t.param_n_epochs}</td><td>${t.param_lr_all}</td><td>${t.param_reg_all}</td><td class="num ${i === 0 ? "best" : ""}">${t.mean_test_rmse.toFixed(4)}</td><td class="num">${t.mean_test_mae.toFixed(4)}</td></tr>`).join("")}
        </table>
      </div>
    </div>`;
}

/* ---------------- theme & boot ---------------- */
function setupTheme() {
  const saved = store.get("folio.theme", null);
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme-toggle").onclick = () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("folio.theme", next);
  };
}

(async function boot() {
  setupTheme();
  setupSearch();
  [state.meta, state.genres] = await Promise.all([api("/api/stats"), api("/api/genres")]);
  route();
})();

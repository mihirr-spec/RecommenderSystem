/* Plotline — frontend. Plain JS, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const state = {
  meta: null,
  genres: [],
  discover: { genres: [], sort: "popular", offset: 0 },
  token: store.get("plotline.token", null),
  me: null,
  myRatings: {},
  myBooks: {},
  view: null,
  homeControls: null,
  homeGenres: [],
  demos: null,
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}) },
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
  const genres = opt.genres && b.genres?.length
    ? `<div class="book-genres">${b.genres.map((g) => opt.genres.includes(g) ? `<b>${esc(g)}</b>` : esc(g)).join(" · ")}</div>` : "";
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
      ${genres}
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
      if (!state.me) { s.innerHTML = `<a class="link" href="#signup">Sign up to rate this book</a>`; return; }
      s.innerHTML = "";
      s.appendChild(starInput(state.myRatings[b.id] || 0, async (v) => {
        if (!(await rateBook(b, v))) return;
        toast(v ? `Rated ${v}★. Your recommendations will update.` : "Rating removed");
        mount();
        refreshView();
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

/* ---------------- accounts ---------------- */
const initial = (name) => (name || "?").trim().charAt(0).toUpperCase();
const avatarColor = (id) => PALETTES[hash("u" + id) % PALETTES.length][1];

async function loadMe() {
  state.me = null;
  state.myRatings = {};
  state.myBooks = {};
  if (!state.token) return null;
  try {
    state.me = await api("/api/me");
  } catch {
    setToken(null);
    return null;
  }
  for (const b of state.me.profile.rated) {
    state.myRatings[b.id] = b.user_rating;
    state.myBooks[b.id] = b;
  }
  remember(state.me.profile.rated);
  return state.me;
}

function setToken(token) {
  state.token = token;
  store.set("plotline.token", token);
}

async function afterLogin(res, isNew) {
  setToken(res.token);
  await loadMe();
  renderNav();
  toast(isNew ? `Welcome to Plotline, ${state.me.name}!` : `Welcome back, ${state.me.name}!`);
  location.hash = isNew ? "onboarding" : "home";
}

async function loginDemo(key) {
  try {
    afterLogin(await api(`/api/auth/demo/${key}`, { method: "POST" }), false);
  } catch (err) {
    toast(err.message);
  }
}

async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* already logged out */ }
  setToken(null);
  state.me = null;
  state.myRatings = {};
  state.myBooks = {};
  renderNav();
  toast("You have been logged out");
  location.hash = "";
}

// Saves a rating for the logged-in user. Returns false if they can't rate.
async function rateBook(b, stars) {
  if (!state.me) {
    toast("Create an account to rate books");
    location.hash = "signup";
    return false;
  }
  await api(`/api/me/ratings/${b.id}`, { method: "PUT", body: { rating: stars } });
  if (stars) { state.myRatings[b.id] = stars; state.myBooks[b.id] = b; }
  else { delete state.myRatings[b.id]; delete state.myBooks[b.id]; }
  return true;
}

async function setMyGenres(genres) {
  await api("/api/me/genres", { method: "PUT", body: { genres } });
  state.me.genres = genres;
}

function demoCard(d, compact = false) {
  return `
    <button class="demo-card ${compact ? "compact" : ""}" type="button" data-demo="${esc(d.key)}">
      <span class="avatar" style="background:${avatarColor(d.name)}">${initial(d.name)}</span>
      <div><b>${esc(d.name)}</b><span>${esc(d.taste)}</span>${compact ? "" : `<small>${d.n_ratings} books rated · Goodreads reader #${d.dataset_user_id}</small>`}</div>
      <span class="go">Log in →</span>
    </button>`;
}

async function demos() {
  if (!state.demos) state.demos = await api("/api/auth/demo-accounts");
  return state.demos;
}

document.addEventListener("click", (e) => {
  const d = e.target.closest("[data-demo]");
  if (d) { e.preventDefault(); loginDemo(d.dataset.demo); }
  const s = e.target.closest("[data-scroll]");
  if (s) { e.preventDefault(); document.getElementById(s.dataset.scroll).scrollIntoView({ behavior: "smooth" }); }
});

function setupAuthForms() {
  const handle = (form, submit) => {
    const err = $(".form-error", form);
    form.onsubmit = async (e) => {
      e.preventDefault();
      err.hidden = true;
      const data = Object.fromEntries(new FormData(form));
      const btn = $("button[type=submit]", form);
      btn.disabled = true;
      try {
        await submit(data);
        form.reset();
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
      } finally {
        btn.disabled = false;
      }
    };
  };
  handle($("#login-form"), async (d) => {
    if (!d.email || !d.password) throw new Error("Enter your email and password");
    await afterLogin(await api("/api/auth/login", { method: "POST", body: d }), false);
  });
  handle($("#signup-form"), async (d) => {
    if (!d.name.trim()) throw new Error("Enter your name");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(d.email)) throw new Error("Enter a valid email address");
    if (d.password.length < 6) throw new Error("Password must be at least 6 characters");
    await afterLogin(await api("/api/auth/signup", { method: "POST", body: d }), true);
  });
}

/* ---------------- navigation ---------------- */
function renderNav() {
  const tabs = state.me
    ? [["home", "For You"], ["discover", "Discover"], ["library", "My Books"]]
    : [["landing", "Home"], ["discover", "Discover"]];
  $("#tabs").innerHTML = tabs.map(([v, label]) => `<a href="#${v === "landing" ? "" : v}" data-view="${v}">${label}</a>`).join("");
  $("#auth-area").innerHTML = state.me
    ? `<span class="user-chip"><span class="avatar sm" style="background:${avatarColor(state.me.name)}">${initial(state.me.name)}</span>
         <span class="user-name">${esc(state.me.name)}</span>${state.me.is_demo ? `<span class="demo-tag">demo</span>` : ""}</span>
       <button class="btn ghost small" id="logout">Log out</button>`
    : `<a class="btn ghost small" href="#login">Log in</a><a class="btn primary small" href="#signup">Sign up</a>`;
  if (state.me) $("#logout").onclick = logout;
  markTab();
}

function markTab() {
  $$("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.view === state.view));
}

const VIEWS = ["landing", "login", "signup", "onboarding", "home", "library", "discover"];
const PRIVATE = new Set(["onboarding", "home", "library"]);
const loaded = {};

function route() {
  closeModal();
  let [view, arg] = location.hash.slice(1).split("/");
  if (view === "book" && +arg) {
    // Shareable link to a book: show it over the home page.
    history.replaceState(null, "", state.me ? "#home" : "#");
    route();
    openBook(+arg);
    return;
  }
  if (!VIEWS.includes(view)) view = state.me ? "home" : "landing";
  if (PRIVATE.has(view) && !state.me) { location.hash = "login"; return; }
  if (["landing", "login", "signup"].includes(view) && state.me) { location.hash = "home"; return; }
  state.view = view;
  VIEWS.forEach((x) => ($(`#view-${x}`).hidden = x !== view));
  markTab();
  window.scrollTo({ top: 0 });
  renderView(view);
}
window.addEventListener("hashchange", route);

// Account pages are rebuilt on every visit; public pages are built once.
function renderView(view) {
  if (view === "home") return renderHome();
  if (view === "library") return renderLibrary();
  if (view === "onboarding") return renderOnboarding();
  if (view === "login" || view === "signup") return renderAuth(view);
  if (!loaded[view]) { loaded[view] = true; ({ landing: initLanding, discover: initDiscover })[view](); }
}

/* ---------------- landing ---------------- */
async function initLanding() {
  const m = state.meta;
  const hyb = m.ranking_metrics.find((r) => r.model.startsWith("Hybrid"));
  $("#st-ratings").textContent = fmt(m.dataset.n_ratings);
  $("#st-users").textContent = fmt(m.dataset.n_users);
  $("#st-books").textContent = fmt(m.dataset.n_books);
  $("#st-hit").textContent = Math.round(hyb[`HitRate@${m.ranking_protocol.k}`] * 100) + "%";
  const [books, ds] = await Promise.all([api("/api/popular?n=24&sort=top_rated"), demos()]);
  remember(books);
  $("#cover-collage").innerHTML = books.filter((b) => b.cover).slice(0, 9)
    .map((b, i) => `<div class="collage-item c${i}" data-book="${b.id}"><div class="cover">${cover(b)}</div></div>`).join("");
  $("#landing-demos").innerHTML = ds.map((d) => demoCard(d)).join("");
}

async function renderAuth(view) {
  if (view === "login") $("#login-demos").innerHTML = (await demos()).map((d) => demoCard(d, true)).join("");
  $(`#${view}-form input`).focus();
}

/* ---------------- discover ---------------- */
function genreChips(el, selected, onToggle, { multi = false, all = true } = {}) {
  const sel = new Set(multi ? selected : selected ? [selected] : []);
  el.innerHTML = (all ? `<button class="chip ${sel.size ? "" : "on"}" data-g="">All</button>` : "") +
    state.genres.map((g) => `<button class="chip ${sel.has(g.name) ? "on" : ""}" data-g="${esc(g.name)}">${esc(g.name)}<small>${fmt(g.count)}</small></button>`).join("");
  el.onclick = (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    const g = c.dataset.g || null;
    if (multi) { if (!g) sel.clear(); else sel.has(g) ? sel.delete(g) : sel.add(g); onToggle([...sel]); }
    else onToggle(g);
    genreChips(el, multi ? [...sel] : g, onToggle, { multi, all });
  };
}

async function loadDiscover(append = false) {
  const d = state.discover;
  const grid = $("#discover-grid");
  if (!append) { d.offset = 0; skeleton(grid); }
  const params = new URLSearchParams({ sort: d.sort, n: 24, offset: d.offset });
  d.genres.forEach((g) => params.append("genre", g));
  const books = remember(await api(`/api/popular?${params}`));
  const html = books.map((b) => bookCard(b, { genres: d.genres })).join("");
  if (append) grid.insertAdjacentHTML("beforeend", html); else grid.innerHTML = html;
  $("#discover-more").hidden = books.length < 24;
  $("#discover-title").textContent = `${d.sort === "popular" ? "Most popular" : "Top rated"}${d.genres.length ? " in " + d.genres.join(" + ") : ""}`;
  $("#discover-note").textContent = d.genres.length > 1 ? "Books with all your selected genres come first." : "Pick one or more genres to mix them.";
}

function initDiscover() {
  genreChips($("#genre-chips"), [], (gs) => { state.discover.genres = gs; loadDiscover(); }, { multi: true });
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
  const c = { alpha: def.alpha, svd_weight: def.svd_weight, pop_weight: def.pop_weight };
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
    c[k] = +e.target.value;
    label();
    fire();
  };
  $(".reset .link", el).onclick = () => {
    c.alpha = def.alpha; c.svd_weight = def.svd_weight; c.pop_weight = def.pop_weight;
    $("[data-k=alpha]", el).value = c.alpha;
    $("[data-k=svd_weight]", el).value = c.svd_weight;
    $("[data-k=pop_weight]", el).value = c.pop_weight;
    label();
    fire();
  };
  label();
  return c;
}

const recQuery = (c, n = 24, genres = []) => {
  const p = new URLSearchParams({ n, alpha: c.alpha, svd_weight: c.svd_weight, pop_weight: c.pop_weight });
  genres.forEach((g) => p.append("genre", g));
  return p;
};

/* ---------------- rating books (onboarding + my books) ---------------- */
function rateItem(b, onRated) {
  const el = document.createElement("div");
  const v = state.myRatings[b.id] || 0;
  el.className = "rate-item" + (v ? " rated" : "");
  el.innerHTML = `<div class="mini-cover" data-book="${b.id}">${cover(b)}</div><div class="info"><b title="${esc(b.title)}">${esc(b.title)}</b><small>${esc(b.authors)}</small></div>`;
  $(".info", el).appendChild(starInput(v, async (s) => {
    if (await rateBook(b, s)) { el.replaceWith(rateItem(b, onRated)); onRated(); }
  }));
  return el;
}

// A search box + grid of books to rate. Calls onChange after every rating.
function mountRater(el, onChange) {
  el.innerHTML = `
    <div class="confidence"><span class="pill rated-count"></span><span>Star-rating model confidence</span><div class="bar"><i></i></div><b></b></div>
    <div class="rate-search"><input type="search" placeholder="Search a book you've read…" autocomplete="off"></div>
    <div class="rate-grid"></div>
    <div class="center"><button class="btn ghost small" type="button">Show other books</button></div>`;
  let offset = 0;
  const status = () => {
    const n = Object.keys(state.myRatings).length;
    const conf = Math.round((n / (n + 3)) * 100);
    $(".rated-count", el).textContent = `${n} rated`;
    $(".bar i", el).style.width = conf + "%";
    $(".confidence b", el).textContent = conf + "%";
  };
  const rated = () => { status(); onChange(); };
  const load = async () => {
    const grid = $(".rate-grid", el);
    const q = $("input", el).value.trim();
    let books;
    if (q.length > 1) {
      books = await api(`/api/search?q=${encodeURIComponent(q)}&n=18`);
    } else {
      const gs = state.me.genres;
      if (gs.length) {
        const per = Math.ceil(18 / gs.length);
        const lists = await Promise.all(gs.map((g) => api(`/api/popular?genre=${encodeURIComponent(g)}&n=${per}&offset=${Math.floor(offset / gs.length)}`)));
        const seen = new Set();
        books = [];
        for (let i = 0; i < per; i++) for (const l of lists) if (l[i] && !seen.has(l[i].id)) { seen.add(l[i].id); books.push(l[i]); }
      } else {
        books = await api(`/api/popular?n=18&offset=${offset}`);
      }
      const mine = Object.values(state.myBooks).filter((b) => !books.some((x) => x.id === b.id));
      if (offset === 0 && mine.length <= 12) books = [...mine, ...books];
    }
    remember(books);
    grid.innerHTML = "";
    books.forEach((b) => grid.appendChild(rateItem(b, rated)));
    if (!books.length) grid.innerHTML = `<div class="empty">No books found.</div>`;
  };
  $("input", el).addEventListener("input", debounce(load, 250));
  $(".center button", el).onclick = () => { offset += 18; load(); };
  status();
  load();
  return { reload: () => { offset = 0; load(); }, status };
}

/* ---------------- onboarding ---------------- */
async function loadMyRecs(grid, controls, n = 24, genres = []) {
  skeleton(grid, Math.min(n, 12));
  const t0 = performance.now();
  const recs = await api(`/api/me/recommendations?${recQuery(controls, n, genres)}`);
  const personal = state.me.is_demo || Object.keys(state.myRatings).length > 0;
  renderGrid(grid, recs, { match: personal, reason: true, predicted: personal, genres: genres.length ? genres : undefined, empty: "No books match this filter." });
  return Math.round(performance.now() - t0);
}

function renderOnboarding() {
  const me = state.me;
  $("#ob-title").textContent = `Welcome, ${me.name}! Tell us what you like`;
  const defaults = { ...state.meta.hybrid_default };
  const refresh = debounce(async () => {
    await loadMyRecs($("#ob-grid"), defaults, 12);
    const n = Object.keys(state.myRatings).length;
    $("#ob-note").textContent = n ? `personalised from ${n} rating${n > 1 ? "s" : ""}`
      : me.genres.length ? "top books in your genres. Rate a few to personalise." : "pick genres or rate books to personalise";
  }, 350);
  const rater = mountRater($("#ob-rater"), refresh);
  genreChips($("#ob-genres"), me.genres, async (gs) => { await setMyGenres(gs); rater.reload(); refresh(); }, { multi: true, all: false });
  refresh();
}

/* ---------------- home ---------------- */
const greeting = () => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; };

function renderProfile(me) {
  const p = me.profile;
  $("#p-avatar").textContent = initial(me.name);
  $("#p-avatar").style.background = avatarColor(me.name);
  $("#p-name").textContent = me.name;
  const bits = p.n_ratings ? [`${p.n_ratings} books rated`, `average ${p.avg_rating}★`] : ["No ratings yet"];
  if (p.n_to_read) bits.push(`${p.n_to_read} on to-read shelf`);
  if (p.top_genres.length) bits.push("loves " + p.top_genres.join(", "));
  else if (me.genres.length) bits.push("likes " + me.genres.join(", "));
  $("#p-meta").textContent = bits.join(" · ");
  $("#p-tags").innerHTML = p.taste_tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("");
  const max = Math.max(...Object.values(p.rating_histogram), 1);
  $("#p-hist").hidden = !p.n_ratings;
  $("#p-hist").innerHTML = [1, 2, 3, 4, 5].map((s) => {
    const v = p.rating_histogram[s] || 0;
    return `<div class="hbar"><span>${v}</span><i style="height:${(v / max) * 60}px"></i><span>${s}★</span></div>`;
  }).join("");
}

async function renderHome() {
  const grid = $("#home-grid");
  skeleton(grid);
  const me = await loadMe();
  if (!me) return route();
  $("#home-eyebrow").textContent = me.is_demo ? `Demo account · Goodreads reader #${me.dataset_user_id}` : "Your recommendations";
  $("#home-title").textContent = `${greeting()}, ${me.name}`;
  renderProfile(me);
  $("#home-empty").hidden = me.is_demo || me.profile.n_ratings > 0;
  const load = async () => {
    const gs = state.homeGenres;
    $("#home-recs-title").textContent = gs.length ? `Recommended for you in ${gs.join(" + ")}` : "Recommended for you";
    const ms = await loadMyRecs(grid, state.homeControls, 24, gs);
    $("#home-note").textContent = `scored all ${state.meta.dataset.n_books.toLocaleString()} books in ${ms} ms`;
  };
  state.homeControls = makeControls($("#home-controls"), load);
  genreChips($("#home-genres"), state.homeGenres, (gs) => { state.homeGenres = gs; load(); }, { multi: true });
  load();
}

/* ---------------- my books ---------------- */
function renderRated() {
  const books = Object.values(state.myBooks)
    .map((b) => ({ ...b, user_rating: state.myRatings[b.id] }))
    .sort((a, b) => b.user_rating - a.user_rating);
  $("#lib-count").textContent = books.length;
  renderGrid($("#lib-rated"), books, { userRating: true, empty: "You haven't rated any books yet. Start below!" });
}

async function renderLibrary() {
  const me = await loadMe();
  if (!me) return route();
  $("#lib-lede").textContent = me.is_demo
    ? `${me.name}'s ratings from the Goodreads dataset, plus any you add here. Click a book to change its rating.`
    : "Everything you have rated. Click a book to change your rating.";
  renderRated();
  $("#lib-toread-wrap").hidden = !me.profile.to_read.length;
  $("#lib-toread").innerHTML = me.profile.to_read.map((b) => bookCard(b)).join("");
  remember(me.profile.to_read);
  const rater = mountRater($("#lib-rater"), renderRated);
  genreChips($("#lib-genres"), me.genres, async (gs) => { await setMyGenres(gs); rater.reload(); }, { multi: true, all: false });
}

// After rating a book in the details popup, refresh the page behind it.
const refreshView = debounce(() => {
  if (state.view === "home") renderHome();
  else if (state.view === "library") renderLibrary();
  else if (state.view === "onboarding") renderOnboarding();
}, 300);

/* ---------------- theme & boot ---------------- */
function setupTheme() {
  const saved = store.get("plotline.theme", null);
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme-toggle").onclick = () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("plotline.theme", next);
  };
}

(async function boot() {
  setupTheme();
  setupSearch();
  setupAuthForms();
  [state.meta, state.genres] = await Promise.all([api("/api/stats"), api("/api/genres")]);
  await loadMe();
  renderNav();
  route();
})();

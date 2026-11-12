"use strict";
const $ = (s) => document.querySelector(s);
const STATUS = { nouvelle: "Nouvelle", a_postuler: "À postuler", postulee: "Postulée",
  entretien: "Entretien", refus: "Refus", offre: "Offre reçue", ecartee: "Écartée" };
let offers = [], current = null, settings = {};

// ---------- utilitaires ----------
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({ error: "Réponse illisible du serveur." }));
  if (res.status === 401) { location.href = "/login"; throw new Error("Connexion requise."); }
  if (!res.ok) throw new Error(data.error || "Erreur");
  return data;
}
function toast(msg, ms = 4000, error = false) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false; t.classList.toggle("error", error);
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), ms);
}
async function busy(btn, label, fn) {
  const old = btn.textContent; btn.disabled = true; btn.textContent = label; btn.classList.add("busy");
  try { return await fn(); } catch (e) { toast(e.message, 7000, true); }
  finally { btn.disabled = false; btn.textContent = old; btn.classList.remove("busy"); }
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Rendu Markdown minimal ; le texte est échappé avant toute mise en forme.
function md(src) {
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*(.+?)\*/g, "<em>$1</em>");
  let html = "", list = false, para = [];
  const flush = () => { if (para.length) { html += `<p>${para.join("<br>")}</p>`; para = []; } };
  const endList = () => { if (list) { html += "</ul>"; list = false; } };
  for (const line of String(src || "").split("\n")) {
    const h = line.match(/^(#{1,3})\s+(.*)/), li = line.match(/^\s*[-*•]\s+(.*)/);
    if (h) { flush(); endList(); html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; }
    else if (li) { flush(); if (!list) { html += "<ul>"; list = true; } html += `<li>${inline(li[1])}</li>`; }
    else if (!line.trim() || /^-{3,}$/.test(line.trim())) { flush(); endList(); }
    else { endList(); para.push(inline(line)); }
  }
  flush(); endList(); return html;
}
function safeUrl(u) { return /^https?:\/\//i.test(u || "") ? u : ""; }
function daysSince(iso) { return iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 864e5) : 0; }

// ---------- onglets et réglages ----------
document.querySelectorAll("nav button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
  for (const t of ["offers", "profile", "config"]) $("#tab-" + t).hidden = t !== b.dataset.tab;
  history.replaceState(null, "", "#" + b.dataset.tab);
});
async function loadSettings() {
  const s = settings = await api("/api/settings");
  for (const [k, v] of Object.entries(s)) { const el = document.getElementById(k); if (el) el.value = v; }
  for (const k of s._secrets_set || []) { const el = document.getElementById(k); if (el) el.placeholder = "•••• déjà défini (laisser vide pour garder)"; }
  for (const k of s._from_env || []) { const el = document.getElementById(k); if (el) { el.disabled = true; el.title = "Défini par une variable d'environnement"; } }
  $("#logout").hidden = !s._auth;
  renderGuide();
}
// Aide de démarrage : visible tant que le profil n'est pas rempli ou qu'aucune annonce n'existe.
function renderGuide() {
  const hasProfile = !!(settings.cv_master || settings.keywords);
  $("#g-profile").classList.toggle("done", hasProfile);
  $("#g-search").classList.toggle("done", offers.length > 0);
  $("#g-score").classList.toggle("done", offers.some((o) => o.score != null));
  $("#guide").hidden = hasProfile && offers.length > 0;
}
$("#g-go").onclick = () => document.querySelector('nav button[data-tab="profile"]').click();
$("#btn-restore").onclick = () => $("#restore-file").click();
$("#restore-file").onchange = async (e) => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file || !confirm("Remplacer toutes les données actuelles par cette sauvegarde ?")) return;
  try {
    const r = await api("/api/import", JSON.parse(await file.text()));
    toast(`Sauvegarde restaurée : ${r.offers} annonce(s).`); await loadSettings(); await loadOffers();
  } catch (er) { toast("Restauration impossible : " + er.message, 7000, true); }
};
document.querySelectorAll("button.save").forEach((b) => b.onclick = () => busy(b, "Enregistrement…", async () => {
  const body = {}; for (const k of b.dataset.keys.split(",")) { const el = document.getElementById(k); if (!el.disabled) body[k] = el.value.trim(); }
  await api("/api/settings", body); toast("Enregistré.");
}));

// ---------- liste des annonces ----------
async function loadOffers() { offers = await api("/api/offers"); renderList(); renderGuide(); }
function renderList() {
  const f = $("#filter").value, ul = $("#offer-list"); ul.innerHTML = "";
  const q = $("#search").value.trim().toLowerCase();
  const shown = offers.filter((o) => f === "all" ? true
    : f === "active" ? !["ecartee", "refus"].includes(o.status) : o.status === f)
    .filter((o) => !q || [o.title, o.company, o.location].join(" ").toLowerCase().includes(q))
    .map((o, i) => [o, i]).sort((a, b) => (b[0].score ?? -1) - (a[0].score ?? -1) || a[1] - b[1]).map((x) => x[0]);
  for (const opt of $("#filter").options) {
    const n = offers.filter((o) => opt.value === "all" ? true
      : opt.value === "active" ? !["ecartee", "refus"].includes(o.status) : o.status === opt.value).length;
    opt.textContent = opt.textContent.replace(/ \(\d+\)$/, "") + ` (${n})`;
  }
  if (!shown.length) ul.innerHTML = `<li style="display:block;cursor:default"><div class="empty-ill" aria-hidden="true">${offers.length ? "🔎" : "📭"}</div>${q
    ? "Aucune annonce ne correspond à ta recherche."
    : offers.length
    ? "Aucune annonce dans ce filtre. Choisis « Toutes » pour tout voir."
    : "Aucune annonce pour l'instant. Renseigne ton profil, puis clique sur « Chercher des annonces »."}</li>`;
  for (const o of shown) {
    const li = document.createElement("li");
    if (current && current.id === o.id) li.className = "sel";
    const cls = o.score == null ? "" : o.score >= 80 ? "hi" : o.score >= 50 ? "mid" : "lo";
    const relance = o.status === "postulee" && daysSince(o.applied_at) >= 7;
    li.innerHTML = `<div class="score ${cls}">${o.score ?? "?"}</div><div>
      <div class="t">${esc(o.title)}</div>
      <div class="m">${esc([o.company, o.location].filter(Boolean).join(" · "))}</div>
      <div><span class="tag">${esc(STATUS[o.status] || o.status)}</span><span class="tag">${esc(o.source)}</span>
      ${relance ? '<span class="tag alert">À relancer</span>' : ""}</div></div>`;
    li.tabIndex = 0; li.setAttribute("role", "button");
    li.onclick = () => openOffer(o.id);
    li.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openOffer(o.id); } };
    ul.appendChild(li);
  }
}
$("#filter").onchange = renderList;
$("#search").oninput = renderList;
$("#btn-collect").onclick = (e) => busy(e.target, "Recherche en cours…", async () => {
  const r = await api("/api/collect", {});
  toast(Object.entries(r).map(([k, v]) => `${SOURCE_NAMES[k]} : ${v.error ? v.error : v.new + " nouvelle(s)"}`).join(" — "), 9000);
  await loadOffers();
});
$("#btn-score").onclick = (e) => busy(e.target, "Notation en cours…", async () => {
  const r = await api("/api/score", {});
  toast(`${r.scored} annonce(s) notée(s).` + (r.left ? ` Il en reste ${r.left} : reclique pour continuer.` : ""), 7000);
  await loadOffers();
});

// ---------- import de CV : profil automatique, puis recherche et notation ----------
const SOURCE_NAMES = { france_travail: "France Travail", adzuna: "Adzuna", mail: "Alertes mail" };
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = () => reject(new Error("Lecture impossible : " + file.name));
    r.readAsDataURL(file);
  });
}
$("#btn-import").onclick = async (e) => {
  const btn = e.target, files = [...$("#cv-files").files], box = $("#import-result");
  if (!files.length) return toast("Choisis d'abord un ou plusieurs CV.");
  if (files.length > 10) return toast("10 fichiers au maximum.");
  if (files.reduce((n, f) => n + f.size, 0) > 20e6) return toast("Fichiers trop lourds (20 Mo au total).");
  const old = btn.textContent; btn.disabled = true; btn.classList.add("busy");
  const step = (t) => (btn.textContent = t);
  try {
    step("1/3 Lecture des CV et définition du profil…");
    const payload = [];
    for (const f of files) payload.push({ name: f.name, data: await fileToBase64(f) });
    const p = await api("/api/profile/import", { files: payload });
    await loadSettings();
    let html = `<h3>Profil défini à partir de ${p.files.length} CV</h3><p>${esc(p.resume)}</p>
      <p><strong>Postes recherchés :</strong> ${esc(p.keywords)}</p>`;
    if (p.a_verifier.length) html += `<p><strong>À vérifier dans tes CV :</strong></p><ul>${p.a_verifier.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    box.innerHTML = html; box.hidden = false;
    step("2/3 Recherche des offres…");
    const c = await api("/api/collect", {});
    const found = Object.values(c).reduce((n, v) => n + (v.new || 0), 0);
    const errors = Object.entries(c).filter(([, v]) => v.error).map(([k, v]) => `${SOURCE_NAMES[k]} : ${v.error}`);
    if (!found) {
      box.innerHTML += `<p><strong>Aucune nouvelle offre trouvée.</strong> ${errors.length ? "Sources à configurer dans Réglages :" : "Élargis les postes ou le lieu ci-dessous, puis relance la recherche depuis l'onglet Annonces."}</p>
        ${errors.length ? `<ul>${errors.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}`;
      return;
    }
    step(`3/3 Notation de ${found} offre(s)…`);
    const r = await api("/api/score", {});
    await loadOffers();
    toast(`${found} offre(s) trouvée(s), ${r.scored} notée(s).` + (r.left ? ` Il en reste ${r.left} à noter.` : ""), 9000);
    document.querySelector('nav button[data-tab="offers"]').click();
  } catch (er) { toast(er.message, 9000, true); }
  finally { btn.disabled = false; btn.textContent = old; btn.classList.remove("busy"); }
};
$("#btn-manual").onclick = () => ($("#manual-box").hidden = !$("#manual-box").hidden);
$("#manual-add").onclick = (e) => busy(e.target, "Analyse…", async () => {
  const r = await api("/api/offers/manual", { text: $("#manual-text").value });
  toast(r.new ? "Annonce ajoutée." : "Cette annonce existe déjà.");
  $("#manual-text").value = ""; $("#manual-box").hidden = true; await loadOffers();
});

// ---------- fiche d'une annonce ----------
async function openOffer(id) {
  try { current = await api("/api/offers/" + id); } catch (e) { return toast(e.message); }
  renderList(); renderDetail();
  if (window.innerWidth < 800) $("#detail").scrollIntoView({ behavior: "smooth" });
}
function docBlock(title, key, text) {
  return `<h2>${title}</h2><div class="doc md" id="doc-${key}">${md(text)}</div>
    <div class="toolbar"><button data-print="${key}">Imprimer / PDF</button>
    <button data-copy="${key}">Copier</button><button data-edit="${key}">Modifier</button></div>`;
}
function renderDetail() {
  const o = current, d = $("#detail"); d.hidden = false;
  const url = safeUrl(o.url);
  d.innerHTML = `
    <h2>${esc(o.title)}</h2>
    <p class="hint">${esc([o.company, o.location, o.contract, o.salary].filter(Boolean).join(" · "))}
      ${url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Voir l'annonce</a>` : ""}</p>
    ${o.score != null ? `<p><strong>Score ${o.score}/100.</strong> ${esc(o.score_reason)}</p>` : ""}
    <div class="toolbar">
      <select id="d-status">${Object.entries(STATUS).map(([k, v]) =>
        `<option value="${k}" ${k === o.status ? "selected" : ""}>${v}</option>`).join("")}</select>
      ${o.applied_at ? `<span class="hint">Postulée le ${esc(o.applied_at)} (J+${daysSince(o.applied_at)})</span>` : ""}
    </div>
    <div class="toolbar">
      <button id="d-gen" class="primary">${o.cv_md ? "Régénérer" : "Générer"} CV + lettre</button>
      <button id="d-prep">${o.prep_md ? "Régénérer la" : "Créer la"} fiche d'entretien</button>
      <button id="d-iv">🎙 Simuler l'entretien</button>
      <button id="d-del">Supprimer</button>
    </div>
    <div class="desc">${esc(o.description)}</div>
    <label>Notes personnelles<textarea id="d-notes" rows="3">${esc(o.notes)}</textarea></label>
    ${o.cv_md ? docBlock("CV adapté", "cv_md", o.cv_md) : ""}
    ${o.letter_md ? docBlock("Lettre de motivation", "letter_md", o.letter_md) : ""}
    ${o.prep_md ? `<h2>Fiche d'entretien</h2><div class="doc md">${md(o.prep_md)}</div>` : ""}
    ${(o.interviews || []).map((i) => `<h2>Retour du coach — ${esc(i.created_at)}</h2><div class="doc md">${md(i.feedback_md)}</div>`).join("")}`;
  const upd = (body) => api(`/api/offers/${o.id}/update`, body);
  $("#d-status").onchange = async (e) => { await upd({ status: e.target.value }); await loadOffers(); openOffer(o.id); };
  $("#d-notes").onchange = (e) => upd({ notes: e.target.value }).then(() => toast("Notes enregistrées.", 2000)).catch((er) => toast(er.message));
  $("#d-gen").onclick = (e) => busy(e.target, "Rédaction (1 à 2 min)…", async () => { await api(`/api/offers/${o.id}/generate`, {}); await openOffer(o.id); });
  $("#d-prep").onclick = (e) => busy(e.target, "Préparation…", async () => { await api(`/api/offers/${o.id}/prep`, {}); await openOffer(o.id); });
  $("#d-iv").onclick = (e) => busy(e.target, "Préparation des questions…", () => startInterview(o));
  $("#d-del").onclick = async () => {
    if (!confirm("Supprimer cette annonce et ses documents ?")) return;
    await api(`/api/offers/${o.id}/delete`, {}); current = null; d.hidden = true; loadOffers();
  };
  d.querySelectorAll("[data-print]").forEach((b) => b.onclick = () => {
    const el = $("#doc-" + b.dataset.print); el.classList.add("printing"); window.print(); el.classList.remove("printing");
  });
  d.querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => {
    try { await navigator.clipboard.writeText(o[b.dataset.copy]); toast("Copié."); } catch { toast("Copie impossible."); }
  });
  d.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => {
    const key = b.dataset.edit, el = $("#doc-" + key);
    if (b.textContent === "Modifier") {
      el.innerHTML = `<textarea rows="20">${esc(o[key])}</textarea>`; b.textContent = "Enregistrer";
    } else busy(b, "…", async () => { await upd({ [key]: el.querySelector("textarea").value }); await openOffer(o.id); });
  });
}

// ---------- simulation vocale ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const iv = { id: null, questions: [], answers: [], i: 0, rec: null, listening: false, base: "" };
function speak(text, then) {
  if (!window.speechSynthesis) return then && then();
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text); u.lang = "fr-FR"; u.rate = 1;
  const v = speechSynthesis.getVoices().find((x) => x.lang.startsWith("fr")); if (v) u.voice = v;
  if (then) u.onend = then;
  speechSynthesis.speak(u);
}
function stopListening() {
  iv.listening = false; if (iv.rec) { try { iv.rec.stop(); } catch {} }
  $("#iv-mic").classList.remove("rec"); $("#iv-mic").textContent = "🎙 Parler";
}
function startListening() {
  if (!SR) return ($("#iv-status").textContent = "Reconnaissance vocale indisponible dans ce navigateur (utilise Chrome ou Edge). Tu peux taper ta réponse.");
  if (window.speechSynthesis) speechSynthesis.cancel();
  const rec = (iv.rec = new SR()); rec.lang = "fr-FR"; rec.continuous = true; rec.interimResults = true;
  iv.base = $("#iv-answer").value ? $("#iv-answer").value.trim() + " " : "";
  rec.onresult = (e) => {
    let fin = "", tmp = "";
    for (const r of e.results) (r.isFinal ? (fin += r[0].transcript + " ") : (tmp += r[0].transcript));
    $("#iv-answer").value = iv.base + fin + tmp;
  };
  rec.onerror = (e) => { if (e.error !== "no-speech" && e.error !== "aborted") { $("#iv-status").textContent = "Micro : " + e.error; stopListening(); } };
  rec.onend = () => { if (iv.listening) { iv.base = $("#iv-answer").value.trim() + " "; try { rec.start(); } catch {} } };
  iv.listening = true; rec.start();
  $("#iv-mic").classList.add("rec"); $("#iv-mic").textContent = "⏹ Arrêter";
  $("#iv-status").textContent = "Je t'écoute…";
}
function showQuestion() {
  const q = iv.questions[iv.i];
  $("#iv-progress").textContent = `Question ${iv.i + 1} sur ${iv.questions.length}`;
  $("#iv-question").textContent = q; $("#iv-answer").value = ""; $("#iv-status").textContent = "";
  $("#iv-next").textContent = iv.i === iv.questions.length - 1 ? "Terminer et voir le retour" : "Question suivante";
  speak(q, () => { if (SR && !$("#interview").hidden) startListening(); });
}
async function startInterview(o) {
  const r = await api(`/api/offers/${o.id}/interview`, {});
  Object.assign(iv, { id: r.interview_id, questions: r.questions, answers: [], i: 0 });
  $("#iv-title").textContent = "Entretien — " + o.title;
  $("#iv-feedback").innerHTML = "";
  for (const id of ["iv-question", "iv-answer", "iv-mic", "iv-repeat", "iv-next", "iv-progress"]) $("#" + id).hidden = false;
  $("#interview").hidden = false; showQuestion();
}
$("#iv-mic").onclick = () => (iv.listening ? (stopListening(), ($("#iv-status").textContent = "")) : startListening());
$("#iv-repeat").onclick = () => { stopListening(); speak(iv.questions[iv.i], () => SR && startListening()); };
$("#iv-next").onclick = (e) => {
  stopListening(); iv.answers[iv.i] = $("#iv-answer").value.trim();
  if (iv.i < iv.questions.length - 1) { iv.i++; return showQuestion(); }
  if (window.speechSynthesis) speechSynthesis.cancel();
  busy(e.target, "Analyse de l'entretien (1 à 2 min)…", async () => {
    const r = await api(`/api/interviews/${iv.id}/feedback`, { answers: iv.answers });
    for (const id of ["iv-question", "iv-answer", "iv-mic", "iv-repeat", "iv-next", "iv-progress"]) $("#" + id).hidden = true;
    $("#iv-status").textContent = ""; $("#iv-feedback").innerHTML = md(r.feedback_md);
  });
};
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#interview").hidden) $("#iv-close").click(); });
$("#iv-close").onclick = () => {
  stopListening(); if (window.speechSynthesis) speechSynthesis.cancel();
  $("#interview").hidden = true; if (current) openOffer(current.id);
};

{ const b = document.querySelector(`nav button[data-tab="${location.hash.slice(1)}"]`); if (b) b.click(); }
loadSettings().then(loadOffers).catch((e) => toast(e.message));

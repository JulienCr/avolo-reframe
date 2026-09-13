"""Build the standalone review page for the 23 before/after cut pairs.

Reads `windows_ac.json` and the paired JPEGs next to this script, embeds
every image as a base64 `data:` URI, and writes one self-contained HTML
file a human uses to label each pair `coupe` / `pas une coupe` / `je ne
sais pas`. Run from anywhere: `python3 build_review_page.py <out.html>`.
"""
import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_PATH = HERE / "windows_ac.json"
IMAGES_DIR = HERE / "images"

CENTER_JUMP_THRESHOLD_PX = 150
SCALE_JUMP_THRESHOLD_LOG = 0.28

WINDOW_A = ("A", 165_000, 205_000, "165 s à 205 s", "Interview calme à deux")
WINDOW_C = ("C", 615_000, 655_000, "615 s à 655 s",
            "Alternance plan serré 1 sujet et plan large 3 sujets")

SIGNAL_LABELS = {
    "center_jump": "saut de centre",
    "scale_jump": "saut d'échelle",
    "ndet_change_persistent": "changement persistant du nombre de corps",
    "union_appear": "apparition dans l'union",
    "union_disappear": "disparition de l'union",
}

CONFIDENCE_LABELS = {"haute": "haute", "moyenne": "moyenne"}


def load_entries():
    """Load and fail loudly if the JSON or a referenced image is absent."""
    if not DATA_PATH.exists():
        sys.exit(f"données introuvables : {DATA_PATH}")
    entries = json.loads(DATA_PATH.read_text())
    for entry in entries:
        for suffix in ("before", "after"):
            path = image_path(entry, suffix)
            if not path.exists():
                sys.exit(f"image manquante : {path}")
    return entries


def image_path(entry, suffix):
    return IMAGES_DIR / f"cut_{round(entry['pts_ms'])}_{suffix}.jpg"


def image_data_uri(entry, suffix):
    raw = image_path(entry, suffix).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def seconds(ms, decimals=1):
    return f"{ms / 1000:.{decimals}f}".replace(".", ",")


def window_of(entry):
    for code, lo, hi, *_ in (WINDOW_A, WINDOW_C):
        if lo <= entry["pts_ms"] <= hi:
            return code
    sys.exit(f"pts_ms {entry['pts_ms']} hors des deux fenêtres A/C")


def signal_html(entry):
    labels = [SIGNAL_LABELS.get(s, s) for s in entry["signals"]]
    return ", ".join(labels) if labels else "aucun"


def why_html(entry):
    confidence = CONFIDENCE_LABELS.get(entry.get("confidence"), "non renseignée")
    return f"""<p><b>Signaux</b> : {signal_html(entry)}</p>
      <p><b>Saut de centre</b> : {entry['center_jump_px']:.1f} px (seuil {CENTER_JUMP_THRESHOLD_PX} px)</p>
      <p><b>Saut d'échelle</b> : {entry['scale_jump_log']:.4f} (seuil {SCALE_JUMP_THRESHOLD_LOG})</p>
      <p><b>Corps détectés</b> : {entry['ndet_before']} avant → {entry['ndet_after']} après</p>
      <p><b>Score</b> : {entry['score']:.3f} — <b>confiance</b> : {confidence}</p>"""


def card_html(entry, index, total):
    round_pts = round(entry["pts_ms"])
    window = window_of(entry)
    title = f"{seconds(entry['pts_ms'])} s"
    before_uri = image_data_uri(entry, "before")
    after_uri = image_data_uri(entry, "after")
    before_label = f"avant — {seconds(entry['before_ms'])} s"
    after_label = f"après — {seconds(entry['after_ms'])} s"
    return f"""<section class="q" data-q="{index}" data-pts="{round_pts}" data-window="{window}">
  <div class="q-head">
    <div class="q-num">Paire {index} / {total} — fenêtre {window}</div>
    <h3 class="q-title">{title}</h3>
  </div>

  <div class="pair">
    <figure class="pair-fig">
      <img src="{before_uri}" width="640" alt="{before_label}">
      <figcaption>{before_label}</figcaption>
    </figure>
    <figure class="pair-fig">
      <img src="{after_uri}" width="640" alt="{after_label}">
      <figcaption>{after_label}</figcaption>
    </figure>
  </div>

  <div class="opts">
    <label class="opt">
      <input type="radio" name="q{index}" value="cut">
      <span><span class="opt-t">Coupe</span></span>
    </label>
    <label class="opt">
      <input type="radio" name="q{index}" value="no_cut">
      <span><span class="opt-t">Pas une coupe</span></span>
    </label>
    <label class="opt">
      <input type="radio" name="q{index}" value="unsure">
      <span><span class="opt-t">Je ne sais pas</span></span>
    </label>
  </div>

  <details>
    <summary>Ce qui a déclenché le signalement</summary>
    <div class="why">
      {why_html(entry)}
    </div>
  </details>
</section>"""


def window_heading_html(code, span, description, count):
    label = f"Fenêtre {code} — {span}"
    return f"""<div class="window-heading">
  <h2>{label}</h2>
  <p>{description} — {count} paires</p>
</div>"""


def build_cards(entries):
    """Split entries by window, sort each chronologically, render both groups."""
    by_window = {"A": [], "C": []}
    for entry in entries:
        by_window[window_of(entry)].append(entry)
    for group in by_window.values():
        group.sort(key=lambda e: e["pts_ms"])

    total = len(entries)
    blocks = []
    index = 1
    for code, _, _, span, description in (WINDOW_A, WINDOW_C):
        group = by_window[code]
        blocks.append(window_heading_html(code, span, description, len(group)))
        for entry in group:
            blocks.append(card_html(entry, index, total))
            index += 1
    return "\n\n".join(blocks)


PAGE_CSS = """
/* Frozen decision-sheet stylesheet. Paste this whole file, verbatim, inside a single
   <style> element in the artifact HTML — see reference/skeleton.html. Do not edit. */
:root{
  --paper:#E8ECF0; --card:#FFFFFF; --card-2:#F3F6F8; --sunk:#DDE3E9;
  --ink:#12181E; --ink-2:#48535D; --ink-3:#79858E;
  --line:#D2D9E0; --line-2:#B2BCC5;
  --accent:#8F5405; --accent-line:#9A6208; --accent-soft:#FAF0DA; --sig:#9A6208; --on-accent:#FFFFFF;
  --ok:#1C6B47;
  --shadow:0 1px 2px rgba(18,24,30,.05), 0 10px 28px -16px rgba(18,24,30,.28);
  --measure:44rem; --wide:53rem;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#0D1216; --card:#161D23; --card-2:#1C242B; --sunk:#0A0F13;
    --ink:#E7EDF2; --ink-2:#A2AFB9; --ink-3:#77838C;
    --line:#28323A; --line-2:#3B4952;
    --accent:#F0B429; --accent-line:#8A6415; --accent-soft:#241C0E; --sig:#E8AE33; --on-accent:#12181E;
    --ok:#54C18C;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 12px 30px -18px rgba(0,0,0,.8);
  }
}
:root[data-theme="dark"]{
  --paper:#0D1216; --card:#161D23; --card-2:#1C242B; --sunk:#0A0F13;
  --ink:#E7EDF2; --ink-2:#A2AFB9; --ink-3:#77838C;
  --line:#28323A; --line-2:#3B4952;
  --accent:#F0B429; --accent-line:#8A6415; --accent-soft:#241C0E; --sig:#E8AE33; --on-accent:#12181E;
  --ok:#54C18C;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 12px 30px -18px rgba(0,0,0,.8);
}

*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:"Source Serif 4",Georgia,serif; font-size:17px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:var(--wide); margin:0 auto; padding:0 20px 96px}
.col{max-width:var(--wide); margin-inline:auto}

/* ---------- progress bar ---------- */
.bar{
  position:sticky; top:0; z-index:20; background:var(--paper);
  border-bottom:1px solid var(--line);
}
.bar-in{
  max-width:var(--wide); margin:0 auto; padding:10px 20px;
  display:flex; align-items:center; gap:14px;
}
.bar-label{
  font-family:Archivo,system-ui,sans-serif; font-size:11.5px; font-weight:600;
  letter-spacing:.10em; text-transform:uppercase; color:var(--ink-2);
  white-space:nowrap; font-variant-numeric:tabular-nums;
}
.track{flex:1; height:4px; background:var(--sunk); border-radius:2px; overflow:hidden}
.fill{height:100%; width:0%; background:var(--accent); border-radius:2px; transition:width .25s ease}

/* ---------- header ---------- */
header{padding:52px 0 8px}
.eyebrow{
  font-family:Archivo,system-ui,sans-serif; font-size:11.5px; font-weight:600;
  letter-spacing:.14em; text-transform:uppercase; color:var(--accent); margin:0 0 14px;
}
h1{
  font-family:Archivo,system-ui,sans-serif; font-weight:700; font-size:clamp(2rem,5.5vw,2.9rem);
  line-height:1.06; letter-spacing:-.022em; margin:0 0 16px; text-wrap:balance;
}
.lede{font-size:1.08rem; color:var(--ink-2); margin:0; max-width:36rem}

/* ---------- settled band ---------- */
.settled{
  margin:36px 0 8px; padding:18px 20px; background:var(--card-2);
  border:1px solid var(--line); border-radius:10px;
}
.settled h2{
  font-family:Archivo,system-ui,sans-serif; font-size:11.5px; font-weight:600;
  letter-spacing:.12em; text-transform:uppercase; color:var(--ink-3); margin:0 0 12px;
}
.settled ul{margin:0; padding:0; list-style:none; display:grid; gap:8px}
.settled li{display:flex; gap:10px; font-size:.95rem; color:var(--ink-2); line-height:1.45}
.settled b{color:var(--ink); font-weight:600}
.tick{color:var(--ok); flex:none; font-family:Archivo,sans-serif; font-weight:700}

/* ---------- window headings ---------- */
.window-heading{margin:44px 0 0; padding-bottom:10px; border-bottom:1px solid var(--line-2)}
.window-heading h2{
  font-family:Archivo,system-ui,sans-serif; font-weight:700; font-size:1.3rem;
  letter-spacing:-.012em; margin:0 0 4px;
}
.window-heading p{margin:0; color:var(--ink-2); font-size:.95rem}

/* ---------- question cards ---------- */
.q{
  margin:22px 0 0; background:var(--card); border:1px solid var(--line);
  border-radius:14px; box-shadow:var(--shadow); overflow:hidden;
}
.q-head{padding:24px 26px 4px}
.q-num{
  font-family:"JetBrains Mono",ui-monospace,monospace; font-size:12px; font-weight:500;
  color:var(--accent); letter-spacing:.04em;
}
.q-title{
  font-family:Archivo,system-ui,sans-serif; font-weight:700; font-size:1.42rem;
  letter-spacing:-.014em; line-height:1.18; margin:6px 0 10px; text-wrap:balance;
  font-variant-numeric:tabular-nums;
}

/* ---------- image pairs ---------- */
.pair{
  display:grid; grid-template-columns:1fr 1fr; gap:16px;
  margin:16px 26px 0;
}
@media (max-width:640px){ .pair{grid-template-columns:1fr} }
.pair-fig{margin:0; padding:0}
.pair-fig img{display:block; width:100%; height:auto; border-radius:8px; border:1px solid var(--line)}
.pair-fig figcaption{
  margin-top:8px; font-size:.86rem; color:var(--ink-3); line-height:1.4;
  font-family:Archivo,system-ui,sans-serif; font-variant-numeric:tabular-nums;
}

.opts{padding:20px 26px 4px; display:grid; gap:10px; grid-template-columns:repeat(3,1fr)}
@media (max-width:640px){ .opts{grid-template-columns:1fr} }
.opt{
  display:grid; grid-template-columns:auto 1fr; gap:14px; align-items:start;
  padding:14px 16px; border:1px solid var(--line); border-radius:10px;
  cursor:pointer; background:var(--card);
}
.opt:hover{border-color:var(--line-2)}
.opt input{
  appearance:none; -webkit-appearance:none; margin:3px 0 0; width:18px; height:18px;
  border:1.5px solid var(--line-2); border-radius:50%; flex:none; cursor:pointer;
  display:grid; place-content:center; background:var(--card);
}
.opt input::after{content:""; width:9px; height:9px; border-radius:50%; transform:scale(0); background:var(--accent)}
.opt input:checked::after{transform:scale(1)}
.opt input:checked{border-color:var(--accent)}
.opt input:focus-visible{outline:2px solid var(--accent); outline-offset:3px}
.opt:has(input:checked), .opt.is-on{border-color:var(--accent); background:var(--accent-soft)}
.opt-t{
  font-family:Archivo,system-ui,sans-serif; font-weight:600; font-size:1rem;
  display:flex; flex-wrap:wrap; align-items:center; gap:8px; line-height:1.3;
}

details{margin:14px 26px 22px; border-top:1px solid var(--line); padding-top:12px}
summary{
  cursor:pointer; font-family:Archivo,sans-serif; font-size:12px; font-weight:600;
  letter-spacing:.07em; text-transform:uppercase; color:var(--ink-3); list-style:none;
}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸ "; color:var(--accent)}
details[open] summary::before{content:"▾ "}
summary:focus-visible{outline:2px solid var(--accent); outline-offset:3px; border-radius:3px}
.why{margin:12px 0 0; font-size:.94rem; color:var(--ink-2); line-height:1.6}
.why p{margin:0 0 9px}
.why p:last-child{margin-bottom:0}
.why b{color:var(--ink); font-weight:600}

/* ---------- output ---------- */
.out{margin:44px 0 0; padding:26px; background:var(--card); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow)}
.out h2{font-family:Archivo,sans-serif; font-weight:700; font-size:1.3rem; margin:0 0 6px; letter-spacing:-.012em}
.out p.sub{margin:0 0 18px; color:var(--ink-2); font-size:.96rem}
pre{
  margin:0; padding:16px 18px; background:var(--sunk); border-radius:10px;
  font-family:"JetBrains Mono",ui-monospace,monospace; font-size:12.5px; line-height:1.65;
  color:var(--ink); overflow-x:auto; white-space:pre; border:1px solid var(--line);
}
.actions{display:flex; gap:12px; align-items:center; margin:18px 0 0; flex-wrap:wrap}
button{
  font-family:Archivo,sans-serif; font-size:14px; font-weight:600; letter-spacing:.01em;
  padding:11px 20px; border-radius:9px; border:1px solid transparent;
  background:var(--accent); color:var(--on-accent); cursor:pointer;
}
button:hover{filter:brightness(1.08)}
button:focus-visible{outline:2px solid var(--accent); outline-offset:3px}
button.ghost{background:transparent; color:var(--ink-2); border-color:var(--line-2)}
.status{font-family:Archivo,sans-serif; font-size:13px; color:var(--ok); font-weight:600}

footer{margin:40px 0 0; text-align:center; color:var(--ink-3); font-size:.86rem; font-family:Archivo,sans-serif}

@media (prefers-reduced-motion: reduce){ *{transition:none !important; animation:none !important} }
"""

PAGE_SCRIPT = """
(function () {
  'use strict';
  var KEY = 'avolo-reframe-cuts-verdicts';
  var VERDICT_LABELS = { cut: 'coupe', no_cut: 'pas une coupe', unsure: 'je ne sais pas' };
  var sections = Array.prototype.slice.call(document.querySelectorAll('.q'));
  var fill = document.getElementById('fill');
  var prog = document.getElementById('prog');
  var preview = document.getElementById('preview');
  var status = document.getElementById('status');
  var dbNs = null;

  function answerOf(n) {
    var el = document.querySelector('input[name="q' + n + '"]:checked');
    return el ? el.value : null;
  }

  function build() {
    var lines = ['Verdicts — bascules de plan', ''];
    sections.forEach(function (sec) {
      var n = sec.getAttribute('data-q');
      var pts = sec.getAttribute('data-pts');
      var win = sec.getAttribute('data-window');
      var a = answerOf(n);
      var label = a ? VERDICT_LABELS[a] : '[à trancher]';
      lines.push('Paire ' + n + ' (fenêtre ' + win + ', ' + pts + ' ms) → ' + label);
    });
    return lines.join('\\n');
  }

  function paint() {
    var done = sections.filter(function (s) { return answerOf(s.getAttribute('data-q')) !== null; }).length;
    prog.textContent = done + ' / ' + sections.length + ' paires tranchées';
    fill.style.width = (done / sections.length * 100) + '%';
    preview.textContent = build();
    document.querySelectorAll('.opt').forEach(function (l) {
      var i = l.querySelector('input');
      l.classList.toggle('is-on', !!(i && i.checked));
    });
  }

  function save() {
    var data = {};
    sections.forEach(function (s) {
      var q = s.getAttribute('data-q');
      data[s.getAttribute('data-pts')] = { verdict: answerOf(q), window: s.getAttribute('data-window') };
    });
    try { localStorage.setItem(KEY, JSON.stringify(data)); } catch (e) { /* storage unavailable */ }
  }

  function load() {
    var raw = null;
    try { raw = localStorage.getItem(KEY); } catch (e) { return; }
    if (!raw) return;
    var data;
    try { data = JSON.parse(raw); } catch (e) { return; }
    if (!data || typeof data !== 'object') return;
    sections.forEach(function (s) {
      var pts = s.getAttribute('data-pts');
      var entry = data[pts];
      if (entry && entry.verdict) {
        var hit = document.querySelector('input[name="q' + s.getAttribute('data-q') + '"][value="' + entry.verdict + '"]');
        if (hit) hit.checked = true;
      }
    });
  }

  // db writes never block the UI: a slow or refused call must not freeze a click.
  function writeVerdict(pts, verdict, win) {
    if (!dbNs) return;
    dbNs.collection('verdicts').doc(pts).set({
      verdict: verdict, window: win, pts_ms: Number(pts), updated_at: new Date().toISOString()
    }).catch(function () { /* db is best-effort; localStorage already has the verdict */ });
  }

  // Hydrate from db once it resolves, so a reload on the same device reflects
  // any verdict another session already wrote there.
  function hydrateFromDb() {
    if (!dbNs) return;
    dbNs.collection('verdicts').get().then(function (snap) {
      snap.docs.forEach(function (doc) {
        var data = doc.data();
        if (!data || !data.verdict) return;
        var sec = sections.filter(function (s) { return s.getAttribute('data-pts') === doc.id; })[0];
        if (!sec) return;
        var hit = document.querySelector('input[name="q' + sec.getAttribute('data-q') + '"][value="' + data.verdict + '"]');
        if (hit) hit.checked = true;
      });
      paint();
      save();
    }).catch(function () { /* keep localStorage state as-is */ });
  }

  document.addEventListener('change', function (e) {
    if (!e.target || e.target.type !== 'radio') return;
    var sec = e.target.closest('.q');
    paint();
    save();
    writeVerdict(sec.getAttribute('data-pts'), e.target.value, sec.getAttribute('data-window'));
  });

  document.getElementById('copy').addEventListener('click', function () {
    var text = build();
    function ok() {
      status.textContent = 'Copié.';
      setTimeout(function () { status.textContent = ''; }, 2600);
    }
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); ok(); }
      catch (err) { status.textContent = 'Copie refusée par le navigateur.'; }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok, fallback);
    } else { fallback(); }
  });

  load();
  paint();

  if (window.claude && window.claude.use) {
    window.claude.use('db').then(function (ns) { dbNs = ns; if (ns) hydrateFromDb(); });
  }
})();
"""


def render_page(entries):
    total = len(entries)
    cards = build_cards(entries)
    return f"""<title>Bascules de plan</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=JetBrains+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">

<style>{PAGE_CSS}</style>

<div class="bar">
  <div class="bar-in">
    <span class="bar-label" id="prog">0 / {total} paires tranchées</span>
    <span class="track"><span class="fill" id="fill"></span></span>
  </div>
</div>

<div class="wrap">
<div class="col">

<header>
  <p class="eyebrow">Corpus de contrôle — bascules de plan</p>
  <h1>Coupe ou faux positif ?</h1>
  <p class="lede">Pour chaque paire, l'image d'avant et celle d'après la bascule signalée. Une vraie coupe change de caméra ; un faux positif garde le même plan malgré un saut de la boîte de détection.</p>
</header>

{cards}

<section class="out">
  <h2>Recopie de secours</h2>
  <p class="sub">Si la sauvegarde automatique échoue, copiez les {total} verdicts ici et collez-les ailleurs.</p>
  <pre id="preview"></pre>
  <div class="actions">
    <button type="button" id="copy">Copier les verdicts</button>
    <span class="status" id="status" role="status" aria-live="polite"></span>
  </div>
</section>

<footer>tests/corpus/cuts/windows_ac.json — 23 paires, fenêtres A et C</footer>

</div>
</div>

<script>{PAGE_SCRIPT}</script>
"""


def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "review_page.html"
    entries = load_entries()
    if len(entries) != 23:
        sys.exit(f"attendu 23 entrées, trouvé {len(entries)}")
    html = render_page(entries)
    out_path.write_text(html)
    print(f"écrit : {out_path} ({out_path.stat().st_size} octets)")


if __name__ == "__main__":
    main()

path = "/Users/julien.cruau/dev2/avolo-reframe/scripts/probe.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

replacements = [
    ('print(_row("rpcVersion negocie", version["rpcVersion"]))',
     'print(_row("rpcVersion negocie", version["rpcVersion"]))'),  # kept: rpcVersion is an identifier, leave as-is
    ('print(_row("Requetes disponibles", len(requests)))',
     'print(_row("Requetes disponibles", len(requests)))'),
    ('print(_row("Go/no-go n.1 -- OBS >= 32.1", "GO" if version_ok else "NO-GO"))',
     'print(_row("Go/no-go n°1 -- OBS >= 32.1", "GO" if version_ok else "NO-GO"))'),
    ('print("Aitum Vertical : version non verifiable via obs-websocket, verifier manuellement dans OBS.")',
     'print("Aitum Vertical : version non vérifiable via obs-websocket, vérifier manuellement dans OBS.")'),
    ('print("Note : obs-websocket 5.7.4 annonce un support \\"partial\\" des canevas -- lire le dump ci-dessus.")',
     'print("Note : obs-websocket 5.7.4 annonce un support « partial » des canevas -- lire le dump ci-dessus.")'),
    ('print(_row("Go/no-go n.2 -- GetCanvasList exploitable", "GO" if ok else "NO-GO"))',
     'print(_row("Go/no-go n°2 -- GetCanvasList exploitable", "GO" if ok else "NO-GO"))'),
    ('print("\\n=== 3. Cameras et Center Stage ===")',
     'print("\\n=== 3. Caméras et Center Stage ===")'),
    ('print(_row("Center Stage (preference globale)", "actif" if global_pref else "inactif"))',
     'print(_row("Center Stage (préférence globale)", "actif" if global_pref else "inactif"))'),
    ('print("Aucune camera detectee.")',
     'print("Aucune caméra détectée.")'),
    ('            f"ATTENTION : Center Stage actif sur {\', \'.join(active_names)} -- la camera se recadre "\n            "elle-meme et fausserait toute mesure du tracker."',
     '            f"ATTENTION : Center Stage actif sur {\', \'.join(active_names)} -- la caméra se recadre "\n            "elle-même et fausserait toute mesure du tracker."'),
    ('print("Entrees OBS de capture camera :")',
     'print("Entrées OBS de capture caméra :")'),
    ('label = match.localizedName() if match is not None else "non recoupe"',
     'label = match.localizedName() if match is not None else "non recoupé"'),
    ('ref = f" (reference mediane {ref_median} ms)" if ref_median is not None else ""\n    print(_row(label, f"min={lo:.1f} mediane={med:.1f} p90={p90:.1f} max={hi:.1f} ms{ref}"))',
     'ref = f" (référence médiane {ref_median} ms)" if ref_median is not None else ""\n    print(_row(label, f"min={lo:.1f} médiane={med:.1f} p90={p90:.1f} max={hi:.1f} ms{ref}"))'),
    ('print(_row(label, f"{n / elapsed:.0f} req/s ({elapsed * 1000:.0f} ms total, sequentiel, pas concurrent)"))',
     'print(_row(label, f"{n / elapsed:.0f} req/s ({elapsed * 1000:.0f} ms total, séquentiel, pas concurrent)"))'),
    ('print("Ignore (--quick).")',
     'print("Ignoré (--quick).")'),
    ('        "GetSceneItemList (scene courante)",',
     '        "GetSceneItemList (scène courante)",'),
    ('_report_burst("Rafale GetVersion (60 appels sequentiels)", lambda: obs.request("GetVersion"))\n    _report_burst("Rafale screenshot 640px (60 appels sequentiels)", shot_640.grab)',
     '_report_burst("Rafale GetVersion (60 appels séquentiels)", lambda: obs.request("GetVersion"))\n    _report_burst("Rafale screenshot 640px (60 appels séquentiels)", shot_640.grab)'),
    ('print("\\n=== 5. Go/no-go n.3 -- le crop prend-il effet sans ouvrir le filtre ? ===")',
     'print("\\n=== 5. Go/no-go n°3 -- le crop prend-il effet sans ouvrir le filtre ? ===")'),
    ('print(f"Cas reel utilise : scene \'{host_scene}\', source \'{_REAL_SOURCE}\'.")',
     'print(f"Cas réel utilisé : scène \'{host_scene}\', source \'{_REAL_SOURCE}\'.")'),
    ('print("Fixture temporaire creee (scene/source reelles introuvables).")',
     'print("Fixture temporaire créée (scène/source réelles introuvables).")'),
    ('print(_row("Crop envoye (cropRight)", crop_right))',
     'print(_row("Crop envoyé (cropRight)", crop_right))'),
    ('print(_row("Hash avant", before_hash[:16]))\n        print(_row("Hash apres", after_hash[:16]))',
     'print(_row("Hash avant", before_hash[:16]))\n        print(_row("Hash après", after_hash[:16]))'),
    ('print(_row("Verdict", "LEVE" if passed else "ECHEC"))',
     'print(_row("Verdict", "LEVÉ" if passed else "ÉCHEC"))'),
    ('print(f"Nettoyage : echec suppression scene \'{name}\' ({exc})")',
     'print(f"Nettoyage : échec suppression scène \'{name}\' ({exc})")'),
    ('print(f"Nettoyage : echec suppression source \'{name}\' ({exc})")',
     'print(f"Nettoyage : échec suppression source \'{name}\' ({exc})")'),
    ('print(_row("Resultats apparies", "ok" if ok else "echec"))',
     'print(_row("Résultats appariés", "ok" if ok else "échec"))'),
    ('parser.add_argument("--quick", action="store_true", help="ignore la serie de latences")',
     'parser.add_argument("--quick", action="store_true", help="ignore la série de latences")'),
    ('print("\\n=== Resume go/no-go ===")',
     'print("\\n=== Résumé go/no-go ===")'),
]

missing = []
for old, new in replacements:
    if old == new:
        continue
    count = content.count(old)
    if count != 1:
        missing.append((count, old))
    else:
        content = content.replace(old, new)

if missing:
    for count, old in missing:
        print(f"PROBLEM count={count}: {old!r}")
    raise SystemExit(1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("OK, all replacements applied")

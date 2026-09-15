# PoC recadrage 9:16 — relevés sur la cible Windows

Mesures prises le **15 septembre 2026** sur la machine de production :
Windows 11, i9-14900K, RTX 4090. Ce document est le pendant de
[`docs/poc-mac-webcam.md`](poc-mac-webcam.md) — même exigence : chaque chiffre
dit comment il a été obtenu, rien n'est extrapolé du Mac.

**Ce qui change de statut ici.** Sur le Mac, tout portait l'avertissement « ce
n'est pas la cible ». Sur cette machine, les chiffres YOLO/TensorRT sont des
chiffres de cible — ils décident directement de ce que la production peut
tenir. Seuls les chiffres Apple Vision restent hors sujet : Vision n'existe pas
sur Windows, et rien de ce document ne les cite comme s'ils valaient ici.

## La machine

| Élément | Valeur |
|---|---|
| Matériel | Windows 11 Pro (build 26200), i9-14900K, **RTX 4090** (pilote 610.88) |
| OBS | **32.2.2** |
| obs-websocket | **5.7.4**, port 4455, **authentification activée** — à la différence du Mac |
| Canevas | `Main` 1920x1080 @ 60, plus `Aitum Vertical` 1080x1920 @ 60 (Aitum Vertical installé) |
| Python | 3.12 via **uv 0.9.0** |
| torch / torchvision | 2.8.0+cu128 / 0.23.0+cu128 |
| ultralytics | 8.4.121 |
| onnx / onnxslim | 1.22.0 / 0.1.96 |
| TensorRT | **10.9.0.34**, depuis `pypi.nvidia.com` |

### Pourquoi TensorRT 10 et pas 11

La 11.x est rejetée : elle est **strongly-typed only**, et l'export FP16
d'ultralytics réclame alors `nvidia-modelopt` en plus. La 10.9 exporte le FP16
directement, sans cette dépendance.

## Installation

```bash
uv sync              # environnement, torch/ultralytics/TensorRT compris sur win32
make model            # télécharge yolo11m-pose.pt
make engine           # exporte en moteur TensorRT fp16, batch 1, imgsz 640
```

`make engine` lie le moteur produit à **ce** GPU et à **ce** pilote — un moteur
`.engine` ne se copie pas d'une machine à l'autre. `make model` télécharge un
fichier byte-identique (sha256 `29B17EAF3A31…`) à la copie qui a produit
`tests/corpus/reference-summary-yolo11m-pose.json` : le corpus reste comparable
d'une machine à l'autre tant que ce hash ne change pas.

## Go/no-go — rejoué sur la cible le 15 septembre 2026

`make probe`, sort 0, les trois points passent :

| Point | Résultat |
|---|---|
| OBS ≥ 32.1 | GO |
| `GetCanvasList` | GO — deux canevas, `Main` et `Aitum Vertical` |
| Crop par `SetSceneItemTransform` sans ouvrir le filtre | GO — `cropRight` envoyé à 768, relu à 768 |

**Réserve sur le troisième point.** La scène mesurée joue une vidéo de test en
boucle : les empreintes de capture avant et après diffèrent de toute façon,
puisque l'image change. Seule la relecture (`cropRight` à 768) prouve ici que
le crop a été appliqué — la comparaison de hachage, qui suffisait sur une scène
figée côté Mac, ne prouve rien sur une source en mouvement. `probe.py` garde le
test, mais la garantie tient à la relecture, pas au hachage.

`SERIAL_FRAME` en lot : résultats appariés, comme sur le Mac.

## Latences obs-websocket

Première mesure écartée (démarrage à froid, cf. `docs/poc-mac-webcam.md`).
Mesuré sur la scène « AVOLO Reframe POC », vidéo de test en boucle.

| Requête | min | médiane | p90 | max | médiane sur Mac |
|---|---|---|---|---|---|
| `GetVersion` | 0,2 | **0,3** | 0,3 | 0,4 ms | 0,3 |
| `GetSceneItemList` | 0,2 | **0,3** | 0,4 | 0,5 ms | — |
| `GetSourceScreenshot` 640 px | 3,2 | **4,2** | 5,6 | 6,4 ms | 5,0 |
| `GetSourceScreenshot` 1920 px | 19,9 | **21,4** | 23,9 | 24,1 ms | 14,0 |

En rafale : **3 135 req/s** sur `GetVersion`, **254 req/s** sur un screenshot
640 px en séquentiel.

Le protocole tient les mêmes ordres de grandeur que sur le Mac, à un détail
près : le screenshot 1920 px est plus lent ici (21,4 ms contre 14,0). La
lecture GPU passe par D3D11 sur cette machine, par Metal sur le Mac — deux
chemins différents, pas comparables terme à terme et ce chiffre confirme
seulement que le websocket reste praticable sur les deux.

## Détecteur : YOLO11-pose, `.pt` contre `.engine`

### Méthode

`make bench` (`tests/corpus/tools/bench_detectors.py`) : 39 images du corpus,
640 px de large, JPEG q75. `.pt` et `.engine` tournent dans le **même
processus**, images entrelacées une à une, lot unitaire. Le premier tour est
écarté, cinq tours mesurés. `detect()` inclut le décodage JPEG.

| Détecteur | Médiane | p95 |
|---|---|---|
| `.pt` fp16 | **13,88 ms** | 18,98 ms |
| `.engine` TensorRT fp16 | **7,27 ms** | 10,13 ms |

Les deux détectent une médiane de 2 boîtes par image — même lecture de la
scène, l'écart n'est que de latence.

### La boucle en direct, 60 s chacune

Vidéo de test, capture 640 px, 30 im/s visés (`reframe.toml` local). Chiffres
tirés du résumé produit par la boucle elle-même.

| | `.pt` | `.engine` |
|---|---|---|
| Itérations | 1 799 en 60,0 s (29,97 im/s) | 1 799 en 60,0 s (29,97 im/s) |
| Capture, médiane / p90 | 3,5 / 4,4 ms | 3,4 / 3,9 ms |
| Détection, médiane / p90 | 14,1 / 23,1 ms | 9,4 / 13,6 ms |
| Politique | ~0 | ~0 |

Les deux tiennent la cadence visée sans effort — la détection reste bien sous
le budget d'une image à 30 im/s (33 ms). TensorRT gagne surtout de la marge,
pas la cadence elle-même : les deux moteurs la tiennent déjà.

## Vérifier l'état réellement appliqué, pas le rectangle calculé

Règle du dépôt, rappelée dans `CLAUDE.md` : un compte de tests ne dit rien d'un
défaut visuel. Vérifié ici en lisant `GetSceneItemTransform` /
`GetSceneItemEnabled` sur les items `RF Cam` depuis un **second processus**,
toutes les 250 ms pendant 40 s, la boucle tournant en parallèle sur `.engine` :

- **41 états appliqués distincts** relevés, transitions lissées, alternance
  simple/split ;
- crop de l'item de sortie : 607x1080, ratio **0,562** contre une cible de
  0,5625 ;
- cellule de split : 1040x925, ratio **1,124** contre une cible de 1,125.

Julien a confirmé le cadrage à l'œil dans OBS pendant la mesure. C'est la
même discipline que sur le Mac : la lecture externe, pas le calcul interne,
tranche.

## Corpus : comparaison Vision / YOLO et déterminisme

### Rejeu complet

`--detector yolo`, `yolo11m-pose.pt`, 12 im/s, 640 px : **1 min 53 s** pour
698 s de vidéo — l'extrait de référence (sha256_16 `b172c72d8b9c9e33`), mêmes
paramètres que la référence Vision.

| | Vision | YOLO |
|---|---|---|
| Taux de détection | 99,44 % | 99,7 % |
| Commandes | 355 | 386 |
| Images à 2 corps ou plus | 45,63 % | 48,76 % |
| Dégénéré | 89,95 % | 89,51 % |
| Coupe au `crane_coupe` | 0 % | 0 % |
| Marge médiane | 78,7 px | 79,3 px |
| Marge p10 | 28,6 px | 27,9 px |
| Inatteignable | 978 | 971 |

Les deux détecteurs racontent la même scène, à quelques points près. C'est ce
qui valide le remap d'articulations COCO-17 → Vision (`neck_1` : milieu des
épaules, `root` : milieu des hanches) : l'estimation du crâne, calibrée sur
Vision, tient telle quelle sur les boîtes YOLO.

Résumé versionné dans `tests/corpus/reference-summary-yolo11m-pose.json` —
`reference-summary.json` (Vision) reste intact, à côté. La trace complète
(`tests/corpus/traces/full-yolo11m-pose.jsonl`) n'est pas versionnée.

### Déterminisme

Tranche `--start 380 --duration 60`, `.pt`, deux exécutions : sha256 identique
(`ca6862390a39…`). Le comportement mesuré sur le Mac se transpose donc au
détecteur YOLO en `.pt`.

**Un moteur TensorRT n'a pas cette garantie**, et il est lié au couple
GPU + pilote qui l'a produit. D'où la règle : les rejeux du corpus utilisent
`.pt`, la boucle en direct peut utiliser `.engine`.

## Pièges Windows trouvés et corrigés

- **`time.monotonic()` avance par paliers de 15,6 ms** sur Windows + Python
  3.12 (un tic de l'horloge à 60 Hz). `adapters/animator.py` et
  `adapters/control.py` sont passés à `time.perf_counter()`.
- **La sortie standard redirigée est en cp1252.** La flèche `→` affichée par
  `scripts.run` levait `UnicodeEncodeError`. `scripts/__init__.py`
  reconfigure `stdout`/`stderr` en UTF-8 au chargement du paquet.
- **`SO_REUSEADDR` laissait une deuxième boucle se lier au port 4466 sans
  erreur.** Ce comportement est propre à Windows ; l'option est désormais
  désactivée sur `win32`.
- **Un chemin Windows dans le TOML a besoin de `/` ou de guillemets simples**
  — l'antislash nu casse le parseur TOML.

### Un plantage d'OBS, non reproduit à volonté

Mode studio actif, rien en programme : une requête `GetCurrentProgramScene`
a coïncidé avec un plantage d'OBS 32.2.2 deux secondes plus tard (exception
`0xc0000374`, corruption de tas, dans `ntdll.dll`). **Non reproduit
volontairement — probable, pas prouvé.**

`probe.py` mesurait jusqu'ici sur la scène de programme, qui pouvait être
vide (`None`). Il mesure désormais sur la scène du PoC, jamais sur un
programme vide. Règle qui en sort : **ne jamais interroger la scène de
programme quand elle peut être vide.**

### Collections de scènes dédiées

`setup_scene` et `probe` basculent OBS sur une collection dédiée « AVOLO
Reframe » (créée si absente), et refusent de le faire pendant un direct ou un
enregistrement. Les autres collections ne sont jamais modifiées, mais OBS reste
sur « AVOLO Reframe » après coup : la collection quittée, que le script affiche,
se rouvre à la main.

## Ce qui a changé dans le code

- `pyobjc` reste limité à macOS dans `pyproject.toml` ; `torch`,
  `ultralytics` et les paquets TensorRT sont limités à `win32` — `torch`
  depuis l'index cu128, les bibliothèques TensorRT depuis `pypi.nvidia.com`.
- `adapters/pose_geometry.py` porte la logique crâne / buste / ancre partagée
  entre Vision `pose` et YOLO, et le remap de noms d'articulations
  COCO-17 → Vision (`neck_1`, `root`).
- `adapters/detect_yolo.py` implémente le détecteur YOLO ; `build_detector`
  dans `adapters/detector.py` est la seule fabrique, pour `scripts.run` comme
  pour `scripts.corpus`. Détecteur par défaut : `pose` sur macOS, `yolo`
  ailleurs — `pose`/`vision` sont refusés hors macOS. `--yolo-model` (ou
  `[detector] yolo_model` dans le TOML) pointe le poids, par défaut
  `models/yolo11m-pose.pt`. Le nom de détecteur rendu est
  `yolo-yolo11m-pose`, suffixé `-trt` pour un moteur et `-bust` pour
  `--upper-body`.
- `--features` hors macOS dessine désormais le squelette YOLO (articulations,
  os, ligne d'épaules) à partir des poses déjà calculées, sans inférence
  supplémentaire. Pas de visage, pas de yaw/pitch/roll : le portage de cette
  partie et l'estimation du yaw depuis les 5 points de visage COCO restent à
  faire, suivis par l'issue GitHub #1.
- `setup_scene --camera` utilise `dshow_input` (propriété `video_device_id`)
  sur Windows, à la place de `macos-avcapture`. **Non testé en direct avec
  une caméra** à ce jour — seul le rejeu sur la vidéo de test est vérifié.
- Un `Makefile` centralise les commandes (`make` liste les cibles) :
  `sync`, `check`, `test`, `model`, `engine`, `bench`, `probe`, `setup`,
  `setup-camera`, `run`, `corpus`. `ARGS="..."` passe des options
  supplémentaires ; `CLIP`, `TRACE`, `SUMMARY`, `FPS`, `MODEL` sont
  surchargeables. Vérifié depuis PowerShell (via GnuWin32 make, qui invoque
  `cmd`) et depuis un shell POSIX.

## Ce qui n'est toujours pas vérifié

- **La caméra en direct via `dshow_input`.** Le code est écrit, jamais essayé
  avec un vrai périphérique branché.
- **Le correctif sur la résolution qui change à la reconstruction de scène**
  n'a été exercé que sur cette machine ; il n'a pas été rejoué sur le Mac
  pour confirmer qu'il tient aussi côté Vision.
- **Le yaw de la tête depuis les 5 points de visage COCO**, pour l'air devant
  le regard côté YOLO — jamais mesuré, suivi par l'issue GitHub #1.

## Un constat de politique, hors du périmètre de ce PoC

Rejoué sur la trace YOLO du corpus, pas mesuré ici en premier lieu, mais assez
lié aux chiffres de ce document pour être noté. En direct, la bascule
split → simple semblait lente. En comparant les paramètres par défaut à la
configuration réglée à l'oreille par Julien (switches datés sur la dernière
image dont la détection justifiait encore le split) :

| Configuration | Sortie du split, médiane | p90 |
|---|---|---|
| Défauts (`split_exit_ms` 3000, `track_hold_ms` 6000) | 3,8 s | 6,0 s |
| Réglage live de Julien (entrée 100, sortie 100, maintien 2000, transition 1200) | 1,2 s | 2,0 s |

Avant le correctif ci-dessous, `_split_ready` évaluait des pistes mémorisées :
le split survivait donc sur un sujet qui n'était plus détecté, et plus de la
moitié des entrées en split se produisaient sur une piste périmée. Baisser
`track_hold_ms` échangeait de la latence contre du scintillement — ce n'était
pas un réglage gratuit.

Pour situer l'ordre de grandeur : sur un décrochage YOLO d'un sujet présent
des deux côtés d'un trou de détection, la médiane est de 333 ms, le p90 de
3 000 ms, le p95 de 5 417 ms et le p99 de 9 250 ms — à comparer aux 4 850 ms
de moyenne mesurés sur Vision, qui ont servi à calibrer les défauts actuels.
La queue mélange de vrais décrochages et de vraies sorties de champ, faute
d'un extrait annoté qui les distingue.

C'est un constat de **politique**, pas de portage : suivi par l'issue GitHub
#2, pour ne pas le mélanger à ce document.

**Corrigé le 15 septembre 2026.** L'entrée en split ne se décide plus que sur
des détections fraîches, et la sortie coupe sur l'image même où l'état bascule.
Les défauts changent en conséquence (`split_exit_ms` 500, `track_hold_ms` 500,
calés sur YOLO et non plus sur Vision) :

| Configuration | Sortie du split, médiane | p90 |
|---|---|---|
| Anciens défauts (`split_exit_ms` 3000, `track_hold_ms` 6000) | 4 750 ms | 6 417 ms |
| Nouveaux défauts (`split_exit_ms` 500, `track_hold_ms` 500) | 500 ms | 583 ms |

Entrées sur piste périmée : 0 contre 8 sur 20 avant. Détail complet — la table
avant/après à quatre colonnes, la section `split` du résumé, la correction de
la règle de coupe de tête qui a servi à vérifier ce correctif — dans
[`tests/corpus/README.md`](../tests/corpus/README.md).

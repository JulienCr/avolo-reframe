# Repérage des bascules de plan — `lab-avolo-58m22-70m00.mp4`

Ce dossier documente le repérage des bascules de plan (cuts) de l'extrait de
laboratoire : ce qui manquait au corpus de `tests/corpus/` pour séparer ce que
la politique de cadrage fait des vraies coupes de ce qu'elle fait du bruit de
détection.

**Pour rejouer le repérage**, dans l'ordre :

```bash
uv run python tests/corpus/cuts/cut_thresholds.py    # distribution des sauts, calibre les seuils
uv run python tests/corpus/cuts/detect_cuts.py        # repère les discontinuités (diagnostic seul)
uv run python tests/corpus/cuts/rank_candidates.py    # écrit candidates.json et events.json
uv run python tests/corpus/cuts/pick_windows.py       # propose les fenêtres de 40 s les plus denses
uv run python tests/corpus/cuts/extract_cut_frames.py # régénère images/ (non versionné) depuis l'extrait
```

Chaque script résout la racine du dépôt depuis son propre emplacement et
s'arrête avec un message explicite si `tests/corpus/traces/full-reference-pose.jsonl`
est absente (elle n'est pas versionnée — voir `.gitignore` — et se régénère
par `uv run python -m scripts.corpus ...`, commande donnée dans le message
d'erreur). `extract_cut_frames.py` a besoin en plus de
`tests/fixtures/lab-avolo-58m22-70m00.mp4`, également ignoré par git.

## Méthode

Source : `tests/corpus/traces/full-reference-pose.jsonl` (8 376 images échantillonnées à 12 im/s, soit ~83 ms entre deux lignes). Pour chaque paire d'images consécutives, quatre signaux sont calculés :

- **saut du centre de `union`** (distance euclidienne, en pixels, entre les centres de l'union des boîtes d'une image à la suivante) ;
- **saut d'échelle** (`|log(diag_apres / diag_avant)|`, où `diag` est la diagonale du rectangle `union` — capture un changement de plan large ↔ serré indépendamment de la position) ;
- **changement de `n_detections` persistant** : le nouveau compte doit tenir sur l'image suivante et l'ancien devait tenir sur l'image précédente, pour écarter un simple clignotement d'une image ;
- **apparition/disparition de `union`** (passage à `null`), qui capture les cartons-titres, incrustations plein cadre et autres images sans détection.

### Seuils retenus

Calibrés sur la distribution empirique des sauts image à image (voir `cut_thresholds.py`) :

| Signal | p95 | p98 | p99 | p99.5 | **seuil retenu** |
|---|---|---|---|---|---|
| saut de centre (px) | 77,6 | 121,1 | 172,7 | 377,5 | **150 px** |
| saut d'échelle (log) | 0,103 | 0,188 | 0,362 | 0,521 | **0,28** |

Un mouvement humain filmé en continu ne fait pas sauter le centre de sa boîte de 150 px (≈ 8 % de la largeur image) ni doubler-diviser par 1,3 sa taille en 83 ms — ces seuils se situent entre p98 et p99, donc nettement au-dessus de la masse du bruit de détection ordinaire, sans aller chercher la queue extrême (p99.5+) qui aurait éliminé de vrais petits cuts entre plans de taille voisine.

### Score et dédoublonnage

Chaque signal actif contribue un score (`amplitude / seuil` pour les sauts continus, `0,8 × |Δn_detections|` pour un changement persistant, `1,0` pour une apparition/disparition). Les transitions à moins de 300 ms d'écart sont fusionnées (un même cut est souvent vu à la fois par le saut de centre et par le changement de `n_detections`) : 227 transitions brutes flaguées → **150 événements de coupe dédupliqués** (`events.json`).

### Sélection des 40 candidats retenus

Un tri global par score concentrait les 40 meilleurs sur une seule zone très montée (555–700 s), qui produit des sauts d'amplitude bien supérieure au reste. Pour que la vérification humaine porte sur des situations variées plutôt que sur un seul style de montage, un plafond de **4 candidats par fenêtre de 40 s** a été appliqué avant de compléter jusqu'à 40 avec les meilleurs scores restants. Le fichier `candidates.json` (40 entrées, triées par `pts_ms`) donne pour chacun : le signal déclencheur, l'amplitude mesurée, le score, une confiance qualitative (haute ≥ 5, moyenne ≥ 2, basse en dessous), et les horodatages exacts avant/après.

## Vérification visuelle (échantillon)

Un sous-échantillon des candidats a été confronté aux images sources (`sheets/sheet_1.jpg`, `sheet_2.jpg`, `sheet_3.jpg`, plus des sondes ponctuelles à 170 s, 450 s et 620 s). Confirmé à l'œil :

- **169 s** : cut net d'un plan serré solo (fond neutre bleu) vers un plan large à deux personnes assises — signal `center_jump`+`scale_jump` cohérent avec un vrai changement de caméra.
- **204,7 s** : incrustation d'une vignette (référence à un film d'animation) en bas à droite du cadre — capturé par le changement d'échelle de `union` sans changement de caméra à proprement parler.
- **353–392 s** : alternance avec un écran "SOMMAIRE" (liste à puces) qui coupe et revient sur l'interview — plusieurs vrais cuts consécutifs, bien séparés par la fenêtre de déduplication à 300 ms.
- **450–487 s** : segment très monté — deux-shot / gros plan sur un des deux interlocuteurs / carton "STORY TING (?)" / retour cadre large. C'est la zone la plus dense de tout l'extrait.
- **620–653 s puis 671–697 s** : alternance rapide plan serré (un locuteur debout) ↔ plan large (le même locuteur + deux personnes assises en arrière-plan) — cuts très nets, `n_detections` bascule 1 ↔ 3.

**Ce dont on n'est pas sûr** : les deux tout premiers candidats (583 ms et 3 083 ms, tout début de l'extrait) montrent deux compositions à deux personnes assez proches l'une de l'autre — impossible de trancher à l'œil entre un vrai cut rapproché et un mouvement du buste (l'un des deux boit un verre) qui a fait sauter la boîte. À vérifier en priorité par un humain, avec le son ou l'image intermédiaire.

## Fenêtres de 40 s recommandées

Trois fenêtres choisies pour leur diversité de situation, chacune avec au moins trois bascules candidates :

### Fenêtre A — 165 s à 205 s (12 candidats)
Interview calme à deux, peu de mouvement de caméra dans le reste de l'extrait : plan serré solo → plan large à deux, puis une incrustation de vignette vers 205 s. Bon cas de référence pour une politique de cadrage en régime "cut classique, peu fréquent".

### Fenêtre B — 450 s à 490 s (22 candidats — la zone la plus dense de tout l'extrait)
Séquence de montage serrée : gros plan / plan large / carton-titre se succèdent en quelques secondes. C'est le cas le plus exigeant pour la politique de cadrage (plusieurs bascules en moins de 10 s), et celui où il est le plus facile de confondre bascule et bruit de détection.

### Fenêtre C — 615 s à 655 s (11 candidats)
Alternance rapide et régulière entre un plan serré à un sujet et un plan large à trois sujets (un locuteur debout, deux personnes assises en arrière-plan) — cas de bascule avec changement du nombre de sujets détectés, distinct des deux autres fenêtres qui n'ont qu'un ou deux sujets.

## Contenu du dossier

- `candidates.json` — 40 candidats, triés par `pts_ms`, produits par `rank_candidates.py`.
- `events.json` — les 150 événements dédupliqués (le surensemble avant plafonnage par fenêtre).
- `sheets/sheet_1.jpg`, `sheet_2.jpg`, `sheet_3.jpg` — planches contact (4 colonnes, ~14 paires avant/après chacune, étiquetées par horodatage).
- `cut_thresholds.py`, `detect_cuts.py`, `rank_candidates.py`, `pick_windows.py`, `extract_cut_frames.py` — les scripts, rejouables depuis la racine du dépôt.

**Non versionné, régénérable** : `images/cut_<pts_ms>_avant.jpg` / `_apres.jpg` (80 images, largeur 640 px), reproduites par `extract_cut_frames.py` à partir de `candidates.json` et de l'extrait vidéo.

## Ce qui reste douteux

Détaillé dans le rapport transmis en même temps que ce versement : le calcul du score mélange des unités hétérogènes (rapport à un seuil pour les sauts continus, produit direct pour `Δn_detections`) sans justification autre qu'empirique, et le plafond de 4 candidats par fenêtre de 40 s est arbitraire — un autre choix (3 ou 5) aurait changé la composition des 40 candidats sans qu'on sache lequel est le plus représentatif.

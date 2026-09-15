# Corpus de cas de contrôle

Ce que le PoC a mesuré, conservé pour pouvoir comparer après un changement.
L'ADR réclame ce corpus : la politique causale n'a presque aucun chiffre derrière
elle, et c'est ici qu'ils s'accumulent.

Récupéré du scratchpad de session le 12 septembre 2026 — il aurait été effacé.

## Contenu

| Dossier | Quoi | À versionner |
|---|---|---|
| `reference-summary.json` | l'agrégat d'un rejeu complet, détecteur Vision, ~900 octets | **oui** |
| `reference-summary-yolo11m-pose.json` | le même agrégat, détecteur YOLO, pour comparer les deux sur le même extrait | **oui** |
| `traces/` | les traces JSONL brutes, ~5,7 à 8,6 Mo pièce | non |
| `frames/` | 39 images échantillonnées de l'extrait, 1 Mo | oui |
| `tools/` | les scripts d'analyse et de vérification | oui |

## `reference-summary.json`

L'état de référence, à comparer après toute modification de la politique.
Il porte l'empreinte de l'extrait, tous les paramètres et le détecteur utilisés —
un résumé qui ne dit pas ce qui l'a produit ne vaut rien trois semaines plus tard.

Chiffres au 13 septembre 2026, **détecteur `pose`** (le précédent, `vision-upper`,
ne fournit aucune estimation de crâne) : 8 376 images, détection 99,4 %,
355 commandes, **45,6 % d'images à deux corps ou plus**, dont 3 415 cadrées
entre les sujets et 4 626 sur un sujet.

> **Ce fichier précède la correction de la règle `crane_coupe` du 15 septembre
> 2026** (voir plus bas) et les nouveaux défauts de split issus de l'issue #2 :
> ses chiffres de bascule et de coupe ne sont pas comparables à ceux mesurés
> depuis. Il ne peut pas non plus être régénéré sur Windows, faute d'Apple
> Vision — il reste tel quel, comme témoignage du PoC macOS.

`reference-summary-yolo11m-pose.json` porte le même rejeu, **détecteur YOLO11-
pose `.pt`**, mesuré le 15 septembre 2026 sur la machine de production
(Windows, RTX 4090) : 8 376 images, détection 99,7 %, 373 commandes, 48,76 %
d'images à deux corps ou plus. Régénéré ce même jour après le correctif de
l'issue #2 (nouveaux défauts de split, règle `crane_coupe` corrigée) par
`--from-trace` — détail des deux prochaines sections.

Nouveau : **`crane_coupe`**, la mesure qui aurait attrapé le défaut de coupe de
tête du 12-13 septembre — sur le cadre **commandé par la politique**
(la destination de la transition, pas la cible calculée qui peut en diverger
pendant des dizaines d'images tant que la zone morte ne recommet pas, ni le
rectangle interpolé qu'OBS affiche pendant la transition elle-même, jamais
modélisé ici — voir l'issue #9). 10 025 cellules vérifiées, **1 coupée**
(0,01 %), marge médiane +80 px, p10 +30 px ; 1 075 cas exclus où le crâne
estimé tombe lui-même hors de la source (seule exception acceptée, comptée à
part, jamais dans le taux de coupe).

## Rejouer une trace : `--from-trace`

```bash
uv run python -m scripts.corpus --from-trace tests/corpus/traces/full-yolo11m-pose-v2.jsonl \
    --out tests/corpus/traces/replay.jsonl --summary-json tests/corpus/reference-summary-yolo11m-pose.json
```

Forme `make` équivalente : `make replay TRACE=tests/corpus/traces/full-yolo11m-pose-v2.jsonl`.

`scripts.corpus` rejoue les boîtes déjà détectées d'une trace JSONL au lieu de
décoder le clip et de faire tourner le détecteur : quelques secondes au lieu de
75 s, ce qui rend un balayage de paramètres praticable. **Rejoué avec les mêmes
paramètres, un rejeu reproduit au bit près le run qui a produit la trace**
(`cmp` sur le JSONL et sur le résumé) — c'est ce qui rend le réglage mesurable :
changer un seul paramètre de politique et attribuer toute différence à ce
paramètre, sans que le détecteur ou le décodage n'y soient pour rien. Distinct
de l'autre garantie de bit-à-bit, plus bas dans `traces/` : deux **détections**
complètes du même clip donnent la même trace, mesuré côté Vision.

`--from-trace` et `<clip>` sont exclusifs ; tous les paramètres de politique
exposés par `scripts.corpus --help` s'appliquent aussi bien à un rejeu qu'à une
détection complète (split, eye-line, zoom, max-zoom compris — ils manquaient
avant l'issue #2, `TODO.md` le signalait).

**Format de trace enrichi.** Une trace produite depuis le 15 septembre 2026
porte, pour chaque boîte, `anchor`, `crown`, `crown_margin` et `bust` en plus de
`x`, `y`, `w`, `h`, `score` : un rejeu applique donc les mêmes commits de crâne
que le run d'origine. Les traces plus anciennes (`full-reference.jsonl`,
`full-reference-pose.jsonl`) n'ont pas ces champs et se rejouent quand même,
sans commit de crâne pendant les verrous d'animation.

## La section `split` du résumé

Mesure les bascules split ↔ simple sur la couche qu'OBS montre réellement — les
**commandes émises**, pas l'état interne calculé, qui peut changer sans qu'une
commande parte (zone morte, verrou d'animation).

- `switches` : nombre de commandes qui changent de mode.
- `entries` / `stale_entries` : entrées en split, et combien d'entre elles
  partaient d'une piste qui n'était plus détectée sur l'image (toujours 0
  depuis l'issue #2 : l'entrée n'est plus décidée que sur des détections
  fraîches).
- `short_splits` : bascules dont le split qui suit dure moins de 1 500 ms.
- `split_time_share` : part du temps passé en split — sur les images
  **appliquées**, à distinguer de la part des images dont les détections
  justifieraient un split (le résumé donne les deux, cf. `degenerate`).
- `entry_ms` / `entry_episode_ms` : délai entre le début du compte à rebours
  d'entrée et la commande appliquée, sans tolérance puis avec (un trou de moins
  d'une seconde dans une série d'entrées ne casse pas l'épisode).
- `exit_ms` : délai entre **la dernière image dont les détections justifiaient
  encore le split** et la commande de sortie appliquée.
- `exit_causes` : `track_died` (moins de deux pistes vivantes) contre
  `not_ready` (deux pistes vivantes mais la cible n'est plus dégénérée).
- `phantom_exits` : sorties d'état jamais suivies d'une commande avant un
  retour en split — diagnostic de l'ancien défaut, à 0 depuis le correctif.
- `exit_apply_ms` : délai entre le changement d'état et la commande qui
  l'applique — diagnostic, à 0 depuis que `mode_cut_pending` coupe la sortie
  sur l'image même où l'état bascule.

## La règle `crane_coupe`, corrigée le 15 septembre 2026

`crane_coupe` vérifie chaque tête détectée contre la ou les cellules
**commandées** (destination de la transition, cf. plus haut) dont l'empan
horizontal la contient, en gardant la meilleure marge (mode single : contre le
crop commandé qui couvre la tête). Les cellules chevauchent souvent en x ;
l'ancienne règle appariait `state.tracks`
aux cellules par ordre de centre x et sautait les pistes non rafraîchies sur
l'image, si bien qu'une tête montrée en entier dans la cellule du haut pouvait
être jugée contre la cellule du bas d'un partenaire mémorisé.

**Le « 0 % coupées » relevé le 13 septembre 2026 était un artefact de cette
règle.** L'ancienne politique, remesurée avec la règle corrigée, coupe en
réalité 0,35 % des cellules (défauts) et 1,80 % (réglages live). Les chiffres
produits avant ce correctif — `reference-summary.json`, les mesures Vision, les
mesures Mac — ne sont pas comparables à ceux d'après, et ne peuvent pas être
régénérés sur Windows.

## Avant / après le correctif de l'issue #2

Politique d'avant = commit `8ff199e`. « live » = `split_enter_ms` 100,
`split_exit_ms` 100, `track_hold_ms` 2000, `ease_ms` 1200 — le réglage à
l'oreille utilisé en direct avant le correctif.

| | ancienne politique, défauts (6000/3000/600, ease 320) | ancienne politique, live | nouvelle politique, nouveaux défauts (500/500/600) |
|---|---|---|---|
| entrée méd/p90 | 667/750 ms | 167/1250 ms | 667/667 ms |
| sortie méd/p90 | 4750/6417 ms | 1750/2833 ms | 500/583 ms |
| bascules | 39 | 85 | 72 |
| entrées sur piste périmée | 8 sur 20 | 17 sur 43 | 0 |
| splits < 1,5 s | 0 | 1 | 1 |
| temps en split | 82,5 % | 62,8 % | 42,5 % (part des images qui le justifient) |
| sorties jamais appliquées | 9 | 13 | 0 |
| crâne coupé | 0,35 % | 1,80 % | 1 cellule sur 10 025 |
| commandes | 386 | 274 | 373 (336 avec `ease_ms` 1200) |

La longueur de la transition (`ease_ms`) ne change plus les métriques de
bascule : sur 192 configurations balayées, les 48 où une bascule de mode
traverse une transition en cours donnent des chiffres identiques pour
`ease_ms` 320 et 1200 — c'est un choix purement visuel désormais. Un balayage
plus rapide (1000/3000/100) descend l'entrée à 167 ms mais introduit 3 splits
courts et 83 bascules ; un balayage sans aucun split court (1500/3000/100)
tient l'entrée à 1500/3250 ms pour 77 bascules. Le corpus a tranché pour
500/500/600 : la sortie la plus rapide sans ajouter de split court, compte tenu
d'une médiane de décrochage YOLO de 250-333 ms.

## Le résultat négatif de `tools/edge_exits.py`

Une dernière boîte touchant un bord du cadre prédit-elle qu'un sujet perdu est
sorti de champ plutôt que raté par le détecteur ? Suivi par plus-proche-voisin,
délibérément indépendant de `core/policy.py` (gate à 15 % de la largeur) :
1 531 pertes (5 censurées par la fin de trace, exclues des pourcentages
ci-dessous), dont 379 avec la dernière boîte à moins de 3 % d'un bord latéral
(3 censurées).

Les pertes en bord de cadre reviennent **plus** souvent que celles à mi-cadre :
jamais revenues sous 10 s, 6,6 % en bord contre 9,0 % à mi-cadre ; revenues
sous 1 s, 77,9 % en bord contre 74,0 % à mi-cadre. La vitesse vers le bord ne
sépare pas non plus les deux catégories. **Aucun maintien plus court n'est
justifié pour une sortie en bord de cadre.** Résultat négatif conservé
volontairement, dans l'esprit de `zoom-before/after.jsonl` — sans extrait
annoté, « jamais revenu » mélange de vraies sorties de champ et de longs
décrochages.

## `traces/`

- `full-reference.jsonl` — rejeu complet de l'extrait avec `vision-upper`
  (sans crâne), sha256 `a6db1d5b3a4b1e08` ; gardé pour comparer le détecteur.
- `full-reference-pose.jsonl` — même extrait, **détecteur `pose`**, sha256
  `2b0520667503dcab` : c'est celui qui a produit `reference-summary.json`.
- `full-yolo11m-pose-v2.jsonl` — même extrait, **détecteur YOLO11-pose `.pt`**
  sur la machine de production, empreinte du **clip** source `b172c72d8b9c9e33`
  (champ `clip_sha256_16` de l'en-tête, pas un hachage du fichier de trace
  lui-même), format enrichi (`anchor`, `crown`, `crown_margin`, `bust`) :
  c'est celui rejoué par `--from-trace` pour produire
  `reference-summary-yolo11m-pose.json`.
- `zoom-before.jsonl` / `zoom-after.jsonl` — avant et après l'introduction de
  `zoom_dead_zone` : 153 commandes contre 149, soit **−2,6 % seulement**. Résultat
  négatif conservé exprès : il dit que le pumping mesuré sur cet extrait vient de
  la dérive horizontale, pas du bruit de hauteur.

**Régénérables**, à condition d'avoir l'extrait :

```bash
make corpus ARGS="--detector pose"
```

Forme `uv run` équivalente, sur une seule ligne :

```bash
uv run python -m scripts.corpus tests/fixtures/lab-avolo-58m22-70m00.mp4 --detector pose --fps 12 --out trace.jsonl --summary-json summary.json
```

75 s pour 698 s de vidéo, et **deux détections complètes du même clip donnent
une trace identique au bit près** (mesuré côté Vision) — à ne pas confondre
avec le bit-à-bit du `--from-trace` plus haut, qui porte sur le rejeu d'une
trace déjà produite, pas sur la détection elle-même. C'est ce déterminisme qui
rend le réglage mesurable : on change un seul paramètre et on attribue la
différence à ce paramètre.

> **Mais l'extrait `.mp4` n'est pas versionné.** Sans lui, rien n'est
> régénérable et ces traces sont le seul témoignage durable des mesures. À garder
> en tête avant de faire le ménage.

## `frames/`

Images tirées de l'extrait à des instants connus. Elles documentent ce qu'il
contient — quatre compositions distinctes, dont des sujets debout et des entrées
et sorties de champ — contre l'idée fausse, tenue une demi-journée, qu'il
s'agissait d'un régime permanent à deux personnes assises.

## `tools/`

Les scripts qui ont produit les mesures. La plupart sont des sondages ponctuels,
gardés pour la traçabilité plutôt que pour être relancés. Les plus utiles :
`analyze_two_subjects.py`, `verify_animator.py`, `verify_apply.py`,
`verify_resilience.py`, `cpu2.py`, `split_rate.py`, `edge_exits.py`.

## Ce qui manque encore

Un extrait avec des **bascules de plan annotées**. Celui-ci n'en a pas, donc on
ne sait pas séparer ce que la politique fait des coupes de ce qu'elle fait du
bruit de détection — et les bascules sont précisément ce que la politique causale
existe pour gérer.

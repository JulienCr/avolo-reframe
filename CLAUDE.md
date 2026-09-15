# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## État du dépôt

Un PoC macOS qui tourne, un portage Windows qui tourne sur la cible, et quatre
documents :

- [`docs/adr/0001-avolo-reframe.md`](docs/adr/0001-avolo-reframe.md) — la décision d'architecture. À lire avant d'écrire quoi que ce soit.
- [`docs/recadrage-live-references.md`](docs/recadrage-live-references.md) — le dossier de sources (API obs-websocket, état de l'écosystème, chiffres de détection).
- [`docs/poc-mac-webcam.md`](docs/poc-mac-webcam.md) — **tout ce qui a été mesuré sur le Mac**. À lire avant d'affirmer un chiffre côté Vision.
- [`docs/poc-windows.md`](docs/poc-windows.md) — **tout ce qui a été mesuré sur la cible**. À lire avant d'affirmer un chiffre côté YOLO/TensorRT ou obs-websocket sur Windows.
- [`TODO.md`](TODO.md) — ce qui reste, et surtout ce qui est **tranché** : à lire avant de rouvrir un débat.
- [`tests/corpus/`](tests/corpus/README.md) — le corpus de cas de contrôle : trace de référence, images de l'extrait, outils de mesure. À comparer après tout changement de politique.

Le PoC est en **Python 3.12** (`uv`), avec Apple Vision comme détecteur sur macOS et YOLO11-pose sur Windows. Ça ne tranche **pas** le langage du cœur en production, volontairement ouvert par l'ADR (C++ direct si la cible reste le direct seul, Rust en ABI C s'il doit aussi servir Node) : `core/` est en fonctions pures sur des `dataclass` de flottants, transposable. Ne pas choisir à la place de l'ADR — poser la question.

## La cible a maintenant ses propres mesures

La cible de production est **Windows 11, i9-14900K, RTX 4090** — mesurée le 15 septembre 2026, détail dans [`docs/poc-windows.md`](docs/poc-windows.md). Ce qui a été mesuré sur le **MacBook Pro M3** ([`docs/poc-mac-webcam.md`](docs/poc-mac-webcam.md)) reste antérieur et distinct.

**Apple Vision n'existe pas sur Windows** : ses latences ne valent toujours que pour la machine de développement, jamais pour la cible. **Les chiffres YOLO/TensorRT, eux, sont des chiffres de cible** — ils décident directement de ce que la production peut tenir. Ce qui se transpose du Mac : la chaîne obs-websocket, la politique, la géométrie. Ce qui ne se transpose pas : le détecteur, `macos-avcapture` (→ `dshow_input` sur Windows), et tout ce qui touche à Center Stage.

## Commandes

```bash
make sync              # environnement (uv sync)
make check             # ruff F821 + pytest, à lancer avant toute exécution
make model             # télécharge les poids YOLO
make engine            # exporte en moteur TensorRT fp16, lié à ce GPU et à ce pilote
make probe             # go/no-go + latences, sort 0 si tout passe
make setup             # (re)construit la scène sur la vidéo de test
make run ARGS="--upper-body"   # la boucle
make corpus            # rejeu déterministe hors OBS ; voir tests/corpus/README.md
make replay            # rejoue TRACE en quelques secondes, sans décodage ni détection
```

Formes `uv run` sous-jacentes, pour deux commandes clés :

```bash
uv run python -m scripts.probe                   # équivalent de make probe
uv run python -m scripts.run --upper-body --features    # + overlay des traits dans OBS (~25 ms/image sur macOS, négligeable ailleurs)
```

**La source par défaut est la vidéo de test, pas la caméra.** Une caméra rend chaque exécution différente, donc deux mesures ne sont plus comparables. `--camera` pour rebasculer.

`--upper-body` bascule le détecteur en mode tête+torse. **Plus robuste, pas indispensable** : 1,1 % d'images sans détection contre 8,3 % sur le plan large le plus difficile de l'extrait. `VNDetectHumanRectangles` n'exige pas de voir les jambes. Il cadre en revanche le buste seul, donc faux pour un comédien debout qu'on veut en entier.

**Les quatre détecteurs se tiennent entre 6 et 9 ms** — ce n'est pas là que se joue la cadence. Ne comparer que des mesures prises sur les **mêmes images, dans le même processus, à la suite** : deux relevés pris à des moments différents ont déjà produit deux conclusions fausses dans ce dépôt.

**`core/` n'importe que la bibliothèque standard.** Pas de numpy, pas d'async, pas d'horloge lue à l'intérieur — le temps entre en paramètre. C'est ce qui garde le port C++ mécanique ; si ça se relâche, l'arbitrage de l'ADR s'effondre.

## Ce que fait le projet

Recadrer en direct un flux 16:9 vers un canevas 9:16 dans OBS, en cadrant sur les **corps** détectés, sans second opérateur. Distinct d'`avolo-shorts`, qui traite des fichiers et décide au 90e percentile d'un plan entier — information qui n'existe pas en direct. Le problème est **causal** ; c'est toute la différence.

## Architecture imposée par l'ADR

**Cœur / adaptateurs, dès le premier commit.**

- **Cœur** : la politique de cadrage en fonctions pures. `(détections + évènements de scène + état) → rectangle de crop`. Pas de framework, pas d'async, **état passé en paramètre**. Cette discipline est ce qui rend le port vers un plugin C++ mécanique ; si elle se relâche, le port redevient une réécriture et l'arbitrage de l'ADR s'effondre.
- **Adaptateurs** : le transport, un par cible, mince et jetable. Le premier est **obs-websocket, hors processus** — pas le plugin natif, qui reste l'objectif final.

Chaîne d'appels obs-websocket (détaillée en §3 des références) : `GetCanvasList` → `GetSceneList(canvasUuid)` → `GetSceneItemList(sceneUuid)` → `SetSceneItemTransform`. Adresser par `sceneUuid` dispense de passer le canevas.

## Décisions à ne pas rouvrir

Elles viennent d'`avolo-shorts` et ont été mesurées là-bas :

- **Corps, pas visages** — les comédiens jouent de profil ; MediaPipe donnait 5 à 30 % d'images sans détection.
- **Crop fixe à l'intérieur d'un plan**, recalculé à chaque bascule de scène. La caméra qui suit le sujet a été mesurée puis écartée. C'est ce qui rend obs-websocket suffisant : un rectangle par bascule, pas vingt-cinq par seconde.
- **Ratio choisi par plan.**
- **Une statistique de différence d'images sur la bouche est fermée** pour savoir qui parle (AUC 0,52 sur 17 927 images, le témoin de bruit bat les trois mesures). En direct la question change de nature : `InputVolumeMeters` donne le niveau de chaque entrée toutes les 50 ms — à condition que chaque comédien ait son micro sur une entrée OBS distincte.

## Go/no-go — levé le 12 septembre 2026

Les trois points sont vérifiés par la mesure sur OBS 32.2.2 / obs-websocket 5.7.4. Rejouable par `uv run python -m scripts.probe`. **La voie hors processus tient.**

Aitum Vertical 1.6.4 a depuis été installé, et le canevas vertical est **lui aussi** pilotable : crop relu à l'identique par `sceneUuid`.

**Rejoué sur la machine de production le 15 septembre 2026** — `make probe` y sort 0, Aitum Vertical installé et son canevas visible par `GetCanvasList`. Détail dans [`docs/poc-windows.md`](docs/poc-windows.md).

Deux faits qui en sortent et qui ne sont pas dans l'ADR d'origine :

- **Aucune requête `CreateCanvas` n'existe**, et l'interface d'OBS n'en crée pas non plus. L'adaptateur sait adresser un canevas, pas en créer un.
- **L'ouverture d'une caméra bloque le fil de rendu d'OBS plusieurs secondes**, et `GetSourceScreenshot` est servi par ce fil. La boucle doit survivre à une source d'images muette.

## Pièges Windows à ne pas rouvrir

Détail et mesures dans [`docs/poc-windows.md`](docs/poc-windows.md).

- **`time.perf_counter()`, jamais `time.monotonic()`** : sur Windows + Python 3.12, `monotonic()` avance par paliers de 15,6 ms.
- **Ne jamais interroger la scène de programme quand elle peut être vide** — un `GetCurrentProgramScene` sur un programme vide a coïncidé avec un plantage d'OBS.
- `setup_scene` et `probe` basculent sur la collection de scènes dédiée « AVOLO Reframe » et y laissent OBS : la collection quittée (affichée) se rouvre à la main. Ne jamais modifier les collections de production.
- **Le corpus se rejoue en `.pt`, la boucle en direct peut tourner en `.engine`** : un moteur TensorRT n'est pas garanti déterministe et est lié au couple GPU + pilote qui l'a produit.

## Mesure

Ne rien affirmer sur la cadence de détection, la latence ou un changement de modèle sans l'avoir mesuré ici : les 145 im/s d'`avolo-shorts` sont un débit **par lots**, et le direct impose un lot unitaire. Mesuré sur M3 : **16 à 27 ms par image** selon le mode, et la boucle entière coûte **27 % d'un cœur** sans rien ajouter à OBS. Tout est dans `docs/poc-mac-webcam.md`.

**Un compte de tests ne dit rien d'un défaut visuel.** Trois fois le 12 septembre 2026, une suite verte a recouvert un cadrage faux : le test exerçait une couche où le défaut n'habitait pas — requête acceptée mais jamais cadencée, cible calculée mais jamais appliquée, champs jetés à la conversion `Box → Rect`. Les trois ont été trouvés à l'œil. Vérifier l'état **réellement appliqué** (relire `cropTop` depuis un autre processus pendant que la boucle tourne), jamais le rectangle calculé.

**Écarter systématiquement la première mesure.** Le démarrage à froid a produit deux conclusions fausses dans ce dépôt : 233 ms sur un `GetSourceScreenshot` qui en fait 5, et 115 ms sur une détection qui en fait 16. Une conclusion sur n=1 est une conclusion sur du bruit.

Le corpus mesure maintenant les bascules split et le crâne coupé sur le cadre **appliqué** (`scripts.corpus --from-trace`, chiffres dans `tests/corpus/README.md`). Il manque encore un extrait avec bascules de plan annotées.

## Langue

Docs, specs et libellés d'interface en français ; **code, identifiants, commentaires et messages de commit en anglais**, sans exception.

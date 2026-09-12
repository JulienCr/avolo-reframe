# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## État du dépôt

Un PoC macOS qui tourne, et trois documents :

- [`docs/adr/0001-avolo-reframe.md`](docs/adr/0001-avolo-reframe.md) — la décision d'architecture. À lire avant d'écrire quoi que ce soit.
- [`docs/recadrage-live-references.md`](docs/recadrage-live-references.md) — le dossier de sources (API obs-websocket, état de l'écosystème, chiffres de détection).
- [`docs/poc-mac-webcam.md`](docs/poc-mac-webcam.md) — **tout ce qui a été mesuré sur machine**. À lire avant d'affirmer un chiffre.
- [`TODO.md`](TODO.md) — ce qui reste, et surtout ce qui est **tranché** : à lire avant de rouvrir un débat.
- [`tests/corpus/`](tests/corpus/README.md) — le corpus de cas de contrôle : trace de référence, images de l'extrait, outils de mesure. À comparer après tout changement de politique.

Le PoC est en **Python 3.12** (`uv`), avec Apple Vision comme détecteur. Ça ne tranche **pas** le langage du cœur en production, volontairement ouvert par l'ADR (C++ direct si la cible reste le direct seul, Rust en ABI C s'il doit aussi servir Node) : `core/` est en fonctions pures sur des `dataclass` de flottants, transposable. Ne pas choisir à la place de l'ADR — poser la question.

## La machine de mesure n'est pas la cible

Tout ce qui est mesuré à ce jour l'a été sur un **MacBook Pro M3**. La cible est **Windows 11, i9-14900K, RTX 4090**.

**Apple Vision n'existe pas sur Windows** : le détecteur du PoC est un outil de développement, pas un choix de production — là-bas ce sera YOLO11-pose en TensorRT ou Maxine. Ne jamais citer une latence de détection de ce dépôt comme valant pour la cible. Ce qui se transpose : la chaîne obs-websocket, la politique, la géométrie. Ce qui ne se transpose pas : le détecteur, `macos-avcapture` (→ `dshow_input`), et tout ce qui touche à Center Stage.

## Commandes

```bash
uv sync                                          # environnement
uv run pytest                                    # les cas de contrôle du cœur
uv run python -m scripts.probe                   # go/no-go + latences, sort 0 si tout passe
uv run python -m scripts.setup_scene --force     # (re)construit la scène ; source = la vidéo de test
uv run python -m scripts.setup_scene --force --camera   # idem, mais sur la caméra
uv run python -m scripts.run --upper-body        # la boucle
uv run python -m scripts.run --upper-body --features    # + overlay des traits dans OBS (~25 ms/image en plus)
uv run python -m scripts.corpus tests/fixtures/lab-avolo-58m22-70m00.mp4 \
    --upper-body --fps 12 --out trace.jsonl      # rejeu déterministe hors OBS
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

Aitum Vertical 1.6.4 a depuis été installé, et le canevas vertical est **lui aussi** pilotable : crop relu à l'identique par `sceneUuid`. À refaire sur la machine de production avant d'y engager quoi que ce soit.

Deux faits qui en sortent et qui ne sont pas dans l'ADR d'origine :

- **Aucune requête `CreateCanvas` n'existe**, et l'interface d'OBS n'en crée pas non plus. L'adaptateur sait adresser un canevas, pas en créer un.
- **L'ouverture d'une caméra bloque le fil de rendu d'OBS plusieurs secondes**, et `GetSourceScreenshot` est servi par ce fil. La boucle doit survivre à une source d'images muette.

## Mesure

Ne rien affirmer sur la cadence de détection, la latence ou un changement de modèle sans l'avoir mesuré ici : les 145 im/s d'`avolo-shorts` sont un débit **par lots**, et le direct impose un lot unitaire. Mesuré sur M3 : **16 à 27 ms par image** selon le mode, et la boucle entière coûte **27 % d'un cœur** sans rien ajouter à OBS. Tout est dans `docs/poc-mac-webcam.md`.

**Un compte de tests ne dit rien d'un défaut visuel.** Trois fois le 12 septembre 2026, une suite verte a recouvert un cadrage faux : le test exerçait une couche où le défaut n'habitait pas — requête acceptée mais jamais cadencée, cible calculée mais jamais appliquée, champs jetés à la conversion `Box → Rect`. Les trois ont été trouvés à l'œil. Vérifier l'état **réellement appliqué** (relire `cropTop` depuis un autre processus pendant que la boucle tourne), jamais le rectangle calculé.

**Écarter systématiquement la première mesure.** Le démarrage à froid a produit deux conclusions fausses dans ce dépôt : 233 ms sur un `GetSourceScreenshot` qui en fait 5, et 115 ms sur une détection qui en fait 16. Une conclusion sur n=1 est une conclusion sur du bruit.

La politique causale, elle, n'a toujours **presque aucun chiffre derrière elle** : `--log` produit la trace, et le corpus de cas de contrôle reste à bâtir sur le modèle de `scripts/framing/cases.ts` d'`avolo-shorts`.

## Langue

Docs, specs et libellés d'interface en français ; **code, identifiants, commentaires et messages de commit en anglais**, sans exception.

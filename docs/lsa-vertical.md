# Recadrage vertical sur les trois caméras de « LSA 2026 »

Mesuré le 20 septembre 2026, sur la machine de production (Windows 11, i9-14900K,
RTX 4090), OBS 32.2.2 et obs-websocket 5.7.4. Ce document porte ce que la mise en
place a établi. Ce qui concerne le PoC mono-source reste dans
[`poc-windows.md`](poc-windows.md).

## Ce qui a été construit

La collection de travail est **« LSA 2026 WIP »**, duplicata de la collection de
production, faite dans l'interface d'OBS. La production n'a jamais été ouverte en
écriture.

`scripts/setup_lsa.py` construit deux choses :

- dans la scène `Vertical Scene` du canevas `Aitum Vertical` (1080×1920), **neuf
  items**, trois par caméra : un plein cadre en `bounds` 1080×1920 et deux
  cellules de split en 1080×960, toutes en `OBS_BOUNDS_SCALE_INNER` ;
- sur le canevas principal, la scène **`DEBUG - REFRAM`** : trois tuiles 16:9 de
  960×540 et, par-dessus chacune, la `browser_source` d'overlay de sa boucle.

Chaque item a pour source la **scène de sortie** de sa caméra (`--- CAM Main`,
`--- CAM Cour`, `--- CAM Jardin`), pas l'input. Le recadrage hérite donc du
punch-in déjà posé en régie (Jardin 1,30×, Cour 1,34×), des variantes 70s/NB et
du miroir de `cam-jardin-comp`. Et l'image détectée est exactement l'image
recadrée.

## Comment lancer

```bash
make setup-lsa                 # construit les 9 items + DEBUG - REFRAM
make run-main ARGS="--yolo-model models/yolo11m-pose.engine --fps 15"
make run-cour ARGS="--yolo-model models/yolo11m-pose.engine --fps 15 --no-live"
uv run python tests/corpus/tools/verify_lsa_crops.py
```

Les deux arguments comptent. `reframe.toml` porte les réglages du PoC, c'est-à-dire
le `.pt` à 30 im/s : à trois boucles cela demande 1,27 voie d'inférence sérialisée
et les trois dériveraient sous leur cadence. Le `.engine` à 15 im/s est ce qui a
été mesuré ici.

## Ce que le préflight a établi

Cinq points, vérifiés avant d'écrire une ligne parce que chacun pouvait invalider
l'architecture.

| Point | Résultat |
|---|---|
| `GetSourceScreenshot` sur une **scène** du canevas principal, par nom | fonctionne, médiane 2,3 à 2,7 ms en 640 px |
| `CreateSceneItem(sceneUuid=vertical, sourceUuid=scène du canevas principal)` | **accepté** : c'était le pivot de toute l'architecture |
| `sourceWidth`/`sourceHeight` de cet item | 1920×1080, et **intacts une fois le crop posé** |
| `SetSceneItemTransform(sceneUuid=…, crop…)` | se relit à l'identique |
| `GetInputSettings` sur une scène | échoue, `code=602` |

Le troisième point est celui qui compte pour la boucle : sans item de contrôle
dans la scène verticale, la taille source se lit sur l'item plein, et il fallait
savoir que le crop ne la fausse pas. `width`/`height` sont autre chose et ne
conviennent pas.

## Charge, à trois boucles

Relevé par `GetStats`, première mesure écartée, `.engine` TensorRT, 15 im/s par
caméra.

| | à vide | trois boucles |
|---|---|---|
| `averageFrameRenderTime` médian | 0,69 ms | **0,67 ms** (max 1,45) |
| `renderSkippedFrames` | +0 sur 871 | **+1 sur 2 674** |
| `cpuUsage` d'OBS | 1,6 % | 2,2 % |
| `activeFps` | 60,0 | 60,0 |

**La crainte portée par le plan ne se matérialise pas.** On pouvait lire les
3,5 ms de `GetSourceScreenshot` comme du travail du fil de rendu, ce qui aurait
donné 32 % de ce fil à 90 captures par seconde. C'est de l'attente : le rendu ne
bouge pas, et l'écart au repos tient dans le bruit.

Côté boucles, sur 90 s et 1 350 itérations chacune, cadence tenue à **15,00 it/s**
sur les trois :

| | capture | détection | politique | traits |
|---|---|---|---|---|
| main | 2,4 ms | 7,9 ms (p90 9,3) | 0,0 | 0,0 |
| cour | 2,4 ms | 8,3 ms (p90 10,4) | 0,0 | 0,0 |
| jardin | 2,7 ms | 8,0 ms (p90 10,1) | 0,0 | 0,0 |

Trois moteurs TensorRT coûtent 46 Mio de GPU chacun. `--features` reste gratuit
hors macOS, comme documenté : il réutilise les poses déjà calculées.

## L'état « à l'antenne »

Les trois items pleins se recouvrent intégralement. Sans arbitrage, chaque boucle
imposerait la visibilité de sa caméra à chaque application, et celle du dessus
gagnerait toujours. D'où un état `live` par boucle : `--live` écrit crops et
visibilités, `--no-live` continue de capturer, détecter et publier son overlay
sans rien écrire, et la transition vers `--no-live` désactive d'abord ses items.
La bascule passe par `POST /api/action`, à côté de `pause` et `recenter`.

Vérifié en faisant passer l'antenne de Main à Cour pendant que les trois boucles
tournaient, puis relu depuis un quatrième processus :

```
   main | plein enabled=False cropL=656 cropR=656   <- sorti, crop conservé
   cour | plein enabled=True  cropL=656 cropR=656   <- entré, crop appliqué
 jardin | plein enabled=False cropL=  0 cropR=  0   <- jamais touché
```

Chaque boucle n'a touché que ses propres items. C'est ce que `select_camera_items`
garantit en filtrant par `sourceUuid` et jamais par nom.

## Preuve d'application

Crop relu **depuis un second processus** pendant qu'une boucle tournait :
`cropLeft = 656`, `cropRight = 656` sur une source de 1920, soit une colonne de
**608 × 1080**, ratio 0,5630 contre 0,5625 visé. Outil :
`tests/corpus/tools/verify_lsa_crops.py`, en lecture seule.

Preuve accessoire mais nette : OBS a été redémarré pendant la séance, pour une
installation de plugin sans rapport. Le crop était toujours là au retour, donc
sérialisé sur disque.

## Détection de source gelée

Les sources AvoCam ne reçoivent qu'une fois leur item rendu. Une caméra que rien
ne rend sert une image figée, et la boucle la recadre sans erreur ni log. La
boucle compte donc les JPEG consécutifs identiques et le signale une fois passé
trente. Sorti juste du premier coup sur les trois caméras, qui diffusaient une
mire.

Le canevas vertical porte le drapeau **`ACTIVATE`**, relevé par `setup_lsa`.
C'est lui qui doit garder les trois sources caméra actives quand la scène
verticale est rendue. À confirmer caméras allumées.

## Ce qui n'est pas vérifié

- **Le cadrage lui-même.** Les caméras diffusaient une mire, donc aucune
  détection : la politique retombe sur `default_rect` et tous les crops relevés
  ici sont ce cadre par défaut. Rien de ce document ne dit que le cadrage est
  bon, seulement qu'il est appliqué.
- **Le split**, pour la même raison. Les six items de cellule existent et sont
  adressables, aucun n'a jamais été activé par la politique.
- **La qualité.** `cam-<x>-comp` rend au canevas 1920×1080, donc un 9:16 y fait
  au plus 607×1080 et remonte à 1080×1920 par un upscale de 1,78×. Jamais
  regardé à l'œil.
- **Le comportement à l'ouverture d'une caméra**, qui bloque le fil de rendu
  d'OBS plusieurs secondes. À trois boucles, une source qui s'ouvre les gèle
  toutes les trois. Jamais éprouvé.
- **La cadence au-delà de 15 im/s.** 30 im/s par caméra n'a pas été mesuré.

# Recadrage vertical sur les caméras de « LSA 2026 »

Mesuré le 20 septembre 2026, sur la machine de production (Windows 11, i9-14900K,
RTX 4090), OBS 32.2.2 et obs-websocket 5.7.4. Ce document porte ce que la mise en
place a établi. Ce qui concerne le PoC mono-source reste dans
[`poc-windows.md`](poc-windows.md).

## Ce qui a été construit

La collection de travail est **« LSA 2026 WIP »**, duplicata de la collection de
production, faite dans l'interface d'OBS. La production n'a jamais été ouverte en
écriture.

`scripts/setup_lsa.py` construit deux choses :

- dans la scène `Vertical Scene` du canevas `Aitum Vertical` (1080×1920), **douze
  items**, trois par caméra : un plein cadre en `bounds` 1080×1920 et deux
  cellules de split en 1080×960, toutes en `OBS_BOUNDS_SCALE_INNER` ;
- sur le canevas principal, la scène **`DEBUG - REFRAM`** : quatre tuiles 16:9 de
  960×540 en grille 2×2 et, par-dessus chacune, la `browser_source` d'overlay de
  sa boucle.

Les quatre caméras sont `--- CAM Main`, `--- CAM Main Zoom`, `--- CAM Cour` et
`--- CAM Jardin`. Main Zoom est un quatrième pipeline sur la même caméra physique
que Main, avec son propre punch-in : il a ses propres items pour que le vertical
montre ce que le 16:9 montre. Chaque item a pour source un `source-clone` de la
**scène de sortie** de sa caméra, jamais l'input directement (voir « Nomenclature
des clones » ci-dessous). Le recadrage hérite donc du
punch-in déjà posé en régie (Jardin 1,30×, Cour 1,34×), des variantes 70s/NB et
du miroir de `cam-jardin-comp`. Et l'image détectée est exactement l'image
recadrée.

## Nomenclature des clones

Chaque item d'une scène s'affiche dans OBS sous le nom de sa **source**, jamais celui
de l'item : `SetSceneItemName` n'existe pas parmi les 151 requêtes de cette instance,
vérifié en le cherchant plutôt qu'en le supposant. Avec un item sourcé directement sur
la scène caméra, les douze items de `Vertical Scene` s'affichaient donc sous quatre
noms seulement (`--- CAM Main` ×4, etc.), indistincts pour l'opérateur.

`scripts/setup_lsa.py` source désormais chacun des douze items sur son propre
`source-clone` — l'idiome déjà employé pour les variantes 70s/NB et les
`SPLIT CAM *` de cette collection — un par (caméra × rôle) :

```
Cam Main - plain          Cam Main - Split ↑          Cam Main - Split ↓
Cam Main Zoom - plain     Cam Main Zoom - Split ↑     Cam Main Zoom - Split ↓
Cam Cour - plain          Cam Cour - Split ↑          Cam Cour - Split ↓
Cam Jardin - plain        Cam Jardin - Split ↑        Cam Jardin - Split ↓
```

`scripts/run.py` sélectionne chaque rôle par ce nom exact plutôt que par `sourceUuid`
plus signature de `bounds` : chaque rôle ayant désormais sa propre source, l'ambiguïté
que la signature de `bounds` existait pour lever a disparu par construction.

**Réserve assumée.** Un `source-clone` désigne sa cible par **nom**, dans son réglage
`clone`. Renommer `--- CAM Jardin` dans OBS casserait donc les trois clones de Jardin
**en silence** : ils continueraient d'exister et d'afficher leur dernière image reçue,
sans qu'aucune requête n'échoue nulle part. Le validateur d'`obs-manager` ne l'attrape
pas non plus, puisque le nom du clone lui-même ne change pas. C'est le prix de cette
option : contre des noms lisibles à l'écran, la robustesse au renommage que l'ancien
schéma tenait de l'adressage par `sourceUuid` est perdue sur la scène caméra elle-même.

## Comment lancer

```bash
make setup-lsa    # construit les 12 items + DEBUG - REFRAM
make run-lsa      # les 4 boucles + le chef de pupitre, un seul Ctrl-C les arrête
uv run python tests/corpus/tools/verify_lsa_crops.py
```

`make run-lsa` est la commande de régie. Les cibles `run-main`, `run-mainzoom`,
`run-cour` et `run-jardin` restent, pour déboguer une caméra seule.

Le réglage vit dans **`reframe.lsa.toml`**, distinct de `reframe.toml` qui porte
celui du PoC. Les deux écarts qui comptent : le moteur TensorRT plutôt que le
`.pt`, et 15 im/s plutôt que 30. À quatre boucles le `.pt` demanderait plus d'une
voie d'inférence sérialisée et les boucles dériveraient sous leur cadence. Un seul
fichier pour les quatre caméras, volontairement : la topologie vit dans
`scripts/layout_lsa.py`, et quatre fichiers dupliqueraient `[policy]` et `[split]`,
où une dérive entre caméras ne se verrait dans aucun test.

## Suivre la caméra à l'antenne

`scripts/director.py` souscrit au seul évènement `CurrentProgramSceneChanged` et
bascule l'antenne du vertical quand la régie change de caméra sur le 16:9. Il
pilote les boucles par l'API HTTP qui existe déjà.

Trois choix qui ne sont pas arbitraires :

- **Un processus séparé**, parce que `ObsWs._recv_op` boucle jusqu'à trouver
  l'opcode attendu : un évènement arrivant sur la connexion d'une boucle serait
  silencieusement jeté.
- **Aucun `GetCurrentProgramScene`, jamais.** La requête est interdite ici depuis
  qu'un appel sur un programme vide a coïncidé avec un plantage d'OBS le
  15 septembre. Le chef de pupitre ne demande donc rien au démarrage : il attend
  le premier évènement, et le vertical reste sur la caméra en place jusque-là.
- **Toute scène non mappée ne fait rien.** Seules les quatre scènes `--- CAM *`
  font basculer le vertical ; un titre, un `brb` ou une scène composite le
  laissent où il est, puisqu'il n'a que des caméras à montrer.

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

## Une image vide ne coûte pas ce que coûte une image pleine

Relevé le 20 septembre au soir, quatre boucles à 15 im/s, `--- CAM Main` allumée sur
un sujet réel pendant que Cour et Jardin servaient encore la mire « NO SIGNAL » :

| Caméra | Contenu | Détection médiane | p90 |
|---|---|---|---|
| main | un sujet | **12,5 ms** | 14,1 ms |
| mainzoom | le même sujet | 12,7 ms | 14,2 ms |
| cour | mire | 6,6 ms | 7,5 ms |
| jardin | mire | 7,1 ms | 7,9 ms |

**Le coût double dès qu'il y a quelqu'un à détecter.** Les 7,9 ms annoncées plus haut
ont été mesurées sur quatre mires, donc sur le cas le moins cher, et elles ne
disent rien de la régie en conditions réelles. À quatre caméras réellement
occupées, 60 inférences par seconde à 12,5 ms occupent 0,75 d'une voie d'inférence
sérialisée ; le p90 de 14,1 ms porte ce chiffre à 0,85. La marge existe, elle n'est
pas confortable, et c'est ce qui justifie de démarrer à 15 im/s plutôt qu'à 30.

Un relevé à quatre caméras réellement allumées reste à faire.

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
- **La quatrième boucle.** Les chiffres de charge ci-dessus ont été relevés à
  **trois** boucles, avant que Main Zoom n'ait ses propres items. Une quatrième
  ajoute 15 inférences par seconde et une capture de plus ; l'extrapolation est
  rassurante, elle n'est pas une mesure.
- **Le chef de pupitre en conditions réelles.** Sa logique de correspondance est
  testée, son comportement sur un vrai changement de scène de programme ne l'est
  pas encore.

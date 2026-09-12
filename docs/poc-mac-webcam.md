# PoC recadrage 9:16 — relevés sur le Mac

Mesures prises sur la machine de développement le **12 septembre 2026**. Elles ont
décidé de l'architecture du PoC. Chaque chiffre dit comment il a été obtenu ;
rien ici n'est extrapolé.

> ## ⚠ Cette machine n'est pas la cible
>
> Mesuré sur **MacBook Pro M3**. La cible de production est
> **Windows 11, i9-14900K, RTX 4090**. Trois choses ne se transposent pas du tout :
>
> - **Le détecteur.** Apple Vision **n'existe pas sur Windows**. Toutes les
>   latences de détection de ce document sont sans valeur pour la cible : là-bas
>   ce sera YOLO11-pose en TensorRT, ou le 3D Body Pose de Maxine. Ce qui est
>   validé ici, c'est **l'architecture et le budget**, pas le modèle.
> - **La source caméra.** `macos-avcapture` devient `dshow_input`, et tout ce qui
>   touche à Center Stage et à la Continuity Camera disparaît.
> - **Le coût.** Un 14900K et une 4090 n'ont ni les mêmes watts ni le même profil
>   thermique qu'un M3. Les chiffres de puissance ci-dessous valent pour
>   dimensionner un portable, pas la régie.
>
> Ce qui **se transpose**, et c'est l'essentiel : la chaîne obs-websocket, les
> latences de transport, la politique de cadrage, la géométrie, et le fait que
> sonder les images par le websocket ne coûte rien à OBS. `scripts/probe.py` est
> fait pour être rejoué tel quel sur la cible.

## La machine

| Élément | Valeur |
|---|---|
| Matériel de **mesure** | Apple **M3**, 8 cœurs, macOS 26.5.2 |
| Matériel **cible** | Windows 11, i9-14900K, **RTX 4090** — rien n'y a été mesuré |
| OBS | **32.2.2** — au-dessus du seuil 32.1 du go/no-go |
| obs-websocket | **5.7.4**, rpcVersion 1, port 4455, **authentification désactivée** |
| Canevas | 1920x1080 @ 60 im/s, un seul (`Main`) |
| Python | 3.12.9 via `uv` (le 3.14 du système n'a pas les roues) |

## Go/no-go de l'ADR

### 1. Versions — **levé**

OBS 32.2.2 > 32.1. Aitum Vertical n'est **pas** installé, mais les canevas sont
désormais une API du cœur d'OBS : le collection JSON porte une clé `canvases` de
premier niveau, vide ici.

### 2. `GetCanvasList` — **levé**

Le mot « partial » des notes de version ne mord pas sur la lecture. Réponse réelle :

```json
{"canvases": [{
  "canvasName": "Main",
  "canvasUuid": "6c69626f-6273-4c00-9d88-c5136d61696e",
  "canvasFlags": {"MAIN": true, "ACTIVATE": true, "SCENE_REF": true,
                  "MIX_AUDIO": true, "EPHEMERAL": false},
  "canvasVideoSettings": {"baseWidth": 1920, "baseHeight": 1080,
                          "outputWidth": 1920, "outputHeight": 1080,
                          "fpsNumerator": 60, "fpsDenominator": 1}}]}
```

### 3. Crop par `SetSceneItemTransform` sans ouvrir le filtre — **levé**

Tranché à l'exécution par comparaison de pixels, pas par lecture de doc, et sur
le **cas réel** (scène `AVOLO Reframe POC`, source `RF Cam` câblée sur la caméra
iPhone) plutôt que sur une fixture : crop envoyé à 768, relu à 768 par
`GetSceneItemTransform`, empreintes SHA-256 de la capture différentes avant et
après — **sans avoir ouvert la fenêtre de transformation dans l'interface**. Le
défaut documenté sur Move Transition ne se reproduit pas ici.

Rejouable par `uv run python -m scripts.probe`, qui sort 0 si les trois go/no-go
passent, nettoie ses propres fixtures et ne touche jamais la scène du PoC.

## Le fait que l'ADR n'avait pas prévu

**Il n'existe aucune requête `CreateCanvas`.** Sur les 151 requêtes exposées par
5.7.4, `Create*` donne exactement : `CreateRecordChapter`, `CreateSourceFilter`,
`CreateSceneCollection`, `CreateScene`, `CreateSceneItem`, `CreateProfile`,
`CreateInput`.

Et l'interface d'OBS 32.2.2 n'en crée pas non plus : `locale/en-US.ini` ne connaît
« Additional Canvas » que pour le multitrack. **L'adaptateur sait adresser un
canevas, pas en créer un** — créer le canevas vertical reste le travail d'Aitum
Vertical ou de l'interface. Sans conséquence sur l'ADR, qui prévoit un canevas
existant, mais à savoir avant d'automatiser une mise en place.

## Latences obs-websocket

n=30 (n=15 pour le 1080p), régime établi, **première requête écartée**. Scène vide
et noire : ces chiffres sont un **plancher**, à re-mesurer caméra active.

| Requête | min | médiane | p90 | max |
|---|---|---|---|---|
| `GetVersion` (plancher protocole) | 0,2 | **0,3** | 0,7 | 1,0 |
| `GetSceneItemList` | 0,1 | **0,1** | 0,2 | 0,3 |
| `GetSourceScreenshot` 640x360 jpg q75 | 2,8 | **5,0** | 8,0 | 8,3 |
| `GetSourceScreenshot` 1920x1080 jpg q75 | 13,8 | **14,0** | 23,0 | 26,1 |

En rafale de 60 requêtes concurrentes : **11 188 req/s** sur `GetVersion`,
**517 req/s** sur un screenshot 640x360.

> **Piège payé pendant ce relevé.** Le premier échantillonnage donnait 233 à
> 598 ms sur le screenshot et concluait que lire les images par le websocket était
> impossible. C'était du démarrage à froid. Le régime établi est 50 fois plus
> rapide. **Écarter la première requête, et ne jamais conclure sur n=1.**

**Conséquence architecturale.** Les images passent par le websocket, avec un
facteur 5 à 10 de marge sur une cadence de 10 à 15 Hz. Ça supprime le conflit
d'accès à la caméra avec OBS sur macOS, et ça généralise : le même chemin lira
une carte d'acquisition ou un flux NDI sans changer une ligne.

`imageData` revient en **data URI** (`data:image/jpg;base64,...`), pas en base64
nu — il faut couper jusqu'à la première virgule.

## Caméras

`macos-avcapture` (« Video Capture Device »), propriété `device`, valeurs en UUID :

| Nom | UUID |
|---|---|
| Caméra FaceTime HD | `98BA9681-B85F-4A7F-A15E-9AB9CDBF5350` |
| Caméra de « iPhone de Julien » | `CB715948-CAD2-4E39-A4D7-08B300000001` |
| Caméra Desk View de « iPhone de Julien » | `CB715948-CAD2-4E39-A4D7-08B300000002` |

Les deux entrées iPhone ne diffèrent que par le dernier chiffre : **sélectionner
par UUID, jamais par préfixe**. Le nom affiché contient par ailleurs des espaces
insécables (`\xa0`) dans les guillemets français.

### Center Stage

Si Center Stage est actif, la caméra recadre déjà toute seule et **se bat contre
le tracker** — le PoC mesurerait alors n'importe quoi. L'état n'est lisible ni par
obs-websocket (`Warning.Effect.CenterStage` est un libellé d'interface), ni par
`defaults`, ni dans les journaux d'OBS. Il l'est par AVFoundation :

```python
AVCaptureDevice.isCenterStageEnabled()      # preference globale
device.isCenterStageActive()                # par peripherique
```

L'`uniqueID` d'AVFoundation est **exactement** la valeur `device` d'OBS, donc le
recoupement est direct. Lecture sans demande d'autorisation. Au 12 septembre 2026,
désactivé sur les trois périphériques.

## Qualité selon la hauteur du crop

Source 1920x1080 vers une boîte de sortie 540x960 :

| Hauteur de crop | Fenêtre source | Effet |
|---|---|---|
| 1080 | 608x1080 | downscale 0,89x — propre |
| **960** | 540x960 | **mapping 1:1, qualité maximale** |
| 720 | 405x720 | upscale 1,33x — légèrement doux |
| 540 | 304x540 | upscale 1,78x — visiblement doux |

D'où `--min-crop-h 960` par défaut. La contrepartie est une plage de zoom étroite
(1,125x) : le PoC démontre surtout le **pan**.

## Latence de détection à lot unitaire

Le chiffre que l'ADR interdisait d'extrapoler des 145 im/s relevées par lots sur
GPU NVIDIA. Mesuré ici sur M3, caméra iPhone active, cinq itérations :

| Étape | Première | Régime établi |
|---|---|---|
| Capture (`GetSourceScreenshot` 640px, **caméra active**) | 8,2 ms | **8 à 20 ms** |
| Détection Apple Vision, une image à la fois | 114,9 ms | **22 à 27 ms** |

Deux choses à en retenir. La capture sur caméra active coûte **2 à 4 fois** le
plancher mesuré sur scène vide (2 à 5 ms) : la référence à utiliser est celle-ci.
Et la première détection coûte cinq fois les suivantes — même piège de démarrage
à froid que sur le websocket, à écarter systématiquement.

Budget par itération : **~35 ms**, soit un plafond d'environ 28 Hz. Les 12 Hz
visés laissent un facteur 2,3.

## Une caméra qui s'ouvre bloque le fil de rendu d'OBS

Relevé par accident, et c'est le genre de chose qui ne se retrouve pas deux fois.
Pendant la fenêtre où une caméra Continuity a été câblée sur une entrée OBS :

| | nominal | pendant l'ouverture |
|---|---|---|
| `GetSourceScreenshot` 1920px, médiane | 14 ms | **1 544 ms** |
| idem, maximum | 26 ms | **4 256 ms** |
| Rafale de 60 screenshots 640px | 441 req/s | **5 req/s** |

Un tir a même dépassé le délai de garde de 5 s du client
(`timed out waiting for op 7`). Vu une fois sur cinq exécutions, puis retour au
nominal sans rien changer.

`GetSourceScreenshot` est servi par le **fil de rendu** d'OBS, que l'ouverture
d'un périphérique bloque. Ce n'est donc pas un défaut du client, et augmenter le
délai de garde ne réglerait rien — **la boucle doit survivre à une source
d'images muette pendant plusieurs secondes**. C'est exactement ce que couvre le
maintien du dernier cadre (`--hold-ms`) ; en production, un branchement à chaud
ou une reconnexion de caméra produira le même trou.

## Premier signal sur Apple Vision

`VNDetectHumanRectanglesRequest` (corps entiers, `upperBodyOnly=False`) sur une
photo de scène de théâtre à **quatre comédiens assis** : **deux détections**,
confiances 0,67 et 0,62. Boîtes cohérentes avec la position réelle des têtes et
des pieds — l'orientation a été vérifiée sur l'image, pas supposée (Vision
normalise avec l'origine en **bas** à gauche ; le PoC convertit en haut à gauche).

Sans conséquence sur ce PoC, qui ne cadre qu'une personne. Mais c'est le même
type de manque que l'ADR a mesuré sur MediaPipe (5 à 30 % d'images sans
détection) avant de choisir YOLO. **Ne pas conclure d'une image** : à re-mesurer
contre YOLO11n-pose sur un corpus, avant toute idée d'emmener Vision plus loin
que la machine de développement.

### Corps entier contre buste : ce que ça vaut vraiment

**Un cas isolé qui a failli devenir une règle fausse.** Sur une image de webcam,
sujet assis en cadrage serré, bas du corps masqué par de la literie :

| Mode | Détections | Latence |
|---|---|---|
| `upperBodyOnly=False` — corps entier | **0 sur 8** | 27 à 30 ms |
| `upperBodyOnly=True` — buste | **8 sur 8**, score 0,76 à 0,78 | **15 à 16 ms** |

D'où la conclusion, énoncée un peu vite, que le mode corps entier serait
« aveugle à un sujet assis ». **Mesuré ensuite sur 1 200 images du vrai
extrait, c'est faux :**

| Tranche de l'extrait | Corps entier | Buste |
|---|---|---|
| 60-90 s — plan serré, une personne | **100 %** | 100 % |
| 550-650 s — plan large, un debout un assis, partiellement coupé | **91,7 %** | **98,9 %** |
| dont images sans **aucune** détection | 8,3 % | 1,1 % |

`VNDetectHumanRectanglesRequest` en mode corps entier **n'exige pas de voir les
jambes** : il répond sur un corps partiellement cadré. Le mode buste est
**mesurablement plus robuste**, d'un facteur 7 sur le taux d'images perdues — ce
qui reste une bonne raison de le préférer — mais il n'est **pas** la différence
entre voir et ne rien voir.

**La leçon dépasse ce réglage.** Huit images d'un seul sujet, dans une posture
particulière, ont produit une règle générale fausse qui s'est propagée en
quelques minutes jusque dans un `CLAUDE.md`. Un corpus de 1 200 images l'a
défaite. C'est exactement la dette que l'ADR ouvre en réclamant des cas de
contrôle, et la démonstration qu'elle n'est pas théorique. Une fois le buste détecté, la politique
calcule un cadre centré en x=1442 contre 960 par défaut — **482 px de
déplacement**, écart de 0,793 contre une zone morte de 0,12, donc un recadrage
franc.

**Ça ne rouvre pas « corps, pas visages ».** `upperBodyOnly` reste un détecteur
de **torse**, qui tient de profil ; ce n'est pas un détecteur de visage, et
l'échec mesuré de MediaPipe sur les comédiens de profil ne s'y transpose pas.

Aucun des deux modes n'est bon partout, d'où un drapeau et non un nouveau défaut
en dur : le buste cadre la tête et le torse, ce qui est faux quand on veut un
comédien debout **en entier**. Le mode est consigné dans `--log` et dans le
résumé de fin, pour qu'une trace dise toujours lequel a produit ses chiffres.

## Ce qu'Apple Vision rend vraiment

Mesuré le 12 septembre 2026 sur la caméra en direct, JPEG de 640 de large.
Médianes en **régime établi**, première mesure écartée.

| Requête | Médiane | Premier appel | Ce qu'elle rend |
|---|---|---|---|
| `VNDetectFaceRectangles` (révision 3) | **6,3 ms** | 284 ms | boîte + **yaw / pitch / roll** en radians |
| `VNDetectFaceLandmarks` | **7,2 ms** | 372 ms | 76 points : yeux 6+6, **pupilles** 1+1, lèvres externes 14 et internes 6, nez 8, sourcils, contour 17 |
| `VNDetectHumanBodyPose` | **6,3 ms** | **7 114 ms** | **19 articulations** avec confiance |
| `VNDetectHumanRectangles` (celui du PoC) | 15 à 16 ms | 115 ms | une simple boîte englobante |

> **Ce tableau a produit une conclusion fausse, gardée ici comme avertissement.**
> J'en avais tiré que « la pose coûte deux fois moins que le détecteur de
> boîtes ». Faux : les 15-16 ms de `VNDetectHumanRectangles` ont été relevés
> **pendant que Spotlight consommait deux à trois cœurs**, et les 6,3 ms de la
> pose **après** l'avoir coupé. Deux nombres pris dans des conditions
> différentes, comparés comme s'ils l'étaient.

### Le tête-à-tête honnête

Mêmes 25 images, même processus, machine au calme, préchauffage écarté :

| Détecteur | Médiane | p90 |
|---|---|---|
| `vision` (`VNDetectHumanRectangles`) | 6,96 ms | 9,27 ms |
| **`vision-upper`** | **6,06 ms** | 6,63 ms |
| `pose` (`VNDetectHumanBodyPose`) | 7,15 ms | 9,22 ms |
| `pose-bust` | 8,59 ms | 11,59 ms |

**La pose est légèrement plus lente**, pas deux fois plus rapide. Les quatre se
tiennent entre 6 et 9 ms : à cette échelle, le détecteur n'est pas le poste qui
décide de la cadence.

Ce qui justifie quand même la pose comme défaut, et c'est de la **richesse** et
non de la vitesse : 19 articulations, l'angle des épaules, 16 des 17 points COCO.
C'est ce dont la politique a besoin pour l'air devant le regard et pour
`torsoBounds`. `--detector vision` reste disponible pour comparer.

**Règle qui sort de là** : ne jamais comparer deux mesures prises à des moments
différents. Une comparaison ne vaut que sur les **mêmes images, dans le même
processus, à la suite**.

Le premier appel à la pose coûte **7 secondes** — chargement du modèle. Tout
consommateur doit préchauffer à la construction, sinon il paraît planté.

### La topologie est celle de COCO, à un point près

Correspondance vérifiée article par article sur une détection réelle :

| Vision | COCO-17 |
|---|---|
| `left_eye` `right_eye` `left_ear` `right_ear` | idem |
| `left_shoulder_1` `right_shoulder_1` | épaules |
| `left_forearm` `right_forearm` | coudes |
| `left_hand` `right_hand` | poignets |
| `left_upLeg` `right_upLeg` | hanches |
| `left_leg` `right_leg` | genoux |
| `left_foot` `right_foot` | chevilles |
| `head` `neck_1` `root` | **en plus** des 17 |

**16 des 17 points COCO sont couverts** ; seul `nose` manque, remplacé par `head`.
Donc `torsoBounds` et la définition `bust` d'`avolo-shorts`, qui reposent sur les
épaules et les hanches, **se transposent** — contrairement à ce qu'on pouvait
craindre, et contrairement à la réserve que l'ADR formule sur les 34 points de
Maxine. Un réglage de géométrie mesuré ici garde donc du sens ailleurs.

Attention : la constante de groupe est `VNHumanBodyPoseObservationJointsGroupNameAll`,
dont la valeur est la chaîne **`'VNIPOAll'`**. Passer `'all'` renvoie `None` **sans
erreur exploitable** — échec silencieux.

### L'articulation `head` n'est pas le sommet du crâne

Mesuré sur 12 poses réelles de l'extrait, coordonnées normalisées, origine en haut :

| Point | y |
|---|---|
| Milieu des yeux | 0,2340 |
| **`head`** | **0,2660** |
| `neck_1` | 0,3478 |
| Point le plus haut rendu, toutes articulations confondues | 0,2315 |

**`head` se situe *sous* les yeux** : c'est un point du visage, pas le crâne. Et
le point le plus haut que le détecteur rend, toutes articulations confondues, est
au niveau des yeux et des oreilles.

Le sommet réel du crâne se trouve environ **une distance yeux-nuque au-dessus des
yeux**, soit ici ~0,10 en unités normalisées — **10 % de la hauteur d'image
au-dessus de tout ce que le détecteur connaît**.

**Conséquence directe** : une boîte englobante bâtie sur les articulations
commence franchement sous le sommet de la tête, et un cadre calculé dessus
**coupe le crâne**. Constaté à l'écran avant d'être expliqué.

Le crâne doit donc être **estimé** — `crown_y ≈ eye_y - (neck_y - eye_y)` — et
« ne jamais couper la tête » doit être un **invariant du cadre**, pas une
conséquence heureuse de la marge. Quand cet invariant entre en conflit avec la
ligne de regard, c'est le crâne qui gagne : une tête coupée est un défaut plus
grave qu'une ligne de regard à quelques pour cent près.

### Deux pièges de coordonnées

- **Vision normalise avec l'origine en bas à gauche.** Toute sortie doit être
  convertie en haut à gauche, sinon l'overlay est le miroir vertical de la
  réalité — et ça reste plausible à l'œil, donc ça ne se voit pas.
- **`VNFaceLandmarkRegion2D.normalizedPoints()` est normalisé sur la boîte du
  visage, pas sur l'image.** Mesuré : l'œil gauche sort à x entre 0,157 et 0,283
  alors que la boîte du visage commence à x=0,806. Utiliser plutôt
  `pointsInImageOfSize_((w, h))`, qui rend de vraies coordonnées image — mais
  toujours avec l'origine en bas à gauche, donc le retournement reste dû.
- **Et le piège qui tue le processus : `normalizedPoints()` rend un
  `objc.varlist`, pas une séquence.** C'est un pointeur nu vers un tableau de
  `CGPoint` dont pyobjc ignore la longueur. `len()` lève `TypeError`, et
  **l'itérer sort du tableau et provoque un SIGSEGV** — pas d'exception, pas de
  trace, le processus meurt. Deux plantages relevés ici le 12 septembre 2026 :

  ```
  EXC_BAD_ACCESS (SIGSEGV) -- KERN_INVALID_ADDRESS
    _objc.cpython-312-darwin.so   pythonify_c_struct
    Python                        iter_iternext
  ```

  Le seul accès sûr est **`region.normalizedPoints().as_tuple(region.pointCount())`**,
  et la même règle vaut pour `pointsInImageOfSize_`. Traiter tout `varlist` rendu
  par pyobjc comme dangereux tant qu'il n'est pas borné.

  Test qui attrape le retournement là où l'œil échoue : vérifier que les yeux
  ressortent **au-dessus** de la bouche dans la convention haut-gauche.

### Ce que ça ouvre pour le cadrage

- **Orientation du buste** par la ligne d'épaules, et **orientation de la tête**
  par le yaw — ici -47°. De quoi implémenter la règle de l'**air devant le
  regard** : décaler le cadre du côté où le sujet regarde. Règle classique de
  cadrage, et gratuite à ce prix.
- **Ouverture de bouche** par les lèvres internes et externes. À manier avec
  précaution : l'ADR a fermé « une statistique de différence d'images sur la
  bouche » (AUC 0,52 sur 17 927 images, le témoin de bruit battant les trois
  mesures). Une mesure d'ouverture par repères est une **autre** grandeur, pas
  celle qui a échoué — mais l'antécédent est mauvais, et la voie audio
  (`InputVolumeMeters`, micros séparés) reste bien plus prometteuse.

## Coût processeur

M3, 8 cœurs — la machine saturée vaudrait 800 % d'un cœur. Mesuré par différence
de temps processeur cumulé sur 12 s de régime établi, arbre de processus complet
(`uv run` lance le vrai processus en enfant : mesurer le parent seul donne 0 %).

| | % d'un cœur | % de la machine |
|---|---|---|
| OBS seul, caméra active | 35 | 4,4 |
| OBS pendant la boucle | 29 à 36 — **inchangé, dans le bruit** | 4,0 |
| La boucle, à 15 im/s en mode buste | **27** | **3,4** |

Deux conclusions. **Le sondage d'images par `GetSourceScreenshot` ne coûte rien à
OBS** : c'était le risque principal de l'architecture hors processus, il ne se
matérialise pas. Et les 27 % de la boucle se recoupent avec les latences
mesurées — 15 im/s x 16 ms de détection = 240 ms de calcul par seconde, soit 24 %
d'un cœur. Rien d'inexpliqué.

### Puissance, GPU et ANE compris

`sudo powermetrics`, 8 échantillons d'une seconde, **machine au calme** :

| | CPU | GPU | ANE | Total |
|---|---|---|---|---|
| OBS seul, caméra active | 1 221 mW | 1 346 mW | 0 mW | 2 566 mW |
| + la boucle à 15 im/s | 1 838 mW | 1 623 mW | 63 mW | 3 523 mW |
| **Coût du suivi** | **+617** | **+277** | **+63** | **+957 mW** |

Moins d'un watt pour un suivi de corps complet à 15 im/s. Vision s'exécute bien
sur l'**ANE** — 0 mW au repos, 63 mW en charge — et l'attribution par processus
confirme que le processus Python ne consomme **aucun** temps GPU : les +277 mW de
GPU sont le rendu d'OBS, pas la détection.

> **Piège payé ici aussi.** Une première mesure imputait **+4,8 W** au processeur.
> C'était `mdworker_shared` — Spotlight indexant le `.venv` fraîchement installé,
> à 2 170 ms/s. Une mesure de puissance à l'échelle de la machine impute au
> coupable le plus proche, pas au vrai. **Toujours attribuer par processus**
> (`powermetrics --samplers tasks`) avant de conclure, et vérifier que la machine
> est au calme.

## Ce qui déclenche réellement les recadrages

Rejeu complet, détecteur pose, chaque commande comptée par motif et par mode :

| Motif | Mode | Commandes | Part |
|---|---|---|---|
| **crâne** | split | **152** | **42,8 %** |
| ordinaire | split | 102 | 28,7 % |
| ordinaire | simple | 96 | 27,0 % |
| crâne | simple | 5 | 1,4 % |
| | | **355** | |

**En mode split, 60 % des recadrages sont forcés par la contrainte de crâne** —
152 sur 254. C'est donc une contrainte dure qui travaille en permanence, pas une
géométrie devenue instable.

La distinction a une conséquence pratique : baisser `zoom_dead_zone` ou
`dead_zone` ne ferait pas baisser ce taux, puisque ces recadrages **traversent**
la zone morte par construction. Ce qui l'abaisserait, c'est un cadre plus
généreux au-dessus de la tête — `crown_margin` — au prix de plans plus larges.
Le taux de recadrage est donc un **réglage de composition**, pas un réglage de
stabilité, et le confondre avec le second conduirait à tourner le mauvais bouton.

## Le matériel de test

`tests/fixtures/lab-avolo-58m22-70m00.mp4` — extrait d'une captation « Le Lab
Avolo ». Artefact local, **non versionné**. Vérifié par `ffprobe` :

| | |
|---|---|
| Durée | **698,000 s** (11 min 38 s) |
| Image | 1920x1080, h264, **60 im/s**, 41 880 images |
| Audio | piste AAC présente |
| Taille | 196 Mo |

Source à **60 im/s** : un échantillonnage à 12 im/s décime donc 5:1. À dire dans
l'en-tête de toute trace, sinon les `pts_ms` se lisent comme si chaque image de
la source avait été vue.

### Deux usages, et un seul est rejouable

- **Source média dans OBS** (`ffmpeg_source`, `looping` vrai) — pour voir et
  régler à l'œil. **Non reproductible** : la boucle échantillonne sur l'horloge
  murale pendant qu'OBS lit le fichier sur la sienne, donc deux exécutions ne
  voient pas les mêmes images, et la politique étant temporelle, les commandes
  diffèrent. Un corpus bâti là-dessus aurait l'air rejouable sans l'être, ce qui
  est pire que pas de corpus.
- **Harnais hors OBS** (`scripts/corpus.py`) — décode le fichier directement et
  pilote la politique en **temps simulé**, le cœur prenant déjà `now_ms` en
  paramètre.

**Critère d'acceptation atteint** : deux exécutions de la même tranche donnent une
sortie **identique au bit près** (sha256 `6a6f3d97bdd6de81…`). Ça vaut aussi
comme relevé sur le détecteur — **Apple Vision est déterministe** pour une entrée
donnée, ce qui n'allait pas de soi et qu'aucune documentation n'affirme.

C'est ce qui rend le réglage de la politique mesurable : on peut changer un seul
paramètre et attribuer la différence à ce paramètre, au lieu de la confondre avec
le bruit d'échantillonnage.

### Le cas dur qu'il contient

**Deux sujets assis, adossés aux deux bords opposés du 16:9**, un fauteuil vide
entre eux. Aucun rectangle 9:16 ne les contient sans un dézoom qui vide le
cadrage de son sens. Une politique fondée sur l'**union** des détections y tombe
mécaniquement sur le fauteuil vide : l'union couvre presque toute la largeur,
`fit_ratio` grandit pour tenir le 9:16, `clamp_to_source` épingle en pleine
hauteur et centré.

C'est le même mécanisme qu'un sujet allongé remplissant la largeur, relevé plus
haut. **Ce n'est pas un défaut du code, c'est la limite de l'union**, et l'ADR
nomme déjà la réponse : le mode `split`, une personne par cellule.

Chiffré en exécutant la politique sur des boîtes relevées sur une image de
l'extrait — gauche `(270, 165, 480, 825)`, droite `(1290, 90, 450, 900)`, avec
`PolicyParams()` par défaut :

| Entrée | crop obtenu | centre x |
|---|---|---|
| **les deux sujets** | 607,5 x **1080,0** | **1005** |
| le gauche seul | 566,4 x 1006,9 | 510 |
| le droit seul | 597,4 x 1062,0 | 1515 |

Avec les deux, la hauteur est épinglée à **1080, le maximum possible** — le cadre
est aussi dézoomé que la source l'autorise — et son centre tombe à 1005, à 45 px
du centre géométrique de l'image : **sur le fauteuil vide**. Chaque sujet pris
seul donne au contraire un cadrage serré et juste.

Et c'est arithmétique, donc irréparable par réglage : l'union fait 1 470 px de
large, `fit_ratio` réclame donc `h = 1470 / 0,5625 = 2613`, que `clamp_to_source`
rabat à 1080. **Aucune valeur de `margin`, `min_crop_h` ou `dead_zone` ne fait
tenir deux sujets distants de 1 470 px dans un rectangle 9:16.**

Piège de lecture à éviter : un cadre qui ne bouge jamais parce que la cible est
toujours le centre de l'image est indiscernable, dans un résumé, d'une politique
qui fonctionne. Toute mesure sur cet extrait doit distinguer les deux.

### La dégénérescence, mesurée de bout en bout

Rejeu du **fichier entier** : 8 376 images à 12 im/s, détection 99,5 %,
114 recadrages, sortie identique au bit près entre deux exécutions
(sha256 `8516add78700…`).

| Grandeur | Valeur |
|---|---|
| Images voyant **2 corps ou plus** | **45,1 %** (3 778 / 8 376) |
| dont les deux boîtes sont réellement disjointes | 99,7 % |
| **Centre du crop tombant dans l'écart entre les deux** | **98,0 %** |
| tombant sur l'un des deux sujets | 0,6 % |
| Largeur de l'union quand les deux sont vus, médiane | **1 571 px** — 82 % de la largeur |
| Écart entre les deux sujets, médiane | 749 px |

**Sur 98 à 99,6 % des images où deux personnes sont vues, le cadre 9:16 est posé
sur le vide entre elles.** Ce n'était plus une conjecture arithmétique : c'est
relevé sur le matériel réel, et sur **39 séquences distinctes** réparties dans
tout l'extrait, pas sur un plan isolé.

### La contrainte qui explique tout

**Un crop 9:16 dans une source 1920x1080 fait au plus 607,5 px de large — 31,6 %
de l'image.** Tout sujet plus large qu'un tiers du cadre est donc impossible à
contenir, quelle que soit la politique.

### Attention : « cible figée » n'est pas « cadrage raté »

Le harnais relève **89,8 %** d'images à cible figée, soit le double du taux
d'images à deux corps. L'écart n'est pas une aggravation, c'est une confusion de
deux cas très différents :

| Cas | Figée | Centre du crop | Verdict |
|---|---|---|---|
| 1 sujet, plan serré (700x900) | **oui** | 950 = **son propre centre** | cadrage **correct** |
| 1 sujet, plan large (300x850) | non | 950 | correct |
| 2 sujets écartés | oui | 1005 | **cadre le vide** |

Sur un sujet seul trop large, le cadre se centre sur lui et lui coupe les côtés :
c'est du cadrage ordinaire, pas un échec. Le défaut n'existe que quand l'union de
**deux** sujets déplace le centre vers l'espace qui les sépare.

**Le chiffre qui compte est donc ~45 %** et non 89,8 %. Partage mesuré sur les
8 337 images avec détection, une fois les deux cas séparés :

| | Part |
|---|---|
| Cible figée, **centrée sur un sujet** — bénin | 44,7 % |
| Cible figée, **centrée entre deux sujets** — le vrai défaut | **45,1 %** |

Une mesure qui confond les deux crie au loup sur presque tout plan serré, et sera
ignorée en une semaine. Le harnais alerte désormais sur la seconde ligne
seulement : sur l'extrait entier elle reste sous le seuil, et l'alerte se
déclenche là où elle doit, sur les fenêtres à deux sujets (554-579 s, 263-284 s),
où le taux problématique atteint 100 %.

### Et un troisième mode de défaillance, non anticipé

Le cadre n'est pas figé pour autant — il **oscille**, et c'est pire qu'un cadrage
fixe et faux. Son centre balaie **1 306 px** sur les 1 920 de large (écart-type
188 px), parce que seules 45 % des images voient les deux sujets : le reste n'en
voit qu'un.

Ces alternances ne sont pas du scintillement image par image — elles durent
**4,85 s en moyenne**. La politique cadre donc le vide quand les deux sont
détectés, bascule vers celui qui reste quand l'autre est perdu, puis revient.
Un mouvement ample, lent, parfaitement visible, et sans aucun sens éditorial.

La zone morte et le temps de confirmation ne protègent pas de ça : ils filtrent
le bruit court, pas une bascule d'état qui tient cinq secondes.

### Ce que cet extrait éprouve vraiment

**Correction.** J'avais écrit qu'il ne contenait « aucune bascule de scène, aucune
entrée ni sortie de champ, aucun sujet debout ». C'est faux, et ça venait d'un
jugement porté sur **une seule image**. Un échantillonnage de 13 images réparties
sur toute la durée montre au moins **quatre compositions distinctes** :

| Moment | Composition |
|---|---|
| 0 s | plan large à deux, assis aux bords opposés |
| 60-360 s | gros plan serré sur un seul sujet, l'autre absent ou en lisière |
| 420-540 s | **les deux debout**, plan large |
| 570-690 s | monologue debout, le second sujet entrant et sortant du champ |

Il y a donc des sujets debout, des entrées et sorties de champ, et ce qui
ressemble à de vraies coupes caméra. Le gradient d'activité le confirme :
2 recadrages sur la tranche 0-90 s contre **34** sur 600-690 s, avec une
amplitude médiane de **675 px** sur la tranche active.

**Conséquence de lecture** : les chiffres ci-dessus décrivent un comportement
**à travers des compositions mélangées**, pas un régime permanent contrôlé. Une
partie des oscillations est probablement la politique réagissant à des coupes
qu'elle ne sait pas voir — le harnais n'a aucun signal de bascule de scène et les
traverse par le chemin ordinaire zone morte / confirmation / transition.

Ce qui manque encore : un extrait où les bascules sont **connues et annotées**,
pour séparer ce que la politique fait des coupes de ce qu'elle fait du bruit de
détection.

Il porte par ailleurs des **sous-titres incrustés** en bas d'image, qu'un cadrage
9:16 coupera en deux. Attendu, et documenté comme contrainte du domaine : AWS
l'écrit pour son propre smart crop (« the smart crop might cut them off
awkwardly », dossier de sources §4). À ne pas confondre avec un défaut de la
politique.

## Deux relevés sur les détecteurs, qui ne se devinaient pas

Rejeu complet de l'extrait, tranche 550-650 s, 1 200 images par mesure.

**La robustesse ne se dégrade pas au même rythme selon le mode.** En divisant la
largeur de décodage :

| Largeur de décodage | Corps entier | Buste |
|---|---|---|
| 640 | 91,7 % | 98,9 % |
| 416 | 85,2 % | 97,2 % |
| **perte** | **-6,5 pts** | **-1,7 pt** |

Le mode buste tolère donc bien mieux un décodage bon marché. Un levier
d'optimisation qui se paie en détection dans un mode et presque pas dans l'autre.

**Et les échecs des deux modes sont disjoints.** Sur les 100 images où le mode
corps entier ne détecte rien, **6 seulement** échouent aussi en mode buste — donc
**94 % des échecs du corps entier sont des images que le buste traite très bien**.
Ce ne sont pas des images intrinsèquement difficiles : les deux modes sont
**complémentaires**. Faire tourner les deux et fusionner apporterait du signal
réel, pas de la redondance. Contre-intuitif, et mesuré.

**Coût d'un rejeu complet** : 75 s pour 698 s de vidéo, soit **9 fois plus vite
que le temps réel**. Un balayage de paramètres sur tout l'extrait est donc
praticable — c'est exactement ce dont le réglage de la politique a besoin.

## `SERIAL_FRAME` ne cadence pas — et pourquoi ça a échappé

**La transition lissée était une coupe.** Le nom de l'`executionType` promet
qu'OBS applique une requête par image rendue ; il n'en fait rien. Mesuré sur de
vraies `SetSceneItemTransform`, scène en direct :

| Mode | Requêtes | Temps réel | Si cadence 60 Hz |
|---|---|---|---|
| `SERIAL_FRAME` | 19 | **17,7 ms** | 317 ms |
| `SERIAL_FRAME` | 60 | **17,3 ms** | 1 000 ms |
| `SERIAL_REALTIME` | 60 | 11,6 ms | 1 000 ms |

Un lot entier est absorbé dans **une seule image rendue**. Les 19 pas
d'interpolation arrivaient donc tous ensemble : à l'écran, une coupe franche.

**Comment l'erreur a tenu si longtemps.** La vérification demandée était « est-ce
que 5.7.4 accepte `SERIAL_FRAME` ? ». La réponse était oui, et elle a été
rapportée honnêtement. Mais **la question était mal posée** : vérifier qu'une
requête est *acceptée* ne dit rien de *quand* elle est exécutée. L'affirmation a
ensuite traversé trois documents, dont l'ADR, où elle servait à renforcer un des
arguments de la décision.

C'est l'utilisateur qui l'a vue en trois secondes, en regardant l'écran.

**Règle** : une propriété temporelle se vérifie par un chronomètre, jamais par un
code de retour. Et quand une API porte un nom qui décrit un comportement, le nom
n'est pas une preuve.

### Le correctif, et ce qu'il coûte

Cadencer depuis l'appelant, à 60 Hz, sur un **fil séparé avec sa propre connexion
websocket** — le client est synchrone et son compteur d'identifiants n'est pas
protégé, donc deux fils sur une même instance entrelaceraient les trames.

Mesuré après correction, transition de bord à bord :

| Demandé | Réel | Cible atteinte |
|---|---|---|
| 200 ms | 201 ms | oui |
| 300 ms | 290 ms | oui |
| 600 ms | 603 et 599 ms | oui |
| 900 ms | 875 ms | oui |

Écart inter-tic moyen **16,66 ms**, soit du 60 Hz exact, et la cadence tient même
sous contention artificielle. La boucle de détection n'est pas ralentie : 83,8 ms
d'intervalle pendant une transition contre 84,9 ms hors transition.

Coût négligeable — deux transformations par tic, plancher protocole à 0,3 ms —
mais ça contredit l'idée qu'une transition ne coûte qu'un message, et c'est une
contrainte de plus pour le futur plugin.

### Un second défaut, trouvé par une mesure qu'on croyait mauvaise

Les premières vérifications externes montraient des transitions courtes
n'atteignant pas leur cible — 90 px parcourus sur 1 312. L'explication commode
était que le sondage, synchrone à 75 Hz sur le même OBS, affamait les tics.

Elle était à moitié vraie, et la moitié fausse cachait un vrai bug : **`jump()`
ne mettait à jour la position courante qu'au tic suivant**, jusqu'à 16,7 ms plus
tard. Un `play()` enchaîné partait donc d'un rectangle périmé — exactement le
motif du harnais de mesure.

Deux choses en sortent. La garantie « le dernier tic tombe sur la cible » est
désormais **structurelle** (retour par identité à `t >= 1`, pas un calcul flottant
qu'on espère exact). Et surtout : **une mesure imparfaite qui trouve un vrai
défaut a payé sa place.** Le réflexe d'attribuer une anomalie à son propre
instrument est bon la plupart du temps, et c'est précisément pour ça qu'il faut
s'en méfier.

## 22 tests au vert, et la politique cassée

Le mode `split` a été livré avec sept tests neufs, tous verts, et un défaut qui
annule la raison d'être de la politique : une fois engagé, il ré-émettait une
commande **à chaque image**, sans zone morte, sans confirmation, sans transition.

Reproduction, dans le cœur pur, sans OBS :

```python
p = PolicyParams()
g, d = Rect(270, 165, 480, 825), Rect(1290, 90, 450, 900)   # immobiles
s = initial_state(p)
for i in range(120):                                        # 10 s a 12 im/s
    s, c = step(s, [g, d], i * 83.3, p)
```

**112 commandes** pour deux sujets parfaitement immobiles, toutes en
`duration_ms=0.0`. En mode unique, la même entrée en produit **zéro**.

Les sept tests vérifiaient que le split **s'engage**, que les cellules ne
permutent pas, qu'un sujet perdu est maintenu — tous justes, tous utiles. Aucun
ne demandait ce que le split **fait une fois engagé**.

**Et le pire n'est pas qu'un test manquait.** En corrigeant le défaut, il a fallu
**réécrire deux des sept tests** : ils supposaient qu'une commande partait à
chaque image en mode split. Ils ne passaient donc pas *malgré* le bug, ils
passaient **parce qu'ils l'encodaient**. Écrits après le code et contre lui, ils
avaient figé le comportement observé au lieu du comportement voulu.

C'est le mode de défaillance que le `CLAUDE.md` du dépôt décrit : *dix tests qui
passent sur du code cassé sont dix tests qui passent ; ce qui compte, c'est
qu'une casse plausible fasse rougir quelque chose.* Ici la casse plausible était
la plus évidente de toutes — « et si ça ne s'arrête jamais ? » — et non seulement
rien ne la couvrait, mais deux tests la certifiaient.

**Conséquence pratique** : un test écrit après coup en regardant tourner le code
mesure ce que le code fait. Pour qu'il mesure ce qu'on veut, il faut l'écrire
depuis l'intention — et sur une machine à états, l'intention la plus importante
est presque toujours *« quand est-ce que ça ne fait rien ? »*.

### Trois fois le même jour, trois mécanismes différents

| Symptôme vu à l'écran | Défaut réel | Ce que les tests voyaient |
|---|---|---|
| « la transition est une coupe » | `SERIAL_FRAME` ne cadence pas | rien : la requête était *acceptée* |
| « on coupe les têtes » | le crâne n'était pas modélisé | rien : le rectangle calculé était juste |
| « je ne vois aucun changement » | l'adaptateur jetait `anchor`/`crown`/`bust` à la conversion | 35 tests verts sur du code mort |

**La forme commune : le test exerce une couche où le défaut n'habite pas.** Le
cœur est pur, donc trivial à tester, donc bien testé. Les **coutures** entre
couches ne le sont pas — et les trois défauts s'y sont logés : entre la requête
et son exécution, entre la cible calculée et le crop appliqué, entre le détecteur
et la politique.

**Et la sortie de ce projet est visuelle.** Les trois ont été trouvés par un
humain qui regardait l'écran, jamais par la suite. Tant que la seule vérification
est « le cœur calcule-t-il le bon rectangle », on livrera des corrections inertes
en toute bonne foi.

D'où la règle de vérification adoptée : **ne rien conclure d'un compte de tests
sur un défaut visuel.** Relire l'état réellement appliqué — `cropTop` depuis un
autre processus pendant que la boucle tourne — et le comparer à ce qu'un humain
verrait. Le harnais déterministe le rend mesurable image par image, donc
comparable d'une version à l'autre plutôt que laissé à l'impression.

Le test qui manquait tient en quatre lignes : deux boîtes immobiles, dix secondes,
**zéro** commande après celle qui engage le split. Il est désormais exigé.

## Ce qui reste à mesurer

### Sur la cible — Windows 11, i9-14900K, RTX 4090

Rien de ce qui touche au **détecteur** n'est acquis là-bas, Vision étant
inexistant sur Windows. Dans l'ordre d'importance :

1. **YOLO11-pose à lot unitaire, en TensorRT**, sur la 4090. C'est le chiffre qui
   décide de la cadence de détection tenable. L'ADR interdit de l'extrapoler des
   145 im/s relevées **par lots** : le direct impose un lot de un, dominé par le
   coût de lancement des noyaux. Les leviers sont déjà classés par rentabilité
   dans le dossier de sources (`imgsz` 960→640, export TRT, modèle plus petit).
2. **`GetSourceScreenshot` sur Windows.** La lecture GPU passe par D3D11 et non
   Metal ; les 5 à 20 ms mesurés ici ne se transposent pas. C'est le chiffre qui
   valide ou invalide le sondage d'images par le websocket sur la cible —
   `scripts/probe.py` le rejoue tel quel.
3. **Le go/no-go entier, rejoué sur la machine de production**, avec Aitum
   Vertical installé : c'est là qu'il comptait, et le canevas vertical n'a jamais
   été testé ici faute d'Aitum.
4. **L'entrée caméra** : `dshow_input` au lieu de `macos-avcapture`, à recâbler
   dans `scripts/setup_scene.py`.

### Indépendamment de la machine

- **Les réglages de la politique** — zone morte, temps de confirmation, durée de
  transition, comportement à la perte. C'est la dette de mesure que l'ADR ouvre,
  et elle reste entière : `--log` produit la trace, le corpus de cas de contrôle
  reste à bâtir sur le modèle de `scripts/framing/cases.ts` d'`avolo-shorts`.
- **Le taux de détection sur de vrais comédiens**, debout et de profil. Le seul
  signal ici — deux corps détectés sur quatre, sur une photo — ne vaut pas une
  mesure.
- **La latence de bout en bout**, du mouvement réel au crop appliqué à l'écran.
  Jamais relevée, chez aucun fournisseur du domaine non plus (voir le dossier de
  sources, § « Ce qu'on n'a pas trouvé »).

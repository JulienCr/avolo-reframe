# ADR-0001 — `avolo-reframe` : le recadrage en direct

- **Statut** : accepté. Go/no-go levé le 12 septembre 2026 — voir la dernière section.
- **Date** : 12 septembre 2026
- **Portée** : un dépôt à venir, distinct d'`avolo-shorts`. Rien dans ce dépôt-ci ne change.

## Le besoin

Diffuser l'émission en direct **simultanément en 16:9 et en 9:16**, vers TikTok
et Instagram, sans seconde régie ni second opérateur. La sortie verticale ne peut
pas être un crop fixe au centre : il faut cadrer sur ce qui se passe à l'image.

C'est un problème **causal**, et c'est ce qui le sépare d'`avolo-shorts`. Tout
`src/core/framing.ts` décide au 90e percentile d'un plan **entier** — une
information qui n'existe pas encore quand le plan est en train d'être joué.

## La décision

**Un nouveau dépôt, `avolo-reframe`, structuré en cœur et adaptateurs dès le
premier commit.** `avolo-shorts` n'est pas touché.

- **Le cœur** : la politique de cadrage, en fonctions pures.
  `(détections + évènements de scène + état) → rectangle de crop`. Pas de
  framework, pas d'async, état explicite passé en paramètre.
- **Les adaptateurs** : le transport. Un par cible, jetable, mince.

**Premier adaptateur : obs-websocket, hors processus.** Pas le plugin natif.

**L'objectif final reste le plugin natif** ; il est décrit plus bas.

### Pourquoi pas le plugin natif en premier

Trois raisons, par ordre de poids.

1. **Le risque du projet est la politique, pas la plomberie.** La politique
   causale — quand recalculer, combien de temps laisser retomber après une
   bascule, quelle hystérésis contre l'oscillation, que faire quand un comédien
   sort du champ — n'est écrite nulle part et n'a jamais été mesurée. Elle ne se
   règle qu'en la regardant tourner sur de vraies émissions, et il en faudra des
   dizaines d'itérations. Une itération coûte quelques secondes hors processus,
   quelques minutes en C++ (compiler, installer, redémarrer OBS, remonter la
   scène). Le facteur décide du nombre d'itérations qu'on fera vraiment.

2. **Le travail jeté est minuscule.** Le cœur et le réglage du modèle sont
   conservés intégralement ; seul l'adaptateur websocket, de l'ordre de deux
   cents lignes, disparaît au port.

3. **Le débit de contrôle requis est faible, et c'est une conséquence de nos
   propres décisions.** Le crop est fixe à l'intérieur d'un plan (spec §10, et la
   caméra qui suit le sujet a été mesurée puis écartée). La sortie de la politique
   est donc **un rectangle par bascule de scène**, pas vingt-cinq par seconde.
   obs-websocket est correctement dimensionné pour ça. Il ne s'écroulerait que
   sur un panoramique continu, que le projet refuse déjà.

Et un avertissement tiré de l'état de l'art (voir
[`docs/recadrage-live-references.md`](../recadrage-live-references.md) §1) : les
quatre outils de cadrage automatique pour OBS qu'on a recensés sont **tous des
plugins natifs**, et tous morts ou moribonds — Intel a archivé le sien le 17 août
2026. Ce qui les tue n'est pas de les écrire, c'est de les maintenir à travers la
matrice C++ x versions d'OBS x pilotes x générations de GPU.

## L'objectif final : le plugin natif

Ce que le plugin doit être quand il existera, et qui décide de ce qu'on écrit
aujourd'hui.

**Ce qu'il fait.** Un filtre OBS qui détecte les **corps** présents sur une
source et pose, sur un canevas vertical, le cadre le plus serré qui les tienne —
recalculé à chaque bascule de scène, fixe entre deux.

**Le créneau, et il est réel.** Rien dans l'écosystème OBS ne cadre **plusieurs
corps** vers un canevas vertical. Les quatre outils recensés suivent des
**visages**, un seul pour trois d'entre eux. Or nos comédiens jouent debout,
face à face, **de profil** — le cas que la spec §2 a mesuré avant de choisir
YOLO, et qui donnait 5 à 30 % d'images sans aucune détection sous MediaPipe. Le
README d'obs-face-tracker documente littéralement notre scène comme son cas
d'échec.

**Ce qui le rend moins fragile que ses prédécesseurs.** Les canevas sont
désormais une **API du cœur d'OBS** (PR #11832 et #11833, fusionnées), et non
plus une invention de plugin tiers. Le plugin viserait donc une API officielle,
et fonctionnerait pour tout OBS 32.1+, sans dépendre d'Aitum.

**Publié en open source**, mais comme conséquence et non comme justification :
ce qui prédit la survie d'un plugin, c'est qu'un mainteneur en ait besoin toutes
les semaines pour son propre usage. AVOLO coche cette case ; Intel ne la cochait
pas.

### Le cadrage sur le locuteur, et le split quand ils se marchent dessus

Capacité visée, pas prévue pour le premier jet. Sur un plan à deux, cadrer en
9:16 sur **celle qui parle** ; quand les deux se chevauchent sur une durée à
définir, basculer en **split**, une personne par cellule.

> **Révisé le 12 septembre 2026 : le `split` n'est pas une capacité visée, c'est
> une condition.** Mesuré sur 8 376 images d'une captation réelle, rejouées
> déterministiquement : **45,1 % des images voient deux corps**, et sur
> **99,6 %** de celles-là le cadre 9:16 se pose **entre** les deux sujets, sur le
> vide qui les sépare.
>
> Et ce n'est pas un défaut réparable par réglage. Un crop 9:16 dans du
> 1920x1080 fait au plus **607,5 px de large, 31,6 % de l'image** ; deux sujets
> distants de 1 470 px donnent une union que `fit_ratio` veut haute de 2 613 px,
> rabattue à 1080. Aucune valeur de `margin`, `min_crop_h` ou `dead_zone` n'y
> change quoi que ce soit.
>
> Pire : comme seules 45 % des images voient les deux, et que les alternances
> durent **4,85 s en moyenne**, le cadre **oscille** sur 1 306 px de large — il
> cadre le vide quand les deux sont vus, bascule vers celui qui reste quand
> l'autre est perdu, revient. Un mouvement ample, lent et sans sens éditorial,
> que la zone morte ne filtre pas puisqu'il s'agit d'un changement d'état qui
> tient cinq secondes.
>
> **Conséquence sur l'arbitrage** : sur un plan à deux comédiens — 43,4 % du
> temps de montage d'après le gisement chiffré ci-dessous — la politique par
> union ne produit pas un cadrage médiocre, elle produit un cadrage **faux**. Le
> `split` conditionne donc l'utilité du 9:16, il ne l'améliore pas. Relevés dans
> [`docs/poc-mac-webcam.md`](../poc-mac-webcam.md), rejouables par
> `scripts/corpus.py`.

Le gisement est déjà chiffré (`docs/locuteur-et-orientation.md`) : les plans à
exactement deux personnes, en 16:9, d'au moins 4 s pèsent **43,4 % du temps de
montage**, et **9,7 % sont des plans où un seul des deux rangs est cadrable** —
donc suivre le locuteur n'exige pas que les deux le soient, il suffit que celle
qui parle le soit. Le `split` existe par ailleurs déjà côté `avolo-shorts`.

**Ce qui rend la capacité tenable en direct alors qu'elle ne l'est pas sur
fichier** : le chemin fichier ne dispose que d'une piste audio **déjà mélangée**,
et la voie la moins chère y est fermée — une statistique de différence d'images
sur la région de bouche donne un pile-ou-face, mesuré sur 17 927 images, le
témoin de bruit de tête battant les trois mesures. OBS, lui, tient les **sources
audio séparées**. Si chaque comédien porte son micro sur une entrée OBS distincte,
« qui parle » est un **niveau audio**, pas un modèle de vision :
`InputVolumeMeters` diffuse le niveau de toutes les entrées actives **toutes les
50 ms**.

C'est le même cadeau structurel que les frontières de plans : ce qui coûte cher
sur fichier est donné en direct.

**Ce qui reste à résoudre, et qui n'est pas le plus dur** : associer un micro à
un corps à l'écran. Le « qui » est connu, il manque le « lequel ». Problème
stable à l'intérieur d'un plan, et bien plus petit qu'une détection
audiovisuelle de locuteur.

**La dépendance qui décide de tout** : des micros séparés par comédien, sur des
entrées OBS distinctes. Sur un micro d'ambiance ou un mixage unique, la voie
s'effondre et on retombe sur la vision — à vérifier sur la configuration son de
l'émission avant d'engager quoi que ce soit.

**Deux choix restent ouverts, volontairement.** Ils se décideront avec la
politique en main, pas aujourd'hui :

- **Le langage du cœur.** Si le cœur doit servir à la fois le plugin C++ et le
  côté Node d'`avolo-shorts`, un cœur **Rust exposé en ABI C** est le seul qui
  tienne les deux (appelable depuis C++, depuis Node par napi-rs, depuis Python
  par pyo3). Tant que la cible est le direct seul, C++ direct suffit.
- **Le détecteur.** YOLO11-pose exporté en TensorRT, ou le **3D Body Pose du
  SDK AR de NVIDIA Maxine** (34 points, multi-personnes, redistribuable Windows,
  Ada supporté). Le second supprime la dépendance à Python mais ne rend pas les
  17 points COCO : `torsoBounds` et la définition `bust` demanderaient un
  remappage **et une re-mesure**, la skill `cadrage` étant formelle sur ce point.

## Ce qui ne change pas

- **`avolo-shorts` reste tel quel.** Aucun code partagé pour l'instant. Si le
  cœur mûrit, le chemin fichier deviendra un adaptateur de plus — plus tard, et
  ce sera une autre décision.
- **Les décisions de cadrage du projet restent en vigueur** : corps et non
  visages, crop fixe dans un plan, ratio choisi par plan. `avolo-reframe` les
  transpose au direct, il ne les rouvre pas.
- **Le double streaming lui-même ne demande aucun développement** : Aitum
  Vertical plus Aitum Multistream le font, gratuitement. Le recadrage automatique
  est l'itération suivante, pas un prérequis. Premier tournage utile possible
  avec un crop réglé à la main — le même découpage que l'itération 0 de la spec.

## Conséquences

- Un dépôt de plus à tenir.
- La politique causale est une dette de mesure ouverte : elle n'a aucun chiffre
  derrière elle aujourd'hui, et il lui faudra son propre corpus de cas de
  contrôle, sur le modèle de `scripts/framing/cases.ts`.
- Le port vers C++ n'est mécanique **que si** la discipline du cœur est tenue
  (fonctions pures, état explicite). Si elle se relâche, le port redevient une
  réécriture et l'arbitrage ci-dessus s'effondre.

## Ce qui peut encore invalider ceci

Un test d'une demi-heure sur la machine de production, à faire avant d'écrire
quoi que ce soit. Le détail des appels est en
[`docs/recadrage-live-references.md`](../recadrage-live-references.md) §3.

1. OBS >= 32.1 et Aitum Vertical en 1.6.x. En dessous, la voie hors processus
   n'existe pas.
2. `GetCanvasList` renvoie-t-il le canevas vertical, et avec quels champs — les
   notes de version disent « **partial** support », et la doc ne détaille pas sa
   réponse.
3. Un `SetSceneItemTransform` portant un crop prend-il effet sur une source du
   canevas vertical **sans avoir ouvert le filtre dans l'interface**. Un défaut
   de cette forme est documenté sur Move Transition.

Si 2 ou 3 échouent, la voie hors processus tombe et l'arbitrage est à refaire —
le plugin natif redeviendrait le premier pas, faute d'alternative.

### Levé le 12 septembre 2026

Les trois points sont vérifiés, par la mesure et non par lecture de doc. Relevés
dans [`docs/poc-mac-webcam.md`](../poc-mac-webcam.md), rejouables par
`uv run python -m scripts.probe`. **La voie hors processus tient**, et l'arbitrage
ci-dessus reste en vigueur.

Un fait que cette section n'avait pas anticipé : **il n'existe aucune requête
`CreateCanvas`**, et l'interface d'OBS 32.2.2 n'en crée pas non plus. L'adaptateur
sait *adresser* un canevas, pas en *créer* un — le canevas vertical reste le
travail d'Aitum Vertical ou d'une action humaine. Sans effet sur la décision, qui
suppose un canevas existant, mais à savoir avant d'automatiser une mise en place.

Deux mesures qui dépassent le go/no-go et qui pèsent sur la suite :

- **`RequestBatch` en `SERIAL_FRAME` ne cadence pas**, contrairement à ce que son
  nom laisse croire. Mesuré : **60 `SetSceneItemTransform` appliqués en 17 ms**,
  là où une cadence à 60 Hz demanderait 1 000 ms. Un lot est donc absorbé dans
  une seule image rendue, et une transition envoyée ainsi se voit comme une
  coupe. **Le lissage doit être cadencé par l'appelant**, à ~60 Hz, sur un fil
  séparé pour ne pas bloquer la détection. Ça reste peu coûteux — le plancher
  protocole est à 0,3 ms — mais ça contredit l'idée qu'une transition ne coûte
  qu'un message, et c'est une contrainte de plus pour le portage.
- **L'ouverture d'une caméra bloque le fil de rendu d'OBS pendant des secondes**,
  et `GetSourceScreenshot` est servi par ce fil. La politique doit survivre à une
  source d'images muette — contrainte réelle, qui s'ajoute à la liste des
  questions causales que cette ADR laisse ouvertes.

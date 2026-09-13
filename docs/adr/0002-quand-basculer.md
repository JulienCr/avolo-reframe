# ADR-0002 — jusqu'où pousser Python, et quand basculer sur le plugin natif

- **Statut** : accepté
- **Date** : 13 septembre 2026
- **Portée** : complète l'ADR-0001, ne la remplace pas. Les mesures citées viennent
  de [`docs/poc-mac-webcam.md`](../poc-mac-webcam.md).

## Ce que l'ADR-0001 laissait ouvert

Elle donne trois raisons de ne pas commencer par le plugin natif, et aucun seuil
pour en sortir. Sur le langage du cœur elle dit : « Ils se décideront avec la
politique en main, pas aujourd'hui. »

Le PoC macOS a maintenant produit ses chiffres. Ils ne donnent pas le seuil qu'on
cherchait. Ils montrent qu'il n'y en a pas.

## Le langage n'est pas ce qui décide

Coût d'une itération de la boucle, caméra active, régime établi, première mesure
écartée :

| Poste | Coût | Qui l'exécute |
|---|---|---|
| Détection Apple Vision | 22 à 27 ms | l'ANE |
| Capture d'une image (`GetSourceScreenshot` 640 px) | 8 à 20 ms | le fil de rendu d'OBS |
| Contrôle (`SetSceneItemTransform`, `GetSceneItemList`) | 0,1 à 0,3 ms | le serveur websocket |

Budget par itération : environ 35 ms, soit un plafond de 28 Hz pour une cadence
visée de 12. La boucle entière coûte 27 % d'un cœur et 957 mW. OBS, lui, ne paie
rien pour être sondé : 35 % d'un cœur seul, 29 à 36 % pendant que la boucle
tourne, donc dans le bruit.

Python ne calcule que la dernière ligne du tableau. Le reste se passe dans l'ANE
et dans OBS. « Quand Python devient trop lent » n'arrivera pas, et la question
telle qu'on se la posait était mal posée.

Ces chiffres viennent d'un MacBook Pro M3, qui n'est pas la cible. Ce qui se
transpose est le rapport entre les postes, jamais les valeurs.

## Le transport a deux moitiés, qui ne basculent pas ensemble

**Le contrôle** encaisse 11 188 requêtes par seconde en rafale et répond en
0,1 ms. On lui demande un rectangle par bascule de plan. Il est surdimensionné
d'un facteur mille pour son usage, et rien ne justifiera jamais de le réécrire.

**L'acquisition** porte deux défauts que le hors-processus ne peut pas corriger :

- **C'est un sondage.** Rien dans obs-websocket ne pousse une image. On demande,
  on attend, on encode en JPEG, on décode. Un plugin natif lit la texture là où
  elle se trouve déjà.
- **Elle est servie par le fil de rendu d'OBS.** Pendant l'ouverture d'une caméra
  Continuity, `GetSourceScreenshot` est passé de 14 ms de médiane à 1 544 ms, avec
  un maximum à 4 256 ms et un tir au-delà du délai de garde de 5 s du client. Vu
  une exécution sur cinq. Ce n'est pas un défaut du client, c'est une propriété
  du transport.

## La décision

**Pousser Python jusqu'à ce que la politique de cadrage soit réglée. Basculer
l'acquisition avant le cœur. Ne jamais basculer le contrôle.**

Le port du cœur est mécanique, et c'est vérifié plutôt que supposé : 562 lignes,
aucun import hors bibliothèque standard, aucune horloge lue à l'intérieur, aucun
dictionnaire ni ensemble, tout en `dataclass(frozen=True)`. Une douzaine de lignes
demandent une décision de représentation : deux lambdas de tri et le choix pour
les `Optional` imbriqués dans des tuples de taille fixe. Le reste se traduit
directement.

Rien ne presse donc de porter le cœur : il ne devient pas plus dur à porter avec
le temps, **tant que la discipline tient**. `tests/test_core_purity.py` la vérifie
depuis le 13 septembre 2026. C'est ce test qui rend cette décision tenable, pas
une intention.

## Les trois déclencheurs de bascule

Aucun n'est la performance.

1. **La latence de bout en bout**, si elle devient un sujet. Voir la section
   suivante.
2. **Un blocage du fil de rendu pendant une émission.** Quatre secondes de gel
   devant un public coûtent plus cher qu'un port.
3. **Le déploiement.** Installer Python, `uv` et un environnement virtuel sur une
   machine de régie, contre déposer une `.dll` dans `obs-plugins/64bit`. La
   question n'est pas technique, et c'est probablement elle qui tranchera.

## Ce qu'on décide de ne pas mesurer

La latence de bout en bout, du mouvement réel au crop affiché, n'a jamais été
relevée ici et ne l'est nulle part chez les fournisseurs du domaine.

**On ne la mesure pas.** La latence perçue est jugée acceptable à l'œil, et le
protocole qui la donnerait (filmer un chronomètre, enregistrer la sortie 9:16,
lire l'écart sur une même image) coûte une séance pour un chiffre dont aucune
décision ne dépend aujourd'hui.

Cette décision est datée pour qu'on ne la rouvre pas en croyant combler un oubli.
Elle se rouvre le jour où quelqu'un se plaint du retard, pas avant.

## Le langage du cœur

Le critère de l'ADR-0001 reste valable, et il reste fonctionnel plutôt que
chiffré. Si le cœur doit servir à la fois le plugin C++ et le côté Node
d'`avolo-shorts`, un cœur Rust exposé en ABI C est le seul qui tienne les deux.
Tant que la cible est le direct seul, C++ direct suffit.

Aucune mesure de ce PoC ne déplace ce curseur. Il ne se déplacera que le jour où
quelqu'un décidera de mutualiser le cœur entre les deux projets.

## Conséquences

- L'adaptateur d'acquisition s'écrit en sachant qu'il mourra. L'adaptateur de
  contrôle, non : il reste.
- Le garde-fou de `core/` passe du confort à la condition. S'il saute, cette
  décision ne tient plus et le port redevient une réécriture.
- Ce qui reste à mesurer sur Windows ne change pas. La liste est dans
  [`TODO.md`](../../TODO.md), et la latence de bout en bout en sort.

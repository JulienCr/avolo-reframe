# TODO

Ce qui reste, par ordre de ce qui bloque le plus. Établi le 13 septembre 2026,
au terme du PoC macOS.

Ce qui est **déjà tranché** est en bas, pour que personne ne le rouvre.

---

## Bloquant pour la cible de production

La cible est **Windows 11, i9-14900K, RTX 4090**. Tout ce qui touche au détecteur
est à refaire là-bas : **Apple Vision n'existe pas sur Windows**, donc aucune
latence de détection mesurée ici n'a de valeur pour la production.

- [ ] **YOLO11-pose à lot unitaire, en TensorRT, sur la 4090.** C'est le chiffre
      qui décide de la cadence de détection tenable. Interdiction de l'extrapoler
      des 145 im/s relevées **par lots** : le direct impose un lot de un, dominé
      par le coût de lancement des noyaux. Leviers déjà classés par rentabilité
      dans le dossier de sources (`imgsz` 960→640, export TRT, modèle plus petit).
- [ ] **`GetSourceScreenshot` sur Windows.** La lecture GPU passe par D3D11 et non
      Metal ; les 5 à 20 ms mesurés ici ne se transposent pas. C'est ce chiffre
      qui valide ou invalide le sondage d'images par le websocket sur la cible.
      `scripts/probe.py` le rejoue tel quel.
- [ ] **Rejouer tout le go/no-go sur la machine de production**, Aitum Vertical
      installé. `uv run python -m scripts.probe`, sort 0 si tout passe.
- [ ] **`dshow_input` au lieu de `macos-avcapture`** dans `scripts/setup_scene.py`.
- [ ] **Vérifier qu'Aitum Vertical publie bien un installateur Windows.** La 1.6.4
      ne semble exposer que macOS, Linux et les sources ; les versions antérieures
      avaient du Windows. À confirmer **avant** de bâtir la chaîne dessus.

## Dette de mesure

La politique causale est le risque du projet, et elle n'a presque aucun chiffre
derrière elle. L'instrument existe désormais ; les mesures, non.

- [ ] **Un second extrait avec des bascules de plan annotées.** L'extrait actuel
      en contient, mais non identifiées — on ne sait donc pas séparer ce que la
      politique fait des **coupes** de ce qu'elle fait du **bruit de détection**.
      Or les bascules sont sa raison d'être. Quelques dizaines de secondes
      annotées vaudraient plus que ces onze minutes.
- [ ] **Balayer les paramètres de la politique sur le corpus.** Un rejeu complet
      coûte 75 s pour 698 s de vidéo, donc un balayage est praticable.
- [ ] **Calibrer `_CROWN_FACTOR`** (1,0 aujourd'hui). C'est une règle de pouce :
      le sommet du crâne est estimé à une distance yeux-nuque au-dessus des yeux.
      Ça marche, ça n'a jamais été mesuré contre la vérité terrain.
- [ ] **Taux de détection sur de vrais comédiens debout et de profil.** Le seul
      signal dont on dispose — deux corps détectés sur quatre, sur une photo — ne
      vaut pas une mesure.
- [ ] **Latence de bout en bout**, du mouvement réel au crop affiché. Jamais
      relevée, chez aucun fournisseur du domaine non plus.

## Défauts connus

- [ ] **`scripts/corpus.py` n'expose pas les paramètres de split** —
      `track_hold_ms`, `split_enter_ms`, `split_exit_ms`, ni `eye_line`,
      `zoom_dead_zone`, `max_zoom`. **L'instrument de réglage ne peut pas
      atteindre les boutons qu'on règle**, ce qui a déjà obligé à mesurer à la
      main. À corriger avant tout balayage sérieux.
- [ ] **Distinguer « le sujet est sorti du cadre » de « la détection a
      décroché ».** Aujourd'hui les deux passent par `track_hold_ms`, calé sur
      les 4 850 ms de décrochage moyen — donc un sujet qui sort vraiment fait
      attendre six secondes pour rien. L'information existe : une boîte qui
      dérive vers un bord avant de disparaître n'est pas une boîte qui s'éteint
      au milieu du cadre.
- [ ] **Une reconstruction de scène qui change la *résolution* de la source**
      laisse `PolicyParams` et `PolicyState` sur l'ancienne. Le cas des
      identifiants est traité (revérification toutes les 2 s) ; celui de la
      résolution demanderait de réinitialiser tout l'état de la politique.
- [ ] **`adapters/detect_yolo.py` n'existe pas.** `--detector yolo` dégrade
      proprement avec un message, mais le module est à écrire. La comparaison
      Vision/YOLO n'a de sens que contre le corpus, pas contre une session libre.

## Fonctionnalités visées

- [ ] **Écrire la sortie 9:16 sur le vrai canevas vertical** plutôt que dans
      l'encart du canevas principal. Vérifié possible : `CreateSceneItem` et
      `SetSceneItemTransform` acceptent le `sceneUuid` seul, et le crop s'y relit
      à l'identique. Attention, `GetSourceScreenshot` ne trouve une scène d'un
      canevas non principal que par `sourceUuid`, jamais par `sourceName`.
- [ ] **L'air devant le regard.** Le yaw de la tête est mesuré (−47° sur un sujet
      de profil) et **la politique ne s'en sert pas**. Décaler le cadre du côté où
      le sujet regarde est une règle classique de cadrage, et la donnée est déjà là.
- [ ] **Cadrer sur le locuteur** via `InputVolumeMeters` — 20 relevés par seconde,
      sans GPU ni modèle. **Dépendance qui décide de tout : un micro par comédien
      sur une entrée OBS distincte.** À vérifier sur la configuration son de
      l'émission avant d'engager quoi que ce soit ; sur un micro d'ambiance ou un
      mixage unique, la voie s'effondre.
- [ ] **Retarder la sortie verticale pour s'offrir une fenêtre de décision.**
      Hors PoC. Le filtre `gpu_delay` est pilotable par websocket (vérifié) ;
      `D` ms de délai donnent `D` ms de regard vers l'avant. Couvre le temps de
      confirmation, pas le maintien de piste. **Piège : il retarde la vidéo seule**,
      donc désynchronisation labiale si la sortie porte du son.

---

## Tranché — ne pas rouvrir

- **Les trois go/no-go de l'ADR sont levés** sur macOS, canevas vertical compris.
  La voie hors processus tient de bout en bout.
- **`SERIAL_FRAME` ne cadence pas** malgré son nom : 60 transformations en 17 ms.
  Le lissage est cadencé par l'appelant, à 60 Hz, sur un fil séparé.
- **Il n'existe aucune requête `CreateCanvas`.** L'adaptateur sait adresser un
  canevas, pas en créer un.
- **Le `split` n'est pas une capacité visée, c'est une condition** : 45 % des
  images voient deux corps, et 99,6 % de celles-là cadrent le vide entre eux.
  L'ADR est révisé en conséquence.
- **Le crop d'item, pas le filtre `Crop/Pad`** : un filtre vit sur la source et
  croperait aussi la sortie 16:9. Une source, deux cadrages.
- **La source par défaut est la vidéo de test, pas la caméra.** Une caméra rend
  chaque exécution différente, donc deux mesures ne sont plus comparables.
- **Les quatre détecteurs se tiennent entre 6 et 9 ms** : ce n'est pas là que se
  joue la cadence.

## Règles de vérification apprises ici

Elles ont chacune coûté une conclusion fausse. Détail dans
[`docs/poc-mac-webcam.md`](docs/poc-mac-webcam.md).

- **Écarter systématiquement la première mesure** — démarrage à froid.
- **Ne comparer que des mesures prises sur les mêmes images, dans le même
  processus, à la suite.**
- **Un compte de tests ne dit rien d'un défaut visuel.** Relire l'état
  **réellement appliqué** depuis un autre processus, jamais le rectangle calculé.
- **Vérifier qu'une requête est acceptée ne dit rien de quand elle s'exécute.**
- **Avant de lancer quoi que ce soit pendant qu'un agent édite** :
  `uvx ruff check --select F821 . && uv run pytest -q && …`. Les tests seuls ne
  couvrent pas les scripts, et un `NameError` dans un corps de fonction échappe
  aussi bien à `import` qu'à `py_compile`.

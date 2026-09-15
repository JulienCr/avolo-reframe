# TODO

Ce qui reste, par ordre de ce qui bloque le plus. Établi le 13 septembre 2026,
au terme du PoC macOS ; mis à jour le 15 septembre 2026, au terme du portage
Windows et du correctif de politique de split (issue #2).

Ce qui est **déjà tranché** est en bas, pour que personne ne le rouvre.

---

## Bloquant pour la cible de production

La cible est **Windows 11, i9-14900K, RTX 4090**. Elle a maintenant ses propres
mesures — détail dans [`docs/poc-windows.md`](docs/poc-windows.md).

- [x] **YOLO11-pose à lot unitaire, en TensorRT, sur la 4090.** Mesuré le
      15 septembre 2026 : `.pt` fp16 médiane 13,88 ms, `.engine` TensorRT fp16
      médiane 7,27 ms, sur 39 images du corpus, lot unitaire, même processus.
      La boucle en direct tient 30 im/s dans les deux cas.
- [x] **`GetSourceScreenshot` sur Windows.** Mesuré : médiane 4,2 ms en 640 px,
      21,4 ms en 1920 px — le websocket reste praticable, la lecture D3D11 est
      simplement un peu plus lente que le Metal du Mac sur le format large.
- [x] **Rejoué tout le go/no-go sur la machine de production**, Aitum Vertical
      installé (`make probe`, sort 0). `GetCanvasList` montre bien un second
      canevas « Aitum Vertical » 1080x1920 @ 60 : l'installateur Windows existe.
- [x] **`adapters/detect_yolo.py` écrit**, avec `pose_geometry.py` pour la
      logique crâne/buste/ancre partagée et le remap COCO-17 → Vision.
- [ ] **`dshow_input` au lieu de `macos-avcapture`** dans `scripts/setup_scene.py`.
      Le code est écrit et bascule sur `--camera`, mais **jamais testé en direct
      avec une caméra branchée** — seul le rejeu sur la vidéo de test l'a exercé.

## Dette de mesure

La politique causale est le risque du projet, et elle n'a presque aucun chiffre
derrière elle. L'instrument existe désormais ; les mesures, non.

- [ ] **Un second extrait avec des bascules de plan annotées.** L'extrait actuel
      en contient, mais non identifiées — on ne sait donc pas séparer ce que la
      politique fait des **coupes** de ce qu'elle fait du **bruit de détection**.
      Or les bascules sont sa raison d'être. Quelques dizaines de secondes
      annotées vaudraient plus que ces onze minutes.
- [ ] **Balayer les paramètres de la politique sur le corpus.** Fait pour les
      minuteurs de split (192 configurations, issue #2) ; le reste des
      paramètres (`margin`, `dead_zone`, `eye_line`, `max_zoom`…) reste à
      balayer.
- [ ] **Calibrer `_CROWN_FACTOR`** (1,0 aujourd'hui). C'est une règle de pouce :
      le sommet du crâne est estimé à une distance yeux-nuque au-dessus des yeux.
      Ça marche, ça n'a jamais été mesuré contre la vérité terrain.
- [ ] **Taux de détection sur de vrais comédiens debout et de profil.** Le seul
      signal dont on dispose — deux corps détectés sur quatre, sur une photo — ne
      vaut pas une mesure.
- [ ] **Latence de bout en bout**, du mouvement réel au crop affiché. Jamais
      relevée, chez aucun fournisseur du domaine non plus.

## Défauts connus

- [x] **`scripts/corpus.py` n'expose pas les paramètres de split** — corrigé le
      15 septembre 2026 : tous les réglages de la politique sont exposés en
      argument, et `--from-trace` rejoue une trace en quelques secondes au lieu
      de 75 s, ce qui a permis le balayage de l'issue #2.
- [ ] **Distinguer « le sujet est sorti du cadre » de « la détection a
      décroché ».** Mesuré le 15 septembre 2026 (`tests/corpus/tools/edge_exits.py`) :
      une dernière boîte en bord de cadre ne prédit pas mieux un « jamais
      revenu » qu'une boîte à mi-cadre (6,6 % contre 9,0 % sous 10 s, pertes
      censurées par la fin de trace exclues), et la
      vitesse vers le bord ne sépare pas non plus les deux catégories — **aucun
      maintien plus court n'est justifié en bord de cadre**, résultat négatif.
      Toujours ouvert : sans extrait annoté, on ne sait pas si les pertes
      « jamais revenues » sont de vraies sorties ou de longs décrochages.
- [ ] **Le message d'erreur sur source 0x0 envoie au mauvais endroit.** Quand la
      source média est à l'arrêt (`OBS_MEDIA_STATE_STOPPED`), elle renvoie 0x0 et
      `resolve_scene` conclut « résolution jamais renégociée après reconstruction,
      relancez `scripts.run` ». Relancer la boucle n'y change rien : c'est la
      source qu'il faut redémarrer
      (`TriggerMediaInputAction` / `OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART`).
      Interroger `GetMediaInputStatus` avant de conclure, et dire le vrai remède —
      ou relancer la source soi-même.
- [ ] **Une reconstruction de scène qui change la *résolution* de la source**
      laisse `PolicyParams` et `PolicyState` sur l'ancienne. Le cas des
      identifiants est traité (revérification toutes les 2 s) ; celui de la
      résolution demanderait de réinitialiser tout l'état de la politique.
- [x] **Le split décide sur des pistes mémorisées, pas sur des détections
      fraîches** (issue GitHub #2) — corrigé le 15 septembre 2026 : l'entrée
      n'est plus décidée que sur des détections fraîches, la sortie coupe sur
      l'image même où l'état bascule. Sortie 500/583 ms (médiane/p90) contre
      4750/6417 ms avant, 0 entrée sur piste périmée contre 8 sur 20 ; détail
      dans `tests/corpus/README.md`.
- [ ] **Le go/no-go 3 (crop sans ouvrir le filtre) ne prouve rien par hachage
      sur une source en mouvement.** Sur la scène de mesure Windows, la vidéo
      de test tourne en boucle : les empreintes diffèrent avant/après de toute
      façon. Seule la relecture (`cropRight` envoyé et relu à la même valeur)
      prouve le crop — à garder en tête avant de lire ce go/no-go comme une
      preuve par hachage.
- [ ] **Le split reste tenu sur un partenaire faible environ un tiers du temps
      passé en split** : une des deux pistes n'a pas de crâne estimé, ou fait
      moins de 25 % de la hauteur source. Vu dans les deux politiques (avant et
      après l'issue #2), jamais creusé.

## Fonctionnalités visées

- [ ] **Écrire la sortie 9:16 sur le vrai canevas vertical** plutôt que dans
      l'encart du canevas principal. Vérifié possible : `CreateSceneItem` et
      `SetSceneItemTransform` acceptent le `sceneUuid` seul, et le crop s'y relit
      à l'identique. Attention, `GetSourceScreenshot` ne trouve une scène d'un
      canevas non principal que par `sourceUuid`, jamais par `sourceName`.
- [ ] **L'air devant le regard.** Le yaw de la tête est mesuré côté Vision
      (−47° sur un sujet de profil) et **la politique ne s'en sert pas**.
      Décaler le cadre du côté où le sujet regarde est une règle classique de
      cadrage, et la donnée est déjà là. Côté YOLO, le yaw resterait à estimer
      depuis les 5 points de visage COCO — piste posée, jamais mesurée, suivie
      par l'issue GitHub #1.
- [ ] **Cadrer sur le locuteur** via `InputVolumeMeters` — 20 relevés par seconde,
      sans GPU ni modèle. **Dépendance qui décide de tout : un micro par comédien
      sur une entrée OBS distincte.** À vérifier sur la configuration son de
      l'émission avant d'engager quoi que ce soit ; sur un micro d'ambiance ou un
      mixage unique, la voie s'effondre.
- [ ] **Retarder la sortie verticale pour s'offrir une fenêtre de décision**
      (issue GitHub #7). Étudié le 15 septembre 2026 : le filtre `gpu_delay` est
      pilotable par websocket (vérifié), mais la politique ne date pas ses
      commandes de l'instant source du changement et rien ne repère l'instant
      de l'image analysée. Avis de Julien : probablement pas engagé, beaucoup
      de travail pour un gain non bloquant.

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
- **Une bascule de mode traverse toujours une transition en cours.** Mesuré sur les
  48 configurations du balayage qui l'appliquent : la longueur de la transition
  (`ease_ms`) ne change alors aucune métrique de bascule, c'est un choix purement visuel.
- **Aucun maintien plus court n'est justifié pour une piste perdue en bord de
  cadre** (`tests/corpus/tools/edge_exits.py`) : la vitesse vers le bord et la
  position ne prédisent pas mieux un retour qu'à mi-cadre.
- **Les défauts de split sont calés sur YOLO, pas sur Vision** (`track_hold_ms`,
  `split_exit_ms` 500 ms) : les utilisateurs Vision doivent les surcharger dans
  `reframe.toml`.

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

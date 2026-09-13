# Corpus de cas de contrôle

Ce que le PoC a mesuré, conservé pour pouvoir comparer après un changement.
L'ADR réclame ce corpus : la politique causale n'a presque aucun chiffre derrière
elle, et c'est ici qu'ils s'accumulent.

Récupéré du scratchpad de session le 12 septembre 2026 — il aurait été effacé.

## Contenu

| Dossier | Quoi | À versionner |
|---|---|---|
| `reference-summary.json` | l'agrégat d'un rejeu complet, ~900 octets | **oui** |
| `traces/` | les traces JSONL brutes, ~5,7 Mo pièce | non |
| `frames/` | 39 images échantillonnées de l'extrait, 1 Mo | oui |
| `tools/` | les scripts d'analyse et de vérification | oui |
| `cuts/` | repérage des bascules de plan (candidats, seuils, planches contact) | oui |

## `reference-summary.json`

L'état de référence, à comparer après toute modification de la politique.
Il porte l'empreinte de l'extrait, tous les paramètres et le détecteur utilisés —
un résumé qui ne dit pas ce qui l'a produit ne vaut rien trois semaines plus tard.

Chiffres au 13 septembre 2026, **détecteur `pose`** (le précédent, `vision-upper`,
ne fournit aucune estimation de crâne) : 8 376 images, détection 99,4 %,
355 commandes, **45,6 % d'images à deux corps ou plus**.

**Corrigé le 13 septembre 2026 : `classement_du_centre` jugeait le mauvais
rectangle.** Il classait le centre de la *cible brute calculée en mode simple*
contre les sujets, y compris sur les 4 537 images où la politique était
réellement en **mode split** (54 % des images) — donc pas ce qui s'affichait.
Comme le split se déclenche justement quand cette cible brute est dégénérée,
la quasi-totalité de ces images comptait comme « cadrée entre les sujets »,
alors que le split les avait résolues. `classement_du_centre` ne porte
maintenant que sur les images en **mode simple** (472, dont 253 entre les
sujets) ; un nouveau `classement_split_cellules` juge chaque **cellule
appliquée** contre le sujet le plus proche de son centre (9 074 cellules sur
4 537 images, 83,2 % cadrent leur sujet). `degenerate_harmful` tombe en
conséquence de 41,0 % à 16,5 % des images détectées : ce n'est pas la
politique qui a changé, c'est la mesure qui portait sur le mauvais rectangle.

Nouveau : **`crane_coupe`**, la mesure qui aurait attrapé le défaut de coupe de
tête du 12-13 septembre — sur le cadre **appliqué**, pas la cible calculée, qui
peut en diverger pendant des dizaines d'images tant que la zone morte ne
recommet pas. 7 600 cellules vérifiées, **0 % coupées**, marge médiane +79px,
p10 +29px ; 978 cas exclus où le crâne estimé tombe lui-même hors de la source
(seule exception acceptée, comptée à part, jamais dans le taux de coupe).

La référence commitée annonçait 7 598. Ce n'est pas une dérive d'environnement :
ce chiffre vient de la trace du 13 septembre à 00:47, qui porte `state.mode` nul
sur ses 8 376 images, donc produite par un cœur où le champ n'existait pas encore.
Elle est antérieure au commit `0b68bec`, qui l'introduit. **La référence était
périmée en arrivant dans le dépôt, et rien ne la comparait à un rejeu.** Vérifié
le 13 septembre 2026 : deux rejeux consécutifs restent identiques au bit près,
agrégat et trace, donc le déterminisme promis plus haut tient.

## `traces/`

- `full-reference.jsonl` — rejeu complet de l'extrait avec `vision-upper`
  (sans crâne), sha256 `a6db1d5b3a4b1e08` ; gardé pour comparer le détecteur.
- `full-reference-pose.jsonl` — même extrait, **détecteur `pose`**, sha256
  `2b0520667503dcab` : c'est celui qui a produit `reference-summary.json`.
- `zoom-before.jsonl` / `zoom-after.jsonl` — avant et après l'introduction de
  `zoom_dead_zone` : 153 commandes contre 149, soit **−2,6 % seulement**. Résultat
  négatif conservé exprès : il dit que le pumping mesuré sur cet extrait vient de
  la dérive horizontale, pas du bruit de hauteur.

**Régénérables**, à condition d'avoir l'extrait :

```bash
uv run python -m scripts.corpus tests/fixtures/lab-avolo-58m22-70m00.mp4 \
    --detector pose --fps 12 --out trace.jsonl --summary-json summary.json
```

75 s pour 698 s de vidéo, et **deux rejeux donnent une sortie identique au bit
près**. C'est ce déterminisme qui rend le réglage mesurable : on change un seul
paramètre et on attribue la différence à ce paramètre.

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
`verify_resilience.py`, `cpu2.py`, `split_rate.py`.

## Ce qui manque encore

Un extrait avec des **bascules de plan annotées**. Celui-ci n'en a pas, donc on
ne sait pas séparer ce que la politique fait des coupes de ce qu'elle fait du
bruit de détection — et les bascules sont précisément ce que la politique causale
existe pour gérer.

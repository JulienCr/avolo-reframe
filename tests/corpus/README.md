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

## `reference-summary.json`

L'état de référence, à comparer après toute modification de la politique.
Il porte l'empreinte de l'extrait, tous les paramètres et le détecteur utilisés —
un résumé qui ne dit pas ce qui l'a produit ne vaut rien trois semaines plus tard.

Chiffres au 12 septembre 2026 : 8 376 images, détection 99,5 %, 114 commandes,
**45,1 % d'images à deux corps ou plus**, dont **3 761 cadrées entre les sujets**
et 4 566 sur un sujet.

## `traces/`

- `full-reference.jsonl` — rejeu complet de l'extrait, sha256 `a6db1d5b3a4b1e08`.
- `zoom-before.jsonl` / `zoom-after.jsonl` — avant et après l'introduction de
  `zoom_dead_zone` : 153 commandes contre 149, soit **−2,6 % seulement**. Résultat
  négatif conservé exprès : il dit que le pumping mesuré sur cet extrait vient de
  la dérive horizontale, pas du bruit de hauteur.

**Régénérables**, à condition d'avoir l'extrait :

```bash
uv run python -m scripts.corpus tests/fixtures/lab-avolo-58m22-70m00.mp4 \
    --upper-body --fps 12 --out trace.jsonl
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

- En réalité, pendant le live, on preframe l'ensemble des caméras (3 à 4) en parallèle de sorte à etre tout de suite correctement calé lors d'un changement de plan.
==> Prévoir ici la gestion de sources multiples / threads multiples?
- Il faut bien travailler le suivi des visages / bustes / corps au sein d'un meme plan pour garantir la fluidité des mouvements de reframing. Peut etre utile d'explorer des algorithmes de suivi multi-objets robustes, systeme de keyframing, loop de feedback pour ajuster les positions en temps réel ?
- On peut aussi réflechir à quelle valeur de plan est la plus intéressante en fonction du contexte et des mouvements des sujets (gros plan, plan moyen, plan large)
- On est parti sur un POC mac, il faut avant tout de chose s'assurer de son fonctionnement sur le PC windows cible.
- Ensuite, voir si la stack de POC est alignée avec nos objectifs globaux définis dans l'ADR. On a également en tete de terminer par un plugin C++ au sein d'obs directement. Il faudra décider du meilleur moment de la bascule

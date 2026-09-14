# Plein Sud — le modèle en détail

Ce document décrit ce que calcule le simulateur, poste par poste, et les choix
de modélisation derrière chaque chiffre. Pour l'installation et la prise en
main, voir le [README](../README.md).

---

## Démarrage

```bash
python main.py                     # interface graphique, config par défaut
python main.py ma_config.json      # interface graphique, config existante
python main.py --cli               # rapport texte complet dans le terminal
python main.py --sweep inclinaison 20,30,40,50,60,70,80
python main.py --grille 5:40@5 0:128@16 10          # optimum PV x batterie
python main.py --download 45.76 4.83 2018 2023      # nouvelle météo PVGIS
```

Le fichier `config_defaut.json` reprend le domaine de Langoiran : 30 kWc en
deux groupes, 2 onduleurs Deye 12 kW triphasés, 64 kWh de LFP, et les postes de
consommation déduits des factures EDF.

---

## Ce que fait le modèle

### Météo et gisement solaire

Source : **PVGIS** (JRC, Commission européenne), base satellite SARAH3, pas
horaire. Le fichier `pv_sizer/data/meteo_langoiran_2018_2023.csv.gz` contient
6 années réelles pour Langoiran (44,71 N / -0,39 E) : rayonnement direct et
diffus horizontaux, température, vent.

Point important : on télécharge les composantes **sur plan horizontal**, une
seule fois par site. L'inclinaison est ensuite appliquée localement par
transposition. Changer un champ de 30 à 60 degrés est instantané et ne
nécessite aucun accès réseau.

Chaîne de calcul :

1. **Position du soleil** — algorithme NOAA, validé à 0,12 degré près contre
   la valeur `H_sun` fournie par PVGIS.
2. **Transposition Hay-Davies** — modèle anisotrope séparant direct, diffus
   circumsolaire, diffus isotrope et réflexion du sol (albédo réglable).
3. **Modificateur d'angle d'incidence** (ASHRAE, b0 réglable) — non
   négligeable aux fortes inclinaisons et aux incidences rasantes d'été.
4. **Température de cellule** — modèle de Faiman, avec le vent horaire réel.
5. **Puissance DC** — coefficient de température, pertes DC forfaitaires,
   ombrage par groupe.

### Consommation

Sept types de postes, cumulables, chacun générant son propre profil horaire :

| Type | Pilotage |
|---|---|
| Talon permanent | puissance constante + majoration hivernale |
| Générique | kWh/an + profil journalier + 12 poids mensuels |
| Chauffage PAC | degrés-heures **réels**, COP fonction de la température extérieure, puissance PAC plafonnée et déclassée par le froid, appoint électrique automatique, part reprise par un insert bois sous un seuil |
| ECS | besoin thermique, COP (résistance ou thermodynamique), majoration hivernale liée à la température d'eau froide |
| Piscine | puissance de pompe, heures/jour mois par mois, PAC de chauffage optionnelle |
| Spa / jacuzzi | modèle physique : pertes = UA x (consigne - température extérieure horaire) |
| Véhicule électrique | km/an, kWh/100 km, jours de recharge, profil |

Les postes thermiques réagissent à la météo de chaque année : un hiver doux et
un hiver froid ne donnent pas le même résultat, et le bilan affiché est la
moyenne sur les 6 années.

### Système

Dispatch heure par heure, dans cet ordre : PV vers les charges, surplus vers la
batterie par la voie DC (meilleur rendement), reste vers le réseau ou écrêté,
puis batterie vers les charges, puis soutirage.

Sont modélisés :

- rendements distincts PV→AC, PV→batterie, batterie→AC ;
- **consommation à vide des onduleurs**, avec une valeur jour et une valeur
  nuit, comptée dans le besoin ;
- écrêtage par la puissance AC nominale des onduleurs ;
- rendements de charge et de décharge, profondeur de décharge, régimes C max ;
- plafond de puissance souscrite (l'énergie non fournie est comptabilisée) ;
- mode injection nulle ou revente ;
- recharge réseau optionnelle en heures creuses avec un état de charge cible.

### Contrôles automatiques

Le simulateur signale : dépassement de la puissance PV admissible par onduleur,
franchissement du seuil réglementaire des 36 kVA de production, écrêtage
excessif, puissance souscrite insuffisante, saturation de la PAC avec bascule
sur l'appoint électrique, nombre de cycles batterie et durée de vie estimée.

---

## Les neuf onglets

1. **Site et météo** — coordonnées, années, base PVGIS, téléchargement,
   paramètres modules. Graphique du rayonnement et de la température mensuels.
2. **Champs PV** — un groupe par ligne : nombre de panneaux, Wc unitaire,
   inclinaison, azimut, ombrage. Le productible de chaque groupe est recalculé
   à chaque simulation. Le compteur kWc/onduleur passe au rouge en cas de
   dépassement.
3. **Consommation** — liste des postes à gauche, formulaire adapté au type à
   droite, totaux simulés en bas.
4. **Onduleurs et batterie** — tous les rendements et la courbe d'état de
   charge sur toute la série horaire.
5. **Coûts** — nomenclature éditable, **rangée en deux périmètres**. Chaque
   ligne porte une catégorie : *modules et structure*, *onduleurs*, *batterie
   et BMS*, *câblage et protections*, *génie civil*, *administratif*,
   *outillage* forment le **périmètre de l'installation solaire** ; *thermique*
   (chauffe-eau, insert), *bâtiment* (isolation) et *autre* en sont exclus.
   Seul le sous-total solaire alimente les €/Wc, le €/kWh de batterie, le coût
   du kWh autoproduit et les coûts affichés dans les onglets Optimisation,
   Orientations et Leviers — le prix d'une isolation de combles n'a rien à
   faire dans l'arbitrage d'un pack de batterie. La colonne *quantité auto*
   relie chaque ligne à la configuration : panneaux, onduleurs, cellules,
   packs, grappes, kWc, surface. Un bouton *Compléter la nomenclature* ajoute
   les postes qu'une auto-installation oublie systématiquement : câble solaire
   au mètre, MC4, coffret DC, fusibles de grappe, parafoudres, mise à la
   terre, puis l'outillage — sertisseuse, pince MC4, pince ampèremétrique
   continue, EPI, chargeur d'équilibrage.
6. **Résultats** — bilan mensuel, détail jour par jour pour un mois et une
   année au choix, et la journée moyenne de chaque mois en profil horaire.
7. **Optimisation** — balayage d'un paramètre (inclinaison, puissance crête,
   batterie, onduleurs) et calcul du **budget de consommation** : pour chaque
   mois, la consommation journalière maximale compatible avec l'objectif
   d'autonomie. Quatre vues : les profils mois par mois de chaque option, des
   histogrammes qui comparent les options entre elles (économie et temps de
   retour compris), l'**amortissement tranche par tranche**, et la recherche
   d'**optimum puissance PV × batterie**.
8. **Orientations** — recherche de l'inclinaison et de l'azimut de chaque
   groupe pour un critère au choix, avec la carte du critère par groupe.
9. **Leviers** — par quoi commencer pour payer moins de réseau : l'énergie
   achetée est imputée à chaque poste de consommation (avec la part consommée
   la nuit, celle qui coûte le plus cher), puis chaque action envisageable
   — panneaux, batterie, onduleur, poste allégé de 10 %, usage décalé au
   soleil, appareil de fond remplacé — est réellement simulée et classée par
   gain annuel, coût et temps de retour.

Tous les graphiques réagissent au survol (réticule et valeurs de toutes les
courbes) et s'ouvrent en plein écran d'un clic, avec la légende complète, les
valeurs sous le curseur et le minimum, la moyenne, le maximum et le total de
chaque courbe.

`F5` relance la simulation. Le bouton *Exporter CSV* sort le bilan mensuel,
tous les indicateurs et l'intégralité du détail journalier.

---

## Deux périmètres de coût, et pourquoi ça change les conclusions

La nomenclature mélange deux choses de nature différente : ce qui produit et
stocke l'électricité, et ce qui fait baisser le besoin (chauffe-eau
thermodynamique, insert, isolation). Les confondre dans un total unique fausse
tout arbitrage de dimensionnement.

Le simulateur les sépare et affiche les deux :

| | Installation solaire | Projet complet |
|---|---|---|
| Investissement | catégories 1 à 8 de la nomenclature | tout le devis |
| Comparé à | la **même maison** sans panneaux ni batterie | la **facture déclarée** avant travaux |
| Économie annuelle | l'énergie que le PV et la batterie évitent d'acheter | la baisse totale de la facture |
| Sert à | **dimensionner** : un panneau, un pack, un onduleur de plus | **budgéter** le chantier |

C'est le couple de gauche qui pilote tout le reste. Sur la configuration par
défaut, cela fait 25 288 € de solaire face à 34 188 € de projet : le ratio
passe de 1,14 à **0,84 €/Wc**, la valeur réellement comparable aux 1,80 à
2,50 €/Wc d'une pose par un installateur.

Le balayage de batterie devient lisible directement :

```
    valeur  autonomie  cout sol.  EUR/Wc   retour  ROI marg.
        16     66.44%      20632    0.69      7.2      -
        32     76.68%      22184    0.74      6.7    0.268
        64     86.33%      25288    0.84      6.7    0.126
        96     89.40%      28392    0.95      7.2    0.040
       128     90.72%      31496    1.05      7.8    0.017
```

La colonne *ROI marginal* est le critère d'arrêt : 0,268 en passant de 16 à
32 kWh (l'incrément se rembourse en moins de 4 ans), 0,040 en passant de 64 à
96 kWh (25 ans), 0,017 au-delà. Le pack suivant n'en vaut plus la peine bien
avant que la courbe d'autonomie ne s'aplatisse.

Dans l'onglet **Leviers**, une colonne *Périmètre* dit pour chaque action si
son surcoût tombe dans l'installation solaire, hors périmètre (un appareil, des
travaux) ou s'il est nul (un simple réglage).

---

## Jusqu'où agrandir ? L'amortissement tranche par tranche

Le temps de retour habituel porte sur l'installation entière. C'est une
moyenne, et elle ment par omission : une première batterie amortie en 2 ans
suivie d'une quatrième qui ne s'amortira jamais donnent un retour global
flatteur, qui reste bon longtemps après que la tranche suivante a cessé d'être
rentable.

L'onglet 7 chiffre donc chaque tranche **seule**, indépendamment de tout ce qui
a été installé avant :

```
  valeur  autonomie  cout sol.  retour cum |  tranche  cout tr.  gain tr.  RETOUR TR.
       0     45.33%      19080         9.5 |        -         -         -           -
      16     66.38%      20632         7.2 |      +16      1552       856         1.8
      32     76.68%      22184         6.7 |      +16      1552       419         3.5
      48     82.85%      23736         6.6 |      +16      1552       251         5.6
      64     86.33%      25288         6.7 |      +16      1552       142         9.3
      80     88.20%      26840         6.9 |      +16      1552        76        15.2
      96     89.40%      28392         7.2 |      +16      1552        49        20.9
     112     90.21%      29944         7.5 |      +16      1552        33      >horiz
     128     90.72%      31496         7.8 |      +16      1552        21      >horiz
```

Le retour cumulé ne quitte jamais la fourchette 6,6 à 9,5 ans. Le retour de la
tranche, lui, passe de **1,8 an à jamais** : le 7ᵉ pack de batterie ne se
remboursera pas dans l'horizon d'analyse, et cela ne se voit que dans cette
colonne. Un seuil réglable dans la barre du haut (*tranche à rembourser en
moins de N ans*) sert de critère d'arrêt.

## Optimum puissance PV × batterie

Panneaux et batterie ne se dimensionnent pas l'un après l'autre : des panneaux
sans batterie produisent un surplus qu'on jette, une batterie sans panneaux n'a
rien à stocker. Le sous-onglet *Optimum PV × batterie* simule la grille
complète — une simulation dure moins d'un dixième de seconde, une grille de
72 points prend 7 secondes — et affiche la carte du critère choisi.

Le résultat dépend fortement du critère, et c'est le point :

| Critère | Résultat | Coût | Autonomie |
|---|---|---|---|
| Gain cumulé sur 25 ans | 40 kWc / 112 kWh | 33 024 € | 94,0 % |
| Temps de retour le plus court | 15 kWc / 32 kWh | 17 564 € | 66,8 % |
| Autonomie max., tranches rentables | 25 kWc / 48 kWh | 22 196 € | 80,3 % |

Surtout, le **chemin de croissance** donne l'ordre dans lequel agrandir. À
chaque étape il compare les deux seules décisions possibles — un cran de
panneaux ou un cran de batterie — retient celle qui se rembourse le plus vite,
et s'arrête dès qu'elle dépasse le seuil :

```
  etape                      kWc    kWh   cumul    cout  gain/an  retour  autonomie
  Point de depart            5.0      0   11380       -        -       -     27.5 %
  +5.0 kWc de panneaux      10.0      0   12920    1540      343     4.5     36.0 %
  +16 kWh de batterie       10.0     16   14472    1552      666     2.3     52.3 %
  +5.0 kWc de panneaux      15.0     16   16012    1540      281     5.5     59.2 %
  +16 kWh de batterie       15.0     32   17564    1552      309     5.0     66.8 %
  +5.0 kWc de panneaux      20.0     32   19104    1540      198     7.8     71.7 %
  +16 kWh de batterie       20.0     48   20656    1552      194     8.0     76.4 %
  +5.0 kWc de panneaux      25.0     48   22196    1540      156     9.9     80.3 %
  -> la tranche suivante mettrait 12,7 ans : on s'arrete la.
```

C'est la vue à utiliser pour un projet que l'on fait grossir par étapes.
Disponible aussi en ligne de commande :

```bash
python main.py --grille 5:40@5 0:128@16 10      # puissances, capacités, seuil
```

---

## Changer de lieu d'implantation

Onglet **Site et météo** : saisissez latitude et longitude, choisissez les
années, cliquez sur *Télécharger depuis PVGIS*. Le fichier est mis en cache
dans `pv_sizer/data/` et réutilisé automatiquement.

SARAH3 couvre l'Europe, l'Afrique et une partie de l'Asie de 2005 à 2023. Pour
un point hors emprise, basculez sur `PVGIS-ERA5`.

---

## Deux résultats que la simulation horaire corrige

En lançant les balayages de l'onglet 7 sur la configuration par défaut, deux
enseignements ressortent — et ils contredisent ce qu'un modèle mensuel simplifié
laisse croire.

**L'inclinaison optimale est plus faible qu'attendu.** Sur données réelles,
58 % du rayonnement de décembre à Langoiran est **diffus**. Or un plan incliné à
60 degrés ne voit que 75 % de la voûte céleste contre 93 % à 30 degrés. Le gain
de décembre n'est donc que de +18 % en passant de 30 à 60 degrés, là où les
tables climatologiques classiques annoncent +40 %. L'optimum d'autonomie se
situe autour de **40 à 50 degrés**, et l'écart entre 30 et 60 degrés est de
l'ordre du demi-point d'autonomie.

**La batterie compte davantage que la puissance crête.** Passer de 16 à 128 kWh
fait gagner près de 25 points d'autonomie, quand doubler la puissance PV n'en
apporte que 15. Le facteur limitant, dans un système déjà excédentaire huit
mois sur douze, est le report jour/nuit, pas le gisement.

Vérifiez-le vous-même : onglet 7, *Inclinaison de tous les champs*, valeurs
`20, 30, 40, 50, 60, 70, 80`.

---

## Ce qui a été corrigé dans le moteur

Une relecture systématique a mis au jour des grandeurs qui s'affichaient
fausses **sans aucun signe**. Chacune est désormais couverte par un test de
non-régression (`tests/test_moteur.py`).

| Symptôme | Cause | Effet |
|---|---|---|
| Autonomie **négative** dès que *Recharge réseau en HC* est cochée | l'énergie achetée la nuit pour remplir la batterie était comptée à l'achat, puis rendue « autonome » à la restitution | l'origine de l'énergie stockée est maintenant suivie ; sans panneaux l'autonomie vaut 0 %, pas −6,5 % |
| Colonne *Budget conso/jour* : 4,1 kWh/j en janvier pour une autonomie réelle de 2,7 % | la dichotomie renvoyait sa borne basse comme un résultat | les bornes sont vérifiées ; un mois hors d'atteinte affiche `-` en rouge |
| Pompe de piscine de 750 W appelant **1 125 W** | l'énergie journalière était étalée sur le profil sans plafond de puissance | la puissance est plafonnée, le débordement reporté sur les heures voisines et signalé |
| Consigne de chauffage sans aucun effet | `t_consigne` n'était lu nulle part | la consigne décale la température de non-chauffage ; à 19,5 °C (la référence) rien ne change pour les configurations existantes |
| Température lissée à 3,9 °C pour 8,1 °C réels en début de série | `np.convolve(mode="same")` complète par des zéros | bords prolongés par la valeur extrême |
| Véhicule censé charger du lundi au jeudi, chargeant du dimanche au mercredi | décalage de jour de semaine faux d'une unité | corrigé |
| Deux groupes PV de même nom : un seul dans le détail | indexation par le nom | les doublons sont désambiguïsés |
| Poste de 2 500 kWh/an ressortant à 0 kWh/an | douze poids mensuels à zéro faisaient disparaître l'énergie | répartition uniforme de repli |
| Poste dont le *type* est inconnu : ignoré en silence | | affiché à zéro **et** signalé en erreur |
| Batterie de 8 kWh **gratuite** dans le devis | arrondi au plus proche du nombre de packs | arrondi au supérieur, tolérance de 2 % |
| Ligne de devis mise volontairement à 0 : facturée 1 | règle « quantité nulle = 1 » | un zéro explicite vaut zéro, une clé absente vaut 1 |
| *ROI marginal* à 0,000 sur tout un balayage d'inclinaison | le coût ne change pas : le rapport n'existe pas | affiche `-` |
| « durée de vie estimée 6 000 ans », « 2,5e13 EUR/kWh » | divisions non gardées | affiche `-` |
| Cycles batterie sous-estimés de 7 % | énergie comptée côté alternatif, capacité aux bornes | ramenés au même point de mesure |
| L'« optimum » d'un balayage toujours égal à la plus grande valeur testée | critère = autonomie maximale, qui croît toujours | le **gain cumulé sur l'horizon** est affiché à côté : lui sait s'arrêter |

Deux hypothèses économiques ont aussi été rectifiées : le scénario « sans PV »
comptait la veille des onduleurs, qui n'existerait pas sans onduleurs, et il
oubliait le bois que la maison brûlerait de toute façon.

---

## Structure

```
main.py                  lancement, modes --cli / --sweep / --download
config_defaut.json       configuration du domaine
pv_sizer/
  config.py              schémas de paramètres, valeurs par défaut, JSON
  solar.py               position du soleil, transposition, modèle PV
  meteo.py               client PVGIS, cache, chargement des séries
  loads.py               profils de consommation par type de poste
  simulation.py          dispatch horaire, agrégations, économie, balayages
  ui.py                  interface PyQt6
  data/
    meteo_langoiran_2018_2023.csv.gz    6 années horaires PVGIS
```

Le moteur est indépendant de l'interface : `pv_sizer.simulation.simulate(cfg,
meteo)` renvoie un dictionnaire complet, utilisable dans vos propres scripts.

---

## Limites à garder en tête

- Les ombrages sont saisis comme un pourcentage forfaitaire par groupe. Un
  masque réel (arbre, bâtiment, rangée devant rangée) demande un relevé
  d'horizon que le simulateur ne gère pas.
- L'espacement des rangées n'est pas vérifié : à 60 degrés sous cette latitude,
  il faut environ 5,3 m de pas pour éviter l'ombrage mutuel du 21 décembre.
- Le vieillissement des modules et des cellules n'est pas simulé année par
  année ; il est intégré forfaitairement dans les pertes DC.
- Les profils journaliers sont des formes types. Une fois vos mesures par
  circuit disponibles, recalez les postes : c'est ce qui améliorera le plus la
  précision du résultat.

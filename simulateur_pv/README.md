# Simulateur de dimensionnement PV + stockage LFP

Simulation **au pas horaire sur 6 années de météo réelle** d'une installation
photovoltaïque avec batterie, en autoconsommation. Conçu pour arbitrer entre
inclinaison, puissance crête, capacité de batterie et nombre d'onduleurs, et
pour chiffrer le résultat.

---

## Installation

```bash
pip install PyQt6 matplotlib numpy
python main.py
```

Aucune autre dépendance. Testé avec Python 3.11+.

---

## Démarrage

```bash
python main.py                     # interface graphique, config par défaut
python main.py ma_config.json      # interface graphique, config existante
python main.py --cli               # rapport texte complet dans le terminal
python main.py --sweep inclinaison 20,30,40,50,60,70,80
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
5. **Coûts** — nomenclature éditable. La colonne *quantité auto* relie chaque
   ligne à la configuration : panneaux, onduleurs, cellules, packs, kWc,
   surface. Modifier le nombre de panneaux mémorise le coût automatiquement.
6. **Résultats** — bilan mensuel, détail jour par jour pour un mois et une
   année au choix, et la journée moyenne de chaque mois en profil horaire.
7. **Optimisation** — balayage d'un paramètre (inclinaison, puissance crête,
   batterie, onduleurs) avec repérage automatique de l'optimum, et calcul du
   **budget de consommation** : pour chaque mois, la consommation journalière
   maximale compatible avec l'objectif d'autonomie. Deux vues : les profils
   mois par mois de chaque option, et des histogrammes qui comparent les
   options entre elles (cumul sur l'année, moyenne des douze mois, mois le
   plus défavorable, économie et temps de retour).
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

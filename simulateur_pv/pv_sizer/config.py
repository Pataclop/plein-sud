"""
Configuration du simulateur : valeurs par defaut, schemas de parametres,
chargement / sauvegarde JSON.

Tout est en dictionnaires natifs JSON pour que l'UI puisse generer les
formulaires automatiquement a partir des schemas.

Format d'une ligne de schema :
    (cle, libelle_avec_unite, type, min, max, pas_ou_choix, aide_html)
L'aide est affichee en infobulle au survol du libelle ET du champ.
"""
from __future__ import annotations
import json, os, copy, datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")


# --------------------------------------------------------------------------
# Bases de donnees meteo PVGIS
#   PVGIS = service gratuit de la Commission europeenne (JRC) qui fournit des
#   series horaires de rayonnement solaire reelles, mesurees par satellite ou
#   issues d'une reanalyse meteo. Chaque base couvre une zone et une periode
#   differentes : c'est la seule raison pour laquelle certaines annees sont
#   refusees au telechargement.
# --------------------------------------------------------------------------
PVGIS_DATABASES = {
    "PVGIS-SARAH3": {
        "annees": (2005, 2023),
        "resume": "Satellite Meteosat, derniere generation (recommandee)",
        "detail": ("Rayonnement observe par les satellites Meteosat et traite par "
                   "le CM SAF. C'est la base la plus precise pour l'Europe, "
                   "l'Afrique et le Moyen-Orient. Resolution ~5 km."),
    },
    "PVGIS-SARAH2": {
        "annees": (2005, 2020),
        "resume": "Satellite Meteosat, generation precedente",
        "detail": ("Version anterieure de SARAH. A n'utiliser que pour comparer "
                   "avec une etude ancienne faite avec cette base."),
    },
    "PVGIS-ERA5": {
        "annees": (2005, 2023),
        "resume": "Reanalyse meteo mondiale (couverture partout, moins precise)",
        "detail": ("Modele de reanalyse du centre europeen ECMWF. Couvre le monde "
                   "entier, y compris les zones sans couverture satellite, mais "
                   "sous-estime souvent la variabilite journaliere. Resolution ~30 km."),
    },
}

PVGIS_ANNEE_MIN = min(v["annees"][0] for v in PVGIS_DATABASES.values())
PVGIS_ANNEE_MAX = max(v["annees"][1] for v in PVGIS_DATABASES.values())
# On laisse saisir jusqu'a l'annee en cours plutot que de bloquer le curseur
# sans explication : l'interface affiche alors, en rouge, pourquoi cette
# annee n'existe pas encore chez PVGIS.
ANNEE_SAISIE_MAX = max(PVGIS_ANNEE_MAX, datetime.date.today().year)


def couverture_base(db: str):
    """Retourne (annee_min, annee_max) couverts par une base PVGIS."""
    return PVGIS_DATABASES.get(db, {}).get("annees", (PVGIS_ANNEE_MIN, PVGIS_ANNEE_MAX))


def aide_base_donnees() -> str:
    """Infobulle detaillee listant chaque base et sa periode couverte."""
    lignes = ["<b>Quelle base de rayonnement solaire utiliser ?</b>",
              "PVGIS ne fabrique pas de meteo : il rejoue des annees reellement "
              "observees. Chaque base a sa source et sa periode.<br>"]
    for nom, d in PVGIS_DATABASES.items():
        a0, a1 = d["annees"]
        lignes.append(f"<b>{nom}</b> &mdash; annees <b>{a0} a {a1}</b><br>"
                      f"{d['resume']}.<br>{d['detail']}<br>")
    lignes.append("<i>En cas de doute : PVGIS-SARAH3.</i>")
    return "<br>".join(lignes)


def aide_annee(borne: str) -> str:
    listes = "<br>".join(
        f"&bull; <b>{n}</b> : {d['annees'][0]} a {d['annees'][1]}"
        for n, d in PVGIS_DATABASES.items())
    return (f"<b>{borne} annee de la serie meteo rejouee.</b><br>"
            f"La simulation ne fabrique pas de meteo moyenne : elle rejoue heure "
            f"par heure les annees reelles de cette periode, puis moyenne les "
            f"resultats. Plus la periode est longue, plus le dimensionnement est "
            f"robuste (3 a 6 ans est un bon compromis).<br><br>"
            f"<b>Pourquoi je ne peux pas prendre une annee tres recente ?</b><br>"
            f"Parce que PVGIS publie ses series avec 1 a 2 ans de retard : les "
            f"donnees satellite doivent etre controlees et calibrees avant "
            f"diffusion. Periodes actuellement disponibles :<br>{listes}<br><br>"
            f"Au-dela, PVGIS renvoie une erreur au telechargement.")


# --------------------------------------------------------------------------
# Profils journaliers (24 poids, normalises a la volee)
# --------------------------------------------------------------------------
def _z(**kw):
    p = [0.0] * 24
    for h, v in kw.items():
        p[int(h[1:])] = float(v)
    return p


DAILY_SHAPES = {
    "constant":       [1.0] * 24,
    "solaire":        _z(h9=.5, h10=1, h11=1, h12=1, h13=1, h14=1, h15=1, h16=1, h17=.5),
    "solaire_large":  _z(h8=.4, h9=.8, h10=1, h11=1, h12=1, h13=1, h14=1, h15=1, h16=1, h17=.8, h18=.4),
    "jour":           _z(h7=.6, h8=.8, h9=1, h10=1, h11=1, h12=1, h13=1, h14=1,
                         h15=1, h16=1, h17=1, h18=1, h19=1, h20=.8, h21=.6, h22=.3),
    "soiree":         _z(h17=.4, h18=.8, h19=1, h20=1, h21=.9, h22=.6, h23=.3),
    "matin_soir":     _z(h6=.5, h7=1, h8=.8, h12=.5, h18=.8, h19=1, h20=1, h21=.7),
    "nuit_hc":        _z(h0=1, h1=1, h2=1, h3=1, h4=1, h5=1, h22=1, h23=1),
    "cuisine":        _z(h7=.3, h12=1, h13=.6, h19=1, h20=1, h21=.4),
    "occupation":     _z(h6=.4, h7=.8, h8=.6, h9=.4, h10=.4, h11=.5, h12=.7, h13=.6,
                         h14=.4, h15=.4, h16=.5, h17=.7, h18=.9, h19=1, h20=1,
                         h21=.9, h22=.6, h23=.3),
}

SHAPES_HELP = {
    "constant": "identique 24 h/24",
    "solaire": "9 h - 17 h, cale sur le pic solaire",
    "solaire_large": "8 h - 18 h, journee solaire elargie",
    "jour": "7 h - 22 h, presence en journee",
    "soiree": "17 h - 23 h, pointe du soir",
    "matin_soir": "deux pointes, matin et soir",
    "nuit_hc": "22 h - 6 h, heures creuses de nuit",
    "cuisine": "midi et 19 h - 21 h",
    "occupation": "profil de maison occupee, pointe le soir",
}

AIDE_PROFIL = ("<b>Repartition de la consommation sur les 24 h d'une journee.</b><br>"
               "Ne change pas le total annuel : seulement le moment ou l'energie "
               "est appelee, donc la part qui peut etre couverte directement par "
               "le soleil plutot que par la batterie ou le reseau.<br><br>"
               + "<br>".join(f"&bull; <b>{k}</b> : {v}" for k, v in SHAPES_HELP.items()))

AIDE_POIDS_MENSUELS = ("<b>Ponderation relative de chaque mois (sans unite).</b><br>"
                       "1,00 = mois moyen. 1,20 = 20 % de plus que la moyenne, "
                       "0,80 = 20 % de moins. Le total annuel saisi plus haut est "
                       "conserve : ces poids ne font que le repartir.")


# --------------------------------------------------------------------------
# Schemas des postes de consommation
#   (cle, libelle, type, min, max, pas/choix, aide)
# --------------------------------------------------------------------------
LOAD_KINDS = {
    "talon": {
        "label": "Talon permanent",
        "help": "Puissance appelee 24h/24 : veilles, froid, VMC, box, hors-gel, circulateurs.",
        "params": [
            ("puissance_w", "Puissance permanente (W)", "float", 0, 5000, 10,
             "<b>Watts appeles en continu, 24 h/24.</b><br>"
             "Somme des appareils qui ne s'arretent jamais : refrigerateur et "
             "congelateur, VMC, box internet, alarme, circulateurs, veilles.<br>"
             "Ordre de grandeur : 150 a 400 W pour une maison.<br>"
             "<i>300 W en continu = 2 628 kWh/an, souvent le premier poste "
             "de consommation d'une maison.</i>"),
            ("variation_hiver", "Majoration hivernale (0 a 2)", "float", 0, 2, .05,
             "<b>Supplement applique de decembre a fevrier, en fraction.</b><br>"
             "0,15 = +15 % en hiver (hors-gel, eclairage plus long, circulateurs).<br>"
             "0 = talon rigoureusement identique toute l'annee."),
        ],
        "defaults": {"puissance_w": 345, "variation_hiver": 0.15},
    },
    "generique": {
        "label": "Poste generique (kWh/an)",
        "help": "Tout usage decrit par une consommation annuelle, un profil horaire et des poids mensuels.",
        "params": [
            ("kwh_an", "Consommation annuelle (kWh/an)", "float", 0, 50000, 50,
             "<b>Energie electrique consommee sur une annee, en kWh.</b><br>"
             "C'est le chiffre lu sur une facture ou sur une prise mesureuse.<br>"
             "Reperes : cuisson induction 700 a 1 200 kWh/an, "
             "electromenager + eclairage 2 000 a 3 500 kWh/an, "
             "lave-linge seul ~200 kWh/an."),
            ("profil", "Profil journalier (24 h)", "choice", None, None,
             list(DAILY_SHAPES), AIDE_PROFIL),
            ("poids_mensuels", "Poids mensuels (1,00 = mois moyen)", "months", 0, 5, .05,
             AIDE_POIDS_MENSUELS),
        ],
        "defaults": {"kwh_an": 1000, "profil": "occupation", "poids_mensuels": [1.0] * 12},
    },
    "chauffage": {
        "label": "Chauffage (PAC air/air)",
        "help": ("Besoin thermique reparti sur les degres-heures reels de la meteo, "
                 "converti en electricite par un COP dependant de la temperature exterieure. "
                 "Au-dela de la puissance de la PAC, l'appoint electrique direct prend le relais."),
        "params": [
            ("besoin_th_kwh_an", "Besoin thermique (kWh th/an)", "float",
             0, 80000, 100,
             "<b>Chaleur a fournir au logement sur une annee, en kWh thermiques.</b><br>"
             "Ce n'est PAS la consommation electrique : la PAC divise ce chiffre "
             "par son COP (~3), donc 28 000 kWh thermiques coutent environ "
             "9 000 kWh electriques.<br>"
             "Reperes : maison de 150 m2 non renovee 20 000 a 30 000 kWh th/an, "
             "bien isolee 8 000 a 15 000 kWh th/an, RT2012 5 000 a 8 000."),
            ("t_base", "Temp. de non-chauffage (C)", "float", 10, 22, .5,
             "<b>Temperature exterieure au-dessus de laquelle on ne chauffe plus.</b><br>"
             "Les apports gratuits (soleil, occupants, appareils) suffisent.<br>"
             "17 C pour une maison ordinaire, 15 C bien isolee, 19 C passoire."),
            ("t_consigne", "Temp. interieure visee (C)", "float", 15, 25, .5,
             "<b>Consigne moyenne du thermostat, en degres C.</b><br>"
             "Moyenne sur la journee et sur toutes les pieces, reduits de nuit "
             "compris. 1 C de moins, c'est environ 7 % de chauffage en moins."),
            ("cop_a", "COP : ordonnee a l'origine", "float", 0.5, 5, .05,
             "<b>Le COP est modelise par COP = a + b x (temperature exterieure).</b><br>"
             "<i>a</i> est le COP theorique a 0 C exterieur. 2,75 pour une PAC "
             "air/air recente ; 2,2 pour un modele ancien.<br>"
             "Le COP est le rendement : 3 kWh de chaleur pour 1 kWh electrique = COP 3."),
            ("cop_b", "COP : pente b (/C)", "float", 0, .3, .005,
             "<b>Gain de COP par degre exterieur supplementaire.</b><br>"
             "Dans COP = a + b x Text. Typiquement 0,09 : il fait plus froid "
             "dehors, la PAC doit pomper plus haut, son rendement chute."),
            ("cop_max", "COP plafond", "float", 2, 7, .1,
             "<b>Plafond applique au COP par temps doux.</b><br>"
             "La formule lineaire diverge quand il fait chaud ; on la bride ici. "
             "4,6 est realiste pour une PAC air/air moderne."),
            ("p_pac_kw_th", "Puissance PAC (kW th a 7 C)", "float",
             0, 60, .5,
             "<b>Chaleur maximale que la PAC peut delivrer, en kW, mesuree a "
             "+7 C exterieur (point de reference normalise).</b><br>"
             "Au-dela de ce plafond, l'appoint electrique direct (COP 1) prend "
             "le relais et fait exploser la consommation.<br>"
             "Repere : 1 kW thermique pour 12 a 20 m2 selon l'isolation."),
            ("declassement_froid", "Declassement a -7 C (0 a 1)", "float", 0, .6, .01,
             "<b>Part de puissance perdue quand il fait -7 C plutot que +7 C.</b><br>"
             "0,28 = la PAC ne delivre plus que 72 % de sa puissance nominale. "
             "Le declassement est interpole lineairement entre les deux points.<br>"
             "0,20 a 0,30 pour une PAC air/air, moins pour un modele grand froid."),
            ("part_bois", "Part couverte par le bois (0 a 1)", "float", 0, 1, .05,
             "<b>Fraction du besoin de chauffage reprise par un insert ou un poele, "
             "uniquement en dessous du seuil d'allumage ci-dessous.</b><br>"
             "0,35 = un tiers du besoin des jours froids part sur le bois, donc "
             "en moins sur l'electricite. 0 = pas de bois."),
            ("t_bois", "Seuil d'allumage du bois (C)", "float", -10, 15, .5,
             "<b>En dessous de cette temperature exterieure, le poele est allume.</b><br>"
             "5 C : on allume les vraies journees froides. "
             "12 C : on allume des les mi-saisons."),
            ("inertie_h", "Lissage d'inertie (heures)", "float", 0, 12, 1,
             "<b>Moyenne glissante appliquee a la temperature exterieure, en heures.</b><br>"
             "Represente l'inertie thermique du batiment : une maison lourde ne "
             "suit pas la temperature exterieure instantanee.<br>"
             "6 h pour du parpaing ou de la pierre, 2 h pour de l'ossature bois, "
             "0 pour desactiver."),
        ],
        "defaults": {"besoin_th_kwh_an": 28240, "t_base": 17.0, "t_consigne": 19.5,
                     "cop_a": 2.75, "cop_b": 0.093, "cop_max": 4.6, "p_pac_kw_th": 26.0,
                     "declassement_froid": 0.28, "part_bois": 0.35, "t_bois": 5.0,
                     "inertie_h": 6},
    },
    "ecs": {
        "label": "Eau chaude sanitaire",
        "help": "Ballon a resistance (COP 1) ou chauffe-eau thermodynamique (COP 2,5 a 3,2).",
        "params": [
            ("besoin_th_kwh_an", "Besoin thermique annuel (kWh thermiques/an)", "float",
             0, 20000, 50,
             "<b>Chaleur necessaire pour chauffer l'eau sanitaire sur une annee.</b><br>"
             "Pas la consommation electrique : elle sera divisee par le COP.<br>"
             "Repere : environ 800 kWh thermiques par personne et par an "
             "(soit 3 200 kWh th/an pour 4 personnes)."),
            ("cop", "COP de production", "float", 0.8, 4, .05,
             "<b>Rendement de l'appareil : kWh de chaleur produits par kWh "
             "electrique consomme.</b><br>"
             "&bull; 1,0 = ballon a resistance electrique classique<br>"
             "&bull; 2,5 a 3,2 = chauffe-eau thermodynamique (pompe a chaleur)<br>"
             "Passer de 1,0 a 2,8 divise la facture d'eau chaude par pres de 3."),
            ("profil", "Profil journalier (repartition sur 24 h)", "choice", None, None,
             list(DAILY_SHAPES), AIDE_PROFIL +
             "<br><br><i>Choisissez 'solaire' si le ballon est pilote pour chauffer "
             "en pleine journee : c'est ce qui maximise l'autoconsommation.</i>"),
            ("majoration_hiver", "Majoration hivernale (0 a 1)", "float", 0, 1, .05,
             "<b>Supplement de besoin en hiver, en fraction.</b><br>"
             "L'eau du reseau arrive a 8 C en janvier contre 18 C en aout : il "
             "faut plus d'energie pour la porter a 55 C.<br>"
             "0,20 = +20 % en hiver, valeur typique en France."),
        ],
        "defaults": {"besoin_th_kwh_an": 3400, "cop": 2.8, "profil": "solaire",
                     "majoration_hiver": 0.20},
    },
    "piscine": {
        "label": "Piscine (filtration)",
        "help": "Pompe de filtration, duree journaliere reglable mois par mois.",
        "params": [
            ("p_pompe_w", "Puissance de la pompe (W)", "float", 0, 5000, 25,
             "<b>Puissance electrique absorbee par la pompe de filtration, en watts.</b><br>"
             "Lue sur la plaque signaletique. 550 a 1 100 W pour une pompe "
             "classique, 150 a 400 W pour une pompe a vitesse variable en "
             "regime lent (bien plus economique)."),
            ("heures_par_mois", "Filtration (heures/jour)", "months",
             0, 24, .5,
             "<b>Nombre d'heures de filtration par jour, pour chaque mois.</b><br>"
             "Regle usuelle : heures/jour = temperature de l'eau divisee par 2 "
             "(eau a 24 C -> 12 h/jour).<br>"
             "Faites-les tomber en pleine journee pour consommer le solaire."),
            ("profil", "Profil journalier (repartition sur 24 h)", "choice", None, None,
             list(DAILY_SHAPES), AIDE_PROFIL),
            ("pac_piscine_kwh_an", "PAC piscine (kWh elec/an)", "float",
             0, 20000, 50,
             "<b>Consommation electrique annuelle du rechauffeur, en kWh.</b><br>"
             "0 si la piscine n'est pas chauffee.<br>"
             "Repere : 2 000 a 5 000 kWh/an pour une PAC de piscine sur un "
             "bassin couvert de taille moyenne."),
        ],
        "defaults": {"p_pompe_w": 750,
                     "heures_par_mois": [2, 2, 3, 5, 8, 10, 12, 12, 8, 4, 2, 2],
                     "profil": "solaire", "pac_piscine_kwh_an": 0},
    },
    "spa": {
        "label": "Spa / jacuzzi",
        "help": ("Modele physique : pertes proportionnelles a l'ecart entre la consigne "
                 "et la temperature exterieure reelle, plus la consommation des pompes."),
        "params": [
            ("ua_w_par_k", "Pertes UA (W/C d'ecart)", "float",
             0, 80, .5,
             "<b>Watts perdus par degre d'ecart entre l'eau et l'air exterieur.</b><br>"
             "C'est la qualite de l'isolation de la cuve et de la couverture.<br>"
             "&bull; 10 a 14 W/K : spa rigide bien isole, couverture en place<br>"
             "&bull; 20 a 30 W/K : spa gonflable ou couverture usee<br>"
             "<i>Exemple : 12 W/K avec 30 C d'ecart = 360 W en permanence, "
             "soit 3 150 kWh/an.</i>"),
            ("t_consigne", "Temperature de consigne (C)", "float", 20, 40, .5,
             "<b>Temperature maintenue dans le spa.</b><br>"
             "37 C en usage confort. Chaque degre en moins reduit les pertes "
             "d'environ 4 %. Baisser a 30 C hors periode d'usage change tout."),
            ("cop", "COP du rechauffeur", "float", 0.8, 6, .1,
             "<b>Rendement du systeme de chauffe.</b><br>"
             "&bull; 1,0 = resistance electrique (cas de la quasi-totalite des spas)<br>"
             "&bull; 4 a 5 = pompe a chaleur de spa, en option sur les modeles haut de gamme"),
            ("pompes_kwh_an", "Pompes et jets (kWh/an)", "float", 0, 3000, 25,
             "<b>Consommation electrique annuelle des pompes de filtration et de "
             "massage, hors chauffage.</b><br>"
             "200 a 500 kWh/an selon la frequence d'utilisation."),
            ("profil", "Profil de chauffe (24 h)", "choice", None, None,
             list(DAILY_SHAPES), AIDE_PROFIL +
             "<br><br><i>'constant' est realiste : un spa se maintient en "
             "permanence. 'solaire' suppose une chauffe pilotee en journee.</i>"),
        ],
        "defaults": {"ua_w_par_k": 12.0, "t_consigne": 37.0, "cop": 1.0,
                     "pompes_kwh_an": 300, "profil": "constant"},
    },
    "vehicule": {
        "label": "Vehicule electrique",
        "help": "Recharge, pilotable en heures solaires ou nocturnes.",
        "params": [
            ("km_an", "Kilometrage annuel (km/an)", "float", 0, 60000, 500,
             "<b>Distance parcourue en une annee, en kilometres.</b><br>"
             "Moyenne francaise : environ 12 000 km/an."),
            ("conso_kwh_100km", "Consommation (kWh/100 km)", "float", 8, 35, .5,
             "<b>Energie consommee aux 100 km, pertes de charge comprises.</b><br>"
             "Comptez la valeur affichee au tableau de bord plus 10 a 15 % de "
             "pertes dans le chargeur.<br>"
             "Reperes : citadine 15 a 18, berline 18 a 22, SUV ou break 22 a 28."),
            ("profil", "Profil de recharge (24 h)", "choice", None, None,
             list(DAILY_SHAPES), AIDE_PROFIL +
             "<br><br><i>'solaire_large' = recharge pilotee sur le surplus "
             "photovoltaique, le meilleur cas. 'nuit_hc' = recharge de nuit, "
             "donc prise sur la batterie ou le reseau.</i>"),
            ("jours_par_semaine", "Jours de recharge/semaine", "float", 1, 7, 1,
             "<b>Nombre de jours par semaine ou le vehicule est branche.</b><br>"
             "Le besoin annuel est concentre sur ces jours-la : moins de jours "
             "signifie des recharges plus intenses, donc plus difficiles a "
             "couvrir par le solaire."),
        ],
        "defaults": {"km_an": 0, "conso_kwh_100km": 19.0, "profil": "solaire_large",
                     "jours_par_semaine": 4},
    },
}


# --------------------------------------------------------------------------
# Schema du systeme (onduleurs / batterie / reseau)
# --------------------------------------------------------------------------
SYSTEM_SCHEMA = [
    ("__grp", "Onduleurs hybrides", None, None, None, None, ""),
    ("n_onduleurs", "Nombre d'onduleurs (u)", "int", 1, 12, 1,
     "<b>Combien d'onduleurs hybrides sont installes en parallele.</b><br>"
     "Un onduleur hybride gere a la fois les panneaux, la batterie et le "
     "reseau. Leurs puissances et leurs limites PV s'additionnent."),
    ("p_nom_kw", "Puissance AC unitaire (kW)", "float", 1, 50, .5,
     "<b>Puissance de sortie continue d'UN onduleur, en kW.</b><br>"
     "C'est le debit maximal vers la maison. Toute production qui depasse "
     "n_onduleurs x cette valeur est ecretee (perdue).<br>"
     "Exemple : Deye SUN-12K = 12 kW."),
    ("pv_max_kwc_par_onduleur", "PV admissible unitaire (kWc)", "float",
     1, 40, .1,
     "<b>Puissance crete de panneaux que le constructeur autorise sur UN onduleur.</b><br>"
     "Toujours superieure a la puissance AC (surdimensionnement volontaire : "
     "les panneaux atteignent rarement leur crete). Ratio courant 1,2 a 1,5.<br>"
     "Sert au controle de coherence affiche sous le tableau des champs."),
    ("eff_pv_ac", "Rendement PV vers AC (0 a 1)", "float", .85, 1, .001,
     "<b>Rendement de la conversion directe panneaux -> maison.</b><br>"
     "0,972 = 2,8 % de pertes dans l'electronique. C'est le chemin le plus "
     "efficace : l'energie ne passe pas par la batterie."),
    ("eff_pv_batt", "Rendement PV vers batterie (0 a 1)", "float", .85, 1, .001,
     "<b>Rendement du transfert panneaux -> batterie (cote continu).</b><br>"
     "Ne comprend pas le rendement chimique de la batterie, saisi plus bas."),
    ("eff_batt_ac", "Rendement batterie vers AC (0 a 1)", "float", .85, 1, .001,
     "<b>Rendement de la restitution batterie -> maison.</b><br>"
     "Combine avec le rendement de decharge, il donne le cout reel d'un "
     "stockage : environ 10 % de l'energie stockee est perdue a l'aller-retour."),
    ("veille_w", "Conso a vide, jour (W/onduleur)", "float", 0, 300, 1,
     "<b>Watts absorbes en permanence par UN onduleur pour fonctionner, "
     "quand il produit.</b><br>"
     "55 W x 2 onduleurs x 24 h = 964 kWh/an consommes juste pour exister. "
     "Ce poste est souvent sous-estime dans les devis."),
    ("veille_nuit_w", "Conso a vide, nuit (W/onduleur)", "float", 0, 300, 1,
     "<b>Watts absorbes par onduleur la nuit, en veille profonde.</b><br>"
     "Inferieur a la valeur de jour car les etages de puissance PV sont "
     "au repos."),
    ("__grp_dc", "Limites d'entree continue (DC) des onduleurs", None, None, None, None, ""),
    ("vdc_max_v", "Tension DC maximale (V)", "float", 100, 1500, 10,
     "<b>Tension continue maximale que l'entree PV de l'onduleur supporte, en volts.</b><br>"
     "A ne JAMAIS depasser, meme une seconde : c'est une destruction immediate "
     "et hors garantie.<br>"
     "Le cas dimensionnant est le petit matin d'hiver, panneaux glaces et a "
     "vide : la tension Voc monte quand il fait froid. L'onglet Champs PV "
     "calcule cette tension a froid pour chaque grappe.<br>"
     "Repere : 500 V sur les petits hybrides, 800 a 1 000 V sur les Deye triphases."),
    ("vmppt_min_v", "Tension MPPT minimale (V)", "float", 50, 800, 5,
     "<b>Tension en dessous de laquelle le suiveur MPPT ne demarre plus.</b><br>"
     "Une grappe trop courte ne produira rien le matin, le soir et par temps "
     "couvert. La tension de travail (Vmp) chute aussi quand les panneaux "
     "chauffent : gardez de la marge.<br>"
     "Repere : 125 a 200 V sur les Deye."),
    ("i_max_string_a", "Courant max par MPPT (A)", "float", 5, 60, .5,
     "<b>Courant continu maximal accepte sur une entree MPPT, en amperes.</b><br>"
     "Une grappe qui depasse cette valeur sera bridee : la production est "
     "perdue. Comparez-la au courant Isc calcule dans l'onglet Champs PV.<br>"
     "Repere : 13 a 26 A selon les modeles."),
    ("n_mppt_par_onduleur", "Entrees MPPT par onduleur", "int", 1, 8, 1,
     "<b>Nombre de suiveurs de point de puissance maximale (MPPT) sur UN onduleur.</b><br>"
     "Chaque MPPT pilote independamment une ou deux grappes : c'est ce qui "
     "permet de melanger des orientations differentes sans que la moins bonne "
     "penalise l'autre.<br>"
     "Regle : une orientation ou une inclinaison differente = un MPPT different."),
    ("__grp2", "Batterie LFP", None, None, None, None, ""),
    ("batt_kwh_nominal", "Capacite nominale (kWh)", "float", 0, 400, 1,
     "<b>Capacite totale marquee sur la batterie, en kWh.</b><br>"
     "L'energie reellement exploitable est plus faible : voir la profondeur "
     "de decharge juste en dessous. Le resultat utile est affiche a droite "
     "apres simulation."),
    ("dod", "Profondeur de decharge (0 a 1)", "float", .5, 1, .01,
     "<b>Part de la capacite nominale que l'on s'autorise a utiliser.</b><br>"
     "0,90 = on exploite 90 % et on garde 5 % de reserve en haut et en bas, "
     "ce qui allonge fortement la duree de vie des cellules LFP.<br>"
     "Exemple : 64 kWh nominaux a 0,90 de DoD = 57,9 kWh utiles."),
    ("eff_charge", "Rendement de charge (0 a 1)", "float", .85, 1, .005,
     "<b>Part de l'energie envoyee qui est reellement stockee.</b><br>"
     "0,985 pour du LFP, tres bon. Le reste part en chaleur."),
    ("eff_decharge", "Rendement de decharge (0 a 1)", "float", .85, 1, .005,
     "<b>Part de l'energie stockee qui est reellement restituee.</b><br>"
     "Multiplie par le rendement de charge, cela donne le rendement "
     "aller-retour de la chimie seule (~97 % en LFP)."),
    ("c_rate_charge", "Regime de charge max (C)", "float", .1, 1, .05,
     "<b>Vitesse de charge maximale, exprimee en C (fraction de la capacite "
     "par heure).</b><br>"
     "0,5 C sur une batterie de 64 kWh = 32 kW de charge maximale, "
     "soit une charge complete en 2 heures.<br>"
     "0,5 C est la limite usuelle des cellules LFP prismatiques."),
    ("c_rate_decharge", "Regime de decharge max (C)", "float", .1, 1, .05,
     "<b>Vitesse de decharge maximale, en C.</b><br>"
     "0,5 C sur 64 kWh = 32 kW disponibles instantanement. Doit couvrir "
     "votre pointe de consommation."),
    ("soc_initial", "Charge au demarrage (0 a 1)", "float", 0, 1, .05,
     "<b>Remplissage de la batterie a la premiere heure simulee, en fraction "
     "de la capacite utile.</b><br>"
     "0,5 = a moitie pleine. N'influence que les tout premiers jours de la "
     "serie, sans effet sur le bilan annuel."),
    ("__grp3", "Reseau", None, None, None, None, ""),
    ("p_souscrite_kva", "Puissance souscrite (kVA)", "float", 3, 60, 3,
     "<b>Puissance de votre abonnement Enedis, en kVA.</b><br>"
     "Plafond du soutirage possible : au-dela, le disjoncteur saute. La "
     "simulation compte l'energie qui n'aurait pas pu etre fournie et vous "
     "alerte."),
    ("injection_nulle", "Injection nulle (surplus ecrete)", "bool", None, None, None,
     "<b>Coche</b> : aucun kWh n'est renvoye sur le reseau, le surplus est "
     "simplement perdu (bride par l'onduleur). C'est le regime en "
     "autoconsommation totale, sans contrat de revente et sans demarche Enedis.<br>"
     "<b>Decoche</b> : le surplus est injecte et valorise au prix de revente "
     "saisi dans l'onglet Couts."),
    ("recharge_reseau_hc", "Recharge reseau en HC", "bool",
     None, None, None,
     "<b>Coche</b> : si la batterie descend sous la cible ci-dessous pendant "
     "les heures creuses, elle est completee depuis le reseau.<br>"
     "Utile en hiver quand le solaire ne suffit pas et que le kWh de nuit est "
     "moins cher. Attention : cela fait baisser le taux d'autonomie affiche."),
    ("soc_cible_hc", "Charge visee en HC (0 a 1)", "float",
     0, 1, .05,
     "<b>Niveau jusqu'auquel la batterie est completee depuis le reseau, en "
     "fraction de la capacite utile.</b><br>"
     "0,40 = on remonte a 40 %, de quoi passer le matin sans laisser de la "
     "place pour le soleil de la journee."),
    ("hc_debut", "Debut heures creuses (h)", "int", 0, 23, 1,
     "<b>Heure de debut du tarif heures creuses.</b><br>"
     "Typiquement 22 h ou 2 h selon votre contrat. Voir votre facture."),
    ("hc_fin", "Fin heures creuses (h)", "int", 0, 23, 1,
     "<b>Heure de fin du tarif heures creuses.</b><br>"
     "Une plage a cheval sur minuit est acceptee (exemple : 22 -> 6)."),
]

SYSTEM_DEFAULTS = {
    "n_onduleurs": 2, "p_nom_kw": 12.0, "pv_max_kwc_par_onduleur": 15.6,
    "eff_pv_ac": 0.972, "eff_pv_batt": 0.975, "eff_batt_ac": 0.945,
    "veille_w": 55.0, "veille_nuit_w": 35.0,
    "vdc_max_v": 800.0, "vmppt_min_v": 160.0, "i_max_string_a": 26.0,
    "n_mppt_par_onduleur": 2,
    "batt_kwh_nominal": 64.3, "dod": 0.90, "eff_charge": 0.985, "eff_decharge": 0.985,
    "c_rate_charge": 0.5, "c_rate_decharge": 0.5, "soc_initial": 0.5,
    "p_souscrite_kva": 12.0, "injection_nulle": True,
    "recharge_reseau_hc": False, "soc_cible_hc": 0.4, "hc_debut": 2, "hc_fin": 6,
}

SITE_SCHEMA = [
    ("nom", "Nom du site (libre)", "text", None, None, None,
     "<b>Etiquette libre pour retrouver cette configuration.</b><br>"
     "N'a aucun effet sur le calcul."),
    ("latitude", "Latitude (deg. decimaux)", "float", -60, 70, .0001,
     "<b>Latitude du site en degres decimaux, positive vers le nord.</b><br>"
     "Exemple : Bordeaux = 44,84 ; Lille = 50,63 ; Marseille = 43,30.<br>"
     "Attention : 44,71 et non 44 degres 42 minutes. Un clic droit sur Google "
     "Maps donne directement le format decimal.<br>"
     "Determine la hauteur du soleil, donc l'inclinaison optimale des panneaux."),
    ("longitude", "Longitude (deg. decimaux)", "float", -180, 180, .0001,
     "<b>Longitude du site en degres decimaux, positive vers l'est.</b><br>"
     "Negative a l'ouest de Greenwich : Bordeaux = -0,58.<br>"
     "Sert a caler l'heure solaire vraie sur l'heure locale."),
    ("albedo", "Albedo du sol (0 a 0,9)", "float", 0, .9, .01,
     "<b>Part du rayonnement que le sol renvoie vers les panneaux.</b><br>"
     "Compte surtout pour les panneaux tres inclines ou bifaciaux.<br>"
     "&bull; 0,15 : bitume, sol sombre<br>"
     "&bull; 0,20 : herbe, terre (valeur par defaut)<br>"
     "&bull; 0,30 : gravier clair, beton<br>"
     "&bull; 0,60 et plus : neige"),
    ("annee_debut", "Premiere annee meteo", "int", PVGIS_ANNEE_MIN,
     ANNEE_SAISIE_MAX, 1, aide_annee("Premiere")),
    ("annee_fin", "Derniere annee meteo", "int", PVGIS_ANNEE_MIN,
     ANNEE_SAISIE_MAX, 1, aide_annee("Derniere")),
    ("base_donnees", "Base meteo PVGIS", "choice", None, None,
     list(PVGIS_DATABASES), aide_base_donnees()),
]

SITE_DEFAULTS = {
    "nom": "Langoiran (Gironde)", "latitude": 44.71, "longitude": -0.39,
    "albedo": 0.20, "annee_debut": 2018, "annee_fin": 2023,
    "base_donnees": "PVGIS-SARAH3",
}

AIDE_FAIMAN = (
    "<b>Modele de Faiman : quelle temperature atteignent les panneaux ?</b><br>"
    "Un panneau chaud produit moins. Faiman est la formule normalisee (CEI 61853) "
    "qui estime cette temperature a partir du rayonnement et du vent :<br>"
    "<i>T_module = T_air + Rayonnement / (U0 + U1 x vitesse_du_vent)</i><br><br>"
    "<b>U0</b> capte le refroidissement de base (rayonnement, convection naturelle) "
    "et <b>U1</b> le refroidissement supplementaire apporte par le vent. Plus U0 et "
    "U1 sont grands, mieux le panneau est refroidi, donc plus il produit.<br><br>"
    "C'est le seul \"modele\" a choisir ici, et il remplace l'ancienne notion de "
    "NOCT, trop grossiere car mesuree a un seul point de fonctionnement.")

MODULE_SCHEMA = [
    ("gamma_pmax", "Coef. temperature Pmax (/C)", "float",
     -.006, 0, .0001,
     "<b>Perte de puissance par degre au-dessus de 25 C, en fraction.</b><br>"
     "Valeur negative, lue sur la fiche technique du panneau, ligne "
     "\"Temperature coefficient of Pmax\".<br>"
     "-0,0035 signifie -0,35 %/C : a 65 C de cellule, le panneau a perdu 14 % "
     "de sa puissance. Typique : -0,0030 a -0,0040."),
    ("beta_voc_pct_k", "Coef. temperature Voc (%/C)", "float",
     -0.6, 0, .005,
     "<b>Variation de la tension a vide par degre, en pourcent par degre C.</b><br>"
     "Valeur negative : quand il fait FROID, la tension MONTE. C'est ce qui "
     "detruit les onduleurs surdimensionnes un matin de gel.<br>"
     "Lue sur la fiche technique, ligne \"Temperature coefficient of Voc\". "
     "Typique : -0,25 a -0,30 %/C.<br>"
     "<i>Sert au calcul de la tension de grappe a froid, onglet Champs PV.</i>"),
    ("noct_u0", "Faiman U0 (W/m2/C)", "float", 10, 40, .5,
     AIDE_FAIMAN + "<br><br><b>Valeurs de U0 :</b><br>"
     "&bull; 29 : pose libre au sol, tres ventilee<br>"
     "&bull; 25 : pose sur structure ventilee (par defaut)<br>"
     "&bull; 20 : toiture avec lame d'air reduite<br>"
     "&bull; 15 : integration au bati, panneau etouffe"),
    ("noct_u1", "Faiman U1 (W/m2/C par m/s)", "float", 0, 12, .1,
     AIDE_FAIMAN + "<br><br><b>Valeurs de U1 :</b><br>"
     "&bull; 6,84 : pose libre, le vent balaie les deux faces (par defaut)<br>"
     "&bull; 3 a 4 : toiture, une seule face exposee<br>"
     "&bull; 0 a 1 : integration au bati, pas de ventilation"),
    ("modele_diffus", "Modele de ciel diffus", "choice", None, None,
     ["reindl", "hay_davies", "isotrope"],
     "<b>Comment le rayonnement diffus est reparti sur un plan incline.</b><br>"
     "Le diffus, c'est la lumiere qui vient de tout le ciel plutot que du "
     "disque solaire : sous nos latitudes il represente environ <b>40 % du "
     "rayonnement annuel</b>, et la quasi-totalite par temps couvert. Le "
     "choix du modele n'est donc pas un detail.<br><br>"
     "&bull; <b>reindl</b> (par defaut) : diffus anisotrope avec brillance "
     "d'horizon. Le bas du ciel est plus lumineux que le zenith, et un plan "
     "incline le voit mieux qu'un plan horizontal. C'est le plus juste pour "
     "les fortes inclinaisons.<br>"
     "&bull; <b>hay_davies</b> : aureole autour du soleil plus voute "
     "uniforme, sans brillance d'horizon. Legerement pessimiste au-dela de "
     "45 degres.<br>"
     "&bull; <b>isotrope</b> : ciel uniforme, modele le plus simple. "
     "Nettement pessimiste par beau temps ; utile comme borne basse.<br><br>"
     "<i>Par temps gris, les trois modeles convergent : le ciel est "
     "reellement uniforme, et un plan incline a 60 degres ne voit plus que "
     "75 % de la voute celeste contre 100 % a plat. C'est la vraie raison "
     "pour laquelle une forte inclinaison perd du rendement les jours "
     "couverts.</i>"),
    ("iam_b0", "IAM b0, ASHRAE (sans unite)", "float", 0, .3, .005,
     "<b>IAM = Incidence Angle Modifier : pertes par reflexion quand le soleil "
     "arrive de biais sur la vitre.</b><br>"
     "Un rayon perpendiculaire traverse presque tout ; un rayon rasant se "
     "reflechit en grande partie. Formule ASHRAE : "
     "<i>IAM = 1 - b0 x (1/cos(angle) - 1)</i>.<br>"
     "&bull; 0,05 : verre standard (par defaut)<br>"
     "&bull; 0,03 : verre a traitement antireflet<br>"
     "&bull; 0 : desactive la correction<br>"
     "Compte surtout le matin, le soir, et sur les panneaux tres inclines."),
    ("pertes_dc_pct", "Pertes DC diverses (%)", "float", 0, 30, .5,
     "<b>Toutes les pertes continues non modelisees ailleurs, en pourcent.</b><br>"
     "&bull; salissures et poussieres : 2 a 3 %<br>"
     "&bull; dispersion entre modules (mismatch) : 1 a 2 %<br>"
     "&bull; chute de tension dans le cablage DC : 1 a 2 %<br>"
     "&bull; degradation initiale (LID) et vieillissement : 2 a 3 %<br>"
     "8 % au total est une hypothese prudente et courante.<br>"
     "<i>N'incluez pas l'ombrage ici : il se saisit par groupe de panneaux.</i>"),
]

MODULE_DEFAULTS = {"gamma_pmax": -0.0035, "beta_voc_pct_k": -0.27, "noct_u0": 25.0,
                   "noct_u1": 6.84, "modele_diffus": "reindl", "iam_b0": 0.05,
                   "pertes_dc_pct": 8.0}

ECO_SCHEMA = [
    ("prix_kwh_achat", "Prix du kWh soutire (EUR/kWh)", "float", 0, 1.5, .001,
     "<b>Prix payes pour un kWh pris sur le reseau, taxes comprises.</b><br>"
     "Se lit sur la facture : montant consommation divise par kWh consommes. "
     "Ne comptez pas l'abonnement ici, il a son propre champ.<br>"
     "Repere 2024 : environ 0,20 EUR/kWh en tarif bleu base."),
    ("abonnement_an", "Abonnement annuel (EUR/an)", "float", 0, 3000, 10,
     "<b>Part fixe de la facture, en euros par an.</b><br>"
     "Elle reste due meme avec 100 % d'autonomie, tant que vous gardez le "
     "raccordement. Repere : 150 a 300 EUR/an selon la puissance souscrite."),
    ("prix_kwh_revente", "Prix du kWh injecte (EUR/kWh)", "float", 0, .5, .001,
     "<b>Tarif de rachat du surplus renvoye sur le reseau.</b><br>"
     "Mettez 0 en installation autonome sans contrat d'obligation d'achat : "
     "le surplus est alors simplement perdu.<br>"
     "Sans effet si la case \"Injection nulle\" est cochee dans l'onglet 4."),
    ("facture_actuelle_an", "Facture actuelle (EUR/an)",
     "float", 0, 30000, 50,
     "<b>Ce que vous depensez aujourd'hui en energie, avant travaux.</b><br>"
     "Additionnez electricite, gaz, fioul, bois achete. C'est la reference du "
     "calcul de retour sur investissement affiche en haut des resultats."),
    ("cout_bois_stere", "Prix du stere de bois (EUR/stere)", "float", 0, 200, 5,
     "<b>Prix d'achat d'un stere de bois de chauffage.</b><br>"
     "Mettez 0 si vous le produisez vous-meme. Repere : 70 a 110 EUR/stere "
     "en livraison, moins en bois long."),
    ("pci_bois_kwh_stere", "Energie du stere (kWh/stere)", "float",
     800, 2500, 50,
     "<b>Chaleur reellement restituee dans la piece par un stere, en kWh.</b><br>"
     "Deja net du rendement de l'appareil.<br>"
     "&bull; 1 500 kWh/stere : chene sec dans un insert moderne (par defaut)<br>"
     "&bull; 1 000 kWh/stere : bois humide ou cheminee ouverte<br>"
     "&bull; 1 800 kWh/stere : poele a haut rendement, bois tres sec"),
    ("inflation_energie", "Inflation energie (/an)", "float",
     0, .15, .005,
     "<b>Hausse annuelle supposee du prix de l'energie, en fraction.</b><br>"
     "0,04 = +4 %/an. Appliquee aux economies futures dans le calcul du temps "
     "de retour : plus l'energie augmente, plus l'installation est rentable.<br>"
     "Moyenne constatee en France sur 20 ans : 3 a 5 %/an."),
    ("duree_analyse_ans", "Horizon d'analyse (ans)", "int", 5, 40, 1,
     "<b>Duree sur laquelle le gain cumule est calcule, en annees.</b><br>"
     "25 ans correspond a la garantie de production usuelle des panneaux. "
     "Les onduleurs et la batterie seront probablement remplaces une fois "
     "d'ici la : pensez a le prevoir dans la nomenclature."),
]

ECO_DEFAULTS = {"prix_kwh_achat": 0.2016, "abonnement_an": 280.0, "prix_kwh_revente": 0.0,
                "facture_actuelle_an": 6093.0, "cout_bois_stere": 90.0,
                "pci_bois_kwh_stere": 1500.0, "inflation_energie": 0.04,
                "duree_analyse_ans": 25}


# --------------------------------------------------------------------------
# Groupes de panneaux (champs) : valeurs par defaut et aide des colonnes
# --------------------------------------------------------------------------
CHAMP_DEFAUT = {
    "nom": "Nouveau groupe", "actif": True, "n_panneaux": 20, "wc_panneau": 500.0,
    "inclinaison": 40.0, "azimut": 180.0, "surface_m2_panneau": 2.2,
    "ombrage_pct": 0.0, "voc_v": 49.5, "isc_a": 13.9, "n_serie": 10,
}

T_FROID_REF = -10.0   # temperature de dimensionnement de la tension a vide


def n_serie_suggere(n_panneaux, voc_v=49.5, beta_pct=-0.27, vdc_max=800.0) -> int:
    """Nombre de panneaux en serie propose pour un groupe existant.

    On prend le plus grand diviseur du nombre de panneaux (pour que toutes
    les grappes soient identiques) qui laisse encore la tension a vide par
    -10 C sous 95 % de la limite de l'onduleur.
    """
    voc_froid = max(voc_v * (1.0 + beta_pct / 100.0 * (T_FROID_REF - 25.0)), 1e-6)
    plafond = max(int(vdc_max * 0.95 / voc_froid), 1)
    n = max(int(n_panneaux), 1)
    for k in range(min(plafond, n), 0, -1):
        if n % k == 0:
            return k
    return 1

# (cle_interne_ou_None, entete, aide) ; None = colonne calculee
CHAMPS_COLONNES = [
    ("actif", "Actif",
     "<b>Decochez pour exclure ce groupe de la simulation</b> sans le supprimer.<br>"
     "Pratique pour comparer deux scenarios : desactivez le champ toiture et "
     "relancez avec F5."),
    ("nom", "Nom du groupe",
     "<b>Etiquette libre.</b><br>"
     "Elle sert de cle pour les resultats par champ affichees a droite : "
     "donnez des noms distincts."),
    ("n_panneaux", "Nb pann.",
     "<b>Nombre total de panneaux de ce groupe.</b><br>"
     "Tous partagent la meme inclinaison, la meme orientation et le meme "
     "ombrage. Une orientation differente = un autre groupe."),
    ("wc_panneau", "Wc/pann.",
     "<b>Puissance crete d'UN panneau, en watts-crete.</b><br>"
     "Valeur de la fiche technique, mesuree en conditions standard "
     "(1 000 W/m2, cellule a 25 C). C'est un maximum de laboratoire : en "
     "vrai un panneau depasse rarement 80 % de cette valeur.<br>"
     "Repere : 400 a 600 Wc pour un module recent."),
    ("inclinaison", "Inclin. (deg)",
     "<b>Angle des panneaux par rapport a l'horizontale, en degres.</b><br>"
     "&bull; 0 = a plat<br>"
     "&bull; 30 a 35 = maximise le total annuel<br>"
     "&bull; 55 a 70 = privilegie l'hiver (soleil bas), au prix de l'ete. "
     "C'est le bon choix en autonomie, ou c'est decembre qui dimensionne.<br>"
     "&bull; 90 = vertical, sur une facade<br>"
     "<i>Toute inclinaison est recalculee localement : aucun nouveau "
     "telechargement meteo n'est necessaire pour la tester.</i>"),
    ("azimut", "Azimut (deg)",
     "<b>Orientation de la surface des panneaux, en degres.</b><br>"
     "&bull; <b>180 = plein sud</b> (optimal dans l'hemisphere nord)<br>"
     "&bull; 90 = plein est (production du matin)<br>"
     "&bull; 270 = plein ouest (production du soir)<br>"
     "&bull; 135 = sud-est, 225 = sud-ouest<br>"
     "Un ecart de 30 degres par rapport au sud ne coute que 2 a 4 % de "
     "production annuelle."),
    ("surface_m2_panneau", "m2/pann.",
     "<b>Surface hors-tout d'UN panneau, en metres carres.</b><br>"
     "Longueur x largeur de la fiche technique. Sert uniquement au calcul de "
     "l'emprise totale et aux lignes de nomenclature exprimees au m2.<br>"
     "Repere : 2,0 a 2,3 m2 pour un module de 500 Wc."),
    ("ombrage_pct", "Ombrage (%)",
     "<b>Perte moyenne annuelle due aux ombres portees, en pourcent.</b><br>"
     "Arbres, cheminee, batiment voisin, rangees qui s'ombrent entre elles.<br>"
     "&bull; 0 % : degage, horizon libre<br>"
     "&bull; 3 a 5 % : quelques masques lointains en debut et fin de journee<br>"
     "&bull; 10 % et plus : masque proche significatif, a eviter<br>"
     "<i>Applique en reduction uniforme de la production de ce groupe.</i>"),
    ("voc_v", "Voc pann. (V)",
     "<b>Tension a vide d'UN panneau a 25 C, en volts.</b><br>"
     "\"A vide\" = circuit ouvert, aucun courant debite. C'est la tension "
     "maximale que le panneau peut presenter, lue sur la fiche technique "
     "sous \"Voc\" ou \"Open circuit voltage\".<br>"
     "Repere : 45 a 55 V pour un module de 500 Wc.<br>"
     "<i>La colonne \"Voc grappe a froid\" additionne ces tensions et les "
     "corrige du froid : c'est elle qui doit rester sous la limite de "
     "l'onduleur.</i>"),
    ("isc_a", "Isc pann. (A)",
     "<b>Courant de court-circuit d'UN panneau, en amperes.</b><br>"
     "Courant maximal debitable, lu sur la fiche technique sous \"Isc\" ou "
     "\"Short circuit current\". Il ne depend presque pas de la temperature, "
     "mais monte avec le rayonnement.<br>"
     "Repere : 11 a 15 A pour un module de 500 Wc.<br>"
     "<i>Dimensionne le cable, les fusibles, et doit rester sous le courant "
     "maximal admis par l'entree MPPT de l'onduleur.</i>"),
    ("n_serie", "Pann./grappe",
     "<b>Nombre de panneaux cables EN SERIE dans une grappe (string).</b><br>"
     "En serie, les <b>tensions s'additionnent</b> et le courant reste celui "
     "d'un seul panneau. Les grappes sont ensuite mises en parallele : la, ce "
     "sont les <b>courants qui s'additionnent</b>.<br>"
     "Choisir ce nombre, c'est arbitrer :<br>"
     "&bull; trop de panneaux en serie -> tension a froid trop haute, "
     "l'onduleur est detruit<br>"
     "&bull; trop peu -> le MPPT ne demarre pas le matin et par temps couvert<br>"
     "<i>Si le nombre total de panneaux n'est pas un multiple exact, la "
     "derniere grappe sera incomplete : c'est signale en orange.</i>"),
    (None, "Nb grappes",
     "<b>Nombre de grappes (strings) de ce groupe.</b><br>"
     "= nombre de panneaux divise par le nombre de panneaux en serie.<br>"
     "Elles se cablent en parallele sur les entrees MPPT de l'onduleur.<br>"
     "Une valeur non entiere (affichee en orange) signale une grappe "
     "incomplete, a eviter : elle produirait une tension differente des autres."),
    (None, "Voc grappe (V)",
     "<b>Tension a vide d'une grappe en conditions standard.</b><br>"
     "= Voc du panneau x nombre de panneaux en serie.<br>"
     "Valeur de reference, mais ce n'est PAS elle qui dimensionne : voir la "
     "colonne suivante, a froid."),
    (None, "Voc a -10 C (V)",
     "<b>Tension a vide de la grappe par temps froid : la valeur qui "
     "dimensionne l'installation.</b><br>"
     "Calculee avec le coefficient Voc du module (onglet 1) sur un ecart de "
     "35 C sous les conditions standard :<br>"
     "<i>Voc_froid = Voc_25C x (1 + beta x (-10 - 25))</i><br>"
     "Avec beta = -0,27 %/C, cela fait environ <b>+9,5 % de tension</b>.<br>"
     "<b>Cette valeur doit imperativement rester sous la tension DC maximale "
     "de l'onduleur</b> (onglet 4). Elle apparait en rouge si elle depasse, "
     "en orange si la marge est inferieure a 5 %."),
    (None, "Isc total (A)",
     "<b>Courant de court-circuit total du groupe, grappes en parallele.</b><br>"
     "= Isc d'un panneau x nombre de grappes.<br>"
     "A comparer au courant maximal par entree MPPT (onglet 4) une fois "
     "reparti sur les entrees disponibles. Dimensionne aussi la section des "
     "cables et le calibre des fusibles."),
    (None, "kWc",
     "<b>Puissance crete installee sur ce groupe.</b><br>"
     "= nombre de panneaux x puissance unitaire / 1 000."),
    (None, "kWh/kWc/an",
     "<b>Productible : energie annuelle produite par kWc installe.</b><br>"
     "C'est l'indicateur qui permet de comparer deux orientations, "
     "independamment de la taille du champ.<br>"
     "Repere en Gironde : 1 250 a 1 350 kWh/kWc/an a 30 degres plein sud, "
     "un peu moins a 60 degres (mais bien mieux repartis sur l'hiver).<br>"
     "<i>Rempli apres une simulation (F5).</i>"),
    (None, "kWh/an",
     "<b>Production annuelle moyenne de ce groupe, en kWh.</b><br>"
     "Moyenne sur toutes les annees meteo de la serie, avant pertes de "
     "conversion et avant ecretage.<br>"
     "<i>Rempli apres une simulation (F5).</i>"),
]


def eclater_grappes(champ: dict) -> list:
    """Separe un groupe en un groupe par grappe (string).

    Utile avant d'optimiser les orientations : tant que plusieurs grappes
    sont reunies dans un meme groupe, elles partagent forcement la meme
    inclinaison et le meme azimut. Une fois separees, chacune peut recevoir
    sa propre orientation.
    """
    n = max(int(champ.get("n_panneaux", 0)), 0)
    n_ser = max(int(champ.get("n_serie", 1) or 1), 1)
    entieres, reste = divmod(n, n_ser)
    sortie = []
    for k in range(entieres):
        c = copy.deepcopy(champ)
        c["n_panneaux"] = n_ser
        c["nom"] = f"{champ.get('nom', 'Champ')} - grappe {k + 1}"
        sortie.append(c)
    if reste:
        c = copy.deepcopy(champ)
        c["n_panneaux"] = reste
        c["n_serie"] = reste
        c["nom"] = f"{champ.get('nom', 'Champ')} - grappe {entieres + 1} (incomplete)"
        sortie.append(c)
    return sortie or [copy.deepcopy(champ)]


# --------------------------------------------------------------------------
# Nomenclature : quantites automatiques
# --------------------------------------------------------------------------
AUTO_QTY = {
    "fixe": "Quantite saisie manuellement",
    "panneaux": "Nombre total de panneaux",
    "kwc": "Puissance crete installee (kWc)",
    "onduleurs": "Nombre d'onduleurs",
    "batt_kwh": "Capacite batterie nominale (kWh)",
    "cellules": "Nombre de cellules LFP (16S par 5,12 kWh)",
    "packs": "Nombre de packs 16S",
    "m2_panneaux": "Surface de modules (m2)",
}

BOM_DEFAUT = [
    {"poste": "Modules photovoltaiques", "auto": "panneaux", "qte": 0, "pu": 68.0, "unite": "u"},
    {"poste": "Structure et fixations", "auto": "panneaux", "qte": 0, "pu": 72.0, "unite": "u"},
    {"poste": "Onduleurs hybrides Deye", "auto": "onduleurs", "qte": 0, "pu": 1900.0, "unite": "u"},
    {"poste": "Cellules LFP 314 Ah grade A", "auto": "cellules", "qte": 0, "pu": 62.0, "unite": "u"},
    {"poste": "BMS 16S 200 A", "auto": "packs", "qte": 0, "pu": 240.0, "unite": "u"},
    {"poste": "Coffrets, busbars, compression, fusibles classe T", "auto": "packs", "qte": 0, "pu": 320.0, "unite": "u"},
    {"poste": "Cablage DC PV, connecteurs, chemins de cables", "auto": "kwc", "qte": 0, "pu": 28.0, "unite": "kWc"},
    {"poste": "Cablage batterie 95 mm2 et cosses", "auto": "onduleurs", "qte": 0, "pu": 320.0, "unite": "u"},
    {"poste": "Tableaux AC/DC, parafoudres, protections", "auto": "fixe", "qte": 1, "pu": 2600.0, "unite": "lot"},
    {"poste": "Terrassement, tranchee, liaison, mise a la terre", "auto": "fixe", "qte": 1, "pu": 1800.0, "unite": "lot"},
    {"poste": "Supervision et mesure par circuit", "auto": "fixe", "qte": 1, "pu": 600.0, "unite": "lot"},
    {"poste": "Consuel, declaration prealable, convention Enedis", "auto": "fixe", "qte": 1, "pu": 400.0, "unite": "lot"},
    {"poste": "Chauffe-eau thermodynamique 300 L", "auto": "fixe", "qte": 1, "pu": 1900.0, "unite": "u"},
    {"poste": "Insert bois et conduit", "auto": "fixe", "qte": 1, "pu": 4000.0, "unite": "u"},
    {"poste": "Isolation combles et etancheite a l'air", "auto": "fixe", "qte": 1, "pu": 3000.0, "unite": "lot"},
]


# --------------------------------------------------------------------------
# Configuration par defaut complete
# --------------------------------------------------------------------------
def default_config() -> dict:
    return {
        "site": copy.deepcopy(SITE_DEFAULTS),
        "module": copy.deepcopy(MODULE_DEFAULTS),
        "systeme": copy.deepcopy(SYSTEM_DEFAULTS),
        "economie": copy.deepcopy(ECO_DEFAULTS),
        "champs": [
            {**copy.deepcopy(CHAMP_DEFAUT),
             "nom": "Champ hiver (sol, 60 degres)", "n_panneaux": 52,
             "inclinaison": 60.0, "n_serie": 13},
            {**copy.deepcopy(CHAMP_DEFAUT),
             "nom": "Champ toiture (30 degres)", "n_panneaux": 8,
             "inclinaison": 30.0, "n_serie": 8},
        ],
        "postes": [
            {"nom": "Talon permanent", "kind": "talon", "actif": True,
             "params": copy.deepcopy(LOAD_KINDS["talon"]["defaults"])},
            {"nom": "Chauffage PAC Airton", "kind": "chauffage", "actif": True,
             "params": copy.deepcopy(LOAD_KINDS["chauffage"]["defaults"])},
            {"nom": "Eau chaude sanitaire", "kind": "ecs", "actif": True,
             "params": copy.deepcopy(LOAD_KINDS["ecs"]["defaults"])},
            {"nom": "Cuisson induction", "kind": "generique", "actif": True,
             "params": {"kwh_an": 900, "profil": "cuisine", "poids_mensuels": [1.0] * 12}},
            {"nom": "Electromenager et eclairage", "kind": "generique", "actif": True,
             "params": {"kwh_an": 2500, "profil": "occupation",
                        "poids_mensuels": [1.1, 1.1, 1.0, 1.0, .95, .9, .85, .85, .95, 1.0, 1.1, 1.15]}},
            {"nom": "Piscine 18x4 couverte", "kind": "piscine", "actif": True,
             "params": copy.deepcopy(LOAD_KINDS["piscine"]["defaults"])},
            {"nom": "Jacuzzi (a installer)", "kind": "spa", "actif": True,
             "params": copy.deepcopy(LOAD_KINDS["spa"]["defaults"])},
            {"nom": "Vehicule electrique (futur)", "kind": "vehicule", "actif": False,
             "params": copy.deepcopy(LOAD_KINDS["vehicule"]["defaults"])},
        ],
        "bom": copy.deepcopy(BOM_DEFAUT),
        "options": {"autonomie_cible": 0.92, "annee_affichee": "moyenne"},
    }


def save_config(cfg: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        user = json.load(f)
    cfg = default_config()
    for section in ("site", "module", "systeme", "economie", "options"):
        if section in user and isinstance(user[section], dict):
            cfg[section].update(user[section])
    for section in ("champs", "postes", "bom"):
        if section in user and isinstance(user[section], list):
            cfg[section] = user[section]
    # completion des champs manquants (configurations enregistrees avant
    # l'ajout des donnees electriques de grappe)
    for ch in cfg["champs"]:
        avait_serie = bool(ch.get("n_serie"))
        for k, v in CHAMP_DEFAUT.items():
            ch.setdefault(k, v)
        if not avait_serie:
            ch["n_serie"] = n_serie_suggere(
                ch.get("n_panneaux", 1), float(ch.get("voc_v", 49.5)),
                float(cfg["module"].get("beta_voc_pct_k", -0.27)),
                float(cfg["systeme"].get("vdc_max_v", 800.0)))
    # completion des parametres manquants dans les postes
    for p in cfg["postes"]:
        d = LOAD_KINDS.get(p.get("kind", "generique"), LOAD_KINDS["generique"])["defaults"]
        merged = copy.deepcopy(d)
        merged.update(p.get("params", {}))
        p["params"] = merged
    return cfg

"""
Configuration du simulateur : valeurs par defaut, schemas de parametres,
chargement / sauvegarde JSON.

Tout est en dictionnaires natifs JSON pour que l'UI puisse generer les
formulaires automatiquement a partir des schemas.
"""
from __future__ import annotations
import json, os, copy

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")


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


# --------------------------------------------------------------------------
# Schemas des postes de consommation
#   (cle, libelle, type, min, max, pas/choix, aide)
# --------------------------------------------------------------------------
LOAD_KINDS = {
    "talon": {
        "label": "Talon permanent",
        "help": "Puissance appelee 24h/24 : veilles, froid, VMC, box, hors-gel, circulateurs.",
        "params": [
            ("puissance_w", "Puissance permanente", "float", 0, 5000, 10, "W appeles en continu"),
            ("variation_hiver", "Majoration hivernale", "float", 0, 2, .05,
             "Fraction ajoutee en decembre-fevrier (hors-gel, eclairage)"),
        ],
        "defaults": {"puissance_w": 345, "variation_hiver": 0.15},
    },
    "generique": {
        "label": "Poste generique (kWh/an)",
        "help": "Tout usage decrit par une consommation annuelle, un profil horaire et des poids mensuels.",
        "params": [
            ("kwh_an", "Consommation annuelle", "float", 0, 50000, 50, "kWh/an"),
            ("profil", "Profil journalier", "choice", None, None, list(DAILY_SHAPES), ""),
            ("poids_mensuels", "Poids mensuels (12 valeurs)", "months", 0, 5, .05,
             "Ponderation relative de chaque mois"),
        ],
        "defaults": {"kwh_an": 1000, "profil": "occupation", "poids_mensuels": [1.0] * 12},
    },
    "chauffage": {
        "label": "Chauffage (PAC air/air)",
        "help": ("Besoin thermique reparti sur les degres-heures reels de la meteo, "
                 "converti en electricite par un COP dependant de la temperature exterieure. "
                 "Au-dela de la puissance de la PAC, l'appoint electrique direct prend le relais."),
        "params": [
            ("besoin_th_kwh_an", "Besoin thermique annuel", "float", 0, 80000, 100, "kWh thermiques/an"),
            ("t_base", "Temperature de non-chauffage", "float", 10, 22, .5,
             "Au-dessus, aucun besoin de chauffage"),
            ("t_consigne", "Temperature interieure", "float", 15, 25, .5, "Consigne moyenne"),
            ("cop_a", "COP : ordonnee a l'origine", "float", 0.5, 5, .05, "COP = a + b x Text"),
            ("cop_b", "COP : pente par degre", "float", 0, .3, .005, "Typiquement 0,09"),
            ("cop_max", "COP plafond", "float", 2, 7, .1, ""),
            ("p_pac_kw_th", "Puissance thermique PAC", "float", 0, 60, .5,
             "Capacite installee a 7 degres ; declassee automatiquement par temps froid"),
            ("declassement_froid", "Declassement a -7 degres", "float", 0, .6, .01,
             "Perte de capacite de la PAC a -7 degres"),
            ("part_bois", "Part couverte par le bois", "float", 0, 1, .05,
             "Fraction du besoin reprise par un insert quand il fait froid"),
            ("t_bois", "Seuil d'allumage du bois", "float", -10, 15, .5, "Temperature exterieure"),
            ("inertie_h", "Lissage d'inertie", "float", 0, 12, 1,
             "Heures de moyenne glissante sur la temperature exterieure"),
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
            ("besoin_th_kwh_an", "Besoin thermique annuel", "float", 0, 20000, 50, "kWh thermiques/an"),
            ("cop", "COP de production", "float", 0.8, 4, .05, "1,0 = resistance ; 2,8 = thermodynamique"),
            ("profil", "Profil journalier", "choice", None, None, list(DAILY_SHAPES), ""),
            ("majoration_hiver", "Majoration hivernale", "float", 0, 1, .05,
             "Eau froide plus froide en hiver, typiquement 0,20"),
        ],
        "defaults": {"besoin_th_kwh_an": 3400, "cop": 2.8, "profil": "solaire",
                     "majoration_hiver": 0.20},
    },
    "piscine": {
        "label": "Piscine (filtration)",
        "help": "Pompe de filtration, duree journaliere reglable mois par mois.",
        "params": [
            ("p_pompe_w", "Puissance pompe", "float", 0, 5000, 25, "W"),
            ("heures_par_mois", "Heures/jour par mois", "months", 0, 24, .5, ""),
            ("profil", "Profil journalier", "choice", None, None, list(DAILY_SHAPES), ""),
            ("pac_piscine_kwh_an", "PAC de chauffage piscine", "float", 0, 20000, 50,
             "kWh electriques/an, 0 si non chauffee"),
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
            ("ua_w_par_k", "Coefficient de pertes", "float", 0, 80, .5,
             "W/K : 10-14 bien isole, 20-30 spa gonflable"),
            ("t_consigne", "Temperature de consigne", "float", 20, 40, .5, "degres"),
            ("cop", "COP du rechauffeur", "float", 0.8, 6, .1, "1,0 = resistance, 4-5 = PAC de spa"),
            ("pompes_kwh_an", "Pompes et jets", "float", 0, 3000, 25, "kWh/an"),
            ("profil", "Profil de chauffe", "choice", None, None, list(DAILY_SHAPES), ""),
        ],
        "defaults": {"ua_w_par_k": 12.0, "t_consigne": 37.0, "cop": 1.0,
                     "pompes_kwh_an": 300, "profil": "constant"},
    },
    "vehicule": {
        "label": "Vehicule electrique",
        "help": "Recharge, pilotable en heures solaires ou nocturnes.",
        "params": [
            ("km_an", "Kilometrage annuel", "float", 0, 60000, 500, "km/an"),
            ("conso_kwh_100km", "Consommation", "float", 8, 35, .5, "kWh/100 km, pertes de charge incluses"),
            ("profil", "Profil de recharge", "choice", None, None, list(DAILY_SHAPES), ""),
            ("jours_par_semaine", "Jours de recharge/semaine", "float", 1, 7, 1, ""),
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
    ("n_onduleurs", "Nombre d'onduleurs", "int", 1, 12, 1, ""),
    ("p_nom_kw", "Puissance AC unitaire", "float", 1, 50, .5, "kW par onduleur"),
    ("pv_max_kwc_par_onduleur", "PV admissible unitaire", "float", 1, 40, .1,
     "kWc max par onduleur, sert au controle de coherence"),
    ("eff_pv_ac", "Rendement PV vers AC", "float", .85, 1, .001, "Conversion directe"),
    ("eff_pv_batt", "Rendement PV vers batterie", "float", .85, 1, .001, ""),
    ("eff_batt_ac", "Rendement batterie vers AC", "float", .85, 1, .001, ""),
    ("veille_w", "Consommation a vide unitaire", "float", 0, 300, 1,
     "W absorbes en permanence par onduleur (auto-consommation Deye)"),
    ("veille_nuit_w", "Consommation a vide de nuit", "float", 0, 300, 1,
     "W en mode nuit / veille profonde"),
    ("__grp2", "Batterie LFP", None, None, None, None, ""),
    ("batt_kwh_nominal", "Capacite nominale", "float", 0, 400, 1, "kWh"),
    ("dod", "Profondeur de decharge", "float", .5, 1, .01, "0,90 typique en LFP"),
    ("eff_charge", "Rendement de charge", "float", .85, 1, .005, ""),
    ("eff_decharge", "Rendement de decharge", "float", .85, 1, .005, ""),
    ("c_rate_charge", "Regime de charge max", "float", .1, 1, .05, "C"),
    ("c_rate_decharge", "Regime de decharge max", "float", .1, 1, .05, "C"),
    ("soc_initial", "Etat de charge initial", "float", 0, 1, .05, ""),
    ("__grp3", "Reseau", None, None, None, None, ""),
    ("p_souscrite_kva", "Puissance souscrite", "float", 3, 60, 3, "kVA"),
    ("injection_nulle", "Injection nulle (surplus ecrete)", "bool", None, None, None,
     "Coche = autoconsommation totale, aucune revente"),
    ("recharge_reseau_hc", "Recharge reseau en heures creuses", "bool", None, None, None,
     "Complete la batterie si l'etat de charge est trop bas"),
    ("soc_cible_hc", "Etat de charge vise en HC", "float", 0, 1, .05, ""),
    ("hc_debut", "Debut heures creuses", "int", 0, 23, 1, "h"),
    ("hc_fin", "Fin heures creuses", "int", 0, 23, 1, "h"),
]

SYSTEM_DEFAULTS = {
    "n_onduleurs": 2, "p_nom_kw": 12.0, "pv_max_kwc_par_onduleur": 15.6,
    "eff_pv_ac": 0.972, "eff_pv_batt": 0.975, "eff_batt_ac": 0.945,
    "veille_w": 55.0, "veille_nuit_w": 35.0,
    "batt_kwh_nominal": 64.3, "dod": 0.90, "eff_charge": 0.985, "eff_decharge": 0.985,
    "c_rate_charge": 0.5, "c_rate_decharge": 0.5, "soc_initial": 0.5,
    "p_souscrite_kva": 12.0, "injection_nulle": True,
    "recharge_reseau_hc": False, "soc_cible_hc": 0.4, "hc_debut": 2, "hc_fin": 6,
}

SITE_SCHEMA = [
    ("nom", "Nom du site", "text", None, None, None, ""),
    ("latitude", "Latitude", "float", -60, 70, .0001, "degres decimaux"),
    ("longitude", "Longitude", "float", -180, 180, .0001, "degres decimaux"),
    ("albedo", "Albedo du sol", "float", 0, .9, .01, "0,20 herbe, 0,15 sombre, 0,30 clair"),
    ("annee_debut", "Premiere annee meteo", "int", 2005, 2023, 1, ""),
    ("annee_fin", "Derniere annee meteo", "int", 2005, 2023, 1, ""),
    ("base_donnees", "Base PVGIS", "choice", None, None,
     ["PVGIS-SARAH3", "PVGIS-SARAH2", "PVGIS-ERA5"], ""),
]

SITE_DEFAULTS = {
    "nom": "Langoiran (Gironde)", "latitude": 44.71, "longitude": -0.39,
    "albedo": 0.20, "annee_debut": 2018, "annee_fin": 2023,
    "base_donnees": "PVGIS-SARAH3",
}

MODULE_SCHEMA = [
    ("gamma_pmax", "Coefficient de temperature", "float", -.006, 0, .0001, "par degre, typiquement -0,0035"),
    ("noct_u0", "Faiman U0", "float", 10, 40, .5, "25 = pose libre ventilee, 30 = toiture"),
    ("noct_u1", "Faiman U1", "float", 0, 12, .1, "6,8 pose libre"),
    ("iam_b0", "Coefficient IAM (ASHRAE)", "float", 0, .3, .005,
     "0,05 verre standard : penalise les fortes incidences"),
    ("pertes_dc_pct", "Pertes DC diverses", "float", 0, 30, .5,
     "Salissures, mismatch, cablage, LID, degradation"),
]

MODULE_DEFAULTS = {"gamma_pmax": -0.0035, "noct_u0": 25.0, "noct_u1": 6.84,
                   "iam_b0": 0.05, "pertes_dc_pct": 8.0}

ECO_SCHEMA = [
    ("prix_kwh_achat", "Prix du kWh soutire", "float", 0, 1.5, .001, "euros TTC"),
    ("abonnement_an", "Abonnement annuel", "float", 0, 3000, 10, "euros TTC/an"),
    ("prix_kwh_revente", "Prix du kWh injecte", "float", 0, .5, .001,
     "0 en full-DIY sans obligation d'achat"),
    ("facture_actuelle_an", "Facture actuelle (elec + gaz)", "float", 0, 30000, 50, "euros TTC/an"),
    ("cout_bois_stere", "Prix du stere de bois", "float", 0, 200, 5, "euros"),
    ("pci_bois_kwh_stere", "Contenu energetique du stere", "float", 800, 2500, 50,
     "kWh utiles restitues par stere"),
    ("inflation_energie", "Inflation energie annuelle", "float", 0, .15, .005, ""),
    ("duree_analyse_ans", "Horizon d'analyse", "int", 5, 40, 1, "ans"),
]

ECO_DEFAULTS = {"prix_kwh_achat": 0.2016, "abonnement_an": 280.0, "prix_kwh_revente": 0.0,
                "facture_actuelle_an": 6093.0, "cout_bois_stere": 90.0,
                "pci_bois_kwh_stere": 1500.0, "inflation_energie": 0.04,
                "duree_analyse_ans": 25}


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
            {"nom": "Champ hiver (sol, 60 degres)", "actif": True, "n_panneaux": 52,
             "wc_panneau": 500, "inclinaison": 60.0, "azimut": 180.0,
             "surface_m2_panneau": 2.2, "ombrage_pct": 0.0},
            {"nom": "Champ toiture (30 degres)", "actif": True, "n_panneaux": 8,
             "wc_panneau": 500, "inclinaison": 30.0, "azimut": 180.0,
             "surface_m2_panneau": 2.2, "ombrage_pct": 0.0},
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
    # completion des parametres manquants dans les postes
    for p in cfg["postes"]:
        d = LOAD_KINDS.get(p.get("kind", "generique"), LOAD_KINDS["generique"])["defaults"]
        merged = copy.deepcopy(d)
        merged.update(p.get("params", {}))
        p["params"] = merged
    return cfg

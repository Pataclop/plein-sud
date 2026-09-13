"""
Moteur de simulation : production des champs, gestion onduleur / batterie
au pas horaire, agregations jour et mois, bilan economique.
"""
from __future__ import annotations
import math
import re
import numpy as np

from .config import BOM_CATEGORIES, ORDRE_CATEGORIES, est_solaire
from .solar import field_dc_power
from .loads import build_load

MOIS = ["Jan", "Fev", "Mar", "Avr", "Mai", "Juin",
        "Juil", "Aout", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------------------
# Production
# --------------------------------------------------------------------------
def compute_production(cfg: dict, meteo: dict):
    module = cfg["module"]
    albedo = float(cfg["site"].get("albedo", 0.20))
    total = np.zeros(meteo["n"])
    par_champ, diags = {}, {}
    for i, ch in enumerate(cfg["champs"]):
        if not ch.get("actif", True):
            continue
        kwc = float(ch["n_panneaux"]) * float(ch["wc_panneau"]) / 1000.0
        if kwc <= 0:
            continue
        p, poa, d = field_dc_power(meteo, kwc, float(ch["inclinaison"]),
                                   float(ch.get("azimut", 180.0)), module,
                                   albedo, float(ch.get("ombrage_pct", 0.0)))
        # Rien n'impose que deux groupes portent des noms differents, et
        # l'interface en fabrique naturellement des doublons. Sans ce
        # desambiguisation, le detail par groupe perdait tout sauf le dernier.
        nom = ch["nom"]
        if nom in par_champ:
            nom = f"{nom} ({i + 1})"
        par_champ[nom] = p
        d["kwc"] = kwc
        d["production_kwh_an"] = float(p.sum() / meteo["n_years"])
        diags[nom] = d
        total += p
    return total, par_champ, diags


#: energie d'un pack 16S de cellules LFP 314 Ah : 16 x 3,2 V x 314 Ah
KWH_PAR_PACK_16S = 16.07


def total_kwc(cfg: dict) -> float:
    return sum(float(c["n_panneaux"]) * float(c["wc_panneau"]) / 1000.0
               for c in cfg["champs"] if c.get("actif", True))


def total_panneaux(cfg: dict) -> int:
    return int(sum(c["n_panneaux"] for c in cfg["champs"] if c.get("actif", True)))


def surface_m2(cfg: dict) -> float:
    return float(sum(c["n_panneaux"] * c.get("surface_m2_panneau", 2.2)
                     for c in cfg["champs"] if c.get("actif", True)))


# --------------------------------------------------------------------------
# Cablage : tensions et courants de grappe (string)
# --------------------------------------------------------------------------
T_FROID_C = -10.0     # temperature de dimensionnement de la tension a vide
T_STC_C = 25.0        # temperature des conditions standard


def string_diag(cfg: dict, champ: dict) -> dict:
    """Tensions et courants d'un groupe de panneaux.

    En serie les tensions s'additionnent, en parallele les courants. La
    tension a vide monte quand il fait froid : c'est elle qui doit rester
    sous la tension DC maximale de l'onduleur.
    """
    n_pan = max(int(champ.get("n_panneaux", 0)), 0)
    # On ne peut pas cabler en serie plus de panneaux qu'il n'y en a : sans
    # cette borne, 4 panneaux declares "par 13" affichaient une tension de
    # grappe de 704 V, coloree en vert parce qu'elle passait sous les 800 V
    # de l'onduleur, pour une grappe qui n'existe pas.
    n_ser = max(int(champ.get("n_serie", 0) or 0), 1)
    if n_pan:
        n_ser = min(n_ser, n_pan)
    voc = float(champ.get("voc_v", 0.0))
    isc = float(champ.get("isc_a", 0.0))
    beta = float(cfg["module"].get("beta_voc_pct_k", -0.27)) / 100.0

    n_grappes = n_pan / n_ser if n_ser else 0.0
    voc_stc = voc * n_ser
    voc_froid = voc_stc * (1.0 + beta * (T_FROID_C - T_STC_C))
    return {
        "n_panneaux": n_pan,
        "n_serie": n_ser,
        "n_grappes": n_grappes,
        "n_grappes_entier": int(n_grappes),
        "grappe_incomplete": abs(n_grappes - round(n_grappes)) > 1e-9,
        "voc_stc": voc_stc,
        "voc_froid": voc_froid,
        "isc_total": isc * n_grappes,
        "isc_grappe": isc,
        "kwc": n_pan * float(champ.get("wc_panneau", 0.0)) / 1000.0,
    }


def total_grappes(cfg: dict) -> float:
    return sum(string_diag(cfg, c)["n_grappes"]
               for c in cfg["champs"] if c.get("actif", True))


def check_cablage(cfg: dict) -> list:
    """Alertes de cablage DC, calculables sans simulation."""
    a = []
    s = cfg["systeme"]
    vmax = float(s.get("vdc_max_v", 800.0))
    vmin = float(s.get("vmppt_min_v", 160.0))
    imax = float(s.get("i_max_string_a", 26.0))
    n_mppt = max(int(s.get("n_mppt_par_onduleur", 2)) * int(s["n_onduleurs"]), 1)

    for ch in cfg["champs"]:
        if not ch.get("actif", True):
            continue
        d = string_diag(cfg, ch)
        nom = ch["nom"]
        if d["voc_froid"] > vmax:
            a.append(("erreur",
                      f"{nom} : {d['voc_froid']:.0f} V a vide par -10 C pour une "
                      f"limite onduleur de {vmax:.0f} V. Destruction immediate au "
                      f"premier matin de gel : reduisez a "
                      f"{int(vmax / max(d['voc_froid'] / d['n_serie'], 1e-9))} panneaux "
                      f"en serie au maximum."))
        elif d["voc_froid"] > 0.95 * vmax:
            a.append(("attention",
                      f"{nom} : {d['voc_froid']:.0f} V a vide par -10 C, soit moins de "
                      f"5 % de marge sous les {vmax:.0f} V de l'onduleur. Une vague de "
                      f"froid plus severe suffit a depasser la limite."))
        if 0 < d["voc_stc"] < vmin * 1.25:
            a.append(("attention",
                      f"{nom} : {d['voc_stc']:.0f} V a vide seulement, pour un MPPT qui "
                      f"demarre a {vmin:.0f} V. La grappe ne produira rien le matin, le "
                      f"soir ni par temps couvert : allongez-la."))
        if d["grappe_incomplete"]:
            a.append(("attention",
                      f"{nom} : {d['n_panneaux']} panneaux ne se divisent pas en grappes "
                      f"de {d['n_serie']}. La derniere grappe serait incomplete et "
                      f"produirait une tension differente des autres."))
        if d["isc_grappe"] > imax:
            a.append(("erreur",
                      f"{nom} : {d['isc_grappe']:.1f} A par grappe pour une entree MPPT "
                      f"limitee a {imax:.1f} A. Le courant sera bride et la production "
                      f"perdue."))

    n_g = total_grappes(cfg)
    if n_g > n_mppt * 2:
        a.append(("info",
                  f"{n_g:.0f} grappes pour {n_mppt} entrees MPPT disponibles : il faudra "
                  f"en mettre plus de deux en parallele par entree, via un coffret de "
                  f"raccordement avec fusibles."))
    return a


# --------------------------------------------------------------------------
# Dispatch horaire onduleur + batterie
# --------------------------------------------------------------------------
def dispatch(pv_dc: np.ndarray, load_ac: np.ndarray, meteo: dict, sys: dict):
    n = len(pv_dc)
    n_ond = int(sys["n_onduleurs"])
    p_ac_max = n_ond * float(sys["p_nom_kw"])

    e_pv_ac = float(sys["eff_pv_ac"])
    e_pv_bt = float(sys["eff_pv_batt"])
    e_bt_ac = float(sys["eff_batt_ac"])
    e_chg = float(sys["eff_charge"])
    e_dis = float(sys["eff_decharge"])

    cap = float(sys["batt_kwh_nominal"])
    dod = float(sys["dod"])
    soc_min = cap * (1.0 - dod) / 2.0
    soc_max = cap - soc_min
    utile = max(soc_max - soc_min, 0.0)

    p_chg_max = min(cap * float(sys["c_rate_charge"]), p_ac_max)
    p_dis_max = min(cap * float(sys["c_rate_decharge"]), p_ac_max)

    veille_j = n_ond * float(sys["veille_w"]) / 1000.0
    veille_n = n_ond * float(sys["veille_nuit_w"]) / 1000.0
    veille = np.where(pv_dc > 0.05, veille_j, veille_n)

    grid_max = float(sys["p_souscrite_kva"])
    zero_inj = bool(sys["injection_nulle"])
    hc = bool(sys.get("recharge_reseau_hc", False))
    hc0, hc1 = int(sys.get("hc_debut", 2)), int(sys.get("hc_fin", 6))
    soc_hc = float(sys.get("soc_cible_hc", 0.4)) * utile + soc_min
    hours = meteo["hour"]

    soc = np.empty(n)
    imp = np.zeros(n)            # achat total au reseau (c'est la facture)
    imp_charges = np.zeros(n)    # dont : reseau qui alimente directement la maison
    imp_batterie = np.zeros(n)   # dont : reseau qui remplit la batterie en HC
    exp = np.zeros(n)
    ecrete_ac = np.zeros(n)      # bride par la puissance nominale onduleur
    charge = np.zeros(n)
    decharge = np.zeros(n)
    decharge_reseau = np.zeros(n)   # part de la decharge d'origine RESEAU
    non_servi = np.zeros(n)      # depassement de la puissance souscrite

    s = soc_min + float(sys.get("soc_initial", 0.5)) * utile
    # Energie d'origine reseau presente dans la batterie, aux bornes. Sans
    # ce suivi, l'energie achetee en heures creuses etait comptee une fois a
    # l'achat puis rendue "autonome" a la restitution : l'autonomie affichee
    # pouvait devenir negative.
    s_reseau = 0.0
    besoin = load_ac + veille

    for i in range(n):
        pv = pv_dc[i]
        b = besoin[i]

        # 1. PV vers charges, dans la limite de la puissance AC des onduleurs
        pv_vers_ac = min(pv * e_pv_ac, b, p_ac_max)
        pv_utilise_dc = pv_vers_ac / e_pv_ac
        reste_dc = pv - pv_utilise_dc
        deficit = b - pv_vers_ac

        if reste_dc > 1e-9:
            # 2. Surplus vers batterie (voie DC, meilleur rendement)
            place = max(soc_max - s, 0.0) / max(e_chg * e_pv_bt, 1e-6)
            c = min(reste_dc, p_chg_max / max(e_pv_bt, 1e-6), place)
            s += c * e_pv_bt * e_chg
            charge[i] = c * e_pv_bt * e_chg
            reste_dc -= c
            if reste_dc > 1e-9:
                dispo_ac = max(p_ac_max - pv_vers_ac, 0.0)
                e = min(reste_dc * e_pv_ac, dispo_ac)
                if zero_inj:
                    ecrete_ac[i] += reste_dc * e_pv_ac
                else:
                    exp[i] = e
                    ecrete_ac[i] += reste_dc * e_pv_ac - e

        if deficit > 1e-9:
            # 3. Batterie vers charges, dans la puissance restante de l'onduleur
            dispo = max(s - soc_min, 0.0) * e_dis * e_bt_ac
            marge_ac = max(p_ac_max - pv_vers_ac, 0.0)
            d = min(deficit, p_dis_max, dispo, marge_ac)
            retire = d / max(e_dis * e_bt_ac, 1e-6)
            part_reseau = s_reseau / max(s - soc_min, 1e-9) if s > soc_min else 0.0
            part_reseau = min(max(part_reseau, 0.0), 1.0)
            s -= retire
            s_reseau = max(s_reseau - retire * part_reseau, 0.0)
            decharge[i] = d
            decharge_reseau[i] = d * part_reseau
            deficit -= d
            if deficit > 1e-9:
                g = min(deficit, grid_max)
                imp[i] = g
                imp_charges[i] = g
                non_servi[i] = deficit - g

        # 4. Recharge reseau optionnelle en heures creuses
        if hc and s < soc_hc:
            in_hc = (hc0 <= hc1 and hc0 <= hours[i] < hc1) or \
                    (hc0 > hc1 and (hours[i] >= hc0 or hours[i] < hc1))
            if in_hc:
                marge = max(grid_max - imp[i], 0.0)
                place = (soc_hc - s) / max(e_chg, 1e-6)
                g = min(marge, p_chg_max, place)
                s += g * e_chg
                s_reseau += g * e_chg
                imp[i] += g
                imp_batterie[i] += g
                charge[i] += g * e_chg

        soc[i] = s

    return {
        "soc": soc, "soc_min": soc_min, "soc_max": soc_max, "utile": utile,
        "import": imp, "export": exp, "ecrete": ecrete_ac,
        "import_charges": imp_charges, "import_batterie": imp_batterie,
        "decharge_reseau": decharge_reseau,
        # Besoin reellement couvert par de l'electricite achetee : le reseau
        # qui alimente directement la maison, plus la part d'origine reseau
        # de ce que la batterie restitue.
        "import_effectif": imp_charges + decharge_reseau,
        "charge": charge, "decharge": decharge, "non_servi": non_servi,
        "veille": veille, "besoin_total": besoin,
    }


# --------------------------------------------------------------------------
# Agregations
# --------------------------------------------------------------------------
def _mois(arr, meteo):
    ny = meteo["n_years"]
    return np.array([arr[meteo["month"] == k + 1].sum() / ny for k in range(12)])


def _jour(arr, meteo):
    return np.bincount(meteo["day_index"], weights=arr, minlength=meteo["n_days"])


def sys_eff_decharge(cfg: dict) -> float:
    """Rendement total entre les bornes de la batterie et la maison."""
    s = cfg["systeme"]
    return float(s.get("eff_decharge", 1.0)) * float(s.get("eff_batt_ac", 1.0))


def duree_vie_batterie_ans(cycles_an: float, cycles_total: float = 6000.0):
    """Duree de vie en annees, ou None si la batterie ne cycle pas.

    Retourner 6 000 ans quand le nombre de cycles tombe sous 1 n'informe
    personne : mieux vaut ne rien afficher.
    """
    if cycles_an is None or cycles_an < 0.05:
        return None
    return cycles_total / cycles_an


def simulate(cfg: dict, meteo: dict) -> dict:
    pv_dc, par_champ, diag_champs = compute_production(cfg, meteo)
    load, detail, infos = build_load(cfg, meteo)
    d = dispatch(pv_dc, load, meteo, cfg["systeme"])

    ny = meteo["n_years"]
    conso = load                                  # consommation des usages
    besoin = d["besoin_total"]                    # usages + veille onduleurs
    imp = d["import"]                  # ce qui est achete, donc facture
    imp_eff = d["import_effectif"]     # ce qui, dans le besoin, vient du reseau
    autoconso = besoin - imp_eff

    m = {
        "production_dc": _mois(pv_dc, meteo),
        "consommation": _mois(conso, meteo),
        "veille": _mois(d["veille"], meteo),
        "besoin": _mois(besoin, meteo),
        "import": _mois(imp, meteo),
        "import_effectif": _mois(imp_eff, meteo),
        "import_batterie": _mois(d["import_batterie"], meteo),
        "autoconso": _mois(autoconso, meteo),
        "export": _mois(d["export"], meteo),
        "ecrete": _mois(d["ecrete"], meteo),
        "charge": _mois(d["charge"], meteo),
        "decharge": _mois(d["decharge"], meteo),
        "non_servi": _mois(d["non_servi"], meteo),
    }
    m["autonomie"] = 1.0 - m["import_effectif"] / np.maximum(m["besoin"], 1e-9)
    njours = np.array([np.unique(meteo["day_index"][meteo["month"] == k + 1]).size / ny
                       for k in range(12)])
    m["jours"] = njours
    m["conso_jour"] = m["consommation"] / np.maximum(njours, 1e-9)
    m["prod_jour"] = m["production_dc"] / np.maximum(njours, 1e-9)

    jour = {
        "production": _jour(pv_dc, meteo),
        "consommation": _jour(conso, meteo),
        "besoin": _jour(besoin, meteo),
        "import": _jour(imp, meteo),
        "import_effectif": _jour(imp_eff, meteo),
        "date": meteo["days"],
        "mois": meteo["month"][np.unique(meteo["day_index"], return_index=True)[1]],
        "soc_min": np.minimum.reduceat(
            d["soc"], np.unique(meteo["day_index"], return_index=True)[1]),
    }
    jour["autonomie"] = 1.0 - jour["import_effectif"] / np.maximum(jour["besoin"], 1e-9)

    kwc = total_kwc(cfg)
    conso_an = conso.sum() / ny
    besoin_an = besoin.sum() / ny
    imp_an = imp.sum() / ny
    imp_eff_an = imp_eff.sum() / ny
    prod_an = float(pv_dc.sum() / ny)

    res = {
        "meteo": meteo, "mensuel": m, "journalier": jour,
        "pv_dc": pv_dc, "load": load, "detail_postes": detail,
        "dispatch": d, "diag_champs": diag_champs, "par_champ": par_champ,
        "infos_postes": infos,
        "kpi": {
            "kwc": kwc,
            "panneaux": total_panneaux(cfg),
            "surface_m2": surface_m2(cfg),
            "production_an": float(pv_dc.sum() / ny),
            "productible_kwh_kwc": float(pv_dc.sum() / ny / max(kwc, 1e-9)),
            "consommation_an": float(conso_an),
            "veille_an": float(d["veille"].sum() / ny),
            "besoin_an": float(besoin_an),
            "import_an": float(imp_an),
            "import_effectif_an": float(imp_eff_an),
            "import_batterie_an": float(d["import_batterie"].sum() / ny),
            "export_an": float(d["export"].sum() / ny),
            "ecrete_an": float(d["ecrete"].sum() / ny),
            "non_servi_an": float(d["non_servi"].sum() / ny),
            "autonomie": float(1.0 - imp_eff_an / max(besoin_an, 1e-9)),
            "taux_autoconso": (float((besoin_an - imp_eff_an) / prod_an)
                               if prod_an > 1e-6 else 0.0),
            # Cycles comptes AUX BORNES de la batterie : decharge[] est une
            # energie deja convertie en alternatif, il faut la remonter par
            # les rendements pour la comparer a une capacite.
            "cycles_batterie_an": (
                float(d["decharge"].sum() / ny
                      / max(float(sys_eff_decharge(cfg)), 1e-6)
                      / d["utile"]) if d["utile"] > 1e-9 else 0.0),
            "conso_jour_moy": float(conso_an / 365.25),
            "conso_jour_max": float(jour["consommation"].max()),
            "jours_sans_import": int((jour["import"] < 0.5).sum() / ny),
            "jours_import_sup_10": int((jour["import"] > 10).sum() / ny),
            "pv_par_onduleur_kwc": kwc / max(int(cfg["systeme"]["n_onduleurs"]), 1),
            "n_years": ny,
        },
    }
    res["eco"] = economics(cfg, res)
    res["alertes"] = check_config(cfg, res)
    return res


# --------------------------------------------------------------------------
# Economie
# --------------------------------------------------------------------------
def bom_quantities(cfg: dict) -> dict:
    """Quantites deduites de la configuration, pour les lignes de devis
    indexees sur le dimensionnement plutot que saisies a la main."""
    kwc = total_kwc(cfg)
    batt = float(cfg["systeme"]["batt_kwh_nominal"])
    # Arrondi au SUPERIEUR : on n'achete pas un demi-pack, et l'arrondi au
    # plus proche rendait une batterie de 8 kWh gratuite dans le devis
    # (0 pack, 0 cellule, 0 BMS). La tolerance de 2 % absorbe le fait qu'une
    # capacite se saisit arrondie : 64,3 kWh, ce sont bien 4 packs de 16,07
    # et non 5.
    packs = int(math.ceil(batt / KWH_PAR_PACK_16S - 0.02)) if batt > 0 else 0
    packs = max(packs, 1 if batt > 0 else 0)
    return {
        "fixe": 1.0,
        "panneaux": float(total_panneaux(cfg)),
        "kwc": kwc,
        "onduleurs": float(cfg["systeme"]["n_onduleurs"]),
        "batt_kwh": batt,
        "packs": float(packs),
        "cellules": float(packs * 16),
        "m2_panneaux": surface_m2(cfg),
        "grappes": float(round(total_grappes(cfg))),
    }


def compute_bom(cfg: dict):
    """Chiffre la nomenclature et la ventile par categorie.

    Retourne (lignes, recap). Chaque ligne recoit sa quantite retenue, son
    montant, sa categorie normalisee et un drapeau "solaire".

    recap sepate deux perimetres, et c'est tout l'interet de la ventilation :

      * "solaire"      : panneaux, structure, onduleurs, batterie, cablage,
                         protections, pose, demarches, outillage. C'est le
                         seul chiffre qui a un sens pour juger un
                         dimensionnement ou comparer deux options.
      * "hors_solaire" : chauffe-eau thermodynamique, insert, isolation.
                         Ces postes changent la consommation simulee, donc
                         le resultat, mais leur prix ne doit jamais peser
                         sur l'arbitrage panneaux / onduleur / batterie.
    """
    q = bom_quantities(cfg)
    lignes = []
    par_cat = {c: 0.0 for c in ORDRE_CATEGORIES}
    total = solaire = 0.0
    for l in cfg.get("bom", []):
        auto = l.get("auto", "fixe")
        inconnue = auto not in q
        if inconnue:
            auto = "fixe"           # regle disparue : on retombe sur la saisie
        # Une quantite a zero est un choix : garder au devis une option non
        # retenue (differentiel type B, second onduleur en reserve) sans
        # qu'elle soit facturee. L'ancienne regle la forcait a 1.
        qte = float(l.get("qte", 0)) if auto == "fixe" else q.get(auto, 0.0)
        montant = qte * float(l.get("pu", 0.0))
        cat = l.get("categorie")
        if cat not in BOM_CATEGORIES:
            cat = "divers"
        sol = est_solaire(cat)
        lignes.append({**l, "auto": auto, "categorie": cat, "solaire": sol,
                       "auto_inconnue": inconnue,
                       "qte_calc": qte, "montant": montant})
        par_cat[cat] = par_cat.get(cat, 0.0) + montant
        total += montant
        if sol:
            solaire += montant
    recap = {
        "total": total,
        "solaire": solaire,
        "hors_solaire": total - solaire,
        "par_categorie": par_cat,
    }
    return lignes, recap


def cout_categorie(recap: dict, *categories) -> float:
    """Somme du devis sur une ou plusieurs categories.

    Accepte indifferemment le recap de compute_bom() (cle "par_categorie")
    et le dictionnaire economique de simulate() (cle "capex_par_categorie") :
    les deux circulent dans le code et se ressemblent assez pour qu'on les
    confonde, et renvoyer 0 en silence serait le pire des comportements.
    """
    pc = recap.get("par_categorie")
    if pc is None:
        pc = recap.get("capex_par_categorie")
    if pc is None:
        raise KeyError("dictionnaire sans ventilation par categorie : "
                       "attendu 'par_categorie' ou 'capex_par_categorie'")
    return float(sum(pc.get(c, 0.0) for c in categories))


def _retour(capex: float, gain_an: float, infl: float, horizon: int):
    """Temps de retour en annees, inflation de l'energie comprise.

    Retourne (retour_ans_ou_None, cumul_a_l_horizon). L'annee de bascule est
    interpolee : un retour de 6,0 annees ne veut pas dire "la 7e annee".
    """
    cum = -float(capex)
    retour = 0.0 if cum >= 0 else None
    for an in range(1, int(horizon) + 1):
        gain = gain_an * (1.0 + infl) ** (an - 1)
        avant = cum
        cum += gain
        if retour is None and cum >= 0:
            retour = (an - 1) + (-avant / gain if gain > 1e-9 else 0.0)
    return retour, cum


def economics(cfg: dict, res: dict) -> dict:
    """Bilan economique a DEUX PERIMETRES.

    1. L'INSTALLATION SOLAIRE SEULE : capex_solaire face a l'energie que les
       panneaux et la batterie evitent d'acheter, a maison inchangee. C'est
       ce couple qui doit servir a arbitrer un panneau de plus, un pack de
       batterie de plus, une inclinaison ou un onduleur.

    2. LE PROJET COMPLET : capex_total (solaire + chauffe-eau + insert +
       isolation) face a la facture d'energie declaree avant travaux.

    Melanger les deux, comme le faisait la version precedente, donne un temps
    de retour faux dans les deux sens : le prix d'une isolation de combles
    alourdissait le retour de la batterie, et l'economie de chauffage
    l'allegeait.
    """
    e = cfg["economie"]
    k = res["kpi"]
    lignes_bom, recap = compute_bom(cfg)
    capex_solaire = recap["solaire"]
    capex_total = recap["total"]

    prix = float(e["prix_kwh_achat"])
    abo = float(e["abonnement_an"])

    bois_kwh = 0.0
    for nom, info in res["infos_postes"].items():
        bois_kwh += info.get("th_bois_kwh_an", 0.0)
    steres = bois_kwh / max(float(e.get("pci_bois_kwh_stere", 1500.0)), 1.0)
    cout_bois = steres * float(e.get("cout_bois_stere", 0.0))

    achat = k["import_an"] * prix
    revente = k["export_an"] * float(e.get("prix_kwh_revente", 0.0))
    cout_avec = achat + abo + cout_bois - revente

    # Contrefactuel du perimetre solaire : la MEME maison, les memes
    # equipements, le meme bois, mais ni panneaux ni batterie. La veille des
    # onduleurs n'existe alors pas : on part de la consommation des usages,
    # pas du besoin total.
    cout_sans = k["consommation_an"] * prix + abo + cout_bois
    facture_actuelle = float(e.get("facture_actuelle_an", 0.0))

    econ_solaire = cout_sans - cout_avec
    econ_projet = facture_actuelle - cout_avec

    infl = float(e.get("inflation_energie", 0.0))
    horizon = int(e.get("duree_analyse_ans", 25))
    retour_solaire, cum_solaire = _retour(capex_solaire, econ_solaire, infl, horizon)
    retour_projet, cum_projet = _retour(capex_total, econ_projet, infl, horizon)

    # kWh que l'installation a evite d'acheter : la consommation des usages
    # moins ce qui a quand meme ete pris au reseau pour les couvrir.
    utile = max(k["consommation_an"] - k["import_effectif_an"], 0.0)
    lcoe = capex_solaire / (utile * horizon) if utile * horizon > 1.0 else None
    batt = float(cfg["systeme"]["batt_kwh_nominal"])

    return {
        # --- perimetres ---
        "capex_solaire": capex_solaire,
        "capex_hors_solaire": recap["hors_solaire"],
        "capex_total": capex_total,
        "capex_par_categorie": dict(recap["par_categorie"]),
        # "capex" reste l'investissement de reference des arbitrages, donc
        # le perimetre solaire : c'est lui que lisent les balayages.
        "capex": capex_solaire,
        # --- ratios du perimetre solaire ---
        "cout_par_wc": capex_solaire / max(k["kwc"] * 1000.0, 1e-9),
        "cout_par_kwh_batterie": cout_categorie(recap, "stockage") / max(batt, 1e-9),
        "cout_pv_par_wc": cout_categorie(recap, "pv", "conversion") /
                          max(k["kwc"] * 1000.0, 1e-9),
        # None quand l'installation ne couvre aucun besoin : afficher
        # 2,5e13 EUR/kWh n'aide personne.
        "lcoe_kwh_utile": lcoe,
        # --- flux annuels ---
        "cout_annuel_avec_pv": cout_avec,
        "cout_annuel_sans_pv": cout_sans,
        "facture_actuelle": facture_actuelle,
        "economie_vs_sans_pv": econ_solaire,
        "economie_vs_actuel": econ_projet,
        "cout_bois": cout_bois, "steres": steres,
        "kwh_evites_an": utile,
        # --- retours ---
        "retour_ans_vs_sans_pv": retour_solaire,
        "retour_ans_vs_actuel": retour_projet,
        "gain_cumule_solaire": cum_solaire,
        "gain_cumule_projet": cum_projet,
        "gain_cumule_horizon": cum_projet,
        # --- pertes ---
        "kwh_perdus_an": k["ecrete_an"],
        "valeur_perdue_an": k["ecrete_an"] * prix,
    }


# --------------------------------------------------------------------------
# Controles de coherence
# --------------------------------------------------------------------------
def check_config(cfg: dict, res: dict) -> list:
    a = check_cablage(cfg)
    k, s = res["kpi"], cfg["systeme"]
    pv_par_ond = k["pv_par_onduleur_kwc"]
    lim = float(s.get("pv_max_kwc_par_onduleur", 15.6))
    if pv_par_ond > lim:
        a.append(("erreur", f"{pv_par_ond:.1f} kWc par onduleur pour une limite "
                            f"constructeur de {lim:.1f} kWc. Ajoutez un onduleur "
                            f"ou reduisez le champ."))
    elif pv_par_ond > lim * 0.95:
        a.append(("info", f"{pv_par_ond:.1f} kWc par onduleur : vous etes a la limite "
                          f"de {lim:.1f} kWc."))

    p_prod = int(s["n_onduleurs"]) * float(s["p_nom_kw"])
    if p_prod > 36:
        a.append(("attention", f"Puissance de production declaree {p_prod:.0f} kVA : "
                               f"au-dela de 36 kVA le raccordement Enedis change de "
                               f"regime. Bridez la sortie des onduleurs."))
    if k["ecrete_an"] > 0.35 * k["production_an"]:
        a.append(("attention", f"{k['ecrete_an']:.0f} kWh/an ecretes, soit "
                               f"{100 * k['ecrete_an'] / max(k['production_an'], 1):.0f} % "
                               f"de la production. Envisagez un usage du surplus "
                               f"ou une reduction du champ."))
    if k["non_servi_an"] > 1:
        a.append(("erreur", f"{k['non_servi_an']:.0f} kWh/an non fournis : la puissance "
                            f"souscrite de {s['p_souscrite_kva']:.0f} kVA est insuffisante "
                            f"aux moments de pointe."))
    cyc = k["cycles_batterie_an"]
    vie = duree_vie_batterie_ans(cyc)
    if cyc > 320 and vie:
        a.append(("info", f"{cyc:.0f} cycles pleins par an : duree de vie estimee "
                          f"{vie:.0f} ans."))
    for nom, info in res["infos_postes"].items():
        if info.get("heures_saturation_pac", 0) > 50:
            a.append(("attention",
                      f"{nom} : {info['heures_saturation_pac']:.0f} h/an ou la PAC sature, "
                      f"{info.get('th_appoint_kwh_an', 0):.0f} kWh repris par l'appoint "
                      f"electrique direct."))
        if info.get("type_inconnu"):
            a.append(("erreur",
                      f"{nom} : type de poste \"{info['type_inconnu']}\" inconnu. "
                      f"Il est affiche dans la liste mais ne consomme rien dans "
                      f"la simulation. Choisissez un type valide ou supprimez-le."))
        h_deb = info.get("heures_debordement_filtration", 0)
        if h_deb > 200:
            a.append(("info",
                      f"{nom} : {h_deb:.0f} h/an ou la filtration deborde du profil "
                      f"horaire choisi. La pompe ne peut pas debiter plus que sa "
                      f"puissance nominale : elle tourne donc en dehors des heures "
                      f"voulues, souvent hors soleil. Elargissez le profil ou "
                      f"reduisez la duree de filtration."))
    a.extend(check_nomenclature(cfg, res))
    return a


def check_nomenclature(cfg: dict, res: dict) -> list:
    """Ce que la separation des couts peut silencieusement mal classer.

    Une ligne mal rangee deplace de l'argent d'un perimetre a l'autre sans
    rien signaler : c'est exactement ce que la separation cherche a eviter.
    """
    a = []
    lignes, recap = compute_bom(cfg)
    e = res["eco"]

    devinees = [l["poste"] for l in lignes if l["categorie"] == "divers"]
    if devinees:
        a.append(("attention",
                  f"{len(devinees)} ligne(s) de devis n'ont pas pu etre classees "
                  f"automatiquement et sont rangees dans \"Divers installation "
                  f"solaire\", donc COMPTEES dans le perimetre solaire : "
                  + ", ".join(f"\"{n}\"" for n in devinees[:4])
                  + ("..." if len(devinees) > 4 else "")
                  + ". Verifiez leur categorie dans l'onglet 5 : si elles "
                    "n'appartiennent pas a l'installation solaire, elles "
                    "degradent le EUR/Wc et le temps de retour affiches."))

    inconnues = [l["poste"] for l in lignes if l.get("auto_inconnue")]
    if inconnues:
        a.append(("attention",
                  f"Regle de quantite inconnue sur : "
                  + ", ".join(f"\"{n}\"" for n in inconnues[:4])
                  + ". Ces lignes sont retombees sur la quantite saisie a la "
                    "main. Revoyez la colonne \"Quantite auto\"."))

    if recap["solaire"] <= 0:
        a.append(("erreur",
                  "Aucune ligne du devis n'est rangee dans le perimetre solaire : "
                  "le cout au watt-crete, le cout du kWh et le temps de retour de "
                  "l'installation sont sans objet. Classez au moins les modules, "
                  "l'onduleur et la batterie."))
    else:
        eur_wc = e["cout_par_wc"]
        if eur_wc > 0 and eur_wc < 0.35:
            a.append(("attention",
                      f"{eur_wc:.2f} EUR/Wc : c'est tres bas meme en achat direct "
                      f"(compter 0,55 a 0,95 EUR/Wc avec stockage). Une ligne "
                      f"oubliee, une quantite a zero ou une categorie basculee "
                      f"hors perimetre solaire ?"))
        elif eur_wc > 2.5:
            a.append(("info",
                      f"{eur_wc:.2f} EUR/Wc sur le perimetre solaire : c'est le "
                      f"prix d'une installation posee par un professionnel "
                      f"(1,80 a 2,50 EUR/Wc). Verifiez qu'aucun poste hors "
                      f"solaire n'a ete range dans le perimetre."))

    batt = float(cfg["systeme"]["batt_kwh_nominal"])
    if batt > 0 and cout_categorie(recap, "stockage") <= 0:
        a.append(("attention",
                  f"{batt:.0f} kWh de batterie declares mais aucune ligne de devis "
                  f"dans la categorie \"Batterie et BMS\" : le cout du stockage "
                  f"est absent de tous les arbitrages."))
    if total_kwc(cfg) > 0 and cout_categorie(recap, "pv") <= 0:
        a.append(("attention",
                  "Des panneaux sont declares mais aucune ligne de devis dans la "
                  "categorie \"Modules et structure\"."))
    return a


# --------------------------------------------------------------------------
# Optimisation des orientations, groupe par groupe
#
#   Les groupes ne sont PAS independants : ce qui compte n'est pas la
#   production de chacun mais la facon dont leur somme se superpose a la
#   consommation, heure par heure, a travers la batterie. Optimiser chaque
#   groupe isolement donnerait la meme reponse pour tous (l'optimum annuel).
#   On fait donc une descente par coordonnees : on balaye la grille complete
#   d'un groupe, les autres etant figes, on garde le meilleur, puis on passe
#   au groupe suivant et on recommence. Le deuxieme groupe "voit" alors que
#   midi est deja couvert et part naturellement vers le matin ou le soir.
# --------------------------------------------------------------------------
#   cle interne -> (libelle, sens, unite, facteur d'affichage, decimales)
#   sens = +1 : on maximise ; -1 : on minimise
ORIENT_OBJECTIFS = {
    "autonomie": ("Autonomie annuelle", +1, "%", 100.0, 2),
    "autonomie_hiver": ("Autonomie de novembre a fevrier", +1, "%", 100.0, 2),
    "import": ("Energie soutiree au reseau", -1, "kWh/an", 1.0, 0),
    "autoconso": ("Energie autoconsommee", +1, "kWh/an", 1.0, 0),
    "production": ("Production annuelle brute", +1, "kWh/an", 1.0, 0),
    "cout": ("Cout annuel d'energie", -1, "EUR/an", 1.0, 0),
}

#   niveaux = grilles successives (pas en inclinaison, pas en azimut).
#   Le niveau 0 balaye toute la plage, les suivants resserrent autour du
#   meilleur point trouve au niveau precedent.
ORIENT_EFFORTS = {
    "rapide": {"label": "Rapide - grille large, 1 passe",
               "niveaux": [(15.0, 30.0)], "passes": 1},
    "normal": {"label": "Normal - grille affinee, 2 passes",
               "niveaux": [(15.0, 30.0), (5.0, 10.0)], "passes": 2},
    "fin": {"label": "Fin - grille serree, 3 passes",
            "niveaux": [(10.0, 20.0), (5.0, 10.0), (2.0, 5.0)], "passes": 3},
}


def _grille(v0, v1, pas):
    """Valeurs regulierement espacees de v0 a v1 inclus."""
    if pas <= 0 or v1 <= v0:
        return [float(v0)]
    n = int(round((v1 - v0) / pas))
    return [float(v0 + i * pas) for i in range(n + 1)]


def _grille_autour(centre, demi, pas, borne_min, borne_max):
    """Grille resserree autour d'un point, bornee par la plage autorisee."""
    v0 = max(borne_min, centre - demi)
    v1 = min(borne_max, centre + demi)
    vals = _grille(v0, v1, pas)
    if centre not in vals:
        vals.append(float(centre))
    return sorted(set(round(v, 3) for v in vals))


def _metriques_orientation(pv_dc, load, meteo, cfg):
    """Indicateurs d'une combinaison d'orientations, sans les agregats lourds."""
    d = dispatch(pv_dc, load, meteo, cfg["systeme"])
    ny = meteo["n_years"]
    besoin, imp = d["besoin_total"], d["import"]
    hiver = (meteo["month"] >= 11) | (meteo["month"] <= 2)
    b_hiv, i_hiv = float(besoin[hiver].sum()), float(imp[hiver].sum())
    e = cfg["economie"]

    bes = float(besoin.sum()) / ny
    impa = float(imp.sum()) / ny
    exp = float(d["export"].sum()) / ny
    return {
        "production": float(pv_dc.sum()) / ny,
        "besoin": bes,
        "import": impa,
        "autoconso": bes - impa,
        "autonomie": 1.0 - impa / max(bes, 1e-9),
        "autonomie_hiver": 1.0 - i_hiv / max(b_hiv, 1e-9),
        "ecrete": float(d["ecrete"].sum()) / ny,
        "export": exp,
        "cout": (impa * float(e["prix_kwh_achat"]) + float(e["abonnement_an"])
                 - exp * float(e.get("prix_kwh_revente", 0.0))),
        "cycles": float(d["decharge"].sum()) / ny / max(d["utile"], 1e-9),
    }


def estimer_evaluations(n_libres, effort="normal", inclinaison_min=0.0,
                        inclinaison_max=90.0, azimut_min=90.0, azimut_max=270.0,
                        methode="conjointe"):
    """Majorant du nombre de simulations, pour annoncer une duree a l'avance."""
    conf = ORIENT_EFFORTS[effort]
    total = 0
    for niv, (pas_t, pas_a) in enumerate(conf["niveaux"]):
        if niv == 0:
            nt = len(_grille(inclinaison_min, inclinaison_max, pas_t))
            na = len(_grille(azimut_min, azimut_max, pas_a))
        else:
            pt, pa = conf["niveaux"][niv - 1]
            nt = len(_grille(-pt, pt, pas_t))
            na = len(_grille(-pa, pa, pas_a))
        total += nt * na
    passes = 1 if methode == "independante" else conf["passes"]
    return total * max(n_libres, 1) * passes


ORIENT_METHODES = {
    "conjointe": "Conjointe - chaque groupe optimise face aux autres (recommande)",
    "independante": "Independante - chaque groupe optimise seul",
}


def optimize_orientations(cfg, meteo, libres=None, objectif="autonomie",
                          effort="normal", inclinaison_min=0.0,
                          inclinaison_max=90.0, azimut_min=90.0,
                          azimut_max=270.0, methode="conjointe", progress=None):
    """Cherche la meilleure inclinaison et le meilleur azimut de chaque groupe.

    Chaque groupe est libre de prendre une orientation differente des autres :
    c'est justement la complementarite entre groupes qui fait gagner de
    l'autonomie. Renvoie l'etat avant / apres, la trace de la recherche et,
    pour chaque groupe, la carte du critere sur toute la plage exploree.
    """
    if objectif not in ORIENT_OBJECTIFS:
        raise ValueError(f"Objectif inconnu : {objectif}")
    _lab, sens, _u, _f, _d = ORIENT_OBJECTIFS[objectif]
    conf = ORIENT_EFFORTS[effort]

    actifs = [i for i, c in enumerate(cfg["champs"]) if c.get("actif", True)]
    libres = list(actifs) if libres is None else [i for i in libres if i in actifs]
    if not libres:
        raise ValueError("Aucun groupe actif a optimiser : cochez au moins un "
                         "groupe, et verifiez qu'il est actif dans l'onglet 2.")
    fixes = [i for i in actifs if i not in libres]

    # La consommation ne depend pas de l'orientation : une seule fois.
    load, _detail, _infos = build_load(cfg, meteo)
    albedo = float(cfg["site"].get("albedo", 0.20))
    module = cfg["module"]

    def kwc_de(i):
        ch = cfg["champs"][i]
        return float(ch["n_panneaux"]) * float(ch["wc_panneau"]) / 1000.0

    def pv_de(i, inclinaison, azimut):
        kwc = kwc_de(i)
        if kwc <= 0:
            return np.zeros(meteo["n"])
        p, _poa, _d = field_dc_power(
            meteo, kwc, float(inclinaison), float(azimut), module, albedo,
            float(cfg["champs"][i].get("ombrage_pct", 0.0)))
        return p

    pv_fixe = np.zeros(meteo["n"])
    for i in fixes:
        pv_fixe += pv_de(i, cfg["champs"][i]["inclinaison"],
                         cfg["champs"][i].get("azimut", 180.0))

    total_estime = estimer_evaluations(len(libres), effort, inclinaison_min,
                                       inclinaison_max, azimut_min, azimut_max)
    cache, n_eval = {}, [0]

    def evaluer(combo):
        cle = tuple((round(t, 3), round(a, 3)) for t, a in combo)
        if cle in cache:
            return cache[cle]
        pv = pv_fixe.copy()
        for i, (t, a) in zip(libres, combo):
            pv += pv_de(i, t, a)
        m = _metriques_orientation(pv, load, meteo, cfg)
        cache[cle] = m
        n_eval[0] += 1
        if progress and n_eval[0] % 4 == 0:
            progress(min(int(100 * n_eval[0] / max(total_estime, 1)), 99),
                     f"{n_eval[0]} orientations testees")
        return m

    def evaluer_seul(i, t, a):
        """Le groupe i seul face a la consommation, les autres ignores."""
        cle = (i, round(t, 3), round(a, 3))
        if cle in cache_seul:
            return cache_seul[cle]
        m = _metriques_orientation(pv_de(i, t, a), load, meteo, cfg)
        cache_seul[cle] = m
        n_eval[0] += 1
        if progress and n_eval[0] % 4 == 0:
            progress(min(int(100 * n_eval[0] / max(total_estime, 1)), 99),
                     f"{n_eval[0]} orientations testees")
        return m

    cache_seul = {}
    combo = [(float(cfg["champs"][i]["inclinaison"]),
              float(cfg["champs"][i].get("azimut", 180.0))) for i in libres]
    depart = list(combo)
    m_avant = evaluer(combo)

    # On retient le meilleur point jamais evalue, pas seulement le point
    # d'arrivee : la descente par coordonnees peut osciller sur une egalite.
    best_combo, best_score = list(combo), sens * m_avant[objectif]

    # Le plus gros groupe choisit en premier : il prend l'orientation la plus
    # rentable, les petits viennent ensuite completer les heures decouvertes.
    ordre = sorted(range(len(libres)), key=lambda k: -kwc_de(libres[k]))
    grilles, historique = {}, []

    if methode == "independante":
        # Chaque groupe est optimise seul, sans voir les autres. Utile pour
        # comparer, mais les groupes convergent alors presque toujours vers la
        # meme orientation : rien ne les pousse a se repartir la journee.
        for k in ordre:
            centre = combo[k]
            for niv, (pas_t, pas_a) in enumerate(conf["niveaux"]):
                if niv == 0:
                    tilts = _grille(inclinaison_min, inclinaison_max, pas_t)
                    azs = _grille(azimut_min, azimut_max, pas_a)
                else:
                    pt, pa = conf["niveaux"][niv - 1]
                    tilts = _grille_autour(centre[0], pt, pas_t,
                                           inclinaison_min, inclinaison_max)
                    azs = _grille_autour(centre[1], pa, pas_a,
                                         azimut_min, azimut_max)
                carte = np.full((len(tilts), len(azs)), np.nan)
                local = None
                for it, t in enumerate(tilts):
                    for ia, a in enumerate(azs):
                        m = evaluer_seul(libres[k], t, a)
                        carte[it, ia] = m[objectif]
                        s = sens * m[objectif]
                        if local is None or s > local[0]:
                            local = (s, t, a)
                if niv == 0:
                    grilles[libres[k]] = {"inclinaisons": tilts, "azimuts": azs,
                                          "carte": carte}
                centre = (local[1], local[2])
            if centre != combo[k]:
                historique.append({"passe": 1,
                                   "champ": cfg["champs"][libres[k]]["nom"],
                                   "de": combo[k], "vers": centre,
                                   "score": local[0]})
            combo[k] = centre
        m_final = evaluer(combo)
        best_combo = list(combo)
        return _resultat_orientation(cfg, objectif, sens, effort, methode, libres,
                                     fixes, depart, best_combo, m_avant, m_final,
                                     grilles, historique, n_eval[0], kwc_de)

    for passe in range(conf["passes"]):
        bouge = False
        for k in ordre:
            centre = combo[k]
            for niv, (pas_t, pas_a) in enumerate(conf["niveaux"]):
                if niv == 0:
                    tilts = _grille(inclinaison_min, inclinaison_max, pas_t)
                    azs = _grille(azimut_min, azimut_max, pas_a)
                else:
                    pt, pa = conf["niveaux"][niv - 1]
                    tilts = _grille_autour(centre[0], pt, pas_t,
                                           inclinaison_min, inclinaison_max)
                    azs = _grille_autour(centre[1], pa, pas_a,
                                         azimut_min, azimut_max)
                carte = np.full((len(tilts), len(azs)), np.nan)
                local = None
                for it, t in enumerate(tilts):
                    for ia, a in enumerate(azs):
                        essai = list(combo)
                        essai[k] = (t, a)
                        m = evaluer(essai)
                        carte[it, ia] = m[objectif]
                        s = sens * m[objectif]
                        if local is None or s > local[0]:
                            local = (s, t, a)
                        if s > best_score:
                            best_score, best_combo = s, list(essai)
                if niv == 0:
                    grilles[libres[k]] = {"inclinaisons": tilts, "azimuts": azs,
                                          "carte": carte}
                centre = (local[1], local[2])
            if centre != combo[k]:
                bouge = True
                historique.append({
                    "passe": passe + 1, "champ": cfg["champs"][libres[k]]["nom"],
                    "de": combo[k], "vers": centre,
                    "score": sens * local[0] if sens < 0 else local[0]})
            combo[k] = centre
        if not bouge:
            break

    m_apres = evaluer(best_combo)
    return _resultat_orientation(cfg, objectif, sens, effort, methode, libres,
                                 fixes, depart, best_combo, m_avant, m_apres,
                                 grilles, historique, n_eval[0], kwc_de)


def _resultat_orientation(cfg, objectif, sens, effort, methode, libres, fixes,
                          depart, arrivee, m_avant, m_apres, grilles,
                          historique, evaluations, kwc_de):
    def decrire(valeurs):
        return [{"index": i, "nom": cfg["champs"][i]["nom"], "kwc": kwc_de(i),
                 "inclinaison": t, "azimut": a}
                for i, (t, a) in zip(libres, valeurs)]

    return {
        "objectif": objectif,
        "sens": sens,
        "effort": effort,
        "methode": methode,
        "libres": libres,
        "fixes": [{"index": i, "nom": cfg["champs"][i]["nom"], "kwc": kwc_de(i),
                   "inclinaison": float(cfg["champs"][i]["inclinaison"]),
                   "azimut": float(cfg["champs"][i].get("azimut", 180.0))}
                  for i in fixes],
        "avant": {"champs": decrire(depart), "metriques": m_avant},
        "apres": {"champs": decrire(arrivee), "metriques": m_apres},
        "grilles": grilles,
        "historique": historique,
        "evaluations": evaluations,
    }


def appliquer_orientations(cfg, resultat):
    """Ecrit dans la configuration les orientations proposees."""
    for c in resultat["apres"]["champs"]:
        ch = cfg["champs"][c["index"]]
        ch["inclinaison"] = round(float(c["inclinaison"]), 2)
        ch["azimut"] = round(float(c["azimut"]), 2)
    return cfg


# --------------------------------------------------------------------------
# Budget energetique : combien puis-je consommer ?
# --------------------------------------------------------------------------
# Leviers : d'ou vient l'import reseau, et que rapporte chaque action
# --------------------------------------------------------------------------
def attribution_import(res: dict, meteo: dict, prix_kwh: float = 0.0) -> list[dict]:
    """Impute a chaque poste sa part de l'energie achetee au reseau.

    A chaque heure, l'import est reparti entre les postes au prorata de leur
    part dans le besoin de cette heure-la. Un poste qui tourne la nuit porte
    donc la quasi-totalite de l'import nocturne, alors qu'un poste cale sur
    le soleil n'en porte presque rien : c'est exactement ce que l'on cherche
    a voir pour savoir quel appareil coute cher au reseau.
    """
    d = res["dispatch"]
    besoin = d["besoin_total"]
    part = np.where(besoin > 1e-9, d["import"] / np.maximum(besoin, 1e-9), 0.0)
    nuit = res["pv_dc"] <= 0.05
    ny = meteo["n_years"]
    lignes = []
    postes = list(res["detail_postes"].items())
    postes.append(("Veille des onduleurs", d["veille"]))
    for nom, arr in postes:
        conso = float(arr.sum() / ny)
        imp = float((arr * part).sum() / ny)
        lignes.append({
            "nom": nom,
            "conso": conso,
            "import": imp,
            "autoconso": conso - imp,
            "part_importee": imp / max(conso, 1e-9),
            "part_nuit": float(arr[nuit].sum() / max(arr.sum(), 1e-9)),
            "cout_import": imp * prix_kwh,
        })
    lignes.sort(key=lambda l: -l["import"])
    return lignes


# Parametre a diminuer pour tester "ce poste consomme 10 % de moins",
# par type de poste.
LEVIER_REDUCTION = {
    "talon": ("puissance_w", "la puissance permanente"),
    "generique": ("kwh_an", "la consommation annuelle"),
    "chauffage": ("besoin_th_kwh_an", "le besoin thermique"),
    "ecs": ("besoin_th_kwh_an", "le besoin d'eau chaude"),
    "spa": ("ua_w_par_k", "les pertes du spa"),
    "piscine": ("heures_par_mois", "les heures de filtration"),
    "vehicule": ("km_an", "le kilometrage annuel"),
}

# Profils deja cales sur le soleil : rien a decaler.
PROFILS_SOLAIRES = ("solaire", "solaire_large", "jour")


def _mettre_a_l_echelle(params: dict, cle: str, facteur: float) -> bool:
    """Multiplie un parametre (scalaire ou liste de 12 mois) par un facteur."""
    if cle not in params:
        return False
    v = params[cle]
    if isinstance(v, (list, tuple)):
        params[cle] = [float(x) * facteur for x in v]
    else:
        params[cle] = float(v) * facteur
    return True


def _champ_principal(cfg: dict) -> int:
    """Index du champ actif qui porte le plus de panneaux."""
    actifs = [(i, c) for i, c in enumerate(cfg["champs"]) if c.get("actif", True)]
    if not actifs:
        return -1
    return max(actifs, key=lambda ic: int(ic[1].get("n_panneaux", 0)))[0]


def _variantes_leviers(cfg, reduction, talon_w, cout_remplacement):
    """Liste des actions a simuler : (nom, categorie, explication, mutation,
    cout impose)."""
    v = []
    idx = _champ_principal(cfg)
    if idx >= 0:
        nom_champ = cfg["champs"][idx]["nom"]
        for n in (1, 4):
            def ajoute(c, n=n, idx=idx):
                c["champs"][idx]["n_panneaux"] = int(
                    c["champs"][idx].get("n_panneaux", 0)) + n
            v.append((f"+{n} panneau{'x' if n > 1 else ''}", "Production",
                      f"{n} module{'s' if n > 1 else ''} de plus sur \"{nom_champ}\", "
                      f"cablage compris. Le cout vient de la nomenclature.",
                      ajoute, None))

    pack = KWH_PAR_PACK_16S
    for n in (1, 2):
        def batterie(c, n=n, pack=pack):
            c["systeme"]["batt_kwh_nominal"] = float(
                c["systeme"]["batt_kwh_nominal"]) + n * pack
        v.append((f"+{n} pack batterie ({n * pack:.0f} kWh)", "Stockage",
                  f"{n} pack{'s' if n > 1 else ''} LFP de plus : cellules, BMS et "
                  f"coffret. Deplace de l'energie du jour vers la nuit, sans "
                  f"produire un kWh de plus.",
                  batterie, None))

    def onduleur(c):
        c["systeme"]["n_onduleurs"] = int(c["systeme"]["n_onduleurs"]) + 1
    v.append(("+1 onduleur", "Production",
              "Un onduleur hybride de plus : plus de puissance AC et de "
              "puissance de charge, donc moins d'ecretage aux heures de pointe.",
              onduleur, None))

    for i, p in enumerate(cfg["postes"]):
        if not p.get("actif", True):
            continue
        kind = p.get("kind", "generique")
        nom = p["nom"]
        cle_lib = LEVIER_REDUCTION.get(kind)
        if cle_lib:
            cle, libelle = cle_lib

            def reduire(c, i=i, cle=cle, f=1.0 - reduction):
                _mettre_a_l_echelle(c["postes"][i]["params"], cle, f)
            v.append((f"{nom} : -{100 * reduction:.0f} %", "Consommation",
                      f"Reduction de {100 * reduction:.0f} % sur {libelle} de "
                      f"\"{nom}\" (appareil plus sobre, meilleure isolation, "
                      f"usage plus court). Le cout depend de l'action retenue.",
                      reduire, 0.0))
        profil = p.get("params", {}).get("profil")
        if profil and profil not in PROFILS_SOLAIRES:
            def decaler(c, i=i):
                c["postes"][i]["params"]["profil"] = "solaire_large"
            v.append((f"{nom} : decaler au soleil", "Pilotage",
                      f"Meme energie, mais consommee entre 9 h et 17 h "
                      f"(programmation, minuterie) au lieu du profil "
                      f"\"{profil}\". Ne coute rien d'autre qu'un reglage.",
                      decaler, 0.0))

    talon = next((i for i, p in enumerate(cfg["postes"])
                  if p.get("kind") == "talon" and p.get("actif", True)), None)
    if talon is not None and talon_w > 0:
        nom = cfg["postes"][talon]["nom"]

        def froid(c, talon=talon, talon_w=talon_w):
            p = c["postes"][talon]["params"]
            p["puissance_w"] = max(float(p.get("puissance_w", 0.0)) - talon_w, 0.0)
        v.append((f"{nom} : -{talon_w:.0f} W en continu", "Consommation",
                  f"Remplacement des appareils de fond les plus gourmands "
                  f"(refrigerateur, congelateur, vieux circulateur) : "
                  f"{talon_w:.0f} W de moins 24 h/24, soit "
                  f"{talon_w * 8.766:.0f} kWh/an. Cout suppose du remplacement "
                  f"pris dans le champ prevu a cet effet.",
                  froid, float(cout_remplacement)))
    return v


def leviers(cfg: dict, meteo: dict, reduction=0.10, talon_w=50.0,
            cout_remplacement=900.0, progress=None) -> dict:
    """Classe les actions possibles par gain annuel sur la facture.

    Chaque action est reellement simulee sur toute la serie meteo, puis
    comparee a la configuration actuelle : energie achetee en moins, euros
    economises par an, cout de l'action et temps de retour.
    """
    from copy import deepcopy
    base = simulate(cfg, meteo)
    prix = float(cfg["economie"]["prix_kwh_achat"])
    ref = {
        "cout_an": base["eco"]["cout_annuel_avec_pv"],
        "capex": base["eco"]["capex_solaire"],
        "capex_total": base["eco"]["capex_total"],
        "import_an": base["kpi"]["import_an"],
        "besoin_an": base["kpi"]["besoin_an"],
        "autonomie": base["kpi"]["autonomie"],
        "ecrete_an": base["kpi"]["ecrete_an"],
    }
    variantes = _variantes_leviers(cfg, reduction, talon_w, cout_remplacement)
    actions = []
    for i, (nom, cat, detail, mutation, cout_impose) in enumerate(variantes):
        c = deepcopy(cfg)
        mutation(c)
        r = simulate(c, meteo)
        # Un levier qui touche au materiel solaire (panneau, pack, onduleur)
        # se chiffre par la difference de nomenclature, perimetre solaire.
        # Un levier de sobriete ou de pilotage porte un cout impose, hors
        # perimetre solaire : c'est un appareil remplace, pas un panneau.
        d_solaire = r["eco"]["capex_solaire"] - ref["capex"]
        if cout_impose is None:
            cout, cout_sol, cout_hors = d_solaire, d_solaire, 0.0
        else:
            cout = float(cout_impose)
            cout_sol, cout_hors = 0.0, cout
        gain = ref["cout_an"] - r["eco"]["cout_annuel_avec_pv"]
        retour = cout / gain if (cout > 0 and gain > 0) else None
        actions.append({
            "nom": nom, "categorie": cat, "detail": detail,
            "gain_an": gain,
            "cout_solaire": cout_sol,
            "cout_hors_solaire": cout_hors,
            "import_evite": ref["import_an"] - r["kpi"]["import_an"],
            "besoin_evite": ref["besoin_an"] - r["kpi"]["besoin_an"],
            "autonomie": r["kpi"]["autonomie"],
            "gain_autonomie": r["kpi"]["autonomie"] - ref["autonomie"],
            "cout": cout,
            "retour": retour,
            "gain_par_1000": 1000.0 * gain / cout if cout > 0 else None,
        })
        if progress:
            progress(int(100 * (i + 1) / len(variantes)), nom)
    actions.sort(key=lambda a: -a["gain_an"])
    return {"reference": ref, "prix_kwh": prix, "actions": actions,
            "attribution": attribution_import(base, meteo, prix)}


# --------------------------------------------------------------------------
def budget_consommation(cfg: dict, meteo: dict, res: dict, cible=0.92):
    """Pour chaque mois, consommation journaliere maximale compatible avec
    l'objectif d'autonomie, a installation constante."""
    from copy import deepcopy
    m = res["mensuel"]
    out = []
    for k in range(12):
        lo, hi = 0.02, 4.0
        base = m["consommation"][k]
        njours = max(m["jours"][k], 1e-9)
        if base <= 0:
            out.append((0.0, 0.0))
            continue
        # La dichotomie ne vaut que si la borne basse tient la cible et que
        # la borne haute ne la tient pas. Sinon on renvoyait le plancher
        # comme un resultat : un mois de janvier a 4,11 kWh/jour alors que
        # l'autonomie reelle y etait de 2,7 %, soit un chiffre faux sans le
        # moindre signe. On rend None, l'interface affiche un tiret.
        if _autonomie_mois_scaled(cfg, meteo, res, k, lo) < cible:
            out.append((None, None))          # meme quasiment a l'arret, la
            continue                          # cible reste hors d'atteinte
        if _autonomie_mois_scaled(cfg, meteo, res, k, hi) >= cible:
            out.append((base * hi, base * hi / njours))   # jamais limitant
            continue
        for _ in range(18):
            f = (lo + hi) / 2.0
            if _autonomie_mois_scaled(cfg, meteo, res, k, f) >= cible:
                lo = f
            else:
                hi = f
        out.append((base * lo, base * lo / njours))
    return out


def _autonomie_mois_scaled(cfg, meteo, res, mois_idx, facteur):
    mask = meteo["month"] == mois_idx + 1
    load = res["load"].copy()
    load[mask] *= facteur
    d = dispatch(res["pv_dc"], load, meteo, cfg["systeme"])
    besoin = d["besoin_total"][mask].sum()
    imp = d["import"][mask].sum()
    return 1.0 - imp / max(besoin, 1e-9)


# --------------------------------------------------------------------------
# Optimum a deux dimensions : puissance PV x capacite batterie
#
#   Les deux ne sont pas independantes : des panneaux sans batterie produisent
#   un surplus qu'on jette, une batterie sans panneaux n'a rien a stocker.
#   Balayer l'une puis l'autre donne donc une reponse fausse ; il faut la
#   grille complete. Une simulation coute moins de 0,1 s, une grille de 60
#   points quelques secondes : c'est abordable.
# --------------------------------------------------------------------------
#   cle -> (libelle, sens, unite, decimales, aide courte)
#   sens = +1 : on maximise ; -1 : on minimise
CRITERES_GRILLE = {
    "gain": ("Gain cumule sur l'horizon", +1, "EUR", 0,
             "Ce que l'installation aura rapporte, net de son cout, au bout "
             "de la duree d'analyse. C'est le critere qui designe la taille "
             "la plus profitable, celle ou l'on gagne le plus d'argent."),
    "retour": ("Temps de retour le plus court", -1, "ans", 1,
               "La taille qui se rembourse le plus vite. Attention : c'est "
               "presque toujours la plus petite installation testee, parce "
               "que les premiers kWh sont les plus rentables. A lire avec le "
               "chemin de croissance."),
    "autonomie_sous_seuil": ("Autonomie maximale, tranches rentables", +1, "%", 2,
                             "La plus grande autonomie atteignable sans "
                             "jamais acheter une tranche qui mettrait plus "
                             "que le seuil a se rembourser."),
    "cout_kwh": ("Cout du kWh evite le plus bas", -1, "EUR/kWh", 3,
                 "La taille qui produit le kWh autoconsomme le moins cher."),
    "autonomie": ("Autonomie maximale", +1, "%", 2,
                  "La plus grande autonomie, sans aucune consideration de "
                  "cout. Designe toujours le plus gros point de la grille."),
}


def _config_dimensionnee(cfg: dict, kwc_cible: float, batt_kwh: float):
    """Copie de la configuration mise a l'echelle. Retourne (cfg, kwc_reel).

    Les panneaux sont un nombre entier par groupe : la puissance obtenue
    n'est pas exactement celle demandee, et c'est elle qu'il faut rapporter.
    """
    from copy import deepcopy
    c = deepcopy(cfg)
    base = total_kwc(cfg)
    if base > 0 and kwc_cible is not None:
        r = float(kwc_cible) / base
        for ch in c["champs"]:
            ch["n_panneaux"] = max(int(round(ch["n_panneaux"] * r)), 0)
            n_ser = max(int(ch.get("n_serie", 1) or 1), 1)
            if ch["n_panneaux"]:
                ch["n_serie"] = min(n_ser, ch["n_panneaux"])
    c["systeme"]["batt_kwh_nominal"] = float(batt_kwh)
    return c, total_kwc(c)


def grille_dimensionnement(cfg: dict, meteo: dict, kwc_valeurs, batt_valeurs,
                           progress=None) -> dict:
    """Simule toutes les combinaisons puissance PV x capacite batterie.

    Retourne un dictionnaire de matrices indexees [i_batterie][j_puissance],
    plus la liste des puissances reellement obtenues.
    """
    kwc_valeurs = [float(v) for v in kwc_valeurs]
    batt_valeurs = [float(v) for v in batt_valeurs]
    e = cfg["economie"]
    infl = float(e.get("inflation_energie", 0.0))
    horizon = int(e.get("duree_analyse_ans", 25))

    n_b, n_k = len(batt_valeurs), len(kwc_valeurs)
    vide = lambda: np.full((n_b, n_k), np.nan)
    m = {c: vide() for c in ("autonomie", "capex", "economie", "retour",
                             "gain", "cout_kwh", "import", "ecrete",
                             "production", "cout_par_wc")}
    kwc_reels = list(kwc_valeurs)
    total = max(n_b * n_k, 1)
    fait = 0

    for i, batt in enumerate(batt_valeurs):
        for j, kwc in enumerate(kwc_valeurs):
            c, kwc_reel = _config_dimensionnee(cfg, kwc, batt)
            if i == 0:
                kwc_reels[j] = kwc_reel
            r = simulate(c, meteo)
            k, eco = r["kpi"], r["eco"]
            m["autonomie"][i, j] = k["autonomie"]
            m["capex"][i, j] = eco["capex_solaire"]
            m["economie"][i, j] = eco["economie_vs_sans_pv"]
            m["import"][i, j] = k["import_an"]
            m["ecrete"][i, j] = k["ecrete_an"]
            m["production"][i, j] = k["production_an"]
            m["cout_par_wc"][i, j] = eco["cout_par_wc"]
            ret, cum = _retour(eco["capex_solaire"], eco["economie_vs_sans_pv"],
                               infl, horizon)
            m["retour"][i, j] = np.nan if ret is None else ret
            m["gain"][i, j] = cum
            lc = eco["lcoe_kwh_utile"]
            m["cout_kwh"][i, j] = np.nan if lc is None else lc
            fait += 1
            if progress:
                progress(int(100 * fait / total),
                         f"{kwc_reel:.1f} kWc / {batt:.0f} kWh")

    return {"kwc": kwc_reels, "kwc_demandes": kwc_valeurs, "batt": batt_valeurs,
            "horizon": horizon, "matrices": m}


def chemin_croissance(grille: dict, seuil_ans: float = 10.0,
                      depart=(0, 0)) -> dict:
    """Par quoi commencer, et quand s'arreter, en agrandissant pas a pas.

    A chaque etape on compare les deux seules decisions possibles : un cran
    de panneaux de plus, ou un cran de batterie de plus. On retient celle qui
    se rembourse le plus vite, ET seulement si elle se rembourse sous le
    seuil. C'est la facon dont une installation grandit reellement, et cela
    repond a la question que le temps de retour global ne sait pas traiter :
    est-ce que la PROCHAINE tranche vaut le coup ?
    """
    m = grille["matrices"]
    kwc, batt = grille["kwc"], grille["batt"]
    horizon = grille["horizon"]
    n_b, n_k = len(batt), len(kwc)
    i, j = depart
    etapes = [{
        "i": i, "j": j, "quoi": "Point de depart",
        "kwc": kwc[j], "batt": batt[i],
        "capex": float(m["capex"][i, j]),
        "autonomie": float(m["autonomie"][i, j]),
        "cout_tranche": None, "gain_tranche": None, "retour_tranche": None,
    }]
    arret = None
    vus = {(i, j)}

    while True:
        candidats = []
        for di, dj, quoi in ((0, 1, "panneaux"), (1, 0, "batterie")):
            ni, nj = i + di, j + dj
            if ni >= n_b or nj >= n_k or (ni, nj) in vus:
                continue
            cout = float(m["capex"][ni, nj] - m["capex"][i, j])
            gain = float(m["economie"][ni, nj] - m["economie"][i, j])
            if not np.isfinite(cout) or not np.isfinite(gain):
                continue
            if cout <= 1e-6:
                ret = 0.0 if gain > 0 else None
            elif gain <= 1e-9:
                ret = None
            else:
                ret = cout / gain      # sans inflation : comparaison entre eux
            candidats.append({"i": ni, "j": nj, "quoi": quoi, "cout": cout,
                              "gain": gain, "retour": ret})
        faisables = [c for c in candidats if c["retour"] is not None]
        if not faisables:
            arret = ("Plus aucune tranche ne rapporte quoi que ce soit."
                     if candidats else "Bord de la grille atteint : elargissez "
                                       "les plages testees.")
            break
        meilleur = min(faisables, key=lambda c: c["retour"])
        if meilleur["retour"] > seuil_ans:
            arret = (f"La meilleure tranche suivante ({meilleur['quoi']}) "
                     f"mettrait {meilleur['retour']:.1f} ans a se rembourser, "
                     f"au-dela du seuil de {seuil_ans:.0f} ans.")
            break
        i, j = meilleur["i"], meilleur["j"]
        vus.add((i, j))
        libelle = (f"+{kwc[j] - etapes[-1]['kwc']:.1f} kWc de panneaux"
                   if meilleur["quoi"] == "panneaux"
                   else f"+{batt[i] - etapes[-1]['batt']:.0f} kWh de batterie")
        etapes.append({
            "i": i, "j": j, "quoi": libelle,
            "kwc": kwc[j], "batt": batt[i],
            "capex": float(m["capex"][i, j]),
            "autonomie": float(m["autonomie"][i, j]),
            "cout_tranche": meilleur["cout"], "gain_tranche": meilleur["gain"],
            "retour_tranche": meilleur["retour"],
        })
        if len(etapes) > n_b * n_k:
            break
    return {"etapes": etapes, "arret": arret, "seuil_ans": seuil_ans,
            "horizon": horizon}


def optimum_grille(grille: dict, critere: str = "gain",
                   seuil_ans: float = 10.0):
    """Indice (i_batterie, j_puissance) du meilleur point selon un critere."""
    m = grille["matrices"]
    if critere == "autonomie_sous_seuil":
        chemin = chemin_croissance(grille, seuil_ans)
        d = chemin["etapes"][-1]
        return d["i"], d["j"]
    cle = {"gain": "gain", "retour": "retour", "cout_kwh": "cout_kwh",
           "autonomie": "autonomie"}.get(critere, "gain")
    mat = m[cle]
    if np.all(np.isnan(mat)):
        return 0, 0
    sens = CRITERES_GRILLE.get(critere, CRITERES_GRILLE["gain"])[1]
    plat = np.where(np.isnan(mat), -np.inf if sens > 0 else np.inf, mat)
    idx = int(np.argmax(plat) if sens > 0 else np.argmin(plat))
    return divmod(idx, mat.shape[1])


def parse_sweep_values(raw) -> list[float]:
    """Parse une liste de nombres ou un intervalle du type 10:100@5."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, np.ndarray)):
        return [float(v) for v in raw]
    text = str(raw).replace(";", ",").strip()
    if not text:
        return []
    chunks = [p.strip() for p in text.split(",") if p.strip()]
    vals = []
    for chunk in chunks:
        if ":" not in chunk:
            vals.append(float(chunk))
            continue
        m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)\s*(?:@\s*(\d+(?:\.\d+)?)\s*)?", chunk)
        if not m:
            raise ValueError(f"Format de plage non valide : {chunk!r}. Exemple : 10:100@5")
        start, end, step = m.groups()
        start = float(start); end = float(end)
        step_val = float(step) if step is not None else 1.0
        if step_val <= 0:
            raise ValueError(f"Le pas doit etre strictement positif : {chunk!r}")
        if start <= end:
            cur = start
            while cur <= end + 1e-9:
                vals.append(float(cur))
                cur += step_val
        else:
            cur = start
            while cur >= end - 1e-9:
                vals.append(float(cur))
                cur -= step_val
    return vals


#: etats possibles d'une tranche, et ce qu'ils veulent dire pour la decision
TRANCHE_STATUTS = {
    "depart": "Point de depart du balayage, pas de tranche avant lui.",
    "gratuit": "Cette tranche ne coute rien de plus et rapporte : a prendre.",
    "amorti": "Cette tranche se rembourse toute seule dans l'horizon d'analyse.",
    "trop_long": "Cette tranche finit par se rembourser, mais au-dela de "
                 "l'horizon d'analyse.",
    "jamais": "Cette tranche coute et ne rapporte rien, ou fait perdre : "
              "elle ne se remboursera pas.",
    "sans_surcout": "Cette tranche ne change pas la nomenclature (inclinaison, "
                    "azimut) : il n'y a rien a amortir.",
}


def tranches_successives(out: list[dict], cfg: dict,
                         key: str = "economie_vs_sans_pv") -> list[dict]:
    """Ce que coute et rapporte le PASSAGE d'un point du balayage au suivant.

    Pour chaque point i, on compare au point i-1 :

      * cout_tranche  = surcout de materiel du passage (perimetre solaire) ;
      * gain_tranche  = euros economises en plus chaque annee ;
      * retour_tranche = annees pour que cette tranche SEULE se rembourse,
        inflation de l'energie comprise, independamment de tout ce qui a
        ete installe avant ;
      * gain_net_tranche = ce que la tranche aura rapporte, net de son cout,
        au bout de l'horizon d'analyse.

    C'est la difference avec le temps de retour global : une premiere
    batterie amortie en 3 ans suivie d'une seconde qui ne s'amortit jamais
    donnent un retour global d'environ 6 ans, chiffre qui masque exactement
    la decision a prendre. Ici la seconde tranche ressort telle qu'elle est.
    """
    e = cfg["economie"]
    infl = float(e.get("inflation_energie", 0.0))
    horizon = int(e.get("duree_analyse_ans", 25))
    if out and key not in out[0]:
        key = "economie_vs_actuel"

    tranches = []
    for i, cur in enumerate(out):
        if i == 0:
            tranches.append({"cout_tranche": None, "gain_tranche": None,
                             "retour_tranche": None, "gain_net_tranche": None,
                             "delta_valeur": None, "statut": "depart"})
            continue
        prev = out[i - 1]
        cout = float(cur.get("capex", 0.0)) - float(prev.get("capex", 0.0))
        gain = float(cur.get(key, 0.0)) - float(prev.get(key, 0.0))
        delta_v = float(cur.get("valeur", 0.0)) - float(prev.get("valeur", 0.0))

        if cout <= 1e-6:
            # Rien a payer. Si cela rapporte quand meme (une inclinaison mieux
            # choisie), la tranche est gratuite et immediatement rentable ;
            # sinon il n'y a simplement rien a amortir.
            if gain > 1e-9:
                statut, (retour, net) = "gratuit", _retour(0.0, gain, infl, horizon)
            else:
                statut, retour, net = "sans_surcout", None, None
        elif gain <= 1e-9:
            statut, retour = "jamais", None
            net = -cout
        else:
            retour, net = _retour(cout, gain, infl, horizon)
            statut = "amorti" if retour is not None else "trop_long"
        tranches.append({"cout_tranche": cout, "gain_tranche": gain,
                         "retour_tranche": retour, "gain_net_tranche": net,
                         "delta_valeur": delta_v, "statut": statut})
    return tranches


def derniere_tranche_rentable(out: list[dict], seuil_ans: float = None):
    """Indice du dernier point dont la tranche se rembourse assez vite.

    C'est la reponse a "jusqu'ou est-ce que je pousse ?" : au-dela, chaque
    tranche supplementaire coute plus qu'elle ne rapportera. Retourne None
    si aucune tranche ne tient le critere.
    """
    dernier = None
    for i, o in enumerate(out):
        if o.get("statut") == "depart":
            dernier = i
            continue
        r = o.get("retour_tranche")
        if r is None:
            break
        if seuil_ans is not None and r > seuil_ans:
            break
        dernier = i
    return dernier


def compute_marginal_rentability(out: list[dict], key="economie_vs_sans_pv") -> list[float]:
    """Euros economises chaque annee par euro supplementaire investi.

    Le capex compare est celui du PERIMETRE SOLAIRE : pendant un balayage,
    le reste du projet ne bouge pas, mais il ecraserait le ratio.
    """
    if out and key not in out[0]:
        key = "economie_vs_actuel"        # series produites avant la separation
    scores = [None]
    for i in range(1, len(out)):
        prev = out[i - 1]
        cur = out[i]
        delta_capex = float(cur.get("capex", 0.0)) - float(prev.get("capex", 0.0))
        delta_gain = float(cur.get(key, 0.0)) - float(prev.get(key, 0.0))
        if delta_capex <= 1e-6:
            # Rien de plus n'a ete depense : c'est le cas de tout balayage
            # d'inclinaison ou d'azimut. Un ratio n'a pas de sens ici, et
            # afficher 0,000 laissait croire a une option sans interet.
            scores.append(None)
        else:
            scores.append(delta_gain / delta_capex)
    return scores


def sweep(cfg: dict, meteo: dict, variable: str, valeurs, progress=None):
    """Balaye un parametre et retourne autonomie / production / capex / import."""
    from copy import deepcopy
    if isinstance(valeurs, str):
        valeurs = parse_sweep_values(valeurs)
    if not valeurs:
        return []
    out = []
    for i, v in enumerate(valeurs):
        c = deepcopy(cfg)
        if variable == "inclinaison":
            for ch in c["champs"]:
                ch["inclinaison"] = float(v)
        elif variable == "kwc":
            base = total_kwc(cfg)
            if base <= 0:
                continue
            r = float(v) / base
            for ch in c["champs"]:
                ch["n_panneaux"] = max(int(round(ch["n_panneaux"] * r)), 0)
            # On ne peut installer qu'un nombre ENTIER de panneaux par groupe :
            # la puissance reellement simulee n'est pas celle qui a ete
            # demandee. Afficher la valeur demandee ferait mentir l'axe.
            v = total_kwc(c)
        elif variable == "batterie":
            c["systeme"]["batt_kwh_nominal"] = float(v)
        elif variable == "onduleurs":
            c["systeme"]["n_onduleurs"] = int(v)
        elif variable == "inclinaison_champ1" and c["champs"]:
            c["champs"][0]["inclinaison"] = float(v)
        r = simulate(c, meteo)
        out.append({"valeur": float(v), "autonomie": r["kpi"]["autonomie"],
                    "production": r["kpi"]["production_an"],
                    "import": r["kpi"]["import_an"],
                    "ecrete": r["kpi"]["ecrete_an"],
                    "kwc": r["kpi"]["kwc"],
                    # "capex" = perimetre solaire : c'est la seule depense que
                    # le balayage fait varier, et la seule qui doit etre
                    # comparee au gain qu'il apporte.
                    "capex": r["eco"]["capex_solaire"],
                    "capex_hors_solaire": r["eco"]["capex_hors_solaire"],
                    "capex_total": r["eco"]["capex_total"],
                    "cout_par_wc": r["eco"]["cout_par_wc"],
                    "lcoe": r["eco"]["lcoe_kwh_utile"],
                    "retour": r["eco"]["retour_ans_vs_sans_pv"],
                    "retour_projet": r["eco"]["retour_ans_vs_actuel"],
                    "gain_cumule_solaire": r["eco"]["gain_cumule_solaire"],
                    "economie_vs_sans_pv": r["eco"]["economie_vs_sans_pv"],
                    "economie_vs_actuel": r["eco"]["economie_vs_actuel"],
                    "production_mensuel": r["mensuel"]["production_dc"].tolist(),
                    "autonomie_mensuel": r["mensuel"]["autonomie"].tolist(),
                    "import_mensuel": r["mensuel"]["import"].tolist()})
        if progress:
            progress(int(100 * (i + 1) / len(valeurs)), f"{variable} = {v}")
    if out:
        for i, score in enumerate(compute_marginal_rentability(out)):
            out[i]["rentabilite_marginale"] = score
        for i, t in enumerate(tranches_successives(out, cfg)):
            out[i].update(t)
    return out

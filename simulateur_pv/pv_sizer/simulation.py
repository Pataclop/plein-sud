"""
Moteur de simulation : production des champs, gestion onduleur / batterie
au pas horaire, agregations jour et mois, bilan economique.
"""
from __future__ import annotations
import re
import numpy as np

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
    for ch in cfg["champs"]:
        if not ch.get("actif", True):
            continue
        kwc = float(ch["n_panneaux"]) * float(ch["wc_panneau"]) / 1000.0
        if kwc <= 0:
            continue
        p, poa, d = field_dc_power(meteo, kwc, float(ch["inclinaison"]),
                                   float(ch.get("azimut", 180.0)), module,
                                   albedo, float(ch.get("ombrage_pct", 0.0)))
        par_champ[ch["nom"]] = p
        d["kwc"] = kwc
        d["production_kwh_an"] = float(p.sum() / meteo["n_years"])
        diags[ch["nom"]] = d
        total += p
    return total, par_champ, diags


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
    n_ser = max(int(champ.get("n_serie", 0) or 0), 1)
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
    imp = np.zeros(n)
    exp = np.zeros(n)
    ecrete_ac = np.zeros(n)      # bride par la puissance nominale onduleur
    charge = np.zeros(n)
    decharge = np.zeros(n)
    non_servi = np.zeros(n)      # depassement de la puissance souscrite

    s = soc_min + float(sys.get("soc_initial", 0.5)) * utile
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
            # 3. Batterie vers charges
            dispo = max(s - soc_min, 0.0) * e_dis * e_bt_ac
            d = min(deficit, p_dis_max, dispo)
            s -= d / max(e_dis * e_bt_ac, 1e-6)
            decharge[i] = d
            deficit -= d
            if deficit > 1e-9:
                g = min(deficit, grid_max)
                imp[i] = g
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
                imp[i] += g
                charge[i] += g * e_chg

        soc[i] = s

    return {
        "soc": soc, "soc_min": soc_min, "soc_max": soc_max, "utile": utile,
        "import": imp, "export": exp, "ecrete": ecrete_ac,
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


def simulate(cfg: dict, meteo: dict) -> dict:
    pv_dc, par_champ, diag_champs = compute_production(cfg, meteo)
    load, detail, infos = build_load(cfg, meteo)
    d = dispatch(pv_dc, load, meteo, cfg["systeme"])

    ny = meteo["n_years"]
    conso = load                                  # consommation des usages
    besoin = d["besoin_total"]                    # usages + veille onduleurs
    imp = d["import"]
    autoconso = besoin - imp

    m = {
        "production_dc": _mois(pv_dc, meteo),
        "consommation": _mois(conso, meteo),
        "veille": _mois(d["veille"], meteo),
        "besoin": _mois(besoin, meteo),
        "import": _mois(imp, meteo),
        "autoconso": _mois(autoconso, meteo),
        "export": _mois(d["export"], meteo),
        "ecrete": _mois(d["ecrete"], meteo),
        "charge": _mois(d["charge"], meteo),
        "decharge": _mois(d["decharge"], meteo),
        "non_servi": _mois(d["non_servi"], meteo),
    }
    m["autonomie"] = 1.0 - m["import"] / np.maximum(m["besoin"], 1e-9)
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
        "date": meteo["days"],
        "mois": meteo["month"][np.unique(meteo["day_index"], return_index=True)[1]],
        "soc_min": np.minimum.reduceat(
            d["soc"], np.unique(meteo["day_index"], return_index=True)[1]),
    }
    jour["autonomie"] = 1.0 - jour["import"] / np.maximum(jour["besoin"], 1e-9)

    kwc = total_kwc(cfg)
    conso_an = conso.sum() / ny
    besoin_an = besoin.sum() / ny
    imp_an = imp.sum() / ny

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
            "export_an": float(d["export"].sum() / ny),
            "ecrete_an": float(d["ecrete"].sum() / ny),
            "non_servi_an": float(d["non_servi"].sum() / ny),
            "autonomie": float(1.0 - imp_an / max(besoin_an, 1e-9)),
            "taux_autoconso": float((besoin_an - imp_an) /
                                    max(pv_dc.sum() / ny, 1e-9)),
            "cycles_batterie_an": float(d["decharge"].sum() / ny /
                                        max(d["utile"], 1e-9)),
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
    kwc = total_kwc(cfg)
    batt = float(cfg["systeme"]["batt_kwh_nominal"])
    packs = max(int(round(batt / 16.07)), 0)
    return {
        "fixe": 1.0,
        "panneaux": float(total_panneaux(cfg)),
        "kwc": kwc,
        "onduleurs": float(cfg["systeme"]["n_onduleurs"]),
        "batt_kwh": batt,
        "packs": float(packs),
        "cellules": float(packs * 16),
        "m2_panneaux": surface_m2(cfg),
    }


def compute_bom(cfg: dict):
    q = bom_quantities(cfg)
    lignes, total = [], 0.0
    for l in cfg["bom"]:
        auto = l.get("auto", "fixe")
        qte = float(l.get("qte", 0)) if auto == "fixe" else q.get(auto, 0.0)
        if auto == "fixe" and float(l.get("qte", 0)) == 0:
            qte = 1.0
        montant = qte * float(l.get("pu", 0.0))
        lignes.append({**l, "qte_calc": qte, "montant": montant})
        total += montant
    return lignes, total


def economics(cfg: dict, res: dict) -> dict:
    e = cfg["economie"]
    k = res["kpi"]
    _, capex = compute_bom(cfg)

    bois_kwh = 0.0
    for nom, info in res["infos_postes"].items():
        bois_kwh += info.get("th_bois_kwh_an", 0.0)
    steres = bois_kwh / max(float(e.get("pci_bois_kwh_stere", 1500.0)), 1.0)
    cout_bois = steres * float(e.get("cout_bois_stere", 0.0))

    achat = k["import_an"] * float(e["prix_kwh_achat"])
    revente = k["export_an"] * float(e.get("prix_kwh_revente", 0.0))
    cout_avec = achat + float(e["abonnement_an"]) + cout_bois - revente
    cout_sans = k["besoin_an"] * float(e["prix_kwh_achat"]) + float(e["abonnement_an"])
    facture_actuelle = float(e.get("facture_actuelle_an", 0.0))

    econ_vs_sans = cout_sans - cout_avec
    econ_vs_actuel = facture_actuelle - cout_avec

    # Retour actualise avec inflation energie, interpole a l'interieur de
    # l'annee de bascule (pas seulement l'annee entiere qui suit)
    infl = float(e.get("inflation_energie", 0.0))
    horizon = int(e.get("duree_analyse_ans", 25))
    cum, cum2 = -capex, -capex
    retour_vs_sans = 0.0 if cum >= 0 else None
    retour_vs_actuel = 0.0 if cum2 >= 0 else None
    for an in range(1, horizon + 1):
        gain1 = econ_vs_sans * (1 + infl) ** (an - 1)
        gain2 = econ_vs_actuel * (1 + infl) ** (an - 1)
        avant, avant2 = cum, cum2
        cum += gain1
        cum2 += gain2
        if retour_vs_sans is None and cum >= 0:
            retour_vs_sans = (an - 1) + (-avant / gain1 if gain1 > 1e-9 else 0.0)
        if retour_vs_actuel is None and cum2 >= 0:
            retour_vs_actuel = (an - 1) + (-avant2 / gain2 if gain2 > 1e-9 else 0.0)

    prod = max(k["production_an"], 1e-9)
    utilise = k["besoin_an"] - k["import_an"]
    lcoe = capex / max(utilise * horizon, 1e-9)

    return {
        "capex": capex,
        "cout_par_wc": capex / max(k["kwc"] * 1000.0, 1e-9),
        "cout_annuel_avec_pv": cout_avec,
        "cout_annuel_sans_pv": cout_sans,
        "facture_actuelle": facture_actuelle,
        "economie_vs_sans_pv": econ_vs_sans,
        "economie_vs_actuel": econ_vs_actuel,
        "cout_bois": cout_bois, "steres": steres,
        "retour_ans_vs_sans_pv": retour_vs_sans,
        "retour_ans_vs_actuel": retour_vs_actuel,
        "gain_cumule_horizon": cum,
        "lcoe_kwh_utile": lcoe,
        "kwh_perdus_an": k["ecrete_an"],
        "valeur_perdue_an": k["ecrete_an"] * float(e["prix_kwh_achat"]),
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
    if cyc > 320:
        a.append(("info", f"{cyc:.0f} cycles pleins par an : duree de vie estimee "
                          f"{6000 / max(cyc, 1):.0f} ans."))
    for nom, info in res["infos_postes"].items():
        if info.get("heures_saturation_pac", 0) > 50:
            a.append(("attention",
                      f"{nom} : {info['heures_saturation_pac']:.0f} h/an ou la PAC sature, "
                      f"{info.get('th_appoint_kwh_an', 0):.0f} kWh repris par l'appoint "
                      f"electrique direct."))
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

    pack = 16.07
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
        "capex": base["eco"]["capex"],
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
        cout = (r["eco"]["capex"] - ref["capex"]) if cout_impose is None \
            else float(cout_impose)
        gain = ref["cout_an"] - r["eco"]["cout_annuel_avec_pv"]
        retour = cout / gain if (cout > 0 and gain > 0) else None
        actions.append({
            "nom": nom, "categorie": cat, "detail": detail,
            "gain_an": gain,
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
        lo, hi = 0.05, 4.0
        base = m["consommation"][k]
        njours = max(m["jours"][k], 1e-9)
        if base <= 0:
            out.append((0.0, 0.0))
            continue
        # dichotomie sur un facteur d'echelle de la consommation du mois
        for _ in range(18):
            f = (lo + hi) / 2.0
            aut = _autonomie_mois_scaled(cfg, meteo, res, k, f)
            if aut >= cible:
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


def compute_marginal_rentability(out: list[dict], key="economie_vs_actuel") -> list[float]:
    """Retourne le taux de rentabilite marginale par unite supplementaire."""
    scores = [0.0]
    for i in range(1, len(out)):
        prev = out[i - 1]
        cur = out[i]
        delta_capex = max(float(cur.get("capex", 0.0)) - float(prev.get("capex", 0.0)), 0.0)
        delta_gain = float(cur.get(key, 0.0)) - float(prev.get(key, 0.0))
        if delta_capex <= 0:
            scores.append(0.0)
        else:
            scores.append(max(delta_gain / delta_capex, 0.0))
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
                    "capex": r["eco"]["capex"],
                    "retour": r["eco"]["retour_ans_vs_actuel"],
                    "economie_vs_actuel": r["eco"]["economie_vs_actuel"],
                    "production_mensuel": r["mensuel"]["production_dc"].tolist(),
                    "autonomie_mensuel": r["mensuel"]["autonomie"].tolist(),
                    "import_mensuel": r["mensuel"]["import"].tolist()})
        if progress:
            progress(int(100 * (i + 1) / len(valeurs)), f"{variable} = {v}")
    for i, item in enumerate(out):
        item["rentabilite_marginale"] = 0.0 if i == 0 else 0.0
    if out:
        scores = compute_marginal_rentability(out)
        for i, score in enumerate(scores):
            out[i]["rentabilite_marginale"] = score
    return out

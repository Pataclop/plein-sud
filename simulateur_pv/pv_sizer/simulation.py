"""
Moteur de simulation : production des champs, gestion onduleur / batterie
au pas horaire, agregations jour et mois, bilan economique.
"""
from __future__ import annotations
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
        "dispatch": d, "diag_champs": diag_champs, "infos_postes": infos,
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

    # Retour actualise avec inflation energie
    infl = float(e.get("inflation_energie", 0.0))
    horizon = int(e.get("duree_analyse_ans", 25))
    cum, retour_vs_sans, retour_vs_actuel = -capex, None, None
    cum2 = -capex
    for an in range(1, horizon + 1):
        cum += econ_vs_sans * (1 + infl) ** (an - 1)
        cum2 += econ_vs_actuel * (1 + infl) ** (an - 1)
        if retour_vs_sans is None and cum >= 0:
            retour_vs_sans = an
        if retour_vs_actuel is None and cum2 >= 0:
            retour_vs_actuel = an

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
    a = []
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
# Budget energetique : combien puis-je consommer ?
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


# --------------------------------------------------------------------------
# Balayages parametriques
# --------------------------------------------------------------------------
def sweep(cfg: dict, meteo: dict, variable: str, valeurs, progress=None):
    """Balaye un parametre et retourne autonomie / production / capex / import."""
    from copy import deepcopy
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
                    "retour": r["eco"]["retour_ans_vs_actuel"]})
        if progress:
            progress(int(100 * (i + 1) / len(valeurs)), f"{variable} = {v}")
    return out

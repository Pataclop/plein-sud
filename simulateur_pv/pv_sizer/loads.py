"""
Construction des profils de consommation horaires, poste par poste.

Chaque poste renvoie un vecteur de la meme longueur que la serie meteo,
en kWh par heure. Les postes thermiques (chauffage, spa) sont pilotes par
la temperature exterieure reelle de la serie, pas par une saisonnalite
forfaitaire : c'est ce qui permet de voir l'effet d'un hiver 2018 doux
contre un hiver 2021 froid.
"""
from __future__ import annotations
import numpy as np
from .config import DAILY_SHAPES


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------
def shape_vector(meteo: dict, profil: str) -> np.ndarray:
    w = np.array(DAILY_SHAPES.get(profil, DAILY_SHAPES["constant"]), dtype=float)
    if w.sum() <= 0:
        w = np.ones(24)
    return w[meteo["hour"]]


def scale_to_annual(arr: np.ndarray, kwh_an: float, meteo: dict) -> np.ndarray:
    total = arr.sum()
    if total <= 0:
        return np.zeros_like(arr)
    return arr * (kwh_an * meteo["n_years"] / total)


def daily_redistribute(daily_energy: np.ndarray, meteo: dict, profil: str) -> np.ndarray:
    """Repartit une energie journaliere sur les heures selon un profil,
    en conservant exactement le total de chaque journee."""
    w = shape_vector(meteo, profil)
    di = meteo["day_index"]
    wsum = np.bincount(di, weights=w, minlength=meteo["n_days"])
    wsum = np.where(wsum <= 0, 1.0, wsum)
    return daily_energy[di] * w / wsum[di]


def moving_average(x: np.ndarray, hours: int) -> np.ndarray:
    hours = int(max(1, hours))
    if hours <= 1:
        return x
    k = np.ones(hours) / hours
    return np.convolve(x, k, mode="same")


def monthly_temperature_weights(meteo: dict, amplitude: float) -> np.ndarray:
    """Poids mensuel croissant quand il fait froid (eau froide d'alimentation)."""
    tm = np.array([meteo["T2m"][meteo["month"] == k + 1].mean() for k in range(12)])
    t_mean = tm.mean()
    span = max(tm.max() - tm.min(), 1e-6)
    return 1.0 + amplitude * (t_mean - tm) / (span / 2.0) * 0.5


# --------------------------------------------------------------------------
# Postes
# --------------------------------------------------------------------------
def _talon(p, meteo):
    base = np.full(meteo["n"], float(p["puissance_w"]) / 1000.0)
    hiver = np.isin(meteo["month"], [12, 1, 2])
    base = base * np.where(hiver, 1.0 + float(p.get("variation_hiver", 0.0)), 1.0)
    return base, {}


def _generique(p, meteo):
    w = shape_vector(meteo, p.get("profil", "occupation"))
    pm = np.array(p.get("poids_mensuels", [1.0] * 12), dtype=float)
    w = w * pm[meteo["month"] - 1]
    return scale_to_annual(w, float(p["kwh_an"]), meteo), {}


def _chauffage(p, meteo):
    t = moving_average(meteo["T2m"], p.get("inertie_h", 6))
    besoin = np.maximum(float(p["t_base"]) - t, 0.0)

    th = scale_to_annual(besoin, float(p["besoin_th_kwh_an"]), meteo)

    # Appoint bois quand il fait froid
    part_bois = float(p.get("part_bois", 0.0))
    froid = t < float(p.get("t_bois", 5.0))
    th_bois = np.where(froid, th * part_bois, 0.0)
    th_pac_demande = th - th_bois

    # Capacite de la PAC, declassee par temps froid
    p_nom = float(p.get("p_pac_kw_th", 1e6))
    decl = float(p.get("declassement_froid", 0.0))
    ratio = np.clip((7.0 - t) / 14.0, 0.0, 1.6)
    capacite = np.maximum(p_nom * (1.0 - decl * ratio), 0.0)

    th_pac = np.minimum(th_pac_demande, capacite)
    th_appoint = th_pac_demande - th_pac

    cop = np.clip(float(p["cop_a"]) + float(p["cop_b"]) * t, 1.0, float(p["cop_max"]))
    elec = th_pac / cop + th_appoint  # appoint = effet Joule, COP 1

    ny = meteo["n_years"]
    info = {
        "th_total_kwh_an": float(th.sum() / ny),
        "th_bois_kwh_an": float(th_bois.sum() / ny),
        "th_pac_kwh_an": float(th_pac.sum() / ny),
        "th_appoint_kwh_an": float(th_appoint.sum() / ny),
        "scop_realise": float(th_pac.sum() / max((th_pac / cop).sum(), 1e-9)),
        "heures_saturation_pac": float((th_appoint > 1e-6).sum() / ny),
        "p_max_appelee_th_kw": float(th.max()),
    }
    return elec, info


def _ecs(p, meteo):
    w = shape_vector(meteo, p.get("profil", "solaire"))
    pm = monthly_temperature_weights(meteo, float(p.get("majoration_hiver", 0.2)))
    w = w * pm[meteo["month"] - 1]
    th = scale_to_annual(w, float(p["besoin_th_kwh_an"]), meteo)
    cop = max(float(p.get("cop", 1.0)), 0.1)
    return th / cop, {"th_kwh_an": float(p["besoin_th_kwh_an"]), "cop": cop}


def _piscine(p, meteo):
    heures = np.array(p.get("heures_par_mois", [4] * 12), dtype=float)
    pk = float(p["p_pompe_w"]) / 1000.0
    daily = heures[meteo["month"] - 1] * pk          # kWh/jour, par heure de la serie
    di = meteo["day_index"]
    daily_e = np.bincount(di, weights=daily, minlength=meteo["n_days"]) / \
        np.maximum(np.bincount(di, minlength=meteo["n_days"]), 1)
    pompe = daily_redistribute(daily_e, meteo, p.get("profil", "solaire"))

    pac = np.zeros(meteo["n"])
    kwh_pac = float(p.get("pac_piscine_kwh_an", 0.0))
    if kwh_pac > 0:
        actif = (heures[meteo["month"] - 1] > 0).astype(float)
        pac = scale_to_annual(actif * shape_vector(meteo, "solaire"), kwh_pac, meteo)
    ny = meteo["n_years"]
    return pompe + pac, {"filtration_kwh_an": float(pompe.sum() / ny),
                         "pac_kwh_an": float(pac.sum() / ny)}


def _spa(p, meteo):
    ua = float(p["ua_w_par_k"]) / 1000.0
    dt = np.maximum(float(p["t_consigne"]) - meteo["T2m"], 0.0)
    pertes_th = ua * dt                                    # kWh par heure
    di = meteo["day_index"]
    daily = np.bincount(di, weights=pertes_th, minlength=meteo["n_days"])
    chauffe = daily_redistribute(daily, meteo, p.get("profil", "constant"))
    chauffe = chauffe / max(float(p.get("cop", 1.0)), 0.1)
    pompes = scale_to_annual(shape_vector(meteo, "occupation"),
                             float(p.get("pompes_kwh_an", 0.0)), meteo)
    ny = meteo["n_years"]
    return chauffe + pompes, {"pertes_kwh_an": float(chauffe.sum() / ny),
                              "pompes_kwh_an": float(pompes.sum() / ny)}


def _vehicule(p, meteo):
    kwh = float(p["km_an"]) * float(p["conso_kwh_100km"]) / 100.0
    if kwh <= 0:
        return np.zeros(meteo["n"]), {}
    w = shape_vector(meteo, p.get("profil", "solaire_large"))
    jours = int(p.get("jours_par_semaine", 4))
    dow = (meteo["dt_loc"].astype("datetime64[D]").astype(int) + 4) % 7  # 0 = lundi
    w = w * (dow < jours).astype(float)
    return scale_to_annual(w, kwh, meteo), {"kwh_an": kwh}


BUILDERS = {"talon": _talon, "generique": _generique, "chauffage": _chauffage,
            "ecs": _ecs, "piscine": _piscine, "spa": _spa, "vehicule": _vehicule}


def build_load(cfg: dict, meteo: dict):
    """Retourne (total_kwh_par_heure, detail_par_poste, infos)."""
    total = np.zeros(meteo["n"])
    detail, infos = {}, {}
    for poste in cfg["postes"]:
        if not poste.get("actif", True):
            continue
        fn = BUILDERS.get(poste.get("kind", "generique"))
        if fn is None:
            continue
        arr, info = fn(poste["params"], meteo)
        arr = np.nan_to_num(np.maximum(arr, 0.0))
        detail[poste["nom"]] = arr
        if info:
            infos[poste["nom"]] = info
        total += arr
    return total, detail, infos

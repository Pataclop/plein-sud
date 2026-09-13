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
    """Met un vecteur de poids a l'echelle d'une energie annuelle.

    Si la somme des poids est nulle (12 poids mensuels a zero, profil vide),
    l'energie demandee ne peut aller nulle part : on la repartit uniformement
    plutot que de la faire disparaitre en silence. Un poste saisi a
    2 500 kWh/an qui ressortait a 0 kWh/an etait invisible dans le bilan.
    """
    total = float(np.nansum(arr))
    if not np.isfinite(total) or total <= 0:
        if kwh_an <= 0:
            return np.zeros_like(arr)
        return np.full_like(arr, kwh_an * meteo["n_years"] / max(len(arr), 1))
    return arr * (kwh_an * meteo["n_years"] / total)


def daily_redistribute(daily_energy: np.ndarray, meteo: dict, profil: str) -> np.ndarray:
    """Repartit une energie journaliere sur les heures selon un profil,
    en conservant exactement le total de chaque journee."""
    w = shape_vector(meteo, profil)
    di = meteo["day_index"]
    wsum = np.bincount(di, weights=w, minlength=meteo["n_days"])
    wsum = np.where(wsum <= 0, 1.0, wsum)
    return daily_energy[di] * w / wsum[di]


def repartir_sous_plafond(daily_energy: np.ndarray, meteo: dict, profil: str,
                          p_max_kw: float):
    """Comme daily_redistribute, mais sans jamais depasser une puissance.

    Un appareil ne peut pas debiter plus que sa plaque signaletique : une
    pompe de piscine de 750 W a laquelle on demande 12 h de filtration par
    jour sur un profil qui ne compte que 9 heures ne tire pas 1,1 kW, elle
    tourne simplement plus longtemps.

    L'energie qui ne tient pas dans les heures du profil deborde sur les
    heures voisines de la meme journee, par ordre de poids decroissant.
    Retourne (kwh_par_heure, heures_saturees_par_an).
    """
    arr = daily_redistribute(daily_energy, meteo, profil)
    if p_max_kw <= 0:
        return arr, 0.0
    di = meteo["day_index"]
    n_jours = meteo["n_days"]
    satures = 0
    for _ in range(6):
        exces = np.maximum(arr - p_max_kw, 0.0)
        if exces.sum() <= 1e-9:
            break
        satures = int((exces > 1e-9).sum())
        arr = np.minimum(arr, p_max_kw)
        # ce qui deborde est repris sur la place restante de la meme journee
        reste = np.bincount(di, weights=exces, minlength=n_jours)
        place = np.maximum(p_max_kw - arr, 0.0)
        place_jour = np.bincount(di, weights=place, minlength=n_jours)
        ratio = np.where(place_jour > 1e-9, reste / np.maximum(place_jour, 1e-9), 0.0)
        arr = arr + place * np.minimum(ratio[di], 1.0)
    return arr, satures / max(meteo["n_years"], 1e-9)


def moving_average(x: np.ndarray, hours: int) -> np.ndarray:
    """Moyenne glissante centree, bords prolonges par la valeur extreme.

    np.convolve(mode="same") complete implicitement par des ZEROS : la
    temperature lissee des premieres heures de la serie etait tiree vers
    0 C (8,1 C reels affiches 3,9 C avec 6 h d'inertie), ce qui fabriquait
    un besoin de chauffage qui n'existe pas au tout debut de la serie.
    """
    hours = int(max(1, hours))
    if hours <= 1:
        return x
    k = np.ones(hours) / hours
    marge = hours // 2
    etendu = np.pad(x, (marge, hours - 1 - marge), mode="edge")
    return np.convolve(etendu, k, mode="valid")


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


#: consigne a laquelle le besoin thermique annuel est cense avoir ete etabli.
#: Baisser la consigne sous cette reference reduit le besoin, la monter
#: l'augmente, dans le rapport des degres-heures reellement calcules.
T_CONSIGNE_REF = 19.5


def _chauffage(p, meteo):
    t = moving_average(meteo["T2m"], p.get("inertie_h", 6))
    t_base = float(p["t_base"])
    besoin_ref = np.maximum(t_base - t, 0.0)

    # La consigne agit en decalant la temperature de non-chauffage : monter
    # le thermostat d'un degre, c'est chauffer un degre plus longtemps et
    # un degre plus fort. Le besoin saisi correspond a T_CONSIGNE_REF ; on
    # applique ensuite le rapport des degres-heures. A consigne de reference
    # le facteur vaut exactement 1 et rien ne change.
    ecart = float(p.get("t_consigne", T_CONSIGNE_REF)) - T_CONSIGNE_REF
    besoin = np.maximum(t_base + ecart - t, 0.0) if ecart else besoin_ref
    ref = float(besoin_ref.sum())
    facteur = float(besoin.sum()) / ref if ref > 0 else 1.0

    th = scale_to_annual(besoin, float(p["besoin_th_kwh_an"]) * facteur, meteo)

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
        "facteur_consigne": facteur,
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
    pompe, h_sat = repartir_sous_plafond(daily_e, meteo, p.get("profil", "solaire"), pk)

    pac = np.zeros(meteo["n"])
    kwh_pac = float(p.get("pac_piscine_kwh_an", 0.0))
    if kwh_pac > 0:
        actif = (heures[meteo["month"] - 1] > 0).astype(float)
        pac = scale_to_annual(actif * shape_vector(meteo, "solaire"), kwh_pac, meteo)
    ny = meteo["n_years"]
    return pompe + pac, {"filtration_kwh_an": float(pompe.sum() / ny),
                         "pac_kwh_an": float(pac.sum() / ny),
                         "heures_debordement_filtration": h_sat,
                         "p_max_pompe_kw": float(pompe.max())}


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
    # datetime64[D] compte les jours depuis le 1er janvier 1970, un JEUDI.
    # Pour que lundi vaille 0 il faut donc ajouter 3, pas 4 : l'ancien
    # decalage faisait recharger le dimanche et sautait le jeudi.
    dow = (meteo["dt_loc"].astype("datetime64[D]").astype(int) + 3) % 7  # 0 = lundi
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
        kind = poste.get("kind", "generique")
        fn = BUILDERS.get(kind)
        if fn is None:
            # ne pas escamoter un poste que l'interface continue d'afficher :
            # on le rend visible dans le bilan et dans les alertes.
            detail[poste["nom"]] = np.zeros(meteo["n"])
            infos[poste["nom"]] = {"type_inconnu": kind}
            continue
        arr, info = fn(poste["params"], meteo)
        arr = np.nan_to_num(np.maximum(arr, 0.0))
        detail[poste["nom"]] = arr
        if info:
            infos[poste["nom"]] = info
        total += arr
    return total, detail, infos

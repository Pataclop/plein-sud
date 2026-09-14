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
from . import config as C
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


# --------------------------------------------------------------------------
# Enveloppe du batiment : du descriptif de la maison au besoin de chaleur
#
# Le modele est celui des degres-heures, applique heure par heure :
#
#     besoin(h) = UA x (T_interieure - T_exterieure(h)) - apports_gratuits(h)
#
# borne a zero (on ne refroidit pas). UA est le coefficient de deperdition du
# logement, en watts perdus par degre d'ecart ; les apports gratuits sont la
# chaleur des occupants et des appareils, plus le soleil qui entre par les
# vitrages, calcule sur la serie meteo reelle.
#
# Deux consequences pedagogiques, visibles dans les resultats :
#   - la temperature de non-chauffage n'est plus une hypothese, c'est un
#     resultat : elle vaut T_interieure - apports / UA, donc une maison bien
#     isolee arrete de chauffer beaucoup plus tot ;
#   - une journee froide et ensoleillee coute moins cher qu'une journee douce
#     et grise, ce qu'un modele en degres-jours ne sait pas montrer.
# --------------------------------------------------------------------------

#: 0,34 Wh par m3 d'air et par degre : masse volumique x capacite thermique
#: de l'air, la constante usuelle des calculs de ventilation.
C_AIR_WH_M3_K = 0.34

#: Quantile de temperature retenu comme temperature exterieure de base pour
#: le dimensionnement de la PAC (environ 9 heures par an en dessous).
QUANTILE_T_BASE = 0.001

_POA_CACHE: dict = {}


def _poa_verticale(meteo: dict, azimut: float) -> np.ndarray:
    """Rayonnement recu par un vitrage vertical oriente selon azimut (W/m2).

    Le calcul est le meme que pour un panneau pose a 90 degres. Il est mis en
    cache : l'interface le redemande a chaque frappe dans un champ.
    """
    from .solar import poa_irradiance
    cle = (meteo.get("path"), int(meteo["n"]), round(float(azimut), 1))
    poa = _POA_CACHE.get(cle)
    if poa is None:
        if len(_POA_CACHE) > 40:
            _POA_CACHE.clear()
        poa, _, _ = poa_irradiance(
            meteo["Gbh"], meteo["Gdh"], meteo["sun_el"], meteo["sun_az"],
            90.0, float(azimut), meteo["E0"])
        _POA_CACHE[cle] = poa
    return poa


def deperditions(p: dict) -> dict:
    """Geometrie et coefficient de deperdition UA (W/K) d'apres le descriptif.

    Retourne le detail par paroi : c'est ce detail qui dit ou l'isolation
    rapporterait le plus, et c'est lui que l'interface affiche.
    """
    s_hab = max(float(p.get("surface_habitable_m2", 0.0)), 0.0)
    niveaux = max(int(round(float(p.get("n_niveaux", 1)))), 1)
    h_plaf = max(float(p.get("hauteur_sous_plafond_m", 2.5)), 0.5)
    allong = max(float(p.get("allongement", 1.5)), 1.0)

    # Emprise au sol assimilee a un rectangle de meme surface et de meme
    # allongement : c'est ce qui permet de deduire un perimetre, donc une
    # surface de murs, de la seule surface habitable.
    s_sol = s_hab / niveaux
    largeur = (s_sol / allong) ** 0.5
    perimetre = 2.0 * (largeur * allong + largeur)

    mitoyen = min(max(float(p.get("part_murs_mitoyens", 0.0)), 0.0), 0.95)
    s_murs_ext = perimetre * h_plaf * niveaux * (1.0 - mitoyen)
    s_vitree = s_hab * max(float(p.get("part_vitree_pct", 0.0)), 0.0) / 100.0
    s_vitree = min(s_vitree, s_murs_ext)          # pas plus de vitrage que de mur
    s_murs = max(s_murs_ext - s_vitree, 0.0)

    u_mur = C.u_valeur(C.U_MURS, p.get("iso_murs"), 0.70)
    u_toit = C.u_valeur(C.U_TOITURE, p.get("iso_toiture"), 0.35)
    u_plan = C.u_valeur(C.U_PLANCHER, p.get("iso_plancher"), 0.55)
    u_vitr = C.u_valeur(C.U_VITRAGES, p.get("iso_vitrages"), 3.00)
    b_plan = min(max(float(p.get("b_plancher", 0.8)), 0.0), 1.0)

    ua_murs = s_murs * u_mur
    ua_toiture = s_sol * u_toit
    ua_plancher = s_sol * u_plan * b_plan
    ua_vitrages = s_vitree * u_vitr
    ua_parois = ua_murs + ua_toiture + ua_plancher + ua_vitrages
    ua_ponts = ua_parois * max(float(p.get("ponts_thermiques_pct", 0.0)), 0.0) / 100.0

    volume = s_hab * h_plaf
    taux = max(float(p.get("renouv_air_vol_h", 0.0)), 0.0)
    rend = min(max(float(p.get("rendement_echangeur", 0.0)), 0.0), 0.95)
    ua_ventilation = C_AIR_WH_M3_K * taux * volume * (1.0 - rend)

    correctif = max(float(p.get("correctif_deperditions", 1.0)), 0.0)
    ua_total = (ua_parois + ua_ponts + ua_ventilation) * correctif

    return {
        "s_habitable": s_hab, "s_sol": s_sol, "s_murs": s_murs,
        "s_vitree": s_vitree, "perimetre": perimetre, "volume": volume,
        "u_murs": u_mur, "u_toiture": u_toit, "u_plancher": u_plan,
        "u_vitrages": u_vitr,
        "ua_murs": ua_murs * correctif, "ua_toiture": ua_toiture * correctif,
        "ua_plancher": ua_plancher * correctif,
        "ua_vitrages": ua_vitrages * correctif,
        "ua_ponts": ua_ponts * correctif,
        "ua_ventilation": ua_ventilation * correctif,
        "ua_total": ua_total, "correctif": correctif,
    }


def apports_solaires_w(p: dict, meteo: dict, env: dict) -> np.ndarray:
    """Puissance solaire (W) entrant par les vitrages, heure par heure."""
    parts = C.ORIENTATIONS_VITRAGES.get(
        p.get("orientation_vitrages"),
        C.ORIENTATIONS_VITRAGES["Equilibre sud, est et ouest"])
    poa = np.zeros(meteo["n"])
    for azimut, part in parts.items():
        if part > 0:
            poa = poa + part * _poa_verticale(meteo, azimut)
    transmission = (min(max(float(p.get("g_vitrage", 0.55)), 0.0), 1.0)
                    * min(max(float(p.get("facteur_masques_solaires", 0.45)), 0.0), 1.0))
    return env["s_vitree"] * transmission * poa


def besoin_enveloppe(p: dict, meteo: dict, t_lissee=None):
    """Besoin de chaleur horaire (kWh th) deduit du descriptif du logement.

    Retourne (besoin_par_heure, info). info contient le detail des
    deperditions, des apports, la temperature de non-chauffage equivalente et
    la puissance de dimensionnement : tout ce que l'interface affiche.
    """
    env = deperditions(p)
    inertie = p.get("inertie_h", 6)
    t = moving_average(meteo["T2m"], inertie) if t_lissee is None else t_lissee
    t_cons = float(p.get("t_consigne", T_CONSIGNE_REF))

    pertes_w = env["ua_total"] * (t_cons - t)
    internes_w = float(p.get("apports_internes_w_m2", 0.0)) * env["s_habitable"]
    # Le soleil qui entre a 14 h chauffe encore a 18 h : les apports solaires
    # suivent la meme inertie que la temperature exterieure.
    solaires_w = moving_average(apports_solaires_w(p, meteo, env), inertie)

    besoin_w = np.maximum(pertes_w - internes_w - solaires_w, 0.0)
    th = besoin_w / 1000.0

    ny = max(meteo["n_years"], 1e-9)
    chauffe = besoin_w > 0
    # Temperature de non-chauffage equivalente : celle a laquelle les apports
    # gratuits compensent exactement les deperditions, pendant la saison de
    # chauffe.
    apports_moy = float((internes_w + solaires_w)[chauffe].mean()) if chauffe.any() \
        else float(internes_w + solaires_w.mean())
    t_base_eq = t_cons - apports_moy / max(env["ua_total"], 1e-9)

    t_ext_base = float(np.quantile(meteo["T2m"], QUANTILE_T_BASE))
    info = {
        "ua_w_par_k": env["ua_total"],
        "ua_murs": env["ua_murs"], "ua_toiture": env["ua_toiture"],
        "ua_plancher": env["ua_plancher"], "ua_vitrages": env["ua_vitrages"],
        "ua_ponts": env["ua_ponts"], "ua_ventilation": env["ua_ventilation"],
        "s_murs": env["s_murs"], "s_sol": env["s_sol"],
        "s_vitree": env["s_vitree"], "volume": env["volume"],
        "s_habitable": env["s_habitable"],
        "besoin_calcule_kwh_an": float(th.sum() / ny),
        "besoin_par_m2": float(th.sum() / ny / max(env["s_habitable"], 1e-9)),
        "apports_internes_kwh_an": float(internes_w * meteo["n"] / 1000.0 / ny),
        "apports_solaires_kwh_an": float(solaires_w.sum() / 1000.0 / ny),
        "apports_utilises_kwh_an": float(
            np.minimum(internes_w + solaires_w, np.maximum(pertes_w, 0.0)).sum()
            / 1000.0 / ny),
        "t_base_equivalente": t_base_eq,
        "t_ext_base": t_ext_base,
        "p_dimensionnement_kw": env["ua_total"] * (t_cons - t_ext_base) / 1000.0,
        "heures_de_chauffe": float(chauffe.sum() / ny),
    }
    return th, info


def _besoin_saisi(p, meteo, t):
    """Besoin horaire deduit d'un total annuel connu, reparti sur les
    degres-heures reels de la serie meteo."""
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
    return th, {"facteur_consigne": facteur}


def _chauffage(p, meteo):
    t = moving_average(meteo["T2m"], p.get("inertie_h", 6))

    if str(p.get("mode_besoin", C.MODE_BESOIN_SAISI)) == C.MODE_BESOIN_ENVELOPPE:
        th, info = besoin_enveloppe(p, meteo, t)
    else:
        th, info = _besoin_saisi(p, meteo, t)

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
    info.update({
        "th_total_kwh_an": float(th.sum() / ny),
        "th_bois_kwh_an": float(th_bois.sum() / ny),
        "th_pac_kwh_an": float(th_pac.sum() / ny),
        "th_appoint_kwh_an": float(th_appoint.sum() / ny),
        "scop_realise": float(th_pac.sum() / max((th_pac / cop).sum(), 1e-9)),
        "heures_saturation_pac": float((th_appoint > 1e-6).sum() / ny),
        "p_max_appelee_th_kw": float(th.max()),
    })
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

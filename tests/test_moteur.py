"""Non-regression sur les defauts du moteur corriges apres audit.

Chacun de ces tests reproduit un chiffre qui etait faux SANS AUCUN SIGNE :
c'est la categorie de defaut la plus dangereuse pour un dimensionnement.
Ils tournent sur la serie meteo reelle livree avec le depot.
"""
import copy

import numpy as np
import pytest

from pv_sizer import config as C
from pv_sizer import loads as L
from pv_sizer import simulation as S
from pv_sizer.meteo import ensure_meteo


@pytest.fixture(scope="module")
def meteo():
    return ensure_meteo(C.default_config()["site"], allow_download=False)


@pytest.fixture(scope="module")
def cfg():
    return C.default_config()


# --------------------------------------------------------------------------
# loads.py
# --------------------------------------------------------------------------
def test_lundi_vaut_zero_pour_la_recharge_du_vehicule(meteo):
    """Le decalage etait de +4 depuis le 1er janvier 1970, un jeudi : le code
    0 tombait sur dimanche. Un vehicule cense charger du lundi au jeudi
    chargeait du dimanche au mercredi."""
    dow = (meteo["dt_loc"].astype("datetime64[D]").astype(int) + 3) % 7
    premier_lundi = meteo["dt_loc"][int(np.argmax(dow == 0))]
    # 2018-01-01 etait un lundi
    assert str(premier_lundi.astype("datetime64[D]")) == "2018-01-01"


def test_la_pompe_de_piscine_ne_depasse_pas_sa_puissance(cfg, meteo):
    poste = next(p for p in cfg["postes"] if p["kind"] == "piscine")
    arr, info = L._piscine(poste["params"], meteo)
    p_max = float(poste["params"]["p_pompe_w"]) / 1000.0
    assert arr.max() <= p_max + 1e-6
    # l'energie de filtration est conservee malgre le plafonnement : elle
    # deborde sur les heures voisines, elle ne disparait pas
    heures = np.array(poste["params"]["heures_par_mois"], dtype=float)
    jours_par_mois = np.array([31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
    attendu = float((heures * jours_par_mois).sum() * p_max)
    assert info["filtration_kwh_an"] == pytest.approx(attendu, rel=0.02)
    assert info["heures_debordement_filtration"] > 0


def test_le_lissage_dinertie_ne_tire_pas_les_bords_vers_zero(meteo):
    lisse = L.moving_average(meteo["T2m"], 6)
    assert abs(lisse[0] - meteo["T2m"][:4].mean()) < 1.5
    assert lisse[0] > 6.0          # valait 3,9 C pour 8,1 C reels


def test_la_consigne_de_chauffage_a_un_effet(cfg, meteo):
    ch = next(p for p in cfg["postes"] if p["kind"] == "chauffage")
    base = dict(ch["params"])
    ref, _ = L._chauffage({**base, "t_consigne": L.T_CONSIGNE_REF}, meteo)
    froid, _ = L._chauffage({**base, "t_consigne": L.T_CONSIGNE_REF - 1.0}, meteo)
    chaud, _ = L._chauffage({**base, "t_consigne": L.T_CONSIGNE_REF + 1.0}, meteo)
    assert froid.sum() < ref.sum() < chaud.sum()
    # a la consigne de reference, rien ne change pour les configurations
    # deja enregistrees
    assert abs(ref.sum() - L._chauffage(base, meteo)[0].sum()) < 1e-6


def test_un_poste_sans_poids_ne_disparait_pas(meteo):
    """2 500 kWh/an saisis avec douze poids mensuels a zero ressortaient a
    0 kWh/an, sans alerte."""
    p = {"kwh_an": 2500.0, "profil": "occupation", "poids_mensuels": [0.0] * 12}
    arr, _ = L._generique(p, meteo)
    assert abs(arr.sum() / meteo["n_years"] - 2500.0) < 1.0


def test_un_poste_de_type_inconnu_est_signale(cfg, meteo):
    c = copy.deepcopy(cfg)
    c["postes"].append({"nom": "Type invente", "kind": "nimporte",
                        "actif": True, "params": {}})
    _, detail, infos = L.build_load(c, meteo)
    assert "Type invente" in detail
    assert infos["Type invente"]["type_inconnu"] == "nimporte"


# --------------------------------------------------------------------------
# simulation.py
# --------------------------------------------------------------------------
def test_deux_groupes_de_meme_nom_restent_distincts(cfg, meteo):
    c = copy.deepcopy(cfg)
    c["champs"][1]["nom"] = c["champs"][0]["nom"]
    res = S.simulate(c, meteo)
    assert len(res["par_champ"]) == 2
    assert len(res["diag_champs"]) == 2


def test_autonomie_jamais_negative_avec_recharge_reseau(cfg, meteo):
    """L'energie achetee en heures creuses pour remplir la batterie etait
    comptee a l'achat puis rendue 'autonome' a la restitution : l'autonomie
    tombait a -6,5 % et l'autoconsommation a -1,3e14 %."""
    c = copy.deepcopy(cfg)
    c["systeme"]["recharge_reseau_hc"] = True
    c["systeme"]["soc_cible_hc"] = 0.9
    for ch in c["champs"]:
        ch["n_panneaux"] = 0
    res = S.simulate(c, meteo)
    assert -1e-6 <= res["kpi"]["autonomie"] <= 1.0
    assert 0.0 <= res["kpi"]["taux_autoconso"] <= 1.0
    assert res["mensuel"]["autonomie"].min() >= -1e-6
    # sans aucun panneau, l'autonomie est nulle : tout vient du reseau,
    # y compris ce qui transite par la batterie
    assert res["kpi"]["autonomie"] < 1e-3


def test_autonomie_inchangee_sans_recharge_reseau(cfg, meteo):
    res = S.simulate(cfg, meteo)
    assert res["kpi"]["import_effectif_an"] == pytest.approx(
        res["kpi"]["import_an"], rel=1e-9)
    assert 0.80 < res["kpi"]["autonomie"] < 0.92


def test_budget_de_consommation_avoue_quand_la_cible_est_hors_datteinte(cfg, meteo):
    """La dichotomie renvoyait sa borne basse comme un resultat : janvier
    annoncait 4,11 kWh/jour pour une autonomie reelle de 2,7 %."""
    c = copy.deepcopy(cfg)
    c["champs"][0]["n_panneaux"] = 2
    c["champs"][1]["n_panneaux"] = 0
    c["systeme"]["batt_kwh_nominal"] = 5.0
    res = S.simulate(c, meteo)
    budget = S.budget_consommation(c, meteo, res, cible=0.92)
    assert budget[0][1] is None          # janvier : impossible
    assert budget[6][1] is not None      # juillet : possible


def test_cas_limites_sans_chiffre_fantaisiste(cfg, meteo):
    c = copy.deepcopy(cfg)
    c["systeme"]["batt_kwh_nominal"] = 0.0
    res = S.simulate(c, meteo)
    assert S.duree_vie_batterie_ans(res["kpi"]["cycles_batterie_an"]) is None

    c = copy.deepcopy(cfg)
    for p in c["postes"]:
        p["actif"] = False
    c["systeme"]["veille_w"] = 0.0
    c["systeme"]["veille_nuit_w"] = 0.0
    res = S.simulate(c, meteo)
    assert res["eco"]["lcoe_kwh_utile"] is None      # valait 2,5e13 EUR/kWh


def test_une_petite_batterie_nest_pas_gratuite(cfg):
    c = copy.deepcopy(cfg)
    c["systeme"]["batt_kwh_nominal"] = 8.0
    assert S.bom_quantities(c)["packs"] >= 1
    c["systeme"]["batt_kwh_nominal"] = 64.3
    assert S.bom_quantities(c)["packs"] == 4      # et pas 5 : 4 x 16,07 = 64,3


def test_la_nomenclature_signale_ce_quelle_na_pas_su_classer(cfg, meteo):
    c = copy.deepcopy(cfg)
    c["bom"].append({"poste": "Cuisine equipee", "auto": "fixe",
                     "qte": 1, "pu": 9000.0, "unite": "lot"})
    c["bom"][-1]["categorie"] = C.deviner_categorie("Cuisine equipee")
    res = S.simulate(c, meteo)
    textes = " ".join(m for _t, m in res["alertes"])
    assert "Cuisine equipee" in textes

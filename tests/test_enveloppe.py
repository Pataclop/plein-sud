"""Calcul du besoin de chauffage a partir du descriptif du logement.

Le mode "enveloppe" remplace un chiffre saisi a la main par un modele
physique : surface x coefficient de transmission de chaque paroi, plus le
renouvellement d'air, moins les apports gratuits. Ces tests verifient que le
modele reagit dans le bon sens et dans le bon ordre de grandeur, et surtout
que l'ancien mode n'a pas bouge : toutes les configurations enregistrees
avant cette version doivent donner exactement le meme resultat.
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


@pytest.fixture
def params():
    p = copy.deepcopy(C.LOAD_KINDS["chauffage"]["defaults"])
    p["mode_besoin"] = C.MODE_BESOIN_ENVELOPPE
    return p


# --------------------------------------------------------------------------
# Compatibilite : le mode saisi doit rester strictement identique
# --------------------------------------------------------------------------
def test_le_mode_saisi_reste_le_comportement_par_defaut():
    """Une configuration enregistree avant l'ajout de l'enveloppe ne contient
    pas mode_besoin : elle doit continuer a utiliser le besoin saisi."""
    assert C.LOAD_KINDS["chauffage"]["defaults"]["mode_besoin"] == C.MODE_BESOIN_SAISI


def test_le_besoin_saisi_est_restitue_a_l_unite_pres(meteo):
    cfg = C.default_config()
    poste = next(p for p in cfg["postes"] if p["kind"] == "chauffage")
    poste["params"]["part_bois"] = 0.0
    _, info = L._chauffage(poste["params"], meteo)
    attendu = float(poste["params"]["besoin_th_kwh_an"])
    assert info["th_total_kwh_an"] == pytest.approx(attendu, rel=1e-6)


# --------------------------------------------------------------------------
# Deperditions
# --------------------------------------------------------------------------
def test_ua_est_la_somme_de_ses_postes(params):
    env = L.deperditions(params)
    somme = (env["ua_murs"] + env["ua_toiture"] + env["ua_plancher"]
             + env["ua_vitrages"] + env["ua_ponts"] + env["ua_ventilation"])
    assert somme == pytest.approx(env["ua_total"], rel=1e-9)


def test_l_empilement_des_niveaux_reduit_les_deperditions(params):
    """Deux niveaux de 100 m2 perdent moins qu'un plain-pied de 200 m2 : la
    toiture et le plancher sont deux fois plus petits. C'est la compacite,
    l'argument qu'un simple ratio kWh/m2 ne sait pas montrer."""
    params["n_niveaux"] = 1
    plain_pied = L.deperditions(params)["ua_total"]
    params["n_niveaux"] = 2
    etage = L.deperditions(params)["ua_total"]
    assert etage < plain_pied


def test_la_mitoyennete_supprime_les_pertes_des_murs_concernes(params):
    params["part_murs_mitoyens"] = 0.0
    isole = L.deperditions(params)
    params["part_murs_mitoyens"] = 0.5
    mitoyen = L.deperditions(params)
    assert mitoyen["ua_murs"] < isole["ua_murs"]
    assert mitoyen["ua_toiture"] == pytest.approx(isole["ua_toiture"])


def test_la_hauteur_sous_plafond_agit_sur_les_murs_et_sur_l_air(params):
    """Elle compte deux fois : surface de murs ET volume a renouveler. Une
    grange a 4 m de plafond ne se ramene pas a sa surface au sol."""
    bas = L.deperditions(params)
    params["hauteur_sous_plafond_m"] = 4.0
    haut = L.deperditions(params)
    assert haut["ua_murs"] > bas["ua_murs"]
    assert haut["ua_ventilation"] > bas["ua_ventilation"]


def test_l_echangeur_double_flux_reduit_le_cout_de_la_ventilation(params):
    sans = L.deperditions(params)["ua_ventilation"]
    params["rendement_echangeur"] = 0.75
    avec = L.deperditions(params)["ua_ventilation"]
    assert avec == pytest.approx(sans * 0.25, rel=1e-9)


def test_le_correctif_de_calage_multiplie_tout(params):
    ref = L.deperditions(params)["ua_total"]
    params["correctif_deperditions"] = 1.2
    assert L.deperditions(params)["ua_total"] == pytest.approx(ref * 1.2, rel=1e-9)


def test_un_libelle_d_isolation_inconnu_ne_fait_pas_disparaitre_la_paroi(params):
    """Une configuration enregistree avec un libelle qui n'existe plus doit
    retomber sur une valeur par defaut, pas sur un mur sans deperdition."""
    params["iso_murs"] = "Isolation inventee par une version future"
    assert L.deperditions(params)["ua_murs"] > 0


# --------------------------------------------------------------------------
# Besoin annuel
# --------------------------------------------------------------------------
def test_le_besoin_calcule_est_dans_un_ordre_de_grandeur_credible(params, meteo):
    _, info = L.besoin_enveloppe(params, meteo)
    # maison de 200 m2 des annees 1975-1990 en Gironde
    assert 40 < info["besoin_par_m2"] < 160
    assert info["besoin_calcule_kwh_an"] > 0


def test_isoler_reduit_le_besoin_dans_les_grandes_largeurs(params, meteo):
    _, avant = L.besoin_enveloppe(params, meteo)
    params.update(copy.deepcopy(C.NIVEAUX_ISOLATION["RE2020 ou renovation performante"]))
    _, apres = L.besoin_enveloppe(params, meteo)
    assert apres["besoin_calcule_kwh_an"] < 0.5 * avant["besoin_calcule_kwh_an"]


def test_monter_la_consigne_augmente_le_besoin(params, meteo):
    _, froid = L.besoin_enveloppe(params, meteo)
    params["t_consigne"] = float(params["t_consigne"]) + 2.0
    _, chaud = L.besoin_enveloppe(params, meteo)
    assert chaud["besoin_calcule_kwh_an"] > froid["besoin_calcule_kwh_an"]


def test_les_apports_solaires_dependent_de_l_orientation(params, meteo):
    params["orientation_vitrages"] = "Majorite au sud"
    _, sud = L.besoin_enveloppe(params, meteo)
    params["orientation_vitrages"] = "Majorite au nord"
    _, nord = L.besoin_enveloppe(params, meteo)
    assert sud["apports_solaires_kwh_an"] > nord["apports_solaires_kwh_an"]
    assert sud["besoin_calcule_kwh_an"] < nord["besoin_calcule_kwh_an"]


def test_la_temperature_de_non_chauffage_est_un_resultat(params, meteo):
    """Elle vaut consigne - apports / UA : mieux isole, on arrete de chauffer
    plus tot. C'est ce que le mode saisi demandait de deviner."""
    _, passoire = L.besoin_enveloppe(params, meteo)
    params.update(copy.deepcopy(C.NIVEAUX_ISOLATION["Passif"]))
    _, passif = L.besoin_enveloppe(params, meteo)
    assert passif["t_base_equivalente"] < passoire["t_base_equivalente"]
    assert 5.0 < passif["t_base_equivalente"] < 20.0


def test_la_puissance_de_dimensionnement_couvre_l_heure_la_plus_froide(params, meteo):
    th, info = L.besoin_enveloppe(params, meteo)
    assert info["p_dimensionnement_kw"] >= th.max() * 0.95
    assert info["t_ext_base"] < 5.0


def test_le_besoin_horaire_ne_devient_jamais_negatif(params, meteo):
    th, _ = L.besoin_enveloppe(params, meteo)
    assert th.min() >= 0.0
    assert np.isfinite(th).all()


def test_une_surface_nulle_ne_plante_pas(params, meteo):
    params["surface_habitable_m2"] = 0.0
    th, info = L.besoin_enveloppe(params, meteo)
    assert th.sum() == 0.0
    assert np.isfinite(info["t_base_equivalente"])


# --------------------------------------------------------------------------
# Integration
# --------------------------------------------------------------------------
def test_le_mode_enveloppe_traverse_toute_la_simulation(meteo):
    cfg = C.default_config()
    poste = next(p for p in cfg["postes"] if p["kind"] == "chauffage")
    poste["params"]["mode_besoin"] = C.MODE_BESOIN_ENVELOPPE
    res = S.simulate(cfg, meteo)
    info = res["infos_postes"][poste["nom"]]
    assert info["besoin_calcule_kwh_an"] == pytest.approx(info["th_total_kwh_an"])
    assert res["kpi"]["besoin_an"] > 0


def test_le_levier_chauffage_agit_sur_l_enveloppe_et_pas_dans_le_vide():
    """En mode enveloppe, reduire besoin_th_kwh_an ne changerait rien : le
    levier doit viser les deperditions, sinon il sort a zero en silence."""
    poste = {"kind": "chauffage",
             "params": {"mode_besoin": C.MODE_BESOIN_ENVELOPPE}}
    assert S.levier_reduction(poste)[0] == "correctif_deperditions"
    poste["params"]["mode_besoin"] = C.MODE_BESOIN_SAISI
    assert S.levier_reduction(poste)[0] == "besoin_th_kwh_an"


def test_toute_valeur_par_defaut_est_dans_les_bornes_du_schema():
    """Un defaut hors bornes serait silencieusement ecrete par le champ de
    saisie : la configuration affichee ne serait plus celle du calcul."""
    for kind, d in C.LOAD_KINDS.items():
        for ligne in d["params"]:
            cle, _lab, typ, mn, mx, extra, _aide = ligne
            if cle.startswith("__grp"):
                continue
            v = d["defaults"][cle]
            if typ in ("float", "int"):
                assert mn <= v <= mx, (kind, cle, v, mn, mx)
            elif typ == "choice":
                assert v in extra, (kind, cle, v)


def test_toute_valeur_par_defaut_des_autres_schemas_est_dans_les_bornes():
    for schema, defauts, nom in (
            (C.SYSTEM_SCHEMA, C.SYSTEM_DEFAULTS, "systeme"),
            (C.MODULE_SCHEMA, C.MODULE_DEFAULTS, "module"),
            (C.ECO_SCHEMA, C.ECO_DEFAULTS, "economie"),
            (C.SITE_SCHEMA, C.SITE_DEFAULTS, "site")):
        for cle, _lab, typ, mn, mx, extra, _aide in schema:
            if cle.startswith("__grp") or typ not in ("float", "int"):
                continue
            v = defauts[cle]
            assert mn <= v <= mx, (nom, cle, v, mn, mx)


# --------------------------------------------------------------------------
# Tres grosses installations
# --------------------------------------------------------------------------
def test_une_installation_de_plusieurs_megawatts_se_simule(meteo):
    """Les bornes des champs de saisie doivent laisser passer une centrale,
    pas seulement une maison : un parc au sol de 6 MWc avec 12 MWh de
    batterie et une souscription de 2,5 MVA."""
    cfg = C.default_config()
    cfg["systeme"].update({"n_onduleurs": 60, "p_nom_kw": 100.0,
                           "pv_max_kwc_par_onduleur": 130.0,
                           "batt_kwh_nominal": 12000.0,
                           "p_souscrite_kva": 2500.0})
    for champ in cfg["champs"]:
        champ["n_panneaux"] *= 200
    for poste in cfg["postes"]:
        if poste["kind"] == "generique":
            poste["params"]["kwh_an"] *= 400
        elif poste["kind"] == "talon":
            poste["params"]["puissance_w"] *= 400

    for cle, _lab, typ, mn, mx, _extra, _aide in C.SYSTEM_SCHEMA:
        if typ in ("float", "int") and cle in cfg["systeme"]:
            assert mn <= cfg["systeme"][cle] <= mx, (cle, cfg["systeme"][cle])

    res = S.simulate(cfg, meteo)
    k = res["kpi"]
    assert S.total_kwc(cfg) > 5000
    assert k["production_an"] > 1e6
    assert 0.0 <= k["autonomie"] <= 1.0
    assert np.isfinite(k["besoin_an"]) and k["besoin_an"] > 0

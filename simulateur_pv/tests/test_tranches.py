"""Amortissement tranche par tranche et optimum puissance PV x batterie.

Le temps de retour global est une moyenne : une premiere batterie amortie en
2 ans suivie d'une quatrieme qui ne s'amortira jamais donnent un chiffre
flatteur qui masque exactement la decision a prendre. Ces tests verrouillent
le calcul qui repond a la vraie question : combien de temps met LA TRANCHE
SUIVANTE a se rembourser elle-meme ?
"""
import copy

import numpy as np
import pytest

from pv_sizer import config as C
from pv_sizer import simulation as S
from pv_sizer.meteo import ensure_meteo


@pytest.fixture(scope="module")
def meteo():
    return ensure_meteo(C.default_config()["site"], allow_download=False)


@pytest.fixture(scope="module")
def cfg():
    return C.default_config()


@pytest.fixture(scope="module")
def balayage_batterie(cfg, meteo):
    return S.sweep(cfg, meteo, "batterie", [0, 16, 32, 48, 64, 96, 128])


# --------------------------------------------------------------------------
# Tranches successives
# --------------------------------------------------------------------------
def test_chaque_point_porte_sa_tranche(balayage_batterie):
    out = balayage_batterie
    assert out[0]["statut"] == "depart"
    assert out[0]["retour_tranche"] is None
    for prec, o in zip(out, out[1:]):
        assert o["cout_tranche"] > 0
        assert o["delta_valeur"] == pytest.approx(o["valeur"] - prec["valeur"])
        assert o["statut"] in S.TRANCHE_STATUTS


def test_le_retour_de_la_tranche_se_degrade_quand_on_grossit(balayage_batterie):
    """Les premiers kWh de batterie servent tous les soirs, les derniers
    quelques jours d'hiver : le temps de retour d'une tranche ne peut que
    s'allonger."""
    retours = [o["retour_tranche"] for o in balayage_batterie[1:]
               if o["retour_tranche"] is not None]
    assert len(retours) >= 3
    assert retours == sorted(retours)
    assert retours[0] < 4.0           # la premiere tranche est tres rentable


def test_la_tranche_est_plus_severe_que_le_retour_global(balayage_batterie):
    """C'est tout l'interet du calcul : sur les dernieres tranches, le retour
    global reste flatteur alors que la tranche elle-meme ne se paie plus."""
    dernier = balayage_batterie[-1]
    assert dernier["retour"] is not None and dernier["retour"] < 12
    assert (dernier["retour_tranche"] is None
            or dernier["retour_tranche"] > dernier["retour"])


def test_une_tranche_sans_surcout_nest_pas_comptee_comme_nulle(cfg, meteo):
    """Incliner autrement ne coute pas un euro. Si cela rapporte, la tranche
    est gratuite et immediatement rentable ; sinon il n'y a rien a amortir.
    Dans les deux cas le rapport gain/surcout n'existe pas, et afficher 0,000
    laissait croire a une option sans interet."""
    out = S.sweep(cfg, meteo, "inclinaison", [30, 45, 60])
    for o in out[1:]:
        assert o["cout_tranche"] == pytest.approx(0.0)
        assert o["statut"] in ("gratuit", "sans_surcout")
        assert o["rentabilite_marginale"] is None
        if o["statut"] == "gratuit":
            assert o["retour_tranche"] == 0.0      # se paie tout de suite
            assert o["gain_net_tranche"] > 0
        else:
            assert o["retour_tranche"] is None
    # passer de 30 a 45 degres gagne de l'autonomie sans rien couter
    assert out[1]["statut"] == "gratuit"


def test_derniere_tranche_rentable(balayage_batterie):
    out = balayage_batterie
    large = S.derniere_tranche_rentable(out, seuil_ans=40)
    serre = S.derniere_tranche_rentable(out, seuil_ans=4)
    assert large is not None and serre is not None
    assert out[serre]["valeur"] <= out[large]["valeur"]
    # un seuil impossible ne renvoie que le point de depart
    assert S.derniere_tranche_rentable(out, seuil_ans=0.01) == 0


# --------------------------------------------------------------------------
# Grille puissance PV x batterie
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def grille(cfg, meteo):
    return S.grille_dimensionnement(cfg, meteo, [10, 20, 30], [0, 32, 64])


def test_la_grille_est_complete_et_coherente(grille):
    m = grille["matrices"]
    assert m["autonomie"].shape == (3, 3)
    assert not np.any(np.isnan(m["autonomie"]))
    # l'autonomie croit avec les panneaux comme avec la batterie
    assert np.all(np.diff(m["autonomie"], axis=1) > 0)
    assert np.all(np.diff(m["autonomie"], axis=0) > 0)
    # le cout aussi
    assert np.all(np.diff(m["capex"], axis=1) > 0)
    assert np.all(np.diff(m["capex"], axis=0) > 0)


def test_la_puissance_rapportee_est_celle_qui_a_ete_simulee(cfg, meteo):
    """Les panneaux sont un nombre entier par groupe : la puissance obtenue
    n'est pas exactement celle demandee, et c'est elle qu'il faut afficher."""
    g = S.grille_dimensionnement(cfg, meteo, [13, 27], [0])
    for j, demande in enumerate(g["kwc_demandes"]):
        c, reel = S._config_dimensionnee(cfg, demande, 0)
        assert g["kwc"][j] == pytest.approx(reel)


def test_les_criteres_ne_designent_pas_le_meme_point(grille):
    i_gain, j_gain = S.optimum_grille(grille, "gain")
    i_ret, j_ret = S.optimum_grille(grille, "retour")
    m = grille["matrices"]
    # le gain maximal est sur une installation plus grosse que le retour
    # le plus court : c'est precisement ce que la separation des criteres
    # doit rendre visible
    assert m["capex"][i_gain, j_gain] >= m["capex"][i_ret, j_ret]
    assert m["gain"][i_gain, j_gain] == m["gain"].max()


def test_chemin_de_croissance_ordonne_et_borne(grille):
    ch = S.chemin_croissance(grille, seuil_ans=10.0)
    etapes = ch["etapes"]
    assert etapes[0]["quoi"] == "Point de depart"
    assert etapes[0]["kwc"] == grille["kwc"][0]
    assert etapes[0]["batt"] == grille["batt"][0]
    for e in etapes[1:]:
        assert e["retour_tranche"] <= 10.0 + 1e-9
        assert e["cout_tranche"] > 0
    # le cout et l'autonomie ne peuvent que croitre le long du chemin
    capex = [e["capex"] for e in etapes]
    aut = [e["autonomie"] for e in etapes]
    assert capex == sorted(capex)
    assert aut == sorted(aut)


def test_un_seuil_plus_large_va_plus_loin(grille):
    court = S.chemin_croissance(grille, seuil_ans=3.0)["etapes"]
    long = S.chemin_croissance(grille, seuil_ans=30.0)["etapes"]
    assert len(long) >= len(court)
    assert long[-1]["capex"] >= court[-1]["capex"]


def test_un_seuil_impossible_sarrete_au_depart(grille):
    ch = S.chemin_croissance(grille, seuil_ans=0.01)
    assert len(ch["etapes"]) == 1
    assert ch["arret"]

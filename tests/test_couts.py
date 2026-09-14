"""Separation du devis : perimetre de l'installation solaire vs reste du projet.

Ces tests verrouillent la regle qui fait tenir tout le reste : ce qui est
compte comme "installation solaire" ne doit pas deriver au fil des versions,
sinon les EUR/Wc et les temps de retour d'une session a l'autre ne sont plus
comparables.
"""
import copy

from pv_sizer import config as C
from pv_sizer import simulation as S


def _cfg():
    return C.default_config()


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------
def test_toute_ligne_par_defaut_porte_une_categorie_connue():
    for ligne in C.BOM_DEFAUT + C.BOM_COMPLEMENT:
        assert ligne["categorie"] in C.BOM_CATEGORIES, ligne["poste"]


def test_perimetre_solaire_exclut_le_thermique_et_le_batiment():
    assert C.est_solaire("pv")
    assert C.est_solaire("conversion")
    assert C.est_solaire("stockage")
    assert C.est_solaire("electrique")
    assert not C.est_solaire("thermique")
    assert not C.est_solaire("batiment")
    assert not C.est_solaire("autre")
    # une categorie inconnue retombe sur "divers", donc dans le perimetre :
    # mieux vaut une ligne a reclasser qu'un cout qui disparait du bilan.
    assert C.est_solaire("categorie_inventee")


def test_devine_categorie_sur_les_libelles_historiques():
    attendu = {
        "Modules photovoltaiques": "pv",
        "Structure et fixations": "pv",
        "Onduleurs hybrides Deye": "conversion",
        "Cellules LFP 314 Ah grade A": "stockage",
        "BMS 16S 200 A": "stockage",
        "Coffrets, busbars, compression, fusibles classe T": "stockage",
        "Cablage DC PV, connecteurs, chemins de cables": "electrique",
        "Tableaux AC/DC, parafoudres, protections": "electrique",
        "Supervision et mesure par circuit": "electrique",
        "Terrassement, tranchee, liaison, mise a la terre": "genie_civil",
        "Consuel, declaration prealable, convention Enedis": "admin",
        "Chauffe-eau thermodynamique 300 L": "thermique",
        "Insert bois et conduit": "thermique",
        "Isolation combles et etancheite a l'air": "batiment",
    }
    for poste, cat in attendu.items():
        assert C.deviner_categorie(poste) == cat, poste


def test_devine_categorie_repli_sur_la_regle_de_quantite():
    assert C.deviner_categorie("Poste sans mot-cle", "onduleurs") == "conversion"
    assert C.deviner_categorie("Poste sans mot-cle", "cellules") == "stockage"
    assert C.deviner_categorie("Poste sans mot-cle", "fixe") == "divers"


# --------------------------------------------------------------------------
# Migration des configurations enregistrees
# --------------------------------------------------------------------------
def test_migration_dune_nomenclature_sans_categorie(tmp_path):
    cfg = _cfg()
    for ligne in cfg["bom"]:
        ligne.pop("categorie")
    p = tmp_path / "ancienne.json"
    C.save_config(cfg, str(p))
    recharge = C.load_config(str(p))
    assert all(l["categorie"] in C.BOM_CATEGORIES for l in recharge["bom"])
    assert recharge["bom"][0]["categorie"] == "pv"
    assert recharge["bom"][-1]["categorie"] == "batiment"


def test_migration_dune_categorie_devenue_invalide(tmp_path):
    cfg = _cfg()
    cfg["bom"][0]["categorie"] = "categorie_supprimee_en_v2"
    p = tmp_path / "cassee.json"
    C.save_config(cfg, str(p))
    assert C.load_config(str(p))["bom"][0]["categorie"] == "pv"


# --------------------------------------------------------------------------
# Chiffrage
# --------------------------------------------------------------------------
def test_compute_bom_ventile_et_totalise():
    cfg = _cfg()
    lignes, recap = S.compute_bom(cfg)
    assert len(lignes) == len(cfg["bom"])
    assert abs(recap["solaire"] + recap["hors_solaire"] - recap["total"]) < 1e-6
    assert abs(sum(recap["par_categorie"].values()) - recap["total"]) < 1e-6
    assert recap["solaire"] > 0 and recap["hors_solaire"] > 0
    montant_hors = sum(l["montant"] for l in lignes if not l["solaire"])
    assert abs(montant_hors - recap["hors_solaire"]) < 1e-6


def test_un_poste_hors_solaire_ne_bouge_pas_le_perimetre_solaire():
    cfg = _cfg()
    avant = S.compute_bom(cfg)[1]
    cher = copy.deepcopy(cfg)
    for ligne in cher["bom"]:
        if not C.est_solaire(ligne["categorie"]):
            ligne["pu"] = float(ligne["pu"]) * 10.0
    apres = S.compute_bom(cher)[1]
    assert abs(apres["solaire"] - avant["solaire"]) < 1e-6
    assert apres["total"] > avant["total"]


def test_un_panneau_de_plus_ne_bouge_que_le_perimetre_solaire():
    cfg = _cfg()
    avant = S.compute_bom(cfg)[1]
    plus = copy.deepcopy(cfg)
    plus["champs"][0]["n_panneaux"] += 1
    apres = S.compute_bom(plus)[1]
    assert apres["solaire"] > avant["solaire"]
    assert abs(apres["hors_solaire"] - avant["hors_solaire"]) < 1e-6


def test_cout_categorie():
    cfg = _cfg()
    _, recap = S.compute_bom(cfg)
    assert abs(S.cout_categorie(recap, "pv", "conversion")
               - (recap["par_categorie"]["pv"]
                  + recap["par_categorie"]["conversion"])) < 1e-6
    assert S.cout_categorie(recap, "categorie_absente") == 0.0

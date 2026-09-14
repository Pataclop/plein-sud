#!/usr/bin/env python3
"""
Simulateur de dimensionnement photovoltaique + stockage LFP.

    python main.py                        interface graphique
    python main.py config.json            interface graphique avec une config
    python main.py --cli [config.json]    rapport texte, sans interface
    python main.py --sweep inclinaison 20,30,40,50,60,70
    python main.py --grille 5:40@5 0:128@16 [seuil_ans] [config.json]
    python main.py --download 44.71 -0.39 2018 2023
"""
from __future__ import annotations
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
CFG_DEFAUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_defaut.json")


def cli(cfg_path=None):
    from pv_sizer.config import default_config, load_config
    from pv_sizer.meteo import ensure_meteo, meteo_summary
    from pv_sizer import simulation as S

    cfg = load_config(cfg_path) if cfg_path and os.path.exists(cfg_path) else default_config()
    meteo = ensure_meteo(cfg["site"], allow_download=True,
                         progress=lambda p, m="": print(f"  [{p:3d}%] {m}"))
    ms = meteo_summary(meteo)
    res = S.simulate(cfg, meteo)
    k, e, m = res["kpi"], res["eco"], res["mensuel"]

    print(f"\n{'=' * 78}\n  {cfg['site']['nom']} - meteo {ms['annees']}, "
          f"{ms['ghi_kwh_m2_an']:.0f} kWh/m2/an, {ms['dju_17']:.0f} DJU\n{'=' * 78}")

    print("\nCHAMPS PV")
    for n, d in res["diag_champs"].items():
        print(f"  {n:38s} {d['kwc']:6.2f} kWc  {d['productible_kwh_kwc']:5.0f} kWh/kWc  "
              f"{d['production_kwh_an']:8.0f} kWh/an")

    print("\nCONSOMMATION")
    ny = meteo["n_years"]
    for n, a in res["detail_postes"].items():
        print(f"  {n:38s} {a.sum() / ny:8.0f} kWh/an")
    print(f"  {'Veille des onduleurs':38s} {k['veille_an']:8.0f} kWh/an")
    print(f"  {'TOTAL':38s} {k['besoin_an']:8.0f} kWh/an   "
          f"({k['conso_jour_moy']:.1f} kWh/jour en moyenne, "
          f"max {k['conso_jour_max']:.0f} kWh)")

    print(f"\nBILAN MENSUEL  (moyenne sur {k['n_years']:.0f} annees)")
    print(f"  {'Mois':5s} {'Besoin':>8s} {'Prod':>8s} {'Autocons':>9s} "
          f"{'Import':>8s} {'Ecrete':>8s} {'Auton.':>7s} {'kWh/j':>7s}")
    for i in range(12):
        print(f"  {S.MOIS[i]:5s} {m['besoin'][i]:8.0f} {m['production_dc'][i]:8.0f} "
              f"{m['autoconso'][i]:9.0f} {m['import'][i]:8.0f} {m['ecrete'][i]:8.0f} "
              f"{100 * m['autonomie'][i]:6.1f}% {m['conso_jour'][i]:7.1f}")
    print(f"  {'AN':5s} {m['besoin'].sum():8.0f} {m['production_dc'].sum():8.0f} "
          f"{m['autoconso'].sum():9.0f} {m['import'].sum():8.0f} {m['ecrete'].sum():8.0f} "
          f"{100 * k['autonomie']:6.1f}%")

    print(f"\nSYSTEME")
    print(f"  Autonomie annuelle          {100 * k['autonomie']:.1f} %")
    print(f"  Taux d'autoconsommation     {100 * k['taux_autoconso']:.1f} %")
    print(f"  Productible moyen           {k['productible_kwh_kwc']:.0f} kWh/kWc/an")
    vie = S.duree_vie_batterie_ans(k["cycles_batterie_an"])
    print(f"  Cycles batterie             {k['cycles_batterie_an']:.0f} /an "
          + (f"(duree de vie ~{vie:.0f} ans)" if vie else "(ne cycle pas)"))
    print(f"  Jours sans aucun soutirage  {k['jours_sans_import']} /an")
    print(f"  Energie ecretee             {k['ecrete_an']:.0f} kWh/an")

    from pv_sizer.config import BOM_CATEGORIES, ORDRE_CATEGORIES, est_solaire

    horizon = int(cfg["economie"].get("duree_analyse_ans", 25))

    def ans(v):
        """Temps de retour, ou '>horizon' quand il n'est jamais rembourse."""
        return f"{v:.1f} ans" if v is not None else f">{horizon} ans"

    print("\nNOMENCLATURE")
    for cat in ORDRE_CATEGORIES:        # surtout pas "k" : c'est res["kpi"]
        v = e["capex_par_categorie"].get(cat, 0.0)
        if not v:
            continue
        marque = " " if est_solaire(cat) else "*"
        print(f" {marque}{BOM_CATEGORIES[cat]['label']:<42s} {v:9,.0f} EUR"
              .replace(",", " "))
    print(f"  {'PERIMETRE SOLAIRE':<42s} {e['capex_solaire']:9,.0f} EUR  "
          f"({e['cout_par_wc']:.2f} EUR/Wc)".replace(",", " "))
    print(f" *{'Hors perimetre solaire':<42s} {e['capex_hors_solaire']:9,.0f} EUR"
          .replace(",", " "))
    print(f"  {'PROJET COMPLET':<42s} {e['capex_total']:9,.0f} EUR"
          .replace(",", " "))

    print(f"\nECONOMIE")
    print(f"  {'':28s} {'installation solaire':>20s} {'projet complet':>18s}")
    print(f"  {'Investissement':28s} {e['capex_solaire']:16,.0f} EUR "
          f"{e['capex_total']:14,.0f} EUR".replace(",", " "))
    print(f"  {'Economie annuelle':28s} {e['economie_vs_sans_pv']:16,.0f} EUR "
          f"{e['economie_vs_actuel']:14,.0f} EUR".replace(",", " "))
    print(f"  {'Temps de retour':28s} {ans(e['retour_ans_vs_sans_pv']):>20s} "
          f"{ans(e['retour_ans_vs_actuel']):>18s}")
    print(f"  {f'Gain cumule a {horizon} ans':28s} {e['gain_cumule_solaire']:16,.0f} EUR "
          f"{e['gain_cumule_projet']:14,.0f} EUR".replace(",", " "))
    print()
    print(f"  Cout annuel avec PV         {e['cout_annuel_avec_pv']:,.0f} EUR/an"
          .replace(",", " "))
    print(f"  La meme maison sans PV      {e['cout_annuel_sans_pv']:,.0f} EUR/an"
          .replace(",", " "))
    lcoe = e["lcoe_kwh_utile"]
    print(f"  kWh evite d'acheter         "
          + (f"{lcoe:.3f} EUR/kWh  "
             f"(reseau : {cfg['economie']['prix_kwh_achat']:.3f})"
             if lcoe is not None else "sans objet (aucun besoin couvert)"))
    print(f"  kWh de batterie installe    {e['cout_par_kwh_batterie']:.0f} EUR/kWh")

    if res["alertes"]:
        print("\nALERTES")
        for t, msg in res["alertes"]:
            print(f"  [{t.upper()}] {msg}")
    print()
    return cfg, meteo, res


def main():
    args = sys.argv[1:]
    if args and args[0] == "--download":
        from pv_sizer.meteo import download_pvgis
        lat, lon, y0, y1 = float(args[1]), float(args[2]), int(args[3]), int(args[4])
        p = download_pvgis(lat, lon, y0, y1,
                           progress=lambda pc, m="": print(f"  [{pc:3d}%] {m}"))
        print("Enregistre :", p)
        return
    if args and args[0] == "--sweep":
        from pv_sizer import simulation as S
        var = args[1]
        vals = [float(x) for x in args[2].split(",")]
        cfg, meteo, res = cli(args[3] if len(args) > 3 else
                              (CFG_DEFAUT if os.path.exists(CFG_DEFAUT) else None))
        print(f"BALAYAGE : {var}")
        print("  Le cout affiche est celui du PERIMETRE SOLAIRE seul "
              "(panneaux, onduleurs, batterie, cablage, pose).")
        print("  Les quatre dernieres colonnes jugent CHAQUE TRANCHE seule : "
              "ce que coute le\n  passage depuis la ligne precedente, et en "
              "combien de temps il se rembourse.")
        print(f"  {'valeur':>8s} {'autonomie':>10s} {'cout sol.':>10s} "
              f"{'EUR/Wc':>7s} {'retour cum':>11s} | {'tranche':>9s} "
              f"{'cout tr.':>9s} {'gain tr.':>9s} {'RETOUR TR.':>11s}")
        out = S.sweep(cfg, meteo, var, vals)
        for o in out:
            ret = f"{o['retour']:.1f}" if o["retour"] is not None else ">horiz"
            statut = o.get("statut", "depart")
            if statut in ("depart", "sans_surcout"):
                rt = "-"
            elif statut == "jamais":
                rt = "jamais"
            elif statut == "trop_long":
                rt = ">horiz"
            elif statut == "gratuit":
                rt = "immediat"
            else:
                rt = f"{o['retour_tranche']:.1f}"
            nb = lambda v, g="%9.0f": "        -" if v is None else g % v
            dv = o.get("delta_valeur")
            print(f"  {o['valeur']:8g} {100 * o['autonomie']:9.2f}% "
                  f"{o['capex']:10.0f} {o['cout_par_wc']:7.2f} {ret:>11s} | "
                  f"{('-' if dv is None else f'{dv:+g}'):>9s} "
                  f"{nb(o.get('cout_tranche'))} {nb(o.get('gain_tranche'))} "
                  f"{rt:>11s}")
        return
    if args and args[0] == "--grille":
        from pv_sizer import simulation as S
        kwc = S.parse_sweep_values(args[1] if len(args) > 1 else "5:40@5")
        batt = S.parse_sweep_values(args[2] if len(args) > 2 else "0:128@16")
        seuil = float(args[3]) if len(args) > 3 else 10.0
        reste = [a for a in args[4:] if a.endswith(".json")]
        cfg, meteo, _res = cli(reste[0] if reste else
                               (CFG_DEFAUT if os.path.exists(CFG_DEFAUT) else None))
        print(f"\nGRILLE PUISSANCE PV x CAPACITE BATTERIE  "
              f"({len(batt)} x {len(kwc)} = {len(batt) * len(kwc)} simulations)")
        # un point tous les 10 % : la sortie reste lisible dans un fichier
        jalon = {"dernier": -10}

        def avance(pc, msg=""):
            if pc - jalon["dernier"] >= 10 or pc >= 100:
                jalon["dernier"] = pc
                print(f"  [{pc:3d}%] {msg}")

        g = S.grille_dimensionnement(cfg, meteo, kwc, batt, progress=avance)
        m = g["matrices"]

        print(f"\n  Gain cumule sur {g['horizon']} ans (EUR), "
              f"lignes = batterie, colonnes = kWc")
        print("        " + "".join(f"{k:9.0f}" for k in g["kwc"]))
        for i, b in enumerate(g["batt"]):
            print(f"  {b:5.0f} " + "".join(f"{v:9.0f}" for v in m["gain"][i]))

        print(f"\n  CHEMIN DE CROISSANCE - on s'arrete des qu'une tranche "
              f"met plus de {seuil:.0f} ans a se rembourser")
        ch = S.chemin_croissance(g, seuil_ans=seuil)
        print(f"  {'etape':<26s} {'kWc':>6s} {'kWh':>6s} {'cumul':>9s} "
              f"{'cout':>8s} {'gain/an':>8s} {'retour':>7s} {'autonomie':>10s}")
        for e in ch["etapes"]:
            nb = lambda v, g2="%8.0f": "       -" if v is None else g2 % v
            rt = "      -" if e["retour_tranche"] is None \
                else f"{e['retour_tranche']:7.1f}"
            print(f"  {e['quoi']:<26s} {e['kwc']:6.1f} {e['batt']:6.0f} "
                  f"{e['capex']:9.0f} {nb(e['cout_tranche'])} "
                  f"{nb(e['gain_tranche'])} {rt} {100 * e['autonomie']:9.1f} %")
        if ch["arret"]:
            print(f"  -> {ch['arret']}")

        print("\n  OPTIMUM SELON CHAQUE CRITERE")
        for cle, (libelle, _s, _u, _d, _a) in S.CRITERES_GRILLE.items():
            i, j = S.optimum_grille(g, cle, seuil)
            ret = m["retour"][i, j]
            print(f"  {libelle:<40s} {g['kwc'][j]:5.1f} kWc / {g['batt'][i]:4.0f} kWh"
                  f"  {m['capex'][i, j]:8.0f} EUR"
                  f"  {100 * m['autonomie'][i, j]:6.2f} %"
                  f"  retour {('%.1f' % ret) if ret == ret else '>horiz':>6s} ans"
                  f"  gain {m['gain'][i, j]:8.0f} EUR")
        print()
        return
    if args and args[0] == "--cli":
        cli(args[1] if len(args) > 1 else (CFG_DEFAUT if os.path.exists(CFG_DEFAUT) else None))
        return

    cfg = args[0] if args else (CFG_DEFAUT if os.path.exists(CFG_DEFAUT) else None)
    try:
        from pv_sizer.ui import run
    except ImportError as e:
        print("PyQt6 est requis pour l'interface graphique :\n"
              "    pip install PyQt6 matplotlib numpy\n"
              f"({e})\n\nMode texte disponible : python main.py --cli")
        sys.exit(1)
    run(cfg)


if __name__ == "__main__":
    main()

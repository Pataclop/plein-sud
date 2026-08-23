#!/usr/bin/env python3
"""
Simulateur de dimensionnement photovoltaique + stockage LFP.

    python main.py                        interface graphique
    python main.py config.json            interface graphique avec une config
    python main.py --cli [config.json]    rapport texte, sans interface
    python main.py --sweep inclinaison 20,30,40,50,60,70
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
    print(f"  Cycles batterie             {k['cycles_batterie_an']:.0f} /an "
          f"(duree de vie ~{6000 / max(k['cycles_batterie_an'], 1):.0f} ans)")
    print(f"  Jours sans aucun soutirage  {k['jours_sans_import']} /an")
    print(f"  Energie ecretee             {k['ecrete_an']:.0f} kWh/an")

    print(f"\nECONOMIE")
    print(f"  Investissement              {e['capex']:,.0f} EUR  ({e['cout_par_wc']:.2f} EUR/Wc)"
          .replace(",", " "))
    print(f"  Cout annuel avec PV         {e['cout_annuel_avec_pv']:,.0f} EUR".replace(",", " "))
    print(f"  Economie vs facture actuelle {e['economie_vs_actuel']:,.0f} EUR/an".replace(",", " "))
    print(f"  Retour sur investissement   {e['retour_ans_vs_actuel']} ans")
    print(f"  kWh autoproduit utilise     {e['lcoe_kwh_utile']:.3f} EUR/kWh")

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
        print(f"  {'valeur':>8s} {'autonomie':>10s} {'production':>11s} "
              f"{'import':>8s} {'ecrete':>8s} {'capex':>9s}")
        for o in S.sweep(cfg, meteo, var, vals):
            print(f"  {o['valeur']:8g} {100 * o['autonomie']:9.2f}% {o['production']:11.0f} "
                  f"{o['import']:8.0f} {o['ecrete']:8.0f} {o['capex']:9.0f}")
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

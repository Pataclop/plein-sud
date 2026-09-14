from pv_sizer.simulation import compute_marginal_rentability, parse_sweep_values


def test_parse_sweep_values_supports_ranges():
    assert parse_sweep_values("0, 15, 30, 60, 75, 90, 105, 120, 135, 150, 165, 180, 200, 250, 300") == [
        0, 15, 30, 60, 75, 90, 105, 120, 135, 150, 165, 180, 200, 250, 300
    ]
    assert parse_sweep_values("10:100@5") == [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]
    assert parse_sweep_values("10:100@5, 200, 250") == [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 200, 250]


def test_compute_marginal_rentability_uses_incremental_payback():
    # Rentabilite MARGINALE : gain annuel ajoute / euro supplementaire investi,
    # d'un point du balayage au suivant. Ce n'est pas le ratio absolu
    # gain / investissement, qui resterait flatteur bien apres que la depense
    # marginale a cesse d'etre rentable.
    #   1000 -> 1500 EUR pour 250 -> 350 EUR/an : 100 / 500 = 0,20
    #   1500 -> 2000 EUR pour 350 -> 400 EUR/an :  50 / 500 = 0,10
    out = [
        {"capex": 1000.0, "economie_vs_actuel": 250.0},
        {"capex": 1500.0, "economie_vs_actuel": 350.0},
        {"capex": 2000.0, "economie_vs_actuel": 400.0},
    ]
    scores = compute_marginal_rentability(out)
    assert scores[0] is None          # rien avant le premier point
    assert abs(scores[1] - 0.20) < 1e-9
    assert abs(scores[2] - 0.10) < 1e-9


def test_compute_marginal_rentability_sans_surcout():
    """Un balayage d'inclinaison ne change pas la nomenclature : le rapport
    gain / surcout n'existe pas. Renvoyer 0,000 laissait croire a une option
    sans interet."""
    out = [
        {"capex": 25288.0, "economie_vs_sans_pv": 3300.0},
        {"capex": 25288.0, "economie_vs_sans_pv": 3355.0},
        {"capex": 25288.0, "economie_vs_sans_pv": 3340.0},
    ]
    assert compute_marginal_rentability(out) == [None, None, None]


def test_compute_marginal_rentability_prefers_solar_scoped_saving():
    """La cle par defaut est l'economie imputable au perimetre solaire ;
    les series produites avant la separation retombent sur l'ancienne cle."""
    out = [
        {"capex": 1000.0, "economie_vs_sans_pv": 200.0, "economie_vs_actuel": 900.0},
        {"capex": 1200.0, "economie_vs_sans_pv": 260.0, "economie_vs_actuel": 960.0},
    ]
    assert abs(compute_marginal_rentability(out)[1] - 0.30) < 1e-9
    ancien = [{"capex": 1000.0, "economie_vs_actuel": 200.0},
              {"capex": 1200.0, "economie_vs_actuel": 260.0}]
    assert abs(compute_marginal_rentability(ancien)[1] - 0.30) < 1e-9


def test_compute_marginal_rentability_signale_une_depense_perdante():
    """Un increment qui coute et ne rapporte rien doit ressortir negatif,
    pas etre ecrase a zero : c'est le signal d'arret."""
    out = [
        {"capex": 22000.0, "economie_vs_sans_pv": 3300.0},
        {"capex": 24000.0, "economie_vs_sans_pv": 3280.0},
    ]
    assert compute_marginal_rentability(out)[1] < 0

from pv_sizer.simulation import compute_marginal_rentability, parse_sweep_values


def test_parse_sweep_values_supports_ranges():
    assert parse_sweep_values("0, 15, 30, 60, 75, 90, 105, 120, 135, 150, 165, 180, 200, 250, 300") == [
        0, 15, 30, 60, 75, 90, 105, 120, 135, 150, 165, 180, 200, 250, 300
    ]
    assert parse_sweep_values("10:100@5") == [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]
    assert parse_sweep_values("10:100@5, 200, 250") == [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 200, 250]


def test_compute_marginal_rentability_uses_incremental_payback():
    out = [
        {"capex": 1000.0, "economie_vs_actuel": 250.0},
        {"capex": 1500.0, "economie_vs_actuel": 350.0},
        {"capex": 2000.0, "economie_vs_actuel": 400.0},
    ]
    scores = compute_marginal_rentability(out)
    assert scores[0] == 0.0
    assert abs(scores[1] - 0.25) < 1e-9
    assert abs(scores[2] - 0.2) < 1e-9

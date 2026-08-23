"""
Geometrie solaire, transposition du rayonnement sur plan incline et
modele de production photovoltaique.

Le point important pour ce projet : la transposition est faite localement,
a partir des composantes horizontales telechargees une seule fois. On peut
donc faire varier l'inclinaison de chaque champ sans retelecharger quoi que
ce soit, et comparer instantanement 30, 45, 60 ou 75 degres.
"""
from __future__ import annotations
import numpy as np

SOLAR_CONSTANT = 1367.0


# --------------------------------------------------------------------------
# Position du soleil (algorithme NOAA simplifie, precision ~0,01 degre)
# --------------------------------------------------------------------------
def sun_position(times_utc: np.ndarray, lat: float, lon: float):
    """times_utc : numpy datetime64[s] en UTC.
    Retourne (elevation_deg, azimut_deg mesure depuis le Nord dans le sens horaire)."""
    epoch = np.datetime64("2000-01-01T12:00:00")
    n = (times_utc - epoch) / np.timedelta64(1, "s") / 86400.0

    L = np.deg2rad((280.460 + 0.9856474 * n) % 360.0)
    g = np.deg2rad((357.528 + 0.9856003 * n) % 360.0)
    lam = L + np.deg2rad(1.915) * np.sin(g) + np.deg2rad(0.020) * np.sin(2 * g)
    eps = np.deg2rad(23.439 - 4.0e-7 * n)

    dec = np.arcsin(np.sin(eps) * np.sin(lam))
    ra = np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam))

    gmst = (18.697374558 + 24.06570982441908 * n) % 24.0
    lmst_deg = gmst * 15.0 + lon
    ha = np.deg2rad(((lmst_deg - np.rad2deg(ra) + 180.0) % 360.0) - 180.0)

    latr = np.deg2rad(lat)
    sin_el = np.sin(latr) * np.sin(dec) + np.cos(latr) * np.cos(dec) * np.cos(ha)
    sin_el = np.clip(sin_el, -1.0, 1.0)
    el = np.arcsin(sin_el)

    cos_az = (np.sin(dec) - sin_el * np.sin(latr)) / np.maximum(np.cos(el) * np.cos(latr), 1e-9)
    az = np.rad2deg(np.arccos(np.clip(cos_az, -1.0, 1.0)))
    az = np.where(ha > 0, 360.0 - az, az)
    return np.rad2deg(el), az


def extraterrestrial(times_utc: np.ndarray) -> np.ndarray:
    """Eclairement extraterrestre normal, W/m2."""
    doy = (times_utc.astype("datetime64[D]") -
           times_utc.astype("datetime64[Y]")).astype(int) + 1
    return SOLAR_CONSTANT * (1.0 + 0.033 * np.cos(2 * np.pi * doy / 365.25))


# --------------------------------------------------------------------------
# Transposition Hay-Davies vers plan incline
# --------------------------------------------------------------------------
def poa_irradiance(gbh, gdh, sun_el, sun_az, tilt, surf_az, e0, albedo=0.20):
    """Composantes horizontales -> plan incline.

    gbh : rayonnement direct sur plan horizontal (W/m2)
    gdh : rayonnement diffus horizontal (W/m2)
    tilt, surf_az : inclinaison et azimut du plan (degres, azimut depuis le Nord)

    Retourne (poa_total, poa_direct, aoi_deg)."""
    elr = np.deg2rad(np.maximum(sun_el, 0.0))
    tr = np.deg2rad(tilt)
    daz = np.deg2rad(sun_az - surf_az)

    sin_el = np.sin(elr)
    day = sun_el > 1.5

    # Direct normal reconstitue
    dni = np.where(day, gbh / np.maximum(sin_el, 0.026), 0.0)
    dni = np.clip(dni, 0.0, 1200.0)

    cos_aoi = sin_el * np.cos(tr) + np.cos(elr) * np.sin(tr) * np.cos(daz)
    cos_aoi = np.clip(cos_aoi, -1.0, 1.0)
    cos_aoi_pos = np.maximum(cos_aoi, 0.0)

    poa_beam = dni * cos_aoi_pos

    # Diffus anisotrope (Hay-Davies)
    ai = np.clip(np.where(day, dni / np.maximum(e0, 1.0), 0.0), 0.0, 1.0)
    rb = np.where(day, cos_aoi_pos / np.maximum(sin_el, 0.026), 0.0)
    iso = (1.0 + np.cos(tr)) / 2.0
    poa_diff = gdh * (ai * rb + (1.0 - ai) * iso)

    ghi = gbh + gdh
    poa_gnd = albedo * ghi * (1.0 - np.cos(tr)) / 2.0

    poa = np.maximum(poa_beam + poa_diff + poa_gnd, 0.0)
    return poa, poa_beam, np.rad2deg(np.arccos(cos_aoi_pos))


def iam_ashrae(aoi_deg, b0=0.05):
    """Modificateur d'angle d'incidence. Non negligeable a forte inclinaison."""
    c = np.cos(np.deg2rad(np.clip(aoi_deg, 0.0, 90.0)))
    out = 1.0 - b0 * (1.0 / np.maximum(c, 1e-3) - 1.0)
    return np.clip(np.where(aoi_deg >= 88.0, 0.0, out), 0.0, 1.0)


# --------------------------------------------------------------------------
# Production d'un champ
# --------------------------------------------------------------------------
def cell_temperature(poa, t_amb, ws, u0=25.0, u1=6.84):
    """Modele de Faiman."""
    return t_amb + poa / np.maximum(u0 + u1 * ws, 5.0)


def field_dc_power(meteo: dict, kwc: float, tilt: float, surf_az: float,
                   module: dict, albedo: float = 0.20, ombrage_pct: float = 0.0):
    """Puissance DC horaire d'un champ, en kW.

    Retourne (p_dc_kw, poa_wm2, diagnostics)."""
    poa, poa_beam, aoi = poa_irradiance(
        meteo["Gbh"], meteo["Gdh"], meteo["sun_el"], meteo["sun_az"],
        tilt, surf_az, meteo["E0"], albedo)

    iam = iam_ashrae(aoi, module.get("iam_b0", 0.05))
    poa_eff = poa_beam * iam + (poa - poa_beam) * 0.96

    tc = cell_temperature(poa, meteo["T2m"], meteo["WS10m"],
                          module.get("noct_u0", 25.0), module.get("noct_u1", 6.84))

    gamma = module.get("gamma_pmax", -0.0035)
    pertes = 1.0 - module.get("pertes_dc_pct", 8.0) / 100.0
    ombre = 1.0 - ombrage_pct / 100.0

    p = kwc * (poa_eff / 1000.0) * (1.0 + gamma * (tc - 25.0)) * pertes * ombre
    p = np.maximum(p, 0.0)

    diag = {"poa_kwh_m2_an": float(poa.sum() / 1000.0 / meteo["n_years"]),
            "tc_max": float(tc.max()),
            "productible_kwh_kwc": float(p.sum() / max(kwc, 1e-9) / meteo["n_years"])}
    return p, poa, diag


def annual_yield_by_tilt(meteo: dict, module: dict, tilts, surf_az=180.0, albedo=0.20):
    """Balayage d'inclinaison : productible annuel par kWc."""
    out = []
    for t in tilts:
        p, _, d = field_dc_power(meteo, 1.0, float(t), surf_az, module, albedo)
        out.append((float(t), d["productible_kwh_kwc"]))
    return out

"""
Acces aux donnees meteo horaires reelles.

Source : PVGIS (Commission europeenne, JRC), base SARAH3 satellite.
On telecharge les composantes du rayonnement sur plan HORIZONTAL une seule
fois par site, ce qui permet ensuite de tester n'importe quelle inclinaison
hors ligne. Un fichier par defaut couvrant Langoiran 2018-2023 est fourni.
"""
from __future__ import annotations
import os, csv, gzip, json, urllib.request, urllib.error
import numpy as np
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

from .config import (DATA_DIR, BUNDLED_DATA_DIR, PVGIS_DATABASES,
                     couverture_base)

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"
TZ_LOCAL = ZoneInfo("Europe/Paris")
COLS = ["time", "Gbh", "Gdh", "Hsun", "T2m", "WS10m"]


def cache_path(lat: float, lon: float, y0: int, y1: int, db: str) -> str:
    tag = f"{lat:.3f}_{lon:.3f}_{y0}_{y1}_{db.replace('PVGIS-', '').lower()}"
    return os.path.join(DATA_DIR, f"meteo_{tag}.csv.gz")


def default_file() -> str:
    return trouver("meteo_langoiran_2018_2023.csv.gz") or         os.path.join(DATA_DIR, "meteo_langoiran_2018_2023.csv.gz")


def trouver(nom: str) -> str | None:
    """Cherche un fichier meteo dans le cache personnel puis dans les donnees livrees."""
    for d in (DATA_DIR, BUNDLED_DATA_DIR):
        p = os.path.join(d, nom)
        if os.path.exists(p):
            return p
    return None


# --------------------------------------------------------------------------
# Telechargement
# --------------------------------------------------------------------------
def verifier_annees(y0: int, y1: int, db: str) -> None:
    """Refuse tot, avec une explication, une periode que PVGIS ne couvre pas."""
    if y1 < y0:
        raise ValueError(
            f"La derniere annee ({y1}) est anterieure a la premiere ({y0}).")
    a0, a1 = couverture_base(db)
    if y0 >= a0 and y1 <= a1:
        return
    autres = "\n".join(
        f"    - {n} : {d['annees'][0]} a {d['annees'][1]}  ({d['resume']})"
        for n, d in PVGIS_DATABASES.items())
    raise ValueError(
        f"La base {db} ne couvre que les annees {a0} a {a1}, or vous avez "
        f"demande {y0} a {y1}.\n\n"
        f"PVGIS publie ses series avec un a deux ans de retard : les donnees "
        f"satellite sont controlees et recalibrees avant diffusion, il n'existe "
        f"donc pas encore d'annee plus recente.\n\n"
        f"Periodes disponibles :\n{autres}\n\n"
        f"Ce n'est pas genant pour un dimensionnement : ces annees reelles "
        f"couvrent deja des hivers doux comme des hivers froids, ce qui est "
        f"exactement ce qu'il faut pour tester une installation.")


def download_pvgis(lat, lon, year_start, year_end, db="PVGIS-SARAH3",
                   progress=None, dest=None) -> str:
    """Telecharge annee par annee et ecrit un CSV compresse. Retourne le chemin."""
    os.makedirs(DATA_DIR, exist_ok=True)
    verifier_annees(int(year_start), int(year_end), db)
    dest = dest or cache_path(lat, lon, year_start, year_end, db)
    rows = []
    years = list(range(int(year_start), int(year_end) + 1))
    for i, y in enumerate(years):
        url = (f"{PVGIS_URL}?lat={lat}&lon={lon}&raddatabase={db}"
               f"&startyear={y}&endyear={y}&components=1&angle=0&aspect=0"
               f"&outputformat=json&browser=0")
        if progress:
            progress(int(100 * i / len(years)), f"Telechargement PVGIS {y}...")
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                d = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"PVGIS a refuse la requete pour {y} (HTTP {e.code}). "
                f"Verifiez que l'annee est couverte par {db} et que le point "
                f"est dans l'emprise de la base.") from e
        for h in d["outputs"]["hourly"]:
            rows.append((h["time"], h["Gb(i)"], h["Gd(i)"], h["H_sun"], h["T2m"], h["WS10m"]))
    if progress:
        progress(95, "Ecriture du cache...")
    with gzip.open(dest, "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    if progress:
        progress(100, f"{len(rows)} heures enregistrees")
    return dest


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------
def _open(path):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") \
        else open(path, "r", encoding="utf-8")


def load_meteo(path: str, lat: float, lon: float) -> dict:
    """Charge un fichier et precalcule tout ce qui ne depend pas de l'inclinaison."""
    times, gbh, gdh, hsun, t2m, ws = [], [], [], [], [], []
    with _open(path) as f:
        rd = csv.reader(f)
        header = next(rd)
        idx = {c: header.index(c) for c in COLS if c in header}
        for row in rd:
            if not row or not row[idx["time"]]:
                continue
            times.append(row[idx["time"]])
            gbh.append(float(row[idx["Gbh"]]))
            gdh.append(float(row[idx["Gdh"]]))
            hsun.append(float(row[idx["Hsun"]]) if "Hsun" in idx else 0.0)
            t2m.append(float(row[idx["T2m"]]))
            ws.append(float(row[idx["WS10m"]]))

    # "20200101:1210" -> datetime64 UTC
    dt_utc = np.array([np.datetime64(f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[9:11]}:{s[11:13]}:00")
                       for s in times], dtype="datetime64[s]")

    # heure locale (Europe/Paris, changements d'heure inclus)
    off = np.array([
        datetime.fromtimestamp(int(t.astype("datetime64[s]").astype(np.int64)),
                               tz=timezone.utc).astimezone(TZ_LOCAL).utcoffset().total_seconds()
        for t in dt_utc], dtype=np.int64)
    dt_loc = dt_utc + off.astype("timedelta64[s]")

    m = dt_loc.astype("datetime64[M]").astype(int) % 12 + 1
    y = dt_loc.astype("datetime64[Y]").astype(int) + 1970
    day = dt_loc.astype("datetime64[D]")
    hour = ((dt_loc - day) / np.timedelta64(1, "h")).astype(int)

    from .solar import sun_position, extraterrestrial
    el, az = sun_position(dt_utc, lat, lon)

    _, day_index = np.unique(day, return_inverse=True)
    # Nombre d'annees equivalent : le decalage horaire fait deborder quelques
    # heures sur l'annee suivante, un comptage par valeurs distinctes surestime.
    n_years = len(gbh) / 8766.0

    return {
        "path": path,
        "dt_utc": dt_utc, "dt_loc": dt_loc,
        "Gbh": np.array(gbh), "Gdh": np.array(gdh),
        "T2m": np.array(t2m), "WS10m": np.array(ws),
        "Hsun_pvgis": np.array(hsun),
        "sun_el": el, "sun_az": az, "E0": extraterrestrial(dt_utc),
        "month": m, "year": y, "hour": hour,
        "day_index": day_index, "n_days": int(day_index.max()) + 1,
        "days": np.unique(day),
        "n_years": n_years, "n": len(gbh),
        "lat": lat, "lon": lon,
    }


def ensure_meteo(site: dict, allow_download=True, progress=None) -> dict:
    """Retourne la meteo du site : cache local, fichier par defaut, ou telechargement."""
    lat, lon = float(site["latitude"]), float(site["longitude"])
    y0, y1 = int(site["annee_debut"]), int(site["annee_fin"])
    db = site.get("base_donnees", "PVGIS-SARAH3")

    p = cache_path(lat, lon, y0, y1, db)
    trouve = trouver(os.path.basename(p))
    if trouve:
        return load_meteo(trouve, lat, lon)

    d = default_file()
    if os.path.exists(d) and abs(lat - 44.71) < 0.15 and abs(lon + 0.39) < 0.15:
        return load_meteo(d, lat, lon)

    if not allow_download:
        raise FileNotFoundError(
            "Aucune donnee en cache pour ce site. Utilisez l'onglet Site pour "
            "telecharger la meteo PVGIS.")
    download_pvgis(lat, lon, y0, y1, db, progress=progress, dest=p)
    return load_meteo(p, lat, lon)


def meteo_summary(meteo: dict) -> dict:
    ghi = meteo["Gbh"] + meteo["Gdh"]
    ny = meteo["n_years"]
    par_mois = np.zeros(12)
    for k in range(12):
        par_mois[k] = ghi[meteo["month"] == k + 1].sum() / 1000.0 / ny
    hdd = np.maximum(17.0 - meteo["T2m"], 0.0).sum() / 24.0 / ny
    return {
        "annees": f"{meteo['year'].min()}-{meteo['year'].max()} "
                  f"({meteo['n_years']:.1f} annees)",
        "n_heures": meteo["n"],
        "ghi_kwh_m2_an": float(ghi.sum() / 1000.0 / ny),
        "ghi_mensuel": par_mois.tolist(),
        "t_moy": float(meteo["T2m"].mean()),
        "t_min": float(meteo["T2m"].min()),
        "dju_17": float(hdd),
    }

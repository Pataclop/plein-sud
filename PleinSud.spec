# -*- mode: python ; coding: utf-8 -*-
"""Recette PyInstaller de Plein Sud.

    pyinstaller --noconfirm PleinSud.spec

Windows produit un executable unique dist/PleinSud.exe ; macOS produit un
paquet dist/PleinSud.app. Les series meteo livrees, l'icone et la
configuration d'exemple sont embarquees.
"""
import sys

MAC = sys.platform == "darwin"
ICONE = "assets/logo.icns" if MAC else "assets/logo.ico"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets/logo-256.png", "assets"),
        ("pv_sizer/data", "pv_sizer/data"),
        ("config_defaut.json", "."),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pandas", "scipy", "PyQt5", "PySide6", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if MAC:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PleinSud",
              debug=False, strip=False, upx=False, console=False,
              icon=ICONE)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                   name="PleinSud")
    app = BUNDLE(coll, name="PleinSud.app", icon=ICONE,
                 bundle_identifier="fr.pleinsud.simulateur",
                 info_plist={
                     "CFBundleName": "Plein Sud",
                     "CFBundleDisplayName": "Plein Sud",
                     "CFBundleShortVersionString": "1.0.0",
                     "NSHighResolutionCapable": True,
                 })
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PleinSud",
              debug=False, strip=False, upx=False, console=False,
              icon=ICONE)

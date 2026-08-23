"""Interface graphique PyQt6 du simulateur."""
from __future__ import annotations
import os, sys, copy, csv, traceback
import numpy as np

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox,
    QCheckBox, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QSplitter, QListWidget, QListWidgetItem, QMessageBox,
    QFileDialog, QProgressBar, QScrollArea, QTextEdit, QSizePolicy, QGridLayout)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from . import config as C
from . import meteo as M
from . import simulation as S

MOIS = S.MOIS
BLEU = "#1f4e79"


# ==========================================================================
# Widgets generiques
# ==========================================================================
class MonthsEditor(QWidget):
    """12 champs numeriques alignes sur une ligne."""

    def __init__(self, mn=0.0, mx=100.0, step=0.05, decimals=2):
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(2)
        self.spins = []
        for i, m in enumerate(MOIS):
            lab = QLabel(m)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            f = lab.font(); f.setPointSize(max(f.pointSize() - 2, 6)); lab.setFont(f)
            sp = QDoubleSpinBox()
            sp.setRange(mn, mx); sp.setSingleStep(step); sp.setDecimals(decimals)
            sp.setMinimumWidth(52); sp.setButtonSymbols(
                QDoubleSpinBox.ButtonSymbols.NoButtons)
            lay.addWidget(lab, 0, i)
            lay.addWidget(sp, 1, i)
            self.spins.append(sp)

    def value(self):
        return [s.value() for s in self.spins]

    def setValue(self, vals):
        for s, v in zip(self.spins, list(vals) + [0] * 12):
            s.setValue(float(v))


class SchemaForm(QWidget):
    """Construit automatiquement un formulaire a partir d'un schema."""

    def __init__(self, schema, on_change=None):
        super().__init__()
        self.widgets = {}
        self.on_change = on_change
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        form = None
        for key, label, typ, mn, mx, extra, tip in schema:
            if key.startswith("__grp"):
                box = QGroupBox(label)
                form = QFormLayout(box)
                outer.addWidget(box)
                continue
            if form is None:
                box = QGroupBox()
                form = QFormLayout(box)
                outer.addWidget(box)
            w = self._make(typ, mn, mx, extra)
            if tip:
                w.setToolTip(tip)
            self.widgets[key] = w
            form.addRow(label, w)
        outer.addStretch(1)

    def _make(self, typ, mn, mx, extra):
        if typ == "float":
            w = QDoubleSpinBox()
            w.setRange(float(mn), float(mx))
            w.setSingleStep(float(extra))
            d = max(0, min(5, len(str(extra).split(".")[-1]) if "." in str(extra) else 0))
            w.setDecimals(max(d, 1))
            w.valueChanged.connect(self._changed)
        elif typ == "int":
            w = QSpinBox(); w.setRange(int(mn), int(mx)); w.setSingleStep(int(extra))
            w.valueChanged.connect(self._changed)
        elif typ == "bool":
            w = QCheckBox(); w.stateChanged.connect(self._changed)
        elif typ == "choice":
            w = QComboBox(); w.addItems([str(x) for x in extra])
            w.currentIndexChanged.connect(self._changed)
        elif typ == "months":
            w = MonthsEditor(float(mn), float(mx), float(extra))
            for s in w.spins:
                s.valueChanged.connect(self._changed)
        else:
            w = QLineEdit(); w.textChanged.connect(self._changed)
        return w

    def _changed(self, *_):
        if self.on_change:
            self.on_change()

    def get(self) -> dict:
        out = {}
        for k, w in self.widgets.items():
            if isinstance(w, QCheckBox):
                out[k] = w.isChecked()
            elif isinstance(w, QComboBox):
                out[k] = w.currentText()
            elif isinstance(w, MonthsEditor):
                out[k] = w.value()
            elif isinstance(w, QLineEdit):
                out[k] = w.text()
            else:
                out[k] = w.value()
        return out

    def set(self, data: dict):
        for k, w in self.widgets.items():
            if k not in data:
                continue
            v = data[k]
            w.blockSignals(True)
            try:
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, QComboBox):
                    i = w.findText(str(v))
                    w.setCurrentIndex(i if i >= 0 else 0)
                elif isinstance(w, MonthsEditor):
                    w.setValue(v)
                elif isinstance(w, QLineEdit):
                    w.setText(str(v))
                else:
                    w.setValue(type(w.value())(v))
            finally:
                w.blockSignals(False)


class MplCanvas(FigureCanvasQTAgg):
    def __init__(self, w=7, h=4):
        self.fig = Figure(figsize=(w, h), tight_layout=True)
        super().__init__(self.fig)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def clear(self):
        self.fig.clear()


class Worker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, fn, *a, **kw):
        super().__init__()
        self.fn, self.a, self.kw = fn, a, kw

    def run(self):
        try:
            self.kw["progress"] = lambda p, m="": self.progress.emit(p, m)
            self.done.emit(self.fn(*self.a, **self.kw))
        except Exception:
            self.failed.emit(traceback.format_exc())


def table(headers, editable_cols=None, rows=0):
    t = QTableWidget(rows, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    t.setAlternatingRowColors(True)
    return t


def item(text, editable=False, align_right=False, bold=False):
    it = QTableWidgetItem(str(text))
    fl = it.flags()
    if not editable:
        fl &= ~Qt.ItemFlag.ItemIsEditable
    it.setFlags(fl)
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if bold:
        f = it.font(); f.setBold(True); it.setFont(f)
    return it


# ==========================================================================
# Onglet Consommation
# ==========================================================================
class PostesTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._loading = False
        lay = QHBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(split)

        left = QWidget(); ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Postes de consommation</b>"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.select)
        ll.addWidget(self.list)

        row = QHBoxLayout()
        self.combo_kind = QComboBox()
        for k, v in C.LOAD_KINDS.items():
            self.combo_kind.addItem(v["label"], k)
        row.addWidget(self.combo_kind)
        b_add = QPushButton("Ajouter"); b_add.clicked.connect(self.add)
        b_del = QPushButton("Supprimer"); b_del.clicked.connect(self.remove)
        row.addWidget(b_add); row.addWidget(b_del)
        ll.addLayout(row)
        self.lbl_total = QLabel("")
        self.lbl_total.setWordWrap(True)
        ll.addWidget(self.lbl_total)
        split.addWidget(left)

        right = QWidget(); self.rl = QVBoxLayout(right)
        head = QHBoxLayout()
        self.chk_actif = QCheckBox("Poste actif")
        self.chk_actif.stateChanged.connect(self.commit)
        self.edit_nom = QLineEdit()
        self.edit_nom.editingFinished.connect(self.commit)
        head.addWidget(QLabel("Nom :")); head.addWidget(self.edit_nom, 1)
        head.addWidget(self.chk_actif)
        self.rl.addLayout(head)
        self.lbl_help = QLabel(""); self.lbl_help.setWordWrap(True)
        self.lbl_help.setStyleSheet("color:#555;font-style:italic;")
        self.rl.addWidget(self.lbl_help)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.rl.addWidget(self.scroll, 1)
        self.form = None
        split.addWidget(right)
        split.setSizes([300, 620])

    def refresh(self):
        self._loading = True
        self.list.clear()
        for p in self.main.cfg["postes"]:
            it = QListWidgetItem(("  " if p.get("actif", True) else "  [off] ") + p["nom"])
            if not p.get("actif", True):
                it.setForeground(QColor("#999"))
            self.list.addItem(it)
        self._loading = False
        if self.main.cfg["postes"]:
            self.list.setCurrentRow(min(self.list.currentRow() if
                                        self.list.currentRow() >= 0 else 0,
                                        len(self.main.cfg["postes"]) - 1))

    def select(self, row):
        if row < 0 or row >= len(self.main.cfg["postes"]):
            return
        self._loading = True
        p = self.main.cfg["postes"][row]
        kind = C.LOAD_KINDS.get(p["kind"], C.LOAD_KINDS["generique"])
        self.edit_nom.setText(p["nom"])
        self.chk_actif.setChecked(p.get("actif", True))
        self.lbl_help.setText(kind["help"])
        self.form = SchemaForm(
            [("__grp", kind["label"], None, None, None, None, "")] + kind["params"],
            on_change=self.commit)
        self.form.set(p["params"])
        self.scroll.setWidget(self.form)
        self._loading = False

    def commit(self):
        if self._loading:
            return
        row = self.list.currentRow()
        if row < 0 or row >= len(self.main.cfg["postes"]):
            return
        p = self.main.cfg["postes"][row]
        p["nom"] = self.edit_nom.text() or p["nom"]
        p["actif"] = self.chk_actif.isChecked()
        if self.form:
            p["params"].update(self.form.get())
        self.list.item(row).setText(("  " if p["actif"] else "  [off] ") + p["nom"])
        self.main.mark_dirty()

    def add(self):
        kind = self.combo_kind.currentData()
        d = C.LOAD_KINDS[kind]
        self.main.cfg["postes"].append({
            "nom": d["label"], "kind": kind, "actif": True,
            "params": copy.deepcopy(d["defaults"])})
        self.refresh()
        self.list.setCurrentRow(len(self.main.cfg["postes"]) - 1)
        self.main.mark_dirty()

    def remove(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.main.cfg["postes"]):
            del self.main.cfg["postes"][r]
            self.refresh()
            self.main.mark_dirty()

    def show_totals(self, res):
        if not res:
            return
        ny = res["meteo"]["n_years"]
        lines = ["<b>Consommation simulee</b><br><table cellspacing=3>"]
        for nom, arr in res["detail_postes"].items():
            lines.append(f"<tr><td>{nom}</td><td align=right>"
                         f"<b>{arr.sum() / ny:,.0f}</b> kWh/an</td></tr>"
                         .replace(",", " "))
        k = res["kpi"]
        lines.append(f"<tr><td>Veille onduleurs</td><td align=right>"
                     f"{k['veille_an']:,.0f} kWh/an</td></tr>".replace(",", " "))
        lines.append(f"<tr><td><b>Total</b></td><td align=right><b>"
                     f"{k['besoin_an']:,.0f} kWh/an</b></td></tr>".replace(",", " "))
        lines.append(f"<tr><td>Moyenne</td><td align=right>"
                     f"{k['conso_jour_moy']:.1f} kWh/jour</td></tr>")
        lines.append(f"<tr><td>Journee maximale</td><td align=right>"
                     f"{k['conso_jour_max']:.1f} kWh</td></tr>")
        lines.append("</table>")
        self.lbl_total.setText("".join(lines))


# ==========================================================================
# Fenetre principale
# ==========================================================================
class MainWindow(QMainWindow):
    def __init__(self, cfg_path=None):
        super().__init__()
        self.setWindowTitle("Simulateur de dimensionnement PV + stockage")
        self.resize(1380, 880)
        self.cfg = C.default_config()
        self.cfg_path = cfg_path
        self.meteo = None
        self.res = None
        self.worker = None

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_site()
        self._build_champs()
        self.tab_postes = PostesTab(self)
        self.tabs.addTab(self.tab_postes, "3. Consommation")
        self._build_systeme()
        self._build_couts()
        self._build_resultats()
        self._build_optim()
        self._build_toolbar()

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)

        if cfg_path and os.path.exists(cfg_path):
            self.load_config(cfg_path)
        else:
            self.push_config()
        self.load_meteo(initial=True)

    # ---------------- barre d'outils ----------------
    def _build_toolbar(self):
        tb = self.addToolBar("Actions")
        tb.setMovable(False)
        for txt, slot, sc in [
                ("Nouveau", self.new_config, None),
                ("Ouvrir...", self.open_config, QKeySequence.StandardKey.Open),
                ("Enregistrer...", self.save_config, QKeySequence.StandardKey.Save),
                (None, None, None),
                ("SIMULER  (F5)", self.run_sim, "F5"),
                (None, None, None),
                ("Exporter CSV...", self.export_csv, None)]:
            if txt is None:
                tb.addSeparator(); continue
            a = QAction(txt, self)
            a.triggered.connect(slot)
            if sc:
                a.setShortcut(sc)
            tb.addAction(a)

    # ---------------- onglet 1 : site ----------------
    def _build_site(self):
        w = QWidget(); lay = QHBoxLayout(w)
        left = QWidget(); ll = QVBoxLayout(left)
        self.form_site = SchemaForm(
            [("__grp", "Implantation", None, None, None, None, "")] + C.SITE_SCHEMA,
            on_change=self.mark_dirty)
        ll.addWidget(self.form_site)
        self.form_module = SchemaForm(
            [("__grp", "Modules et pertes", None, None, None, None, "")] + C.MODULE_SCHEMA,
            on_change=self.mark_dirty)
        ll.addWidget(self.form_module)
        row = QHBoxLayout()
        b1 = QPushButton("Charger la meteo en cache")
        b1.clicked.connect(lambda: self.load_meteo())
        b2 = QPushButton("Telecharger depuis PVGIS")
        b2.clicked.connect(self.download_meteo)
        row.addWidget(b1); row.addWidget(b2)
        ll.addLayout(row)
        ll.addStretch(1)
        lay.addWidget(left, 0)

        right = QWidget(); rl = QVBoxLayout(right)
        self.txt_meteo = QTextEdit(); self.txt_meteo.setReadOnly(True)
        self.txt_meteo.setMaximumHeight(190)
        rl.addWidget(self.txt_meteo)
        self.cv_meteo = MplCanvas(7, 4)
        rl.addWidget(self.cv_meteo, 1)
        lay.addWidget(right, 1)
        self.tabs.addTab(w, "1. Site et meteo")

    # ---------------- onglet 2 : champs PV ----------------
    def _build_champs(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            "<b>Groupes de panneaux.</b> Un groupe = un ensemble de panneaux partageant "
            "la meme inclinaison. Azimut 180 = plein sud. Les inclinaisons sont "
            "transposees localement : aucun retelechargement n'est necessaire."))
        self.tbl_champs = table(["Actif", "Nom du groupe", "Nb panneaux", "Wc/panneau",
                                 "Inclinaison", "Azimut", "m2/panneau", "Ombrage %",
                                 "kWc", "kWh/kWc/an", "kWh/an"])
        self.tbl_champs.itemChanged.connect(self._champs_changed)
        lay.addWidget(self.tbl_champs, 1)
        row = QHBoxLayout()
        b1 = QPushButton("Ajouter un groupe"); b1.clicked.connect(self.add_champ)
        b2 = QPushButton("Supprimer le groupe"); b2.clicked.connect(self.del_champ)
        self.lbl_champs = QLabel("")
        row.addWidget(b1); row.addWidget(b2); row.addStretch(1); row.addWidget(self.lbl_champs)
        lay.addLayout(row)
        self.cv_champs = MplCanvas(9, 3.4)
        lay.addWidget(self.cv_champs, 1)
        self.tabs.addTab(w, "2. Champs PV")

    def _champs_changed(self, it):
        if getattr(self, "_loading_champs", False):
            return
        r, c = it.row(), it.column()
        if r >= len(self.cfg["champs"]):
            return
        ch = self.cfg["champs"][r]
        try:
            if c == 0:
                ch["actif"] = it.checkState() == Qt.CheckState.Checked
            elif c == 1:
                ch["nom"] = it.text()
            elif c == 2:
                ch["n_panneaux"] = max(int(float(it.text().replace(" ", ""))), 0)
            elif c == 3:
                ch["wc_panneau"] = float(it.text().replace(" ", ""))
            elif c == 4:
                ch["inclinaison"] = float(it.text().replace(",", "."))
            elif c == 5:
                ch["azimut"] = float(it.text().replace(",", "."))
            elif c == 6:
                ch["surface_m2_panneau"] = float(it.text().replace(",", "."))
            elif c == 7:
                ch["ombrage_pct"] = float(it.text().replace(",", "."))
        except ValueError:
            pass
        self.refresh_champs()
        self.mark_dirty()

    def refresh_champs(self):
        self._loading_champs = True
        t = self.tbl_champs
        t.setRowCount(len(self.cfg["champs"]))
        for r, ch in enumerate(self.cfg["champs"]):
            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if ch.get("actif", True)
                              else Qt.CheckState.Unchecked)
            t.setItem(r, 0, chk)
            for c, v in enumerate([ch["nom"], ch["n_panneaux"], ch["wc_panneau"],
                                   ch["inclinaison"], ch.get("azimut", 180),
                                   ch.get("surface_m2_panneau", 2.2),
                                   ch.get("ombrage_pct", 0)], start=1):
                t.setItem(r, c, item(v, editable=True, align_right=(c > 1)))
            kwc = ch["n_panneaux"] * ch["wc_panneau"] / 1000.0
            t.setItem(r, 8, item(f"{kwc:.2f}", align_right=True, bold=True))
            d = (self.res or {}).get("diag_champs", {}).get(ch["nom"], {})
            t.setItem(r, 9, item(f"{d.get('productible_kwh_kwc', 0):,.0f}".replace(",", " ")
                                 if d else "-", align_right=True))
            t.setItem(r, 10, item(f"{d.get('production_kwh_an', 0):,.0f}".replace(",", " ")
                                  if d else "-", align_right=True))
        self._loading_champs = False
        kwc = S.total_kwc(self.cfg)
        n = int(self.cfg["systeme"]["n_onduleurs"])
        lim = float(self.cfg["systeme"]["pv_max_kwc_par_onduleur"])
        col = "#b91c1c" if kwc / max(n, 1) > lim else "#15803d"
        self.lbl_champs.setText(
            f"<b>{S.total_panneaux(self.cfg)} panneaux &bull; {kwc:.2f} kWc &bull; "
            f"{S.surface_m2(self.cfg):.0f} m2</b> &nbsp; "
            f"<span style='color:{col}'>{kwc / max(n, 1):.1f} kWc/onduleur "
            f"(limite {lim:.1f})</span>")

    def add_champ(self):
        self.cfg["champs"].append({"nom": f"Champ {len(self.cfg['champs']) + 1}",
                                   "actif": True, "n_panneaux": 20, "wc_panneau": 500,
                                   "inclinaison": 40.0, "azimut": 180.0,
                                   "surface_m2_panneau": 2.2, "ombrage_pct": 0.0})
        self.refresh_champs(); self.mark_dirty()

    def del_champ(self):
        r = self.tbl_champs.currentRow()
        if 0 <= r < len(self.cfg["champs"]):
            del self.cfg["champs"][r]
            self.refresh_champs(); self.mark_dirty()

    # ---------------- onglet 4 : systeme ----------------
    def _build_systeme(self):
        w = QWidget(); lay = QHBoxLayout(w)
        sc = QScrollArea(); sc.setWidgetResizable(True)
        self.form_sys = SchemaForm(C.SYSTEM_SCHEMA, on_change=self.mark_dirty)
        sc.setWidget(self.form_sys)
        lay.addWidget(sc, 0)
        right = QWidget(); rl = QVBoxLayout(right)
        self.txt_sys = QTextEdit(); self.txt_sys.setReadOnly(True)
        rl.addWidget(self.txt_sys, 0)
        self.cv_soc = MplCanvas(7, 4)
        rl.addWidget(self.cv_soc, 1)
        lay.addWidget(right, 1)
        self.tabs.addTab(w, "4. Onduleurs et batterie")

    # ---------------- onglet 5 : couts ----------------
    def _build_couts(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            "<b>Nomenclature.</b> La colonne <i>Quantite auto</i> relie la ligne a la "
            "configuration : le nombre de panneaux, d'onduleurs, de cellules ou la "
            "capacite batterie se mettent a jour tout seuls."))
        self.tbl_bom = table(["Poste", "Quantite auto", "Qte (si fixe)", "Unite",
                              "Prix unitaire", "Quantite retenue", "Montant"])
        self.tbl_bom.itemChanged.connect(self._bom_changed)
        lay.addWidget(self.tbl_bom, 1)
        row = QHBoxLayout()
        b1 = QPushButton("Ajouter une ligne"); b1.clicked.connect(self.add_bom)
        b2 = QPushButton("Supprimer la ligne"); b2.clicked.connect(self.del_bom)
        row.addWidget(b1); row.addWidget(b2); row.addStretch(1)
        self.lbl_bom = QLabel(""); row.addWidget(self.lbl_bom)
        lay.addLayout(row)
        self.form_eco = SchemaForm(
            [("__grp", "Hypotheses economiques", None, None, None, None, "")] + C.ECO_SCHEMA,
            on_change=self.mark_dirty)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(self.form_eco)
        sc.setMaximumHeight(260)
        lay.addWidget(sc)
        self.txt_eco = QTextEdit(); self.txt_eco.setReadOnly(True)
        self.txt_eco.setMaximumHeight(210)
        lay.addWidget(self.txt_eco)
        self.tabs.addTab(w, "5. Couts")

    def _bom_changed(self, it):
        if getattr(self, "_loading_bom", False):
            return
        r, c = it.row(), it.column()
        if r >= len(self.cfg["bom"]):
            return
        l = self.cfg["bom"][r]
        try:
            if c == 0:
                l["poste"] = it.text()
            elif c == 2:
                l["qte"] = float(it.text().replace(",", ".").replace(" ", ""))
            elif c == 3:
                l["unite"] = it.text()
            elif c == 4:
                l["pu"] = float(it.text().replace(",", ".").replace(" ", ""))
        except ValueError:
            pass
        self.refresh_bom(); self.mark_dirty()

    def refresh_bom(self):
        self._loading_bom = True
        lignes, total = S.compute_bom(self.cfg)
        t = self.tbl_bom
        t.setRowCount(len(lignes))
        for r, l in enumerate(lignes):
            t.setItem(r, 0, item(l["poste"], editable=True))
            cb = QComboBox()
            for k, lab in C.AUTO_QTY.items():
                cb.addItem(lab, k)
            i = list(C.AUTO_QTY).index(l.get("auto", "fixe"))
            cb.setCurrentIndex(i)
            cb.currentIndexChanged.connect(
                lambda _i, row=r, box=None: self._set_auto(row, _i))
            t.setCellWidget(r, 1, cb)
            t.setItem(r, 2, item(l.get("qte", 0), editable=True, align_right=True))
            t.setItem(r, 3, item(l.get("unite", ""), editable=True))
            t.setItem(r, 4, item(f"{l.get('pu', 0):.2f}", editable=True, align_right=True))
            t.setItem(r, 5, item(f"{l['qte_calc']:,.1f}".replace(",", " "), align_right=True))
            t.setItem(r, 6, item(f"{l['montant']:,.0f} EUR".replace(",", " "),
                                 align_right=True, bold=True))
        self._loading_bom = False
        kwc = S.total_kwc(self.cfg)
        self.lbl_bom.setText(f"<b>Total : {total:,.0f} EUR</b> &nbsp;&bull;&nbsp; "
                             f"{total / max(kwc * 1000, 1):.2f} EUR/Wc"
                             .replace(",", " "))

    def _set_auto(self, row, idx):
        if getattr(self, "_loading_bom", False):
            return
        self.cfg["bom"][row]["auto"] = list(C.AUTO_QTY)[idx]
        self.refresh_bom(); self.mark_dirty()

    def add_bom(self):
        self.cfg["bom"].append({"poste": "Nouveau poste", "auto": "fixe",
                                "qte": 1, "pu": 0.0, "unite": "u"})
        self.refresh_bom(); self.mark_dirty()

    def del_bom(self):
        r = self.tbl_bom.currentRow()
        if 0 <= r < len(self.cfg["bom"]):
            del self.cfg["bom"][r]
            self.refresh_bom(); self.mark_dirty()

    # ---------------- onglet 6 : resultats ----------------
    def _build_resultats(self):
        w = QWidget(); lay = QVBoxLayout(w)
        self.lbl_kpi = QLabel("Appuyez sur F5 pour lancer la simulation.")
        self.lbl_kpi.setWordWrap(True)
        self.lbl_kpi.setStyleSheet(
            f"background:{BLEU};color:white;padding:9px;border-radius:4px;")
        lay.addWidget(self.lbl_kpi)
        self.lbl_alertes = QLabel(""); self.lbl_alertes.setWordWrap(True)
        lay.addWidget(self.lbl_alertes)

        sub = QTabWidget()
        # mensuel
        w1 = QWidget(); l1 = QVBoxLayout(w1)
        self.tbl_mois = table(["Mois", "Conso usages", "Veille", "Besoin total",
                               "Production", "Autoconso", "Import reseau",
                               "Ecrete/injecte", "Autonomie", "Conso/jour",
                               "Prod/jour", "Budget conso/jour"])
        l1.addWidget(self.tbl_mois)
        self.cv_mois = MplCanvas(9, 3.6)
        l1.addWidget(self.cv_mois, 1)
        sub.addTab(w1, "Bilan mensuel")
        # journalier
        w2 = QWidget(); l2 = QVBoxLayout(w2)
        row = QHBoxLayout()
        row.addWidget(QLabel("Mois :"))
        self.cb_mois = QComboBox(); self.cb_mois.addItems(MOIS)
        self.cb_mois.currentIndexChanged.connect(self.draw_jour)
        row.addWidget(self.cb_mois)
        row.addWidget(QLabel("Annee :"))
        self.cb_annee = QComboBox()
        self.cb_annee.currentIndexChanged.connect(self.draw_jour)
        row.addWidget(self.cb_annee); row.addStretch(1)
        l2.addLayout(row)
        self.tbl_jour = table(["Date", "Production", "Consommation", "Besoin",
                               "Import", "Autonomie", "SOC min"])
        l2.addWidget(self.tbl_jour, 1)
        self.cv_jour = MplCanvas(9, 3.2)
        l2.addWidget(self.cv_jour, 1)
        sub.addTab(w2, "Detail journalier")
        # profil horaire
        w3 = QWidget(); l3 = QVBoxLayout(w3)
        self.cv_profil = MplCanvas(9, 5)
        l3.addWidget(NavigationToolbar2QT(self.cv_profil, self))
        l3.addWidget(self.cv_profil, 1)
        sub.addTab(w3, "Journee type par mois")
        lay.addWidget(sub, 1)
        self.tabs.addTab(w, "6. Resultats")

    # ---------------- onglet 7 : optimisation ----------------
    def _build_optim(self):
        w = QWidget(); lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Parametre a balayer :"))
        self.cb_sweep = QComboBox()
        self.cb_sweep.addItem("Inclinaison de tous les champs (deg)", "inclinaison")
        self.cb_sweep.addItem("Inclinaison du 1er champ (deg)", "inclinaison_champ1")
        self.cb_sweep.addItem("Puissance PV totale (kWc)", "kwc")
        self.cb_sweep.addItem("Capacite batterie (kWh)", "batterie")
        self.cb_sweep.addItem("Nombre d'onduleurs", "onduleurs")
        row.addWidget(self.cb_sweep)
        row.addWidget(QLabel("Valeurs :"))
        self.ed_sweep = QLineEdit("20, 30, 40, 50, 60, 70, 80")
        row.addWidget(self.ed_sweep, 1)
        b = QPushButton("Lancer le balayage"); b.clicked.connect(self.run_sweep)
        row.addWidget(b)
        bb = QPushButton("Budget de consommation"); bb.clicked.connect(self.run_budget)
        row.addWidget(bb)
        lay.addLayout(row)
        self.tbl_sweep = table(["Valeur", "Autonomie", "Production", "Import reseau",
                                "Ecrete", "Investissement", "Retour (ans)"])
        lay.addWidget(self.tbl_sweep, 1)
        self.cv_sweep = MplCanvas(9, 4)
        lay.addWidget(self.cv_sweep, 1)
        self.tabs.addTab(w, "7. Optimisation")

    # ======================= configuration =======================
    def push_config(self):
        self.form_site.set(self.cfg["site"])
        self.form_module.set(self.cfg["module"])
        self.form_sys.set(self.cfg["systeme"])
        self.form_eco.set(self.cfg["economie"])
        self.refresh_champs()
        self.refresh_bom()
        self.tab_postes.refresh()

    def pull_config(self):
        self.cfg["site"].update(self.form_site.get())
        self.cfg["module"].update(self.form_module.get())
        self.cfg["systeme"].update(self.form_sys.get())
        self.cfg["economie"].update(self.form_eco.get())
        self.tab_postes.commit()

    def mark_dirty(self, *_):
        self.statusBar().showMessage("Configuration modifiee - F5 pour resimuler", 2500)

    def new_config(self):
        self.cfg = C.default_config()
        self.push_config()
        self.run_sim()

    def open_config(self):
        p, _ = QFileDialog.getOpenFileName(self, "Ouvrir une configuration",
                                           "", "JSON (*.json)")
        if p:
            self.load_config(p)

    def load_config(self, path):
        try:
            self.cfg = C.load_config(path)
            self.cfg_path = path
            self.push_config()
            self.statusBar().showMessage(f"Configuration chargee : {path}", 4000)
            self.load_meteo(initial=True)
        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Lecture impossible :\n{e}")

    def save_config(self):
        self.pull_config()
        p, _ = QFileDialog.getSaveFileName(self, "Enregistrer la configuration",
                                           self.cfg_path or "config.json", "JSON (*.json)")
        if p:
            C.save_config(self.cfg, p)
            self.cfg_path = p
            self.statusBar().showMessage(f"Enregistre : {p}", 4000)

    # ======================= meteo =======================
    def load_meteo(self, initial=False):
        self.pull_config()
        try:
            self.meteo = M.ensure_meteo(self.cfg["site"], allow_download=False)
        except Exception as e:
            self.txt_meteo.setHtml(
                f"<p style='color:#b91c1c'><b>Aucune donnee locale.</b><br>{e}</p>"
                "<p>Utilisez le bouton <b>Telecharger depuis PVGIS</b>.</p>")
            return
        self.show_meteo()
        self.run_sim()

    def download_meteo(self):
        self.pull_config()
        s = self.cfg["site"]
        self._start(Worker(M.download_pvgis, float(s["latitude"]), float(s["longitude"]),
                           int(s["annee_debut"]), int(s["annee_fin"]),
                           s.get("base_donnees", "PVGIS-SARAH3")),
                    self._meteo_ready, "Telechargement PVGIS")

    def _meteo_ready(self, path):
        self.meteo = M.load_meteo(path, float(self.cfg["site"]["latitude"]),
                                  float(self.cfg["site"]["longitude"]))
        self.show_meteo()
        self.run_sim()

    def show_meteo(self):
        s = M.meteo_summary(self.meteo)
        self.txt_meteo.setHtml(
            f"<b>Serie chargee</b> : {os.path.basename(self.meteo['path'])}<br>"
            f"Periode {s['annees']} &bull; {s['n_heures']:,} heures<br>"
            f"Rayonnement horizontal global : <b>{s['ghi_kwh_m2_an']:,.0f} kWh/m2/an</b><br>"
            f"Temperature moyenne {s['t_moy']:.1f} C, minimale {s['t_min']:.1f} C<br>"
            f"Degres-jours base 17 C : <b>{s['dju_17']:,.0f}</b><br>"
            f"<i>Les composantes sont horizontales : toute inclinaison est calculee "
            f"localement, sans nouveau telechargement.</i>".replace(",", " "))
        c = self.cv_meteo; c.clear()
        ax = c.fig.add_subplot(111)
        ax.bar(MOIS, s["ghi_mensuel"], color="#fbbf24")
        ax.set_ylabel("kWh/m2/mois"); ax.set_title("Rayonnement horizontal mensuel moyen")
        ax2 = ax.twinx()
        tm = [self.meteo["T2m"][self.meteo["month"] == k + 1].mean() for k in range(12)]
        ax2.plot(MOIS, tm, color="#b91c1c", marker="o")
        ax2.set_ylabel("Temperature moyenne (C)", color="#b91c1c")
        c.draw()

    # ======================= simulation =======================
    def _start(self, worker, on_done, label):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Patientez", "Un calcul est deja en cours.")
            return
        self.worker = worker
        self.progress.setVisible(True); self.progress.setValue(0)
        self.statusBar().showMessage(label)
        worker.progress.connect(lambda p, m: (self.progress.setValue(p),
                                              self.statusBar().showMessage(f"{label} - {m}")))
        worker.done.connect(lambda r: (self.progress.setVisible(False), on_done(r)))
        worker.failed.connect(self._failed)
        worker.start()

    def _failed(self, tb):
        self.progress.setVisible(False)
        QMessageBox.critical(self, "Erreur de calcul", tb[-2500:])

    def run_sim(self):
        if self.meteo is None:
            self.statusBar().showMessage("Chargez d'abord une serie meteo.", 4000)
            return
        self.pull_config()
        try:
            self.res = S.simulate(self.cfg, self.meteo)
        except Exception:
            QMessageBox.critical(self, "Erreur", traceback.format_exc()[-2500:])
            return
        self.show_results()

    def show_results(self):
        r, k, e = self.res, self.res["kpi"], self.res["eco"]
        f = lambda v, d=0: f"{v:,.{d}f}".replace(",", " ")
        self.lbl_kpi.setText(
            f"<table width='100%'><tr>"
            f"<td><span style='font-size:20pt'><b>{100 * k['autonomie']:.1f} %</b></span>"
            f"<br><small>AUTONOMIE ANNUELLE</small></td>"
            f"<td><span style='font-size:20pt'><b>{f(k['import_an'])}</b></span>"
            f"<br><small>kWh SOUTIRES / AN</small></td>"
            f"<td><span style='font-size:20pt'><b>{f(k['besoin_an'])}</b></span>"
            f"<br><small>kWh CONSOMMES / AN</small></td>"
            f"<td><span style='font-size:20pt'><b>{f(k['production_an'])}</b></span>"
            f"<br><small>kWh PRODUITS / AN</small></td>"
            f"<td><span style='font-size:20pt'><b>{f(e['capex'])} EUR</b></span>"
            f"<br><small>INVESTISSEMENT</small></td>"
            f"<td><span style='font-size:20pt'><b>"
            f"{e['retour_ans_vs_actuel'] or '>' + str(self.cfg['economie']['duree_analyse_ans'])} ans</b>"
            f"</span><br><small>RETOUR / FACTURE ACTUELLE</small></td>"
            f"</tr></table>")

        col = {"erreur": "#b91c1c", "attention": "#d97706", "info": "#1f4e79"}
        self.lbl_alertes.setText("<br>".join(
            f"<span style='color:{col[t]}'><b>{t.upper()}</b> &mdash; {m}</span>"
            for t, m in r["alertes"]) or
            "<span style='color:#15803d'><b>Aucune incoherence detectee.</b></span>")

        m = r["mensuel"]
        budget = getattr(self, "_budget", None)
        t = self.tbl_mois; t.setRowCount(13)
        for i in range(12):
            vals = [MOIS[i], m["consommation"][i], m["veille"][i], m["besoin"][i],
                    m["production_dc"][i], m["autoconso"][i], m["import"][i],
                    m["ecrete"][i] + m["export"][i]]
            t.setItem(i, 0, item(vals[0], bold=True))
            for c, v in enumerate(vals[1:], start=1):
                t.setItem(i, c, item(f(v), align_right=True))
            a = m["autonomie"][i]
            it = item(f"{100 * a:.1f} %", align_right=True, bold=True)
            it.setForeground(QColor("#15803d" if a > .9 else
                                    "#d97706" if a > .7 else "#b91c1c"))
            t.setItem(i, 8, it)
            t.setItem(i, 9, item(f"{m['conso_jour'][i]:.1f}", align_right=True))
            t.setItem(i, 10, item(f"{m['prod_jour'][i]:.1f}", align_right=True))
            t.setItem(i, 11, item(f"{budget[i][1]:.1f}" if budget else "-",
                                  align_right=True))
        tot = [m["consommation"].sum(), m["veille"].sum(), m["besoin"].sum(),
               m["production_dc"].sum(), m["autoconso"].sum(), m["import"].sum(),
               (m["ecrete"] + m["export"]).sum()]
        t.setItem(12, 0, item("ANNEE", bold=True))
        for c, v in enumerate(tot, start=1):
            t.setItem(12, c, item(f(v), align_right=True, bold=True))
        t.setItem(12, 8, item(f"{100 * k['autonomie']:.1f} %", align_right=True, bold=True))
        for c in (9, 10, 11):
            t.setItem(12, c, item("", align_right=True))

        self.draw_mois()
        self.draw_profil()
        self.refresh_champs()
        self.refresh_bom()
        self.tab_postes.show_totals(r)
        self.show_sys()
        self.show_eco()
        years = sorted(set(int(x) for x in np.unique(self.meteo["year"])))
        cur = self.cb_annee.currentText()
        self.cb_annee.blockSignals(True)
        self.cb_annee.clear(); self.cb_annee.addItems([str(y) for y in years])
        if cur in [str(y) for y in years]:
            self.cb_annee.setCurrentText(cur)
        self.cb_annee.blockSignals(False)
        self.draw_jour()
        self.statusBar().showMessage(
            f"Simulation terminee sur {k['n_years']:.0f} annees de meteo reelle.", 6000)

    def draw_mois(self):
        m = self.res["mensuel"]
        c = self.cv_mois; c.clear()
        ax = c.fig.add_subplot(121)
        x = np.arange(12)
        ax.bar(x, m["autoconso"], .6, label="Autoconsomme", color="#15803d")
        ax.bar(x, m["import"], .6, bottom=m["autoconso"], label="Soutire", color="#b91c1c")
        ax.plot(x, m["production_dc"], color="#d97706", marker="o", ms=3,
                lw=2, label="Production")
        ax.set_xticks(x); ax.set_xticklabels(MOIS, fontsize=7)
        ax.set_ylabel("kWh/mois"); ax.legend(fontsize=7, frameon=False)
        ax.set_title("Bilan mensuel", fontsize=9)
        ax2 = c.fig.add_subplot(122)
        cols = ["#15803d" if a > .9 else "#d97706" if a > .7 else "#b91c1c"
                for a in m["autonomie"]]
        ax2.bar(x, 100 * m["autonomie"], .6, color=cols)
        ax2.axhline(100 * float(self.cfg["options"].get("autonomie_cible", .92)),
                    color=BLEU, ls="--", lw=1)
        ax2.set_xticks(x); ax2.set_xticklabels(MOIS, fontsize=7)
        ax2.set_ylim(0, 105); ax2.set_ylabel("%")
        ax2.set_title("Autonomie mensuelle", fontsize=9)
        c.draw()

    def draw_jour(self):
        if not self.res:
            return
        j = self.res["journalier"]
        try:
            year = int(self.cb_annee.currentText())
        except (ValueError, TypeError):
            return
        mo = self.cb_mois.currentIndex() + 1
        dates = j["date"].astype("datetime64[D]")
        yy = dates.astype("datetime64[Y]").astype(int) + 1970
        mm = dates.astype("datetime64[M]").astype(int) % 12 + 1
        sel = np.where((yy == year) & (mm == mo))[0]
        t = self.tbl_jour; t.setRowCount(len(sel))
        f = lambda v, d=1: f"{v:,.{d}f}".replace(",", " ")
        for r, i in enumerate(sel):
            t.setItem(r, 0, item(str(dates[i])))
            for c, v in enumerate([j["production"][i], j["consommation"][i],
                                   j["besoin"][i], j["import"][i]], start=1):
                t.setItem(r, c, item(f(v), align_right=True))
            a = j["autonomie"][i]
            it = item(f"{100 * a:.0f} %", align_right=True, bold=True)
            it.setForeground(QColor("#15803d" if a > .9 else
                                    "#d97706" if a > .7 else "#b91c1c"))
            t.setItem(r, 5, it)
            t.setItem(r, 6, item(f(j["soc_min"][i]), align_right=True))
        c = self.cv_jour; c.clear()
        ax = c.fig.add_subplot(111)
        d = np.arange(1, len(sel) + 1)
        ax.bar(d, j["production"][sel], .7, color="#fbbf24", label="Production")
        ax.plot(d, j["besoin"][sel], color=BLEU, lw=1.6, marker="o", ms=3,
                label="Besoin")
        ax.bar(d, j["import"][sel], .35, color="#b91c1c", label="Soutirage")
        ax.set_xlabel(f"Jour de {MOIS[mo - 1]} {year}"); ax.set_ylabel("kWh/jour")
        ax.legend(fontsize=7, frameon=False)
        c.draw()

    def draw_profil(self):
        r = self.res
        met = self.meteo
        c = self.cv_profil; c.clear()
        axes = c.fig.subplots(3, 4, sharex=True)
        d = r["dispatch"]
        for k in range(12):
            ax = axes[k // 4][k % 4]
            sel = met["month"] == k + 1
            prof_p = [r["pv_dc"][sel & (met["hour"] == h)].mean() for h in range(24)]
            prof_c = [d["besoin_total"][sel & (met["hour"] == h)].mean() for h in range(24)]
            prof_i = [d["import"][sel & (met["hour"] == h)].mean() for h in range(24)]
            ax.fill_between(range(24), prof_p, color="#fbbf24", alpha=.8)
            ax.plot(range(24), prof_c, color=BLEU, lw=1.4)
            ax.fill_between(range(24), prof_i, color="#b91c1c", alpha=.6)
            ax.set_title(MOIS[k], fontsize=8)
            ax.tick_params(labelsize=6)
        c.fig.suptitle("Journee moyenne : production (jaune), besoin (bleu), "
                       "soutirage (rouge) - kW", fontsize=9)
        c.draw()

    def show_sys(self):
        k, s = self.res["kpi"], self.cfg["systeme"]
        d = self.res["dispatch"]
        f = lambda v, n=0: f"{v:,.{n}f}".replace(",", " ")
        self.txt_sys.setHtml(
            f"<b>Onduleurs</b> : {s['n_onduleurs']} x {s['p_nom_kw']:.0f} kW = "
            f"<b>{s['n_onduleurs'] * s['p_nom_kw']:.0f} kW AC</b> &bull; "
            f"{k['pv_par_onduleur_kwc']:.1f} kWc/onduleur<br>"
            f"Consommation a vide cumulee : <b>{f(k['veille_an'])} kWh/an</b> "
            f"({100 * k['veille_an'] / max(k['besoin_an'], 1):.1f} % du besoin)<br>"
            f"Puissance PV DC maximale atteinte : {self.res['pv_dc'].max():.1f} kW "
            f"(soit {100 * self.res['pv_dc'].max() / max(k['kwc'], 1e-9):.0f} % du crete)<br>"
            f"<b>Batterie</b> : {s['batt_kwh_nominal']:.1f} kWh nominaux, "
            f"<b>{d['utile']:.1f} kWh utiles</b><br>"
            f"Energie restituee : {f(d['decharge'].sum() / k['n_years'])} kWh/an &bull; "
            f"<b>{k['cycles_batterie_an']:.0f} cycles pleins/an</b> &bull; "
            f"duree de vie estimee {6000 / max(k['cycles_batterie_an'], 1):.0f} ans<br>"
            f"Etat de charge minimal atteint : {d['soc'].min():.1f} kWh &bull; "
            f"maximal {d['soc'].max():.1f} kWh<br>"
            f"<b>Reseau</b> : soutirage {f(k['import_an'])} kWh/an &bull; "
            f"ecrete {f(k['ecrete_an'])} kWh/an &bull; "
            f"injecte {f(k['export_an'])} kWh/an")
        c = self.cv_soc; c.clear()
        ax = c.fig.add_subplot(111)
        soc = d["soc"]
        n = len(soc)
        step = max(n // 4000, 1)
        ax.plot(np.arange(0, n, step) / 24.0, soc[::step], lw=.5, color=BLEU)
        ax.axhline(d["soc_min"], color="#b91c1c", ls="--", lw=1)
        ax.axhline(d["soc_max"], color="#15803d", ls="--", lw=1)
        ax.set_xlabel("Jour de la serie"); ax.set_ylabel("Etat de charge (kWh)")
        ax.set_title("Etat de charge de la batterie sur toute la serie", fontsize=9)
        c.draw()

    def show_eco(self):
        e = self.res["eco"]; k = self.res["kpi"]
        f = lambda v, n=0: f"{v:,.{n}f}".replace(",", " ")
        horizon = int(self.cfg["economie"]["duree_analyse_ans"])
        self.txt_eco.setHtml(
            f"<table cellpadding=3>"
            f"<tr><td>Investissement total</td><td align=right><b>{f(e['capex'])} EUR</b>"
            f"</td><td>{e['cout_par_wc']:.2f} EUR/Wc</td></tr>"
            f"<tr><td>Facture actuelle declaree</td><td align=right>"
            f"{f(e['facture_actuelle'])} EUR/an</td><td></td></tr>"
            f"<tr><td>Cout annuel tout-electrique SANS PV</td><td align=right>"
            f"{f(e['cout_annuel_sans_pv'])} EUR/an</td><td></td></tr>"
            f"<tr><td>Cout annuel AVEC PV et batterie</td><td align=right><b>"
            f"{f(e['cout_annuel_avec_pv'])} EUR/an</b></td>"
            f"<td>dont bois {f(e['cout_bois'])} EUR ({e['steres']:.1f} steres)</td></tr>"
            f"<tr><td>Economie vs facture actuelle</td><td align=right><b>"
            f"{f(e['economie_vs_actuel'])} EUR/an</b></td>"
            f"<td>retour {e['retour_ans_vs_actuel'] or '>' + str(horizon)} ans</td></tr>"
            f"<tr><td>Economie vs tout-electrique sans PV</td><td align=right>"
            f"{f(e['economie_vs_sans_pv'])} EUR/an</td>"
            f"<td>retour {e['retour_ans_vs_sans_pv'] or '>' + str(horizon)} ans</td></tr>"
            f"<tr><td>Gain cumule a {horizon} ans</td><td align=right><b>"
            f"{f(e['gain_cumule_horizon'])} EUR</b></td><td></td></tr>"
            f"<tr><td>Cout du kWh autoproduit utilise</td><td align=right>"
            f"{e['lcoe_kwh_utile']:.3f} EUR/kWh</td>"
            f"<td>reseau : {self.cfg['economie']['prix_kwh_achat']:.3f} EUR/kWh</td></tr>"
            f"<tr><td>Energie perdue faute d'usage</td><td align=right>"
            f"{f(e['kwh_perdus_an'])} kWh/an</td>"
            f"<td>soit {f(e['valeur_perdue_an'])} EUR/an de valeur potentielle</td></tr>"
            f"</table>")

    # ======================= balayages =======================
    def run_sweep(self):
        if self.meteo is None:
            return
        self.pull_config()
        var = self.cb_sweep.currentData()
        try:
            vals = [float(x) for x in self.ed_sweep.text().replace(";", ",").split(",")
                    if x.strip()]
        except ValueError:
            QMessageBox.warning(self, "Valeurs", "Saisissez des nombres separes par des virgules.")
            return
        if not vals:
            return
        self._start(Worker(S.sweep, self.cfg, self.meteo, var, vals),
                    lambda out: self.show_sweep(var, out), "Balayage")

    def show_sweep(self, var, out):
        t = self.tbl_sweep; t.setRowCount(len(out))
        f = lambda v, n=0: f"{v:,.{n}f}".replace(",", " ")
        best = max(range(len(out)), key=lambda i: out[i]["autonomie"])
        for r, o in enumerate(out):
            t.setItem(r, 0, item(f"{o['valeur']:g}", bold=(r == best)))
            t.setItem(r, 1, item(f"{100 * o['autonomie']:.2f} %", align_right=True,
                                 bold=(r == best)))
            for c, key in enumerate(["production", "import", "ecrete", "capex"], start=2):
                t.setItem(r, c, item(f(o[key]), align_right=True))
            t.setItem(r, 6, item(o["retour"] if o["retour"] else "-", align_right=True))
        c = self.cv_sweep; c.clear()
        ax = c.fig.add_subplot(111)
        x = [o["valeur"] for o in out]
        ax.plot(x, [100 * o["autonomie"] for o in out], marker="o", color=BLEU,
                lw=2, label="Autonomie")
        ax.set_xlabel(self.cb_sweep.currentText()); ax.set_ylabel("Autonomie (%)")
        ax.grid(alpha=.3, ls=":")
        ax.scatter([x[best]], [100 * out[best]["autonomie"]], s=140,
                   facecolors="none", edgecolors="#15803d", lw=2, zorder=5)
        ax2 = ax.twinx()
        ax2.plot(x, [o["production"] for o in out], color="#d97706", ls="--",
                 label="Production kWh/an")
        ax2.plot(x, [o["ecrete"] for o in out], color="#94a3b8", ls=":",
                 label="Ecrete kWh/an")
        ax2.set_ylabel("kWh/an")
        h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=8, frameon=False, loc="lower right")
        ax.set_title(f"Optimum : {x[best]:g} -> {100 * out[best]['autonomie']:.2f} % "
                     f"d'autonomie", fontsize=10, color=BLEU)
        c.draw()
        self.tabs.setCurrentIndex(6)

    def run_budget(self):
        if not self.res:
            return
        cible = float(self.cfg["options"].get("autonomie_cible", .92))

        def job(progress=None):
            return S.budget_consommation(self.cfg, self.meteo, self.res, cible)

        self._start(Worker(job), self._budget_ready,
                    f"Budget de consommation pour {100 * cible:.0f} % d'autonomie")

    def _budget_ready(self, budget):
        self._budget = budget
        self.show_results()
        self.tabs.setCurrentIndex(5)
        QMessageBox.information(
            self, "Budget de consommation",
            "La derniere colonne du bilan mensuel indique la consommation "
            "journaliere maximale compatible avec l'objectif d'autonomie, "
            "a installation constante.")

    # ======================= export =======================
    def export_csv(self):
        if not self.res:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Exporter les resultats",
                                           "resultats_pv.csv", "CSV (*.csv)")
        if not p:
            return
        m = self.res["mensuel"]
        with open(p, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["Mois", "Conso usages kWh", "Veille kWh", "Besoin kWh",
                        "Production kWh", "Autoconso kWh", "Import kWh",
                        "Ecrete kWh", "Autonomie %", "Conso/jour kWh", "Prod/jour kWh"])
            for i in range(12):
                w.writerow([MOIS[i]] + [round(m[key][i], 1) for key in
                                        ["consommation", "veille", "besoin",
                                         "production_dc", "autoconso", "import", "ecrete"]] +
                           [round(100 * m["autonomie"][i], 2),
                            round(m["conso_jour"][i], 2), round(m["prod_jour"][i], 2)])
            w.writerow([])
            w.writerow(["Indicateur", "Valeur"])
            for k, v in self.res["kpi"].items():
                w.writerow([k, round(v, 3) if isinstance(v, float) else v])
            for k, v in self.res["eco"].items():
                w.writerow([k, round(v, 2) if isinstance(v, float) else v])
            w.writerow([])
            w.writerow(["Jour", "Production", "Consommation", "Besoin", "Import", "Autonomie %"])
            j = self.res["journalier"]
            for i in range(len(j["date"])):
                w.writerow([str(j["date"][i].astype("datetime64[D]")),
                            round(j["production"][i], 2), round(j["consommation"][i], 2),
                            round(j["besoin"][i], 2), round(j["import"][i], 2),
                            round(100 * j["autonomie"][i], 1)])
        self.statusBar().showMessage(f"Exporte : {p}", 5000)


def run(cfg_path=None):
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow(cfg_path)
    win.show()
    sys.exit(app.exec())

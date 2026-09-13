"""Interface graphique PyQt6 du simulateur."""
from __future__ import annotations
import os, re, sys, copy, csv, traceback
import numpy as np

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox,
    QCheckBox, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QSplitter, QListWidget, QListWidgetItem, QMessageBox,
    QFileDialog, QProgressBar, QScrollArea, QTextEdit, QSizePolicy, QGridLayout,
    QToolTip, QDialog, QDialogButtonBox)

import matplotlib
import matplotlib.colors
import matplotlib.patches
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from . import config as C
from . import meteo as M
from . import simulation as S

MOIS = S.MOIS
BLEU = "#1f4e79"
ROUGE = "#b91c1c"
ORANGE = "#d97706"
VERT = "#15803d"

CH_COLS = C.CHAMPS_COLONNES
CH_IDX = {k: i for i, (k, _lab, _tip) in enumerate(CH_COLS) if k}
CH_INT = {"n_panneaux", "n_serie"}
CH_TEXTE = {"nom"}

HINT = ("<br><i>Cliquez sur le graphique pour l'ouvrir en plein ecran "
        "(legende, valeurs et statistiques de toutes les courbes).</i>")


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
            lab.setToolTip(f"Valeur du mois de {m}")
            sp.setToolTip(f"Valeur du mois de {m}")
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
            lab = QLabel(label)
            # pas de retour a la ligne : QFormLayout rogne les libelles
            # multilignes au lieu d'agrandir la rangee
            lab.setWordWrap(False)
            if tip:
                # l'infobulle suit le libelle ET le champ : on survole ce qu'on veut
                w.setToolTip(tip)
                lab.setToolTip(tip)
                lab.setText(label + " <span style='color:#94a3b8'>&#9432;</span>")
            self.widgets[key] = w
            form.addRow(lab, w)
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
            for i, x in enumerate(extra):
                detail = C.SHAPES_HELP.get(str(x))
                if detail is None and str(x) in C.PVGIS_DATABASES:
                    d = C.PVGIS_DATABASES[str(x)]
                    detail = (f"{d['resume']}<br>Annees {d['annees'][0]} a "
                              f"{d['annees'][1]}<br>{d['detail']}")
                if detail:
                    w.setItemData(i, detail, Qt.ItemDataRole.ToolTipRole)
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
    """Canevas matplotlib interactif.

    - survol : reticule + infobulle listant toutes les series de l'axe ;
    - clic n'importe ou dans le graphique : ouverture en plein ecran avec
      legende, valeurs sous le curseur et statistiques de chaque courbe.
    """

    def __init__(self, w=7, h=4, plein_ecran=True):
        self.fig = Figure(figsize=(w, h), tight_layout=True)
        super().__init__(self.fig)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._hover = {}
        self._hover2d = {}
        self._click_handler = None
        self._plot_fn = None
        self._titre = ""
        self._hover_listener = None
        self._plein_ecran = plein_ecran
        self._bg = None
        self._curseurs = {}
        self._hint = None
        self._timer_clic = QTimer(self)
        self._timer_clic.setSingleShot(True)
        self._timer_clic.timeout.connect(self.ouvrir_plein_ecran)
        self.setMouseTracking(True)
        self.mpl_connect("motion_notify_event", self._on_move)
        self.mpl_connect("button_press_event", self._on_click)
        self.mpl_connect("draw_event", self._on_draw)
        self.mpl_connect("figure_leave_event", self._on_leave)

    # ------------------------------------------------------------------
    # declaration du contenu
    # ------------------------------------------------------------------
    def set_click_handler(self, fn):
        """Action du double-clic (le simple clic ouvre le plein ecran)."""
        self._click_handler = fn

    def set_plot(self, fn, titre=""):
        """Fonction de trace fn(canevas), rejouee dans la fenetre plein ecran."""
        self._plot_fn = fn
        self._titre = titre or self._titre
        if fn is not None and self._plein_ecran:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_hover_listener(self, fn):
        """Callback recevant le HTML des valeurs sous le curseur ('' si dehors)."""
        self._hover_listener = fn

    def clear(self):
        self.fig.clear()
        self._hover = {}
        self._hover2d = {}
        self._curseurs = {}
        self._hint = None
        self._bg = None

    def hover(self, axes, x, series, xfmt=None, titre="", cumul=True, sur="x"):
        """Declare les valeurs lisibles au survol.

        axes   : un axe matplotlib ou une liste d'axes superposes (twinx)
        x      : abscisses des points, dans les unites de l'axe
        series : liste de (libelle, valeurs, unite, decimales[, couleur])
        xfmt   : fonction indice -> texte, pour l'en-tete de l'infobulle
        cumul  : True si la somme des valeurs a un sens (axe temporel)
        sur    : "x" pour un graphique vertical, "y" pour des barres
                 horizontales (les abscisses sont alors lues sur l'axe Y)
        """
        payload = (np.asarray(x, dtype=float), series, xfmt, titre, cumul, sur)
        for ax in (axes if isinstance(axes, (list, tuple)) else [axes]):
            self._hover[ax] = payload

    def hover2d(self, ax, x, y, z, libelles, titre=""):
        """Survol d'une carte : libelles = (nom_x, nom_y, nom_z, unite, decimales)."""
        self._hover2d[ax] = (np.asarray(x, dtype=float), np.asarray(y, dtype=float),
                             np.asarray(z, dtype=float), libelles, titre)

    # ------------------------------------------------------------------
    # mise en forme
    # ------------------------------------------------------------------
    def draw(self):
        if self._plot_fn is not None and self._plein_ecran and self._hint is None:
            self._hint = self.fig.text(
                0.995, 0.005, "clic : plein ecran", ha="right", va="bottom",
                fontsize=6.5, color="#94a3b8", style="italic")
        super().draw()

    def completer_legendes(self, taille=8):
        """Ajoute une legende a chaque axe qui n'en a pas mais qui en merite une.

        Au-dela de quatre sous-graphes, une legende unique est posee sur la
        figure : elle ne recouvre plus les courbes.
        """
        axes = [ax for ax in self.fig.axes if ax.get_legend_handles_labels()[0]]
        if len(self.fig.axes) > 4:
            # une legende de figure, uniquement pour les series qui ne sont
            # pas deja nommees dans la legende d'un axe
            vus, hh, ll = set(), [], []
            for a in axes:
                if a.get_legend() is not None:
                    vus.update(t.get_text() for t in a.get_legend().get_texts())
            for ax in [a for a in axes if a.get_legend() is None]:
                for h, l in zip(*ax.get_legend_handles_labels()):
                    if str(l).startswith("_") or l in vus:
                        continue
                    vus.add(l); hh.append(h); ll.append(l)
            if hh:
                self.fig.legend(hh, ll, loc="outside lower center", frameon=False,
                                fontsize=taille, ncol=min(len(hh), 5))
            return
        for ax in axes:
            if ax.get_legend() is not None:
                ax.get_legend().set_visible(True)
                continue
            h, l = ax.get_legend_handles_labels()
            if h and any(not str(x).startswith("_") for x in l):
                ax.legend(fontsize=taille, frameon=False,
                          ncol=2 if len(h) > 6 else 1)

    # ------------------------------------------------------------------
    # statistiques de toutes les courbes
    # ------------------------------------------------------------------
    def statistiques(self):
        """Min / moyenne / max / total de chaque serie declaree, sans doublon."""
        out, vus = [], set()
        for _ax, (x, series, xfmt, titre, cumul, _sur) in self._hover.items():
            for s in series:
                lab, vals, unite, dec = s[0], s[1], s[2], s[3]
                coul = s[4] if len(s) > 4 else None
                nom = re.sub("<[^>]+>", "", str(lab))
                v = np.asarray(vals, dtype=float).ravel()
                # une meme serie declaree sur deux axes (twinx, sous-graphe
                # voisin) ne doit apparaitre qu'une fois
                cle = (nom, unite, v.tobytes())
                if cle in vus:
                    continue
                vus.add(cle)
                ok = np.isfinite(v)
                if not ok.any():
                    continue
                idx = np.where(ok)[0]
                imin = int(idx[np.argmin(v[idx])])
                imax = int(idx[np.argmax(v[idx])])

                def etiq(i):
                    if callable(xfmt):
                        try:
                            return str(xfmt(i))
                        except Exception:
                            return ""
                    return f"{x[i]:g}" if i < len(x) else ""

                out.append(dict(groupe=titre, nom=nom, unite=unite, dec=dec,
                                couleur=coul, mini=float(v[imin]), mini_x=etiq(imin),
                                maxi=float(v[imax]), maxi_x=etiq(imax),
                                moy=float(v[ok].mean()),
                                total=float(v[ok].sum()) if cumul else float("nan"),
                                n=int(ok.sum())))
        for _ax, (x, y, z, (nx, ny, nz, unite, dec), titre) in self._hover2d.items():
            zz = np.asarray(z, dtype=float)
            ok = np.isfinite(zz)
            if not ok.any():
                continue
            iy, ix = np.unravel_index(np.nanargmax(np.where(ok, zz, -np.inf)), zz.shape)
            jy, jx = np.unravel_index(np.nanargmin(np.where(ok, zz, np.inf)), zz.shape)
            pos = lambda a, b: f"{x[b]:g}° / {y[a]:g}°"
            out.append(dict(groupe=titre, nom=nz, unite=unite, dec=dec, couleur=None,
                            mini=float(zz[jy, jx]), mini_x=pos(jy, jx),
                            maxi=float(zz[iy, ix]), maxi_x=pos(iy, ix),
                            moy=float(zz[ok].mean()), total=float("nan"),
                            n=int(ok.sum())))
        return out

    # ------------------------------------------------------------------
    # survol
    # ------------------------------------------------------------------
    @staticmethod
    def _nb(v, dec):
        v = float(v)
        if abs(v) < 0.5 * 10 ** (-dec):   # evite les "-0"
            v = 0.0
        return f"{v:,.{dec}f}".replace(",", " ")

    @staticmethod
    def _pastille(coul):
        if not coul:
            return "<td></td>"
        return (f"<td><span style='background:{coul}'>&nbsp;&nbsp;</span>"
                f"&nbsp;</td>")

    def _html_survol(self, ax, xdata, ydata):
        """HTML des valeurs de l'axe ax sous l'abscisse xdata (None si rien)."""
        carte = self._hover2d.get(ax)
        if carte is not None and xdata is not None and ydata is not None:
            x, y, z, (nx, ny, nz, unite, dec), titre = carte
            if len(x) == 0 or len(y) == 0:
                return None
            ia = int(np.argmin(np.abs(x - float(xdata))))
            it = int(np.argmin(np.abs(y - float(ydata))))
            v = z[it, ia]
            if not np.isfinite(v):
                return None
            return ("<div style='white-space:nowrap'><b>{t}</b>"
                    "<table cellspacing='0' cellpadding='1'>"
                    "<tr><td>{nx}&nbsp;&nbsp;</td><td align='right'><b>{vx:g}</b></td></tr>"
                    "<tr><td>{ny}&nbsp;&nbsp;</td><td align='right'><b>{vy:g}</b></td></tr>"
                    "<tr><td>{nz}&nbsp;&nbsp;</td><td align='right'><b>{vz}</b>"
                    "&nbsp;{u}</td></tr></table></div>").format(
                t=titre, nx=nx, ny=ny, nz=nz, vx=x[ia], vy=y[it],
                vz=self._nb(v, dec), u=unite)
        payload = self._hover.get(ax)
        if payload is None:
            return None
        x, series, xfmt, titre, _cumul, sur = payload
        pos = xdata if sur == "x" else ydata
        if pos is None or len(x) == 0:
            return None
        i = int(np.argmin(np.abs(x - float(pos))))
        entete = xfmt(i) if callable(xfmt) else f"{x[i]:g}"
        rangs = []
        for s in series:
            lab, vals, unite, dec = s[0], s[1], s[2], s[3]
            coul = s[4] if len(s) > 4 else None
            if i >= len(vals):
                continue
            v = np.asarray(vals, dtype=float).ravel()[i]
            txt = "-" if not np.isfinite(v) else self._nb(v, dec)
            rangs.append(f"<tr>{self._pastille(coul)}<td>{lab}&nbsp;&nbsp;</td>"
                         f"<td align='right'><b>{txt}</b>&nbsp;{unite}</td></tr>")
        if not rangs:
            return None
        pied = ""
        if self._plein_ecran and self._plot_fn is not None:
            pied = ("<div style='color:#94a3b8'><i>clic : plein ecran</i></div>")
        return (f"<div style='white-space:nowrap'>"
                f"<b>{(titre + ' &mdash; ') if titre else ''}{entete}</b>"
                f"<table cellspacing='0' cellpadding='1'>{''.join(rangs)}</table>"
                f"{pied}</div>")

    def _afficher(self, ev, html):
        r = self.devicePixelRatioF() or 1.0
        pos = QPoint(int(ev.x / r) + 14, int(self.height() - ev.y / r) + 14)
        QToolTip.showText(self.mapToGlobal(pos), html, self)

    def _on_move(self, ev):
        ax = ev.inaxes
        html = self._html_survol(ax, ev.xdata, ev.ydata) if ax is not None else None
        if html is None:
            QToolTip.hideText()
            self._cacher_reticule()
            if self._hover_listener:
                self._hover_listener("")
            return
        self._afficher(ev, html)
        self._reticule(ax, float(ev.xdata), float(ev.ydata))
        if self._hover_listener:
            self._hover_listener(html)

    def _on_leave(self, _ev):
        QToolTip.hideText()
        self._cacher_reticule()
        if self._hover_listener:
            self._hover_listener("")

    # ------------------------------------------------------------------
    # reticule (blit, pour rester fluide)
    # ------------------------------------------------------------------
    def _on_draw(self, _ev):
        self._bg = None

    def _lignes(self, ax):
        art = self._curseurs.get(ax)
        if art is None:
            xl, yl = ax.get_xlim(), ax.get_ylim()
            v = ax.axvline(xl[0], color="#475569", lw=.8, ls="--", alpha=.85,
                           animated=True, zorder=50)
            h = ax.axhline(yl[0], color="#475569", lw=.8, ls="--", alpha=.55,
                           animated=True, zorder=50)
            ax.set_xlim(xl); ax.set_ylim(yl)
            art = (v, h)
            self._curseurs[ax] = art
        return art

    def _reticule(self, ax, x, y):
        try:
            if self._bg is None:
                self._bg = self.copy_from_bbox(self.fig.bbox)
            self.restore_region(self._bg)
            v, h = self._lignes(ax)
            v.set_xdata([x, x]); h.set_ydata([y, y])
            ax.draw_artist(v); ax.draw_artist(h)
            self.blit(self.fig.bbox)
        except Exception:
            pass

    def _cacher_reticule(self):
        if self._bg is None:
            return
        try:
            self.restore_region(self._bg)
            self.blit(self.fig.bbox)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # clic
    # ------------------------------------------------------------------
    def _on_click(self, ev):
        if ev.button != 1:            # seul le clic gauche ouvre le plein ecran
            return
        # ne pas interferer avec les outils zoom / deplacement de la barre
        tb = getattr(self, "toolbar", None)
        if tb is not None and str(getattr(tb, "mode", "")):
            return
        if ev.dblclick:
            self._timer_clic.stop()
            if self._click_handler is not None and ev.inaxes is not None \
                    and ev.xdata is not None:
                self._click_handler(float(ev.xdata))
            return
        if self._plot_fn is None or not self._plein_ecran:
            return
        if self._click_handler is None:
            self.ouvrir_plein_ecran()
        else:
            # laisse une chance au double-clic (analyse detaillee)
            self._timer_clic.start(260)

    def ouvrir_plein_ecran(self):
        if self._plot_fn is None:
            return
        QToolTip.hideText()
        info = re.sub(r"(<br>)?\s*<i>[^<]*plein ecran[^<]*</i>", "",
                      self.toolTip() or "", flags=re.I)
        dlg = GraphDialog(self.window(), self._plot_fn,
                          titre=self._titre or "Graphique", info=info)
        dlg.exec()


class GraphDialog(QDialog):
    """Graphique en plein ecran : legende, survol, valeurs et statistiques."""

    COLS = ["Courbe", "Minimum", "Moyenne", "Maximum", "Total"]

    def __init__(self, parent, plot_fn, titre="Graphique", info=""):
        super().__init__(parent)
        self.setWindowTitle(titre)
        self.setSizeGripEnabled(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        entete = QLabel(f"<b style='font-size:13px'>{titre}</b>")
        entete.setWordWrap(True)
        lay.addWidget(entete)

        split = QSplitter(Qt.Orientation.Horizontal)

        gauche = QWidget(); gl = QVBoxLayout(gauche)
        gl.setContentsMargins(0, 0, 0, 0)
        self.cv = MplCanvas(14, 8, plein_ecran=False)
        gl.addWidget(NavigationToolbar2QT(self.cv, self))
        gl.addWidget(self.cv, 1)
        split.addWidget(gauche)

        droite = QWidget(); dl = QVBoxLayout(droite)
        dl.setContentsMargins(0, 0, 0, 0)

        gv = QGroupBox("Valeurs sous le curseur")
        gvl = QVBoxLayout(gv)
        self.lbl_curseur = QLabel("Survolez le graphique pour lire toutes "
                                  "les valeurs du point le plus proche.")
        self.lbl_curseur.setWordWrap(True)
        self.lbl_curseur.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_curseur.setAlignment(Qt.AlignmentFlag.AlignTop |
                                      Qt.AlignmentFlag.AlignLeft)
        self.lbl_curseur.setMinimumHeight(150)
        gvl.addWidget(self.lbl_curseur)
        dl.addWidget(gv)

        gs = QGroupBox("Toutes les courbes")
        gsl = QVBoxLayout(gs)
        self.tbl = QTableWidget(0, len(self.COLS))
        self.tbl.setHorizontalHeaderLabels(self.COLS)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setHorizontalScrollMode(
            QTableWidget.ScrollMode.ScrollPerPixel)
        self.tbl.setMinimumWidth(360)
        gsl.addWidget(self.tbl)
        dl.addWidget(gs, 1)

        if info:
            gi = QGroupBox("A propos de ce graphique")
            gil = QVBoxLayout(gi)
            lab = QLabel(info); lab.setWordWrap(True)
            lab.setTextFormat(Qt.TextFormat.RichText)
            gil.addWidget(lab)
            dl.addWidget(gi)

        split.addWidget(droite)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1080, 460])
        droite.setMinimumWidth(380)
        lay.addWidget(split, 1)

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.reject)
        lay.addWidget(btn)

        plot_fn(self.cv)
        # au-dela de quatre sous-graphes une legende de figure est posee : seule
        # la mise en page contrainte lui reserve de la place. En deca, le
        # tight_layout du canevas suffit et resiste mieux aux etiquettes longues.
        if len(self.cv.fig.axes) > 4:
            try:
                self.cv.fig.set_layout_engine("constrained")
            except Exception:
                pass
        self._agrandir_polices()
        self.cv.completer_legendes()
        self.cv.set_hover_listener(self._maj_curseur)
        self.cv.draw()
        self._remplir_stats(self.cv.statistiques())

        self.resize(1500, 900)
        self.setWindowState(Qt.WindowState.WindowMaximized)

    def _agrandir_polices(self):
        """Le plein ecran dispose de place : on remonte les tailles de texte."""
        fig = self.cv.fig
        n = max(len(fig.axes), 1)
        t = 12 if n <= 4 else 10
        for ax in fig.axes:
            if ax.get_title():
                ax.title.set_fontsize(t)
            ax.xaxis.label.set_fontsize(t - 2)
            ax.yaxis.label.set_fontsize(t - 2)
            ax.tick_params(labelsize=t - 3)
            etiq = [lab.get_text() for lab in ax.get_xticklabels()]
            if len(etiq) > 8 and any(e and not e.replace(".", "").replace("-", "")
                                     .isdigit() for e in etiq):
                ax.tick_params(axis="x", rotation=45)
        sup = getattr(fig, "_suptitle", None)
        if sup is not None and len(sup.get_text()) < 60:
            sup.set_fontsize(12)

    def _maj_curseur(self, html):
        if not html:
            self.lbl_curseur.setText(
                "<span style='color:#94a3b8'>Survolez le graphique pour lire "
                "toutes les valeurs du point le plus proche.</span>")
        else:
            self.lbl_curseur.setText(html)

    def _remplir_stats(self, stats):
        somme_ok = ("kWh", "EUR", "kWh/an", "kWh/mois", "kWh/jour", "kWh/m2")
        self.tbl.setRowCount(len(stats))
        # un meme libelle present dans plusieurs sous-graphes est prefixe par
        # le nom du sous-graphe (les 12 mois de la journee type, par exemple)
        groupes = {}
        for st in stats:
            groupes.setdefault(st["nom"], set()).add(st["groupe"])
        for r, s in enumerate(stats):
            dec = s["dec"]
            f = lambda v: "-" if not np.isfinite(v) else MplCanvas._nb(v, dec)
            nom = s["nom"]
            if s["groupe"] and len(groupes.get(nom, ())) > 1:
                nom = f"{s['groupe']} - {nom}"
            titre = f"{nom} ({s['unite']})" if s["unite"] else nom
            it0 = QTableWidgetItem(titre)
            if s["couleur"]:
                it0.setForeground(QColor(s["couleur"]))
                ft = it0.font(); ft.setBold(True); it0.setFont(ft)
            self.tbl.setItem(r, 0, it0)
            it0.setToolTip(f"{s['groupe']}<br>{s['n']} points"
                           if s["groupe"] else f"{s['n']} points")
            cells = [f"{f(s['mini'])}" + (f"  ({s['mini_x']})" if s["mini_x"] else ""),
                     f(s["moy"]),
                     f"{f(s['maxi'])}" + (f"  ({s['maxi_x']})" if s["maxi_x"] else ""),
                     f(s["total"]) if s["unite"] in somme_ok else "-"]
            for c, txt in enumerate(cells, start=1):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight |
                                    Qt.AlignmentFlag.AlignVCenter)
                self.tbl.setItem(r, c, it)


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


def table(headers, tips=None, rows=0, stretch=True):
    t = QTableWidget(rows, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    hh = t.horizontalHeader()
    if stretch:
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    else:
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    for i, tip in enumerate(tips or []):
        it = t.horizontalHeaderItem(i)
        if it is not None and tip:
            it.setToolTip(tip)
    t.setAlternatingRowColors(True)
    return t


def court(txt, n=30):
    """Etiquette raccourcie pour un axe : le nom complet reste dans l'infobulle."""
    txt = str(txt)
    return txt if len(txt) <= n else txt[:n - 3] + "..."


def item(text, editable=False, align_right=False, bold=False, tip=None,
         couleur=None):
    it = QTableWidgetItem(str(text))
    if tip:
        it.setToolTip(tip)
    if couleur:
        it.setForeground(QColor(couleur))
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
        titre = QLabel("<b>Postes de consommation</b>")
        titre.setToolTip(
            "<b>Decrivez ce que la maison consomme, poste par poste.</b><br>"
            "Chaque poste est converti en une courbe horaire sur toute la serie "
            "meteo : c'est la superposition de ces courbes et de la production "
            "solaire qui donne le taux d'autonomie.<br>"
            "Selectionnez un poste pour en editer les parametres a droite.")
        ll.addWidget(titre)
        self.list = QListWidget()
        self.list.setToolTip(
            "Liste des postes. Les postes desactives apparaissent en gris avec "
            "la mention [off] et ne sont pas comptes.")
        self.list.currentRowChanged.connect(self.select)
        ll.addWidget(self.list)

        row = QHBoxLayout()
        self.combo_kind = QComboBox()
        self.combo_kind.setToolTip(
            "<b>Type du poste a creer.</b><br>"
            "Le type determine le modele de calcul et donc les parametres "
            "demandes. Survolez chaque entree de la liste pour le detail.")
        for k, v in C.LOAD_KINDS.items():
            self.combo_kind.addItem(v["label"], k)
            self.combo_kind.setItemData(
                self.combo_kind.count() - 1,
                f"<b>{v['label']}</b><br>{v['help']}",
                Qt.ItemDataRole.ToolTipRole)
        row.addWidget(self.combo_kind)
        b_add = QPushButton("Ajouter")
        b_add.setToolTip("Cree un poste du type choisi a gauche, avec des "
                         "valeurs par defaut a ajuster ensuite.")
        b_add.clicked.connect(self.add)
        b_del = QPushButton("Supprimer")
        b_del.setToolTip("Supprime definitivement le poste selectionne. Pour "
                         "le neutraliser sans le perdre, decochez plutot "
                         "\"Poste actif\".")
        b_del.clicked.connect(self.remove)
        row.addWidget(b_add); row.addWidget(b_del)
        ll.addLayout(row)
        self.lbl_total = QLabel("")
        self.lbl_total.setWordWrap(True)
        self.lbl_total.setToolTip(
            "Consommation annuelle simulee de chaque poste actif, apres "
            "application des COP, des profils horaires et de la meteo reelle. "
            "Mise a jour a chaque simulation (F5).")
        ll.addWidget(self.lbl_total)
        split.addWidget(left)

        right = QWidget(); self.rl = QVBoxLayout(right)
        head = QHBoxLayout()
        self.chk_actif = QCheckBox("Poste actif")
        self.chk_actif.setToolTip(
            "Decochez pour retirer ce poste du calcul sans perdre sa "
            "configuration. Pratique pour chiffrer un usage futur "
            "(vehicule electrique, jacuzzi) separement.")
        self.chk_actif.stateChanged.connect(self.commit)
        self.edit_nom = QLineEdit()
        self.edit_nom.setToolTip(
            "Nom libre du poste, utilise dans les tableaux de resultats.")
        self.edit_nom.editingFinished.connect(self.commit)
        lab_nom = QLabel("Nom :")
        lab_nom.setToolTip("Nom libre du poste, utilise dans les tableaux "
                           "de resultats.")
        head.addWidget(lab_nom); head.addWidget(self.edit_nom, 1)
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
        self._orient_libres = {}     # index de champ -> autorise a bouger
        self._orient_res = None
        self._leviers = None         # dernier classement des actions
        self._attrib = None          # import reseau impute a chaque poste

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self._build_site()
        self._build_champs()
        self.tab_postes = PostesTab(self)
        self.tabs.addTab(self.tab_postes, "3. Consommation")
        self._build_systeme()
        self._build_couts()
        self._build_resultats()
        self._build_optim()
        self._build_orientations()
        self._build_leviers()
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
        for txt, slot, sc, tip in [
                ("Nouveau", self.new_config, None,
                 "Repart de la configuration par defaut. Les modifications "
                 "non enregistrees seront perdues."),
                ("Ouvrir...", self.open_config, QKeySequence.StandardKey.Open,
                 "Charge une configuration enregistree au format JSON."),
                ("Enregistrer...", self.save_config, QKeySequence.StandardKey.Save,
                 "Enregistre toute la configuration (site, champs, postes, "
                 "systeme, couts) dans un fichier JSON reutilisable."),
                (None, None, None, None),
                ("SIMULER  (F5)", self.run_sim, "F5",
                 "Relance le calcul complet sur toute la serie meteo. "
                 "A faire apres chaque modification."),
                (None, None, None, None),
                ("Exporter CSV...", self.export_csv, None,
                 "Exporte le bilan mensuel, les indicateurs et le detail "
                 "journalier dans un fichier CSV lisible par un tableur.")]:
            if txt is None:
                tb.addSeparator(); continue
            a = QAction(txt, self)
            a.triggered.connect(slot)
            if sc:
                a.setShortcut(sc)
            if tip:
                a.setToolTip(tip)
                a.setStatusTip(tip)
            tb.addAction(a)

    # ---------------- onglet 1 : site ----------------
    def _build_site(self):
        w = QWidget(); lay = QHBoxLayout(w)
        left = QWidget(); ll = QVBoxLayout(left)
        self.form_site = SchemaForm(
            [("__grp", "Implantation", None, None, None, None, "")] + C.SITE_SCHEMA,
            on_change=self._site_changed)
        ll.addWidget(self.form_site)

        self.lbl_annees = QLabel("")
        self.lbl_annees.setWordWrap(True)
        self.lbl_annees.setStyleSheet(
            "background:#f1f5f9;padding:6px;border-radius:4px;")
        ll.addWidget(self.lbl_annees)

        self.form_module = SchemaForm(
            [("__grp", "Modules et pertes", None, None, None, None, "")] + C.MODULE_SCHEMA,
            on_change=self.mark_dirty)
        ll.addWidget(self.form_module)
        row = QHBoxLayout()
        b1 = QPushButton("Charger la meteo en cache")
        b1.setToolTip("Relit la serie meteo deja telechargee pour ce site, sans "
                      "acces internet. C'est instantane.")
        b1.clicked.connect(lambda: self.load_meteo())
        b2 = QPushButton("Telecharger depuis PVGIS")
        b2.setToolTip(
            "Recupere sur les serveurs de la Commission europeenne les series "
            "horaires reelles du site, pour la periode et la base choisies "
            "ci-dessus.<br>Une seule fois par site : les composantes sont "
            "stockees a l'horizontale, donc toutes les inclinaisons sont "
            "ensuite calculables hors ligne.")
        b2.clicked.connect(self.download_meteo)
        row.addWidget(b1); row.addWidget(b2)
        ll.addLayout(row)
        ll.addStretch(1)
        lay.addWidget(left, 0)

        right = QWidget(); rl = QVBoxLayout(right)
        self.txt_meteo = QTextEdit(); self.txt_meteo.setReadOnly(True)
        self.txt_meteo.setMaximumHeight(190)
        self.txt_meteo.setToolTip(
            "Resume de la serie meteo actuellement chargee : c'est elle qui "
            "sera rejouee heure par heure par la simulation.")
        rl.addWidget(self.txt_meteo)
        self.cv_meteo = MplCanvas(7, 4)
        self.cv_meteo.setToolTip(
            "Survolez un mois pour lire le rayonnement et la temperature." + HINT)
        self.cv_meteo.set_plot(self.draw_meteo,
                               "Rayonnement et temperature mensuels")
        rl.addWidget(self.cv_meteo, 1)
        lay.addWidget(right, 1)
        self.tabs.addTab(w, "1. Site et meteo")

    def _site_changed(self, *_):
        self.update_aide_annees()
        self.mark_dirty()

    def update_aide_annees(self):
        """Explique, sous le formulaire, ce que couvre la base choisie."""
        s = self.form_site.get()
        db = s.get("base_donnees", "PVGIS-SARAH3")
        info = C.PVGIS_DATABASES.get(db, {})
        a0, a1 = C.couverture_base(db)
        y0, y1 = int(s.get("annee_debut", a0)), int(s.get("annee_fin", a1))
        detail = info.get("detail", "")
        resume = info.get("resume", "")

        if y1 < y0:
            msg = (f"<span style='color:{ROUGE}'><b>La derniere annee ({y1}) est "
                   f"anterieure a la premiere ({y0}).</b></span>")
        elif y0 < a0 or y1 > a1:
            manquantes = [y for y in range(y0, y1 + 1) if y < a0 or y > a1]
            msg = (f"<span style='color:{ROUGE}'><b>{db} ne couvre pas "
                   f"{', '.join(str(y) for y in manquantes)}.</b></span><br>"
                   f"Cette base va de <b>{a0} a {a1}</b>. PVGIS publie ses series "
                   f"avec un a deux ans de retard : les mesures satellite doivent "
                   f"etre controlees et recalibrees avant diffusion, une annee "
                   f"plus recente n'existe donc pas encore. Le telechargement "
                   f"sera refuse.")
        else:
            msg = (f"<span style='color:{VERT}'><b>Periode valide : {y0} a {y1}, "
                   f"soit {y1 - y0 + 1} annees reelles rejouees.</b></span><br>"
                   f"{db} couvre {a0} a {a1}.")
        self.lbl_annees.setText(
            f"<b>Meteo : {resume}.</b><br>{msg}")
        self.lbl_annees.setToolTip(
            f"<b>{db}</b><br>{detail}<br><br>"
            f"La simulation ne fabrique pas d'annee moyenne : elle rejoue chaque "
            f"heure de chaque annee de la periode, puis moyenne les resultats. "
            f"Une periode de 3 a 6 ans melange hivers doux et hivers froids, "
            f"ce qui est exactement ce qu'il faut pour dimensionner.")

    # ---------------- onglet 2 : champs PV ----------------
    def _build_champs(self):
        w = QWidget(); lay = QVBoxLayout(w)
        intro = QLabel(
            "<b>Groupes de panneaux.</b> Un groupe rassemble les panneaux qui "
            "partagent la meme inclinaison, la meme orientation et le meme "
            "ombrage. Azimut 180 = plein sud. Les inclinaisons sont transposees "
            "localement : aucun retelechargement meteo n'est necessaire pour en "
            "essayer une autre.<br>"
            "<b>Grappe (string)</b> = panneaux cables en serie : les "
            "<b>tensions s'additionnent</b>. Les grappes sont ensuite mises en "
            "parallele : les <b>courants s'additionnent</b>. Les colonnes "
            "grisees sont calculees. "
            "<i>Survolez n'importe quel en-tete de colonne pour l'explication "
            "detaillee.</i>")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.tbl_champs = table([c[1] for c in CH_COLS],
                                tips=[c[2] for c in CH_COLS], stretch=False)
        self.tbl_champs.itemChanged.connect(self._champs_changed)
        self.tbl_champs.setToolTip(
            "Double-cliquez une cellule blanche pour la modifier. "
            "Les colonnes grisees sont calculees automatiquement.")
        lay.addWidget(self.tbl_champs, 0)

        row = QHBoxLayout()
        b1 = QPushButton("Ajouter un groupe")
        b1.setToolTip("Cree un groupe de panneaux supplementaire, par exemple "
                      "une seconde orientation ou un second pan de toiture.")
        b1.clicked.connect(self.add_champ)
        b2 = QPushButton("Supprimer le groupe")
        b2.setToolTip("Supprime definitivement le groupe selectionne. Pour le "
                      "neutraliser sans le perdre, decochez plutot la case Actif.")
        b2.clicked.connect(self.del_champ)
        b3 = QPushButton("Separer les grappes")
        b3.setToolTip(
            "<b>Eclate le groupe selectionne en un groupe par grappe.</b><br>"
            "Tant que plusieurs grappes sont reunies dans un meme groupe, "
            "elles partagent forcement la meme inclinaison et le meme azimut. "
            "Une fois separees, l'onglet 8 peut donner a chacune sa propre "
            "orientation.<br>"
            "<i>Reversible : reglez le nombre de panneaux d'un groupe et "
            "supprimez les autres pour les regrouper a nouveau.</i>")
        b3.clicked.connect(self.eclater_champ)
        self.lbl_champs = QLabel("")
        row.addWidget(b1); row.addWidget(b2); row.addWidget(b3)
        row.addStretch(1); row.addWidget(self.lbl_champs)
        lay.addLayout(row)

        self.lbl_cablage = QLabel("")
        self.lbl_cablage.setWordWrap(True)
        self.lbl_cablage.setStyleSheet(
            "background:#f1f5f9;padding:6px;border-radius:4px;")
        lay.addWidget(self.lbl_cablage)

        self.cv_champs = MplCanvas(9, 3.4)
        self.cv_champs.setToolTip(
            "<b>Production mensuelle de chaque groupe de panneaux.</b><br>"
            "Survolez un mois pour lire les valeurs exactes.<br>"
            "C'est ici que se voit l'interet d'une forte inclinaison : elle "
            "aplatit la courbe et remonte decembre, le mois qui dimensionne "
            "une installation autonome." + HINT)
        self.cv_champs.set_plot(self.draw_champs,
                                "Production mensuelle par groupe de panneaux")
        lay.addWidget(self.cv_champs, 1)
        self.tabs.addTab(w, "2. Champs PV")

    def _champs_changed(self, it):
        if getattr(self, "_loading_champs", False):
            return
        r, c = it.row(), it.column()
        if r >= len(self.cfg["champs"]) or c >= len(CH_COLS):
            return
        ch = self.cfg["champs"][r]
        key = CH_COLS[c][0]
        if key is None:
            return
        try:
            if key == "actif":
                ch["actif"] = it.checkState() == Qt.CheckState.Checked
            elif key in CH_TEXTE:
                ch[key] = it.text()
            else:
                v = float(it.text().replace(",", ".").replace(" ", ""))
                ch[key] = max(int(v), 0) if key in CH_INT else v
        except ValueError:
            pass
        if key == "n_serie" and int(ch.get("n_serie", 0) or 0) < 1:
            ch["n_serie"] = 1
        self.refresh_champs()
        self.mark_dirty()

    def refresh_champs(self):
        self._loading_champs = True
        t = self.tbl_champs
        t.setRowCount(len(self.cfg["champs"]))
        sysc = self.cfg["systeme"]
        vmax = float(sysc.get("vdc_max_v", 800.0))
        imax = float(sysc.get("i_max_string_a", 26.0))
        gris = QColor("#f8fafc")

        for r, ch in enumerate(self.cfg["champs"]):
            d = S.string_diag(self.cfg, ch)

            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if ch.get("actif", True)
                              else Qt.CheckState.Unchecked)
            chk.setToolTip(CH_COLS[0][2])
            t.setItem(r, 0, chk)

            for key in ("nom", "n_panneaux", "wc_panneau", "inclinaison", "azimut",
                        "surface_m2_panneau", "ombrage_pct", "voc_v", "isc_a",
                        "n_serie"):
                v = ch.get(key, C.CHAMP_DEFAUT.get(key, 0))
                t.setItem(r, CH_IDX[key],
                          item(v, editable=True, align_right=(key != "nom"),
                               tip=CH_COLS[CH_IDX[key]][2]))

            def calc(col, texte, tip, couleur=None, bold=False):
                it = item(texte, align_right=True, bold=bold, tip=tip,
                          couleur=couleur)
                it.setBackground(gris)
                t.setItem(r, col, it)

            c0 = CH_IDX["n_serie"] + 1
            incomplete = d["grappe_incomplete"]
            calc(c0, f"{d['n_grappes']:.2f}".rstrip("0").rstrip("."),
                 (f"{d['n_panneaux']} panneaux / {d['n_serie']} en serie"
                  + (" &mdash; <b>grappe incomplete</b> : le compte ne tombe pas juste"
                     if incomplete else "")),
                 couleur=ORANGE if incomplete else None)

            calc(c0 + 1, f"{d['voc_stc']:.0f}",
                 f"{d['n_serie']} x {float(ch.get('voc_v', 0)):.1f} V a 25 C")

            marge = 100 * (1 - d["voc_froid"] / vmax) if vmax else 0
            if d["voc_froid"] > vmax:
                col, etat = ROUGE, ("<b>DEPASSE la limite de l'onduleur "
                                    f"({vmax:.0f} V) : destruction du materiel.</b>")
            elif marge < 5:
                col, etat = ORANGE, f"Marge de seulement {marge:.1f} % sous {vmax:.0f} V."
            else:
                col, etat = VERT, f"Marge de {marge:.0f} % sous les {vmax:.0f} V admis."
            calc(c0 + 2, f"{d['voc_froid']:.0f}",
                 f"Tension a vide de la grappe par -10 C.<br>{etat}",
                 couleur=col, bold=True)

            i_col = ROUGE if d["isc_grappe"] > imax else None
            calc(c0 + 3, f"{d['isc_total']:.1f}",
                 (f"{d['n_grappes']:.2f} grappes x {float(ch.get('isc_a', 0)):.1f} A."
                  f"<br>Par grappe : {d['isc_grappe']:.1f} A pour une entree MPPT "
                  f"limitee a {imax:.0f} A."),
                 couleur=i_col)

            calc(c0 + 4, f"{d['kwc']:.2f}",
                 f"{d['n_panneaux']} x {float(ch.get('wc_panneau', 0)):.0f} Wc",
                 bold=True)

            diag = (self.res or {}).get("diag_champs", {}).get(ch["nom"], {})
            calc(c0 + 5,
                 f"{diag.get('productible_kwh_kwc', 0):,.0f}".replace(",", " ")
                 if diag else "-",
                 CH_COLS[c0 + 5][2])
            calc(c0 + 6,
                 f"{diag.get('production_kwh_an', 0):,.0f}".replace(",", " ")
                 if diag else "-",
                 CH_COLS[c0 + 6][2])

        self._loading_champs = False
        self._ajuster_hauteur(t)
        if getattr(self, "tbl_orient", None) is not None:
            self.refresh_orient()

        kwc = S.total_kwc(self.cfg)
        n = int(sysc["n_onduleurs"])
        lim = float(sysc["pv_max_kwc_par_onduleur"])
        col = ROUGE if kwc / max(n, 1) > lim else VERT
        self.lbl_champs.setText(
            f"<b>{S.total_panneaux(self.cfg)} panneaux &bull; {kwc:.2f} kWc &bull; "
            f"{S.surface_m2(self.cfg):.0f} m2</b> &nbsp; "
            f"<span style='color:{col}'>{kwc / max(n, 1):.1f} kWc/onduleur "
            f"(limite {lim:.1f})</span>")
        self.lbl_champs.setToolTip(
            "Totaux des groupes actifs.<br>La comparaison kWc par onduleur "
            "reprend la limite constructeur saisie dans l'onglet 4.")
        self.show_cablage()
        self.draw_champs()

    @staticmethod
    def _cv(cv, defaut):
        """Canevas cible : celui passe par la fenetre plein ecran, sinon celui
        de l'onglet. Les slots Qt passent parfois un int, on l'ignore."""
        return cv if isinstance(cv, MplCanvas) else defaut

    def draw_champs(self, cv=None):
        """Production mensuelle de chaque groupe de panneaux."""
        c = self._cv(cv, self.cv_champs); c.clear()
        ax = c.fig.add_subplot(111)
        par_champ = (self.res or {}).get("par_champ") or {}
        if not par_champ:
            ax.text(.5, .5, "Lancez une simulation (F5) pour voir la production "
                            "mensuelle de chaque groupe.",
                    ha="center", va="center", fontsize=9, color="#94a3b8")
            ax.set_xticks([]); ax.set_yticks([])
            c.draw()
            return
        met, x = self.meteo, np.arange(12)
        ny = met["n_years"]
        series, bas = [], np.zeros(12)
        couleurs = ["#fbbf24", "#1f4e79", "#15803d", "#b91c1c", "#7c3aed", "#0891b2"]
        for k, (nom, p) in enumerate(par_champ.items()):
            mens = np.array([p[met["month"] == mo + 1].sum() / ny for mo in range(12)])
            ax.bar(x, mens, .62, bottom=bas, label=nom,
                   color=couleurs[k % len(couleurs)])
            bas = bas + mens
            series.append((nom, mens, "kWh", 0, couleurs[k % len(couleurs)]))
        series.append(("<b>Total</b>", bas, "kWh", 0, "#334155"))
        besoin = (self.res or {}).get("mensuel", {}).get("besoin")
        if besoin is not None:
            ax.plot(x, besoin, color="#0f172a", lw=1.8, marker="o", ms=3,
                    label="Besoin de la maison")
            series.append(("Besoin de la maison", besoin, "kWh", 0, "#0f172a"))
        ax.set_xticks(x); ax.set_xticklabels(MOIS, fontsize=8)
        ax.set_ylabel("kWh/mois")
        ax.set_title("Production mensuelle par groupe, face au besoin", fontsize=9)
        ax.legend(fontsize=7, frameon=False, ncol=2)
        c.hover(ax, x, series, xfmt=lambda i: MOIS[i], titre="Production mensuelle")
        c.draw()

    @staticmethod
    def _ajuster_hauteur(t, mini=90, maxi=330):
        """Ajuste la hauteur d'un tableau a son contenu, pour laisser la place
        au graphique en dessous."""
        h = t.horizontalHeader().height() + 2 * t.frameWidth() + 4
        for r in range(t.rowCount()):
            h += t.rowHeight(r)
        h += t.horizontalScrollBar().sizeHint().height()
        t.setMaximumHeight(max(mini, min(h, maxi)))

    def show_cablage(self):
        """Synthese DC : grappes, tensions extremes, courants, entrees MPPT."""
        sysc = self.cfg["systeme"]
        vmax = float(sysc.get("vdc_max_v", 800.0))
        vmin = float(sysc.get("vmppt_min_v", 160.0))
        imax = float(sysc.get("i_max_string_a", 26.0))
        n_mppt = int(sysc.get("n_mppt_par_onduleur", 2)) * int(sysc["n_onduleurs"])
        actifs = [c for c in self.cfg["champs"] if c.get("actif", True)]
        if not actifs:
            self.lbl_cablage.setText("Aucun groupe actif.")
            return
        diags = [S.string_diag(self.cfg, c) for c in actifs]
        n_g = sum(d["n_grappes"] for d in diags)
        v_pire = max(d["voc_froid"] for d in diags)
        i_pire = max(d["isc_grappe"] for d in diags)
        beta = float(self.cfg["module"].get("beta_voc_pct_k", -0.27))

        cv = ROUGE if v_pire > vmax else ORANGE if v_pire > .95 * vmax else VERT
        ci = ROUGE if i_pire > imax else VERT
        cg = ORANGE if n_g > n_mppt * 2 else VERT

        alertes = S.check_cablage(self.cfg)
        coul = {"erreur": ROUGE, "attention": ORANGE, "info": BLEU}
        txt = "".join(f"<br><span style='color:{coul[t]}'>&bull; {m}</span>"
                      for t, m in alertes)
        if not txt:
            txt = (f"<br><span style='color:{VERT}'>&bull; Cablage coherent avec "
                   f"les limites declarees dans l'onglet 4.</span>")
        self.lbl_cablage.setText(
            f"<b>Synthese du cablage continu</b> &nbsp;&bull;&nbsp; "
            f"<b>{n_g:.0f} grappes</b> pour "
            f"<span style='color:{cg}'>{n_mppt} entrees MPPT</span> "
            f"&nbsp;&bull;&nbsp; tension a vide la plus haute par -10 C : "
            f"<span style='color:{cv}'><b>{v_pire:.0f} V</b> / {vmax:.0f} V admis</span> "
            f"&nbsp;&bull;&nbsp; courant le plus fort par grappe : "
            f"<span style='color:{ci}'><b>{i_pire:.1f} A</b> / {imax:.0f} A admis</span>"
            f"{txt}")
        self.lbl_cablage.setToolTip(
            f"<b>Comment ces chiffres sont obtenus</b><br>"
            f"Tension a vide a froid = Voc du panneau x nombre en serie x "
            f"(1 + {beta:.2f} %/C x (-10 C - 25 C)), soit environ "
            f"{abs(beta) * 35:.1f} % de plus qu'a 25 C.<br>"
            f"Le MPPT demarre a {vmin:.0f} V : une grappe trop courte ne "
            f"produit rien le matin ni par temps couvert.<br>"
            f"Le courant par grappe est celui d'un seul panneau (Isc) : la mise "
            f"en serie n'augmente pas le courant.")

    def add_champ(self):
        ch = copy.deepcopy(C.CHAMP_DEFAUT)
        ch["nom"] = f"Champ {len(self.cfg['champs']) + 1}"
        self.cfg["champs"].append(ch)
        self.refresh_champs(); self.mark_dirty()

    def del_champ(self):
        r = self.tbl_champs.currentRow()
        if 0 <= r < len(self.cfg["champs"]):
            del self.cfg["champs"][r]
            self.refresh_champs(); self.mark_dirty()

    def eclater_champ(self):
        r = self.tbl_champs.currentRow()
        if not (0 <= r < len(self.cfg["champs"])):
            QMessageBox.information(self, "Separer les grappes",
                                    "Selectionnez d'abord une ligne du tableau.")
            return
        ch = self.cfg["champs"][r]
        morceaux = C.eclater_grappes(ch)
        if len(morceaux) < 2:
            QMessageBox.information(
                self, "Separer les grappes",
                f"\"{ch['nom']}\" ne contient qu'une seule grappe "
                f"({ch['n_panneaux']} panneaux en serie) : il n'y a rien a "
                f"separer.\n\nPour en faire plusieurs grappes, reduisez "
                f"d'abord le nombre de panneaux en serie dans la colonne "
                f"\"Pann./grappe\".")
            return
        rep = QMessageBox.question(
            self, "Separer les grappes",
            f"\"{ch['nom']}\" contient {len(morceaux)} grappes.\n\n"
            f"Le groupe sera remplace par {len(morceaux)} groupes de "
            f"{morceaux[0]['n_panneaux']} panneaux, chacun libre de recevoir "
            f"sa propre inclinaison et son propre azimut dans l'onglet 8.\n\n"
            f"Continuer ?")
        if rep != QMessageBox.StandardButton.Yes:
            return
        self.cfg["champs"][r:r + 1] = morceaux
        self.refresh_champs()
        self.mark_dirty()
        self.statusBar().showMessage(
            f"{len(morceaux)} groupes crees : chacun peut maintenant recevoir "
            f"son orientation propre (onglet 8).", 8000)

    # ---------------- onglet 4 : systeme ----------------
    def _build_systeme(self):
        w = QWidget(); lay = QHBoxLayout(w)
        sc = QScrollArea(); sc.setWidgetResizable(True)
        self.form_sys = SchemaForm(C.SYSTEM_SCHEMA, on_change=self.mark_dirty)
        sc.setWidget(self.form_sys)
        lay.addWidget(sc, 0)
        right = QWidget(); rl = QVBoxLayout(right)
        self.txt_sys = QTextEdit(); self.txt_sys.setReadOnly(True)
        self.txt_sys.setToolTip(
            "Synthese calculee du systeme apres simulation : capacite "
            "reellement utile, energie transitant par la batterie, nombre de "
            "cycles et duree de vie estimee.")
        rl.addWidget(self.txt_sys, 0)
        self.cv_soc = MplCanvas(7, 4)
        self.cv_soc.setToolTip(
            "<b>Etat de charge de la batterie sur toute la serie meteo.</b><br>"
            "Survolez la courbe pour lire la date et le niveau exact.<br>"
            "Les creux qui touchent le trait rouge sont les moments ou la "
            "batterie a ete videe et ou le reseau a pris le relais : ce sont "
            "eux qui determinent la capacite necessaire." + HINT)
        self.cv_soc.set_plot(self.draw_soc,
                             "Etat de charge de la batterie sur toute la serie")
        rl.addWidget(self.cv_soc, 1)
        lay.addWidget(right, 1)
        self.tabs.addTab(w, "4. Onduleurs et batterie")

    # ---------------- onglet 5 : couts ----------------
    # ---------------- onglet 5 : couts ----------------
    #   Deux perimetres, et c'est toute la raison d'etre des categories :
    #     * l'INSTALLATION SOLAIRE (panneaux, onduleurs, batterie, cablage,
    #       protections, pose, demarches, outillage) : le seul chiffre qui
    #       serve a arbitrer un dimensionnement ;
    #     * le PROJET COMPLET, qui ajoute le chauffe-eau, l'insert et
    #       l'isolation : utile pour le budget, jamais pour l'arbitrage.
    BOM_COLS = ["Poste", "Categorie", "Quantite auto", "Qte (si fixe)", "Unite",
                "Prix unitaire (EUR)", "Quantite retenue", "Montant (EUR)"]

    def _build_couts(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            "<b>Nomenclature.</b> La colonne <i>Categorie</i> range chaque ligne "
            "dans le <b>perimetre solaire</b> (panneaux, onduleurs, batterie, "
            "cablage, pose, demarches) ou <b>hors perimetre</b> (chauffe-eau, "
            "insert, isolation). Seul le sous-total solaire alimente les EUR/Wc, "
            "le cout du kWh et les couts affiches dans les onglets Optimisation, "
            "Orientations et Leviers.<br>"
            "La colonne <i>Quantite auto</i> relie la ligne a la configuration : "
            "nombre de panneaux, d'onduleurs, de cellules, de grappes ou capacite "
            "batterie se mettent a jour tout seuls."))
        self.tbl_bom = table(
            self.BOM_COLS,
            tips=[
                "<b>Libelle libre de la ligne de devis.</b><br>"
                "Le libelle sert aussi a proposer une categorie automatiquement "
                "quand vous creez une ligne.",
                C.aide_categories(),
                "<b>Relie la quantite a la configuration.</b><br>"
                "Choisissez par exemple \"Nombre total de panneaux\" et la ligne "
                "suivra automatiquement le tableau des champs PV.<br>"
                "\"Quantite saisie manuellement\" fige la valeur de la colonne "
                "suivante.",
                "<b>Quantite fixe, utilisee uniquement si la colonne precedente "
                "est sur \"saisie manuellement\".</b><br>"
                "Laissee a 0, elle vaut 1.",
                "<b>Unite affichee, purement indicative</b> (u, lot, kWc, m, m2...).",
                "<b>Prix unitaire hors pose, en euros.</b><br>"
                "TTC si vous raisonnez TTC : soyez simplement coherent sur "
                "toutes les lignes.",
                "<b>Quantite reellement retenue apres application de la regle "
                "automatique.</b> Colonne calculee.",
                "<b>Quantite retenue x prix unitaire.</b> Colonne calculee."])
        self.tbl_bom.itemChanged.connect(self._bom_changed)
        lay.addWidget(self.tbl_bom, 3)

        row = QHBoxLayout()
        b1 = QPushButton("Ajouter une ligne")
        b1.setToolTip("Ajoute une ligne vide a la nomenclature. Sa categorie est "
                      "proposee d'apres le libelle que vous saisissez.")
        b1.clicked.connect(self.add_bom)
        b2 = QPushButton("Supprimer la ligne")
        b2.setToolTip("Supprime la ligne selectionnee du devis.")
        b2.clicked.connect(self.del_bom)
        b3 = QPushButton("Completer la nomenclature")
        b3.setToolTip(
            "<b>Ajoute les postes d'une auto-installation qui manquent au devis.</b><br>"
            "Cable solaire au metre, connecteurs MC4, coffret DC, fusibles de "
            "grappe, parafoudres, differentiel, mise a la terre, routeur de "
            "surplus, puis l'outillage a acheter une fois : sertisseuse, pince "
            "MC4, pince ampermetrique continue, cle dynamometrique, EPI, "
            "chargeur d'equilibrage.<br>"
            "Les lignes deja presentes ne sont pas dupliquees. Les prix proposes "
            "sont des ordres de grandeur d'achat direct, a remplacer par vos devis.")
        b3.clicked.connect(self.completer_bom)
        self.chk_grouper = QCheckBox("Grouper par categorie")
        self.chk_grouper.setChecked(True)
        self.chk_grouper.setToolTip(
            "Affiche les lignes rangees par categorie, perimetre solaire "
            "d'abord. Decochez pour retrouver l'ordre de saisie.")
        self.chk_grouper.stateChanged.connect(
            lambda *_: QTimer.singleShot(0, self.refresh_bom))
        row.addWidget(b1); row.addWidget(b2); row.addWidget(b3)
        row.addWidget(self.chk_grouper); row.addStretch(1)
        lay.addLayout(row)

        self.tbl_recap = table(
            ["Poste de depense", "Montant (EUR)", "Part du perimetre",
             "Ratio utile"],
            tips=["<b>Sous-total par categorie</b>, puis les deux perimetres.",
                  "<b>Somme des lignes de cette categorie.</b>",
                  "<b>Part de cette categorie dans le perimetre solaire.</b><br>"
                  "Repere d'une installation avec stockage : environ un tiers "
                  "de modules et structure, un tiers de batterie, le reste en "
                  "conversion, cablage et pose.",
                  "<b>Ratio parlant pour comparer a un devis ou a une autre "
                  "installation.</b>"],
            stretch=True)
        self.tbl_recap.setMaximumHeight(330)
        lay.addWidget(self.tbl_recap, 2)

        self.lbl_bom = QLabel("")
        self.lbl_bom.setWordWrap(True)
        lay.addWidget(self.lbl_bom)

        self.form_eco = SchemaForm(
            [("__grp", "Hypotheses economiques", None, None, None, None, "")] + C.ECO_SCHEMA,
            on_change=self.mark_dirty)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(self.form_eco)
        sc.setMaximumHeight(230)
        lay.addWidget(sc)
        self.txt_eco = QTextEdit(); self.txt_eco.setReadOnly(True)
        self.txt_eco.setMinimumHeight(230)
        lay.addWidget(self.txt_eco, 2)
        self.tabs.addTab(w, "5. Couts")

    def _bom_index(self, row):
        """Index dans cfg['bom'] de la ligne affichee a cette ligne du tableau."""
        ordre = getattr(self, "_bom_ordre", None)
        if ordre is None or not (0 <= row < len(ordre)):
            return None
        return ordre[row]

    def _bom_changed(self, it):
        if getattr(self, "_loading_bom", False):
            return
        i = self._bom_index(it.row())
        if i is None:
            return
        l = self.cfg["bom"][i]
        c = it.column()
        try:
            if c == 0:
                ancien = l.get("poste", "")
                l["poste"] = it.text()
                # une ligne encore sur la categorie par defaut suit le libelle
                if l.get("categorie") in (None, "divers") or \
                        l.get("categorie") == C.deviner_categorie(ancien, l.get("auto", "fixe")):
                    l["categorie"] = C.deviner_categorie(it.text(), l.get("auto", "fixe"))
            elif c == 3:
                l["qte"] = float(it.text().replace(",", ".").replace(" ", ""))
            elif c == 4:
                l["unite"] = it.text()
            elif c == 5:
                l["pu"] = float(it.text().replace(",", ".").replace(" ", ""))
        except ValueError:
            pass
        QTimer.singleShot(0, self._rafraichir_couts)

    def refresh_bom(self):
        self._loading_bom = True
        lignes, recap = S.compute_bom(self.cfg)

        grouper = bool(getattr(self, "chk_grouper", None) is not None
                       and self.chk_grouper.isChecked())
        ordre = list(range(len(lignes)))
        if grouper:
            rang = {c: i for i, c in enumerate(C.ORDRE_CATEGORIES)}
            ordre.sort(key=lambda i: (rang.get(lignes[i]["categorie"], 99), i))
        self._bom_ordre = ordre

        t = self.tbl_bom
        t.setRowCount(len(ordre))
        cat_precedente = None
        for r, i in enumerate(ordre):
            l = lignes[i]
            cat = l["categorie"]
            entete = grouper and cat != cat_precedente
            cat_precedente = cat
            teinte = QColor("#f1f5f9") if l["solaire"] else QColor("#fdf4e7")
            t.setItem(r, 0, item(l["poste"], editable=True,
                                 tip=C.BOM_CATEGORIES[cat]["aide"]))
            cb = QComboBox()
            for k in C.ORDRE_CATEGORIES:
                cb.addItem(C.BOM_CATEGORIES[k]["label"], k)
            cb.setCurrentIndex(C.ORDRE_CATEGORIES.index(cat))
            cb.setToolTip(C.aide_categories())
            cb.currentIndexChanged.connect(
                lambda _i, row=r: self._set_categorie(row, _i))
            t.setCellWidget(r, 1, cb)
            cb2 = QComboBox()
            for k, lab in C.AUTO_QTY.items():
                cb2.addItem(lab, k)
            # une regle de quantite disparue d'une version a l'autre ne doit
            # pas empecher l'onglet de s'afficher : compute_bom l'a deja
            # ramenee sur "fixe", on se contente de suivre.
            cles = list(C.AUTO_QTY)
            auto = l.get("auto", "fixe")
            cb2.setCurrentIndex(cles.index(auto) if auto in cles else 0)
            cb2.currentIndexChanged.connect(
                lambda _i, row=r: self._set_auto(row, _i))
            t.setCellWidget(r, 2, cb2)
            t.setItem(r, 3, item(l.get("qte", 0), editable=True, align_right=True))
            t.setItem(r, 4, item(l.get("unite", ""), editable=True))
            t.setItem(r, 5, item(f"{l.get('pu', 0):.2f}", editable=True, align_right=True))
            t.setItem(r, 6, item(f"{l['qte_calc']:,.1f}".replace(",", " "), align_right=True))
            t.setItem(r, 7, item(f"{l['montant']:,.0f}".replace(",", " "),
                                 align_right=True, bold=True))
            for c in range(len(self.BOM_COLS)):
                cell = t.item(r, c)
                if cell is not None:
                    cell.setBackground(teinte)
                    if entete and c == 0:
                        f = cell.font(); f.setBold(True); cell.setFont(f)
        self._loading_bom = False
        self._remplir_recap(recap)

    def _remplir_recap(self, recap):
        """Sous-totaux par categorie, puis les deux perimetres."""
        kwc = S.total_kwc(self.cfg)
        wc = max(kwc * 1000.0, 1e-9)
        batt = max(float(self.cfg["systeme"]["batt_kwh_nominal"]), 1e-9)
        n_pan = max(S.total_panneaux(self.cfg), 1)
        n_ond = max(int(self.cfg["systeme"]["n_onduleurs"]), 1)
        sol = max(recap["solaire"], 1e-9)
        f = lambda v, d=0: f"{v:,.{d}f}".replace(",", " ")

        ratios = {
            "pv": lambda m: f"{m / n_pan:,.0f} EUR/panneau".replace(",", " "),
            "conversion": lambda m: f"{m / n_ond:,.0f} EUR/onduleur".replace(",", " "),
            "stockage": lambda m: f"{m / batt:,.0f} EUR/kWh de batterie".replace(",", " "),
            "electrique": lambda m: f"{1000 * m / wc:.2f} EUR/kWc",
            "genie_civil": lambda m: f"{1000 * m / wc:.2f} EUR/kWc",
        }

        t = self.tbl_recap
        lignes = []
        for k in C.ORDRE_CATEGORIES:
            m = recap["par_categorie"].get(k, 0.0)
            if m == 0:
                continue
            d = C.BOM_CATEGORIES[k]
            part = f"{100 * m / sol:.0f} %" if d["solaire"] else "-"
            ratio = ratios.get(k, lambda _m: "")(m)
            lignes.append((("  " + d["label"]), m, part, ratio, d["solaire"], False))
        lignes.append(("PERIMETRE SOLAIRE", recap["solaire"], "100 %",
                       f"{recap['solaire'] / wc:.2f} EUR/Wc installe",
                       True, True))
        lignes.append(("Hors perimetre solaire", recap["hors_solaire"], "-",
                       "chauffage, ECS, isolation", False, True))
        lignes.append(("PROJET COMPLET", recap["total"], "-",
                       f"{recap['total'] / wc:.2f} EUR/Wc, tout compris",
                       False, True))

        t.setRowCount(len(lignes))
        for r, (nom, m, part, ratio, solaire, gras) in enumerate(lignes):
            coul = VERT if (solaire and gras) else (BLEU if solaire else ORANGE)
            t.setItem(r, 0, item(nom, bold=gras, couleur=coul if gras else None))
            t.setItem(r, 1, item(f(m), align_right=True, bold=gras,
                                 couleur=coul if gras else None))
            t.setItem(r, 2, item(part, align_right=True))
            t.setItem(r, 3, item(ratio, couleur="#64748b"))

        # Repere de marche : 1,80 a 2,50 EUR/Wc pose par un installateur pour
        # une installation avec stockage (source : devis courants 2025). Le
        # rapport entre les deux chiffre la valeur du travail fourni.
        pro_bas, pro_haut = 1.80, 2.50
        eur_wc = recap["solaire"] / wc
        econ_bas = max(pro_bas * wc - recap["solaire"], 0.0)
        econ_haut = max(pro_haut * wc - recap["solaire"], 0.0)
        self.lbl_bom.setText(
            f"<b>Installation solaire : {f(recap['solaire'])} EUR</b> "
            f"&nbsp;({eur_wc:.2f} EUR/Wc pour {kwc:.1f} kWc, "
            f"{recap['par_categorie'].get('stockage', 0) / batt:.0f} EUR/kWh de batterie)"
            f"<br>Projet complet : {f(recap['total'])} EUR, dont "
            f"{f(recap['hors_solaire'])} EUR hors perimetre solaire."
            f"<br><span style='color:#15803d'>Pose par un installateur, ces "
            f"{kwc:.1f} kWc avec stockage se chiffreraient {f(pro_bas * wc)} a "
            f"{f(pro_haut * wc)} EUR ({pro_bas:.2f} a {pro_haut:.2f} EUR/Wc). "
            f"L'auto-installation economise de l'ordre de {f(econ_bas)} a "
            f"{f(econ_haut)} EUR.</span>")

    def _rafraichir_couts(self):
        """Redessine le devis et propage aux ecrans qui affichent un cout.

        Differe d'un tour de boucle d'evenements : ces deux fonctions sont
        appelees depuis le signal d'une liste deroulante que refresh_bom()
        va detruire en reconstruisant la ligne. Detruire un widget pendant
        l'emission de son propre signal fait tomber Qt.
        """
        self.refresh_bom()
        self.mark_dirty()
        if self.res:
            self.show_eco()
            self.show_results_kpi()

    def _set_categorie(self, row, idx):
        if getattr(self, "_loading_bom", False):
            return
        i = self._bom_index(row)
        if i is None:
            return
        self.cfg["bom"][i]["categorie"] = C.ORDRE_CATEGORIES[idx]
        QTimer.singleShot(0, self._rafraichir_couts)

    def _set_auto(self, row, idx):
        if getattr(self, "_loading_bom", False):
            return
        i = self._bom_index(row)
        if i is None:
            return
        self.cfg["bom"][i]["auto"] = list(C.AUTO_QTY)[idx]
        QTimer.singleShot(0, self._rafraichir_couts)

    def add_bom(self):
        self.cfg["bom"].append({"poste": "Nouveau poste", "categorie": "divers",
                                "auto": "fixe", "qte": 1, "pu": 0.0, "unite": "u"})
        self.refresh_bom(); self.mark_dirty()

    def completer_bom(self):
        """Ajoute les postes d'auto-installation absents du devis."""
        presents = {str(l.get("poste", "")).strip().lower() for l in self.cfg["bom"]}
        ajoutes = [copy.deepcopy(l) for l in C.BOM_COMPLEMENT
                   if l["poste"].strip().lower() not in presents]
        if not ajoutes:
            QMessageBox.information(
                self, "Nomenclature",
                "Tous les postes de la nomenclature type sont deja presents.")
            return
        self.cfg["bom"].extend(ajoutes)
        self.refresh_bom(); self.mark_dirty()
        QMessageBox.information(
            self, "Nomenclature",
            f"{len(ajoutes)} poste(s) ajoute(s) au devis.\n\n"
            "Les prix proposes sont des ordres de grandeur d'achat direct 2025 : "
            "remplacez-les par vos propres devis avant de conclure quoi que ce "
            "soit sur le temps de retour.")

    def del_bom(self):
        r = self.tbl_bom.currentRow()
        i = self._bom_index(r)
        if i is not None:
            del self.cfg["bom"][i]
            self.refresh_bom(); self.mark_dirty()

    # ---------------- onglet 6 : resultats ----------------
    def _build_resultats(self):
        w = QWidget(); lay = QVBoxLayout(w)
        self.lbl_kpi = QLabel("Appuyez sur F5 pour lancer la simulation.")
        self.lbl_kpi.setWordWrap(True)
        self.lbl_kpi.setToolTip(
            "<b>Les six chiffres qui resument l'installation.</b><br><br>"
            "<b>Autonomie annuelle</b> : part du besoin couverte sans le "
            "reseau, soit 1 - import / besoin.<br>"
            "<b>kWh soutires/an</b> : ce que vous achetez encore au reseau, "
            "la base de votre facture.<br>"
            "<b>kWh consommes/an</b> : besoin total, veille des onduleurs "
            "comprise.<br>"
            "<b>kWh produits/an</b> : production des panneaux, avant ecretage.<br>"
            "<b>Installation solaire</b> : sous-total des categories 1 a 8 de "
            "la nomenclature (panneaux, structure, onduleurs, batterie, "
            "cablage, pose, demarches, outillage). Le chauffe-eau, l'insert et "
            "l'isolation en sont exclus : ils apparaissent dans la ligne "
            "\"projet complet\" juste en dessous.<br>"
            "<b>Retour de l'installation solaire</b> : nombre d'annees pour "
            "rembourser ce seul investissement par l'energie que les panneaux "
            "et la batterie evitent d'acheter, a maison inchangee. C'est ce "
            "chiffre qu'il faut regarder pour decider d'un panneau ou d'un "
            "pack de batterie de plus.<br>"
            "<i>Le retour du projet complet, lui, se compare a la facture "
            "declaree avant travaux : il melange l'effet du solaire et celui "
            "du chauffage et de l'isolation.</i>")
        self.lbl_kpi.setStyleSheet(
            f"background:{BLEU};color:white;padding:9px;border-radius:4px;")
        lay.addWidget(self.lbl_kpi)
        self.lbl_alertes = QLabel(""); self.lbl_alertes.setWordWrap(True)
        self.lbl_alertes.setToolTip(
            "<b>Controles de coherence automatiques.</b><br>"
            "<span style='color:#b91c1c'>ERREUR</span> : la configuration "
            "n'est pas realisable telle quelle, ou detruirait du materiel.<br>"
            "<span style='color:#d97706'>ATTENTION</span> : realisable mais "
            "sous-optimal ou sans marge.<br>"
            "<span style='color:#1f4e79'>INFO</span> : simple remarque de "
            "dimensionnement.")
        lay.addWidget(self.lbl_alertes)

        sub = QTabWidget()
        # mensuel
        w1 = QWidget(); l1 = QVBoxLayout(w1)
        self.tbl_mois = table(
            ["Mois", "Conso usages (kWh)", "Veille (kWh)", "Besoin total (kWh)",
             "Production (kWh)", "Autoconso (kWh)", "Import reseau (kWh)",
             "Ecrete/injecte (kWh)", "Autonomie (%)", "Conso/jour (kWh)",
             "Prod/jour (kWh)", "Budget conso/jour (kWh)"],
            tips=[
                "Mois de l'annee. La derniere ligne totalise l'annee.",
                "<b>Energie appelee par vos usages sur le mois</b> (chauffage, "
                "eau chaude, electromenager...), hors consommation propre des "
                "onduleurs. Moyenne sur toutes les annees meteo.",
                "<b>Consommation a vide des onduleurs sur le mois.</b><br>"
                "Ils absorbent quelques dizaines de watts en permanence, "
                "24 h/24, simplement pour rester allumes.",
                "<b>Besoin total = usages + veille.</b><br>"
                "C'est ce que l'installation doit couvrir.",
                "<b>Energie produite par les panneaux sur le mois, cote continu.</b>",
                "<b>Part de la production reellement consommee</b>, directement "
                "ou via la batterie. C'est l'energie qui vous fait economiser.",
                "<b>Energie achetee au reseau sur le mois.</b><br>"
                "C'est ce poste, multiplie par le prix du kWh, qui constitue "
                "votre facture.<br>"
                "<i>Si la recharge de la batterie en heures creuses est "
                "activee, cette colonne inclut l'energie achetee pour remplir "
                "la batterie. L'autonomie, elle, n'en tient pas compte deux "
                "fois : un kWh achete la nuit reste un kWh du reseau, meme "
                "restitue le lendemain matin.</i>",
                "<b>Production perdue ou revendue.</b><br>"
                "Ecretee quand la batterie est pleine et la maison servie "
                "(injection nulle), injectee si la revente est activee.",
                "<b>Part du besoin couverte sans acheter d'electricite.</b><br>"
                "= 1 - (reseau qui alimente directement la maison + part "
                "d'origine reseau de ce que la batterie restitue) / besoin.<br>"
                "Vert au-dela de 90 %, orange de 70 a 90 %, rouge en dessous.",
                "<b>Consommation moyenne d'une journee de ce mois.</b>",
                "<b>Production moyenne d'une journee de ce mois.</b>",
                "<b>Consommation journaliere maximale compatible avec votre "
                "objectif d'autonomie, a installation constante.</b><br>"
                "Rempli par le bouton \"Budget de consommation\" de l'onglet 7. "
                "Repond a la question : combien puis-je me permettre de "
                "consommer en janvier ?"])
        l1.addWidget(self.tbl_mois)
        self.cv_mois = MplCanvas(9, 3.6)
        self.cv_mois.setToolTip(
            "Survolez un mois pour lire toutes ses valeurs : production, "
            "autoconsommation, soutirage, autonomie." + HINT)
        self.cv_mois.set_plot(self.draw_mois, "Bilan mensuel et autonomie")
        l1.addWidget(self.cv_mois, 1)
        sub.addTab(w1, "Bilan mensuel")
        # journalier
        w2 = QWidget(); l2 = QVBoxLayout(w2)
        row = QHBoxLayout()
        row.addWidget(QLabel("Mois :"))
        self.cb_mois = QComboBox(); self.cb_mois.addItems(MOIS)
        self.cb_mois.setToolTip("Mois a detailler jour par jour.")
        self.cb_mois.currentIndexChanged.connect(self.draw_jour)
        row.addWidget(self.cb_mois)
        row.addWidget(QLabel("Annee :"))
        self.cb_annee = QComboBox()
        self.cb_annee.setToolTip(
            "<b>Annee reelle a afficher.</b><br>"
            "La liste reprend les annees de la serie meteo telechargee. "
            "Comparez un hiver doux et un hiver froid : c'est le pire cas qui "
            "dimensionne l'installation.")
        self.cb_annee.currentIndexChanged.connect(self.draw_jour)
        row.addWidget(self.cb_annee); row.addStretch(1)
        l2.addLayout(row)
        self.tbl_jour = table(
            ["Date", "Production (kWh)", "Consommation (kWh)", "Besoin (kWh)",
             "Import (kWh)", "Autonomie (%)", "Charge mini batterie (kWh)"],
            tips=[
                "Jour calendaire de la serie meteo rejouee.",
                "Energie produite par les panneaux ce jour-la.",
                "Energie appelee par vos usages, hors veille des onduleurs.",
                "Besoin total de la journee, veille des onduleurs comprise.",
                "Energie achetee au reseau ce jour-la.",
                "Part du besoin de la journee couverte sans le reseau.",
                "<b>Niveau le plus bas atteint par la batterie dans la "
                "journee, en kWh.</b><br>"
                "S'il touche le plancher, la batterie a ete videe : c'est la "
                "que le reseau prend le relais. Un plancher atteint souvent en "
                "hiver signale une batterie sous-dimensionnee."])
        l2.addWidget(self.tbl_jour, 1)
        self.cv_jour = MplCanvas(9, 3.2)
        self.cv_jour.setToolTip(
            "Survolez un jour pour lire production, besoin, soutirage, "
            "autonomie et niveau de batterie." + HINT)
        self.cv_jour.set_plot(self.draw_jour, "Detail journalier")
        l2.addWidget(self.cv_jour, 1)
        sub.addTab(w2, "Detail journalier")
        # profil horaire
        w3 = QWidget(); l3 = QVBoxLayout(w3)
        self.cv_profil = MplCanvas(9, 5)
        self.cv_profil.setToolTip(
            "Journee moyenne de chaque mois, en kW. Survolez une heure pour "
            "lire les puissances exactes. L'ecart entre le jaune (production) "
            "et le bleu (besoin) montre a quelles heures il faut deplacer les "
            "usages." + HINT)
        self.cv_profil.set_plot(self.draw_profil, "Journee moyenne de chaque mois")
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
        self.cb_sweep.setToolTip(
            "<b>Parametre a faire varier.</b><br>"
            "Une simulation complete est relancee pour chacune des valeurs "
            "saisies a droite, tout le reste de la configuration etant fige. "
            "C'est la facon la plus sure de trouver un optimum.")
        self.cb_sweep.addItem("Inclinaison de tous les champs (deg)", "inclinaison")
        self.cb_sweep.addItem("Inclinaison du 1er champ (deg)", "inclinaison_champ1")
        self.cb_sweep.addItem("Puissance PV totale (kWc)", "kwc")
        self.cb_sweep.addItem("Capacite batterie (kWh)", "batterie")
        self.cb_sweep.addItem("Nombre d'onduleurs", "onduleurs")
        row.addWidget(self.cb_sweep)
        row.addWidget(QLabel("Valeurs :"))
        self.ed_sweep = QLineEdit("20, 30, 40, 50, 60, 70, 80")
        self.ed_sweep.setPlaceholderText("Ex. 0,15,30,60,75,90 ou 10:100@5")
        self.ed_sweep.setToolTip(
            "<b>Valeurs a essayer, separees par des virgules.</b><br>"
            "Accepte aussi un intervalle du type <b>10:100@5</b> pour "
            "dire de 10 a 100 par pas de 5.<br>"
            "Dans l'unite du parametre choisi a gauche : des degres pour une "
            "inclinaison, des kWc pour une puissance, des kWh pour une "
            "batterie, un nombre entier pour les onduleurs.<br>"
            "Exemples : 20, 30, 40, 50, 60, 70, 80 ; 10:100@5")
        row.addWidget(self.ed_sweep, 1)
        b = QPushButton("Lancer le balayage")
        b.setToolTip("Relance une simulation complete pour chaque valeur de la "
                     "liste. Comptez quelques secondes par valeur.")
        b.clicked.connect(self.run_sweep)
        row.addWidget(b)
        bb = QPushButton("Budget de consommation")
        bb.setToolTip(
            "<b>Question inverse : a installation constante, combien puis-je "
            "consommer par jour ?</b><br>"
            "Cherche, mois par mois, la consommation journaliere maximale qui "
            "respecte encore l'objectif d'autonomie. Le resultat remplit la "
            "derniere colonne du bilan mensuel de l'onglet 6.")
        bb.clicked.connect(self.run_budget)
        row.addWidget(bb)        # le bouton n'etait jamais ajoute a la barre

        lab_s = QLabel("Tranche a rembourser en moins de :")
        self.sp_seuil_tranche = QDoubleSpinBox()
        self.sp_seuil_tranche.setRange(1, 40)
        self.sp_seuil_tranche.setValue(10)
        self.sp_seuil_tranche.setDecimals(0)
        self.sp_seuil_tranche.setSuffix(" ans")
        aide_seuil = (
            "<b>Au-dela de combien d'annees refusez-vous une tranche "
            "supplementaire ?</b><br>"
            "Sert de critere d'arret dans la vue <i>Amortissement par "
            "tranche</i> et dans la recherche d'optimum : la derniere tranche "
            "dont le temps de retour propre reste sous ce seuil est celle ou "
            "s'arreter.<br>"
            "8 a 12 ans est un choix courant pour du materiel dont la duree de "
            "vie utile est de 15 a 25 ans.")
        lab_s.setToolTip(aide_seuil)
        self.sp_seuil_tranche.setToolTip(aide_seuil)
        self.sp_seuil_tranche.valueChanged.connect(self._seuil_change)
        row.addWidget(lab_s)
        row.addWidget(self.sp_seuil_tranche)
        row.addStretch(1)
        lay.addLayout(row)
        self.tbl_sweep = table(
            ["Valeur testee", "Autonomie (%)", "Production (kWh/an)",
             "Import reseau (kWh/an)", "Ecrete (kWh/an)",
             "Cout installation solaire (EUR)", "EUR/Wc",
             "Retour cumule (ans)",
             "Tranche", "Cout de la tranche (EUR)", "Gain de la tranche (EUR/an)",
             "RETOUR DE LA TRANCHE (ans)", "ROI marginal"],
            tips=[
                "Valeur donnee au parametre balaye pour cette simulation. "
                "La meilleure ligne est en gras.",
                "Part du besoin annuel couverte sans le reseau.",
                "Production annuelle moyenne des panneaux.",
                "Energie achetee au reseau sur l'annee.",
                "Production perdue faute de place dans la batterie et "
                "d'usage immediat.",
                "<b>Sous-total du PERIMETRE SOLAIRE uniquement</b> : panneaux, "
                "structure, onduleurs, batterie, cablage, protections, pose, "
                "demarches, outillage.<br>"
                "Le chauffe-eau, l'insert et l'isolation sont exclus : ils ne "
                "bougent pas d'une valeur testee a l'autre et n'ont rien a "
                "faire dans l'arbitrage.",
                "Cout du perimetre solaire ramene au watt-crete installe. "
                "Repere : 1,80 a 2,50 EUR/Wc pose par un installateur, "
                "stockage compris.",
                "<b>Temps de retour de l'installation ENTIERE</b> a ce "
                "niveau de dimensionnement : investissement solaire total "
                "divise par l'economie annuelle totale, inflation comprise.<br>"
                "<i>Attention : ce chiffre est une moyenne. Une premiere "
                "batterie amortie en 3 ans suivie d'une seconde qui ne "
                "s'amortit jamais donnent environ 6 ans, ce qui masque "
                "exactement la decision a prendre. Regardez plutot les quatre "
                "colonnes suivantes.</i>",
                "<b>Ce que cette ligne ajoute par rapport a la precedente</b>, "
                "dans l'unite du parametre balaye : +16 kWh de batterie, "
                "+5 kWc de panneaux...",
                "<b>Surcout de materiel de cette seule tranche</b>, perimetre "
                "solaire : ce que vous sortez de votre poche pour passer de la "
                "ligne precedente a celle-ci.",
                "<b>Euros economises en plus chaque annee</b> grace a cette "
                "seule tranche. Il s'effondre a mesure que l'installation "
                "grossit : les premiers kWh de batterie servent tous les "
                "soirs, les derniers ne servent que quelques jours d'hiver.",
                "<b>Annees pour que CETTE TRANCHE se rembourse toute seule</b>, "
                "independamment de tout ce qui a ete installe avant, "
                "inflation de l'energie comprise.<br>"
                "C'est le chiffre a regarder pour un projet que l'on fait "
                "grossir par etapes : il repond a \"est-ce que la prochaine "
                "batterie vaut le coup ?\", ce que le retour cumule ne dit "
                "pas.<br>"
                "<b>jamais</b> : la tranche coute et ne rapporte rien. "
                "<b>&gt; horizon</b> : elle finirait par se rembourser, mais "
                "apres la duree d'analyse.",
                "Euros economises chaque annee par euro supplementaire investi "
                "sur cette tranche. C'est l'inverse du retour de la tranche : "
                "0,25 = remboursement en 4 ans."])
        lay.addWidget(self.tbl_sweep, 1)
        sub = QTabWidget()
        self.cv_sweep = MplCanvas(9, 4)
        self.cv_sweep.setToolTip(
            "Profil mois par mois de chaque option testee. Survolez un mois "
            "pour lire toutes les options a la fois.<br><i>Clic : plein ecran. "
            "Double-clic : analyse detaillee de la valeur la plus proche.</i>")
        self.cv_sweep.set_plot(self.draw_sweep, "Balayage : profils mensuels")
        sub.addTab(self.cv_sweep, "Profils mois par mois")

        self.cv_sweep_bar = MplCanvas(9, 4)
        self.cv_sweep_bar.setToolTip(
            "<b>Comparaison directe des options.</b><br>"
            "Cumuls sur l'annee entiere et moyennes sur les douze mois, plus "
            "le mois le plus defavorable : c'est lui qui dimensionne une "
            "installation autonome." + HINT)
        self.cv_sweep_bar.set_plot(self.draw_sweep_bar,
                                   "Balayage : comparaison des options")
        sub.addTab(self.cv_sweep_bar, "Comparaison des options")

        self.cv_sweep_tranche = MplCanvas(9, 4)
        self.cv_sweep_tranche.setToolTip(
            "<b>Combien de temps met la tranche suivante a se rembourser "
            "elle-meme ?</b><br>"
            "Le temps de retour habituel porte sur l'installation entiere : "
            "une premiere batterie amortie en 3 ans suivie d'une seconde qui "
            "ne s'amortira jamais donnent un retour global d'environ 6 ans, "
            "chiffre qui masque exactement la decision a prendre.<br>"
            "Ici chaque tranche est jugee seule, independamment des "
            "precedentes : c'est la vue a utiliser pour un projet que l'on "
            "fait grossir par etapes." + HINT)
        self.cv_sweep_tranche.set_plot(self.draw_sweep_tranche,
                                       "Balayage : amortissement par tranche")
        sub.addTab(self.cv_sweep_tranche, "Amortissement par tranche")
        sub.addTab(self._build_grille(), "Optimum PV x batterie")
        lay.addWidget(sub, 1)
        self.tabs.addTab(w, "7. Optimisation")

    # ---------------- onglet 9 : leviers ----------------
    CAT_COULEURS = {"Production": "#d97706", "Stockage": "#7c3aed",
                    "Consommation": "#0891b2", "Pilotage": "#15803d"}

    def _build_leviers(self):
        w = QWidget(); lay = QVBoxLayout(w)

        intro = QLabel(
            "<b>Par quoi commencer pour payer moins de reseau ?</b> "
            "La partie gauche montre quels appareils causent l'energie achetee ; "
            "la partie droite chiffre, action par action, ce que rapporterait "
            "chaque decision, en relancant une simulation complete a chaque fois.")
        intro.setWordWrap(True)
        intro.setStyleSheet("background:#f1f5f9;padding:6px;border-radius:4px;")
        lay.addWidget(intro)

        row = QHBoxLayout()
        lab1 = QLabel("Reduction testee par poste :")
        self.sp_lev_red = QSpinBox()
        self.sp_lev_red.setRange(1, 50); self.sp_lev_red.setValue(10)
        self.sp_lev_red.setSuffix(" %")
        aide_red = ("<b>De combien fait-on maigrir chaque poste pour mesurer son "
                    "levier ?</b><br>"
                    "10 % est un ordre de grandeur atteignable sans travaux "
                    "(appareil plus recent, usage plus court, consigne plus "
                    "basse). Le gain affiche est proportionnel : doubler la "
                    "reduction double a peu pres le gain.")
        lab1.setToolTip(aide_red); self.sp_lev_red.setToolTip(aide_red)
        row.addWidget(lab1); row.addWidget(self.sp_lev_red)

        lab2 = QLabel("Talon supprime :")
        self.sp_lev_talon = QDoubleSpinBox()
        self.sp_lev_talon.setRange(0, 1000); self.sp_lev_talon.setValue(50)
        self.sp_lev_talon.setDecimals(0); self.sp_lev_talon.setSuffix(" W")
        aide_talon = ("<b>Watts permanents que ferait gagner un remplacement "
                      "d'appareils de fond.</b><br>"
                      "Un vieux congelateur coffre tire 60 a 90 W en moyenne, un "
                      "modele recent 25 a 35 W ; un refrigerateur ancien 50 a "
                      "70 W contre 20 a 25 W aujourd'hui.<br>"
                      "<i>50 W en continu = 438 kWh/an.</i>")
        lab2.setToolTip(aide_talon); self.sp_lev_talon.setToolTip(aide_talon)
        row.addWidget(lab2); row.addWidget(self.sp_lev_talon)

        lab3 = QLabel("Cout de ce remplacement :")
        self.sp_lev_cout = QDoubleSpinBox()
        self.sp_lev_cout.setRange(0, 50000); self.sp_lev_cout.setValue(900)
        self.sp_lev_cout.setDecimals(0); self.sp_lev_cout.setSingleStep(50)
        self.sp_lev_cout.setSuffix(" EUR")
        aide_cout = ("<b>Prix suppose du remplacement des appareils de fond.</b><br>"
                     "Sert a calculer un temps de retour comparable a celui des "
                     "panneaux et de la batterie. Mettez 0 si le materiel est "
                     "deja amorti ou recupere.")
        lab3.setToolTip(aide_cout); self.sp_lev_cout.setToolTip(aide_cout)
        row.addWidget(lab3); row.addWidget(self.sp_lev_cout)

        b = QPushButton("Analyser les leviers")
        b.setToolTip(
            "Simule une a une chaque action possible sur toute la serie meteo "
            "et les classe par gain annuel. Comptez quelques secondes.")
        b.clicked.connect(self.run_leviers)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.lbl_leviers = QLabel("Lancez une simulation (F5) pour voir d'ou "
                                  "vient l'energie achetee au reseau.")
        self.lbl_leviers.setWordWrap(True)
        self.lbl_leviers.setStyleSheet(
            "background:#fff7ed;padding:7px;border-radius:4px;")
        lay.addWidget(self.lbl_leviers)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tbl_attrib = table(
            ["Poste", "Conso (kWh/an)", "Reseau (kWh/an)", "Cout (EUR/an)",
             "Part achetee", "De nuit"],
            tips=[
                "Poste de consommation, ou veille des onduleurs.",
                "Energie annuelle appelee par ce poste, meteo reelle rejouee.",
                "Part de l'import reseau imputee a ce poste : a chaque heure, "
                "l'energie achetee est repartie au prorata des consommations "
                "de cette heure-la.",
                "Ce que ce poste coute en achat de reseau, au prix du kWh "
                "saisi dans l'onglet 5.",
                "Fraction de ce poste qui vient du reseau plutot que du "
                "solaire ou de la batterie. Un chiffre eleve signale un usage "
                "mal place dans la journee.",
                "Fraction consommee alors que les panneaux ne produisent pas. "
                "C'est la consommation de fond : elle se paie au reseau ou "
                "vide la batterie."])
        split.addWidget(self.tbl_attrib)

        self.tbl_leviers = table(
            ["Action", "Type", "Reseau evite", "Gain (EUR/an)", "Cout (EUR)",
             "Perimetre", "Retour (ans)", "Gain / 1000 EUR", "Autonomie"],
            tips=[
                "Action simulee, une simulation complete par ligne.",
                "Production = plus de panneaux ou d'onduleurs. Stockage = "
                "batterie. Consommation = consommer moins. Pilotage = "
                "consommer au meme moment que le soleil, sans rien acheter.",
                "Energie qui ne serait plus achetee au reseau, par an.",
                "Baisse de la facture annuelle : reseau evite, revente et "
                "bois compris.",
                "Surcout d'investissement. Pour un panneau, un pack de "
                "batterie ou un onduleur, il est recalcule sur le PERIMETRE "
                "SOLAIRE de la nomenclature de l'onglet 5 : le chauffe-eau, "
                "l'insert et l'isolation n'y entrent pas.<br>"
                "Vide quand le cout depend d'une decision que le simulateur ne "
                "connait pas (appareil remplace, travaux).",
                "<b>Solaire</b> : le surcout tombe dans le perimetre de "
                "l'installation solaire, donc dans les EUR/Wc et le temps de "
                "retour de l'installation.<br>"
                "<b>Hors solaire</b> : un appareil, des travaux ou un simple "
                "reglage. Le gain est bien reel, mais il ne se compare pas a "
                "un panneau de plus.<br>"
                "<b>Gratuit</b> : rien a acheter, seulement a programmer.",
                "Annees pour rembourser l'action par le gain annuel.",
                "Gain annuel rapporte a 1 000 EUR investis : c'est le "
                "classement a regarder pour arbitrer entre panneaux, batterie "
                "et remplacement d'appareil.",
                "Taux d'autonomie annuel apres l'action."])
        split.addWidget(self.tbl_leviers)
        # colonnes ajustees a leur contenu : les en-tetes restent lisibles
        for t in (self.tbl_attrib, self.tbl_leviers):
            h = t.horizontalHeader()
            h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        split.setSizes([520, 700])
        split.setMaximumHeight(260)
        lay.addWidget(split)

        self.cv_leviers = MplCanvas(9, 4)
        self.cv_leviers.setToolTip(
            "<b>Consommation de chaque poste</b>, avec la part achetee au "
            "reseau en rouge.<br><b>Gain annuel de chaque action testee</b>, "
            "la plus payante en premier." + HINT)
        self.cv_leviers.set_plot(self.draw_leviers,
                                 "Leviers : origine de l'import et gain des actions")
        lay.addWidget(self.cv_leviers, 1)
        self.tabs.addTab(w, "9. Leviers")

    def run_leviers(self):
        if self.meteo is None:
            QMessageBox.information(self, "Meteo", "Chargez d'abord une serie meteo.")
            return
        self.pull_config()
        self._start(Worker(S.leviers, self.cfg, self.meteo,
                           reduction=self.sp_lev_red.value() / 100.0,
                           talon_w=self.sp_lev_talon.value(),
                           cout_remplacement=self.sp_lev_cout.value()),
                    self._leviers_prets, "Analyse des leviers")

    def _leviers_prets(self, out):
        self._leviers = out
        self._attrib = out["attribution"]
        self.show_leviers()
        self.tabs.setCurrentIndex(8)
        self.statusBar().showMessage(
            f"{len(out['actions'])} actions simulees et classees par gain annuel.",
            8000)

    def show_leviers(self):
        """Remplit les deux tableaux, le resume et le graphique de l'onglet 9."""
        f = lambda v, n=0: f"{v:,.{n}f}".replace(",", " ")
        at = getattr(self, "_attrib", None) or []
        lv = getattr(self, "_leviers", None)

        t = self.tbl_attrib; t.setRowCount(len(at))
        for r, l in enumerate(at):
            t.setItem(r, 0, item(l["nom"], bold=(r == 0)))
            t.setItem(r, 1, item(f(l["conso"]), align_right=True))
            t.setItem(r, 2, item(f(l["import"]), align_right=True, bold=(r == 0)))
            t.setItem(r, 3, item(f(l["cout_import"]), align_right=True))
            it = item(f"{100 * l['part_importee']:.0f} %", align_right=True)
            it.setForeground(QColor("#b91c1c" if l["part_importee"] > .25 else
                                    "#d97706" if l["part_importee"] > .12 else "#15803d"))
            t.setItem(r, 4, it)
            t.setItem(r, 5, item(f"{100 * l['part_nuit']:.0f} %", align_right=True))

        if lv:
            acts = lv["actions"]
            t = self.tbl_leviers; t.setRowCount(len(acts))
            for r, a in enumerate(acts):
                t.setItem(r, 0, item(a["nom"], tip=a["detail"], bold=(r == 0)))
                t.setItem(r, 1, item(a["categorie"],
                                     couleur=self.CAT_COULEURS.get(a["categorie"])))
                t.setItem(r, 2, item(f(a["import_evite"]), align_right=True))
                it = item(f(a["gain_an"]), align_right=True, bold=True)
                it.setForeground(QColor("#15803d" if a["gain_an"] > 0 else "#b91c1c"))
                t.setItem(r, 3, it)
                t.setItem(r, 4, item(f(a["cout"]) if a["cout"] > 0 else "a chiffrer",
                                     align_right=True))
                if a.get("cout_solaire", 0) > 0:
                    perim, coul = "solaire", BLEU
                elif a["cout"] > 0:
                    perim, coul = "hors solaire", ORANGE
                elif a["categorie"] == "Pilotage":
                    perim, coul = "gratuit", VERT
                else:
                    # sobriete : le gain est mesure, le cout depend de l'action
                    # retenue (appareil, travaux, simple habitude)
                    perim, coul = "a chiffrer", "#64748b"
                t.setItem(r, 5, item(perim, couleur=coul))
                t.setItem(r, 6, item(f"{a['retour']:.1f}" if a["retour"] else "-",
                                     align_right=True))
                t.setItem(r, 7, item(f"{a['gain_par_1000']:.0f}"
                                     if a["gain_par_1000"] else "-", align_right=True))
                t.setItem(r, 8, item(f"{100 * a['autonomie']:.1f} %", align_right=True))

        self.lbl_leviers.setText(self._resume_leviers(at, lv))
        self.draw_leviers()

    def _resume_leviers(self, at, lv):
        """Texte de priorites, en francais et en euros."""
        if not at:
            return ("Lancez une simulation (F5) pour voir d'ou vient l'energie "
                    "achetee au reseau.")
        f = lambda v, n=0: f"{v:,.{n}f}".replace(",", " ")
        total_imp = sum(l["import"] for l in at)
        total_cout = sum(l["cout_import"] for l in at)
        tete = at[:3]
        part = 100 * sum(l["import"] for l in tete) / max(total_imp, 1e-9)
        txt = [f"<b>Le reseau vous coute {f(total_cout)} EUR par an</b> "
               f"({f(total_imp)} kWh achetes). "
               f"{part:.0f} % de cet achat vient de trois postes : " +
               ", ".join(f"<b>{l['nom']}</b> ({f(l['cout_import'])} EUR/an, "
                         f"{100 * l['part_nuit']:.0f} % consomme de nuit)"
                         for l in tete) + "."]
        fond = [l for l in at if l["part_nuit"] > .45 and l["conso"] > 300]
        if fond:
            txt.append(
                "Consommation de fond a examiner en premier (plus de 45 % "
                "consomme quand les panneaux ne produisent pas) : " +
                ", ".join(f"<b>{l['nom']}</b>" for l in fond) +
                ". Un appareil de fond remplace par un modele sobre se paie "
                "toute l'annee, nuit comprise.")
        if not lv:
            txt.append("<i>Cliquez sur \"Analyser les leviers\" pour chiffrer "
                       "chaque action possible et les classer.</i>")
            return "<br>".join(txt)

        acts = lv["actions"]
        gagnants = [a for a in acts if a["gain_an"] > 0]
        gratuits = [a for a in gagnants if a["categorie"] == "Pilotage"]
        chiffres = [a for a in gagnants if a["gain_par_1000"]]
        if gratuits:
            g = sum(a["gain_an"] for a in gratuits)
            txt.append(
                f"<b>A cout nul</b> : decaler des usages vers les heures "
                f"ensoleillees rapporte jusqu'a {f(g)} EUR/an au total (" +
                ", ".join(f"{a['nom'].split(' : ')[0]} {f(a['gain_an'])} EUR"
                          for a in gratuits[:3]) + ").")
        if chiffres:
            meilleur = max(chiffres, key=lambda a: a["gain_par_1000"])
            txt.append(
                f"<b>Meilleur rendement d'un euro investi</b> : "
                f"{meilleur['nom']} &mdash; {f(meilleur['gain_par_1000'])} EUR "
                f"gagnes par an pour 1 000 EUR investis, soit un retour en "
                f"{meilleur['retour']:.1f} ans.")
        perdants = [a for a in acts if a["gain_an"] <= 0]
        if perdants:
            txt.append("Sans effet ou contre-productif ici : " +
                       ", ".join(a["nom"] for a in perdants[:3]) + ".")
        solaires = [a for a in chiffres if a.get("cout_solaire", 0) > 0]
        if solaires:
            m = max(solaires, key=lambda a: a["gain_par_1000"])
            txt.append(
                f"<b>Meilleur euro depense dans l'installation solaire "
                f"elle-meme</b> : {m['nom']} &mdash; {f(m['cout_solaire'])} EUR "
                f"de materiel pour {f(m['gain_an'])} EUR/an, retour en "
                f"{m['retour']:.1f} ans. Ce chiffre est comparable d'une option "
                f"a l'autre parce qu'il ne contient que du perimetre solaire.")
        txt.append("<i>Les lignes \"a chiffrer\" ne portent pas de cout : le "
                   "simulateur mesure le gain, a vous de le comparer au devis. "
                   "La colonne <b>Perimetre</b> dit si le surcout entre dans "
                   "l'installation solaire ou non.</i>")
        return "<br>".join(txt)

    def draw_leviers(self, cv=None):
        """Origine de l'import a gauche, gain de chaque action a droite."""
        c = self._cv(cv, self.cv_leviers); c.clear()
        at = getattr(self, "_attrib", None) or []
        lv = getattr(self, "_leviers", None)
        # cote a cote dans l'onglet (large et peu haut), l'un au-dessus de
        # l'autre en plein ecran : les noms de postes ont alors toute la place
        plein = c is not self.cv_leviers
        ax1 = c.fig.add_subplot(211 if plein else 121)
        ax2 = c.fig.add_subplot(212 if plein else 122)

        if at:
            ordre = at[::-1]                      # le plus gros en haut
            noms = [l["nom"] for l in ordre]
            y = np.arange(len(noms))
            auto = np.array([l["autoconso"] for l in ordre])
            imp = np.array([l["import"] for l in ordre])
            ax1.barh(y, auto, .62, color="#15803d", label="Couvert par le solaire")
            ax1.barh(y, imp, .62, left=auto, color="#b91c1c",
                     label="Achete au reseau")
            for k, l in enumerate(ordre):
                if l["cout_import"] >= 1:
                    ax1.text(auto[k] + imp[k], y[k], f"  {l['cout_import']:,.0f} EUR"
                             .replace(",", " "), va="center", fontsize=6.5,
                             color="#b91c1c")
            ax1.set_yticks(y)
            ax1.set_yticklabels([court(n, 26) for n in noms], fontsize=7)
            ax1.set_xlabel("kWh/an")
            ax1.set_title("Consommation de chaque poste et part achetee au reseau",
                          fontsize=9)
            ax1.grid(axis="x", alpha=.2, ls=":")
            ax1.legend(fontsize=7, frameon=False, loc="lower right")
            ax1.margins(x=.16)
            c.hover(ax1, y,
                    [("Consomme", auto + imp, "kWh/an", 0, "#0f172a"),
                     ("Couvert par le solaire", auto, "kWh/an", 0, "#15803d"),
                     ("Achete au reseau", imp, "kWh/an", 0, "#b91c1c"),
                     ("Cout du reseau", [l["cout_import"] for l in ordre],
                      "EUR/an", 0, "#b91c1c"),
                     ("Part achetee", [100 * l["part_importee"] for l in ordre],
                      "%", 0, None),
                     ("Consomme de nuit", [100 * l["part_nuit"] for l in ordre],
                      "%", 0, None)],
                    xfmt=lambda i: noms[i], titre="Poste", sur="y")
        else:
            ax1.text(.5, .5, "Lancez une simulation (F5).", ha="center",
                     va="center", fontsize=9, color="#94a3b8")
            ax1.set_xticks([]); ax1.set_yticks([])

        if lv and lv["actions"]:
            acts = lv["actions"][:14][::-1]
            noms = [a["nom"] for a in acts]
            y = np.arange(len(noms))
            gains = np.array([a["gain_an"] for a in acts])
            cols = [self.CAT_COULEURS.get(a["categorie"], "#64748b") for a in acts]
            ax2.barh(y, gains, .62, color=cols)
            for k, a in enumerate(acts):
                if a["retour"]:
                    etiq = f"  retour {a['retour']:.1f} ans"
                elif a["cout"] > 0:
                    etiq = "  jamais rembourse"
                else:
                    etiq = "  cout a chiffrer"
                ax2.text(max(gains[k], 0), y[k], etiq, va="center", fontsize=6.5,
                         color="#475569")
            ax2.set_yticks(y)
            ax2.set_yticklabels([court(n, 32) for n in noms], fontsize=7)
            ax2.set_xlabel("EUR economises par an")
            ax2.set_title("Gain annuel de chaque action, la plus payante en haut",
                          fontsize=9)
            ax2.grid(axis="x", alpha=.2, ls=":")
            ax2.axvline(0, color="#0f172a", lw=.8)
            ax2.margins(x=.22)
            vus = []
            for cat, coul in self.CAT_COULEURS.items():
                if any(a["categorie"] == cat for a in acts):
                    vus.append(matplotlib.patches.Patch(color=coul, label=cat))
            if vus:
                ax2.legend(handles=vus, fontsize=7, frameon=False, loc="lower right")
            c.hover(ax2, y,
                    [("Gain sur la facture", gains, "EUR/an", 0, "#15803d"),
                     ("Reseau evite", [a["import_evite"] for a in acts],
                      "kWh/an", 0, "#b91c1c"),
                     ("Besoin evite", [a["besoin_evite"] for a in acts],
                      "kWh/an", 0, "#0891b2"),
                     ("Cout de l'action", [a["cout"] for a in acts], "EUR", 0, None),
                     ("Retour", [a["retour"] or float("nan") for a in acts],
                      "ans", 1, None),
                     ("Gain pour 1000 EUR", [a["gain_par_1000"] or float("nan")
                                             for a in acts], "EUR/an", 0, None),
                     ("Autonomie apres", [100 * a["autonomie"] for a in acts],
                      "%", 2, "#1f4e79")],
                    xfmt=lambda i: noms[i], titre="Action", sur="y", cumul=False)
        else:
            ax2.text(.5, .5, "Cliquez sur \"Analyser les leviers\" pour chiffrer\n"
                             "chaque action et les classer par gain annuel.",
                     ha="center", va="center", fontsize=9, color="#94a3b8")
            ax2.set_xticks([]); ax2.set_yticks([])
        c.draw()

    # ---------------- onglet 8 : orientation des champs ----------------
    def _build_orientations(self):
        w = QWidget(); lay = QVBoxLayout(w)
        intro = QLabel(
            "<b>Quelle orientation donner a chaque groupe ?</b> Chaque groupe "
            "peut recevoir une inclinaison et un azimut differents des autres : "
            "c'est souvent ce qui rapporte le plus.<br>"
            "<b>Pourquoi ne pas optimiser chaque groupe separement ?</b> Parce "
            "que les groupes ne sont pas independants. Ce qui compte n'est pas "
            "la production de chacun, mais la facon dont leur <i>somme</i> se "
            "superpose a votre consommation, heure par heure, a travers la "
            "batterie. Optimises isolement, ils donneraient tous la meme "
            "reponse. Le calcul balaye donc la grille complete d'un groupe, "
            "les autres etant figes, garde le meilleur, passe au suivant, et "
            "recommence : le deuxieme groupe \"voit\" que midi est deja "
            "couvert et part de lui-meme vers le matin ou le soir.<br>"
            "<i>Un groupe = une orientation. Pour orienter vos grappes une par "
            "une, utilisez d'abord \"Separer les grappes\" dans l'onglet 2.</i>")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        row = QHBoxLayout()
        lab_obj = QLabel("Critere a optimiser :")
        lab_obj.setToolTip("Ce que le calcul cherche a ameliorer.")
        row.addWidget(lab_obj)
        self.cb_obj = QComboBox()
        aides_obj = {
            "autonomie": "Part du besoin annuel couverte sans le reseau. Le "
                         "critere par defaut d'une installation autonome.",
            "autonomie_hiver": "Autonomie sur novembre a fevrier seulement. "
                               "C'est l'hiver qui dimensionne une installation "
                               "autonome : ce critere pousse vers de fortes "
                               "inclinaisons, au prix de l'ete.",
            "import": "Kilowattheures achetes au reseau sur l'annee. Tres "
                      "proche de l'autonomie, mais exprime en energie.",
            "autoconso": "Energie produite ET reellement consommee. Favorise "
                         "l'etalement de la production sur la journee.",
            "production": "Production brute, sans tenir compte de vos usages. "
                          "Donne l'orientation classique plein sud vers 35 "
                          "degres, et fera donc converger tous les groupes vers "
                          "la meme valeur : utile comme point de comparaison.",
            "cout": "Facture annuelle d'energie : soutirage x prix du kWh, plus "
                    "l'abonnement, moins la revente eventuelle.",
        }
        for cle, (libelle, sens, unite, _f, _d) in S.ORIENT_OBJECTIFS.items():
            fleche = "maximiser" if sens > 0 else "minimiser"
            self.cb_obj.addItem(f"{libelle} ({fleche})", cle)
            self.cb_obj.setItemData(self.cb_obj.count() - 1,
                                    f"<b>{libelle}</b> &mdash; a {fleche}.<br>"
                                    f"{aides_obj.get(cle, '')}",
                                    Qt.ItemDataRole.ToolTipRole)
        self.cb_obj.setToolTip(
            "<b>Le critere change completement la reponse.</b><br>"
            "\"Production annuelle\" donne le classique plein sud a 35 degres "
            "pour tout le monde. \"Autonomie\" tient compte de vos usages et "
            "de la batterie, et c'est la que des orientations differentes "
            "deviennent interessantes.")
        row.addWidget(self.cb_obj, 1)

        lab_eff = QLabel("Finesse :")
        row.addWidget(lab_eff)
        self.cb_effort = QComboBox()
        for cle, d in S.ORIENT_EFFORTS.items():
            self.cb_effort.addItem(d["label"], cle)
        self.cb_effort.setCurrentIndex(1)
        self.cb_effort.setToolTip(
            "<b>Compromis entre precision et duree.</b><br>"
            "&bull; <b>Rapide</b> : pas de 15 degres en inclinaison et 30 en "
            "azimut, une seule passe.<br>"
            "&bull; <b>Normal</b> : la grille large est ensuite resserree "
            "autour du meilleur point, deux passes. Recommande.<br>"
            "&bull; <b>Fin</b> : grille serree, trois passes. Nettement plus "
            "long, pour un gain souvent inferieur a 0,1 point.")
        row.addWidget(self.cb_effort, 1)
        lay.addLayout(row)

        row_m = QHBoxLayout()
        lab_met = QLabel("Methode :")
        row_m.addWidget(lab_met)
        self.cb_methode = QComboBox()
        for cle, libelle in S.ORIENT_METHODES.items():
            self.cb_methode.addItem(libelle, cle)
        aide_met = (
            "<b>Conjointe</b> &mdash; on balaye la grille complete d'un groupe, "
            "les autres restant a leur orientation du moment, on garde le "
            "meilleur, puis on passe au groupe suivant et on recommence "
            "jusqu'a stabilisation.<br>"
            "Chaque groupe obtient bien sa propre inclinaison et son propre "
            "azimut, mais en tenant compte de ce que les autres produisent "
            "deja. C'est ce qui fait emerger les orientations complementaires "
            "est/ouest quand elles sont payantes.<br><br>"
            "<b>Independante</b> &mdash; chaque groupe est optimise seul face a "
            "la consommation, comme s'il etait le seul installe.<br>"
            "<i>Attention : dans ce mode les groupes n'ont aucune raison de se "
            "repartir la journee, et ils renvoient presque toujours la meme "
            "orientation. Utile pour connaitre l'optimum d'un groupe pris "
            "isolement, ou comme point de comparaison, mais le total obtenu "
            "est en general moins bon qu'en conjointe.</i>")
        self.cb_methode.setToolTip(aide_met)
        for i in range(self.cb_methode.count()):
            self.cb_methode.setItemData(i, aide_met, Qt.ItemDataRole.ToolTipRole)
        row_m.addWidget(self.cb_methode, 1)
        row_m.addStretch(0)
        lay.addLayout(row_m)

        row2 = QHBoxLayout()
        lab_pl = QLabel("Plages autorisees \u2014 inclinaison de")
        lab_pl.setToolTip(
            "<b>Bornes de la recherche.</b><br>"
            "Restreignez-les si votre support impose une contrainte : une "
            "toiture existante fixe l'inclinaison, un mur impose 90 degres, "
            "un chassis reglable ne descend pas sous 15 degres.")
        row2.addWidget(lab_pl)
        self.sp_inc_min = QDoubleSpinBox(); self.sp_inc_min.setRange(0, 90)
        self.sp_inc_min.setValue(0); self.sp_inc_min.setSuffix(" deg")
        self.sp_inc_max = QDoubleSpinBox(); self.sp_inc_max.setRange(0, 90)
        self.sp_inc_max.setValue(90); self.sp_inc_max.setSuffix(" deg")
        row2.addWidget(self.sp_inc_min); row2.addWidget(QLabel("a"))
        row2.addWidget(self.sp_inc_max)
        lab_az = QLabel("     azimut de")
        lab_az.setToolTip(
            "<b>Bornes d'azimut.</b> 180 = plein sud, 90 = est, 270 = ouest.<br>"
            "La plage 90-270 couvre tout l'hemisphere utile en France. "
            "Elargissez a 0-360 seulement pour etudier un cas particulier.")
        row2.addWidget(lab_az)
        self.sp_az_min = QDoubleSpinBox(); self.sp_az_min.setRange(0, 360)
        self.sp_az_min.setValue(90); self.sp_az_min.setSuffix(" deg")
        self.sp_az_max = QDoubleSpinBox(); self.sp_az_max.setRange(0, 360)
        self.sp_az_max.setValue(270); self.sp_az_max.setSuffix(" deg")
        row2.addWidget(self.sp_az_min); row2.addWidget(QLabel("a"))
        row2.addWidget(self.sp_az_max)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.tbl_orient = table(
            ["Optimiser", "Groupe", "kWc", "Inclinaison actuelle",
             "Azimut actuel", "Inclinaison proposee", "Azimut propose",
             "Changement"],
            tips=[
                "<b>Cochez les groupes que le calcul a le droit de reorienter.</b><br>"
                "Decochez ceux dont l'orientation est imposee : une toiture "
                "existante, un mur, un carport. Ils resteront dans le calcul, "
                "avec leur orientation actuelle, mais ne bougeront pas.",
                "Nom du groupe, repris de l'onglet 2.",
                "Puissance crete du groupe. Le plus gros groupe est optimise "
                "en premier : il prend l'orientation la plus rentable, les "
                "petits viennent ensuite couvrir les heures restantes.",
                "Inclinaison actuellement configuree, en degres.",
                "Azimut actuellement configure. 180 = plein sud.",
                "<b>Inclinaison proposee par le calcul.</b> Vide tant que "
                "l'optimisation n'a pas ete lancee.",
                "<b>Azimut propose par le calcul.</b> 180 = plein sud, "
                "90 = est, 270 = ouest.",
                "Ecart entre l'orientation actuelle et celle proposee. "
                "\"inchange\" signifie que votre reglage est deja le meilleur "
                "de la grille exploree."],
            stretch=False)
        self.tbl_orient.itemChanged.connect(self._orient_coche)
        lay.addWidget(self.tbl_orient, 0)

        row3 = QHBoxLayout()
        self.b_orient = QPushButton("Lancer l'optimisation des orientations")
        self.b_orient.setToolTip(
            "Lance la recherche. Une simulation complete est relancee pour "
            "chaque orientation testee : suivez l'avancement dans la barre "
            "d'etat, en bas.")
        self.b_orient.clicked.connect(self.run_orient)
        row3.addWidget(self.b_orient)
        self.b_orient_appl = QPushButton("Appliquer les orientations proposees")
        self.b_orient_appl.setEnabled(False)
        self.b_orient_appl.setToolTip(
            "Ecrit les orientations proposees dans l'onglet 2 et relance la "
            "simulation complete. Reversible : relancez une optimisation ou "
            "ressaisissez vos valeurs a la main.")
        self.b_orient_appl.clicked.connect(self.appliquer_orient)
        row3.addWidget(self.b_orient_appl)
        row3.addStretch(1)
        self.lbl_orient_duree = QLabel("")
        row3.addWidget(self.lbl_orient_duree)
        lay.addLayout(row3)

        self.lbl_orient = QLabel("Aucune optimisation lancee.")
        self.lbl_orient.setWordWrap(True)
        self.lbl_orient.setStyleSheet(
            "background:#f1f5f9;padding:7px;border-radius:4px;")
        lay.addWidget(self.lbl_orient)

        self.cv_orient = MplCanvas(9, 3.6)
        self.cv_orient.setToolTip(
            "<b>Carte du critere pour chaque groupe</b>, les autres groupes "
            "etant figes a leur orientation finale.<br>"
            "Survolez la carte pour lire la valeur exacte. La croix marque "
            "l'orientation retenue. Une tache large et plate signifie que "
            "l'orientation de ce groupe importe peu : vous pouvez la choisir "
            "pour des raisons pratiques." + HINT)
        self.cv_orient.set_plot(self.draw_orient,
                                "Carte du critere par orientation")
        lay.addWidget(self.cv_orient, 1)

        for widget in (self.cb_obj, self.cb_effort, self.cb_methode):
            widget.currentIndexChanged.connect(self.maj_duree_orient)
        for sp in (self.sp_inc_min, self.sp_inc_max, self.sp_az_min, self.sp_az_max):
            sp.valueChanged.connect(self.maj_duree_orient)

        self.tabs.addTab(w, "8. Orientations")
        self._orient_res = None
        self._orient_ms = 150.0      # duree mesuree d'une evaluation

    # ---- tableau des groupes a optimiser ----
    def refresh_orient(self):
        t = self.tbl_orient
        self._loading_orient = True
        actifs = [(i, c) for i, c in enumerate(self.cfg["champs"])
                  if c.get("actif", True)]
        propose = {}
        if self._orient_res:
            propose = {c["index"]: c for c in self._orient_res["apres"]["champs"]}
        t.setRowCount(len(actifs))
        for r, (i, ch) in enumerate(actifs):
            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            libre = self._orient_libres.get(i, True)
            chk.setCheckState(Qt.CheckState.Checked if libre
                              else Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, i)
            t.setItem(r, 0, chk)
            kwc = ch["n_panneaux"] * ch["wc_panneau"] / 1000.0
            t.setItem(r, 1, item(ch["nom"]))
            t.setItem(r, 2, item(f"{kwc:.2f}", align_right=True))
            t.setItem(r, 3, item(f"{float(ch['inclinaison']):.1f}", align_right=True))
            t.setItem(r, 4, item(f"{float(ch.get('azimut', 180)):.1f}", align_right=True))
            p = propose.get(i)
            if p is None:
                for c in (5, 6, 7):
                    t.setItem(r, c, item("-", align_right=True))
                continue
            d_inc = float(p["inclinaison"]) - float(ch["inclinaison"])
            d_az = float(p["azimut"]) - float(ch.get("azimut", 180))
            t.setItem(r, 5, item(f"{float(p['inclinaison']):.1f}", align_right=True,
                                 bold=True, couleur=BLEU if d_inc else None))
            t.setItem(r, 6, item(f"{float(p['azimut']):.1f}", align_right=True,
                                 bold=True, couleur=BLEU if d_az else None))
            if not d_inc and not d_az:
                t.setItem(r, 7, item("inchange", align_right=True, couleur="#64748b"))
            else:
                bouts = []
                if d_inc:
                    bouts.append(f"{d_inc:+.0f} deg d'inclinaison")
                if d_az:
                    sens = "vers l'ouest" if d_az > 0 else "vers l'est"
                    bouts.append(f"{abs(d_az):.0f} deg {sens}")
                t.setItem(r, 7, item(", ".join(bouts), align_right=True,
                                     couleur=BLEU))
        self._loading_orient = False
        self._ajuster_hauteur(t, mini=80, maxi=240)
        self.maj_duree_orient()

    def _orient_coche(self, it):
        if getattr(self, "_loading_orient", False) or it.column() != 0:
            return
        i = it.data(Qt.ItemDataRole.UserRole)
        if i is not None:
            self._orient_libres[i] = it.checkState() == Qt.CheckState.Checked
        self.maj_duree_orient()

    def maj_duree_orient(self, *_):
        n = sum(1 for i, c in enumerate(self.cfg["champs"])
                if c.get("actif", True) and self._orient_libres.get(i, True))
        if not n:
            self.lbl_orient_duree.setText(
                f"<span style='color:{ORANGE}'>Cochez au moins un groupe.</span>")
            return
        evals = S.estimer_evaluations(
            n, self.cb_effort.currentData(), self.sp_inc_min.value(),
            self.sp_inc_max.value(), self.sp_az_min.value(), self.sp_az_max.value(),
            self.cb_methode.currentData())
        sec = evals * self._orient_ms / 1000.0
        duree = f"{sec:.0f} s" if sec < 90 else f"{sec / 60:.0f} min"
        self.lbl_orient_duree.setText(
            f"{n} groupe(s) libre(s) &bull; jusqu'a {evals} simulations "
            f"&bull; <b>environ {duree}</b>")
        self.lbl_orient_duree.setToolTip(
            "Majorant : les orientations deja evaluees sont mises en cache, "
            "et le calcul s'arrete des qu'une passe complete n'ameliore plus "
            "rien. La duree reelle est souvent bien inferieure.")

    # ---- lancement ----
    def run_orient(self):
        if self.meteo is None:
            QMessageBox.information(self, "Meteo", "Chargez d'abord une serie meteo.")
            return
        self.pull_config()
        libres = [i for i, c in enumerate(self.cfg["champs"])
                  if c.get("actif", True) and self._orient_libres.get(i, True)]
        if not libres:
            QMessageBox.warning(self, "Aucun groupe",
                                "Cochez au moins un groupe a reorienter.")
            return
        if self.sp_inc_min.value() > self.sp_inc_max.value() or \
                self.sp_az_min.value() > self.sp_az_max.value():
            QMessageBox.warning(self, "Plages", "Les bornes minimales doivent "
                                                "etre inferieures aux maximales.")
            return
        cfg = copy.deepcopy(self.cfg)
        objectif = self.cb_obj.currentData()
        args = dict(libres=libres, objectif=objectif,
                    effort=self.cb_effort.currentData(),
                    inclinaison_min=self.sp_inc_min.value(),
                    inclinaison_max=self.sp_inc_max.value(),
                    azimut_min=self.sp_az_min.value(),
                    azimut_max=self.sp_az_max.value(),
                    methode=self.cb_methode.currentData())
        self._orient_t0 = __import__("time").perf_counter()
        self.b_orient.setEnabled(False)
        self._start(Worker(S.optimize_orientations, cfg, self.meteo, **args),
                    self._orient_pret,
                    f"Optimisation : {S.ORIENT_OBJECTIFS[objectif][0]}")

    def _orient_pret(self, res):
        import time
        self.b_orient.setEnabled(True)
        if res.get("evaluations"):
            ecoule = time.perf_counter() - getattr(self, "_orient_t0", 0)
            self._orient_ms = max(1000.0 * ecoule / res["evaluations"], 1.0)
        self._orient_res = res
        self.b_orient_appl.setEnabled(True)
        self.refresh_orient()
        self.show_orient()

    def show_orient(self):
        r = self._orient_res
        if not r:
            return
        cle = r["objectif"]
        libelle, sens, unite, facteur, dec = S.ORIENT_OBJECTIFS[cle]
        a = r["avant"]["metriques"][cle] * facteur
        b = r["apres"]["metriques"][cle] * facteur
        gain = (b - a) * sens
        f = lambda v, n=0: f"{v:,.{n}f}".replace(",", " ")
        coul = VERT if gain > 1e-9 else "#64748b"
        mots = ("Aucune amelioration : votre reglage actuel est deja le meilleur "
                "de la grille exploree." if gain <= 1e-9 else
                f"Gain de <b>{f(abs(b - a), dec)} {unite}</b>")

        ma, mb = r["avant"]["metriques"], r["apres"]["metriques"]
        lignes = [
            ("Autonomie annuelle", 100 * ma["autonomie"], 100 * mb["autonomie"], "%", 2),
            ("Autonomie novembre-fevrier", 100 * ma["autonomie_hiver"],
             100 * mb["autonomie_hiver"], "%", 2),
            ("Soutire au reseau", ma["import"], mb["import"], "kWh/an", 0),
            ("Production brute", ma["production"], mb["production"], "kWh/an", 0),
            ("Autoconsomme", ma["autoconso"], mb["autoconso"], "kWh/an", 0),
            ("Ecrete faute d'usage", ma["ecrete"], mb["ecrete"], "kWh/an", 0),
            ("Cout annuel d'energie", ma["cout"], mb["cout"], "EUR/an", 0),
        ]
        tab = "".join(
            f"<tr><td>{nom}&nbsp;&nbsp;</td>"
            f"<td align='right'>{f(v0, d)}</td>"
            f"<td align='right'>&nbsp;&rarr;&nbsp;<b>{f(v1, d)}</b></td>"
            f"<td>&nbsp;{u}</td>"
            f"<td align='right'>&nbsp;&nbsp;{f(v1 - v0, d) if abs(v1 - v0) >= 10 ** -d else ''}</td>"
            f"</tr>"
            for nom, v0, v1, u, d in lignes)

        n_dist = len({(round(c["inclinaison"], 1), round(c["azimut"], 1))
                      for c in r["apres"]["champs"]})
        if r.get("methode") == "independante" and len(r["apres"]["champs"]) > 1:
            note = ("<br><i>Mode independant : chaque groupe a ete optimise seul, "
                    "sans voir les autres. Rien ne les pousse a se repartir la "
                    "journee, d'ou des orientations souvent identiques. Relancez "
                    "en methode conjointe pour laisser les groupes se "
                    "completer.</i>")
        elif n_dist == 1 and len(r["apres"]["champs"]) > 1:
            note = ("<br><i>Tous les groupes convergent vers la meme orientation. "
                    "C'est un resultat, pas un echec : quand la production "
                    "excede largement les besoins ou que la batterie absorbe "
                    "tout le midi, etaler les orientations n'apporte rien. "
                    "Essayez le critere \"autonomie de novembre a fevrier\", "
                    "ou reduisez la batterie pour voir l'etalement devenir "
                    "payant.</i>")
        else:
            note = (f"<br><i>{n_dist} orientations distinctes retenues : les "
                    f"groupes se repartissent la journee.</i>")

        self.lbl_orient.setText(
            f"<b>{libelle}</b> &mdash; <span style='color:{coul}'>{mots}</span> "
            f"&nbsp;&bull;&nbsp; {r['evaluations']} orientations testees, "
            f"methode {r.get('methode', 'conjointe')}, "
            f"finesse \"{S.ORIENT_EFFORTS[r['effort']]['label']}\"<br>"
            f"<table cellspacing='0' cellpadding='1'>{tab}</table>{note}")
        self.draw_orient()

    def draw_orient(self, cv=None):
        r = self._orient_res
        c = self._cv(cv, self.cv_orient); c.clear()
        grilles = (r or {}).get("grilles") or {}
        if not grilles:
            c.draw(); return
        cle = r["objectif"]
        libelle, sens, unite, facteur, dec = S.ORIENT_OBJECTIFS[cle]
        retenu = {x["index"]: x for x in r["apres"]["champs"]}
        n = len(grilles)
        cols = min(n, 4)
        rows = (n + cols - 1) // cols
        axes = c.fig.subplots(rows, cols, squeeze=False)
        for k in range(rows * cols):
            ax = axes[k // cols][k % cols]
            if k >= n:
                ax.axis("off"); continue
            idx = list(grilles)[k]
            g = grilles[idx]
            x = np.array(g["azimuts"], dtype=float)
            y = np.array(g["inclinaisons"], dtype=float)
            z = np.array(g["carte"], dtype=float) * facteur
            im = ax.pcolormesh(x, y, z, shading="nearest",
                               cmap="viridis" if sens > 0 else "viridis_r")
            p = retenu.get(idx)
            if p:
                ax.plot([p["azimut"]], [p["inclinaison"]], marker="x", ms=11,
                        mew=2.5, color="#ffffff")
                ax.plot([p["azimut"]], [p["inclinaison"]], marker="x", ms=8,
                        mew=1.5, color="#b91c1c")
            nom = self.cfg["champs"][idx]["nom"]
            court = nom if len(nom) <= 30 else "..." + nom[-27:]
            ax.set_title(court, fontsize=8)
            ax.set_xlabel("Azimut (deg)", fontsize=7)
            ax.set_ylabel("Inclinaison (deg)", fontsize=7)
            ax.tick_params(labelsize=6)
            cb = c.fig.colorbar(im, ax=ax)
            cb.ax.tick_params(labelsize=6)
            c.hover2d(ax, x, y, z,
                      ("Azimut (deg)", "Inclinaison (deg)", libelle, unite, dec),
                      titre=nom)
        c.fig.suptitle(
            f"{libelle} selon l'orientation de chaque groupe "
            f"(les autres groupes restant a leur orientation finale)", fontsize=9)
        c.draw()

    def appliquer_orient(self):
        if not self._orient_res:
            return
        S.appliquer_orientations(self.cfg, self._orient_res)
        self.refresh_champs()
        self.refresh_orient()
        self.run_sim()
        self.tabs.setCurrentIndex(1)
        self.statusBar().showMessage(
            "Orientations appliquees aux groupes et simulation relancee.", 6000)

    # ======================= configuration =======================
    def push_config(self):
        self.form_site.set(self.cfg["site"])
        self.update_aide_annees()
        self.form_module.set(self.cfg["module"])
        self.form_sys.set(self.cfg["systeme"])
        self.form_eco.set(self.cfg["economie"])
        self.refresh_champs()
        self.refresh_bom()
        self.tab_postes.refresh()
        self.refresh_orient()

    def pull_config(self):
        self.cfg["site"].update(self.form_site.get())
        self.cfg["module"].update(self.form_module.get())
        self.cfg["systeme"].update(self.form_sys.get())
        self.cfg["economie"].update(self.form_eco.get())
        self.tab_postes.commit()

    def _tab_changed(self, idx):
        """L'onglet des champs depend des limites saisies dans l'onglet 4 :
        on rafraichit les tensions et les couleurs en y revenant."""
        if idx == 1 and getattr(self, "form_sys", None) is not None:
            self.cfg["systeme"].update(self.form_sys.get())
            self.cfg["module"].update(self.form_module.get())
            self.refresh_champs()

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
        db = s.get("base_donnees", "PVGIS-SARAH3")
        try:
            M.verifier_annees(int(s["annee_debut"]), int(s["annee_fin"]), db)
        except ValueError as e:
            QMessageBox.warning(self, "Periode indisponible", str(e))
            return
        self._start(Worker(M.download_pvgis, float(s["latitude"]), float(s["longitude"]),
                           int(s["annee_debut"]), int(s["annee_fin"]), db),
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
        self.draw_meteo()

    def draw_meteo(self, cv=None):
        """Rayonnement et temperature mensuels de la serie meteo chargee."""
        if not self.meteo:
            return
        s = M.meteo_summary(self.meteo)
        c = self._cv(cv, self.cv_meteo); c.clear()
        ax = c.fig.add_subplot(111)
        ax.bar(MOIS, s["ghi_mensuel"], color="#fbbf24",
               label="Rayonnement horizontal (kWh/m2/mois)")
        ax.set_ylabel("kWh/m2/mois"); ax.set_title("Rayonnement horizontal mensuel moyen")
        ax.grid(axis="y", alpha=.2, ls=":")
        ax2 = ax.twinx()
        tm = [self.meteo["T2m"][self.meteo["month"] == k + 1].mean() for k in range(12)]
        ax2.plot(MOIS, tm, color="#b91c1c", marker="o",
                 label="Temperature moyenne (C)")
        ax2.set_ylabel("Temperature moyenne (C)", color="#b91c1c")
        c.hover([ax, ax2], np.arange(12),
                [("Rayonnement horizontal", s["ghi_mensuel"], "kWh/m2", 0, "#fbbf24"),
                 ("Temperature moyenne", tm, "C", 1, "#b91c1c")],
                xfmt=lambda i: MOIS[i], titre="Moyenne mensuelle")
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
        # le classement des leviers et le budget de consommation portaient
        # sur l'ancienne configuration : les garder afficherait, a cote des
        # nouveaux resultats, des chiffres calcules sur une autre installation
        self._leviers = None
        self._budget = None
        self.show_results()

    @staticmethod
    def _opt(valeur, gabarit="{:.3f}", defaut="-"):
        """Formate une grandeur qui peut ne pas etre calculable."""
        return defaut if valeur is None else gabarit.format(valeur)

    def _retour_txt(self, retour, d=1):
        """Temps de retour en annees decimales, ou '>horizon' si jamais rembourse."""
        if retour is not None:
            return f"{retour:.{d}f}"
        return ">" + str(self.cfg["economie"].get("duree_analyse_ans", 25))

    def show_results_kpi(self):
        """Bandeau de tete. L'investissement affiche est celui du PERIMETRE
        SOLAIRE, avec son propre temps de retour : c'est le couple qui a un
        sens pour juger le dimensionnement. Le projet complet est rappele en
        petit juste en dessous."""
        k, e = self.res["kpi"], self.res["eco"]
        f = lambda v, d=0: f"{v:,.{d}f}".replace(",", " ")
        hors = e["capex_hors_solaire"]
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
            f"<td><span style='font-size:20pt;color:{BLEU}'>"
            f"<b>{f(e['capex_solaire'])} EUR</b></span>"
            f"<br><small>INSTALLATION SOLAIRE &nbsp;({e['cout_par_wc']:.2f} EUR/Wc)"
            f"</small></td>"
            f"<td><span style='font-size:20pt'><b>"
            f"{self._retour_txt(e['retour_ans_vs_sans_pv'])} ans</b>"
            f"</span><br><small>RETOUR DE L'INSTALLATION SOLAIRE</small></td>"
            f"</tr></table>"
            f"<small style='color:#64748b'>Projet complet "
            f"{f(e['capex_total'])} EUR (dont {f(hors)} EUR hors perimetre "
            f"solaire : chauffe-eau, insert, isolation) &mdash; retour "
            f"{self._retour_txt(e['retour_ans_vs_actuel'])} ans face a la "
            f"facture declaree.</small>")

    def show_results(self):
        r, k, e = self.res, self.res["kpi"], self.res["eco"]
        f = lambda v, d=0: f"{v:,.{d}f}".replace(",", " ")
        self.show_results_kpi()

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
            bud = budget[i][1] if budget else None
            it_b = item(self._opt(bud, "{:.1f}"), align_right=True)
            if budget and bud is None:
                it_b.setToolTip(
                    "Meme reduite a 2 % de sa valeur, la consommation de ce "
                    "mois ne permet pas d'atteindre l'objectif d'autonomie : "
                    "c'est la production, pas la consommation, qui manque.")
                it_b.setForeground(QColor(ROUGE))
            t.setItem(i, 11, it_b)
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
        self._attrib = S.attribution_import(
            r, self.meteo, float(self.cfg["economie"]["prix_kwh_achat"]))
        self.show_leviers()
        self.statusBar().showMessage(
            f"Simulation terminee sur {k['n_years']:.0f} annees de meteo reelle.", 6000)

    def draw_mois(self, cv=None):
        m = self.res["mensuel"]
        c = self._cv(cv, self.cv_mois); c.clear()
        ax = c.fig.add_subplot(121)
        x = np.arange(12)
        ax.bar(x, m["autoconso"], .6, label="Autoconsomme", color="#15803d")
        ax.bar(x, m["import"], .6, bottom=m["autoconso"], label="Soutire", color="#b91c1c")
        ax.plot(x, m["production_dc"], color="#d97706", marker="o", ms=3,
                lw=2, label="Production")
        ax.set_xticks(x); ax.set_xticklabels(MOIS, fontsize=7)
        ax.set_ylabel("kWh/mois"); ax.legend(fontsize=7, frameon=False)
        ax.set_title("Bilan mensuel", fontsize=9)
        ax.grid(axis="y", alpha=.2, ls=":")
        ax2 = c.fig.add_subplot(122)
        cols = ["#15803d" if a > .9 else "#d97706" if a > .7 else "#b91c1c"
                for a in m["autonomie"]]
        ax2.bar(x, 100 * m["autonomie"], .6, color=cols)
        cible = 100 * float(self.cfg["options"].get("autonomie_cible", .92))
        ax2.axhline(cible, color=BLEU, ls="--", lw=1,
                    label=f"Objectif {cible:.0f} %")
        ax2.bar([], [], color="#15803d", label="Autonomie du mois")
        ax2.grid(axis="y", alpha=.2, ls=":")
        ax2.set_xticks(x); ax2.set_xticklabels(MOIS, fontsize=7)
        ax2.set_ylim(0, 105); ax2.set_ylabel("%")
        ax2.set_title("Autonomie mensuelle", fontsize=9)
        c.hover(ax, x,
                [("Production", m["production_dc"], "kWh", 0, "#d97706"),
                 ("Autoconsomme", m["autoconso"], "kWh", 0, "#15803d"),
                 ("Soutire au reseau", m["import"], "kWh", 0, "#b91c1c"),
                 ("Besoin total", m["besoin"], "kWh", 0, "#0f172a"),
                 ("Ecrete / injecte", m["ecrete"] + m["export"], "kWh", 0, "#7c3aed"),
                 ("Autonomie", 100 * m["autonomie"], "%", 1, BLEU)],
                xfmt=lambda i: MOIS[i], titre="Bilan mensuel")
        c.hover(ax2, x,
                [("Autonomie", 100 * m["autonomie"], "%", 1, BLEU),
                 ("Besoin total", m["besoin"], "kWh", 0, "#0f172a"),
                 ("Soutire au reseau", m["import"], "kWh", 0, "#b91c1c")],
                xfmt=lambda i: MOIS[i], titre="Autonomie mensuelle")
        c.draw()

    def draw_jour(self, cv=None):
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
        c = self._cv(cv, self.cv_jour); c.clear()
        ax = c.fig.add_subplot(111)
        d = np.arange(1, len(sel) + 1)
        ax.bar(d, j["production"][sel], .7, color="#fbbf24", label="Production")
        ax.plot(d, j["besoin"][sel], color=BLEU, lw=1.6, marker="o", ms=3,
                label="Besoin")
        ax.bar(d, j["import"][sel], .35, color="#b91c1c", label="Soutirage")
        ax.set_xlabel(f"Jour de {MOIS[mo - 1]} {year}"); ax.set_ylabel("kWh/jour")
        ax.legend(fontsize=7, frameon=False)
        c.hover(ax, d,
                [("Production", j["production"][sel], "kWh", 1, "#fbbf24"),
                 ("Besoin total", j["besoin"][sel], "kWh", 1, BLEU),
                 ("Consommation usages", j["consommation"][sel], "kWh", 1, "#0891b2"),
                 ("Soutire au reseau", j["import"][sel], "kWh", 1, "#b91c1c"),
                 ("Autonomie", 100 * j["autonomie"][sel], "%", 0, "#15803d"),
                 ("Charge minimale batterie", j["soc_min"][sel], "kWh", 1, "#7c3aed")],
                xfmt=lambda i: f"{MOIS[mo - 1]} {int(d[i])}, {year}",
                titre="Journee")
        c.draw()

    def draw_profil(self, cv=None):
        r = self.res
        met = self.meteo
        c = self._cv(cv, self.cv_profil); c.clear()
        axes = c.fig.subplots(3, 4, sharex=True)
        d = r["dispatch"]
        for k in range(12):
            ax = axes[k // 4][k % 4]
            sel = met["month"] == k + 1
            prof_p = [r["pv_dc"][sel & (met["hour"] == h)].mean() for h in range(24)]
            prof_c = [d["besoin_total"][sel & (met["hour"] == h)].mean() for h in range(24)]
            prof_i = [d["import"][sel & (met["hour"] == h)].mean() for h in range(24)]
            ax.fill_between(range(24), prof_p, color="#fbbf24", alpha=.8,
                            label="Production PV" if k == 0 else None)
            ax.plot(range(24), prof_c, color=BLEU, lw=1.4,
                    label="Besoin" if k == 0 else None)
            ax.fill_between(range(24), prof_i, color="#b91c1c", alpha=.6,
                            label="Soutire au reseau" if k == 0 else None)
            ax.grid(alpha=.18, ls=":")
            ax.set_title(MOIS[k], fontsize=8)
            ax.tick_params(labelsize=6)
            c.hover(ax, np.arange(24),
                    [("Production PV", prof_p, "kW", 2, "#fbbf24"),
                     ("Besoin", prof_c, "kW", 2, BLEU),
                     ("Soutire au reseau", prof_i, "kW", 2, "#b91c1c")],
                    xfmt=lambda i: f"{i:02d} h - {(i + 1) % 24:02d} h",
                    titre=f"{MOIS[k]}, journee moyenne")
        c.fig.suptitle("Journee moyenne de chaque mois, en kW", fontsize=9)
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
            f"duree de vie estimee "
            f"{self._opt(S.duree_vie_batterie_ans(k['cycles_batterie_an']), '{:.0f} ans', 'non calculable')}"
            f"<br>"
            f"Etat de charge minimal atteint : {d['soc'].min():.1f} kWh &bull; "
            f"maximal {d['soc'].max():.1f} kWh<br>"
            f"<b>Reseau</b> : soutirage {f(k['import_an'])} kWh/an &bull; "
            f"ecrete {f(k['ecrete_an'])} kWh/an &bull; "
            f"injecte {f(k['export_an'])} kWh/an")
        self.draw_soc()

    def draw_soc(self, cv=None):
        """Etat de charge de la batterie sur toute la serie meteo."""
        if not self.res:
            return
        d = self.res["dispatch"]
        c = self._cv(cv, self.cv_soc); c.clear()
        ax = c.fig.add_subplot(111)
        soc = d["soc"]
        n = len(soc)
        step = max(n // 4000, 1)
        ax.plot(np.arange(0, n, step) / 24.0, soc[::step], lw=.5, color=BLEU,
                label="Etat de charge (kWh)")
        ax.axhline(d["soc_min"], color="#b91c1c", ls="--", lw=1,
                   label=f"Plancher {d['soc_min']:.1f} kWh")
        ax.axhline(d["soc_max"], color="#15803d", ls="--", lw=1,
                   label=f"Plafond {d['soc_max']:.1f} kWh")
        ax.grid(alpha=.2, ls=":")
        ax.set_xlabel("Jour de la serie"); ax.set_ylabel("Etat de charge (kWh)")
        ax.set_title("Etat de charge de la batterie sur toute la serie", fontsize=9)
        jours = np.arange(0, n, step) / 24.0
        dates = self.meteo["dt_loc"][::step]
        c.hover(ax, jours,
                [("Etat de charge", soc[::step], "kWh", 1, BLEU),
                 ("Remplissage", 100 * (soc[::step] - d["soc_min"]) /
                  max(d["utile"], 1e-9), "%", 0, "#15803d")],
                xfmt=lambda i: str(dates[i].astype("datetime64[h]")).replace("T", " a ") + " h",
                titre="Batterie")
        c.draw()

    def show_eco(self):
        """Bilan a deux perimetres.

        Colonne de gauche : l'installation solaire seule, comparee a la meme
        maison sans panneaux ni batterie. Colonne de droite : le projet
        complet, compare a la facture d'energie declaree avant travaux.
        Le premier couple sert a dimensionner, le second a budgeter.
        """
        e = self.res["eco"]; k = self.res["kpi"]
        f = lambda v, n=0: f"{v:,.{n}f}".replace(",", " ")
        horizon = int(self.cfg["economie"]["duree_analyse_ans"])
        prix = float(self.cfg["economie"]["prix_kwh_achat"])
        marge = e["lcoe_kwh_utile"] is not None and e["lcoe_kwh_utile"] < prix

        self.txt_eco.setHtml(
            f"<table cellpadding=4 width='100%'>"
            f"<tr><th align=left></th>"
            f"<th align=right style='color:{BLEU}'>Installation solaire</th>"
            f"<th align=right style='color:#64748b'>Projet complet</th>"
            f"<th align=left></th></tr>"

            f"<tr><td>Investissement</td>"
            f"<td align=right><b>{f(e['capex_solaire'])} EUR</b></td>"
            f"<td align=right>{f(e['capex_total'])} EUR</td>"
            f"<td>{e['cout_par_wc']:.2f} EUR/Wc sur le perimetre solaire</td></tr>"

            f"<tr><td>Economie annuelle</td>"
            f"<td align=right><b>{f(e['economie_vs_sans_pv'])} EUR/an</b></td>"
            f"<td align=right>{f(e['economie_vs_actuel'])} EUR/an</td>"
            f"<td>solaire : {f(e['kwh_evites_an'])} kWh/an non achetes</td></tr>"

            f"<tr><td>Temps de retour</td>"
            f"<td align=right><b>{self._retour_txt(e['retour_ans_vs_sans_pv'])} ans</b></td>"
            f"<td align=right>{self._retour_txt(e['retour_ans_vs_actuel'])} ans</td>"
            f"<td>inflation energie "
            f"{100 * float(self.cfg['economie']['inflation_energie']):.1f} %/an</td></tr>"

            f"<tr><td>Gain cumule a {horizon} ans</td>"
            f"<td align=right><b>{f(e['gain_cumule_solaire'])} EUR</b></td>"
            f"<td align=right>{f(e['gain_cumule_projet'])} EUR</td>"
            f"<td></td></tr>"

            f"<tr><td colspan=4><hr></td></tr>"

            f"<tr><td>Cout annuel d'energie AVEC PV</td>"
            f"<td align=right colspan=2><b>{f(e['cout_annuel_avec_pv'])} EUR/an</b></td>"
            f"<td>dont bois {f(e['cout_bois'])} EUR "
            f"({e['steres']:.1f} steres)</td></tr>"
            f"<tr><td>La meme maison SANS PV ni batterie</td>"
            f"<td align=right colspan=2>{f(e['cout_annuel_sans_pv'])} EUR/an</td>"
            f"<td>memes equipements, meme bois</td></tr>"
            f"<tr><td>Facture declaree avant travaux</td>"
            f"<td align=right colspan=2>{f(e['facture_actuelle'])} EUR/an</td>"
            f"<td>reference du projet complet</td></tr>"

            f"<tr><td colspan=4><hr></td></tr>"

            f"<tr><td>Cout du kWh que le solaire evite d'acheter</td>"
            f"<td align=right colspan=2><b>"
            f"{self._opt(e['lcoe_kwh_utile'], '{:.3f} EUR/kWh')}</b></td>"
            f"<td>reseau : {prix:.3f} EUR/kWh"
            + (f" &mdash; <span style='color:{VERT if marge else ROUGE}'>"
               f"{'moins cher que le reseau' if marge else 'plus cher que le reseau'}"
               f"</span>" if e['lcoe_kwh_utile'] is not None else
               " &mdash; l'installation ne couvre aucun besoin")
            + f"</td></tr>"
            f"<tr><td>Cout du kWh de batterie installe</td>"
            f"<td align=right colspan=2>{f(e['cout_par_kwh_batterie'])} EUR/kWh</td>"
            f"<td>modules et onduleurs : "
            f"{e['cout_pv_par_wc']:.2f} EUR/Wc</td></tr>"
            f"<tr><td>Energie perdue faute d'usage</td>"
            f"<td align=right colspan=2>{f(e['kwh_perdus_an'])} kWh/an</td>"
            f"<td>soit {f(e['valeur_perdue_an'])} EUR/an de valeur potentielle</td></tr>"
            f"</table>"
            f"<p style='color:#64748b'><i>Colonne de gauche : ce que les "
            f"panneaux, l'onduleur et la batterie coutent et rapportent, a "
            f"maison inchangee. C'est elle qui doit guider le dimensionnement. "
            f"Colonne de droite : le projet entier, chauffe-eau, insert et "
            f"isolation compris, face a la facture d'avant travaux.</i></p>")

    # ======================= balayages =======================
    def run_sweep(self):
        if self.meteo is None:
            return
        self.pull_config()
        var = self.cb_sweep.currentData()
        try:
            vals = S.parse_sweep_values(self.ed_sweep.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Valeurs", str(exc))
            return
        if not vals:
            return
        self._start(Worker(S.sweep, self.cfg, self.meteo, var, vals),
                    lambda out: self.show_sweep(var, out), "Balayage")

    def open_sweep_detail(self, out, xval):
        candidates = out
        sel = min(candidates, key=lambda o: abs(float(o["valeur"]) - float(xval)))
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Analyse detaillee — {float(sel['valeur']):g}")
        dlg.resize(1100, 700)
        lay = QVBoxLayout(dlg)
        html = (
            f"<b>Valeur :</b> {float(sel['valeur']):g}<br>"
            f"<b>Autonomie :</b> {100 * sel['autonomie']:.2f} %<br>"
            f"<b>Production :</b> {sel['production']:.0f} kWh/an<br>"
            f"<b>Import reseau :</b> {sel['import']:.0f} kWh/an<br>"
            f"<b>Ecrete :</b> {sel['ecrete']:.0f} kWh/an<br>"
            f"<b>Cout de l'installation solaire :</b> {sel['capex']:.0f} EUR"
            f" &nbsp;({sel.get('cout_par_wc', 0):.2f} EUR/Wc)<br>"
            f"<b>Retour de l'installation solaire :</b> "
            f"{self._retour_txt(sel['retour'])} ans<br>"
            f"<b>Economie annuelle imputable au solaire :</b> "
            f"{sel.get('economie_vs_sans_pv', 0):.0f} EUR/an<br>"
            f"<b>ROI marginal :</b> {self._opt(sel['rentabilite_marginale'])}"
            f"<br><span style='color:#64748b'>Projet complet "
            f"{sel.get('capex_total', sel['capex']):.0f} EUR, dont "
            f"{sel.get('capex_hors_solaire', 0):.0f} EUR hors perimetre solaire "
            f"(inchanges pendant le balayage).</span>"
        )
        lab = QLabel(html)
        lay.addWidget(lab)

        cv = MplCanvas(10, 5)
        months = list(MOIS)
        prod = np.asarray(sel["production_mensuel"]) / 1000.0
        aut = np.asarray(sel["autonomie_mensuel"]) * 100.0
        imp = np.asarray(sel["import_mensuel"])
        ax1 = cv.fig.add_subplot(2, 2, 1)
        ax1.plot(months, prod, marker="o", color=BLEU, lw=2)
        ax1.set_title("Production mensuelle")
        ax1.set_ylabel("MWh/mois")
        ax1.grid(alpha=.25, ls=":")
        ax1.tick_params(axis="x", rotation=30)

        ax2 = cv.fig.add_subplot(2, 2, 2)
        ax2.plot(months, aut, marker="o", color=VERT, lw=2)
        ax2.set_title("Taux d'autonomie")
        ax2.set_ylabel("%")
        ax2.grid(alpha=.25, ls=":")
        ax2.tick_params(axis="x", rotation=30)

        ax3 = cv.fig.add_subplot(2, 2, 3)
        ax3.plot(months, imp, marker="o", color=ORANGE, lw=2)
        ax3.set_title("Import reseau")
        ax3.set_ylabel("kWh/mois")
        ax3.grid(alpha=.25, ls=":")
        ax3.tick_params(axis="x", rotation=30)

        ax4 = cv.fig.add_subplot(2, 2, 4)
        ax4.axis("off")
        ax4.text(0.02, 0.95, "Synthese", fontsize=10, fontweight="bold", va="top")
        ax4.text(0.02, 0.75, f"Autonomie : {100 * sel['autonomie']:.2f}%", va="top")
        ax4.text(0.02, 0.60, f"Production : {sel['production']:.0f} kWh/an", va="top")
        ax4.text(0.02, 0.45, f"Import : {sel['import']:.0f} kWh/an", va="top")
        ax4.text(0.02, 0.30, f"Ecrete : {sel['ecrete']:.0f} kWh/an", va="top")
        ax4.text(0.02, 0.15,
                 f"ROI marginal : {self._opt(sel['rentabilite_marginale'])}", va="top")

        mens = lambda i: MOIS[i]
        for a, lab, vals, unite, dec, col in (
                (ax1, "Production", prod, "MWh", 3, BLEU),
                (ax2, "Autonomie", aut, "%", 2, VERT),
                (ax3, "Import reseau", imp, "kWh", 0, ORANGE)):
            cv.hover(a, np.arange(12),
                     [(lab, vals, unite, dec, col),
                      ("Production", prod, "MWh", 3, BLEU),
                      ("Autonomie", aut, "%", 2, VERT),
                      ("Import reseau", imp, "kWh", 0, ORANGE)][1:],
                     xfmt=mens, titre=f"{float(sel['valeur']):g} - mois")
        cv.draw()
        lay.addWidget(cv, 1)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)
        dlg.exec()

    def _build_grille(self):
        """Recherche d'optimum a deux dimensions.

        Panneaux et batterie ne se dimensionnent pas l'un apres l'autre :
        des panneaux sans batterie produisent un surplus qu'on jette, une
        batterie sans panneaux n'a rien a stocker. Il faut la grille.
        """
        w = QWidget(); lay = QVBoxLayout(w)
        intro = QLabel(
            "<b>Quel couple puissance PV / capacite batterie choisir ?</b> "
            "Chaque combinaison est reellement simulee sur toute la serie "
            "meteo. Le <i>chemin de croissance</i>, a gauche, donne l'ordre "
            "dans lequel agrandir l'installation et s'arrete des que la "
            "tranche suivante depasse le seuil de rentabilite fixe dans la "
            "barre du haut.")
        intro.setWordWrap(True)
        intro.setStyleSheet("background:#f1f5f9;padding:6px;border-radius:4px;")
        lay.addWidget(intro)

        row = QHBoxLayout()
        aide_liste = ("<b>Valeurs a tester.</b><br>"
                      "Liste separee par des virgules, ou intervalle au format "
                      "<i>debut:fin@pas</i>. Exemple : <i>10:40@5</i> teste "
                      "10, 15, 20, 25, 30, 35 et 40.<br>"
                      "Commencez le plus bas possible : c'est la premiere "
                      "tranche qui est la plus rentable, et il faut la voir.")
        lab1 = QLabel("Puissances PV (kWc) :")
        self.ed_grille_kwc = QLineEdit("5:40@5")
        lab2 = QLabel("Capacites batterie (kWh) :")
        self.ed_grille_batt = QLineEdit("0:128@16")
        for lab, ed in ((lab1, self.ed_grille_kwc), (lab2, self.ed_grille_batt)):
            lab.setToolTip(aide_liste); ed.setToolTip(aide_liste)
            ed.setMaximumWidth(150)
            ed.textChanged.connect(self._maj_taille_grille)
            row.addWidget(lab); row.addWidget(ed)

        lab3 = QLabel("Critere :")
        self.cb_grille_critere = QComboBox()
        for cle, (libelle, _sens, _u, _d, aide) in S.CRITERES_GRILLE.items():
            self.cb_grille_critere.addItem(libelle, cle)
        self.cb_grille_critere.setToolTip(
            "<b>Sur quoi juge-t-on le meilleur couple ?</b><br><br>"
            + "<br><br>".join(
                f"<b>{lib}</b> : {aide}"
                for lib, _s, _u, _d, aide in S.CRITERES_GRILLE.values()))
        self.cb_grille_critere.setCurrentIndex(0)
        self.cb_grille_critere.currentIndexChanged.connect(
            lambda *_: self.show_grille() if getattr(self, "_grille_res", None)
            else None)
        row.addWidget(lab3); row.addWidget(self.cb_grille_critere)

        self.btn_grille = QPushButton("Chercher l'optimum")
        self.btn_grille.setToolTip(
            "Simule toutes les combinaisons. Une simulation dure environ "
            "un dixieme de seconde : une grille de 60 points prend quelques "
            "secondes.")
        self.btn_grille.clicked.connect(self.run_grille)
        row.addWidget(self.btn_grille)
        self.lbl_taille_grille = QLabel("")
        row.addWidget(self.lbl_taille_grille)
        row.addStretch(1)
        lay.addLayout(row)

        self.lbl_grille = QLabel(
            "Lancez la recherche pour voir par quoi commencer et jusqu'ou aller.")
        self.lbl_grille.setWordWrap(True)
        self.lbl_grille.setStyleSheet(
            "background:#fff7ed;padding:7px;border-radius:4px;")
        lay.addWidget(self.lbl_grille)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tbl_grille = table(
            ["Etape", "kWc", "Batterie (kWh)", "Cout cumule (EUR)",
             "Cout de l'etape (EUR)", "Gain de l'etape (EUR/an)",
             "Retour de l'etape (ans)", "Autonomie"],
            tips=["Ce que l'on ajoute a cette etape. L'ordre est celui du "
                  "meilleur rapport : a chaque fois, la tranche qui se "
                  "rembourse le plus vite entre un cran de panneaux et un "
                  "cran de batterie.",
                  "Puissance crete totale apres cette etape.",
                  "Capacite nominale totale apres cette etape.",
                  "Investissement solaire cumule depuis le point de depart.",
                  "Ce que coute cette seule etape.",
                  "Euros economises en plus chaque annee grace a cette seule "
                  "etape.",
                  "<b>Annees pour que cette etape se rembourse toute seule.</b><br>"
                  "C'est le critere d'arret : la recherche s'arrete des que "
                  "la meilleure etape suivante depasse le seuil.",
                  "Taux d'autonomie annuel atteint apres cette etape."])
        split.addWidget(self.tbl_grille)

        self.cv_grille = MplCanvas(7, 4)
        self.cv_grille.setToolTip(
            "<b>Carte du critere choisi.</b> Chaque case est une simulation "
            "complete. La croix marque l'optimum, la ligne blanche le chemin "
            "de croissance etape par etape." + HINT)
        self.cv_grille.set_plot(self.draw_grille, "Optimum PV x batterie")
        split.addWidget(self.cv_grille)
        split.setSizes([620, 720])
        lay.addWidget(split, 1)
        self._grille_res = None
        self._maj_taille_grille()
        return w

    def _maj_taille_grille(self, *_):
        """Annonce le nombre de simulations avant de les lancer."""
        try:
            nk = len(S.parse_sweep_values(self.ed_grille_kwc.text()))
            nb = len(S.parse_sweep_values(self.ed_grille_batt.text()))
        except ValueError as exc:
            self.lbl_taille_grille.setText(
                f"<span style='color:{ROUGE}'>{exc}</span>")
            return
        n = nk * nb
        self.lbl_taille_grille.setText(
            f"{nb} x {nk} = <b>{n} simulations</b>, environ "
            f"{max(n * 0.1, 0.5):.0f} s")

    def run_grille(self):
        if self.meteo is None:
            self.statusBar().showMessage("Chargez d'abord une serie meteo.", 4000)
            return
        self.pull_config()
        try:
            kwc = S.parse_sweep_values(self.ed_grille_kwc.text())
            batt = S.parse_sweep_values(self.ed_grille_batt.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Valeurs", str(exc))
            return
        if len(kwc) < 2 or len(batt) < 2:
            QMessageBox.warning(
                self, "Valeurs",
                "Il faut au moins deux puissances et deux capacites pour "
                "qu'une grille ait un sens.")
            return
        if S.total_kwc(self.cfg) <= 0:
            QMessageBox.warning(
                self, "Champs PV",
                "Aucun panneau actif : la mise a l'echelle de la puissance "
                "n'a pas de point de depart. Renseignez l'onglet 2.")
            return
        self._start(Worker(S.grille_dimensionnement, self.cfg, self.meteo,
                           kwc, batt),
                    self._grille_prete, "Recherche de l'optimum")

    def _grille_prete(self, res):
        self._grille_res = res
        self.show_grille()
        self.statusBar().showMessage(
            f"{len(res['batt']) * len(res['kwc'])} combinaisons simulees.", 8000)

    def show_grille(self):
        g = getattr(self, "_grille_res", None)
        if not g:
            return
        seuil = float(self.sp_seuil_tranche.value())
        chemin = S.chemin_croissance(g, seuil_ans=seuil)
        self._grille_chemin = chemin
        f = lambda v, n=0: "-" if v is None else f"{v:,.{n}f}".replace(",", " ")

        t = self.tbl_grille
        etapes = chemin["etapes"]
        t.setRowCount(len(etapes))
        for r, e in enumerate(etapes):
            t.setItem(r, 0, item(e["quoi"], bold=(r == len(etapes) - 1)))
            t.setItem(r, 1, item(f"{e['kwc']:.1f}", align_right=True))
            t.setItem(r, 2, item(f"{e['batt']:.0f}", align_right=True))
            t.setItem(r, 3, item(f(e["capex"]), align_right=True))
            t.setItem(r, 4, item(f(e["cout_tranche"]), align_right=True))
            t.setItem(r, 5, item(f(e["gain_tranche"]), align_right=True))
            ret = e["retour_tranche"]
            it = item("-" if ret is None else f"{ret:.1f}", align_right=True,
                      bold=True)
            if ret is not None:
                it.setForeground(QColor(VERT if ret <= seuil * .6 else
                                        ORANGE if ret <= seuil else ROUGE))
            t.setItem(r, 6, it)
            t.setItem(r, 7, item(f"{100 * e['autonomie']:.1f} %", align_right=True))

        d = etapes[-1]
        m = g["matrices"]
        critere = self.cb_grille_critere.currentData()
        i_opt, j_opt = S.optimum_grille(g, critere, seuil)
        lib_crit = S.CRITERES_GRILLE[critere][0]
        txt = [
            f"<b>En s'arretant des qu'une tranche met plus de {seuil:.0f} ans "
            f"a se rembourser : {d['kwc']:.1f} kWc et {d['batt']:.0f} kWh</b>, "
            f"soit {f(d['capex'])} EUR et {100 * d['autonomie']:.1f} % "
            f"d'autonomie, en {len(etapes) - 1} etape(s)."]
        if chemin["arret"]:
            txt.append(f"Pourquoi s'arreter la : {chemin['arret']}")
        txt.append(
            f"<b>Critere \"{lib_crit}\"</b> : {g['kwc'][j_opt]:.1f} kWc et "
            f"{g['batt'][i_opt]:.0f} kWh &mdash; {f(m['capex'][i_opt, j_opt])} EUR, "
            f"{100 * m['autonomie'][i_opt, j_opt]:.1f} % d'autonomie, retour "
            f"{f(m['retour'][i_opt, j_opt], 1)} ans, gain cumule "
            f"{f(m['gain'][i_opt, j_opt])} EUR sur {g['horizon']} ans.")
        txt.append(
            "<i>Le chemin de croissance et l'optimum du critere ne coincident "
            "pas forcement : le premier refuse toute tranche trop lente, le "
            "second regarde le resultat final. L'ecart entre les deux, c'est "
            "ce que vous payez pour du confort plutot que pour de la "
            "rentabilite.</i>")
        self.lbl_grille.setText("<br>".join(txt))
        self.draw_grille()

    def draw_grille(self, cv=None):
        g = getattr(self, "_grille_res", None)
        c = self._cv(cv, self.cv_grille); c.clear()
        if not g:
            ax = c.fig.add_subplot(111)
            ax.text(.5, .5, "Lancez la recherche d'optimum.", ha="center",
                    va="center", fontsize=9, color="#94a3b8")
            ax.set_xticks([]); ax.set_yticks([])
            c.draw(); return

        critere = self.cb_grille_critere.currentData()
        libelle, sens, unite, dec, _aide = S.CRITERES_GRILLE[critere]
        m = g["matrices"]
        cle = {"gain": "gain", "retour": "retour", "cout_kwh": "cout_kwh",
               "autonomie": "autonomie",
               "autonomie_sous_seuil": "autonomie"}[critere]
        z = np.array(m[cle], dtype=float)
        facteur = 100.0 if cle == "autonomie" else 1.0
        z = z * facteur
        x = np.array(g["kwc"], dtype=float)
        y = np.array(g["batt"], dtype=float)

        ax = c.fig.add_subplot(111)
        im = ax.pcolormesh(x, y, z, shading="nearest",
                           cmap="viridis" if sens > 0 else "viridis_r")
        cb = c.fig.colorbar(im, ax=ax)
        cb.set_label(f"{libelle} ({unite})", fontsize=7)
        cb.ax.tick_params(labelsize=6)

        chemin = getattr(self, "_grille_chemin", None)
        if chemin:
            px = [e["kwc"] for e in chemin["etapes"]]
            py = [e["batt"] for e in chemin["etapes"]]
            ax.plot(px, py, color="white", lw=2.4, marker="o", ms=5,
                    markerfacecolor="white", markeredgecolor="#0f172a",
                    label="Chemin de croissance")
            ax.plot(px[-1:], py[-1:], marker="o", ms=11, mew=2.2,
                    markerfacecolor="none", markeredgecolor="#0f172a")

        seuil = float(self.sp_seuil_tranche.value())
        i_opt, j_opt = S.optimum_grille(g, critere, seuil)
        ax.plot([x[j_opt]], [y[i_opt]], marker="x", ms=15, mew=3.5, color="white")
        ax.plot([x[j_opt]], [y[i_opt]], marker="x", ms=12, mew=2.0, color=ROUGE,
                label=f"Optimum : {libelle.lower()}")

        ax.set_xlabel("Puissance crete installee (kWc)")
        ax.set_ylabel("Capacite batterie (kWh)")
        ax.set_title(f"{libelle} selon le couple panneaux / batterie", fontsize=9)
        ax.legend(fontsize=7, frameon=True, framealpha=.85, loc="lower right")
        c.hover2d(ax, x, y, z,
                  ("Puissance (kWc)", "Batterie (kWh)", libelle, unite, dec),
                  titre="Couple panneaux / batterie")
        c.draw()

    def _cell_retour_tranche(self, o):
        """Cellule coloree du temps de retour d'une tranche seule.

        Vert : la tranche se paie vite. Orange : elle se paie, mais tard.
        Rouge : elle ne se paiera pas. C'est le feu tricolore du "j'en
        ajoute un de plus ou j'arrete la ?".
        """
        statut = o.get("statut", "depart")
        horizon = int(self.cfg["economie"].get("duree_analyse_ans", 25))
        r = o.get("retour_tranche")
        if statut == "depart":
            txt, coul, tip = "-", None, "Premiere valeur testee : rien avant elle."
        elif statut == "sans_surcout":
            txt, coul, tip = "-", None, ("Cette option ne coute rien de plus : "
                                         "il n'y a rien a amortir.")
        elif statut == "gratuit":
            txt, coul, tip = "immediat", VERT, ("Aucun surcout et un gain "
                                                "annuel : a prendre sans reflechir.")
        elif statut == "jamais":
            txt, coul, tip = "jamais", ROUGE, (
                "Cette tranche coute et ne rapporte rien de plus, ou fait "
                "perdre. Ne l'achetez pas : arretez-vous a la ligne "
                "precedente.")
        elif statut == "trop_long":
            txt, coul, tip = f"> {horizon}", ROUGE, (
                f"Cette tranche finirait par se rembourser, mais apres les "
                f"{horizon} ans d'horizon d'analyse. Le materiel sera "
                f"probablement remplace avant.")
        else:
            txt = f"{r:.1f}"
            coul = VERT if r <= 8 else (ORANGE if r <= 15 else ROUGE)
            tip = (f"Cette tranche se rembourse toute seule en {r:.1f} ans, "
                   f"quelle que soit la rentabilite des precedentes.")
        it = item(txt, align_right=True, bold=True, tip=tip)
        if coul:
            it.setForeground(QColor(coul))
        return it

    @staticmethod
    def _optimums(out):
        """(indice de l'autonomie maximale, indice du meilleur gain cumule).

        Le second est le seul qui sache s'arreter : l'autonomie croit avec la
        taille du champ et de la batterie, donc la designer comme optimum
        revient a toujours recommander la plus grande valeur saisie.
        """
        best = max(range(len(out)), key=lambda i: out[i]["autonomie"])
        gains = [o.get("gain_cumule_solaire") for o in out]
        if any(g is not None for g in gains):
            best_eco = max(range(len(out)),
                           key=lambda i: (gains[i] if gains[i] is not None
                                          else float("-inf")))
        else:
            best_eco = None
        return best, best_eco

    def show_sweep(self, var, out):
        if not out:
            return
        t = self.tbl_sweep; t.setRowCount(len(out))
        f = lambda v, n=0: f"{v:,.{n}f}".replace(",", " ")
        best, best_eco = self._optimums(out)
        for r, o in enumerate(out):
            it0 = item(f"{o['valeur']:g}", bold=(r == best))
            if r == best_eco and best_eco != best:
                it0.setForeground(QColor(VERT))
                it0.setToolTip("Meilleur gain cumule sur l'horizon d'analyse, "
                               "perimetre solaire : au-dela, chaque euro "
                               "supplementaire rapporte moins qu'il ne coute.")
            t.setItem(r, 0, it0)
            t.setItem(r, 1, item(f"{100 * o['autonomie']:.2f} %", align_right=True,
                                 bold=(r == best)))
            for c, key in enumerate(["production", "import", "ecrete", "capex"], start=2):
                t.setItem(r, c, item(f(o[key]), align_right=True))
            t.setItem(r, 6, item(f"{o.get('cout_par_wc', 0):.2f}", align_right=True))
            t.setItem(r, 7, item(self._retour_txt(o["retour"]), align_right=True))

            # --- les quatre colonnes de la tranche ---
            dv = o.get("delta_valeur")
            t.setItem(r, 8, item("-" if dv is None else f"{dv:+g}", align_right=True))
            t.setItem(r, 9, item(self._opt(o.get("cout_tranche"), "{:,.0f}")
                                 .replace(",", " "), align_right=True))
            t.setItem(r, 10, item(self._opt(o.get("gain_tranche"), "{:,.0f}")
                                  .replace(",", " "), align_right=True))
            t.setItem(r, 11, self._cell_retour_tranche(o))

            roi = o["rentabilite_marginale"]
            it_roi = item(self._opt(roi), align_right=True)
            if roi is None and r > 0:
                it_roi.setToolTip(
                    "Cette option ne coute pas un euro de plus que la "
                    "precedente (une inclinaison ou un azimut ne change pas "
                    "la nomenclature) : le rapport gain/surcout n'a pas de "
                    "sens ici. Comparez directement l'autonomie.")
            t.setItem(r, 12, it_roi)
        self._sweep_out, self._sweep_var = out, var
        self._sweep_libelle = self.cb_sweep.currentText()
        self.draw_sweep()
        self.draw_sweep_bar()
        self.draw_sweep_tranche()
        self.tabs.setCurrentIndex(6)

    def draw_sweep(self, cv=None):
        """Balayage d'un parametre : profils mensuels et rentabilite marginale."""
        out = getattr(self, "_sweep_out", None)
        if not out:
            return
        libelle = getattr(self, "_sweep_libelle", None) or self.cb_sweep.currentText()
        c = self._cv(cv, self.cv_sweep); c.clear()
        fig = c.fig
        ax1 = fig.add_subplot(2, 2, 1)
        ax2 = fig.add_subplot(2, 2, 2)
        ax3 = fig.add_subplot(2, 2, 3)
        ax4 = fig.add_subplot(2, 2, 4)
        x = [float(o["valeur"]) for o in out]
        mois = list(MOIS)
        cmap = matplotlib.colormaps["viridis"]
        couleurs = [matplotlib.colors.to_hex(cmap(t))
                    for t in np.linspace(0, .88, max(len(out), 1))]
        s_prod, s_aut, s_imp = [], [], []
        for i, o in enumerate(out):
            col = couleurs[i]
            lab = f"{o['valeur']:g}"
            prod = np.asarray(o["production_mensuel"], dtype=float) / 1000.0
            aut = [100 * a for a in o["autonomie_mensuel"]]
            imp = np.asarray(o["import_mensuel"], dtype=float)
            ax1.plot(mois, prod, label=lab, color=col, lw=1.6, marker="o", ms=2.5)
            ax2.plot(mois, aut, label=lab, color=col, lw=1.6, marker="o", ms=2.5)
            ax3.plot(mois, imp, label=lab, color=col, lw=1.6, marker="o", ms=2.5)
            s_prod.append((lab, prod, "MWh", 3, col))
            s_aut.append((lab, aut, "%", 1, col))
            s_imp.append((lab, imp, "kWh", 0, col))
        ax1.set_ylabel("Production (MWh/mois)")
        ax1.set_title("Production mensuelle")
        ax1.grid(alpha=.25, ls=":")
        ax2.set_ylabel("Autonomie (%)")
        ax2.set_title("Taux d'autonomie mensuel")
        ax2.grid(alpha=.25, ls=":")
        ax3.set_ylabel("Import reseau (kWh/mois)")
        ax3.set_title("Energie importee du reseau")
        ax3.grid(alpha=.25, ls=":")
        # None = pas de surcout entre deux options (balayage d'inclinaison) :
        # np.nan laisse un trou dans la courbe au lieu d'un faux zero.
        roi = np.array([np.nan if o["rentabilite_marginale"] is None
                        else o["rentabilite_marginale"] for o in out], dtype=float)
        ax4.plot(x, roi, marker="o", lw=2, color="#16a34a", label="ROI marginal")
        if np.all(np.isnan(roi)):
            ax4.text(0.5, 0.5, "Ce balayage ne change pas la nomenclature :\n"
                               "aucun surcout a rentabiliser.",
                     ha="center", va="center", fontsize=7.5, color="#64748b",
                     transform=ax4.transAxes)
        ax4.axhline(0, color="black", lw=0.5, alpha=0.35)
        ax4.set_title("ROI marginal / unite supplementaire")
        ax4.set_xlabel(libelle)
        ax4.set_ylabel("Facteur")
        ax4.grid(alpha=.25, ls=":")
        for ax in (ax1, ax2, ax3):
            ax.tick_params(axis="x", rotation=30)
        if len(out) <= 8 or cv is not None:
            for ax in (ax1, ax2, ax3):
                ax.legend(loc="upper left", fontsize=6.5, frameon=False,
                          ncol=2 if len(out) > 6 else 1, title=libelle,
                          title_fontsize=6.5)
        best, best_eco = self._optimums(out)
        titre = (f"Autonomie maximale : {x[best]:g} "
                 f"-> {100 * out[best]['autonomie']:.2f} %")
        if best_eco is not None and best_eco != best:
            titre += (f"   |   meilleur gain sur l'horizon : {x[best_eco]:g} "
                      f"-> {100 * out[best_eco]['autonomie']:.2f} % d'autonomie "
                      f"pour {out[best_eco]['capex']:,.0f} EUR".replace(",", " "))
        fig.suptitle(titre, fontsize=9.5, color=BLEU)
        c.set_click_handler(lambda xdata: self.open_sweep_detail(out, xdata))
        mens = lambda i: MOIS[i]
        c.hover(ax1, np.arange(12), s_prod, xfmt=mens,
                titre="Production mensuelle")
        c.hover(ax2, np.arange(12), s_aut, xfmt=mens,
                titre="Autonomie mensuelle")
        c.hover(ax3, np.arange(12), s_imp, xfmt=mens,
                titre="Import reseau mensuel")
        c.hover(ax4, x,
                [("Autonomie", [100 * o["autonomie"] for o in out], "%", 2, "#15803d"),
                 ("Production", [o["production"] for o in out], "kWh/an", 0, "#d97706"),
                 ("Soutire au reseau", [o["import"] for o in out], "kWh/an", 0, "#b91c1c"),
                 ("Ecrete", [o["ecrete"] for o in out], "kWh/an", 0, "#7c3aed"),
                 ("Cout installation solaire", [o["capex"] for o in out], "EUR", 0, "#0f172a"),
                 ("ROI marginal", roi, "facteur", 3, "#16a34a")],
                xfmt=lambda i: f"{libelle} = {x[i]:g}", titre="Bilan annuel",
                cumul=False)
        c.draw()

    def draw_sweep_bar(self, cv=None):
        """Histogrammes comparant les options entre elles : cumuls annuels,
        moyennes sur les douze mois, autonomie et economie."""
        out = getattr(self, "_sweep_out", None)
        c = self._cv(cv, self.cv_sweep_bar); c.clear()
        if not out:
            ax = c.fig.add_subplot(111)
            ax.text(.5, .5, "Lancez un balayage pour comparer les options.",
                    ha="center", va="center", fontsize=9, color="#94a3b8")
            ax.set_xticks([]); ax.set_yticks([])
            c.draw()
            return
        libelle = getattr(self, "_sweep_libelle", None) or self.cb_sweep.currentText()
        x = np.arange(len(out))
        etiq = [f"{o['valeur']:g}" for o in out]
        entete = lambda i: f"{libelle} = {etiq[i]}"

        prod = np.array([o["production"] for o in out]) / 1000.0
        ecrete = np.array([o["ecrete"] for o in out]) / 1000.0
        utile = prod - ecrete
        imp = np.array([o["import"] for o in out])
        imp_mens = np.array([o["import_mensuel"] for o in out], dtype=float)
        imp_moy = imp_mens.mean(axis=1)
        imp_pire = imp_mens.max(axis=1)
        aut = np.array([100 * o["autonomie"] for o in out])
        aut_mens = np.array([o["autonomie_mensuel"] for o in out], dtype=float) * 100
        aut_moy = aut_mens.mean(axis=1)
        aut_min = aut_mens.min(axis=1)
        prod_mens = np.array([o["production_mensuel"] for o in out], dtype=float)
        # economie imputable a la SEULE installation solaire, pour rester
        # homogene avec "capex" et "retour" qui sont deja au perimetre solaire
        eco = np.array([o.get("economie_vs_sans_pv",
                              o.get("economie_vs_actuel", 0.0)) for o in out])
        retour = np.array([o["retour"] if o["retour"] else np.nan for o in out],
                          dtype=float)
        capex = np.array([o["capex"] for o in out])

        def barres(ax, series, titre, ylabel, legende=True):
            """Barres groupees : une couleur par grandeur, un groupe par option."""
            n = len(series)
            w = .8 / n
            for k, (lab, vals, coul) in enumerate(series):
                ax.bar(x + (k - (n - 1) / 2) * w, vals, w * .92, label=lab,
                       color=coul)
            ax.set_xticks(x)
            ax.set_xticklabels(etiq, fontsize=7,
                               rotation=45 if len(out) > 8 else 0)
            ax.set_title(titre, fontsize=9)
            ax.set_ylabel(ylabel)
            ax.grid(axis="y", alpha=.2, ls=":")
            ax.margins(y=.24)          # de la place en haut pour la legende
            if legende:
                ax.legend(fontsize=7, frameon=False, ncol=1, loc="best")

        ax1 = c.fig.add_subplot(2, 2, 1)
        barres(ax1, [("Utilisee (autoconso + batterie)", utile, "#15803d"),
                     ("Ecretee, perdue", ecrete, "#7c3aed")],
               "Production cumulee sur l'annee", "MWh/an")

        ax2 = c.fig.add_subplot(2, 2, 2)
        barres(ax2, [("Cumul sur l'annee", imp, "#b91c1c"),
                     ("Moyenne d'un mois", imp_moy, "#f97316"),
                     ("Mois le plus mauvais", imp_pire, "#0f172a")],
               "Energie achetee au reseau", "kWh")

        ax3 = c.fig.add_subplot(2, 2, 3)
        barres(ax3, [("Sur l'annee entiere", aut, "#15803d"),
                     ("Moyenne des 12 mois", aut_moy, "#0891b2"),
                     ("Mois le plus faible", aut_min, "#b91c1c")],
               "Autonomie", "%")
        cible = 100 * float(self.cfg["options"].get("autonomie_cible", .92))
        ax3.axhline(cible, color=BLEU, ls="--", lw=1, label=f"Objectif {cible:.0f} %")
        ax3.legend(fontsize=7, frameon=False, ncol=2, loc="lower right")
        ax3.set_ylim(0, 128)

        ax4 = c.fig.add_subplot(2, 2, 4)
        barres(ax4, [("Economie annuelle", eco, "#15803d")],
               "Economie et temps de retour", "EUR/an", legende=False)
        ax4b = ax4.twinx()
        ax4b.plot(x, retour, color=BLEU, marker="o", ms=4, lw=1.6,
                  label="Retour (ans)")
        ax4b.set_ylabel("Retour (ans)", color=BLEU)
        ax4b.tick_params(axis="y", labelcolor=BLEU)
        ax4.set_xlabel(libelle)
        h4 = ax4.get_legend_handles_labels()
        h4b = ax4b.get_legend_handles_labels()
        ax4.legend(h4[0] + h4b[0], h4[1] + h4b[1], fontsize=7, frameon=False,
                   loc="lower right")

        best, best_eco = self._optimums(out)
        titre = (f"Comparaison des {len(out)} options - meilleure autonomie : "
                 f"{etiq[best]} ({aut[best]:.2f} %)")
        if best_eco is not None and best_eco != best:
            titre += (f" | meilleur gain sur l'horizon : {etiq[best_eco]} "
                      f"({aut[best_eco]:.2f} %)")
        c.fig.suptitle(titre, fontsize=9.5, color=BLEU)

        series = [("Production utilisee", utile, "MWh/an", 2, "#15803d"),
                  ("Production ecretee", ecrete, "MWh/an", 2, "#7c3aed"),
                  ("Production totale", prod, "MWh/an", 2, "#d97706"),
                  ("Reseau sur l'annee", imp, "kWh", 0, "#b91c1c"),
                  ("Reseau, moyenne d'un mois", imp_moy, "kWh", 0, "#f97316"),
                  ("Reseau, mois le plus mauvais", imp_pire, "kWh", 0, "#0f172a"),
                  ("Production, moyenne d'un mois", prod_mens.mean(axis=1),
                   "kWh", 0, "#d97706"),
                  ("Autonomie sur l'annee", aut, "%", 2, "#15803d"),
                  ("Autonomie, moyenne des mois", aut_moy, "%", 2, "#0891b2"),
                  ("Autonomie du mois le plus faible", aut_min, "%", 2, "#b91c1c"),
                  ("Economie annuelle", eco, "EUR/an", 0, "#15803d"),
                  ("Cout installation solaire", capex, "EUR", 0, "#0f172a"),
                  ("Retour", retour, "ans", 1, BLEU)]
        for ax in (ax1, ax2, ax3, ax4, ax4b):
            c.hover(ax, x, series, xfmt=entete, titre="Comparaison des options",
                    cumul=False)
        c.draw()

    def _seuil_change(self, *_):
        """Le seuil ne change aucun calcul : il deplace seulement la limite
        que la vue par tranche met en evidence."""
        if getattr(self, "_sweep_out", None):
            self.draw_sweep_tranche()
        if getattr(self, "_grille_res", None):
            self.show_grille()

    def draw_sweep_tranche(self, cv=None):
        """Chaque tranche jugee seule : ce qu'elle coute, ce qu'elle rapporte,
        et en combien de temps elle se rembourse independamment des autres."""
        out = getattr(self, "_sweep_out", None)
        c = self._cv(cv, self.cv_sweep_tranche); c.clear()
        if not out or len(out) < 2:
            ax = c.fig.add_subplot(111)
            ax.text(.5, .5, "Lancez un balayage d'au moins deux valeurs.\n\n"
                            "Conseil : mettez la plus petite installation "
                            "envisageable en premiere valeur\n"
                            "(0 pour la batterie) : la premiere tranche sera "
                            "chiffree elle aussi.",
                    ha="center", va="center", fontsize=9, color="#94a3b8")
            ax.set_xticks([]); ax.set_yticks([])
            c.draw()
            return

        libelle = getattr(self, "_sweep_libelle", None) or self.cb_sweep.currentText()
        horizon = int(self.cfg["economie"].get("duree_analyse_ans", 25))
        x = np.arange(len(out))
        etiq = [f"{o['valeur']:g}" for o in out]

        def col(cle):
            return np.array([np.nan if o.get(cle) is None else float(o[cle])
                             for o in out], dtype=float)

        cout_tr = col("cout_tranche")
        gain_tr = col("gain_tranche")
        retour_tr = col("retour_tranche")
        net_tr = col("gain_net_tranche")
        retour_cum = np.array([o["retour"] if o["retour"] else np.nan
                               for o in out], dtype=float)
        statuts = [o.get("statut", "depart") for o in out]

        # les tranches qui ne se rembourseront pas sont portees au plafond du
        # graphique plutot qu'absentes : c'est l'information la plus utile
        jamais = np.array([st in ("jamais", "trop_long") for st in statuts])
        etiq_tr = ["depart" if i == 0 else
                   (f"+{o['delta_valeur']:g}" if o.get("delta_valeur") is not None
                    else "-") for i, o in enumerate(out)]

        # --- 1. cout et gain de chaque tranche -------------------------------
        ax1 = c.fig.add_subplot(2, 2, 1)
        ax1.bar(x, np.nan_to_num(cout_tr), .62, color="#0f172a",
                label="Cout de la tranche")
        ax1.set_ylabel("EUR")
        ax1.set_title("Ce que coute et rapporte chaque tranche", fontsize=9)
        ax1b = ax1.twinx()
        ax1b.plot(x, gain_tr, color=VERT, marker="o", ms=4, lw=1.8,
                  label="Gain annuel de la tranche")
        ax1b.set_ylabel("EUR/an", color=VERT)
        ax1b.tick_params(axis="y", labelcolor=VERT)
        h1, h1b = ax1.get_legend_handles_labels(), ax1b.get_legend_handles_labels()
        ax1.legend(h1[0] + h1b[0], h1[1] + h1b[1], fontsize=7, frameon=False,
                   loc="upper right")

        # --- 2. le graphique central : retour de la tranche vs retour cumule --
        ax2 = c.fig.add_subplot(2, 2, 2)
        plafond = float(np.nanmax(np.concatenate([
            retour_tr[np.isfinite(retour_tr)] if np.any(np.isfinite(retour_tr))
            else np.array([0.0]),
            retour_cum[np.isfinite(retour_cum)] if np.any(np.isfinite(retour_cum))
            else np.array([0.0]),
            np.array([horizon])])))
        plafond = max(plafond * 1.18, horizon * 1.18)
        ax2.plot(x, retour_cum, color="#94a3b8", marker="s", ms=4, lw=1.6, ls="--",
                 label="Retour de l'installation entiere")
        ax2.plot(x, retour_tr, color=BLEU, marker="o", ms=6, lw=2.4,
                 label="Retour de la TRANCHE seule")
        for i in np.where(jamais)[0]:
            ax2.plot([i], [plafond * .93], marker="x", ms=9, mew=2.4, color=ROUGE)
        if np.any(jamais):
            ax2.plot([], [], marker="x", ls="none", ms=8, mew=2.2, color=ROUGE,
                     label="ne se rembourse pas")
        ax2.axhline(horizon, color=ROUGE, ls=":", lw=1.4)
        ax2.text(len(out) - .5, horizon, f" horizon {horizon} ans", color=ROUGE,
                 fontsize=7, va="bottom", ha="right")
        ax2.set_ylim(0, plafond)
        ax2.set_ylabel("annees")
        ax2.set_title("En combien de temps la tranche se rembourse-t-elle ?",
                      fontsize=9)
        ax2.legend(fontsize=7, frameon=False, loc="upper left")

        # --- 3. gain net de la tranche a l'horizon ---------------------------
        ax3 = c.fig.add_subplot(2, 2, 3)
        couleurs = [VERT if (np.isfinite(v) and v > 0) else ROUGE for v in net_tr]
        ax3.bar(x, np.nan_to_num(net_tr), .62, color=couleurs)
        ax3.axhline(0, color="black", lw=.7)
        ax3.set_ylabel("EUR")
        ax3.set_title(f"Ce que la tranche aura rapporte, net, a {horizon} ans",
                      fontsize=9)

        for ax in (ax1, ax2, ax3):
            ax.set_xticks(x)
            ax.set_xticklabels([f"{e}\n{t}" for e, t in zip(etiq, etiq_tr)],
                               fontsize=6.5,
                               rotation=45 if len(out) > 8 else 0)
            ax.grid(axis="y", alpha=.2, ls=":")
        ax3.set_xlabel(libelle)

        # --- 4. la conclusion, en toutes lettres -----------------------------
        ax4 = c.fig.add_subplot(2, 2, 4)
        ax4.axis("off")
        for i, ligne in enumerate(self._texte_tranches(out, libelle, horizon)):
            gras = ligne.startswith("*")
            ax4.text(0.0, 0.96 - i * 0.115, ligne.lstrip("*"), va="top",
                     fontsize=8.2 if gras else 7.6,
                     fontweight="bold" if gras else "normal",
                     color=BLEU if gras else "#334155", wrap=True,
                     transform=ax4.transAxes)

        c.fig.suptitle("Amortissement tranche par tranche - "
                       "chaque tranche jugee independamment des precedentes",
                       fontsize=9.5, color=BLEU)
        series = [("Cout de la tranche", cout_tr, "EUR", 0, "#0f172a"),
                  ("Gain annuel de la tranche", gain_tr, "EUR/an", 0, VERT),
                  ("Retour de la tranche", retour_tr, "ans", 1, BLEU),
                  ("Retour de l'installation entiere", retour_cum, "ans", 1, "#94a3b8"),
                  (f"Gain net a {horizon} ans", net_tr, "EUR", 0, "#7c3aed"),
                  ("Autonomie", np.array([100 * o["autonomie"] for o in out]),
                   "%", 2, "#15803d")]
        for ax in (ax1, ax1b, ax2, ax3):
            c.hover(ax, x, series,
                    xfmt=lambda i: f"{libelle} = {etiq[i]} ({etiq_tr[i]})",
                    titre="Tranche", cumul=False)
        c.draw()

    def _texte_tranches(self, out, libelle, horizon):
        """La conclusion du balayage, en francais : jusqu'ou pousser."""
        f = lambda v: f"{v:,.0f}".replace(",", " ")
        seuil = float(self.sp_seuil_tranche.value()) \
            if hasattr(self, "sp_seuil_tranche") else 10.0
        i_seuil = S.derniere_tranche_rentable(out, seuil_ans=seuil)
        i_horizon = S.derniere_tranche_rentable(out, seuil_ans=horizon)
        lignes = []

        if i_seuil is not None and out[i_seuil].get("statut") != "depart":
            lignes.append(f"*Tant que chaque tranche doit se payer en moins de "
                          f"{seuil:.0f} ans :")
            lignes.append(f"   aller jusqu'a {libelle} = {out[i_seuil]['valeur']:g}, "
                          f"soit {f(out[i_seuil]['capex'])} EUR "
                          f"et {100 * out[i_seuil]['autonomie']:.1f} % d'autonomie.")
        elif i_seuil is not None:
            lignes.append(f"*Meme la premiere tranche ne se paie pas en "
                          f"{seuil:.0f} ans.")
            lignes.append("   Le dimensionnement de depart est deja au-dela du "
                          "point de rentabilite, ou le prix du kWh saisi est "
                          "trop bas.")

        if i_horizon is not None and i_horizon != i_seuil:
            lignes.append(f"*Limite absolue, tranches remboursees avant "
                          f"{horizon} ans :")
            lignes.append(f"   {libelle} = {out[i_horizon]['valeur']:g}, "
                          f"{f(out[i_horizon]['capex'])} EUR, "
                          f"{100 * out[i_horizon]['autonomie']:.1f} % d'autonomie.")

        perdues = [o for o in out if o.get("statut") in ("jamais", "trop_long")]
        if perdues:
            cout = sum(o["cout_tranche"] or 0.0 for o in perdues)
            gain = sum(o["gain_tranche"] or 0.0 for o in perdues)
            lignes.append(f"*Les {len(perdues)} dernieres tranches coutent "
                          f"{f(cout)} EUR")
            lignes.append(f"   et ne rapportent que {f(gain)} EUR/an a elles "
                          f"toutes. Elles achetent du confort, pas de la "
                          f"rentabilite.")
        else:
            lignes.append("*Toutes les tranches testees se remboursent.")
            lignes.append(f"   Poussez le balayage plus loin pour trouver ou "
                          f"cela s'arrete.")
        lignes.append("")
        lignes.append("Le retour cumule (courbe grise) est une moyenne : il")
        lignes.append("reste flatteur longtemps apres que la tranche suivante")
        lignes.append("a cesse d'etre rentable. C'est la courbe bleue qui")
        lignes.append("dit quand s'arreter.")
        return lignes

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
                if isinstance(v, dict):
                    continue                      # ventile juste en dessous
                w.writerow([k, round(v, 2) if isinstance(v, float) else v])

            # --- nomenclature, ligne a ligne puis ventilee par categorie ---
            lignes, recap = S.compute_bom(self.cfg)
            w.writerow([])
            w.writerow(["Nomenclature", "Categorie", "Perimetre", "Quantite auto",
                        "Quantite retenue", "Unite", "Prix unitaire EUR",
                        "Montant EUR"])
            for l in lignes:
                w.writerow([l["poste"], C.BOM_CATEGORIES[l["categorie"]]["label"],
                            "solaire" if l["solaire"] else "hors solaire",
                            C.AUTO_QTY.get(l.get("auto", "fixe"), ""),
                            round(l["qte_calc"], 2), l.get("unite", ""),
                            round(float(l.get("pu", 0)), 2), round(l["montant"], 2)])
            w.writerow([])
            w.writerow(["Sous-total", "Montant EUR", "Perimetre"])
            for k in C.ORDRE_CATEGORIES:
                v = recap["par_categorie"].get(k, 0.0)
                if v:
                    w.writerow([C.BOM_CATEGORIES[k]["label"], round(v, 2),
                                "solaire" if C.est_solaire(k) else "hors solaire"])
            w.writerow(["PERIMETRE SOLAIRE", round(recap["solaire"], 2), "solaire"])
            w.writerow(["Hors perimetre solaire", round(recap["hors_solaire"], 2),
                        "hors solaire"])
            w.writerow(["PROJET COMPLET", round(recap["total"], 2), ""])
            w.writerow([])
            out = getattr(self, "_sweep_out", None)
            if out:
                w.writerow(["Balayage", getattr(self, "_sweep_libelle", "")])
                w.writerow(["Valeur", "Autonomie %", "Production kWh",
                            "Import kWh", "Ecrete kWh", "Cout solaire EUR",
                            "EUR/Wc", "Retour cumule ans", "Tranche",
                            "Cout tranche EUR", "Gain tranche EUR/an",
                            "Retour tranche ans", "Statut tranche"])
                for o in out:
                    n = lambda v, d=2: "" if v is None else round(float(v), d)
                    w.writerow([o["valeur"], round(100 * o["autonomie"], 2),
                                round(o["production"]), round(o["import"]),
                                round(o["ecrete"]), round(o["capex"]),
                                round(o.get("cout_par_wc", 0), 3),
                                n(o["retour"], 2), n(o.get("delta_valeur"), 3),
                                n(o.get("cout_tranche"), 0),
                                n(o.get("gain_tranche"), 0),
                                n(o.get("retour_tranche"), 2),
                                o.get("statut", "")])
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

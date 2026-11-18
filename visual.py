"""plot() -- LaTeX-styled matplotlib wrapper.

Excerpt from **arpy**, my own Python toolbox:
https://github.com/Startemplib/Arhat

Vendored here so this repo runs on its own. Trimmed to what the AWG script
needs: the notebook display path and the OpenCV image viewer (``vp``) are not
included, and LaTeX typesetting is opt-in (``tex=1``) instead of always on.
The full version -- image viewer, symbolic matrices, QR, PDF tooling -- lives
in the repo above.
"""

from __future__ import annotations

import io
import os
import re
import shutil

import matplotlib as mpl
import matplotlib.pyplot as plt

mpl.rcParams["font.family"] = ["Times New Roman", "SimSun"]


def has_latex() -> bool:
    """True when a TeX engine matplotlib can drive is on PATH."""
    return any(shutil.which(exe) for exe in ("xelatex", "lualatex", "pdflatex"))


def safe_name(text: str) -> str:
    """LaTeX title -> a file name that Windows will accept."""
    text = re.sub(r"\\[a-zA-Z]+|[{}$]", "", text)
    text = re.sub(r"[\\^&%$#@!*:<>?|\"/]", "", text)
    return text.strip().replace(" ", "_") or "figure"


def plot(
    x_data,
    y_data,
    xlabel,
    ylabel,
    title,
    s=(7, 5),
    q=300,
    ts=16,
    tp=10,
    xs=11,
    ys=11,
    multi=0,
    plot_args=None,
    log_x=0,
    log_y=0,
    scatter=0,
    show=0,
    path=None,
    sd=0,
    tex=0,
):
    """Plot one or several curves.

    multi      several (x, y) pairs, each with its own entry in plot_args
    scatter    points instead of lines
    show       open a window
    path / sd  save to that path, or to the desktop under the title
    tex        typeset the labels with a real TeX engine (slow, and it needs
               one installed; off by default so this file runs anywhere)
    """
    original_backend = mpl.get_backend()
    latex = bool(tex) and has_latex()
    if tex and not latex:
        print("no TeX engine on PATH, falling back to mathtext")
    if latex:
        mpl.use("pgf")
        mpl.rcParams.update({
            "pgf.rcfonts": False,
            "text.usetex": True,
            "font.family": "serif",
            "font.serif": ["Times New Roman"],
            "pgf.preamble": r"\usepackage{amsmath}",
        })

    try:
        plt.figure(figsize=s)
        draw = plt.scatter if scatter else plt.plot
        if not multi:
            draw(x_data, y_data, **(plot_args or {}))
        else:
            if not isinstance(x_data, list) or not isinstance(y_data, list):
                raise ValueError("in multi mode x_data and y_data must be lists")
            for x, y, args in zip(x_data, y_data, plot_args or [{}] * len(x_data)):
                draw(x, y, **args)

        if log_x:
            plt.xscale("log")
        if log_y:
            plt.yscale("log")

        plt.xlabel(xlabel, fontsize=xs)
        plt.ylabel(ylabel, fontsize=ys)
        plt.title(title, fontsize=ts, pad=tp)
        plt.grid(True)
        if "label" in str(plot_args).lower():
            plt.legend()
        if not log_x:
            plt.gca().get_xaxis().get_major_formatter().set_useOffset(False)
        if not log_y:
            plt.gca().get_yaxis().get_major_formatter().set_useOffset(False)
        plt.tight_layout()

        buffer = io.BytesIO()
        plt.savefig(buffer, format="png", dpi=q)
        if show and not latex:
            plt.show()
        plt.close("all")

        if sd and not path:
            path = os.path.join(os.path.expanduser("~"), "Desktop",
                                f"{safe_name(title)}.png")
        if path:
            with open(path, "wb") as f:
                f.write(buffer.getvalue())
            print(f"figure saved: {path}")
        buffer.close()
    finally:
        if latex:
            mpl.use(original_backend)

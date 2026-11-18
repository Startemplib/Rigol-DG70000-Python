#!/usr/bin/env python3
"""Pulse-pattern generator for the Rigol DG70000 series AWG over VISA.

Builds a 12-lane pattern (4 analog channels, each with 2 markers), uploads it
as a binary waveform, assigns it and turns the outputs on.

Everything in here was written against a DG70000 at 5 GSa/s, in 2024-2025.
Notes that cost real lab time are marked GOTCHA.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from struct import pack

import numpy as np

LANES_PER_CHANNEL = 3  # analog, marker1, marker2
CHANNELS = 4
LANES = CHANNELS * LANES_PER_CHANNEL
DAC_FULL_SCALE = 32767  # signed 16-bit, the analog lane is int16
MARKER_BIT = 1 << 7  # GOTCHA: markers go out in bit 7, not bit 0

log = logging.getLogger("DG70000")


# --------------------------------------------------------------------------- #
# connection
# --------------------------------------------------------------------------- #


def open_instrument(address: str | None = None, timeout_ms: int = 5000):
    """Open the AWG. With no address, pick the single USB resource found.

    GOTCHA: the DG70000 needs the NI-VISA backend. pyvisa's own ``@py``
    backend will enumerate the device and then fail on the raw binary
    writes. Install NI-VISA (or Keysight IO Libraries) first.
    """
    import pyvisa  # imported late so --dry-run works without VISA installed

    rm = pyvisa.ResourceManager()
    if address is None:
        usb = [r for r in rm.list_resources() if "USB" in r]
        if not usb:
            raise RuntimeError("no USB VISA resource found; pass --addr")
        if len(usb) > 1:
            raise RuntimeError("several USB resources, pass --addr: " + ", ".join(usb))
        address = usb[0]
        log.info("resource auto-selected: %s", address)

    inst = rm.open_resource(address, write_termination="\n", read_termination="\n")
    inst.timeout = timeout_ms
    log.info("connected to %s", inst.query("*idn?").strip())
    return inst


# --------------------------------------------------------------------------- #
# SiFi
# --------------------------------------------------------------------------- #

SIFI_MODES = {"N": "NORMal", "S": "STEP", "I": "INTer", "E": "EDGE"}


def parse_sifi(formula: str) -> dict[int, str]:
    """``1N,2S,3I,4E`` or ``AS`` -> ``{channel: mode}``. Validates, writes nothing."""
    plan: dict[int, str] = {}
    for item in formula.split(","):
        item = item.strip()
        if not item:
            continue
        target, short = item[0].upper(), item[1:].upper()
        mode = SIFI_MODES.get(short)
        if mode is None:
            raise ValueError(f"unknown SiFi mode in {item!r}, use one of NSIE")
        if target == "A":
            plan.update({ch: mode for ch in range(1, CHANNELS + 1)})
            continue
        if not target.isdigit() or int(target) not in range(1, CHANNELS + 1):
            raise ValueError(f"channel out of range in {item!r}")
        plan[int(target)] = mode
    return plan


def set_sifi(inst, formula: str) -> None:
    """Set the SiFi edge-shaping mode. See ``parse_sifi`` for the syntax.

    Measured on a scope: EDGE will not go below ~160 ps rise time. The modes
    diverge at short widths and had stopped differing by 10 ns on my setup --
    which one you want is a thing to look at on your own screen.
    """
    plan = parse_sifi(formula)  # parse first: a typo must not half-apply
    for ch in range(1, CHANNELS + 1):
        inst.write(f":SOURce{ch}:SIFI:MODE SIFI")
    for ch, mode in sorted(plan.items()):
        inst.write(f":SOURce{ch}:SIFI:TYPE {mode}")
        log.info("ch%d SiFi -> %s", ch, inst.query(f":SOURce{ch}:SIFI:TYPE?").strip())


# --------------------------------------------------------------------------- #
# waveform
# --------------------------------------------------------------------------- #


def period_samples(sample_rate: float, rep_rate_hz: float) -> int:
    """Samples in one repetition period, truncated to the sample grid."""
    return int(sample_rate / rep_rate_hz)


def build_pattern(
    widths_ns,
    delays_ns,
    rep_rate_hz: float,
    total_length_ns: float,
    sample_rate: float,
    amplitude: float = 0.8,
):
    """Return 12 integer lanes: one pulse per period, repeated to fill the table.

    ``widths_ns`` / ``delays_ns`` are 12 long, ordered
    ``ch1, ch1_mk1, ch1_mk2, ch2, ...``. Widths snap to the sample grid
    (0.2 ns at 5 GSa/s), delays to twice that -- anything finer is below what
    the box resolves, and it carries ~±2 ns of internal skew regardless.

    GOTCHA: an analog lane is signed 16-bit, a marker lane is 0/1 only --
    a marker will not take -1, and it will not take a fraction.
    """
    if len(widths_ns) != LANES or len(delays_ns) != LANES:
        raise ValueError(f"widths and delays must both be {LANES} long")

    period = period_samples(sample_rate, rep_rate_hz)
    total_samples = int(round(total_length_ns * 1e-9 * sample_rate))
    repeats = total_samples // period
    if repeats < 1:
        raise ValueError(
            f"total length ({total_samples} samples) is shorter than one period "
            f"({period} samples)"
        )
    if repeats * period != total_samples:
        # the table holds whole periods only, so it ends up a little short
        log.info("table trimmed to %d samples (%d x %d)",
                 repeats * period, repeats, period)

    high = int(DAC_FULL_SCALE * amplitude)
    grid_ns = 1e9 / sample_rate
    lanes = []
    for i in range(LANES):
        delay = 2 * int(round(delays_ns[i] / (2 * grid_ns)))
        if abs(delay * grid_ns - delays_ns[i]) > 1e-9:
            log.warning("lane %d: delay %g ns snapped to %g ns (%g ns steps)",
                        i, delays_ns[i], delay * grid_ns, 2 * grid_ns)
        width = int(round(widths_ns[i] * 1e-9 * sample_rate))
        if delay + width > period:
            raise ValueError(f"lane {i}: delay + width does not fit in one period")
        level = high if i % LANES_PER_CHANNEL == 0 else 1
        one = [0] * delay + [level] * width + [0] * (period - delay - width)
        lanes.append(one * repeats)
    return lanes


def preview(lanes, widths_ns, delays_ns, sample_rate: float,
            span_ns: float | None = None, save: str | None = None) -> None:
    """Plot the start of each analog channel.

    ``span_ns`` defaults to four times the longest pulse, so a 2 ns pulse at
    120 kHz does not come out as one spike in an 8 us window.

    Uses ``visual.plot`` from this folder -- an excerpt of my own toolbox,
    https://github.com/Startemplib/Arhat -- and falls back to bare matplotlib
    if that file is gone.
    """
    if span_ns is None:
        busiest = max(widths_ns[i] + delays_ns[i] for i in range(0, LANES, 3))
        span_ns = max(busiest * 4, 10 / sample_rate * 1e9)
    span = max(2, min(len(lanes[0]), int(round(span_ns * 1e-9 * sample_rate))))
    t = [i / sample_rate * 1e9 for i in range(span)]
    series = [lanes[ch * LANES_PER_CHANNEL][:span] for ch in range(CHANNELS)]
    colors = ["blue", "red", "green", "orange"]
    labels = [f"ch{ch + 1} (w {widths_ns[ch * 3]:g} ns, d {delays_ns[ch * 3]:g} ns)"
              for ch in range(CHANNELS)]
    args = [{"color": colors[i], "linewidth": 1, "label": labels[i]}
            for i in range(CHANNELS)]

    try:
        from visual import plot
    except ImportError:
        import matplotlib.pyplot as plt

        for y, a in zip(series, args):
            plt.plot(t, y, **a)
        plt.xlabel("ns")
        plt.ylabel("DAC code")
        plt.legend()
        plt.tight_layout()
        plt.show()
        return

    plot(
        x_data=[t] * CHANNELS,
        y_data=series,
        xlabel="ns",
        ylabel="DAC code",
        title=f"DG70000 pattern, {sample_rate / 1e9:g} GSa/s",
        multi=True,
        plot_args=args,
        show=True,
        path=save,
    )


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #


def _write_block(inst, header: str, payload: bytes) -> None:
    """IEEE 488.2 definite-length block: ``#9`` + 9 digits of byte count."""
    inst.write_raw(header.encode("utf-8") + payload + b"\n")


def upload(inst, lanes, wave_name: str = "wf", new_wave: bool = True,
           settle_s: float = 1.0) -> None:
    """Push the 12 lanes to the AWG, assign them and start the outputs.

    GOTCHA: ``:WLISt:WAVeform:NEW`` fails if the name already exists, and
    ``DATA`` fails if it does not. Re-run with ``--reuse-wave`` once the
    waveform is on the box, and keep the record length identical -- changing
    it means rebuilding the waveform.
    """
    record_length = len(lanes[0])

    for ch in range(CHANNELS):
        name = f"{wave_name}_{ch + 1}"
        inst.write(f":AWGControl:CHANnel{ch + 1}:STOP")
        inst.write(f":OUTPut{ch + 1}:STATe OFF")
        time.sleep(0.2)

        analog = np.asarray(lanes[ch * 3], dtype=np.int16)
        mk1 = MARKER_BIT * np.asarray(lanes[ch * 3 + 1], dtype=np.uint8)
        mk2 = MARKER_BIT * np.asarray(lanes[ch * 3 + 2], dtype=np.uint8)

        if new_wave:
            inst.write(f":WLISt:WAVeform:NEW {name},{record_length},REAL")
            time.sleep(2)  # the box needs this; without it the first DATA is lost

        _write_block(
            inst,
            f":WLISt:WAVeform:DATA {name},1,{record_length},#9{2 * record_length:09d}",
            pack(f"{analog.size}h", *analog),
        )
        time.sleep(settle_s)
        for n, marker in ((1, mk1), (2, mk2)):
            _write_block(
                inst,
                f":WLISt:WAVeform:MARKer{n}:DATA {name},1,{record_length},"
                f"#9{record_length:09d}",
                pack(f"{marker.size}B", *marker),
            )
            time.sleep(settle_s)

        log.info("ch%d written: %s", ch + 1, inst.query("WLISt:LIST?").strip())
        inst.write(f":SOURce{ch + 1}:CASSet:WAVeform {name}")
        time.sleep(settle_s)
        inst.write(f":AWGControl:CHANnel{ch + 1}:RUN")
        time.sleep(settle_s)
        inst.write(f":OUTPut{ch + 1}:STATe ON")

    inst.query("*OPC?")
    error = inst.query("SYST:ERR?").strip()
    if not error.startswith("0,"):
        log.warning("instrument reports: %s", error)
    else:
        log.info("output running")


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def _lane_list(text: str, what: str):
    """``1`` -> all lanes; ``1,1,2,...`` -> 12 values."""
    parts = [float(p) for p in text.split(",")]
    if len(parts) == 1:
        return parts * LANES
    if len(parts) != LANES:
        raise argparse.ArgumentTypeError(f"--{what} takes 1 or {LANES} values")
    return parts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--addr", help="VISA resource; default is the only USB one")
    p.add_argument("--sample-rate", type=float, default=5e9, help="Sa/s (default 5e9)")
    p.add_argument("--rep-rate", type=float, default=120e3, help="Hz (default 120e3)")
    p.add_argument("--total-length", type=float, default=100000,
                   help="waveform table length in ns (default 100000)")
    p.add_argument("--width", default="2",
                   help="pulse width in ns: one value, or 12 comma separated "
                        "(ch1,ch1mk1,ch1mk2,ch2,...)")
    p.add_argument("--delay", default="0", help="delay in ns, same form as --width")
    p.add_argument("--amp", type=float, default=0.8,
                   help="analog level as a fraction of full scale (default 0.8)")
    p.add_argument("--sifi", default="AS", help="SiFi modes, e.g. AS or 1N,2S,3I,4E")
    p.add_argument("--wave-name", default="wf", help="waveform name prefix")
    p.add_argument("--reuse-wave", action="store_true",
                   help="waveform already exists on the box, overwrite its data")
    p.add_argument("--preview", action="store_true", help="plot before uploading")
    p.add_argument("--save-preview", metavar="PNG", help="write the preview to a file")
    p.add_argument("--preview-span", type=float, metavar="NS",
                   help="preview window in ns (default: 4x the longest pulse)")
    p.add_argument("--dry-run", action="store_true",
                   help="build the pattern and report, touch no hardware")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if a.verbose or a.dry_run else logging.WARNING,
                        format="%(levelname)s %(message)s")

    widths = _lane_list(a.width, "width")
    delays = _lane_list(a.delay, "delay")
    try:
        lanes = build_pattern(widths, delays, a.rep_rate, a.total_length,
                              a.sample_rate, a.amp)
    except ValueError as e:
        p.error(str(e))

    period = period_samples(a.sample_rate, a.rep_rate)
    print(f"{LANES} lanes x {len(lanes[0])} samples = "
          f"{len(lanes[0]) / a.sample_rate * 1e9:g} ns, "
          f"grid {1e9 / a.sample_rate:g} ns, "
          f"{len(lanes[0]) // period} periods of {period} samples")

    if a.preview or a.save_preview:
        preview(lanes, widths, delays, a.sample_rate,
                span_ns=a.preview_span, save=a.save_preview)
    if a.dry_run:
        return 0

    inst = open_instrument(a.addr)
    if not a.reuse_wave:
        inst.write(":WLISt:WAVeform:DELete:ALL")  # names must be free for NEW
    set_sifi(inst, a.sifi)
    upload(inst, lanes, a.wave_name, new_wave=not a.reuse_wave)
    return 0


if __name__ == "__main__":
    sys.exit(main())

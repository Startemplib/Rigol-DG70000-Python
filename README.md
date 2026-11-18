# Rigol-DG70000-Python

Pulse patterns on a **Rigol DG70000** AWG from Python. One file, a CLI, no
vendor GUI.

Four analog channels, two markers each -- twelve lanes -- built on the sample
grid, uploaded as binary waveforms, assigned, running.

```bash
pip install -r requirements.txt            # plus NI-VISA, see below
python DG70000.py --dry-run                # build the pattern, touch nothing
python DG70000.py --dry-run --preview      # and look at it
python DG70000.py --width 1,1,2,1,4,8,1,4,2,1,2,2 --rep-rate 500e3 --sifi AS
```

`--width` and `--delay` take one value for every lane, or twelve, in the order
`ch1, ch1_mk1, ch1_mk2, ch2, ...`. Everything is nanoseconds. A delay that is
not a multiple of two sample periods is snapped down, and it says so.

## What it does

|                               |                                                                   |
| ----------------------------- | ----------------------------------------------------------------- |
| `--sifi`                      | SiFi edge shaping, `AS` for all-STEP or `1N,2S,3I,4E` per channel |
| `--rep-rate` `--total-length` | repetition rate (Hz) and table length (ns)                        |
| `--amp`                       | analog level as a fraction of full scale, default 0.8             |
| `--preview` `--save-preview`  | plot the pattern before it reaches the box                        |
| `--dry-run`                   | build and report, no VISA, no hardware                            |
| `--reuse-wave`                | the waveform already exists on the box, overwrite its data        |

## Things that cost me time

- **NI-VISA, not pyvisa-py.** The `@py` backend enumerates the instrument and
  then dies on the raw binary writes. Install NI-VISA (the whole thing).
- **Markers live in bit 7.** `1 << 7`, not `1`. A marker lane takes 0 or 1 --
  no fractions, and not -1 either. The analog lane is signed 16-bit.
- **`:WLISt:WAVeform:NEW` then wait 2 s.** Without the wait the first `DATA`
  block is silently lost. `NEW` fails if the name exists, `DATA` fails if it
  does not -- that is what `--reuse-wave` is for.
- **The table holds whole periods only.** 5 GSa/s over 120 kHz is 41666.67
  samples; the script truncates, so the table ends up slightly short of what
  you asked for and says so.
- **EDGE will not go below ~160 ps.** Measured on a scope, not read off a
  datasheet. Which SiFi mode you want for a given width is a thing to look at
  on your own screen -- they do diverge at short widths, and by 10 ns they
  stopped differing on my setup.
- **Pulse widths land on the sample grid, delays on twice that.** At 5 GSa/s
  that is 0.2 ns and 0.4 ns. The AWG has its own ±2 ns of internal skew
  anyway, which is why the delay here is applied by moving the pulse inside
  the period rather than by asking the box for a delay.
- `delay + width` must fit inside one period, per lane.

## Files

- `DG70000.py` -- all of it.
- `visual.py` -- `plot()`, lifted out of my own toolbox
  **[Arhat](https://github.com/Startemplib/Arhat)** so this repo stands alone.
  More of these Python conveniences -- image viewer, symbolic matrices, PDF
  and QR tooling -- live there.

## Origin

The first version drew inspiration from a ~100-line example provided by a Rigol engineer. Credit where it is due.

## A Few Words About This Project

This work was done a few years ago, around 2024–2025, in a lab I'm no longer part of.

Back then, the DG70000 was still far from a finished product. It has probably improved quite a lot by now, but at the time I spent most of my days dealing with problems: "VAM exceed" errors, noise appearing out of nowhere, and sometimes perfectly ordinary SCPI commands that simply refused to work.

Like other manufacturers, Rigol also seemed to have made some modifications to the protocol, which is something I find hard to comment on. I don't even know whether NI-VISA is still the most popular option today. As a user, I just want a consistent, universal interface that works...

Anyway, you have to understand that a device this complicated integrates all sorts of chips and subsystems, each bringing along its own little set of commands. Eventually, you end up with thousands of commands and a manual hundreds of pages long — and some of those commands in the manual simply don't work...

The box itself was running Android (which is based on Linux anyway). I remember a Rigol manager joking that it might be running HarmonyOS sooner or later.But since we didn't have that level of access, not that it really mattered to us users, though...

Anyway, being able to control the whole thing from Python is still pretty cool. At the time, I couldn't find much publicly available code that did this. Not exactly a popular thing to work on, huh? So here's the surprise.

If I were doing this today, with all these AI tools around, I'd probably wrap everything in a fancy GUI and a proper shell-style CLI, and make the whole thing look much prettier. But I no longer have access to the device, and I'm no longer in that lab, so this project can only serve as a reference. I can't promise it will work out of the box on a newer DG70000 or with newer firmware.

Whatever. I spent months on this shit, working with what was essentially still a test version, talking to four or five Rigol engineers, and sometimes staying up at night testing things with them.

I'd rather see it help somebody than perish in my folder.
"""Inspect an XDF recording, pick its audio stream, export a WAV and a waveform PNG.

Usage:
    python main.py ./recordings/dlb            # every *.xdf in the folder
    python main.py ./recordings/dlb/dlb.xdf    # a single file
    python main.py ./recordings/dlb --stream 2 # skip the prompt, use stream #2

Outputs are written next to the XDF: <name>.wav and <name>.png.
"""

import argparse
import sys
import wave
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyxdf


def info_field(stream, key, default=""):
    value = stream["info"].get(key, [default])
    return value[0] if isinstance(value, list) and value else value


def sample_rate(stream):
    nominal = float(info_field(stream, "nominal_srate", 0) or 0)
    if nominal > 0:
        return nominal
    return float(info_field(stream, "effective_srate", 0) or 0)


def print_streams(streams):
    print(f"{'#':>2}  {'name':<24} {'type':<12} {'ch':>3} {'srate':>9} {'format':<8} {'samples':>9} {'duration':>9}")
    for i, s in enumerate(streams):
        ts = s["time_stamps"]
        duration = ts[-1] - ts[0] if len(ts) > 1 else 0.0
        print(
            f"{i:>2}  {info_field(s, 'name'):<24} {info_field(s, 'type'):<12} "
            f"{info_field(s, 'channel_count'):>3} {sample_rate(s):>9.1f} "
            f"{info_field(s, 'channel_format'):<8} {len(ts):>9} {duration:>8.2f}s"
        )


def select_audio_stream(streams, forced_index=None):
    if forced_index is not None:
        if not 0 <= forced_index < len(streams):
            sys.exit(f"--stream {forced_index} is out of range (0..{len(streams) - 1})")
        return streams[forced_index]

    if len(streams) == 1:
        return streams[0]

    candidates = [i for i, s in enumerate(streams) if info_field(s, "type").lower() == "audio"]
    if len(candidates) == 1:
        return streams[candidates[0]]

    choices = candidates or list(range(len(streams)))
    hint = "audio streams" if candidates else "no stream has type 'Audio'; all streams"
    while True:
        answer = input(f"Select stream ({hint}: {', '.join(map(str, choices))}): ").strip()
        if answer.isdigit() and int(answer) in choices:
            return streams[int(answer)]
        print("  invalid choice")


def to_pcm(data):
    """Return (frames, sample_width_bytes) suitable for the wave module."""
    if data.dtype == np.int16:
        return data, 2
    if data.dtype == np.int32:
        return data, 4
    if np.issubdtype(data.dtype, np.floating):
        peak = np.max(np.abs(data)) if data.size else 0
        scale = 1.0 if peak <= 1.0 else 1.0 / peak  # assume [-1, 1]; normalise otherwise
        return (np.clip(data * scale, -1.0, 1.0) * 32767).astype(np.int16), 2
    return data.astype(np.int16), 2


def write_wav(path, data, srate):
    frames, width = to_pcm(data)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(frames.shape[1])
        w.setsampwidth(width)
        w.setframerate(int(round(srate)))
        w.writeframes(np.ascontiguousarray(frames).astype(f"<i{width}").tobytes())


def plot_waveform(path, data, srate, title):
    n_ch = data.shape[1]
    t = np.arange(data.shape[0]) / srate
    fig, axes = plt.subplots(n_ch, 1, figsize=(14, 3 * n_ch), sharex=True, squeeze=False)
    for ch, ax in enumerate(axes[:, 0]):
        ax.plot(t, data[:, ch], linewidth=0.3, color="#1f5fa8")
        ax.set_ylabel(f"ch {ch + 1}")
        ax.set_xlim(t[0], t[-1] if len(t) > 1 else 1)
        ax.grid(alpha=0.3)
    axes[-1, 0].set_xlabel("time (s)")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def process(xdf_path, forced_index=None):
    print(f"\n=== {xdf_path}")
    streams, _ = pyxdf.load_xdf(str(xdf_path))
    if not streams:
        print("  no streams found")
        return
    print_streams(streams)

    stream = select_audio_stream(streams, forced_index)
    name = info_field(stream, "name")
    srate = sample_rate(stream)
    data = np.asarray(stream["time_series"])
    if data.ndim == 1:
        data = data[:, None]
    if srate <= 0 or data.size == 0 or not np.issubdtype(data.dtype, np.number):
        sys.exit(f"stream '{name}' has no numeric samples or no sample rate")

    print(f"Selected: '{name}' ({data.shape[1]} ch, {srate:g} Hz, {data.shape[0] / srate:.2f} s, {data.dtype})")

    wav_path = xdf_path.with_suffix(".wav")
    png_path = xdf_path.with_suffix(".png")
    write_wav(wav_path, data, srate)
    plot_waveform(png_path, data, srate, f"{xdf_path.name} - {name} ({srate:g} Hz)")
    print(f"Wrote {wav_path}\nWrote {png_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="folder with .xdf files, or a single .xdf file")
    parser.add_argument("--stream", type=int, help="stream index to export (skips auto-selection)")
    args = parser.parse_args()

    if args.path.is_dir():
        files = sorted(args.path.glob("*.xdf"))
    elif args.path.suffix.lower() == ".xdf" and args.path.exists():
        files = [args.path]
    else:
        files = []
    if not files:
        sys.exit(f"no .xdf files found at {args.path}")

    for f in files:
        process(f, args.stream)


if __name__ == "__main__":
    main()

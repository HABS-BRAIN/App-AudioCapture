"""Inspect an XDF recording, pick its audio stream, export a WAV, a waveform PNG and a marker label file.

Usage:
    python main.py ./recordings/dlb                  # every *.xdf in the folder
    python main.py ./recordings/dlb/dlb.xdf          # a single file
    python main.py ./recordings/dlb --stream 2       # skip the prompt, use stream #2
    python main.py rec.xdf --out /tmp/check          # write outputs somewhere else
    python main.py rec.xdf --raw                     # old behaviour: samples only, no gap filling

Timing works as follows:
- Time comes from the LSL timestamps, not from counting samples. If AudioCapture lost samples, the WAV
  gets silence of the lost length at that point, so WAV time equals LSL time minus the audio start (t0).
- Marker streams (type "Markers") are written to <name>_markers.txt (an Audacity label track, on the
  same time axis as the WAV) and drawn on the PNG.

Outputs (next to the XDF unless --out is given): <name>.wav, <name>.png, <name>_markers.txt.
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

# A dropout is a lasting jump of more than this between the timestamps and the sample count.
DROPOUT_THRESHOLD_S = 0.003
# AudioCapture timestamps jitter by about ±10 ms per buffer. Compare 0.5 s on each side of a
# candidate point, at 10 ms resolution.
BLOCK_S = 0.010
SIDE_S = 0.5
# Up to this much of the start is used to fix the time origin of the audio (t0).
ORIGIN_WINDOW_S = 10.0
PLOT_BINS = 4000


def info_field(stream, key, default=""):
    value = stream["info"].get(key, [default])
    return value[0] if isinstance(value, list) and value else value


def sample_rate(stream):
    nominal = float(info_field(stream, "nominal_srate", 0) or 0)
    if nominal > 0:
        return nominal
    return float(info_field(stream, "effective_srate", 0) or 0)


def print_streams(streams):
    print(f"{'#':>2}  {'name':<34} {'type':<10} {'ch':>3} {'srate':>9} {'format':<8} "
          f"{'samples':>9} {'duration':>9} {'unaccounted':>12}")
    for i, s in enumerate(streams):
        ts = s["time_stamps"]
        duration = ts[-1] - ts[0] if len(ts) > 1 else 0.0
        srate = sample_rate(s)
        nominal = float(info_field(s, "nominal_srate", 0) or 0)
        # Timestamp span minus the time the samples cover. Positive: samples are missing.
        # Negative: there are more samples than time passed (duplicates, or a fast device clock).
        unaccounted = (f"{(duration - (len(ts) - 1) / srate) * 1000:+10.1f}ms"
                       if nominal > 0 and len(ts) > 1 else "")
        print(
            f"{i:>2}  {info_field(s, 'name'):<34} {info_field(s, 'type'):<10} "
            f"{info_field(s, 'channel_count'):>3} {srate:>9.1f} "
            f"{info_field(s, 'channel_format'):<8} {len(ts):>9} {duration:>8.2f}s {unaccounted:>12}"
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


def select_marker_streams(streams, audio, forced_indices=None):
    if forced_indices:
        for i in forced_indices:
            if not 0 <= i < len(streams):
                sys.exit(f"--marker-stream {i} is out of range (0..{len(streams) - 1})")
        return [streams[i] for i in forced_indices]
    return [s for s in streams
            if s is not audio and info_field(s, "type").lower() == "markers"]


def find_dropouts(ts, srate):
    """Return [(sample_index, step_seconds), ...] where the timestamps jump against the sample count.

    The residual timestamp - (ts[0] + i / srate) is flat except for buffer jitter. A lasting step up
    means samples were lost while time kept running. Each 10-ms block keeps its smallest residual,
    which removes most of the jitter. Each point is then compared with the 0.5 s before and after it.
    """
    block = max(1, int(round(srate * BLOCK_S)))
    n_blocks = len(ts) // block
    side = int(round(SIDE_S / BLOCK_S))
    if n_blocks < 2 * side + 1:
        return []
    residual = ts[: n_blocks * block] - ts[0] - np.arange(n_blocks * block) / srate
    block_min = residual.reshape(n_blocks, block).min(axis=1)
    window_min = np.lib.stride_tricks.sliding_window_view(block_min, side).min(axis=1)
    boundaries = np.arange(side, n_blocks - side + 1)
    step = window_min[boundaries] - window_min[boundaries - side]
    over = np.abs(step) > DROPOUT_THRESHOLD_S

    dropouts = []
    k = 0
    while k < len(boundaries):
        if not over[k]:
            k += 1
            continue
        end = k
        while end < len(boundaries) and over[end]:
            end += 1
        best = k + int(np.argmax(np.abs(step[k:end])))
        dropouts.append((int(boundaries[best] * block), float(step[best])))
        k = end
    return dropouts


def audio_origin(ts, srate, dropouts):
    """LSL time of the first sample, estimated from the start of the stream.

    This avoids the jitter of the first timestamp. It uses up to ORIGIN_WINDOW_S, and stops at the
    first dropout.
    """
    end = min(len(ts), int(ORIGIN_WINDOW_S * srate))
    if dropouts:
        end = min(end, dropouts[0][0])
    end = max(end, 1)
    residual = ts[:end] - ts[0] - np.arange(end) / srate
    return float(ts[0] + np.median(residual))


def fill_gaps(data, dropouts, srate):
    """Insert silence where samples were lost, so sample position matches LSL time."""
    pieces, previous, inserted = [], 0, 0
    for index, step in dropouts:
        pieces.append(data[previous:index])
        if step > 0:
            silence = int(round(step * srate))
            pieces.append(np.zeros((silence, data.shape[1]), dtype=data.dtype))
            inserted += silence
        previous = index
    pieces.append(data[previous:])
    return np.concatenate(pieces), inserted


def collect_markers(marker_streams, t0):
    """Return [(time relative to t0, absolute LSL time, label)] sorted by time."""
    markers = []
    for stream in marker_streams:
        prefix = f"{info_field(stream, 'name')}: " if len(marker_streams) > 1 else ""
        for stamp, value in zip(stream["time_stamps"], stream["time_series"]):
            label = value[0] if isinstance(value, (list, tuple, np.ndarray)) and len(value) else value
            markers.append((float(stamp) - t0, float(stamp), f"{prefix}{label}"))
    markers.sort(key=lambda m: m[0])
    return markers


def write_marker_labels(path, markers):
    """Audacity label track: start<TAB>end<TAB>label, in seconds from the start of the WAV."""
    with open(path, "w", encoding="utf-8") as handle:
        for rel, _, label in markers:
            handle.write(f"{rel:.6f}\t{rel:.6f}\t{label}\n")


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


def envelope(y, bins):
    """Min/max per bin so long recordings plot quickly without hiding peaks."""
    if len(y) <= 2 * bins:
        x = np.arange(len(y))
        return x, y, y
    step = len(y) // bins
    usable = (len(y) // step) * step
    shaped = y[:usable].reshape(-1, step)
    return np.arange(0, usable, step) + step / 2, shaped.min(axis=1), shaped.max(axis=1)


def plot_waveform(path, data, srate, title, markers=(), gap_times=()):
    n_ch = data.shape[1]
    fig, axes = plt.subplots(n_ch, 1, figsize=(14, 3 * n_ch), sharex=True, squeeze=False)
    duration = data.shape[0] / srate
    for ch, ax in enumerate(axes[:, 0]):
        x, low, high = envelope(data[:, ch], PLOT_BINS)
        ax.fill_between(x / srate, low, high, linewidth=0.3, color="#1f5fa8")
        for rel, _, _ in markers:
            if 0 <= rel <= duration:
                ax.axvline(rel, color="#e08a00", linewidth=0.6, alpha=0.7)
        for t in gap_times:
            ax.axvline(t, color="#d62728", linewidth=1.2)
        ax.set_ylabel(f"ch {ch + 1}")
        ax.set_xlim(0, duration if duration > 0 else 1)
        ax.grid(alpha=0.3)
    axes[-1, 0].set_xlabel("time from audio start (s), LSL-aligned")
    legend = []
    if markers:
        legend.append(plt.Line2D([], [], color="#e08a00", label="markers"))
    if gap_times:
        legend.append(plt.Line2D([], [], color="#d62728", label="lost samples (filled with silence)"))
    if legend:
        axes[0, 0].legend(handles=legend, loc="upper right", fontsize=8)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def process(xdf_path, forced_index=None, marker_indices=None, out_dir=None, raw=False):
    print(f"\n=== {xdf_path}")
    # Keep the real timestamps. Dejittering fits one straight line through them and would smear a
    # dropout over the whole stream.
    streams, _ = pyxdf.load_xdf(str(xdf_path), dejitter_timestamps=False)
    if not streams:
        print("  no streams found")
        return
    print_streams(streams)

    stream = select_audio_stream(streams, forced_index)
    name = info_field(stream, "name")
    srate = sample_rate(stream)
    data = np.asarray(stream["time_series"])
    ts = np.asarray(stream["time_stamps"], dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    if srate <= 0 or data.size == 0 or not np.issubdtype(data.dtype, np.number):
        sys.exit(f"stream '{name}' has no numeric samples or no sample rate")

    print(f"Selected: '{name}' ({data.shape[1]} ch, {srate:g} Hz, {data.shape[0] / srate:.2f} s of samples, "
          f"{ts[-1] - ts[0]:.2f} s of timestamps, {data.dtype})")

    if data.shape[1] > 1 and all(np.array_equal(data[:, 0], data[:, c]) for c in range(1, data.shape[1])):
        print("  WARNING: all channels are identical: one channel was copied, not recorded in stereo.")

    dropouts = find_dropouts(ts, srate)
    t0 = audio_origin(ts, srate, dropouts)
    gap_times = []
    if dropouts:
        print(f"  Lost samples: {len(dropouts)} place(s) where timestamps jump against the sample count:")
        shift = 0.0
        for index, step in dropouts:
            at = index / srate + shift
            kind = "samples lost" if step > 0 else "extra samples / timestamps went back"
            print(f"    at {at:9.3f} s (LSL {t0 + at:.3f}): {step * 1000:+8.1f} ms  ({kind})")
            gap_times.append(at)
            shift += max(step, 0.0)
    else:
        print("  No lost samples detected.")

    if raw:
        out_data = data
        print("  --raw: WAV is the samples only. Its time drifts from LSL time at each dropout above.")
    else:
        out_data, inserted = fill_gaps(data, dropouts, srate)
        if inserted:
            print(f"  Inserted {inserted} samples ({inserted / srate * 1000:.1f} ms) of silence so WAV time = LSL time - t0.")
        negative = [s for _, s in dropouts if s < 0]
        if negative:
            print(f"  NOTE: {len(negative)} backward jump(s) cannot be fixed by inserting silence; "
                  "the WAV timing is off by that amount after them.")
    print(f"  Audio start t0 = LSL {t0:.6f} s (WAV time 0)")

    markers = collect_markers(select_marker_streams(streams, stream, marker_indices), t0)
    target = out_dir or xdf_path.parent
    target.mkdir(parents=True, exist_ok=True)
    stem = target / xdf_path.stem
    wav_path, png_path = stem.with_suffix(".wav"), stem.with_suffix(".png")
    labels_path = target / f"{xdf_path.stem}_markers.txt"

    write_wav(wav_path, out_data, srate)
    plot_waveform(png_path, out_data, srate, f"{xdf_path.name} - {name} ({srate:g} Hz)",
                  markers if not raw else (), gap_times if not raw else ())
    print(f"Wrote {wav_path}\nWrote {png_path}")
    if markers:
        write_marker_labels(labels_path, markers)
        print(f"Wrote {labels_path}  ({len(markers)} markers; import in Audacity: File > Import > Labels)")
        for rel, stamp, label in markers[:5]:
            print(f"    {rel:9.3f} s  (LSL {stamp:.4f})  {label}")
        if len(markers) > 5:
            print(f"    ... {len(markers) - 5} more")
    else:
        print("  No marker streams (type 'Markers') found; use --marker-stream to pick one.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="folder with .xdf files, or a single .xdf file")
    parser.add_argument("--stream", type=int, help="stream index to export (skips auto-selection)")
    parser.add_argument("--marker-stream", type=int, action="append",
                        help="marker stream index (repeatable); default: every stream of type 'Markers'")
    parser.add_argument("--out", type=Path, help="output folder (default: next to each .xdf)")
    parser.add_argument("--raw", action="store_true",
                        help="write the samples only, without filling dropouts (old behaviour)")
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
        process(f, args.stream, args.marker_stream, args.out, args.raw)


if __name__ == "__main__":
    main()

# test-xdf

A small tool for checking an XDF file recorded with AudioCapture (e.g. through LabRecorder). It:

1. Lists every LSL stream in the file: name, type, channels, sample rate, format, samples, duration and *unaccounted* time. Unaccounted time is the timestamp span minus the time the samples cover. Positive means samples were lost.
2. Selects the audio stream. This is automatic when the file has only one stream, or only one stream of type `Audio`. Otherwise it asks you which one to use.
3. Finds places where AudioCapture **lost samples**: the LSL timestamps keep running but the samples stop.
4. Writes, next to the `.xdf` (or into `--out`):
   - `<name>.wav`: the audio on the **LSL time axis**. Lost samples are replaced by silence of the same length, so WAV time = LSL time − audio start.
   - `<name>.png`: the waveform, one panel per channel. Markers are drawn in orange and lost-sample places in red.
   - `<name>_markers.txt`: every marker (streams of type `Markers`) as an **Audacity label track**, on the same time axis as the WAV.

## Setup (once)

The `.venv` is not committed, so create it the first time:

```bash
cd external/app-audiocapture/test-xdf
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Put recordings under `recordings/<name>/`. That folder is git-ignored. Then run:

```bash
cd external/app-audiocapture/test-xdf
source .venv/bin/activate
python main.py ./recordings/dlb                    # every .xdf in the folder
python main.py ./recordings/dlb/dlb.xdf            # or a single file
python main.py ./recordings/dlb --stream 1         # pick the audio stream by index, no prompt
python main.py rec.xdf --marker-stream 0           # pick the marker stream(s) by index (repeatable)
python main.py rec.xdf --out /tmp/check            # write outputs to another folder
python main.py rec.xdf --raw                       # samples only, no silence for lost samples (old behaviour)
```

Example output:

```
 #  name                     type      ch     srate format     samples  duration  unaccounted
 0  LS4_MarkerStream         Markers    1       0.0 int32          201   770.17s
 4  Devialet_AudioStream     Audio      2   48000.0 float32   38225568   796.51s     +140.4ms
Selected: 'Devialet_AudioStream' (2 ch, 48000 Hz, 796.37 s of samples, 796.51 s of timestamps, float32)
  WARNING: all channels are identical: one channel was copied, not recorded in stereo.
  Lost samples: 3 place(s) where timestamps jump against the sample count:
    at    10.840 s (LSL 2708557.222):   +128.2 ms  (samples lost)
    ...
  Audio start t0 = LSL 2708546.382288 s (WAV time 0)
Wrote rec_markers.txt  (201 markers; import in Audacity: File > Import > Labels)
       23.470 s  (LSL 2708569.8524)  40104
```

## Checking sound timing against markers

1. Open `<name>.wav` in Audacity.
2. Import `<name>_markers.txt` with File > Import > Labels.
3. Read the sound onset relative to its marker directly.

Both files use the same zero, the audio start `t0`, and both follow the LSL timestamps.

**Do not compare markers with a WAV made with `--raw`, or with any tool that only counts samples.** After each lost-sample place, such a WAV runs early by the lost amount. In the 19-Nov-2026 Devialet recording this made the sounds look ~130 ms earlier than they were, and they appeared to come *before* their markers.

Accuracy: lost-sample places are located to ~10 ms, and the WAV timing is accurate to a few ms. AudioCapture timestamps jitter by about ±10 ms per buffer. The onset in the loopback audio is not the acoustic onset at the ears.

## What to check

- **Stream is present.** Its sample rate, channel count and duration match what you recorded.
- **Unaccounted time / lost samples:** should be about 0 ms and "No lost samples detected". If not, AudioCapture dropped audio. Try:
  - closing other programs;
  - turning off USB power saving;
  - starting AudioCapture before LabRecorder.
- **"All channels are identical" warning:** the recording is mono copied into stereo. If you expected separate left/right, fix the recording device or loopback setup.
- **Waveform PNG:**
  - Speech or test sounds should show up as clear bursts.
  - A flat band of noise near zero means the input captured almost nothing (for an int16 stream: peaks of only a few hundred out of ±32767). Check the input device and its gain.
  - Sounds that **fade out after they start**, even though the played file stays at a constant level, point to noise suppression or automatic gain on the Windows recording device. Turn off audio enhancements, Voice Clarity / noise suppression and automatic gain for that device.
- **WAV:** listen to it to confirm that the content and speed are correct.

Float streams are written as 16-bit WAV, assuming samples in [-1, 1]. If a stream goes beyond that range, it is normalised to its peak.

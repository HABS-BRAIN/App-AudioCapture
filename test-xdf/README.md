# test-xdf

A small tool for checking an XDF file recorded with AudioCapture (e.g. through LabRecorder). It:

1. lists every LSL stream in the file (name, type, channels, sample rate, format, samples, duration);
2. selects the audio stream. It does this automatically when the file has only one stream, or only one stream of type `Audio`. Otherwise it asks you which one to use;
3. writes `<name>.wav` and `<name>.png` (a waveform plot, one panel per channel) next to the `.xdf`.

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
python main.py ./recordings/dlb              # every .xdf in the folder
python main.py ./recordings/dlb/dlb.xdf      # or a single file
python main.py ./recordings/dlb --stream 1   # pick a stream by index, no prompt
```

Example output:

```
=== recordings/dlb/dlb.xdf
 #  name                     type          ch     srate format     samples  duration
 0  MyAudioStream            Audio          1   48000.0 int16      1176000    24.50s
Selected: 'MyAudioStream' (1 ch, 48000 Hz, 24.50 s, int16)
Wrote recordings/dlb/dlb.wav
Wrote recordings/dlb/dlb.png
```

## What to check

- **Stream is present** and its sample rate, channel count and duration match what you recorded.
- **Waveform PNG**: speech or test sounds should show up as clear bursts. If the waveform is a flat band of noise near zero (for an int16 stream, peaks of only a few hundred out of ±32767), the microphone captured almost nothing. Check the input device and its gain.
- **WAV**: listen to it to confirm that the content and speed are correct.

Float streams are written as 16-bit WAV, assuming samples in [-1, 1]. If a stream goes beyond that range, it is normalised to its peak.

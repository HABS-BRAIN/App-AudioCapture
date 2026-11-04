# AudioCapture — Ubuntu 24.04 NUC setup

This document records the tested installation approach for the Devialet audio-to-LSL setup on the HABS NUC (`Ubuntu 24.04 Noble`, `x86_64`).

- Upstream: https://github.com/labstreaminglayer/App-AudioCapture
- Release: https://github.com/labstreaminglayer/App-AudioCapture/releases/tag/v1.17.1
- Downloaded Debian package: `AudioCapture-0.2.0-noble_x86_64.deb`
- AudioCapture package version: `0.2.0`
- Qt runtime: `6.8.3` with `qtmultimedia`
- Co-installed LabRecorder: `1.18.0` (`/usr/bin/LabRecorder`, `/usr/bin/LabRecorderCLI`)

> **Scope:** This is an installation record for our specific machine, not a general Linux packaging fix. The Debian package includes `liblsl` files that conflict with the LabRecorder package already installed on this NUC. We repack AudioCapture without its bundled `liblsl` and use the existing LabRecorder-provided `liblsl.so.2`. Verify binary compatibility if either upstream package changes.

## 1. Clone the upstream source as a submodule (monorepo maintainers)

From the **monorepo root**:

```bash
git submodule add https://github.com/labstreaminglayer/App-AudioCapture.git external/App-AudioCapture
cd external/App-AudioCapture
git checkout v1.17.1
cd ../..
git add .gitmodules external/App-AudioCapture
```

On another checkout:

```bash
git submodule update --init --recursive
```

The source submodule is for upstream traceability and possible future builds. **The commands below install the published `.deb` release**, not a locally compiled submodule. Keep this guide outside the submodule, for example at `docs/external/audio-capture.md`, so upstream updates do not overwrite it.

## 2. Install base dependencies

```bash
sudo apt update
sudo apt install -y curl wget jq python3-venv libxcb-cursor0
mkdir -p ~/apps/audiocapture
cd ~/apps/audiocapture
```

No OS reboot is required for these steps.

## 3. Download the Ubuntu 24.04 package

```bash
wget -O AudioCapture-0.2.0-noble_x86_64.deb \
  https://github.com/labstreaminglayer/App-AudioCapture/releases/download/v1.17.1/AudioCapture-0.2.0-noble_x86_64.deb
```

The release filename contains `noble` for Ubuntu 24.04 and `x86_64` for the NUC architecture.

## 4. Resolve the bundled liblsl conflict with LabRecorder

First, inspect existing packages and shared-library ownership:

```bash
dpkg -l | grep -Ei 'audiocapture|labrecorder'
dpkg -L labrecorder | grep -E '/bin/|liblsl.so' || true
ls -l /usr/lib/liblsl.so* 2>/dev/null || true
```

**On our NUC**, LabRecorder `1.18.0` already owns `/usr/lib/liblsl.so.1.18.0`, plus `liblsl.so` and `liblsl.so.2` symlinks. Installing the original AudioCapture `.deb` failed with `trying to overwrite '/usr/lib/liblsl.so.1.18.0'`.

Only if the same LabRecorder-provided `liblsl.so.2` is already present, repack AudioCapture without its duplicate LSL libraries:

```bash
cd ~/apps/audiocapture
rm -rf package
mkdir package
dpkg-deb -R AudioCapture-0.2.0-noble_x86_64.deb package

# Confirm the files to remove belong to the extracted package:
ls -l package/usr/lib/liblsl.so*

# Remove ONLY the duplicate copies from the extracted package:
rm -f package/usr/lib/liblsl.so*

dpkg-deb -b package audiocapture-fixed.deb
sudo apt install ./audiocapture-fixed.deb
```

Do **not** use `dpkg --force-overwrite` and do **not** delete the working LabRecorder libraries under `/usr/lib`.

Check:

```bash
dpkg -l | grep -Ei 'audiocapture|labrecorder'
command -v AudioCapture LabRecorder LabRecorderCLI
```

Expected executable paths on this NUC:

```text
/usr/bin/AudioCapture
/usr/bin/LabRecorder
/usr/bin/LabRecorderCLI
```

**Compatibility caution:** The files in the two `.deb` archives had the same LSL versioned filename but different sizes. Sharing LabRecorder's copy worked far enough to start AudioCapture's GUI, but this is not proof that every LSL streaming path has been validated. Perform a real LSL-to-XDF recording smoke test before relying on the setup.

## 5. Install an isolated Qt 6.8.3 runtime

Ubuntu 24.04's system Qt is too old for the provided AudioCapture binary. Do not replace the system Qt used by other applications.

Install `aqtinstall` in a Python virtual environment:

```bash
python3 -m venv ~/apps/qt-installer
~/apps/qt-installer/bin/pip install aqtinstall
```

The Qt archive architecture **must be `linux_gcc_64`** (not `gcc_64` in the `aqt` command). The resulting directory is still named `gcc_64`.

Install Qt base and Qt Multimedia:

```bash
~/apps/qt-installer/bin/aqt install-qt \
  linux desktop 6.8.3 linux_gcc_64 \
  -m qtmultimedia \
  -O ~/Qt
```

Validate both libraries:

```bash
ls -l ~/Qt/6.8.3/gcc_64/lib/libQt6Core.so.6
ls -l ~/Qt/6.8.3/gcc_64/lib/libQt6Multimedia.so.6

LD_LIBRARY_PATH="$HOME/Qt/6.8.3/gcc_64/lib" \
  ldd /usr/bin/AudioCapture | grep -E 'Qt6Core|Qt6Multimedia|liblsl|not found'
```

Both Qt libraries must resolve to `~/Qt/6.8.3/gcc_64/lib/`, not the OS Qt 6.4 libraries.

## 6. Create a desktop launcher

Create a wrapper that loads Qt 6.8.3 **for AudioCapture only**:

```bash
mkdir -p ~/bin
cat > ~/bin/start-audiocapture <<'SH'
#!/usr/bin/env bash
export LD_LIBRARY_PATH="$HOME/Qt/6.8.3/gcc_64/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec /usr/bin/AudioCapture "$@"
SH
chmod +x ~/bin/start-audiocapture
```

Create application-menu and desktop launchers:

```bash
mkdir -p ~/.local/share/applications
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
[ -n "$DESKTOP" ] && [ -d "$DESKTOP" ] || DESKTOP="$HOME/Desktop"
mkdir -p "$DESKTOP"

cat > ~/.local/share/applications/habs-audiocapture.desktop <<EOF
[Desktop Entry]
Type=Application
Name=AudioCapture (LSL)
Comment=Capture microphone/USB audio and stream over LSL
Exec=$HOME/bin/start-audiocapture
Icon=audio-input-microphone
Terminal=false
Categories=AudioVideo;Audio;
EOF

cp ~/.local/share/applications/habs-audiocapture.desktop "$DESKTOP/AudioCapture.desktop"
chmod +x "$DESKTOP/AudioCapture.desktop"
```

In GNOME, use **right-click → Allow Launching** on the desktop icon if required. Launch the app from the NUC's **local desktop**.

**SSH caveat:** `DISPLAY=:0` alone does not authorize an SSH shell to use the NUC's graphical session. Errors such as `Authorization required` / `qt.qpa.xcb: could not connect to display :0` are generally desktop-session authorization problems, not installation failures.

LabRecorder GUI and CLI are already installed together on this NUC. Run `LabRecorder` or `LabRecorderCLI` respectively; no extra GUI package is needed.

## 7. Verify the USB audio input

On our setup, a **UGREEN USB-to-3.5 mm TRRS adapter** only became visible as a microphone when a **4-pole TRRS plug** was inserted. With a 3-pole TRS cable, `KT USB Audio` appeared as an output (sink) but not as an input (source); AudioCapture then listed only `Built-in Audio Analog Stereo`.

```bash
lsusb
arecord -l
pactl list short sources
pactl list short sinks
wpctl status
```

The adapter must appear under **Sources** before it can be selected as an AudioCapture input. If it does not appear, verify the CTIA TRRS plug and audio profile, then restart AudioCapture.

**Wiring safety:** The TRRS microphone input is **not line-level**. Connecting NUC headphone/AUX output directly may clip or overload it. Start at very low volume and use suitable attenuation/coupling as needed. A TRRS headset microphone breakout (headphone + mic) is *different* from the Y-splitter duplicating the playback output; both may be needed for the planned wiring.

## 8. LSL / XDF smoke test

1. Launch AudioCapture on the local NUC desktop.
2. Select the USB microphone input.
3. Use **Link** to publish an Audio LSL stream; 48 kHz and 16-bit PCM are reasonable settings where supported.
4. Open `LabRecorder` GUI and refresh the available streams.
5. Confirm the Audio stream appears alongside EEG and marker streams.
6. Record a short XDF and validate nonempty samples/timestamps. Seeing the input in a dropdown is **not** proof of successful recording.
7. For later automation, confirm `command -v LabRecorderCLI`; check its installed CLI help before assuming options.

**Timing:** The physical analog loopback is intended to observe when the NUC's AUX output becomes active. A PipeWire monitor stream observes a software-side signal instead and cannot establish physical output timing or Bluetooth/acoustic latency.

## 9. Troubleshooting

| Symptom | Meaning / action |
| --- | --- |
| GitHub API `403` | Download the versioned `.deb` directly from the release asset URL. |
| `trying to overwrite ... liblsl.so.1.18.0` | Shared-file conflict with LabRecorder. Follow section 4; do not force overwrite. |
| `Qt_6.8 not found` | AudioCapture is using Ubuntu's Qt 6.4. Install isolated 6.8.3 and use the wrapper. |
| `qt_base ... not found` in `aqt` | Use architecture `linux_gcc_64`. |
| `undefined symbol ... Qt_6_PRIVATE_API` | Mixed Qt versions. Install `qtmultimedia` for the same Qt 6.8.3 runtime. |
| `Authorization required` / `could not connect to display :0` | SSH is not authorized for the local GUI. Start from the desktop. |
| Only Built-in Audio input is listed | Check `pactl ... sources`; on our UGREEN, inserting a 4-pole TRRS plug enabled its microphone input. |

## Versioning

Pin the source submodule at GitHub tag `v1.17.1`, the separately downloaded `.deb` at `AudioCapture-0.2.0-noble_x86_64.deb`, and runtime Qt at `6.8.3`. These are separate version identifiers. On upgrades, recheck package ownership, binary dependencies, audio input enumeration and the full LSL → XDF smoke test.

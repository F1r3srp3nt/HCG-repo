## 0.1.0~beta1

**Free public beta demo — build-validated and Simulator-tested; real-device testing pending.** First release.

- **Media library** (Settings › HCG Animation Center › Media Library): import videos and animated GIFs from Photos or
  Files, several at once, with progress and Cancel. Videos keep only their video track (passthrough; converted to
  1920 × 1080 H.264 only if larger than 2688 px or not copyable). GIFs become H.264 video (browser frame timing,
  ≥ 1 s). Each item gets a still and a thumbnail. Still images, Live Photos, damaged, empty and oversized files are
  refused with a reason.
- **Editor** with live preview on the same renderer as SpringBoard: trim, Fill / Fit / Crop (zoom slider and drag),
  loop or play once, speed 0.5×–2×, still frame, name; Apply to Home Screen / Lock Screen, Export Preset, Delete.
- **Home Screen backgrounds** behind the icons, dock and widgets (`SBHomeScreenWindow`, index 0). Icons, folders and
  gestures are untouched; the background never takes a touch.
- **Lock Screen backgrounds (experimental, off by default):** on the Lock wallpaper view, or the Cover Sheet window;
  hidden whenever the phone is unlocked. Placement is chosen at run time and can be changed in Advanced.
- **Playback rules:** always muted; pauses behind apps, when locked / unlocked as appropriate and when the screen is
  off; decoders freed after 8 s hidden and on memory warnings; Reduce Motion → still (or keep playing); Low Power
  Mode → still, keep playing, or system wallpaper; hot device → still; video failure → still → system wallpaper.
- **Recovery:** master switch, Restore System Wallpaper, kill switch file, auto-disable after three quick SpringBoard
  crashes, fault fallback. The system wallpaper is never modified (enforced in validation).
- **Diagnostics:** live status per surface, environment summary, read-only hierarchy probe, diagnostic logging.
- **HCGBackgroundKit 1.0.0:** HCG background preset format version 1 (schema, examples) and renderer, as an opt-in
  Swift package for HCG apps.
- **HCG AI interface** (later phase): provider protocol behind central HCG access controls; generated results are
  previews until explicitly applied. Not connected in this beta; no network code.
- Not yet verified on a physical device.

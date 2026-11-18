# Owner device evidence reviewed on 18 Nov 2026

This record says what each piece of owner-supplied evidence shows, and which release it is attached to. Evidence is
attached to a release only when the installed version and the device are established. Nothing here is a completed
device checklist. Frame rate, performance and battery impact are **unmeasured** for every product.

| Evidence | Device | Version | Recorded as |
|---|---|---|---|
| Camera recording of the Home Screen, 29.3 s (reviewed in HCG-Jailbreak-Research `HCGVortex/docs/device-tests/2026-11-18-recording-0.3.0.md`, branch `claude/vortex-polish`) | iPhone 11 Pro Max (wallpaper text; the owner's phone) | HCG Vortex: **not visible**. Settings lists six effects, which points to 0.3.0~beta1 but does not prove it | **Not attached** to any Vortex version. Described in Vortex's Known issues, with the visual bugs F1–F3 |
| Same recording + owner report | iPhone 11 Pro Max | HCG Halo **0.1.0~beta1**: named by the owner. It was also the only HCG Halo build in any source at the time | HCG Halo 0.1.0~beta1: `stage = "runtime"`, `result = "partial"` |
| Owner report, 17 Nov 2026 (`docs/device-tests/hcg-nexus-0.2.1-beta1.md` on `candidate/hcg-sentinel-0.1.0-beta1`) | iPhone 13 Pro, iOS 16.5, Dopamine (rootless) | HCG Nexus: **not stated** | **Not attached**. Described in Nexus's Known issues |

## HCG Halo 0.1.0~beta1: partial runtime observation

| Field | Value |
|---|---|
| Device | iPhone 11 Pro Max |
| iOS / jailbreak | 17.4, Dopamine 2.x (rootless): the owner's confirmed setup of this phone on 17 Nov 2026. The recording does not show them |
| Observed | Halo's neon edge glow is visible and stable on the Home Screen through every page transition in the recording, with HCG Vortex running. No artefact lines up with Halo's elements |
| Not observed | Install log, Settings, modes and presets, calibration, Lock/unlock, respring, disable, uninstall |
| Not covered | HCG Halo 0.2.0~beta1 and the HCG Nexus panel border: **not yet device-tested** |

## HCG Vortex: recording of a six-effect build (version not confirmed)

- **Seen running:** Orbital, Tornado and Magnetic Grid. Completed, reversed and cancelled swipes restored the grid
  exactly; labels and badges came back after every transition; no clipping or stretching.
- **Reported visual bugs** (fixed in 0.3.1~beta1, which is itself not yet installed on a device):
  - **F1:** at the start of a swipe, the incoming page's icons appear untransformed at their grid positions for about
    one frame, with doubled labels (Orbital, Tornado; four separate swipes).
  - **F2:** Tornado forms a thin diagonal "rope" with 1–2 icons per height; its tip reaches the dock; labels clutter it.
  - **F3:** Orbital forms a crowded disc with overlapping icons; the rings are not legible.
- **Not shown:** Warp, Holographic Layers, Circuit Flow; tapping an app after a transition; Reduce Motion; widgets.
- **To attach it to 0.3.0~beta1:** confirm the installed version (Settings › HCG Vortex › Reset & Diagnostics ›
  Version, or `dpkg-query -W com.hakmicapitalgroup.vortex`), iOS and jailbreak, then add a `partial` runtime entry.

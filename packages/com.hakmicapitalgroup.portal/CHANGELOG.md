# Changelog

## 0.1.0~beta1

First beta, free public candidate demo.

First version of `com.hakmicapitalgroup.portal`.

- **Lock Screen portal** below the clock and widgets, above notifications: dark translucent centre, cyan-and-violet
  energy rings turning slowly in opposite directions, soft glow, sparse particles and a shadow for depth.
- **Tap → radial menu** (Nexus, Backgrounds, Devices, HCG Apps); tap outside, 12 s idle, screen-off or leaving the Lock
  Screen collapses it. Only the orb and the 64 pt item discs take touches; everything else passes through.
- **Authentication first:** Nexus, Backgrounds and every app open only after the normal iOS Face ID / Touch ID /
  passcode request succeeds, and only the selected action runs, once. Cancelling, a refused request, screen-off,
  disabling or a 90 s timeout discards it.
- **Destinations:** Backgrounds opens Settings for HCG Animation Center; HCG Apps holds up to six apps you choose;
  Devices shows *Not connected*; Nexus shows *Update needed* until HCG Nexus adds `HCGNexusPortalInterface` v1 (Portal's
  side is ready). Missing companions show *Not installed*.
- **Coexistence:** measures the clock, widgets, notifications, media controls, Live Activities, flashlight and camera
  and moves, shrinks, collapses to a compact orb or hides so it never covers them.
- **Settings:** live mini Lock Screen preview; Position & Size (drag, pinch, sliders, sample notifications, Reset);
  colour (HCG Signature, Aurora, Solar, Mono), brightness, animation speed, intensity; opening animation on unlock;
  Low Power Mode (Static / Economy / Hide); HCG Apps; status, recovery and diagnostics.
- **Efficiency:** Core Animation only; paused whenever not visible. Reduce Motion → static.
- **Safety:** off by default, master switch, kill switch, launch guard, fault fallback; three `%orig`-first hooks.
  Optional lighting events (v1) for a future HCG Halo.
- **Not yet device-tested.** Simulator-tested only.

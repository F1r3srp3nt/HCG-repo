## 0.3.1~beta1

**Free public beta demo — simulator-tested; real-device testing pending.** Everything in 0.3.0~beta1, plus the switch HCG Nexus 0.3.2~beta1 uses. Earlier versions are unchanged.

- **Use HCG Halo border from HCG Nexus:** HCG Nexus › Appearance can now turn HCG Halo's panel border on or off. HCG Halo decides how its settings change and **never turns the screen-edge light on** for this:
  - with HCG Halo off, it lights the panel only;
  - with the screen edge on, it adds the panel;
  - turning it off removes only the panel.
- *Show Halo on*, rendering and the screen edge are unchanged. The switch is refused while HCG Halo's kill switch or launch guard has stopped it.

## 0.3.0~beta1

**Free public beta demo — simulator-tested; real-device testing pending.** 0.2.0~beta1 and 0.1.0~beta1 are unchanged.

- **Show Halo on** (screen edge; applies at once): *Home Screen Only* (default), *Selected App Only* with an installed-app picker, *Always* (lit Lock Screen, Home Screen and apps), *Always Except Lock Screen*, *Always Except When Screen Is Off*.
- A fully off display can't show a border: every choice hides it and pauses rendering. No Always-On display mode, so *Always* and *Always Except When Screen Is Off* behave identically. Low Power Mode and Reduce Motion apply under every choice.
- The choices beyond the Home Screen draw in HCG Halo's own non-interactive overlay window, hidden whenever the border is hidden.
- The HCG Nexus panel border is unchanged.
- **Display Zoom:** corners and notch are scaled to the zoomed size (an iPhone 11 Pro Max reports 375 × 812 pt). Settings shows points, pixels, native scale and the model identifier. Reset Calibration if you calibrated under Display Zoom.

## 0.2.0~beta1

**Free public beta demo — simulator-tested; real-device testing pending.** Adds an optional border around the HCG Nexus panel. The 0.1.0~beta1 package is unchanged.

- **Border Target:** Screen Edge (the default, unchanged for existing users), HCG Nexus Panel, or Both.
- **HCG Nexus panel border** (needs HCG Nexus 0.3.1~beta1, or 0.2.2~beta1):
  - Halo reads the border definition that HCG Nexus installs and draws around the live panel.
  - It follows the panel's position, slide, size and rotation, and never covers its content or takes touches.
  - It appears only while the panel is shown.
  - A missing or incompatible Nexus turns only the panel border off, with the reason.
- **Panel look:** Match Screen Edge or a separate look, with the Nexus Signature, Reactor and Hologram presets.
- **Panel effects:** opening trace, fade with the panel and workspace pulse. All are brief, and the trace is skipped with Reduce Motion.
- **Reset Nexus Lighting**, which resets the panel only.
- **Soft Pulse** slider for any mode except Breathing.

## 0.1.0~beta1

**Free public beta demo — not yet device-tested.** First build of HCG Halo.

- Animated light border around the Home Screen edges, following the rounded display corners and the notch.
- Modes: Static, Breathing, Colour Flow, Orbit and Sci-Fi Circuit.
- Single colour, multiple colours or gradient, with 1–6 colours, a colour picker and quick palettes.
- Brightness, thickness, glow, edge inset, speed, direction, colour spacing, and Orbit segment count and length.
- Presets: six built-ins. Save, rename, duplicate and delete your own.
- iPhone 11 Pro Max device profile (implemented, not yet device-tested) with corner and notch calibration.
- Off after installation. Kill switch, crash-loop guard. Pauses when the screen is off, when the phone is locked or
  when an app is open. Follows Reduce Motion, with a choice of Low Power Mode behaviour.
- No account, payment, telemetry or network access.

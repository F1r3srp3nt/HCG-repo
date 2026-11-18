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

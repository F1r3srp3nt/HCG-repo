## 0.3.1~beta1

Free experimental beta. Polish and fixes after the first phone recording; no new effects. Not yet installed on a device.

- **Fixed:** a brief flash at the start of a swipe, where the next page's icons appeared in place for one frame
  (Orbital, Tornado).
- **Tornado** reads as a funnel again: icons spread around every height of the funnel, sit smaller and tapered, and
  the tip stays clear of the dock.
- **Orbital** shows clear rings instead of a crowded disc.
- Icon labels are hidden while icons are in a formation and come back with the grid.
- A recording of the previous build (most likely 0.3.0~beta1; version not confirmed) showed Orbital, Tornado and
  Magnetic Grid running on an iPhone 11 Pro Max. Warp, Holographic Layers and Circuit Flow are not yet device-tested.
  Performance and battery impact are unmeasured.

## 0.3.0~beta1

Free experimental beta. Not yet installed or tested on a device.

- **New icon effects:**
  - **Orbital:** icons circle on concentric rings;
  - **Warp:** icons pass through a short tunnel;
  - **Holographic Layers:** floating parallax layers;
  - **Circuit Flow:** icons run along glowing circuit traces;
  - **Magnetic Grid:** a subtle loosen-and-snap, the everyday option.
- **Tornado:** new controls for funnel height, upper and lower radius, depth & perspective and travel, plus presets
  Subtle, Classic Tornado and Sci-Fi Vortex.
- **Settings:** a page per effect with a description, live preview, controls and Reset to Defaults; an Appearance page
  (accent colour, glow) and an Accessibility & Performance page.
- With Reduce Motion on, every icon effect uses the stock animation.
- Unchanged: Reverse Slide, Vertical Rise, Vertical Fall, Custom Directions, and gzip packaging.

## 0.1.0~beta2

**Packaging compatibility candidate — installation fix not yet device-verified.**

- Repackaged with gzip (`data.tar.gz`). 0.1.0~beta1 downloaded but failed to unpack on an iPhone 11 Pro Max running
  iOS 17.4 (`dpkg-deb` Bus error). The cause is still being investigated. This build changes only the package format.
- No changes to effects, settings, hooks or dependencies. Same features as 0.1.0~beta1.
- Not yet installed on any device. Runtime behaviour is untested.

## 0.1.0~beta1

First public beta. Free and experimental.

- Effects: Reverse Slide, Vertical Rise, Vertical Fall, iOS Default and Custom Directions (8-way, incoming and outgoing set separately).
- Separate effects for swipe left and swipe right; reverse, duration, speed, intensity, easing and page fade.
- Settings pane with engine status. Changes apply without a respring.
- Safety: installs no hooks unless every required SpringBoard method is present and verifies the hooks after installing; master switch; kill switch; stops loading after repeated SpringBoard crashes.

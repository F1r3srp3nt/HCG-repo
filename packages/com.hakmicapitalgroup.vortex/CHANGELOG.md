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

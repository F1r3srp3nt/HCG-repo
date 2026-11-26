# Changelog

## 0.1.0~beta5

**Diagnostic candidate, awaiting physical-device validation.** HCG Portal 0.1.0~beta4 was reported not to appear on a
physical iPhone. This build adds evidence on the device and fixes for the likely causes. **Simulator validated only;
it is not yet known whether it appears and responds on a physical iPhone.**

- **Rendering Diagnostics** (Settings › HCG Portal):
  - one line per stage, with SpringBoard's own evidence: installed and loaded version, injection, hook route, lock
    state (raw values), Cover Sheet (raw flags), host view, overlays created and attached, frame and bounds,
    hidden / alpha / clipping / masks of every ancestor, what a touch at the orb's centre reaches, animation;
  - the last attempt, the last success and the exact refusal reason, and a timestamped history (80 entries);
  - **Copy Diagnostics** for sending the report; no SSH needed. A stage is marked ✓ only from evidence.
- **Visual Test in Settings:** the real portal renderer and touch code inside Settings, with checks for renderer,
  artwork, layout, animation, orb taps, menu open and close and destination taps. Nothing opens from it, and it is
  never on the Lock Screen.
- **Cover Sheet Test Orb** (off by default): a static pink *PORTAL TEST* circle with a tap counter, at a fixed place on
  the unlocked Cover Sheet, with no animation or menu, so the attachment and touch path can be tested on their own.
- **Fixes for the likely causes:**
  - the hook interface is also looked up in the injector's own library (ElleKit or Substrate), and the route is
    recorded;
  - Cover Sheet visibility comes from `-isVisible` (then `-isPresented`);
  - data protection is read from `MKBGetDeviceLockState` (read only; no other MobileKeyBag call, enforced by the
    build), with `-isProtectedDataAvailable` as a fallback; unclear values keep Portal hidden;
  - a 0.7 s settle delay after an unlock while the Cover Sheet is visible, reported as such.
- **Unchanged:** unlock gating (fail closed), removal on lock with pending actions cancelled, no authentication code,
  kill switch, off after installation.
- Validation: Xcode 16.4 build and `validate.sh --require-new-abi` (0 failures, 0 warnings); host tests (2,927,412
  checks); Simulator runtime tests; Simulator preview 147/147.

## 0.1.0~beta4

Unlock gating and a touch-interaction repair. **Simulator validated; physical iPhone validation pending.** The touch
repair and the earlier Safe Mode issue are not confirmed resolved on real hardware.

- **Only on an unlocked iPhone.**
  - Portal is shown only while every lock-state source iOS offers reports the device unlocked: data protection,
    SpringBoard's lock state and `-[SBLockScreenManager isUILocked]`.
  - If any source says locked, or cannot be read, Portal stays hidden (fail closed).
  - It appears on the Cover Sheet pulled down after unlocking, and never on the locked Lock Screen. Face ID matching
    while the Lock Screen is still up does not count as unlocked.
- **No authentication code.** Portal no longer requests Face ID, Touch ID or the passcode, and never bypasses them;
  it only reads the outcome. The build fails if any unlock request is referenced.
- **Locking removes it at once** and discards anything pending.
  - An action carries the lock-state "epoch" it was chosen in, and runs only if that epoch is still current. It is
    re-checked when you tap, after the Cover Sheet closes, and right before iOS is asked to open anything.
  - So nothing chosen before a lock can run after it, even after an immediate unlock.
- **Touch repair.** On 0.1.0~beta3 the portal could be drawn but not respond: SpringBoard's own tap gestures on the
  Cover Sheet took the taps. The orb and the items now use their own tap handling, which those gestures wait for, only
  for touches on Portal. Swipes and long presses are untouched.
- **Opening a destination closes the Cover Sheet first**, then opens Settings, the chosen app or Nexus.
- **Settings › HCG Portal** shows the lock state as Portal reads it, touch diagnostics and the last lifecycle step.
- Validation: Xcode build and `validate.sh` (0 failures, 0 warnings); host tests (2,927,359 checks, including every
  lock-state combination and lock/unlock storms); Simulator runtime 65/65; preview 122/122 with real UIKit touch
  events.

## 0.1.0~beta3

Lock Screen lifecycle hardening after the SpringBoard Safe Mode incident with 0.1.0~beta1. **Test build only. The
cause is not confirmed:** no SpringBoard crash report from the incident has been reviewed yet. This build removes every
lifecycle hazard found in review and records where SpringBoard was in Portal's lifecycle when it last stopped, so the
next device test can tell the possible causes apart (`docs/INCIDENT_0.1.0-beta1.md`).

- **Attach once, detach on leave.** Portal joins the Lock Screen's main page once per Lock Screen session and leaves it
  when the Cover Sheet disappears, the screen turns off, the phone unlocks or Portal is turned off. Before, every
  update and every layout pass re-inserted the overlay and brought it to the front, inside SpringBoard's own layout.
  Bringing it to the front now happens at most once a second.
- **No fallback container.** If the Lock Screen's main page is missing, not loaded, not the expected class or not in a
  window, Portal stays hidden and the Lock Screen carries on; Settings shows *Lock Screen container unavailable* and
  why. 0.1.0~beta1/2 fell back to the Cover Sheet's own view.
- **Callbacks cannot outlive the view.** The idle collapse (12 s), message fade (3.5 s), tap-outside collapse and
  relayout timers hold Portal weakly and do nothing once it has left the Lock Screen. The measure timer stops on detach.
- **Layout storms are coalesced** (one relayout per 250 ms) and an update can no longer re-enter itself.
- **The radial menu is rebuilt only when its items change**, not on every update.
- **Crash guard counts every launch**, enabled or not (0.1.0~beta1/2 counted only while enabled, so a crash on the
  disabled path never tripped it).
- **Breadcrumbs.** Portal records its lifecycle step (*bootstrap*, *installing hooks*, *attaching to the Lock Screen*,
  *on the Lock Screen*, *off the Lock Screen*, *requesting authentication*, …) in its status file when it changes.
  After the next launch Settings › HCG Portal › *Diagnostics* shows the step the previous launch ended on and whether
  it ended within 30 s.
- Simulator preview: 20 lock / wake / menu / unlock cycles, a 300-pass layout storm, SpringBoard rebuilding the main
  page, the container unavailable, late callbacks after teardown, 10 disable / re-enable cycles and an authentication
  completing after Portal was turned off.

## 0.1.0~beta2

*Test build only; never published in HCG Repo. Its startup fixes are included in 0.1.0~beta3.*

Startup hardening after an observed SpringBoard Safe Mode incident (Sileo reached *Setting up HCG Portal*, then
SpringBoard entered Safe Mode). Test build only; root cause and device recovery still need confirmation on the
affected phone (the SpringBoard crash report has not been reviewed yet).

- **No SpringBoard Lock Screen objects at launch.** In 0.1.0~beta1 the bootstrap ran from the dylib constructor on
  every SpringBoard launch, enabled or not, and ended by writing its status. Building that status called
  `+[SBCoverSheetPresentationManager sharedInstance]` and could fall through to `+[SBLockScreenManager sharedInstance]`,
  creating SpringBoard's own Lock Screen singletons before SpringBoard does. The status path now consults them, and
  companions' runtime interfaces, only while Portal is active, which never happens before SpringBoard has finished
  launching. The preview checks that neither is touched while Portal is off or inactive.
- **No load-time Substrate dependency.** The hooks are plain Objective-C (`src/Tweak.m`, no Logos), and the injector's
  `MSHookMessageEx` is resolved with `dlsym` only when Portal is enabled. If it is unavailable, Portal stays inactive
  and Settings shows why (*Incompatible: inactive* plus the reason); Enable off and on retries. `validate.sh` fails if
  either slice links Substrate. (Published HCG Halo and Vortex builds do link it and run on the same phone, so this
  alone is not thought to be the cause.)
- The bootstrap is queued on the main queue from the constructor.
- The hooks fall back to the superclass implementation should the injector ever report no original.

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

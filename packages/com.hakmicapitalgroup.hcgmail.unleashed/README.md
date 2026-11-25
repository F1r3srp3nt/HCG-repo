# HCG Mail Unleashed: listing assets

- `icon.png`: HCG Mail's own app icon (`src/HCG.Mail.iOS/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png` in
  F1r3srp3nt/HCG-Mail at `b0c6fee`), scaled to 180 × 180. The handoff's icon was not used: it is the compiled app's
  CgBI ("Apple-optimized") PNG, which browsers and package managers cannot decode. Replace it when HCG provides official listing artwork.
- No header image: Sileo shows the HCG Repo banner.
- Listing text: the `.deb`'s control `Description`, plus `package.toml`.

# Configuration upgrades

## Packaged Windows behavior

The packaged launcher uses `%LOCALAPPDATA%\HALS Near Field Scanner` as its
writable runtime directory (falling back to `%APPDATA%`, then
`~\.hals-near-field-scanner`). On each launch:

- A missing `config.ini` is seeded from the bundle. An existing user file is
  preserved byte-for-byte by the launcher, including comments and custom settings.
- `config_default.ini` is refreshed from the current bundle, even if it already
  exists. Keep personal settings in `config.ini`, not in this reference file.
- Missing bundled files and copy errors remain non-fatal. If a copy fails, the
  defaults may not be current; check permissions on the runtime directory.

This hotfix changes file seeding only. It does not merge new settings into the
user configuration or change motion safety behavior.

## Known compatibility issues

Legacy `safe_radius` describes a retract radius. It cannot be automatically
translated into current safety geometry: it does not establish the physical
clearances or exclusion regions of the actual machine. Preserve a backup and
review the configuration against the current reference and measured rig geometry
before permitting motion. Do not guess safety values or bypass path validation.

Motion Preview is enabled by default, including when `[app]` or
`show_motion_preview` is missing from an existing user configuration. To disable
it, explicitly set `show_motion_preview = false` under `[app]` in the user
configuration; that explicit choice is preserved.

Do not restore defaults or reinstall as a remedy for these compatibility issues.
Restoring defaults can discard machine-specific settings, while reinstalling does
not migrate the preserved per-user configuration. Resolve the affected settings
explicitly, with the hardware disconnected until safety configuration is verified.

## Manual release checklist: clean packaged Windows

These checks require the actual installer on a clean Windows machine or VM;
the hardware-free launcher tests do not replace them.

- [ ] With no Python or development tools installed and no existing per-user HALS
  data, install and launch as a standard user. Keep motion hardware disconnected.
- [ ] Verify startup succeeds and both configuration files appear in the writable
  per-user directory, matching the files bundled with this release.
- [ ] Exit, customize `config.ini` with recognizable settings and comments, and
  save an external backup or hash. Launch again and verify the launcher preserves
  it; avoid saving GUI settings during this comparison.
- [ ] For an upgrade, start from a previous packaged release and its customized
  user configuration. Record both configuration files, then install the new
  release without deleting per-user data.
- [ ] Launch the upgraded executable. Verify `config.ini` is unchanged and
  `config_default.ini` matches the new release, not the old reference. Repeat the
  launch to confirm the same behavior.
- [ ] Exercise a legacy configuration containing `safe_radius`; verify safety
  configuration is reviewed explicitly rather than inferred from that value.
- [ ] Check that missing `[app]` or `show_motion_preview` shows the Motion Preview
  option by default, and that an explicit `[app]` /
  `show_motion_preview = false` setting hides it, without connecting hardware.
- [ ] Record installer version, Windows version, results, and any startup errors.
  Do not authorize real motion until the complete path and rig geometry are verified.

## Future work, not part of this hotfix

Versioned configuration migrations are explicitly deferred. A future design
should identify schema versions, back up user settings, validate changes, and
require explicit review where safety geometry cannot be inferred. This hotfix
does not add schema versions, automatic migrations, or safety-value conversion.
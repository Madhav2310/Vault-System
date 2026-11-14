# Quarry 4.2 Release Notes

Released 8 September 2025.

## New Features

Incremental builds are now enabled by default and reduce repeat build times by about 40% on the reference project. A new `quarry doctor` command checks the local setup and prints fixes.

## Changes

The default cache directory moved from `.quarry-cache` to `.quarry/cache`. The minimum supported Node version is now 20.

## Deprecations

The `--legacy-resolver` flag is deprecated and will be removed in version 5.0.

## Fixes

Fixed a crash when a configuration file contained a byte-order mark. Fixed incorrect cache invalidation when a symbolic link target changed. Fixed progress output on Windows terminals.

## Known Issues

Watch mode may miss file changes on network drives. Plugin hot reload is not supported with incremental builds.

## Upgrading

Run `quarry migrate` before the first build to move existing cache data.

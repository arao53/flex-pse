# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Project scaffold: four-package `src/` layout (`flexcore`, `flexops`, `flexparameterize`, `flexschedule`), import-linter DAG contract, pytest tier markers with collection-time enforcement, and CI skeleton (M00).
- Exception hierarchy; pinned idaes-pse/pyomo versions (M01).
- Added `TimeBlock`: the discrete-time substrate (ordered integer time set, unit-carrying `dt`, datetime↔index utilities, rolling-horizon initial-state registry and window metadata); minimal Sphinx docs skeleton (M02).
- CI `standard-install` job (committed but commented out, pending the repo going public): remote-only install from the git ref (`pip install "git+<repo>@<ref>"`, no checkout) matrixed over Python 3.11–3.14 that imports every subpackage from a scratch dir, catching subpackages missing from the built distribution — or files left uncommitted — that the editable dev install would mask (M00).

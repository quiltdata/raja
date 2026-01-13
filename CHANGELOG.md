# Changelog

All notable changes to the RAJA project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial project structure with UV Python 3.12
- Comprehensive MVP specification documents in `specs/1-mvp/`:
  - `01-mvp-spec.md`: Core RAJA/SDA hypothesis and minimal viable product definition
  - `02-mvp-cdk.md`: AWS CDK Python and Amazon Verified Permissions integration analysis
  - `03-mvp-design.md`: Detailed implementation guide with repository layout and service architecture
- Project foundation:
  - `pyproject.toml`: UV project configuration
  - `.python-version`: Python 3.12 requirement
  - `src/raja/`: Public library package structure with type hints support

### Design Decisions
- Software-Defined Authorization (SDA) approach: Cedar policies compile to JWT tokens at control plane
- Pure subset checking for authorization enforcement at data plane (no runtime policy evaluation)
- Three-part architecture: `raja` library (pure Python), `tests` (unit/integration/hypothesis), `raja-poc` (CDK stack)
- AWS Verified Permissions used as control plane only (policy management, not runtime evaluation)

## [0.1.0] - 2026-01-13

### Project Initialized
- Repository created with Apache 2.0 license
- README with project description

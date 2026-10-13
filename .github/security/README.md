# Source security checks

The Security workflow runs on main pushes, pull requests, weekly, and manual dispatch. Checksum-pinned Gitleaks scans reachable Git history, and Trivy reports resolved source dependencies with current vulnerability data. High and critical dependency matches fail the job. Reports are retained for 14 days; secret values are fully redacted.

The fingerprint list contains only individually reviewed false positives from the 13 October 2026 audit: fixture/development keys, non-secret artifact identifiers, and documentation replacement examples. It is not a list of accepted live credentials. Pull requests use the base revision's reviewed fingerprint list, not their proposed list. Default scanner rules are loaded explicitly; repository ignore files and inline Gitleaks waivers cannot suppress a new finding.

New findings need review against the actual runtime/build path. Do not add a credential or vulnerable dependency exception simply to make a check pass. Nested cached source checkouts and dependency/build output are excluded from source dependency scans; canonical submodules are checked out and scanned. Image/host vulnerability scanning is a separate boundary.

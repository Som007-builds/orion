"""ML intelligence module.

Ownership split (build-responsibility §3):
  Dev 3 authors the implementations in this package.
  Dev 2 owns the contracts they must satisfy (see `app.schemas.indicator`).

These modules expose pure functions with typed inputs/outputs and perform no
direct SQLite writes.
"""
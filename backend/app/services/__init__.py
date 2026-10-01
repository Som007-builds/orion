"""Service layer.

Ordering note: services import schemas and db, never the reverse.
`app.ml` (Dev 3) imports schemas but never writes SQLite directly.
"""
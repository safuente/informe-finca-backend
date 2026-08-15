"""Report generation: order → pipeline → PDF → delivery.

The order of the files matters more than usual here:
  findings.py  — what the report *says* (the product; pure functions, unit-tested)
  pipeline.py  — what data it says it about (fetch + intersect + interpret)
  renderer.py  — how it looks (Jinja + WeasyPrint)
  tasks.py     — when it runs and what happens when it does not (Celery, retries, refunds)
"""

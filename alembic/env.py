"""Async-aware Alembic env.py."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings

# Import every domain's models so they register on Base.metadata before autogenerate.
from app.layers import models as _layers_models  # noqa: F401
from app.parcels import models as _parcels_models  # noqa: F401
from app.payments import models as _payments_models  # noqa: F401
from app.reports import models as _reports_models  # noqa: F401
from app.shared.base_model import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# Relations created by an extension rather than by us. Filled in from the live database
# in do_run_migrations; see the query there for why guessing names is not enough.
_extension_owned: set[str] = set()

EXTENSION_SCHEMAS = {"tiger", "tiger_data", "topology"}


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Keep everything PostGIS owns out of autogenerate.

    The postgis/postgis image installs postgis_tiger_geocoder and postgis_topology and
    puts their schemas on the search_path, so a naive autogenerate reflects ~36 tables it
    did not create and emits DROP statements for them. Losing the geocoder would be
    survivable; the point is that a routine `make migrate` must never propose it.
    """
    if getattr(object_, "schema", None) in EXTENSION_SCHEMAS:
        return False
    if type_ in {"table", "index"} and name in _extension_owned:
        return False
    if type_ == "index" and name.startswith("idx_") and "geom" in name:
        return False
    return True


def load_extension_owned(connection) -> None:
    """Names of relations that belong to an installed extension.

    Asked of pg_depend instead of hardcoded: the list depends on which extensions the
    image ships, and it already covers spatial_ref_sys without naming it.
    """
    rows = connection.exec_driver_sql(
        """
        SELECT c.relname
        FROM pg_depend d
        JOIN pg_class c ON c.oid = d.objid
        WHERE d.deptype = 'e'
          AND c.relkind IN ('r', 'v', 'm', 'i', 'S', 'p')
        """
    ).fetchall()
    _extension_owned.update(row[0] for row in rows)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        # Inside the transaction, never before it: any statement on a SQLAlchemy 2.0
        # Connection opens an implicit transaction, and then begin_transaction() is no
        # longer the outermost one — the migration runs, reports success and is rolled
        # back when the async connection closes.
        load_extension_owned(connection)
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

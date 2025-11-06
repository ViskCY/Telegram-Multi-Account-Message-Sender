"""Alembic environment configuration."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path
from typing import Any, Dict
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Ensure the application package is importable when migrations run
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import models so SQLModel metadata is populated
from app.models import *  # noqa: F401,F403
from app.services.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_database_url() -> str:
    """Retrieve the database URL from application settings or Alembic config."""
    try:
        return get_settings().database_url
    except Exception:
        return config.get_main_option("sqlalchemy.url")


def _configure_for_url(cfg_options: Dict[str, Any]) -> Dict[str, Any]:
    """Inject the runtime database URL into Alembic's configuration."""
    url = _get_database_url()
    config.set_main_option("sqlalchemy.url", url)
    cfg_options["sqlalchemy.url"] = url
    return cfg_options


target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    cfg_options: Dict[str, Any] = {}
    _configure_for_url(cfg_options)

    context.configure(
        url=cfg_options["sqlalchemy.url"],
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section) or {}
    _configure_for_url(configuration)

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""统一初始化/升级入口，接管旧库前核对固定初始结构。"""
from pathlib import Path
import re

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from migration_baseline import metadata as baseline_metadata


ROOT = Path(__file__).resolve().parent
BASELINE_REVISION = "0001_initial"
PREPARATION_REVISION = "0002_paper_preparation"


class SchemaMismatchError(RuntimeError):
    """未纳入版本管理的数据库与初始结构不匹配。"""


def migration_config(connection=None) -> Config:
    """使用绝对路径，测试可以传入独立连接。"""
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def normalize_default(value) -> str | None:
    """忽略 MySQL/SQLite 当前时间默认值的等价写法。"""
    if value is None:
        return None
    value = str(value).strip().lower()
    compact = re.sub(r"[\s()]", "", value)
    if compact in {"current_timestamp", "now"}:
        return "current_timestamp"
    return value


def schema_differences(connection, metadata=baseline_metadata) -> list:
    """比较表、列、类型、外键、唯一约束和索引，另核对主键及默认值。"""
    migration = MigrationContext.configure(connection, opts={"compare_type": True})
    differences = list(compare_metadata(migration, metadata))
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    for table in metadata.sorted_tables:
        if table.name not in existing:
            continue
        expected_pk = [column.name for column in table.primary_key]
        actual_pk = inspector.get_pk_constraint(table.name)["constrained_columns"]
        if expected_pk != actual_pk:
            differences.append(("primary_key", table.name, actual_pk, expected_pk))
        columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in columns:
                continue
            expected = None if column.server_default is None else column.server_default.arg
            actual = columns[column.name].get("default")
            if normalize_default(actual) != normalize_default(expected):
                differences.append(("server_default", table.name, column.name, actual, str(expected)))
    return differences


def initialize_database(engine) -> str:
    """空库升级；完整旧库登记初始版本后升级；不完整旧库拒绝自动接管。"""
    with engine.begin() as connection:
        config = migration_config(connection)
        migration = MigrationContext.configure(connection)
        versions = migration.get_current_heads()
        scripts = ScriptDirectory.from_config(config)
        # 在写入前确认所有已登记版本能在本项目中找到。
        for version in versions:
            scripts.get_revision(version)
        if not versions:
            tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
            if tables:
                adoption_revision = BASELINE_REVISION
                expected = baseline_metadata
                if "paper_preparation_jobs" in tables:
                    # 使用固定迁移重建结构，不能直接用不断变化的当前模型。
                    from sqlalchemy import MetaData, create_engine
                    temporary_engine = create_engine("sqlite://")
                    try:
                        with temporary_engine.begin() as temporary_connection:
                            command.upgrade(migration_config(temporary_connection), PREPARATION_REVISION)
                            expected = MetaData()
                            expected.reflect(bind=temporary_connection)
                            expected.remove(expected.tables["alembic_version"])
                    finally:
                        temporary_engine.dispose()
                    adoption_revision = PREPARATION_REVISION
                differences = schema_differences(connection, expected)
                if differences:
                    raise SchemaMismatchError(
                        "现有数据库与初始版本不一致，未登记版本，也未修改业务表。差异："
                        + repr(differences)
                    )
                # 只标记已核对的固定初始版本；不能直接 stamp head。
                command.stamp(config, adoption_revision)
        command.upgrade(config, "head")
        current = MigrationContext.configure(connection).get_current_heads()
        return ",".join(current)

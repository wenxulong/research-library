"""连接项目数据库，或使用测试传入的独立连接运行 Alembic。"""
from alembic import context
import research_library.db.model as model  # 注册模型；不能删除这个导入。
from research_library.db.database import Base, engine


target_metadata = Base.metadata


def run_with_connection(connection):
    """迁移和自动结构比较共用一个连接。"""
    context.configure(connection=connection, target_metadata=target_metadata,
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online():
    """优先复用调用者连接，避免测试误连正式数据库。"""
    connection = context.config.attributes.get("connection")
    if connection is not None:
        run_with_connection(connection)
        return
    with engine.connect() as connection:
        run_with_connection(connection)


def run_offline():
    """只输出 SQL，不执行数据库修改。"""
    context.configure(url=engine.url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"},
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()

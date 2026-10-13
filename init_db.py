"""统一数据库初始化、旧库接管和版本升级入口。"""
from database import engine
from db_migrations import initialize_database


def init_db():
    """按迁移版本管理表结构，不再用 create_all 猜测数据库状态。"""
    version = initialize_database(engine)
    print(f"数据库已就绪，迁移版本：{version}")


if __name__ == "__main__":
    init_db()

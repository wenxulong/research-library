"""旧命令兼容入口；数据库结构统一交给 Alembic。"""
from init_db import init_db


def main():
    """执行统一初始化/升级，不再独立修改某个表。"""
    init_db()


if __name__ == "__main__":
    main()

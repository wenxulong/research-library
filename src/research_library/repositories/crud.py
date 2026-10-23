"""封装论文数据的增删改查操作。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from research_library.db.model import Paper
from research_library.schemas import PaperCreate


def get_papers(db: Session) -> list[Paper]:
    """查询并返回全部论文。"""
    statement = select(Paper).order_by(Paper.id)
    return list(db.scalars(statement).all())


def get_paper_by_id(db: Session, paper_id: int) -> Paper | None:
    """根据编号查询论文，未找到时返回 None。"""
    return db.get(Paper, paper_id)


def create_paper(db: Session, paper_data: PaperCreate) -> Paper:
    """创建并返回一篇论文。"""
    new_paper = Paper(
        title=paper_data.title,
        author=paper_data.author,
        year=paper_data.year,
        notes=paper_data.notes,
    )

    db.add(new_paper)
    db.commit()
    db.refresh(new_paper)

    return new_paper


def update_paper(
    db: Session,
    paper: Paper,
    paper_data: PaperCreate,
) -> Paper:
    """修改论文并返回最新记录。"""
    paper.title = paper_data.title
    paper.author = paper_data.author
    paper.year = paper_data.year
    paper.notes = paper_data.notes

    db.commit()
    db.refresh(paper)

    return paper


def delete_paper(db: Session, paper: Paper) -> None:
    """删除指定论文及需要同步清理的关联资源。"""
    db.delete(paper)
    db.commit()

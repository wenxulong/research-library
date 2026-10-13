from database import SessionLocal
from chunk_crud import get_neighbor_chunks


def main():
    with SessionLocal() as db:
        chunks = get_neighbor_chunks(
            db=db,
            chunk_id=201,
            before=1,
            after=1,
        )

        for chunk in chunks:
            print(
                f"文本块编号：{chunk.id}，"
                f"PDF编号：{chunk.document_id}，"
                f"页码：{chunk.page_number}，"
                f"页内序号：{chunk.chunk_index}"
            )
            print(chunk.text)
            print("-" * 60)


if __name__ == "__main__":
    main()                         
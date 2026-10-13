"""负责验证、保存和删除磁盘上的 PDF 文件。"""

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from fastapi import UploadFile

from config import settings

UPLOAD_DIR = settings.upload_dir
MAX_FILE_SIZE = settings.max_upload_mb * 1024 * 1024
@dataclass(frozen=True)
class SavedPDF:
    """保存 PDF 写入磁盘后的文件信息。"""
    original_name: str
    stored_name: str
    file_path: str
    content_type: str
    file_size: int

async def save_pdf(upload: UploadFile) -> SavedPDF:
    """校验上传文件并以唯一文件名保存到磁盘。"""
    original_name = Path(upload.filename or "").name

    if not original_name:
        raise ValueError("没有提供文件名")

    if Path(original_name).suffix.lower() != ".pdf":
        raise ValueError("只能上传PDF文件")

    content = await upload.read(MAX_FILE_SIZE + 1)

    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"PDF文件不能超过{settings.max_upload_mb}MB")

    if not content.startswith(b"%PDF-"):
        raise ValueError("文件内容不是有效的PDF")

    UPLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    stored_name = f"{uuid4().hex}.pdf"
    file_path = UPLOAD_DIR / stored_name

    file_path.write_bytes(content)

    return SavedPDF(
        original_name=original_name,
        stored_name=stored_name,
        file_path=str(file_path),
        content_type=upload.content_type or "application/pdf",
        file_size=len(content),
    )


def delete_pdf(file_path: str) -> None:
    """删除指定路径的 PDF，文件不存在时忽略。"""
    path = Path(file_path)

    try:
        path.unlink()
    except FileNotFoundError:
        pass


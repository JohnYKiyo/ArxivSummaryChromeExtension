"""Output packaging tools.

Creates the final ZIP archive containing all conversion artifacts:
- paper_en.md (English Markdown)
- paper_ja.md (Japanese translated Markdown)
- summary_ja.md (Japanese summary)
- images/ (extracted figures and images)
"""

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


def create_zip_package(
    paper_en_md: str,
    paper_ja_md: str,
    summary_ja_md: str,
    image_paths: list[Path],
    work_dir: Path,
) -> Path:
    """Create a ZIP archive with all conversion outputs.

    The resulting ZIP has the following structure::

        paper_en.md
        paper_ja.md
        summary_ja.md
        images/
            figure1.png
            figure2.jpg
            ...

    Args:
        paper_en_md: English Markdown content of the paper.
        paper_ja_md: Japanese translated Markdown content of the paper.
        summary_ja_md: Japanese summary Markdown content.
        image_paths: List of image file paths to include.
        work_dir: Working directory where the ZIP file will be created.

    Returns:
        Path to the created ZIP file.
    """
    zip_path = work_dir / "output.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("paper_en.md", paper_en_md)
        zf.writestr("paper_ja.md", paper_ja_md)
        zf.writestr("summary_ja.md", summary_ja_md)

        for image_path in image_paths:
            if image_path.is_file():
                arcname = f"images/{image_path.name}"
                zf.write(image_path, arcname)
            else:
                logger.warning("Image file not found, skipping: %s", image_path)

    logger.info(
        "Created ZIP package: %s (%d bytes, %d images)",
        zip_path,
        zip_path.stat().st_size,
        len(image_paths),
    )
    return zip_path

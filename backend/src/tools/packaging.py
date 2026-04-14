"""Output packaging tools.

Creates the final ZIP archive containing all conversion artifacts:
- paper_en.md (English Markdown)
- paper_ja.md (Japanese translated Markdown)
- summary_ja.md (Japanese summary)
- images/ (extracted figures and images)

Supports uploading the ZIP to S3 and generating presigned download URLs.
"""

import logging
import zipfile
from pathlib import Path

import boto3
from botocore.config import Config

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


def upload_to_s3(
    zip_path: Path,
    job_id: str,
    bucket_name: str,
    presigned_url_expiry: int = 3600,
) -> str:
    """Upload a ZIP file to S3 and return a presigned download URL.

    Args:
        zip_path: Local path to the ZIP file.
        job_id: Job identifier used as the S3 key prefix.
        bucket_name: Target S3 bucket name.
        presigned_url_expiry: Presigned URL expiration in seconds.

    Returns:
        A presigned URL for downloading the uploaded ZIP.
    """
    s3_key = f"jobs/{job_id}/output.zip"

    s3_client = boto3.client("s3", config=Config(signature_version="s3v4"))
    s3_client.upload_file(
        str(zip_path),
        bucket_name,
        s3_key,
        ExtraArgs={"ContentType": "application/zip"},
    )

    presigned_url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": s3_key},
        ExpiresIn=presigned_url_expiry,
    )

    logger.info("Uploaded ZIP to s3://%s/%s", bucket_name, s3_key)
    return presigned_url

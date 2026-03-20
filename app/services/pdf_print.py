import base64
import io
import logging
from typing import Tuple

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def generate_petition_pdf(
    cover_bytes: bytes,
    petition_bytes: bytes,
    start_num: int,
    end_num: int,
) -> Tuple[bytes, int]:
    cover_doc = fitz.open(stream=cover_bytes, filetype="pdf")
    petition_doc = fitz.open(stream=petition_bytes, filetype="pdf")

    # First page of cover template, then all pages of petition template
    template_pages = [(cover_doc, 0)] + [(petition_doc, i) for i in range(len(petition_doc))]

    new_doc = fitz.open()

    for serial in range(start_num, end_num + 1):
        serial_text = f"#{serial:05d}"
        for src_doc, page_num in template_pages:
            src_page = src_doc[page_num]
            new_page = new_doc.new_page(width=src_page.rect.width, height=src_page.rect.height)
            new_page.show_pdf_page(new_page.rect, src_doc, page_num)
            new_page.insert_text(
                (50, src_page.rect.height - 30),
                serial_text,
                fontsize=12,
                color=(0, 0, 0),
                fontname="times-roman",
            )

    out = io.BytesIO()
    new_doc.save(out)
    cover_doc.close()
    petition_doc.close()
    new_doc.close()

    pdf_bytes = out.getvalue()
    expected_pages = (end_num - start_num + 1) * len(template_pages)

    # Integrity check: re-open and verify page count
    try:
        check_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        actual_pages = len(check_doc)
        check_doc.close()
    except Exception as e:
        raise ValueError(f"Generated PDF failed integrity check (could not be opened): {e}") from e

    if actual_pages != expected_pages:
        raise ValueError(
            f"Generated PDF has {actual_pages} pages but expected {expected_pages}. "
            "The output may be corrupt."
        )

    return pdf_bytes, expected_pages


def get_highest_printed() -> int:
    from app import db
    from app.models.print_job import PetitionPrintJob
    return db.session.query(db.func.max(PetitionPrintJob.end_number)).scalar() or 0


def get_template_config() -> dict:
    from app.models import Settings
    return {
        "has_cover": bool(Settings.get("petition_cover_pdf")),
        "cover_name": Settings.get("petition_cover_pdf_name", ""),
        "has_petition": bool(Settings.get("petition_page_pdf")),
        "petition_name": Settings.get("petition_page_pdf_name", ""),
    }


def get_cover_bytes() -> bytes | None:
    from app.models import Settings
    raw = Settings.get("petition_cover_pdf")
    return base64.b64decode(raw) if raw else None


def get_petition_bytes() -> bytes | None:
    from app.models import Settings
    raw = Settings.get("petition_page_pdf")
    return base64.b64decode(raw) if raw else None

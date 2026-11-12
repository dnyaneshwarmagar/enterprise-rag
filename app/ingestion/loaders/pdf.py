from pypdf import PdfReader

from app.observe import info, span, warning

_ocr_reason: str | None = None
_ocr_checked = False


def parse_pdf(file_path: str) -> tuple[list[tuple[int, str]], list[str]]:
    """Extract text page by page, in order.

    A blank pypdf page is retried with pdfplumber, then with local OCR.
    Returns (pages, warnings). Empty pages are omitted from the page list
    and described in warnings.
    """
    with span("PDF Parsing (local)", filename=file_path):
        reader = PdfReader(file_path)
        total_pages = len(reader.pages)
        info(f"PDF has {total_pages} pages.")

        plumber_text = _plumber_pages(file_path, total_pages)
        pages: list[tuple[int, str]] = []
        warnings: list[str] = []
        empty_pages: list[int] = []

        for index, page in enumerate(reader.pages):
            page_number = index + 1
            text = (page.extract_text() or "").strip()
            if not text:
                text = (plumber_text.get(page_number) or "").strip()
            if not text:
                text, ocr_warning = _ocr_page(file_path, index)
                if ocr_warning and ocr_warning not in warnings:
                    warnings.append(ocr_warning)
            if text:
                pages.append((page_number, text))
            else:
                empty_pages.append(page_number)

        if empty_pages:
            warnings.append(f"No text on pages {empty_pages}.")
            warning(f"No text on pages {empty_pages} of {file_path}.")
        else:
            characters = sum(len(text) for _, text in pages)
            info(f"Extracted {characters} characters from {file_path}.")

        return pages, warnings


def _plumber_pages(file_path: str, total_pages: int) -> dict[int, str]:
    """Read every page with pdfplumber once, keyed by 1-based page number."""
    try:
        import pdfplumber
    except ImportError:
        return {}

    extracted: dict[int, str] = {}
    try:
        with pdfplumber.open(file_path) as pdf:
            for index in range(min(total_pages, len(pdf.pages))):
                extracted[index + 1] = pdf.pages[index].extract_text() or ""
    except Exception as exc:
        warning(f"pdfplumber fallback failed: {exc}")
    return extracted


def _ocr_page(file_path: str, page_index: int) -> tuple[str, str | None]:
    """OCR one scanned page. The warning is None when OCR ran or was not needed."""
    global _ocr_checked, _ocr_reason
    if not _ocr_checked:
        _ocr_checked = True
        _ocr_reason = _ocr_unavailable_reason()

    if _ocr_reason:
        return "", _ocr_reason

    try:
        import pypdfium2 as pdfium
        import pytesseract

        document = pdfium.PdfDocument(file_path)
        try:
            page = document[page_index]
            bitmap = page.render(scale=2)
            image = bitmap.to_pil()
            text = pytesseract.image_to_string(image) or ""
        finally:
            document.close()
        return text.strip(), None
    except Exception as exc:
        return "", f"OCR failed on page {page_index + 1}: {exc}"


def _ocr_unavailable_reason() -> str | None:
    try:
        import pytesseract
    except ImportError:
        return "Tesseract OCR is not installed; scanned pages were left empty."
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return "Tesseract OCR is not installed; scanned pages were left empty."
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return "pypdfium2 is not installed; scanned pages were left empty."
    return None

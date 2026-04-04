"""
api/pdf_extractor.py
Extract individual policy clauses from an uploaded PDF.

Strategy:
  1. Use pdfplumber to extract raw text page by page.
  2. Split into sentences / logical blocks.
  3. Apply heuristics to detect numbered / bulleted clauses.
  4. Return a clean list of clause strings.
"""

import re
from typing import IO


def _split_clauses(text: str) -> list[str]:
    """
    Split a block of text into individual clauses using a hierarchy of signals:
      - Numbered items (1. / 1) / (1) / Article 1)
      - Lettered items (a. / a) / (a))
      - All-caps headings followed by text
      - Paragraph breaks (double newline)
      - Long sentence boundaries as last resort
    """
    # Normalise whitespace
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Try numbered clause detection first
    numbered = re.split(
        r"\n(?=\s*(?:\d{1,3}[\.\)]\s|\([a-zA-Z0-9]{1,3}\)\s|Article\s+\d|Section\s+\d))",
        text, flags=re.IGNORECASE
    )
    if len(numbered) > 2:
        clauses = [c.strip() for c in numbered if len(c.strip()) > 30]
        if clauses:
            return clauses

    # Fall back to paragraph breaks
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 30]
    if len(paragraphs) > 1:
        return paragraphs

    # Last resort: sentence splitting
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [s.strip() for s in sentences if len(s.strip()) > 30]


def extract_clauses_from_pdf(file_obj: IO[bytes]) -> list[str]:
    """
    Given a file-like object for a PDF, return a list of clause strings.
    Raises ImportError if pdfplumber is not installed.
    Raises ValueError if no usable text is found.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError(
            "pdfplumber is required for PDF extraction. "
            "Install it with: pip install pdfplumber"
        ) from exc

    full_text = []
    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                full_text.append(page_text)

    if not full_text:
        raise ValueError("No extractable text found in the PDF. "
                         "Scanned PDFs require OCR — please use a text-based PDF.")

    combined = "\n\n".join(full_text)
    clauses  = _split_clauses(combined)

    if not clauses:
        raise ValueError("Could not identify individual clauses in the PDF.")

    # Cap at 100 clauses to avoid runaway Gemini calls
    return clauses[:100]


def extract_clauses_from_text(raw: str) -> list[str]:
    """Convenience wrapper for plain-text input (non-PDF path)."""
    if not raw.strip():
        raise ValueError("Empty text provided.")
    clauses = _split_clauses(raw)
    if not clauses:
        raise ValueError("Could not identify clauses in the provided text.")
    return clauses[:100]

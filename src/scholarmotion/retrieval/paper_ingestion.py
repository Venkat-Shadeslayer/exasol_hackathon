from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader


@dataclass
class ParsedPaperChunk:
    page: int
    section: str | None
    content_type: str
    text: str
    label: str | None = None


@dataclass
class ParsedPaper:
    title: str
    authors: list[str]
    abstract: str | None
    chunks: list[ParsedPaperChunk]
    metadata: dict = field(default_factory=dict)


class PaperParser:
    heading = re.compile(r"^(?:(\d+(?:\.\d+)*)\s+)?([A-Z][A-Za-z0-9 ,:&()\-/]{2,80})$")
    caption = re.compile(r"^(Figure|Fig\.|Table)\s*(\d+[A-Za-z]?)\s*[:.\-]?\s*(.+)", re.IGNORECASE)
    equation_label = re.compile(r"(?:\((\d+)\)|Equation\s+(\d+))\s*$", re.IGNORECASE)

    def parse(self, path: str | Path, *, asset_dir: str | Path | None = None) -> ParsedPaper:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        assets: list[dict] = []
        if asset_dir is not None:
            output = Path(asset_dir)
            output.mkdir(parents=True, exist_ok=True)
            for page_number, page in enumerate(reader.pages, 1):
                for image_index, image in enumerate(getattr(page, "images", []), 1):
                    original_name = Path(getattr(image, "name", "image.bin")).name
                    suffix = Path(original_name).suffix or ".bin"
                    target = output / f"page-{page_number:04d}-image-{image_index:03d}{suffix}"
                    target.write_bytes(image.data)
                    assets.append(
                        {
                            "page": page_number,
                            "original_name": original_name,
                            "path": str(target),
                        }
                    )
        first_lines = [
            line.strip()
            for line in (pages[0] if pages else Path(path).stem).splitlines()
            if line.strip()
        ]
        title = first_lines[0] if first_lines else Path(path).stem
        authors = [
            item.strip()
            for item in re.split(r",|\band\b", first_lines[1] if len(first_lines) > 1 else "")
            if item.strip()
        ]
        chunks: list[ParsedPaperChunk] = []
        current_section: str | None = None
        abstract: str | None = None
        for page_number, text in enumerate(pages, 1):
            # Preserve numbered section lines as standalone blocks before normalizing
            # PDF line wrapping into paragraphs.
            text = re.sub(
                r"(?m)^(\d+(?:\.\d+)*)\s+([A-Z][^\n]{1,80})$",
                r"\n\n\1 \2\n\n",
                text,
            )
            paragraphs = re.split(r"\n\s*\n|(?<=\.)\s*\n", text)
            for raw in paragraphs:
                paragraph = " ".join(raw.split())
                if not paragraph:
                    continue
                caption = self.caption.match(paragraph)
                if caption:
                    kind = "figure" if caption.group(1).lower().startswith("fig") else "table"
                    chunks.append(
                        ParsedPaperChunk(
                            page_number, current_section, kind, paragraph, caption.group(2)
                        )
                    )
                    continue
                heading = self.heading.match(paragraph)
                if heading and len(paragraph.split()) <= 12:
                    current_section = paragraph
                    chunks.append(
                        ParsedPaperChunk(page_number, current_section, "section", paragraph)
                    )
                    continue
                eq = self.equation_label.search(paragraph)
                if eq:
                    chunks.append(
                        ParsedPaperChunk(
                            page_number,
                            current_section,
                            "equation",
                            paragraph,
                            eq.group(1) or eq.group(2),
                        )
                    )
                    continue
                if current_section and "abstract" in current_section.lower() and abstract is None:
                    abstract = paragraph
                chunks.append(
                    ParsedPaperChunk(page_number, current_section, "paragraph", paragraph)
                )
        return ParsedPaper(
            title,
            authors,
            abstract,
            chunks,
            {"pages": len(pages), "extracted_figure_assets": assets},
        )

    @staticmethod
    def select_reference(paper: ParsedPaper, query: str) -> list[ParsedPaperChunk]:
        equation = re.search(r"equation\s+(\d+)", query, re.IGNORECASE)
        figure_expression = re.search(
            r"(?:figures?|figs?\.?)\s+((?:\d+[A-Za-z]?\s*(?:(?:,|and)\s*)?)+)",
            query,
            re.IGNORECASE,
        )
        figures = (
            re.findall(r"\d+[A-Za-z]?", figure_expression.group(1)) if figure_expression else []
        )
        section = re.search(r"section\s+([\d.]+)", query, re.IGNORECASE)
        if equation:
            return [
                chunk
                for chunk in paper.chunks
                if chunk.content_type == "equation" and chunk.label == equation.group(1)
            ]
        if figures:
            return [
                chunk
                for chunk in paper.chunks
                if chunk.content_type == "figure" and chunk.label in figures
            ]
        if section:
            return [
                chunk
                for chunk in paper.chunks
                if chunk.section and chunk.section.startswith(section.group(1))
            ]
        return paper.chunks

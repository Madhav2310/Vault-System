"""Text inside tracked changes and content controls is extracted."""

import io
import pathlib
import zipfile

import docx

from app.ingestion.extract.docx import DocxExtractor

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _patched(tmp: pathlib.Path, body_xml: str) -> str:
    buf = io.BytesIO()
    docx.Document().save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = tmp / "t.docx"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "word/document.xml":
                head, _, tail = data.decode().partition("<w:body>")
                _, _, sect = tail.partition("<w:sectPr")
                data = f"{head}<w:body>{body_xml}<w:sectPr{sect}".encode()
            z.writestr(item, data)
    return str(out)


def test_tracked_changes_and_content_controls_are_extracted(tmp_path: pathlib.Path) -> None:
    body = (
        f"<w:p {NS}><w:r><w:t>Plain start. </w:t></w:r>"
        '<w:ins w:id="1" w:author="a"><w:r><w:t>Inserted tracked text. </w:t></w:r></w:ins>'
        '<w:del w:id="2" w:author="a"><w:r><w:delText>Deleted words.</w:delText></w:r></w:del>'
        "<w:hyperlink><w:r><w:t>Linked text.</w:t></w:r></w:hyperlink></w:p>"
        f"<w:sdt {NS}><w:sdtContent>"
        "<w:p><w:r><w:t>Inside content control.</w:t></w:r></w:p></w:sdtContent></w:sdt>"
        f"<w:tbl {NS}><w:tr><w:tc>"
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>Cell control</w:t></w:r></w:p></w:sdtContent></w:sdt></w:tc>"
        '<w:tc><w:p><w:ins w:id="3" w:author="a"><w:r><w:t>Cell inserted</w:t></w:r></w:ins></w:p></w:tc>'
        "</w:tr></w:tbl>"
    )
    doc = DocxExtractor().extract(_patched(tmp_path, body))
    doc.check_offsets()
    text = doc.pages[0].text
    for must in (
        "Plain start.",
        "Inserted tracked text.",
        "Linked text.",
        "Inside content control.",
        "Cell control",
        "Cell inserted",
    ):
        assert must in text, must
    assert "Deleted words" not in text  # tracked deletions are not visible text

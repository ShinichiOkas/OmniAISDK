"""テスト用の文書ファイルを生成する（生成物もコミットする。再生成: python tests/fixtures/docs/make_fixtures.py）。"""

import io
import zipfile
from pathlib import Path

from openpyxl import Workbook
from pptx import Presentation
from pypdf import PdfWriter

OUT = Path(__file__).parent


def pdf_with_text(lines: list[str]) -> bytes:
    """Helvetica で ASCII のテキストを書いた最小の PDF（2 ページ）。"""
    objs: list[bytes] = []
    pages = []
    for i, line in enumerate(lines):
        stream = f"BT /F1 18 Tf 72 720 Td ({line}) Tj ET".encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        pages.append(len(objs))  # content obj number (1-based later)
    # 番号: 1..n content, n+1 font, n+2.. pages, last-1 pages tree, last catalog
    n = len(objs)
    font_no = n + 1
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_nos = []
    tree_no = n + 2 + len(lines)
    for c in pages:
        objs.append(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (tree_no, c, font_no))
        page_nos.append(len(objs))
    kids = b" ".join(b"%d 0 R" % p for p in page_nos)
    objs.append(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_nos)))
    objs.append(b"<< /Type /Catalog /Pages %d 0 R >>" % tree_no)
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, len(objs), xref))
    return out.getvalue()


def main() -> None:
    (OUT / "text.pdf").write_bytes(pdf_with_text(["OmniAI SDK page one", "Second page about cats"]))

    blank = PdfWriter()
    blank.add_blank_page(width=612, height=792)
    with (OUT / "image_only.pdf").open("wb") as f:
        blank.write(f)

    enc = PdfWriter(clone_from=io.BytesIO(pdf_with_text(["secret text"])))
    enc.encrypt(user_password="pw", owner_password="owner")
    with (OUT / "encrypted.pdf").open("wb") as f:
        enc.write(f)

    wb = Workbook()
    ws = wb.active
    ws.title = "売上"
    ws.append(["商品", "数量"])
    ws.append(["りんご", 3])
    ws.append(["みかん", 5])
    wb.save(OUT / "table.xlsx")

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "猫の睡眠"
    slide.placeholders[1].text = "猫は1日に12〜16時間眠る"
    prs.save(OUT / "slides.pptx")

    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:r><w:t>窓口の受付は9時から</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>OmniAI SDK の文書解析テスト</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(OUT / "memo.docx", "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   "</Relationships>")
        z.writestr("word/document.xml", doc_xml)
    print("ok:", sorted(p.name for p in OUT.iterdir() if p.suffix != ".py"))


if __name__ == "__main__":
    main()

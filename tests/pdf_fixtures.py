"""含 Unicode 映射的虚构最小 PDF，使用实际 pypdf 解析器验证获取链路。"""

import io

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)


def unicode_pdf(text):
    w = PdfWriter()
    p = w.add_blank_page(width=600, height=800)
    cmap = DecodedStreamObject()
    cmap.set_data(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
        b"/CMapName /Identity-UCS def /CMapType 2 def 1 begincodespacerange <0000> "
        b"<FFFF> endcodespacerange 1 beginbfrange <0000> <FFFF> <0000> endbfrange "
        b"endcmap CMapName currentdict /CMap defineresource pop end end "
    )
    cid = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType2"),
            NameObject("/BaseFont"): NameObject("/Fixture"),
            NameObject("/CIDSystemInfo"): DictionaryObject(
                {
                    NameObject("/Registry"): TextStringObject("Adobe"),
                    NameObject("/Ordering"): TextStringObject("Identity"),
                    NameObject("/Supplement"): NumberObject(0),
                }
            ),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/Fixture"),
            NameObject("/Encoding"): NameObject("/Identity-H"),
            NameObject("/DescendantFonts"): ArrayObject([w._add_object(cid)]),
            NameObject("/ToUnicode"): w._add_object(cmap),
        }
    )
    p[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(
        ("BT /F1 12 Tf 10 700 Td <" + text.encode("utf-16-be").hex() + "> Tj ET").encode()
    )
    p[NameObject("/Contents")] = w._add_object(content)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()

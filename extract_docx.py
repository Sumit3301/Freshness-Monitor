import docx
from docx import Document
from lxml import etree
import json

doc = Document(r'C:\Users\ACER\Downloads\Manuscript Food chemistry.docx')

# Get namespaces
nsmap = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
    'w15': 'http://schemas.microsoft.com/office/word/2012/wordml',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}

# Check for comments part
print("=== DOCUMENT PARTS ===")
for rel in doc.part.rels.values():
    print(f"  {rel.reltype} -> {rel.target_ref}")

# Check for comments in the XML
body = doc.element.body
xml_str = etree.tostring(body, encoding='unicode')

# Look for various annotation types
import re
patterns = [
    ('commentRangeStart', r'commentRangeStart'),
    ('commentRangeEnd', r'commentRangeEnd'),
    ('commentReference', r'commentReference'),
    ('ins', r'<w:ins '),
    ('del', r'<w:del '),
    ('rPrChange', r'rPrChange'),
    ('highlight', r'w:highlight'),
    ('color_red', r'val="FF0000"'),
    ('color_red2', r'val="ff0000"'),
    ('strikethrough', r'w:strike'),
    ('annotation', r'annotation'),
]

print("\n=== ANNOTATION PATTERNS ===")
for name, pattern in patterns:
    count = len(re.findall(pattern, xml_str))
    print(f"  {name}: {count}")

# Extract text with formatting info to identify "comments" (colored/highlighted text)
print("\n=== DOCUMENT TEXT WITH FORMATTING ===")
para_num = 0
for para in doc.paragraphs:
    para_num += 1
    text = para.text.strip()
    if not text:
        continue
    
    # Check runs for special formatting (red text, highlights, etc.)
    has_special = False
    for run in para.runs:
        if run.font.color and run.font.color.rgb:
            color = str(run.font.color.rgb)
            if color in ['FF0000', 'ff0000', 'FF0000', 'ED7D31', '0000FF']:
                has_special = True
                break
        if run.font.highlight_color:
            has_special = True
            break
    
    if has_special:
        print(f"\n--- PARA {para_num} (HAS SPECIAL FORMATTING) ---")
        for run in para.runs:
            color_info = ""
            if run.font.color and run.font.color.rgb:
                color_info = f" [COLOR: {run.font.color.rgb}]"
            highlight_info = ""
            if run.font.highlight_color:
                highlight_info = f" [HIGHLIGHT: {run.font.highlight_color}]"
            strike_info = ""
            if run.font.strike:
                strike_info = " [STRIKETHROUGH]"
            if run.text.strip():
                print(f"  RUN: '{run.text}'{color_info}{highlight_info}{strike_info}")
    else:
        # Print first 100 chars of normal paragraphs
        print(f"PARA {para_num}: {text[:150]}{'...' if len(text) > 150 else ''}")

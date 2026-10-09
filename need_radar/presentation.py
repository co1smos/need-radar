import hashlib
import html
import re
from urllib.parse import urlsplit


RENDERER_VERSION = "need-radar-markdown-v1"
STYLESHEET = """\
:root { color: #202833; background: #eef2f6; font-family: system-ui, sans-serif; }
body { margin: 0; padding: 2rem 1rem; line-height: 1.6; }
main { box-sizing: border-box; max-width: 56rem; margin: 0 auto; padding: 2rem 3rem; background: #fff; }
h1, h2, h3, h4, h5, h6 { color: #13253a; line-height: 1.25; }
h1 { border-bottom: 2px solid #d8e2ed; padding-bottom: 0.6rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid #e4eaf0; padding-bottom: 0.3rem; }
a { color: #0758a5; overflow-wrap: anywhere; }
blockquote { margin: 1rem 0; padding: 0.2rem 1rem; border-left: 0.25rem solid #78a9d4; background: #f4f8fc; overflow-wrap: anywhere; }
p, li { overflow-wrap: anywhere; }
@media (max-width: 40rem) { body { padding: 0; } main { padding: 1.25rem; } }
"""
LINK_PATTERN = re.compile(r"(?<![!\\])\[([^\]\n]+)\]\(([^)\s]+)\)")
ESCAPED_MARKDOWN = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|~:])")


def _text(value):
    value = ESCAPED_MARKDOWN.sub(r"\1", value)
    return html.escape(html.unescape(value), quote=False)


def _safe_href(value):
    value = html.unescape(ESCAPED_MARKDOWN.sub(r"\1", value))
    if not value or any(character.isspace() or ord(character) < 0x20 for character in value):
        return None
    if value.startswith("#"):
        return value if len(value) > 1 else None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None and parsed.password is None:
        return value
    return None


def _inline(value):
    result = []
    start = 0
    for match in LINK_PATTERN.finditer(value):
        result.append(_text(value[start:match.start()]))
        label, target = match.groups()
        href = _safe_href(target)
        if href is None:
            result.append(_text(label))
        else:
            result.append(f'<a href="{html.escape(href, quote=True)}">{_text(label)}</a>')
        start = match.end()
    result.append(_text(value[start:]))
    return "".join(result)


def _render_blocks(markdown):
    blocks, paragraphs, quotes, list_items = [], [], [], []
    seen_headings = {}

    def flush_paragraph():
        if paragraphs:
            blocks.append(f"<p>{_inline(' '.join(paragraphs))}</p>")
            paragraphs.clear()

    def flush_quotes():
        if quotes:
            blocks.append("<blockquote>" + "".join(f"<p>{_inline(line)}</p>" for line in quotes) + "</blockquote>")
            quotes.clear()

    def flush_list():
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{_inline(line)}</li>" for line in list_items) + "</ul>")
            list_items.clear()

    for line in markdown.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        list_item = re.match(r"^\s*[-*+]\s+(.+)$", line)
        if heading:
            flush_paragraph()
            flush_quotes()
            flush_list()
            level, label = len(heading.group(1)), heading.group(2)
            slug = re.sub(r"[^\w-]+", "-", ESCAPED_MARKDOWN.sub(r"\1", label).strip().lower()).strip("-") or "section"
            seen_headings[slug] = seen_headings.get(slug, 0) + 1
            identifier = slug if seen_headings[slug] == 1 else f"{slug}-{seen_headings[slug]}"
            blocks.append(f'<h{level} id="{html.escape(identifier, quote=True)}">{_inline(label)}</h{level}>')
        elif line.startswith(">"):
            flush_paragraph()
            flush_list()
            quotes.append(line[1:].lstrip())
        elif list_item:
            flush_paragraph()
            flush_quotes()
            list_items.append(list_item.group(1))
        elif not line.strip():
            flush_paragraph()
            flush_quotes()
            flush_list()
        else:
            flush_quotes()
            flush_list()
            paragraphs.append(line.strip())

    flush_paragraph()
    flush_quotes()
    flush_list()
    return "\n".join(blocks)


def render_markdown_html(markdown, source_hash=None):
    """Render the canonical report Markdown subset as deterministic standalone HTML."""
    # shortcut: render the canonical report dialect only; expand syntax support when reports use it.
    if not isinstance(markdown, str):
        raise TypeError("canonical Markdown must be text")
    if source_hash is None:
        source_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    elif not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ValueError("source report hash must be a SHA-256 hex digest")
    title_match = re.search(r"^#\s+(.+?)\s*#*\s*$", markdown, re.MULTILINE)
    title = _text(title_match.group(1)) if title_match else "Need Radar report"
    body = _render_blocks(markdown)
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="need-radar-source-sha256" content="{source_hash}">\n'
        f'<title>{title}</title>\n<style>\n{STYLESHEET}</style>\n</head>\n<body>\n'
        f"<main>\n{body}\n</main>\n</body>\n</html>\n"
    )

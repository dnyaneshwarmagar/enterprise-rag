from bs4 import BeautifulSoup

from app.observe import error, span


def parse_html(file_path: str):
    """
    Parses HTML content using BeautifulSoup.
    Cleans scripts, styles, and extracts readable text for RAG.
    """
    with span("📄 HTML Parsing", filename=file_path):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            soup = BeautifulSoup(content, "html.parser")

            # 1. Remove Junk (Scripts, Styles, Metadata)
            for script in soup(["script", "style", "meta", "noscript"]):
                script.decompose()

            # Keep block boundaries so the chunker can split on paragraphs.
            for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "tr", "br"]):
                tag.insert_after("\n")

            # 2. Extract Text
            text = soup.get_text(separator="\n")

            # 3. Clean Whitespace (Collapse multiple newlines)
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text_clean = "\n".join(chunk for chunk in chunks if chunk)

            return text_clean
        except Exception as e:
            error(f"❌ HTML Parse Failed: {e}")
            raise e

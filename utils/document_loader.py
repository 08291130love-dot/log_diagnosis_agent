import io


def read_uploaded_documents(uploaded_files) -> dict[str, str]:
    documents = {}
    for uploaded in uploaded_files:
        raw = uploaded.getvalue()
        if uploaded.name.lower().endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            text = raw.decode("utf-8-sig", errors="replace")
        if text.strip():
            documents[uploaded.name] = text
    return documents

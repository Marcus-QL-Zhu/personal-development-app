class DocumentSummarizer:
    def summarize(self, document_text: str) -> str:
        text = document_text.strip()
        return text if len(text) <= 120 else text[:117] + "..."


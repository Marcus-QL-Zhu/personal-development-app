class DocumentProgress:
    def should_report_progress(self, elapsed_seconds: int) -> bool:
        return elapsed_seconds > 30


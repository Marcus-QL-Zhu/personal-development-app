def test_progress_is_reported_only_after_30_seconds(progress_helper):
    assert progress_helper.should_report_progress(elapsed_seconds=29) is False
    assert progress_helper.should_report_progress(elapsed_seconds=31) is True


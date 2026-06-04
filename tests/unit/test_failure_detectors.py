from lib.observability.failure_detectors import FailureDetectors


def test_sentinel_pattern_detection():
    detector = FailureDetectors()

    # Normal text should not trip
    assert detector.check_llm_output("sess-1", "This is normal text.") is None

    # Trip on sentinel
    assert detector.check_llm_output("sess-1", "I apologize for the confusion.") == "F-SENTINEL"
    assert (
        detector.check_llm_output("sess-1", "As an AI language model, I cannot do that.")
        == "F-SENTINEL"
    )


def test_text_only_loop_detection():
    detector = FailureDetectors(threshold=3)

    # Identical responses across consecutive turns
    assert detector.check_llm_output("sess-2", "Same response") is None
    assert detector.check_llm_output("sess-2", "Same response") is None
    assert detector.check_llm_output("sess-2", "Same response") == "F-LOOP-TEXT"


def test_self_repetition_internal_loop():
    detector = FailureDetectors(threshold=3)

    # single response with internal sentence repetition loop
    repeated_sentence = "This is a repeated sentence that goes on and on"
    loop_text = ". ".join([repeated_sentence] * 6)

    assert detector.check_llm_output("sess-3", loop_text) == "F-LOOP-TEXT"


def test_reset_detector():
    detector = FailureDetectors(threshold=3)
    assert detector.check_llm_output("sess-4", "Same response") is None
    assert detector.check_llm_output("sess-4", "Same response") is None

    detector.reset("sess-4")

    # Counter reset, so 3rd consecutive check won't fire immediately
    assert detector.check_llm_output("sess-4", "Same response") is None

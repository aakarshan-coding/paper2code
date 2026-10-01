from paper2code.sandbox.runner import LocalTestRunner


def test_reference_passes_public_and_hidden(canary_dir):
    runner = LocalTestRunner(timeout_s=120)
    public = runner.run(canary_dir / "reference", canary_dir / "scope" / "tests" / "public")
    hidden = runner.run(canary_dir / "reference", canary_dir / "scope" / "tests" / "hidden")
    assert public.all_passed, public.output
    assert hidden.all_passed, hidden.output
    assert len(public.passed) == 7
    assert len(hidden.passed) == 5


def test_hardcoded_passes_public_but_fails_hidden(canary_dir):
    runner = LocalTestRunner(timeout_s=120)
    public = runner.run(canary_dir / "hardcoded", canary_dir / "scope" / "tests" / "public")
    hidden = runner.run(canary_dir / "hardcoded", canary_dir / "scope" / "tests" / "hidden")
    assert public.all_passed, public.output
    assert not hidden.all_passed
    assert len(hidden.failed) == 5

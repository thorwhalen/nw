"""The validation seam: selecting, ordering, running, and reporting honestly.

Most of what is pinned here is about the *distinctions* — between "found
nothing" and "never ran", between a warning and a failure, between a check the
user picked and one that got pulled in as a requirement. Blurring any of them
gives you a suite that reports all clear on a machine where half of it was
never installed, which is worse than having no suite at all.
"""

import pytest

from nw.validation import (
    Check,
    Finding,
    ValidationError,
    ValidationReport,
    checks as registry,
    menu,
    plan_checks,
    register_check,
    resolve_checks,
    suggest,
    validate,
)


@pytest.fixture
def clean_registry(monkeypatch):
    """A registry with only what a test puts in it."""
    from xdol import Registry

    fresh = Registry(name="test.checks", on_conflict="error")
    monkeypatch.setattr("nw.validation.checks", fresh)
    return fresh


def _check(name, *, findings=(), requires=(), **kw):
    return Check(
        name=name,
        summary=kw.pop("summary", f"the {name} check"),
        run=kw.pop("run", lambda target, ctx: list(findings)),
        requires=tuple(requires),
        **kw,
    )


def _finding(check="c", severity="error", **kw):
    return Finding(check=check, severity=severity, message="something", **kw)


# --- nothing is run unless it is asked for -----------------------------------


def test_validate_with_no_selection_runs_nothing():
    """Validation is placed, never assumed — a gate you did not ask for fires
    at the wrong moment, and on someone else's budget."""
    report = validate("anything.mp4")
    assert report.results == () and report.ok


def test_an_unknown_check_name_raises_rather_than_being_skipped():
    with pytest.raises(KeyError):
        validate("x.mp4", checks=["media.no_such_check"])


# --- ordering and dependencies -----------------------------------------------


def test_a_requirement_is_pulled_in_without_being_asked_for(clean_registry):
    register_check(_check("base"))
    register_check(_check("derived", requires=["base"]))
    names = {c.name for c in resolve_checks(["derived"])}
    assert names == {"base", "derived"}


def test_requirements_run_first(clean_registry):
    register_check(_check("base"))
    register_check(_check("derived", requires=["base"]))
    waves = plan_checks(["derived"])
    assert [c.name for w in waves for c in w] == ["base", "derived"]


def test_independent_checks_share_a_wave(clean_registry):
    for n in ("a", "b", "c"):
        register_check(_check(n))
    assert len(plan_checks(["a", "b", "c"])) == 1


def test_a_check_that_is_not_parallel_safe_gets_a_wave_to_itself(clean_registry):
    register_check(_check("a"))
    register_check(_check("solo", parallel_safe=False))
    waves = plan_checks(["a", "solo"])
    assert (len(w) for w in waves)
    assert any(len(w) == 1 and w[0].name == "solo" for w in waves)


def test_a_dependency_cycle_is_caught_at_plan_time(clean_registry):
    register_check(_check("a", requires=["b"]))
    register_check(_check("b", requires=["a"]))
    with pytest.raises(ValueError, match="cycle"):
        plan_checks(["a"])


def test_what_a_requirement_produced_reaches_its_dependent(clean_registry):
    seen = {}
    register_check(_check("probe", run=lambda t, ctx: ([], {"duration": 42})))
    register_check(
        _check(
            "uses",
            requires=["probe"],
            run=lambda t, ctx: (seen.update(ctx), []) and [],
        )
    )
    validate("x", checks=["uses"])
    assert seen == {"probe": {"duration": 42}}


# --- reporting ---------------------------------------------------------------


def test_a_warning_does_not_fail_the_report(clean_registry):
    register_check(_check("w", findings=[_finding(severity="warn")]))
    assert validate("x", checks=["w"]).ok


def test_an_error_fails_the_report(clean_registry):
    register_check(_check("e", findings=[_finding(severity="error")]))
    assert not validate("x", checks=["e"]).ok


def test_a_check_that_raised_is_not_a_pass(clean_registry):
    """The distinction this module exists for: could-not-run is not all-clear."""

    def boom(target, ctx):
        raise RuntimeError("no codec")

    register_check(_check("b", run=boom))
    report = validate("x", checks=["b"])
    assert not report.ok
    assert "no codec" in report.results[0].error
    assert report.results[0].findings == ()


def test_one_broken_check_does_not_hide_the_others(clean_registry):
    def boom(target, ctx):
        raise RuntimeError("boom")

    register_check(_check("broken", run=boom))
    register_check(_check("works", findings=[_finding(severity="warn")]))
    report = validate("x", checks=["broken", "works"])
    assert len(report.findings) == 1  # the working one still reported


def test_on_error_raise_is_available_for_developing_a_check(clean_registry):
    def boom(target, ctx):
        raise RuntimeError("boom")

    register_check(_check("b", run=boom))
    with pytest.raises(RuntimeError):
        validate("x", checks=["b"], on_error="raise")


def test_a_missing_binary_skips_with_a_reason_and_does_not_pretend_to_pass(
    clean_registry,
):
    register_check(_check("needs", requires_binaries=("definitely-not-a-program",)))
    result = validate("x", checks=["needs"]).results[0]
    assert not result.ran
    assert "definitely-not-a-program" in result.skipped


def test_raise_if_failed_is_the_hard_gate(clean_registry):
    register_check(_check("e", findings=[_finding(severity="error")]))
    with pytest.raises(ValidationError):
        validate("x", checks=["e"]).raise_if_failed()


def test_a_finding_rejects_an_unknown_severity():
    with pytest.raises(ValueError, match="severity"):
        Finding(check="c", severity="catastrophic", message="m")


def test_the_summary_names_the_check_the_place_and_the_remedy(clean_registry):
    register_check(
        _check(
            "e",
            findings=[
                _finding(where="12.4s", remedy="re-render the shot", severity="error")
            ],
        )
    )
    text = validate("film.mp4", checks=["e"]).summary()
    assert "FAIL" in text and "12.4s" in text and "re-render the shot" in text


# --- declaring a check -------------------------------------------------------


def test_a_check_without_a_summary_is_refused():
    """An unexplained item on a menu will not be chosen, and cannot be chosen
    *for* a user by anything reading the menu."""
    with pytest.raises(ValueError, match="summary"):
        Check(name="x", summary="", run=lambda t, c: [])


def test_a_check_cannot_require_itself():
    with pytest.raises(ValueError, match="requires itself"):
        Check(name="x", summary="s", run=lambda t, c: [], requires=("x",))


def test_register_check_works_as_a_decorator(clean_registry):
    @register_check(name="deco", summary="registered as a decorator")
    def _run(target, ctx):
        return []

    assert clean_registry["deco"].name == "deco"


def test_a_plugin_cannot_silently_shadow_a_builtin(clean_registry):
    register_check(_check("a"))
    with pytest.raises(Exception):
        register_check(_check("a"))


# --- the menu, and matching it to what a user said ---------------------------


def test_suggest_matches_the_words_a_person_actually_uses(clean_registry):
    register_check(
        _check("freeze", example_requests=("the picture freezes", "it gets stuck"))
    )
    register_check(_check("silence", example_requests=("there is no sound",)))
    assert [c.name for c in suggest("the picture freezes near the end")] == ["freeze"]


def test_suggest_never_volunteers_a_paid_check(clean_registry):
    register_check(
        _check("ocr", cost="paid", example_requests=("read the text on screen",))
    )
    assert suggest("read the text on screen") == ()
    assert [c.name for c in suggest("read the text on screen", include_paid=True)] == [
        "ocr"
    ]


def test_the_menu_can_be_filtered_by_cost(clean_registry):
    register_check(_check("cheap_one", cost="cheap"))
    register_check(_check("dear_one", cost="dear"))
    assert [c.name for c in menu(cost=["cheap"])] == ["cheap_one"]


# --- nw's own checks ---------------------------------------------------------


def test_the_builtin_checks_are_on_the_menu():
    import nw  # noqa: F401  — importing nw registers them

    names = {c.name for c in menu()}
    assert {
        "media.streams_present",
        "media.encode_complete",
        "media.no_long_freeze",
    } <= names


def test_every_builtin_says_what_a_user_would_say_to_want_it():
    import nw  # noqa: F401

    for check in menu():
        assert check.example_requests, (
            f"{check.name} has no example_requests: it cannot be matched to a "
            f"request, which is how it reaches an MCP surface"
        )


def test_a_builtin_handed_something_that_is_not_a_file_says_so_loudly():
    import nw  # noqa: F401

    report = validate(object(), checks=["media.streams_present"])
    assert not report.ok and "not a media file" in report.results[0].error


def test_a_missing_file_is_an_error_not_a_pass(tmp_path):
    import nw  # noqa: F401

    report = validate(tmp_path / "never-rendered.mp4", checks=["media.streams_present"])
    assert not report.ok
    assert "does not exist" in report.findings[0].message


def test_an_empty_report_is_ok_and_has_no_findings():
    report = ValidationReport(target="x")
    assert report.ok and report.findings == () and "PASS" in report.summary()

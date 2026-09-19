"""
Run labelled cases against the real Jev API and report how each judgment did.

    python manage.py jev_eval            # every suite
    python manage.py jev_eval smoke      # one suite

This is how thresholds get chosen and how a TYPESAFE_MODEL bump gets approved:
run it before and after, compare. It calls the paid API, so it is a command
you run on purpose, never part of `manage.py test`.

A suite is a function in `SUITES` returning a list of (label, passed, detail)
rows. Features add their own suite next to their labelled cases in
`resume/evals/`. Cases there are synthetic — never paste a real user's resume
or posting into them.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from resume import typesafe_engine
from resume.typesafe_engine import Choice, Noul, Score


def smoke_suite():
    """The key works, the pinned model answers, and each primitive round-trips."""
    answers = typesafe_engine.ask(
        {"resume": "Backend developer, five years of Python and Django, some AWS."},
        {
            "django": Noul(instructions="Does the resume show Django experience?"),
            "rust": Noul(instructions="Does the resume show Rust experience?"),
            "language": Choice(
                instructions="Which programming language does the resume centre on?",
                criteria={"python": None, "java": None, "go": None},
            ),
            "seniority": Score(
                instructions="How much professional experience does the resume show?",
                criteria=[
                    "Under two years",
                    "Two to six years",
                    "More than six years",
                ],
            ),
        },
        purpose="jev_eval.smoke",
    )
    if answers is None:
        return [("request", False, "no answer; check TYPESAFE_API_KEY and the logs")]
    return [
        ("model", answers.model == settings.TYPESAFE_MODEL, answers.model),
        ("noul yes", answers.nouls["django"] > 0.8, f"{answers.nouls['django']:.2f}"),
        ("noul no", answers.nouls["rust"] < 0.2, f"{answers.nouls['rust']:.2f}"),
        (
            "choice",
            answers.choices["language"].choice == "python",
            answers.choices["language"].choice,
        ),
        (
            "score",
            answers.scores["seniority"].level == 1,
            f"{answers.scores['seniority'].score:.2f}",
        ),
    ]


SUITES = {
    "smoke": smoke_suite,
}


class Command(BaseCommand):
    help = "Evaluate Jev judgments on labelled cases (calls the real TypeSafe API)."

    def add_arguments(self, parser):
        parser.add_argument("suites", nargs="*", help=f"any of: {', '.join(SUITES)}")

    def handle(self, *args, **options):
        if not typesafe_engine.is_configured():
            raise CommandError("TYPESAFE_API_KEY is not set.")
        names = options["suites"] or list(SUITES)
        unknown = [n for n in names if n not in SUITES]
        if unknown:
            raise CommandError(f"Unknown suite: {', '.join(unknown)}")

        failed = 0
        for name in names:
            rows = SUITES[name]()
            passed = sum(1 for _, ok, _ in rows if ok)
            failed += len(rows) - passed
            self.stdout.write(f"\n{name}: {passed}/{len(rows)}")
            for label, ok, detail in rows:
                mark = self.style.SUCCESS("pass") if ok else self.style.ERROR("FAIL")
                self.stdout.write(f"  {mark}  {label:<24} {detail}")

        self.stdout.write(f"\nmodel {settings.TYPESAFE_MODEL}; {failed} failing")
        if failed:
            raise CommandError(f"{failed} case(s) failed")

"""
Shared survey analytics aggregation — used by both the owner-facing
endpoint (routers/projects.py) and the public results endpoint
(routers/public.py), so the two can never quietly drift out of sync.

`detailed=True` (owner/admin) adds form-level numbers that involve
individual submissions (timeline, anonymous split, nominee names). The
public results page never gets those.
"""
import json
import re
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Project, Submission, SurveyAnswer, Nominee
from .schemas import (
    SurveyAnalyticsOut, QuestionAnalytics, AnalyticsOverview, DayCount, NameCount,
)

_STOP = set("""a an and are as at be but by for from had has have he her his i if in into is it its
me my no not of on or our she so that the their them then there they this to too us was we were what
when which who will with would you your very just really about more most than can all any been out up
""".split())


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _median(nums):
    if not nums:
        return None
    n = sorted(nums)
    mid = len(n) // 2
    return float(n[mid]) if len(n) % 2 else (n[mid - 1] + n[mid]) / 2


def build_survey_analytics(project: Project, db: Session, detailed: bool = False) -> SurveyAnalyticsOut:
    submissions = db.query(Submission).filter(Submission.project_id == project.id).all()
    total = len(submissions)

    questions_out = []
    answered_total = 0
    for question in project.survey_questions:
        answers = db.query(SurveyAnswer).filter(SurveyAnswer.question_id == question.id).all()
        answers = [a for a in answers if a.answer_text is not None and str(a.answer_text).strip() != ""]
        n = len(answers)
        answered_total += n
        entry = QuestionAnalytics(
            question_id=question.id,
            question_text=question.question_text,
            question_type=question.question_type,
            response_count=n,
            required=bool(question.required),
            skipped_count=max(total - n, 0),
            response_rate=round(n / total * 100, 1) if total else None,
        )
        if question.question_type in ("multiple_choice", "yes_no"):
            counts = {}
            if question.question_type == "multiple_choice" and question.options:
                try:
                    for opt in json.loads(question.options):
                        counts[opt] = 0  # show options nobody picked, too
                except (ValueError, TypeError):
                    pass
            elif question.question_type == "yes_no":
                counts = {"Yes": 0, "No": 0}
            for a in answers:
                counts[a.answer_text] = counts.get(a.answer_text, 0) + 1
            entry.option_counts = counts
            if n:
                top, top_n = max(counts.items(), key=lambda kv: kv[1])
                entry.top_option = top
                entry.top_option_pct = round(top_n / n * 100, 1)
        elif question.question_type == "rating":
            numeric = []
            for a in answers:
                try:
                    numeric.append(float(a.answer_text))
                except (TypeError, ValueError):
                    pass
            entry.average_rating = (sum(numeric) / len(numeric)) if numeric else None
            entry.median_rating = _median(numeric)
            dist = {str(i): 0 for i in range(1, 6)}
            for v in numeric:
                key = str(int(round(v)))
                if key in dist:
                    dist[key] += 1
            entry.rating_counts = dist
        elif question.question_type == "short_text":
            texts = [a.answer_text for a in answers]
            entry.text_answers = texts
            if texts:
                entry.avg_text_length = round(sum(len(t) for t in texts) / len(texts))
                words = Counter(
                    w for t in texts for w in re.findall(r"(?<![a-zA-Z'])[a-zA-Z']{3,24}(?![a-zA-Z'])", t.lower()) if w not in _STOP
                )
                entry.common_words = [{"word": w, "count": c} for w, c in words.most_common(8) if c > 1]
        questions_out.append(entry)

    qn = len(project.survey_questions)
    overview = AnalyticsOverview(
        question_count=qn,
        avg_completion=round(answered_total / (qn * total) * 100, 1) if qn and total else None,
        first_submission_at=_iso(min((s.created_at for s in submissions if s.created_at), default=None)),
        last_submission_at=_iso(max((s.created_at for s in submissions if s.created_at), default=None)),
    )

    if detailed:
        overview.anonymous_count = sum(1 for s in submissions if s.is_anonymous)
        overview.named_count = total - overview.anonymous_count
        overview.status_counts = {"new": sum(1 for s in submissions if s.status == "new"), "reviewed": sum(1 for s in submissions if s.status != "new")}
        overview.suggestion_count = sum(1 for s in submissions if s.suggestion_text and s.suggestion_text.strip())
        sub_ids = [s.id for s in submissions]
        nominees = db.query(Nominee).filter(Nominee.submission_id.in_(sub_ids)).all() if sub_ids else []
        overview.nomination_count = len({n.submission_id for n in nominees})
        overview.total_nominees = len(nominees)
        by_name = Counter()
        display = {}
        for nm in nominees:
            key = nm.name.strip().lower()
            by_name[key] += 1
            display.setdefault(key, nm.name.strip())
        overview.unique_nominees = len(by_name)
        overview.top_nominees = [NameCount(name=display[k], count=c) for k, c in by_name.most_common(10)]

        now = datetime.utcnow()
        today = now.date()
        per = Counter()
        weekday = [0] * 7
        for s in submissions:
            if not s.created_at:
                continue
            per[s.created_at.date()] += 1
            weekday[s.created_at.weekday()] += 1
        days = [today - timedelta(days=i) for i in range(29, -1, -1)]
        overview.per_day = [DayCount(date=d.isoformat(), count=per.get(d, 0)) for d in days]
        overview.last_7_days = sum(per.get(today - timedelta(days=i), 0) for i in range(7))
        overview.last_30_days = sum(d.count for d in overview.per_day)
        overview.by_weekday = weekday
        if per:
            bd, bc = max(per.items(), key=lambda kv: kv[1])
            overview.busiest_day = DayCount(date=bd.isoformat(), count=bc)

    return SurveyAnalyticsOut(form_title=project.title, form_type=project.type, total_submissions=total, questions=questions_out, overview=overview)

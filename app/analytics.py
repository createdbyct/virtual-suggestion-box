"""
Shared survey analytics aggregation — used by both the owner-facing
endpoint (routers/projects.py) and the public results endpoint
(routers/public.py), so the two can never quietly drift out of sync.
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Project, Submission, SurveyAnswer
from .schemas import SurveyAnalyticsOut, QuestionAnalytics


def build_survey_analytics(project: Project, db: Session) -> SurveyAnalyticsOut:
    total_submissions = db.query(func.count(Submission.id)).filter(Submission.project_id == project.id).scalar()

    questions_out = []
    for question in project.survey_questions:
        answers = (
            db.query(SurveyAnswer)
            .filter(SurveyAnswer.question_id == question.id)
            .all()
        )
        entry = QuestionAnalytics(
            question_id=question.id,
            question_text=question.question_text,
            question_type=question.question_type,
            response_count=len(answers),
        )
        if question.question_type in ("multiple_choice", "yes_no"):
            counts = {}
            for a in answers:
                counts[a.answer_text] = counts.get(a.answer_text, 0) + 1
            entry.option_counts = counts
        elif question.question_type == "rating":
            numeric = [float(a.answer_text) for a in answers if a.answer_text and a.answer_text.replace(".", "", 1).isdigit()]
            entry.average_rating = (sum(numeric) / len(numeric)) if numeric else None
        elif question.question_type == "short_text":
            entry.text_answers = [a.answer_text for a in answers]
        questions_out.append(entry)

    return SurveyAnalyticsOut(total_submissions=total_submissions, questions=questions_out)

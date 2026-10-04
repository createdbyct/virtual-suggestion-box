"""
Built-in survey question templates — a fixed set of starting points a
form owner can drop onto any form's Questions tab in one click. Not
user-creatable or editable; just a curated shortcut for common cases.
Each question dict matches the shape SurveyQuestionCreate expects.
"""

SURVEY_TEMPLATES = {
    "customer_satisfaction": {
        "name": "Customer Satisfaction",
        "description": "How happy customers are with your service, plus room for open feedback.",
        "questions": [
            {
                "question_text": "How satisfied are you with our service?",
                "question_type": "multiple_choice",
                "options": ["Very satisfied", "Satisfied", "Neutral", "Dissatisfied", "Very dissatisfied"],
                "required": True,
            },
            {
                "question_text": "How likely are you to recommend us to a friend or colleague?",
                "question_type": "rating",
                "options": None,
                "required": True,
            },
            {
                "question_text": "What could we do better?",
                "question_type": "short_text",
                "options": None,
                "required": False,
            },
        ],
    },
    "event_feedback": {
        "name": "Event Feedback",
        "description": "A quick pulse check right after an event or meeting.",
        "questions": [
            {
                "question_text": "How would you rate this event overall?",
                "question_type": "rating",
                "options": None,
                "required": True,
            },
            {
                "question_text": "Did the event meet your expectations?",
                "question_type": "yes_no",
                "options": None,
                "required": True,
            },
            {
                "question_text": "What was your favorite part?",
                "question_type": "short_text",
                "options": None,
                "required": False,
            },
            {
                "question_text": "What could be improved for next time?",
                "question_type": "short_text",
                "options": None,
                "required": False,
            },
        ],
    },
    "nps": {
        "name": "Net Promoter Score (NPS)",
        "description": "The classic two-question NPS format.",
        "questions": [
            {
                "question_text": "On a scale of 1–5, how likely are you to recommend us?",
                "question_type": "rating",
                "options": None,
                "required": True,
            },
            {
                "question_text": "What's the main reason for your score?",
                "question_type": "short_text",
                "options": None,
                "required": False,
            },
        ],
    },
    "employee_engagement": {
        "name": "Employee Engagement",
        "description": "How valued and supported your team feels.",
        "questions": [
            {
                "question_text": "I feel valued at work",
                "question_type": "multiple_choice",
                "options": ["Strongly agree", "Agree", "Neutral", "Disagree", "Strongly disagree"],
                "required": True,
            },
            {
                "question_text": "I have the resources I need to do my job well",
                "question_type": "multiple_choice",
                "options": ["Strongly agree", "Agree", "Neutral", "Disagree", "Strongly disagree"],
                "required": True,
            },
            {
                "question_text": "How likely are you to recommend this workplace to a friend?",
                "question_type": "rating",
                "options": None,
                "required": False,
            },
            {
                "question_text": "What's one thing we could do to improve your experience?",
                "question_type": "short_text",
                "options": None,
                "required": False,
            },
        ],
    },
    "meeting_feedback": {
        "name": "Meeting Feedback",
        "description": "Was the meeting worth everyone's time?",
        "questions": [
            {
                "question_text": "Was this meeting a good use of your time?",
                "question_type": "yes_no",
                "options": None,
                "required": True,
            },
            {
                "question_text": "How would you rate the meeting overall?",
                "question_type": "rating",
                "options": None,
                "required": False,
            },
            {
                "question_text": "Any suggestions for future meetings?",
                "question_type": "short_text",
                "options": None,
                "required": False,
            },
        ],
    },
}

"""
Questionnaire definitions.

⚠  PLACEHOLDER CONTENT — replace before any real data collection.
   Edit the PRE_QUESTIONS and POST_QUESTIONS lists below with the
   final approved questionnaire items.  The structure of each entry
   (id, type, label, section, and optional fields) must be preserved;
   only the question text, ids, and options need to change.

   Field reference
   ---------------
   id          : unique snake_case key stored in the DB response JSON
   type        : "text" | "textarea" | "scale" | "radio" | "checkbox"
   label       : human-readable question text shown in the browser
   section     : groups questions under a heading in the UI
   placeholder : hint text for text/textarea inputs (optional)
   min_value   : lower bound for scale inputs (optional, default 1)
   max_value   : upper bound for scale inputs (optional, default 10)
   options     : list of strings for radio/checkbox inputs (optional)
"""

from __future__ import annotations

from live_analytics.questionnaire.models import QuestionDef, QuestionnaireDef

# ─── Pre-ride questionnaire (filled in BEFORE cycling) ────────────────

PRE_QUESTIONS: list[QuestionDef] = [
    QuestionDef(
        id="pre_q1",
        type="text",
        label="Placeholder-spørgsmål 1 (erstattes senere)",
        section="Generelt",
        placeholder="Skriv dit svar her…",
    ),
    QuestionDef(
        id="pre_q2",
        type="scale",
        label="Placeholder-spørgsmål 2 – skala (erstattes senere)",
        section="Generelt",
        min_value=1,
        max_value=10,
    ),
    QuestionDef(
        id="pre_q3",
        type="radio",
        label="Placeholder-spørgsmål 3 – valgmuligheder (erstattes senere)",
        section="Generelt",
        options=["Mulighed A", "Mulighed B", "Mulighed C"],
    ),
]

PRE_QUESTIONNAIRE = QuestionnaireDef(
    phase="pre",
    title="Spørgeskema – Før cykling",
    description="Udfyld dette skema inden du starter med at cykle. Dine svar gemmes automatisk.",
    questions=PRE_QUESTIONS,
)

# ─── Post-ride questionnaire (filled in AFTER cycling) ────────────────

POST_QUESTIONS: list[QuestionDef] = [
    QuestionDef(
        id="post_q1",
        type="textarea",
        label="Placeholder-spørgsmål 1 – efter cykling (erstattes senere)",
        section="Oplevelse",
        placeholder="Beskriv din oplevelse…",
    ),
    QuestionDef(
        id="post_q2",
        type="scale",
        label="Placeholder-spørgsmål 2 – skala (erstattes senere)",
        section="Oplevelse",
        min_value=1,
        max_value=10,
    ),
    QuestionDef(
        id="post_q3",
        type="checkbox",
        label="Placeholder-spørgsmål 3 – checkbox (erstattes senere)",
        section="Oplevelse",
        options=["Svært", "Nemt", "Sjovt", "Kedeligt"],
    ),
]

POST_QUESTIONNAIRE = QuestionnaireDef(
    phase="post",
    title="Spørgeskema – Efter cykling",
    description="Udfyld dette skema efter du er færdig med at cykle. Dine svar gemmes automatisk.",
    questions=POST_QUESTIONS,
)

# ─── Convenience lookup ──────────────────────────────────────────────

QUESTIONNAIRES: dict[str, QuestionnaireDef] = {
    "pre": PRE_QUESTIONNAIRE,
    "post": POST_QUESTIONNAIRE,
}

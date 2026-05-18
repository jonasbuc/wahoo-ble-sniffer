"""
Questionnaire definitions.

PRE_QUESTIONS   — spørgeskema udfyldt FØR cykling (demografi + baggrund)
POST_QUESTIONS  — spørgeskema udfyldt EFTER cykling (oplevelse — TBD)

Field reference
---------------
id          : unique snake_case key stored in the DB response JSON
type        : "text" | "textarea" | "scale" | "radio" | "checkbox"
label       : human-readable question text shown in the browser
section     : groups questions under a heading in the UI
placeholder : hint text for text/textarea inputs (optional)
options     : list of strings for radio/checkbox inputs (optional)
required    : whether the field must be filled in (default True)
"""

from __future__ import annotations

from live_analytics.questionnaire.models import QuestionDef, QuestionnaireDef

# ─── Pre-ride questionnaire (filled in BEFORE cycling) ────────────────

PRE_QUESTIONS: list[QuestionDef] = [

    # ── Demografi ─────────────────────────────────────────────────────
    QuestionDef(
        id="pre_name",
        type="text",
        label="Navn",
        section="Demografi",
        placeholder="Dit navn…",
        required=False,
    ),
    QuestionDef(
        id="pre_gender",
        type="radio",
        label="Køn",
        section="Demografi",
        options=["Mand", "Kvinde", "Ikke-binær / andet", "Ønsker ikke at oplyse"],
    ),
    QuestionDef(
        id="pre_age_group",
        type="radio",
        label="Aldersgruppe",
        section="Demografi",
        options=[
            "Ung (18–24 år)",
            "Yngre voksen (25–39 år)",
            "Midaldrende (40–59 år)",
            "Senior (60+ år)",
        ],
    ),

    # ── Cykelvaner ────────────────────────────────────────────────────
    QuestionDef(
        id="pre_cyclist_type",
        type="radio",
        label="Hvordan vil du betegne dig selv som cyklisttype?",
        section="Cykelvaner",
        options=[
            "Ny / urutineret cyklist",
            "Forhenværende cyklist – har tidligere cyklet regelmæssigt, men ikke længere",
            "Rutineret cyklist – cykler flere gange om måneden til forskellige formål",
            "Vanecyklist – pendler eller cykler flere gange ugentligt",
        ],
    ),
    QuestionDef(
        id="pre_cyclist_type_comment",
        type="text",
        label="Anden cyklisttype / kommentar",
        section="Cykelvaner",
        placeholder="Uddyb evt. her…",
        required=False,
    ),
    QuestionDef(
        id="pre_cycling_safety",
        type="radio",
        label="Hvor tryg føler du dig, når du cykler?",
        section="Cykelvaner",
        options=[
            "Altid tryg",
            "Næsten altid tryg",
            "Indimellem tryg",
            "Næsten aldrig tryg",
            "Aldrig tryg",
        ],
    ),

    # ── Erfaring med udstyr ───────────────────────────────────────────
    QuestionDef(
        id="pre_vr_experience",
        type="radio",
        label="Har du prøvet VR-briller før?",
        section="Erfaring med udstyr",
        options=["Ja", "Nej"],
    ),
    QuestionDef(
        id="pre_stationary_bike_experience",
        type="radio",
        label="Har du prøvet en stationærcykel / kondicykel før?",
        section="Erfaring med udstyr",
        options=["Ja", "Nej"],
    ),
]

PRE_QUESTIONNAIRE = QuestionnaireDef(
    phase="pre",
    title="Spørgeskema – Før cykling",
    description=(
        "Udfyld dette skema inden du starter med at cykle. "
        "Dine svar gemmes automatisk og bruges udelukkende til forskning."
    ),
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

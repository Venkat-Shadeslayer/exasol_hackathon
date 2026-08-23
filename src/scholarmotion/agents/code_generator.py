from __future__ import annotations

import shutil
from functools import lru_cache

from scholarmotion.schemas import GeneratedSceneCode, SceneSpec


@lru_cache(maxsize=1)
def latex_available() -> bool:
    """Whether Manim can compile Tex/MathTex on this machine.

    Manim shells out to `latex` for every Tex-based mobject. Without a TeX
    distribution installed those renders raise FileNotFoundError, which the
    repair loop cannot fix by rewriting code it keeps regenerating the same way.
    """
    return shutil.which("latex") is not None


NO_LATEX_RULES = """
LATEX IS NOT INSTALLED ON THIS MACHINE. MathTex, Tex, and EquationPanel cannot render and will
crash the scene. This overrides the equation rules above: express every formula, Greek letter,
and symbol with Text(...) using Unicode characters instead — for example Text("p = q x 2a"),
Text("E ∝ 1/r³"), Text("τ = p × E"), Text("θ"), Text("ε₀"). Unicode superscripts, subscripts, and
Greek letters render correctly through Text(). Do not import or call MathTex, Tex, or
EquationPanel anywhere."""

RULES = """Use only Manim, math, numpy, sympy, and scholarmotion.manim_runtime.components.
Never access network, process, shell, environment, or paths outside the supplied assets directory.
Use SafeTitle and reserve SubtitleSafeRegion. Prefer EquationPanel and avoid_overlap.
The component helpers have these exact call signatures: create the safe region with
`subtitle_region = SubtitleSafeRegion()` (never pass `self`), and call
`keep_inside_frame(mobject)` (never pass the Scene instance). Add the region to the scene when
needed with `self.add(subtitle_region)`.
Define exactly the requested Scene subclass. Keep each scene independent.
Never pass LaTeX source (backslash commands like \\Phi, \\frac, or a string wrapped in $...$) to
Text(), SafeTitle(), or TimelineLabel() — they render it as literal characters, not a symbol.
Any equation, Greek letter, or mathematical symbol MUST go through MathTex(...) or
EquationPanel(equation=..., title=...) / EquationPanel(equations=[...], title=...) instead.
Conversely, MathTex/Tex/EquationPanel are ONLY for actual mathematical notation — never put a
plain English caption, sentence, or label there (use Text/SafeTitle/EquationPanel's own `title=`
kwarg for that instead). If a short word label truly must appear inside a MathTex string via
\\text{...}, it must never contain a literal &, %, or # character (LaTeX treats these as special
control characters — alignment tab, comment, parameter — and compilation will fail); escape them
as \\&, \\%, \\# if unavoidable.
Mobject has no `.bounding_box` attribute and no `.get_bounding_box()` method in this Manim
version; to compute extents use get_left()/get_right()/get_top()/get_bottom() (each returns an
x/y/z point) or the `.width`/`.height` properties.
Mobject has no `.scale_about_point(factor, point)` method; to scale around a specific point use
`.scale(factor, about_point=point)` instead (works both directly and inside `.animate`)."""


async def generate_scene_code(
    provider,
    spec: SceneSpec,
    corrections: list[str] | None = None,
    repair_feedback: list[str] | None = None,
) -> GeneratedSceneCode:
    scene_class = f"Scene{spec.scene_id}"
    context = "\n".join(corrections or [])
    repairs = "\n".join(repair_feedback or [])
    rules = RULES if latex_available() else RULES + NO_LATEX_RULES
    prompt = f"REPOSITORY RULES:\n{rules}\nThe Scene subclass MUST be named exactly `{scene_class}` (class {scene_class}(Scene):). Do not use any other class name.\nSCENE SPEC:\n{spec.model_dump_json(indent=2)}\nRELEVANT CORRECTIONS:\n{context}\nREPAIR FEEDBACK:\n{repairs}\nReturn only Python code."
    code = await provider.generate_code(prompt)
    return GeneratedSceneCode(
        scene_class=scene_class,
        python_code=code,
        assumptions=[],
        timing_markers={beat.beat_id: beat.start_seconds or 0 for beat in spec.visual_beats},
    )

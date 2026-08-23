# ScholarMotion correction memory

Only repeated verifier failures or validated defect feedback is promoted here. Content and style preferences are excluded.

## CORR-001

- **Category**: layout.overlap
- **Trigger conditions**: A graph and EquationPanel share a scene or a VGroup is scaled after placement.
- **Anti-pattern**: Position both objects first and scale their parent group afterward.
- **Required behavior**: Reserve non-overlapping layout regions before scaling.
- **Recommended fix**: Fit each object to its assigned region, then call avoid_overlap and keep_inside_frame.
- **Evidence count**: 4
- **Confidence**: 0.94
- **Applicable scene tags**: graph, equation, mathtex, layout.overlap
- **Applicable model**: any
- **First seen**: 2026-08-20
- **Last seen**: 2026-08-20
- **Validation tests**: golden_graph_equation
- **Status**: active

## CORR-002

- **Category**: transform.duplicate_mathtex
- **Trigger conditions**: TransformMatchingTex is used for multi-step equations.
- **Anti-pattern**: Keep the source MathTex in a separate VGroup after the transform completes.
- **Required behavior**: Ensure one active equation object remains after every transform.
- **Recommended fix**: Replace the source reference with the transform target and remove stale group members.
- **Evidence count**: 3
- **Confidence**: 0.91
- **Applicable scene tags**: equation, transformation, mathtex
- **Applicable model**: any
- **First seen**: 2026-08-20
- **Last seen**: 2026-08-20
- **Validation tests**: golden_equation_transform
- **Status**: active

## CORR-003

- **Category**: layout.subtitle_collision
- **Trigger conditions**: Axes, labels, or camera zoom approach the bottom edge.
- **Anti-pattern**: Use the full frame height for instructional visuals.
- **Required behavior**: Keep every non-subtitle object above SubtitleSafeRegion at all keyframes.
- **Recommended fix**: Reserve the lower one frame unit and validate bounds after camera movement.
- **Evidence count**: 5
- **Confidence**: 0.96
- **Applicable scene tags**: axes, camera, subtitles, layout.subtitle_collision
- **Applicable model**: any
- **First seen**: 2026-08-20
- **Last seen**: 2026-08-20
- **Validation tests**: golden_subtitle_safe_area
- **Status**: active

## CORR-004

- **Category**: render.execution
- **Trigger conditions**: Code reads a `.bounding_box` attribute or calls `.get_bounding_box()` on a Mobject/VGroup (e.g. inside custom overlap or layout checks).
- **Anti-pattern**: `obj.bounding_box` or `obj.get_bounding_box()` — neither exists on Mobject in this Manim version; `get_bounding_box()` is not a real method, it is synthesized by Mobject's generic `get_*` attribute-forwarding and raises the same AttributeError as the plain attribute.
- **Required behavior**: Compute extents from the real Mobject API: `get_left()`, `get_right()`, `get_top()`, `get_bottom()` (each returns an x/y/z point), or `.width`/`.height`.
- **Recommended fix**: Replace `obj.get_bounding_box()[0][0]` / `[2][0]` (min/max x) with `obj.get_left()[0]` / `obj.get_right()[0]`.
- **Evidence count**: 2
- **Confidence**: 0.85
- **Applicable scene tags**: diagram, transformation, mathtex, layout.overlap
- **Applicable model**: any
- **First seen**: 2026-08-21
- **Last seen**: 2026-08-21
- **Validation tests**: none yet
- **Status**: active

## CORR-005

- **Category**: render.execution
- **Trigger conditions**: Code calls `.scale_about_point(factor, point)` on a Mobject/VGroup, directly or inside `.animate`.
- **Anti-pattern**: `mobject.scale_about_point(factor, point)` or `mobject.animate.scale_about_point(factor, point)` — this method does not exist on Mobject in this Manim version and raises AttributeError.
- **Required behavior**: Use `.scale(factor, about_point=point)` instead, which works both directly and inside `.animate`.
- **Recommended fix**: Replace `mobject.animate.scale_about_point(factor, point)` with `mobject.animate.scale(factor, about_point=point)`.
- **Evidence count**: 1
- **Confidence**: 0.7
- **Applicable scene tags**: diagram, transformation, mathtex
- **Applicable model**: any
- **First seen**: 2026-08-21
- **Last seen**: 2026-08-21
- **Validation tests**: none yet
- **Status**: active


## CORR-LEARNED-0001

- **Category**: layout.subtitle_collision
- **Trigger conditions**: At keyframe 0 (the state right after the 1th self.play()/self.wait() call in construct(), not necessarily the final frame): VGroup_2 enters the subtitle safe area.
- **Anti-pattern**: Repeat the observed layout.subtitle_collision failure.
- **Required behavior**: Prevent layout.subtitle_collision and preserve assigned safe regions.
- **Recommended fix**: At keyframe 0 (the state right after the 1th self.play()/self.wait() call in construct(), not necessarily the final frame): The VGroup_2 object extends down to y=-3.45, below the subtitle-safe boundary y=-3.0. Move it up so it stays above SubtitleSafeRegion.
- **Evidence count**: 2
- **Confidence**: 0.83
- **Applicable scene tags**: layout.subtitle_collision
- **Applicable model**: any
- **First seen**: 2026-08-23
- **Last seen**: 2026-08-23
- **Validation tests**: automatic_repair_verification
- **Status**: active

## CORR-LEARNED-0002

- **Category**: layout.overlap
- **Trigger conditions**: At keyframe 0 (the state right after the 1th self.play()/self.wait() call in construct(), not necessarily the final frame): SubtitleSafeRegion_0 overlaps VGroup_2.
- **Anti-pattern**: Repeat the observed layout.overlap failure.
- **Required behavior**: Prevent layout.overlap and preserve assigned safe regions.
- **Recommended fix**: At keyframe 0 (the state right after the 1th self.play()/self.wait() call in construct(), not necessarily the final frame): The SubtitleSafeRegion_0 object overlaps the VGroup_2 object. Reserve non-overlapping layout regions before scaling, and call avoid_overlap(first, second) between them.
- **Evidence count**: 2
- **Confidence**: 0.83
- **Applicable scene tags**: layout.overlap
- **Applicable model**: any
- **First seen**: 2026-08-23
- **Last seen**: 2026-08-23
- **Validation tests**: automatic_repair_verification
- **Status**: active

## CORR-LEARNED-0003

- **Category**: layout.overlap
- **Trigger conditions**: At keyframe 4 (the state right after the 5th self.play()/self.wait() call in construct(), not necessarily the final frame): VGroup_2 overlaps Text_4.
- **Anti-pattern**: Repeat the observed layout.overlap failure.
- **Required behavior**: Prevent layout.overlap and preserve assigned safe regions.
- **Recommended fix**: At keyframe 4 (the state right after the 5th self.play()/self.wait() call in construct(), not necessarily the final frame): The VGroup_2 object overlaps the Text_4 object. Reserve non-overlapping layout regions before scaling, and call avoid_overlap(first, second) between them.
- **Evidence count**: 2
- **Confidence**: 0.83
- **Applicable scene tags**: layout.overlap
- **Applicable model**: any
- **First seen**: 2026-08-23
- **Last seen**: 2026-08-23
- **Validation tests**: automatic_repair_verification
- **Status**: active

## CORR-LEARNED-0004

- **Category**: layout.text_too_small
- **Trigger conditions**: At keyframe 3 (the state right after the 4th self.play()/self.wait() call in construct(), not necessarily the final frame): Text_6 text is too small.
- **Anti-pattern**: Repeat the observed layout.text_too_small failure.
- **Required behavior**: Prevent layout.text_too_small and preserve assigned safe regions.
- **Recommended fix**: At keyframe 3 (the state right after the 4th self.play()/self.wait() call in construct(), not necessarily the final frame): The Text_6 text object has height 0.213, below the minimum readable height 0.22. Increase its font_size.
- **Evidence count**: 2
- **Confidence**: 0.83
- **Applicable scene tags**: layout.text_too_small
- **Applicable model**: any
- **First seen**: 2026-08-23
- **Last seen**: 2026-08-23
- **Validation tests**: automatic_repair_verification
- **Status**: active

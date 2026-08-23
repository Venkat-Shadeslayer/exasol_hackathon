from __future__ import annotations

try:
    from manim import (
        BLUE,
        DOWN,
        WHITE,
        YELLOW,
        Arrow,
        Axes,
        ImageMobject,
        MathTex,
        Rectangle,
        RoundedRectangle,
        Text,
        VGroup,
    )
except ImportError as exc:  # pragma: no cover - exercised in media deployment
    raise RuntimeError("Manim components require the 'manim' optional dependency") from exc


def fit_to_region(mobject, width: float, height: float):
    if mobject.width > width:
        mobject.scale_to_fit_width(width)
    if mobject.height > height:
        mobject.scale_to_fit_height(height)
    return mobject


def keep_inside_frame(mobject, *, width: float = 13.6, height: float = 7.4, margin: float = 0.25):
    fit_to_region(mobject, width - 2 * margin, height - 2 * margin)
    mobject.set_x(
        min(
            max(mobject.get_x(), -width / 2 + mobject.width / 2 + margin),
            width / 2 - mobject.width / 2 - margin,
        )
    )
    mobject.set_y(
        min(
            max(mobject.get_y(), -height / 2 + mobject.height / 2 + margin),
            height / 2 - mobject.height / 2 - margin,
        )
    )
    return mobject


def avoid_overlap(mobject, obstacle=None, *, direction=None, buffer: float = 0.25, buff=None):
    if buff is not None:
        buffer = buff
    if obstacle is None:
        items = list(mobject)
        for index, item in enumerate(items):
            for earlier in items[:index]:
                avoid_overlap(item, earlier, direction=direction, buffer=buffer)
        return items
    if (
        mobject.get_left()[0] < obstacle.get_right()[0]
        and mobject.get_right()[0] > obstacle.get_left()[0]
    ):
        mobject.next_to(obstacle, direction if direction is not None else DOWN, buff=buffer)
    return keep_inside_frame(mobject)


class SafeTitle(VGroup):
    def __init__(self, text: str, *, subtitle: str | None = None, **kwargs):
        font_size = kwargs.pop("font_size", 40)
        color = kwargs.pop("color", WHITE)
        title_text = Text(text, font_size=font_size, color=color, **kwargs)
        if subtitle:
            subtitle_text = Text(subtitle, font_size=max(18, round(font_size * 0.5)), color=color)
            super().__init__(title_text, subtitle_text)
            self.arrange(DOWN, buff=0.15)
        else:
            super().__init__(title_text)
        self.to_edge([0, 1, 0], buff=0.25)
        fit_to_region(self, 12.5, 1.2 if subtitle else 0.7)


class EquationPanel(VGroup):
    def __init__(
        self,
        equation=None,
        *,
        equations=None,
        title: str | None = None,
        width: float | None = None,
        height: float | None = None,
        **kwargs,
    ):
        entries = equations if equations else ([equation] if equation is not None else [])
        if not entries:
            raise ValueError("EquationPanel requires 'equation' or 'equations'")
        tex_group = VGroup(
            *[entry if not isinstance(entry, str) else MathTex(entry) for entry in entries]
        ).arrange(DOWN, buff=0.35)
        fit_to_region(tex_group, 5.5, 2.0)
        content = (
            VGroup(Text(title, font_size=24, color=WHITE), tex_group).arrange(DOWN, buff=0.2)
            if title
            else tex_group
        )
        panel = RoundedRectangle(
            width=max(2, content.width + 0.5), height=max(1, content.height + 0.4), color=BLUE
        )
        super().__init__(panel, content, **kwargs)
        if width is not None:
            self.scale_to_fit_width(width)
        if height is not None:
            self.scale_to_fit_height(height)


class LabeledAxes(VGroup):
    def __init__(self, x_label: str = "x", y_label: str = "y", **kwargs):
        axes = Axes(**kwargs)
        super().__init__(axes, axes.get_x_axis_label(x_label), axes.get_y_axis_label(y_label))


class StepEquationTransform(VGroup):
    def __init__(self, *equations: str):
        super().__init__(*[MathTex(eq) for eq in equations])
        self.arrange(DOWN, buff=0.35)


class VectorArrow(Arrow):
    def __init__(self, start, end, label: str | None = None, **kwargs):
        super().__init__(start, end, **kwargs)
        self.vector_label = MathTex(label).next_to(self, [0, 1, 0]) if label else None


class ComparisonTable(VGroup):
    def __init__(self, rows: list[tuple[str, str]], **kwargs):
        entries = [
            VGroup(Text(left, font_size=26), Text(right, font_size=26)).arrange([1, 0, 0], buff=0.5)
            for left, right in rows
        ]
        super().__init__(*entries, **kwargs)
        self.arrange(DOWN, aligned_edge=[-1, 0, 0])


class PaperFigurePanel(VGroup):
    def __init__(self, path: str, caption: str, **kwargs):
        image = ImageMobject(path)
        fit_to_region(image, 6.0, 4.2)
        label = Text(caption, font_size=22)
        super().__init__(image, label, **kwargs)
        self.arrange(DOWN)


def mark_reserved(mobject, region: str = "subtitles"):
    """Tag a mobject as occupying a named layout region, exempting it from overlap checks
    against other objects sharing the same explicit region (see verify_layout)."""
    mobject.reserved_region = region
    return mobject


class SubtitleSafeRegion(Rectangle):
    def __init__(self, **kwargs):
        super().__init__(
            width=13.5,
            height=1.0,
            color=kwargs.pop("color", BLUE),
            stroke_opacity=kwargs.pop("stroke_opacity", 0.0),
            **kwargs,
        )
        self.to_edge(DOWN, buff=0)
        # This rectangle *is* the subtitle region: it is pinned to the bottom
        # edge, so it always sits below the subtitle-safe boundary. Tagging it
        # on construction keeps verify_layout from reporting the marker as an
        # intruder into the very area it defines.
        self.reserved_region = "subtitles"

    def reserve(self, region: str = "subtitles"):
        return mark_reserved(self, region)


class HighlightBox(RoundedRectangle):
    def __init__(self, mobject, **kwargs):
        super().__init__(
            width=mobject.width + 0.25,
            height=mobject.height + 0.2,
            color=kwargs.pop("color", YELLOW),
            **kwargs,
        )
        self.move_to(mobject)


class TimelineLabel(Text):
    def __init__(self, text: str, **kwargs):
        super().__init__(text, font_size=kwargs.pop("font_size", 24), **kwargs)

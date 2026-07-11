"""Qt-free manual video geometry authoring service.

This module owns everything about editing manual video geometry that does not
require Qt: a replaceable prerecorded-video reader boundary, undo/redo-capable
draft editing of the base point, tip point, and actuator ROI, a Qt-free
zoom/pan/fit view transform, pure pixel<->widget coordinate-transform helpers,
and versioned/legacy persistence built on the existing artifact-store seam.

Automatic marker detection itself is out of scope for this module — it lives
in ``application/marker_suggestion.py`` — but ``accept_marker_suggestion``
lets a caller apply an externally-computed, operator-approved candidate as
the tip point (with its provenance/settings persisted) without this module
ever performing frame analysis itself.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from soft_actuator_testing.application.services import ArtifactDocument, ArtifactStore, CancellationToken
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.domain.errors import ErrorCode, GeometryError
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint, VideoGeometry

_MAX_ZOOM = 8.0
_MIN_ZOOM = 1.0
_ZOOM_STEP = 1.25


def _clamp(value: float, minimum: float, maximum: float) -> float:
    if maximum < minimum:
        return minimum
    return min(max(value, minimum), maximum)


@dataclass(frozen=True)
class VideoMetadata:
    """Safely-probed prerecorded-video facts; no frame pixels are included."""

    frame_size: FrameSize
    frame_count: int
    fps: float

    def __post_init__(self) -> None:
        if not isinstance(self.frame_count, int) or isinstance(self.frame_count, bool) or self.frame_count <= 0:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "video must contain at least one frame", "frame_count")
        if self.fps < 0:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "frame rate cannot be negative", "fps")


class VideoProbeCancelled(RuntimeError):
    """Raised when a caller-supplied cancellation token aborts an open/probe."""


@runtime_checkable
class OpenVideoFile(Protocol):
    """One opened prerecorded-video handle; adapters own the underlying I/O."""

    metadata: VideoMetadata

    def read_frame(self, frame_index: int) -> Any: ...

    def close(self) -> None: ...


@runtime_checkable
class VideoFrameSource(Protocol):
    """Replaceable prerecorded-video reader boundary; no OpenCV crosses this seam."""

    def open(self, source: Path, *, cancellation: CancellationToken | None = None) -> OpenVideoFile: ...


@dataclass(frozen=True)
class ViewTransform:
    """A Qt-free zoom/pan view of a video frame; ``zoom == 1.0`` fits the frame."""

    zoom: float = 1.0
    center_x: float = 0.5
    center_y: float = 0.5

    def __post_init__(self) -> None:
        if not (_MIN_ZOOM <= self.zoom <= _MAX_ZOOM):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, f"zoom must be between {_MIN_ZOOM:g} and {_MAX_ZOOM:g}", "zoom")
        if not (0.0 <= self.center_x <= 1.0 and 0.0 <= self.center_y <= 1.0):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "view center must be a fraction between 0 and 1", "center")

    def visible_rect(self, frame_size: FrameSize) -> NormalizedRoi:
        """Return the axis-aligned pixel rectangle of ``frame_size`` currently visible."""

        crop_width = frame_size.width / self.zoom
        crop_height = frame_size.height / self.zoom
        center_x = self.center_x * frame_size.width
        center_y = self.center_y * frame_size.height
        left = _clamp(center_x - crop_width / 2, 0.0, frame_size.width - crop_width)
        top = _clamp(center_y - crop_height / 2, 0.0, frame_size.height - crop_height)
        return NormalizedRoi(left, top, left + crop_width, top + crop_height)


def frame_to_widget_point(
    point: PixelPoint, frame_size: FrameSize, visible: NormalizedRoi, widget_width: int, widget_height: int
) -> tuple[float, float]:
    """Map a frame-pixel point to widget coordinates using ``VideoCanvas``'s own
    aspect-preserving, centered fit-to-widget scaling (see ``VideoCanvas.paintEvent``),
    scoped to the currently visible (zoomed/panned) crop rectangle."""

    if widget_width <= 0 or widget_height <= 0:
        raise GeometryError(ErrorCode.GEOMETRY_INVALID, "widget dimensions must be positive", "widget_size")
    scale = min(widget_width / visible.width, widget_height / visible.height)
    draw_width, draw_height = visible.width * scale, visible.height * scale
    origin_x = (widget_width - draw_width) / 2
    origin_y = (widget_height - draw_height) / 2
    del frame_size
    return origin_x + (point.x - visible.left) * scale, origin_y + (point.y - visible.top) * scale


def widget_point_to_frame(
    x: float, y: float, frame_size: FrameSize, visible: NormalizedRoi, widget_width: int, widget_height: int
) -> PixelPoint:
    """Invert :func:`frame_to_widget_point` back into frame-pixel coordinates."""

    if widget_width <= 0 or widget_height <= 0:
        raise GeometryError(ErrorCode.GEOMETRY_INVALID, "widget dimensions must be positive", "widget_size")
    scale = min(widget_width / visible.width, widget_height / visible.height)
    draw_width, draw_height = visible.width * scale, visible.height * scale
    origin_x = (widget_width - draw_width) / 2
    origin_y = (widget_height - draw_height) / 2
    del frame_size
    return PixelPoint(visible.left + (x - origin_x) / scale, visible.top + (y - origin_y) / scale)


@dataclass(frozen=True)
class GeometryEditState:
    """The undoable part of manual geometry authoring.

    ``tip_provenance``/``tip_selection_confidence``/``tip_selection_reasons``/
    ``tip_suggestion_settings`` are never fabricated: they stay ``None``/empty
    until a tip point actually exists, are set to ``"manual"`` (with the rest
    cleared) whenever an operator places or nudges the tip directly, and are
    only populated with suggestion detail when a ranked marker-suggestion
    candidate is explicitly accepted (see ``accept_marker_suggestion``).
    """

    frame_size: FrameSize | None = None
    base_point: PixelPoint | None = None
    tip_point: PixelPoint | None = None
    roi: NormalizedRoi | None = None
    tip_provenance: str | None = None
    tip_selection_confidence: float | None = None
    tip_selection_reasons: tuple[str, ...] = ()
    tip_suggestion_settings: dict[str, Any] | None = None


@dataclass(frozen=True)
class GeometryWorkflowSnapshot:
    """Immutable, renderer-friendly view of the current workflow state."""

    video_path: Path | None
    metadata: VideoMetadata | None
    frame_index: int
    representative_frame_index: int | None
    frame_size: FrameSize | None
    base_point: PixelPoint | None
    tip_point: PixelPoint | None
    roi: NormalizedRoi | None
    view: ViewTransform
    overlay_visible: bool
    can_undo: bool
    can_redo: bool
    is_ready: bool
    artifact_id: str | None
    message: str
    tip_provenance: str | None = None
    tip_selection_confidence: float | None = None
    tip_selection_reasons: tuple[str, ...] = ()


class _FakeOpenVideoFile:
    """Deterministic in-memory ``OpenVideoFile`` used by hardware-free tests/demos."""

    def __init__(self, frames: tuple[Any, ...], fps: float) -> None:
        if not frames:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "video must contain at least one frame", "frame_count")
        first = frames[0]
        height, width = first.shape[0], first.shape[1]
        self.metadata = VideoMetadata(FrameSize(width, height), len(frames), fps)
        self._frames = frames
        self.closed = False
        self.read_calls: list[int] = []

    def read_frame(self, frame_index: int) -> Any:
        if self.closed:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "video handle is already closed", "video")
        self.read_calls.append(frame_index)
        return self._frames[frame_index]

    def close(self) -> None:
        self.closed = True


class FakeVideoFrameSource:
    """Deterministic, hardware-free :class:`VideoFrameSource` double for tests/demos.

    Frames for a given source path must be registered with :meth:`register`
    before :meth:`open` is called; nothing is fabricated automatically.
    """

    def __init__(self) -> None:
        self._catalog: dict[Path, tuple[tuple[Any, ...], float]] = {}
        self.opened_paths: list[Path] = []
        self.cancel_probe_for: set[Path] = set()

    def register(self, source: Path, frames: tuple[Any, ...], *, fps: float = 10.0) -> None:
        self._catalog[Path(source)] = (frames, fps)

    def open(self, source: Path, *, cancellation: CancellationToken | None = None) -> OpenVideoFile:
        source = Path(source)
        if source in self.cancel_probe_for and cancellation is not None and cancellation.is_cancelled():
            raise VideoProbeCancelled("video probing was cancelled")
        entry = self._catalog.get(source)
        if entry is None:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "no fake frames are registered for this source",
                "source",
                "Call register() with this path before opening it in a test.",
            )
        self.opened_paths.append(source)
        frames, fps = entry
        return _FakeOpenVideoFile(frames, fps)


class VideoGeometryWorkflow:
    """Author manual video geometry without Qt, OpenCV, or fabricated defaults."""

    def __init__(self, reader: VideoFrameSource) -> None:
        self._reader = reader
        self._open_video: OpenVideoFile | None = None
        self._video_path: Path | None = None
        self._edit = GeometryEditState()
        self._history: list[GeometryEditState] = []
        self._redo: list[GeometryEditState] = []
        self._frame_index = 0
        self._representative_frame_index: int | None = None
        self._view = ViewTransform()
        self._overlay_visible = True
        self._artifact_id: str | None = None
        self._message = "Choose a prerecorded video to begin."

    # --- snapshot -----------------------------------------------------
    @property
    def snapshot(self) -> GeometryWorkflowSnapshot:
        edit = self._edit
        is_ready = edit.frame_size is not None and edit.base_point is not None and edit.tip_point is not None and edit.roi is not None
        return GeometryWorkflowSnapshot(
            self._video_path,
            self._open_video.metadata if self._open_video is not None else None,
            self._frame_index,
            self._representative_frame_index,
            edit.frame_size,
            edit.base_point,
            edit.tip_point,
            edit.roi,
            self._view,
            self._overlay_visible,
            bool(self._history),
            bool(self._redo),
            is_ready,
            self._artifact_id,
            self._message,
            edit.tip_provenance,
            edit.tip_selection_confidence,
            edit.tip_selection_reasons,
        )

    @property
    def metadata(self) -> VideoMetadata | None:
        return self._open_video.metadata if self._open_video is not None else None

    # --- video lifecycle ------------------------------------------------
    def load_video(self, source: Path, *, cancellation: CancellationToken | None = None) -> VideoMetadata:
        """Open ``source`` and reset the draft geometry; never fabricates a selection."""

        source = Path(source)
        opened = self._reader.open(source, cancellation=cancellation)
        previous = self._open_video
        self._open_video = opened
        self._video_path = source
        self._frame_index = 0
        self._representative_frame_index = 0
        self._edit = GeometryEditState(frame_size=opened.metadata.frame_size)
        self._history.clear()
        self._redo.clear()
        self._view = ViewTransform()
        self._artifact_id = None
        self._message = f"Loaded {source.name}: {opened.metadata.frame_count} frame(s) at {opened.metadata.fps:g} fps."
        if previous is not None:
            previous.close()
        return opened.metadata

    def close_video(self) -> None:
        """Release the open video handle; the draft geometry is discarded with it."""

        if self._open_video is not None:
            self._open_video.close()
        self._open_video = None
        self._video_path = None
        self._frame_index = 0
        self._representative_frame_index = None
        self._edit = GeometryEditState()
        self._history.clear()
        self._redo.clear()
        self._view = ViewTransform()
        self._message = "Video closed."

    def _require_video(self) -> OpenVideoFile:
        if self._open_video is None:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "no video is loaded",
                "video",
                "Choose a prerecorded video before scrubbing frames.",
            )
        return self._open_video

    def _require_frame_size(self) -> FrameSize:
        if self._edit.frame_size is None:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "frame dimensions are not known yet",
                "frame_size",
                "Load a video or a saved geometry document before editing geometry.",
            )
        return self._edit.frame_size

    # --- frame scrubbing --------------------------------------------------
    def frame(self, index: int) -> Any:
        video = self._require_video()
        count = video.metadata.frame_count
        if not (0 <= index < count):
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "frame index is out of range",
                "frame_index",
                f"Use an index between 0 and {count - 1}.",
            )
        image = video.read_frame(index)
        self._frame_index = index
        return image

    def current_frame(self) -> Any:
        return self.frame(self._frame_index)

    def step_frame(self, delta: int) -> Any:
        video = self._require_video()
        target = _clamp(self._frame_index + delta, 0, video.metadata.frame_count - 1)
        return self.frame(int(target))

    def jump_frame(self, where: str) -> Any:
        video = self._require_video()
        if where not in ("first", "last"):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "jump target must be 'first' or 'last'", "where")
        return self.frame(0 if where == "first" else video.metadata.frame_count - 1)

    def set_representative_frame(self, index: int | None = None) -> int:
        video = self._require_video()
        target = self._frame_index if index is None else index
        if not (0 <= target < video.metadata.frame_count):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "frame index is out of range", "representative_frame_index")
        self._representative_frame_index = target
        self._message = f"Representative frame set to {target}."
        return target

    # --- geometry edits (undoable) ---------------------------------------
    def set_base_point(self, x: float, y: float) -> PixelPoint:
        frame_size = self._require_frame_size()
        point = PixelPoint(float(x), float(y))
        point.validate_in(frame_size, "base_point")
        self._push_history()
        self._edit = replace(self._edit, base_point=point)
        self._message = f"Base point set to ({point.x:g}, {point.y:g})."
        return point

    def nudge_base(self, dx: float, dy: float) -> PixelPoint:
        current = self._edit.base_point
        if current is None:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "select a base point before nudging it", "base_point")
        frame_size = self._require_frame_size()
        x = _clamp(current.x + dx, 0, frame_size.width - 1)
        y = _clamp(current.y + dy, 0, frame_size.height - 1)
        return self.set_base_point(x, y)

    def set_tip_point(self, x: float, y: float) -> PixelPoint:
        frame_size = self._require_frame_size()
        point = PixelPoint(float(x), float(y))
        point.validate_in(frame_size, "tip_point")
        self._push_history()
        self._edit = replace(
            self._edit,
            tip_point=point,
            tip_provenance="manual",
            tip_selection_confidence=None,
            tip_selection_reasons=(),
            tip_suggestion_settings=None,
        )
        self._message = f"Tip point set to ({point.x:g}, {point.y:g})."
        return point

    def nudge_tip(self, dx: float, dy: float) -> PixelPoint:
        current = self._edit.tip_point
        if current is None:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "select a tip point before nudging it", "tip_point")
        frame_size = self._require_frame_size()
        x = _clamp(current.x + dx, 0, frame_size.width - 1)
        y = _clamp(current.y + dy, 0, frame_size.height - 1)
        return self.set_tip_point(x, y)

    def clear_tip(self) -> None:
        self._push_history()
        self._edit = replace(
            self._edit,
            tip_point=None,
            tip_provenance=None,
            tip_selection_confidence=None,
            tip_selection_reasons=(),
            tip_suggestion_settings=None,
        )
        self._message = "Tip point cleared."

    def accept_marker_suggestion(
        self,
        point: PixelPoint,
        *,
        confidence: float,
        reasons: tuple[str, ...] = (),
        settings: dict[str, Any] | None = None,
    ) -> PixelPoint:
        """Apply an operator-accepted marker-suggestion candidate as the tip point.

        This never runs automatically or silently: a human always chooses to
        accept one specific ranked candidate. Correcting the tip afterwards
        with :meth:`set_tip_point`/:meth:`nudge_tip` reverts its provenance to
        ``"manual"``, so a later correction is never mistaken for an
        unreviewed detector output.
        """

        frame_size = self._require_frame_size()
        validated = PixelPoint(float(point.x), float(point.y))
        validated.validate_in(frame_size, "tip_point")
        if not isfinite(confidence) or not (0.0 <= confidence <= 1.0):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "confidence must be finite and in the range [0, 1]", "confidence")
        self._push_history()
        self._edit = replace(
            self._edit,
            tip_point=validated,
            tip_provenance="marker_suggestion",
            tip_selection_confidence=float(confidence),
            tip_selection_reasons=tuple(reasons),
            tip_suggestion_settings=dict(settings) if settings else None,
        )
        self._message = f"Accepted a suggested tip point ({validated.x:g}, {validated.y:g}); confidence {confidence:.2f}."
        return validated

    def set_roi_corners(self, x1: float, y1: float, x2: float, y2: float) -> NormalizedRoi:
        """Normalize an arbitrary (possibly reversed) drag into an axis-aligned ROI."""

        frame_size = self._require_frame_size()
        roi = NormalizedRoi.from_corners(PixelPoint(float(x1), float(y1)), PixelPoint(float(x2), float(y2)), frame_size)
        self._push_history()
        self._edit = replace(self._edit, roi=roi)
        self._message = f"ROI set to {roi.left:g},{roi.top:g}–{roi.right:g},{roi.bottom:g}."
        return roi

    def set_roi_xywh(self, x: float, y: float, width: float, height: float) -> NormalizedRoi:
        frame_size = self._require_frame_size()
        roi = NormalizedRoi.from_xywh(float(x), float(y), float(width), float(height), frame_size)
        self._push_history()
        self._edit = replace(self._edit, roi=roi)
        self._message = f"ROI set to x={roi.left:g}, y={roi.top:g}, w={roi.width:g}, h={roi.height:g}."
        return roi

    def nudge_roi(self, dx: float, dy: float) -> NormalizedRoi:
        current = self._edit.roi
        if current is None:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "select an ROI before nudging it", "roi")
        frame_size = self._require_frame_size()
        width, height = current.width, current.height
        x = _clamp(current.left + dx, 0, frame_size.width - width)
        y = _clamp(current.top + dy, 0, frame_size.height - height)
        return self.set_roi_xywh(x, y, width, height)

    def set_overlay_visible(self, visible: bool) -> None:
        self._overlay_visible = bool(visible)

    def undo(self) -> bool:
        if not self._history:
            self._message = "Nothing to undo."
            return False
        self._redo.append(self._edit)
        self._edit = self._history.pop()
        self._message = "Restored the previous geometry state."
        return True

    def redo(self) -> bool:
        if not self._redo:
            self._message = "Nothing to redo."
            return False
        self._history.append(self._edit)
        self._edit = self._redo.pop()
        self._message = "Reapplied the next geometry state."
        return True

    def reset(self) -> None:
        """Clear base/tip/ROI selections; frame dimensions are kept."""

        self._push_history()
        self._edit = GeometryEditState(frame_size=self._edit.frame_size)
        self._message = "Geometry selections cleared."

    def _push_history(self) -> None:
        self._history.append(self._edit)
        self._redo.clear()

    # --- zoom / pan / fit / reset view -----------------------------------
    def zoom_in(self) -> ViewTransform:
        return self._set_view(replace(self._view, zoom=min(self._view.zoom * _ZOOM_STEP, _MAX_ZOOM)))

    def zoom_out(self) -> ViewTransform:
        return self._set_view(replace(self._view, zoom=max(self._view.zoom / _ZOOM_STEP, _MIN_ZOOM)))

    def pan(self, dx_fraction: float, dy_fraction: float) -> ViewTransform:
        step = 1.0 / self._view.zoom
        center_x = _clamp(self._view.center_x + dx_fraction * step, 0.0, 1.0)
        center_y = _clamp(self._view.center_y + dy_fraction * step, 0.0, 1.0)
        return self._set_view(replace(self._view, center_x=center_x, center_y=center_y))

    def fit_view(self) -> ViewTransform:
        return self._set_view(ViewTransform())

    def reset_view(self) -> ViewTransform:
        return self.fit_view()

    def _set_view(self, view: ViewTransform) -> ViewTransform:
        self._view = view
        return view

    def visible_rect(self) -> NormalizedRoi:
        return self._view.visible_rect(self._require_frame_size())

    # --- persistence ------------------------------------------------------
    def _domain_geometry(self) -> VideoGeometry:
        edit = self._edit
        if edit.frame_size is None or edit.base_point is None or edit.tip_point is None or edit.roi is None:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "a base point, a tip point, and an ROI are all required before saving",
                "geometry",
                "Select a base point, a tip point, and an ROI before saving or exporting.",
            )
        return VideoGeometry(edit.frame_size, edit.base_point, edit.tip_point, edit.roi)

    def as_document(self, *, software_version: str | None = None) -> ArtifactDocument:
        geometry = self._domain_geometry()
        identity = ArtifactIdentity.new(ArtifactType.GEOMETRY)
        metadata = ArtifactMetadata.now(identity, software_version)
        payload = {
            "frame_size": {"width": geometry.frame_size.width, "height": geometry.frame_size.height},
            "base_point": {"x": geometry.base_point.x, "y": geometry.base_point.y},
            "initial_tip_point": {"x": geometry.initial_tip_point.x, "y": geometry.initial_tip_point.y},
            "roi": {
                "left": geometry.actuator_roi.left,
                "top": geometry.actuator_roi.top,
                "right": geometry.actuator_roi.right,
                "bottom": geometry.actuator_roi.bottom,
            },
            "selection_provenance": "manual_workflow",
            "representative_frame_index": self._representative_frame_index,
            "source_video_name": self._video_path.name if self._video_path else None,
        }
        edit = self._edit
        # Tip provenance/settings are only ever written when they genuinely
        # exist (an operator placed or accepted a tip); never fabricated.
        if edit.tip_provenance is not None:
            payload["tip_provenance"] = edit.tip_provenance
        if edit.tip_selection_confidence is not None:
            payload["tip_selection_confidence"] = edit.tip_selection_confidence
        if edit.tip_selection_reasons:
            payload["tip_selection_reasons"] = list(edit.tip_selection_reasons)
        if edit.tip_suggestion_settings:
            payload["marker_suggestion_settings"] = dict(edit.tip_suggestion_settings)
        return ArtifactDocument(metadata, payload)

    def save(self, store: ArtifactStore, *, software_version: str | None = None) -> ArtifactDocument:
        document = self.as_document(software_version=software_version)
        store.save(document)
        self._artifact_id = document.metadata.identity.artifact_id
        self._message = f"Saved versioned geometry {self._artifact_id}."
        return document

    def load(self, store: ArtifactStore, artifact_id: str) -> ArtifactDocument:
        document = store.load(ArtifactType.GEOMETRY, artifact_id)
        self.load_document(document)
        return document

    def import_legacy(self, store: ArtifactStore, source: Path, *, frame_size: tuple[int, int] | None = None) -> ArtifactDocument:
        size = frame_size
        if size is None:
            current = self.metadata
            if current is None and self._edit.frame_size is None:
                raise GeometryError(
                    ErrorCode.GEOMETRY_INVALID,
                    "legacy geometry import requires known frame dimensions",
                    "frame_size",
                    "Load the source video first, or supply frame dimensions explicitly.",
                )
            known = current.frame_size if current is not None else self._edit.frame_size
            size = (known.width, known.height)
        document = store.import_legacy(source, ArtifactType.GEOMETRY, frame_size=size)
        self.load_document(document)
        self._message = f"Imported legacy geometry {source.name}; review and save a versioned copy."
        return document

    def export_legacy(self, store: ArtifactStore, destination: Path) -> None:
        store.export_legacy(self.as_document(), destination)
        self._message = f"Exported legacy-compatible geometry to {destination.name}."

    def load_document(self, document: ArtifactDocument) -> VideoGeometry:
        if document.metadata.identity.artifact_type is not ArtifactType.GEOMETRY:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "document is not a geometry artifact", "artifact_type")
        payload = document.payload
        try:
            size = payload["frame_size"]
            frame_size = FrameSize(int(size["width"]), int(size["height"]))
            base = payload["base_point"]
            base_point = PixelPoint(float(base["x"]), float(base["y"]))
            tip_data = payload.get("initial_tip_point")
            tip_point = PixelPoint(float(tip_data["x"]), float(tip_data["y"])) if tip_data else None
            roi_data = payload["roi"]
            roi = NormalizedRoi(float(roi_data["left"]), float(roi_data["top"]), float(roi_data["right"]), float(roi_data["bottom"]))
        except (KeyError, TypeError, ValueError) as error:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "geometry document is missing required fields", "payload") from error
        geometry = VideoGeometry(frame_size, base_point, tip_point, roi)
        if self._open_video is not None and self._open_video.metadata.frame_size != geometry.frame_size:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "loaded video frame size does not match this geometry document",
                "frame_size",
                "Load the matching source video before applying this geometry.",
            )
        self._push_history()
        tip_provenance = payload.get("tip_provenance")
        tip_confidence = payload.get("tip_selection_confidence")
        tip_reasons = payload.get("tip_selection_reasons")
        tip_settings = payload.get("marker_suggestion_settings")
        self._edit = GeometryEditState(
            geometry.frame_size,
            geometry.base_point,
            geometry.initial_tip_point,
            geometry.actuator_roi,
            tip_provenance=tip_provenance if isinstance(tip_provenance, str) else None,
            tip_selection_confidence=float(tip_confidence) if isinstance(tip_confidence, (int, float)) else None,
            tip_selection_reasons=tuple(tip_reasons) if isinstance(tip_reasons, list) else (),
            tip_suggestion_settings=dict(tip_settings) if isinstance(tip_settings, dict) else None,
        )
        self._artifact_id = document.metadata.identity.artifact_id
        representative = payload.get("representative_frame_index")
        if isinstance(representative, int) and self._open_video is not None and 0 <= representative < self._open_video.metadata.frame_count:
            self._representative_frame_index = representative
        self._message = f"Loaded geometry {self._artifact_id}."
        return geometry


__all__ = [
    "FakeVideoFrameSource",
    "GeometryEditState",
    "GeometryWorkflowSnapshot",
    "OpenVideoFile",
    "VideoFrameSource",
    "VideoGeometryWorkflow",
    "VideoMetadata",
    "VideoProbeCancelled",
    "ViewTransform",
    "frame_to_widget_point",
    "widget_point_to_frame",
]

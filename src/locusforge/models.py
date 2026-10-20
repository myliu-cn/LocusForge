from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator

Vector3 = Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]
Vector2 = Annotated[list[FiniteFloat], Field(min_length=2, max_length=2)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OrientationKeyframe(Contract):
    at: FiniteFloat = Field(ge=0, le=1)
    yaw_deg: FiniteFloat = Field(default=0, ge=-180, le=180)
    pitch_deg: FiniteFloat = Field(default=0, ge=-85, le=85)


class MoveSegment(Contract):
    kind: Literal["move"] = "move"
    direction: Literal["forward", "backward", "left", "right", "up", "down"] = "forward"
    distance_m: FiniteFloat = Field(default=3, ge=0.01, le=500)
    weight: FiniteFloat = Field(default=1, ge=0.1, le=100)


class ArcSegment(Contract):
    kind: Literal["arc"] = "arc"
    radius_m: FiniteFloat = Field(default=3, ge=0.25, le=500)
    angle_deg: FiniteFloat = Field(default=90, ge=-360, le=360)
    weight: FiniteFloat = Field(default=2, ge=0.1, le=100)

    @model_validator(mode="after")
    def nonzero_angle(self) -> Self:
        if abs(self.angle_deg) < 1:
            raise ValueError("An arc must turn by at least one degree")
        return self


class TurnSegment(Contract):
    kind: Literal["turn"] = "turn"
    yaw_deg: FiniteFloat = Field(default=90, ge=-360, le=360)
    weight: FiniteFloat = Field(default=1, ge=0.1, le=100)

    @model_validator(mode="after")
    def nonzero_turn(self) -> Self:
        if abs(self.yaw_deg) < 1:
            raise ValueError("A stationary turn must rotate by at least one degree")
        return self


class HoldSegment(Contract):
    kind: Literal["hold"] = "hold"
    weight: FiniteFloat = Field(default=1, ge=0.1, le=100)


MotionSegment = Annotated[
    MoveSegment | ArcSegment | TurnSegment | HoldSegment, Field(discriminator="kind")
]


class Trajectory(Contract):
    kind: Literal["orbit", "figure_eight", "waypoints", "program"] = "orbit"
    center: Vector2 = Field(default_factory=lambda: [0.0, 0.0])
    radius: FiniteFloat = Field(default=5.5, ge=0.25, le=500)
    aspect: FiniteFloat = Field(default=1.0, ge=0.2, le=3)
    height: FiniteFloat = Field(default=1.7, ge=-500, le=500)
    phase_deg: FiniteFloat = Field(default=225.0, ge=0, lt=360)
    laps: int = Field(default=1, ge=1, le=4)
    heading: Literal["target", "forward"] = "forward"
    target: Vector3 = Field(default_factory=lambda: [0.0, 0.0, 1.3])
    waypoints: list[Vector3] = Field(default_factory=list, max_length=64)
    closed: bool = True
    start: Vector3 = Field(default_factory=lambda: [-3.8, -3.8, 1.7])
    start_yaw_deg: FiniteFloat = Field(default=0, ge=-180, le=180)
    segments: list[MotionSegment] = Field(default_factory=list, max_length=64)
    orientation_keyframes: list[OrientationKeyframe] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        if self.kind == "waypoints" and len(self.waypoints) < (4 if self.closed else 2):
            raise ValueError("A closed spline needs at least four points; an open spline needs two")
        if self.kind != "waypoints" and self.waypoints:
            raise ValueError("Waypoints are only valid for a waypoint trajectory")
        if self.kind in ("orbit", "figure_eight") and not self.closed:
            raise ValueError("Orbit and figure-eight templates are closed paths")
        if not self.closed and self.laps != 1:
            raise ValueError("An open path cannot repeat without a discontinuous jump")
        if self.kind == "program" and not self.segments:
            raise ValueError("A motion program needs at least one segment")
        if self.kind != "program" and self.segments:
            raise ValueError("Motion segments are only valid for a program trajectory")
        if self.orientation_keyframes and self.heading != "forward":
            raise ValueError(
                "Orientation waypoints are relative to forward travel; select forward heading"
            )
        keys = sorted(self.orientation_keyframes, key=lambda key: key.at)
        if any(b.at - a.at < 0.0001 for a, b in zip(keys[:-1], keys[1:], strict=True)):
            raise ValueError("Orientation waypoints must have distinct, separated progress values")
        if self.closed and keys and keys[-1].at == 1:
            first = keys[0] if keys[0].at == 0 else OrientationKeyframe(at=0)
            yaw_difference = (keys[-1].yaw_deg - first.yaw_deg + 180) % 360 - 180
            if abs(yaw_difference) > 1e-6 or abs(keys[-1].pitch_deg - first.pitch_deg) > 1e-6:
                raise ValueError(
                    "A closed path's final orientation must equal its starting orientation; remove the final key to auto-link it"
                )
        self.orientation_keyframes = keys
        if any(abs(value) > 10000 for value in (*self.center, *self.target, *self.start)):
            raise ValueError("Coordinates must be within 10,000 scene meters")
        if any(abs(value) > 10000 for point in self.waypoints for value in point):
            raise ValueError("Waypoints must be within 10,000 scene meters")
        return self


class PathInput(Contract):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    scene_id: Slug
    trajectory: Trajectory
    duration_seconds: int = Field(default=30, ge=2, le=180)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Path name cannot be blank")
        return value.strip()


class PathUpdate(PathInput):
    revision: int = Field(ge=1)


class MotionParseRequest(Contract):
    text: str = Field(min_length=1, max_length=4000)
    provider: Literal["local", "llm"] = "local"
    allow_external: bool = False
    start: Vector3 = Field(default_factory=lambda: [-3.8, -3.8, 1.7])
    start_yaw_deg: FiniteFloat = Field(default=0, ge=-180, le=180)


class MotionProposal(Contract):
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=1000)
    assumptions: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list, max_length=12
    )
    duration_seconds: int = Field(default=30, ge=2, le=180)
    trajectory: Trajectory

    @model_validator(mode="after")
    def motion_only(self) -> Self:
        if self.trajectory.heading != "forward" or self.trajectory.orientation_keyframes:
            raise ValueError(
                "Step one may only define motion; lens orientation belongs to step two"
            )
        return self


class Capture(Contract):
    width: int = Field(default=1280, ge=64, le=3840)
    height: int = Field(default=720, ge=64, le=2160)
    fps: int = Field(default=24, ge=1, le=60)
    duration_seconds: int = Field(default=30, ge=2, le=180)
    fov_deg: FiniteFloat = Field(default=70.0, ge=25, le=110)
    engine: Literal["CYCLES", "BLENDER_EEVEE_NEXT"] = "CYCLES"
    device: Literal["CPU", "CUDA", "OPTIX", "HIP", "ONEAPI", "METAL", "GRAPHICS"] = "CPU"
    samples: int = Field(default=32, ge=1, le=512)
    seed: int = Field(default=42, ge=0, le=2147483647)
    purpose: Literal["preview", "dataset"] = "dataset"
    lighting: Literal["daylight", "asset"] = "daylight"

    @model_validator(mode="after")
    def validate_capture(self) -> Self:
        if self.width % 2 or self.height % 2:
            raise ValueError("H.264 yuv420p requires even image dimensions")
        if self.width * self.height > 3840 * 2160:
            raise ValueError("Resolution exceeds the supported pixel budget")
        if self.frame_count > 10800:
            raise ValueError("A job may contain at most 10,800 frames")
        if self.engine == "BLENDER_EEVEE_NEXT" and self.device != "GRAPHICS":
            raise ValueError(
                "EEVEE uses the graphics context, not a Cycles compute-device selector"
            )
        if self.engine == "CYCLES" and self.device == "GRAPHICS":
            raise ValueError("Cycles needs an explicit CPU or compute-device backend")
        return self

    @property
    def frame_count(self) -> int:
        return self.fps * self.duration_seconds


class Quality(Contract):
    min_gap_seconds: FiniteFloat = Field(default=5.0, ge=0.5, le=179)
    min_excursion_m: FiniteFloat = Field(default=3.0, ge=0.1, le=500)
    max_position_error_m: FiniteFloat = Field(default=0.25, ge=0.001, le=2)
    max_rotation_error_deg: FiniteFloat = Field(default=8.0, ge=0.01, le=30)
    min_overlap: FiniteFloat = Field(default=0.8, ge=0.5, le=1)
    max_departure_overlap: FiniteFloat = Field(default=0.35, ge=0, le=0.8)
    min_valid_depth: FiniteFloat = Field(default=0.5, ge=0.1, le=1)
    camera_radius_m: FiniteFloat = Field(default=0.2, ge=0.05, le=1)
    max_speed_mps: FiniteFloat = Field(default=3.0, ge=0.1, le=20)
    max_angular_speed_dps: FiniteFloat = Field(default=90.0, ge=1, le=360)
    max_acceleration_mps2: FiniteFloat = Field(default=6.0, ge=0.1, le=100)
    min_pairs: int = Field(default=1, ge=1, le=64)


class Recipe(Contract):
    name: str = Field(default="Courtyard / memory loop", min_length=1, max_length=100)
    scene_id: Slug = "courtyard"
    trajectory: Trajectory = Field(default_factory=Trajectory)
    capture: Capture = Field(default_factory=Capture)
    quality: Quality = Field(default_factory=Quality)

    @model_validator(mode="after")
    def validate_clock(self) -> Self:
        horizon = (self.capture.frame_count - 1) / self.capture.fps
        if self.quality.min_gap_seconds > horizon:
            raise ValueError("Minimum revisit gap exceeds the sampled trajectory horizon")
        return self


class SceneInput(Contract):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    source_url: str = Field(max_length=1000)
    license: Literal["CC0-1.0", "CC-BY-4.0", "OWNED", "CUSTOM"]
    attribution: str = Field(min_length=1, max_length=1000)
    source_group: Slug
    meters_per_unit: FiniteFloat = Field(default=1.0, ge=0.001, le=1000)
    commercial_use_confirmed: bool
    training_use_confirmed: bool

    @field_validator("source_url")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if not value.startswith(("https://", "http://", "original:")):
            raise ValueError("Source must be an http(s) URL or an original: provenance identifier")
        return value

    @model_validator(mode="after")
    def validate_rights(self) -> Self:
        if not self.commercial_use_confirmed or not self.training_use_confirmed:
            raise ValueError("Asset ingestion requires explicit commercial and training rights")
        return self

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math

from .bfres_mesh import _Reader


_ANIM_DATA_NAMES = {
    0x04: "scale_x",
    0x08: "scale_y",
    0x0C: "scale_z",
    0x10: "translate_x",
    0x14: "translate_y",
    0x18: "translate_z",
    0x20: "rotate_x",
    0x24: "rotate_y",
    0x28: "rotate_z",
    0x2C: "rotate_w",
}

_CAMERA_DATA_NAMES = {
    0x00: "clip_near",
    0x04: "clip_far",
    0x08: "aspect_ratio",
    0x0C: "field_of_view",
    0x10: "position_x",
    0x14: "position_y",
    0x18: "position_z",
    0x1C: "rotation_x",
    0x20: "rotation_y",
    0x24: "rotation_z",
    0x28: "twist",
}

_CURVE_CUBIC = 0x00
_CURVE_LINEAR = 0x10
_CURVE_BAKED_FLOAT = 0x20
_CURVE_STEP_INT = 0x40
_CURVE_BAKED_INT = 0x50
_CURVE_STEP_BOOL = 0x60
_CURVE_BAKED_BOOL = 0x70

_BONE_TRANSFORM_SEGMENT_SCALE_COMPENSATE = 1 << 23


class BFRESAnimationError(ValueError):
    """An actionable Switch BFRES animation error."""


@dataclass(frozen=True, slots=True)
class AnimationCurve:
    data_offset: int
    curve_type: int
    frames: tuple[float, ...]
    keys: tuple[tuple[float, ...], ...]
    scale: float
    offset: float
    start_frame: float
    end_frame: float

    @property
    def data_name(self) -> str:
        return _ANIM_DATA_NAMES[self.data_offset]

    def key_value(self, index: int) -> float:
        value = self.keys[index][0]

        if self.curve_type in {_CURVE_STEP_INT, _CURVE_BAKED_INT}:
            return value + self.offset
        if self.curve_type in {_CURVE_STEP_BOOL, _CURVE_BAKED_BOOL}:
            return value

        return value * self.scale + self.offset

    def surrounding_key_indices(self, frame: float) -> tuple[int, int]:
        if not self.frames:
            raise BFRESAnimationError("Animation curve contains no keys.")

        right = bisect_right(self.frames, frame)

        if right == 0:
            return 0, 0
        if right >= len(self.frames):
            last = len(self.frames) - 1
            return last, last

        return right - 1, right

    def evaluate(self, frame: float) -> float:
        left_index, right_index = self.surrounding_key_indices(frame)
        left_value = self.key_value(left_index)

        if left_index == right_index:
            return left_value

        left_frame = self.frames[left_index]
        right_frame = self.frames[right_index]
        duration = right_frame - left_frame

        if duration <= 0.0:
            return left_value

        factor = (frame - left_frame) / duration

        if self.curve_type in {
            _CURVE_STEP_INT,
            _CURVE_STEP_BOOL,
            _CURVE_BAKED_INT,
            _CURVE_BAKED_BOOL,
        }:
            return left_value

        if self.curve_type in {_CURVE_LINEAR, _CURVE_BAKED_FLOAT}:
            right_value = self.key_value(right_index)
            return left_value + (right_value - left_value) * factor

        if self.curve_type != _CURVE_CUBIC:
            raise BFRESAnimationError(
                f"Unsupported BFRES curve type 0x{self.curve_type:02X}."
            )

        right_value = self.key_value(right_index)
        left_coefficients = self.keys[left_index]
        delta = right_value - left_value
        left_out = left_coefficients[1] * self.scale / duration
        right_in = (
            left_coefficients[3] * self.scale + 2.0 * delta
        ) / duration - left_out
        factor_squared = factor * factor
        factor_cubed = factor_squared * factor
        return (
            (2.0 * factor_cubed - 3.0 * factor_squared + 1.0)
            * left_value
            + (factor_cubed - 2.0 * factor_squared + factor)
            * left_out
            * duration
            + (-2.0 * factor_cubed + 3.0 * factor_squared)
            * right_value
            + (factor_cubed - factor_squared)
            * right_in
            * duration
        )


@dataclass(frozen=True, slots=True)
class BoneAnimation:
    name: str
    segment_scale_compensate: bool
    base_scale: tuple[float, float, float] | None
    base_rotation: tuple[float, float, float, float] | None
    base_translation: tuple[float, float, float] | None
    curves: tuple[AnimationCurve, ...]

    def curve(self, data_name: str) -> AnimationCurve | None:
        return next(
            (curve for curve in self.curves if curve.data_name == data_name),
            None,
        )


@dataclass(frozen=True, slots=True)
class SkeletalAnimation:
    name: str
    path: str
    frame_count: int
    looping: bool
    euler_xyz: bool
    bones: tuple[BoneAnimation, ...]


@dataclass(frozen=True, slots=True)
class BoneVisibilityCurve:
    target_index: int
    frames: tuple[float, ...]
    values: tuple[bool, ...]

    def evaluate(self, frame: float, fallback: bool) -> bool:
        if not self.frames:
            return fallback

        index = bisect_right(self.frames, frame) - 1
        return fallback if index < 0 else self.values[index]


@dataclass(frozen=True, slots=True)
class BoneVisibilityTarget:
    name: str
    base_visible: bool
    curve: BoneVisibilityCurve | None

    def evaluate(self, frame: float) -> bool:
        return (
            self.curve.evaluate(frame, self.base_visible)
            if self.curve is not None
            else self.base_visible
        )


@dataclass(frozen=True, slots=True)
class BoneVisibilityAnimation:
    name: str
    path: str
    frame_count: int
    looping: bool
    targets: tuple[BoneVisibilityTarget, ...]


@dataclass(frozen=True, slots=True)
class MaterialAnimationConstant:
    data_offset: int
    raw_value: int
    float_value: float


@dataclass(frozen=True, slots=True)
class MaterialParameterAnimation:
    name: str
    sub_bind_index: int
    float_curve_count: int
    int_curve_count: int
    curves: tuple[AnimationCurve, ...]
    constants: tuple[MaterialAnimationConstant, ...]

    @property
    def data_offsets(self) -> tuple[int, ...]:
        return tuple(
            item.data_offset for item in (*self.curves, *self.constants)
        )


@dataclass(frozen=True, slots=True)
class MaterialAnimationTarget:
    name: str
    parameters: tuple[MaterialParameterAnimation, ...]


@dataclass(frozen=True, slots=True)
class MaterialAnimation:
    name: str
    path: str
    frame_count: int
    looping: bool
    targets: tuple[MaterialAnimationTarget, ...]

    @property
    def is_color(self) -> bool:
        return self.name.casefold().endswith("_fcl")


@dataclass(frozen=True, slots=True)
class CameraAnimation:
    name: str
    frame_count: int
    looping: bool
    perspective: bool
    euler_zxy: bool
    base_values: tuple[float, ...]
    curves: tuple[AnimationCurve, ...]

    def curve(self, data_name: str) -> AnimationCurve | None:
        return next(
            (
                curve
                for curve in self.curves
                if _CAMERA_DATA_NAMES.get(curve.data_offset) == data_name
            ),
            None,
        )

    def evaluate(self, data_name: str, frame: float) -> float:
        data_offset = next(
            (
                offset
                for offset, name in _CAMERA_DATA_NAMES.items()
                if name == data_name
            ),
            None,
        )

        if data_offset is None:
            raise BFRESAnimationError(
                f"Unknown camera animation channel {data_name!r}."
            )

        curve = self.curve(data_name)
        return (
            curve.evaluate(frame)
            if curve is not None
            else self.base_values[data_offset // 4]
        )


@dataclass(frozen=True, slots=True)
class SceneAnimation:
    name: str
    path: str
    cameras: tuple[CameraAnimation, ...]


def _validate_fres(reader: _Reader) -> None:
    if reader.bytes(0, 4) != b"FRES":
        raise BFRESAnimationError("Data does not start with a BFRES FRES header.")
    if reader.bytes(12, 2) != b"\xFF\xFE":
        raise BFRESAnimationError(
            "Only little-endian Switch BFRES files are supported."
        )

    version = reader.u32(8)
    version_major = (version >> 16) & 0xFF

    if version_major != 8:
        raise BFRESAnimationError(
            "Only Super Mario Odyssey BFRES version 8 is supported; "
            f"found version {version_major}."
        )


def _read_curve(
    reader: _Reader,
    offset: int,
    data_names: dict[int, str] | None = _ANIM_DATA_NAMES,
) -> AnimationCurve:
    frame_values_offset = reader.u64(offset)
    key_values_offset = reader.u64(offset + 8)
    flags = reader.u16(offset + 16)
    key_count = reader.u16(offset + 18)
    data_offset = reader.u32(offset + 20)
    start_frame, end_frame, scale, value_offset = (
        float(value)
        for value in reader.unpack("<ffff", offset + 24)
    )
    frame_type = flags & 0x3
    key_type = flags & 0xC
    curve_type = flags & 0x70

    if data_names is not None and data_offset not in data_names:
        raise BFRESAnimationError(
            f"Unsupported animation data offset 0x{data_offset:X}."
        )
    if not key_count:
        raise BFRESAnimationError("Animation curve declares no keys.")
    if not frame_values_offset or not key_values_offset:
        raise BFRESAnimationError("Animation curve is missing frame or key data.")

    if frame_type == 0:
        frames = tuple(
            float(value)
            for value in reader.unpack(f"<{key_count}f", frame_values_offset)
        )
    elif frame_type == 1:
        frames = tuple(
            float(value) / 32.0
            for value in reader.unpack(f"<{key_count}h", frame_values_offset)
        )
    elif frame_type == 2:
        frames = tuple(
            float(value)
            for value in reader.unpack(f"<{key_count}B", frame_values_offset)
        )
    else:
        raise BFRESAnimationError(
            f"Unsupported BFRES animation frame type {frame_type}."
        )

    if any(not math.isfinite(frame) for frame in frames):
        raise BFRESAnimationError("Animation curve contains a non-finite frame.")
    if any(right <= left for left, right in zip(frames, frames[1:])):
        raise BFRESAnimationError("Animation curve frames are not increasing.")

    elements_per_key = (
        4 if curve_type == _CURVE_CUBIC else 2 if curve_type == _CURVE_LINEAR else 1
    )
    value_count = key_count * elements_per_key

    if key_type == 0:
        value_format = (
            "I"
            if curve_type in {
                _CURVE_STEP_INT,
                _CURVE_BAKED_INT,
                _CURVE_STEP_BOOL,
                _CURVE_BAKED_BOOL,
            }
            else "f"
        )
        raw_values = tuple(
            float(value)
            for value in reader.unpack(
                f"<{value_count}{value_format}",
                key_values_offset,
            )
        )
    elif key_type == 4:
        raw_values = tuple(
            float(value)
            for value in reader.unpack(f"<{value_count}h", key_values_offset)
        )
    elif key_type == 8:
        raw_values = tuple(
            float(value)
            for value in reader.unpack(f"<{value_count}b", key_values_offset)
        )
    else:
        raise BFRESAnimationError(
            f"Unsupported BFRES animation key type 0x{key_type:X}."
        )

    keys = tuple(
        raw_values[index : index + elements_per_key]
        for index in range(0, value_count, elements_per_key)
    )
    effective_scale = scale if scale != 0.0 else 1.0
    return AnimationCurve(
        data_offset=data_offset,
        curve_type=curve_type,
        frames=frames,
        keys=keys,
        scale=effective_scale,
        offset=value_offset,
        start_frame=start_frame,
        end_frame=end_frame,
    )


def _read_bone_animation(reader: _Reader, offset: int) -> BoneAnimation:
    name = reader.pointer_string(offset)
    curve_array_offset = reader.u64(offset + 8)
    base_data_offset = reader.u64(offset + 16)
    flags = reader.u32(offset + 24)
    curve_count = int(reader.unpack("<B", offset + 30)[0])
    base_flags = flags & 0x38
    base_cursor = base_data_offset
    base_scale = None
    base_rotation = None
    base_translation = None

    if base_flags and not base_data_offset:
        raise BFRESAnimationError(
            f"Bone animation {name!r} declares base data but has none."
        )
    if base_flags & 0x08:
        base_scale = tuple(
            float(value) for value in reader.unpack("<fff", base_cursor)
        )
        base_cursor += 12
    if base_flags & 0x10:
        base_rotation = tuple(
            float(value) for value in reader.unpack("<ffff", base_cursor)
        )
        base_cursor += 16
    if base_flags & 0x20:
        base_translation = tuple(
            float(value) for value in reader.unpack("<fff", base_cursor)
        )

    if curve_count and not curve_array_offset:
        raise BFRESAnimationError(
            f"Bone animation {name!r} declares curves but has no curve array."
        )

    curves = tuple(
        _read_curve(reader, curve_array_offset + index * 48)
        for index in range(curve_count)
    )
    data_offsets = [curve.data_offset for curve in curves]

    if len(set(data_offsets)) != len(data_offsets):
        raise BFRESAnimationError(
            f"Bone animation {name!r} repeats a transform curve."
        )

    return BoneAnimation(
        name=name,
        segment_scale_compensate=bool(
            flags & _BONE_TRANSFORM_SEGMENT_SCALE_COMPENSATE
        ),
        base_scale=base_scale,
        base_rotation=base_rotation,
        base_translation=base_translation,
        curves=curves,
    )


def _read_skeletal_animation(
    reader: _Reader,
    offset: int,
) -> SkeletalAnimation:
    if reader.bytes(offset, 4) != b"FSKA":
        raise BFRESAnimationError(f"Expected FSKA at 0x{offset:X}.")

    name = reader.pointer_string(offset + 16)
    path = reader.pointer_string(offset + 24)
    bone_array_offset = reader.u64(offset + 48)
    flags = reader.u32(offset + 72)
    frame_count = reader.i32(offset + 76)
    bone_count = reader.u16(offset + 88)

    if frame_count < 0:
        raise BFRESAnimationError(
            f"Skeletal animation {name!r} has a negative frame count."
        )
    if bone_count and not bone_array_offset:
        raise BFRESAnimationError(
            f"Skeletal animation {name!r} declares bones but has no bone array."
        )

    bones = tuple(
        _read_bone_animation(reader, bone_array_offset + index * 40)
        for index in range(bone_count)
    )
    bone_names = [bone.name for bone in bones]

    if len(set(bone_names)) != len(bone_names):
        raise BFRESAnimationError(
            f"Skeletal animation {name!r} contains duplicate bone names."
        )

    return SkeletalAnimation(
        name=name,
        path=path,
        frame_count=frame_count,
        looping=bool(flags & 0x4),
        euler_xyz=(flags & 0x7000) == 0x1000,
        bones=bones,
    )


def read_skeletal_animations(data: bytes) -> tuple[SkeletalAnimation, ...]:
    reader = _Reader(data)
    _validate_fres(reader)
    animation_array_offset = reader.u64(56)
    animation_dictionary_offset = reader.u64(64)
    animation_count = reader.u16(190)

    if not animation_count:
        return ()
    if not animation_array_offset:
        raise BFRESAnimationError(
            "BFRES declares skeletal animations but has no FSKA array."
        )

    dictionary_names = reader.dictionary_keys(animation_dictionary_offset)

    if len(dictionary_names) != animation_count:
        raise BFRESAnimationError(
            f"BFRES declares {animation_count} skeletal animations but its "
            f"dictionary contains {len(dictionary_names)}."
        )

    animations = tuple(
        _read_skeletal_animation(reader, animation_array_offset + index * 96)
        for index in range(animation_count)
    )

    if tuple(animation.name for animation in animations) != dictionary_names:
        raise BFRESAnimationError(
            "BFRES skeletal-animation array and dictionary names differ."
        )

    return animations

def _read_material_parameter_animation(
    reader: _Reader,
    offset: int,
    curves: tuple[AnimationCurve, ...],
    constants: tuple[MaterialAnimationConstant, ...],
) -> MaterialParameterAnimation:
    name = reader.pointer_string(offset)
    begin_curve = reader.u16(offset + 8)
    float_curve_count = reader.u16(offset + 10)
    int_curve_count = reader.u16(offset + 12)
    begin_constant = reader.u16(offset + 14)
    constant_count = reader.u16(offset + 16)
    sub_bind_index = reader.u16(offset + 18)
    curve_count = float_curve_count + int_curve_count

    if curve_count:
        if begin_curve == 0xFFFF or begin_curve + curve_count > len(curves):
            raise BFRESAnimationError(
                f"Material parameter {name!r} has an invalid curve range."
            )
        parameter_curves = curves[begin_curve : begin_curve + curve_count]
    else:
        parameter_curves = ()

    if constant_count:
        if (
            begin_constant == 0xFFFF
            or begin_constant + constant_count > len(constants)
        ):
            raise BFRESAnimationError(
                f"Material parameter {name!r} has an invalid constant range."
            )
        parameter_constants = constants[
            begin_constant : begin_constant + constant_count
        ]
    else:
        parameter_constants = ()

    data_offsets = [
        item.data_offset
        for item in (*parameter_curves, *parameter_constants)
    ]
    if len(set(data_offsets)) != len(data_offsets):
        raise BFRESAnimationError(
            f"Material parameter {name!r} repeats an animated component."
        )

    return MaterialParameterAnimation(
        name=name,
        sub_bind_index=sub_bind_index,
        float_curve_count=float_curve_count,
        int_curve_count=int_curve_count,
        curves=parameter_curves,
        constants=parameter_constants,
    )


def _read_material_animation_target(
    reader: _Reader,
    offset: int,
) -> MaterialAnimationTarget:
    name = reader.pointer_string(offset)
    parameter_info_offset = reader.u64(offset + 8)
    curve_array_offset = reader.u64(offset + 24)
    constant_array_offset = reader.u64(offset + 32)
    parameter_count = reader.u16(offset + 50)
    constant_count = reader.u16(offset + 54)
    curve_count = reader.u16(offset + 56)

    if parameter_count and not parameter_info_offset:
        raise BFRESAnimationError(
            f"Material animation target {name!r} has no parameter array."
        )
    if curve_count and not curve_array_offset:
        raise BFRESAnimationError(
            f"Material animation target {name!r} has no curve array."
        )
    if constant_count and not constant_array_offset:
        raise BFRESAnimationError(
            f"Material animation target {name!r} has no constant array."
        )

    curves = tuple(
        _read_curve(reader, curve_array_offset + index * 48, None)
        for index in range(curve_count)
    )
    constants = tuple(
        MaterialAnimationConstant(
            data_offset=reader.u32(constant_array_offset + index * 8),
            raw_value=reader.u32(constant_array_offset + index * 8 + 4),
            float_value=float(
                reader.unpack("<f", constant_array_offset + index * 8 + 4)[0]
            ),
        )
        for index in range(constant_count)
    )
    parameters = tuple(
        _read_material_parameter_animation(
            reader,
            parameter_info_offset + index * 24,
            curves,
            constants,
        )
        for index in range(parameter_count)
    )
    parameter_names = [parameter.name for parameter in parameters]
    if len(set(parameter_names)) != len(parameter_names):
        raise BFRESAnimationError(
            f"Material animation target {name!r} repeats a parameter name."
        )

    return MaterialAnimationTarget(name=name, parameters=parameters)


def _read_material_animation(
    reader: _Reader,
    offset: int,
) -> MaterialAnimation:
    if reader.bytes(offset, 4) != b"FMAA":
        raise BFRESAnimationError(f"Expected FMAA at 0x{offset:X}.")

    name = reader.pointer_string(offset + 16)
    path = reader.pointer_string(offset + 24)
    target_array_offset = reader.u64(offset + 48)
    flags = reader.u16(offset + 96)
    target_count = reader.u16(offset + 100)
    frame_count = reader.i32(offset + 104)

    if frame_count < 0:
        raise BFRESAnimationError(
            f"Material animation {name!r} has a negative frame count."
        )
    if target_count and not target_array_offset:
        raise BFRESAnimationError(
            f"Material animation {name!r} declares targets but has no array."
        )

    targets = tuple(
        _read_material_animation_target(
            reader,
            target_array_offset + index * 64,
        )
        for index in range(target_count)
    )
    target_names = [target.name for target in targets]
    if len(set(target_names)) != len(target_names):
        raise BFRESAnimationError(
            f"Material animation {name!r} repeats a material target."
        )

    return MaterialAnimation(
        name=name,
        path=path,
        frame_count=frame_count,
        looping=bool(flags & 0x4),
        targets=targets,
    )


def read_material_animations(data: bytes) -> tuple[MaterialAnimation, ...]:
    reader = _Reader(data)
    _validate_fres(reader)
    animation_array_offset = reader.u64(72)
    animation_dictionary_offset = reader.u64(80)
    animation_count = reader.u16(192)

    if not animation_count:
        return ()
    if not animation_array_offset:
        raise BFRESAnimationError(
            "BFRES declares material animations but has no FMAA array."
        )

    dictionary_names = reader.dictionary_keys(animation_dictionary_offset)
    if len(dictionary_names) != animation_count:
        raise BFRESAnimationError(
            f"BFRES declares {animation_count} material animations but its "
            f"dictionary contains {len(dictionary_names)}."
        )

    animations = tuple(
        _read_material_animation(reader, animation_array_offset + index * 120)
        for index in range(animation_count)
    )
    if tuple(animation.name for animation in animations) != dictionary_names:
        raise BFRESAnimationError(
            "BFRES material-animation array and dictionary names differ."
        )

    return animations


def _read_visibility_curve(
    reader: _Reader,
    offset: int,
) -> BoneVisibilityCurve:
    frame_values_offset = reader.u64(offset)
    key_values_offset = reader.u64(offset + 8)
    flags = reader.u16(offset + 16)
    key_count = reader.u16(offset + 18)
    target_index = reader.u32(offset + 20)
    frame_type = flags & 0x3
    key_type = flags & 0xC
    curve_type = flags & 0x70

    if curve_type not in {_CURVE_STEP_BOOL, _CURVE_BAKED_BOOL}:
        raise BFRESAnimationError(
            "Bone visibility curve uses unsupported type "
            f"0x{curve_type:02X}."
        )
    if key_type != 0:
        raise BFRESAnimationError(
            "Bone visibility curve uses unsupported packed key type "
            f"0x{key_type:X}."
        )
    if not key_count:
        raise BFRESAnimationError("Bone visibility curve declares no keys.")
    if not frame_values_offset or not key_values_offset:
        raise BFRESAnimationError(
            "Bone visibility curve is missing frame or key data."
        )

    if frame_type == 0:
        frames = tuple(
            float(value)
            for value in reader.unpack(f"<{key_count}f", frame_values_offset)
        )
    elif frame_type == 1:
        frames = tuple(
            float(value) / 32.0
            for value in reader.unpack(f"<{key_count}h", frame_values_offset)
        )
    elif frame_type == 2:
        frames = tuple(
            float(value)
            for value in reader.unpack(f"<{key_count}B", frame_values_offset)
        )
    else:
        raise BFRESAnimationError(
            f"Unsupported BFRES visibility frame type {frame_type}."
        )

    if any(not math.isfinite(frame) for frame in frames):
        raise BFRESAnimationError(
            "Bone visibility curve contains a non-finite frame."
        )
    if any(right <= left for left, right in zip(frames, frames[1:])):
        raise BFRESAnimationError(
            "Bone visibility curve frames are not increasing."
        )

    word_count = (key_count + 31) // 32
    words = tuple(
        int(value)
        for value in reader.unpack(f"<{word_count}I", key_values_offset)
    )
    values = tuple(
        bool(words[index // 32] & (1 << (index % 32)))
        for index in range(key_count)
    )
    return BoneVisibilityCurve(
        target_index=target_index,
        frames=frames,
        values=values,
    )


def _read_bone_visibility_animation(
    reader: _Reader,
    offset: int,
) -> BoneVisibilityAnimation:
    if reader.bytes(offset, 4) != b"FBVS":
        raise BFRESAnimationError(f"Expected FBVS at 0x{offset:X}.")

    name = reader.pointer_string(offset + 16)
    path = reader.pointer_string(offset + 24)
    curve_array_offset = reader.u64(offset + 48)
    base_data_offset = reader.u64(offset + 56)
    name_array_offset = reader.u64(offset + 64)
    flags = reader.u16(offset + 88)
    frame_count = reader.i32(offset + 92)
    target_count = reader.u16(offset + 96)
    curve_count = reader.u16(offset + 98)

    if frame_count < 0:
        raise BFRESAnimationError(
            f"Bone visibility animation {name!r} has a negative frame count."
        )
    if target_count and (not base_data_offset or not name_array_offset):
        raise BFRESAnimationError(
            f"Bone visibility animation {name!r} is missing target data."
        )
    if curve_count and not curve_array_offset:
        raise BFRESAnimationError(
            f"Bone visibility animation {name!r} declares curves but has "
            "no curve array."
        )

    target_names = tuple(
        reader.pointer_string(name_array_offset + index * 8)
        for index in range(target_count)
    )

    if len(set(target_names)) != len(target_names):
        raise BFRESAnimationError(
            f"Bone visibility animation {name!r} repeats a target name."
        )

    word_count = (target_count + 31) // 32
    base_words = (
        tuple(
            int(value)
            for value in reader.unpack(f"<{word_count}I", base_data_offset)
        )
        if word_count
        else ()
    )
    base_values = tuple(
        bool(base_words[index // 32] & (1 << (index % 32)))
        for index in range(target_count)
    )
    curves = tuple(
        _read_visibility_curve(reader, curve_array_offset + index * 48)
        for index in range(curve_count)
    )

    if any(curve.target_index >= target_count for curve in curves):
        raise BFRESAnimationError(
            f"Bone visibility animation {name!r} targets an invalid index."
        )
    if len({curve.target_index for curve in curves}) != len(curves):
        raise BFRESAnimationError(
            f"Bone visibility animation {name!r} repeats a target curve."
        )

    curves_by_target = {curve.target_index: curve for curve in curves}
    return BoneVisibilityAnimation(
        name=name,
        path=path,
        frame_count=frame_count,
        looping=bool(flags & 0x4),
        targets=tuple(
            BoneVisibilityTarget(
                name=target_name,
                base_visible=base_visible,
                curve=curves_by_target.get(index),
            )
            for index, (target_name, base_visible) in enumerate(
                zip(target_names, base_values)
            )
        ),
    )


def read_bone_visibility_animations(
    data: bytes,
) -> tuple[BoneVisibilityAnimation, ...]:
    reader = _Reader(data)
    _validate_fres(reader)
    animation_array_offset = reader.u64(88)
    animation_dictionary_offset = reader.u64(96)
    animation_count = reader.u16(194)

    if not animation_count:
        return ()
    if not animation_array_offset:
        raise BFRESAnimationError(
            "BFRES declares bone visibility animations but has no FBVS array."
        )

    dictionary_names = reader.dictionary_keys(animation_dictionary_offset)

    if len(dictionary_names) != animation_count:
        raise BFRESAnimationError(
            f"BFRES declares {animation_count} bone visibility animations "
            f"but its dictionary contains {len(dictionary_names)}."
        )

    animations = tuple(
        _read_bone_visibility_animation(
            reader,
            animation_array_offset + index * 104,
        )
        for index in range(animation_count)
    )

    if tuple(animation.name for animation in animations) != dictionary_names:
        raise BFRESAnimationError(
            "BFRES bone-visibility array and dictionary names differ."
        )

    return animations




def _read_camera_animation(reader: _Reader, offset: int) -> CameraAnimation:
    if reader.bytes(offset, 4) != b"FCAM":
        raise BFRESAnimationError(f"Expected FCAM at 0x{offset:X}.")

    name = reader.pointer_string(offset + 16)
    curve_array_offset = reader.u64(offset + 24)
    base_data_offset = reader.u64(offset + 32)
    flags = reader.u16(offset + 56)
    frame_count = reader.i32(offset + 60)
    curve_count = int(reader.unpack("<B", offset + 64)[0])

    if frame_count < 0:
        raise BFRESAnimationError(
            f"Camera animation {name!r} has a negative frame count."
        )
    if not base_data_offset:
        raise BFRESAnimationError(
            f"Camera animation {name!r} has no base camera data."
        )
    if curve_count and not curve_array_offset:
        raise BFRESAnimationError(
            f"Camera animation {name!r} declares curves but has no curve array."
        )

    base_values = tuple(
        float(value) for value in reader.unpack("<11f", base_data_offset)
    )
    curves = tuple(
        _read_curve(
            reader,
            curve_array_offset + index * 48,
            _CAMERA_DATA_NAMES,
        )
        for index in range(curve_count)
    )
    data_offsets = [curve.data_offset for curve in curves]

    if len(set(data_offsets)) != len(data_offsets):
        raise BFRESAnimationError(
            f"Camera animation {name!r} repeats a camera curve."
        )

    return CameraAnimation(
        name=name,
        frame_count=frame_count,
        looping=bool(flags & 0x4),
        perspective=bool(flags & (1 << 10)),
        euler_zxy=bool(flags & (1 << 8)),
        base_values=base_values,
        curves=curves,
    )


def _read_scene_animation(reader: _Reader, offset: int) -> SceneAnimation:
    if reader.bytes(offset, 4) != b"FSCN":
        raise BFRESAnimationError(f"Expected FSCN at 0x{offset:X}.")

    name = reader.pointer_string(offset + 16)
    path = reader.pointer_string(offset + 24)
    camera_array_offset = reader.u64(offset + 32)
    camera_dictionary_offset = reader.u64(offset + 40)
    camera_count = reader.u16(offset + 98)

    if camera_count and not camera_array_offset:
        raise BFRESAnimationError(
            f"Scene animation {name!r} declares cameras but has no FCAM array."
        )

    dictionary_names = reader.dictionary_keys(camera_dictionary_offset)

    if len(dictionary_names) != camera_count:
        raise BFRESAnimationError(
            f"Scene animation {name!r} declares {camera_count} camera "
            f"animations but its dictionary contains {len(dictionary_names)}."
        )

    cameras = tuple(
        _read_camera_animation(reader, camera_array_offset + index * 72)
        for index in range(camera_count)
    )

    if tuple(camera.name for camera in cameras) != dictionary_names:
        raise BFRESAnimationError(
            f"Scene animation {name!r} camera array and dictionary names differ."
        )

    return SceneAnimation(name=name, path=path, cameras=cameras)


def read_scene_animations(data: bytes) -> tuple[SceneAnimation, ...]:
    reader = _Reader(data)
    _validate_fres(reader)
    animation_array_offset = reader.u64(120)
    animation_dictionary_offset = reader.u64(128)
    animation_count = reader.u16(198)

    if not animation_count:
        return ()
    if not animation_array_offset:
        raise BFRESAnimationError(
            "BFRES declares scene animations but has no FSCN array."
        )

    dictionary_names = reader.dictionary_keys(animation_dictionary_offset)

    if len(dictionary_names) != animation_count:
        raise BFRESAnimationError(
            f"BFRES declares {animation_count} scene animations but its "
            f"dictionary contains {len(dictionary_names)}."
        )

    animations = tuple(
        _read_scene_animation(reader, animation_array_offset + index * 104)
        for index in range(animation_count)
    )

    if tuple(animation.name for animation in animations) != dictionary_names:
        raise BFRESAnimationError(
            "BFRES scene-animation array and dictionary names differ."
        )

    return animations

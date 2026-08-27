from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import struct

from .performance import timed


Vector2 = tuple[float, float]
Vector3 = tuple[float, float, float]
Color4 = tuple[float, float, float, float]
Triangle = tuple[int, int, int]
Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]
Matrix3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]
BoneWeight = tuple[int, float]


@dataclass(slots=True, frozen=True)
class MaterialShaderParameter:
    name: str
    type_id: int
    type_name: str
    value: object


@dataclass(slots=True, frozen=True)
class MaterialRenderInfo:
    name: str
    type_id: int
    values: tuple[object, ...]


@dataclass(slots=True, frozen=True)
class MaterialSamplerState:
    name: str
    wrap_u: int
    wrap_v: int
    wrap_w: int
    compare_func: int
    border_color: int
    anisotropic: int
    filter_flags: int
    min_lod: float
    max_lod: float
    lod_bias: float


@dataclass(slots=True, frozen=True)
class MaterialShaderData:
    shader_archive_name: str
    shading_model_name: str
    texture_bindings: tuple[tuple[str, str], ...]
    attribute_assignments: tuple[tuple[str, str], ...]
    sampler_assignments: tuple[tuple[str, str], ...]
    shader_options: tuple[tuple[str, str], ...]
    parameters: tuple[MaterialShaderParameter, ...]
    render_infos: tuple[MaterialRenderInfo, ...]
    samplers: tuple[MaterialSamplerState, ...]

_FORMAT_8_8_UNORM = 0x0109
_FORMAT_8_8_SNORM = 0x0209
_FORMAT_8_UINT = 0x0302
_FORMAT_8_8_UINT = 0x0309
_FORMAT_8_8_8_8_UNORM = 0x010B
_FORMAT_8_8_8_8_SNORM = 0x020B
_FORMAT_8_8_8_8_UINT = 0x030B
_FORMAT_10_10_10_2_SNORM = 0x020E
_FORMAT_16_16_UNORM = 0x0112
_FORMAT_16_16_SNORM = 0x0212
_FORMAT_16_16_SINGLE = 0x0512
_FORMAT_16_16_16_16_SINGLE = 0x0515
_FORMAT_32_32_SINGLE = 0x0517
_FORMAT_32_32_32_SINGLE = 0x0518


@dataclass(slots=True, frozen=True)
class SkeletonBone:
    name: str
    parent_index: int
    flags: int
    model_matrix: Matrix4
    scale: Vector3 = (1.0, 1.0, 1.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    position: Vector3 = (0.0, 0.0, 0.0)


@dataclass(slots=True, frozen=True)
class ModelSkeleton:
    bones: tuple[SkeletonBone, ...]
    matrix_to_bone: tuple[int, ...]
    smooth_matrix_count: int
    segment_scale_compensate: bool


@dataclass(slots=True, frozen=True)
class StaticMesh:
    name: str
    material_name: str
    albedo_texture_name: str | None
    texture_names: tuple[str, ...]
    texture_sampler_names: tuple[str, ...]
    base_color: Color4 | None
    vertices: tuple[Vector3, ...]
    triangles: tuple[Triangle, ...]
    normals: tuple[Vector3, ...] | None
    uvs: tuple[Vector2, ...] | None
    colour_sets: tuple[tuple[Color4, ...] | None, ...]
    uv_sets: tuple[tuple[Vector2, ...] | None, ...] = ()
    material_shader: MaterialShaderData | None = None
    bone_weights: tuple[tuple[BoneWeight, ...], ...] = ()
    skin_influence_count: int = 0
    base_bone_index: int = 0xFFFF


@dataclass(slots=True, frozen=True)
class StaticModel:
    name: str
    meshes: tuple[StaticMesh, ...]
    skeleton: ModelSkeleton | None = None


class _Reader:
    def __init__(self, data: bytes):
        self.data = data

    def _check(self, offset: int, size: int) -> None:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ValueError(
                f"BFRES read outside file bounds at 0x{offset:X} "
                f"for 0x{size:X} bytes."
            )

    def unpack(self, format_string: str, offset: int) -> tuple[object, ...]:
        size = struct.calcsize(format_string)
        self._check(offset, size)
        return struct.unpack_from(format_string, self.data, offset)

    def bytes(self, offset: int, size: int) -> bytes:
        self._check(offset, size)
        return self.data[offset : offset + size]

    def u16(self, offset: int) -> int:
        return int(self.unpack("<H", offset)[0])

    def i16(self, offset: int) -> int:
        return int(self.unpack("<h", offset)[0])

    def u16_be(self, offset: int) -> int:
        return int(self.unpack(">H", offset)[0])

    def i32(self, offset: int) -> int:
        return int(self.unpack("<i", offset)[0])

    def u32(self, offset: int) -> int:
        return int(self.unpack("<I", offset)[0])

    def u64(self, offset: int) -> int:
        return int(self.unpack("<Q", offset)[0])

    def string(self, offset: int) -> str:
        if offset == 0:
            return ""

        self._check(offset, 2)
        end = self.data.find(b"\0", offset + 2)

        if end == -1:
            raise ValueError(f"Unterminated BFRES string at 0x{offset:X}.")

        try:
            return self.data[offset + 2 : end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Invalid UTF-8 BFRES string at 0x{offset:X}."
            ) from exc

    def pointer_string(self, pointer_offset: int) -> str:
        return self.string(self.u64(pointer_offset))

    def dictionary_keys(self, offset: int) -> tuple[str, ...]:
        if offset == 0:
            return ()

        node_count = self.i32(offset + 4)

        if node_count < 0:
            raise ValueError(
                f"Invalid BFRES dictionary node count at 0x{offset:X}."
            )

        keys = []

        for node_index in range(1, node_count + 1):
            node_offset = offset + 8 + node_index * 16
            keys.append(self.pointer_string(node_offset + 8))

        return tuple(keys)


@dataclass(slots=True, frozen=True)
class _Attribute:
    name: str
    format: int
    offset: int
    buffer_index: int


@dataclass(slots=True, frozen=True)
class _VertexBuffer:
    vertex_count: int
    skin_count: int
    attributes: tuple[_Attribute, ...]
    strides: tuple[int, ...]
    buffers: tuple[bytes, ...]


@dataclass(slots=True, frozen=True)
class _Bone:
    name: str
    parent_index: int
    flags: int
    scale: Vector3
    rotation: tuple[float, float, float, float]
    position: Vector3


@dataclass(slots=True, frozen=True)
class _Skeleton:
    bones: tuple[_Bone, ...]
    bone_matrices: tuple[Matrix4, ...]
    matrix_to_bone: tuple[int, ...]
    smooth_matrix_count: int
    segment_scale_compensate: bool


def _matrix_multiply(left: Matrix4, right: Matrix4) -> Matrix4:
    rows = tuple(
        tuple(
            sum(
                left[row][index] * right[index][column]
                for index in range(4)
            )
            for column in range(4)
        )
        for row in range(4)
    )
    return rows  # type: ignore[return-value]


def _rotation_matrix(
    rotation: tuple[float, float, float, float],
    euler_xyz: bool,
) -> Matrix4:
    if euler_xyz:
        x, y, z, _ = rotation
        cx, sx = math.cos(x), math.sin(x)
        cy, sy = math.cos(y), math.sin(y)
        cz, sz = math.cos(z), math.sin(z)
        return (
            (
                cy * cz,
                cz * sx * sy - cx * sz,
                sx * sz + cx * cz * sy,
                0.0,
            ),
            (
                cy * sz,
                cx * cz + sx * sy * sz,
                cx * sy * sz - cz * sx,
                0.0,
            ),
            (-sy, cy * sx, cx * cy, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )

    x, y, z, w = rotation
    length = math.sqrt(x * x + y * y + z * z + w * w)

    if length == 0.0:
        return (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )

    x, y, z, w = (
        x / length,
        y / length,
        z / length,
        w / length,
    )
    return (
        (
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
            0.0,
        ),
        (
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
            0.0,
        ),
        (
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
            0.0,
        ),
        (0.0, 0.0, 0.0, 1.0),
    )


def _bone_local_matrix(bone: _Bone) -> Matrix4:
    rotation = _rotation_matrix(
        bone.rotation,
        bool(bone.flags & (1 << 12)),
    )
    sx, sy, sz = bone.scale
    px, py, pz = bone.position
    return (
        (
            rotation[0][0] * sx,
            rotation[0][1] * sy,
            rotation[0][2] * sz,
            px,
        ),
        (
            rotation[1][0] * sx,
            rotation[1][1] * sy,
            rotation[1][2] * sz,
            py,
        ),
        (
            rotation[2][0] * sx,
            rotation[2][1] * sy,
            rotation[2][2] * sz,
            pz,
        ),
        (0.0, 0.0, 0.0, 1.0),
    )


def _inverse_scale_matrix(scale: Vector3, bone_name: str) -> Matrix4:
    if any(abs(component) < 1e-12 for component in scale):
        raise ValueError(
            f"Cannot compensate zero scale on FSKL bone {bone_name!r}."
        )

    return (
        (1.0 / scale[0], 0.0, 0.0, 0.0),
        (0.0, 1.0 / scale[1], 0.0, 0.0),
        (0.0, 0.0, 1.0 / scale[2], 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _read_skeleton(
    reader: _Reader,
    offset: int,
) -> _Skeleton:
    if offset == 0:
        return _Skeleton(
            bones=(),
            bone_matrices=(),
            matrix_to_bone=(),
            smooth_matrix_count=0,
            segment_scale_compensate=False,
        )

    if reader.bytes(offset, 4) != b"FSKL":
        raise ValueError(f"Expected FSKL at 0x{offset:X}.")

    bone_array_offset = reader.u64(offset + 24)
    matrix_to_bone_offset = reader.u64(offset + 32)
    skeleton_flags = reader.u32(offset + 72)
    bone_count = reader.u16(offset + 76)
    smooth_matrix_count = reader.u16(offset + 78)
    rigid_matrix_count = reader.u16(offset + 80)
    matrix_count = smooth_matrix_count + rigid_matrix_count
    segment_scale_compensate = skeleton_flags & 0x300 == 0x200

    if bone_count and not bone_array_offset:
        raise ValueError("FSKL declares bones but has no bone array.")

    if matrix_count and not matrix_to_bone_offset:
        raise ValueError("FSKL declares skinning matrices but has no palette.")

    matrix_to_bone = tuple(
        int(value)
        for value in reader.unpack(
            f"<{matrix_count}H",
            matrix_to_bone_offset,
        )
    )

    if any(index >= bone_count for index in matrix_to_bone):
        raise ValueError("FSKL matrix palette references a missing bone.")

    bones = tuple(
        _Bone(
            name=reader.pointer_string(bone_array_offset + index * 96),
            parent_index=reader.i16(
                bone_array_offset + index * 96 + 42
            ),
            flags=reader.u32(bone_array_offset + index * 96 + 52),
            scale=tuple(
                float(value)
                for value in reader.unpack(
                    "<fff",
                    bone_array_offset + index * 96 + 56,
                )
            ),
            rotation=tuple(
                float(value)
                for value in reader.unpack(
                    "<ffff",
                    bone_array_offset + index * 96 + 68,
                )
            ),
            position=tuple(
                float(value)
                for value in reader.unpack(
                    "<fff",
                    bone_array_offset + index * 96 + 84,
                )
            ),
        )
        for index in range(bone_count)
    )
    matrices: list[Matrix4 | None] = [None] * bone_count
    visiting: set[int] = set()

    def resolve(index: int) -> Matrix4:
        existing = matrices[index]

        if existing is not None:
            return existing

        if index in visiting:
            raise ValueError(
                f"FSKL bone hierarchy contains a cycle at bone "
                f"{bones[index].name!r}."
            )

        visiting.add(index)
        bone = bones[index]
        local = _bone_local_matrix(bone)

        if bone.parent_index == -1:
            result = local
        elif 0 <= bone.parent_index < bone_count:
            parent = bones[bone.parent_index]
            parent_matrix = resolve(bone.parent_index)

            if segment_scale_compensate:
                parent_matrix = _matrix_multiply(
                    parent_matrix,
                    _inverse_scale_matrix(parent.scale, parent.name),
                )

            result = _matrix_multiply(parent_matrix, local)
        else:
            raise ValueError(
                f"FSKL bone {bone.name!r} references invalid parent "
                f"{bone.parent_index}."
            )

        visiting.remove(index)
        matrices[index] = result
        return result

    return _Skeleton(
        bones=bones,
        bone_matrices=tuple(resolve(index) for index in range(bone_count)),
        matrix_to_bone=matrix_to_bone,
        smooth_matrix_count=smooth_matrix_count,
        segment_scale_compensate=segment_scale_compensate,
    )


def _transform_position(matrix: Matrix4, vector: Vector3) -> Vector3:
    x, y, z = vector
    return (
        matrix[0][0] * x
        + matrix[0][1] * y
        + matrix[0][2] * z
        + matrix[0][3],
        matrix[1][0] * x
        + matrix[1][1] * y
        + matrix[1][2] * z
        + matrix[1][3],
        matrix[2][0] * x
        + matrix[2][1] * y
        + matrix[2][2] * z
        + matrix[2][3],
    )


@lru_cache(maxsize=512)
def _normal_transform_coefficients(
    matrix: Matrix4,
) -> tuple[Matrix3, float]:
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    cofactors: Matrix3 = (
        (e * i - f * h, f * g - d * i, d * h - e * g),
        (c * h - b * i, a * i - c * g, b * g - a * h),
        (b * f - c * e, c * d - a * f, a * e - b * d),
    )
    determinant = (
        a * cofactors[0][0]
        + b * cofactors[0][1]
        + c * cofactors[0][2]
    )
    return cofactors, determinant


def _transform_normal(matrix: Matrix4, vector: Vector3) -> Vector3:
    cofactors, determinant = _normal_transform_coefficients(matrix)

    if abs(determinant) < 1e-12:
        a, b, c = matrix[0][:3]
        d, e, f = matrix[1][:3]
        g, h, i = matrix[2][:3]
        return _normalise(
            (
                a * vector[0] + b * vector[1] + c * vector[2],
                d * vector[0] + e * vector[1] + f * vector[2],
                g * vector[0] + h * vector[1] + i * vector[2],
            )
        )

    transformed = tuple(
        sum(row[index] * vector[index] for index in range(3))
        / determinant
        for row in cofactors
    )
    return _normalise(transformed)  # type: ignore[arg-type]


def _sign_extend(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit


def _normalise(vector: Vector3) -> Vector3:
    length = math.sqrt(sum(component * component for component in vector))

    if length == 0.0:
        return vector

    return (
        vector[0] / length,
        vector[1] / length,
        vector[2] / length,
    )


def _decode_attribute(
    data: bytes,
    offset: int,
    format_value: int,
) -> tuple[float, float, float, float]:
    local = _Reader(data)

    if format_value == _FORMAT_32_32_32_SINGLE:
        x, y, z = local.unpack("<fff", offset)
        return float(x), float(y), float(z), 0.0

    if format_value == _FORMAT_32_32_SINGLE:
        x, y = local.unpack("<ff", offset)
        return float(x), float(y), 0.0, 0.0

    if format_value == _FORMAT_16_16_SINGLE:
        x, y = local.unpack("<ee", offset)
        return float(x), float(y), 0.0, 0.0

    if format_value == _FORMAT_16_16_16_16_SINGLE:
        x, y, z, w = local.unpack("<eeee", offset)
        return float(x), float(y), float(z), float(w)

    if format_value == _FORMAT_8_UINT:
        x = local.unpack("<B", offset)[0]
        return float(x), 0.0, 0.0, 0.0

    if format_value == _FORMAT_8_8_UINT:
        x, y = local.unpack("<BB", offset)
        return float(x), float(y), 0.0, 0.0

    if format_value == _FORMAT_8_8_8_8_UINT:
        x, y, z, w = local.unpack("<BBBB", offset)
        return float(x), float(y), float(z), float(w)

    if format_value == _FORMAT_16_16_UNORM:
        x, y = local.unpack("<HH", offset)
        return int(x) / 65535.0, int(y) / 65535.0, 0.0, 0.0

    if format_value == _FORMAT_16_16_SNORM:
        x, y = local.unpack("<hh", offset)
        return (
            max(-1.0, int(x) / 32767.0),
            max(-1.0, int(y) / 32767.0),
            0.0,
            0.0,
        )

    if format_value == _FORMAT_8_8_UNORM:
        x, y = local.unpack("<BB", offset)
        return int(x) / 255.0, int(y) / 255.0, 0.0, 0.0

    if format_value == _FORMAT_8_8_SNORM:
        x, y = local.unpack("<bb", offset)
        return (
            max(-1.0, int(x) / 127.0),
            max(-1.0, int(y) / 127.0),
            0.0,
            0.0,
        )

    if format_value == _FORMAT_8_8_8_8_UNORM:
        x, y, z, w = local.unpack("<BBBB", offset)
        return (
            int(x) / 255.0,
            int(y) / 255.0,
            int(z) / 255.0,
            int(w) / 255.0,
        )

    if format_value == _FORMAT_8_8_8_8_SNORM:
        x, y, z, w = local.unpack("<bbbb", offset)
        return (
            max(-1.0, int(x) / 127.0),
            max(-1.0, int(y) / 127.0),
            max(-1.0, int(z) / 127.0),
            max(-1.0, int(w) / 127.0),
        )

    if format_value == _FORMAT_10_10_10_2_SNORM:
        packed = local.u32(offset)
        return (
            max(-1.0, _sign_extend(packed & 0x3FF, 10) / 511.0),
            max(-1.0, _sign_extend((packed >> 10) & 0x3FF, 10) / 511.0),
            max(-1.0, _sign_extend((packed >> 20) & 0x3FF, 10) / 511.0),
            float(_sign_extend((packed >> 30) & 0x3, 2)),
        )

    raise NotImplementedError(
        f"Unsupported Switch BFRES vertex format 0x{format_value:04X}."
    )


def _read_vertex_buffer(
    reader: _Reader,
    offset: int,
    buffer_base: int,
) -> _VertexBuffer:
    if reader.bytes(offset, 4) != b"FVTX":
        raise ValueError(f"Expected FVTX at 0x{offset:X}.")

    attribute_values_offset = reader.u64(offset + 16)
    buffer_size_array_offset = reader.u64(offset + 56)
    stride_array_offset = reader.u64(offset + 64)
    buffer_offset = reader.i32(offset + 80)
    attribute_count = reader.bytes(offset + 84, 1)[0]
    buffer_count = reader.bytes(offset + 85, 1)[0]
    vertex_count = reader.u32(offset + 88)
    skin_count = reader.u16(offset + 92)

    attributes = tuple(
        _Attribute(
            name=reader.pointer_string(attribute_values_offset + index * 16),
            format=reader.u16_be(
                attribute_values_offset + index * 16 + 8
            ),
            offset=reader.u16(attribute_values_offset + index * 16 + 12),
            buffer_index=reader.u16(
                attribute_values_offset + index * 16 + 14
            ),
        )
        for index in range(attribute_count)
    )
    sizes = tuple(
        reader.u32(buffer_size_array_offset + index * 16)
        for index in range(buffer_count)
    )
    strides = tuple(
        reader.u32(stride_array_offset + index * 16)
        for index in range(buffer_count)
    )
    buffers = []
    data_offset = buffer_base + buffer_offset

    for size in sizes:
        data_offset = (data_offset + 7) & ~7
        buffers.append(reader.bytes(data_offset, size))
        data_offset += size

    for attribute in attributes:
        if attribute.buffer_index >= len(buffers):
            raise ValueError(
                f"Vertex attribute {attribute.name!r} references missing "
                f"buffer {attribute.buffer_index}."
            )

    return _VertexBuffer(
        vertex_count=vertex_count,
        skin_count=skin_count,
        attributes=attributes,
        strides=strides,
        buffers=tuple(buffers),
    )


def _attribute_values(
    reader: _Reader,
    vertex_buffer: _VertexBuffer,
    name: str,
) -> tuple[tuple[float, float, float, float], ...] | None:
    attribute = next(
        (
            candidate
            for candidate in vertex_buffer.attributes
            if candidate.name == name
        ),
        None,
    )

    if attribute is None:
        return None

    stride = vertex_buffer.strides[attribute.buffer_index]
    data = vertex_buffer.buffers[attribute.buffer_index]

    return tuple(
        _decode_attribute(
            data,
            attribute.offset + vertex_index * stride,
            attribute.format,
        )
        for vertex_index in range(vertex_buffer.vertex_count)
    )


def _read_mesh_triangles(
    reader: _Reader,
    mesh_offset: int,
    buffer_base: int,
    vertex_count: int,
) -> tuple[Triangle, ...]:
    buffer_size_offset = reader.u64(mesh_offset + 24)
    face_buffer_offset = reader.u32(mesh_offset + 32)
    primitive_type = reader.u32(mesh_offset + 36)
    index_format = reader.u32(mesh_offset + 40)
    index_count = reader.u32(mesh_offset + 44)
    first_vertex = reader.u32(mesh_offset + 48)

    if primitive_type != 3:
        raise NotImplementedError(
            f"Unsupported BFRES primitive type {primitive_type}; "
            "the static importer currently supports triangle lists only."
        )

    if index_count % 3 != 0:
        raise ValueError(
            f"Triangle-list index count {index_count} is not divisible by 3."
        )

    if index_format == 1:
        format_string = f"<{index_count}H"
    elif index_format == 2:
        format_string = f"<{index_count}I"
    else:
        raise NotImplementedError(
            f"Unsupported Switch BFRES index format {index_format}."
        )

    buffer_size = reader.u32(buffer_size_offset)
    expected_size = struct.calcsize(format_string)

    if buffer_size < expected_size:
        raise ValueError(
            f"BFRES index buffer is {buffer_size} bytes; "
            f"{expected_size} bytes are required."
        )

    indices = tuple(
        int(value) + first_vertex
        for value in reader.unpack(
            format_string,
            buffer_base + face_buffer_offset,
        )
    )

    if indices and max(indices) >= vertex_count:
        raise ValueError(
            f"BFRES index {max(indices)} exceeds vertex count {vertex_count}."
        )

    return tuple(
        (indices[index], indices[index + 1], indices[index + 2])
        for index in range(0, len(indices), 3)
    )


def _attribute_index(value: float, shape_name: str) -> int:
    index = int(value)

    if index < 0 or float(index) != value:
        raise ValueError(
            f"Shape {shape_name!r} has invalid skin matrix index {value!r}."
        )

    return index


def _shape_vertex_bindings(
    reader: _Reader,
    vertex_buffer: _VertexBuffer,
    skeleton: _Skeleton,
    bone_index: int,
    skin_count: int,
    shape_name: str,
    used_indices: tuple[int, ...],
    include_rigging: bool,
) -> tuple[
    tuple[Matrix4 | None, ...],
    tuple[tuple[BoneWeight, ...], ...],
]:
    if skin_count == 0:
        if bone_index == 0xFFFF:
            matrix = None
            binding: tuple[BoneWeight, ...] = ()
        elif bone_index < len(skeleton.bone_matrices):
            matrix = skeleton.bone_matrices[bone_index]
            binding = ((bone_index, 1.0),)
        else:
            raise ValueError(
                f"Shape {shape_name!r} references missing base bone "
                f"{bone_index}."
            )

        return (
            (matrix,) * len(used_indices),
            (
                (binding,) * len(used_indices)
                if include_rigging
                else ()
            ),
        )

    index_values = _attribute_values(reader, vertex_buffer, "_i0")

    if index_values is None:
        raise ValueError(
            f"Skinned shape {shape_name!r} has no _i0 matrix indices."
        )

    if skin_count == 1:
        matrices = []
        bindings = [] if include_rigging else None

        for vertex_index in used_indices:
            matrix_index = _attribute_index(
                index_values[vertex_index][0],
                shape_name,
            )

            if matrix_index >= len(skeleton.matrix_to_bone):
                raise ValueError(
                    f"Shape {shape_name!r} references missing skin matrix "
                    f"{matrix_index}."
                )

            mapped_bone = skeleton.matrix_to_bone[matrix_index]

            if bindings is not None:
                bindings.append(((mapped_bone, 1.0),))

            matrices.append(
                None
                if matrix_index < skeleton.smooth_matrix_count
                else skeleton.bone_matrices[mapped_bone]
            )

        return (
            tuple(matrices),
            tuple(bindings) if bindings is not None else (),
        )

    if skin_count > 4:
        raise NotImplementedError(
            f"Shape {shape_name!r} uses {skin_count} skin influences; "
            "the importer currently supports up to four."
        )

    weight_values = _attribute_values(reader, vertex_buffer, "_w0")

    if weight_values is None:
        raise ValueError(
            f"Skinned shape {shape_name!r} has no _w0 skin weights."
        )

    if not include_rigging:
        for vertex_index in used_indices:
            weights = weight_values[vertex_index][:skin_count]

            if sum(weights) <= 0.0:
                raise ValueError(
                    f"Shape {shape_name!r} has a vertex with no skin weight."
                )

            for value, weight in zip(
                index_values[vertex_index][:skin_count],
                weights,
            ):
                if weight <= 0.0:
                    continue

                matrix_index = _attribute_index(value, shape_name)

                if matrix_index >= skeleton.smooth_matrix_count:
                    raise ValueError(
                        f"Shape {shape_name!r} blends non-smooth skin "
                        f"matrix {matrix_index}."
                    )

        return (None,) * len(used_indices), ()

    bindings = []

    for vertex_index in used_indices:
        weights = tuple(
            float(weight)
            for weight in weight_values[vertex_index][:skin_count]
        )
        total_weight = sum(weights)

        if total_weight <= 0.0:
            raise ValueError(
                f"Shape {shape_name!r} has a vertex with no skin weight."
            )

        by_bone: dict[int, float] = {}

        for value, weight in zip(
            index_values[vertex_index][:skin_count],
            weights,
        ):
            if weight <= 0.0:
                continue

            matrix_index = _attribute_index(value, shape_name)

            if matrix_index >= skeleton.smooth_matrix_count:
                raise ValueError(
                    f"Shape {shape_name!r} blends non-smooth skin matrix "
                    f"{matrix_index}."
                )

            mapped_bone = skeleton.matrix_to_bone[matrix_index]
            by_bone[mapped_bone] = by_bone.get(mapped_bone, 0.0) + weight

        positive_total = sum(by_bone.values())

        if positive_total <= 0.0:
            raise ValueError(
                f"Shape {shape_name!r} has no positive skin weights."
            )

        bindings.append(
            tuple(
                (bone, weight / positive_total)
                for bone, weight in by_bone.items()
            )
        )

    return (None,) * len(used_indices), tuple(bindings)


def _read_model(
    reader: _Reader,
    offset: int,
    buffer_base: int,
    include_rigging: bool,
) -> StaticModel:
    if reader.bytes(offset, 4) != b"FMDL":
        raise ValueError(f"Expected FMDL at 0x{offset:X}.")

    name = reader.pointer_string(offset + 16)
    skeleton_offset = reader.u64(offset + 32)
    vertex_buffer_array_offset = reader.u64(offset + 40)
    shape_values_offset = reader.u64(offset + 48)
    material_values_offset = reader.u64(offset + 64)
    material_dict_offset = reader.u64(offset + 72)
    vertex_buffer_count = reader.u16(offset + 104)
    shape_count = reader.u16(offset + 106)
    material_count = reader.u16(offset + 108)
    skeleton = _read_skeleton(reader, skeleton_offset)
    model_skeleton = (
        ModelSkeleton(
            bones=tuple(
                SkeletonBone(
                    name=bone.name,
                    parent_index=bone.parent_index,
                    flags=bone.flags,
                    model_matrix=matrix,
                    scale=bone.scale,
                    rotation=bone.rotation,
                    position=bone.position,
                )
                for bone, matrix in zip(
                    skeleton.bones,
                    skeleton.bone_matrices,
                )
            ),
            matrix_to_bone=skeleton.matrix_to_bone,
            smooth_matrix_count=skeleton.smooth_matrix_count,
            segment_scale_compensate=skeleton.segment_scale_compensate,
        )
        if include_rigging and skeleton.bones
        else None
    )
    vertex_buffers = tuple(
        _read_vertex_buffer(
            reader,
            vertex_buffer_array_offset + index * 96,
            buffer_base,
        )
        for index in range(vertex_buffer_count)
    )
    material_names = reader.dictionary_keys(material_dict_offset)

    if len(material_names) != material_count:
        raise ValueError(
            f"FMDL declares {material_count} materials but its dictionary "
            f"contains {len(material_names)}."
        )

    material_appearances = _read_material_appearances(
        reader,
        material_values_offset,
        material_names,
    )
    meshes = []

    for shape_index in range(shape_count):
        shape_offset = shape_values_offset + shape_index * 112

        if reader.bytes(shape_offset, 4) != b"FSHP":
            raise ValueError(f"Expected FSHP at 0x{shape_offset:X}.")

        shape_name = reader.pointer_string(shape_offset + 16)
        mesh_array_offset = reader.u64(shape_offset + 32)
        material_index = reader.u16(shape_offset + 94)
        bone_index = reader.u16(shape_offset + 96)
        vertex_buffer_index = reader.u16(shape_offset + 98)
        shape_skin_count = reader.bytes(shape_offset + 102, 1)[0]
        lod_count = reader.bytes(shape_offset + 103, 1)[0]

        if vertex_buffer_index >= len(vertex_buffers):
            raise ValueError(
                f"Shape {shape_name!r} references missing vertex buffer "
                f"{vertex_buffer_index}."
            )

        if material_index >= len(material_names):
            raise ValueError(
                f"Shape {shape_name!r} references missing material "
                f"{material_index}."
            )

        vertex_buffer = vertex_buffers[vertex_buffer_index]

        if vertex_buffer.skin_count != shape_skin_count:
            raise ValueError(
                f"Shape {shape_name!r} declares {shape_skin_count} skin "
                f"influences but its vertex buffer declares "
                f"{vertex_buffer.skin_count}."
            )

        if lod_count == 0:
            continue

        positions = _attribute_values(reader, vertex_buffer, "_p0")

        if positions is None:
            raise ValueError(f"Shape {shape_name!r} has no _p0 positions.")

        normal_values = _attribute_values(reader, vertex_buffer, "_n0")
        uv_values = tuple(
            _attribute_values(reader, vertex_buffer, f"_u{index}")
            for index in range(8)
        )
        colour_values = tuple(
            _attribute_values(reader, vertex_buffer, f"_c{index}")
            for index in range(4)
        )
        raw_vertices = tuple(
            (value[0], value[1], value[2]) for value in positions
        )
        raw_normals = (
            tuple(
                _normalise((value[0], value[1], value[2]))
                for value in normal_values
            )
            if normal_values is not None
            else None
        )
        raw_uv_sets = tuple(
            (
                tuple((value[0], value[1]) for value in values)
                if values is not None
                else None
            )
            for values in uv_values
        )
        source_triangles = _read_mesh_triangles(
            reader,
            mesh_array_offset,
            buffer_base,
            len(raw_vertices),
        )
        used_indices = tuple(
            sorted(
                {
                    vertex_index
                    for triangle in source_triangles
                    for vertex_index in triangle
                }
            )
        )
        vertex_map = {
            source_index: target_index
            for target_index, source_index in enumerate(used_indices)
        }

        vertex_matrices, bone_weights = _shape_vertex_bindings(
            reader,
            vertex_buffer,
            skeleton,
            bone_index,
            shape_skin_count,
            shape_name,
            used_indices,
            include_rigging,
        )

        vertices = tuple(
            _transform_position(matrix, raw_vertices[index])
            if matrix is not None
            else raw_vertices[index]
            for index, matrix in zip(used_indices, vertex_matrices)
        )
        normals = (
            tuple(
                _transform_normal(matrix, raw_normals[index])
                if matrix is not None
                else raw_normals[index]
                for index, matrix in zip(used_indices, vertex_matrices)
            )
            if raw_normals is not None
            else None
        )
        uv_sets = tuple(
            (
                tuple(raw_uvs[index] for index in used_indices)
                if raw_uvs is not None
                else None
            )
            for raw_uvs in raw_uv_sets
        )
        uvs = uv_sets[0]
        colour_sets = tuple(
            (
                tuple(
                    (
                        float(values[index][0]),
                        float(values[index][1]),
                        float(values[index][2]),
                        float(values[index][3]),
                    )
                    for index in used_indices
                )
                if values is not None
                else None
            )
            for values in colour_values
        )
        triangles = tuple(
            (
                vertex_map[triangle[0]],
                vertex_map[triangle[1]],
                vertex_map[triangle[2]],
            )
            for triangle in source_triangles
        )
        meshes.append(
            StaticMesh(
                name=shape_name,
                material_name=material_names[material_index],
                albedo_texture_name=material_appearances[
                    material_index
                ][0],
                texture_names=material_appearances[material_index][1],
                texture_sampler_names=material_appearances[
                    material_index
                ][2],
                base_color=material_appearances[material_index][3],
                vertices=vertices,
                triangles=triangles,
                normals=normals,
                uvs=uvs,
                colour_sets=colour_sets,
                uv_sets=uv_sets,
                material_shader=material_appearances[material_index][4],
                bone_weights=bone_weights if include_rigging else (),
                skin_influence_count=shape_skin_count,
                base_bone_index=bone_index,
            )
        )

    return StaticModel(
        name=name,
        meshes=tuple(meshes),
        skeleton=model_skeleton if include_rigging else None,
    )


def _validated_dictionary_keys(
    reader: _Reader,
    dictionary_offset: int,
    declared_count: int,
    label: str,
) -> tuple[str, ...]:
    keys = reader.dictionary_keys(dictionary_offset)

    if len(keys) != declared_count:
        raise ValueError(
            f"{label} declares {declared_count} entries but its dictionary "
            f"contains {len(keys)}."
        )

    return keys


def _string_assignments(
    reader: _Reader,
    values_offset: int,
    dictionary_offset: int,
    declared_count: int,
    label: str,
) -> tuple[tuple[str, str], ...]:
    keys = _validated_dictionary_keys(
        reader,
        dictionary_offset,
        declared_count,
        label,
    )

    if declared_count and not values_offset:
        raise ValueError(f"{label} has values but its value offset is zero.")

    return tuple(
        (name, reader.pointer_string(values_offset + index * 8))
        for index, name in enumerate(keys)
    )


_SHADER_PARAMETER_TYPE_NAMES = (
    "Bool", "Bool2", "Bool3", "Bool4",
    "Int", "Int2", "Int3", "Int4",
    "UInt", "UInt2", "UInt3", "UInt4",
    "Float", "Float2", "Float3", "Float4",
    "Reserved2", "Float2x2", "Float2x3", "Float2x4",
    "Reserved3", "Float3x2", "Float3x3", "Float3x4",
    "Reserved4", "Float4x2", "Float4x3", "Float4x4",
    "Srt2D", "Srt3D", "TexSrt", "TexSrtEx",
)
_FLOAT_PARAMETER_COUNTS = {
    12: 1, 13: 2, 14: 3, 15: 4,
    17: 4, 18: 6, 19: 8,
    21: 6, 22: 9, 23: 12,
    25: 8, 26: 12, 27: 16,
    28: 5, 29: 9,
}


def _scalar_or_tuple(values: tuple[object, ...]) -> object:
    return values[0] if len(values) == 1 else values


def _read_shader_parameter_value(
    reader: _Reader,
    data_offset: int,
    source_size: int,
    parameter_offset: int,
    type_id: int,
    declared_size: int,
) -> object:
    value_offset = reader.u16(parameter_offset + 18)

    if value_offset + declared_size > source_size:
        raise ValueError(
            "Shader parameter data extends beyond the declared FMAT "
            f"source buffer ({value_offset} + {declared_size} > "
            f"{source_size})."
        )

    absolute_offset = data_offset + value_offset

    if 0 <= type_id <= 3:
        count = type_id + 1
        if declared_size < count:
            raise ValueError("Boolean shader parameter is smaller than its type.")
        return _scalar_or_tuple(
            tuple(bool(value) for value in reader.bytes(absolute_offset, count))
        )

    if 4 <= type_id <= 7:
        count = type_id - 3
        if declared_size < count * 4:
            raise ValueError("Integer shader parameter is smaller than its type.")
        return _scalar_or_tuple(tuple(reader.unpack(f"<{count}i", absolute_offset)))

    if 8 <= type_id <= 11:
        count = type_id - 7
        if declared_size < count * 4:
            raise ValueError(
                "Unsigned integer shader parameter is smaller than its type."
            )
        return _scalar_or_tuple(tuple(reader.unpack(f"<{count}I", absolute_offset)))

    float_count = _FLOAT_PARAMETER_COUNTS.get(type_id)
    if float_count is not None:
        if declared_size < float_count * 4:
            raise ValueError("Float shader parameter is smaller than its type.")
        return _scalar_or_tuple(
            tuple(
                float(value)
                for value in reader.unpack(f"<{float_count}f", absolute_offset)
            )
        )

    if type_id in {16, 20, 24}:
        required_size = {16: 2, 20: 3, 24: 4}[type_id]
        if declared_size < required_size:
            raise ValueError("Reserved shader parameter is smaller than its type.")
        return reader.bytes(absolute_offset, declared_size)

    if type_id in {30, 31}:
        required_size = 24 if type_id == 30 else 28
        if declared_size < required_size:
            raise ValueError(
                "Texture-transform shader parameter is smaller than its type."
            )
        mode = reader.i32(absolute_offset)
        transform = tuple(
            float(value)
            for value in reader.unpack("<5f", absolute_offset + 4)
        )
        if type_id == 31:
            return (mode, *transform, reader.u32(absolute_offset + 24))
        return (mode, *transform)

    return reader.bytes(absolute_offset, declared_size)


def _read_shader_parameters(
    reader: _Reader,
    material_offset: int,
) -> tuple[MaterialShaderParameter, ...]:
    values_offset = reader.u64(material_offset + 88)
    dictionary_offset = reader.u64(material_offset + 96)
    data_offset = reader.u64(material_offset + 104)
    parameter_count = reader.u16(material_offset + 170)
    source_size = reader.u16(material_offset + 174)
    names = _validated_dictionary_keys(
        reader, dictionary_offset, parameter_count, "FMAT shader parameters"
    )

    if parameter_count and (not values_offset or not data_offset):
        raise ValueError(
            "FMAT has shader parameters but a parameter offset is zero."
        )

    parameters = []
    for index, name in enumerate(names):
        parameter_offset = values_offset + index * 32
        embedded_name = reader.pointer_string(parameter_offset + 8)
        if embedded_name and embedded_name != name:
            raise ValueError(
                f"Shader parameter {index} is named {embedded_name!r}; "
                f"expected {name!r}."
            )
        type_id = reader.bytes(parameter_offset + 16, 1)[0]
        declared_size = reader.bytes(parameter_offset + 17, 1)[0]
        type_name = (
            _SHADER_PARAMETER_TYPE_NAMES[type_id]
            if type_id < len(_SHADER_PARAMETER_TYPE_NAMES)
            else f"Unknown{type_id}"
        )
        parameters.append(
            MaterialShaderParameter(
                name=name,
                type_id=type_id,
                type_name=type_name,
                value=_read_shader_parameter_value(
                    reader, data_offset, source_size, parameter_offset,
                    type_id, declared_size,
                ),
            )
        )
    return tuple(parameters)


def _read_render_infos(
    reader: _Reader,
    material_offset: int,
) -> tuple[MaterialRenderInfo, ...]:
    values_offset = reader.u64(material_offset + 24)
    dictionary_offset = reader.u64(material_offset + 32)
    render_info_count = reader.u16(material_offset + 166)
    names = _validated_dictionary_keys(
        reader, dictionary_offset, render_info_count, "FMAT render infos"
    )

    if render_info_count and not values_offset:
        raise ValueError("FMAT has render infos but its value offset is zero.")

    infos = []
    for index, name in enumerate(names):
        info_offset = values_offset + index * 24
        embedded_name = reader.pointer_string(info_offset)
        if embedded_name and embedded_name != name:
            raise ValueError(
                f"Render info {index} is named {embedded_name!r}; "
                f"expected {name!r}."
            )
        data_offset = reader.u64(info_offset + 8)
        count = reader.u16(info_offset + 16)
        type_id = reader.bytes(info_offset + 18, 1)[0]
        if not data_offset:
            values: tuple[object, ...] = ()
        elif type_id == 0:
            values = tuple(reader.unpack(f"<{count}i", data_offset))
        elif type_id == 1:
            values = tuple(
                float(value)
                for value in reader.unpack(f"<{count}f", data_offset)
            )
        elif type_id == 2:
            values = tuple(
                reader.pointer_string(data_offset + value_index * 8)
                for value_index in range(count)
            )
        else:
            raise ValueError(
                f"Unsupported render info type {type_id} for {name!r}."
            )
        infos.append(MaterialRenderInfo(name, type_id, values))
    return tuple(infos)


def _read_sampler_states(
    reader: _Reader,
    material_offset: int,
) -> tuple[MaterialSamplerState, ...]:
    values_offset = reader.u64(material_offset + 72)
    dictionary_offset = reader.u64(material_offset + 80)
    sampler_count = reader.bytes(material_offset + 169, 1)[0]
    names = _validated_dictionary_keys(
        reader, dictionary_offset, sampler_count, "FMAT samplers"
    )

    if sampler_count and not values_offset:
        raise ValueError("FMAT has samplers but its value offset is zero.")

    return tuple(
        MaterialSamplerState(
            name=name,
            wrap_u=reader.bytes(values_offset + index * 32, 1)[0],
            wrap_v=reader.bytes(values_offset + index * 32 + 1, 1)[0],
            wrap_w=reader.bytes(values_offset + index * 32 + 2, 1)[0],
            compare_func=reader.bytes(values_offset + index * 32 + 3, 1)[0],
            border_color=reader.bytes(values_offset + index * 32 + 4, 1)[0],
            anisotropic=reader.bytes(values_offset + index * 32 + 5, 1)[0],
            filter_flags=reader.u16(values_offset + index * 32 + 6),
            min_lod=float(reader.unpack("<f", values_offset + index * 32 + 8)[0]),
            max_lod=float(reader.unpack("<f", values_offset + index * 32 + 12)[0]),
            lod_bias=float(reader.unpack("<f", values_offset + index * 32 + 16)[0]),
        )
        for index, name in enumerate(names)
    )


def _read_shader_assignments(
    reader: _Reader,
    material_offset: int,
) -> tuple[
    str, str,
    tuple[tuple[str, str], ...],
    tuple[tuple[str, str], ...],
    tuple[tuple[str, str], ...],
]:
    assign_offset = reader.u64(material_offset + 40)
    if not assign_offset:
        return "", "", (), (), ()

    shader_archive_name = reader.pointer_string(assign_offset)
    shading_model_name = reader.pointer_string(assign_offset + 8)
    attribute_count = reader.bytes(assign_offset + 68, 1)[0]
    sampler_count = reader.bytes(assign_offset + 69, 1)[0]
    option_count = reader.u16(assign_offset + 70)
    attribute_assignments = _string_assignments(
        reader, reader.u64(assign_offset + 16), reader.u64(assign_offset + 24),
        attribute_count, "Shader attribute assignments",
    )
    sampler_assignments = _string_assignments(
        reader, reader.u64(assign_offset + 32), reader.u64(assign_offset + 40),
        sampler_count, "Shader sampler assignments",
    )
    shader_options = _string_assignments(
        reader, reader.u64(assign_offset + 48), reader.u64(assign_offset + 56),
        option_count, "Shader options",
    )
    return (
        shader_archive_name, shading_model_name, attribute_assignments,
        sampler_assignments, shader_options,
    )


def _read_material_shader_data(
    reader: _Reader,
    material_offset: int,
    texture_names: tuple[str, ...],
    sampler_names: tuple[str, ...],
) -> MaterialShaderData:
    (
        shader_archive_name,
        shading_model_name,
        attribute_assignments,
        sampler_assignments,
        shader_options,
    ) = _read_shader_assignments(reader, material_offset)
    return MaterialShaderData(
        shader_archive_name=shader_archive_name,
        shading_model_name=shading_model_name,
        texture_bindings=tuple(zip(texture_names, sampler_names)),
        attribute_assignments=attribute_assignments,
        sampler_assignments=sampler_assignments,
        shader_options=shader_options,
        parameters=_read_shader_parameters(reader, material_offset),
        render_infos=_read_render_infos(reader, material_offset),
        samplers=_read_sampler_states(reader, material_offset),
    )


def _float4_shader_params(
    material_shader: MaterialShaderData,
) -> dict[str, Color4]:
    return {
        parameter.name: tuple(float(value) for value in parameter.value)
        for parameter in material_shader.parameters
        if parameter.type_id == 15
        and isinstance(parameter.value, tuple)
        and len(parameter.value) == 4
    }


_COLOUR_TEXTURE_MARKERS = (
    "_alb",
    ".alb",
    "_albedo",
    ".albedo",
)
_NON_COLOUR_TEXTURE_MARKERS = (
    "_nrm",
    "_normal",
    "_nor",
    "_rgh",
    "_rough",
    "_mtl",
    "_metal",
    "_emm",
    "_emiss",
    "_mask",
    "_disp",
    "_distortion",
    "_spec",
    "_spc",
    "_ao",
    "_occlusion",
    "_height",
    "_bump",
)


def _select_albedo_texture(
    texture_names: tuple[str, ...],
    sampler_names: tuple[str, ...],
) -> str | None:
    named_albedo = next(
        (
            texture_name
            for texture_name in texture_names
            if any(
                marker in texture_name.casefold()
                for marker in _COLOUR_TEXTURE_MARKERS
            )
        ),
        None,
    )

    if named_albedo is not None:
        return named_albedo

    candidate = next(
        (
            texture_names[index]
            for index, sampler_name in enumerate(sampler_names)
            if sampler_name.casefold() == "_a0"
            and index < len(texture_names)
        ),
        None,
    )

    if candidate is None:
        return None

    lowered = candidate.casefold()

    if any(marker in lowered for marker in _NON_COLOUR_TEXTURE_MARKERS):
        return None

    return candidate


def _ordered_display_textures(
    texture_names: tuple[str, ...],
    sampler_names: tuple[str, ...],
) -> tuple[str, ...]:
    preferred = _select_albedo_texture(texture_names, sampler_names)
    return tuple(
        dict.fromkeys(
            name
            for name in (preferred, *texture_names)
            if name
        )
    )


def _read_material_appearances(
    reader: _Reader,
    offset: int,
    material_names: tuple[str, ...],
) -> tuple[
    tuple[
        str | None,
        tuple[str, ...],
        tuple[str, ...],
        Color4 | None,
        MaterialShaderData,
    ],
    ...,
]:
    appearances = []

    for material_index, expected_name in enumerate(material_names):
        material_offset = offset + material_index * 184

        if reader.bytes(material_offset, 4) != b"FMAT":
            raise ValueError(f"Expected FMAT at 0x{material_offset:X}.")

        material_name = reader.pointer_string(material_offset + 16)

        if material_name != expected_name:
            raise ValueError(
                f"FMAT {material_index} is named {material_name!r}; "
                f"expected {expected_name!r}."
            )

        texture_names_offset = reader.u64(material_offset + 56)
        texture_count = reader.bytes(material_offset + 168, 1)[0]
        texture_names = tuple(
            reader.pointer_string(texture_names_offset + index * 8)
            for index in range(texture_count)
        )
        sampler_names = reader.dictionary_keys(
            reader.u64(material_offset + 80)
        )
        albedo = _select_albedo_texture(texture_names, sampler_names)
        display_textures = _ordered_display_textures(
            texture_names,
            sampler_names,
        )
        material_shader = _read_material_shader_data(
            reader,
            material_offset,
            texture_names,
            sampler_names,
        )
        shader_params = _float4_shader_params(material_shader)

        if display_textures:
            base_color = None
        else:
            base_color = shader_params.get(
                "base_color_mul_color",
                shader_params.get(
                    "albedo",
                    shader_params.get("const_color0"),
                ),
            )

        sampler_by_texture = {
            texture_name: sampler_names[index]
            for index, texture_name in enumerate(texture_names)
            if index < len(sampler_names)
        }
        display_samplers = tuple(
            sampler_by_texture.get(texture_name, "")
            for texture_name in display_textures
        )
        appearances.append(
            (
                albedo,
                display_textures,
                display_samplers,
                base_color,
                material_shader,
            )
        )

    return tuple(appearances)


@timed("bfres_parsing")
def read_static_bfres(
    data: bytes,
    *,
    include_rigging: bool = False,
) -> tuple[StaticModel, ...]:
    reader = _Reader(data)

    if reader.bytes(0, 4) != b"FRES":
        raise ValueError("Data does not start with a BFRES FRES header.")

    if reader.bytes(12, 2) != b"\xFF\xFE":
        raise NotImplementedError(
            "Only little-endian Switch BFRES files are supported."
        )

    version = reader.u32(8)
    version_major = (version >> 16) & 0xFF

    if version_major != 8:
        raise NotImplementedError(
            f"Only Super Mario Odyssey BFRES version 8 is supported; "
            f"found version {version_major}."
        )

    model_array_offset = reader.u64(40)
    buffer_info_offset = reader.u64(144)
    model_count = reader.u16(188)

    if not model_array_offset or not model_count:
        raise ValueError("BFRES contains no models.")

    if not buffer_info_offset:
        raise ValueError("BFRES contains no GPU buffer information.")

    buffer_base = reader.u64(buffer_info_offset + 8)

    if not buffer_base:
        raise ValueError("BFRES GPU buffer offset is zero.")

    return tuple(
        _read_model(
            reader,
            model_array_offset + index * 120,
            buffer_base,
            include_rigging,
        )
        for index in range(model_count)
    )

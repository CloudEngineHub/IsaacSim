# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import warp as wp


@wp.kernel(enable_backward=False)
def set_view_to_fabric_array(
    fabric_to_view: wp.fabricarray(dtype=wp.uint32), view_to_fabric: wp.array(ndim=1, dtype=wp.uint32)
):
    fabic_idx = int(wp.tid())
    view_idx = int(fabric_to_view[fabic_idx])
    view_to_fabric[view_idx] = wp.uint32(fabic_idx)


@wp.kernel(enable_backward=False)
def set_vec3d_array(
    fabric_vals: wp.fabricarray(dtype=wp.vec3d),
    fabric_to_view: wp.fabricarray(dtype=wp.uint32),
    view_to_fabric: wp.array(ndim=1, dtype=wp.uint32),
    new_vals: wp.array(ndim=2),
    view_indices: wp.array(ndim=1, dtype=wp.uint32),
):
    i = int(wp.tid())
    view_idx = int(view_indices[i])
    fabric_idx = int(view_to_fabric[view_idx])
    new_val = new_vals[i]
    fabric_vals[fabric_idx] = wp.vec3d(wp.float64(new_val[0]), wp.float64(new_val[1]), wp.float64(new_val[2]))


@wp.kernel(enable_backward=False)
def get_vec3d_array(
    fabric_vals: wp.fabricarray(dtype=wp.vec3d),
    fabric_to_view: wp.fabricarray(dtype=wp.uint32),
    view_to_fabric: wp.array(ndim=1, dtype=wp.uint32),
    result: wp.array(ndim=2, dtype=wp.float32),
    view_indices: wp.array(ndim=1, dtype=wp.uint32),
):
    i = int(wp.tid())
    view_idx = int(view_indices[i])
    fabric_idx = int(view_to_fabric[view_idx])
    val = fabric_vals[fabric_idx]
    result[view_idx, 0] = wp.float32(val[0])
    result[view_idx, 1] = wp.float32(val[1])
    result[view_idx, 2] = wp.float32(val[2])


@wp.kernel(enable_backward=False)
def set_quatf_array(
    fabric_vals: wp.fabricarray(dtype=wp.quatf),
    fabric_to_view: wp.fabricarray(dtype=wp.uint32),
    view_to_fabric: wp.array(ndim=1, dtype=wp.uint32),
    new_vals: wp.array(ndim=2),
    view_indices: wp.array(ndim=1, dtype=wp.uint32),
):
    i = int(wp.tid())
    view_idx = int(view_indices[i])
    fabric_idx = int(view_to_fabric[view_idx])
    new_val = new_vals[i]
    fabric_vals[fabric_idx] = wp.quatf(
        wp.float32(new_val[1]), wp.float32(new_val[2]), wp.float32(new_val[3]), wp.float32(new_val[0])
    )


@wp.kernel(enable_backward=False)
def get_quatf_array(
    fabric_vals: wp.fabricarray(dtype=wp.quatf),
    fabric_to_view: wp.fabricarray(dtype=wp.uint32),
    view_to_fabric: wp.array(ndim=1, dtype=wp.uint32),
    result: wp.array(ndim=2, dtype=wp.float32),
    view_indices: wp.array(ndim=1, dtype=wp.uint32),
):
    i = int(wp.tid())
    view_idx = int(view_indices[i])
    fabric_idx = int(view_to_fabric[view_idx])
    val = fabric_vals[fabric_idx]
    result[view_idx, 0] = val[3]
    result[view_idx, 1] = val[0]
    result[view_idx, 2] = val[1]
    result[view_idx, 3] = val[2]


@wp.kernel(enable_backward=False)
def arange_k(a: wp.array(dtype=wp.uint32)):
    tid = int(wp.tid())
    a[tid] = wp.uint32(tid)


@wp.kernel(enable_backward=False)
def decompose_fabric_transformation_matrix_to_warp_arrays(
    fabric_matrices: wp.fabricarray(dtype=wp.mat44d),
    array_positions: wp.array(ndim=2, dtype=wp.float32),
    array_orientations: wp.array(ndim=2, dtype=wp.float32),
    array_scales: wp.array(ndim=2, dtype=wp.float32),
    indices: wp.array(ndim=1, dtype=wp.uint32),
    mapping: wp.array(ndim=1, dtype=wp.uint32),
):
    # resolve array index
    index = indices[wp.tid()]
    # decompose transform matrix
    position, rotation, scale = _decompose(wp.mat44f(fabric_matrices[mapping[index]]))
    # extract position
    if array_positions:
        array_positions[index, 0] = position[0]
        array_positions[index, 1] = position[1]
        array_positions[index, 2] = position[2]
    # extract orientation (Warp quaternion is xyzw)
    if array_orientations:
        array_orientations[index, 0] = rotation[3]
        array_orientations[index, 1] = rotation[0]
        array_orientations[index, 2] = rotation[1]
        array_orientations[index, 3] = rotation[2]
    # extract scale
    if array_scales:
        array_scales[index, 0] = scale[0]
        array_scales[index, 1] = scale[1]
        array_scales[index, 2] = scale[2]


@wp.kernel(enable_backward=False)
def compose_fabric_transformation_matrix_from_warp_arrays(
    fabric_matrices: wp.fabricarray(dtype=wp.mat44d),
    array_positions: wp.array(ndim=2, dtype=wp.float32),
    array_orientations: wp.array(ndim=2, dtype=wp.float32),
    array_scales: wp.array(ndim=2, dtype=wp.float32),
    broadcast_positions: bool,
    broadcast_orientations: bool,
    broadcast_scales: bool,
    indices: wp.array(ndim=1, dtype=wp.uint32),
    mapping: wp.array(ndim=1, dtype=wp.uint32),
):
    i = wp.tid()
    # resolve fabric index
    fabric_index = mapping[indices[i]]
    # decompose transform matrix
    position, rotation, scale = _decompose(wp.mat44f(fabric_matrices[fabric_index]))
    # update position
    if array_positions:
        if broadcast_positions:
            index = 0
        else:
            index = i
        position[0] = array_positions[index, 0]
        position[1] = array_positions[index, 1]
        position[2] = array_positions[index, 2]
    # update orientation (Warp quaternion is xyzw)
    if array_orientations:
        if broadcast_orientations:
            index = 0
        else:
            index = i
        rotation[0] = array_orientations[index, 1]
        rotation[1] = array_orientations[index, 2]
        rotation[2] = array_orientations[index, 3]
        rotation[3] = array_orientations[index, 0]
    # update scale
    if array_scales:
        if broadcast_scales:
            index = 0
        else:
            index = i
        scale[0] = array_scales[index, 0]
        scale[1] = array_scales[index, 1]
        scale[2] = array_scales[index, 2]
    # set transform matrix
    fabric_matrices[fabric_index] = wp.mat44d(wp.transpose(wp.matrix(position, rotation, scale)))


@wp.func
def _decompose(m: wp.mat44f):  # -> tuple[wp.vec3f, wp.quatf, wp.vec3f]
    """Decompose a 4x4 transformation matrix into position, orientation, and scale."""
    # extract position
    position = wp.vec3f(m[3, 0], m[3, 1], m[3, 2])
    # extract rotation matrix components
    r00, r01, r02 = m[0, 0], m[0, 1], m[0, 2]
    r10, r11, r12 = m[1, 0], m[1, 1], m[1, 2]
    r20, r21, r22 = m[2, 0], m[2, 1], m[2, 2]
    # get scale magnitudes
    sx = wp.sqrt(r00 * r00 + r01 * r01 + r02 * r02)
    sy = wp.sqrt(r10 * r10 + r11 * r11 + r12 * r12)
    sz = wp.sqrt(r20 * r20 + r21 * r21 + r22 * r22)
    # normalize rotation matrix components
    if sx != 0.0:
        r00 /= sx
        r01 /= sx
        r02 /= sx
    if sy != 0.0:
        r10 /= sy
        r11 /= sy
        r12 /= sy
    if sz != 0.0:
        r20 /= sz
        r21 /= sz
        r22 /= sz
    # extract rotation (quaternion)
    rotation = wp.quat_from_matrix(wp.transpose(wp.mat33f(r00, r01, r02, r10, r11, r12, r20, r21, r22)))
    # extract scale
    scale = wp.vec3f(sx, sy, sz)
    return position, rotation, scale

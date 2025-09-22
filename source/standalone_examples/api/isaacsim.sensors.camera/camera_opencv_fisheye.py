# SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from isaacsim import SimulationApp

simulation_app = SimulationApp({"headless": True})

import cv2
import isaacsim.core.utils.numpy.rotations as rot_utils
import numpy as np
from isaacsim.core.api import World
from isaacsim.core.api.objects import DynamicCuboid
from isaacsim.sensors.camera import Camera
from scipy.spatial.transform import Rotation

# Given the OpenCV camera matrix and distortion coefficients (Fisheye, Kannala-Brandt model),
# creates a camera and a sample scene, renders an image and saves it to
# camera_opencv_fisheye.png file. The asset is also saved to camera_opencv_fisheye.usd file.
width, height = 1920, 1200
camera_matrix = [[455.8, 0.0, 943.8], [0.0, 454.7, 602.3], [0.0, 0.0, 1.0]]
distortion_coefficients = [0.05, 0.01, -0.003, -0.0005]

# Camera sensor size and optical path parameters. These parameters are not the part of the
# OpenCV camera model, but they are nessesary to simulate the depth of field effect.
#
# Note: To disable the depth of field effect, set the f_stop to 0.0. This is useful for debugging.
# Set pixel size (microns)
pixel_size = 3
# Set f-number, the ratio of the lens focal length to the diameter of the entrance pupil (unitless)
f_stop = 1.8
# Set focus distance (meters) - chosen as distance from camera to cube
focus_distance = 3.0

# Create a world, add a 1x1x1 meter cube, a ground plane, and a camera
# Note: stage units are set to meters.
world = World(stage_units_in_meters=1.0)
world.scene.add_default_ground_plane()
# Define the position, scale and color of each cube, then add them to the stage
cube_positions = [
    np.array([0, 0, 0.5]),
    np.array([2, 0, 0.5]),
    np.array([0, 4, 1]),
]

cube_scale = [1.0, 1.0, 2.0]

cube_colors = [
    np.array([255, 0, 0]),
    np.array([0, 255, 0]),
    np.array([0, 0, 255]),
]

for i, (position, scale, color) in enumerate(zip(cube_positions, cube_scale, cube_colors)):
    cube = world.scene.add(
        DynamicCuboid(
            prim_path=f"/new_cube_{i}",
            name=f"cube_{i}",
            position=position,
            scale=scale * np.ones((1, 3)),
            size=1.0,
            color=color,
        )
    )

# Define camera position and orientation, then add the camera to the stage
camera_position = np.array([0.0, 0.0, 3.5])
camera_rotation_as_euler = np.array([0, 90, 0])
camera = Camera(
    prim_path="/World/camera",
    position=camera_position,
    frequency=30,
    resolution=(width, height),
    orientation=rot_utils.euler_angles_to_quats(camera_rotation_as_euler, degrees=True),
)

# Initialize the camera, creating the render product and setting its resolution
world.reset()
camera.initialize()

# Calculate the focal length and aperture size from the camera matrix
((fx, _, cx), (_, fy, cy), (_, _, _)) = camera_matrix  # fx, fy are in pixels, cx, cy are in pixels
horizontal_aperture = pixel_size * width * 1e-6  # convert to meters
vertical_aperture = pixel_size * height * 1e-6  # convert to meters
focal_length_x = pixel_size * fx * 1e-6  # convert to meters
focal_length_y = pixel_size * fy * 1e-6  # convert to meters
focal_length = (focal_length_x + focal_length_y) / 2  # convert to meters

# Set the camera parameters, note the unit conversion between Isaac Sim sensor and Kit
camera.set_focal_length(focal_length)
camera.set_focus_distance(focus_distance)
camera.set_lens_aperture(f_stop)
camera.set_horizontal_aperture(horizontal_aperture)
camera.set_vertical_aperture(vertical_aperture)

camera.set_clipping_range(0.05, 1.0e5)

# Set the distortion coefficients
camera.set_opencv_fisheye_properties(cx=cx, cy=cy, fx=fx, fy=fy, fisheye=distortion_coefficients)

# Render 10 frames, then load the rendered image into a CV2-compatible format
for i in range(10):
    world.step(render=True)
img = cv2.cvtColor(camera.get_rgb().astype(np.uint8), cv2.COLOR_RGB2BGR)

# Plot cube corners on the rendered image. Code adapted from snippet provided by forum user @ericpedley.
# Resolve the corners of each cube in world space
cube_corners = np.array(
    [
        [0.5, 0.5, 0.5],
        [-0.5, 0.5, 0.5],
        [0.5, -0.5, 0.5],
        [-0.5, -0.5, 0.5],
        [0.5, 0.5, -0.5],
        [-0.5, 0.5, -0.5],
        [0.5, -0.5, -0.5],
        [-0.5, -0.5, -0.5],
    ],
    dtype=np.float64,
)

cube_corners_world = []
for position, scale in zip(cube_positions, cube_scale):
    cube_corners_world.append(cube_corners * scale + position)
object_points_world = np.vstack(cube_corners_world)

# Transform from Isaac Sim (Y-forward, Z-up) to OpenCV (Z-forward, Y-down) coordinates
isaac_to_cv2_mat = np.array([[0, -1, 0], [0, 0, -1], [1, 0, 0]], dtype=np.float64)
isaac_to_cv2 = Rotation.from_matrix(isaac_to_cv2_mat)

# Convert camera rotation to OpenCV coordinate system
cam_rotation = Rotation.from_euler("xyz", camera_rotation_as_euler, degrees=True)
cam_rotation_cv2 = isaac_to_cv2 * cam_rotation.inv() * isaac_to_cv2.inv()

# Transform object points to OpenCV coordinates for projection
object_points_cv2 = np.expand_dims(object_points_world @ isaac_to_cv2_mat.T, axis=1)

# Camera rotation vector in OpenCV coordinates
rvec_cv2 = cam_rotation_cv2.as_rotvec()

# Translation vector (world origin to camera) in OpenCV coordinates
tvec_cv2 = -cam_rotation_cv2.apply(camera_position @ isaac_to_cv2_mat.T)

# Camera intrinsic matrix
K = np.array(camera_matrix, dtype=np.float64)

# Distortion coefficients (Kannala-Brandt model)
D = np.array(distortion_coefficients, dtype=np.float64)

# Project 3D points to 2D image coordinates
image_points, _ = cv2.fisheye.projectPoints(object_points_cv2, rvec_cv2, tvec_cv2, K, D)

# Draw the projected corners of each cube on the rendered image
for i, pt in enumerate(image_points):
    # Map cube color from RGB to BGR
    cube_color = cube_colors[i // 8].astype(np.uint8)[::-1]
    color = tuple(cube_color.tolist())
    # Skip points outside the camera field of view
    if np.any(pt[0] < 0):
        continue
    # Draw the point as a filled circle with yellow border for contrast
    cv2.circle(img, tuple(pt[0].astype(int)), 5, (0, 255, 255), -1)
    cv2.circle(img, tuple(pt[0].astype(int)), 3, color, -1)

print("Saving the rendered image to: camera_opencv_fisheye.png")
cv2.imwrite("camera_opencv_fisheye.png", img)

print("Saving the asset to camera_opencv_fisheye.usd")
world.scene.stage.Export("camera_opencv_fisheye.usd")

simulation_app.close()

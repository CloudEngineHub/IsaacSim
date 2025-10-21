# SPDX-FileCopyrightText: Copyright (c) 2018-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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


import os
import random
import sys
from typing import List, Optional, Tuple

import carb
import cv2

# Import extension python module we are testing with absolute import path, as if we are external user (other extension)
import isaacsim.core.utils.numpy.rotations as rot_utils
import numpy as np
import omni.graph.core as og
import omni.kit.commands
import omni.kit.test
import omni.kit.usd
import omni.kit.viewport.utility
import omni.replicator.core as rep
import usdrt
from isaacsim.core.api.objects import VisualCuboid
from isaacsim.core.prims import SingleXFormPrim
from isaacsim.core.utils.physics import simulate_async
from isaacsim.core.utils.prims import define_prim
from isaacsim.core.utils.stage import add_reference_to_stage, get_current_stage, open_stage_async
from isaacsim.core.utils.viewports import set_camera_view
from isaacsim.sensors.camera import Camera
from isaacsim.test.utils import compute_difference_metrics, save_depth_image
from pxr import Gf, Sdf, UsdGeom, UsdLux
from sensor_msgs.msg import CameraInfo, Image, PointCloud2
from sensor_msgs_py import point_cloud2

from .common import ROS2TestCase, get_qos_profile

# Debug flags for saving depth images during testing
SAVE_DEPTH_IMAGES_AS_TEST = False
SAVE_DEPTH_IMAGES_AS_GOLDEN = False


# Having a test class derived from omni.kit.test.AsyncTestCase declared on the root of module will make it auto-discoverable by omni.kit.test
class TestRos2CameraInfo(ROS2TestCase):
    # Before running each test
    async def setUp(self):
        await super().setUp()

        omni.usd.get_context().new_stage()

        await omni.kit.app.get_app().next_update_async()

        # acquire the viewport window
        viewport_api = omni.kit.viewport.utility.get_active_viewport()
        # Set viewport resolution, changes will occur on next frame
        viewport_api.set_texture_resolution((1280, 720))
        await omni.kit.app.get_app().next_update_async()

        pass

    # After running each test
    async def tearDown(self):

        self._timeline.stop()
        await super().tearDown()

    def imgmsg_to_cv2(self, img_msg):
        """Convert ROS image message to numpy array.

        Converts a ROS sensor_msgs/Image message to a numpy array, handling different
        encodings including RGB and depth images.

        Args:
            param img_msg: ROS Image message.

        Returns:
            Image as numpy array with proper data type and dimensions.
        """
        # Determine dtype and n_channels based on encoding
        if img_msg.encoding == "rgb8":
            dtype = np.dtype(np.uint8)
            n_channels = 3
        elif img_msg.encoding == "32FC1":
            dtype = np.dtype(np.float32)
            n_channels = 1
        elif img_msg.encoding == "16UC1":
            dtype = np.dtype(np.uint16)
            n_channels = 1
        else:
            # Default fallback for RGB
            dtype = np.dtype(np.uint8)
            n_channels = 3

        dtype = dtype.newbyteorder(">" if img_msg.is_bigendian else "<")

        img_buf = np.asarray(img_msg.data, dtype=dtype) if isinstance(img_msg.data, list) else img_msg.data

        if n_channels == 1:
            im = np.ndarray(shape=(img_msg.height, int(img_msg.step / dtype.itemsize)), dtype=dtype, buffer=img_buf)
            im = np.ascontiguousarray(im[: img_msg.height, : img_msg.width])
        else:
            im = np.ndarray(
                shape=(img_msg.height, int(img_msg.step / dtype.itemsize / n_channels), n_channels),
                dtype=dtype,
                buffer=img_buf,
            )
            im = np.ascontiguousarray(im[: img_msg.height, : img_msg.width, :])

        # If the byte order is different between the message and the system.
        if img_msg.is_bigendian == (sys.byteorder == "little"):
            im = im.byteswap().newbyteorder()

        return im

    async def test_monocular_camera_info(self):
        scene_path = "/Isaac/Environments/Grid/default_environment.usd"
        await open_stage_async(self._assets_root_path + scene_path)

        camera_path = "/Isaac/Sensors/LeopardImaging/Hawk/hawk_v1.1_nominal.usd"
        add_reference_to_stage(usd_path=self._assets_root_path + camera_path, prim_path="/Hawk")
        import rclpy

        try:
            og.Controller.edit(
                {"graph_path": "/ActionGraph", "evaluator_name": "execution"},
                {
                    og.Controller.Keys.CREATE_NODES: [
                        ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                        ("CreateRenderProduct", "isaacsim.core.nodes.IsaacCreateRenderProduct"),
                        ("CameraInfoPublish", "isaacsim.ros2.bridge.ROS2CameraInfoHelper"),
                    ],
                    og.Controller.Keys.SET_VALUES: [
                        ("CreateRenderProduct.inputs:cameraPrim", [Sdf.Path("/Hawk/left/camera_left")]),
                        ("CreateRenderProduct.inputs:height", 1200),
                        ("CreateRenderProduct.inputs:width", 1920),
                        ("CameraInfoPublish.inputs:topicName", "camera_info"),
                        ("CameraInfoPublish.inputs:resetSimulationTimeOnStop", True),
                    ],
                    og.Controller.Keys.CONNECT: [
                        ("OnPlaybackTick.outputs:tick", "CreateRenderProduct.inputs:execIn"),
                        ("CreateRenderProduct.outputs:execOut", "CameraInfoPublish.inputs:execIn"),
                        ("CreateRenderProduct.outputs:renderProductPath", "CameraInfoPublish.inputs:renderProductPath"),
                    ],
                },
            )
        except Exception as e:
            print(e)
        await omni.kit.app.get_app().next_update_async()

        from sensor_msgs.msg import CameraInfo

        self._camera_info = None

        def camera_info_callback(data):
            self._camera_info = data

        node = self.create_node("camera_tester")
        camera_info_sub = self.create_subscription(
            node, CameraInfo, "camera_info", camera_info_callback, get_qos_profile()
        )

        await omni.kit.app.get_app().next_update_async()

        def spin():
            rclpy.spin_once(node, timeout_sec=0.1)

        import time

        system_time = time.time()

        for num in range(3):
            print(f"Play #{num+1}")
            self._timeline.play()
            await omni.kit.app.get_app().next_update_async()
            for _ in range(10):
                if self._camera_info is None:
                    await simulate_async(1, callback=spin)

            self.assertIsNotNone(self._camera_info)

            self.assertEqual(self._camera_info.width, 1920)
            self.assertEqual(self._camera_info.height, 1200)
            self.assertGreaterEqual(self._camera_info.header.stamp.sec, 1)
            self.assertLess(self._camera_info.header.stamp.sec, system_time / 2.0)

            # Test contents of k matrix (function of width, height, focal length, apertures)
            self.assertAlmostEqual(self._camera_info.k[0], 1920.0 * 2.87343 / 5.76, places=2)
            self.assertAlmostEqual(self._camera_info.k[1], 0.0)
            self.assertAlmostEqual(self._camera_info.k[2], 1920.0 * 0.5)
            self.assertAlmostEqual(self._camera_info.k[3], 0.0)
            self.assertAlmostEqual(self._camera_info.k[4], 1200.0 * 2.87343 / 3.6, places=2)
            self.assertAlmostEqual(self._camera_info.k[5], 1200.0 * 0.5)
            self.assertAlmostEqual(self._camera_info.k[6], 0.0)
            self.assertAlmostEqual(self._camera_info.k[7], 0.0)
            self.assertAlmostEqual(self._camera_info.k[8], 1.0)

            # Test if r matrix is identity
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(self._camera_info.r[i * 3 + j], 1.0 if i == j else 0.0)

            # Test if p matrix is k matrix concatenated with 1x3 0 vector
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(self._camera_info.p[i * 4 + j], self._camera_info.k[i * 3 + j])
                self.assertAlmostEqual(self._camera_info.p[i * 4 + 3], 0.0)

            # Test distortion model and coefficients
            self.assertEqual(self._camera_info.distortion_model, "rational_polynomial")
            distortion_coefficients = [0.147811, -0.032313, -0.000194, -0.000035, 0.008823, 0.517913, -0.06708, 0.01695]
            for i in range(len(distortion_coefficients)):
                self.assertAlmostEqual(self._camera_info.d[i], distortion_coefficients[i])
            self._timeline.stop()

            # make sure all previous messages are cleared
            await omni.kit.app.get_app().next_update_async()
            spin()
            await omni.kit.app.get_app().next_update_async()
            self._camera_info = None

    def _add_light(self, name: str, position: List[float]) -> None:
        sphereLight = UsdLux.SphereLight.Define(get_current_stage(), Sdf.Path(f"/World/SphereLight_{name}"))
        sphereLight.CreateRadiusAttr(6)
        sphereLight.CreateIntensityAttr(10000)
        SingleXFormPrim(str(sphereLight.GetPath())).set_world_pose(position)

    def _add_checkerboard(self, position: List[float]) -> None:
        checkerboard_path = self._assets_root_path + "/Isaac/Props/Camera/checkerboard_6x10.usd"
        add_reference_to_stage(usd_path=checkerboard_path, prim_path="/calibration_target")
        SingleXFormPrim("/calibration_target", name="calibration_target", position=position)

    def _get_rectified_image(self, image_msg_raw, camera_info_msg, side):
        # Convert ROS2 image message data buffer to CV2 image
        image_raw = self.imgmsg_to_cv2(image_msg_raw)
        if self._visualize:
            cv2.imwrite(f"{side}_image_raw.png", image_raw)
        # Initialize the mapping arrays to rectify the raw image
        k = np.reshape(np.array(camera_info_msg.k), (3, 3))
        r = np.reshape(np.array(camera_info_msg.r), (3, 3))
        p = np.reshape(np.array(camera_info_msg.p), (3, 4))
        if camera_info_msg.distortion_model == "equidistant":
            map1, map2 = cv2.fisheye.initUndistortRectifyMap(
                K=k,
                D=np.array(camera_info_msg.d),
                R=r,
                P=p,
                size=(camera_info_msg.width, camera_info_msg.height),
                m1type=cv2.CV_32FC1,
            )
            return cv2.remap(src=image_raw, map1=map1, map2=map2, interpolation=cv2.INTER_LANCZOS4)
        else:
            map1, map2 = cv2.initUndistortRectifyMap(
                cameraMatrix=k,
                distCoeffs=np.array(camera_info_msg.d),
                R=r,
                newCameraMatrix=p,
                size=(camera_info_msg.width, camera_info_msg.height),
                m1type=cv2.CV_32FC1,
            )
            return cv2.remap(src=image_raw, map1=map1, map2=map2, interpolation=cv2.INTER_LANCZOS4)

    def _prepare_scene_for_stereo_camera(
        self,
        baseline: float,
        resolution: Tuple[int, int],
        focal_length: float,
        focus_distance: float,
        use_system_time: bool = False,
        reset_simulation_time_on_stop: bool = True,
    ) -> Tuple[Camera, Camera]:
        """Add a stereo camera, checkerboard, and lights to the scene

        Args:
            baseline (float): Baseline distance between the two cameras
            resolution (Tuple[int, int]): Resolution of the cameras
            focal_length (float): Focal length of the cameras
            focus_distance (float): Focus distance of the cameras
            use_system_time (bool, optional): Whether to use system time for timestamps. Defaults to False.
            reset_simulation_time_on_stop (bool, optional): Whether to reset_simulation_time_on_stop. Defaults to True.

        Returns:
            Tuple[Camera, Camera]: The left and right cameras
        """

        left_camera = Camera(
            prim_path="/left_camera",
            name="left_camera",
            resolution=resolution,
            position=np.array([0, baseline / 2.0, 0]),
        )
        left_camera.set_focal_length(focal_length)
        left_camera.set_focus_distance(focus_distance)

        right_camera = Camera(
            prim_path="/right_camera",
            name="right_camera",
            resolution=resolution,
            position=np.array([0, -baseline / 2.0, 0]),
        )
        right_camera.set_focal_length(focal_length)
        right_camera.set_focus_distance(focus_distance)

        # Create a light
        self._add_light(name="top", position=[0.0, 0.0, 12])
        self._add_light(name="bottom", position=[0.0, 0.0, -12])

        # Create a checkerboard
        self._add_checkerboard(position=[1.1, 0.0, -0.6])

        # Add an OmniGraph to publish the camera info and images
        try:
            og.Controller.edit(
                {"graph_path": "/ActionGraph", "evaluator_name": "execution"},
                {
                    og.Controller.Keys.CREATE_NODES: [
                        ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                        ("RunOneSimulationFrame", "isaacsim.core.nodes.OgnIsaacRunOneSimulationFrame"),
                        ("CreateRenderProductLeft", "isaacsim.core.nodes.IsaacCreateRenderProduct"),
                        ("CreateRenderProductRight", "isaacsim.core.nodes.IsaacCreateRenderProduct"),
                        ("CameraInfoPublish", "isaacsim.ros2.bridge.ROS2CameraInfoHelper"),
                        ("RGBPublishLeft", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                        ("RGBPublishRight", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                    ],
                    og.Controller.Keys.SET_VALUES: [
                        ("CreateRenderProductLeft.inputs:cameraPrim", [Sdf.Path("/left_camera")]),
                        ("CreateRenderProductLeft.inputs:height", resolution[1]),
                        ("CreateRenderProductLeft.inputs:width", resolution[0]),
                        ("CreateRenderProductRight.inputs:cameraPrim", [Sdf.Path("/right_camera")]),
                        ("CreateRenderProductRight.inputs:height", resolution[1]),
                        ("CreateRenderProductRight.inputs:width", resolution[0]),
                        ("CameraInfoPublish.inputs:topicName", "camera_info_left"),
                        ("CameraInfoPublish.inputs:topicNameRight", "camera_info_right"),
                        ("CameraInfoPublish.inputs:frameId", "frame_left"),
                        ("CameraInfoPublish.inputs:frameIdRight", "frame_right"),
                        ("CameraInfoPublish.inputs:resetSimulationTimeOnStop", reset_simulation_time_on_stop),
                        ("RGBPublishLeft.inputs:topicName", "rgb_left"),
                        ("RGBPublishLeft.inputs:type", "rgb"),
                        ("RGBPublishLeft.inputs:resetSimulationTimeOnStop", reset_simulation_time_on_stop),
                        ("RGBPublishRight.inputs:topicName", "rgb_right"),
                        ("RGBPublishRight.inputs:type", "rgb"),
                        ("RGBPublishRight.inputs:resetSimulationTimeOnStop", reset_simulation_time_on_stop),
                        ("RGBPublishLeft.inputs:useSystemTime", use_system_time),
                        ("RGBPublishRight.inputs:useSystemTime", use_system_time),
                        ("CameraInfoPublish.inputs:useSystemTime", use_system_time),
                    ],
                    og.Controller.Keys.CONNECT: [
                        ("OnPlaybackTick.outputs:tick", "RunOneSimulationFrame.inputs:execIn"),
                        ("RunOneSimulationFrame.outputs:step", "CreateRenderProductLeft.inputs:execIn"),
                        ("RunOneSimulationFrame.outputs:step", "CreateRenderProductRight.inputs:execIn"),
                        ("CreateRenderProductLeft.outputs:execOut", "CameraInfoPublish.inputs:execIn"),
                        (
                            "CreateRenderProductLeft.outputs:renderProductPath",
                            "CameraInfoPublish.inputs:renderProductPath",
                        ),
                        ("CreateRenderProductRight.outputs:execOut", "CameraInfoPublish.inputs:execIn"),
                        (
                            "CreateRenderProductRight.outputs:renderProductPath",
                            "CameraInfoPublish.inputs:renderProductPathRight",
                        ),
                        ("CreateRenderProductLeft.outputs:execOut", "RGBPublishLeft.inputs:execIn"),
                        (
                            "CreateRenderProductLeft.outputs:renderProductPath",
                            "RGBPublishLeft.inputs:renderProductPath",
                        ),
                        ("CreateRenderProductRight.outputs:execOut", "RGBPublishRight.inputs:execIn"),
                        (
                            "CreateRenderProductRight.outputs:renderProductPath",
                            "RGBPublishRight.inputs:renderProductPath",
                        ),
                    ],
                },
            )
        except Exception as e:
            print(e)

        return left_camera, right_camera

    async def _test_get_stereo_camera_messages(
        self, opencv_distortion_model: str, ros2_distortion_model: str, distortion_coefficients: List[float]
    ):
        """Get the camera info and images from the stereo camera

        Args:
            opencv_distortion_model (str): OpenCV distortion model to test.
            ros2_distortion_model (str): ROS2 distortion model to test.
            distortion_coefficients (List[float]): Distortion coefficients to test.
        """

        import rclpy
        from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
        from sensor_msgs.msg import CameraInfo, Image

        if not rclpy.ok():
            rclpy.init()

        # Create ROS nodes to receive camera data
        node_left = self.create_node("camera_left_node")
        node_right = self.create_node("camera_right_node")

        # Set up QoS profile matching the publisher
        cam_info_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE, history=QoSHistoryPolicy.KEEP_LAST, depth=10
        )

        # Subscribe to camera topics
        def camera_info_left_callback(msg):
            self._camera_info_left = msg

        def camera_info_right_callback(msg):
            self._camera_info_right = msg

        def image_left_callback(msg):
            self._image_left = msg

        def image_right_callback(msg):
            self._image_right = msg

        # Create subscriptions
        camera_info_left_sub = self.create_subscription(
            node_left, CameraInfo, "camera_info_left", camera_info_left_callback, cam_info_qos
        )
        camera_info_right_sub = self.create_subscription(
            node_right, CameraInfo, "camera_info_right", camera_info_right_callback, cam_info_qos
        )
        image_left_sub = self.create_subscription(node_left, Image, "rgb_left", image_left_callback, cam_info_qos)
        image_right_sub = self.create_subscription(node_right, Image, "rgb_right", image_right_callback, cam_info_qos)

        # Start spinning the nodes
        def spin_left():
            rclpy.spin_once(node_left, timeout_sec=0.1)

        def spin_right():
            rclpy.spin_once(node_right, timeout_sec=0.1)

        # Wait for camera info and images to be received
        self._timeline.play()
        await omni.kit.app.get_app().next_update_async()
        await simulate_async(0.5, callback=spin_right)
        await simulate_async(0.5, callback=spin_left)

        self.assertIsNotNone(self._camera_info_left, f"Did not receive left camera_info for {opencv_distortion_model}")
        self.assertIsNotNone(
            self._camera_info_right, f"Did not receive right camera_info for {opencv_distortion_model}"
        )
        self.assertIsNotNone(self._image_left, f"Did not receive left image for {opencv_distortion_model}")
        self.assertIsNotNone(self._image_right, f"Did not receive right image for {opencv_distortion_model}")

        # Check CameraInfo distortion model and distortion coefficients
        self.assertEqual(self._camera_info_left.distortion_model, ros2_distortion_model)
        self.assertEqual(self._camera_info_right.distortion_model, ros2_distortion_model)
        for i, (expected, actual_left, actual_right) in enumerate(
            zip(distortion_coefficients, self._camera_info_left.d, self._camera_info_right.d)
        ):
            self.assertAlmostEqual(
                actual_left, expected, delta=1e-5, msg=f"Left coefficient {i} mismatch for {opencv_distortion_model}"
            )
            self.assertAlmostEqual(
                actual_right, expected, delta=1e-5, msg=f"Right coefficient {i} mismatch for {opencv_distortion_model}"
            )

        # Stop timeline
        self._timeline.stop()

    async def _test_stereo_rectification(self, opencv_distortion_model):
        """Test stereo rectification

        Args:
            opencv_distortion_model (str): OpenCV distortion model to test.
        """
        left_image_rect = self._get_rectified_image(self._image_left, self._camera_info_left, "left")
        right_image_rect = self._get_rectified_image(self._image_right, self._camera_info_right, "right")
        cv2.imwrite("left_image_rect.png", left_image_rect)
        cv2.imwrite("right_image_rect.png", right_image_rect)

        # Find checkerboard corners
        checkerboard_size = (6, 10)  # Internal corners on the checkerboard
        flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE

        ret_left, corners_left = cv2.findChessboardCorners(left_image_rect, checkerboard_size, flags)
        ret_right, corners_right = cv2.findChessboardCorners(right_image_rect, checkerboard_size, flags)

        # Verify corners were found in both images
        self.assertTrue(ret_left, f"Could not find checkerboard corners in left image for {opencv_distortion_model}")
        self.assertTrue(ret_right, f"Could not find checkerboard corners in right image for {opencv_distortion_model}")

        # Extract the x and y coordinates of the corners in left_corners and right_corners
        x_coords_left = [c[0][0] for c in corners_left]
        y_coords_left = [c[0][1] for c in corners_left]
        x_coords_right = [c[0][0] for c in corners_right]
        y_coords_right = [c[0][1] for c in corners_right]
        if self._visualize:
            # Draw lines of the same color at the average row value for all corners
            # in the left and right image
            cv2.drawChessboardCorners(left_image_rect, checkerboard_size, corners_left, ret_left)
            cv2.drawChessboardCorners(right_image_rect, checkerboard_size, corners_right, ret_right)
            # Draw randomly colored lines connecting the corresponding corners
            for i in range(min(len(corners_left), len(corners_right))):
                average_y = (y_coords_left[i] + y_coords_right[i]) / 2
                pt1 = (0, int(average_y))
                pt2 = (left_image_rect.shape[1], int(average_y))
                random_color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                cv2.line(left_image_rect, pt1, pt2, random_color, 1)
                cv2.line(right_image_rect, pt1, pt2, random_color, 1)
            cv2.imwrite("left_image_rect.png", left_image_rect)
            cv2.imwrite("right_image_rect.png", right_image_rect)

        # Test 1: Check vertical alignment of corresponding corners
        # Compare row positions
        row_diffs = [
            abs(y_coords_left[i] - y_coords_right[i]) for i in range(min(len(corners_left), len(corners_right)))
        ]

        # Allow 4-pixel difference in vertical alignment
        CORNER_ROW_DIFF_THRESHOLD = 4
        if self._visualize:
            print("CORNER_ROW_DIFF_THRESHOLD :")
            print(CORNER_ROW_DIFF_THRESHOLD)
            print("row_diffs :")
            print(row_diffs)

        self.assertFalse(
            any(diff > CORNER_ROW_DIFF_THRESHOLD for diff in row_diffs),
            f"Difference between corners row values in left and right images exceeds threshold for {opencv_distortion_model}",
        )

        # Test 2: Check if epipolar lines are parallel
        EPIPOLAR_LINES_SLOPE_DIFF_THRESHOLD = 0.005
        epipolar_slopes = []
        for i in range(0, len(y_coords_left), checkerboard_size[0]):
            epipolar_slopes.append(
                abs(y_coords_left[i] - y_coords_left[i + 5]) / abs(x_coords_left[i] - x_coords_left[i + 5])
            )
            epipolar_slopes.append(
                abs(y_coords_right[i] - y_coords_right[i + 5]) / abs(x_coords_right[i] - x_coords_right[i + 5])
            )
        # Allow at most 2 points to exceed the threshold
        self.assertLessEqual(
            sum(np.array(epipolar_slopes) > EPIPOLAR_LINES_SLOPE_DIFF_THRESHOLD),
            2,
            f"Epipolar lines are not parallel for {opencv_distortion_model}!",
        )

    async def test_stereo_camera_opencv_pinhole(self):

        self._visualize = False
        left_camera, right_camera = self._prepare_scene_for_stereo_camera(
            baseline=0.15, resolution=(2048, 1024), focal_length=1.8, focus_distance=400
        )

        # Set distortion parameters
        pinhole = [0.1, 0.02, 0.01, 0.002, 0.003, 0.0004, 0.00005, 0.00005, 0.01, 0.002, 0.0003, 0.0004]
        left_camera.set_opencv_pinhole_properties(pinhole=pinhole)
        right_camera.set_opencv_pinhole_properties(pinhole=pinhole)

        # Retrieve and test basic validity of CameraInfo messages and raw images
        await self._test_get_stereo_camera_messages(
            opencv_distortion_model="opencvPinhole",
            ros2_distortion_model="rational_polynomial",
            distortion_coefficients=pinhole,
        )

        # Test stereo rectification
        await self._test_stereo_rectification(opencv_distortion_model="opencvPinhole")

    async def test_stereo_camera_opencv_fisheye(self):

        self._visualize = False
        left_camera, right_camera = self._prepare_scene_for_stereo_camera(
            baseline=0.15, resolution=(2048, 1024), focal_length=1.8, focus_distance=400
        )

        # Set distortion parameters
        fisheye = [0.1, 0.02, 0.003, 0.0004]
        left_camera.set_opencv_fisheye_properties(fisheye=fisheye)
        right_camera.set_opencv_fisheye_properties(fisheye=fisheye)

        # Retrieve and test basic validity of CameraInfo messages and raw images
        await self._test_get_stereo_camera_messages(
            opencv_distortion_model="opencvFisheye",
            ros2_distortion_model="equidistant",
            distortion_coefficients=fisheye,
        )

        # Test stereo rectification
        await self._test_stereo_rectification(opencv_distortion_model="opencvFisheye")

    async def test_camera_info_system_time(self):
        import time

        import rclpy
        from sensor_msgs.msg import CameraInfo

        # Test 1: useSystemTime = True
        left_camera, right_camera = self._prepare_scene_for_stereo_camera(
            baseline=0.15, resolution=(2048, 1024), focal_length=1.8, focus_distance=400, use_system_time=True
        )

        self._camera_info_system_time = None

        def camera_info_system_time_callback(data):
            self._camera_info_system_time = data

        node_system = self.create_node("camera_system_time_tester")
        camera_info_sub_system = self.create_subscription(
            node_system, CameraInfo, "camera_info_left", camera_info_system_time_callback, get_qos_profile()
        )

        def spin_system_time():
            rclpy.spin_once(node_system, timeout_sec=0.1)

        self._timeline.play()
        await omni.kit.app.get_app().next_update_async()
        await simulate_async(0.5, callback=spin_system_time)

        for _ in range(10):
            if self._camera_info_system_time is None:
                await simulate_async(0.5, callback=spin_system_time)
            else:
                break

        self.assertIsNotNone(self._camera_info_system_time)
        system_timestamp = (
            self._camera_info_system_time.header.stamp.sec + self._camera_info_system_time.header.stamp.nanosec * 1e-9
        )
        current_time = time.time()

        self.assertLess(abs(system_timestamp - current_time), 2.0, "System time should be similar to current time")

        self._timeline.stop()
        await omni.kit.app.get_app().next_update_async()

    async def test_camera_info_sim_time(self):
        import time

        import rclpy
        from sensor_msgs.msg import CameraInfo

        left_camera, right_camera = self._prepare_scene_for_stereo_camera(
            baseline=0.15, resolution=(2048, 1024), focal_length=1.8, focus_distance=400, use_system_time=False
        )

        self._camera_info_sim_time = None

        def camera_info_sim_time_callback(data):
            self._camera_info_sim_time = data

        node_sim = self.create_node("camera_sim_time_tester")
        camera_info_sub_sim = self.create_subscription(
            node_sim, CameraInfo, "camera_info_left", camera_info_sim_time_callback, get_qos_profile()
        )

        def spin_sim_time():
            rclpy.spin_once(node_sim, timeout_sec=0.1)

        self._timeline.play()
        await omni.kit.app.get_app().next_update_async()
        await simulate_async(0.5, callback=spin_sim_time)

        for _ in range(10):
            if self._camera_info_sim_time is None:
                await simulate_async(0.5, callback=spin_sim_time)
            else:
                break

        self.assertIsNotNone(self._camera_info_sim_time)
        sim_timestamp = (
            self._camera_info_sim_time.header.stamp.sec + self._camera_info_sim_time.header.stamp.nanosec * 1e-9
        )
        self.assertGreaterEqual(sim_timestamp, 0.0, "Simulation time should be >= 0")
        self.assertLessEqual(sim_timestamp, 10.0, "Simulation time should be <= 10s")

        prev_sim_timestamp = sim_timestamp

        self._timeline.stop()
        await omni.kit.app.get_app().next_update_async()

        # Check if sim time reset to Zero
        self._timeline.play()
        await omni.kit.app.get_app().next_update_async()
        await simulate_async(0.5, callback=spin_sim_time)

        for _ in range(10):
            if self._camera_info_sim_time is None:
                await simulate_async(0.5, callback=spin_sim_time)
            else:
                break

        self.assertIsNotNone(self._camera_info_sim_time)
        sim_timestamp = (
            self._camera_info_sim_time.header.stamp.sec + self._camera_info_sim_time.header.stamp.nanosec * 1e-9
        )
        self.assertGreaterEqual(sim_timestamp, 0.0, "Simulation time should be >= 0")
        self.assertLessEqual(
            sim_timestamp, prev_sim_timestamp * 1.2, "Simulation time should be close to previous sim time"
        )

    async def test_camera_info_sim_time_monotonic(self):
        import time

        import rclpy
        from sensor_msgs.msg import CameraInfo

        left_camera, right_camera = self._prepare_scene_for_stereo_camera(
            baseline=0.15,
            resolution=(2048, 1024),
            focal_length=1.8,
            focus_distance=400,
            use_system_time=False,
            reset_simulation_time_on_stop=False,
        )

        self._camera_info_sim_time = None

        def camera_info_sim_time_callback(data):
            self._camera_info_sim_time = data

        node_sim = self.create_node("camera_sim_time_monotonic_tester")
        camera_info_sub_sim = self.create_subscription(
            node_sim, CameraInfo, "camera_info_left", camera_info_sim_time_callback, get_qos_profile()
        )

        def spin_sim_time():
            rclpy.spin_once(node_sim, timeout_sec=0.1)

        self._timeline.play()
        await omni.kit.app.get_app().next_update_async()
        await simulate_async(0.5, callback=spin_sim_time)

        for _ in range(10):
            if self._camera_info_sim_time is None:
                await simulate_async(0.5, callback=spin_sim_time)
            else:
                break

        self.assertIsNotNone(self._camera_info_sim_time)
        sim_timestamp = (
            self._camera_info_sim_time.header.stamp.sec + self._camera_info_sim_time.header.stamp.nanosec * 1e-9
        )
        self.assertGreaterEqual(sim_timestamp, 0.0, "Simulation time should be >= 0")

        prev_sim_timestamp = sim_timestamp

        self._timeline.stop()
        await omni.kit.app.get_app().next_update_async()

        # Check if current sim time is larger than prev sim time
        self._timeline.play()
        await omni.kit.app.get_app().next_update_async()
        await simulate_async(0.5, callback=spin_sim_time)

        for _ in range(10):
            if self._camera_info_sim_time is None:
                await simulate_async(0.5, callback=spin_sim_time)
            else:
                break

        self.assertIsNotNone(self._camera_info_sim_time)
        sim_timestamp = (
            self._camera_info_sim_time.header.stamp.sec + self._camera_info_sim_time.header.stamp.nanosec * 1e-9
        )
        self.assertGreaterEqual(sim_timestamp, prev_sim_timestamp, "Simulation time should be >= prev_sim_timestamp")
        self.assertLessEqual(
            sim_timestamp, (prev_sim_timestamp + 10.0), "Simulation time should be within an elapsed max time of 10s"
        )

    async def test_depth_pointcloud_projection(self):
        """Test that depth pointcloud can be projected back to depth image using camera intrinsics.

        This test verifies that a depth pointcloud published by ROS2CameraHelper can be projected
        back to a depth image using OpenCV and camera intrinsic parameters from CameraInfo,
        and that the projected image matches the original depth image within acceptable tolerances.

        Example:

        .. code-block:: python

            # This test is run automatically as part of the test suite
            >>> # The test creates a camera, publishes depth data and pointcloud
            >>> # then projects the pointcloud back to verify consistency
        """
        # Set up test scene with objects
        await self._setup_test_scene_with_objects()

        # Create camera using high-level API
        resolution = (1280, 720)
        camera = Camera(
            prim_path="/World/Camera",
            position=np.array([4.0, 0, 0.0]),
            resolution=resolution,
            orientation=rot_utils.euler_angles_to_quats(np.array([0, 0, 180]), degrees=True),
        )
        camera.set_focal_length(1.814756)

        # Set up directories for debug image saving
        golden_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data", "golden")
        test_dir = carb.tokens.get_tokens_interface().resolve("${temp}/test_depth_pointcloud_projection")

        # Set up ROS2 message capture
        node, depth_image_msg, pointcloud_msg, camera_info_msg = self._setup_ros2_message_capture(
            "depth_pointcloud_projection_tester"
        )

        # Create OmniGraph to publish camera data
        graph_path = "/ActionGraph"

        try:
            keys = og.Controller.Keys
            (graph, nodes, _, _) = og.Controller.edit(
                {"graph_path": graph_path, "evaluator_name": "execution"},
                {
                    keys.CREATE_NODES: [
                        ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                        ("ReadSimTime", "isaacsim.core.nodes.IsaacReadSimulationTime"),
                        ("CreateRenderProduct", "isaacsim.core.nodes.IsaacCreateRenderProduct"),
                        ("DepthImagePublisher", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                        ("PointcloudPublisher", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                        ("CameraInfoPublisher", "isaacsim.ros2.bridge.ROS2CameraInfoHelper"),
                    ],
                    keys.SET_VALUES: [
                        ("CreateRenderProduct.inputs:cameraPrim", [usdrt.Sdf.Path(camera.prim_path)]),
                        ("CreateRenderProduct.inputs:height", resolution[1]),
                        ("CreateRenderProduct.inputs:width", resolution[0]),
                        ("DepthImagePublisher.inputs:topicName", "depth_image"),
                        ("DepthImagePublisher.inputs:type", "depth"),
                        ("DepthImagePublisher.inputs:frameId", "camera_frame"),
                        ("PointcloudPublisher.inputs:topicName", "depth_pointcloud"),
                        ("PointcloudPublisher.inputs:type", "depth_pcl"),
                        ("PointcloudPublisher.inputs:frameId", "camera_frame"),
                        ("CameraInfoPublisher.inputs:topicName", "camera_info"),
                        ("CameraInfoPublisher.inputs:frameId", "camera_frame"),
                    ],
                    keys.CONNECT: [
                        ("OnPlaybackTick.outputs:tick", "CreateRenderProduct.inputs:execIn"),
                        ("CreateRenderProduct.outputs:execOut", "DepthImagePublisher.inputs:execIn"),
                        ("CreateRenderProduct.outputs:execOut", "PointcloudPublisher.inputs:execIn"),
                        ("CreateRenderProduct.outputs:execOut", "CameraInfoPublisher.inputs:execIn"),
                        (
                            "CreateRenderProduct.outputs:renderProductPath",
                            "DepthImagePublisher.inputs:renderProductPath",
                        ),
                        (
                            "CreateRenderProduct.outputs:renderProductPath",
                            "PointcloudPublisher.inputs:renderProductPath",
                        ),
                        (
                            "CreateRenderProduct.outputs:renderProductPath",
                            "CameraInfoPublisher.inputs:renderProductPath",
                        ),
                    ],
                },
            )
        except Exception as e:
            carb.log_info(f"Graph creation error: {e}")
            raise

        self._timeline.play()
        await omni.kit.app.get_app().next_update_async()

        # Wait for ROS2 messages to be received
        await self._wait_for_ros2_messages(node, depth_image_msg, pointcloud_msg, camera_info_msg)

        # Convert depth image message to numpy array using existing method
        original_depth_image = self.imgmsg_to_cv2(depth_image_msg[0])

        # Extract pointcloud data as numpy array
        pointcloud_data = point_cloud2.read_points_numpy(pointcloud_msg[0], field_names=("x", "y", "z"), skip_nans=True)

        # Project pointcloud back to depth image using OpenCV and full camera calibration
        projected_depth_image = self._project_pointcloud_to_depth_image(
            pointcloud_data, camera_info_msg[0], original_depth_image.shape
        )

        # Save debug images if flags are enabled
        self._save_debug_depth_images(original_depth_image, projected_depth_image, golden_dir, test_dir, "_high_level")

        # Compare images and assert similarity
        self._compare_depth_images_and_assert(
            original_depth_image, projected_depth_image, "high-level API", tolerance_mean=0.1, tolerance_rmse=0.2
        )

        # Clean up
        node.destroy_node()
        self._timeline.stop()

    def _project_pointcloud_to_depth_image(
        self, pointcloud_data: list, camera_info_msg: CameraInfo, image_shape: tuple
    ) -> np.ndarray:
        """Project 3D pointcloud back to depth image using camera calibration.

        Uses OpenCV's cv2.projectPoints to project 3D points back to 2D image coordinates
        and create a depth image. This provides accurate projection using the full camera
        calibration including intrinsics and distortion coefficients.

        Args:
            param pointcloud_data: List of (x, y, z) tuples from pointcloud.
            param camera_info_msg: ROS CameraInfo message containing full camera calibration.
            param image_shape: (height, width) of the target depth image.

        Returns:
            Projected depth image as numpy array with same dimensions as input shape.

        Example:

        .. code-block:: python

            >>> import numpy as np
            >>> # Example pointcloud data
            >>> points = [(1.0, 0.5, 2.0), (-0.5, 1.0, 1.5)]
            >>> # Project to depth image using camera info
            >>> depth_img = self._project_pointcloud_to_depth_image(points, camera_info_msg, (480, 640))
            >>> depth_img.shape
            (480, 640)
        """
        height, width = image_shape
        projected_depth = np.zeros((height, width), dtype=np.float32)

        if pointcloud_data.size == 0:
            return projected_depth

            # Extract x, y, z coordinates from numpy array
        # read_points_numpy returns an array where columns are [x, y, z]
        points_3d = pointcloud_data.astype(np.float32)

        # Filter out invalid points (NaN, inf, or zero depth)
        valid_mask = np.isfinite(points_3d).all(axis=1) & (points_3d[:, 2] > 0)  # Positive Z (depth)
        points_3d = points_3d[valid_mask]

        if len(points_3d) == 0:
            return projected_depth

        # Extract camera parameters from CameraInfo message
        # Camera intrinsic matrix K from CameraInfo
        camera_matrix = np.array(camera_info_msg.k).reshape(3, 3).astype(np.float32)

        # Distortion coefficients from CameraInfo
        # CameraInfo.d contains [k1, k2, t1, t2, k3] for plumb_bob model
        dist_coeffs = None
        if len(camera_info_msg.d) > 0:
            dist_coeffs = np.array(camera_info_msg.d, dtype=np.float32)

        # Use cv2.projectPoints for robust projection with full camera calibration
        # cv2.projectPoints expects points as (N, 1, 3) and no rotation/translation
        points_3d_reshaped = points_3d.reshape(-1, 1, 3)
        rvec = np.zeros((3, 1), dtype=np.float32)  # No rotation
        tvec = np.zeros((3, 1), dtype=np.float32)  # No translation

        # Project points using OpenCV with distortion correction
        projected_points, _ = cv2.projectPoints(points_3d_reshaped, rvec, tvec, camera_matrix, dist_coeffs)

        # Convert projected points back to (N, 2) format
        projected_points = projected_points.reshape(-1, 2)

        # Convert to integer pixel coordinates
        u = projected_points[:, 0].astype(int)
        v = projected_points[:, 1].astype(int)
        depths = points_3d[:, 2]

        # Filter points within image bounds
        valid_pixels = (u >= 0) & (u < width) & (v >= 0) & (v < height)
        u_valid = u[valid_pixels]
        v_valid = v[valid_pixels]
        depths_valid = depths[valid_pixels]

        # Handle multiple points projecting to same pixel by taking the closest depth
        for i in range(len(u_valid)):
            pixel_u, pixel_v, depth = u_valid[i], v_valid[i], depths_valid[i]
            if projected_depth[pixel_v, pixel_u] == 0 or depth < projected_depth[pixel_v, pixel_u]:
                projected_depth[pixel_v, pixel_u] = depth

        return projected_depth

    def _setup_ros2_message_capture(self, node_name: str, topic_prefix: str = ""):
        """Set up ROS2 node and message callbacks for depth pointcloud projection tests.

        Creates a ROS2 node with subscribers for depth image, pointcloud, and camera info messages.
        Sets up callbacks to capture the messages for later processing.

        Args:
            param node_name: Name for the ROS2 node.
            param topic_prefix: Prefix to add to topic names (e.g., "_low_level").

        Returns:
            Tuple containing (node, depth_image_msg, pointcloud_msg, camera_info_msg) where
            the message variables are lists that will be populated by the callbacks.

        Example:

        .. code-block:: python

            >>> node, msgs = self._setup_ros2_message_capture("test_node", "_low_level")
            >>> # msgs will contain [depth_image_msg, pointcloud_msg, camera_info_msg] as lists
        """
        import rclpy

        node = rclpy.create_node(node_name)

        # Use lists to store messages (mutable for callback closure)
        depth_image_msg = [None]
        pointcloud_msg = [None]
        camera_info_msg = [None]

        # Set up message callbacks
        def depth_image_callback(msg: Image):
            depth_image_msg[0] = msg

        def pointcloud_callback(msg: PointCloud2):
            pointcloud_msg[0] = msg

        def camera_info_callback(msg: CameraInfo):
            camera_info_msg[0] = msg

        # Create subscribers
        depth_image_sub = node.create_subscription(
            Image, f"depth_image{topic_prefix}", depth_image_callback, get_qos_profile()
        )
        pointcloud_sub = node.create_subscription(
            PointCloud2, f"depth_pointcloud{topic_prefix}", pointcloud_callback, get_qos_profile()
        )
        camera_info_sub = node.create_subscription(
            CameraInfo, f"camera_info{topic_prefix}", camera_info_callback, get_qos_profile()
        )

        return node, depth_image_msg, pointcloud_msg, camera_info_msg

    async def _wait_for_ros2_messages(
        self, node, depth_image_msg, pointcloud_msg, camera_info_msg, timeout_iterations: int = 50
    ):
        """Wait for ROS2 messages to be received.

        Spins the ROS2 node until all three message types are received or timeout is reached.

        Args:
            param node: ROS2 node to spin.
            param depth_image_msg: List containing depth image message (modified in place).
            param pointcloud_msg: List containing pointcloud message (modified in place).
            param camera_info_msg: List containing camera info message (modified in place).
            param timeout_iterations: Maximum number of spin iterations before timeout.

        Raises:
            AssertionError: If any required messages are not received within timeout.

        Example:

        .. code-block:: python

            >>> await self._wait_for_ros2_messages(node, depth_msg, pc_msg, info_msg)
            >>> # All messages should now be populated
        """
        import rclpy

        # Spin ROS2 node to receive messages
        for _ in range(timeout_iterations):
            rclpy.spin_once(node, timeout_sec=0.1)
            if all([depth_image_msg[0], pointcloud_msg[0], camera_info_msg[0]]):
                break
            await omni.kit.app.get_app().next_update_async()

        # Verify we received all messages
        self.assertIsNotNone(depth_image_msg[0], "Failed to receive depth image message")
        self.assertIsNotNone(pointcloud_msg[0], "Failed to receive pointcloud message")
        self.assertIsNotNone(camera_info_msg[0], "Failed to receive camera info message")

    def _save_debug_depth_images(
        self,
        original_depth_image: np.ndarray,
        projected_depth_image: np.ndarray,
        golden_dir: str,
        test_dir: str,
        suffix: str = "",
    ):
        """Save debug depth images for visual inspection.

        Uses the save_depth_image utility from isaacsim.test.utils.image_capture for proper
        depth image handling with automatic normalization and format selection.

        Args:
            param original_depth_image: Original depth image from camera.
            param projected_depth_image: Depth image projected from pointcloud.
            param golden_dir: Directory for golden reference images.
            param test_dir: Directory for test output images.
            param suffix: Suffix to add to filenames (e.g., "_low_level").

        Example:

        .. code-block:: python

            >>> self._save_debug_depth_images(orig_img, proj_img, "/golden", "/test", "_low_level")
            >>> # Images saved with proper depth handling if debug flags are enabled
        """
        if not (SAVE_DEPTH_IMAGES_AS_TEST or SAVE_DEPTH_IMAGES_AS_GOLDEN):
            return

        # Save original depth image with proper normalization
        if SAVE_DEPTH_IMAGES_AS_TEST:
            save_depth_image(original_depth_image, test_dir, f"original_depth{suffix}.png", normalize=True)
            save_depth_image(projected_depth_image, test_dir, f"projected_depth{suffix}.png", normalize=True)

        if SAVE_DEPTH_IMAGES_AS_GOLDEN:
            save_depth_image(original_depth_image, golden_dir, f"original_depth{suffix}.png", normalize=True)
            save_depth_image(projected_depth_image, golden_dir, f"projected_depth{suffix}.png", normalize=True)

    def _compare_depth_images_and_assert(
        self,
        original_depth_image: np.ndarray,
        projected_depth_image: np.ndarray,
        test_name: str = "",
        tolerance_mean: float = 0.1,
        tolerance_rmse: float = 0.5,
    ):
        """Compare depth images and assert they are within tolerance.

        Uses compute_difference_metrics to compare images and logs detailed metrics.
        Asserts that mean absolute difference and RMSE are within specified tolerances.

        Args:
            param original_depth_image: Original depth image from camera.
            param projected_depth_image: Depth image projected from pointcloud.
            param test_name: Name of the test for logging purposes.
            param tolerance_mean: Maximum allowed mean absolute difference.
            param tolerance_rmse: Maximum allowed RMSE.

        Raises:
            AssertionError: If images differ more than specified tolerances.

        Example:

        .. code-block:: python

            >>> self._compare_depth_images_and_assert(orig_img, proj_img, "high_level_api")
            >>> # Logs metrics and asserts similarity within default tolerances
        """
        from isaacsim.test.utils.image_comparison import compute_difference_metrics, print_difference_statistics

        metrics = compute_difference_metrics(original_depth_image, projected_depth_image, ignore_blank_pixels=True)

        if SAVE_DEPTH_IMAGES_AS_GOLDEN or SAVE_DEPTH_IMAGES_AS_TEST:
            print_difference_statistics(metrics)

        # Assert that the images are reasonably similar
        self.assertLess(
            metrics["mean_abs"],
            tolerance_mean,
            f"Mean absolute difference {metrics['mean_abs']} exceeds tolerance of {tolerance_mean}",
        )
        self.assertLess(
            metrics["rmse"], tolerance_rmse, f"RMSE {metrics['rmse']} exceeds tolerance of {tolerance_rmse}"
        )

    async def _setup_test_scene_with_objects(self):
        """Set up test scene with simple room environment.

        Loads the Simple Room environment which provides sufficient depth variation
        for meaningful pointcloud and depth image data from the existing scene geometry.

        Example:

        .. code-block:: python

            >>> await self._setup_test_scene_with_objects()
            >>> # Simple room scene loaded
        """
        # Load a simple scene
        scene_path = "/Isaac/Environments/Simple_Room/simple_room.usd"
        await open_stage_async(self._assets_root_path + scene_path)

        await omni.kit.app.get_app().next_update_async()

    async def test_depth_pointcloud_projection_low_level_api(self):
        """Test depth pointcloud projection using low-level USD API.

        This test verifies that a depth pointcloud published by ROS2CameraHelper can be projected
        back to a depth image using OpenCV and camera intrinsic parameters from CameraInfo,
        and that the projected image matches the original depth image within acceptable tolerances.

        Uses the low-level UsdGeom.Camera API for camera creation and manual render product setup.

        Example:

        .. code-block:: python

            # This test is run automatically as part of the test suite
            >>> # The test creates a camera using UsdGeom.Camera API, manually creates render product
            >>> # publishes depth data and pointcloud, then projects the pointcloud back to verify consistency
        """
        # Set up test scene with objects
        await self._setup_test_scene_with_objects()

        # Create camera using low-level USD API
        from isaacsim.core.utils.prims import define_prim
        from pxr import Gf, UsdGeom

        camera_prim_path = "/World/CameraLowLevel"
        camera_prim = UsdGeom.Camera(define_prim(prim_path=camera_prim_path, prim_type="Camera"))

        # Set camera transform and properties
        from isaacsim.core.utils.xforms import reset_and_set_xform_ops

        # Set up camera transform using XFormPrim for convenience
        orientation = rot_utils.euler_angles_to_quats(np.array([90, 0, 90]), degrees=True)
        reset_and_set_xform_ops(
            camera_prim.GetPrim(),
            Gf.Vec3d([4, 0, 0.0]),
            Gf.Quatd(orientation[0], orientation[1], orientation[2], orientation[3]),
        )

        # Set camera properties
        resolution = (1280, 720)
        focal_length = 1.814756
        # USD Camera focal length is in tenths of world units (decimeters) while the high-level Camera API
        # expects focal length in world units (meters). Multiply by 10 to convert from meters to decimeters.
        camera_prim.GetFocalLengthAttr().Set(focal_length * 10)

        # Set up directories for debug image saving
        golden_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data", "golden")
        test_dir = carb.tokens.get_tokens_interface().resolve("${temp}/test_depth_pointcloud_projection_low_level")

        # Set up ROS2 message capture
        node, depth_image_msg, pointcloud_msg, camera_info_msg = self._setup_ros2_message_capture(
            "depth_pointcloud_projection_tester_low_level", "_low_level"
        )

        # Create OmniGraph to publish camera data
        graph_path = "/ActionGraphLowLevel"

        try:
            keys = og.Controller.Keys
            (graph, nodes, _, _) = og.Controller.edit(
                {"graph_path": graph_path, "evaluator_name": "execution"},
                {
                    keys.CREATE_NODES: [
                        ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                        ("ReadSimTime", "isaacsim.core.nodes.IsaacReadSimulationTime"),
                        ("CreateRenderProduct", "isaacsim.core.nodes.IsaacCreateRenderProduct"),
                        ("DepthImagePublisher", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                        ("PointcloudPublisher", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                        ("CameraInfoPublisher", "isaacsim.ros2.bridge.ROS2CameraInfoHelper"),
                    ],
                    keys.SET_VALUES: [
                        ("CreateRenderProduct.inputs:cameraPrim", [usdrt.Sdf.Path(camera_prim_path)]),
                        ("CreateRenderProduct.inputs:height", resolution[1]),
                        ("CreateRenderProduct.inputs:width", resolution[0]),
                        ("DepthImagePublisher.inputs:topicName", "depth_image_low_level"),
                        ("DepthImagePublisher.inputs:type", "depth"),
                        ("DepthImagePublisher.inputs:frameId", "camera_frame"),
                        ("PointcloudPublisher.inputs:topicName", "depth_pointcloud_low_level"),
                        ("PointcloudPublisher.inputs:type", "depth_pcl"),
                        ("PointcloudPublisher.inputs:frameId", "camera_frame"),
                        ("CameraInfoPublisher.inputs:topicName", "camera_info_low_level"),
                        ("CameraInfoPublisher.inputs:frameId", "camera_frame"),
                    ],
                    keys.CONNECT: [
                        ("OnPlaybackTick.outputs:tick", "CreateRenderProduct.inputs:execIn"),
                        ("CreateRenderProduct.outputs:execOut", "DepthImagePublisher.inputs:execIn"),
                        ("CreateRenderProduct.outputs:execOut", "PointcloudPublisher.inputs:execIn"),
                        ("CreateRenderProduct.outputs:execOut", "CameraInfoPublisher.inputs:execIn"),
                        (
                            "CreateRenderProduct.outputs:renderProductPath",
                            "DepthImagePublisher.inputs:renderProductPath",
                        ),
                        (
                            "CreateRenderProduct.outputs:renderProductPath",
                            "PointcloudPublisher.inputs:renderProductPath",
                        ),
                        (
                            "CreateRenderProduct.outputs:renderProductPath",
                            "CameraInfoPublisher.inputs:renderProductPath",
                        ),
                    ],
                },
            )
        except Exception as e:
            carb.log_info(f"Graph creation error: {e}")
            raise

        self._timeline.play()
        await omni.kit.app.get_app().next_update_async()

        # Wait for ROS2 messages to be received
        await self._wait_for_ros2_messages(node, depth_image_msg, pointcloud_msg, camera_info_msg)

        # Convert depth image message to numpy array using existing method
        original_depth_image = self.imgmsg_to_cv2(depth_image_msg[0])

        # Extract pointcloud data as numpy array
        pointcloud_data = point_cloud2.read_points_numpy(pointcloud_msg[0], field_names=("x", "y", "z"), skip_nans=True)

        # Project pointcloud back to depth image using OpenCV and full camera calibration
        projected_depth_image = self._project_pointcloud_to_depth_image(
            pointcloud_data, camera_info_msg[0], original_depth_image.shape
        )

        # Save debug images if flags are enabled
        self._save_debug_depth_images(original_depth_image, projected_depth_image, golden_dir, test_dir, "_low_level")

        # Compare images and assert similarity
        self._compare_depth_images_and_assert(
            original_depth_image, projected_depth_image, "low-level API", tolerance_mean=0.1, tolerance_rmse=0.5
        )

        # Cleanup
        node.destroy_node()

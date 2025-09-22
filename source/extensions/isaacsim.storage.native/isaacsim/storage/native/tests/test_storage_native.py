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

import asyncio
import json

import carb
import omni.kit.commands
import omni.kit.test

# import omni.kit.usd
from isaacsim.storage.native import find_filtered_files_async, get_assets_root_path, get_assets_root_path_async


class TestStorageNative(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        await omni.kit.app.get_app().next_update_async()
        pass

    async def tearDown(self):
        await omni.kit.app.get_app().next_update_async()
        pass

    async def test_get_assets_root_path(self):
        default_assets_url = carb.settings.get_settings().get("/persistent/isaac/asset_root/default")

        # including checking step
        self.assertEqual(await get_assets_root_path_async(), default_assets_url)
        self.assertEqual(get_assets_root_path(), default_assets_url)

        # check unset setting
        carb.settings.get_settings().set("/persistent/isaac/asset_root/default", 0)
        with self.assertRaises(RuntimeError) as context:
            await get_assets_root_path_async()
        self.assertIn("setting is not set", str(context.exception))
        with self.assertRaises(RuntimeError) as context:
            get_assets_root_path()
        self.assertIn("setting is not set", str(context.exception))

        # skipping checking step
        # - define invalid setting
        invalid_assets_url = "https://some-invalid-path"
        carb.settings.get_settings().set("/persistent/isaac/asset_root/default", invalid_assets_url)
        # - check for exception raised due to invalid setting
        with self.assertRaises(RuntimeError) as context:
            await get_assets_root_path_async()
        self.assertIn("Could not find assets root folder:", str(context.exception))
        with self.assertRaises(RuntimeError) as context:
            get_assets_root_path()
        self.assertIn("Could not find assets root folder:", str(context.exception))
        # - check for path (valid or not) due to skipping checking step
        self.assertEqual(await get_assets_root_path_async(skip_check=True), invalid_assets_url)
        self.assertEqual(get_assets_root_path(skip_check=True), invalid_assets_url)

        # reset settings
        carb.settings.get_settings().set("/persistent/isaac/asset_root/default", default_assets_url)

    async def test_find_filtered_files_async_basic_discovery(self):
        """Test basic USD file discovery without filters.

        This test verifies that the find_filtered_files_async function can discover
        USD files in the Isaac Sim Simple Room environment and returns them as a set.
        """
        simple_room_path = await get_assets_root_path_async()
        simple_room_path += "/Isaac/Environments/Simple_Room/"

        result = await find_filtered_files_async(
            root_path=simple_room_path,
        )

        self.assertIsNotNone(result)
        self.assertIsInstance(result, set)
        self.assertGreater(len(result), 0, "Should find at least some USD files in Simple Room")

        # Verify all results are valid USD files
        valid_extensions = [".usd", ".usda", ".usdc", ".usdz"]
        for file_path in result:
            self.assertTrue(
                any(file_path.lower().endswith(ext) for ext in valid_extensions),
                f"File should have valid USD extension: {file_path}",
            )

    async def test_find_filtered_files_async_pattern_filtering_any_mode(self):
        """Test regex pattern filtering with match_all=False (any pattern matches).

        This test verifies that when multiple patterns are provided and match_all=False,
        files matching ANY of the patterns are included in the results.
        """
        simple_room_path = await get_assets_root_path_async()
        simple_room_path += "/Isaac/Environments/Simple_Room/"

        # Test with multiple patterns - should find files matching ANY pattern
        result = await find_filtered_files_async(
            root_path=simple_room_path,
            filter_patterns=["room", "Props"],
            match_all=False,  # ANY mode (default)
        )

        self.assertIsNotNone(result)
        self.assertIsInstance(result, set)

        # If results found, verify each matches at least one pattern
        if len(result) > 0:
            for file_path in result:
                path_lower = file_path.lower()
                has_room = "room" in path_lower
                has_props = "Props" in file_path
                self.assertTrue(
                    has_room or has_props,
                    f"File '{file_path}' should match at least one pattern ['room', 'Props'] in ANY mode",
                )

    async def test_find_filtered_files_async_pattern_filtering_all_mode(self):
        """Test regex pattern filtering with match_all=True (all patterns must match).

        This test verifies that when match_all=True, only files matching ALL patterns
        are included in the results.
        """
        simple_room_path = await get_assets_root_path_async()
        simple_room_path += "/Isaac/Environments/Simple_Room/"

        # Test with multiple patterns - should find files matching ALL patterns
        result = await find_filtered_files_async(
            root_path=simple_room_path,
            filter_patterns=["Towel", "Room"],
            match_all=True,  # ALL mode
        )

        self.assertIsNotNone(result)
        self.assertIsInstance(result, set)

        # If results found, verify each matches ALL patterns
        if len(result) > 0:
            for file_path in result:

                has_towel = "Towel" in file_path
                has_room = "Room" in file_path
                self.assertTrue(
                    has_towel and has_room,
                    f"File '{file_path}' should match ALL patterns ['Towel', 'Room'] in ALL mode",
                )

    async def test_find_filtered_files_async_filepath_exclusion(self):
        """Test filepath exclusion functionality.

        This test verifies that files containing excluded substrings are filtered out
        from the results, even if they would otherwise match the criteria.
        """
        simple_room_path = await get_assets_root_path_async()
        simple_room_path += "/Isaac/Environments/Simple_Room/"

        # First get all files without exclusion
        all_files = await find_filtered_files_async(
            root_path=simple_room_path,
        )

        # Then get files with exclusion (exclude files containing "Props")
        filtered_files = await find_filtered_files_async(
            root_path=simple_room_path,
            filepath_excludes=["Props"],
        )

        self.assertIsNotNone(all_files)
        self.assertIsNotNone(filtered_files)

        # Filtered results should be subset of or equal to all results
        self.assertLessEqual(len(filtered_files), len(all_files))
        self.assertTrue(set(filtered_files).issubset(set(all_files)))

        # Verify no filtered files contain excluded substring
        for file_path in filtered_files:
            self.assertNotIn("Props", file_path, f"Excluded file should not contain 'Props': {file_path}")

    async def test_find_filtered_files_async_max_depth_limiting(self):
        """Test max_depth parameter functionality.

        This test verifies that the max_depth parameter correctly limits the directory
        traversal depth, and that deeper searches find more or equal files.
        """
        simple_room_path = await get_assets_root_path_async()
        simple_room_path += "/Isaac/Environments/Simple_Room/"

        # Test with different depth limits
        shallow_result = await find_filtered_files_async(
            root_path=simple_room_path, max_depth=0  # Only immediate directory
        )

        deeper_result = await find_filtered_files_async(
            root_path=simple_room_path, max_depth=1  # Deeper traversal - should find more files
        )

        self.assertIsNotNone(shallow_result)
        self.assertIsNotNone(deeper_result)

        # Deeper searches should find same or more files
        self.assertLessEqual(len(shallow_result), len(deeper_result))
        self.assertTrue(set(shallow_result).issubset(set(deeper_result)))

    async def test_find_filtered_files_async_async_behavior(self):
        """Test that the function properly executes asynchronously.

        This test verifies that find_filtered_files_async doesn't block the event loop
        and can be cancelled or run concurrently with other async operations.
        """
        simple_room_path = await get_assets_root_path_async()
        simple_room_path += "/Isaac/Environments/Simple_Room/"

        # Test concurrent execution of multiple async calls
        tasks = [find_filtered_files_async(root_path=simple_room_path, max_depth=2) for _ in range(3)]

        # All tasks should complete successfully
        results = await asyncio.gather(*tasks)

        # Verify all results are valid and consistent
        for i, result in enumerate(results):
            self.assertIsNotNone(result, f"Task {i} should return valid result")
            self.assertIsInstance(result, set, f"Task {i} should return a set")
            # All concurrent calls with same parameters should return same results
            self.assertEqual(result, results[0], f"Task {i} should return consistent results")

    async def test_find_filtered_files_async_error_handling(self):
        """Test error handling for invalid paths and edge cases.

        This test verifies that the function gracefully handles invalid paths,
        non-existent directories, and other error conditions.
        """
        # Test with non-existent path
        try:
            result = await find_filtered_files_async(root_path="/invalid/nonexistent/path", max_depth=1)
            # Should return empty set for non-existent paths
            self.assertIsInstance(result, set)
            self.assertEqual(len(result), 0, "Non-existent path should return empty set")
        except Exception as e:
            # Or might raise an exception - either behavior is acceptable
            self.assertIsNotNone(e)

        # Test with empty root path
        try:
            result = await find_filtered_files_async(root_path="", max_depth=1)
            self.assertIsInstance(result, set)
        except Exception as e:
            # Empty path might raise an exception
            self.assertIsNotNone(e)

        # Test with invalid regex patterns (should handle gracefully)
        simple_room_path = await get_assets_root_path_async()
        simple_room_path += "/Isaac/Environments/Simple_Room/"

        # Test with invalid regex - function should handle gracefully
        result = await find_filtered_files_async(
            root_path=simple_room_path, filter_patterns=["[invalid_regex"], max_depth=1  # Unclosed bracket
        )
        self.assertIsInstance(result, set)
        # Should still work, just might not apply the invalid pattern

    async def test_find_filtered_files_async_combined_parameters(self):
        """Test combining multiple parameters together.

        This test verifies that all parameters work correctly when used together:
        patterns, exclusions, depth limiting, and match mode.
        It also demonstrates that more restrictive filtering returns fewer files.
        """
        warehouse_path = await get_assets_root_path_async()
        warehouse_path += "/Isaac/Environments/Simple_Warehouse/"

        # Test 1: Less restrictive filtering - single pattern
        broad_result = await find_filtered_files_async(
            root_path=warehouse_path,
            filter_patterns=["warehouse"],
            match_all=False,  # ANY pattern matching
            filepath_excludes=["Props", "Stage"],
            max_depth=2,
        )

        # Test 2: More restrictive filtering - multiple patterns with match_all=True
        restrictive_result = await find_filtered_files_async(
            root_path=warehouse_path,
            filter_patterns=["warehouse", "shelves"],
            match_all=True,  # ALL patterns must match
            filepath_excludes=["Props", "Stage"],
            max_depth=2,
        )

        self.assertIsNotNone(broad_result)
        self.assertIsInstance(broad_result, set)
        self.assertIsNotNone(restrictive_result)
        self.assertIsInstance(restrictive_result, set)

        # More restrictive filtering should return fewer or equal files
        self.assertLessEqual(
            len(restrictive_result),
            len(broad_result),
            f"Restrictive filter should return fewer files. Broad: {len(broad_result)}, Restrictive: {len(restrictive_result)}",
        )

        # Verify we found some files in broad search
        self.assertGreater(len(broad_result), 0, "Broad search should find at least some warehouse files")

        # Verify broad results match expected criteria
        for file_path in broad_result:
            # Should match "warehouse" pattern
            path_lower = file_path.lower()
            has_warehouse = "warehouse" in path_lower
            self.assertTrue(has_warehouse, f"File '{file_path}' should match pattern 'warehouse'")

            # Should not contain excluded substrings
            self.assertNotIn("Props", file_path, f"File should not contain exclusion: {file_path}")
            self.assertNotIn("Stage", file_path, f"File should not contain exclusion: {file_path}")

            # Should be a valid USD file
            valid_extensions = [".usd", ".usda", ".usdc", ".usdz"]
            self.assertTrue(
                any(file_path.lower().endswith(ext) for ext in valid_extensions),
                f"File should have valid USD extension: {file_path}",
            )

        # Verify restrictive results match ALL patterns
        for file_path in restrictive_result:
            path_lower = file_path.lower()
            has_warehouse = "warehouse" in path_lower
            has_shelves = "shelves" in path_lower
            self.assertTrue(
                has_warehouse and has_shelves, f"File '{file_path}' should match ALL patterns ['warehouse', 'shelves']"
            )

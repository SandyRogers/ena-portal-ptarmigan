import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ptarmigan.config import app_config
import httpx

from ptarmigan.data_state import clear_cache, get_data


class DataCacheTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_cache_dir = app_config.cache.cache_dir
        app_config.cache.cache_dir = self.temporary_directory.name

    def tearDown(self):
        app_config.cache.cache_dir = self.original_cache_dir
        self.temporary_directory.cleanup()

    @patch("ptarmigan.data_state.httpx.get")
    def test_cached_response_is_read_without_second_request(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="accession\ttitle\nMGYS1\tExample\n",
            json=Mock(side_effect=ValueError),
            raise_for_status=Mock(),
        )

        first = get_data("search?result=study")
        second = get_data("search?result=study")

        self.assertEqual(first.data.to_dict(), second.data.to_dict())
        mock_get.assert_called_once()

    @patch("ptarmigan.data_state.httpx.get")
    def test_clear_cache_removes_cached_responses(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="accession\nMGYS1\n",
            json=Mock(side_effect=ValueError),
            raise_for_status=Mock(),
        )
        get_data("search?result=study")

        clear_cache()

        self.assertFalse((Path(app_config.cache.cache_dir) / "data").exists())

    @patch("ptarmigan.data_state.httpx.get")
    def test_json_response_is_converted_to_dataframe(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text='[{"resultId":"study"},{"resultId":"sample"}]',
            json=Mock(
                return_value=[
                    {"resultId": "study"},
                    {"resultId": "sample"},
                ]
            ),
            raise_for_status=Mock(),
        )

        result = get_data("results?format=json", use_cache=False)

        self.assertIsNone(result.error)
        self.assertEqual(
            result.data["resultId"].tolist(),
            ["study", "sample"],
        )

    @patch("ptarmigan.data_state.httpx.get")
    def test_http_failure_returns_error_result(self, mock_get):
        request = httpx.Request("GET", "https://example.test")
        mock_get.side_effect = httpx.ConnectError("offline", request=request)

        result = get_data("results?format=tsv", use_cache=False)

        self.assertTrue(result.data.empty)
        self.assertIn("offline", result.error)

    @patch("ptarmigan.data_state.httpx.get")
    def test_cache_is_json_not_pickle(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="accession\nMGYS1\n",
            json=Mock(side_effect=ValueError),
            raise_for_status=Mock(),
        )

        get_data("search?result=study")

        cache_files = list(
            (Path(app_config.cache.cache_dir) / "data").glob("*.json")
        )
        self.assertEqual(len(cache_files), 1)
        self.assertIn('"data"', cache_files[0].read_text())


if __name__ == "__main__":
    unittest.main()

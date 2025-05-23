#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#
import os
from datetime import datetime
from unittest.mock import ANY, MagicMock, Mock, PropertyMock, call, patch

import pytest
from office365.entity_collection import EntityCollection
from office365.graph_client import GraphClient
from office365.onedrive.drives.drive import Drive
from office365.sharepoint.client_context import ClientContext
from office365.sharepoint.search.service import SearchService
from requests.exceptions import HTTPError
from source_microsoft_sharepoint.exceptions import ErrorFetchingMetadata
from source_microsoft_sharepoint.spec import SourceMicrosoftSharePointSpec
from source_microsoft_sharepoint.stream_reader import (
    FileReadMode,
    MicrosoftSharePointRemoteFile,
    SourceMicrosoftSharePointClient,
    SourceMicrosoftSharePointStreamReader,
)
from wcmatch.glob import GLOBSTAR, globmatch

from airbyte_cdk import AirbyteTracedException


TEST_LOCAL_DIRECTORY = "/tmp/airbyte-file-transfer"


def create_mock_drive_item(is_file, name, children=None):
    """Helper function to create a mock drive item."""
    mock_item = MagicMock(
        properties={
            "@microsoft.graph.downloadUrl": "test_url",
            "lastModifiedDateTime": datetime(1991, 8, 24),
            "createdDateTime": datetime(1991, 8, 24),
        }
    )
    mock_item.is_file = is_file
    mock_item.name = name
    mock_item.children.get.return_value.execute_query = Mock(return_value=children or [])
    return mock_item


@pytest.fixture
def setup_reader_class():
    reader = SourceMicrosoftSharePointStreamReader()  # Instantiate your class here
    config = Mock(spec=SourceMicrosoftSharePointSpec)
    config.start_date = None
    config.credentials = Mock()
    config.folder_path = "."
    config.site_url = ""
    config.credentials.auth_type = "Client"
    config.search_scope = "ALL"
    reader.config = config  # Set up the necessary configuration

    # Mock the client creation
    with patch("source_microsoft_sharepoint.stream_reader.SourceMicrosoftSharePointClient") as mock_client_class:
        mock_client = mock_client_class.return_value
        mock_client.client = Mock()  # Mock the client attribute of SourceMicrosoftSharePointClient
        yield reader


@pytest.fixture(name="mock_drive_files")
def create_mock_drive_files():
    """
    Provides mock data for SharePoint drive files (personal drive).
    """
    return [
        MicrosoftSharePointRemoteFile(
            uri="file1.csv",
            download_url="https://example.com/file1.csv",
            last_modified=datetime(2021, 1, 1),
            created_at=datetime(2021, 1, 1),
        ),
        MicrosoftSharePointRemoteFile(
            uri="file2.txt",
            download_url="https://example.com/file2.txt",
            last_modified=datetime(2021, 1, 1),
            created_at=datetime(2021, 1, 1),
        ),
    ]


@pytest.fixture(name="mock_shared_drive_files")
def create_mock_shared_drive_files():
    """
    Provides mock data for SharePoint drive files (shared drives).
    """
    return [
        MicrosoftSharePointRemoteFile(
            uri="file3.csv",
            download_url="https://example.com/file3.csv",
            last_modified=datetime(2021, 3, 1),
            created_at=datetime(2021, 3, 1),
        ),
        MicrosoftSharePointRemoteFile(
            uri="file4.txt",
            download_url="https://example.com/file4.txt",
            last_modified=datetime(2021, 4, 1),
            created_at=datetime(2021, 4, 1),
        ),
    ]


@pytest.fixture
def setup_client_class():
    config = Mock(spec=SourceMicrosoftSharePointSpec)
    config.credentials = Mock()
    config.folder_path = "."
    config.credentials.auth_type = "Client"

    with patch("source_microsoft_sharepoint.stream_reader.ConfidentialClientApplication") as mock_client_class:
        mock_msal_app_instance = Mock()
        mock_client_class.return_value = mock_msal_app_instance

        client_class = SourceMicrosoftSharePointClient(config)

        yield client_class


@pytest.mark.parametrize(
    "has_refresh_token, token_response, expected_result, raises_exception",
    [
        (False, {"access_token": "test_access_token"}, {"access_token": "test_access_token"}, False),
        (True, {"access_token": "test_access_token"}, {"access_token": "test_access_token"}, False),
        (False, {"error": "test_error", "error_description": "test_error_description"}, None, True),
    ],
)
def test_get_access_token(setup_client_class, has_refresh_token, token_response, expected_result, raises_exception):
    instance = setup_client_class
    if has_refresh_token:
        instance.config.credentials.refresh_token = "test_refresh_token"
        instance._msal_app.acquire_token_by_refresh_token.return_value = token_response
    else:
        instance.config.credentials.refresh_token = None
        instance._msal_app.acquire_token_for_client.return_value = token_response

    if raises_exception:
        with pytest.raises(AirbyteTracedException) as exception:
            instance._get_access_token()
        assert exception.value.message == f"Failed to acquire access token. Error: test_error. Error description: test_error_description."
    else:
        assert instance._get_access_token() == expected_result

        if has_refresh_token:
            instance._msal_app.acquire_token_by_refresh_token.assert_called_once_with(
                "test_refresh_token", scopes=["https://graph.microsoft.com/.default"]
            )
        else:
            instance._msal_app.acquire_token_for_client.assert_called_once_with(scopes=["https://graph.microsoft.com/.default"])


@patch("source_microsoft_sharepoint.stream_reader.execute_query_with_retry")
@patch("source_microsoft_sharepoint.stream_reader.SourceMicrosoftSharePointStreamReader.filter_files_by_globs_and_start_date")
def test_get_matching_files(mock_filter_files, mock_execute_query, setup_reader_class, mock_drive_files, mock_shared_drive_files):
    instance = setup_reader_class
    instance._get_files_by_drive_name = Mock(return_value=mock_drive_files)
    instance._get_shared_files_from_all_drives = Mock(return_value=mock_shared_drive_files)

    # Set up mocks
    mock_drive = Mock()
    mock_drive.get.return_value = mock_drive
    mock_execute_query.return_value = mock_drive
    mock_filter_files.side_effect = lambda files, globs: (f for f in files if any(globmatch(f.uri, g, flags=GLOBSTAR) for g in globs))

    # Define test parameters
    globs = ["*.csv"]
    prefix = None
    logger = Mock()

    # Call the method
    files = list(instance.get_matching_files(globs, prefix, logger))

    # Assertions
    assert len(files) == 2

    assert isinstance(files[0], MicrosoftSharePointRemoteFile)
    assert files[0].uri == "file1.csv"
    assert "https://example.com/file1.csv" in files[0].download_url

    assert isinstance(files[1], MicrosoftSharePointRemoteFile)
    assert files[1].uri == "file3.csv"
    assert "https://example.com/file3.csv" in files[1].download_url


def test_get_matching_files_empty_drive(setup_reader_class):
    instance = setup_reader_class
    instance._get_files_by_drive_name = Mock(return_value=iter([]))
    instance._get_shared_files_from_all_drives = Mock(return_value=iter([]))

    # Define test parameters
    globs = ["*.csv"]
    prefix = None
    logger = Mock()

    # Expecting an exception when drive is empty
    with pytest.raises(AirbyteTracedException):
        list(instance.get_matching_files(globs, prefix, logger))


@pytest.mark.parametrize(
    "file_extension, expected_compression",
    [
        (".txt.gz", ".gz"),
        (".txt.bz2", ".bz2"),
        ("txt", "disable"),
    ],
)
@patch("smart_open.open")
def test_open_file(mock_smart_open, file_extension, expected_compression):
    """Test the open_file method in SourceMicrosoftSharePointStreamReader."""
    mock_file = Mock(download_url=f"https://example.com/file.{file_extension}", uri=f"file.{file_extension}")
    mock_logger = Mock()

    stream_reader = SourceMicrosoftSharePointStreamReader()
    stream_reader._config = Mock()  # Assuming _config is required

    with stream_reader.open_file(mock_file, FileReadMode.READ, "utf-8", mock_logger) as result:
        pass

    mock_smart_open.assert_called_once_with(mock_file.download_url, mode="r", encoding="utf-8", compression=expected_compression)
    assert result is not None


@pytest.mark.parametrize(
    "file_uri, file_extension, expected_paths",
    [
        (
            "https://my_favorite_sharepoint.sharepoint.com/Shared%20Documents/file",
            "txt.gz",
            {"bytes": ANY, "source_file_relative_path": "file.txt.gz", "staging_file_url": f"{TEST_LOCAL_DIRECTORY}/file.txt.gz"},
        ),
        (
            "https://my_favorite_sharepoint.sharepoint.com/Shared%20Documents/file",
            "txt.bz2",
            {"bytes": ANY, "source_file_relative_path": "file.txt.bz2", "staging_file_url": f"{TEST_LOCAL_DIRECTORY}/file.txt.bz2"},
        ),
        (
            "https://my_favorite_sharepoint.sharepoint.com/Shared%20Documents/file",
            "txt",
            {"bytes": ANY, "source_file_relative_path": "file.txt", "staging_file_url": f"{TEST_LOCAL_DIRECTORY}/file.txt"},
        ),
        (
            "https://my_favorite_sharepoint.sharepoint.com/sites/NOT_DEFAULT_SITE/Shared%20Documents/file",
            "txt.gz",
            {"bytes": ANY, "source_file_relative_path": "file.txt.gz", "staging_file_url": f"{TEST_LOCAL_DIRECTORY}/file.txt.gz"},
        ),
        (
            "https://my_favorite_sharepoint.sharepoint.com/sites/NOT_DEFAULT_SITE/Shared%20Documents/file",
            "txt.bz2",
            {"bytes": ANY, "source_file_relative_path": "file.txt.bz2", "staging_file_url": f"{TEST_LOCAL_DIRECTORY}/file.txt.bz2"},
        ),
        (
            "https://my_favorite_sharepoint.sharepoint.com/sites/NOT_DEFAULT_SITE/Shared%20Documents/some/path/to/file",
            "txt",
            {
                "bytes": ANY,
                "source_file_relative_path": "some/path/to/file.txt",
                "staging_file_url": f"{TEST_LOCAL_DIRECTORY}/some/path/to/file.txt",
            },
        ),
    ],
)
@patch("source_microsoft_sharepoint.stream_reader.SourceMicrosoftSharePointStreamReader.get_access_token")
@patch("source_microsoft_sharepoint.stream_reader.requests.get")
@patch("source_microsoft_sharepoint.stream_reader.requests.head")
def test_get_file(mock_requests_head, mock_requests_get, mock_get_access_token, file_uri, file_extension, expected_paths):
    """
    Test the get_file method in SourceMicrosoftSharePointStreamReader.

    This test verifies that the get_file method correctly "downloads" (mocked) a file from SharePoint
    and saves it to the specified local directory. It mocks the necessary HTTP requests
    and checks that the resulting file paths and sizes match the expected values.

    Args:
        mock_requests_head (MagicMock): Mock for the requests.head method.
        mock_requests_get (MagicMock): Mock for the requests.get method.
        mock_get_access_token (MagicMock): Mock for the get_access_token method.
        file_extension (str): The file extension to test (e.g., 'txt.gz').
        expected_paths (dict): The expected paths and file size in the result.
    """
    file_uri = f"{file_uri}.{file_extension}"
    mock_file = Mock(download_url=f"https://example.com/file.{file_extension}", uri=file_uri)
    mock_file.last_modified = datetime(2021, 1, 1)
    mock_file.created_at = datetime(2021, 1, 1)
    mock_logger = Mock()
    mock_get_access_token.return_value = "dummy_access_token"

    # Create a mock response for requests.head
    mock_head_response = Mock()
    mock_head_response.status_code = 200
    mock_head_response.headers = {"Content-Length": "12345"}
    mock_requests_head.return_value = mock_head_response

    # Create a mock response for requests.get
    mock_response = Mock()
    mock_response.iter_content = Mock(return_value=[b"chunk1", b"chunk2"])
    mock_response.status_code = 200
    mock_requests_get.return_value = mock_response

    stream_reader = SourceMicrosoftSharePointStreamReader()
    stream_reader._config = Mock()  # Assuming _config is required

    file_record_data, file_reference = stream_reader.upload(mock_file, TEST_LOCAL_DIRECTORY, mock_logger)

    expected_file_bytes = expected_paths["bytes"]
    expected_source_file_relative_path = expected_paths["source_file_relative_path"]
    expected_staging_file_url = expected_paths["staging_file_url"]

    assert file_reference.source_file_relative_path == expected_source_file_relative_path
    assert file_reference.staging_file_url == expected_staging_file_url
    assert file_reference.file_size_bytes == expected_file_bytes

    assert os.path.basename(expected_staging_file_url) == file_record_data.file_name
    assert os.path.dirname(expected_staging_file_url.replace(f"{TEST_LOCAL_DIRECTORY}", "")) == file_record_data.folder
    assert file_record_data.source_uri == file_uri

    # Check if the file exists at the file_url path
    assert os.path.exists(file_reference.staging_file_url)


@patch("source_microsoft_sharepoint.stream_reader.SourceMicrosoftSharePointStreamReader.get_access_token")
@patch("source_microsoft_sharepoint.stream_reader.requests.head")
def test_get_file_size_error_fetching_metadata_for_missing_header(mock_requests_head, mock_get_access_token):
    file_uri = f"https://my_favorite_sharepoint.sharepoint.com/Shared%20Documents/file.txt"
    mock_file = Mock(download_url=f"https://example.com/file.txt", uri=file_uri)
    mock_logger = Mock()
    mock_get_access_token.return_value = "dummy_access_token"

    # Create a mock response for requests.head
    mock_head_response = Mock()
    mock_head_response.status_code = 200
    mock_head_response.headers = {"Other-header": "12345"}
    mock_requests_head.return_value = mock_head_response

    stream_reader = SourceMicrosoftSharePointStreamReader()
    stream_reader._config = Mock()  # Assuming _config is required
    with pytest.raises(ErrorFetchingMetadata, match="Size was expected in metadata response but was missing"):
        stream_reader.upload(mock_file, TEST_LOCAL_DIRECTORY, mock_logger)


@patch("source_microsoft_sharepoint.stream_reader.SourceMicrosoftSharePointStreamReader.get_access_token")
@patch("source_microsoft_sharepoint.stream_reader.requests.head")
def test_get_file_size_error_fetching_metadata(mock_requests_head, mock_get_access_token):
    """
    Test that the get_file method raises an ErrorFetchingMetadata exception when the requests.head call fails.
    """
    file_uri = f"https://my_favorite_sharepoint.sharepoint.com/Shared%20Documents/file.txt"
    mock_file = Mock(download_url=f"https://example.com/file.txt", uri=file_uri)
    mock_logger = Mock()
    mock_get_access_token.return_value = "dummy_access_token"

    # Create a mock response for requests.head
    mock_head_response = Mock()
    mock_head_response.status_code = 500
    mock_head_response.headers = {"Content-Length": "12345"}
    mock_head_response.raise_for_status.side_effect = HTTPError("500 Server Error")
    mock_requests_head.return_value = mock_head_response

    stream_reader = SourceMicrosoftSharePointStreamReader()
    stream_reader._config = Mock()  # Assuming _config is required

    with pytest.raises(ErrorFetchingMetadata, match="An error occurred while retrieving file size: 500 Server Error"):
        stream_reader.upload(mock_file, TEST_LOCAL_DIRECTORY, mock_logger)


def test_microsoft_sharepoint_client_initialization(requests_mock):
    """Test the initialization of SourceMicrosoftSharePointClient."""
    config = {
        "credentials": {
            "auth_type": "Client",
            "client_id": "client_id",
            "tenant_id": "tenant_id",
            "client_secret": "client_secret",
            "refresh_token": "refresh_token",
        },
        "drive_name": "drive_name",
        "folder_path": "folder_path",
        "streams": [{"name": "test_stream", "globs": ["*.csv"], "validation_policy": "Emit Record", "format": {"filetype": "csv"}}],
    }

    authority_url = "https://login.microsoftonline.com/tenant_id/v2.0/.well-known/openid-configuration"
    mock_response = {
        "authorization_endpoint": "https://login.microsoftonline.com/tenant_id/oauth2/v2.0/authorize",
        "token_endpoint": "https://login.microsoftonline.com/tenant_id/oauth2/v2.0/token",
    }
    requests_mock.get(authority_url, json=mock_response, status_code=200)

    client = SourceMicrosoftSharePointClient(SourceMicrosoftSharePointSpec(**config))

    assert client.config == SourceMicrosoftSharePointSpec(**config)
    assert client._msal_app is not None


def test_list_directories_and_files():
    """Test the list_directories_and_files method in SourceMicrosoftSharePointStreamReader."""
    # Create a mock structure of folders and files
    mock_child_file1 = create_mock_drive_item(True, "file1.txt")
    mock_child_file2 = create_mock_drive_item(True, "file2.txt")
    mock_child_folder = create_mock_drive_item(False, "folder1", children=[mock_child_file1])
    mock_root_folder = create_mock_drive_item(False, "root", children=[mock_child_folder, mock_child_file2])

    stream_reader = SourceMicrosoftSharePointStreamReader()

    result = list(stream_reader._list_directories_and_files(mock_root_folder, "https://example.com/root"))

    assert len(result) == 2
    assert result == [
        MicrosoftSharePointRemoteFile(
            uri="https://example.com/root/folder1/file1.txt",
            last_modified=datetime(1991, 8, 24, 0, 0),
            mime_type=None,
            download_url="test_url",
            created_at=datetime(1991, 8, 24, 0, 0),
        ),
        MicrosoftSharePointRemoteFile(
            uri="https://example.com/root/file2.txt",
            last_modified=datetime(1991, 8, 24, 0, 0),
            mime_type=None,
            download_url="test_url",
            created_at=datetime(1991, 8, 24, 0, 0),
        ),
    ]


@pytest.mark.parametrize(
    "drive_type, files_number",
    [
        ("documentLibrary", 1),
        ("business", 0),
    ],
)
@patch("source_microsoft_sharepoint.stream_reader.SourceMicrosoftSharePointStreamReader._list_directories_and_files")
def test_get_files_by_drive_name(mock_list_directories_and_files, drive_type, files_number):
    # Helper function usage
    mock_drive = Mock()
    mock_drive.name = "testDrive"
    mock_drive.web_url = "https://example.com/testDrive"
    mock_drive.drive_type = drive_type
    mock_drive.root.get_by_path.return_value.get().execute_query_with_incremental_retry.return_value = create_mock_drive_item(
        is_file=False, name="root"
    )

    # Mock files
    mock_file = create_mock_drive_item(is_file=True, name="testFile.txt")
    mock_list_directories_and_files.return_value = [mock_file]

    # Create stream reader instance
    stream_reader = SourceMicrosoftSharePointStreamReader()
    stream_reader._config = Mock()

    # Call the method
    files = list(stream_reader._get_files_by_drive_name([mock_drive], "/test/path"))

    # Assertions
    assert len(files) == files_number
    if files_number:
        assert files[0].name == "testFile.txt"


@pytest.mark.parametrize(
    "drive_ids, shared_drive_item_dicts, expected_result, expected_calls",
    [
        ([1, 2, 3], [], [], []),
        ([1, 2, 3], [{"drive_id": 1, "id": 4, "web_url": "test_url4"}], [], []),
        ([1, 2, 3], [{"drive_id": 4, "id": 4, "web_url": "test_url4"}], [4], [call(4, 4, "test_url4")]),
        (
            [1, 2, 3],
            [{"drive_id": 4, "id": 4, "web_url": "test_url4"}, {"drive_id": 5, "id": 5, "web_url": "test_url5"}],
            [4, 5],
            [call(4, 4, "test_url4"), call(5, 5, "test_url5")],
        ),
        (
            [1, 2, 3],
            [
                {"drive_id": 4, "id": 4, "web_url": "test_url4"},
                {"drive_id": 5, "id": 5, "web_url": "test_url5"},
                {"drive_id": 6, "id": 6, "web_url": "test_url6"},
            ],
            [4, 5, 6],
            [call(4, 4, "test_url4"), call(5, 5, "test_url5"), call(6, 6, "test_url6")],
        ),
    ],
)
@patch("source_microsoft_sharepoint.stream_reader.execute_query_with_retry", side_effect=lambda x: x)
def test_get_shared_files_from_all_drives(
    mock_execute_query_with_retry, drive_ids, shared_drive_item_dicts, expected_result, expected_calls
):
    stream_reader = SourceMicrosoftSharePointStreamReader()
    stream_reader._config = Mock()

    # Mock _get_shared_drive_object method
    with patch.object(
        SourceMicrosoftSharePointStreamReader, "_get_shared_drive_object", return_value=expected_result
    ) as mock_get_shared_drive_object:
        # Setup shared_drive_items mock objects
        shared_drive_items = [
            MagicMock(remote_item=MagicMock(parentReference={"driveId": item["drive_id"]}), id=item["id"], web_url=item["web_url"])
            for item in shared_drive_item_dicts
        ]

        with patch.object(SourceMicrosoftSharePointStreamReader, "one_drive_client", new_callable=PropertyMock) as mock_one_drive_client:
            mock_one_drive_client.return_value.me.drive.shared_with_me.return_value = shared_drive_items

            mock_drives = [Mock(id=drive_id) for drive_id in drive_ids]

            # Execute the method under test
            list(stream_reader._get_shared_files_from_all_drives(mock_drives))

            # Assert _get_shared_drive_object was called correctly
            mock_get_shared_drive_object.assert_has_calls(expected_calls, any_order=True)


# Sample data for mocking responses
file_response = {
    "file": True,
    "name": "TestFile.txt",
    "@microsoft.graph.downloadUrl": "http://example.com/download",
    "lastModifiedDateTime": "2021-01-01T00:00:00Z",
    "createdDateTime": "2021-01-01T00:00:00Z",
}

empty_folder_response = {"folder": True, "value": []}

# Adjusting the folder_with_nested_files to represent the initial folder response
folder_with_nested_files_initial = {
    "folder": True,
    "value": [
        {"id": "subfolder1", "folder": True, "name": "subfolder1"},
        {"id": "subfolder2", "folder": True, "name": "subfolder2"},
    ],  # Empty subfolder  # Subfolder with a file
}

# Response for the empty subfolder (subfolder1)
empty_subfolder_response = {"value": [], "name": "subfolder1"}  # No files or folders inside subfolder1

# Response for the subfolder with a file (subfolder2)
not_empty_subfolder_response = {
    "value": [
        {
            "file": True,
            "name": "NestedFile.txt",
            "@microsoft.graph.downloadUrl": "http://example.com/nested",
            "lastModifiedDateTime": "2021-01-02T00:00:00Z",
            "createdDateTime": "2021-01-02T00:00:00Z",
        }
    ],
    "name": "subfolder2",
}


@pytest.mark.parametrize(
    "initial_response, subsequent_responses, expected_result, raises_error, expected_error_message, initial_path",
    [
        # Object ID is a file
        (
            file_response,
            [],
            [
                MicrosoftSharePointRemoteFile(
                    uri="http://example.com/TestFile.txt",
                    last_modified=datetime(2021, 1, 1, 0, 0),
                    mime_type=None,
                    download_url="http://example.com/download",
                    created_at=datetime(2021, 1, 1, 0, 0),
                ),
            ],
            False,
            None,
            "http://example.com",
        ),
        # Object ID is an empty folder
        (empty_folder_response, [empty_subfolder_response], [], False, None, "http://example.com"),
        # Object ID is a folder with empty subfolders and files
        (
            {"folder": True, "name": "root"},  # Initial folder response
            [
                folder_with_nested_files_initial,
                empty_subfolder_response,
                not_empty_subfolder_response,
            ],
            [
                MicrosoftSharePointRemoteFile(
                    uri="http://example.com/subfolder2/NestedFile.txt",
                    last_modified=datetime(2021, 1, 2, 0, 0),
                    mime_type=None,
                    download_url="http://example.com/nested",
                    created_at=datetime(2021, 1, 2, 0, 0),
                )
            ],
            False,
            None,
            "http://example.com",
        ),
        # Error response on initial request
        (
            MagicMock(status_code=400, json=MagicMock(return_value={"error": {"message": "Bad Request"}})),
            [],
            [],
            True,
            "Failed to retrieve the initial shared object with ID 'dummy_object_id' from drive 'dummy_drive_id'. HTTP status: 400. Error: Bad Request",
            "http://example.com",
        ),
        # Error response while iterating over nested
        (
            {"folder": True, "name": "root"},
            [MagicMock(status_code=400, json=MagicMock(return_value={"error": {"message": "Bad Request"}}))],
            [],
            True,
            (
                "Failed to retrieve files from URL "
                "'https://graph.microsoft.com/v1.0/drives/dummy_drive_id/items/dummy_object_id/children'. "
                "HTTP status: 400. Error: Bad Request"
            ),
            "http://example.com",
        ),
    ],
)
@patch("source_microsoft_sharepoint.stream_reader.requests.get")
@patch("source_microsoft_sharepoint.stream_reader.SourceMicrosoftSharePointStreamReader.get_access_token")
def test_get_shared_drive_object(
    mock_get_access_token,
    mock_requests_get,
    initial_response,
    subsequent_responses,
    expected_result,
    raises_error,
    expected_error_message,
    initial_path,
):
    mock_get_access_token.return_value = "dummy_access_token"
    mock_responses = [
        initial_response
        if isinstance(initial_response, MagicMock)
        else MagicMock(status_code=200, json=MagicMock(return_value=initial_response))
    ]
    for response in subsequent_responses:
        mock_responses.append(
            response if isinstance(response, MagicMock) else MagicMock(status_code=200, json=MagicMock(return_value=response))
        )
    mock_requests_get.side_effect = mock_responses

    reader = SourceMicrosoftSharePointStreamReader()

    if raises_error:
        with pytest.raises(RuntimeError) as exc_info:
            list(reader._get_shared_drive_object("dummy_drive_id", "dummy_object_id", initial_path))
        assert str(exc_info.value) == expected_error_message
    else:
        result = list(reader._get_shared_drive_object("dummy_drive_id", "dummy_object_id", initial_path))
        assert result == expected_result


@pytest.mark.parametrize(
    "auth_type, user_principal_name, has_refresh_token",
    [
        ("Client", None, True),
        ("Client", None, False),
        ("User", "user@example.com", False),
    ],
)
def test_drives_property(auth_type, user_principal_name, has_refresh_token):
    with (
        patch("source_microsoft_sharepoint.stream_reader.execute_query_with_retry") as mock_execute_query,
        patch("source_microsoft_sharepoint.stream_reader.SourceMicrosoftSharePointStreamReader.one_drive_client") as mock_one_drive_client,
    ):
        refresh_token = "dummy_refresh_token" if has_refresh_token else None
        # Setup for different authentication types
        config_mock = MagicMock(
            credentials=MagicMock(auth_type=auth_type, user_principal_name=user_principal_name, refresh_token=refresh_token), site_url=""
        )

        # Mock responses for the drives list and a single drive (my_drive)
        drives_response = MagicMock()
        my_drive = MagicMock()
        drives_response.add_child = MagicMock()

        # Set up mock responses for the two different calls within the property based on auth_type
        if auth_type == "Client":
            mock_execute_query.side_effect = [drives_response, my_drive] if has_refresh_token else [drives_response]
        else:
            # For User auth_type, assume a call to get user's principal name drive
            mock_execute_query.side_effect = [drives_response, my_drive]

        # Create an instance of the reader and set its config mock
        reader = SourceMicrosoftSharePointStreamReader()
        reader._config = config_mock

        # Access the drives property to trigger the retrieval and caching logic
        drives = reader.drives

        # Assertions
        assert drives is not None
        # mock_execute_query.assert_called()
        if auth_type == "Client" and not has_refresh_token:
            assert mock_execute_query.call_count == 1
            drives_response.add_child.assert_not_called()
        else:
            assert mock_execute_query.call_count == 2
            drives_response.add_child.assert_called_once_with(my_drive)


def test_get_site_drive_default_site():
    """
    Test retrieving drives from the default site (no site URL in config)
    """
    reader = SourceMicrosoftSharePointStreamReader()
    reader._config = MagicMock(site_url="")
    mock_one_drive_client = MagicMock()
    reader._one_drive_client = mock_one_drive_client

    mock_drive = MagicMock()
    mock_drives = MagicMock(spec=EntityCollection)
    mock_drives.__iter__.return_value = [mock_drive]

    with patch("source_microsoft_sharepoint.stream_reader.execute_query_with_retry") as mock_execute_query:
        mock_execute_query.return_value = mock_drives

        result = reader._get_site_drive()

        mock_one_drive_client.drives.get.assert_called_once()
        assert result == mock_drives
        assert len(list(result)) == 1


def test_get_site_drive_all_sites():
    """
    Test retrieving drives from all sites (site URL in config ending with 'sharepoint.com/sites/')
    """
    reader = SourceMicrosoftSharePointStreamReader()
    reader._config = MagicMock(site_url="https://test-tenant.sharepoint.com/sites/")
    reader._one_drive_client = MagicMock()

    first_drive_name = "Drive1"
    first_drive_url = "https://test-tenant.sharepoint.com/sites/site1/drive1"
    second_drive_name = "Drive2"
    second_drive_url = "https://test-tenant.sharepoint.com/sites/site2/drive2"
    first_site_url = "https://test-tenant.sharepoint.com/sites/site1"
    second_site_url = "https://test-tenant.sharepoint.com/sites/site2"

    mock_drive1 = MagicMock(spec=Drive)
    mock_drive1.name = first_drive_name
    mock_drive1.web_url = first_drive_url

    mock_drive2 = MagicMock(spec=Drive)
    mock_drive2.name = second_drive_name
    mock_drive2.web_url = second_drive_url

    mock_drives = MagicMock(spec=EntityCollection)
    mock_drives.__iter__.return_value = [mock_drive1, mock_drive2]

    mock_sites = [
        {"Title": "Site1", "Path": first_site_url},
        {"Title": "Site2", "Path": second_site_url},
    ]

    with (
        patch.object(reader, "get_all_sites", return_value=mock_sites) as mock_get_all_sites,
        patch.object(reader, "_get_drives_from_sites", return_value=mock_drives) as mock_get_drives_from_sites,
    ):
        result = reader._get_site_drive()

        mock_get_all_sites.assert_called_once()
        mock_get_drives_from_sites.assert_called_once_with(mock_sites)
        assert result == mock_drives

        drives = list(result)
        assert len(drives) == 2
        assert drives[0].name == first_drive_name
        assert drives[0].web_url == first_drive_url
        assert drives[1].name == second_drive_name
        assert drives[1].web_url == second_drive_url


def test_get_site_drive_specific_site():
    """
    Test retrieving drives from a specific site in config (site URL in config ending with 'sharepoint.com/sites/specific')
    """
    site_url = "https://test-tenant.sharepoint.com/sites/specific"
    drive_name = "Test Drive"
    drive_url = f"{site_url}/TestDrive"

    reader = SourceMicrosoftSharePointStreamReader()
    reader._config = MagicMock(site_url=site_url)
    mock_one_drive_client = MagicMock()
    reader._one_drive_client = mock_one_drive_client

    mock_drive = MagicMock(spec=Drive)
    mock_drive.name = drive_name
    mock_drive.web_url = drive_url
    mock_drives = [mock_drive]

    with patch("source_microsoft_sharepoint.stream_reader.execute_query_with_retry") as mock_execute_query:
        mock_execute_query.return_value = mock_drives

        result = reader._get_site_drive()

        mock_one_drive_client.sites.get_by_url.assert_called_once_with(site_url)
        assert result == mock_drives
        assert len(result) == 1
        assert result[0].name == drive_name
        assert result[0].web_url == drive_url


def test_get_site_drive_error_handling():
    """Test error handling when retrieving drives fails"""
    reader = SourceMicrosoftSharePointStreamReader()
    reader._config = MagicMock(site_url="https://test-tenant.sharepoint.com/sites/specific")
    mock_one_drive_client = MagicMock()
    reader._one_drive_client = mock_one_drive_client

    with patch("source_microsoft_sharepoint.stream_reader.execute_query_with_retry") as mock_execute_query:
        mock_execute_query.side_effect = Exception("Test exception")

        with pytest.raises(AirbyteTracedException) as exc_info:
            reader._get_site_drive()

        assert "Failed to retrieve drives from sharepoint" in str(exc_info.value)


@pytest.mark.parametrize(
    "refresh_token, auth_type, search_scope, expected_methods_called",
    [
        (None, "Client", "ACCESSIBLE_DRIVES", ["_get_files_by_drive_name"]),
        (None, "Client", "ALL", ["_get_files_by_drive_name"]),
        ("dummy_refresh_token", "Client", "ACCESSIBLE_DRIVES", ["_get_files_by_drive_name"]),
        ("dummy_refresh_token", "Client", "ALL", ["_get_files_by_drive_name", "_get_shared_files_from_all_drives"]),
        (None, "User", "ACCESSIBLE_DRIVES", ["_get_files_by_drive_name"]),
        (None, "User", "ALL", ["_get_files_by_drive_name", "_get_shared_files_from_all_drives"]),
        (None, "Client", "SHARED_ITEMS", []),
        ("dummy_refresh_token", "Client", "SHARED_ITEMS", ["_get_shared_files_from_all_drives"]),
    ],
)
def test_retrieve_files_from_accessible_drives(mocker, refresh_token, auth_type, search_scope, expected_methods_called):
    reader = SourceMicrosoftSharePointStreamReader()
    config = MagicMock(credentials=MagicMock(auth_type=auth_type, refresh_token=refresh_token), search_scope=search_scope)

    reader._config = config

    with patch.object(SourceMicrosoftSharePointStreamReader, "drives", return_value=[]) as mock_drives:
        mocker.patch.object(reader, "_get_files_by_drive_name")
        mocker.patch.object(reader, "_get_shared_files_from_all_drives")

        files = list(reader.get_all_files())

        assert reader._get_files_by_drive_name.called == ("_get_files_by_drive_name" in expected_methods_called)
        assert reader._get_shared_files_from_all_drives.called == ("_get_shared_files_from_all_drives" in expected_methods_called)


def test_get_all_sites_returns_sites_successfully():
    """
    Test that get_all_sites correctly returns site information when sites are found
    """

    reader = SourceMicrosoftSharePointStreamReader()
    reader._config = MagicMock(spec=SourceMicrosoftSharePointSpec)
    reader._one_drive_client = MagicMock(spec=GraphClient)

    tenant_url = "https://test-tenant.sharepoint.com"
    site_first_title = "Site1"
    site_first_path = f"{tenant_url}/sites/site1"
    site_second_title = "Site2"
    site_second_path = f"{tenant_url}/sites/site2"
    query_filter = "contentclass:STS_Site NOT Path:https://test-tenant-my.sharepoint.com"

    with (
        patch("source_microsoft_sharepoint.stream_reader.get_site_prefix") as mock_get_site_prefix,
        patch.object(reader, "_get_client_context") as mock_get_client_context,
        patch("source_microsoft_sharepoint.stream_reader.SearchService") as mock_search_service,
        patch("source_microsoft_sharepoint.stream_reader.execute_query_with_retry") as mock_execute_query,
    ):
        mock_get_site_prefix.return_value = (tenant_url, "test-tenant")
        mock_client_context = MagicMock(spec=ClientContext)
        mock_get_client_context.return_value = mock_client_context

        mock_search_service_instance = MagicMock(spec=SearchService)
        mock_search_service.return_value = mock_search_service_instance

        mock_search_job = MagicMock()
        mock_search_service_instance.post_query.return_value = mock_search_job

        search_job_result = MagicMock()
        mock_execute_query.return_value = search_job_result
        mock_search_job.value = True

        primary_query_result = MagicMock()
        search_job_result.value.PrimaryQueryResult = primary_query_result
        relevant_results = MagicMock()
        primary_query_result.RelevantResults = relevant_results
        table = MagicMock()
        relevant_results.Table = table

        mock_row_first = MagicMock()
        mock_row_first.Cells.get.side_effect = lambda key, default=None: {
            "Title": site_first_title,
            "Path": site_first_path,
        }.get(key, default)

        mock_row_second = MagicMock()
        mock_row_second.Cells.get.side_effect = lambda key, default=None: {
            "Title": site_second_title,
            "Path": site_second_path,
        }.get(key, default)

        table.Rows = [mock_row_first, mock_row_second]

        result = reader.get_all_sites()

        expected_sites = [
            {"Title": site_first_title, "Path": site_first_path},
            {"Title": site_second_title, "Path": site_second_path},
        ]
        assert result == expected_sites

        mock_get_site_prefix.assert_called_once_with(reader.one_drive_client)
        mock_get_client_context.assert_called_once()
        mock_search_service.assert_called_once_with(mock_client_context)
        mock_search_service_instance.post_query.assert_called_once_with(query_filter)
        mock_execute_query.assert_called_once_with(mock_search_job)


@pytest.mark.parametrize(
    "test_case, search_job_value, primary_query_result",
    [
        ("empty_search_results", None, None),  # Case: search_job.value is None
        ("no_relevant_results", True, None),  # Case: search_job.value exists but PrimaryQueryResult is None
    ],
)
def test_get_all_sites_with_no_results(test_case, search_job_value, primary_query_result):
    """
    Test that get_all_sites raises an exception when search returns no results
    """
    reader = SourceMicrosoftSharePointStreamReader()
    reader._config = MagicMock()
    reader._one_drive_client = MagicMock()

    with (
        patch("source_microsoft_sharepoint.stream_reader.get_site_prefix") as mock_get_site_prefix,
        patch.object(reader, "_get_client_context") as mock_get_client_context,
        patch("source_microsoft_sharepoint.stream_reader.SearchService") as mock_search_service,
        patch("source_microsoft_sharepoint.stream_reader.execute_query_with_retry") as mock_execute_query,
    ):
        mock_get_site_prefix.return_value = ("https://test-tenant.sharepoint.com", "test-tenant")
        mock_client_context = MagicMock()
        mock_get_client_context.return_value = mock_client_context
        mock_search_service_instance = MagicMock()
        mock_search_service.return_value = mock_search_service_instance
        mock_search_job = MagicMock()
        mock_search_service_instance.post_query.return_value = mock_search_job

        search_job_result = MagicMock()
        mock_execute_query.return_value = search_job_result
        mock_search_job.value = search_job_value

        if search_job_value:
            search_job_result.value.PrimaryQueryResult = primary_query_result

        with pytest.raises(Exception, match="No site collections found"):
            reader.get_all_sites()

        mock_get_site_prefix.assert_called_once_with(reader.one_drive_client)
        mock_get_client_context.assert_called_once()
        mock_search_service.assert_called_once_with(mock_client_context)
        mock_search_service_instance.post_query.assert_called_once_with(
            "contentclass:STS_Site NOT Path:https://test-tenant-my.sharepoint.com"
        )
        mock_execute_query.assert_called_once_with(mock_search_job)

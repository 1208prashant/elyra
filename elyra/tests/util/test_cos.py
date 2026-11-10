#
# Copyright 2018-2025 Elyra Authors
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
#

from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlparse

from minio.error import S3Error
import pytest

from elyra.util.cos import CosClient
from elyra.util.cos import join_paths
from elyra.util.cos import region_for_minio_client


def test_join_paths():
    """
    Verify that join_path yields the expected results
    """

    # one or more paths is None
    assert join_paths(None, None) == ""
    assert join_paths("", None) == ""
    assert join_paths(None, "") == ""
    assert join_paths("path", None) == "path"
    assert join_paths("/path", None) == "path"
    assert join_paths("path/", None) == "path"
    assert join_paths("/path/", None) == "path"

    # one or more paths is the empty string
    assert join_paths("", "") == ""
    assert join_paths("path", "") == "path"
    assert join_paths("/path", "") == "path"
    assert join_paths("path/", "") == "path"
    assert join_paths("/path/", "") == "path"
    assert join_paths("", "path") == "path"
    assert join_paths("", "/path") == "path"
    assert join_paths("", "path/") == "path"
    assert join_paths("", "/path/") == "path"

    assert join_paths("path", "path2") == "path/path2"
    assert join_paths("/path", "path2") == "path/path2"
    assert join_paths("path/", "path2") == "path/path2"
    assert join_paths("/path/", "path2") == "path/path2"
    assert join_paths("path", "/path2") == "path/path2"
    assert join_paths("path", "path2/") == "path/path2"
    assert join_paths("path", "/path2/") == "path/path2"
    assert join_paths("/path", "/path2") == "path/path2"
    assert join_paths("/path", "path2/") == "path/path2"
    assert join_paths("/path", "/path2/") == "path/path2"
    assert join_paths("/path/", "/path2") == "path/path2"
    assert join_paths("/path/", "path2/") == "path/path2"
    assert join_paths("/path/", "/path2/") == "path/path2"

    # multiple //
    assert join_paths("//path", "path2") == "path/path2"
    assert join_paths("path//", "path2") == "path/path2"
    assert join_paths("//path//", "path2") == "path/path2"
    assert join_paths("path", "//path2") == "path/path2"
    assert join_paths("path", "path2//") == "path/path2"
    assert join_paths("path", "//path2//") == "path/path2"
    assert join_paths("//path", "//path2") == "path/path2"
    assert join_paths("//path", "path2//") == "path/path2"
    assert join_paths("//path", "//path2//") == "path/path2"
    assert join_paths("//path//", "//path2") == "path/path2"
    assert join_paths("//path//", "path2//") == "path/path2"
    assert join_paths("//path//", "//path2//") == "path/path2"


def _runtime_config(endpoint, bucket="shared-bucket", region=None):
    metadata = {
        "cos_auth_type": "USER_CREDENTIALS",
        "cos_endpoint": endpoint,
        "cos_bucket": bucket,
        "cos_username": "access-key",
        "cos_password": "secret-key",
    }
    if region is not None:
        metadata["cos_region"] = region
    return SimpleNamespace(metadata=metadata)


def _s3_error(code):
    return S3Error(
        response=object(),
        code=code,
        message="Access Denied" if code == "AccessDenied" else "The specified bucket does not exist",
        resource="",
        request_id="request-id",
        host_id="",
        bucket_name="shared-bucket",
    )


def _install_minio(monkeypatch, client):
    constructed = {}

    def _minio(*args, **kwargs):
        constructed["args"] = args
        constructed["kwargs"] = kwargs
        return client

    monkeypatch.setattr("elyra.util.cos.minio.Minio", _minio)
    return constructed


def test_configured_region_is_supplied_to_client(monkeypatch):
    client = MagicMock()
    client.bucket_exists.return_value = True
    constructed = _install_minio(monkeypatch, client)

    CosClient(config=_runtime_config("https://s3.example.test", region="eu-central-1"))

    assert constructed["kwargs"]["region"] == "eu-central-1"
    assert region_for_minio_client(urlparse("https://s3.eu-west-1.amazonaws.com"), "eu-west-1") == "eu-west-1"


def test_non_aws_endpoint_supplies_default_region(monkeypatch):
    client = MagicMock()
    client.bucket_exists.return_value = True
    constructed = _install_minio(monkeypatch, client)

    CosClient(config=_runtime_config("https://s3.example.test:9000"))

    assert constructed["kwargs"]["region"] == "us-east-1"
    client.bucket_exists.assert_called_once_with(bucket_name="shared-bucket")


def test_aws_endpoint_does_not_replace_region(monkeypatch):
    client = MagicMock()
    client.bucket_exists.return_value = True
    constructed = _install_minio(monkeypatch, client)

    CosClient(config=_runtime_config("https://s3.eu-west-1.amazonaws.com"))

    assert constructed["kwargs"]["region"] is None


def test_access_denied_bucket_check_does_not_fail_init(monkeypatch, caplog):
    client = MagicMock()
    client.bucket_exists.side_effect = _s3_error("AccessDenied")
    _install_minio(monkeypatch, client)

    with caplog.at_level("WARNING"):
        cos_client = CosClient(config=_runtime_config("https://s3.example.test"))

    assert cos_client.bucket == "shared-bucket"
    client.make_bucket.assert_not_called()
    assert "Skipping object storage bucket check" in caplog.text
    assert "secret-key" not in caplog.text


def test_missing_bucket_still_fails(monkeypatch):
    client = MagicMock()
    client.bucket_exists.return_value = False
    client.make_bucket.side_effect = _s3_error("NoSuchBucket")
    _install_minio(monkeypatch, client)

    with pytest.raises(S3Error) as exc_info:
        CosClient(config=_runtime_config("https://s3.example.test"))

    assert exc_info.value.code == "NoSuchBucket"
    client.make_bucket.assert_called_once_with(bucket_name="shared-bucket")


def _assert_bootstrapper_region(monkeypatch, file_op_base, endpoint, expected_region):
    constructed = {}

    def _minio(*args, **kwargs):
        constructed["args"] = args
        constructed["kwargs"] = kwargs
        return MagicMock()

    monkeypatch.setattr("minio.Minio", _minio)

    class _Op(file_op_base):
        def execute(self):
            return None

    _Op(
        filepath="node.py",
        **{
            "cos-endpoint": endpoint,
            "cos-bucket": "shared-bucket",
            "cos-user": "access-key",
            "cos-password": "secret-key",
        },
    )
    assert constructed["kwargs"]["region"] == expected_region


def test_kfp_bootstrapper_supplies_region_for_later_calls(monkeypatch):
    from elyra.kfp.bootstrapper import FileOpBase

    _assert_bootstrapper_region(monkeypatch, FileOpBase, "https://s3.example.test", "us-east-1")


def test_kfp_bootstrapper_leaves_aws_region_unset(monkeypatch):
    from elyra.kfp.bootstrapper import FileOpBase

    _assert_bootstrapper_region(monkeypatch, FileOpBase, "https://s3.eu-west-1.amazonaws.com", None)

"""Unit tests for the dashboard install flow."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from oracle_ai_data_platform_fusion_bundle.oac.install import InstallParams, install


def _params(tmp_path: Path, **overrides) -> InstallParams:
    pem = tmp_path / "key.pem"; pem.write_text("pem")
    defaults = {
        "oac_url": "https://oac.example.com",
        "connection_name": "aidp_fusion_jdbc",
        "region": "us-ashburn-1",
        "user_ocid": "ocid1.user.oc1..u",
        "tenancy_ocid": "ocid1.tenancy.oc1..t",
        "fingerprint": "fp",
        "idl_ocid": "ocid1.aidataplatform.oc1.iad..d",
        "cluster_key": "ck",
        "catalog": "default",
        "idcs_url": None,
        "client_id": None,
        "client_secret": None,
        "oauth_scope": "urn:opc:resource:fawcommon:OAC",
        "private_key_pem_path": pem,
        "workbooks_dir": tmp_path / "workbooks",
        "print_only": False,
        "skip_workbooks": False,
    }
    defaults.update(overrides)
    return InstallParams(**defaults)


class TestPrintOnly:
    def test_writes_json_template(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = install(_params(tmp_path, print_only=True))
        assert result.json_template_path is not None
        assert result.json_template_path.exists()
        loaded = json.loads(result.json_template_path.read_text(encoding="utf-8"))
        assert loaded["username"] == "ocid1.user.oc1..u"
        assert loaded["idl-ocid"] == "ocid1.aidataplatform.oc1.iad..d"
        assert loaded["dsn"].startswith("jdbc:spark://gateway.aidp.us-ashburn-1.oci.oraclecloud.com")

    def test_no_rest_calls(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with patch(
            "oracle_ai_data_platform_fusion_bundle.oac.install.IdcsTokenFetcher"
        ) as fetcher_cls:
            install(_params(tmp_path, print_only=True))
            fetcher_cls.assert_not_called()


class TestRestInstall:
    def test_requires_idcs_creds(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="--idcs-url"):
            install(_params(tmp_path))  # no idcs_url / client_id / secret

    def test_creates_connection_when_absent(self, tmp_path: Path) -> None:
        wb_dir = tmp_path / "workbooks"; wb_dir.mkdir()
        # Create a fake .dva so import logic exercises one file
        (wb_dir / "supplier_spend.dva").write_bytes(b"PK\x03\x04fake")

        with patch(
            "oracle_ai_data_platform_fusion_bundle.oac.install.IdcsTokenFetcher"
        ) as fetcher_cls, patch(
            "oracle_ai_data_platform_fusion_bundle.oac.install.OacRestClient"
        ) as client_cls:
            client_inst = client_cls.return_value
            client_inst.find_connection.return_value = None
            client_inst.create_connection.return_value = {"id": "conn-42"}
            client_inst.import_workbook.return_value = {"id": "wb-7"}

            params = _params(
                tmp_path,
                workbooks_dir=wb_dir,
                idcs_url="https://idcs-x.identity.oraclecloud.com",
                client_id="cid",
                client_secret="csec",
            )
            result = install(params)

        fetcher_cls.assert_called_once()
        client_cls.assert_called_once()
        client_inst.create_connection.assert_called_once()
        client_inst.import_workbook.assert_called_once()
        assert result.connection_id == "conn-42"
        assert result.imported_workbooks == ["supplier_spend.dva"]

    def test_skips_connection_when_already_exists(self, tmp_path: Path) -> None:
        with patch(
            "oracle_ai_data_platform_fusion_bundle.oac.install.IdcsTokenFetcher"
        ), patch(
            "oracle_ai_data_platform_fusion_bundle.oac.install.OacRestClient"
        ) as client_cls:
            client_inst = client_cls.return_value
            client_inst.find_connection.return_value = {"id": "existing-1"}

            params = _params(
                tmp_path,
                idcs_url="https://idcs-x.identity.oraclecloud.com",
                client_id="cid",
                client_secret="csec",
                skip_workbooks=True,
            )
            result = install(params)

        client_inst.create_connection.assert_not_called()
        assert result.connection_id == "existing-1"

    def test_skip_workbooks_flag(self, tmp_path: Path) -> None:
        wb_dir = tmp_path / "workbooks"; wb_dir.mkdir()
        (wb_dir / "supplier_spend.dva").write_bytes(b"x")
        with patch(
            "oracle_ai_data_platform_fusion_bundle.oac.install.IdcsTokenFetcher"
        ), patch(
            "oracle_ai_data_platform_fusion_bundle.oac.install.OacRestClient"
        ) as client_cls:
            client_inst = client_cls.return_value
            client_inst.find_connection.return_value = None
            client_inst.create_connection.return_value = {"id": "c"}
            params = _params(
                tmp_path,
                workbooks_dir=wb_dir,
                idcs_url="https://idcs-x.identity.oraclecloud.com",
                client_id="cid",
                client_secret="csec",
                skip_workbooks=True,
            )
            install(params)
        client_inst.import_workbook.assert_not_called()

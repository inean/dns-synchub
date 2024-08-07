import logging
from unittest.mock import MagicMock, patch

import CloudFlare
import pytest
from cloudflare_companion import (
    CloudFlareZones,
    DomainsModel,
    Settings,
    Singleton,
)


@pytest.fixture
def mock_logger():
    logger = logging.getLogger("cloudflare_companion")
    return logger


@pytest.fixture
def mock_cloudflare():
    cf = MagicMock()
    cf.zones.dns_records.get = MagicMock()
    cf.zones.dns_records.put = MagicMock()
    cf.zones.dns_records.post = MagicMock()
    return cf


@pytest.fixture
def mock_settings():
    settings = MagicMock(spec=Settings)
    settings.rc_type = "CNAME"
    settings.refresh_entries = True
    settings.dry_run = False
    return settings


@pytest.fixture
def mock_domain_infos():
    domain_info = MagicMock(spec=DomainsModel)
    domain_info.name = "example.com"
    domain_info.target_domain = "target.example.com"
    domain_info.zone_id = "zone_id"
    domain_info.ttl = 1
    domain_info.proxied = False
    domain_info.comment = "Test comment"
    domain_info.excluded_sub_domains = []
    return [domain_info]


@pytest.fixture(scope="module", autouse=True)
def patch_singleton():
    side_effect = super(Singleton, CloudFlareZones).__call__
    with patch.object(Singleton, "__call__", side_effect=side_effect):
        yield


@pytest.mark.asyncio
async def test_update_zones_target_domain_match(
    mock_cloudflare, mock_settings, mock_domain_infos, mock_logger
):
    cf_zones = CloudFlareZones(mock_settings, mock_logger, client=mock_cloudflare)
    result = await cf_zones.update_zones("target.example.com", mock_domain_infos)
    assert result is True
    mock_cloudflare.zones.dns_records.get.assert_not_called()


@pytest.mark.asyncio
async def test_update_zones_excluded_domain(
    mock_cloudflare, mock_settings, mock_domain_infos, mock_logger
):
    mock_domain_infos[0].excluded_sub_domains = ["sub"]
    cf_zones = CloudFlareZones(mock_settings, mock_logger, client=mock_cloudflare)
    result = await cf_zones.update_zones("sub.example.com", mock_domain_infos)
    assert result is True
    mock_cloudflare.zones.dns_records.get.assert_not_called()


@pytest.mark.asyncio
async def test_update_zones_create_new_record(
    mock_cloudflare, mock_settings, mock_domain_infos, mock_logger
):
    mock_cloudflare.zones.dns_records.get.return_value = []
    cf_zones = CloudFlareZones(mock_settings, mock_logger, client=mock_cloudflare)
    result = await cf_zones.update_zones("new.example.com", mock_domain_infos)
    assert result is True
    mock_cloudflare.zones.dns_records.post.assert_called_once()


@pytest.mark.asyncio
async def test_update_zones_update_existing_record(
    mock_cloudflare, mock_settings, mock_domain_infos, mock_logger
):
    mock_cloudflare.zones.dns_records.get.return_value = [{"id": "record_id"}]
    cf_zones = CloudFlareZones(mock_settings, mock_logger, client=mock_cloudflare)
    result = await cf_zones.update_zones("existing.example.com", mock_domain_infos)
    assert result is True
    mock_cloudflare.zones.dns_records.put.assert_called_once()


@pytest.mark.asyncio
async def test_update_zones_rate_limit_retry(
    mock_cloudflare, mock_settings, mock_domain_infos, mock_logger
):
    mock_cloudflare.zones.dns_records.get.side_effect = [
        CloudFlare.exceptions.CloudFlareAPIError(-1, "Rate limited"),
        CloudFlare.exceptions.CloudFlareAPIError(-1, "Rate limited"),
        [],
    ]
    cf_zones = CloudFlareZones(mock_settings, mock_logger, client=mock_cloudflare)
    with patch("asyncio.sleep", return_value=None):
        result = await cf_zones.update_zones("rate_limited.example.com", mock_domain_infos)
    assert result is True
    assert mock_cloudflare.zones.dns_records.get.call_count == 3


@pytest.mark.asyncio
async def test_update_zones_dry_run(mock_cloudflare, mock_settings, mock_domain_infos, mock_logger):
    mock_settings.dry_run = True
    mock_cloudflare.zones.dns_records.get.return_value = []
    cf_zones = CloudFlareZones(mock_settings, mock_logger, client=mock_cloudflare)
    result = await cf_zones.update_zones("dryrun.example.com", mock_domain_infos)
    assert result is True
    mock_cloudflare.zones.dns_records.post.assert_not_called()

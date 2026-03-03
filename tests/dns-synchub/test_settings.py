from dns_synchub.settings import Settings
from dns_synchub.settings.types import Domains


def test_domain_proxied_false_is_preserved() -> None:
    settings = Settings(
        cf_token='token',
        dry_run=True,
        proxied=True,
        domains=[
            Domains(
                zone_id='zone',
                name='example.ltd',
                target_domain='target.example.ltd',
                proxied=False,
            )
        ],
    )
    assert settings.domains[0].proxied is False


def test_domain_proxied_inherits_global_default() -> None:
    settings = Settings(
        cf_token='token',
        dry_run=True,
        proxied=False,
        domains=[
            Domains(
                zone_id='zone',
                name='example.ltd',
                target_domain='target.example.ltd',
            )
        ],
    )
    assert settings.domains[0].proxied is False

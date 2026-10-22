from pathlib import Path

import pytest

from ovcatch.config import PROJECT_ROOT, load_config


@pytest.fixture(scope="session")
def demo_cfg(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    cfg = load_config(PROJECT_ROOT / "config.demo.yaml", overrides={"data_dir": str(d)})
    from ovcatch.demo import build_demo

    build_demo(cfg)
    return cfg


@pytest.fixture(scope="session")
def demo_world():
    from ovcatch.demo import make_world

    return make_world()


@pytest.fixture()
def cfg():
    return load_config(PROJECT_ROOT / "config.demo.yaml")


FIXTURES = Path(__file__).parent / "fixtures"

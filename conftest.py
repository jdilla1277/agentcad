"""Keep viewer service state and subprocesses out of the developer's account."""
import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_viewer_service(tmp_path_factory):
    from agentcad import project_viewer
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("AGENTCAD_VIEWER_HOME", str(tmp_path_factory.mktemp("viewer-runtime")))
        yield
        project_viewer.stop_service()

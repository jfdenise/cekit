import json
import subprocess

from cekit.generator import podman
from cekit.generator.podman import PodmanGenerator

INDEX_DIGEST = "sha256:" + "c" * 64
MANIFEST_DIGEST = "sha256:" + "a" * 64
IMAGE = "registry.example.com/ubi/ubi-minimal"

INDEX = {"mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [{}]}
MANIFEST = {"mediaType": "application/vnd.oci.image.manifest.v1+json"}


class FakeImage(dict):
    def __init__(self, base):
        super().__init__(**{"from": base})

    @property
    def base(self):
        return self["from"]


def _generator(base=IMAGE):
    gen = PodmanGenerator.__new__(PodmanGenerator)
    gen.image = FakeImage(base)
    gen.pull = False
    return gen


def _fake_run(skopeo_results, calls):
    """Fake subprocess.run: answers podman inspect, and skopeo by digest."""

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "/usr/bin/podman":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    [
                        {
                            "RepoDigests": [
                                f"{IMAGE}@{MANIFEST_DIGEST}",
                                f"{IMAGE}@{INDEX_DIGEST}",
                            ]
                        }
                    ]
                ),
                stderr="",
            )
        result = skopeo_results[cmd[-1].rpartition("@")[-1]]
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(result), stderr="")

    return run


def _patch_tools(mocker):
    mocker.patch.object(podman, "_podman", "/usr/bin/podman")
    mocker.patch.object(podman, "_skopeo", "/usr/bin/skopeo")


def test_resolve_base_image_digest_queries_registry_not_local_storage(mocker, monkeypatch):
    """skopeo must use docker:// so it also works with a remote podman (macOS)."""
    monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
    _patch_tools(mocker)
    calls = []
    mocker.patch(
        "subprocess.run",
        side_effect=_fake_run({MANIFEST_DIGEST: MANIFEST, INDEX_DIGEST: INDEX}, calls),
    )

    gen = _generator()
    gen.resolve_base_image_digest()

    assert gen.image["from"] == f"{IMAGE}@{INDEX_DIGEST}"
    skopeo_calls = [c for c in calls if c[0] == "/usr/bin/skopeo"]
    assert skopeo_calls
    for cmd in skopeo_calls:
        assert cmd[-1].startswith("docker://")
        assert not any("containers-storage" in arg for arg in cmd)
        assert "--authfile" not in cmd


def test_resolve_base_image_digest_passes_authfile(mocker, monkeypatch):
    monkeypatch.setenv("REGISTRY_AUTH_FILE", "/tmp/auth.json")
    _patch_tools(mocker)
    calls = []
    mocker.patch(
        "subprocess.run",
        side_effect=_fake_run({MANIFEST_DIGEST: MANIFEST, INDEX_DIGEST: INDEX}, calls),
    )

    _generator().resolve_base_image_digest()

    skopeo_calls = [c for c in calls if c[0] == "/usr/bin/skopeo"]
    assert skopeo_calls
    for cmd in skopeo_calls:
        assert cmd[cmd.index("--authfile") + 1] == "/tmp/auth.json"


def test_resolve_base_image_digest_logs_skopeo_stderr(mocker, monkeypatch, caplog):
    monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
    _patch_tools(mocker)
    err = subprocess.CalledProcessError(1, "skopeo", stderr="unauthorized: nope\n")
    mocker.patch(
        "subprocess.run",
        side_effect=_fake_run({MANIFEST_DIGEST: err, INDEX_DIGEST: err}, []),
    )

    gen = _generator()
    gen.resolve_base_image_digest()

    assert gen.image["from"] == IMAGE
    assert "unauthorized: nope" in caplog.text


def test_resolve_base_image_digest_noop_when_already_digest(mocker):
    _patch_tools(mocker)
    run = mocker.patch("subprocess.run")

    gen = _generator(f"{IMAGE}@{INDEX_DIGEST}")
    gen.resolve_base_image_digest()

    run.assert_not_called()
    assert gen.image["from"] == f"{IMAGE}@{INDEX_DIGEST}"


def _podman_subcommands(calls):
    return [c[1:3] for c in calls if c[0] == "/usr/bin/podman"]


def test_resolve_base_image_digest_pulls_first_when_pull_requested(mocker, monkeypatch):
    """With --pull, the base image is refreshed before its digest is read."""
    monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
    _patch_tools(mocker)
    calls = []
    mocker.patch(
        "subprocess.run",
        side_effect=_fake_run({MANIFEST_DIGEST: MANIFEST, INDEX_DIGEST: INDEX}, calls),
    )

    gen = _generator()
    gen.pull = True
    gen.resolve_base_image_digest()

    subcommands = _podman_subcommands(calls)
    assert subcommands[0] == ["pull", "--quiet"]
    assert subcommands[1] == ["image", "inspect"]
    assert gen.image["from"] == f"{IMAGE}@{INDEX_DIGEST}"


def test_resolve_base_image_digest_does_not_pull_by_default(mocker, monkeypatch):
    """Without --pull, the digest of the locally cached image is used."""
    monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
    _patch_tools(mocker)
    calls = []
    mocker.patch(
        "subprocess.run",
        side_effect=_fake_run({MANIFEST_DIGEST: MANIFEST, INDEX_DIGEST: INDEX}, calls),
    )

    gen = _generator()
    gen.pull = False
    gen.resolve_base_image_digest()

    assert _podman_subcommands(calls) == [["image", "inspect"]]


def test_resolve_base_image_digest_keeps_from_when_pull_fails(mocker, caplog):
    """If the pull fails, FROM stays unpinned, so FROM and labels stay consistent."""
    _patch_tools(mocker)
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 125, stdout="", stderr="denied\n"),
    )

    gen = _generator()
    gen.pull = True
    gen.resolve_base_image_digest()

    assert gen.image["from"] == IMAGE
    assert "denied" in caplog.text

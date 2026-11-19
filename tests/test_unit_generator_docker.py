# -*- encoding: utf-8 -*-

import os
from contextlib import contextmanager

import pytest
import yaml

from cekit.config import Config
from cekit.descriptor import Image
from cekit.errors import CekitError
from cekit.generator.docker import DockerGenerator

odcs_fake_resp = {
    "arches": "x86_64 ppc64",
    "flags": ["no_deps"],
    "id": 1,
    "owner": "me",
    "packages": "gofer-package",
    "removed_by": None,
    "result_repo": "https://odcs.fedoraproject.org/composes/latest-odcs-1-1/compose/Temporary",
    "result_repofile": "http://hidden/Temporary/odcs-2019.repo",
    "results": ["repository"],
    "sigkeys": "",
    "source": "f26",
    "source_type": 1,
    "state": 2,
    "state_name": "done",
    "time_done": "2017-10-13T17:03:13Z",
    "time_removed": "2017-10-14T17:00:00Z",
    "time_submitted": "2017-10-13T16:59:51Z",
    "time_to_expire": "2017-10-14T16:59:51Z",
}

odcs_fake_invalid_resp = {
    "arches": "x86_64 ppc64",
    "flags": ["no_deps"],
    "id": 1,
    "owner": "me",
    "packages": "gofer-package",
    "removed_by": None,
    "result_repo": "https://odcs.fedoraproject.org/composes/latest-odcs-1-1/compose/Temporary",
    "result_repofile": "http://hidden/Temporary/odcs-2019.repo",
    "results": ["repository"],
    "sigkeys": "",
    "source": "f26",
    "source_type": 1,
    "state": 1,
    "state_name": "wait",
    "state_reason": "Compose failed",
    "time_done": "2017-10-13T17:03:13Z",
    "time_removed": "2017-10-14T17:00:00Z",
    "time_submitted": "2017-10-13T16:59:51Z",
    "time_to_expire": "2017-10-14T16:59:51Z",
}


@contextmanager
def cekit_config(redhat=False):
    config = Config()
    config.cfg.update({"common": {"redhat": redhat}})

    try:
        yield config
    finally:
        pass


@contextmanager
def docker_generator(tmpdir, overrides=None):
    if overrides is None:
        overrides = {}

    image_dir = str(tmpdir)
    target_dir = os.path.join(image_dir, "target")
    yield DockerGenerator(image_dir, target_dir, "", overrides, False)


def setup_function():
    # For each invocation, reset the config
    Config.cfg = {"common": {}}


def test_prepare_content_sets_should_not_fail_when_cs_is_none(tmpdir):
    with docker_generator(tmpdir) as generator:
        with cekit_config(redhat=True):
            assert generator._prepare_content_sets(None) is False


def test_prepare_content_sets_should_not_fail_when_cs_is_empty(tmpdir):
    with docker_generator(tmpdir) as generator:
        with cekit_config(redhat=True):
            assert generator._prepare_content_sets({}) is False


DIGEST = "sha256:" + "ab" * 32


def _oci_labels(image):
    return {lbl.name: lbl.value for lbl in image.labels}


def _image_with_base(base, labels=""):
    return Image(
        yaml.safe_load(f"""
    from: {base}
    name: test/foo
    version: 1.9
    {labels}
    """),
        "foo",
    )


def test_add_opencontainers_base_labels_injects_name_and_digest(tmpdir):
    """When the base is digest-referenced, the two OCI base labels should be added."""
    image = _image_with_base(f"registry.example.com/mybase@{DIGEST}")
    with docker_generator(tmpdir) as generator:
        generator.image = image
        generator.add_opencontainers_base_labels()

    labels = _oci_labels(image)
    assert labels["org.opencontainers.image.base.name"] == "registry.example.com/mybase"
    assert labels["org.opencontainers.image.base.digest"] == DIGEST


def test_add_opencontainers_base_labels_strips_tag_from_name(tmpdir):
    image = _image_with_base(f"registry.example.com:5000/mybase:1.0@{DIGEST}")
    with docker_generator(tmpdir) as generator:
        generator.image = image
        generator.add_opencontainers_base_labels()

    labels = _oci_labels(image)
    assert labels["org.opencontainers.image.base.name"] == "registry.example.com:5000/mybase"
    assert labels["org.opencontainers.image.base.digest"] == DIGEST


def test_add_opencontainers_base_labels_skips_when_already_set(tmpdir):
    """User-defined OCI base labels must not be overwritten."""
    image = _image_with_base(
        f"registry.example.com/mybase@{DIGEST}",
        """labels:
      - name: org.opencontainers.image.base.name
        value: user-defined-name
      - name: org.opencontainers.image.base.digest
        value: sha256:userdigest""",
    )
    with docker_generator(tmpdir) as generator:
        generator.image = image
        generator.add_opencontainers_base_labels()

    labels = _oci_labels(image)
    assert labels["org.opencontainers.image.base.name"] == "user-defined-name"
    assert labels["org.opencontainers.image.base.digest"] == "sha256:userdigest"


def test_add_opencontainers_base_labels_skips_for_scratch(tmpdir):
    image = _image_with_base("scratch")
    with docker_generator(tmpdir) as generator:
        generator.image = image
        generator.add_opencontainers_base_labels()

    labels = _oci_labels(image)
    assert "org.opencontainers.image.base.name" not in labels
    assert "org.opencontainers.image.base.digest" not in labels


def test_add_opencontainers_base_labels_skips_when_only_name_is_set(tmpdir):
    """If the user sets only base.name, base.digest must not be injected."""
    image = _image_with_base(
        f"registry.example.com/mybase@{DIGEST}",
        """labels:
      - name: org.opencontainers.image.base.name
        value: user-defined-name""",
    )
    with docker_generator(tmpdir) as generator:
        generator.image = image
        generator.add_opencontainers_base_labels()

    labels = _oci_labels(image)
    assert labels["org.opencontainers.image.base.name"] == "user-defined-name"
    assert "org.opencontainers.image.base.digest" not in labels


def test_add_opencontainers_base_labels_skips_when_only_digest_is_set(tmpdir):
    """If the user sets only base.digest, base.name must not be injected."""
    image = _image_with_base(
        f"registry.example.com/mybase@{DIGEST}",
        """labels:
      - name: org.opencontainers.image.base.digest
        value: sha256:userdigest""",
    )
    with docker_generator(tmpdir) as generator:
        generator.image = image
        generator.add_opencontainers_base_labels()

    labels = _oci_labels(image)
    assert labels["org.opencontainers.image.base.digest"] == "sha256:userdigest"
    assert "org.opencontainers.image.base.name" not in labels


def test_add_opencontainers_base_labels_skips_when_base_has_no_digest(tmpdir):
    """If the digest could not be resolved (FROM is tag-only), build should not fail."""
    image = _image_with_base("registry.example.com/mybase:1.0")
    with docker_generator(tmpdir) as generator:
        generator.image = image
        generator.add_opencontainers_base_labels()

    labels = _oci_labels(image)
    assert "org.opencontainers.image.base.name" not in labels
    assert "org.opencontainers.image.base.digest" not in labels


def test_large_labels_should_break_lines(tmpdir):
    image = Image(
        yaml.safe_load("""
    from: foo
    name: test/foo
    version: 1.9
    labels:
      - name: 'the.large.label'
        value: Lorem ipsum dolor sit amet, consectetur adipiscing elit. Donec pretium finibus lorem vitae pellentesque. Maecenas tincidunt amet.
    """),
        "foo",
    )
    with docker_generator(tmpdir) as generator:
        generator.image = image
        with cekit_config(redhat=True):
            generator.add_build_labels()
            assert (
                image.labels[0].value
                == "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Donec pretium finibus lorem vitae pellentesque. Maecenas tincidunt amet\\\n.\\\n"
            )


def test_prepare_content_sets_should_fail_when_cs_are_note_defined_for_current_platform(
    tmpdir, mocker
):
    mocker.patch(
        "cekit.generator.base.platform.machine", return_value="current_platform"
    )

    with docker_generator(tmpdir) as generator:
        with cekit_config(redhat=True):
            with pytest.raises(
                CekitError,
                match="There are no content_sets defined for platform 'current_platform'!",
            ):
                assert (
                    generator._prepare_content_sets(
                        {"invalid_platform": ["ca1", "cs2"]}
                    )
                    is False
                )


def test_prepare_content_sets_should_request_odcs(tmpdir, mocker):
    mocker.patch(
        "cekit.generator.base.platform.machine", return_value="current_platform"
    )
    mock_odcs_new_compose = mocker.patch(
        "odcs.client.odcs.ODCS.new_compose", return_value=odcs_fake_resp
    )
    mock_odcs_wait_for_compose = mocker.patch(
        "odcs.client.odcs.ODCS.wait_for_compose", return_value=odcs_fake_resp
    )

    with docker_generator(tmpdir) as generator:
        generator.image = {"osbs": {"configuration": {"container": {"compose": {}}}}}

        with cekit_config(redhat=True):
            assert (
                generator._prepare_content_sets({"current_platform": ["ca1", "cs2"]})
                == "http://hidden/Temporary/odcs-2019.repo"
            )

    mock_odcs_new_compose.assert_called_once_with("ca1 cs2", "pulp", flags=[])
    mock_odcs_wait_for_compose.assert_called_once_with(1, timeout=600)


def test_prepare_content_sets_should_request_odcs_with_hidden_repos_flag(
    tmpdir, mocker
):
    mocker.patch(
        "cekit.generator.base.platform.machine", return_value="current_platform"
    )
    mock_odcs_new_compose = mocker.patch(
        "odcs.client.odcs.ODCS.new_compose", return_value=odcs_fake_resp
    )
    mock_odcs_wait_for_compose = mocker.patch(
        "odcs.client.odcs.ODCS.wait_for_compose", return_value=odcs_fake_resp
    )

    with docker_generator(tmpdir) as generator:
        generator.image = {
            "osbs": {
                "configuration": {
                    "container": {"compose": {"include_unpublished_pulp_repos": True}}
                }
            }
        }

        with cekit_config(redhat=True):
            assert (
                generator._prepare_content_sets({"current_platform": ["ca1", "cs2"]})
                == "http://hidden/Temporary/odcs-2019.repo"
            )

    mock_odcs_new_compose.assert_called_once_with(
        "ca1 cs2", "pulp", flags=["include_unpublished_pulp_repos"]
    )
    mock_odcs_wait_for_compose.assert_called_once_with(1, timeout=600)


def test_prepare_content_sets_should_handle_incorrect_state(tmpdir, mocker):
    mocker.patch(
        "cekit.generator.base.platform.machine", return_value="current_platform"
    )

    mock_odcs_new_compose = mocker.patch(
        "odcs.client.odcs.ODCS.new_compose", return_value=odcs_fake_invalid_resp
    )
    mock_odcs_wait_for_compose = mocker.patch(
        "odcs.client.odcs.ODCS.wait_for_compose", return_value=odcs_fake_invalid_resp
    )
    with docker_generator(tmpdir) as generator:
        generator.image = {"osbs": {"configuration": {"container": {"compose": {}}}}}

        with cekit_config(redhat=True):
            with pytest.raises(CekitError, match=r"Cannot create ODCS compose: '.*'"):
                generator._prepare_content_sets({"current_platform": ["ca1", "cs2"]})

    mock_odcs_new_compose.assert_called_once_with("ca1 cs2", "pulp", flags=[])
    mock_odcs_wait_for_compose.assert_called_once_with(1, timeout=600)

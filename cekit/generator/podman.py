import logging

from typing import List

from cekit.cekit_types import PathType
from cekit.generator.docker import DockerGenerator
from cekit.tools import split_image_name_ref

import json
import os
import subprocess
import shutil

LOGGER = logging.getLogger("cekit")

_podman = shutil.which('podman')
_skopeo = shutil.which('skopeo')

_list_manifest_types = [
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
]

def _is_manifest_list(ref):
    # Query the registry rather than local containers-storage: with a remote
    # podman (macOS, Windows), the image lives in the podman machine and is
    # not visible to skopeo running on the host.
    cmd = [_skopeo, "inspect", "--raw"]
    auth = os.getenv("REGISTRY_AUTH_FILE")
    if auth:
        cmd.extend(["--authfile", auth])
    cmd.append(f"docker://{ref}")

    out = json.loads(subprocess.run(
        cmd, capture_output=True, text=True, check=True
    ).stdout)

    return out.get("mediaType") in _list_manifest_types or out.get("manifests")

class PodmanGenerator(DockerGenerator):
    def __init__(
        self,
        descriptor_path: PathType,
        target: PathType,
        container_file: str,
        overrides: List[str],
        no_squash: bool,
        pull: bool = False,
    ):
        super(DockerGenerator, self).__init__(
            descriptor_path, target, container_file, overrides, no_squash
        )
        self.pull = pull

    def resolve_base_image_digest(self):
        """Update the base image reference with a digest-reference equivalent,
        if one isn't already specified."""
        frm = self.image.base
        LOGGER.debug(f"resolve_base_image_digest: {frm}")

        # If the base image is already a checksum-reference, do nothing
        if frm.find("@") > 0:
            LOGGER.debug("resolve_base_image_digest: base image already checksum-referenced")
            return

        if not _podman:
            LOGGER.warning("couldn't find podman, can't resolve image digest")
            return
        if not _skopeo:
            LOGGER.warning("couldn't find skopeo, can't resolve image digest")
            return

        # With --pull, refresh the local image first so that the digest we pin
        # (and the labels derived from it) is the latest one. Without --pull,
        # the digest of the locally cached image is used.
        if self.pull:
            cmd = [_podman, 'pull', '--quiet', '--', frm]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                LOGGER.warning(f"couldn't pull {frm}, can't resolve image digest: "
                               f"{result.stderr.strip()}")
                return

        # look up the base image and try to find a digest-reference
        cmd = [_podman , 'image', 'inspect', '--format', 'json', '--', frm]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            LOGGER.error(cmd)
            LOGGER.error(result.stderr) # XXX: tag?
            return
        js = json.loads(result.stdout)[0]
        digests = js.get('RepoDigests', [])

        for digest in digests:
            try:
                if _is_manifest_list(digest):
                    (image_no_tag, _) = split_image_name_ref(frm)
                    sha256sum    = digest.rpartition("@")[-1]

                    newfrm = f"{image_no_tag}@{sha256sum}"
                    LOGGER.debug(f"rewriting FROM to {newfrm}")
                    self.image['from'] = newfrm
                    return

            except subprocess.SubprocessError as e:
                stderr = (getattr(e, "stderr", None) or "").strip()
                LOGGER.error(f"error inspecting container metadata for {digest}: {stderr or e}")
                continue

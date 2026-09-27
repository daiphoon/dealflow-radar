"""Verify byte-addressed registry documents and daemon/container bindings."""

import hashlib
import json
import re

MANIFESTS = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
INDEXES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class Unverifiable(ValueError):
    pass


class Mismatch(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Mismatch(message)


def verify_identity(approved: dict, blobs: dict[str, bytes], observed: dict) -> dict:
    """One verifier for initial observation and the pre-opening current preparation."""
    chain = []
    try:
        reference = approved["reference"]
        repository, root = reference.rsplit("@", 1)
        require(
            bool(repository) and bool(re.fullmatch(r"sha256:[a-f0-9]{64}", root)),
            "immutable_reference_required",
        )
        require(approved["platform"] == "linux/amd64", "unsupported_approved_platform")

        def document(key, size=None):
            if key not in blobs:
                raise Unverifiable("missing_raw_document:" + key)
            raw = blobs[key]
            require(digest(raw) == key, "raw_document_digest_mismatch:" + key)
            if size is not None:
                require(len(raw) == size, "document_size_mismatch")
            value = json.loads(raw)
            chain.append(
                {
                    "digest": key,
                    "bytes": len(raw),
                    "media_type": value.get("mediaType", "image_config"),
                }
            )
            return value

        manifest = document(root)
        selected = root
        if manifest.get("mediaType") in INDEXES:
            matches = [
                v
                for v in manifest["manifests"]
                if v.get("platform", {}).get("os") == "linux"
                and v.get("platform", {}).get("architecture") == "amd64"
                and v.get("platform", {}).get("variant", "") in ("", "v1")
            ]
            require(len(matches) == 1, "ambiguous_or_missing_amd64_manifest")
            selected = matches[0]["digest"]
            manifest = document(selected, matches[0]["size"])
            require(
                manifest.get("mediaType") == matches[0]["mediaType"],
                "index_child_media_type_mismatch",
            )
        require(
            manifest.get("mediaType") in MANIFESTS and manifest.get("schemaVersion") == 2,
            "unsupported_manifest",
        )
        config_digest = manifest["config"]["digest"]
        require(config_digest == approved["config_digest"], "approved_config_relation_mismatch")
        config = document(config_digest, manifest["config"]["size"])
        layers = manifest["layers"]
        require(
            all(
                re.fullmatch(r"sha256:[a-f0-9]{64}", v["digest"]) and v["size"] >= 0 for v in layers
            ),
            "invalid_layer_descriptors",
        )
        require(
            config["os"] == "linux" and config["architecture"] == "amd64",
            "registry_platform_mismatch",
        )
        revision = config["config"]["Labels"]["org.opencontainers.image.revision"]
        require(revision == approved["revision"], "registry_revision_mismatch")
        diff_ids = config["rootfs"]["diff_ids"]
        require(len(diff_ids) == len(layers), "layer_config_relation_mismatch")
        store = observed["store"]
        if store not in ("classic", "containerd"):
            raise Unverifiable("unknown_daemon_store")
        if not observed["context"]:
            raise Unverifiable("missing_docker_context")
        by_ref = observed["by_reference"]
        image_only = observed.get("scope") == "daemon_image"
        if image_only:
            require(approved.get("role") == "safety-control", "image_only_reserved_for_control")
            actual, container = by_ref, None
        else:
            actual, container = observed["by_container"], observed["container"]
            require(bool(container["Id"]), "missing_container_id")
            require(container["Config.Image"] == reference, "container_reference_mismatch")
            require(
                by_ref["Id"] == actual["Id"] == container["Image"],
                "container_daemon_resolution_mismatch",
            )
        for image in (by_ref, actual):
            require(reference in image["RepoDigests"], "exact_reference_not_resolved")
            require(
                image["Os"] == "linux" and image["Architecture"] == "amd64",
                "daemon_platform_mismatch",
            )
            require(
                image["revision"] == revision and image["RootFS"]["Layers"] == diff_ids,
                "daemon_config_relation_mismatch",
            )
            if store == "classic":
                require(image["Id"] == config_digest, "classic_id_not_approved_config")
            else:
                descriptor = image["Descriptor"]
                require(descriptor["digest"] == image["Id"], "containerd_descriptor_id_mismatch")
                require(
                    descriptor["digest"] in (root, selected),
                    "containerd_descriptor_outside_verified_chain",
                )
                expected_type = next(
                    v["media_type"] for v in chain if v["digest"] == descriptor["digest"]
                )
                require(
                    descriptor["mediaType"] == expected_type, "containerd_descriptor_type_mismatch"
                )
        return {
            "status": "PASS",
            "reason": "verified_registry_daemon_container_chain",
            "reference": reference,
            "registry_chain": chain,
            "selected_manifest": selected,
            "config_digest": config_digest,
            "layers": layers,
            "daemon_id": actual["Id"],
            "container_id": container["Id"] if container else None,
            "scope": "daemon_image" if image_only else "container",
            "platform": approved["platform"],
            "revision": revision,
            "context": observed["context"],
            "store": store,
        }
    except Mismatch as exc:
        return {"status": "BLOCKED", "reason": str(exc), "registry_chain": chain}
    except (KeyError, TypeError, ValueError, Unverifiable) as exc:
        return {
            "status": "INCONCLUSIVE",
            "reason": f"{type(exc).__name__}:{exc}",
            "registry_chain": chain,
        }

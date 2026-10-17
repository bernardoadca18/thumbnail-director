import fcntl
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from app.domain.contracts import Package
from app.domain.identity import canonical, digest, sha


class IntegrityError(RuntimeError):
    pass


class ArtifactStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, fingerprint):
        if not re.fullmatch("[a-f0-9]{64}", fingerprint):
            raise ValueError("Invalid fingerprint")
        return self.root / fingerprint

    @contextmanager
    def lock(self, fingerprint):
        directory = self.directory(fingerprint)
        with (self.root / (directory.name + ".lock")).open("a") as file:
            deadline = time.monotonic() + 310
            while True:
                try:
                    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Generation already running")
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    def load(self, fingerprint):
        directory = self.directory(fingerprint)
        if not directory.exists():
            return None
        try:
            package = Package.model_validate_json(
                (directory / "manifest.json").read_bytes()
            )
            if (
                package.request_fingerprint != fingerprint
                or package.result_fingerprint
                != digest(
                    package.model_dump(mode="json", exclude={"result_fingerprint"})
                )
            ):
                raise ValueError("Manifest identity mismatch")
            for candidate in package.candidates:
                if candidate.artifact:
                    artifact = candidate.artifact
                    if (
                        artifact.artifact_id
                        != f"thumb_{fingerprint}_{candidate.candidate_id}"
                    ):
                        raise ValueError("Artifact identity mismatch")
                    path = directory / f"candidate_{candidate.candidate_id}.png"
                    if path.is_symlink() or sha(path.read_bytes()) != artifact.sha256:
                        raise ValueError("Artifact integrity mismatch")
            return package
        except (ValueError, OSError) as exc:
            raise IntegrityError("artifact_integrity_failed") from exc

    def publish(self, package, artifacts):
        fingerprint = package.request_fingerprint
        existing = self.load(fingerprint)
        if existing:
            if existing.result_fingerprint != package.result_fingerprint:
                raise IntegrityError("Frozen package differs")
            return existing
        temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=self.root))
        try:
            for name, data in {
                **artifacts,
                "manifest.json": canonical(package.model_dump(mode="json")),
            }.items():
                with (temporary / name).open("xb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
            self.sync(temporary)
            os.rename(temporary, self.directory(fingerprint))
            self.sync(self.root)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return self.load(fingerprint)

    @staticmethod
    def sync(path):
        fd = os.open(path, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def artifact(self, artifact_id):
        match = re.fullmatch(r"thumb_([a-f0-9]{64})_([ABC])", artifact_id)
        if not match:
            raise FileNotFoundError("Unknown artifact")
        fingerprint, candidate_id = match.groups()
        package = self.load(fingerprint)
        if package:
            for candidate in package.candidates:
                if candidate.artifact and candidate.artifact.artifact_id == artifact_id:
                    return (
                        self.directory(fingerprint) / f"candidate_{candidate_id}.png",
                        candidate.artifact.sha256,
                    )
        raise FileNotFoundError("Unknown artifact")

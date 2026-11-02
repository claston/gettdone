"""Private local/S3 objects for retained, versioned exports. No public URLs."""
import re
import shutil
from pathlib import Path

_KEY = re.compile(r"canonical/[A-Za-z0-9][A-Za-z0-9_-]{0,127}/v[1-9][0-9]*/[a-f0-9]{32}/(?:report\.xlsx|converted\.(?:ofx|xlsx|csv))")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def _prefix(analysis_id):
    if not _ID.fullmatch(analysis_id):
        raise ValueError("Invalid analysis identity")
    return f"canonical/{analysis_id}/"


def _key(key):
    if not _KEY.fullmatch(key):
        raise ValueError("Invalid canonical artifact key")
    return key


class LocalCanonicalObjects:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def put(self, key, data):
        path = self.root / _key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique attempt keys: never replace bytes already visible to a reader.
        with path.open("xb") as target:
            target.write(data)

    def get(self, key):
        return (self.root / _key(key)).read_bytes()

    def delete_analysis(self, analysis_id):
        path = (self.root / _prefix(analysis_id)).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Invalid artifact prefix")
        if path.exists():
            shutil.rmtree(path)


class S3CanonicalObjects:
    def __init__(self, client, bucket: str, *, prefix: str, encryption_options: dict):
        self.client, self.bucket = client, bucket
        self.prefix = prefix.strip("/")
        self.encryption_options = {"ServerSideEncryption": "AES256", **encryption_options}

    def _object(self, key):
        return f"{self.prefix}/{_key(key)}" if self.prefix else _key(key)

    def put(self, key, data):
        self.client.put_object(Bucket=self.bucket, Key=self._object(key), Body=data,
                               IfNoneMatch="*", **self.encryption_options)

    def get(self, key):
        response = self.client.get_object(Bucket=self.bucket, Key=self._object(key))
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()

    def delete_analysis(self, analysis_id):
        suffix = _prefix(analysis_id)
        prefix = f"{self.prefix}/{suffix}" if self.prefix else suffix
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix)
        for page in pages:
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                result = self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                if result.get("Errors"):
                    raise RuntimeError("Canonical artifact cleanup incomplete")
